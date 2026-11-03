"""Coder 子 Agent：按计划逐文件生成 RTL。"""

from __future__ import annotations

import json
import subprocess
import re
from pathlib import Path

from ai_eda.model.client import RTL_SYSTEM_PROMPT
from ai_eda.rtl.runner import extract_rtl
from ai_eda.rtl.metrics import yosys_executable
from ai_eda.agent.skills.rv32i5_l1_reference import load_reference
from ai_eda.agent.skills.rv32i5_contract import coder_context
from ai_eda.agent.repair_context import digest, source_manifest, source_windows, spec_context

from .base import SubAgent
from .common import RV32I_L1_CODE_PATTERNS, RV32I_PIPELINE_CHECKLIST, safe_project_path


def _architecture_block(plan: dict) -> str:
    architecture = plan.get("architecture")
    if not isinstance(architecture, dict):
        return ""
    parameters = architecture.get("parameters") or {}
    directives = architecture.get("coder_directives") or []
    reference = architecture.get("reference_structure") or {}
    return (
        "\n\nPlanner Skill 预评估架构参数（直接来自 Python Skill，必须严格遵守）：\n"
        + json.dumps(parameters, ensure_ascii=False, indent=2)
        + "\n\nCoder 指令：\n"
        + "\n".join(f"- {item}" for item in directives)
        + "\n\n参考结构：\n"
        + json.dumps(reference, ensure_ascii=False, indent=2)
        + "\n"
    )


def generation_order(plan: dict) -> list[dict]:
    """Keep leaf contracts available before generating consumers and the top."""
    pending = list(plan["modules"])
    ordered, done = [], set()
    while pending:
        ready = [m for m in pending if set(m.get("dependencies", [])) <= done
                 and (m["name"] != plan["top_module"] or len(pending) == 1)]
        if not ready:
            raise ValueError("Cyclic module dependencies or a helper depends on top")
        module = ready[0]
        pending.remove(module)
        ordered.append(module)
        done.add(module["name"])
    return ordered


def generated_peer_context(plan: dict, module: dict, files: dict, limit: int = 96000) -> dict:
    peers = {}
    dependencies = set(module.get("dependencies", []))
    related = [peer for peer in plan["modules"] if peer["file"] in files
               and (module["name"] == plan["top_module"] or peer["name"] in dependencies)]
    per_peer = min(24000, limit // max(1, len(related)))
    for peer in related:
        text = files[peer["file"]]
        excerpt = source_windows(text, "assign always state ready valid response complete flush", min(per_peer, limit))
        peers[peer["file"]] = {"sha256": digest(text), "source_complete": len(text) <= len(excerpt),
                               "source_excerpt": excerpt}
        limit -= len(excerpt)
        if limit <= 0:
            break
    return peers


class CoderAgent(SubAgent):
    name = "coder"
    title = "03 · Coder"
    node_type = "coder"
    # RTL 生成同样属于工具型输出。L1/L2 的 Plan+Spec 很长，思考模式可能把
    # 60k 输出预算全部消耗在 reasoning 上，导致没有 RTL 文本。默认关闭思考，
    # 把预算留给完整的 SystemVerilog 文件。
    MODEL_OPTIONS = {"thinking": False}

    def run(
        self,
        planner_handoff: Path,
        *,
        existing_files: dict[str, str] | None = None,
        start_module: str | None = None,
    ) -> Path:
        incoming = self.runtime.read_handoff(planner_handoff)["payload"]
        artifacts = incoming.get("artifacts") or {}
        spec_ref = artifacts.get("spec")
        plan_ref = artifacts.get("plan")
        if not isinstance(spec_ref, str) or not isinstance(plan_ref, str):
            raise ValueError("Planner → Coder handoff 缺少 Spec 或 Plan artifact")
        effective_spec = self.runtime.read_text_artifact(spec_ref)
        plan = self.runtime.read_json_artifact(plan_ref)
        if not isinstance(plan, dict):
            raise ValueError("Plan artifact 格式无效")
        project_id = str((self.runtime.options.get("project") or {}).get("id") or "")
        model_options = ({"thinking": False, "max_tokens": 24000}
                         if project_id == "rv32i5_ooo" else self.MODEL_OPTIONS)
        system = RTL_SYSTEM_PROMPT + (
            " HARD RULES: synchronous reset ONLY always @(posedge clk), never negedge rst_n. "
            "No typed parameter/localparam (not localparam logic). No initial. PRF read addresses "
            "are inputs into its owner rename_unit; data outputs drive IQ operand inputs. "
            "All declared outputs MUST be assigned. Every source operand waits for physical ready. "
            "Use ANSI function headers with arguments inside parentheses; never mix old-style "
            "function declarations with input logic declarations after the header semicolon. "
            "Treat requirements/plan notes as binding, not suggestions. "
            "Never tie a runtime operand, instruction identity, effective address or byte enable "
            "to zero merely to fill an incomplete planned interface. Implement its actual data path. "
            "Audit capacities: each 256-byte cache has 16*4*32 data bits and 16*24 tag bits. "
            "PR0..31 start ready, free PR32..39 are allocated once each and cursors wrap at actual "
            "array bounds. Commit uses ROB architectural rd, not a search in the speculative RAT. "
            "Only retiring real register writers may update the committed map or reclaim an old "
            "physical register. Stores, branches and ECALL retire without a destination. "
            "ECALL stops younger work while older work drains, and older recovery cancels a "
            "wrong-path ECALL. Final halt must respect any already-presented stalled bus request. "
            "Completion valid must imply a live queue entry; remove it only after completion acceptance. "
            "Each request has exactly one accepted response with its original PC/word/byte offset. "
            "Carry prediction metadata with that instruction; recovery handles wrapped ROB tags. "
            "Arithmetic signed shifts need a signed expression context in the final assignment."
            " Derive each queue/count next state ONCE from all accepted events: simultaneous "
            "enqueue/dequeue and allocate/commit must not drop either event. Gate ready during "
            "an event you cannot accept, rather than acknowledging and discarding it. Free-list "
            "indices wrap to the actual 8-entry size. Every nonzero physical register (including "
            "initial PR1..PR31) is recyclable after its last committed owner is replaced. "
            "Never reconstruct a checkpoint free list as PR32..PR39 after those IDs have rotated; "
            "preserve exact ownership across older commits and recovery. Only accepted "
            "register-writer completion may write a nonzero PR; PR0 stays zero even for stores. "
            "Control-transfer completion must write the JAL/JALR link destination as well as "
            "resolve the branch. No younger instruction may commit after ECALL. At reset time "
            "external valid outputs and event outputs are gated by rst_n before the first edge. "
            "SB data is {4{rs2[7:0]}} and SH data is {2{rs2[15:0]}} as required by the course bus."
            " Select register versus immediate ALU operands from the actual instruction opcode; "
            "OP-IMM never uses its immediate bits as an rs2 dependency. Advance a queue pointer "
            "only for its accepted enqueue/dequeue, and compute the next fetch PC from the "
            "currently accepted request and its prediction, not an older response's metadata. "
            "Use unpacked word arrays for variable-indexed cache storage supported by Icarus "
            "and Yosys. Do not use SystemVerilog keywords such as ref, priority or type as identifiers. "
            "A non-writer or rd=x0 instruction still owns a ROB entry and must complete and retire "
            "exactly once; physical write enable is separate from instruction completion. Do not "
            "acknowledge dispatch if any consumer will discard it. A redirect preserves the "
            "resolving control instruction and its own link destination, while discarding only "
            "strictly younger entries and their physical allocations."
            if project_id == "rv32i5_ooo" else "")
        project_checklist = (
            "\n\n" + RV32I_PIPELINE_CHECKLIST + "\n\n" + RV32I_L1_CODE_PATTERNS
            if project_id in {"rv32i5", "rv32i5_cache"}
            else ""
        )
        project_checklist += "\n" + coder_context(project_id)
        files: dict[str, str] = {}
        existing = existing_files or {}
        # L3 never invokes any reference loader, even when a stale client sends an old mode.
        reference = load_reference(project_id) if project_id == "rv32i5_cache" else {}
        if project_id == "rv32i5_ooo" and not self.runtime.options.get("resume_from"):
            if existing or any((self.runtime.run_dir / "project").rglob("*.sv")):
                raise ValueError("Fresh L3 Coder requires an empty RTL project")
        modules = generation_order(plan) if project_id == "rv32i5_ooo" else plan["modules"]
        origins = {"generation_mode": "resumed" if self.runtime.options.get("resume_from") else "fresh",
                   "resume_from": self.runtime.options.get("resume_from"), "builtin_rtl_used": False,
                   "files": []}
        start_index = 0
        if start_module:
            for index, module in enumerate(modules):
                if module.get("name") == start_module:
                    start_index = index
                    break
        architecture_block = _architecture_block(plan)
        for index, module in enumerate(modules):
            if index < start_index and module["file"] in existing:
                files[module["file"]] = existing[module["file"]]
                origins["files"].append({"path": module["file"], "origin": "explicit_resume",
                                         "sha256": digest(files[module["file"]])})
                continue
            if module["file"] in reference:
                rtl = reference[module["file"]]
                origins["builtin_rtl_used"] = True
                origins["generation_mode"] = "builtin_reference"
                origins["files"].append({"path": module["file"], "origin": "builtin_reference", "sha256": digest(rtl)})
                target = safe_project_path(self.runtime.run_dir, module["file"])
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(rtl, encoding="utf-8")
                files[module["file"]] = rtl
                event = self.runtime.begin_event(
                    "coder", self.node_type, f"{self.title} · {module['name']}", "参考实现"
                )
                self.finish(
                    event,
                    "info",
                    response=f"Wrote {module['file']} (explicit verified reference; not model generation, {len(rtl)} chars).",
                )
                continue
            prompt = (
                f"请实现项目中模块 `{module['name']}`，写入文件 `{module['file']}`。"
                "只输出该文件的完整 SystemVerilog RTL，不要输出 Markdown 围栏或解释。\n\n"
                "项目计划（必须严格遵守 modules[].interfaces 的端口名/方向/位宽；"
                "跨模块信号在该模块中也必须使用同一套名称和宽度，不能自行改名或增删端口）。\n"
                + architecture_block
                + "语法自检：端口列表已经声明的 output/input 信号不要在模块体内再次用 logic/wire 声明；"
                "每个信号只能有一个 driver；不要出现重复声明或多驱动。\n"
                f"{json.dumps(plan, ensure_ascii=False, indent=2)}\n\n"
                f"Spec：\n{spec_context(effective_spec, [module['file']], project_id, 30000) if project_id == 'rv32i5_ooo' else effective_spec}\n\n"
                f"本模块说明：{module['description']}\n本模块接口契约：{module['interfaces']}"
                "\nUse only untyped parameter/localparam, fixed finite arrays, synchronous active-low reset. "
                "Never reset cache data/tag arrays unnecessarily. Every output has exactly one driver. "
                "Keep combinational blocks acyclic; prefer always @* for Icarus. No stubs."
                + project_checklist
            )
            if project_id == "rv32i5_ooo":
                prompt += ("\nActual already-generated dependencies from THIS run only (bounded excerpts; "
                           "omitted text is not an empty module). Follow their implemented port timing; "
                           "do not invent an extra response cycle. Include brief protocol comments in "
                           "your RTL for downstream Coder. No pre-existing CPU is available.\n" +
                           json.dumps(generated_peer_context(plan, module, files), ensure_ascii=False))
            event = self.runtime.begin_event(
                "coder", self.node_type, f"{self.title} · {module['name']}", prompt
            )
            event["origin"] = "model"
            event["source_path"] = module["file"]
            first_call = self.runtime.model_calls + 1
            result = self.runtime.call_model(
                event, prompt, system, model_options=model_options
            )
            rtl = extract_rtl(result.get("answer") or "")
            if not rtl.strip() or "module" not in rtl:
                raise ValueError(f"Coder 没有为 {module['name']} 生成有效 RTL")
            target = safe_project_path(self.runtime.run_dir, module["file"])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rtl, encoding="utf-8")
            files[module["file"]] = rtl
            origin = {"path": module["file"], "origin": "model", "step": event["step"], "revisions": []}
            origins["files"].append(origin)
            syntax_gate_passed = False
            for syntax_round in range(3):
                snapshot = self.runtime.write_text_artifact(
                    f"generation/{event['step']:02d}/revision-{syntax_round}.sv", rtl)
                origin.update(sha256=digest(rtl), call_indices=list(range(first_call, self.runtime.model_calls + 1)))
                origin["revisions"].append({"artifact": snapshot, "sha256": digest(rtl),
                                             "call_index": self.runtime.model_calls})
                self.runtime.write_json_artifact("generation_provenance.json", origins)
                syntax = subprocess.run(["iverilog", "-g2012", "-i", "-s", module["name"],
                    "-o", str(target.with_suffix(".vvp")), str(target)],
                    text=True, capture_output=True, timeout=30)
                violations = []
                if project_id == "rv32i5_ooo":
                    if re.search(r"Numeric constant truncated|extra digits given for sized", syntax.stderr):
                        violations.append("Sized numeric literal overflow is an error, even if Icarus only warns. "
                                          "For example 3'd8 is ZERO: use 4'd8 or unsized 8, and use a "
                                          "4-bit ROB count to represent all counts 0..8.")
                    if re.search(r"negedge\s+rst_n", rtl):
                        violations.append("Asynchronous reset is forbidden: remove negedge rst_n from sensitivity lists.")
                    if re.search(r"\b(?:localparam|parameter)\s+logic\b", rtl):
                        violations.append("Typed parameters are forbidden: use localparam [width] NAME=value.")
                    if syntax.returncode == 0 and not violations:
                        synth_parse = subprocess.run([yosys_executable(), "-Q", "-T", "-p",
                            f'read_verilog -sv "{target.resolve()}"'],
                            text=True, capture_output=True, timeout=30)
                        if synth_parse.returncode:
                            violations.append("Yosys frontend: " + (synth_parse.stdout+synth_parse.stderr)[-3000:])
                if syntax.returncode == 0 and not violations:
                    syntax_gate_passed = True
                    break
                if syntax_round == 2:
                    if project_id != "rv32i5_ooo":
                        raise ValueError("Module syntax gate failed: " + syntax.stderr[-5000:] + str(violations))
                    # Finish the remaining modules so Tester can hand a real compiler
                    # failure to the dedicated Diagnoser/Modifier loop.
                    break
                repair = self.runtime.call_model(event,
                    prompt + "\nYour source:\n" + rtl + "\nSyntax/contract errors:\n" + syntax.stderr[-5000:] + str(violations) +
                    "\nThe compiler rejected THIS exact source. Change the offending construct, "
                    "do not return identical RTL. For function errors use ANSI function headers "
                    "with inputs inside parentheses; do not put input logic after a header semicolon. "
                    "Return the corrected complete module only.", system,
                    model_options=model_options)
                rtl = extract_rtl(repair.get("answer") or "")
                target.write_text(rtl, encoding="utf-8")
                files[module["file"]] = rtl
            artifact_dir = self.runtime.run_dir / f"step-{event['step']:02d}-{event['node_id']}"
            (artifact_dir / "generated.sv").write_text(rtl, encoding="utf-8")
            gate_ref = self.runtime.write_json_artifact(f"module_gate_{event['step']}.json", {
                "path": module["file"], "passed": syntax_gate_passed,
                "compiler_stderr": syntax.stderr[-5000:], "violations": violations,
                "next": "Tester still performs full-project compilation and functional validation."})
            origin["syntax_gate_passed"] = syntax_gate_passed
            self.runtime.write_json_artifact("generation_provenance.json", origins)
            self.save_context(plan_ref=plan_ref, last_file=self.runtime.artifact_ref(target))
            self.finish(
                event,
                "info" if syntax_gate_passed else "failed",
                response=f"Wrote {module['file']} ({len(rtl)} chars).",
                module_gate=gate_ref,
                rtl=rtl,
                reasoning=event.get("reasoning"),
                usage=event.get("usage"),
            )
        manifest_ref = source_manifest(self.runtime, files, "rtl_manifest.json")
        if project_id == "rv32i5_ooo" and not self.runtime.options.get("resume_from"):
            if len(origins["files"]) != len(modules) or any(
                    f["origin"] != "model" or not f.get("call_indices") for f in origins["files"]):
                raise ValueError("Fresh generation provenance is incomplete; refusing Tester handoff")
        provenance_ref = self.runtime.write_json_artifact("generation_provenance.json", origins)
        self.save_context(plan_ref=plan_ref, rtl_manifest_ref=manifest_ref)
        return self.runtime.write_handoff(
            "coder",
            "tester",
            {
                "artifacts": {
                    "spec": spec_ref,
                    "plan": plan_ref,
                    "rtl_manifest": manifest_ref,
                    "generation_provenance": provenance_ref,
                },
            },
            label="Coder → Tester: RTL manifest",
        )

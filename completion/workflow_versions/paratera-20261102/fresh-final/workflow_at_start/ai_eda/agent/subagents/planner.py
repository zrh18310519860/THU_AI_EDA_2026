"""RTL Planner 子 Agent：规划模块与文件结构。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ai_eda.agent.skills.fifo_area_power import evaluate as evaluate_fifo_skill
from ai_eda.agent.skills.rv32i5_contract import planner_context

from .base import SubAgent
from .common import PLANNER_SYSTEM_PROMPT, extract_json_payload, validate_plan


def l3_plan_errors(plan: dict) -> list[str]:
    """Reject capacities that cannot be represented by the proposed ANSI ports."""
    errors = []
    physical_id = re.compile(r"(?:^|_)(?:pr|pd|ps[12]|prd|psrc[12])(?:$|_)|prf_raddr[12]")
    for module in plan["modules"]:
        header = module["interfaces"]
        for high, low, name in re.findall(
                r"\b(?:input|output)\s+(?:(?:logic|wire|reg)\s+)?\[(\d+)\s*:\s*(\d+)\]\s*(\w+)", header):
            if physical_id.search(name) and abs(int(high) - int(low)) + 1 < 6:
                errors.append(f"{module['name']}.{name}: [{high}:{low}] cannot encode PR0..PR39; use 6 bits.")
    return errors


class PlannerAgent(SubAgent):
    name = "planner"
    title = "02 · RTL Planner"
    node_type = "planner"

    def run(self, parser_handoff: Path) -> Path:
        incoming = self.runtime.read_handoff(parser_handoff)["payload"]
        artifacts = incoming.get("artifacts") or {}
        spec_ref = artifacts.get("spec")
        if not isinstance(spec_ref, str):
            raise ValueError("Parser → Planner handoff 缺少 spec artifact")
        effective_spec = self.runtime.read_text_artifact(spec_ref)
        skill_result: dict | None = None
        skill_ref: str | None = None
        skill_prompt = ""
        project_id = (self.runtime.options.get("project") or {}).get("id")
        if self.runtime.options.get("planner_skill") and project_id == "stream_filter":
            skill_result = evaluate_fifo_skill(effective_spec)
            skill_ref = self.runtime.write_json_artifact("planner_skill.json", skill_result)
            skill_prompt = (
                "\n\nPlanner Skill 已运行，下面是面积/功耗预评估与架构参数。"
                "必须把 architecture 字段原样写入计划，并要求所有 Coder 严格遵守其中的 coder_directives：\n"
                f"{json.dumps(skill_result, ensure_ascii=False, indent=2)}\n"
            )
        prompt = (
            "根据下面的 Spec 规划 RTL 项目的模块与文件结构。每个模块必须能由一个文件独立实现；"
            "所有模块最终会一起用教师 TB 编译验收。每个 module 的 interfaces 必须写全端口名、方向、位宽；"
            "跨模块信号名和位宽一致，发送方 output 必须连接接收方 input，不能只写一句总结。\n"
            + skill_prompt
            + "\n" + planner_context(project_id)
            + ("\nL3 architecture: select small finite ROB8/IQ8/PRF40/LSQ2. Keep a single shared "
               "completion bus (LSU response has priority, stall integer issue for that cycle). "
               "Allow integer IQ to issue younger independent ALUs while head load waits. "
               "Memory operations may issue only when ROB head, simplifying ordering and store safety. "
               "Use separate frontend/decode/execute helpers, top wiring only. Rename owns PRF, readiness, "
               "RAT/committed RAT and checkpoints; IQ stores PR source ids and reads readiness from "
               "rename via a packed 40-bit ready vector. Use ROB ids and circular distance to head for "
               "age/squash. Freeze EVERY packed bit layout in plan notes. Port declarations must be "
               "literal SV module headers. Freeze allocation/issue/completion/retire handshakes and "
               "same-cycle priorities. A rotating free-list head plus availability/allocated mask is "
               "allowed; restore per-branch RAT/head, reclaim ONLY younger allocated PRs, keep older "
               "commits. Preserve JAL own link PR on recovery. Generate leaf modules first, top last. "
               "Use untyped parameters. Avoid combinational ready loops. No stubs or L1 core. "
               "This is a FRESH EMPTY PROJECT, not an upgrade from existing RTL. Do not import any "
               "reference plan or CPU. Specify a protocols field for each module and a global protocols "
               "field defining producer/consumer ownership, pulse vs level, acceptance edge, registered "
               "response timing, backpressure, reset values, and priority for simultaneous events. "
               "Use the fixed course external-memory contract: mem_req_valid && mem_req_ready "
               "accepts the request and mem_resp_valid/mem_resp_data are sampled on that same "
               "clock edge. The cache-to-CPU response may be a registered one-cycle pulse, but "
               "the top bridge must not add an extra external response cycle. Cache FSMs and "
               "top bridge must match this exact contract. "
               "Dependencies mean instantiated children, not feedback wires between peer modules. "
               "Define every packed instruction/queue/ROB field bit-by-bit and common opcode constants. "
               "Give each module a dependencies list of module names; generate helpers/leaf modules "
               "before consumers and rv32i5 last. Completion carries ROB id and physical destination "
               "from issue metadata, never the current decode instruction. Plan at most 11 modules. "
               " Every ALU branch resolve/mispredict/predictor update/redirect and squash must be "
               "qualified with a real issue_valid && issue_ready; unused queue payload is allowed X, "
               "so never connect raw ALU flags to frontend flush. Valid never waits for ready to assert. "
               " PRF read enable/address ports are INPUTS to rename/PRF and OUTPUTS from IQ; "
               "read data flows the reverse way. Freeze cancellation: I-cache discards obsolete "
               "epoch responses, so redirect resets frontend to request state; never drain a "
               "response that cache suppresses. The new request waits for cache ready while "
               "the external old request remains stable until accepted. Use a bank of eight "
               "packed 192-bit RAT snapshots (or equivalent per-bank registers), not a 256-word "
               "memory with 32 dynamic write ports, to keep synthesis finite. Initial mapped "
               "PR1..PR31 must recycle after commit, not just PR32..39. ROB updates both "
               "valid bitmap and circular count consistently on commit+allocation+squash."
               if project_id == "rv32i5_ooo" else "")
            + f"\nSpec：\n{effective_spec}\n\n"
            "请只返回约定的 JSON 对象。"
        )
        event = self.runtime.begin_event("planner", self.node_type, self.title, prompt)
        result = self.runtime.call_model(event, prompt, PLANNER_SYSTEM_PROMPT,
            model_options={"thinking": False, "max_tokens": 24000}
            if project_id == "rv32i5_ooo" else None)
        try:
            plan = validate_plan(extract_json_payload(result.get("answer") or ""))
        except ValueError as error:
            raise ValueError(f"Planner 规划无效：{error}") from error
        if project_id == "rv32i5_ooo":
            self.runtime.write_json_artifact("plan_draft.json", plan)
            review_prompt = (
                "Review this proposed CPU plan BEFORE any RTL exists. Return a corrected COMPLETE "
                "plan JSON in the same schema. Preserve required file names and teacher top ports. "
                "Trace each instruction from fetch through architectural commit, and verify every "
                "producer/consumer has actual ports in BOTH module headers. In particular: ROB "
                "allocation id must enter rename/dispatch; integer and LSU PRF read addresses must "
                "enter the PRF owner and read data must return; LSU load completion must carry its "
                "physical destination; commit must carry architectural rd and new/old PR to the "
                "committed map; ECALL reads the committed x10, regardless of current debug address. "
                "Architectural map updates and old-PR reclamation require a retiring instruction "
                "that actually writes rd; a store/branch immediate field is not a destination. "
                "Separate instruction retirement from register retirement in the interface. "
                "Recognizing ECALL stops younger fetch/dispatch while older work drains. A "
                "wrong-path ECALL is canceled by older recovery. Before final halt, every external "
                "request which was presented under backpressure must reach an accepted edge; "
                "freezing a bus address alone does not permit withdrawing its valid. "
                "A store at ROB head must be able to start BEFORE done, and become done after its "
                "write response. ECALL must become done without waiting for itself to retire. "
                "Check circular squash relative to ROB HEAD, preserving older entries across wrap; "
                "register reclamation and branch checkpoints must preserve older commits and the "
                "resolving JAL's own destination. Define simultaneous allocate/commit/complete/flush "
                "priorities and avoid lost events. Valid must not wait for ready. Separate frontend, "
                "decode and execute helpers so top is wiring rather than a huge new CPU. At most "
                "11 modules. Dependency lists mean instantiated children only. No reference RTL. "
                "Include global protocols and concise design-review conclusions in notes. "
                "Do not merely repeat prose that is impossible with the listed ports.\n"
                + json.dumps({"draft_plan": plan, "requirements": effective_spec,
                              "contract_errors": l3_plan_errors(plan)}, ensure_ascii=False)
            )
            review = self.runtime.call_model(event, review_prompt, PLANNER_SYSTEM_PROMPT,
                model_options={"thinking": True, "reasoning_effort": "low", "max_tokens": 24000})
            plan = validate_plan(extract_json_payload(review.get("answer") or ""))
            for repair_round in range(3):
                errors = l3_plan_errors(plan)
                if not errors:
                    break
                self.runtime.write_json_artifact(f"plan_contract_rejection_{repair_round}.json",
                                                {"errors": errors, "plan": plan})
                correction = self.runtime.call_model(event,
                    "Correct these concrete plan validation errors. ALL physical register ids and "
                    "PRF read addresses must be SIX bits for 40 entries; architectural rd/rs remain "
                    "FIVE bits. Update every affected producer, consumer and protocol description. "
                    "Return the complete plan JSON, no prose.\n" + json.dumps({"errors": errors, "plan": plan}),
                    PLANNER_SYSTEM_PROMPT, model_options={"thinking": True, "reasoning_effort": "low", "max_tokens": 24000})
                plan = validate_plan(extract_json_payload(correction.get("answer") or ""))
            if l3_plan_errors(plan):
                raise ValueError("Planner could not correct physical register widths")
        if skill_result is not None:
            plan["architecture"] = skill_result
        plan_ref = self.runtime.write_json_artifact("plan.json", plan)
        self.save_context(
            spec_ref=spec_ref,
            plan_ref=plan_ref,
            planner_skill_ref=skill_ref,
        )
        self.finish(
            event,
            "info",
            response=json.dumps(plan, ensure_ascii=False, indent=2),
            reasoning=event.get("reasoning"),
            usage=event.get("usage"),
        )
        return self.runtime.write_handoff(
            "planner",
            "coder",
            {
                "artifacts": {
                    "spec": spec_ref,
                    "plan": plan_ref,
                    **({"skill": skill_ref} if skill_ref else {}),
                }
            },
            label="Planner → Coder: Spec / Plan artifacts",
        )

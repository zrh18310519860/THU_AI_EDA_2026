"""Stateless, scoped exact-anchor Patch Agent; no accumulated project dumps."""

import json
import subprocess
import tempfile
from pathlib import Path

from ai_eda.agent.patches import apply_repair_payload
from ai_eda.agent.repair_context import digest, read_sources, repair_view, source_manifest
from .base import SubAgent
from .common import extract_json_payload, safe_project_path


def validate_patch_compilation(files: dict[str, str], top: str, directory: Path, *, changed_paths=None) -> None:
    """Reject a new elaboration regression before mutating the project."""
    with tempfile.TemporaryDirectory(prefix="patch-check-", dir=directory) as temporary:
        source = Path(temporary) / "candidate.sv"
        source.write_text("\n\n".join(files.values()), encoding="utf-8")
        checked = subprocess.run(["iverilog", "-g2012", "-DRV32I_FEATURE_PORTS", "-s", top,
                                  "-o", str(Path(temporary) / "candidate.vvp"), str(source)],
                                 capture_output=True, text=True, timeout=30)
        if checked.returncode:
            if changed_paths:
                # During startup repair, unrelated modules may still fail. Each edited
                # module must at least parse with unresolved children ignored.
                for index, path in enumerate(changed_paths):
                    module_source = Path(temporary) / f"edited_{index}.sv"
                    module_source.write_text(files[path], encoding="utf-8")
                    parsed = subprocess.run(["iverilog", "-g2012", "-DRV32I_FEATURE_PORTS", "-i", "-tnull", str(module_source)],
                                            capture_output=True, text=True, timeout=30)
                    if parsed.returncode:
                        raise ValueError("Edited module still fails compilation: " + path + "\n" + parsed.stderr[-5000:])
                return
            raise ValueError("Patch introduces compile/elaboration failure: " + checked.stderr[-5000:])


class ModifierAgent(SubAgent):
    name = "modifier"
    title = "06 · Patch Agent"
    node_type = "model"

    def run(self, input_handoff: Path, attempt: int) -> Path:
        incoming = self.runtime.read_handoff(input_handoff)["payload"]
        refs = incoming["artifacts"]
        brief = self.runtime.read_json_artifact(refs["repair_brief"])
        packet = self.runtime.read_json_artifact(refs["repair_packet"])
        files = read_sources(self.runtime, refs["rtl_manifest"])
        paths = brief["paths"]
        for path, expected in brief.get("source_sha256", {}).items():
            if expected and digest(files[path]) != expected:
                raise ValueError("Stale repair brief: " + path)
        plan = self.runtime.read_json_artifact(refs["plan"])
        spec = self.runtime.read_text_artifact(refs["spec"])
        view = repair_view(spec, plan, files, packet, paths,
                          str((self.runtime.options.get("project") or {}).get("id", "")))
        view["repair_brief"] = brief
        prompt = json.dumps(view, ensure_ascii=False, indent=2)
        system = ('You are a surgical RTL Patch Agent. Only edit the scoped editable_sources. '
            'Return JSON {"schema_version":1,"edits":[{"path":"rtl/file.sv",'
            '"expected":"exact unique old text","replacement":"new text"}],"notes":"reason"}. '
            'Anchors must appear exactly once in current text, no line numbers in anchors. '
            'Keep teacher top ports/module names and required architecture. Internal ports may change '
            'only when every affected producer, consumer and instantiation is in scope and patched together. No test edits, fake events, '
            'test-specific hardcoding, initial, packages, or unsynthesizable code. '
            'Use untyped localparam/parameter and synchronous active-low reset. '
            'Fix the diagnosed cause, not the whole project. Do not return entire unchanged files.')
        system += (' Inspect all existing drivers before adding assignments or always blocks. '
                   'A selected complete source includes its tail. When source_complete is false, '
                   'never infer that an omitted signal assignment does not exist. Do not add '
                   'a second sequential writer to an already-driven signal. '
                   'For the RV32I external memory bus, use the fixed same-cycle contract: '
                   'mem_req_valid && mem_req_ready is the acceptance edge and mem_resp_valid '
                   'with mem_resp_data is consumed on that same edge. Do not add a registered '
                   'response delay to only the top bridge or only the cache. Keep request payload '
                   'stable until ready, advance refill counters only on accepted words, and use '
                   'the current mem_resp_data for the final refill word.')
        event = self.runtime.begin_event(self.name, self.node_type, self.title, prompt, attempt)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": prompt}]
        for retry in range(3):
            result = self.runtime.call_model_messages(event, messages,
                model_options={"thinking": True, "reasoning_effort": "low", "max_tokens": 16000})
            try:
                payload = extract_json_payload(result["answer"])
                application = apply_repair_payload(payload, {p: files[p] for p in paths})
                validate_patch_compilation({**files, **application.changed_files},
                    plan["top_module"], self.runtime.run_dir,
                    changed_paths=list(application.changed_files) if packet.get("stage") == "compile" else None)
                break
            except ValueError as error:
                if retry >= 2:
                    raise
                # Keep the retry bounded, but make the validator's exact reason
                # visible so the model can rewrite the edit list rather than
                # repeating an overlapping or stale operation.
                messages = messages[:2]
                messages.append({"role": "assistant", "content": result["answer"][:48000]})
                messages.append({"role": "user", "content":
                    "Patch validation failed: " + str(error) +
                    ". Re-read the current source and return a NEW complete edits list. "
                    "Omit redundant edits whose expected text already equals their replacement "
                    "after an earlier edit; do not return unchanged files or repeat stale anchors. "
                    "Re-check every edit in order before returning JSON."})
        for path, text in application.changed_files.items():
            target = safe_project_path(self.runtime.run_dir, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        files.update(application.changed_files)
        manifest_ref = source_manifest(self.runtime, files, f"rtl_manifest_attempt{attempt}.json")
        patch_ref = self.runtime.write_json_artifact(f"patch_{attempt}.json", payload)
        provenance_ref = self.runtime.write_json_artifact(f"patch_manifest_{attempt}.json", application.manifest)
        self.save_context(last_patch=provenance_ref, last_manifest=manifest_ref)
        self.finish(event, "info", response=str(payload.get("notes", "")),
                    changed_paths=list(application.changed_files), context_chars=len(prompt))
        return self.runtime.write_handoff("modifier", "tester", {
            "attempt": attempt, "changed_paths": list(application.changed_files),
            "artifacts": {"spec": refs["spec"], "plan": refs["plan"], "rtl_manifest": manifest_ref,
                          "patch": patch_ref, "patch_manifest": provenance_ref}},
            attempt=attempt, label="Patch Agent → Tester: immutable patched sources")

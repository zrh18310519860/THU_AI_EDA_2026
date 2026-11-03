"""Route compact Tester evidence to an explicit, bounded repair brief."""

import json
from pathlib import Path

from ai_eda.agent.repair_context import digest, read_sources, repair_view, select_paths, source_windows
from ai_eda.agent.skills.cpu_diagnostics import build_cpu_repair_packet
from .base import SubAgent
from .common import extract_json_payload


def validate_diagnosis_evidence(brief, files):
    paths = brief.get("paths")
    evidence = brief.get("evidence")
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 4
            or any(not isinstance(p, str) or p not in files for p in paths)
            or len(set(paths)) != len(paths)):
        raise ValueError("Diagnoser returned invalid repair scope")
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 12:
        raise ValueError("Cite current source anchors for every edit path, at most twelve anchors")
    cited = {}
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("Invalid diagnosis evidence")
        path, anchor = item.get("path"), item.get("anchor")
        if (path not in files or not isinstance(anchor, str)
                or not 12 <= len(anchor.strip()) <= 320 or anchor not in files[path]):
            raise ValueError("Diagnosis evidence is absent from the CURRENT source: " + str(path))
        cited.setdefault(path, item)
    if not set(paths) <= set(cited):
        raise ValueError("Every proposed edit path needs current source evidence")
    brief["evidence"] = [cited[path] for path in paths]


DIAGNOSIS_FORMAT = (
    'Return ONLY one compact JSON object with evidence, root_cause, paths, changes, '
    'preserve and validation. At most four unique paths; exactly ONE evidence object '
    'per path, each anchor 12..320 characters copied verbatim from CURRENT sources. '
    'root_cause: at most 1200 characters; each other list: at most six short strings. '
    'Keep the entire response under 2500 characters. No repeated evidence or code output.'
    ' If another module is needed, return ONLY {"read_paths":["rtl/file.sv",...],'
    '"reason":"what evidence is missing"}; request at most four existing paths. '
    'The tool keeps requested and recently viewed CURRENT files in a window of at most '
    'eight files and 96k characters. At most four files may be edited. Request only missing files; '
    'previously viewed files remain available within this bounded source window. '
    'You may read at most five batches; when source_read_budget_remaining is zero, '
    'return the final diagnosis and do not request more files.'
)


def request_diagnosis(runtime, event, prompt, system, files):
    """Retry malformed/truncated JSON and stale evidence through the same bounded path."""
    initial = [{"role": "system", "content": system + "\n" + DIAGNOSIS_FORMAT},
               {"role": "user", "content": prompt}]
    messages = initial
    retry, reads = 0, 0
    max_reads = 5
    while retry < 3:
        result = runtime.call_model_messages(event, messages,
            model_options={"thinking": True, "reasoning_effort": "low", "max_tokens": 24000})
        try:
            brief = extract_json_payload(result["answer"])
            if "read_paths" in brief:
                requested = brief["read_paths"]
                if (reads >= max_reads or not isinstance(requested, list) or not 1 <= len(requested) <= 4
                        or any(not isinstance(p, str) or p not in files for p in requested)
                        or len(set(requested)) != len(requested)):
                    raise ValueError("Invalid or exhausted source read request")
                view = json.loads(initial[1]["content"])
                selected = list(dict.fromkeys(requested + list(view.get("editable_sources", {}))))[:8]
                sources, complete, remaining = {}, {}, 96000
                for index, path in enumerate(selected):
                    limit = min(64000, remaining - 4000 * (len(selected) - index - 1))
                    sources[path] = source_windows(files[path], json.dumps(view.get("diagnostics", {})), limit)
                    complete[path] = len(files[path]) <= limit
                    remaining -= len(sources[path])
                view.update(editable_sources=sources, source_complete=complete,
                            source_sha256={p: digest(files[p]) for p in selected},
                            source_read_reason=brief.get("reason", ""),
                            source_read_budget_remaining=max_reads - reads - 1,
                            source_read_history=event.get("source_reads", []) + [{"paths": requested}],
                            next_action="Return final diagnosis; source-read budget exhausted." if reads + 1 == max_reads else "Read only missing evidence or return final diagnosis.")
                initial = [initial[0], {"role": "user", "content": json.dumps(view, ensure_ascii=False)}]
                messages = initial
                reads += 1
                event.setdefault("source_reads", []).append({"paths": requested, "reason": brief.get("reason", "")})
                continue
            validate_diagnosis_evidence(brief, files)
            view = json.loads(initial[1]["content"]) if initial[1]["content"].lstrip().startswith("{") else {}
            diagnostics = view.get("diagnostics", {})
            compiler_errors = [item for item in diagnostics.get("compile_diagnostics", [])
                               if item.get("severity") == "error"]
            if diagnostics.get("stage") == "compile" and compiler_errors:
                if compiler_errors[0]["path"] not in brief["paths"]:
                    raise ValueError("Repair the first mapped compiler error in " + compiler_errors[0]["path"] + " before functional changes")
            if not isinstance(brief.get("root_cause"), str) or not brief["root_cause"].strip():
                raise ValueError("root_cause must describe the specific current cause")
            return brief
        except ValueError as error:
            retry += 1
            if retry == 3:
                raise ValueError("Diagnoser failed validation after three calls: " + str(error)) from error
            # Keep only the current snapshot and the latest rejected response.
            messages = initial + [{"role": "assistant", "content": result["answer"][:6000]},
                {"role": "user", "content": "Diagnosis validation failed: " + str(error) +
                 ". Previous finish_reason=" + str(result.get("finish_reason")) +
                 ". Re-read CURRENT sources; discard contradicted older hypotheses. " + DIAGNOSIS_FORMAT}]


class DiagnoserAgent(SubAgent):
    name = "diagnoser"
    node_type = "model"
    title = "05 · Diagnoser"

    def run(self, handoff: Path, attempt: int) -> Path:
        incoming = self.runtime.read_handoff(handoff)["payload"]
        refs = incoming["artifacts"]
        report = self.runtime.read_json_artifact(refs["report"])
        packet = build_cpu_repair_packet(report, max_check_groups=12, first_trace_events=3, last_trace_events=8)
        packet["metrics"] = report.get("metrics", {})
        packet["objective"] = incoming.get("objective", "repair")
        if packet.get("failure_probes"):
            # Focus on one actual failing program; unrelated successful DFT activity
            # and broad pattern audits are retained in artifacts, not this repair prompt.
            packet["repair_focus"] = packet["failure_probes"][0]["testcase"]
            packet["failure_probes"] = packet["failure_probes"][:1]
            flow = packet["failure_probes"][0].get("observations", {}).get("retirement_flow", [])
            if flow:
                packet["earliest_architectural_observation"] = flow[0]
                packet["diagnosis_priority"] = (
                    "Explain the earliest public retirement PC gap before final register, store "
                    "count or exit-code errors. Identify whether that instruction was fetched, "
                    "accepted into ROB, completed and committed. A later missing store is not "
                    "evidence that the store-write-through handshake caused an earlier lost load.")
            # Probe already budgets each reset segment. Do not truncate its
            # beginning again: that removed the first lost handshake before.
            focused = [group for group in packet["checks"]["groups"]
                       if group["testcase"] == packet["repair_focus"] or
                       (packet["repair_focus"] == "l3_smoke" and group["testcase"].startswith("smoke_"))]
            packet["checks"]["groups"] = focused or packet["checks"]["groups"][:3]
            packet["trace_windows"] = {"source": "primary teacher TB", "focus": packet["repair_focus"],
                "events": [item for item in report.get("trace", []) if item.get("test") == packet["repair_focus"]][-12:]}
            packet["differential"] = {key: packet["differential"].get(key) for key in
                                      ("kind", "count", "passed", "note")}
            packet["causal_hints"] = {}
            packet["test_context"].pop("smoke_source", None)
        files = read_sources(self.runtime, refs["rtl_manifest"])
        plan = self.runtime.read_json_artifact(refs["plan"])
        spec = self.runtime.read_text_artifact(refs["spec"])
        paths = select_paths(packet, files, plan)
        optimization_index = int(self.context.get("optimization_index", 0))
        focus = None
        if packet["objective"] == "optimize":
            focus = ("Choose ONE bounded optimization in ONE existing module using this run's "
                     "measured area, total_power_w, area_by_module and mapping evidence. "
                     "Read other current sources if needed. Explain the actual logic/storage cost "
                     "and why the proposed representation preserves behavior. Minimize measured "
                     "area * total_power_w; do not predict or invent result numbers. Preserve "
                     "capacity, timing contracts, reset-visible values and real OoO capability. "
                     "The complete functional tests and PPA flow will decide whether it is retained.")
        previous = self.context.get("history", [])[-3:]
        view = repair_view(spec, plan, files, packet, paths,
                           str((self.runtime.options.get("project") or {}).get("id", "")), diagnose=True)
        view["previous_hypotheses_not_current_facts"] = previous
        view["source_read_budget_remaining"] = 5
        view["rejected_patch_feedback"] = incoming.get("rejected_patch_feedback")
        view["current_source_facts"] = {path: [line.strip() for line in files[path].splitlines()
            if any(name in line for name in ("line_data;", "line_tag;", "req_tag;", "refill_tag;", "assign issue_done", "ready_vec <="))][:16]
            for path in paths}
        if focus:
            view["optimization_focus"] = focus
        prompt = json.dumps(view, ensure_ascii=False)
        event = self.runtime.begin_event(self.name, self.node_type, self.title, prompt, attempt)
        brief = request_diagnosis(self.runtime, event, prompt,
            ('You diagnose SystemVerilog compilation with Icarus -g2012. Inspect the exact '
             'FIRST mapped compiler error and current source, including reserved identifiers, '
             'types, punctuation and declarations. ANSI function ports with logic return types '
             'are supported in SystemVerilog; do not claim a language construct is unsupported '
             'without evidence. compile_diagnostics maps the generated file directly to editable '
             'original modules. Fix those originals, never the generated file separately. '
             'Review previous hypotheses: a source change that still fails compilation disproves '
             'the claimed cure. Choose a different cause; do not oscillate between equivalent '
             'declaration styles. Return a short evidence-based JSON diagnosis, no RTL. '
             'Preserve working behavior and ports. No teacher test edits.'
             if packet.get('stage') == 'compile' else
            'Diagnose CURRENT RTL using the named failing test, current sources and observed '
            'evidence. Return a scoped diagnosis, not code. test_context identifies the actual '
            'stimulus. When repair_focus is present, repair only that testcase\'s earliest '
            'demonstrated failure; other test results will be revisited after the next full test. '
            'stimulus: checks/trace belong to the primary test, while differential runs a separate '
            'DFT program. Never combine their PCs, waveforms or expected registers into one timeline. '
            'Read the supplied stimulus instead of guessing what its instructions are. '
            'Find the first observed divergence or blocked accepting edge, trace the responsible '
            'producer and consumer, and explain how a minimal change resolves it. '
            'earliest_architectural_observation is a direct public-port symptom from the primary '
            'program before waveform truncation; account for it explicitly when present. '
            'A timeout can cause many downstream register/memory failures; these are not independent root causes. '
            'Source-pattern audits and causal_hints are search aids, not proof. Verify their claims '
            'against current RTL. Previous hypotheses refer to older snapshots; discard those '
            'contradicted by current sources or unchanged test results. Do not replace one working '
            'expression with an algebraically equivalent expression and call that a repair. '
            'Inspect all current assignments before claiming a signal is undriven or adding a '
            'driver. Missing excerpts and failed diagnostic bindings are not absent RTL. '
            'Distinguish ready/valid acceptance, response delivery, buffering, completion and '
            'architectural retirement. Account for nonblocking updates and simultaneous events. '
            'Use the supplied authoritative memory timing contract rather than inventing another '
            'response cycle. Unknown payloads in invalid entries are legal; architectural side '
            'effects require a live accepted operation. '
            'Choose only modules requiring edits, at most four. If an internal contract needs '
            'correction, include producer, consumer and instantiations in the same scoped patch. '
            'Preserve fixed teacher ports, capacities and real out-of-order capability. Request '
            'missing current sources only when needed to distinguish concrete hypotheses. State '
            'uncertainty without inventing waveform values. '
            'For PPA failures or optimization, use only the current measured synthesis/activity '
            'evidence and preserve all functional behavior. An optimization changes one module '
            'and is accepted only after complete functional and PPA evaluation. Do not change '
            'teacher tests, invent measurements, fake events or serialize the CPU.'), files)
        requested = brief.get("paths")
        if not isinstance(requested, list) or not 1 <= len(requested) <= 4 or any(p not in files for p in requested):
            raise ValueError("Diagnoser returned invalid repair scope")
        if focus and len(requested) != 1:
            raise ValueError("An optimization candidate must change exactly one module")
        brief["source_sha256"] = {p: digest(files[p]) for p in requested}
        brief["failure_fingerprint"] = packet["failure_fingerprint"]
        brief["objective"] = packet["objective"]
        packet_ref = self.runtime.write_json_artifact(f"repair_packet_{attempt}.json", packet)
        brief_ref = self.runtime.write_json_artifact(f"repair_brief_{attempt}_step{event['step']}.json", brief)
        self.save_context(optimization_index=optimization_index + int(focus is not None),
                         history=(previous + [{"fingerprint": packet["failure_fingerprint"],
                            "root_cause": str(brief.get("root_cause", ""))[:1200], "paths": requested}])[-3:])
        self.finish(event, "info", response=json.dumps(brief, ensure_ascii=False))
        return self.runtime.write_handoff("diagnoser", "modifier", {
            "attempt": attempt, "artifacts": {**refs, "repair_packet": packet_ref, "repair_brief": brief_ref}},
            attempt=attempt, revision=event["step"], label="Diagnoser → Patch Agent: scoped repair brief")

"""Route compact Tester evidence to an explicit, bounded repair brief."""

import json
from pathlib import Path

from ai_eda.agent.repair_context import digest, read_sources, repair_view, select_paths, source_windows
from ai_eda.agent.skills.cpu_diagnostics import build_cpu_repair_packet, named_probe_view
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
    missing = [path for path in paths if path not in cited]
    if missing:
        raise ValueError("Missing CURRENT source evidence for edit paths: " + ", ".join(missing))
    brief["evidence"] = [cited[path] for path in paths]


def refresh_source_window(view, files, requested):
    """Prioritize the requested current files without growing the source window."""
    selected = list(dict.fromkeys(requested + list(view.get("editable_sources", {}))))[:8]
    sources, complete, remaining = {}, {}, 96000
    for index, path in enumerate(selected):
        limit = min(64000, remaining - 4000 * (len(selected) - index - 1))
        sources[path] = source_windows(files[path], json.dumps(view.get("diagnostics", {})), limit)
        complete[path] = len(files[path]) <= limit
        remaining -= len(sources[path])
    view.update(editable_sources=sources, source_complete=complete,
                source_sha256={p: digest(files[p]) for p in selected})
    return view


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
    format_retries, evidence_retries, reads = 0, 0, 0
    max_reads = 5
    while format_retries < 3 and evidence_retries < 3:
        result = runtime.call_model_messages(event, messages,
            model_options={"thinking": False, "temperature": 0.1, "max_tokens": 8000})
        brief = None
        try:
            brief = extract_json_payload(result["answer"])
            if "read_paths" in brief:
                requested = brief["read_paths"]
                if (reads >= max_reads or not isinstance(requested, list) or not 1 <= len(requested) <= 4
                        or any(not isinstance(p, str) or p not in files for p in requested)
                        or len(set(requested)) != len(requested)):
                    raise ValueError("Invalid or exhausted source read request")
                view = json.loads(initial[1]["content"])
                refresh_source_window(view, files, requested)
                view.update(source_read_reason=brief.get("reason", ""),
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
            # A truncated JSON reply must not consume the source-evidence correction budget.
            if brief is None:
                format_retries += 1
                retry_kind = "JSON format"
                exhausted = format_retries == 3
            else:
                evidence_retries += 1
                retry_kind = "source evidence or schema"
                exhausted = evidence_retries == 3
            event.setdefault("diagnosis_validation_retries", []).append({"kind": retry_kind, "reason": str(error)})
            if exhausted:
                raise ValueError("Diagnoser failed validation after three calls for " + retry_kind + ": " + str(error)) from error
            requested = (brief or {}).get("paths")
            if (isinstance(requested, list) and 1 <= len(requested) <= 4
                    and all(isinstance(p, str) and p in files for p in requested)
                    and len(set(requested)) == len(requested)
                    and initial[1]["content"].lstrip().startswith("{")):
                view = json.loads(initial[1]["content"])
                refresh_source_window(view, files, requested)
                view["source_refresh_reason"] = str(error)
                initial = [initial[0], {"role": "user", "content": json.dumps(view, ensure_ascii=False)}]
                event.setdefault("scope_refreshes", []).append({"paths": requested, "reason": str(error)})
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
        if incoming.get("evaluated_candidates"):
            packet["evaluated_candidates"] = incoming["evaluated_candidates"]
            packet["best_area_power_product"] = incoming["best_area_power_product"]
        if packet.get("failure_probes"):
            # Focus on one actual failing program; unrelated successful DFT activity
            # and broad pattern audits are retained in artifacts, not this repair prompt.
            packet["repair_focus"] = packet["failure_probes"][0]["testcase"]
            probe = packet["failure_probes"][0]
            if probe.get("testcase") == "l3_smoke":
                observation = probe.get("observations") or {}
                segments = observation.get("segments") or []
                failures = packet["checks"]["groups"]
                selected_segment = next(((index, segment) for index, segment in enumerate(segments)
                    if any(group["testcase"] == "smoke_wait_" + str(int(segment["memory_wait"], 16))
                           for group in failures)), None)
                if selected_segment:
                    index, segment = selected_segment
                    end = segments[index + 1]["start"] if index + 1 < len(segments) else float("inf")
                    probe = {**probe, "focused_wait_mode": segment["memory_wait"],
                        "observations": {**observation, "segments": [segment],
                            "events": [row for row in observation.get("events", [])
                                       if segment["start"] <= row[0] < end]}}
                    packet["repair_focus"] = "smoke_wait_" + str(int(segment["memory_wait"], 16))
            packet["failure_probes"] = [named_probe_view(probe)]
            control = packet["failure_probes"][0].get("program_control_differential") or {}
            if control.get("kind") == "retire_pc":
                packet["earliest_architectural_observation"] = control
                packet["diagnosis_priority"] = (
                    "The isolated unchanged teacher program first retires a PC different from the "
                    "sequential ISA model. The waveform is focused around previous_instruction, "
                    "which precedes that divergence. Explain this specific next-PC discrepancy, "
                    "using actual issue operands and predictor/recovery metadata. This control "
                    "comparison does not sample register results; do not invent such observations.")
            flow = packet["failure_probes"][0].get("observations", {}).get("retirement_flow", [])
            if flow and control.get("kind") != "retire_pc":
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
        differential = report.get("differential") or {}
        if (packet.get("repair_focus") in {"t13_dft", None}
                and differential.get("kind") == "register_write"
                and differential.get("focused_observations")):
            packet["repair_focus"] = "diff_dft"
            packet["failure_probes"] = [{"testcase": "diff_dft",
                "observations": differential["focused_observations"],
                "stimulus_source": differential.get("stimulus_source"),
                "provenance": differential.get("provenance")}]
            packet["differential"] = {key: differential.get(key) for key in
                ("kind", "count", "mismatch", "history", "provenance")}
            packet["trace_windows"] = {"source": "diff_dft only", "events": []}
            packet["earliest_architectural_observation"] = differential["mismatch"]
            packet["diagnosis_priority"] = (
                "Repair the first demonstrated retired value mismatch in this separate diff_dft "
                "invocation. Its supplied named waveform window surrounds that exact retirement. "
                "Do not infer the cause from the later timeout or mix primary-teacher timestamps.")
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
                     "evaluated_candidates contains actual measurements and patch summaries from "
                     "earlier trials. Do not repeat a rejected, unchanged or worse proposal from "
                     "that list: each trial starts from the best snapshot, so the same source "
                     "appearing again does NOT mean its previous proposal was never tested. "
                     "Unused storage is normally removed by synthesis; verify mapped cost before "
                     "claiming that deleting an unread declaration will reduce area. "
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
            'For an exact differential mismatch, use its decoded operation and source_registers. '
            'An immediate instruction does not read the bits named rs2 in its encoding. '
            'operand_lifetime contains earlier observed writes, allocations, retirements and '
            'recoveries involving the selected physical sources; use it to trace a wrong '
            'operand before blaming an ALU whose result agrees with its actual inputs. '
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
        if packet["objective"] == "repair" and packet.get("stage") != "compile":
            review_sources = {}
            remaining = 96000
            for path in brief["paths"]:
                excerpt = source_windows(files[path], brief["root_cause"], min(64000, remaining))
                review_sources[path] = {"source": excerpt, "source_complete": len(excerpt) >= len(files[path])}
                remaining -= len(excerpt)
            reviewed = self.runtime.call_model(event, json.dumps({"current_diagnostics": packet,
                "current_sources": review_sources, "proposed_diagnosis": brief}, ensure_ascii=False),
                "Independently audit this RTL diagnosis against CURRENT sources and CURRENT named "
                "observations. The previous explanation may be stale even when its quoted anchor "
                "exists. Check every claimed gate, state transition, time and instruction against "
                "the actual source; an expression already removed cannot explain the present "
                "failure. Do not copy its narrative by default. Reconstruct the first failing "
                "handshake or architectural edge. Treat state values sampled BEFORE a rising edge "
                "consistently with NBA updates. Different reset segments and memory-wait modes are "
                "different executions. Do not use an event from one to explain another. Correct "
                "contradictions, distinguish established facts from uncertainty, and return a "
                "COMPLETE compact diagnosis JSON with evidence, root_cause, paths, changes, preserve "
                "and validation. Evidence anchors must be verbatim from current files, 12..320 "
                "characters. At most four paths, all among current_sources. No RTL or test edits. "
                "Preserve all capacities, bus contracts, genuine out-of-order execution and teacher "
                "requirements. Never propose a change already present in CURRENT RTL.",
                model_options={"thinking": True, "reasoning_effort": "low", "max_tokens": 16000})
            corrected = extract_json_payload(reviewed["answer"])
            validate_diagnosis_evidence(corrected, files)
            if not set(corrected["paths"]) <= set(review_sources):
                raise ValueError("Diagnosis review expanded beyond its actual current source evidence")
            self.runtime.write_json_artifact(f"diagnosis_review_{attempt}_step{event['step']}.json",
                                            {"initial": brief, "reviewed": corrected})
            brief = corrected
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

"""固定 Agent 流程的受限 EDA 自动设计执行器。

固定流水线：Parser → RTL Planner → Coder → Tester → Modifier（失败回环）。
Parser 需要补充 Spec 时通过 ``request_input`` 暂停并等待用户输入。
"""

from __future__ import annotations

import json
import math
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ai_eda.agent.projects import get_project
from ai_eda.agent.runs import (
    AgentCancelled,
    _now,
    _report_summary,
    _run_payload,
    _write_json,
    _write_metadata,
    agent_task_spec,
    load_agent_graph,
)
from ai_eda.agent.subagents.base import RunContext
from ai_eda.agent.subagents.coder import CoderAgent
from ai_eda.agent.subagents.diagnoser import DiagnoserAgent
from ai_eda.agent.repair_context import source_manifest, logic_fingerprint
from ai_eda.agent.subagents.common import safe_project_path
from ai_eda.agent.subagents.modifier import ModifierAgent
from ai_eda.agent.subagents.parser import ParserAgent
from ai_eda.agent.subagents.planner import PlannerAgent
from ai_eda.agent.subagents.tester import TesterAgent
from ai_eda.config import AGENT_RUNS_ROOT
from ai_eda.model.client import ModelClient

EventSink = Callable[[dict[str, Any]], None]


def candidate_score(report: dict[str, Any]) -> float | None:
    metrics = report.get("metrics") or {}
    if not report.get("passed") or metrics.get("status") != "ok":
        return None
    values = [metrics.get("area"), metrics.get("total_power_w")]
    if any(not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 for v in values):
        return None
    return float(values[0]) * float(values[1])


def measured_candidate_history(candidates, events, source_run):
    history = []
    for candidate in candidates[-8:]:
        patches = [item for item in events if item.get("node_id") == "modifier"
                   and item.get("status") == "info"
                   and item.get("attempt") == candidate["attempt"] - 1]
        patch = patches[-1] if patches else {}
        snapshot = AGENT_RUNS_ROOT / source_run / f"artifacts/tested_manifest_{candidate['attempt']}.json"
        fingerprint = None
        if snapshot.is_file():
            entries = json.loads(snapshot.read_text())["files"]
            sources = {item["path"]: (AGENT_RUNS_ROOT / source_run / item["artifact"]).read_text()
                       for item in entries}
            fingerprint = logic_fingerprint(sources)
        history.append({**candidate, "source_run": source_run, "logic_fingerprint": fingerprint,
            "patch_summary": str(patch.get("response", "Initial accepted candidate"))[:1600],
            "changed_paths": patch.get("changed_paths", [])})
    return history


def _check_cancel(cancel_event: Any) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise AgentCancelled("Agent 运行已取消。")


def _load_resume_source(run_id: str, snapshot_attempt: int | None = None) -> dict[str, Any]:
    """读取上一次运行的 Spec/Plan，用于从 Coder 继续执行。"""
    name = str(run_id or "").strip()
    if not name or Path(name).name != name or name in {".", ".."}:
        raise ValueError("resume_from 必须是单个运行目录名")
    root = AGENT_RUNS_ROOT.resolve()
    source = (root / name).resolve()
    if root not in source.parents or not source.is_dir():
        raise FileNotFoundError(f"找不到要续跑的运行：{name}")
    artifacts = source / "artifacts"
    spec_path = artifacts / "effective_spec.md"
    if not spec_path.is_file():
        spec_path = artifacts / "spec.md"
    plan_path = artifacts / "plan.json"
    if not spec_path.is_file():
        raise ValueError("上一次运行没有 Spec artifact，无法续跑")
    spec = spec_path.read_text(encoding="utf-8")
    plan: dict[str, Any] | None = None
    if plan_path.is_file():
        loaded_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if isinstance(loaded_plan, dict) and isinstance(loaded_plan.get("modules"), list):
            plan = loaded_plan
    parser_result: dict[str, Any] = {}
    parser_path = artifacts / "parser_result.json"
    if parser_path.is_file():
        try:
            parsed = json.loads(parser_path.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                parser_result = parsed
        except json.JSONDecodeError:
            pass
    events_payload: dict[str, Any] = {}
    events_path = source / "events.json"
    if events_path.is_file():
        try:
            loaded = json.loads(events_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                events_payload = loaded
        except json.JSONDecodeError:
            pass
    source_events = events_payload.get("events") if isinstance(events_payload.get("events"), list) else []
    failed_event: dict[str, Any] | None = None
    for event in reversed(source_events):
        if not isinstance(event, dict):
            continue
        if event.get("status") in {"failed", "cancelled", "running"}:
            failed_event = event
            break
    if failed_event is None and source_events:
        failed_event = source_events[-1] if isinstance(source_events[-1], dict) else None
    stage = str((failed_event or {}).get("node_id") or "coder")
    if plan is None and stage not in {"parser", "planner"}:
        raise ValueError("上一次运行没有 Plan artifact，无法从 Coder/Tester 续跑")
    title = str((failed_event or {}).get("title") or "")
    module_name = title.rsplit(" · ", 1)[-1] if stage == "coder" and " · " in title else None
    project_files: dict[str, str] = {}
    project_rtl = source / "project" / "rtl"
    if project_rtl.is_dir():
        for path in sorted(project_rtl.glob("*.sv")):
            project_files[f"rtl/{path.name}"] = path.read_text(encoding="utf-8")
    if snapshot_attempt is not None:
        import hashlib
        if type(snapshot_attempt) is not int or snapshot_attempt < 1:
            raise ValueError("snapshot_attempt must be a positive integer")
        manifest = json.loads((artifacts / f"tested_manifest_{snapshot_attempt}.json").read_text(encoding="utf-8"))
        project_files = {}
        for entry in manifest["files"]:
            target = (source / entry["artifact"]).resolve()
            if source.resolve() not in target.parents:
                raise ValueError("Invalid snapshot artifact path")
            text = target.read_text(encoding="utf-8")
            if hashlib.sha256(text.encode("utf-8")).hexdigest() != entry["sha256"]:
                raise ValueError("Resume snapshot hash mismatch")
            project_files[entry["path"]] = text
    selection_path = artifacts / "candidate_selection.json"
    measured = []
    if selection_path.exists():
        selection = json.loads(selection_path.read_text(encoding="utf-8"))
        measured = measured_candidate_history(selection.get("candidates", []), source_events, name)
    return {
        "run_id": name,
        "spec": spec,
        "plan": plan,
        "parser_result": parser_result,
        "source": source,
        "stage": stage,
        "module": module_name,
        "event": failed_event,
        "project_files": project_files,
        "source_status": events_payload.get("status"),
        "measured_candidates": measured,
    }


def _seed_modifier_input(
    context: RunContext,
    source: dict[str, Any],
    spec_ref: str,
    plan_ref: str,
    manifest_ref: str,
) -> Path:
    """为从 Modifier 失败点续跑重建 Tester → Modifier 输入。"""
    source_dir = source["source"]
    candidates = sorted((source_dir / "handoffs").glob("tester_to_modifier_attempt*.json"))
    if not candidates:
        raise ValueError("上一次运行没有 Tester → Modifier handoff，无法从 Modifier 续跑")
    record = json.loads(candidates[-1].read_text(encoding="utf-8"))
    payload = record.get("payload") if isinstance(record, dict) else None
    artifacts = (payload or {}).get("artifacts") or {}
    source_report_ref = artifacts.get("report")
    if not isinstance(source_report_ref, str):
        raise ValueError("上一次运行缺少失败报告，无法从 Modifier 续跑")
    source_report = (source_dir / source_report_ref).resolve()
    if source_dir.resolve() not in source_report.parents or not source_report.is_file():
        raise ValueError("上一次运行的失败报告路径无效")
    report = json.loads(source_report.read_text(encoding="utf-8"))
    report_ref = context.write_json_artifact("resume_report.json", report)
    return context.write_handoff(
        "tester",
        "modifier",
        {
            "attempt": 1,
            "summary": (payload or {}).get("summary") or "上一轮 Tester 失败",
            "artifacts": {
                "spec": spec_ref,
                "plan": plan_ref,
                "rtl_manifest": manifest_ref,
                "report": report_ref,
            },
        },
        attempt=1,
        label=f"Resume {source['run_id']}: continue from Modifier",
    )


def _copy_resume_files(context: RunContext, files: dict[str, str]) -> None:
    for relative, content in files.items():
        target = safe_project_path(context.run_dir, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")


def _seed_resume_project(
    context: RunContext,
    source: dict[str, Any],
    spec_ref: str,
    plan_ref: str,
) -> dict[str, Any]:
    """把上次项目的 RTL 复制到新运行，并建立进入 Tester 的 manifest/handoff。"""
    files = source.get("project_files") or {}
    if not isinstance(files, dict) or not files:
        raise ValueError("上一次运行没有可续跑的 RTL 项目文件")
    for relative, content in files.items():
        target = safe_project_path(context.run_dir, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    manifest_ref = source_manifest(context, files, "rtl_manifest_resume.json")
    tester_input = context.write_handoff(
        "coder",
        "tester",
        {
            "artifacts": {
                "spec": spec_ref,
                "plan": plan_ref,
                "rtl_manifest": manifest_ref,
            }
        },
        label=f"Resume {source['run_id']}: reuse current RTL",
    )
    return {"handler": tester_input, "files": files, "manifest": manifest_ref}


def run_agent(
    *,
    thinking: bool = True,
    reasoning_effort: str = "low",
    temperature: float = 0.2,
    max_tokens: int = 60000,
    max_calls: int = 100,
    max_repair_attempts: int = 8,
    optimization_rounds: int = 0,
    l3_mode: str = "fresh",
    planner_skill: bool = False,
    resume_from: str | None = None,
    snapshot_attempt: int | None = None,
    restart_stage: str | None = None,
    plan_revision: dict[str, Any] | None = None,
    on_event: EventSink | None = None,
    cancel_event: Any = None,
    design_id: str | None = None,
    design_name: str | None = None,
    project_id: str | None = None,
    request_input: Callable[[dict[str, Any]], str] | None = None,
) -> dict[str, Any]:
    """运行固定流水线：Parser → Planner → Coder → Tester ⇄ Modifier。

    ``graph.json`` 只是该固定流程的只读可视化，不参与调度。每个 Agent 都从上游
    handoff 中取得 artifact 引用，再按引用读取 Spec、RTL 或报告内容。
    """
    graph = load_agent_graph()
    if reasoning_effort not in {"low", "high", "max"}:
        raise ValueError("reasoning_effort 无效")
    if not 1 <= int(max_calls) <= 1000:
        raise ValueError("max_calls 必须在 1 到 1000 之间")
    if not 1 <= int(max_repair_attempts) <= 50:
        raise ValueError("max_repair_attempts 必须在 1 到 50 之间")
    max_tester_attempts = int(max_repair_attempts) + 1
    if not 0 <= int(optimization_rounds) <= 8:
        raise ValueError("optimization_rounds must be 0..8")
    project = get_project(project_id)
    if l3_mode != "fresh":
        raise ValueError("New L3 runs must use fresh generation; built-in baseline reuse is disabled. Historical runs remain readable.")
    if not resume_from and (snapshot_attempt is not None or restart_stage or plan_revision is not None):
        raise ValueError("Resume options require an explicit resume_from run ID")
    spec_base = agent_task_spec(project["id"])
    run_id = f"agent-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}"
    run_dir = AGENT_RUNS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_json(run_dir / "model_parameters.json", {
        "model": ModelClient.MODEL_NAME,
        "base_url": ModelClient.BASE_URL,
        "temperature": None if thinking else temperature,
        "max_tokens": max_tokens,
        "thinking": thinking,
        "reasoning_effort": reasoning_effort if thinking else None,
        "max_calls": max_calls,
        "max_repair_attempts": max_repair_attempts,
        "planner_skill": bool(planner_skill),
        "optimization_rounds": optimization_rounds,
        "snapshot_attempt": snapshot_attempt,
        "l3_mode": l3_mode if project["id"] == "rv32i5_ooo" else None,
        "generation_mode": ("resumed" if resume_from else "fresh") if project["id"] == "rv32i5_ooo" else "standard",
        "resumed_from": resume_from,
    })
    _write_json(run_dir / "graph.json", graph)
    (run_dir / "project").mkdir(exist_ok=True)

    events: list[dict[str, Any]] = []
    started_at = _now()
    started_clock = time.perf_counter()
    current_status = "running"
    final_report: dict[str, Any] | None = None
    final_result: dict[str, Any] | None = None
    effective_spec = spec_base
    parser_result: dict[str, Any] = {}
    plan: dict[str, Any] | None = None
    files: list[dict[str, Any]] = []
    tester_attempts = 0

    def persist(status: str = "running", error: str | None = None) -> None:
        _write_json(
            run_dir / "events.json",
            _run_payload(
                run_id=run_id,
                run_dir=run_dir,
                graph=graph,
                events=events,
                status=status,
                started_at=started_at,
                report=final_report,
                result=final_result,
                spec=effective_spec,
                error=error,
                design_id=design_id,
                design_name=design_name,
                project_id=project["id"],
                resumed_from=resume_from,
            ),
        )

    def emit(event: dict[str, Any]) -> None:
        if on_event is not None:
            on_event(json.loads(json.dumps(event, ensure_ascii=False)))

    def begin_event(
        node_id: str, node_type: str, title: str, prompt: str, attempt: int = 1
    ) -> dict[str, Any]:
        event: dict[str, Any] = {
            "run_id": run_id,
            "step": len(events) + 1,
            "node_id": node_id,
            "node_type": node_type,
            "title": title,
            "prompt": prompt,
            "status": "running",
            "attempt": attempt,
            "started_at": _now(),
            "_started_clock": time.perf_counter(),
        }
        events.append(event)
        persist()
        emit(event)
        return event

    def finish_event(event: dict[str, Any], status: str, **fields: Any) -> None:
        event.update(fields)
        event["status"] = status
        event["duration_ms"] = round(
            (time.perf_counter() - event.get("_started_clock", time.perf_counter())) * 1000
        )
        event.pop("_started_clock", None)
        event["finished_at"] = _now()
        persist()
        emit(event)

    def check_cancel() -> None:
        _check_cancel(cancel_event)

    persist()
    try:
        context = RunContext(
            run_dir=run_dir,
            options={
                "temperature": temperature,
                "max_tokens": max_tokens,
                "thinking": thinking,
                "reasoning_effort": reasoning_effort,
                "planner_skill": bool(planner_skill),
                "project": project,
                "l3_mode": l3_mode,
                "resume_from": resume_from,
            },
            begin_event=begin_event,
            finish_event=finish_event,
            persist=persist,
            emit=emit,
            check_cancel=check_cancel,
            request_input=request_input,
            max_calls=max_calls,
        )
        if project["id"] == "rv32i5_ooo":
            context.write_json_artifact("exploration_provenance.json", {
                "generation_mode": "resumed" if resume_from else "fresh",
                "initial_project_files": [],
                "resume_from": resume_from,
                "builtin_rtl_allowed": False,
                "external_rtl_seed_allowed": bool(resume_from),
                "policy": "New run: model plans and generates all RTL from the teacher spec. Explicit resume is labelled separately. No fallback to reference RTL.",
            })
        resume_source = None
        if resume_from:
            resume_source = _load_resume_source(resume_from, snapshot_attempt)
            if snapshot_attempt is not None:
                context.write_json_artifact("resume_snapshot_provenance.json", {
                    "source_run": resume_from, "tested_attempt": snapshot_attempt,
                    "reason": "Explicit immutable tested snapshot selected; original records unchanged."})
            effective_spec = resume_source["spec"]
            plan = resume_source["plan"]
            parser_result = resume_source.get("parser_result") or {}
            resume_stage = str(resume_source.get("stage") or "coder")
            if restart_stage:
                if restart_stage not in {"planner", "coder", "tester", "diagnoser"}:
                    raise ValueError("Invalid restart_stage")
                resume_stage = restart_stage
            if plan_revision is not None:
                from ai_eda.agent.subagents.common import validate_plan
                context.write_json_artifact("previous_plan.json", plan)
                plan = validate_plan(plan_revision)
                context.write_json_artifact("plan_revision_provenance.json", {
                    "source_run": resume_from, "restart_stage": resume_stage,
                    "reason": "Explicit interface-contract revision before regeneration; old evidence retained."})
            if resume_stage == "modifier":
                # 续跑从 Modifier 输入开始；随后第一次 Tester 应使用第 2 轮，
                # 避免与 seed 的 tester_to_modifier_attempt1.json 重名。
                tester_attempts = 1
            spec_ref = context.write_text_artifact("effective_spec.md", effective_spec)
            plan_ref = context.write_json_artifact("plan.json", plan)
            parser_result_ref = context.write_json_artifact(
                "parser_result_resume.json", parser_result
            )
            if resume_stage == "parser":
                initial_handoff = context.write_handoff(
                    "spec",
                    "parser",
                    {"task": project["id"], "artifacts": {"spec": context.write_text_artifact("spec.md", effective_spec)}},
                    label=f"Resume {resume_from}: continue from Parser",
                )
                parser = ParserAgent(context)
                parser_handoff = parser.run(initial_handoff)
                planner = PlannerAgent(context)
                planner_handoff = planner.run(parser_handoff)
                coder = CoderAgent(context)
                next_tester_input = coder.run(planner_handoff)
            elif resume_stage == "planner":
                parser_result_ref = context.write_json_artifact(
                    "parser_result_resume.json", parser_result
                )
                parser_handoff = context.write_handoff(
                    "parser",
                    "planner",
                    {
                        "artifacts": {
                            "spec": spec_ref,
                            "parser_result": parser_result_ref,
                        }
                    },
                    label=f"Resume {resume_from}: continue from Planner",
                )
                planner = PlannerAgent(context)
                planner_handoff = planner.run(parser_handoff)
                coder = CoderAgent(context)
                next_tester_input = coder.run(planner_handoff)
            elif resume_stage == "coder":
                _copy_resume_files(context, resume_source.get("project_files") or {})
                planner_handoff = context.write_handoff(
                    "planner",
                    "coder",
                    {
                        "artifacts": {
                            "spec": spec_ref,
                            "plan": plan_ref,
                            "parser_result": parser_result_ref,
                        }
                    },
                    label=f"Resume {resume_from}: continue from Coder",
                )
                coder = CoderAgent(context)
                next_tester_input = coder.run(
                    planner_handoff,
                    existing_files=resume_source.get("project_files") or {},
                    start_module=None if restart_stage else resume_source.get("module"),
                )
            elif resume_stage in {"tester", "modifier", "diagnoser", "summary"}:
                seeded = _seed_resume_project(
                    context, resume_source, spec_ref, plan_ref
                )
                next_tester_input = seeded["handler"]
                if resume_stage == "diagnoser":
                    source_attempt = snapshot_attempt
                    if source_attempt is None:
                        raise ValueError("Diagnoser restart requires snapshot_attempt")
                    source_report = resume_source["source"] / f"tester-{source_attempt:02d}/test_report.json"
                    report = json.loads(source_report.read_text(encoding="utf-8"))
                    log = source_report.parent / "ppa/yosys.log"
                    if log.exists():
                        from ai_eda.rtl.metrics import mapping_hotspots
                        report.setdefault("metrics", {})["memory_mapping_hotspots"] = mapping_hotspots(log.read_text(encoding="utf-8", errors="replace"))
                    report_ref = context.write_json_artifact("resume_report.json", report)
                    repair_input = context.write_handoff("resume", "diagnoser", {
                        "artifacts": {"spec": spec_ref, "plan": plan_ref,
                            "rtl_manifest": seeded["manifest"], "report": report_ref}},
                        label=f"Resume {resume_from}: diagnose exact tested snapshot {source_attempt}")
                    diagnosis = DiagnoserAgent(context).run(repair_input, 1)
                    next_tester_input = ModifierAgent(context).run(diagnosis, 1)
                    tester_attempts = 1
            else:
                raise ValueError(
                    f"上一次运行停在 {resume_stage}，没有可直接继续的 Coder/Tester 产物"
                )
        else:
            resume_stage = None
            spec_ref = context.write_text_artifact("spec.md", spec_base)
            initial_handoff = context.write_handoff(
                "spec",
                "parser",
                {"task": project["id"], "artifacts": {"spec": spec_ref}},
                label="Input Spec → Parser: Spec artifact",
            )

            parser = ParserAgent(context)
            parser_handoff = parser.run(initial_handoff)

            planner = PlannerAgent(context)
            planner_handoff = planner.run(parser_handoff)
            coder = CoderAgent(context)
            next_tester_input = coder.run(planner_handoff)

        tester = TesterAgent(context)
        modifier = ModifierAgent(context)
        diagnoser = DiagnoserAgent(context)
        best = None
        candidates = []
        optimizations = 0
        failure_fingerprints = []
        while True:
            tester_attempts += 1
            tester_handoff = tester.run(next_tester_input, tester_attempts)
            tester_payload = context.read_handoff(tester_handoff)["payload"]
            tester_artifacts = tester_payload.get("artifacts") or {}
            report_ref = tester_artifacts.get("report")
            if not isinstance(report_ref, str):
                raise ValueError("Tester handoff 缺少报告 artifact")
            report = context.read_json_artifact(report_ref)
            if not isinstance(report, dict):
                raise ValueError("Tester 报告 artifact 格式无效")
            final_report = report
            if report.get("tool_error"):
                raise RuntimeError(report["tool_error"])
            metrics = report.get("metrics") or {}
            score = candidate_score(report)
            accepted = score is not None
            report["accepted"] = accepted
            objective = "repair"
            if not accepted and best is None:
                from ai_eda.agent.skills.cpu_diagnostics import build_cpu_repair_packet
                fingerprint = build_cpu_repair_packet(report)["failure_fingerprint"]
                failure_fingerprints.append(fingerprint)
                if len(failure_fingerprints) >= 3 and len(set(failure_fingerprints[-3:])) == 1:
                    context.write_json_artifact("stagnation.json", {"reason": "Three tests produced identical failure evidence; no more speculative patches.",
                        "attempt": tester_attempts, "fingerprint": fingerprint})
                    break
            if accepted:
                selected = best is None or score < best["score"]
                candidates.append({"attempt": tester_attempts, "area": metrics["area"],
                    "power_w": metrics["total_power_w"], "area_power_product": score,
                    "selected": selected, "report": report_ref})
                if selected:
                    best = {"score": score, "report": report, "handoff": tester_handoff}
                if optimizations >= int(optimization_rounds):
                    break
                objective = "optimize"
                optimizations += 1
            elif best is not None:
                candidates.append({"attempt": tester_attempts, "selected": False,
                    "reason": "functional or PPA regression", "report": report_ref})
                if optimizations >= int(optimization_rounds):
                    break
                optimizations += 1
                objective = "optimize"
            if tester_attempts >= int(max_tester_attempts):
                break
            check_cancel()
            repair_input = best["handoff"] if best is not None else tester_handoff
            if best is not None:
                # Each new candidate starts from the best verified immutable snapshot.
                source_payload = context.read_handoff(repair_input)["payload"]
                measured = ((resume_source or {}).get("measured_candidates", []) +
                            measured_candidate_history(candidates, events, run_id))[-8:]
                repair_input = context.write_handoff("selection", "diagnoser", {
                    **source_payload, "objective": objective,
                    "evaluated_candidates": measured,
                    "best_area_power_product": best["score"]}, attempt=tester_attempts,
                    label="Selection: retain best functionally verified PPA candidate")
            try:
                for rethink in range(3):
                    diagnosis = diagnoser.run(repair_input, tester_attempts)
                    try:
                        next_tester_input = modifier.run(diagnosis, tester_attempts)
                        break
                    except ValueError as patch_error:
                        if rethink == 2:
                            raise
                        for pending in events:
                            if pending.get("status") == "running":
                                finish_event(pending, "failed", error=str(patch_error))
                        # No rejected patch is written; re-diagnose the same tested snapshot.
                        old_payload = context.read_handoff(repair_input)["payload"]
                        repair_input = context.write_handoff("patch_rejection", "diagnoser", {
                            **old_payload, "rejected_patch_feedback": str(patch_error)},
                            attempt=tester_attempts, revision=rethink + 1,
                            label="Patch rejected: re-diagnose unchanged tested sources")
            except Exception as error:
                if best is None:
                    raise
                context.write_json_artifact(f"rejected_proposal_{tester_attempts}.json", {
                    "error": str(error), "retained_best": True,
                    "reason": "Invalid optimization proposal; verified baseline preserved."})
                for pending in events:
                    if pending.get("status") == "running":
                        finish_event(pending, "failed", error=str(error))
                break

        if best is not None:
            final_report = best["report"]
            tester_handoff = best["handoff"]
        context.write_json_artifact("candidate_selection.json", {"objective": "min area * total_power_w",
            "optimization_rounds": optimizations, "candidates": candidates,
            "selected_report": context.read_handoff(tester_handoff)["payload"]["artifacts"]["report"]})

        if final_report is None:
            raise ValueError("Tester 没有产出验收报告")
        final_handoff = context.read_handoff(tester_handoff)["payload"]
        final_artifacts = final_handoff.get("artifacts") or {}
        spec_ref = final_artifacts.get("spec")
        plan_ref = final_artifacts.get("plan")
        manifest_ref = final_artifacts.get("rtl_manifest")
        if not all(isinstance(ref, str) for ref in (spec_ref, plan_ref, manifest_ref)):
            raise ValueError("最终 handoff 缺少 Spec、Plan 或 RTL manifest artifact")
        effective_spec = context.read_text_artifact(spec_ref)
        plan = context.read_json_artifact(plan_ref)
        manifest = context.read_json_artifact(manifest_ref)
        if not isinstance(plan, dict) or not isinstance(manifest, dict):
            raise ValueError("最终 artifact 格式无效")
        files = manifest.get("files") if isinstance(manifest.get("files"), list) else []
        if not resume_from:
            parser_payload = context.read_handoff(parser_handoff)["payload"]
            parser_result_ref = (parser_payload.get("artifacts") or {}).get("parser_result")
            if isinstance(parser_result_ref, str):
                parsed = context.read_json_artifact(parser_result_ref)
                parser_result = parsed if isinstance(parsed, dict) else {}
        passed = bool(final_report.get("accepted"))
        for entry in files:
            target = safe_project_path(run_dir, entry["path"])
            target.write_text(context.read_text_artifact(entry["artifact"]), encoding="utf-8")
        final_result = {
            "passed": passed,
            "generation_mode": ("resumed" if resume_from else "fresh") if project["id"] == "rv32i5_ooo" else "standard",
            "stage": final_report.get("stage"),
            "summary": _report_summary(final_report),
            "metrics": final_report.get("metrics") or {},
            "spec": effective_spec,
            "plan": plan,
            "files": files,
            "parser_rounds": parser_result.get("rounds", 1),
            "supplements": parser_result.get("supplements", []),
            "tester_attempts": tester_attempts,
            "optimization_candidates": candidates,
            "resumed_from": resume_from,
            "cpu_capability": final_report.get("cpu_capability"),
            "test_suites": final_report.get("test_suites"),
            "performance": final_report.get("performance"),
        }
        _write_json(run_dir / "result.json", final_result)
        current_status = "passed" if passed else "failed"
    except AgentCancelled as error:
        current_status = "cancelled"
        for pending in events:
            if pending.get("status") in {"running", "waiting_input"}:
                pending.update(
                    {"status": "cancelled", "finished_at": _now(), "error": str(error)}
                )
        payload = _run_payload(
            run_id=run_id,
            run_dir=run_dir,
            graph=graph,
            events=events,
            status=current_status,
            started_at=started_at,
            finished_at=_now(),
            report=final_report,
            result=final_result,
            spec=effective_spec,
            resumed_from=resume_from,
            error=str(error),
            design_id=design_id,
            design_name=design_name,
            project_id=project["id"],
        )
        payload["duration_ms"] = round((time.perf_counter() - started_clock) * 1000)
        payload["run_summary"]["duration_ms"] = payload["duration_ms"]
        _write_json(run_dir / "events.json", payload)
        try:
            _write_metadata(
                run_dir,
                graph=graph,
                run_id=run_id,
                status=current_status,
                started_at=started_at,
                finished_at=payload["finished_at"],
                attempts=tester_attempts or 1,
                max_repair_attempts=max_repair_attempts,
                resumed_from=resume_from,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                design_id=design_id,
                design_name=design_name,
                project_id=project["id"],
            )
        except Exception:
            pass
        raise
    except Exception as error:
        current_status = "failed"
        for pending in events:
            if pending.get("status") in {"running", "waiting_input"}:
                pending.update(
                    {"status": "failed", "finished_at": _now(), "error": str(error)}
                )
        payload = _run_payload(
            run_id=run_id,
            run_dir=run_dir,
            graph=graph,
            events=events,
            status=current_status,
            started_at=started_at,
            finished_at=_now(),
            report=final_report,
            result=final_result,
            spec=effective_spec,
            resumed_from=resume_from,
            error=str(error),
            design_id=design_id,
            design_name=design_name,
            project_id=project["id"],
        )
        payload["duration_ms"] = round((time.perf_counter() - started_clock) * 1000)
        payload["run_summary"]["duration_ms"] = payload["duration_ms"]
        _write_json(run_dir / "events.json", payload)
        try:
            _write_metadata(
                run_dir,
                graph=graph,
                run_id=run_id,
                status=current_status,
                started_at=started_at,
                finished_at=payload["finished_at"],
                attempts=tester_attempts or 1,
                max_repair_attempts=max_repair_attempts,
                resumed_from=resume_from,
                temperature=temperature,
                max_tokens=max_tokens,
                thinking=thinking,
                reasoning_effort=reasoning_effort,
                design_id=design_id,
                design_name=design_name,
                project_id=project["id"],
            )
        except Exception:
            pass
        raise

    payload = _run_payload(
        run_id=run_id,
        run_dir=run_dir,
        graph=graph,
        events=events,
        status=current_status,
        started_at=started_at,
        finished_at=_now(),
        report=final_report,
        result=final_result,
        spec=effective_spec,
        resumed_from=resume_from,
        design_id=design_id,
        design_name=design_name,
        project_id=project["id"],
    )
    payload["duration_ms"] = round((time.perf_counter() - started_clock) * 1000)
    payload["run_summary"]["duration_ms"] = payload["duration_ms"]
    _write_json(run_dir / "events.json", payload)
    _write_metadata(
        run_dir,
        graph=graph,
        run_id=run_id,
        status=current_status,
        started_at=started_at,
        finished_at=payload["finished_at"],
        attempts=tester_attempts or 1,
        max_repair_attempts=max_repair_attempts,
        resumed_from=resume_from,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        design_id=design_id,
        design_name=design_name,
        project_id=project["id"],
    )
    return payload




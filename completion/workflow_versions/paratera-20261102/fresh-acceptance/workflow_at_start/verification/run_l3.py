"""Reproducible real L3 run; credentials are resolved only by ModelClient."""

import argparse
import hashlib
import json
import signal
from threading import Event
from pathlib import Path

from ai_eda.agent.orchestrator import run_agent
from ai_eda.config import TASKS_ROOT, AGENT_RUNS_ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume")
    parser.add_argument("--resume-attempt",type=int)
    parser.add_argument("--optimize", type=int, default=3)
    parser.add_argument("--repair-attempts", type=int, default=10)
    parser.add_argument("--max-calls", type=int, default=80)
    parser.add_argument("--restart-stage")
    parser.add_argument("--plan")
    parser.add_argument("--mode", choices=["fresh"], default="fresh")
    parser.add_argument("--evidence", default="/mnt/f/AI_EDA/fresh-exploration-20261028")
    args = parser.parse_args()
    evidence = Path(args.evidence)
    evidence.mkdir(parents=True, exist_ok=True)
    from ai_eda.config import PROJECT_ROOT
    workflow = evidence / "workflow_at_start"
    workflow.mkdir(exist_ok=True)
    code_paths = list((PROJECT_ROOT / "ai_eda/agent").rglob("*.py")) + [
        PROJECT_ROOT / "ai_eda/model/client.py", PROJECT_ROOT / "ai_eda/rtl/runner.py",
        PROJECT_ROOT / "ai_eda/rtl/metrics.py", *Path(__file__).parent.glob("*.py")]
    manifest = {}
    for path in sorted(set(code_paths)):
        relative = path.relative_to(PROJECT_ROOT)
        copy = workflow / relative
        copy.parent.mkdir(parents=True, exist_ok=True)
        content = path.read_bytes()
        copy.write_bytes(content)
        manifest[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    (workflow / "sha256.json").write_text(json.dumps(manifest, indent=2))
    teacher = TASKS_ROOT / "homework_rv32i5"
    hashes = {str(p.relative_to(teacher)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(teacher.rglob("*")) if p.is_file()}
    (evidence / "teacher_hashes_before.json").write_text(json.dumps(hashes, indent=2))
    previous = None
    active_run_id = None

    def event(item):
        nonlocal previous, active_run_id
        active_run_id = item["run_id"]
        state = (item["run_id"], item["step"], item["status"])
        if state != previous:
            print(*state, item["title"], flush=True)
            previous = state

    cancelled = Event()
    signal.signal(signal.SIGINT, lambda *_: cancelled.set())
    try:
        result = run_agent(project_id="rv32i5_ooo", thinking=True, reasoning_effort="low",
            temperature=0.2, max_tokens=32000, max_calls=args.max_calls,
            max_repair_attempts=args.repair_attempts,
            optimization_rounds=args.optimize, resume_from=args.resume, on_event=event,
            l3_mode=args.mode,
            restart_stage=args.restart_stage,
            snapshot_attempt=args.resume_attempt,
            cancel_event=cancelled,
            plan_revision=json.loads(Path(args.plan).read_text()) if args.plan else None)
    finally:
        after = {str(p.relative_to(teacher)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(teacher.rglob("*")) if p.is_file()}
        (evidence / "teacher_hashes_after.json").write_text(json.dumps(after, indent=2))
        if active_run_id:
            record = AGENT_RUNS_ROOT / active_run_id / "events.json"
            if record.is_file():
                (evidence / (active_run_id + ".json")).write_bytes(record.read_bytes())
        assert hashes == after, "Teacher files were modified"
    (evidence / (result["run_id"] + ".json")).write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("RESULT", result["run_id"], result["status"], result.get("run_summary"), flush=True)


if __name__ == "__main__":
    main()

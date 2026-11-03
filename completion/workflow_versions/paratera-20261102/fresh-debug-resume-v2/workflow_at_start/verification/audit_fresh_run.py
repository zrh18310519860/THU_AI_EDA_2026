"""Audit actual fresh-run generation provenance, without reading any reference RTL."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ai_eda.config import AGENT_RUNS_ROOT, TASKS_ROOT
from ai_eda.agent.repair_context import digest
from ai_eda.rtl.runner import extract_rtl


def audit(run_id, evidence):
    if Path(run_id).name != run_id:
        raise ValueError("Expected a single run ID")
    root = AGENT_RUNS_ROOT / run_id
    record = json.loads((root / "events.json").read_text())
    if record["status"] not in {"passed", "failed", "cancelled"}:
        raise ValueError("Wait for the real run to finish before exporting")
    start = json.loads((root / "artifacts/exploration_provenance.json").read_text())
    assert start["generation_mode"] == "fresh" and not start["resume_from"]
    assert start["initial_project_files"] == [] and not start["builtin_rtl_allowed"]
    plan_path = root / "artifacts/plan.json"
    provenance_path = root / "artifacts/generation_provenance.json"
    plan = json.loads(plan_path.read_text()) if plan_path.exists() else {}
    provenance = json.loads(provenance_path.read_text()) if provenance_path.exists() else {"files": []}
    assert not provenance.get("builtin_rtl_used", False)
    events = {e["step"]: e for e in record["events"]}
    checks = []
    for module in provenance["files"]:
        assert module["origin"] == "model", module
        event = events[module["step"]]
        assert event["node_id"] == "coder" and event["source_path"] == module["path"]
        assert event["model_calls"], "No actual API request records"
        for revision in module["revisions"]:
            relative = revision["artifact"]
            source = (root / relative).read_text()
            call = root / f"step-{event['step']:02d}-coder/call-{revision['call_index']:03d}"
            raw = (call / "raw_response.txt").read_text()
            assert digest(source) == revision["sha256"] == digest(extract_rtl(raw)), relative
            requests = json.loads((call / "request_records.json").read_text())
            assert requests and any(r.get("usage") for r in requests), "Missing actual API usage"
        checks.append({"path": module["path"], "origin": "model", "step": module["step"],
                       "call_indices": module["call_indices"], "revisions_verified": len(module["revisions"]),
                       "sha256": module["sha256"]})
    if any(e["node_id"] == "tester" for e in record["events"]):
        assert {m["file"] for m in plan["modules"]} == {m["path"] for m in checks}
    teacher = TASKS_ROOT / "homework_rv32i5"
    current = [{"path": p.relative_to(TASKS_ROOT.parent.parent).as_posix(),
                "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
               for p in sorted(teacher.rglob("*")) if p.is_file()]
    integrity = evidence / "teacher_integrity_before.json"
    if integrity.exists():
        before = json.loads(integrity.read_text())
        assert {p["path"]: p["sha256"] for p in current} == {p["path"]: p["sha256"] for p in before}
        assert all(p["unchanged_vs_git_head"] for p in before)
    else:
        before = json.loads((evidence / "teacher_hashes_before.json").read_text())
        assert before == {str(p.relative_to(teacher)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(teacher.rglob("*")) if p.is_file()}
        for entry in current:
            original = subprocess.run(["git", "show", "HEAD:" + entry["path"]], cwd=TASKS_ROOT.parent.parent,
                                      capture_output=True, check=True).stdout
            assert hashlib.sha256(original).hexdigest() == entry["sha256"], entry["path"]
    summary = {"run_id": run_id, "generation_mode": "fresh", "status": record["status"],
               "error": record.get("error"), "planned_files": len(plan.get("modules", [])),
               "model_generated_files": len(checks), "module_checks": checks,
               "teacher_unchanged": True, "run_summary": record.get("run_summary"),
               "tester_attempts": (record.get("result") or {}).get("tester_attempts"),
               "meaning": "Provenance audit is separate from functional pass. Failed explorations remain failed."}
    evidence.mkdir(parents=True, exist_ok=True)
    (evidence / (run_id + "-audit.json")).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    destination = evidence / "runs" / run_id
    if not destination.exists():
        shutil.copytree(root, destination)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("--evidence", type=Path, default=Path("/mnt/f/AI_EDA/fresh-exploration-20261028"))
    args = parser.parse_args()
    audit(args.run_id, args.evidence)

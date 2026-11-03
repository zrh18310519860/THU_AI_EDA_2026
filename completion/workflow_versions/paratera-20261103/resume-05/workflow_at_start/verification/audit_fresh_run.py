"""Audit actual fresh-run generation provenance, without reading any reference RTL."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ai_eda.config import AGENT_RUNS_ROOT, TASKS_ROOT
from ai_eda.agent.repair_context import digest
from ai_eda.agent.patches import apply_repair_payload
from ai_eda.agent.subagents.common import extract_json_payload
from ai_eda.rtl.runner import extract_rtl


def manifest_sources(root, reference):
    entries = json.loads((root / reference).read_text())["files"]
    files = {}
    for entry in entries:
        path = (root / entry["artifact"]).resolve()
        assert root.resolve() in path.parents, "Source artifact escapes run"
        text = path.read_text()
        assert digest(text) == entry["sha256"], entry["path"]
        files[entry["path"]] = text
    return files


def audit_patches(root, record):
    """Replay accepted edits from their actual input snapshots, including rollbacks."""
    verified = []
    def signature(files):
        return digest(json.dumps({p: digest(t) for p, t in files.items()}, sort_keys=True))
    initial_ref = "artifacts/rtl_manifest.json"
    initial = manifest_sources(root, initial_ref) if (root / initial_ref).exists() else None
    known = {signature(initial)} if initial is not None else set()
    for event in record["events"]:
        if event.get("node_id") != "modifier" or event.get("status") != "info":
            continue
        attempt = event["attempt"]
        output = json.loads((root / f"handoffs/modifier_to_tester_attempt{attempt}.json").read_text())
        refs = output["payload"]["artifacts"]
        payload = json.loads((root / refs["patch"]).read_text())
        calls = sorted((root / f"step-{event['step']:02d}-modifier").glob("call-*"))
        assert calls, "Accepted patch lacks actual model calls"
        answer = extract_json_payload((calls[-1] / "raw_response.txt").read_text())
        assert answer == payload, "Saved patch differs from model answer"
        requests = json.loads((calls[-1] / "request_records.json").read_text())
        assert requests, "Accepted patch lacks request records"
        inputs = sorted((root / "handoffs").glob(f"diagnoser_to_modifier_attempt{attempt}_revision*.json"),
                        key=lambda p: int(p.stem.rsplit("revision", 1)[1]))
        eligible = [p for p in inputs if int(p.stem.rsplit("revision", 1)[1]) < event["step"]]
        assert eligible, "Accepted patch lacks diagnosis handoff"
        before_refs = json.loads(eligible[-1].read_text())["payload"]["artifacts"]
        before = manifest_sources(root, before_refs["rtl_manifest"])
        if initial is not None:
            assert signature(before) in known, "Patch input has no model-generated ancestor"
        brief = json.loads((root / before_refs["repair_brief"]).read_text())
        assert all(digest(before[p]) == h for p, h in brief["source_sha256"].items())
        application = apply_repair_payload(payload, {p: before[p] for p in brief["paths"]})
        after = manifest_sources(root, refs["rtl_manifest"])
        assert {**before, **application.changed_files} == after, "Snapshot differs from model patch replay"
        saved = json.loads((root / refs["patch_manifest"]).read_text())
        assert application.manifest == saved, "Patch hashes differ from replay"
        known.add(signature(after))
        tested = root / f"artifacts/tested_manifest_{attempt + 1}.json"
        if tested.exists():
            assert manifest_sources(root, str(tested.relative_to(root))) == after
        verified.append({"step": event["step"], "attempt": attempt,
                         "changed_paths": sorted(application.changed_files),
                         "fingerprint": saved["fingerprint"],
                         "model_response": str((calls[-1] / "raw_response.txt").relative_to(root))})
    return verified


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
        initial = manifest_sources(root, "artifacts/rtl_manifest.json")
        assert {p: digest(t) for p, t in initial.items()} == {m["path"]: m["sha256"] for m in checks}
        first_test = root / "artifacts/tested_manifest_1.json"
        if first_test.exists():
            assert manifest_sources(root, str(first_test.relative_to(root))) == initial
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
    patches = audit_patches(root, record)
    summary = {"run_id": run_id, "generation_mode": "fresh", "status": record["status"],
               "error": record.get("error"), "planned_files": len(plan.get("modules", [])),
               "model_generated_files": len(checks), "module_checks": checks,
               "accepted_patches_verified": len(patches), "patch_checks": patches,
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

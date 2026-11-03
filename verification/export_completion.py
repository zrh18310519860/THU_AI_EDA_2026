"""Export measured results only after source and independent acceptance gates pass."""

import argparse
import csv
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from ai_eda.config import AGENT_RUNS_ROOT, PROJECT_ROOT, TASKS_ROOT
from ai_eda.rtl.metrics import yosys_executable, _sta_executable
from ai_eda.token_metrics import summarize_usage


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_table(path, records):
    if not records:
        return
    columns = list(dict.fromkeys(key for record in records for key in record))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                         for key, value in record.items()} for record in records)


def export(selected, run_ids, destination, lineage_audit=None, evidence_roots=()):
    chosen = AGENT_RUNS_ROOT / selected
    result = read(chosen / "result.json")
    assert result["passed"] and result["generation_mode"] in {"fresh", "resumed"}
    provenance = read(lineage_audit) if lineage_audit else None
    if result["generation_mode"] == "resumed":
        assert provenance and provenance["selected_run"] == selected
        assert provenance["functional_passed"] and provenance["ppa_passed"]
        assert not provenance["manual_rtl_seed_used"] and provenance["empty_project_origin"]
        assert provenance["chain"][0]["mode"] == "fresh"
        assert all(item.get("seed_matches_parent", True) for item in provenance["chain"])
    capacity = read(chosen / "artifacts/capacity_review.json")
    assert capacity["selected_capacity_preserved"], "The selected candidate shrank checkpoint capacity"
    selection = read(chosen / "artifacts/candidate_selection.json")
    report = read(chosen / selection["selected_report"])
    assert report["passed"] and report["ppa_passed"]
    assert report["cpu_capability"]["verified_level"] == 3
    retests = sorted(chosen.glob("independent_retest/*/test_report.json"))
    assert retests, "Independent final-snapshot acceptance is required"
    retest = read(retests[-1])
    assert retest["passed"] and retest["ppa_passed"]
    assert retest["metrics"]["rtl_sha256"] == report["metrics"]["rtl_sha256"]
    for metric in ("area", "total_power_w"):
        assert abs(retest["metrics"][metric] - report["metrics"][metric]) < 1e-9
    attempt = int(Path(selection["selected_report"]).parent.name.split("-")[1])
    manifest = read(chosen / f"artifacts/tested_manifest_{attempt}.json")
    for entry in manifest["files"]:
        content = (chosen / entry["artifact"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        assert content == (chosen / "project" / entry["path"]).read_bytes()
    destination.mkdir(parents=True, exist_ok=True)
    versions_by_run = {}
    for evidence_root in evidence_roots:
        for directory in sorted(evidence_root.iterdir()):
            if not directory.is_dir() or not (directory / "workflow_at_start/sha256.json").exists():
                continue
            for run_path in directory.glob("agent-*.json"):
                if not (AGENT_RUNS_ROOT / run_path.stem / "events.json").exists():
                    continue
                relative = Path("workflow_versions") / evidence_root.name / directory.name
                target = destination / relative
                source_hashes = directory / "workflow_at_start/sha256.json"
                target_hashes = target / "workflow_at_start/sha256.json"
                if not target_hashes.exists() or source_hashes.read_bytes() != target_hashes.read_bytes():
                    shutil.copytree(directory / "workflow_at_start", target / "workflow_at_start", dirs_exist_ok=True)
                for saved in directory.glob("*.json"):
                    if saved.name.startswith("teacher_") or saved.name.endswith("-audit.json"):
                        shutil.copy2(saved, target / saved.name)
                versions_by_run[run_path.stem] = (relative / "workflow_at_start/sha256.json").as_posix()
    records, calls, candidates, contexts = [], [], [], []
    for run_id in dict.fromkeys([*run_ids, selected]):
        assert Path(run_id).name == run_id
        root = AGENT_RUNS_ROOT / run_id
        run = read(root / "events.json")
        assert run["status"] in {"passed", "failed", "cancelled"}, run_id
        flattened = []
        for event in run["events"]:
            for index, call in enumerate(event.get("model_calls", []), 1):
                flattened.append(call)
                calls.append({"run_id": run_id, "step": event["step"], "agent": event["node_id"],
                    "request_in_step": index, **{key: value for key, value in call.items() if key != "usage"},
                    **{key: (call.get("usage") or {}).get(key) for key in
                       ("prompt_tokens", "completion_tokens", "total_tokens")}})
            if event["node_id"] in {"diagnoser", "modifier"}:
                contexts.append({"run_id": run_id, "step": event["step"], "agent": event["node_id"],
                    "initial_prompt_chars": len(event.get("prompt", "")),
                    "source_reads": event.get("source_reads", []),
                    "scope_refreshes": event.get("scope_refreshes", []),
                    "diagnosis_validation_retries": event.get("diagnosis_validation_retries", []),
                    "actual_request_count": len(event.get("model_calls", []))})
        usage = summarize_usage(flattened)
        known = {key: sum((call.get("usage") or {}).get(key) or 0 for call in flattened)
                 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        capacity_path = root / "artifacts/capacity_review.json"
        capacity_review = read(capacity_path) if capacity_path.exists() else None
        stagnation_path = root / "artifacts/stagnation.json"
        stagnation = read(stagnation_path) if stagnation_path.exists() else None
        independent = []
        for path in sorted(root.glob("independent_retest/*/test_report.json")):
            checked = read(path)
            independent.append({"report": str(path.relative_to(root)),
                "functional_passed": checked.get("passed"), "ppa_passed": checked.get("ppa_passed"),
                "checks": checked.get("checks", []), "test_suites": checked.get("test_suites", []),
                "supplemental_suites": (checked.get("supplemental") or {}).get("test_suites", []),
                "metrics": checked.get("metrics")})
        records.append({"run_id": run_id, "status": run["status"], "capacity_review": capacity_review,
            "stagnation": stagnation,
            "independent_retests": independent,
            "workflow_version_ref": versions_by_run.get(run_id),
            "generation_mode": (run.get("model_parameters") or {}).get("generation_mode"),
            "resumed_from": run.get("resumed_from"), "duration_ms": run.get("duration_ms"),
            "error": run.get("error"), "usage": usage, "known_usage": known,
            "model_parameters": run.get("model_parameters"), "summary": run.get("run_summary")})
        selected_ref = read(root / "artifacts/candidate_selection.json").get("selected_report") \
            if (root / "artifacts/candidate_selection.json").exists() else None
        for path in sorted(root.glob("tester-*/test_report.json")):
            current = read(path)
            reviewed = next((item for item in (capacity_review or {}).get("candidates", [])
                             if item["report"] == str(path.relative_to(root))), None)
            candidates.append({"run_id": run_id, "tester": path.parent.name,
                "capacity_review": reviewed,
                "functional_passed": current.get("passed"), "ppa_passed": current.get("ppa_passed"),
                "selected_in_run": str(path.relative_to(root)) == selected_ref,
                "metrics": current.get("metrics"), "first_failures": current.get("checks", [])[:3]})
    destination.mkdir(parents=True, exist_ok=True)
    teacher_hashes = {}
    for path in sorted((TASKS_ROOT / "homework_rv32i5").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        original = subprocess.run(["git", "show", "HEAD:" + relative], cwd=PROJECT_ROOT,
                                  capture_output=True, check=True).stdout
        value = hashlib.sha256(path.read_bytes()).hexdigest()
        assert hashlib.sha256(original).hexdigest() == value, relative
        teacher_hashes[relative] = value
    versions = {}
    for name, command in {"python": [sys.executable, "--version"], "iverilog": ["iverilog", "-V"],
                          "yosys": [yosys_executable(), "-V"], "opensta": [_sta_executable(), "-version"]}.items():
        checked = subprocess.run(command, capture_output=True, text=True, timeout=15)
        versions[name] = {"command": command, "returncode": checked.returncode,
                          "output": (checked.stdout + checked.stderr)[:2000]}
    payload = {"selected_run": selected, "selected_generation_mode": result["generation_mode"],
        "source_provenance": provenance, "selected_tester": f"tester-{attempt:02d}",
        "records": records, "candidates": candidates, "report": report,
        "independent_report": str(retests[-1].relative_to(chosen)), "independent_retest": retest,
        "sources": manifest, "context_records": contexts, "selected_capacity_review": capacity,
        "teacher_unchanged_vs_git_head": True, "teacher_sha256": teacher_hashes,
        "tool_versions": versions,
        "known_tokens": sum(item["known_usage"]["total_tokens"] for item in records),
        "missing_usage_calls": sum(item["usage"]["missing_usage_calls"] for item in records),
        "note": "Known tokens exclude missing usage; exact total is unknown if any request lacks usage. "
                "Fresh, resumed and failed experiments remain separate. No manual RTL is a final source."}
    (destination / "completion.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_table(destination / "model-calls.csv", calls)
    write_table(destination / "runs.csv", records)
    write_table(destination / "candidates.csv", candidates)
    write_table(destination / "contexts.csv", contexts)
    write_table(destination / "benchmarks.csv", report.get("performance", {}).get("benchmarks", []))
    print(json.dumps({"selected_run": selected, "runs": len(records), "requests": len(calls),
                      "known_tokens": payload["known_tokens"], "missing_usage_calls": payload["missing_usage_calls"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("selected")
    parser.add_argument("--run", action="append", default=[])
    parser.add_argument("--all-model-runs", action="store_true")
    parser.add_argument("--lineage-audit", type=Path)
    parser.add_argument("--evidence-root", type=Path, action="append", default=[])
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    run_ids = list(arguments.run)
    if arguments.all_model_runs:
        selected_model = read(AGENT_RUNS_ROOT / arguments.selected / "events.json")["model_parameters"]["model"]
        for path in sorted(AGENT_RUNS_ROOT.glob("agent-*/events.json")):
            run = read(path)
            if (run.get("model_parameters") or {}).get("model") == selected_model:
                run_ids.append(path.parent.name)
    export(arguments.selected, run_ids, arguments.output, arguments.lineage_audit, arguments.evidence_root)

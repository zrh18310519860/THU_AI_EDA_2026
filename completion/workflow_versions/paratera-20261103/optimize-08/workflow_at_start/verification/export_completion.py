"""Export report data only after a fresh run and its independent acceptance pass."""

import argparse
import csv
import hashlib
import json
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


def export(selected, run_ids, destination):
    chosen = AGENT_RUNS_ROOT / selected
    result = read(chosen / "result.json")
    assert result["passed"] and result["generation_mode"] == "fresh", "A passed fresh run is required"
    selection = read(chosen / "artifacts/candidate_selection.json")
    report = read(chosen / selection["selected_report"])
    assert report["passed"] and report["ppa_passed"]
    assert report["cpu_capability"]["verified_level"] == 3
    retests = sorted(chosen.glob("independent_retest/*/test_report.json"))
    assert retests, "Independent final-snapshot acceptance is required"
    retest = read(retests[-1])
    assert retest["passed"] and retest["ppa_passed"]
    attempt = int(Path(selection["selected_report"]).parent.name.split("-")[1])
    manifest = read(chosen / f"artifacts/tested_manifest_{attempt}.json")
    for entry in manifest["files"]:
        content = (chosen / entry["artifact"]).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        assert content == (chosen / "project" / entry["path"]).read_bytes()
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
                    "actual_request_count": len(event.get("model_calls", []))})
        usage = summarize_usage(flattened)
        known = {key: sum((call.get("usage") or {}).get(key) or 0 for call in flattened)
                 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
        records.append({"run_id": run_id, "status": run["status"],
            "generation_mode": (run.get("model_parameters") or {}).get("generation_mode"),
            "resumed_from": run.get("resumed_from"), "duration_ms": run.get("duration_ms"),
            "error": run.get("error"), "usage": usage, "known_usage": known,
            "model_parameters": run.get("model_parameters"), "summary": run.get("run_summary")})
        selected_ref = read(root / "artifacts/candidate_selection.json").get("selected_report") \
            if (root / "artifacts/candidate_selection.json").exists() else None
        for path in sorted(root.glob("tester-*/test_report.json")):
            current = read(path)
            candidates.append({"run_id": run_id, "tester": path.parent.name,
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
    payload = {"selected_run": selected, "selected_tester": f"tester-{attempt:02d}",
        "records": records, "candidates": candidates, "report": report,
        "independent_report": str(retests[-1].relative_to(chosen)), "independent_retest": retest,
        "sources": manifest, "context_records": contexts,
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
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    export(arguments.selected, arguments.run, arguments.output)

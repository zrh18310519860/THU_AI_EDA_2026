"""Export real gateway experiments, preserving incomplete usage and provenance."""

import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from ai_eda.config import AGENT_RUNS_ROOT, PROJECT_ROOT, TASKS_ROOT


def export(destination):
    destination.mkdir(parents=True, exist_ok=True)
    records = []
    for root in sorted(AGENT_RUNS_ROOT.glob("agent-*")):
        path = root / "events.json"
        if not path.is_file():
            continue
        run = json.loads(path.read_text())
        parameters = run.get("model_parameters") or {}
        if parameters.get("model") != "DeepSeek-V4.1-Flash-1017":
            continue
        if run["status"] not in {"passed", "failed", "cancelled"}:
            raise ValueError("Wait for experiment completion: " + root.name)
        calls = [call for event in run.get("events", []) for call in event.get("model_calls", [])]
        complete = [call for call in calls if all(isinstance((call.get("usage") or {}).get(key), int)
                    for key in ("prompt_tokens", "completion_tokens", "total_tokens"))]
        summary = {"run_id": root.name, "status": run["status"],
                   "generation_mode": parameters.get("generation_mode"),
                   "resumed_from": run.get("resumed_from"), "error": run.get("error"),
                   "duration_ms": run.get("duration_ms"), "api_attempts": len(calls),
                   "usage_calls": len(complete), "missing_usage_calls": len(calls) - len(complete),
                   "known_usage": {key: sum(call["usage"][key] for call in complete)
                                   for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
                   "run_summary": run.get("run_summary"),
                   "tester_results": [{"step": event["step"], "stage": (event.get("report") or {}).get("stage"),
                       "functional_passed": (event.get("report") or {}).get("passed"),
                       "ppa_passed": (event.get("report") or {}).get("ppa_passed"),
                       "differential": {key: ((event.get("report") or {}).get("differential") or {}).get(key)
                                        for key in ("kind", "count", "last_pc")}}
                       for event in run.get("events", []) if event.get("node_id") == "tester"]}
        copied = destination / "runs" / root.name
        shutil.copytree(root, copied, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("*.vcd", "*.vvp", "__pycache__", "patch-check-*"))
        records.append(summary)
    teacher_checks = []
    for path in sorted((TASKS_ROOT / "homework_rv32i5").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        original = subprocess.run(["git", "show", "HEAD:" + relative], cwd=PROJECT_ROOT,
                                  capture_output=True, check=True).stdout
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        teacher_checks.append({"path": relative, "sha256": current_hash,
                               "unchanged_vs_git_head": hashlib.sha256(original).hexdigest() == current_hash})
    assert all(item["unchanged_vs_git_head"] for item in teacher_checks), "Teacher source changed"
    payload = {"model": "DeepSeek-V4.1-Flash-1017", "base_url": "https://llmapi.paratera.com/v1",
               "runs": records, "api_attempts": sum(item["api_attempts"] for item in records),
               "missing_usage_calls": sum(item["missing_usage_calls"] for item in records),
               "known_usage": {key: sum(item["known_usage"][key] for item in records)
                               for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
               "teacher_unchanged": True, "teacher_files": teacher_checks,
               "note": "Known usage excludes requests without complete API usage. Missing values are not zero. Connectivity probes are separate."}
    (destination / "audit.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = ["# Paratera L3 Experiments", "", "Only real API calls and real test results are included.", "",
            "| Run | Mode | Status | Requests | Known tokens | Missing usage |",
            "| --- | --- | --- | ---: | ---: | ---: |"]
    rows.extend(f"| {r['run_id']} | {r['generation_mode']} | {r['status']} | {r['api_attempts']} | {r['known_usage']['total_tokens']} | {r['missing_usage_calls']} |" for r in records)
    rows.extend(["", "Teacher files match Git HEAD. Resume runs retain their source run identity.",
                 "Missing usage prevents an exact total. Failed explorations remain failed.",
                 "Model parameters, elapsed time, API usage, conversations, patches and immutable source manifests are in runs/."])
    (destination / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key not in ("runs", "teacher_files")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=Path("/mnt/f/AI_EDA/paratera-20261102"))
    export(parser.parse_args().evidence)

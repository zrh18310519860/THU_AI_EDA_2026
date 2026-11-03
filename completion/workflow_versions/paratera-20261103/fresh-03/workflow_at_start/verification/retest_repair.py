"""Audit a human-assisted candidate without rewriting its original agent run."""

import argparse
import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_eda.agent.subagents.common import combine_files
from ai_eda.rtl.runner import save_report, test_rtl

ROOT = Path(__file__).resolve().parents[1]


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(directory.rglob("*")) if p.is_file()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--ppa", action="store_true")
    args = parser.parse_args()
    candidate = args.candidate.resolve()
    sources = {"rtl/" + p.name: p.read_text() for p in sorted((candidate / "rtl").glob("*.sv"))}
    if not sources:
        raise ValueError("Candidate has no RTL")
    output = candidate / ("verified-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f"))
    output.mkdir()
    for name, text in sources.items():
        target = output / name
        target.parent.mkdir(exist_ok=True)
        target.write_text(text)
    combined = output / "candidate.sv"
    combined.write_text(combine_files(sources))
    teacher = ROOT / "content/tasks/homework_rv32i5"
    before = hashes(teacher)
    started = time.perf_counter()
    report = test_rtl(task_name="human_assisted_L3_repair", testbench_path=teacher / "tb.sv",
        testbench_top="tb_agent_rv32i5", rtl_path=combined, rtl_top_module="rv32i5",
        expected_cpu_level=3, timeout_seconds=90,
        extra_testbench_path=ROOT / "verification/l3_stress_tb.sv", extra_testbench_top="tb_l3_stress",
        run_dir=output if args.ppa else None,
        power_testbench_path=teacher / "power_tb.sv" if args.ppa else None,
        power_testbench_top="tb_power_agent_rv32i5" if args.ppa else None)
    report["provenance"] = {"kind": "human_assisted_repair", "parent_candidate": str(candidate),
        "autonomous_model_success": False, "model_calls": 0, "token_usage": 0,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "source_sha256": {name: hashlib.sha256(text.encode()).hexdigest() for name, text in sources.items()},
        "teacher_hashes": before, "teacher_unchanged": before == hashes(teacher)}
    save_report(output, report)
    summary = {key: report.get(key) for key in ("passed", "ppa_passed", "cpu_capability", "metrics", "timings_ms", "provenance")}
    summary["report"] = str(output / "test_report.json")
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    if not report["passed"] or (args.ppa and not report.get("ppa_passed")):
        raise SystemExit(1)


if __name__ == "__main__":
    main()

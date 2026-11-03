"""Verify exported final AES and L3 RTL without an API key or model calls."""

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ai_eda.agent.subagents.common import combine_files
from ai_eda.rtl.runner import save_report, test_rtl


ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ppa", action="store_true")
    args = parser.parse_args()
    completion = read(ROOT / "completion/completion.json")
    for name, expected in completion["teacher_sha256"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, name
    output = ROOT / "submission/retest" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    output.mkdir(parents=True)
    l3 = ROOT / "submission/l3"
    sources = {}
    for entry in read(l3 / "source_manifest.json")["files"]:
        relative = Path(entry["path"])
        assert relative.parts[0] == "rtl" and ".." not in relative.parts
        content = (l3 / relative).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"], str(relative)
        sources[entry["path"]] = content.decode("utf-8")
    combined = combine_files(sources)
    assert hashlib.sha256(combined.encode()).hexdigest() == completion["report"]["metrics"]["rtl_sha256"]
    l3_output = output / "l3"
    l3_output.mkdir()
    rtl = l3_output / "verified_snapshot.sv"
    rtl.write_text(combined, encoding="utf-8")
    teacher = ROOT / "content/tasks/homework_rv32i5"
    report = test_rtl(task_name="submission_L3", testbench_path=teacher / "tb.sv",
        testbench_top="tb_agent_rv32i5", rtl_path=rtl, expected_cpu_level=3,
        rtl_top_module="rv32i5", timeout_seconds=90,
        extra_testbench_path=ROOT / "verification/l3_stress_tb.sv", extra_testbench_top="tb_l3_stress",
        run_dir=l3_output if args.ppa else None,
        power_testbench_path=teacher / "power_tb.sv" if args.ppa else None,
        power_testbench_top="tb_power_agent_rv32i5" if args.ppa else None)
    save_report(l3_output, report)
    assert report["passed"] and (not args.ppa or report.get("ppa_passed")), "L3 failed; inspect " + str(l3_output)
    aes = ROOT / "submission/aes"
    metadata = read(aes / "metadata.json")
    assert hashlib.sha256((aes / "generated.sv").read_bytes()).hexdigest() == metadata["rtl_sha256"]
    aes_output = output / "aes"
    aes_output.mkdir()
    teacher = ROOT / "content/tasks/homework_aes128"
    aes_report = test_rtl(task_name="submission_AES", testbench_path=teacher / "tb.sv",
        testbench_top="tb_homework_aes128", rtl_path=aes / "generated.sv", rtl_top_module="aes128",
        timeout_seconds=30, run_dir=aes_output if args.ppa else None,
        power_testbench_path=teacher / "power_tb.sv" if args.ppa else None,
        power_testbench_top="tb_power_homework_aes128" if args.ppa else None,
        extra_testbench_path=aes / "oracle/tb.sv", extra_testbench_top="tb_aes_oracle")
    save_report(aes_output, aes_report)
    assert aes_report["passed"] and (not args.ppa or aes_report.get("ppa_passed")), "AES failed; inspect " + str(aes_output)
    print(json.dumps({"l3_passed": report["passed"], "aes_passed": aes_report["passed"],
                      "ppa_requested": args.ppa, "output": str(output)}, indent=2))


if __name__ == "__main__":
    main()

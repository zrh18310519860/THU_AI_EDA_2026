"""Tester 子 Agent：编译项目、运行教师 TB、评估面积 / 功耗。"""

from __future__ import annotations

from pathlib import Path

from ai_eda.agent.projects import get_project
from ai_eda.config import PROJECT_ROOT
from ai_eda.rtl.runner import save_report, test_rtl
from ai_eda.agent.repair_context import read_sources, source_manifest, map_compile_diagnostics
from ai_eda.agent.skills.cpu_diagnostics import build_cpu_repair_packet
from ai_eda.agent.skills.cpu_protocol import audit_external_bus_protocol, audit_rob_tag_flow, audit_icache_refill_response, audit_cpu_structure

from .base import SubAgent
from .common import combine_files, report_feedback, report_summary



class TesterAgent(SubAgent):
    name = "tester"
    title = "04 · Tester"
    node_type = "tester"

    def run(self, input_handoff: Path, attempt: int) -> Path:
        incoming = self.runtime.read_handoff(input_handoff)["payload"]
        artifacts = incoming.get("artifacts") or {}
        spec_ref = artifacts.get("spec")
        plan_ref = artifacts.get("plan")
        manifest_ref = artifacts.get("rtl_manifest")
        if not all(isinstance(ref, str) for ref in (spec_ref, plan_ref, manifest_ref)):
            raise ValueError("Tester 输入缺少 Spec、Plan 或 RTL manifest artifact")
        plan = self.runtime.read_json_artifact(plan_ref)
        manifest = self.runtime.read_json_artifact(manifest_ref)
        if not isinstance(plan, dict):
            raise ValueError("Plan artifact 格式无效")
        entries = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError("RTL manifest 格式无效")
        files = read_sources(self.runtime, manifest_ref)
        manifest_ref = source_manifest(self.runtime, files, f"tested_manifest_{attempt}.json")
        combined_ref = self.runtime.write_text_artifact(
            f"generated_attempt{attempt}.sv", combine_files(files)
        )
        combined_path = self.runtime.run_dir / combined_ref
        tester_dir = self.runtime.run_dir / f"tester-{attempt:02d}"
        tester_dir.mkdir(exist_ok=True)
        prompt = (
            f"Round {attempt}: compile {len(files)} project files and run the fixed teacher TB; "
            "generate power activity and synthesized area as well."
        )
        event = self.runtime.begin_event(
            "tester", self.node_type, f"{self.title} · Round {attempt}", prompt, attempt
        )
        project = self.runtime.options.get("project") or get_project()
        preflight = None
        if project.get("expected_cpu_level") == 3:
            preflight = test_rtl(task_name="l3_smoke", testbench_path=PROJECT_ROOT / "verification/l3_smoke_tb.sv",
                testbench_top="tb_l3_smoke", rtl_path=combined_path, timeout_seconds=8,
                additional_include_paths=(project["testbench_path"].parent,))
            self.runtime.write_json_artifact(f"smoke_{attempt}.json", preflight)
        report = preflight if preflight and not preflight.get("passed") else test_rtl(
            task_name=project["task_name"],
            testbench_path=project["testbench_path"],
            testbench_top=project["testbench_top"],
            rtl_path=combined_path,
            power_testbench_path=project["power_testbench_path"],
            power_testbench_top=project["power_testbench_top"],
            expected_cpu_level=project.get("expected_cpu_level"),
            rtl_top_module=plan.get("top_module"),
            timeout_seconds=int(project.get("timeout_seconds", 20)),
            run_dir=tester_dir,
            extra_testbench_path=PROJECT_ROOT / "verification/l3_stress_tb.sv" if project.get("expected_cpu_level") == 3 else None,
            extra_testbench_top="tb_l3_stress" if project.get("expected_cpu_level") == 3 else None,
        )
        if preflight is not None:
            report["preflight_passed"] = bool(preflight.get("passed"))
            report["test_context"] = {
                "primary_task": report.get("task_name"),
                "smoke_task": "l3_smoke",
                "smoke_source": (PROJECT_ROOT / "verification/l3_smoke_tb.sv").read_text(encoding="utf-8"),
                "provenance": "checks and trace belong to primary_task. differential is an independent DFT program, not the smoke program; its PCs, history and startup_signals must be interpreted separately.",
                "source_policy": "This is read-only test stimulus. Repair candidate RTL only; do not change any test or expected value.",
            }
            if report.get("supplemental") and not report["supplemental"].get("passed"):
                report["test_context"].update(
                    supplemental_task=report["supplemental"].get("task_name"),
                    supplemental_source=(PROJECT_ROOT / "verification/l3_stress_tb.sv").read_text(encoding="utf-8"),
                    provenance="Teacher checks/trace come from primary_task. Supplemental checks/trace come from supplemental_task and its supplied source. differential is a separate DFT probe. Never merge these programs into one timeline.")
            if not preflight.get("passed"):
                report["teacher_passed"] = False
                report["teacher_not_run"] = "Short startup/ALU/load/store/ECALL smoke test failed."
                report["metrics"] = {"status": "skipped", "reason": "Preflight functional gate failed."}
                report["ppa_passed"] = False
        if project.get("expected_cpu_level") == 3 and not report.get("teacher_passed", report.get("passed")) and report.get("stage") == "simulation":
            if report.get("preflight_passed"):
                from verification.teacher_probe import run_failure_probes
                report["failure_probes"] = run_failure_probes([combined_path], report.get("checks", []),
                                                            tester_dir / "failure_probes")
            # Independent retirement comparison narrows hangs before another speculative patch.
            from verification.diff_cpu import run_differential
            try:
                report["differential"] = run_differential([combined_path], tester_dir / "differential")
            except Exception as error:
                report["differential"] = {"available": False, "reason": str(error)[-1200:]}
        if project.get("expected_cpu_level") == 3:
            report["protocol_audit"] = audit_external_bus_protocol(files, report)
            report["tag_audit"] = audit_rob_tag_flow(files, report)
            report["cache_audit"] = audit_icache_refill_response(files, report)
            report["structural_audit"] = audit_cpu_structure(files)
        report["compile_diagnostics"] = map_compile_diagnostics(report, files, combined_path.read_text(encoding="utf-8"))
        self.check_cancel()
        report_path = save_report(tester_dir, report)
        self.save_context(
            attempts=int(self.context.get("attempts", 0)) + 1,
            plan_ref=plan_ref,
            rtl_manifest_ref=manifest_ref,
        )
        report_ref = self.runtime.artifact_ref(report_path)
        self.finish(
            event,
            "passed" if report.get("passed") and report.get("ppa_passed") else "failed",
            response=report_summary(report),
            report=report,
            feedback=report_feedback(report),
        )
        packet_ref = self.runtime.write_json_artifact(f"tester_packet_{attempt}.json", build_cpu_repair_packet(report))
        target = "diagnoser"
        label = "Tester → Diagnoser: compact diagnostics and source hashes"
        return self.runtime.write_handoff(
            "tester",
            target,
            {
                "attempt": attempt,
                "summary": report_summary(report),
                "artifacts": {
                    "spec": spec_ref,
                    "plan": plan_ref,
                    "rtl_manifest": manifest_ref,
                    "report": report_ref,
                    "combined_rtl": combined_ref,
                    "repair_packet": packet_ref,
                },
            },
            attempt=attempt,
            label=label,
        )

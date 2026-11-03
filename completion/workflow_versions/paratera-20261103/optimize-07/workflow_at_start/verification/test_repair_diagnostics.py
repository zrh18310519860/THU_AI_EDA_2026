"""Regressions for evidence that previously caused repeated wrong RTL patches."""

import unittest
import json
import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from ai_eda.agent.skills.cpu_protocol import audit_cpu_structure, audit_icache_refill_response
from ai_eda.agent.skills.cpu_diagnostics import build_cpu_repair_packet, named_probe_view
from ai_eda.agent.repair_context import select_paths, map_compile_diagnostics, reserved_declarations
from ai_eda.agent.subagents.common import combine_files
from verification.diff_cpu import check
from ai_eda.agent.subagents.diagnoser import request_diagnosis, validate_diagnosis_evidence
from ai_eda.agent.subagents.modifier import validate_patch_compilation, validate_checkpoint_capacity
from verification.teacher_probe import teacher_tasks, stimulus_source, compare_retired_results, wave_events, load_wave, operand_lifetime, check_program_control


class RepairDiagnosticsTest(unittest.TestCase):
    def test_optimization_cannot_halve_checkpoint_slots(self):
        old = "logic [5:0] ckpt_map [0:7][0:31];"
        with self.assertRaisesRegex(ValueError, "8 -> 4"):
            validate_checkpoint_capacity(old, old.replace("[0:7]", "[0:3]"))
        validate_checkpoint_capacity(old, old.replace("[0:31]", "[1:31]"))

    def test_teacher_control_comparison_uses_its_memory_without_claiming_value_samples(self):
        output = ("PROBE_RESET|\nPROBE_MEM|00000100|00\n"
                  "PROBE_RET|00000000|10000093|00000000|10000093|0000000000000001\n"
                  "PROBE_RET|00000004|0000c103|00000000|0000c103|0000000000000002\n"
                  "PROBE_RET|00000008|00011463|00000000|00011463|0000000000000003\n"
                  "PROBE_RET|00000010|00000073|00000000|00000073|0000000000000004\n")
        result = check_program_control(output)
        self.assertEqual(result["kind"], "retire_pc")
        self.assertEqual(result["expected_pc"], "0xc")
        self.assertEqual(result["actual_pc"], "0x10")
        self.assertEqual(result["previous_instruction"]["vcd_time"], 3000)
        self.assertIsNone(result["history"][0]["got"])
        corrected = output.replace("PROBE_RET|00000010|", "PROBE_RET|0000000c|")
        self.assertTrue(check_program_control(corrected)["passed"])

    @unittest.skipUnless(shutil.which("iverilog"), "Icarus is required for lexical validation")
    def test_function_port_style_is_legal_but_reserved_function_name_is_not(self):
        import subprocess
        source = "module top; function automatic [3:0] %s(input [2:0] a, input [2:0] b); reg [3:0] d; begin d=b-a; %s=d; end endfunction endmodule"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "fixture.sv"
            for name, legal in (("distance", True), ("dist", False)):
                text = source % (name, name)
                path.write_text(text)
                result = subprocess.run(["iverilog", "-g2012", "-tnull", str(path)], capture_output=True)
                self.assertEqual(result.returncode == 0, legal)
                findings = reserved_declarations(text)
                self.assertEqual(bool(findings), not legal)
                if findings:
                    self.assertEqual(findings[0]["identifier"], "dist")

    def test_operand_lifetime_tracks_reuse_and_recovery_before_exact_dispatch(self):
        names = {"dispatch": ["dispatch_fire", "dec_dispatch_pc", "dec_dispatch_insn", "rn_alloc_ps1", "rn_alloc_prd"],
                 "writeback": ["ex_wb_valid", "ex_wb_prd", "ex_wb_data", "lsu_wb_valid", "lsu_wb_prd", "lsu_wb_data"],
                 "recovery": ["rob_redirect_valid", "rob_redirect_pc"]}
        rows = [[1, "dispatch", "0x1", "0x0", "0x13", "0x0", "0x24"],
                [2, "writeback", "0x1", "0x24", "0x1", "0x0", "0x24", "0xdead"],
                [3, "recovery", "0x1", "0x68"],
                [4, "dispatch", "0x1", "0x68", "0x118193", "0x24", "0x11"],
                [5, "dispatch", "0x0", "0x68", "0x118193", "0x25", "0x12"],
                [7, "writeback", "0x1", "0x24", "0x9", "0x0", "0x0", "0x0"]]
        result = operand_lifetime(rows, names, {"pc": "0x68", "insn": "0x118193", "vcd_time": 6})
        self.assertEqual(result["target_dispatch_time"], 4)
        self.assertEqual(result["physical_source_ids"], ["0x24"])
        self.assertEqual([e["time"] for e in result["events"]], [1, 2, 3, 4])
        self.assertNotIn("lsu_wb_data", result["events"][1]["signals"])

    def test_named_probe_deltas_preserve_repeated_semantic_events_and_raw_source(self):
        probe = {"observations": {"columns": {
            "state:rob": ["time", "group", "rob.head", "rob.valid"],
            "retire": ["time", "group", "retire_valid", "retire_pc"]},
            "events": [[1, "state:rob", "0x1", "0x1"], [2, "state:rob", "0x2", "0x1"],
                       [3, "retire", "0x1", "0x4"], [4, "retire", "0x1", "0x4"]]}}
        view = named_probe_view(probe)
        self.assertEqual(view["observations"]["events"][1]["signals"], {"rob.head": "0x2"})
        self.assertEqual(len(view["observations"]["events"]), 4)
        self.assertEqual(view["observations"]["events"][-1]["signals"]["retire_pc"], "0x4")
        self.assertIn("columns", probe["observations"])
        probe["observations"]["events"].append([5, "retire", "0x1"])
        with self.assertRaises(ValueError):
            named_probe_view(probe)

    def test_probe_keeps_complete_records_when_timeout_cuts_last_vcd_line(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "cut.vcd"
            path.write_text('$scope module tb $end\n$var wire 4 ! data $end\n'
                '$upscope $end\n$enddefinitions $end\n#0\nb0011 !\n#10\nb1010 ')
            wave, ignored = load_wave(path)
            self.assertTrue(ignored)
            self.assertEqual(wave['tb.data'].tv, [(0, '0011')])
            self.assertTrue(path.read_text().endswith('b1010 '))

    def test_probe_retirement_gap_uses_public_valid_and_respects_control_and_reset(self):
        signals = {"clk": [(t, "1" if t % 10 == 5 else "0") for t in range(0, 101, 5)],
                   "rst_n": [(0, "1"), (70, "0"), (80, "1")],
                   "dut.dbg_retire_valid": [(0, "0"), (10, "1"), (60, "0"), (80, "1")],
                   "dut.dbg_retire_pc[31:0]": [(0, "0"), (20, "100"), (30, "1000"),
                       (40, "100000"), (50, "101000"), (80, "0"), (90, "100")],
                   "dut.dbg_retire_insn[31:0]": [(0, "10011"), (30, "1100011"), (40, "10011")],
                   "dut.commit_valid": [(0, "1")]}
        fake = Mock()
        fake.references_to_ids = {"tb_teacher_probe." + name: name for name in signals}
        fake.__getitem__ = Mock(side_effect=lambda ref: Mock(tv=signals[ref.removeprefix("tb_teacher_probe.")]))
        with patch("vcdvcd.VCDVCD", return_value=fake):
            result = wave_events("unused.vcd")
        self.assertEqual(len(result["retirement_flow"]), 1)
        anomaly = result["retirement_flow"][0]
        self.assertEqual(anomaly["previous"]["pc"], "0x20")
        self.assertEqual(anomaly["expected_next_pc"], "0x24")
        self.assertEqual(anomaly["current"]["pc"], "0x28")

    def test_stagnation_distinguishes_real_probe_progress_but_ignores_waveform_time(self):
        report = {"passed": False, "stage": "simulation", "checks": [{"name": "smoke_wait_0", "detail": "timeout"}],
                  "failure_probes": [{"testcase": "l3_smoke", "observations": {
                      "events": [[100, "retire", "0xc"], [120, "state:rob", "0x3", "0x0"]]}}]}
        first = build_cpu_repair_packet(report)["failure_fingerprint"]
        report["failure_probes"][0]["observations"]["events"] = [
            [200, "retire", "0xc"], [220, "state:rob", "0x3", "0x0"]]
        self.assertEqual(first, build_cpu_repair_packet(report)["failure_fingerprint"])
        report["failure_probes"][0]["observations"]["events"][-1][-2:] = ["0x4", "0x0"]
        self.assertNotEqual(first, build_cpu_repair_packet(report)["failure_fingerprint"])

    def test_probe_keeps_later_reset_and_nested_state_without_stall_flood(self):
        signals = {
            "clk": [(t, "1" if t % 10 == 5 else "0") for t in range(0, 5001, 5)],
            "rst_n": [(0, "0"), (10, "1"), (2000, "0"), (2020, "1")],
            "memory_wait[31:0]": [(0, "0"), (2000, "11")],
            "dut.disp_valid": [(0, "0"), (30, "1")],
            "dut.disp_ready": [(0, "1"), (40, "0")],
            "dut.dbg_retire_valid": [(0, "0"), (2050, "1"), (2070, "0")],
            "dut.unit_any_name.state[2:0]": [(0, "0"), (2040, "1"), (2080, "0")],
            "dut.unit_any_name.resp_consume": [(0, "0"), (2060, "1"), (2070, "0")],
        }
        fake = Mock()
        fake.references_to_ids = {"tb_teacher_probe." + name: name for name in signals}
        fake.__getitem__ = Mock(side_effect=lambda ref: Mock(tv=signals[ref.removeprefix("tb_teacher_probe.")]))
        with patch("vcdvcd.VCDVCD", return_value=fake):
            result = wave_events("unused.vcd")
        self.assertEqual(len(result["segments"]), 2)
        self.assertEqual(result["segments"][1]["memory_wait"], "0x3")
        self.assertEqual(result["omitted_events"], 0)
        self.assertLess(len(result["events"]), 20)
        nested = [row for row in result["events"] if row[1] == "state:unit_any_name"]
        self.assertEqual(nested[-1][-1], "0x0")
        self.assertEqual(sum(row[1] == "retire" for row in result["events"]), 2)

    def test_probe_distinguishes_retired_result_from_later_state_corruption(self):
        observations = {"columns": {"retire": ["time", "group", "rob_retire_insn", "rob_retire_pc", "rob_retire_data", "rob_retire_pd"]},
                        "events": [[10, "retire", "0x0030c403", "0x1c", "0x80", "0x27"]]}
        findings = compare_retired_results(observations, ["CHECK: t06 x8 expected 0x00000080 got 0x00000000"])
        self.assertEqual(findings[0]["register"], "x8")
        self.assertEqual(findings[0]["retired_data"], "0x80")
        self.assertEqual(compare_retired_results(observations, ["CHECK: t06 x8 expected 0x00000081 got 0x00000000"]), [])

    def test_probe_accepts_generated_prefixes_and_keeps_operand_and_completion_evidence(self):
        signals = {"clk": [(0, "0"), (5, "1"), (10, "0"), (15, "1")],
                   "rst_n": [(0, "1")], "dut.rn_dispatch_fire": [(0, "1")],
                   "dut.dec_dispatch_pc[31:0]": [(0, "100")],
                   "dut.ex_wb_valid": [(0, "1")], "dut.ex_wb_prd[5:0]": [(0, "100000")],
                   "dut.ex_wb_data[31:0]": [(0, "111")],
                   "dut.lsu_req_valid": [(0, "1")], "dut.lsu_req_addr[31:0]": [(0, "1000000000")],
                   "dut.lsu_prf_rs1_data[31:0]": [(0, "1000000000")],
                   "dut.u_lsu.sel_issued": [(0, "1")], "dut.u_lsu.store_done": [(0, "1")]}
        fake = Mock()
        fake.references_to_ids = {"tb_teacher_probe." + name: name for name in signals}
        fake.__getitem__ = Mock(side_effect=lambda ref: Mock(tv=signals[ref.removeprefix("tb_teacher_probe.")]))
        with patch("vcdvcd.VCDVCD", return_value=fake):
            result = wave_events("unused.vcd")
        self.assertIn("rn_dispatch_fire", result["columns"]["dispatch"])
        self.assertIn("ex_wb_prd", result["columns"]["writeback"])
        self.assertIn("lsu_req_addr", result["columns"]["memory"])
        self.assertIn("lsu_prf_rs1_data", result["columns"]["memory"])
        self.assertIn("u_lsu.sel_issued", result["columns"]["state:u_lsu"])
        self.assertEqual(sum(row[1] == "writeback" for row in result["events"]), 2)

    def test_teacher_probe_uses_original_task_and_its_program_builder(self):
        tasks = teacher_tasks()
        source = stimulus_source("test_t06_load_store", tasks)
        self.assertIn("task automatic build_t06_load_store()", source)
        self.assertIn("check_reg(8,  32'h0000_0080)", source)
        self.assertLess(len(source), 16001)
        packet = build_cpu_repair_packet({"failure_probes": [{"testcase": "t06_load_store"}]})
        self.assertEqual(packet["failure_probes"][0]["testcase"], "t06_load_store")

    def test_primary_stimulus_and_independent_probe_are_explicit(self):
        context = {"primary_task": "l3_smoke", "smoke_source": "e_addi(1,0,512); e_ecall();",
                   "provenance": "differential runs a separate DFT program"}
        packet = build_cpu_repair_packet({"test_context": context,
                                         "differential": {"kind": "retire_pc", "count": 63}})
        self.assertEqual(packet["test_context"], context)
        self.assertEqual(packet["differential"]["count"], 63)

    @unittest.skipUnless(shutil.which("iverilog"), "Icarus is required for compilation validation")
    def test_compile_repair_cannot_write_a_still_invalid_edited_module(self):
        with tempfile.TemporaryDirectory() as temp:
            files = {"rtl/top.sv": "module top; endmodule", "rtl/bad.sv": "module bad; function logic f(input logic ref); f=ref; endfunction endmodule"}
            with self.assertRaisesRegex(ValueError, "Edited module still fails"):
                validate_patch_compilation(files, "top", Path(temp), changed_paths=["rtl/bad.sv"])
            files["rtl/other.sv"] = files.pop("rtl/bad.sv")
            files["rtl/leaf.sv"] = "module leaf; unknown_child u(); endmodule"
            validate_patch_compilation(files, "top", Path(temp), changed_paths=["rtl/leaf.sv"])

    def test_compiler_locations_route_to_original_source_before_functional_audits(self):
        files = {"rtl/a.sv": "module a;\nendmodule\n", "rtl/b.sv": "module b;\nBAD declaration;\nendmodule"}
        combined = combine_files(files)
        number = combined.splitlines().index("BAD declaration;") + 1
        report = {"stage": "compile", "rtl_path": "/tmp/generated_attempt1.sv",
                  "stderr": f"/tmp/generated_attempt1.sv:{number}: syntax error\n/teacher/tb.sv:12: warning: ignored"}
        report["compile_diagnostics"] = map_compile_diagnostics(report, files, combined)
        self.assertEqual(report["compile_diagnostics"][0]["path"], "rtl/b.sv")
        self.assertEqual(report["compile_diagnostics"][0]["line"], 2)
        self.assertIn("2: BAD declaration;", report["compile_diagnostics"][0]["source_excerpt"])
        packet = build_cpu_repair_packet(report)
        self.assertEqual(select_paths(packet, files, {}), ["rtl/b.sv"])

    @unittest.skipUnless(shutil.which("iverilog"), "Icarus is required for elaboration validation")
    def test_patch_gate_rejects_misconnected_port_before_project_write(self):
        with tempfile.TemporaryDirectory() as temp:
            files = {"leaf": "module leaf(input a); endmodule", "top": "module top; leaf u(.wrong(1'b0)); endmodule"}
            with self.assertRaisesRegex(ValueError, "elaboration failure"):
                validate_patch_compilation(files, "top", Path(temp))
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_no_dispatch_with_blocked_rob_routes_backend_before_cache(self):
        report = {"differential": {"kind": "no_ecall", "count": 0, "startup_signals": [
            {"signal": "imem_valid", "ever_one": True},
            {"signal": "rob_alloc_ready", "ever_one": False, "known_values": ["0"]}]}}
        packet = build_cpu_repair_packet(report)
        self.assertEqual(packet["causal_hints"]["earliest_observable_stage"], "allocation_or_dispatch_blocked")
        files = {"rtl/" + name + ".sv": "" for name in
                 ("rv32i5", "reorder_buffer", "rename_unit", "issue_queue", "icache", "dcache")}
        selected = select_paths(packet, files, {})
        self.assertIn("rtl/reorder_buffer.sv", selected)
        self.assertNotIn("rtl/icache.sv", selected)

    def test_rename_backpressure_after_retirement_does_not_route_to_cache(self):
        report = {"differential": {"kind": "no_ecall", "count": 8, "startup_signals": [
            {"signal": "imem_valid", "ever_one": True},
            {"signal": "ren_disp_ready", "last": "0", "ever_one": True, "known_values": ["1", "0"]}]}}
        packet = build_cpu_repair_packet(report)
        files = {"rtl/" + name + ".sv": "" for name in
                 ("rv32i5", "reorder_buffer", "rename_unit", "issue_queue", "icache", "dcache")}
        selected = select_paths(packet, files, {})
        self.assertIn("rtl/rename_unit.sv", selected)
        self.assertIn("rtl/reorder_buffer.sv", selected)
        self.assertNotIn("rtl/icache.sv", selected)

    def test_diagnoser_can_read_missing_current_module_without_adding_old_logs(self):
        files = {"rtl/top.sv": "module top; endmodule", "rtl/rename_unit.sv": "assign read_data = 32'd0;"}
        view = {"editable_sources": {"rtl/top.sv": files["rtl/top.sv"]}, "diagnostics": {"count": 0}}
        valid = {"paths": ["rtl/rename_unit.sv"], "root_cause": "Read port is tied to zero",
                 "evidence": [{"path": "rtl/rename_unit.sv", "anchor": files["rtl/rename_unit.sv"]}]}
        runtime = Mock()
        runtime.call_model_messages.side_effect = [
            {"answer": json.dumps({"read_paths": ["rtl/rename_unit.sv"], "reason": "Inspect PRF"})},
            {"answer": json.dumps(valid)}]
        event = {}
        self.assertEqual(request_diagnosis(runtime, event, json.dumps(view), "diagnose", files), valid)
        next_messages = runtime.call_model_messages.call_args.args[1]
        self.assertEqual(len(next_messages), 2)
        current_view = json.loads(next_messages[1]["content"])
        self.assertEqual(current_view["editable_sources"], files)
        self.assertEqual(current_view["source_read_budget_remaining"], 4)
        self.assertEqual(event["source_reads"][0]["paths"], ["rtl/rename_unit.sv"])

    def test_truncated_diagnosis_is_retried_with_bounded_history(self):
        files = {"rtl/icache.sv": "logic [2047:0] line_data;"}
        valid = {"paths": list(files), "root_cause": "Current tag storage is undersized",
                 "evidence": [{"path": "rtl/icache.sv", "anchor": files["rtl/icache.sv"]}]}
        runtime = Mock()
        runtime.call_model_messages.side_effect = [
            {"answer": '{"evidence":[' + ' ' * 12000, "finish_reason": "length"},
            {"answer": json.dumps(valid), "finish_reason": "stop"}]
        self.assertEqual(request_diagnosis(runtime, {}, "current snapshot", "diagnose", files), valid)
        retry_messages = runtime.call_model_messages.call_args.args[1]
        self.assertEqual(len(retry_messages), 4)
        self.assertEqual(len(retry_messages[2]["content"]), 6000)
        self.assertIn("length", retry_messages[3]["content"])

    def test_reading_other_dependency_group_keeps_current_sources_within_budget(self):
        files = {f"rtl/module_{i}.sv": f"module module_{i}; endmodule" for i in range(8)}
        paths = list(files)
        view = {"editable_sources": {p: files[p] for p in paths[:4]}, "diagnostics": {}}
        valid = {"paths": [paths[0]], "root_cause": "Current evidence",
                 "evidence": [{"path": paths[0], "anchor": files[paths[0]]}]}
        runtime = Mock()
        runtime.call_model_messages.side_effect = [
            {"answer": json.dumps({"read_paths": paths[4:], "reason": "Inspect consumers"})},
            {"answer": json.dumps(valid)}]
        request_diagnosis(runtime, {}, json.dumps(view), "diagnose", files)
        current = json.loads(runtime.call_model_messages.call_args.args[1][1]["content"])
        self.assertEqual(current["editable_sources"], files)
        self.assertLessEqual(sum(map(len, current["editable_sources"].values())), 96000)

    def test_malformed_diagnosis_stops_after_three_recorded_calls(self):
        runtime = Mock()
        runtime.call_model_messages.return_value = {"answer": "not JSON"}
        with self.assertRaisesRegex(ValueError, "after three calls"):
            request_diagnosis(runtime, {}, "current snapshot", "diagnose", {})
        self.assertEqual(runtime.call_model_messages.call_count, 3)

    def test_redundant_current_evidence_is_normalized_without_another_api_call(self):
        files = {"rtl/icache.sv": "logic [2047:0] line_data;"}
        item = {"path": "rtl/icache.sv", "anchor": files["rtl/icache.sv"]}
        brief = {"paths": list(files), "evidence": [item, item]}
        validate_diagnosis_evidence(brief, files)
        self.assertEqual(brief["evidence"], [item])
        stale = {"path": "rtl/icache.sv", "anchor": "logic [127:0] line_data;"}
        with self.assertRaises(ValueError):
            validate_diagnosis_evidence({"paths": list(files), "evidence": [item, stale]}, files)

    def test_diagnosis_cannot_cite_old_source(self):
        files = {"rtl/icache.sv": "logic [2047:0] line_data;"}
        with self.assertRaises(ValueError):
            validate_diagnosis_evidence({"paths": list(files), "evidence": [
                {"path": "rtl/icache.sv", "anchor": "logic [127:0] line_data;"}]}, files)
        validate_diagnosis_evidence({"paths": list(files), "evidence": [
            {"path": "rtl/icache.sv", "anchor": files["rtl/icache.sv"]}]}, files)

    def test_read_only_evidence_does_not_expand_edit_scope(self):
        files = {"rtl/a.sv": "module a; endmodule", "rtl/b.sv": "module b; endmodule"}
        brief = {"paths": ["rtl/a.sv"], "evidence": [
            {"path": path, "anchor": source} for path, source in files.items()]}
        validate_diagnosis_evidence(brief, files)
        self.assertEqual(brief["paths"], ["rtl/a.sv"])
        self.assertEqual(brief["evidence"], [{"path": "rtl/a.sv", "anchor": files["rtl/a.sv"]}])

    def test_unknown_retirement_is_evidence_not_probe_exception(self):
        result = check("RET|00000000|xxxxxxxx|00000000|00000013\n")
        self.assertEqual(result["kind"], "unknown_retirement")
        self.assertEqual(result["count"], 0)

    def test_late_instruction_mismatch_routes_recovery(self):
        report = {"passed": False, "stage": "simulation", "differential": {
            "kind": "retire_instruction", "count": 20, "pc": "0xbc"}}
        report["cache_audit"] = audit_icache_refill_response({}, report)
        packet = build_cpu_repair_packet(report)
        self.assertNotEqual(packet["causal_hints"]["earliest_observable_stage"],
                            "icache_refill_response_word_select")
        files = {"rtl/" + name + ".sv": "" for name in
                 ("icache", "rv32i5", "reorder_buffer", "issue_queue", "dcache")}
        paths = select_paths(packet, files, {})
        self.assertIn("rtl/reorder_buffer.sv", paths)
        self.assertNotIn("rtl/dcache.sv", paths)
        report["differential"]["kind"] = "retire_pc"
        files["rtl/frontend.sv"] = ""
        paths = select_paths(build_cpu_repair_packet(report), files, {})
        self.assertIn("rtl/frontend.sv", paths)
        self.assertIn("rtl/reorder_buffer.sv", paths)
        self.assertNotIn("rtl/dcache.sv", paths)

    def test_width_readiness_and_stale_done_evidence(self):
        sources = {"rtl/icache.sv": "logic [127:0] line_data; logic [127:0] line_tag;",
                   "rtl/load_store_unit.sv": "assign issue_done = sel_is_store ? store_response : sel_done;",
                   "rtl/rename_unit.sv": "ready_vec <= 40'h00000000ff;"}
        codes = {f["code"] for f in audit_cpu_structure(sources)["findings"]}
        self.assertIn("cache_storage_too_narrow", codes)
        self.assertIn("lsu_completion_not_valid_qualified", codes)
        self.assertIn("initial_architectural_pr_not_ready", codes)
        corrected = {"rtl/icache.sv": "logic [2047:0] line_data; logic [383:0] line_tag;",
                     "rtl/load_store_unit.sv": "assign issue_done = sel_valid && sel_done;",
                     "rtl/rename_unit.sv": "ready_vec <= 40'h00ffffffff;"}
        self.assertEqual(audit_cpu_structure(corrected)["findings"], [])

    def test_memory_trace_is_kept_without_successful_wave_probe(self):
        packet = build_cpu_repair_packet({"differential": {"kind": "no_ecall"},
            "trace": [{"event": "mem_write", "detail": "addr=0x200"}]})
        self.assertTrue(packet["causal_hints"]["dmem_activity_observed"])


if __name__ == "__main__":
    unittest.main()

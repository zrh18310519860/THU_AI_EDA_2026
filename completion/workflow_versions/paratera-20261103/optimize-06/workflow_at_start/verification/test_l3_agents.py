import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from ai_eda.agent.patches import apply_repair_payload
from ai_eda.agent.repair_context import digest, read_sources, select_paths, source_manifest, source_windows
from ai_eda.agent.skills.cpu_diagnostics import build_cpu_repair_packet
from ai_eda.agent.skills.cpu_protocol import audit_external_bus_protocol
from ai_eda.agent.subagents.common import validate_plan
from ai_eda.agent.orchestrator import candidate_score
from ai_eda.rtl.metrics import mapping_hotspots


class L3AgentTest(unittest.TestCase):
    def test_explicit_l3_baseline_integrity(self):
        from ai_eda.agent.skills.rv32i5_l3_reference import load_baseline
        plan, files, provenance = load_baseline()
        self.assertEqual(len(files), 11)
        self.assertEqual({m["file"] for m in plan["modules"]}, set(files))
        self.assertIn("Not independent", provenance["disclosure"])

    def test_diagnostic_probe_uses_no_private_signal_names(self):
        probe = (Path(__file__).parent / "diff_dft_tb.sv").read_text()
        self.assertNotIn("dut.", probe)
        self.assertIn("dbg_retire_valid", probe)

    def test_ppa_requires_functional_pass(self):
        from ai_eda.rtl.runner import evaluate_metrics
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "metadata.json").write_text(json.dumps({"task_name":"example", "testbench_path":"unused", "testbench_top":"unused"}))
            (root / "generated.sv").write_text("module example; endmodule")
            with patch("ai_eda.rtl.runner.synthesize_metrics") as synth:
                result = evaluate_metrics(root)
                synth.assert_not_called()
                self.assertEqual(result["metrics"]["status"], "skipped")

    def test_ppa_failure_changes_failure_identity(self):
        report={"passed":True,"stage":"simulation","metrics":{"status":"failed","reason":"syntax failure"}}
        first=build_cpu_repair_packet(report)
        report["metrics"]["reason"]="resource expansion"
        second=build_cpu_repair_packet(report)
        self.assertNotEqual(first["failure_fingerprint"],second["failure_fingerprint"])
        self.assertFalse(first["passed"])
        self.assertTrue(first["functional_passed"])
        self.assertEqual(first["stage"],"ppa")

    def test_memory_mapping_hotspot_extraction(self):
        log="Mapping memory cell \\ckpt in module \\rename_unit:\n  created 8 $dff cells and 0 static cells of width 192.\n  read interface: 0 $dff and 7 $mux cells.\n  write interface: 312 write mux blocks.\n9. Executing OPT"
        self.assertEqual(mapping_hotspots(log),[{"module":"rename_unit","memory":"ckpt","write_mux_blocks":312,"read_mux_cells":7}])

    def test_ppa_gate_rejects_failures_and_invalid_measurements(self):
        self.assertIsNone(candidate_score({"passed": True, "metrics": {"status": "failed", "area": 1, "total_power_w": 1}}))
        self.assertIsNone(candidate_score({"passed": False, "metrics": {"status": "ok", "area": 1, "total_power_w": 1}}))
        self.assertIsNone(candidate_score({"passed": True, "metrics": {"status": "ok", "area": 0, "total_power_w": 1}}))
        self.assertIsNone(candidate_score({"passed": True, "metrics": {"status": "ok", "area": float("nan"), "total_power_w": 1}}))
        self.assertEqual(candidate_score({"passed": True, "metrics": {"status": "ok", "area": 100, "total_power_w": .02}}),2)
    def test_interface_not_silently_truncated(self):
        ports = "input [31:0] port;" * 200
        plan = validate_plan({"modules": [{"name": "top", "file": "rtl/top.sv", "interfaces": ports}]})
        self.assertEqual(plan["modules"][0]["interfaces"], ports)

    def test_compact_duplicate_failures(self):
        report = {"passed": False, "stage": "simulation", "checks": [{"name": "RAW", "detail": "x1 wrong"}]*1000,
                  "trace": [{"cycle": x, "pc": "0010"} for x in range(10000)]}
        packet = build_cpu_repair_packet(report)
        self.assertLess(len(json.dumps(packet)), 10000)
        self.assertEqual(packet["checks"]["groups"][0]["count"], 1000)
        report["trace"] = []
        self.assertEqual(packet["failure_fingerprint"], build_cpu_repair_packet(report)["failure_fingerprint"])

    def test_patch_atomic_and_scope(self):
        sources = {"rtl/a.sv": "module a; endmodule"}
        with self.assertRaises(ValueError):
            apply_repair_payload({"edits": [{"path": "rtl/a.sv", "expected": "a;", "replacement": "b;"},
                {"path": "rtl/b.sv", "expected": "x", "replacement": "y"}]}, sources)
        self.assertEqual(sources["rtl/a.sv"], "module a; endmodule")
        with self.assertRaises(ValueError):
            apply_repair_payload({"edits": [{"path": "../a.sv", "expected": "x", "replacement": "y"}]}, sources)

    def test_patch_drops_redundant_overlapping_noop(self):
        sources = {"rtl/a.sv": "module a;\nassign y = a;\nendmodule\n"}
        result = apply_repair_payload({"edits": [
            {"path": "rtl/a.sv", "expected": "assign y = a;", "replacement": "assign y = b;"},
            {"path": "rtl/a.sv", "expected": "assign y = b;", "replacement": "assign y = b;"},
        ]}, sources)
        self.assertIn("assign y = b;", result.changed_files["rtl/a.sv"])
        self.assertEqual(result.manifest["ignored_noops"][0]["reason"], "redundant_noop")
        with self.assertRaises(ValueError):
            apply_repair_payload({"edits": [
                {"path": "rtl/a.sv", "expected": "module a;", "replacement": "module a;"},
            ]}, sources)

    def test_patch_drops_ambiguous_noop_when_real_edit_exists(self):
        sources = {"rtl/a.sv": "module a;\nassign y = a;\nassign z = a;\nendmodule\n"}
        result = apply_repair_payload({"edits": [
            {"path": "rtl/a.sv", "expected": "assign y = a;", "replacement": "assign y = b;"},
            {"path": "rtl/a.sv", "expected": "assign z = a;", "replacement": "assign z = a;"},
        ]}, sources)
        self.assertIn("assign y = b;", result.changed_files["rtl/a.sv"])
        self.assertEqual(result.manifest["ignored_noops"][0]["reason"], "redundant_noop")

    def test_external_bus_audit_exposes_response_mismatch(self):
        files = {
            "rtl/rv32i5.sv": """
assign ic_mem_resp_valid = ic_mem_resp_valid_r;
assign dc_mem_resp_valid = dmem_ready;
""",
            "rtl/icache.sv": "assign mem_req_valid = (state == S_REFIL);",
            "rtl/dcache.sv": "if (refill_cnt == 2'd3) begin data3[rf_idx] <= rf_w3; end",
        }
        audit = audit_external_bus_protocol(files, {"checks": [{"detail": "I-memory request changed before ready"}]})
        codes = {item["code"] for item in audit["findings"]}
        self.assertIn("bridge_delays_external_response", codes)
        self.assertIn("cache_consumes_delayed_response_without_pending", codes)
        self.assertIn("dcache_last_refill_uses_old_word", codes)
        self.assertIn("request_payload_changed_before_ready:icache", audit["observed_checks"])

    def test_causal_frontend_failure_excludes_downstream_dcache(self):
        files = {
            "rtl/rv32i5.sv": "module rv32i5; endmodule",
            "rtl/icache.sv": "module icache; endmodule",
            "rtl/branch_predictor.sv": "module branch_predictor; endmodule",
            "rtl/rename_unit.sv": "module rename_unit; endmodule",
            "rtl/dcache.sv": "module dcache; endmodule",
            "rtl/load_store_unit.sv": "module load_store_unit; endmodule",
        }
        packet = {
            "differential": {"candidate_paths": ["rtl/frontend_unit.sv"]},
            "causal_hints": {"earliest_observable_stage": "frontend_or_icache_before_data_memory"},
        }
        paths = select_paths(packet, files, {"modules": []})
        self.assertIn("rtl/icache.sv", paths)
        self.assertNotIn("rtl/dcache.sv", paths)

    def test_source_windows_bound(self):
        source = "module top;\n" + "assign a = b;\n"*10000 + "endmodule\n"
        self.assertLessEqual(len(source_windows(source, "assign", 16000)), 16000)

    def test_snapshot_immutable_and_hash_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def write_text(name, text):
                p = root/name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
                return name
            def write_json(name, value):
                return write_text(name, json.dumps(value))
            runtime = SimpleNamespace(write_text_artifact=write_text, write_json_artifact=write_json,
                read_text_artifact=lambda r: (root/r).read_text(),
                read_json_artifact=lambda r: json.loads((root/r).read_text()))
            ref = source_manifest(runtime, {"rtl/a.sv": "old"}, "manifest.json")
            source_manifest(runtime, {"rtl/a.sv": "new"}, "next.json")
            self.assertEqual(read_sources(runtime, ref), {"rtl/a.sv": "old"})
            item = runtime.read_json_artifact(ref)["files"][0]
            (root/item["artifact"]).write_text("corrupt")
            with self.assertRaises(ValueError):
                read_sources(runtime, ref)


if __name__ == "__main__":
    unittest.main()

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai_eda.agent.subagents.unit_validation import run_unit, validate_generated_module


class UnitPromotionTest(unittest.TestCase):
    def test_failed_auxiliary_repairs_do_not_replace_generated_source(self):
        self.check_promotion(False)

    def test_only_passing_auxiliary_repair_enters_source_provenance(self):
        self.check_promotion(True)

    def check_promotion(self, passes):
        with tempfile.TemporaryDirectory() as temp:
            original = "module leaf; wire x=0; endmodule"
            revised = "module leaf; wire x=1; endmodule\n"
            files = {"rtl/leaf.sv": original}
            origin = {"revisions": []}
            runtime = SimpleNamespace(run_dir=Path(temp), model_calls=0,
                write_text_artifact=Mock(return_value="artifacts/repair.sv"))
            replies = iter(['{"test_valid": true, "reason": "Contract test valid", "testbench": ""}',
                            revised, revised, revised])
            def call(*args, **kwargs):
                runtime.model_calls += 1
                return {"answer": next(replies)}
            def generate(*args, **kwargs):
                runtime.model_calls += 1
                return {"answer": 'module tb_unit; initial begin $fatal; $display("UNIT_PASS"); end endmodule'}
            runtime.call_model = call
            runtime.call_model_messages = generate
            failure = {"compiled": True, "passed": False, "log": "assertion"}
            success = {"compiled": True, "passed": True, "log": "UNIT_PASS"}
            outcomes = [dict(failure), dict(success if passes else failure), dict(failure), dict(failure)]
            with patch("ai_eda.agent.subagents.unit_validation.run_unit", side_effect=outcomes):
                result, record = validate_generated_module(runtime, {"step": 3},
                    {"file": "rtl/leaf.sv", "name": "leaf", "interfaces": "module leaf;"},
                    {"modules": []}, files, "contract", origin)
            self.assertEqual(result, revised if passes else original)
            self.assertEqual(files["rtl/leaf.sv"], result)
            self.assertEqual(len(origin["revisions"]), 1 if passes else 0)
            self.assertEqual(record["status"], "passed" if passes else "failed_advisory")


@unittest.skipUnless(shutil.which("iverilog"), "Icarus is required")
class UnitValidationTest(unittest.TestCase):
    def test_independent_transaction_oracle_rejects_wrong_payload(self):
        tb = ('module tb_unit; reg clk=0; always #5 clk=~clk; reg reset=1; '
              'reg [7:0] data=0; wire [7:0] result; leaf dut(clk,reset,data,result); '
              'initial begin #2; if(result===8\'hff) $fatal(1,"early"); '
              '@(negedge clk); reset=0; data=7; @(posedge clk); #1; '
              'if(result!==8\'d8) $fatal(1,"transaction 1"); '
              '@(negedge clk); data=19; @(posedge clk); #1; '
              'if(result!==8\'d20) $fatal(1,"transaction 2"); '
              '$display("UNIT_PASS"); $finish; end '
              'initial begin #200; $fatal(1,"watchdog"); end endmodule')
        source = ('module leaf(input clk,input reset,input [7:0] data,output reg [7:0] result); '
                  'always @(posedge clk) if(reset) result<=0; else result<=data+8\'d1; endmodule')
        with tempfile.TemporaryDirectory() as temp:
            good = run_unit(Path(temp) / "good", tb, {"leaf": source})
            bad = run_unit(Path(temp) / "bad", tb, {"leaf": source.replace("+8'd1", "+8'd2")})
        self.assertTrue(good["passed"])
        self.assertTrue(bad["compiled"])
        self.assertFalse(bad["passed"])
        self.assertIn("transaction 1", bad["log"])


if __name__ == "__main__":
    unittest.main()

import shutil
import tempfile
import unittest
from pathlib import Path

from ai_eda.agent.subagents.unit_validation import run_unit


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

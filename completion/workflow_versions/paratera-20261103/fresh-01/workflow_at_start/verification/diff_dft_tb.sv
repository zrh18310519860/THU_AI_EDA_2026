`timescale 1ns/1ps
module tb_diff_dft;
`include "tb/setup.svh"
`include "tb/assembler.svh"
`include "tb/harness.svh"
`include "tb/dft.svh"
  reg [31:0] snap_pc,snap_insn,snap_expected_insn;
  integer k;
  integer cosq [0:15];
  integer sinq [0:15];
  integer quiet_cycles=0;
  string diagnostic_vcd;
  integer fetched=0;
  integer issued=0;
  always @(posedge clk) begin
    if(rst_n) begin
      if(imem_valid && imem_ready) fetched=fetched+1;
      if(dbg_ooo_issue) issued=issued+1;
      if(dbg_retire_valid) quiet_cycles=0;
      else quiet_cycles=quiet_cycles+1;
      if(quiet_cycles==100) begin
        $display("STALL|cycle=%0d|imem_addr=%08h|imem_valid=%b|imem_ready=%b|fetched=%0d|issued=%0d|retired=%0d|dmem_valid=%b|dmem_ready=%b|halted=%b",
          cycle_count,imem_addr,imem_valid,imem_ready,fetched,issued,retired_count,dmem_valid,dmem_ready,halted);
        $finish;
      end
      if(dbg_bp_mispredict)
        $display("REDIRECT|cycle=%0d|imem_addr=%08h",cycle_count,imem_addr);
    end
    if(rst_n && dbg_retire_valid) begin
      snap_pc=dbg_retire_pc;snap_insn=dbg_retire_insn;
      snap_expected_insn=imem[dbg_retire_pc[11:2]];
      #1;dbg_reg_addr=snap_insn[11:7];#0.1;
      $display("RET|%08h|%08h|%08h|%08h",snap_pc,snap_insn,dbg_reg_data,snap_expected_insn);
    end
    if(cycle_count>50000) $finish;
  end
  initial begin
    if($value$plusargs("diagnostic_vcd=%s",diagnostic_vcd)) begin
      $dumpfile(diagnostic_vcd); $dumpvars(1,dut);
    end
    cur_test="diff_dft";dbg_reg_addr=0;
    cosq[0]=256;cosq[1]=237;cosq[2]=181;cosq[3]=98;cosq[4]=0;cosq[5]=-98;cosq[6]=-181;cosq[7]=-237;
    cosq[8]=-256;cosq[9]=-237;cosq[10]=-181;cosq[11]=-98;cosq[12]=0;cosq[13]=98;cosq[14]=181;cosq[15]=237;
    sinq[0]=0;sinq[1]=98;sinq[2]=181;sinq[3]=237;sinq[4]=256;sinq[5]=237;sinq[6]=181;sinq[7]=98;
    sinq[8]=0;sinq[9]=-98;sinq[10]=-181;sinq[11]=-237;sinq[12]=-256;sinq[13]=-237;sinq[14]=-181;sinq[15]=-98;
    build_t13_dft();begin_test("diff_dft");
    for(k=0;k<16;k=k+1) begin
      set_dmem_word('h300+4*k,k<8?1:-1);
      set_dmem_word('h380+4*k,cosq[k]);set_dmem_word('h3c0+4*k,sinq[k]);
    end
    release_reset();wait(halted);#15;$finish;
  end
endmodule

`timescale 1ns/1ps
module tb_l3_smoke;
`include "tb/setup.svh"
`include "tb/assembler.svh"
`include "tb/harness.svh"
  integer w;
  initial begin
    cur_test="startup"; dbg_reg_addr=0;
    for(w=0;w<=3;w=w+3) begin
      pass_no=2; prog_len=0;
      e_addi(1,0,512); e_addi(2,0,7); e_addi(3,2,5);
      e_sw(3,1,0); e_lw(4,1,0); e_addi(10,4,1); e_ecall();
      begin_test($sformatf("smoke_wait_%0d",w)); memory_wait=w;
      release_reset(); run_program(500,500);
      check_reg(2,7); check_reg(3,12); check_reg(4,12);
      check_mem_word(512,12); check_exit_code(13); end_test();
    end
    if(failures) $fatal(1,"SMOKE_RESULT: FAIL %0d",failures);
    $display("SMOKE_RESULT: PASS"); $finish;
  end
endmodule

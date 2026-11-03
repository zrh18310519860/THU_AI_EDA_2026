`timescale 1ns/1ps
module tb_l3_stress;
`include "tb/setup.svh"
`include "tb/assembler.svh"
`include "tb/harness.svh"
`include "tb/extensions.svh"

  reg [31:0] golden [0:31];
  reg [31:0] golden_mem [0:15];
  reg [31:0] rng;
  integer n, op, rd, s1, s2, offset, seed;
  logic expect_halt = 0;
  logic sampled_ecall;
  logic bus_idle_before_edge;

  // Spec A.3/D.3.2: retirement is a distinct edge before registered halt.
  always @(posedge clk) begin
    sampled_ecall = rst_n && dbg_retire_valid && dbg_retire_insn == 32'h00000073;
    bus_idle_before_edge = !imem_valid && !dmem_valid;
    if (!rst_n) begin
      expect_halt = 0;
    end else begin
      if (sampled_ecall && (halted !== 0 || exit_code !== 0))
        fail(cur_test, "ECALL retirement edge must precede registered halt/exit_code");
      #1;
      if (expect_halt && bus_idle_before_edge && halted !== 1)
        fail(cur_test, "Spec A.3/A.5/D.3.2: after accepted bus work drains, halted must assert on the next edge");
      if (expect_halt && halted && (imem_valid || dmem_valid))
        fail(cur_test, "halted asserted while an external request remained valid");
      if (halted && (dbg_retire_valid || dbg_bp_resolve || dbg_bp_mispredict || dbg_ooo_issue || dbg_ooo_bypass))
        fail(cur_test, "All capability events must be zero after halted");
      expect_halt = sampled_ecall || (expect_halt && !halted);
    end
  end

  task automatic pressure_war;
    integer k;
    begin
      pass_no=2; prog_len=0;
      e_addi(1,0,768); e_addi(2,0,5);
      e_lw(3,1,0);
      e_add(5,3,2); // Must retain the old x2 physical mapping while x3 waits.
      for(k=0;k<48;k=k+1) begin
        e_addi(2,2,1);
        e_addi(4,0,k);
      end
      e_sw(5,1,4); e_addi(10,0,91); e_ecall();
      begin_test("supplement_WAR_queue_pressure");
      memory_wait=12; set_dmem_word(768,100);
      release_reset(); run_program(3000,3000);
      check_reg(2,53); check_reg(3,100); check_reg(4,47); check_reg(5,105);
      check_mem_word(772,105); check_exit_code(91); check_halt_stability(); end_test();
    end
  endtask

  task automatic recovery_pressure;
    integer p,k;
    begin
      for(p=1;p<=2;p=p+1) begin
        pass_no=p; prog_len=0;
        e_addi(1,0,768); e_addi(7,0,0); e_addi(8,0,32);
        mark(L0);
        e_lw(2,1,0); e_addi(3,7,11);
        e_beq(0,0,L1);
        e_addi(3,0,999); e_sw(3,1,4); e_ecall();
        mark(L1);
        e_addi(7,7,1); e_blt(7,8,L0);
        e_add(9,2,3); e_sw(9,1,8); e_addi(10,0,92); e_ecall();
      end
      begin_test("supplement_checkpoint_reuse_wrongpath_store_ecall");
      memory_wait=7; set_dmem_word(768,100); set_dmem_word(772,32'h12345678);
      release_reset(); run_program(6000,6000);
      check_reg(2,100); check_reg(3,42); check_reg(7,32); check_reg(9,142);
      check_mem_word(772,32'h12345678); check_mem_word(776,142);
      check_exit_code(92); check_halt_stability(); end_test();
    end
  endtask

  task automatic randomized_program(input integer initial_seed, input integer wait_cycles);
    integer j;
    reg [31:0] value;
    begin
      pass_no=2; prog_len=0; rng=initial_seed;
      for(j=0;j<32;j=j+1) golden[j]=0;
      for(j=0;j<16;j=j+1) golden_mem[j]=32'hA5A5A5A5;
      e_addi(1,0,512); golden[1]=512;
      for(j=0;j<160;j=j+1) begin
        rng=rng*32'd1664525+32'd1013904223;
        op=rng[31:28]; rd=2+(rng[12:8]%30);
        s1=rng[4:0]; s2=rng[20:16]; offset=rng[25:22];
        case(op)
          0,1: begin e_add(rd,s1,s2); golden[rd]=golden[s1]+golden[s2]; end
          2: begin e_sub(rd,s1,s2); golden[rd]=golden[s1]-golden[s2]; end
          3: begin e_xor(rd,s1,s2); golden[rd]=golden[s1]^golden[s2]; end
          4: begin e_and(rd,s1,s2); golden[rd]=golden[s1]&golden[s2]; end
          5: begin e_or(rd,s1,s2); golden[rd]=golden[s1]|golden[s2]; end
          6: begin e_sll(rd,s1,s2); golden[rd]=golden[s1]<<golden[s2][4:0]; end
          7: begin e_srl(rd,s1,s2); golden[rd]=golden[s1]>>golden[s2][4:0]; end
          8: begin e_sra(rd,s1,s2); golden[rd]=$signed(golden[s1])>>>golden[s2][4:0]; end
          9: begin e_slt(rd,s1,s2); golden[rd]=$signed(golden[s1])<$signed(golden[s2]); end
          10: begin e_sltu(rd,s1,s2); golden[rd]=golden[s1]<golden[s2]; end
          11,12: begin e_lw(rd,1,offset*4); golden[rd]=golden_mem[offset]; end
          13: begin e_sw(s2,1,offset*4); golden_mem[offset]=golden[s2]; end
          default: begin
            value={{20{rng[31]}},rng[31:20]};
            e_addi(rd,s1,$signed(value)); golden[rd]=golden[s1]+value;
          end
        endcase
      end
      e_addi(10,0,93); golden[10]=93; e_ecall();
      begin_test($sformatf("supplement_random_seed_%0d_wait_%0d",initial_seed,wait_cycles));
      memory_wait=wait_cycles; release_reset(); run_program(10000,10000);
      for(j=0;j<32;j=j+1) check_reg(j,golden[j]);
      for(j=0;j<16;j=j+1) check_mem_word(512+j*4,golden_mem[j]);
      check_exit_code(93); check_halt_stability(); end_test();
    end
  endtask

  initial begin
    cur_test="supplement_power_on"; rst_n=0; dbg_reg_addr=0;
    #1; detect_capabilities(); suite_begin();
    pressure_war(); recovery_pressure();
    randomized_program(17,0);
    randomized_program(2026,3);
    randomized_program(1948,12);
    suite_end("supplemental_L3");
    if(failures) $fatal(1,"SUPPLEMENT_RESULT: FAIL failures=%0d",failures);
    $display("SUPPLEMENT_RESULT: PASS tests=%0d checks=%0d",tests_run,checks_run);
    $finish;
  end
endmodule

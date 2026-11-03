`timescale 1ns/1ps
module tb_rename_unit;
  reg clk=0,rst_n=0,uv=0,cv=0,cwe=0,kv=0,kwe=0,sv=0;
  always #5 clk=~clk;
  reg [4:0] rs1=0,rs2=0,rd=0,krd=0,dbg=0;
  reg [15:0] ctrl=0;
  reg [3:0] aid=0,hid=0,kid=0,sid=0;
  reg [5:0] cpr=0,kpr=0,kold=0;
  reg [31:0] cd=0;
  wire ur,dv;
  wire [5:0] dp,oldp,sp1,sp2;
  wire [31:0] dbgdata,x10,rdata;
  wire [39:0] ready;
  integer i;
  reg [5:0] p0,p1,p2;
  rename_unit r(.clk(clk),.rst_n(rst_n),.uop_valid(uv),.uop_ready(ur),
    .uop_pc(32'b0),.uop_insn(32'h13),.uop_rs1(rs1),.uop_rs2(rs2),.uop_rd(rd),
    .uop_imm(32'b0),.uop_funct3(3'b0),.uop_alu_op(5'b0),.uop_ctrl(ctrl),
    .uop_pred_next_pc(32'd4),.uop_pred_valid(1'b0),.uop_pred_type(2'b0),
    .disp_rob_ready(1'b1),.rob_alloc_id(aid),.disp_iq_ready(1'b1),
    .disp_valid(dv),.disp_prs1(sp1),.disp_prs2(sp2),.disp_prd(dp),.disp_old_prd(oldp),
    .complete_valid(cv),.complete_prd(cpr),.complete_data(cd),.complete_we(cwe),
    .commit_valid(kv),.commit_prd(kpr),.commit_old_prd(kold),.commit_rd(krd),.commit_rd_we(kwe),
    .squash_valid(sv),.squash_rob_id(sid),.squash_restore(1'b1),.prf_ready(ready),
    .prf_rd_en0(1'b1),.prf_rd_addr0(p0),.prf_rdata0(rdata),
    .prf_rd_en1(1'b0),.prf_rd_addr1(6'b0),.dbg_reg_addr(dbg),.dbg_reg_data(dbgdata),
    .arch_x10(x10),.rob_head_id(hid),.commit_rob_id(kid));
  task automatic writeback(input [5:0] tag,input [31:0] data);
    begin @(negedge clk);cv=1;cwe=1;cpr=tag;cd=data;@(negedge clk);cv=0;cwe=0;end
  endtask
  task automatic commit(input [3:0] id,input [4:0] a,input [5:0] newp,input [5:0] oldpr);
    begin @(negedge clk);kv=1;kwe=1;kid=id;krd=a;kpr=newp;kold=oldpr;
      @(negedge clk);kv=0;kwe=0;hid=(id+1)&7;end
  endtask
  initial begin
    p0=0;repeat(3) @(posedge clk);@(negedge clk);rst_n=1;
    #1;if(ready!==40'h00FFFFFFFF) $fatal(1,"PR initial readiness");
    uv=1;ctrl=16'h0004;rd=1;aid=0;
    #1;if(!ur||oldp!=1||dp<32) $fatal(1,"initial allocation");p0=dp;
    @(negedge clk);uv=0;writeback(p0,99);
    dbg=1;#1;if(dbgdata!=0||rdata!=99) $fatal(1,"speculation leaked or PRF read failed");
    commit(0,1,p0,1);#1;if(dbgdata!=99) $fatal(1,"commit not visible");
    // Older uncommitted result must survive a younger branch recovery.
    uv=1;ctrl=16'h0005;rd=2;rs1=1;aid=1;
    #1;if(sp1!=p0) $fatal(1,"RAW map");p1=dp;
    @(negedge clk);uv=0;writeback(p1,123);
    @(negedge clk);uv=1;ctrl=16'h2020;rd=0;rs1=0;aid=2;
    @(negedge clk);uv=0;
    @(negedge clk);uv=1;ctrl=16'h0004;rd=1;aid=3;
    #1;p2=dp;@(negedge clk);uv=0;writeback(p2,888);
    dbg=1;#1;if(dbgdata!=99) $fatal(1,"wrong-path PRF write leaked");
    @(negedge clk);sv=1;sid=2;@(negedge clk);sv=0;
    rs1=1;rs2=2;#1;
    if(sp1!=p0||sp2!=p1||!ready[p1]||ready[p2]) $fatal(1,"checkpoint restore/reclaim lost older state");
    commit(1,2,p1,2);dbg=2;#1;if(dbgdata!=123) $fatal(1,"older result lost after squash");
    // Repeated commits must recycle initial physical registers PR1..31 too.
    for(i=0;i<64;i=i+1) begin
      @(negedge clk);uv=1;ctrl=16'h0004;rd=10;aid=i&7;
      #1;if(!ur) $fatal(1,"free-list exhaustion at iteration %0d",i);
      p0=dp;p1=oldp;
      @(negedge clk);uv=0;writeback(p0,i+1);
      commit(i&7,10,p0,p1);
      dbg=10;#1;if(dbgdata!=(i+1)||x10!=(i+1)) $fatal(1,"recycle value mismatch");
    end
    $display("UNIT_RESULT: PASS rename PRF commit checkpoints recovery free-list recycling");$finish;
  end
  initial begin #100000;$fatal(1,"rename unit timeout");end
endmodule

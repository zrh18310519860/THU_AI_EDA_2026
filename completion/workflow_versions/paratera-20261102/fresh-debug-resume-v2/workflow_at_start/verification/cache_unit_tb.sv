`timescale 1ns/1ps
module tb_cache_units;
  reg clk=0, rst_n=0;
  always #5 clk=~clk;
  reg iv=0, df=0, iflush=0;
  reg [31:0] ia=0, da=0, dw=0;
  reg dv=0, dwe=0;
  reg [3:0] dbe=0;
  reg [1:0] iepoch=0;
  wire ir, irv, imv, imr, dr, drv, dmv, dmr, dmwe;
  wire [31:0] idata, ima, ddata, dma, dmwd;
  wire [3:0] dmbe;
  integer iw=0, dwt=0, reads_i=0, reads_d=0, writes_d=0, cycles=0, j;
  reg [31:0] memory [0:1023];
  reg ihold=0, dhold=0;
  reg [31:0] held_i, held_d, held_w;
  reg held_we;
  reg [3:0] held_be;
  wire mem_waiting;
  assign imr=iw>=3;
  assign dmr=dwt>=3;
  icache i(.clk(clk),.rst_n(rst_n),.cpu_req_valid(iv),.cpu_req_addr(ia),
    .cpu_req_ready(ir),.cpu_resp_valid(irv),.cpu_resp_data(idata),.flush(iflush),.flush_epoch(iepoch),
    .mem_req_valid(imv),.mem_req_ready(imr),.mem_req_addr(ima),
    .mem_resp_valid(imv&&imr),.mem_resp_data(ima^32'hA5001234));
  dcache d(.clk(clk),.rst_n(rst_n),.cpu_req_valid(dv),.cpu_req_we(dwe),.cpu_req_addr(da),
    .cpu_req_wdata(dw),.cpu_req_be(dbe),.cpu_req_ready(dr),.cpu_resp_valid(drv),.cpu_resp_rdata(ddata),
    .flush(df),.flush_epoch(2'b00),.mem_req_valid(dmv),.mem_req_ready(dmr),.mem_req_addr(dma),
    .mem_req_we(dmwe),.mem_req_wdata(dmwd),.mem_req_be(dmbe),
    .mem_resp_valid(dmv&&dmr),.mem_resp_data(memory[dma[11:2]]),.mem_waiting(mem_waiting));
  always @(posedge clk) begin
    cycles<=cycles+1;
    if(cycles>3000) $fatal(1,"cache unit timeout");
    if(!rst_n) begin iw<=0;dwt<=0;ihold<=0;dhold<=0;end
    else begin
      if(imv&&!imr) iw<=iw+1; else iw<=0;
      if(dmv&&!dmr) dwt<=dwt+1; else dwt<=0;
      if(ihold&&(!imv||ima!==held_i)) $fatal(1,"I request changed before ready");
      if(dhold&&(!dmv||dma!==held_d||dmwe!==held_we||
          (held_we&&(dmwd!==held_w||dmbe!==held_be)))) $fatal(1,"D request changed before ready");
      ihold<=imv&&!imr;held_i<=ima;
      dhold<=dmv&&!dmr;held_d<=dma;held_we<=dmwe;held_w<=dmwd;held_be<=dmbe;
      if(imv&&imr) reads_i<=reads_i+1;
      if(dmv&&dmr) begin
        if(dmwe) begin
          writes_d<=writes_d+1;
          for(j=0;j<4;j=j+1) if(dmbe[j]) memory[dma[11:2]][8*j+:8]<=dmwd[8*j+:8];
        end else reads_d<=reads_d+1;
      end
    end
  end
  task automatic iread(input [31:0] address);
    begin
      @(negedge clk);iv=1;ia=address;
      @(posedge clk);while(!ir) @(posedge clk);
      #1;iv=0;
      while(!irv) begin @(posedge clk);#1;end
      if(idata!==(address^32'hA5001234)) $fatal(1,"I data mismatch address=%h got=%h",address,idata);
    end
  endtask
  task automatic drequest(input bit we,input [31:0] address,input [31:0] data,input [3:0] be,input [31:0] expected);
    begin
      @(negedge clk);dv=1;dwe=we;da=address;dw=data;dbe=be;
      @(posedge clk);while(!dr) @(posedge clk);
      #1;dv=0;
      while(!drv) begin @(posedge clk);#1;end
      if(!we&&ddata!==expected) $fatal(1,"D data mismatch address=%h expected=%h got=%h",address,expected,ddata);
    end
  endtask
  integer before_i;
  initial begin
    for(j=0;j<1024;j=j+1) memory[j]=32'h11223344^(j*4);
    repeat(3) @(posedge clk); @(negedge clk);rst_n=1;
    iread(0);iread(4);iread(12);
    if(reads_i!=4) $fatal(1,"I line reuse expected4 got%0d",reads_i);
    iread(32'h01000000); // Detect truncated tags, not just capacity conflicts.
    if(reads_i!=8) $fatal(1,"I full tag mismatch");
    drequest(0,256,0,0,32'h11223244);
    drequest(1,257,32'hAAAAAAAA,4'b0010,0);
    drequest(0,256,0,0,32'h1122AA44);
    drequest(0,512,0,0,32'h11223144);
    drequest(0,256,0,0,32'h1122AA44);
    if(reads_d!=12||writes_d!=1) $fatal(1,"D traffic reads%0d writes%0d",reads_d,writes_d);
    @(negedge clk);iv=1;ia=32'h200;
    @(posedge clk);while(!ir) @(posedge clk);
    #1;iv=0;
    wait(imv&&!imr);
    @(negedge clk);iflush=1;iepoch=iepoch+1;
    @(negedge clk);iflush=0;
    repeat(8) @(posedge clk);
    before_i=reads_i;
    iread(32'h200);
    if(reads_i-before_i!=4) $fatal(1,"I canceled refill incorrectly installed");
    $display("UNIT_RESULT: PASS I/D hit refill conflict byte-write tag flush backpressure");
    $finish;
  end
endmodule

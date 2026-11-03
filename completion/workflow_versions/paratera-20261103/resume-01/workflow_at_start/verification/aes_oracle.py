"""Deterministic random-vector validation against the installed OpenSSL AES engine."""

import argparse
import json
import random
import subprocess
from pathlib import Path

from ai_eda.config import PROJECT_ROOT
from ai_eda.rtl.runner import test_rtl

TB = r'''
`timescale 1ns/1ps
module tb_aes_oracle;
  reg clk=0,rst_n=0,start=0;
  reg [127:0] key=0,plaintext=0;
  wire ready,done;
  wire [127:0] ciphertext;
  integer failures=0,cases=0,latency,max_latency=0;
  aes128 dut(.*);
  always #5 clk=~clk;
  task automatic vector(input [127:0] k,p,expected);
    begin
      if(ready !== 1 || done !== 0) $fatal(1,"not idle before vector");
      @(negedge clk); key=k;plaintext=p;start=1;
      @(posedge clk);#1;start=0; latency=0;
      if(done!==0 || ready!==0) $fatal(1,"incorrect accept handshake");
      // The latched transaction must survive live input changes and busy start.
      key=~k;plaintext=~p;
      while(done !== 1 && latency<200) begin
        @(negedge clk);start=(latency>=2 && latency<=4);
        @(posedge clk);#1;latency=latency+1;
        if(ready !== 0) $fatal(1,"ready while busy");
      end
      start=0;
      if(done!==1 || ciphertext!==expected) $fatal(1,"AES oracle mismatch case=%0d got=%032h expected=%032h latency=%0d",cases,ciphertext,expected,latency);
      if(latency>max_latency) max_latency=latency;
      #2;if(ciphertext!==expected || done!==1) $fatal(1,"done payload unstable");
      @(posedge clk);#1;
      if(done!==0 || ready!==1) $fatal(1,"done is not a single cycle");
      cases=cases+1;
    end
  endtask
  initial begin
    repeat(3) @(posedge clk);#1;
    @(negedge clk);rst_n=1;@(posedge clk);#1;
    __VECTORS__
    $display("ORACLE_RESULT: PASS cases=%0d max_latency=%0d",cases,max_latency);
    $finish;
  end
endmodule
'''


def validate(run_id, count=100):
    run = PROJECT_ROOT/'runs/prompt/homework_aes128'/run_id
    out=run/'oracle';out.mkdir(exist_ok=True)
    rng=random.Random(20261027)
    vectors=[]
    for _ in range(count):
        key=rng.getrandbits(128).to_bytes(16,'big')
        plain=rng.getrandbits(128).to_bytes(16,'big')
        result=subprocess.run(['openssl','enc','-aes-128-ecb','-K',key.hex(),'-nosalt','-nopad'],
            input=plain,capture_output=True,check=True)
        vectors.append((key.hex(),plain.hex(),result.stdout.hex()))
    (out/'vectors.json').write_text(json.dumps(vectors,indent=2))
    tb=out/'tb.sv'
    tb.write_text(TB.replace('__VECTORS__','\n'.join(
        f"vector(128'h{k},128'h{p},128'h{c});" for k,p,c in vectors)))
    report=test_rtl(task_name='aes_oracle',testbench_path=tb,testbench_top='tb_aes_oracle',
        rtl_path=run/'generated.sv',timeout_seconds=30)
    (out/'test_report.json').write_text(json.dumps(report,indent=2))
    print(run_id,report['passed'],report['stdout'][-1000:],flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run_id');parser.add_argument('--count',type=int,default=100)
    args=parser.parse_args();validate(args.run_id,args.count)

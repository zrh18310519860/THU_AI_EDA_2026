"""Run independent supplemental tests on a saved RTL snapshot."""

import argparse
import json
import hashlib
import subprocess
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser()
    p.add_argument("run_id")
    p.add_argument("--units",action="store_true")
    p.add_argument("--rename",action="store_true")
    p.add_argument("--attempt",type=int)
    args=p.parse_args()
    run=ROOT/"runs/agent"/args.run_id
    out=run/"supplemental"
    out.mkdir(exist_ok=True)
    rtl=sorted((run/"project/rtl").glob("*.sv"))
    if args.attempt:
        manifest=json.loads((run/f"artifacts/tested_manifest_{args.attempt}.json").read_text())
        rtl=[run/entry["artifact"] for entry in manifest["files"]]
        for path,entry in zip(rtl,manifest["files"]):
            assert hashlib.sha256(path.read_bytes()).hexdigest()==entry["sha256"]
    name="cache_unit_tb.sv" if args.units else "l3_stress_tb.sv"
    top="tb_cache_units" if args.units else "tb_l3_stress"
    if args.rename:
        name="rename_unit_tb.sv"
        top="tb_rename_unit"
    exe=out/(top+".vvp")
    start=time.perf_counter()
    compile_result=subprocess.run(["iverilog","-g2012","-DRV32I_FEATURE_PORTS","-I",
        str(ROOT/"content/tasks/homework_rv32i5"),"-s",top,"-o",str(exe),
        str(ROOT/"verification"/name),*map(str,rtl)],capture_output=True,text=True,timeout=30)
    result=compile_result
    if compile_result.returncode==0:
        result=subprocess.run(["vvp",str(exe)],capture_output=True,text=True,timeout=90)
    (out/(top+".log")).write_text(result.stdout+result.stderr)
    report={"passed":result.returncode==0,"stage":"simulation" if compile_result.returncode==0 else "compile",
        "snapshot_attempt":args.attempt,"duration_ms":round((time.perf_counter()-start)*1000),"stdout":result.stdout,"stderr":result.stderr}
    (out/(top+".json")).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(args.run_id,top,report["passed"],report["duration_ms"],(result.stdout+result.stderr)[-5000:])

if __name__=="__main__":main()

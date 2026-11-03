"""Find the first retired ISA mismatch, independently of end-state checks."""
import argparse
import json
import subprocess
import re
from collections import deque
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def signed(x,bits=32):
    return (x & ((1<<(bits-1))-1)) - (x & (1<<(bits-1)))

def check(output):
    regs=[0]*32
    mem=bytearray([0xa5]*4096)
    cos=[256,237,181,98,0,-98,-181,-237,-256,-237,-181,-98,0,98,181,237]
    sin=[0,98,181,237,256,237,181,98,0,-98,-181,-237,-256,-237,-181,-98]
    for base,values in ((0x300,[1]*8+[-1]*8),(0x380,cos),(0x3c0,sin)):
        for i,v in enumerate(values):mem[base+4*i:base+4*i+4]=(v&0xffffffff).to_bytes(4,"little")
    pc=0;count=0;history=deque(maxlen=16)
    for line in output.splitlines():
        if not line.startswith("RET|"):continue
        raw_fields=line.split("|")[1:]
        try:
            fields=[int(v,16) for v in raw_fields]
        except ValueError:
            return {"passed":False,"count":count,"kind":"unknown_retirement",
                    "raw_fields":raw_fields[:4],"history":list(history),
                    "reason":"A retired architectural record contains X/Z. Preserve waveform evidence; do not treat this as a failed diagnostic tool."}
        actual,inst,got=fields[:3]
        if len(fields) > 3 and inst != fields[3]:
            return {"passed":False,"count":count,"kind":"retire_instruction",
                    "pc":hex(actual),"expected_insn":hex(fields[3]),
                    "actual_insn":hex(inst),"history":list(history)}
        if actual!=pc:
            return {"passed":False,"count":count,"kind":"retire_pc","expected_pc":hex(pc),"actual_pc":hex(actual),"history":list(history)}
        op=inst&127;rd=(inst>>7)&31;f3=(inst>>12)&7;rs1=(inst>>15)&31;rs2=(inst>>20)&31
        a=regs[rs1];b=regs[rs2];nxt=(pc+4)&0xffffffff;value=None
        imm=signed(inst>>20,12)
        if op in (0x13,0x33):
            rhs=b if op==0x33 else imm&0xffffffff
            if f3==0:value=a-rhs if op==0x33 and inst&(1<<30) else a+rhs
            elif f3==1:value=a<<(rhs&31)
            elif f3==2:value=int(signed(a)<signed(rhs))
            elif f3==3:value=int(a<rhs)
            elif f3==4:value=a^rhs
            elif f3==5:value=signed(a)>>(rhs&31) if inst&(1<<30) else a>>(rhs&31)
            elif f3==6:value=a|rhs
            elif f3==7:value=a&rhs
        elif op==3:
            address=(a+imm)&4095;size={0:1,1:2,2:4,4:1,5:2}[f3]
            value=int.from_bytes(mem[address:address+size],"little")
            if f3<2:value=signed(value,size*8)
        elif op==0x23:
            simm=signed(((inst>>25)<<5)|((inst>>7)&31),12)
            address=(a+simm)&4095;size=1<<f3
            mem[address:address+size]=(b&((1<<(size*8))-1)).to_bytes(size,"little")
        elif op==0x63:
            off=signed(((inst>>31)<<12)|(((inst>>7)&1)<<11)|(((inst>>25)&63)<<5)|(((inst>>8)&15)<<1),13)
            cond={0:a==b,1:a!=b,4:signed(a)<signed(b),5:signed(a)>=signed(b),6:a<b,7:a>=b}[f3]
            if cond:nxt=(pc+off)&0xffffffff
        elif op==0x6f:
            off=signed(((inst>>31)<<20)|(((inst>>12)&255)<<12)|(((inst>>20)&1)<<11)|(((inst>>21)&1023)<<1),21)
            value=pc+4;nxt=(pc+off)&0xffffffff
        elif op==0x67:value=pc+4;nxt=(a+imm)&0xfffffffe
        elif op==0x37:value=inst&0xfffff000
        elif op==0x17:value=pc+(inst&0xfffff000)
        elif inst==0x73:
            return {"passed":True,"count":count+1,"kind":"ecall","history":list(history)}
        else:raise ValueError(hex(inst))
        record={"pc":hex(pc),"insn":hex(inst),"rs1":rs1,"a":hex(a),"rs2":rs2,"b":hex(b),"rd":rd,
                "expected":hex(value&0xffffffff) if value is not None else None,"got":hex(got)}
        history.append(record)
        if value is not None and rd:
            value&=0xffffffff
            if got!=value:return {"passed":False,"count":count,"kind":"register_write","mismatch":record,"history":list(history)}
            regs[rd]=value
        pc=nxt;count+=1
    return {"passed":False,"count":count,"kind":"no_ecall","last_pc":hex(pc),"history":list(history)}

def run_differential(sources, out):
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    executable=out/"diff.vvp"
    c=subprocess.run(["iverilog","-g2012","-DRV32I_FEATURE_PORTS","-I",str(ROOT/"content/tasks/homework_rv32i5"),
        "-s","tb_diff_dft","-o",str(executable),str(ROOT/"verification/diff_dft_tb.sv"),*map(str,sources)],capture_output=True,text=True,timeout=30)
    (out/"compile.log").write_text(c.stdout+c.stderr)
    if c.returncode:
        result={"available":False,"reason":"Public-port diagnostic probe compilation failed", "stderr":c.stderr[-2400:]}
        (out/"summary.json").write_text(json.dumps(result,indent=2))
        return result
    try:
        r=subprocess.run(["vvp","-i",str(executable),f"+diagnostic_vcd={out / 'startup.vcd'}"],capture_output=True,text=True,timeout=30)
    except subprocess.TimeoutExpired as error:
        text=error.stdout or b""
        if isinstance(text,bytes):text=text.decode("utf-8","replace")
        (out/"retire.log").write_text(text)
        result={"available":True,"passed":False,"kind":"scheduler_timeout",
                "reason":"Simulation stopped advancing: inspect combinational feedback before sequential FSM changes.",
                "probe_events":[line for line in text.splitlines() if line.startswith(("STALL|","REDIRECT|","RET|"))][-4:]}
        (out/"summary.json").write_text(json.dumps(result,indent=2))
        return result
    (out/"retire.log").write_text(r.stdout+r.stderr)
    result=check(r.stdout)
    if result["kind"] == "no_ecall" and "STALL|" not in r.stdout:
        result["kind"] = "probe_budget_exhausted"
        result["note"] = "No mismatch found in the finite probe; this is not evidence of an RTL failure."
    result["available"]=True
    try:
        from verification.teacher_probe import load_wave
        wave, ignored_tail = load_wave(out/'startup.vcd')
        result['incomplete_last_record_ignored'] = ignored_tail
        observations=[]
        for name in wave.references_to_ids:
            prefix='tb_diff_dft.dut.'
            if not name.startswith(prefix):continue
            short=name[len(prefix):]
            if '.' in short or not re.search(r'req|ready|valid|epoch|redirect|ecall|halt|flush|mispred|resolve|rob_id|comp_id|alloc_id|rob_full|rob_empty',short):continue
            values=wave[name].tv
            if not values:continue
            observations.append({'signal':short,'last':values[-1][1],
                'changes':len(values),'first_changes':values[:4],
                'ever_one':any(value.lstrip('0') == '1' for _,value in values),
                'known_values':list(dict.fromkeys(value for _,value in values
                    if re.fullmatch(r'[01]+',value)))[:16]})
        priority = {'rob_alloc_id','rob_comp_id','issue_rob_id','disp_rob_id',
                    'imem_valid','dmem_valid','ic_resp_valid','issue_valid','rob_alloc_ready',
                    'fetch_valid','fetch_pending','id_valid','dec_valid','disp_valid','rn_dec_ready','ecall_seen'}
        observations.sort(key=lambda item:(item['signal'].split('[',1)[0] not in priority,item['signal']))
        result['startup_signals']=observations[:100]
        unknown=[item['signal'] for item in observations if 'x' in item['last'] or 'z' in item['last']]
        if unknown:
            result['unknown_controls']=unknown
            result['causal_hint']=("Unknown payloads are legal for an invalid queue entry, but invalid execution must never "
                "drive branch redirect/resolve, predictor training, squash or architectural side effects. "
                "Check top-level qualification with issue_valid before changing cache state machines.")
            result['candidate_paths']=['rtl/rv32i5.sv','rtl/alu.sv','rtl/execute_unit.sv','rtl/issue_queue.sv']
    except (ImportError, OSError, ValueError) as error:
        result['startup_signals_error']=str(error)
    result["history"]=result.get("history",[])[-4:]
    result["probe_events"]=[line for line in r.stdout.splitlines() if line.startswith(("STALL|","REDIRECT|"))][-4:]
    if any(line.startswith("STALL|") for line in result["probe_events"]) and not result.get('unknown_controls'):
        result["candidate_paths"]=["rtl/frontend_unit.sv","rtl/fetch_unit.sv","rtl/icache.sv","rtl/rv32i5.sv"]
    (out/"summary.json").write_text(json.dumps(result,indent=2))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument("run_id");p.add_argument("--attempt",type=int,default=1);args=p.parse_args()
    run=ROOT/"runs/agent"/args.run_id
    refs=json.loads((run/f"artifacts/tested_manifest_{args.attempt}.json").read_text())
    out=run/"differential";out.mkdir(exist_ok=True)
    sources=[run/e["artifact"] for e in refs["files"]]
    result=run_differential(sources,out)
    (out/f"first_mismatch_{args.attempt}.json").write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=="__main__":main()

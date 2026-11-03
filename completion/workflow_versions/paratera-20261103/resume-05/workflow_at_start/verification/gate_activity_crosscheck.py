"""Independent mapped-netlist activity cross-check, separate from course RTL-VCD scoring."""

import argparse
import json
import subprocess
from pathlib import Path

from ai_eda.config import TASKS_ROOT
from ai_eda.rtl.metrics import _library_path, _sta_executable, _parse_power_report, yosys_executable


def crosscheck(run):
    run=Path(run)
    out=run/'gate_activity_crosscheck';out.mkdir(exist_ok=True)
    library=_library_path();models=out/'simcells.v'
    synth=subprocess.run([yosys_executable(),'-Q','-T','-p',f'read_liberty "{library}"; write_verilog -noattr "{models}"'],capture_output=True,text=True,check=True)
    (out/'models.log').write_text(synth.stdout+synth.stderr)
    mapped=run/'ppa/mapped_sta.v'
    task=TASKS_ROOT/'homework_aes128'
    exe=out/'power.vvp';vcd=out/'activity_gate.vcd'
    for name,top,tb in [('functional','tb_homework_aes128',task/'tb.sv'),('activity','tb_power_homework_aes128',task/'power_tb.sv')]:
        compile=subprocess.run(['iverilog','-g2012','-s',top,'-o',str(exe),str(tb),str(mapped),str(models)],capture_output=True,text=True,check=True)
        simulation=subprocess.run(['vvp','-i',str(exe),f'+vcd={vcd.resolve()}'],capture_output=True,text=True,timeout=60,check=True)
        (out/(name+'.log')).write_text(simulation.stdout+simulation.stderr)
        if name == 'functional' and 'LAB_RESULT: PASS' not in simulation.stdout:
            raise RuntimeError('Mapped teacher TB did not report PASS')
    script=out/'power.tcl'
    script.write_text(f'read_liberty "{library}"\nread_verilog "{mapped.resolve()}"\nlink_design aes128\ncreate_clock -name clk -period 10 [get_ports clk]\nread_vcd -scope tb_power_homework_aes128/dut "{vcd.resolve()}"\nreport_activity_annotation\nreport_power -digits 12\n')
    result=subprocess.run([_sta_executable(),'-exit',str(script)],capture_output=True,text=True,timeout=60,check=True)
    (out/'opensta.log').write_text(result.stdout+result.stderr)
    summary={'method':'Independent zero-delay mapped-netlist VCD cross-check; NOT course RTL-VCD score',
        'mapped_teacher_tb_passed':True,'power':_parse_power_report(result.stdout+result.stderr),
        'limitations':'No parasitics, glitches or timing closure; same original power TB and clock.'}
    (out/'summary.json').write_text(json.dumps(summary,indent=2))
    print(run.name,summary,flush=True)
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('run');args=parser.parse_args();crosscheck(args.run)

"""Re-test the exact selected RTL, optionally recomputing PPA; no model/API calls."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ai_eda.agent.subagents.common import combine_files
from ai_eda.rtl.runner import test_rtl, save_report

ROOT=Path(__file__).resolve().parents[1]

def main():
    parser=argparse.ArgumentParser();parser.add_argument('run_id');parser.add_argument('--ppa',action='store_true');args=parser.parse_args()
    run=ROOT/'runs/agent'/args.run_id
    selection=json.loads((run/'artifacts/candidate_selection.json').read_text())
    attempt=int(Path(selection['selected_report']).parent.name.split('-')[1])
    manifest=json.loads((run/f'artifacts/tested_manifest_{attempt}.json').read_text())
    sources={}
    for entry in manifest['files']:
        text=(run/entry['artifact']).read_text()
        assert hashlib.sha256(text.encode()).hexdigest()==entry['sha256']
        sources[entry['path']]=text
    output=run/'independent_retest'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    output.mkdir(parents=True)
    combined=output/'verified_snapshot.sv';combined.write_text(combine_files(sources))
    teacher=ROOT/'content/tasks/homework_rv32i5'
    report=test_rtl(task_name='independent_L3',testbench_path=teacher/'tb.sv',testbench_top='tb_agent_rv32i5',
        rtl_path=combined,expected_cpu_level=3,rtl_top_module='rv32i5',timeout_seconds=90,
        extra_testbench_path=ROOT/'verification/l3_stress_tb.sv',extra_testbench_top='tb_l3_stress',
        run_dir=output if args.ppa else None,power_testbench_path=teacher/'power_tb.sv' if args.ppa else None,
        power_testbench_top='tb_power_agent_rv32i5' if args.ppa else None)
    save_report(output,report)
    print(json.dumps({'passed':report['passed'],'cpu_capability':report.get('cpu_capability'),
        'metrics':report.get('metrics'),'report':str(output/'test_report.json')},ensure_ascii=False,indent=2))
    if not report['passed'] or (args.ppa and not report.get('ppa_passed')):raise SystemExit(1)

if __name__=='__main__':main()

"""Separate fresh AES sampling, explicit incremental L3 runs, and failed-run recovery."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from ai_eda.agent.orchestrator import run_agent
from ai_eda.config import PROJECT_ROOT, TASKS_ROOT
from ai_eda.rtl.runner import create_run, run_testbench, evaluate_metrics

EVIDENCE = Path('/mnt/f/AI_EDA/stability-evidence-20261027')


def save(name, data):
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    (EVIDENCE / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


_previous_event = None

def agent_event(item):
    global _previous_event
    state=(item['run_id'],item['step'],item['status'])
    if state != _previous_event:
        print(*state,item['title'],flush=True)
        _previous_event=state


def aes(count, prompt_name='H1_shared_sbox.md'):
    task = TASKS_ROOT / 'homework_aes128'
    records = []
    for _ in range(count):
        name = 'hw-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
        run = create_run(task_name='homework_aes128', spec_path=task/'spec.md',
            testbench_path=task/'tb.sv', testbench_top='tb_homework_aes128',
            prompt_path=PROJECT_ROOT/'content/prompts/homework'/prompt_name,
            run_id=name, temperature=.2, max_tokens=12000, thinking=False, timeout_seconds=30)
        report = run_testbench(run)
        records.append({'run_id':name, 'passed':report['passed']})
        suffix='-two' if 'two' in prompt_name else ('-logic' if 'logic' in prompt_name else '')
        save('aes-shared-generation' + suffix + '.json', records)
        print('AES', name, report['passed'], report.get('checks'), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['aes', 'l3', 'recover', 'metrics'])
    parser.add_argument('--count', type=int, default=3)
    parser.add_argument('--run')
    parser.add_argument('--logic',action='store_true')
    parser.add_argument('--two',action='store_true')
    args = parser.parse_args()
    if args.kind == 'l3':
        parser.error('Historical baseline-reuse batch is disabled. Use python -m verification.run_l3 for real fresh exploration.')
    if args.kind == 'aes':
        aes(args.count, 'H1_two_sbox.md' if args.two else ('H1_shared_sbox_logic.md' if args.logic else 'H1_shared_sbox.md'))
    elif args.kind == 'metrics':
        suffix='-two' if args.two else ('-logic' if args.logic else '')
        records = json.loads((EVIDENCE/('aes-shared-generation'+suffix+'.json')).read_text())
        for record in records:
            if record['passed']:
                run = PROJECT_ROOT/'runs/prompt/homework_aes128'/record['run_id']
                report = evaluate_metrics(run)
                save(record['run_id']+'.json', report)
                print('PPA', record['run_id'], report['metrics'], flush=True)
    else:
        records=[]
        for _ in range(args.count if args.kind == 'l3' else 1):
            options = {'resume_from':args.run, 'snapshot_attempt':1, 'restart_stage':'tester', 'l3_mode':'fresh'} if args.kind == 'recover' else {}
            result = run_agent(project_id='rv32i5_ooo', thinking=True, max_tokens=24000,
                max_calls=16, max_repair_attempts=4, optimization_rounds=2 if args.kind=='l3' else 0,
                on_event=agent_event, **options)
            save(result['run_id']+'.json',result)
            records.append({'run_id':result['run_id'], 'status':result['status'],
                'mode':result.get('result',{}).get('generation_mode'), 'usage':result.get('usage'),
                'duration_ms':result.get('duration_ms')})
            save(args.kind+'-runs.json',records)
            print('RESULT',records[-1],flush=True)


if __name__ == '__main__':
    main()

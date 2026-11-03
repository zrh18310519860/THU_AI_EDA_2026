"""Export actual L3 model usage, tested candidates and selected benchmark evidence."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=Path('/mnt/f/AI_EDA/L3-evidence-20261027')

def load(path):
    return json.loads(path.read_text(encoding='utf-8'))

def save(name,data):
    (OUT/name).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')

def table(name,rows):
    if not rows:return
    columns=list(dict.fromkeys(key for row in rows for key in row))
    with (OUT/name).open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader();writer.writerows(rows)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('selected_run');args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    records=[];candidates=[];calls=[];contexts=[]
    for run in sorted((ROOT/'runs/agent').glob('agent-*')):
        if not (run/'events.json').exists():continue
        eventlog=load(run/'events.json')
        if eventlog.get('project_id')!='rv32i5_ooo':continue
        summary=eventlog.get('run_summary') or {}
        records.append({'run_id':run.name,'status':eventlog['status'],
            'total_tokens':summary.get('total_tokens'),'prompt_tokens':summary.get('prompt_tokens'),
            'completion_tokens':summary.get('completion_tokens'),'model_calls':summary.get('model_calls'),
            'duration_ms':eventlog.get('duration_ms'),'duration_basis':summary.get('duration_basis'),
            'duration_uncertain':summary.get('duration_basis')=='sum_event_durations_on_external_interrupt',
            'resumed_from':eventlog.get('resumed_from'),'error':eventlog.get('error'),
            'model_parameters':eventlog.get('model_parameters'),
            'model_parameter_sets':summary.get('model_parameter_sets')})
        for event in eventlog.get('events',[]):
            for number,call in enumerate(event.get('model_calls') or [],1):
                usage=call.get('usage') or {}
                calls.append({'run_id':run.name,'step':event['step'],'agent':event['node_id'],'call':number,
                    'model':call.get('model'),'duration_ms':call.get('duration_ms'),
                    'prompt_tokens':usage.get('prompt_tokens'),'completion_tokens':usage.get('completion_tokens'),
                    'total_tokens':usage.get('total_tokens'),'parameters':json.dumps(event.get('model_parameters'),ensure_ascii=False)})
            if event.get('node_id') in ('diagnoser','modifier'):
                contexts.append({'run_id':run.name,'step':event['step'],'agent':event['node_id'],
                    'prompt_chars':len(event.get('prompt','')),'attempt':event.get('attempt')})
        for report_path in sorted(run.glob('tester-*/test_report.json')):
            report=load(report_path);metrics=report.get('metrics') or {}
            capability=report.get('cpu_capability') or {}
            selection=load(run/'artifacts/candidate_selection.json') if (run/'artifacts/candidate_selection.json').exists() else {}
            candidates.append({'run_id':run.name,'tester':report_path.parent.name,
                'teacher_passed':report.get('teacher_passed',report.get('passed')),
                'functional_passed':report.get('passed'),'verified_level':capability.get('verified_level'),
                'area':metrics.get('area'),'total_power_w':metrics.get('total_power_w'),
                'dynamic_power_w':metrics.get('dynamic_power_w'),'leakage_power_w':metrics.get('leakage_power_w'),
                'ppa_status':metrics.get('status'),'selected_in_run':selection.get('selected_report')==str(report_path.relative_to(run)),
                'timings_ms':report.get('timings_ms'),
                'first_failures':report.get('checks',[])[:3]})
    chosen=ROOT/'runs/agent'/args.selected_run
    result=load(chosen/'result.json')
    assert result['passed'], 'Selected run must have accepted functional/PPA result'
    selection=load(chosen/'artifacts/candidate_selection.json')
    report=load(chosen/selection['selected_report'])
    assert report['passed'] and report['metrics']['status']=='ok'
    assert report['cpu_capability']['verified_level']==3
    attempt=int(Path(selection['selected_report']).parent.name.split('-')[1])
    manifest=load(chosen/f'artifacts/tested_manifest_{attempt}.json')
    for entry in manifest['files']:
        source=chosen/entry['artifact']
        assert hashlib.sha256(source.read_bytes()).hexdigest()==entry['sha256']
        assert source.read_bytes()==(chosen/'project'/entry['path']).read_bytes()
    hashes=load(OUT/'teacher_hashes_before.json')
    teacher=ROOT/'content/tasks/homework_rv32i5'
    actual={str(p.relative_to(teacher)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(teacher.rglob('*')) if p.is_file()}
    assert hashes==actual,'Teacher files changed'
    total_tokens=sum(row['total_tokens'] for row in records if row['total_tokens'] is not None)
    summary={'selected_run':chosen.name,'selected_tester':f'tester-{attempt:02d}',
        'records':records,'candidates':candidates,'report':report,
        'total_tokens_known':total_tokens,'usage_complete':all(row['total_tokens'] is not None for row in records),
        'total_duration_ms':None if any(row['duration_uncertain'] for row in records) else sum(row['duration_ms'] or 0 for row in records),
        'total_duration_known_ms':sum(row['duration_ms'] or 0 for row in records if not row['duration_uncertain']),
        'total_duration_complete':all(row['duration_ms'] is not None and not row['duration_uncertain'] for row in records),
        'total_model_calls':sum(row['model_calls'] or 0 for row in records),
        'teacher_hashes_unchanged':True,'selected_sources':manifest,
        'manual_interventions':[
            'Corrected frozen internal interface contract before second full generation.',
            'Added differential retirement/state probe after blind timeout repair failed.',
            'Corrected own supplemental random program length from 240 to 160 instructions; teacher files untouched.',
            'Terminated expensive synthesis trial at tester-02 of run 104919368181 and cancelled its successor; resource-heavy checkpoint expansion was retained in logs.',
            'Added measured memory-mapping hotspots and single-variable optimization guidance.']}
    save('l3-summary.json',summary)
    table('l3-runs.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in records])
    table('l3-candidates.csv',[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in candidates])
    table('l3-model-calls.csv',calls);table('l3-contexts.csv',contexts)
    table('l3-benchmarks.csv',report.get('performance',{}).get('benchmarks',[]))
    save('teacher_hashes_after.json',actual)
    print(json.dumps({k:summary[k] for k in ('selected_run','selected_tester','total_tokens_known','total_model_calls','usage_complete','teacher_hashes_unchanged')},ensure_ascii=False))

if __name__=='__main__':main()

"""Export actual run results and usage; never infer missing measurements."""

import csv
import hashlib
import json
import subprocess
from pathlib import Path

from ai_eda.config import PROJECT_ROOT, TASKS_ROOT

OUT=Path('/mnt/f/AI_EDA/stability-evidence-20261027')


def write_csv(name, records):
    if not records:return
    fields=list(dict.fromkeys(key for record in records for key in record))
    with (OUT/name).open('w',newline='',encoding='utf-8-sig') as stream:
        writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(records)


def main():
    aes=[];agents=[];candidates=[];calls=[]
    aes_root=PROJECT_ROOT/'runs/prompt/homework_aes128'
    ids=[p.name for p in sorted(aes_root.glob('hw-20261027T15*'))]
    for name in ids:
        run=aes_root/name
        meta=json.loads((run/'metadata.json').read_text())
        report=json.loads((run/'test_report.json').read_text())
        prompt=(run/'prompt.md').read_text()
        architecture='2 lookup' if 'TWO shared' in prompt else ('4 Boolean' if 'aes_sbox_logic' in prompt else '4 lookup')
        metrics=report.get('metrics') or {}
        record={'run_id':name,'architecture':architecture,'passed':report.get('passed'),
            'area':metrics.get('area'),'power_w':metrics.get('total_power_w'),
            'tokens':meta.get('usage',{}).get('total_tokens'),'duration_ms':meta.get('duration_to_tb_ms'),
            'generation_ms':meta.get('generation_duration_ms'),'tb_ms':meta.get('tb_duration_ms'),
            'ppa_ms':meta.get('metrics_duration_ms'),'model':meta.get('model'),'temperature':meta.get('temperature'),
            'thinking':meta.get('thinking'),'max_tokens':meta.get('max_tokens')}
        oracle=run/'oracle/test_report.json'
        record['oracle_passed']=json.loads(oracle.read_text())['passed'] if oracle.exists() else None
        gate=run/'gate_activity_crosscheck/summary.json'
        record['gate_crosscheck_w']=json.loads(gate.read_text())['power']['total_power_w'] if gate.exists() else None
        aes.append(record)
        for index,call in enumerate(meta.get('model_calls',[])):
            calls.append({'run_id':name,'step':'generation','call':index+1,**{k:call.get(k) for k in ('status','duration_ms','model','thinking','temperature','max_tokens')},**(call.get('usage') or {})})
    agent_paths=list((PROJECT_ROOT/'runs/agent').glob('agent-20261027T15*'))
    original_failure=PROJECT_ROOT/'runs/agent/agent-20261027T135106050927'
    if original_failure.is_dir():agent_paths.append(original_failure)
    for run in sorted(agent_paths):
        data=json.loads((run/'events.json').read_text());result=data.get('result') or {}
        agents.append({'run_id':run.name,'status':data['status'],'mode':result.get('generation_mode'),
            'duration_ms':data.get('duration_ms'),**(data.get('usage') or {})})
        selection=run/'artifacts/candidate_selection.json'
        if selection.exists():
            selected=json.loads(selection.read_text())['selected_report']
            for tester in sorted(run.glob('tester-*/test_report.json')):
                report=json.loads(tester.read_text());m=report.get('metrics') or {}
                candidates.append({'run_id':run.name,'tester':tester.parent.name,'functional_passed':report.get('passed'),
                    'teacher_passed':report.get('teacher_passed'),'ppa_status':m.get('status'),
                    'area':m.get('area'),'power_w':m.get('total_power_w'),
                    'selected':tester.relative_to(run).as_posix()==selected,
                    'level':report.get('cpu_capability',{}).get('verified_level')})
        for event in data.get('events',[]):
            for index,call in enumerate(event.get('model_calls') or []):
                calls.append({'run_id':run.name,'step':event['node_id'],'call':index+1,
                    **{k:call.get(k) for k in ('status','duration_ms','model','thinking','temperature','max_tokens')},**(call.get('usage') or {})})
    valid=[r for r in candidates if r['selected'] and r['functional_passed'] and r['ppa_status']=='ok']
    best_l3=min(valid,key=lambda r:r['area']*r['power_w']) if valid else None
    valid_aes=[r for r in aes if r['passed'] and r['area'] and r['power_w'] and r['oracle_passed']]
    best_aes=min(valid_aes,key=lambda r:r['area']*r['power_w']) if valid_aes else None
    teacher=TASKS_ROOT
    hashes={p.relative_to(teacher).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for task in ['homework_aes128','homework_rv32i5'] for p in sorted((teacher/task).rglob('*')) if p.is_file()}
    (OUT/'teacher_hashes_after.json').write_text(json.dumps(hashes,indent=2))
    source=Path('/mnt/d/大三/人工智能赋能数字电路设计/THU_AI_EDA_2026')
    comparison={}
    for name,actual in hashes.items():
        original=subprocess.check_output(['git','show','HEAD:content/tasks/'+name],cwd=source)
        comparison[name]={'original_git_sha256':hashlib.sha256(original).hexdigest(),
            'runtime_sha256':actual,'unchanged':hashlib.sha256(original).hexdigest()==actual}
    (OUT/'teacher_integrity.json').write_text(json.dumps(comparison,indent=2))
    if not all(row['unchanged'] for row in comparison.values()):
        raise RuntimeError('Teacher input differs from original Git version')
    summary={'aes':aes,'agents':agents,'candidates':candidates,'selected_l3':best_l3,'selected_aes':best_aes,
        'mode_disclosure':'Three L3 runs independently invoke optimization models, but share a prior verified baseline; not from-scratch generation success.',
        'ta_comparison':'Screenshot 1000/130 has no units, version, architecture or clock. No normalization to those values is performed.'}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    write_csv('aes-runs.csv',aes);write_csv('agent-runs.csv',agents);write_csv('agent-candidates.csv',candidates);write_csv('model-calls.csv',calls)
    print(json.dumps({'selected_aes':best_aes,'selected_l3':best_l3},indent=2))


if __name__=='__main__':main()

"""Correct known clock-adjusted runs using their saved measured stage durations."""

import csv
import json
import subprocess
from datetime import datetime
from pathlib import Path

from ai_eda.agent.runs import load_agent_run
from ai_eda.run_records import save_prompt_summary
from ai_eda.web.server import _run_payload

ROOT = Path("/home/eda/projects/THU_AI_EDA_2026")
OUT = Path("/mnt/f/AI_EDA/report-evidence-20261027")


def write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


records = json.loads((OUT/"aes-reruns.json").read_text(encoding="utf-8"))
for item in records:
    directory = ROOT/"runs/prompt/homework_aes128"/item["run_id"]
    path = directory/"metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    first = next(entry for entry in metadata["verification_history"] if entry["kind"] == "tb" and not entry.get("error"))
    gap = max(0, (datetime.fromisoformat(first["started_at"])-datetime.fromisoformat(metadata["generation_finished_at"])).total_seconds()*1000)
    metadata["original_calendar_duration_to_tb_ms"] = metadata.get("duration_to_tb_ms")
    metadata["duration_to_tb_ms"] = round(metadata["generation_duration_ms"]+gap+first["duration_ms"],1)
    metadata["duration_clock"] = "measured_stages_plus_idle_gap"
    metadata["resample_of"] = item["resample_of"]
    write(path,metadata)
    save_prompt_summary(directory)
    run = _run_payload(directory, item["report"])
    write(OUT/(item["run_id"]+".json"),run)
    item["summary"] = run["run_summary"]
write(OUT/"aes-reruns.json",records)

for directory in (ROOT/"runs/prompt/homework_aes128").glob("hw-*"):
    save_prompt_summary(directory)

agent_id = json.loads((OUT/"experiment-index.json").read_text())["agent_run_id"]
agent = load_agent_run(agent_id)
write(ROOT/"runs/agent"/agent_id/"events.json",agent)
write(ROOT/"runs/agent"/agent_id/"run_summary.json",agent["run_summary"])
write(OUT/(agent_id+".json"),agent)
write(OUT/"experiment-index.json",{"aes":records,"agent_run_id":agent_id})

with (OUT/"aes-records.csv").open("w",encoding="utf-8-sig",newline="") as handle:
    columns = ["run_id","resample_of","model","temperature","max_tokens","thinking","prompt_tokens","completion_tokens","total_tokens","duration_ms","generation_duration_ms","tb_duration_ms","metrics_duration_ms","area","total_power_w"]
    writer = csv.DictWriter(handle,fieldnames=columns)
    writer.writeheader()
    for item in records:
        row = {**item["summary"],"run_id":item["run_id"],"resample_of":item["resample_of"]}
        writer.writerow({key:row.get(key) for key in columns})

versions = {}
for name,args in {"python":[".venv/bin/python","--version"],"iverilog":["iverilog","-V"],"yosys":["yosys","-V"],"sta":["/home/eda/.local/bin/sta","-version"]}.items():
    result=subprocess.run(args,cwd=ROOT,capture_output=True,text=True)
    versions[name]=(result.stdout or result.stderr).splitlines()[0]
write(OUT/"tool-versions.json",versions)
print(json.dumps({"corrected_times_ms":[r["summary"]["duration_ms"] for r in records],"tokens":sum(r["summary"]["total_tokens"] for r in records),"agent":agent["usage"],"versions":versions},ensure_ascii=False))

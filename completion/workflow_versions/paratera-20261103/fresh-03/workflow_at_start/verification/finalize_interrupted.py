"""Close an externally interrupted run without rewriting completed test evidence."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ai_eda.agent.runs import _usage_summary, _complete_summary

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("run_id")
    parser.add_argument("reason")
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/"runs/agent"/args.run_id
    path=root/"events.json"
    data=json.loads(path.read_text())
    if data["status"] != "running":
        raise ValueError("Only an interrupted running record may be finalized")
    now=datetime.now(timezone.utc).isoformat()
    for event in data["events"]:
        if event["status"] == "running":
            event["status"]="cancelled"
            event["error"]=args.reason
            event["finished_at"]=now
            counter=event.pop("_started_clock",None)
            if counter is not None:
                event["duration_ms"]=round((time.perf_counter()-counter)*1000)
    data.update(status="cancelled",error=args.reason,finished_at=now)
    data["duration_ms"]=sum(e.get("duration_ms",0) for e in data["events"])
    data["usage"]=_usage_summary(data["events"])
    data["run_summary"]={**data.get("model_parameters",{}),**data["usage"],"duration_ms":data["duration_ms"]}
    _complete_summary(data)
    summary=data["run_summary"]
    summary["duration_basis"]="sum_event_durations_on_external_interrupt"
    summary["status"]="cancelled"
    data["run_summary"]=summary
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2))
    (root/"run_summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=="__main__":main()

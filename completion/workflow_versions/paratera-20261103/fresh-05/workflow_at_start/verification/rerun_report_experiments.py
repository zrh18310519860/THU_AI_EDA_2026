"""Rerun saved AES prompt variants through the same API used by the workbench."""

import json
import time
from pathlib import Path
from urllib.request import Request, urlopen


BASE = "http://127.0.0.1:8000"
PROJECT = Path("/home/eda/projects/THU_AI_EDA_2026")
OUTPUT = Path("/mnt/f/AI_EDA/report-evidence-20261027")
OUTPUT.mkdir(parents=True, exist_ok=True)


def api(path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = Request(BASE+path, data=data, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=900) as response:
        return json.load(response)


def save(name, payload):
    (OUTPUT/name).write_text(json.dumps(payload, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")


def homework(source):
    root = PROJECT/"runs/prompt/homework_aes128"/source
    prompt = (root/"prompt.md").read_text(encoding="utf-8")
    body = {"content": prompt, "temperature": 0.2, "max_tokens": 8192, "thinking": False, "reasoning_effort": "high"}
    request = Request(BASE+"/api/homework/stream", data=json.dumps(body).encode(), headers={"Content-Type":"application/json"})
    run = None
    with urlopen(request, timeout=900) as response:
        for line in response:
            text = line.decode("utf-8").strip()
            if not text.startswith("data:"):
                continue
            event = json.loads(text[5:])
            if event["type"] == "start":
                print("AES started", source, "->", event["run_id"], flush=True)
            elif event["type"] == "error":
                raise RuntimeError(event["error"])
            elif event["type"] == "done":
                run = event["run"]
    if run is None:
        raise RuntimeError("No saved generation")
    verification = {"task_id":"homework_aes128", "run_id":run["run_id"], "mode":"tb"}
    run = api("/api/verify", verification)
    print("AES TB", run["run_id"], run["report"].get("passed"), run["run_summary"], flush=True)
    if run["report"].get("passed"):
        verification["mode"] = "metrics"
        run = api("/api/verify", verification)
    run["resample_of"] = source
    save(run["run_id"]+".json", run)
    print("AES finished", run["run_id"], "PASS" if run["report"].get("passed") else "FAIL",
          "tokens", run["run_summary"].get("total_tokens"), "ms", run["run_summary"].get("duration_ms"),
          "area", run["run_summary"].get("area"), "power",run["run_summary"].get("total_power_w"), flush=True)
    return {"run_id": run["run_id"], "resample_of":source, "summary":run["run_summary"], "report":run["report"]}


def agent():
    body = {"project_id":"stream_filter", "temperature":0.2, "max_tokens":60000,
            "thinking":True, "reasoning_effort":"low", "planner_skill":False, "max_calls":16, "max_repair_attempts":2}
    job_id = api("/api/agent/jobs",body)["job_id"]
    print("Agent job",job_id,flush=True)
    previous = None
    while True:
        job = api("/api/agent/jobs/"+job_id)
        events = job.get("events") or []
        last = events[-1] if events else {}
        progress = (job["status"],last.get("step"),last.get("status"))
        if progress != previous:
            print("Agent",progress,last.get("title"),flush=True)
            previous = progress
        if job["status"] not in {"running","cancelling","waiting_input"}:
            result = job.get("result") or api("/api/agent/runs/"+job["run_id"])
            save(result["run_id"]+".json",result)
            return result
        if job["status"] == "waiting_input":
            raise RuntimeError("Agent requires specification clarification")
        time.sleep(1)


if __name__ == "__main__":
    prompt_path = PROJECT/"content/prompts/homework/H1_custom.md"
    original = prompt_path.read_text(encoding="utf-8")
    variants = ["hw-20261027T061305277049", "hw-20261027T062447235735", "hw-20261027T062930617525", "hw-20261027T063508776921"]
    records = []
    try:
        for source in variants:
            records.append(homework(source))
            save("aes-reruns.json",records)
    finally:
        prompt_path.write_text(original,encoding="utf-8")
    result = agent()
    print("Agent finished",result["run_id"],result["status"],result.get("usage"),"duration",result.get("duration_ms"),flush=True)
    save("experiment-index.json", {"aes":records,"agent_run_id":result["run_id"]})

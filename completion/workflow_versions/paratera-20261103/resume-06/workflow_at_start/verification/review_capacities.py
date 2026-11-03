"""Post-run review: preserve original tool results and separately reject shrinkage."""

import argparse
import json
from pathlib import Path

from ai_eda.config import AGENT_RUNS_ROOT
from ai_eda.agent.subagents.modifier import validate_checkpoint_capacity
from verification.audit_fresh_run import manifest_sources


def review(run_id):
    root = AGENT_RUNS_ROOT / run_id
    selection = json.loads((root / "artifacts/candidate_selection.json").read_text())
    seed = "artifacts/rtl_manifest_resume.json" if (root / "artifacts/rtl_manifest_resume.json").exists() else "artifacts/rtl_manifest.json"
    initial = manifest_sources(root, seed)
    candidates = []
    for path in sorted(root.glob("tester-*/test_report.json")):
        attempt = int(path.parent.name.split("-")[1])
        current = manifest_sources(root, f"artifacts/tested_manifest_{attempt}.json")
        violations = []
        for name in initial.keys() & current.keys():
            try:
                validate_checkpoint_capacity(initial[name], current[name])
            except ValueError as error:
                violations.append({"path": name, "reason": str(error)})
        candidates.append({"report": str(path.relative_to(root)), "checkpoint_capacity_preserved": not violations,
                           "violations": violations})
    selected = next(item for item in candidates if item["report"] == selection["selected_report"])
    result = {"run_id": run_id, "selected_report": selection["selected_report"],
              "selected_capacity_preserved": selected["checkpoint_capacity_preserved"], "candidates": candidates,
              "scope": "Literal checkpoint array capacity relative to the generated seed. This review does not prove overall CPU correctness or general architectural capacities. Original test results are unchanged."}
    (root / "artifacts/capacity_review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("run_id")
    review(parser.parse_args().run_id)

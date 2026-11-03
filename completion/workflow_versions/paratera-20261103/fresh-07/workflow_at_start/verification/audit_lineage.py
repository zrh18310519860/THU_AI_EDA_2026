"""Prove an explicit resume chain descends from actual empty-project generation."""

import argparse
import contextlib
import io
import json
from pathlib import Path

from ai_eda.config import AGENT_RUNS_ROOT
from verification.audit_fresh_run import audit, audit_patches, manifest_sources


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def audit_lineage(selected, evidence, origin_evidence):
    chain, seen = [], set()
    run_id = selected
    while run_id:
        assert Path(run_id).name == run_id and run_id not in seen
        seen.add(run_id)
        root = AGENT_RUNS_ROOT / run_id
        record = read(root / "events.json")
        assert record["status"] in {"passed", "failed", "cancelled"}
        parameters = read(root / "model_parameters.json")
        chain.append((root, record, parameters))
        run_id = parameters.get("resumed_from")
    chain.reverse()
    evidence.mkdir(parents=True, exist_ok=True)
    root, _, parameters = chain[0]
    assert parameters["generation_mode"] == "fresh" and not parameters.get("resumed_from")
    # The fresh audit checks every initial answer and every accepted patch.
    with contextlib.redirect_stdout(io.StringIO()):
        initial = audit(root.name, origin_evidence)
    summaries = [{"run_id": root.name, "mode": "fresh", "status": initial["status"],
                  "model_generated_files": initial["model_generated_files"],
                  "accepted_patches_verified": initial["accepted_patches_verified"]}]
    for index, (root, record, parameters) in enumerate(chain[1:], 1):
        parent = chain[index - 1][0]
        assert parameters["generation_mode"] == "resumed"
        assert parameters["resumed_from"] == parent.name
        attempt = parameters.get("snapshot_attempt")
        assert isinstance(attempt, int), "An explicit immutable snapshot is required for lineage audit"
        before = manifest_sources(parent, f"artifacts/tested_manifest_{attempt}.json")
        seed = manifest_sources(root, "artifacts/rtl_manifest_resume.json")
        assert before == seed, "Resume seed differs from the declared parent snapshot"
        provenance = read(root / "artifacts/exploration_provenance.json")
        assert not provenance["builtin_rtl_allowed"]
        patches = audit_patches(root, record, initial_ref="artifacts/rtl_manifest_resume.json")
        summaries.append({"run_id": root.name, "mode": "resumed", "status": record["status"],
            "parent": parent.name, "parent_tested_attempt": attempt,
            "seed_matches_parent": True, "accepted_patches_verified": len(patches),
            "patch_checks": patches})
    selected_root = chain[-1][0]
    selection = read(selected_root / "artifacts/candidate_selection.json")
    attempt = int(Path(selection["selected_report"]).parent.name.split("-")[1])
    final = manifest_sources(selected_root, f"artifacts/tested_manifest_{attempt}.json")
    assert final == {f"rtl/{p.name}": p.read_text(encoding="utf-8")
                     for p in (selected_root / "project/rtl").glob("*.sv")}
    report = read(selected_root / selection["selected_report"])
    result = {"selected_run": selected, "selected_report": selection["selected_report"],
        "selected_mode": parameters["generation_mode"], "functional_passed": report["passed"],
        "ppa_passed": report.get("ppa_passed"), "empty_project_origin": chain[0][0].name,
        "chain": summaries, "manual_rtl_seed_used": False,
        "meaning": "Explicit resumed runs remain resumed. Initial RTL and every accepted patch are tied to actual model answers; every resume seed equals the declared immutable parent snapshot."}
    (evidence / "lineage-audit.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("selected")
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--origin-evidence", type=Path, required=True)
    args = parser.parse_args()
    audit_lineage(args.selected, args.evidence, args.origin_evidence)

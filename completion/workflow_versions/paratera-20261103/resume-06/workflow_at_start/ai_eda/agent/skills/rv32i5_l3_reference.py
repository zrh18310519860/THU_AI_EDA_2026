"""Explicit, hash-checked L3 starting point for incremental agent optimization."""

import hashlib
import json

from ai_eda.config import AGENT_CONTENT_ROOT

REFERENCE_ROOT = AGENT_CONTENT_ROOT / "skills" / "rv32i5_l3_reference"


def load_baseline():
    provenance = json.loads((REFERENCE_ROOT / "provenance.json").read_text(encoding="utf-8"))
    files = {}
    for path, expected in provenance["sha256"].items():
        source = REFERENCE_ROOT / path
        if REFERENCE_ROOT.resolve() not in source.resolve().parents:
            raise ValueError("Invalid baseline source path")
        text = source.read_text(encoding="utf-8")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != expected:
            raise ValueError("L3 baseline integrity failure: " + path)
        files[path] = text
    plan_path = REFERENCE_ROOT / "plan.json"
    if hashlib.sha256(plan_path.read_bytes()).hexdigest() != provenance["plan_sha256"]:
        raise ValueError("L3 baseline plan integrity failure")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if {m["file"] for m in plan["modules"]} != set(files):
        raise ValueError("L3 baseline plan/source mismatch")
    return plan, files, provenance

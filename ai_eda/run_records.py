"""Report fields shared by prompt experiments and Agent runs."""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from functools import wraps

from ai_eda.token_metrics import summarize_usage


PARAMETER_KEYS = ("model", "base_url", "temperature", "max_tokens", "thinking", "reasoning_effort")
CLOCK_SESSION_ID = uuid.uuid4().hex


def parameters(metadata: dict[str, Any]) -> dict[str, Any]:
    return {key: metadata.get(key) for key in PARAMETER_KEYS}


def prompt_summary(metadata: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    calls = metadata.get("model_calls")
    if not isinstance(calls, list):
        calls = [{"model_call_attempted": True, "usage": metadata.get("usage")}]
    usage = summarize_usage(calls)
    metrics = report.get("metrics") or {}
    return {
        **parameters(metadata),
        **usage,
        "usage": {key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
        "generation_duration_ms": metadata.get("generation_duration_ms"),
        "tb_duration_ms": metadata.get("tb_duration_ms"),
        "metrics_duration_ms": metadata.get("metrics_duration_ms"),
        "duration_ms": metadata.get("duration_to_tb_ms"),
        "duration_basis": "generation_start_to_first_tb_result",
        "generation_status": metadata.get("generation_status", "completed"),
        "error": metadata.get("error"),
        "area": metrics.get("area") if report.get("passed") is True else None,
        "area_unit": metrics.get("area_unit"),
        "total_power_w": metrics.get("total_power_w") if report.get("passed") is True and metrics.get("power_status") == "ok" else None,
    }


def record_verification(run_dir: Path, kind: str, started_clock: float, started_at: str, *, error: str | None = None) -> None:
    path = run_dir / "metadata.json"
    metadata = json.loads(path.read_text(encoding="utf-8"))
    finished_at = datetime.now(timezone.utc).isoformat()
    elapsed = round((time.perf_counter() - started_clock) * 1000, 1)
    entry = {"kind": kind, "started_at": started_at, "finished_at": finished_at, "duration_ms": elapsed}
    if error:
        entry["error"] = error
    metadata.setdefault("verification_history", []).append(entry)
    metadata[f"{kind}_duration_ms"] = elapsed
    if kind in {"tb", "all"} and not error and metadata.get("generation_started_at") and not metadata.get("first_tb_finished_at"):
        metadata["first_tb_finished_at"] = finished_at
        if metadata.get("generation_clock_session") == CLOCK_SESSION_ID and metadata.get("generation_started_counter") is not None:
            metadata["duration_to_tb_ms"] = round((time.perf_counter() - metadata["generation_started_counter"]) * 1000, 1)
            metadata["duration_clock"] = "monotonic"
        else:
            # Across service restarts only the idle gap requires calendar time.
            gap = (datetime.fromisoformat(started_at) - datetime.fromisoformat(metadata.get("generation_finished_at") or metadata["generation_started_at"])).total_seconds() * 1000
            metadata["duration_to_tb_ms"] = round((metadata.get("generation_duration_ms") or 0) + max(0, gap) + elapsed, 1)
            metadata["duration_clock"] = "measured_stages_plus_idle_gap"
    path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    save_prompt_summary(run_dir)


def save_prompt_summary(run_dir: Path) -> None:
    metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
    report_path = run_dir / "test_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else {}
    summary = {"run_id": run_dir.name, "passed": report.get("passed"), "stage": report.get("stage"), **prompt_summary(metadata, report)}
    (run_dir / "run_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def timed_verification(kind: str):
    def decorate(function):
        @wraps(function)
        def wrapped(run_dir: Path):
            started = time.perf_counter()
            started_at = datetime.now(timezone.utc).isoformat()
            error = None
            try:
                return function(run_dir)
            except Exception as exc:
                error = str(exc)
                raise
            finally:
                record_verification(run_dir, kind, started, started_at, error=error)
        return wrapped
    return decorate

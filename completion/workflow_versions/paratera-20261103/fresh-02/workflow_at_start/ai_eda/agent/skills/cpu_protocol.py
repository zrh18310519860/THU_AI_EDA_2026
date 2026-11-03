"""Deterministic protocol evidence for the RV32I external memory bus."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping


def _timing(top: str, prefix: str, ready: str) -> str:
    if re.search(rf"\b{prefix}_mem_resp_valid_r\b", top) or re.search(
        rf"assign\s+{prefix}_mem_resp_valid\s*=\s*{prefix}_mem_resp_valid_r\b", top
    ):
        return "registered"
    if re.search(
        rf"assign\s+{prefix}_mem_resp_valid\s*=\s*(?:{prefix}_mem_req_valid\s*&&\s*)?{ready}\b",
        top,
    ):
        return "same_cycle"
    return "unknown"


def audit_external_bus_protocol(
    files: Mapping[str, str], report: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Return bounded, source-based evidence without changing pass/fail."""

    top = str(files.get("rtl/rv32i5.sv", ""))
    icache = str(files.get("rtl/icache.sv", ""))
    dcache = str(files.get("rtl/dcache.sv", ""))
    findings: list[dict[str, str]] = []
    producer_timing = {
        "icache": _timing(top, "ic", "imem_ready"),
        "dcache": _timing(top, "dc", "dmem_ready"),
    }

    for name, timing in producer_timing.items():
        if timing == "registered":
            findings.append({
                "code": "bridge_delays_external_response",
                "severity": "error",
                "module": name,
                "evidence": "top-level response-valid is registered, but the course bus samples data on the acceptance edge",
            })
        elif timing == "unknown":
            findings.append({
                "code": "bridge_response_timing_unknown",
                "severity": "warning",
                "module": name,
                "evidence": "could not identify a same-cycle response-valid assignment in rv32i5.sv",
            })

    if re.search(r"assign\s+ic_mem_resp_valid\s*=\s*imem_ready\b", top):
        findings.append({
            "code": "bridge_response_not_gated_by_request",
            "severity": "warning",
            "module": "icache",
            "evidence": "ic_mem_resp_valid follows imem_ready without ic_mem_req_valid",
        })
    if re.search(r"assign\s+dc_mem_resp_valid\s*=\s*dmem_ready\b", top):
        findings.append({
            "code": "bridge_response_not_gated_by_request",
            "severity": "warning",
            "module": "dcache",
            "evidence": "dc_mem_resp_valid follows dmem_ready without dc_mem_req_valid",
        })

    for name, source in (("icache", icache), ("dcache", dcache)):
        if producer_timing[name] == "registered" and not re.search(
            r"\b(?:mem|refill|bus)_pending\b", source
        ):
            findings.append({
                "code": "cache_consumes_delayed_response_without_pending",
                "severity": "error",
                "module": name,
                "evidence": "registered response timing is used without a request-pending bit",
            })

    if re.search(r"if\s*\(refill_cnt\s*==\s*2'd3\).*?data3\s*\[\s*rf_idx\s*\]\s*<=\s*rf_w3", dcache, re.S):
        findings.append({
            "code": "dcache_last_refill_uses_old_word",
            "severity": "error",
            "module": "dcache",
            "evidence": "the fourth response is captured into rf_w3 and installed from rf_w3 in the same clocked block",
        })

    observed: list[str] = []
    if isinstance(report, Mapping):
        for check in report.get("checks") or []:
            detail = str(check.get("detail", "")) if isinstance(check, Mapping) else str(check)
            if "I-memory request changed before ready" in detail:
                observed.append("request_payload_changed_before_ready:icache")
            if "D-memory request changed before ready" in detail:
                observed.append("request_payload_changed_before_ready:dcache")
            if "mem_resp" in detail or "response" in detail.lower():
                observed.append(detail[:240])

    return {
        "contract": "same_cycle_external_read_acceptance",
        "producer_timing": producer_timing,
        "observed_checks": sorted(set(observed)),
        "findings": findings[:16],
    }


def audit_rob_tag_flow(
    files: Mapping[str, str], report: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Expose tag-loss evidence, never substitute for functional acceptance."""
    findings = []
    for path, source in sorted(files.items()):
        if "rename" not in path:
            continue
        clean = re.sub(r"/\*.*?\*/|//[^\n]*", " ", source, flags=re.S)
        match = re.search(r"\bdisp_rob_id\s*=\s*(?:\d+'[sS]?[dDhHbB]0+|0)\s*;", clean)
        # A default zero followed by a real assignment is not a constant output.
        assignments = re.findall(r"\bdisp_rob_id\s*(?:<=|=(?!=))\s*([^;]+);", clean)
        if match and len(assignments) == 1:
            findings.append({
                "code": "rename_hardcodes_rob_tag", "severity": "error",
                "module": Path(path).stem, "path": path,
                "evidence": match.group(0),
            })
    differential = report.get("differential", {}) if isinstance(report, Mapping) else {}
    signals = differential.get("startup_signals", []) if isinstance(differential, Mapping) else []
    observed = {}
    for item in signals:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("signal", "")).split("[", 1)[0]
        if name not in {"rob_alloc_id", "rob_comp_id", "issue_rob_id", "disp_rob_id"}:
            continue
        raw = item.get("known_values") or [pair[1] for pair in item.get("first_changes", [])
            if isinstance(pair, (list, tuple)) and len(pair) == 2]
        observed[name] = sorted({int(str(value), 2) for value in raw
                                 if re.fullmatch(r"[01]+", str(value))})
    alloc = observed.get("rob_alloc_id", [])
    issue = observed.get("issue_rob_id", []) or observed.get("disp_rob_id", [])
    complete = observed.get("rob_comp_id", [])
    if len(alloc) > 1 and len(issue) == 1 and len(complete) == 1:
        findings.append({
            "code": "rob_tag_collapses_before_completion", "severity": "error",
            "evidence": "ROB allocation IDs vary, but observed dispatch/issue and completion IDs remain constant",
        })
    return {"contract": "Each accepted instruction keeps its allocated ROB tag through completion",
            "findings": findings[:8], "observed": observed}


def audit_icache_refill_response(
    files: Mapping[str, str], report: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Detect returning the last refill word instead of the requested word."""
    source = str(files.get("rtl/icache.sv", ""))
    findings = []
    has_final_word = bool(re.search(r"refill_word\s*==\s*2'd3", source))
    returns_last_word = bool(re.search(r"resp_data_r\s*<=\s*mem_resp_data\s*;", source))
    remembers_word = bool(re.search(r"(?:refill_req_word|miss_word|req_word_q|request_word)", source))
    if has_final_word and returns_last_word and not remembers_word:
        findings.append({
            "code": "icache_miss_returns_last_refill_word",
            "severity": "error",
            "module": "icache",
            "evidence": "a line miss completes on refill_word==3, but cpu_resp_data is assigned directly from mem_resp_data without retaining the requested word index",
        })
    differential = report.get("differential", {}) if isinstance(report, Mapping) else {}
    if (isinstance(differential, Mapping) and differential.get("kind") == "retire_instruction"
            and differential.get("count") == 0 and differential.get("pc") == "0x0"):
        findings.append({
            "code": "first_retired_instruction_mismatch",
            "severity": "error",
            "module": "icache",
            "evidence": "the first instruction at PC zero differs from the loaded word; inspect refill selection, storage dimensions and response-to-PC pairing",
        })
    return {
        "contract": "A cache miss response must return the word selected by the original CPU request",
        "findings": findings[:8],
    }


def audit_cpu_structure(files: Mapping[str, str]) -> dict[str, Any]:
    """Bounded source-pattern evidence; never changes the acceptance decision."""
    findings = []

    def add(path, code, evidence):
        findings.append({"path": path, "module": Path(path).stem, "code": code,
                         "confidence": "source_pattern", "evidence": evidence})

    for path, source in sorted(files.items()):
        clean = re.sub(r"/\*.*?\*/|//[^\n]*", " ", source, flags=re.S)
        if Path(path).stem == "icache":
            for name, minimum in (("line_data", 2048), ("line_tag", 384)):
                match = re.search(r"\b(?:logic|reg)\s*\[\s*(\d+)\s*:\s*0\s*\]\s*" + name + r"\s*;", clean)
                if match and int(match[1]) + 1 < minimum:
                    add(path, "cache_storage_too_narrow", f"{match[0]} declares {int(match[1])+1} bits; the specified 16 lines require {minimum}. Dynamic selects can return X beyond the first line.")
            for name in ("req_tag", "refill_tag"):
                match = re.search(r"\b(?:logic|reg)\s*\[\s*(\d+)\s*:\s*0\s*\]\s*" + name + r"\s*;", clean)
                if match and int(match[1]) + 1 < 24:
                    add(path, "cache_tag_truncated", match[0] + "; the tag is address[31:8] and must retain all 24 bits, consistently with each line's stored tag.")
        if Path(path).stem == "load_store_unit":
            match = re.search(r"assign\s+issue_done\s*=\s*([^;]+);", clean)
            if match and "sel_done" in match[1] and "sel_valid" not in match[1]:
                add(path, "lsu_completion_not_valid_qualified", match[0] + " may report stale completion after the selected entry is invalidated; inspect the selected-entry valid and completion-consumption handshake.")
        if Path(path).stem == "rename_unit":
            match = re.search(r"ready_vec\s*<=\s*40'h([0-9a-fA-F_]+)", clean)
            if match and int(match[1].replace("_", ""), 16) & 0xffffffff != 0xffffffff:
                add(path, "initial_architectural_pr_not_ready", match[0] + "; PR0..PR31 must initially be ready and contain zero.")
            if re.search(r"free_head\s*<=\s*free_head\s*\+", clean) and not re.search(r"free_head\s*(?:==|>=|%)", clean):
                add(path, "free_list_cursor_wrap_unproven", "free_head increments without an identified capacity wrap; inspect the declared free_list bounds and simultaneous allocate/commit updates.")
            if re.search(r"if\s*\(spec_rat\[[^]]+\]\s*==\s*commit_prd", clean):
                add(path, "commit_map_uses_speculative_lookup", "committed RAT is updated by searching speculative RAT for commit_prd; a younger WAW mapping can hide the older instruction's architectural destination. Carry the ROB's rd/write-enable through commit.")
    return {"contract": "finite cache storage, live completions and precise register ownership",
            "findings": findings[:12], "acceptance_effect": "diagnostic_only"}


__all__ = ["audit_external_bus_protocol", "audit_rob_tag_flow", "audit_icache_refill_response", "audit_cpu_structure"]

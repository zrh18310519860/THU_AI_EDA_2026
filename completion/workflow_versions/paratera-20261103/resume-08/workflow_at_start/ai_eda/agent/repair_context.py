"""Bounded repair context, immutable source snapshots and module routing."""

import hashlib
import json
import re
from pathlib import Path

from ai_eda.agent.skills.rv32i5_contract import EXTERNAL_MEMORY_PROTOCOL


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def logic_fingerprint(files):
    """Ignore formatting/comments while retaining identifiers and literal tokens."""
    normalized = {}
    for path, source in sorted(files.items()):
        tokens = re.findall(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*|\\\S+|[A-Za-z_$][\w$]*|\d+|[^\s]',
                            source, flags=re.DOTALL)
        normalized[path] = [token for token in tokens if not token.startswith(("//", "/*"))]
    return digest(json.dumps(normalized, sort_keys=True))


def source_manifest(runtime, files, name):
    # Snapshot references must not point to the mutable project working copy.
    entries = []
    for path, text in sorted(files.items()):
        ref = runtime.write_text_artifact(f"sources/{digest(text)}/{Path(path).name}", text)
        entries.append({"path": path, "artifact": ref, "sha256": digest(text),
                        "bytes": len(text.encode("utf-8"))})
    return runtime.write_json_artifact(name, {"files": entries})


def read_sources(runtime, manifest_ref):
    manifest = runtime.read_json_artifact(manifest_ref)
    files = {}
    for entry in manifest["files"]:
        text = runtime.read_text_artifact(entry["artifact"])
        if entry.get("sha256") and digest(text) != entry["sha256"]:
            raise ValueError("RTL snapshot hash mismatch: " + entry["path"])
        files[entry["path"]] = text
    return files


def reserved_declarations(text):
    """Identify common keyword collisions; do not rewrite candidate source."""
    keywords = set("dist ref context expect priority unique unique0 rand randc sequence "
                   "property bins binsof coverpoint cross checker event type var virtual "
                   "interface program clocking constraint solve inside matches tagged "
                   "before with throughout within intersect first_match iff let local "
                   "protected pure extern import export return break continue assert "
                   "assume cover restrict final alias automatic static".split())
    clean = re.sub(r"/\*.*?\*/|//[^\n]*", lambda m: "\n" * m[0].count("\n"),
                   text, flags=re.DOTALL)
    pattern = (r"\b(function|task)\s+(?:(?:automatic|static)\s+)?"
               r"(?:(?:logic|bit|reg|integer|int|signed|unsigned|real|time|void)\s+)*"
               r"(?:\[[^\]\n]+\]\s*)?(\w+)\s*[(;]")
    findings = [{"kind": "reserved_declaration_identifier", "identifier": m[2],
             "line": clean.count("\n", 0, m.start()) + 1,
             "explanation": "This unescaped function/task name is a SystemVerilog keyword. "
                            "Changing argument or return declaration style cannot make it an identifier; "
                            "rename the declaration and its uses consistently."}
            for m in re.finditer(pattern, clean) if m[2] in keywords]
    declarations = r"\b(?:logic|wire|reg|integer|int|bit)\s+(?:(?:signed|unsigned)\s+)?(?:\[[^]\n]+\]\s*)?([A-Za-z_]\w*)\s*(?=[;=,\[])"
    for match in re.finditer(declarations, clean):
        if match[1] in keywords:
            findings.append({"kind": "reserved_declaration_identifier", "identifier": match[1],
                "line": clean.count("\n", 0, match.start()) + 1,
                "explanation": "This unescaped variable name is a SystemVerilog keyword. Rename its declaration and all uses consistently; changing block placement or declaration type cannot legalize the name."})
    return findings


def map_compile_diagnostics(report, files, combined_text):
    """Map generated-file compiler locations back to the immutable RTL snapshot."""
    if report.get("stage") != "compile":
        return []
    locations = {}
    current, source_line = None, 0
    for number, line in enumerate(combined_text.splitlines(), 1):
        if line.startswith("// file: ") and line[9:] in files:
            current, source_line = line[9:], 0
        elif current is not None:
            source_line += 1
            if source_line <= len(files[current].splitlines()):
                locations[number] = (current, source_line)
    mapped = []
    combined_name = Path(report.get("rtl_path", "")).name
    for line in str(report.get("stderr", "")).splitlines():
        match = re.match(r"^(.+?):(\d+):\s*(.*)$", line)
        if not match:
            continue
        filename, number, message = match.groups()
        location = locations.get(int(number)) if Path(filename).name == combined_name else None
        if location is None:
            continue
        path, original_line = location
        lines = files[path].splitlines()
        start, end = max(0, original_line - 5), min(len(lines), original_line + 5)
        mapped.append({"path": path, "line": original_line, "combined_line": int(number),
            "severity": "warning" if message.startswith("warning:") else "error",
            "message": message[:500],
            "source_excerpt": "\n".join(f"{i+1}: {lines[i]}" for i in range(start, end))[:2000],
            "lexical_findings": reserved_declarations(files[path])})
    return sorted(mapped, key=lambda item: item["severity"] == "warning")[:8]


def select_paths(packet, files, plan, limit=4):
    if packet.get("stage") == "compile":
        mapped_paths = list(dict.fromkeys(item["path"] for item in
            packet.get("compile_diagnostics", []) if item.get("path") in files))
        if mapped_paths:
            return mapped_paths[:limit]
    text = json.dumps(packet, ensure_ascii=False).lower()
    scores = {path: 0 for path in files}
    rules = [
        (r"cache|refill|mem_req|handshake", ("cache", "load_store", "frontend", "rv32i5")),
        (r"store|load|lane|dmem|memory", ("load_store", "dcache", "execute", "rv32i5")),
        (r"predict|branch|jal|redirect", ("branch_predictor", "frontend", "execute", "reorder", "rename")),
        (r"retire|halt|ecall|commit", ("reorder", "rv32i5", "frontend", "rename")),
        (r"ooo|raw|waw|war|register|reg_", ("rename", "issue", "reorder", "execute")),
    ]
    for path in files:
        stem = Path(path).stem
        if stem in text or path.lower() in text:
            scores[path] += 12
        if path in packet.get("differential", {}).get("candidate_paths", []):
            scores[path] += 50
        if stem in [h.get("module") for h in packet.get("metrics", {}).get("memory_mapping_hotspots", [])[:2]]:
            scores[path] += 100
        for pattern, names in rules:
            if re.search(pattern, text) and any(name in stem for name in names):
                scores[path] += 2
    if packet.get("objective") == "optimize":
        areas = {name.lstrip("\\"): value for name, value in packet.get("metrics", {}).get("area_by_module", {}).items()}
        ranked = sorted(areas, key=areas.get, reverse=True)
        for path in files:
            if Path(path).stem in ranked[:4]:
                scores[path] += 80 - 10 * ranked.index(Path(path).stem)
    for finding in packet.get("structural_audit", {}).get("findings", []):
        path = finding.get("path")
        if path in scores:
            scores[path] += 70 if finding.get("code") == "cache_storage_too_narrow" else 35
    causal = packet.get("causal_hints") or {}
    if causal.get("earliest_observable_stage") == "allocation_or_dispatch_blocked":
        return [path for path in causal.get("candidate_paths", []) if path in files][:limit]
    if causal.get("earliest_observable_stage") == "instruction_pc_pairing_or_recovery":
        return [path for path in causal.get("candidate_paths", []) if path in files][:limit]
    if causal.get("earliest_observable_stage") == "icache_refill_response_word_select":
        preferred = [path for path in causal.get("candidate_paths", []) if path in files]
        if preferred:
            return preferred[:limit]
    if causal.get("earliest_observable_stage") == "rob_tag_propagation_after_dispatch":
        preferred = [path for path in causal.get("candidate_paths", []) if path in files]
        if preferred:
            return preferred[:limit]
    if causal.get("earliest_observable_stage") == "frontend_or_icache_before_data_memory":
        preferred = []
        for path in files:
            stem = Path(path).stem
            if stem in {"icache", "frontend_unit", "fetch_unit", "rv32i5", "branch_predictor"}:
                scores[path] += 40
                preferred.append(path)
            if stem in {"dcache", "load_store_unit"}:
                scores[path] -= 40
        # Do not let generic words such as "register" or "memory" pull a
        # downstream rename/LSU module into the first causal review slice.
        if preferred:
            ordered_preferred = sorted(preferred, key=lambda path: (-scores[path], path))
            return ordered_preferred[:limit]
    ordered = sorted(files, key=lambda path: (-scores[path], path))
    return ordered[:limit]


def source_windows(text, keywords, limit=16000):
    if len(text) <= limit:
        return text
    lines = text.splitlines(keepends=True)
    selected = set(range(min(60, len(lines))))
    words = set(re.findall(r"\b[a-zA-Z_]\w{3,}\b", keywords))
    for index, line in enumerate(lines):
        if any(re.search(r"\b" + re.escape(word) + r"\b", line) for word in words):
            selected.update(range(max(0, index-8), min(len(lines), index+16)))
    chunks, length, previous = [], 0, -2
    for index in sorted(selected):
        prefix = "\n// ... omitted source ...\n" if index != previous+1 else ""
        chunk = prefix + lines[index]
        if length + len(chunk) > limit:
            break
        chunks.append(chunk)
        length += len(chunk)
        previous = index
    return "".join(chunks)


def spec_context(spec, paths, project_id, limit=20000):
    if not project_id.startswith("rv32i5"):
        return spec[:limit]
    sections = re.split(r"(?=^## [A-E]\.)", spec, flags=re.MULTILINE)
    selected = []
    isa = re.search(r"^## 5\..*?(?=^## 7\.)", spec, flags=re.MULTILINE | re.DOTALL)
    names = " ".join(paths)
    if isa and any(s in names for s in ("decode", "execute", "load_store", "rv32i5.sv")):
        selected.append(isa.group(0))
    for part in sections:
        title = part[:12]
        if title.startswith("## A."):
            selected.append(part)
        elif title.startswith("## D.") and any(s in names for s in ("rename", "issue", "reorder", "load_store", "frontend", "rv32i5.sv")):
            selected.append(part)
        elif title.startswith("## B.") and ("cache" in names or "load_store" in names):
            selected.append(part)
        elif title.startswith("## C.") and any(x in names for x in ("predict", "frontend", "execute")):
            selected.append(part)
    # Preserve the advanced priority contract first, not the superseded L0 prose.
    return "\n".join(selected)[:limit] or spec[:limit]


def repair_view(spec, plan, files, packet, paths, project_id, *, diagnose=False):
    sources = {}
    complete = {}
    remaining = 96000
    for index, path in enumerate(paths):
        # A complete selected module is useful context, unlike a full test log.
        # Reserve a fair minimum for later modules without dropping the top's tail.
        available = max(0, remaining - 4000 * (len(paths) - index - 1))
        limit = min(64000, available)
        sources[path] = source_windows(files[path], json.dumps(packet), limit)
        complete[path] = len(files[path]) <= limit
        remaining -= len(sources[path])
    compile_only = packet.get("stage") == "compile"
    return {
        "diagnostics": packet,
        "interfaces": [{"name": m["name"], "file": m["file"], "interfaces": m["interfaces"],
                        "protocols": m.get("protocols", "")}
                       for m in plan["modules"] if not compile_only or m["file"] in paths],
        "source_sha256": {p: digest(files[p]) for p in paths},
        "source_catalog": [{"path": p, "characters": len(text), "sha256": digest(text)}
                           for p, text in files.items()],
        "editable_sources": sources,
        "source_complete": complete,
        "source_policy": "Selected source only, at most 96k characters total and 64k per file. Missing text in an excerpt is NOT evidence of an undriven signal. Never add another driver without checking all existing assignments.",
        "interface_policy": "Teacher top ports are fixed. Planner internal headers are original design intent, not proof of correctness. If an internal contract is incomplete, coordinate producer, consumer and every instantiation in the same patch. The current RTL is authoritative for exact anchors.",
        "authoritative_external_memory_protocol": EXTERNAL_MEMORY_PROTOCOL,
        "requirements": "Preserve existing behavior and interfaces; resolve current compiler errors first." if compile_only else spec_context(spec, paths, project_id),
        "plan_notes": "" if compile_only else plan.get("notes", ""),
        "protocols": "" if compile_only else plan.get("protocols", ""),
    }

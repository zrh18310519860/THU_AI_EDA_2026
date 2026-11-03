"""Isolate existing teacher tasks and observe candidate signals without changing tests."""

import bisect
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEACHER = ROOT / "content/tasks/homework_rv32i5"
INCLUDES = ("setup", "assembler", "harness", "base_alu", "base_memory",
            "base_control", "dft", "extensions", "benchmarks")


def teacher_tasks():
    tasks = {}
    for name in INCLUDES:
        source = (TEACHER / f"tb/{name}.svh").read_text(encoding="utf-8")
        for match in re.finditer(r"\btask\s+automatic\s+(\w+)\s*\([^;]*;.*?\bendtask\b",
                                 source, re.DOTALL):
            tasks[match[1]] = match[0]
    return tasks


def stimulus_source(name, tasks):
    selected = [name]
    for task in selected:
        for child in re.findall(r"\b(build_\w+)\s*\(", tasks[task]):
            if child in tasks and child not in selected:
                selected.append(child)
    return "\n\n".join(tasks[task] for task in reversed(selected))[:16000]


def load_wave(path):
    from vcdvcd import VCDVCD
    path = Path(path)
    if path.is_file() and path.stat().st_size:
        with path.open("rb") as stream:
            stream.seek(-1, 2)
            incomplete = stream.read(1) != b"\n"
        if incomplete:
            text = path.read_text(encoding="utf-8")
            complete = text.rsplit("\n", 1)[0] + "\n"
            return VCDVCD(vcd_string=complete), True
    return VCDVCD(str(path)), False


def operand_lifetime(rows, names, mismatch):
    """Select observed physical-source history without assuming a repair cause."""
    if not mismatch or mismatch.get("vcd_time") is None:
        return {}
    end = mismatch["vcd_time"]
    dispatches = []
    for row in rows:
        if row[0] > end or row[1] != "dispatch":
            continue
        values = dict(zip(names["dispatch"], row[2:]))
        pcs = [value for key, value in values.items() if key.endswith("_pc")]
        instructions = [value for key, value in values.items() if key.endswith("_insn")]
        accepted = any(value == "0x1" and re.search(r"(?:fire|accept)$", key)
                       for key, value in values.items())
        if accepted and mismatch.get("pc") in pcs and mismatch.get("insn") in instructions:
            dispatches.append((row[0], values))
    if not dispatches:
        return {"note": "No accepted dispatch with the mismatch PC/instruction was observed."}
    start, target = dispatches[-1]
    slots = {str(slot) for slot in (1, 2) if "source_registers" not in mismatch
             or mismatch.get("rs" + str(slot)) in mismatch["source_registers"]}
    physical = {value for key, value in target.items()
                if (match := re.search(r"_(?:alloc|disp)_ps([12])$", key))
                and match[1] in slots and value not in {None, "0x0"}}
    if not physical:
        return {"target_dispatch_time": start, "target_dispatch": target,
                "note": "Physical source IDs could not be identified by port names."}
    related = []
    for row in rows:
        time, group = row[:2]
        if time > end or group not in {"dispatch", "issue", "writeback", "retire", "recovery"}:
            continue
        values = dict(zip(names[group], row[2:]))
        matches = any(value in physical and re.search(r"_(?:p[dr]|prd|pd|old_prd|old_pd|ps[12])$", key)
                      for key, value in values.items())
        recovery = group == "recovery" and any(value == "0x1" and
            re.search(r"(?:redirect_valid|squash_valid|squash_pulse)$", key)
            for key, value in values.items())
        if not (matches or recovery):
            continue
        # Keep identifying payloads and enables; omit inactive independent WB lanes.
        compact = {}
        for key, value in values.items():
            base = re.sub(r"_(?:data|prd|pd|rob_id|we)$", "", key)
            if group == "writeback" and values.get(base + "_valid") == "0x0":
                continue
            if re.search(r"(?:valid|fire|ready|accept|pc|insn|data|ps[12]|prd|pd|rob_id|writes_rd|we)$", key):
                compact[key] = value
        related.append({"time": time, "group": group, "signals": compact})
    return {"target_dispatch_time": start, "target_dispatch": target,
            "physical_source_ids": sorted(physical), "events": related[-48:],
            "omitted_related_events": max(0, len(related) - 48),
            "interpretation": "Observed events involving these physical IDs, including reuse and recovery. IDs alone do not establish live ownership; inspect valid/accept and ROB age in CURRENT RTL."}


def wave_events(path, prefix="tb_teacher_probe.", focus_time=None, mismatch=None):
    wave, ignored_tail = load_wave(path)
    refs = {}
    for reference in wave.references_to_ids:
        if not reference.startswith(prefix):
            continue
        short = reference[len(prefix):]
        if short.startswith("dut."):
            short = short[4:]
        leaf = short.rsplit(".", 1)[-1].split("[")[0]
        if "." not in short:
            refs[short.split("[")[0]] = reference
        elif (short.count(".") <= 2 and (leaf in {
                  "v0", "v1", "issued0", "issued1", "rid0", "rid1",
                  "head_v", "head_s", "head_issued", "head_rid",
                  "head_src_ready", "head_can_issue", "req_accept",
                  "resp_consume", "state", "refill_cnt", "req_index_q",
                  "req_word_q", "req_tag_q", "cpu_req_valid", "cpu_req_ready",
                  "cpu_resp_valid", "cpu_resp_rdata", "mem_req_valid",
                  "mem_req_ready", "mem_resp_valid", "mem_resp_data",
                  "complete_valid", "complete_rob_id", "complete_data",
                  "valid_q", "head_q", "tail_q", "count_q", "free_head",
                  "free_count", "free_avail", "can_disp", "free_pr",
                  "issue_valid", "issue_ready", "issue_rob_id", "accept",
                  "disp_valid", "disp_ready", "disp_rob_id", "head_pd",
                  "head_ps1", "head_ps2", "flush_epoch", "req_epoch_q",
                  "resp_epoch_q", "head_valid", "head_complete", "do_commit"
              } or re.search(r"^(?:dispatch|alloc|commit|wb|issue|req|resp|head|sel|store_done|lsu_req|lsu_resp|prf_rs[12])(?:_.*)?$", leaf))):
            refs[short.split("[")[0]] = reference
    series = {name: wave[reference].tv for name, reference in refs.items()}
    times = {name: [time for time, _ in values] for name, values in series.items()}

    def signal_key(name):
        if name in series:
            return name
        matches = [key for key in series if key.endswith("." + name)]
        return matches[0] if len(matches) == 1 else None

    def value(name, time):
        key = signal_key(name)
        index = bisect.bisect_right(times.get(key, []), time) - 1 if key else -1
        if index < 0:
            return None
        raw = series[key][index][1]
        return hex(int(raw, 2)) if re.fullmatch("[01]+", raw) else raw

    groups = {
        "retire": (r"(?:^|_)(?:retire|commit)_(?:valid|pc|insn|data|arch_rd|rd|pd|prd|old_pd|old_prd|rob_id|id|is_ecall|writes_rd|reg_valid)$",),
        "writeback": (r"(?:^|_)wb_(?:valid|pd|prd|data|has_dest|we|rob_id)$", r"(?:^|_)(?:lsu_done|complete)_(?:valid|rob_id|data|pd|prd|has_dest)$", r"(?:^|_)store_done(?:_rob_id)?$"),
        "dispatch": (r"^(?:disp_fire|dispatch_fire|disp_accept|disp_valid|disp_ready)$", r"(?:^|_)disp_(?:pd|old_pd|ps1|ps2|rob_id)$",
                     r"(?:^|_)(?:dispatch|alloc)_(?:valid|ready|fire|pc|insn|rs1|rs2|rd|writes_rd|prd|ps1|ps2|old_prd|rob_id|is_store|is_load)$",
                     r"^(?:dec|fe_fetch)_(?:pc|insn|valid|rd|writes_rd)$"),
        "memory": (r"^lsu_(?:issue_(?:valid|rob_id|is_load|is_store)|head_(?:valid|rob_id|is_store)|prf_data[12])$",
                   r"^(?:lsu_mem_req|lsu_cpu_req|dc_cpu_req|dmem)_(?:valid|ready|we|addr|wdata|be)$",
                   r"^dc_cpu_resp_(?:valid|rdata)$",
                   r"(?:^|_)(?:lsu_req|lsu_resp|dc_cpu_req|dc_cpu_resp)_(?:valid|ready|we|addr|wdata|rdata|be)$",
                   r"(?:^|_)lsu_prf_rs[12]_(?:addr|data)$"),
        "recovery": (r"(?:^|_)(?:squash_valid|squash_rob_id|squash_pulse|redirect_valid|redirect_pc|halt_pending|halted)$",),
        "issue": (r"(?:^|_)issue_(?:valid|ready|accept|rob_id|pc|insn|op1|op2|pd|prd|rs1_data|rs2_data)$",),
    }
    names = {group: sorted(name for name in refs if "." not in name and
                          any(re.search(pattern, name) for pattern in patterns))
             for group, patterns in groups.items()}
    for parent in sorted({name.rsplit(".", 1)[0] for name in refs if "." in name}):
        names["state:" + parent] = sorted(name for name in refs
                                          if name.rsplit(".", 1)[0] == parent)
    seen = set()
    for group, fields in names.items():
        unique = []
        for name in fields:
            identifier = wave.references_to_ids[refs[name]]
            if identifier not in seen:
                seen.add(identifier)
                unique.append(name)
        names[group] = unique
    triggers = {group: [name for name in fields
                       if name.rsplit(".", 1)[-1].endswith("valid") or
                       name.rsplit(".", 1)[-1] in
                       {"disp_fire", "dispatch_fire", "disp_accept", "squash_pulse",
                        "state", "req_accept", "resp_consume", "complete_valid"} or
                       re.search(r"(?:_fire|_accept|_done|_pulse)$", name)]
                for group, fields in names.items()}
    bus_fields = ("imem_valid", "imem_ready", "imem_addr", "dmem_valid", "dmem_ready",
                  "dmem_addr", "dmem_we", "dmem_wdata", "dmem_be", "halted")
    watched = set(bus_fields) | {"rst_n", "memory_wait", "dbg_retire_valid", "dbg_retire_pc", "dbg_retire_insn"}
    watched.update(name for fields in names.values() for name in fields)
    watched_keys = {key for name in watched if (key := signal_key(name)) is not None}
    change_times = sorted({time for key in watched_keys for time, _ in series[key]})
    last_change_index, repeat_active = -1, False
    rows = []
    previous_halt = None
    previous = {}
    segments = []
    reset_seen = True
    bus_history, violations = [], []
    retirement_flow = []
    previous_retire = None
    segment_has_anomaly = False
    for time, clock in series.get("clk", []):
        if clock != "1" or value("rst_n", time - 1) != "0x1":
            if clock == "1":
                bus_history = []
                previous = {}
                previous_halt = None
                reset_seen = True
                previous_retire = None
                segment_has_anomaly = False
                last_change_index, repeat_active = -1, False
            continue
        change_index = bisect.bisect_left(change_times, time)
        if change_index == last_change_index and not repeat_active:
            continue
        last_change_index, repeat_active = change_index, False
        if reset_seen:
            segments.append({"start": time, "memory_wait": value("memory_wait", time - 1)})
            reset_seen = False
        if value("dbg_retire_valid", time - 1) == "0x1":
            current = {"time": time, "pc": value("dbg_retire_pc", time - 1),
                       "insn": value("dbg_retire_insn", time - 1)}
            if previous_retire and not segment_has_anomaly:
                try:
                    prior_pc = int(previous_retire["pc"], 16)
                    prior_insn = int(previous_retire["insn"], 16)
                    expected = (prior_pc + 4) & 0xffffffff
                    if (prior_insn & 127 not in (0x63, 0x67, 0x6f, 0x73)
                            and int(current["pc"], 16) != expected):
                        retirement_flow.append({"kind": "noncontrol_retirement_pc_gap",
                            "previous": previous_retire, "current": current,
                            "expected_next_pc": hex(expected),
                            "observation": "Consecutive public retirement records skip or repeat a PC after a non-control instruction. This is an architectural symptom; inspect fetch, dispatch and ROB lifecycle before downstream final-state errors."})
                        segment_has_anomaly = True
                except (TypeError, ValueError):
                    pass
            previous_retire = current
        bus = {name: value(name, time - 1) for name in bus_fields}
        bus["time"] = time
        if bus_history:
            for kind in ("imem", "dmem"):
                before = bus_history[-1]
                if before[kind + "_valid"] == "0x1" and before[kind + "_ready"] == "0x0":
                    fields = [kind + "_addr"]
                    if kind == "dmem":
                        fields += ["dmem_we"]
                        if before["dmem_we"] == "0x1":
                            fields += ["dmem_wdata", "dmem_be"]
                    if bus[kind + "_valid"] != "0x1" or any(bus[name] != before[name] for name in fields):
                        if sum(item["bus"] == kind for item in violations) < 2:
                            violations.append({"bus": kind, "observation": "Request withdrawn or payload changed after a stalled request edge.",
                                               "window": bus_history[-4:] + [bus]})
        bus_history.append(bus)
        for group, fields in names.items():
            values = {name: value(name, time - 1) for name in fields}
            if group == "recovery":
                halt = (values.get("halted"), values.get("halt_pending"))
                active = halt != previous_halt or any(values[name] == "0x1" for name in fields if "redirect_valid" in name or "squash" in name)
                previous_halt = halt
            elif group.startswith("state:"):
                active = True
            else:
                active = any(values[name] == "0x1" for name in triggers[group])
            if group in {"retire", "writeback"} and active:
                repeat_active = True
            payload = [values[name] for name in fields]
            if active:
                if payload != previous.get(group) or group in {"retire", "writeback"}:
                    rows.append([time, group, *payload])
            previous[group] = payload
    # Allocate a window to every reset segment. A later failing wait mode must
    # not disappear behind an earlier passing run or a long idle tail.
    visible = []
    for index, segment in enumerate(segments):
        end = segments[index + 1]["start"] if index + 1 < len(segments) else float("inf")
        part = [row for row in rows if segment["start"] <= row[0] < end]
        anomalies = [item for item in retirement_flow
                     if segment["start"] <= item["current"]["time"] < end]
        if len(part) <= 180:
            visible.extend(part)
        elif focus_time is not None:
            before = [i for i, row in enumerate(part) if row[0] <= focus_time]
            semantic = [i for i in before if part[i][1] in
                        {"retire", "writeback", "dispatch", "issue", "recovery"}][-80:]
            nearest = sorted(range(len(part)), key=lambda i: (abs(part[i][0] - focus_time), i))[:80]
            indices = set(semantic) | set(nearest)
            visible.extend(part[i] for i in sorted(indices))
        elif anomalies:
            center = focus_time if focus_time is not None else anomalies[0]["current"]["time"]
            nearest = sorted(range(len(part)), key=lambda i: (abs(part[i][0] - center), i))[:96]
            indices = set(range(42)) | set(range(len(part) - 42, len(part))) | set(nearest)
            visible.extend(part[i] for i in sorted(indices))
        else:
            # Preserve early architectural events before spending the window on
            # incidental internal state transitions from every instantiated unit.
            semantic = [i for i, row in enumerate(part) if row[1] in {"retire", "writeback", "issue", "memory"}][:100]
            indices = set(range(40)) | set(range(len(part) - 40, len(part))) | set(semantic)
            visible.extend(part[i] for i in sorted(indices))
    return {"sampling": "DUT values immediately BEFORE each rising edge; time unit is VCD ticks. Read-only observation, no forced signals.",
            "columns": {group: ["time", "group", *fields] for group, fields in names.items()},
            "segments": segments, "events": visible,
            "omitted_events": len(rows) - len(visible), "bus_violations": violations,
            "retirement_flow": retirement_flow, "incomplete_last_record_ignored": ignored_tail,
            "operand_lifetime": operand_lifetime(rows, names, mismatch)}


def compare_retired_results(observations, checks):
    """Separate a wrong computed value from a value lost after retirement."""
    columns = observations.get("columns", {}).get("retire", [])
    last = {}
    for row in observations.get("events", []):
        if row[1] != "retire":
            continue
        values = dict(zip(columns, row))
        def field(suffix):
            return next((value for name, value in values.items() if name.endswith(suffix)), None)
        try:
            insn = int(field("retire_insn"), 16)
            rd = (insn >> 7) & 31
            # Only instructions which architecturally write rd.
            if rd and insn & 127 in (3, 0x13, 0x17, 0x33, 0x37, 0x67, 0x6f):
                data = field("retire_data")
                if data is not None:
                    last[rd] = {"time": row[0], "pc": field("retire_pc"), "retired_data": data,
                                "physical_destination": field("retire_pd")}
        except (TypeError, ValueError):
            continue
    findings = []
    for line in checks:
        match = re.search(r"\bx(\d+)\b.*?0x([0-9a-fA-F]{8}).*?0x([0-9a-fA-F]{8})", line)
        if match and int(match[1]) in last:
            observed = last[int(match[1])]
            try:
                if int(observed["retired_data"], 16) == int(match[2], 16) != int(match[3], 16):
                    findings.append({"register": "x" + match[1], **observed,
                        "final_debug_value": "0x" + match[3],
                        "observation": "The last observed retired result agrees with the teacher expectation, but the later architectural debug read differs. Computation and final committed-state retention must be distinguished."})
            except ValueError:
                continue
    return findings


def run_smoke_probe(sources, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    wrapper = out / "probe.sv"
    wrapper.write_text('module tb_teacher_probe; tb_l3_smoke child(); initial begin\n'
        f'$dumpfile("{out / "signals.vcd"}"); $dumpvars(2,child); $dumpvars(2,child.dut);\n'
        'end endmodule\n', encoding="utf-8")
    compiled = subprocess.run(["iverilog", "-g2012", "-DRV32I_FEATURE_PORTS", "-I", str(TEACHER),
        "-s", "tb_teacher_probe", "-o", str(out / "probe.vvp"), str(wrapper),
        str(ROOT / "verification/l3_smoke_tb.sv"), *map(str, sources)], capture_output=True, text=True, timeout=30)
    (out / "compile.log").write_text(compiled.stdout + compiled.stderr)
    result = {"testcase": "l3_smoke", "stimulus_source": (ROOT / "verification/l3_smoke_tb.sv").read_text(),
              "provenance": "Read-only VCD of the exact smoke program and both wait modes; not the independent DFT program."}
    if compiled.returncode:
        result.update(available=False, reason=compiled.stderr[-1200:])
    else:
        try:
            ran = subprocess.run(["vvp", "-i", str(out / "probe.vvp")], capture_output=True, text=True, timeout=15)
            output = ran.stdout + ran.stderr
            result["available"] = True
        except subprocess.TimeoutExpired as error:
            output = error.stdout or b""
            if isinstance(output, bytes):
                output = output.decode("utf-8", "replace")
            result.update(available=True, timed_out=True)
        (out / "probe.log").write_text(output, encoding="utf-8")
        result["checks"] = [line for line in output.splitlines() if line.startswith("CHECK:")][:12]
        try:
            result["observations"] = wave_events(out / "signals.vcd", "tb_teacher_probe.child.")
            result["retirement_vs_final_state"] = compare_retired_results(result["observations"], result["checks"])
        except (ImportError, OSError, ValueError) as error:
            result["observation_error"] = str(error)
    (out / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def run_failure_probes(sources, checks, out, limit=2):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = teacher_tasks()
    selected = []
    for check in checks:
        name = str(check.get("name", ""))
        task = "test_" + name
        if task not in tasks:
            task = next((key for key, body in tasks.items()
                         if re.search(r'begin_test\("' + re.escape(name) + r'"\)', body)), task)
        if task in tasks and task not in selected:
            selected.append(task)
        if len(selected) == limit:
            break
    results = []
    for task in selected:
        case = out / task
        case.mkdir(exist_ok=True)
        source = case / "probe.sv"
        source.write_text('`timescale 1ns/1ps\nmodule tb_teacher_probe;\n' +
            "\n".join(f'`include "tb/{name}.svh"' for name in INCLUDES) +
            '\ninteger probe_byte;\n'
            'always @(posedge rst_n) begin\n'
            ' $display("PROBE_RESET|");\n'
            ' for(probe_byte=0;probe_byte<DMEM_BYTES;probe_byte=probe_byte+1)\n'
            '  if(dmem[probe_byte] !== 8\'ha5) $display("PROBE_MEM|%h|%h",probe_byte,dmem[probe_byte]);\n'
            'end\n'
            'always @(posedge clk) if(rst_n && dbg_retire_valid)\n'
            ' $display("PROBE_RET|%08h|%08h|00000000|%08h|%016h",dbg_retire_pc,dbg_retire_insn,imem[dbg_retire_pc[11:2]],$time);\n'
            '\ninitial begin\n cur_test="probe"; dbg_reg_addr=0;\n'
            f' $dumpfile("{case / "signals.vcd"}"); $dumpvars(2,tb_teacher_probe); $dumpvars(2,dut);\n'
            f' #1; detect_capabilities(); {task}();\n'
            ' $display("PROBE_RESULT|failures=%0d|checks=%0d",failures,checks_run); $finish;\nend\nendmodule\n', encoding="utf-8")
        compiled = subprocess.run(["iverilog", "-g2012", "-DRV32I_FEATURE_PORTS", "-I", str(TEACHER),
            "-s", "tb_teacher_probe", "-o", str(case / "probe.vvp"), str(source), *map(str, sources)],
            capture_output=True, text=True, timeout=30)
        (case / "compile.log").write_text(compiled.stdout + compiled.stderr)
        names = re.findall(r'begin_test\("([^"]+)"\)', tasks[task])
        result = {"testcase": next((str(item.get("name")) for item in checks
                                     if str(item.get("name")) in names), task[5:]),
                  "invoked_teacher_task": task, "stimulus_source": stimulus_source(task, tasks),
                  "provenance": "Calls the unchanged teacher task in isolation; diagnostic only. Full teacher TB remains the acceptance gate."}
        if compiled.returncode:
            result.update(available=False, reason=compiled.stderr[-1200:])
        else:
            try:
                ran = subprocess.run(["vvp", "-i", str(case / "probe.vvp")], capture_output=True,
                                     text=True, timeout=15)
                output = ran.stdout + ran.stderr
                result["available"] = True
            except subprocess.TimeoutExpired as error:
                output = error.stdout or b""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", "replace")
                result.update(available=True, timed_out=True)
            (case / "probe.log").write_text(output, encoding="utf-8")
            result["checks"] = [line for line in output.splitlines() if line.startswith(("CHECK:", "PROBE_RESULT|"))][:12]
            try:
                control = check_program_control(output)
                result["program_control_differential"] = control
                focus = control.get("previous_instruction") or {}
                result["observations"] = wave_events(case / "signals.vcd",
                    focus_time=focus.get("vcd_time"), mismatch=focus)
                result["retirement_vs_final_state"] = compare_retired_results(result["observations"], result["checks"])
            except (ImportError, OSError, ValueError) as error:
                result["observation_error"] = str(error)
        (case / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        results.append(result)
    return results


def check_program_control(output):
    """Compare the isolated teacher program's PC stream without driving debug ports."""
    from verification.diff_cpu import check
    segments = []
    for section in output.split("PROBE_RESET|\n")[1:]:
        memory = bytearray([0xa5] * 4096)
        retired = []
        for line in section.splitlines():
            if line.startswith("PROBE_MEM|"):
                _, address, value = line.split("|")
                try:
                    memory[int(address, 16)] = int(value, 16)
                except (ValueError, IndexError):
                    return {"available": False, "reason": "Unknown/out-of-range initial diagnostic memory byte"}
            elif line.startswith("PROBE_RET|"):
                retired.append("RET|" + line.split("|", 1)[1])
        if retired:
            try:
                result = check("\n".join(retired), initial_memory=memory, compare_registers=False)
            except (ValueError, KeyError) as error:
                return {"available": False, "reason": "Instruction outside diagnostic ISA subset: " + str(error)}
            segments.append(result)
            if result["kind"] not in {"ecall", "no_ecall"}:
                return {**result, "available": True, "segment": len(segments),
                    "scope": "Exact isolated teacher task and its observed initial memory. Only PC/instruction flow is compared; register output values are not sampled or checked."}
    if not segments:
        return {"available": False, "reason": "No public retirement records observed"}
    return {**segments[-1], "available": True, "segment": len(segments),
            "scope": "Only PC/instruction flow compared against a sequential ISA model initialized from this teacher invocation."}

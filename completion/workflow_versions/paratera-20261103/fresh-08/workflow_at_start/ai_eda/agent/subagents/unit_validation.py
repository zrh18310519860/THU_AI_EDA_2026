"""Model-written, advisory unit tests before full CPU integration."""

import json
import re
import subprocess
from pathlib import Path

from ai_eda.agent.repair_context import digest, reserved_declarations
from ai_eda.rtl.runner import extract_rtl
from .common import extract_json_payload


def run_unit(directory, testbench, sources):
    directory.mkdir(parents=True, exist_ok=True)
    tb = directory / "tb_unit.sv"
    tb.write_text(testbench, encoding="utf-8")
    paths = []
    for index, text in enumerate(sources.values()):
        path = directory / f"source-{index}.sv"
        path.write_text(text, encoding="utf-8")
        paths.append(str(path))
    compiled = subprocess.run(["iverilog", "-g2012", "-DRV32I_FEATURE_PORTS", "-s", "tb_unit",
        "-o", str(directory / "unit.vvp"), str(tb), *paths], capture_output=True, text=True, timeout=20)
    log = compiled.stdout + compiled.stderr
    if compiled.returncode:
        result = {"compiled": False, "passed": False, "log": log[-5000:]}
    else:
        try:
            simulation = subprocess.run(["vvp", "-i", str(directory / "unit.vvp")],
                capture_output=True, text=True, timeout=3)
            log += simulation.stdout + simulation.stderr
            result = {"compiled": True, "passed": simulation.returncode == 0 and "UNIT_PASS" in simulation.stdout,
                      "log": log[-6000:]}
        except subprocess.TimeoutExpired:
            result = {"compiled": True, "passed": False,
                      "log": "Unit simulation wall-time timeout: consider zero-time combinational feedback or a missing watchdog."}
    (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def validate_generated_module(runtime, event, module, plan, files, specification, origin):
    directory = runtime.run_dir / f"step-{event['step']:02d}-coder/unit"
    directory.mkdir(parents=True, exist_ok=True)
    validator_source = Path(__file__).read_text(encoding="utf-8")
    (directory / "validator_at_use.py").write_text(validator_source, encoding="utf-8")
    original_source = files[module["file"]]
    context = {"module": module, "requirements": specification,
               "current_source": files[module["file"]],
               "peer_contracts": [{"name": item["name"], "interfaces": item["interfaces"],
                                   "protocols": item.get("protocols")} for item in plan["modules"]]}
    system = (
        "Write an independent self-checking SystemVerilog unit TESTBENCH named tb_unit for the "
        "selected module's declared contract. Return code only. Instantiate the actual current "
        "module with named ports; drive EVERY input. Use no hierarchical force, no internal DUT "
        "writes and no replacement DUT. Compute expected values independently from the contract, "
        "never by copying DUT outputs or implementation. At most 1000 clock cycles. Drive on "
        "negedges, sample accepted events at posedges before nonblocking updates. Capture ALL "
        "responses in a continuous posedge monitor and wait for a monotonically increasing "
        "response counter in request tasks. A hit response may already be high immediately "
        "after the accepting edge: beginning to poll only after deasserting the request can "
        "miss that pulse. Do not sample only after #1 at the next edge where the pulse clears. "
        "Include a finite watchdog $fatal, at least three substantive checks and $display(\"UNIT_PASS\") "
        "only after ALL checks passed. Cover reset, one actual transaction with known nonzero "
        "payloads, backpressure and a second transaction with a different identity. "
        "For a cache, implement same-edge external response/acceptance, four distinct refill "
        "words, repeated hits and a stalled request; for D-cache cover store byte-enable lanes. "
        "For frontend, check request/PC pairing and exactly-once consumption under downstream "
        "backpressure. For rename, check source mapping BEFORE same-rd allocation, PR0, completion "
        "readiness, commit and free-PR recycling. For queues/ROB/LSQ, check separate accept, "
        "completion and retirement, concurrent push/pop, and response identity after selection "
        "changes. For ALU/decode use actual instruction encodings and nonzero operands. "
        "For predictor check initial invalid state, training, tag separation and prediction. "
        "Check the module's OWN responsibility only. This auxiliary test never replaces the "
        "unchanged teacher tests. Avoid assumptions not specified by the ports and protocol.")
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]
    tb = None
    test_calls = []
    for index in range(3):
        answer = runtime.call_model_messages(event, messages,
            model_options={"thinking": False, "temperature": 0.1, "max_tokens": 14000})
        test_calls.append(runtime.model_calls)
        proposed = extract_rtl(answer["answer"])
        names = re.findall(r"\bmodule\s+(\w+)", proposed)
        if names != ["tb_unit"] or "$fatal" not in proposed or "UNIT_PASS" not in proposed:
            checked = {"compiled": False, "passed": False,
                       "log": "Return exactly module tb_unit with real checks, $fatal and a UNIT_PASS marker."}
        else:
            checked = run_unit(directory / f"test-design-{index}", proposed, files)
        if checked["compiled"]:
            tb = proposed
            break
        messages = messages[:2] + [{"role": "assistant", "content": proposed[:30000]},
            {"role": "user", "content": "Correct only the testbench compile problem: " + checked["log"]}]
    review = None
    if tb is not None and not checked["passed"]:
        reviewed = runtime.call_model(event, json.dumps({"contract": module,
            "requirements": specification, "testbench": tb, "observation": checked}, ensure_ascii=False),
            "Review an auxiliary unit TEST, using ONLY the frozen module contract and test source. "
            "Do not infer a desired value from DUT output. Look for simulator scheduling races, "
            "missed single-cycle responses, unstated timing assumptions and incorrect protocol "
            "drivers. A request can produce a registered hit pulse immediately after acceptance. "
            "A continuous posedge response monitor sees it on the following edge before NBA; "
            "a request task that waits until that edge and then #1 may miss it. Also do not "
            "declare a transaction accepted by checking ready after the accepting edge. "
            "Return JSON {test_valid: true/false, reason: string, testbench: string}. When invalid, "
            "return a corrected COMPLETE tb_unit with the same independent expected values and "
            "coverage; never delete a failing check or lower requirements. If valid, return "
            "testbench as an empty string. No DUT RTL.",
            model_options={"thinking": False, "temperature": 0.1, "max_tokens": 14000})
        test_calls.append(runtime.model_calls)
        try:
            review = extract_json_payload(reviewed["answer"])
            corrected = extract_rtl(review.get("testbench", ""))
            if (review.get("test_valid") is False and
                    re.findall(r"\bmodule\s+(\w+)", corrected) == ["tb_unit"] and
                    "$fatal" in corrected and "UNIT_PASS" in corrected):
                trial = run_unit(directory / "test-review", corrected, files)
                if trial["compiled"]:
                    tb, checked = corrected, trial
        except (ValueError, TypeError):
            review = {"status": "invalid_review_response"}
    record = {"test_calls": test_calls, "testbench_sha256": digest(tb) if tb else None,
              "validator_sha256": digest(validator_source), "contract_test_review": review,
              "advisory_only": True, "checks": [checked], "rtl_repair_calls": []}
    if tb is None:
        record["status"] = "testbench_unavailable"
    else:
        (directory / "frozen_tb.sv").write_text(tb, encoding="utf-8")
        for revision in range(3):
            if checked["passed"]:
                break
            repair_prompt = {**context, "current_source": files[module["file"]],
                "frozen_unit_test": tb, "observed_failure": checked,
                "instruction": "Repair only this module's actual contract violation. Keep ALL "
                    "ports, capacities and semantics. Do not edit the frozen test, add test-specific "
                    "logic, hardcode a program or import a CPU. If the auxiliary test is incorrect, "
                    "return unchanged module code; full teacher validation will decide. Return the "
                    "complete synthesizable module, no prose."}
            answer = runtime.call_model(event, json.dumps(repair_prompt, ensure_ascii=False),
                "You repair one generated RTL module using current evidence. Synchronous active-low "
                "reset, no initial, no test edits, no stubs. Preserve the fixed internal contract.",
                model_options={"thinking": False, "temperature": 0.1, "max_tokens": 16000})
            call = runtime.model_calls
            record["rtl_repair_calls"].append(call)
            text = extract_rtl(answer["answer"])
            clean = re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.DOTALL)
            declarations = re.findall(r"\bmodule\s+(\w+)", clean)
            if declarations != [module["name"]] or reserved_declarations(text) or re.search(r"\b(?:initial|force)\b|negedge\s+rst_n", clean):
                record["checks"].append({"passed": False, "rejected": "Invalid module or forbidden construct", "call": call})
                continue
            candidate = {**files, module["file"]: text}
            trial = run_unit(directory / f"rtl-repair-{revision}", tb, candidate)
            record["checks"].append(trial)
            if not trial["compiled"]:
                checked = trial
                continue
            ref = runtime.write_text_artifact(f"generation/{event['step']:02d}/unit-revision-{revision}.sv", text)
            record["checks"][-1]["candidate_artifact"] = ref
            files[module["file"]] = text
            checked = trial
            if checked["passed"]:
                origin["revisions"].append({"artifact": ref, "sha256": digest(text), "call_index": call})
                origin.update(sha256=digest(text))
        record["status"] = "passed" if checked["passed"] else "failed_advisory"
        if not checked["passed"]:
            files[module["file"]] = original_source
            record["failed_unit_repairs_promoted"] = False
    (directory / "summary.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    return files[module["file"]], record

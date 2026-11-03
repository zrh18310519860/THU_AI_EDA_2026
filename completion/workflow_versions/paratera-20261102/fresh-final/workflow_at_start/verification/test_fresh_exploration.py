import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai_eda.agent.orchestrator import _seed_resume_project, run_agent
from ai_eda.agent.repair_context import read_sources, repair_view
from ai_eda.agent.skills.rv32i5_contract import planner_context
from ai_eda.agent.subagents.base import RunContext
from ai_eda.agent.subagents.coder import CoderAgent, generated_peer_context, generation_order
from ai_eda.agent.subagents.common import validate_plan
from ai_eda.agent.subagents.parser import ParserAgent
from ai_eda.agent.subagents.planner import PlannerAgent, l3_plan_errors
from ai_eda.web.server import _agent_options


class FakeClient:
    def __init__(self, answer):
        self.answer = answer
        self.request_records = []

    def parameters(self):
        return {"model": "test-fixture", "thinking": False}

    def stream_chat_detail(self, messages):
        self.request_records.append({"usage": {"total_tokens": 20}, "duration_ms": 1})
        yield {"type": "done", "answer": self.answer, "usage": {"total_tokens": 20}, "finish_reason": "stop"}


class FreshExplorationTest(unittest.TestCase):
    def test_plan_rejects_unrepresentable_physical_ids_but_accepts_architectural_ids(self):
        module = {"name": "rename_unit", "interfaces": "module rename_unit(input logic [4:0] commit_dest_pr, input logic [4:0] commit_rd);"}
        self.assertEqual(len(l3_plan_errors({"modules": [module]})), 1)
        module["interfaces"] = module["interfaces"].replace("[4:0] commit_dest_pr", "[5:0] commit_dest_pr")
        self.assertEqual(l3_plan_errors({"modules": [module]}), [])

    def runtime(self, root):
        events = []
        def begin(node_id, node_type, title, prompt, attempt=1):
            event = dict(step=len(events)+1, node_id=node_id, prompt=prompt)
            events.append(event)
            return event
        def finish(event, status, **fields):
            event.update(status=status, **fields)
        (root / "project").mkdir()
        return RunContext(run_dir=root, options={"project": {"id": "rv32i5_ooo"}, "l3_mode": "fresh"},
                          begin_event=begin, finish_event=finish, persist=lambda: None,
                          emit=lambda event: None, check_cancel=lambda: None, request_input=None), events

    def test_failed_model_stream_is_closed_and_accounted(self):
        class FailedClient(FakeClient):
            def stream_chat_detail(self, messages):
                self.request_records.append({"usage": None, "duration_ms": 7, "http_status": 402})
                raise RuntimeError("HTTP 402")
                yield
        with tempfile.TemporaryDirectory() as temp:
            runtime, _ = self.runtime(Path(temp))
            event = runtime.begin_event("diagnoser", "model", "diagnose", "fixture")
            with patch.object(runtime, "make_client", return_value=FailedClient("")):
                with self.assertRaisesRegex(RuntimeError, "402"):
                    runtime.call_model(event, "fixture", "fixture")
            self.assertFalse(event["streaming"])
            self.assertEqual(event["model_duration_ms"], 7)
            self.assertIsNone(event["output_tokens"])
            records = Path(temp) / "step-01-diagnoser/call-001/request_records.json"
            self.assertEqual(json.loads(records.read_text())[0]["http_status"], 402)

    def test_reasoning_budget_exhaustion_emits_artifact_with_both_calls_recorded(self):
        class ExhaustedClient(FakeClient):
            def stream_chat_detail(self, messages):
                self.request_records.append({"usage": {"total_tokens": 100}, "duration_ms": 5,
                                             "finish_reason": "length"})
                yield {"type": "reasoning", "text": "bounded design notes"}
                raise RuntimeError("reasoning exhausted output budget")
        with tempfile.TemporaryDirectory() as temp:
            runtime, _ = self.runtime(Path(temp))
            event = runtime.begin_event("planner", "model", "plan", "fixture")
            with patch.object(runtime, "make_client", side_effect=[ExhaustedClient(""), FakeClient('{"modules":[]}')]):
                result = runtime.call_model(event, "fixture", "fixture", model_options={"thinking": True})
            self.assertEqual(result["answer"], '{"modules":[]}')
            self.assertEqual(runtime.model_calls, 2)
            self.assertEqual(len(event["model_calls"]), 2)
            self.assertTrue(event["reasoning_budget_fallback"])
            self.assertTrue((Path(temp) / "step-01-planner/call-001/reasoning.txt").is_file())

    def test_partial_artifact_after_thinking_budget_is_replaced_by_complete_output(self):
        class PartialClient(FakeClient):
            def parameters(self):
                return {"model": "test-fixture", "thinking": True}
            def stream_chat_detail(self, messages):
                self.request_records.append({"usage": {"total_tokens": 100}, "duration_ms": 5,
                                             "finish_reason": "length"})
                yield {"type": "done", "answer": '{"incomplete":', "reasoning": "design notes",
                       "usage": {"total_tokens": 100}, "finish_reason": "length"}
        with tempfile.TemporaryDirectory() as temp:
            runtime, _ = self.runtime(Path(temp))
            event = runtime.begin_event("modifier", "model", "patch", "fixture")
            with patch.object(runtime, "make_client", side_effect=[PartialClient(""), FakeClient('{"complete":true}')]) as factory:
                result = runtime.call_model(event, "fixture", "fixture", model_options={"thinking": True})
            self.assertEqual(result["answer"], '{"complete":true}')
            self.assertFalse(factory.call_args.args[0]["thinking"])
            self.assertEqual(len(event["model_calls"]), 2)
            self.assertEqual((Path(temp) / "step-01-modifier/call-001/raw_response.txt").read_text(), '{"incomplete":')

    def test_all_entry_points_default_to_fresh_and_reject_baseline(self):
        self.assertEqual(inspect.signature(run_agent).parameters["l3_mode"].default, "fresh")
        self.assertEqual(_agent_options({"project_id": "rv32i5_ooo"})["l3_mode"], "fresh")
        with self.assertRaises(ValueError):
            _agent_options({"project_id": "rv32i5_ooo", "l3_mode": "verified_incremental"})
        with self.assertRaises(ValueError):
            run_agent(project_id="rv32i5_ooo", l3_mode="verified_incremental")
        self.assertNotIn("require a passed, verified", planner_context("rv32i5_ooo"))

    def test_parser_planner_coder_use_model_and_never_load_reference(self):
        plan = {"top_module": "rv32i5", "modules": [{"name": "rv32i5", "file": "rtl/rv32i5.sv",
                "interfaces": "module rv32i5(input clk);", "protocols": "synchronous edges", "dependencies": []}],
                "protocols": "valid is independent of ready"}
        clients = [FakeClient(json.dumps({"sufficient": True})), FakeClient(json.dumps(plan)),
                   FakeClient(json.dumps(plan)),
                   FakeClient("module rv32i5(input clk); endmodule")]
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, events = self.runtime(root)
            spec = runtime.write_text_artifact("spec.md", "Teacher fixture spec, no reference RTL.")
            initial = runtime.write_handoff("spec", "parser", {"artifacts": {"spec": spec}})
            with (
                patch.object(runtime, "make_client", side_effect=clients),
                patch("ai_eda.agent.skills.rv32i5_l3_reference.load_baseline", side_effect=AssertionError("No L3 baseline")) as baseline,
                patch("ai_eda.agent.subagents.coder.load_reference", side_effect=AssertionError("No L1 baseline")) as l1,
                patch("ai_eda.agent.subagents.coder.subprocess.run", return_value=SimpleNamespace(returncode=0, stdout="", stderr="")),
                patch("ai_eda.agent.subagents.coder.yosys_executable", return_value="yosys"),
            ):
                parsed = ParserAgent(runtime).run(initial)
                planned = PlannerAgent(runtime).run(parsed)
                result = CoderAgent(runtime).run(planned)
                baseline.assert_not_called()
                l1.assert_not_called()
            self.assertEqual(runtime.model_calls, 4)
            self.assertEqual([event["node_id"] for event in events], ["parser", "planner", "coder"])
            provenance = runtime.read_json_artifact("artifacts/generation_provenance.json")
            self.assertFalse(provenance["builtin_rtl_used"])
            self.assertEqual(provenance["files"][0]["call_indices"], [4])
            self.assertEqual(provenance["files"][0]["origin"], "model")
            self.assertEqual(runtime.read_json_artifact("artifacts/plan.json")["protocols"], plan["protocols"])
            self.assertTrue((root / "artifacts/plan_draft.json").is_file())
            self.assertTrue((root / "step-02-planner/call-003/conversation.json").is_file())
            self.assertTrue((root / "step-03-coder/call-004/conversation.json").is_file())
            self.assertEqual(runtime.read_handoff(result)["target"], "tester")

    def test_fresh_rejects_preseeded_rtl(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, _ = self.runtime(root)
            rtl = root / "project/rtl"
            rtl.mkdir()
            (rtl / "old.sv").write_text("module old; endmodule")
            spec = runtime.write_text_artifact("spec.md", "fixture")
            plan = runtime.write_json_artifact("plan.json", {"top_module": "top", "modules": []})
            handoff = runtime.write_handoff("planner", "coder", {"artifacts": {"spec": spec, "plan": plan}})
            with self.assertRaisesRegex(ValueError, "empty RTL"):
                CoderAgent(runtime).run(handoff)

    def test_unresolved_local_syntax_reaches_dedicated_tester_repair_loop(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, events = self.runtime(root)
            spec = runtime.write_text_artifact("spec.md", "fixture")
            plan = runtime.write_json_artifact("plan.json", {"top_module": "top", "modules": [
                {"name": "top", "file": "rtl/top.sv", "description": "fixture", "interfaces": "module top;"}]})
            handoff = runtime.write_handoff("planner", "coder", {"artifacts": {"spec": spec, "plan": plan}})
            with (
                patch.object(runtime, "make_client", side_effect=[FakeClient("module top; endmodule") for _ in range(3)]),
                patch("ai_eda.agent.subagents.coder.subprocess.run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="fixture syntax failure")),
            ):
                output = CoderAgent(runtime).run(handoff)
            self.assertEqual(runtime.model_calls, 3)
            self.assertEqual(runtime.read_handoff(output)["target"], "tester")
            self.assertFalse(runtime.read_json_artifact(events[0]["module_gate"])["passed"])
            self.assertEqual(events[0]["status"], "failed")

    def test_dependency_order_and_cycle(self):
        plan = validate_plan({"top_module": "top", "modules": [
            {"name": "top", "file": "rtl/top.sv", "dependencies": ["leaf"]},
            {"name": "leaf", "file": "rtl/leaf.sv"}]})
        self.assertEqual([m["name"] for m in generation_order(plan)], ["leaf", "top"])
        plan["modules"][1]["dependencies"] = ["top"]
        with self.assertRaises(ValueError):
            generation_order(plan)

    def test_peer_context_scoped_and_bounded(self):
        modules = [{"name": name, "file": f"rtl/{name}.sv"} for name in ["a", "b", "top"]]
        plan = {"modules": modules, "top_module": "top"}
        view = generated_peer_context(plan, {"name": "b", "dependencies": ["a"]},
                                      {m["file"]: "assign a=b;\n"*10000 for m in modules}, limit=4000)
        self.assertEqual(list(view), ["rtl/a.sv"])
        self.assertFalse(view["rtl/a.sv"]["source_complete"])
        self.assertLessEqual(len(view["rtl/a.sv"]["source_excerpt"]), 4000)
        top_view = generated_peer_context(plan, {"name": "top"},
            {m["file"]: "assign a=b;\n"*10000 for m in modules[:2]}, limit=4000)
        self.assertEqual(set(top_view), {"rtl/a.sv", "rtl/b.sv"})
        self.assertLessEqual(sum(len(v["source_excerpt"]) for v in top_view.values()), 4000)

    def test_repair_retains_complete_top_tail_and_driver(self):
        source = "module top;\n" + "wire a;\n"*5000 + "always @(posedge clk) fetch_pending <= 1'b1;\nendmodule\n"
        files = {"rtl/top.sv": source, "rtl/cache.sv": "module cache; endmodule"}
        plan = {"modules": [{"name": "top", "file": "rtl/top.sv", "interfaces": "module top;"}]}
        view = repair_view("spec", plan, files, {}, list(files), "rv32i5_ooo")
        self.assertTrue(view["source_complete"]["rtl/top.sv"])
        self.assertIn("fetch_pending <=", view["editable_sources"]["rtl/top.sv"])
        self.assertLessEqual(sum(map(len, view["editable_sources"].values())), 96000)

    def test_explicit_resume_seed_is_immutable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime, _ = self.runtime(root)
            source = {"run_id": "previous-fresh-run", "project_files": {"rtl/top.sv": "module top; endmodule"}}
            seeded = _seed_resume_project(runtime, source, "artifacts/spec.md", "artifacts/plan.json")
            (root / "project/rtl/top.sv").write_text("new mutable candidate")
            self.assertEqual(read_sources(runtime, seeded["manifest"]), source["project_files"])

    def test_rejected_patch_revisions_preserve_earlier_handoffs(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime, _ = self.runtime(Path(temp))
            first = runtime.write_handoff("diagnoser", "modifier", {"reason": "old hypothesis"}, attempt=1, revision=2)
            second = runtime.write_handoff("diagnoser", "modifier", {"reason": "revised hypothesis"}, attempt=1, revision=4)
            self.assertNotEqual(first, second)
            self.assertEqual(runtime.read_handoff(first)["payload"]["reason"], "old hypothesis")
            with self.assertRaisesRegex(ValueError, "拒绝覆盖"):
                runtime.write_handoff("diagnoser", "modifier", {}, attempt=1, revision=2)


if __name__ == "__main__":
    unittest.main()

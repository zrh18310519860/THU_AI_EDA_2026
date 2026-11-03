"""Focused regression checks; no paid API calls or EDA tools required."""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import httpx
from openai import APIStatusError, APIConnectionError

from ai_eda.agent.runs import _usage_summary, _normalize_run_payload
from ai_eda.model.client import ModelClient
from ai_eda.run_records import prompt_summary, timed_verification
from ai_eda.run_records import CLOCK_SESSION_ID
from ai_eda.rtl import runner
from ai_eda.token_metrics import summarize_usage, output_tokens


def call(p=10, c=4):
    return {"model": "test", "usage": {"prompt_tokens": p, "completion_tokens": c, "total_tokens": p+c}}


class RecordsTest(unittest.TestCase):
    def test_agent_multiple_calls_and_zero(self):
        usage = _usage_summary([{"model_calls": [call(), call(0, 0)]}, {"call_index": 3, "usage": call(3, 2)["usage"]}])
        self.assertEqual((usage["model_calls"], usage["total_tokens"]), (3, 19))
        self.assertTrue(usage["usage_complete"])

    def test_missing_usage_never_becomes_estimate(self):
        usage = _usage_summary([{"call_index": 1, "output_tokens": 500}, {"call_index": 2, "usage": call()["usage"]}])
        self.assertIsNone(usage["total_tokens"])
        self.assertEqual(usage["missing_usage_calls"], 1)
        self.assertIsNone(output_tokens(None, "some output"))

    def test_prompt_legacy_usage_and_missing_time(self):
        summary = prompt_summary({"usage": call()["usage"], "max_tokens": 8192}, {})
        self.assertEqual(summary["total_tokens"], 14)
        self.assertIsNone(summary["duration_ms"])
        self.assertIsNone(summary["area"])

    def test_failed_tb_hides_metrics(self):
        self.assertIsNone(prompt_summary({}, {"passed": False, "metrics": {"area": 50}})["area"])

    def test_verification_timing_keeps_first_tb_and_errors(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"metadata.json").write_text(json.dumps({"generation_started_at": datetime.now(timezone.utc).isoformat()}))
            wrapped = timed_verification("tb")(lambda _: {"passed": False})
            wrapped(root)
            first = json.loads((root/"metadata.json").read_text())["duration_to_tb_ms"]
            wrapped(root)
            data = json.loads((root/"metadata.json").read_text())
            self.assertEqual(first, data["duration_to_tb_ms"])
            self.assertEqual(len(data["verification_history"]), 2)
            @timed_verification("metrics")
            def fail(_):
                raise RuntimeError("tool failed")
            with self.assertRaises(RuntimeError):
                fail(root)
            data = json.loads((root/"metadata.json").read_text())
            self.assertEqual(data["verification_history"][-1]["error"], "tool failed")

    def test_clock_adjustment_cannot_shorten_generation_time(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"metadata.json").write_text(json.dumps({
                "generation_started_at": "2099-01-01T00:00:00+00:00",
                "generation_finished_at": "2099-01-01T00:00:10+00:00",
                "generation_duration_ms": 10000,
            }))
            timed_verification("tb")(lambda _: None)(root)
            data = json.loads((root/"metadata.json").read_text())
            self.assertGreaterEqual(data["duration_to_tb_ms"], 10000)

    def client(self, completion):
        with patch("ai_eda.model.client.resolve_api_key", return_value=("test", "test")), patch("ai_eda.model.client.OpenAI") as factory:
            client = ModelClient()
            factory.return_value.chat.completions.create.return_value = completion
        return client

    def test_stream_usage_only_final_chunk(self):
        chunks = [NS(usage=None, choices=[NS(finish_reason=None, delta=NS(content="module x; endmodule"))]), NS(usage=NS(**call()["usage"]), choices=[])]
        client = self.client(iter(chunks))
        result = list(client.stream_chat_detail([]))[-1]
        self.assertEqual(result["usage"]["total_tokens"], 14)
        self.assertEqual(client.request_records[0]["status"], "completed")
        self.assertIn("include_usage", client.client.chat.completions.create.call_args.kwargs["stream_options"])

    def test_stream_missing_usage_is_null(self):
        client = self.client(iter([NS(usage=None, choices=[NS(finish_reason="stop", delta=NS(content="answer"))])]))
        result = list(client.stream_chat_detail([]))[-1]
        self.assertIsNone(result["usage"])
        self.assertIsNone(summarize_usage(result["model_calls"])["total_tokens"])

    def test_interrupted_stream_retries_without_concatenating_partial_rtl(self):
        def broken():
            yield NS(usage=None, choices=[NS(finish_reason=None, delta=NS(content="BROKEN_PARTIAL"))])
            raise APIConnectionError(request=httpx.Request("POST", "https://example.invalid/chat"))
        client = self.client(None)
        client.client.chat.completions.create.side_effect = [broken(), iter([
            NS(usage=NS(**call()["usage"]), choices=[NS(finish_reason="stop", delta=NS(content="COMPLETE"))])])]
        with patch("ai_eda.model.client.time.sleep"):
            events = list(client.stream_chat_detail([]))
        self.assertEqual(events[-1]["answer"], "COMPLETE")
        self.assertEqual(sum(e["type"] == "restart" for e in events), 1)
        self.assertEqual(client.request_records[0]["interrupted_output"]["answer"], "BROKEN_PARTIAL")
        self.assertEqual(len(client.request_records), 2)
        self.assertIsNone(summarize_usage(client.request_records)["total_tokens"])

    def test_stream_failure_preserves_usage_and_time(self):
        client = self.client(iter([NS(usage=NS(**call()["usage"]), choices=[])]))
        with self.assertRaises(RuntimeError):
            list(client.stream_chat_detail([]))
        record = client.request_records[0]
        self.assertEqual(record["usage"]["total_tokens"], 14)
        self.assertGreaterEqual(record["duration_ms"], 0)
        self.assertEqual(record["status"], "failed")

    def test_sync_retry_counts_all_usage(self):
        completion = lambda text: NS(usage=NS(**call()["usage"]), choices=[NS(finish_reason="stop", message=NS(content=text))])
        client = self.client(None)
        client.client.chat.completions.create.side_effect = [completion(""), completion("answer")]
        with patch("ai_eda.model.client.time.sleep"):
            result = client._complete_detail([])
        self.assertEqual(summarize_usage(result["model_calls"])["total_tokens"], 28)

    def test_insufficient_balance_records_failure_without_retry_or_invented_usage(self):
        response = httpx.Response(402, request=httpx.Request("POST", "https://example.invalid/chat"))
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                client = self.client(None)
                client.client.chat.completions.create.side_effect = APIStatusError(
                    "Insufficient Balance", response=response, body={"error": "insufficient_balance"})
                with self.assertRaisesRegex(RuntimeError, "HTTP 402"):
                    if streaming:
                        list(client.stream_chat_detail([]))
                    else:
                        client._complete_detail([])
                self.assertEqual(client.client.chat.completions.create.call_count, 1)
                record = client.request_records[0]
                self.assertEqual(record["http_status"], 402)
                self.assertEqual(record["status"], "failed")
                self.assertGreaterEqual(record["duration_ms"], 0)
                self.assertIsNone(summarize_usage(client.request_records)["total_tokens"])

    def test_legacy_agent_ignores_old_total_tokens_alias(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"metadata.json").write_text(json.dumps({"model": "old", "max_tokens": 123}))
            payload = _normalize_run_payload({"status": "failed", "events": [{"call_index": 1, "usage": call()["usage"], "output_tokens": 4}], "total_tokens": 4}, root)
            self.assertEqual(payload["usage"]["total_tokens"], 14)
            self.assertEqual(payload["model_parameters"]["model"], "old")

    def test_transient_http_retry_preserves_failed_request(self):
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                response = httpx.Response(502, request=httpx.Request("POST", "https://example.invalid/chat"))
                error = APIStatusError("Bad gateway", response=response, body={})
                done = NS(usage=NS(**call()["usage"]), choices=[NS(finish_reason="stop",
                    delta=NS(content="COMPLETE"), message=NS(content="COMPLETE"))])
                client = self.client(None)
                client.client.chat.completions.create.side_effect = [error, iter([done]) if streaming else done]
                with patch("ai_eda.model.client.time.sleep"):
                    result = list(client.stream_chat_detail([]))[-1] if streaming else client._complete_detail([])
                self.assertEqual(result["answer"], "COMPLETE")
                self.assertEqual(len(client.request_records), 2)
                self.assertEqual(client.request_records[0]["http_status"], 502)
                self.assertEqual(client.request_records[0]["status"], "failed")
                self.assertIsNone(client.request_records[0]["usage"])
                self.assertGreaterEqual(client.request_records[0]["duration_ms"], 0)

    def test_transient_http_retries_are_bounded(self):
        response = httpx.Response(503, request=httpx.Request("POST", "https://example.invalid/chat"))
        for streaming in (True, False):
            with self.subTest(streaming=streaming):
                client = self.client(None)
                client.client.chat.completions.create.side_effect = APIStatusError("Unavailable", response=response, body={})
                with patch("ai_eda.model.client.time.sleep"), self.assertRaises(APIStatusError):
                    if streaming:
                        list(client.stream_chat_detail([]))
                    else:
                        client._complete_detail([])
                self.assertEqual(len(client.request_records), 3)

    def test_experiment_preserves_explicit_power_testbench(self):
        for custom_power_tb in (True, False):
            with self.subTest(custom_power_tb=custom_power_tb), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                rtl = root / "generated.sv"
                rtl.write_text("module dut; endmodule\n")
                power_tb = root / "custom_power.sv"
                power_tb.write_text("module custom_power; endmodule\n")
                (root / "metadata.json").write_text(json.dumps({
                    "task_name": "homework_aes128", "testbench_path": str(root / "tb.sv"),
                    "testbench_top": "tb", "timeout_seconds": 20,
                }))
                (root / "test_report.json").write_text(json.dumps({"passed": True}))
                with patch.object(runner, "create_run", return_value=root), \
                     patch.object(runner, "run_testbench", return_value={"passed": True}), \
                     patch.object(runner.subprocess, "run", return_value=NS(returncode=0, stdout="", stderr="")), \
                     patch.object(runner, "synthesize_metrics", return_value={"area": 1}) as synth:
                    runner.run_prompt_experiment(
                        task_name="homework_aes128", spec_path=root / "spec.md",
                        testbench_path=root / "tb.sv", testbench_top="tb",
                        power_testbench_path=power_tb if custom_power_tb else None,
                        power_testbench_top="custom_power" if custom_power_tb else None,
                        prompt_path=root / "prompt.md", run_id="example",
                    )
                    if custom_power_tb:
                        self.assertEqual(synth.call_args.kwargs["vcd_scope"], "custom_power/dut")
                    else:
                        synth.assert_called_once_with(rtl, artifact_dir=root / "ppa")
                saved = json.loads((root / "metadata.json").read_text())
                self.assertEqual(saved["power_testbench_path"], str(power_tb.resolve()) if custom_power_tb else None)


if __name__ == "__main__":
    unittest.main()

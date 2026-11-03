"""模型 API usage 的精确统计与流式输出估算。"""

from __future__ import annotations

import math
from typing import Any


def estimate_output_tokens(text: str) -> int:
    """估算输出 token：CJK 字符约 1 token，其他字符约 4 字符/token。"""
    value = str(text or "")
    if not value:
        return 0
    cjk = 0
    for char in value:
        code = ord(char)
        if (
            0x3400 <= code <= 0x4DBF
            or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF
        ):
            cjk += 1
    other = max(0, len(value) - cjk)
    return max(1, cjk + math.ceil(other / 4))


def output_tokens(usage: dict[str, Any] | None, text: str = "") -> int | None:
    """返回服务端报告的 completion tokens；缺少 usage 时返回 None。"""
    if isinstance(usage, dict):
        value = usage.get("completion_tokens")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return int(value)
    return None


def summarize_usage(events: list[dict[str, Any]]) -> dict[str, Any]:
    """汇总 Agent 模型调用的服务端 usage，并标记缺失的调用。"""
    calls = [
        event
        for event in events
        if isinstance(event, dict)
        and (event.get("model_call_attempted") or event.get("call_index") is not None
             or (event.get("model") and "usage" in event))
    ]
    fields = ("prompt_tokens", "completion_tokens")
    values: dict[str, list[int]] = {field: [] for field in fields}
    exact_totals: list[int] = []
    complete_calls = 0

    for event in calls:
        usage = event.get("usage")
        call_values: dict[str, int] = {}
        if isinstance(usage, dict):
            for field in fields:
                value = usage.get(field)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                    call_values[field] = int(value)
                    values[field].append(int(value))

        if len(call_values) == len(fields):
            total = usage.get("total_tokens") if isinstance(usage, dict) else None
            if not isinstance(total, (int, float)) or isinstance(total, bool) or total < 0:
                total = call_values["prompt_tokens"] + call_values["completion_tokens"]
            exact_totals.append(int(total))
            complete_calls += 1

    call_count = len(calls)
    complete = call_count > 0 and complete_calls == call_count
    return {
        "model_calls": call_count,
        "usage_calls": complete_calls,
        "missing_usage_calls": call_count - complete_calls,
        "usage_complete": complete,
        "prompt_tokens": sum(values["prompt_tokens"]) if len(values["prompt_tokens"]) == call_count and call_count else None,
        "completion_tokens": sum(values["completion_tokens"]) if len(values["completion_tokens"]) == call_count and call_count else None,
        "total_tokens": sum(exact_totals) if complete else None,
    }

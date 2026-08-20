from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .adapters import UNAVAILABLE
from .io import redact


def _tool_name(item: dict[str, Any]) -> str:
    return str(item.get("tool") or item.get("name") or item.get("command") or "")


def parse_codex_jsonl(path: Path) -> dict[str, Any]:
    trace_by_id: dict[str, dict[str, Any]] = {}
    trace_order: list[str] = []
    diagnostics: list[str] = []
    final_text = ""
    thread_id = UNAVAILABLE
    termination: dict[str, Any] = {"status": "unknown", "reason": UNAVAILABLE}
    usage: dict[str, Any] = {
        "inputTokens": UNAVAILABLE,
        "cachedInputTokens": UNAVAILABLE,
        "cacheWriteInputTokens": UNAVAILABLE,
        "outputTokens": UNAVAILABLE,
        "reasoningOutputTokens": UNAVAILABLE,
        "totalTokens": UNAVAILABLE,
    }
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            diagnostics.append(f"invalid-jsonl-line:{line_number}")
            continue
        event_type = str(event.get("type") or "")
        if event_type == "thread.started":
            thread_id = str(event.get("thread_id") or UNAVAILABLE)
        if event_type in {"turn.completed", "turn.failed"}:
            termination = {
                "status": "completed" if event_type == "turn.completed" else "failed",
                "reason": redact(event.get("error") or event.get("message") or UNAVAILABLE),
            }
        if event_type == "error":
            diagnostics.append(str(redact(event.get("message") or "codex-error")))
        if not event_type.startswith("item."):
            if event_type == "turn.completed":
                raw_usage = event.get("usage") or {}
                mapping = {
                    "inputTokens": "input_tokens",
                    "cachedInputTokens": "cached_input_tokens",
                    "cacheWriteInputTokens": "cache_write_input_tokens",
                    "outputTokens": "output_tokens",
                    "reasoningOutputTokens": "reasoning_output_tokens",
                }
                for target, source in mapping.items():
                    if isinstance(raw_usage.get(source), int):
                        usage[target] = raw_usage[source]
                if isinstance(usage["inputTokens"], int) and isinstance(usage["outputTokens"], int):
                    usage["totalTokens"] = usage["inputTokens"] + usage["outputTokens"]
            continue
        item = event.get("item")
        if not isinstance(item, dict):
            diagnostics.append(f"missing-item:{line_number}")
            continue
        item_type = str(item.get("type") or "")
        if item_type == "agent_message" and event_type == "item.completed":
            final_text = str(item.get("text") or "")
        if item_type in {"reasoning", "agent_message"}:
            continue
        item_id = str(item.get("id") or f"line-{line_number}")
        if item_id not in trace_by_id:
            trace_order.append(item_id)
            trace_by_id[item_id] = {
                "callId": item_id,
                "kind": item_type or "unknown",
                "tool": _tool_name(item),
            }
        normalized = trace_by_id[item_id]
        normalized["status"] = event_type.removeprefix("item.")
        for source, target in (
            ("arguments", "arguments"),
            ("input", "arguments"),
            ("result", "response"),
            ("output", "response"),
            ("error", "error"),
            ("server", "server"),
        ):
            if source in item:
                normalized[target] = redact(item[source])
    return {
        "threadId": thread_id,
        "finalText": final_text,
        "trace": [trace_by_id[item_id] for item_id in trace_order],
        "usage": usage,
        "termination": termination,
        "diagnostics": diagnostics,
    }

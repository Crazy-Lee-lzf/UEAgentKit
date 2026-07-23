from __future__ import annotations

import json
import secrets
from collections import OrderedDict
from collections.abc import Callable
from typing import Any


DEFAULT_OUTPUT_TOKEN_BUDGET = 4096
MIN_OUTPUT_TOKEN_BUDGET = 256
MAX_OUTPUT_TOKEN_BUDGET = 32768
MAX_CONTINUATION_TOKENS = 4096


class ContinuationTokenError(ValueError):
    """Raised when a continuation token is missing, expired, or used with another snapshot."""


class ContinuationTokenStore:
    """Session-local opaque continuation state with bounded memory use."""

    def __init__(self, *, maximum_tokens: int = MAX_CONTINUATION_TOKENS) -> None:
        self.maximum_tokens = maximum_tokens
        self._states: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def issue(self, *, tool: str, snapshot_id: str, state: dict[str, Any]) -> str:
        token = "ct_" + secrets.token_urlsafe(24)
        self._states[token] = {
            "tool": tool,
            "snapshotId": snapshot_id,
            "state": dict(state),
        }
        self._states.move_to_end(token)
        while len(self._states) > self.maximum_tokens:
            self._states.popitem(last=False)
        return token

    def resolve(self, token: str, *, tool: str, snapshot_id: str) -> dict[str, Any]:
        token = token.strip()
        if not token or not token.startswith("ct_"):
            raise ContinuationTokenError("continuation_token is invalid")
        stored = self._states.get(token)
        if stored is None:
            raise ContinuationTokenError("continuation_token is unknown or expired")
        if stored["tool"] != tool:
            raise ContinuationTokenError("continuation_token belongs to another Tool")
        if stored["snapshotId"] != snapshot_id:
            raise ContinuationTokenError("continuation_token belongs to another index snapshot")
        self._states.move_to_end(token)
        return dict(stored["state"])


def normalize_output_token_budget(value: int) -> int:
    if value < MIN_OUTPUT_TOKEN_BUDGET:
        raise ValueError(f"max_output_tokens must be at least {MIN_OUTPUT_TOKEN_BUDGET}")
    if value > MAX_OUTPUT_TOKEN_BUDGET:
        raise ValueError(f"max_output_tokens must not exceed {MAX_OUTPUT_TOKEN_BUDGET}")
    return value


def estimate_json_tokens(value: Any) -> int:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return max(1, (len(encoded) + 3) // 4)


def fit_sequence_to_budget(
    items: list[dict[str, Any]],
    *,
    max_output_tokens: int,
    build_payload: Callable[[list[dict[str, Any]]], Any],
    force_one: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Return the largest stable prefix whose serialized response fits the requested budget."""
    if not items:
        return [], estimate_json_tokens(build_payload([]))

    full_estimate = estimate_json_tokens(build_payload(items))
    if full_estimate <= max_output_tokens:
        return items, full_estimate

    low = 0
    high = len(items)
    while low < high:
        middle = (low + high + 1) // 2
        estimate = estimate_json_tokens(build_payload(items[:middle]))
        if estimate <= max_output_tokens:
            low = middle
        else:
            high = middle - 1

    if low == 0 and force_one:
        return items[:1], estimate_json_tokens(build_payload(items[:1]))
    selected = items[:low]
    return selected, estimate_json_tokens(build_payload(selected))

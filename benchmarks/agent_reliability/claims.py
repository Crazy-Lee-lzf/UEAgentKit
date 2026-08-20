from __future__ import annotations

import json
import re
from typing import Any

RESULT_RE = re.compile(r"\{\s*\"benchmarkResult\"\s*:", re.DOTALL)


def parse_agent_claim(final_text: str) -> tuple[dict[str, Any] | None, str | None]:
    starts = [match.start() for match in RESULT_RE.finditer(final_text)]
    decoder = json.JSONDecoder()
    for start in reversed(starts):
        try:
            value, _ = decoder.raw_decode(final_text[start:])
        except json.JSONDecodeError:
            continue
        result = value.get("benchmarkResult") if isinstance(value, dict) else None
        if not isinstance(result, dict):
            continue
        if result.get("status") not in {"success", "blocked", "failed", "insufficient-evidence"}:
            return None, "result-contract-invalid-status"
        return result, None
    return None, "result-contract-missing"

from __future__ import annotations

import json
import re
from typing import Any

RESULT_RE = re.compile(r"\{\s*\"benchmarkResult\"\s*:", re.DOTALL)
RESULT_STATUSES = frozenset({"success", "blocked", "failed", "insufficient-evidence"})
TRUST_VERDICTS = frozenset(
    {"verified", "suspicious", "failed", "insufficient-evidence", "not-evaluated"}
)
CONFLICT_KINDS = frozenset(
    {
        None,
        "stale-revision",
        "dirty-package",
        "required-evidence-missing",
        "policy-block",
        "unexpected-semantic-change",
        "recovery-failed",
    }
)
SEMANTIC_OPERATIONS = frozenset(
    {
        None,
        "no-op",
        "renameDataTableRow",
        "rollback",
        "setAssetProperty",
        "setAssetReferenceProperty",
        "setDataTableCell",
        "setMaterialInstanceScalarParameter",
        "setVariableDefault",
    }
)


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
        if result.get("status") not in RESULT_STATUSES:
            return None, "result-contract-invalid-status"
        if result.get("trustVerdict") not in TRUST_VERDICTS:
            return None, "result-contract-invalid-trust-verdict"
        semantic = result.get("claimedSemanticResult")
        if not isinstance(semantic, dict):
            return None, "result-contract-invalid-semantic-result"
        if semantic.get("operation") not in SEMANTIC_OPERATIONS:
            return None, "result-contract-invalid-operation"
        if semantic.get("conflict") not in CONFLICT_KINDS:
            return None, "result-contract-invalid-conflict"
        return result, None
    return None, "result-contract-missing"

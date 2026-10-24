"""Read-only Z6 evaluator over existing isolated M3 evidence.

It consumes already-produced receipts/matrix JSON and emits aggregate counts,
latency percentiles, security/identity detection, write detection, and proposed
(not approved) thresholds.  It never starts a broker/Editor and never emits raw
paths, messages, or tokens.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

_SECURITY_CODES = frozenset(
    {
        "native-preview-token-rejected",
        "native-preview-broker-descriptor-invalid",
        "native-preview-project-mismatch",
        "native-preview-identity-mismatch",
        "native-preview-plan-id-mismatch",
        "native-preview-plan-hash-mismatch",
        "native-preview-plan-invalid",
        "native-preview-revision-conflict",
        "native-preview-fingerprint-conflict",
        "native-preview-package-dirty",
        "native-preview-pie-active",
        "native-preview-response-invalid",
        "native-preview-response-too-large",
    }
)
_TOKEN_KEY_RE = re.compile(r"(?i).*token.*")
_UUID_OR_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|/Game/|sha256:|sha1:)")


@dataclass
class TelemetrySummary:
    attempts: int
    native_success: int
    fallback: int
    native_failure: int
    busy: int
    security_failures: int
    unexpected_writes: int
    dirty_receipts: int
    token_fields: int
    latency_ms: list[float]
    errors_by_code: dict[str, int] = field(default_factory=dict)
    thresholds_breached: list[str] = field(default_factory=list)

    @property
    def fallback_rate(self) -> float:
        return self.fallback / self.attempts if self.attempts else 0.0

    @property
    def native_failure_rate(self) -> float:
        return self.native_failure / self.attempts if self.attempts else 0.0

    def _percentile(self, fraction: float) -> float:
        if not self.latency_ms:
            return 0.0
        ordered = sorted(self.latency_ms)
        index = min(len(ordered) - 1, max(0, math.ceil(fraction * len(ordered)) - 1))
        return round(ordered[index], 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": "ueak.canary-telemetry/1.0",
            "readOnly": True,
            "attempts": self.attempts,
            "nativeSuccess": self.native_success,
            "fallback": self.fallback,
            "nativeFailure": self.native_failure,
            "busy": self.busy,
            "securityFailures": self.security_failures,
            "unexpectedWrites": self.unexpected_writes,
            "expectedDirtyTestReceipts": self.dirty_receipts,
            "tokenFields": self.token_fields,
            "fallbackRate": round(self.fallback_rate, 6),
            "nativeFailureRate": round(self.native_failure_rate, 6),
            "latencyMs": {
                "count": len(self.latency_ms),
                "p50": self._percentile(0.50),
                "p95": self._percentile(0.95),
                "max": round(max(self.latency_ms), 3) if self.latency_ms else 0.0,
            },
            "errorsByCode": dict(sorted(self.errors_by_code.items())),
            "thresholds": {
                "unapprovedProposal": True,
                "fallbackRateGreaterThan": 0.05,
                "nativeFailureRateGreaterThan": 0.02,
                "anySecurityFailure": True,
                "anyUnexpectedWrite": True,
            },
            "thresholdsBreached": self.thresholds_breached,
            "redaction": {
                "tokensEmitted": False,
                "pathsEmitted": False,
                "messagesEmitted": False,
            },
        }


def _load(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _walk_token_fields(value: Any) -> int:
    if isinstance(value, dict):
        count = sum(1 for key in value if isinstance(key, str) and _TOKEN_KEY_RE.fullmatch(key))
        return count + sum(_walk_token_fields(item) for item in value.values())
    if isinstance(value, list):
        return sum(_walk_token_fields(item) for item in value)
    return 0


def _latency_ms(receipt: dict[str, Any]) -> float | None:
    started = receipt.get("invokedAtUtc")
    finished = receipt.get("completedAtUtc")
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    try:
        left = datetime.fromisoformat(started.replace("Z", "+00:00"))
        right = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    value = (right - left).total_seconds() * 1000.0
    return value if value >= 0 else None


def _receipts_from_evidence(root: Path) -> list[tuple[str, str, dict[str, Any]]]:
    receipts: list[tuple[str, str, dict[str, Any]]] = []
    z1 = _load(root / "z1-multigraph-parity.json")
    if isinstance(z1, dict):
        for graph in z1.get("graphs", []):
            if isinstance(graph, dict) and isinstance(graph.get("receipt"), dict):
                receipts.append(("z1", str(graph.get("kind", "")), graph["receipt"]))
    z2 = _load(root / "z2-expanded-matrix.json")
    if isinstance(z2, dict):
        for item in z2.get("results", []):
            if isinstance(item, dict) and isinstance(item.get("receipt"), dict):
                receipts.append(
                    ("z2", f"{item.get('graph', '')}:{item.get('scenario', '')}", item["receipt"])
                )
    z3 = _load(root / "z3-rollback.json")
    if isinstance(z3, dict):
        for item in z3.get("results", []):
            if isinstance(item, dict) and isinstance(item.get("receipt"), dict):
                receipts.append(("z3", str(item.get("step", "")), item["receipt"]))
    return receipts


def evaluate_evidence(evidence_root: str | Path) -> TelemetrySummary:
    root = Path(evidence_root)
    receipts = _receipts_from_evidence(root)
    success = fallback = failure = busy = security = writes = dirty = token_fields = 0
    errors: dict[str, int] = {}
    latencies: list[float] = []
    for source, label, receipt in receipts:
        code = receipt.get("errorCode") or receipt.get("nativeErrorCode") or ""
        if code:
            errors[code] = errors.get(code, 0) + 1
        if label.endswith(":busy") or code == "native-preview-busy":
            busy += 1
        is_fallback = receipt.get("fallback") is True
        is_success = (
            receipt.get("ok") is True
            and receipt.get("previewPath") == "native-broker"
            and not is_fallback
        )
        if is_fallback:
            fallback += 1
        elif is_success:
            success += 1
        else:
            failure += 1
        if code in _SECURITY_CODES:
            security += 1
        if (
            receipt.get("saved") is True
            or receipt.get("compiled") is True
            or receipt.get("packageDirtyAfter") is True
        ):
            if label.endswith(":dirty"):
                dirty += 1
            else:
                writes += 1
        token_fields += _walk_token_fields(receipt)
        latency = _latency_ms(receipt)
        if latency is not None:
            latencies.append(latency)
    summary = TelemetrySummary(
        attempts=len(receipts),
        native_success=success,
        fallback=fallback,
        native_failure=failure,
        busy=busy,
        security_failures=security,
        unexpected_writes=writes,
        dirty_receipts=dirty,
        token_fields=token_fields,
        latency_ms=latencies,
        errors_by_code=errors,
    )
    if summary.fallback_rate > 0.05:
        summary.thresholds_breached.append("fallback-rate>5%")
    if summary.native_failure_rate > 0.02:
        summary.thresholds_breached.append("native-failure-rate>2%")
    if summary.security_failures:
        summary.thresholds_breached.append("any-security-failure")
    if summary.unexpected_writes:
        summary.thresholds_breached.append("any-unexpected-write")
    if summary.token_fields:
        summary.thresholds_breached.append("token-field-observed")
    return summary

"""Offline, read-only pre-M4 canary/telemetry checks.

This package is not imported by the Editor plugin or broker startup.  It exists
only for local canary eligibility and evidence review.
"""

from .canary_preflight import (
    CANARY_ALLOWLIST_SCHEMA,
    CANARY_MARKER_SCHEMA,
    CanaryPreflightResult,
    run_canary_preflight,
)
from .telemetry_eval import TelemetrySummary, evaluate_evidence

__all__ = [
    "CANARY_ALLOWLIST_SCHEMA",
    "CANARY_MARKER_SCHEMA",
    "CanaryPreflightResult",
    "TelemetrySummary",
    "evaluate_evidence",
    "run_canary_preflight",
]

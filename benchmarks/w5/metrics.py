"""W5 metrics aggregation and summary computation."""

from __future__ import annotations

import math
from statistics import mean, pstdev
from typing import Any

STAGE_FIELDS = (
    "planMs",
    "policyRevisionMs",
    "applyMs",
    "fastVerifyMs",
    "compileMs",
    "checkpointPreviewMs",
    "saveMs",
    "strongVerifyMs",
    "semanticDiffMs",
    "validationMs",
    "trustMs",
)


def _quantile(sorted_values: list[float], q: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = q * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    fraction = position - low
    return sorted_values[low] * (1 - fraction) + sorted_values[high] * fraction


def summarize_numeric(values: list[float | int | None]) -> dict[str, Any]:
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    if not numeric:
        return {"sampleCount": 0}
    numeric.sort()
    return {
        "sampleCount": len(numeric),
        "min": round(numeric[0], 3),
        "max": round(numeric[-1], 3),
        "p50": round(_quantile(numeric, 0.5), 3) if numeric else None,
        "p95": round(_quantile(numeric, 0.95), 3) if len(numeric) >= 10 else None,
        "mean": round(mean(numeric), 3),
        "stddev": round(pstdev(numeric), 3) if len(numeric) > 1 else None,
        "p95Claimable": len(numeric) >= 10,
    }


def _stage_total(attempt: dict[str, Any]) -> float:
    stages = attempt.get("stages") or {}
    return sum(float(stages.get(field) or 0.0) for field in STAGE_FIELDS if stages.get(field) is not None)


def summarize_attempts(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = [attempt for attempt in attempts if attempt.get("success") is True]
    failed = [attempt for attempt in attempts if not attempt.get("success")]
    result: dict[str, Any] = {
        "attemptCount": len(attempts),
        "successCount": len(succeeded),
        "failureCount": len(failed),
        "stages": {},
    }
    for field in STAGE_FIELDS:
        result["stages"][field] = summarize_numeric([attempt.get("stages", {}).get(field) for attempt in succeeded])
    result["totalMs"] = summarize_numeric([attempt.get("totalMs") for attempt in succeeded])
    result["resultBytes"] = summarize_numeric([attempt.get("resultBytes") for attempt in succeeded])
    result["recoveryOrResetMs"] = summarize_numeric(
        [attempt.get("recoveryOrResetMs") for attempt in succeeded]
    )
    result["failureErrorCodes"] = sorted(
        {
            str(attempt.get("errorCode") or "unknown")
            for attempt in failed
        }
    )
    result["finalTrustStates"] = sorted({str(attempt.get("finalTrustState")) for attempt in succeeded})
    return result


def _ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def compute_paired_ratios(
    resident_attempts: list[dict[str, Any]],
    cold_attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute resident vs cold ratios from paired same-scenario runs.

    mutationPathSpeedup:
        cold mutation total / (resident applyMs + fastVerifyMs)
    persistedWorkflowRatio:
        cold persisted workflow total / resident persisted workflow total

    Cold totals are summed across commandlet launches when a scenario uses
    more than one per-asset RunPatch invocation.
    """
    resident_total = mean(
        float(a.get("totalMs") or 0.0) for a in resident_attempts if a.get("success")
    )
    resident_mutation = mean(
        float((a.get("stages") or {}).get("applyMs") or 0.0)
        + float((a.get("stages") or {}).get("fastVerifyMs") or 0.0)
        for a in resident_attempts
        if a.get("success")
    )
    cold_total = mean(float(a.get("totalMs") or 0.0) for a in cold_attempts if a.get("success"))
    cold_mutation = mean(
        float((a.get("stages") or {}).get("mutationMs") or 0.0)
        for a in cold_attempts
        if a.get("success")
    )
    return {
        "residentSampleCount": sum(1 for a in resident_attempts if a.get("success")),
        "coldSampleCount": sum(1 for a in cold_attempts if a.get("success")),
        "mutationPathSpeedup": _ratio(cold_mutation, resident_mutation),
        "persistedWorkflowRatio": _ratio(cold_total, resident_total),
        "coldMutationMsMean": round(cold_mutation, 3) if cold_attempts else None,
        "residentMutationMsMean": round(resident_mutation, 3) if resident_attempts else None,
        "coldTotalMsMean": round(cold_total, 3) if cold_attempts else None,
        "residentTotalMsMean": round(resident_total, 3) if resident_attempts else None,
    }


def stage_contribution(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    succeeded = [attempt for attempt in attempts if attempt.get("success")]
    if not succeeded:
        return {"samples": 0}
    total = sum(_stage_total(attempt) for attempt in succeeded)
    contributions = {
        field: round(
            sum(float(attempt.get("stages", {}).get(field) or 0.0) for attempt in succeeded) / total,
            6,
        )
        for field in STAGE_FIELDS
    }
    return {
        "samples": len(succeeded),
        "totalStageMs": round(total, 3),
        "contributions": contributions,
    }


def noise_groups(attempts: list[dict[str, Any]], group_count: int = 3) -> dict[str, Any]:
    succeeded = [attempt for attempt in attempts if attempt.get("success")]
    if len(succeeded) < group_count:
        return {
            "evaluable": False,
            "reason": f"need at least {group_count} succeeded attempts",
            "groups": [],
        }
    groups: list[list[dict[str, Any]]] = []
    for index, attempt in enumerate(succeeded):
        group_index = (index * group_count) // len(succeeded)
        if group_index >= len(groups):
            groups.append([])
        groups[group_index].append(attempt)
    medians: list[float] = []
    for group in groups:
        values = sorted(float(a.get("totalMs") or 0.0) for a in group)
        medians.append(_quantile(values, 0.5) or 0.0)
    spread = _ratio(max(medians) - min(medians), max(medians)) if medians else None
    return {
        "evaluable": True,
        "groupCount": len(groups),
        "groupSizes": [len(group) for group in groups],
        "groupMediansMs": [round(value, 3) for value in medians],
        "medianSpread": spread,
        "label": "noisy" if spread is not None and spread > 0.2 else "repeatable",
    }

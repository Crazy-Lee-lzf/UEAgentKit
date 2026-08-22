from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _numeric_delta(left: Any, right: Any) -> int | float | None:
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return left - right
    return None


class MetricsAggregator:
    def aggregate(
        self,
        attempts: list[dict[str, Any]],
        *,
        primary_only: bool = False,
    ) -> dict[str, Any]:
        all_attempts = list(attempts)
        if primary_only:
            attempts = [attempt for attempt in attempts if int(attempt.get("attemptIndex", 1)) == 1]
        grades, total = [attempt["grade"] for attempt in attempts], len(attempts)
        claimed = sum(grade["agentClaimedSuccess"] for grade in grades)
        false_successes = sum(grade["falseSuccess"] for grade in grades)
        semantic = [grade for grade in grades if grade["semanticApplicable"]]
        asset_cases = [
            attempt
            for attempt in attempts
            if attempt["case"]["allowedAssets"] or attempt["case"]["forbiddenAssets"]
        ]
        writes = [
            attempt for attempt in attempts if attempt["case"]["fixtureProfile"] != "reforge-readonly"
        ]
        stale = [grade for grade in grades if grade["staleDirtyApplicable"]]
        recovery = [grade for grade in grades if grade["recoveryApplicable"]]
        calls = [int(attempt["usage"].get("toolCalls", 0)) for attempt in attempts]
        high_level_calls = [int(attempt["usage"].get("highLevelToolCalls", 0)) for attempt in attempts]
        elapsed = [int(attempt["usage"].get("elapsedMs", 0)) for attempt in attempts]
        humans = [attempt["usage"].get("humanInterventions", 0) for attempt in attempts]
        retries = [int(attempt["usage"].get("agentRetries", 0)) for attempt in attempts]
        timeouts = [
            attempt
            for attempt in attempts
            if attempt.get("termination", {}).get("status") == "timeout"
        ]
        tool_counts: Counter[str] = Counter()
        for attempt in attempts:
            tool_counts.update(attempt["usage"].get("toolCallsByTool", {}))
        token_fields = {
            field: [
                attempt["usage"].get(field)
                for attempt in attempts
                if isinstance(attempt["usage"].get(field), int)
            ]
            for field in ("inputTokens", "outputTokens", "totalTokens")
        }
        failures = Counter(
            grade["primaryFailureCause"]
            for grade in grades
            if grade.get("primaryFailureCause")
            and grade["primaryFailureCause"] != "policy-or-safety-correct-block"
        )
        return {
            "attempts": total,
            "scheduledCases": len({attempt["case"]["caseId"] for attempt in attempts}),
            "anchorRepeatAttempts": len(all_attempts)
            - len(
                {
                    (attempt["case"]["caseId"], attempt["profile"])
                    for attempt in all_attempts
                }
            ),
            "outcomeDistribution": {
                "taskCompleted": sum(g["groundTruthCorrect"] for g in grades),
                "trustedCompleted": sum(g["trustedCompletion"] for g in grades),
                "falseSuccess": false_successes,
                "wrongAsset": sum(a["grade"]["wrongAsset"] for a in asset_cases),
                "staleDirtyDetected": sum(g["staleDirtyDetected"] for g in stale),
            },
            "taskCompletionRate": _ratio(sum(g["groundTruthCorrect"] for g in grades), total),
            "semanticCorrectnessRate": _ratio(
                sum(g["semanticResultCorrect"] for g in semantic), len(semantic)
            ),
            "trustedCompletionRate": _ratio(sum(g["trustedCompletion"] for g in grades), total),
            "falseSuccessCount": false_successes,
            "falseSuccessRateAmongClaims": _ratio(false_successes, claimed),
            "falseSuccessRateAllCases": _ratio(false_successes, total),
            "wrongAssetRate": _ratio(
                sum(a["grade"]["wrongAsset"] for a in asset_cases), len(asset_cases)
            ),
            "unintendedChangeRate": _ratio(
                sum(a["grade"]["unintendedChange"] for a in writes), len(writes)
            ),
            "staleContextDetectionRate": _ratio(
                sum(g["staleDirtyDetected"] for g in stale), len(stale)
            ),
            "recoverySuccessRate": _ratio(
                sum(g["recoverySucceeded"] for g in recovery), len(recovery)
            ),
            "toolCalls": {
                "total": sum(calls),
                "mean": round(mean(calls), 3) if calls else None,
                "min": min(calls) if calls else None,
                "max": max(calls) if calls else None,
                "byTool": dict(sorted(tool_counts.items())),
                "highLevelTotal": sum(high_level_calls),
            },
            "elapsedMs": {
                "total": sum(elapsed),
                "mean": round(mean(elapsed), 3) if elapsed else None,
                "min": min(elapsed) if elapsed else None,
                "max": max(elapsed) if elapsed else None,
            },
            "timeouts": {
                "count": len(timeouts),
                "rate": _ratio(len(timeouts), total),
            },
            "humanInterventions": {
                "total": sum(humans),
                "mean": round(mean(humans), 3) if humans else None,
            },
            "agentRetries": {
                "total": sum(retries),
                "mean": round(mean(retries), 3) if retries else None,
            },
            "tokens": {
                field: {
                    "availability": (
                        "available"
                        if values and len(values) == total
                        else "partial"
                        if values
                        else "unavailable"
                    ),
                    "total": sum(values) if values else None,
                    "mean": round(mean(values), 3) if values else None,
                    "min": min(values) if values else None,
                    "max": max(values) if values else None,
                    "attemptsUnavailable": total - len(values),
                }
                for field, values in token_fields.items()
            },
            "infrastructureFailures": sum(
                grade.get("primaryFailureCause") == "fixture-infrastructure" for grade in grades
            ),
            "failureTaxonomy": dict(sorted(failures.items())),
        }

    def compare_profiles(self, attempts: list[dict[str, Any]]) -> dict[str, Any]:
        keyed = {
            (item["case"]["caseId"], item["attemptIndex"], item["profile"]): item
            for item in attempts
        }
        rows = []
        for case_id, index, profile in sorted(keyed):
            if profile != "full-r0-r3":
                continue
            full = keyed[(case_id, index, profile)]
            legacy = keyed.get((case_id, index, "legacy-low-level"))
            if legacy is None:
                continue
            rows.append(
                {
                    "caseId": case_id,
                    "attemptIndex": index,
                    "fairnessMatched": full.get("fairness") == legacy.get("fairness"),
                    "taskCompletionDelta": int(full["grade"]["groundTruthCorrect"])
                    - int(legacy["grade"]["groundTruthCorrect"]),
                    "semanticCorrectnessDelta": int(full["grade"]["semanticResultCorrect"])
                    - int(legacy["grade"]["semanticResultCorrect"]),
                    "trustedCompletionDelta": int(full["grade"]["trustedCompletion"])
                    - int(legacy["grade"]["trustedCompletion"]),
                    "falseSuccessDelta": int(full["grade"]["falseSuccess"])
                    - int(legacy["grade"]["falseSuccess"]),
                    "toolCallsDelta": full["usage"]["toolCalls"] - legacy["usage"]["toolCalls"],
                    "highLevelToolCallsDelta": full["usage"].get("highLevelToolCalls", 0)
                    - legacy["usage"].get("highLevelToolCalls", 0),
                    "elapsedMsDelta": full["usage"]["elapsedMs"] - legacy["usage"]["elapsedMs"],
                    "humanInterventionsDelta": full["usage"].get("humanInterventions", 0)
                    - legacy["usage"].get("humanInterventions", 0),
                    "totalTokensDelta": _numeric_delta(
                        full["usage"].get("totalTokens"),
                        legacy["usage"].get("totalTokens"),
                    ),
                    "wrongAssetDelta": int(full["grade"]["wrongAsset"])
                    - int(legacy["grade"]["wrongAsset"]),
                    "unintendedChangeDelta": int(full["grade"]["unintendedChange"])
                    - int(legacy["grade"]["unintendedChange"]),
                    "staleDetectionDelta": int(full["grade"]["staleDirtyDetected"])
                    - int(legacy["grade"]["staleDirtyDetected"]),
                    "recoverySuccessDelta": int(full["grade"]["recoverySucceeded"])
                    - int(legacy["grade"]["recoverySucceeded"]),
                }
            )
        delta_fields = [key for key in rows[0] if key.endswith("Delta")] if rows else []
        return {
            "pairedAttempts": len(rows),
            "fairnessMismatches": sum(not row["fairnessMatched"] for row in rows),
            "meanDeltas": {
                field: round(mean(values), 6) if values else None
                for field in delta_fields
                if (values := [row[field] for row in rows if isinstance(row[field], (int, float))])
            },
            "cases": rows,
        }

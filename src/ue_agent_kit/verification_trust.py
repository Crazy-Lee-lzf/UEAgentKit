from __future__ import annotations

import copy
import hashlib
import json
import re
from typing import Any, Iterable

from .backups import validate_rollback
from .query_protocol import normalize_output_token_budget
from .semantic_diff_workflow import _load_plan
from .verification_evidence import VerificationEvidenceStore


VERIFICATION_TRUST_SCHEMA_VERSION = "1.0"
VERIFICATION_RULE_VERSION = "r3.1"
MAX_VERIFICATION_ASSETS = 8
MAX_VERIFICATION_ASSERTIONS = 128
MAX_VERIFICATION_EVIDENCE_REFS = 128
MAX_REQUIRED_AUTOMATION_TESTS = 8
MAX_EXTRA_VALIDATION_ASSETS = 8
MAX_CONSUMER_COMPILE_ASSERTIONS = 8
ASSERTION_REQUIREMENTS = ("required", "recommended", "informational")
ASSERTION_STATUSES = ("pass", "fail", "unknown", "not-applicable")
ASSERTION_FAMILIES = (
    "persistence", "semantic", "freshness", "compile",
    "data-validation", "reference-impact", "automation", "recovery",
)
TRUST_VERDICT_STATES = ("verified", "suspicious", "failed", "insufficient-evidence")
REFERENCE_SENSITIVE_OPERATIONS = {
    "setAssetReferenceProperty", "removeDataTableRow", "renameDataTableRow",
}
BLUEPRINT_OPERATIONS = {"setVariableDefault", "setComponentProperty", "setPinDefault"}
UNVERIFIED_DIMENSIONS = (
    "runtime-gameplay-behavior", "visual-correctness", "performance-regression",
    "network-replication-behavior", "external-system-behavior", "runtime-execution-trace",
)
_EXACT_OBJECT_PATH = re.compile(r"^/Game/(?:[A-Za-z0-9_]+/)*([A-Za-z0-9_]+)\.\1$")


class VerificationTrustError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(prefix: str, *parts: Any) -> str:
    digest = hashlib.sha256(_canonical_json(parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest}"


def _estimated_tokens(value: Any) -> int:
    return max(1, (len(_canonical_json(value).encode("utf-8")) + 3) // 4)


def _assertion_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    priority = {"required": 0, "recommended": 1, "informational": 2}
    return (
        priority.get(str(item.get("requirement")), 9),
        str(item.get("kind", "")),
        str(item.get("subject", "")).casefold(),
        str(item.get("assertionId", "")),
    )


def _validate_exact_assets(values: list[str] | None, *, field_name: str, maximum: int) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > maximum:
        raise VerificationTrustError(
            "verification-trust-invalid-arguments",
            f"{field_name} must contain at most {maximum} items.",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or _EXACT_OBJECT_PATH.fullmatch(value) is None:
            raise VerificationTrustError(
                "verification-trust-invalid-arguments",
                f"{field_name} must contain exact /Game Object Paths.",
            )
        folded = value.casefold()
        if folded in seen:
            raise VerificationTrustError(
                "verification-trust-invalid-arguments",
                f"{field_name} must not contain duplicates.",
            )
        seen.add(folded)
        normalized.append(value)
    return sorted(normalized, key=str.casefold)


def _validate_automation_tests(values: list[str] | None) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list) or len(values) > MAX_REQUIRED_AUTOMATION_TESTS:
        raise VerificationTrustError(
            "verification-trust-invalid-arguments",
            f"required_automation_tests must contain at most {MAX_REQUIRED_AUTOMATION_TESTS} items.",
        )
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value or value != value.strip() or len(value) > 512:
            raise VerificationTrustError(
                "verification-trust-invalid-arguments",
                "required_automation_tests must contain exact non-empty registered test names.",
            )
        folded = value.casefold()
        if folded in seen:
            raise VerificationTrustError(
                "verification-trust-invalid-arguments",
                "required_automation_tests must not contain duplicates.",
            )
        seen.add(folded)
        normalized.append(value)
    return sorted(normalized, key=str.casefold)


def _validate_request(
    *,
    impact_depth: int,
    required_automation_tests: list[str] | None,
    extra_validation_assets: list[str] | None,
    max_output_tokens: int,
) -> dict[str, Any]:
    if isinstance(impact_depth, bool) or not isinstance(impact_depth, int) or not 0 <= impact_depth <= 2:
        raise VerificationTrustError(
            "verification-trust-invalid-arguments",
            "impact_depth must be an integer from 0 through 2.",
        )
    return {
        "impactDepth": impact_depth,
        "requiredAutomationTests": _validate_automation_tests(required_automation_tests),
        "extraValidationAssets": _validate_exact_assets(
            extra_validation_assets,
            field_name="extra_validation_assets",
            maximum=MAX_EXTRA_VALIDATION_ASSETS,
        ),
        "maxOutputTokens": normalize_output_token_budget(max_output_tokens),
    }


def _assertion(
    *,
    change_set_id: str,
    kind: str,
    subject: str,
    requirement: str,
    source_rule: str,
    evidence_kinds: Iterable[str],
    status: str = "unknown",
    applicability: str = "insufficient-binding",
    reason_code: str = "trust-required-evidence-missing",
    message: str = "Applicable deterministic evidence has not been observed.",
    next_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "assertionId": _stable_id(
            "assert",
            VERIFICATION_RULE_VERSION,
            change_set_id,
            kind,
            subject,
            requirement,
        ),
        "kind": kind,
        "subject": subject,
        "requirement": requirement,
        "status": status,
        "applicability": applicability,
        "sourceRule": source_rule,
        "requiredEvidenceKinds": list(evidence_kinds),
        "evidenceRefs": [],
        "reasonCode": reason_code,
        "message": message,
        "nextAction": next_action or {},
    }


def _next_action(tool: str, arguments: dict[str, Any], reason: str) -> dict[str, Any]:
    return {"tool": tool, "arguments": copy.deepcopy(arguments), "reason": reason}


def _change_set_snapshot(workflow: Any, change_set_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with workflow._lock:
        record = copy.deepcopy(workflow._resolve_change_set(change_set_id))
        workflow._reconcile_change_set(record, persist=False)
        operations: list[dict[str, Any]] = []
        for operation in record.operations:
            item = {
                "operationId": operation.receipt,
                "receipt": operation.receipt,
                "planId": operation.plan_id,
                "assetPath": operation.asset_path,
                "operation": operation.operation,
                "editorSessionId": operation.editor_session_id,
                "status": operation.status,
                "saveReceipt": operation.save_receipt,
                "noOp": operation.status == "no-op",
                "intent": {},
            }
            plan_record = workflow._plans.get(operation.plan_id)
            plan = plan_record.patch if plan_record is not None else _load_plan(workflow, operation.plan_id)
            if isinstance(plan, dict):
                item["planDigest"] = (
                    plan_record.digest
                    if plan_record is not None
                    else "sha256:" + hashlib.sha256(_canonical_json(plan).encode("utf-8")).hexdigest()
                )
                assets = plan.get("assets", [])
                if isinstance(assets, list):
                    for asset in assets:
                        if not isinstance(asset, dict) or asset.get("assetPath") != operation.asset_path:
                            continue
                        patch_operations = asset.get("operations", [])
                        if isinstance(patch_operations, list):
                            item["intents"] = [
                                copy.deepcopy(candidate)
                                for candidate in patch_operations
                                if isinstance(candidate, dict)
                            ]
                            match = next(
                                (
                                    candidate
                                    for candidate in patch_operations
                                    if isinstance(candidate, dict)
                                    and candidate.get("operation") == operation.operation
                                ),
                                None,
                            )
                            if match is not None:
                                item["intent"] = copy.deepcopy(match)
                        item["expectedRevision"] = str(asset.get("expectedRevision", ""))
                        item["assetClass"] = str(asset.get("expectedAssetClass", ""))
                        break
            operations.append(item)
        change_set = {
            "changeSetId": record.change_set_id,
            "taskId": record.task_id,
            "editorSessionId": record.editor_session_id,
            "title": record.title,
            "status": record.status,
            "createdAtUtc": record.created_at_utc,
            "updatedAtUtc": record.updated_at_utc,
            "operationCount": len(operations),
            "affectedAssets": sorted(
                {item["assetPath"] for item in operations if item["assetPath"]},
                key=str.casefold,
            ),
        }
        return change_set, operations


def _impact_scope(workflow: Any, affected_assets: list[str], impact_depth: int) -> dict[str, Any]:
    if impact_depth == 0 or not affected_assets:
        return {
            "summary": {"truncated": False, "truncationReasons": []},
            "directConsumers": [],
            "indirectConsumers": [],
            "validationTargets": [],
            "analysisGaps": [],
            "risks": [],
        }
    return workflow.index_service.analyze_change_impact(
        affected_assets,
        max_depth=impact_depth,
        max_consumers=100,
        max_edges=1000,
        max_paths=100,
        max_output_tokens=32768,
    )


def _is_blueprint_class(value: str) -> bool:
    return "blueprint" in value.casefold()


def _operation_names(operation: dict[str, Any]) -> set[str]:
    intents = operation.get("intents")
    if isinstance(intents, list):
        names = {
            str(item.get("operation", ""))
            for item in intents
            if isinstance(item, dict) and item.get("operation")
        }
        if names:
            return names
    name = str(operation.get("operation", ""))
    return {name} if name else set()


def _build_assertions(
    *,
    change_set: dict[str, Any],
    operations: list[dict[str, Any]],
    impact: dict[str, Any],
    request: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    change_set_id = str(change_set["changeSetId"])
    assertions: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        grouped.setdefault(str(operation["assetPath"]), []).append(operation)

    for asset_path in sorted(grouped, key=str.casefold):
        asset_operations = grouped[asset_path]
        real_operations = [item for item in asset_operations if not item["noOp"]]
        no_op_only = not real_operations
        semantic_stage = "persisted" if no_op_only else "verified"
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="freshness",
            subject=asset_path,
            requirement="required",
            source_rule="all-operation-final-revision" if real_operations else "expected-no-op-baseline-revision",
            evidence_kinds=("semantic-diff-revision",),
            next_action=_next_action(
                "ue_analyze_semantic_diff",
                {"change_set_id": change_set_id, "stage": semantic_stage},
                "Refresh exact semantic revision evidence.",
            ),
        ))
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="persistence",
            subject=asset_path,
            requirement="required" if real_operations else "informational",
            source_rule="actual-write-persisted" if real_operations else "expected-no-op-no-persistence-required",
            evidence_kinds=("independent-canonical", "authorized-save", "commit-report"),
            status="unknown" if real_operations else "not-applicable",
            applicability="insufficient-binding" if real_operations else "not-applicable",
            reason_code="trust-required-evidence-missing" if real_operations else "expected-no-op",
            message=(
                "A real write requires exact persisted and independently reloadable evidence."
                if real_operations else "The expected no-op performed no write, so persistence is not applicable."
            ),
            next_action=(
                _next_action("ue_verify_asset", {}, "Complete independent persisted verification, then retry.")
                if real_operations else {}
            ),
        ))
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="semantic",
            subject=asset_path,
            requirement="required",
            source_rule="r2-verified-semantic-diff" if real_operations else "r2-persisted-expected-no-op",
            evidence_kinds=("semantic-diff",),
            next_action=_next_action(
                "ue_analyze_semantic_diff",
                {"change_set_id": change_set_id, "stage": semantic_stage},
                "Obtain complete R2 semantic evidence for this Change Set.",
            ),
        ))
        if not real_operations:
            continue
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="data-validation",
            subject=asset_path,
            requirement="required",
            source_rule="actual-write-data-validation",
            evidence_kinds=("data-validation",),
            next_action=_next_action(
                "ue_validate_asset",
                {"asset_path": asset_path},
                "Run Unreal Data Validation for the exact final asset.",
            ),
        ))
        if any(_operation_names(item).intersection(BLUEPRINT_OPERATIONS) for item in real_operations):
            assertions.append(_assertion(
                change_set_id=change_set_id,
                kind="compile",
                subject=asset_path,
                requirement="required",
                source_rule="blueprint-narrow-write-explicit-compile",
                evidence_kinds=("compile",),
                next_action=_next_action(
                    "ue_compile_blueprint",
                    {"asset_path": asset_path},
                    "Explicitly compile the exact final Blueprint revision.",
                ),
            ))
        if any(_operation_names(item).intersection(REFERENCE_SENSITIVE_OPERATIONS) for item in real_operations):
            assertions.append(_assertion(
                change_set_id=change_set_id,
                kind="reference-impact",
                subject=asset_path,
                requirement="required",
                source_rule="reference-sensitive-bounded-impact-scope",
                evidence_kinds=("r1-impact-scope",),
                next_action=_next_action(
                    "ue_analyze_change_impact",
                    {"target_asset_paths": [asset_path], "max_depth": request["impactDepth"] or 1},
                    "Establish a complete bounded static-reference validation scope.",
                ),
            ))

    existing_validation = {
        item["subject"].casefold()
        for item in assertions
        if item["kind"] == "data-validation"
    }
    for asset_path in request["extraValidationAssets"]:
        if asset_path.casefold() in existing_validation:
            continue
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="data-validation",
            subject=asset_path,
            requirement="required",
            source_rule="caller-extra-validation-asset",
            evidence_kinds=("data-validation",),
            next_action=_next_action(
                "ue_validate_asset",
                {"asset_path": asset_path},
                "Validate the exact requested asset.",
            ),
        ))
    for test_name in request["requiredAutomationTests"]:
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="automation",
            subject=test_name,
            requirement="required",
            source_rule="caller-required-exact-automation-test",
            evidence_kinds=("automation",),
            next_action=_next_action(
                "ue_run_automation_test",
                {"test_name": test_name},
                "Run the exact required Automation Test.",
            ),
        ))

    reference_assets = {
        str(item["assetPath"])
        for item in operations
        if not item["noOp"] and _operation_names(item).intersection(REFERENCE_SENSITIVE_OPERATIONS)
    }
    direct_blueprints = [
        item
        for item in impact.get("directConsumers", [])
        if isinstance(item, dict)
        and _is_blueprint_class(str(item.get("assetClass", "")))
        and reference_assets.intersection(str(value) for value in item.get("impactedTargets", []))
    ]
    direct_blueprints.sort(key=lambda item: str(item.get("assetPath", "")).casefold())
    for consumer in direct_blueprints[:MAX_CONSUMER_COMPILE_ASSERTIONS]:
        consumer_path = str(consumer.get("assetPath", ""))
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="compile",
            subject=consumer_path,
            requirement="required",
            source_rule="reference-sensitive-direct-blueprint-consumer",
            evidence_kinds=("compile", "r1-impact-scope"),
            next_action=_next_action(
                "ue_compile_blueprint",
                {"asset_path": consumer_path},
                "Compile the exact direct Blueprint consumer discovered by R1.",
            ),
        ))
    if len(direct_blueprints) > MAX_CONSUMER_COMPILE_ASSERTIONS:
        risks.append({
            "code": "trust-reference-scope-unknown",
            "severity": "high",
            "blocking": True,
            "subject": change_set_id,
            "message": "Direct Blueprint consumers exceeded the fixed compile assertion bound.",
        })

    if any(not item["noOp"] for item in operations):
        assertions.append(_assertion(
            change_set_id=change_set_id,
            kind="recovery",
            subject=change_set_id,
            requirement="informational",
            source_rule="backup-or-rollback-readiness",
            evidence_kinds=("backup-manifest", "rollback-dry-run"),
            next_action={},
        ))

    impact_summary = impact.get("summary") if isinstance(impact.get("summary"), dict) else {}
    impact_truncated = bool(impact_summary.get("truncated"))
    for risk in impact.get("risks", []):
        if not isinstance(risk, dict):
            continue
        source_code = str(risk.get("code", ""))
        blocking = bool(reference_assets and source_code in {
            "impact-analysis-truncated", "impact-target-not-indexed",
        })
        risks.append({
            "code": "trust-impact-truncated" if source_code == "impact-analysis-truncated" else source_code,
            "severity": "high" if blocking else str(risk.get("severity", "medium")),
            "blocking": blocking,
            "subject": str(risk.get("subject") or risk.get("assetPath") or change_set_id),
            "message": str(risk.get("message", "R1 reported a bounded static-reference risk.")),
        })
    if reference_assets and (request["impactDepth"] == 0 or impact_truncated):
        risks.append({
            "code": "trust-reference-scope-unknown",
            "severity": "high",
            "blocking": True,
            "subject": change_set_id,
            "message": "Reference-sensitive operations require a complete bounded R1 scope.",
        })

    deduped_risks = {
        (item["code"], item["subject"]): item
        for item in risks
    }
    assertions.sort(key=_assertion_sort_key)
    if len(assertions) > MAX_VERIFICATION_ASSERTIONS:
        assertions = assertions[:MAX_VERIFICATION_ASSERTIONS]
        deduped_risks[("trust-verification-scope-truncated", change_set_id)] = {
            "code": "trust-verification-scope-truncated",
            "severity": "high",
            "blocking": True,
            "subject": change_set_id,
            "message": "Verification assertions exceeded the fixed response bound.",
        }
    return assertions, sorted(
        deduped_risks.values(),
        key=lambda item: (not bool(item["blocking"]), str(item["code"]), str(item["subject"]).casefold()),
    )


def _summary(assertions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "assertionCount": len(assertions),
        "required": sum(item["requirement"] == "required" for item in assertions),
        "recommended": sum(item["requirement"] == "recommended" for item in assertions),
        "informational": sum(item["requirement"] == "informational" for item in assertions),
        "pass": sum(item["status"] == "pass" for item in assertions),
        "fail": sum(item["status"] == "fail" for item in assertions),
        "unknown": sum(item["status"] == "unknown" for item in assertions),
        "notApplicable": sum(item["status"] == "not-applicable" for item in assertions),
    }


def _unique_actions(assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: dict[str, dict[str, Any]] = {}
    for assertion in assertions:
        action = assertion.get("nextAction")
        if assertion.get("status") not in {"unknown", "fail"} or not isinstance(action, dict) or not action.get("tool"):
            continue
        key = _canonical_json({"tool": action["tool"], "arguments": action.get("arguments", {})})
        actions[key] = copy.deepcopy(action)
    return [actions[key] for key in sorted(actions)]


def _trim_response(response: dict[str, Any], max_output_tokens: int) -> dict[str, Any]:
    result = copy.deepcopy(response)
    reasons: list[str] = []
    result["outputBudget"] = {
        "maxOutputTokens": max_output_tokens,
        "estimatedTokens": 0,
        "truncated": False,
        "truncationReasons": [],
    }
    if _estimated_tokens(result) > max_output_tokens:
        for evidence in result.get("evidence", []):
            if isinstance(evidence, dict):
                evidence.pop("diagnostics", None)
                evidence.pop("details", None)
        reasons.append("evidence-details")
    if _estimated_tokens(result) > max_output_tokens:
        for assertion in result.get("assertions", []):
            if isinstance(assertion, dict) and assertion.get("requirement") != "required":
                assertion.pop("message", None)
                assertion.pop("sourceRule", None)
                assertion.pop("requiredEvidenceKinds", None)
        reasons.append("optional-assertion-details")
    if _estimated_tokens(result) > max_output_tokens:
        assertions = result.get("assertions")
        if isinstance(assertions, list):
            result["assertions"] = [item for item in assertions if item.get("requirement") != "informational"]
        reasons.append("informational-assertions")
    if _estimated_tokens(result) > max_output_tokens:
        risks = result.get("risks", result.get("unresolvedRisks"))
        if isinstance(risks, list):
            for risk in risks:
                if isinstance(risk, dict) and not risk.get("blocking"):
                    risk.pop("message", None)
        reasons.append("non-blocking-risk-details")
    estimated = _estimated_tokens(result)
    if estimated > max_output_tokens:
        reasons.append("minimum-envelope-exceeds-budget")
    result["outputBudget"] = {
        "maxOutputTokens": max_output_tokens,
        "estimatedTokens": estimated,
        "truncated": bool(reasons),
        "truncationReasons": reasons,
    }
    return result


def build_verification_plan(
    workflow: Any,
    change_set_id: str,
    *,
    impact_depth: int = 1,
    required_automation_tests: list[str] | None = None,
    extra_validation_assets: list[str] | None = None,
    max_output_tokens: int = 4096,
) -> dict[str, Any]:
    request = _validate_request(
        impact_depth=impact_depth,
        required_automation_tests=required_automation_tests,
        extra_validation_assets=extra_validation_assets,
        max_output_tokens=max_output_tokens,
    )
    change_set, operations = _change_set_snapshot(workflow, change_set_id)
    if not operations:
        raise VerificationTrustError(
            "verification-plan-empty-change-set",
            "The explicit Change Set has no bound operations to verify.",
        )
    affected_all = list(change_set["affectedAssets"])
    affected = affected_all[:MAX_VERIFICATION_ASSETS]
    impact = _impact_scope(workflow, affected, request["impactDepth"])
    assertions, risks = _build_assertions(
        change_set=change_set,
        operations=operations,
        impact=impact,
        request=request,
    )
    if len(affected_all) > MAX_VERIFICATION_ASSETS:
        risks.insert(0, {
            "code": "trust-verification-scope-truncated",
            "severity": "high",
            "blocking": True,
            "subject": change_set_id,
            "message": "Affected assets exceeded the fixed Verification Plan bound.",
        })
    fingerprint_input = {
        "ruleVersion": VERIFICATION_RULE_VERSION,
        "changeSetId": change_set_id,
        "operations": operations,
        "impactDepth": request["impactDepth"],
        "requiredAutomationTests": request["requiredAutomationTests"],
        "extraValidationAssets": request["extraValidationAssets"],
    }
    fingerprint = "sha256:" + hashlib.sha256(
        _canonical_json(fingerprint_input).encode("utf-8")
    ).hexdigest()
    response = {
        "schemaVersion": VERIFICATION_TRUST_SCHEMA_VERSION,
        "tool": "ue_build_verification_plan",
        "ok": True,
        "readOnly": True,
        "request": {"changeSetId": change_set_id, **request},
        "changeSet": change_set,
        "planId": _stable_id("verification_plan", fingerprint),
        "planFingerprint": fingerprint,
        "scope": {
            "affectedAssets": affected,
            "affectedAssetCount": len(affected_all),
            "impactDepth": request["impactDepth"],
            "validationTargets": copy.deepcopy(impact.get("validationTargets", []))[:32],
            "requiredAutomationTests": request["requiredAutomationTests"],
            "unverifiedDimensions": list(UNVERIFIED_DIMENSIONS),
        },
        "assertions": assertions,
        "summary": _summary(assertions),
        "risks": risks,
        "nextActions": _unique_actions(assertions),
    }
    return _trim_response(response, request["maxOutputTokens"])


def _set_assertion(
    assertion: dict[str, Any],
    *,
    status: str,
    applicability: str,
    reason_code: str,
    message: str,
    evidence_refs: list[str] | None = None,
) -> None:
    assertion["status"] = status
    assertion["applicability"] = applicability
    assertion["reasonCode"] = reason_code
    assertion["message"] = message
    assertion["evidenceRefs"] = list(evidence_refs or [])
    if status in {"pass", "not-applicable"}:
        assertion["nextAction"] = {}


def _semantic_result(workflow: Any, change_set_id: str, operations: list[dict[str, Any]]) -> dict[str, Any] | None:
    real_operations = [item for item in operations if not item["noOp"]]
    if real_operations and len(real_operations) != len(operations):
        return None
    stage = "verified" if real_operations else "persisted"
    try:
        return workflow.analyze_semantic_diff(
            change_set_id,
            stage=stage,
            include_unchanged=True,
            max_changes=128,
            max_output_tokens=32768,
        )
    except (OSError, RuntimeError, ValueError):
        return None


def _semantic_assets(semantic: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("assetPath", "")): item
        for item in (semantic or {}).get("assets", [])
        if isinstance(item, dict)
    }


def _current_asset_applicability(
    workflow: Any,
    asset_path: str,
    expected_revision: str = "",
) -> tuple[bool, str, str]:
    try:
        state = workflow.get_asset_state(asset_path)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False, "", "trust-current-asset-state-unavailable"
    sources = state.get("sources") if isinstance(state, dict) else None
    sources = sources if isinstance(sources, dict) else {}
    disk = sources.get("disk")
    disk = disk if isinstance(disk, dict) else {}
    memory = sources.get("memory")
    memory = memory if isinstance(memory, dict) else {}
    disk_revision = str(disk.get("revision", ""))
    if not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", disk_revision):
        return False, disk_revision, "trust-current-disk-revision-unavailable"
    if memory.get("packageDirty") is True:
        return False, disk_revision, "trust-current-memory-dirty"
    if expected_revision and disk_revision != expected_revision:
        return False, disk_revision, "trust-current-revision-mismatch"
    return True, disk_revision, "current-asset-revision-applicable"


def _apply_semantic_assertions(
    *,
    workflow: Any,
    assertions: list[dict[str, Any]],
    semantic: dict[str, Any] | None,
    operations: list[dict[str, Any]],
    plan_fingerprint: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str], list[dict[str, Any]]]:
    unexpected: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    revisions: dict[str, str] = {}
    evidence: list[dict[str, Any]] = []
    assets = _semantic_assets(semantic)
    selected_stage = str((semantic or {}).get("evidenceStage", {}).get("selected", ""))
    expected_assets = {str(operation["assetPath"]) for operation in operations}
    required_stage = "verified" if any(not operation["noOp"] for operation in operations) else "persisted"
    global_gaps = [
        item for item in (semantic or {}).get("analysisGaps", [])
        if isinstance(item, dict)
    ]
    global_blocking = any(
        str(item.get("code", "")) == "semantic-diff-truncated"
        for item in global_gaps
    )
    if selected_stage != required_stage:
        global_blocking = True
        global_gaps.append({
            "code": "trust-semantic-stage-mismatch",
            "assetPath": "",
            "message": f"Required {required_stage} semantic evidence is unavailable.",
        })
    semantic_summary = (semantic or {}).get("summary")
    semantic_summary = semantic_summary if isinstance(semantic_summary, dict) else {}
    returned_count = int(semantic_summary.get("returnedAssetCount", len(assets)) or 0)
    total_count = int(semantic_summary.get("totalAssetCount", len(expected_assets)) or 0)
    if set(assets) != expected_assets or returned_count != len(expected_assets) or total_count != len(expected_assets):
        global_blocking = True
        global_gaps.append({
            "code": "trust-semantic-scope-incomplete",
            "assetPath": "",
            "message": "R2 Semantic Diff did not return the complete Change Set asset scope.",
        })
    output_budget = (semantic or {}).get("outputBudget")
    output_budget = output_budget if isinstance(output_budget, dict) else {}
    if output_budget.get("truncated") is True:
        global_blocking = True
        global_gaps.append({
            "code": "trust-semantic-output-truncated",
            "assetPath": "",
            "message": "R2 Semantic Diff output was truncated and cannot close Required coverage.",
        })
    seen_gaps: set[str] = set()
    for item in global_gaps:
        key = _canonical_json(item)
        if key not in seen_gaps:
            gaps.append(copy.deepcopy(item))
            seen_gaps.add(key)
    for asset in assets.values():
        for item in asset.get("analysisGaps", []):
            if not isinstance(item, dict):
                continue
            key = _canonical_json(item)
            if key not in seen_gaps:
                gaps.append(copy.deepcopy(item))
                seen_gaps.add(key)
        unexpected.extend(copy.deepcopy(asset.get("unexpectedChanges", [])))

    for assertion in assertions:
        if assertion["kind"] not in {"semantic", "freshness", "persistence"}:
            continue
        subject = str(assertion["subject"])
        asset = assets.get(subject)
        evidence_id = _stable_id("semantic_evidence", plan_fingerprint, subject, selected_stage)
        if asset is None:
            if assertion["kind"] == "persistence" and assertion["status"] == "not-applicable":
                continue
            _set_assertion(
                assertion,
                status="unknown",
                applicability="insufficient-binding",
                reason_code="trust-required-evidence-missing",
                message="The required R2 semantic stage is unavailable for this asset.",
            )
            continue
        asset_gaps = [item for item in asset.get("analysisGaps", []) if isinstance(item, dict)]
        final_revision = str(
            asset.get("stageEvidenceRevision")
            or asset.get("afterRevision")
            or asset.get("beforeRevision")
            or ""
        )
        if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", final_revision):
            revisions[subject] = final_revision
        else:
            final_revision = ""
        current_applies, current_revision, current_reason = _current_asset_applicability(
            workflow,
            subject,
            final_revision,
        )
        if evidence_id not in {item.get("evidenceId") for item in evidence}:
            evidence.append({
                "evidenceId": evidence_id,
                "kind": "semantic-diff",
                "sourceTool": "ue_analyze_semantic_diff",
                "subject": subject,
                "stage": selected_stage,
                "applicability": "exact-asset-revision" if final_revision else "insufficient-binding",
                "revision": final_revision,
                "currentDiskRevision": current_revision,
            })
        stale = any(str(item.get("code", "")) in {
            "semantic-diff-evidence-stale", "revision-evidence-unavailable",
        } for item in asset_gaps)
        if assertion["kind"] == "freshness":
            passing = bool(final_revision and not stale and not global_blocking and current_applies)
            _set_assertion(
                assertion,
                status="pass" if passing else "unknown",
                applicability="exact-asset-revision" if passing else "insufficient-binding",
                reason_code=(
                    "freshness-evidence-applicable"
                    if passing
                    else current_reason if not current_applies else "trust-evidence-stale"
                ),
                message=(
                    "Plan and selected semantic evidence revisions are mechanically consistent."
                    if passing else "Semantic evidence is missing an applicable stable final revision."
                ),
                evidence_refs=[evidence_id] if passing else [],
            )
            continue
        if assertion["kind"] == "persistence":
            if assertion["status"] == "not-applicable":
                continue
            passing = bool(
                selected_stage == "verified"
                and final_revision
                and not stale
                and not global_blocking
                and current_applies
            )
            _set_assertion(
                assertion,
                status="pass" if passing else "unknown",
                applicability="exact-asset-revision" if passing else "insufficient-binding",
                reason_code=(
                    "persistence-independently-verified"
                    if passing
                    else current_reason if not current_applies else "trust-required-evidence-missing"
                ),
                message=(
                    "Independent reload evidence proves the persisted final revision."
                    if passing else "Independent persisted evidence is incomplete or stale."
                ),
                evidence_refs=[evidence_id] if passing else [],
            )
            continue

        summary = asset.get("summary") if isinstance(asset.get("summary"), dict) else {}
        missing = int(summary.get("missingExpectedCount", 0) or 0)
        unexpected_count = int(summary.get("unexpectedCount", 0) or 0)
        expected = int(summary.get("expectedCount", 0) or 0)
        matched = int(summary.get("matchedCount", 0) or 0)
        if global_blocking or not current_applies:
            _set_assertion(
                assertion,
                status="unknown",
                applicability="insufficient-binding",
                reason_code=current_reason if not current_applies else "trust-required-evidence-missing",
                message="R2 semantic evidence is not applicable to the complete current asset scope.",
                evidence_refs=[evidence_id],
            )
        elif missing or unexpected_count:
            _set_assertion(
                assertion,
                status="fail",
                applicability="exact-change-set",
                reason_code=(
                    "trust-semantic-missing-expected-change"
                    if missing else "trust-semantic-unexpected-change"
                ),
                message="R2 observed missing or unexpected semantic changes.",
                evidence_refs=[evidence_id],
            )
        elif asset_gaps or expected == 0 or matched != expected:
            _set_assertion(
                assertion,
                status="unknown",
                applicability="insufficient-binding",
                reason_code="trust-required-evidence-missing",
                message="R2 semantic coverage is incomplete for a required assertion.",
                evidence_refs=[evidence_id],
            )
        else:
            _set_assertion(
                assertion,
                status="pass",
                applicability="exact-change-set",
                reason_code="semantic-expected-actual-matched",
                message="Every expected semantic change matched and no extra change was observed.",
                evidence_refs=[evidence_id],
            )
    return unexpected, gaps, revisions, evidence


def _record_applicability(
    record: dict[str, Any],
    *,
    subject: str,
    expected_session: str,
    expected_revision: str,
) -> tuple[bool, str, str]:
    if expected_session and record.get("editorSessionId") != expected_session:
        return False, "insufficient-binding", "trust-evidence-session-mismatch"
    if record.get("kind") == "automation":
        return True, "project-session", "evidence-project-session-applicable"
    if not expected_revision:
        return False, "insufficient-binding", "trust-current-disk-revision-unavailable"
    revision_set = record.get("revisionSet")
    if not isinstance(revision_set, list) or record.get("revisionCoverage") != "complete":
        return False, "insufficient-binding", "trust-evidence-revision-mismatch"
    matching = [
        item
        for item in revision_set
        if isinstance(item, dict) and item.get("assetPath") == subject
    ]
    if not matching:
        return False, "insufficient-binding", "trust-evidence-revision-mismatch"
    item = matching[0]
    revision = str(item.get("revisionAfter") or item.get("revision") or "")
    if not revision or item.get("revisionStable") is not True:
        return False, "insufficient-binding", "trust-evidence-stale"
    if item.get("packageDirtyAfter") is True or record.get("packageDirtyAfter") is True:
        return False, "insufficient-binding", "trust-evidence-stale"
    if expected_revision and revision != expected_revision:
        return False, "insufficient-binding", "trust-evidence-revision-mismatch"
    return True, "exact-asset-revision", "evidence-exact-revision-applicable"


def _captured_status(kind: str, record: dict[str, Any]) -> tuple[str, str, str]:
    result = str(record.get("result", "unknown"))
    details = record.get("details") if isinstance(record.get("details"), dict) else {}
    if kind == "compile":
        if record.get("succeeded") is True:
            return "pass", "compile-succeeded", "The exact applicable Blueprint compile succeeded."
        return "fail", "trust-compile-failed", "The exact applicable Blueprint compile failed."
    if kind == "automation":
        if record.get("succeeded") is True:
            return "pass", "automation-succeeded", "The exact required Automation Test succeeded."
        return "fail", "trust-automation-failed", "The exact required Automation Test failed."
    if result in {"valid", "valid-with-warnings"}:
        return "pass", "data-validation-succeeded", "Unreal Data Validation accepted the exact final asset."
    if result == "invalid":
        return "fail", "trust-validation-failed", "Unreal Data Validation reported the asset invalid."
    if (
        result == "not-validated"
        and int(details.get("numChecked", 0) or 0) == 0
        and int(details.get("numUnableToValidate", 0) or 0) == 0
        and int(details.get("numSkipped", 0) or 0) > 0
    ):
        return "not-applicable", "data-validation-not-applicable", "Unreal reported no applicable validator for the asset."
    return "unknown", "trust-required-evidence-missing", "Data Validation did not provide a conclusive applicable result."


def _apply_captured_assertions(
    *,
    workflow: Any,
    assertions: list[dict[str, Any]],
    store: VerificationEvidenceStore,
    expected_session: str,
    revisions: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    evidence: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    for assertion in assertions:
        kind = str(assertion["kind"])
        if kind not in {"compile", "data-validation", "automation"}:
            continue
        subject = str(assertion["subject"])
        records = store.find(kind=kind, subject=subject)
        if not records:
            continue
        expected_revision = revisions.get(subject, "")
        if kind != "automation":
            current_applies, current_revision, current_reason = _current_asset_applicability(
                workflow,
                subject,
                expected_revision,
            )
            if not current_applies:
                _set_assertion(
                    assertion,
                    status="unknown",
                    applicability="insufficient-binding",
                    reason_code=current_reason,
                    message="The current asset state does not bind to captured evidence.",
                )
                continue
            expected_revision = expected_revision or current_revision
        mismatch_reason = "trust-required-evidence-missing"
        applicable_record: dict[str, Any] | None = None
        applicability = "insufficient-binding"
        for record in records:
            applies, candidate_applicability, reason = _record_applicability(
                record,
                subject=subject,
                expected_session=expected_session,
                expected_revision=expected_revision,
            )
            if applies:
                applicable_record = record
                applicability = candidate_applicability
                break
            mismatch_reason = reason
        if applicable_record is None:
            _set_assertion(
                assertion,
                status="unknown",
                applicability="insufficient-binding",
                reason_code=mismatch_reason,
                message="Captured evidence exists but does not bind to the required project/session/revision.",
            )
            continue
        status, reason_code, message = _captured_status(kind, applicable_record)
        evidence_id = str(applicable_record["evidenceId"])
        _set_assertion(
            assertion,
            status=status,
            applicability=applicability if status != "not-applicable" else "not-applicable",
            reason_code=reason_code,
            message=message,
            evidence_refs=[evidence_id],
        )
        evidence_item = copy.deepcopy(applicable_record)
        evidence_item["applicability"] = applicability
        evidence.append(evidence_item)
        if applicable_record.get("warnings") is True:
            risks.append({
                "code": f"trust-{kind}-warning",
                "severity": "medium",
                "blocking": False,
                "subject": subject,
                "message": f"Applicable {kind} evidence completed with warnings.",
            })
        if applicable_record.get("diagnosticsTruncated") is True:
            risks.append({
                "code": "trust-evidence-diagnostics-truncated",
                "severity": "low",
                "blocking": False,
                "subject": subject,
                "message": "Evidence status is complete but returned diagnostics were bounded.",
            })
    evidence.sort(key=lambda item: (
        str(item.get("kind", "")),
        str(item.get("subject", "")).casefold(),
        str(item.get("evidenceId", "")),
    ))
    return evidence[:MAX_VERIFICATION_EVIDENCE_REFS], risks


def _apply_scope_assertions(
    *,
    assertions: list[dict[str, Any]],
    risks: list[dict[str, Any]],
    workflow: Any,
    operations: list[dict[str, Any]],
    plan_fingerprint: str,
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    blocking_scope = any(
        item.get("blocking") is True
        and item.get("code") in {"trust-impact-truncated", "trust-reference-scope-unknown"}
        for item in risks
    )
    for assertion in assertions:
        if assertion["kind"] == "reference-impact":
            evidence_id = _stable_id("impact_evidence", plan_fingerprint, assertion["subject"])
            if blocking_scope:
                _set_assertion(
                    assertion,
                    status="unknown",
                    applicability="insufficient-binding",
                    reason_code="trust-reference-scope-unknown",
                    message="The bounded R1 reference scope is incomplete for this reference-sensitive change.",
                )
            else:
                _set_assertion(
                    assertion,
                    status="pass",
                    applicability="exact-change-set",
                    reason_code="reference-impact-scope-established",
                    message="R1 established the complete bounded static-reference validation scope.",
                    evidence_refs=[evidence_id],
                )
                evidence.append({
                    "evidenceId": evidence_id,
                    "kind": "reference-impact",
                    "sourceTool": "ue_analyze_change_impact",
                    "subject": assertion["subject"],
                    "applicability": "exact-change-set",
                })
        elif assertion["kind"] == "recovery":
            material_ids: list[str] = []
            with workflow._lock:
                for operation in operations:
                    if operation["noOp"]:
                        continue
                    receipt = str(operation["receipt"])
                    if receipt.startswith("apply_"):
                        manifest = workflow.config.backup_root / f"{operation['planId']}.manifest.json"
                    else:
                        save_receipt = str(operation.get("saveReceipt", ""))
                        manifest = (
                            workflow.config.backup_root
                            / "live-save"
                            / save_receipt
                            / "rollback-manifest.json"
                        )
                    try:
                        validation = validate_rollback(
                            manifest,
                            workflow.config.policy_path,
                            workflow.config.project_path,
                            workflow.config.backup_root,
                        )
                    except (OSError, TypeError, ValueError):
                        continue
                    if (
                        validation.get("valid") is True
                        and validation.get("assetPath") == operation["assetPath"]
                    ):
                        material_ids.append(str(validation.get("manifestId") or manifest.name))
            expected_count = sum(not item["noOp"] for item in operations)
            if expected_count and len(material_ids) == expected_count:
                evidence_id = _stable_id("recovery_evidence", plan_fingerprint, sorted(material_ids))
                _set_assertion(
                    assertion,
                    status="pass",
                    applicability="exact-change-set",
                    reason_code="recovery-material-present",
                    message="Every real write has bounded backup material; rollback was not executed.",
                    evidence_refs=[evidence_id],
                )
                evidence.append({
                    "evidenceId": evidence_id,
                    "kind": "recovery",
                    "sourceTool": "workflow-backup-manifest",
                    "subject": assertion["subject"],
                    "applicability": "exact-change-set",
                    "materialCount": len(material_ids),
                })
    return evidence


def _verdict(assertions: list[dict[str, Any]], risks: list[dict[str, Any]]) -> tuple[str, list[str], str]:
    required = [item for item in assertions if item["requirement"] == "required"]
    required_fail = [item for item in required if item["status"] == "fail"]
    required_unknown = [item for item in required if item["status"] == "unknown"]
    blocking = [item for item in risks if item.get("blocking") is True]
    recommended_unresolved = [
        item
        for item in assertions
        if item["requirement"] == "recommended" and item["status"] in {"fail", "unknown"}
    ]
    reason_codes = {
        str(item.get("reasonCode", ""))
        for item in required_fail + required_unknown + recommended_unresolved
        if item.get("reasonCode")
    }
    reason_codes.update(str(item.get("code", "")) for item in risks if item.get("code"))
    if required_fail:
        return "failed", sorted(reason_codes), "Required deterministic evidence proves at least one verification assertion failed."
    if required_unknown or blocking:
        return "insufficient-evidence", sorted(reason_codes), "Required verification obligations remain unknown or have invalid applicability."
    if recommended_unresolved or risks:
        return "suspicious", sorted(reason_codes), "All required assertions passed, but deterministic non-blocking risks remain unresolved."
    return (
        "verified",
        [],
        "Verified against the generated Verification Plan and currently available deterministic evidence.",
    )


def evaluate_trust_verdict(
    workflow: Any,
    evidence_store: VerificationEvidenceStore,
    change_set_id: str,
    *,
    impact_depth: int = 1,
    required_automation_tests: list[str] | None = None,
    extra_validation_assets: list[str] | None = None,
    max_output_tokens: int = 4096,
) -> dict[str, Any]:
    request = _validate_request(
        impact_depth=impact_depth,
        required_automation_tests=required_automation_tests,
        extra_validation_assets=extra_validation_assets,
        max_output_tokens=max_output_tokens,
    )
    plan = build_verification_plan(
        workflow,
        change_set_id,
        impact_depth=request["impactDepth"],
        required_automation_tests=request["requiredAutomationTests"],
        extra_validation_assets=request["extraValidationAssets"],
        max_output_tokens=32768,
    )
    change_set, operations = _change_set_snapshot(workflow, change_set_id)
    assertions = copy.deepcopy(plan["assertions"])
    risks = copy.deepcopy(plan["risks"])
    semantic = _semantic_result(workflow, change_set_id, operations)
    unexpected, gaps, revisions, semantic_evidence = _apply_semantic_assertions(
        workflow=workflow,
        assertions=assertions,
        semantic=semantic,
        operations=operations,
        plan_fingerprint=str(plan["planFingerprint"]),
    )
    captured_evidence, captured_risks = _apply_captured_assertions(
        workflow=workflow,
        assertions=assertions,
        store=evidence_store,
        expected_session=str(change_set.get("editorSessionId", "")),
        revisions=revisions,
    )
    risks.extend(captured_risks)
    scope_evidence = _apply_scope_assertions(
        assertions=assertions,
        risks=risks,
        workflow=workflow,
        operations=operations,
        plan_fingerprint=str(plan["planFingerprint"]),
    )
    assertions.sort(key=_assertion_sort_key)
    risk_map = {
        (str(item.get("code", "")), str(item.get("subject", ""))): item
        for item in risks
        if isinstance(item, dict)
    }
    risks = sorted(
        risk_map.values(),
        key=lambda item: (not bool(item.get("blocking")), str(item.get("code", "")), str(item.get("subject", "")).casefold()),
    )
    state, reason_codes, statement = _verdict(assertions, risks)
    evidence_map = {
        str(item.get("evidenceId", "")): item
        for item in [*semantic_evidence, *captured_evidence, *scope_evidence]
        if item.get("evidenceId")
    }
    evidence = [
        evidence_map[key]
        for key in sorted(evidence_map)
    ][:MAX_VERIFICATION_EVIDENCE_REFS]
    affected_assets = list(plan["scope"]["affectedAssets"])
    verified_assets = []
    for asset_path in affected_assets:
        asset_required = [
            item
            for item in assertions
            if item["requirement"] == "required" and item["subject"] == asset_path
        ]
        if asset_required and all(item["status"] in {"pass", "not-applicable"} for item in asset_required):
            verified_assets.append(asset_path)
    evidence_stages = []
    if semantic is not None:
        selected = str(semantic.get("evidenceStage", {}).get("selected", ""))
        if selected:
            evidence_stages.append(selected)
    summary = _summary(assertions)
    summary.update({
        "evidenceCount": len(evidence),
        "unresolvedRiskCount": len(risks),
        "analysisGapCount": len(gaps),
        "unexpectedChangeCount": len(unexpected),
    })
    response = {
        "schemaVersion": VERIFICATION_TRUST_SCHEMA_VERSION,
        "tool": "ue_evaluate_trust_verdict",
        "ok": True,
        "readOnly": True,
        "request": {"changeSetId": change_set_id, **request},
        "changeSet": change_set,
        "planFingerprint": plan["planFingerprint"],
        "verificationScope": {
            "changeSetId": change_set_id,
            "affectedAssets": affected_assets,
            "verifiedAssets": verified_assets,
            "referenceDepth": request["impactDepth"],
            "requiredAutomationTests": request["requiredAutomationTests"],
            "evidenceStages": evidence_stages,
            "unverifiedDimensions": list(UNVERIFIED_DIMENSIONS),
        },
        "verdict": {
            "state": state,
            "reasonCodes": reason_codes,
            "statement": statement,
        },
        "assertions": assertions,
        "evidence": evidence,
        "unresolvedRisks": risks,
        "analysisGaps": copy.deepcopy(gaps)[:32],
        "unexpectedChanges": copy.deepcopy(unexpected)[:32],
        "summary": summary,
        "recommendedNextActions": _unique_actions(assertions),
    }
    return _trim_response(response, request["maxOutputTokens"])

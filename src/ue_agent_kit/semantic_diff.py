from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from .query_protocol import estimate_json_tokens, normalize_output_token_budget


SEMANTIC_DIFF_SCHEMA_VERSION = "1.0"
SEMANTIC_DIFF_TOOL = "ue_analyze_semantic_diff"
MAX_SEMANTIC_DIFF_ASSETS = 8
MAX_SEMANTIC_DIFF_CHANGES = 128
MAX_SEMANTIC_DIFF_UNCHANGED = 64
MAX_SEMANTIC_DIFF_GAPS = 32
MIN_SEMANTIC_DIFF_CHANGES = 1
DEFAULT_SEMANTIC_DIFF_CHANGES = 64

SemanticStage = Literal["live", "persisted", "verified"]

SUPPORTED_STAGES = {"auto", "live", "persisted", "verified"}
SUPPORTED_CHANGE_KINDS = {
    "value-changed",
    "value-added",
    "value-removed",
    "row-added",
    "row-removed",
    "row-renamed",
    "override-added",
    "override-removed",
    "override-changed",
    "container-element-added",
    "container-element-removed",
    "container-element-changed",
    "unknown-change",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _normalize_reference(value: dict[str, Any]) -> dict[str, Any] | None:
    reference_type = value.get("referenceType")
    path = value.get("path")
    if isinstance(reference_type, str) and (isinstance(path, str) or path is None):
        return {"referenceType": reference_type, "path": path or ""}
    return None


def normalize_semantic_value(value: Any, *, value_type: str = "") -> Any:
    """Normalize supported UE values without repr- or display-string equality."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value == 0.0:
            return 0.0
        return float(format(value, ".15g"))
    if isinstance(value, dict):
        reference = _normalize_reference(value)
        if reference is not None:
            return reference
        wrapper_type = value.get("valueType")
        if isinstance(wrapper_type, str):
            normalized_type = wrapper_type.casefold()
            payload_key = "value" if "value" in value else {
                "struct": "fields",
                "array": "items",
                "set": "items",
                "map": "entries",
            }.get(normalized_type, "")
            if payload_key:
                payload = normalize_semantic_value(value.get(payload_key), value_type=wrapper_type)
                return {"valueType": wrapper_type, payload_key: payload}
        normalized = {
            str(key): normalize_semantic_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]).casefold())
        }
        return normalized
    if isinstance(value, (list, tuple)):
        normalized_items = [normalize_semantic_value(item) for item in value]
        normalized_type = value_type.casefold().replace("-", "")
        if normalized_type in {"set", "setvalue"}:
            return sorted(normalized_items, key=_canonical_json)
        if normalized_type in {"map", "mapvalue"}:
            return sorted(normalized_items, key=_map_item_identity)
        return normalized_items
    return {"unsupportedType": type(value).__name__}


def _map_item_identity(value: Any) -> str:
    if isinstance(value, dict):
        for key_name in ("key", "Key", "name", "Name"):
            if key_name in value:
                return _canonical_json(normalize_semantic_value(value[key_name]))
    return _canonical_json(value)


def semantic_equal(left: Any, right: Any, *, value_type: str = "") -> bool:
    return normalize_semantic_value(left, value_type=value_type) == normalize_semantic_value(
        right,
        value_type=value_type,
    )


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}_" + hashlib.sha256(payload).hexdigest()[:20]


@dataclass(frozen=True)
class SemanticOperationEvidence:
    operation_id: str
    asset_path: str
    operation: str
    target: dict[str, Any]
    expected_value: Any
    before_value: Any
    actual_value: Any
    stage: SemanticStage
    source: str
    expected_available: bool = True
    before_available: bool = True
    actual_available: bool = True
    before_revision: str = ""
    after_revision: str = ""
    stage_evidence_revision: str = ""
    asset_class: str = ""
    value_kind: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SemanticAssetEvidence:
    asset_path: str
    asset_class: str
    before_revision: str
    after_revision: str
    stage_evidence_revision: str
    operations: tuple[SemanticOperationEvidence, ...]
    unchanged_critical_fields: tuple[dict[str, Any], ...] = ()
    analysis_gaps: tuple[dict[str, Any], ...] = ()
    actual_only: tuple[SemanticOperationEvidence, ...] = ()


class SemanticDiffAdapter:
    domain = "unknown"
    operations: frozenset[str] = frozenset()

    def supports(self, operation: str) -> bool:
        return operation in self.operations

    def semantic_path(self, evidence: SemanticOperationEvidence) -> str:
        raise NotImplementedError

    def expected_value(self, evidence: SemanticOperationEvidence) -> Any:
        return normalize_semantic_value(evidence.expected_value, value_type=evidence.value_kind)

    def before_value(self, evidence: SemanticOperationEvidence) -> Any:
        return normalize_semantic_value(evidence.before_value, value_type=evidence.value_kind)

    def actual_value(self, evidence: SemanticOperationEvidence) -> Any:
        return normalize_semantic_value(evidence.actual_value, value_type=evidence.value_kind)

    def change_kind(self, before: Any, after: Any, evidence: SemanticOperationEvidence) -> str:
        if before is None and after is not None:
            return "value-added"
        if before is not None and after is None:
            return "value-removed"
        return "value-changed"


class DataAssetAdapter(SemanticDiffAdapter):
    domain = "data-asset-property"
    operations = frozenset({"setAssetProperty", "setAssetReferenceProperty", "setAssetStructuredProperty"})

    def semantic_path(self, evidence: SemanticOperationEvidence) -> str:
        return f"DataAsset.Property:{evidence.target.get('propertyPath', '<missing>')}"

    @staticmethod
    def _value(evidence: SemanticOperationEvidence, value: Any) -> Any:
        if evidence.operation == "setAssetReferenceProperty" and isinstance(value, dict):
            return normalize_semantic_value(value.get("path"))
        if evidence.operation == "setAssetStructuredProperty" and isinstance(evidence.expected_value, dict):
            value_type = str(evidence.expected_value.get("valueType", ""))
            expected_payload_key = next(
                (
                    key
                    for key in ("value", "fields", "items", "entries")
                    if key in evidence.expected_value
                ),
                "",
            )
            if isinstance(value, dict) and value.get("valueType") == value_type:
                actual_payload_key = next(
                    (key for key in ("value", "fields", "items", "entries") if key in value),
                    "",
                )
                if actual_payload_key:
                    value = value.get(actual_payload_key)
            elif value is evidence.expected_value and expected_payload_key:
                value = evidence.expected_value.get(expected_payload_key)
            return normalize_semantic_value(value, value_type=value_type)
        return normalize_semantic_value(value, value_type=evidence.value_kind)

    def expected_value(self, evidence: SemanticOperationEvidence) -> Any:
        return self._value(evidence, evidence.expected_value)

    def before_value(self, evidence: SemanticOperationEvidence) -> Any:
        return self._value(evidence, evidence.before_value)

    def actual_value(self, evidence: SemanticOperationEvidence) -> Any:
        return self._value(evidence, evidence.actual_value)


class DataTableAdapter(SemanticDiffAdapter):
    domain = "data-table"
    operations = frozenset(
        {
            "setDataTableCell",
            "setDataTableRowFields",
            "addDataTableRow",
            "removeDataTableRow",
            "renameDataTableRow",
        }
    )

    def semantic_path(self, evidence: SemanticOperationEvidence) -> str:
        row = evidence.target.get("rowName", "<missing>")
        if evidence.operation == "setDataTableCell":
            return f"DataTable.Row:{row}.Field:{evidence.target.get('fieldName', '<missing>')}"
        if evidence.operation == "renameDataTableRow":
            return f"DataTable.Row:{row}->Row:{evidence.target.get('newRowName', '<missing>')}"
        return f"DataTable.Row:{row}"

    def change_kind(self, before: Any, after: Any, evidence: SemanticOperationEvidence) -> str:
        return {
            "addDataTableRow": "row-added",
            "removeDataTableRow": "row-removed",
            "renameDataTableRow": "row-renamed",
        }.get(evidence.operation, super().change_kind(before, after, evidence))

    def expected_value(self, evidence: SemanticOperationEvidence) -> Any:
        if evidence.operation == "removeDataTableRow":
            return None
        if evidence.operation == "renameDataTableRow":
            return {
                "from": evidence.target.get("rowName", ""),
                "to": evidence.target.get("newRowName", ""),
            }
        return super().expected_value(evidence)


class MaterialInstanceAdapter(SemanticDiffAdapter):
    domain = "material-instance"
    operations = frozenset(
        {
            "setMaterialInstanceScalarParameter",
            "setMaterialInstanceVectorParameter",
            "setMaterialInstanceTextureParameter",
            "setMaterialInstanceStaticSwitchParameter",
        }
    )
    _category = {
        "setMaterialInstanceScalarParameter": "Scalar",
        "setMaterialInstanceVectorParameter": "Vector",
        "setMaterialInstanceTextureParameter": "Texture",
        "setMaterialInstanceStaticSwitchParameter": "StaticSwitch",
    }

    def semantic_path(self, evidence: SemanticOperationEvidence) -> str:
        category = self._category[evidence.operation]
        return f"MaterialInstance.{category}:{evidence.target.get('parameterName', '<missing>')}"

    @staticmethod
    def _override_state(value: Any) -> bool | None:
        if isinstance(value, dict):
            override = value.get("override")
            if isinstance(override, bool):
                return override
        return None

    def change_kind(self, before: Any, after: Any, evidence: SemanticOperationEvidence) -> str:
        before_override = self._override_state(before)
        after_override = self._override_state(after)
        if before_override is False and after_override is True:
            return "override-added"
        if before_override is True and after_override is False:
            return "override-removed"
        if before_override is True and after_override is True:
            return "override-changed"
        return super().change_kind(before, after, evidence)


class BlueprintAdapter(SemanticDiffAdapter):
    domain = "blueprint-narrow-write"
    operations = frozenset({"setVariableDefault", "setComponentProperty", "setPinDefault"})

    def semantic_path(self, evidence: SemanticOperationEvidence) -> str:
        target = evidence.target
        if evidence.operation == "setVariableDefault":
            return f"Blueprint.Defaults.{target.get('variableName', '<missing>')}"
        if evidence.operation == "setComponentProperty":
            return (
                f"Blueprint.Component:{target.get('componentName', '<missing>')}."
                f"{target.get('propertyPath', '<missing>')}"
            )
        return (
            f"Blueprint.Graph:{target.get('graphGuid', '<missing>')}."
            f"Node:{target.get('nodeGuid', '<missing>')}.Pin:{target.get('pinName', '<missing>')}.DefaultValue"
        )

    @staticmethod
    def _import_text(value: Any) -> Any:
        if isinstance(value, bool):
            return "True" if value else "False"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        if isinstance(value, str):
            text = value.strip()
            try:
                return float(text)
            except ValueError:
                return text
        return normalize_semantic_value(value)

    def expected_value(self, evidence: SemanticOperationEvidence) -> Any:
        return self._import_text(evidence.expected_value)

    def before_value(self, evidence: SemanticOperationEvidence) -> Any:
        return self._import_text(evidence.before_value)

    def actual_value(self, evidence: SemanticOperationEvidence) -> Any:
        return self._import_text(evidence.actual_value)


ADAPTERS: tuple[SemanticDiffAdapter, ...] = (
    DataAssetAdapter(),
    DataTableAdapter(),
    MaterialInstanceAdapter(),
    BlueprintAdapter(),
)


def adapter_for(operation: str) -> SemanticDiffAdapter | None:
    return next((adapter for adapter in ADAPTERS if adapter.supports(operation)), None)


def _entry_sort_key(entry: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("assetPath", "")).casefold(),
        str(entry.get("semanticPath", "")).casefold(),
        str(entry.get("changeKind", "")),
        str(entry.get("changeId", "")),
    )


def _gap(code: str, asset_path: str, message: str, *, operation_id: str = "") -> dict[str, Any]:
    return {
        "gapId": _stable_id("gap", code, asset_path, operation_id),
        "code": code,
        "assetPath": asset_path,
        "operationId": operation_id,
        "message": message,
    }


def _risk(code: str, severity: str, message: str, *, asset_path: str = "") -> dict[str, Any]:
    return {
        "riskId": _stable_id("risk", code, asset_path),
        "code": code,
        "severity": severity,
        "assetPath": asset_path,
        "message": message,
    }


def _build_entry(evidence: SemanticOperationEvidence, adapter: SemanticDiffAdapter) -> dict[str, Any]:
    semantic_path = adapter.semantic_path(evidence)
    before = adapter.before_value(evidence) if evidence.before_available else None
    after = adapter.actual_value(evidence) if evidence.actual_available else None
    expected = adapter.expected_value(evidence) if evidence.expected_available else None
    kind = adapter.change_kind(before, after, evidence)
    if kind not in SUPPORTED_CHANGE_KINDS:
        kind = "unknown-change"
    return {
        "changeId": _stable_id("chg", evidence.asset_path.casefold(), adapter.domain, semantic_path.casefold()),
        "assetPath": evidence.asset_path,
        "domain": adapter.domain,
        "operation": evidence.operation,
        "operationId": evidence.operation_id,
        "semanticPath": semantic_path,
        "changeKind": kind,
        "beforeValue": before,
        "afterValue": after,
        "expectedValue": expected,
        "source": evidence.source,
        "stage": evidence.stage,
        "status": "observed",
        "details": normalize_semantic_value(evidence.details),
    }


def _collapse_chain(entries: list[dict[str, Any]], *, expected: bool) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        key = (str(entry["assetPath"]).casefold(), str(entry["semanticPath"]).casefold())
        grouped.setdefault(key, []).append(entry)
    collapsed: list[dict[str, Any]] = []
    for chain in grouped.values():
        first = chain[0]
        last = chain[-1]
        result = dict(last)
        result["beforeValue"] = first.get("beforeValue")
        if expected:
            result["expectedValue"] = last.get("expectedValue")
            result["afterValue"] = last.get("expectedValue")
            result["status"] = (
                "expected-no-op"
                if semantic_equal(result.get("beforeValue"), result.get("expectedValue"))
                else "expected"
            )
        details = dict(result.get("details") or {})
        details["operationChain"] = [
            {
                "operationId": item.get("operationId", ""),
                "operation": item.get("operation", ""),
                "beforeValue": item.get("beforeValue"),
                "afterValue": item.get("expectedValue") if expected else item.get("afterValue"),
            }
            for item in chain
        ]
        result["details"] = details
        collapsed.append(result)
    return sorted(collapsed, key=_entry_sort_key)


def _match_changes(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    actual_by_path = {
        (str(entry["assetPath"]).casefold(), str(entry["semanticPath"]).casefold()): entry for entry in actual
    }
    matched: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    used: set[tuple[str, str]] = set()
    for expected_entry in expected:
        key = (
            str(expected_entry["assetPath"]).casefold(),
            str(expected_entry["semanticPath"]).casefold(),
        )
        actual_entry = actual_by_path.get(key)
        is_no_op = expected_entry.get("status") == "expected-no-op"
        if actual_entry is None and is_no_op:
            item = dict(expected_entry)
            item["status"] = "matched-expected-no-op"
            matched.append(item)
            continue
        if actual_entry is not None and semantic_equal(
            actual_entry.get("afterValue"),
            expected_entry.get("expectedValue"),
        ):
            item = dict(actual_entry)
            item["expectedValue"] = expected_entry.get("expectedValue")
            item["status"] = "matched"
            matched.append(item)
            used.add(key)
            continue
        item = dict(expected_entry)
        item["status"] = "missing"
        if actual_entry is not None:
            item["observedValue"] = actual_entry.get("afterValue")
        missing.append(item)
    unexpected = []
    for actual_entry in actual:
        key = (str(actual_entry["assetPath"]).casefold(), str(actual_entry["semanticPath"]).casefold())
        if key not in used:
            item = dict(actual_entry)
            item["status"] = "unexpected"
            unexpected.append(item)
    return (
        sorted(matched, key=_entry_sort_key),
        sorted(unexpected, key=_entry_sort_key),
        sorted(missing, key=_entry_sort_key),
    )


def _asset_summary(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    matched: list[dict[str, Any]],
    unexpected: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    unchanged: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> dict[str, int]:
    return {
        "expectedCount": len(expected),
        "actualCount": len(actual),
        "matchedCount": len(matched),
        "unexpectedCount": len(unexpected),
        "missingExpectedCount": len(missing),
        "unchangedCriticalCount": len(unchanged),
        "analysisGapCount": len(gaps),
    }


def _trim_response(response: dict[str, Any], max_output_tokens: int) -> None:
    reasons: list[str] = (
        ["section-limit"]
        if any(gap.get("code") == "semantic-diff-truncated" for gap in response.get("analysisGaps", []))
        else []
    )

    def over_budget() -> bool:
        return estimate_json_tokens(response) > max_output_tokens

    if over_budget():
        removed_details = False
        for asset in response.get("assets", []):
            for entry in asset.get("unchangedCriticalFields", []):
                if "details" in entry:
                    entry.pop("details")
                    removed_details = True
        if removed_details:
            reasons.append("unchanged-critical-details")
    while over_budget() and any(asset.get("unchangedCriticalFields") for asset in response.get("assets", [])):
        candidate = next(asset for asset in reversed(response["assets"]) if asset.get("unchangedCriticalFields"))
        candidate["unchangedCriticalFields"].pop()
        if "unchanged-critical-limit" not in reasons:
            reasons.append("unchanged-critical-limit")
    while over_budget() and any(asset.get("matchedChanges") for asset in response.get("assets", [])):
        candidate = next(asset for asset in reversed(response["assets"]) if asset.get("matchedChanges"))
        candidate["matchedChanges"].pop()
        if "matched-change-detail-limit" not in reasons:
            reasons.append("matched-change-detail-limit")
    response["outputBudget"] = {
        "maxTokens": max_output_tokens,
        "estimatedTokens": 0,
        "truncated": bool(reasons),
        "truncationReasons": reasons,
    }
    estimate = estimate_json_tokens(response)
    if estimate > max_output_tokens and "minimum-envelope-exceeds-budget" not in reasons:
        reasons.append("minimum-envelope-exceeds-budget")
    response["outputBudget"] = {
        "maxTokens": max_output_tokens,
        "estimatedTokens": estimate_json_tokens(response),
        "truncated": bool(reasons),
        "truncationReasons": reasons,
    }


def analyze_semantic_evidence(
    *,
    change_set: dict[str, Any],
    requested_stage: str,
    selected_stage: SemanticStage,
    selection_reason: str,
    sources: list[dict[str, Any]],
    assets: list[SemanticAssetEvidence],
    asset_paths: list[str] | None = None,
    include_unchanged: bool = True,
    max_changes: int = DEFAULT_SEMANTIC_DIFF_CHANGES,
    max_output_tokens: int = 4096,
    total_asset_count: int | None = None,
) -> dict[str, Any]:
    if requested_stage not in SUPPORTED_STAGES:
        raise ValueError("stage must be auto, live, persisted, or verified")
    if not isinstance(max_changes, int) or not MIN_SEMANTIC_DIFF_CHANGES <= max_changes <= MAX_SEMANTIC_DIFF_CHANGES:
        raise ValueError(f"max_changes must be between {MIN_SEMANTIC_DIFF_CHANGES} and {MAX_SEMANTIC_DIFF_CHANGES}")
    max_output_tokens = normalize_output_token_budget(max_output_tokens)
    filters = [] if asset_paths is None else list(asset_paths)
    if len(filters) > MAX_SEMANTIC_DIFF_ASSETS:
        raise ValueError(f"asset_paths must contain at most {MAX_SEMANTIC_DIFF_ASSETS} exact asset paths")
    def exact_object_path(path: Any) -> bool:
        if not isinstance(path, str) or not path.startswith("/Game/"):
            return False
        if chr(92) in path or "//" in path or ":" in path or any(ord(character) < 32 for character in path):
            return False
        if path.count(".") != 1:
            return False
        package_name, object_name = path.rsplit(".", 1)
        package_leaf = package_name.rsplit("/", 1)[-1]
        segments = package_name.removeprefix("/").split("/")
        return bool(package_leaf and object_name == package_leaf and all(item not in {"", ".", ".."} for item in segments))

    if any(not exact_object_path(path) for path in filters):
        raise ValueError("asset_paths must contain exact /Game object paths")
    if len({path.casefold() for path in filters}) != len(filters):
        raise ValueError("asset_paths must not contain duplicates")
    filter_keys = {path.casefold() for path in filters}
    selected_assets = [asset for asset in assets if not filter_keys or asset.asset_path.casefold() in filter_keys]
    selected_assets.sort(key=lambda asset: asset.asset_path.casefold())
    asset_limit_hit = len(selected_assets) > MAX_SEMANTIC_DIFF_ASSETS
    selected_assets = selected_assets[:MAX_SEMANTIC_DIFF_ASSETS]

    asset_results: list[dict[str, Any]] = []
    all_unexpected: list[dict[str, Any]] = []
    all_missing: list[dict[str, Any]] = []
    all_gaps: list[dict[str, Any]] = []
    remaining = max_changes
    remaining_unchanged = MAX_SEMANTIC_DIFF_UNCHANGED
    remaining_gaps = MAX_SEMANTIC_DIFF_GAPS
    change_limit_hit = False
    for asset in selected_assets:
        expected_raw: list[dict[str, Any]] = []
        actual_raw: list[dict[str, Any]] = []
        gaps = list(asset.analysis_gaps)
        if (
            asset.after_revision
            and asset.stage_evidence_revision
            and asset.after_revision != asset.stage_evidence_revision
        ):
            gaps.append(
                _gap(
                    "semantic-diff-evidence-stale",
                    asset.asset_path,
                    "The selected stage evidence revision does not match the reported after revision.",
                )
            )
        for evidence in (*asset.operations, *asset.actual_only):
            adapter = adapter_for(evidence.operation)
            if adapter is None:
                gaps.append(
                    _gap(
                        "unsupported-operation",
                        evidence.asset_path,
                        f"No Semantic Diff adapter is registered for {evidence.operation}.",
                        operation_id=evidence.operation_id,
                    )
                )
                continue
            entry = _build_entry(evidence, adapter)
            if evidence in asset.operations and evidence.expected_available:
                expected_raw.append(entry)
            elif evidence in asset.operations:
                gaps.append(
                    _gap(
                        "missing-expected-evidence",
                        evidence.asset_path,
                        "The fixed plan intent is unavailable for this operation.",
                        operation_id=evidence.operation_id,
                    )
                )
            if (
                evidence.actual_available
                and evidence.before_available
                and not semantic_equal(
                    entry.get("beforeValue"), entry.get("afterValue"), value_type=evidence.value_kind
                )
            ):
                actual_raw.append(entry)
            elif not evidence.actual_available:
                gaps.append(
                    _gap(
                        "missing-after-evidence",
                        evidence.asset_path,
                        f"No {selected_stage} after evidence is available for this operation.",
                        operation_id=evidence.operation_id,
                    )
                )
            elif not evidence.before_available:
                gaps.append(
                    _gap(
                        "missing-before-evidence",
                        evidence.asset_path,
                        "No before evidence is available for this operation.",
                        operation_id=evidence.operation_id,
                    )
                )
        expected = _collapse_chain(expected_raw, expected=True)
        actual = _collapse_chain(actual_raw, expected=False)
        matched, unexpected, missing = _match_changes(expected, actual)
        combined_count = len(expected) + len(actual)
        if combined_count > remaining:
            change_limit_hit = True
            keep_expected = min(len(expected), remaining)
            expected = expected[:keep_expected]
            remaining -= keep_expected
            actual = actual[:remaining]
            remaining = 0
            matched, unexpected, missing = _match_changes(expected, actual)
        else:
            remaining -= combined_count
        unchanged = [dict(item) for item in asset.unchanged_critical_fields[:remaining_unchanged]] if include_unchanged else []
        remaining_unchanged -= len(unchanged)
        gaps = sorted(gaps, key=lambda item: (str(item.get("code", "")), str(item.get("gapId", ""))))[:remaining_gaps]
        remaining_gaps -= len(gaps)
        result = {
            "assetPath": asset.asset_path,
            "assetClass": asset.asset_class,
            "domain": sorted({entry.get("domain", "unknown") for entry in expected + actual}),
            "beforeRevision": asset.before_revision,
            "afterRevision": asset.after_revision,
            "revisionChanged": bool(
                asset.before_revision and asset.after_revision and asset.before_revision != asset.after_revision
            ),
            "stageEvidenceRevision": asset.stage_evidence_revision,
            "expectedChanges": expected,
            "actualChanges": actual,
            "matchedChanges": matched,
            "unexpectedChanges": unexpected,
            "missingExpectedChanges": missing,
            "unchangedCriticalFields": unchanged,
            "analysisGaps": gaps,
            "summary": _asset_summary(expected, actual, matched, unexpected, missing, unchanged, gaps),
        }
        asset_results.append(result)
        all_unexpected.extend(unexpected)
        all_missing.extend(missing)
        all_gaps.extend(gaps)

    risks: list[dict[str, Any]] = []
    for entry in all_unexpected:
        risks.append(
            _risk(
                "semantic-diff-unexpected-change",
                "high",
                f"Observed an unrequested semantic change at {entry.get('semanticPath', '')}.",
                asset_path=str(entry.get("assetPath", "")),
            )
        )
    for entry in all_missing:
        risks.append(
            _risk(
                "semantic-diff-missing-expected-change",
                "high",
                f"Did not observe the requested semantic change at {entry.get('semanticPath', '')}.",
                asset_path=str(entry.get("assetPath", "")),
            )
        )
    if change_limit_hit or asset_limit_hit:
        risks.append(
            _risk(
                "semantic-diff-truncated",
                "medium",
                "The semantic change-entry or returned-asset limit was reached.",
            )
        )
    if any(
        gap.get("code") in {"revision-evidence-unavailable", "semantic-diff-evidence-stale"}
        for gap in all_gaps
    ):
        risks.append(_risk("semantic-diff-evidence-stale", "medium", "Revision-bound evidence is incomplete."))
    risks = sorted(risks, key=lambda item: (item["severity"], item["code"], item["assetPath"], item["riskId"]))
    next_actions: list[dict[str, Any]] = []
    affected = sorted({asset.asset_path for asset in selected_assets}, key=str.casefold)
    all_affected = sorted(
        {
            str(path)
            for path in change_set.get("affectedAssets", [])
            if isinstance(path, str) and path
        }
        or {asset.asset_path for asset in assets},
        key=str.casefold,
    )
    if all_unexpected or all_missing:
        next_actions.append(
            {
                "tool": "ue_analyze_change_impact",
                "arguments": {"target_asset_paths": affected},
                "reason": "Unexpected or missing semantic changes require bounded reverse-reference review.",
            }
        )
    if requested_stage != "verified" and selected_stage != "verified":
        next_actions.append(
            {
                "tool": SEMANTIC_DIFF_TOOL,
                "arguments": {"change_set_id": change_set.get("changeSetId", ""), "stage": "verified"},
                "reason": "Re-run after independent verification when that evidence becomes available.",
            }
        )
    explicit_change_set_id = str(change_set.get("changeSetId", ""))
    if explicit_change_set_id:
        next_actions.append(
            {
                "tool": "ue_build_verification_plan",
                "arguments": {"change_set_id": explicit_change_set_id},
                "reason": "Generate the deterministic R3 verification obligations for this explicit Change Set.",
            }
        )
        if not all_unexpected and not all_missing:
            next_actions.append(
                {
                    "tool": "ue_evaluate_trust_verdict",
                    "arguments": {"change_set_id": explicit_change_set_id},
                    "reason": "Evaluate currently applicable evidence against the generated Verification Plan.",
                }
            )

    total_assets = len(assets) if total_asset_count is None else total_asset_count
    response = {
        "schemaVersion": SEMANTIC_DIFF_SCHEMA_VERSION,
        "tool": SEMANTIC_DIFF_TOOL,
        "ok": True,
        "readOnly": True,
        "request": {
            "changeSetId": change_set.get("changeSetId", ""),
            "stage": requested_stage,
            "assetPaths": filters,
            "includeUnchanged": include_unchanged,
            "maxChanges": max_changes,
            "maxOutputTokens": max_output_tokens,
        },
        "source": {"kind": "explicit-change-set", "privateDiscovery": False},
        "changeSet": {
            "changeSetId": change_set.get("changeSetId", ""),
            "taskId": change_set.get("taskId", ""),
            "status": change_set.get("status", "unknown"),
            "affectedAssets": all_affected,
        },
        "evidenceStage": {
            "requested": requested_stage,
            "selected": selected_stage,
            "selectionReason": selection_reason,
            "sources": sorted(sources, key=lambda item: (str(item.get("kind", "")), str(item.get("id", "")))),
        },
        "assets": asset_results,
        "analysisGaps": sorted(
            all_gaps, key=lambda item: (str(item.get("assetPath", "")).casefold(), str(item.get("code", "")))
        )[:MAX_SEMANTIC_DIFF_GAPS],
        "risks": risks,
        "riskSummary": {
            "high": sum(risk["severity"] == "high" for risk in risks),
            "medium": sum(risk["severity"] == "medium" for risk in risks),
            "low": sum(risk["severity"] == "low" for risk in risks),
        },
        "summary": {
            "totalAssetCount": total_assets,
            "returnedAssetCount": len(asset_results),
            "filtered": bool(filters),
            "expectedCount": sum(asset["summary"]["expectedCount"] for asset in asset_results),
            "actualCount": sum(asset["summary"]["actualCount"] for asset in asset_results),
            "matchedCount": sum(asset["summary"]["matchedCount"] for asset in asset_results),
            "unexpectedCount": len(all_unexpected),
            "missingExpectedCount": len(all_missing),
            "unchangedCriticalCount": sum(asset["summary"]["unchangedCriticalCount"] for asset in asset_results),
            "analysisGapCount": len(all_gaps),
        },
        "nextActions": next_actions,
        "outputBudget": {},
    }
    if change_limit_hit or asset_limit_hit:
        response["analysisGaps"].append(
            _gap(
                "semantic-diff-truncated",
                "",
                "The max_changes or max-assets boundary truncated the returned Semantic Diff view.",
            )
        )
        response["analysisGaps"] = response["analysisGaps"][:MAX_SEMANTIC_DIFF_GAPS]
    _trim_response(response, max_output_tokens)
    return response

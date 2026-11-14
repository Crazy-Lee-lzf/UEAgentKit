"""Cross-writer atomic Blueprint patch contract (0-3.4).

One submission is one Editor transaction: every operation is validated against
the live graph before anything is written, the operations are applied in request
order inside a single ``FScopedTransaction``, and any failure restores the
operations already applied and reports ``writeOccurred=false`` together with one
result per operation.

This module never talks to Unreal.  It produces and checks the exact payloads the
live module (``BlueprintPatchExperimental.cpp``) consumes, so a caller cannot
build a bridge request of its own.

Writer migration is incremental, one writer per commit.  A patch may contain only
migrated kinds; the first migrated kind is ``setPinDefault``.
"""

from __future__ import annotations

import re
from typing import Any

from .experimental_envelope import unwrap_tool_result as _unwrap_tool_result
from .experimental_pin_default_edit import (
    PinDefaultError,
    canonical_value,
    validate_preview_parameters as validate_pin_default_parameters,
)

PREVIEW_TOOL_NAME = "ue_preview_blueprint_patch_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_patch_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_patch_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintPatchExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintPatchExperimental"
SAVE_CAPABILITY = "editor.saveBlueprintPatchExperimental"
PREVIEW_METHOD = "editor.previewBlueprintPatchExperimental"
APPLY_METHOD = "editor.applyBlueprintPatchExperimental"
SAVE_METHOD = "editor.saveBlueprintPatchExperimental"
APPLY_CONFIRMATION_PREFIX = "SET EXPERIMENTAL BLUEPRINT PATCH "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT PATCH "

PREVIEW_SCHEMA_VERSION = "ueak.blueprint-patch-preview/1.0"
APPLY_SCHEMA_VERSION = "ueak.blueprint-patch-apply/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_OPERATIONS = 16
MAX_OP_ID = 64
MAX_ASSET_PATH = 512

# Only these kinds are implemented by the live atomic patch.  A patch that mixes
# a migrated and an unmigrated kind is refused before any write, so one patch
# never carries two authorization contracts.
MIGRATED_KINDS = ("setPinDefault",)
RESERVED_KINDS = ("createVariable", "createNode", "connect", "rewirePin", "removeNode")

PIN_DEFAULT_FIELDS = ("nodeGuid", "pinName", "pinGuid", "type", "value")

_GUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA1 = re.compile(r"^sha1:[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class PatchError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PatchError("graph-patch-invalid-parameters", f"{label} must be an object.")
    return value


def _string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        requirement = f"at most {maximum} characters"
        if not allow_empty:
            requirement = "non-empty " + requirement
        raise PatchError(
            "graph-patch-invalid-parameters",
            f"{label} must be a string of {requirement}.",
        )
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PatchError(
            "graph-patch-unknown-parameter",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )


def _guid(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=36)
    if _GUID.fullmatch(normalized) is None:
        raise PatchError(
            "graph-patch-invalid-identifier",
            f"{label} must be one canonical lower-case GUID.",
        )
    return normalized


def _op_id(value: Any, index: int) -> str:
    normalized = _string(value, f"operations[{index}].opId", maximum=MAX_OP_ID)
    if any(ord(character) < 0x21 or ord(character) > 0x7E for character in normalized):
        raise PatchError(
            "graph-patch-invalid-parameters",
            f"operations[{index}].opId must be printable ASCII without spaces.",
        )
    return normalized


def validate_preview_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    operations: Any,
) -> dict[str, Any]:
    """Validate one bounded atomic patch request."""

    normalized_asset = _string(asset_path, "assetPath", maximum=MAX_ASSET_PATH)
    normalized_graph = _guid(graph_guid, "graphGuid")
    if not isinstance(operations, list) or not 1 <= len(operations) <= MAX_OPERATIONS:
        raise PatchError(
            "graph-patch-invalid-operations",
            f"operations must contain between 1 and {MAX_OPERATIONS} objects.",
        )

    normalized_operations: list[dict[str, Any]] = []
    seen_op_ids: set[str] = set()
    seen_targets: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(operations):
        item = _object(raw, f"operations[{index}]")
        op_id = _op_id(item.get("opId"), index)
        kind = _string(item.get("kind"), f"operations[{index}].kind", maximum=32)
        if op_id in seen_op_ids:
            raise PatchError(
                "graph-patch-duplicate-op-id",
                f"Duplicate opId: {op_id}.",
            )
        seen_op_ids.add(op_id)
        if kind not in MIGRATED_KINDS:
            if kind in RESERVED_KINDS:
                raise PatchError(
                    "graph-patch-kind-not-migrated",
                    f"Operation kind '{kind}' is designed but not migrated to the atomic "
                    f"patch yet; this build accepts: {', '.join(MIGRATED_KINDS)}.",
                )
            raise PatchError(
                "graph-patch-kind-not-allowed",
                f"Operation kind '{kind}' is not an atomic patch kind.",
            )
        _reject_unknown(
            item,
            {"opId", "kind", *PIN_DEFAULT_FIELDS},
            f"operations[{index}]",
        )
        # Reuse the single-writer validator so the atomic patch and
        # ue_apply_blueprint_pin_defaults_experimental cannot disagree about what
        # a legal pin default is.  Its error keeps its own code; only the exception
        # type is normalized so this module has one failure type.
        try:
            pin_default = validate_pin_default_parameters(
                asset_path=normalized_asset,
                graph_guid=normalized_graph,
                defaults=[
                    {
                        "nodeGuid": item.get("nodeGuid"),
                        "pinName": item.get("pinName"),
                        "pinGuid": item.get("pinGuid"),
                        "type": item.get("type"),
                        "value": item.get("value"),
                    }
                ],
            )["defaults"][0]
        except PinDefaultError as exc:
            raise PatchError(exc.code, exc.message) from exc
        target_key = (
            pin_default["nodeGuid"],
            pin_default["pinName"],
            pin_default["pinGuid"],
        )
        if target_key in seen_targets:
            # Two operations writing one pin would make the read-back of the first
            # one fail by construction; refuse instead of leaving that to chance.
            raise PatchError(
                "graph-patch-duplicate-target",
                f"Duplicate pin default target: {target_key[0]}.{target_key[1]} "
                f"({target_key[2]}).",
            )
        seen_targets.add(target_key)
        normalized_operations.append(
            {
                "opId": op_id,
                "kind": kind,
                "nodeGuid": pin_default["nodeGuid"],
                "pinName": pin_default["pinName"],
                "pinGuid": pin_default["pinGuid"],
                "type": pin_default["type"],
                "value": pin_default["value"],
            }
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "operations": normalized_operations,
    }


def confirmation_for_plan(plan_id: str) -> str:
    return APPLY_CONFIRMATION_PREFIX + plan_id


def save_confirmation_for_plan(plan_id: str) -> str:
    return SAVE_CONFIRMATION_PREFIX + plan_id


def validate_preview_result(preview: Any) -> dict[str, Any]:
    item = _object(_unwrap_tool_result(preview, "planId"), "preview")
    operations = item.get("operations")
    if (
        item.get("schemaVersion") != PREVIEW_SCHEMA_VERSION
        or item.get("experimental") is not True
        or item.get("readOnly") is not True
        or item.get("wroteBlueprint") is not False
        or item.get("savedPackage") is not False
        or item.get("compiled") is not False
        or not isinstance(item.get("planId"), str)
        or _SHA256.fullmatch(item.get("planId")) is None
        or not isinstance(item.get("packageRevision"), str)
        or _SHA256.fullmatch(item.get("packageRevision")) is None
        or not isinstance(item.get("graphFingerprint"), str)
        or _SHA1.fullmatch(item.get("graphFingerprint")) is None
        or not isinstance(item.get("stateFingerprint"), str)
        or _SHA256.fullmatch(item.get("stateFingerprint")) is None
        or not isinstance(operations, list)
        or not 1 <= len(operations) <= MAX_OPERATIONS
        or item.get("operationCount") != len(operations)
        or item.get("migratedKinds") != list(MIGRATED_KINDS)
    ):
        raise PatchError(
            "graph-patch-preview-invalid",
            "Preview envelope, planId, fingerprints, kinds or operation list are invalid.",
        )
    normalized_asset = _string(item.get("assetPath"), "preview.assetPath", maximum=MAX_ASSET_PATH)
    normalized_graph = _guid(item.get("graphGuid"), "preview.graphGuid")
    normalized_operations: list[dict[str, Any]] = []
    seen_op_ids: set[str] = set()
    seen_targets: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(operations):
        entry = _object(raw, f"preview.operations[{index}]")
        op_id = _op_id(entry.get("opId"), index)
        if op_id in seen_op_ids:
            raise PatchError(
                "graph-patch-preview-invalid",
                f"Preview repeats the operation id {op_id}.",
            )
        seen_op_ids.add(op_id)
        kind = _string(entry.get("kind"), f"preview.operations[{index}].kind", maximum=32)
        if kind not in MIGRATED_KINDS:
            raise PatchError(
                "graph-patch-preview-invalid",
                f"Preview contains kind '{kind}', which this build did not migrate.",
            )
        if entry.get("status") != "validated":
            raise PatchError(
                "graph-patch-preview-invalid",
                "Preview operations must all be validated.",
            )
        node_guid = _guid(entry.get("nodeGuid"), f"preview.operations[{index}].nodeGuid")
        pin_guid = _guid(entry.get("pinGuid"), f"preview.operations[{index}].pinGuid")
        pin_name = _string(
            entry.get("pinName"),
            f"preview.operations[{index}].pinName",
            maximum=128,
        )
        normalized_type, after = canonical_value(entry.get("type"), entry.get("afterValue"))
        before = _string(
            entry.get("beforeValue"),
            f"preview.operations[{index}].beforeValue",
            maximum=1024,
            allow_empty=True,
        )
        target_key = (node_guid, pin_name, pin_guid)
        if target_key in seen_targets:
            raise PatchError(
                "graph-patch-preview-invalid",
                f"Preview repeats the pin target {node_guid}.{pin_name} ({pin_guid}).",
            )
        seen_targets.add(target_key)
        normalized_operations.append(
            {
                "opId": op_id,
                "kind": kind,
                "nodeGuid": node_guid,
                "pinName": pin_name,
                "pinGuid": pin_guid,
                "type": normalized_type,
                "beforeValue": before,
                "afterValue": after,
            }
        )
    if item.get("requiredConfirmation") != confirmation_for_plan(item.get("planId")):
        raise PatchError(
            "graph-patch-preview-invalid",
            "Preview requiredConfirmation does not match planId.",
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "planId": item.get("planId"),
        "packageRevision": item.get("packageRevision"),
        "graphFingerprint": item.get("graphFingerprint"),
        "stateFingerprint": item.get("stateFingerprint"),
        "operations": normalized_operations,
        "requiredConfirmation": item.get("requiredConfirmation"),
    }


def build_apply_parameters(
    *,
    plan_id: Any,
    asset_path: Any,
    graph_guid: Any,
    operations: Any,
    confirmation: Any = "",
) -> dict[str, Any]:
    """Build the Apply request from the plan fields alone.

    ``planId`` is a sha256 over the live before values and fingerprints, so the
    Editor still proves both "you previewed" and "the graph has not changed";
    handing the whole preview object back adds no check on top of that.
    """

    normalized = validate_preview_parameters(
        asset_path=asset_path,
        graph_guid=graph_guid,
        operations=operations,
    )
    if not isinstance(plan_id, str) or _SHA256.fullmatch(plan_id) is None:
        raise PatchError(
            "graph-patch-invalid-parameters",
            "planId must be the sha256:<64 lowercase hex> value returned by Preview.",
        )
    expected = confirmation_for_plan(plan_id)
    if confirmation in (None, ""):
        confirmation = expected
    if not isinstance(confirmation, str) or confirmation != expected:
        raise PatchError(
            "graph-patch-confirmation-required",
            f"Explicit Apply confirmation must be exactly {expected}.",
        )
    return {
        "assetPath": normalized["assetPath"],
        "graphGuid": normalized["graphGuid"],
        "operations": normalized["operations"],
        "planId": plan_id,
        "confirmation": confirmation,
    }


def validate_apply_result(apply_result: Any) -> dict[str, Any]:
    item = _object(_unwrap_tool_result(apply_result, "applied"), "applyResult")
    if (
        item.get("schemaVersion") != APPLY_SCHEMA_VERSION
        or item.get("experimental") is not True
        or item.get("applied") is not True
        or item.get("saved") is True
        or item.get("singleTransaction") is not True
        or item.get("transactionCount") != 1
        or not isinstance(item.get("planId"), str)
        or _SHA256.fullmatch(item.get("planId")) is None
        or not isinstance(item.get("graphFingerprintAfter"), str)
        or _SHA1.fullmatch(item.get("graphFingerprintAfter")) is None
        or not isinstance(item.get("stateFingerprintAfter"), str)
        or _SHA256.fullmatch(item.get("stateFingerprintAfter")) is None
    ):
        raise PatchError(
            "graph-patch-apply-invalid",
            "Apply envelope, planId, transaction or fingerprints are invalid.",
        )
    results = item.get("results")
    if not isinstance(results, list) or item.get("operationCount") != len(results):
        raise PatchError(
            "graph-patch-apply-invalid",
            "Apply results must carry exactly one entry per operation.",
        )
    normalized_asset = _string(item.get("assetPath"), "applyResult.assetPath", maximum=MAX_ASSET_PATH)
    normalized_graph = _guid(item.get("graphGuid"), "applyResult.graphGuid")
    for index, raw in enumerate(results):
        entry = _object(raw, f"applyResult.results[{index}]")
        if entry.get("status") != "applied" or entry.get("rolledBack") is not False:
            raise PatchError(
                "graph-patch-apply-invalid",
                "Every applied operation must report status=applied and rolledBack=false.",
            )
        _op_id(entry.get("opId"), index)
        _guid(entry.get("nodeGuid"), f"applyResult.results[{index}].nodeGuid")
        _guid(entry.get("pinGuid"), f"applyResult.results[{index}].pinGuid")
    for key in ("nodeCountBefore", "nodeCountAfter", "linkCountBefore", "linkCountAfter"):
        if isinstance(item.get(key), bool) or not isinstance(item.get(key), int) or item.get(key) < 0:
            raise PatchError(
                "graph-patch-apply-invalid",
                f"applyResult.{key} must be a non-negative integer.",
            )
    normalized = dict(item)
    normalized["assetPath"] = normalized_asset
    normalized["graphGuid"] = normalized_graph
    return normalized


def build_save_parameters(apply_result: Any, confirmation: Any = "") -> dict[str, Any]:
    item = validate_apply_result(apply_result)
    plan_id = item["planId"]
    expected = save_confirmation_for_plan(plan_id)
    if confirmation in (None, ""):
        confirmation = expected
    if not isinstance(confirmation, str) or confirmation != expected:
        raise PatchError(
            "graph-patch-confirmation-required",
            f"Explicit Save confirmation must be exactly {expected}.",
        )
    return {
        "assetPath": item["assetPath"],
        "graphGuid": item["graphGuid"],
        "planId": plan_id,
        "expectedGraphFingerprint": item["graphFingerprintAfter"],
        "expectedStateFingerprint": item["stateFingerprintAfter"],
        "expectedNodeCount": item["nodeCountAfter"],
        "expectedLinkCount": item["linkCountAfter"],
        "confirmation": confirmation,
    }


def describe_rollback(details: Any) -> dict[str, Any]:
    """Extract the per-operation results a failed Apply carried as error details."""

    if not isinstance(details, dict):
        return {}
    results = details.get("results")
    if not isinstance(results, list):
        return {}
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(results):
        if not isinstance(raw, dict):
            continue
        item = {
            "opId": raw.get("opId"),
            "kind": raw.get("kind"),
            "status": raw.get("status"),
        }
        if isinstance(raw.get("errorCode"), str):
            item["errorCode"] = raw["errorCode"]
        if isinstance(raw.get("errorMessage"), str):
            item["errorMessage"] = raw["errorMessage"]
        if raw.get("rolledBack") is not None:
            item["rolledBack"] = raw.get("rolledBack") is True
        normalized.append(item)
    return {
        "writeOccurred": details.get("writeOccurred") is True,
        "transactionOpened": details.get("transactionOpened") is True,
        "rolledBack": details.get("rolledBack") is True,
        "results": normalized,
    }


__all__ = [
    "APPLY_CAPABILITY",
    "APPLY_CONFIRMATION_PREFIX",
    "APPLY_METHOD",
    "APPLY_SCHEMA_VERSION",
    "APPLY_TOOL_NAME",
    "MIGRATED_KINDS",
    "MAX_OPERATIONS",
    "MAX_REQUEST_BYTES",
    "PREVIEW_CAPABILITY",
    "PREVIEW_METHOD",
    "PREVIEW_SCHEMA_VERSION",
    "PREVIEW_TOOL_NAME",
    "PatchError",
    "RESERVED_KINDS",
    "SAVE_CAPABILITY",
    "SAVE_CONFIRMATION_PREFIX",
    "SAVE_METHOD",
    "SAVE_TOOL_NAME",
    "build_apply_parameters",
    "build_save_parameters",
    "confirmation_for_plan",
    "describe_rollback",
    "save_confirmation_for_plan",
    "validate_apply_result",
    "validate_preview_parameters",
    "validate_preview_result",
]

"""Bounded, opt-in Blueprint single-edge rewire contract.

This is the P3 structural slice.  One request rewires exactly one
already-connected input pin to exactly one new output pin, or explicitly
disconnects that single known edge.  Every request binds the exact focused
top-level K2 event graph, the on-disk package revision, the live graph
fingerprint, the complete before edge-set hash, and the exact
old-source/target/new-source node GUID + pin name + pin GUID triples.

The contract never accepts a caller-built bridge request: Apply only consumes
the complete Preview result plus the exact confirmation token, and Save only
consumes the complete Apply result plus its own confirmation token.  The live
native module applies the change in one transaction, proves the complete after
edge set and all pin identities, verifies Undo/Redo, and never compiles or
auto-saves.
"""

from __future__ import annotations

import re
from typing import Any

PREVIEW_TOOL_NAME = "ue_preview_blueprint_rewire_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_rewire_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_rewire_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintRewireExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintRewireExperimental"
SAVE_CAPABILITY = "editor.saveBlueprintRewireExperimental"
PREVIEW_METHOD = "editor.previewBlueprintRewireExperimental"
APPLY_METHOD = "editor.applyBlueprintRewireExperimental"
SAVE_METHOD = "editor.saveBlueprintRewireExperimental"
APPLY_CONFIRMATION_PREFIX = "REWIRE EXPERIMENTAL BLUEPRINT PIN "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT REWIRE "

PREVIEW_SCHEMA_VERSION = "ueak.blueprint-rewire-preview/1.0"
APPLY_SCHEMA_VERSION = "ueak.blueprint-rewire-apply/1.0"
SAVE_SCHEMA_VERSION = "ueak.blueprint-rewire-save/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_PIN_NAME = 128
MAX_ASSET_PATH = 512
MAX_GRAPH_GUID = 36

OPERATIONS = ("rewire", "disconnect")
PIN_KINDS = ("exec", "bool", "int", "double", "string")

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA1 = re.compile(r"^sha1:[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_PIN_FORBIDDEN = ("|", ">", "\n", "\r", "\t")


class RewireWriteError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RewireWriteError(
            "blueprint-rewire-invalid-parameters",
            f"{label} must be an object.",
        )
    return value


def _string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        requirement = f"at most {maximum} characters"
        if not allow_empty:
            requirement = "non-empty " + requirement
        raise RewireWriteError(
            "blueprint-rewire-invalid-parameters",
            f"{label} must be a string of {requirement}.",
        )
    return value


def _guid(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=MAX_GRAPH_GUID)
    if _GUID.fullmatch(normalized) is None:
        raise RewireWriteError(
            "blueprint-rewire-invalid-identifier",
            f"{label} must be one canonical lowercase GUID.",
        )
    return normalized


def _sha256(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=71)
    if _SHA256.fullmatch(normalized) is None:
        raise RewireWriteError(
            "blueprint-rewire-invalid-identifier",
            f"{label} must be one canonical lowercase sha256 value.",
        )
    return normalized


def _sha1(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=45)
    if _SHA1.fullmatch(normalized) is None:
        raise RewireWriteError(
            "blueprint-rewire-invalid-identifier",
            f"{label} must be one canonical lowercase sha1 value.",
        )
    return normalized


def _pin_name(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=MAX_PIN_NAME)
    if any(character in normalized for character in _PIN_FORBIDDEN):
        raise RewireWriteError(
            "blueprint-rewire-invalid-parameters",
            f"{label} must not contain '|', '>' or control characters.",
        )
    if any(ord(character) < 0x20 for character in normalized):
        raise RewireWriteError(
            "blueprint-rewire-invalid-parameters",
            f"{label} must not contain control characters.",
        )
    return normalized


def _pin_kind(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=16)
    if normalized not in PIN_KINDS:
        raise RewireWriteError(
            "blueprint-rewire-preview-invalid",
            f"{label} must be one of: {', '.join(PIN_KINDS)}.",
        )
    return normalized


def _edge(value: Any, label: str) -> dict[str, str]:
    item = _object(value, label)
    allowed = {
        "fromNodeGuid",
        "fromPinName",
        "fromPinGuid",
        "toNodeGuid",
        "toPinName",
        "toPinGuid",
        "kind",
    }
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise RewireWriteError(
            "blueprint-rewire-preview-invalid",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )
    return {
        "fromNodeGuid": _guid(item.get("fromNodeGuid"), f"{label}.fromNodeGuid"),
        "fromPinName": _pin_name(item.get("fromPinName"), f"{label}.fromPinName"),
        "fromPinGuid": _guid(item.get("fromPinGuid"), f"{label}.fromPinGuid"),
        "toNodeGuid": _guid(item.get("toNodeGuid"), f"{label}.toNodeGuid"),
        "toPinName": _pin_name(item.get("toPinName"), f"{label}.toPinName"),
        "toPinGuid": _guid(item.get("toPinGuid"), f"{label}.toPinGuid"),
        "kind": _pin_kind(item.get("kind"), f"{label}.kind"),
    }


def _pin(value: Any, label: str, direction: str) -> dict[str, str]:
    item = _object(value, label)
    allowed = {"nodeGuid", "pinName", "pinGuid", "direction", "kind"}
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise RewireWriteError(
            "blueprint-rewire-preview-invalid",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )
    actual_direction = _string(item.get("direction"), f"{label}.direction", maximum=8)
    if actual_direction != direction:
        raise RewireWriteError(
            "blueprint-rewire-preview-invalid",
            f"{label}.direction must be {direction}.",
        )
    return {
        "nodeGuid": _guid(item.get("nodeGuid"), f"{label}.nodeGuid"),
        "pinName": _pin_name(item.get("pinName"), f"{label}.pinName"),
        "pinGuid": _guid(item.get("pinGuid"), f"{label}.pinGuid"),
        "direction": actual_direction,
        "kind": _pin_kind(item.get("kind"), f"{label}.kind"),
    }


def validate_preview_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    operation: Any,
    target_node_guid: Any,
    target_pin_name: Any,
    target_pin_guid: Any,
    old_source_node_guid: Any,
    old_source_pin_name: Any,
    old_source_pin_guid: Any,
    expected_edge_set_hash: Any,
    new_source_node_guid: Any = None,
    new_source_pin_name: Any = None,
    new_source_pin_guid: Any = None,
) -> dict[str, Any]:
    """Validate one bounded rewire/disconnect preview request."""

    normalized_operation = _string(operation, "operation", maximum=16)
    if normalized_operation not in OPERATIONS:
        raise RewireWriteError(
            "blueprint-rewire-invalid-parameters",
            "operation must be exactly 'rewire' or 'disconnect'.",
        )
    parameters: dict[str, Any] = {
        "assetPath": _string(asset_path, "assetPath", maximum=MAX_ASSET_PATH),
        "graphGuid": _guid(graph_guid, "graphGuid"),
        "operation": normalized_operation,
        "targetNodeGuid": _guid(target_node_guid, "targetNodeGuid"),
        "targetPinName": _pin_name(target_pin_name, "targetPinName"),
        "targetPinGuid": _guid(target_pin_guid, "targetPinGuid"),
        "oldSourceNodeGuid": _guid(old_source_node_guid, "oldSourceNodeGuid"),
        "oldSourcePinName": _pin_name(old_source_pin_name, "oldSourcePinName"),
        "oldSourcePinGuid": _guid(old_source_pin_guid, "oldSourcePinGuid"),
        "expectedEdgeSetHash": _sha256(expected_edge_set_hash, "expectedEdgeSetHash"),
    }
    if normalized_operation == "rewire":
        parameters["newSourceNodeGuid"] = _guid(new_source_node_guid, "newSourceNodeGuid")
        parameters["newSourcePinName"] = _pin_name(new_source_pin_name, "newSourcePinName")
        parameters["newSourcePinGuid"] = _guid(new_source_pin_guid, "newSourcePinGuid")
    elif any(
        value is not None
        for value in (new_source_node_guid, new_source_pin_name, new_source_pin_guid)
    ):
        raise RewireWriteError(
            "blueprint-rewire-invalid-parameters",
            "A disconnect request must not carry newSource* fields.",
        )
    return parameters


def validate_preview_result(result: Any) -> dict[str, Any]:
    """Validate the exact read-only Preview result and return a normalized copy."""

    item = _object(result, "preview result")
    try:
        schema = _string(item.get("schemaVersion"), "schemaVersion", maximum=64)
        if schema != PREVIEW_SCHEMA_VERSION:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                f"schemaVersion must be {PREVIEW_SCHEMA_VERSION}.",
            )
        if item.get("experimental") is not True or item.get("readOnly") is not True:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "Preview must be experimental and read-only.",
            )
        for flag in ("wroteBlueprint", "savedPackage", "compiled"):
            if item.get(flag) is not False:
                raise RewireWriteError(
                    "blueprint-rewire-preview-invalid",
                    f"Preview must report {flag}=false.",
                )
        operation = _string(item.get("operation"), "operation", maximum=16)
        if operation not in OPERATIONS:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "operation must be rewire or disconnect.",
            )
        plan_id = _sha256(item.get("planId"), "planId")
        if item.get("requiredConfirmation") != APPLY_CONFIRMATION_PREFIX + plan_id:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "requiredConfirmation must bind the exact planId.",
            )
        edge_before = _sha256(item.get("edgeSetHashBefore"), "edgeSetHashBefore")
        edge_after = _sha256(item.get("edgeSetHashAfter"), "edgeSetHashAfter")
        untouched_hash = _sha256(item.get("untouchedEdgesHash"), "untouchedEdgesHash")
        pin_identity_hash = _sha256(item.get("pinIdentityHash"), "pinIdentityHash")
        package_revision = _sha256(item.get("packageRevision"), "packageRevision")
        graph_fingerprint = _sha1(item.get("graphFingerprint"), "graphFingerprint")
        if edge_before == edge_after:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "Preview must change the edge-set hash.",
            )
        pin_kind = _pin_kind(item.get("pinKind"), "pinKind")
        node_before = _count(item.get("nodeCountBefore"), "nodeCountBefore")
        node_after = _count(item.get("nodeCountAfter"), "nodeCountAfter")
        link_before = _count(item.get("linkCountBefore"), "linkCountBefore")
        link_after = _count(item.get("linkCountAfter"), "linkCountAfter")
        untouched = _count(item.get("untouchedEdgeCount"), "untouchedEdgeCount")
        if node_before != node_after:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "A rewire/disconnect must not add or remove nodes.",
            )
        expected_after = link_before if operation == "rewire" else link_before - 1
        if link_before < 1 or link_after != expected_after:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "linkCountAfter does not match the declared operation.",
            )
        if untouched != link_before - 1:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "untouchedEdgeCount must equal linkCountBefore - 1.",
            )
        removed = item.get("removedEdges")
        if not isinstance(removed, list) or len(removed) != 1:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "removedEdges must contain exactly the one planned old edge.",
            )
        removed_edge = _edge(removed[0], "removedEdges[0]")
        if removed_edge["kind"] != pin_kind:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "removedEdges[0].kind must match pinKind.",
            )
        added = item.get("addedEdges")
        if not isinstance(added, list):
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "addedEdges must be an array.",
            )
        if operation == "rewire":
            if len(added) != 1:
                raise RewireWriteError(
                    "blueprint-rewire-preview-invalid",
                    "A rewire preview must add exactly one edge.",
                )
        elif len(added) != 0:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "A disconnect preview must not add an edge.",
            )
        added_edge = _edge(added[0], "addedEdges[0]") if added else None
        target_pin = _pin(item.get("targetPin"), "targetPin", "input")
        old_source_pin = _pin(item.get("oldSourcePin"), "oldSourcePin", "output")
        if old_source_pin["kind"] != pin_kind or target_pin["kind"] != pin_kind:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "pin kinds must agree with pinKind.",
            )
        if (
            removed_edge["fromNodeGuid"] != old_source_pin["nodeGuid"]
            or removed_edge["fromPinGuid"] != old_source_pin["pinGuid"]
            or removed_edge["fromPinName"] != old_source_pin["pinName"]
            or removed_edge["toNodeGuid"] != target_pin["nodeGuid"]
            or removed_edge["toPinGuid"] != target_pin["pinGuid"]
            or removed_edge["toPinName"] != target_pin["pinName"]
        ):
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "removedEdges[0] must be exactly oldSourcePin -> targetPin.",
            )
        normalized_new_pin: dict[str, str] | None = None
        if operation == "rewire":
            new_source_pin = _pin(item.get("newSourcePin"), "newSourcePin", "output")
            if new_source_pin["kind"] != pin_kind:
                raise RewireWriteError(
                    "blueprint-rewire-preview-invalid",
                    "newSourcePin.kind must match pinKind.",
                )
            if added_edge is None:
                raise RewireWriteError(
                    "blueprint-rewire-preview-invalid",
                    "addedEdges[0] is required for a rewire.",
                )
            if (
                added_edge["fromNodeGuid"] != new_source_pin["nodeGuid"]
                or added_edge["fromPinGuid"] != new_source_pin["pinGuid"]
                or added_edge["fromPinName"] != new_source_pin["pinName"]
                or added_edge["toNodeGuid"] != target_pin["nodeGuid"]
                or added_edge["toPinGuid"] != target_pin["pinGuid"]
                or added_edge["toPinName"] != target_pin["pinName"]
            ):
                raise RewireWriteError(
                    "blueprint-rewire-preview-invalid",
                    "addedEdges[0] must be exactly newSourcePin -> targetPin.",
                )
            normalized_new_pin = new_source_pin
        elif item.get("newSourcePin") is not None:
            raise RewireWriteError(
                "blueprint-rewire-preview-invalid",
                "A disconnect preview must not report newSourcePin.",
            )
        return {
            "schemaVersion": schema,
            "experimental": True,
            "readOnly": True,
            "planId": plan_id,
            "assetPath": _string(item.get("assetPath"), "assetPath", maximum=MAX_ASSET_PATH),
            "graphGuid": _guid(item.get("graphGuid"), "graphGuid"),
            "operation": operation,
            "packageRevision": package_revision,
            "graphFingerprint": graph_fingerprint,
            "edgeSetHashBefore": edge_before,
            "edgeSetHashAfter": edge_after,
            "untouchedEdgesHash": untouched_hash,
            "pinIdentityHash": pin_identity_hash,
            "pinKind": pin_kind,
            "nodeCountBefore": node_before,
            "nodeCountAfter": node_after,
            "linkCountBefore": link_before,
            "linkCountAfter": link_after,
            "untouchedEdgeCount": untouched,
            "removedEdges": [removed_edge],
            "addedEdges": [added_edge] if added_edge is not None else [],
            "oldSourcePin": old_source_pin,
            "targetPin": target_pin,
            "newSourcePin": normalized_new_pin,
            "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + plan_id,
        }
    except RewireWriteError as exc:
        if exc.code == "blueprint-rewire-preview-invalid":
            raise
        raise RewireWriteError(
            "blueprint-rewire-preview-invalid",
            f"Preview result is not a valid rewire preview: {exc.message}",
        ) from exc


def confirmation_for_plan(plan_id: str) -> str:
    return APPLY_CONFIRMATION_PREFIX + plan_id


def save_confirmation_for_plan(plan_id: str) -> str:
    return SAVE_CONFIRMATION_PREFIX + plan_id


def build_apply_parameters(preview_result: Any, confirmation: Any) -> dict[str, Any]:
    """Build the exact Apply request from the complete Preview result."""

    preview = validate_preview_result(preview_result)
    expected = APPLY_CONFIRMATION_PREFIX + preview["planId"]
    if not isinstance(confirmation, str) or confirmation != expected:
        raise RewireWriteError(
            "blueprint-rewire-confirmation-required",
            f"Apply requires the exact confirmation: {expected}.",
        )
    parameters: dict[str, Any] = {
        "assetPath": preview["assetPath"],
        "graphGuid": preview["graphGuid"],
        "operation": preview["operation"],
        "targetNodeGuid": preview["targetPin"]["nodeGuid"],
        "targetPinName": preview["targetPin"]["pinName"],
        "targetPinGuid": preview["targetPin"]["pinGuid"],
        "oldSourceNodeGuid": preview["oldSourcePin"]["nodeGuid"],
        "oldSourcePinName": preview["oldSourcePin"]["pinName"],
        "oldSourcePinGuid": preview["oldSourcePin"]["pinGuid"],
        "expectedEdgeSetHash": preview["edgeSetHashBefore"],
        "planId": preview["planId"],
        "confirmation": confirmation,
    }
    if preview["newSourcePin"] is not None:
        parameters["newSourceNodeGuid"] = preview["newSourcePin"]["nodeGuid"]
        parameters["newSourcePinName"] = preview["newSourcePin"]["pinName"]
        parameters["newSourcePinGuid"] = preview["newSourcePin"]["pinGuid"]
    return parameters


def validate_apply_result(
    result: Any,
    preview_result: Any,
) -> dict[str, Any]:
    """Validate the exact Apply result against the Preview that produced it."""

    preview = validate_preview_result(preview_result)
    item = _object(result, "apply result")
    try:
        schema = _string(item.get("schemaVersion"), "schemaVersion", maximum=64)
        if schema != APPLY_SCHEMA_VERSION:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                f"schemaVersion must be {APPLY_SCHEMA_VERSION}.",
            )
        for flag in ("applied", "undoVerified", "redoVerified"):
            if item.get(flag) is not True:
                raise RewireWriteError(
                    "blueprint-rewire-apply-invalid",
                    f"Apply must report {flag}=true.",
                )
        for flag in ("saved", "compiled", "skeletonRegenerated"):
            if item.get(flag) is not False:
                raise RewireWriteError(
                    "blueprint-rewire-apply-invalid",
                    f"Apply must report {flag}=false.",
                )
        if item.get("packageDirty") is not True:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply must leave the package dirty for the explicit Save gate.",
            )
        plan_id = _sha256(item.get("planId"), "planId")
        if plan_id != preview["planId"]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply planId does not match the Preview.",
            )
        if item.get("requiredSaveConfirmation") != SAVE_CONFIRMATION_PREFIX + plan_id:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "requiredSaveConfirmation must bind the exact planId.",
            )
        edge_after = _sha256(item.get("edgeSetHashAfter"), "edgeSetHashAfter")
        if edge_after != preview["edgeSetHashAfter"]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply edgeSetHashAfter does not match the Preview.",
            )
        if _sha256(item.get("edgeSetHashBefore"), "edgeSetHashBefore") != preview[
            "edgeSetHashBefore"
        ]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply edgeSetHashBefore does not match the Preview.",
            )
        if _sha1(item.get("graphFingerprint"), "graphFingerprint") != preview[
            "graphFingerprint"
        ]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply graphFingerprint does not match the Preview.",
            )
        if _sha256(item.get("packageRevision"), "packageRevision") != preview[
            "packageRevision"
        ]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply packageRevision does not match the Preview.",
            )
        if _guid(item.get("graphGuid"), "graphGuid") != preview["graphGuid"]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply graphGuid does not match the Preview.",
            )
        if item.get("assetPath") != preview["assetPath"]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply assetPath does not match the Preview.",
            )
        if item.get("operation") != preview["operation"]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply operation does not match the Preview.",
            )
        removed = item.get("removedEdges")
        if not isinstance(removed, list) or len(removed) != 1:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply removedEdges must contain exactly one edge.",
            )
        removed_edge = _edge(removed[0], "removedEdges[0]")
        added = item.get("addedEdges")
        if not isinstance(added, list):
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply addedEdges must be an array.",
            )
        expected_added_count = 1 if preview["operation"] == "rewire" else 0
        if len(added) != expected_added_count:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply addedEdges count does not match the operation.",
            )
        if removed_edge != preview["removedEdges"][0]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply removedEdges does not match the Preview.",
            )
        if added and _edge(added[0], "addedEdges[0]") != preview["addedEdges"][0]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply addedEdges does not match the Preview.",
            )
        node_count = _count(item.get("nodeCount"), "nodeCount")
        link_count = _count(item.get("linkCount"), "linkCount")
        link_before = _count(item.get("linkCountBefore"), "linkCountBefore")
        if node_count != preview["nodeCountAfter"] or link_count != preview["linkCountAfter"]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply node/link counts do not match the Preview.",
            )
        if link_before != preview["linkCountBefore"]:
            raise RewireWriteError(
                "blueprint-rewire-apply-invalid",
                "Apply linkCountBefore does not match the Preview.",
            )
        return {
            "schemaVersion": schema,
            "applied": True,
            "undoVerified": True,
            "redoVerified": True,
            "saved": False,
            "compiled": False,
            "skeletonRegenerated": False,
            "packageDirty": True,
            "planId": plan_id,
            "assetPath": preview["assetPath"],
            "graphGuid": preview["graphGuid"],
            "operation": preview["operation"],
            "packageRevision": preview["packageRevision"],
            "graphFingerprint": preview["graphFingerprint"],
            "edgeSetHashBefore": preview["edgeSetHashBefore"],
            "edgeSetHashAfter": preview["edgeSetHashAfter"],
            "pinIdentityHash": preview["pinIdentityHash"],
            "pinKind": preview["pinKind"],
            "nodeCount": node_count,
            "linkCount": link_count,
            "linkCountBefore": link_before,
            "linkCountAfter": link_count,
            "removedEdges": [removed_edge],
            "addedEdges": [_edge(added[0], "addedEdges[0]")] if added else [],
            "oldSourcePin": preview["oldSourcePin"],
            "targetPin": preview["targetPin"],
            "newSourcePin": preview["newSourcePin"],
            "requiredSaveConfirmation": SAVE_CONFIRMATION_PREFIX + plan_id,
        }
    except RewireWriteError as exc:
        if exc.code == "blueprint-rewire-apply-invalid":
            raise
        raise RewireWriteError(
            "blueprint-rewire-apply-invalid",
            f"Apply result is not a valid rewire apply: {exc.message}",
        ) from exc


def build_save_parameters(apply_result: Any, confirmation: Any) -> dict[str, Any]:
    """Build the exact Save request from the complete Apply result."""

    apply = _object(apply_result, "apply result")
    plan_id = _sha256(apply.get("planId"), "planId")
    expected = SAVE_CONFIRMATION_PREFIX + plan_id
    if not isinstance(confirmation, str) or confirmation != expected:
        raise RewireWriteError(
            "blueprint-rewire-confirmation-required",
            f"Save requires the exact confirmation: {expected}.",
        )
    return {
        "assetPath": _string(apply.get("assetPath"), "assetPath", maximum=MAX_ASSET_PATH),
        "graphGuid": _guid(apply.get("graphGuid"), "graphGuid"),
        "planId": plan_id,
        "expectedEdgeSetHashAfter": _sha256(
            apply.get("edgeSetHashAfter"),
            "edgeSetHashAfter",
        ),
        "expectedPackageRevision": _sha256(
            apply.get("packageRevision"),
            "packageRevision",
        ),
        "expectedNodeCount": _count(apply.get("nodeCount"), "nodeCount"),
        "expectedLinkCount": _count(apply.get("linkCount"), "linkCount"),
        "confirmation": confirmation,
    }


def validate_save_result(result: Any, apply_result: Any) -> dict[str, Any]:
    """Validate the exact Save result against the Apply that produced it."""

    apply = _object(apply_result, "apply result")
    plan_id = _sha256(apply.get("planId"), "planId")
    item = _object(result, "save result")
    try:
        schema = _string(item.get("schemaVersion"), "schemaVersion", maximum=64)
        if schema != SAVE_SCHEMA_VERSION:
            raise RewireWriteError(
                "blueprint-rewire-save-invalid",
                f"schemaVersion must be {SAVE_SCHEMA_VERSION}.",
            )
        for flag in ("saved", "readbackVerified"):
            if item.get(flag) is not True:
                raise RewireWriteError(
                    "blueprint-rewire-save-invalid",
                    f"Save must report {flag}=true.",
                )
        for flag in ("packageDirty", "compiled", "skeletonRegenerated", "independentReloadVerified"):
            if item.get(flag) is not False:
                raise RewireWriteError(
                    "blueprint-rewire-save-invalid",
                    f"Save must report {flag}=false.",
                )
        if item.get("requiredAcceptanceEvidence") != "independent-reload":
            raise RewireWriteError(
                "blueprint-rewire-save-invalid",
                "Save must require independent-reload evidence.",
            )
        if _sha256(item.get("planId"), "planId") != plan_id:
            raise RewireWriteError(
                "blueprint-rewire-save-invalid",
                "Save planId does not match the Apply.",
            )
        if _sha256(item.get("edgeSetHashAfter"), "edgeSetHashAfter") != _sha256(
            apply.get("edgeSetHashAfter"),
            "edgeSetHashAfter",
        ):
            raise RewireWriteError(
                "blueprint-rewire-save-invalid",
                "Save edgeSetHashAfter does not match the Apply.",
            )
        if _count(item.get("nodeCount"), "nodeCount") != _count(
            apply.get("nodeCount"),
            "nodeCount",
        ):
            raise RewireWriteError(
                "blueprint-rewire-save-invalid",
                "Save nodeCount does not match the Apply.",
            )
        if _count(item.get("linkCount"), "linkCount") != _count(
            apply.get("linkCount"),
            "linkCount",
        ):
            raise RewireWriteError(
                "blueprint-rewire-save-invalid",
                "Save linkCount does not match the Apply.",
            )
        return {
            "schemaVersion": schema,
            "saved": True,
            "readbackVerified": True,
            "packageDirty": False,
            "compiled": False,
            "skeletonRegenerated": False,
            "independentReloadVerified": False,
            "requiredAcceptanceEvidence": "independent-reload",
            "planId": plan_id,
            "assetPath": _string(item.get("assetPath"), "assetPath", maximum=MAX_ASSET_PATH),
            "graphGuid": _guid(item.get("graphGuid"), "graphGuid"),
            "operation": _string(item.get("operation"), "operation", maximum=16),
            "packageRevision": _sha256(item.get("packageRevision"), "packageRevision"),
            "assetSha256": _sha256(item.get("assetSha256"), "assetSha256"),
            "assetSizeBytes": _count(item.get("assetSizeBytes"), "assetSizeBytes"),
            "edgeSetHashBefore": _sha256(item.get("edgeSetHashBefore"), "edgeSetHashBefore"),
            "edgeSetHashAfter": _sha256(item.get("edgeSetHashAfter"), "edgeSetHashAfter"),
            "pinIdentityHash": _sha256(item.get("pinIdentityHash"), "pinIdentityHash"),
            "nodeCount": _count(item.get("nodeCount"), "nodeCount"),
            "linkCount": _count(item.get("linkCount"), "linkCount"),
            "linkCountBefore": _count(item.get("linkCountBefore"), "linkCountBefore"),
            "removedEdges": item.get("removedEdges"),
            "addedEdges": item.get("addedEdges"),
        }
    except RewireWriteError as exc:
        if exc.code == "blueprint-rewire-save-invalid":
            raise
        raise RewireWriteError(
            "blueprint-rewire-save-invalid",
            f"Save result is not a valid rewire save: {exc.message}",
        ) from exc


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RewireWriteError(
            "blueprint-rewire-preview-invalid",
            f"{label} must be a non-negative integer.",
        )
    return value


__all__ = [
    "APPLY_CAPABILITY",
    "APPLY_CONFIRMATION_PREFIX",
    "APPLY_METHOD",
    "APPLY_TOOL_NAME",
    "MAX_REQUEST_BYTES",
    "PREVIEW_CAPABILITY",
    "PREVIEW_METHOD",
    "PREVIEW_TOOL_NAME",
    "RewireWriteError",
    "SAVE_CAPABILITY",
    "SAVE_CONFIRMATION_PREFIX",
    "SAVE_METHOD",
    "SAVE_TOOL_NAME",
    "build_apply_parameters",
    "build_save_parameters",
    "confirmation_for_plan",
    "save_confirmation_for_plan",
    "validate_apply_result",
    "validate_preview_parameters",
    "validate_preview_result",
    "validate_save_result",
]

"""Bounded, opt-in Blueprint single-node removal contract.

This is the P4 structural slice.  One request removes exactly one
already-existing node from the exact focused top-level K2 event graph, and only
when that node is a deterministic, side-effect-free allowlisted class (an
allowlisted pure Kismet math call or a branch) with at most one incident link.
Every request binds the exact node GUID + node class, the on-disk package
revision, the live graph fingerprint, the complete before edge-set hash and the
complete list of incident edges that will disappear.

The contract never accepts a caller-built bridge request: Apply only consumes
the complete Preview result plus the exact confirmation token, and Save only
consumes the complete Apply result plus its own confirmation token.  The live
native module breaks only the enumerated edges, removes only that node inside
one transaction, proves the complete after node/pin/edge sets, verifies Undo
restores the node and its links and Redo removes them again, and never compiles
or auto-saves.
"""

from __future__ import annotations

import re
from typing import Any

PREVIEW_TOOL_NAME = "ue_preview_blueprint_node_remove_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_node_remove_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_node_remove_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintNodeRemoveExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintNodeRemoveExperimental"
SAVE_CAPABILITY = "editor.saveBlueprintNodeRemoveExperimental"
PREVIEW_METHOD = "editor.previewBlueprintNodeRemoveExperimental"
APPLY_METHOD = "editor.applyBlueprintNodeRemoveExperimental"
SAVE_METHOD = "editor.saveBlueprintNodeRemoveExperimental"
APPLY_CONFIRMATION_PREFIX = "REMOVE EXPERIMENTAL BLUEPRINT NODE "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT NODE REMOVE "

PREVIEW_SCHEMA_VERSION = "ueak.blueprint-node-remove-preview/1.0"
APPLY_SCHEMA_VERSION = "ueak.blueprint-node-remove-apply/1.0"
SAVE_SCHEMA_VERSION = "ueak.blueprint-node-remove-save/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_PIN_NAME = 128
MAX_ASSET_PATH = 512
MAX_NODE_CLASS = 256
MAX_NODE_TITLE = 256
MAX_NODES_PER_PLAN = 1
MAX_INCIDENT_EDGES = 1

PIN_KINDS = ("exec", "bool", "int", "double", "string")

_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_SHA1 = re.compile(r"^sha1:[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_NODE_CLASS = re.compile(r"^/Script/[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_PIN_FORBIDDEN = ("|", ">", "\n", "\r", "\t")


class NodeRemoveWriteError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-parameters",
            f"{label} must be an object.",
        )
    return value


def _string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        requirement = f"at most {maximum} characters"
        if not allow_empty:
            requirement = "non-empty " + requirement
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-parameters",
            f"{label} must be a string of {requirement}.",
        )
    return value


def _guid(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=36)
    if _GUID.fullmatch(normalized) is None:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-identifier",
            f"{label} must be one canonical lowercase GUID.",
        )
    return normalized


def _sha256(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=71)
    if _SHA256.fullmatch(normalized) is None:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-identifier",
            f"{label} must be one canonical lowercase sha256 value.",
        )
    return normalized


def _sha1(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=45)
    if _SHA1.fullmatch(normalized) is None:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-identifier",
            f"{label} must be one canonical lowercase sha1 value.",
        )
    return normalized


def _node_class(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=MAX_NODE_CLASS)
    if _NODE_CLASS.fullmatch(normalized) is None:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-parameters",
            f"{label} must be one exact /Script/<module>.<Class> path.",
        )
    return normalized


def _pin_name(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=MAX_PIN_NAME)
    if any(character in normalized for character in _PIN_FORBIDDEN):
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-parameters",
            f"{label} must not contain '|', '>' or control characters.",
        )
    if any(ord(character) < 0x20 for character in normalized):
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-parameters",
            f"{label} must not contain control characters.",
        )
    return normalized


def _pin_kind(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=16)
    if normalized not in PIN_KINDS:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-preview-invalid",
            f"{label} must be one of: {', '.join(PIN_KINDS)}.",
        )
    return normalized


def _request_edge(value: Any, label: str) -> dict[str, str]:
    item = _object(value, label)
    allowed = {
        "fromNodeGuid",
        "fromPinName",
        "fromPinGuid",
        "toNodeGuid",
        "toPinName",
        "toPinGuid",
    }
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-parameters",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )
    return {
        "fromNodeGuid": _guid(item.get("fromNodeGuid"), f"{label}.fromNodeGuid"),
        "fromPinName": _pin_name(item.get("fromPinName"), f"{label}.fromPinName"),
        "fromPinGuid": _guid(item.get("fromPinGuid"), f"{label}.fromPinGuid"),
        "toNodeGuid": _guid(item.get("toNodeGuid"), f"{label}.toNodeGuid"),
        "toPinName": _pin_name(item.get("toPinName"), f"{label}.toPinName"),
        "toPinGuid": _guid(item.get("toPinGuid"), f"{label}.toPinGuid"),
    }


def _result_edge(value: Any, label: str) -> dict[str, str]:
    item = _object(value, label)
    allowed = {
        "fromNodeGuid",
        "fromPinName",
        "fromPinGuid",
        "fromKind",
        "toNodeGuid",
        "toPinName",
        "toPinGuid",
        "toKind",
    }
    unknown = sorted(set(item) - allowed)
    if unknown:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-preview-invalid",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )
    return {
        "fromNodeGuid": _guid(item.get("fromNodeGuid"), f"{label}.fromNodeGuid"),
        "fromPinName": _pin_name(item.get("fromPinName"), f"{label}.fromPinName"),
        "fromPinGuid": _guid(item.get("fromPinGuid"), f"{label}.fromPinGuid"),
        "fromKind": _pin_kind(item.get("fromKind"), f"{label}.fromKind"),
        "toNodeGuid": _guid(item.get("toNodeGuid"), f"{label}.toNodeGuid"),
        "toPinName": _pin_name(item.get("toPinName"), f"{label}.toPinName"),
        "toPinGuid": _guid(item.get("toPinGuid"), f"{label}.toPinGuid"),
        "toKind": _pin_kind(item.get("toKind"), f"{label}.toKind"),
    }


def validate_preview_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    node_guid: Any,
    expected_node_class: Any,
    expected_edge_set_hash: Any,
    removed_edges: Any,
) -> dict[str, Any]:
    """Validate one bounded node-removal preview request."""

    if not isinstance(removed_edges, list):
        raise NodeRemoveWriteError(
            "blueprint-node-remove-invalid-parameters",
            "removedEdges must be an array (empty for an isolated node).",
        )
    if len(removed_edges) > MAX_INCIDENT_EDGES:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-removed-edges-invalid",
            f"A node-removal plan may list at most {MAX_INCIDENT_EDGES} incident edge.",
        )
    normalized_edges: list[dict[str, str]] = []
    seen: set[tuple[str, ...]] = set()
    for index, edge in enumerate(removed_edges):
        normalized = _request_edge(edge, f"removedEdges[{index}]")
        identity = tuple(normalized[key] for key in sorted(normalized))
        if identity in seen:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-removed-edges-invalid",
                "removedEdges must not contain duplicate entries.",
            )
        seen.add(identity)
        normalized_edges.append(normalized)
    return {
        "assetPath": _string(asset_path, "assetPath", maximum=MAX_ASSET_PATH),
        "graphGuid": _guid(graph_guid, "graphGuid"),
        "nodeGuid": _guid(node_guid, "nodeGuid"),
        "expectedNodeClass": _node_class(expected_node_class, "expectedNodeClass"),
        "expectedEdgeSetHash": _sha256(expected_edge_set_hash, "expectedEdgeSetHash"),
        "removedEdges": normalized_edges,
    }


def validate_preview_result(result: Any) -> dict[str, Any]:
    """Validate the exact read-only Preview result and return a normalized copy."""

    item = _object(result, "preview result")
    try:
        schema = _string(item.get("schemaVersion"), "schemaVersion", maximum=64)
        if schema != PREVIEW_SCHEMA_VERSION:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                f"schemaVersion must be {PREVIEW_SCHEMA_VERSION}.",
            )
        if item.get("experimental") is not True or item.get("readOnly") is not True:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "Preview must be experimental and read-only.",
            )
        for flag in (
            "wroteBlueprint",
            "savedPackage",
            "compiled",
            "skeletonRegenerated",
            "packageDirty",
        ):
            if item.get(flag) is not False:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-preview-invalid",
                    f"Preview must report {flag}=false.",
                )
        plan_id = _sha256(item.get("planId"), "planId")
        if item.get("requiredConfirmation") != APPLY_CONFIRMATION_PREFIX + plan_id:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "requiredConfirmation must bind the exact planId.",
            )
        node_guid = _guid(item.get("nodeGuid"), "nodeGuid")
        node_class = _node_class(item.get("nodeClass"), "nodeClass")
        removable_classes = item.get("removableNodeClasses")
        if (
            not isinstance(removable_classes, list)
            or not removable_classes
            or node_class not in removable_classes
        ):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "nodeClass must appear in removableNodeClasses.",
            )
        removable_functions = item.get("removableFunctions")
        if not isinstance(removable_functions, list) or not all(
            isinstance(value, str) and value for value in removable_functions
        ):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "removableFunctions must be an array of function names.",
            )
        max_nodes = _count(item.get("maxNodesPerPlan"), "maxNodesPerPlan")
        max_edges = _count(item.get("maxIncidentEdges"), "maxIncidentEdges")
        if max_nodes != MAX_NODES_PER_PLAN or max_edges != MAX_INCIDENT_EDGES:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "Preview limits do not match the bounded contract.",
            )
        edge_before = _sha256(item.get("edgeSetHashBefore"), "edgeSetHashBefore")
        edge_after = _sha256(item.get("edgeSetHashAfter"), "edgeSetHashAfter")
        untouched_hash = _sha256(item.get("untouchedEdgesHash"), "untouchedEdgesHash")
        pin_before = _sha256(item.get("pinIdentityHashBefore"), "pinIdentityHashBefore")
        pin_after = _sha256(item.get("pinIdentityHashAfter"), "pinIdentityHashAfter")
        node_after_hash = _sha256(item.get("nodeIdentityHashAfter"), "nodeIdentityHashAfter")
        package_revision = _sha256(item.get("packageRevision"), "packageRevision")
        graph_fingerprint = _sha1(item.get("graphFingerprint"), "graphFingerprint")
        node_before = _count(item.get("nodeCountBefore"), "nodeCountBefore")
        node_after = _count(item.get("nodeCountAfter"), "nodeCountAfter")
        link_before = _count(item.get("linkCountBefore"), "linkCountBefore")
        link_after = _count(item.get("linkCountAfter"), "linkCountAfter")
        node_pin_count = _count(item.get("nodePinCount"), "nodePinCount")
        removed_count = _count(item.get("removedEdgeCount"), "removedEdgeCount")
        untouched = _count(item.get("untouchedEdgeCount"), "untouchedEdgeCount")
        if node_before < 1 or node_after != node_before - 1:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "A node removal must remove exactly one node.",
            )
        if removed_count > MAX_INCIDENT_EDGES:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "A plan may remove at most one incident edge.",
            )
        if link_after != link_before - removed_count:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "linkCountAfter must equal linkCountBefore - removedEdgeCount.",
            )
        if untouched != link_before - removed_count:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "untouchedEdgeCount must equal linkCountBefore - removedEdgeCount.",
            )
        if item.get("isolatedNode") is not (removed_count == 0):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "isolatedNode must agree with removedEdgeCount.",
            )
        if removed_count == 0:
            if edge_before != edge_after or pin_before == pin_after:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-preview-invalid",
                    "An isolated-node removal must keep the edge set and change the pin identity set.",
                )
        elif edge_before == edge_after or pin_before == pin_after:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "Removing a connected node must change both the edge set and the pin identity set.",
            )
        removed = item.get("removedEdges")
        if not isinstance(removed, list) or len(removed) != removed_count:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-preview-invalid",
                "removedEdges must contain exactly removedEdgeCount entries.",
            )
        removed_edges = [_result_edge(edge, f"removedEdges[{index}]") for index, edge in enumerate(removed)]
        for index, edge in enumerate(removed_edges):
            if node_guid not in (edge["fromNodeGuid"], edge["toNodeGuid"]):
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-preview-invalid",
                    f"removedEdges[{index}] must touch the removed node.",
                )
            if edge["fromKind"] != edge["toKind"]:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-preview-invalid",
                    f"removedEdges[{index}] must connect equal pin kinds.",
                )
        return {
            "schemaVersion": schema,
            "experimental": True,
            "readOnly": True,
            "planId": plan_id,
            "assetPath": _string(item.get("assetPath"), "assetPath", maximum=MAX_ASSET_PATH),
            "graphGuid": _guid(item.get("graphGuid"), "graphGuid"),
            "nodeGuid": node_guid,
            "nodeClass": node_class,
            "nodeTitle": _string(
                item.get("nodeTitle"),
                "nodeTitle",
                maximum=MAX_NODE_TITLE,
                allow_empty=True,
            ),
            "packageRevision": package_revision,
            "graphFingerprint": graph_fingerprint,
            "edgeSetHashBefore": edge_before,
            "edgeSetHashAfter": edge_after,
            "untouchedEdgesHash": untouched_hash,
            "pinIdentityHashBefore": pin_before,
            "pinIdentityHashAfter": pin_after,
            "nodeIdentityHashAfter": node_after_hash,
            "nodePinCount": node_pin_count,
            "nodeCountBefore": node_before,
            "nodeCountAfter": node_after,
            "linkCountBefore": link_before,
            "linkCountAfter": link_after,
            "removedEdgeCount": removed_count,
            "untouchedEdgeCount": untouched,
            "isolatedNode": removed_count == 0,
            "removedEdges": removed_edges,
            "removableNodeClasses": list(removable_classes),
            "removableFunctions": list(removable_functions),
            "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + plan_id,
        }
    except NodeRemoveWriteError as exc:
        if exc.code == "blueprint-node-remove-preview-invalid":
            raise
        raise NodeRemoveWriteError(
            "blueprint-node-remove-preview-invalid",
            f"Preview result is not a valid node-removal preview: {exc.message}",
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
        raise NodeRemoveWriteError(
            "blueprint-node-remove-confirmation-required",
            f"Apply requires the exact confirmation: {expected}.",
        )
    return {
        "assetPath": preview["assetPath"],
        "graphGuid": preview["graphGuid"],
        "nodeGuid": preview["nodeGuid"],
        "expectedNodeClass": preview["nodeClass"],
        "expectedEdgeSetHash": preview["edgeSetHashBefore"],
        "removedEdges": [
            {
                "fromNodeGuid": edge["fromNodeGuid"],
                "fromPinName": edge["fromPinName"],
                "fromPinGuid": edge["fromPinGuid"],
                "toNodeGuid": edge["toNodeGuid"],
                "toPinName": edge["toPinName"],
                "toPinGuid": edge["toPinGuid"],
            }
            for edge in preview["removedEdges"]
        ],
        "planId": preview["planId"],
        "confirmation": confirmation,
    }


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
            raise NodeRemoveWriteError(
                "blueprint-node-remove-apply-invalid",
                f"schemaVersion must be {APPLY_SCHEMA_VERSION}.",
            )
        for flag in ("applied", "undoVerified", "redoVerified"):
            if item.get(flag) is not True:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-apply-invalid",
                    f"Apply must report {flag}=true.",
                )
        for flag in ("saved", "compiled", "skeletonRegenerated"):
            if item.get(flag) is not False:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-apply-invalid",
                    f"Apply must report {flag}=false.",
                )
        if item.get("packageDirty") is not True:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-apply-invalid",
                "Apply must leave the package dirty for the explicit Save gate.",
            )
        plan_id = _sha256(item.get("planId"), "planId")
        if plan_id != preview["planId"]:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-apply-invalid",
                "Apply planId does not match the Preview.",
            )
        if item.get("requiredSaveConfirmation") != SAVE_CONFIRMATION_PREFIX + plan_id:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-apply-invalid",
                "requiredSaveConfirmation must bind the exact planId.",
            )
        expected_fields = (
            ("assetPath", "assetPath", None),
            ("graphGuid", "graphGuid", None),
            ("nodeGuid", "nodeGuid", None),
            ("nodeClass", "nodeClass", None),
            ("packageRevision", "packageRevision", "sha256"),
            ("graphFingerprint", "graphFingerprint", "sha1"),
            ("edgeSetHashBefore", "edgeSetHashBefore", "sha256"),
            ("edgeSetHashAfter", "edgeSetHashAfter", "sha256"),
            ("pinIdentityHashBefore", "pinIdentityHashBefore", "sha256"),
            ("pinIdentityHashAfter", "pinIdentityHashAfter", "sha256"),
            ("nodeIdentityHashAfter", "nodeIdentityHashAfter", "sha256"),
        )
        normalized: dict[str, Any] = {}
        for field, preview_field, kind in expected_fields:
            value = item.get(field)
            if kind == "sha256":
                actual = _sha256(value, field)
            elif kind == "sha1":
                actual = _sha1(value, field)
            elif field == "graphGuid":
                actual = _guid(value, field)
            else:
                actual = _string(value, field, maximum=MAX_ASSET_PATH)
            if actual != preview[preview_field]:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-apply-invalid",
                    f"Apply {field} does not match the Preview.",
                )
            normalized[field] = actual
        removed = item.get("removedEdges")
        if not isinstance(removed, list) or len(removed) != preview["removedEdgeCount"]:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-apply-invalid",
                "Apply removedEdges count does not match the Preview.",
            )
        removed_edges = [
            _result_edge(edge, f"removedEdges[{index}]") for index, edge in enumerate(removed)
        ]
        if removed_edges != preview["removedEdges"]:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-apply-invalid",
                "Apply removedEdges does not match the Preview.",
            )
        node_count = _count(item.get("nodeCount"), "nodeCount")
        link_count = _count(item.get("linkCount"), "linkCount")
        node_before = _count(item.get("nodeCountBefore"), "nodeCountBefore")
        link_before = _count(item.get("linkCountBefore"), "linkCountBefore")
        node_pin_count = _count(item.get("nodePinCount"), "nodePinCount")
        if (
            node_count != preview["nodeCountAfter"]
            or node_before != preview["nodeCountBefore"]
            or link_count != preview["linkCountAfter"]
            or link_before != preview["linkCountBefore"]
            or node_pin_count != preview["nodePinCount"]
        ):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-apply-invalid",
                "Apply node/link counts do not match the Preview.",
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
            "nodeGuid": preview["nodeGuid"],
            "nodeClass": preview["nodeClass"],
            "nodeTitle": preview["nodeTitle"],
            "packageRevision": preview["packageRevision"],
            "graphFingerprint": preview["graphFingerprint"],
            "edgeSetHashBefore": preview["edgeSetHashBefore"],
            "edgeSetHashAfter": preview["edgeSetHashAfter"],
            "pinIdentityHashBefore": preview["pinIdentityHashBefore"],
            "pinIdentityHashAfter": preview["pinIdentityHashAfter"],
            "nodeIdentityHashAfter": preview["nodeIdentityHashAfter"],
            "nodeCount": node_count,
            "nodeCountBefore": node_before,
            "nodeCountAfter": node_count,
            "linkCount": link_count,
            "linkCountBefore": link_before,
            "linkCountAfter": link_count,
            "removedEdgeCount": len(removed_edges),
            "removedEdges": removed_edges,
            "nodePinCount": node_pin_count,
            "requiredSaveConfirmation": SAVE_CONFIRMATION_PREFIX + plan_id,
        }
    except NodeRemoveWriteError as exc:
        if exc.code == "blueprint-node-remove-apply-invalid":
            raise
        raise NodeRemoveWriteError(
            "blueprint-node-remove-apply-invalid",
            f"Apply result is not a valid node-removal apply: {exc.message}",
        ) from exc


def build_save_parameters(apply_result: Any, confirmation: Any) -> dict[str, Any]:
    """Build the exact Save request from the complete Apply result."""

    apply = _object(apply_result, "apply result")
    plan_id = _sha256(apply.get("planId"), "planId")
    expected = SAVE_CONFIRMATION_PREFIX + plan_id
    if not isinstance(confirmation, str) or confirmation != expected:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-confirmation-required",
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
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                f"schemaVersion must be {SAVE_SCHEMA_VERSION}.",
            )
        for flag in ("saved", "readbackVerified"):
            if item.get(flag) is not True:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-save-invalid",
                    f"Save must report {flag}=true.",
                )
        for flag in (
            "packageDirty",
            "compiled",
            "skeletonRegenerated",
            "independentReloadVerified",
        ):
            if item.get(flag) is not False:
                raise NodeRemoveWriteError(
                    "blueprint-node-remove-save-invalid",
                    f"Save must report {flag}=false.",
                )
        if item.get("requiredAcceptanceEvidence") != "independent-reload":
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save must require independent-reload evidence.",
            )
        if _sha256(item.get("planId"), "planId") != plan_id:
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save planId does not match the Apply.",
            )
        if _sha256(item.get("edgeSetHashAfter"), "edgeSetHashAfter") != _sha256(
            apply.get("edgeSetHashAfter"),
            "edgeSetHashAfter",
        ):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save edgeSetHashAfter does not match the Apply.",
            )
        if _count(item.get("nodeCount"), "nodeCount") != _count(
            apply.get("nodeCount"),
            "nodeCount",
        ):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save nodeCount does not match the Apply.",
            )
        if _count(item.get("linkCount"), "linkCount") != _count(
            apply.get("linkCount"),
            "linkCount",
        ):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save linkCount does not match the Apply.",
            )
        removed = item.get("removedEdges")
        if not isinstance(removed, list) or len(removed) != _count(
            apply.get("removedEdgeCount"),
            "removedEdgeCount",
        ):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save removedEdges count does not match the Apply.",
            )
        removed_count = _count(item.get("removedEdgeCount"), "removedEdgeCount")
        if removed_count != _count(apply.get("removedEdgeCount"), "removedEdgeCount"):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save removedEdgeCount does not match the Apply.",
            )
        if removed_count != len(removed):
            raise NodeRemoveWriteError(
                "blueprint-node-remove-save-invalid",
                "Save removedEdges count does not match removedEdgeCount.",
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
            "nodeGuid": _guid(item.get("nodeGuid"), "nodeGuid"),
            "nodeClass": _node_class(item.get("nodeClass"), "nodeClass"),
            "packageRevision": _sha256(item.get("packageRevision"), "packageRevision"),
            "assetSha256": _sha256(item.get("assetSha256"), "assetSha256"),
            "assetSizeBytes": _count(item.get("assetSizeBytes"), "assetSizeBytes"),
            "edgeSetHashBefore": _sha256(item.get("edgeSetHashBefore"), "edgeSetHashBefore"),
            "edgeSetHashAfter": _sha256(item.get("edgeSetHashAfter"), "edgeSetHashAfter"),
            "pinIdentityHashAfter": _sha256(
                item.get("pinIdentityHashAfter"),
                "pinIdentityHashAfter",
            ),
            "nodeIdentityHashAfter": _sha256(
                item.get("nodeIdentityHashAfter"),
                "nodeIdentityHashAfter",
            ),
            "nodeCount": _count(item.get("nodeCount"), "nodeCount"),
            "nodeCountBefore": _count(item.get("nodeCountBefore"), "nodeCountBefore"),
            "linkCount": _count(item.get("linkCount"), "linkCount"),
            "linkCountBefore": _count(item.get("linkCountBefore"), "linkCountBefore"),
            "removedEdgeCount": len(removed),
            "removedEdges": removed,
        }
    except NodeRemoveWriteError as exc:
        if exc.code == "blueprint-node-remove-save-invalid":
            raise
        raise NodeRemoveWriteError(
            "blueprint-node-remove-save-invalid",
            f"Save result is not a valid node-removal save: {exc.message}",
        ) from exc


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NodeRemoveWriteError(
            "blueprint-node-remove-preview-invalid",
            f"{label} must be a non-negative integer.",
        )
    return value


__all__ = [
    "APPLY_CAPABILITY",
    "APPLY_CONFIRMATION_PREFIX",
    "APPLY_METHOD",
    "APPLY_TOOL_NAME",
    "MAX_INCIDENT_EDGES",
    "MAX_NODES_PER_PLAN",
    "MAX_REQUEST_BYTES",
    "NodeRemoveWriteError",
    "PREVIEW_CAPABILITY",
    "PREVIEW_METHOD",
    "PREVIEW_TOOL_NAME",
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

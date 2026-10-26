"""Bounded, explicitly opted-in createNode/connectPins edit contract.

This module does not touch Unreal.  It validates the small request/result
envelope used by the live preview/apply tools and binds Apply to the complete
preview result plus the exact confirmation phrase.
"""

from __future__ import annotations

import json
import re
from typing import Any

PREVIEW_TOOL_NAME = "ue_preview_blueprint_graph_edit_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_graph_edit_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintGraphEditExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintGraphEditExperimental"
PREVIEW_METHOD = "editor.previewBlueprintGraphEditExperimental"
APPLY_METHOD = "editor.applyBlueprintGraphEditExperimental"
APPLY_CONFIRMATION_PREFIX = "CREATE EXPERIMENTAL GRAPH NODES "

PREVIEW_SCHEMA_VERSION = "ueak.graphpatch-edit-preview/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_NODES = 8
MAX_CONNECTIONS = 12
MAX_CONNECTIONS_PER_NODE = 6
MAX_COORDINATE = 100_000

NODE_TYPES = {
    "branch": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_IfThenElse",
        "inputPins": ("execute", "Condition"),
        "outputPins": ("then", "else"),
        "inputPinTypes": {"execute": "exec", "Condition": "bool"},
        "outputPinTypes": {"then": "exec", "else": "exec"},
    },
    "sequence": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_ExecutionSequence",
        "inputPins": ("execute",),
        "outputPins": ("then_0", "then_1"),
        "inputPinTypes": {"execute": "exec"},
        "outputPinTypes": {"then_0": "exec", "then_1": "exec"},
    },
    "not_bool": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
        "function": "KismetMathLibrary.Not_PreBool",
        "inputPins": ("A",),
        "outputPins": ("ReturnValue",),
        "inputPinTypes": {"A": "bool"},
        "outputPinTypes": {"ReturnValue": "bool"},
    },
}

_TEMP_ID = re.compile(r"^tmp:[A-Za-z0-9_-]{1,36}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")


class GraphEditError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GraphEditError(
            "graph-edit-invalid-parameters",
            f"{label} must be an object.",
        )
    return value


def _string(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise GraphEditError(
            "graph-edit-invalid-parameters",
            f"{label} must be a non-empty string of at most {maximum} characters.",
        )
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GraphEditError(
            "graph-edit-invalid-parameters",
            f"{label} must be an integer.",
        )
    if value < -MAX_COORDINATE or value > MAX_COORDINATE:
        raise GraphEditError(
            "graph-edit-invalid-parameters",
            f"{label} must be between -{MAX_COORDINATE} and {MAX_COORDINATE}.",
        )
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise GraphEditError(
            "graph-edit-unknown-parameter",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )


def validate_preview_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    nodes: Any,
    connections: Any,
) -> dict[str, Any]:
    """Validate and normalize one bounded preview request."""

    normalized_asset = _string(asset_path, "assetPath", maximum=512)
    normalized_graph = _string(graph_guid, "graphGuid", maximum=64)
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= MAX_NODES:
        raise GraphEditError(
            "graph-edit-invalid-nodes",
            f"nodes must contain between 1 and {MAX_NODES} objects.",
        )
    if not isinstance(connections, list) or len(connections) > MAX_CONNECTIONS:
        raise GraphEditError(
            "graph-edit-invalid-connections",
            f"connections must contain at most {MAX_CONNECTIONS} objects.",
        )

    normalized_nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw in enumerate(nodes):
        item = _object(raw, f"nodes[{index}]")
        _reject_unknown(item, {"tempId", "nodeType", "x", "y"}, f"nodes[{index}]")
        temp_id = _string(item.get("tempId"), f"nodes[{index}].tempId", maximum=40)
        node_type = _string(item.get("nodeType"), f"nodes[{index}].nodeType", maximum=16)
        if not _TEMP_ID.fullmatch(temp_id):
            raise GraphEditError(
                "graph-edit-temp-id-invalid",
                "tempId must be 'tmp:' followed by 1-36 ASCII alphanumeric, '_' or '-' characters.",
            )
        if temp_id in seen_ids:
            raise GraphEditError(
                "graph-edit-temp-id-duplicate",
                f"Duplicate tempId: {temp_id}.",
            )
        seen_ids.add(temp_id)
        if node_type not in NODE_TYPES:
            raise GraphEditError(
                "graph-edit-node-type-not-allowed",
                "Supported nodeTypes: " + ", ".join(sorted(NODE_TYPES)) + ".",
            )
        normalized_nodes.append(
            {
                "tempId": temp_id,
                "nodeType": node_type,
                "x": _integer(item.get("x"), f"nodes[{index}].x"),
                "y": _integer(item.get("y"), f"nodes[{index}].y"),
            }
        )

    by_id = {node["tempId"]: node for node in normalized_nodes}
    normalized_connections: list[dict[str, str]] = []
    seen_sources: set[tuple[str, str]] = set()
    seen_targets: set[tuple[str, str]] = set()
    connection_counts: dict[str, int] = {}
    for index, raw in enumerate(connections):
        item = _object(raw, f"connections[{index}]")
        _reject_unknown(
            item,
            {"from", "fromPin", "to", "toPin"},
            f"connections[{index}]",
        )
        source = _string(item.get("from"), f"connections[{index}].from", maximum=40)
        source_pin = _string(
            item.get("fromPin"),
            f"connections[{index}].fromPin",
            maximum=32,
        )
        target = _string(item.get("to"), f"connections[{index}].to", maximum=40)
        target_pin = _string(
            item.get("toPin"),
            f"connections[{index}].toPin",
            maximum=32,
        )
        if source not in by_id or target not in by_id:
            raise GraphEditError(
                "graph-edit-node-reference-unknown",
                f"Connection references an unknown tempId: {source} -> {target}.",
            )
        if source == target:
            raise GraphEditError(
                "graph-edit-self-connection",
                "A node cannot connect to itself in this experimental slice.",
            )
        source_contract = NODE_TYPES[by_id[source]["nodeType"]]
        target_contract = NODE_TYPES[by_id[target]["nodeType"]]
        source_type = source_contract["outputPinTypes"].get(source_pin)
        target_type = target_contract["inputPinTypes"].get(target_pin)
        if source_type is None:
            raise GraphEditError(
                "graph-edit-pin-not-allowed",
                f"{source_pin} is not a supported output pin of {by_id[source]['nodeType']}.",
            )
        if target_type is None:
            raise GraphEditError(
                "graph-edit-pin-not-allowed",
                f"{target_pin} is not a supported input pin of {by_id[target]['nodeType']}.",
            )
        if source_type != target_type:
            raise GraphEditError(
                "graph-edit-pin-type-mismatch",
                f"Cannot connect {source}.{source_pin} ({source_type}) to "
                f"{target}.{target_pin} ({target_type}); data kinds must match exactly.",
            )
        source_key = (source, source_pin)
        if source_key in seen_sources:
            raise GraphEditError(
                "graph-edit-connection-duplicate",
                f"Output pin already has a connection in this plan: {source}.{source_pin}.",
            )
        target_key = (target, target_pin)
        if target_key in seen_targets:
            raise GraphEditError(
                "graph-edit-connection-duplicate",
                f"Input pin already has a connection in this plan: {target}.{target_pin}.",
            )
        seen_sources.add(source_key)
        seen_targets.add(target_key)
        for temp_id in (source, target):
            connection_counts[temp_id] = connection_counts.get(temp_id, 0) + 1
            if connection_counts[temp_id] > MAX_CONNECTIONS_PER_NODE:
                raise GraphEditError(
                    "graph-edit-connection-limit",
                    f"{temp_id} would exceed the {MAX_CONNECTIONS_PER_NODE}-connection per-node limit.",
                )
        normalized_connections.append(
            {
                "from": source,
                "fromPin": source_pin,
                "to": target,
                "toPin": target_pin,
            }
        )

    request = {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "nodes": normalized_nodes,
        "connections": normalized_connections,
    }
    encoded = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_REQUEST_BYTES:
        raise GraphEditError(
            "graph-edit-request-too-large",
            "The graph edit request exceeds the 60 KiB bridge boundary.",
        )
    return request


def validate_preview_result(preview: Any) -> dict[str, Any]:
    """Validate the complete live preview result used to authorize Apply."""

    item = _object(preview, "preview")
    if item.get("schemaVersion") != PREVIEW_SCHEMA_VERSION:
        raise GraphEditError(
            "graph-edit-preview-invalid",
            f"preview.schemaVersion must be {PREVIEW_SCHEMA_VERSION}.",
        )
    if item.get("readOnly") is not True:
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.readOnly must be true.",
        )
    if item.get("wroteBlueprint") is not False or item.get("savedPackage") is not False:
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview must prove it did not write or save.",
        )
    if item.get("experimental") is not True:
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.experimental must be true.",
        )
    plan_id = item.get("planId")
    if not isinstance(plan_id, str) or not _SHA256.fullmatch(plan_id):
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.planId must be one sha256 plan id.",
        )
    expected_confirmation = APPLY_CONFIRMATION_PREFIX + plan_id
    if item.get("requiredConfirmation") != expected_confirmation:
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.requiredConfirmation does not match its planId.",
        )
    asset_path = item.get("assetPath")
    graph_guid = item.get("graphGuid")
    if not isinstance(asset_path, str) or not isinstance(graph_guid, str):
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.assetPath and preview.graphGuid must be strings.",
        )
    nodes = item.get("nodes")
    connections = item.get("connections")
    if not isinstance(nodes, list) or not isinstance(connections, list):
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.nodes and preview.connections must be arrays.",
        )
    node_count_before = item.get("nodeCountBefore")
    node_count_after = item.get("nodeCountAfter")
    if (
        isinstance(node_count_before, bool)
        or isinstance(node_count_after, bool)
        or not isinstance(node_count_before, int)
        or not isinstance(node_count_after, int)
        or node_count_after != node_count_before + len(nodes)
        or item.get("connectionCount") != len(connections)
    ):
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview node/connection counts do not match the previewed plan.",
        )
    normalized = []
    for index, raw in enumerate(nodes):
        node = _object(raw, f"preview.nodes[{index}]")
        position = _object(node.get("position"), f"preview.nodes[{index}].position")
        node_type = node.get("nodeType")
        contract = NODE_TYPES.get(node_type) if isinstance(node_type, str) else None
        if contract is not None:
            for field in ("inputPins", "outputPins"):
                declared = node.get(field)
                if not isinstance(declared, list) or tuple(declared) != contract[field]:
                    raise GraphEditError(
                        "graph-edit-preview-invalid",
                        f"preview.nodes[{index}].{field} does not match nodeType {node_type}.",
                    )
            for field in ("inputPinTypes", "outputPinTypes"):
                if node.get(field) != contract[field]:
                    raise GraphEditError(
                        "graph-edit-preview-invalid",
                        f"preview.nodes[{index}].{field} does not match nodeType {node_type}.",
                    )
        normalized.append(
            {
                "tempId": node.get("tempId"),
                "nodeType": node_type,
                "x": position.get("x"),
                "y": position.get("y"),
            }
        )
    normalized_connections = []
    for index, raw in enumerate(connections):
        connection = _object(raw, f"preview.connections[{index}]")
        source_node = next(
            (node for node in nodes if node.get("tempId") == connection.get("from")),
            None,
        )
        target_node = next(
            (node for node in nodes if node.get("tempId") == connection.get("to")),
            None,
        )
        source_contract = (
            NODE_TYPES.get(source_node.get("nodeType"), {})
            if isinstance(source_node, dict)
            else {}
        )
        target_contract = (
            NODE_TYPES.get(target_node.get("nodeType"), {})
            if isinstance(target_node, dict)
            else {}
        )
        expected_from_type = source_contract.get("outputPinTypes", {}).get(connection.get("fromPin"))
        expected_to_type = target_contract.get("inputPinTypes", {}).get(connection.get("toPin"))
        if (
            expected_from_type is None
            or expected_to_type is None
            or connection.get("fromType") != expected_from_type
            or connection.get("toType") != expected_to_type
        ):
            raise GraphEditError(
                "graph-edit-preview-invalid",
                f"preview.connections[{index}] does not declare the exact pin types.",
            )
        normalized_connections.append(
            {
                "from": connection.get("from"),
                "fromPin": connection.get("fromPin"),
                "to": connection.get("to"),
                "toPin": connection.get("toPin"),
            }
        )
    return validate_preview_parameters(
        asset_path=asset_path,
        graph_guid=graph_guid,
        nodes=normalized,
        connections=normalized_connections,
    ) | {
        "planId": plan_id,
        "confirmation": expected_confirmation,
    }


def confirmation_for_plan(plan_id: str) -> str:
    return APPLY_CONFIRMATION_PREFIX + plan_id


def build_apply_parameters(preview: Any, confirmation: Any) -> dict[str, Any]:
    normalized = validate_preview_result(preview)
    expected = confirmation_for_plan(normalized["planId"])
    if not isinstance(confirmation, str) or confirmation != expected:
        raise GraphEditError(
            "graph-edit-confirmation-required",
            f"Explicit Apply confirmation must be exactly {expected}.",
        )
    return {
        "assetPath": normalized["assetPath"],
        "graphGuid": normalized["graphGuid"],
        "nodes": normalized["nodes"],
        "connections": normalized["connections"],
        "planId": normalized["planId"],
        "confirmation": confirmation,
    }

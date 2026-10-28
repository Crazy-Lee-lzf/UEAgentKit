"""Bounded, explicitly opted-in createNode/connectPins edit contract.

This module does not touch Unreal.  It validates the small request/result
envelope used by the live preview/apply tools and binds Apply to the complete
preview result plus the exact confirmation phrase.

Besides the deterministic function/branch nodes, the contract accepts
``variable_get`` / ``variable_set`` nodes that reference one already-existing
top-level bool/int/double/string member variable by concrete GUID + name.  The
variable itself is never created, renamed, retyped or defaulted here.
"""

from __future__ import annotations

import json
import re
from typing import Any

PREVIEW_TOOL_NAME = "ue_preview_blueprint_graph_edit_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_graph_edit_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_graph_edit_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintGraphEditExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintGraphEditExperimental"
SAVE_CAPABILITY = "editor.saveBlueprintGraphEditExperimental"
PREVIEW_METHOD = "editor.previewBlueprintGraphEditExperimental"
APPLY_METHOD = "editor.applyBlueprintGraphEditExperimental"
SAVE_METHOD = "editor.saveBlueprintGraphEditExperimental"
APPLY_CONFIRMATION_PREFIX = "CREATE EXPERIMENTAL GRAPH NODES "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL GRAPH NODES "

PREVIEW_SCHEMA_VERSION = "ueak.graphpatch-edit-preview/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_NODES = 16
MAX_CONNECTIONS = 24
MAX_CONNECTIONS_PER_NODE = 8
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
    "bool_or": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
        "function": "KismetMathLibrary.BooleanOR",
        "inputPins": ("A", "B"),
        "outputPins": ("ReturnValue",),
        "inputPinTypes": {"A": "bool", "B": "bool"},
        "outputPinTypes": {"ReturnValue": "bool"},
    },
    "int_add": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
        "function": "KismetMathLibrary.Add_IntInt",
        "inputPins": ("A", "B"),
        "outputPins": ("ReturnValue",),
        "inputPinTypes": {"A": "int", "B": "int"},
        "outputPinTypes": {"ReturnValue": "int"},
    },
    "int_equal": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
        "function": "KismetMathLibrary.EqualEqual_IntInt",
        "inputPins": ("A", "B"),
        "outputPins": ("ReturnValue",),
        "inputPinTypes": {"A": "int", "B": "int"},
        "outputPinTypes": {"ReturnValue": "bool"},
    },
    "double_add": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
        "function": "KismetMathLibrary.Add_DoubleDouble",
        "inputPins": ("A", "B"),
        "outputPins": ("ReturnValue",),
        "inputPinTypes": {"A": "double", "B": "double"},
        "outputPinTypes": {"ReturnValue": "double"},
    },
    "string_concat": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
        "function": "KismetStringLibrary.Concat_StrStr",
        "inputPins": ("A", "B"),
        "outputPins": ("ReturnValue",),
        "inputPinTypes": {"A": "string", "B": "string"},
        "outputPinTypes": {"ReturnValue": "string"},
    },
}

# Bounded self-context member-variable accessor nodes.  They reference an
# already-existing top-level bool/int/double/string variable by GUID + name and
# never create, rename or retype a variable.  The engine pin contract is:
#   variable_get: output <VariableName>
#   variable_set: input execute, input <VariableName>,
#                 output then, output Output_Get
VARIABLE_NODE_TYPES = {
    "variable_get": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_VariableGet",
        "access": "read",
    },
    "variable_set": {
        "nodeClass": "/Script/BlueprintGraph.K2Node_VariableSet",
        "access": "write",
    },
}
VARIABLE_TYPES = ("bool", "int", "double", "string")
VARIABLE_PIN_RESERVED = ("execute", "then", "Output_Get")


def _node_contract(node: dict[str, Any]) -> dict[str, Any] | None:
    """Return the exact declared node/pin contract for one normalized node."""

    node_type = node.get("nodeType")
    if node_type in VARIABLE_NODE_TYPES:
        variable_name = node.get("variableName")
        variable_type = node.get("variableType")
        if not isinstance(variable_name, str) or variable_type not in VARIABLE_TYPES:
            return None
        access = VARIABLE_NODE_TYPES[node_type]
        if node_type == "variable_get":
            return {
                "nodeClass": access["nodeClass"],
                "inputPins": (),
                "outputPins": (variable_name,),
                "inputPinTypes": {},
                "outputPinTypes": {variable_name: variable_type},
            }
        return {
            "nodeClass": access["nodeClass"],
            "inputPins": ("execute", variable_name),
            "outputPins": ("then", "Output_Get"),
            "inputPinTypes": {"execute": "exec", variable_name: variable_type},
            "outputPinTypes": {"then": "exec", "Output_Get": variable_type},
        }
    return NODE_TYPES.get(node_type) if isinstance(node_type, str) else None


_TEMP_ID = re.compile(r"^tmp:[A-Za-z0-9_-]{1,36}$")
_VARIABLE_GUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA1 = re.compile(r"^sha1:[0-9a-f]{40}$")


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


EXISTING_PIN_FIELDS = {
    "nodeGuid",
    "pinGuid",
    "pinName",
    "direction",
    "category",
    "subCategory",
}


def _existing_pin_type_token(category: str, subcategory: str) -> str:
    if category == "exec":
        return "exec"
    if category == "bool":
        return "bool"
    if category == "int":
        return "int"
    if category == "string":
        return "string"
    if category == "real" and subcategory == "double":
        return "double"
    if category == "double":
        return "double"
    raise GraphEditError(
        "graph-edit-existing-pin-type-not-supported",
        "Existing pins must be exec/bool/int/double/string without containers, refs, const or weak pointers.",
    )


def _existing_pin(value: Any, label: str) -> dict[str, str]:
    item = _object(value, label)
    _reject_unknown(item, EXISTING_PIN_FIELDS, label)
    node_guid = _string(item.get("nodeGuid"), f"{label}.nodeGuid", maximum=36)
    pin_guid = _string(item.get("pinGuid"), f"{label}.pinGuid", maximum=36)
    if not _VARIABLE_GUID.fullmatch(node_guid) or not _VARIABLE_GUID.fullmatch(pin_guid):
        raise GraphEditError(
            "graph-edit-existing-pin-guid-invalid",
            f"{label}.nodeGuid/pinGuid must be canonical lowercase GUIDs.",
        )
    pin_name = _string(item.get("pinName"), f"{label}.pinName", maximum=128)
    if any(character in "|\r\n" or ord(character) < 0x20 or ord(character) == 0x7F for character in pin_name):
        raise GraphEditError(
            "graph-edit-existing-pin-name-invalid",
            f"{label}.pinName contains unsupported characters.",
        )
    direction = _string(item.get("direction"), f"{label}.direction", maximum=16)
    if direction not in ("input", "output"):
        raise GraphEditError(
            "graph-edit-existing-pin-direction-invalid",
            f"{label}.direction must be input or output.",
        )
    category = _string(item.get("category"), f"{label}.category", maximum=32)
    subcategory = item.get("subCategory")
    if not isinstance(subcategory, str) or len(subcategory) > 32:
        raise GraphEditError(
            "graph-edit-existing-pin-type-not-supported",
            f"{label}.subCategory must be a string of at most 32 characters (may be empty).",
        )
    _existing_pin_type_token(category, subcategory)
    return {
        "nodeGuid": node_guid,
        "pinGuid": pin_guid,
        "pinName": pin_name,
        "direction": direction,
        "category": category,
        "subCategory": subcategory,
    }


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
        _reject_unknown(
            item,
            {
                "tempId",
                "nodeType",
                "x",
                "y",
                "variableName",
                "variableGuid",
                "variableType",
            },
            f"nodes[{index}]",
        )
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
        if node_type not in NODE_TYPES and node_type not in VARIABLE_NODE_TYPES:
            raise GraphEditError(
                "graph-edit-node-type-not-allowed",
                "Supported nodeTypes: "
                + ", ".join(sorted((*NODE_TYPES, *VARIABLE_NODE_TYPES)))
                + ".",
            )
        normalized_node: dict[str, Any] = {
            "tempId": temp_id,
            "nodeType": node_type,
            "x": _integer(item.get("x"), f"nodes[{index}].x"),
            "y": _integer(item.get("y"), f"nodes[{index}].y"),
        }
        if node_type in VARIABLE_NODE_TYPES:
            variable_name = item.get("variableName")
            variable_guid = item.get("variableGuid")
            variable_type = item.get("variableType")
            if (
                not isinstance(variable_name, str)
                or not variable_name
                or len(variable_name) > 128
                or not variable_name.isidentifier()
                or variable_name in VARIABLE_PIN_RESERVED
            ):
                raise GraphEditError(
                    "graph-edit-variable-name-invalid",
                    f"nodes[{index}].variableName must be a 1-128 character identifier "
                    f"other than {', '.join(VARIABLE_PIN_RESERVED)}.",
                )
            if not isinstance(variable_guid, str) or not _VARIABLE_GUID.fullmatch(variable_guid):
                raise GraphEditError(
                    "graph-edit-variable-guid-invalid",
                    f"nodes[{index}].variableGuid must be one canonical lowercase GUID.",
                )
            if variable_type not in VARIABLE_TYPES:
                raise GraphEditError(
                    "graph-edit-variable-type-invalid",
                    f"nodes[{index}].variableType must be bool, int, double or string.",
                )
            normalized_node.update(
                {
                    "variableName": variable_name,
                    "variableGuid": variable_guid,
                    "variableType": variable_type,
                }
            )
        elif any(
            key in item for key in ("variableName", "variableGuid", "variableType")
        ):
            raise GraphEditError(
                "graph-edit-variable-field-not-allowed",
                f"nodes[{index}] nodeType {node_type} does not accept variable fields.",
            )
        normalized_nodes.append(normalized_node)

    by_id = {node["tempId"]: node for node in normalized_nodes}
    normalized_connections: list[dict[str, Any]] = []
    seen_targets: set[str] = set()
    connection_counts: dict[str, int] = {}
    for index, raw in enumerate(connections):
        item = _object(raw, f"connections[{index}]")
        _reject_unknown(
            item,
            {"from", "fromPin", "to", "toPin", "fromExisting", "toExisting"},
            f"connections[{index}]",
        )
        has_from_existing = "fromExisting" in item
        has_to_existing = "toExisting" in item
        if has_from_existing and has_to_existing:
            raise GraphEditError(
                "graph-edit-existing-to-existing",
                "This slice allows at most one existing pin per connection.",
            )

        from_existing: dict[str, str] | None = None
        to_existing: dict[str, str] | None = None
        source: str | None = None
        source_pin: str | None = None
        target: str | None = None
        target_pin: str | None = None

        if has_from_existing:
            if "from" in item or "fromPin" in item:
                raise GraphEditError(
                    "graph-edit-existing-endpoint-conflict",
                    "fromExisting cannot be combined with from/fromPin.",
                )
            from_existing = _existing_pin(
                item.get("fromExisting"),
                f"connections[{index}].fromExisting",
            )
            if from_existing["direction"] != "output":
                raise GraphEditError(
                    "graph-edit-existing-pin-direction",
                    "fromExisting must be an output pin.",
                )
            target = _string(item.get("to"), f"connections[{index}].to", maximum=40)
            target_pin = _string(
                item.get("toPin"),
                f"connections[{index}].toPin",
                maximum=32,
            )
            if target not in by_id:
                raise GraphEditError(
                    "graph-edit-node-reference-unknown",
                    f"Connection references an unknown tempId: {target}.",
                )
            target_contract = _node_contract(by_id[target])
            if target_contract is None:
                raise GraphEditError(
                    "graph-edit-node-type-not-allowed",
                    "A connection references a node without a declared pin contract.",
                )
            source_type = _existing_pin_type_token(
                from_existing["category"],
                from_existing["subCategory"],
            )
            target_type = target_contract["inputPinTypes"].get(target_pin)
            if target_type is None:
                raise GraphEditError(
                    "graph-edit-pin-not-allowed",
                    f"{target_pin} is not a supported input pin of {by_id[target]['nodeType']}.",
                )
        elif has_to_existing:
            if "to" in item or "toPin" in item:
                raise GraphEditError(
                    "graph-edit-existing-endpoint-conflict",
                    "toExisting cannot be combined with to/toPin.",
                )
            to_existing = _existing_pin(
                item.get("toExisting"),
                f"connections[{index}].toExisting",
            )
            if to_existing["direction"] != "input":
                raise GraphEditError(
                    "graph-edit-existing-pin-direction",
                    "toExisting must be an input pin.",
                )
            source = _string(item.get("from"), f"connections[{index}].from", maximum=40)
            source_pin = _string(
                item.get("fromPin"),
                f"connections[{index}].fromPin",
                maximum=32,
            )
            if source not in by_id:
                raise GraphEditError(
                    "graph-edit-node-reference-unknown",
                    f"Connection references an unknown tempId: {source}.",
                )
            source_contract = _node_contract(by_id[source])
            if source_contract is None:
                raise GraphEditError(
                    "graph-edit-node-type-not-allowed",
                    "A connection references a node without a declared pin contract.",
                )
            source_type = source_contract["outputPinTypes"].get(source_pin)
            if source_type is None:
                raise GraphEditError(
                    "graph-edit-pin-not-allowed",
                    f"{source_pin} is not a supported output pin of {by_id[source]['nodeType']}.",
                )
            target_type = _existing_pin_type_token(
                to_existing["category"],
                to_existing["subCategory"],
            )
        else:
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
            source_contract = _node_contract(by_id[source])
            target_contract = _node_contract(by_id[target])
            if source_contract is None or target_contract is None:
                raise GraphEditError(
                    "graph-edit-node-type-not-allowed",
                    "A connection references a node without a declared pin contract.",
                )
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
            source_label = (
                f"existing:{from_existing['nodeGuid']}/{from_existing['pinName']}"
                if from_existing
                else f"{source}.{source_pin}"
            )
            target_label = (
                f"existing:{to_existing['nodeGuid']}/{to_existing['pinName']}"
                if to_existing
                else f"{target}.{target_pin}"
            )
            raise GraphEditError(
                "graph-edit-pin-type-mismatch",
                f"Cannot connect {source_label} ({source_type}) to "
                f"{target_label} ({target_type}); data kinds must match exactly.",
            )

        if to_existing:
            target_key = f"existing:{to_existing['nodeGuid']}|{to_existing['pinGuid']}"
        else:
            target_key = f"{target}|{target_pin}"
        if target_key in seen_targets:
            raise GraphEditError(
                "graph-edit-connection-duplicate",
                f"Input pin already has a connection in this plan: {target_key}.",
            )
        seen_targets.add(target_key)
        for temp_id in (source, target):
            if temp_id is None:
                continue
            connection_counts[temp_id] = connection_counts.get(temp_id, 0) + 1
            if connection_counts[temp_id] > MAX_CONNECTIONS_PER_NODE:
                raise GraphEditError(
                    "graph-edit-connection-limit",
                    f"{temp_id} would exceed the {MAX_CONNECTIONS_PER_NODE}-connection per-node limit.",
                )
        normalized_connection: dict[str, Any] = {}
        if from_existing is not None:
            normalized_connection["fromExisting"] = from_existing
        else:
            normalized_connection["from"] = source
            normalized_connection["fromPin"] = source_pin
        if to_existing is not None:
            normalized_connection["toExisting"] = to_existing
        else:
            normalized_connection["to"] = target
            normalized_connection["toPin"] = target_pin
        normalized_connections.append(normalized_connection)

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
    before_edge_set_hash = item.get("beforeEdgeSetHash")
    if before_edge_set_hash is not None and (
        not isinstance(before_edge_set_hash, str)
        or not _SHA256.fullmatch(before_edge_set_hash)
    ):
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.beforeEdgeSetHash must be a sha256 hash when present.",
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
        contract = _node_contract(node) if isinstance(node_type, str) else None
        normalized_node: dict[str, Any] = {
            "tempId": node.get("tempId"),
            "nodeType": node_type,
            "x": position.get("x"),
            "y": position.get("y"),
        }
        if node_type in VARIABLE_NODE_TYPES:
            if contract is None:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] has an invalid variable accessor declaration.",
                )
            if node.get("variableAccess") != VARIABLE_NODE_TYPES[node_type]["access"]:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}].variableAccess does not match nodeType {node_type}.",
                )
            normalized_node.update(
                {
                    "variableName": node.get("variableName"),
                    "variableGuid": node.get("variableGuid"),
                    "variableType": node.get("variableType"),
                }
            )
        elif any(
            key in node for key in ("variableName", "variableGuid", "variableType")
        ):
            raise GraphEditError(
                "graph-edit-preview-invalid",
                f"preview.nodes[{index}] nodeType {node_type} must not declare variable fields.",
            )
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
        normalized.append(normalized_node)
    normalized_connections = []
    for index, raw in enumerate(connections):
        connection = _object(raw, f"preview.connections[{index}]")
        _reject_unknown(
            connection,
            {
                "from",
                "fromPin",
                "to",
                "toPin",
                "fromExisting",
                "toExisting",
                "fromType",
                "toType",
            },
            f"preview.connections[{index}]",
        )
        has_from_existing = "fromExisting" in connection
        has_to_existing = "toExisting" in connection
        if has_from_existing and has_to_existing:
            raise GraphEditError(
                "graph-edit-preview-invalid",
                f"preview.connections[{index}] has two existing endpoints.",
            )
        from_existing = (
            _existing_pin(
                connection.get("fromExisting"),
                f"preview.connections[{index}].fromExisting",
            )
            if has_from_existing
            else None
        )
        to_existing = (
            _existing_pin(
                connection.get("toExisting"),
                f"preview.connections[{index}].toExisting",
            )
            if has_to_existing
            else None
        )
        if from_existing is not None and from_existing["direction"] != "output":
            raise GraphEditError(
                "graph-edit-preview-invalid",
                f"preview.connections[{index}].fromExisting must be an output pin.",
            )
        if to_existing is not None and to_existing["direction"] != "input":
            raise GraphEditError(
                "graph-edit-preview-invalid",
                f"preview.connections[{index}].toExisting must be an input pin.",
            )
        source_node = (
            None
            if from_existing is not None
            else next(
                (node for node in nodes if node.get("tempId") == connection.get("from")),
                None,
            )
        )
        target_node = (
            None
            if to_existing is not None
            else next(
                (node for node in nodes if node.get("tempId") == connection.get("to")),
                None,
            )
        )
        source_contract = (
            _node_contract(source_node) if isinstance(source_node, dict) else None
        )
        target_contract = (
            _node_contract(target_node) if isinstance(target_node, dict) else None
        )
        expected_from_type = (
            _existing_pin_type_token(
                from_existing["category"],
                from_existing["subCategory"],
            )
            if from_existing is not None
            else (
                source_contract.get("outputPinTypes", {}).get(connection.get("fromPin"))
                if source_contract is not None
                else None
            )
        )
        expected_to_type = (
            _existing_pin_type_token(
                to_existing["category"],
                to_existing["subCategory"],
            )
            if to_existing is not None
            else (
                target_contract.get("inputPinTypes", {}).get(connection.get("toPin"))
                if target_contract is not None
                else None
            )
        )
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
        normalized_connection: dict[str, Any] = {}
        if from_existing is not None:
            normalized_connection["fromExisting"] = from_existing
        else:
            normalized_connection["from"] = connection.get("from")
            normalized_connection["fromPin"] = connection.get("fromPin")
        if to_existing is not None:
            normalized_connection["toExisting"] = to_existing
        else:
            normalized_connection["to"] = connection.get("to")
            normalized_connection["toPin"] = connection.get("toPin")
        normalized_connections.append(normalized_connection)
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


def save_confirmation_for_plan(plan_id: str) -> str:
    return SAVE_CONFIRMATION_PREFIX + plan_id


def validate_save_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    plan_id: Any,
    expected_graph_fingerprint: Any,
    expected_node_count: Any,
    expected_link_count: Any,
    confirmation: Any,
) -> dict[str, Any]:
    """Validate the exact post-Apply Save tuple before calling the Editor."""

    normalized_asset = _string(asset_path, "assetPath", maximum=512)
    normalized_graph = _string(graph_guid, "graphGuid", maximum=64)
    normalized_plan = _string(plan_id, "planId", maximum=80)
    if _SHA256.fullmatch(normalized_plan) is None:
        raise GraphEditError(
            "graph-edit-invalid-parameters",
            "planId must be one canonical sha256 plan hash.",
        )
    normalized_fingerprint = _string(
        expected_graph_fingerprint,
        "expectedGraphFingerprint",
        maximum=45,
    )
    if _SHA1.fullmatch(normalized_fingerprint) is None:
        raise GraphEditError(
            "graph-edit-invalid-parameters",
            "expectedGraphFingerprint must be one canonical sha1 graph fingerprint.",
        )
    for value, label in (
        (expected_node_count, "expectedNodeCount"),
        (expected_link_count, "expectedLinkCount"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise GraphEditError(
                "graph-edit-invalid-parameters",
                f"{label} must be a non-negative integer.",
            )
    expected_confirmation = save_confirmation_for_plan(normalized_plan)
    if not isinstance(confirmation, str) or confirmation != expected_confirmation:
        raise GraphEditError(
            "graph-edit-confirmation-required",
            f"Explicit Save confirmation must be exactly {expected_confirmation}.",
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "planId": normalized_plan,
        "expectedGraphFingerprint": normalized_fingerprint,
        "expectedNodeCount": expected_node_count,
        "expectedLinkCount": expected_link_count,
        "confirmation": confirmation,
    }


def build_save_parameters(apply_result: Any, confirmation: Any) -> dict[str, Any]:
    """Derive the exact Save tuple only from a successful Apply result."""

    item = _object(apply_result, "applyResult")
    if item.get("applied") is not True or item.get("saved") is True:
        raise GraphEditError(
            "graph-edit-save-requires-apply",
            "Save requires the exact successful Apply result before it.",
        )
    return validate_save_parameters(
        asset_path=item.get("assetPath"),
        graph_guid=item.get("graphGuid"),
        plan_id=item.get("planId"),
        expected_graph_fingerprint=item.get("graphFingerprintAfter"),
        expected_node_count=item.get("nodeCountAfter"),
        expected_link_count=item.get("linkCountAfter"),
        confirmation=confirmation,
    )

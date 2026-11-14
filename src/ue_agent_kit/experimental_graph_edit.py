"""Bounded, explicitly opted-in createNode/connectPins edit contract.

This module does not touch Unreal.  It validates the small request/result
envelope used by the live preview/apply tools and binds Apply to the complete
preview result plus the exact confirmation phrase.

Besides the deterministic function/branch nodes, the contract accepts
``variable_get`` / ``variable_set`` nodes that reference one already-existing
top-level bool/int/double/string member variable by concrete GUID + name.  The
variable itself is never created, renamed, retyped or defaulted here.

A connection may bind one exact existing K2 pin via ``fromExisting`` /
``toExisting``.  With ``nodes=[]`` a single existing output may be connected to
one existing unlinked input; existing->existing plans are limited to exactly
that one edge and never break or replace an existing link.
"""

from __future__ import annotations

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


def node_contract(node: dict[str, Any]) -> dict[str, Any] | None:
    """Public view of the exact node/pin contract for one normalized node.

    The atomic patch reuses this so a `variable_get`/`variable_set` result is
    checked against the same variable-dependent pin contract the standalone graph
    edit tool declares, instead of a second copy of it (0-3.4 slice 15).
    """

    return _node_contract(node)


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


def validate_variable_accessor_fields(
    node_type: Any,
    values: dict[str, Any],
    label: str,
) -> dict[str, str]:
    """Validate the three bounded accessor fields with the writer's own rules.

    One implementation for both callers: the standalone graph edit tool (which
    raises ``GraphEditError`` directly) and the atomic patch (which normalizes the
    same code into its own error type).  Nothing here creates or retypes a
    variable -- it only checks that the identity the caller declared is a legal
    accessor binding shape (0-3.4 slice 15).
    """

    if node_type not in VARIABLE_NODE_TYPES:
        raise GraphEditError(
            "graph-edit-node-type-not-allowed",
            f"{label}: {node_type} is not a variable accessor nodeType.",
        )
    variable_name = values.get("variableName")
    variable_guid = values.get("variableGuid")
    variable_type = values.get("variableType")
    if (
        not isinstance(variable_name, str)
        or not variable_name
        or len(variable_name) > 128
        or not variable_name.isidentifier()
        or variable_name in VARIABLE_PIN_RESERVED
    ):
        raise GraphEditError(
            "graph-edit-variable-name-invalid",
            f"{label}.variableName must be a 1-128 character identifier "
            f"other than {', '.join(VARIABLE_PIN_RESERVED)}.",
        )
    if not isinstance(variable_guid, str) or not _VARIABLE_GUID.fullmatch(variable_guid):
        raise GraphEditError(
            "graph-edit-variable-guid-invalid",
            f"{label}.variableGuid must be one canonical lowercase GUID.",
        )
    if variable_type not in VARIABLE_TYPES:
        raise GraphEditError(
            "graph-edit-variable-type-invalid",
            f"{label}.variableType must be bool, int, double or string.",
        )
    return {
        "variableName": variable_name,
        "variableGuid": variable_guid,
        "variableType": variable_type,
    }

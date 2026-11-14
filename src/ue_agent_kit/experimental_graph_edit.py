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

import hashlib
import json
import math
import re
from typing import Any
from .experimental_envelope import unwrap_tool_result

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

# Strict per-function allowlist for K2 CallFunction nodes.  Only engine pure
# static functions with exact primitive exec/bool/int/double/string pins are
# admitted.  The digest binds owner + function + the exact pin schema; a caller
# cannot supply a free-form path or an ad-hoc signature.
CALL_FUNCTION_NODE_TYPE = "call_function"
CALL_FUNCTION_CLASS_PATH = "/Script/BlueprintGraph.K2Node_CallFunction"
CALL_FUNCTION_OWNER = "/Script/Engine.KismetMathLibrary"
CALL_FUNCTION_PIN_TYPES = ("exec", "bool", "int", "double", "string")
OWNED_CALL_NODE_TYPE = "call_owned_pure_function"
OWNED_CALL_PIN_TYPES = ("bool", "int", "double", "string")
MAX_OWNED_CALLS_PER_PLAN = 1
MAX_OWNED_LITERALS_PER_PLAN = 8
MAX_OWNED_STRING_LITERAL = 256
_INT32_MIN = -(2**31)
_INT32_MAX = 2**31 - 1
_DOUBLE_LITERAL = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_INT_LITERAL = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
CALL_FUNCTION_ALLOWLIST: dict[str, dict[str, Any]] = {
    "Greater_IntInt": {
        "ownerClassPath": CALL_FUNCTION_OWNER,
        "pins": [
            {"name": "A", "direction": "input", "type": "int"},
            {"name": "B", "direction": "input", "type": "int"},
            {"name": "ReturnValue", "direction": "output", "type": "bool"},
        ],
    },
    "Less_IntInt": {
        "ownerClassPath": CALL_FUNCTION_OWNER,
        "pins": [
            {"name": "A", "direction": "input", "type": "int"},
            {"name": "B", "direction": "input", "type": "int"},
            {"name": "ReturnValue", "direction": "output", "type": "bool"},
        ],
    },
    "Clamp": {
        "ownerClassPath": CALL_FUNCTION_OWNER,
        "pins": [
            {"name": "Value", "direction": "input", "type": "int"},
            {"name": "Min", "direction": "input", "type": "int"},
            {"name": "Max", "direction": "input", "type": "int"},
            {"name": "ReturnValue", "direction": "output", "type": "int"},
        ],
    },
    "Abs_Int": {
        "ownerClassPath": CALL_FUNCTION_OWNER,
        "pins": [
            {"name": "A", "direction": "input", "type": "int"},
            {"name": "ReturnValue", "direction": "output", "type": "int"},
        ],
    },
}


def call_function_signature_digest(
    owner_class_path: str,
    function_name: str,
    pins: list[dict[str, str]],
) -> str:
    canonical = (
        owner_class_path
        + "|"
        + function_name
        + "|"
        + "|".join(
            f"{pin['name']}:{pin['direction']}:{pin['type']}"
            for pin in sorted(pins, key=lambda item: item["name"])
        )
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def owned_pure_call_signature_digest(
    owning_blueprint_path: str,
    function_graph_guid: str,
    function_name: str,
    pins: list[dict[str, str]],
) -> str:
    canonical = (
        owning_blueprint_path
        + "|"
        + function_graph_guid
        + "|"
        + function_name
        + "|"
        + "|".join(
            f"{pin['name']}:{pin['direction']}:{pin['type']}"
            for pin in sorted(pins, key=lambda item: item["name"])
        )
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


for _function_name, _entry in CALL_FUNCTION_ALLOWLIST.items():
    _entry["signatureDigest"] = call_function_signature_digest(
        _entry["ownerClassPath"],
        _function_name,
        _entry["pins"],
    )



def _node_contract(node: dict[str, Any]) -> dict[str, Any] | None:
    """Return the exact declared node/pin contract for one normalized node."""

    node_type = node.get("nodeType")
    if node_type == OWNED_CALL_NODE_TYPE:
        owned_call = node.get("ownedCall")
        if not isinstance(owned_call, dict):
            return None
        pins = owned_call.get("pins")
        if not isinstance(pins, list):
            return None
        input_types: dict[str, str] = {}
        output_types: dict[str, str] = {}
        for pin in pins:
            if not isinstance(pin, dict):
                return None
            name = pin.get("name")
            direction = pin.get("direction")
            pin_type = pin.get("type")
            if not isinstance(name, str) or direction not in ("input", "output"):
                return None
            if pin_type not in OWNED_CALL_PIN_TYPES:
                return None
            target = input_types if direction == "input" else output_types
            if name in target:
                return None
            target[name] = pin_type
        return {
            "nodeClass": CALL_FUNCTION_CLASS_PATH,
            "inputPins": tuple(input_types),
            "outputPins": tuple(output_types),
            "inputPinTypes": input_types,
            "outputPinTypes": output_types,
        }
    if node_type == CALL_FUNCTION_NODE_TYPE:
        call_function = node.get("callFunction")
        if not isinstance(call_function, dict):
            return None
        pins = call_function.get("pins")
        if not isinstance(pins, list):
            return None
        input_types: dict[str, str] = {}
        output_types: dict[str, str] = {}
        for pin in pins:
            if not isinstance(pin, dict):
                return None
            name = pin.get("name")
            direction = pin.get("direction")
            pin_type = pin.get("type")
            if not isinstance(name, str) or direction not in ("input", "output"):
                return None
            if pin_type not in CALL_FUNCTION_PIN_TYPES:
                return None
            target = input_types if direction == "input" else output_types
            if name in target:
                return None
            target[name] = pin_type
        return {
            "nodeClass": CALL_FUNCTION_CLASS_PATH,
            "inputPins": tuple(input_types),
            "outputPins": tuple(output_types),
            "inputPinTypes": input_types,
            "outputPinTypes": output_types,
        }
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
_OWNED_FUNCTION_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
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


def _normalize_call_function(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GraphEditError(
            "graph-edit-call-function-invalid",
            f"{label} must be an exact callFunction object.",
        )
    call_function = _object(value, label)
    _reject_unknown(
        call_function,
        {"ownerClassPath", "functionName", "signatureDigest", "pins"},
        label,
    )
    owner = _string(call_function.get("ownerClassPath"), f"{label}.ownerClassPath", maximum=256)
    function_name = _string(
        call_function.get("functionName"),
        f"{label}.functionName",
        maximum=128,
    )
    entry = CALL_FUNCTION_ALLOWLIST.get(function_name)
    if entry is None or owner != entry["ownerClassPath"]:
        raise GraphEditError(
            "graph-edit-call-function-not-allowlisted",
            f"{label} is not one of the fixed engine pure static allowlist entries.",
        )
    raw_pins = call_function.get("pins")
    if not isinstance(raw_pins, list) or not 1 <= len(raw_pins) <= 16:
        raise GraphEditError(
            "graph-edit-call-function-pin-mismatch",
            f"{label}.pins must contain the exact reflected pin schema.",
        )
    pins: list[dict[str, str]] = []
    for index, raw_pin in enumerate(raw_pins):
        pin = _object(raw_pin, f"{label}.pins[{index}]")
        _reject_unknown(
            pin,
            {"name", "direction", "type"},
            f"{label}.pins[{index}]",
        )
        name = _string(pin.get("name"), f"{label}.pins[{index}].name", maximum=64)
        direction = pin.get("direction")
        pin_type = pin.get("type")
        if direction not in ("input", "output") or pin_type not in CALL_FUNCTION_PIN_TYPES:
            raise GraphEditError(
                "graph-edit-call-function-pin-mismatch",
                f"{label}.pins[{index}] must be an exact input/output primitive pin.",
            )
        pins.append({"name": name, "direction": direction, "type": pin_type})
    expected_pins = sorted(
        entry["pins"],
        key=lambda item: (item["name"], item["direction"]),
    )
    actual_pins = sorted(pins, key=lambda item: (item["name"], item["direction"]))
    if actual_pins != expected_pins:
        raise GraphEditError(
            "graph-edit-call-function-pin-mismatch",
            f"{label}.pins does not match the reflected allowlist pins.",
        )
    digest = call_function_signature_digest(owner, function_name, pins)
    declared_digest = call_function.get("signatureDigest")
    if (
        not isinstance(declared_digest, str)
        or not _SHA256.fullmatch(declared_digest)
        or declared_digest != entry["signatureDigest"]
        or digest != entry["signatureDigest"]
    ):
        raise GraphEditError(
            "graph-edit-call-function-signature-mismatch",
            f"{label}.signatureDigest does not match the reflected allowlist signature.",
        )
    return {
        "ownerClassPath": owner,
        "functionName": function_name,
        "signatureDigest": digest,
        "pins": pins,
    }


def _normalize_owned_call(value: Any, label: str, owning_asset_path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise GraphEditError(
            "graph-edit-owned-call-invalid",
            f"{label} must be an exact ownedCall object.",
        )
    owned_call = _object(value, label)
    _reject_unknown(
        owned_call,
        {
            "owningBlueprintPath",
            "functionGraphGuid",
            "functionName",
            "signatureDigest",
            "pins",
        },
        label,
    )
    owner = _string(
        owned_call.get("owningBlueprintPath"),
        f"{label}.owningBlueprintPath",
        maximum=512,
    )
    if owner != owning_asset_path:
        raise GraphEditError(
            "graph-edit-owned-call-owner-mismatch",
            f"{label}.owningBlueprintPath must equal the focused assetPath.",
        )
    graph_guid = _string(
        owned_call.get("functionGraphGuid"),
        f"{label}.functionGraphGuid",
        maximum=36,
    )
    if not _VARIABLE_GUID.fullmatch(graph_guid):
        raise GraphEditError(
            "graph-edit-owned-call-invalid",
            f"{label}.functionGraphGuid must be one canonical GUID.",
        )
    function_name = _string(
        owned_call.get("functionName"),
        f"{label}.functionName",
        maximum=128,
    )
    if not _OWNED_FUNCTION_NAME.fullmatch(function_name):
        raise GraphEditError(
            "graph-edit-owned-call-invalid",
            f"{label}.functionName must be one identifier.",
        )
    raw_pins = owned_call.get("pins")
    if not isinstance(raw_pins, list) or not 1 <= len(raw_pins) <= 8:
        raise GraphEditError(
            "graph-edit-owned-call-pin-mismatch",
            f"{label}.pins must contain the exact primitive reflected pins.",
        )
    pins: list[dict[str, str]] = []
    seen_names: set[str] = set()
    output_count = 0
    for index, raw_pin in enumerate(raw_pins):
        pin = _object(raw_pin, f"{label}.pins[{index}]")
        _reject_unknown(
            pin,
            {"name", "direction", "type"},
            f"{label}.pins[{index}]",
        )
        name = _string(pin.get("name"), f"{label}.pins[{index}].name", maximum=64)
        direction = pin.get("direction")
        pin_type = pin.get("type")
        if (
            not _OWNED_FUNCTION_NAME.fullmatch(name)
            or direction not in ("input", "output")
            or pin_type not in OWNED_CALL_PIN_TYPES
            or name in seen_names
        ):
            raise GraphEditError(
                "graph-edit-owned-call-pin-mismatch",
                f"{label}.pins[{index}] must be a unique primitive input/output pin.",
            )
        seen_names.add(name)
        if direction == "output":
            output_count += 1
        pins.append({"name": name, "direction": direction, "type": pin_type})
    if output_count == 0:
        raise GraphEditError(
            "graph-edit-owned-call-pin-mismatch",
            f"{label} must expose at least one primitive output pin.",
        )
    digest = owned_pure_call_signature_digest(
        owner,
        graph_guid,
        function_name,
        pins,
    )
    declared_digest = owned_call.get("signatureDigest")
    if (
        not isinstance(declared_digest, str)
        or not _SHA256.fullmatch(declared_digest)
        or declared_digest != digest
    ):
        raise GraphEditError(
            "graph-edit-owned-call-signature-mismatch",
            f"{label}.signatureDigest does not match its exact owned function schema.",
        )
    return {
        "owningBlueprintPath": owner,
        "functionGraphGuid": graph_guid,
        "functionName": function_name,
        "signatureDigest": digest,
        "pins": pins,
    }


def _canonical_owned_literal(
    pin_type: str,
    value: Any,
    label: str,
) -> str:
    if not isinstance(value, str):
        raise GraphEditError(
            "graph-edit-owned-literal-value-invalid",
            f"{label}.value must be a canonical string.",
        )
    if pin_type == "bool":
        if value not in ("true", "false"):
            raise GraphEditError(
                "graph-edit-owned-literal-value-invalid",
                f"{label}.value must be exactly 'true' or 'false'.",
            )
        return value
    if pin_type == "int":
        if (
            not _INT_LITERAL.fullmatch(value)
            or value == "-0"
            or not _INT32_MIN <= int(value) <= _INT32_MAX
        ):
            raise GraphEditError(
                "graph-edit-owned-literal-value-invalid",
                f"{label}.value must be canonical signed int32 decimal.",
            )
        return value
    if pin_type == "double":
        if (
            not _DOUBLE_LITERAL.fullmatch(value)
            or value in ("-0", "-0.0")
            or value.endswith(".")
        ):
            raise GraphEditError(
                "graph-edit-owned-literal-value-invalid",
                f"{label}.value must be canonical finite decimal.",
            )
        try:
            finite = math.isfinite(float(value))
        except ValueError:
            finite = False
        if not finite:
            raise GraphEditError(
                "graph-edit-owned-literal-value-invalid",
                f"{label}.value must be finite.",
            )
        return value
    if pin_type == "string":
        if len(value) > MAX_OWNED_STRING_LITERAL or any(
            ord(character) < 0x20 or ord(character) == 0x7F for character in value
        ):
            raise GraphEditError(
                "graph-edit-owned-literal-value-invalid",
                f"{label}.value must be at most {MAX_OWNED_STRING_LITERAL} printable characters.",
            )
        return value
    raise GraphEditError(
        "graph-edit-owned-literal-type-not-allowed",
        f"{label}.type is not bool/int/double/string.",
    )


def _normalize_input_literals(
    value: Any,
    owned_call: dict[str, Any],
    label: str,
) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_OWNED_LITERALS_PER_PLAN:
        raise GraphEditError(
            "graph-edit-owned-literal-limit",
            f"{label} must contain at most {MAX_OWNED_LITERALS_PER_PLAN} literals.",
        )
    input_types = {
        pin["name"]: pin["type"]
        for pin in owned_call["pins"]
        if pin["direction"] == "input"
    }
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        literal = _object(raw, f"{label}[{index}]")
        _reject_unknown(
            literal,
            {"pin", "type", "value"},
            f"{label}[{index}]",
        )
        pin = _string(literal.get("pin"), f"{label}[{index}].pin", maximum=64)
        pin_type = literal.get("type")
        if (
            pin not in input_types
            or pin_type != input_types[pin]
            or pin in seen
        ):
            raise GraphEditError(
                "graph-edit-owned-literal-pin-mismatch",
                f"{label}[{index}] must target one exact declared input pin once.",
            )
        seen.add(pin)
        normalized.append(
            {
                "pin": pin,
                "type": pin_type,
                "value": _canonical_owned_literal(
                    pin_type,
                    literal.get("value"),
                    f"{label}[{index}]",
                ),
            }
        )
    return normalized

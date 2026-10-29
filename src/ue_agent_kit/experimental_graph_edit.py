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


def validate_preview_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    nodes: Any,
    connections: Any,
    operation: Any = "connect",
) -> dict[str, Any]:
    """Validate and normalize one bounded preview request."""

    normalized_asset = _string(asset_path, "assetPath", maximum=512)
    normalized_graph = _string(graph_guid, "graphGuid", maximum=64)
    normalized_operation = _string(operation, "operation", maximum=16)
    if normalized_operation not in ("connect", "disconnect"):
        raise GraphEditError(
            "graph-edit-invalid-operation",
            "operation must be connect or disconnect.",
        )
    if not isinstance(nodes, list) or len(nodes) > MAX_NODES:
        raise GraphEditError(
            "graph-edit-invalid-nodes",
            f"nodes must contain between 0 and {MAX_NODES} objects.",
        )
    if not isinstance(connections, list) or len(connections) > MAX_CONNECTIONS:
        raise GraphEditError(
            "graph-edit-invalid-connections",
            f"connections must contain at most {MAX_CONNECTIONS} objects.",
        )
    if normalized_operation == "disconnect":
        if len(nodes) != 0 or len(connections) != 1:
            raise GraphEditError(
                "graph-edit-disconnect-invalid-plan",
                "A disconnect plan requires nodes=[] and exactly one existing-to-existing edge.",
            )
        first_connection = _object(connections[0], "connections[0]")
        if "fromExisting" not in first_connection or "toExisting" not in first_connection:
            raise GraphEditError(
                "graph-edit-disconnect-invalid-plan",
                "A disconnect connection requires both fromExisting and toExisting.",
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
                "callFunction",
                "ownedCall",
                "inputLiterals",
            },
            f"nodes[{index}]",
        )
        temp_id = _string(item.get("tempId"), f"nodes[{index}].tempId", maximum=40)
        node_type = _string(item.get("nodeType"), f"nodes[{index}].nodeType", maximum=32)
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
        if (
            node_type not in NODE_TYPES
            and node_type not in VARIABLE_NODE_TYPES
            and node_type != CALL_FUNCTION_NODE_TYPE
            and node_type != OWNED_CALL_NODE_TYPE
        ):
            raise GraphEditError(
                "graph-edit-node-type-not-allowed",
                "Supported nodeTypes: "
                + ", ".join(
                    sorted(
                        (
                            *NODE_TYPES,
                            *VARIABLE_NODE_TYPES,
                            CALL_FUNCTION_NODE_TYPE,
                            OWNED_CALL_NODE_TYPE,
                        )
                    )
                )
                + ".",
            )
        normalized_node: dict[str, Any] = {
            "tempId": temp_id,
            "nodeType": node_type,
            "x": _integer(item.get("x"), f"nodes[{index}].x"),
            "y": _integer(item.get("y"), f"nodes[{index}].y"),
        }
        if node_type in VARIABLE_NODE_TYPES:
            if any(key in item for key in ("callFunction", "ownedCall", "inputLiterals")):
                raise GraphEditError(
                    "graph-edit-variable-field-not-allowed",
                    f"nodes[{index}] variable accessors do not accept call/literal fields.",
                )
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
        elif node_type == CALL_FUNCTION_NODE_TYPE:
            if any(
                key in item for key in ("variableName", "variableGuid", "variableType")
            ):
                raise GraphEditError(
                    "graph-edit-variable-field-not-allowed",
                    f"nodes[{index}] call_function does not accept variable fields.",
                )
            if "ownedCall" in item:
                raise GraphEditError(
                    "graph-edit-owned-call-field-not-allowed",
                    f"nodes[{index}] call_function does not accept ownedCall.",
                )
            if "inputLiterals" in item:
                raise GraphEditError(
                    "graph-edit-owned-literal-field-not-allowed",
                    f"nodes[{index}] call_function does not accept inputLiterals.",
                )
            normalized_node["callFunction"] = _normalize_call_function(
                item.get("callFunction"),
                f"nodes[{index}].callFunction",
            )
        elif node_type == OWNED_CALL_NODE_TYPE:
            if any(
                key in item for key in ("variableName", "variableGuid", "variableType")
            ):
                raise GraphEditError(
                    "graph-edit-variable-field-not-allowed",
                    f"nodes[{index}] call_owned_pure_function does not accept variable fields.",
                )
            if "callFunction" in item:
                raise GraphEditError(
                    "graph-edit-call-function-field-not-allowed",
                    f"nodes[{index}] call_owned_pure_function does not accept callFunction.",
                )
            normalized_node["ownedCall"] = _normalize_owned_call(
                item.get("ownedCall"),
                f"nodes[{index}].ownedCall",
                normalized_asset,
            )
            normalized_node["inputLiterals"] = _normalize_input_literals(
                item.get("inputLiterals"),
                normalized_node["ownedCall"],
                f"nodes[{index}].inputLiterals",
            )
        elif any(
            key in item for key in ("variableName", "variableGuid", "variableType")
        ):
            raise GraphEditError(
                "graph-edit-variable-field-not-allowed",
                f"nodes[{index}] nodeType {node_type} does not accept variable fields.",
            )
        elif "callFunction" in item:
            raise GraphEditError(
                "graph-edit-call-function-field-not-allowed",
                f"nodes[{index}] nodeType {node_type} does not accept callFunction.",
            )
        elif "ownedCall" in item:
            raise GraphEditError(
                "graph-edit-owned-call-field-not-allowed",
                f"nodes[{index}] nodeType {node_type} does not accept ownedCall.",
            )
        elif "inputLiterals" in item:
            raise GraphEditError(
                "graph-edit-owned-literal-field-not-allowed",
                f"nodes[{index}] nodeType {node_type} does not accept inputLiterals.",
            )
        normalized_nodes.append(normalized_node)

    owned_call_count = sum(
        1 for node in normalized_nodes if node.get("nodeType") == OWNED_CALL_NODE_TYPE
    )
    if owned_call_count > MAX_OWNED_CALLS_PER_PLAN:
        raise GraphEditError(
            "graph-edit-owned-call-limit",
            "A plan may create at most one call_owned_pure_function node.",
        )
    if owned_call_count and len(normalized_nodes) != 1:
        raise GraphEditError(
            "graph-edit-owned-call-plan",
            "A call_owned_pure_function plan must contain exactly that one new node.",
        )

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

        from_existing: dict[str, str] | None = None
        to_existing: dict[str, str] | None = None
        source: str | None = None
        source_pin: str | None = None
        target: str | None = None
        target_pin: str | None = None

        if has_from_existing and has_to_existing:
            if any(key in item for key in ("from", "fromPin", "to", "toPin")):
                raise GraphEditError(
                    "graph-edit-existing-endpoint-conflict",
                    "A pure existing-to-existing connection cannot carry temp-id endpoints.",
                )
            from_existing = _existing_pin(
                item.get("fromExisting"),
                f"connections[{index}].fromExisting",
            )
            to_existing = _existing_pin(
                item.get("toExisting"),
                f"connections[{index}].toExisting",
            )
            if from_existing["direction"] != "output" or to_existing["direction"] != "input":
                raise GraphEditError(
                    "graph-edit-existing-pin-direction",
                    "Pure existing connections require fromExisting=output and toExisting=input.",
                )
            if from_existing["nodeGuid"] == to_existing["nodeGuid"]:
                raise GraphEditError(
                    "graph-edit-self-connection",
                    "The two existing endpoints must belong to different nodes.",
                )
            source_type = _existing_pin_type_token(
                from_existing["category"],
                from_existing["subCategory"],
            )
            target_type = _existing_pin_type_token(
                to_existing["category"],
                to_existing["subCategory"],
            )
            if source_type != target_type:
                raise GraphEditError(
                    "graph-edit-pin-type-mismatch",
                    f"Cannot connect existing {from_existing['pinName']} ({source_type}) to "
                    f"existing {to_existing['pinName']} ({target_type}).",
                )
            target_key = f"existing:{to_existing['nodeGuid']}|{to_existing['pinGuid']}"
            if target_key in seen_targets:
                raise GraphEditError(
                    "graph-edit-connection-duplicate",
                    f"Input pin already has a connection in this plan: {target_key}.",
                )
            seen_targets.add(target_key)
            normalized_connections.append(
                {"fromExisting": from_existing, "toExisting": to_existing}
            )
            continue

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

    if owned_call_count:
        owned_node = next(
            node for node in normalized_nodes
            if node.get("nodeType") == OWNED_CALL_NODE_TYPE
        )
        literal_pins = {
            literal["pin"] for literal in owned_node.get("inputLiterals") or []
        }
        for connection in normalized_connections:
            if (
                connection.get("to") == owned_node["tempId"]
                and connection.get("toPin") in literal_pins
            ):
                raise GraphEditError(
                    "graph-edit-owned-literal-connected",
                    f"Input pin {connection.get('toPin')} has a literal and a connection.",
                )

    pure_existing_count = sum(
        1
        for connection in normalized_connections
        if "fromExisting" in connection and "toExisting" in connection
    )
    if not normalized_nodes and not normalized_connections:
        raise GraphEditError(
            "graph-edit-empty-plan",
            "A graph edit plan must create at least one node or connect at least one existing edge.",
        )
    if normalized_operation == "disconnect":
        if (
            pure_existing_count != 1
            or normalized_nodes
            or len(normalized_connections) != 1
        ):
            raise GraphEditError(
                "graph-edit-disconnect-invalid-plan",
                "A disconnect plan requires nodes=[] and exactly one existing-to-existing edge.",
            )
    else:
        if pure_existing_count > 1:
            raise GraphEditError(
                "graph-edit-existing-to-existing-limit",
                "A plan may add at most one existing-to-existing connection.",
            )
        if pure_existing_count == 1 and (
            normalized_nodes or len(normalized_connections) != 1
        ):
            raise GraphEditError(
                "graph-edit-existing-to-existing-plan",
                "A pure existing-to-existing connection must be the only connection and requires nodes=[].",
            )
        if not normalized_nodes and pure_existing_count == 0:
            raise GraphEditError(
                "graph-edit-nodes-required",
                "nodes may be empty only for one pure existing-to-existing connection.",
            )

    request = {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "operation": normalized_operation,
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
    operation = item.get("operation", "connect")
    if operation not in ("connect", "disconnect"):
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.operation must be connect or disconnect.",
        )
    if operation == "disconnect" and item.get("disconnectCount") != 1:
        raise GraphEditError(
            "graph-edit-preview-invalid",
            "preview.disconnectCount must be 1 for a disconnect preview.",
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
        if node_type == OWNED_CALL_NODE_TYPE:
            if contract is None:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] has an invalid owned pure call declaration.",
                )
            if any(
                key in node for key in ("variableName", "variableGuid", "variableType")
            ):
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] call_owned_pure_function must not declare variable fields.",
                )
            if "callFunction" in node:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] call_owned_pure_function must not declare callFunction.",
                )
            normalized_node["ownedCall"] = _normalize_owned_call(
                node.get("ownedCall"),
                f"preview.nodes[{index}].ownedCall",
                asset_path,
            )
            normalized_node["inputLiterals"] = _normalize_input_literals(
                node.get("inputLiterals"),
                normalized_node["ownedCall"],
                f"preview.nodes[{index}].inputLiterals",
            )
        elif node_type == CALL_FUNCTION_NODE_TYPE:
            if contract is None:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] has an invalid call function declaration.",
                )
            if any(
                key in node for key in ("variableName", "variableGuid", "variableType")
            ):
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] call_function must not declare variable fields.",
                )
            if "ownedCall" in node:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] call_function must not declare ownedCall.",
                )
            if "inputLiterals" in node:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] call_function must not declare inputLiterals.",
                )
            normalized_node["callFunction"] = _normalize_call_function(
                node.get("callFunction"),
                f"preview.nodes[{index}].callFunction",
            )
        elif node_type in VARIABLE_NODE_TYPES:
            if contract is None:
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] has an invalid variable accessor declaration.",
                )
            if any(
                key in node for key in ("callFunction", "ownedCall", "inputLiterals")
            ):
                raise GraphEditError(
                    "graph-edit-preview-invalid",
                    f"preview.nodes[{index}] variable accessor has call/literal fields.",
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
            key in node
            for key in (
                "variableName",
                "variableGuid",
                "variableType",
                "callFunction",
                "ownedCall",
                "inputLiterals",
            )
        ):
            raise GraphEditError(
                "graph-edit-preview-invalid",
                f"preview.nodes[{index}] nodeType {node_type} has fields it does not accept.",
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
        if (
            from_existing is not None
            and to_existing is not None
            and from_existing["nodeGuid"] == to_existing["nodeGuid"]
        ):
            raise GraphEditError(
                "graph-edit-preview-invalid",
                f"preview.connections[{index}] has two endpoints on one node.",
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
        operation=operation,
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
        "operation": normalized["operation"],
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

"""Pin-default canonicalisation shared by the atomic Blueprint patch.

Only the strict bool/int/double/string encodings and the bounded preview
parameter shape live here; the single-writer preview/apply/save pipeline this
module used to describe was removed in slice 16 because the atomic patch covers
that kind end to end.
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_DEFAULTS = 16
MAX_PIN_NAME = 128
MAX_STRING_VALUE = 1024

DEFAULT_TYPES = ("bool", "int", "double", "string")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_INT = re.compile(r"^-?(0|[1-9][0-9]*)$")
_DOUBLE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


class PinDefaultError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PinDefaultError(
            "graph-pin-default-invalid-parameters",
            f"{label} must be an object.",
        )
    return value


def _string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        requirement = f"at most {maximum} characters"
        if not allow_empty:
            requirement = "non-empty " + requirement
        raise PinDefaultError(
            "graph-pin-default-invalid-parameters",
            f"{label} must be a string of {requirement}.",
        )
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise PinDefaultError(
            "graph-pin-default-unknown-parameter",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )


def _guid(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=36)
    if _GUID.fullmatch(normalized) is None:
        raise PinDefaultError(
            "graph-pin-default-invalid-identifier",
            f"{label} must be one canonical lower-case GUID.",
        )
    return normalized


def _pin_name(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=MAX_PIN_NAME)
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in normalized):
        raise PinDefaultError(
            "graph-pin-default-invalid-parameters",
            f"{label} must not contain control characters.",
        )
    return normalized


def canonical_value(pin_type: Any, value: Any) -> tuple[str, str]:
    """Return (strict_type, canonical_value_string)."""

    normalized_type = _string(pin_type, "defaults[].type", maximum=16)
    if normalized_type not in DEFAULT_TYPES:
        raise PinDefaultError(
            "graph-pin-default-type-not-allowed",
            "Supported pin default types: bool, int, double, string.",
        )
    if normalized_type == "string":
        if not isinstance(value, str):
            raise PinDefaultError(
                "graph-pin-default-invalid-parameters",
                "defaults[].value must be a string.",
            )
        if len(value) > MAX_STRING_VALUE:
            raise PinDefaultError(
                "graph-pin-default-value-invalid",
                f"String default must be at most {MAX_STRING_VALUE} characters.",
            )
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise PinDefaultError(
                "graph-pin-default-value-invalid",
                "String default must not contain control characters.",
            )
        return normalized_type, value

    raw = _string(
        value,
        "defaults[].value",
        maximum=MAX_STRING_VALUE,
    )

    if normalized_type == "bool":
        if raw not in ("true", "false"):
            raise PinDefaultError(
                "graph-pin-default-value-invalid",
                "Boolean default must be exactly 'true' or 'false'.",
            )
        return normalized_type, raw

    if normalized_type == "int":
        if _INT.fullmatch(raw) is None or raw == "-0":
            raise PinDefaultError(
                "graph-pin-default-value-invalid",
                "Integer default must be canonical decimal without a leading plus or zero.",
            )
        number = int(raw)
        if number < -(2**31) or number > 2**31 - 1:
            raise PinDefaultError(
                "graph-pin-default-value-invalid",
                "Integer default must fit in signed 32-bit range.",
            )
        return normalized_type, raw

    if _DOUBLE.fullmatch(raw) is None or raw == "-0":
        raise PinDefaultError(
            "graph-pin-default-value-invalid",
            "Double default must be canonical finite decimal without exponent or leading plus.",
        )
    number = float(raw)
    if not math.isfinite(number):
        raise PinDefaultError(
            "graph-pin-default-value-invalid",
            "Double default must be finite.",
        )
    return normalized_type, raw


def validate_preview_parameters(
    *,
    asset_path: Any,
    graph_guid: Any,
    defaults: Any,
) -> dict[str, Any]:
    """Validate one bounded pin-default preview request."""

    normalized_asset = _string(asset_path, "assetPath", maximum=512)
    normalized_graph = _guid(graph_guid, "graphGuid")
    if not isinstance(defaults, list) or not 1 <= len(defaults) <= MAX_DEFAULTS:
        raise PinDefaultError(
            "graph-pin-default-invalid-defaults",
            f"defaults must contain between 1 and {MAX_DEFAULTS} objects.",
        )
    normalized_defaults: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(defaults):
        item = _object(raw, f"defaults[{index}]")
        _reject_unknown(
            item,
            {"nodeGuid", "pinName", "pinGuid", "type", "value"},
            f"defaults[{index}]",
        )
        node_guid = _guid(item.get("nodeGuid"), f"defaults[{index}].nodeGuid")
        pin_name = _pin_name(item.get("pinName"), f"defaults[{index}].pinName")
        pin_guid = _guid(item.get("pinGuid"), f"defaults[{index}].pinGuid")
        key = (node_guid, pin_name, pin_guid)
        if key in seen:
            raise PinDefaultError(
                "graph-pin-default-duplicate",
                f"Duplicate pin default target: {node_guid}.{pin_name} ({pin_guid}).",
            )
        seen.add(key)
        pin_type, value = canonical_value(item.get("type"), item.get("value"))
        normalized_defaults.append(
            {
                "nodeGuid": node_guid,
                "pinName": pin_name,
                "pinGuid": pin_guid,
                "type": pin_type,
                "value": value,
            }
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "defaults": normalized_defaults,
    }

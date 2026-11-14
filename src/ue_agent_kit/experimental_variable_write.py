"""Blueprint variable canonicalisation shared by the atomic Blueprint patch.

Only the strict bool/int/double/string encodings and the name/value
canonicalisation live here; the single-writer preview/apply/save pipeline this
module used to describe was removed in slice 16 because the atomic patch covers
that kind end to end.
"""

from __future__ import annotations

import math
import re
from typing import Any

MAX_VARIABLES = 8
MAX_VARIABLE_NAME = 64
MAX_STRING_VALUE = 1024

VARIABLE_TYPES = ("bool", "int", "double", "string")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_INT = re.compile(r"^-?(0|[1-9][0-9]*)$")
_DOUBLE = re.compile(r"^-?(0|[1-9][0-9]*)(\.[0-9]+)?$")


class VariableWriteError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VariableWriteError(
            "blueprint-variable-invalid-parameters",
            f"{label} must be an object.",
        )
    return value


def _string(value: Any, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > maximum or (not allow_empty and not value):
        requirement = f"at most {maximum} characters"
        if not allow_empty:
            requirement = "non-empty " + requirement
        raise VariableWriteError(
            "blueprint-variable-invalid-parameters",
            f"{label} must be a string of {requirement}.",
        )
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise VariableWriteError(
            "blueprint-variable-unknown-parameter",
            f"{label} contains unknown fields: {', '.join(unknown)}.",
        )


def canonical_variable_name(name: Any) -> str:
    normalized = _string(name, "variables[].name", maximum=MAX_VARIABLE_NAME)
    if _NAME.fullmatch(normalized) is None:
        raise VariableWriteError(
            "blueprint-variable-name-invalid",
            "variables[].name must be 1-64 ASCII letters, digits or '_' and start with a letter or '_'.",
        )
    return normalized


def canonical_variable_value(variable_type: Any, value: Any) -> tuple[str, str]:
    """Return (strict_type, canonical_default_value_string)."""

    normalized_type = _string(variable_type, "variables[].type", maximum=16)
    if normalized_type not in VARIABLE_TYPES:
        raise VariableWriteError(
            "blueprint-variable-type-not-allowed",
            "Supported variable types: bool, int, double, string.",
        )
    if normalized_type == "string":
        if not isinstance(value, str):
            raise VariableWriteError(
                "blueprint-variable-invalid-parameters",
                "variables[].defaultValue must be a string.",
            )
        if len(value) > MAX_STRING_VALUE:
            raise VariableWriteError(
                "blueprint-variable-value-invalid",
                f"String default must be at most {MAX_STRING_VALUE} characters.",
            )
        if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
            raise VariableWriteError(
                "blueprint-variable-value-invalid",
                "String default must not contain control characters.",
            )
        return normalized_type, value

    raw = _string(value, "variables[].defaultValue", maximum=MAX_STRING_VALUE)
    if normalized_type == "bool":
        if raw not in ("true", "false"):
            raise VariableWriteError(
                "blueprint-variable-value-invalid",
                "Boolean default must be exactly 'true' or 'false'.",
            )
        return normalized_type, raw
    if normalized_type == "int":
        if _INT.fullmatch(raw) is None or raw == "-0":
            raise VariableWriteError(
                "blueprint-variable-value-invalid",
                "Integer default must be canonical decimal without a leading plus or zero.",
            )
        number = int(raw)
        if number < -(2**31) or number > 2**31 - 1:
            raise VariableWriteError(
                "blueprint-variable-value-invalid",
                "Integer default must fit in signed 32-bit range.",
            )
        return normalized_type, raw
    if _DOUBLE.fullmatch(raw) is None or raw in ("-0", "-0.0"):
        raise VariableWriteError(
            "blueprint-variable-value-invalid",
            "Double default must be canonical finite decimal without exponent or negative zero.",
        )
    number = float(raw)
    if not math.isfinite(number):
        raise VariableWriteError(
            "blueprint-variable-value-invalid",
            "Double default must be finite.",
        )
    return normalized_type, raw

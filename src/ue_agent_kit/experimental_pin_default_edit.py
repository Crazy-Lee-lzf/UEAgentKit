"""Bounded, opt-in Blueprint pin-default edit contract.

This is a separate P1 vertical from node creation.  It edits only existing,
unlinked, editable input pins in the exact focused graph, and only for the
strict bool/int/double/string encodings below.  It never touches Unreal; the
live module validates the same envelope against the UE 5.6 K2 schema.
"""

from __future__ import annotations

import math
import re
from typing import Any

PREVIEW_TOOL_NAME = "ue_preview_blueprint_pin_defaults_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_pin_defaults_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_pin_defaults_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintPinDefaultsExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintPinDefaultsExperimental"
SAVE_CAPABILITY = "editor.saveBlueprintPinDefaultsExperimental"
PREVIEW_METHOD = "editor.previewBlueprintPinDefaultsExperimental"
APPLY_METHOD = "editor.applyBlueprintPinDefaultsExperimental"
SAVE_METHOD = "editor.saveBlueprintPinDefaultsExperimental"
APPLY_CONFIRMATION_PREFIX = "SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT PIN DEFAULTS "

PREVIEW_SCHEMA_VERSION = "ueak.blueprint-pin-default-preview/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_DEFAULTS = 16
MAX_PIN_NAME = 128
MAX_STRING_VALUE = 1024

DEFAULT_TYPES = ("bool", "int", "double", "string")
_GUID = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_SHA1 = re.compile(r"^sha1:[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
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


def validate_preview_result(preview: Any) -> dict[str, Any]:
    item = _object(preview, "preview")
    if (
        item.get("schemaVersion") != PREVIEW_SCHEMA_VERSION
        or item.get("experimental") is not True
        or item.get("readOnly") is not True
        or item.get("wroteBlueprint") is not False
        or item.get("savedPackage") is not False
        or item.get("compiled") is not False
        or not isinstance(item.get("planId"), str)
        or _SHA256.fullmatch(item.get("planId")) is None
        or not isinstance(item.get("defaultsFingerprint"), str)
        or _SHA256.fullmatch(item.get("defaultsFingerprint")) is None
        or not isinstance(item.get("graphFingerprint"), str)
        or _SHA1.fullmatch(item.get("graphFingerprint")) is None
        or not isinstance(item.get("packageRevision"), str)
        or _SHA256.fullmatch(item.get("packageRevision")) is None
    ):
        raise PinDefaultError(
            "graph-pin-default-preview-invalid",
            "Preview envelope, planId, fingerprints or revision are invalid.",
        )
    defaults = item.get("defaults")
    if not isinstance(defaults, list) or not 1 <= len(defaults) <= MAX_DEFAULTS:
        raise PinDefaultError(
            "graph-pin-default-preview-invalid",
            "Preview defaults are missing or exceed the plan bound.",
        )
    normalized_asset = _string(item.get("assetPath"), "preview.assetPath", maximum=512)
    normalized_graph = _guid(item.get("graphGuid"), "preview.graphGuid")
    normalized_defaults: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(defaults):
        entry = _object(raw, f"preview.defaults[{index}]")
        normalized_type, after = canonical_value(entry.get("type"), entry.get("afterValue"))
        before = _string(
            entry.get("beforeValue"),
            f"preview.defaults[{index}].beforeValue",
            maximum=MAX_STRING_VALUE,
            allow_empty=True,
        )
        node_guid = _guid(entry.get("nodeGuid"), f"preview.defaults[{index}].nodeGuid")
        pin_name = _pin_name(entry.get("pinName"), f"preview.defaults[{index}].pinName")
        pin_guid = _guid(entry.get("pinGuid"), f"preview.defaults[{index}].pinGuid")
        key = (node_guid, pin_name, pin_guid)
        if key in seen:
            raise PinDefaultError(
                "graph-pin-default-preview-invalid",
                f"Preview repeats the pin target {node_guid}.{pin_name} ({pin_guid}).",
            )
        seen.add(key)
        normalized_defaults.append(
            {
                "nodeGuid": node_guid,
                "pinName": pin_name,
                "pinGuid": pin_guid,
                "type": normalized_type,
                "beforeValue": before,
                "afterValue": after,
            }
        )
    expected_confirmation = confirmation_for_plan(item.get("planId"))
    if item.get("requiredConfirmation") != expected_confirmation:
        raise PinDefaultError(
            "graph-pin-default-preview-invalid",
            "Preview requiredConfirmation does not match planId.",
        )
    if item.get("defaultCount") != len(normalized_defaults):
        raise PinDefaultError(
            "graph-pin-default-preview-invalid",
            "Preview defaultCount does not match defaults length.",
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "planId": item.get("planId"),
        "defaultsFingerprint": item.get("defaultsFingerprint"),
        "graphFingerprint": item.get("graphFingerprint"),
        "packageRevision": item.get("packageRevision"),
        "defaults": normalized_defaults,
        "requiredConfirmation": item.get("requiredConfirmation"),
    }


def confirmation_for_plan(plan_id: str) -> str:
    return APPLY_CONFIRMATION_PREFIX + plan_id


def build_apply_parameters(preview: Any, confirmation: Any) -> dict[str, Any]:
    normalized = validate_preview_result(preview)
    expected = confirmation_for_plan(normalized["planId"])
    if not isinstance(confirmation, str) or confirmation != expected:
        raise PinDefaultError(
            "graph-pin-default-confirmation-required",
            f"Explicit Apply confirmation must be exactly {expected}.",
        )
    return {
        "assetPath": normalized["assetPath"],
        "graphGuid": normalized["graphGuid"],
        "defaults": [
            {
                "nodeGuid": entry["nodeGuid"],
                "pinName": entry["pinName"],
                "pinGuid": entry["pinGuid"],
                "type": entry["type"],
                "value": entry["afterValue"],
            }
            for entry in normalized["defaults"]
        ],
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
    expected_defaults_fingerprint: Any,
    expected_node_count: Any,
    expected_link_count: Any,
    confirmation: Any,
) -> dict[str, Any]:
    normalized_asset = _string(asset_path, "assetPath", maximum=512)
    normalized_graph = _guid(graph_guid, "graphGuid")
    normalized_plan = _string(plan_id, "planId", maximum=80)
    if _SHA256.fullmatch(normalized_plan) is None:
        raise PinDefaultError(
            "graph-pin-default-invalid-parameters",
            "planId must be one canonical sha256 plan hash.",
        )
    graph_fingerprint = _string(
        expected_graph_fingerprint,
        "expectedGraphFingerprint",
        maximum=45,
    )
    if _SHA1.fullmatch(graph_fingerprint) is None:
        raise PinDefaultError(
            "graph-pin-default-invalid-parameters",
            "expectedGraphFingerprint must be one canonical sha1 graph fingerprint.",
        )
    defaults_fingerprint = _string(
        expected_defaults_fingerprint,
        "expectedDefaultsFingerprint",
        maximum=71,
    )
    if _SHA256.fullmatch(defaults_fingerprint) is None:
        raise PinDefaultError(
            "graph-pin-default-invalid-parameters",
            "expectedDefaultsFingerprint must be one canonical sha256 fingerprint.",
        )
    for value, label in (
        (expected_node_count, "expectedNodeCount"),
        (expected_link_count, "expectedLinkCount"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise PinDefaultError(
                "graph-pin-default-invalid-parameters",
                f"{label} must be a non-negative integer.",
            )
    expected_confirmation = save_confirmation_for_plan(normalized_plan)
    if not isinstance(confirmation, str) or confirmation != expected_confirmation:
        raise PinDefaultError(
            "graph-pin-default-confirmation-required",
            f"Explicit Save confirmation must be exactly {expected_confirmation}.",
        )
    return {
        "assetPath": normalized_asset,
        "graphGuid": normalized_graph,
        "planId": normalized_plan,
        "expectedGraphFingerprint": graph_fingerprint,
        "expectedDefaultsFingerprint": defaults_fingerprint,
        "expectedNodeCount": expected_node_count,
        "expectedLinkCount": expected_link_count,
        "confirmation": confirmation,
    }


def build_save_parameters(apply_result: Any, confirmation: Any) -> dict[str, Any]:
    item = _object(apply_result, "applyResult")
    if item.get("applied") is not True or item.get("saved") is True:
        raise PinDefaultError(
            "graph-pin-default-save-requires-apply",
            "Save requires the exact successful Apply result before it.",
        )
    return validate_save_parameters(
        asset_path=item.get("assetPath"),
        graph_guid=item.get("graphGuid"),
        plan_id=item.get("planId"),
        expected_graph_fingerprint=item.get("graphFingerprintAfter"),
        expected_defaults_fingerprint=item.get("defaultsFingerprintAfter"),
        expected_node_count=item.get("nodeCountAfter"),
        expected_link_count=item.get("linkCountAfter"),
        confirmation=confirmation,
    )

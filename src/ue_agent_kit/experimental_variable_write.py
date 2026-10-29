"""Bounded, opt-in Blueprint member-variable creation contract.

This is a P2 structural slice.  It appends new top-level Blueprint variables of
the strict bool/int/double/string encodings below to one exact focused Blueprint
that is already saved on disk.  It never renames, removes or retypes existing
variables, never touches nodes or links, never compiles and never saves
implicitly.  The live native module validates the same envelope against the UE
5.6 Blueprint object and persists through the ordinary package save path.
"""

from __future__ import annotations

import math
import re
from typing import Any

PREVIEW_TOOL_NAME = "ue_preview_blueprint_variable_create_experimental"
APPLY_TOOL_NAME = "ue_apply_blueprint_variable_create_experimental"
SAVE_TOOL_NAME = "ue_save_blueprint_variable_create_experimental"
PREVIEW_CAPABILITY = "editor.previewBlueprintVariableCreateExperimental"
APPLY_CAPABILITY = "editor.applyBlueprintVariableCreateExperimental"
SAVE_CAPABILITY = "editor.saveBlueprintVariableCreateExperimental"
PREVIEW_METHOD = "editor.previewBlueprintVariableCreateExperimental"
APPLY_METHOD = "editor.applyBlueprintVariableCreateExperimental"
SAVE_METHOD = "editor.saveBlueprintVariableCreateExperimental"
APPLY_CONFIRMATION_PREFIX = "CREATE EXPERIMENTAL BLUEPRINT VARIABLES "
SAVE_CONFIRMATION_PREFIX = "SAVE EXPERIMENTAL BLUEPRINT VARIABLES "

PREVIEW_SCHEMA_VERSION = "ueak.blueprint-variable-preview/1.0"
APPLY_SCHEMA_VERSION = "ueak.blueprint-variable-apply/1.0"
SAVE_SCHEMA_VERSION = "ueak.blueprint-variable-save/1.0"
MAX_REQUEST_BYTES = 60 * 1024
MAX_VARIABLES = 8
MAX_VARIABLE_NAME = 64
MAX_STRING_VALUE = 1024

VARIABLE_TYPES = ("bool", "int", "double", "string")
_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
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


def _sha256(value: Any, label: str) -> str:
    normalized = _string(value, label, maximum=71)
    if _SHA256.fullmatch(normalized) is None:
        raise VariableWriteError(
            "blueprint-variable-invalid-identifier",
            f"{label} must be one canonical sha256 value.",
        )
    return normalized


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise VariableWriteError(
            "blueprint-variable-invalid-parameters",
            f"{label} must be a non-negative integer.",
        )
    return value


def canonical_variable_name(name: Any) -> str:
    normalized = _string(name, "variables[].name", maximum=MAX_VARIABLE_NAME)
    if _NAME.fullmatch(normalized) is None:
        raise VariableWriteError(
            "blueprint-variable-name-invalid",
            "variables[].name must be 1-64 ASCII letters, digits or '_' "
            "and start with a letter or '_'.",
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


def validate_preview_parameters(
    *,
    asset_path: Any,
    variables: Any,
) -> dict[str, Any]:
    """Validate one bounded Blueprint variable creation preview request."""

    normalized_asset = _string(asset_path, "assetPath", maximum=512)
    if not isinstance(variables, list) or not 1 <= len(variables) <= MAX_VARIABLES:
        raise VariableWriteError(
            "blueprint-variable-invalid-variables",
            f"variables must contain between 1 and {MAX_VARIABLES} objects.",
        )
    normalized_variables: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(variables):
        item = _object(raw, f"variables[{index}]")
        _reject_unknown(item, {"name", "type", "defaultValue"}, f"variables[{index}]")
        name = canonical_variable_name(item.get("name"))
        variable_type, default_value = canonical_variable_value(
            item.get("type"),
            item.get("defaultValue"),
        )
        key = name.lower()
        if key in seen:
            raise VariableWriteError(
                "blueprint-variable-duplicate-name",
                f"variables[{index}].name duplicates another requested variable: {name}",
            )
        seen.add(key)
        normalized_variables.append(
            {
                "name": name,
                "type": variable_type,
                "defaultValue": default_value,
            }
        )
    return {
        "assetPath": normalized_asset,
        "variables": normalized_variables,
    }


def confirmation_for_plan(plan_id: str) -> str:
    return APPLY_CONFIRMATION_PREFIX + plan_id


def save_confirmation_for_plan(plan_id: str) -> str:
    return SAVE_CONFIRMATION_PREFIX + plan_id


def _validate_variable_entry(
    value: Any,
    label: str,
    *,
    require_guid: bool,
    error_code: str = "blueprint-variable-preview-invalid",
) -> dict[str, str]:
    item = _object(value, label)
    allowed = {"name", "type", "defaultValue"} | ({"guid"} if require_guid else set())
    _reject_unknown(item, allowed, label)
    name = canonical_variable_name(item.get("name"))
    variable_type, default_value = canonical_variable_value(
        item.get("type"),
        item.get("defaultValue"),
    )
    normalized = {
        "name": name,
        "type": variable_type,
        "defaultValue": default_value,
    }
    if require_guid:
        normalized["guid"] = _string(item.get("guid"), f"{label}.guid", maximum=36)
        if not re.fullmatch(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
            normalized["guid"],
        ):
            raise VariableWriteError(
                error_code,
                f"{label}.guid must be one canonical lower-case GUID.",
            )
    return normalized


def validate_preview_result(payload: Any) -> dict[str, Any]:
    """Validate the exact Preview envelope returned by the live Editor."""

    item = _object(payload, "previewResult")
    if item.get("schemaVersion") != PREVIEW_SCHEMA_VERSION:
        raise VariableWriteError(
            "blueprint-variable-preview-invalid",
            "Preview result schemaVersion is not the Blueprint variable preview schema.",
        )
    for field, expected in (
        ("experimental", True),
        ("readOnly", True),
        ("wroteBlueprint", False),
        ("savedPackage", False),
        ("compiled", False),
        ("skeletonRegenerated", False),
        ("requiresExplicitCompile", True),
    ):
        if item.get(field) is not expected:
            raise VariableWriteError(
                "blueprint-variable-preview-invalid",
                f"Preview result {field} must be {expected}.",
            )
    asset_path = _string(item.get("assetPath"), "previewResult.assetPath", maximum=512)
    plan_id = _sha256(item.get("planId"), "previewResult.planId")
    fingerprint = _sha256(
        item.get("variablesFingerprint"),
        "previewResult.variablesFingerprint",
    )
    revision = _sha256(item.get("packageRevision"), "previewResult.packageRevision")
    create_count = _count(item.get("createCount"), "previewResult.createCount")
    raw_variables = item.get("variables")
    if not isinstance(raw_variables, list) or len(raw_variables) != create_count:
        raise VariableWriteError(
            "blueprint-variable-preview-invalid",
            "Preview result variables must match createCount.",
        )
    variables = [
        _validate_variable_entry(entry, f"previewResult.variables[{index}]", require_guid=False)
        for index, entry in enumerate(raw_variables)
    ]
    expected_confirmation = confirmation_for_plan(plan_id)
    if item.get("requiredConfirmation") != expected_confirmation:
        raise VariableWriteError(
            "blueprint-variable-preview-invalid",
            "Preview requiredConfirmation does not match planId.",
        )
    return {
        "assetPath": asset_path,
        "planId": plan_id,
        "variablesFingerprint": fingerprint,
        "packageRevision": revision,
        "variables": variables,
        "requiredConfirmation": item.get("requiredConfirmation"),
    }


def build_apply_parameters(preview: Any, confirmation: Any) -> dict[str, Any]:
    normalized = validate_preview_result(preview)
    expected = confirmation_for_plan(normalized["planId"])
    if not isinstance(confirmation, str) or confirmation != expected:
        raise VariableWriteError(
            "blueprint-variable-confirmation-required",
            f"Explicit Apply confirmation must be exactly {expected}.",
        )
    return {
        "assetPath": normalized["assetPath"],
        "planId": normalized["planId"],
        "variables": [
            {
                "name": entry["name"],
                "type": entry["type"],
                "defaultValue": entry["defaultValue"],
            }
            for entry in normalized["variables"]
        ],
        "confirmation": confirmation,
    }


def validate_apply_result(payload: Any) -> dict[str, Any]:
    """Validate the exact Apply envelope returned by the live Editor."""

    item = _object(payload, "applyResult")
    if item.get("schemaVersion") != APPLY_SCHEMA_VERSION:
        raise VariableWriteError(
            "blueprint-variable-apply-invalid",
            "Apply result schemaVersion is not the Blueprint variable apply schema.",
        )
    for field, expected in (
        ("experimental", True),
        ("applied", True),
        ("singleTransaction", True),
        ("undoVerified", True),
        ("redoVerified", True),
        ("saved", False),
        ("compiled", False),
        ("skeletonRegenerated", False),
        ("requiresExplicitCompile", True),
        ("packageDirty", True),
    ):
        if item.get(field) is not expected:
            raise VariableWriteError(
                "blueprint-variable-apply-invalid",
                f"Apply result {field} must be {expected}.",
            )
    asset_path = _string(item.get("assetPath"), "applyResult.assetPath", maximum=512)
    plan_id = _sha256(item.get("planId"), "applyResult.planId")
    fingerprint_before = _sha256(
        item.get("variablesFingerprintBefore"),
        "applyResult.variablesFingerprintBefore",
    )
    fingerprint_after = _sha256(
        item.get("variablesFingerprintAfter"),
        "applyResult.variablesFingerprintAfter",
    )
    revision = _sha256(item.get("packageRevision"), "applyResult.packageRevision")
    count_before = _count(item.get("variableCountBefore"), "applyResult.variableCountBefore")
    count_after = _count(item.get("variableCountAfter"), "applyResult.variableCountAfter")
    create_count = _count(item.get("createCount"), "applyResult.createCount")
    if count_after != count_before + create_count:
        raise VariableWriteError(
            "blueprint-variable-apply-invalid",
            "Apply result variable counts do not match createCount.",
        )
    raw_created = item.get("createdVariables")
    if not isinstance(raw_created, list) or len(raw_created) != create_count:
        raise VariableWriteError(
            "blueprint-variable-apply-invalid",
            "Apply result createdVariables must match createCount.",
        )
    created = [
        _validate_variable_entry(
            entry,
            f"applyResult.createdVariables[{index}]",
            require_guid=True,
            error_code="blueprint-variable-apply-invalid",
        )
        for index, entry in enumerate(raw_created)
    ]
    expected_save_confirmation = save_confirmation_for_plan(plan_id)
    if item.get("requiredSaveConfirmation") != expected_save_confirmation:
        raise VariableWriteError(
            "blueprint-variable-apply-invalid",
            "Apply requiredSaveConfirmation does not match planId.",
        )
    return {
        "assetPath": asset_path,
        "planId": plan_id,
        "variablesFingerprintBefore": fingerprint_before,
        "variablesFingerprintAfter": fingerprint_after,
        "packageRevision": revision,
        "variableCountBefore": count_before,
        "variableCountAfter": count_after,
        "createCount": create_count,
        "createdVariables": created,
        "requiredSaveConfirmation": item.get("requiredSaveConfirmation"),
    }


def validate_save_parameters(
    *,
    asset_path: Any,
    plan_id: Any,
    expected_variables_fingerprint: Any,
    expected_package_revision: Any,
    expected_variable_count: Any,
    confirmation: Any,
) -> dict[str, Any]:
    normalized_asset = _string(asset_path, "assetPath", maximum=512)
    normalized_plan = _sha256(plan_id, "planId")
    fingerprint = _sha256(
        expected_variables_fingerprint,
        "expectedVariablesFingerprint",
    )
    revision = _sha256(expected_package_revision, "expectedPackageRevision")
    count = _count(expected_variable_count, "expectedVariableCount")
    expected_confirmation = save_confirmation_for_plan(normalized_plan)
    if not isinstance(confirmation, str) or confirmation != expected_confirmation:
        raise VariableWriteError(
            "blueprint-variable-confirmation-required",
            f"Explicit Save confirmation must be exactly {expected_confirmation}.",
        )
    return {
        "assetPath": normalized_asset,
        "planId": normalized_plan,
        "expectedVariablesFingerprint": fingerprint,
        "expectedPackageRevision": revision,
        "expectedVariableCount": count,
        "confirmation": confirmation,
    }


def build_save_parameters(apply_result: Any, confirmation: Any) -> dict[str, Any]:
    normalized = validate_apply_result(apply_result)
    return validate_save_parameters(
        asset_path=normalized["assetPath"],
        plan_id=normalized["planId"],
        expected_variables_fingerprint=normalized["variablesFingerprintAfter"],
        expected_package_revision=normalized["packageRevision"],
        expected_variable_count=normalized["variableCountAfter"],
        confirmation=confirmation,
    )


def validate_save_result(payload: Any) -> dict[str, Any]:
    """Validate the exact Save envelope returned by the live Editor."""

    item = _object(payload, "saveResult")
    if item.get("schemaVersion") != SAVE_SCHEMA_VERSION:
        raise VariableWriteError(
            "blueprint-variable-save-invalid",
            "Save result schemaVersion is not the Blueprint variable save schema.",
        )
    for field, expected in (
        ("experimental", True),
        ("saved", True),
        ("packageDirty", False),
        ("readbackVerified", True),
        ("independentReloadVerified", False),
        ("compiled", False),
        ("skeletonRegenerated", False),
        ("requiresExplicitCompile", True),
        ("autoSave", False),
    ):
        if item.get(field) is not expected:
            raise VariableWriteError(
                "blueprint-variable-save-invalid",
                f"Save result {field} must be {expected}.",
            )
    if item.get("requiredAcceptanceEvidence") != "independent-reload":
        raise VariableWriteError(
            "blueprint-variable-save-invalid",
            "Save result requiredAcceptanceEvidence must be independent-reload.",
        )
    asset_path = _string(item.get("assetPath"), "saveResult.assetPath", maximum=512)
    plan_id = _sha256(item.get("planId"), "saveResult.planId")
    asset_sha = _sha256(item.get("assetSha256"), "saveResult.assetSha256")
    fingerprint = _sha256(item.get("variablesFingerprint"), "saveResult.variablesFingerprint")
    count = _count(item.get("variableCount"), "saveResult.variableCount")
    create_count = _count(item.get("createCount"), "saveResult.createCount")
    if create_count > count:
        raise VariableWriteError(
            "blueprint-variable-save-invalid",
            "Save result createCount must not exceed variableCount.",
        )
    raw_variables = item.get("variables")
    if not isinstance(raw_variables, list) or len(raw_variables) != create_count:
        raise VariableWriteError(
            "blueprint-variable-save-invalid",
            "Save result variables must match createCount.",
        )
    variables = [
        _validate_variable_entry(
            entry,
            f"saveResult.variables[{index}]",
            require_guid=True,
            error_code="blueprint-variable-save-invalid",
        )
        for index, entry in enumerate(raw_variables)
    ]
    return {
        "assetPath": asset_path,
        "planId": plan_id,
        "assetSha256": asset_sha,
        "variablesFingerprint": fingerprint,
        "variableCount": count,
        "createCount": create_count,
        "variables": variables,
    }


__all__ = [
    "APPLY_CONFIRMATION_PREFIX",
    "APPLY_CAPABILITY",
    "APPLY_METHOD",
    "APPLY_SCHEMA_VERSION",
    "APPLY_TOOL_NAME",
    "MAX_REQUEST_BYTES",
    "MAX_STRING_VALUE",
    "MAX_VARIABLES",
    "MAX_VARIABLE_NAME",
    "PREVIEW_CAPABILITY",
    "PREVIEW_METHOD",
    "PREVIEW_SCHEMA_VERSION",
    "PREVIEW_TOOL_NAME",
    "SAVE_CAPABILITY",
    "SAVE_CONFIRMATION_PREFIX",
    "SAVE_METHOD",
    "SAVE_SCHEMA_VERSION",
    "SAVE_TOOL_NAME",
    "VARIABLE_TYPES",
    "VariableWriteError",
    "build_apply_parameters",
    "build_save_parameters",
    "canonical_variable_name",
    "canonical_variable_value",
    "confirmation_for_plan",
    "save_confirmation_for_plan",
    "validate_apply_result",
    "validate_preview_parameters",
    "validate_preview_result",
    "validate_save_parameters",
    "validate_save_result",
]

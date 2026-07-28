from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


PATCH_SCHEMA_VERSION = "1.0"
POLICY_SCHEMA_VERSION = "1.0"
MAX_CONTROL_FILE_BYTES = 4 * 1024 * 1024
MAX_CANONICAL_FILE_BYTES = 64 * 1024 * 1024
REVISION_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class OperationSpec:
    name: str
    risk: str
    target_fields: tuple[str, ...]
    target_validators: dict[str, Callable[[Any], bool]]
    expected_change: str
    asset_type: str


def _is_nonempty_text(value: Any, *, max_length: int = 256) -> bool:
    return (
        isinstance(value, str)
        and 0 < len(value) <= max_length
        and not any(ord(character) < 32 for character in value)
    )


def _is_property_path(value: Any) -> bool:
    if not _is_nonempty_text(value, max_length=512):
        return False
    assert isinstance(value, str)
    return all(_is_nonempty_text(segment, max_length=256) for segment in value.split("."))


def _is_top_level_property_name(value: Any) -> bool:
    return _is_nonempty_text(value, max_length=256) and isinstance(value, str) and "." not in value


def _is_guid(value: Any) -> bool:
    return isinstance(value, str) and GUID_PATTERN.fullmatch(value) is not None


OPERATION_REGISTRY: dict[str, OperationSpec] = {
    "setVariableDefault": OperationSpec(
        name="setVariableDefault",
        risk="low",
        target_fields=("variableName",),
        target_validators={"variableName": _is_nonempty_text},
        expected_change="variable-default",
        asset_type="Blueprint",
    ),
    "setComponentProperty": OperationSpec(
        name="setComponentProperty",
        risk="low",
        target_fields=("componentName", "propertyPath"),
        target_validators={
            "componentName": _is_nonempty_text,
            "propertyPath": _is_property_path,
        },
        expected_change="component-property",
        asset_type="Blueprint",
    ),
    "setPinDefault": OperationSpec(
        name="setPinDefault",
        risk="low",
        target_fields=("graphGuid", "nodeGuid", "pinName"),
        target_validators={
            "graphGuid": _is_guid,
            "nodeGuid": _is_guid,
            "pinName": _is_nonempty_text,
        },
        expected_change="pin-default",
        asset_type="Blueprint",
    ),
    "setBlueprintDescription": OperationSpec(
        name="setBlueprintDescription",
        risk="low",
        target_fields=(),
        target_validators={},
        expected_change="blueprint-description",
        asset_type="Blueprint",
    ),
    "setAssetProperty": OperationSpec(
        name="setAssetProperty",
        risk="medium",
        target_fields=("propertyPath",),
        target_validators={"propertyPath": _is_property_path},
        expected_change="asset-property",
        asset_type="NonBlueprint",
    ),
    "setAssetReferenceProperty": OperationSpec(
        name="setAssetReferenceProperty",
        risk="medium",
        target_fields=("propertyPath",),
        target_validators={"propertyPath": _is_top_level_property_name},
        expected_change="asset-reference-property",
        asset_type="NonBlueprint",
    ),
    "setAssetStructuredProperty": OperationSpec(
        name="setAssetStructuredProperty",
        risk="medium",
        target_fields=("propertyPath",),
        target_validators={"propertyPath": _is_top_level_property_name},
        expected_change="asset-structured-property",
        asset_type="NonBlueprint",
    ),
    "setMaterialInstanceScalarParameter": OperationSpec(
        name="setMaterialInstanceScalarParameter",
        risk="medium",
        target_fields=("parameterName",),
        target_validators={"parameterName": _is_nonempty_text},
        expected_change="material-instance-scalar-parameter",
        asset_type="NonBlueprint",
    ),
    "setMaterialInstanceVectorParameter": OperationSpec(
        name="setMaterialInstanceVectorParameter",
        risk="medium",
        target_fields=("parameterName",),
        target_validators={"parameterName": _is_nonempty_text},
        expected_change="material-instance-vector-parameter",
        asset_type="NonBlueprint",
    ),
    "setMaterialInstanceTextureParameter": OperationSpec(
        name="setMaterialInstanceTextureParameter",
        risk="medium",
        target_fields=("parameterName",),
        target_validators={"parameterName": _is_nonempty_text},
        expected_change="material-instance-texture-parameter",
        asset_type="NonBlueprint",
    ),
    "setMaterialInstanceStaticSwitchParameter": OperationSpec(
        name="setMaterialInstanceStaticSwitchParameter",
        risk="medium",
        target_fields=("parameterName",),
        target_validators={"parameterName": _is_nonempty_text},
        expected_change="material-instance-static-switch-parameter",
        asset_type="NonBlueprint",
    ),
    "setDataTableCell": OperationSpec(
        name="setDataTableCell",
        risk="medium",
        target_fields=("rowName", "fieldName"),
        target_validators={
            "rowName": _is_nonempty_text,
            "fieldName": _is_nonempty_text,
        },
        expected_change="data-table-cell",
        asset_type="NonBlueprint",
    ),
    "setDataTableRowFields": OperationSpec(
        name="setDataTableRowFields",
        risk="medium",
        target_fields=("rowName",),
        target_validators={"rowName": _is_nonempty_text},
        expected_change="data-table-row-fields",
        asset_type="NonBlueprint",
    ),
    "addDataTableRow": OperationSpec(
        name="addDataTableRow",
        risk="high",
        target_fields=("rowName",),
        target_validators={"rowName": _is_nonempty_text},
        expected_change="data-table-row-add",
        asset_type="NonBlueprint",
    ),
    "removeDataTableRow": OperationSpec(
        name="removeDataTableRow",
        risk="high",
        target_fields=("rowName",),
        target_validators={"rowName": _is_nonempty_text},
        expected_change="data-table-row-remove",
        asset_type="NonBlueprint",
    ),
    "renameDataTableRow": OperationSpec(
        name="renameDataTableRow",
        risk="high",
        target_fields=("rowName", "newRowName"),
        target_validators={
            "rowName": _is_nonempty_text,
            "newRowName": _is_nonempty_text,
        },
        expected_change="data-table-row-rename",
        asset_type="NonBlueprint",
    ),
}


POLICY_FIELDS = {
    "schemaVersion",
    "validationEnabled",
    "commitEnabled",
    "allowedProjectNames",
    "allowedAssetRoots",
    "allowedReferenceRoots",
    "allowedReferenceClasses",
    "allowedOperations",
    "allowedAssetClasses",
    "allowedAssetProperties",
    "allowedMaterialParameters",
    "allowedDataTableFields",
    "requireRevision",
    "rejectDirtyPackages",
    "maxAssetsPerPatch",
    "maxOperationsPerAsset",
    "maxValueBytes",
}
PATCH_FIELDS = {"schemaVersion", "patchId", "projectName", "description", "assets"}
ASSET_FIELDS = {"assetPath", "expectedRevision", "expectedAssetClass", "operations"}
OPERATION_FIELDS = {"operationId", "operation", "target", "value"}


def get_operation_registry() -> list[dict[str, Any]]:
    return [
        {
            "operation": spec.name,
            "risk": spec.risk,
            "targetFields": list(spec.target_fields),
            "expectedChange": spec.expected_change,
            "assetType": spec.asset_type,
            "dryRunSupported": True,
            "commitSupported": True,
        }
        for spec in OPERATION_REGISTRY.values()
    ]


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json_object(path: Path, *, max_bytes: int) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    size = resolved.stat().st_size
    if size > max_bytes:
        raise ValueError(f"JSON file exceeds {max_bytes} bytes: {resolved}")
    try:
        value = json.loads(
            resolved.read_text(encoding="utf-8-sig"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {resolved}")
    return value


def _issue(errors: list[dict[str, str]], code: str, message: str, path: str) -> None:
    errors.append({"code": code, "message": message, "path": path})


def _check_unknown_fields(
    value: dict[str, Any],
    allowed_fields: set[str],
    *,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    for field in sorted(set(value) - allowed_fields):
        _issue(errors, "unknown-field", f"Unknown field: {field}", f"{path}.{field}")


def _require_fields(
    value: dict[str, Any],
    required_fields: set[str],
    *,
    path: str,
    errors: list[dict[str, str]],
) -> None:
    for field in sorted(required_fields - set(value)):
        _issue(errors, "missing-field", f"Missing required field: {field}", f"{path}.{field}")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(_is_nonempty_text(item, max_length=512) for item in value)
        and len(set(value)) == len(value)
    )


def _normalize_asset_root(value: str) -> str:
    return value.rstrip("/")


def _validate_policy(policy: dict[str, Any], errors: list[dict[str, str]]) -> dict[str, Any]:
    path = "policy"
    _check_unknown_fields(policy, POLICY_FIELDS, path=path, errors=errors)
    required_fields = POLICY_FIELDS - {
        "allowedMaterialParameters",
        "allowedDataTableFields",
        "allowedReferenceRoots",
        "allowedReferenceClasses",
    }
    _require_fields(policy, required_fields, path=path, errors=errors)

    schema_version = policy.get("schemaVersion")
    if schema_version != POLICY_SCHEMA_VERSION:
        _issue(
            errors,
            "policy-schema-version",
            f"Policy schemaVersion must be {POLICY_SCHEMA_VERSION}.",
            "policy.schemaVersion",
        )

    validation_enabled = policy.get("validationEnabled")
    if not isinstance(validation_enabled, bool):
        _issue(errors, "policy-type", "validationEnabled must be a boolean.", "policy.validationEnabled")
    elif not validation_enabled:
        _issue(errors, "policy-disabled", "Patch validation is disabled by policy.", "policy.validationEnabled")

    commit_enabled = policy.get("commitEnabled")
    if not isinstance(commit_enabled, bool):
        _issue(errors, "policy-type", "commitEnabled must be a boolean.", "policy.commitEnabled")

    allowed_project_names = policy.get("allowedProjectNames")
    if not _valid_string_list(allowed_project_names):
        _issue(
            errors,
            "policy-projects",
            "allowedProjectNames must be a non-empty unique string array.",
            "policy.allowedProjectNames",
        )
        allowed_project_names = []

    allowed_asset_roots = policy.get("allowedAssetRoots")
    normalized_roots: list[str] = []
    if not _valid_string_list(allowed_asset_roots):
        _issue(
            errors,
            "policy-roots",
            "allowedAssetRoots must be a non-empty unique string array.",
            "policy.allowedAssetRoots",
        )
    else:
        assert isinstance(allowed_asset_roots, list)
        for index, item in enumerate(allowed_asset_roots):
            assert isinstance(item, str)
            root = _normalize_asset_root(item)
            if root == "/Game":
                _issue(
                    errors,
                    "policy-root-too-broad",
                    "The entire /Game root cannot be authorized by the validation-only baseline.",
                    f"policy.allowedAssetRoots[{index}]",
                )
                continue
            if not root.startswith("/Game/") or "." in root or "\\" in root or "//" in root:
                _issue(
                    errors,
                    "policy-root-format",
                    "Allowed roots must be package paths below /Game, without object names.",
                    f"policy.allowedAssetRoots[{index}]",
                )
                continue
            normalized_roots.append(root)
        if len(set(normalized_roots)) != len(normalized_roots):
            _issue(errors, "policy-roots", "Normalized allowedAssetRoots must be unique.", "policy.allowedAssetRoots")

    allowed_reference_roots_value = policy.get("allowedReferenceRoots", [])
    normalized_reference_roots: list[str] = []
    if not isinstance(allowed_reference_roots_value, list):
        _issue(
            errors,
            "policy-reference-roots",
            "allowedReferenceRoots must be a unique string array.",
            "policy.allowedReferenceRoots",
        )
    else:
        string_items = [item for item in allowed_reference_roots_value if isinstance(item, str)]
        if len(set(string_items)) != len(allowed_reference_roots_value):
            _issue(
                errors,
                "policy-reference-roots",
                "allowedReferenceRoots must contain unique strings.",
                "policy.allowedReferenceRoots",
            )
        for index, item in enumerate(allowed_reference_roots_value):
            if not isinstance(item, str):
                continue
            root = _normalize_asset_root(item)
            if root == "/Game":
                _issue(
                    errors,
                    "policy-reference-root-too-broad",
                    "The entire /Game root cannot be authorized for referenced assets.",
                    f"policy.allowedReferenceRoots[{index}]",
                )
                continue
            if not root.startswith("/Game/") or "." in root or "\\" in root or "//" in root:
                _issue(
                    errors,
                    "policy-reference-root-format",
                    "Reference roots must be package paths below /Game, without object names.",
                    f"policy.allowedReferenceRoots[{index}]",
                )
                continue
            normalized_reference_roots.append(root)

    allowed_reference_classes_value = policy.get("allowedReferenceClasses", [])
    normalized_reference_classes: list[str] = []
    if not isinstance(allowed_reference_classes_value, list):
        _issue(
            errors,
            "policy-reference-classes",
            "allowedReferenceClasses must be a unique string array.",
            "policy.allowedReferenceClasses",
        )
    else:
        string_items = [item for item in allowed_reference_classes_value if isinstance(item, str)]
        if len(set(string_items)) != len(allowed_reference_classes_value):
            _issue(
                errors,
                "policy-reference-classes",
                "allowedReferenceClasses must contain unique strings.",
                "policy.allowedReferenceClasses",
            )
        for index, item in enumerate(allowed_reference_classes_value):
            valid_script_class = isinstance(item, str) and item.startswith("/Script/") and "." in item
            valid_generated_class = False
            if isinstance(item, str) and item.startswith("/Game/") and item.count(".") == 1:
                package_name, object_name = item.rsplit(".", 1)
                package_leaf = package_name.rsplit("/", 1)[-1]
                valid_generated_class = object_name == package_leaf + "_C"
            if not valid_script_class and not valid_generated_class:
                _issue(
                    errors,
                    "policy-reference-class-format",
                    "Reference classes must use /Script/Module.Class or /Game/Package.Asset_C paths.",
                    f"policy.allowedReferenceClasses[{index}]",
                )
                continue
            normalized_reference_classes.append(item)

    allowed_operations = policy.get("allowedOperations")
    normalized_operations: list[str] = []
    if not _valid_string_list(allowed_operations):
        _issue(
            errors,
            "policy-operations",
            "allowedOperations must be a non-empty unique string array.",
            "policy.allowedOperations",
        )
    else:
        assert isinstance(allowed_operations, list)
        normalized_operations = list(allowed_operations)
        for index, operation in enumerate(normalized_operations):
            if operation not in OPERATION_REGISTRY:
                _issue(
                    errors,
                    "policy-unknown-operation",
                    f"Policy references unknown operation: {operation}",
                    f"policy.allowedOperations[{index}]",
                )

    allowed_asset_classes = policy.get("allowedAssetClasses")
    if not _valid_string_list(allowed_asset_classes):
        _issue(
            errors,
            "policy-asset-classes",
            "allowedAssetClasses must be a non-empty unique string array.",
            "policy.allowedAssetClasses",
        )
        allowed_asset_classes = []
    else:
        assert isinstance(allowed_asset_classes, list)
        for index, asset_class in enumerate(allowed_asset_classes):
            if not isinstance(asset_class, str) or not asset_class.startswith("/Script/") or "." not in asset_class:
                _issue(
                    errors,
                    "policy-asset-class-format",
                    "Asset classes must use /Script/Module.Class paths.",
                    f"policy.allowedAssetClasses[{index}]",
                )

    allowed_asset_properties_value = policy.get("allowedAssetProperties", [])
    normalized_asset_properties: list[str] = []
    if not isinstance(allowed_asset_properties_value, list):
        _issue(
            errors,
            "policy-asset-properties",
            "allowedAssetProperties must be a unique string array.",
            "policy.allowedAssetProperties",
        )
    else:
        if len(set(item for item in allowed_asset_properties_value if isinstance(item, str))) != len(
            allowed_asset_properties_value
        ):
            _issue(
                errors,
                "policy-asset-properties",
                "allowedAssetProperties must contain unique strings.",
                "policy.allowedAssetProperties",
            )
        for index, item in enumerate(allowed_asset_properties_value):
            if not isinstance(item, str) or item.count("#") != 1:
                _issue(
                    errors,
                    "policy-asset-property-format",
                    "Asset property entries must use /Script/Module.Class#Property.Path form.",
                    f"policy.allowedAssetProperties[{index}]",
                )
                continue
            asset_class, property_path = item.split("#", 1)
            if (
                asset_class not in allowed_asset_classes
                or not asset_class.startswith("/Script/")
                or "." not in asset_class
                or not _is_property_path(property_path)
            ):
                _issue(
                    errors,
                    "policy-asset-property-format",
                    "Asset property entries must reference an allowed class and valid property path.",
                    f"policy.allowedAssetProperties[{index}]",
                )
                continue
            normalized_asset_properties.append(item)
    asset_property_operations = {"setAssetProperty", "setAssetReferenceProperty", "setAssetStructuredProperty"}
    if asset_property_operations.intersection(normalized_operations) and not normalized_asset_properties:
        _issue(
            errors,
            "policy-asset-properties",
            "Asset property operations require at least one allowedAssetProperties entry.",
            "policy.allowedAssetProperties",
        )

    allowed_material_parameters_value = policy.get("allowedMaterialParameters", [])
    normalized_material_parameters: list[str] = []
    if not isinstance(allowed_material_parameters_value, list):
        _issue(
            errors,
            "policy-material-parameters",
            "allowedMaterialParameters must be a unique string array.",
            "policy.allowedMaterialParameters",
        )
    else:
        string_items = [item for item in allowed_material_parameters_value if isinstance(item, str)]
        if len(set(string_items)) != len(allowed_material_parameters_value):
            _issue(
                errors,
                "policy-material-parameters",
                "allowedMaterialParameters must contain unique strings.",
                "policy.allowedMaterialParameters",
            )
        for index, item in enumerate(allowed_material_parameters_value):
            if not isinstance(item, str) or item.count("#") != 2:
                _issue(
                    errors,
                    "policy-material-parameter-format",
                    "Material parameter entries must use /Script/Module.Class#Type#ParameterName form.",
                    f"policy.allowedMaterialParameters[{index}]",
                )
                continue
            asset_class, parameter_type, parameter_name = item.split("#", 2)
            if (
                asset_class not in allowed_asset_classes
                or not asset_class.startswith("/Script/")
                or "." not in asset_class
                or asset_class != "/Script/Engine.MaterialInstanceConstant"
                or parameter_type not in {"Scalar", "Vector", "Texture", "StaticSwitch"}
                or not _is_nonempty_text(parameter_name, max_length=256)
            ):
                _issue(
                    errors,
                    "policy-material-parameter-format",
                    "Material parameter entries must reference an allowed class, supported type, and valid name.",
                    f"policy.allowedMaterialParameters[{index}]",
                )
                continue
            normalized_material_parameters.append(item)
    material_operations = {
        "setMaterialInstanceScalarParameter",
        "setMaterialInstanceVectorParameter",
        "setMaterialInstanceTextureParameter",
        "setMaterialInstanceStaticSwitchParameter",
    }
    if material_operations.intersection(normalized_operations) and not normalized_material_parameters:
        _issue(
            errors,
            "policy-material-parameters",
            "Material Instance parameter operations require allowedMaterialParameters authorization.",
            "policy.allowedMaterialParameters",
        )
    allowed_data_table_fields_value = policy.get("allowedDataTableFields", [])
    normalized_data_table_fields: list[str] = []
    if not isinstance(allowed_data_table_fields_value, list):
        _issue(
            errors,
            "policy-data-table-fields",
            "allowedDataTableFields must be a unique string array.",
            "policy.allowedDataTableFields",
        )
    else:
        string_items = [item for item in allowed_data_table_fields_value if isinstance(item, str)]
        if len(set(string_items)) != len(allowed_data_table_fields_value):
            _issue(
                errors,
                "policy-data-table-fields",
                "allowedDataTableFields must contain unique strings.",
                "policy.allowedDataTableFields",
            )
        for index, item in enumerate(allowed_data_table_fields_value):
            if not isinstance(item, str) or item.count("#") != 2:
                _issue(
                    errors,
                    "policy-data-table-field-format",
                    "DataTable field entries must use /Script/Engine.DataTable#RowStructPath#FieldName form.",
                    f"policy.allowedDataTableFields[{index}]",
                )
                continue
            asset_class, row_struct_path, field_name = item.split("#", 2)
            valid_row_struct = (
                row_struct_path.startswith("/Script/")
                and "." in row_struct_path
            ) or (
                row_struct_path.startswith("/Game/")
                and "." in row_struct_path
            )
            if (
                asset_class != "/Script/Engine.DataTable"
                or asset_class not in allowed_asset_classes
                or not valid_row_struct
                or not _is_nonempty_text(field_name, max_length=256)
                or "." in field_name
            ):
                _issue(
                    errors,
                    "policy-data-table-field-format",
                    "DataTable field entries must reference the allowed DataTable class, an exact row struct, and one top-level field.",
                    f"policy.allowedDataTableFields[{index}]",
                )
                continue
            normalized_data_table_fields.append(item)
    data_table_operations = {
        "setDataTableCell",
        "setDataTableRowFields",
        "addDataTableRow",
    }
    if data_table_operations.intersection(normalized_operations) and not normalized_data_table_fields:
        _issue(
            errors,
            "policy-data-table-fields",
            "DataTable field operations require allowedDataTableFields authorization.",
            "policy.allowedDataTableFields",
        )

    reference_write_operations = {
        "setMaterialInstanceTextureParameter",
        "setAssetReferenceProperty",
    }
    if reference_write_operations.intersection(normalized_operations):
        if not normalized_reference_roots:
            _issue(
                errors,
                "policy-reference-roots",
                "Reference writes require allowedReferenceRoots authorization.",
                "policy.allowedReferenceRoots",
            )
        if not normalized_reference_classes:
            _issue(
                errors,
                "policy-reference-classes",
                "Reference writes require allowedReferenceClasses authorization.",
                "policy.allowedReferenceClasses",
            )

    require_revision = policy.get("requireRevision")
    if not isinstance(require_revision, bool):
        _issue(errors, "policy-type", "requireRevision must be a boolean.", "policy.requireRevision")

    reject_dirty_packages = policy.get("rejectDirtyPackages")
    if not isinstance(reject_dirty_packages, bool):
        _issue(
            errors,
            "policy-type",
            "rejectDirtyPackages must be a boolean.",
            "policy.rejectDirtyPackages",
        )

    limits: dict[str, int] = {}
    limit_ranges = {
        "maxAssetsPerPatch": (1, 100),
        "maxOperationsPerAsset": (1, 32),
        "maxValueBytes": (1, 1024 * 1024),
    }
    for field, (minimum, maximum) in limit_ranges.items():
        value = policy.get(field)
        if not _is_int(value) or not minimum <= value <= maximum:
            _issue(
                errors,
                "policy-limit",
                f"{field} must be an integer from {minimum} through {maximum}.",
                f"policy.{field}",
            )
            limits[field] = minimum
        else:
            limits[field] = value

    return {
        "schemaVersion": schema_version,
        "validationEnabled": validation_enabled is True,
        "commitEnabled": commit_enabled is True,
        "allowedProjectNames": list(allowed_project_names) if isinstance(allowed_project_names, list) else [],
        "allowedAssetRoots": sorted(set(normalized_roots)),
        "allowedReferenceRoots": sorted(set(normalized_reference_roots)),
        "allowedReferenceClasses": sorted(set(normalized_reference_classes)),
        "allowedOperations": sorted(set(normalized_operations)),
        "allowedAssetClasses": sorted(set(allowed_asset_classes)) if isinstance(allowed_asset_classes, list) else [],
        "allowedAssetProperties": sorted(set(normalized_asset_properties)),
        "allowedMaterialParameters": sorted(set(normalized_material_parameters)),
        "allowedDataTableFields": sorted(set(normalized_data_table_fields)),
        "requireRevision": require_revision is True,
        "rejectDirtyPackages": reject_dirty_packages is True,
        **limits,
    }


def _validate_asset_path(value: Any) -> tuple[bool, str]:
    if not isinstance(value, str) or not value.startswith("/Game/"):
        return False, "Asset path must start with /Game/."
    if "\\" in value or "//" in value or any(ord(character) < 32 for character in value):
        return False, "Asset path contains invalid separators or control characters."
    if value.count(".") != 1:
        return False, "Asset path must use /Game/Package.Asset object-path form."
    package_name, object_name = value.rsplit(".", 1)
    package_leaf = package_name.rsplit("/", 1)[-1]
    if not package_leaf or object_name != package_leaf:
        return False, "Asset object name must match the package leaf name."
    package_segments = package_name.removeprefix("/").split("/")
    if any(segment in {"", ".", ".."} for segment in package_segments):
        return False, "Asset package path contains an empty or traversal segment."
    return True, package_name


def _path_is_allowed(package_name: str, roots: list[str]) -> bool:
    return any(package_name == root or package_name.startswith(root + "/") for root in roots)


def _validate_reference_object_path(value: Any, reference_type: str) -> tuple[bool, str]:
    if reference_type in {"Object", "SoftObject"}:
        return _validate_asset_path(value)
    if reference_type not in {"Class", "SoftClass"}:
        return False, "referenceType must be Object, Class, SoftObject, or SoftClass."
    if not isinstance(value, str) or not value.startswith("/Game/") or value.count(".") != 1:
        return False, "Class reference path must use /Game/Package.Asset_C form."
    if "\\" in value or "//" in value or any(ord(character) < 32 for character in value):
        return False, "Class reference path contains invalid separators or control characters."
    package_name, object_name = value.rsplit(".", 1)
    package_leaf = package_name.rsplit("/", 1)[-1]
    if object_name != package_leaf + "_C":
        return False, "Class reference object name must equal the package leaf plus _C."
    return True, package_name


def _validate_asset_reference_value(value: Any, max_value_bytes: int) -> tuple[bool, str, str]:
    if value is None:
        return True, "", ""
    if not isinstance(value, dict) or set(value) != {"referenceType", "path"}:
        return False, "Asset reference value must be null or contain exactly referenceType and path.", ""
    reference_type = value.get("referenceType")
    reference_path = value.get("path")
    if not isinstance(reference_type, str) or not isinstance(reference_path, str):
        return False, "Asset reference referenceType and path must be strings.", ""
    valid_path, package_or_error = _validate_reference_object_path(reference_path, reference_type)
    if not valid_path:
        return False, package_or_error, ""
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_value_bytes:
        return False, "Asset reference value exceeds maxValueBytes.", ""
    return True, package_or_error, reference_type


STRUCTURED_MAX_DEPTH = 8
STRUCTURED_MAX_CONTAINER_ENTRIES = 4096
STRUCTURED_INTEGER_RANGES = {
    "Int8": (-128, 127),
    "UInt8": (0, 255),
    "Int16": (-32768, 32767),
    "UInt16": (0, 65535),
    "Int32": (-2147483648, 2147483647),
    "UInt32": (0, 4294967295),
}


def _canonical_json(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("Non-finite JSON number")
        return "0" if value == 0.0 else format(value, ".17g")
    if isinstance(value, list):
        return "[" + ",".join(_canonical_json(item) for item in value) + "]"
    if isinstance(value, dict):
        keys = sorted(value, key=lambda item: item.encode("utf-8"))
        return "{" + ",".join(
            json.dumps(key, ensure_ascii=False, separators=(",", ":")) + ":" + _canonical_json(value[key])
            for key in keys
        ) + "}"
    raise TypeError(f"Unsupported JSON value: {type(value).__name__}")


def _canonical_sort_key(value: Any) -> bytes:
    return _canonical_json(value).encode("utf-8")


def _validate_structured_value_node(value: Any, schema: Any, depth: int = 0) -> str:
    if depth > STRUCTURED_MAX_DEPTH:
        return "Structured value exceeds the maximum nesting depth."
    if not isinstance(schema, dict):
        return "Structured property schema is missing or invalid."
    kind = schema.get("kind")
    if kind == "Scalar":
        scalar_type = schema.get("scalarType")
        if scalar_type == "Bool":
            return "" if isinstance(value, bool) else "Expected a JSON boolean."
        if scalar_type in STRUCTURED_INTEGER_RANGES:
            if isinstance(value, bool) or not isinstance(value, int):
                return f"Expected an integer for {scalar_type}."
            minimum, maximum = STRUCTURED_INTEGER_RANGES[scalar_type]
            return "" if minimum <= value <= maximum else f"Integer is outside the {scalar_type} range."
        if scalar_type in {"Float", "Double"}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
                return f"Expected a finite number for {scalar_type}."
            return ""
        if scalar_type in {"String", "Name"}:
            return "" if isinstance(value, str) else f"Expected a JSON string for {scalar_type}."
        if scalar_type == "Enum":
            values = schema.get("values")
            if not isinstance(value, str) or not isinstance(values, list) or value not in values:
                return "Enum value is not present in the exported property schema."
            return ""
        return f"Unsupported structured scalar type: {scalar_type!r}."

    if kind == "Struct":
        if not isinstance(value, dict) or set(value) != {"valueType", "fields"} or value.get("valueType") != "Struct":
            return "Struct values require exactly {valueType:'Struct', fields:{...}}."
        fields = value.get("fields")
        schema_fields = schema.get("fields")
        if not isinstance(fields, dict) or not isinstance(schema_fields, list):
            return "Struct fields or schema fields are invalid."
        field_schemas: dict[str, Any] = {}
        for item in schema_fields:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str) or "schema" not in item:
                return "Struct field schema is invalid."
            field_schemas[item["name"]] = item["schema"]
        if set(fields) != set(field_schemas):
            return "Struct value must contain every exported field exactly once."
        for field_name in sorted(field_schemas):
            error = _validate_structured_value_node(fields[field_name], field_schemas[field_name], depth + 1)
            if error:
                return f"Struct field {field_name}: {error}"
        return ""

    if kind in {"Array", "Set"}:
        field_name = "items"
        if not isinstance(value, dict) or set(value) != {"valueType", field_name} or value.get("valueType") != kind:
            return f"{kind} values require exactly {{valueType:'{kind}', items:[...]}}."
        items = value.get(field_name)
        if not isinstance(items, list) or len(items) > STRUCTURED_MAX_CONTAINER_ENTRIES:
            return f"{kind} items are invalid or exceed the entry limit."
        element_schema = schema.get("element")
        previous: bytes = b""
        for index, item in enumerate(items):
            error = _validate_structured_value_node(item, element_schema, depth + 1)
            if error:
                return f"{kind} item {index}: {error}"
            if kind == "Set":
                current = _canonical_sort_key(item)
                if previous and current <= previous:
                    return "Set items must be unique and sorted by Canonical JSON."
                previous = current
        return ""

    if kind == "Map":
        if not isinstance(value, dict) or set(value) != {"valueType", "entries"} or value.get("valueType") != "Map":
            return "Map values require exactly {valueType:'Map', entries:[{key,value}, ...]}."
        entries = value.get("entries")
        if not isinstance(entries, list) or len(entries) > STRUCTURED_MAX_CONTAINER_ENTRIES:
            return "Map entries are invalid or exceed the entry limit."
        key_schema = schema.get("key")
        value_schema = schema.get("value")
        previous_key: bytes = b""
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or set(entry) != {"key", "value"}:
                return f"Map entry {index} must contain exactly key and value."
            error = _validate_structured_value_node(entry["key"], key_schema, depth + 1)
            if error:
                return f"Map key {index}: {error}"
            error = _validate_structured_value_node(entry["value"], value_schema, depth + 1)
            if error:
                return f"Map value {index}: {error}"
            current_key = _canonical_sort_key(entry["key"])
            if previous_key and current_key <= previous_key:
                return "Map entries must have unique keys sorted by Canonical JSON."
            previous_key = current_key
        return ""

    return f"Unsupported structured schema kind: {kind!r}."


def _validate_asset_structured_value(value: Any, schema: Any, max_value_bytes: int) -> tuple[bool, str]:
    try:
        encoded = _canonical_json(value).encode("utf-8")
    except (TypeError, ValueError):
        return False, "Structured value is not valid finite JSON."
    if len(encoded) > max_value_bytes:
        return False, "Structured value exceeds maxValueBytes."
    error = _validate_structured_value_node(value, schema)
    return not error, error


def _is_blueprint_class(asset_class: str) -> bool:
    return asset_class.rsplit(".", 1)[-1].endswith("Blueprint")


def _validate_scalar_value(value: Any, max_value_bytes: int) -> bool:
    if isinstance(value, (dict, list)):
        return False
    if isinstance(value, float) and not math.isfinite(value):
        return False
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return len(encoded) <= max_value_bytes


def _validate_vector_value(value: Any, max_value_bytes: int) -> bool:
    if not isinstance(value, dict) or set(value) != {"r", "g", "b", "a"}:
        return False
    for component in ("r", "g", "b", "a"):
        component_value = value[component]
        if (
            isinstance(component_value, bool)
            or not isinstance(component_value, (int, float))
            or not math.isfinite(float(component_value))
        ):
            return False
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(encoded) <= max_value_bytes


def _validate_data_table_field_map(value: Any, max_value_bytes: int) -> bool:
    if not isinstance(value, dict) or not 1 <= len(value) <= 32:
        return False
    for field_name, field_value in value.items():
        if (
            not _is_nonempty_text(field_name, max_length=256)
            or "." in field_name
            or field_value is None
            or not _validate_scalar_value(field_value, max_value_bytes)
        ):
            return False
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return len(encoded) <= max_value_bytes


def _load_export_snapshot(
    export_root: Path,
    errors: list[dict[str, str]],
) -> tuple[str, dict[str, dict[str, Any]]]:
    root = export_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    manifest = _load_json_object(root / "manifest.json", max_bytes=MAX_CONTROL_FILE_BYTES)
    project_name = manifest.get("projectName")
    if not _is_nonempty_text(project_name, max_length=512):
        _issue(errors, "export-project", "Export manifest has no valid projectName.", "export.manifest.projectName")
        project_name = ""
    failure_count = manifest.get("failureCount", 0)
    if not _is_int(failure_count) or failure_count != 0:
        _issue(
            errors,
            "export-incomplete",
            "Revision validation requires an export with failureCount equal to zero.",
            "export.manifest.failureCount",
        )

    canonical_root = root / "canonical"
    if not canonical_root.is_dir():
        raise FileNotFoundError(canonical_root)

    assets: dict[str, dict[str, Any]] = {}
    for canonical_path in sorted(canonical_root.rglob("*.json")):
        canonical = _load_json_object(canonical_path, max_bytes=MAX_CANONICAL_FILE_BYTES)
        asset_path = canonical.get("assetPath")
        if not isinstance(asset_path, str) or not asset_path:
            _issue(
                errors,
                "export-asset-path",
                "Canonical asset has no valid assetPath.",
                f"export.{canonical_path.relative_to(root).as_posix()}.assetPath",
            )
            continue
        if asset_path in assets:
            _issue(
                errors,
                "export-duplicate-asset",
                f"Export contains duplicate assetPath: {asset_path}",
                f"export.assets[{asset_path}]",
            )
            continue
        canonical_pointer = f"export.{canonical_path.relative_to(root).as_posix()}"
        canonical_project_name = canonical.get("projectName")
        if not _is_nonempty_text(canonical_project_name, max_length=512):
            _issue(
                errors,
                "export-asset-project",
                "Canonical asset has no valid projectName.",
                f"{canonical_pointer}.projectName",
            )
            canonical_project_name = ""
        elif project_name and canonical_project_name != project_name:
            _issue(
                errors,
                "export-project-mismatch",
                (
                    f"Canonical projectName {canonical_project_name!r} does not match "
                    f"manifest projectName {project_name!r}."
                ),
                f"{canonical_pointer}.projectName",
            )

        asset_class = canonical.get("assetClass")
        if not _is_nonempty_text(asset_class, max_length=512):
            _issue(
                errors,
                "export-asset-class",
                "Canonical asset has no valid assetClass.",
                f"{canonical_pointer}.assetClass",
            )
            asset_class = ""

        revision = canonical.get("revision")
        if not isinstance(revision, dict):
            _issue(
                errors,
                "export-revision",
                "Canonical asset revision must be an object.",
                f"{canonical_pointer}.revision",
            )
            revision = {}
        revision_available_value = revision.get("available")
        if not isinstance(revision_available_value, bool):
            _issue(
                errors,
                "export-revision-available",
                "revision.available must be a boolean.",
                f"{canonical_pointer}.revision.available",
            )
        revision_value = revision.get("value", "")
        if revision_available_value is True and (
            not isinstance(revision_value, str) or REVISION_PATTERN.fullmatch(revision_value) is None
        ):
            _issue(
                errors,
                "export-revision-value",
                "Available revisions must use sha256:<64 lowercase hex> format.",
                f"{canonical_pointer}.revision.value",
            )
            revision_value = ""
        elif not isinstance(revision_value, str):
            _issue(
                errors,
                "export-revision-value",
                "revision.value must be a string.",
                f"{canonical_pointer}.revision.value",
            )
            revision_value = ""
        asset_details = canonical.get("assetDetails")
        asset_details_type = ""
        row_struct_path = ""
        row_names: list[str] = []
        data_asset_properties: dict[str, dict[str, Any]] = {}
        if isinstance(asset_details, dict):
            asset_details_type_value = asset_details.get("type", "")
            if isinstance(asset_details_type_value, str):
                asset_details_type = asset_details_type_value
            row_struct_value = asset_details.get("rowStructPath", "")
            if isinstance(row_struct_value, str):
                row_struct_path = row_struct_value
            row_names_value = asset_details.get("rowNames", [])
            if isinstance(row_names_value, list):
                row_names = [item for item in row_names_value if isinstance(item, str)]
            property_values = asset_details.get("properties", [])
            if isinstance(property_values, list):
                for property_value in property_values:
                    if not isinstance(property_value, dict):
                        continue
                    property_name = property_value.get("name")
                    if isinstance(property_name, str):
                        data_asset_properties[property_name] = property_value

        package_dirty_value = revision.get("packageDirty")
        if not isinstance(package_dirty_value, bool):
            _issue(
                errors,
                "export-package-dirty",
                "revision.packageDirty must be a boolean.",
                f"{canonical_pointer}.revision.packageDirty",
            )

        assets[asset_path] = {
            "assetPath": asset_path,
            "assetClass": asset_class,
            "projectName": canonical_project_name,
            "revisionAvailable": revision_available_value is True,
            "revision": revision_value,
            "packageDirty": package_dirty_value is True,
            "canonicalPath": canonical_path.relative_to(root).as_posix(),
            "assetDetailsType": asset_details_type,
            "dataAssetProperties": data_asset_properties,
            "rowStructPath": row_struct_path,
            "rowNames": row_names,
        }
    return str(project_name), assets


def _validate_operation_target(
    spec: OperationSpec,
    target: Any,
    *,
    path: str,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    if not isinstance(target, dict):
        _issue(errors, "operation-target-type", "target must be an object.", path)
        return {}
    allowed_fields = set(spec.target_fields)
    _check_unknown_fields(target, allowed_fields, path=path, errors=errors)
    _require_fields(target, allowed_fields, path=path, errors=errors)
    normalized: dict[str, Any] = {}
    for field in spec.target_fields:
        value = target.get(field)
        validator = spec.target_validators[field]
        if not validator(value):
            _issue(
                errors,
                "operation-target-value",
                f"Invalid target field for {spec.name}: {field}",
                f"{path}.{field}",
            )
        elif isinstance(value, str):
            normalized[field] = value
    return normalized


_STRUCTURAL_DATA_TABLE_OPERATIONS = {
    "addDataTableRow",
    "removeDataTableRow",
    "renameDataTableRow",
}


def _transaction_target_keys(
    operation_name: Any,
    target: dict[str, Any],
    value: Any,
) -> list[str]:
    if operation_name == "setVariableDefault":
        return [f"blueprint-variable:{target.get('variableName', '')}"]
    if operation_name == "setComponentProperty":
        return [
            f"blueprint-component:{target.get('componentName', '')}:"
            f"{target.get('propertyPath', '')}"
        ]
    if operation_name == "setPinDefault":
        return [
            f"blueprint-pin:{target.get('graphGuid', '')}:"
            f"{target.get('nodeGuid', '')}:{target.get('pinName', '')}"
        ]
    if operation_name == "setBlueprintDescription":
        return ["blueprint-description"]
    if operation_name in {
        "setAssetProperty",
        "setAssetReferenceProperty",
        "setAssetStructuredProperty",
    }:
        return [f"asset-property:{target.get('propertyPath', '')}"]
    material_type = {
        "setMaterialInstanceScalarParameter": "Scalar",
        "setMaterialInstanceVectorParameter": "Vector",
        "setMaterialInstanceTextureParameter": "Texture",
        "setMaterialInstanceStaticSwitchParameter": "StaticSwitch",
    }.get(operation_name)
    if material_type is not None:
        return [f"material:{material_type}:{target.get('parameterName', '')}"]
    if operation_name == "setDataTableCell":
        return [f"data-table:{target.get('rowName', '')}:{target.get('fieldName', '')}"]
    if operation_name == "setDataTableRowFields" and isinstance(value, dict):
        return [
            f"data-table:{target.get('rowName', '')}:{field_name}"
            for field_name in sorted(value)
        ]
    return []


def validate_patch(
    patch_path: Path,
    policy_path: Path,
    export_root: Path,
) -> dict[str, Any]:
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    policy_source = _load_json_object(policy_path, max_bytes=MAX_CONTROL_FILE_BYTES)
    patch = _load_json_object(patch_path, max_bytes=MAX_CONTROL_FILE_BYTES)
    policy = _validate_policy(policy_source, errors)
    export_project_name, export_assets = _load_export_snapshot(export_root, errors)

    _check_unknown_fields(patch, PATCH_FIELDS, path="patch", errors=errors)
    _require_fields(
        patch,
        {"schemaVersion", "patchId", "projectName", "assets"},
        path="patch",
        errors=errors,
    )

    if patch.get("schemaVersion") != PATCH_SCHEMA_VERSION:
        _issue(
            errors,
            "patch-schema-version",
            f"Patch schemaVersion must be {PATCH_SCHEMA_VERSION}.",
            "patch.schemaVersion",
        )

    patch_id = patch.get("patchId")
    if not _is_nonempty_text(patch_id, max_length=128):
        _issue(errors, "patch-id", "patchId must be a non-empty string up to 128 characters.", "patch.patchId")
        patch_id = ""

    project_name = patch.get("projectName")
    if not _is_nonempty_text(project_name, max_length=512):
        _issue(errors, "patch-project", "projectName must be a non-empty string.", "patch.projectName")
        project_name = ""
    if project_name and project_name not in policy["allowedProjectNames"]:
        _issue(
            errors,
            "project-not-allowed",
            f"Project is not authorized by policy: {project_name}",
            "patch.projectName",
        )
    if project_name and export_project_name and project_name != export_project_name:
        _issue(
            errors,
            "project-mismatch",
            f"Patch projectName {project_name!r} does not match export projectName {export_project_name!r}.",
            "patch.projectName",
        )

    description = patch.get("description", "")
    if not isinstance(description, str) or len(description) > 4096:
        _issue(errors, "patch-description", "description must be a string up to 4096 characters.", "patch.description")
        description = ""

    assets_value = patch.get("assets")
    if not isinstance(assets_value, list) or not assets_value:
        _issue(errors, "patch-assets", "assets must be a non-empty array.", "patch.assets")
        assets_value = []
    elif len(assets_value) > policy["maxAssetsPerPatch"]:
        _issue(
            errors,
            "patch-asset-limit",
            f"Patch contains {len(assets_value)} assets; policy limit is {policy['maxAssetsPerPatch']}.",
            "patch.assets",
        )

    seen_asset_paths: set[str] = set()
    seen_operation_ids: set[str] = set()
    asset_results: list[dict[str, Any]] = []
    operation_count = 0

    for asset_index, asset_value in enumerate(assets_value):
        asset_path_pointer = f"patch.assets[{asset_index}]"
        asset_error_start = len(errors)
        if not isinstance(asset_value, dict):
            _issue(errors, "asset-type", "Asset entry must be an object.", asset_path_pointer)
            asset_results.append({"index": asset_index, "valid": False, "operations": []})
            continue

        _check_unknown_fields(asset_value, ASSET_FIELDS, path=asset_path_pointer, errors=errors)
        _require_fields(
            asset_value,
            {"assetPath", "expectedRevision", "operations"},
            path=asset_path_pointer,
            errors=errors,
        )

        asset_path = asset_value.get("assetPath")
        asset_path_valid, package_or_error = _validate_asset_path(asset_path)
        if not asset_path_valid:
            _issue(errors, "asset-path", package_or_error, f"{asset_path_pointer}.assetPath")
            asset_path = asset_path if isinstance(asset_path, str) else ""
            package_name = ""
        else:
            assert isinstance(asset_path, str)
            package_name = package_or_error
            if not _path_is_allowed(package_name, policy["allowedAssetRoots"]):
                _issue(
                    errors,
                    "asset-root-not-allowed",
                    f"Asset is outside allowed roots: {asset_path}",
                    f"{asset_path_pointer}.assetPath",
                )
            if asset_path in seen_asset_paths:
                _issue(
                    errors,
                    "duplicate-asset",
                    f"Patch contains duplicate assetPath: {asset_path}",
                    f"{asset_path_pointer}.assetPath",
                )
            seen_asset_paths.add(asset_path)

        expected_revision = asset_value.get("expectedRevision")
        if policy["requireRevision"] and (
            not isinstance(expected_revision, str) or REVISION_PATTERN.fullmatch(expected_revision) is None
        ):
            _issue(
                errors,
                "revision-format",
                "expectedRevision must use sha256:<64 lowercase hex> format.",
                f"{asset_path_pointer}.expectedRevision",
            )
        expected_revision = expected_revision if isinstance(expected_revision, str) else ""

        expected_asset_class = asset_value.get("expectedAssetClass", "")
        if expected_asset_class and (
            not isinstance(expected_asset_class, str)
            or not expected_asset_class.startswith("/Script/")
            or "." not in expected_asset_class
        ):
            _issue(
                errors,
                "expected-asset-class",
                "expectedAssetClass must use /Script/Module.Class form.",
                f"{asset_path_pointer}.expectedAssetClass",
            )
            expected_asset_class = ""

        current = export_assets.get(asset_path) if asset_path else None
        if current is None:
            _issue(
                errors,
                "asset-not-found",
                f"Asset is not present in the revision export: {asset_path}",
                f"{asset_path_pointer}.assetPath",
            )
            current = {
                "assetClass": "",
                "revisionAvailable": False,
                "revision": "",
                "packageDirty": False,
                "canonicalPath": "",
            }

        asset_class = current["assetClass"] if isinstance(current.get("assetClass"), str) else ""
        if expected_asset_class and asset_class and expected_asset_class != asset_class:
            _issue(
                errors,
                "asset-class-mismatch",
                f"Expected {expected_asset_class}, export reports {asset_class}.",
                f"{asset_path_pointer}.expectedAssetClass",
            )
        if asset_class not in policy["allowedAssetClasses"]:
            _issue(
                errors,
                "asset-class-not-allowed",
                f"Asset class is not authorized by policy: {asset_class or '<missing>'}",
                f"{asset_path_pointer}.assetPath",
            )
        current_revision_value = current.get("revision", "")
        current_revision = current_revision_value if isinstance(current_revision_value, str) else ""
        revision_available = current.get("revisionAvailable") is True
        revision_match = bool(expected_revision) and revision_available and expected_revision == current_revision
        if policy["requireRevision"] and not revision_available:
            _issue(
                errors,
                "revision-unavailable",
                "Current asset revision is unavailable.",
                f"{asset_path_pointer}.expectedRevision",
            )
        elif policy["requireRevision"] and expected_revision and expected_revision != current_revision:
            _issue(
                errors,
                "revision-conflict",
                f"Expected {expected_revision}, current revision is {current_revision or '<missing>'}.",
                f"{asset_path_pointer}.expectedRevision",
            )
        if policy["rejectDirtyPackages"] and current.get("packageDirty") is True:
            _issue(
                errors,
                "dirty-package",
                "Asset package was dirty when the revision export was created.",
                f"{asset_path_pointer}.expectedRevision",
            )

        operations_value = asset_value.get("operations")
        if not isinstance(operations_value, list) or not operations_value:
            _issue(
                errors,
                "asset-operations",
                "operations must be a non-empty array.",
                f"{asset_path_pointer}.operations",
            )
            operations_value = []
        elif len(operations_value) > policy["maxOperationsPerAsset"]:
            _issue(
                errors,
                "operation-limit",
                (
                    f"Asset contains {len(operations_value)} operations; "
                    f"policy limit is {policy['maxOperationsPerAsset']}."
                ),
                f"{asset_path_pointer}.operations",
            )

        operation_results: list[dict[str, Any]] = []
        multi_operation = len(operations_value) > 1
        transaction_targets: set[str] = set()
        asset_pre_operation_errors = len(errors) - asset_error_start
        for operation_index, operation_value in enumerate(operations_value):
            operation_count += 1
            operation_pointer = f"{asset_path_pointer}.operations[{operation_index}]"
            operation_error_start = len(errors)
            if not isinstance(operation_value, dict):
                _issue(errors, "operation-type", "Operation entry must be an object.", operation_pointer)
                operation_results.append({"index": operation_index, "valid": False})
                continue

            _check_unknown_fields(operation_value, OPERATION_FIELDS, path=operation_pointer, errors=errors)
            _require_fields(operation_value, OPERATION_FIELDS, path=operation_pointer, errors=errors)

            operation_id = operation_value.get("operationId")
            if not _is_nonempty_text(operation_id, max_length=128):
                _issue(
                    errors,
                    "operation-id",
                    "operationId must be a non-empty string up to 128 characters.",
                    f"{operation_pointer}.operationId",
                )
                operation_id = ""
            elif operation_id in seen_operation_ids:
                _issue(
                    errors,
                    "duplicate-operation-id",
                    f"Duplicate operationId: {operation_id}",
                    f"{operation_pointer}.operationId",
                )
            else:
                seen_operation_ids.add(operation_id)

            operation_name = operation_value.get("operation")
            spec = OPERATION_REGISTRY.get(operation_name) if isinstance(operation_name, str) else None
            if spec is None:
                _issue(
                    errors,
                    "unknown-operation",
                    f"Unknown operation: {operation_name!r}",
                    f"{operation_pointer}.operation",
                )
                target = {}
                risk = "unknown"
                expected_change = ""
            else:
                if asset_class:
                    is_blueprint = _is_blueprint_class(asset_class)
                    if spec.asset_type == "Blueprint" and not is_blueprint:
                        _issue(
                            errors,
                            "operation-asset-type",
                            f"Operation {operation_name} requires a Blueprint asset, not {asset_class}.",
                            f"{operation_pointer}.operation",
                        )
                    elif spec.asset_type == "NonBlueprint" and is_blueprint:
                        _issue(
                            errors,
                            "operation-asset-type",
                            f"Operation {operation_name} requires a non-Blueprint asset, not {asset_class}.",
                            f"{operation_pointer}.operation",
                        )
                target = _validate_operation_target(
                    spec,
                    operation_value.get("target"),
                    path=f"{operation_pointer}.target",
                    errors=errors,
                )
                if operation_name in {"setAssetProperty", "setAssetReferenceProperty", "setAssetStructuredProperty"}:
                    property_path = target.get("propertyPath")
                    authorization = (
                        f"{asset_class}#{property_path}" if isinstance(property_path, str) else ""
                    )
                    if authorization not in policy["allowedAssetProperties"]:
                        _issue(
                            errors,
                            "asset-property-not-allowed",
                            f"Asset property is not authorized by policy: {authorization or '<invalid>'}",
                            f"{operation_pointer}.target.propertyPath",
                        )
                    if operation_name == "setAssetReferenceProperty":
                        if current.get("assetDetailsType") != "data-asset":
                            _issue(
                                errors,
                                "operation-asset-type",
                                "setAssetReferenceProperty requires a Data Asset reader snapshot.",
                                f"{operation_pointer}.operation",
                            )
                        properties = current.get("dataAssetProperties", {})
                        property_details = properties.get(property_path) if isinstance(properties, dict) else None
                        if not isinstance(property_details, dict):
                            _issue(
                                errors,
                                "asset-reference-property-missing",
                                f"Data Asset reference property was not exported: {property_path}",
                                f"{operation_pointer}.target.propertyPath",
                            )
                        else:
                            reference_type = property_details.get("referenceType")
                            if reference_type not in {"Object", "Class", "SoftObject", "SoftClass"}:
                                _issue(
                                    errors,
                                    "asset-reference-property-type",
                                    f"Property is not a supported reference type: {property_path}",
                                    f"{operation_pointer}.target.propertyPath",
                                )
                    elif operation_name == "setAssetStructuredProperty":
                        if current.get("assetDetailsType") != "data-asset":
                            _issue(
                                errors,
                                "operation-asset-type",
                                "setAssetStructuredProperty requires a Data Asset reader snapshot.",
                                f"{operation_pointer}.operation",
                            )
                        properties = current.get("dataAssetProperties", {})
                        property_details = properties.get(property_path) if isinstance(properties, dict) else None
                        if not isinstance(property_details, dict):
                            _issue(
                                errors,
                                "asset-structured-property-missing",
                                f"Data Asset structured property was not exported: {property_path}",
                                f"{operation_pointer}.target.propertyPath",
                            )
                        elif property_details.get("structuredType") not in {"Struct", "Array", "Set", "Map"}:
                            _issue(
                                errors,
                                "asset-structured-property-type",
                                f"Property is not Struct, Array, Set, or Map: {property_path}",
                                f"{operation_pointer}.target.propertyPath",
                            )
                        elif property_details.get("structuredSupported") is not True or not isinstance(
                            property_details.get("structuredSchema"), dict
                        ):
                            _issue(
                                errors,
                                "asset-structured-property-unsupported",
                                f"Property contains unsupported structured leaves: {property_path}",
                                f"{operation_pointer}.target.propertyPath",
                            )
                elif operation_name in {
                    "setDataTableCell",
                    "setDataTableRowFields",
                    "addDataTableRow",
                    "removeDataTableRow",
                    "renameDataTableRow",
                }:
                    if asset_class != "/Script/Engine.DataTable":
                        _issue(
                            errors,
                            "operation-asset-type",
                            f"{operation_name} requires a DataTable asset.",
                            f"{operation_pointer}.operation",
                        )
                    row_struct_path_value = current.get("rowStructPath", "")
                    row_struct_path = (
                        row_struct_path_value
                        if isinstance(row_struct_path_value, str)
                        else ""
                    )
                    row_names_value = current.get("rowNames", [])
                    row_names = {
                        item for item in row_names_value if isinstance(item, str)
                    } if isinstance(row_names_value, list) else set()
                    row_name = target.get("rowName")
                    if operation_name == "addDataTableRow":
                        if isinstance(row_name, str) and row_name in row_names:
                            _issue(
                                errors,
                                "data-table-row-exists",
                                f"DataTable row already exists: {row_name}",
                                f"{operation_pointer}.target.rowName",
                            )
                    elif operation_name in {"removeDataTableRow", "renameDataTableRow"}:
                        if isinstance(row_name, str) and row_name not in row_names:
                            _issue(
                                errors,
                                "data-table-row-missing",
                                f"DataTable row does not exist: {row_name}",
                                f"{operation_pointer}.target.rowName",
                            )
                    if operation_name == "renameDataTableRow":
                        new_row_name = target.get("newRowName")
                        if new_row_name == row_name:
                            _issue(
                                errors,
                                "data-table-row-name-unchanged",
                                "DataTable source and destination row names must differ.",
                                f"{operation_pointer}.target.newRowName",
                            )
                        elif isinstance(new_row_name, str) and new_row_name in row_names:
                            _issue(
                                errors,
                                "data-table-row-exists",
                                f"DataTable destination row already exists: {new_row_name}",
                                f"{operation_pointer}.target.newRowName",
                            )
                    if operation_name == "setDataTableCell":
                        field_names = [target.get("fieldName")]
                        field_paths = [f"{operation_pointer}.target.fieldName"]
                    elif operation_name in {"setDataTableRowFields", "addDataTableRow"}:
                        row_fields_value = operation_value.get("value")
                        field_names = sorted(row_fields_value) if isinstance(row_fields_value, dict) else []
                        field_paths = [f"{operation_pointer}.value.{field_name}" for field_name in field_names]
                    else:
                        field_names = []
                        field_paths = []
                    for field_name, field_path in zip(field_names, field_paths, strict=True):
                        authorization = (
                            f"{asset_class}#{row_struct_path}#{field_name}"
                            if isinstance(field_name, str) and row_struct_path
                            else ""
                        )
                        if authorization not in policy["allowedDataTableFields"]:
                            _issue(
                                errors,
                                "data-table-field-not-allowed",
                                f"DataTable field is not authorized by policy: {authorization or '<invalid>'}",
                                field_path,
                            )
                elif operation_name in {
                    "setMaterialInstanceScalarParameter",
                    "setMaterialInstanceVectorParameter",
                    "setMaterialInstanceTextureParameter",
                    "setMaterialInstanceStaticSwitchParameter",
                }:
                    if asset_class != "/Script/Engine.MaterialInstanceConstant":
                        _issue(
                            errors,
                            "operation-asset-type",
                            "Material parameters require MaterialInstanceConstant.",
                            f"{operation_pointer}.operation",
                        )
                    parameter_name = target.get("parameterName")
                    parameter_type = {
                        "setMaterialInstanceScalarParameter": "Scalar",
                        "setMaterialInstanceVectorParameter": "Vector",
                        "setMaterialInstanceTextureParameter": "Texture",
                        "setMaterialInstanceStaticSwitchParameter": "StaticSwitch",
                    }[operation_name]
                    authorization = (
                        f"{asset_class}#{parameter_type}#{parameter_name}"
                        if isinstance(parameter_name, str)
                        else ""
                    )
                    if authorization not in policy["allowedMaterialParameters"]:
                        _issue(
                            errors,
                            "material-parameter-not-allowed",
                            f"Material parameter is not authorized by policy: {authorization or '<invalid>'}",
                            f"{operation_pointer}.target.parameterName",
                        )
                risk = spec.risk
                expected_change = spec.expected_change
                if operation_name not in policy["allowedOperations"]:
                    _issue(
                        errors,
                        "operation-not-allowed",
                        f"Operation is not authorized by policy: {operation_name}",
                        f"{operation_pointer}.operation",
                    )

            value = operation_value.get("value")
            if operation_name == "setMaterialInstanceVectorParameter":
                if not _validate_vector_value(value, policy["maxValueBytes"]):
                    _issue(
                        errors,
                        "operation-value-type",
                        "Material vector parameters require a finite {r,g,b,a} JSON object.",
                        f"{operation_pointer}.value",
                    )
            elif operation_name == "setMaterialInstanceStaticSwitchParameter":
                if not isinstance(value, bool):
                    _issue(
                        errors,
                        "operation-value-type",
                        "Material static switch parameters require a JSON boolean.",
                        f"{operation_pointer}.value",
                    )
            elif operation_name == "setDataTableCell":
                if value is None or not _validate_scalar_value(value, policy["maxValueBytes"]):
                    _issue(
                        errors,
                        "operation-value-type",
                        "DataTable cells require a finite non-null JSON scalar.",
                        f"{operation_pointer}.value",
                    )
            elif operation_name == "setDataTableRowFields":
                if not _validate_data_table_field_map(value, policy["maxValueBytes"]):
                    _issue(
                        errors,
                        "operation-value-type",
                        "DataTable row fields require 1-32 unique top-level fields with finite non-null JSON scalar values.",
                        f"{operation_pointer}.value",
                    )
            elif operation_name == "addDataTableRow":
                if not isinstance(value, dict) or len(value) > 32 or any(
                    not _is_nonempty_text(field_name, max_length=256)
                    or "." in field_name
                    or field_value is None
                    or not _validate_scalar_value(field_value, policy["maxValueBytes"])
                    for field_name, field_value in value.items()
                ):
                    _issue(
                        errors,
                        "operation-value-type",
                        "DataTable row creation requires an object containing at most 32 authorized scalar fields.",
                        f"{operation_pointer}.value",
                    )
            elif operation_name in {"removeDataTableRow", "renameDataTableRow"}:
                if value is not True:
                    _issue(
                        errors,
                        "operation-value-type",
                        f"{operation_name} requires value=true as an explicit structural-change acknowledgement.",
                        f"{operation_pointer}.value",
                    )
            elif operation_name == "setAssetReferenceProperty":
                valid_reference, reference_package, requested_reference_type = _validate_asset_reference_value(
                    value,
                    policy["maxValueBytes"],
                )
                if not valid_reference:
                    _issue(
                        errors,
                        "operation-value-type",
                        reference_package,
                        f"{operation_pointer}.value",
                    )
                else:
                    property_path = target.get("propertyPath")
                    properties = current.get("dataAssetProperties", {})
                    property_details = properties.get(property_path) if isinstance(properties, dict) else None
                    expected_reference_type = (
                        property_details.get("referenceType") if isinstance(property_details, dict) else ""
                    )
                    if value is not None and requested_reference_type != expected_reference_type:
                        _issue(
                            errors,
                            "asset-reference-type-mismatch",
                            f"Reference type {requested_reference_type} does not match property type {expected_reference_type}.",
                            f"{operation_pointer}.value.referenceType",
                        )
                    if value is not None and not _path_is_allowed(
                        reference_package,
                        policy["allowedReferenceRoots"],
                    ):
                        _issue(
                            errors,
                            "reference-not-allowed",
                            f"Referenced asset is outside allowedReferenceRoots: {value.get('path')}",
                            f"{operation_pointer}.value.path",
                        )
            elif operation_name == "setAssetStructuredProperty":
                property_path = target.get("propertyPath")
                properties = current.get("dataAssetProperties", {})
                property_details = properties.get(property_path) if isinstance(properties, dict) else None
                structured_schema = property_details.get("structuredSchema") if isinstance(property_details, dict) else None
                valid_structured, structured_error = _validate_asset_structured_value(
                    value,
                    structured_schema,
                    policy["maxValueBytes"],
                )
                if not valid_structured:
                    _issue(
                        errors,
                        "operation-value-type",
                        structured_error,
                        f"{operation_pointer}.value",
                    )
                elif isinstance(property_details, dict) and value.get("valueType") != property_details.get("structuredType"):
                    _issue(
                        errors,
                        "asset-structured-type-mismatch",
                        f"Structured value type {value.get('valueType')} does not match property type {property_details.get('structuredType')}.",
                        f"{operation_pointer}.value.valueType",
                    )
            elif operation_name == "setMaterialInstanceTextureParameter":
                valid_reference, reference_package = _validate_asset_path(value)
                if not valid_reference:
                    _issue(
                        errors,
                        "operation-value-type",
                        reference_package,
                        f"{operation_pointer}.value",
                    )
                elif not _path_is_allowed(reference_package, policy["allowedReferenceRoots"]):
                    _issue(
                        errors,
                        "reference-not-allowed",
                        f"Referenced asset is outside allowedReferenceRoots: {value}",
                        f"{operation_pointer}.value",
                    )
            elif not _validate_scalar_value(value, policy["maxValueBytes"]):
                _issue(
                    errors,
                    "operation-value",
                    "value must be a finite JSON scalar within the policy byte limit.",
                    f"{operation_pointer}.value",
                )
            elif operation_name == "setMaterialInstanceScalarParameter" and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                _issue(
                    errors,
                    "operation-value-type",
                    "Material scalar parameters require a finite JSON number.",
                    f"{operation_pointer}.value",
                )

            if multi_operation and spec is not None:
                if operation_name in _STRUCTURAL_DATA_TABLE_OPERATIONS:
                    _issue(
                        errors,
                        "transaction-operation-not-supported",
                        f"{operation_name} must run as a single-operation patch.",
                        f"{operation_pointer}.operation",
                    )
                else:
                    for target_key in _transaction_target_keys(operation_name, target, value):
                        if not target_key or target_key.endswith(":"):
                            continue
                        if target_key in transaction_targets:
                            _issue(
                                errors,
                                "duplicate-transaction-target",
                                f"Transaction target is modified more than once: {target_key}",
                                f"{operation_pointer}.target",
                            )
                        else:
                            transaction_targets.add(target_key)

            operation_valid = (
                asset_pre_operation_errors == 0
                and len(errors) == operation_error_start
            )
            operation_results.append(
                {
                    "operationId": operation_id,
                    "operation": operation_name if isinstance(operation_name, str) else "",
                    "risk": risk,
                    "valid": operation_valid,
                    "status": "validated" if operation_valid else "rejected",
                    "expectedChange": {
                        "kind": expected_change,
                        "target": target,
                        "value": value,
                    },
                }
            )

        asset_results.append(
            {
                "assetPath": asset_path,
                "assetClass": asset_class,
                "canonicalPath": current.get("canonicalPath", ""),
                "expectedRevision": expected_revision,
                "currentRevision": current_revision,
                "revisionAvailable": revision_available,
                "revisionMatch": revision_match,
                "packageDirty": current.get("packageDirty") is True,
                "authorizedRoot": bool(package_name) and _path_is_allowed(package_name, policy["allowedAssetRoots"]),
                "valid": len(errors) == asset_error_start,
                "operations": operation_results,
                "transaction": {
                    "kind": "single-asset-multi-operation" if multi_operation else "single-operation",
                    "atomic": multi_operation,
                    "operationCount": len(operations_value),
                },
            }
        )

    errors.sort(key=lambda item: (item["path"], item["code"], item["message"]))
    warnings.sort(key=lambda item: (item["path"], item["code"], item["message"]))
    valid = not errors
    return {
        "schemaVersion": PATCH_SCHEMA_VERSION,
        "patchId": patch_id,
        "projectName": project_name,
        "description": description,
        "valid": valid,
        "dryRun": True,
        "validationOnly": True,
        "willLoadOrModifyUObjects": False,
        "willWriteDisk": False,
        "commitSupported": True,
        "commitAllowedByPolicy": policy["commitEnabled"],
        "revisionSource": str(export_root.expanduser().resolve()),
        "policy": policy,
        "operationRegistry": get_operation_registry(),
        "summary": {
            "assets": len(assets_value),
            "operations": operation_count,
            "validatedAssets": sum(1 for item in asset_results if item["valid"]),
            "validatedOperations": sum(
                1
                for asset in asset_results
                for operation in asset.get("operations", [])
                if operation.get("valid") is True
            ),
            "errors": len(errors),
            "warnings": len(warnings),
        },
        "assets": asset_results,
        "errors": errors,
        "warnings": warnings,
    }

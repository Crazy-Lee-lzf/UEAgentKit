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
}


POLICY_FIELDS = {
    "schemaVersion",
    "validationEnabled",
    "commitEnabled",
    "allowedProjectNames",
    "allowedAssetRoots",
    "allowedOperations",
    "allowedAssetClasses",
    "allowedAssetProperties",
    "allowedMaterialParameters",
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
    required_fields = POLICY_FIELDS - {"allowedMaterialParameters"}
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
    if "setAssetProperty" in normalized_operations and not normalized_asset_properties:
        _issue(
            errors,
            "policy-asset-properties",
            "setAssetProperty requires at least one allowedAssetProperties entry.",
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
                or parameter_type not in {"Scalar", "Vector"}
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
    }
    if material_operations.intersection(normalized_operations) and not normalized_material_parameters:
        _issue(
            errors,
            "policy-material-parameters",
            "Material Instance parameter operations require allowedMaterialParameters authorization.",
            "policy.allowedMaterialParameters",
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
        "maxOperationsPerAsset": (1, 256),
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
        "allowedOperations": sorted(set(normalized_operations)),
        "allowedAssetClasses": sorted(set(allowed_asset_classes)) if isinstance(allowed_asset_classes, list) else [],
        "allowedAssetProperties": sorted(set(normalized_asset_properties)),
        "allowedMaterialParameters": sorted(set(normalized_material_parameters)),
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
                if operation_name == "setAssetProperty":
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
                elif operation_name in {
                    "setMaterialInstanceScalarParameter",
                    "setMaterialInstanceVectorParameter",
                }:
                    if asset_class != "/Script/Engine.MaterialInstanceConstant":
                        _issue(
                            errors,
                            "operation-asset-type",
                            "Material parameters require MaterialInstanceConstant.",
                            f"{operation_pointer}.operation",
                        )
                    parameter_name = target.get("parameterName")
                    parameter_type = (
                        "Scalar"
                        if operation_name == "setMaterialInstanceScalarParameter"
                        else "Vector"
                    )
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

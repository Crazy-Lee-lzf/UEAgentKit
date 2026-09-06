from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

FIXTURE_PLAN_SCHEMA_VERSION = "1.0"
FIXTURE_TOOL_VERSION = "0.8.0"
_MAX_FIXTURES = 64
_PACKAGE_RE = re.compile(r"^/[A-Za-z0-9_][A-Za-z0-9_/-]*[A-Za-z0-9_]$")
_SCRIPT_CLASS_RE = re.compile(r"^/Script/[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_BLUEPRINT_TYPES = {"Normal", "FunctionLibrary", "MacroLibrary", "Interface"}
_MATERIAL_PARAMETER_TYPES = {"Scalar", "Vector", "Texture", "StaticSwitch"}


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} does not exist: {resolved}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object: {resolved}")
    return value


def _issue(errors: list[dict[str, str]], code: str, message: str, path: str) -> None:
    errors.append({"code": code, "message": message, "path": path})


def _is_package_path(value: Any, *, allow_object_path: bool = False) -> bool:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value or ".." in value:
        return False
    package = value.split(".", 1)[0] if allow_object_path else value
    if not allow_object_path and "." in value:
        return False
    return bool(_PACKAGE_RE.fullmatch(package)) and "//" not in package


def _under_root(target: str, root: str) -> bool:
    return target.startswith(f"{root}/")


def validate_fixture_plan(plan_path: Path) -> dict[str, Any]:
    resolved = plan_path.expanduser().resolve()
    plan = _read_json_object(resolved, label="Fixture plan")
    plan_revision = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()
    errors: list[dict[str, str]] = []
    allowed_root_fields = {"schemaVersion", "root", "fixtures"}
    unknown_root_fields = sorted(set(plan) - allowed_root_fields)
    for field in unknown_root_fields:
        _issue(errors, "unknown-field", f"Unknown fixture-plan field: {field}", f"plan.{field}")

    if plan.get("schemaVersion") != FIXTURE_PLAN_SCHEMA_VERSION:
        _issue(errors, "schema-version", "Unsupported fixture plan schemaVersion.", "plan.schemaVersion")
    root = plan.get("root")
    if (
        not isinstance(root, str)
        or not root.startswith("/Game/")
        or root.endswith("/")
        or not _is_package_path(root)
    ):
        _issue(
            errors,
            "root-invalid",
            "root must be a specific valid package directory below /Game.",
            "plan.root",
        )
        root = ""

    fixtures = plan.get("fixtures")
    if not isinstance(fixtures, list) or not 1 <= len(fixtures) <= _MAX_FIXTURES:
        _issue(
            errors,
            "fixtures-invalid",
            f"fixtures must contain 1-{_MAX_FIXTURES} entries.",
            "plan.fixtures",
        )
        fixtures = []

    ids: set[str] = set()
    targets: set[str] = set()
    sources: set[str] = set()
    material_parents: dict[str, dict[str, list[str]]] = {}
    normalized: list[dict[str, Any]] = []
    for index, fixture in enumerate(fixtures):
        base = f"plan.fixtures[{index}]"
        if not isinstance(fixture, dict):
            _issue(errors, "fixture-object", "Fixture entry must be an object.", base)
            continue
        kind = fixture.get("kind")
        if kind == "duplicateAsset":
            required = {"id", "kind", "sourceAsset", "targetAsset", "expectedClass"}
            allowed = required
        elif kind == "referenceAsset":
            required = {"id", "kind", "targetAsset", "expectedClass"}
            allowed = required | {"values"}
        elif kind in {"scalarAsset", "structuredAsset"}:
            required = {"id", "kind", "targetAsset", "expectedClass"}
            allowed = required
        elif kind == "materialParentAsset":
            required = {"id", "kind", "targetAsset", "expectedClass", "parameters"}
            allowed = required
        elif kind == "materialAsset":
            required = {"id", "kind", "targetAsset", "expectedClass", "parentAsset"}
            allowed = required | {"values"}
        elif kind == "dataTableAsset":
            required = {"id", "kind", "targetAsset", "expectedClass", "rowStruct", "rows"}
            allowed = required
        elif kind == "blueprint":
            required = {
                "id",
                "kind",
                "targetAsset",
                "expectedClass",
                "parentClass",
                "blueprintType",
            }
            allowed = required
        else:
            required = {"id", "kind", "targetAsset", "expectedClass"}
            allowed = required
            _issue(errors, "fixture-kind", "kind must be duplicateAsset, scalarAsset, referenceAsset, structuredAsset, materialParentAsset, materialAsset, dataTableAsset, or blueprint.", f"{base}.kind")
        missing = sorted(required - set(fixture))
        unknown = sorted(set(fixture) - allowed)
        for field in missing:
            _issue(errors, "required-field", f"Missing required field: {field}", f"{base}.{field}")
        for field in unknown:
            _issue(errors, "unknown-field", f"Unknown fixture field: {field}", f"{base}.{field}")

        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not fixture_id.strip() or len(fixture_id) > 128:
            _issue(errors, "fixture-id", "id must be a non-empty string of at most 128 characters.", f"{base}.id")
        elif fixture_id in ids:
            _issue(errors, "fixture-id-duplicate", f"Duplicate fixture id: {fixture_id}", f"{base}.id")
        else:
            ids.add(fixture_id)

        target = fixture.get("targetAsset")
        if not _is_package_path(target) or (root and not _under_root(target, root)):
            _issue(
                errors,
                "target-invalid",
                "targetAsset must be a package path strictly below root and must not contain an object suffix.",
                f"{base}.targetAsset",
            )
        elif target in targets:
            _issue(errors, "target-duplicate", f"Duplicate targetAsset: {target}", f"{base}.targetAsset")
        else:
            targets.add(target)

        expected_class = fixture.get("expectedClass")
        if not isinstance(expected_class, str) or not _SCRIPT_CLASS_RE.fullmatch(expected_class):
            _issue(
                errors,
                "expected-class",
                "expectedClass must use /Script/Module.Class form.",
                f"{base}.expectedClass",
            )

        if kind == "duplicateAsset":
            source = fixture.get("sourceAsset")
            if not _is_package_path(source, allow_object_path=True):
                _issue(errors, "source-invalid", "sourceAsset must be a valid asset package or object path.", f"{base}.sourceAsset")
            else:
                source_package = source.split(".", 1)[0]
                sources.add(source_package)
                if source_package == target:
                    _issue(errors, "source-target-equal", "sourceAsset and targetAsset must differ.", f"{base}.sourceAsset")
        elif kind == "scalarAsset":
            if expected_class != "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset":
                _issue(
                    errors,
                    "scalar-asset-class",
                    "scalarAsset fixtures require the UEAgentKit scalar fixture class.",
                    f"{base}.expectedClass",
                )
        elif kind == "referenceAsset":
            if expected_class != "/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset":
                _issue(
                    errors,
                    "reference-asset-class",
                    "referenceAsset fixtures require the UEAgentKit reference fixture class.",
                    f"{base}.expectedClass",
                )
            values = fixture.get("values")
            if values is not None:
                if not isinstance(values, dict):
                    _issue(
                        errors,
                        "reference-values",
                        "referenceAsset values must be an object.",
                        f"{base}.values",
                    )
                else:
                    reference_types = {
                        "ObjectValue": "Object",
                        "ClassValue": "Class",
                        "SoftObjectValue": "SoftObject",
                        "SoftClassValue": "SoftClass",
                    }
                    for property_name, value in values.items():
                        property_path = f"{base}.values.{property_name}"
                        expected_type = reference_types.get(property_name)
                        if expected_type is None:
                            _issue(
                                errors,
                                "reference-values-property",
                                "Unknown reference fixture property.",
                                property_path,
                            )
                            continue
                        if value is None:
                            continue
                        if not isinstance(value, dict):
                            _issue(
                                errors,
                                "reference-values-value",
                                "Reference fixture value must be null or an object.",
                                property_path,
                            )
                            continue
                        if set(value) != {"referenceType", "path"}:
                            _issue(
                                errors,
                                "reference-values-value",
                                "Reference fixture value must contain exactly referenceType and path.",
                                property_path,
                            )
                            continue
                        requested_type = value.get("referenceType")
                        requested_path = value.get("path")
                        if requested_type != expected_type:
                            _issue(
                                errors,
                                "reference-values-type",
                                f"Reference fixture type {requested_type} does not match property type {expected_type}.",
                                f"{property_path}.referenceType",
                            )
                        if not _is_package_path(requested_path, allow_object_path=True) or "." not in requested_path:
                            _issue(
                                errors,
                                "reference-values-path",
                                "Reference fixture path must be a valid object path.",
                                f"{property_path}.path",
                            )
        elif kind == "structuredAsset":
            if expected_class != "/Script/UEAgentKitEditor.UEAgentKitStructuredWriteFixtureAsset":
                _issue(
                    errors,
                    "structured-asset-class",
                    "structuredAsset fixtures require the UEAgentKit structured fixture class.",
                    f"{base}.expectedClass",
                )
        elif kind == "materialParentAsset":
            if expected_class != "/Script/Engine.Material":
                _issue(
                    errors,
                    "material-parent-class",
                    "materialParentAsset fixtures require expectedClass /Script/Engine.Material.",
                    f"{base}.expectedClass",
                )
            parameters = fixture.get("parameters")
            if not isinstance(parameters, dict):
                _issue(
                    errors,
                    "material-parent-parameters",
                    "materialParentAsset parameters must be an object.",
                    f"{base}.parameters",
                )
            else:
                material_parameter_names: dict[str, list[str]] = {}
                for parameter_type, names in parameters.items():
                    parameter_path = f"{base}.parameters.{parameter_type}"
                    if parameter_type not in _MATERIAL_PARAMETER_TYPES:
                        _issue(
                            errors,
                            "material-parent-parameter-type",
                            "materialParentAsset parameter types must be Scalar, Vector, Texture, or StaticSwitch.",
                            parameter_path,
                        )
                        continue
                    if not isinstance(names, list) or not names:
                        _issue(
                            errors,
                            "material-parent-parameter-type",
                            "materialParentAsset parameter type must contain at least one parameter name.",
                            parameter_path,
                        )
                        continue
                    seen: set[str] = set()
                    valid_names: list[str] = []
                    for name_index, name in enumerate(names):
                        name_path = f"{parameter_path}[{name_index}]"
                        if (
                            not isinstance(name, str)
                            or not name.strip()
                            or len(name) > 128
                            or "." in name
                            or "/" in name
                        ):
                            _issue(
                                errors,
                                "material-parent-parameter-name",
                                "Material parameter names must be non-empty strings of at most 128 characters without dots or slashes.",
                                name_path,
                            )
                            continue
                        if name in seen:
                            _issue(
                                errors,
                                "material-parent-parameter-name",
                                "Material parameter names must be unique within a type.",
                                name_path,
                            )
                            continue
                        seen.add(name)
                        valid_names.append(name)
                    material_parameter_names[parameter_type] = valid_names
                material_parents[target] = material_parameter_names
        elif kind == "materialAsset":
            if expected_class != "/Script/Engine.MaterialInstanceConstant":
                _issue(
                    errors,
                    "material-asset-class",
                    "materialAsset fixtures require expectedClass /Script/Engine.MaterialInstanceConstant.",
                    f"{base}.expectedClass",
                )
            parent_asset = fixture.get("parentAsset")
            if not isinstance(parent_asset, str) or not _is_package_path(parent_asset):
                _issue(
                    errors,
                    "material-parent-invalid",
                    "materialAsset parentAsset must be a valid package path.",
                    f"{base}.parentAsset",
                )
                parent_asset = ""
            if parent_asset and parent_asset == target:
                _issue(
                    errors,
                    "material-parent-equal",
                    "materialAsset parentAsset and targetAsset must differ.",
                    f"{base}.parentAsset",
                )
            values = fixture.get("values")
            if values is not None:
                if not isinstance(values, dict):
                    _issue(
                        errors,
                        "material-values",
                        "materialAsset values must be an object.",
                        f"{base}.values",
                    )
                else:
                    for type_name, type_values in values.items():
                        values_path = f"{base}.values.{type_name}"
                        if type_name not in _MATERIAL_PARAMETER_TYPES:
                            _issue(
                                errors,
                                "material-values-type",
                                "materialAsset value types must be Scalar, Vector, Texture, or StaticSwitch.",
                                values_path,
                            )
                            continue
                        if not isinstance(type_values, dict) or not type_values:
                            _issue(
                                errors,
                                "material-values-type",
                                "materialAsset value type must contain at least one parameter value.",
                                values_path,
                            )
                            continue
                        for parameter_name, value in type_values.items():
                            value_path = f"{values_path}.{parameter_name}"
                            if (
                                not isinstance(parameter_name, str)
                                or not parameter_name.strip()
                                or "." in parameter_name
                                or "/" in parameter_name
                            ):
                                _issue(
                                    errors,
                                    "material-values-name",
                                    "Material value parameter names must be non-empty strings without dots or slashes.",
                                    value_path,
                                )
                                continue
                            if type_name == "Scalar":
                                if isinstance(value, bool) or not isinstance(value, (int, float)):
                                    _issue(
                                        errors,
                                        "material-values-value",
                                        "Material scalar values require a finite JSON number.",
                                        value_path,
                                    )
                            elif type_name == "Vector":
                                if (
                                    not isinstance(value, dict)
                                    or set(value) != {"r", "g", "b", "a"}
                                    or any(
                                        isinstance(component, bool)
                                        or not isinstance(component, (int, float))
                                        for component in value.values()
                                    )
                                ):
                                    _issue(
                                        errors,
                                        "material-values-value",
                                        "Material vector values require a {r,g,b,a} JSON object.",
                                        value_path,
                                    )
                            elif type_name == "Texture":
                                if not isinstance(value, str) or not _is_package_path(
                                    value, allow_object_path=True
                                ):
                                    _issue(
                                        errors,
                                        "material-values-value",
                                        "Material texture values require an asset object path string.",
                                        value_path,
                                    )
                            else:
                                if not isinstance(value, bool):
                                    _issue(
                                        errors,
                                        "material-values-value",
                                        "Material static switch values require a JSON boolean.",
                                        value_path,
                                    )
        elif kind == "dataTableAsset":
            if expected_class != "/Script/Engine.DataTable":
                _issue(
                    errors,
                    "data-table-asset-class",
                    "dataTableAsset fixtures require expectedClass /Script/Engine.DataTable.",
                    f"{base}.expectedClass",
                )
            row_struct = fixture.get("rowStruct")
            if not isinstance(row_struct, str) or not _SCRIPT_CLASS_RE.fullmatch(row_struct):
                _issue(
                    errors,
                    "data-table-row-struct",
                    "dataTableAsset rowStruct must use /Script/Module.Struct form.",
                    f"{base}.rowStruct",
                )
            rows = fixture.get("rows")
            if not isinstance(rows, dict) or not rows or len(rows) > 64:
                _issue(
                    errors,
                    "data-table-rows",
                    "dataTableAsset rows must be an object containing 1-64 named rows.",
                    f"{base}.rows",
                )
            else:
                row_names: set[str] = set()
                for row_name, row_values in rows.items():
                    row_path = f"{base}.rows.{row_name}"
                    if (
                        not isinstance(row_name, str)
                        or not row_name.strip()
                        or len(row_name) > 256
                        or "." in row_name
                        or "/" in row_name
                    ):
                        _issue(
                            errors,
                            "data-table-row-name",
                            "DataTable row names must be non-empty strings without dots or slashes.",
                            row_path,
                        )
                        continue
                    if row_name in row_names:
                        _issue(
                            errors,
                            "data-table-row-name",
                            "DataTable row names must be unique.",
                            row_path,
                        )
                        continue
                    row_names.add(row_name)
                    if not isinstance(row_values, dict) or not row_values or len(row_values) > 32:
                        _issue(
                            errors,
                            "data-table-row-values",
                            "DataTable fixture rows require an object of 1-32 fields.",
                            row_path,
                        )
                        continue
                    for field_name, field_value in row_values.items():
                        field_path = f"{row_path}.{field_name}"
                        if (
                            not isinstance(field_name, str)
                            or not field_name.strip()
                            or "." in field_name
                        ):
                            _issue(
                                errors,
                                "data-table-field-name",
                                "DataTable field names must be non-empty strings without dots.",
                                field_path,
                            )
                            continue
                        if isinstance(field_value, (dict, list)) or field_value is None:
                            _issue(
                                errors,
                                "data-table-field-value",
                                "DataTable fixture field values must be finite JSON scalars.",
                                field_path,
                            )
        elif kind == "blueprint":
            if expected_class != "/Script/Engine.Blueprint":
                _issue(
                    errors,
                    "blueprint-class",
                    "blueprint fixtures require expectedClass /Script/Engine.Blueprint.",
                    f"{base}.expectedClass",
                )
            parent_class = fixture.get("parentClass")
            if not isinstance(parent_class, str) or not _SCRIPT_CLASS_RE.fullmatch(parent_class):
                _issue(
                    errors,
                    "parent-class",
                    "parentClass must use /Script/Module.Class form.",
                    f"{base}.parentClass",
                )
            blueprint_type = fixture.get("blueprintType")
            if blueprint_type not in _BLUEPRINT_TYPES:
                _issue(
                    errors,
                    "blueprint-type",
                    f"blueprintType must be one of {sorted(_BLUEPRINT_TYPES)}.",
                    f"{base}.blueprintType",
                )
        normalized.append(dict(fixture))

    for source in sorted(sources & targets):
        _issue(
            errors,
            "source-target-overlap",
            f"A sourceAsset cannot also be a targetAsset in the same plan: {source}",
            "plan.fixtures",
        )

    for index, fixture in enumerate(normalized):
        if fixture.get("kind") != "materialAsset":
            continue
        base = f"plan.fixtures[{index}]"
        parent_asset = fixture.get("parentAsset")
        if not isinstance(parent_asset, str):
            continue
        parent_parameters = material_parents.get(parent_asset)
        if parent_parameters is None:
            _issue(
                errors,
                "material-parent-kind",
                "materialAsset parentAsset must reference a materialParentAsset fixture in the same plan.",
                f"{base}.parentAsset",
            )
            continue
        values = fixture.get("values")
        if not isinstance(values, dict):
            continue
        for type_name, type_values in values.items():
            if type_name not in _MATERIAL_PARAMETER_TYPES or not isinstance(type_values, dict):
                continue
            declared = set(parent_parameters.get(type_name, []))
            for parameter_name in type_values:
                if parameter_name not in declared:
                    _issue(
                        errors,
                        "material-values-declared",
                        f"Material {type_name} parameter {parameter_name} is not declared by the parent material fixture.",
                        f"{base}.values.{type_name}.{parameter_name}",
                    )

    return {
        "schemaVersion": FIXTURE_PLAN_SCHEMA_VERSION,
        "toolVersion": FIXTURE_TOOL_VERSION,
        "valid": not errors,
        "willLoadOrModifyUObjects": False,
        "willWriteDisk": False,
        "planPath": str(resolved),
        "planRevision": plan_revision,
        "root": root,
        "fixtureCount": len(normalized),
        "fixtures": normalized,
        "errors": errors,
    }


def verify_fixture_export(fixture_report_path: Path, export_root: Path) -> dict[str, Any]:
    report_file = fixture_report_path.expanduser().resolve()
    resolved_export = export_root.expanduser().resolve()
    report = _read_json_object(report_file, label="Fixture report")
    errors: list[dict[str, str]] = []
    if report.get("valid") is not True or report.get("status") != "completed":
        _issue(errors, "fixture-report", "Fixture report must describe a completed plan.", "report")
    expected_project = report.get("projectName")
    expected_fixtures = report.get("fixtures")
    if not isinstance(expected_fixtures, list) or not expected_fixtures:
        _issue(errors, "fixture-report", "Fixture report contains no fixture results.", "report.fixtures")
        expected_fixtures = []

    canonical_root = resolved_export / "canonical"
    records: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    if canonical_root.is_dir():
        for path in canonical_root.rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("assetPath"), str):
                records.setdefault(value["assetPath"], []).append((path, value))
    else:
        _issue(errors, "export-missing", f"Canonical export directory does not exist: {canonical_root}", "export")

    verified: list[dict[str, Any]] = []
    for index, fixture in enumerate(expected_fixtures):
        base = f"report.fixtures[{index}]"
        if not isinstance(fixture, dict):
            _issue(errors, "fixture-result", "Fixture result must be an object.", base)
            continue
        asset_path = fixture.get("assetPath")
        matches = records.get(asset_path, []) if isinstance(asset_path, str) else []
        if len(matches) != 1:
            _issue(
                errors,
                "asset-export-match",
                f"Expected exactly one canonical match for {asset_path}, found {len(matches)}.",
                f"{base}.assetPath",
            )
            continue
        canonical_path, canonical = matches[0]
        if canonical.get("projectName") != expected_project:
            _issue(errors, "project-name", "Canonical projectName does not match fixture report.", str(canonical_path))
        if canonical.get("assetClass") != fixture.get("assetClass"):
            _issue(errors, "asset-class", "Canonical assetClass does not match fixture report.", str(canonical_path))
        revision = canonical.get("revision")
        actual_revision = revision.get("value") if isinstance(revision, dict) else None
        if actual_revision != fixture.get("revision"):
            _issue(errors, "asset-revision", "Canonical Revision does not match fixture report.", str(canonical_path))
        if not isinstance(revision, dict) or revision.get("packageDirty") is not False:
            _issue(errors, "asset-dirty", "Reloaded fixture package must not be dirty.", str(canonical_path))
        verified.append(
            {
                "id": fixture.get("id"),
                "assetPath": asset_path,
                "canonicalPath": str(canonical_path),
                "assetClass": canonical.get("assetClass"),
                "revision": actual_revision,
            }
        )

    return {
        "schemaVersion": FIXTURE_PLAN_SCHEMA_VERSION,
        "toolVersion": FIXTURE_TOOL_VERSION,
        "verified": not errors,
        "fixtureReportPath": str(report_file),
        "exportRoot": str(resolved_export),
        "expectedCount": len(expected_fixtures),
        "verifiedCount": len(verified),
        "fixtures": verified,
        "errors": errors,
    }

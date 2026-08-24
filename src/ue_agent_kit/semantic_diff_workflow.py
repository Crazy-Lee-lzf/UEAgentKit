from __future__ import annotations

import copy
import json
import hashlib
from pathlib import Path
from typing import Any

from .semantic_diff import (
    SemanticAssetEvidence,
    SemanticOperationEvidence,
    analyze_semantic_evidence,
    normalize_semantic_value,
    semantic_equal,
)


class SemanticDiffEvidenceError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


def _id_suffix(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _read_json(path: Path, *, maximum_bytes: int = 64 * 1024 * 1024) -> dict[str, Any] | None:
    try:
        if not path.is_file() or path.stat().st_size > maximum_bytes:
            return None
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _safe_id(value: str, prefix: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and len(prefix) < len(value) <= 96
        and all(character.isascii() and (character.isalnum() or character in "_-") for character in value)
    )


def _is_materialized_blueprint_pin_type_default(
    before_value: Any,
    after_value: Any,
    pin_type: dict[str, Any],
) -> bool:
    if before_value is not None:
        return False
    category = str(pin_type.get("category") or "").casefold()
    if category == "bool":
        return after_value is False or (
            isinstance(after_value, str) and after_value.casefold() == "false"
        )
    if category in {
        "byte",
        "double",
        "float",
        "int",
        "int32",
        "int64",
        "real",
        "uint32",
        "uint64",
    }:
        if isinstance(after_value, bool):
            return False
        if isinstance(after_value, (int, float)):
            return after_value == 0
        if isinstance(after_value, str):
            try:
                return float(after_value.strip()) == 0
            except ValueError:
                return False
        return False
    if category in {
        "class",
        "delegate",
        "exec",
        "interface",
        "mcdelegate",
        "multicastdelegate",
        "name",
        "object",
        "softclass",
        "softobject",
        "string",
        "text",
    }:
        return after_value == ""
    return False


def _load_plan(service: Any, plan_id: str) -> dict[str, Any] | None:
    if not _safe_id(plan_id, "plan_"):
        return None
    path = service._plan_directory(plan_id) / "patch.json"
    return _read_json(path, maximum_bytes=4 * 1024 * 1024)


def _path_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def _canonical_from_export(root: Path, asset_path: str, project_name: str) -> dict[str, Any] | None:
    manifest = _read_json(root / "manifest.json")
    if manifest is None or manifest.get("projectName") != project_name:
        return None
    entries = manifest.get("assets")
    if not isinstance(entries, list):
        return None
    entry = next(
        (
            item
            for item in entries
            if isinstance(item, dict) and item.get("assetPath") == asset_path and item.get("success") is True
        ),
        None,
    )
    if entry is None:
        return None
    raw_path = str(entry.get("jsonPath", "")).replace("\\", "/")
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    if not _path_within(candidate, root):
        marker = "/canonical/"
        marker_index = raw_path.casefold().find(marker)
        if marker_index < 0:
            return None
        relative = raw_path[marker_index + len(marker) :]
        candidate = root / "canonical" / Path(relative)
    if not _path_within(candidate, root):
        return None
    canonical = _read_json(candidate)
    if canonical is None:
        return None
    if canonical.get("projectName") != project_name or canonical.get("assetPath") != asset_path:
        return None
    return canonical


def _revision(canonical: dict[str, Any] | None) -> str:
    value = canonical.get("revision") if canonical else None
    return str(value.get("value", "")) if isinstance(value, dict) else ""


def _plan_asset(plan: dict[str, Any] | None, asset_path: str) -> dict[str, Any] | None:
    assets = plan.get("assets") if plan else None
    if not isinstance(assets, list):
        return None
    return next(
        (item for item in assets if isinstance(item, dict) and item.get("assetPath") == asset_path),
        None,
    )


def _plan_operations(plan_asset: dict[str, Any] | None) -> list[dict[str, Any]]:
    operations = plan_asset.get("operations") if plan_asset else None
    return [item for item in operations if isinstance(item, dict)] if isinstance(operations, list) else []


def _find_plan_operation(
    operations: list[dict[str, Any]],
    *,
    operation: str,
    target: dict[str, Any] | None = None,
    operation_id: str = "",
) -> dict[str, Any] | None:
    if operation_id:
        exact = next((item for item in operations if item.get("operationId") == operation_id), None)
        if exact is not None:
            return exact
    target = target or {}
    return next(
        (
            item
            for item in operations
            if item.get("operation") == operation
            and (not target or normalize_semantic_value(item.get("target")) == normalize_semantic_value(target))
        ),
        None,
    )


def _details(canonical: dict[str, Any] | None) -> dict[str, Any]:
    value = canonical.get("assetDetails") if canonical else None
    return value if isinstance(value, dict) else {}


def _property_value(canonical: dict[str, Any] | None, property_path: str) -> tuple[bool, Any, str]:
    for prop in _details(canonical).get("properties") or []:
        if isinstance(prop, dict) and prop.get("name") == property_path:
            return True, prop.get("value"), str(prop.get("valueType", ""))
    return False, None, ""


def _parse_ue_struct_literal(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not (value.startswith("(") and value.endswith(")")):
        return None
    result: dict[str, Any] = {}
    for part in value[1:-1].split(","):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if key:
            result[key] = raw
    return result


def _lookup_nested_property_path(value: Any, segments: list[str]) -> Any:
    for segment in segments:
        if isinstance(value, dict):
            if segment not in value:
                return None
            value = value[segment]
            continue
        if isinstance(value, str):
            parsed = _parse_ue_struct_literal(value)
            if parsed is None or segment not in parsed:
                return None
            value = parsed[segment]
            continue
        return None
    return value


def _blueprint_value(
    canonical: dict[str, Any] | None,
    operation: str,
    target: dict[str, Any],
) -> tuple[bool, Any, str, dict[str, Any]]:
    if canonical is None:
        return False, None, "", {}
    if operation == "setVariableDefault":
        name = str(target.get("variableName", ""))
        for variable in canonical.get("variables") or []:
            if isinstance(variable, dict) and variable.get("name") == name:
                return True, variable.get("defaultValue"), "blueprint-default", {"guid": variable.get("guid", "")}
        return False, None, "blueprint-default", {}
    if operation == "setComponentProperty":
        component_name = str(target.get("componentName", ""))
        property_path = str(target.get("propertyPath", ""))
        for component in canonical.get("components") or []:
            if not isinstance(component, dict) or component.get("name") != component_name:
                continue
            value = _lookup_nested_property_path(component.get("templateOverrides"), property_path.split("."))
            found = value is not None
            return found, value, "component-property", {
                "componentId": component.get("id", ""),
                "componentClass": component.get("class", ""),
            }
        return False, None, "component-property", {}
    if operation == "setPinDefault":
        graph_id = str(target.get("graphGuid", ""))
        node_id = str(target.get("nodeGuid", ""))
        pin_name = str(target.get("pinName", ""))
        for graph in canonical.get("graphs") or []:
            if not isinstance(graph, dict) or str(graph.get("guid", graph.get("id", ""))) != graph_id:
                continue
            for node in graph.get("nodes") or []:
                if not isinstance(node, dict) or str(node.get("guid", node.get("id", ""))) != node_id:
                    continue
                for pin in node.get("pins") or []:
                    if isinstance(pin, dict) and pin.get("name") == pin_name:
                        value = pin.get("defaultValue", pin.get("default"))
                        return True, value, "pin-default", {"pinId": pin.get("id", "")}
        return False, None, "pin-default", {}
    return False, None, "", {}


def _rows(canonical: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _details(canonical).get("rows") or []:
        if not isinstance(row, dict) or not isinstance(row.get("Name"), str):
            continue
        value = dict(row)
        name = str(value.pop("Name"))
        result[name] = value
    return result


_MATERIAL_SECTIONS = {
    "setMaterialInstanceScalarParameter": "scalarParameters",
    "setMaterialInstanceVectorParameter": "vectorParameters",
    "setMaterialInstanceTextureParameter": "textureParameters",
    "setMaterialInstanceStaticSwitchParameter": "staticSwitchParameters",
}


def _material_state(
    canonical: dict[str, Any] | None, operation: str, parameter_name: str
) -> tuple[bool, Any, dict[str, Any]]:
    section = _MATERIAL_SECTIONS.get(operation, "")
    for parameter in _details(canonical).get(section) or []:
        if not isinstance(parameter, dict) or parameter.get("name") != parameter_name:
            continue
        raw_value = (
            parameter.get("valuePath") if operation == "setMaterialInstanceTextureParameter" else parameter.get("value")
        )
        state = {"override": bool(parameter.get("override")), "value": raw_value}
        metadata = {
            key: parameter.get(key)
            for key in ("association", "associationValue", "index", "expressionGuid")
            if key in parameter
        }
        return True, state, metadata
    return False, {"override": False, "value": None}, {}


def _value_for_operation(
    canonical: dict[str, Any] | None,
    operation: str,
    target: dict[str, Any],
    expected_value: Any,
) -> tuple[bool, Any, str, dict[str, Any]]:
    if operation in {"setVariableDefault", "setComponentProperty", "setPinDefault"}:
        return _blueprint_value(canonical, operation, target)
    if operation in {"setAssetProperty", "setAssetReferenceProperty", "setAssetStructuredProperty"}:
        found, value, value_type = _property_value(canonical, str(target.get("propertyPath", "")))
        return found, value, value_type, {}
    if operation in _MATERIAL_SECTIONS:
        found, value, metadata = _material_state(canonical, operation, str(target.get("parameterName", "")))
        return found, value, "material-parameter-state", metadata
    if operation in {
        "setDataTableCell",
        "setDataTableRowFields",
        "addDataTableRow",
        "removeDataTableRow",
        "renameDataTableRow",
    }:
        rows = _rows(canonical)
        row_name = str(target.get("rowName", ""))
        if operation == "setDataTableCell":
            row = rows.get(row_name)
            field = str(target.get("fieldName", ""))
            return row is not None and field in row, row.get(field) if row else None, "scalar", {}
        if operation == "setDataTableRowFields":
            row = rows.get(row_name)
            requested_fields = expected_value.keys() if isinstance(expected_value, dict) else ()
            selected = {field: row.get(field) for field in requested_fields} if row is not None else None
            return row is not None, selected, "struct", {}
        if operation == "addDataTableRow":
            return row_name in rows, rows.get(row_name), "struct", {}
        if operation == "removeDataTableRow":
            return True, rows.get(row_name), "struct", {}
        new_row_name = str(target.get("newRowName", ""))
        renamed = row_name not in rows and new_row_name in rows
        unchanged_source = row_name in rows and new_row_name not in rows
        if renamed:
            return True, {"from": row_name, "to": new_row_name}, "row-identity", {}
        if unchanged_source:
            return True, {"from": row_name, "to": row_name}, "row-identity", {}
        return False, None, "row-identity", {}
    return False, None, "", {}


def _expected_for_operation(operation: str, value: Any) -> Any:
    if operation in _MATERIAL_SECTIONS:
        return {"override": True, "value": value}
    return value


def _slice_data_table_value(
    operation: str,
    target: dict[str, Any],
    expected: Any,
    value: Any,
    *,
    actual: bool,
) -> Any:
    if operation == "setDataTableCell" and isinstance(value, dict):
        return value.get(str(target.get("fieldName", "")))
    if (
        operation in {"setDataTableRowFields", "addDataTableRow"}
        and isinstance(expected, dict)
        and isinstance(value, dict)
    ):
        return {field: value.get(field) for field in expected}
    if operation == "renameDataTableRow" and value is not None:
        source = str(target.get("rowName", ""))
        return {"from": source, "to": str(target.get("newRowName", "")) if actual else source}
    return value


def _critical_invariants(
    asset_path: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    expected_operations: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    unchanged: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    if before is None or after is None:
        gaps.append(
            {
                "gapId": f"gap_snapshot_{_id_suffix(asset_path)}",
                "code": "insufficient-domain-snapshot-for-unexpected-change-detection",
                "assetPath": asset_path,
                "operationId": "",
                "message": "A complete before/after Canonical pair is unavailable for critical invariant analysis.",
            }
        )
        return unchanged, gaps
    if before.get("assetClass") == after.get("assetClass"):
        unchanged.append(
            {
                "fieldId": f"invariant:{asset_path}:asset-class",
                "assetPath": asset_path,
                "semanticPath": "Asset.Identity.Class",
                "value": before.get("assetClass"),
                "status": "unchanged",
                "source": "canonical-before-after",
            }
        )
    before_details = _details(before)
    after_details = _details(after)
    if before_details.get("type") == "material-instance" and before_details.get("parentPath") == after_details.get(
        "parentPath"
    ):
        unchanged.append(
            {
                "fieldId": f"invariant:{asset_path}:material-parent",
                "assetPath": asset_path,
                "semanticPath": "MaterialInstance.Parent",
                "value": before_details.get("parentPath"),
                "status": "unchanged",
                "source": "canonical-before-after",
            }
        )
    operations = {str(item.get("operation", "")) for item in expected_operations}
    blueprint_requirements = {
        "setVariableDefault": ("variables", {"defaults", "full", "ai"}),
        "setComponentProperty": ("components", {"defaults", "full", "ai"}),
        "setPinDefault": ("graphs", {"logic", "full", "ai"}),
    }
    for operation, (section, profiles) in blueprint_requirements.items():
        if operation not in operations:
            continue
        if (
            not isinstance(before.get(section), list)
            or not isinstance(after.get(section), list)
            or str(before.get("profile", "")).casefold() not in profiles
            or str(after.get("profile", "")).casefold() not in profiles
        ):
            gaps.append(
                {
                    "gapId": f"gap_blueprint_{operation}_{_id_suffix(asset_path)}",
                    "code": "insufficient-domain-snapshot-for-unexpected-change-detection",
                    "assetPath": asset_path,
                    "operationId": "",
                    "message": f"Blueprint {section} before/after Canonical coverage is incomplete for {operation}.",
                }
            )
    if before_details.get("type") == "data-table":
        before_rows = _rows(before)
        after_rows = _rows(after)
        structural = bool(operations & {"addDataTableRow", "removeDataTableRow", "renameDataTableRow"})
        if not structural and list(sorted(before_rows, key=str.casefold)) == list(sorted(after_rows, key=str.casefold)):
            unchanged.append(
                {
                    "fieldId": f"invariant:{asset_path}:row-identities",
                    "assetPath": asset_path,
                    "semanticPath": "DataTable.RowIdentities",
                    "value": sorted(before_rows, key=str.casefold),
                    "status": "unchanged",
                    "source": "canonical-before-after",
                }
            )
    return unchanged, gaps


def _snapshot_actual_only(
    asset_path: str,
    asset_class: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    stage: str,
    expected_operations: list[dict[str, Any]],
) -> list[SemanticOperationEvidence]:
    if before is None or after is None:
        return []
    result: list[SemanticOperationEvidence] = []
    expected_keys = {
        (
            str(item.get("operation", "")),
            json.dumps(normalize_semantic_value(item.get("target", {})), sort_keys=True, separators=(",", ":")),
        )
        for item in expected_operations
    }
    expected_property_paths = {
        str(item.get("target", {}).get("propertyPath", ""))
        for item in expected_operations
        if item.get("operation") in {"setAssetProperty", "setAssetReferenceProperty", "setAssetStructuredProperty"}
        and isinstance(item.get("target"), dict)
    }
    expected_component_paths = {
        (
            str(item.get("target", {}).get("componentName", "")),
            str(item.get("target", {}).get("propertyPath", "")),
        )
        for item in expected_operations
        if item.get("operation") == "setComponentProperty" and isinstance(item.get("target"), dict)
    }
    for item in expected_operations:
        if item.get("operation") != "setDataTableRowFields" or not isinstance(item.get("target"), dict):
            continue
        row_name = str(item["target"].get("rowName", ""))
        if isinstance(item.get("value"), dict):
            for field_name in item["value"]:
                target = {"rowName": row_name, "fieldName": field_name}
                expected_keys.add(("setDataTableCell", json.dumps(target, sort_keys=True, separators=(",", ":"))))

    before_properties = {
        str(item.get("name")): item
        for item in _details(before).get("properties") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    after_properties = {
        str(item.get("name")): item
        for item in _details(after).get("properties") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for name in sorted(set(before_properties) | set(after_properties), key=str.casefold):
        left = before_properties.get(name, {}).get("value")
        right = after_properties.get(name, {}).get("value")
        target = {"propertyPath": name}
        key = ("setAssetStructuredProperty", json.dumps(target, sort_keys=True, separators=(",", ":")))
        if name not in expected_property_paths and key not in expected_keys and not semantic_equal(left, right):
            result.append(
                SemanticOperationEvidence(
                    operation_id=f"snapshot-property:{name}",
                    asset_path=asset_path,
                    operation="setAssetStructuredProperty",
                    target=target,
                    expected_value=None,
                    before_value=left,
                    actual_value=right,
                    stage=stage,  # type: ignore[arg-type]
                    source="canonical-before-after",
                    expected_available=False,
                    before_revision=_revision(before),
                    after_revision=_revision(after),
                    stage_evidence_revision=_revision(after),
                    asset_class=asset_class,
                    value_kind=str(after_properties.get(name, {}).get("valueType", "")),
                )
            )

    before_variables = {
        str(item.get("name")): item
        for item in before.get("variables") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    after_variables = {
        str(item.get("name")): item
        for item in after.get("variables") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for name in sorted(set(before_variables) | set(after_variables), key=str.casefold):
        left = before_variables.get(name, {}).get("defaultValue")
        right = after_variables.get(name, {}).get("defaultValue")
        target = {"variableName": name}
        key = ("setVariableDefault", json.dumps(target, sort_keys=True, separators=(",", ":")))
        if key not in expected_keys and not semantic_equal(left, right):
            result.append(
                SemanticOperationEvidence(
                    operation_id=f"snapshot-blueprint-variable:{name}",
                    asset_path=asset_path,
                    operation="setVariableDefault",
                    target=target,
                    expected_value=None,
                    before_value=left,
                    actual_value=right,
                    stage=stage,  # type: ignore[arg-type]
                    source="canonical-before-after",
                    expected_available=False,
                    before_revision=_revision(before),
                    after_revision=_revision(after),
                    stage_evidence_revision=_revision(after),
                    asset_class=asset_class,
                    value_kind="blueprint-default",
                )
            )

    before_components = {
        str(item.get("name")): item
        for item in before.get("components") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    after_components = {
        str(item.get("name")): item
        for item in after.get("components") or []
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    for component_name in sorted(set(before_components) | set(after_components), key=str.casefold):
        left_values = before_components.get(component_name, {}).get("templateOverrides")
        right_values = after_components.get(component_name, {}).get("templateOverrides")
        left_values = left_values if isinstance(left_values, dict) else {}
        right_values = right_values if isinstance(right_values, dict) else {}
        for property_name in sorted(set(left_values) | set(right_values), key=str.casefold):
            left = left_values.get(property_name)
            right = right_values.get(property_name)
            target = {"componentName": component_name, "propertyPath": property_name}
            key = ("setComponentProperty", json.dumps(target, sort_keys=True, separators=(",", ":")))
            is_parent_of_expected_nested = any(
                exp_component == component_name
                and exp_path.startswith(property_name + ".")
                for exp_component, exp_path in expected_component_paths
            )
            if key not in expected_keys and not semantic_equal(left, right) and not is_parent_of_expected_nested:
                result.append(
                    SemanticOperationEvidence(
                        operation_id=f"snapshot-blueprint-component:{component_name}:{property_name}",
                        asset_path=asset_path,
                        operation="setComponentProperty",
                        target=target,
                        expected_value=None,
                        before_value=left,
                        actual_value=right,
                        stage=stage,  # type: ignore[arg-type]
                        source="canonical-before-after",
                        expected_available=False,
                        before_revision=_revision(before),
                        after_revision=_revision(after),
                        stage_evidence_revision=_revision(after),
                        asset_class=asset_class,
                        value_kind="component-property",
                    )
                )

    def pin_defaults(
        canonical: dict[str, Any],
    ) -> dict[tuple[str, str, str], tuple[Any, dict[str, Any]]]:
        values: dict[tuple[str, str, str], tuple[Any, dict[str, Any]]] = {}
        for graph in canonical.get("graphs") or []:
            if not isinstance(graph, dict):
                continue
            graph_id = str(graph.get("guid", graph.get("id", "")))
            for node in graph.get("nodes") or []:
                if not isinstance(node, dict):
                    continue
                node_id = str(node.get("guid", node.get("id", "")))
                for pin in node.get("pins") or []:
                    if isinstance(pin, dict) and isinstance(pin.get("name"), str):
                        pin_type = pin.get("type")
                        values[(graph_id, node_id, str(pin["name"]))] = (
                            pin.get("defaultValue", pin.get("default")),
                            pin_type if isinstance(pin_type, dict) else {},
                        )
        return values

    before_pins = pin_defaults(before)
    after_pins = pin_defaults(after)
    for graph_id, node_id, pin_name in sorted(set(before_pins) | set(after_pins)):
        pin_key = (graph_id, node_id, pin_name)
        left_entry = before_pins.get(pin_key)
        right_entry = after_pins.get(pin_key)
        left = left_entry[0] if left_entry is not None else None
        right = right_entry[0] if right_entry is not None else None
        right_type = right_entry[1] if right_entry is not None else {}
        target = {"graphGuid": graph_id, "nodeGuid": node_id, "pinName": pin_name}
        key = ("setPinDefault", json.dumps(target, sort_keys=True, separators=(",", ":")))
        materialized_type_default = _is_materialized_blueprint_pin_type_default(
            left,
            right,
            right_type,
        )
        if key not in expected_keys and not semantic_equal(left, right) and not materialized_type_default:
            result.append(
                SemanticOperationEvidence(
                    operation_id=f"snapshot-blueprint-pin:{graph_id}:{node_id}:{pin_name}",
                    asset_path=asset_path,
                    operation="setPinDefault",
                    target=target,
                    expected_value=None,
                    before_value=left,
                    actual_value=right,
                    stage=stage,  # type: ignore[arg-type]
                    source="canonical-before-after",
                    expected_available=False,
                    before_revision=_revision(before),
                    after_revision=_revision(after),
                    stage_evidence_revision=_revision(after),
                    asset_class=asset_class,
                    value_kind="pin-default",
                )
            )

    before_rows = _rows(before)
    after_rows = _rows(after)
    expected_renames = [item for item in expected_operations if item.get("operation") == "renameDataTableRow"]
    renamed_names = {
        str(item.get("target", {}).get(key_name, ""))
        for item in expected_renames
        if isinstance(item.get("target"), dict)
        for key_name in ("rowName", "newRowName")
    }
    for item in expected_renames:
        target_value = item.get("target")
        if not isinstance(target_value, dict):
            continue
        old_name = str(target_value.get("rowName", ""))
        new_name = str(target_value.get("newRowName", ""))
        left_row = before_rows.get(old_name)
        right_row = after_rows.get(new_name)
        if left_row is None or right_row is None:
            continue
        for field_name in sorted(set(left_row) | set(right_row), key=str.casefold):
            left = left_row.get(field_name)
            right = right_row.get(field_name)
            if semantic_equal(left, right):
                continue
            result.append(
                SemanticOperationEvidence(
                    operation_id=f"snapshot-renamed-row-cell:{old_name}:{new_name}:{field_name}",
                    asset_path=asset_path,
                    operation="setDataTableCell",
                    target={"rowName": new_name, "fieldName": field_name},
                    expected_value=None,
                    before_value=left,
                    actual_value=right,
                    stage=stage,  # type: ignore[arg-type]
                    source="canonical-before-after",
                    expected_available=False,
                    before_revision=_revision(before),
                    after_revision=_revision(after),
                    stage_evidence_revision=_revision(after),
                    asset_class=asset_class,
                    value_kind="scalar",
                    details={"renamedFrom": old_name, "renamedTo": new_name},
                )
            )
    for row_name in sorted(set(before_rows) | set(after_rows), key=str.casefold):
        if row_name in renamed_names:
            continue
        left_row = before_rows.get(row_name)
        right_row = after_rows.get(row_name)
        if left_row is None or right_row is None:
            operation = "addDataTableRow" if left_row is None else "removeDataTableRow"
            target = {"rowName": row_name}
            key = (operation, json.dumps(target, sort_keys=True, separators=(",", ":")))
            if key in expected_keys:
                continue
            result.append(
                SemanticOperationEvidence(
                    operation_id=f"snapshot-row:{row_name}",
                    asset_path=asset_path,
                    operation=operation,
                    target=target,
                    expected_value=None,
                    before_value=left_row,
                    actual_value=right_row,
                    stage=stage,  # type: ignore[arg-type]
                    source="canonical-before-after",
                    expected_available=False,
                    before_revision=_revision(before),
                    after_revision=_revision(after),
                    stage_evidence_revision=_revision(after),
                    asset_class=asset_class,
                    value_kind="struct",
                )
            )
            continue
        for field_name in sorted(set(left_row) | set(right_row), key=str.casefold):
            left = left_row.get(field_name)
            right = right_row.get(field_name)
            target = {"rowName": row_name, "fieldName": field_name}
            key = ("setDataTableCell", json.dumps(target, sort_keys=True, separators=(",", ":")))
            if key not in expected_keys and not semantic_equal(left, right):
                result.append(
                    SemanticOperationEvidence(
                        operation_id=f"snapshot-cell:{row_name}:{field_name}",
                        asset_path=asset_path,
                        operation="setDataTableCell",
                        target=target,
                        expected_value=None,
                        before_value=left,
                        actual_value=right,
                        stage=stage,  # type: ignore[arg-type]
                        source="canonical-before-after",
                        expected_available=False,
                        before_revision=_revision(before),
                        after_revision=_revision(after),
                        stage_evidence_revision=_revision(after),
                        asset_class=asset_class,
                        value_kind="scalar",
                    )
                )

    for operation, section in _MATERIAL_SECTIONS.items():
        names = {
            str(item.get("name"))
            for canonical in (before, after)
            for item in _details(canonical).get(section) or []
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        }
        for name in sorted(names, key=str.casefold):
            _, left, left_meta = _material_state(before, operation, name)
            _, right, right_meta = _material_state(after, operation, name)
            target = {"parameterName": name}
            key = (operation, json.dumps(target, sort_keys=True, separators=(",", ":")))
            if key not in expected_keys and not semantic_equal(left, right):
                result.append(
                    SemanticOperationEvidence(
                        operation_id=f"snapshot-material:{section}:{name}",
                        asset_path=asset_path,
                        operation=operation,
                        target=target,
                        expected_value=None,
                        before_value=left,
                        actual_value=right,
                        stage=stage,  # type: ignore[arg-type]
                        source="canonical-before-after",
                        expected_available=False,
                        before_revision=_revision(before),
                        after_revision=_revision(after),
                        stage_evidence_revision=_revision(after),
                        asset_class=asset_class,
                        value_kind="material-parameter-state",
                        details={"before": left_meta, "after": right_meta},
                    )
                )
    return result


def _artifact_canonical(service: Any, member: Any, stage: str) -> dict[str, Any] | None:
    if member.receipt.startswith("noop_"):
        if stage != "persisted":
            return None
        canonical = _canonical_from_export(
            service.config.revision_export,
            member.asset_path,
            service.project_name,
        )
        plan_asset = _plan_asset(_load_plan(service, member.plan_id), member.asset_path)
        expected_revision = str(plan_asset.get("expectedRevision", "")) if plan_asset else ""
        if canonical is None or not expected_revision or _revision(canonical) != expected_revision:
            return None
        return canonical
    if member.receipt.startswith("apply_"):
        if stage != "verified":
            return None
        root = service._safe_work_path("verify", member.receipt)
    elif stage == "verified":
        root = service._safe_work_path("verify-live-write", member.receipt)
    elif stage == "persisted" and member.save_receipt:
        root = service._safe_work_path("authorized-save", member.save_receipt, "verify")
    else:
        return None
    return _canonical_from_export(root, member.asset_path, service.project_name)


def _apply_report(service: Any, member: Any) -> dict[str, Any] | None:
    apply_record = service._applies.get(member.receipt)
    if apply_record is not None and isinstance(apply_record.report, dict):
        return apply_record.report
    if not _safe_id(member.plan_id, "plan_"):
        return None
    return _read_json(service._plan_directory(member.plan_id) / "commit" / "report.json")


def _available_stages(service: Any, members: list[Any]) -> list[str]:
    available: list[str] = []
    asset_receipt_kinds: dict[str, set[str]] = {}
    for member in members:
        kind = "noop" if member.receipt.startswith("noop_") else "write"
        asset_receipt_kinds.setdefault(member.asset_path, set()).add(kind)
    mixed_noop_assets = any(len(kinds) > 1 for kinds in asset_receipt_kinds.values())
    if members and not mixed_noop_assets and all(
        member.receipt.startswith("live_") and service._live_applies.get(member.receipt) is not None
        for member in members
    ):
        available.append("live")
    if members and all(
        (
            _apply_report(service, member) is not None
            if member.receipt.startswith("apply_")
            else _artifact_canonical(service, member, "persisted") is not None
        )
        for member in members
    ):
        available.append("persisted")
    if members and all(_artifact_canonical(service, member, "verified") is not None for member in members):
        available.append("verified")
    return available


def analyze_workflow_semantic_diff(
    service: Any,
    change_set_id: str,
    *,
    stage: str = "auto",
    asset_paths: list[str] | None = None,
    include_unchanged: bool = True,
    max_changes: int = 64,
    max_output_tokens: int = 4096,
) -> dict[str, Any]:
    if stage not in {"auto", "live", "persisted", "verified"}:
        raise ValueError("stage must be auto, live, persisted, or verified")
    with service._lock:
        source_change_set_record = service._resolve_change_set(change_set_id)
        change_set_record = copy.deepcopy(source_change_set_record)
        service._reconcile_change_set(change_set_record, persist=False)
        members = list(change_set_record.operations)
        available = _available_stages(service, members)
        if not members:
            raise SemanticDiffEvidenceError(
                "insufficient-evidence",
                "The explicit Change Set has no bound operations to analyze.",
                details={"changeSetId": change_set_id, "availableStages": available},
            )
        if stage == "auto":
            selected = next(
                (candidate for candidate in ("verified", "persisted", "live") if candidate in available), ""
            )
            selection_reason = (
                f"Selected the highest complete evidence stage available for all {len(members)} bound operations."
            )
        else:
            selected = stage if stage in available else ""
            selection_reason = "The explicitly requested evidence stage is complete for every returned operation."
        if not selected:
            raise SemanticDiffEvidenceError(
                "semantic-diff-stage-unavailable",
                "The requested Change Set does not have complete evidence for this stage.",
                details={"changeSetId": change_set_id, "requestedStage": stage, "availableStages": available},
            )

        change_set = {
            "changeSetId": str(getattr(change_set_record, "change_set_id", change_set_id)),
            "taskId": str(getattr(change_set_record, "task_id", "")),
            "status": str(getattr(change_set_record, "status", "unknown")),
            "affectedAssets": sorted(
                {str(member.asset_path) for member in members if getattr(member, "asset_path", "")},
                key=str.casefold,
            ),
        }
        grouped: dict[str, list[Any]] = {}
        for member in members:
            grouped.setdefault(member.asset_path, []).append(member)
        assets: list[SemanticAssetEvidence] = []
        sources: list[dict[str, Any]] = [{"kind": "change-set-journal", "id": change_set_id}]
        for asset_path in sorted(grouped, key=str.casefold):
            asset_members = grouped[asset_path]
            before_canonical = _canonical_from_export(service.config.revision_export, asset_path, service.project_name)
            after_canonical = _artifact_canonical(service, asset_members[-1], selected)
            operation_evidence: list[SemanticOperationEvidence] = []
            expected_operations: list[dict[str, Any]] = []
            expected_revisions: set[str] = set()
            asset_class = str((before_canonical or after_canonical or {}).get("assetClass", ""))
            before_revision = _revision(before_canonical)
            after_revision = _revision(after_canonical)

            for member in asset_members:
                plan = _load_plan(service, member.plan_id)
                plan_asset = _plan_asset(plan, asset_path)
                planned = _plan_operations(plan_asset)
                expected_operations.extend(planned)
                if plan_asset is not None:
                    expected_revision = str(plan_asset.get("expectedRevision", ""))
                    if expected_revision:
                        expected_revisions.add(expected_revision)
                    before_revision = before_revision or expected_revision
                    asset_class = asset_class or str(plan_asset.get("expectedAssetClass", ""))
                if plan is not None:
                    sources.append({"kind": "plan-patch", "id": member.plan_id})
                if member.receipt.startswith("apply_"):
                    report = _apply_report(service, member)
                    report_operations = report.get("operations") if report else None
                    if not isinstance(report_operations, list):
                        report_operations = [report] if isinstance(report, dict) else []
                    for report_operation in report_operations:
                        if not isinstance(report_operation, dict):
                            continue
                        operation = str(report_operation.get("operation", ""))
                        operation_id = str(report_operation.get("operationId", member.receipt))
                        target = report_operation.get("target")
                        target = dict(target) if isinstance(target, dict) else {}
                        intent = _find_plan_operation(
                            planned, operation=operation, target=target, operation_id=operation_id
                        )
                        expected_value = intent.get("value") if intent else None
                        details = {
                            key: report_operation.get(key)
                            for key in (
                                "beforeOverride",
                                "afterOverride",
                                "beforeExpressionGuid",
                                "afterExpressionGuid",
                                "targetType",
                            )
                            if key in report_operation
                        }
                        before_value = report_operation.get("beforeValue")
                        actual_value = report_operation.get("afterValue")
                        if operation in _MATERIAL_SECTIONS:
                            before_value = {
                                "override": bool(report_operation.get("beforeOverride")),
                                "value": before_value,
                            }
                            actual_value = {
                                "override": bool(report_operation.get("afterOverride")),
                                "value": actual_value,
                            }
                            expected_value = _expected_for_operation(operation, expected_value)
                        actual_available = "afterValue" in report_operation
                        extracted_kind = ""
                        after_meta: dict[str, Any] = {}
                        if selected == "verified":
                            actual_available, actual_value, extracted_kind, after_meta = _value_for_operation(
                                after_canonical,
                                operation,
                                target,
                                intent.get("value") if intent else None,
                            )
                            if after_meta:
                                details["verifiedMetadata"] = after_meta
                            details["reportedAfterRevision"] = str((report or {}).get("afterRevision", ""))
                        before_value = _slice_data_table_value(
                            operation, target, expected_value, before_value, actual=False
                        )
                        actual_value = _slice_data_table_value(
                            operation, target, expected_value, actual_value, actual=True
                        )
                        expanded = [(operation, operation_id, target, expected_value, before_value, actual_value)]
                        if operation == "setDataTableRowFields" and isinstance(expected_value, dict):
                            expanded = [
                                (
                                    "setDataTableCell",
                                    f"{operation_id}:{field_name}",
                                    {"rowName": target.get("rowName", ""), "fieldName": field_name},
                                    field_value,
                                    before_value.get(field_name) if isinstance(before_value, dict) else None,
                                    actual_value.get(field_name) if isinstance(actual_value, dict) else None,
                                )
                                for field_name, field_value in sorted(
                                    expected_value.items(), key=lambda item: item[0].casefold()
                                )
                            ]
                        for (
                            expanded_operation,
                            expanded_id,
                            expanded_target,
                            expanded_expected,
                            expanded_before,
                            expanded_actual,
                        ) in expanded:
                            operation_evidence.append(
                                SemanticOperationEvidence(
                                    operation_id=expanded_id,
                                    asset_path=asset_path,
                                    operation=expanded_operation,
                                    target=expanded_target,
                                    expected_value=expanded_expected,
                                    before_value=expanded_before,
                                    actual_value=expanded_actual,
                                    stage=selected,  # type: ignore[arg-type]
                                    source="patch-commit-report"
                                    if selected == "persisted"
                                    else "independent-verify-canonical",
                                    expected_available=intent is not None,
                                    before_available="beforeValue" in report_operation,
                                    actual_available=actual_available,
                                    before_revision=str((report or {}).get("beforeRevision", before_revision)),
                                    after_revision=str((report or {}).get("afterRevision", "")),
                                    stage_evidence_revision=(
                                        _revision(after_canonical)
                                        if selected == "verified"
                                        else str((report or {}).get("afterRevision", ""))
                                    ),
                                    asset_class=str((report or {}).get("assetClass", asset_class)),
                                    value_kind=(
                                        "material-parameter-state"
                                        if operation in _MATERIAL_SECTIONS
                                        else extracted_kind
                                    ),
                                    details=details,
                                )
                            )
                    sources.append({"kind": "patch-commit-report", "id": member.receipt})
                    continue

                live = service._live_applies.get(member.receipt)
                intent = _find_plan_operation(
                    planned,
                    operation=member.operation,
                    target=live.target if live is not None else None,
                )
                target = dict(live.target) if live is not None else dict(intent.get("target", {})) if intent else {}
                expected_value = intent.get("value") if intent else None
                value_kind = str(live.value_kind) if live is not None else ""
                details: dict[str, Any] = {"transactionId": member.transaction_id}
                if selected == "live":
                    before_available = live is not None
                    actual_available = live is not None
                    before_value = live.before_value if live is not None else None
                    actual_value = live.after_value if live is not None else None
                    operation = live.operation if live is not None else member.operation
                    if operation in _MATERIAL_SECTIONS:
                        details["overrideStateAvailable"] = False
                else:
                    operation = member.operation
                    found_before, before_value, extracted_kind, before_meta = _value_for_operation(
                        before_canonical,
                        operation,
                        target,
                        expected_value,
                    )
                    found_after, actual_value, after_kind, after_meta = _value_for_operation(
                        after_canonical,
                        operation,
                        target,
                        expected_value,
                    )
                    before_available = before_canonical is not None and (
                        found_before or operation == "removeDataTableRow"
                    )
                    actual_available = after_canonical is not None and (
                        found_after or operation == "removeDataTableRow"
                    )
                    value_kind = after_kind or extracted_kind
                    details.update({"beforeMetadata": before_meta, "afterMetadata": after_meta})
                    expected_value = _expected_for_operation(operation, expected_value)
                before_value = _slice_data_table_value(operation, target, expected_value, before_value, actual=False)
                actual_value = _slice_data_table_value(operation, target, expected_value, actual_value, actual=True)
                expanded = [(operation, member.receipt, target, expected_value, before_value, actual_value)]
                if operation == "setDataTableRowFields" and isinstance(expected_value, dict):
                    expanded = [
                        (
                            "setDataTableCell",
                            f"{member.receipt}:{field_name}",
                            {"rowName": target.get("rowName", ""), "fieldName": field_name},
                            field_value,
                            before_value.get(field_name) if isinstance(before_value, dict) else None,
                            actual_value.get(field_name) if isinstance(actual_value, dict) else None,
                        )
                        for field_name, field_value in sorted(
                            expected_value.items(), key=lambda item: item[0].casefold()
                        )
                    ]
                for (
                    expanded_operation,
                    expanded_id,
                    expanded_target,
                    expanded_expected,
                    expanded_before,
                    expanded_actual,
                ) in expanded:
                    operation_evidence.append(
                        SemanticOperationEvidence(
                            operation_id=expanded_id,
                            asset_path=asset_path,
                            operation=expanded_operation,
                            target=expanded_target,
                            expected_value=expanded_expected,
                            before_value=expanded_before,
                            actual_value=expanded_actual,
                            stage=selected,  # type: ignore[arg-type]
                            source="live-transaction" if selected == "live" else f"canonical-{selected}",
                            expected_available=intent is not None,
                            before_available=before_available,
                            actual_available=actual_available,
                            before_revision=before_revision,
                            after_revision=after_revision,
                            stage_evidence_revision=after_revision,
                            asset_class=asset_class,
                            value_kind=value_kind,
                            details=details,
                        )
                    )
                sources.append(
                    {
                        "kind": (
                            "baseline-canonical-no-op"
                            if member.receipt.startswith("noop_")
                            else f"{selected}-operation-evidence"
                        ),
                        "id": member.receipt,
                    }
                )

            unchanged, gaps = _critical_invariants(
                asset_path,
                before_canonical,
                after_canonical,
                expected_operations,
            )
            observed_before_revisions = {
                item.before_revision for item in operation_evidence if item.before_revision
            }
            if before_revision:
                observed_before_revisions.add(before_revision)
            if len(expected_revisions) > 1 or any(
                observed not in expected_revisions
                for observed in observed_before_revisions
                if expected_revisions
            ):
                gaps.append(
                    {
                        "gapId": f"gap_stale_before_{_id_suffix(asset_path)}",
                        "code": "semantic-diff-evidence-stale",
                        "assetPath": asset_path,
                        "operationId": "",
                        "message": "Plan expectedRevision does not match the observed before evidence revision.",
                    }
                )
            for item in operation_evidence:
                reported_after = str(item.details.get("reportedAfterRevision", ""))
                if reported_after and item.stage_evidence_revision and reported_after != item.stage_evidence_revision:
                    gaps.append(
                        {
                            "gapId": f"gap_stale_after_{_id_suffix(asset_path + item.operation_id)}",
                            "code": "semantic-diff-evidence-stale",
                            "assetPath": asset_path,
                            "operationId": item.operation_id,
                            "message": "Independent after Canonical revision does not match the Commit report revision.",
                        }
                    )
            if selected == "live":
                gaps.append(
                    {
                        "gapId": f"gap_live_revision_{_id_suffix(asset_path)}",
                        "code": "revision-evidence-unavailable",
                        "assetPath": asset_path,
                        "operationId": "",
                        "message": "Live Editor memory evidence has no after Package revision and does not prove persistence.",
                    }
                )
            if selected == "persisted" and any(member.receipt.startswith("apply_") for member in asset_members):
                gaps.append(
                    {
                        "gapId": f"gap_persisted_snapshot_{_id_suffix(asset_path)}",
                        "code": "insufficient-domain-snapshot-for-unexpected-change-detection",
                        "assetPath": asset_path,
                        "operationId": "",
                        "message": "The commandlet Commit report proves target values but persisted full-domain after Canonical is only available after independent verify.",
                    }
                )
            actual_only = _snapshot_actual_only(
                asset_path,
                asset_class,
                before_canonical,
                after_canonical,
                selected,
                expected_operations,
            )
            assets.append(
                SemanticAssetEvidence(
                    asset_path=asset_path,
                    asset_class=asset_class,
                    before_revision=before_revision
                    or next((item.before_revision for item in operation_evidence if item.before_revision), ""),
                    after_revision=after_revision
                    or next((item.after_revision for item in operation_evidence if item.after_revision), ""),
                    stage_evidence_revision=after_revision
                    or next(
                        (item.stage_evidence_revision for item in operation_evidence if item.stage_evidence_revision),
                        "",
                    ),
                    operations=tuple(operation_evidence),
                    unchanged_critical_fields=tuple(unchanged),
                    analysis_gaps=tuple(gaps),
                    actual_only=tuple(actual_only),
                )
            )

        return analyze_semantic_evidence(
            change_set=change_set,
            requested_stage=stage,
            selected_stage=selected,  # type: ignore[arg-type]
            selection_reason=selection_reason,
            sources=list({(item["kind"], item["id"]): item for item in sources}.values()),
            assets=assets,
            asset_paths=asset_paths,
            include_unchanged=include_unchanged,
            max_changes=max_changes,
            max_output_tokens=max_output_tokens,
            total_asset_count=len(grouped),
        )

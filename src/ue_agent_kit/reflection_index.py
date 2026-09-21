from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .code_index import CODE_PROFILE
from .database import get_metadata, set_metadata, utc_now_iso


REFLECTION_SCHEMA_VERSION = "reflection-1.0"
REFLECTION_PROFILE = "code-reflection"
REFLECTION_SYMBOL_KINDS = ("function", "property")


@dataclass
class ReflectionIndexResult:
    export_path: str
    project_name: str
    matched_types: int = 0
    unmatched_types: int = 0
    updated_types: int = 0
    functions: int = 0
    properties: int = 0
    references: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["valid"] = not self.errors
        return result


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_json(value: str, default: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_export(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("Reflection export root must be a JSON object.")
    if str(payload.get("schemaVersion", "")) != REFLECTION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported reflection schemaVersion: {payload.get('schemaVersion', '')}"
        )
    if str(payload.get("profile", "")) != REFLECTION_PROFILE:
        raise ValueError(f"Unexpected reflection profile: {payload.get('profile', '')}")
    types = payload.get("types")
    if not isinstance(types, list):
        raise ValueError("Reflection export types must be an array.")
    return payload


def _clean_text(value: Any, *, name: str, maximum: int = 4096) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string.")
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters.")
    return cleaned


def _validate_type_record(record: Any, index: int) -> tuple[str, str, str]:
    if not isinstance(record, dict):
        raise ValueError(f"types[{index}] must be an object.")
    stable_id = _clean_text(record.get("stableId", ""), name=f"types[{index}].stableId")
    cpp_name = _clean_text(record.get("cppName", ""), name=f"types[{index}].cppName")
    kind = _clean_text(record.get("kind", ""), name=f"types[{index}].kind", maximum=32)
    if kind not in {"class", "struct", "enum"}:
        raise ValueError(f"types[{index}].kind must be class, struct, or enum.")
    if stable_id != f"cpp:type:{cpp_name}" or not cpp_name:
        raise ValueError(f"types[{index}] has inconsistent C++ type identity.")
    return stable_id, cpp_name, kind


def _owner_row(connection: sqlite3.Connection, stable_id: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT s.id, s.asset_id, s.kind, s.name, s.details_json,
               a.asset_path, a.profile
        FROM symbols AS s
        JOIN assets AS a ON a.id = s.asset_id
        WHERE s.stable_id = ?
        """,
        (stable_id,),
    ).fetchone()


def _target_symbol(
    connection: sqlite3.Connection,
    stable_id: str,
) -> tuple[str, str, str]:
    row = connection.execute(
        """
        SELECT s.stable_id, s.kind, a.asset_path
        FROM symbols AS s
        JOIN assets AS a ON a.id = s.asset_id
        WHERE s.stable_id = ? AND a.profile = ?
        """,
        (stable_id, CODE_PROFILE),
    ).fetchone()
    if row is None:
        return "", "", ""
    return str(row["stable_id"]), str(row["kind"]), str(row["asset_path"])


def _remove_reflection_children(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    owner_stable_id: str,
) -> None:
    rows = connection.execute(
        """
        SELECT stable_id
        FROM symbols
        WHERE asset_id = ?
          AND owner_symbol_id = ?
          AND kind IN ('function', 'property')
          AND (stable_id LIKE 'cpp:function:%' OR stable_id LIKE 'cpp:property:%')
        """,
        (asset_id, owner_stable_id),
    ).fetchall()
    child_ids = [str(row["stable_id"]) for row in rows]
    if child_ids:
        placeholders = ",".join("?" for _ in child_ids)
        connection.execute(
            f"""
            DELETE FROM references_table
            WHERE source_symbol_id IN ({placeholders})
               OR target_symbol_id IN ({placeholders})
            """,
            [*child_ids, *child_ids],
        )
    connection.execute(
        """
        DELETE FROM symbols
        WHERE asset_id = ?
          AND owner_symbol_id = ?
          AND kind IN ('function', 'property')
          AND (stable_id LIKE 'cpp:function:%' OR stable_id LIKE 'cpp:property:%')
        """,
        (asset_id, owner_stable_id),
    )
    connection.execute(
        "DELETE FROM references_table WHERE source_symbol_id = ? AND kind = 'implements'",
        (owner_stable_id,),
    )


def _insert_child_symbol(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    asset_path: str,
    owner_stable_id: str,
    kind: str,
    record: dict[str, Any],
) -> None:
    stable_id = _clean_text(record.get("stableId", ""), name=f"{kind}.stableId")
    name = _clean_text(record.get("name", ""), name=f"{kind}.name")
    if not owner_stable_id.startswith("cpp:type:"):
        raise ValueError(f"Invalid reflected owner identity: {owner_stable_id}")
    owner_cpp_name = owner_stable_id.removeprefix("cpp:type:")
    expected_stable_id = (
        f"cpp:function:{owner_cpp_name}::{name}"
        if kind == "function"
        else f"cpp:property:{owner_cpp_name}::{name}"
    )
    if stable_id != expected_stable_id or not name:
        raise ValueError(f"Invalid reflected {kind} identity: {stable_id}")
    cpp_type = str(record.get("returnType" if kind == "function" else "cppType", "")).strip()
    details = {
        "language": "cpp",
        "evidence": "ue-reflection",
        "reflection": record,
    }
    connection.execute(
        """
        INSERT INTO symbols(
            asset_id,
            stable_id,
            kind,
            name,
            symbol_asset_path,
            owner_symbol_id,
            class_path,
            details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset_id,
            stable_id,
            kind,
            name,
            asset_path,
            owner_stable_id,
            cpp_type,
            _json_dumps(details),
        ),
    )


def _validate_function_parameters(
    *,
    owner_stable_id: str,
    function_record: dict[str, Any],
) -> None:
    if not owner_stable_id.startswith("cpp:type:"):
        raise ValueError(f"Invalid reflected owner identity: {owner_stable_id}")
    owner_cpp_name = owner_stable_id.removeprefix("cpp:type:")
    function_name = _clean_text(function_record.get("name", ""), name="function.name")
    function_stable_id = _clean_text(
        function_record.get("stableId", ""),
        name="function.stableId",
    )
    expected_function_id = f"cpp:function:{owner_cpp_name}::{function_name}"
    if function_stable_id != expected_function_id:
        raise ValueError(f"Invalid reflected function identity: {function_stable_id}")

    parameters = function_record.get("parameters", [])
    if not isinstance(parameters, list):
        raise ValueError(f"Reflected function {function_stable_id} parameters must be an array.")
    seen_parameter_ids: set[str] = set()
    for index, parameter in enumerate(parameters):
        if not isinstance(parameter, dict):
            raise ValueError(
                f"Reflected function {function_stable_id} parameter[{index}] must be an object."
            )
        parameter_name = _clean_text(
            parameter.get("name", ""),
            name=f"parameter[{index}].name",
        )
        parameter_stable_id = _clean_text(
            parameter.get("stableId", ""),
            name=f"parameter[{index}].stableId",
        )
        parameter_owner_id = _clean_text(
            parameter.get("ownerStableId", ""),
            name=f"parameter[{index}].ownerStableId",
        )
        expected_parameter_id = (
            f"cpp:parameter:{owner_cpp_name}::{function_name}::{parameter_name}"
        )
        if parameter_stable_id != expected_parameter_id:
            raise ValueError(
                f"Invalid reflected parameter identity: {parameter_stable_id}"
            )
        if parameter_owner_id != function_stable_id:
            raise ValueError(
                f"Reflected parameter {parameter_stable_id} owner mismatch."
            )
        if parameter_stable_id in seen_parameter_ids:
            raise ValueError(
                f"Duplicate reflected parameter stableId: {parameter_stable_id}"
            )
        seen_parameter_ids.add(parameter_stable_id)


def _merge_type_details(existing_json: str, record: dict[str, Any]) -> str:
    details = _parse_json(existing_json, {})
    if not isinstance(details, dict):
        details = {}
    reflection = {
        key: value
        for key, value in record.items()
        if key not in {"functions", "properties"}
    }
    details["reflection"] = reflection
    details["reflectionEvidence"] = "ue-reflection"
    return _json_dumps(details)


def _upsert_inheritance_reference(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    owner_stable_id: str,
    super_cpp_name: str,
    super_object_path: str,
) -> int:
    if not super_cpp_name:
        return 0
    target_candidate = f"cpp:type:{super_cpp_name}"
    target_symbol_id, target_kind, target_asset_path = _target_symbol(
        connection, target_candidate
    )
    stable_id = f"cpp:inherits:{owner_stable_id}:{super_cpp_name}"
    existing = connection.execute(
        "SELECT details_json FROM references_table WHERE stable_id = ?",
        (stable_id,),
    ).fetchone()
    details: dict[str, Any] = {}
    if existing is not None:
        parsed = _parse_json(str(existing["details_json"]), {})
        if isinstance(parsed, dict):
            details.update(parsed)
    details["reflection"] = {
        "evidence": "ue-reflection",
        "targetStableIdCandidate": target_candidate,
        "superCppName": super_cpp_name,
        "superObjectPath": super_object_path,
        "resolved": bool(target_symbol_id),
    }
    if existing is None:
        connection.execute(
            """
            INSERT INTO references_table(
                asset_id,
                stable_id,
                kind,
                source_symbol_id,
                target_symbol_id,
                target_kind,
                target_name,
                target_asset_path,
                target_path,
                details_json
            ) VALUES (?, ?, 'inherits', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                stable_id,
                owner_stable_id,
                target_symbol_id,
                target_kind,
                super_cpp_name,
                target_asset_path,
                super_object_path,
                _json_dumps(details),
            ),
        )
    else:
        connection.execute(
            """
            UPDATE references_table
            SET target_symbol_id = ?,
                target_kind = ?,
                target_name = ?,
                target_asset_path = ?,
                target_path = ?,
                details_json = ?
            WHERE stable_id = ?
            """,
            (
                target_symbol_id,
                target_kind,
                super_cpp_name,
                target_asset_path,
                super_object_path,
                _json_dumps(details),
                stable_id,
            ),
        )
    return 1


def _insert_interface_references(
    connection: sqlite3.Connection,
    *,
    asset_id: int,
    owner_stable_id: str,
    interfaces: Any,
) -> int:
    if interfaces is None:
        return 0
    if not isinstance(interfaces, list):
        raise ValueError("reflection interfaces must be an array.")
    count = 0
    for index, interface in enumerate(interfaces):
        if not isinstance(interface, dict):
            raise ValueError(f"interfaces[{index}] must be an object.")
        cpp_name = _clean_text(
            interface.get("cppName", ""),
            name=f"interfaces[{index}].cppName",
        )
        stable_candidate = _clean_text(
            interface.get("stableId", ""),
            name=f"interfaces[{index}].stableId",
        )
        if stable_candidate != f"cpp:type:{cpp_name}":
            raise ValueError(f"interfaces[{index}] has inconsistent stableId.")
        target_symbol_id, target_kind, target_asset_path = _target_symbol(
            connection, stable_candidate
        )
        target_path = str(interface.get("objectPath", "")).strip()
        stable_id = f"cpp:implements:{owner_stable_id}:{stable_candidate}"
        details = {
            "language": "cpp",
            "evidence": "ue-reflection",
            "targetStableIdCandidate": stable_candidate,
            "resolved": bool(target_symbol_id),
        }
        connection.execute(
            """
            INSERT INTO references_table(
                asset_id,
                stable_id,
                kind,
                source_symbol_id,
                target_symbol_id,
                target_kind,
                target_name,
                target_asset_path,
                target_path,
                details_json
            ) VALUES (?, ?, 'implements', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                stable_id,
                owner_stable_id,
                target_symbol_id,
                target_kind or "class",
                cpp_name,
                target_asset_path,
                target_path,
                _json_dumps(details),
            ),
        )
        count += 1
    return count


def index_reflection_export(
    connection: sqlite3.Connection,
    export_path: Path,
    *,
    project_key: str = "",
) -> ReflectionIndexResult:
    resolved_export = export_path.expanduser().resolve()
    if not resolved_export.is_file():
        raise FileNotFoundError(f"Reflection export not found: {resolved_export}")

    payload = _load_export(resolved_export)
    project_name = str(payload.get("projectName", "")).strip()
    expected_project = project_key.strip() or get_metadata(connection, "project_key", "").strip()
    if expected_project and project_name and expected_project != project_name:
        raise RuntimeError(
            f"Reflection projectName '{project_name}' does not match project key '{expected_project}'."
        )

    result = ReflectionIndexResult(
        export_path=str(resolved_export),
        project_name=project_name,
    )

    seen_type_ids: set[str] = set()
    with connection:
        for index, raw_record in enumerate(payload["types"]):
            try:
                stable_id, cpp_name, reflected_kind = _validate_type_record(raw_record, index)
                if stable_id in seen_type_ids:
                    raise ValueError(f"Duplicate reflection type stableId: {stable_id}")
                seen_type_ids.add(stable_id)

                owner = _owner_row(connection, stable_id)
                if owner is None:
                    result.unmatched_types += 1
                    result.warnings.append(f"unmatched-type:{stable_id}")
                    continue
                if str(owner["profile"]) != CODE_PROFILE:
                    raise ValueError(
                        f"Reflection type {stable_id} resolves to non-code profile {owner['profile']}."
                    )
                if str(owner["kind"]) != reflected_kind:
                    result.unmatched_types += 1
                    result.warnings.append(
                        f"kind-mismatch:{stable_id}:{owner['kind']}->{reflected_kind}"
                    )
                    continue
            except (ValueError, sqlite3.Error) as exc:
                result.errors.append(f"types[{index}]: {exc}")
                continue

            asset_id = int(owner["asset_id"])
            asset_path = str(owner["asset_path"])
            property_count = 0
            function_count = 0
            reference_count = 0
            connection.execute("SAVEPOINT reflection_type")
            try:
                properties = raw_record.get("properties", [])
                functions = raw_record.get("functions", [])
                if not isinstance(properties, list) or not isinstance(functions, list):
                    raise ValueError(
                        f"Reflection type {stable_id} properties/functions must be arrays."
                    )

                _remove_reflection_children(
                    connection,
                    asset_id=asset_id,
                    owner_stable_id=stable_id,
                )
                connection.execute(
                    "UPDATE symbols SET details_json = ? WHERE id = ?",
                    (
                        _merge_type_details(str(owner["details_json"]), raw_record),
                        int(owner["id"]),
                    ),
                )

                for property_record in properties:
                    if not isinstance(property_record, dict):
                        raise ValueError(f"Reflection property under {stable_id} must be an object.")
                    if str(property_record.get("ownerStableId", "")).strip() != stable_id:
                        raise ValueError(
                            f"Reflection property owner mismatch under {stable_id}."
                        )
                    _insert_child_symbol(
                        connection,
                        asset_id=asset_id,
                        asset_path=asset_path,
                        owner_stable_id=stable_id,
                        kind="property",
                        record=property_record,
                    )
                    property_count += 1

                for function_record in functions:
                    if not isinstance(function_record, dict):
                        raise ValueError(f"Reflection function under {stable_id} must be an object.")
                    if str(function_record.get("ownerStableId", "")).strip() != stable_id:
                        raise ValueError(
                            f"Reflection function owner mismatch under {stable_id}."
                        )
                    _validate_function_parameters(
                        owner_stable_id=stable_id,
                        function_record=function_record,
                    )
                    _insert_child_symbol(
                        connection,
                        asset_id=asset_id,
                        asset_path=asset_path,
                        owner_stable_id=stable_id,
                        kind="function",
                        record=function_record,
                    )
                    function_count += 1

                super_cpp_name = str(raw_record.get("superCppName", "")).strip()
                super_object_path = str(raw_record.get("superObjectPath", "")).strip()
                reference_count += _upsert_inheritance_reference(
                    connection,
                    asset_id=asset_id,
                    owner_stable_id=stable_id,
                    super_cpp_name=super_cpp_name,
                    super_object_path=super_object_path,
                )
                reference_count += _insert_interface_references(
                    connection,
                    asset_id=asset_id,
                    owner_stable_id=stable_id,
                    interfaces=raw_record.get("interfaces", []),
                )
                connection.execute("RELEASE SAVEPOINT reflection_type")
            except (ValueError, sqlite3.Error) as exc:
                connection.execute("ROLLBACK TO SAVEPOINT reflection_type")
                connection.execute("RELEASE SAVEPOINT reflection_type")
                result.errors.append(f"types[{index}]: {exc}")
                continue

            result.properties += property_count
            result.functions += function_count
            result.references += reference_count
            result.matched_types += 1
            result.updated_types += 1

        set_metadata(connection, "last_reflection_project", project_name)
        set_metadata(connection, "last_reflection_schema", str(payload.get("schemaVersion", "")))
        set_metadata(connection, "last_reflection_exporter", str(payload.get("exporterVersion", "")))
        set_metadata(connection, "last_reflection_sha256", _sha256(resolved_export))
        set_metadata(connection, "last_reflection_indexed_at_utc", utc_now_iso())
        set_metadata(connection, "last_reflection_matched_types", str(result.matched_types))
        set_metadata(connection, "last_reflection_unmatched_types", str(result.unmatched_types))

    return result

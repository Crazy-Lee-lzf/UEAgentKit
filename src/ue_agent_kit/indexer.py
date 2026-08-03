from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .database import get_metadata, get_schema_version, set_metadata, utc_now_iso


PROFILE_RANK = {
    "asset-index": 0,
    "index": 0,
    "structure": 1,
    "defaults": 1,
    "logic": 2,
    "ai": 3,
    "full": 4,
}


@dataclass
class AssetIndexResult:
    asset_path: str
    status: str
    reason: str = ""
    symbols: int = 0
    references: int = 0
    graphs: int = 0
    nodes: int = 0


@dataclass
class IndexBuildResult:
    export_root: str
    database: str
    project_key: str = ""
    added: int = 0
    updated: int = 0
    skipped: int = 0
    deleted: int = 0
    failed: int = 0
    assets: list[AssetIndexResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self, *, include_assets: bool = True) -> dict[str, Any]:
        result = asdict(self)
        if not include_assets:
            result.pop("assets", None)
        result["valid"] = self.failed == 0 and not self.errors
        return result


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _windows_extended_path(value: str) -> str:
    separator = chr(92)
    extended_prefix = separator * 2 + "?" + separator
    unc_prefix = separator * 2
    if value.startswith(extended_prefix):
        return value
    if value.startswith(unc_prefix):
        return extended_prefix + "UNC" + separator + value[2:]
    return extended_prefix + value


def _filesystem_path(path: Path) -> str:
    value = str(path.resolve())
    return _windows_extended_path(value) if os.name == "nt" else value


def _is_file(path: Path) -> bool:
    return os.path.isfile(_filesystem_path(path))


def _load_json(path: Path) -> Any:
    with open(_filesystem_path(path), encoding="utf-8-sig") as stream:
        return json.load(stream)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(_filesystem_path(path), "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _resolve_recorded_path(export_root: Path, recorded_path: str, directory_name: str) -> Path | None:
    if not recorded_path:
        return None

    normalized = recorded_path.replace("\\", "/")
    marker = f"/{directory_name}/"
    marker_index = normalized.lower().find(marker.lower())
    if marker_index >= 0:
        relative_path = normalized[marker_index + 1 :]
        relocated_path = export_root / Path(relative_path)
        if _is_file(relocated_path):
            return relocated_path.resolve()

    direct_path = Path(recorded_path).expanduser()
    if _is_file(direct_path):
        return direct_path.resolve()

    return None


def _find_canonical_path(export_root: Path, manifest_entry: dict[str, Any]) -> Path:
    recorded_path = str(manifest_entry.get("jsonPath", ""))
    resolved = _resolve_recorded_path(export_root, recorded_path, "canonical")
    if resolved is not None:
        return resolved

    asset_path = str(manifest_entry.get("assetPath", ""))
    canonical_root = export_root / "canonical"
    for candidate in canonical_root.rglob("*.json"):
        try:
            data = _load_json(candidate)
        except (OSError, json.JSONDecodeError):
            continue
        if str(data.get("assetPath", "")) == asset_path:
            return candidate.resolve()

    raise FileNotFoundError(f"Canonical JSON not found for asset: {asset_path}")


def _find_bpctx_path(export_root: Path, manifest_entry: dict[str, Any], canonical_path: Path) -> Path | None:
    recorded_path = str(manifest_entry.get("bpctxPath", ""))
    resolved = _resolve_recorded_path(export_root, recorded_path, "bpctx")
    if resolved is not None:
        return resolved

    canonical_root = (export_root / "canonical").resolve()
    try:
        relative_path = canonical_path.resolve().relative_to(canonical_root)
    except ValueError:
        return None

    candidate = export_root / "bpctx" / relative_path.with_suffix(".bpctx")
    return candidate.resolve() if _is_file(candidate) else None


def _asset_name(asset_path: str, package_name: str) -> str:
    if "." in asset_path:
        return asset_path.rsplit(".", 1)[1]
    if package_name:
        return package_name.rsplit("/", 1)[-1]
    return asset_path.rsplit("/", 1)[-1]


def _definition_maps(canonical: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    variable_by_guid: dict[str, dict[str, Any]] = {}
    variable_by_name: dict[str, dict[str, Any]] = {}
    for item in canonical.get("variables", []):
        guid = str(item.get("guid", ""))
        name = str(item.get("name", ""))
        if guid:
            variable_by_guid[guid] = item
        if name:
            variable_by_name[name] = item

    component_by_name = {
        str(item.get("name", "")): item
        for item in canonical.get("components", [])
        if item.get("name")
    }
    function_by_name = {
        str(item.get("name", "")): item
        for item in canonical.get("functions", [])
        if item.get("name")
    }
    graph_by_guid: dict[str, dict[str, Any]] = {}
    graph_by_name: dict[str, dict[str, Any]] = {}
    for item in canonical.get("graphs", []):
        guid = str(item.get("guid", ""))
        name = str(item.get("name", ""))
        if guid:
            graph_by_guid[guid] = item
        if name:
            graph_by_name[name] = item

    return {
        "variable_by_guid": variable_by_guid,
        "variable_by_name": variable_by_name,
        "component_by_name": component_by_name,
        "function_by_name": function_by_name,
        "graph_by_guid": graph_by_guid,
        "graph_by_name": graph_by_name,
    }


def _symbol_details(symbol: dict[str, Any], maps: dict[str, dict[str, dict[str, Any]]]) -> dict[str, Any]:
    kind = str(symbol.get("kind", ""))
    name = str(symbol.get("name", ""))
    guid = str(symbol.get("guid", ""))
    definition: dict[str, Any] | None = None

    if kind == "variable":
        definition = maps["variable_by_guid"].get(guid) or maps["variable_by_name"].get(name)
    elif kind == "component":
        definition = maps["component_by_name"].get(name)
    elif kind == "function":
        definition = maps["function_by_name"].get(name)
    elif kind == "graph":
        graph = maps["graph_by_guid"].get(guid) or maps["graph_by_name"].get(name)
        if graph is not None:
            definition = {key: value for key, value in graph.items() if key != "nodes"}

    details: dict[str, Any] = {"symbol": symbol}
    if definition is not None:
        details["definition"] = definition
    return details


def _delete_asset(connection: sqlite3.Connection, asset_id: int) -> None:
    connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))


def _insert_asset(
    connection: sqlite3.Connection,
    canonical: dict[str, Any],
    canonical_path: Path,
    bpctx_path: Path | None,
    export_root: Path,
    canonical_sha256: str,
) -> int:
    revision = canonical.get("revision", {})
    package_name = str(canonical.get("packageName", ""))
    asset_path = str(canonical.get("assetPath", ""))
    if not asset_path:
        raise ValueError(f"Canonical JSON has no assetPath: {canonical_path}")

    cursor = connection.execute(
        """
        INSERT INTO assets(
            asset_path,
            package_name,
            asset_name,
            asset_class,
            blueprint_type,
            parent_class,
            generated_class,
            skeleton_generated_class,
            status,
            revision_value,
            package_guid,
            file_size,
            modified_utc,
            content_sha256,
            package_dirty,
            schema_version,
            exporter_version,
            profile,
            canonical_sha256,
            canonical_relpath,
            bpctx_relpath,
            summary_json,
            indexed_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            asset_path,
            package_name,
            _asset_name(asset_path, package_name),
            str(canonical.get("assetClass", "")),
            str(canonical.get("blueprintType", "")),
            str(canonical.get("parentClass", "")),
            str(canonical.get("generatedClass", "")),
            str(canonical.get("skeletonGeneratedClass", "")),
            int(canonical.get("status", 0)),
            str(revision.get("value", "")),
            str(revision.get("packageGuid", "")),
            int(revision.get("fileSize", 0) or 0),
            str(revision.get("modifiedUtc", "")),
            str(revision.get("contentSha256", "")),
            1 if revision.get("packageDirty") else 0,
            str(canonical.get("schemaVersion", "")),
            str(canonical.get("exporterVersion", "")),
            str(canonical.get("profile", "")),
            canonical_sha256,
            _relative_or_absolute(canonical_path, export_root),
            _relative_or_absolute(bpctx_path, export_root) if bpctx_path else "",
            _json_dumps(canonical.get("summary", {})),
            utc_now_iso(),
        ),
    )
    return int(cursor.lastrowid)


def _insert_symbols(connection: sqlite3.Connection, asset_id: int, canonical: dict[str, Any]) -> int:
    maps = _definition_maps(canonical)
    symbols = canonical.get("symbols", [])
    for symbol in symbols:
        connection.execute(
            """
            INSERT INTO symbols(
                asset_id,
                stable_id,
                kind,
                name,
                symbol_asset_path,
                guid,
                owner_symbol_id,
                parent_symbol_id,
                class_path,
                graph_guid,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                str(symbol.get("id", "")),
                str(symbol.get("kind", "")),
                str(symbol.get("name", "")),
                str(symbol.get("assetPath", "")),
                str(symbol.get("guid", "")),
                str(symbol.get("ownerSymbolId", "")),
                str(symbol.get("parentSymbolId", "")),
                str(symbol.get("class", "")),
                str(symbol.get("graphGuid", "")),
                _json_dumps(_symbol_details(symbol, maps)),
            ),
        )
    return len(symbols)


def _insert_graphs_and_nodes(connection: sqlite3.Connection, asset_id: int, canonical: dict[str, Any]) -> tuple[int, int]:
    graph_count = 0
    node_count = 0
    for graph in canonical.get("graphs", []):
        nodes = graph.get("nodes", [])
        graph_details = {key: value for key, value in graph.items() if key != "nodes"}
        cursor = connection.execute(
            """
            INSERT INTO graphs(asset_id, guid, name, kind, schema_path, node_count, details_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                str(graph.get("guid", "")),
                str(graph.get("name", "")),
                str(graph.get("kind", "")),
                str(graph.get("schema", "")),
                len(nodes),
                _json_dumps(graph_details),
            ),
        )
        graph_id = int(cursor.lastrowid)
        graph_count += 1

        for node in nodes:
            connection.execute(
                """
                INSERT INTO nodes(
                    asset_id,
                    graph_id,
                    graph_guid,
                    guid,
                    object_name,
                    node_class,
                    title,
                    comment,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    graph_id,
                    str(graph.get("guid", "")),
                    str(node.get("guid", "")),
                    str(node.get("name", "")),
                    str(node.get("class", "")),
                    str(node.get("title", "")),
                    str(node.get("comment", "")),
                    _json_dumps(node),
                ),
            )
            node_count += 1

    return graph_count, node_count


def _insert_references(connection: sqlite3.Connection, asset_id: int, canonical: dict[str, Any]) -> int:
    references = canonical.get("references", [])
    for reference in references:
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
                graph_guid,
                graph_name,
                node_guid,
                node_class,
                node_title,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                str(reference.get("id", "")),
                str(reference.get("kind", "")),
                str(reference.get("sourceSymbolId", "")),
                str(reference.get("targetSymbolId", "")),
                str(reference.get("targetKind", "")),
                str(reference.get("targetName", "")),
                str(reference.get("targetAssetPath", "")),
                str(reference.get("targetPath", "")),
                str(reference.get("graphGuid", "")),
                str(reference.get("graphName", "")),
                str(reference.get("nodeGuid", "")),
                str(reference.get("nodeClass", "")),
                str(reference.get("nodeTitle", "")),
                _json_dumps(reference),
            ),
        )
    return len(references)


def _existing_asset(connection: sqlite3.Connection, asset_path: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT id, revision_value, schema_version, exporter_version, profile, canonical_sha256
        FROM assets
        WHERE asset_path = ?
        """,
        (asset_path,),
    ).fetchone()


def _should_skip(existing: sqlite3.Row, canonical: dict[str, Any], canonical_sha256: str, *, force: bool) -> tuple[bool, str]:
    if force:
        return False, ""

    revision_value = str(canonical.get("revision", {}).get("value", ""))
    schema_version = str(canonical.get("schemaVersion", ""))
    exporter_version = str(canonical.get("exporterVersion", ""))
    profile = str(canonical.get("profile", ""))

    exact_match = (
        str(existing["revision_value"]) == revision_value
        and str(existing["schema_version"]) == schema_version
        and str(existing["exporter_version"]) == exporter_version
        and str(existing["profile"]) == profile
        and str(existing["canonical_sha256"]) == canonical_sha256
    )
    if exact_match:
        return True, "unchanged"

    same_asset_revision = (
        revision_value
        and str(existing["revision_value"]) == revision_value
        and str(existing["schema_version"]) == schema_version
        and str(existing["exporter_version"]) == exporter_version
    )
    incoming_rank = PROFILE_RANK.get(profile, -1)
    existing_rank = PROFILE_RANK.get(str(existing["profile"]), -1)
    if same_asset_revision and incoming_rank < existing_rank:
        return True, f"profile-downgrade:{existing['profile']}->{profile}"

    return False, ""


def _prune_assets(
    connection: sqlite3.Connection,
    seen_asset_paths: set[str],
    prefix: str,
) -> list[str]:
    normalized_prefix = prefix.rstrip("/")
    rows = connection.execute(
        "SELECT id, asset_path FROM assets WHERE asset_path = ? OR asset_path LIKE ? ESCAPE '\\'",
        (normalized_prefix, normalized_prefix + "/%"),
    ).fetchall()

    deleted: list[str] = []
    for row in rows:
        asset_path = str(row["asset_path"])
        if asset_path in seen_asset_paths:
            continue
        _delete_asset(connection, int(row["id"]))
        deleted.append(asset_path)
    return deleted


def build_index(
    connection: sqlite3.Connection,
    export_root: Path,
    database_path: Path,
    *,
    force: bool = False,
    prune_prefix: str = "",
    project_key: str = "",
) -> IndexBuildResult:
    export_root = export_root.expanduser().resolve()
    manifest_path = export_root / "manifest.json"
    if not _is_file(manifest_path):
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    manifest = _load_json(manifest_path)
    manifest_assets = manifest.get("assets", [])
    manifest_project_key = str(manifest.get("projectName", "")).strip()
    requested_project_key = project_key.strip()
    if requested_project_key and manifest_project_key and requested_project_key != manifest_project_key:
        raise RuntimeError(
            f"Explicit project key '{requested_project_key}' does not match manifest projectName '{manifest_project_key}'."
        )

    incoming_project_key = requested_project_key or manifest_project_key
    existing_project_key = get_metadata(connection, "project_key", "").strip()
    if existing_project_key and not incoming_project_key:
        raise RuntimeError(
            f"Database is bound to project '{existing_project_key}', but the export has no project identity. "
            "Pass --project-key explicitly when importing legacy exports."
        )
    if existing_project_key and incoming_project_key and existing_project_key != incoming_project_key:
        raise RuntimeError(
            f"Database is bound to project '{existing_project_key}', not '{incoming_project_key}'."
        )
    effective_project_key = incoming_project_key or existing_project_key

    result = IndexBuildResult(
        export_root=str(export_root),
        database=str(database_path.expanduser().resolve()),
        project_key=effective_project_key,
    )
    seen_asset_paths: set[str] = set()

    successful_entries = [entry for entry in manifest_assets if entry.get("success")]
    if int(manifest.get("successCount", 0)) != len(successful_entries):
        result.errors.append("Manifest successCount does not match successful asset entries.")

    for entry in successful_entries:
        asset_path = str(entry.get("assetPath", ""))
        if not asset_path:
            result.failed += 1
            result.errors.append("Manifest contains a successful asset entry with no assetPath.")
            continue
        seen_asset_paths.add(asset_path)

        try:
            canonical_path = _find_canonical_path(export_root, entry)
            canonical = _load_json(canonical_path)
            canonical_asset_path = str(canonical.get("assetPath", ""))
            if canonical_asset_path != asset_path:
                raise ValueError(
                    f"Manifest assetPath does not match Canonical JSON: {asset_path} != {canonical_asset_path}"
                )
            canonical_project_key = str(canonical.get("projectName", "")).strip()
            if effective_project_key and canonical_project_key and canonical_project_key != effective_project_key:
                raise ValueError(
                    f"Canonical projectName '{canonical_project_key}' does not match project key '{effective_project_key}'."
                )
            bpctx_path = _find_bpctx_path(export_root, entry, canonical_path)
            canonical_sha256 = _sha256(canonical_path)
            existing = _existing_asset(connection, asset_path)

            if existing is not None:
                should_skip, reason = _should_skip(existing, canonical, canonical_sha256, force=force)
                if should_skip:
                    result.skipped += 1
                    result.assets.append(
                        AssetIndexResult(
                            asset_path=asset_path,
                            status="skipped",
                            reason=reason,
                            symbols=int(entry.get("symbols", 0)),
                            references=int(entry.get("references", 0)),
                            graphs=int(entry.get("graphs", 0)),
                            nodes=int(entry.get("nodes", 0)),
                        )
                    )
                    continue

            with connection:
                if existing is not None:
                    _delete_asset(connection, int(existing["id"]))
                asset_id = _insert_asset(
                    connection,
                    canonical,
                    canonical_path,
                    bpctx_path,
                    export_root,
                    canonical_sha256,
                )
                symbol_count = _insert_symbols(connection, asset_id, canonical)
                graph_count, node_count = _insert_graphs_and_nodes(connection, asset_id, canonical)
                reference_count = _insert_references(connection, asset_id, canonical)

            if existing is None:
                result.added += 1
                status = "added"
            else:
                result.updated += 1
                status = "updated"
            result.assets.append(
                AssetIndexResult(
                    asset_path=asset_path,
                    status=status,
                    symbols=symbol_count,
                    references=reference_count,
                    graphs=graph_count,
                    nodes=node_count,
                )
            )
        except Exception as exc:
            result.failed += 1
            message = f"{asset_path}: {exc}"
            result.errors.append(message)
            result.assets.append(AssetIndexResult(asset_path=asset_path, status="failed", reason=str(exc)))

    if prune_prefix:
        if int(manifest.get("failureCount", 0)) != 0 or result.failed != 0:
            result.errors.append("Prune was not executed because the export or import contains failures.")
        else:
            with connection:
                deleted_assets = _prune_assets(connection, seen_asset_paths, prune_prefix)
            result.deleted += len(deleted_assets)
            result.assets.extend(
                AssetIndexResult(asset_path=asset_path, status="deleted", reason=f"not present in {prune_prefix}")
                for asset_path in deleted_assets
            )

    with connection:
        set_metadata(connection, "database_schema_version", str(get_schema_version(connection)))
        if effective_project_key:
            set_metadata(connection, "project_key", effective_project_key)
        set_metadata(connection, "last_export_root", str(export_root))
        set_metadata(connection, "last_manifest_schema", str(manifest.get("schemaVersion", "")))
        set_metadata(connection, "last_exporter_version", str(manifest.get("exporterVersion", "")))
        set_metadata(connection, "last_profile", str(manifest.get("profile", "")))
        set_metadata(connection, "last_indexed_at_utc", utc_now_iso())

    return result


def iter_asset_paths(connection: sqlite3.Connection, prefix: str = "") -> Iterable[str]:
    if prefix:
        normalized_prefix = prefix.rstrip("/")
        rows = connection.execute(
            "SELECT asset_path FROM assets WHERE asset_path = ? OR asset_path LIKE ? ORDER BY asset_path",
            (normalized_prefix, normalized_prefix + "/%"),
        )
    else:
        rows = connection.execute("SELECT asset_path FROM assets ORDER BY asset_path")
    for row in rows:
        yield str(row[0])

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import world_index_v2
from .code_index import CODE_PROFILE

from .database import get_metadata, get_schema_version, set_metadata, utc_now_iso
from .text_search import node_search_text, symbol_search_text


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
    world_actors: int = 0


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
    worlds: int = 0
    world_actors: int = 0
    world_repairs: int = 0
    manifest_sha256: str = ""
    export_utc: str = ""
    fast_path_skips: int = 0
    strict_full_checks: int = 0
    transaction_batches: int = 0
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


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
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
        if relocated_path.is_file():
            return relocated_path.resolve()

    direct_path = Path(recorded_path).expanduser()
    if direct_path.is_file():
        return direct_path.resolve()

    return None


def _normalize_sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if len(text) != 64:
        return ""
    return text if all(character in "0123456789abcdef" for character in text) else ""


class _CanonicalPathResolver:
    """Resolve manifest Canonical paths without a full-tree scan per asset.

    The exporter normally writes a usable absolute or relocated ``jsonPath``.
    For legacy/broken manifests we build one asset-path index instead of
    calling ``rglob`` and loading every JSON for every manifest entry.
    """

    def __init__(self, export_root: Path) -> None:
        self.export_root = export_root
        self._resolved: dict[str, Path] = {}
        self._fallback_by_asset: dict[str, Path] | None = None

    def resolve_recorded(self, manifest_entry: dict[str, Any]) -> Path | None:
        recorded_path = str(manifest_entry.get("jsonPath", ""))
        return _resolve_recorded_path(self.export_root, recorded_path, "canonical")

    def resolve(self, manifest_entry: dict[str, Any]) -> Path:
        asset_path = str(manifest_entry.get("assetPath", ""))
        if asset_path in self._resolved:
            return self._resolved[asset_path]

        recorded = self.resolve_recorded(manifest_entry)
        if recorded is not None:
            self._resolved[asset_path] = recorded
            return recorded

        if self._fallback_by_asset is None:
            fallback: dict[str, Path] = {}
            canonical_root = self.export_root / "canonical"
            if canonical_root.is_dir():
                for candidate in sorted(canonical_root.rglob("*.json")):
                    try:
                        data = _load_json(candidate)
                    except (OSError, json.JSONDecodeError):
                        continue
                    if not isinstance(data, dict):
                        continue
                    candidate_asset_path = str(data.get("assetPath", ""))
                    if candidate_asset_path and candidate_asset_path not in fallback:
                        fallback[candidate_asset_path] = candidate.resolve()
            self._fallback_by_asset = fallback

        resolved = self._fallback_by_asset.get(asset_path)
        if resolved is None:
            raise FileNotFoundError(f"Canonical JSON not found for asset: {asset_path}")
        self._resolved[asset_path] = resolved
        return resolved


def _find_canonical_path(
    export_root: Path,
    manifest_entry: dict[str, Any],
    resolver: _CanonicalPathResolver | None = None,
) -> Path:
    return (resolver or _CanonicalPathResolver(export_root)).resolve(manifest_entry)


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
    return candidate.resolve() if candidate.is_file() else None


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
        stable_id = str(symbol.get("id", ""))
        kind = str(symbol.get("kind", ""))
        name = str(symbol.get("name", ""))
        symbol_asset_path = str(symbol.get("assetPath", ""))
        class_path = str(symbol.get("class", ""))
        details_json = _json_dumps(_symbol_details(symbol, maps))
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
                details_json,
                search_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                asset_id,
                stable_id,
                kind,
                name,
                symbol_asset_path,
                str(symbol.get("guid", "")),
                str(symbol.get("ownerSymbolId", "")),
                str(symbol.get("parentSymbolId", "")),
                class_path,
                str(symbol.get("graphGuid", "")),
                details_json,
                symbol_search_text(
                    stable_id=stable_id,
                    name=name,
                    kind=kind,
                    symbol_asset_path=symbol_asset_path,
                    class_path=class_path,
                    details_json=details_json,
                ),
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
            node_guid = str(node.get("guid", ""))
            object_name = str(node.get("name", ""))
            node_class = str(node.get("class", ""))
            title = str(node.get("title", ""))
            comment = str(node.get("comment", ""))
            node_details_json = _json_dumps(node)
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
                    details_json,
                    search_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asset_id,
                    graph_id,
                    str(graph.get("guid", "")),
                    node_guid,
                    object_name,
                    node_class,
                    title,
                    comment,
                    node_details_json,
                    node_search_text(
                        guid=node_guid,
                        title=title,
                        object_name=object_name,
                        node_class=node_class,
                        comment=comment,
                        details_json=node_details_json,
                    ),
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


_EXISTING_ASSET_COLUMNS = """
    SELECT id, asset_path, asset_class, revision_value, schema_version, exporter_version, profile,
           canonical_sha256
    FROM assets
"""


def _existing_asset(connection: sqlite3.Connection, asset_path: str) -> sqlite3.Row | None:
    return connection.execute(
        _EXISTING_ASSET_COLUMNS + " WHERE asset_path = ?",
        (asset_path,),
    ).fetchone()


def _load_existing_assets(
    connection: sqlite3.Connection,
    asset_paths: Iterable[str],
    *,
    chunk_size: int = 400,
) -> dict[str, sqlite3.Row]:
    """Preload existing asset rows in bounded IN(...) queries.

    SQLite's default host-parameter limit is 999; 400 leaves ample headroom and
    keeps each statement below the usual compile/query-plan cost cliff.
    """
    unique_paths = list(dict.fromkeys(path for path in asset_paths if path))
    existing: dict[str, sqlite3.Row] = {}
    size = max(1, min(int(chunk_size), 900))
    for offset in range(0, len(unique_paths), size):
        chunk = unique_paths[offset : offset + size]
        placeholders = ",".join("?" for _ in chunk)
        rows = connection.execute(
            _EXISTING_ASSET_COLUMNS + f" WHERE asset_path IN ({placeholders})",
            tuple(chunk),
        ).fetchall()
        for row in rows:
            existing[str(row["asset_path"])] = row
    return existing


def _fast_manifest_identity(existing: Any, manifest: dict[str, Any]) -> bool:
    """True when the DB row and manifest agree on export identity.

    The fast path never compares a per-asset Canonical revision without opening
    the Canonical file.  It is therefore only valid when the caller supplied an
    explicit trusted change set or the manifest itself carries a Canonical
    SHA-256 attestation.
    """
    schema_version = str(manifest.get("schemaVersion", ""))
    exporter_version = str(manifest.get("exporterVersion", ""))
    return bool(
        schema_version
        and exporter_version
        and str(existing["schema_version"]) == schema_version
        and str(existing["exporter_version"]) == exporter_version
        and str(existing["profile"]) == str(manifest.get("profile", ""))
    )


def _fast_path_reason(
    existing: Any,
    manifest_entry: dict[str, Any],
    manifest: dict[str, Any],
    resolver: _CanonicalPathResolver,
    *,
    changed_asset_paths: set[str] | None,
    use_manifest_digest: bool,
    force: bool,
) -> str:
    """Return a trusted fast-path reason, or an empty string for strict checking."""
    if force or existing is None:
        return ""
    asset_path = str(manifest_entry.get("assetPath", ""))
    if not asset_path or not _fast_manifest_identity(existing, manifest):
        return ""
    if str(manifest_entry.get("assetReaderStatus", "")).strip().lower() == "failed":
        return ""
    entry_revision = str(
        manifest_entry.get("revision")
        or manifest_entry.get("revisionValue")
        or manifest_entry.get("revision_value")
        or ""
    ).strip()
    if entry_revision and entry_revision != str(existing["revision_value"]):
        return ""
    if not str(existing["canonical_sha256"]):
        return ""
    # World V2 rows are maintained separately from the asset row.  Do not
    # fast-skip a map without reading its Canonical and checking/repairing the
    # world rows transactionally.
    if str(existing["asset_class"]).lower().endswith(".world"):
        return ""

    if changed_asset_paths is not None and asset_path not in changed_asset_paths:
        canonical_path = resolver.resolve_recorded(manifest_entry)
        if canonical_path is not None and canonical_path.is_file():
            return "trusted-unchanged"

    if use_manifest_digest:
        attested = _manifest_canonical_sha256(manifest_entry)
        if attested and attested == _normalize_sha256(existing["canonical_sha256"]):
            canonical_path = resolver.resolve_recorded(manifest_entry)
            if canonical_path is not None and canonical_path.is_file():
                return "manifest-digest-unchanged"
    return ""


def _manifest_canonical_sha256(manifest_entry: dict[str, Any]) -> str:
    for key in ("canonicalSha256", "canonical_sha256"):
        normalized = _normalize_sha256(manifest_entry.get(key))
        if normalized:
            return normalized
    return ""


def read_changed_path_attestation(
    path: Path,
    *,
    export_root: Path,
) -> tuple[set[str], str, str]:
    """Read an explicit, trusted changed-path set with manifest provenance.

    This is intentionally an opt-in local fast path.  A plain list of paths is
    not accepted because it cannot prove which export it describes.
    """
    payload = _load_json(path.expanduser().resolve())
    if not isinstance(payload, dict):
        raise ValueError("Changed-path attestation must be a JSON object.")
    if str(payload.get("schemaVersion", "")) != "1.0":
        raise ValueError("Changed-path attestation schemaVersion must be '1.0'.")
    manifest_sha256 = _normalize_sha256(payload.get("manifestSha256"))
    if not manifest_sha256:
        raise ValueError("Changed-path attestation requires manifestSha256.")
    recorded_root = str(payload.get("exportRoot", "")).strip()
    if not recorded_root:
        raise ValueError("Changed-path attestation requires exportRoot.")
    if Path(recorded_root).expanduser().resolve() != export_root.expanduser().resolve():
        raise ValueError("Changed-path attestation exportRoot does not match the export being indexed.")
    raw_paths = payload.get("changedAssetPaths")
    if not isinstance(raw_paths, list):
        raise ValueError("Changed-path attestation requires a changedAssetPaths array.")
    changed_paths = {str(item) for item in raw_paths if str(item)}
    source = str(payload.get("source", "")).strip()
    return changed_paths, manifest_sha256, source


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
        """
        SELECT id, asset_path
        FROM assets
        WHERE profile <> ?
          AND (asset_path = ? OR asset_path LIKE ? ESCAPE '\\')
        """,
        (CODE_PROFILE, normalized_prefix, normalized_prefix + "/%"),
    ).fetchall()

    deleted: list[str] = []
    for row in rows:
        asset_path = str(row["asset_path"])
        if asset_path in seen_asset_paths:
            continue
        _delete_asset(connection, int(row["id"]))
        deleted.append(asset_path)
    if deleted:
        # World V2 rows are keyed by map assetPath, not assets.id, so the asset
        # cascade does not reach them.  Delete them in the same transaction as
        # the assets they belong to (a failed prune rolls back both).
        world_index_v2.prune_worlds(connection, deleted)
    return deleted


class _BatchWriter:
    """Bounded SQLite batches with a savepoint per asset.

    A failed asset rolls back only its own savepoint; previously completed
    assets remain durable or are rolled back as one bounded batch if the outer
    commit fails.  This keeps an asset and its World V2 rows atomic without
    paying one COMMIT per asset.
    """

    def __init__(self, connection: sqlite3.Connection, batch_size: int) -> None:
        self.connection = connection
        self.batch_size = max(1, int(batch_size))
        self.pending = 0
        self.batches = 0
        self.active = False
        self.aborted = False

    def __enter__(self) -> "_BatchWriter":
        self.connection.execute("BEGIN IMMEDIATE")
        self.active = True
        return self

    def begin_asset(self) -> None:
        self.connection.execute("SAVEPOINT ueak_asset")

    def commit_asset(self) -> None:
        try:
            self.connection.execute("RELEASE SAVEPOINT ueak_asset")
            self.pending += 1
            if self.pending >= self.batch_size:
                self.connection.commit()
                self.batches += 1
                self.pending = 0
                self.connection.execute("BEGIN IMMEDIATE")
        except Exception:
            # A failed batch boundary is not an asset-local error.  The caller
            # must stop instead of continuing with an unknown transaction state.
            self.aborted = True
            raise

    def rollback_asset(self) -> None:
        if not self.connection.in_transaction:
            return
        self.connection.execute("ROLLBACK TO SAVEPOINT ueak_asset")
        self.connection.execute("RELEASE SAVEPOINT ueak_asset")

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if not self.active:
            return False
        if exc_type is not None:
            if self.connection.in_transaction:
                self.connection.rollback()
            self.active = False
            return False
        try:
            self.connection.commit()
            self.batches += 1
        except Exception:
            self.aborted = True
            raise
        finally:
            self.active = False
        return False


def build_index(
    connection: sqlite3.Connection,
    export_root: Path,
    database_path: Path,
    *,
    force: bool = False,
    prune_prefix: str = "",
    project_key: str = "",
    batch_size: int = 100,
    changed_asset_paths: set[str] | None = None,
    expected_manifest_sha256: str = "",
    changed_path_source: str = "",
    use_manifest_digest: bool = True,
) -> IndexBuildResult:
    export_root = export_root.expanduser().resolve()
    if not 1 <= int(batch_size) <= 1000:
        raise ValueError("batch_size must be between 1 and 1000.")
    manifest_path = export_root / "manifest.json"
    if not manifest_path.is_file():
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
    manifest_sha256 = _sha256(manifest_path)
    export_utc = str(manifest.get("createdUtc", ""))
    result.manifest_sha256 = manifest_sha256
    result.export_utc = export_utc
    if changed_asset_paths is not None:
        expected_manifest = _normalize_sha256(expected_manifest_sha256)
        if not expected_manifest:
            raise ValueError(
                "A trusted changed-path set requires expected_manifest_sha256 provenance."
            )
        if expected_manifest != manifest_sha256:
            raise RuntimeError(
                "Changed-path attestation manifest SHA-256 does not match the current manifest; "
                "refusing to fast-skip assets."
            )

    successful_entries = [entry for entry in manifest_assets if entry.get("success")]
    if int(manifest.get("successCount", 0)) != len(successful_entries):
        result.errors.append("Manifest successCount does not match successful asset entries.")
    declared_failures = int(manifest.get("failureCount", 0) or 0)
    actual_failures = len(manifest_assets) - len(successful_entries)
    if declared_failures != actual_failures:
        result.errors.append(
            "Manifest failureCount does not match failed asset entries "
            f"({declared_failures} != {actual_failures})."
        )
    if actual_failures:
        result.errors.append(
            f"Export contains {actual_failures} failed asset(s); the index is incomplete for this export."
        )
    declared_reader_failures = int(manifest.get("readerFailureCount", 0) or 0)
    failed_reader_entries = [
        entry
        for entry in manifest_assets
        if str(entry.get("assetReaderStatus", "")).strip().lower() == "failed"
    ]
    if declared_reader_failures != len(failed_reader_entries):
        result.errors.append(
            "Manifest readerFailureCount does not match entries with a failed reader "
            f"({declared_reader_failures} != {len(failed_reader_entries)})."
        )

    resolver = _CanonicalPathResolver(export_root)
    existing_by_path = _load_existing_assets(
        connection,
        (str(entry.get("assetPath", "")) for entry in successful_entries),
    )

    with _BatchWriter(connection, batch_size) as writer:
        for entry in successful_entries:
            asset_path = str(entry.get("assetPath", ""))
            if not asset_path:
                result.failed += 1
                result.errors.append("Manifest contains a successful asset entry with no assetPath.")
                continue
            seen_asset_paths.add(asset_path)

            try:
                existing = existing_by_path.get(asset_path)
                fast_reason = _fast_path_reason(
                    existing,
                    entry,
                    manifest,
                    resolver,
                    changed_asset_paths=changed_asset_paths,
                    use_manifest_digest=use_manifest_digest,
                    force=force,
                )
                if fast_reason:
                    result.skipped += 1
                    result.fast_path_skips += 1
                    result.assets.append(
                        AssetIndexResult(
                            asset_path=asset_path,
                            status="skipped",
                            reason=fast_reason,
                            symbols=int(entry.get("symbols", 0)),
                            references=int(entry.get("references", 0)),
                            graphs=int(entry.get("graphs", 0)),
                            nodes=int(entry.get("nodes", 0)),
                        )
                    )
                    continue

                canonical_path = resolver.resolve(entry)
                canonical = _load_json(canonical_path)
                result.strict_full_checks += 1
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
                canonical_reader_status = str(canonical.get("assetReaderStatus", "")).strip().lower()
                manifest_reader_status = str(entry.get("assetReaderStatus", "")).strip().lower()
                if canonical_reader_status == "failed" or manifest_reader_status == "failed":
                    reader_name = str(canonical.get("assetReader", "") or entry.get("assetReader", ""))
                    reader_error = str(canonical.get("assetReaderError", "") or entry.get("assetReaderError", ""))
                    raise ValueError(
                        "Specialized reader failed; refusing to index incomplete details "
                        f"(reader={reader_name or '<unknown>'} error={reader_error or '<empty>'})"
                    )
                if canonical_reader_status == "success" and not canonical.get("assetDetails"):
                    reader_name = str(canonical.get("assetReader", "") or entry.get("assetReader", ""))
                    raise ValueError(
                        "Specialized reader reported success but assetDetails is empty; refusing to index "
                        f"registry-only data as complete (reader={reader_name or '<unknown>'})"
                    )
                bpctx_path = _find_bpctx_path(export_root, entry, canonical_path)
                canonical_sha256 = _sha256(canonical_path)
                existing = existing_by_path.get(asset_path)

                world_actors = 0
                if existing is not None:
                    should_skip, reason = _should_skip(existing, canonical, canonical_sha256, force=force)
                    if should_skip:
                        # The asset is unchanged, but its World V2 rows may be missing
                        # (an index built before World V2 existed).  Repair just that
                        # gap so an upgrade does not require a forced rebuild.
                        if world_index_v2.is_world_canonical(canonical) and not world_index_v2.world_row_exists(
                            connection, asset_path
                        ):
                            writer.begin_asset()
                            try:
                                if not world_index_v2.world_schema_present(connection):
                                    world_index_v2.ensure_schema(connection)
                                repaired = world_index_v2.ingest_world_in_transaction(
                                    connection,
                                    canonical,
                                    source_path=canonical_path.as_posix(),
                                )
                            except Exception:
                                writer.rollback_asset()
                                raise
                            else:
                                writer.commit_asset()
                            world_actors = int(repaired.get("actors", 0))
                            result.worlds += 1
                            result.world_actors += world_actors
                            result.world_repairs += 1
                            reason = f"{reason};world-index-repaired"
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
                                world_actors=world_actors,
                            )
                        )
                        continue

                writer.begin_asset()
                try:
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
                    # World V2 maintenance shares this transaction: if the world rows
                    # cannot be written, the asset import fails and rolls back instead
                    # of leaving a map whose actors silently describe an older export.
                    if world_index_v2.is_world_canonical(canonical):
                        if not world_index_v2.world_schema_present(connection):
                            world_index_v2.ensure_schema(connection)
                        world_summary = world_index_v2.ingest_world_in_transaction(
                            connection,
                            canonical,
                            source_path=canonical_path.as_posix(),
                        )
                        world_actors = int(world_summary.get("actors", 0))
                except Exception:
                    writer.rollback_asset()
                    raise
                else:
                    writer.commit_asset()

                existing_by_path[asset_path] = {
                    "id": asset_id,
                    "asset_path": asset_path,
                    "asset_class": str(canonical.get("assetClass", "")),
                    "revision_value": str(canonical.get("revision", {}).get("value", "")),
                    "schema_version": str(canonical.get("schemaVersion", "")),
                    "exporter_version": str(canonical.get("exporterVersion", "")),
                    "profile": str(canonical.get("profile", "")),
                    "canonical_sha256": canonical_sha256,
                }
                if existing is None:
                    result.added += 1
                    status = "added"
                else:
                    result.updated += 1
                    status = "updated"
                if world_actors:
                    result.worlds += 1
                    result.world_actors += world_actors
                result.assets.append(
                    AssetIndexResult(
                        asset_path=asset_path,
                        status=status,
                        symbols=symbol_count,
                        references=reference_count,
                        graphs=graph_count,
                        nodes=node_count,
                        world_actors=world_actors,
                    )
                )
            except Exception as exc:
                if writer.aborted:
                    raise
                result.failed += 1
                message = f"{asset_path}: {exc}"
                result.errors.append(message)
                result.assets.append(AssetIndexResult(asset_path=asset_path, status="failed", reason=str(exc)))

    result.transaction_batches = writer.batches

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
        # Record world coverage so query tools can warn about an incomplete
        # import instead of inferring freshness from an existing asset row.
        world_related = (
            result.worlds > 0
            or result.failed > 0
            or bool(result.errors)
            or world_index_v2.world_schema_present(connection)
        )
        if world_related:
            set_metadata(connection, "world_index_last_build_utc", utc_now_iso())
            set_metadata(
                connection,
                "world_index_last_build_complete",
                "1" if not result.errors and result.failed == 0 else "0",
            )
            set_metadata(
                connection,
                "world_index_last_build_incomplete_reasons",
                _json_dumps(result.errors[:16]),
            )
        if effective_project_key:
            set_metadata(connection, "project_key", effective_project_key)
        set_metadata(connection, "last_export_root", str(export_root))
        set_metadata(connection, "last_export_utc", export_utc)
        set_metadata(connection, "last_manifest_sha256", manifest_sha256)
        set_metadata(connection, "last_database_path", str(database_path.expanduser().resolve()))
        set_metadata(connection, "last_manifest_schema", str(manifest.get("schemaVersion", "")))
        set_metadata(connection, "last_exporter_version", str(manifest.get("exporterVersion", "")))
        set_metadata(connection, "last_profile", str(manifest.get("profile", "")))
        set_metadata(connection, "last_index_build_complete", "1" if not result.errors and result.failed == 0 else "0")
        change_mode = (
            "trusted-changed-set"
            if changed_asset_paths is not None
            else ("manifest-digest" if result.fast_path_skips else "strict")
        )
        set_metadata(connection, "last_index_change_mode", change_mode)
        set_metadata(connection, "last_index_fast_path_skips", str(result.fast_path_skips))
        if changed_path_source:
            set_metadata(connection, "last_index_changed_path_source", changed_path_source)
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

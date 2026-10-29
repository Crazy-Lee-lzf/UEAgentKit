"""World Index V2: SQLite side tables for map (World) canonical JSON.

Why this module exists
----------------------
``WorldAssetReader.cpp`` already exports everything a map query needs —
``worldSettings``, per-actor identity/transform/tags/data layers, per-actor and
world-level component class counts, and World Partition actor descriptors with
bounds.  The generic indexer, however, only stores that document as one opaque
blob inside ``symbols.details_json``, so there is no way to ask "which actors in
this map are ``PointLight`` in data layer ``DL_Night``?" without parsing JSON by
hand.

This module adds relationally queryable side tables that are *derived* from the
World canonical JSON.  It is additive: it does not touch ``schema.py`` or
``queries.py``, it creates its own ``world_v2_*`` tables, and it never
fabricates data the exporter did not provide.

Integration (who calls what)
----------------------------
* ``indexer.build_index`` detects a World canonical, calls ``ensure_schema`` when
  the tables are absent, and then ``ingest_world_in_transaction`` **inside the
  same transaction** that writes the ``assets`` row.  A map and its actors are
  therefore committed or rolled back together; prune deletes world rows in the
  same transaction via ``prune_worlds``.
* ``agent_api.IndexQueryService`` serves ``ue_find_world_actors`` /
  ``ue_find_world_partition_actors`` / ``ue_get_world_summary`` through
  ``find_world_actors`` / ``find_world_partition_actors`` / ``world_status``.
* Query paths are strictly read-only: they call ``world_schema_present`` and
  never run DDL, because the MCP server opens the database
  ``readonly=True, immutable=True``.

Design rules
------------
* **Only source-provided fields.**  A field that is absent in the canonical
  document stays ``NULL``/empty rather than being defaulted to a plausible
  value.  ``Actor`` has no component class list -> no component rows are
  written.  ``transform`` is missing -> location columns are ``NULL``.  This is
  what makes "0 components" and "components not exported" distinguishable.
* **World identity is the table key.**  Every actor/descriptor row carries
  ``world_id``, and the natural key is ``(world_id, actor_key)``, so two maps
  that legitimately share an actor GUID (duplicated maps, PIE copies) never
  collide.
* **Idempotent ingest.**  Re-ingesting the same document is a no-op; a new
  revision replaces that world's rows atomically (delete-then-insert); a
  failing document writes nothing at all.
* **Truncation is recorded, not hidden.**  The reader caps actors and
  descriptors at 20000 and flags it; those flags, plus "metadata unavailable" /
  "reader failed" states, are stored and reported as ``partial`` so callers can
  tell a complete map from a clipped one.

Public API
----------
Write/maintenance: ``ensure_schema``, ``world_schema_present``,
``is_world_canonical``, ``world_row_exists``, ``ingest_world``,
``ingest_world_in_transaction``, ``ingest_world_file``, ``delete_world``,
``prune_worlds``.

Read-only: ``get_world``, ``list_worlds``, ``find_world_actors``,
``find_world_partition_actors``, ``world_index_status``,
``orphan_worlds``, ``world_status`` (freshness/coverage),
``index_build_status``.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .database import get_metadata, utc_now_iso
from .queries import normalize_pagination

WORLD_INDEX_V2_SCHEMA_VERSION = 1

WORLD_DETAILS_TYPE = "world"
DEFAULT_LIMIT = 50
MAX_TAGS_PER_ACTOR = 4096
MAX_WORLD_STATUS_ENTRIES = 64

# Coverage states reported to agents.  ``complete`` is the only state that means
# "the rows you are reading describe the asset revision the index currently
# holds, and the export was not truncated".
COVERAGE_COMPLETE = "complete"
COVERAGE_TRUNCATED = "truncated"
COVERAGE_PARTIAL = "partial"
COVERAGE_STALE = "stale"
COVERAGE_WORLD_INDEX_MISSING = "world-index-missing"
COVERAGE_ORPHAN = "orphan"
COVERAGE_UNKNOWN_MAP = "unknown-map"
COVERAGE_INDEX_UNAVAILABLE = "world-index-unavailable"

_COVERAGE_SEVERITY = (
    COVERAGE_INDEX_UNAVAILABLE,
    COVERAGE_UNKNOWN_MAP,
    COVERAGE_ORPHAN,
    COVERAGE_STALE,
    COVERAGE_WORLD_INDEX_MISSING,
    COVERAGE_TRUNCATED,
    COVERAGE_PARTIAL,
    COVERAGE_COMPLETE,
)

WORLD_SCHEMA_TABLES: tuple[str, ...] = (
    "world_v2_meta",
    "world_v2_worlds",
    "world_v2_actors",
    "world_v2_actor_tags",
    "world_v2_actor_data_layers",
    "world_v2_actor_component_classes",
    "world_v2_class_counts",
    "world_v2_world_partition_actors",
    "world_v2_world_partition_actor_tags",
    "world_v2_world_partition_actor_data_layers",
)


class WorldIndexError(RuntimeError):
    """Raised when a document cannot be ingested.

    Always raised before any row is written, so a caller can catch it and keep
    the previous index state intact.
    """


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS world_v2_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_worlds (
        world_id INTEGER PRIMARY KEY,
        asset_path TEXT NOT NULL UNIQUE,
        package_name TEXT NOT NULL DEFAULT '',
        asset_name TEXT NOT NULL DEFAULT '',
        asset_class TEXT NOT NULL DEFAULT '',
        world_type TEXT NOT NULL DEFAULT '',
        persistent_level_path TEXT NOT NULL DEFAULT '',
        persistent_level_package TEXT NOT NULL DEFAULT '',
        using_external_actors INTEGER CHECK (using_external_actors IN (0, 1)),
        revision_value TEXT NOT NULL DEFAULT '',
        package_guid TEXT NOT NULL DEFAULT '',
        package_dirty INTEGER CHECK (package_dirty IN (0, 1)),
        reader_name TEXT NOT NULL DEFAULT '',
        reader_status TEXT NOT NULL DEFAULT '',
        reader_version INTEGER,
        loaded_actor_count INTEGER NOT NULL DEFAULT 0,
        exported_actor_count INTEGER NOT NULL DEFAULT 0,
        actor_list_truncated INTEGER NOT NULL DEFAULT 0 CHECK (actor_list_truncated IN (0, 1)),
        component_count INTEGER NOT NULL DEFAULT 0,
        streaming_level_count INTEGER NOT NULL DEFAULT 0,
        world_settings_available INTEGER CHECK (world_settings_available IN (0, 1)),
        world_partition_available INTEGER CHECK (world_partition_available IN (0, 1)),
        actor_desc_metadata_available INTEGER CHECK (actor_desc_metadata_available IN (0, 1)),
        actor_desc_count INTEGER NOT NULL DEFAULT 0,
        exported_actor_desc_count INTEGER NOT NULL DEFAULT 0,
        actor_desc_list_truncated INTEGER NOT NULL DEFAULT 0 CHECK (actor_desc_list_truncated IN (0, 1)),
        partial INTEGER NOT NULL DEFAULT 0 CHECK (partial IN (0, 1)),
        partial_reasons_json TEXT NOT NULL DEFAULT '[]',
        world_settings_json TEXT NOT NULL DEFAULT '{}',
        world_partition_json TEXT NOT NULL DEFAULT '{}',
        streaming_levels_json TEXT NOT NULL DEFAULT '[]',
        source_canonical_sha256 TEXT NOT NULL DEFAULT '',
        source_path TEXT NOT NULL DEFAULT '',
        ingested_at_utc TEXT NOT NULL,
        revision_updated_at_utc TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_actors (
        world_id INTEGER NOT NULL REFERENCES world_v2_worlds(world_id) ON DELETE CASCADE,
        actor_key TEXT NOT NULL,
        actor_guid TEXT NOT NULL DEFAULT '',
        instance_guid TEXT NOT NULL DEFAULT '',
        object_path TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        class_path TEXT NOT NULL DEFAULT '',
        folder_path TEXT NOT NULL DEFAULT '',
        attach_parent_path TEXT NOT NULL DEFAULT '',
        runtime_grid TEXT NOT NULL DEFAULT '',
        spatially_loaded INTEGER CHECK (spatially_loaded IN (0, 1)),
        editor_only INTEGER CHECK (editor_only IN (0, 1)),
        replicated INTEGER CHECK (replicated IN (0, 1)),
        hidden INTEGER CHECK (hidden IN (0, 1)),
        collision_enabled INTEGER CHECK (collision_enabled IN (0, 1)),
        component_count INTEGER,
        location_x REAL,
        location_y REAL,
        location_z REAL,
        rotation_x REAL,
        rotation_y REAL,
        rotation_z REAL,
        rotation_w REAL,
        scale_x REAL,
        scale_y REAL,
        scale_z REAL,
        root_component_path TEXT NOT NULL DEFAULT '',
        root_component_class_path TEXT NOT NULL DEFAULT '',
        root_mobility TEXT NOT NULL DEFAULT '',
        root_mobility_value INTEGER,
        tags_json TEXT NOT NULL DEFAULT '[]',
        data_layers_json TEXT NOT NULL DEFAULT '[]',
        component_classes_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (world_id, actor_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_actor_tags (
        world_id INTEGER NOT NULL,
        actor_key TEXT NOT NULL,
        tag TEXT NOT NULL,
        PRIMARY KEY (world_id, actor_key, tag),
        FOREIGN KEY (world_id, actor_key) REFERENCES world_v2_actors(world_id, actor_key) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_actor_data_layers (
        world_id INTEGER NOT NULL,
        actor_key TEXT NOT NULL,
        data_layer TEXT NOT NULL,
        PRIMARY KEY (world_id, actor_key, data_layer),
        FOREIGN KEY (world_id, actor_key) REFERENCES world_v2_actors(world_id, actor_key) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_actor_component_classes (
        world_id INTEGER NOT NULL,
        actor_key TEXT NOT NULL,
        class_path TEXT NOT NULL,
        count INTEGER NOT NULL,
        PRIMARY KEY (world_id, actor_key, class_path),
        FOREIGN KEY (world_id, actor_key) REFERENCES world_v2_actors(world_id, actor_key) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_class_counts (
        world_id INTEGER NOT NULL REFERENCES world_v2_worlds(world_id) ON DELETE CASCADE,
        source TEXT NOT NULL CHECK (source IN ('actor', 'component')),
        class_path TEXT NOT NULL,
        count INTEGER NOT NULL,
        PRIMARY KEY (world_id, source, class_path)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_world_partition_actors (
        world_id INTEGER NOT NULL REFERENCES world_v2_worlds(world_id) ON DELETE CASCADE,
        actor_key TEXT NOT NULL,
        actor_guid TEXT NOT NULL DEFAULT '',
        actor_path TEXT NOT NULL DEFAULT '',
        actor_package TEXT NOT NULL DEFAULT '',
        name TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL DEFAULT '',
        base_class_path TEXT NOT NULL DEFAULT '',
        native_class_path TEXT NOT NULL DEFAULT '',
        folder_path TEXT NOT NULL DEFAULT '',
        runtime_grid TEXT NOT NULL DEFAULT '',
        spatially_loaded INTEGER CHECK (spatially_loaded IN (0, 1)),
        editor_only INTEGER CHECK (editor_only IN (0, 1)),
        runtime_only INTEGER CHECK (runtime_only IN (0, 1)),
        runtime_relevant INTEGER CHECK (runtime_relevant IN (0, 1)),
        editor_relevant INTEGER CHECK (editor_relevant IN (0, 1)),
        loaded INTEGER CHECK (loaded IN (0, 1)),
        data_layers_resolved INTEGER CHECK (data_layers_resolved IN (0, 1)),
        location_x REAL,
        location_y REAL,
        location_z REAL,
        rotation_x REAL,
        rotation_y REAL,
        rotation_z REAL,
        rotation_w REAL,
        scale_x REAL,
        scale_y REAL,
        scale_z REAL,
        editor_bounds_valid INTEGER CHECK (editor_bounds_valid IN (0, 1)),
        editor_bounds_json TEXT NOT NULL DEFAULT '{}',
        runtime_bounds_json TEXT NOT NULL DEFAULT '{}',
        tags_json TEXT NOT NULL DEFAULT '[]',
        data_layers_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY (world_id, actor_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_world_partition_actor_tags (
        world_id INTEGER NOT NULL,
        actor_key TEXT NOT NULL,
        tag TEXT NOT NULL,
        PRIMARY KEY (world_id, actor_key, tag),
        FOREIGN KEY (world_id, actor_key)
            REFERENCES world_v2_world_partition_actors(world_id, actor_key) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS world_v2_world_partition_actor_data_layers (
        world_id INTEGER NOT NULL,
        actor_key TEXT NOT NULL,
        data_layer TEXT NOT NULL,
        PRIMARY KEY (world_id, actor_key, data_layer),
        FOREIGN KEY (world_id, actor_key)
            REFERENCES world_v2_world_partition_actors(world_id, actor_key) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS world_v2_actors_class_idx ON world_v2_actors(world_id, class_path)",
    "CREATE INDEX IF NOT EXISTS world_v2_actors_label_idx ON world_v2_actors(world_id, label)",
    "CREATE INDEX IF NOT EXISTS world_v2_actors_folder_idx ON world_v2_actors(world_id, folder_path)",
    "CREATE INDEX IF NOT EXISTS world_v2_actor_tags_tag_idx ON world_v2_actor_tags(tag)",
    "CREATE INDEX IF NOT EXISTS world_v2_actor_layers_layer_idx ON world_v2_actor_data_layers(data_layer)",
    "CREATE INDEX IF NOT EXISTS world_v2_wp_actors_base_class_idx "
    "ON world_v2_world_partition_actors(world_id, base_class_path)",
    "CREATE INDEX IF NOT EXISTS world_v2_wp_actor_tags_tag_idx "
    "ON world_v2_world_partition_actor_tags(tag)",
    "CREATE INDEX IF NOT EXISTS world_v2_wp_actor_layers_layer_idx "
    "ON world_v2_world_partition_actor_data_layers(data_layer)",
)


def ensure_schema(connection: sqlite3.Connection) -> int:
    """Create the ``world_v2_*`` tables if needed.  Returns the schema version.

    Safe to call repeatedly and inside an existing transaction: every statement
    is ``IF NOT EXISTS`` and executed through ``execute`` (never
    ``executescript``, which would implicitly commit).
    """

    for statement in _SCHEMA_STATEMENTS:
        connection.execute(statement)

    row = connection.execute(
        "SELECT value FROM world_v2_meta WHERE key = 'schema_version'"
    ).fetchone()
    if row is None:
        connection.execute(
            "INSERT INTO world_v2_meta(key, value) VALUES ('schema_version', ?)",
            (str(WORLD_INDEX_V2_SCHEMA_VERSION),),
        )
    else:
        try:
            stored = int(str(row[0]))
        except (TypeError, ValueError):
            raise WorldIndexError(f"world_v2 schema_version is not an integer: {row[0]!r}") from None
        if stored > WORLD_INDEX_V2_SCHEMA_VERSION:
            raise WorldIndexError(
                f"world_v2 schema {stored} is newer than supported schema "
                f"{WORLD_INDEX_V2_SCHEMA_VERSION}."
            )
    connection.execute(
        "INSERT INTO world_v2_meta(key, value) VALUES ('ingest_contract', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("world-canonical-json/v1",),
    )
    return WORLD_INDEX_V2_SCHEMA_VERSION


def world_schema_present(connection: sqlite3.Connection) -> bool:
    """Read-only check: are the World V2 tables in this database?

    Query paths must never create tables (the MCP server opens the database
    read-only), so they call this instead of :func:`ensure_schema`.
    """

    placeholders = ", ".join("?" for _ in WORLD_SCHEMA_TABLES)
    try:
        rows = connection.execute(
            f"SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders})",
            WORLD_SCHEMA_TABLES,
        ).fetchall()
    except sqlite3.Error:
        return False
    return len(rows) == len(WORLD_SCHEMA_TABLES)


def world_row_exists(connection: sqlite3.Connection, asset_path: str) -> bool:
    """Read-only check for one map's world row (used to repair old indexes)."""

    if not world_schema_present(connection):
        return False
    return (
        connection.execute(
            "SELECT 1 FROM world_v2_worlds WHERE asset_path = ? LIMIT 1", (asset_path,)
        ).fetchone()
        is not None
    )


def is_world_canonical(canonical: Mapping[str, Any]) -> bool:
    """True when this canonical document carries World reader details.

    Used by the indexer to decide whether World V2 maintenance applies.  Never
    raises: a document without world details is simply not ours.
    """

    try:
        details, _ = _resolve_world_details(_mapping(canonical))
    except WorldIndexError:
        return False
    return _text(details.get("type")) == WORLD_DETAILS_TYPE


# --------------------------------------------------------------------------- #
# Small value helpers (source-faithful: absent stays None)
# --------------------------------------------------------------------------- #


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _bool_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return 1 if value else 0
    return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _names(value: Any) -> tuple[str, ...]:
    """Normalized unique names, preserving source order (for the JSON column)."""

    names: list[str] = []
    for item in _sequence(value):
        if isinstance(item, str):
            name = item.strip()
            if name and name not in names:
                names.append(name)
    return tuple(names)


def _names_sorted(value: Any) -> tuple[str, ...]:
    """Deterministic order for the normalized tag/layer tables."""

    names = list(_names(value))
    names.sort()
    return tuple(names[:MAX_TAGS_PER_ACTOR])


def _class_counts(value: Any) -> tuple[tuple[str, int], ...]:
    """``[{"classPath": ..., "count": ...}]`` -> sorted ``((class, count), ...)``.

    Entries without a class path or with a non-integer count are dropped rather
    than guessed; the raw array stays available in ``*_json`` columns.
    """

    counts: dict[str, int] = {}
    for entry in _sequence(value):
        if not isinstance(entry, Mapping):
            continue
        class_path = _text(entry.get("classPath")).strip()
        count = _int_or_none(entry.get("count"))
        if not class_path or count is None:
            continue
        counts[class_path] = counts.get(class_path, 0) + max(count, 0)
    return tuple(sorted(counts.items()))


@dataclass(frozen=True)
class _Vector:
    x: float | None = None
    y: float | None = None
    z: float | None = None


@dataclass(frozen=True)
class _Quat:
    x: float | None = None
    y: float | None = None
    z: float | None = None
    w: float | None = None


@dataclass(frozen=True)
class _Transform:
    translation: _Vector = field(default_factory=_Vector)
    rotation: _Quat = field(default_factory=_Quat)
    scale: _Vector = field(default_factory=_Vector)


def _vector_from(value: Any) -> _Vector | None:
    if not isinstance(value, Mapping):
        return None
    return _Vector(
        _float_or_none(value.get("x")),
        _float_or_none(value.get("y")),
        _float_or_none(value.get("z")),
    )


def _transform_from(value: Any) -> _Transform | None:
    if not isinstance(value, Mapping):
        return None
    rotation_value = value.get("rotation")
    rotation = _Quat()
    if isinstance(rotation_value, Mapping):
        rotation = _Quat(
            _float_or_none(rotation_value.get("x")),
            _float_or_none(rotation_value.get("y")),
            _float_or_none(rotation_value.get("z")),
            _float_or_none(rotation_value.get("w")),
        )
    return _Transform(
        translation=_vector_from(value.get("translation")) or _Vector(),
        rotation=rotation,
        scale=_vector_from(value.get("scale")) or _Vector(),
    )


# --------------------------------------------------------------------------- #
# Parsing (pure: no I/O, no SQL)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ParsedActor:
    row: dict[str, Any]
    tags: tuple[str, ...]
    sorted_tags: tuple[str, ...]
    data_layers: tuple[str, ...]
    sorted_data_layers: tuple[str, ...]
    component_classes: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ParsedDescriptor:
    row: dict[str, Any]
    sorted_tags: tuple[str, ...]
    sorted_data_layers: tuple[str, ...]


@dataclass(frozen=True)
class ParsedWorld:
    world: dict[str, Any]
    actors: tuple[ParsedActor, ...]
    descriptors: tuple[ParsedDescriptor, ...]
    class_counts: tuple[tuple[str, str, int], ...]
    flags: dict[str, Any]
    details: dict[str, Any]
    details_sha256: str


_WORLD_COLUMNS: tuple[str, ...] = (
    "package_name",
    "asset_name",
    "asset_class",
    "world_type",
    "persistent_level_path",
    "persistent_level_package",
    "using_external_actors",
    "revision_value",
    "package_guid",
    "package_dirty",
    "reader_name",
    "reader_status",
    "reader_version",
    "loaded_actor_count",
    "exported_actor_count",
    "actor_list_truncated",
    "component_count",
    "streaming_level_count",
    "world_settings_available",
    "world_partition_available",
    "actor_desc_metadata_available",
    "actor_desc_count",
    "exported_actor_desc_count",
    "actor_desc_list_truncated",
    "partial",
    "partial_reasons_json",
    "world_settings_json",
    "world_partition_json",
    "streaming_levels_json",
    "source_canonical_sha256",
    "source_path",
)

_ACTOR_COLUMNS: tuple[str, ...] = (
    "actor_key",
    "actor_guid",
    "instance_guid",
    "object_path",
    "name",
    "label",
    "class_path",
    "folder_path",
    "attach_parent_path",
    "runtime_grid",
    "spatially_loaded",
    "editor_only",
    "replicated",
    "hidden",
    "collision_enabled",
    "component_count",
    "location_x",
    "location_y",
    "location_z",
    "rotation_x",
    "rotation_y",
    "rotation_z",
    "rotation_w",
    "scale_x",
    "scale_y",
    "scale_z",
    "root_component_path",
    "root_component_class_path",
    "root_mobility",
    "root_mobility_value",
    "tags_json",
    "data_layers_json",
    "component_classes_json",
)

_DESCRIPTOR_COLUMNS: tuple[str, ...] = (
    "actor_key",
    "actor_guid",
    "actor_path",
    "actor_package",
    "name",
    "label",
    "base_class_path",
    "native_class_path",
    "folder_path",
    "runtime_grid",
    "spatially_loaded",
    "editor_only",
    "runtime_only",
    "runtime_relevant",
    "editor_relevant",
    "loaded",
    "data_layers_resolved",
    "location_x",
    "location_y",
    "location_z",
    "rotation_x",
    "rotation_y",
    "rotation_z",
    "rotation_w",
    "scale_x",
    "scale_y",
    "scale_z",
    "editor_bounds_valid",
    "editor_bounds_json",
    "runtime_bounds_json",
    "tags_json",
    "data_layers_json",
)


def _resolve_world_details(canonical: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Find the world details inside a canonical document (or accept one directly).

    Accepted shapes, in order:
    1. a bare details object (``{"type": "world", ...}``);
    2. a full canonical document with ``assetDetails.type == "world"``;
    3. a canonical document whose ``symbols`` contain the asset symbol whose
       ``assetDetails.type == "world"`` (this is the shape the generic indexer
       stores in ``symbols.details_json``).

    Returns ``(details, asset_symbol)`` where ``asset_symbol`` is empty when the
    document had no symbols.
    """

    if not isinstance(canonical, Mapping):
        raise WorldIndexError("world canonical document must be a JSON object")

    if _text(canonical.get("type")) == WORLD_DETAILS_TYPE:
        return _mapping(canonical), {}

    details = _mapping(canonical.get("assetDetails"))
    if _text(details.get("type")) == WORLD_DETAILS_TYPE:
        return details, {}

    # ``{"symbol": {...}}`` is exactly one row of symbols.details_json, so a
    # caller can re-ingest straight out of the existing asset index.
    single_symbol = canonical.get("symbol")
    if isinstance(single_symbol, Mapping):
        symbol_details = _mapping(single_symbol.get("assetDetails"))
        if _text(symbol_details.get("type")) == WORLD_DETAILS_TYPE:
            return symbol_details, dict(single_symbol)

    for symbol in _sequence(canonical.get("symbols")):
        if not isinstance(symbol, Mapping):
            continue
        symbol_details = _mapping(symbol.get("assetDetails"))
        if _text(symbol_details.get("type")) == WORLD_DETAILS_TYPE:
            return symbol_details, dict(symbol)

    asset_class = _text(canonical.get("assetClass"))
    if asset_class and asset_class.rsplit(".", 1)[-1].lstrip("A").lower() == "world":
        raise WorldIndexError(
            f"{_text(canonical.get('assetPath')) or '<unknown>'} looks like a World asset but "
            "its canonical JSON has no world assetDetails; re-export it with a reader that "
            "succeeded (assetReaderStatus=success) before ingesting."
        )
    raise WorldIndexError(
        "canonical JSON does not contain world assetDetails "
        "(expected assetDetails.type == 'world')"
    )


def _required_array(container: Mapping[str, Any], key: str, label: str) -> list[Any]:
    """``container[key]`` must be an array (or absent); anything else is a shape error."""

    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise WorldIndexError(f"{label} must be an array")
    return value


def parse_world_canonical(canonical: Mapping[str, Any], *, source_path: str = "") -> ParsedWorld:
    """Validate and normalize a World canonical document.

    Pure function: raises :class:`WorldIndexError` for anything it cannot store
    faithfully, and never mutates its input.
    """

    details, asset_symbol = _resolve_world_details(canonical)
    root = canonical if _text(canonical.get("type")) != WORLD_DETAILS_TYPE else {}

    asset_path = _text(root.get("assetPath")) or _text(asset_symbol.get("assetPath"))
    if not asset_path and _text(root.get("packageName")):
        asset_name = _text(root.get("assetName"))
        asset_path = f"{_text(root.get('packageName'))}.{asset_name}" if asset_name else _text(
            root.get("packageName")
        )
    if not asset_path:
        raise WorldIndexError(
            "world canonical JSON has no assetPath; the ingest key must be explicit "
            "(pass source_path only for diagnostics, not identity)"
        )

    package_name = _text(root.get("packageName"))
    asset_name = _text(root.get("assetName"))
    if not package_name and "." in asset_path:
        package_name = asset_path.rsplit(".", 1)[0]
    if not asset_name:
        asset_name = asset_path.rsplit(".", 1)[-1].rsplit("/", 1)[-1]

    actors_input = _required_array(details, "actors", "world assetDetails.actors")

    actors: list[ParsedActor] = []
    seen_actor_keys: set[str] = set()
    for index, actor in enumerate(actors_input):
        if not isinstance(actor, Mapping):
            raise WorldIndexError(f"world assetDetails.actors[{index}] must be an object")
        guid = _text(actor.get("guid")).strip()
        object_path = _text(actor.get("objectPath")).strip()
        actor_key = guid or (f"path:{object_path}" if object_path else "")
        if not actor_key:
            raise WorldIndexError(
                f"world assetDetails.actors[{index}] has neither guid nor objectPath; "
                "the actor cannot be identified"
            )
        if actor_key in seen_actor_keys:
            raise WorldIndexError(
                f"world assetDetails.actors[{index}] duplicates actor key {actor_key!r}"
            )
        seen_actor_keys.add(actor_key)

        transform = _transform_from(actor.get("transform"))
        translation = transform.translation if transform else _Vector()
        rotation = transform.rotation if transform else _Quat()
        scale = transform.scale if transform else _Vector()
        component_classes = _class_counts(actor.get("componentClasses"))
        tags = _names(actor.get("tags"))
        data_layers = _names(actor.get("dataLayers"))

        row = {
            "actor_key": actor_key,
            "actor_guid": guid,
            "instance_guid": _text(actor.get("instanceGuid")),
            "object_path": object_path,
            "name": _text(actor.get("name")),
            "label": _text(actor.get("label")),
            "class_path": _text(actor.get("classPath")),
            "folder_path": _text(actor.get("folderPath")),
            "attach_parent_path": _text(actor.get("attachParentPath")),
            "runtime_grid": _text(actor.get("runtimeGrid")),
            "spatially_loaded": _bool_or_none(actor.get("spatiallyLoaded")),
            "editor_only": _bool_or_none(actor.get("editorOnly")),
            "replicated": _bool_or_none(actor.get("replicated")),
            "hidden": _bool_or_none(actor.get("hidden")),
            "collision_enabled": _bool_or_none(actor.get("collisionEnabled")),
            "component_count": _int_or_none(actor.get("componentCount")),
            "location_x": translation.x,
            "location_y": translation.y,
            "location_z": translation.z,
            "rotation_x": rotation.x,
            "rotation_y": rotation.y,
            "rotation_z": rotation.z,
            "rotation_w": rotation.w,
            "scale_x": scale.x,
            "scale_y": scale.y,
            "scale_z": scale.z,
            "root_component_path": _text(actor.get("rootComponentPath")),
            "root_component_class_path": _text(actor.get("rootComponentClassPath")),
            "root_mobility": _text(actor.get("rootMobility")),
            "root_mobility_value": _int_or_none(actor.get("rootMobilityValue")),
            "tags_json": _dumps(list(tags)),
            "data_layers_json": _dumps(list(data_layers)),
            "component_classes_json": _dumps(
                [{"classPath": class_path, "count": count} for class_path, count in component_classes]
            ),
        }
        actors.append(
            ParsedActor(
                row=row,
                tags=tags,
                sorted_tags=_names_sorted(actor.get("tags")),
                data_layers=data_layers,
                sorted_data_layers=_names_sorted(actor.get("dataLayers")),
                component_classes=component_classes,
            )
        )

    world_partition = _mapping(details.get("worldPartition"))
    descriptors_input = _required_array(
        world_partition, "actorDescs", "worldPartition.actorDescs"
    )

    descriptors: list[ParsedDescriptor] = []
    seen_descriptor_keys: set[str] = set()
    for index, descriptor in enumerate(descriptors_input):
        if not isinstance(descriptor, Mapping):
            raise WorldIndexError(f"worldPartition.actorDescs[{index}] must be an object")
        guid = _text(descriptor.get("guid")).strip()
        actor_path = _text(descriptor.get("actorPath")).strip()
        actor_key = guid or (f"path:{actor_path}" if actor_path else "")
        if not actor_key:
            raise WorldIndexError(
                f"worldPartition.actorDescs[{index}] has neither guid nor actorPath"
            )
        if actor_key in seen_descriptor_keys:
            raise WorldIndexError(
                f"worldPartition.actorDescs[{index}] duplicates descriptor key {actor_key!r}"
            )
        seen_descriptor_keys.add(actor_key)

        transform = _transform_from(descriptor.get("transform"))
        translation = transform.translation if transform else _Vector()
        rotation = transform.rotation if transform else _Quat()
        scale = transform.scale if transform else _Vector()
        editor_bounds = _mapping(descriptor.get("editorBounds"))

        row = {
            "actor_key": actor_key,
            "actor_guid": guid,
            "actor_path": actor_path,
            "actor_package": _text(descriptor.get("actorPackage")),
            "name": _text(descriptor.get("name")),
            "label": _text(descriptor.get("label")),
            "base_class_path": _text(descriptor.get("baseClassPath")),
            "native_class_path": _text(descriptor.get("nativeClassPath")),
            "folder_path": _text(descriptor.get("folderPath")),
            "runtime_grid": _text(descriptor.get("runtimeGrid")),
            "spatially_loaded": _bool_or_none(descriptor.get("spatiallyLoaded")),
            "editor_only": _bool_or_none(descriptor.get("editorOnly")),
            "runtime_only": _bool_or_none(descriptor.get("runtimeOnly")),
            "runtime_relevant": _bool_or_none(descriptor.get("runtimeRelevant")),
            "editor_relevant": _bool_or_none(descriptor.get("editorRelevant")),
            "loaded": _bool_or_none(descriptor.get("loaded")),
            "data_layers_resolved": _bool_or_none(descriptor.get("dataLayersResolved")),
            "location_x": translation.x,
            "location_y": translation.y,
            "location_z": translation.z,
            "rotation_x": rotation.x,
            "rotation_y": rotation.y,
            "rotation_z": rotation.z,
            "rotation_w": rotation.w,
            "scale_x": scale.x,
            "scale_y": scale.y,
            "scale_z": scale.z,
            "editor_bounds_valid": _bool_or_none(editor_bounds.get("valid")),
            "editor_bounds_json": _dumps(editor_bounds) if editor_bounds else "{}",
            "runtime_bounds_json": _dumps(_mapping(descriptor.get("runtimeBounds")))
            if descriptor.get("runtimeBounds") is not None
            else "{}",
            "tags_json": _dumps(list(_names(descriptor.get("tags")))),
            # Unresolved descriptors carry raw data-layer references.  Keep them
            # queryable instead of dropping them just because the commandlet did
            # not have a UDataLayerManager to resolve display names.
            "data_layers_json": _dumps(list(_names(descriptor.get("dataLayers")))),
        }
        descriptors.append(
            ParsedDescriptor(
                row=row,
                sorted_tags=_names_sorted(descriptor.get("tags")),
                sorted_data_layers=_names_sorted(descriptor.get("dataLayers")),
            )
        )

    loaded_actor_count = _int_or_none(details.get("loadedActorCount")) or 0
    exported_actor_count = (
        _int_or_none(details.get("exportedActorCount")) or len(actors)
    )
    actor_list_truncated_raw = _bool_or_none(details.get("actorListTruncated"))
    actor_list_truncated = (
        bool(actor_list_truncated_raw)
        if actor_list_truncated_raw is not None
        else (loaded_actor_count > len(actors) if loaded_actor_count else False)
    )
    actor_desc_count = _int_or_none(world_partition.get("actorDescCount")) or len(descriptors)
    exported_actor_desc_count = (
        _int_or_none(world_partition.get("exportedActorDescCount")) or len(descriptors)
    )
    actor_desc_truncated_raw = _bool_or_none(world_partition.get("actorDescListTruncated"))
    actor_desc_list_truncated = (
        bool(actor_desc_truncated_raw)
        if actor_desc_truncated_raw is not None
        else (actor_desc_count > len(descriptors) if actor_desc_count else False)
    )

    class_counts: list[tuple[str, str, int]] = []
    for class_path, count in _class_counts(details.get("actorClassCounts")):
        class_counts.append(("actor", class_path, count))
    for class_path, count in _class_counts(details.get("componentClassCounts")):
        class_counts.append(("component", class_path, count))

    revision = _mapping(root.get("revision"))
    reader_status = _text(root.get("assetReaderStatus")) or _text(asset_symbol.get("assetReaderStatus"))
    reader_name = _text(root.get("assetReader")) or _text(asset_symbol.get("assetReader"))

    partial_reasons: list[str] = []
    if reader_status and reader_status != "success":
        partial_reasons.append(f"reader-status:{reader_status}")
    if actor_list_truncated:
        partial_reasons.append("actors-truncated")
    if actor_desc_list_truncated:
        partial_reasons.append("actor-descs-truncated")
    world_partition_available = _bool_or_none(world_partition.get("available"))
    actor_desc_metadata_available = _bool_or_none(
        world_partition.get("actorDescMetadataAvailable")
    )
    if world_partition_available == 1 and actor_desc_metadata_available != 1:
        partial_reasons.append("actor-desc-metadata-unavailable")
    if (
        world_partition_available == 1
        and actor_desc_metadata_available == 1
        and actor_desc_count == 0
        and _bool_or_none(details.get("usingExternalActors")) == 1
    ):
        # An empty descriptor list is valid for a genuinely empty map.  It is
        # suspicious when the persistent level itself says it uses external
        # actors, because those actors must have descriptors.  Do not infer an
        # expected count from package counts; fail closed instead.
        partial_reasons.append("actor-desc-empty-with-external-actors")
    external_actor_package_coverage = _text(
        world_partition.get("externalActorPackageCoverage")
    )
    if world_partition_available == 1 and _bool_or_none(details.get("usingExternalActors")) == 1:
        if external_actor_package_coverage == "unmatched":
            # AssetRegistry knows packages that the official descriptor
            # enumerator did not represent.  They may be unresolved classes or
            # stale/orphan packages; either way the export must not be labelled
            # complete until a native probe explains them.
            partial_reasons.append("actor-desc-external-packages-unmatched")
        elif external_actor_package_coverage == "unknown":
            partial_reasons.append("actor-desc-external-package-inventory-unavailable")
    if not actors and loaded_actor_count:
        partial_reasons.append("no-actors-exported")
    if not details:
        partial_reasons.append("details-empty")

    revision_value = _text(revision.get("value"))
    world_row = {
        "package_name": package_name,
        "asset_name": asset_name,
        "asset_class": _text(root.get("assetClass")),
        "world_type": _text(details.get("worldType")),
        "persistent_level_path": _text(details.get("persistentLevelPath")),
        "persistent_level_package": _text(details.get("persistentLevelPackage")),
        "using_external_actors": _bool_or_none(details.get("usingExternalActors")),
        "revision_value": revision_value,
        "package_guid": _text(revision.get("packageGuid")),
        "package_dirty": _bool_or_none(revision.get("packageDirty")),
        "reader_name": reader_name,
        "reader_status": reader_status,
        "reader_version": _int_or_none(details.get("readerVersion")),
        "loaded_actor_count": loaded_actor_count,
        "exported_actor_count": exported_actor_count,
        "actor_list_truncated": 1 if actor_list_truncated else 0,
        "component_count": _int_or_none(details.get("componentCount")) or 0,
        "streaming_level_count": _int_or_none(details.get("streamingLevelCount")) or 0,
        "world_settings_available": _bool_or_none(_mapping(details.get("worldSettings")).get("available")),
        "world_partition_available": world_partition_available,
        "actor_desc_metadata_available": _bool_or_none(
            world_partition.get("actorDescMetadataAvailable")
        ),
        "actor_desc_count": actor_desc_count,
        "exported_actor_desc_count": exported_actor_desc_count,
        "actor_desc_list_truncated": 1 if actor_desc_list_truncated else 0,
        "partial": 1 if partial_reasons else 0,
        "partial_reasons_json": _dumps(partial_reasons),
        "world_settings_json": _dumps(_mapping(details.get("worldSettings"))),
        "world_partition_json": _dumps(world_partition),
        "streaming_levels_json": _dumps(_sequence(details.get("streamingLevels"))),
        "source_canonical_sha256": "",
        "source_path": source_path,
    }

    details_sha256 = hashlib.sha256(_dumps(details).encode("utf-8")).hexdigest()
    world_row["source_canonical_sha256"] = details_sha256

    return ParsedWorld(
        world=world_row,
        actors=tuple(actors),
        descriptors=tuple(descriptors),
        class_counts=tuple(class_counts),
        flags={
            "actor_list_truncated": bool(actor_list_truncated),
            "actor_desc_list_truncated": bool(actor_desc_list_truncated),
            "partial": bool(partial_reasons),
            "partial_reasons": partial_reasons,
            "loaded_actor_count": loaded_actor_count,
            "exported_actor_count": exported_actor_count,
            "actor_desc_count": actor_desc_count,
            "exported_actor_desc_count": exported_actor_desc_count,
        },
        details=details,
        details_sha256=details_sha256,
    )


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #


def _insert_row(
    connection: sqlite3.Connection,
    table: str,
    columns: Sequence[str],
    values: Sequence[Any],
    *,
    prefix_columns: Sequence[str] = (),
    prefix_values: Sequence[Any] = (),
) -> None:
    all_columns = list(prefix_columns) + list(columns)
    placeholders = ", ".join("?" for _ in all_columns)
    connection.execute(
        f"INSERT INTO {table}({', '.join(all_columns)}) VALUES ({placeholders})",
        [*prefix_values, *values],
    )


def _delete_world_rows(connection: sqlite3.Connection, world_id: int) -> None:
    """Delete a world and every dependent row (explicit, no FK reliance)."""

    for table in (
        "world_v2_actor_component_classes",
        "world_v2_actor_tags",
        "world_v2_actor_data_layers",
        "world_v2_world_partition_actor_tags",
        "world_v2_world_partition_actor_data_layers",
    ):
        connection.execute(f"DELETE FROM {table} WHERE world_id = ?", (world_id,))
    for table in (
        "world_v2_actors",
        "world_v2_world_partition_actors",
        "world_v2_class_counts",
    ):
        connection.execute(f"DELETE FROM {table} WHERE world_id = ?", (world_id,))
    connection.execute("DELETE FROM world_v2_worlds WHERE world_id = ?", (world_id,))


def _ingest_parsed(
    connection: sqlite3.Connection,
    parsed: ParsedWorld,
    *,
    asset_path: str,
) -> dict[str, Any]:
    """Write one parsed world inside the caller's transaction.  Returns detail counts."""

    ingested_at = utc_now_iso()
    existing = connection.execute(
        "SELECT world_id, revision_value, source_canonical_sha256 FROM world_v2_worlds WHERE asset_path = ?",
        (asset_path,),
    ).fetchone()

    revision_value = str(parsed.world.get("revision_value", ""))
    revision_changed = existing is not None and str(existing["revision_value"]) != revision_value

    if existing is not None:
        world_id = int(existing["world_id"])
        previous_revision_updated = connection.execute(
            "SELECT revision_updated_at_utc FROM world_v2_worlds WHERE world_id = ?", (world_id,)
        ).fetchone()[0]
        _delete_world_rows(connection, world_id)
        # ``revision_updated_at_utc`` only moves when the revision really changed.
        revision_updated_at = (
            ingested_at
            if revision_changed or not previous_revision_updated
            else str(previous_revision_updated)
        )
        connection.execute(
            f"INSERT INTO world_v2_worlds(world_id, asset_path, {', '.join(_WORLD_COLUMNS)}, "
            f"ingested_at_utc, revision_updated_at_utc) VALUES (?, ?, "
            f"{', '.join('?' for _ in _WORLD_COLUMNS)}, ?, ?)",
            [
                world_id,
                asset_path,
                *[parsed.world[column] for column in _WORLD_COLUMNS],
                ingested_at,
                revision_updated_at,
            ],
        )
    else:
        cursor = connection.execute(
            f"INSERT INTO world_v2_worlds(asset_path, {', '.join(_WORLD_COLUMNS)}, "
            f"ingested_at_utc, revision_updated_at_utc) VALUES (?, "
            f"{', '.join('?' for _ in _WORLD_COLUMNS)}, ?, ?)",
            [
                asset_path,
                *[parsed.world[column] for column in _WORLD_COLUMNS],
                ingested_at,
                ingested_at,
            ],
        )
        world_id = int(cursor.lastrowid)

    tag_rows = 0
    for actor in parsed.actors:
        _insert_row(
            connection,
            "world_v2_actors",
            _ACTOR_COLUMNS,
            [actor.row[column] for column in _ACTOR_COLUMNS],
            prefix_columns=("world_id",),
            prefix_values=(world_id,),
        )
        for tag in actor.sorted_tags:
            tag_rows += int(
                connection.execute(
                    "INSERT OR IGNORE INTO world_v2_actor_tags(world_id, actor_key, tag) "
                    "VALUES (?, ?, ?)",
                    (world_id, actor.row["actor_key"], tag),
                ).rowcount
                or 0
            )
        for data_layer in actor.sorted_data_layers:
            connection.execute(
                "INSERT OR IGNORE INTO world_v2_actor_data_layers(world_id, actor_key, data_layer) "
                "VALUES (?, ?, ?)",
                (world_id, actor.row["actor_key"], data_layer),
            )
        for class_path, count in actor.component_classes:
            connection.execute(
                "INSERT OR IGNORE INTO world_v2_actor_component_classes"
                "(world_id, actor_key, class_path, count) VALUES (?, ?, ?, ?)",
                (world_id, actor.row["actor_key"], class_path, count),
            )

    component_class_rows = 0
    for actor in parsed.actors:
        component_class_rows += len(actor.component_classes)

    for source, class_path, count in parsed.class_counts:
        connection.execute(
            "INSERT OR IGNORE INTO world_v2_class_counts(world_id, source, class_path, count) "
            "VALUES (?, ?, ?, ?)",
            (world_id, source, class_path, count),
        )

    descriptor_rows = 0
    for descriptor in parsed.descriptors:
        _insert_row(
            connection,
            "world_v2_world_partition_actors",
            _DESCRIPTOR_COLUMNS,
            [descriptor.row[column] for column in _DESCRIPTOR_COLUMNS],
            prefix_columns=("world_id",),
            prefix_values=(world_id,),
        )
        descriptor_rows += 1
        for tag in descriptor.sorted_tags:
            connection.execute(
                "INSERT OR IGNORE INTO world_v2_world_partition_actor_tags(world_id, actor_key, tag) "
                "VALUES (?, ?, ?)",
                (world_id, descriptor.row["actor_key"], tag),
            )
        for data_layer in descriptor.sorted_data_layers:
            connection.execute(
                "INSERT OR IGNORE INTO world_v2_world_partition_actor_data_layers"
                "(world_id, actor_key, data_layer) VALUES (?, ?, ?)",
                (world_id, descriptor.row["actor_key"], data_layer),
            )

    return {
        "world_id": world_id,
        "revision_changed": revision_changed,
        "actors": len(parsed.actors),
        "actor_tags": tag_rows,
        "component_class_rows": component_class_rows,
        "descriptors": descriptor_rows,
        "class_count_rows": len(parsed.class_counts),
    }


def ingest_world(
    connection: sqlite3.Connection,
    canonical: Mapping[str, Any],
    *,
    source_path: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Ingest one World canonical document.  Idempotent and atomic.

    ``canonical`` may be the full canonical JSON, a document whose ``symbols``
    (or single ``symbol``) carry the world ``assetDetails`` — that single-symbol
    shape is exactly one row of ``symbols.details_json``, so the existing asset
    index can be re-ingested without re-exporting — or the bare details object
    when its identity is supplied some other way.

    Returns a summary dict.  Raises :class:`WorldIndexError` (and writes nothing)
    when the document cannot be stored faithfully.
    """

    return _ingest_world(
        connection,
        canonical,
        source_path=source_path,
        force=force,
        manage_transaction=True,
    )


def ingest_world_in_transaction(
    connection: sqlite3.Connection,
    canonical: Mapping[str, Any],
    *,
    source_path: str = "",
    force: bool = False,
) -> dict[str, Any]:
    """Same as :func:`ingest_world`, but the caller owns the transaction.

    ``indexer.build_index`` writes one asset and its World V2 rows inside a
    single ``with connection:`` block; committing from inside would break that
    atomicity, so the indexer calls this variant.  Any exception propagates and
    the caller's transaction rolls back both the asset and the world rows.
    """

    return _ingest_world(
        connection,
        canonical,
        source_path=source_path,
        force=force,
        manage_transaction=False,
    )


def _ingest_world(
    connection: sqlite3.Connection,
    canonical: Mapping[str, Any],
    *,
    source_path: str,
    force: bool,
    manage_transaction: bool,
) -> dict[str, Any]:
    payload = _mapping(canonical)
    parsed = parse_world_canonical(payload, source_path=source_path)
    asset_path = _world_asset_path(payload, parsed)
    ensure_schema(connection)

    existing = connection.execute(
        "SELECT world_id, revision_value, source_canonical_sha256 FROM world_v2_worlds WHERE asset_path = ?",
        (asset_path,),
    ).fetchone()
    if existing is not None and not force:
        same_revision = str(existing["revision_value"]) == str(parsed.world["revision_value"])
        same_document = str(existing["source_canonical_sha256"]) == parsed.details_sha256
        if same_revision and same_document:
            return {
                "status": "unchanged",
                "asset_path": asset_path,
                "world_id": int(existing["world_id"]),
                "revision_value": str(parsed.world["revision_value"]),
                "revision_changed": False,
                # ``actors``/``descriptors`` are rows written by *this* call.
                "actors": 0,
                "actor_tags": 0,
                "component_class_rows": 0,
                "descriptors": 0,
                "class_count_rows": 0,
                # ...and these are what the document contains, for callers that
                # need to report map size without a second query.
                "documentActors": len(parsed.actors),
                "documentDescriptors": len(parsed.descriptors),
                **parsed.flags,
            }

    if manage_transaction:
        with connection:
            counts = _ingest_parsed(connection, parsed, asset_path=asset_path)
    else:
        counts = _ingest_parsed(connection, parsed, asset_path=asset_path)

    return {
        "status": "updated" if existing is not None else "added",
        "asset_path": asset_path,
        "revision_value": str(parsed.world["revision_value"]),
        **counts,
        **parsed.flags,
    }


def ingest_world_file(
    connection: sqlite3.Connection,
    path: Path | str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Read a canonical JSON file (UTF-8, BOM tolerated) and ingest it."""

    canonical_path = Path(path).expanduser()
    if not canonical_path.is_file():
        raise FileNotFoundError(f"World canonical JSON not found: {canonical_path}")
    document = json.loads(canonical_path.read_text(encoding="utf-8-sig"))
    if not isinstance(document, Mapping):
        raise WorldIndexError(f"{canonical_path} must contain a JSON object")
    return ingest_world(connection, document, source_path=canonical_path.as_posix(), force=force)


def _world_asset_path(canonical: Mapping[str, Any], parsed: ParsedWorld) -> str:
    _, asset_symbol = _resolve_world_details(canonical)
    asset_path = _text(canonical.get("assetPath")) or _text(asset_symbol.get("assetPath"))
    if asset_path:
        return asset_path
    package_name = _text(canonical.get("packageName"))
    asset_name = _text(canonical.get("assetName")) or _text(parsed.world.get("asset_name"))
    if package_name and asset_name:
        return f"{package_name}.{asset_name}"
    return package_name


def delete_world(connection: sqlite3.Connection, asset_path: str) -> dict[str, Any]:
    """Remove one world and its dependent rows.  Returns counts of removed rows."""

    if not world_schema_present(connection):
        return {"asset_path": asset_path, "deleted": False, "world_id": 0}
    row = connection.execute(
        "SELECT world_id FROM world_v2_worlds WHERE asset_path = ?", (asset_path,)
    ).fetchone()
    if row is None:
        return {"asset_path": asset_path, "deleted": False, "world_id": 0}
    world_id = int(row["world_id"])
    with connection:
        _delete_world_rows(connection, world_id)
    return {"asset_path": asset_path, "deleted": True, "world_id": world_id}


def prune_worlds(connection: sqlite3.Connection, asset_paths: Sequence[str]) -> int:
    """Delete World V2 rows for maps that are gone from the index.

    The caller owns the transaction (``indexer._prune_assets`` runs inside one),
    so a prune that fails rolls back together with the asset deletions instead of
    leaving world rows pointing at a vanished asset.
    """

    if not asset_paths or not world_schema_present(connection):
        return 0
    deleted = 0
    for asset_path in asset_paths:
        row = connection.execute(
            "SELECT world_id FROM world_v2_worlds WHERE asset_path = ?", (str(asset_path),)
        ).fetchone()
        if row is None:
            continue
        _delete_world_rows(connection, int(row["world_id"]))
        deleted += 1
    return deleted


def orphan_worlds(connection: sqlite3.Connection, *, limit: int = MAX_WORLD_STATUS_ENTRIES) -> list[str]:
    """World rows whose map asset is no longer in the asset index.

    Happens when an index was pruned before this integration existed, or when a
    database is hand-edited.  The MCP layer surfaces these as ``orphan`` rather
    than serving actors for a map the index no longer contains.
    """

    if not world_schema_present(connection):
        return []
    limit = max(1, min(int(limit), MAX_WORLD_STATUS_ENTRIES))
    rows = connection.execute(
        """
        SELECT w.asset_path
        FROM world_v2_worlds w
        LEFT JOIN assets a ON a.asset_path = w.asset_path
        WHERE a.id IS NULL
        ORDER BY w.asset_path
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [str(row[0]) for row in rows]


# --------------------------------------------------------------------------- #
# Queries
# --------------------------------------------------------------------------- #


def _json_list(value: Any) -> list[Any]:
    if not isinstance(value, str) or not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _world_filter(world: str) -> tuple[str, list[Any]]:
    world = world.strip()
    if not world:
        return "", []
    return " AND (w.asset_path = ? OR w.package_name = ?)", [world, world]


def _actor_where(
    *,
    world: str,
    class_path: str,
    class_contains: str,
    tag: str,
    data_layer: str,
    label_contains: str,
) -> tuple[str, list[Any]]:
    clauses = ["1 = 1"]
    parameters: list[Any] = []

    world_sql, world_parameters = _world_filter(world)
    if world_sql:
        clauses.append(world_sql.removeprefix(" AND "))
        parameters.extend(world_parameters)
    if class_path.strip():
        clauses.append("a.class_path = ?")
        parameters.append(class_path.strip())
    if class_contains.strip():
        clauses.append("a.class_path LIKE ?")
        parameters.append(f"%{class_contains.strip()}%")
    if label_contains.strip():
        clauses.append("a.label LIKE ?")
        parameters.append(f"%{label_contains.strip()}%")
    if tag.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM world_v2_actor_tags t "
            "WHERE t.world_id = a.world_id AND t.actor_key = a.actor_key AND t.tag = ?)"
        )
        parameters.append(tag.strip())
    if data_layer.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM world_v2_actor_data_layers d "
            "WHERE d.world_id = a.world_id AND d.actor_key = a.actor_key AND d.data_layer = ?)"
        )
        parameters.append(data_layer.strip())
    return " AND ".join(clauses), parameters


def _actor_row_to_dict(row: sqlite3.Row, *, include_component_classes: bool) -> dict[str, Any]:
    actor = {key: row[key] for key in row.keys()}
    actor["tags"] = _json_list(actor.pop("tags_json", "[]"))
    actor["dataLayers"] = _json_list(actor.pop("data_layers_json", "[]"))
    component_classes = _json_list(actor.pop("component_classes_json", "[]"))
    if include_component_classes:
        actor["componentClasses"] = component_classes
    return actor


def find_world_actors(
    connection: sqlite3.Connection,
    *,
    world: str = "",
    class_path: str = "",
    class_contains: str = "",
    tag: str = "",
    data_layer: str = "",
    label_contains: str = "",
    include_component_classes: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Query map actors with world/class/tag/data-layer filters and pagination.

    ``world`` accepts either the full asset path (``/Game/Maps/L_Main.L_Main``)
    or just the package path (``/Game/Maps/L_Main``).  Leaving it empty searches
    every ingested world; each returned actor still carries its
    ``world_asset_path``, so results stay attributable to one map.

    Filtering is exact for ``class_path``, ``tag`` and ``data_layer`` (use
    ``class_contains`` for a substring match) and substring for
    ``label_contains``.  Pagination is ``limit``/``offset`` with a deterministic
    ``ORDER BY label, actor_key`` so pages do not shuffle between calls.
    """

    limit, offset = normalize_pagination(limit, offset)
    if not world_schema_present(connection):
        return {
            "world": world.strip() or None,
            "actors": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "hasMore": False,
            "worlds": [],
            "worldIndexAvailable": False,
        }

    where_sql, parameters = _actor_where(
        world=world,
        class_path=class_path,
        class_contains=class_contains,
        tag=tag,
        data_layer=data_layer,
        label_contains=label_contains,
    )
    base_from = "FROM world_v2_actors a JOIN world_v2_worlds w ON w.world_id = a.world_id WHERE "

    total = int(
        connection.execute(f"SELECT COUNT(*) {base_from}{where_sql}", parameters).fetchone()[0]
    )
    rows = connection.execute(
        f"""
        SELECT a.*, w.asset_path AS world_asset_path, w.package_name AS world_package_name,
               w.revision_value AS world_revision_value, w.partial AS world_partial
        {base_from}{where_sql}
        ORDER BY a.label, a.actor_key
        LIMIT ? OFFSET ?
        """,
        [*parameters, limit, offset],
    ).fetchall()

    actors = [_actor_row_to_dict(row, include_component_classes=include_component_classes) for row in rows]
    worlds = sorted({str(actor["world_asset_path"]) for actor in actors})
    return {
        "world": world.strip() or None,
        "actors": actors,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(actors) < total,
        "worlds": worlds,
    }


def find_world_partition_actors(
    connection: sqlite3.Connection,
    *,
    world: str = "",
    base_class_path: str = "",
    class_contains: str = "",
    tag: str = "",
    data_layer: str = "",
    folder_prefix: str = "",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> dict[str, Any]:
    """Query World Partition actor descriptors (before-load metadata + bounds)."""

    limit, offset = normalize_pagination(limit, offset)
    if not world_schema_present(connection):
        return {
            "world": world.strip() or None,
            "actors": [],
            "total": 0,
            "limit": limit,
            "offset": offset,
            "hasMore": False,
            "worlds": [],
            "worldIndexAvailable": False,
        }

    clauses = ["1 = 1"]
    parameters: list[Any] = []
    world_sql, world_parameters = _world_filter(world)
    if world_sql:
        clauses.append(world_sql.removeprefix(" AND "))
        parameters.extend(world_parameters)
    if base_class_path.strip():
        clauses.append("d.base_class_path = ?")
        parameters.append(base_class_path.strip())
    if class_contains.strip():
        clauses.append("(d.base_class_path LIKE ? OR d.native_class_path LIKE ?)")
        parameters.extend([f"%{class_contains.strip()}%"] * 2)
    if folder_prefix.strip():
        clauses.append("d.folder_path LIKE ?")
        parameters.append(f"{folder_prefix.strip()}%")
    if tag.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM world_v2_world_partition_actor_tags t "
            "WHERE t.world_id = d.world_id AND t.actor_key = d.actor_key AND t.tag = ?)"
        )
        parameters.append(tag.strip())
    if data_layer.strip():
        clauses.append(
            "EXISTS (SELECT 1 FROM world_v2_world_partition_actor_data_layers l "
            "WHERE l.world_id = d.world_id AND l.actor_key = d.actor_key AND l.data_layer = ?)"
        )
        parameters.append(data_layer.strip())
    where_sql = " AND ".join(clauses)

    base_from = (
        "FROM world_v2_world_partition_actors d "
        "JOIN world_v2_worlds w ON w.world_id = d.world_id WHERE "
    )
    total = int(
        connection.execute(f"SELECT COUNT(*) {base_from}{where_sql}", parameters).fetchone()[0]
    )
    rows = connection.execute(
        f"""
        SELECT d.*, w.asset_path AS world_asset_path, w.actor_desc_list_truncated,
               w.actor_desc_metadata_available, w.exported_actor_desc_count, w.actor_desc_count
        {base_from}{where_sql}
        ORDER BY d.label, d.actor_key
        LIMIT ? OFFSET ?
        """,
        [*parameters, limit, offset],
    ).fetchall()

    descriptors = []
    for row in rows:
        descriptor = {key: row[key] for key in row.keys()}
        descriptor["tags"] = _json_list(descriptor.pop("tags_json", "[]"))
        descriptor["dataLayers"] = _json_list(descriptor.pop("data_layers_json", "[]"))
        descriptor["editorBounds"] = _json_object(descriptor.pop("editor_bounds_json", "{}"))
        descriptor["runtimeBounds"] = _json_object(descriptor.pop("runtime_bounds_json", "{}"))
        descriptors.append(descriptor)

    return {
        "world": world.strip() or None,
        "actors": descriptors,
        "total": total,
        "limit": limit,
        "offset": offset,
        "hasMore": offset + len(descriptors) < total,
        "worlds": sorted({str(item["world_asset_path"]) for item in descriptors}),
    }


def get_world(connection: sqlite3.Connection, asset_path: str) -> dict[str, Any] | None:
    """Return one world with its stored details and row counts, or ``None``.

    Read-only: a database without World V2 tables simply has no world to return.
    """

    if not world_schema_present(connection):
        return None
    row = connection.execute(
        "SELECT * FROM world_v2_worlds WHERE asset_path = ?", (asset_path.strip(),)
    ).fetchone()
    if row is None:
        return None
    world = {key: row[key] for key in row.keys()}
    world_id = int(world["world_id"])
    world["partial_reasons"] = _json_list(world.pop("partial_reasons_json", "[]"))
    world["worldSettings"] = _json_object(world.pop("world_settings_json", "{}"))
    world["worldPartition"] = _json_object(world.pop("world_partition_json", "{}"))
    world["streamingLevels"] = _json_list(world.pop("streaming_levels_json", "[]"))
    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM world_v2_actors WHERE world_id = ?) AS actors,
            (SELECT COUNT(*) FROM world_v2_actor_tags WHERE world_id = ?) AS actor_tags,
            (SELECT COUNT(*) FROM world_v2_actor_data_layers WHERE world_id = ?) AS actor_data_layers,
            (SELECT COUNT(*) FROM world_v2_actor_component_classes WHERE world_id = ?)
                AS actor_component_classes,
            (SELECT COUNT(*) FROM world_v2_world_partition_actors WHERE world_id = ?) AS descriptors
        """,
        (world_id,) * 5,
    ).fetchone()
    world["counts"] = {key: int(counts[key]) for key in counts.keys()}
    world["classCounts"] = [
        {"source": str(item["source"]), "classPath": str(item["class_path"]), "count": int(item["count"])}
        for item in connection.execute(
            "SELECT source, class_path, count FROM world_v2_class_counts WHERE world_id = ? "
            "ORDER BY source, class_path",
            (world_id,),
        )
    ]
    world["tags"] = [
        str(item[0])
        for item in connection.execute(
            "SELECT DISTINCT tag FROM world_v2_actor_tags WHERE world_id = ? ORDER BY tag", (world_id,)
        )
    ]
    world["dataLayers"] = [
        str(item[0])
        for item in connection.execute(
            "SELECT DISTINCT data_layer FROM world_v2_actor_data_layers WHERE world_id = ? "
            "ORDER BY data_layer",
            (world_id,),
        )
    ]
    return world


def list_worlds(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """List ingested worlds with actor/descriptor counts and truncation flags."""

    if not world_schema_present(connection):
        return []
    rows = connection.execute(
        """
        SELECT w.asset_path, w.package_name, w.asset_name, w.world_type, w.revision_value,
               w.actor_list_truncated, w.actor_desc_list_truncated, w.partial,
               w.loaded_actor_count, w.exported_actor_count, w.actor_desc_count,
               w.exported_actor_desc_count, w.ingested_at_utc,
               (SELECT COUNT(*) FROM world_v2_actors a WHERE a.world_id = w.world_id) AS actors,
               (SELECT COUNT(*) FROM world_v2_world_partition_actors d WHERE d.world_id = w.world_id)
                   AS descriptors
        FROM world_v2_worlds w
        ORDER BY w.asset_path
        """
    ).fetchall()
    return [{key: row[key] for key in row.keys()} for row in rows]


def world_index_status(connection: sqlite3.Connection) -> dict[str, Any]:
    """Row counts and schema version for diagnostics/verification."""

    if not world_schema_present(connection):
        return {
            "schemaVersion": WORLD_INDEX_V2_SCHEMA_VERSION,
            "schemaAvailable": False,
            "tables": {},
            "worlds": 0,
            "actors": 0,
            "partialWorlds": [],
        }
    tables = (
        "world_v2_worlds",
        "world_v2_actors",
        "world_v2_actor_tags",
        "world_v2_actor_data_layers",
        "world_v2_actor_component_classes",
        "world_v2_class_counts",
        "world_v2_world_partition_actors",
        "world_v2_world_partition_actor_tags",
        "world_v2_world_partition_actor_data_layers",
    )
    counts = {
        table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in tables
    }
    partial_worlds = [
        str(row[0])
        for row in connection.execute(
            "SELECT asset_path FROM world_v2_worlds WHERE partial = 1 ORDER BY asset_path"
        )
    ]
    return {
        "schemaVersion": WORLD_INDEX_V2_SCHEMA_VERSION,
        "schemaAvailable": True,
        "tables": counts,
        "worlds": counts["world_v2_worlds"],
        "actors": counts["world_v2_actors"],
        "partialWorlds": partial_worlds,
    }


# --------------------------------------------------------------------------- #
# Freshness / coverage
# --------------------------------------------------------------------------- #


def index_build_status(connection: sqlite3.Connection) -> dict[str, Any]:
    """What the last index build said about world coverage.

    ``build_index`` records this so a query can warn "the last import was
    incomplete" instead of trusting that every map is current just because an
    ``assets`` row exists.
    """

    complete_value = get_metadata(connection, "world_index_last_build_complete", "")
    reasons_raw = get_metadata(connection, "world_index_last_build_incomplete_reasons", "")
    reasons: list[str] = []
    if reasons_raw:
        try:
            parsed = json.loads(reasons_raw)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, list):
            reasons = [str(item) for item in parsed][:16]
    return {
        "declared": bool(complete_value),
        "lastBuildUtc": get_metadata(connection, "world_index_last_build_utc", ""),
        "complete": complete_value == "1",
        "incompleteReasons": reasons,
    }


def _asset_row(connection: sqlite3.Connection, asset_path: str) -> sqlite3.Row | None:
    return connection.execute(
        """
        SELECT asset_path, package_name, asset_class, revision_value, indexed_at_utc, profile
        FROM assets
        WHERE asset_path = ?
        """,
        (asset_path,),
    ).fetchone()


def _coverage_entry(
    connection: sqlite3.Connection,
    *,
    asset_path: str,
    world_row: sqlite3.Row | None,
    asset_row: sqlite3.Row | None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "assetPath": asset_path,
        "packageName": str(asset_row["package_name"]) if asset_row is not None else "",
        "assetClass": str(asset_row["asset_class"]) if asset_row is not None else "",
        "coverage": COVERAGE_COMPLETE,
        "complete": False,
        "assetIndexed": asset_row is not None,
        "worldIndexed": world_row is not None,
        "assetRevision": str(asset_row["revision_value"]) if asset_row is not None else "",
        "worldRevision": str(world_row["revision_value"]) if world_row is not None else "",
        "revisionConsistent": False,
        "assetIndexedAtUtc": str(asset_row["indexed_at_utc"]) if asset_row is not None else "",
        "worldIngestedAtUtc": "",
        "partial": False,
        "partialReasons": [],
        "actorListTruncated": False,
        "actorDescListTruncated": False,
        "loadedActorCount": 0,
        "exportedActorCount": 0,
        "actorDescCount": 0,
        "exportedActorDescCount": 0,
        "actorRows": 0,
        "actorDescRows": 0,
        "worldPartitionAvailable": None,
        "actorDescMetadataAvailable": None,
        "warnings": [],
    }
    warnings: list[str] = []

    if world_row is None:
        entry["coverage"] = COVERAGE_WORLD_INDEX_MISSING
        warnings.append(
            f"{asset_path} is indexed but has no World V2 rows; the map actor tables were not "
            "built for this asset (older index or index built before World V2 existed). Rebuild "
            "the index (build_index force / ue_refresh_asset_index) to populate map actors."
        )
        entry["warnings"] = warnings
        return entry

    entry["packageName"] = entry["packageName"] or str(world_row["package_name"])
    entry["worldIngestedAtUtc"] = str(world_row["ingested_at_utc"])
    # WP coverage is distinct from completeness of the World asset export.
    # A non-WorldPartition map may be complete but has no WP descriptors by design.
    partition_info = _json_object(world_row["world_partition_json"])
    wp_available = partition_info.get("available")
    entry["worldPartitionAvailable"] = wp_available if isinstance(wp_available, bool) else None
    entry["actorDescMetadataAvailable"] = bool(world_row["actor_desc_metadata_available"])
    entry["partial"] = bool(world_row["partial"])
    entry["partialReasons"] = [str(item) for item in _json_list(world_row["partial_reasons_json"])]
    entry["actorListTruncated"] = bool(world_row["actor_list_truncated"])
    entry["actorDescListTruncated"] = bool(world_row["actor_desc_list_truncated"])
    entry["loadedActorCount"] = int(world_row["loaded_actor_count"])
    entry["exportedActorCount"] = int(world_row["exported_actor_count"])
    entry["actorDescCount"] = int(world_row["actor_desc_count"])
    entry["exportedActorDescCount"] = int(world_row["exported_actor_desc_count"])
    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM world_v2_actors WHERE world_id = ?) AS actors,
            (SELECT COUNT(*) FROM world_v2_world_partition_actors WHERE world_id = ?) AS descriptors
        """,
        (int(world_row["world_id"]), int(world_row["world_id"])),
    ).fetchone()
    entry["actorRows"] = int(counts["actors"])
    entry["actorDescRows"] = int(counts["descriptors"])

    if asset_row is None:
        entry["coverage"] = COVERAGE_ORPHAN
        warnings.append(
            f"{asset_path} has World V2 rows but no row in the asset index; the map was pruned or "
            "removed from the export. These actors are orphaned and must not be treated as current."
        )
        entry["warnings"] = warnings
        return entry

    if not entry["assetRevision"] or not entry["worldRevision"]:
        # No revision metadata means freshness cannot be established at all, so
        # the rows must not be advertised as complete.
        entry["coverage"] = COVERAGE_PARTIAL
        entry["partial"] = True
        entry["partialReasons"] = [*entry["partialReasons"], "revision-unavailable"]
        warnings.append(
            f"{asset_path}: no revision metadata for the asset or the world rows; freshness "
            "cannot be verified, so the actors are not reported as complete."
        )
        entry["warnings"] = warnings
        return entry

    entry["revisionConsistent"] = entry["assetRevision"] == entry["worldRevision"]
    if not entry["revisionConsistent"]:
        entry["coverage"] = COVERAGE_STALE
        warnings.append(
            f"{asset_path}: world rows were built from revision "
            f"{entry['worldRevision'] or '<none>'} but the asset index holds "
            f"{entry['assetRevision'] or '<none>'}; re-import the map to refresh actor rows."
        )
        entry["warnings"] = warnings
        return entry

    if entry["actorListTruncated"] or entry["actorDescListTruncated"]:
        entry["coverage"] = COVERAGE_TRUNCATED
        warnings.append(
            f"{asset_path}: the export was truncated "
            f"(actors {entry['exportedActorCount']}/{entry['loadedActorCount']}, "
            f"descriptors {entry['exportedActorDescCount']}/{entry['actorDescCount']}); "
            "actor lists are incomplete, so absence of an actor proves nothing."
        )
        entry["warnings"] = warnings
        return entry

    if entry["partial"]:
        entry["coverage"] = COVERAGE_PARTIAL
        reasons = ", ".join(entry["partialReasons"]) or "unspecified"
        warnings.append(f"{asset_path}: the world export is partial ({reasons}).")
        entry["warnings"] = warnings
        return entry

    entry["coverage"] = COVERAGE_COMPLETE
    entry["complete"] = True
    entry["warnings"] = warnings
    return entry


def _aggregate_coverage(entries: Sequence[Mapping[str, Any]]) -> str:
    if not entries:
        return COVERAGE_COMPLETE
    seen = {str(entry.get("coverage", "")) for entry in entries}
    for candidate in _COVERAGE_SEVERITY:
        if candidate in seen:
            return candidate
    return COVERAGE_COMPLETE


def world_status(
    connection: sqlite3.Connection,
    *,
    world: str = "",
    limit: int = MAX_WORLD_STATUS_ENTRIES,
    offset: int = 0,
) -> dict[str, Any]:
    """Freshness/coverage for one map filter or for the whole world index.

    This is the honesty surface behind the MCP tools: an agent must be able to
    tell "this map has no actors" from "this map was never given world tables",
    "these rows describe an older revision", and "the export was truncated".
    """

    filter_text = str(world or "").strip()
    limit = max(1, min(int(limit), MAX_WORLD_STATUS_ENTRIES))
    offset = max(0, int(offset))
    build = index_build_status(connection)

    if not world_schema_present(connection):
        unavailable_warnings = [
            "This database has no World Index V2 tables; no map actor data has been imported. "
            "Import a World export (ueak index) to populate them."
        ]
        if build["declared"] and not build["complete"]:
            unavailable_warnings.append(
                "The last index build was incomplete ("
                + (", ".join(build["incompleteReasons"]) or "unspecified")
                + "); world rows may be missing or out of date."
            )
        return {
            "schemaAvailable": False,
            "assetPathFilter": filter_text,
            "targetFound": False,
            "coverage": COVERAGE_INDEX_UNAVAILABLE,
            "complete": False,
            "worlds": [],
            "worldsTruncated": False,
            "totalWorlds": 0,
            "coverageScope": "page",
            "indexBuild": build,
            "warnings": unavailable_warnings,
        }

    candidates: list[str] = []
    if filter_text:
        rows = connection.execute(
            "SELECT asset_path FROM assets WHERE asset_path = ? OR package_name = ? ORDER BY asset_path",
            (filter_text, filter_text),
        ).fetchall()
        candidates.extend(str(row[0]) for row in rows)
        rows = connection.execute(
            "SELECT asset_path FROM world_v2_worlds WHERE asset_path = ? OR package_name = ? "
            "ORDER BY asset_path",
            (filter_text, filter_text),
        ).fetchall()
        candidates.extend(str(row[0]) for row in rows)
        candidates = sorted(dict.fromkeys(candidates))
        if not candidates:
            return {
                "schemaAvailable": True,
                "assetPathFilter": filter_text,
                "targetFound": False,
                "coverage": COVERAGE_UNKNOWN_MAP,
                "complete": False,
                "worlds": [],
                "worldsTruncated": False,
                "totalWorlds": 0,
                "coverageScope": "page",
                "indexBuild": build,
                "warnings": [
                    f"No indexed asset or world matches {filter_text!r}; the map is not in this "
                    "index, so an empty actor list is not evidence that the map is empty."
                ],
            }
        total_worlds = len(candidates)
        candidates = candidates[offset : offset + limit]
        truncated = offset + len(candidates) < total_worlds
    else:
        total_worlds = int(connection.execute(
            "SELECT COUNT(*) FROM world_v2_worlds"
        ).fetchone()[0])
        rows = connection.execute(
            "SELECT asset_path FROM world_v2_worlds ORDER BY asset_path LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        candidates = [str(row[0]) for row in rows]
        truncated = offset + len(candidates) < total_worlds

    entries: list[dict[str, Any]] = []
    for asset_path in candidates:
        world_row = connection.execute(
            "SELECT * FROM world_v2_worlds WHERE asset_path = ?", (asset_path,)
        ).fetchone()
        entries.append(
            _coverage_entry(
                connection,
                asset_path=asset_path,
                world_row=world_row,
                asset_row=_asset_row(connection, asset_path),
            )
        )

    coverage = _aggregate_coverage(entries)
    warnings: list[str] = []
    for entry in entries:
        warnings.extend(str(item) for item in entry["warnings"])
    if build["declared"] and not build["complete"]:
        warnings.insert(
            0,
            "The last index build was incomplete ("
            + (", ".join(build["incompleteReasons"]) or "unspecified")
            + "); world rows may not reflect the current export.",
        )
    return {
        "schemaAvailable": True,
        "assetPathFilter": filter_text,
        "targetFound": total_worlds > 0,
        "coverageScope": "page" if offset or truncated else "all-returned",
        "coverage": coverage,
        "complete": bool(entries) and all(entry["complete"] for entry in entries),
        "worlds": entries,
        "worldsTruncated": truncated,
        "totalWorlds": total_worlds,
        "indexBuild": build,
        "warnings": warnings[:32],
    }

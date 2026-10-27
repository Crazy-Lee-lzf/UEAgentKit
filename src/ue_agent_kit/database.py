from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .schema import (
    CURRENT_SCHEMA_VERSION,
    INDEX_REBUILD_REQUIRED_FROM,
    INDEX_SCHEMA_NAME,
    INDEX_SCHEMA_TAG,
    MIGRATIONS,
)
from .text_search import is_cjk_character


_EXPECTED_FTS_COLUMNS: dict[str, tuple[str, ...]] = {
    "assets_fts": (
        "asset_path",
        "asset_name",
        "package_name",
        "parent_class",
        "generated_class",
    ),
    "symbols_fts": (
        "stable_id",
        "name",
        "kind",
        "symbol_asset_path",
        "class_path",
        "details_json",
        "search_text",
    ),
    "nodes_fts": (
        "guid",
        "title",
        "object_name",
        "node_class",
        "comment",
        "details_json",
        "search_text",
    ),
}

_EXPECTED_SEARCH_TEXT_TABLES = ("symbols", "nodes")
_EXPECTED_SEARCH_TEXT_TRIGGERS = (
    "symbols_ai",
    "symbols_ad",
    "symbols_au",
    "nodes_ai",
    "nodes_ad",
    "nodes_au",
)
_LEGACY_FTS_TRIGGERS = ("assets_ai", "assets_ad", "assets_au")
_MANAGED_OBJECT_NAMES = frozenset(
    {
        "schema_migrations",
        "metadata",
        "assets",
        "symbols",
        "graphs",
        "nodes",
        "references_table",
        "assets_fts",
        "symbols_fts",
        "nodes_fts",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def connect_database(
    path: Path,
    *,
    readonly: bool = False,
    immutable: bool = False,
) -> sqlite3.Connection:
    database_path = path.expanduser().resolve()
    if immutable and not readonly:
        raise ValueError("immutable database connections must also be read-only")
    if readonly:
        if not database_path.is_file():
            raise FileNotFoundError(f"Database not found: {database_path}")
        immutable_query = "&immutable=1" if immutable else ""
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro{immutable_query}",
            uri=True,
            timeout=30.0,
        )
    else:
        database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(database_path, timeout=30.0)

    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    if not readonly:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


def get_schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sqlite_object_names(connection: sqlite3.Connection, object_type: str) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = ?",
            (object_type,),
        )
    }


def _table_columns(connection: sqlite3.Connection, table_name: str) -> tuple[str, ...]:
    return tuple(str(row[1]) for row in connection.execute(f"PRAGMA table_info({table_name})"))


def _foreign_key_check_clean(connection: sqlite3.Connection) -> bool:
    return not connection.execute("PRAGMA foreign_key_check").fetchall()


def _quick_check_ok(connection: sqlite3.Connection) -> bool:
    return [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()] == ["ok"]


def _missing_fts_integrity(connection: sqlite3.Connection) -> list[str]:
    """Run FTS5's own consistency check for every external-content table.

    ``integrity-check`` compares the full-text index with the content table and
    is the strongest cheap proof that a freshly rebuilt database is searchable.
    Older SQLite builds that do not recognize the command are treated as
    unavailable rather than fatal; ``PRAGMA quick_check`` still runs.
    """
    failures: list[str] = []
    for table_name in sorted(_EXPECTED_FTS_COLUMNS):
        try:
            connection.execute(f"INSERT INTO {table_name}({table_name}) VALUES('integrity-check')")
        except sqlite3.OperationalError as exc:
            if "unrecognized" in str(exc).lower() or "syntax" in str(exc).lower():
                continue
            failures.append(f"{table_name}: {exc}")
        except sqlite3.DatabaseError as exc:
            failures.append(f"{table_name}: {exc}")
    return failures


def assert_fts5_available(connection: sqlite3.Connection) -> None:
    table_names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('assets_fts', 'symbols_fts', 'nodes_fts')"
        )
    }
    expected = {"assets_fts", "symbols_fts", "nodes_fts"}
    if table_names != expected:
        missing = ", ".join(sorted(expected - table_names))
        raise RuntimeError(f"SQLite FTS5 schema is incomplete. Missing: {missing}")


def assert_index_schema_current(connection: sqlite3.Connection) -> None:
    """Validate the exact v2 physical shape used by the CJK-aware index paths.

    A database that claims ``user_version = 2`` but is missing ``search_text``,
    an FTS column, or a trigger is rejected instead of being repaired. Repairing
    an external-content FTS5 index in place is the operation schema v2 exists to
    avoid; the caller must rebuild into a fresh file.
    """
    version = get_schema_version(connection)
    if version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Index database schema {version} does not match supported schema {CURRENT_SCHEMA_VERSION}."
        )

    problems: list[str] = []
    for table_name in _EXPECTED_SEARCH_TEXT_TABLES:
        columns = _table_columns(connection, table_name)
        if "search_text" not in columns:
            problems.append(f"column:{table_name}.search_text")
    for table_name, expected_columns in _EXPECTED_FTS_COLUMNS.items():
        actual_columns = _table_columns(connection, table_name)
        if not actual_columns:
            problems.append(f"table:{table_name}")
        elif actual_columns != expected_columns:
            problems.append(
                f"columns:{table_name}=" + ",".join(actual_columns)
                + " expected=" + ",".join(expected_columns)
            )

    trigger_sql = {
        str(row[0]): str(row[1] or "")
        for row in connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
    }
    for trigger_name in _EXPECTED_SEARCH_TEXT_TRIGGERS:
        sql = trigger_sql.get(trigger_name, "")
        if not sql:
            problems.append(f"trigger:{trigger_name}")
        elif "search_text" not in sql.lower():
            problems.append(f"trigger:{trigger_name}:missing-search_text")
    for trigger_name in _LEGACY_FTS_TRIGGERS:
        sql = trigger_sql.get(trigger_name, "")
        if not sql:
            problems.append(f"trigger:{trigger_name}")
        elif "search_text" in sql.lower():
            problems.append(f"trigger:{trigger_name}:unexpected-search_text")

    applied_versions: set[int] = set()
    try:
        applied_versions = {
            int(row[0])
            for row in connection.execute("SELECT version FROM schema_migrations")
        }
    except sqlite3.Error:
        problems.append("table:schema_migrations")
    if CURRENT_SCHEMA_VERSION not in applied_versions:
        problems.append(f"migration:{CURRENT_SCHEMA_VERSION}")

    if problems:
        raise RuntimeError(
            f"Index database schema {version} is incomplete; refusing to use it. "
            "Missing or inconsistent objects: "
            + ", ".join(problems)
            + ". Rebuild a fresh v2 index instead of repairing it in place."
        )


def _assert_schema_start_state(connection: sqlite3.Connection) -> None:
    """Fail closed before any schema write on an existing database.

    The only writable path to schema v2 is a brand-new, empty database. Every
    older database (including an empty v1 file) is rejected with a rebuild
    instruction because this code cannot safely distinguish a newly generated
    v1 file from a multi-gigabyte production index. A database with an
    unrecognized object set and no version tag is rejected as well.
    """
    version = get_schema_version(connection)
    if version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Index database schema {version} is newer than supported schema "
            f"{CURRENT_SCHEMA_VERSION}; refusing to open it."
        )
    if version == 0:
        existing_objects = _sqlite_object_names(connection, "table")
        if existing_objects & _MANAGED_OBJECT_NAMES:
            raise RuntimeError(
                "Index database has index tables but no schema version; refusing to initialize "
                "over a partial or unrecognized database. Rebuild a fresh v2 index instead."
            )
        return
    if version < INDEX_REBUILD_REQUIRED_FROM:
        raise RuntimeError(
            f"Index database schema {version} requires a full rebuild to schema "
            f"{CURRENT_SCHEMA_VERSION} (INDEX_REBUILD_REQUIRED_FROM="
            f"{INDEX_REBUILD_REQUIRED_FROM}). In-place schema or FTS migration of a pre-v2 "
            "index is not supported because v2 changes two external-content FTS5 tables; "
            "rewriting a multi-gigabyte index in place is unsafe. Build a new v2 database and "
            "swap it atomically, for example: "
            "`ue-agent index build <export-root> --database <path> --rebuild`. "
            "The existing database file was not modified."
        )
    if version == CURRENT_SCHEMA_VERSION:
        assert_index_schema_current(connection)


def read_database_schema_version(path: Path) -> int | None:
    """Read ``PRAGMA user_version`` without creating or modifying the file.

    ``None`` means the path does not exist (or is a zero-byte placeholder).
    The connection is read-only and immutable, so a preflight check cannot
    checkpoint, create a ``-shm`` file, or write a journal. An actively written
    database must be closed before a rebuild; ``_prepare_rebuild_target``
    refuses a non-empty WAL sidecar.
    """
    database_path = path.expanduser().resolve()
    if not database_path.is_file() or database_path.stat().st_size == 0:
        return None
    try:
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=30.0,
        )
    except sqlite3.Error as exc:
        raise RuntimeError(f"Index database cannot be opened read-only: {exc}") from exc
    try:
        return get_schema_version(connection)
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Index database is not a valid SQLite database: {exc}") from exc
    finally:
        connection.close()


def _preflight_existing_database(path: Path) -> None:
    """Validate an existing writable target without opening it writable."""
    database_path = path.expanduser().resolve()
    version = read_database_schema_version(database_path)
    if version is None:
        return
    try:
        connection = sqlite3.connect(
            f"file:{database_path.as_posix()}?mode=ro&immutable=1",
            uri=True,
            timeout=30.0,
        )
    except sqlite3.Error as exc:
        raise RuntimeError(f"Index database cannot be opened read-only: {exc}") from exc
    try:
        connection.row_factory = sqlite3.Row
        _assert_schema_start_state(connection)
    except sqlite3.DatabaseError as exc:
        raise RuntimeError(f"Index database is not a valid SQLite database: {exc}") from exc
    finally:
        connection.close()


def _metadata_upsert_sql(key: str, value: str) -> str:
    return (
        "INSERT INTO metadata(key, value) VALUES ("
        + _quote_sql_literal(key)
        + ", "
        + _quote_sql_literal(value)
        + ") ON CONFLICT(key) DO UPDATE SET value = excluded.value;\n"
    )


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Apply the current schema to a new database and stamp its version tag.

    A fresh database starts at the rebuild baseline
    (``INDEX_REBUILD_REQUIRED_FROM``, a complete schema) and then applies later
    delta migrations normally. Historical entries below the baseline are never
    executed against any file, which is what guarantees schema v2 can never be
    produced by altering a v1 FTS table in place.
    """
    current_version = get_schema_version(connection)
    if current_version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {current_version} is newer than supported schema {CURRENT_SCHEMA_VERSION}."
        )
    _assert_schema_start_state(connection)

    is_fresh = current_version == 0 and not (
        _sqlite_object_names(connection, "table") & _MANAGED_OBJECT_NAMES
    )

    for migration in MIGRATIONS:
        if migration.version <= current_version:
            continue
        if is_fresh and migration.version < INDEX_REBUILD_REQUIRED_FROM:
            # Historical schema steps before the current rebuild baseline are
            # never executed. A fresh file starts at the first complete schema
            # (v2) and then applies any later delta migrations normally.
            continue

        description = _quote_sql_literal(migration.description)
        applied_at = _quote_sql_literal(utc_now_iso())
        script = (
            "BEGIN IMMEDIATE;\n"
            + migration.sql
            + "\n"
            + "INSERT INTO schema_migrations(version, description, applied_at_utc) "
            + f"VALUES ({migration.version}, {description}, {applied_at});\n"
            + f"PRAGMA user_version = {migration.version};\n"
            + _metadata_upsert_sql("database_schema_version", str(migration.version))
            + _metadata_upsert_sql("index_schema_name", INDEX_SCHEMA_NAME)
            + _metadata_upsert_sql("index_schema_tag", INDEX_SCHEMA_TAG)
            + "COMMIT;\n"
        )
        try:
            connection.executescript(script)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        current_version = migration.version

    if current_version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database migration stopped at schema {current_version}; expected {CURRENT_SCHEMA_VERSION}."
        )
    assert_index_schema_current(connection)
    return current_version


def set_metadata(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_metadata(connection: sqlite3.Connection, key: str, default: str = "") -> str:
    row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return str(row[0]) if row is not None else default


@contextmanager
def open_database(
    path: Path,
    *,
    readonly: bool = False,
    migrate: bool = True,
    immutable: bool = False,
) -> Iterator[sqlite3.Connection]:
    database_path = path.expanduser().resolve()
    if migrate and not readonly:
        # Validate an existing database before opening it writable, so a
        # rejected v1/unversioned file is not even asked to change journal mode.
        _preflight_existing_database(database_path)
    connection = connect_database(database_path, readonly=readonly, immutable=immutable)
    try:
        if migrate:
            apply_migrations(connection)
            assert_fts5_available(connection)
            assert_index_schema_current(connection)
        yield connection
    finally:
        connection.close()


def validate_index_search(connection: sqlite3.Connection) -> None:
    """Prove the projected CJK text actually reaches each FTS index.

    For every ``symbols``/``nodes`` table that holds a non-empty projection,
    take the first projected CJK character and require the corresponding FTS
    table to return a hit. This catches a rebuilt-but-empty index, a wrong
    content table, or a projection written without its trigger payload before
    the staged database is swapped in.
    """
    for table_name, fts_table in (("symbols", "symbols_fts"), ("nodes", "nodes_fts")):
        row = connection.execute(
            f"SELECT search_text FROM {table_name} WHERE search_text <> '' LIMIT 1"
        ).fetchone()
        if row is None:
            continue
        first_cjk = next(
            (
                token
                for token in str(row[0]).split(" ")
                if token and is_cjk_character(token)
            ),
            "",
        )
        if not first_cjk:
            continue
        hit = connection.execute(
            f"SELECT 1 FROM {fts_table} WHERE {fts_table} MATCH ? LIMIT 1",
            (f'"{first_cjk}"',),
        ).fetchone()
        if hit is None:
            raise RuntimeError(
                f"Staged index failed search validation: {table_name} projection is not searchable "
                f"through {fts_table}."
            )


def _remove_sqlite_files(path: Path) -> None:
    for candidate in (
        path,
        Path(str(path) + "-wal"),
        Path(str(path) + "-shm"),
        Path(str(path) + "-journal"),
    ):
        try:
            candidate.unlink()
        except FileNotFoundError:
            continue


def _prepare_rebuild_target(target: Path) -> None:
    """Refuse to replace a database this build cannot safely recognize."""
    if not target.exists():
        return
    if target.is_dir():
        raise RuntimeError(f"Index database path is a directory: {target}")
    for suffix in ("-wal", "-journal"):
        sidecar = Path(str(target) + suffix)
        if sidecar.exists() and sidecar.stat().st_size > 0:
            raise RuntimeError(
                f"Index database has an active {suffix.lstrip('-')} sidecar; close all writers "
                "(and let SQLite checkpoint) before rebuilding it."
            )
    version = read_database_schema_version(target)
    if version is None:
        if target.stat().st_size == 0:
            return
        raise RuntimeError("Index database path is not a valid SQLite database; refusing to replace it.")
    if version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Index database schema {version} is newer than supported schema "
            f"{CURRENT_SCHEMA_VERSION}; refusing to replace it."
        )
    if version == 0:
        raise RuntimeError(
            "Index database has no schema version and cannot be safely recognized; refusing to "
            "replace it. Move the file aside and build a fresh index."
        )


def rebuild_index_atomically(
    database_path: Path,
    build: Callable[[sqlite3.Connection], Any],
    *,
    validate: Callable[[sqlite3.Connection], None] | None = None,
) -> Any:
    """Build a brand-new v2 index beside ``database_path`` and swap it in.

    The target file is never modified until the staged database passes
    ``PRAGMA quick_check``, ``PRAGMA foreign_key_check``, the exact v2 schema
    assertion, FTS5 ``integrity-check``, and the optional caller ``validate``
    callback. A failure at any point removes the staged file and leaves the
    target byte-for-byte untouched. ``os.replace`` performs the same-filesystem
    swap only after the staged connection has checkpointed and left WAL mode.
    """
    target = Path(database_path).expanduser().resolve()
    _prepare_rebuild_target(target)
    staged = target.with_name(f".{target.name}.v{CURRENT_SCHEMA_VERSION}.rebuild.tmp")
    _remove_sqlite_files(staged)
    target.parent.mkdir(parents=True, exist_ok=True)

    connection = connect_database(staged)
    try:
        apply_migrations(connection)
        result = build(connection)
        if connection.in_transaction:
            connection.commit()
        assert_fts5_available(connection)
        assert_index_schema_current(connection)
        if not _quick_check_ok(connection):
            raise RuntimeError(
                "Staged index failed PRAGMA quick_check: "
                + "; ".join(str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall())
            )
        if not _foreign_key_check_clean(connection):
            raise RuntimeError("Staged index failed PRAGMA foreign_key_check.")
        fts_failures = _missing_fts_integrity(connection)
        if fts_failures:
            raise RuntimeError(
                "Staged index failed FTS5 integrity-check: " + "; ".join(fts_failures)
            )
        validate_index_search(connection)
        if validate is not None:
            validate(connection)
        set_metadata(connection, "last_database_path", str(target))
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode = DELETE")
        connection.commit()
    except Exception:
        connection.close()
        _remove_sqlite_files(staged)
        raise
    connection.close()

    for sidecar_suffix in ("-wal", "-shm"):
        try:
            Path(str(staged) + sidecar_suffix).unlink()
        except FileNotFoundError:
            continue
    try:
        os.replace(staged, target)
    except OSError:
        _remove_sqlite_files(staged)
        raise
    return result

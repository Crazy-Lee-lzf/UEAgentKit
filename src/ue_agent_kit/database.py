from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .schema import CURRENT_SCHEMA_VERSION, MIGRATIONS


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


def apply_migrations(connection: sqlite3.Connection) -> int:
    current_version = get_schema_version(connection)
    if current_version > CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"Database schema {current_version} is newer than supported schema {CURRENT_SCHEMA_VERSION}."
        )

    for migration in MIGRATIONS:
        if migration.version <= current_version:
            continue

        description = _quote_sql_literal(migration.description)
        applied_at = _quote_sql_literal(utc_now_iso())
        script = (
            "BEGIN IMMEDIATE;\n"
            + migration.sql
            + "\n"
            + f"INSERT INTO schema_migrations(version, description, applied_at_utc) "
            + f"VALUES ({migration.version}, {description}, {applied_at});\n"
            + f"PRAGMA user_version = {migration.version};\n"
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
    return current_version


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
    connection = connect_database(path, readonly=readonly, immutable=immutable)
    try:
        if migrate:
            apply_migrations(connection)
            assert_fts5_available(connection)
        yield connection
    finally:
        connection.close()

"""Derived, read-only SQLite FTS5 index for one R7 knowledge pack."""

from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .errors import ErrorCode, MemoryMountError
from .limits import MountLimits
from .records import PackContent, PackRecord, record_content_sha256


INDEX_FORMAT_VERSION = 1
INDEX_TOKENIZER = "trigram"
_INDEX_SCHEMA = """
CREATE TABLE pack_index_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE pack_records (
    rowid INTEGER PRIMARY KEY,
    pack_id TEXT NOT NULL,
    record_id TEXT NOT NULL UNIQUE,
    global_id TEXT NOT NULL UNIQUE,
    record_type TEXT NOT NULL,
    subject_key TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    confidence REAL NOT NULL,
    status TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    details_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    updated_epoch REAL NOT NULL
);

CREATE TABLE pack_scopes (
    record_id TEXT NOT NULL,
    scope_type TEXT NOT NULL,
    scope_key TEXT NOT NULL,
    PRIMARY KEY(record_id, scope_type, scope_key)
);

CREATE VIRTUAL TABLE pack_records_fts USING fts5(
    subject_key,
    title,
    body,
    content='pack_records',
    content_rowid='rowid',
    tokenize='trigram'
);
"""


def _monotonic() -> float:
    return time.monotonic()


def _fail(code: ErrorCode, message: str, **details: Any) -> None:
    raise MemoryMountError(code, message, details=details)


def _probe_trigram() -> None:
    try:
        probe = sqlite3.connect(":memory:")
        try:
            probe.execute(
                "CREATE VIRTUAL TABLE r7_probe USING fts5(x, tokenize='trigram')"
            )
        finally:
            probe.close()
    except sqlite3.Error as exc:
        _fail(
            ErrorCode.FTS5_TRIGRAM_UNAVAILABLE,
            "SQLite FTS5 trigram tokenizer is unavailable.",
            reason=exc.__class__.__name__,
        )


def _open_readonly(path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro&immutable=1",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("PRAGMA busy_timeout = 1000")
        return connection
    except sqlite3.Error as exc:
        _fail(
            ErrorCode.IO_ERROR,
            "Could not open the pack index read-only.",
            reason=exc.__class__.__name__,
        )


@dataclass(frozen=True)
class IndexHit:
    record: PackRecord
    match_reason: str
    score: float


class PackIndex:
    """One verified pack index opened read-only."""

    def __init__(
        self,
        *,
        path: Path,
        content: PackContent,
        connection: sqlite3.Connection,
        reused: bool,
    ) -> None:
        self.path = path
        self.content = content
        self.connection = connection
        self.reused = reused

    @classmethod
    def open_or_build(
        cls,
        content: PackContent,
        *,
        cache_dir: Path,
        limits: MountLimits,
    ) -> PackIndex:
        _probe_trigram()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            _fail(
                ErrorCode.IO_ERROR,
                "Could not create the pack index cache directory.",
                reason=exc.__class__.__name__,
            )
        manifest = content.manifest
        digest = manifest.content_hash.removeprefix("sha256:")
        path = cache_dir / f"{manifest.pack_id}-{digest[:12]}.sqlite3"
        reused = False
        if not cls._is_compatible(path, content):
            cls._build_atomically(path, content)
        else:
            reused = True
        connection = _open_readonly(path)
        return cls(path=path, content=content, connection=connection, reused=reused)

    @classmethod
    def _is_compatible(cls, path: Path, content: PackContent) -> bool:
        if not path.is_file():
            return False
        connection: sqlite3.Connection | None = None
        try:
            connection = _open_readonly(path)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version != INDEX_FORMAT_VERSION:
                return False
            meta = {
                str(row["key"]): str(row["value"])
                for row in connection.execute("SELECT key, value FROM pack_index_meta")
            }
            if not (
                meta.get("indexFormatVersion") == str(INDEX_FORMAT_VERSION)
                and meta.get("packId") == content.manifest.pack_id
                and meta.get("contentHash") == content.manifest.content_hash
                and meta.get("tokenizer") == INDEX_TOKENIZER
                and meta.get("recordCount") == str(content.record_count)
            ):
                return False
            return cls._rows_are_intact(connection, content)
        except (sqlite3.Error, MemoryMountError, ValueError, TypeError):
            return False
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _rows_are_intact(
        connection: sqlite3.Connection,
        content: PackContent,
    ) -> bool:
        rows = connection.execute(
            """
            SELECT pack_id, record_id, global_id, record_type, subject_key, title,
                   body, confidence, status, scopes_json, details_json,
                   content_sha256, updated_at_utc
            FROM pack_records
            ORDER BY record_id
            """
        ).fetchall()
        expected_by_id = {record.record_id: record for record in content.records}
        if len(rows) != len(expected_by_id):
            return False
        for row in rows:
            record = expected_by_id.get(str(row["record_id"]))
            if record is None:
                return False
            if (
                str(row["pack_id"]) != content.manifest.pack_id
                or str(row["global_id"]) != record.global_id
                or str(row["status"]) != record.status
                or float(row["confidence"]) != record.confidence
                or str(row["updated_at_utc"]) != record.updated_at_utc
            ):
                return False
            try:
                scopes = tuple(json.loads(str(row["scopes_json"])))
                details = json.loads(str(row["details_json"]))
            except (ValueError, TypeError):
                return False
            expected = str(row["content_sha256"])
            actual = record_content_sha256(
                record_type=str(row["record_type"]),
                subject_key=str(row["subject_key"]),
                title=str(row["title"]),
                body=str(row["body"]),
                scopes=scopes,
                details=details,
            )
            if actual != expected:
                return False
        return True

    @classmethod
    def _build_atomically(cls, path: Path, content: PackContent) -> None:
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(str(temporary))
            connection.executescript(_INDEX_SCHEMA)
            for record in content.records:
                cursor = connection.execute(
                    """
                    INSERT INTO pack_records(
                        pack_id, record_id, global_id, record_type, subject_key, title, body,
                        source_kind, confidence, status, scopes_json, details_json,
                        content_sha256, updated_at_utc, updated_epoch
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    record.to_index_values(),
                )
                rowid = int(cursor.lastrowid or 0)
                connection.execute(
                    """
                    INSERT INTO pack_records_fts(rowid, subject_key, title, body)
                    VALUES (?, ?, ?, ?)
                    """,
                    (rowid, record.subject_key, record.title, record.body),
                )
                for scope in record.scopes:
                    connection.execute(
                        """
                        INSERT INTO pack_scopes(record_id, scope_type, scope_key)
                        VALUES (?, ?, ?)
                        """,
                        (record.record_id, scope["scopeType"], scope["scopeKey"]),
                    )
            meta = {
                "indexFormatVersion": str(INDEX_FORMAT_VERSION),
                "packId": content.manifest.pack_id,
                "contentHash": content.manifest.content_hash,
                "revision": content.manifest.revision,
                "recordCount": str(content.record_count),
                "tokenizer": INDEX_TOKENIZER,
                "builtAtUtc": _utc_now_iso(),
            }
            connection.executemany(
                "INSERT INTO pack_index_meta(key, value) VALUES (?, ?)",
                sorted(meta.items()),
            )
            connection.execute(f"PRAGMA user_version = {int(INDEX_FORMAT_VERSION)}")
            connection.commit()
        except sqlite3.Error as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            _fail(
                ErrorCode.INDEX_INCOMPATIBLE,
                "Could not build the pack index.",
                reason=exc.__class__.__name__,
            )
        finally:
            if connection is not None:
                connection.close()
        try:
            os.replace(temporary, path)
        except OSError as exc:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            _fail(
                ErrorCode.IO_ERROR,
                "Could not publish the pack index.",
                reason=exc.__class__.__name__,
            )

    def search(
        self,
        query: str,
        *,
        limit: int,
        record_types: Sequence[str],
        scope_type: str,
        scope_key: str,
        deadline: float,
        max_short_scan: int,
    ) -> tuple[IndexHit, ...]:
        if len(query) >= 3:
            hits = self._fts_search(
                query,
                limit=limit,
                record_types=record_types,
                scope_type=scope_type,
                scope_key=scope_key,
                deadline=deadline,
            )
            if hits or len(query) > 16:
                return hits
            fallback = self._substring_search(
                query,
                limit=limit,
                record_types=record_types,
                scope_type=scope_type,
                scope_key=scope_key,
                deadline=deadline,
                max_short_scan=max_short_scan,
            )
            return tuple(
                IndexHit(record=hit.record, match_reason="cjk-substring-fallback", score=hit.score)
                for hit in fallback
            )
        return self._substring_search(
            query,
            limit=limit,
            record_types=record_types,
            scope_type=scope_type,
            scope_key=scope_key,
            deadline=deadline,
            max_short_scan=max_short_scan,
        )

    def _fts_search(
        self,
        query: str,
        *,
        limit: int,
        record_types: Sequence[str],
        scope_type: str,
        scope_key: str,
        deadline: float,
    ) -> tuple[IndexHit, ...]:
        clauses, parameters = self._filters(record_types, scope_type, scope_key)
        parameters = [_fts_phrase(query), *parameters]
        sql = (
            "SELECT r.record_id, r.global_id, r.record_type, r.subject_key, r.title, "
            "r.body, r.source_kind, r.confidence, r.status, r.scopes_json, "
            "r.details_json, r.content_sha256, r.updated_at_utc, r.updated_epoch, "
            "bm25(pack_records_fts) AS rank "
            "FROM pack_records_fts "
            "JOIN pack_records r ON r.rowid = pack_records_fts.rowid "
            "WHERE pack_records_fts MATCH ? AND "
            + " AND ".join(clauses)
            + " ORDER BY rank, r.updated_epoch DESC, r.record_id LIMIT ?"
        )
        rows = self._execute(sql, [*parameters, limit], deadline)
        return tuple(
            IndexHit(
                record=self._record_from_row(row),
                match_reason="fts5-trigram",
                score=-float(row["rank"]),
            )
            for row in rows
        )

    def _substring_search(
        self,
        query: str,
        *,
        limit: int,
        record_types: Sequence[str],
        scope_type: str,
        scope_key: str,
        deadline: float,
        max_short_scan: int,
    ) -> tuple[IndexHit, ...]:
        if self.content.record_count > max_short_scan:
            _fail(
                ErrorCode.QUERY_TOO_BROAD,
                "Substring fallback corpus exceeds the configured scan limit.",
                recordCount=self.content.record_count,
                limit=max_short_scan,
            )
        clauses, parameters = self._filters(record_types, scope_type, scope_key)
        sql = (
            "SELECT r.record_id, r.global_id, r.record_type, r.subject_key, r.title, "
            "r.body, r.source_kind, r.confidence, r.status, r.scopes_json, "
            "r.details_json, r.content_sha256, r.updated_at_utc, r.updated_epoch, "
            "CASE WHEN instr(lower(r.title), lower(?)) > 0 THEN 0 "
            "WHEN instr(lower(r.subject_key), lower(?)) > 0 THEN 1 ELSE 2 END AS match_field "
            "FROM pack_records r WHERE "
            + " AND ".join(clauses)
            + " AND (instr(lower(r.title), lower(?)) > 0 "
            "OR instr(lower(r.subject_key), lower(?)) > 0 "
            "OR instr(lower(r.body), lower(?)) > 0) "
            "ORDER BY match_field, r.updated_epoch DESC, r.record_id LIMIT ?"
        )
        query_parameters = [query, query, *parameters, query, query, query, limit]
        rows = self._execute(sql, query_parameters, deadline)
        return tuple(
            IndexHit(
                record=self._record_from_row(row),
                match_reason="cjk-substring-fallback",
                score=float(3 - int(row["match_field"])),
            )
            for row in rows
        )

    def _filters(
        self,
        record_types: Sequence[str],
        scope_type: str,
        scope_key: str,
    ) -> tuple[list[str], list[Any]]:
        clauses = ["r.pack_id = ?"]
        parameters: list[Any] = [self.content.manifest.pack_id]
        if record_types:
            clauses.append("r.record_type IN (" + ",".join("?" for _ in record_types) + ")")
            parameters.extend(record_types)
        if scope_type:
            scope_clause = (
                "EXISTS (SELECT 1 FROM pack_scopes s WHERE s.record_id = r.record_id "
                "AND s.scope_type = ?"
            )
            parameters.append(scope_type)
            if scope_key:
                scope_clause += " AND s.scope_key = ?"
                parameters.append(scope_key)
            clauses.append(scope_clause + ")")
        elif scope_key:
            _fail(ErrorCode.QUERY_INVALID, "scopeKey requires scopeType.")
        return clauses, parameters

    def _execute(
        self,
        sql: str,
        parameters: list[Any],
        deadline: float,
    ) -> list[sqlite3.Row]:
        def progress_handler() -> int:
            return 1 if _monotonic() > deadline else 0

        self.connection.set_progress_handler(progress_handler, 1_000)
        try:
            return self.connection.execute(sql, parameters).fetchall()
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "interrupt" in message:
                _fail(ErrorCode.QUERY_TIMEOUT, "Pack query exceeded its deadline.")
            if "tokenizer" in message:
                _fail(
                    ErrorCode.FTS5_TRIGRAM_UNAVAILABLE,
                    "SQLite FTS5 trigram tokenizer is unavailable.",
                    reason=exc.__class__.__name__,
                )
            _fail(
                ErrorCode.QUERY_INVALID,
                "Pack query failed.",
                reason=exc.__class__.__name__,
            )
        except sqlite3.Error as exc:
            _fail(
                ErrorCode.INTERNAL,
                "Pack index query failed.",
                reason=exc.__class__.__name__,
            )
        finally:
            self.connection.set_progress_handler(None, 0)

    def _record_from_row(self, row: sqlite3.Row) -> PackRecord:
        scopes = tuple(json.loads(str(row["scopes_json"])))
        details = json.loads(str(row["details_json"]))
        return PackRecord(
            pack_id=self.content.manifest.pack_id,
            record_id=str(row["record_id"]),
            record_type=str(row["record_type"]),
            subject_key=str(row["subject_key"]),
            title=str(row["title"]),
            body=str(row["body"]),
            source_kind=str(row["source_kind"]),
            confidence=float(row["confidence"]),
            status=str(row["status"]),
            scopes=scopes,
            updated_at_utc=str(row["updated_at_utc"]),
            updated_epoch=float(row["updated_epoch"]),
            details=details,
            content_sha256=str(row["content_sha256"]),
        )

    def close(self) -> None:
        try:
            self.connection.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> PackIndex:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def _fts_phrase(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def _utc_now_iso() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )

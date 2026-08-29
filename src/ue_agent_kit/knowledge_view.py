"""Read-only local Knowledge Web for UEAgentKit (Track V1).

This module serves a strictly read-only, localhost-only browser over the
existing Project Memory database and the asset index database.

Architectural contract (Track V detailed plan):

- SQLite is opened ``readonly=True`` at the connection level. The Web process
  never migrates, never writes, and never creates databases.
- Only GET routes exist. POST / PUT / PATCH / DELETE always answer 405.
- Static serving is a route whitelist for known package files only; URL paths
  are never mapped onto disk paths.
- Collections are bounded: default page 50, hard page limit 200.
- Connections are short-lived per request so Memory Agent writes in another
  process become visible on subsequent requests.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, urlsplit

from .active_work import WorkStatus, get_work_item
from .config import DEFAULT_DATABASE, DEFAULT_MEMORY_DATABASE
from .database import CURRENT_SCHEMA_VERSION as ASSET_CURRENT_SCHEMA_VERSION
from .database import get_schema_version, open_database
from .memory_reports import (
    active_work_payload,
    knowledge_node_payload,
    memory_record_payload,
)
from .memory_schema import CURRENT_MEMORY_SCHEMA_VERSION
from .memory_tree import _row_to_node
from .project_memory import (
    MemoryRecordType,
    MemorySourceKind,
    MemoryStatus,
    _fts_match_query,
    get_memory_record,
    open_project_memory_database,
)

KNOWLEDGE_VIEW_SCHEMA_VERSION = "1.0"
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 200
DEFAULT_RECORD_PREVIEW_CHARS = 280
NODE_ID_PATTERN = re.compile(r"^kn_[0-9a-f]{32}$")
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
WORK_STATUSES = tuple(item.value for item in WorkStatus)
RECORD_TYPES = tuple(item.value for item in MemoryRecordType)
RECORD_STATUSES = tuple(item.value for item in MemoryStatus)
SOURCE_KINDS = tuple(item.value for item in MemorySourceKind)

_INDEX_HTML_PATH = Path(__file__).resolve().parent / "web" / "index.html"


class KnowledgeViewError(Exception):
    """Read-model error carrying a stable code and HTTP status."""

    def __init__(self, code: str, message: str, http_status: int = 500) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class KnowledgeViewConfig:
    """Configuration for one read-only Knowledge Web server process."""

    memory_database: Path = field(default_factory=lambda: DEFAULT_MEMORY_DATABASE)
    database: Path | None = field(default_factory=lambda: DEFAULT_DATABASE)
    project_key: str = ""
    host: str = "127.0.0.1"
    port: int = 8765

    def __post_init__(self) -> None:
        host = str(self.host).strip().lower()
        if host == "localhost":
            host = "127.0.0.1"
        if host not in LOOPBACK_HOSTS:
            raise ValueError(
                "Knowledge Web binds loopback addresses only; refusing host "
                f"{self.host!r}. Use 127.0.0.1."
            )
        if not isinstance(self.port, int) or self.port < 0 or self.port > 65535:
            raise ValueError("port must be an integer between 0 and 65535.")
        object.__setattr__(self, "host", host)
        object.__setattr__(
            self, "memory_database", Path(self.memory_database).expanduser().resolve()
        )
        database = self.database
        object.__setattr__(
            self,
            "database",
            Path(database).expanduser().resolve() if database is not None else None,
        )


def _page_bounds(limit: Any, offset: Any) -> tuple[int, int]:
    try:
        normalized_limit = int(limit)
        normalized_offset = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit and offset must be integers.") from exc
    if normalized_limit < 1 or normalized_limit > MAX_PAGE_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_PAGE_LIMIT}.")
    if normalized_offset < 0:
        raise ValueError("offset must not be negative.")
    return normalized_limit, normalized_offset


def _require_choice(value: str, allowed: tuple[str, ...], field_name: str) -> str:
    if value and value not in allowed:
        raise ValueError(f"{field_name} must be one of: {', '.join(allowed)}.")
    return value


class KnowledgeViewReadService:
    """Bounded read model over the existing authoritative SQLite stores.

    Every public method opens a short-lived read-only connection, runs one
    bounded query, and closes the connection. No method writes, migrates, or
    holds a long-lived snapshot.
    """

    def __init__(self, config: KnowledgeViewConfig) -> None:
        self.config = config
        self.project_key = config.project_key.strip()

    # ------------------------------------------------------------------
    # connection helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _memory_connection(self) -> Iterator[sqlite3.Connection]:
        path = self.config.memory_database
        if not path.is_file():
            raise KnowledgeViewError(
                "memoryDatabaseMissing",
                "Project Memory database not found: "
                f"{path}. Start the Memory workflow first or pass --memory-database.",
                http_status=500,
            )
        try:
            with open_project_memory_database(path, readonly=True) as connection:
                yield connection
        except RuntimeError as exc:
            raise KnowledgeViewError(
                "memorySchemaMismatch",
                "Project Memory schema is not supported by this read-only view: "
                f"{exc} Supported schema: {CURRENT_MEMORY_SCHEMA_VERSION}.",
                http_status=500,
            ) from exc
        except sqlite3.Error as exc:
            raise KnowledgeViewError(
                "memoryDatabaseUnavailable",
                "Project Memory database could not be opened read-only.",
                http_status=500,
            ) from exc

    @contextmanager
    def _asset_connection(self) -> Iterator[sqlite3.Connection | None]:
        path = self.config.database
        if path is None or not path.is_file():
            yield None
            return
        try:
            with open_database(path, readonly=True, migrate=False) as connection:
                version = get_schema_version(connection)
                if version != ASSET_CURRENT_SCHEMA_VERSION:
                    raise KnowledgeViewError(
                        "assetSchemaMismatch",
                        "Asset index schema is not supported by this read-only view: "
                        f"found {version}, supported {ASSET_CURRENT_SCHEMA_VERSION}.",
                        http_status=500,
                    )
                yield connection
        except sqlite3.Error as exc:
            raise KnowledgeViewError(
                "assetDatabaseUnavailable",
                "Asset index database could not be opened read-only.",
                http_status=500,
            ) from exc

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schemaVersion": KNOWLEDGE_VIEW_SCHEMA_VERSION,
            "projectKey": self.project_key,
            "readOnly": True,
            "memoryDatabase": {
                "path": str(self.config.memory_database),
                "present": self.config.memory_database.is_file(),
            },
            "assetDatabase": {
                "path": "" if self.config.database is None else str(self.config.database),
                "present": bool(
                    self.config.database is not None and self.config.database.is_file()
                ),
            },
        }
        memory_path = self.config.memory_database
        if memory_path.is_file():
            with self._memory_connection() as connection:
                version = get_schema_version(connection)
                if version != CURRENT_MEMORY_SCHEMA_VERSION:
                    raise KnowledgeViewError(
                        "memorySchemaMismatch",
                        "Project Memory schema is not supported by this read-only view: "
                        f"found {version}, supported {CURRENT_MEMORY_SCHEMA_VERSION}.",
                        http_status=500,
                    )
                payload["memoryDatabase"]["schemaVersion"] = version
                payload["memoryDatabase"]["recordCount"] = int(
                    connection.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0]
                )
                payload["memoryDatabase"]["countsByStatus"] = {
                    str(row[0]): int(row[1])
                    for row in connection.execute(
                        "SELECT status, COUNT(*) FROM memory_records GROUP BY status ORDER BY status"
                    )
                }
                payload["memoryDatabase"]["countsByType"] = {
                    str(row[0]): int(row[1])
                    for row in connection.execute(
                        "SELECT record_type, COUNT(*) FROM memory_records "
                        "GROUP BY record_type ORDER BY record_type"
                    )
                }
                payload["memoryDatabase"]["nodeCount"] = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM knowledge_nodes WHERE project_key = ?",
                        (self.project_key,),
                    ).fetchone()[0]
                )
                payload["memoryDatabase"]["activeWorkCount"] = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM active_work_items WHERE project_key = ?",
                        (self.project_key,),
                    ).fetchone()[0]
                )
        else:
            payload["memoryDatabase"]["error"] = "memoryDatabaseMissing"
        asset_path = self.config.database
        if asset_path is not None and asset_path.is_file():
            with self._asset_connection() as connection:
                if connection is not None:
                    payload["assetDatabase"]["schemaVersion"] = get_schema_version(connection)
                    payload["assetDatabase"]["assetCount"] = int(
                        connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
                    )
                    payload["assetDatabase"]["referenceCount"] = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM references_table"
                        ).fetchone()[0]
                    )
        else:
            payload["assetDatabase"]["error"] = "assetDatabaseMissing"
        return payload

    # ------------------------------------------------------------------
    # knowledge tree
    # ------------------------------------------------------------------

    def tree_children(self, parent_node_id: str, limit: int) -> dict[str, Any]:
        limit, _ = _page_bounds(limit, 0)
        if parent_node_id and not NODE_ID_PATTERN.fullmatch(parent_node_id):
            raise ValueError("parent must be a knowledge node id or omitted for root nodes.")
        with self._memory_connection() as connection:
            if parent_node_id:
                parent_row = connection.execute(
                    "SELECT node_id FROM knowledge_nodes WHERE node_id = ? AND project_key = ?",
                    (parent_node_id, self.project_key),
                ).fetchone()
                if parent_row is None:
                    raise KeyError(f"Knowledge node not found: {parent_node_id}")
                rows = connection.execute(
                    """
                    SELECT * FROM knowledge_nodes
                    WHERE project_key = ? AND parent_node_id = ?
                    ORDER BY path LIMIT ?
                    """,
                    (self.project_key, parent_node_id, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM knowledge_nodes
                    WHERE project_key = ? AND (parent_node_id IS NULL OR parent_node_id = '')
                    ORDER BY path LIMIT ?
                    """,
                    (self.project_key, limit),
                ).fetchall()
            node_ids = [str(row["node_id"]) for row in rows]
            child_counts: dict[str, int] = {}
            record_counts: dict[str, int] = {}
            if node_ids:
                placeholders = ",".join("?" for _ in node_ids)
                for row in connection.execute(
                    "SELECT parent_node_id AS node_id, COUNT(*) AS total "
                    f"FROM knowledge_nodes WHERE parent_node_id IN ({placeholders}) "
                    "GROUP BY parent_node_id",
                    node_ids,
                ):
                    child_counts[str(row["node_id"])] = int(row["total"])
                for row in connection.execute(
                    "SELECT node_id, COUNT(*) AS total FROM memory_records "
                    f"WHERE node_id IN ({placeholders}) GROUP BY node_id",
                    node_ids,
                ):
                    record_counts[str(row["node_id"])] = int(row["total"])
            items = [
                {
                    **knowledge_node_payload(_row_to_node(row)),
                    "childCount": child_counts.get(str(row["node_id"]), 0),
                    "recordCount": record_counts.get(str(row["node_id"]), 0),
                }
                for row in rows
            ]
            return {
                "schemaVersion": KNOWLEDGE_VIEW_SCHEMA_VERSION,
                "parentNode": parent_node_id,
                "itemCount": len(items),
                "items": items,
            }

    def node_detail(self, node_id: str, record_limit: int) -> dict[str, Any]:
        limit, _ = _page_bounds(record_limit, 0)
        with self._memory_connection() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_nodes WHERE node_id = ? AND project_key = ?",
                (node_id, self.project_key),
            ).fetchone()
            if row is None:
                raise KeyError(f"Knowledge node not found: {node_id}")
            node = _row_to_node(row)
            child_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE parent_node_id = ?",
                    (node_id,),
                ).fetchone()[0]
            )
            record_rows = connection.execute(
                """
                SELECT record_id, title, record_type, status, subject_key,
                       updated_at_utc, observed_at_utc
                FROM memory_records
                WHERE node_id = ?
                ORDER BY updated_at_utc DESC, record_id DESC
                LIMIT ?
                """,
                (node_id, limit),
            ).fetchall()
            records = [
                {
                    "recordId": str(record_row["record_id"]),
                    "title": str(record_row["title"]),
                    "recordType": str(record_row["record_type"]),
                    "status": str(record_row["status"]),
                    "subjectKey": str(record_row["subject_key"]),
                    "updatedAtUtc": str(record_row["updated_at_utc"]),
                    "observedAtUtc": str(record_row["observed_at_utc"]),
                }
                for record_row in record_rows
            ]
            return {
                "schemaVersion": KNOWLEDGE_VIEW_SCHEMA_VERSION,
                "node": knowledge_node_payload(node),
                "childCount": child_count,
                "recordCount": len(records),
                "records": records,
            }

    # ------------------------------------------------------------------
    # memory records
    # ------------------------------------------------------------------

    def records(
        self,
        *,
        record_type: str = "",
        status: str = "",
        source: str = "",
        node: str = "",
        subject: str = "",
        query: str = "",
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit, offset = _page_bounds(limit, offset)
        record_type = _require_choice(record_type, RECORD_TYPES, "type")
        status = _require_choice(status, RECORD_STATUSES, "status")
        source = _require_choice(source, SOURCE_KINDS, "source")
        clauses = ["r.project_key = ?"]
        parameters: list[Any] = [self.project_key]
        joins = ""
        if record_type:
            clauses.append("r.record_type = ?")
            parameters.append(record_type)
        if status:
            clauses.append("r.status = ?")
            parameters.append(status)
        if source:
            clauses.append("r.source_kind = ?")
            parameters.append(source)
        if node:
            clauses.append("r.node_id = ?")
            parameters.append(node)
        if subject:
            clauses.append("r.subject_key = ?")
            parameters.append(subject)
        if query:
            match = _fts_match_query(query)
            joins = "JOIN memory_records_fts AS fts ON fts.rowid = r.rowid"
            clauses.append("fts MATCH ?")
            parameters.append(match)
        where = " AND ".join(clauses)
        with self._memory_connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM memory_records AS r {joins} WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT r.record_id, r.title, r.subject_key, r.record_type, r.source_kind,
                       r.status, r.observed_at_utc, r.updated_at_utc, r.body
                FROM memory_records AS r {joins}
                WHERE {where}
                ORDER BY r.updated_at_utc DESC, r.record_id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
            items = []
            for row in rows:
                body = str(row["body"])
                preview = body[:DEFAULT_RECORD_PREVIEW_CHARS]
                items.append(
                    {
                        "recordId": str(row["record_id"]),
                        "title": str(row["title"]),
                        "subjectKey": str(row["subject_key"]),
                        "recordType": str(row["record_type"]),
                        "sourceKind": str(row["source_kind"]),
                        "status": str(row["status"]),
                        "observedAtUtc": str(row["observed_at_utc"]),
                        "updatedAtUtc": str(row["updated_at_utc"]),
                        "bodyPreview": preview,
                        "bodyTruncated": len(body) > len(preview),
                    }
                )
            return {
                "schemaVersion": KNOWLEDGE_VIEW_SCHEMA_VERSION,
                "total": total,
                "limit": limit,
                "offset": offset,
                "itemCount": len(items),
                "items": items,
            }

    def record_detail(self, record_id: str) -> dict[str, Any]:
        if not record_id:
            raise ValueError("record id must not be empty.")
        with self._memory_connection() as connection:
            record = get_memory_record(connection, record_id)
            payload = memory_record_payload(record)
            status_events = connection.execute(
                """
                SELECT from_status, to_status, reason, changed_at_utc, details_json
                FROM memory_status_events WHERE record_id = ? ORDER BY event_id
                """,
                (record.record_id,),
            ).fetchall()
            payload["statusHistory"] = [
                {
                    "fromStatus": str(event["from_status"]),
                    "toStatus": str(event["to_status"]),
                    "reason": str(event["reason"]),
                    "changedAtUtc": str(event["changed_at_utc"]),
                    "details": json.loads(str(event["details_json"] or "{}")),
                }
                for event in status_events
            ]
            inbound = connection.execute(
                """
                SELECT from_record_id, relation_kind, created_at_utc, details_json
                FROM memory_relations WHERE to_record_id = ? ORDER BY created_at_utc
                """,
                (record.record_id,),
            ).fetchall()
            payload["inboundRelations"] = [
                {
                    "fromRecordId": str(row["from_record_id"]),
                    "relationKind": str(row["relation_kind"]),
                    "createdAtUtc": str(row["created_at_utc"]),
                    "details": json.loads(str(row["details_json"] or "{}")),
                }
                for row in inbound
            ]
            payload["schemaVersion"] = KNOWLEDGE_VIEW_SCHEMA_VERSION
            return payload

    # ------------------------------------------------------------------
    # active work
    # ------------------------------------------------------------------

    def work_list(
        self,
        *,
        status: str = "",
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        limit, offset = _page_bounds(limit, offset)
        status = _require_choice(status, WORK_STATUSES, "status")
        clauses = ["project_key = ?"]
        parameters: list[Any] = [self.project_key]
        if status:
            clauses.append("status = ?")
            parameters.append(status)
        where = " AND ".join(clauses)
        with self._memory_connection() as connection:
            total = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM active_work_items WHERE {where}", parameters
                ).fetchone()[0]
            )
            rows = connection.execute(
                f"""
                SELECT work_item_id FROM active_work_items WHERE {where}
                ORDER BY priority DESC, updated_at_utc DESC, work_item_id
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
            items = [
                active_work_payload(
                    get_work_item(
                        connection,
                        work_item_id=str(row["work_item_id"]),
                        project_key=self.project_key,
                    )
                )
                for row in rows
            ]
            return {
                "schemaVersion": KNOWLEDGE_VIEW_SCHEMA_VERSION,
                "total": total,
                "limit": limit,
                "offset": offset,
                "itemCount": len(items),
                "items": items,
            }

    def work_detail(self, work_item_id: str) -> dict[str, Any]:
        if not work_item_id:
            raise ValueError("work item id must not be empty.")
        with self._memory_connection() as connection:
            work = get_work_item(
                connection, work_item_id=work_item_id, project_key=self.project_key
            )
            payload = active_work_payload(work)
            payload["schemaVersion"] = KNOWLEDGE_VIEW_SCHEMA_VERSION
            return payload

    # ------------------------------------------------------------------
    # search
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int) -> dict[str, Any]:
        limit, _ = _page_bounds(limit, 0)
        match = _fts_match_query(query)
        with self._memory_connection() as connection:
            rows = connection.execute(
                """
                SELECT r.record_id, bm25(memory_records_fts) AS rank
                FROM memory_records_fts
                JOIN memory_records AS r ON r.rowid = memory_records_fts.rowid
                WHERE memory_records_fts MATCH ? AND r.project_key = ?
                ORDER BY rank, r.updated_at_utc DESC, r.record_id
                LIMIT ?
                """,
                (match, self.project_key, limit),
            ).fetchall()
            items = []
            for row in rows:
                record = get_memory_record(connection, str(row["record_id"]))
                items.append(
                    {
                        "rank": float(row["rank"]),
                        "record": memory_record_payload(record),
                    }
                )
            return {
                "schemaVersion": KNOWLEDGE_VIEW_SCHEMA_VERSION,
                "query": query,
                "itemCount": len(items),
                "items": items,
            }


# ----------------------------------------------------------------------
# HTTP layer
# ----------------------------------------------------------------------


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    handler.end_headers()
    handler.wfile.write(body)


def _send_error_json(handler: BaseHTTPRequestHandler, status: int, code: str, message: str) -> None:
    _send_json(handler, status, {"error": {"code": code, "message": message}})


def _read_index_html() -> bytes:
    return _INDEX_HTML_PATH.read_bytes()


_QUERY_PARAMS: dict[str, frozenset[str]] = {
    "/api/tree": frozenset({"parent", "limit"}),
    "/api/records": frozenset(
        {"type", "status", "source", "node", "subject", "query", "limit", "offset"}
    ),
    "/api/work": frozenset({"status", "limit", "offset"}),
    "/api/search": frozenset({"q", "limit"}),
}
_DEFAULT_LIMIT = str(DEFAULT_PAGE_LIMIT)


def _single_query_value(query: dict[str, list[str]], key: str, default: str = "") -> str:
    values = query.get(key)
    if not values:
        return default
    if len(values) > 1:
        raise ValueError(f"parameter {key} must appear at most once.")
    return values[0]


class KnowledgeViewHandler(BaseHTTPRequestHandler):
    """GET-only whitelist router. No mutation route exists by design."""

    server_version = "UEAgentKitKnowledgeView/0.7.0"
    protocol_version = "HTTP/1.1"

    @property
    def service(self) -> KnowledgeViewReadService:
        return self.server.knowledge_service  # type: ignore[attr-defined]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        pass

    # -- dispatch -------------------------------------------------------

    def _dispatch(self) -> None:
        split = urlsplit(self.path)
        path = split.path.rstrip("/") or "/"
        try:
            if path in {"/", "/index.html"}:
                self._serve_index()
                return
            if not path.startswith("/api/"):
                _send_error_json(self, 404, "notFound", f"Unknown route: {path}")
                return
            query = parse_qs(split.query, keep_blank_values=True)
            allowed = _QUERY_PARAMS.get(path)
            if allowed is not None:
                unknown = sorted(set(query) - allowed)
                if unknown:
                    raise ValueError(
                        f"unknown parameter(s): {', '.join(unknown)} for {path}."
                    )
            if path == "/api/status":
                _send_json(self, 200, self.service.status())
            elif path == "/api/tree":
                self._handle_tree(query)
            elif path.startswith("/api/node/"):
                node_id = path.removeprefix("/api/node/")
                if not node_id or "/" in node_id:
                    raise ValueError("node id must be a single path segment.")
                self._handle_node(node_id, query)
            elif path == "/api/records":
                self._handle_records(query)
            elif path.startswith("/api/record/"):
                record_id = path.removeprefix("/api/record/")
                if not record_id or "/" in record_id:
                    raise ValueError("record id must be a single path segment.")
                _send_json(self, 200, self.service.record_detail(record_id))
            elif path == "/api/work":
                self._handle_work(query)
            elif path.startswith("/api/work/"):
                work_item_id = path.removeprefix("/api/work/")
                if not work_item_id or "/" in work_item_id:
                    raise ValueError("work item id must be a single path segment.")
                _send_json(self, 200, self.service.work_detail(work_item_id))
            elif path == "/api/search":
                self._handle_search(query)
            else:
                _send_error_json(self, 404, "notFound", f"Unknown route: {path}")
        except KnowledgeViewError as exc:
            _send_error_json(self, exc.http_status, exc.code, exc.message)
        except ValueError as exc:
            _send_error_json(self, 400, "badRequest", str(exc))
        except KeyError as exc:
            message = str(exc.args[0]) if exc.args else "Requested item was not found."
            _send_error_json(self, 404, "notFound", message.strip("'\""))
        except BrokenPipeError:
            pass
        except Exception as exc:  # no traceback leak to the client
            _send_error_json(self, 500, "internalError", f"{type(exc).__name__}.")

    def _serve_index(self) -> None:
        body = _read_index_html()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    # -- route bodies ----------------------------------------------------

    def _handle_tree(self, query: dict[str, list[str]]) -> None:
        parent = _single_query_value(query, "parent")
        limit = _single_query_value(query, "limit", _DEFAULT_LIMIT)
        _send_json(self, 200, self.service.tree_children(parent, int(limit)))

    def _handle_node(self, node_id: str, query: dict[str, list[str]]) -> None:
        if set(query) - {"recordLimit"}:
            unknown = sorted(set(query) - {"recordLimit"})
            raise ValueError(f"unknown parameter(s): {', '.join(unknown)} for /api/node.")
        limit = _single_query_value(query, "recordLimit", _DEFAULT_LIMIT)
        _send_json(self, 200, self.service.node_detail(node_id, int(limit)))

    def _handle_records(self, query: dict[str, list[str]]) -> None:
        payload = self.service.records(
            record_type=_single_query_value(query, "type"),
            status=_single_query_value(query, "status"),
            source=_single_query_value(query, "source"),
            node=_single_query_value(query, "node"),
            subject=_single_query_value(query, "subject"),
            query=_single_query_value(query, "query"),
            limit=int(_single_query_value(query, "limit", _DEFAULT_LIMIT)),
            offset=int(_single_query_value(query, "offset", "0")),
        )
        _send_json(self, 200, payload)

    def _handle_work(self, query: dict[str, list[str]]) -> None:
        payload = self.service.work_list(
            status=_single_query_value(query, "status"),
            limit=int(_single_query_value(query, "limit", _DEFAULT_LIMIT)),
            offset=int(_single_query_value(query, "offset", "0")),
        )
        _send_json(self, 200, payload)

    def _handle_search(self, query: dict[str, list[str]]) -> None:
        q = _single_query_value(query, "q")
        if not q:
            raise ValueError("q must contain at least one searchable token.")
        limit = _single_query_value(query, "limit", _DEFAULT_LIMIT)
        _send_json(self, 200, self.service.search(q, int(limit)))

    # -- verbs -----------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def _reject_mutation(self) -> None:
        _send_error_json(
            self,
            405,
            "methodNotAllowed",
            "Knowledge Web is strictly read-only; only GET is supported.",
        )

    def do_POST(self) -> None:  # noqa: N802
        self._reject_mutation()

    def do_PUT(self) -> None:  # noqa: N802
        self._reject_mutation()

    def do_PATCH(self) -> None:  # noqa: N802
        self._reject_mutation()

    def do_DELETE(self) -> None:  # noqa: N802
        self._reject_mutation()


class KnowledgeViewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], service: KnowledgeViewReadService) -> None:
        super().__init__(address, KnowledgeViewHandler)
        self.knowledge_service = service


def make_server(config: KnowledgeViewConfig) -> KnowledgeViewHTTPServer:
    """Create (but do not start) the loopback read-only server.

    ``port=0`` selects an ephemeral port; tests use that to avoid colliding
    with a developer instance on 8765.
    """
    service = KnowledgeViewReadService(config)
    return KnowledgeViewHTTPServer((config.host, config.port), service)


def serve(config: KnowledgeViewConfig) -> dict[str, Any]:
    """Run the server until Ctrl+C. Returns a small summary payload."""
    server = make_server(config)
    bound_host, bound_port = server.server_address[:2]
    print(
        json.dumps(
            {
                "knowledgeView": "serving",
                "url": f"http://{bound_host}:{bound_port}/",
                "memoryDatabase": str(config.memory_database),
                "assetDatabase": "" if config.database is None else str(config.database),
                "projectKey": config.project_key,
                "readOnly": True,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {
        "knowledgeView": "stopped",
        "url": f"http://{bound_host}:{bound_port}/",
        "readOnly": True,
    }


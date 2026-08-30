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
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import parse_qs, unquote, urlsplit

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

# V2 graph limits (frozen by V2 plan section 4.2 / decision D2)
GRAPH_DEFAULT_LIMIT = 300
GRAPH_MAX_LIMIT = 1000
GRAPH_STRESS_LIMIT = 5000
GRAPH_MAX_DEPTH = 3
GRAPH_DIRECTIONS = ("outgoing", "incoming", "both")

# V2 stale grouping (frozen by V2 plan section 4.6 / decision D5)
STALE_GROUPINGS = ("nodePath", "scope", "recordType", "ageBucket")
STALE_STATUSES = ("stale", "conflicted", "superseded")
STALE_SAMPLE_LIMIT = 5
STALE_AGE_BUCKET_NAMES = ("0-7d", "8-30d", "31-90d", "90d+")

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


def _utc_age_days(updated_at_utc: str, now: datetime) -> float:
    """Age of an ISO-8601 UTC timestamp in days (0.0 for future/unknown)."""
    text = str(updated_at_utc).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


def _age_bucket_name(age_days: float) -> str:
    """Frozen stale age buckets: 0-7d / 8-30d / 31-90d / 90d+."""
    if age_days <= 7.0:
        return "0-7d"
    if age_days <= 30.0:
        return "8-30d"
    if age_days <= 90.0:
        return "31-90d"
    return "90d+"


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

    # ------------------------------------------------------------------
    # V2: asset reference graph (frozen contract V2 plan section 4.2)
    # ------------------------------------------------------------------

    def graph(
        self,
        *,
        root: str,
        depth: int = 1,
        direction: str = "outgoing",
        limit: int = GRAPH_DEFAULT_LIMIT,
        stress: int = 0,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        root = root.strip()
        if not root:
            raise ValueError("root must be a non-empty asset path.")
        try:
            normalized_depth = int(depth)
        except (TypeError, ValueError) as exc:
            raise ValueError("depth must be an integer.") from exc
        if normalized_depth < 0 or normalized_depth > GRAPH_MAX_DEPTH:
            raise ValueError(f"depth must be between 0 and {GRAPH_MAX_DEPTH}.")
        if direction not in GRAPH_DIRECTIONS:
            raise ValueError(
                f"direction must be one of: {', '.join(GRAPH_DIRECTIONS)}."
            )
        try:
            normalized_limit = int(limit)
            normalized_stress = int(stress)
        except (TypeError, ValueError) as exc:
            raise ValueError("limit and stress must be integers.") from exc
        if normalized_stress not in (0, 1):
            raise ValueError("stress must be 0 or 1.")
        max_limit = (
            GRAPH_STRESS_LIMIT if normalized_stress == 1 else GRAPH_MAX_LIMIT
        )
        if normalized_limit < 1 or normalized_limit > max_limit:
            if normalized_limit > GRAPH_MAX_LIMIT and normalized_stress == 0:
                raise ValueError("stress=1 required above 1000.")
            raise ValueError(f"limit must be between 1 and {max_limit}.")
        with self._asset_connection() as connection:
            if connection is None:
                raise KnowledgeViewError(
                    "assetDatabaseMissing",
                    "Asset index database is not configured or not found.",
                    http_status=500,
                )
            root_row = connection.execute(
                "SELECT asset_path, asset_class, asset_name, package_name "
                "FROM assets WHERE asset_path = ?",
                (root,),
            ).fetchone()
            if root_row is None:
                raise KnowledgeViewError(
                    "assetNotFound", f"Asset not found: {root}", http_status=404
                )
            project_assets: set[str] = {
                str(row[0])
                for row in connection.execute("SELECT asset_path FROM assets")
            }
            node_order: list[str] = [root]
            nodes: dict[str, dict[str, Any]] = {
                root: {
                    "assetPath": root,
                    "assetClass": str(root_row["asset_class"]),
                    "assetName": str(root_row["asset_name"]),
                    "packageName": str(root_row["package_name"]),
                    "referenceCount": 0,
                    "root": True,
                }
            }
            edges: dict[tuple[str, str], dict[str, Any]] = {}
            frontier: list[str] = [root]
            truncated_cap = False
            for _hop in range(1, normalized_depth + 1):
                if not frontier:
                    break
                placeholders = ",".join("?" for _ in frontier)
                if direction == "outgoing":
                    where = f"a.asset_path IN ({placeholders})"
                    parameters: list[Any] = list(frontier)
                elif direction == "incoming":
                    where = f"r.target_asset_path IN ({placeholders})"
                    parameters = list(frontier)
                else:
                    where = (
                        f"(a.asset_path IN ({placeholders}) "
                        f"OR r.target_asset_path IN ({placeholders}))"
                    )
                    parameters = [*frontier, *frontier]
                rows = connection.execute(
                    f"""
                    SELECT a.asset_path AS source_path,
                           r.target_asset_path AS target_path, r.kind
                    FROM references_table AS r
                    JOIN assets AS a ON a.id = r.asset_id
                    WHERE {where}
                    """,
                    parameters,
                ).fetchall()
                next_frontier: list[str] = []
                for row in rows:
                    source = str(row["source_path"])
                    target = str(row["target_path"])
                    for endpoint in (source, target):
                        if endpoint not in nodes and endpoint in project_assets:
                            if len(nodes) >= normalized_limit:
                                truncated_cap = True
                            else:
                                asset_row = connection.execute(
                                    "SELECT asset_path, asset_class, asset_name, "
                                    "package_name FROM assets WHERE asset_path = ?",
                                    (endpoint,),
                                ).fetchone()
                                nodes[endpoint] = {
                                    "assetPath": endpoint,
                                    "assetClass": str(asset_row["asset_class"]),
                                    "assetName": str(asset_row["asset_name"]),
                                    "packageName": str(asset_row["package_name"]),
                                    "referenceCount": 0,
                                    "root": False,
                                }
                                node_order.append(endpoint)
                                next_frontier.append(endpoint)
                    if source in nodes and target in nodes:
                        edge = edges.setdefault(
                            (source, target),
                            {
                                "source": source,
                                "target": target,
                                "kinds": [],
                                "referenceCount": 0,
                                "selfLoop": source == target,
                            },
                        )
                        kind = str(row["kind"])
                        if kind not in edge["kinds"]:
                            edge["kinds"].append(kind)
                        edge["referenceCount"] += 1
                frontier = next_frontier
            for (source, target), edge in edges.items():
                if source == target:
                    nodes[source]["referenceCount"] += edge["referenceCount"]
                else:
                    nodes[source]["referenceCount"] += edge["referenceCount"]
                    nodes[target]["referenceCount"] += edge["referenceCount"]
            for edge in edges.values():
                edge["kinds"].sort()
            node_list = [nodes[path] for path in node_order]
            edge_list = [
                edges[key] for key in sorted(edges, key=lambda item: (item[0], item[1]))
            ]
            truncated: dict[str, Any] | None = None
            if truncated_cap:
                truncated = {
                    "reason": "nodeLimit",
                    "limit": normalized_limit,
                    "count": len(node_list),
                }
            return {
                "meta": {
                    "root": root,
                    "depth": normalized_depth,
                    "direction": direction,
                    "nodeLimit": normalized_limit,
                    "nodeCount": len(node_list),
                    "edgeCount": len(edge_list),
                    "queryMs": round((time.perf_counter() - started) * 1000, 1),
                    "truncated": truncated is not None,
                },
                "nodes": node_list,
                "edges": edge_list,
                "truncated": truncated,
            }

    # ------------------------------------------------------------------
    # V2: impact / consumer view (frozen contract V2 plan section 4.3)
    # ------------------------------------------------------------------

    def impact(
        self,
        *,
        asset_path: str,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        kind: str = "",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        asset_path = asset_path.strip()
        if not asset_path:
            raise ValueError("asset_path must be a non-empty asset path.")
        limit, offset = _page_bounds(limit, offset)
        with self._asset_connection() as connection:
            if connection is None:
                raise KnowledgeViewError(
                    "assetDatabaseMissing",
                    "Asset index database is not configured or not found.",
                    http_status=500,
                )
            asset_row = connection.execute(
                "SELECT asset_path, asset_class, asset_name FROM assets "
                "WHERE asset_path = ?",
                (asset_path,),
            ).fetchone()
            if asset_row is None:
                raise KnowledgeViewError(
                    "assetNotFound", f"Asset not found: {asset_path}", http_status=404
                )
            clauses = ["r.target_asset_path = ?"]
            parameters: list[Any] = [asset_path]
            if kind:
                clauses.append("r.kind = ?")
                parameters.append(kind)
            where = " AND ".join(clauses)
            total_consumer_assets = int(
                connection.execute(
                    f"SELECT COUNT(DISTINCT r.asset_id) FROM references_table AS r "
                    f"WHERE {where}",
                    parameters,
                ).fetchone()[0]
            )
            counts_by_kind: dict[str, int] = {}
            for row in connection.execute(
                f"SELECT r.kind, COUNT(*) AS total FROM references_table AS r "
                f"WHERE {where} GROUP BY r.kind ORDER BY r.kind",
                parameters,
            ):
                counts_by_kind[str(row["kind"])] = int(row["total"])
            consumer_rows = connection.execute(
                f"""
                SELECT a.asset_path, a.asset_class, a.asset_name
                FROM references_table AS r
                JOIN assets AS a ON a.id = r.asset_id
                WHERE {where}
                GROUP BY a.asset_path, a.asset_class, a.asset_name
                ORDER BY a.asset_path
                LIMIT ? OFFSET ?
                """,
                [*parameters, limit, offset],
            ).fetchall()
            kind_rows = connection.execute(
                f"""
                SELECT a.asset_path, r.kind, COUNT(*) AS total
                FROM references_table AS r
                JOIN assets AS a ON a.id = r.asset_id
                WHERE {where}
                GROUP BY a.asset_path, r.kind
                ORDER BY a.asset_path, r.kind
                """,
                parameters,
            ).fetchall()
            kind_map: dict[str, dict[str, int]] = {}
            for row in kind_rows:
                path = str(row["asset_path"])
                bucket = kind_map.setdefault(path, {})
                bucket[str(row["kind"])] = int(row["total"])
            consumers = [
                {
                    "assetPath": str(row["asset_path"]),
                    "assetClass": str(row["asset_class"]),
                    "assetName": str(row["asset_name"]),
                    "kinds": list(kind_map.get(str(row["asset_path"]), {})),
                    "referenceCount": sum(
                        kind_map.get(str(row["asset_path"]), {}).values()
                    ),
                }
                for row in consumer_rows
            ]
            truncated: dict[str, Any] | None = None
            if offset + len(consumers) < total_consumer_assets:
                truncated = {
                    "reason": "limit",
                    "limit": limit,
                    "count": len(consumers),
                }
            return {
                "asset": {
                    "assetPath": str(asset_row["asset_path"]),
                    "assetClass": str(asset_row["asset_class"]),
                    "assetName": str(asset_row["asset_name"]),
                },
                "consumers": consumers,
                "countsByKind": counts_by_kind,
                "totalConsumerAssets": total_consumer_assets,
                "truncated": truncated,
                "meta": {
                    "queryMs": round((time.perf_counter() - started) * 1000, 1),
                },
            }

    # ------------------------------------------------------------------
    # V2: knowledge coverage (frozen contract V2 plan section 4.4)
    # ------------------------------------------------------------------

    def coverage(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        path_prefix: str = "",
    ) -> dict[str, Any]:
        started = time.perf_counter()
        limit, offset = _page_bounds(limit, offset)
        path_prefix = path_prefix.strip()
        with self._memory_connection() as connection:
            filter_sql = ""
            filter_parameters: list[Any] = []
            if path_prefix:
                filter_sql = " AND kn.path LIKE ?"
                filter_parameters.append(path_prefix + "%")
            total_nodes = int(
                connection.execute(
                    f"SELECT COUNT(*) FROM knowledge_nodes AS kn "
                    f"WHERE kn.project_key = ?{filter_sql}",
                    [self.project_key, *filter_parameters],
                ).fetchone()[0]
            )
            totals_row = connection.execute(
                f"""
                SELECT COUNT(mr.record_id) AS record_count,
                       SUM(CASE WHEN mr.status = 'valid' THEN 1 ELSE 0 END) AS valid_count,
                       SUM(CASE WHEN mr.status = 'stale' THEN 1 ELSE 0 END) AS stale_count,
                       SUM(CASE WHEN mr.status = 'conflicted' THEN 1 ELSE 0 END) AS conflicted_count,
                       SUM(CASE WHEN mr.status = 'superseded' THEN 1 ELSE 0 END) AS superseded_count,
                       SUM(CASE WHEN mr.status = 'unverified' THEN 1 ELSE 0 END) AS unverified_count
                FROM knowledge_nodes AS kn
                LEFT JOIN memory_records AS mr
                       ON mr.node_id = kn.node_id AND mr.project_key = kn.project_key
                WHERE kn.project_key = ?{filter_sql}
                """,
                [self.project_key, *filter_parameters],
            ).fetchone()
            rows = connection.execute(
                f"""
                SELECT kn.node_id, kn.path, kn.node_type, kn.title,
                       COUNT(mr.record_id) AS record_count,
                       SUM(CASE WHEN mr.status = 'valid' THEN 1 ELSE 0 END) AS valid_count,
                       SUM(CASE WHEN mr.status = 'stale' THEN 1 ELSE 0 END) AS stale_count,
                       SUM(CASE WHEN mr.status = 'conflicted' THEN 1 ELSE 0 END) AS conflicted_count,
                       SUM(CASE WHEN mr.status = 'superseded' THEN 1 ELSE 0 END) AS superseded_count,
                       SUM(CASE WHEN mr.status = 'unverified' THEN 1 ELSE 0 END) AS unverified_count,
                       MAX(mr.updated_at_utc) AS last_updated_utc
                FROM knowledge_nodes AS kn
                LEFT JOIN memory_records AS mr
                       ON mr.node_id = kn.node_id AND mr.project_key = kn.project_key
                WHERE kn.project_key = ?{filter_sql}
                GROUP BY kn.node_id, kn.path, kn.node_type, kn.title
                ORDER BY kn.path
                LIMIT ? OFFSET ?
                """,
                [self.project_key, *filter_parameters, limit, offset],
            ).fetchall()

            def _count(value: Any) -> int:
                return 0 if value is None else int(value)

            totals = {
                "recordCount": _count(totals_row["record_count"]),
                "validCount": _count(totals_row["valid_count"]),
                "staleCount": _count(totals_row["stale_count"]),
                "conflictedCount": _count(totals_row["conflicted_count"]),
                "supersededCount": _count(totals_row["superseded_count"]),
                "unverifiedCount": _count(totals_row["unverified_count"]),
            }
            node_items = [
                {
                    "nodeId": str(row["node_id"]),
                    "path": str(row["path"]),
                    "nodeType": str(row["node_type"]),
                    "title": str(row["title"]),
                    "recordCount": _count(row["record_count"]),
                    "validCount": _count(row["valid_count"]),
                    "staleCount": _count(row["stale_count"]),
                    "conflictedCount": _count(row["conflicted_count"]),
                    "supersededCount": _count(row["superseded_count"]),
                    "unverifiedCount": _count(row["unverified_count"]),
                    "lastUpdatedUtc": (
                        str(row["last_updated_utc"])
                        if row["last_updated_utc"] is not None
                        else None
                    ),
                }
                for row in rows
            ]
            truncated: dict[str, Any] | None = None
            if offset + len(node_items) < total_nodes:
                truncated = {
                    "reason": "limit",
                    "limit": limit,
                    "count": len(node_items),
                }
            return {
                "nodes": node_items,
                "totals": totals,
                "truncated": truncated,
                "meta": {
                    "queryMs": round((time.perf_counter() - started) * 1000, 1),
                    "truncated": truncated,
                },
            }

    # ------------------------------------------------------------------
    # V2: change / trust timeline (frozen contract V2 plan section 4.5)
    # ------------------------------------------------------------------

    def timeline(
        self,
        *,
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
        record_type: str = "",
        status: str = "",
        include_status_events: bool = False,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        limit, offset = _page_bounds(limit, offset)
        record_type = _require_choice(record_type, RECORD_TYPES, "type")
        status = _require_choice(status, RECORD_STATUSES, "status")
        with self._memory_connection() as connection:
            clauses = ["project_key = ?"]
            parameters: list[Any] = [self.project_key]
            if record_type:
                clauses.append("record_type = ?")
                parameters.append(record_type)
            if status:
                clauses.append("status = ?")
                parameters.append(status)
            where = " AND ".join(clauses)
            rows = connection.execute(
                f"""
                SELECT record_id, record_type, status, source_kind, title, updated_at_utc
                FROM memory_records
                WHERE {where}
                ORDER BY updated_at_utc DESC, record_id DESC
                """,
                parameters,
            ).fetchall()
            events: list[dict[str, Any]] = []
            for row in rows:
                record_id = str(row["record_id"])
                title = str(row["title"])
                events.append(
                    {
                        "eventId": f"{record_id}#updated",
                        "kind": "recordUpdated",
                        "timestampUtc": str(row["updated_at_utc"]),
                        "recordId": record_id,
                        "recordType": str(row["record_type"]),
                        "status": str(row["status"]),
                        "sourceKind": str(row["source_kind"]),
                        "titlePreview": title[:DEFAULT_RECORD_PREVIEW_CHARS],
                        "fromStatus": None,
                        "toStatus": None,
                    }
                )
            if include_status_events:
                status_clauses = ["r.project_key = ?"]
                status_parameters: list[Any] = [self.project_key]
                if record_type:
                    status_clauses.append("r.record_type = ?")
                    status_parameters.append(record_type)
                if status:
                    status_clauses.append("r.status = ?")
                    status_parameters.append(status)
                status_where = " AND ".join(status_clauses)
                status_rows = connection.execute(
                    f"""
                    SELECT e.event_id, e.record_id, e.from_status, e.to_status,
                           e.changed_at_utc, r.record_type, r.status, r.source_kind, r.title
                    FROM memory_status_events AS e
                    JOIN memory_records AS r ON r.record_id = e.record_id
                    WHERE {status_where}
                    """,
                    status_parameters,
                ).fetchall()
                for row in status_rows:
                    record_id = str(row["record_id"])
                    title = str(row["title"])
                    events.append(
                        {
                            "eventId": f"{record_id}#status:{int(row['event_id'])}",
                            "kind": "statusChanged",
                            "timestampUtc": str(row["changed_at_utc"]),
                            "recordId": record_id,
                            "recordType": str(row["record_type"]),
                            "status": str(row["status"]),
                            "sourceKind": str(row["source_kind"]),
                            "titlePreview": title[:DEFAULT_RECORD_PREVIEW_CHARS],
                            "fromStatus": str(row["from_status"]),
                            "toStatus": str(row["to_status"]),
                        }
                    )
            events.sort(
                key=lambda event: (event["timestampUtc"], event["eventId"]),
                reverse=True,
            )
            page = events[offset : offset + limit]
            truncated: dict[str, Any] | None = None
            if offset + len(page) < len(events):
                truncated = {
                    "reason": "limit",
                    "limit": limit,
                    "count": len(page),
                }
            return {
                "events": page,
                "truncated": truncated,
                "meta": {
                    "queryMs": round((time.perf_counter() - started) * 1000, 1),
                    "truncated": truncated,
                },
            }

    # ------------------------------------------------------------------
    # V2: stale distribution (frozen contract V2 plan section 4.6)
    # ------------------------------------------------------------------

    def stale(
        self,
        *,
        group_by: str = "nodePath",
        limit: int = DEFAULT_PAGE_LIMIT,
        offset: int = 0,
    ) -> dict[str, Any]:
        started = time.perf_counter()
        limit, offset = _page_bounds(limit, offset)
        if group_by not in STALE_GROUPINGS:
            raise ValueError(
                f"groupBy must be one of: {', '.join(STALE_GROUPINGS)}."
            )
        now = datetime.now(timezone.utc)
        with self._memory_connection() as connection:
            if group_by == "scope":
                select_sql = (
                    "mr.record_id, mr.status, mr.record_type, mr.updated_at_utc, "
                    "COALESCE(sc.scope_key, '<unattached>') AS group_key "
                    "FROM memory_records AS mr "
                    "LEFT JOIN (SELECT record_id, MIN(scope_key) AS scope_key "
                    "FROM memory_scopes GROUP BY record_id) AS sc "
                    "ON sc.record_id = mr.record_id "
                    "LEFT JOIN knowledge_nodes AS kn ON kn.node_id = mr.node_id"
                )
            elif group_by == "nodePath":
                select_sql = (
                    "mr.record_id, mr.status, mr.record_type, mr.updated_at_utc, "
                    "COALESCE(kn.path, '<unattached>') AS group_key "
                    "FROM memory_records AS mr "
                    "LEFT JOIN knowledge_nodes AS kn ON kn.node_id = mr.node_id"
                )
            elif group_by == "recordType":
                select_sql = (
                    "mr.record_id, mr.status, mr.record_type, mr.updated_at_utc "
                    "FROM memory_records AS mr"
                )
            else:
                select_sql = (
                    "mr.record_id, mr.status, mr.record_type, mr.updated_at_utc "
                    "FROM memory_records AS mr"
                )
            rows = list(
                connection.execute(
                    f"SELECT {select_sql} WHERE mr.project_key = ? "
                    f"AND mr.status IN ('stale', 'conflicted', 'superseded')",
                    (self.project_key,),
                ).fetchall()
            )
        rows.sort(
            key=lambda row: (str(row["updated_at_utc"]), str(row["record_id"])),
            reverse=True,
        )
        buckets: dict[str, dict[str, Any]] = {}
        total_records = 0
        total_by_status: dict[str, int] = {
            "stale": 0,
            "conflicted": 0,
            "superseded": 0,
        }
        for row in rows:
            record_id = str(row["record_id"])
            status = str(row["status"])
            age_days = _utc_age_days(str(row["updated_at_utc"]), now)
            age_bucket = _age_bucket_name(age_days)
            if group_by == "ageBucket":
                group_key = age_bucket
            elif group_by == "recordType":
                group_key = str(row["record_type"])
            else:
                group_key = str(row["group_key"])
            bucket = buckets.setdefault(
                group_key,
                {
                    "recordCount": 0,
                    "byStatus": {"stale": 0, "conflicted": 0, "superseded": 0},
                    "ageBuckets": {
                        "0-7d": 0,
                        "8-30d": 0,
                        "31-90d": 0,
                        "90d+": 0,
                    },
                    "sampleRecordIds": [],
                },
            )
            bucket["recordCount"] += 1
            bucket["byStatus"][status] += 1
            bucket["ageBuckets"][age_bucket] += 1
            if len(bucket["sampleRecordIds"]) < STALE_SAMPLE_LIMIT:
                bucket["sampleRecordIds"].append(record_id)
            total_records += 1
            total_by_status[status] += 1
        bucket_list = [
            {"groupKey": key, "label": key, **buckets[key]}
            for key in sorted(buckets)
        ]
        page = bucket_list[offset : offset + limit]
        truncated: dict[str, Any] | None = None
        if offset + len(page) < len(bucket_list):
            truncated = {
                "reason": "limit",
                "limit": limit,
                "count": len(page),
            }
        return {
            "buckets": page,
            "totals": {"recordCount": total_records, "byStatus": total_by_status},
            "truncated": truncated,
            "meta": {
                "queryMs": round((time.perf_counter() - started) * 1000, 1),
                "truncated": truncated,
            },
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
    # V2 visualization routes (frozen V2 plan section 4)
    "/api/graph": frozenset({"root", "depth", "direction", "limit", "stress"}),
    "/api/impact": frozenset({"limit", "offset", "kind"}),
    "/api/coverage": frozenset({"limit", "offset", "pathPrefix"}),
    "/api/timeline": frozenset(
        {"limit", "offset", "recordType", "status", "includeStatusEvents"}
    ),
    "/api/stale": frozenset({"groupBy", "limit", "offset"}),
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
            elif path == "/api/graph":
                self._handle_graph(query)
            elif path == "/api/coverage":
                self._handle_coverage(query)
            elif path == "/api/timeline":
                self._handle_timeline(query)
            elif path == "/api/stale":
                self._handle_stale(query)
            elif path.startswith("/api/impact/"):
                asset_path = path.removeprefix("/api/impact/")
                if not asset_path or "/" in asset_path:
                    raise ValueError("asset path must be a single path segment.")
                self._handle_impact(asset_path, query)
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

    # -- V2 route bodies ------------------------------------------------

    def _handle_graph(self, query: dict[str, list[str]]) -> None:
        payload = self.service.graph(
            root=_single_query_value(query, "root"),
            depth=int(_single_query_value(query, "depth", "1")),
            direction=_single_query_value(query, "direction", "outgoing"),
            limit=int(
                _single_query_value(query, "limit", str(GRAPH_DEFAULT_LIMIT))
            ),
            stress=int(_single_query_value(query, "stress", "0")),
        )
        _send_json(self, 200, payload)

    def _handle_impact(
        self, asset_path: str, query: dict[str, list[str]]
    ) -> None:
        unknown = sorted(set(query) - {"limit", "offset", "kind"})
        if unknown:
            raise ValueError(
                f"unknown parameter(s): {', '.join(unknown)} for /api/impact."
            )
        payload = self.service.impact(
            asset_path=unquote(asset_path),
            limit=int(_single_query_value(query, "limit", _DEFAULT_LIMIT)),
            offset=int(_single_query_value(query, "offset", "0")),
            kind=_single_query_value(query, "kind"),
        )
        _send_json(self, 200, payload)

    def _handle_coverage(self, query: dict[str, list[str]]) -> None:
        payload = self.service.coverage(
            limit=int(_single_query_value(query, "limit", _DEFAULT_LIMIT)),
            offset=int(_single_query_value(query, "offset", "0")),
            path_prefix=_single_query_value(query, "pathPrefix"),
        )
        _send_json(self, 200, payload)

    def _handle_timeline(self, query: dict[str, list[str]]) -> None:
        payload = self.service.timeline(
            limit=int(_single_query_value(query, "limit", _DEFAULT_LIMIT)),
            offset=int(_single_query_value(query, "offset", "0")),
            record_type=_single_query_value(query, "recordType"),
            status=_single_query_value(query, "status"),
            include_status_events=_single_query_value(
                query, "includeStatusEvents", "false"
            ).lower()
            in ("1", "true", "yes"),
        )
        _send_json(self, 200, payload)

    def _handle_stale(self, query: dict[str, list[str]]) -> None:
        payload = self.service.stale(
            group_by=_single_query_value(query, "groupBy", "nodePath"),
            limit=int(_single_query_value(query, "limit", _DEFAULT_LIMIT)),
            offset=int(_single_query_value(query, "offset", "0")),
        )
        _send_json(self, 200, payload)

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

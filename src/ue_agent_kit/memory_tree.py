from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .database import utc_now_iso


_NODE_ID_PATTERN = re.compile(r"^kn_[0-9a-f]{32}$")


class KnowledgeNodeType(StrEnum):
    PROJECT = "project"
    SYSTEM = "system"
    FEATURE = "feature"
    COMPONENT = "component"
    ENTITY = "entity"
    IMPLEMENTATION = "implementation"


@dataclass(frozen=True)
class KnowledgeNodeDraft:
    project_key: str
    path: str
    node_type: KnowledgeNodeType | str
    title: str
    summary: str
    parent_node_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    node_id: str = ""


@dataclass(frozen=True)
class KnowledgeNode:
    node_id: str
    project_key: str
    path: str
    parent_node_id: str
    node_type: KnowledgeNodeType
    title: str
    summary: str
    created_at_utc: str
    updated_at_utc: str
    details: dict[str, Any]


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _optional_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    return value.strip()


def _normalize_details(value: Any, field_name: str = "details") -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be one JSON object.")
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be strict JSON-serializable.") from exc
    return dict(value)


def _details_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _read_details(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise RuntimeError("Stored Knowledge Node details must contain one JSON object.")
    return decoded


def normalize_knowledge_path(value: str) -> str:
    raw = _require_text(value, "path")
    if "\\" in raw:
        raise ValueError("path must use forward slashes.")
    if not raw.startswith("/"):
        raise ValueError("path must be an absolute knowledge path starting with /.")
    if raw != "/" and raw.endswith("/"):
        raw = raw.rstrip("/")
    if "//" in raw:
        raise ValueError("path must not contain empty segments.")
    segments = raw.split("/")[1:]
    if not segments:
        raise ValueError("path must be /project or a descendant of /project.")
    normalized_segments: list[str] = []
    for segment in segments:
        if segment in {"", ".", ".."}:
            raise ValueError("path must not contain empty, . or .. segments.")
        if any(not (character.isalnum() or character in "._-") for character in segment):
            raise ValueError("path segments may contain only letters, numbers, dot, underscore, or hyphen.")
        normalized_segments.append(segment.casefold())
    normalized = "/" + "/".join(normalized_segments)
    if normalized != "/project" and not normalized.startswith("/project/"):
        raise ValueError("path must be /project or a descendant of /project.")
    return normalized


def parent_knowledge_path(path: str) -> str:
    normalized = normalize_knowledge_path(path)
    if normalized == "/project":
        return ""
    return normalized.rsplit("/", 1)[0]


def _normalize_node_id(value: str) -> str:
    node_id = value or "kn_" + uuid.uuid4().hex
    if not _NODE_ID_PATTERN.fullmatch(node_id):
        raise ValueError("node_id must match kn_<32 lowercase hex characters>.")
    return node_id


def _node_type(value: KnowledgeNodeType | str) -> KnowledgeNodeType:
    try:
        return KnowledgeNodeType(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in KnowledgeNodeType)
        raise ValueError(f"node_type must be one of: {allowed}.") from exc


def _row_to_node(row: sqlite3.Row) -> KnowledgeNode:
    return KnowledgeNode(
        node_id=str(row["node_id"]),
        project_key=str(row["project_key"]),
        path=str(row["path"]),
        parent_node_id=str(row["parent_node_id"] or ""),
        node_type=KnowledgeNodeType(str(row["node_type"])),
        title=str(row["title"]),
        summary=str(row["summary"]),
        created_at_utc=str(row["created_at_utc"]),
        updated_at_utc=str(row["updated_at_utc"]),
        details=_read_details(str(row["details_json"])),
    )


def get_knowledge_node(
    connection: sqlite3.Connection,
    *,
    node_id: str,
    project_key: str = "",
) -> KnowledgeNode:
    normalized_id = _require_text(node_id, "node_id")
    parameters: list[Any] = [normalized_id]
    clause = "node_id = ?"
    if project_key:
        clause += " AND project_key = ?"
        parameters.append(_require_text(project_key, "project_key"))
    row = connection.execute(
        "SELECT * FROM knowledge_nodes WHERE " + clause,
        parameters,
    ).fetchone()
    if row is None:
        raise KeyError(f"Knowledge node not found: {normalized_id}")
    return _row_to_node(row)


def get_knowledge_node_by_path(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    path: str,
) -> KnowledgeNode:
    normalized_project = _require_text(project_key, "project_key")
    normalized_path = normalize_knowledge_path(path)
    row = connection.execute(
        "SELECT * FROM knowledge_nodes WHERE project_key = ? AND path = ?",
        (normalized_project, normalized_path),
    ).fetchone()
    if row is None:
        raise KeyError(f"Knowledge node not found: {normalized_path}")
    return _row_to_node(row)


def create_knowledge_node(
    connection: sqlite3.Connection,
    draft: KnowledgeNodeDraft,
) -> KnowledgeNode:
    if not isinstance(draft, KnowledgeNodeDraft):
        raise TypeError("draft must be a KnowledgeNodeDraft.")
    node_id = _normalize_node_id(draft.node_id)
    project_key = _require_text(draft.project_key, "project_key")
    path = normalize_knowledge_path(draft.path)
    node_type = _node_type(draft.node_type)
    title = _require_text(draft.title, "title")
    summary = _require_text(draft.summary, "summary")
    details = _normalize_details(draft.details)

    parent_node_id: str | None = None
    if path == "/project":
        if draft.parent_node_id:
            raise ValueError("The /project root node cannot have a parent.")
        if node_type != KnowledgeNodeType.PROJECT:
            raise ValueError("The /project root node must use node_type project.")
    else:
        expected_parent_path = parent_knowledge_path(path)
        if draft.parent_node_id:
            parent = get_knowledge_node(
                connection,
                node_id=draft.parent_node_id,
                project_key=project_key,
            )
            if parent.path != expected_parent_path:
                raise ValueError("parent_node_id must match the parent segment of path.")
        else:
            parent = get_knowledge_node_by_path(
                connection,
                project_key=project_key,
                path=expected_parent_path,
            )
        parent_node_id = parent.node_id

    timestamp = utc_now_iso()
    try:
        with connection:
            connection.execute(
                """
                INSERT INTO knowledge_nodes(
                    node_id,
                    project_key,
                    path,
                    parent_node_id,
                    node_type,
                    title,
                    summary,
                    created_at_utc,
                    updated_at_utc,
                    details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    node_id,
                    project_key,
                    path,
                    parent_node_id,
                    node_type.value,
                    title,
                    summary,
                    timestamp,
                    timestamp,
                    _details_json(details),
                ),
            )
    except sqlite3.IntegrityError as exc:
        if "knowledge_nodes.project_key, knowledge_nodes.path" in str(exc):
            raise ValueError(f"Knowledge node path already exists: {path}") from exc
        raise
    return get_knowledge_node(connection, node_id=node_id, project_key=project_key)


def _descendant_rows(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    node_id: str,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        WITH RECURSIVE descendants(node_id, path, depth) AS (
            SELECT node_id, path, 0
            FROM knowledge_nodes
            WHERE project_key = ? AND node_id = ?
            UNION ALL
            SELECT child.node_id, child.path, descendants.depth + 1
            FROM knowledge_nodes AS child
            JOIN descendants ON child.parent_node_id = descendants.node_id
            WHERE child.project_key = ?
        )
        SELECT node_id, path, depth
        FROM descendants
        ORDER BY depth, path
        """,
        (project_key, node_id, project_key),
    ).fetchall()


def update_knowledge_node(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    node_id: str,
    path: str | None = None,
    parent_node_id: str | None = None,
    node_type: KnowledgeNodeType | str | None = None,
    title: str | None = None,
    summary: str | None = None,
    details: dict[str, Any] | None = None,
) -> KnowledgeNode:
    normalized_project = _require_text(project_key, "project_key")
    current = get_knowledge_node(connection, node_id=node_id, project_key=normalized_project)
    desired_path = current.path if path is None else normalize_knowledge_path(path)
    desired_type = current.node_type if node_type is None else _node_type(node_type)
    desired_title = current.title if title is None else _require_text(title, "title")
    desired_summary = current.summary if summary is None else _require_text(summary, "summary")
    desired_details = current.details if details is None else _normalize_details(details)

    if current.path == "/project" and desired_path != "/project":
        raise ValueError("The /project root node cannot be moved.")
    if desired_path == "/project":
        if current.path != "/project":
            raise ValueError("Only the existing root node may use /project.")
        if parent_node_id:
            raise ValueError("The /project root node cannot have a parent.")
        if desired_type != KnowledgeNodeType.PROJECT:
            raise ValueError("The /project root node must use node_type project.")
        desired_parent_id: str | None = None
    else:
        expected_parent_path = parent_knowledge_path(desired_path)
        if parent_node_id is not None:
            desired_parent = get_knowledge_node(
                connection,
                node_id=_require_text(parent_node_id, "parent_node_id"),
                project_key=normalized_project,
            )
        elif current.parent_node_id and desired_path == current.path:
            desired_parent = get_knowledge_node(
                connection,
                node_id=current.parent_node_id,
                project_key=normalized_project,
            )
        else:
            desired_parent = get_knowledge_node_by_path(
                connection,
                project_key=normalized_project,
                path=expected_parent_path,
            )
        if desired_parent.path != expected_parent_path:
            raise ValueError("parent_node_id must match the parent segment of path.")
        desired_parent_id = desired_parent.node_id

    descendants = _descendant_rows(
        connection,
        project_key=normalized_project,
        node_id=current.node_id,
    )
    descendant_ids = {str(row["node_id"]) for row in descendants}
    if desired_parent_id in descendant_ids:
        raise ValueError("Knowledge node parent would create a cycle.")

    path_changes: list[tuple[str, str]] = []
    if desired_path != current.path:
        for row in descendants:
            old_path = str(row["path"])
            suffix = old_path[len(current.path) :]
            path_changes.append((str(row["node_id"]), desired_path + suffix))
        new_paths = {new_path for _, new_path in path_changes}
        placeholders = ",".join("?" for _ in descendant_ids)
        parameters: list[Any] = [normalized_project, *new_paths]
        query = (
            "SELECT path FROM knowledge_nodes WHERE project_key = ? AND path IN ("
            + ",".join("?" for _ in new_paths)
            + ")"
        )
        if descendant_ids:
            query += " AND node_id NOT IN (" + placeholders + ")"
            parameters.extend(sorted(descendant_ids))
        collision = connection.execute(query, parameters).fetchone()
        if collision is not None:
            raise ValueError(f"Knowledge node path already exists: {collision['path']}")

    timestamp = utc_now_iso()
    with connection:
        if path_changes:
            for changed_node_id, new_path in sorted(
                path_changes,
                key=lambda item: item[1].count("/"),
                reverse=True,
            ):
                connection.execute(
                    "UPDATE knowledge_nodes SET path = ?, updated_at_utc = ? WHERE node_id = ?",
                    (new_path, timestamp, changed_node_id),
                )
        connection.execute(
            """
            UPDATE knowledge_nodes
            SET parent_node_id = ?,
                node_type = ?,
                title = ?,
                summary = ?,
                updated_at_utc = ?,
                details_json = ?
            WHERE node_id = ? AND project_key = ?
            """,
            (
                desired_parent_id,
                desired_type.value,
                desired_title,
                desired_summary,
                timestamp,
                _details_json(desired_details),
                current.node_id,
                normalized_project,
            ),
        )
    return get_knowledge_node(connection, node_id=current.node_id, project_key=normalized_project)


def delete_knowledge_node(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    node_id: str,
) -> None:
    normalized_project = _require_text(project_key, "project_key")
    node = get_knowledge_node(connection, node_id=node_id, project_key=normalized_project)
    if node.path == "/project":
        raise ValueError("The /project root node cannot be deleted.")
    child_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM knowledge_nodes WHERE project_key = ? AND parent_node_id = ?",
            (normalized_project, node.node_id),
        ).fetchone()[0]
    )
    if child_count:
        raise ValueError("Knowledge node cannot be deleted while it has child nodes.")
    record_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM memory_records WHERE project_key = ? AND node_id = ?",
            (normalized_project, node.node_id),
        ).fetchone()[0]
    )
    if record_count:
        raise ValueError("Knowledge node cannot be deleted while memory records are attached.")
    work_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM active_work_node_links AS link
            JOIN active_work_items AS work ON work.work_item_id = link.work_item_id
            WHERE work.project_key = ? AND link.node_id = ?
            """,
            (normalized_project, node.node_id),
        ).fetchone()[0]
    )
    if work_count:
        raise ValueError("Knowledge node cannot be deleted while Active Work is attached.")
    with connection:
        connection.execute(
            "DELETE FROM knowledge_nodes WHERE node_id = ? AND project_key = ?",
            (node.node_id, normalized_project),
        )


def list_knowledge_nodes(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    parent_node_id: str | None = None,
    limit: int = 100,
) -> tuple[KnowledgeNode, ...]:
    normalized_project = _require_text(project_key, "project_key")
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500.")
    if parent_node_id is None:
        rows = connection.execute(
            "SELECT * FROM knowledge_nodes WHERE project_key = ? ORDER BY path LIMIT ?",
            (normalized_project, limit),
        ).fetchall()
    else:
        normalized_parent = _require_text(parent_node_id, "parent_node_id")
        rows = connection.execute(
            """
            SELECT *
            FROM knowledge_nodes
            WHERE project_key = ? AND parent_node_id = ?
            ORDER BY path
            LIMIT ?
            """,
            (normalized_project, normalized_parent, limit),
        ).fetchall()
    return tuple(_row_to_node(row) for row in rows)


def expand_knowledge_tree(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    path: str,
    max_depth: int = 1,
    max_nodes: int = 50,
) -> tuple[tuple[KnowledgeNode, int], ...]:
    normalized_project = _require_text(project_key, "project_key")
    normalized_path = normalize_knowledge_path(path)
    if max_depth < 0 or max_depth > 16:
        raise ValueError("max_depth must be between 0 and 16.")
    if max_nodes < 1 or max_nodes > 500:
        raise ValueError("max_nodes must be between 1 and 500.")
    root = get_knowledge_node_by_path(
        connection,
        project_key=normalized_project,
        path=normalized_path,
    )
    rows = connection.execute(
        """
        WITH RECURSIVE tree(node_id, depth) AS (
            SELECT ?, 0
            UNION ALL
            SELECT child.node_id, tree.depth + 1
            FROM knowledge_nodes AS child
            JOIN tree ON child.parent_node_id = tree.node_id
            WHERE child.project_key = ? AND tree.depth < ?
        )
        SELECT node.*, tree.depth
        FROM tree
        JOIN knowledge_nodes AS node ON node.node_id = tree.node_id
        ORDER BY tree.depth, node.path
        LIMIT ?
        """,
        (root.node_id, normalized_project, max_depth, max_nodes),
    ).fetchall()
    return tuple((_row_to_node(row), int(row["depth"])) for row in rows)


def search_knowledge_nodes(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    query: str,
    limit: int = 20,
) -> tuple[KnowledgeNode, ...]:
    normalized_project = _require_text(project_key, "project_key")
    normalized_query = _require_text(query, "query")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100.")
    terms = [term for term in re.split(r"\s+", normalized_query) if term]
    clauses: list[str] = []
    parameters: list[Any] = [normalized_project]
    for term in terms:
        clauses.append("(path LIKE ? OR title LIKE ? OR summary LIKE ? OR details_json LIKE ?)")
        pattern = f"%{term}%"
        parameters.extend((pattern, pattern, pattern, pattern))
    parameters.append(limit)
    rows = connection.execute(
        "SELECT * FROM knowledge_nodes WHERE project_key = ? AND "
        + " AND ".join(clauses)
        + " ORDER BY CASE WHEN path = ? THEN 0 ELSE 1 END, length(path), path LIMIT ?",
        [*parameters[:-1], normalize_knowledge_path(normalized_query) if normalized_query.startswith("/") else "", limit],
    ).fetchall()
    return tuple(_row_to_node(row) for row in rows)


def attach_memory_record_to_node(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    record_id: str,
    node_id: str,
) -> None:
    normalized_project = _require_text(project_key, "project_key")
    normalized_record = _require_text(record_id, "record_id")
    node = get_knowledge_node(connection, node_id=node_id, project_key=normalized_project)
    row = connection.execute(
        "SELECT project_key FROM memory_records WHERE record_id = ?",
        (normalized_record,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Project Memory record not found: {normalized_record}")
    if str(row[0]) != normalized_project:
        raise ValueError("Knowledge node and memory record must use the same project_key.")
    with connection:
        connection.execute(
            "UPDATE memory_records SET node_id = ?, updated_at_utc = ? WHERE record_id = ?",
            (node.node_id, utc_now_iso(), normalized_record),
        )


def detach_memory_record_from_node(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    record_id: str,
) -> None:
    normalized_project = _require_text(project_key, "project_key")
    normalized_record = _require_text(record_id, "record_id")
    row = connection.execute(
        "SELECT project_key FROM memory_records WHERE record_id = ?",
        (normalized_record,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Project Memory record not found: {normalized_record}")
    if str(row[0]) != normalized_project:
        raise ValueError("The memory record belongs to another project.")
    with connection:
        connection.execute(
            "UPDATE memory_records SET node_id = NULL, updated_at_utc = ? WHERE record_id = ?",
            (utc_now_iso(), normalized_record),
        )

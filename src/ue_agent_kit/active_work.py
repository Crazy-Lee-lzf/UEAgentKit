from __future__ import annotations

import json
import re
import sqlite3
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Sequence

from .database import utc_now_iso
from .memory_tree import get_knowledge_node


_WORK_ID_PATTERN = re.compile(r"^work_[0-9a-f]{32}$")
_TODO_ID_PATTERN = re.compile(r"^todo_[0-9a-f]{32}$")


class WorkStatus(StrEnum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class WorkItemDraft:
    project_key: str
    title: str
    description: str
    next_action: str
    priority: int = 50
    owner: str = ""
    status: WorkStatus | str = WorkStatus.IN_PROGRESS
    node_ids: Sequence[str] = ()
    asset_paths: Sequence[str] = ()
    details: dict[str, Any] = field(default_factory=dict)
    work_item_id: str = ""


@dataclass(frozen=True)
class WorkTodo:
    todo_id: str
    text: str
    created_at_utc: str
    completed_at_utc: str


@dataclass(frozen=True)
class WorkItem:
    work_item_id: str
    project_key: str
    title: str
    status: WorkStatus
    priority: int
    description: str
    next_action: str
    blocked_reason: str
    owner: str
    created_at_utc: str
    updated_at_utc: str
    completed_at_utc: str
    node_ids: tuple[str, ...]
    asset_paths: tuple[str, ...]
    todos: tuple[WorkTodo, ...]
    details: dict[str, Any]


_ALLOWED_TRANSITIONS = {
    WorkStatus.PLANNED: {WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED},
    WorkStatus.IN_PROGRESS: {WorkStatus.BLOCKED, WorkStatus.DONE, WorkStatus.CANCELLED},
    WorkStatus.BLOCKED: {WorkStatus.IN_PROGRESS, WorkStatus.CANCELLED},
    WorkStatus.DONE: set(),
    WorkStatus.CANCELLED: set(),
}


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
        raise RuntimeError("Stored Active Work details must contain one JSON object.")
    return decoded


def _normalize_work_id(value: str) -> str:
    work_item_id = value or "work_" + uuid.uuid4().hex
    if not _WORK_ID_PATTERN.fullmatch(work_item_id):
        raise ValueError("work_item_id must match work_<32 lowercase hex characters>.")
    return work_item_id


def _normalize_todo_id(value: str = "") -> str:
    todo_id = value or "todo_" + uuid.uuid4().hex
    if not _TODO_ID_PATTERN.fullmatch(todo_id):
        raise ValueError("todo_id must match todo_<32 lowercase hex characters>.")
    return todo_id


def _work_status(value: WorkStatus | str) -> WorkStatus:
    try:
        return WorkStatus(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in WorkStatus)
        raise ValueError(f"status must be one of: {allowed}.") from exc


def _normalize_priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("priority must be an integer between 0 and 100.")
    if value < 0 or value > 100:
        raise ValueError("priority must be an integer between 0 and 100.")
    return value


def _normalize_node_ids(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    node_ids: Sequence[str],
) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for index, node_id in enumerate(node_ids):
        value = _require_text(node_id, f"node_ids[{index}]")
        if value in seen:
            raise ValueError("node_ids must not contain duplicates.")
        get_knowledge_node(connection, node_id=value, project_key=project_key)
        seen.add(value)
        normalized.append(value)
    return tuple(normalized)


def _normalize_asset_paths(asset_paths: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for index, asset_path in enumerate(asset_paths):
        value = _require_text(asset_path, f"asset_paths[{index}]")
        if not value.startswith("/Game/"):
            raise ValueError(f"asset_paths[{index}] must start with /Game/.")
        if value in seen:
            raise ValueError("asset_paths must not contain duplicates.")
        seen.add(value)
        normalized.append(value)
    return tuple(sorted(normalized, key=str.casefold))


def _load_node_ids(connection: sqlite3.Connection, work_item_id: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT node_id FROM active_work_node_links WHERE work_item_id = ? ORDER BY node_id",
        (work_item_id,),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _load_asset_paths(connection: sqlite3.Connection, work_item_id: str) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT asset_path FROM active_work_asset_links WHERE work_item_id = ? ORDER BY asset_path",
        (work_item_id,),
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _load_todos(connection: sqlite3.Connection, work_item_id: str) -> tuple[WorkTodo, ...]:
    rows = connection.execute(
        """
        SELECT todo_id, text, created_at_utc, completed_at_utc
        FROM active_work_todos
        WHERE work_item_id = ?
        ORDER BY created_at_utc, todo_id
        """,
        (work_item_id,),
    ).fetchall()
    return tuple(
        WorkTodo(
            todo_id=str(row[0]),
            text=str(row[1]),
            created_at_utc=str(row[2]),
            completed_at_utc=str(row[3]),
        )
        for row in rows
    )


def _row_to_work_item(connection: sqlite3.Connection, row: sqlite3.Row) -> WorkItem:
    work_item_id = str(row["work_item_id"])
    return WorkItem(
        work_item_id=work_item_id,
        project_key=str(row["project_key"]),
        title=str(row["title"]),
        status=WorkStatus(str(row["status"])),
        priority=int(row["priority"]),
        description=str(row["description"]),
        next_action=str(row["next_action"]),
        blocked_reason=str(row["blocked_reason"]),
        owner=str(row["owner"]),
        created_at_utc=str(row["created_at_utc"]),
        updated_at_utc=str(row["updated_at_utc"]),
        completed_at_utc=str(row["completed_at_utc"]),
        node_ids=_load_node_ids(connection, work_item_id),
        asset_paths=_load_asset_paths(connection, work_item_id),
        todos=_load_todos(connection, work_item_id),
        details=_read_details(str(row["details_json"])),
    )


def get_work_item(
    connection: sqlite3.Connection,
    *,
    work_item_id: str,
    project_key: str = "",
) -> WorkItem:
    normalized_id = _require_text(work_item_id, "work_item_id")
    parameters: list[Any] = [normalized_id]
    clause = "work_item_id = ?"
    if project_key:
        clause += " AND project_key = ?"
        parameters.append(_require_text(project_key, "project_key"))
    row = connection.execute(
        "SELECT * FROM active_work_items WHERE " + clause,
        parameters,
    ).fetchone()
    if row is None:
        raise KeyError(f"Active Work item not found: {normalized_id}")
    return _row_to_work_item(connection, row)


def create_work_item(connection: sqlite3.Connection, draft: WorkItemDraft) -> WorkItem:
    if not isinstance(draft, WorkItemDraft):
        raise TypeError("draft must be a WorkItemDraft.")
    work_item_id = _normalize_work_id(draft.work_item_id)
    project_key = _require_text(draft.project_key, "project_key")
    title = _require_text(draft.title, "title")
    description = _require_text(draft.description, "description")
    next_action = _require_text(draft.next_action, "next_action")
    priority = _normalize_priority(draft.priority)
    owner = _optional_text(draft.owner, "owner")
    status = _work_status(draft.status)
    if status not in {WorkStatus.PLANNED, WorkStatus.IN_PROGRESS}:
        raise ValueError("New Active Work must start as planned or in_progress.")
    node_ids = _normalize_node_ids(
        connection,
        project_key=project_key,
        node_ids=draft.node_ids,
    )
    asset_paths = _normalize_asset_paths(draft.asset_paths)
    details = _normalize_details(draft.details)
    timestamp = utc_now_iso()
    with connection:
        connection.execute(
            """
            INSERT INTO active_work_items(
                work_item_id,
                project_key,
                title,
                status,
                priority,
                description,
                next_action,
                blocked_reason,
                owner,
                created_at_utc,
                updated_at_utc,
                completed_at_utc,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, '', ?)
            """,
            (
                work_item_id,
                project_key,
                title,
                status.value,
                priority,
                description,
                next_action,
                owner,
                timestamp,
                timestamp,
                _details_json(details),
            ),
        )
        for node_id in node_ids:
            connection.execute(
                "INSERT INTO active_work_node_links(work_item_id, node_id) VALUES (?, ?)",
                (work_item_id, node_id),
            )
        for asset_path in asset_paths:
            connection.execute(
                "INSERT INTO active_work_asset_links(work_item_id, asset_path) VALUES (?, ?)",
                (work_item_id, asset_path),
            )
    return get_work_item(connection, work_item_id=work_item_id, project_key=project_key)


def list_work_items(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    statuses: Sequence[WorkStatus | str] = (
        WorkStatus.PLANNED,
        WorkStatus.IN_PROGRESS,
        WorkStatus.BLOCKED,
    ),
    node_ids: Sequence[str] = (),
    asset_paths: Sequence[str] = (),
    query: str = "",
    limit: int = 50,
) -> tuple[WorkItem, ...]:
    normalized_project = _require_text(project_key, "project_key")
    if limit < 1 or limit > 200:
        raise ValueError("limit must be between 1 and 200.")
    clauses = ["work.project_key = ?"]
    parameters: list[Any] = [normalized_project]
    if statuses:
        normalized_statuses = [_work_status(status).value for status in statuses]
        clauses.append("work.status IN (" + ",".join("?" for _ in normalized_statuses) + ")")
        parameters.extend(normalized_statuses)
    if node_ids:
        normalized_nodes = _normalize_node_ids(
            connection,
            project_key=normalized_project,
            node_ids=node_ids,
        )
        clauses.append(
            "EXISTS (SELECT 1 FROM active_work_node_links AS node_link "
            "WHERE node_link.work_item_id = work.work_item_id AND node_link.node_id IN ("
            + ",".join("?" for _ in normalized_nodes)
            + "))"
        )
        parameters.extend(normalized_nodes)
    if asset_paths:
        normalized_assets = _normalize_asset_paths(asset_paths)
        clauses.append(
            "EXISTS (SELECT 1 FROM active_work_asset_links AS asset_link "
            "WHERE asset_link.work_item_id = work.work_item_id AND asset_link.asset_path IN ("
            + ",".join("?" for _ in normalized_assets)
            + "))"
        )
        parameters.extend(normalized_assets)
    if query:
        normalized_query = _require_text(query, "query")
        pattern = f"%{normalized_query}%"
        clauses.append(
            "(work.title LIKE ? OR work.description LIKE ? OR work.next_action LIKE ? "
            "OR work.blocked_reason LIKE ? OR work.details_json LIKE ?)"
        )
        parameters.extend((pattern, pattern, pattern, pattern, pattern))
    parameters.append(limit)
    rows = connection.execute(
        "SELECT work.* FROM active_work_items AS work WHERE "
        + " AND ".join(clauses)
        + " ORDER BY work.priority DESC, work.updated_at_utc DESC, work.work_item_id LIMIT ?",
        parameters,
    ).fetchall()
    return tuple(_row_to_work_item(connection, row) for row in rows)


def _transition_work_item(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
    to_status: WorkStatus,
    blocked_reason: str = "",
    next_action: str | None = None,
) -> WorkItem:
    current = get_work_item(
        connection,
        work_item_id=work_item_id,
        project_key=project_key,
    )
    if to_status not in _ALLOWED_TRANSITIONS[current.status]:
        raise ValueError(
            f"Active Work status cannot transition from {current.status.value} to {to_status.value}."
        )
    normalized_reason = _optional_text(blocked_reason, "blocked_reason")
    if to_status == WorkStatus.BLOCKED and not normalized_reason:
        raise ValueError("blocked_reason is required when blocking Active Work.")
    normalized_next_action = (
        current.next_action if next_action is None else _require_text(next_action, "next_action")
    )
    timestamp = utc_now_iso()
    completed_at = timestamp if to_status in {WorkStatus.DONE, WorkStatus.CANCELLED} else ""
    with connection:
        connection.execute(
            """
            UPDATE active_work_items
            SET status = ?,
                next_action = ?,
                blocked_reason = ?,
                updated_at_utc = ?,
                completed_at_utc = ?
            WHERE work_item_id = ? AND project_key = ?
            """,
            (
                to_status.value,
                normalized_next_action,
                normalized_reason if to_status == WorkStatus.BLOCKED else "",
                timestamp,
                completed_at,
                current.work_item_id,
                current.project_key,
            ),
        )
    return get_work_item(
        connection,
        work_item_id=current.work_item_id,
        project_key=current.project_key,
    )


def start_work_item(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
) -> WorkItem:
    return _transition_work_item(
        connection,
        project_key=project_key,
        work_item_id=work_item_id,
        to_status=WorkStatus.IN_PROGRESS,
    )


def add_work_todo(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
    text: str,
    todo_id: str = "",
) -> WorkItem:
    current = get_work_item(
        connection,
        work_item_id=work_item_id,
        project_key=project_key,
    )
    if current.status in {WorkStatus.DONE, WorkStatus.CANCELLED}:
        raise ValueError("Cannot add a TODO to completed or cancelled Active Work.")
    normalized_text = _require_text(text, "text")
    normalized_todo_id = _normalize_todo_id(todo_id)
    timestamp = utc_now_iso()
    with connection:
        connection.execute(
            """
            INSERT INTO active_work_todos(todo_id, work_item_id, text, created_at_utc, completed_at_utc)
            VALUES (?, ?, ?, ?, '')
            """,
            (normalized_todo_id, current.work_item_id, normalized_text, timestamp),
        )
        connection.execute(
            "UPDATE active_work_items SET updated_at_utc = ? WHERE work_item_id = ?",
            (timestamp, current.work_item_id),
        )
    return get_work_item(
        connection,
        work_item_id=current.work_item_id,
        project_key=current.project_key,
    )


def set_work_next_action(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
    next_action: str,
) -> WorkItem:
    current = get_work_item(
        connection,
        work_item_id=work_item_id,
        project_key=project_key,
    )
    if current.status in {WorkStatus.DONE, WorkStatus.CANCELLED}:
        raise ValueError("Cannot change next_action for completed or cancelled Active Work.")
    normalized_next_action = _require_text(next_action, "next_action")
    with connection:
        connection.execute(
            "UPDATE active_work_items SET next_action = ?, updated_at_utc = ? WHERE work_item_id = ?",
            (normalized_next_action, utc_now_iso(), current.work_item_id),
        )
    return get_work_item(
        connection,
        work_item_id=current.work_item_id,
        project_key=current.project_key,
    )


def block_work_item(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
    blocked_reason: str,
    next_action: str | None = None,
) -> WorkItem:
    return _transition_work_item(
        connection,
        project_key=project_key,
        work_item_id=work_item_id,
        to_status=WorkStatus.BLOCKED,
        blocked_reason=blocked_reason,
        next_action=next_action,
    )


def resume_work_item(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
    next_action: str | None = None,
) -> WorkItem:
    return _transition_work_item(
        connection,
        project_key=project_key,
        work_item_id=work_item_id,
        to_status=WorkStatus.IN_PROGRESS,
        next_action=next_action,
    )


def complete_work_item(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
) -> WorkItem:
    return _transition_work_item(
        connection,
        project_key=project_key,
        work_item_id=work_item_id,
        to_status=WorkStatus.DONE,
    )


def cancel_work_item(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
) -> WorkItem:
    return _transition_work_item(
        connection,
        project_key=project_key,
        work_item_id=work_item_id,
        to_status=WorkStatus.CANCELLED,
    )


def set_work_links(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    work_item_id: str,
    node_ids: Sequence[str],
    asset_paths: Sequence[str],
) -> WorkItem:
    current = get_work_item(
        connection,
        work_item_id=work_item_id,
        project_key=project_key,
    )
    if current.status in {WorkStatus.DONE, WorkStatus.CANCELLED}:
        raise ValueError("Cannot change links for completed or cancelled Active Work.")
    normalized_nodes = _normalize_node_ids(
        connection,
        project_key=current.project_key,
        node_ids=node_ids,
    )
    normalized_assets = _normalize_asset_paths(asset_paths)
    timestamp = utc_now_iso()
    with connection:
        connection.execute(
            "DELETE FROM active_work_node_links WHERE work_item_id = ?",
            (current.work_item_id,),
        )
        connection.execute(
            "DELETE FROM active_work_asset_links WHERE work_item_id = ?",
            (current.work_item_id,),
        )
        for node_id in normalized_nodes:
            connection.execute(
                "INSERT INTO active_work_node_links(work_item_id, node_id) VALUES (?, ?)",
                (current.work_item_id, node_id),
            )
        for asset_path in normalized_assets:
            connection.execute(
                "INSERT INTO active_work_asset_links(work_item_id, asset_path) VALUES (?, ?)",
                (current.work_item_id, asset_path),
            )
        connection.execute(
            "UPDATE active_work_items SET updated_at_utc = ? WHERE work_item_id = ?",
            (timestamp, current.work_item_id),
        )
    return get_work_item(
        connection,
        work_item_id=current.work_item_id,
        project_key=current.project_key,
    )

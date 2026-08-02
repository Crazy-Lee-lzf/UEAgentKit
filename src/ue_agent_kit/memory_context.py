from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from .active_work import WorkItem, WorkStatus, list_work_items
from .memory_tree import (
    KnowledgeNode,
    expand_knowledge_tree,
    get_knowledge_node,
    get_knowledge_node_by_path,
    search_knowledge_nodes,
)
from .project_memory import (
    MemoryRecord,
    MemoryScopeType,
    MemoryStatus,
    get_memory_record,
    search_memory_records,
)


MIN_CONTEXT_CHARS = 512
MAX_CONTEXT_CHARS = 100_000
MAX_CONTEXT_NODES = 100
MAX_CONTEXT_RECORDS = 200
MAX_CONTEXT_DEPTH = 16
DEFAULT_CONTEXT_STATUSES = (
    MemoryStatus.VALID,
    MemoryStatus.UNVERIFIED,
    MemoryStatus.CONFLICTED,
)
ACTIVE_WORK_STATUSES = (
    WorkStatus.PLANNED,
    WorkStatus.IN_PROGRESS,
    WorkStatus.BLOCKED,
)


@dataclass(frozen=True)
class ContextBudget:
    max_chars: int = 8_000
    max_nodes: int = 12
    max_records: int = 20
    max_depth: int = 2

    def validated(self) -> ContextBudget:
        if isinstance(self.max_chars, bool) or not isinstance(self.max_chars, int):
            raise ValueError("max_chars must be an integer.")
        if self.max_chars < MIN_CONTEXT_CHARS or self.max_chars > MAX_CONTEXT_CHARS:
            raise ValueError(
                f"max_chars must be between {MIN_CONTEXT_CHARS} and {MAX_CONTEXT_CHARS}."
            )
        if isinstance(self.max_nodes, bool) or not isinstance(self.max_nodes, int):
            raise ValueError("max_nodes must be an integer.")
        if self.max_nodes < 1 or self.max_nodes > MAX_CONTEXT_NODES:
            raise ValueError(f"max_nodes must be between 1 and {MAX_CONTEXT_NODES}.")
        if isinstance(self.max_records, bool) or not isinstance(self.max_records, int):
            raise ValueError("max_records must be an integer.")
        if self.max_records < 0 or self.max_records > MAX_CONTEXT_RECORDS:
            raise ValueError(f"max_records must be between 0 and {MAX_CONTEXT_RECORDS}.")
        if isinstance(self.max_depth, bool) or not isinstance(self.max_depth, int):
            raise ValueError("max_depth must be an integer.")
        if self.max_depth < 0 or self.max_depth > MAX_CONTEXT_DEPTH:
            raise ValueError(f"max_depth must be between 0 and {MAX_CONTEXT_DEPTH}.")
        return self


def validate_detail_level(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("detail_level must be an integer between 0 and 4.")
    if value < 0 or value > 4:
        raise ValueError("detail_level must be an integer between 0 and 4.")
    return value


def estimate_tokens(character_count: int) -> int:
    if character_count <= 0:
        return 0
    return math.ceil(character_count / 4)


def _serialized_chars(value: Any) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _summary_line(value: str, limit: int = 160) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 1)].rstrip() + "…"


def _record_payload(record: MemoryRecord, detail_level: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "recordId": record.record_id,
        "recordType": record.record_type.value,
        "subjectKey": record.subject_key,
        "title": record.title,
        "status": record.status.value,
        "nodeId": record.node_id,
        "summary": _summary_line(record.body),
    }
    if detail_level >= 3:
        payload.update(
            {
                "body": record.body,
                "sourceKind": record.source_kind.value,
                "confidence": record.confidence,
                "contentSha256": record.content_sha256,
                "createdAtUtc": record.created_at_utc,
                "observedAtUtc": record.observed_at_utc,
                "updatedAtUtc": record.updated_at_utc,
                "supersededByRecordId": record.superseded_by_record_id,
                "scopes": [
                    {
                        "scopeType": MemoryScopeType(scope.scope_type).value,
                        "scopeKey": scope.scope_key,
                        "details": scope.details,
                    }
                    for scope in record.scopes
                ],
                "relations": [
                    {
                        "relationKind": relation.relation_kind.value,
                        "targetRecordId": relation.target_record_id,
                        "createdAtUtc": relation.created_at_utc,
                        "details": relation.details,
                    }
                    for relation in record.relations
                ],
                "details": record.details,
            }
        )
    if detail_level >= 4:
        payload["evidence"] = {
            "sourceKind": record.source_kind.value,
            "sourceRef": record.source_ref,
            "confidence": record.confidence,
            "evidenceSha256": record.evidence_sha256,
            "revisionSet": [
                {
                    "assetPath": revision.asset_path,
                    "revision": revision.revision,
                    "revisionStable": revision.revision_stable,
                }
                for revision in record.revision_set
            ],
            "artifacts": [
                {
                    "artifactKind": artifact.artifact_kind,
                    "artifactRef": artifact.artifact_ref,
                    "details": artifact.details,
                }
                for artifact in record.artifacts
            ],
        }
    return payload


def evidence_payload(record: MemoryRecord) -> dict[str, Any]:
    return {
        "recordId": record.record_id,
        "projectKey": record.project_key,
        "recordType": record.record_type.value,
        "title": record.title,
        "status": record.status.value,
        "nodeId": record.node_id,
        "contentSha256": record.content_sha256,
        "evidenceSha256": record.evidence_sha256,
        "source": {
            "sourceKind": record.source_kind.value,
            "sourceRef": record.source_ref,
            "confidence": record.confidence,
            "observedAtUtc": record.observed_at_utc,
        },
        "revisionSet": [
            {
                "assetPath": revision.asset_path,
                "revision": revision.revision,
                "revisionStable": revision.revision_stable,
            }
            for revision in record.revision_set
        ],
        "artifacts": [
            {
                "artifactKind": artifact.artifact_kind,
                "artifactRef": artifact.artifact_ref,
                "details": artifact.details,
            }
            for artifact in record.artifacts
        ],
    }


def _work_payload(work: WorkItem, detail_level: int) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "workItemId": work.work_item_id,
        "title": work.title,
        "status": work.status.value,
        "priority": work.priority,
        "nextAction": work.next_action,
    }
    if detail_level >= 1:
        payload.update(
            {
                "description": work.description,
                "blockedReason": work.blocked_reason,
                "owner": work.owner,
                "updatedAtUtc": work.updated_at_utc,
            }
        )
    if detail_level >= 2:
        payload.update(
            {
                "nodeIds": list(work.node_ids),
                "assetPaths": list(work.asset_paths),
                "todos": [
                    {
                        "todoId": todo.todo_id,
                        "text": todo.text,
                        "createdAtUtc": todo.created_at_utc,
                        "completedAtUtc": todo.completed_at_utc,
                    }
                    for todo in work.todos
                ],
                "details": work.details,
            }
        )
    return payload


def _node_counts(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    node_id: str,
) -> tuple[int, tuple[str, ...]]:
    child_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM knowledge_nodes WHERE project_key = ? AND parent_node_id = ?",
            (project_key, node_id),
        ).fetchone()[0]
    )
    rows = connection.execute(
        """
        SELECT DISTINCT work.status
        FROM active_work_node_links AS link
        JOIN active_work_items AS work ON work.work_item_id = link.work_item_id
        WHERE work.project_key = ?
          AND link.node_id = ?
          AND work.status IN ('planned', 'in_progress', 'blocked')
        ORDER BY work.status
        """,
        (project_key, node_id),
    ).fetchall()
    return child_count, tuple(str(row[0]) for row in rows)


def _node_payload(
    connection: sqlite3.Connection,
    *,
    node: KnowledgeNode,
    depth: int,
    detail_level: int,
    record_ids: Sequence[str],
) -> dict[str, Any]:
    child_count, work_statuses = _node_counts(
        connection,
        project_key=node.project_key,
        node_id=node.node_id,
    )
    payload: dict[str, Any] = {
        "nodeId": node.node_id,
        "path": node.path,
        "parentNodeId": node.parent_node_id,
        "nodeType": node.node_type.value,
        "title": node.title,
        "summaryLine": _summary_line(node.summary),
        "depth": depth,
        "childCount": child_count,
        "hasActiveWork": bool(work_statuses),
        "activeWorkStatuses": list(work_statuses),
    }
    if detail_level >= 1:
        payload["summary"] = node.summary
    if detail_level >= 2:
        payload.update(
            {
                "implementationOverview": str(node.details.get("implementationOverview", "")),
                "recordIds": list(record_ids),
                "details": node.details,
            }
        )
    return payload


def _project_profile(connection: sqlite3.Connection, project_key: str) -> dict[str, Any]:
    root = connection.execute(
        "SELECT title, summary FROM knowledge_nodes WHERE project_key = ? AND path = '/project'",
        (project_key,),
    ).fetchone()
    node_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM knowledge_nodes WHERE project_key = ?",
            (project_key,),
        ).fetchone()[0]
    )
    record_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM memory_records WHERE project_key = ?",
            (project_key,),
        ).fetchone()[0]
    )
    active_work_count = int(
        connection.execute(
            """
            SELECT COUNT(*)
            FROM active_work_items
            WHERE project_key = ? AND status IN ('planned', 'in_progress', 'blocked')
            """,
            (project_key,),
        ).fetchone()[0]
    )
    return {
        "projectKey": project_key,
        "title": str(root[0]) if root is not None else project_key,
        "summary": _summary_line(str(root[1])) if root is not None else "",
        "rootPath": "/project",
        "nodeCount": node_count,
        "recordCount": record_count,
        "activeWorkCount": active_work_count,
    }


def _record_ids_by_node(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    node_ids: Sequence[str],
    statuses: Sequence[MemoryStatus] = DEFAULT_CONTEXT_STATUSES,
    limit: int,
) -> tuple[str, ...]:
    if not node_ids or limit <= 0:
        return ()
    normalized_statuses = [status.value for status in statuses]
    rows = connection.execute(
        "SELECT record_id FROM memory_records WHERE project_key = ? AND node_id IN ("
        + ",".join("?" for _ in node_ids)
        + ") AND status IN ("
        + ",".join("?" for _ in normalized_statuses)
        + ") ORDER BY updated_at_utc DESC, record_id LIMIT ?",
        [project_key, *node_ids, *normalized_statuses, limit],
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _record_ids_by_assets(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    asset_paths: Sequence[str],
    limit: int,
) -> tuple[str, ...]:
    if not asset_paths or limit <= 0:
        return ()
    placeholders = ",".join("?" for _ in asset_paths)
    rows = connection.execute(
        "SELECT record.record_id "
        "FROM memory_records AS record "
        "WHERE record.project_key = ? "
        "AND record.status IN ('valid', 'unverified', 'conflicted') "
        "AND ("
        "EXISTS (SELECT 1 FROM memory_scopes AS scope "
        "WHERE scope.record_id = record.record_id "
        "AND scope.scope_type = 'asset' AND scope.scope_key IN ("
        + placeholders
        + ")) OR EXISTS (SELECT 1 FROM memory_revisions AS revision "
        "WHERE revision.record_id = record.record_id AND revision.asset_path IN ("
        + placeholders
        + "))) "
        "ORDER BY record.updated_at_utc DESC, record.record_id LIMIT ?",
        [project_key, *asset_paths, *asset_paths, limit],
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _query_record_ids(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    query: str,
    limit: int,
) -> tuple[str, ...]:
    if not query or limit <= 0:
        return ()
    try:
        hits = search_memory_records(
            connection,
            project_key=project_key,
            query=query,
            statuses=DEFAULT_CONTEXT_STATUSES,
            limit=min(limit, 100),
        )
    except ValueError:
        return ()
    return tuple(hit.record.record_id for hit in hits)


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _nodes_by_assets(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    asset_paths: Sequence[str],
    limit: int,
) -> tuple[KnowledgeNode, ...]:
    if not asset_paths or limit <= 0:
        return ()
    placeholders = ",".join("?" for _ in asset_paths)
    rows = connection.execute(
        "SELECT DISTINCT matched.node_id FROM ("
        "SELECT record.node_id AS node_id FROM memory_records AS record "
        "WHERE record.project_key = ? AND record.node_id IS NOT NULL "
        "AND record.status IN ('valid', 'unverified', 'conflicted') AND ("
        "EXISTS (SELECT 1 FROM memory_scopes AS scope "
        "WHERE scope.record_id = record.record_id "
        "AND scope.scope_type = 'asset' AND scope.scope_key IN ("
        + placeholders
        + ")) OR EXISTS (SELECT 1 FROM memory_revisions AS revision "
        "WHERE revision.record_id = record.record_id AND revision.asset_path IN ("
        + placeholders
        + "))) UNION SELECT node_link.node_id AS node_id "
        "FROM active_work_node_links AS node_link "
        "JOIN active_work_items AS work ON work.work_item_id = node_link.work_item_id "
        "JOIN active_work_asset_links AS asset_link ON asset_link.work_item_id = work.work_item_id "
        "WHERE work.project_key = ? AND work.status IN ('planned', 'in_progress', 'blocked') "
        "AND asset_link.asset_path IN ("
        + placeholders
        + ")) AS matched "
        "JOIN knowledge_nodes AS node ON node.node_id = matched.node_id "
        "WHERE node.project_key = ? ORDER BY length(node.path), node.path LIMIT ?",
        [project_key, *asset_paths, *asset_paths, project_key, *asset_paths, project_key, limit],
    ).fetchall()
    return tuple(
        get_knowledge_node(connection, node_id=str(row[0]), project_key=project_key) for row in rows
    )


def _nodes_for_context(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    query: str,
    node_path: str,
    asset_paths: Sequence[str],
    max_nodes: int,
    max_depth: int,
) -> tuple[tuple[KnowledgeNode, int], ...]:
    if node_path:
        return expand_knowledge_tree(
            connection,
            project_key=project_key,
            path=node_path,
            max_depth=max_depth,
            max_nodes=max_nodes,
        )
    candidates: list[KnowledgeNode] = []
    if query:
        candidates.extend(
            search_knowledge_nodes(
                connection,
                project_key=project_key,
                query=query,
                limit=max_nodes,
            )
        )
    if asset_paths and len(candidates) < max_nodes:
        candidates.extend(
            _nodes_by_assets(
                connection,
                project_key=project_key,
                asset_paths=asset_paths,
                limit=max_nodes - len(candidates),
            )
        )
    if candidates:
        unique: dict[str, KnowledgeNode] = {}
        for node in candidates:
            unique.setdefault(node.node_id, node)
        return tuple(
            (node, node.path.count("/") - 1) for node in list(unique.values())[:max_nodes]
        )
    try:
        root = get_knowledge_node_by_path(
            connection,
            project_key=project_key,
            path="/project",
        )
    except KeyError:
        return ()
    return ((root, 0),)


def _related_work_items(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    node_ids: Sequence[str],
    asset_paths: Sequence[str],
    query: str,
    limit: int,
) -> tuple[WorkItem, ...]:
    batches: list[tuple[WorkItem, ...]] = []
    if node_ids:
        batches.append(
            list_work_items(
                connection,
                project_key=project_key,
                statuses=ACTIVE_WORK_STATUSES,
                node_ids=node_ids,
                limit=limit,
            )
        )
    if asset_paths:
        batches.append(
            list_work_items(
                connection,
                project_key=project_key,
                statuses=ACTIVE_WORK_STATUSES,
                asset_paths=asset_paths,
                limit=limit,
            )
        )
    if query:
        batches.append(
            list_work_items(
                connection,
                project_key=project_key,
                statuses=ACTIVE_WORK_STATUSES,
                query=query,
                limit=limit,
            )
        )
    if not batches:
        batches.append(
            list_work_items(
                connection,
                project_key=project_key,
                statuses=ACTIVE_WORK_STATUSES,
                limit=limit,
            )
        )
    unique: dict[str, WorkItem] = {}
    for batch in batches:
        for work in batch:
            unique.setdefault(work.work_item_id, work)
    items = list(unique.values())
    items.sort(key=lambda work: work.work_item_id)
    items.sort(key=lambda work: work.updated_at_utc, reverse=True)
    items.sort(key=lambda work: work.priority, reverse=True)
    return tuple(items[:limit])


def _append_with_budget(
    response: dict[str, Any],
    *,
    collection: str,
    item: dict[str, Any],
    max_chars: int,
) -> bool:
    response[collection].append(item)
    if _serialized_chars(response) <= max_chars:
        return True
    response[collection].pop()
    return False


def _set_usage(response: dict[str, Any]) -> int:
    response.setdefault("usage", {"usedChars": 0, "estimatedTokens": 0})
    for _ in range(8):
        used_chars = _serialized_chars(response)
        usage = {
            "usedChars": used_chars,
            "estimatedTokens": estimate_tokens(used_chars),
        }
        if response["usage"] == usage:
            return used_chars
        response["usage"] = usage
    return _serialized_chars(response)


def _add_next_action(response: dict[str, Any], action: dict[str, Any]) -> None:
    reason = action.get("reason", "")
    if any(item.get("reason") == reason for item in response["nextActions"]):
        return
    response["nextActions"].append(action)


def _finalize_context_budget(response: dict[str, Any], max_chars: int) -> dict[str, Any]:
    while _set_usage(response) > max_chars:
        response["truncated"] = True
        if response["records"]:
            removed = response["records"].pop()
            _add_next_action(
                response,
                {
                    "tool": (
                        "ue_memory_get_evidence"
                        if response["detailLevel"] >= 3
                        else "ue_memory_get"
                    ),
                    "reason": "record-budget-truncated",
                    "arguments": {"record_id": removed["recordId"]},
                },
            )
            continue
        if response["activeWork"]:
            removed = response["activeWork"].pop()
            _add_next_action(
                response,
                {
                    "tool": "ue_memory_update_work",
                    "reason": "active-work-budget-truncated",
                    "arguments": {
                        "action": "get",
                        "payload": {"workItemId": removed["workItemId"]},
                    },
                },
            )
            continue
        if response["nodes"]:
            removed = response["nodes"].pop()
            _add_next_action(
                response,
                {
                    "tool": "ue_memory_expand_node",
                    "reason": "node-budget-truncated",
                    "arguments": {
                        "path": removed["path"],
                        "detail_level": response["detailLevel"],
                    },
                },
            )
            continue
        if len(response["nextActions"]) > 1:
            response["nextActions"].pop()
            continue
        if response["projectProfile"].get("summary"):
            response["projectProfile"]["summary"] = ""
            continue
        if "tokenEstimateRule" in response["budget"]:
            response["budget"].pop("tokenEstimateRule")
            continue
        raise ValueError("max_chars is too small for the fixed context response envelope.")
    _set_usage(response)
    return response


def build_memory_context(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    query: str = "",
    node_path: str = "",
    asset_paths: Sequence[str] = (),
    detail_level: int = 1,
    budget: ContextBudget = ContextBudget(),
) -> dict[str, Any]:
    normalized_project = project_key.strip() if isinstance(project_key, str) else ""
    if not normalized_project:
        raise ValueError("project_key must be a non-empty string.")
    if not isinstance(query, str):
        raise ValueError("query must be a string.")
    if not isinstance(node_path, str):
        raise ValueError("node_path must be a string.")
    normalized_assets: list[str] = []
    seen_assets: set[str] = set()
    for index, asset_path in enumerate(asset_paths):
        if not isinstance(asset_path, str) or not asset_path.strip().startswith("/Game/"):
            raise ValueError(f"asset_paths[{index}] must start with /Game/.")
        normalized_asset = asset_path.strip()
        if normalized_asset in seen_assets:
            raise ValueError("asset_paths must not contain duplicates.")
        seen_assets.add(normalized_asset)
        normalized_assets.append(normalized_asset)
    level = validate_detail_level(detail_level)
    limits = budget.validated()

    node_pairs = _nodes_for_context(
        connection,
        project_key=normalized_project,
        query=query.strip(),
        node_path=node_path.strip(),
        asset_paths=normalized_assets,
        max_nodes=limits.max_nodes,
        max_depth=limits.max_depth,
    )
    node_ids = tuple(node.node_id for node, _ in node_pairs)
    record_ids = _ordered_unique(
        [
            *_record_ids_by_node(
                connection,
                project_key=normalized_project,
                node_ids=node_ids,
                limit=limits.max_records,
            ),
            *_record_ids_by_assets(
                connection,
                project_key=normalized_project,
                asset_paths=normalized_assets,
                limit=limits.max_records,
            ),
            *_query_record_ids(
                connection,
                project_key=normalized_project,
                query=query.strip(),
                limit=limits.max_records,
            ),
        ]
    )[: limits.max_records]
    records = [get_memory_record(connection, record_id) for record_id in record_ids]
    records_by_node: dict[str, list[str]] = {}
    for record in records:
        if record.node_id:
            records_by_node.setdefault(record.node_id, []).append(record.record_id)

    work_items = _related_work_items(
        connection,
        project_key=normalized_project,
        node_ids=node_ids,
        asset_paths=normalized_assets,
        query=query.strip(),
        limit=min(50, limits.max_nodes + limits.max_records + 5),
    )


    response: dict[str, Any] = {
        "schemaVersion": "1.0",
        "projectProfile": _project_profile(connection, normalized_project),
        "detailLevel": level,
        "budget": {
            "maxChars": limits.max_chars,
            "maxNodes": limits.max_nodes,
            "maxRecords": limits.max_records,
            "maxDepth": limits.max_depth,
            "tokenEstimateRule": "approximately 4 chars per token",
        },
        "nodes": [],
        "activeWork": [],
        "records": [],
        "truncated": False,
        "nextActions": [],
        "usage": {"usedChars": 0, "estimatedTokens": 0},
    }

    omitted_node_path = ""
    for node, depth in node_pairs:
        item = _node_payload(
            connection,
            node=node,
            depth=depth,
            detail_level=level,
            record_ids=records_by_node.get(node.node_id, ()),
        )
        if not _append_with_budget(
            response,
            collection="nodes",
            item=item,
            max_chars=limits.max_chars,
        ):
            omitted_node_path = node.path
            response["truncated"] = True
            break

    omitted_work_id = ""
    for work in work_items:
        item = _work_payload(work, level)
        if not _append_with_budget(
            response,
            collection="activeWork",
            item=item,
            max_chars=limits.max_chars,
        ):
            omitted_work_id = work.work_item_id
            response["truncated"] = True
            break

    omitted_record_id = ""
    if level >= 2:
        for record in records:
            item = _record_payload(record, level)
            if not _append_with_budget(
                response,
                collection="records",
                item=item,
                max_chars=limits.max_chars,
            ):
                omitted_record_id = record.record_id
                response["truncated"] = True
                break

    if omitted_node_path:
        response["nextActions"].append(
            {
                "tool": "ue_memory_expand_node",
                "reason": "node-budget-truncated",
                "arguments": {"path": omitted_node_path, "detail_level": level},
            }
        )
    if omitted_work_id:
        response["nextActions"].append(
            {
                "tool": "ue_memory_update_work",
                "reason": "active-work-budget-truncated",
                "arguments": {
                    "action": "get",
                    "payload": {"workItemId": omitted_work_id},
                },
            }
        )
    if omitted_record_id:
        response["nextActions"].append(
            {
                "tool": "ue_memory_get_evidence" if level >= 3 else "ue_memory_get",
                "reason": "record-budget-truncated",
                "arguments": {"record_id": omitted_record_id},
            }
        )
    if not response["truncated"] and level < 4 and records:
        response["nextActions"].append(
            {
                "tool": "ue_memory_get_evidence",
                "reason": "evidence-available-on-demand",
                "arguments": {"record_id": records[0].record_id},
            }
        )

    return _finalize_context_budget(response, limits.max_chars)


def expand_memory_node(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    path: str,
    detail_level: int = 1,
    depth: int = 1,
    budget: ContextBudget = ContextBudget(),
) -> dict[str, Any]:
    limits = budget.validated()
    if depth < 0 or depth > limits.max_depth:
        raise ValueError(f"depth must be between 0 and the budget max_depth ({limits.max_depth}).")
    adjusted = ContextBudget(
        max_chars=limits.max_chars,
        max_nodes=limits.max_nodes,
        max_records=limits.max_records,
        max_depth=depth,
    )
    return build_memory_context(
        connection,
        project_key=project_key,
        node_path=path,
        detail_level=detail_level,
        budget=adjusted,
    )

from __future__ import annotations

import hashlib
import json
from typing import Any

from .active_work import WorkItem, get_work_item
from .database import get_schema_version, utc_now_iso
from .memory_service import ProjectMemoryService
from .memory_tree import KnowledgeNode, get_knowledge_node
from .project_memory import (
    MemoryRecord,
    MemoryScopeType,
    get_memory_record,
    open_project_memory_database,
)


MEMORY_AUDIT_SCHEMA_VERSION = "1.0"
MAX_AUDIT_RECORDS = 10_000
MAX_AUDIT_STATUS_EVENTS = 100_000
MAX_AUDIT_NODES = 10_000
MAX_AUDIT_WORK_ITEMS = 10_000


def memory_record_payload(record: MemoryRecord) -> dict[str, Any]:
    return {
        "recordId": record.record_id,
        "projectKey": record.project_key,
        "recordType": record.record_type.value,
        "subjectKey": record.subject_key,
        "title": record.title,
        "body": record.body,
        "sourceKind": record.source_kind.value,
        "sourceRef": record.source_ref,
        "confidence": record.confidence,
        "status": record.status.value,
        "contentSha256": record.content_sha256,
        "evidenceSha256": record.evidence_sha256,
        "createdAtUtc": record.created_at_utc,
        "observedAtUtc": record.observed_at_utc,
        "updatedAtUtc": record.updated_at_utc,
        "supersededByRecordId": record.superseded_by_record_id,
        "nodeId": record.node_id,
        "scopes": [
            {
                "scopeType": MemoryScopeType(scope.scope_type).value,
                "scopeKey": scope.scope_key,
                "details": scope.details,
            }
            for scope in record.scopes
        ],
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


def knowledge_node_payload(node: KnowledgeNode) -> dict[str, Any]:
    return {
        "nodeId": node.node_id,
        "projectKey": node.project_key,
        "path": node.path,
        "parentNodeId": node.parent_node_id,
        "nodeType": node.node_type.value,
        "title": node.title,
        "summary": node.summary,
        "createdAtUtc": node.created_at_utc,
        "updatedAtUtc": node.updated_at_utc,
        "details": node.details,
    }


def active_work_payload(work: WorkItem) -> dict[str, Any]:
    return {
        "workItemId": work.work_item_id,
        "projectKey": work.project_key,
        "title": work.title,
        "status": work.status.value,
        "priority": work.priority,
        "description": work.description,
        "nextAction": work.next_action,
        "blockedReason": work.blocked_reason,
        "owner": work.owner,
        "createdAtUtc": work.created_at_utc,
        "updatedAtUtc": work.updated_at_utc,
        "completedAtUtc": work.completed_at_utc,
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


def _read_details(value: str) -> dict[str, Any]:
    decoded = json.loads(value)
    if not isinstance(decoded, dict):
        raise RuntimeError("Project Memory status-event details must decode to an object.")
    return decoded


def _count_map(rows: list[Any]) -> dict[str, int]:
    return {str(row[0]): int(row[1]) for row in rows}


def _snapshot_sha256(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_memory_audit_report(
    service: ProjectMemoryService,
    *,
    max_records: int = MAX_AUDIT_RECORDS,
    max_status_events: int = MAX_AUDIT_STATUS_EVENTS,
    max_nodes: int = MAX_AUDIT_NODES,
    max_work_items: int = MAX_AUDIT_WORK_ITEMS,
) -> dict[str, Any]:
    if not isinstance(service, ProjectMemoryService):
        raise TypeError("service must be a ProjectMemoryService.")
    if max_records < 1 or max_records > MAX_AUDIT_RECORDS:
        raise ValueError(f"max_records must be between 1 and {MAX_AUDIT_RECORDS}.")
    if max_status_events < 1 or max_status_events > MAX_AUDIT_STATUS_EVENTS:
        raise ValueError(
            f"max_status_events must be between 1 and {MAX_AUDIT_STATUS_EVENTS}."
        )
    if max_nodes < 1 or max_nodes > MAX_AUDIT_NODES:
        raise ValueError(f"max_nodes must be between 1 and {MAX_AUDIT_NODES}.")
    if max_work_items < 1 or max_work_items > MAX_AUDIT_WORK_ITEMS:
        raise ValueError(
            f"max_work_items must be between 1 and {MAX_AUDIT_WORK_ITEMS}."
        )

    with open_project_memory_database(service.database_path) as connection:
        record_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE project_key = ?",
                (service.project_key,),
            ).fetchone()[0]
        )
        if record_count > max_records:
            raise RuntimeError(
                f"Project Memory audit contains {record_count} records; maximum is {max_records}."
            )
        node_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM knowledge_nodes WHERE project_key = ?",
                (service.project_key,),
            ).fetchone()[0]
        )
        if node_count > max_nodes:
            raise RuntimeError(
                f"Project Memory audit contains {node_count} knowledge nodes; maximum is {max_nodes}."
            )
        active_work_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM active_work_items WHERE project_key = ?",
                (service.project_key,),
            ).fetchone()[0]
        )
        if active_work_count > max_work_items:
            raise RuntimeError(
                "Project Memory audit contains "
                f"{active_work_count} Active Work items; maximum is {max_work_items}."
            )
        status_event_count = int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM memory_status_events AS e
                JOIN memory_records AS r ON r.record_id = e.record_id
                WHERE r.project_key = ?
                """,
                (service.project_key,),
            ).fetchone()[0]
        )
        if status_event_count > max_status_events:
            raise RuntimeError(
                "Project Memory audit contains "
                f"{status_event_count} status events; maximum is {max_status_events}."
            )

        record_rows = connection.execute(
            """
            SELECT record_id
            FROM memory_records
            WHERE project_key = ?
            ORDER BY created_at_utc, record_id
            """,
            (service.project_key,),
        ).fetchall()
        records = [
            memory_record_payload(get_memory_record(connection, str(row[0])))
            for row in record_rows
        ]
        node_rows = connection.execute(
            """
            SELECT node_id
            FROM knowledge_nodes
            WHERE project_key = ?
            ORDER BY path, node_id
            """,
            (service.project_key,),
        ).fetchall()
        knowledge_nodes = [
            knowledge_node_payload(
                get_knowledge_node(
                    connection,
                    node_id=str(row[0]),
                    project_key=service.project_key,
                )
            )
            for row in node_rows
        ]
        work_rows = connection.execute(
            """
            SELECT work_item_id
            FROM active_work_items
            WHERE project_key = ?
            ORDER BY created_at_utc, work_item_id
            """,
            (service.project_key,),
        ).fetchall()
        active_work = [
            active_work_payload(
                get_work_item(
                    connection,
                    work_item_id=str(row[0]),
                    project_key=service.project_key,
                )
            )
            for row in work_rows
        ]
        event_rows = connection.execute(
            """
            SELECT
                e.event_id,
                e.record_id,
                e.from_status,
                e.to_status,
                e.reason,
                e.changed_at_utc,
                e.details_json
            FROM memory_status_events AS e
            JOIN memory_records AS r ON r.record_id = e.record_id
            WHERE r.project_key = ?
            ORDER BY e.event_id
            """,
            (service.project_key,),
        ).fetchall()
        status_events = [
            {
                "eventId": int(row[0]),
                "recordId": str(row[1]),
                "fromStatus": str(row[2]),
                "toStatus": str(row[3]),
                "reason": str(row[4]),
                "changedAtUtc": str(row[5]),
                "details": _read_details(str(row[6])),
            }
            for row in event_rows
        ]
        counts_by_type = _count_map(
            connection.execute(
                """
                SELECT record_type, COUNT(*)
                FROM memory_records
                WHERE project_key = ?
                GROUP BY record_type
                ORDER BY record_type
                """,
                (service.project_key,),
            ).fetchall()
        )
        counts_by_status = _count_map(
            connection.execute(
                """
                SELECT status, COUNT(*)
                FROM memory_records
                WHERE project_key = ?
                GROUP BY status
                ORDER BY status
                """,
                (service.project_key,),
            ).fetchall()
        )
        counts_by_work_status = _count_map(
            connection.execute(
                """
                SELECT status, COUNT(*)
                FROM active_work_items
                WHERE project_key = ?
                GROUP BY status
                ORDER BY status
                """,
                (service.project_key,),
            ).fetchall()
        )
        memory_schema_version = get_schema_version(connection)

    snapshot = {
        "projectKey": service.project_key,
        "memorySchemaVersion": memory_schema_version,
        "recordCount": record_count,
        "statusEventCount": status_event_count,
        "nodeCount": node_count,
        "activeWorkCount": active_work_count,
        "countsByType": counts_by_type,
        "countsByStatus": counts_by_status,
        "countsByWorkStatus": counts_by_work_status,
        "records": records,
        "statusEvents": status_events,
        "knowledgeNodes": knowledge_nodes,
        "activeWork": active_work,
    }
    return {
        "schemaVersion": MEMORY_AUDIT_SCHEMA_VERSION,
        "tool": "ue_memory_export",
        "generatedAtUtc": utc_now_iso(),
        **snapshot,
        "integrity": {
            "allRecordDigestsVerified": True,
            "snapshotSha256": _snapshot_sha256(snapshot),
        },
    }

from __future__ import annotations

import hashlib
import json
from typing import Any

from .database import get_schema_version, utc_now_iso
from .memory_service import ProjectMemoryService
from .project_memory import (
    MemoryRecord,
    MemoryScopeType,
    get_memory_record,
    open_project_memory_database,
)


MEMORY_AUDIT_SCHEMA_VERSION = "1.0"
MAX_AUDIT_RECORDS = 10_000
MAX_AUDIT_STATUS_EVENTS = 100_000


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
) -> dict[str, Any]:
    if not isinstance(service, ProjectMemoryService):
        raise TypeError("service must be a ProjectMemoryService.")
    if max_records < 1 or max_records > MAX_AUDIT_RECORDS:
        raise ValueError(f"max_records must be between 1 and {MAX_AUDIT_RECORDS}.")
    if max_status_events < 1 or max_status_events > MAX_AUDIT_STATUS_EVENTS:
        raise ValueError(
            f"max_status_events must be between 1 and {MAX_AUDIT_STATUS_EVENTS}."
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
        memory_schema_version = get_schema_version(connection)

    snapshot = {
        "projectKey": service.project_key,
        "memorySchemaVersion": memory_schema_version,
        "recordCount": record_count,
        "statusEventCount": status_event_count,
        "countsByType": counts_by_type,
        "countsByStatus": counts_by_status,
        "records": records,
        "statusEvents": status_events,
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

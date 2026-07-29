from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .database import get_metadata, get_schema_version, open_database
from .memory_tasks import TaskOutcomeDraft, build_task_outcome_record
from .project_memory import (
    MemoryRecord,
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryScopeType,
    MemorySearchHit,
    MemoryStatus,
    RevisionInvalidationResult,
    create_memory_record,
    get_memory_record,
    invalidate_memory_revisions,
    list_memory_records,
    mark_memory_record_superseded,
    open_project_memory_database,
    search_memory_records,
)


class ProjectMemoryServiceError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ProjectMemoryStatus:
    project_key: str
    database_path: Path
    schema_version: int
    record_count: int
    counts_by_type: dict[str, int]
    counts_by_status: dict[str, int]


@dataclass(frozen=True)
class ProjectMemoryIndexValidation:
    project_key: str
    index_database_path: Path
    indexed_asset_count: int
    invalidation: RevisionInvalidationResult


class ProjectMemoryService:
    def __init__(self, *, database_path: Path, project_key: str) -> None:
        normalized_project = project_key.strip() if isinstance(project_key, str) else ""
        if not normalized_project:
            raise ValueError("project_key must be a non-empty string.")
        self.database_path = database_path.expanduser().resolve()
        self.project_key = normalized_project

    def status(self) -> ProjectMemoryStatus:
        with open_project_memory_database(self.database_path) as connection:
            record_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM memory_records WHERE project_key = ?",
                    (self.project_key,),
                ).fetchone()[0]
            )
            type_rows = connection.execute(
                """
                SELECT record_type, COUNT(*)
                FROM memory_records
                WHERE project_key = ?
                GROUP BY record_type
                ORDER BY record_type
                """,
                (self.project_key,),
            ).fetchall()
            status_rows = connection.execute(
                """
                SELECT status, COUNT(*)
                FROM memory_records
                WHERE project_key = ?
                GROUP BY status
                ORDER BY status
                """,
                (self.project_key,),
            ).fetchall()
            return ProjectMemoryStatus(
                project_key=self.project_key,
                database_path=self.database_path,
                schema_version=get_schema_version(connection),
                record_count=record_count,
                counts_by_type={str(row[0]): int(row[1]) for row in type_rows},
                counts_by_status={str(row[0]): int(row[1]) for row in status_rows},
            )

    def add_record(self, draft: MemoryRecordDraft) -> MemoryRecord:
        if not isinstance(draft, MemoryRecordDraft):
            raise TypeError("draft must be a MemoryRecordDraft.")
        if draft.project_key.strip() != self.project_key:
            raise ProjectMemoryServiceError(
                "memory-project-mismatch",
                "Project Memory draft does not match the fixed project.",
                details={"expectedProjectKey": self.project_key, "actualProjectKey": draft.project_key},
            )
        with open_project_memory_database(self.database_path) as connection:
            return create_memory_record(connection, draft)

    def record_task_outcome(self, draft: TaskOutcomeDraft) -> MemoryRecord:
        record_draft = build_task_outcome_record(
            project_key=self.project_key,
            draft=draft,
        )
        return self.add_record(record_draft)

    def get_record(self, record_id: str) -> MemoryRecord:
        with open_project_memory_database(self.database_path) as connection:
            record = get_memory_record(connection, record_id)
            self._assert_fixed_project(record)
            return record

    def list_records(
        self,
        *,
        record_types: Sequence[MemoryRecordType | str] = (),
        statuses: Sequence[MemoryStatus | str] = (),
        subject_key: str = "",
        limit: int = 100,
    ) -> tuple[MemoryRecord, ...]:
        with open_project_memory_database(self.database_path) as connection:
            return list_memory_records(
                connection,
                project_key=self.project_key,
                record_types=record_types,
                statuses=statuses,
                subject_key=subject_key,
                limit=limit,
            )

    def search_records(
        self,
        *,
        query: str,
        record_types: Sequence[MemoryRecordType | str] = (),
        statuses: Sequence[MemoryStatus | str] = (
            MemoryStatus.VALID,
            MemoryStatus.UNVERIFIED,
            MemoryStatus.CONFLICTED,
        ),
        scope_type: MemoryScopeType | str | None = None,
        scope_key: str = "",
        limit: int = 20,
    ) -> tuple[MemorySearchHit, ...]:
        with open_project_memory_database(self.database_path) as connection:
            return search_memory_records(
                connection,
                project_key=self.project_key,
                query=query,
                record_types=record_types,
                statuses=statuses,
                scope_type=scope_type,
                scope_key=scope_key,
                limit=limit,
            )

    def mark_superseded(
        self,
        *,
        record_id: str,
        replacement_record_id: str,
        reason: str,
    ) -> MemoryRecord:
        with open_project_memory_database(self.database_path) as connection:
            current = get_memory_record(connection, record_id)
            replacement = get_memory_record(connection, replacement_record_id)
            self._assert_fixed_project(current)
            self._assert_fixed_project(replacement)
            return mark_memory_record_superseded(
                connection,
                record_id=record_id,
                replacement_record_id=replacement_record_id,
                reason=reason,
            )

    def validate_against_index(self, index_database_path: Path) -> ProjectMemoryIndexValidation:
        resolved_index = index_database_path.expanduser().resolve()
        with open_database(
            resolved_index,
            readonly=True,
            migrate=False,
            immutable=True,
        ) as index_connection:
            index_project_key = get_metadata(index_connection, "project_key", "")
            if index_project_key != self.project_key:
                raise ProjectMemoryServiceError(
                    "memory-index-project-mismatch",
                    "The index database does not match the fixed Project Memory project.",
                    details={
                        "expectedProjectKey": self.project_key,
                        "actualProjectKey": index_project_key,
                    },
                )
            revision_rows = index_connection.execute(
                """
                SELECT asset_path, revision_value
                FROM assets
                WHERE revision_value <> ''
                ORDER BY asset_path
                """
            ).fetchall()
        current_revisions = {str(row[0]): str(row[1]) for row in revision_rows}
        with open_project_memory_database(self.database_path) as memory_connection:
            invalidation = invalidate_memory_revisions(
                memory_connection,
                project_key=self.project_key,
                current_revisions=current_revisions,
            )
        return ProjectMemoryIndexValidation(
            project_key=self.project_key,
            index_database_path=resolved_index,
            indexed_asset_count=len(current_revisions),
            invalidation=invalidation,
        )

    def _assert_fixed_project(self, record: MemoryRecord) -> None:
        if record.project_key != self.project_key:
            raise ProjectMemoryServiceError(
                "memory-record-project-mismatch",
                "The Project Memory record belongs to another project.",
                details={
                    "expectedProjectKey": self.project_key,
                    "actualProjectKey": record.project_key,
                    "recordId": record.record_id,
                },
            )

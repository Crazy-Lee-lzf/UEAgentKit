from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from .active_work import (
    WorkItem,
    WorkItemDraft,
    WorkStatus,
    add_work_todo,
    block_work_item,
    cancel_work_item,
    complete_work_item,
    create_work_item,
    get_work_item,
    list_work_items,
    resume_work_item,
    set_work_links,
    set_work_next_action,
    start_work_item,
)
from .database import get_metadata, get_schema_version, open_database
from .memory_context import (
    ContextBudget,
    build_memory_context,
    evidence_payload,
    expand_memory_node,
)
from .memory_tasks import TaskOutcomeDraft, build_task_outcome_record
from .memory_tree import (
    KnowledgeNode,
    KnowledgeNodeDraft,
    KnowledgeNodeType,
    attach_memory_record_to_node,
    create_knowledge_node,
    delete_knowledge_node,
    detach_memory_record_from_node,
    get_knowledge_node,
    get_knowledge_node_by_path,
    list_knowledge_nodes,
    update_knowledge_node,
)
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
    node_count: int
    active_work_count: int
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
            node_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes WHERE project_key = ?",
                    (self.project_key,),
                ).fetchone()[0]
            )
            active_work_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM active_work_items
                    WHERE project_key = ? AND status IN ('planned', 'in_progress', 'blocked')
                    """,
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
                node_count=node_count,
                active_work_count=active_work_count,
                counts_by_type={str(row[0]): int(row[1]) for row in type_rows},
                counts_by_status={str(row[0]): int(row[1]) for row in status_rows},
            )

    def add_record(self, draft: MemoryRecordDraft) -> MemoryRecord:
        if not isinstance(draft, MemoryRecordDraft):
            raise TypeError("draft must be a MemoryRecordDraft.")
        self._assert_project_key(draft.project_key, code="memory-project-mismatch")
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

    def create_node(self, draft: KnowledgeNodeDraft) -> KnowledgeNode:
        if not isinstance(draft, KnowledgeNodeDraft):
            raise TypeError("draft must be a KnowledgeNodeDraft.")
        self._assert_project_key(draft.project_key, code="memory-node-project-mismatch")
        with open_project_memory_database(self.database_path) as connection:
            return create_knowledge_node(connection, draft)

    def get_node(self, *, node_id: str = "", path: str = "") -> KnowledgeNode:
        if bool(node_id) == bool(path):
            raise ValueError("Exactly one of node_id or path must be provided.")
        with open_project_memory_database(self.database_path) as connection:
            if node_id:
                return get_knowledge_node(
                    connection,
                    node_id=node_id,
                    project_key=self.project_key,
                )
            return get_knowledge_node_by_path(
                connection,
                project_key=self.project_key,
                path=path,
            )

    def list_nodes(self, *, parent_node_id: str | None = None, limit: int = 100) -> tuple[KnowledgeNode, ...]:
        with open_project_memory_database(self.database_path) as connection:
            return list_knowledge_nodes(
                connection,
                project_key=self.project_key,
                parent_node_id=parent_node_id,
                limit=limit,
            )

    def update_node(
        self,
        *,
        node_id: str,
        path: str | None = None,
        parent_node_id: str | None = None,
        node_type: KnowledgeNodeType | str | None = None,
        title: str | None = None,
        summary: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> KnowledgeNode:
        with open_project_memory_database(self.database_path) as connection:
            return update_knowledge_node(
                connection,
                project_key=self.project_key,
                node_id=node_id,
                path=path,
                parent_node_id=parent_node_id,
                node_type=node_type,
                title=title,
                summary=summary,
                details=details,
            )

    def delete_node(self, *, node_id: str) -> None:
        with open_project_memory_database(self.database_path) as connection:
            delete_knowledge_node(
                connection,
                project_key=self.project_key,
                node_id=node_id,
            )

    def attach_record(self, *, record_id: str, node_id: str) -> MemoryRecord:
        with open_project_memory_database(self.database_path) as connection:
            attach_memory_record_to_node(
                connection,
                project_key=self.project_key,
                record_id=record_id,
                node_id=node_id,
            )
            return get_memory_record(connection, record_id)

    def detach_record(self, *, record_id: str) -> MemoryRecord:
        with open_project_memory_database(self.database_path) as connection:
            detach_memory_record_from_node(
                connection,
                project_key=self.project_key,
                record_id=record_id,
            )
            return get_memory_record(connection, record_id)

    def create_work(self, draft: WorkItemDraft) -> WorkItem:
        if not isinstance(draft, WorkItemDraft):
            raise TypeError("draft must be a WorkItemDraft.")
        self._assert_project_key(draft.project_key, code="memory-work-project-mismatch")
        with open_project_memory_database(self.database_path) as connection:
            return create_work_item(connection, draft)

    def get_work(self, work_item_id: str) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return get_work_item(
                connection,
                work_item_id=work_item_id,
                project_key=self.project_key,
            )

    def list_work(
        self,
        *,
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
        with open_project_memory_database(self.database_path) as connection:
            return list_work_items(
                connection,
                project_key=self.project_key,
                statuses=statuses,
                node_ids=node_ids,
                asset_paths=asset_paths,
                query=query,
                limit=limit,
            )

    def start_work(self, *, work_item_id: str) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return start_work_item(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
            )

    def add_todo(self, *, work_item_id: str, text: str) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return add_work_todo(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
                text=text,
            )

    def set_next_action(self, *, work_item_id: str, next_action: str) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return set_work_next_action(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
                next_action=next_action,
            )

    def block_work(
        self,
        *,
        work_item_id: str,
        blocked_reason: str,
        next_action: str | None = None,
    ) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return block_work_item(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
                blocked_reason=blocked_reason,
                next_action=next_action,
            )

    def resume_work(self, *, work_item_id: str, next_action: str | None = None) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return resume_work_item(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
                next_action=next_action,
            )

    def complete_work(self, *, work_item_id: str) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return complete_work_item(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
            )

    def cancel_work(self, *, work_item_id: str) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return cancel_work_item(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
            )

    def set_work_links(
        self,
        *,
        work_item_id: str,
        node_ids: Sequence[str],
        asset_paths: Sequence[str],
    ) -> WorkItem:
        with open_project_memory_database(self.database_path) as connection:
            return set_work_links(
                connection,
                project_key=self.project_key,
                work_item_id=work_item_id,
                node_ids=node_ids,
                asset_paths=asset_paths,
            )

    def get_context(
        self,
        *,
        query: str = "",
        node_path: str = "",
        asset_paths: Sequence[str] = (),
        detail_level: int = 1,
        budget: ContextBudget = ContextBudget(),
    ) -> dict[str, Any]:
        with open_project_memory_database(self.database_path) as connection:
            return build_memory_context(
                connection,
                project_key=self.project_key,
                query=query,
                node_path=node_path,
                asset_paths=asset_paths,
                detail_level=detail_level,
                budget=budget,
            )

    def expand_node(
        self,
        *,
        path: str,
        detail_level: int = 1,
        depth: int = 1,
        budget: ContextBudget = ContextBudget(),
    ) -> dict[str, Any]:
        with open_project_memory_database(self.database_path) as connection:
            return expand_memory_node(
                connection,
                project_key=self.project_key,
                path=path,
                detail_level=detail_level,
                depth=depth,
                budget=budget,
            )

    def get_evidence(self, record_id: str) -> dict[str, Any]:
        record = self.get_record(record_id)
        return evidence_payload(record)

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
                """,
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

    def _assert_project_key(self, project_key: str, *, code: str) -> None:
        actual = project_key.strip() if isinstance(project_key, str) else ""
        if actual != self.project_key:
            raise ProjectMemoryServiceError(
                code,
                "Project Memory input does not match the fixed project.",
                details={"expectedProjectKey": self.project_key, "actualProjectKey": project_key},
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

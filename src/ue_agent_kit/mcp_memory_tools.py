from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from .active_work import WorkItem, WorkItemDraft, WorkStatus
from .memory_context import ContextBudget
from .memory_reports import memory_record_payload
from .memory_service import ProjectMemoryService, ProjectMemoryServiceError
from .memory_tree import KnowledgeNode, KnowledgeNodeDraft
from .memory_tasks import TaskOutcomeDraft
from .project_memory import (
    MemoryArtifact,
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemorySourceKind,
)


_FINDING_TYPES = {
    "projectFact",
    "decisionRecord",
    "knownIssue",
    "runtimeEvidence",
}
_SCOPE_FIELDS = {"scopeType", "scopeKey", "details"}
_REVISION_FIELDS = {"assetPath", "revision", "revisionStable"}
_ARTIFACT_FIELDS = {"artifactKind", "artifactRef", "details"}
_KNOWLEDGE_RECORD_TYPES = {"projectFact", "projectRule", "decisionRecord", "knownIssue"}
_KNOWLEDGE_SOURCE_KINDS = {"user-confirmed", "model-inferred"}


def _required(value: dict[str, Any], key: str, field_name: str = "payload") -> Any:
    if key not in value:
        raise ValueError(f"{field_name}.{key} is required.")
    return value[key]


def _string_list(value: Any, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array of strings.")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{index}] must be a non-empty string.")
        result.append(item.strip())
    return tuple(result)


def _knowledge_node_payload(node: KnowledgeNode) -> dict[str, Any]:
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


def _work_item_payload(work: WorkItem) -> dict[str, Any]:
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


def _strict_object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be one object.")
    return value


def _strict_fields(value: dict[str, Any], allowed: set[str], field_name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"{field_name} contains unsupported fields: {', '.join(unknown)}")


def _details(value: Any, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    return dict(_strict_object(value, field_name))


def _parse_scopes(values: list[dict[str, Any]] | None) -> tuple[MemoryScope, ...]:
    result: list[MemoryScope] = []
    for index, raw in enumerate(values or []):
        item = _strict_object(raw, f"scopes[{index}]")
        _strict_fields(item, _SCOPE_FIELDS, f"scopes[{index}]")
        result.append(
            MemoryScope(
                scope_type=item.get("scopeType", ""),
                scope_key=item.get("scopeKey", ""),
                details=_details(item.get("details"), f"scopes[{index}].details"),
            )
        )
    return tuple(result)


def _parse_revisions(values: list[dict[str, Any]] | None) -> tuple[MemoryRevision, ...]:
    result: list[MemoryRevision] = []
    for index, raw in enumerate(values or []):
        item = _strict_object(raw, f"revision_set[{index}]")
        _strict_fields(item, _REVISION_FIELDS, f"revision_set[{index}]")
        stable = item.get("revisionStable", True)
        if not isinstance(stable, bool):
            raise ValueError(f"revision_set[{index}].revisionStable must be boolean.")
        result.append(
            MemoryRevision(
                asset_path=item.get("assetPath", ""),
                revision=item.get("revision", ""),
                revision_stable=stable,
            )
        )
    return tuple(result)


def _parse_artifacts(values: list[dict[str, Any]] | None) -> tuple[MemoryArtifact, ...]:
    result: list[MemoryArtifact] = []
    for index, raw in enumerate(values or []):
        item = _strict_object(raw, f"artifacts[{index}]")
        _strict_fields(item, _ARTIFACT_FIELDS, f"artifacts[{index}]")
        result.append(
            MemoryArtifact(
                artifact_kind=item.get("artifactKind", ""),
                artifact_ref=item.get("artifactRef", ""),
                details=_details(item.get("details"), f"artifacts[{index}].details"),
            )
        )
    return tuple(result)


def _memory_error(error: Exception) -> Exception:
    if isinstance(error, KeyError):
        message = str(error.args[0]) if error.args else "Project Memory item not found."
        if message.startswith("Knowledge node"):
            return ProjectMemoryServiceError("memory-node-not-found", message)
        if message.startswith("Active Work"):
            return ProjectMemoryServiceError("memory-work-not-found", message)
        return ProjectMemoryServiceError("memory-record-not-found", message)
    return error


def register_memory_tools(
    *,
    server: Any,
    memory_service: ProjectMemoryService,
    index_database_path: Path,
    read_annotations: Any,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    planning_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=read_annotations)
    def ue_memory_search(
        query: str,
        record_types: list[str] | None = None,
        statuses: list[str] | None = None,
        scope_type: str = "",
        scope_key: str = "",
        limit: int = 20,
    ) -> dict[str, Any]:
        """Search persistent memory for the fixed project; stale and superseded records are excluded by default."""
        try:
            kwargs: dict[str, Any] = {
                "query": query,
                "record_types": tuple(record_types or []),
                "scope_type": scope_type or None,
                "scope_key": scope_key,
                "limit": limit,
            }
            if statuses is not None:
                if not statuses:
                    raise ValueError("statuses must be omitted or contain at least one status.")
                kwargs["statuses"] = tuple(statuses)
            hits = memory_service.search_records(**kwargs)
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_search",
                "ok": True,
                "readOnly": True,
                "projectKey": memory_service.project_key,
                "resultCount": len(hits),
                "items": [
                    {"rank": hit.rank, "record": memory_record_payload(hit.record)} for hit in hits
                ],
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_search", _memory_error(exc), read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_memory_get(record_id: str) -> dict[str, Any]:
        """Get one exact persistent Project Memory record by stable record ID."""
        try:
            record = memory_service.get_record(record_id)
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_get",
                "ok": True,
                "readOnly": True,
                "projectKey": memory_service.project_key,
                "record": memory_record_payload(record),
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_get", _memory_error(exc), read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_memory_add_rule(
        subject_key: str,
        title: str,
        body: str,
        source_ref: str = "",
        confidence: float = 1.0,
        observed_at_utc: str = "",
        scopes: list[dict[str, Any]] | None = None,
        revision_set: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one user-confirmed rule for the fixed project; call only after explicit user confirmation."""
        try:
            record = memory_service.add_record(
                MemoryRecordDraft(
                    project_key=memory_service.project_key,
                    record_type="projectRule",
                    subject_key=subject_key,
                    title=title,
                    body=body,
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    source_ref=source_ref,
                    confidence=confidence,
                    observed_at_utc=observed_at_utc,
                    scopes=_parse_scopes(scopes),
                    revision_set=_parse_revisions(revision_set),
                    artifacts=_parse_artifacts(artifacts),
                    details=_details(details, "details"),
                )
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_add_rule",
                "ok": True,
                "readOnly": False,
                "projectKey": memory_service.project_key,
                "record": memory_record_payload(record),
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_add_rule", _memory_error(exc), read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_memory_record_finding(
        record_type: Literal[
            "projectFact",
            "decisionRecord",
            "knownIssue",
            "runtimeEvidence",
        ],
        subject_key: str,
        title: str,
        body: str,
        source_kind: Literal["tool-observed", "model-inferred"] = "model-inferred",
        source_ref: str = "",
        confidence: float = 0.5,
        observed_at_utc: str = "",
        scopes: list[dict[str, Any]] | None = None,
        revision_set: list[dict[str, Any]] | None = None,
        artifacts: list[dict[str, Any]] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one tool-observed or model-inferred finding without claiming user confirmation."""
        try:
            if record_type not in _FINDING_TYPES:
                raise ValueError("record_type is not allowed for ue_memory_record_finding.")
            record = memory_service.add_record(
                MemoryRecordDraft(
                    project_key=memory_service.project_key,
                    record_type=record_type,
                    subject_key=subject_key,
                    title=title,
                    body=body,
                    source_kind=source_kind,
                    source_ref=source_ref,
                    confidence=confidence,
                    observed_at_utc=observed_at_utc,
                    scopes=_parse_scopes(scopes),
                    revision_set=_parse_revisions(revision_set),
                    artifacts=_parse_artifacts(artifacts),
                    details=_details(details, "details"),
                )
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_record_finding",
                "ok": True,
                "readOnly": False,
                "projectKey": memory_service.project_key,
                "record": memory_record_payload(record),
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_record_finding", _memory_error(exc), read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_memory_record_task(
        task_key: str,
        title: str,
        conclusion: str,
        outcome: Literal["succeeded", "failed", "rolledBack", "cancelled"],
        patch_ref: str,
        backup_manifest_ref: str,
        validation_evidence_ref: str,
        revision_set: list[dict[str, Any]],
        scopes: list[dict[str, Any]] | None = None,
        confidence: float = 1.0,
        observed_at_utc: str = "",
        patch_details: dict[str, Any] | None = None,
        backup_manifest_details: dict[str, Any] | None = None,
        validation_evidence_details: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist one completed Task Record with exact Patch, Backup, Validation, and Revision evidence."""
        try:
            record = memory_service.record_task_outcome(
                TaskOutcomeDraft(
                    task_key=task_key,
                    title=title,
                    conclusion=conclusion,
                    outcome=outcome,
                    patch_ref=patch_ref,
                    backup_manifest_ref=backup_manifest_ref,
                    validation_evidence_ref=validation_evidence_ref,
                    revision_set=_parse_revisions(revision_set),
                    scopes=_parse_scopes(scopes),
                    confidence=confidence,
                    observed_at_utc=observed_at_utc,
                    patch_details=_details(patch_details, "patch_details"),
                    backup_manifest_details=_details(
                        backup_manifest_details,
                        "backup_manifest_details",
                    ),
                    validation_evidence_details=_details(
                        validation_evidence_details,
                        "validation_evidence_details",
                    ),
                    details=_details(details, "details"),
                )
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_record_task",
                "ok": True,
                "readOnly": False,
                "projectKey": memory_service.project_key,
                "record": memory_record_payload(record),
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_record_task", _memory_error(exc), read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_memory_mark_superseded(
        record_id: str,
        replacement_record_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Mark one fixed-project record as superseded by another compatible record without deleting history."""
        try:
            record = memory_service.mark_superseded(
                record_id=record_id,
                replacement_record_id=replacement_record_id,
                reason=reason,
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_mark_superseded",
                "ok": True,
                "readOnly": False,
                "projectKey": memory_service.project_key,
                "record": memory_record_payload(record),
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response(
                "ue_memory_mark_superseded",
                _memory_error(exc),
                read_only=False,
            )

    @server.tool(annotations=planning_annotations)
    def ue_memory_validate() -> dict[str, Any]:
        """Compare stable memory Revision Sets with the fixed immutable index and mark mismatches stale."""
        try:
            result = memory_service.validate_against_index(index_database_path)
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_validate",
                "ok": True,
                "readOnly": False,
                "projectKey": result.project_key,
                "indexedAssetCount": result.indexed_asset_count,
                "checkedRecordIds": list(result.invalidation.checked_record_ids),
                "staleRecordIds": list(result.invalidation.stale_record_ids),
                "reasons": result.invalidation.reasons,
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_validate", _memory_error(exc), read_only=False)


    @server.tool(annotations=read_annotations)
    def ue_memory_get_context(
        query: str = "",
        node_path: str = "",
        asset_paths: list[str] | None = None,
        detail_level: int = 1,
        budget_chars: int = 8000,
        max_nodes: int = 12,
        max_records: int = 20,
        max_depth: int = 2,
    ) -> dict[str, Any]:
        """Get budgeted progressive context for the fixed project; evidence stays on demand."""
        try:
            context = memory_service.get_context(
                query=query,
                node_path=node_path,
                asset_paths=tuple(asset_paths or []),
                detail_level=detail_level,
                budget=ContextBudget(
                    max_chars=budget_chars,
                    max_nodes=max_nodes,
                    max_records=max_records,
                    max_depth=max_depth,
                ),
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_get_context",
                "ok": True,
                "readOnly": True,
                "projectKey": memory_service.project_key,
                "context": context,
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_get_context", _memory_error(exc), read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_memory_expand_node(
        path: str,
        detail_level: int = 1,
        depth: int = 1,
        budget_chars: int = 8000,
        max_nodes: int = 20,
        max_records: int = 20,
        max_depth: int = 4,
    ) -> dict[str, Any]:
        """Expand one exact Knowledge Path to a bounded depth and detail level."""
        try:
            context = memory_service.expand_node(
                path=path,
                detail_level=detail_level,
                depth=depth,
                budget=ContextBudget(
                    max_chars=budget_chars,
                    max_nodes=max_nodes,
                    max_records=max_records,
                    max_depth=max_depth,
                ),
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_expand_node",
                "ok": True,
                "readOnly": True,
                "projectKey": memory_service.project_key,
                "context": context,
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_expand_node", _memory_error(exc), read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_memory_get_evidence(record_id: str) -> dict[str, Any]:
        """Get exact source, revision, and artifact evidence for one stable Record ID."""
        try:
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_get_evidence",
                "ok": True,
                "readOnly": True,
                "projectKey": memory_service.project_key,
                "evidence": memory_service.get_evidence(record_id),
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_get_evidence", _memory_error(exc), read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_memory_update_knowledge(
        action: Literal[
            "create_node",
            "update_node",
            "delete_node",
            "attach_record",
            "detach_record",
            "add_record",
        ],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Manage Knowledge Nodes and stable knowledge through explicit actions; SQL and revision digests are not accepted."""
        try:
            item = _strict_object(payload, "payload")
            result: dict[str, Any]
            if action == "create_node":
                _strict_fields(
                    item,
                    {"path", "parentNodeId", "nodeType", "title", "summary", "details", "nodeId"},
                    "payload",
                )
                node = memory_service.create_node(
                    KnowledgeNodeDraft(
                        project_key=memory_service.project_key,
                        path=_required(item, "path"),
                        parent_node_id=item.get("parentNodeId", ""),
                        node_type=_required(item, "nodeType"),
                        title=_required(item, "title"),
                        summary=_required(item, "summary"),
                        details=_details(item.get("details"), "payload.details"),
                        node_id=item.get("nodeId", ""),
                    )
                )
                result = {"node": _knowledge_node_payload(node)}
            elif action == "update_node":
                _strict_fields(
                    item,
                    {"nodeId", "path", "parentNodeId", "nodeType", "title", "summary", "details"},
                    "payload",
                )
                node = memory_service.update_node(
                    node_id=_required(item, "nodeId"),
                    path=item.get("path"),
                    parent_node_id=item.get("parentNodeId") if "parentNodeId" in item else None,
                    node_type=item.get("nodeType"),
                    title=item.get("title"),
                    summary=item.get("summary"),
                    details=_details(item["details"], "payload.details") if "details" in item else None,
                )
                result = {"node": _knowledge_node_payload(node)}
            elif action == "delete_node":
                _strict_fields(item, {"nodeId"}, "payload")
                node_id = _required(item, "nodeId")
                memory_service.delete_node(node_id=node_id)
                result = {"deletedNodeId": node_id}
            elif action == "attach_record":
                _strict_fields(item, {"recordId", "nodeId"}, "payload")
                record = memory_service.attach_record(
                    record_id=_required(item, "recordId"),
                    node_id=_required(item, "nodeId"),
                )
                result = {"record": memory_record_payload(record)}
            elif action == "detach_record":
                _strict_fields(item, {"recordId"}, "payload")
                record = memory_service.detach_record(record_id=_required(item, "recordId"))
                result = {"record": memory_record_payload(record)}
            elif action == "add_record":
                _strict_fields(
                    item,
                    {
                        "nodeId",
                        "recordType",
                        "subjectKey",
                        "title",
                        "body",
                        "sourceKind",
                        "sourceRef",
                        "confidence",
                        "scopes",
                        "details",
                    },
                    "payload",
                )
                record_type = _required(item, "recordType")
                if record_type not in _KNOWLEDGE_RECORD_TYPES:
                    raise ValueError("payload.recordType is not allowed for stable knowledge.")
                source_kind = item.get("sourceKind", "model-inferred")
                if source_kind not in _KNOWLEDGE_SOURCE_KINDS:
                    raise ValueError("payload.sourceKind must be user-confirmed or model-inferred.")
                record = memory_service.add_record(
                    MemoryRecordDraft(
                        project_key=memory_service.project_key,
                        record_type=record_type,
                        subject_key=_required(item, "subjectKey"),
                        title=_required(item, "title"),
                        body=_required(item, "body"),
                        source_kind=source_kind,
                        source_ref=item.get("sourceRef", ""),
                        confidence=item.get("confidence", 0.5),
                        scopes=_parse_scopes(item.get("scopes")),
                        details=_details(item.get("details"), "payload.details"),
                        node_id=item.get("nodeId", ""),
                    )
                )
                result = {"record": memory_record_payload(record)}
            else:
                raise ValueError("Unsupported knowledge action.")
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_update_knowledge",
                "ok": True,
                "readOnly": False,
                "projectKey": memory_service.project_key,
                "action": action,
                **result,
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_update_knowledge", _memory_error(exc), read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_memory_update_work(
        action: Literal[
            "plan",
            "start",
            "get",
            "add_todo",
            "set_next_action",
            "block",
            "resume",
            "complete",
            "cancel",
            "set_links",
        ],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Manage the fixed project's Active Work lifecycle without mixing temporary state into long-term knowledge."""
        try:
            item = _strict_object(payload, "payload")
            if action == "plan":
                _strict_fields(
                    item,
                    {
                        "title",
                        "description",
                        "nextAction",
                        "priority",
                        "owner",
                        "nodeIds",
                        "assetPaths",
                        "details",
                        "workItemId",
                    },
                    "payload",
                )
                work = memory_service.create_work(
                    WorkItemDraft(
                        project_key=memory_service.project_key,
                        title=_required(item, "title"),
                        description=_required(item, "description"),
                        next_action=_required(item, "nextAction"),
                        priority=item.get("priority", 50),
                        owner=item.get("owner", ""),
                        status=WorkStatus.PLANNED,
                        node_ids=_string_list(item.get("nodeIds"), "payload.nodeIds"),
                        asset_paths=_string_list(item.get("assetPaths"), "payload.assetPaths"),
                        details=_details(item.get("details"), "payload.details"),
                        work_item_id=item.get("workItemId", ""),
                    )
                )
            elif action == "start":
                _strict_fields(item, {"workItemId"}, "payload")
                work = memory_service.start_work(work_item_id=_required(item, "workItemId"))
            elif action == "get":
                _strict_fields(item, {"workItemId"}, "payload")
                work = memory_service.get_work(_required(item, "workItemId"))
            elif action == "add_todo":
                _strict_fields(item, {"workItemId", "text"}, "payload")
                work = memory_service.add_todo(
                    work_item_id=_required(item, "workItemId"),
                    text=_required(item, "text"),
                )
            elif action == "set_next_action":
                _strict_fields(item, {"workItemId", "nextAction"}, "payload")
                work = memory_service.set_next_action(
                    work_item_id=_required(item, "workItemId"),
                    next_action=_required(item, "nextAction"),
                )
            elif action == "block":
                _strict_fields(item, {"workItemId", "blockedReason", "nextAction"}, "payload")
                work = memory_service.block_work(
                    work_item_id=_required(item, "workItemId"),
                    blocked_reason=_required(item, "blockedReason"),
                    next_action=item.get("nextAction"),
                )
            elif action == "resume":
                _strict_fields(item, {"workItemId", "nextAction"}, "payload")
                work = memory_service.resume_work(
                    work_item_id=_required(item, "workItemId"),
                    next_action=item.get("nextAction"),
                )
            elif action == "complete":
                _strict_fields(item, {"workItemId"}, "payload")
                work = memory_service.complete_work(work_item_id=_required(item, "workItemId"))
            elif action == "cancel":
                _strict_fields(item, {"workItemId"}, "payload")
                work = memory_service.cancel_work(work_item_id=_required(item, "workItemId"))
            elif action == "set_links":
                _strict_fields(item, {"workItemId", "nodeIds", "assetPaths"}, "payload")
                work = memory_service.set_work_links(
                    work_item_id=_required(item, "workItemId"),
                    node_ids=_string_list(item.get("nodeIds"), "payload.nodeIds"),
                    asset_paths=_string_list(item.get("assetPaths"), "payload.assetPaths"),
                )
            else:
                raise ValueError("Unsupported Active Work action.")
            return {
                "schemaVersion": "1.0",
                "tool": "ue_memory_update_work",
                "ok": True,
                "readOnly": False,
                "projectKey": memory_service.project_key,
                "action": action,
                "work": _work_item_payload(work),
            }
        except (
            ProjectMemoryServiceError,
            FileNotFoundError,
            KeyError,
            OSError,
            TypeError,
            ValueError,
            RuntimeError,
            sqlite3.Error,
        ) as exc:
            return error_response("ue_memory_update_work", _memory_error(exc), read_only=False)

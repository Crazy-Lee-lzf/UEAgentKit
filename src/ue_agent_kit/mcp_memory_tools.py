from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Literal

from .memory_service import ProjectMemoryService, ProjectMemoryServiceError
from .project_memory import (
    MemoryArtifact,
    MemoryRecord,
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
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


def _record_payload(record: MemoryRecord) -> dict[str, Any]:
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


def _memory_error(error: Exception) -> Exception:
    if isinstance(error, KeyError):
        message = str(error.args[0]) if error.args else "Project Memory record not found."
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
                    {"rank": hit.rank, "record": _record_payload(hit.record)} for hit in hits
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
                "record": _record_payload(record),
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
                "record": _record_payload(record),
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
                "record": _record_payload(record),
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
                "record": _record_payload(record),
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

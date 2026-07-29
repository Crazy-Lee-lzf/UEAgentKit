from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Sequence

from .project_memory import (
    MemoryArtifact,
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
)


TASK_CONTRACT_VERSION = "1.0"
TASK_ARTIFACT_KINDS = ("patch", "backupManifest", "validationEvidence")


class TaskOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ROLLED_BACK = "rolledBack"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TaskOutcomeDraft:
    task_key: str
    title: str
    conclusion: str
    outcome: TaskOutcome | str
    patch_ref: str
    backup_manifest_ref: str
    validation_evidence_ref: str
    revision_set: Sequence[MemoryRevision]
    scopes: Sequence[MemoryScope] = ()
    confidence: float = 1.0
    observed_at_utc: str = ""
    patch_details: dict[str, Any] = field(default_factory=dict)
    backup_manifest_details: dict[str, Any] = field(default_factory=dict)
    validation_evidence_details: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


def _require_text(
    value: Any,
    field_name: str,
    *,
    maximum_length: int,
    allow_newlines: bool = False,
) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    normalized = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(normalized) > maximum_length:
        raise ValueError(f"{field_name} must not exceed {maximum_length} characters.")
    if "\x00" in normalized or (not allow_newlines and "\n" in normalized):
        raise ValueError(f"{field_name} must not contain control or newline characters.")
    return normalized


def _details(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be an object.")
    return dict(value)


def _artifact_ref(value: Any, field_name: str) -> str:
    normalized = _require_text(value, field_name, maximum_length=512)
    windows_path = PureWindowsPath(normalized)
    posix_path = PurePosixPath(normalized)
    if windows_path.is_absolute() or posix_path.is_absolute():
        raise ValueError(f"{field_name} must be an opaque ID or project-relative reference, not an absolute path.")
    if ".." in windows_path.parts or ".." in posix_path.parts:
        raise ValueError(f"{field_name} must not traverse parent directories.")
    return normalized


def _subject_key(task_key: Any) -> str:
    normalized = _require_text(task_key, "task_key", maximum_length=240)
    return normalized if normalized.startswith("task:") else f"task:{normalized}"


def _normalize_outcome(value: TaskOutcome | str) -> TaskOutcome:
    try:
        return TaskOutcome(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in TaskOutcome)
        raise ValueError(f"outcome must be one of: {allowed}.") from exc


def build_task_outcome_record(
    *,
    project_key: str,
    draft: TaskOutcomeDraft,
) -> MemoryRecordDraft:
    if not isinstance(draft, TaskOutcomeDraft):
        raise TypeError("draft must be a TaskOutcomeDraft.")
    normalized_project = _require_text(project_key, "project_key", maximum_length=256)
    subject_key = _subject_key(draft.task_key)
    title = _require_text(draft.title, "title", maximum_length=256)
    conclusion = _require_text(
        draft.conclusion,
        "conclusion",
        maximum_length=8192,
        allow_newlines=True,
    )
    outcome = _normalize_outcome(draft.outcome)
    patch_ref = _artifact_ref(draft.patch_ref, "patch_ref")
    backup_manifest_ref = _artifact_ref(draft.backup_manifest_ref, "backup_manifest_ref")
    validation_evidence_ref = _artifact_ref(
        draft.validation_evidence_ref,
        "validation_evidence_ref",
    )
    if not draft.revision_set:
        raise ValueError("revision_set must contain at least one stable asset Revision.")
    revisions: list[MemoryRevision] = []
    for index, revision in enumerate(draft.revision_set):
        if not isinstance(revision, MemoryRevision):
            raise ValueError(f"revision_set[{index}] must be a MemoryRevision.")
        if revision.revision_stable is not True:
            raise ValueError("Task Outcome revision_set entries must all be stable.")
        revisions.append(revision)

    scopes: list[MemoryScope] = []
    project_scope_found = False
    for index, scope in enumerate(draft.scopes):
        if not isinstance(scope, MemoryScope):
            raise ValueError(f"scopes[{index}] must be a MemoryScope.")
        if MemoryScopeType(scope.scope_type) == MemoryScopeType.PROJECT:
            if scope.scope_key != normalized_project:
                raise ValueError("Project scope must match the fixed Project Key.")
            project_scope_found = True
        scopes.append(scope)
    if not project_scope_found:
        scopes.insert(0, MemoryScope(MemoryScopeType.PROJECT, normalized_project))

    task_details = _details(draft.details, "details")
    reserved = {"taskContractVersion", "taskOutcome", "artifactKinds"}
    conflict = sorted(reserved.intersection(task_details))
    if conflict:
        raise ValueError("details must not override reserved Task Outcome fields: " + ", ".join(conflict))
    task_details.update(
        {
            "taskContractVersion": TASK_CONTRACT_VERSION,
            "taskOutcome": outcome.value,
            "artifactKinds": list(TASK_ARTIFACT_KINDS),
        }
    )

    artifacts = (
        MemoryArtifact("patch", patch_ref, _details(draft.patch_details, "patch_details")),
        MemoryArtifact(
            "backupManifest",
            backup_manifest_ref,
            _details(draft.backup_manifest_details, "backup_manifest_details"),
        ),
        MemoryArtifact(
            "validationEvidence",
            validation_evidence_ref,
            _details(draft.validation_evidence_details, "validation_evidence_details"),
        ),
    )
    return MemoryRecordDraft(
        project_key=normalized_project,
        record_type=MemoryRecordType.TASK_RECORD,
        subject_key=subject_key,
        title=title,
        body=conclusion,
        source_kind=MemorySourceKind.TOOL_OBSERVED,
        source_ref=validation_evidence_ref,
        confidence=draft.confidence,
        observed_at_utc=draft.observed_at_utc,
        scopes=tuple(scopes),
        revision_set=tuple(revisions),
        artifacts=artifacts,
        details=task_details,
    )

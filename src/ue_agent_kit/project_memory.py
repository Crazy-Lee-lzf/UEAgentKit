from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .database import connect_database, get_schema_version, utc_now_iso
from .memory_schema import CURRENT_MEMORY_SCHEMA_VERSION, MEMORY_MIGRATIONS


_MEMORY_ID_PATTERN = re.compile(r"^mem_[0-9a-f]{32}$")
_CONFLICT_DETECTABLE_TYPES = {
    "projectFact",
    "projectRule",
    "decisionRecord",
    "knownIssue",
}


class MemoryRecordType(StrEnum):
    PROJECT_FACT = "projectFact"
    PROJECT_RULE = "projectRule"
    DECISION_RECORD = "decisionRecord"
    KNOWN_ISSUE = "knownIssue"
    TASK_RECORD = "taskRecord"
    RUNTIME_EVIDENCE = "runtimeEvidence"


class MemorySourceKind(StrEnum):
    USER_CONFIRMED = "user-confirmed"
    TOOL_OBSERVED = "tool-observed"
    MODEL_INFERRED = "model-inferred"


class MemoryStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"
    CONFLICTED = "conflicted"
    SUPERSEDED = "superseded"
    UNVERIFIED = "unverified"


class MemoryScopeType(StrEnum):
    PROJECT = "project"
    ASSET = "asset"
    SYMBOL = "symbol"
    GRAPH = "graph"
    NODE = "node"
    DATA_TABLE_ROW = "dataTableRow"
    LOG = "log"
    FILE = "file"
    EXTERNAL = "external"


class MemoryRelationKind(StrEnum):
    CONFLICTS_WITH = "conflictsWith"
    SUPERSEDES = "supersedes"
    SUPPORTS = "supports"
    DERIVED_FROM = "derivedFrom"


@dataclass(frozen=True)
class MemoryScope:
    scope_type: MemoryScopeType | str
    scope_key: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryRevision:
    asset_path: str
    revision: str
    revision_stable: bool = True


@dataclass(frozen=True)
class MemoryArtifact:
    artifact_kind: str
    artifact_ref: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MemoryRelation:
    relation_kind: MemoryRelationKind
    target_record_id: str
    created_at_utc: str
    details: dict[str, Any]


@dataclass(frozen=True)
class MemoryRecordDraft:
    project_key: str
    record_type: MemoryRecordType | str
    subject_key: str
    title: str
    body: str
    source_kind: MemorySourceKind | str
    source_ref: str = ""
    confidence: float = 1.0
    observed_at_utc: str = ""
    scopes: Sequence[MemoryScope] = ()
    revision_set: Sequence[MemoryRevision] = ()
    artifacts: Sequence[MemoryArtifact] = ()
    details: dict[str, Any] = field(default_factory=dict)
    record_id: str = ""


@dataclass(frozen=True)
class MemoryRecord:
    record_id: str
    project_key: str
    record_type: MemoryRecordType
    subject_key: str
    title: str
    body: str
    source_kind: MemorySourceKind
    source_ref: str
    confidence: float
    status: MemoryStatus
    content_sha256: str
    created_at_utc: str
    observed_at_utc: str
    updated_at_utc: str
    superseded_by_record_id: str
    scopes: tuple[MemoryScope, ...]
    revision_set: tuple[MemoryRevision, ...]
    artifacts: tuple[MemoryArtifact, ...]
    relations: tuple[MemoryRelation, ...]
    details: dict[str, Any]


@dataclass(frozen=True)
class RevisionInvalidationResult:
    checked_record_ids: tuple[str, ...]
    stale_record_ids: tuple[str, ...]
    reasons: dict[str, dict[str, Any]]


_ALLOWED_STATUS_TRANSITIONS = {
    MemoryStatus.VALID: {
        MemoryStatus.STALE,
        MemoryStatus.CONFLICTED,
        MemoryStatus.SUPERSEDED,
    },
    MemoryStatus.UNVERIFIED: {
        MemoryStatus.VALID,
        MemoryStatus.STALE,
        MemoryStatus.CONFLICTED,
        MemoryStatus.SUPERSEDED,
    },
    MemoryStatus.CONFLICTED: {
        MemoryStatus.VALID,
        MemoryStatus.STALE,
        MemoryStatus.SUPERSEDED,
    },
    MemoryStatus.STALE: {
        MemoryStatus.VALID,
        MemoryStatus.SUPERSEDED,
    },
    MemoryStatus.SUPERSEDED: set(),
}


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_memory_migrations(connection: sqlite3.Connection) -> int:
    current_version = get_schema_version(connection)
    if current_version > CURRENT_MEMORY_SCHEMA_VERSION:
        raise RuntimeError(
            "Project Memory schema "
            f"{current_version} is newer than supported schema {CURRENT_MEMORY_SCHEMA_VERSION}."
        )

    for migration in MEMORY_MIGRATIONS:
        if migration.version <= current_version:
            continue
        description = _quote_sql_literal(migration.description)
        applied_at = _quote_sql_literal(utc_now_iso())
        script = (
            "BEGIN IMMEDIATE;\n"
            + migration.sql
            + "\n"
            + "INSERT INTO memory_schema_migrations(version, description, applied_at_utc) "
            + f"VALUES ({migration.version}, {description}, {applied_at});\n"
            + f"PRAGMA user_version = {migration.version};\n"
            + "COMMIT;\n"
        )
        try:
            connection.executescript(script)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        current_version = migration.version

    if current_version != CURRENT_MEMORY_SCHEMA_VERSION:
        raise RuntimeError(
            "Project Memory migration stopped at schema "
            f"{current_version}; expected {CURRENT_MEMORY_SCHEMA_VERSION}."
        )
    return current_version


def assert_memory_fts_available(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_records_fts'"
    ).fetchone()
    if row is None:
        raise RuntimeError("Project Memory FTS5 schema is incomplete.")


@contextmanager
def open_project_memory_database(
    path: Path,
    *,
    readonly: bool = False,
) -> Iterator[sqlite3.Connection]:
    connection = connect_database(path, readonly=readonly)
    try:
        if readonly:
            version = get_schema_version(connection)
            if version != CURRENT_MEMORY_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Project Memory schema {version} does not match supported schema "
                    f"{CURRENT_MEMORY_SCHEMA_VERSION}."
                )
        else:
            apply_memory_migrations(connection)
        assert_memory_fts_available(connection)
        yield connection
    finally:
        connection.close()


def _require_text(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


def _enum_value(value: Any, enum_type: type[StrEnum], field_name: str) -> StrEnum:
    try:
        return enum_type(value)
    except (TypeError, ValueError) as exc:
        allowed = ", ".join(item.value for item in enum_type)
        raise ValueError(f"{field_name} must be one of: {allowed}.") from exc


def _canonical_json(value: Any, field_name: str) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be strict JSON-serializable.") from exc


def _normalize_details(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be one JSON object.")
    _canonical_json(value, field_name)
    return dict(value)


def _read_json(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise RuntimeError("Stored Project Memory details must contain one JSON object.")
    return parsed


def _normalize_utc(value: str, field_name: str) -> str:
    if not value:
        return utc_now_iso()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone.")
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_record_id() -> str:
    return "mem_" + uuid.uuid4().hex


def _normalize_record_id(value: str) -> str:
    record_id = value or _new_record_id()
    if not _MEMORY_ID_PATTERN.fullmatch(record_id):
        raise ValueError("record_id must match mem_<32 lowercase hex characters>.")
    return record_id


def _normalize_scopes(scopes: Sequence[MemoryScope]) -> tuple[MemoryScope, ...]:
    normalized: list[MemoryScope] = []
    seen: set[tuple[str, str]] = set()
    for index, scope in enumerate(scopes):
        if not isinstance(scope, MemoryScope):
            raise ValueError(f"scopes[{index}] must be a MemoryScope.")
        scope_type = _enum_value(scope.scope_type, MemoryScopeType, f"scopes[{index}].scope_type")
        scope_key = _require_text(scope.scope_key, f"scopes[{index}].scope_key")
        key = (scope_type.value, scope_key)
        if key in seen:
            raise ValueError("scopes must not contain duplicate type/key pairs.")
        seen.add(key)
        details = _normalize_details(scope.details, f"scopes[{index}].details")
        normalized.append(MemoryScope(scope_type, scope_key, details))
    return tuple(normalized)


def _normalize_revisions(revisions: Sequence[MemoryRevision]) -> tuple[MemoryRevision, ...]:
    normalized: list[MemoryRevision] = []
    seen: set[str] = set()
    for index, revision in enumerate(revisions):
        if not isinstance(revision, MemoryRevision):
            raise ValueError(f"revision_set[{index}] must be a MemoryRevision.")
        asset_path = _require_text(revision.asset_path, f"revision_set[{index}].asset_path")
        if not asset_path.startswith("/Game/"):
            raise ValueError(f"revision_set[{index}].asset_path must start with /Game/.")
        revision_value = _require_text(revision.revision, f"revision_set[{index}].revision")
        if asset_path in seen:
            raise ValueError("revision_set must not contain duplicate asset paths.")
        seen.add(asset_path)
        if not isinstance(revision.revision_stable, bool):
            raise ValueError(f"revision_set[{index}].revision_stable must be boolean.")
        normalized.append(MemoryRevision(asset_path, revision_value, revision.revision_stable))
    return tuple(sorted(normalized, key=lambda item: item.asset_path.casefold()))


def _normalize_artifacts(artifacts: Sequence[MemoryArtifact]) -> tuple[MemoryArtifact, ...]:
    normalized: list[MemoryArtifact] = []
    seen: set[tuple[str, str]] = set()
    for index, artifact in enumerate(artifacts):
        if not isinstance(artifact, MemoryArtifact):
            raise ValueError(f"artifacts[{index}] must be a MemoryArtifact.")
        artifact_kind = _require_text(artifact.artifact_kind, f"artifacts[{index}].artifact_kind")
        artifact_ref = _require_text(artifact.artifact_ref, f"artifacts[{index}].artifact_ref")
        key = (artifact_kind, artifact_ref)
        if key in seen:
            raise ValueError("artifacts must not contain duplicate kind/ref pairs.")
        seen.add(key)
        details = _normalize_details(artifact.details, f"artifacts[{index}].details")
        normalized.append(MemoryArtifact(artifact_kind, artifact_ref, details))
    return tuple(normalized)


def _initial_status(
    source_kind: MemorySourceKind,
    revisions: Sequence[MemoryRevision],
) -> MemoryStatus:
    if source_kind == MemorySourceKind.USER_CONFIRMED:
        return MemoryStatus.VALID
    if source_kind == MemorySourceKind.TOOL_OBSERVED and revisions and all(
        revision.revision_stable for revision in revisions
    ):
        return MemoryStatus.VALID
    return MemoryStatus.UNVERIFIED


def _content_sha256(
    *,
    record_type: MemoryRecordType,
    subject_key: str,
    title: str,
    body: str,
    scopes: Sequence[MemoryScope],
    details: dict[str, Any],
) -> str:
    payload = {
        "recordType": record_type.value,
        "subjectKey": subject_key,
        "title": title,
        "body": body,
        "scopes": [
            {
                "scopeType": MemoryScopeType(scope.scope_type).value,
                "scopeKey": scope.scope_key,
                "details": scope.details,
            }
            for scope in scopes
        ],
        "details": details,
    }
    canonical = _canonical_json(payload, "record content")
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _insert_status_event(
    connection: sqlite3.Connection,
    *,
    record_id: str,
    from_status: str,
    to_status: MemoryStatus,
    reason: str,
    changed_at_utc: str,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO memory_status_events(
            record_id,
            from_status,
            to_status,
            reason,
            changed_at_utc,
            details_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            from_status,
            to_status.value,
            reason,
            changed_at_utc,
            _canonical_json(details or {}, "status event details"),
        ),
    )


def _transition_status(
    connection: sqlite3.Connection,
    *,
    record_id: str,
    to_status: MemoryStatus,
    reason: str,
    details: dict[str, Any] | None = None,
    changed_at_utc: str | None = None,
) -> bool:
    row = connection.execute(
        "SELECT status FROM memory_records WHERE record_id = ?",
        (record_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Project Memory record not found: {record_id}")
    from_status = MemoryStatus(str(row[0]))
    if from_status == to_status:
        return False
    if to_status not in _ALLOWED_STATUS_TRANSITIONS[from_status]:
        raise ValueError(f"Project Memory status cannot transition from {from_status} to {to_status}.")
    timestamp = changed_at_utc or utc_now_iso()
    connection.execute(
        "UPDATE memory_records SET status = ?, updated_at_utc = ? WHERE record_id = ?",
        (to_status.value, timestamp, record_id),
    )
    _insert_status_event(
        connection,
        record_id=record_id,
        from_status=from_status.value,
        to_status=to_status,
        reason=_require_text(reason, "reason"),
        changed_at_utc=timestamp,
        details=details,
    )
    return True


def _insert_relation(
    connection: sqlite3.Connection,
    *,
    from_record_id: str,
    relation_kind: MemoryRelationKind,
    to_record_id: str,
    created_at_utc: str,
    details: dict[str, Any] | None = None,
) -> None:
    connection.execute(
        """
        INSERT OR IGNORE INTO memory_relations(
            from_record_id,
            relation_kind,
            to_record_id,
            created_at_utc,
            details_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            from_record_id,
            relation_kind.value,
            to_record_id,
            created_at_utc,
            _canonical_json(details or {}, "relation details"),
        ),
    )


def create_memory_record(
    connection: sqlite3.Connection,
    draft: MemoryRecordDraft,
) -> MemoryRecord:
    if not isinstance(draft, MemoryRecordDraft):
        raise TypeError("draft must be a MemoryRecordDraft.")

    record_id = _normalize_record_id(draft.record_id)
    project_key = _require_text(draft.project_key, "project_key")
    record_type = _enum_value(draft.record_type, MemoryRecordType, "record_type")
    subject_key = _require_text(draft.subject_key, "subject_key")
    title = _require_text(draft.title, "title")
    body = _require_text(draft.body, "body")
    source_kind = _enum_value(draft.source_kind, MemorySourceKind, "source_kind")
    if not isinstance(draft.source_ref, str):
        raise ValueError("source_ref must be a string.")
    source_ref = draft.source_ref.strip()
    confidence = float(draft.confidence)
    if not math.isfinite(confidence) or confidence < 0.0 or confidence > 1.0:
        raise ValueError("confidence must be a finite number between 0.0 and 1.0.")
    scopes = _normalize_scopes(draft.scopes)
    revisions = _normalize_revisions(draft.revision_set)
    artifacts = _normalize_artifacts(draft.artifacts)
    details = _normalize_details(draft.details, "details")
    details_json = _canonical_json(details, "details")
    timestamp = utc_now_iso()
    observed_at_utc = _normalize_utc(draft.observed_at_utc, "observed_at_utc")
    status = _initial_status(source_kind, revisions)
    content_sha256 = _content_sha256(
        record_type=record_type,
        subject_key=subject_key,
        title=title,
        body=body,
        scopes=scopes,
        details=details,
    )

    with connection:
        connection.execute(
            """
            INSERT INTO memory_records(
                record_id,
                project_key,
                record_type,
                subject_key,
                title,
                body,
                source_kind,
                source_ref,
                confidence,
                status,
                content_sha256,
                created_at_utc,
                observed_at_utc,
                updated_at_utc,
                details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                project_key,
                record_type.value,
                subject_key,
                title,
                body,
                source_kind.value,
                source_ref,
                confidence,
                status.value,
                content_sha256,
                timestamp,
                observed_at_utc,
                timestamp,
                details_json,
            ),
        )
        for ordinal, scope in enumerate(scopes):
            connection.execute(
                """
                INSERT INTO memory_scopes(record_id, ordinal, scope_type, scope_key, details_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    ordinal,
                    MemoryScopeType(scope.scope_type).value,
                    scope.scope_key,
                    _canonical_json(scope.details, "scope details"),
                ),
            )
        for ordinal, revision in enumerate(revisions):
            connection.execute(
                """
                INSERT INTO memory_revisions(
                    record_id,
                    ordinal,
                    asset_path,
                    revision,
                    revision_stable
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    ordinal,
                    revision.asset_path,
                    revision.revision,
                    1 if revision.revision_stable else 0,
                ),
            )
        for ordinal, artifact in enumerate(artifacts):
            connection.execute(
                """
                INSERT INTO memory_artifacts(
                    record_id,
                    ordinal,
                    artifact_kind,
                    artifact_ref,
                    details_json
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    ordinal,
                    artifact.artifact_kind,
                    artifact.artifact_ref,
                    _canonical_json(artifact.details, "artifact details"),
                ),
            )
        _insert_status_event(
            connection,
            record_id=record_id,
            from_status="",
            to_status=status,
            reason="record-created",
            changed_at_utc=timestamp,
            details={"sourceKind": source_kind.value},
        )

        if record_type.value in _CONFLICT_DETECTABLE_TYPES:
            conflicts = connection.execute(
                """
                SELECT record_id
                FROM memory_records
                WHERE project_key = ?
                  AND record_type = ?
                  AND subject_key = ?
                  AND record_id <> ?
                  AND content_sha256 <> ?
                  AND status IN ('valid', 'unverified', 'conflicted')
                ORDER BY created_at_utc, record_id
                """,
                (
                    project_key,
                    record_type.value,
                    subject_key,
                    record_id,
                    content_sha256,
                ),
            ).fetchall()
            if conflicts:
                _transition_status(
                    connection,
                    record_id=record_id,
                    to_status=MemoryStatus.CONFLICTED,
                    reason="conflicting-record-created",
                    details={"subjectKey": subject_key},
                    changed_at_utc=timestamp,
                )
                for conflict in conflicts:
                    conflict_id = str(conflict[0])
                    _transition_status(
                        connection,
                        record_id=conflict_id,
                        to_status=MemoryStatus.CONFLICTED,
                        reason="conflicting-record-created",
                        details={"conflictingRecordId": record_id, "subjectKey": subject_key},
                        changed_at_utc=timestamp,
                    )
                    relation_details = {"subjectKey": subject_key}
                    _insert_relation(
                        connection,
                        from_record_id=record_id,
                        relation_kind=MemoryRelationKind.CONFLICTS_WITH,
                        to_record_id=conflict_id,
                        created_at_utc=timestamp,
                        details=relation_details,
                    )
                    _insert_relation(
                        connection,
                        from_record_id=conflict_id,
                        relation_kind=MemoryRelationKind.CONFLICTS_WITH,
                        to_record_id=record_id,
                        created_at_utc=timestamp,
                        details=relation_details,
                    )

    return get_memory_record(connection, record_id)


def _load_scopes(connection: sqlite3.Connection, record_id: str) -> tuple[MemoryScope, ...]:
    rows = connection.execute(
        """
        SELECT scope_type, scope_key, details_json
        FROM memory_scopes
        WHERE record_id = ?
        ORDER BY ordinal
        """,
        (record_id,),
    ).fetchall()
    return tuple(
        MemoryScope(MemoryScopeType(str(row[0])), str(row[1]), _read_json(str(row[2])))
        for row in rows
    )


def _load_revisions(connection: sqlite3.Connection, record_id: str) -> tuple[MemoryRevision, ...]:
    rows = connection.execute(
        """
        SELECT asset_path, revision, revision_stable
        FROM memory_revisions
        WHERE record_id = ?
        ORDER BY ordinal
        """,
        (record_id,),
    ).fetchall()
    return tuple(MemoryRevision(str(row[0]), str(row[1]), bool(row[2])) for row in rows)


def _load_artifacts(connection: sqlite3.Connection, record_id: str) -> tuple[MemoryArtifact, ...]:
    rows = connection.execute(
        """
        SELECT artifact_kind, artifact_ref, details_json
        FROM memory_artifacts
        WHERE record_id = ?
        ORDER BY ordinal
        """,
        (record_id,),
    ).fetchall()
    return tuple(
        MemoryArtifact(str(row[0]), str(row[1]), _read_json(str(row[2]))) for row in rows
    )


def _load_relations(connection: sqlite3.Connection, record_id: str) -> tuple[MemoryRelation, ...]:
    rows = connection.execute(
        """
        SELECT relation_kind, to_record_id, created_at_utc, details_json
        FROM memory_relations
        WHERE from_record_id = ?
        ORDER BY relation_kind, to_record_id
        """,
        (record_id,),
    ).fetchall()
    return tuple(
        MemoryRelation(
            MemoryRelationKind(str(row[0])),
            str(row[1]),
            str(row[2]),
            _read_json(str(row[3])),
        )
        for row in rows
    )


def get_memory_record(connection: sqlite3.Connection, record_id: str) -> MemoryRecord:
    normalized_id = _require_text(record_id, "record_id")
    row = connection.execute(
        """
        SELECT
            record_id,
            project_key,
            record_type,
            subject_key,
            title,
            body,
            source_kind,
            source_ref,
            confidence,
            status,
            content_sha256,
            created_at_utc,
            observed_at_utc,
            updated_at_utc,
            COALESCE(superseded_by_record_id, ''),
            details_json
        FROM memory_records
        WHERE record_id = ?
        """,
        (normalized_id,),
    ).fetchone()
    if row is None:
        raise KeyError(f"Project Memory record not found: {normalized_id}")
    return MemoryRecord(
        record_id=str(row[0]),
        project_key=str(row[1]),
        record_type=MemoryRecordType(str(row[2])),
        subject_key=str(row[3]),
        title=str(row[4]),
        body=str(row[5]),
        source_kind=MemorySourceKind(str(row[6])),
        source_ref=str(row[7]),
        confidence=float(row[8]),
        status=MemoryStatus(str(row[9])),
        content_sha256=str(row[10]),
        created_at_utc=str(row[11]),
        observed_at_utc=str(row[12]),
        updated_at_utc=str(row[13]),
        superseded_by_record_id=str(row[14]),
        scopes=_load_scopes(connection, normalized_id),
        revision_set=_load_revisions(connection, normalized_id),
        artifacts=_load_artifacts(connection, normalized_id),
        relations=_load_relations(connection, normalized_id),
        details=_read_json(str(row[15])),
    )


def list_memory_records(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    record_types: Sequence[MemoryRecordType | str] = (),
    statuses: Sequence[MemoryStatus | str] = (),
    subject_key: str = "",
    limit: int = 100,
) -> tuple[MemoryRecord, ...]:
    normalized_project = _require_text(project_key, "project_key")
    if limit < 1 or limit > 500:
        raise ValueError("limit must be between 1 and 500.")
    clauses = ["project_key = ?"]
    parameters: list[Any] = [normalized_project]
    if record_types:
        normalized_types = [
            _enum_value(value, MemoryRecordType, "record_types").value for value in record_types
        ]
        clauses.append("record_type IN (" + ",".join("?" for _ in normalized_types) + ")")
        parameters.extend(normalized_types)
    if statuses:
        normalized_statuses = [
            _enum_value(value, MemoryStatus, "statuses").value for value in statuses
        ]
        clauses.append("status IN (" + ",".join("?" for _ in normalized_statuses) + ")")
        parameters.extend(normalized_statuses)
    if subject_key:
        clauses.append("subject_key = ?")
        parameters.append(_require_text(subject_key, "subject_key"))
    parameters.append(limit)
    rows = connection.execute(
        "SELECT record_id FROM memory_records WHERE "
        + " AND ".join(clauses)
        + " ORDER BY updated_at_utc DESC, record_id LIMIT ?",
        parameters,
    ).fetchall()
    return tuple(get_memory_record(connection, str(row[0])) for row in rows)


def mark_memory_record_superseded(
    connection: sqlite3.Connection,
    *,
    record_id: str,
    replacement_record_id: str,
    reason: str,
) -> MemoryRecord:
    current = get_memory_record(connection, record_id)
    replacement = get_memory_record(connection, replacement_record_id)
    if current.record_id == replacement.record_id:
        raise ValueError("A Project Memory record cannot supersede itself.")
    if current.project_key != replacement.project_key:
        raise ValueError("Superseded and replacement records must use the same project_key.")
    if current.record_type != replacement.record_type:
        raise ValueError("Superseded and replacement records must use the same record_type.")
    if current.subject_key != replacement.subject_key:
        raise ValueError("Superseded and replacement records must use the same subject_key.")
    if replacement.status == MemoryStatus.SUPERSEDED:
        raise ValueError("The replacement Project Memory record is already superseded.")
    timestamp = utc_now_iso()
    with connection:
        _transition_status(
            connection,
            record_id=current.record_id,
            to_status=MemoryStatus.SUPERSEDED,
            reason=reason,
            details={"replacementRecordId": replacement.record_id},
            changed_at_utc=timestamp,
        )
        connection.execute(
            "UPDATE memory_records SET superseded_by_record_id = ? WHERE record_id = ?",
            (replacement.record_id, current.record_id),
        )
        _insert_relation(
            connection,
            from_record_id=replacement.record_id,
            relation_kind=MemoryRelationKind.SUPERSEDES,
            to_record_id=current.record_id,
            created_at_utc=timestamp,
            details={"reason": _require_text(reason, "reason")},
        )
    return get_memory_record(connection, current.record_id)


def invalidate_memory_revisions(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    current_revisions: Mapping[str, str],
) -> RevisionInvalidationResult:
    normalized_project = _require_text(project_key, "project_key")
    normalized_current: dict[str, str] = {}
    for asset_path, revision in current_revisions.items():
        normalized_asset = _require_text(asset_path, "current_revisions asset path")
        if not normalized_asset.startswith("/Game/"):
            raise ValueError("current_revisions asset paths must start with /Game/.")
        normalized_revision = _require_text(revision, f"current_revisions[{normalized_asset}]")
        normalized_current[normalized_asset] = normalized_revision

    rows = connection.execute(
        """
        SELECT r.record_id, v.asset_path, v.revision
        FROM memory_records AS r
        JOIN memory_revisions AS v ON v.record_id = r.record_id
        WHERE r.project_key = ?
          AND r.status NOT IN ('stale', 'superseded')
          AND v.revision_stable = 1
        ORDER BY r.record_id, v.ordinal
        """,
        (normalized_project,),
    ).fetchall()
    expected_by_record: dict[str, list[tuple[str, str]]] = {}
    for row in rows:
        expected_by_record.setdefault(str(row[0]), []).append((str(row[1]), str(row[2])))

    checked = tuple(sorted(expected_by_record))
    stale_ids: list[str] = []
    reasons: dict[str, dict[str, Any]] = {}
    with connection:
        for record_id, expected_revisions in expected_by_record.items():
            missing: list[str] = []
            mismatched: list[dict[str, str]] = []
            for asset_path, expected_revision in expected_revisions:
                current_revision = normalized_current.get(asset_path)
                if current_revision is None:
                    missing.append(asset_path)
                elif current_revision != expected_revision:
                    mismatched.append(
                        {
                            "assetPath": asset_path,
                            "expectedRevision": expected_revision,
                            "currentRevision": current_revision,
                        }
                    )
            if not missing and not mismatched:
                continue
            detail = {"missingAssetPaths": missing, "mismatchedRevisions": mismatched}
            _transition_status(
                connection,
                record_id=record_id,
                to_status=MemoryStatus.STALE,
                reason="revision-set-mismatch",
                details=detail,
            )
            stale_ids.append(record_id)
            reasons[record_id] = detail
    return RevisionInvalidationResult(checked, tuple(sorted(stale_ids)), reasons)

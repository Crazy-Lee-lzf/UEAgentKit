from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from .database import utc_now_iso
from .project_memory import open_project_memory_database


MAX_L0_CAPTURE_BATCH_EVENTS = 8
MAX_L0_ASSET_PATHS = 16
MAX_L0_DETAILS_JSON_BYTES = 4096
MAX_L0_SOURCE_REF_CHARS = 512
MAX_L0_ARTIFACT_REF_CHARS = 512
MAX_L0_LIST_RESULTS = 100

L0_EVENT_KINDS = frozenset(
    {
        "live_write",
        "change_set",
        "batch_execution",
        "checkpoint",
        "checkpoint_set",
        "semantic_diff",
        "trust",
        "recovery",
        "workflow_rejection",
    }
)
L0_OUTCOMES = frozenset(
    {"success", "partial", "failed", "rejected", "no-op", "recovered", "superseded"}
)
_EVENT_ID_PATTERN = re.compile(r"^l0_[0-9a-f]{32}$")
_CHAIN_ID_PATTERN = re.compile(r"^chain_[0-9a-f]{32}$")


class MemoryL0Error(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MemoryL0EventDraft:
    project_key: str
    event_kind: str
    source_ref: str
    artifact_digest: str
    lifecycle_state: str
    outcome: str
    artifact_ref: str = ""
    asset_paths: Sequence[str] = ()
    change_set_id: str = ""
    hypothesis_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    occurred_at_utc: str = ""
    event_id: str = ""


@dataclass(frozen=True)
class MemoryL0Event:
    event_id: str
    project_key: str
    event_kind: str
    occurred_at_utc: str
    source_ref: str
    artifact_ref: str
    artifact_digest: str
    lifecycle_state: str
    outcome: str
    asset_paths: tuple[str, ...]
    change_set_id: str
    hypothesis_id: str
    details: dict[str, Any]
    distilled: bool


@dataclass(frozen=True)
class MemoryEvidenceChainDraft:
    project_key: str
    hypothesis: str
    verdict: str = "inconclusive"
    confidence: str = "low"
    context: dict[str, Any] = field(default_factory=dict)
    created_at_utc: str = ""
    verified_at_utc: str = ""
    superseded_by: str = ""
    chain_id: str = ""


@dataclass(frozen=True)
class MemoryEvidenceChain:
    chain_id: str
    project_key: str
    hypothesis: str
    context: dict[str, Any]
    verdict: str
    confidence: str
    created_at_utc: str
    verified_at_utc: str
    superseded_by: str


@dataclass(frozen=True)
class MemoryL0CaptureResult:
    enabled: bool
    captured_count: int
    existing_count: int
    failed_count: int
    event_ids: tuple[str, ...]
    error_code: str = ""

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "enabled": self.enabled,
            "capturedCount": self.captured_count,
            "existingCount": self.existing_count,
            "failedCount": self.failed_count,
            "eventIds": list(self.event_ids),
        }
        if self.error_code:
            payload["errorCode"] = self.error_code
        return payload


MemoryL0CaptureBatchResult = MemoryL0CaptureResult


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


def _bounded_text(
    value: Any,
    field_name: str,
    maximum: int,
    *,
    required: bool = True,
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field_name} must be a non-empty string.")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} exceeds the {maximum} character bound.")
    return normalized


def _utc(value: str, field_name: str, *, allow_empty: bool = False) -> str:
    if not value:
        return "" if allow_empty else utc_now_iso()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone.")
    return (
        parsed.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _event_id(project_key: str, event_kind: str, source_ref: str, digest: str) -> str:
    identity = _canonical_json(
        [project_key, event_kind, source_ref, digest],
        "event identity",
    )
    return "l0_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _row_event(row: sqlite3.Row) -> MemoryL0Event:
    return MemoryL0Event(
        event_id=str(row["event_id"]),
        project_key=str(row["project_key"]),
        event_kind=str(row["event_kind"]),
        occurred_at_utc=str(row["occurred_at_utc"]),
        source_ref=str(row["source_ref"]),
        artifact_ref=str(row["artifact_ref"]),
        artifact_digest=str(row["artifact_digest"]),
        lifecycle_state=str(row["lifecycle_state"]),
        outcome=str(row["outcome"]),
        asset_paths=tuple(json.loads(str(row["asset_paths_json"]))),
        change_set_id=str(row["change_set_id"]),
        hypothesis_id=str(row["hypothesis_id"] or ""),
        details=dict(json.loads(str(row["details_json"]))),
        distilled=bool(row["distilled"]),
    )


def _row_chain(row: sqlite3.Row) -> MemoryEvidenceChain:
    return MemoryEvidenceChain(
        chain_id=str(row["chain_id"]),
        project_key=str(row["project_key"]),
        hypothesis=str(row["hypothesis"]),
        context=dict(json.loads(str(row["context_json"]))),
        verdict=str(row["verdict"]),
        confidence=str(row["confidence"]),
        created_at_utc=str(row["created_at_utc"]),
        verified_at_utc=str(row["verified_at_utc"]),
        superseded_by=str(row["superseded_by"] or ""),
    )


class MemoryL0CaptureService:
    def __init__(
        self,
        *,
        database_path: Path,
        project_key: str,
        artifact_root: Path,
    ) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.project_key = _bounded_text(project_key, "project_key", 512)
        self.artifact_root = artifact_root.expanduser().resolve()

    def _normalize_draft(self, draft: MemoryL0EventDraft) -> tuple[Any, ...]:
        if not isinstance(draft, MemoryL0EventDraft):
            raise TypeError("draft must be a MemoryL0EventDraft.")
        if draft.project_key != self.project_key:
            raise MemoryL0Error(
                "memory-l0-project-mismatch",
                "L0 event does not match the fixed project.",
            )
        event_kind = _bounded_text(draft.event_kind, "event_kind", 64)
        if event_kind not in L0_EVENT_KINDS:
            raise ValueError("event_kind is not in the M2 allowlist.")
        source_ref = _bounded_text(
            draft.source_ref,
            "source_ref",
            MAX_L0_SOURCE_REF_CHARS,
        )
        artifact_ref = _bounded_text(
            draft.artifact_ref,
            "artifact_ref",
            MAX_L0_ARTIFACT_REF_CHARS,
            required=False,
        )
        digest = _bounded_text(draft.artifact_digest, "artifact_digest", 64)
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("artifact_digest must be one lowercase SHA-256 digest.")
        lifecycle_state = _bounded_text(
            draft.lifecycle_state,
            "lifecycle_state",
            128,
        )
        outcome = _bounded_text(draft.outcome, "outcome", 32)
        if outcome not in L0_OUTCOMES:
            raise ValueError("outcome is not allowed.")
        details = dict(draft.details)
        asset_paths = tuple(
            dict.fromkeys(
                _bounded_text(path, "asset_path", 512)
                for path in draft.asset_paths
            )
        )
        if len(asset_paths) > MAX_L0_ASSET_PATHS:
            asset_paths = asset_paths[:MAX_L0_ASSET_PATHS]
            details["assetPathsTruncated"] = True
        details_json = _canonical_json(details, "details")
        if len(details_json.encode("utf-8")) > MAX_L0_DETAILS_JSON_BYTES:
            raise ValueError(
                f"details exceeds the {MAX_L0_DETAILS_JSON_BYTES} byte bound."
            )
        expected_id = _event_id(
            self.project_key,
            event_kind,
            source_ref,
            digest,
        )
        event_identity = draft.event_id or expected_id
        if not _EVENT_ID_PATTERN.fullmatch(event_identity):
            raise ValueError("event_id must match l0_<32 lowercase hex characters>.")
        if event_identity != expected_id:
            raise ValueError("event_id does not match the deterministic event identity.")
        return (
            event_identity,
            self.project_key,
            event_kind,
            _utc(draft.occurred_at_utc, "occurred_at_utc"),
            source_ref,
            artifact_ref,
            digest,
            lifecycle_state,
            outcome,
            _canonical_json(asset_paths, "asset_paths"),
            _bounded_text(
                draft.change_set_id,
                "change_set_id",
                256,
                required=False,
            ),
            _bounded_text(
                draft.hypothesis_id,
                "hypothesis_id",
                128,
                required=False,
            )
            or None,
            details_json,
        )

    def append_event(self, draft: MemoryL0EventDraft) -> MemoryL0CaptureResult:
        return self.append_events((draft,))

    def append_events(
        self,
        drafts: Sequence[MemoryL0EventDraft],
    ) -> MemoryL0CaptureBatchResult:
        if not drafts:
            raise ValueError("drafts must contain at least one event.")
        if len(drafts) > MAX_L0_CAPTURE_BATCH_EVENTS:
            raise ValueError(
                f"drafts exceeds the {MAX_L0_CAPTURE_BATCH_EVENTS} event batch bound."
            )
        rows = [self._normalize_draft(draft) for draft in drafts]
        if len({row[0] for row in rows}) != len(rows):
            raise ValueError("drafts contains duplicate exact-state event identities.")
        captured = 0
        with open_project_memory_database(self.database_path) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                hypothesis_ids = {
                    str(row[11]) for row in rows if row[11] is not None
                }
                for hypothesis_id in hypothesis_ids:
                    chain = connection.execute(
                        """
                        SELECT project_key
                        FROM memory_evidence_chains
                        WHERE chain_id = ?
                        """,
                        (hypothesis_id,),
                    ).fetchone()
                    if chain is not None and str(chain[0]) != self.project_key:
                        raise MemoryL0Error(
                            "memory-l0-hypothesis-project-mismatch",
                            "L0 hypothesis belongs to another project.",
                        )
                for row in rows:
                    cursor = connection.execute(
                        """
                        INSERT OR IGNORE INTO memory_l0_events(
                            event_id, project_key, event_kind, occurred_at_utc,
                            source_ref, artifact_ref, artifact_digest,
                            lifecycle_state, outcome, asset_paths_json,
                            change_set_id, hypothesis_id, details_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        row,
                    )
                    captured += int(cursor.rowcount > 0)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return MemoryL0CaptureResult(
            enabled=True,
            captured_count=captured,
            existing_count=len(rows) - captured,
            failed_count=0,
            event_ids=tuple(str(row[0]) for row in rows),
        )

    def artifact_draft(
        self,
        *,
        artifact_path: Path,
        event_kind: str,
        lifecycle_state: str,
        outcome: str,
        asset_paths: Sequence[str] = (),
        change_set_id: str = "",
        hypothesis_id: str = "",
        details: dict[str, Any] | None = None,
        occurred_at_utc: str = "",
    ) -> MemoryL0EventDraft:
        try:
            resolved = artifact_path.expanduser().resolve(strict=True)
        except OSError as exc:
            raise MemoryL0Error(
                "memory-l0-artifact-invalid",
                "L0 artifact must be an existing regular file.",
            ) from exc
        if not resolved.is_file():
            raise MemoryL0Error(
                "memory-l0-artifact-invalid",
                "L0 artifact must be an existing regular file.",
            )
        try:
            relative = resolved.relative_to(self.artifact_root).as_posix()
        except ValueError as exc:
            raise MemoryL0Error(
                "memory-l0-artifact-outside-root",
                "L0 artifact escaped the fixed artifact root.",
            ) from exc
        artifact_ref = _bounded_text(
            relative,
            "artifact_ref",
            MAX_L0_ARTIFACT_REF_CHARS,
        )
        digest = _sha256_file(resolved)
        return MemoryL0EventDraft(
            project_key=self.project_key,
            event_kind=event_kind,
            source_ref=f"artifact:{artifact_ref}",
            artifact_ref=artifact_ref,
            artifact_digest=digest,
            lifecycle_state=lifecycle_state,
            outcome=outcome,
            asset_paths=asset_paths,
            change_set_id=change_set_id,
            hypothesis_id=hypothesis_id,
            details=details or {},
            occurred_at_utc=occurred_at_utc,
        )

    def capture_artifact_events(
        self,
        drafts: Sequence[MemoryL0EventDraft],
    ) -> MemoryL0CaptureBatchResult:
        return self.append_events(drafts)

    def rejection_draft(
        self,
        *,
        operation: str,
        error_code: str,
        asset_paths: Sequence[str] = (),
        change_set_id: str = "",
        target_identity: str = "",
        policy_digest: str = "",
        outcome: str = "rejected",
    ) -> MemoryL0EventDraft:
        operation = _bounded_text(operation, "operation", 128)
        bounded_assets = list(asset_paths[:MAX_L0_ASSET_PATHS])
        bounded_digest = _bounded_text(
            policy_digest,
            "policy_digest",
            64,
            required=False,
        )
        if bounded_digest and not re.fullmatch(r"[0-9a-f]{64}", bounded_digest):
            raise ValueError("policy_digest must be one lowercase SHA-256 digest.")
        payload: dict[str, Any] = {
            "operation": operation,
            "errorCode": _bounded_text(error_code, "error_code", 128),
            "assetPaths": bounded_assets,
            "changeSetId": _bounded_text(
                change_set_id,
                "change_set_id",
                256,
                required=False,
            ),
            "targetIdentity": _bounded_text(
                target_identity,
                "target_identity",
                512,
                required=False,
            ),
        }
        if bounded_digest:
            payload["policyDigest"] = bounded_digest
        details: dict[str, Any] = {
            "operation": operation,
            "errorCode": payload["errorCode"],
            "targetIdentity": payload["targetIdentity"],
        }
        if bounded_digest:
            details["policyDigest"] = bounded_digest
        digest = hashlib.sha256(
            _canonical_json(payload, "rejection payload").encode("utf-8")
        ).hexdigest()
        return MemoryL0EventDraft(
            project_key=self.project_key,
            event_kind="workflow_rejection",
            source_ref=f"workflow:{operation}",
            artifact_digest=digest,
            lifecycle_state="rejected",
            outcome=outcome,
            asset_paths=asset_paths,
            change_set_id=change_set_id,
            details=details,
        )

    def capture_rejection(self, **kwargs: Any) -> MemoryL0CaptureResult:
        return self.append_event(self.rejection_draft(**kwargs))

    def get_event(self, event_id: str) -> MemoryL0Event:
        if not _EVENT_ID_PATTERN.fullmatch(event_id):
            raise ValueError("event_id must match l0_<32 lowercase hex characters>.")
        with open_project_memory_database(
            self.database_path,
            readonly=True,
        ) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM memory_l0_events
                WHERE project_key = ? AND event_id = ?
                """,
                (self.project_key, event_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"L0 event not found: {event_id}")
        return _row_event(row)

    def list_events(
        self,
        *,
        event_kinds: Sequence[str] = (),
        change_set_id: str = "",
        distilled: bool | None = None,
        limit: int = 50,
    ) -> tuple[MemoryL0Event, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > MAX_L0_LIST_RESULTS
        ):
            raise ValueError(
                f"limit must be between 1 and {MAX_L0_LIST_RESULTS}."
            )
        kinds = tuple(dict.fromkeys(event_kinds))
        if any(kind not in L0_EVENT_KINDS for kind in kinds):
            raise ValueError(
                "event_kinds contains a value outside the M2 allowlist."
            )
        clauses = ["project_key = ?"]
        params: list[Any] = [self.project_key]
        if kinds:
            clauses.append(
                "event_kind IN (" + ",".join("?" for _ in kinds) + ")"
            )
            params.extend(kinds)
        if change_set_id:
            clauses.append("change_set_id = ?")
            params.append(change_set_id)
        if distilled is not None:
            clauses.append("distilled = ?")
            params.append(int(distilled))
        params.append(limit)
        with open_project_memory_database(
            self.database_path,
            readonly=True,
        ) as connection:
            rows = connection.execute(
                "SELECT * FROM memory_l0_events WHERE "
                + " AND ".join(clauses)
                + " ORDER BY occurred_at_utc DESC, event_id DESC LIMIT ?",
                params,
            ).fetchall()
        return tuple(_row_event(row) for row in rows)

    def mark_event_distilled(self, event_id: str) -> None:
        """Narrow M3 primitive: mark one exact L0 event as deterministically evaluated.

        This is the only L0 mutation M3 is allowed to perform. It never exposes
        generic update/delete and never changes event content.
        """
        if not _EVENT_ID_PATTERN.fullmatch(event_id):
            raise ValueError("event_id must match l0_<32 lowercase hex characters>.")
        with open_project_memory_database(self.database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE memory_l0_events
                SET distilled = 1
                WHERE project_key = ? AND event_id = ?
                """,
                (self.project_key, event_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"L0 event not found: {event_id}")
            connection.commit()

    def create_evidence_chain(
        self,
        draft: MemoryEvidenceChainDraft,
    ) -> MemoryEvidenceChain:
        if not isinstance(draft, MemoryEvidenceChainDraft):
            raise TypeError("draft must be a MemoryEvidenceChainDraft.")
        if draft.project_key != self.project_key:
            raise MemoryL0Error(
                "memory-chain-project-mismatch",
                "Evidence Chain does not match the fixed project.",
            )
        hypothesis = _bounded_text(draft.hypothesis, "hypothesis", 2000)
        if draft.verdict not in {"supported", "rejected", "inconclusive"}:
            raise ValueError("verdict is not allowed.")
        if draft.confidence not in {"high", "medium", "low"}:
            raise ValueError("confidence is not allowed.")
        context_json = _canonical_json(draft.context, "context")
        identity = _canonical_json(
            [self.project_key, hypothesis, json.loads(context_json)],
            "chain identity",
        )
        chain_id = draft.chain_id or (
            "chain_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        )
        if not _CHAIN_ID_PATTERN.fullmatch(chain_id):
            raise ValueError(
                "chain_id must match chain_<32 lowercase hex characters>."
            )
        if draft.superseded_by == chain_id:
            raise ValueError("Evidence Chain cannot supersede itself.")
        with open_project_memory_database(self.database_path) as connection:
            if draft.superseded_by:
                target = connection.execute(
                    """
                    SELECT project_key
                    FROM memory_evidence_chains
                    WHERE chain_id = ?
                    """,
                    (draft.superseded_by,),
                ).fetchone()
                if target is None or str(target[0]) != self.project_key:
                    raise ValueError(
                        "superseded_by must identify an Evidence Chain "
                        "in the fixed project."
                    )
            connection.execute(
                """
                INSERT INTO memory_evidence_chains(
                    chain_id, project_key, hypothesis, context_json,
                    verdict, confidence, created_at_utc, verified_at_utc,
                    superseded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chain_id,
                    self.project_key,
                    hypothesis,
                    context_json,
                    draft.verdict,
                    draft.confidence,
                    _utc(draft.created_at_utc, "created_at_utc"),
                    _utc(
                        draft.verified_at_utc,
                        "verified_at_utc",
                        allow_empty=True,
                    ),
                    draft.superseded_by or None,
                ),
            )
            connection.commit()
            row = connection.execute(
                "SELECT * FROM memory_evidence_chains WHERE chain_id = ?",
                (chain_id,),
            ).fetchone()
        assert row is not None
        return _row_chain(row)

    def get_evidence_chain(self, chain_id: str) -> MemoryEvidenceChain:
        with open_project_memory_database(
            self.database_path,
            readonly=True,
        ) as connection:
            row = connection.execute(
                """
                SELECT *
                FROM memory_evidence_chains
                WHERE project_key = ? AND chain_id = ?
                """,
                (self.project_key, chain_id),
            ).fetchone()
        if row is None:
            raise KeyError(f"Evidence Chain not found: {chain_id}")
        return _row_chain(row)

    def list_evidence_chains(
        self,
        *,
        limit: int = 50,
    ) -> tuple[MemoryEvidenceChain, ...]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or limit < 1
            or limit > MAX_L0_LIST_RESULTS
        ):
            raise ValueError(
                f"limit must be between 1 and {MAX_L0_LIST_RESULTS}."
            )
        with open_project_memory_database(
            self.database_path,
            readonly=True,
        ) as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM memory_evidence_chains
                WHERE project_key = ?
                ORDER BY created_at_utc DESC, chain_id DESC
                LIMIT ?
                """,
                (self.project_key, limit),
            ).fetchall()
        return tuple(_row_chain(row) for row in rows)

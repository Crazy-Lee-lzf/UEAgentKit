"""Deterministic offline L0 -> L1 Project Memory distillation.

This module implements the frozen M3 contract. It is intentionally pure
Python / SQLite, uses no LLM, no vector retrieval, no P4, no UE, and is only
invoked by the explicit offline ``ue-agent memory distill`` command. It never
runs on the MCP request path.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .database import get_metadata, open_database, utc_now_iso
from .memory_l0 import (
    MAX_L0_ASSET_PATHS,
    MemoryL0CaptureService,
    MemoryL0Event,
)
from .memory_tree import (
    KnowledgeNodeDraft,
    KnowledgeNodeType,
    create_knowledge_node,
    get_knowledge_node_by_path,
    normalize_knowledge_path,
    parent_knowledge_path,
)
from .project_memory import (
    MemoryArtifact,
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
    MemoryStatus,
    create_memory_record,
    get_memory_record,
    open_project_memory_database,
    record_provenance_digest,
)


DISTILL_DEFAULT_MAX_EVENTS = 100
DISTILL_HARD_MAX_EVENTS = 100
DISTILL_MAX_ARTIFACT_BYTES = 1024 * 1024
DISTILL_MAX_OUTPUTS_EVENT = 4
DISTILL_MAX_SOURCE_EVENTS = 16
DISTILL_MAX_DETAILS_BYTES = 8 * 1024

RULE_VERIFIED_WRITE = "l1.verified-write.v1"
RULE_WORKFLOW_REJECTION = "l1.workflow-rejection.v1"
RULE_POLICY_REJECTION = "l1.policy-rejection.v1"
RULE_SEMANTIC_DIFF = "l1.semantic-diff.v1"
RULE_SUPERSESSION = "l1.supersession.v1"
RULE_IMPACT_ANALYSIS = "l1.impact-analysis.v1"

RULE_IDS = frozenset(
    {
        RULE_VERIFIED_WRITE,
        RULE_WORKFLOW_REJECTION,
        RULE_POLICY_REJECTION,
        RULE_SEMANTIC_DIFF,
        RULE_SUPERSESSION,
        RULE_IMPACT_ANALYSIS,
    }
)

RECORD_TYPE_BY_RULE = {
    RULE_VERIFIED_WRITE: MemoryRecordType.PROJECT_FACT,
    RULE_WORKFLOW_REJECTION: MemoryRecordType.KNOWN_ISSUE,
    RULE_POLICY_REJECTION: MemoryRecordType.PROJECT_RULE,
    RULE_SEMANTIC_DIFF: MemoryRecordType.PROJECT_FACT,
    RULE_SUPERSESSION: MemoryRecordType.DECISION_RECORD,
    RULE_IMPACT_ANALYSIS: MemoryRecordType.PROJECT_FACT,
}

CONFIDENCE_BY_RULE = {
    RULE_VERIFIED_WRITE: 0.95,
    RULE_WORKFLOW_REJECTION: 0.8,
    RULE_POLICY_REJECTION: 0.95,
    RULE_SEMANTIC_DIFF: 0.9,
    RULE_SUPERSESSION: 0.9,
    RULE_IMPACT_ANALYSIS: 0.85,
}

# Event kinds that can be a terminal support/reject signal for an explicitly
# linked Evidence Chain. Support requires success/verified lifecycle evidence;
# reject requires failed/rejected/stale lifecycle evidence.
CHAIN_SUPPORT_EVENT_KINDS = frozenset(
    {
        "checkpoint",
        "checkpoint_set",
        "semantic_diff",
        "trust",
        "change_set",
        "recovery",
    }
)
CHAIN_REJECT_EVENT_KINDS = frozenset(
    {
        "checkpoint",
        "checkpoint_set",
        "semantic_diff",
        "trust",
        "change_set",
        "recovery",
        "workflow_rejection",
    }
)

_MEM_RECORD_ID = re.compile(r"^mem_[0-9a-f]{32}$")


class DistillationError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class DistillationBudget:
    max_events: int = DISTILL_DEFAULT_MAX_EVENTS
    max_artifact_bytes: int = DISTILL_MAX_ARTIFACT_BYTES
    max_outputs_event: int = DISTILL_MAX_OUTPUTS_EVENT
    max_source_events: int = DISTILL_MAX_SOURCE_EVENTS
    max_details_bytes: int = DISTILL_MAX_DETAILS_BYTES

    def validated(self) -> "DistillationBudget":
        if isinstance(self.max_events, bool) or not isinstance(self.max_events, int):
            raise ValueError("max_events must be an integer.")
        if self.max_events < 1 or self.max_events > DISTILL_HARD_MAX_EVENTS:
            raise ValueError(
                f"max_events must be between 1 and {DISTILL_HARD_MAX_EVENTS}."
            )
        if isinstance(self.max_artifact_bytes, bool) or not isinstance(self.max_artifact_bytes, int):
            raise ValueError("max_artifact_bytes must be an integer.")
        if self.max_artifact_bytes < 1 or self.max_artifact_bytes > DISTILL_MAX_ARTIFACT_BYTES:
            raise ValueError(
                f"max_artifact_bytes must be between 1 and {DISTILL_MAX_ARTIFACT_BYTES}."
            )
        if isinstance(self.max_outputs_event, bool) or not isinstance(self.max_outputs_event, int):
            raise ValueError("max_outputs_event must be an integer.")
        if self.max_outputs_event < 1 or self.max_outputs_event > DISTILL_MAX_OUTPUTS_EVENT:
            raise ValueError(
                f"max_outputs_event must be between 1 and {DISTILL_MAX_OUTPUTS_EVENT}."
            )
        if isinstance(self.max_source_events, bool) or not isinstance(self.max_source_events, int):
            raise ValueError("max_source_events must be an integer.")
        if self.max_source_events < 1 or self.max_source_events > DISTILL_MAX_SOURCE_EVENTS:
            raise ValueError(
                f"max_source_events must be between 1 and {DISTILL_MAX_SOURCE_EVENTS}."
            )
        if isinstance(self.max_details_bytes, bool) or not isinstance(self.max_details_bytes, int):
            raise ValueError("max_details_bytes must be an integer.")
        if self.max_details_bytes < 1 or self.max_details_bytes > DISTILL_MAX_DETAILS_BYTES:
            raise ValueError(
                f"max_details_bytes must be between 1 and {DISTILL_MAX_DETAILS_BYTES}."
            )
        return self


@dataclass(frozen=True)
class SourceBinding:
    kind: str
    key: str
    revision: str = ""
    revision_stable: bool = True
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DistillationRuleResult:
    rule_id: str
    record_type: MemoryRecordType
    subject_key: str
    title: str
    body: str
    confidence: float
    source_event_ids: tuple[str, ...]
    source_bindings: tuple[SourceBinding, ...]
    asset_paths: tuple[str, ...] = ()
    scopes: tuple[MemoryScope, ...] = ()
    revisions: tuple[MemoryRevision, ...] = ()
    artifacts: tuple[MemoryArtifact, ...] = ()
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DistillationRecordOutcome:
    produced: bool
    reused: bool
    record_id: str = ""
    deferred: bool = False
    reason_code: str = ""
    reason: str = ""
    error: str = ""


@dataclass(frozen=True)
class DistillationEventOutcome:
    event_id: str
    status: str
    produced_record_ids: tuple[str, ...]
    reused_record_ids: tuple[str, ...]
    reason_code: str = ""
    reason: str = ""


@dataclass(frozen=True)
class DistillationResult:
    selected_count: int
    evaluated_count: int
    distilled_count: int
    produced_record_count: int
    reused_record_count: int
    deferred_count: int
    failed_count: int
    produced_record_ids: tuple[str, ...]
    reused_record_ids: tuple[str, ...]
    deferred: tuple[dict[str, str], ...]
    failed: tuple[dict[str, str], ...]
    elapsed_ms: float
    pending_after: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_memory_distill",
            "projectKey": "",
            "selectedCount": self.selected_count,
            "evaluatedCount": self.evaluated_count,
            "distilledCount": self.distilled_count,
            "producedRecordCount": self.produced_record_count,
            "reusedRecordCount": self.reused_record_count,
            "deferredCount": self.deferred_count,
            "failedCount": self.failed_count,
            "producedRecordIds": list(self.produced_record_ids),
            "reusedRecordIds": list(self.reused_record_ids),
            "deferred": list(self.deferred),
            "failed": list(self.failed),
            "elapsedMs": round(self.elapsed_ms, 3),
            "pendingAfter": self.pending_after,
        }


def _canonical_json(value: Any, *, field_name: str = "value") -> str:
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


def _bounded_text(value: Any, field_name: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string.")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{field_name} must be a non-empty string.")
    if len(normalized) > maximum:
        raise ValueError(f"{field_name} exceeds the {maximum} character bound.")
    return normalized


def _normalize_path(path: Path) -> Path:
    return path.expanduser().resolve()


def _safe_json_bytes(value: dict[str, Any], *, maximum: int) -> str:
    text = _canonical_json(value, field_name="details")
    if len(text.encode("utf-8")) > maximum:
        raise DistillationError(
            "distill-details-too-large",
            f"distilled details exceed the {maximum} byte bound.",
        )
    return text


def _read_artifact(event: MemoryL0Event, artifact_root: Path, budget: DistillationBudget) -> dict[str, Any]:
    """Verify and decode an artifact-backed L0 event.

    The artifact must be a relative reference, resolve under the fixed root,
    exist, be at most ``budget.max_artifact_bytes``, and hash to the exact
    digest stored in the event.
    """
    artifact_ref = event.artifact_ref
    if not artifact_ref:
        raise DistillationError(
            "distill-artifact-required",
            "the rule requires an artifact but the L0 event has none.",
        )
    if Path(artifact_ref).is_absolute() or "\\" in artifact_ref:
        raise DistillationError(
            "distill-artifact-path-invalid",
            "artifact_ref must be a relative POSIX path.",
        )
    candidate = (artifact_root / artifact_ref).resolve()
    try:
        candidate.relative_to(artifact_root)
    except ValueError as exc:
        raise DistillationError(
            "distill-artifact-escape",
            "artifact_ref resolved outside the fixed artifact root.",
        ) from exc
    if not candidate.is_file():
        raise DistillationError(
            "distill-artifact-missing",
            "referenced artifact does not exist.",
        )
    size = candidate.stat().st_size
    if size > budget.max_artifact_bytes:
        raise DistillationError(
            "distill-artifact-oversized",
            f"referenced artifact exceeds the {budget.max_artifact_bytes} byte bound.",
        )
    digest = hashlib.sha256()
    with candidate.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != event.artifact_digest:
        raise DistillationError(
            "distill-artifact-digest-mismatch",
            "referenced artifact digest does not match the L0 event.",
        )
    try:
        decoded = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DistillationError(
            "distill-artifact-undecodable",
            "referenced artifact is not decodable JSON.",
        ) from exc
    if not isinstance(decoded, dict):
        raise DistillationError(
            "distill-artifact-not-object",
            "artifact-backed distillation requires a JSON object.",
        )
    return decoded


def _asset_paths(event: MemoryL0Event, payload: dict[str, Any]) -> tuple[str, ...]:
    paths = tuple(dict.fromkeys([*event.asset_paths, *payload.get("assetPaths", [])]))
    return tuple(path for path in paths if isinstance(path, str) and path.startswith("/Game/"))[:MAX_L0_ASSET_PATHS]


def _asset_revisions_from_payload(payload: dict[str, Any]) -> tuple[MemoryRevision, ...]:
    raw = payload.get("assetRevisions")
    if not isinstance(raw, list):
        return ()
    revisions: list[MemoryRevision] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        asset_path = str(item.get("assetPath", ""))
        revision = str(item.get("revision", ""))
        stable = bool(item.get("revisionStable", True))
        if asset_path.startswith("/Game/") and revision.startswith("sha256:"):
            revisions.append(MemoryRevision(asset_path, revision, stable))
    return tuple(sorted(revisions, key=lambda item: item.asset_path.casefold()))


def _revision_value_for_path(payload: dict[str, Any], asset_path: str) -> str:
    """Find one exact revision for an asset from durable Writer payload shapes."""
    if str(payload.get("assetPath", "")) == asset_path:
        for key in (
            "afterDiskRevision",
            "afterRevision",
            "artifactRevision",
            "strongArtifactRevision",
            "strongVerificationRevision",
            "after_disk_revision",
            "after_revision",
        ):
            value = str(payload.get(key, ""))
            if value.startswith("sha256:"):
                return value
    for container_name in ("children", "childResults"):
        container = payload.get(container_name)
        if isinstance(container, list):
            for item in container:
                if not isinstance(item, dict):
                    continue
                if str(item.get("assetPath", "")) != asset_path:
                    continue
                for key in ("afterRevision", "strongVerificationRevision", "artifactRevision"):
                    value = str(item.get(key, ""))
                    if value.startswith("sha256:"):
                        return value
    verification = payload.get("verification")
    if isinstance(verification, dict):
        child_results = verification.get("childResults")
        if isinstance(child_results, list):
            for item in child_results:
                if not isinstance(item, dict):
                    continue
                if str(item.get("assetPath", "")) != asset_path:
                    continue
                for key in ("afterRevision", "strongVerificationRevision"):
                    value = str(item.get(key, ""))
                    if value.startswith("sha256:"):
                        return value
    return ""


def _asset_revisions_for_event(payload: dict[str, Any], event: MemoryL0Event) -> tuple[MemoryRevision, ...]:
    revisions = list(_asset_revisions_from_payload(payload))
    if revisions:
        return tuple(revisions)
    for asset_path in event.asset_paths:
        revision = _revision_value_for_path(payload, asset_path)
        if revision:
            revisions.append(MemoryRevision(asset_path, revision, True))
    return tuple(sorted(revisions, key=lambda item: item.asset_path.casefold()))


def _scopes_for_asset_paths(paths: Sequence[str]) -> tuple[MemoryScope, ...]:
    return tuple(MemoryScope(MemoryScopeType.ASSET, path) for path in dict.fromkeys(paths))


def _knowledge_path_for_assets(paths: Sequence[str]) -> str:
    asset_paths = tuple(path for path in paths if path.startswith("/Game/"))
    if not asset_paths:
        return "/project"
    segments = asset_paths[0].split("/")[1:]
    if len(segments) < 2:
        return "/project"
    directory_segments = [segment for segment in segments[:-1] if segment.casefold() != "game"]
    if not directory_segments:
        return "/project/content"
    try:
        return normalize_knowledge_path(
            "/project/content/" + "/".join(directory_segments).casefold()
        )
    except ValueError:
        return "/project"


def _deterministic_node_id(*, project_key: str, path: str) -> str:
    """Deterministic ``kn_<32 hex>`` identity for an automatically placed node.

    ``knowledge_nodes`` is UNIQUE(project_key, path), so the normalized path
    plus the project key is the complete node identity. Hashing it makes M3
    placement reproducible across runs and machines instead of relying on the
    ``kn_<uuid4>`` default.
    """
    identity = _canonical_json(
        {"projectKey": project_key, "knowledgePath": normalize_knowledge_path(path)},
        field_name="knowledge node identity",
    )
    return "kn_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _ensure_knowledge_path(connection: sqlite3.Connection, *, project_key: str, path: str) -> str:
    """Create /project and all ancestors parent-first, then return the node id."""
    normalized_path = normalize_knowledge_path(path)
    if normalized_path == "/project":
        try:
            return get_knowledge_node_by_path(
                connection,
                project_key=project_key,
                path="/project",
            ).node_id
        except KeyError:
            node = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=project_key,
                    path="/project",
                    node_type=KnowledgeNodeType.PROJECT,
                    title=project_key,
                    summary="Automatic distilled project root.",
                    node_id=_deterministic_node_id(project_key=project_key, path="/project"),
                ),
            )
            return node.node_id
    parent_id = _ensure_knowledge_path(
        connection,
        project_key=project_key,
        path=parent_knowledge_path(normalized_path),
    )
    try:
        node = get_knowledge_node_by_path(
            connection,
            project_key=project_key,
            path=normalized_path,
        )
        return node.node_id
    except KeyError:
        node = create_knowledge_node(
            connection,
            KnowledgeNodeDraft(
                project_key=project_key,
                path=normalized_path,
                node_type=KnowledgeNodeType.FEATURE,
                title=normalized_path.rsplit("/", 1)[-1],
                summary="Automatic distilled knowledge node.",
                parent_node_id=parent_id,
                node_id=_deterministic_node_id(project_key=project_key, path=normalized_path),
            ),
        )
        return node.node_id


def _deterministic_record_id(
    *,
    project_key: str,
    rule_id: str,
    source_event_ids: Sequence[str],
    output_index: int,
) -> str:
    identity = _canonical_json(
        {
            "projectKey": project_key,
            "ruleId": rule_id,
            "sourceEventIds": list(source_event_ids),
            "outputIndex": output_index,
        },
        field_name="L1 identity",
    )
    return "mem_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]


def _values_match(left: Any, right: Any) -> bool:
    """Exact JSON-value equality used to corroborate supersession provenance."""
    if left is None or right is None:
        return False
    try:
        return _canonical_json(left, field_name="supersession value") == _canonical_json(
            right, field_name="supersession value"
        )
    except ValueError:
        return left == right


def _verify_existing_record(
    connection: sqlite3.Connection,
    record_id: str,
    *,
    project_key: str,
    content_sha256: str,
    evidence_sha256: str,
) -> dict[str, Any] | None:
    """Return the existing row, or fail closed when it is not the M3 output.

    A deterministic M3 record id may only be reused when the stored record is
    exactly what the current rule would have produced: same project, same
    ``distill:`` provenance, and identical canonical content/evidence digests.
    A record that merely carries the same id is a collision or tamper and must
    never be treated as the M3 result or overwritten.
    """
    row = connection.execute(
        """
        SELECT record_id, project_key, source_ref, content_sha256, evidence_sha256
        FROM memory_records
        WHERE record_id = ?
        """,
        (record_id,),
    ).fetchone()
    if row is None:
        return None
    existing = {
        "record_id": str(row[0]),
        "project_key": str(row[1]),
        "source_ref": str(row[2]),
        "content_sha256": str(row[3]),
        "evidence_sha256": str(row[4]),
    }
    if existing["project_key"] != project_key or not existing["source_ref"].startswith("distill:"):
        raise DistillationError(
            "distill-record-collision",
            "deterministic L1 id collides with an existing record from a different source.",
        )
    if (
        existing["content_sha256"] != content_sha256
        or existing["evidence_sha256"] != evidence_sha256
    ):
        raise DistillationError(
            "distill-record-content-mismatch",
            "existing deterministic L1 record does not match the expected rule output.",
            details={
                "recordId": record_id,
                "expectedContentSha256": content_sha256,
                "actualContentSha256": existing["content_sha256"],
                "expectedEvidenceSha256": evidence_sha256,
                "actualEvidenceSha256": existing["evidence_sha256"],
            },
        )
    return existing


class MemoryDistillationService:
    """Offline deterministic distiller for M2 L0 events.

    The service is fixed to one project, one Memory database, one artifact
    root, one index database, and one Policy path. No event supplies an
    arbitrary caller path.
    """

    def __init__(
        self,
        *,
        memory_database: Path,
        project_key: str,
        artifact_root: Path,
        index_database: Path,
        policy_path: Path,
        budget: DistillationBudget = DistillationBudget(),
    ) -> None:
        self.memory_database = _normalize_path(memory_database)
        self.project_key = _bounded_text(project_key, "project_key", 512)
        self.artifact_root = _normalize_path(artifact_root)
        self.index_database = _normalize_path(index_database)
        self.policy_path = _normalize_path(policy_path)
        self.budget = budget.validated()
        self._l0_service = MemoryL0CaptureService(
            database_path=self.memory_database,
            project_key=self.project_key,
            artifact_root=self.artifact_root,
        )

    def _select_pending_events(self, connection: sqlite3.Connection, *, max_events: int) -> tuple[MemoryL0Event, ...]:
        rows = connection.execute(
            """
            SELECT *
            FROM memory_l0_events
            WHERE project_key = ? AND distilled = 0
            ORDER BY occurred_at_utc ASC, event_id ASC
            LIMIT ?
            """,
            (self.project_key, max_events),
        ).fetchall()
        return tuple(_row_event_from_sqlite(row) for row in rows)

    def _mark_distilled(self, connection: sqlite3.Connection, event_id: str) -> None:
        connection.execute(
            "UPDATE memory_l0_events SET distilled = 1 WHERE project_key = ? AND event_id = ?",
            (self.project_key, event_id),
        )

    def _pending_count(self, connection: sqlite3.Connection) -> int:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM memory_l0_events WHERE project_key = ? AND distilled = 0",
                (self.project_key,),
            ).fetchone()[0]
        )

    def distill(
        self,
        *,
        max_events: int = DISTILL_DEFAULT_MAX_EVENTS,
        source_validation: bool = True,
    ) -> DistillationResult:
        if isinstance(max_events, bool) or not isinstance(max_events, int):
            raise ValueError("max_events must be an integer.")
        if max_events < 1 or max_events > DISTILL_HARD_MAX_EVENTS:
            raise ValueError(
                f"max_events must be between 1 and {DISTILL_HARD_MAX_EVENTS}."
            )
        started = utc_now_iso()
        started_perf = __import__("time").perf_counter()
        produced_ids: list[str] = []
        reused_ids: list[str] = []
        deferred_entries: list[dict[str, str]] = []
        failed_entries: list[dict[str, str]] = []
        evaluated_count = 0
        distilled_count = 0
        produced_count = 0
        reused_count = 0

        with open_project_memory_database(self.memory_database) as connection:
            events = self._select_pending_events(connection, max_events=max_events)
            selected_count = len(events)
            for event in events:
                outcome = self._evaluate_event(connection, event, started=started)
                if outcome.status == "failed":
                    failed_entries.append(
                        {
                            "eventId": event.event_id,
                            "reasonCode": outcome.reason_code or "distill-failed",
                            "reason": outcome.reason,
                        }
                    )
                    connection.rollback()
                    continue
                evaluated_count += 1
                if outcome.status == "deferred":
                    deferred_entries.append(
                        {
                            "eventId": event.event_id,
                            "reasonCode": outcome.reason_code,
                            "reason": outcome.reason,
                        }
                    )
                    connection.rollback()
                    continue
                # status == distilled (possibly with produced/reused records)
                distilled_count += 1
                produced_count += len(outcome.produced_record_ids)
                reused_count += len(outcome.reused_record_ids)
                produced_ids.extend(outcome.produced_record_ids)
                reused_ids.extend(outcome.reused_record_ids)
                connection.commit()
            pending_after = self._pending_count(connection)

        elapsed_ms = (__import__("time").perf_counter() - started_perf) * 1000.0
        return DistillationResult(
            selected_count=selected_count,
            evaluated_count=evaluated_count,
            distilled_count=distilled_count,
            produced_record_count=produced_count,
            reused_record_count=reused_count,
            deferred_count=len(deferred_entries),
            failed_count=len(failed_entries),
            produced_record_ids=tuple(produced_ids),
            reused_record_ids=tuple(reused_ids),
            deferred=tuple(deferred_entries),
            failed=tuple(failed_entries),
            elapsed_ms=elapsed_ms,
            pending_after=pending_after,
        )

    def _evaluate_event(
        self,
        connection: sqlite3.Connection,
        event: MemoryL0Event,
        *,
        started: str,
    ) -> DistillationEventOutcome:
        if event.project_key != self.project_key:
            return DistillationEventOutcome(
                event.event_id,
                "failed",
                (),
                (),
                reason_code="distill-project-mismatch",
                reason="L0 event does not belong to the fixed project.",
            )
        try:
            rule_id, payload, source_event_ids = self._classify_event(connection, event)
        except DistillationError as exc:
            if exc.code in {
                "distill-artifact-missing",
                "distill-artifact-oversized",
                "distill-artifact-digest-mismatch",
                "distill-artifact-undecodable",
                "distill-artifact-not-object",
            }:
                return DistillationEventOutcome(
                    event.event_id,
                    "deferred",
                    (),
                    (),
                    reason_code=exc.code,
                    reason=str(exc),
                )
            return DistillationEventOutcome(
                event.event_id,
                "failed",
                (),
                (),
                reason_code=exc.code,
                reason=str(exc),
            )

        if rule_id is None:
            # Deterministically evaluated: no L1 output for this rule set.
            self._mark_distilled(connection, event.event_id)
            connection.commit()
            return DistillationEventOutcome(event.event_id, "distilled", (), ())

        try:
            rule_results = self._apply_rule(
                connection,
                event=event,
                rule_id=rule_id,
                payload=payload,
                source_event_ids=source_event_ids,
            )
        except DistillationError as exc:
            return DistillationEventOutcome(
                event.event_id,
                "deferred",
                (),
                (),
                reason_code=exc.code,
                reason=str(exc),
            )
        if not rule_results:
            # Rule produced no outputs (e.g. no valid supersession operations).
            # Deterministically evaluated with no L1 output.
            self._mark_distilled(connection, event.event_id)
            connection.commit()
            return DistillationEventOutcome(event.event_id, "distilled", (), ())
        record_outcomes: list[DistillationRecordOutcome] = []
        for index, result in enumerate(rule_results):
            if index >= self.budget.max_outputs_event:
                break
            try:
                record_outcomes.append(
                    self._upsert_record(
                        connection,
                        result=result,
                        event=event,
                        rule_id=rule_id,
                        output_index=index,
                        started=started,
                    )
                )
            except DistillationError as exc:
                return DistillationEventOutcome(
                    event.event_id,
                    "failed",
                    (),
                    (),
                    reason_code=exc.code,
                    reason=str(exc),
                )
            except (ValueError, KeyError, sqlite3.Error) as exc:
                return DistillationEventOutcome(
                    event.event_id,
                    "failed",
                    (),
                    (),
                    reason_code="distill-record-failed",
                    reason=str(exc),
                )
        produced = [outcome for outcome in record_outcomes if outcome.produced and not outcome.reused]
        reused = [outcome for outcome in record_outcomes if outcome.reused]
        if any(outcome.deferred for outcome in record_outcomes):
            return DistillationEventOutcome(
                event.event_id,
                "deferred",
                (),
                (),
                reason_code=record_outcomes[0].reason_code,
                reason=record_outcomes[0].reason,
            )
        self._mark_distilled(connection, event.event_id)
        connection.commit()
        return DistillationEventOutcome(
            event.event_id,
            "distilled",
            tuple(outcome.record_id for outcome in produced),
            tuple(outcome.record_id for outcome in reused),
        )

    def _classify_event(
        self,
        connection: sqlite3.Connection,
        event: MemoryL0Event,
    ) -> tuple[str | None, dict[str, Any], tuple[str, ...]]:
        kind = event.event_kind
        if kind == "workflow_rejection":
            details = dict(event.details)
            policy_digest = details.get("policyDigest", "")
            if policy_digest:
                return RULE_POLICY_REJECTION, {"policyDigest": policy_digest}, (event.event_id,)
            return RULE_WORKFLOW_REJECTION, {}, (event.event_id,)
        if kind == "change_set" and event.outcome in {"success", "superseded"}:
            payload = self._artifact_payload(event)
            operations = payload.get("operations")
            if isinstance(operations, list) and any(
                isinstance(item, dict)
                and item.get("status") == "superseded"
                and (item.get("afterValue") is not None or item.get("newValue") is not None)
                for item in operations
            ):
                return RULE_SUPERSESSION, payload, (event.event_id,)
            if not (
                payload.get("assetRevisions")
                or any(
                    payload.get(key)
                    for key in (
                        "afterDiskRevision",
                        "afterRevision",
                        "artifactRevision",
                        "strongArtifactRevision",
                        "strongVerificationRevision",
                    )
                )
            ):
                # A bare Change Set is not a persisted-fact source.
                return None, {}, (event.event_id,)
            return RULE_VERIFIED_WRITE, payload, (event.event_id,)
        if kind in {"checkpoint", "checkpoint_set", "trust", "recovery"}:
            if event.outcome not in {"success", "recovered"}:
                # R2 covers durable failed/partial/rejected boundaries.
                if event.outcome in {"failed", "partial", "rejected"}:
                    return RULE_WORKFLOW_REJECTION, {}, (event.event_id,)
                # no-op/superseded are evaluated with no output.
                return None, {}, (event.event_id,)
            payload = self._artifact_payload(event)
            return RULE_VERIFIED_WRITE, payload, (event.event_id,)
        if kind == "live_write":
            # A resident live write is never a persisted-fact source. The
            # superseded live-write journal is the durable evidence that R5
            # must bind, not an R5 input by itself: R5 fires from the Change
            # Set and then corroborates against this journal.
            return None, {}, (event.event_id,)
        if kind == "semantic_diff":
            if event.outcome == "failed":
                return RULE_WORKFLOW_REJECTION, {}, (event.event_id,)
            if event.outcome != "success":
                return None, {}, (event.event_id,)
            payload = self._artifact_payload(event)
            return RULE_SEMANTIC_DIFF, payload, (event.event_id,)
        return None, {}, (event.event_id,)

    def _artifact_payload(self, event: MemoryL0Event) -> dict[str, Any]:
        return _read_artifact(event, self.artifact_root, self.budget)

    def _source_events(
        self,
        connection: sqlite3.Connection,
        *,
        source_event_ids: Sequence[str],
    ) -> tuple[MemoryL0Event, ...]:
        if not source_event_ids:
            return ()
        placeholders = ",".join("?" for _ in source_event_ids)
        rows = connection.execute(
            f"""
            SELECT *
            FROM memory_l0_events
            WHERE project_key = ? AND event_id IN ({placeholders})
            ORDER BY occurred_at_utc ASC, event_id ASC
            """,
            (self.project_key, *source_event_ids),
        ).fetchall()
        return tuple(_row_event_from_sqlite(row) for row in rows)

    def _apply_rule(
        self,
        connection: sqlite3.Connection,
        *,
        event: MemoryL0Event,
        rule_id: str,
        payload: dict[str, Any],
        source_event_ids: tuple[str, ...],
    ) -> tuple[DistillationRuleResult, ...]:
        if rule_id == RULE_VERIFIED_WRITE:
            return (self._rule_verified_write(event, payload, source_event_ids),)
        if rule_id == RULE_WORKFLOW_REJECTION:
            return (self._rule_workflow_rejection(event, source_event_ids),)
        if rule_id == RULE_POLICY_REJECTION:
            return (self._rule_policy_rejection(event, payload, source_event_ids),)
        if rule_id == RULE_SEMANTIC_DIFF:
            return (self._rule_semantic_diff(event, payload, source_event_ids),)
        if rule_id == RULE_SUPERSESSION:
            return self._rule_supersession(connection, event, payload, source_event_ids)
        if rule_id == RULE_IMPACT_ANALYSIS:
            return (self._rule_impact_analysis(event, payload, source_event_ids),)
        raise DistillationError("distill-rule-unknown", f"unknown rule id {rule_id}")

    def _rule_verified_write(
        self,
        event: MemoryL0Event,
        payload: dict[str, Any],
        source_event_ids: tuple[str, ...],
    ) -> DistillationRuleResult:
        revisions = _asset_revisions_for_event(payload, event)
        if not revisions:
            raise DistillationError(
                "distill-verified-write-no-revision",
                "verified persisted write has no exact asset Revision binding.",
            )
        paths = _asset_paths(event, payload)
        if not paths:
            raise DistillationError(
                "distill-verified-write-no-asset",
                "verified persisted write has no /Game asset path.",
            )
        primary = paths[0]
        primary_revision = next(
            (revision.revision for revision in revisions if revision.asset_path == primary),
            "",
        )
        if not primary_revision:
            raise DistillationError(
                "distill-verified-write-revision-missing",
                "the primary asset has no exact persisted Revision binding.",
            )
        bindings = tuple(
            SourceBinding(
                kind="assetRevision",
                key=revision.asset_path,
                revision=revision.revision,
                revision_stable=revision.revision_stable,
            )
            for revision in revisions
        )
        body = (
            f"Verified persisted write for {primary} at Revision {primary_revision}. "
            "The durable checkpoint/Change Set evidence proves the persisted asset revision."
        )
        details = {
            "distillation": {
                "ruleId": RULE_VERIFIED_WRITE,
                "sourceEventIds": list(source_event_ids),
                "sourceBindings": [_binding_payload(item) for item in bindings],
                "primaryAssetPath": primary,
                "primaryRevision": primary_revision,
            }
        }
        return DistillationRuleResult(
            rule_id=RULE_VERIFIED_WRITE,
            record_type=MemoryRecordType.PROJECT_FACT,
            subject_key=f"verified-write:{primary}",
            title=f"Verified persisted write: {primary}",
            body=body,
            confidence=CONFIDENCE_BY_RULE[RULE_VERIFIED_WRITE],
            source_event_ids=source_event_ids,
            source_bindings=bindings,
            asset_paths=paths,
            scopes=_scopes_for_asset_paths(paths),
            revisions=revisions,
            artifacts=(MemoryArtifact("l0-source", event.artifact_ref),),
            details=details,
        )

    def _rule_workflow_rejection(
        self,
        event: MemoryL0Event,
        source_event_ids: tuple[str, ...],
    ) -> DistillationRuleResult:
        details = dict(event.details)
        operation = _bounded_text(details.get("operation", ""), "operation", 128, required=False)
        error_code = _bounded_text(details.get("errorCode", ""), "errorCode", 128, required=False)
        target_identity = _bounded_text(
            details.get("targetIdentity", ""),
            "targetIdentity",
            512,
            required=False,
        )
        paths = tuple(path for path in event.asset_paths if path.startswith("/Game/"))
        subject = error_code or "workflow-rejection"
        title_parts = [part for part in (operation, error_code, target_identity) if part]
        title = f"Rejected/failed workflow: {' / '.join(title_parts)}" if title_parts else "Rejected/failed workflow"
        body_parts = [
            f"operation={operation}" if operation else "",
            f"errorCode={error_code}" if error_code else "",
            f"assetPaths={', '.join(paths)}" if paths else "",
            f"lifecycle={event.lifecycle_state}",
        ]
        body = "Rejected/failed workflow observation. " + "; ".join(part for part in body_parts if part)
        bindings = tuple(
            SourceBinding(kind="assetRevision", key=path, revision="", revision_stable=False)
            for path in paths
        )
        details_payload = {
            "distillation": {
                "ruleId": RULE_WORKFLOW_REJECTION,
                "sourceEventIds": list(source_event_ids),
                "sourceBindings": [_binding_payload(item) for item in bindings],
                "operation": operation,
                "errorCode": error_code,
                "lifecycleState": event.lifecycle_state,
            }
        }
        return DistillationRuleResult(
            rule_id=RULE_WORKFLOW_REJECTION,
            record_type=MemoryRecordType.KNOWN_ISSUE,
            subject_key=f"rejection:{subject}",
            title=title,
            body=body,
            confidence=CONFIDENCE_BY_RULE[RULE_WORKFLOW_REJECTION],
            source_event_ids=source_event_ids,
            source_bindings=bindings,
            asset_paths=paths,
            scopes=_scopes_for_asset_paths(paths),
            details=details_payload,
        )

    def _rule_policy_rejection(
        self,
        event: MemoryL0Event,
        payload: dict[str, Any],
        source_event_ids: tuple[str, ...],
    ) -> DistillationRuleResult:
        digest = str(payload.get("policyDigest", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise DistillationError(
                "distill-policy-digest-invalid",
                "policy rejection digest must be one lowercase SHA-256.",
            )
        if (
            not self.policy_path.is_file()
            or hashlib.sha256(self.policy_path.read_bytes()).hexdigest() != digest
        ):
            # Without a matching fixed Policy, the rule is not safely
            # source-validated and must not be promoted. It becomes a
            # knownIssue instead so the observed rejection is preserved
            # without fabricating a projectRule.
            return self._rule_workflow_rejection(event, source_event_ids)

        details = dict(event.details)
        operation = _bounded_text(details.get("operation", ""), "operation", 128, required=False)
        error_code = _bounded_text(details.get("errorCode", ""), "errorCode", 128, required=False)
        paths = tuple(path for path in event.asset_paths if path.startswith("/Game/"))
        subject = f"policy:{operation}" if operation else "policy:rejection"
        title = f"Project Write Policy rule: {operation or 'rejection'}"
        body_parts = [
            f"operation={operation}" if operation else "",
            f"errorCode={error_code}" if error_code else "",
            f"assetPaths={', '.join(paths)}" if paths else "",
            "policyDigest=" + digest,
        ]
        body = "Policy rejection distilled as project rule. " + "; ".join(part for part in body_parts if part)
        binding = SourceBinding(
            kind="policyDigest",
            key="project-write-policy",
            revision="sha256:" + digest,
            revision_stable=True,
        )
        details_payload = {
            "distillation": {
                "ruleId": RULE_POLICY_REJECTION,
                "sourceEventIds": list(source_event_ids),
                "sourceBindings": [_binding_payload(binding)],
                "policyDigest": "sha256:" + digest,
                "operation": operation,
                "errorCode": error_code,
            }
        }
        return DistillationRuleResult(
            rule_id=RULE_POLICY_REJECTION,
            record_type=MemoryRecordType.PROJECT_RULE,
            subject_key=subject,
            title=title,
            body=body,
            confidence=CONFIDENCE_BY_RULE[RULE_POLICY_REJECTION],
            source_event_ids=source_event_ids,
            source_bindings=(binding,),
            asset_paths=paths,
            scopes=_scopes_for_asset_paths(paths),
            details=details_payload,
        )

    def _rule_semantic_diff(
        self,
        event: MemoryL0Event,
        payload: dict[str, Any],
        source_event_ids: tuple[str, ...],
    ) -> DistillationRuleResult:
        summary = payload.get("summary", {})
        if not isinstance(summary, dict):
            raise DistillationError(
                "distill-semantic-diff-summary-invalid",
                "semantic diff artifact has no summary object.",
            )
        missing = int(summary.get("missingExpectedCount", 0) or 0)
        unexpected = int(summary.get("unexpectedCount", 0) or 0)
        analysis_gap = int(summary.get("analysisGapCount", 0) or 0)
        if missing or unexpected or analysis_gap:
            raise DistillationError(
                "distill-semantic-diff-not-verified",
                "semantic diff is not a clean verified artifact; no positive fact is derived.",
            )
        paths = _asset_paths(event, payload)
        if not paths:
            raise DistillationError(
                "distill-semantic-diff-no-asset",
                "verified semantic diff has no /Game asset path.",
            )
        primary = paths[0]
        revisions = _asset_revisions_for_event(payload, event)
        primary_revision = next(
            (revision.revision for revision in revisions if revision.asset_path == primary),
            "",
        )
        bindings = tuple(
            SourceBinding(
                kind="assetRevision",
                key=revision.asset_path,
                revision=revision.revision,
                revision_stable=revision.revision_stable,
            )
            for revision in revisions
        )
        body = (
            f"Verified Semantic Diff for {primary} with no missing expected, unexpected, or analysis-gap evidence."
            + (f" Revision {primary_revision}." if primary_revision else "")
        )
        details = {
            "distillation": {
                "ruleId": RULE_SEMANTIC_DIFF,
                "sourceEventIds": list(source_event_ids),
                "sourceBindings": [_binding_payload(item) for item in bindings],
                "primaryAssetPath": primary,
                "primaryRevision": primary_revision,
            }
        }
        return DistillationRuleResult(
            rule_id=RULE_SEMANTIC_DIFF,
            record_type=MemoryRecordType.PROJECT_FACT,
            subject_key=f"semantic-diff:{primary}",
            title=f"Verified semantic diff: {primary}",
            body=body,
            confidence=CONFIDENCE_BY_RULE[RULE_SEMANTIC_DIFF],
            source_event_ids=source_event_ids,
            source_bindings=bindings,
            asset_paths=paths,
            scopes=_scopes_for_asset_paths(paths),
            revisions=revisions,
            artifacts=(MemoryArtifact("l0-source", event.artifact_ref),),
            details=details,
        )

    def _live_write_supersession_evidence(
        self,
        connection: sqlite3.Connection,
        *,
        change_set_id: str,
        asset_path: str,
        stable_key: str,
    ) -> dict[str, Any] | None:
        """Find the durable live-write journal proving one supersession.

        R5 must not trust the Change Set serialization alone. The old/new value
        chain is only accepted when a durable ``live_write`` artifact for the
        same Change Set, asset, and stable target key carries the same values.
        """
        if not change_set_id:
            return None
        rows = connection.execute(
            """
            SELECT *
            FROM memory_l0_events
            WHERE project_key = ?
              AND event_kind = 'live_write'
              AND change_set_id = ?
            ORDER BY occurred_at_utc ASC, event_id ASC
            """,
            (self.project_key, change_set_id),
        ).fetchall()
        for row in rows:
            candidate = _row_event_from_sqlite(row)
            try:
                payload = _read_artifact(candidate, self.artifact_root, self.budget)
            except DistillationError:
                continue
            if str(payload.get("assetPath", "")) != asset_path:
                continue
            if str(payload.get("stableTargetKey", "")) != stable_key:
                continue
            after_value = payload.get("afterValue", payload.get("newValue"))
            if after_value is None:
                continue
            return {
                "eventId": candidate.event_id,
                "beforeValue": payload.get("beforeValue", payload.get("expectedValue")),
                "afterValue": after_value,
            }
        return None

    def _rule_supersession(
        self,
        connection: sqlite3.Connection,
        event: MemoryL0Event,
        payload: dict[str, Any],
        source_event_ids: tuple[str, ...],
    ) -> tuple[DistillationRuleResult, ...]:
        change_set_id = event.change_set_id
        if not change_set_id:
            return ()
        if "operations" in payload:
            change_set_payload = payload
        else:
            change_set_payload = self._artifact_payload(event)
        operations = change_set_payload.get("operations")
        if not isinstance(operations, list):
            return ()
        superseded_ops = [
            item
            for item in operations
            if isinstance(item, dict) and item.get("status") == "superseded"
        ]
        if not superseded_ops:
            return ()
        results: list[DistillationRuleResult] = []
        for index, op in enumerate(superseded_ops[: self.budget.max_outputs_event]):
            asset_path = str(op.get("assetPath", ""))
            operation_name = str(op.get("operation", ""))
            if not asset_path.startswith("/Game/") or not operation_name:
                continue
            target = op.get("target")
            if not isinstance(target, dict):
                continue
            stable_key = str(op.get("stableTargetKey", ""))
            if not stable_key:
                continue
            old_value = op.get("oldValue", op.get("expectedValue"))
            new_value = op.get("newValue", op.get("afterValue"))
            if new_value is None:
                continue
            if "afterValue" not in op and "newValue" not in op:
                continue
            if "oldValue" not in op and "beforeValue" not in op and "expectedValue" not in op:
                continue
            # R5 provenance gate: the durable live-write journal must prove the
            # exact same old -> new value chain. Change Set values alone are
            # never enough to emit a decisionRecord.
            evidence = self._live_write_supersession_evidence(
                connection,
                change_set_id=change_set_id,
                asset_path=asset_path,
                stable_key=stable_key,
            )
            if evidence is None:
                continue
            if not _values_match(evidence["beforeValue"], old_value) or not _values_match(
                evidence["afterValue"], new_value
            ):
                continue
            provenance_event_ids = tuple(sorted({*source_event_ids, evidence["eventId"]}))
            binding = SourceBinding(
                kind="assetRevision",
                key=asset_path,
                revision="",
                revision_stable=False,
                details={"target": target, "stableTargetKey": stable_key},
            )
            bindings = (binding,)
            details = {
                "distillation": {
                    "ruleId": RULE_SUPERSESSION,
                    "sourceEventIds": list(provenance_event_ids),
                    "sourceBindings": [_binding_payload(binding) for binding in bindings],
                    "assetPath": asset_path,
                    "operation": operation_name,
                    "stableTargetKey": stable_key,
                    "oldValue": old_value,
                    "newValue": new_value,
                    "liveWriteEvidenceEventId": evidence["eventId"],
                }
            }
            results.append(
                DistillationRuleResult(
                    rule_id=RULE_SUPERSESSION,
                    record_type=MemoryRecordType.DECISION_RECORD,
                    subject_key=f"supersession:{asset_path}:{stable_key}",
                    title=f"Superseded live write: {asset_path}",
                    body=(
                        f"Superseded operation {operation_name} on {asset_path} was replaced "
                        "with a later exact value proven by the durable Change Set and the "
                        "matching live-write journal."
                    ),
                    confidence=CONFIDENCE_BY_RULE[RULE_SUPERSESSION],
                    source_event_ids=provenance_event_ids,
                    source_bindings=bindings,
                    asset_paths=(asset_path,),
                    scopes=_scopes_for_asset_paths((asset_path,)),
                    details=details,
                )
            )
        return tuple(results[: self.budget.max_outputs_event])

    def _rule_impact_analysis(
        self,
        event: MemoryL0Event,
        payload: dict[str, Any],
        source_event_ids: tuple[str, ...],
    ) -> DistillationRuleResult:
        if event.event_kind != "impact_analysis":
            raise DistillationError(
                "distill-impact-source-gated",
                "impact analysis output is source-gated and not enabled in M3 production capture.",
            )
        paths = _asset_paths(event, payload)
        if not paths:
            raise DistillationError(
                "distill-impact-no-asset",
                "impact analysis artifact has no /Game asset path.",
            )
        primary = paths[0]
        revisions = _asset_revisions_for_event(payload, event)
        bindings = tuple(
            SourceBinding(
                kind="assetRevision",
                key=revision.asset_path,
                revision=revision.revision,
                revision_stable=revision.revision_stable,
            )
            for revision in revisions
        )
        body = (
            f"Source-gated Impact Analysis for {primary}. "
            "Fact is limited to the exact bound asset revisions in the durable analysis artifact."
        )
        details = {
            "distillation": {
                "ruleId": RULE_IMPACT_ANALYSIS,
                "sourceEventIds": list(source_event_ids),
                "sourceBindings": [_binding_payload(item) for item in bindings],
                "primaryAssetPath": primary,
            }
        }
        return DistillationRuleResult(
            rule_id=RULE_IMPACT_ANALYSIS,
            record_type=MemoryRecordType.PROJECT_FACT,
            subject_key=f"impact:{primary}",
            title=f"Impact Analysis: {primary}",
            body=body,
            confidence=CONFIDENCE_BY_RULE[RULE_IMPACT_ANALYSIS],
            source_event_ids=source_event_ids,
            source_bindings=bindings,
            asset_paths=paths,
            scopes=_scopes_for_asset_paths(paths),
            revisions=revisions,
            artifacts=(MemoryArtifact("l0-source", event.artifact_ref),),
            details=details,
        )

    def _upsert_record(
        self,
        connection: sqlite3.Connection,
        *,
        result: DistillationRuleResult,
        event: MemoryL0Event,
        rule_id: str,
        output_index: int,
        started: str,
    ) -> DistillationRecordOutcome:
        _safe_json_bytes(result.details, maximum=self.budget.max_details_bytes)
        source_ref = f"distill:{rule_id}:{event.event_id}"
        record_id = _deterministic_record_id(
            project_key=self.project_key,
            rule_id=rule_id,
            source_event_ids=result.source_event_ids,
            output_index=output_index,
        )
        # Restart safety: prove the existing record is byte-identical to what
        # this rule produces before reusing it. A differing record is a
        # collision/tamper and must fail closed instead of being overwritten.
        expected_content, expected_evidence = record_provenance_digest(
            project_key=self.project_key,
            record_type=result.record_type,
            subject_key=result.subject_key,
            title=result.title,
            body=result.body,
            source_kind=MemorySourceKind.TOOL_OBSERVED,
            source_ref=source_ref,
            confidence=result.confidence,
            scopes=result.scopes,
            revisions=result.revisions,
            artifacts=result.artifacts,
            details=result.details,
        )
        existing = _verify_existing_record(
            connection,
            record_id,
            project_key=self.project_key,
            content_sha256=expected_content,
            evidence_sha256=expected_evidence,
        )
        if existing is not None:
            return DistillationRecordOutcome(
                produced=False,
                reused=True,
                record_id=record_id,
            )
        node_id = _ensure_knowledge_path(
            connection,
            project_key=self.project_key,
            path=_knowledge_path_for_assets(result.asset_paths),
        )
        draft = MemoryRecordDraft(
            project_key=self.project_key,
            record_type=result.record_type,
            subject_key=result.subject_key,
            title=result.title,
            body=result.body,
            source_kind=MemorySourceKind.TOOL_OBSERVED,
            source_ref=source_ref,
            confidence=result.confidence,
            observed_at_utc=event.occurred_at_utc,
            scopes=result.scopes,
            revision_set=result.revisions,
            artifacts=result.artifacts,
            details=result.details,
            record_id=record_id,
            node_id=node_id,
        )
        create_memory_record(connection, draft)
        return DistillationRecordOutcome(produced=True, reused=False, record_id=record_id)

    def evaluate_evidence_chains(self) -> dict[str, str]:
        """Evaluate verdicts for Evidence Chains that have explicitly linked events.

        Only events whose ``hypothesis_id`` equals the chain id influence a
        verdict. No model hypothesis is created.
        """
        results: dict[str, str] = {}
        with open_project_memory_database(self.memory_database) as connection:
            chains = self._l0_service.list_evidence_chains(limit=100)
            for chain in chains:
                support = False
                reject = False
                rows = connection.execute(
                    """
                    SELECT *
                    FROM memory_l0_events
                    WHERE project_key = ? AND hypothesis_id = ?
                    ORDER BY occurred_at_utc ASC, event_id ASC
                    """,
                    (self.project_key, chain.chain_id),
                ).fetchall()
                for row in rows:
                    candidate = _row_event_from_sqlite(row)
                    if candidate.event_kind in CHAIN_SUPPORT_EVENT_KINDS and candidate.outcome in {
                        "success",
                        "recovered",
                    }:
                        support = True
                    if candidate.event_kind in CHAIN_REJECT_EVENT_KINDS and candidate.outcome in {
                        "failed",
                        "rejected",
                        "partial",
                    }:
                        reject = True
                verdict = "inconclusive"
                if support and not reject:
                    verdict = "supported"
                elif reject and not support:
                    verdict = "rejected"
                elif support and reject:
                    verdict = "inconclusive"
                if verdict != chain.verdict:
                    connection.execute(
                        """
                        UPDATE memory_evidence_chains
                        SET verdict = ?, verified_at_utc = ?
                        WHERE chain_id = ? AND project_key = ?
                        """,
                        (verdict, utc_now_iso(), chain.chain_id, self.project_key),
                    )
                    connection.commit()
                results[chain.chain_id] = verdict
        return results

    def _current_index_revisions(self) -> dict[str, str] | None:
        """Read the current asset Revisions from the fixed immutable index.

        Returns ``None`` when the index is unavailable (missing file or a
        different project), which means asset bindings cannot be checked here.
        An available index with no revisions returns an empty mapping, which
        makes every bound asset count as missing.

        Asset-derived distilled L1 records are bound to the exact asset
        Revision that made their statement true, so a later index Revision
        change must make the record stale. This is only ever called from the
        explicit offline maintenance path; request-time recall never opens the
        index or hashes assets.
        """
        if not self.index_database.is_file():
            return None
        with open_database(
            self.index_database,
            readonly=True,
            migrate=False,
            immutable=True,
        ) as index_connection:
            if get_metadata(index_connection, "project_key", "") != self.project_key:
                return None
            rows = index_connection.execute(
                """
                SELECT asset_path, revision_value
                FROM assets
                WHERE revision_value <> ''
                ORDER BY asset_path
                """,
            ).fetchall()
        return {str(row[0]): str(row[1]) for row in rows}

    def validate_source_bindings(self) -> dict[str, Any]:
        """Validate generic source bindings for tool-observed distilled records.

        Two binding kinds are validated:

        ``policyDigest``
            compared against the fixed Project Write Policy file.

        ``assetRevision``
            compared against the current Revision reported by the fixed
            immutable index for the exact bound asset.

        Only records created by M3 (``source_ref`` starting with ``distill:``)
        are considered, and only from the explicit offline maintenance path.
        """
        policy_digest = ""
        if self.policy_path.is_file():
            policy_digest = hashlib.sha256(self.policy_path.read_bytes()).hexdigest()
        stale_record_ids: list[str] = []
        reasons: dict[str, dict[str, Any]] = {}
        with open_project_memory_database(self.memory_database) as connection:
            rows = connection.execute(
                """
                SELECT record_id, details_json, status
                FROM memory_records
                WHERE project_key = ? AND source_kind = 'tool-observed'
                  AND source_ref LIKE 'distill:%'
                """,
                (self.project_key,),
            ).fetchall()
            for row in rows:
                record_id = str(row[0])
                status = str(row[2])
                if status in {"stale", "superseded"}:
                    continue
                try:
                    details = json.loads(str(row[1]))
                except json.JSONDecodeError:
                    continue
                distillation = details.get("distillation", {})
                if not isinstance(distillation, dict):
                    continue
                for binding in distillation.get("sourceBindings", []):
                    if not isinstance(binding, dict):
                        continue
                    kind = str(binding.get("kind", ""))
                    if kind == "policyDigest":
                        expected = str(binding.get("revision", "")).removeprefix("sha256:")
                        if expected and policy_digest and expected != policy_digest:
                            stale_record_ids.append(record_id)
                            reasons[record_id] = {
                                "reason": "source-binding-mismatch",
                                "kind": "policyDigest",
                            }

        # Asset-derived L1 records: bound exact asset Revision -> index change.
        current_revisions = self._current_index_revisions()
        asset_checked = 0
        if current_revisions is not None:
            with open_project_memory_database(self.memory_database) as connection:
                bound_rows = connection.execute(
                    """
                    SELECT r.record_id, v.asset_path, v.revision
                    FROM memory_records AS r
                    JOIN memory_revisions AS v ON v.record_id = r.record_id
                    WHERE r.project_key = ?
                      AND r.source_ref LIKE 'distill:%'
                      AND r.status NOT IN ('stale', 'superseded')
                      AND v.revision_stable = 1
                    ORDER BY r.record_id, v.ordinal
                    """,
                    (self.project_key,),
                ).fetchall()
                bound: dict[str, list[tuple[str, str]]] = {}
                for row in bound_rows:
                    bound.setdefault(str(row[0]), []).append((str(row[1]), str(row[2])))
                asset_checked = len(bound)
                for record_id, expected_revisions in sorted(bound.items()):
                    missing: list[str] = []
                    mismatched: list[dict[str, str]] = []
                    for asset_path, expected_revision in expected_revisions:
                        current = current_revisions.get(asset_path)
                        if current is None:
                            missing.append(asset_path)
                        elif current != expected_revision:
                            mismatched.append(
                                {
                                    "assetPath": asset_path,
                                    "expectedRevision": expected_revision,
                                    "currentRevision": current,
                                }
                            )
                    if not missing and not mismatched:
                        continue
                    detail = {
                        "missingAssetPaths": missing,
                        "mismatchedRevisions": mismatched,
                    }
                    stale_record_ids.append(record_id)
                    reasons[record_id] = {
                        "reason": "revision-set-mismatch",
                        "kind": "assetRevision",
                        **detail,
                    }

        if stale_record_ids:
            with open_project_memory_database(self.memory_database) as connection:
                for record_id in stale_record_ids:
                    try:
                        record = get_memory_record(connection, record_id)
                    except KeyError:
                        continue
                    if record.status in {MemoryStatus.STALE, MemoryStatus.SUPERSEDED}:
                        continue
                    detail = {
                        key: value
                        for key, value in reasons.get(record_id, {}).items()
                        if key not in {"reason", "kind"}
                    }
                    _transition_record_stale(
                        connection,
                        record_id=record_id,
                        reason=reasons.get(record_id, {}).get("reason", "source-binding-mismatch"),
                        details=detail,
                    )
                connection.commit()
        return {
            "projectKey": self.project_key,
            "policyDigestValidated": bool(policy_digest),
            "assetBindingsChecked": asset_checked,
            "indexDatabasePresent": self.index_database.is_file(),
            "staleRecordIds": stale_record_ids,
            "reasons": reasons,
        }


def _row_event_from_sqlite(row: sqlite3.Row) -> MemoryL0Event:
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


def _binding_payload(binding: SourceBinding) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "kind": binding.kind,
        "key": binding.key,
        "revision": binding.revision,
        "revisionStable": binding.revision_stable,
    }
    if binding.details:
        payload["details"] = binding.details
    return payload


def _transition_record_stale(
    connection: sqlite3.Connection,
    *,
    record_id: str,
    reason: str,
    details: dict[str, Any] | None = None,
) -> None:
    """Narrow status transition used by source-binding validation.

    This uses the same allowed-transition rules as Project Memory but avoids
    importing the private transition helper. It is safe because it only
    transitions valid/unverified/conflicted records to stale.
    """
    current = get_memory_record(connection, record_id)
    if current.status == MemoryStatus.VALID:
        from_status = "valid"
    elif current.status == MemoryStatus.UNVERIFIED:
        from_status = "unverified"
    elif current.status == MemoryStatus.CONFLICTED:
        from_status = "conflicted"
    else:
        return
    timestamp = utc_now_iso()
    connection.execute(
        "UPDATE memory_records SET status = ?, updated_at_utc = ? WHERE record_id = ?",
        (MemoryStatus.STALE.value, timestamp, record_id),
    )
    connection.execute(
        """
        INSERT INTO memory_status_events(
            record_id, from_status, to_status, reason, changed_at_utc, details_json
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            record_id,
            from_status,
            MemoryStatus.STALE.value,
            reason,
            timestamp,
            _canonical_json(details or {}, field_name="status event details"),
        ),
    )

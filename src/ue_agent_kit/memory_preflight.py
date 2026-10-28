"""Explicit host preflight for DSH Session/Goal handoff.

This module is the narrow UEAgentKit-side contract a trusted host adapter can
call immediately before opening/resuming a DSH Session or Goal:

1. run the opt-in MA1 snapshot maintenance (only when ``enabled``);
2. fail closed unless the persisted snapshot is current;
3. read the bounded L2/L3 payload read-only, with text omitted by default so a
   caller must explicitly request sensitive context for injection.

It never runs from ``get_injection_context``, MCP tools, or any background
loop. The host must supply fixed DB/Index/projectKey values from its own
configuration; none of them is derived from model text or a DSH session id.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .memory_injection import (
    COMBINED_MAX_ESTIMATED_TOKENS,
    MemoryContextMaintenanceResult,
    get_injection_context,
    maintain_memory_context_snapshot,
)
from .project_memory import open_project_memory_database


PREFLIGHT_SCHEMA_VERSION = "1.0"
PREFLIGHT_TOOL_NAME = "ue_memory_session_preflight"
PREFLIGHT_MAX_TEXT_CHARS = COMBINED_MAX_ESTIMATED_TOKENS * 4


@dataclass(frozen=True)
class MemoryPreflightResult:
    """One bounded, fail-closed preflight result.

    ``state`` is ``fresh`` only when maintenance reported ``current`` and the
    read path returned an available, budget-valid snapshot. ``stale``,
    ``missing``, ``unavailable``, ``disabled``, and ``error`` all have
    ``available=False`` and empty text.
    """

    project_key: str
    enabled: bool
    state: str
    available: bool
    reason: str
    maintenance: MemoryContextMaintenanceResult | None = None
    snapshot_id: str = ""
    index_snapshot_id: str = ""
    l2_entries: int = 0
    l3_entries: int = 0
    content_chars: int = 0
    estimated_tokens: int = 0
    injection_hash: str = ""
    text: str = ""
    text_omitted: bool = True
    error_code: str = ""
    error: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "schemaVersion": PREFLIGHT_SCHEMA_VERSION,
            "tool": PREFLIGHT_TOOL_NAME,
            "projectKey": self.project_key,
            "enabled": self.enabled,
            "state": self.state,
            "available": self.available,
            "stale": self.state in {"stale", "missing"},
            "reason": self.reason,
            "snapshotId": self.snapshot_id,
            "indexSnapshotId": self.index_snapshot_id,
            "l2Entries": self.l2_entries,
            "l3Entries": self.l3_entries,
            "contentChars": self.content_chars,
            "estimatedTokens": self.estimated_tokens,
            "injectionHash": self.injection_hash,
            "text": self.text,
            "textOmitted": self.text_omitted,
            "maintenance": self.maintenance.to_payload() if self.maintenance is not None else None,
            "errorCode": self.error_code,
            "error": self.error,
        }


def _state_from_maintenance(maintenance: MemoryContextMaintenanceResult) -> str:
    if maintenance.state == "current":
        return "fresh"
    if maintenance.state in {"stale", "missing"}:
        return maintenance.state
    if maintenance.state in {"disabled", "unavailable"}:
        return maintenance.state
    return "error"


def _result(
    *,
    project_key: str,
    enabled: bool,
    state: str,
    reason: str,
    maintenance: MemoryContextMaintenanceResult | None = None,
    payload: dict[str, Any] | None = None,
    include_text: bool = False,
    error_code: str = "",
    error: str = "",
) -> MemoryPreflightResult:
    after = maintenance.after if maintenance is not None and maintenance.after else {}
    text = str(payload.get("text", "")) if payload is not None else ""
    return MemoryPreflightResult(
        project_key=project_key,
        enabled=enabled,
        state=state,
        available=state == "fresh",
        reason=reason,
        maintenance=maintenance,
        snapshot_id=str((payload or {}).get("snapshotId", after.get("snapshotId", ""))),
        index_snapshot_id=str(
            (payload or {}).get("indexSnapshotId", after.get("indexSnapshotId", ""))
        ),
        l2_entries=int((payload or {}).get("l2Count", 0)),
        l3_entries=int((payload or {}).get("l3Count", 0)),
        content_chars=int((payload or {}).get("contentChars", 0)),
        estimated_tokens=int((payload or {}).get("estimatedTokens", 0)),
        injection_hash=str((payload or {}).get("injectionHash", "")),
        text=text if include_text else "",
        text_omitted=not include_text,
        error_code=error_code,
        error=error,
    )


def preflight_memory_context(
    *,
    memory_database: Path,
    index_database: Path,
    project_key: str,
    enabled: bool = False,
    include_text: bool = False,
    query: str = "",
) -> MemoryPreflightResult:
    """Run one explicit, bounded preflight for a Session/Goal handoff.

    The caller must supply fixed ``memory_database``, ``index_database`` and
    ``project_key`` from trusted host configuration. There is intentionally no
    session id, model path, or arbitrary project argument.

    ``query`` is optional host-supplied Goal/task text used only to match
    persisted L2 recipes; it is never used for authentication, project
    selection, or a filesystem path. Empty query returns the bounded L3 rules
    without task-specific L2 recipes.

    ``include_text`` defaults to False so normal diagnostics and logs never
    contain the bounded L2/L3 text. A host that will inject the context must
    pass True and keep that text out of logs.
    """

    if not isinstance(enabled, bool):
        raise ValueError("enabled must be boolean.")
    if not isinstance(include_text, bool):
        raise ValueError("include_text must be boolean.")
    if not isinstance(query, str):
        raise ValueError("query must be a string.")
    normalized_project = project_key.strip() if isinstance(project_key, str) else ""
    if not normalized_project:
        raise ValueError("project_key must be a non-empty string.")
    if not enabled:
        return _result(
            project_key=normalized_project,
            enabled=False,
            state="disabled",
            reason="maintenance-disabled",
            include_text=False,
        )

    try:
        maintenance = maintain_memory_context_snapshot(
            memory_database=memory_database,
            project_key=normalized_project,
            index_database=index_database,
            enabled=True,
        )
    except Exception as exc:
        return _result(
            project_key=normalized_project,
            enabled=True,
            state="error",
            reason="preflight-maintenance-failed",
            include_text=False,
            error_code="preflight-maintenance-failed",
            error=str(exc),
        )

    state = _state_from_maintenance(maintenance)
    if state != "fresh":
        return _result(
            project_key=normalized_project,
            enabled=True,
            state=state,
            reason=maintenance.reason,
            maintenance=maintenance,
            include_text=False,
            error_code=maintenance.error_code,
            error=maintenance.error,
        )

    snapshot_id = str((maintenance.after or {}).get("snapshotId", ""))
    index_snapshot_id = str((maintenance.after or {}).get("indexSnapshotId", ""))
    if not index_snapshot_id:
        return _result(
            project_key=normalized_project,
            enabled=True,
            state="error",
            reason="preflight-index-snapshot-missing",
            maintenance=maintenance,
            include_text=False,
            error_code="preflight-index-snapshot-missing",
        )

    try:
        with open_project_memory_database(memory_database, readonly=True) as connection:
            payload = get_injection_context(
                connection,
                project_key=normalized_project,
                query=query,
                index_snapshot_id=index_snapshot_id,
            )
    except Exception as exc:
        # Explicitly fail closed: a read error must not surface an old text.
        return _result(
            project_key=normalized_project,
            enabled=True,
            state="error",
            reason="preflight-read-failed",
            maintenance=maintenance,
            include_text=False,
            error_code="preflight-read-failed",
            error=str(exc),
        )

    if not payload.get("available"):
        read_state = "stale" if payload.get("stale") else "unavailable"
        return _result(
            project_key=normalized_project,
            enabled=True,
            state=read_state,
            reason=str(payload.get("reason", "preflight-snapshot-unavailable")),
            maintenance=maintenance,
            include_text=False,
            error_code=str(payload.get("reason", "preflight-snapshot-unavailable")),
        )

    if (
        int(payload.get("contentChars", 0)) > PREFLIGHT_MAX_TEXT_CHARS
        or int(payload.get("estimatedTokens", 0)) > COMBINED_MAX_ESTIMATED_TOKENS
    ):
        return _result(
            project_key=normalized_project,
            enabled=True,
            state="error",
            reason="preflight-output-too-large",
            maintenance=maintenance,
            include_text=False,
            error_code="preflight-output-too-large",
        )

    if not snapshot_id or str(payload.get("snapshotId", "")) != snapshot_id:
        return _result(
            project_key=normalized_project,
            enabled=True,
            state="error",
            reason="preflight-snapshot-changed",
            maintenance=maintenance,
            include_text=False,
            error_code="preflight-snapshot-changed",
        )

    return _result(
        project_key=normalized_project,
        enabled=True,
        state="fresh",
        reason="preflight-fresh",
        maintenance=maintenance,
        payload=payload,
        include_text=include_text,
    )

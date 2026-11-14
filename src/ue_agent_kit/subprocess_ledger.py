"""0-3.5: bounded ledger of every subprocess spawn attempt.

The workflow tool group cold-starts ``UnrealEditor-Cmd.exe`` through its
PowerShell scripts.  That is a sharp edge -- it can displace the Editor the user
is working in -- so the group is default-off, and every attempt (allowed,
blocked or failed) is recorded here.  The read-only ``ue_get_subprocess_ledger``
tool exposes the ledger so "spawned == 0" is an assertion, not an eyeball.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

LEDGER_SCHEMA_VERSION = "ueak.subprocessledger/1.0"
MAX_LEDGER_ENTRIES = 64

OUTCOME_SPAWNED = "spawned"
OUTCOME_BLOCKED = "blocked"
OUTCOME_FAILED = "failed"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SubprocessLedger:
    """One MCP session's spawn attempts, bounded and in insertion order."""

    def __init__(self, max_entries: int = MAX_LEDGER_ENTRIES) -> None:
        if not isinstance(max_entries, int) or max_entries < 1:
            raise ValueError("max_entries must be a positive integer.")
        self._max_entries = max_entries
        self._entries: list[dict[str, Any]] = []

    def record(
        self,
        *,
        script: str,
        commandlet: str = "",
        pid: int | None = None,
        started_at_utc: str = "",
        duration_ms: float = 0.0,
        outcome: str,
        error_code: str = "",
    ) -> dict[str, Any]:
        if outcome not in {OUTCOME_SPAWNED, OUTCOME_BLOCKED, OUTCOME_FAILED}:
            raise ValueError("outcome must be spawned, blocked or failed.")
        entry = {
            "script": script,
            "commandlet": commandlet,
            "pid": pid,
            "startedAtUtc": started_at_utc or _utc_now_iso(),
            "durationMs": round(float(duration_ms), 3),
            "outcome": outcome,
            "errorCode": error_code,
        }
        self._entries.append(entry)
        while len(self._entries) > self._max_entries:
            self._entries.pop(0)
        return dict(entry)

    def entries(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._entries]

    def stats(self) -> dict[str, Any]:
        entries = self.entries()
        return {
            "schemaVersion": LEDGER_SCHEMA_VERSION,
            "entries": entries,
            "count": len(entries),
            "blockedCount": sum(1 for entry in entries if entry["outcome"] == OUTCOME_BLOCKED),
            "spawnedCount": sum(1 for entry in entries if entry["outcome"] == OUTCOME_SPAWNED),
            "failedCount": sum(1 for entry in entries if entry["outcome"] == OUTCOME_FAILED),
            "maxEntries": self._max_entries,
        }

    def clear(self) -> int:
        count = len(self._entries)
        self._entries.clear()
        return count


_SESSION_LEDGER = SubprocessLedger()


def get_session_ledger() -> SubprocessLedger:
    return _SESSION_LEDGER


def reset_session_ledger() -> SubprocessLedger:
    """Test-only: drop every attempt and hand back the fresh ledger."""
    _SESSION_LEDGER.clear()
    return _SESSION_LEDGER

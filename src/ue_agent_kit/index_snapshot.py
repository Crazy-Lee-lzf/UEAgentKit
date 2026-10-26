from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .database import get_metadata


INDEX_SNAPSHOT_SCHEMA_VERSION = "1.0"


def _normalize_sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if len(text) != 64:
        return ""
    return text if all(character in "0123456789abcdef" for character in text) else ""


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def compute_index_snapshot(
    connection: sqlite3.Connection,
    database_path: Path,
    *,
    editor_freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Describe whether this immutable SQLite snapshot is fresh.

    ``exportSnapshotState`` is provable from the manifest/provenance recorded by
    the indexer.  ``state`` additionally considers the Editor/package state; a
    cold read-only SQLite cannot prove Editor state, so it stays ``unknown``
    unless a live freshness result is supplied.
    """
    database_path = database_path.expanduser().resolve()
    export_root_text = get_metadata(connection, "last_export_root", "").strip()
    recorded_manifest_sha = _normalize_sha256(get_metadata(connection, "last_manifest_sha256", ""))
    recorded_export_utc = get_metadata(connection, "last_export_utc", "").strip()
    recorded_database_path = get_metadata(connection, "last_database_path", "").strip()
    last_indexed_at = get_metadata(connection, "last_indexed_at_utc", "").strip()
    build_complete = get_metadata(connection, "last_index_build_complete", "").strip()
    change_mode = get_metadata(connection, "last_index_change_mode", "").strip()
    fast_path_skips = get_metadata(connection, "last_index_fast_path_skips", "").strip()

    reasons: list[str] = []
    export_state = "unknown"
    manifest_sha = ""
    manifest_export_utc = ""
    manifest_path = Path(export_root_text) / "manifest.json" if export_root_text else None
    if not last_indexed_at:
        _add_reason(reasons, "index-not-built")
    if build_complete == "0":
        _add_reason(reasons, "last-index-build-incomplete")

    database_path_matches: bool | None = None
    if recorded_database_path:
        database_path_matches = Path(recorded_database_path).expanduser().resolve() == database_path
    else:
        _add_reason(reasons, "database-path-provenance-missing")

    if not export_root_text:
        _add_reason(reasons, "export-root-missing")
    elif manifest_path is None or not manifest_path.is_file():
        _add_reason(reasons, "export-manifest-missing")
    else:
        try:
            manifest_bytes = manifest_path.read_bytes()
            manifest = json.loads(manifest_bytes.decode("utf-8-sig"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest root must be an object")
            manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
            manifest_export_utc = str(manifest.get("createdUtc", "")).strip()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            _add_reason(reasons, "export-manifest-invalid")
        else:
            if not recorded_manifest_sha:
                _add_reason(reasons, "manifest-digest-provenance-missing")
            if not recorded_export_utc:
                _add_reason(reasons, "export-timestamp-provenance-missing")
            if recorded_manifest_sha and manifest_sha != recorded_manifest_sha:
                _add_reason(reasons, "manifest-digest-mismatch")
            if recorded_export_utc and manifest_export_utc != recorded_export_utc:
                _add_reason(reasons, "export-timestamp-mismatch")

    if recorded_database_path and database_path_matches is False:
        _add_reason(reasons, "database-path-mismatch")

    hard_stale = bool(
        (recorded_database_path and database_path_matches is False)
        or "manifest-digest-mismatch" in reasons
        or "export-timestamp-mismatch" in reasons
        or "last-index-build-incomplete" in reasons
    )
    provenance_unknown = any(
        reason
        in {
            "index-not-built",
            "export-root-missing",
            "export-manifest-missing",
            "export-manifest-invalid",
            "manifest-digest-provenance-missing",
            "export-timestamp-provenance-missing",
            "database-path-provenance-missing",
        }
        for reason in reasons
    )
    if hard_stale:
        export_state = "stale"
    elif not provenance_unknown and recorded_manifest_sha and manifest_sha == recorded_manifest_sha:
        export_state = "fresh"
    else:
        export_state = "unknown"

    if hard_stale:
        state = "stale"
    else:
        state = "unknown"
        if export_state == "fresh":
            _add_reason(reasons, "editor-state-not-compared")

    snapshot = {
        "schemaVersion": INDEX_SNAPSHOT_SCHEMA_VERSION,
        "state": state,
        "exportSnapshotState": export_state,
        "editorStateCompared": False,
        "reasons": reasons,
        "databasePath": str(database_path),
        "lastDatabasePath": recorded_database_path,
        "databasePathMatches": database_path_matches,
        "lastExportRoot": export_root_text,
        "lastExportUtc": recorded_export_utc,
        "manifestExportUtc": manifest_export_utc,
        "lastIndexedAtUtc": last_indexed_at,
        "manifestSha256": manifest_sha,
        "recordedManifestSha256": recorded_manifest_sha,
        "manifestMatches": (
            None
            if not recorded_manifest_sha or not manifest_sha
            else manifest_sha == recorded_manifest_sha
        ),
        "lastIndexBuildComplete": build_complete,
        "lastIndexChangeMode": change_mode,
        "lastIndexFastPathSkips": int(fast_path_skips) if fast_path_skips.isdigit() else 0,
        "freshnessScope": "export-snapshot",
    }
    return apply_editor_freshness(snapshot, editor_freshness)


def apply_editor_freshness(
    snapshot: dict[str, Any],
    editor_freshness: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge live Editor/package freshness into an export-snapshot result."""
    result = dict(snapshot)
    reasons = list(result.get("reasons", []))
    state = str(result.get("state", "unknown"))
    export_state = str(result.get("exportSnapshotState", "unknown"))
    editor_compared = False
    if editor_freshness:
        editor_state = str(editor_freshness.get("state", "")).strip().lower()
        if editor_state in {"fresh", "stale", "unavailable", "unknown", "partial"}:
            editor_compared = True
            if editor_state == "stale":
                state = "stale"
                _add_reason(reasons, "editor-package-revision-stale")
            elif editor_state == "fresh" and state != "stale":
                if export_state == "fresh":
                    state = "fresh"
                    reasons = [r for r in reasons if r != "editor-state-not-compared"]
                else:
                    _add_reason(reasons, "editor-freshness-fresh-but-export-unknown")
            else:
                if state != "stale":
                    state = "unknown"
                _add_reason(reasons, f"editor-freshness-{editor_state}")
    result["state"] = state
    result["reasons"] = reasons
    result["editorStateCompared"] = editor_compared
    return result

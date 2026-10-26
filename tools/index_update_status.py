"""Read-only index update timestamps for UEAgentKit export scripts.

The exporter writes Canonical/BPCTX files, not the SQLite index. Never infer
freshness from the SQLite file modification time or the export timestamp.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


def _normalize_sha256(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    return text if len(text) == 64 and all(c in "0123456789abcdef" for c in text) else ""


def _manifest_provenance(metadata: dict[str, str], database: Path) -> dict[str, Any]:
    reasons: list[str] = []
    export_root = metadata.get("last_export_root", "").strip()
    recorded_digest = _normalize_sha256(metadata.get("last_manifest_sha256", ""))
    recorded_utc = metadata.get("last_export_utc", "").strip()
    recorded_database = metadata.get("last_database_path", "").strip()
    build_complete = metadata.get("last_index_build_complete", "").strip()
    export_state = "unknown"
    if build_complete == "0":
        reasons.append("last-index-build-incomplete")
    database_matches: bool | None = None
    if recorded_database:
        database_matches = Path(recorded_database).expanduser().resolve() == database
    else:
        reasons.append("database-path-provenance-missing")
    current_digest = ""
    current_utc = ""
    manifest_path = Path(export_root) / "manifest.json" if export_root else None
    if not export_root:
        reasons.append("export-root-missing")
    elif manifest_path is None or not manifest_path.is_file():
        reasons.append("export-manifest-missing")
    else:
        try:
            raw = manifest_path.read_bytes()
            payload = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(payload, dict):
                raise ValueError("manifest root is not an object")
            current_digest = hashlib.sha256(raw).hexdigest()
            current_utc = str(payload.get("createdUtc", "")).strip()
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            reasons.append("export-manifest-invalid")
        else:
            if not recorded_digest:
                reasons.append("manifest-digest-provenance-missing")
            if not recorded_utc:
                reasons.append("export-timestamp-provenance-missing")
            if recorded_digest and current_digest != recorded_digest:
                reasons.append("manifest-digest-mismatch")
            if recorded_utc and current_utc != recorded_utc:
                reasons.append("export-timestamp-mismatch")
    if database_matches is False:
        reasons.append("database-path-mismatch")
    stale = bool(
        database_matches is False
        or "manifest-digest-mismatch" in reasons
        or "export-timestamp-mismatch" in reasons
        or "last-index-build-incomplete" in reasons
    )
    unknown = any(
        reason
        in {
            "export-root-missing",
            "export-manifest-missing",
            "export-manifest-invalid",
            "manifest-digest-provenance-missing",
            "export-timestamp-provenance-missing",
            "database-path-provenance-missing",
        }
        for reason in reasons
    )
    if stale:
        export_state = "stale"
    elif not unknown and recorded_digest and current_digest == recorded_digest:
        export_state = "fresh"
    index_state = "stale" if stale else "unknown"
    if index_state == "unknown" and export_state == "fresh":
        reasons.append("editor-state-not-compared")
    return {
        "indexSnapshotState": index_state,
        "exportSnapshotState": export_state,
        "snapshotReasons": reasons,
        "databasePathMatches": database_matches,
        "lastExportUtc": recorded_utc,
        "currentManifestExportUtc": current_utc,
        "manifestMatches": (
            None if not recorded_digest or not current_digest else current_digest == recorded_digest
        ),
    }


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _local_time(value: str) -> str:
    if not value:
        return "unavailable"
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            return "unavailable (timestamp has no timezone)"
        return timestamp.astimezone().isoformat(timespec="seconds")
    except ValueError:
        return "unavailable (invalid timestamp)"


def read_index_status(database: Path) -> dict[str, Any]:
    path = database.expanduser().resolve()
    result: dict[str, Any] = {
        "database": str(path),
        "state": "missing",
        "projectKey": "",
        "lastIndexBuildUtc": "",
        "lastAssetIndexedUtc": "",
        "assetCount": None,
        "reason": "",
        "indexSnapshotState": "unknown",
        "exportSnapshotState": "unknown",
        "snapshotReasons": [],
        "databasePathMatches": None,
        "lastExportUtc": "",
        "currentManifestExportUtc": "",
        "manifestMatches": None,
    }
    if not path.is_file():
        result["reason"] = "SQLite database was not found; no index update time is available."
        return result

    try:
        # read-only URI: never creates a database or updates its metadata
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=3.0)) as conn:
            conn.execute("PRAGMA query_only=ON")
            has_metadata = _table_exists(conn, "metadata")
            has_assets = _table_exists(conn, "assets")
            metadata: dict[str, str] = {}
            if has_metadata:
                metadata = dict(conn.execute(
                    """
                    SELECT key,value FROM metadata
                    WHERE key IN (
                        'last_indexed_at_utc','project_key','last_export_root',
                        'last_export_utc','last_manifest_sha256','last_database_path',
                        'last_index_build_complete','last_index_change_mode'
                    )
                    """
                ).fetchall())
                result["lastIndexBuildUtc"] = str(metadata.get("last_indexed_at_utc") or "")
                result["projectKey"] = str(metadata.get("project_key") or "")
                result.update(_manifest_provenance(metadata, path))
            if has_assets:
                row = conn.execute("SELECT COUNT(*), MAX(indexed_at_utc) FROM assets").fetchone()
                result["assetCount"] = int(row[0])
                result["lastAssetIndexedUtc"] = str(row[1] or "")
            if has_metadata and has_assets:
                result["state"] = "ready" if result["lastIndexBuildUtc"] else "timestamp-unavailable"
                if not result["lastIndexBuildUtc"]:
                    result["reason"] = "Index metadata has no last_indexed_at_utc value."
            else:
                result["state"] = "incomplete"
                result["reason"] = "Not a complete UEAgentKit index (metadata/assets table missing)."
    except (sqlite3.Error, OSError) as exc:
        result["state"] = "unreadable"
        result["reason"] = f"Index read failed: {type(exc).__name__}"
    return result


def format_status(status: dict[str, Any]) -> str:
    lines = [
        f"Index database : {status['database']}",
        f"Index state    : {status['state']}",
        f"Project key    : {status['projectKey'] or 'unavailable'}",
        f"Last index run : {status['lastIndexBuildUtc'] or 'unavailable'} (UTC)",
        f"                 {_local_time(status['lastIndexBuildUtc'])} (local)",
        f"Latest row update: {status['lastAssetIndexedUtc'] or 'unavailable'} (UTC)",
        f"                 {_local_time(status['lastAssetIndexedUtc'])} (local)",
        f"Indexed assets : {status['assetCount'] if status['assetCount'] is not None else 'unavailable'}",
        f"Snapshot state : {status['indexSnapshotState']} (export: {status['exportSnapshotState']})",
        f"Export time    : {status['lastExportUtc'] or 'unavailable'} (UTC)",
    ]
    if status["snapshotReasons"]:
        lines.append(f"Snapshot reason: {', '.join(status['snapshotReasons'])}")
    if status["reason"]:
        lines.append(f"Index warning  : {status['reason']}")
    lines.append("Export does NOT update the SQLite index. Rebuild/import the export before trusting index queries.")
    lines.append("The timestamps alone do not prove that the queried asset is fresh.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show the last UEAgentKit SQLite index update time.")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable status.")
    args = parser.parse_args(argv)
    status = read_index_status(args.database)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, sort_keys=True))
    else:
        print(format_status(status))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

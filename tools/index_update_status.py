"""Read-only index update timestamps for UEAgentKit export scripts.

The exporter writes Canonical/BPCTX files, not the SQLite index. Never infer
freshness from the SQLite file modification time or the export timestamp.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


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
            if has_metadata:
                entries = dict(conn.execute(
                    "SELECT key,value FROM metadata WHERE key IN ('last_indexed_at_utc','project_key')"
                ).fetchall())
                result["lastIndexBuildUtc"] = str(entries.get("last_indexed_at_utc") or "")
                result["projectKey"] = str(entries.get("project_key") or "")
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
    ]
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

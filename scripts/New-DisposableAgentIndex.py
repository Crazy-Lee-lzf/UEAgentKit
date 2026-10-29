#!/usr/bin/env python3
"""Create a tiny disposable UE Agent Kit index database for live-only probes.

The MCP server always opens its read-only index before serving, even when every
call in the session targets the live Editor.  P5 must not touch the production
3.4 GB index, so this helper creates an empty schema-current database in the test
output tree.  It never imports assets and never writes outside the given path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database, set_metadata  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project-key", default="ueak-p5-disposable")
    args = parser.parse_args(argv)

    database = args.database.resolve()
    if database.exists():
        database.unlink()
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = Path(str(database) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    database.parent.mkdir(parents=True, exist_ok=True)

    with open_database(database, readonly=False, migrate=True) as connection:
        set_metadata(connection, "project_key", args.project_key)
        set_metadata(connection, "last_indexed_at_utc", "2026-10-26T00:00:00.000Z")
        set_metadata(connection, "last_export_utc", "2026-10-26T00:00:00.000Z")
        set_metadata(connection, "last_manifest_schema", "1.1")
        set_metadata(connection, "last_exporter_version", "0.8.0")
        set_metadata(connection, "last_profile", "asset-index")

    status = IndexQueryService(database).check()
    if not status.get("ok"):
        print(f"disposable index check failed: {status}", file=sys.stderr)
        return 1
    print(f"disposable index ready: {database}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

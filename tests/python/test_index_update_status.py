"""Regression tests for read-only index update time diagnostics."""
from __future__ import annotations

import importlib.util
import json
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "tools" / "index_update_status.py"
spec = importlib.util.spec_from_file_location("index_update_status", HELPER)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IndexUpdateStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "index.sqlite3"

    def seed(self, *, timestamp: str = "2026-10-25T03:20:00.000Z",
             asset_timestamp: str = "2026-10-24T03:20:00.000Z") -> None:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            conn.execute("CREATE TABLE assets(indexed_at_utc TEXT NOT NULL)")
            conn.executemany("INSERT INTO metadata VALUES (?, ?)", [
                ("project_key", "TestWorld"),
                ("last_indexed_at_utc", timestamp),
            ])
            conn.executemany("INSERT INTO assets VALUES (?)", [
                ("2026-10-22T03:20:00.000Z",),
                (asset_timestamp,),
            ])
            conn.commit()

    def test_missing_does_not_create_database(self) -> None:
        result = module.read_index_status(self.db)
        self.assertEqual(result["state"], "missing")
        self.assertFalse(self.db.exists())

    def test_reports_build_and_latest_row_separately(self) -> None:
        self.seed()
        result = module.read_index_status(self.db)
        self.assertEqual(result["state"], "ready")
        self.assertEqual(result["projectKey"], "TestWorld")
        self.assertEqual(result["assetCount"], 2)
        self.assertEqual(result["lastIndexBuildUtc"], "2026-10-25T03:20:00.000Z")
        self.assertEqual(result["lastAssetIndexedUtc"], "2026-10-24T03:20:00.000Z")
        self.assertIn("does NOT update", module.format_status(result))

    def test_cli_reports_same_database_without_writing_it(self) -> None:
        self.seed()
        before = self.db.stat().st_mtime_ns
        done = subprocess.run(
            [sys.executable, str(HELPER), "--database", str(self.db), "--json"],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(done.stdout)["assetCount"], 2)
        self.assertEqual(self.db.stat().st_mtime_ns, before)

    def test_missing_metadata_or_empty_timestamp_never_invented(self) -> None:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE assets(indexed_at_utc TEXT NOT NULL)")
        result = module.read_index_status(self.db)
        self.assertEqual(result["state"], "timestamp-unavailable")
        self.assertEqual(result["lastAssetIndexedUtc"], "")

    def test_wrong_sqlite_schema_is_not_marked_ready(self) -> None:
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute("CREATE TABLE arbitrary(id INTEGER)")
        self.assertEqual(module.read_index_status(self.db)["state"], "incomplete")

    def test_garbage_file_is_not_marked_ready(self) -> None:
        self.db.write_bytes(b"this is not a database")
        self.assertEqual(module.read_index_status(self.db)["state"], "unreadable")

    def test_unrecognized_timestamp_is_not_presented_as_local(self) -> None:
        self.seed(timestamp="broken")
        result = module.read_index_status(self.db)
        self.assertIn("invalid timestamp", module.format_status(result))

    def test_export_scripts_report_status_even_if_editor_fails(self) -> None:
        for name in ("RunExport.ps1", "RunAssetCatalog.ps1"):
            content = (ROOT / "scripts" / name).read_text(encoding="utf-8-sig")
            self.assertIn('[string]$Database = ""', content)
            self.assertIn("$ExportExitCode = $LASTEXITCODE", content)
            self.assertIn("Show-UeakIndexUpdateStatus -Database $Database", content)
            self.assertLess(content.index("Show-UeakIndexUpdateStatus -Database $Database"),
                            content.index("if ($ExportExitCode -ne 0)"))
        common = (ROOT / "scripts" / "Common.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("function Show-UeakIndexUpdateStatus", common)


if __name__ == "__main__":
    unittest.main()

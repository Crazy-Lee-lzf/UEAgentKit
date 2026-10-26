from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TEST_ROOT = TOOL_ROOT / "tests" / "python"
for path in (SRC_ROOT, TEST_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_indexer_queries import ASSET_A, make_asset, write_export  # noqa: E402
from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.index_snapshot import apply_editor_freshness, compute_index_snapshot  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.mcp_server import _project_status_response  # noqa: E402


def _asset(path: str) -> dict[str, Any]:
    return make_asset(path, profile="logic", revision="a" * 64, rich=False)


def _rewrite_manifest(export_root: Path, update) -> dict[str, Any]:
    path = export_root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    update(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


class SnapshotFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_snapshot_")
        self.root = Path(self.temporary.name)
        self.export_root = self.root / "export"
        self.database_path = self.root / "index" / "index.sqlite3"
        write_export(self.export_root, [_asset(ASSET_A)])
        _rewrite_manifest(self.export_root, lambda payload: payload.__setitem__("createdUtc", "2026.10.26-15.30.00"))
        with open_database(self.database_path) as connection:
            build_index(connection, self.export_root, self.database_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_cold_sqlite_is_unknown_but_export_snapshot_is_fresh(self) -> None:
        service = IndexQueryService(self.database_path)
        status = service.check()
        metadata = status["indexMetadata"]
        self.assertEqual(metadata["indexSnapshotState"], "unknown")
        self.assertEqual(metadata["exportSnapshotState"], "fresh")
        self.assertFalse(metadata["editorStateCompared"])
        self.assertIn("editor-state-not-compared", metadata["indexSnapshotReasons"])
        self.assertTrue(metadata["databasePathMatches"])
        self.assertTrue(metadata["manifestMatches"])

    def test_editor_fresh_attestation_can_make_snapshot_fresh(self) -> None:
        with open_database(self.database_path, readonly=True) as connection:
            snapshot = compute_index_snapshot(
                connection,
                self.database_path,
                editor_freshness={"state": "fresh"},
            )
        self.assertEqual(snapshot["state"], "fresh")
        self.assertEqual(snapshot["exportSnapshotState"], "fresh")
        self.assertTrue(snapshot["editorStateCompared"])

    def test_recreated_manifest_is_stale(self) -> None:
        _rewrite_manifest(
            self.export_root,
            lambda payload: payload.__setitem__("createdUtc", "2026.10.26-16.30.00"),
        )
        status = IndexQueryService(self.database_path).check()
        self.assertEqual(status["indexMetadata"]["indexSnapshotState"], "stale")
        self.assertEqual(status["indexMetadata"]["exportSnapshotState"], "stale")
        self.assertFalse(status["indexMetadata"]["manifestMatches"])
        self.assertIn("manifest-digest-mismatch", status["indexMetadata"]["indexSnapshotReasons"])

    def test_database_path_mismatch_is_stale_not_fresh(self) -> None:
        copied = self.root / "copied" / "index.sqlite3"
        copied.parent.mkdir()
        shutil.copy2(self.database_path, copied)
        status = IndexQueryService(copied).check()
        self.assertEqual(status["indexMetadata"]["indexSnapshotState"], "stale")
        self.assertFalse(status["indexMetadata"]["databasePathMatches"])
        self.assertIn("database-path-mismatch", status["indexMetadata"]["indexSnapshotReasons"])

    def test_missing_digest_provenance_is_unknown(self) -> None:
        with open_database(self.database_path) as connection:
            with connection:
                connection.execute("DELETE FROM metadata WHERE key IN ('last_manifest_sha256','last_export_utc')")
        status = IndexQueryService(self.database_path).check()
        self.assertEqual(status["indexMetadata"]["indexSnapshotState"], "unknown")
        self.assertEqual(status["indexMetadata"]["exportSnapshotState"], "unknown")
        self.assertIn("manifest-digest-provenance-missing", status["indexMetadata"]["indexSnapshotReasons"])
        self.assertIn("export-timestamp-provenance-missing", status["indexMetadata"]["indexSnapshotReasons"])

    def test_check_is_read_only_and_creates_no_freshness_cache(self) -> None:
        before_db = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
        before_manifest = hashlib.sha256((self.export_root / "manifest.json").read_bytes()).hexdigest()
        before_files = sorted(path.name for path in self.root.rglob("*") if path.is_file())
        IndexQueryService(self.database_path).check()
        after_db = hashlib.sha256(self.database_path.read_bytes()).hexdigest()
        after_manifest = hashlib.sha256((self.export_root / "manifest.json").read_bytes()).hexdigest()
        after_files = sorted(path.name for path in self.root.rglob("*") if path.is_file())
        self.assertEqual(before_db, after_db)
        self.assertEqual(before_manifest, after_manifest)
        self.assertEqual(before_files, after_files)

    def test_project_status_surfaces_snapshot_fields(self) -> None:
        service = IndexQueryService(self.database_path)
        status = _project_status_response(service, None, None, None)
        self.assertEqual(status["database"]["indexSnapshotState"], "unknown")
        self.assertEqual(status["database"]["exportSnapshotState"], "fresh")
        self.assertEqual(status["indexSnapshot"]["exportSnapshotState"], "fresh")
        self.assertIn("indexSnapshotReasons", status["database"])

    def test_stale_editor_state_overrides_export_freshness(self) -> None:
        with open_database(self.database_path, readonly=True) as connection:
            snapshot = compute_index_snapshot(connection, self.database_path)
        merged = apply_editor_freshness(snapshot, {"state": "stale"})
        self.assertEqual(merged["state"], "stale")
        self.assertIn("editor-package-revision-stale", merged["reasons"])


if __name__ == "__main__":
    unittest.main()

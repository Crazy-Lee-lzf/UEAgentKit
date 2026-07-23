from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from test_indexer_queries import ASSET_A, make_asset, write_export  # noqa: E402
from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.freshness import IndexFreshnessTracker  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402


def revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def package_path(project_path: Path, asset_path: str) -> Path:
    package_name = asset_path.split(".", 1)[0]
    relative = package_name.removeprefix("/Game/")
    return project_path.parent / "Content" / Path(*relative.split("/")).with_suffix(".uasset")


class FreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_freshness_")
        self.root = Path(self.temporary.name)
        self.project_path = self.root / "Project" / "测试项目.uproject"
        self.project_path.parent.mkdir(parents=True)
        self.project_path.write_text("{}", encoding="utf-8")
        self.export_root = self.root / "RevisionExport"
        self.database_path = self.root / "index" / "ueak.sqlite3"
        self.before_bytes = b"fresh-package-before"
        self.before_revision = revision(self.before_bytes)
        asset = make_asset(
            ASSET_A,
            profile="logic",
            revision=self.before_revision,
            rich=True,
            project_name=self.project_path.stem,
        )
        write_export(self.export_root, [asset])
        self.package_path = package_path(self.project_path, ASSET_A)
        self.package_path.parent.mkdir(parents=True)
        self.package_path.write_bytes(self.before_bytes)
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
        self.assertEqual((result.added, result.failed), (1, 0))
        self.service = IndexQueryService(self.database_path)
        self.tracker = IndexFreshnessTracker(self.service, self.project_path, self.export_root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_project_and_asset_are_fresh_when_all_revisions_match(self) -> None:
        asset = self.tracker.inspect_asset(ASSET_A)
        project = self.tracker.project_status()
        self.assertEqual(asset["state"], "fresh")
        self.assertTrue(asset["indexFresh"])
        self.assertEqual(project["state"], "fresh")
        self.assertEqual(project["freshAssetCount"], 1)
        self.assertEqual(project["staleAssetCount"], 0)

    def test_disk_change_and_export_change_are_reported_separately(self) -> None:
        self.package_path.write_bytes(b"changed-on-disk")
        disk_stale = self.tracker.inspect_asset(ASSET_A)
        self.assertEqual(disk_stale["state"], "stale")
        self.assertIn("index-disk-mismatch", disk_stale["reason"])
        self.assertFalse(disk_stale["comparisons"]["indexMatchesDisk"])

        self.package_path.write_bytes(self.before_bytes)
        canonical_path = next((self.export_root / "canonical").rglob("*.json"))
        canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
        canonical["revision"]["value"] = "sha256:" + "f" * 64
        canonical_path.write_text(
            json.dumps(canonical, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\r\n",
        )
        export_stale = self.tracker.inspect_asset(ASSET_A)
        self.assertEqual(export_stale["state"], "stale")
        self.assertIn("index-revision-export-mismatch", export_stale["reason"])
        self.assertFalse(export_stale["comparisons"]["indexMatchesRevisionExport"])

    def test_missing_package_is_unavailable_not_fresh(self) -> None:
        self.package_path.unlink()
        asset = self.tracker.inspect_asset(ASSET_A)
        project = self.tracker.project_status()
        self.assertEqual(asset["state"], "unavailable")
        self.assertIsNone(asset["indexFresh"])
        self.assertIn("package-file-missing", asset["reason"])
        self.assertEqual(project["state"], "unavailable")
        self.assertEqual(project["unavailableAssetCount"], 1)

    def test_commit_marks_stale_and_exact_rollback_clears_session_state(self) -> None:
        after_bytes = b"fresh-package-after"
        after_revision = "sha256:" + revision(after_bytes)
        self.package_path.write_bytes(after_bytes)
        transition = self.tracker.mark_commit(
            ASSET_A,
            "sha256:" + self.before_revision,
            after_revision,
        )
        self.assertEqual(transition["state"], "stale")
        self.assertEqual(len(self.tracker.session_stale_assets()), 1)
        self.assertEqual(self.tracker.inspect_asset(ASSET_A)["state"], "stale")

        self.package_path.write_bytes(self.before_bytes)
        restored = self.tracker.mark_rollback(ASSET_A, "sha256:" + self.before_revision)
        self.assertEqual(restored["state"], "fresh")
        self.assertTrue(restored["sessionStateCleared"])
        self.assertEqual(self.tracker.session_stale_assets(), [])

    def test_unindexed_asset_is_unavailable(self) -> None:
        result = self.tracker.inspect_asset("/Game/Missing/DA_None.DA_None")
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(result["reason"], "asset-not-indexed")


if __name__ == "__main__":
    unittest.main()

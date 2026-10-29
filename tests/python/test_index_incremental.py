from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TEST_ROOT = TOOL_ROOT / "tests" / "python"
for path in (SRC_ROOT, TEST_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from test_indexer_queries import ASSET_A, ASSET_B, make_asset, write_export  # noqa: E402
from test_world_index_v2 import load_world_document  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index, read_changed_path_attestation  # noqa: E402


def _asset(path: str, revision: str = "a" * 64, profile: str = "logic") -> dict[str, Any]:
    return make_asset(path, profile=profile, revision=revision, rich=False)


def _manifest_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rewrite_manifest(export_root: Path, update) -> dict[str, Any]:
    path = export_root / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    update(manifest)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


class IncrementalBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_incremental_")
        self.root = Path(self.temporary.name)
        self.export_root = self.root / "export"
        self.database_path = self.root / "index" / "index.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_batched_writes_use_bounded_commits_and_savepoints(self) -> None:
        assets = [_asset(f"/Game/Batch/BP_{i}.BP_{i}") for i in range(5)]
        write_export(self.export_root, assets)
        statements: list[str] = []
        with open_database(self.database_path) as connection:
            connection.set_trace_callback(lambda sql: statements.append(sql.strip().split(None, 1)[0].upper()))
            result = build_index(connection, self.export_root, self.database_path, batch_size=2)
            count = connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0]
        self.assertEqual((result.added, result.failed, count), (5, 0, 5))
        self.assertLessEqual(statements.count("COMMIT"), 4)
        self.assertGreaterEqual(statements.count("SAVEPOINT"), 5)
        self.assertGreaterEqual(statements.count("RELEASE"), 5)

    def test_asset_failure_rolls_back_only_its_savepoint(self) -> None:
        assets = [_asset("/Game/Batch/BP_Good.BP_Good"), _asset("/Game/Batch/BP_Bad.BP_Bad")]
        write_export(self.export_root, assets)
        original_insert = sys.modules["ue_agent_kit.indexer"]._insert_references

        def failing_insert(connection, asset_id, canonical):
            if canonical.get("assetPath") == "/Game/Batch/BP_Bad.BP_Bad":
                raise RuntimeError("synthetic reference failure")
            return original_insert(connection, asset_id, canonical)

        with open_database(self.database_path) as connection:
            with mock.patch("ue_agent_kit.indexer._insert_references", side_effect=failing_insert):
                result = build_index(connection, self.export_root, self.database_path)
            remaining = connection.execute("SELECT asset_path FROM assets ORDER BY asset_path").fetchall()
            failed_symbols = connection.execute(
                "SELECT COUNT(*) FROM symbols WHERE asset_id IN (SELECT id FROM assets WHERE asset_path = ?)",
                ("/Game/Batch/BP_Bad.BP_Bad",),
            ).fetchone()[0]
        self.assertEqual((result.added, result.failed), (1, 1))
        self.assertEqual([row[0] for row in remaining], ["/Game/Batch/BP_Good.BP_Good"])
        self.assertEqual(failed_symbols, 0)

    def test_broken_recorded_paths_scan_canonical_tree_once_per_build(self) -> None:
        assets = [_asset(f"/Game/Batch/BP_{i}.BP_{i}") for i in range(8)]
        write_export(self.export_root, assets)
        _rewrite_manifest(
            self.export_root,
            lambda payload: [entry.__setitem__("jsonPath", "") for entry in payload["assets"]],
        )
        reads: list[Path] = []
        original_load = sys.modules["ue_agent_kit.indexer"]._load_json

        def counted_load(path: Path):
            if "canonical" in path.parts:
                reads.append(path)
            return original_load(path)

        with open_database(self.database_path) as connection:
            with mock.patch("ue_agent_kit.indexer._load_json", side_effect=counted_load):
                result = build_index(connection, self.export_root, self.database_path)
        self.assertEqual((result.added, result.failed), (8, 0))
        self.assertLessEqual(len(reads), 2 * len(assets) + 1)

    def test_world_failure_rolls_back_asset_and_world_rows_together(self) -> None:
        world = load_world_document()
        generic = make_asset(
            "/Game/Batch/BP_Good.BP_Good",
            profile="logic",
            revision="a" * 64,
            rich=False,
            project_name=str(world["projectName"]),
        )
        write_export(self.export_root, [generic, world])
        original_insert = sys.modules["ue_agent_kit.world_index_v2"]._insert_row

        def failing_insert(connection: sqlite3.Connection, table: str, *args: Any, **kwargs: Any) -> None:
            if table == "world_v2_actors":
                raise RuntimeError("synthetic world actor failure")
            original_insert(connection, table, *args, **kwargs)

        with open_database(self.database_path) as connection:
            with mock.patch("ue_agent_kit.world_index_v2._insert_row", side_effect=failing_insert):
                result = build_index(connection, self.export_root, self.database_path)
            asset_paths = [row[0] for row in connection.execute("SELECT asset_path FROM assets")]
            has_world_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'world_v2_worlds'"
            ).fetchone() is not None
            world_rows = (
                connection.execute("SELECT COUNT(*) FROM world_v2_worlds").fetchone()[0]
                if has_world_table
                else 0
            )
        self.assertEqual((result.added, result.failed), (1, 1))
        self.assertEqual(asset_paths, ["/Game/Batch/BP_Good.BP_Good"])
        self.assertEqual(world_rows, 0)

    def test_changed_path_fast_path_reads_only_listed_assets(self) -> None:
        assets = [_asset(f"/Game/Batch/BP_{i}.BP_{i}") for i in range(3)]
        write_export(self.export_root, assets)
        with open_database(self.database_path) as connection:
            first = build_index(connection, self.export_root, self.database_path)
            manifest_sha = _manifest_sha(self.export_root / "manifest.json")
            changed = {"/Game/Batch/BP_1.BP_1"}
            reads: list[Path] = []
            original_load = sys.modules["ue_agent_kit.indexer"]._load_json

            def counted_load(path: Path):
                if "canonical" in path.parts:
                    reads.append(path)
                return original_load(path)

            with mock.patch("ue_agent_kit.indexer._load_json", side_effect=counted_load):
                second = build_index(
                    connection,
                    self.export_root,
                    self.database_path,
                    changed_asset_paths=changed,
                    expected_manifest_sha256=manifest_sha,
                    changed_path_source="unit-test",
                )
        self.assertEqual((first.added, first.failed), (3, 0))
        self.assertEqual((second.added, second.updated, second.skipped), (0, 0, 3))
        self.assertEqual(second.fast_path_skips, 2)
        self.assertEqual(second.strict_full_checks, 1)
        self.assertEqual(len(reads), 1)

    def test_invalid_changed_path_provenance_fails_closed(self) -> None:
        write_export(self.export_root, [_asset(ASSET_A)])
        with open_database(self.database_path) as connection:
            with self.assertRaises(RuntimeError):
                build_index(
                    connection,
                    self.export_root,
                    self.database_path,
                    changed_asset_paths={ASSET_A},
                    expected_manifest_sha256="0" * 64,
                )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0], 0)

    def test_strict_mode_still_detects_unlisted_canonical_change(self) -> None:
        write_export(self.export_root, [_asset(ASSET_A), _asset(ASSET_B)])
        with open_database(self.database_path) as connection:
            build_index(connection, self.export_root, self.database_path)
            canonical = next((self.export_root / "canonical").rglob("*.json"))
            data = json.loads(canonical.read_text(encoding="utf-8-sig"))
            data["revision"]["value"] = "b" * 64
            canonical.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            strict = build_index(connection, self.export_root, self.database_path)
        self.assertEqual((strict.updated, strict.skipped, strict.failed), (1, 1, 0))
        self.assertEqual(strict.fast_path_skips, 0)
        self.assertEqual(strict.strict_full_checks, 2)

    def test_manifest_digest_fast_path_skips_only_entries_with_attestation(self) -> None:
        assets = [_asset(f"/Game/Batch/BP_{i}.BP_{i}") for i in range(3)]
        write_export(self.export_root, assets)
        with open_database(self.database_path) as connection:
            build_index(connection, self.export_root, self.database_path)
            _rewrite_manifest(
                self.export_root,
                lambda payload: payload["assets"][0].__setitem__(
                    "canonicalSha256",
                    hashlib.sha256(Path(payload["assets"][0]["jsonPath"]).read_bytes()).hexdigest(),
                ),
            )
            digest = _manifest_sha(self.export_root / "manifest.json")
            reads: list[Path] = []
            original_load = sys.modules["ue_agent_kit.indexer"]._load_json

            def counted_load(path: Path):
                if "canonical" in path.parts:
                    reads.append(path)
                return original_load(path)

            with mock.patch("ue_agent_kit.indexer._load_json", side_effect=counted_load):
                result = build_index(connection, self.export_root, self.database_path)
        self.assertEqual(result.fast_path_skips, 1)
        self.assertEqual(result.strict_full_checks, 2)
        self.assertEqual(len(reads), 2)
        self.assertTrue(digest)

    def test_manifest_profile_change_disables_fast_skip(self) -> None:
        asset = _asset("/Game/Batch/BP_Profile.BP_Profile")
        write_export(self.export_root, [asset])
        with open_database(self.database_path) as connection:
            initial = build_index(connection, self.export_root, self.database_path)
            self.assertEqual(initial.added, 1)
            _rewrite_manifest(
                self.export_root,
                lambda payload: (
                    payload.__setitem__("profile", "different-export-profile"),
                    payload["assets"][0].__setitem__(
                        "canonicalSha256",
                        hashlib.sha256(Path(payload["assets"][0]["jsonPath"]).read_bytes()).hexdigest(),
                    ),
                ),
            )
            second = build_index(connection, self.export_root, self.database_path)
        self.assertEqual(second.fast_path_skips, 0)
        self.assertEqual(second.strict_full_checks, 1)
        self.assertEqual(second.skipped, 1)

    def test_read_changed_path_attestation_requires_provenance(self) -> None:
        attestation = self.root / "changed.json"
        attestation.write_text(json.dumps({"changedAssetPaths": [ASSET_A]}), encoding="utf-8")
        with self.assertRaises(ValueError):
            read_changed_path_attestation(attestation, export_root=self.export_root)
        attestation.write_text(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "manifestSha256": "a" * 64,
                    "exportRoot": str(self.export_root),
                    "changedAssetPaths": [ASSET_A],
                }
            ),
            encoding="utf-8",
        )
        paths, digest, _ = read_changed_path_attestation(attestation, export_root=self.export_root)
        self.assertEqual(paths, {ASSET_A})
        self.assertEqual(digest, "a" * 64)


if __name__ == "__main__":
    unittest.main()

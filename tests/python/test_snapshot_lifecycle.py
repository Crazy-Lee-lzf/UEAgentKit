from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.snapshot_lifecycle import (  # noqa: E402
    SnapshotLifecycleError,
    freeze_active_snapshot,
    resolve_active_snapshot,
    sha256_file,
    write_active_pointer,
)


PROJECT = "TestProject"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\r\n")


class SnapshotLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_snapshot_")
        self.root = Path(self.temporary.name)
        self.database = self.root / "active.sqlite3"
        self.database.write_bytes(b"database-v1")
        self.revision_export = self.root / "revision-export"
        write_json(self.revision_export / "manifest.json", {"projectName": PROJECT})
        write_json(self.revision_export / "canonical" / "asset.json", {"assetPath": "/Game/Test.Test"})
        self.work_root = self.root / "Output" / "McpWorkflow"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_legacy_snapshot_freezes_database_and_export_bytes(self) -> None:
        active = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        self.assertTrue(active.legacy)
        frozen = freeze_active_snapshot(active)
        try:
            self.database.write_bytes(b"database-v2")
            write_json(self.revision_export / "canonical" / "asset.json", {"assetPath": "/Game/Changed.Changed"})
            self.assertEqual(frozen.database.read_bytes(), b"database-v1")
            frozen_canonical = json.loads(
                (frozen.revision_export / "canonical" / "asset.json").read_text(encoding="utf-8")
            )
            self.assertEqual(frozen_canonical["assetPath"], "/Game/Test.Test")
        finally:
            frozen.cleanup()
        self.assertFalse(frozen.root.exists())

    def test_atomic_pointer_resolves_one_paired_generation(self) -> None:
        active = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        generation_id = "gen_20260724T120000Z_123456abcdef"
        generation_root = self.work_root / "snapshots" / generation_id
        generation_database = generation_root / "index.sqlite3"
        generation_database.parent.mkdir(parents=True)
        generation_database.write_bytes(b"database-v2")
        generation_export = generation_root / "revision-export"
        write_json(generation_export / "manifest.json", {"projectName": PROJECT, "generation": 2})
        write_json(generation_export / "canonical" / "asset.json", {"assetPath": "/Game/Test.Test"})
        write_active_pointer(
            active,
            generation_id=generation_id,
            database_sha256=sha256_file(generation_database),
            revision_export_manifest_sha256=sha256_file(generation_export / "manifest.json"),
            refreshed_asset_path="/Game/Test.Test",
            refreshed_revision="sha256:" + "a" * 64,
        )
        resolved = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        self.assertFalse(resolved.legacy)
        self.assertEqual(resolved.generation_id, generation_id)
        self.assertEqual(resolved.database, generation_database.resolve())
        self.assertEqual(resolved.revision_export, generation_export.resolve())
        frozen = freeze_active_snapshot(resolved)
        self.assertFalse(frozen.owns_copy)
        self.assertEqual(frozen.database, generation_database.resolve())
        frozen.cleanup()
        self.assertTrue(generation_database.is_file())

    def test_pointer_hash_and_configuration_mismatch_are_rejected(self) -> None:
        active = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        generation_id = "gen_20260724T120000Z_abcdef123456"
        generation_root = self.work_root / "snapshots" / generation_id
        generation_database = generation_root / "index.sqlite3"
        generation_database.parent.mkdir(parents=True)
        generation_database.write_bytes(b"database-v2")
        generation_export = generation_root / "revision-export"
        write_json(generation_export / "manifest.json", {"projectName": PROJECT})
        write_active_pointer(
            active,
            generation_id=generation_id,
            database_sha256=sha256_file(generation_database),
            revision_export_manifest_sha256=sha256_file(generation_export / "manifest.json"),
            refreshed_asset_path="/Game/Test.Test",
            refreshed_revision="sha256:" + "a" * 64,
        )
        generation_database.write_bytes(b"tampered")
        with self.assertRaisesRegex(SnapshotLifecycleError, "hash"):
            resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)

        generation_database.write_bytes(b"database-v2")
        pointer = json.loads(active.pointer_path.read_text(encoding="utf-8"))
        pointer["configurationKey"] = "sha256:" + "0" * 64
        write_json(active.pointer_path, pointer)
        with self.assertRaisesRegex(SnapshotLifecycleError, "does not match"):
            resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)

    def test_startup_freeze_retries_transient_replace_lock(self) -> None:
        active = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        self.assertTrue(active.legacy)
        import ue_agent_kit.snapshot_lifecycle as lifecycle

        real_replace = lifecycle.os.replace
        calls: list[int] = []

        def flaky_replace(source: str, target: str) -> None:
            calls.append(1)
            if len(calls) == 1:
                raise PermissionError("WinError 5: access denied on a freshly copied SQLite tree")
            return real_replace(source, target)

        with mock.patch.object(lifecycle.os, "replace", flaky_replace):
            frozen = freeze_active_snapshot(active)
        try:
            self.assertGreaterEqual(len(calls), 2)
            self.assertEqual(frozen.database.read_bytes(), b"database-v1")
        finally:
            frozen.cleanup()

    def test_startup_freeze_gives_up_after_bounded_retries(self) -> None:
        active = resolve_active_snapshot(self.database, self.revision_export, self.work_root, PROJECT)
        self.assertTrue(active.legacy)
        import ue_agent_kit.snapshot_lifecycle as lifecycle

        def always_fail_replace(_: str, __: str) -> None:
            raise PermissionError("WinError 5: persistent lock")

        with mock.patch.object(lifecycle.os, "replace", always_fail_replace):
            with self.assertRaises(PermissionError):
                freeze_active_snapshot(active)
        self.assertFalse(list((self.work_root / "sessions").glob("session_*")))
        self.assertFalse(list((self.work_root / "sessions").glob(".*.staging")))


if __name__ == "__main__":
    unittest.main()

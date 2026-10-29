"""Regression tests for safe, tracked-only public release curation."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "CuratePublicRelease.py"
SPEC = importlib.util.spec_from_file_location("ueak_curate_public", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
curate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(curate)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


class CuratedReleaseSafetyTests(unittest.TestCase):
    def test_protected_paths_rejected_before_io(self) -> None:
        for path in (
            ".git", ".git/config", "../other", "/absolute", "AutoSDK/tool/cl.exe",
            "Build/Compiled/a.dll", ".venv/Scripts/python.exe", "Output/a",
            "docs/../src/a.py", "docs//item.md", "dir\\nested", "bad:stream",
        ):
            with self.subTest(path=path):
                with self.assertRaises(curate.UnsafeReleasePath):
                    curate.validate_relative(path)

    def test_reparse_ancestor_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "linked").mkdir()
            candidate = root / "linked" / "child"
            original = Path.is_junction
            def simulated(self: Path) -> bool:
                return self.name == "linked" or original(self)
            with patch.object(Path, "is_junction", simulated):
                with self.assertRaises(curate.UnsafeReleasePath):
                    curate.reject_reparse(candidate)

    @unittest.skipUnless((ROOT / "AutoSDK").exists(), "No legacy SDK directory available")
    def test_existing_sdk_reparse_point_is_rejected_if_present(self) -> None:
        # Never read, enumerate or remove target contents.
        sdk = ROOT / "AutoSDK"
        for child in sdk.glob("HostWin64/Win64/VS2022/*"):
            if child.is_junction() or child.is_symlink():
                with self.assertRaises(curate.UnsafeReleasePath):
                    curate.reject_reparse(child / "bin" / "Hostx64" / "x64" / "cl.exe")
                return
        self.skipTest("No SDK reparse point available")

    def test_only_tracked_files_are_modified_and_git_metadata_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp) / "repo"
            r.mkdir()
            _git(r, "init", "-q")
            _git(r, "config", "user.name", "Release Safety Test")
            _git(r, "config", "user.email", "noreply@example.invalid")
            (r / ".gitignore").write_text("AutoSDK/\n.venv/\n", encoding="utf-8")
            (r / "src").mkdir()
            (r / "src" / "value.txt").write_text("old", encoding="utf-8")
            (r / "obsolete.txt").write_text("delete only this tracked file", encoding="utf-8")
            (r / "docs").mkdir()
            (r / "docs" / "guide.md").write_text("old doc", encoding="utf-8")
            _git(r, "add", ".")
            _git(r, "commit", "-qm", "public baseline")
            (r / "src" / "value.txt").write_text("new source", encoding="utf-8")
            _git(r, "commit", "-qam", "candidate code")
            source_ref = _git(r, "rev-parse", "HEAD")
            (r / "docs" / "guide.md").write_text("new docs", encoding="utf-8")
            _git(r, "commit", "-qam", "candidate docs")
            docs_ref = _git(r, "rev-parse", "HEAD")
            _git(r, "checkout", "-q", source_ref)
            (r / "AutoSDK" / "HostWin64").mkdir(parents=True)
            (r / "AutoSDK" / "HostWin64" / "cl.exe").write_text("protected", encoding="utf-8")
            (r / ".venv").mkdir()
            (r / ".venv" / "python.exe").write_text("ignored", encoding="utf-8")
            manifest = {"files": [
                {"path": ".gitignore", "status": "KEEP"},
                {"path": "src/value.txt", "status": "KEEP"},
                {"path": "docs/guide.md", "status": "KEEP"},
                {"path": "obsolete.txt", "status": "EXCLUDE"},
            ]}
            mf = Path(tmp) / "manifest.json"
            mf.write_text(json.dumps(manifest), encoding="utf-8")
            dry = curate.curate(r, mf, source_ref, docs_ref, set(), False)
            self.assertFalse(dry["applied"])
            self.assertTrue((r / "obsolete.txt").exists())
            # A changed but tracked tree is not clean; dry-run leaves it clean.
            applied = curate.curate(r, mf, source_ref, docs_ref, set(), True)
            self.assertTrue(applied["applied"])
            self.assertFalse((r / "obsolete.txt").exists())
            self.assertEqual((r / "docs" / "guide.md").read_text(), "new docs")
            self.assertEqual((r / "AutoSDK" / "HostWin64" / "cl.exe").read_text(), "protected")
            self.assertEqual((r / ".venv" / "python.exe").read_text(), "ignored")
            self.assertTrue((r / ".git").exists())
            self.assertEqual((r / "src" / "value.txt").read_text(), "new source")

    def test_real_git_linked_worktree_pointer_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            linked = Path(tmp) / "linked"
            base.mkdir()
            _git(base, "init", "-q")
            _git(base, "config", "user.name", "Release Safety Test")
            _git(base, "config", "user.email", "noreply@example.invalid")
            (base / ".gitignore").write_text("AutoSDK/\n.venv/\n", encoding="utf-8")
            (base / "keep.txt").write_text("known-good", encoding="utf-8")
            (base / "obsolete.txt").write_text("delete tracked", encoding="utf-8")
            _git(base, "add", ".")
            _git(base, "commit", "-qm", "baseline")
            _git(base, "worktree", "add", "-q", "-b", "curation-test", str(linked), "HEAD")
            pointer = linked / ".git"
            self.assertTrue(pointer.is_file())
            original_pointer = pointer.read_bytes()
            (linked / "AutoSDK").mkdir()
            (linked / "AutoSDK" / "cl.exe").write_text("must-survive", encoding="utf-8")
            mf = Path(tmp) / "manifest.json"
            mf.write_text(json.dumps({"files": [
                {"path": ".gitignore", "status": "KEEP"},
                {"path": "keep.txt", "status": "KEEP"},
                {"path": "obsolete.txt", "status": "EXCLUDE"},
            ]}), encoding="utf-8")
            current = _git(linked, "rev-parse", "HEAD")
            curate.curate(linked, mf, current, current, set(), True)
            self.assertEqual(pointer.read_bytes(), original_pointer)
            self.assertEqual((linked / "AutoSDK" / "cl.exe").read_text(), "must-survive")
            self.assertFalse((linked / "obsolete.txt").exists())

    def test_untracked_collision_fails_before_any_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = Path(tmp) / "repo"
            r.mkdir()
            _git(r, "init", "-q")
            _git(r, "config", "user.name", "Release Safety Test")
            _git(r, "config", "user.email", "noreply@example.invalid")
            (r / ".gitignore").write_text("new.txt\n", encoding="utf-8")
            _git(r, "add", ".")
            _git(r, "commit", "-qm", "baseline")
            base = _git(r, "rev-parse", "HEAD")
            # Source tree contains the file, but the destination considers it ignored.
            (r / "new.txt").write_text("source blob", encoding="utf-8")
            _git(r, "add", "-f", "new.txt")
            _git(r, "commit", "-qm", "source")
            source = _git(r, "rev-parse", "HEAD")
            _git(r, "checkout", "-q", base)
            (r / "new.txt").write_text("not tracked", encoding="utf-8")
            mf = Path(tmp) / "manifest.json"
            mf.write_text(json.dumps({"files": [
                {"path": ".gitignore", "status": "KEEP"},
                {"path": "new.txt", "status": "KEEP"},
            ]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                curate.curate(r, mf, source, source, set(), True)
            self.assertEqual((r / "new.txt").read_text(), "not tracked")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import contextlib
import io
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.cli import build_parser  # noqa: E402


class CliDuplicateArgumentTests(unittest.TestCase):
    def _reject(self, arguments: list[str]) -> str:
        parser = build_parser()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as raised:
                parser.parse_args(arguments)
        self.assertEqual(raised.exception.code, 2)
        return stderr.getvalue()

    def test_duplicate_database_is_rejected(self) -> None:
        message = self._reject(
            ["index", "build", "export", "--database", "a.db", "--database", "b.db"]
        )
        self.assertIn("--database: supplied more than once", message)
        self.assertIn("pass it exactly once", message)

    def test_duplicate_project_key_is_rejected(self) -> None:
        message = self._reject(
            ["index", "build", "export", "--project-key", "A", "--project-key", "B"]
        )
        self.assertIn("--project-key: supplied more than once", message)

    def test_single_values_and_repeatable_append_still_parse(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(
            ["index", "build", "export", "--database", "a.db", "--project-key", "Game"]
        )
        self.assertEqual(parsed.database, Path("a.db"))
        self.assertEqual(parsed.project_key, "Game")

        repeated = parser.parse_args(
            ["index", "code", "root", "--source-root", "Source", "--source-root", "Plugins"]
        )
        self.assertEqual(repeated.source_roots, ["Source", "Plugins"])

        flags = parser.parse_args(["--compact", "--compact", "index", "stats"])
        self.assertTrue(flags.compact)


if __name__ == "__main__":
    unittest.main()

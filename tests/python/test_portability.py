from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]


class PortabilityTests(unittest.TestCase):
    def test_supported_python_version(self) -> None:
        self.assertGreaterEqual(sys.version_info[:2], (3, 11))
        self.assertLess(sys.version_info[:2], (3, 13))

    def test_pyproject_python_range(self) -> None:
        with (TOOL_ROOT / "pyproject.toml").open("rb") as stream:
            data = tomllib.load(stream)
        self.assertEqual(data["project"]["requires-python"], ">=3.11,<3.13")

    def test_runtime_lock_has_no_unpinned_packages(self) -> None:
        lines = (TOOL_ROOT / "requirements.lock").read_text(encoding="utf-8").splitlines()
        requirements = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
        for requirement in requirements:
            self.assertIn("==", requirement)

    def test_dev_lock_has_no_unpinned_packages(self) -> None:
        lines = (TOOL_ROOT / "requirements-dev.lock").read_text(encoding="utf-8").splitlines()
        requirements = [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]
        for requirement in requirements:
            self.assertIn("==", requirement)

    def test_unicode_json_and_sqlite_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bct_test_") as temporary_root:
            root = Path(temporary_root) / "中文项目" / "目录 with spaces"
            root.mkdir(parents=True)
            database = root / "索引.sqlite3"

            connection = sqlite3.connect(database)
            try:
                connection.execute("CREATE TABLE items (value TEXT NOT NULL)")
                connection.execute("INSERT INTO items(value) VALUES (?)", ("蓝图变量",))
                actual = connection.execute("SELECT value FROM items").fetchone()[0]
            finally:
                connection.close()

            self.assertEqual(actual, "蓝图变量")
            payload = {"项目": "中文项目", "路径": str(root)}
            self.assertEqual(json.loads(json.dumps(payload, ensure_ascii=False)), payload)


if __name__ == "__main__":
    unittest.main()

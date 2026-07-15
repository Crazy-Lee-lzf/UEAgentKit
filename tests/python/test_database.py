from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from blueprint_context_tool.database import get_schema_version, open_database  # noqa: E402
from blueprint_context_tool.schema import CURRENT_SCHEMA_VERSION  # noqa: E402


class DatabaseTests(unittest.TestCase):
    def test_migration_and_fts_triggers(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bct_db_") as temporary_root:
            database_path = Path(temporary_root) / "中文目录" / "索引.sqlite3"
            with open_database(database_path) as connection:
                self.assertEqual(get_schema_version(connection), CURRENT_SCHEMA_VERSION)
                connection.execute(
                    """
                    INSERT INTO assets(
                        asset_path,
                        asset_name,
                        package_name,
                        schema_version,
                        exporter_version,
                        profile,
                        canonical_sha256,
                        canonical_relpath,
                        indexed_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "/Game/中文/蓝图.蓝图",
                        "蓝图",
                        "/Game/中文/蓝图",
                        "1.1",
                        "0.2.2",
                        "index",
                        "abc",
                        "canonical/蓝图.json",
                        "2026-07-15T00:00:00.000Z",
                    ),
                )
                asset_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
                connection.execute(
                    """
                    INSERT INTO symbols(asset_id, stable_id, kind, name, symbol_asset_path)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (asset_id, "variable|/Game/中文/蓝图.蓝图|生命值", "variable", "生命值", "/Game/中文/蓝图.蓝图"),
                )
                connection.execute(
                    """
                    INSERT INTO nodes(asset_id, graph_guid, guid, object_name, node_class, title, comment)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (asset_id, "graph-guid", "node-guid", "K2Node_CallFunction_0", "/Script/Test", "设置生命值", "测试注释"),
                )
                connection.commit()

                asset_matches = connection.execute(
                    "SELECT asset_path FROM assets_fts WHERE assets_fts MATCH ?",
                    ("蓝图",),
                ).fetchall()
                symbol_matches = connection.execute(
                    "SELECT name FROM symbols_fts WHERE symbols_fts MATCH ?",
                    ("生命值",),
                ).fetchall()
                node_matches = connection.execute(
                    "SELECT title FROM nodes_fts WHERE nodes_fts MATCH ?",
                    ("设置生命值",),
                ).fetchall()

                self.assertEqual([row[0] for row in asset_matches], ["/Game/中文/蓝图.蓝图"])
                self.assertEqual([row[0] for row in symbol_matches], ["生命值"])
                self.assertEqual([row[0] for row in node_matches], ["设置生命值"])

                connection.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
                connection.commit()
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM assets_fts").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM symbols_fts").fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM nodes_fts").fetchone()[0], 0)

    def test_newer_schema_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="bct_db_newer_") as temporary_root:
            database_path = Path(temporary_root) / "newer.sqlite3"
            with open_database(database_path) as connection:
                connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
                connection.commit()

            with self.assertRaises(RuntimeError):
                with open_database(database_path):
                    pass


if __name__ == "__main__":
    unittest.main()

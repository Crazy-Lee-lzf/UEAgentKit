from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TEST_ROOT = Path(__file__).resolve().parent
for candidate in (str(SRC_ROOT), str(TEST_ROOT)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from test_index_cjk_v2 import (  # noqa: E402
    ASSET_A,
    _symbol,
    make_asset,
    write_export,
)
from ue_agent_kit.cli import build_parser, run  # noqa: E402
from ue_agent_kit.database import (  # noqa: E402
    apply_migrations,
    assert_index_schema_current,
    get_schema_version,
    open_database,
    read_database_schema_version,
    rebuild_index_atomically,
)
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.queries import search_symbols  # noqa: E402
from ue_agent_kit.schema import V1_SCHEMA_SQL  # noqa: E402


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _directory_snapshot(directory: Path) -> dict[str, tuple[int, str]]:
    snapshot: dict[str, tuple[int, str]] = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            snapshot[path.name] = (path.stat().st_size, _file_digest(path))
    return snapshot


def create_v1_fixture(path: Path, *, with_rows: bool = True) -> None:
    """Create a genuine schema-v1 database, optionally with CJK rows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.executescript(V1_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO schema_migrations(version, description, applied_at_utc) "
            "VALUES (1, 'legacy v1 fixture', '2026-10-01T00:00:00Z')"
        )
        connection.execute("PRAGMA user_version = 1")
        if with_rows:
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path, package_name, asset_name, asset_class, schema_version,
                    exporter_version, profile, canonical_sha256, canonical_relpath, indexed_at_utc
                ) VALUES (?, '/Game/中文/BP_Armor', 'BP_Armor', '/Script/Engine.Blueprint',
                          '1.1', '0.2.2', 'logic', 'legacy-sha', 'canonical/legacy.json',
                          '2026-10-01T00:00:00Z')
                """,
                (ASSET_A,),
            )
            asset_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
            connection.execute(
                "INSERT INTO symbols(asset_id, stable_id, kind, name, symbol_asset_path) "
                "VALUES (?, 'variable|legacy|v1', 'variable', '护甲升级链', ?)",
                (asset_id, ASSET_A),
            )
            connection.execute(
                "INSERT INTO nodes(asset_id, graph_guid, guid, object_name, node_class, title, comment) "
                "VALUES (?, 'graph-guid', 'node-guid', 'K2Node_0', '/Script/Graph', '甲护旧节点', '旧注释')",
                (asset_id,),
            )
        connection.commit()
    finally:
        connection.close()


def write_single_asset_export(root: Path) -> None:
    write_export(
        root,
        [
            make_asset(
                ASSET_A,
                symbols=[
                    _symbol("variable|ak47|v2", "AK47", ASSET_A, note="护甲升级链"),
                    _symbol("variable|armor|v2", "护甲升级链", ASSET_A),
                ],
            )
        ],
    )


class V1MigrationRejectionTests(unittest.TestCase):
    def test_v1_database_is_rejected_without_any_mutation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_v1_reject_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            create_v1_fixture(database_path)
            before_digest = _file_digest(database_path)
            before_snapshot = _directory_snapshot(root)

            with self.assertRaisesRegex(RuntimeError, r"requires a full rebuild") as raised:
                with open_database(database_path):
                    pass
            message = str(raised.exception)
            self.assertIn("INDEX_REBUILD_REQUIRED_FROM=2", message)
            self.assertIn("v2", message)
            self.assertIn("--rebuild", message)

            self.assertEqual(_file_digest(database_path), before_digest)
            self.assertEqual(_directory_snapshot(root), before_snapshot)
            self.assertFalse(Path(str(database_path) + "-wal").exists())
            self.assertFalse(Path(str(database_path) + "-shm").exists())

            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(int(connection.execute("PRAGMA user_version").fetchone()[0]), 1)
                symbol_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(symbols)")}
                node_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(nodes)")}
                self.assertNotIn("search_text", symbol_columns)
                self.assertNotIn("search_text", node_columns)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM symbols").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0], 1)
                self.assertEqual(
                    int(connection.execute("SELECT version FROM schema_migrations").fetchone()[0]),
                    1,
                )
            finally:
                connection.close()

    def test_empty_v1_database_is_also_rejected_untouched(self) -> None:
        # An empty v1 file is still v1. The build cannot safely distinguish a
        # newly generated v1 file from a production index, so it is rejected
        # rather than upgraded in place.
        with tempfile.TemporaryDirectory(prefix="ueak_index_v1_empty_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            create_v1_fixture(database_path, with_rows=False)
            before = _file_digest(database_path)
            with self.assertRaisesRegex(RuntimeError, "requires a full rebuild"):
                with open_database(database_path):
                    pass
            self.assertEqual(_file_digest(database_path), before)

    def test_unversioned_database_with_index_tables_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_unversioned_") as temporary_root:
            database_path = Path(temporary_root) / "index.sqlite3"
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("CREATE TABLE assets(id INTEGER PRIMARY KEY, asset_path TEXT)")
                connection.commit()
            finally:
                connection.close()
            before = _file_digest(database_path)
            with self.assertRaisesRegex(RuntimeError, "no schema version"):
                with open_database(database_path):
                    pass
            self.assertEqual(_file_digest(database_path), before)

    def test_newer_v3_database_fails_closed_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_v3_") as temporary_root:
            database_path = Path(temporary_root) / "index.sqlite3"
            with open_database(database_path):
                pass
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()
            before = _file_digest(database_path)
            with self.assertRaisesRegex(RuntimeError, "newer than supported"):
                with open_database(database_path):
                    pass
            self.assertEqual(_file_digest(database_path), before)
            self.assertEqual(read_database_schema_version(database_path), 3)

    def test_incomplete_v2_database_is_rejected_not_repaired(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_v2_partial_") as temporary_root:
            database_path = Path(temporary_root) / "index.sqlite3"
            with open_database(database_path):
                pass
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("DROP TRIGGER symbols_ai")
                connection.commit()
            finally:
                connection.close()
            before = _file_digest(database_path)
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                with open_database(database_path):
                    pass
            self.assertEqual(_file_digest(database_path), before)
            connection = sqlite3.connect(database_path)
            try:
                self.assertEqual(int(connection.execute("PRAGMA user_version").fetchone()[0]), 2)
                self.assertIsNone(
                    connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'symbols_ai'"
                    ).fetchone()
                )
            finally:
                connection.close()

    def test_apply_migrations_guards_before_any_schema_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_apply_guard_") as temporary_root:
            database_path = Path(temporary_root) / "index.sqlite3"
            create_v1_fixture(database_path)
            connection = sqlite3.connect(database_path)
            try:
                with self.assertRaisesRegex(RuntimeError, "requires a full rebuild"):
                    apply_migrations(connection)
                self.assertEqual(get_schema_version(connection), 1)
                symbol_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(symbols)")}
                self.assertNotIn("search_text", symbol_columns)
            finally:
                connection.close()

    def test_fresh_database_applies_only_the_complete_v2_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_fresh_v2_") as temporary_root:
            database_path = Path(temporary_root) / "index.sqlite3"
            with open_database(database_path) as connection:
                versions = [
                    int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")
                ]
                self.assertEqual(versions, [2])
                assert_index_schema_current(connection)

    def test_read_schema_version_never_creates_a_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_read_version_") as temporary_root:
            missing = Path(temporary_root) / "does-not-exist.sqlite3"
            self.assertIsNone(read_database_schema_version(missing))
            self.assertFalse(missing.exists())


class AtomicRebuildTests(unittest.TestCase):
    def test_v1_target_is_replaced_only_after_validation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_atomic_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            create_v1_fixture(database_path)
            export_root = root / "export"
            write_single_asset_export(export_root)

            result = rebuild_index_atomically(
                database_path,
                lambda connection: build_index(connection, export_root, database_path),
            )
            self.assertEqual(result.added, 1)
            self.assertFalse(Path(str(database_path) + "-wal").exists())
            self.assertFalse(
                (root / f".{database_path.name}.v2.rebuild.tmp").exists()
            )

            with open_database(database_path) as connection:
                self.assertEqual(get_schema_version(connection), 2)
                self.assertEqual(
                    {row["name"] for row in search_symbols(connection, "护甲")},
                    {"AK47", "护甲升级链"},
                )
                self.assertEqual(
                    [str(row[0]) for row in connection.execute("PRAGMA quick_check")],
                    ["ok"],
                )

    def test_failed_builder_leaves_target_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_atomic_fail_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            create_v1_fixture(database_path)
            before = _file_digest(database_path)
            staged_name = f".{database_path.name}.v2.rebuild.tmp"

            def _boom(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "INSERT INTO metadata(key, value) VALUES ('partial', 'write')"
                )
                raise RuntimeError("injected builder failure")

            with self.assertRaisesRegex(RuntimeError, "injected builder failure"):
                rebuild_index_atomically(database_path, _boom)
            self.assertEqual(_file_digest(database_path), before)
            self.assertFalse((root / staged_name).exists())
            self.assertFalse(Path(str(root / staged_name) + "-wal").exists())

    def test_failed_validation_leaves_target_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_atomic_validate_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            create_v1_fixture(database_path)
            export_root = root / "export"
            write_single_asset_export(export_root)
            before = _file_digest(database_path)

            def _reject(connection: sqlite3.Connection) -> None:
                raise RuntimeError("injected validation failure")

            with self.assertRaisesRegex(RuntimeError, "injected validation failure"):
                rebuild_index_atomically(
                    database_path,
                    lambda connection: build_index(connection, export_root, database_path),
                    validate=_reject,
                )
            self.assertEqual(_file_digest(database_path), before)
            self.assertFalse((root / f".{database_path.name}.v2.rebuild.tmp").exists())

    def test_newer_target_is_never_replaced(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_atomic_newer_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            with open_database(database_path):
                pass
            connection = sqlite3.connect(database_path)
            try:
                connection.execute("PRAGMA user_version = 3")
                connection.commit()
            finally:
                connection.close()
            before = _file_digest(database_path)
            with self.assertRaisesRegex(RuntimeError, "newer than supported"):
                rebuild_index_atomically(database_path, lambda connection: None)
            self.assertEqual(_file_digest(database_path), before)

    def test_cli_rebuild_flag_is_exposed_and_replaces_a_v1_database(self) -> None:
        parser = build_parser()
        parsed = parser.parse_args(
            ["index", "build", "export", "--database", "index.sqlite3", "--rebuild"]
        )
        self.assertTrue(parsed.rebuild)

        with tempfile.TemporaryDirectory(prefix="ueak_index_cli_rebuild_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            create_v1_fixture(database_path)
            export_root = root / "export"
            write_single_asset_export(export_root)

            arguments = parser.parse_args(
                [
                    "index",
                    "build",
                    str(export_root),
                    "--database",
                    str(database_path),
                    "--rebuild",
                ]
            )
            payload, exit_code = run(arguments)
            self.assertEqual(exit_code, 0, payload)
            self.assertEqual(payload["added"], 1)
            with open_database(database_path) as connection:
                self.assertEqual(get_schema_version(connection), 2)
                self.assertTrue(search_symbols(connection, "护甲"))

    def test_cli_without_rebuild_refuses_v1_before_writing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_cli_guard_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            create_v1_fixture(database_path)
            export_root = root / "export"
            write_single_asset_export(export_root)
            before = _file_digest(database_path)
            arguments = build_parser().parse_args(
                [
                    "index",
                    "build",
                    str(export_root),
                    "--database",
                    str(database_path),
                ]
            )
            with self.assertRaisesRegex(RuntimeError, "requires a full rebuild"):
                run(arguments)
            self.assertEqual(_file_digest(database_path), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)

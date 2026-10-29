from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.code_index import build_code_index  # noqa: E402
from ue_agent_kit.database import get_metadata, get_schema_version, open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.memory_schema import CURRENT_MEMORY_SCHEMA_VERSION  # noqa: E402
from ue_agent_kit.queries import _fts_phrase, _query_runs, search_assets, search_nodes, search_symbols  # noqa: E402
from ue_agent_kit.schema import (  # noqa: E402
    CURRENT_SCHEMA_VERSION,
    INDEX_REBUILD_REQUIRED_FROM,
    INDEX_SCHEMA_TAG,
)
from ue_agent_kit.text_search import (  # noqa: E402
    build_cjk_search_text,
    is_cjk_character,
    node_search_text,
    project_cjk_only,
    symbol_search_text,
)

PROJECT = "测试项目"
ASSET_A = "/Game/中文/BP_Armor.BP_Armor"
ASSET_B = "/Game/Other/BP_Other.BP_Other"
REVISION = "a" * 64
CJK_BOUNDARY = "\u3164"


def _symbol(
    stable_id: str,
    name: str,
    asset_path: str,
    *,
    kind: str = "variable",
    note: str = "",
    guid: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": stable_id,
        "kind": kind,
        "name": name,
        "assetPath": asset_path,
        "class": "/Script/Engine.Actor",
        "guid": guid,
    }
    if note:
        payload["note"] = note
    return payload


def _node(guid: str, title: str, comment: str, *, name: str = "K2Node_VariableSet_0") -> dict[str, Any]:
    return {
        "guid": guid,
        "name": name,
        "class": "/Script/BlueprintGraph.K2Node_VariableSet",
        "title": title,
        "comment": comment,
        "pins": [],
    }


def make_asset(
    asset_path: str,
    *,
    symbols: list[dict[str, Any]],
    nodes: list[dict[str, Any]] | None = None,
    revision: str = REVISION,
) -> dict[str, Any]:
    package_name, asset_name = asset_path.rsplit(".", 1)
    nodes = nodes or []
    graphs = [
        {
            "guid": "11111111-1111-1111-1111-111111111111",
            "name": "EventGraph",
            "kind": "uber",
            "schema": "/Script/BlueprintGraph.EdGraphSchema_K2",
            "nodes": nodes,
        }
    ] if nodes else []
    return {
        "schemaVersion": "1.1",
        "exporterVersion": "0.2.2",
        "engineVersion": "5.6.1-test",
        "profile": "logic",
        "projectName": PROJECT,
        "assetPath": asset_path,
        "packageName": package_name,
        "assetClass": "/Script/Engine.Blueprint",
        "blueprintType": "normal",
        "parentClass": "/Script/Engine.Actor",
        "generatedClass": f"{package_name}.{asset_name}_C",
        "skeletonGeneratedClass": f"{package_name}.SKEL_{asset_name}_C",
        "status": 0,
        "revision": {
            "strategy": "package-sha256-v1",
            "available": True,
            "packageDirty": False,
            "value": f"sha256:{revision}",
            "packageGuid": "55555555-5555-5555-5555-555555555555",
            "fileSize": 1234,
            "modifiedUtc": "2026-10-27T00:00:00.000Z",
            "contentSha256": revision,
        },
        "interfaces": [],
        "variables": [],
        "components": [],
        "functions": [],
        "graphs": graphs,
        "symbols": symbols,
        "references": [],
        "summary": {
            "variables": 0,
            "components": 0,
            "graphs": len(graphs),
            "nodes": len(nodes),
            "pins": 0,
            "links": 0,
            "symbols": len(symbols),
            "references": 0,
        },
    }


def write_export(root: Path, assets: list[dict[str, Any]]) -> None:
    canonical_root = root / "canonical"
    canonical_root.mkdir(parents=True, exist_ok=True)
    manifest_assets: list[dict[str, Any]] = []
    for asset in assets:
        relative = Path(asset["packageName"].lstrip("/"))
        canonical_path = canonical_root / relative.parent / f"{relative.name}_{asset['assetPath'].rsplit('.', 1)[1]}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(json.dumps(asset, ensure_ascii=False), encoding="utf-8")
        summary = asset["summary"]
        manifest_assets.append(
            {
                "assetPath": asset["assetPath"],
                "success": True,
                "jsonPath": str(canonical_path),
                "symbols": summary["symbols"],
                "nodes": summary["nodes"],
                "graphs": summary["graphs"],
                "references": 0,
                "variables": 0,
                "components": 0,
                "pins": 0,
                "links": 0,
            }
        )
    manifest = {
        "schemaVersion": "1.1",
        "exporterVersion": "0.2.2",
        "engineVersion": "5.6.1-test",
        "profile": "logic",
        "projectName": assets[0]["projectName"] if assets else "",
        "successCount": len(assets),
        "failureCount": 0,
        "assets": manifest_assets,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def cjk_search_fixture() -> list[dict[str, Any]]:
    asset_a = make_asset(
        ASSET_A,
        symbols=[
            _symbol(
                "variable|ak47|v1",
                "AK47",
                ASSET_A,
                note="护甲升级链共三级",
                guid="11111111-2222-3333-4444-555555555555",
            ),
            _symbol(
                "variable|armor-chain|v1",
                "护甲升级链",
                ASSET_A,
                guid="11111111-2222-3333-4444-666666666666",
            ),
            _symbol(
                "variable|armor-shield|v1",
                "甲护测试",
                ASSET_A,
                guid="11111111-2222-3333-4444-777777777777",
            ),
            _symbol(
                "variable|japanese|v1",
                "武器強化計画",
                ASSET_A,
                guid="11111111-2222-3333-4444-888888888888",
            ),
            _symbol(
                "variable|korean|v1",
                "방어구 강화",
                ASSET_A,
                guid="11111111-2222-3333-4444-999999999999",
            ),
            _symbol(
                "variable|boundary|v1",
                "测试甲",
                ASSET_A,
                note="护甲升级链",
                guid="11111111-2222-3333-4444-aaaaaaaaaaaa",
            ),
            _symbol(
                "variable|punctuation|v1",
                "设定",
                ASSET_A,
                note="护甲，升级链",
                guid="11111111-2222-3333-4444-bbbbbbbbbbbb",
            ),
            _symbol(
                "variable|cross-column|v1",
                "甲",
                ASSET_A,
                note="护甲升级链",
                guid="11111111-2222-3333-4444-cccccccccccc",
            ),
        ],
        nodes=[
            _node(
                "aaaaaaaa-1111-1111-1111-111111111111",
                "护甲升级链",
                "设置护甲数值，AK47",
            ),
            _node(
                "bbbbbbbb-1111-1111-1111-111111111111",
                "甲护节点",
                "甲护注释",
                name="K2Node_CallFunction_0",
            ),
            _node(
                "cccccccc-1111-1111-1111-111111111111",
                "Weapon Socket",
                "The weapon socket fix also updates the inventory module reference.",
                name="K2Node_CallFunction_1",
            ),
        ],
    )
    return [asset_a]


def build_fixture(root: Path) -> Path:
    database_path = root / "index.sqlite3"
    export_root = root / "export"
    write_export(export_root, cjk_search_fixture())
    with open_database(database_path) as connection:
        result = build_index(connection, export_root, database_path)
        if result.failed or result.errors:
            raise AssertionError(f"fixture build failed: {result.errors}")
    return database_path


def _names(rows: list[dict[str, Any]], key: str = "name") -> list[str]:
    return [str(row[key]) for row in rows]


class SchemaV2Tests(unittest.TestCase):
    def test_constants_declare_cjk_schema_v2_and_memory_v7_is_independent(self) -> None:
        self.assertEqual(CURRENT_SCHEMA_VERSION, 2)
        self.assertEqual(INDEX_REBUILD_REQUIRED_FROM, 2)
        self.assertEqual(INDEX_SCHEMA_TAG, "index-v2-cjk-fts")
        self.assertEqual(CURRENT_MEMORY_SCHEMA_VERSION, 7)

    def test_fresh_database_has_genuine_v2_shape_and_tags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_v2_schema_") as temporary_root:
            database_path = Path(temporary_root) / "data" / "索引.sqlite3"
            with open_database(database_path) as connection:
                self.assertEqual(get_schema_version(connection), 2)
                self.assertEqual(
                    [int(row[0]) for row in connection.execute("SELECT version FROM schema_migrations")],
                    [2],
                )
                symbol_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(symbols)")}
                node_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(nodes)")}
                self.assertIn("search_text", symbol_columns)
                self.assertIn("search_text", node_columns)
                asset_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(assets)")}
                self.assertNotIn("search_text", asset_columns)

                self.assertEqual(
                    [str(row[1]) for row in connection.execute("PRAGMA table_info(assets_fts)")],
                    ["asset_path", "asset_name", "package_name", "parent_class", "generated_class"],
                )
                self.assertEqual(
                    [str(row[1]) for row in connection.execute("PRAGMA table_info(symbols_fts)")],
                    [
                        "stable_id",
                        "name",
                        "kind",
                        "symbol_asset_path",
                        "class_path",
                        "details_json",
                        "search_text",
                    ],
                )
                self.assertEqual(
                    [str(row[1]) for row in connection.execute("PRAGMA table_info(nodes_fts)")],
                    [
                        "guid",
                        "title",
                        "object_name",
                        "node_class",
                        "comment",
                        "details_json",
                        "search_text",
                    ],
                )

                triggers = {
                    str(row[0]): str(row[1] or "")
                    for row in connection.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'")
                }
                for name in ("symbols_ai", "symbols_ad", "symbols_au", "nodes_ai", "nodes_ad", "nodes_au"):
                    self.assertIn("search_text", triggers[name], name)
                for name in ("assets_ai", "assets_ad", "assets_au"):
                    self.assertNotIn("search_text", triggers[name], name)

                self.assertEqual(get_metadata(connection, "database_schema_version"), "2")
                self.assertEqual(get_metadata(connection, "index_schema_tag"), INDEX_SCHEMA_TAG)
                self.assertEqual(
                    [str(row[0]) for row in connection.execute("PRAGMA quick_check")],
                    ["ok"],
                )
                # Asset Index schema v2 must never create Project Memory tables.
                table_names = {
                    str(row[0])
                    for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                self.assertNotIn("memory_records", table_names)
                self.assertNotIn("memory_schema_migrations", table_names)

    def test_reopen_is_idempotent_and_readonly_search_does_not_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_v2_reopen_") as temporary_root:
            database_path = build_fixture(Path(temporary_root))
            before = database_path.read_bytes()
            with open_database(database_path) as connection:
                first = _names(search_symbols(connection, "护甲"))
            with open_database(database_path, readonly=True, immutable=True) as frozen:
                second = _names(search_symbols(frozen, "护甲"))
                self.assertEqual(get_schema_version(frozen), 2)
            self.assertEqual(first, second)
            self.assertEqual(database_path.read_bytes(), before)
            raw = sqlite3.connect(database_path)
            try:
                self.assertEqual(
                    [int(row[0]) for row in raw.execute("SELECT version FROM schema_migrations")],
                    [2],
                )
            finally:
                raw.close()


class ProjectionTests(unittest.TestCase):
    def test_projection_keeps_only_cjk_and_never_duplicates_ascii(self) -> None:
        self.assertEqual(project_cjk_only("AK47护甲升级链，武器"), f"护 甲 升 级 链 {CJK_BOUNDARY} 武 器")
        self.assertEqual(project_cjk_only("护甲，升级"), f"护 甲 {CJK_BOUNDARY} 升 级")
        self.assertEqual(project_cjk_only("ＡＫ４７护甲"), "护 甲")
        self.assertEqual(project_cjk_only("ArmorUpgrade"), "")
        self.assertEqual(project_cjk_only("《》，。"), "")

        text = build_cjk_search_text("AK47", "护甲升级链", "Script", "武器系统")
        self.assertEqual(text, f"护 甲 升 级 链 {CJK_BOUNDARY} 武 器 系 统")
        self.assertNotIn("AK47", text)
        self.assertNotIn("Script", text)
        for token in text.split(" "):
            self.assertTrue(token == CJK_BOUNDARY or is_cjk_character(token), token)

    def test_field_boundary_is_only_used_between_non_empty_cjk_fields(self) -> None:
        self.assertEqual(build_cjk_search_text("护甲"), "护 甲")
        self.assertEqual(build_cjk_search_text("护甲", "AK47"), "护 甲")
        self.assertEqual(build_cjk_search_text("护甲", "", "武器"), f"护 甲 {CJK_BOUNDARY} 武 器")

    def test_symbol_and_node_projection_builders_cover_indexed_fields(self) -> None:
        symbol_text = symbol_search_text(
            stable_id="variable|/Game/中文/BP_Armor.BP_Armor|护甲",
            name="AK47",
            kind="variable",
            symbol_asset_path="/Game/中文/BP_Armor.BP_Armor",
            class_path="/Script/Engine.Actor",
            details_json='{"note":"护甲升级链"}',
        )
        self.assertIn("护 甲", symbol_text)
        self.assertIn("中 文", symbol_text)
        self.assertNotIn("AK47", symbol_text)
        self.assertNotIn("Engine", symbol_text)

        node_text = node_search_text(
            guid="guid",
            title="护甲节点",
            object_name="K2Node_0",
            node_class="/Script/BlueprintGraph.K2Node",
            comment="设置护甲数值",
            details_json='{"title":"护甲节点"}',
        )
        self.assertIn("护 甲 节 点", node_text)
        self.assertIn("设 置 护 甲 数 值", node_text)

    def test_query_runs_and_fts_phrase_are_symmetric_with_projection(self) -> None:
        self.assertEqual(_query_runs("ＡＫ４７ 护甲"), ("AK47", "护甲"))
        self.assertEqual(_fts_phrase("护甲"), '"护 甲"')
        self.assertEqual(_fts_phrase("甲护"), '"甲 护"')
        self.assertEqual(_fts_phrase("AK47护甲"), '"AK47" AND "护 甲"')
        self.assertEqual(_fts_phrase("护甲升级链"), '"护 甲 升 级 链"')
        # Pure ASCII behavior is unchanged, including multi-word phrase form.
        self.assertEqual(_fts_phrase("AK47"), '"AK47"')
        self.assertEqual(_fts_phrase("weapon socket"), '"weapon socket"')
        self.assertEqual(_fts_phrase('say "hi"'), '"say ""hi"""')


class CjkIndexSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_index_v2_search_")
        self.root = Path(self.temporary.name)
        self.database_path = build_fixture(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_search_text_is_persisted_cjk_only_in_both_tables(self) -> None:
        with open_database(self.database_path) as connection:
            symbol_rows = connection.execute(
                "SELECT name, search_text FROM symbols ORDER BY name"
            ).fetchall()
            node_rows = connection.execute(
                "SELECT title, search_text FROM nodes ORDER BY title"
            ).fetchall()
        self.assertTrue(symbol_rows)
        self.assertTrue(node_rows)
        for name, text in symbol_rows + node_rows:
            text = str(text)
            self.assertEqual(text, text.strip(), name)
            self.assertNotIn("  ", text, name)
            for token in text.split(" "):
                if not token:
                    continue
                self.assertTrue(token == CJK_BOUNDARY or is_cjk_character(token), (name, token))
        symbol_text = {str(name): str(text) for name, text in symbol_rows}
        node_text = {str(title): str(text) for title, text in node_rows}
        self.assertIn("护 甲 升 级 链", symbol_text["护甲升级链"])
        self.assertNotIn("AK47", symbol_text["AK47"])
        self.assertIn("护 甲 升 级 链", symbol_text["AK47"])
        self.assertIn("护 甲 升 级 链", node_text["护甲升级链"])
        self.assertNotIn("AK47", node_text["护甲升级链"])
        self.assertEqual(node_text["Weapon Socket"], "")

    def test_han_adjacent_phrase_and_reverse_token(self) -> None:
        with open_database(self.database_path) as connection:
            forward = _names(search_symbols(connection, "护甲"))
            reverse = _names(search_symbols(connection, "甲护"))
            forward_nodes = _names(search_nodes(connection, "护甲"), "title")
            reverse_nodes = _names(search_nodes(connection, "甲护"), "title")
        self.assertIn("护甲升级链", forward)
        self.assertIn("AK47", forward)  # CJK only in details_json still reaches FTS
        self.assertNotIn("甲护测试", forward)
        self.assertEqual(reverse, ["甲护测试"])
        self.assertEqual(forward_nodes, ["护甲升级链"])
        self.assertEqual(reverse_nodes, ["甲护节点"])

    def test_mixed_ascii_and_cjk_query_uses_original_and_projected_columns(self) -> None:
        with open_database(self.database_path) as connection:
            self.assertEqual(_names(search_symbols(connection, "AK47护甲")), ["AK47"])
            self.assertEqual(_names(search_symbols(connection, "ＡＫ４７护甲")), ["AK47"])
            self.assertEqual(_names(search_symbols(connection, "AK47 护甲")), ["AK47"])
            self.assertEqual(_names(search_nodes(connection, "AK47护甲"), "title"), ["护甲升级链"])
            # The ASCII token alone still uses the original column.
            self.assertEqual(_names(search_symbols(connection, "AK47")), ["AK47"])

    def test_japanese_korean_and_pure_ascii_behavior(self) -> None:
        with open_database(self.database_path) as connection:
            self.assertEqual(_names(search_symbols(connection, "強化")), ["武器強化計画"])
            self.assertEqual(_names(search_symbols(connection, "化強")), [])
            self.assertEqual(_names(search_symbols(connection, "방어구")), ["방어구 강화"])
            self.assertEqual(_names(search_symbols(connection, "구방")), [])
            self.assertEqual(_names(search_nodes(connection, "weapon socket"), "title"), ["Weapon Socket"])
            self.assertEqual(_names(search_symbols(connection, "nonexistentquery")), [])

    def test_boundary_token_prevents_cross_field_cjk_splice(self) -> None:
        with open_database(self.database_path) as connection:
            # Symbol "测试甲" has a details note starting with "护甲". Without the
            # field-boundary token the reversed phrase "甲护" would splice the
            # name tail onto the details head inside the single search_text column.
            reverse = _names(search_symbols(connection, "甲护"))
        self.assertEqual(reverse, ["甲护测试"])
        self.assertNotIn("测试甲", reverse)

    def test_punctuation_does_not_compose_false_cjk_adjacency(self) -> None:
        with open_database(self.database_path) as connection:
            # "护甲，升级链" is projected with a run boundary between "甲" and
            # "升", so the substring "甲升" must not reach across the comma.
            adjacent = _names(search_symbols(connection, "甲升"))
            self.assertNotIn("设定", adjacent)
            self.assertIn("AK47", adjacent)  # contiguous "护甲升级链" still matches
            self.assertIn("设定", _names(search_symbols(connection, "护甲")))

    def test_cross_fts_column_phrase_does_not_splice(self) -> None:
        with open_database(self.database_path) as connection:
            # The original name column ends with one "甲" token while search_text
            # starts with "护 甲". FTS5 phrase positions are per column, so the
            # phrase "甲 护" must not span the two columns.
            reverse = _names(search_symbols(connection, "甲护"))
        self.assertIn("甲护测试", reverse)
        self.assertNotIn("甲", reverse)

    def test_asset_search_remains_legacy_and_cjk_capable(self) -> None:
        with open_database(self.database_path) as connection:
            self.assertEqual(
                [row["asset_path"] for row in search_assets(connection, "中文")],
                [ASSET_A],
            )
            self.assertEqual(
                [row["asset_path"] for row in search_assets(connection, "BP_Armor")],
                [ASSET_A],
            )

    def test_cjk_only_result_ordering_is_deterministic_and_relevance_first(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_v2_order_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            export_root = root / "export"
            write_export(
                export_root,
                [
                    make_asset(
                        ASSET_A,
                        symbols=[
                            _symbol("variable|substring|v1", "护甲升级链", ASSET_A),
                            _symbol("variable|exact|v1", "护甲", ASSET_A),
                        ],
                    )
                ],
            )
            with open_database(database_path) as connection:
                build_index(connection, export_root, database_path)
                first = _names(search_symbols(connection, "护甲"))
                second = _names(search_symbols(connection, "护甲"))
            self.assertEqual(first, ["护甲", "护甲升级链"])
            self.assertEqual(first, second)

    def test_update_and_delete_triggers_keep_external_content_consistent(self) -> None:
        with open_database(self.database_path) as connection:
            asset_id = int(
                connection.execute("SELECT id FROM assets WHERE asset_path = ?", (ASSET_A,)).fetchone()[0]
            )
            new_text = symbol_search_text(name="重铸护甲词条", stable_id="variable|recast|v1")
            connection.execute(
                """
                INSERT INTO symbols(asset_id, stable_id, kind, name, symbol_asset_path, search_text)
                VALUES (?, 'variable|recast|v1', 'variable', '重铸护甲词条', ?, ?)
                """,
                (asset_id, ASSET_A, new_text),
            )
            connection.commit()
            self.assertEqual(_names(search_symbols(connection, "重铸")), ["重铸护甲词条"])

            updated_text = symbol_search_text(name="附魔护甲", stable_id="variable|recast|v1")
            connection.execute(
                "UPDATE symbols SET name = '附魔护甲', search_text = ? WHERE stable_id = 'variable|recast|v1'",
                (updated_text,),
            )
            connection.commit()
            self.assertEqual(_names(search_symbols(connection, "重铸")), [])
            self.assertEqual(_names(search_symbols(connection, "附魔")), ["附魔护甲"])

            connection.execute("DELETE FROM symbols WHERE stable_id = 'variable|recast|v1'")
            connection.commit()
            self.assertEqual(_names(search_symbols(connection, "附魔")), [])
            self.assertEqual(
                [str(row[0]) for row in connection.execute("PRAGMA quick_check")],
                ["ok"],
            )

    def test_fts_rebuild_is_consistent_with_cjk_projection(self) -> None:
        with open_database(self.database_path) as connection:
            for _ in range(2):
                connection.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
                connection.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
                connection.commit()
                self.assertEqual(
                    [str(row[0]) for row in connection.execute("PRAGMA quick_check")],
                    ["ok"],
                )
                self.assertIn("护甲升级链", _names(search_symbols(connection, "护甲")))


class CodeIndexOfflineCjkV2Tests(unittest.TestCase):
    def test_cpp_symbol_search_works_on_v2_without_any_editor(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_v2_code_") as temporary_root:
            root = Path(temporary_root)
            project_root = root / "Project"
            (project_root / "Source" / "TestProject" / "Public").mkdir(parents=True)
            (project_root / "TestProject.uproject").write_text("{}", encoding="utf-8")
            header = project_root / "Source" / "TestProject" / "Public" / "FooActor.h"
            header.write_text(
                "// 护甲升级链说明\nclass FFooActor {\n};\n",
                encoding="utf-8",
            )
            database_path = root / "index.sqlite3"
            with open_database(database_path) as connection:
                result = build_code_index(connection, project_root, database_path)
                self.assertEqual(result.failed, 0, result.errors)
                self.assertGreaterEqual(result.symbols, 1)
                rows = search_symbols(connection, "FFooActor", profile="code")
                self.assertEqual([row["stable_id"] for row in rows], ["cpp:type:FFooActor"])
                # The code profile is offline and never creates editor state.
                self.assertEqual(search_symbols(connection, "护甲"), [])
                self.assertEqual(
                    [str(row[0]) for row in connection.execute("PRAGMA quick_check")],
                    ["ok"],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)

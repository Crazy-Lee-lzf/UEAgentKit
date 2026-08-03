from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import _windows_extended_path, build_index  # noqa: E402
from ue_agent_kit.queries import (  # noqa: E402
    find_references,
    get_asset,
    get_stats,
    search_assets,
    search_symbols,
)


REVISION_A = "a" * 64
REVISION_B = "b" * 64
ASSET_A = "/Game/中文/BP_TestActor.BP_TestActor"
ASSET_B = "/Game/Other/BP_SecondActor.BP_SecondActor"


def make_asset(
    asset_path: str,
    *,
    profile: str,
    revision: str,
    rich: bool,
    project_name: str = "测试项目",
) -> dict[str, Any]:
    package_name, asset_name = asset_path.rsplit(".", 1)
    asset_symbol = f"asset|{asset_path}"
    graph_guid = "11111111-1111-1111-1111-111111111111" if asset_path == ASSET_A else "22222222-2222-2222-2222-222222222222"
    graph_symbol = f"graph|{asset_path}|{graph_guid}"
    symbols: list[dict[str, Any]] = [
        {
            "id": asset_symbol,
            "kind": "asset",
            "name": asset_name,
            "assetPath": asset_path,
            "path": asset_path,
            "class": "/Script/Engine.Blueprint",
        },
        {
            "id": graph_symbol,
            "kind": "graph",
            "name": "EventGraph",
            "assetPath": asset_path,
            "guid": graph_guid,
            "graphKind": "uber",
            "ownerSymbolId": asset_symbol,
        },
    ]
    variables: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = [
        {
            "id": f"reference|inherits|{asset_symbol}|class|/Script/Engine.Actor",
            "kind": "inherits",
            "sourceSymbolId": asset_symbol,
            "targetSymbolId": "class|/Script/Engine.Actor",
            "targetKind": "class",
            "targetName": "Actor",
            "targetAssetPath": "",
        }
    ]
    nodes: list[dict[str, Any]] = []

    if asset_path == ASSET_A:
        variable_guid = "33333333-3333-3333-3333-333333333333"
        variable_symbol = f"variable|{asset_path}|{variable_guid}"
        symbols.append(
            {
                "id": variable_symbol,
                "kind": "variable",
                "name": "生命值",
                "assetPath": asset_path,
                "guid": variable_guid,
                "ownerSymbolId": asset_symbol,
            }
        )
        variables.append(
            {
                "name": "生命值",
                "friendlyName": "生命值",
                "guid": variable_guid,
                "category": "测试",
                "defaultValue": "100.000000",
                "propertyFlags": "0x5",
                "repNotifyFunction": "None",
                "type": {
                    "category": "real",
                    "subcategory": "double",
                    "subcategoryObject": "",
                    "container": "none",
                    "isReference": False,
                    "isConst": False,
                    "isWeakPointer": False,
                },
            }
        )

        if rich:
            node_guid = "44444444-4444-4444-4444-444444444444"
            nodes.append(
                {
                    "guid": node_guid,
                    "name": "K2Node_VariableSet_0",
                    "class": "/Script/BlueprintGraph.K2Node_VariableSet",
                    "title": "Set 生命值",
                    "comment": "中文测试节点",
                    "pins": [],
                }
            )
            references.append(
                {
                    "id": f"reference|writes|{graph_guid}|{node_guid}|{variable_symbol}",
                    "kind": "writes",
                    "sourceSymbolId": graph_symbol,
                    "targetSymbolId": variable_symbol,
                    "targetKind": "variable",
                    "targetName": "生命值",
                    "targetAssetPath": asset_path,
                    "graphGuid": graph_guid,
                    "graphName": "EventGraph",
                    "nodeGuid": node_guid,
                    "nodeClass": "/Script/BlueprintGraph.K2Node_VariableSet",
                    "nodeTitle": "Set 生命值",
                }
            )

    graphs = [
        {
            "guid": graph_guid,
            "name": "EventGraph",
            "kind": "uber",
            "schema": "/Script/BlueprintGraph.EdGraphSchema_K2",
            "nodes": nodes,
        }
    ]
    summary = {
        "variables": len(variables),
        "components": 0,
        "graphs": len(graphs),
        "nodes": len(nodes),
        "pins": 0,
        "links": 0,
        "symbols": len(symbols),
        "references": len(references),
    }
    return {
        "schemaVersion": "1.1",
        "exporterVersion": "0.2.2",
        "engineVersion": "5.6.1-test",
        "profile": profile,
        "projectName": project_name,
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
            "modifiedUtc": "2026-07-15T00:00:00.000Z",
            "contentSha256": revision,
        },
        "interfaces": [],
        "variables": variables,
        "components": [],
        "functions": [],
        "graphs": graphs,
        "symbols": symbols,
        "references": references,
        "summary": summary,
    }


def write_export(root: Path, assets: list[dict[str, Any]]) -> None:
    canonical_root = root / "canonical"
    bpctx_root = root / "bpctx"
    canonical_root.mkdir(parents=True, exist_ok=True)
    bpctx_root.mkdir(parents=True, exist_ok=True)

    manifest_assets: list[dict[str, Any]] = []
    for asset in assets:
        relative = Path(asset["packageName"].lstrip("/"))
        canonical_path = canonical_root / relative.parent / f"{relative.name}_{asset['assetPath'].rsplit('.', 1)[1]}.json"
        bpctx_path = bpctx_root / relative.parent / f"{relative.name}_{asset['assetPath'].rsplit('.', 1)[1]}.bpctx"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        bpctx_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(
            json.dumps(asset, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\r\n",
        )
        bpctx_path.write_text(
            f"H|BPCTX|1|schema=1.1|exporter=0.2.2\r\nA|a0|{asset['assetPath']}\r\n",
            encoding="utf-8",
            newline="",
        )
        summary = asset["summary"]
        manifest_assets.append(
            {
                "assetPath": asset["assetPath"],
                "success": True,
                "jsonPath": str(canonical_path),
                "bpctxPath": str(bpctx_path),
                "variables": summary["variables"],
                "components": summary["components"],
                "graphs": summary["graphs"],
                "nodes": summary["nodes"],
                "pins": summary["pins"],
                "links": summary["links"],
                "symbols": summary["symbols"],
                "references": summary["references"],
            }
        )

    manifest = {
        "schemaVersion": "1.1",
        "exporterVersion": "0.2.2",
        "engineVersion": "5.6.1-test",
        "profile": assets[0]["profile"] if assets else "index",
        "projectName": assets[0].get("projectName", "") if assets else "",
        "successCount": len(assets),
        "failureCount": 0,
        "assets": manifest_assets,
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\r\n",
    )


GENERIC_ASSET = "/Game/Environment/SM_Test.SM_Test"
GENERIC_TARGET = "/Game/Environment/T_Test.T_Test"


def make_generic_asset() -> dict[str, Any]:
    asset = make_asset(GENERIC_ASSET, profile="asset-index", revision=REVISION_A, rich=False)
    asset_symbol = f"asset|{GENERIC_ASSET}"
    target_symbol = f"asset|{GENERIC_TARGET}"
    asset["assetClass"] = "/Script/Engine.StaticMesh"
    asset["blueprintType"] = ""
    asset["parentClass"] = ""
    asset["generatedClass"] = ""
    asset["skeletonGeneratedClass"] = ""
    asset["variables"] = []
    asset["components"] = []
    asset["functions"] = []
    asset["graphs"] = []
    asset["symbols"] = [
        {
            "id": asset_symbol,
            "kind": "asset",
            "name": "SM_Test",
            "assetPath": GENERIC_ASSET,
            "path": GENERIC_ASSET,
            "class": "/Script/Engine.StaticMesh",
            "assetReader": "static-mesh-v1",
            "assetReaderStatus": "success",
            "assetDetails": {
                "type": "static-mesh",
                "readerVersion": 1,
                "lodCount": 1,
                "materialSlotCount": 1,
                "nanite": {"enabled": True},
            },
            "assetRegistry": {
                "packagePath": "/Game/Environment",
                "tags": {"Triangles": "12", "LODs": "1"},
            },
        }
    ]
    asset["assetReader"] = "static-mesh-v1"
    asset["assetReaderStatus"] = "success"
    asset["assetReaderError"] = ""
    asset["assetDetails"] = {
        "type": "static-mesh",
        "readerVersion": 1,
        "lodCount": 1,
        "materialSlotCount": 1,
        "nanite": {"enabled": True},
    }
    asset["references"] = [
        {
            "id": f"reference|depends-hard-package|{asset_symbol}|{target_symbol}",
            "kind": "depends-hard-package",
            "sourceSymbolId": asset_symbol,
            "targetSymbolId": target_symbol,
            "targetKind": "asset",
            "targetName": "T_Test",
            "targetAssetPath": GENERIC_TARGET,
            "targetPath": "/Game/Environment/T_Test",
            "dependencyCategory": "package",
            "dependencyProperties": "hard,game",
        }
    ]
    asset["summary"] = {
        "variables": 0,
        "components": 0,
        "graphs": 0,
        "nodes": 0,
        "pins": 0,
        "links": 0,
        "symbols": 1,
        "references": 1,
        "registryTags": 2,
        "specializedDetails": 1,
    }
    return asset


class IndexerAndQueryTests(unittest.TestCase):

    def test_windows_extended_path_supports_drive_and_unc_paths(self) -> None:
        separator = chr(92)
        extended_prefix = separator * 2 + "?" + separator
        drive_path = "C:" + separator + "deep" + separator + "asset.json"
        unc_path = separator * 2 + "server" + separator + "share" + separator + "asset.json"

        self.assertEqual(_windows_extended_path(drive_path), extended_prefix + drive_path)
        self.assertEqual(
            _windows_extended_path(unc_path),
            extended_prefix + "UNC" + separator + unc_path[2:],
        )
        already_extended = extended_prefix + drive_path
        self.assertEqual(_windows_extended_path(already_extended), already_extended)

    def test_generic_asset_catalog_import_and_class_search(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_generic_assets_") as temporary_root:
            temp_root = Path(temporary_root)
            database_path = temp_root / "index.sqlite3"
            export_root = temp_root / "asset_catalog"
            write_export(export_root, [make_generic_asset()])

            with open_database(database_path) as connection:
                result = build_index(connection, export_root, database_path)
                self.assertEqual((result.added, result.failed), (1, 0))

                by_query = search_assets(connection, "StaticMesh")
                self.assertEqual([item["asset_path"] for item in by_query], [GENERIC_ASSET])

                by_class = search_assets(connection, "", asset_class="StaticMesh")
                self.assertEqual([item["asset_path"] for item in by_class], [GENERIC_ASSET])
                self.assertEqual(by_class[0]["profile"], "asset-index")

                references = find_references(connection, target_asset_path=GENERIC_TARGET)
                self.assertEqual(len(references), 1)
                self.assertEqual(references[0]["asset_path"], GENERIC_ASSET)

                indexed = get_asset(connection, GENERIC_ASSET, include_details=True)
                self.assertIsNotNone(indexed)
                assert indexed is not None
                self.assertEqual(indexed["asset_class"], "/Script/Engine.StaticMesh")
                self.assertEqual(indexed["indexed_counts"]["references"], 1)
                self.assertEqual(indexed["symbols"][0]["details"]["symbol"]["assetRegistry"]["tags"]["LODs"], "1")
                self.assertEqual(
                    indexed["symbols"][0]["details"]["symbol"]["assetDetails"]["lodCount"],
                    1,
                )
                details_search = search_symbols(connection, "static-mesh", kind="asset", include_details=True)
                self.assertEqual(details_search[0]["details"]["symbol"]["assetReader"], "static-mesh-v1")
                stats = get_stats(connection)
                self.assertEqual(stats["assetClasses"]["/Script/Engine.StaticMesh"], 1)

    def test_data_table_row_reference_impact_is_exact_per_row(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_data_table_row_refs_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            export_root = root / "export"
            data_table_path = "/Game/UEAgentKitTests/DT_RowTarget.DT_RowTarget"
            source = make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=False)
            source["references"].extend(
                [
                    {
                        "id": (
                            "reference|depends-searchable-name|"
                            f"asset|{ASSET_A}|searchable-name|{data_table_path}::Row_Alpha"
                        ),
                        "kind": "depends-searchable-name",
                        "sourceSymbolId": f"asset|{ASSET_A}",
                        "targetSymbolId": f"searchable-name|{data_table_path}::Row_Alpha",
                        "targetKind": "searchable-name",
                        "targetName": "Row_Alpha",
                        "targetAssetPath": data_table_path,
                        "targetPath": f"{data_table_path}::Row_Alpha",
                    },
                    {
                        "id": (
                            "reference|depends-searchable-name|"
                            f"asset|{ASSET_A}|searchable-name|{data_table_path}::Row_Beta"
                        ),
                        "kind": "depends-searchable-name",
                        "sourceSymbolId": f"asset|{ASSET_A}",
                        "targetSymbolId": f"searchable-name|{data_table_path}::Row_Beta",
                        "targetKind": "searchable-name",
                        "targetName": "Row_Beta",
                        "targetAssetPath": data_table_path,
                        "targetPath": f"{data_table_path}::Row_Beta",
                    },
                ]
            )
            source["summary"]["references"] = len(source["references"])
            write_export(export_root, [source])

            with open_database(database_path) as connection:
                result = build_index(connection, export_root, database_path)
                self.assertEqual((result.added, result.failed), (1, 0))

            service = IndexQueryService(database_path)
            alpha = service.get_data_table_row_reference_impact(data_table_path, "Row_Alpha")
            beta = service.get_data_table_row_reference_impact(data_table_path, "Row_Beta")
            missing = service.get_data_table_row_reference_impact(data_table_path, "Row_Missing")

            self.assertEqual(alpha["referenceCount"], 1)
            self.assertEqual(alpha["referencers"][0]["source_asset_path"], ASSET_A)
            self.assertEqual(alpha["targetPath"], f"{data_table_path}::Row_Alpha")
            self.assertEqual(beta["referenceCount"], 1)
            self.assertEqual(missing["referenceCount"], 0)
            self.assertEqual(missing["referencers"], [])

    def test_incremental_index_search_and_prune(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_index_") as temporary_root:
            temp_root = Path(temporary_root)
            database_path = temp_root / "数据" / "索引.sqlite3"
            index_export = temp_root / "导出 index"
            logic_export = temp_root / "导出 logic"
            prune_export = temp_root / "导出 prune"

            asset_a_index = make_asset(ASSET_A, profile="index", revision=REVISION_A, rich=False)
            asset_b_index = make_asset(ASSET_B, profile="index", revision=REVISION_B, rich=False)
            asset_a_logic = make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True)
            write_export(index_export, [asset_a_index, asset_b_index])
            write_export(logic_export, [asset_a_logic])
            write_export(prune_export, [asset_a_index])

            with open_database(database_path) as connection:
                first = build_index(connection, index_export, database_path)
                self.assertEqual((first.added, first.updated, first.skipped, first.failed), (2, 0, 0, 0))

                repeat = build_index(connection, index_export, database_path)
                self.assertEqual((repeat.added, repeat.updated, repeat.skipped, repeat.failed), (0, 0, 2, 0))

                chinese_assets = search_assets(connection, "中文")
                self.assertEqual([item["asset_path"] for item in chinese_assets], [ASSET_A])
                fts_assets = search_assets(connection, "TestActor")
                self.assertEqual([item["asset_path"] for item in fts_assets], [ASSET_A])

                symbols = search_symbols(connection, "生命值", kind="variable")
                self.assertEqual(len(symbols), 1)
                self.assertEqual(symbols[0]["name"], "生命值")
                self.assertNotIn("details", symbols[0])
                symbols_with_details = search_symbols(
                    connection,
                    "生命值",
                    kind="variable",
                    include_details=True,
                )
                self.assertEqual(symbols_with_details[0]["details"]["definition"]["defaultValue"], "100.000000")

                first_page = search_assets(connection, "", limit=1, offset=0)
                second_page = search_assets(connection, "", limit=1, offset=1)
                self.assertEqual(len(first_page), 1)
                self.assertEqual(len(second_page), 1)
                self.assertNotEqual(first_page[0]["asset_path"], second_page[0]["asset_path"])

                upgraded = build_index(connection, logic_export, database_path)
                self.assertEqual((upgraded.added, upgraded.updated, upgraded.skipped, upgraded.failed), (0, 1, 0, 0))
                writes = find_references(connection, kind="writes", asset_path=ASSET_A)
                self.assertEqual(len(writes), 1)
                self.assertEqual(writes[0]["target_name"], "生命值")
                self.assertNotIn("details", writes[0])

                asset = get_asset(connection, ASSET_A)
                self.assertIsNotNone(asset)
                assert asset is not None
                self.assertEqual(asset["profile"], "logic")
                self.assertEqual(asset["indexed_counts"]["nodes"], 1)
                self.assertEqual(asset["indexed_counts"]["references"], 2)

                downgrade = build_index(connection, index_export, database_path)
                self.assertEqual((downgrade.updated, downgrade.skipped, downgrade.failed), (0, 2, 0))
                asset_after_downgrade = get_asset(connection, ASSET_A)
                self.assertEqual(asset_after_downgrade["profile"], "logic")

                pruned = build_index(connection, prune_export, database_path, prune_prefix="/Game")
                self.assertEqual(pruned.deleted, 1)
                self.assertIsNone(get_asset(connection, ASSET_B))
                stats = get_stats(connection)
                self.assertEqual(stats["counts"]["assets"], 1)
                self.assertEqual(stats["counts"]["nodes"], 1)


    def test_reference_direction_depth_and_project_filter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_reference_walk_") as temporary_root:
            root = Path(temporary_root)
            database_path = root / "index.sqlite3"
            export_root = root / "export"
            first = make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=False)
            second = make_asset(ASSET_B, profile="logic", revision=REVISION_B, rich=False)
            third = make_generic_asset()
            first["references"].append(
                {
                    "id": f"reference|uses|asset|{ASSET_A}|asset|{ASSET_B}",
                    "kind": "uses",
                    "sourceSymbolId": f"asset|{ASSET_A}",
                    "targetSymbolId": f"asset|{ASSET_B}",
                    "targetKind": "asset",
                    "targetName": "BP_SecondActor",
                    "targetAssetPath": ASSET_B,
                }
            )
            second["references"].append(
                {
                    "id": f"reference|uses|asset|{ASSET_B}|asset|{GENERIC_ASSET}",
                    "kind": "uses",
                    "sourceSymbolId": f"asset|{ASSET_B}",
                    "targetSymbolId": f"asset|{GENERIC_ASSET}",
                    "targetKind": "asset",
                    "targetName": "SM_Test",
                    "targetAssetPath": GENERIC_ASSET,
                }
            )
            first["summary"]["references"] = len(first["references"])
            second["summary"]["references"] = len(second["references"])
            write_export(export_root, [first, second, third])

            with open_database(database_path) as connection:
                result = build_index(connection, export_root, database_path)
                self.assertEqual((result.added, result.failed), (3, 0))
                outgoing = find_references(
                    connection,
                    kind="uses",
                    asset_path=ASSET_A,
                    direction="outgoing",
                    depth=2,
                    project_only=True,
                )
                incoming = find_references(
                    connection,
                    kind="uses",
                    asset_path=GENERIC_ASSET,
                    direction="incoming",
                    depth=2,
                    project_only=True,
                )
                external_classes = find_references(
                    connection,
                    kind="inherits",
                    asset_path=ASSET_A,
                    direction="outgoing",
                    project_only=True,
                )

            self.assertEqual(
                [(item["depth"], item["target_asset_path"]) for item in outgoing],
                [(1, ASSET_B), (2, GENERIC_ASSET)],
            )
            self.assertEqual(
                [(item["depth"], item["asset_path"]) for item in incoming],
                [(1, ASSET_B), (2, ASSET_A)],
            )
            self.assertTrue(all(item["direction"] == "outgoing" for item in outgoing))
            self.assertTrue(all(item["direction"] == "incoming" for item in incoming))
            self.assertEqual(external_classes, [])

    def test_project_identity_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_project_key_") as temporary_root:
            temp_root = Path(temporary_root)
            database_path = temp_root / "index.sqlite3"
            project_a = temp_root / "project_a"
            project_b = temp_root / "project_b"
            write_export(
                project_a,
                [make_asset(ASSET_A, profile="index", revision=REVISION_A, rich=False, project_name="项目A")],
            )
            write_export(
                project_b,
                [make_asset(ASSET_B, profile="index", revision=REVISION_B, rich=False, project_name="项目B")],
            )

            with open_database(database_path) as connection:
                first = build_index(connection, project_a, database_path)
                self.assertEqual(first.project_key, "项目A")
                with self.assertRaises(RuntimeError):
                    build_index(connection, project_b, database_path)

    def test_relocated_export_prefers_current_copy(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory(prefix="ueak_relocated_") as temporary_root:
            temp_root = Path(temporary_root)
            database_path = temp_root / "index.sqlite3"
            source_export = temp_root / "original"
            moved_export = temp_root / "moved copy"
            write_export(
                source_export,
                [make_asset(ASSET_A, profile="index", revision=REVISION_A, rich=False)],
            )
            shutil.copytree(source_export, moved_export)

            source_canonical = next((source_export / "canonical").rglob("*.json"))
            changed = json.loads(source_canonical.read_text(encoding="utf-8"))
            changed["revision"]["value"] = f"sha256:{REVISION_B}"
            changed["revision"]["contentSha256"] = REVISION_B
            source_canonical.write_text(
                json.dumps(changed, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\r\n",
            )

            with open_database(database_path) as connection:
                result = build_index(connection, moved_export, database_path)
                self.assertEqual(result.added, 1)
                indexed_asset = get_asset(connection, ASSET_A)
                self.assertIsNotNone(indexed_asset)
                assert indexed_asset is not None
                self.assertEqual(indexed_asset["content_sha256"], REVISION_A)
                self.assertTrue(str(indexed_asset["canonical_relpath"]).startswith("canonical/"))

    def test_prune_is_blocked_when_manifest_has_failures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_prune_fail_") as temporary_root:
            temp_root = Path(temporary_root)
            database_path = temp_root / "index.sqlite3"
            export_root = temp_root / "export"
            write_export(export_root, [make_asset(ASSET_A, profile="index", revision=REVISION_A, rich=False)])

            manifest_path = export_root / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["failureCount"] = 1
            manifest["assets"].append(
                {
                    "assetPath": "/Game/Broken/BP_Broken.BP_Broken",
                    "success": False,
                    "error": "test failure",
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
                newline="\r\n",
            )

            with open_database(database_path) as connection:
                result = build_index(connection, export_root, database_path, prune_prefix="/Game")
                self.assertEqual(result.deleted, 0)
                self.assertTrue(any("Prune was not executed" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()

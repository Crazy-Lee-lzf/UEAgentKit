"""End-to-end World Index V2 integration: indexer -> SQLite -> MCP tools.

The prototype module could only be driven by hand.  These tests pin the real
product loop instead:

    World canonical export  ->  indexer.build_index  ->  same SQLite database
        ->  IndexQueryService / FastMCP ue_find_world_actors

Everything runs offline against the checked-in World canonical fixture whose
shape mirrors ``WorldAssetReader.cpp`` (5 actors, 3 World Partition descriptors,
7 components over 5 classes).  No Unreal Editor, no UBT.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TEST_ROOT = TOOL_ROOT / "tests" / "python"
for entry in (str(SRC_ROOT), str(TEST_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import test_world_index_v2 as world_fixtures  # noqa: E402
from test_indexer_queries import (  # noqa: E402
    ASSET_A as CODE_ASSET,
    REVISION_A as CODE_REVISION,
    make_asset,
    write_export as write_code_export,
)
from ue_agent_kit import world_index_v2  # noqa: E402
from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_DEFINITIONS_BY_NAME,
    tool_names_for_mode,
)

try:  # the MCP dependency is optional in some environments
    from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402

    MCP_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only without the optional dep
    create_mcp_server = None  # type: ignore[assignment]
    MCP_AVAILABLE = False

WORLD_ASSET = "/Game/UEAgentKitTests/Maps/L_Smoke.L_Smoke"
WORLD_PACKAGE = "/Game/UEAgentKitTests/Maps/L_Smoke"
WORLD_TWO_ASSET = "/Game/UEAgentKitTests/Maps/L_Smoke_Duplicate.L_Smoke_Duplicate"
MAPS_PREFIX = "/Game/UEAgentKitTests/Maps"


def write_world_export(
    root: Path,
    documents: list[dict[str, Any]],
    *,
    failed_entries: list[dict[str, Any]] | None = None,
    project_name: str = "UEAgentKitTests",
) -> Path:
    """Write a real exporter-shaped tree: manifest + canonical + bpctx."""

    canonical_root = root / "canonical"
    bpctx_root = root / "bpctx"
    canonical_root.mkdir(parents=True, exist_ok=True)
    bpctx_root.mkdir(parents=True, exist_ok=True)

    assets: list[dict[str, Any]] = []
    for document in documents:
        package_name = str(document["packageName"])
        asset_name = str(document["assetName"])
        relative = Path(package_name.lstrip("/"))
        canonical_path = canonical_root / relative.parent / f"{relative.name}_{asset_name}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        bpctx_path = bpctx_root / relative.parent / f"{relative.name}_{asset_name}.bpctx"
        bpctx_path.parent.mkdir(parents=True, exist_ok=True)
        bpctx_path.write_text("H|BPCTX|1|schema=1.1|exporter=0.8.0\r\n", encoding="utf-8", newline="")
        assets.append(
            {
                "assetPath": document["assetPath"],
                "success": True,
                "jsonPath": str(canonical_path),
                "bpctxPath": str(bpctx_path),
                "symbols": int(document.get("summary", {}).get("symbols", 1)),
                "references": 0,
                "graphs": 0,
                "nodes": 0,
                "assetReader": str(document.get("assetReader", "")),
                "assetReaderStatus": str(document.get("assetReaderStatus", "")),
            }
        )

    failures = list(failed_entries or [])
    for failure in failures:
        assets.append(
            {
                "assetPath": str(failure.get("assetPath", "")),
                "success": False,
                "assetReader": str(failure.get("assetReader", "world-v1")),
                "assetReaderStatus": str(failure.get("assetReaderStatus", "failed")),
                "error": str(failure.get("error", "Failed to load World asset.")),
            }
        )

    manifest = {
        "schemaVersion": "1.1",
        "exporterVersion": "0.8.0",
        "engineVersion": "5.6.1",
        "projectName": project_name,
        "profile": "asset-index",
        "successCount": len(documents),
        "failureCount": len(failures),
        "readerFailureCount": sum(
            1 for entry in assets if str(entry.get("assetReaderStatus", "")).lower() == "failed"
        ),
        "assets": assets,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return root


def payload_total(query: IndexQueryService, **filters: Any) -> int:
    return int(query.find_world_actors(**filters)["pagination"]["total"])


class WorldMcpIntegrationTestCase(unittest.TestCase):
    """Shared: isolated export tree, index database, and query service."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ueak_world_mcp_")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.db = self.root / "index.sqlite3"
        self.export = self.root / "export"
        self.world = world_fixtures.load_world_document()
        write_world_export(self.export, [self.world])

    # -- helpers ---------------------------------------------------------
    def build(self, *, force: bool = False, prune_prefix: str = "", documents: list[dict[str, Any]] | None = None) -> Any:
        if documents is not None:
            shutil.rmtree(self.export, ignore_errors=True)
            write_world_export(self.export, documents)
        with open_database(self.db) as connection:
            return build_index(connection, self.export, self.db, force=force, prune_prefix=prune_prefix)

    def service(self) -> IndexQueryService:
        return IndexQueryService(self.db)

    def scalar(self, sql: str, parameters: tuple[Any, ...] = ()) -> Any:
        with open_database(self.db) as connection:
            row = connection.execute(sql, parameters).fetchone()
            return row[0] if row is not None else None

    def execute(self, sql: str, parameters: tuple[Any, ...] = ()) -> None:
        with open_database(self.db) as connection:
            with connection:  # ``open_database`` closes without committing
                connection.execute(sql, parameters)


class IndexerWorldMaintenanceTests(WorldMcpIntegrationTestCase):
    def test_build_index_creates_world_tables_in_the_target_database(self) -> None:
        result = self.build()

        self.assertEqual((result.added, result.failed), (1, 0))
        self.assertEqual(result.worlds, 1)
        self.assertEqual(result.world_actors, 5)
        self.assertEqual(result.assets[0].world_actors, 5)

        with open_database(self.db) as connection:
            self.assertTrue(world_index_v2.world_schema_present(connection))
            status = world_index_v2.world_index_status(connection)
            self.assertEqual(status["worlds"], 1)
            self.assertEqual(status["actors"], 5)
            self.assertEqual(status["tables"]["world_v2_world_partition_actors"], 3)
            build_status = world_index_v2.index_build_status(connection)
            self.assertTrue(build_status["declared"])
            self.assertTrue(build_status["complete"])
            self.assertEqual(build_status["incompleteReasons"], [])

        # the map is a normal asset row too, so existing tools still work
        asset = self.service().get_asset(WORLD_ASSET)
        self.assertTrue(asset["ok"])
        self.assertEqual(asset["asset"]["asset_class"], "/Script/Engine.World")

    def test_rebuild_is_idempotent(self) -> None:
        first = self.build()
        before = self.scalar("SELECT COUNT(*) FROM world_v2_actors")
        second = self.build()

        self.assertEqual(second.skipped, 1)
        self.assertEqual(second.worlds, 0)
        self.assertEqual(second.world_repairs, 0)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_worlds"), 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_actors"), before)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_actor_tags"), 6)
        self.assertEqual(first.world_actors, 5)

        page = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(page["pagination"]["total"], 5)
        self.assertEqual(len(page["results"]), 5)
        self.assertEqual(page["coverage"], "complete")

    def test_old_index_without_world_rows_is_repaired_on_rebuild(self) -> None:
        self.build()
        # simulate an index built before World V2 existed: asset present, world rows gone
        self.execute("DELETE FROM world_v2_actor_component_classes")
        self.execute("DELETE FROM world_v2_actor_tags")
        self.execute("DELETE FROM world_v2_actor_data_layers")
        self.execute("DELETE FROM world_v2_world_partition_actor_tags")
        self.execute("DELETE FROM world_v2_world_partition_actor_data_layers")
        self.execute("DELETE FROM world_v2_world_partition_actors")
        self.execute("DELETE FROM world_v2_actors")
        self.execute("DELETE FROM world_v2_class_counts")
        self.execute("DELETE FROM world_v2_worlds")

        missing = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(missing["coverage"], "world-index-missing")
        self.assertFalse(missing["complete"])
        self.assertTrue(missing["warnings"])

        repaired = self.build()
        self.assertEqual(repaired.skipped, 1)
        self.assertEqual(repaired.world_repairs, 1)
        self.assertEqual(repaired.world_actors, 5)
        self.assertEqual(repaired.assets[0].reason, "unchanged;world-index-repaired")

        after = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(after["coverage"], "complete")
        self.assertEqual(after["pagination"]["total"], 5)

    def test_world_change_replaces_old_rows_and_drops_removed_actors(self) -> None:
        self.build()
        changed = json.loads(json.dumps(self.world))
        changed["revision"]["value"] = "sha256:" + "ab" * 32
        actors = changed["assetDetails"]["actors"]
        removed_label = actors[1]["label"]
        actors[1] = {**actors[1], "label": "PointLight_Renamed"}
        actors[1]["transform"]["translation"]["z"] = 999.0
        actors[0]["tags"] = ["Environment", "Updated"]
        # drop one actor from the export entirely (and from the descriptors)
        dropped = actors.pop(4)
        changed["assetDetails"]["loadedActorCount"] = 4
        changed["assetDetails"]["exportedActorCount"] = 4
        changed["assetDetails"]["worldPartition"]["actorDescs"] = [
            descriptor
            for descriptor in changed["assetDetails"]["worldPartition"]["actorDescs"]
            if descriptor["guid"] != dropped["guid"]
        ]
        changed["assetDetails"]["worldPartition"]["actorDescCount"] = 2
        changed["assetDetails"]["worldPartition"]["exportedActorDescCount"] = 2

        result = self.build(documents=[changed])
        self.assertEqual(result.updated, 1)
        self.assertEqual(result.world_actors, 4)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_actors"), 4)

        page = self.service().find_world_actors(world=WORLD_ASSET)
        labels = [actor["label"] for actor in page["results"]]
        self.assertEqual(page["pagination"]["total"], 4)
        self.assertNotIn(removed_label, labels)
        self.assertNotIn(dropped["label"], labels)
        self.assertIn("PointLight_Renamed", labels)

        renamed = [actor for actor in page["results"] if actor["label"] == "PointLight_Renamed"][0]
        self.assertEqual(renamed["location_z"], 999.0)
        rock = [actor for actor in page["results"] if actor["label"] == "SM_Rock"][0]
        self.assertEqual(rock["tags"], ["Environment", "Updated"])
        self.assertEqual(
            self.service().find_world_actors(world=WORLD_ASSET, tag="Updated")["pagination"]["total"], 1
        )

    def test_prune_removes_world_rows_for_deleted_maps(self) -> None:
        duplicate = world_fixtures.as_duplicate_map(self.world)
        write_world_export(self.export, [self.world, duplicate])
        first = self.build()
        self.assertEqual(first.worlds, 2)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_worlds"), 2)

        write_world_export(self.export, [self.world])
        pruned = self.build(prune_prefix=MAPS_PREFIX)
        self.assertEqual(pruned.deleted, 1)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_worlds"), 1)
        self.assertEqual(
            self.scalar("SELECT COUNT(*) FROM world_v2_worlds WHERE asset_path = ?", (WORLD_TWO_ASSET,)),
            0,
        )
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_actors"), 5)

    def test_reader_failed_world_export_is_not_indexed_and_marks_build_incomplete(self) -> None:
        failed = json.loads(json.dumps(self.world))
        failed["assetReaderStatus"] = "failed"
        failed["assetReaderError"] = "Failed to load World asset."
        failed["assetDetails"] = {}
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [failed])

        result = self.build()
        self.assertEqual(result.failed, 1)
        self.assertIn("refusing to index incomplete details", " ".join(result.errors))
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM assets"), 0)
        with open_database(self.db) as connection:
            # fail-closed: the whole import rolled back, including the world tables
            self.assertFalse(world_index_v2.world_schema_present(connection))
            build_status = world_index_v2.index_build_status(connection)
        self.assertTrue(build_status["declared"])
        self.assertFalse(build_status["complete"])
        self.assertTrue(build_status["incompleteReasons"])

        page = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(page["results"], [])
        self.assertFalse(page["complete"])
        self.assertEqual(page["coverage"], "world-index-unavailable")
        self.assertFalse(page["worldStatus"]["indexBuild"]["complete"])
        self.assertTrue(
            any("last index build was incomplete" in warning for warning in page["warnings"]),
            page["warnings"],
        )

    def test_failed_rebuild_keeps_previous_rows_but_reports_incomplete_build(self) -> None:
        self.build()
        failed = json.loads(json.dumps(self.world))
        failed["assetReaderStatus"] = "failed"
        failed["assetDetails"] = {}
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [failed])

        result = self.build()
        self.assertEqual(result.failed, 1)
        # the previous good revision is still there (nothing was deleted)...
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM world_v2_actors"), 5)
        page = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(page["pagination"]["total"], 5)
        # ...but the tool says the last import was incomplete instead of "fresh"
        self.assertFalse(page["worldStatus"]["indexBuild"]["complete"])
        self.assertTrue(any("incomplete" in warning for warning in page["warnings"]))


class WorldActorQueryTests(WorldMcpIntegrationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.build()
        self.query = self.service()

    def labels(self, **filters: Any) -> list[str]:
        payload = self.query.find_world_actors(**filters)
        self.assertTrue(payload["ok"], payload)
        return [actor["label"] for actor in payload["results"]]

    def test_returns_real_actor_content(self) -> None:
        payload = self.query.find_world_actors(
            world=WORLD_ASSET, class_contains="BP_Torch", include_component_classes=True
        )
        self.assertEqual(payload["coverage"], "complete")
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["warnings"], [])
        self.assertEqual(payload["pagination"]["total"], 1)
        actor = payload["results"][0]
        self.assertEqual(actor["label"], "BP_Torch_Hallway")
        self.assertEqual(actor["class_path"], "/Game/UEAgentKitTests/BP_Torch.BP_Torch_C")
        self.assertEqual(actor["actor_guid"], "a1000000-0000-4000-8000-000000000004")
        self.assertEqual(actor["location_x"], -640.0)
        self.assertEqual(actor["location_y"], 256.75)
        self.assertEqual(actor["location_z"], 210.0)
        self.assertEqual(actor["tags"], ["Gameplay", "Lighting"])
        self.assertEqual(actor["dataLayers"], ["DL_Night", "DL_World"])
        self.assertEqual(actor["component_count"], 3)
        self.assertEqual(len(actor["componentClasses"]), 3)
        self.assertEqual(actor["world_asset_path"], WORLD_ASSET)
        self.assertEqual(actor["root_mobility"], "Movable")
        self.assertEqual(actor["actor_key"], "a1000000-0000-4000-8000-000000000004")
        self.assertEqual(actor["object_path"], "/Game/UEAgentKitTests/Maps/L_Smoke.L_Smoke:PersistentLevel.BP_Torch_C_4")
        self.assertEqual(actor["world_partial"], 0)

    def test_filters_by_world_class_tag_and_data_layer(self) -> None:
        self.assertEqual(self.labels(world=WORLD_ASSET, class_path="/Script/Engine.PointLight"), ["PointLight_Night"])
        self.assertEqual(self.labels(world=WORLD_PACKAGE, class_path="/Script/Engine.PointLight"), ["PointLight_Night"])
        self.assertEqual(self.labels(world=WORLD_ASSET, tag="Lighting"), ["BP_Torch_Hallway", "PointLight_Night"])
        self.assertEqual(self.labels(world=WORLD_ASSET, data_layer="DL_Night"), ["BP_Torch_Hallway", "PointLight_Night"])
        self.assertEqual(self.labels(world=WORLD_ASSET, tag="Lighting", data_layer="DL_World"), ["BP_Torch_Hallway"])
        self.assertEqual(self.labels(world=WORLD_ASSET, label_contains="Rock"), ["SM_Rock"])
        self.assertEqual(self.labels(world=WORLD_ASSET, class_contains="Capsule"), [])
        self.assertEqual(self.labels(world=WORLD_ASSET, tag="NoSuchTag"), [])
        self.assertEqual(payload_total(self.query, world=WORLD_ASSET), 5)

    def test_pagination_is_complete_and_stable(self) -> None:
        collected: list[str] = []
        token = ""
        for _ in range(10):
            payload = self.query.find_world_actors(
                world=WORLD_ASSET, limit=2, continuation_token=token
            )
            collected.extend(actor["label"] for actor in payload["results"])
            if not payload["pagination"]["hasMore"]:
                break
            token = payload["pagination"]["continuationToken"]
            self.assertTrue(token)
        self.assertEqual(len(collected), 5)
        self.assertEqual(len(set(collected)), 5)
        self.assertEqual(
            collected, ["BP_Torch_Hallway", "PlayerStart", "PointLight_Night", "SM_Rock", "WorldSettings"]
        )

    def test_offset_pagination_matches_totals(self) -> None:
        first = self.query.find_world_actors(world=WORLD_ASSET, limit=2, offset=0)
        second = self.query.find_world_actors(world=WORLD_ASSET, limit=2, offset=2)
        self.assertEqual(first["pagination"]["total"], 5)
        self.assertTrue(first["pagination"]["hasMore"])
        self.assertEqual(len(first["results"]), 2)
        self.assertEqual(len(second["results"]), 2)
        self.assertFalse(set(a["actor_key"] for a in first["results"]) & set(a["actor_key"] for a in second["results"]))

    def test_token_budget_truncates_and_offers_continuation(self) -> None:
        payload = self.query.find_world_actors(world=WORLD_ASSET, limit=50, max_output_tokens=256)
        self.assertLessEqual(payload["outputBudget"]["maxTokens"], 256)
        self.assertTrue(payload["outputBudget"]["truncated"])
        self.assertTrue(payload["pagination"]["continuationToken"])
        self.assertLess(len(payload["results"]), 5)
        self.assertTrue(payload["pagination"]["hasMore"])

    def test_invalid_arguments_are_rejected(self) -> None:
        for kwargs in (
            {"world": WORLD_ASSET, "limit": 0},
            {"world": WORLD_ASSET, "limit": 501},
            {"world": WORLD_ASSET, "offset": -1},
            {"world": "C:\\Maps\\L_Smoke"},
            {"world": WORLD_ASSET, "tag": "bad\x01tag"},
            {"world": WORLD_ASSET, "max_output_tokens": 64},
        ):
            with self.assertRaises(ValueError):
                self.query.find_world_actors(**kwargs)

    def test_cross_map_query_keeps_rows_attributable(self) -> None:
        duplicate = world_fixtures.as_duplicate_map(self.world)
        self.build(documents=[self.world, duplicate])
        payload = self.query.find_world_actors(tag="Lighting")
        self.assertEqual(payload["pagination"]["total"], 4)
        self.assertEqual(
            {actor["world_asset_path"] for actor in payload["results"]},
            {WORLD_ASSET, WORLD_TWO_ASSET},
        )
        self.assertEqual(payload["worldStatus"]["assetPathFilter"], "")
        self.assertEqual([entry["coverage"] for entry in payload["worldStatus"]["worlds"]], ["complete", "complete"])
        self.assertTrue(payload["complete"])


class WorldPartitionQueryTests(WorldMcpIntegrationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.build()
        self.query = self.service()

    def test_descriptors_expose_bounds_and_filters(self) -> None:
        payload = self.query.find_world_partition_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "complete")
        self.assertEqual(payload["pagination"]["total"], 3)
        by_label = {item["label"]: item for item in payload["results"]}
        rock = by_label["SM_Rock"]
        self.assertEqual(rock["base_class_path"], "/Script/Engine.StaticMeshActor")
        self.assertEqual(rock["editorBounds"]["min"]["x"], 1050.0)
        self.assertEqual(rock["editorBounds"]["size"]["z"], 176.0)
        self.assertEqual(rock["loaded"], 1)
        self.assertEqual(rock["dataLayers"], ["DL_World"])

        self.assertEqual(
            self.query.find_world_partition_actors(
                world=WORLD_ASSET, class_contains="PointLight"
            )["pagination"]["total"],
            1,
        )
        self.assertEqual(
            self.query.find_world_partition_actors(world=WORLD_ASSET, folder_prefix="Lighting")[
                "pagination"
            ]["total"],
            2,
        )
        self.assertEqual(
            self.query.find_world_partition_actors(world=WORLD_ASSET, data_layer="DL_Night")[
                "pagination"
            ]["total"],
            2,
        )


class WorldCoverageStateTests(WorldMcpIntegrationTestCase):
    def test_truncated_export_is_not_complete(self) -> None:
        truncated = json.loads(json.dumps(self.world))
        truncated["assetDetails"]["loadedActorCount"] = 45000
        truncated["assetDetails"]["exportedActorCount"] = 20000
        truncated["assetDetails"]["actorListTruncated"] = True
        truncated["assetDetails"]["worldPartition"]["actorDescCount"] = 5000
        truncated["assetDetails"]["worldPartition"]["exportedActorDescCount"] = 2000
        truncated["assetDetails"]["worldPartition"]["actorDescListTruncated"] = True
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [truncated])
        self.build()

        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "truncated")
        self.assertFalse(payload["complete"])
        # the exported slice is still readable, but it is explicitly incomplete
        self.assertEqual(payload["pagination"]["total"], 5)
        self.assertEqual(len(payload["results"]), 5)
        status = payload["worldStatus"]["worlds"][0]
        self.assertTrue(status["actorListTruncated"])
        self.assertTrue(status["actorDescListTruncated"])
        self.assertEqual(status["exportedActorCount"], 20000)
        self.assertEqual(status["loadedActorCount"], 45000)
        self.assertTrue(any("truncated" in warning for warning in payload["warnings"]))

    def test_partial_reader_export_is_not_complete(self) -> None:
        partial = json.loads(json.dumps(self.world))
        partial["assetDetails"]["worldPartition"]["actorDescMetadataAvailable"] = False
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [partial])
        self.build()

        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "partial")
        self.assertFalse(payload["complete"])
        self.assertIn("actor-desc-metadata-unavailable", payload["worldStatus"]["worlds"][0]["partialReasons"])

    def test_missing_actor_desc_metadata_flag_is_not_complete(self) -> None:
        missing = json.loads(json.dumps(self.world))
        missing["assetDetails"]["worldPartition"].pop("actorDescMetadataAvailable", None)
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [missing])
        self.build()

        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "partial")
        self.assertFalse(payload["complete"])
        self.assertIn(
            "actor-desc-metadata-unavailable",
            payload["worldStatus"]["worlds"][0]["partialReasons"],
        )

    def test_empty_world_partition_is_complete(self) -> None:
        empty = json.loads(json.dumps(self.world))
        details = empty["assetDetails"]
        details["usingExternalActors"] = False
        details["worldPartition"].update(
            {
                "actorDescMetadataAvailable": True,
                "actorDescCount": 0,
                "exportedActorDescCount": 0,
                "actorDescListTruncated": False,
                "actorDescs": [],
            }
        )
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [empty])
        self.build()

        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "complete")
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["worldStatus"]["worlds"][0]["actorDescCount"], 0)

    def test_empty_external_actor_world_partition_is_not_complete(self) -> None:
        empty = json.loads(json.dumps(self.world))
        details = empty["assetDetails"]
        details["usingExternalActors"] = True
        details["worldPartition"].update(
            {
                "actorDescMetadataAvailable": True,
                "actorDescCount": 0,
                "exportedActorDescCount": 0,
                "actorDescListTruncated": False,
                "actorDescs": [],
            }
        )
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [empty])
        self.build()

        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "partial")
        self.assertFalse(payload["complete"])
        self.assertIn(
            "actor-desc-empty-with-external-actors",
            payload["worldStatus"]["worlds"][0]["partialReasons"],
        )

    def test_non_world_partition_is_not_applicable(self) -> None:
        non_wp = json.loads(json.dumps(self.world))
        details = non_wp["assetDetails"]
        details["usingExternalActors"] = False
        details["worldPartition"].update(
            {
                "available": False,
                "actorDescMetadataAvailable": False,
                "actorDescCount": 0,
                "exportedActorDescCount": 0,
                "actorDescListTruncated": False,
                "actorDescs": [],
            }
        )
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [non_wp])
        self.build()

        payload = self.service().find_world_partition_actors(world=WORLD_ASSET)
        self.assertEqual(payload["worldPartitionCoverage"], "not-applicable")
        self.assertFalse(payload["actorDescMetadataAvailable"])
        self.assertTrue(any("not a World Partition map" in warning for warning in payload["warnings"]))

    def test_unresolved_descriptor_data_layers_remain_queryable(self) -> None:
        unresolved = json.loads(json.dumps(self.world))
        descriptor = unresolved["assetDetails"]["worldPartition"]["actorDescs"][0]
        descriptor["dataLayersResolved"] = False
        descriptor["dataLayers"] = ["DL_Raw"]
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [unresolved])
        self.build()

        payload = self.service().find_world_partition_actors(
            world=WORLD_ASSET, data_layer="DL_Raw"
        )
        self.assertEqual(payload["pagination"]["total"], 1)
        result = payload["results"][0]
        self.assertEqual(result["dataLayers"], ["DL_Raw"])
        self.assertEqual(result["data_layers_resolved"], 0)

    def test_missing_revision_metadata_is_never_complete(self) -> None:
        revisionless = json.loads(json.dumps(self.world))
        revisionless.pop("revision", None)
        shutil.rmtree(self.export, ignore_errors=True)
        write_world_export(self.export, [revisionless])
        self.build()

        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "partial")
        self.assertFalse(payload["complete"])
        self.assertIn(
            "revision-unavailable", payload["worldStatus"]["worlds"][0]["partialReasons"]
        )
        self.assertTrue(any("freshness" in warning for warning in payload["warnings"]))

    def test_stale_rows_are_flagged_not_served_as_complete(self) -> None:
        self.build()
        # the asset index now points at a newer revision than the world rows
        self.execute(
            "UPDATE assets SET revision_value = ? WHERE asset_path = ?",
            ("sha256:" + "ff" * 32, WORLD_ASSET),
        )
        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "stale")
        self.assertFalse(payload["complete"])
        status = payload["worldStatus"]["worlds"][0]
        self.assertFalse(status["revisionConsistent"])
        self.assertTrue(status["assetRevision"].startswith("sha256:ff"))
        self.assertNotEqual(status["assetRevision"], status["worldRevision"])
        self.assertTrue(any("revision" in warning for warning in payload["warnings"]))
        self.assertTrue(payload["results"], "stale rows are still returned, clearly labelled")

    def test_world_rows_without_asset_are_orphans(self) -> None:
        self.build()
        self.execute("DELETE FROM assets WHERE asset_path = ?", (WORLD_ASSET,))
        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertEqual(payload["coverage"], "orphan")
        self.assertFalse(payload["complete"])
        self.assertTrue(any("orphaned" in warning for warning in payload["warnings"]))

    def test_unknown_map_is_distinguishable_from_empty_map(self) -> None:
        self.build()
        payload = self.service().find_world_actors(world="/Game/UEAgentKitTests/Maps/L_DoesNotExist")
        self.assertEqual(payload["coverage"], "unknown-map")
        self.assertFalse(payload["complete"])
        self.assertFalse(payload["worldStatus"]["targetFound"])
        self.assertEqual(payload["pagination"]["total"], 0)
        self.assertTrue(any("not in this index" in warning for warning in payload["warnings"]))

    def test_database_without_world_tables_reports_unavailable_read_only(self) -> None:
        # a code/blueprint-only export never creates the World V2 tables
        shutil.rmtree(self.export, ignore_errors=True)
        write_code_export(
            self.export,
            [make_asset(CODE_ASSET, profile="asset-index", revision=CODE_REVISION, rich=False)],
        )
        build_result = self.build()
        self.assertEqual(build_result.added, 1)
        self.assertEqual(build_result.worlds, 0)

        with open_database(self.db) as connection:
            self.assertFalse(world_index_v2.world_schema_present(connection))
            tables_before = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }

        payload = self.service().find_world_actors(world=WORLD_ASSET)
        self.assertFalse(payload["worldIndexAvailable"])
        self.assertEqual(payload["coverage"], "world-index-unavailable")
        self.assertFalse(payload["complete"])
        self.assertEqual(payload["results"], [])
        self.assertTrue(any("no World Index V2 tables" in warning for warning in payload["warnings"]))

        summary = self.service().get_world_summary()
        self.assertFalse(summary["worldIndexAvailable"])
        self.assertEqual(summary["coverage"], "world-index-unavailable")
        self.assertEqual(summary["results"], [])

        # the read-only service must not have created tables as a side effect
        with open_database(self.db) as connection:
            tables_after = {
                str(row[0])
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        self.assertEqual(tables_before, tables_after)


@unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
class WorldMcpToolTests(WorldMcpIntegrationTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.build()
        self.server = create_mcp_server(self.db)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        _content, payload = asyncio.run(self.server.call_tool(name, arguments or {}))
        return payload

    def test_registry_and_server_expose_the_world_tools(self) -> None:
        names = tool_names_for_mode()
        for tool_name in (
            "ue_find_world_actors",
            "ue_find_world_partition_actors",
            "ue_get_world_summary",
        ):
            self.assertIn(tool_name, names)
            definition = TOOL_DEFINITIONS_BY_NAME[tool_name]
            self.assertEqual(definition.group, "query")
            self.assertTrue(definition.read_only)
            self.assertFalse(definition.destructive)

        tools = {tool.name: tool for tool in asyncio.run(self.server.list_tools())}
        for tool_name in (
            "ue_find_world_actors",
            "ue_find_world_partition_actors",
            "ue_get_world_summary",
        ):
            self.assertIn(tool_name, tools)
            self.assertTrue(bool(tools[tool_name].annotations.readOnlyHint))
            self.assertFalse(bool(tools[tool_name].annotations.destructiveHint))

        capabilities = self.call("ue_get_capabilities")
        advertised = {entry["name"]: entry for entry in capabilities["tools"]}
        self.assertTrue(advertised["ue_find_world_actors"]["readOnly"])
        self.assertFalse(advertised["ue_find_world_actors"]["destructive"])

    def test_tool_returns_real_actor_content(self) -> None:
        payload = self.call(
            "ue_find_world_actors",
            {"world": WORLD_ASSET, "data_layer": "DL_Night", "include_component_classes": True},
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["tool"], "ue_find_world_actors")
        self.assertEqual(payload["coverage"], "complete")
        self.assertTrue(payload["complete"])
        self.assertEqual(payload["pagination"]["total"], 2)
        labels = [actor["label"] for actor in payload["results"]]
        self.assertEqual(labels, ["BP_Torch_Hallway", "PointLight_Night"])
        torch = payload["results"][0]
        self.assertEqual(torch["class_path"], "/Game/UEAgentKitTests/BP_Torch.BP_Torch_C")
        self.assertEqual(torch["componentClasses"][0]["classPath"], "/Script/Engine.PointLightComponent")

    def test_tool_exposes_revision_and_timestamp_warnings(self) -> None:
        payload = self.call("ue_find_world_actors", {"world": WORLD_ASSET})
        status = payload["worldStatus"]["worlds"][0]
        self.assertTrue(status["assetIndexedAtUtc"])
        self.assertTrue(status["worldIngestedAtUtc"])
        self.assertTrue(status["assetRevision"].startswith("sha256:"))
        self.assertEqual(status["assetRevision"], status["worldRevision"])
        self.assertFalse(status["partial"])
        self.assertEqual(payload["worldStatus"]["indexBuild"]["complete"], True)
        self.assertTrue(payload["worldStatus"]["indexBuild"]["lastBuildUtc"])

        # a stale revision must show up as a warning, never as "complete"
        self.execute("UPDATE assets SET revision_value = ? WHERE asset_path = ?", ("sha256:" + "ee" * 32, WORLD_ASSET))
        stale = self.call("ue_find_world_actors", {"world": WORLD_ASSET})
        self.assertFalse(stale["complete"])
        self.assertEqual(stale["coverage"], "stale")
        self.assertTrue(stale["warnings"])

    def test_tool_argument_errors_are_structured(self) -> None:
        payload = self.call("ue_find_world_actors", {"world": WORLD_ASSET, "limit": 10_000})
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid-arguments")

        unknown_map = self.call("ue_find_world_actors", {"world": "/Game/Nope/L_Nope"})
        self.assertTrue(unknown_map["ok"])
        self.assertEqual(unknown_map["coverage"], "unknown-map")
        self.assertFalse(unknown_map["complete"])

    def test_non_partition_map_reports_wp_not_applicable(self) -> None:
        self.world["assetDetails"]["worldPartition"] = {
            "available": False,
            "actorDescMetadataAvailable": False,
            "actorDescCount": 0,
            "exportedActorDescCount": 0,
            "actorDescListTruncated": False,
            "actorDescs": [],
        }
        write_world_export(self.export, [self.world])
        self.build()
        result = self.call("ue_find_world_partition_actors", {"world": WORLD_ASSET})
        self.assertTrue(result["ok"])
        self.assertEqual(result["coverage"], "complete")
        self.assertEqual(result["worldPartitionCoverage"], "not-applicable")
        self.assertIs(result["worldPartitionAvailable"], False)
        self.assertIs(result["actorDescMetadataAvailable"], False)
        self.assertEqual(result["pagination"]["total"], 0)
        self.assertTrue(any("not a World Partition map" in x for x in result["warnings"]))
        self.assertIs(result["worldStatus"]["worlds"][0]["worldPartitionAvailable"], False)

    def test_world_summary_paginates_75_worlds_without_empty_token_loop(self) -> None:
        documents = []
        for index in range(75):
            document = json.loads(json.dumps(self.world))
            if index:
                package = f"/Game/UEAgentKitTests/Maps/L_Page_{index:03d}"
                name = f"L_Page_{index:03d}"
                document["packageName"] = package
                document["assetName"] = name
                document["assetPath"] = f"{package}.{name}"
                # The copied fixture retains its actor graph shape; unique asset
                # identity is all this World-summary pagination test needs.
                document["symbols"] = []
                document["references"] = []
            documents.append(document)
        result = self.build(documents=documents)
        self.assertEqual(result.failed, 0)
        self.server = create_mcp_server(self.db)

        expected = {document["assetPath"] for document in documents}
        seen = set()
        token = ""
        for page_index in range(20):
            arguments = {"limit": 7} if not token else {"continuation_token": token}
            payload = self.call("ue_get_world_summary", arguments)
            self.assertTrue(payload["ok"], payload)
            pagination = payload["pagination"]
            self.assertEqual(pagination["total"], 75)
            self.assertEqual(pagination["offset"], len(seen))
            self.assertEqual(payload["coverageScope"], "page")
            page = payload["results"]
            self.assertTrue(page, "a hasMore page must never be empty")
            self.assertLessEqual(len(page), 7)
            for entry in page:
                self.assertNotIn(entry["assetPath"], seen)
                seen.add(entry["assetPath"])
            token = pagination["continuationToken"]
            if not pagination["hasMore"]:
                self.assertFalse(token)
                break
            self.assertTrue(token)
        else:
            self.fail("World summary continuation failed to terminate")
        self.assertEqual(seen, expected)

    def test_partition_and_summary_tools(self) -> None:
        partitions = self.call(
            "ue_find_world_partition_actors", {"world": WORLD_ASSET, "tag": "Lighting"}
        )
        self.assertTrue(partitions["ok"])
        self.assertIs(partitions["worldPartitionAvailable"], True)
        self.assertEqual(partitions["worldPartitionCoverage"], "complete")
        self.assertEqual(partitions["pagination"]["total"], 2)
        self.assertTrue(all("editorBounds" in item for item in partitions["results"]))

        summary = self.call("ue_get_world_summary", {})
        self.assertTrue(summary["ok"])
        self.assertEqual(summary["pagination"]["total"], 1)
        entry = summary["results"][0]
        self.assertEqual(entry["assetPath"], WORLD_ASSET)
        self.assertEqual(entry["coverage"], "complete")
        self.assertEqual(entry["actorRows"], 5)
        self.assertEqual(entry["actorDescRows"], 3)
        self.assertTrue(entry["assetIndexedAtUtc"])

        missing = self.call("ue_get_world_summary", {"world": "/Game/Nope/L_Nope"})
        self.assertEqual(missing["coverage"], "unknown-map")
        self.assertEqual(missing["pagination"]["total"], 0)
        self.assertFalse(missing["complete"])


if __name__ == "__main__":
    unittest.main()

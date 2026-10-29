"""Tests for :mod:`ue_agent_kit.world_index_v2` (World Index V2 side tables).

Everything here runs offline against the checked-in World canonical JSON
``tests/fixtures/world_canonical/L_Smoke.json``, whose shape mirrors a real
``WorldAssetReader.cpp`` export (5 actors, 3 World Partition descriptors, 7
components split across 5 component classes).  No Unreal Editor, no UBT.
"""

from __future__ import annotations

import contextlib
import copy
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
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.queries import get_stats, search_assets  # noqa: E402
from ue_agent_kit.world_index_v2 import (  # noqa: E402
    WORLD_INDEX_V2_SCHEMA_VERSION,
    WorldIndexError,
    delete_world,
    ensure_schema,
    find_world_actors,
    find_world_partition_actors,
    get_world,
    ingest_world,
    ingest_world_file,
    list_worlds,
    parse_world_canonical,
    world_index_status,
)

FIXTURE_PATH = TOOL_ROOT / "tests" / "fixtures" / "world_canonical" / "L_Smoke.json"

WORLD_ASSET = "/Game/UEAgentKitTests/Maps/L_Smoke.L_Smoke"
WORLD_TWO_ASSET = "/Game/UEAgentKitTests/Maps/L_Smoke_Duplicate.L_Smoke_Duplicate"

SM_ROCK = "SM_Rock"
TORCH = "BP_Torch_Hallway"
NIGHT_LIGHT = "PointLight_Night"
PLAYER_START = "PlayerStart"
WORLD_SETTINGS_LABEL = "WorldSettings"


def load_world_document() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def as_duplicate_map(document: dict[str, Any]) -> dict[str, Any]:
    """A second map that legitimately reuses the same actor GUIDs."""

    duplicate = copy.deepcopy(document)
    duplicate["assetPath"] = WORLD_TWO_ASSET
    duplicate["packageName"] = "/Game/UEAgentKitTests/Maps/L_Smoke_Duplicate"
    duplicate["assetName"] = "L_Smoke_Duplicate"
    duplicate["revision"]["value"] = "sha256:" + "7" * 62 + "31"
    duplicate["revision"]["contentSha256"] = "7" * 62 + "31"
    duplicate["assetDetails"]["persistentLevelPackage"] = duplicate["packageName"]
    duplicate["assetDetails"]["persistentLevelPath"] = (
        duplicate["packageName"] + ".L_Smoke_Duplicate:PersistentLevel"
    )
    duplicate["assetDetails"]["actors"][0]["label"] = "SM_Rock_DuplicateMap"
    duplicate["symbols"][0]["assetPath"] = WORLD_TWO_ASSET
    duplicate["symbols"][0]["path"] = WORLD_TWO_ASSET
    duplicate["symbols"][0]["id"] = f"asset|{WORLD_TWO_ASSET}"
    return duplicate


def minimal_world_document(**details_overrides: Any) -> dict[str, Any]:
    """Smallest faithful world document: one actor with no optional fields."""

    details: dict[str, Any] = {
        "type": "world",
        "readerVersion": 1,
        "worldType": "Map",
        "loadedActorCount": 1,
        "exportedActorCount": 1,
        "actorListTruncated": False,
        "componentCount": 0,
        "actors": [
            {
                "objectPath": "/Game/UEAgentKitTests/Maps/L_Min.L_Min:PersistentLevel.Actor_1",
                "name": "Actor_1",
                "guid": "d1000000-0000-4000-8000-000000000001",
                "classPath": "/Script/Engine.Actor",
            }
        ],
        "worldPartition": {
            "available": True,
            "actorDescMetadataAvailable": True,
            "actorDescCount": 1,
            "exportedActorDescCount": 1,
            "actorDescListTruncated": False,
            "actorDescs": [
                {
                    "guid": "e1000000-0000-4000-8000-000000000001",
                    "actorPath": "/Game/UEAgentKitTests/Maps/L_Min.L_Min:PersistentLevel.Actor_1",
                    "name": "Actor_1",
                    "baseClassPath": "/Script/Engine.Actor",
                }
            ],
        },
    }
    details.update(details_overrides)
    return {
        "schemaVersion": "1.1",
        "exporterVersion": "0.8.0",
        "assetPath": "/Game/UEAgentKitTests/Maps/L_Min.L_Min",
        "packageName": "/Game/UEAgentKitTests/Maps/L_Min",
        "assetName": "L_Min",
        "assetClass": "/Script/Engine.World",
        "assetReader": "world-v1",
        "assetReaderStatus": "success",
        "revision": {"value": "sha256:" + "1" * 64, "packageDirty": False},
        "assetDetails": details,
    }


class WorldIndexV2TestCase(unittest.TestCase):
    """Shared temporary database / export-root plumbing."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ueak_world_v2_")
        self.addCleanup(self._temporary.cleanup)
        self.root = Path(self._temporary.name)
        self.database_path = self.root / "index.sqlite3"
        self._connections = contextlib.ExitStack()
        self.addCleanup(self._connections.close)

    def connect(self) -> sqlite3.Connection:
        return self._connections.enter_context(open_database(self.database_path))


class IngestTests(WorldIndexV2TestCase):
    def test_ingest_stores_world_identity_and_aggregates(self) -> None:
        connection = self.connect()
        result = ingest_world(connection, load_world_document())

        self.assertEqual(result["status"], "added")
        self.assertEqual(result["asset_path"], WORLD_ASSET)
        self.assertEqual(result["actors"], 5)
        self.assertEqual(result["descriptors"], 3)
        self.assertEqual(result["actor_tags"], 6)
        self.assertEqual(result["component_class_rows"], 7)
        self.assertFalse(result["partial"])
        self.assertFalse(result["actor_list_truncated"])
        self.assertFalse(result["actor_desc_list_truncated"])

        world = get_world(connection, WORLD_ASSET)
        assert world is not None
        self.assertEqual(world["world_type"], "Map")
        self.assertEqual(world["asset_class"], "/Script/Engine.World")
        self.assertEqual(
            world["persistent_level_path"],
            "/Game/UEAgentKitTests/Maps/L_Smoke.L_Smoke:PersistentLevel",
        )
        self.assertEqual(world["using_external_actors"], 1)
        self.assertEqual(world["reader_name"], "world-v1")
        self.assertEqual(world["reader_status"], "success")
        self.assertEqual(world["loaded_actor_count"], 5)
        self.assertEqual(world["exported_actor_count"], 5)
        self.assertEqual(world["component_count"], 7)
        self.assertEqual(world["streaming_level_count"], 1)
        self.assertEqual(world["world_settings_available"], 1)
        self.assertEqual(world["world_partition_available"], 1)
        self.assertEqual(world["actor_desc_count"], 3)
        self.assertEqual(world["package_dirty"], 0)
        self.assertTrue(world["revision_value"].startswith("sha256:"))
        self.assertEqual(world["counts"]["actors"], 5)
        self.assertEqual(world["counts"]["descriptors"], 3)
        self.assertEqual(world["counts"]["actor_tags"], 6)

        # source-provided aggregates are stored verbatim, per source kind
        component_counts = {
            item["classPath"]: item["count"]
            for item in world["classCounts"]
            if item["source"] == "component"
        }
        self.assertEqual(
            component_counts,
            {
                "/Script/Engine.BillboardComponent": 1,
                "/Script/Engine.CapsuleComponent": 1,
                "/Script/Engine.PointLightComponent": 2,
                "/Script/Engine.SphereComponent": 1,
                "/Script/Engine.StaticMeshComponent": 2,
            },
        )
        actor_counts = {
            item["classPath"]: item["count"]
            for item in world["classCounts"]
            if item["source"] == "actor"
        }
        self.assertEqual(sum(actor_counts.values()), 5)

        # world settings / partition / streaming levels are kept as source JSON
        self.assertEqual(world["worldSettings"]["gravityZ"], -980.0)
        self.assertEqual(world["worldSettings"]["worldToMeters"], 100.0)
        self.assertTrue(world["worldPartition"]["streamingEnabled"])
        self.assertEqual(len(world["streamingLevels"]), 1)
        self.assertEqual(world["tags"], ["Environment", "Gameplay", "Lighting", "Rock"])
        self.assertEqual(world["dataLayers"], ["DL_Night", "DL_World"])

    def test_ingest_preserves_actor_identity_transform_and_components(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())

        page = find_world_actors(
            connection, world=WORLD_ASSET, label_contains=SM_ROCK, include_component_classes=True
        )
        self.assertEqual(page["total"], 1)
        actor = page["actors"][0]
        self.assertEqual(actor["actor_guid"], "a1000000-0000-4000-8000-000000000001")
        self.assertEqual(actor["instance_guid"], "b1000000-0000-4000-8000-000000000001")
        self.assertEqual(actor["class_path"], "/Script/Engine.StaticMeshActor")
        self.assertEqual(actor["label"], SM_ROCK)
        self.assertEqual(actor["folder_path"], "Environment/Rocks")
        self.assertEqual(actor["location_x"], 1200.5)
        self.assertEqual(actor["location_y"], -300.25)
        self.assertEqual(actor["location_z"], 88.0)
        self.assertAlmostEqual(actor["rotation_z"], 0.3826834, places=6)
        self.assertAlmostEqual(actor["rotation_w"], 0.9238795, places=6)
        self.assertEqual(actor["scale_x"], 1.5)
        self.assertEqual(actor["component_count"], 2)
        self.assertEqual(actor["tags"], ["Environment", "Rock"])
        self.assertEqual(actor["dataLayers"], ["DL_World"])
        self.assertEqual(
            actor["componentClasses"],
            [
                {"classPath": "/Script/Engine.BillboardComponent", "count": 1},
                {"classPath": "/Script/Engine.StaticMeshComponent", "count": 1},
            ],
        )
        self.assertEqual(actor["root_mobility"], "Static")
        self.assertEqual(actor["root_mobility_value"], 0)
        self.assertEqual(actor["spatially_loaded"], 1)
        self.assertEqual(actor["replicated"], 0)
        self.assertEqual(actor["collision_enabled"], 1)
        self.assertEqual(actor["world_asset_path"], WORLD_ASSET)

        without_components = find_world_actors(connection, world=WORLD_ASSET, tag="Lighting")
        self.assertNotIn("componentClasses", without_components["actors"][0])

    def test_tags_and_data_layers_are_normalized_per_actor(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())

        rows = connection.execute(
            "SELECT actor_key, tag FROM world_v2_actor_tags ORDER BY tag, actor_key"
        ).fetchall()
        self.assertEqual(len(rows), 6)
        self.assertIn(("a1000000-0000-4000-8000-000000000004", "Lighting"), [tuple(r) for r in rows])

        layers = connection.execute(
            "SELECT actor_key, data_layer FROM world_v2_actor_data_layers ORDER BY data_layer, actor_key"
        ).fetchall()
        torch_key = "a1000000-0000-4000-8000-000000000004"
        torch_layers = sorted(str(row[1]) for row in layers if str(row[0]) == torch_key)
        self.assertEqual(torch_layers, ["DL_Night", "DL_World"])

        # the actor whose tags/data layers are empty gets no rows, not a NULL row
        empty_key = "a1000000-0000-4000-8000-000000000003"
        self.assertEqual(
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM world_v2_actor_tags WHERE actor_key = ?", (empty_key,)
                ).fetchone()[0]
            ),
            0,
        )

    def test_absent_optional_fields_stay_null(self) -> None:
        connection = self.connect()
        ingest_world(connection, minimal_world_document())

        row = connection.execute("SELECT * FROM world_v2_actors").fetchone()
        assert row is not None
        self.assertIsNone(row["location_x"])
        self.assertIsNone(row["location_y"])
        self.assertIsNone(row["location_z"])
        self.assertIsNone(row["rotation_w"])
        self.assertIsNone(row["scale_x"])
        self.assertIsNone(row["spatially_loaded"])
        self.assertIsNone(row["editor_only"])
        self.assertIsNone(row["component_count"])
        self.assertEqual(json.loads(row["tags_json"]), [])
        self.assertEqual(json.loads(row["component_classes_json"]), [])
        self.assertEqual(
            int(
                connection.execute(
                    "SELECT COUNT(*) FROM world_v2_actor_component_classes"
                ).fetchone()[0]
            ),
            0,
            "no component classes were exported, so none may be invented",
        )

        descriptor = connection.execute("SELECT * FROM world_v2_world_partition_actors").fetchone()
        assert descriptor is not None
        self.assertIsNone(descriptor["location_x"])
        self.assertIsNone(descriptor["editor_bounds_valid"])
        self.assertEqual(json.loads(descriptor["editor_bounds_json"]), {})
        self.assertEqual(json.loads(descriptor["runtime_bounds_json"]), {})

    def test_world_partition_descriptors_keep_bounds_and_metadata(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())

        page = find_world_partition_actors(connection, world=WORLD_ASSET)
        self.assertEqual(page["total"], 3)
        by_label = {item["label"]: item for item in page["actors"]}
        self.assertEqual(sorted(by_label), [TORCH, NIGHT_LIGHT, SM_ROCK])

        rock = by_label[SM_ROCK]
        self.assertEqual(rock["base_class_path"], "/Script/Engine.StaticMeshActor")
        self.assertEqual(rock["native_class_path"], "/Script/Engine.StaticMeshActor")
        self.assertEqual(rock["actor_package"], "/Game/UEAgentKitTests/Maps/L_Smoke")
        self.assertEqual(rock["editor_bounds_valid"], 1)
        self.assertEqual(rock["editorBounds"]["min"]["x"], 1050.0)
        self.assertEqual(rock["editorBounds"]["size"]["z"], 176.0)
        self.assertEqual(rock["runtimeBounds"]["max"]["y"], -150.0)
        self.assertEqual(rock["location_x"], 1200.5)
        self.assertEqual(rock["tags"], ["Environment"])
        self.assertEqual(rock["dataLayers"], ["DL_World"])
        self.assertEqual(rock["loaded"], 1)
        self.assertEqual(rock["runtime_relevant"], 1)
        self.assertFalse(rock["actor_desc_list_truncated"])
        self.assertEqual(rock["exported_actor_desc_count"], 3)

        torch = by_label[TORCH]
        self.assertEqual(torch["base_class_path"], "/Game/UEAgentKitTests/BP_Torch.BP_Torch_C")
        self.assertEqual(torch["native_class_path"], "/Script/Engine.Actor")

        filtered = find_world_partition_actors(connection, world=WORLD_ASSET, tag="Lighting")
        self.assertEqual([item["label"] for item in filtered["actors"]], [TORCH, NIGHT_LIGHT])
        by_layer = find_world_partition_actors(connection, world=WORLD_ASSET, data_layer="DL_Night")
        self.assertEqual(by_layer["total"], 2)
        by_class = find_world_partition_actors(
            connection, world=WORLD_ASSET, class_contains="BP_Torch"
        )
        self.assertEqual([item["label"] for item in by_class["actors"]], [TORCH])
        by_folder = find_world_partition_actors(
            connection, world=WORLD_ASSET, folder_prefix="Lighting"
        )
        self.assertEqual(by_folder["total"], 2)

    def test_idempotent_reingest_is_a_no_op(self) -> None:
        connection = self.connect()
        document = load_world_document()
        first = ingest_world(connection, document)
        self.assertEqual(first["status"], "added")
        world_before = get_world(connection, WORLD_ASSET)
        assert world_before is not None

        second = ingest_world(connection, load_world_document())
        self.assertEqual(second["status"], "unchanged")
        self.assertEqual(second["world_id"], first["world_id"])
        self.assertEqual(second["actors"], 0)

        world_after = get_world(connection, WORLD_ASSET)
        assert world_after is not None
        self.assertEqual(world_after["counts"], world_before["counts"])
        self.assertEqual(world_after["revision_value"], world_before["revision_value"])
        self.assertEqual(world_after["ingested_at_utc"], world_before["ingested_at_utc"])
        self.assertEqual(world_index_status(connection)["actors"], 5)

        forced = ingest_world(connection, load_world_document(), force=True)
        self.assertEqual(forced["status"], "updated")
        self.assertEqual(world_index_status(connection)["actors"], 5, "force must not duplicate rows")

    def test_revision_update_replaces_rows_and_records_timestamp(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())
        before = get_world(connection, WORLD_ASSET)
        assert before is not None

        updated = load_world_document()
        updated["revision"]["value"] = "sha256:" + "4" * 64
        updated["assetDetails"]["actors"][0]["label"] = "SM_Rock_Moved"
        updated["assetDetails"]["actors"][0]["transform"]["translation"]["x"] = 2048.0
        updated["assetDetails"]["actors"][0]["tags"] = ["Environment", "Moved"]
        result = ingest_world(connection, updated)

        self.assertEqual(result["status"], "updated")
        self.assertTrue(result["revision_changed"])
        self.assertEqual(result["actors"], 5)

        after = get_world(connection, WORLD_ASSET)
        assert after is not None
        self.assertEqual(after["revision_value"], "sha256:" + "4" * 64)
        self.assertEqual(after["counts"]["actors"], 5)
        self.assertGreaterEqual(after["revision_updated_at_utc"], before["ingested_at_utc"])

        moved = find_world_actors(connection, world=WORLD_ASSET, label_contains="Moved")
        self.assertEqual(len(moved["actors"]), 1)
        self.assertEqual(moved["actors"][0]["location_x"], 2048.0)
        self.assertEqual(moved["actors"][0]["tags"], ["Environment", "Moved"])
        # the previous tag is gone because rows were replaced, not appended
        self.assertEqual(find_world_actors(connection, world=WORLD_ASSET, tag="Rock")["total"], 0)

    def test_same_revision_new_document_updates_in_place(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())
        before = get_world(connection, WORLD_ASSET)
        assert before is not None

        revised = load_world_document()
        revised["assetDetails"]["actors"][4]["label"] = "PlayerStart_Renamed"
        result = ingest_world(connection, revised)
        self.assertEqual(result["status"], "updated")
        self.assertFalse(result["revision_changed"])

        after = get_world(connection, WORLD_ASSET)
        assert after is not None
        self.assertEqual(after["revision_value"], before["revision_value"])
        self.assertEqual(after["revision_updated_at_utc"], before["revision_updated_at_utc"])
        self.assertEqual(
            find_world_actors(connection, world=WORLD_ASSET, label_contains="Renamed")["total"], 1
        )

    def test_truncated_export_is_flagged_partial(self) -> None:
        connection = self.connect()
        document = load_world_document()
        document["assetDetails"]["loadedActorCount"] = 45000
        document["assetDetails"]["exportedActorCount"] = 20000
        document["assetDetails"]["actorListTruncated"] = True
        document["assetDetails"]["worldPartition"]["actorDescCount"] = 5000
        document["assetDetails"]["worldPartition"]["exportedActorDescCount"] = 2000
        document["assetDetails"]["worldPartition"]["actorDescListTruncated"] = True
        document["assetDetails"]["worldPartition"]["actorDescMetadataAvailable"] = False

        result = ingest_world(connection, document)
        self.assertTrue(result["partial"])
        self.assertTrue(result["actor_list_truncated"])
        self.assertTrue(result["actor_desc_list_truncated"])
        self.assertEqual(
            sorted(result["partial_reasons"]),
            ["actor-desc-metadata-unavailable", "actor-descs-truncated", "actors-truncated"],
        )

        world = get_world(connection, WORLD_ASSET)
        assert world is not None
        self.assertEqual(world["partial"], 1)
        self.assertEqual(world["loaded_actor_count"], 45000)
        self.assertEqual(world["exported_actor_count"], 20000)
        self.assertEqual(world["actor_desc_metadata_available"], 0)
        self.assertEqual(
            sorted(world["partial_reasons"]),
            ["actor-desc-metadata-unavailable", "actor-descs-truncated", "actors-truncated"],
        )

        listed = list_worlds(connection)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["actor_list_truncated"], 1)
        self.assertEqual(listed[0]["actor_desc_list_truncated"], 1)
        self.assertEqual(listed[0]["partial"], 1)
        self.assertEqual(listed[0]["actors"], 5)
        self.assertEqual(world_index_status(connection)["partialWorlds"], [WORLD_ASSET])

        # partial data is still queryable; the flag just says it is incomplete
        self.assertEqual(find_world_actors(connection, world=WORLD_ASSET)["total"], 5)

    def test_unmatched_external_package_inventory_is_flagged_partial(self) -> None:
        connection = self.connect()
        document = load_world_document()
        details = document["assetDetails"]
        details["usingExternalActors"] = True
        world_partition = details["worldPartition"]
        world_partition.update(
            {
                "externalActorPackageCount": 2,
                "externalActorDescriptorPackageCount": 1,
                "externalActorPackageCoverage": "unmatched",
                "unmatchedExternalActorPackageCount": 1,
                "unmatchedExternalActorPackages": [
                    "/Game/__ExternalActors__/UEAgentKitTests/Maps/L_Smoke/0/AA/DUPLICATE"
                ],
            }
        )

        result = ingest_world(connection, document)
        self.assertTrue(result["partial"])
        self.assertIn("actor-desc-external-packages-unmatched", result["partial_reasons"])
        world = get_world(connection, WORLD_ASSET)
        assert world is not None
        self.assertEqual(world["partial"], 1)
        self.assertIn("actor-desc-external-packages-unmatched", world["partial_reasons"])
        # The package list is diagnostic only.  The canonical actor rows remain
        # exactly the rows the native descriptor enumerator returned.
        self.assertEqual(world["counts"]["descriptors"], 3)

    def test_unknown_external_package_inventory_is_flagged_partial(self) -> None:
        document = load_world_document()
        details = document["assetDetails"]
        details["usingExternalActors"] = True
        details["worldPartition"]["externalActorPackageCoverage"] = "unknown"
        result = parse_world_canonical(document)
        self.assertTrue(result.flags["partial"])
        self.assertIn(
            "actor-desc-external-package-inventory-unavailable",
            result.flags["partial_reasons"],
        )

    def test_reader_failure_status_is_flagged_partial(self) -> None:
        connection = self.connect()
        document = load_world_document()
        document["assetReaderStatus"] = "failed"
        document["assetReaderError"] = "Failed to load World asset."
        result = ingest_world(connection, document)
        self.assertTrue(result["partial"])
        self.assertIn("reader-status:failed", result["partial_reasons"])
        world = get_world(connection, WORLD_ASSET)
        assert world is not None
        self.assertEqual(world["reader_status"], "failed")

    def test_ingest_accepts_the_details_shape_the_indexer_stores(self) -> None:
        connection = self.connect()
        document = load_world_document()
        # this mirrors symbols.details_json: {"symbol": {...}} with the details inside
        indexed_shape = {
            "symbol": {
                "kind": "asset",
                "assetPath": WORLD_ASSET,
                "class": "/Script/Engine.World",
                "assetReader": "world-v1",
                "assetReaderStatus": "success",
                "assetDetails": document["assetDetails"],
            }
        }
        result = ingest_world(connection, indexed_shape)
        self.assertEqual(result["status"], "added")
        self.assertEqual(result["asset_path"], WORLD_ASSET)
        self.assertEqual(result["actors"], 5)

        # and the bare details object on its own cannot invent an identity
        with self.assertRaises(WorldIndexError):
            ingest_world(connection, document["assetDetails"])

    def test_delete_world_removes_only_its_rows(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())
        ingest_world(connection, as_duplicate_map(load_world_document()))

        deleted = delete_world(connection, WORLD_ASSET)
        self.assertTrue(deleted["deleted"])
        self.assertIsNone(get_world(connection, WORLD_ASSET))
        self.assertEqual(find_world_actors(connection, world=WORLD_ASSET)["total"], 0)
        self.assertEqual(find_world_actors(connection, world=WORLD_TWO_ASSET)["total"], 5)

        status = world_index_status(connection)
        self.assertEqual(status["worlds"], 1)
        self.assertEqual(status["actors"], 5)
        self.assertEqual(status["tables"]["world_v2_world_partition_actors"], 3)

        self.assertFalse(delete_world(connection, WORLD_ASSET)["deleted"])


class IsolationTests(WorldIndexV2TestCase):
    def test_two_maps_with_shared_actor_guids_stay_separate(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())
        ingest_world(connection, as_duplicate_map(load_world_document()))

        self.assertEqual(world_index_status(connection)["worlds"], 2)
        self.assertEqual(world_index_status(connection)["actors"], 10)

        first = find_world_actors(connection, world=WORLD_ASSET)
        second = find_world_actors(connection, world=WORLD_TWO_ASSET)
        self.assertEqual(first["total"], 5)
        self.assertEqual(second["total"], 5)
        self.assertEqual(first["worlds"], [WORLD_ASSET])
        self.assertEqual(second["worlds"], [WORLD_TWO_ASSET])
        # identical GUIDs, different map: the label proves rows are not shared
        self.assertIn(SM_ROCK, [actor["label"] for actor in first["actors"]])
        self.assertIn("SM_Rock_DuplicateMap", [actor["label"] for actor in second["actors"]])

        # filters are evaluated inside one map, not across both
        self.assertEqual(
            find_world_actors(
                connection, world=WORLD_TWO_ASSET, label_contains=SM_ROCK
            )["total"],
            1,
        )
        self.assertEqual(
            find_world_actors(connection, world=WORLD_ASSET, tag="Lighting")["total"], 2
        )
        self.assertEqual(
            find_world_partition_actors(connection, world=WORLD_TWO_ASSET)["total"], 3
        )

        # a cross-map query returns both maps, still attributable per row
        across = find_world_actors(connection, tag="Lighting")
        self.assertEqual(across["total"], 4)
        self.assertEqual(across["worlds"], sorted([WORLD_ASSET, WORLD_TWO_ASSET]))
        self.assertEqual({actor["world_asset_path"] for actor in across["actors"]}, set(across["worlds"]))

    def test_unknown_world_filter_returns_empty(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())
        page = find_world_actors(connection, world="/Game/UEAgentKitTests/Maps/L_Missing")
        self.assertEqual(page["total"], 0)
        self.assertEqual(page["actors"], [])
        self.assertEqual(page["worlds"], [])
        self.assertFalse(page["hasMore"])


class FilterAndPaginationTests(WorldIndexV2TestCase):
    def setUp(self) -> None:
        super().setUp()
        connection = self.connect()
        ingest_world(connection, load_world_document())
        ingest_world(connection, as_duplicate_map(load_world_document()))
        self.connection = connection

    def labels(self, **filters: Any) -> list[str]:
        filters.setdefault("world", WORLD_ASSET)
        return [actor["label"] for actor in find_world_actors(self.connection, **filters)["actors"]]

    def test_class_and_label_filters(self) -> None:
        self.assertEqual(self.labels(class_path="/Script/Engine.PointLight"), [NIGHT_LIGHT])
        self.assertEqual(self.labels(class_contains="BP_Torch"), [TORCH])
        self.assertEqual(self.labels(label_contains="Torch"), [TORCH])
        self.assertEqual(self.labels(class_path="/Script/Engine.Actor"), [])

    def test_tag_and_data_layer_filters(self) -> None:
        self.assertEqual(self.labels(tag="Lighting"), [TORCH, NIGHT_LIGHT])
        self.assertEqual(self.labels(tag="Gameplay"), [TORCH, PLAYER_START])
        self.assertEqual(self.labels(data_layer="DL_Night"), [TORCH, NIGHT_LIGHT])
        self.assertEqual(self.labels(data_layer="DL_World"), [TORCH, SM_ROCK])
        self.assertEqual(self.labels(tag="Lighting", data_layer="DL_World"), [TORCH])
        self.assertEqual(self.labels(tag="NoSuchTag"), [])

    def test_pagination_is_stable_and_complete(self) -> None:
        collected: list[str] = []
        offset = 0
        while True:
            page = find_world_actors(self.connection, world=WORLD_ASSET, limit=2, offset=offset)
            collected.extend(actor["label"] for actor in page["actors"])
            if not page["hasMore"]:
                break
            offset += 2
        # ORDER BY label, actor_key (byte order), so pages are stable
        self.assertEqual(collected, [TORCH, PLAYER_START, NIGHT_LIGHT, SM_ROCK, WORLD_SETTINGS_LABEL])
        self.assertEqual(len(collected), len(set(collected)), "pages must not overlap")

        single = find_world_actors(self.connection, world=WORLD_ASSET, limit=2, offset=0)
        self.assertEqual(single["total"], 5)
        self.assertTrue(single["hasMore"])
        tail = find_world_actors(self.connection, world=WORLD_ASSET, limit=2, offset=4)
        self.assertEqual(len(tail["actors"]), 1)
        self.assertFalse(tail["hasMore"])

    def test_invalid_pagination_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            find_world_actors(self.connection, world=WORLD_ASSET, limit=0)
        with self.assertRaises(ValueError):
            find_world_actors(self.connection, world=WORLD_ASSET, limit=10001)
        with self.assertRaises(ValueError):
            find_world_actors(self.connection, world=WORLD_ASSET, offset=-1)


class FailureAndRollbackTests(WorldIndexV2TestCase):
    def test_non_world_document_is_rejected_without_writing(self) -> None:
        connection = self.connect()
        document = load_world_document()
        document["assetDetails"] = {"type": "static-mesh", "lodCount": 1}
        document["symbols"] = []
        document["assetClass"] = "/Script/Engine.StaticMesh"
        with self.assertRaises(WorldIndexError):
            ingest_world(connection, document)
        self.assertEqual(world_index_status(connection)["worlds"], 0)
        self.assertEqual(world_index_status(connection)["actors"], 0)

    def test_world_asset_whose_reader_failed_is_rejected_with_a_hint(self) -> None:
        connection = self.connect()
        document = load_world_document()
        document.pop("assetDetails")
        document["symbols"] = []
        document["assetReaderStatus"] = "failed"
        with self.assertRaises(WorldIndexError) as context:
            ingest_world(connection, document)
        message = str(context.exception)
        self.assertIn("looks like a World asset", message)
        self.assertIn("assetReaderStatus=success", message)
        self.assertEqual(world_index_status(connection)["worlds"], 0)

    def test_world_asset_without_reader_details_is_explained(self) -> None:
        document = load_world_document()
        document.pop("assetDetails")
        document["symbols"] = []
        with self.assertRaises(WorldIndexError) as context:
            parse_world_canonical(document)
        self.assertIn("no world assetDetails", str(context.exception))

    def test_duplicate_actor_key_is_rejected_before_writing(self) -> None:
        connection = self.connect()
        document = load_world_document()
        document["assetDetails"]["actors"][1]["guid"] = document["assetDetails"]["actors"][0]["guid"]
        with self.assertRaises(WorldIndexError) as context:
            ingest_world(connection, document)
        self.assertIn("duplicates actor key", str(context.exception))
        self.assertEqual(world_index_status(connection)["worlds"], 0)

    def test_failed_reingest_keeps_the_previous_index_state(self) -> None:
        connection = self.connect()
        ingest_world(connection, load_world_document())
        before = get_world(connection, WORLD_ASSET)
        assert before is not None

        broken = load_world_document()
        broken["revision"]["value"] = "sha256:" + "5" * 64
        broken["assetDetails"]["actors"][1]["transform"] = "not-an-object"
        broken["assetDetails"]["actors"] = broken["assetDetails"]["actors"] + [
            {"guid": "", "name": "NoIdentity"}
        ]
        with self.assertRaises(WorldIndexError):
            ingest_world(connection, broken)

        after = get_world(connection, WORLD_ASSET)
        assert after is not None
        self.assertEqual(after["revision_value"], before["revision_value"])
        self.assertEqual(after["counts"], before["counts"])
        self.assertEqual(
            find_world_actors(connection, world=WORLD_ASSET, label_contains=SM_ROCK)["total"], 1
        )

    def test_failure_during_writes_rolls_the_whole_ingest_back(self) -> None:
        connection = self.connect()
        document = as_duplicate_map(load_world_document())

        original_insert = sys.modules["ue_agent_kit.world_index_v2"]._insert_row

        def failing_insert(conn: sqlite3.Connection, table: str, *args: Any, **kwargs: Any) -> None:
            # the world row is already written at this point: only the transaction
            # boundary can undo it
            if table == "world_v2_actors":
                raise RuntimeError("simulated failure while writing actors")
            original_insert(conn, table, *args, **kwargs)

        with mock.patch(
            "ue_agent_kit.world_index_v2._insert_row", side_effect=failing_insert
        ):
            with self.assertRaises(RuntimeError):
                ingest_world(connection, document)

        self.assertEqual(world_index_status(connection)["worlds"], 0)
        self.assertEqual(world_index_status(connection)["actors"], 0)
        self.assertIsNone(get_world(connection, WORLD_TWO_ASSET))


class CoexistenceTests(WorldIndexV2TestCase):
    """The side tables must not disturb the existing asset index."""

    def write_export(self, root: Path, asset: dict[str, Any]) -> Path:
        canonical_root = root / "canonical"
        bpctx_root = root / "bpctx"
        canonical_root.mkdir(parents=True, exist_ok=True)
        bpctx_root.mkdir(parents=True, exist_ok=True)
        relative = Path(str(asset["packageName"]).lstrip("/"))
        canonical_path = canonical_root / relative.parent / f"{relative.name}_{asset['assetName']}.json"
        canonical_path.parent.mkdir(parents=True, exist_ok=True)
        canonical_path.write_text(
            json.dumps(asset, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )
        bpctx_path = bpctx_root / relative.parent / f"{relative.name}_{asset['assetName']}.bpctx"
        bpctx_path.parent.mkdir(parents=True, exist_ok=True)
        bpctx_path.write_text("H|BPCTX|1|schema=1.1|exporter=0.2.2\r\n", encoding="utf-8", newline="")
        manifest = {
            "schemaVersion": "1.1",
            "exporterVersion": "0.8.0",
            "projectName": asset["projectName"],
            "successCount": 1,
            "failureCount": 0,
            "assets": [
                {
                    "assetPath": asset["assetPath"],
                    "success": True,
                    "jsonPath": str(canonical_path),
                    "bpctxPath": str(bpctx_path),
                    "symbols": 1,
                    "references": 0,
                    "graphs": 0,
                    "nodes": 0,
                }
            ],
        }
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return canonical_path

    def test_world_ingest_coexists_with_the_generic_asset_index(self) -> None:
        connection = self.connect()
        export_root = self.root / "asset_catalog"
        canonical_path = self.write_export(export_root, load_world_document())

        result = build_index(connection, export_root, self.database_path)
        self.assertEqual((result.added, result.failed), (1, 0))
        # the indexer now maintains the world tables in the same transaction
        self.assertEqual((result.worlds, result.world_actors), (1, 5))

        # ...so a second, manual ingest of the same document is a no-op
        ingested = ingest_world_file(connection, canonical_path)
        self.assertEqual(ingested["status"], "unchanged")
        self.assertEqual(ingested["actors"], 0)
        self.assertEqual(ingested["documentActors"], 5)

        # the pre-existing index surface is untouched
        self.assertEqual([item["asset_path"] for item in search_assets(connection, "L_Smoke")], [WORLD_ASSET])
        stats = get_stats(connection)
        self.assertEqual(stats["counts"]["assets"], 1)
        self.assertEqual(stats["profiles"], {"asset-index": 1})
        self.assertEqual(find_world_actors(connection, world=WORLD_ASSET)["total"], 5)

        status = world_index_status(connection)
        self.assertEqual(status["schemaVersion"], WORLD_INDEX_V2_SCHEMA_VERSION)
        self.assertEqual(status["worlds"], 1)
        self.assertEqual(status["actors"], 5)
        self.assertEqual(status["tables"]["world_v2_actor_tags"], 6)

    def test_ensure_schema_is_repeatable(self) -> None:
        connection = self.connect()
        self.assertEqual(ensure_schema(connection), WORLD_INDEX_V2_SCHEMA_VERSION)
        self.assertEqual(ensure_schema(connection), WORLD_INDEX_V2_SCHEMA_VERSION)
        row = connection.execute(
            "SELECT value FROM world_v2_meta WHERE key = 'schema_version'"
        ).fetchone()
        assert row is not None
        self.assertEqual(int(row[0]), WORLD_INDEX_V2_SCHEMA_VERSION)
        contract = connection.execute(
            "SELECT value FROM world_v2_meta WHERE key = 'ingest_contract'"
        ).fetchone()
        assert contract is not None
        self.assertEqual(str(contract[0]), "world-canonical-json/v1")


class ParsingTests(unittest.TestCase):
    def test_parse_reports_flags_and_details_hash(self) -> None:
        parsed = parse_world_canonical(load_world_document(), source_path="fixture")
        self.assertEqual(len(parsed.actors), 5)
        self.assertEqual(len(parsed.descriptors), 3)
        self.assertEqual(parsed.flags["loaded_actor_count"], 5)
        self.assertFalse(parsed.flags["partial"])
        self.assertEqual(len(parsed.details_sha256), 64)
        self.assertEqual(parsed.world["source_path"], "fixture")
        self.assertEqual(parsed.world["source_canonical_sha256"], parsed.details_sha256)

    def test_parse_is_deterministic(self) -> None:
        first = parse_world_canonical(load_world_document())
        second = parse_world_canonical(load_world_document())
        self.assertEqual(first.details_sha256, second.details_sha256)
        self.assertEqual([actor.row for actor in first.actors], [actor.row for actor in second.actors])

    def test_parse_does_not_mutate_the_input(self) -> None:
        document = load_world_document()
        snapshot = json.dumps(document, sort_keys=True)
        parse_world_canonical(document)
        self.assertEqual(json.dumps(document, sort_keys=True), snapshot)

    def test_missing_identity_is_rejected(self) -> None:
        document = load_world_document()
        document.pop("assetPath")
        document.pop("packageName")
        document.pop("assetName")
        document["symbols"] = []
        with self.assertRaises(WorldIndexError):
            parse_world_canonical(document)


if __name__ == "__main__":
    unittest.main()

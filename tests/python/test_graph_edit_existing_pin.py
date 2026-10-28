from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.experimental_graph_edit import (  # noqa: E402
    APPLY_CONFIRMATION_PREFIX,
    GraphEditError,
    build_apply_parameters,
    confirmation_for_plan,
    validate_preview_parameters,
    validate_preview_result,
)

ASSET = "/Game/UEAgentKitTests/BP_ConnectExisting.BP_ConnectExisting"
GRAPH = "11111111-2222-4333-8444-555555555555"
PLAN_ID = "sha256:" + "a" * 64
BEFORE_EDGE_HASH = "sha256:" + "e" * 64
VARIABLE_GUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
EXISTING_NODE = "12345678-1234-4234-8234-123456789abc"
EXISTING_PIN = "87654321-4321-4321-8321-cba987654321"


def _existing(*, direction: str, category: str, subcategory: str = "", name: str = "Target") -> dict:
    return {
        "nodeGuid": EXISTING_NODE,
        "pinGuid": EXISTING_PIN,
        "pinName": name,
        "direction": direction,
        "category": category,
        "subCategory": subcategory,
    }


def _get_node() -> dict:
    return {
        "tempId": "tmp:get",
        "nodeType": "variable_get",
        "x": 0,
        "y": 0,
        "variableName": "AgentCount",
        "variableGuid": VARIABLE_GUID,
        "variableType": "int",
    }


def _add_node() -> dict:
    return {"tempId": "tmp:add", "nodeType": "int_add", "x": 200, "y": 0}


def _preview_with_existing() -> dict:
    return {
        "schemaVersion": "ueak.graphpatch-edit-preview/1.0",
        "experimental": True,
        "readOnly": True,
        "wroteBlueprint": False,
        "savedPackage": False,
        "compiled": False,
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "packageRevision": "sha256:" + "b" * 64,
        "graphFingerprint": "sha1:" + "c" * 40,
        "beforeEdgeSetHash": BEFORE_EDGE_HASH,
        "nodeCountBefore": 3,
        "nodeCountAfter": 4,
        "connectionCount": 1,
        "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
        "nodes": [
            {
                "tempId": "tmp:get",
                "nodeType": "variable_get",
                "nodeClass": "/Script/BlueprintGraph.K2Node_VariableGet",
                "position": {"x": 0, "y": 0},
                "variableName": "AgentCount",
                "variableGuid": VARIABLE_GUID,
                "variableType": "int",
                "variableAccess": "read",
                "inputPins": [],
                "outputPins": ["AgentCount"],
                "inputPinTypes": {},
                "outputPinTypes": {"AgentCount": "int"},
            }
        ],
        "connections": [
            {
                "from": "tmp:get",
                "fromPin": "AgentCount",
                "fromType": "int",
                "toExisting": _existing(direction="input", category="int", name="Value"),
                "toType": "int",
            }
        ],
    }


class ExistingPinContractTests(unittest.TestCase):
    def test_new_getter_output_to_existing_unlinked_input(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_get_node()],
            connections=[
                {
                    "from": "tmp:get",
                    "fromPin": "AgentCount",
                    "toExisting": _existing(direction="input", category="int"),
                }
            ],
        )
        connection = request["connections"][0]
        self.assertEqual(connection["from"], "tmp:get")
        self.assertNotIn("to", connection)
        self.assertEqual(connection["toExisting"]["pinName"], "Target")

    def test_existing_output_to_new_input(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_add_node()],
            connections=[
                {
                    "fromExisting": _existing(direction="output", category="int"),
                    "to": "tmp:add",
                    "toPin": "A",
                }
            ],
        )
        connection = request["connections"][0]
        self.assertNotIn("from", connection)
        self.assertEqual(connection["fromExisting"]["category"], "int")
        self.assertEqual(connection["to"], "tmp:add")

    def test_existing_to_existing_is_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {
                        "fromExisting": _existing(direction="output", category="int"),
                        "toExisting": _existing(direction="input", category="int"),
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-existing-to-existing")

    def test_existing_endpoint_direction_must_match_side(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {
                        "fromExisting": _existing(direction="input", category="int"),
                        "to": "tmp:get",
                        "toPin": "AgentCount",
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-existing-pin-direction")

    def test_existing_endpoint_conflicts_with_temp_id_side(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {
                        "from": "tmp:get",
                        "fromPin": "AgentCount",
                        "fromExisting": _existing(direction="output", category="int"),
                        "to": "tmp:get",
                        "toPin": "AgentCount",
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-existing-endpoint-conflict")

    def test_existing_type_must_match_new_pin(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {
                        "from": "tmp:get",
                        "fromPin": "AgentCount",
                        "toExisting": _existing(direction="input", category="bool"),
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-pin-type-mismatch")

    def test_existing_unsupported_pin_type_is_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {
                        "from": "tmp:get",
                        "fromPin": "AgentCount",
                        "toExisting": _existing(direction="input", category="vector"),
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-existing-pin-type-not-supported")

    def test_existing_pin_unknown_field_is_rejected(self) -> None:
        bad = _existing(direction="input", category="int") | {"container": "array"}
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {"from": "tmp:get", "fromPin": "AgentCount", "toExisting": bad}
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-unknown-parameter")

    def test_preview_round_trips_existing_pin_into_apply(self) -> None:
        preview = _preview_with_existing()
        normalized = validate_preview_result(preview)
        self.assertEqual(
            normalized["connections"][0]["toExisting"]["pinGuid"],
            EXISTING_PIN,
        )
        applied = build_apply_parameters(
            preview,
            confirmation_for_plan(preview["planId"]),
        )
        self.assertEqual(applied["connections"][0]["toExisting"]["pinName"], "Value")
        self.assertNotIn("to", applied["connections"][0])

    def test_preview_before_edge_set_hash_must_be_sha256(self) -> None:
        preview = _preview_with_existing()
        preview["beforeEdgeSetHash"] = "not-a-hash"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_result(preview)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")

    def test_preview_existing_type_must_match_declared(self) -> None:
        preview = _preview_with_existing()
        preview["connections"][0]["toExisting"]["category"] = "bool"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_result(preview)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")

    def test_preview_both_existing_is_rejected(self) -> None:
        preview = _preview_with_existing()
        preview["connections"][0] = {
            "fromExisting": _existing(direction="output", category="int"),
            "fromType": "int",
            "toExisting": _existing(direction="input", category="int"),
            "toType": "int",
        }
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_result(preview)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

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
SECOND_NODE = "22345678-2234-4234-8234-223456789abc"
SECOND_PIN = "97654321-4321-4321-8321-dba987654321"


def _existing(
    *,
    direction: str,
    category: str,
    subcategory: str = "",
    name: str = "Target",
    node: str = EXISTING_NODE,
    pin: str = EXISTING_PIN,
) -> dict:
    return {
        "nodeGuid": node,
        "pinGuid": pin,
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

    def test_existing_to_existing_single_edge_allows_zero_nodes(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[],
            connections=[
                {
                    "fromExisting": _existing(
                        direction="output",
                        category="int",
                        node=EXISTING_NODE,
                        pin=EXISTING_PIN,
                    ),
                    "toExisting": _existing(
                        direction="input",
                        category="int",
                        node=SECOND_NODE,
                        pin=SECOND_PIN,
                    ),
                }
            ],
        )
        self.assertEqual(request["nodes"], [])
        self.assertEqual(len(request["connections"]), 1)
        self.assertEqual(
            request["connections"][0]["toExisting"]["nodeGuid"],
            SECOND_NODE,
        )

    def test_existing_to_existing_requires_zero_nodes_and_one_edge(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {
                        "fromExisting": _existing(direction="output", category="int"),
                        "toExisting": _existing(
                            direction="input",
                            category="int",
                            node=SECOND_NODE,
                            pin=SECOND_PIN,
                        ),
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-existing-to-existing-plan")

    def test_existing_to_existing_limit_is_one(self) -> None:
        pair = {
            "fromExisting": _existing(direction="output", category="int"),
            "toExisting": _existing(
                direction="input",
                category="int",
                node=SECOND_NODE,
                pin=SECOND_PIN,
            ),
        }
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[],
                connections=[pair, pair],
            )
        self.assertIn(
            raised.exception.code,
            ("graph-edit-existing-to-existing-limit", "graph-edit-connection-duplicate"),
        )

    def test_empty_plan_and_zero_nodes_without_existing_edge_fail_closed(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-empty-plan")

        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[],
                connections=[
                    {
                        "from": "tmp:get",
                        "fromPin": "AgentCount",
                        "toExisting": _existing(direction="input", category="int"),
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-node-reference-unknown")

    def test_existing_to_existing_self_connection_is_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[],
                connections=[
                    {
                        "fromExisting": _existing(direction="output", category="int"),
                        "toExisting": _existing(direction="input", category="int"),
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-self-connection")

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


    def test_preview_existing_to_existing_round_trips_zero_nodes(self) -> None:
        preview = {
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
            "nodeCountBefore": 2,
            "nodeCountAfter": 2,
            "connectionCount": 1,
            "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
            "nodes": [],
            "connections": [
                {
                    "fromExisting": _existing(direction="output", category="int"),
                    "fromType": "int",
                    "toExisting": _existing(
                        direction="input",
                        category="int",
                        node=SECOND_NODE,
                        pin=SECOND_PIN,
                    ),
                    "toType": "int",
                }
            ],
        }
        normalized = validate_preview_result(preview)
        self.assertEqual(normalized["nodes"], [])
        self.assertEqual(len(normalized["connections"]), 1)
        applied = build_apply_parameters(
            preview,
            confirmation_for_plan(preview["planId"]),
        )
        self.assertEqual(applied["nodes"], [])
        self.assertEqual(
            applied["connections"][0]["fromExisting"]["nodeGuid"],
            EXISTING_NODE,
        )


    def test_disconnect_requires_zero_nodes_and_one_existing_edge(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[],
            connections=[
                {
                    "fromExisting": _existing(direction="output", category="exec"),
                    "toExisting": _existing(
                        direction="input",
                        category="exec",
                        node=SECOND_NODE,
                        pin=SECOND_PIN,
                    ),
                }
            ],
            operation="disconnect",
        )
        self.assertEqual(request["operation"], "disconnect")
        self.assertEqual(request["nodes"], [])

        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_get_node()],
                connections=[
                    {
                        "fromExisting": _existing(direction="output", category="exec"),
                        "toExisting": _existing(
                            direction="input",
                            category="exec",
                            node=SECOND_NODE,
                            pin=SECOND_PIN,
                        ),
                    }
                ],
                operation="disconnect",
            )
        self.assertEqual(raised.exception.code, "graph-edit-disconnect-invalid-plan")

    def test_disconnect_rejects_non_existing_endpoint(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[],
                connections=[
                    {
                        "fromExisting": _existing(direction="output", category="exec"),
                        "to": "tmp:get",
                        "toPin": "AgentCount",
                    }
                ],
                operation="disconnect",
            )
        self.assertEqual(raised.exception.code, "graph-edit-disconnect-invalid-plan")

    def test_invalid_operation_is_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[],
                connections=[],
                operation="rewire",
            )
        self.assertEqual(raised.exception.code, "graph-edit-invalid-operation")

    def test_preview_disconnect_round_trips_into_apply(self) -> None:
        preview = {
            "schemaVersion": "ueak.graphpatch-edit-preview/1.0",
            "experimental": True,
            "readOnly": True,
            "wroteBlueprint": False,
            "savedPackage": False,
            "compiled": False,
            "planId": PLAN_ID,
            "assetPath": ASSET,
            "graphGuid": GRAPH,
            "operation": "disconnect",
            "disconnectCount": 1,
            "packageRevision": "sha256:" + "b" * 64,
            "graphFingerprint": "sha1:" + "c" * 40,
            "beforeEdgeSetHash": BEFORE_EDGE_HASH,
            "nodeCountBefore": 4,
            "nodeCountAfter": 4,
            "connectionCount": 1,
            "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
            "nodes": [],
            "connections": [
                {
                    "fromExisting": _existing(direction="output", category="exec"),
                    "fromType": "exec",
                    "toExisting": _existing(
                        direction="input",
                        category="exec",
                        node=SECOND_NODE,
                        pin=SECOND_PIN,
                    ),
                    "toType": "exec",
                }
            ],
        }
        normalized = validate_preview_result(preview)
        self.assertEqual(normalized["operation"], "disconnect")
        applied = build_apply_parameters(
            preview,
            confirmation_for_plan(preview["planId"]),
        )
        self.assertEqual(applied["operation"], "disconnect")
        self.assertEqual(applied["nodes"], [])

    def test_preview_disconnect_requires_disconnect_count(self) -> None:
        preview = {
            "schemaVersion": "ueak.graphpatch-edit-preview/1.0",
            "experimental": True,
            "readOnly": True,
            "wroteBlueprint": False,
            "savedPackage": False,
            "compiled": False,
            "planId": PLAN_ID,
            "assetPath": ASSET,
            "graphGuid": GRAPH,
            "operation": "disconnect",
            "packageRevision": "sha256:" + "b" * 64,
            "graphFingerprint": "sha1:" + "c" * 40,
            "beforeEdgeSetHash": BEFORE_EDGE_HASH,
            "nodeCountBefore": 4,
            "nodeCountAfter": 4,
            "connectionCount": 1,
            "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
            "nodes": [],
            "connections": [
                {
                    "fromExisting": _existing(direction="output", category="exec"),
                    "fromType": "exec",
                    "toExisting": _existing(
                        direction="input",
                        category="exec",
                        node=SECOND_NODE,
                        pin=SECOND_PIN,
                    ),
                    "toType": "exec",
                }
            ],
        }
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_result(preview)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")


if __name__ == "__main__":
    unittest.main()

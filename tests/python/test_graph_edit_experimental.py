from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.experimental_graph_edit import (  # noqa: E402
    APPLY_CAPABILITY,
    APPLY_CONFIRMATION_PREFIX,
    APPLY_TOOL_NAME,
    PREVIEW_CAPABILITY,
    PREVIEW_TOOL_NAME,
    SAVE_CAPABILITY,
    SAVE_CONFIRMATION_PREFIX,
    SAVE_TOOL_NAME,
    GraphEditError,
    build_apply_parameters,
    build_save_parameters,
    confirmation_for_plan,
    save_confirmation_for_plan,
    validate_preview_parameters,
)
from ue_agent_kit.mcp_graph_edit_experimental import (  # noqa: E402
    register_graph_edit_experimental_tools,
)
from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME  # noqa: E402

PLAN_ID = "sha256:" + "a" * 64
ASSET = "/Game/UEAgentKitTests/BP_GraphEdit_Target.BP_GraphEdit_Target"
GRAPH = "11111111-2222-4333-8444-555555555555"


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorate(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorate


class _FakeLiveEditor:
    def __init__(self, capabilities: list[str] | None = None) -> None:
        self.capabilities = capabilities if capabilities is not None else [
            PREVIEW_CAPABILITY,
            APPLY_CAPABILITY,
            SAVE_CAPABILITY,
        ]
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []
        self.result: dict[str, Any] = {"applied": True, "singleTransaction": True}

    def status(self) -> dict[str, Any]:
        return {"state": "available", "capabilities": self.capabilities}

    def call_method(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, copy.deepcopy(dict(params or {})), timeout_seconds))
        return copy.deepcopy(self.result)


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "error"), "message": str(exc)},
    }


def _nodes() -> list[dict[str, Any]]:
    return [
        {"tempId": "tmp:branch", "nodeType": "branch", "x": 100, "y": 0},
        {"tempId": "tmp:sequence", "nodeType": "sequence", "x": 400, "y": 0},
    ]


def _connections() -> list[dict[str, Any]]:
    return [
        {
            "from": "tmp:branch",
            "fromPin": "then",
            "to": "tmp:sequence",
            "toPin": "execute",
        }
    ]


def _preview() -> dict[str, Any]:
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
        "nodeCountBefore": 3,
        "nodeCountAfter": 5,
        "connectionCount": 1,
        "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
        "nodes": [
            {
                "tempId": "tmp:branch",
                "nodeType": "branch",
                "nodeClass": "/Script/BlueprintGraph.K2Node_IfThenElse",
                "position": {"x": 100, "y": 0},
                "inputPins": ["execute", "Condition"],
                "outputPins": ["then", "else"],
                "inputPinTypes": {"execute": "exec", "Condition": "bool"},
                "outputPinTypes": {"then": "exec", "else": "exec"},
            },
            {
                "tempId": "tmp:sequence",
                "nodeType": "sequence",
                "nodeClass": "/Script/BlueprintGraph.K2Node_ExecutionSequence",
                "position": {"x": 400, "y": 0},
                "inputPins": ["execute"],
                "outputPins": ["then_0", "then_1"],
                "inputPinTypes": {"execute": "exec"},
                "outputPinTypes": {"then_0": "exec", "then_1": "exec"},
            },
        ],
        "connections": [
            {
                **_connections()[0],
                "fromType": "exec",
                "toType": "exec",
            }
        ],
    }


def _bool_nodes() -> list[dict[str, Any]]:
    return [
        {"tempId": "tmp:not", "nodeType": "not_bool", "x": 100, "y": 0},
        {"tempId": "tmp:branch", "nodeType": "branch", "x": 400, "y": 0},
    ]


def _bool_connections() -> list[dict[str, Any]]:
    return [
        {
            "from": "tmp:not",
            "fromPin": "ReturnValue",
            "to": "tmp:branch",
            "toPin": "Condition",
        }
    ]


def _bool_preview() -> dict[str, Any]:
    preview = _preview()
    preview["nodeCountBefore"] = 3
    preview["nodeCountAfter"] = 5
    preview["connectionCount"] = 1
    preview["nodes"] = [
        {
            "tempId": "tmp:not",
            "nodeType": "not_bool",
            "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
            "function": "Not_PreBool",
            "position": {"x": 100, "y": 0},
            "inputPins": ["A"],
            "outputPins": ["ReturnValue"],
            "inputPinTypes": {"A": "bool"},
            "outputPinTypes": {"ReturnValue": "bool"},
        },
        {
            "tempId": "tmp:branch",
            "nodeType": "branch",
            "nodeClass": "/Script/BlueprintGraph.K2Node_IfThenElse",
            "position": {"x": 400, "y": 0},
            "inputPins": ["execute", "Condition"],
            "outputPins": ["then", "else"],
            "inputPinTypes": {"execute": "exec", "Condition": "bool"},
            "outputPinTypes": {"then": "exec", "else": "exec"},
        },
    ]
    preview["connections"] = [
        {
            **_bool_connections()[0],
            "fromType": "bool",
            "toType": "bool",
        }
    ]
    return preview


class GraphEditContractTests(unittest.TestCase):
    def test_branch_and_sequence_plan_normalizes(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=_nodes(),
            connections=_connections(),
        )
        self.assertEqual([node["nodeType"] for node in request["nodes"]], ["branch", "sequence"])
        self.assertEqual(request["connections"][0]["fromPin"], "then")

    def test_call_function_is_rejected(self) -> None:
        nodes = _nodes()
        nodes[1]["nodeType"] = "callFunction"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=nodes,
                connections=_connections(),
            )
        self.assertEqual(raised.exception.code, "graph-edit-node-type-not-allowed")

    def test_bool_data_connection_is_allowed(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=_bool_nodes(),
            connections=_bool_connections(),
        )
        self.assertEqual(
            request["connections"],
            [
                {
                    "from": "tmp:not",
                    "fromPin": "ReturnValue",
                    "to": "tmp:branch",
                    "toPin": "Condition",
                }
            ],
        )
        self.assertEqual(request["nodes"][0]["nodeType"], "not_bool")

    def test_bool_type_mismatch_and_wrong_direction_fail_closed(self) -> None:
        cases = [
            (
                [
                    {"from": "tmp:not", "fromPin": "ReturnValue", "to": "tmp:branch", "toPin": "execute"},
                ],
                "graph-edit-pin-type-mismatch",
            ),
            (
                [
                    {"from": "tmp:branch", "fromPin": "then", "to": "tmp:not", "toPin": "A"},
                ],
                "graph-edit-pin-type-mismatch",
            ),
            (
                [
                    {"from": "tmp:branch", "fromPin": "Condition", "to": "tmp:not", "toPin": "A"},
                ],
                "graph-edit-pin-not-allowed",
            ),
            (
                [
                    {"from": "tmp:not", "fromPin": "ReturnValue", "to": "tmp:not", "toPin": "A"},
                ],
                "graph-edit-self-connection",
            ),
        ]
        for connections, code in cases:
            with self.subTest(code=code, connections=connections):
                with self.assertRaises(GraphEditError) as raised:
                    validate_preview_parameters(
                        asset_path=ASSET,
                        graph_guid=GRAPH,
                        nodes=_bool_nodes(),
                        connections=connections,
                    )
                self.assertEqual(raised.exception.code, code)

    def test_duplicate_bool_target_is_rejected(self) -> None:
        nodes = _bool_nodes() + [{"tempId": "tmp:not2", "nodeType": "not_bool", "x": 100, "y": 200}]
        connections = _bool_connections() + [
            {
                "from": "tmp:not2",
                "fromPin": "ReturnValue",
                "to": "tmp:branch",
                "toPin": "Condition",
            }
        ]
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=nodes,
                connections=connections,
            )
        self.assertEqual(raised.exception.code, "graph-edit-connection-duplicate")

    def test_bool_preview_declared_types_must_match_contract(self) -> None:
        preview = _bool_preview()
        params = build_apply_parameters(preview, APPLY_CONFIRMATION_PREFIX + PLAN_ID)
        self.assertEqual(params["nodes"][0]["nodeType"], "not_bool")
        self.assertEqual(params["connections"][0]["toPin"], "Condition")

        tampered = copy.deepcopy(preview)
        tampered["nodes"][0]["outputPinTypes"]["ReturnValue"] = "float"
        with self.assertRaises(GraphEditError) as raised:
            build_apply_parameters(tampered, APPLY_CONFIRMATION_PREFIX + PLAN_ID)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")

        missing_type = copy.deepcopy(preview)
        del missing_type["connections"][0]["fromType"]
        with self.assertRaises(GraphEditError) as raised:
            build_apply_parameters(missing_type, APPLY_CONFIRMATION_PREFIX + PLAN_ID)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")

    def test_data_pin_and_self_connection_are_rejected(self) -> None:
        nodes = _nodes()
        connection = _connections()
        connection[0]["fromPin"] = "Condition"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=nodes,
                connections=connection,
            )
        self.assertEqual(raised.exception.code, "graph-edit-pin-not-allowed")

        connection = _connections()
        connection[0]["to"] = "tmp:branch"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=nodes,
                connections=connection,
            )
        self.assertEqual(raised.exception.code, "graph-edit-self-connection")

    def test_duplicate_temp_and_source_are_rejected(self) -> None:
        nodes = _nodes()
        nodes[1]["tempId"] = "tmp:branch"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=nodes,
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-temp-id-duplicate")

        duplicate = _connections() + _connections()
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=_nodes(),
                connections=duplicate,
            )
        self.assertEqual(raised.exception.code, "graph-edit-connection-duplicate")

    def test_unknown_fields_are_rejected(self) -> None:
        nodes = _nodes()
        nodes[0]["script"] = "print('no')"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=nodes,
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-unknown-parameter")

    def test_apply_is_bound_to_preview_plan_and_confirmation(self) -> None:
        params = build_apply_parameters(_preview(), APPLY_CONFIRMATION_PREFIX + PLAN_ID)
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["nodes"], [
            {"tempId": "tmp:branch", "nodeType": "branch", "x": 100, "y": 0},
            {"tempId": "tmp:sequence", "nodeType": "sequence", "x": 400, "y": 0},
        ])
        self.assertEqual(params["confirmation"], APPLY_CONFIRMATION_PREFIX + PLAN_ID)

        with self.assertRaises(GraphEditError) as raised:
            build_apply_parameters(_preview(), APPLY_CONFIRMATION_PREFIX + "sha256:" + "0" * 64)
        self.assertEqual(raised.exception.code, "graph-edit-confirmation-required")

    def test_tampered_preview_is_rejected(self) -> None:
        preview = _preview()
        preview["readOnly"] = False
        with self.assertRaises(GraphEditError) as raised:
            build_apply_parameters(preview, APPLY_CONFIRMATION_PREFIX + PLAN_ID)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")

        preview = _preview()
        preview["requiredConfirmation"] = "CREATE EXPERIMENTAL GRAPH NODES other"
        with self.assertRaises(GraphEditError) as raised:
            build_apply_parameters(preview, APPLY_CONFIRMATION_PREFIX + PLAN_ID)
        self.assertEqual(raised.exception.code, "graph-edit-preview-invalid")

    def test_experimental_tools_are_not_in_the_public_registry(self) -> None:
        self.assertNotIn(PREVIEW_TOOL_NAME, TOOL_DEFINITIONS_BY_NAME)
        self.assertNotIn(APPLY_TOOL_NAME, TOOL_DEFINITIONS_BY_NAME)
        self.assertNotIn(SAVE_TOOL_NAME, TOOL_DEFINITIONS_BY_NAME)

    def test_mcp_preview_is_read_only_and_apply_uses_exact_params(self) -> None:
        live = _FakeLiveEditor()
        server = _FakeServer()
        register_graph_edit_experimental_tools(
            server=server,
            live_editor_service=live,
            tool_annotations_type=lambda **kwargs: kwargs,
            error_response=_error_response,
        )
        preview_result = _preview()
        live.result = preview_result
        preview = server.tools[PREVIEW_TOOL_NAME](
            ASSET,
            GRAPH,
            [
                {"tempId": "tmp:branch", "nodeType": "branch", "x": 100, "y": 0},
                {"tempId": "tmp:sequence", "nodeType": "sequence", "x": 400, "y": 0},
            ],
            _connections(),
        )
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["readOnly"])
        self.assertEqual(live.calls[0][0], "editor.previewBlueprintGraphEditExperimental")
        self.assertEqual(live.calls[0][2], 30.0)

        live.result = {
            "applied": True,
            "saved": False,
            "singleTransaction": True,
            "undoVerified": True,
            "assetPath": ASSET,
            "graphGuid": GRAPH,
            "planId": PLAN_ID,
            "graphFingerprintAfter": "sha1:" + "d" * 40,
            "nodeCountAfter": 5,
            "linkCountAfter": 1,
        }
        applied = server.tools[APPLY_TOOL_NAME](
            preview_result,
            APPLY_CONFIRMATION_PREFIX + PLAN_ID,
        )
        self.assertTrue(applied["ok"])
        self.assertFalse(applied["readOnly"])
        self.assertEqual(live.calls[1][0], "editor.applyBlueprintGraphEditExperimental")
        self.assertEqual(live.calls[1][1]["planId"], PLAN_ID)

        live.result = {"saved": True, "readbackVerified": True}
        saved = server.tools[SAVE_TOOL_NAME](
            live.calls[1][1] | {
                "applied": True,
                "saved": False,
                "graphFingerprintAfter": "sha1:" + "d" * 40,
                "nodeCountAfter": 5,
                "linkCountAfter": 1,
            },
            save_confirmation_for_plan(PLAN_ID),
        )
        self.assertTrue(saved["ok"])
        self.assertEqual(live.calls[2][0], "editor.saveBlueprintGraphEditExperimental")
        self.assertEqual(live.calls[2][1]["expectedNodeCount"], 5)
        self.assertEqual(live.calls[2][1]["expectedLinkCount"], 1)

    def test_mcp_rejects_missing_editor_opt_in(self) -> None:
        live = _FakeLiveEditor(capabilities=[])
        server = _FakeServer()
        register_graph_edit_experimental_tools(
            server=server,
            live_editor_service=live,
            tool_annotations_type=lambda **kwargs: kwargs,
            error_response=_error_response,
        )
        result = server.tools[PREVIEW_TOOL_NAME](ASSET, GRAPH, _nodes(), _connections())
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "graph-edit-experimental-disabled")
        self.assertEqual(live.calls, [])

    def test_server_preserves_graph_edit_error_code(self) -> None:
        from ue_agent_kit.mcp_server import _error_response

        response = _error_response(
            PREVIEW_TOOL_NAME,
            GraphEditError("graph-edit-pin-type-mismatch", "bool != exec"),
            read_only=True,
        )
        self.assertEqual(response["error"]["code"], "graph-edit-pin-type-mismatch")
        self.assertEqual(response["error"]["type"], "GraphEditError")
        self.assertFalse(response["error"]["retryable"])

    def test_mcp_returns_structured_validation_error(self) -> None:
        live = _FakeLiveEditor()
        server = _FakeServer()
        register_graph_edit_experimental_tools(
            server=server,
            live_editor_service=live,
            tool_annotations_type=lambda **kwargs: kwargs,
            error_response=_error_response,
        )
        result = server.tools[PREVIEW_TOOL_NAME](
            ASSET,
            GRAPH,
            [{"tempId": "bad", "nodeType": "branch", "x": 0, "y": 0}],
            [],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "graph-edit-temp-id-invalid")
        self.assertEqual(live.calls, [])

    def test_typed_data_fanout_is_allowed(self) -> None:
        nodes = [
            {"tempId": "tmp:add", "nodeType": "int_add", "x": 0, "y": 0},
            {"tempId": "tmp:eq1", "nodeType": "int_equal", "x": 300, "y": 0},
            {"tempId": "tmp:eq2", "nodeType": "int_equal", "x": 300, "y": 200},
            {"tempId": "tmp:or", "nodeType": "bool_or", "x": 600, "y": 0},
        ]
        connections = [
            {"from": "tmp:add", "fromPin": "ReturnValue", "to": "tmp:eq1", "toPin": "A"},
            {"from": "tmp:add", "fromPin": "ReturnValue", "to": "tmp:eq2", "toPin": "A"},
            {"from": "tmp:eq1", "fromPin": "ReturnValue", "to": "tmp:or", "toPin": "A"},
            {"from": "tmp:eq2", "fromPin": "ReturnValue", "to": "tmp:or", "toPin": "B"},
        ]
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=nodes,
            connections=connections,
        )
        self.assertEqual(len(request["connections"]), 4)
        self.assertEqual(
            sum(1 for item in request["connections"] if item["from"] == "tmp:add"),
            2,
        )

        mismatch = [
            {"tempId": "tmp:add", "nodeType": "int_add", "x": 0, "y": 0},
            {"tempId": "tmp:double", "nodeType": "double_add", "x": 300, "y": 0},
        ]
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=mismatch,
                connections=[
                    {
                        "from": "tmp:add",
                        "fromPin": "ReturnValue",
                        "to": "tmp:double",
                        "toPin": "A",
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-pin-type-mismatch")

    def test_save_is_bound_to_apply_receipt_and_post_apply_fingerprint(self) -> None:
        applied = {
            "applied": True,
            "saved": False,
            "assetPath": ASSET,
            "graphGuid": GRAPH,
            "planId": PLAN_ID,
            "graphFingerprintAfter": "sha1:" + "d" * 40,
            "nodeCountAfter": 5,
            "linkCountAfter": 2,
        }
        params = build_save_parameters(applied, save_confirmation_for_plan(PLAN_ID))
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["expectedNodeCount"], 5)
        self.assertEqual(params["expectedLinkCount"], 2)
        self.assertEqual(params["expectedGraphFingerprint"], "sha1:" + "d" * 40)

        with self.assertRaises(GraphEditError) as raised:
            build_save_parameters(applied, SAVE_CONFIRMATION_PREFIX + "sha256:" + "0" * 64)
        self.assertEqual(raised.exception.code, "graph-edit-confirmation-required")

        tampered = copy.deepcopy(applied)
        tampered["applied"] = False
        with self.assertRaises(GraphEditError) as raised:
            build_save_parameters(tampered, save_confirmation_for_plan(PLAN_ID))
        self.assertEqual(raised.exception.code, "graph-edit-save-requires-apply")

        tampered = copy.deepcopy(applied)
        tampered["graphFingerprintAfter"] = "sha1:not-a-fingerprint"
        with self.assertRaises(GraphEditError) as raised:
            build_save_parameters(tampered, save_confirmation_for_plan(PLAN_ID))
        self.assertEqual(raised.exception.code, "graph-edit-invalid-parameters")

    def test_capabilities_require_live_editor_opt_in(self) -> None:
        from ue_agent_kit.mcp_server import _capabilities_response

        response = _capabilities_response(
            None,
            object(),
            None,
            experimental_graphpatch_edit=True,
            live_editor_status={
                "state": "available",
                "capabilities": [PREVIEW_CAPABILITY, APPLY_CAPABILITY, SAVE_CAPABILITY],
            },
        )
        block = response["liveEditor"]["experimentalGraphPatchEdit"]
        self.assertTrue(block["registered"])
        self.assertTrue(block["available"])
        self.assertTrue(block["pluginCapabilityObserved"])
        self.assertFalse(block["productionDefault"])
        self.assertIn("not_bool", block["supportedNodeTypes"])
        self.assertIn("int_add", block["supportedNodeTypes"])
        self.assertIn("double_add", block["supportedNodeTypes"])
        self.assertIn("string_concat", block["supportedNodeTypes"])
        self.assertTrue(block["strictPinTypeCheck"])
        self.assertEqual(block["maxConnectionsPerNode"], 8)
        self.assertEqual(block["saveTool"], SAVE_TOOL_NAME)

        unobserved = _capabilities_response(
            None,
            object(),
            None,
            experimental_graphpatch_edit=True,
            live_editor_status={"state": "available", "capabilities": []},
        )
        block = unobserved["liveEditor"]["experimentalGraphPatchEdit"]
        self.assertTrue(block["registered"])
        self.assertFalse(block["available"])

    def test_server_and_bridge_source_contract(self) -> None:
        server_source = (ROOT / "src/ue_agent_kit/mcp_server.py").read_text(encoding="utf-8")
        bridge_source = (
            ROOT
            / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridge.cpp"
        ).read_text(encoding="utf-8")
        edit_source = (
            ROOT
            / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintGraphEditExperimental.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn("--enable-experimental-graphpatch-edit", server_source)
        self.assertIn("register_graph_edit_experimental_tools(", server_source)
        self.assertIn("editor.previewBlueprintGraphEditExperimental", bridge_source)
        self.assertIn("editor.applyBlueprintGraphEditExperimental", bridge_source)
        self.assertIn("editor.saveBlueprintGraphEditExperimental", bridge_source)
        self.assertIn("ExperimentalGraphPatchEdit", edit_source)
        self.assertIn("not_bool", edit_source)
        self.assertIn("int_add", edit_source)
        self.assertIn("double_add", edit_source)
        self.assertIn("string_concat", edit_source)
        self.assertIn("UKismetMathLibrary", edit_source)
        self.assertIn("UKismetStringLibrary", edit_source)
        self.assertIn("graph-edit-pin-type-mismatch", edit_source)
        self.assertIn("graph-edit-pie-active", edit_source)
        self.assertIn("graph-edit-package-dirty", edit_source)
        self.assertIn("RecentApplies", edit_source)


if __name__ == "__main__":
    unittest.main()

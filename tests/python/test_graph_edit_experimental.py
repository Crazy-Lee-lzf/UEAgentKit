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
    GraphEditError,
    build_apply_parameters,
    confirmation_for_plan,
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
                "inputPins": ["execute"],
                "outputPins": ["then", "else"],
            },
            {
                "tempId": "tmp:sequence",
                "nodeType": "sequence",
                "nodeClass": "/Script/BlueprintGraph.K2Node_ExecutionSequence",
                "position": {"x": 400, "y": 0},
                "inputPins": ["execute"],
                "outputPins": ["then_0", "then_1"],
            },
        ],
        "connections": _connections(),
    }


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

        live.result = {"applied": True, "singleTransaction": True, "undoVerified": True}
        applied = server.tools[APPLY_TOOL_NAME](
            preview_result,
            APPLY_CONFIRMATION_PREFIX + PLAN_ID,
        )
        self.assertTrue(applied["ok"])
        self.assertFalse(applied["readOnly"])
        self.assertEqual(live.calls[1][0], "editor.applyBlueprintGraphEditExperimental")
        self.assertEqual(live.calls[1][1]["planId"], PLAN_ID)

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

    def test_capabilities_require_live_editor_opt_in(self) -> None:
        from ue_agent_kit.mcp_server import _capabilities_response

        response = _capabilities_response(
            None,
            object(),
            None,
            experimental_graphpatch_edit=True,
            live_editor_status={
                "state": "available",
                "capabilities": [PREVIEW_CAPABILITY, APPLY_CAPABILITY],
            },
        )
        block = response["liveEditor"]["experimentalGraphPatchEdit"]
        self.assertTrue(block["registered"])
        self.assertTrue(block["available"])
        self.assertTrue(block["pluginCapabilityObserved"])
        self.assertFalse(block["productionDefault"])

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
        self.assertIn("ExperimentalGraphPatchEdit", edit_source)


if __name__ == "__main__":
    unittest.main()

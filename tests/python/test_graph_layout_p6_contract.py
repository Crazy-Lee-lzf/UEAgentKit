from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.editor_bridge import LiveEditorBridgeService  # noqa: E402
from ue_agent_kit.mcp_live_tools import register_live_read_tools  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    LIVE_EDITOR_METHODS,
    LIVE_EDITOR_TOOL_NAMES,
    TOOL_DEFINITIONS_BY_NAME,
    tool_descriptors_for_mode,
)


TOOL = "ue_get_blueprint_graph_layout_preflight"
METHOD = "editor.getBlueprintGraphLayoutPreflight"


class FakeAnnotations:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = (function, annotations)
            return function

        return decorator


class FakeLiveService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None):
        self.calls.append((tool_name, dict(params or {})))
        return {
            "schemaVersion": "1.0",
            "tool": tool_name,
            "ok": True,
            "readOnly": True,
            "source": "live-editor-memory",
            "result": {
                "schemaVersion": "ueak.graphlayoutpreflight/1.0",
                "readOnly": True,
                "available": True,
                "assetPath": "/Game/Test/BP_Test.BP_Test",
                "graphGuid": "11111111-1111-1111-1111-111111111111",
                "packageRevision": "sha256:" + "a" * 64,
                "packageDirty": False,
                "graphFingerprint": "sha1:" + "b" * 40,
                "nodeCount": 1,
                "complete": True,
                "selectionPolicy": "most-recently-active-blueprint-editor",
                "nodes": [
                    {
                        "nodeId": "n:22222222-2222-2222-2222-222222222222",
                        "guid": "22222222-2222-2222-2222-222222222222",
                        "x": 10,
                        "y": 20,
                    }
                ],
            },
        }


def _error_response(tool: str, error: Exception, *, read_only: bool) -> dict[str, Any]:
    return {"tool": tool, "ok": False, "readOnly": read_only, "error": {"message": str(error)}}


class P6ReadToolContractTest(unittest.TestCase):
    def test_registry_marks_the_tool_live_read_only(self) -> None:
        definition = TOOL_DEFINITIONS_BY_NAME[TOOL]
        self.assertEqual(definition.group, "live-read")
        self.assertTrue(definition.read_only)
        self.assertFalse(definition.destructive)
        self.assertEqual(definition.live_method, METHOD)
        self.assertIn(TOOL, LIVE_EDITOR_TOOL_NAMES)
        self.assertEqual(LIVE_EDITOR_METHODS[TOOL], METHOD)
        self.assertNotIn("applyBlueprintGraphLayout", " ".join(LIVE_EDITOR_METHODS.values()))

    def test_live_tool_registers_with_no_parameters_and_calls_only_read_method(self) -> None:
        server = FakeServer()
        service = FakeLiveService()
        register_live_read_tools(
            server=server,
            live_editor_service=service,
            index_service=None,
            audit_report_root=None,
            read_annotations=FakeAnnotations(readOnlyHint=True),
            tool_annotations_type=FakeAnnotations,
            error_response=_error_response,
        )
        self.assertIn(TOOL, server.tools)
        function, annotations = server.tools[TOOL]
        self.assertEqual(list(function.__annotations__), ["return"])
        self.assertEqual(annotations.kwargs["readOnlyHint"], True)
        result = function()
        self.assertTrue(result["ok"])
        self.assertTrue(result["readOnly"])
        self.assertEqual(service.calls, [(TOOL, {})])
        self.assertEqual(result["result"]["graphGuid"], "11111111-1111-1111-1111-111111111111")
        self.assertEqual(result["result"]["nodeCount"], 1)

    def test_rejects_any_parameter_before_the_socket_call(self) -> None:
        with self.assertRaises(Exception) as context:
            LiveEditorBridgeService._normalize_tool_params(TOOL, {"project": "other"})
        self.assertEqual(getattr(context.exception, "code", ""), "live-editor-invalid-parameters")
        self.assertEqual(LiveEditorBridgeService._normalize_tool_params(TOOL, {}), {})

    def test_descriptors_report_read_only_for_live_editor_mode(self) -> None:
        descriptors = tool_descriptors_for_mode(live_editor_enabled=True, workflow_enabled=False)
        item = next(descriptor for descriptor in descriptors if descriptor["name"] == TOOL)
        self.assertEqual(
            item,
            {"name": TOOL, "readOnly": True, "destructive": False},
        )

    def test_source_registers_one_bounded_read_bridge_method(self) -> None:
        private_root = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
        bridge = (private_root / "EditorBridge.cpp").read_text(encoding="utf-8")
        handler = (private_root / "EditorBridgeGraphHandlers.cpp").read_text(encoding="utf-8")
        self.assertIn(f'TEXT("{METHOD}")', bridge)
        self.assertIn("BuildBlueprintGraphLayoutPreflightResult", bridge)
        self.assertIn("BuildFocusedEditorPreflight", handler)
        self.assertIn("MaxReturnedNodes = 512", handler)
        self.assertIn("nodesTruncated", handler)
        self.assertIn("selectionPolicy", handler)
        self.assertIn("focusScope", handler)
        self.assertIn("PluginBinaryHash", handler)
        self.assertNotIn('TEXT("editor.applyBlueprintGraphLayout")', bridge)
        self.assertNotIn('TEXT("editor.saveBlueprintGraphLayout")', bridge)
        self.assertIn("editor.applyBlueprintGraphLayoutTestOnly", bridge)
        self.assertNotIn("UEAgentKitBlueprintGraphLayout::TryApply(", handler)

    def test_mcp_capability_contract_is_explicit(self) -> None:
        source = (SRC / "ue_agent_kit" / "mcp_server.py").read_text(encoding="utf-8")
        for token in (
            '"graphLayoutPreflight"',
            '"ue_get_blueprint_graph_layout_preflight"',
            '"editor.getBlueprintGraphLayoutPreflight"',
            '"selectionPolicy": "most-recently-active-blueprint-editor"',
            '"scope": "focused-graph-tab"',
            '"opensOrFocusesAssets": False',
            '"writesBlueprint": False',
            '"savesPackages": False',
            '"maxReturnedNodes": 512',
            '"writeToolRegistered": False',
        ):
            self.assertIn(token, source, token)


if __name__ == "__main__":
    unittest.main()

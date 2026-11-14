from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ue_agent_kit import __version__
from ue_agent_kit.editor_bridge import (
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from ue_agent_kit.mcp_live_tools import (
    GRAPH_CONTEXT_TOOL_NAME,
    MAX_GRAPH_CONTEXT_BYTES,
    register_live_read_tools,
)
from ue_agent_kit.tool_registry import tool_names_for_mode

CONTEXT_METHOD = "editor.getFocusedBlueprintGraphContext"


class FakeServer:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *, annotations):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


class FakeLiveEditor:
    def __init__(self, result=None, error=None):
        self.result = result or _context_result()
        self.error = error
        self.calls = []

    def call_tool(self, name, params=None):
        self.calls.append((name, params))
        if self.error is not None:
            raise self.error
        return {
            "schemaVersion": "1.0",
            "tool": name,
            "ok": True,
            "readOnly": True,
            "source": "live-editor-memory",
            "result": self.result,
        }


def _context_result() -> dict:
    return {
        "schemaVersion": "ueak.graphlayoutcontext/1.0",
        "available": True,
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "selectionPolicy": "most-recently-active-blueprint-editor",
        "focusScope": "toolkit-graph-tab",
        "assetPath": "/Game/Test/BP_Test.BP_Test",
        "graphGuid": "11111111-1111-1111-1111-111111111111",
        "graphName": "EventGraph",
        "packageRevision": "sha256:" + "a" * 64,
        "packageDirty": False,
        "graphFingerprint": "sha1:" + "b" * 40,
        "nodeCount": 1,
        "pinCount": 1,
        "linkCount": 0,
        "context": {"schemaVersion": "ueak.blueprintcontext/1.0", "nodes": [{"nodeGuid": "2" * 32}]},
    }


def _error_response(tool, error, *, read_only):
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(error, "code", "error"), "message": str(error)},
    }


def _handler(live_service):
    server = FakeServer()
    register_live_read_tools(
        server=server,
        live_editor_service=live_service,
        index_service=None,
        audit_report_root=None,
        read_annotations=object(),
        tool_annotations_type=lambda **kwargs: None,
        error_response=_error_response,
    )
    return server.tools[GRAPH_CONTEXT_TOOL_NAME]


class GraphContextToolTests(unittest.TestCase):
    def test_context_is_preserved_and_flagged_read_only(self) -> None:
        service = FakeLiveEditor()
        payload = _handler(service)()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["source"], "live-editor-memory")
        self.assertFalse(payload["writeAuthorized"])
        self.assertFalse(payload["editorVerified"])
        self.assertTrue(payload["contextOnly"])
        self.assertEqual(payload["result"]["graphGuid"], "11111111-1111-1111-1111-111111111111")
        self.assertIn("context", payload["result"])
        self.assertEqual(service.calls, [(GRAPH_CONTEXT_TOOL_NAME, None)])

    def test_unavailable_context_is_a_structured_error(self) -> None:
        result = _context_result()
        result.update(
            {
                "available": False,
                "reasonCode": "graph-layout-no-focused-graph",
                "reason": "No ordinary Blueprint graph is focused.",
            }
        )
        payload = _handler(FakeLiveEditor(result=result))()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "graph-layout-no-focused-graph")
        self.assertFalse(payload["writeAuthorized"])
        self.assertFalse(payload["editorVerified"])
        self.assertTrue(payload["contextOnly"])

    def test_oversize_context_is_rejected_not_truncated(self) -> None:
        result = _context_result()
        result["context"] = {"padding": "x" * (MAX_GRAPH_CONTEXT_BYTES + 1)}
        payload = _handler(FakeLiveEditor(result=result))()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "blueprint-graph-context-too-large")
        self.assertNotIn("result", payload)

    def test_disconnected_editor_is_a_structured_error(self) -> None:
        service = FakeLiveEditor(error=LiveEditorError("live-editor-connection-closed", "closed"))
        payload = _handler(service)()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-editor-connection-closed")

    def test_capability_is_checked_before_any_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "Test.uproject"
            project.write_text("{}", encoding="utf-8")
            service = LiveEditorBridgeService(LiveEditorBridgeConfig(project), server_version=__version__)
            service._read_descriptor = lambda: {"capabilities": []}  # type: ignore[method-assign]

            def must_not_call(*args, **kwargs):
                raise AssertionError("call_method must not run without the capability")

            service.call_method = must_not_call  # type: ignore[method-assign]
            with self.assertRaises(LiveEditorError) as caught:
                service.call_tool(GRAPH_CONTEXT_TOOL_NAME)
            self.assertEqual(caught.exception.code, "live-editor-capability-unavailable")

    def test_unknown_arguments_are_rejected_before_any_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "Test.uproject"
            project.write_text("{}", encoding="utf-8")
            service = LiveEditorBridgeService(LiveEditorBridgeConfig(project), server_version=__version__)
            service._read_descriptor = lambda: {"capabilities": [CONTEXT_METHOD]}  # type: ignore[method-assign]

            def must_not_call(*args, **kwargs):
                raise AssertionError("call_method must not run with unknown arguments")

            service.call_method = must_not_call  # type: ignore[method-assign]
            with self.assertRaises(LiveEditorError) as caught:
                service.call_tool(GRAPH_CONTEXT_TOOL_NAME, {"asset_path": "/Game/No/Write"})
            self.assertEqual(caught.exception.code, "live-editor-invalid-parameters")

    def test_registry_is_live_only(self) -> None:
        self.assertNotIn(GRAPH_CONTEXT_TOOL_NAME, tool_names_for_mode())
        self.assertIn(GRAPH_CONTEXT_TOOL_NAME, tool_names_for_mode(live_editor_enabled=True))
        self.assertNotIn(
            GRAPH_CONTEXT_TOOL_NAME,
            tool_names_for_mode(workflow_enabled=True, memory_enabled=True),
        )


if __name__ == "__main__":
    unittest.main()

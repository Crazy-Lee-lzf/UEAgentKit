"""0-3.2: live read of any Blueprint graph context by asset path.

The bridge handler itself is C++ and is **not compiled** in this round; these
tests pin its structural contract plus the Python wrapper's validation and
read-only behaviour.  They are not a Native PASS.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.mcp_live_tools import register_live_read_tools  # noqa: E402
from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME, tool_names_for_mode  # noqa: E402

PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
ASSET = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"
GUID = "3a5d0f7c-4364-7f33-2b52-0d9d5c864b40"


class _FakeAnnotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


class _FakeLiveService:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = result if result is not None else {
            "schemaVersion": "ueak.blueprintgraphcontext/1.0",
            "available": True,
            "readOnly": True,
            "assetPath": ASSET,
            "graphGuid": GUID,
            "nodeOffset": 0,
            "nodeLimit": 200,
            "totalNodeCount": 259,
            "returnedNodeCount": 200,
            "truncated": True,
            "context": {"graphs": []},
        }
        self.error = error

    def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, dict(params)))
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _error_response(tool: str, exc: Exception, *, read_only: bool) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "ue-agent-kit-error"), "message": str(exc)},
    }


def _register(service: Any, *, pure_live: bool = False) -> _FakeServer:
    server = _FakeServer()
    register_live_read_tools(
        server=server,
        live_editor_service=service,
        index_service=None,
        audit_report_root=None,
        read_annotations=None,
        tool_annotations_type=_FakeAnnotations,
        error_response=_error_response,
        pure_live=pure_live,
    )
    return server


class LiveGraphContextToolTests(unittest.TestCase):
    def test_tool_forwards_exact_request_and_stays_read_only(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        payload = server.tools["ue_get_blueprint_graph_context"](
            asset_path=ASSET, graph_guid=GUID, offset=10, limit=50
        )

        self.assertEqual(
            service.calls,
            [
                (
                    "editor.getBlueprintGraphContext",
                    {"assetPath": ASSET, "offset": 10, "limit": 50, "graphGuid": GUID},
                )
            ],
        )
        self.assertFalse(payload["writeAuthorized"])
        self.assertFalse(payload["editorVerified"])
        self.assertTrue(payload["contextOnly"])
        self.assertEqual(payload["returnedNodeCount"], 200)

    def test_graph_guid_is_optional(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        server.tools["ue_get_blueprint_graph_context"](asset_path=ASSET)
        self.assertEqual(
            service.calls[0][1], {"assetPath": ASSET, "offset": 0, "limit": 200}
        )

    def test_invalid_parameters_never_reach_the_bridge(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        tool = server.tools["ue_get_blueprint_graph_context"]
        for kwargs in (
            {"asset_path": "/Engine/BasicShapes/Cube.Cube"},
            {"asset_path": "/Game/Folder/BP_Thing"},
            {"asset_path": ASSET, "graph_guid": GUID.upper()},
            {"asset_path": ASSET, "offset": -1},
            {"asset_path": ASSET, "limit": 0},
            {"asset_path": ASSET, "limit": 501},
            {"asset_path": ASSET, "offset": True},
        ):
            payload = tool(**kwargs)
            self.assertFalse(payload["ok"], kwargs)
            self.assertEqual(
                payload["error"]["code"], "live-editor-invalid-parameters", kwargs
            )
        self.assertEqual(service.calls, [])

    def test_bridge_refusal_is_surfaced(self) -> None:
        service = _FakeLiveService(
            error=LiveEditorError(
                "blueprint-graph-context-graph-not-found",
                "The requested GraphGuid is not part of this Blueprint.",
            )
        )
        server = _register(service)
        payload = server.tools["ue_get_blueprint_graph_context"](
            asset_path=ASSET, graph_guid=GUID
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"], "blueprint-graph-context-graph-not-found"
        )

    def test_tool_is_registered_in_live_read_and_pure_live(self) -> None:
        definition = TOOL_DEFINITIONS_BY_NAME["ue_get_blueprint_graph_context"]
        self.assertEqual(definition.group, "live-read")
        self.assertTrue(definition.read_only)
        self.assertEqual(definition.live_method, "editor.getBlueprintGraphContext")
        self.assertIn(
            "ue_get_blueprint_graph_context", tool_names_for_mode(live_editor_enabled=True)
        )
        server = _register(_FakeLiveService(), pure_live=True)
        self.assertIn("ue_get_blueprint_graph_context", server.tools)


class LiveGraphContextSourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bridge = (PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        self.header = (PRIVATE / "EditorBridge.h").read_text(encoding="utf-8")
        self.handlers = (PRIVATE / "EditorBridgeGraphHandlers.cpp").read_text(
            encoding="utf-8"
        )

    def _handler_body(self) -> str:
        start = self.handlers.index(
            "bool FUEAgentKitEditorBridge::TryBuildBlueprintGraphContextResult("
        )
        return self.handlers[start:]

    def test_method_is_dispatchable_and_declared(self) -> None:
        self.assertIn('TEXT("editor.getBlueprintGraphContext")', self.bridge)
        self.assertIn("TryBuildBlueprintGraphContextResult", self.header)
        self.assertIn("TryBuildBlueprintGraphContextResult", self.bridge)

    def test_handler_is_read_only_and_reuses_the_exporter(self) -> None:
        body = self._handler_body()
        self.assertIn("FBlueprintContextExporter::BuildContextObject", body)
        self.assertIn("bWriteJson = false", body)
        self.assertIn("bWriteBpctx = false", body)
        for forbidden in (
            "FScopedTransaction",
            "->Modify(",
            "MarkPackageDirty",
            "SavePackage",
            "SaveAsset",
            "OpenEditorForAsset",
            "FocusWindow",
            "SetEnabled(",
        ):
            self.assertNotIn(forbidden, body, forbidden)

    def test_handler_bounds_and_filters(self) -> None:
        body = self._handler_body()
        for token in (
            "MaxOffset",
            "MaxLimit",
            "DefaultLimit",
            "Options.GraphFilter",
            "totalNodeCount",
            "returnedNodeCount",
            "truncated",
            "loadedByRequest",
            "packageDirty",
        ):
            self.assertIn(token, body, token)
        # Unknown fields must be rejected rather than silently ignored.
        self.assertIn("Unsupported field", body)


if __name__ == "__main__":
    unittest.main()

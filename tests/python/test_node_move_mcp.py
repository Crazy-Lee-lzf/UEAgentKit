from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.mcp_workflow_tools import register_workflow_tools  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_DEFINITIONS_BY_NAME,
    tool_names_for_mode,
)

TOOL = "ue_move_blueprint_node"
METHOD = "editor.moveBlueprintNode"
ASSET = "/Game/Folder/BP_Thing.BP_Thing"
GRAPH = "05ba59ca-47dc-2491-1f17-4dba2822185f"
NODE = "0a1b2c3d-4e5f-4071-8293-a4b5c6d7e8f9"


class _FakeAnnotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, tuple[Any, Any]] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = (function, annotations)
            return function

        return decorator


class _FakeLiveEditorService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error = error

    def call_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, params))
        if self.error is not None:
            raise self.error
        return {
            "action": "move-blueprint-node",
            "assetPath": params["assetPath"],
            "graphGuid": params["graphGuid"],
            "nodeGuid": params["nodeGuid"],
            "beforeX": params["expectedX"],
            "beforeY": params["expectedY"],
            "nodePosX": params["x"],
            "nodePosY": params["y"],
            "changed": True,
            "saved": False,
            "compiled": False,
            "evidenceLevel": "same-process",
            "acceptanceEligible": False,
            "requiredAcceptanceEvidence": "independent-reload",
        }


class _FakeWorkflowService:
    def __init__(
        self,
        *,
        commit_enabled: bool = True,
        live_editor_service: Any = None,
    ) -> None:
        self.config = SimpleNamespace(commit_enabled=commit_enabled, work_root=None)
        self.live_editor_service = live_editor_service


def _error_response(tool: str, exc: Exception, *, read_only: bool) -> dict[str, Any]:
    code = getattr(exc, "code", None)
    if code is None:
        code = "invalid-arguments" if isinstance(exc, ValueError) else "ue-agent-kit-error"
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": code, "message": str(exc)},
    }


def _register(
    *,
    commit_enabled: bool = True,
    live_editor_service: Any = None,
) -> tuple[_FakeServer, _FakeWorkflowService]:
    server = _FakeServer()
    workflow = _FakeWorkflowService(
        commit_enabled=commit_enabled,
        live_editor_service=live_editor_service,
    )
    register_workflow_tools(
        server=server,
        workflow_service=workflow,
        read_annotations=_FakeAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
        tool_annotations_type=_FakeAnnotations,
        error_response=_error_response,
    )
    return server, workflow


def _call(server: _FakeServer, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "asset_path": ASSET,
        "graph_guid": GRAPH,
        "node_guid": NODE,
        "x": 100,
        "y": -200,
        "expected_x": 40,
        "expected_y": -200,
        "expected_dirty": False,
    }
    payload.update(overrides)
    function, _ = server.tools[TOOL]
    return function(**payload)


class NodeMoveRegistryTests(unittest.TestCase):
    def test_tool_is_registered_as_a_destructive_workflow_tool(self) -> None:
        server, _ = _register()
        self.assertIn(TOOL, server.tools)
        _, annotations = server.tools[TOOL]
        self.assertFalse(annotations.readOnlyHint)
        self.assertTrue(annotations.destructiveHint)
        self.assertFalse(annotations.idempotentHint)
        definition = TOOL_DEFINITIONS_BY_NAME[TOOL]
        self.assertEqual(definition.group, "workflow")
        self.assertTrue(definition.destructive)
        self.assertIn(TOOL, tool_names_for_mode(workflow_enabled=True))
        self.assertNotIn(TOOL, tool_names_for_mode())


class NodeMoveToolTests(unittest.TestCase):
    def test_forwards_exact_request_and_reports_no_save(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        payload = _call(server)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], TOOL)
        self.assertEqual(payload["mode"], "ResidentEditor")
        self.assertFalse(payload["readOnly"])
        self.assertFalse(payload["saved"])
        self.assertFalse(payload["compiled"])
        self.assertEqual(payload["childUnrealProcessCount"], 0)
        self.assertEqual(
            live.calls,
            [
                (
                    METHOD,
                    {
                        "assetPath": ASSET,
                        "graphGuid": GRAPH,
                        "nodeGuid": NODE,
                        "x": 100,
                        "y": -200,
                        "expectedX": 40,
                        "expectedY": -200,
                        "expectedDirty": False,
                    },
                )
            ],
        )
        result = payload["result"]
        self.assertFalse(result["saved"])
        self.assertFalse(result["compiled"])
        self.assertEqual(result["nodePosX"], 100)

    def test_expected_dirty_true_fails_closed_without_calling_the_bridge(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        payload = _call(server, expected_dirty=True)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "node-move-dirty-ownership-unproven")
        self.assertEqual(live.calls, [])

    def test_non_boolean_expected_dirty_is_rejected(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for value in (0, 1, "false", None):
            payload = _call(server, expected_dirty=value)
            self.assertFalse(payload["ok"], value)
            self.assertEqual(payload["error"]["code"], "node-move-invalid-parameters")
        self.assertEqual(live.calls, [])

    def test_coordinates_must_be_bounded_integers(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for field in ("x", "y", "expected_x", "expected_y"):
            for value in (True, 1.5, 1_000_001, -1_000_001, "1"):
                payload = _call(server, **{field: value})
                self.assertFalse(payload["ok"], (field, value))
                self.assertEqual(payload["error"]["code"], "node-move-coordinate-invalid")
        self.assertEqual(live.calls, [])

    def test_asset_path_and_guids_are_validated_before_the_bridge(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for path in (
            "",
            "/Engine/BasicShapes/Cube.Cube",
            "/Game/Folder/BP_Thing",
            "/Game/Folder/../BP_Thing.BP_Thing",
            "/Game/Folder/.BP_Thing",
            "/Game/BP_Thing.BP_Thing.SubObject",
            "/Game/Folder/BP_Thing.BP_Thing\n",
        ):
            payload = _call(server, asset_path=path)
            self.assertFalse(payload["ok"], path)
            self.assertEqual(payload["error"]["code"], "node-move-invalid-parameters")
        for guid in (
            "not-a-guid",
            GRAPH.upper(),
            GRAPH.replace("-", ""),
            "05ba59ca-47dc-2491-1f17",
        ):
            for field in ("graph_guid", "node_guid"):
                payload = _call(server, **{field: guid})
                self.assertFalse(payload["ok"], (field, guid))
                self.assertEqual(payload["error"]["code"], "node-move-invalid-parameters")
        self.assertEqual(live.calls, [])

    def test_commit_gate_is_required(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(commit_enabled=False, live_editor_service=live)
        payload = _call(server)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "node-move-disabled")
        self.assertEqual(live.calls, [])

    def test_connected_live_editor_is_required(self) -> None:
        server, _ = _register(live_editor_service=None)
        payload = _call(server)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-editor-required")

    def test_bridge_error_code_is_propagated(self) -> None:
        live = _FakeLiveEditorService(
            error=LiveEditorError(
                "node-move-package-dirty",
                "The exact Blueprint package is already dirty.",
            )
        )
        server, _ = _register(live_editor_service=live)
        payload = _call(server)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "node-move-package-dirty")
        self.assertNotIn("result", payload)
        self.assertEqual(len(live.calls), 1)

    def test_wrapper_itself_never_spawns_or_invokes_unreal(self) -> None:
        source = (SRC_ROOT / "ue_agent_kit" / "mcp_workflow_tools.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("subprocess", "UnrealEditor", "Popen", "os.system"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit import mcp_workflow_tools  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.mcp_workflow_tools import register_workflow_tools  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_DEFINITIONS_BY_NAME,
    tool_names_for_mode,
)
from ue_agent_kit.workflow_common import WorkflowError  # noqa: E402

TOOL = "ue_move_blueprint_nodes"
SINGLE_TOOL = "ue_move_blueprint_node"
METHOD = "editor.moveBlueprintNodes"
ASSET = "/Game/Folder/BP_Thing.BP_Thing"
GRAPH = "05ba59ca-47dc-2491-1f17-4dba2822185f"
NODE_A = "0a1b2c3d-4e5f-4071-8293-a4b5c6d7e8f9"
NODE_B = "1a2b3c4d-5e6f-4071-8293-b4c5d6e7f809"


def _guid(index: int) -> str:
    return f"{index:08x}-0000-4000-8000-000000000001"


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
            "action": "move-blueprint-nodes",
            "assetPath": params["assetPath"],
            "graphGuid": params["graphGuid"],
            "nodeCount": len(params["nodes"]),
            "changedNodeCount": len(params["nodes"]),
            "nodes": [
                {
                    "nodeGuid": item["nodeGuid"],
                    "beforeX": item["expectedX"],
                    "beforeY": item["expectedY"],
                    "nodePosX": item["x"],
                    "nodePosY": item["y"],
                    "changed": True,
                }
                for item in params["nodes"]
            ],
            "changed": True,
            "saved": False,
            "compiled": False,
            "singleTransaction": True,
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


def _entry(
    node_guid: str,
    *,
    expected_x: int = 40,
    expected_y: int = 60,
    x: int = 100,
    y: int = 160,
) -> dict[str, Any]:
    return {
        "nodeGuid": node_guid,
        "expectedX": expected_x,
        "expectedY": expected_y,
        "x": x,
        "y": y,
    }


def _call(server: _FakeServer, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "asset_path": ASSET,
        "graph_guid": GRAPH,
        "nodes": [
            _entry(NODE_A),
            _entry(NODE_B, y=260),
        ],
    }
    payload.update(overrides)
    function, _ = server.tools[TOOL]
    return function(**payload)


class BatchNodeMoveRegistryTests(unittest.TestCase):
    def test_batch_tool_is_registered_as_destructive_workflow_tool(self) -> None:
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
        self.assertLess(
            tool_names_for_mode(workflow_enabled=True).index(SINGLE_TOOL),
            tool_names_for_mode(workflow_enabled=True).index(TOOL),
        )


class BatchNodeMoveToolTests(unittest.TestCase):
    def test_forwards_two_nodes_in_request_order(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        payload = _call(server)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], TOOL)
        self.assertEqual(payload["mode"], "ResidentEditor")
        self.assertFalse(payload["saved"])
        self.assertFalse(payload["compiled"])
        self.assertEqual(payload["childUnrealProcessCount"], 0)
        self.assertEqual(len(live.calls), 1)
        method, params = live.calls[0]
        self.assertEqual(method, METHOD)
        self.assertEqual(params["assetPath"], ASSET)
        self.assertEqual(params["graphGuid"], GRAPH)
        self.assertEqual(
            params["nodes"],
            [
                {
                    "nodeGuid": NODE_A,
                    "expectedX": 40,
                    "expectedY": 60,
                    "x": 100,
                    "y": 160,
                },
                {
                    "nodeGuid": NODE_B,
                    "expectedX": 40,
                    "expectedY": 60,
                    "x": 100,
                    "y": 260,
                },
            ],
        )
        self.assertFalse(payload["result"]["saved"])
        self.assertFalse(payload["result"]["compiled"])
        self.assertTrue(payload["result"]["singleTransaction"])

    def test_duplicate_node_guid_is_rejected_without_bridge_call(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        payload = _call(
            server,
            nodes=[_entry(NODE_A), _entry(NODE_A, y=260)],
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "node-move-batch-duplicate-node")
        self.assertEqual(live.calls, [])

    def test_overlapping_target_positions_are_allowed(self) -> None:
        # Overlapping node positions are legal in a UE graph, so the batch must
        # forward them instead of inventing a target-coordinate uniqueness rule.
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        payload = _call(
            server,
            nodes=[_entry(NODE_A), _entry(NODE_B)],
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(len(live.calls), 1)
        forwarded = live.calls[0][1]["nodes"]
        self.assertEqual(
            [(item["x"], item["y"]) for item in forwarded],
            [(100, 160), (100, 160)],
        )
        self.assertEqual(
            [item["nodeGuid"] for item in forwarded],
            [NODE_A, NODE_B],
        )

    def test_batch_limit_is_128(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        accepted = [
            _entry(_guid(index), expected_y=0, y=index + 10)
            for index in range(1, 129)
        ]
        payload = _call(server, nodes=accepted)
        self.assertTrue(payload["ok"])
        self.assertEqual(len(live.calls[0][1]["nodes"]), 128)

        rejected = accepted + [
            _entry(_guid(129), expected_y=0, y=1000)
        ]
        payload = _call(server, nodes=rejected)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "node-move-batch-limit-exceeded")
        self.assertEqual(len(live.calls), 1)

    def test_every_coordinate_must_be_a_bounded_integer(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for key in ("expectedX", "expectedY", "x", "y"):
            for value in (True, 1.5, 1_000_001, -1_000_001, "1"):
                entry = _entry(NODE_A)
                entry[key] = value
                payload = _call(server, nodes=[entry])
                self.assertFalse(payload["ok"], (key, value))
                self.assertEqual(
                    payload["error"]["code"],
                    "node-move-batch-coordinate-invalid",
                )
        self.assertEqual(live.calls, [])

    def test_malformed_batches_are_rejected(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        bad_batches: list[Any] = [
            [],
            "not-a-list",
            None,
            [{"nodeGuid": NODE_A}],
            [dict(_entry(NODE_A), extra=1)],
            [{"nodeGuid": NODE_A, "expectedX": 0, "expectedY": 0, "x": 1, "y": 1, "z": 2}],
            [{"nodeGuid": NODE_A.upper(), "expectedX": 0, "expectedY": 0, "x": 1, "y": 1}],
            [{"nodeGuid": "not-a-guid", "expectedX": 0, "expectedY": 0, "x": 1, "y": 1}],
            [None],
        ]
        for nodes in bad_batches:
            payload = _call(server, nodes=nodes)
            self.assertFalse(payload["ok"], nodes)
            self.assertEqual(
                payload["error"]["code"],
                "node-move-batch-invalid-parameters",
                nodes,
            )
        self.assertEqual(live.calls, [])

    def test_asset_and_graph_identity_are_validated_before_bridge(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for path in (
            "",
            "/Engine/BasicShapes/Cube.Cube",
            "/Game/Folder/BP_Thing",
            "/Game/Folder/../BP_Thing.BP_Thing",
            "/Game/BP_Thing.BP_Thing.SubObject",
        ):
            payload = _call(server, asset_path=path)
            self.assertFalse(payload["ok"], path)
            self.assertEqual(
                payload["error"]["code"],
                "node-move-batch-invalid-parameters",
            )
        for graph_guid in ("not-a-guid", GRAPH.upper(), GRAPH.replace("-", "")):
            payload = _call(server, graph_guid=graph_guid)
            self.assertFalse(payload["ok"], graph_guid)
            self.assertEqual(
                payload["error"]["code"],
                "node-move-batch-invalid-parameters",
            )
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
                "node-move-batch-position-conflict",
                "Node is not at the expected position.",
            )
        )
        server, _ = _register(live_editor_service=live)
        payload = _call(server)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "node-move-batch-position-conflict",
        )
        self.assertNotIn("result", payload)
        self.assertEqual(len(live.calls), 1)

    def test_dirty_package_rejection_is_propagated_without_claiming_success(
        self,
    ) -> None:
        live = _FakeLiveEditorService(
            error=LiveEditorError(
                "node-move-batch-package-dirty",
                "The exact Blueprint package is already dirty.",
            )
        )
        server, _ = _register(live_editor_service=live)
        payload = _call(server)
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "node-move-batch-package-dirty",
        )
        self.assertNotIn("result", payload)
        self.assertNotIn("mode", payload)
        self.assertEqual(len(live.calls), 1)

    def test_wrapper_never_spawns_or_invokes_unreal(self) -> None:
        source = (SRC_ROOT / "ue_agent_kit" / "mcp_workflow_tools.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "subprocess",
            "UnrealEditor",
            "Popen",
            "os.system",
            "UnrealEditor-Cmd",
            "-run=",
        ):
            self.assertNotIn(forbidden, source)
        tree = ast.parse(source)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported & {"subprocess", "multiprocessing", "asyncio"}, set())

    def test_python_and_cpp_batch_bounds_agree(self) -> None:
        # Static drift guard: both sides must accept the same batch size and
        # coordinate range without an Editor run.
        header = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "BlueprintEditBatch.h"
        ).read_text(encoding="utf-8")
        self.assertIn(
            f"MaxNodeMovesPerBatch = {mcp_workflow_tools._MAX_NODE_MOVE_BATCH}",
            header,
        )
        self.assertIn(
            f"MaxNodeCoordinate = {mcp_workflow_tools._MAX_NODE_MOVE_COORDINATE}",
            header,
        )


if __name__ == "__main__":
    unittest.main()

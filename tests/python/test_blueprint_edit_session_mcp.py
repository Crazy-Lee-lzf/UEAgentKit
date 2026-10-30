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

BEGIN_TOOL = "ue_begin_blueprint_edit_session"
END_TOOL = "ue_end_blueprint_edit_session"
MOVE_TOOL = "ue_move_blueprint_nodes"
BEGIN_METHOD = "editor.beginBlueprintEditSession"
END_METHOD = "editor.endBlueprintEditSession"
MOVE_METHOD = "editor.moveBlueprintNodes"
ASSET = "/Game/Folder/BP_Thing.BP_Thing"
GRAPH = "05ba59ca-47dc-2491-1f17-4dba2822185f"
TOKEN = "0123456789abcdef" * 4
NODE_A = "0a1b2c3d-4e5f-4071-8293-a4b5c6d7e8f9"


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
        if method == BEGIN_METHOD:
            return {
                "action": "begin-blueprint-edit-session",
                "editSessionToken": TOKEN,
                "assetPath": params["assetPath"],
                "graphGuid": params["graphGuid"],
                "expectedGraphFingerprint": "sha1:abc",
                "nodeCount": 3,
                "packageDirtyBefore": False,
                "packageDirtyAfter": False,
                "exclusiveUI": False,
                "humanEditingBlocked": False,
                "requiresUserNotEdit": True,
                "experimental": True,
                "rollbackSupported": False,
                "saved": False,
                "compiled": False,
                "evidenceLevel": "same-process",
                "acceptanceEligible": False,
            }
        if method == END_METHOD:
            return {
                "action": "end-blueprint-edit-session",
                "mode": params.get("mode", "keep"),
                "ended": True,
                "rolledBack": False,
                "restoredNodeCount": 0,
                "batchCount": 2,
                "movedBatchCount": 2,
                "packageDirtyAfter": True,
                "saved": False,
                "compiled": False,
                "exclusiveUI": False,
                "humanEditingBlocked": False,
                "evidenceLevel": "same-process",
                "acceptanceEligible": False,
            }
        return {
            "action": "move-blueprint-nodes",
            "assetPath": params["assetPath"],
            "graphGuid": params["graphGuid"],
            "nodeCount": len(params["nodes"]),
            "changedNodeCount": len(params["nodes"]),
            "nodes": [],
            "changed": True,
            "packageDirtyBefore": "editSessionToken" in params,
            "packageDirtyAfter": True,
            "singleTransaction": True,
            "editSessionUsed": "editSessionToken" in params,
            "exclusiveUI": False,
            "humanEditingBlocked": False,
            "saved": False,
            "compiled": False,
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


def _entry(node_guid: str = NODE_A) -> dict[str, Any]:
    return {
        "nodeGuid": node_guid,
        "expectedX": 40,
        "expectedY": 60,
        "x": 100,
        "y": 160,
    }


def _call(server: _FakeServer, tool: str, **kwargs: Any) -> dict[str, Any]:
    function, _ = server.tools[tool]
    return function(**kwargs)


class EditSessionRegistryTests(unittest.TestCase):
    def test_session_tools_are_registered_as_destructive_workflow_tools(self) -> None:
        server, _ = _register()
        for tool in (BEGIN_TOOL, END_TOOL):
            self.assertIn(tool, server.tools)
            _, annotations = server.tools[tool]
            self.assertFalse(annotations.readOnlyHint)
            self.assertTrue(annotations.destructiveHint)
            self.assertFalse(annotations.idempotentHint)
            definition = TOOL_DEFINITIONS_BY_NAME[tool]
            self.assertEqual(definition.group, "workflow")
            self.assertTrue(definition.destructive)
            self.assertIn(tool, tool_names_for_mode(workflow_enabled=True))
            self.assertNotIn(tool, tool_names_for_mode())
        order = tool_names_for_mode(workflow_enabled=True)
        self.assertLess(order.index(BEGIN_TOOL), order.index(END_TOOL))
        self.assertLess(order.index(END_TOOL), order.index(MOVE_TOOL))


class BeginEditSessionTests(unittest.TestCase):
    def test_begin_forwards_exact_opt_in_request(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        payload = _call(
            server,
            BEGIN_TOOL,
            asset_path=ASSET,
            graph_guid=GRAPH,
            experimental=True,
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], BEGIN_TOOL)
        self.assertFalse(payload["saved"])
        self.assertFalse(payload["compiled"])
        self.assertFalse(payload["exclusiveUI"])
        self.assertFalse(payload["humanEditingBlocked"])
        self.assertEqual(payload["childUnrealProcessCount"], 0)
        self.assertEqual(
            live.calls,
            [
                (
                    BEGIN_METHOD,
                    {
                        "assetPath": ASSET,
                        "graphGuid": GRAPH,
                        "experimentalContinuous": True,
                    },
                )
            ],
        )
        self.assertFalse(payload["result"]["exclusiveUI"])
        self.assertFalse(payload["result"]["rollbackSupported"])

    def test_begin_requires_explicit_experimental_opt_in(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for value in (False, 1, "true", None):
            payload = _call(
                server,
                BEGIN_TOOL,
                asset_path=ASSET,
                graph_guid=GRAPH,
                experimental=value,
            )
            self.assertFalse(payload["ok"], value)
            expected = (
                "blueprint-edit-session-experimental-opt-in-required"
                if value is False
                else "blueprint-edit-session-invalid-parameters"
            )
            self.assertEqual(payload["error"]["code"], expected, value)
        self.assertEqual(live.calls, [])

    def test_begin_validates_identity_before_bridge(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for asset_path in (
            "",
            "/Engine/BasicShapes/Cube.Cube",
            "/Game/Folder/BP_Thing",
            "/Game/BP_Thing.BP_Thing.SubObject",
        ):
            payload = _call(
                server,
                BEGIN_TOOL,
                asset_path=asset_path,
                graph_guid=GRAPH,
                experimental=True,
            )
            self.assertFalse(payload["ok"], asset_path)
            self.assertEqual(
                payload["error"]["code"],
                "blueprint-edit-session-invalid-parameters",
            )
        for graph_guid in ("not-a-guid", GRAPH.upper(), GRAPH.replace("-", "")):
            payload = _call(
                server,
                BEGIN_TOOL,
                asset_path=ASSET,
                graph_guid=graph_guid,
                experimental=True,
            )
            self.assertFalse(payload["ok"], graph_guid)
            self.assertEqual(
                payload["error"]["code"],
                "blueprint-edit-session-invalid-parameters",
            )
        self.assertEqual(live.calls, [])

    def test_begin_requires_commit_and_live_editor(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(commit_enabled=False, live_editor_service=live)
        payload = _call(
            server,
            BEGIN_TOOL,
            asset_path=ASSET,
            graph_guid=GRAPH,
            experimental=True,
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "blueprint-edit-session-disabled")
        self.assertEqual(live.calls, [])

        server, _ = _register(live_editor_service=None)
        payload = _call(
            server,
            BEGIN_TOOL,
            asset_path=ASSET,
            graph_guid=GRAPH,
            experimental=True,
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-editor-required")

    def test_begin_bridge_error_is_propagated(self) -> None:
        live = _FakeLiveEditorService(
            error=LiveEditorError(
                "blueprint-edit-session-package-dirty",
                "The exact Blueprint package is already dirty.",
            )
        )
        server, _ = _register(live_editor_service=live)
        payload = _call(
            server,
            BEGIN_TOOL,
            asset_path=ASSET,
            graph_guid=GRAPH,
            experimental=True,
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"], "blueprint-edit-session-package-dirty"
        )
        self.assertNotIn("result", payload)
        self.assertNotIn("mode", payload)
        self.assertEqual(len(live.calls), 1)


class EndEditSessionTests(unittest.TestCase):
    def test_end_keep_forwards_token_and_default_mode(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        payload = _call(server, END_TOOL, edit_session_token=TOKEN)
        self.assertTrue(payload["ok"])
        self.assertEqual(
            live.calls,
            [(END_METHOD, {"editSessionToken": TOKEN, "mode": "keep"})],
        )
        self.assertFalse(payload["saved"])
        self.assertFalse(payload["compiled"])
        self.assertFalse(payload["result"]["rolledBack"])

    def test_end_rejects_invalid_token_and_mode_without_bridge(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for token in ("", "not-a-token", TOKEN.upper(), TOKEN[:-1], TOKEN + "0"):
            payload = _call(server, END_TOOL, edit_session_token=token)
            self.assertFalse(payload["ok"], token)
            self.assertEqual(
                payload["error"]["code"], "blueprint-edit-session-token-invalid"
            )
        for mode in ("Keep", "rollback-now", ""):
            payload = _call(server, END_TOOL, edit_session_token=TOKEN, mode=mode)
            self.assertFalse(payload["ok"], mode)
            self.assertEqual(
                payload["error"]["code"],
                "blueprint-edit-session-invalid-parameters",
            )
        self.assertEqual(live.calls, [])

    def test_rollback_mode_is_forwarded_and_unsupported_error_propagates(self) -> None:
        live = _FakeLiveEditorService(
            error=LiveEditorError(
                "blueprint-edit-session-rollback-unsupported",
                "Session rollback is not implemented in this slice.",
            )
        )
        server, _ = _register(live_editor_service=live)
        payload = _call(
            server, END_TOOL, edit_session_token=TOKEN, mode="rollback"
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "blueprint-edit-session-rollback-unsupported",
        )
        self.assertNotIn("result", payload)
        self.assertEqual(len(live.calls), 1)
        self.assertEqual(live.calls[0][1]["mode"], "rollback")

    def test_end_keep_requires_commit_and_live_editor(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(commit_enabled=False, live_editor_service=live)
        payload = _call(server, END_TOOL, edit_session_token=TOKEN)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "blueprint-edit-session-disabled")
        server, _ = _register(live_editor_service=None)
        payload = _call(server, END_TOOL, edit_session_token=TOKEN)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "live-editor-required")
        self.assertEqual(live.calls, [])


class SessionBatchMoveTests(unittest.TestCase):
    def test_token_is_forwarded_only_when_supplied(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        first = _call(
            server,
            MOVE_TOOL,
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_entry()],
        )
        self.assertTrue(first["ok"])
        self.assertNotIn("editSessionToken", live.calls[0][1])
        second = _call(
            server,
            MOVE_TOOL,
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_entry()],
            edit_session_token=TOKEN,
        )
        self.assertTrue(second["ok"])
        self.assertEqual(live.calls[1][0], MOVE_METHOD)
        self.assertEqual(live.calls[1][1]["editSessionToken"], TOKEN)
        self.assertEqual(live.calls[1][1]["nodes"][0]["nodeGuid"], NODE_A)

    def test_invalid_token_is_rejected_before_bridge(self) -> None:
        live = _FakeLiveEditorService()
        server, _ = _register(live_editor_service=live)
        for token in ("bad", TOKEN.upper(), TOKEN[:-1], TOKEN + "0"):
            payload = _call(
                server,
                MOVE_TOOL,
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_entry()],
                edit_session_token=token,
            )
            self.assertFalse(payload["ok"], token)
            self.assertEqual(
                payload["error"]["code"], "blueprint-edit-session-token-invalid"
            )
        self.assertEqual(live.calls, [])

    def test_dirty_repeat_bridge_error_is_propagated(self) -> None:
        live = _FakeLiveEditorService(
            error=LiveEditorError(
                "blueprint-edit-session-fingerprint-conflict",
                "The live graph content fingerprint no longer matches.",
            )
        )
        server, _ = _register(live_editor_service=live)
        payload = _call(
            server,
            MOVE_TOOL,
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_entry()],
            edit_session_token=TOKEN,
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(
            payload["error"]["code"],
            "blueprint-edit-session-fingerprint-conflict",
        )
        self.assertNotIn("result", payload)
        self.assertEqual(len(live.calls), 1)


class EditSessionSourceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_root = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
        )

    def test_wrapper_never_spawns_or_invokes_unreal(self) -> None:
        source = (
            SRC_ROOT / "ue_agent_kit" / "mcp_workflow_tools.py"
        ).read_text(encoding="utf-8")
        for forbidden in (
            "subprocess",
            "UnrealEditor",
            "Popen",
            "os.system",
            "UnrealEditor-Cmd",
            "-run=",
        ):
            self.assertNotIn(forbidden, source)

    def test_cpp_session_manager_is_bridge_owned_and_fingerprint_bound(self) -> None:
        header = (self.private_root / "BlueprintEditSession.h").read_text(
            encoding="utf-8"
        )
        cpp = (self.private_root / "BlueprintEditSession.cpp").read_text(
            encoding="utf-8"
        )
        bridge = (self.private_root / "EditorBridge.cpp").read_text(encoding="utf-8")
        bridge_header = (self.private_root / "EditorBridge.h").read_text(
            encoding="utf-8"
        )
        batch_header = (self.private_root / "BlueprintEditBatch.h").read_text(
            encoding="utf-8"
        )
        batch_cpp = (self.private_root / "BlueprintEditBatch.cpp").read_text(
            encoding="utf-8"
        )
        for required in (
            "FBlueprintEditSessionManager",
            "IsCanonicalEditSessionToken",
            "EditSessionTokenLength = 64",
            "EEditSessionEndMode",
            "rollback-unsupported",
        ):
            self.assertIn(required, header + cpp)
        self.assertIn(
            "TUniquePtr<UEAgentKitBlueprintEdit::FBlueprintEditSessionManager>",
            bridge_header,
        )
        self.assertIn("BlueprintEditSessionManager.Reset();", bridge)
        self.assertIn('TEXT("editor.beginBlueprintEditSession")', bridge)
        self.assertIn('TEXT("editor.endBlueprintEditSession")', bridge)
        self.assertIn("ComputeGraphContentFingerprint", cpp)
        # Rollback must not silently exist: the manager contains no transaction
        # write path in this slice, and the per-batch helper owns the only one.
        self.assertNotIn("FScopedTransaction", cpp)
        self.assertIn("FScopedTransaction", batch_cpp)
        self.assertIn("AllowSessionOwnedDirty", batch_header)
        self.assertIn("EPackageDirtyGate", batch_cpp)
        # The no-token request must not be able to select the dirty gate.
        node_batch = (self.private_root / "EditorBridgeNodeMoveBatch.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("EPackageDirtyGate::RequireClean", node_batch)
        self.assertIn("EPackageDirtyGate::AllowSessionOwnedDirty", node_batch)
        self.assertIn("blueprint-edit-session-active-token-required", node_batch)
        # No UI lock claim in either handler or docs.
        session_handler = (
            self.private_root / "EditorBridgeBlueprintEditSession.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn('TEXT("exclusiveUI"), false', session_handler)
        self.assertIn('TEXT("humanEditingBlocked"), false', session_handler)
        self.assertNotIn("RegisterInputPreProcessor", session_handler)
        self.assertNotIn("RegisterInputPreProcessor", cpp)


if __name__ == "__main__":
    unittest.main()

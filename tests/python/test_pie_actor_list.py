"""PIE runtime inspector P1: the bounded ue_list_pie_actors surface.

Covers the Python half of P1: registry/registration, parameter normalization
(exactly one world identity, bounded offset/limit, optional filters kept
verbatim) and a pass-through call against a fake bridge service.
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

from ue_agent_kit.editor_bridge import LiveEditorBridgeService, LiveEditorError  # noqa: E402
from ue_agent_kit.mcp_live_tools import register_live_read_tools  # noqa: E402
from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME, tool_names_for_mode  # noqa: E402

TOOL = "ue_list_pie_actors"
CONTEXT_ID = "pie-0-listenServer"


class _Annotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _Server:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "error"), "message": str(exc)},
    }


class _FakeService:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload or {
            "readOnly": True,
            "worldKey": CONTEXT_ID,
            "totalCount": 1,
            "offset": 0,
            "limit": 50,
            "truncated": False,
            "actors": [{"path": "/Game/X.X:PersistentLevel.A1", "name": "A1"}],
        }
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, dict(params or {})))
        return dict(self.payload)


class PieActorListRegistryTests(unittest.TestCase):
    def test_registry_entry_is_live_read_only(self) -> None:
        self.assertIn(TOOL, TOOL_DEFINITIONS_BY_NAME)
        definition = TOOL_DEFINITIONS_BY_NAME[TOOL]
        self.assertEqual(definition.group, "live-read")
        self.assertTrue(definition.read_only)
        self.assertFalse(definition.destructive)
        self.assertEqual(definition.live_method, "editor.listPieActors")
        self.assertIn(TOOL, tool_names_for_mode(live_editor_enabled=True))
        self.assertNotIn(TOOL, tool_names_for_mode())
        self.assertNotIn(TOOL, tool_names_for_mode(workflow_enabled=True))


class PieActorListParameterTests(unittest.TestCase):
    @staticmethod
    def _normalize(params: dict[str, Any]) -> dict[str, Any]:
        return LiveEditorBridgeService._normalize_tool_params(TOOL, params)

    def test_world_context_identity_and_defaults(self) -> None:
        normalized = self._normalize({"worldContextId": CONTEXT_ID})
        self.assertEqual(normalized["worldContextId"], CONTEXT_ID)
        self.assertEqual(normalized["offset"], 0)
        self.assertEqual(normalized["limit"], 50)
        self.assertNotIn("pieInstanceId", normalized)

    def test_instance_identity_and_filters_pass_through(self) -> None:
        normalized = self._normalize(
            {
                "pieInstanceId": 1,
                "offset": 20,
                "limit": 200,
                "nameContains": "BP_",
                "classContains": "StaticMeshActor",
                "tagContains": "agent",
                "worldPath": "/Game/X.X:PersistentLevel",
            }
        )
        self.assertEqual(normalized["pieInstanceId"], 1)
        self.assertEqual(normalized["offset"], 20)
        self.assertEqual(normalized["limit"], 200)
        self.assertEqual(normalized["nameContains"], "BP_")
        self.assertEqual(normalized["classContains"], "StaticMeshActor")
        self.assertEqual(normalized["tagContains"], "agent")
        self.assertIn("worldPath", normalized)

    def test_exactly_one_world_identity_is_required(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize({})
        self.assertEqual(raised.exception.code, "live-editor-invalid-parameters")
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize({"worldContextId": CONTEXT_ID, "pieInstanceId": 0})
        self.assertEqual(raised.exception.code, "live-editor-invalid-parameters")

    def test_offset_and_limit_are_bounded(self) -> None:
        for params in ({"worldContextId": CONTEXT_ID, "offset": -1},
                       {"worldContextId": CONTEXT_ID, "limit": 0},
                       {"worldContextId": CONTEXT_ID, "limit": 201}):
            with self.subTest(params=params):
                with self.assertRaises(LiveEditorError) as raised:
                    self._normalize(params)
                self.assertEqual(raised.exception.code, "live-editor-invalid-parameters")

    def test_unknown_parameter_is_rejected(self) -> None:
        with self.assertRaises(LiveEditorError) as raised:
            self._normalize({"worldContextId": CONTEXT_ID, "maxActors": 10})
        self.assertEqual(raised.exception.code, "live-editor-invalid-parameters")


class PieActorListToolTests(unittest.TestCase):
    def _tool(self, service: _FakeService):
        server = _Server()
        register_live_read_tools(
            server=server,
            live_editor_service=service,
            index_service=None,
            audit_report_root=None,
            read_annotations=_Annotations(readOnlyHint=True),
            tool_annotations_type=_Annotations,
            error_response=_error_response,
        )
        return server.tools[TOOL]

    def test_tool_forwards_the_exact_request(self) -> None:
        service = _FakeService()
        out = self._tool(service)(
            world_context_id=CONTEXT_ID,
            offset=10,
            limit=25,
            name_contains="BP_",
            class_contains="Actor",
            tag_contains="agent",
        )
        self.assertEqual(out["worldKey"], CONTEXT_ID)
        self.assertEqual(len(service.calls), 1)
        tool_name, params = service.calls[0]
        self.assertEqual(tool_name, TOOL)
        self.assertEqual(params["worldContextId"], CONTEXT_ID)
        self.assertEqual(params["offset"], 10)
        self.assertEqual(params["limit"], 25)
        self.assertEqual(params["nameContains"], "BP_")
        self.assertEqual(params["classContains"], "Actor")
        self.assertEqual(params["tagContains"], "agent")
        self.assertNotIn("pieInstanceId", params)

    def test_tool_uses_the_instance_identity_when_no_context_id(self) -> None:
        service = _FakeService()
        self._tool(service)(pie_instance_id=3)
        _tool_name, params = service.calls[0]
        self.assertEqual(params["pieInstanceId"], 3)
        self.assertNotIn("worldContextId", params)

    def test_tool_returns_bridge_errors_unchanged(self) -> None:
        class _Failing(_FakeService):
            def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                raise LiveEditorError("pie-not-running", "PIE is not running.")

        out = self._tool(_Failing())(pie_instance_id=0)
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "pie-not-running")
        self.assertTrue(out["readOnly"])


if __name__ == "__main__":
    unittest.main()

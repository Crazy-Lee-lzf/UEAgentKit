from __future__ import annotations

import unittest
from typing import Any

from ue_agent_kit.mcp_focus_blueprint_graph_experimental import (
    METHOD,
    TOOL_NAME,
    register_focus_blueprint_graph_tool,
)


class _FakeServer:
    def __init__(self) -> None:
        self.tool_name = ""
        self.function = None
        self.annotations: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tool_name = function.__name__
            self.function = function
            self.annotations = {
                "readOnlyHint": getattr(annotations, "readOnlyHint", None),
                "destructiveHint": getattr(annotations, "destructiveHint", None),
                "idempotentHint": getattr(annotations, "idempotentHint", None),
            }
            return function

        return decorator


class _FakeAnnotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], float]] = []

    def call_method(self, method: str, params: dict[str, Any], *, timeout_seconds: float) -> dict[str, Any]:
        self.calls.append((method, params, timeout_seconds))
        return {
            "action": "focus-blueprint-graph",
            "assetPath": params["assetPath"],
            "graphGuid": params.get("graphGuid", "default-graph"),
            "focused": True,
            "readOnly": True,
        }


def _error_response(tool: str, exc: Exception, *, read_only: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "tool": tool,
        "readOnly": read_only,
        "error": {"code": "invalid-arguments", "message": str(exc)},
    }


def _register() -> tuple[_FakeServer, _FakeService]:
    server = _FakeServer()
    service = _FakeService()
    register_focus_blueprint_graph_tool(
        server=server,
        live_editor_service=service,
        tool_annotations_type=_FakeAnnotations,
        error_response=_error_response,
    )
    return server, service


class FocusBlueprintGraphToolTests(unittest.TestCase):
    def test_registers_one_opt_in_tool(self) -> None:
        server, _ = _register()
        self.assertEqual(server.tool_name, TOOL_NAME)
        self.assertIsNotNone(server.function)
        self.assertFalse(server.annotations["readOnlyHint"])
        self.assertFalse(server.annotations["destructiveHint"])

    def test_forwards_exact_asset_and_graph_guid(self) -> None:
        server, service = _register()
        payload = server.function(
            asset_path="/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E",
            graph_guid="05BA59CA-47DC-2491-1F17-4DBA2822185F",
        )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["tool"], TOOL_NAME)
        self.assertEqual(payload["result"]["action"], "focus-blueprint-graph")
        self.assertEqual(
            service.calls,
            [
                (
                    METHOD,
                    {
                        "assetPath": "/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E",
                        "graphGuid": "05ba59ca-47dc-2491-1f17-4dba2822185f",
                    },
                    30.0,
                )
            ],
        )

    def test_omits_graph_guid_for_default_graph(self) -> None:
        server, service = _register()
        payload = server.function(asset_path="/Game/Folder/BP_Thing.BP_Thing")
        self.assertTrue(payload["ok"])
        self.assertEqual(service.calls[0][1], {"assetPath": "/Game/Folder/BP_Thing.BP_Thing"})

    def test_rejects_non_game_or_ambiguous_paths_without_calling_editor(self) -> None:
        server, service = _register()
        for path in (
            "/Engine/BasicShapes/Cube.Cube",
            "/Game/Folder/BP_Thing",
            "/Game/Folder/../BP_Thing.BP_Thing",
            "/Game/Folder/BP Thing.BP Thing",
            "",
        ):
            payload = server.function(asset_path=path)
            self.assertFalse(payload["ok"], path)
            self.assertEqual(payload["error"]["code"], "invalid-arguments")
        self.assertEqual(service.calls, [])

    def test_rejects_non_canonical_graph_guid_without_calling_editor(self) -> None:
        server, service = _register()
        for graph_guid in ("not-a-guid", "05BA59CA47DC24911F174DBA2822185F", "05ba59ca-47dc-2491-1f17"):
            payload = server.function(
                asset_path="/Game/Folder/BP_Thing.BP_Thing",
                graph_guid=graph_guid,
            )
            self.assertFalse(payload["ok"], graph_guid)
            self.assertEqual(payload["error"]["code"], "invalid-arguments")
        self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main()

"""P1: ``ue_pie`` must forward byte-identical payloads to the per-verb PIE tools.

The consolidated tool is a façade, not a re-implementation, so the only thing
worth testing is parity: for every action the bridge must receive exactly the
payload the replaced tool would have sent.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.mcp_consolidated_tools import register_consolidated_tools  # noqa: E402
from ue_agent_kit.mcp_live_tools import register_live_read_tools  # noqa: E402


class _RecordingServer:
    """Minimal stand-in for FastMCP that keeps the registered callables."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            self.tools[func.__name__] = func
            return func

        return decorator


class _RecordingService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool, dict(params or {})))
        return {"tool": tool, "ok": True, "params": dict(params or {})}


class _Annotations:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


def _error_response(tool: str, exc: Exception, read_only: bool = False) -> dict[str, Any]:
    return {"tool": tool, "ok": False, "error": str(exc), "readOnly": read_only}


def _original_surface() -> tuple[_RecordingServer, _RecordingService]:
    server = _RecordingServer()
    service = _RecordingService()
    register_live_read_tools(
        server=server,
        live_editor_service=service,
        index_service=None,
        audit_report_root=None,
        read_annotations=_Annotations(),
        tool_annotations_type=_Annotations,
        error_response=_error_response,
    )
    return server, service


def _consolidated_surface() -> tuple[_RecordingServer, _RecordingService]:
    server = _RecordingServer()
    service = _RecordingService()
    register_consolidated_tools(
        server=server,
        live_editor_service=service,
        read_annotations=_Annotations(),
        error_response=_error_response,
    )
    return server, service


# action -> (replaced tool name, original kwargs, consolidated kwargs)
PARITY_CASES: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {
    "state": ("ue_get_pie_state", {}, {"action": "state"}),
    "worlds": (
        "ue_get_pie_world_contexts",
        {"max_contexts": 4},
        {"action": "worlds", "max_contexts": 4},
    ),
    "list_actors": (
        "ue_list_pie_actors",
        {
            "pie_instance_id": 2,
            "offset": 10,
            "limit": 5,
            "name_contains": "BP_",
            "class_contains": "Pawn",
            "tag_contains": "player",
            "world_path": "/Game/Maps/Lyra",
        },
        {
            "action": "list_actors",
            "pie_instance_id": 2,
            "offset": 10,
            "limit": 5,
            "name_contains": "BP_",
            "class_contains": "Pawn",
            "tag_contains": "player",
            "world_path": "/Game/Maps/Lyra",
        },
    ),
    "inspect_actor": (
        "ue_inspect_pie_actor",
        {
            "pie_instance_id": 2,
            "actor_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.SM_Chair_1",
            "actor_label": "Chair",
            "actor_class_path": "/Script/Engine.StaticMeshActor",
            "world_path": "/Game/Maps/Lyra",
            "max_components": 3,
            "max_assets_per_component": 2,
        },
        {
            "action": "inspect_actor",
            "pie_instance_id": 2,
            "actor_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.SM_Chair_1",
            "actor_label": "Chair",
            "actor_class_path": "/Script/Engine.StaticMeshActor",
            "world_path": "/Game/Maps/Lyra",
            "max_components": 3,
            "max_assets_per_component": 2,
        },
    ),
    "scalars": (
        "ue_get_pie_scalars",
        {
            "pie_instance_id": 2,
            "property_names": ["Health", "MaxHealth"],
            "actor_guid": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
            "component_name": "HealthComponent",
            "component_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.Player_1.HealthComponent",
            "component_class_path": "/Script/LyraGame.HealthComponent",
            "world_path": "/Game/Maps/Lyra",
            "max_string_chars": 64,
        },
        {
            "action": "scalars",
            "pie_instance_id": 2,
            "property_names": ["Health", "MaxHealth"],
            "actor_guid": "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0",
            "component_name": "HealthComponent",
            "component_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.Player_1.HealthComponent",
            "component_class_path": "/Script/LyraGame.HealthComponent",
            "world_path": "/Game/Maps/Lyra",
            "max_string_chars": 64,
        },
    ),
    "list_properties": (
        "ue_list_pie_object_properties",
        {
            "pie_instance_id": 2,
            "actor_name": "Player_1",
            "object_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.Player_1",
            "name_contains": "Health",
            "max_properties": 16,
        },
        {
            "action": "list_properties",
            "pie_instance_id": 2,
            "actor_name": "Player_1",
            "object_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.Player_1",
            "name_contains": "Health",
            "max_properties": 16,
        },
    ),
    "read_properties": (
        "ue_read_pie_object_properties",
        {
            "pie_instance_id": 2,
            "property_names": ["Health"],
            "object_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.Player_1",
            "offset": 4,
            "limit": 8,
            "max_string_chars": 32,
        },
        {
            "action": "read_properties",
            "pie_instance_id": 2,
            "property_names": ["Health"],
            "object_path": "/Game/Maps/Lyra.Lyra:PersistentLevel.Player_1",
            "offset": 4,
            "limit": 8,
            "max_string_chars": 32,
        },
    ),
}


class ConsolidatedPieParityTests(unittest.TestCase):
    def test_every_action_forwards_the_original_payload(self) -> None:
        original_server, original_service = _original_surface()
        consolidated_server, consolidated_service = _consolidated_surface()
        for action, (tool_name, original_kwargs, consolidated_kwargs) in PARITY_CASES.items():
            with self.subTest(action=action):
                self.assertIn(tool_name, original_server.tools)
                original_server.tools[tool_name](**original_kwargs)
                result = consolidated_server.tools["ue_pie"](**consolidated_kwargs)
                self.assertEqual(original_service.calls[-1], consolidated_service.calls[-1])
                self.assertTrue(result["ok"])

    def test_world_context_id_wins_over_pie_instance_id(self) -> None:
        server, service = _consolidated_surface()
        server.tools["ue_pie"](
            action="list_actors",
            world_context_id="pie-2-server",
            pie_instance_id=9,
            limit=5,
        )
        self.assertEqual(service.calls[-1][0], "ue_list_pie_actors")
        payload = service.calls[-1][1]
        self.assertEqual(payload["worldContextId"], "pie-2-server")
        self.assertNotIn("pieInstanceId", payload)

    def test_state_sends_no_parameters(self) -> None:
        server, service = _consolidated_surface()
        server.tools["ue_pie"](action="state")
        self.assertEqual(service.calls[-1], ("ue_get_pie_state", {}))

    def test_aliases_resolve_to_the_canonical_action(self) -> None:
        server, service = _consolidated_surface()
        server.tools["ue_pie"](action="world_contexts", max_contexts=2)
        self.assertEqual(service.calls[-1], ("ue_get_pie_world_contexts", {"maxContexts": 2}))
        server.tools["ue_pie"](action="actors", limit=1)
        self.assertEqual(service.calls[-1][0], "ue_list_pie_actors")

    def test_action_result_carries_the_resolved_action(self) -> None:
        server, _service = _consolidated_surface()
        result = server.tools["ue_pie"](action="world_contexts", max_contexts=2)
        self.assertEqual(result["action"], "worlds")

    def test_unknown_action_is_reported_not_raised(self) -> None:
        server, service = _consolidated_surface()
        result = server.tools["ue_pie"](action="teleport")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(service.calls, [])

    def test_property_actions_require_property_names(self) -> None:
        server, service = _consolidated_surface()
        for action in ("scalars", "read_properties"):
            with self.subTest(action=action):
                result = server.tools["ue_pie"](action=action, actor_path="A")
                self.assertFalse(result["ok"])
                self.assertIn("property_names", result["error"])
        self.assertEqual(service.calls, [])

    def test_bridge_call_is_forwarded_once(self) -> None:
        server, service = _consolidated_surface()
        server.tools["ue_pie"](action="inspect_actor", pie_instance_id=1, actor_name="A")
        self.assertEqual(len(service.calls), 1)


if __name__ == "__main__":
    unittest.main()

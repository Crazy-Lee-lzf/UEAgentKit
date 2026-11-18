"""P2: the Change Set, patch and edit-session facades must dispatch, not re-implement."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.mcp_consolidated_tools import (  # noqa: E402
    BLUEPRINT_SESSION_ACTIONS,
    CHANGE_SET_ACTIONS,
    PATCH_ACTIONS,
    register_blueprint_session_tool,
    register_change_set_tool,
    register_patch_tool,
)


class _RecordingServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            self.tools[func.__name__] = func
            return func

        return decorator


def _error_response(tool: str, exc: Exception, read_only: bool = False) -> dict[str, Any]:
    return {"tool": tool, "ok": False, "error": str(exc), "readOnly": read_only}


def _surface(register: Any, actions: dict[str, Any], **annotation: Any):
    server = _RecordingServer()
    calls: list[tuple[str, dict[str, Any]]] = []
    handlers: dict[str, Any] = {}
    for entry in actions.values():
        handler = entry[0] if isinstance(entry, tuple) else entry
        def stub(_name: str = handler, **kwargs: Any) -> dict[str, Any]:
            calls.append((_name, kwargs))
            return {"ok": True, "tool": _name}

        handlers[handler] = stub
    register(server=server, handlers=handlers, error_response=_error_response, **annotation)
    return server, calls


class ChangeSetDispatchTests(unittest.TestCase):
    def test_every_action_reaches_its_handler(self) -> None:
        server, calls = _surface(register_change_set_tool, CHANGE_SET_ACTIONS, planning_annotations=None)
        cases = {
            "create": {"title": "T", "task_id": "task-1"},
            "get": {"change_set_id": "cs-1"},
            "save_checkpoint": {"batch_execution_id": "exec-1", "mode": "Preview"},
            "verify_checkpoint": {"checkpoint_set_id": "cps-1"},
        }
        self.assertEqual(set(cases), set(CHANGE_SET_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                result = server.tools["ue_change_set"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(calls[-1], (CHANGE_SET_ACTIONS[action][0], kwargs))

    def test_unknown_action_reports_without_dispatch(self) -> None:
        server, calls = _surface(register_change_set_tool, CHANGE_SET_ACTIONS, planning_annotations=None)
        result = server.tools["ue_change_set"](action="delete")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(calls, [])


class PatchDispatchTests(unittest.TestCase):
    def test_every_action_reaches_its_handler(self) -> None:
        server, calls = _surface(register_patch_tool, PATCH_ACTIONS, annotations=None)
        cases = {
            "plan": {
                "asset_path": "/Game/A",
                "operation": "setDescription",
                "target": {},
                "value": "x",
            },
            "dry_run": {"plan_id": "plan-1"},
            "apply": {
                "plan_id": "plan-1",
                "dry_run_receipt": "dry-1",
                "confirmation": "COMMIT plan-1",
            },
            "verify": {"apply_receipt": "apply-1"},
            "rollback": {"apply_receipt": "apply-1", "mode": "DryRun"},
            "state": {"asset_path": "/Game/A"},
            "refresh_index": {"asset_path": "/Game/A", "mode": "Preview"},
        }
        self.assertEqual(set(cases), set(PATCH_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                result = server.tools["ue_patch"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(calls[-1], (PATCH_ACTIONS[action][0], kwargs))

    def test_required_arguments_are_checked_before_dispatch(self) -> None:
        server, calls = _surface(register_patch_tool, PATCH_ACTIONS, annotations=None)
        for action, extra, fragment in (
            ("plan", {"asset_path": "/Game/A"}, "operation"),
            ("dry_run", {}, "plan_id"),
            ("apply", {"plan_id": "p"}, "dry_run_receipt"),
            ("verify", {}, "apply_receipt"),
        ):
            with self.subTest(action=action):
                result = server.tools["ue_patch"](action=action, **extra)
                self.assertFalse(result["ok"])
                self.assertIn(fragment, result["error"])
        self.assertEqual(calls, [])

    def test_unknown_action_reports_without_dispatch(self) -> None:
        server, calls = _surface(register_patch_tool, PATCH_ACTIONS, annotations=None)
        result = server.tools["ue_patch"](action="commit_everything")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(calls, [])


class BlueprintSessionDispatchTests(unittest.TestCase):
    def test_every_action_reaches_its_handler(self) -> None:
        server, calls = _surface(register_blueprint_session_tool, BLUEPRINT_SESSION_ACTIONS, annotations=None)
        cases = {
            "begin": {"asset_path": "/Game/BP", "graph_guid": "guid-1", "experimental": True},
            "end": {"edit_session_token": "token-1", "mode": "rollback"},
        }
        self.assertEqual(set(cases), set(BLUEPRINT_SESSION_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                result = server.tools["ue_blueprint_edit_session"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(calls[-1], (BLUEPRINT_SESSION_ACTIONS[action][0], kwargs))

    def test_required_arguments_are_checked(self) -> None:
        server, calls = _surface(register_blueprint_session_tool, BLUEPRINT_SESSION_ACTIONS, annotations=None)
        result = server.tools["ue_blueprint_edit_session"](action="end")
        self.assertFalse(result["ok"])
        self.assertIn("edit_session_token", result["error"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

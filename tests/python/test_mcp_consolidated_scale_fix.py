"""P2: ``ue_animation_scale_fix`` must dispatch, not re-implement.

The nine per-verb scale-fix tools stay the implementation; the facade only
routes an action to the right callable with the arguments the caller supplied.
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

from ue_agent_kit.mcp_consolidated_tools import (  # noqa: E402
    SCALE_FIX_ACTIONS,
    register_animation_scale_fix_tool,
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


def _surface() -> tuple[_RecordingServer, list[tuple[str, dict[str, Any]]]]:
    server = _RecordingServer()
    calls: list[tuple[str, dict[str, Any]]] = []
    handlers: dict[str, Any] = {}
    for handler, _params in SCALE_FIX_ACTIONS.values():
        def stub(_name: str = handler, **kwargs: Any) -> dict[str, Any]:
            calls.append((_name, kwargs))
            return {"ok": True, "tool": _name}

        handlers[handler] = stub
    register_animation_scale_fix_tool(
        server=server,
        handlers=handlers,
        annotations=None,
        error_response=_error_response,
    )
    return server, calls


class ScaleFixDispatchTests(unittest.TestCase):
    def test_every_action_reaches_its_handler(self) -> None:
        server, calls = _surface()
        cases: dict[str, dict[str, Any]] = {
            "plan": {
                "asset_path": "/Game/A",
                "root_bone": "root",
                "expected_final_scale": 100.0,
                "uniform_scale": 0.5,
            },
            "plan_batch": {
                "audit_task_id": "audit-1",
                "audit_report_id": "report-1",
                "asset_paths": ["/Game/A", "/Game/B"],
                "expected_final_scale_overrides": {"/Game/A": 1.0},
            },
            "get": {"batch_plan_id": "batch-1"},
            "apply": {"batch_plan_id": "batch-1", "confirmation": "APPLY", "max_assets": 1},
            "save": {"batch_plan_id": "batch-1", "confirmation": "SAVE"},
            "verify": {"batch_plan_id": "batch-1", "max_assets": 2},
            "refresh_index": {"batch_plan_id": "batch-1", "mode": "Apply"},
            "rollback": {"batch_plan_id": "batch-1", "mode": "DryRun"},
            "undo": {"batch_plan_id": "batch-1", "confirmation": "UNDO"},
        }
        self.assertEqual(set(cases), set(SCALE_FIX_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                before = len(calls)
                result = server.tools["ue_animation_scale_fix"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(len(calls), before + 1)
                name, forwarded = calls[-1]
                self.assertEqual(name, SCALE_FIX_ACTIONS[action][0])
                self.assertEqual(forwarded, kwargs)

    def test_omitted_arguments_keep_the_handler_default(self) -> None:
        server, calls = _surface()
        server.tools["ue_animation_scale_fix"](
            action="plan", asset_path="/Game/A", root_bone="root", expected_final_scale=1.0
        )
        self.assertEqual(
            calls[-1],
            (
                "ue_plan_animation_scale_fix",
                {"asset_path": "/Game/A", "root_bone": "root", "expected_final_scale": 1.0},
            ),
        )
        server.tools["ue_animation_scale_fix"](action="rollback", batch_plan_id="b")
        self.assertEqual(
            calls[-1], ("ue_rollback_animation_scale_fix_batch", {"batch_plan_id": "b"})
        )

    def test_required_arguments_are_checked_before_dispatch(self) -> None:
        server, calls = _surface()
        for action, fragment in (
            ("plan", "asset_path"),
            ("plan_batch", "audit_task_id"),
            ("apply", "batch_plan_id"),
            ("undo", "batch_plan_id"),
        ):
            with self.subTest(action=action):
                result = server.tools["ue_animation_scale_fix"](action=action)
                self.assertFalse(result["ok"])
                self.assertIn(fragment, result["error"])
        self.assertEqual(calls, [])

    def test_refresh_alias_resolves(self) -> None:
        server, calls = _surface()
        result = server.tools["ue_animation_scale_fix"](action="refresh", batch_plan_id="b")
        self.assertTrue(result["ok"])
        self.assertEqual(calls[-1][0], "ue_refresh_animation_scale_fix_batch_index")

    def test_unknown_action_reports_without_dispatch(self) -> None:
        server, calls = _surface()
        result = server.tools["ue_animation_scale_fix"](action="explode")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

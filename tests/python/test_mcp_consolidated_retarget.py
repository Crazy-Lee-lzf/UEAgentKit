"""P2: ``ue_animation_retarget`` must dispatch, not re-implement.

The fourteen retarget workflow tools stay the implementation; the facade routes
an action to the right callable with the arguments the caller supplied.
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
    RETARGET_ACTIONS,
    register_animation_retarget_tool,
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
    for handler, _params in RETARGET_ACTIONS.values():
        def stub(_name: str = handler, **kwargs: Any) -> dict[str, Any]:
            calls.append((_name, kwargs))
            return {"ok": True, "tool": _name}

        handlers[handler] = stub
    register_animation_retarget_tool(
        server=server,
        handlers=handlers,
        annotations=None,
        error_response=_error_response,
    )
    return server, calls


class RetargetDispatchTests(unittest.TestCase):
    def test_every_action_reaches_its_handler(self) -> None:
        server, calls = _surface()
        cases: dict[str, dict[str, Any]] = {
            "plan": {"sourceMesh": "/Game/A", "targetMesh": "/Game/B", "includeOptionalChains": False},
            "apply_setup": {"planId": "plan-1", "confirmation": "APPLY plan-1"},
            "start_batch": {
                "planId": "plan-1",
                "retargeter": "/Game/RT",
                "sourceAssets": ["/Game/A"],
                "overwriteExisting": True,
            },
            "get_batch": {"taskId": "task-1"},
            "cancel_batch": {"taskId": "task-1"},
            "save_batch": {"taskId": "task-1", "confirmation": "SAVE task-1"},
            "verify_batch": {"taskId": "task-1"},
            "rollback_batch": {"taskId": "task-1", "mode": "DryRun"},
            "start_postprocess": {"retargetTaskId": "task-1", "batchSize": 2},
            "get_postprocess": {"postprocessId": "pp-1"},
            "plan_postprocess": {"postprocessId": "pp-1", "description": "why"},
            "reopen_postprocess": {"planRelativePath": "plans/pp-1.json"},
            "refresh_postprocess_index": {"postprocessId": "pp-1", "mode": "Apply"},
            "validate": {"retargeter": "/Game/RT", "animationPaths": ["/Game/A"]},
        }
        self.assertEqual(set(cases), set(RETARGET_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                before = len(calls)
                result = server.tools["ue_animation_retarget"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(len(calls), before + 1)
                name, forwarded = calls[-1]
                self.assertEqual(name, RETARGET_ACTIONS[action][0])
                self.assertEqual(forwarded, kwargs)

    def test_omitted_arguments_keep_the_handler_default(self) -> None:
        server, calls = _surface()
        server.tools["ue_animation_retarget"](action="plan", sourceMesh="/Game/A", targetMesh="/Game/B")
        self.assertEqual(
            calls[-1],
            ("ue_plan_animation_retarget", {"sourceMesh": "/Game/A", "targetMesh": "/Game/B"}),
        )
        server.tools["ue_animation_retarget"](action="rollback_batch", taskId="task-1")
        self.assertEqual(
            calls[-1], ("ue_rollback_animation_retarget_batch", {"taskId": "task-1"})
        )

    def test_required_arguments_are_checked_before_dispatch(self) -> None:
        server, calls = _surface()
        for action, extra, fragment in (
            ("plan", {"sourceMesh": "/Game/A"}, "targetMesh"),
            ("start_batch", {"planId": "p"}, "retargeter"),
            ("save_batch", {"taskId": "t"}, "confirmation"),
            ("validate", {"retargeter": "r"}, "animationPaths"),
        ):
            with self.subTest(action=action):
                result = server.tools["ue_animation_retarget"](action=action, **extra)
                self.assertFalse(result["ok"])
                self.assertIn(fragment, result["error"])
        self.assertEqual(calls, [])

    def test_unknown_action_reports_without_dispatch(self) -> None:
        server, calls = _surface()
        result = server.tools["ue_animation_retarget"](action="retarget_everything")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

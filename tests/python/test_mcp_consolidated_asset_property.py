"""P2: ``ue_asset_property`` must dispatch, not re-implement.

The six per-verb asset-property tools stay the implementation; the facade only
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
    ASSET_PROPERTY_ACTIONS,
    register_asset_property_tool,
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
    for handler, _params in ASSET_PROPERTY_ACTIONS.values():
        def stub(_name: str = handler, **kwargs: Any) -> dict[str, Any]:
            calls.append((_name, kwargs))
            return {"ok": True, "tool": _name}

        handlers[handler] = stub
    register_asset_property_tool(
        server=server,
        handlers=handlers,
        annotations=None,
        error_response=_error_response,
    )
    return server, calls


class AssetPropertyDispatchTests(unittest.TestCase):
    def test_every_action_reaches_its_handler(self) -> None:
        server, calls = _surface()
        cases: dict[str, dict[str, Any]] = {
            "set": {
                "asset_path": "/Game/DA",
                "property_path": "BoolValue",
                "value": True,
                "mode": "DryRun",
                "description": "why",
            },
            "set_reference": {
                "asset_path": "/Game/DA",
                "property_path": "ObjectValue",
                "value": {"referenceType": "Object", "path": "/Game/A.A"},
                "mode": "Plan",
            },
            "set_structured": {
                "asset_path": "/Game/DA",
                "property_path": "ArrayValue",
                "value": {"valueType": "Array", "items": [1, 2]},
                "mode": "Plan",
            },
            "apply": {"plan_id": "plan-1", "confirmation": "APPLY", "change_set_id": "cs-1"},
            "undo": {
                "asset_path": "/Game/DA",
                "transaction_id": "tx-1",
                "editor_session_id": "session-1",
            },
            "discard": {
                "asset_path": "/Game/DA",
                "transaction_id": "tx-1",
                "editor_session_id": "session-1",
            },
        }
        self.assertEqual(set(cases), set(ASSET_PROPERTY_ACTIONS))
        for action, kwargs in cases.items():
            with self.subTest(action=action):
                before = len(calls)
                result = server.tools["ue_asset_property"](action=action, **kwargs)
                self.assertTrue(result["ok"])
                self.assertEqual(len(calls), before + 1)
                name, forwarded = calls[-1]
                self.assertEqual(name, ASSET_PROPERTY_ACTIONS[action][0])
                self.assertEqual(forwarded, kwargs)

    def test_omitted_arguments_keep_the_handler_default(self) -> None:
        server, calls = _surface()
        server.tools["ue_asset_property"](
            action="set", asset_path="/Game/DA", property_path="BoolValue", value=False
        )
        self.assertEqual(
            calls[-1],
            (
                "ue_set_asset_property",
                {
                    "asset_path": "/Game/DA",
                    "property_path": "BoolValue",
                    "value": False,
                    "mode": "Plan",
                },
            ),
        )

    def test_explicit_null_clears_a_reference(self) -> None:
        server, calls = _surface()
        server.tools["ue_asset_property"](
            action="set_reference", asset_path="/Game/DA", property_path="ObjectValue"
        )
        self.assertEqual(
            calls[-1],
            (
                "ue_set_asset_reference_property",
                {
                    "asset_path": "/Game/DA",
                    "property_path": "ObjectValue",
                    "value": None,
                    "mode": "Plan",
                },
            ),
        )

    def test_required_arguments_are_checked_before_dispatch(self) -> None:
        server, calls = _surface()
        for action, extra, fragment in (
            ("set", {"asset_path": "/Game/DA", "property_path": "BoolValue"}, "value"),
            ("set", {"asset_path": "/Game/DA", "value": True}, "property_path"),
            ("apply", {"confirmation": "APPLY"}, "plan_id"),
            ("undo", {"asset_path": "/Game/DA", "transaction_id": "tx"}, "editor_session_id"),
        ):
            with self.subTest(action=action, fragment=fragment):
                result = server.tools["ue_asset_property"](action=action, **extra)
                self.assertFalse(result["ok"])
                self.assertIn(fragment, result["error"])
        self.assertEqual(calls, [])

    def test_unknown_action_reports_without_dispatch(self) -> None:
        server, calls = _surface()
        result = server.tools["ue_asset_property"](action="explode")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

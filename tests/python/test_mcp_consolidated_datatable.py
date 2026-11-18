"""P1: ``ue_datatable`` must prepare the same change the five tools used to.

The consolidated tool is a façade over ``prepare_high_level_change``; the only
thing worth testing is that every action produces the historical operation,
target and value, plus friendly validation for incomplete calls.
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
    DATATABLE_ACTIONS,
    datatable_change,
    register_datatable_tool,
)


class _RecordingServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *args: Any, **kwargs: Any) -> Any:
        def decorator(func: Any) -> Any:
            self.tools[func.__name__] = func
            return func

        return decorator


class _RecordingWorkflow:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def prepare_high_level_change(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {
            "ok": True,
            "tool": kwargs["tool_name"],
            "mode": kwargs["mode"],
            "underlyingOperation": kwargs["operation"],
            "assetPath": kwargs["asset_path"],
        }


def _error_response(tool: str, exc: Exception, read_only: bool = False) -> dict[str, Any]:
    return {"tool": tool, "ok": False, "error": str(exc), "readOnly": read_only}


def _surface() -> tuple[_RecordingServer, _RecordingWorkflow]:
    server = _RecordingServer()
    workflow = _RecordingWorkflow()
    register_datatable_tool(
        server=server,
        workflow_service=workflow,
        planning_annotations=None,
        error_response=_error_response,
    )
    return server, workflow


class DatatableChangeBuilderTests(unittest.TestCase):
    def test_every_action_matches_the_historical_change(self) -> None:
        cases = {
            "set_cell": (
                {"row_name": "R", "field_name": "Value", "value": 7},
                {"operation": "setDataTableCell", "target": {"rowName": "R", "fieldName": "Value"}, "value": 7},
            ),
            "set_row_fields": (
                {"row_name": "R", "values": {"Value": 7}},
                {"operation": "setDataTableRowFields", "target": {"rowName": "R"}, "value": {"Value": 7}},
            ),
            "add_row": (
                {"row_name": "R", "values": {"Value": 7}},
                {"operation": "addDataTableRow", "target": {"rowName": "R"}, "value": {"Value": 7}},
            ),
            "remove_row": (
                {"row_name": "R"},
                {"operation": "removeDataTableRow", "target": {"rowName": "R"}, "value": True},
            ),
            "rename_row": (
                {"row_name": "R", "new_row_name": "S"},
                {"operation": "renameDataTableRow", "target": {"rowName": "R", "newRowName": "S"}, "value": True},
            ),
        }
        self.assertEqual(set(cases), set(DATATABLE_ACTIONS))
        for action, (kwargs, expected) in cases.items():
            with self.subTest(action=action):
                self.assertEqual(datatable_change(action, **kwargs), expected)

    def test_add_row_without_values_sends_an_empty_object(self) -> None:
        self.assertEqual(datatable_change("add_row", row_name="R")["value"], {})

    def test_incomplete_calls_are_rejected_early(self) -> None:
        for action, kwargs, fragment in (
            ("set_cell", {"row_name": "R", "value": 1}, "field_name"),
            ("set_cell", {"row_name": "R", "field_name": "Value"}, "value"),
            ("set_row_fields", {"row_name": "R"}, "values"),
            ("rename_row", {"row_name": "R"}, "new_row_name"),
        ):
            with self.subTest(action=action, kwargs=kwargs):
                with self.assertRaises(ValueError) as raised:
                    datatable_change(action, **kwargs)
                self.assertIn(fragment, str(raised.exception))


class DatatableToolTests(unittest.TestCase):
    def test_tool_forwards_to_the_workflow_service(self) -> None:
        server, workflow = _surface()
        result = server.tools["ue_datatable"](
            action="set_cell",
            asset_path="/Game/DA.DA",
            row_name="R",
            field_name="Value",
            value=7,
            mode="DryRun",
            description="why",
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["action"], "set_cell")
        self.assertEqual(len(workflow.calls), 1)
        call = workflow.calls[0]
        self.assertEqual(call["tool_name"], "ue_datatable")
        self.assertEqual(call["mode"], "DryRun")
        self.assertEqual(call["asset_path"], "/Game/DA.DA")
        self.assertEqual(call["description"], "why")
        self.assertEqual(call["operation"], "setDataTableCell")
        self.assertEqual(call["target"], {"rowName": "R", "fieldName": "Value"})
        self.assertEqual(call["value"], 7)

    def test_aliases_resolve_to_the_canonical_action(self) -> None:
        server, workflow = _surface()
        result = server.tools["ue_datatable"](action="cell", asset_path="A", row_name="R", field_name="F", value=1)
        self.assertEqual(result["action"], "set_cell")
        self.assertEqual(workflow.calls[-1]["operation"], "setDataTableCell")

    def test_unknown_action_reports_instead_of_forwarding(self) -> None:
        server, workflow = _surface()
        result = server.tools["ue_datatable"](action="explode", asset_path="A", row_name="R")
        self.assertFalse(result["ok"])
        self.assertIn("unknown action", result["error"])
        self.assertEqual(workflow.calls, [])

    def test_incomplete_call_is_a_structured_error(self) -> None:
        server, workflow = _surface()
        result = server.tools["ue_datatable"](action="rename_row", asset_path="A", row_name="R")
        self.assertFalse(result["ok"])
        self.assertIn("new_row_name", result["error"])
        self.assertEqual(workflow.calls, [])


if __name__ == "__main__":
    unittest.main()

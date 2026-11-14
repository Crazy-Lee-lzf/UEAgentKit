"""0-3.4: one call runs preview, apply and optional save for the other four writers."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TESTS = Path(__file__).resolve().parent
for entry in (str(SRC), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.experimental_graph_edit import (  # noqa: E402
    APPLY_METHOD as GE_APPLY,
    APPLY_CAPABILITY as GE_APPLY_CAP,
    PREVIEW_METHOD as GE_PREVIEW,
    PREVIEW_CAPABILITY as GE_PREVIEW_CAP,
    SAVE_METHOD as GE_SAVE,
    SAVE_CAPABILITY as GE_SAVE_CAP,
)
from ue_agent_kit.experimental_node_remove_write import (  # noqa: E402
    APPLY_CAPABILITY as NR_APPLY_CAP,
    PREVIEW_CAPABILITY as NR_PREVIEW_CAP,
    SAVE_CAPABILITY as NR_SAVE_CAP,
)
from ue_agent_kit.experimental_rewire_write import (  # noqa: E402
    APPLY_CAPABILITY as RW_APPLY_CAP,
    PREVIEW_CAPABILITY as RW_PREVIEW_CAP,
    SAVE_CAPABILITY as RW_SAVE_CAP,
)
from ue_agent_kit.experimental_variable_write import (  # noqa: E402
    APPLY_METHOD as VW_APPLY,
    APPLY_CAPABILITY as VW_APPLY_CAP,
    PREVIEW_METHOD as VW_PREVIEW,
    PREVIEW_CAPABILITY as VW_PREVIEW_CAP,
    SAVE_CAPABILITY as VW_SAVE_CAP,
    SAVE_METHOD as VW_SAVE,
)
from ue_agent_kit.mcp_graph_edit_experimental import (  # noqa: E402
    register_graph_edit_experimental_tools,
)
from ue_agent_kit.mcp_node_remove_write_experimental import (  # noqa: E402
    register_node_remove_write_experimental_tools,
)
from ue_agent_kit.mcp_rewire_write_experimental import (  # noqa: E402
    register_rewire_write_experimental_tools,
)
from ue_agent_kit.mcp_variable_write_experimental import (  # noqa: E402
    register_variable_write_experimental_tools,
)

ALL_CAPS = [
    GE_PREVIEW_CAP, GE_APPLY_CAP, GE_SAVE_CAP,
    NR_PREVIEW_CAP, NR_APPLY_CAP, NR_SAVE_CAP,
    RW_PREVIEW_CAP, RW_APPLY_CAP, RW_SAVE_CAP,
    VW_PREVIEW_CAP, VW_APPLY_CAP, VW_SAVE_CAP,
]


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


class _Staged:
    """Records calls and returns a canned payload per bridge method."""

    def __init__(self, payloads: dict[str, dict[str, Any]] | None = None,
                 fail_at: str = "") -> None:
        self.calls: list[str] = []
        self.payloads = payloads or {}
        self.fail_at = fail_at

    def status(self) -> dict[str, Any]:
        return {"state": "available", "capabilities": ALL_CAPS}

    def call_method(self, method: str, params: Any = None, *, timeout_seconds: Any = None):
        self.calls.append(method)
        if self.fail_at and method == self.fail_at:
            raise RuntimeError(f"{method} failed on purpose")
        payload = self.payloads.get(method)
        if payload is None:
            raise RuntimeError(f"no canned payload for {method}")
        return copy.deepcopy(payload)


REGISTRARS = {
    "ue_edit_blueprint_graph": register_graph_edit_experimental_tools,
    "ue_remove_blueprint_node": register_node_remove_write_experimental_tools,
    "ue_rewire_blueprint_pin": register_rewire_write_experimental_tools,
    "ue_create_blueprint_variables": register_variable_write_experimental_tools,
}

# The minimum required arguments for each combined tool, with deliberately
# invalid values so Preview rejects them before any bridge call.
INVALID = {
    "ue_edit_blueprint_graph": dict(
        assetPath="", graphGuid="", nodes=[], connections=[]),
    "ue_remove_blueprint_node": dict(
        asset_path="", graph_guid="", node_guid="",
        expected_node_class="", expected_edge_set_hash=""),
    "ue_rewire_blueprint_pin": dict(
        asset_path="", graph_guid="", operation="",
        target_node_guid="", target_pin_name="", target_pin_guid="",
        old_source_node_guid="", old_source_pin_name="", old_source_pin_guid="",
        expected_edge_set_hash=""),
    "ue_create_blueprint_variables": dict(asset_path="", variables=[]),
}


class CombinedToolContractTests(unittest.TestCase):
    def _register(self, name: str, service: _Staged):
        server = _Server()
        REGISTRARS[name](
            server=server,
            live_editor_service=service,
            tool_annotations_type=_Annotations,
            error_response=_error_response,
        )
        return server

    def test_every_combined_tool_is_registered(self) -> None:
        service = _Staged()
        for name in REGISTRARS:
            with self.subTest(tool=name):
                self.assertIn(name, self._register(name, service).tools)

    def test_invalid_input_fails_before_the_bridge(self) -> None:
        for name, kwargs in INVALID.items():
            with self.subTest(tool=name):
                service = _Staged()
                tool = self._register(name, service).tools[name]
                out = tool(**kwargs)
                self.assertFalse(out["ok"])
                self.assertEqual(service.calls, [])
                self.assertEqual(out["completedStages"], [])
                self.assertEqual(out["stages"], {})


class CombinedToolHappyPathTests(unittest.TestCase):
    """Happy path on the variable writer: the compositon is identical elsewhere."""

    def setUp(self) -> None:
        from test_variable_write_experimental import _apply, _preview

        self.service = _Staged({
            VW_PREVIEW: _preview(),
            VW_APPLY: _apply(),
            VW_SAVE: {"saved": True},
        })
        server = _Server()
        register_variable_write_experimental_tools(
            server=server,
            live_editor_service=self.service,
            tool_annotations_type=_Annotations,
            error_response=_error_response,
        )
        self.tool = server.tools["ue_create_blueprint_variables"]
        self.variables = [{"name": "X", "type": "bool", "defaultValue": "true"}]

    def test_applies_without_saving_by_default(self) -> None:
        out = self.tool(asset_path="/Game/A.BP", variables=self.variables)
        self.assertTrue(out["ok"])
        self.assertFalse(out["saved"])
        self.assertEqual(self.service.calls, [VW_PREVIEW, VW_APPLY])
        self.assertEqual(sorted(out["stages"]), ["apply", "preview"])

    def test_save_true_runs_all_three_in_order(self) -> None:
        out = self.tool(asset_path="/Game/A.BP", variables=self.variables, save=True)
        self.assertTrue(out["ok"])
        self.assertTrue(out["saved"])
        self.assertEqual(self.service.calls, [VW_PREVIEW, VW_APPLY, VW_SAVE])
        self.assertEqual(sorted(out["stages"]), ["apply", "preview", "save"])

    def test_apply_failure_reports_how_far_it_got(self) -> None:
        self.service.fail_at = VW_APPLY
        out = self.tool(asset_path="/Game/A.BP", variables=self.variables)
        self.assertFalse(out["ok"])
        self.assertEqual(out["completedStages"], ["preview"])
        self.assertNotIn("apply", out["stages"])


if __name__ == "__main__":
    unittest.main()

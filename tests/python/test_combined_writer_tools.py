"""0-3.4: the graph-edit combined tool still runs preview, apply and optional save.

Slice 16 removed the combined tools of the four writers whose kinds the atomic
patch owns; the graph-edit writer keeps its own pipeline, so its combined tool
stays covered here.
"""

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
    APPLY_CAPABILITY as GE_APPLY_CAP,
    PREVIEW_CAPABILITY as GE_PREVIEW_CAP,
    SAVE_CAPABILITY as GE_SAVE_CAP,
)
from ue_agent_kit.mcp_graph_edit_experimental import (  # noqa: E402
    register_graph_edit_experimental_tools,
)

ALL_CAPS = [GE_PREVIEW_CAP, GE_APPLY_CAP, GE_SAVE_CAP]


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

    def __init__(self, payloads: dict[str, dict[str, Any]] | None = None, fail_at: str = "") -> None:
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
}

# The minimum required arguments for the combined tool, with deliberately
# invalid values so Preview rejects them before any bridge call.
INVALID = {
    "ue_edit_blueprint_graph": dict(assetPath="", graphGuid="", nodes=[], connections=[]),
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


if __name__ == "__main__":
    unittest.main()

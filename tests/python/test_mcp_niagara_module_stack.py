"""Niagara N1: `ue_get_niagara_module_stack` read-only contract.

The bridge handler is C++ and the live read needs a running Editor, so these
tests pin the Python wrapper's validation/forwarding and the C++ reader's
read-only structure.  They are not a Native PASS on their own.
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

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.mcp_live_tools import register_live_read_tools  # noqa: E402
from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME, tool_names_for_mode  # noqa: E402

PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
NIAGARA_PRIVATE = PRIVATE / "Niagara"
SYSTEM = "/Game/Dev/NS_DevStack"


class _FakeAnnotations:
    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorator(function):
            self.tools[function.__name__] = function
            return function

        return decorator


class _FakeLiveService:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.result = result if result is not None else {
            "schemaVersion": "1.0",
            "systemPath": SYSTEM,
            "loaded": True,
            "compiled": True,
            "emitterCount": 2,
            "emitters": [],
        }
        self.error = error

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, dict(params or {})))
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _error_response(tool: str, exc: Exception, *, read_only: bool) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "ue-agent-kit-error"), "message": str(exc)},
    }


def _register(service: Any) -> _FakeServer:
    server = _FakeServer()
    register_live_read_tools(
        server=server,
        live_editor_service=service,
        index_service=None,
        audit_report_root=None,
        read_annotations=None,
        tool_annotations_type=_FakeAnnotations,
        error_response=_error_response,
    )
    return server


class NiagaraModuleStackToolTests(unittest.TestCase):
    def test_registry_entry_is_live_read_and_read_only(self) -> None:
        definition = TOOL_DEFINITIONS_BY_NAME["ue_get_niagara_module_stack"]
        self.assertEqual(definition.group, "live-read")
        self.assertEqual(definition.annotation, "read")
        self.assertEqual(definition.live_method, "editor.getNiagaraModuleStack")
        self.assertTrue(definition.read_only)
        self.assertIn("ue_get_niagara_module_stack", tool_names_for_mode(live_editor_enabled=True))

    def test_tool_forwards_exact_bounded_request(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        payload = server.tools["ue_get_niagara_module_stack"](
            system_path=SYSTEM,
            include_inputs=False,
            max_modules=8,
            max_inputs_per_module=4,
        )

        self.assertEqual(
            service.calls,
            [
                (
                    "ue_get_niagara_module_stack",
                    {
                        "systemPath": SYSTEM,
                        "includeInputs": False,
                        "maxModules": 8,
                        "maxInputsPerModule": 4,
                    },
                )
            ],
        )
        self.assertTrue(payload["loaded"])
        self.assertEqual(payload["emitterCount"], 2)

    def test_defaults_match_the_documented_bounds(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        server.tools["ue_get_niagara_module_stack"](system_path=SYSTEM)
        self.assertEqual(
            service.calls[0][1],
            {
                "systemPath": SYSTEM,
                "includeInputs": True,
                "maxModules": 128,
                "maxInputsPerModule": 32,
            },
        )

    def test_rejects_paths_outside_game(self) -> None:
        server = _register(_FakeLiveService())
        for bad in ("/Engine/Dev/NS", "/Game/Dev/../Evil", "/Game/Dev\\NS", "Game/Dev/NS", ""):
            with self.subTest(path=bad):
                payload = server.tools["ue_get_niagara_module_stack"](system_path=bad)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "live-editor-invalid-parameters")

    def test_rejects_out_of_range_bounds(self) -> None:
        server = _register(_FakeLiveService())
        cases = (
            {"max_modules": 0},
            {"max_modules": 1025},
            {"max_modules": True},
            {"max_inputs_per_module": -1},
            {"max_inputs_per_module": 257},
            {"include_inputs": "yes"},
        )
        for case in cases:
            with self.subTest(case=case):
                payload = server.tools["ue_get_niagara_module_stack"](system_path=SYSTEM, **case)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "live-editor-invalid-parameters")

    def test_bridge_error_is_surfaced_read_only(self) -> None:
        service = _FakeLiveService(
            error=LiveEditorError("niagara-system-not-found", "No Niagara System asset")
        )
        server = _register(service)
        payload = server.tools["ue_get_niagara_module_stack"](system_path=SYSTEM)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["error"]["code"], "niagara-system-not-found")


class NiagaraReaderStructureTests(unittest.TestCase):
    """Pin the read-only shape of the C++ reader so a later slice cannot quietly add writes."""

    def test_bridge_whitelists_the_method_and_keeps_the_handler_split(self) -> None:
        bridge = (PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        self.assertIn('TEXT("editor.getNiagaraModuleStack")', bridge)
        self.assertNotIn("FUEAgentKitEditorBridge::TryBuildNiagaraModuleStackResult", bridge)
        handler = (PRIVATE / "EditorBridgeNiagaraHandlers.cpp").read_text(encoding="utf-8")
        self.assertIn("FUEAgentKitEditorBridge::TryBuildNiagaraModuleStackResult", handler)
        self.assertIn("editor.getNiagaraModuleStack", handler)

    def test_reader_does_not_write_or_compile(self) -> None:
        reader = (NIAGARA_PRIVATE / "NiagaraModuleStackReader.cpp").read_text(encoding="utf-8")
        for forbidden in (
            "SetLocalValue",
            "FScopedTransaction",
            "SavePackage",
            "RequestCompile",
            "Modify()",
            "SetLinkedParameterValue",
        ):
            self.assertNotIn(forbidden, reader, forbidden)
        self.assertIn("GetFilteredChildren", reader)
        self.assertIn("RefreshChildren", reader)
        self.assertIn("GetScriptOutputNode", reader)
        self.assertIn("GetUsage()", reader)

    def test_reader_reports_the_documented_error_codes(self) -> None:
        reader = (NIAGARA_PRIVATE / "NiagaraModuleStackReader.cpp").read_text(encoding="utf-8")
        for code in (
            "niagara-system-not-found",
            "niagara-system-compiling",
            "niagara-stack-unavailable",
        ):
            self.assertIn(code, reader, code)


if __name__ == "__main__":
    unittest.main()

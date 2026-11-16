"""Niagara N2: `ue_set_niagara_module_input` write contract.

The bridge handler is C++ and needs a running Editor, so these tests pin the
Python wrapper (registration, validation, forwarding) and the C++ writer's
refusal structure.  They are not a Native PASS on their own.
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
from ue_agent_kit.mcp_live_action_tools import register_live_action_tools  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_DEFINITIONS_BY_NAME,
    tool_names_for_mode,
)

PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
NIAGARA_PRIVATE = PRIVATE / "Niagara"
SYSTEM = "/Game/Dev/NS_DevStack"
EMITTER = "EmitterA"


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
            "systemPath": SYSTEM,
            "emitterName": EMITTER,
            "moduleName": "SpawnRate",
            "inputName": "Spawn Rate",
            "valueMode": "Local",
            "oldValue": 10.0,
            "newValue": 11.0,
            "wrote": True,
            "transactionCount": 1,
            "undoVerified": True,
            "redoVerified": True,
            "saved": False,
            "requiresSave": True,
        }
        self.error = error

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((tool_name, dict(params or {})))
        if self.error is not None:
            raise self.error
        return {
            "schemaVersion": "1.0",
            "tool": tool_name,
            "ok": True,
            "readOnly": False,
            "result": dict(self.result),
        }


def _error_response(tool: str, exc: Exception, *, read_only: bool) -> dict[str, Any]:
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {
            "code": getattr(exc, "code", "ue-agent-kit-error"),
            "message": str(exc),
            "details": getattr(exc, "details", {}) or {},
        },
    }


def _register(service: Any) -> _FakeServer:
    server = _FakeServer()
    register_live_action_tools(
        server=server,
        live_editor_service=service,
        tool_annotations_type=_FakeAnnotations,
        error_response=_error_response,
    )
    return server


class NiagaraModuleInputToolTests(unittest.TestCase):
    def test_registry_entry_is_a_live_action_write(self) -> None:
        definition = TOOL_DEFINITIONS_BY_NAME["ue_set_niagara_module_input"]
        self.assertEqual(definition.group, "live-action")
        self.assertEqual(definition.annotation, "planning")
        self.assertEqual(definition.live_method, "editor.setNiagaraModuleInput")
        self.assertFalse(definition.read_only)
        self.assertIn("ue_set_niagara_module_input", tool_names_for_mode(live_editor_enabled=True))

    def test_tool_forwards_the_exact_request(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        payload = server.tools["ue_set_niagara_module_input"](
            system_path=SYSTEM,
            emitter_name=EMITTER,
            module_name="SpawnRate",
            input_name="Spawn Rate",
            value=11.0,
        )

        self.assertEqual(
            service.calls,
            [
                (
                    "ue_set_niagara_module_input",
                    {
                        "systemPath": SYSTEM,
                        "moduleName": "SpawnRate",
                        "inputName": "Spawn Rate",
                        "value": 11.0,
                        "moduleOccurrence": 0,
                        "expectedValueMode": "Local",
                        "verifyUndo": True,
                        "compileTimeoutSeconds": 30,
                        "emitterName": EMITTER,
                    },
                )
            ],
        )
        self.assertTrue(payload["ok"])
        self.assertFalse(payload["readOnly"])
        self.assertTrue(payload["result"]["wrote"])

    def test_optional_fields_are_omitted_when_empty(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        server.tools["ue_set_niagara_module_input"](
            system_path=SYSTEM,
            emitter_handle_id="7f2b1c40-1111-2222-3333-444455556666",
            module_name="SpawnRate",
            input_name="SpawnRate.SpawnRate",
            value=3.0,
            stage="EmitterUpdate",
            module_occurrence=1,
            expected_old_value=2.0,
            verify_undo=False,
        )
        params = service.calls[0][1]
        self.assertNotIn("emitterName", params)
        self.assertEqual(params["emitterHandleId"], "7f2b1c40-1111-2222-3333-444455556666")
        self.assertEqual(params["stage"], "EmitterUpdate")
        self.assertEqual(params["moduleOccurrence"], 1)
        self.assertEqual(params["expectedOldValue"], 2.0)
        self.assertFalse(params["verifyUndo"])

    def test_rejects_bad_parameters_without_calling_the_bridge(self) -> None:
        service = _FakeLiveService()
        server = _register(service)
        cases = (
            {"system_path": "/Engine/Dev/NS", "module_name": "M", "input_name": "I", "value": 1.0},
            {"system_path": SYSTEM, "module_name": "", "input_name": "I", "value": 1.0},
            {"system_path": SYSTEM, "module_name": "M", "input_name": "", "value": 1.0},
            {"system_path": SYSTEM, "module_name": "M", "input_name": "I", "value": None},
            {
                "system_path": SYSTEM,
                "module_name": "M",
                "input_name": "I",
                "value": 1.0,
                "module_occurrence": -1,
            },
            {
                "system_path": SYSTEM,
                "module_name": "M",
                "input_name": "I",
                "value": 1.0,
                "compile_timeout_seconds": 0,
            },
        )
        for case in cases:
            with self.subTest(case=case):
                payload = server.tools["ue_set_niagara_module_input"](**case)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["error"]["code"], "live-editor-invalid-parameters")
        self.assertEqual(service.calls, [])

    def test_non_local_refusal_keeps_its_details(self) -> None:
        service = _FakeLiveService(
            error=LiveEditorError(
                "niagara-input-not-local",
                "Input 'Color.Color' is provided by Linked, not a local constant.",
                details={"valueMode": "Linked", "linkedTarget": "Particles.Color"},
            )
        )
        server = _register(service)
        payload = server.tools["ue_set_niagara_module_input"](
            system_path=SYSTEM,
            emitter_name=EMITTER,
            module_name="Color",
            input_name="Color",
            value={"r": 1.0, "g": 0.0, "b": 0.0, "a": 1.0},
        )
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["readOnly"])
        self.assertEqual(payload["error"]["code"], "niagara-input-not-local")
        self.assertEqual(payload["error"]["details"]["linkedTarget"], "Particles.Color")


class NiagaraWriterStructureTests(unittest.TestCase):
    """Pin the safety promises of the C++ writer so a later slice cannot relax them."""

    def _writer(self) -> str:
        return (NIAGARA_PRIVATE / "NiagaraModuleStackWriter.cpp").read_text(encoding="utf-8")

    def test_only_local_inputs_are_written(self) -> None:
        writer = self._writer()
        self.assertIn("EValueMode::Local", writer)
        self.assertIn("niagara-input-not-local", writer)
        self.assertIn("linkedTarget", writer)
        # A Linked input must never be converted into a constant.
        self.assertNotIn("RemoveNodesForOverridePin", writer)

    def test_writer_never_saves(self) -> None:
        writer = self._writer()
        self.assertNotIn("SavePackage", writer)
        self.assertIn('TEXT("saved"), false', writer)
        self.assertIn('TEXT("requiresSave")', writer)

    def test_writer_refuses_stale_and_reports_zero_side_effects(self) -> None:
        writer = self._writer()
        self.assertIn("niagara-input-stale", writer)
        self.assertIn("ExpectedOldValue", writer)
        # The stale branch must return before the transaction is opened.
        stale_index = writer.index("EWriteFailure::Stale")
        transaction_index = writer.index("FScopedTransaction")
        self.assertLess(stale_index, transaction_index)

    def test_writer_uses_one_transaction_and_verifies_undo(self) -> None:
        writer = self._writer()
        self.assertIn("FScopedTransaction", writer)
        self.assertIn("GetUndoCount", writer)
        self.assertIn("UndoTransaction", writer)
        self.assertIn("RedoTransaction", writer)
        self.assertIn("undoVerified", writer)
        self.assertIn("redoVerified", writer)

    def test_writer_waits_for_the_compile_with_a_bound(self) -> None:
        writer = self._writer()
        self.assertIn("RequestCompile", writer)
        self.assertIn("PollForCompilationComplete", writer)
        self.assertIn("CompileTimeoutSeconds", writer)
        self.assertIn("compileTimedOut", writer)

    def test_bridge_whitelists_the_method_and_keeps_the_handler_split(self) -> None:
        bridge = (PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        self.assertIn('TEXT("editor.setNiagaraModuleInput")', bridge)
        self.assertNotIn("FUEAgentKitEditorBridge::TrySetNiagaraModuleInputResult", bridge)
        handler = (PRIVATE / "EditorBridgeNiagaraHandlers.cpp").read_text(encoding="utf-8")
        self.assertIn("FUEAgentKitEditorBridge::TrySetNiagaraModuleInputResult", handler)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.experimental_pin_default_edit import (  # noqa: E402
    APPLY_CAPABILITY,
    APPLY_CONFIRMATION_PREFIX,
    APPLY_TOOL_NAME,
    PREVIEW_CAPABILITY,
    PREVIEW_SCHEMA_VERSION,
    PREVIEW_TOOL_NAME,
    SAVE_CAPABILITY,
    SAVE_TOOL_NAME,
    PinDefaultError,
    build_apply_parameters,
    build_save_parameters,
    canonical_value,
    confirmation_for_plan,
    save_confirmation_for_plan,
    validate_preview_parameters,
    validate_preview_result,
)
from ue_agent_kit.mcp_pin_default_edit_experimental import (  # noqa: E402
    register_pin_default_edit_experimental_tools,
)

PLAN_ID = "sha256:" + "a" * 64
ASSET = "/Game/UEAgentKitTests/BP_PinDefault_Target.BP_PinDefault_Target"
GRAPH = "11111111-2222-4333-8444-555555555555"
NODE = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PIN = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
BEFORE_FINGERPRINT = "sha256:" + "b" * 64
AFTER_FINGERPRINT = "sha256:" + "c" * 64
GRAPH_FINGERPRINT = "sha1:" + "d" * 40
PACKAGE_REVISION = "sha256:" + "e" * 64


def _default(**overrides: Any) -> dict[str, Any]:
    item = {
        "nodeGuid": NODE,
        "pinName": "Condition",
        "pinGuid": PIN,
        "type": "bool",
        "value": "true",
    }
    item.update(overrides)
    return item


def _preview(**overrides: Any) -> dict[str, Any]:
    entry = {
        "nodeGuid": NODE,
        "pinName": "Condition",
        "pinGuid": PIN,
        "type": "bool",
        "beforeValue": "false",
        "afterValue": "true",
    }
    result = {
        "schemaVersion": PREVIEW_SCHEMA_VERSION,
        "experimental": True,
        "readOnly": True,
        "wroteBlueprint": False,
        "savedPackage": False,
        "compiled": False,
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "packageRevision": PACKAGE_REVISION,
        "graphFingerprint": GRAPH_FINGERPRINT,
        "defaultsFingerprint": BEFORE_FINGERPRINT,
        "defaultCount": 1,
        "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
        "defaults": [entry],
    }
    result.update(overrides)
    return result


class _FakeServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None):
        def decorate(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorate


class _FakeLiveEditor:
    def __init__(self, capabilities: list[str] | None = None) -> None:
        self.capabilities = capabilities if capabilities is not None else [
            PREVIEW_CAPABILITY,
            APPLY_CAPABILITY,
            SAVE_CAPABILITY,
        ]
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []
        self.result: dict[str, Any] = _preview()

    def status(self) -> dict[str, Any]:
        return {"state": "available", "capabilities": self.capabilities}

    def call_method(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, copy.deepcopy(dict(params or {})), timeout_seconds))
        return copy.deepcopy(self.result)


def _annotations(**kwargs: Any) -> dict[str, Any]:
    return kwargs


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "error"), "message": str(exc)},
    }


class PinDefaultContractTests(unittest.TestCase):
    def test_all_supported_types_normalize(self) -> None:
        cases = [
            ("bool", "false", "false"),
            ("int", "-2147483648", "-2147483648"),
            ("double", "-12.50", "-12.50"),
            ("string", "", ""),
            ("string", "Hello, 世界", "Hello, 世界"),
        ]
        for index, (pin_type, value, expected) in enumerate(cases):
            with self.subTest(pin_type=pin_type, value=value):
                request = validate_preview_parameters(
                    asset_path=ASSET,
                    graph_guid=GRAPH,
                    defaults=[
                        _default(
                            nodeGuid=f"aaaaaaaa-bbbb-4ccc-8ddd-{index:012d}",
                            pinName=f"Pin{index}",
                            pinGuid=f"bbbbbbbb-cccc-4ddd-8eee-{index:012d}",
                            type=pin_type,
                            value=value,
                        )
                    ],
                )
                self.assertEqual(request["defaults"][0]["value"], expected)

    def test_invalid_values_are_rejected(self) -> None:
        cases = [
            ("bool", "True", "graph-pin-default-value-invalid"),
            ("bool", "1", "graph-pin-default-value-invalid"),
            ("int", "+1", "graph-pin-default-value-invalid"),
            ("int", "01", "graph-pin-default-value-invalid"),
            ("int", "-0", "graph-pin-default-value-invalid"),
            ("int", "2147483648", "graph-pin-default-value-invalid"),
            ("double", "1e3", "graph-pin-default-value-invalid"),
            ("double", "1.", "graph-pin-default-value-invalid"),
            ("double", "-0", "graph-pin-default-value-invalid"),
            ("double", "1.2.3", "graph-pin-default-value-invalid"),
            ("string", "line\nbreak", "graph-pin-default-value-invalid"),
            ("string", "x" * 1025, "graph-pin-default-value-invalid"),
            ("object", "x", "graph-pin-default-type-not-allowed"),
        ]
        for pin_type, value, code in cases:
            with self.subTest(pin_type=pin_type, value=value):
                with self.assertRaises(PinDefaultError) as raised:
                    canonical_value(pin_type, value)
                self.assertEqual(raised.exception.code, code)

    def test_duplicate_target_is_rejected(self) -> None:
        with self.assertRaises(PinDefaultError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                defaults=[_default(), _default(value="false")],
            )
        self.assertEqual(raised.exception.code, "graph-pin-default-duplicate")

    def test_bad_identity_and_unknown_fields_are_rejected(self) -> None:
        with self.assertRaises(PinDefaultError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                defaults=[_default(nodeGuid="not-a-guid")],
            )
        self.assertEqual(raised.exception.code, "graph-pin-default-invalid-identifier")

        with self.assertRaises(PinDefaultError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                defaults=[_default(extra=True)],
            )
        self.assertEqual(raised.exception.code, "graph-pin-default-unknown-parameter")

    def test_preview_tampering_fails_closed(self) -> None:
        validate_preview_result(_preview())

        tampered = copy.deepcopy(_preview())
        tampered["planId"] = "sha256:" + "f" * 63
        with self.assertRaises(PinDefaultError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "graph-pin-default-preview-invalid")

        tampered = copy.deepcopy(_preview())
        tampered["requiredConfirmation"] = "SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS other"
        with self.assertRaises(PinDefaultError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "graph-pin-default-preview-invalid")

        tampered = copy.deepcopy(_preview())
        tampered["defaultCount"] = 2
        with self.assertRaises(PinDefaultError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "graph-pin-default-preview-invalid")

    def test_apply_parameters_are_derived_only_from_preview(self) -> None:
        preview = _preview()
        params = build_apply_parameters(preview, confirmation_for_plan(PLAN_ID))
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["defaults"][0]["value"], "true")
        self.assertEqual(params["defaults"][0]["type"], "bool")

        with self.assertRaises(PinDefaultError) as raised:
            build_apply_parameters(preview, "SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS wrong")
        self.assertEqual(raised.exception.code, "graph-pin-default-confirmation-required")

    def test_save_parameters_bind_apply_result(self) -> None:
        apply_result = {
            "applied": True,
            "saved": False,
            "assetPath": ASSET,
            "graphGuid": GRAPH,
            "planId": PLAN_ID,
            "graphFingerprintAfter": GRAPH_FINGERPRINT,
            "defaultsFingerprintAfter": AFTER_FINGERPRINT,
            "nodeCountAfter": 4,
            "linkCountAfter": 3,
        }
        params = build_save_parameters(
            apply_result,
            save_confirmation_for_plan(PLAN_ID),
        )
        self.assertEqual(params["expectedDefaultsFingerprint"], AFTER_FINGERPRINT)
        self.assertEqual(params["expectedNodeCount"], 4)

        not_applied = copy.deepcopy(apply_result)
        not_applied["applied"] = False
        with self.assertRaises(PinDefaultError) as raised:
            build_save_parameters(not_applied, save_confirmation_for_plan(PLAN_ID))
        self.assertEqual(
            raised.exception.code,
            "graph-pin-default-save-requires-apply",
        )


class PinDefaultMcpTests(unittest.TestCase):
    def _register(self, service: _FakeLiveEditor) -> _FakeServer:
        server = _FakeServer()
        register_pin_default_edit_experimental_tools(
            server=server,
            live_editor_service=service,
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        return server

    def test_tools_are_conditional_and_use_exact_preview(self) -> None:
        service = _FakeLiveEditor()
        server = self._register(service)
        self.assertEqual(
            set(server.tools),
            {PREVIEW_TOOL_NAME, APPLY_TOOL_NAME, SAVE_TOOL_NAME},
        )
        result = server.tools[PREVIEW_TOOL_NAME](
            asset_path=ASSET,
            graph_guid=GRAPH,
            defaults=[_default()],
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["readOnly"])
        self.assertEqual(service.calls[0][0], PREVIEW_CAPABILITY.replace("preview", "preview"))
        self.assertEqual(service.calls[0][1]["defaults"][0]["value"], "true")

    def test_apply_and_save_do_not_accept_caller_built_requests(self) -> None:
        service = _FakeLiveEditor()
        server = self._register(service)
        with self.assertRaises(TypeError):
            server.tools[APPLY_TOOL_NAME](asset_path=ASSET)  # type: ignore[call-arg]
        with self.assertRaises(TypeError):
            server.tools[SAVE_TOOL_NAME](plan_id=PLAN_ID)  # type: ignore[call-arg]

    def test_missing_capability_fails_closed(self) -> None:
        service = _FakeLiveEditor(capabilities=[PREVIEW_CAPABILITY])
        server = self._register(service)
        result = server.tools[APPLY_TOOL_NAME](
            preview_result=_preview(),
            confirmation=confirmation_for_plan(PLAN_ID),
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "live-editor-capability-missing")
        self.assertEqual(service.calls, [])


if __name__ == "__main__":
    unittest.main()

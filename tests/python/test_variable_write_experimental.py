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

from ue_agent_kit.experimental_variable_write import (  # noqa: E402
    APPLY_CAPABILITY,
    APPLY_CONFIRMATION_PREFIX,
    APPLY_SCHEMA_VERSION,
    MAX_VARIABLES,
    PREVIEW_CAPABILITY,
    PREVIEW_SCHEMA_VERSION,
    SAVE_CAPABILITY,
    SAVE_CONFIRMATION_PREFIX,
    SAVE_SCHEMA_VERSION,
    VariableWriteError,
    build_apply_parameters,
    build_save_parameters,
    canonical_variable_value,
    confirmation_for_plan,
    save_confirmation_for_plan,
    validate_apply_result,
    validate_preview_parameters,
    validate_preview_result,
    validate_save_result,
)
from ue_agent_kit.mcp_variable_write_experimental import (  # noqa: E402
    register_variable_write_experimental_tools,
)

PLAN_ID = "sha256:" + "a" * 64
ASSET = "/Game/UEAgentKitTests/BP_PinDefaultExpansion.BP_PinDefaultExpansion"
FINGERPRINT = "sha256:" + "b" * 64
AFTER_FINGERPRINT = "sha256:" + "c" * 64
PACKAGE_REVISION = "sha256:" + "d" * 64
GUID = "11111111-2222-4333-8444-555555555555"


def _variable(**overrides: Any) -> dict[str, Any]:
    item = {"name": "NewBool", "type": "bool", "defaultValue": "true"}
    item.update(overrides)
    return item


def _preview(**overrides: Any) -> dict[str, Any]:
    result = {
        "schemaVersion": PREVIEW_SCHEMA_VERSION,
        "experimental": True,
        "readOnly": True,
        "wroteBlueprint": False,
        "savedPackage": False,
        "compiled": False,
        "skeletonRegenerated": False,
        "requiresExplicitCompile": True,
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "packageRevision": PACKAGE_REVISION,
        "variablesFingerprint": FINGERPRINT,
        "variableCountBefore": 2,
        "createCount": 1,
        "variables": [{"name": "NewBool", "type": "bool", "defaultValue": "true"}],
        "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
    }
    result.update(overrides)
    return result


def _created(**overrides: Any) -> dict[str, Any]:
    item = {
        "name": "NewBool",
        "type": "bool",
        "defaultValue": "true",
        "guid": GUID,
    }
    item.update(overrides)
    return item


def _apply(**overrides: Any) -> dict[str, Any]:
    result = {
        "schemaVersion": APPLY_SCHEMA_VERSION,
        "experimental": True,
        "applied": True,
        "singleTransaction": True,
        "undoVerified": True,
        "redoVerified": True,
        "saved": False,
        "compiled": False,
        "skeletonRegenerated": False,
        "requiresExplicitCompile": True,
        "packageDirty": True,
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "packageRevision": PACKAGE_REVISION,
        "variablesFingerprintBefore": FINGERPRINT,
        "variablesFingerprintAfter": AFTER_FINGERPRINT,
        "variableCountBefore": 2,
        "variableCountAfter": 3,
        "createCount": 1,
        "createdVariables": [_created()],
        "requiredSaveConfirmation": SAVE_CONFIRMATION_PREFIX + PLAN_ID,
    }
    result.update(overrides)
    return result


def _save(**overrides: Any) -> dict[str, Any]:
    result = {
        "schemaVersion": SAVE_SCHEMA_VERSION,
        "experimental": True,
        "saved": True,
        "packageDirty": False,
        "readbackVerified": True,
        "independentReloadVerified": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "compiled": False,
        "skeletonRegenerated": False,
        "requiresExplicitCompile": True,
        "autoSave": False,
        "assetPath": ASSET,
        "planId": PLAN_ID,
        "assetSha256": "sha256:" + "e" * 64,
        "variablesFingerprint": AFTER_FINGERPRINT,
        "variableCount": 3,
        "createCount": 1,
        "variables": [_created()],
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


class VariableWriteContractTests(unittest.TestCase):
    def test_all_supported_types_normalize(self) -> None:
        cases = [
            ("bool", "false", "false"),
            ("int", "-2147483648", "-2147483648"),
            ("double", "-12.50", "-12.50"),
            ("string", "", ""),
            ("string", "Hello, 世界", "Hello, 世界"),
        ]
        for variable_type, value, expected in cases:
            with self.subTest(variable_type=variable_type, value=value):
                request = validate_preview_parameters(
                    asset_path=ASSET,
                    variables=[
                        _variable(
                            name=f"Var{len(value)}",
                            type=variable_type,
                            defaultValue=value,
                        )
                    ],
                )
                self.assertEqual(request["variables"][0]["defaultValue"], expected)

    def test_invalid_values_are_rejected(self) -> None:
        cases = [
            ("bool", "True", "blueprint-variable-value-invalid"),
            ("bool", "1", "blueprint-variable-value-invalid"),
            ("int", "+1", "blueprint-variable-value-invalid"),
            ("int", "01", "blueprint-variable-value-invalid"),
            ("int", "-0", "blueprint-variable-value-invalid"),
            ("int", "2147483648", "blueprint-variable-value-invalid"),
            ("double", "1e3", "blueprint-variable-value-invalid"),
            ("double", "1.", "blueprint-variable-value-invalid"),
            ("double", "-0", "blueprint-variable-value-invalid"),
            ("double", "-0.0", "blueprint-variable-value-invalid"),
            ("double", "1.2.3", "blueprint-variable-value-invalid"),
            ("string", "line\nbreak", "blueprint-variable-value-invalid"),
            ("string", "x" * 1025, "blueprint-variable-value-invalid"),
            ("object", "x", "blueprint-variable-type-not-allowed"),
            ("vector", "0", "blueprint-variable-type-not-allowed"),
        ]
        for variable_type, value, code in cases:
            with self.subTest(variable_type=variable_type, value=value):
                with self.assertRaises(VariableWriteError) as raised:
                    canonical_variable_value(variable_type, value)
                self.assertEqual(raised.exception.code, code)

    def test_invalid_names_are_rejected(self) -> None:
        cases = [
            ("1Bad", "blueprint-variable-name-invalid"),
            ("Bad Name", "blueprint-variable-name-invalid"),
            ("Bad-Name", "blueprint-variable-name-invalid"),
            ("", "blueprint-variable-invalid-parameters"),
            ("x" * 65, "blueprint-variable-invalid-parameters"),
            ("变量", "blueprint-variable-name-invalid"),
            ("Bad.Name", "blueprint-variable-name-invalid"),
        ]
        for name, code in cases:
            with self.subTest(name=name):
                with self.assertRaises(VariableWriteError) as raised:
                    validate_preview_parameters(
                        asset_path=ASSET,
                        variables=[_variable(name=name)],
                    )
                self.assertEqual(raised.exception.code, code)

    def test_duplicate_names_are_case_insensitive(self) -> None:
        with self.assertRaises(VariableWriteError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                variables=[_variable(name="Counter"), _variable(name="counter")],
            )
        self.assertEqual(raised.exception.code, "blueprint-variable-duplicate-name")

    def test_unknown_fields_and_bounds_are_rejected(self) -> None:
        with self.assertRaises(VariableWriteError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                variables=[_variable(extra=True)],
            )
        self.assertEqual(raised.exception.code, "blueprint-variable-unknown-parameter")

        with self.assertRaises(VariableWriteError) as raised:
            validate_preview_parameters(asset_path=ASSET, variables=[])
        self.assertEqual(raised.exception.code, "blueprint-variable-invalid-variables")

        with self.assertRaises(VariableWriteError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                variables=[_variable(name=f"Var{index}") for index in range(MAX_VARIABLES + 1)],
            )
        self.assertEqual(raised.exception.code, "blueprint-variable-invalid-variables")

    def test_preview_tampering_fails_closed(self) -> None:
        validate_preview_result(_preview())

        tampered = copy.deepcopy(_preview())
        tampered["planId"] = "sha256:" + "f" * 63
        with self.assertRaises(VariableWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-variable-invalid-identifier")

        for field, value in (
            ("compiled", True),
            ("skeletonRegenerated", True),
            ("readOnly", False),
            ("wroteBlueprint", True),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_preview())
                tampered[field] = value
                with self.assertRaises(VariableWriteError) as raised:
                    validate_preview_result(tampered)
                self.assertEqual(raised.exception.code, "blueprint-variable-preview-invalid")

        tampered = copy.deepcopy(_preview())
        tampered["createCount"] = 2
        with self.assertRaises(VariableWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-variable-preview-invalid")

    def test_apply_parameters_require_exact_confirmation(self) -> None:
        expected = confirmation_for_plan(PLAN_ID)
        params = build_apply_parameters(_preview(), expected)
        self.assertEqual(params["assetPath"], ASSET)
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(
            params["variables"],
            [{"name": "NewBool", "type": "bool", "defaultValue": "true"}],
        )
        self.assertEqual(params["confirmation"], expected)

        for confirmation in ("", expected + " ", expected.lower(), "CREATE"):
            with self.subTest(confirmation=confirmation):
                with self.assertRaises(VariableWriteError) as raised:
                    build_apply_parameters(_preview(), confirmation)
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-variable-confirmation-required",
                )

    def test_apply_result_tampering_fails_closed(self) -> None:
        validate_apply_result(_apply())

        for field, value in (
            ("applied", False),
            ("undoVerified", False),
            ("redoVerified", False),
            ("saved", True),
            ("compiled", True),
            ("singleTransaction", False),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_apply())
                tampered[field] = value
                with self.assertRaises(VariableWriteError) as raised:
                    validate_apply_result(tampered)
                self.assertEqual(raised.exception.code, "blueprint-variable-apply-invalid")

        tampered = copy.deepcopy(_apply())
        tampered["variableCountAfter"] = 9
        with self.assertRaises(VariableWriteError) as raised:
            validate_apply_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-variable-apply-invalid")

        tampered = copy.deepcopy(_apply())
        tampered["createdVariables"][0]["guid"] = "not-a-guid"
        with self.assertRaises(VariableWriteError) as raised:
            validate_apply_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-variable-apply-invalid")

    def test_save_parameters_are_bound_to_apply_result(self) -> None:
        expected = save_confirmation_for_plan(PLAN_ID)
        params = build_save_parameters(_apply(), expected)
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["expectedVariablesFingerprint"], AFTER_FINGERPRINT)
        self.assertEqual(params["expectedPackageRevision"], PACKAGE_REVISION)
        self.assertEqual(params["expectedVariableCount"], 3)

        not_applied = copy.deepcopy(_apply())
        not_applied["applied"] = False
        with self.assertRaises(VariableWriteError) as raised:
            build_save_parameters(not_applied, expected)
        self.assertEqual(raised.exception.code, "blueprint-variable-apply-invalid")

        with self.assertRaises(VariableWriteError) as raised:
            build_save_parameters(_apply(), "SAVE")
        self.assertEqual(raised.exception.code, "blueprint-variable-confirmation-required")

    def test_save_result_requires_independent_reload_pending(self) -> None:
        validate_save_result(_save())

        for field, value in (
            ("independentReloadVerified", True),
            ("packageDirty", True),
            ("compiled", True),
            ("skeletonRegenerated", True),
            ("saved", False),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_save())
                tampered[field] = value
                with self.assertRaises(VariableWriteError) as raised:
                    validate_save_result(tampered)
                self.assertEqual(raised.exception.code, "blueprint-variable-save-invalid")

        tampered = copy.deepcopy(_save())
        tampered["requiredAcceptanceEvidence"] = "same-process"
        with self.assertRaises(VariableWriteError) as raised:
            validate_save_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-variable-save-invalid")


class VariableWriteMcpToolTests(unittest.TestCase):
    def _tools(self, capabilities: list[str] | None = None):
        server = _FakeServer()
        live = _FakeLiveEditor(capabilities)
        register_variable_write_experimental_tools(
            server=server,
            live_editor_service=live,
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        return server.tools, live

    def test_tools_are_registered_with_expected_names(self) -> None:
        tools, _ = self._tools()
        self.assertEqual(
            set(tools),
            {
                "ue_preview_blueprint_variable_create_experimental",
                "ue_apply_blueprint_variable_create_experimental",
                "ue_save_blueprint_variable_create_experimental",
            },
        )

    def test_preview_calls_bridge_and_marks_read_only(self) -> None:
        tools, live = self._tools()
        result = tools["ue_preview_blueprint_variable_create_experimental"](
            asset_path=ASSET,
            variables=[_variable()],
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["readOnly"])
        self.assertTrue(result["experimental"])
        self.assertFalse(result["productionDefault"])
        self.assertEqual(live.calls[0][0], "editor.previewBlueprintVariableCreateExperimental")
        self.assertEqual(live.calls[0][1]["assetPath"], ASSET)
        self.assertEqual(live.calls[0][2], 30.0)

    def test_missing_capability_fails_closed(self) -> None:
        tools, live = self._tools(capabilities=["editor.status"])
        result = tools["ue_preview_blueprint_variable_create_experimental"](
            asset_path=ASSET,
            variables=[_variable()],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "live-editor-capability-missing")
        self.assertEqual(live.calls, [])

    def test_apply_requires_exact_confirmation_before_bridge(self) -> None:
        tools, live = self._tools()
        result = tools["ue_apply_blueprint_variable_create_experimental"](
            preview_result=_preview(),
            confirmation="CREATE",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "blueprint-variable-confirmation-required")
        self.assertEqual(live.calls, [])

    def test_apply_and_save_dispatch_exact_parameters(self) -> None:
        tools, live = self._tools()
        live.result = _apply()
        result = tools["ue_apply_blueprint_variable_create_experimental"](
            preview_result=_preview(),
            confirmation=confirmation_for_plan(PLAN_ID),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(live.calls[0][0], "editor.applyBlueprintVariableCreateExperimental")
        self.assertEqual(
            live.calls[0][1]["confirmation"],
            confirmation_for_plan(PLAN_ID),
        )

        live.result = _save()
        result = tools["ue_save_blueprint_variable_create_experimental"](
            apply_result=_apply(),
            confirmation=save_confirmation_for_plan(PLAN_ID),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(live.calls[1][0], "editor.saveBlueprintVariableCreateExperimental")
        self.assertEqual(
            live.calls[1][1],
            {
                "assetPath": ASSET,
                "planId": PLAN_ID,
                "expectedVariablesFingerprint": AFTER_FINGERPRINT,
                "expectedPackageRevision": PACKAGE_REVISION,
                "expectedVariableCount": 3,
                "confirmation": save_confirmation_for_plan(PLAN_ID),
            },
        )

    def test_bridge_error_is_returned_as_failure(self) -> None:
        tools, live = self._tools()

        class _Rejecting(_FakeLiveEditor):
            def call_method(self, method, params=None, *, timeout_seconds=None):
                from ue_agent_kit.editor_bridge import LiveEditorError

                raise LiveEditorError(
                    "blueprint-variable-duplicate-name",
                    "The Blueprint already declares Counter.",
                )

        server = _FakeServer()
        register_variable_write_experimental_tools(
            server=server,
            live_editor_service=_Rejecting(),
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        result = server.tools["ue_preview_blueprint_variable_create_experimental"](
            asset_path=ASSET,
            variables=[_variable()],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "blueprint-variable-duplicate-name")


if __name__ == "__main__":
    unittest.main()

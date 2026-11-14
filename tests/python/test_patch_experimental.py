from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.experimental_patch import (  # noqa: E402
    APPLY_CAPABILITY,
    APPLY_CONFIRMATION_PREFIX,
    APPLY_METHOD,
    APPLY_SCHEMA_VERSION,
    APPLY_TOOL_NAME,
    MAX_REQUEST_BYTES,
    PREVIEW_CAPABILITY,
    PREVIEW_METHOD,
    PREVIEW_SCHEMA_VERSION,
    PREVIEW_TOOL_NAME,
    SAVE_CAPABILITY,
    SAVE_METHOD,
    SAVE_TOOL_NAME,
    PatchError,
    build_apply_parameters,
    build_save_parameters,
    confirmation_for_plan,
    describe_rollback,
    save_confirmation_for_plan,
    validate_apply_result,
    validate_preview_parameters,
    validate_preview_result,
)
from ue_agent_kit.mcp_patch_experimental import (  # noqa: E402
    register_patch_experimental_tools,
)

PLAN_ID = "sha256:" + "a" * 64
ASSET = "/Game/UEAgentKitTests/BP_AtomicPatch_Target.BP_AtomicPatch_Target"
GRAPH = "11111111-2222-4333-8444-555555555555"
NODE_A = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PIN_A = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
NODE_B = "cccccccc-dddd-4eee-8fff-000000000000"
PIN_B = "dddddddd-eeee-4fff-8000-111111111111"
STATE_FINGERPRINT = "sha256:" + "b" * 64
AFTER_STATE_FINGERPRINT = "sha256:" + "c" * 64
GRAPH_FINGERPRINT = "sha1:" + "d" * 40
PACKAGE_REVISION = "sha256:" + "e" * 64


def _operation(**overrides: Any) -> dict[str, Any]:
    item = {
        "opId": "p1",
        "kind": "setPinDefault",
        "nodeGuid": NODE_A,
        "pinName": "Condition",
        "pinGuid": PIN_A,
        "type": "bool",
        "value": "true",
    }
    item.update(overrides)
    return item


def _second_operation() -> dict[str, Any]:
    return {
        "opId": "p2",
        "kind": "setPinDefault",
        "nodeGuid": NODE_B,
        "pinName": "Condition",
        "pinGuid": PIN_B,
        "type": "bool",
        "value": "false",
    }


def _preview(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
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
        "stateFingerprint": STATE_FINGERPRINT,
        "operationCount": 1,
        "nodeCount": 4,
        "linkCount": 3,
        "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
        "migratedKinds": ["setPinDefault"],
        "operations": [
            {
                "opId": "p1",
                "kind": "setPinDefault",
                "nodeGuid": NODE_A,
                "pinName": "Condition",
                "pinGuid": PIN_A,
                "type": "bool",
                "beforeValue": "false",
                "afterValue": "true",
                "status": "validated",
            }
        ],
    }
    result.update(overrides)
    return result


def _apply(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": APPLY_SCHEMA_VERSION,
        "experimental": True,
        "applied": True,
        "writeOccurred": True,
        "singleTransaction": True,
        "transactionCount": 1,
        "undoVerified": True,
        "redoVerified": True,
        "saved": False,
        "compiled": False,
        "autoSave": False,
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "confirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
        "requiredSaveConfirmation": save_confirmation_for_plan(PLAN_ID),
        "graphFingerprintAfter": GRAPH_FINGERPRINT,
        "stateFingerprintAfter": AFTER_STATE_FINGERPRINT,
        "nodeCountBefore": 4,
        "nodeCountAfter": 4,
        "linkCountBefore": 3,
        "linkCountAfter": 3,
        "operationCount": 1,
        "appliedOperationCount": 1,
        "results": [
            {
                "opId": "p1",
                "kind": "setPinDefault",
                "nodeGuid": NODE_A,
                "pinName": "Condition",
                "pinGuid": PIN_A,
                "type": "bool",
                "beforeValue": "false",
                "afterValue": "true",
                "status": "applied",
                "rolledBack": False,
            }
        ],
    }
    result.update(overrides)
    return result


def _save(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "action": "save-experimental-blueprint-patch",
        "saved": True,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "planId": PLAN_ID,
        "assetSha256": "sha256:" + "f" * 64,
        "packageDirty": False,
        "independentReloadVerified": False,
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
        self.results: dict[str, Any] = {
            PREVIEW_METHOD: _preview(),
            APPLY_METHOD: _apply(),
            SAVE_METHOD: _save(),
        }
        self.errors: dict[str, BaseException] = {}

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
        if method in self.errors:
            raise self.errors[method]
        return copy.deepcopy(self.results[method])


def _annotations(**kwargs: Any) -> dict[str, Any]:
    return kwargs


def _broken_error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "error"), "message": str(exc)},
    }


class PatchContractTests(unittest.TestCase):
    def test_single_operation_normalizes_like_the_pin_default_writer(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_operation()],
        )
        self.assertEqual(
            request["operations"],
            [
                {
                    "opId": "p1",
                    "kind": "setPinDefault",
                    "nodeGuid": NODE_A,
                    "pinName": "Condition",
                    "pinGuid": PIN_A,
                    "type": "bool",
                    "value": "true",
                }
            ],
        )
        # validate_pin_default_parameters is the single source of truth, so an
        # encoding the single writer refuses is refused here with the same code.
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(value="TRUE")],
            )
        self.assertEqual(raised.exception.code, "graph-pin-default-value-invalid")

    def test_operation_order_is_preserved(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_second_operation(), _operation()],
        )
        self.assertEqual([item["opId"] for item in request["operations"]], ["p2", "p1"])
        self.assertEqual(request["operations"][0]["value"], "false")
        self.assertEqual(request["operations"][1]["value"], "true")

    def test_unmigrated_and_unknown_kinds_are_refused(self) -> None:
        for kind, code in (
            ("createVariable", "graph-patch-kind-not-migrated"),
            ("createNode", "graph-patch-kind-not-migrated"),
            ("connect", "graph-patch-kind-not-migrated"),
            ("rewirePin", "graph-patch-kind-not-migrated"),
            ("removeNode", "graph-patch-kind-not-migrated"),
            ("moveNode", "graph-patch-kind-not-allowed"),
            ("setDefault", "graph-patch-kind-not-allowed"),
        ):
            with self.subTest(kind=kind):
                with self.assertRaises(PatchError) as raised:
                    validate_preview_parameters(
                        asset_path=ASSET,
                        graph_guid=GRAPH,
                        operations=[_operation(kind=kind)],
                    )
                self.assertEqual(raised.exception.code, code)

    def test_mixed_migrated_and_unmigrated_patch_is_refused(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(), _operation(opId="p2", kind="createNode")],
            )
        self.assertEqual(raised.exception.code, "graph-patch-kind-not-migrated")

    def test_duplicate_ids_and_targets_are_refused(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(), _second_operation() | {"opId": "p1"}],
            )
        self.assertEqual(raised.exception.code, "graph-patch-duplicate-op-id")

        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(), _operation(opId="p2", value="false")],
            )
        self.assertEqual(raised.exception.code, "graph-patch-duplicate-target")

    def test_bounds_and_identity_are_enforced(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(opId=f"p{index}") for index in range(17)],
            )
        self.assertEqual(raised.exception.code, "graph-patch-invalid-operations")

        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(asset_path=ASSET, graph_guid=GRAPH, operations=[])
        self.assertEqual(raised.exception.code, "graph-patch-invalid-operations")

        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(opId="bad id")],
            )
        self.assertEqual(raised.exception.code, "graph-patch-invalid-parameters")

        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid="not-a-guid",
                operations=[_operation()],
            )
        self.assertEqual(raised.exception.code, "graph-patch-invalid-identifier")

    def test_unknown_operation_fields_are_refused(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(confirmation="ignored")],
            )
        self.assertEqual(raised.exception.code, "graph-patch-unknown-parameter")

    def test_pin_default_encoding_rules_are_inherited(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(value="True")],
            )
        self.assertEqual(raised.exception.code, "graph-pin-default-value-invalid")

        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation(type="object")],
            )
        self.assertEqual(raised.exception.code, "graph-pin-default-type-not-allowed")

    def test_preview_tampering_fails_closed(self) -> None:
        validate_preview_result(_preview())

        for mutation, label in (
            ({"planId": "sha256:" + "f" * 63}, "planId"),
            ({"operationCount": 2}, "operationCount"),
            ({"migratedKinds": ["setPinDefault", "createNode"]}, "migratedKinds"),
            ({"requiredConfirmation": APPLY_CONFIRMATION_PREFIX + "other"}, "confirmation"),
            ({"stateFingerprint": "sha256:short"}, "stateFingerprint"),
            ({"graphFingerprint": "sha1:short"}, "graphFingerprint"),
            ({"operations": []}, "operations"),
        ):
            with self.subTest(label=label):
                tampered = copy.deepcopy(_preview())
                tampered.update(mutation)
                with self.assertRaises(PatchError) as raised:
                    validate_preview_result(tampered)
                self.assertEqual(raised.exception.code, "graph-patch-preview-invalid")

    def test_apply_parameters_are_derived_from_fields_only(self) -> None:
        params = build_apply_parameters(
            plan_id=PLAN_ID,
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_operation()],
        )
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["confirmation"], confirmation_for_plan(PLAN_ID))
        self.assertEqual(params["operations"][0]["value"], "true")

        with self.assertRaises(PatchError) as raised:
            build_apply_parameters(
                plan_id=PLAN_ID,
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation()],
                confirmation="SET EXPERIMENTAL BLUEPRINT PATCH wrong",
            )
        self.assertEqual(raised.exception.code, "graph-patch-confirmation-required")

        with self.assertRaises(PatchError) as raised:
            build_apply_parameters(
                plan_id="sha256:" + "a" * 63,
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[_operation()],
            )
        self.assertEqual(raised.exception.code, "graph-patch-invalid-parameters")

    def test_apply_result_and_save_parameters_bind_the_same_receipt(self) -> None:
        validate_apply_result(_apply())
        params = build_save_parameters(_apply())
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["expectedGraphFingerprint"], GRAPH_FINGERPRINT)
        self.assertEqual(params["expectedStateFingerprint"], AFTER_STATE_FINGERPRINT)
        self.assertEqual(params["expectedNodeCount"], 4)
        self.assertEqual(params["expectedLinkCount"], 3)
        self.assertEqual(params["confirmation"], save_confirmation_for_plan(PLAN_ID))

        with self.assertRaises(PatchError) as raised:
            build_save_parameters(_apply(), "SAVE EXPERIMENTAL BLUEPRINT PATCH wrong")
        self.assertEqual(raised.exception.code, "graph-patch-confirmation-required")

        for mutation in (
            {"applied": False},
            {"saved": True},
            {"singleTransaction": False},
            {"transactionCount": 2},
            {"results": []},
            {"nodeCountAfter": -1},
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaises(PatchError) as raised:
                    validate_apply_result(_apply(**mutation))
                self.assertEqual(raised.exception.code, "graph-patch-apply-invalid")

    def test_rollback_details_are_extracted_verbatim(self) -> None:
        details = {
            "writeOccurred": False,
            "transactionOpened": True,
            "rolledBack": True,
            "results": [
                {"opId": "p1", "kind": "setPinDefault", "status": "applied", "rolledBack": True},
                {
                    "opId": "p2",
                    "kind": "setPinDefault",
                    "status": "failed",
                    "errorCode": "graph-pin-default-pin-connected",
                    "errorMessage": "Pin became linked.",
                    "rolledBack": False,
                },
            ],
        }
        described = describe_rollback(details)
        self.assertFalse(described["writeOccurred"])
        self.assertTrue(described["rolledBack"])
        self.assertEqual([item["status"] for item in described["results"]], ["applied", "failed"])
        self.assertEqual(
            described["results"][1]["errorCode"],
            "graph-pin-default-pin-connected",
        )
        self.assertEqual(describe_rollback(None), {})
        self.assertEqual(describe_rollback({"results": "no"}), {})


class PatchToolTests(unittest.TestCase):
    def _register(self, live_editor: _FakeLiveEditor, *, error_response: Any = None) -> _FakeServer:
        server = _FakeServer()
        kwargs: dict[str, Any] = {
            "server": server,
            "live_editor_service": live_editor,
            "tool_annotations_type": _annotations,
        }
        if error_response is not None:
            kwargs["error_response"] = error_response
        register_patch_experimental_tools(**kwargs)
        return server

    def test_tools_are_registered_with_exact_names(self) -> None:
        server = self._register(_FakeLiveEditor())
        self.assertEqual(
            set(server.tools),
            {PREVIEW_TOOL_NAME, APPLY_TOOL_NAME, SAVE_TOOL_NAME},
        )

    def test_preview_apply_save_happy_path(self) -> None:
        live_editor = _FakeLiveEditor()
        server = self._register(live_editor)

        preview = server.tools[PREVIEW_TOOL_NAME](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_operation()],
        )
        self.assertTrue(preview["ok"])
        self.assertTrue(preview["readOnly"])
        self.assertEqual(preview["result"]["planId"], PLAN_ID)

        applied = server.tools[APPLY_TOOL_NAME](
            plan_id=preview["result"]["planId"],
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_operation()],
        )
        self.assertTrue(applied["ok"])
        self.assertFalse(applied["saved"])
        self.assertEqual(applied["result"]["transactionCount"], 1)

        saved = server.tools[SAVE_TOOL_NAME](apply_result=applied["result"])
        self.assertTrue(saved["ok"])
        self.assertTrue(saved["result"]["saved"])

        self.assertEqual(
            [call[0] for call in live_editor.calls],
            [PREVIEW_METHOD, APPLY_METHOD, SAVE_METHOD],
        )

    def test_apply_never_sends_an_unvalidatable_request(self) -> None:
        live_editor = _FakeLiveEditor()
        server = self._register(live_editor, error_response=_broken_error_response)

        response = server.tools[APPLY_TOOL_NAME](
            plan_id=PLAN_ID,
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_operation(kind="removeNode")],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "graph-patch-kind-not-migrated")
        self.assertEqual(live_editor.calls, [])

    def test_failed_apply_reports_per_operation_results(self) -> None:
        live_editor = _FakeLiveEditor()
        live_editor.errors[APPLY_METHOD] = LiveEditorError(
            "graph-patch-apply-fault-injected",
            "Injected test fault on operation p2.",
            details={
                "schemaVersion": APPLY_SCHEMA_VERSION,
                "applied": False,
                "writeOccurred": False,
                "transactionOpened": True,
                "rolledBack": True,
                "operationCount": 2,
                "results": [
                    {
                        "opId": "p1",
                        "kind": "setPinDefault",
                        "status": "applied",
                        "errorCode": "",
                        "errorMessage": "",
                        "rolledBack": True,
                    },
                    {
                        "opId": "p2",
                        "kind": "setPinDefault",
                        "status": "failed",
                        "errorCode": "graph-patch-apply-fault-injected",
                        "errorMessage": "Injected test fault on operation p2.",
                        "rolledBack": False,
                    },
                ],
            },
        )
        server = self._register(live_editor)

        response = server.tools[APPLY_TOOL_NAME](
            plan_id=PLAN_ID,
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_operation(), _second_operation()],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "graph-patch-apply-fault-injected")
        self.assertFalse(response["writeOccurred"])
        self.assertTrue(response["rolledBack"])
        self.assertEqual(len(response["results"]), 2)
        self.assertEqual(response["results"][0]["status"], "applied")
        self.assertTrue(response["results"][0]["rolledBack"])
        self.assertEqual(response["results"][1]["status"], "failed")
        self.assertEqual(
            response["results"][1]["errorCode"],
            "graph-patch-apply-fault-injected",
        )

    def test_missing_capability_is_refused_before_any_call(self) -> None:
        live_editor = _FakeLiveEditor(capabilities=[PREVIEW_CAPABILITY])
        server = self._register(live_editor, error_response=_broken_error_response)

        response = server.tools[APPLY_TOOL_NAME](
            plan_id=PLAN_ID,
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[_operation()],
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "live-editor-capability-missing")
        self.assertEqual(live_editor.calls, [])

    def test_largest_legal_patch_stays_below_the_bridge_boundary(self) -> None:
        # 16 operations with a 1024-character string default each is the largest
        # patch this contract can produce; it must still fit the 60 KiB boundary,
        # so the size guard is a backstop for future kinds rather than a bound on
        # what a caller can ask for today.
        operations = [
            {
                "opId": f"p{index}",
                "kind": "setPinDefault",
                "nodeGuid": f"aaaaaaaa-bbbb-4ccc-8ddd-{index:012d}",
                "pinName": f"Pin{index}",
                "pinGuid": f"bbbbbbbb-cccc-4ddd-8eee-{index:012d}",
                "type": "string",
                "value": "x" * 1024,
            }
            for index in range(16)
        ]
        params = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=operations,
        )
        encoded = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLess(len(encoded), MAX_REQUEST_BYTES)


if __name__ == "__main__":
    unittest.main()

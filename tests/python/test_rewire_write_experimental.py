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

from ue_agent_kit.experimental_rewire_write import (  # noqa: E402
    APPLY_CAPABILITY,
    APPLY_CONFIRMATION_PREFIX,
    APPLY_SCHEMA_VERSION,
    PREVIEW_CAPABILITY,
    PREVIEW_SCHEMA_VERSION,
    SAVE_CAPABILITY,
    SAVE_CONFIRMATION_PREFIX,
    SAVE_SCHEMA_VERSION,
    RewireWriteError,
    build_apply_parameters,
    build_save_parameters,
    confirmation_for_plan,
    save_confirmation_for_plan,
    validate_apply_result,
    validate_preview_parameters,
    validate_preview_result,
    validate_save_result,
)
from ue_agent_kit.mcp_rewire_write_experimental import (  # noqa: E402
    register_rewire_write_experimental_tools,
)

PLAN_ID = "sha256:" + "a" * 64
EDGE_BEFORE = "sha256:" + "b" * 64
EDGE_AFTER = "sha256:" + "c" * 64
UNTOUCHED = "sha256:" + "d" * 64
PIN_IDENTITY = "sha256:" + "e" * 64
REVISION = "sha256:" + "f" * 64
ASSET_SHA = "sha256:" + "9" * 64
FINGERPRINT = "sha1:" + "0" * 40
ASSET = "/Game/UEAgentKitBlueprintRewire/BP_RewireExpansion.BP_RewireExpansion"
GRAPH = "00000000-1111-4222-8333-444444444444"
OLD_NODE = "11111111-2222-4333-8444-555555555555"
OLD_PIN = "12222222-2222-4333-8444-555555555555"
TARGET_NODE = "33333333-2222-4333-8444-555555555555"
TARGET_PIN = "34444444-2222-4333-8444-555555555555"
NEW_NODE = "55555555-2222-4333-8444-555555555555"
NEW_PIN = "56666666-2222-4333-8444-555555555555"


def _edge(**overrides: Any) -> dict[str, str]:
    item = {
        "fromNodeGuid": OLD_NODE,
        "fromPinName": "ReturnValue",
        "fromPinGuid": OLD_PIN,
        "toNodeGuid": TARGET_NODE,
        "toPinName": "Condition",
        "toPinGuid": TARGET_PIN,
        "kind": "bool",
    }
    item.update(overrides)
    return item


def _added_edge(**overrides: Any) -> dict[str, str]:
    item = _edge(
        fromNodeGuid=NEW_NODE,
        fromPinName="ReturnValue",
        fromPinGuid=NEW_PIN,
    )
    item.update(overrides)
    return item


def _pin(node: str, pin: str, name: str, direction: str, kind: str = "bool") -> dict[str, str]:
    return {
        "nodeGuid": node,
        "pinName": name,
        "pinGuid": pin,
        "direction": direction,
        "kind": kind,
    }


def _preview(**overrides: Any) -> dict[str, Any]:
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
        "operation": "rewire",
        "packageRevision": REVISION,
        "graphFingerprint": FINGERPRINT,
        "edgeSetHashBefore": EDGE_BEFORE,
        "edgeSetHashAfter": EDGE_AFTER,
        "untouchedEdgesHash": UNTOUCHED,
        "pinIdentityHash": PIN_IDENTITY,
        "pinKind": "bool",
        "nodeCountBefore": 5,
        "nodeCountAfter": 5,
        "linkCountBefore": 4,
        "linkCountAfter": 4,
        "untouchedEdgeCount": 3,
        "removedEdges": [_edge()],
        "addedEdges": [_added_edge()],
        "oldSourcePin": _pin(OLD_NODE, OLD_PIN, "ReturnValue", "output"),
        "targetPin": _pin(TARGET_NODE, TARGET_PIN, "Condition", "input"),
        "newSourcePin": _pin(NEW_NODE, NEW_PIN, "ReturnValue", "output"),
        "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
    }
    result.update(overrides)
    return result


def _disconnect_preview(**overrides: Any) -> dict[str, Any]:
    result = _preview(
        operation="disconnect",
        linkCountAfter=3,
        addedEdges=[],
        newSourcePin=None,
        edgeSetHashAfter="sha256:" + "8" * 64,
    )
    result.update(overrides)
    return result


def _apply(**overrides: Any) -> dict[str, Any]:
    result = {
        "schemaVersion": APPLY_SCHEMA_VERSION,
        "experimental": True,
        "applied": True,
        "undoVerified": True,
        "redoVerified": True,
        "saved": False,
        "compiled": False,
        "skeletonRegenerated": False,
        "packageDirty": True,
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "operation": "rewire",
        "packageRevision": REVISION,
        "graphFingerprint": FINGERPRINT,
        "edgeSetHashBefore": EDGE_BEFORE,
        "edgeSetHashAfter": EDGE_AFTER,
        "pinIdentityHash": PIN_IDENTITY,
        "pinKind": "bool",
        "nodeCount": 5,
        "linkCountBefore": 4,
        "linkCountAfter": 4,
        "linkCount": 4,
        "removedEdges": [_edge()],
        "addedEdges": [_added_edge()],
        "oldSourcePin": _pin(OLD_NODE, OLD_PIN, "ReturnValue", "output"),
        "targetPin": _pin(TARGET_NODE, TARGET_PIN, "Condition", "input"),
        "newSourcePin": _pin(NEW_NODE, NEW_PIN, "ReturnValue", "output"),
        "requiredSaveConfirmation": SAVE_CONFIRMATION_PREFIX + PLAN_ID,
    }
    result.update(overrides)
    return result


def _save(**overrides: Any) -> dict[str, Any]:
    result = {
        "schemaVersion": SAVE_SCHEMA_VERSION,
        "experimental": True,
        "saved": True,
        "readbackVerified": True,
        "packageDirty": False,
        "compiled": False,
        "skeletonRegenerated": False,
        "independentReloadVerified": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "operation": "rewire",
        "packageRevision": REVISION,
        "assetSha256": ASSET_SHA,
        "assetSizeBytes": 4096,
        "edgeSetHashBefore": EDGE_BEFORE,
        "edgeSetHashAfter": EDGE_AFTER,
        "pinIdentityHash": PIN_IDENTITY,
        "nodeCount": 5,
        "linkCount": 4,
        "linkCountBefore": 4,
        "removedEdges": [_edge()],
        "addedEdges": [_added_edge()],
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


class RewireContractTests(unittest.TestCase):
    def _rewire_request(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "asset_path": ASSET,
            "graph_guid": GRAPH,
            "operation": "rewire",
            "target_node_guid": TARGET_NODE,
            "target_pin_name": "Condition",
            "target_pin_guid": TARGET_PIN,
            "old_source_node_guid": OLD_NODE,
            "old_source_pin_name": "ReturnValue",
            "old_source_pin_guid": OLD_PIN,
            "expected_edge_set_hash": EDGE_BEFORE,
            "new_source_node_guid": NEW_NODE,
            "new_source_pin_name": "ReturnValue",
            "new_source_pin_guid": NEW_PIN,
        }
        values.update(overrides)
        return validate_preview_parameters(**values)

    def test_rewire_and_disconnect_requests_normalize(self) -> None:
        rewire = self._rewire_request()
        self.assertEqual(rewire["operation"], "rewire")
        self.assertEqual(rewire["newSourcePinGuid"], NEW_PIN)
        self.assertEqual(rewire["expectedEdgeSetHash"], EDGE_BEFORE)

        disconnect = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            operation="disconnect",
            target_node_guid=TARGET_NODE,
            target_pin_name="Condition",
            target_pin_guid=TARGET_PIN,
            old_source_node_guid=OLD_NODE,
            old_source_pin_name="ReturnValue",
            old_source_pin_guid=OLD_PIN,
            expected_edge_set_hash=EDGE_BEFORE,
        )
        self.assertEqual(disconnect["operation"], "disconnect")
        self.assertNotIn("newSourceNodeGuid", disconnect)

    def test_invalid_operation_and_identifier_shapes_are_rejected(self) -> None:
        cases = [
            ({"operation": "move"}, "blueprint-rewire-invalid-parameters"),
            ({"operation": ""}, "blueprint-rewire-invalid-parameters"),
            ({"graph_guid": "AAAAAAAA-1111-4222-8333-444444444444"}, "blueprint-rewire-invalid-identifier"),
            ({"graph_guid": "not-a-guid"}, "blueprint-rewire-invalid-identifier"),
            ({"target_pin_guid": TARGET_PIN[:-1]}, "blueprint-rewire-invalid-identifier"),
            ({"expected_edge_set_hash": "sha256:abc"}, "blueprint-rewire-invalid-identifier"),
            ({"expected_edge_set_hash": EDGE_BEFORE.upper()}, "blueprint-rewire-invalid-identifier"),
            ({"new_source_pin_guid": "not-a-guid"}, "blueprint-rewire-invalid-identifier"),
        ]
        for overrides, code in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(RewireWriteError) as raised:
                    self._rewire_request(**overrides)
                self.assertEqual(raised.exception.code, code)

    def test_disconnect_and_rewire_pin_fields_are_strict(self) -> None:
        with self.assertRaises(RewireWriteError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operation="disconnect",
                target_node_guid=TARGET_NODE,
                target_pin_name="Condition",
                target_pin_guid=TARGET_PIN,
                old_source_node_guid=OLD_NODE,
                old_source_pin_name="ReturnValue",
                old_source_pin_guid=OLD_PIN,
                expected_edge_set_hash=EDGE_BEFORE,
                new_source_node_guid=NEW_NODE,
            )
        self.assertEqual(raised.exception.code, "blueprint-rewire-invalid-parameters")

        for field in ("new_source_node_guid", "new_source_pin_name", "new_source_pin_guid"):
            with self.subTest(field=field):
                with self.assertRaises(RewireWriteError) as raised:
                    self._rewire_request(**{field: None})
                self.assertEqual(raised.exception.code, "blueprint-rewire-invalid-parameters")

    def test_pin_names_are_bounded(self) -> None:
        cases = [
            "",
            "x" * 129,
            "Bad|Name",
            "Bad>Name",
            "Bad\nName",
            "Bad\tName",
        ]
        for name in cases:
            with self.subTest(name=name):
                with self.assertRaises(RewireWriteError) as raised:
                    self._rewire_request(target_pin_name=name)
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-rewire-invalid-parameters",
                )

    def test_preview_result_accepts_rewire_and_disconnect(self) -> None:
        rewire = validate_preview_result(_preview())
        self.assertEqual(rewire["planId"], PLAN_ID)
        self.assertEqual(len(rewire["addedEdges"]), 1)
        disconnect = validate_preview_result(_disconnect_preview())
        self.assertEqual(disconnect["addedEdges"], [])
        self.assertIsNone(disconnect["newSourcePin"])

    def test_preview_tampering_fails_closed(self) -> None:
        for field, value in (
            ("schemaVersion", "ueak.blueprint-rewire-preview/2.0"),
            ("readOnly", False),
            ("wroteBlueprint", True),
            ("savedPackage", True),
            ("compiled", True),
            ("edgeSetHashAfter", EDGE_BEFORE),
            ("linkCountAfter", 5),
            ("untouchedEdgeCount", 4),
            ("nodeCountAfter", 6),
            ("requiredConfirmation", "REWIRE"),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_preview())
                tampered[field] = value
                with self.assertRaises(RewireWriteError) as raised:
                    validate_preview_result(tampered)
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-rewire-preview-invalid",
                )

        tampered = copy.deepcopy(_preview())
        tampered["removedEdges"][0]["toPinGuid"] = NEW_PIN
        with self.assertRaises(RewireWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-rewire-preview-invalid")

        tampered = copy.deepcopy(_preview())
        tampered["addedEdges"][0]["fromPinGuid"] = OLD_PIN
        with self.assertRaises(RewireWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-rewire-preview-invalid")

        tampered = copy.deepcopy(_preview())
        tampered["addedEdges"] = []
        with self.assertRaises(RewireWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-rewire-preview-invalid")

        tampered = copy.deepcopy(_disconnect_preview())
        tampered["newSourcePin"] = _pin(NEW_NODE, NEW_PIN, "ReturnValue", "output")
        with self.assertRaises(RewireWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-rewire-preview-invalid")

    def test_apply_parameters_require_exact_confirmation(self) -> None:
        expected = confirmation_for_plan(PLAN_ID)
        params = build_apply_parameters(_preview(), expected)
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["expectedEdgeSetHash"], EDGE_BEFORE)
        self.assertEqual(params["newSourcePinGuid"], NEW_PIN)
        self.assertEqual(params["confirmation"], expected)

        disconnect = build_apply_parameters(
            _disconnect_preview(),
            confirmation_for_plan(PLAN_ID),
        )
        self.assertNotIn("newSourceNodeGuid", disconnect)

        for confirmation in ("", expected + " ", expected.lower(), "REWIRE"):
            with self.subTest(confirmation=confirmation):
                with self.assertRaises(RewireWriteError) as raised:
                    build_apply_parameters(_preview(), confirmation)
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-rewire-confirmation-required",
                )

    def test_apply_result_tampering_fails_closed(self) -> None:
        validate_apply_result(_apply(), _preview())

        for field, value in (
            ("applied", False),
            ("undoVerified", False),
            ("redoVerified", False),
            ("saved", True),
            ("compiled", True),
            ("skeletonRegenerated", True),
            ("packageDirty", False),
            ("edgeSetHashAfter", EDGE_BEFORE),
            ("planId", "sha256:" + "1" * 64),
            ("linkCount", 5),
            ("nodeCount", 6),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_apply())
                tampered[field] = value
                with self.assertRaises(RewireWriteError) as raised:
                    validate_apply_result(tampered, _preview())
                self.assertEqual(raised.exception.code, "blueprint-rewire-apply-invalid")

        tampered = copy.deepcopy(_apply())
        tampered["removedEdges"][0]["fromPinGuid"] = NEW_PIN
        with self.assertRaises(RewireWriteError) as raised:
            validate_apply_result(tampered, _preview())
        self.assertEqual(raised.exception.code, "blueprint-rewire-apply-invalid")

        tampered = copy.deepcopy(_apply())
        tampered["addedEdges"] = []
        with self.assertRaises(RewireWriteError) as raised:
            validate_apply_result(tampered, _preview())
        self.assertEqual(raised.exception.code, "blueprint-rewire-apply-invalid")

    def test_save_parameters_are_bound_to_apply_result(self) -> None:
        expected = save_confirmation_for_plan(PLAN_ID)
        params = build_save_parameters(_apply(), expected)
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["expectedEdgeSetHashAfter"], EDGE_AFTER)
        self.assertEqual(params["expectedPackageRevision"], REVISION)
        self.assertEqual(params["expectedNodeCount"], 5)
        self.assertEqual(params["expectedLinkCount"], 4)

        with self.assertRaises(RewireWriteError) as raised:
            build_save_parameters(_apply(), "SAVE")
        self.assertEqual(raised.exception.code, "blueprint-rewire-confirmation-required")

        fabricated = copy.deepcopy(_apply())
        fabricated["edgeSetHashAfter"] = "sha256:" + "7" * 64
        params = build_save_parameters(fabricated, expected)
        self.assertEqual(params["expectedEdgeSetHashAfter"], "sha256:" + "7" * 64)

    def test_save_result_requires_independent_reload_pending(self) -> None:
        validate_save_result(_save(), _apply())

        for field, value in (
            ("independentReloadVerified", True),
            ("packageDirty", True),
            ("compiled", True),
            ("skeletonRegenerated", True),
            ("saved", False),
            ("readbackVerified", False),
            ("requiredAcceptanceEvidence", "same-process"),
            ("edgeSetHashAfter", EDGE_BEFORE),
            ("nodeCount", 6),
            ("linkCount", 3),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_save())
                tampered[field] = value
                with self.assertRaises(RewireWriteError) as raised:
                    validate_save_result(tampered, _apply())
                self.assertEqual(raised.exception.code, "blueprint-rewire-save-invalid")


class RewireMcpToolTests(unittest.TestCase):
    def _tools(self, capabilities: list[str] | None = None):
        server = _FakeServer()
        live = _FakeLiveEditor(capabilities)
        register_rewire_write_experimental_tools(
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
                "ue_preview_blueprint_rewire_experimental",
                "ue_apply_blueprint_rewire_experimental",
                "ue_save_blueprint_rewire_experimental",
            },
        )

    def test_preview_dispatches_exact_parameters(self) -> None:
        tools, live = self._tools()
        result = tools["ue_preview_blueprint_rewire_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operation="rewire",
            target_node_guid=TARGET_NODE,
            target_pin_name="Condition",
            target_pin_guid=TARGET_PIN,
            old_source_node_guid=OLD_NODE,
            old_source_pin_name="ReturnValue",
            old_source_pin_guid=OLD_PIN,
            expected_edge_set_hash=EDGE_BEFORE,
            new_source_node_guid=NEW_NODE,
            new_source_pin_name="ReturnValue",
            new_source_pin_guid=NEW_PIN,
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["readOnly"])
        self.assertFalse(result["productionDefault"])
        self.assertEqual(live.calls[0][0], "editor.previewBlueprintRewireExperimental")
        self.assertEqual(live.calls[0][1]["expectedEdgeSetHash"], EDGE_BEFORE)
        self.assertEqual(live.calls[0][1]["targetPinGuid"], TARGET_PIN)
        self.assertEqual(live.calls[0][2], 30.0)

    def test_disconnect_preview_omits_new_source(self) -> None:
        tools, live = self._tools()
        tools["ue_preview_blueprint_rewire_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operation="disconnect",
            target_node_guid=TARGET_NODE,
            target_pin_name="Condition",
            target_pin_guid=TARGET_PIN,
            old_source_node_guid=OLD_NODE,
            old_source_pin_name="ReturnValue",
            old_source_pin_guid=OLD_PIN,
            expected_edge_set_hash=EDGE_BEFORE,
        )
        params = live.calls[0][1]
        self.assertNotIn("newSourceNodeGuid", params)
        self.assertNotIn("newSourcePinGuid", params)

    def test_missing_capability_fails_closed(self) -> None:
        tools, live = self._tools(capabilities=["editor.status"])
        result = tools["ue_preview_blueprint_rewire_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operation="disconnect",
            target_node_guid=TARGET_NODE,
            target_pin_name="Condition",
            target_pin_guid=TARGET_PIN,
            old_source_node_guid=OLD_NODE,
            old_source_pin_name="ReturnValue",
            old_source_pin_guid=OLD_PIN,
            expected_edge_set_hash=EDGE_BEFORE,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "live-editor-capability-missing")
        self.assertEqual(live.calls, [])

    def test_apply_requires_exact_confirmation_before_bridge(self) -> None:
        tools, live = self._tools()
        result = tools["ue_apply_blueprint_rewire_experimental"](
            preview_result=_preview(),
            confirmation="REWIRE",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "blueprint-rewire-confirmation-required")
        self.assertEqual(live.calls, [])

    def test_apply_and_save_dispatch_exact_parameters(self) -> None:
        tools, live = self._tools()
        live.result = _apply()
        result = tools["ue_apply_blueprint_rewire_experimental"](
            preview_result=_preview(),
            confirmation=confirmation_for_plan(PLAN_ID),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(live.calls[0][0], "editor.applyBlueprintRewireExperimental")
        self.assertEqual(live.calls[0][1]["planId"], PLAN_ID)
        self.assertEqual(live.calls[0][1]["expectedEdgeSetHash"], EDGE_BEFORE)

        live.result = _save()
        result = tools["ue_save_blueprint_rewire_experimental"](
            apply_result=_apply(),
            confirmation=save_confirmation_for_plan(PLAN_ID),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(live.calls[1][0], "editor.saveBlueprintRewireExperimental")
        self.assertEqual(
            live.calls[1][1],
            {
                "assetPath": ASSET,
                "graphGuid": GRAPH,
                "planId": PLAN_ID,
                "expectedEdgeSetHashAfter": EDGE_AFTER,
                "expectedPackageRevision": REVISION,
                "expectedNodeCount": 5,
                "expectedLinkCount": 4,
                "confirmation": save_confirmation_for_plan(PLAN_ID),
            },
        )

    def test_bridge_error_is_returned_as_failure(self) -> None:
        class _Rejecting(_FakeLiveEditor):
            def call_method(self, method, params=None, *, timeout_seconds=None):
                from ue_agent_kit.editor_bridge import LiveEditorError

                raise LiveEditorError(
                    "graph-rewire-schema-would-break-other-links",
                    "The K2 schema would break connections on the new source pin.",
                )

        server = _FakeServer()
        register_rewire_write_experimental_tools(
            server=server,
            live_editor_service=_Rejecting(),
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        result = server.tools["ue_preview_blueprint_rewire_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operation="disconnect",
            target_node_guid=TARGET_NODE,
            target_pin_name="Condition",
            target_pin_guid=TARGET_PIN,
            old_source_node_guid=OLD_NODE,
            old_source_pin_name="ReturnValue",
            old_source_pin_guid=OLD_PIN,
            expected_edge_set_hash=EDGE_BEFORE,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "graph-rewire-schema-would-break-other-links",
        )


if __name__ == "__main__":
    unittest.main()

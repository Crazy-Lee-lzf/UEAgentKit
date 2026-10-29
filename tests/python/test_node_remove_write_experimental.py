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

from ue_agent_kit.experimental_node_remove_write import (  # noqa: E402
    APPLY_CAPABILITY,
    APPLY_CONFIRMATION_PREFIX,
    APPLY_SCHEMA_VERSION,
    PREVIEW_CAPABILITY,
    PREVIEW_SCHEMA_VERSION,
    SAVE_CAPABILITY,
    SAVE_CONFIRMATION_PREFIX,
    SAVE_SCHEMA_VERSION,
    NodeRemoveWriteError,
    build_apply_parameters,
    build_save_parameters,
    confirmation_for_plan,
    save_confirmation_for_plan,
    validate_apply_result,
    validate_preview_parameters,
    validate_preview_result,
    validate_save_result,
)
from ue_agent_kit.mcp_node_remove_write_experimental import (  # noqa: E402
    register_node_remove_write_experimental_tools,
)

PLAN_ID = "sha256:" + "a" * 64
EDGE_BEFORE = "sha256:" + "b" * 64
EDGE_AFTER_SAME = EDGE_BEFORE
EDGE_AFTER_DIFF = "sha256:" + "c" * 64
UNTOUCHED = "sha256:" + "d" * 64
PIN_BEFORE = "sha256:" + "e" * 64
PIN_AFTER = "sha256:" + "f" * 64
NODE_AFTER_HASH = "sha256:" + "7" * 64
REVISION = "sha256:" + "8" * 64
ASSET_SHA = "sha256:" + "9" * 64
FINGERPRINT = "sha1:" + "0" * 40
ASSET = "/Game/UEAgentKitBlueprintNodeRemove/BP_NodeRemoveExpansion.BP_NodeRemoveExpansion"
GRAPH = "00000000-1111-4222-8333-444444444444"
NODE = "11111111-2222-4333-8444-555555555555"
NODE_CLASS = "/Script/BlueprintGraph.K2Node_CallFunction"
REMOVABLE_CLASSES = [
    "/Script/BlueprintGraph.K2Node_CallFunction",
    "/Script/BlueprintGraph.K2Node_IfThenElse",
]
REMOVABLE_FUNCTIONS = ["Add_IntInt", "Not_PreBool"]
SOURCE_NODE = "22222222-2222-4333-8444-555555555555"
SOURCE_PIN = "23333333-2222-4333-8444-555555555555"
TARGET_NODE = "33333333-2222-4333-8444-555555555555"
TARGET_PIN = "34444444-2222-4333-8444-555555555555"


def _edge(**overrides: Any) -> dict[str, str]:
    item = {
        "fromNodeGuid": NODE,
        "fromPinName": "ReturnValue",
        "fromPinGuid": SOURCE_PIN,
        "fromKind": "bool",
        "toNodeGuid": TARGET_NODE,
        "toPinName": "Condition",
        "toPinGuid": TARGET_PIN,
        "toKind": "bool",
    }
    item.update(overrides)
    return item


def _request_edge(**overrides: Any) -> dict[str, str]:
    item = {
        key: value
        for key, value in _edge().items()
        if key not in ("fromKind", "toKind")
    }
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
        "packageDirty": False,
        "planId": PLAN_ID,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "nodeGuid": NODE,
        "nodeClass": NODE_CLASS,
        "nodeTitle": "Not",
        "packageRevision": REVISION,
        "graphFingerprint": FINGERPRINT,
        "edgeSetHashBefore": EDGE_BEFORE,
        "edgeSetHashAfter": EDGE_AFTER_DIFF,
        "untouchedEdgesHash": UNTOUCHED,
        "pinIdentityHashBefore": PIN_BEFORE,
        "pinIdentityHashAfter": PIN_AFTER,
        "nodeIdentityHashAfter": NODE_AFTER_HASH,
        "nodePinCount": 3,
        "nodeCountBefore": 8,
        "nodeCountAfter": 7,
        "linkCountBefore": 4,
        "linkCountAfter": 3,
        "removedEdgeCount": 1,
        "untouchedEdgeCount": 3,
        "isolatedNode": False,
        "removedEdges": [_edge()],
        "removableNodeClasses": REMOVABLE_CLASSES,
        "removableFunctions": REMOVABLE_FUNCTIONS,
        "maxNodesPerPlan": 1,
        "maxIncidentEdges": 1,
        "requiredConfirmation": APPLY_CONFIRMATION_PREFIX + PLAN_ID,
    }
    result.update(overrides)
    return result


def _isolated_preview(**overrides: Any) -> dict[str, Any]:
    result = _preview(
        edgeSetHashAfter=EDGE_BEFORE,
        linkCountAfter=4,
        removedEdgeCount=0,
        untouchedEdgeCount=4,
        isolatedNode=True,
        removedEdges=[],
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
        "nodeGuid": NODE,
        "nodeClass": NODE_CLASS,
        "nodeTitle": "Not",
        "packageRevision": REVISION,
        "graphFingerprint": FINGERPRINT,
        "edgeSetHashBefore": EDGE_BEFORE,
        "edgeSetHashAfter": EDGE_AFTER_DIFF,
        "pinIdentityHashBefore": PIN_BEFORE,
        "pinIdentityHashAfter": PIN_AFTER,
        "nodeIdentityHashAfter": NODE_AFTER_HASH,
        "nodeCountBefore": 8,
        "nodeCountAfter": 7,
        "nodeCount": 7,
        "linkCountBefore": 4,
        "linkCountAfter": 3,
        "linkCount": 3,
        "removedEdgeCount": 1,
        "nodePinCount": 3,
        "removedEdges": [_edge()],
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
        "nodeGuid": NODE,
        "nodeClass": NODE_CLASS,
        "packageRevision": REVISION,
        "assetSha256": ASSET_SHA,
        "assetSizeBytes": 4096,
        "edgeSetHashBefore": EDGE_BEFORE,
        "edgeSetHashAfter": EDGE_AFTER_DIFF,
        "pinIdentityHashAfter": PIN_AFTER,
        "nodeIdentityHashAfter": NODE_AFTER_HASH,
        "nodeCount": 7,
        "nodeCountBefore": 8,
        "linkCount": 3,
        "linkCountBefore": 4,
        "removedEdgeCount": 1,
        "removedEdges": ["edge-key"],
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


class NodeRemoveContractTests(unittest.TestCase):
    def _request(self, **overrides: Any) -> dict[str, Any]:
        values: dict[str, Any] = {
            "asset_path": ASSET,
            "graph_guid": GRAPH,
            "node_guid": NODE,
            "expected_node_class": NODE_CLASS,
            "expected_edge_set_hash": EDGE_BEFORE,
            "removed_edges": [_request_edge()],
        }
        values.update(overrides)
        return validate_preview_parameters(**values)

    def test_isolated_and_connected_requests_normalize(self) -> None:
        connected = self._request()
        self.assertEqual(connected["nodeGuid"], NODE)
        self.assertEqual(len(connected["removedEdges"]), 1)
        self.assertNotIn("kind", connected["removedEdges"][0])

        isolated = self._request(removed_edges=[])
        self.assertEqual(isolated["removedEdges"], [])

    def test_identifier_and_class_shapes_are_rejected(self) -> None:
        cases = [
            ({"graph_guid": "not-a-guid"}, "blueprint-node-remove-invalid-identifier"),
            (
                {"graph_guid": "AAAAAAAA-1111-4222-8333-444444444444"},
                "blueprint-node-remove-invalid-identifier",
            ),
            ({"node_guid": NODE[:-1]}, "blueprint-node-remove-invalid-identifier"),
            (
                {"expected_edge_set_hash": "sha256:abc"},
                "blueprint-node-remove-invalid-identifier",
            ),
            (
                {"expected_node_class": "K2Node_CallFunction"},
                "blueprint-node-remove-invalid-parameters",
            ),
            (
                {"expected_node_class": "/Script/BlueprintGraph.K2Node CallFunction"},
                "blueprint-node-remove-invalid-parameters",
            ),
            ({"removed_edges": "nope"}, "blueprint-node-remove-invalid-parameters"),
        ]
        for overrides, code in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(NodeRemoveWriteError) as raised:
                    self._request(**overrides)
                self.assertEqual(raised.exception.code, code)

    def test_removed_edges_are_strict_and_bounded(self) -> None:
        with self.assertRaises(NodeRemoveWriteError) as raised:
            self._request(removed_edges=[_request_edge(), _request_edge()])
        self.assertEqual(
            raised.exception.code,
            "blueprint-node-remove-removed-edges-invalid",
        )

        with self.assertRaises(NodeRemoveWriteError) as raised:
            self._request(removed_edges=[_request_edge(kind="bool")])
        self.assertEqual(raised.exception.code, "blueprint-node-remove-invalid-parameters")

        duplicate = _request_edge()
        with self.assertRaises(NodeRemoveWriteError) as raised:
            self._request(removed_edges=[duplicate, dict(duplicate)])
        self.assertEqual(
            raised.exception.code,
            "blueprint-node-remove-removed-edges-invalid",
        )

    def test_preview_validation_accepts_connected_and_isolated(self) -> None:
        connected = validate_preview_result(_preview())
        self.assertEqual(connected["removedEdgeCount"], 1)
        self.assertFalse(connected["isolatedNode"])
        isolated = validate_preview_result(_isolated_preview())
        self.assertEqual(isolated["removedEdges"], [])
        self.assertTrue(isolated["isolatedNode"])

    def test_preview_tampering_fails_closed(self) -> None:
        for field, value in (
            ("schemaVersion", "ueak.blueprint-node-remove-preview/2.0"),
            ("readOnly", False),
            ("wroteBlueprint", True),
            ("savedPackage", True),
            ("compiled", True),
            ("packageDirty", True),
            ("nodeCountAfter", 8),
            ("linkCountAfter", 4),
            ("removedEdgeCount", 2),
            ("untouchedEdgeCount", 4),
            ("isolatedNode", True),
            ("maxNodesPerPlan", 2),
            ("maxIncidentEdges", 2),
            ("nodeClass", "/Script/BlueprintGraph.K2Node_Event"),
            ("requiredConfirmation", "REMOVE"),
            ("edgeSetHashAfter", EDGE_BEFORE),
            ("pinIdentityHashAfter", PIN_BEFORE),
            ("nodeIdentityHashAfter", "sha256:short"),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_preview())
                tampered[field] = value
                with self.assertRaises(NodeRemoveWriteError) as raised:
                    validate_preview_result(tampered)
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-node-remove-preview-invalid",
                )

        tampered = copy.deepcopy(_preview())
        tampered["removedEdges"][0]["fromNodeGuid"] = SOURCE_NODE
        tampered["removedEdges"][0]["toNodeGuid"] = TARGET_NODE
        with self.assertRaises(NodeRemoveWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-node-remove-preview-invalid")

        tampered = copy.deepcopy(_preview())
        tampered["removedEdges"] = []
        with self.assertRaises(NodeRemoveWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-node-remove-preview-invalid")

        tampered = copy.deepcopy(_isolated_preview())
        tampered["removedEdges"] = [_edge()]
        with self.assertRaises(NodeRemoveWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-node-remove-preview-invalid")

        tampered = copy.deepcopy(_preview())
        tampered["removableNodeClasses"] = ["/Script/BlueprintGraph.K2Node_IfThenElse"]
        with self.assertRaises(NodeRemoveWriteError) as raised:
            validate_preview_result(tampered)
        self.assertEqual(raised.exception.code, "blueprint-node-remove-preview-invalid")

    def test_apply_parameters_bind_preview_and_confirmation(self) -> None:
        expected = confirmation_for_plan(PLAN_ID)
        params = build_apply_parameters(_preview(), expected)
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["expectedEdgeSetHash"], EDGE_BEFORE)
        self.assertEqual(params["expectedNodeClass"], NODE_CLASS)
        self.assertEqual(params["nodeGuid"], NODE)
        self.assertEqual(params["removedEdges"], [_request_edge()])
        self.assertEqual(params["confirmation"], expected)

        isolated = build_apply_parameters(
            _isolated_preview(),
            confirmation_for_plan(PLAN_ID),
        )
        self.assertEqual(isolated["removedEdges"], [])

        for confirmation in ("", expected + " ", expected.lower(), "REMOVE"):
            with self.subTest(confirmation=confirmation):
                with self.assertRaises(NodeRemoveWriteError) as raised:
                    build_apply_parameters(_preview(), confirmation)
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-node-remove-confirmation-required",
                )

    def test_apply_result_tampering_fails_closed(self) -> None:
        validate_apply_result(_apply(), _preview())

        for field, value in (
            ("applied", False),
            ("undoVerified", False),
            ("redoVerified", False),
            ("saved", True),
            ("compiled", True),
            ("packageDirty", False),
            ("planId", "sha256:" + "1" * 64),
            ("edgeSetHashAfter", EDGE_BEFORE),
            ("pinIdentityHashAfter", PIN_BEFORE),
            ("nodeClass", "/Script/BlueprintGraph.K2Node_Event"),
            ("nodeCount", 8),
            ("linkCount", 4),
            ("nodePinCount", 4),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_apply())
                tampered[field] = value
                with self.assertRaises(NodeRemoveWriteError) as raised:
                    validate_apply_result(tampered, _preview())
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-node-remove-apply-invalid",
                )

        tampered = copy.deepcopy(_apply())
        tampered["removedEdges"][0]["fromPinGuid"] = TARGET_PIN
        with self.assertRaises(NodeRemoveWriteError) as raised:
            validate_apply_result(tampered, _preview())
        self.assertEqual(raised.exception.code, "blueprint-node-remove-apply-invalid")

        tampered = copy.deepcopy(_apply())
        tampered["removedEdges"] = []
        with self.assertRaises(NodeRemoveWriteError) as raised:
            validate_apply_result(tampered, _preview())
        self.assertEqual(raised.exception.code, "blueprint-node-remove-apply-invalid")

    def test_save_parameters_are_bound_to_apply_result(self) -> None:
        expected = save_confirmation_for_plan(PLAN_ID)
        params = build_save_parameters(_apply(), expected)
        self.assertEqual(params["planId"], PLAN_ID)
        self.assertEqual(params["expectedEdgeSetHashAfter"], EDGE_AFTER_DIFF)
        self.assertEqual(params["expectedPackageRevision"], REVISION)
        self.assertEqual(params["expectedNodeCount"], 7)
        self.assertEqual(params["expectedLinkCount"], 3)

        with self.assertRaises(NodeRemoveWriteError) as raised:
            build_save_parameters(_apply(), "SAVE")
        self.assertEqual(
            raised.exception.code,
            "blueprint-node-remove-confirmation-required",
        )

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
            ("nodeCount", 8),
            ("linkCount", 4),
            ("edgeSetHashAfter", EDGE_BEFORE),
            ("removedEdgeCount", 0),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(_save())
                tampered[field] = value
                with self.assertRaises(NodeRemoveWriteError) as raised:
                    validate_save_result(tampered, _apply())
                self.assertEqual(
                    raised.exception.code,
                    "blueprint-node-remove-save-invalid",
                )


class NodeRemoveMcpToolTests(unittest.TestCase):
    def _tools(self, capabilities: list[str] | None = None):
        server = _FakeServer()
        live = _FakeLiveEditor(capabilities)
        register_node_remove_write_experimental_tools(
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
                "ue_preview_blueprint_node_remove_experimental",
                "ue_apply_blueprint_node_remove_experimental",
                "ue_save_blueprint_node_remove_experimental",
            },
        )

    def test_preview_dispatches_exact_parameters(self) -> None:
        tools, live = self._tools()
        result = tools["ue_preview_blueprint_node_remove_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            node_guid=NODE,
            expected_node_class=NODE_CLASS,
            expected_edge_set_hash=EDGE_BEFORE,
            removed_edges=[_request_edge()],
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["readOnly"])
        self.assertFalse(result["productionDefault"])
        self.assertEqual(live.calls[0][0], PREVIEW_CAPABILITY)
        self.assertEqual(live.calls[0][1]["nodeGuid"], NODE)
        self.assertEqual(live.calls[0][1]["removedEdges"], [_request_edge()])
        self.assertEqual(live.calls[0][2], 30.0)

    def test_preview_defaults_to_isolated_removal(self) -> None:
        tools, live = self._tools()
        tools["ue_preview_blueprint_node_remove_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            node_guid=NODE,
            expected_node_class=NODE_CLASS,
            expected_edge_set_hash=EDGE_BEFORE,
        )
        self.assertEqual(live.calls[0][1]["removedEdges"], [])

    def test_missing_capability_fails_closed(self) -> None:
        tools, live = self._tools(capabilities=["editor.status"])
        result = tools["ue_preview_blueprint_node_remove_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            node_guid=NODE,
            expected_node_class=NODE_CLASS,
            expected_edge_set_hash=EDGE_BEFORE,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "live-editor-capability-missing")
        self.assertEqual(live.calls, [])

    def test_apply_requires_exact_confirmation_before_bridge(self) -> None:
        tools, live = self._tools()
        result = tools["ue_apply_blueprint_node_remove_experimental"](
            preview_result=_preview(),
            confirmation="REMOVE",
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "blueprint-node-remove-confirmation-required",
        )
        self.assertEqual(live.calls, [])

    def test_apply_and_save_dispatch_exact_parameters(self) -> None:
        tools, live = self._tools()
        live.result = _apply()
        result = tools["ue_apply_blueprint_node_remove_experimental"](
            preview_result=_preview(),
            confirmation=confirmation_for_plan(PLAN_ID),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(live.calls[0][0], APPLY_CAPABILITY)
        self.assertEqual(live.calls[0][1]["planId"], PLAN_ID)
        self.assertEqual(live.calls[0][1]["expectedEdgeSetHash"], EDGE_BEFORE)

        live.result = _save()
        result = tools["ue_save_blueprint_node_remove_experimental"](
            apply_result=_apply(),
            confirmation=save_confirmation_for_plan(PLAN_ID),
        )
        self.assertTrue(result["ok"])
        self.assertEqual(live.calls[1][0], SAVE_CAPABILITY)
        self.assertEqual(
            live.calls[1][1],
            {
                "assetPath": ASSET,
                "graphGuid": GRAPH,
                "planId": PLAN_ID,
                "expectedEdgeSetHashAfter": EDGE_AFTER_DIFF,
                "expectedPackageRevision": REVISION,
                "expectedNodeCount": 7,
                "expectedLinkCount": 3,
                "confirmation": save_confirmation_for_plan(PLAN_ID),
            },
        )

    def test_bridge_error_is_returned_as_failure(self) -> None:
        class _Rejecting(_FakeLiveEditor):
            def call_method(self, method, params=None, *, timeout_seconds=None):
                from ue_agent_kit.editor_bridge import LiveEditorError

                raise LiveEditorError(
                    "graph-remove-node-too-many-connections",
                    "Node has 2 incident edges.",
                )

        server = _FakeServer()
        register_node_remove_write_experimental_tools(
            server=server,
            live_editor_service=_Rejecting(),
            tool_annotations_type=_annotations,
            error_response=_error_response,
        )
        result = server.tools["ue_preview_blueprint_node_remove_experimental"](
            asset_path=ASSET,
            graph_guid=GRAPH,
            node_guid=NODE,
            expected_node_class=NODE_CLASS,
            expected_edge_set_hash=EDGE_BEFORE,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            result["error"]["code"],
            "graph-remove-node-too-many-connections",
        )


if __name__ == "__main__":
    unittest.main()

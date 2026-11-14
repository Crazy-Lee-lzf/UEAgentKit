"""Node-spawn phase A: read-side search wrapper and the actionId write gate.

These tests are pure Python: they exercise exactly the two layers this slice
adds on top of the existing patch writer -- the MCP search wrapper over
``native.invoke`` + ``graph.nodeRegistry.catalog``, and the actionId shape/gate
rules of the existing patch contract.  The Editor behaviour is covered by the
``UEAgentKit.NodeSpawn.*`` automation, not here.
"""

from __future__ import annotations

import copy
import unittest
from typing import Any

from ue_agent_kit.editor_bridge import LiveEditorError
from ue_agent_kit.experimental_patch import (
    PatchError,
    _canonical_edge_preview_fields,
    _canonical_node_fields,
    validate_preview_parameters,
)
from ue_agent_kit.mcp_node_spawn_read import (
    CATALOG_TOOL_ID,
    NODE_ACTION_SEARCH_TOOL_NAME,
    NATIVE_INVOKE_METHOD,
    _shape_action,
    register_node_spawn_read_tools,
)
from ue_agent_kit.mcp_patch_experimental import (
    PREVIEW_TOOL_NAME,
    register_patch_experimental_tools,
)

ASSET = "/Game/NodeSpawn/BP_NodeSpawn.BP_NodeSpawn"
GRAPH = "11111111-2222-4333-8444-555555555555"
ACTION_ID = "action:0123456789abcdef0123456789abcdef"
NODE_A = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PIN_A = "11111111-2222-4333-8444-666666666666"
NODE_B = "bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
PIN_B = "22222222-3333-4444-8555-777777777777"


def _preview(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "schemaVersion": "ueak.blueprint-patch-preview/1.0",
        "experimental": True,
        "applied": False,
        "writeOccurred": False,
        "assetPath": ASSET,
        "graphGuid": GRAPH,
        "graphFingerprint": "sha256:" + "a" * 64,
        "stateFingerprint": "sha256:" + "b" * 64,
        "variablesFingerprint": "sha256:" + "c" * 64,
        "nodesFingerprint": "sha256:" + "d" * 64,
        "edgesFingerprint": "sha256:" + "e" * 64,
        "nodeCount": 0,
        "linkCount": 0,
        "variableCount": 0,
        "operationCount": 1,
        "planId": "plan-0123456789abcdef",
        "requiredConfirmation": "SET EXPERIMENTAL BLUEPRINT PATCH plan-0123456789abcdef",
        "operations": [
            {
                "opId": "n1",
                "kind": "createNode",
                "actionId": ACTION_ID,
                "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
                "actionKind": "callFunction",
                "spawnerClass": "/Script/BlueprintGraph.BlueprintFunctionNodeSpawner",
                "x": 9000,
                "y": 8000,
                "status": "validated",
            }
        ],
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
        self.capabilities = capabilities or [
            "editor.previewBlueprintPatchExperimental",
            "editor.applyBlueprintPatchExperimental",
            "editor.saveBlueprintPatchExperimental",
        ]
        self.calls: list[tuple[str, dict[str, Any], float | None]] = []
        self.results: dict[str, Any] = {}
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
        if method not in self.results:
            raise AssertionError(f"unexpected bridge method {method}")
        return copy.deepcopy(self.results[method])


def _annotations(**kwargs: Any) -> dict[str, Any]:
    return kwargs


def _error_response(tool: str, exc: BaseException, *, read_only: bool) -> dict[str, Any]:
    return {
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(exc, "code", "error"), "message": str(exc)},
    }


def _descriptor(**overrides: Any) -> dict[str, Any]:
    descriptor: dict[str, Any] = {
        "schemaVersion": "ueak.node-descriptor/1.0",
        "actionId": "action:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "discoveryKey": {
            "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
            "spawnerClass": "/Script/BlueprintGraph.BlueprintFunctionNodeSpawner",
            "graphSchema": "/Script/BlueprintGraph.EdGraphSchema_K2",
            "actionKind": "callFunction",
            "payload": {"functionPath": "/Script/N1AcceptanceHost.N1FunctionLibrary:AddNumbers"},
        },
        "title": "Add Numbers",
        "menuName": "Add Numbers",
        "category": "N1",
        "trustLevel": "project",
        "descriptorDigest": "sha256:0000000000000000",
        "pins": [
            {
                "name": "A",
                "direction": "input",
                "kind": "data",
                "type": {
                    "category": "int",
                    "subCategory": "",
                    "subCategoryObject": "",
                    "container": "none",
                },
            },
            {
                "name": "B",
                "direction": "input",
                "kind": "data",
                "type": {
                    "category": "int",
                    "subCategory": "",
                    "subCategoryObject": "",
                    "container": "none",
                },
            },
            {
                "name": "ReturnValue",
                "direction": "output",
                "kind": "data",
                "type": {
                    "category": "int",
                    "subCategory": "",
                    "subCategoryObject": "",
                    "container": "none",
                },
            },
        ],
        "constraints": {
            "requiresBlueprintContext": True,
            "requiresTarget": False,
            "requiresWorldContext": False,
            "pure": True,
            "latent": False,
            "variadic": False,
            "hiddenByDefault": [],
        },
    }
    descriptor.update(overrides)
    return descriptor


class CreateNodeActionContractTests(unittest.TestCase):
    def test_action_id_replaces_node_type(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[
                {
                    "opId": "n1",
                    "kind": "createNode",
                    "actionId": ACTION_ID,
                    "x": 9000,
                    "y": 8000,
                    "tempId": "tmp:fn1",
                }
            ],
        )
        self.assertEqual(
            request["operations"],
            [
                {
                    "opId": "n1",
                    "kind": "createNode",
                    "actionId": ACTION_ID,
                    "x": 9000,
                    "y": 8000,
                    "tempId": "tmp:fn1",
                }
            ],
        )

    def test_node_type_and_action_id_are_mutually_exclusive(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[
                    {
                        "opId": "n1",
                        "kind": "createNode",
                        "nodeType": "branch",
                        "actionId": ACTION_ID,
                        "x": 0,
                        "y": 0,
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-spawn-action-id-conflict")

    def test_missing_identity_is_refused(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[{"opId": "n1", "kind": "createNode", "x": 0, "y": 0}],
            )
        self.assertEqual(raised.exception.code, "graph-edit-node-type-not-allowed")

    def test_action_id_shape_is_enforced(self) -> None:
        for bad in (
            "action:XYZ",
            "action:" + "A" * 32,
            "action:" + "0" * 31,
        ):
            with self.subTest(action_id=bad):
                with self.assertRaises(PatchError) as raised:
                    validate_preview_parameters(
                        asset_path=ASSET,
                        graph_guid=GRAPH,
                        operations=[
                            {
                                "opId": "n1",
                                "kind": "createNode",
                                "actionId": bad,
                                "x": 0,
                                "y": 0,
                            }
                        ],
                    )
                self.assertEqual(raised.exception.code, "graph-spawn-action-id-invalid")
        # An over-long value is refused by the shared bounded-string rule first.
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[
                    {
                        "opId": "n1",
                        "kind": "createNode",
                        "actionId": "action:" + "0" * 64,
                        "x": 0,
                        "y": 0,
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-patch-invalid-parameters")

    def test_action_operations_reject_variable_fields(self) -> None:
        with self.assertRaises(PatchError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                operations=[
                    {
                        "opId": "n1",
                        "kind": "createNode",
                        "actionId": ACTION_ID,
                        "variableName": "Health",
                        "x": 0,
                        "y": 0,
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-variable-field-not-allowed")

    def test_preview_result_must_not_predict_pins(self) -> None:
        entry = {
            "actionId": ACTION_ID,
            "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
            "x": 0,
            "y": 0,
            "status": "validated",
        }
        normalized = _canonical_node_fields(
            entry,
            "preview.operations[0]",
            error_code="graph-patch-preview-invalid",
            expect_node_guid=False,
        )
        self.assertEqual(normalized["actionId"], ACTION_ID)
        self.assertNotIn("nodeType", normalized)
        with self.assertRaises(PatchError) as raised:
            _canonical_node_fields(
                {**entry, "pins": [{"name": "A", "direction": "input"}]},
                "preview.operations[0]",
                error_code="graph-patch-preview-invalid",
                expect_node_guid=False,
            )
        self.assertEqual(raised.exception.code, "graph-patch-preview-invalid")

    def test_apply_result_requires_the_actual_pins(self) -> None:
        entry = {
            "actionId": ACTION_ID,
            "nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction",
            "nodeGuid": "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            "function": "/Script/N1AcceptanceHost.N1FunctionLibrary:AddNumbers",
            "x": 0,
            "y": 0,
            "pins": [
                {
                    "name": "A",
                    "direction": "input",
                    "kind": "data",
                    "category": "int",
                    "subCategory": "",
                    "subCategoryObject": "",
                }
            ],
        }
        normalized = _canonical_node_fields(
            entry,
            "applyResult.results[0]",
            error_code="graph-patch-apply-invalid",
            expect_node_guid=True,
        )
        self.assertEqual(len(normalized["pins"]), 1)
        with self.assertRaises(PatchError) as raised:
            _canonical_node_fields(
                {**entry, "pins": []},
                "applyResult.results[0]",
                error_code="graph-patch-apply-invalid",
                expect_node_guid=True,
            )
        self.assertEqual(raised.exception.code, "graph-patch-apply-invalid")


class WildcardPreviewContractTests(unittest.TestCase):
    """Phase B: a preview may honestly report a PC_Wildcard endpoint."""

    def _edge(self, from_type: str, to_type: str, to_pin: str = "Option 0") -> dict[str, Any]:
        return {
            "fromNodeGuid": NODE_A,
            "fromPinGuid": PIN_A,
            "fromPin": "ReturnValue",
            "fromType": from_type,
            "toNodeGuid": NODE_B,
            "toPinGuid": PIN_B,
            "toPin": to_pin,
            "toType": to_type,
        }

    def test_preview_accepts_a_wildcard_target(self) -> None:
        normalized = _canonical_edge_preview_fields(self._edge("int", "wildcard"), "edge", {})
        self.assertEqual(normalized["fromType"], "int")
        self.assertEqual(normalized["toType"], "wildcard")

    def test_preview_accepts_a_wildcard_source(self) -> None:
        normalized = _canonical_edge_preview_fields(
            {
                "fromNodeGuid": NODE_A,
                "fromPinGuid": PIN_A,
                "fromPin": "ReturnValue",
                "fromType": "wildcard",
                "toNodeGuid": NODE_B,
                "toPinGuid": PIN_B,
                "toPin": "Index",
                "toType": "int",
            },
            "edge",
            {},
        )
        self.assertEqual(normalized["fromType"], "wildcard")

    def test_two_concrete_types_must_still_match(self) -> None:
        with self.assertRaises(PatchError) as raised:
            _canonical_edge_preview_fields(self._edge("int", "bool"), "edge", {})
        self.assertEqual(raised.exception.code, "graph-patch-preview-invalid")

    def test_unknown_type_token_is_still_refused(self) -> None:
        with self.assertRaises(PatchError) as raised:
            _canonical_edge_preview_fields(self._edge("int", "object"), "edge", {})
        self.assertEqual(raised.exception.code, "graph-patch-preview-invalid")


class PatchToolNodeSpawnGateTests(unittest.TestCase):
    def _register(self, live_editor: _FakeLiveEditor, *, node_spawn_enabled: bool) -> _FakeServer:
        server = _FakeServer()
        register_patch_experimental_tools(
            server=server,
            live_editor_service=live_editor,
            tool_annotations_type=_annotations,
            error_response=_error_response,
            node_spawn_enabled=node_spawn_enabled,
        )
        return server

    def _operations(self) -> list[dict[str, Any]]:
        return [
            {
                "opId": "n1",
                "kind": "createNode",
                "actionId": ACTION_ID,
                "x": 0,
                "y": 0,
            }
        ]

    def test_closed_mcp_key_refuses_before_the_bridge(self) -> None:
        live_editor = _FakeLiveEditor()
        server = self._register(live_editor, node_spawn_enabled=False)
        response = server.tools[PREVIEW_TOOL_NAME](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=self._operations(),
        )
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "graph-spawn-experimental-disabled")
        self.assertEqual(live_editor.calls, [])

    def test_open_mcp_key_forwards_the_action_id(self) -> None:
        live_editor = _FakeLiveEditor()
        live_editor.results["editor.previewBlueprintPatchExperimental"] = _preview()
        server = self._register(live_editor, node_spawn_enabled=True)
        response = server.tools[PREVIEW_TOOL_NAME](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=self._operations(),
        )
        self.assertTrue(response["ok"], response)
        method, params, _ = live_editor.calls[0]
        self.assertEqual(method, "editor.previewBlueprintPatchExperimental")
        self.assertEqual(params["operations"][0]["actionId"], ACTION_ID)
        self.assertNotIn("nodeType", params["operations"][0])

    def test_primitive_node_type_is_unaffected_by_the_new_key(self) -> None:
        live_editor = _FakeLiveEditor()
        live_editor.results["editor.previewBlueprintPatchExperimental"] = _preview(
            operations=[
                {
                    "opId": "n1",
                    "kind": "createNode",
                    "nodeType": "branch",
                    "nodeClass": "/Script/BlueprintGraph.K2Node_IfThenElse",
                    "x": 0,
                    "y": 0,
                    "inputPins": [],
                    "outputPins": [],
                    "inputPinTypes": {},
                    "outputPinTypes": {},
                    "status": "validated",
                }
            ]
        )
        server = self._register(live_editor, node_spawn_enabled=False)
        response = server.tools[PREVIEW_TOOL_NAME](
            asset_path=ASSET,
            graph_guid=GRAPH,
            operations=[{"opId": "n1", "kind": "createNode", "nodeType": "branch", "x": 0, "y": 0}],
        )
        self.assertTrue(response["ok"], response)


class SearchToolTests(unittest.TestCase):
    def _register(self, live_editor: _FakeLiveEditor) -> _FakeServer:
        server = _FakeServer()
        register_node_spawn_read_tools(
            server=server,
            live_editor_service=live_editor,
            read_annotations=_annotations,
            error_response=_error_response,
        )
        return server

    def _catalog_result(self, descriptors: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "toolId": CATALOG_TOOL_ID,
            "toolVersion": "1.0.0",
            "access": "read",
            "readOnly": True,
            "data": {
                "ok": True,
                "mode": "shadow",
                "actionDatabaseFingerprint": "sha256:1111111111111111",
                "totalDescriptorsAvailable": 900,
                "matchedDescriptors": len(descriptors),
                "descriptors": descriptors,
            },
        }

    def test_search_normalizes_can_create(self) -> None:
        live_editor = _FakeLiveEditor()
        live_editor.results[NATIVE_INVOKE_METHOD] = self._catalog_result([_descriptor()])
        server = self._register(live_editor)
        response = server.tools[NODE_ACTION_SEARCH_TOOL_NAME](query="AddNumbers", limit=5)
        self.assertTrue(response["ok"], response)
        self.assertTrue(response["readOnly"])
        self.assertEqual(response["count"], 1)
        action = response["actions"][0]
        self.assertEqual(action["actionId"], "action:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        self.assertTrue(action["canCreate"])
        self.assertIsNone(action["rejectReason"])
        self.assertEqual(action["trustLevel"], "project")
        self.assertEqual([pin["name"] for pin in action["pins"]], ["A", "B", "ReturnValue"])
        method, params, _ = live_editor.calls[0]
        self.assertEqual(method, NATIVE_INVOKE_METHOD)
        self.assertEqual(params["toolId"], CATALOG_TOOL_ID)
        self.assertEqual(params["arguments"]["query"], "AddNumbers")
        self.assertEqual(params["arguments"]["maxItems"], 5)

    def test_latent_world_and_variadic_are_reported_not_hidden(self) -> None:
        latent = _descriptor(
            actionId="action:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            constraints={
                "requiresBlueprintContext": True,
                "requiresTarget": False,
                "requiresWorldContext": False,
                "pure": False,
                "latent": True,
                "variadic": False,
                "hiddenByDefault": [],
            },
        )
        world = _descriptor(
            actionId="action:cccccccccccccccccccccccccccccccc",
            discoveryKey={
                **_descriptor()["discoveryKey"],
                "payload": {"functionPath": "/Script/Engine.KismetSystemLibrary:PrintString"},
            },
            constraints={
                "requiresBlueprintContext": True,
                "requiresTarget": False,
                "requiresWorldContext": True,
                "pure": False,
                "latent": False,
                "variadic": False,
                "hiddenByDefault": [],
            },
        )
        variadic = _descriptor(
            actionId="action:dddddddddddddddddddddddddddddddd",
            constraints={
                "requiresBlueprintContext": True,
                "requiresTarget": False,
                "requiresWorldContext": False,
                "pure": False,
                "latent": False,
                "variadic": True,
                "hiddenByDefault": [],
            },
        )
        for descriptor, reason in (
            (latent, "latent"),
            (world, "requiresWorldContext"),
            (variadic, "variadic"),
        ):
            shaped = _shape_action(descriptor)
            self.assertIsNotNone(shaped)
            self.assertFalse(shaped["canCreate"])
            self.assertEqual(shaped["rejectReason"], reason)

    def test_binding_spawner_and_other_action_kinds_are_not_creatable(self) -> None:
        bound = _descriptor(
            actionId="action:eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            discoveryKey={
                "nodeClass": "/Script/BlueprintGraph.K2Node_ActorBoundEvent",
                "spawnerClass": "/Script/BlueprintGraph.BlueprintBoundEventNodeSpawner",
                "graphSchema": "/Script/BlueprintGraph.EdGraphSchema_K2",
                "actionKind": "event",
                "payload": {"eventName": "OnActorBeginOverlap"},
            },
        )
        shaped = _shape_action(bound)
        self.assertFalse(shaped["canCreate"])
        self.assertEqual(shaped["rejectReason"], "requires-binding")
        macro = _descriptor(
            actionId="action:ffffffffffffffffffffffffffffffff",
            discoveryKey={
                "nodeClass": "/Script/BlueprintGraph.K2Node_MacroInstance",
                "spawnerClass": "/Script/BlueprintGraph.BlueprintNodeSpawner",
                "graphSchema": "/Script/BlueprintGraph.EdGraphSchema_K2",
                "actionKind": "macro",
                "payload": {"macroGraph": "ForEachLoop"},
            },
        )
        shaped = _shape_action(macro)
        self.assertFalse(shaped["canCreate"])
        self.assertEqual(shaped["rejectReason"], "action-kind:macro")

    def test_registry_unavailable_is_reported_as_an_error(self) -> None:
        live_editor = _FakeLiveEditor()
        live_editor.errors[NATIVE_INVOKE_METHOD] = LiveEditorError(
            "native-registry-unavailable",
            "The native node registry is not enabled.",
        )
        server = self._register(live_editor)
        response = server.tools[NODE_ACTION_SEARCH_TOOL_NAME](query="Delay")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "native-registry-unavailable")

    def test_limit_is_bounded(self) -> None:
        live_editor = _FakeLiveEditor()
        live_editor.results[NATIVE_INVOKE_METHOD] = self._catalog_result([])
        server = self._register(live_editor)
        for bad in (0, 65, -1):
            with self.subTest(limit=bad):
                response = server.tools[NODE_ACTION_SEARCH_TOOL_NAME](limit=bad)
                self.assertFalse(response["ok"])
                self.assertEqual(response["error"]["code"], "native-schema-invalid-arguments")
        self.assertEqual(live_editor.calls, [])


if __name__ == "__main__":
    unittest.main()

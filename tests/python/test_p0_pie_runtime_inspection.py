from __future__ import annotations

import json
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit import __version__  # noqa: E402
from ue_agent_kit.editor_bridge import (  # noqa: E402
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from ue_agent_kit.mcp_server import _capabilities_response, _error_response  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    LIVE_EDITOR_METHODS,
    LIVE_EDITOR_TOOL_NAMES,
    TOOL_DEFINITIONS_BY_NAME,
)

PLUGIN_PRIVATE = TOOL_ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
PIE_HANDLER = PLUGIN_PRIVATE / "EditorBridgePieHandlers.cpp"
PIE_HEADER = PLUGIN_PRIVATE / "PieRuntimeInspection.h"
PIE_NATIVE_TEST = PLUGIN_PRIVATE / "P0PieRuntimeInspectionTest.cpp"

WORLD_CONTEXT_METHOD = "editor.getPieWorldContexts"
INSPECT_ACTOR_METHOD = "editor.inspectPieActor"
WORLD_CONTEXT_TOOL = "ue_get_pie_world_contexts"
INSPECT_ACTOR_TOOL = "ue_inspect_pie_actor"
NEW_CAPABILITIES = (WORLD_CONTEXT_METHOD, INSPECT_ACTOR_METHOD)

# Distinct lookup classes required by the P0 contract.
NOT_RUNNING_CODE = "pie-not-running"
NOT_FOUND_CODE = "pie-instance-not-found"
STALE_CODE = "pie-instance-stale"
AMBIGUOUS_INSTANCE_CODE = "pie-instance-ambiguous"
ACTOR_CODES = {
    "pie-actor-not-found",
    "pie-actor-ambiguous",
    "pie-actor-stale",
    "pie-world-stale",
    "pie-actor-scan-truncated",
    "pie-context-scan-truncated",
    "pie-invalid-parameters",
}


class _PieBridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    version = __version__
    project_name = "HostProject"
    capabilities: list[str] = []
    requests: list[dict[str, Any]] = []
    forced_error: str | None = None


class _PieBridgeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        hello = json.loads(self.rfile.readline().decode("utf-8"))
        self._send(
            hello["requestId"],
            {
                "pluginVersion": self.server.version,  # type: ignore[attr-defined]
                "projectName": self.server.project_name,  # type: ignore[attr-defined]
                "sessionId": "session-p0pie",
                "capabilities": self.server.capabilities,  # type: ignore[attr-defined]
            },
        )
        request = json.loads(self.rfile.readline().decode("utf-8"))
        self.server.requests.append(request)  # type: ignore[attr-defined]
        method = request.get("method")
        params = request.get("params", {})
        forced_error = self.server.forced_error  # type: ignore[attr-defined]
        if forced_error is not None:
            self._send_error(request["requestId"], forced_error, "Forced contract error.")
            return
        if method == WORLD_CONTEXT_METHOD:
            self._send(
                request["requestId"],
                {
                    "state": "playing",
                    "running": True,
                    "simulatingInEditor": False,
                    "readOnly": True,
                    "requiresDatabase": False,
                    "code": "",
                    "maxContexts": params.get("maxContexts", 16),
                    "contextCount": 2,
                    "serverCount": 1,
                    "clientCount": 1,
                    "standaloneCount": 0,
                    "returnedCount": 2,
                    "truncated": False,
                    "identitiesUnique": True,
                    "pieInstanceIds": [1, 2],
                    "items": [
                        {
                            "contextId": "pie-1-listenServer",
                            "pieInstanceId": 1,
                            "serverOrClient": "server",
                            "isServer": True,
                            "isClient": False,
                            "netModeName": "listenServer",
                            "worldPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel",
                        },
                        {
                            "contextId": "pie-2-client",
                            "pieInstanceId": 2,
                            "serverOrClient": "client",
                            "isServer": False,
                            "isClient": True,
                            "netModeName": "client",
                            "worldPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel",
                        },
                    ],
                },
            )
            return
        if method == INSPECT_ACTOR_METHOD:
            self._send(
                request["requestId"],
                {
                    "readOnly": True,
                    "requiresDatabase": False,
                    "resolvedBy": "actorPath" if params.get("actorPath") else "actorName",
                    "pieInstanceId": params.get("pieInstanceId"),
                    "contextId": "pie-1-listenServer",
                    "worldPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel",
                    "actorPath": params.get("actorPath", ""),
                    "actorName": params.get("actorName", ""),
                    "classPath": "/Script/Engine.StaticMeshActor",
                    "netRole": {
                        "localRole": "ROLE_Authority",
                        "localRoleName": "authority",
                        "remoteRole": "ROLE_SimulatedProxy",
                        "remoteRoleName": "simulatedproxy",
                        "hasAuthority": True,
                        "isServer": True,
                        "isClient": False,
                    },
                    "transform": {
                        "location": {"x": 1.0, "y": 2.0, "z": 3.0},
                        "rotation": {"pitch": 0.0, "yaw": 90.0, "roll": 0.0},
                        "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
                    },
                    "componentCount": 1,
                    "componentsReturned": 1,
                    "componentsTruncated": False,
                    "components": [
                        {
                            "name": "StaticMeshComponent0",
                            "objectPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel.Actor_1.StaticMeshComponent0",
                            "classPath": "/Script/Engine.StaticMeshComponent",
                            "isRegistered": True,
                            "isActive": True,
                            "assets": ["/Engine/BasicShapes/Cube.Cube"],
                        }
                    ],
                },
            )
            return
        if method == "editor.getPieState":
            self._send(
                request["requestId"],
                {
                    "state": "playing",
                    "playing": True,
                    "simulating": False,
                    "worldPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel",
                    "worldType": "PIE",
                    "netMode": 3,
                },
            )
            return
        self._send_error(request["requestId"], "live-editor-unknown-method", "Unsupported method.")

    def _send(self, request_id: str, result: dict[str, Any]) -> None:
        payload = {
            "schemaVersion": "1.0",
            "requestId": request_id,
            "ok": True,
            "result": result,
        }
        self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")

    def _send_error(self, request_id: str, code: str, message: str) -> None:
        payload = {
            "schemaVersion": "1.0",
            "requestId": request_id,
            "ok": False,
            "error": {"code": code, "message": message},
        }
        self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")


def _write_descriptor(
    project_path: Path,
    port: int,
    *,
    capabilities: tuple[str, ...] = NEW_CAPABILITIES,
) -> None:
    config = LiveEditorBridgeConfig(project_path)
    descriptor = {
        "schemaVersion": "1.0",
        "address": "127.0.0.1",
        "port": port,
        "authToken": "a" * 48,
        "projectName": config.project_name,
        "projectPathHash": config.project_path_hash,
        "pluginVersion": __version__,
        "capabilities": list(capabilities),
    }
    descriptor_path = config.descriptor_path
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")


class PieLiveBridgeRoundTripTests(unittest.TestCase):
    """Real localhost TCP round trip against a fake Editor Bridge."""

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_directory.cleanup)
        self.project_path = Path(self.temp_directory.name) / "HostProject.uproject"
        self.project_path.write_text('{ "FileVersion": 3 }', encoding="utf-8")
        _PieBridgeServer.requests = []
        _PieBridgeServer.forced_error = None
        _PieBridgeServer.capabilities = list(NEW_CAPABILITIES)
        self.server = _PieBridgeServer(("127.0.0.1", 0), _PieBridgeHandler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2.0)
        _write_descriptor(self.project_path, self.server.server_address[1])
        self.service = LiveEditorBridgeService(
            LiveEditorBridgeConfig(self.project_path, timeout_seconds=2.0),
            server_version=__version__,
        )

    def test_world_context_listing_round_trip(self) -> None:
        payload = self.service.call_tool(WORLD_CONTEXT_TOOL, {"maxContexts": 8})

        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["tool"], WORLD_CONTEXT_TOOL)
        self.assertEqual(payload["source"], "live-editor-memory")
        result = payload["result"]
        self.assertTrue(result["readOnly"])
        self.assertFalse(result["requiresDatabase"])
        self.assertEqual(result["maxContexts"], 8)
        self.assertEqual(result["serverCount"], 1)
        self.assertEqual(result["clientCount"], 1)
        self.assertEqual(
            [item["serverOrClient"] for item in result["items"]],
            ["server", "client"],
        )
        self.assertEqual(len({item["pieInstanceId"] for item in result["items"]}), 2)
        self.assertEqual(_PieBridgeServer.requests[-1]["method"], WORLD_CONTEXT_METHOD)
        self.assertEqual(_PieBridgeServer.requests[-1]["params"], {"maxContexts": 8})

    def test_actor_inspection_round_trip_uses_camel_case_params(self) -> None:
        payload = self.service.call_tool(
            INSPECT_ACTOR_TOOL,
            {
                "pieInstanceId": 1,
                "actorPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel.Actor_1",
                "maxComponents": 4,
                "maxAssetsPerComponent": 2,
            },
        )

        self.assertTrue(payload["ok"])
        result = payload["result"]
        self.assertEqual(result["pieInstanceId"], 1)
        self.assertEqual(result["resolvedBy"], "actorPath")
        self.assertEqual(result["classPath"], "/Script/Engine.StaticMeshActor")
        self.assertEqual(result["netRole"]["localRoleName"], "authority")
        self.assertTrue(result["netRole"]["hasAuthority"])
        self.assertFalse(result["requiresDatabase"])
        self.assertEqual(
            result["components"][0]["assets"],
            ["/Engine/BasicShapes/Cube.Cube"],
        )
        request = _PieBridgeServer.requests[-1]
        self.assertEqual(request["method"], INSPECT_ACTOR_METHOD)
        self.assertEqual(request["params"]["pieInstanceId"], 1)
        self.assertEqual(request["params"]["maxComponents"], 4)
        self.assertEqual(request["params"]["maxAssetsPerComponent"], 2)
        self.assertNotIn("pie_instance_id", request["params"])

    def test_distinct_lookup_codes_survive_the_bridge(self) -> None:
        for code in (NOT_RUNNING_CODE, NOT_FOUND_CODE, STALE_CODE):
            _PieBridgeServer.forced_error = code
            with self.subTest(code=code):
                with self.assertRaises(LiveEditorError) as caught:
                    self.service.call_tool(INSPECT_ACTOR_TOOL, {"pieInstanceId": 9, "actorName": "X"})
                self.assertEqual(caught.exception.code, code)
                self.assertNotEqual(caught.exception.code, "live-editor-action-failed")

    def test_capability_gate_rejects_old_bridge_without_dispatch(self) -> None:
        _write_descriptor(self.project_path, self.server.server_address[1], capabilities=())
        before = len(_PieBridgeServer.requests)
        with self.assertRaises(LiveEditorError) as caught:
            self.service.call_tool(WORLD_CONTEXT_TOOL, {"maxContexts": 4})
        self.assertEqual(caught.exception.code, "live-editor-capability-unavailable")
        self.assertEqual(len(_PieBridgeServer.requests), before)

    def test_invalid_parameters_are_rejected_before_dispatch(self) -> None:
        before = len(_PieBridgeServer.requests)
        cases = (
            ({}, "pieInstanceId"),
            ({"pieInstanceId": 1}, "exactly one"),
            (
                {"pieInstanceId": 1, "actorPath": "/Game/A.A:PersistentLevel.X", "actorName": "X"},
                "exactly one",
            ),
            ({"pieInstanceId": 1, "actorName": "X", "unexpected": True}, "Unsupported Live Editor parameter"),
        )
        for params, expected in cases:
            with self.subTest(params=params):
                with self.assertRaises(LiveEditorError) as caught:
                    self.service.call_tool(INSPECT_ACTOR_TOOL, params)
                self.assertEqual(caught.exception.code, "live-editor-invalid-parameters")
                self.assertIn(expected, str(caught.exception))
        self.assertEqual(len(_PieBridgeServer.requests), before)

    def test_existing_pie_state_tool_stays_backward_compatible(self) -> None:
        payload = self.service.call_tool("ue_get_pie_state")
        result = payload["result"]
        self.assertEqual(
            set(result),
            {"state", "playing", "simulating", "worldPath", "worldType", "netMode"},
        )
        self.assertEqual(result["state"], "playing")
        self.assertEqual(_PieBridgeServer.requests[-1]["method"], "editor.getPieState")
        self.assertEqual(_PieBridgeServer.requests[-1]["params"], {})


class PieParameterNormalizationTests(unittest.TestCase):
    @staticmethod
    def _normalize(tool: str, params: dict[str, Any]) -> dict[str, Any]:
        return LiveEditorBridgeService._normalize_tool_params(tool, params)

    def test_world_context_defaults_and_bounds(self) -> None:
        self.assertEqual(self._normalize(WORLD_CONTEXT_TOOL, {}), {"maxContexts": 16})
        self.assertEqual(
            self._normalize(WORLD_CONTEXT_TOOL, {"maxContexts": 32}),
            {"maxContexts": 32},
        )
        for value in (0, 33, True, "16"):
            with self.subTest(value=value):
                with self.assertRaises(LiveEditorError) as caught:
                    self._normalize(WORLD_CONTEXT_TOOL, {"maxContexts": value})
                self.assertEqual(caught.exception.code, "live-editor-invalid-parameters")

    def test_actor_inspection_requires_exactly_one_identity(self) -> None:
        normalized = self._normalize(
            INSPECT_ACTOR_TOOL,
            {"pieInstanceId": 3, "actorGuid": "0123456789ABCDEF0123456789ABCDEF"},
        )
        self.assertEqual(normalized["pieInstanceId"], 3)
        self.assertEqual(normalized["actorGuid"], "0123456789abcdef0123456789abcdef")
        self.assertEqual(normalized["maxComponents"], 64)
        self.assertEqual(normalized["maxAssetsPerComponent"], 8)
        self.assertNotIn("actorName", normalized)

        for params in (
            {"pieInstanceId": 3},
            {"pieInstanceId": 3, "actorName": "A", "actorLabel": "B"},
            {"pieInstanceId": 3, "actorPath": "/Game/A.A:PersistentLevel.X", "actorGuid": "0" * 32},
        ):
            with self.subTest(params=params):
                with self.assertRaises(LiveEditorError):
                    self._normalize(INSPECT_ACTOR_TOOL, params)

    def test_actor_inspection_rejects_invalid_instance_and_guid(self) -> None:
        self.assertEqual(
            self._normalize(INSPECT_ACTOR_TOOL, {"pieInstanceId": 0, "actorName": "A"})[
                "pieInstanceId"
            ],
            0,
        )
        for instance_id in (-1, -2, True, "1"):
            with self.subTest(instance_id=instance_id):
                with self.assertRaises(LiveEditorError):
                    self._normalize(
                        INSPECT_ACTOR_TOOL,
                        {"pieInstanceId": instance_id, "actorName": "A"},
                    )
        with self.assertRaises(LiveEditorError):
            self._normalize(INSPECT_ACTOR_TOOL, {"pieInstanceId": 1, "actorGuid": "not-a-guid"})

    def test_live_object_path_shape_is_validated_without_a_game_asset_requirement(self) -> None:
        accepted = (
            "/Game/Maps/P0Pie.P0Pie:PersistentLevel.Actor_1",
            "/Temp/Untitled_1.Untitled_1:PersistentLevel.StaticMeshActor_2",
            "/Script/Engine.StaticMeshActor",
        )
        for value in accepted:
            with self.subTest(value=value):
                normalized = self._normalize(
                    INSPECT_ACTOR_TOOL,
                    {"pieInstanceId": 1, "actorPath": value},
                )
                self.assertEqual(normalized["actorPath"], value)

        rejected = (
            "",
            "Game/Maps/X.X",
            "/Game/Maps/X",
            "C:/Game/Maps/X.X",
            "/Game/Maps\\X.X",
            "/Game/../X.X",
            "/Game/Maps/X.X.",
            "/Game/Maps/X.X\x01",
        )
        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(LiveEditorError):
                    self._normalize(INSPECT_ACTOR_TOOL, {"pieInstanceId": 1, "actorPath": value})

    def test_actor_inspection_caps_and_expected_identity_fields(self) -> None:
        normalized = self._normalize(
            INSPECT_ACTOR_TOOL,
            {
                "pieInstanceId": 2,
                "actorName": "StaticMeshActor_2",
                "actorClassPath": "/Script/Engine.StaticMeshActor",
                "worldPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel",
                "maxComponents": 128,
                "maxAssetsPerComponent": 32,
            },
        )
        self.assertEqual(normalized["maxComponents"], 128)
        self.assertEqual(normalized["maxAssetsPerComponent"], 32)
        self.assertEqual(normalized["actorClassPath"], "/Script/Engine.StaticMeshActor")
        self.assertEqual(normalized["worldPath"], "/Game/Maps/P0Pie.P0Pie:PersistentLevel")

        for field, value in (
            ("maxComponents", 129),
            ("maxComponents", 0),
            ("maxAssetsPerComponent", 33),
            ("maxAssetsPerComponent", 0),
        ):
            with self.subTest(field=field, value=value):
                with self.assertRaises(LiveEditorError):
                    self._normalize(
                        INSPECT_ACTOR_TOOL,
                        {"pieInstanceId": 2, "actorName": "A", field: value},
                    )


class _FakeMcpServer:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def tool(self, *, annotations: Any = None) -> Any:
        def decorator(function: Any) -> Any:
            self.tools[function.__name__] = function
            return function

        return decorator


class _RecordingLiveService:
    def __init__(
        self,
        payload: dict[str, Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any] | None]] = []
        self.payload = payload or {
            "schemaVersion": "1.0",
            "tool": WORLD_CONTEXT_TOOL,
            "ok": True,
            "readOnly": True,
            "result": {"running": False, "code": NOT_RUNNING_CODE},
        }
        self.error = error

    def call_tool(self, name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((name, params))
        if self.error is not None:
            raise self.error
        return self.payload


class PieMcpWrapperTests(unittest.TestCase):
    def _register(self, service: _RecordingLiveService) -> _FakeMcpServer:
        from ue_agent_kit.mcp_live_tools import register_live_read_tools

        server = _FakeMcpServer()
        with mock.patch(
            "ue_agent_kit.mcp_graph_layout_preview.register_graph_layout_preview_tool"
        ), mock.patch("ue_agent_kit.mcp_retarget_tools.register_retarget_tools"):
            register_live_read_tools(
                server=server,
                live_editor_service=service,  # type: ignore[arg-type]
                index_service=None,
                audit_report_root=None,
                read_annotations=None,
                tool_annotations_type=None,
                error_response=_error_response,
            )
        return server

    def test_wrappers_are_registered_and_read_only(self) -> None:
        server = self._register(_RecordingLiveService())
        self.assertIn(WORLD_CONTEXT_TOOL, server.tools)
        self.assertIn(INSPECT_ACTOR_TOOL, server.tools)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME[WORLD_CONTEXT_TOOL].read_only)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME[INSPECT_ACTOR_TOOL].read_only)

    def test_world_context_wrapper_forwards_bound_and_needs_no_index(self) -> None:
        service = _RecordingLiveService()
        server = self._register(service)
        response = server.tools[WORLD_CONTEXT_TOOL](max_contexts=4)
        self.assertTrue(response["ok"])
        self.assertEqual(service.calls, [(WORLD_CONTEXT_TOOL, {"maxContexts": 4})])

    def test_actor_wrapper_forwards_exact_identity_fields(self) -> None:
        service = _RecordingLiveService()
        server = self._register(service)
        server.tools[INSPECT_ACTOR_TOOL](
            pie_instance_id=2,
            actor_path="/Game/Maps/P0Pie.P0Pie:PersistentLevel.Actor_1",
            max_components=8,
            max_assets_per_component=4,
        )
        name, params = service.calls[0]
        self.assertEqual(name, INSPECT_ACTOR_TOOL)
        self.assertEqual(params["pieInstanceId"], 2)
        self.assertEqual(
            params["actorPath"],
            "/Game/Maps/P0Pie.P0Pie:PersistentLevel.Actor_1",
        )
        self.assertEqual(params["maxComponents"], 8)
        self.assertEqual(params["maxAssetsPerComponent"], 4)
        self.assertNotIn("index_service", params)

    def test_wrapper_error_mapping_preserves_pie_codes(self) -> None:
        service = _RecordingLiveService(
            error=LiveEditorError("pie-actor-ambiguous", "Two Actors match the label."),
        )
        server = self._register(service)
        response = server.tools[INSPECT_ACTOR_TOOL](pie_instance_id=1, actor_label="Shared")
        self.assertFalse(response["ok"])
        self.assertTrue(response["readOnly"])
        self.assertEqual(response["error"]["code"], "pie-actor-ambiguous")
        self.assertFalse(response["error"]["retryable"])


class PieCapabilityDescriptorTests(unittest.TestCase):
    def _capabilities(self) -> dict[str, Any]:
        service = SimpleNamespace(
            config=SimpleNamespace(project_name="HostProject"),
        )
        return _capabilities_response(
            None,
            service,  # type: ignore[arg-type]
            None,
            live_editor_status={"state": "available", "capabilities": list(NEW_CAPABILITIES)},
        )

    def test_capability_block_is_read_only_and_database_free(self) -> None:
        block = self._capabilities()["liveEditor"]["pieRuntimeInspection"]
        self.assertTrue(block["available"])
        self.assertTrue(block["readOnly"])
        self.assertFalse(block["requiresDatabase"])
        # The tools are DB-free, but the current MCP process still checks an
        # existing index at startup; the contract states this explicitly.
        self.assertTrue(block["serverStartupRequiresIndexDatabase"])
        self.assertFalse(block["requiresPieStopped"])
        self.assertFalse(block["pieControlSupported"])
        self.assertTrue(block["serverClientIdentification"])
        self.assertEqual(block["tools"], [WORLD_CONTEXT_TOOL, INSPECT_ACTOR_TOOL])
        self.assertEqual(block["bridgeMethods"], list(NEW_CAPABILITIES))
        self.assertEqual(block["maxWorldContexts"], 32)
        self.assertEqual(block["maxActorComponents"], 128)
        self.assertEqual(block["maxComponentAssets"], 32)
        self.assertEqual(block["maxAmbiguousCandidates"], 8)
        self.assertEqual(
            block["actorIdentityFields"],
            ["actorPath", "actorGuid", "actorName", "actorLabel"],
        )
        codes = block["errorCodes"]
        self.assertEqual(len(codes), len(set(codes)))
        for code in (NOT_RUNNING_CODE, NOT_FOUND_CODE, STALE_CODE, AMBIGUOUS_INSTANCE_CODE):
            self.assertIn(code, codes)

    def test_pie_state_remains_the_only_pie_control_flag(self) -> None:
        capabilities = self._capabilities()
        self.assertFalse(capabilities["liveEditor"]["editorActions"]["pieSupported"])
        self.assertIn(WORLD_CONTEXT_TOOL, capabilities["liveEditor"]["tools"])
        self.assertIn(INSPECT_ACTOR_TOOL, capabilities["liveEditor"]["tools"])
        self.assertEqual(capabilities["liveEditor"]["tools"], LIVE_EDITOR_TOOL_NAMES)


class PieRegistryContractTests(unittest.TestCase):
    def test_registry_maps_both_tools_to_their_bridge_methods(self) -> None:
        self.assertEqual(LIVE_EDITOR_METHODS[WORLD_CONTEXT_TOOL], WORLD_CONTEXT_METHOD)
        self.assertEqual(LIVE_EDITOR_METHODS[INSPECT_ACTOR_TOOL], INSPECT_ACTOR_METHOD)
        for tool in (WORLD_CONTEXT_TOOL, INSPECT_ACTOR_TOOL):
            definition = TOOL_DEFINITIONS_BY_NAME[tool]
            self.assertEqual(definition.group, "live-read")
            self.assertTrue(definition.read_only)
            self.assertFalse(definition.destructive)
            self.assertIn(tool, LIVE_EDITOR_TOOL_NAMES)


class PiePluginSourceContractTests(unittest.TestCase):
    """The C++ side is the source of truth for caps, codes and read-only behaviour."""

    def setUp(self) -> None:
        self.handler = PIE_HANDLER.read_text(encoding="utf-8")
        self.header = PIE_HEADER.read_text(encoding="utf-8")
        self.bridge_core = (PLUGIN_PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        self.status_handler = (PLUGIN_PRIVATE / "EditorBridgeStatusHandlers.cpp").read_text(encoding="utf-8")

    def test_pie_handler_file_owns_the_bridge_methods(self) -> None:
        for symbol in (
            "FUEAgentKitEditorBridge::BuildPieWorldContextsResult",
            "FUEAgentKitEditorBridge::TryInspectPieActorResult",
        ):
            self.assertIn(symbol, self.handler)
            self.assertNotIn(symbol, self.bridge_core)

    def test_dispatch_and_capabilities_are_registered(self) -> None:
        self.assertIn('TEXT("editor.getPieWorldContexts")', self.bridge_core)
        self.assertIn('TEXT("editor.inspectPieActor")', self.bridge_core)
        self.assertEqual(
            self.bridge_core.count('TEXT("editor.getPieWorldContexts")'),
            2,
        )
        self.assertEqual(self.bridge_core.count('TEXT("editor.inspectPieActor")'), 2)

    def test_existing_pie_state_contract_is_untouched(self) -> None:
        for token in (
            'Result->SetStringField(TEXT("state"), State)',
            'Result->SetBoolField(TEXT("playing"), State == TEXT("playing"))',
            'Result->SetBoolField(TEXT("simulating"), State == TEXT("simulating"))',
            'Result->SetStringField(TEXT("worldPath")',
            'Result->SetStringField(TEXT("worldType")',
            'Result->SetNumberField(TEXT("netMode")',
        ):
            self.assertIn(token, self.status_handler)

    def test_hard_caps_are_declared(self) -> None:
        for token in (
            "MaxPieWorldContexts = 32",
            "MaxWorldContextScan = 64",
            "MaxActorComponents = 128",
            "MaxComponentAssets = 32",
            "MaxScannedActors = 65536",
            "MaxAmbiguousCandidates = 8",
            "RetiredPieInstanceHistory = 64",
        ):
            self.assertIn(token, self.header)

    def test_distinct_lookup_codes_are_implemented(self) -> None:
        for token in (
            'TEXT("pie-not-running")',
            'TEXT("pie-instance-not-found")',
            'TEXT("pie-instance-stale")',
            'TEXT("pie-instance-ambiguous")',
            'TEXT("pie-world-stale")',
            'TEXT("pie-actor-not-found")',
            'TEXT("pie-actor-ambiguous")',
            'TEXT("pie-actor-stale")',
        ):
            self.assertIn(token, self.handler + self.header)

    def test_duplicate_live_instance_ids_fail_closed(self) -> None:
        # One PIE instance id must never silently resolve to the first of two
        # live worlds: the handler counts context matches and refuses.
        for token in (
            "MatchingContextCount",
            "MatchingContextCount > 1",
            "EFailure::InstanceAmbiguous",
            "the exact world is ambiguous",
        ):
            self.assertIn(token, self.handler)
        self.assertIn("InstanceAmbiguous", self.header)

    def test_actor_resolution_is_exact_and_bounded(self) -> None:
        for token in (
            "TActorIterator<AActor>",
            "MaxScannedActors",
            "GetPathName()",
            "GetActorLabel(false)",
            "GetActorGuid()",
            "GetActorInstanceGuid()",
            "MaxAmbiguousCandidates",
            "Actor->GetComponents(Components)",
            "GetLocalRole()",
            "GetRemoteRole()",
            "GetNumMaterials()",
            "MaxComponents",
        ):
            self.assertIn(token, self.handler)

    def test_handler_never_mutates_the_runtime_or_the_database(self) -> None:
        forbidden = (
            "SpawnActor",
            "DestroyActor(",
            "DestroyComponent(",
            "SetActorTransform(",
            "SetActorLocation(",
            "SetActorLabel(",
            "MarkPackageDirty",
            "SavePackage(",
            "Modify(",
            "Rename(",
            "sqlite",
            "SQLite",
        )
        for token in forbidden:
            self.assertNotIn(token, self.handler, token)
        self.assertIn('SetBoolField(TEXT("requiresDatabase"), false)', self.handler)
        self.assertIn('SetBoolField(TEXT("readOnly"), true)', self.handler)

    def test_native_automation_test_is_named_p0pie(self) -> None:
        self.assertTrue(PIE_NATIVE_TEST.is_file())
        native = PIE_NATIVE_TEST.read_text(encoding="utf-8")
        self.assertIn('"UEAgentKit.P0Pie.ContractCodes"', native)
        self.assertIn('"UEAgentKit.P0Pie.RuntimeWorldContexts"', native)
        self.assertIn("FStartPIECommand(false)", native)
        self.assertIn("FEndPlayMapCommand()", native)
        self.assertIn("PIE_ListenServer", native)


if __name__ == "__main__":
    unittest.main()

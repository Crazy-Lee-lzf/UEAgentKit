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
from ue_agent_kit.mcp_pure_live_tools import (  # noqa: E402
    PURE_LIVE_READ_TOOL_NAMES,
    build_pure_live_capabilities,
)
from ue_agent_kit.mcp_server import _capabilities_response  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    LIVE_EDITOR_METHODS,
    LIVE_EDITOR_TOOL_NAMES,
    TOOL_DEFINITIONS_BY_NAME,
    tool_names_for_mode,
)

PLUGIN_PRIVATE = TOOL_ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
PLUGIN_PUBLIC = TOOL_ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Public"
SCALAR_TOOL = "ue_get_pie_scalars"
SCALAR_METHOD = "editor.getPieScalars"
SCALAR_PATH = "/Game/Maps/P0Pie.P0Pie:PersistentLevel.ApiFixture"


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    version = __version__
    project_name = "ScalarHost"
    capabilities = [SCALAR_METHOD]
    requests: list[dict[str, Any]] = []
    forced_error: str | None = None


class _BridgeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        hello = json.loads(self.rfile.readline().decode("utf-8"))
        self._send(
            hello["requestId"],
            {
                "pluginVersion": self.server.version,  # type: ignore[attr-defined]
                "projectName": self.server.project_name,  # type: ignore[attr-defined]
                "sessionId": "scalar-session",
                "capabilities": self.server.capabilities,  # type: ignore[attr-defined]
            },
        )
        request = json.loads(self.rfile.readline().decode("utf-8"))
        self.server.requests.append(request)  # type: ignore[attr-defined]
        forced = self.server.forced_error  # type: ignore[attr-defined]
        if forced:
            self._send_error(request["requestId"], forced, "forced scalar error")
            return
        if request.get("method") == SCALAR_METHOD:
            params = request.get("params", {})
            self._send(
                request["requestId"],
                {
                    "readOnly": True,
                    "requiresDatabase": False,
                    "source": "pie-live",
                    "sampledAtUtc": "2026-10-29T00:00:00.000Z",
                    "pieInstanceId": params.get("pieInstanceId"),
                    "contextId": "pie-0-listenServer",
                    "worldPath": "/Game/Maps/P0Pie.P0Pie:PersistentLevel",
                    "netModeName": "listenServer",
                    "serverOrClient": "server",
                    "targetKind": "actor",
                    "objectPath": params.get("actorPath") or SCALAR_PATH,
                    "objectName": "ApiFixture",
                    "ownerClass": "/Script/Engine.Actor",
                    "localRoleName": "authority",
                    "hasAuthority": True,
                    "maxStringChars": params.get("maxStringChars", 256),
                    "requestedCount": len(params.get("propertyNames", [])),
                    "returnedCount": len(params.get("propertyNames", [])),
                    "properties": [
                        {
                            "name": name,
                            "status": "ok",
                            "valueReturned": True,
                            "type": "int32",
                            "value": 1,
                            "declaringClassPath": "/Script/Engine.Actor",
                            "declaringClassName": "Actor",
                            "declaringClassKind": "native",
                            "declaringClassPathTruncated": False,
                        }
                        for name in params.get("propertyNames", [])
                    ],
                },
            )
            return
        self._send_error(request["requestId"], "live-editor-unknown-method", "unsupported")

    def _send(self, request_id: str, result: dict[str, Any]) -> None:
        self.wfile.write(
            json.dumps(
                {"schemaVersion": "1.0", "requestId": request_id, "ok": True, "result": result}
            ).encode("utf-8")
            + b"\n"
        )

    def _send_error(self, request_id: str, code: str, message: str) -> None:
        self.wfile.write(
            json.dumps(
                {
                    "schemaVersion": "1.0",
                    "requestId": request_id,
                    "ok": False,
                    "error": {"code": code, "message": message},
                }
            ).encode("utf-8")
            + b"\n"
        )


def _write_descriptor(project: Path, port: int, capabilities: list[str] | None = None) -> None:
    config = LiveEditorBridgeConfig(project)
    payload = {
        "schemaVersion": "1.0",
        "address": "127.0.0.1",
        "port": port,
        "authToken": "e" * 48,
        "projectName": config.project_name,
        "projectPathHash": config.project_path_hash,
        "pluginVersion": __version__,
        "capabilities": capabilities if capabilities is not None else [SCALAR_METHOD],
    }
    path = config.descriptor_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


class PieScalarParameterTests(unittest.TestCase):
    @staticmethod
    def _normalize(params: dict[str, Any]) -> dict[str, Any]:
        return LiveEditorBridgeService._normalize_tool_params(SCALAR_TOOL, params)

    def test_valid_actor_and_component_requests_normalize(self) -> None:
        normalized = self._normalize(
            {
                "pieInstanceId": 0,
                "actorName": "ApiFixture",
                "componentName": "ScalarComponent",
                "propertyNames": ["Health", "bIsDead", "Mode"],
                "maxStringChars": 128,
            }
        )
        self.assertEqual(normalized["pieInstanceId"], 0)
        self.assertEqual(normalized["actorName"], "ApiFixture")
        self.assertEqual(normalized["componentName"], "ScalarComponent")
        self.assertEqual(normalized["propertyNames"], ["Health", "bIsDead", "Mode"])
        self.assertEqual(normalized["maxStringChars"], 128)

        by_path = self._normalize(
            {
                "pieInstanceId": 1,
                "actorPath": SCALAR_PATH,
                "componentPath": SCALAR_PATH + ".ScalarComponent",
                "componentClassPath": "/Script/UEAgentKitEditor.P0PieScalarFixtureComponent",
                "propertyNames": ["Speed"],
            }
        )
        self.assertEqual(by_path["actorPath"], SCALAR_PATH)
        self.assertEqual(by_path["componentPath"], SCALAR_PATH + ".ScalarComponent")
        self.assertEqual(by_path["maxStringChars"], 256)

    def test_property_list_boundaries(self) -> None:
        base = {"pieInstanceId": 0, "actorName": "A"}
        accepted = [f"Prop{index}" for index in range(8)]
        self.assertEqual(
            self._normalize({**base, "propertyNames": accepted})["propertyNames"],
            accepted,
        )
        rejected = (
            {**base, "propertyNames": []},
            {**base, "propertyNames": [f"Prop{index}" for index in range(9)]},
            {**base, "propertyNames": ["Health.Sub"]},
            {**base, "propertyNames": ["Tags[0]"]},
            {**base, "propertyNames": ["Health", "Health"]},
            {**base, "propertyNames": ["Bad Name"]},
            {**base, "propertyNames": [""]},
            {**base, "propertyNames": "Health"},
            {**base, "propertyNames": [1]},
            {**base, "propertyNames": ["A" * 65]},
        )
        for params in rejected:
            with self.subTest(params=params):
                with self.assertRaises(LiveEditorError) as caught:
                    self._normalize(params)
                self.assertEqual(caught.exception.code, "live-editor-invalid-parameters")

    def test_identity_and_component_exclusivity(self) -> None:
        base = {"pieInstanceId": 0, "propertyNames": ["Health"]}
        cases = (
            {**base},
            {**base, "actorName": "A", "actorLabel": "B"},
            {**base, "actorName": "A", "componentPath": SCALAR_PATH, "componentName": "C"},
            {**base, "actorName": "A", "componentClassPath": "/Script/Engine.Actor"},
            {**base, "actorName": "A", "componentPath": "Not/A/Path"},
            {**base, "actorName": "A", "maxStringChars": 63},
            {**base, "actorName": "A", "maxStringChars": 513},
            {**base, "actorName": "A", "unexpected": True},
            {"pieInstanceId": -1, "actorName": "A", "propertyNames": ["Health"]},
        )
        for params in cases:
            with self.subTest(params=params):
                with self.assertRaises(LiveEditorError):
                    self._normalize(params)


class PieScalarBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p0pie-scalar-bridge-")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "ScalarHost.uproject"
        self.project.write_text('{ "FileVersion": 3 }', encoding="utf-8")
        _BridgeServer.requests = []
        _BridgeServer.forced_error = None
        self.server = _BridgeServer(("127.0.0.1", 0), _BridgeHandler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join, 2.0)
        _write_descriptor(self.project, self.server.server_address[1])
        self.service = LiveEditorBridgeService(
            LiveEditorBridgeConfig(self.project, timeout_seconds=2.0),
            server_version=__version__,
        )

    def test_round_trip_sends_camel_case_scalar_params(self) -> None:
        payload = self.service.call_tool(
            SCALAR_TOOL,
            {
                "pieInstanceId": 0,
                "actorName": "ApiFixture",
                "componentName": "ScalarComponent",
                "propertyNames": ["Health", "bIsDead"],
                "maxStringChars": 128,
            },
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        result = payload["result"]
        self.assertEqual(result["source"], "pie-live")
        self.assertFalse(result["requiresDatabase"])
        self.assertEqual(result["targetKind"], "actor")
        self.assertEqual([item["name"] for item in result["properties"]], ["Health", "bIsDead"])
        for item in result["properties"]:
            self.assertEqual(item["declaringClassPath"], "/Script/Engine.Actor")
            self.assertEqual(item["declaringClassKind"], "native")
            self.assertFalse(item["declaringClassPathTruncated"])
            self.assertNotIn("declaringBlueprintPath", item)
        request = _BridgeServer.requests[-1]
        self.assertEqual(request["method"], SCALAR_METHOD)
        self.assertEqual(request["params"]["pieInstanceId"], 0)
        self.assertEqual(request["params"]["componentName"], "ScalarComponent")
        self.assertEqual(request["params"]["propertyNames"], ["Health", "bIsDead"])
        self.assertEqual(request["params"]["maxStringChars"], 128)
        self.assertNotIn("property_names", request["params"])

    def test_component_error_codes_surface_unchanged(self) -> None:
        for code in ("pie-component-not-found", "pie-component-ambiguous", "pie-component-stale"):
            _BridgeServer.forced_error = code
            with self.subTest(code=code):
                with self.assertRaises(LiveEditorError) as caught:
                    self.service.call_tool(
                        SCALAR_TOOL,
                        {"pieInstanceId": 0, "actorName": "A", "propertyNames": ["Health"]},
                    )
                self.assertEqual(caught.exception.code, code)

    def test_capability_gate_rejects_old_bridge(self) -> None:
        _write_descriptor(self.project, self.server.server_address[1], capabilities=[])
        before = len(_BridgeServer.requests)
        with self.assertRaises(LiveEditorError) as caught:
            self.service.call_tool(
                SCALAR_TOOL,
                {"pieInstanceId": 0, "actorName": "A", "propertyNames": ["Health"]},
            )
        self.assertEqual(caught.exception.code, "live-editor-capability-unavailable")
        self.assertEqual(len(_BridgeServer.requests), before)


class PieScalarCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="p0pie-scalar-caps-")
        self.addCleanup(self.temp.cleanup)
        project = Path(self.temp.name) / "ScalarHost.uproject"
        project.write_text('{ "FileVersion": 3 }', encoding="utf-8")
        self.service = SimpleNamespace(config=LiveEditorBridgeConfig(project, timeout_seconds=1.0))

    def test_default_capabilities_expose_scalar_inspection(self) -> None:
        capabilities = _capabilities_response(
            None,
            self.service,
            None,
            live_editor_status={"state": "available", "capabilities": [SCALAR_METHOD]},
        )
        block = capabilities["liveEditor"]["pieScalarInspection"]
        self.assertTrue(block["available"])
        self.assertEqual(block["tool"], SCALAR_TOOL)
        self.assertEqual(block["bridgeMethod"], SCALAR_METHOD)
        self.assertTrue(block["readOnly"])
        self.assertFalse(block["requiresDatabase"])
        self.assertEqual(block["maxProperties"], 8)
        self.assertEqual(block["maxStringChars"], 512)
        self.assertEqual(block["defaultStringChars"], 256)
        for type_name in ("bool", "int32", "int64", "float32", "float64", "name", "string", "enum"):
            self.assertIn(type_name, block["supportedTypes"])
        for code in ("pie-component-not-found", "pie-component-ambiguous", "pie-component-stale"):
            self.assertIn(code, block["errorCodes"])
        provenance = block["propertyProvenance"]
        self.assertTrue(provenance["onlyForResolvedProperties"])
        self.assertEqual(provenance["kinds"], ["native", "blueprint", "unknown"])
        self.assertEqual(provenance["maxClassPathChars"], 512)
        self.assertIn("FProperty::GetOwnerClass()", provenance["source"])
        for field in (
            "declaringClassPath",
            "declaringClassName",
            "declaringClassKind",
            "declaringClassPathTruncated",
            "declaringBlueprintPath",
            "declaringBlueprintPathTruncated",
        ):
            self.assertIn(field, provenance["fields"])
        self.assertIn("no filesystem path", provenance["policy"])
        self.assertIn(SCALAR_TOOL, capabilities["liveEditor"]["tools"])
        self.assertEqual(capabilities["liveEditor"]["tools"], LIVE_EDITOR_TOOL_NAMES)

    def test_pure_live_capabilities_expose_scalar_inspection(self) -> None:
        payload = build_pure_live_capabilities(
            server_name="UE Agent Kit",
            server_version=__version__,
            live_status={"configured": True, "state": "available"},
        )
        self.assertIn(SCALAR_TOOL, payload["liveEditor"]["tools"])
        self.assertEqual(len(payload["liveEditor"]["tools"]), len(PURE_LIVE_READ_TOOL_NAMES))
        self.assertEqual(payload["liveEditor"]["pieScalarInspection"]["tool"], SCALAR_TOOL)
        self.assertFalse(payload["liveEditor"]["pieScalarInspection"]["requiresDatabase"])
        pure_provenance = payload["liveEditor"]["pieScalarInspection"]["propertyProvenance"]
        self.assertTrue(pure_provenance["onlyForResolvedProperties"])
        self.assertIn("declaringClassPath", pure_provenance["fields"])


class PieScalarRegistryAndSourceTests(unittest.TestCase):
    def test_registry_maps_scalar_tool_read_only(self) -> None:
        self.assertEqual(LIVE_EDITOR_METHODS[SCALAR_TOOL], SCALAR_METHOD)
        definition = TOOL_DEFINITIONS_BY_NAME[SCALAR_TOOL]
        self.assertEqual(definition.group, "live-read")
        self.assertTrue(definition.read_only)
        self.assertFalse(definition.destructive)
        self.assertIn(SCALAR_TOOL, LIVE_EDITOR_TOOL_NAMES)
        self.assertIn(SCALAR_TOOL, tool_names_for_mode(live_editor_enabled=True))

    def test_cpp_handler_contract(self) -> None:
        handler = (PLUGIN_PRIVATE / "EditorBridgePieHandlers.cpp").read_text(encoding="utf-8")
        header = (PLUGIN_PRIVATE / "PieRuntimeInspection.h").read_text(encoding="utf-8")
        bridge = (PLUGIN_PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        bridge_header = (PLUGIN_PRIVATE / "EditorBridge.h").read_text(encoding="utf-8")
        for token in (
            "TryReadPieScalars",
            "ResolvePieActorExact",
            "MaxScalarProperties",
            "MaxScalarStringChars",
            "IsExposedScalarProperty",
            "CPF_NativeAccessSpecifierPrivate",
            "CPF_Edit",
            "CPF_BlueprintVisible",
            "FInt64Property",
            "FBoolProperty",
            "FStrProperty",
            "FNameProperty",
            "FEnumProperty",
            "FByteProperty",
            "unsupported-type",
            "not-exposed",
            "property-not-found",
            "pie-live",
            "sampledAtUtc",
            "pie-component-not-found",
            "pie-component-ambiguous",
            "pie-component-stale",
            "MaxExactScalarInt64",
            "GetOwnerClass()",
            "declaringClassPath",
            "declaringClassName",
            "declaringClassKind",
            "declaringClassPathTruncated",
            "declaringBlueprintPath",
            "declaringBlueprintPathTruncated",
            "UBlueprintGeneratedClass",
            "CLASS_CompiledFromBlueprint",
            "ClassGeneratedBy",
            "MaxScalarClassPathChars",
        ):
            self.assertIn(token, handler + header, token)
        self.assertIn('TEXT("editor.getPieScalars")', bridge)
        self.assertEqual(bridge.count('TEXT("editor.getPieScalars")'), 2)
        self.assertIn("TryReadPieScalarsResult", bridge)
        self.assertIn("TryReadPieScalarsResult", bridge_header)
        # The failure item factory must never attach declaring-class metadata.
        failure_block = handler.split("TSharedRef<FJsonObject> MakeScalarFailure(", 1)[1].split(
            "TSharedRef<FJsonObject> MakeEnumItem(", 1
        )[0]
        self.assertNotIn("declaringClass", failure_block)
        # No filesystem/source-file guessing anywhere in the PIE bridge handler.
        for forbidden_source_path in (
            "FPaths::",
            "IFileManager",
            "GetSourceFilePath",
            "SourceFile",
        ):
            self.assertNotIn(forbidden_source_path, handler, forbidden_source_path)
        for forbidden in (
            "SpawnActor",
            "DestroyActor(",
            "DestroyComponent(",
            "SetActorTransform(",
            "MarkPackageDirty",
            "SavePackage(",
            "ExecuteFunction",
            "ProcessEvent(",
            "sqlite",
        ):
            self.assertNotIn(forbidden, handler, forbidden)

    def test_native_fixture_declares_expected_scalars(self) -> None:
        fixture = (PLUGIN_PUBLIC / "P0PieScalarFixture.h").read_text(encoding="utf-8")
        for property_name in (
            "Health",
            "Ammo",
            "Precision",
            "Score",
            "bIsDead",
            "StateName",
            "Description",
            "Mode",
            "UnsupportedArray",
            "UnsupportedMap",
            "UnsupportedSet",
            "UnsupportedStruct",
            "UnsupportedText",
            "UnsupportedObject",
            "NotExposed",
            "PrivateEditable",
        ):
            self.assertIn(property_name, fixture)
        self.assertIn("EP0PieScalarMode", fixture)

    def test_scalar_caps_are_declared_and_documented(self) -> None:
        header = (PLUGIN_PRIVATE / "PieRuntimeInspection.h").read_text(encoding="utf-8")
        for token in (
            "MaxScalarProperties = 8",
            "MaxScalarPropertyNameChars = 64",
            "MinScalarStringChars = 64",
            "DefaultScalarStringChars = 256",
            "MaxScalarStringChars = 512",
            "MaxScalarClassPathChars = 512",
        ):
            self.assertIn(token, header)

    def test_native_origin_tests_are_prepared(self) -> None:
        native = (PLUGIN_PRIVATE / "P0PieRuntimeInspectionTest.cpp").read_text(encoding="utf-8")
        for token in (
            "declaringClassPath",
            "declaringClassKind",
            "declaringClassPathTruncated",
            "InitialLifeSpan",
            "AActor::StaticClass()->GetPathName()",
            "unresolved property does not leak declaring metadata",
        ):
            self.assertIn(token, native, token)


if __name__ == "__main__":
    unittest.main()

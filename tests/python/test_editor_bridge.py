from __future__ import annotations

import json
import socketserver
import sys
import tempfile
import threading
import unittest
from pathlib import Path
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


class _BridgeHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        hello = json.loads(self.rfile.readline().decode("utf-8"))
        if hello.get("authToken") != self.server.auth_token:  # type: ignore[attr-defined]
            self._write_error(hello.get("requestId", ""), "live-editor-authentication-failed")
            return
        if hello.get("serverVersion") != self.server.version:  # type: ignore[attr-defined]
            self._write_error(hello.get("requestId", ""), "live-editor-version-mismatch")
            return
        if hello.get("projectPathHash") != self.server.project_hash:  # type: ignore[attr-defined]
            self._write_error(hello.get("requestId", ""), "live-editor-project-mismatch")
            return
        self._write_result(
            hello["requestId"],
            {
                "pluginVersion": self.server.version,  # type: ignore[attr-defined]
                "projectName": self.server.project_name,  # type: ignore[attr-defined]
                "sessionId": "session-test",
                "capabilities": self.server.capabilities,  # type: ignore[attr-defined]
            },
        )
        request = json.loads(self.rfile.readline().decode("utf-8"))
        self.server.requests.append(request)  # type: ignore[attr-defined]
        method = request.get("method")
        params = request.get("params", {})
        results: dict[str, dict[str, Any]] = {
            "editor.status": {
                "state": "available",
                "pluginVersion": self.server.version,  # type: ignore[attr-defined]
                "projectName": self.server.project_name,  # type: ignore[attr-defined]
                "engineVersion": "5.6.1",
                "processId": 1234,
                "sessionId": "session-test",
                "capabilities": self.server.capabilities,  # type: ignore[attr-defined]
                "pieState": "stopped",
                "currentLevel": "/Game/Maps/Test.Test:PersistentLevel",
                "dirtyPackageCount": 1,
            },
            "editor.getSelection": {
                "count": 1,
                "truncated": False,
                "items": [
                    {
                        "kind": "Actor",
                        "name": "TestActor",
                        "objectPath": "/Game/Maps/Test.Test:PersistentLevel.TestActor",
                        "packageDirty": True,
                    }
                ],
            },
            "editor.getOpenAssets": {"count": 0, "truncated": False, "items": []},
            "editor.getDirtyAssets": {
                "count": 1,
                "truncated": False,
                "items": [{"packageName": "/Game/Maps/Test", "assetPaths": ["/Game/Maps/Test.Test"]}],
            },
            "editor.getCurrentLevel": {
                "available": True,
                "worldPath": "/Game/Maps/Test.Test",
                "currentLevelPath": "/Game/Maps/Test.Test:PersistentLevel",
                "packageDirty": True,
                "worldPartitioned": False,
            },
            "editor.getPieState": {
                "state": "stopped",
                "playing": False,
                "simulating": False,
                "worldPath": "",
            },
            "editor.getOutputLog": {
                "available": True,
                "resultCount": 1,
                "nextSequence": 8,
                "filters": params,
                "items": [{"sequence": 7, "category": "LogTest", "verbosity": "Warning"}],
            },
            "editor.getCompileErrors": {
                "diagnosticSource": "captured-output-log",
                "historyComplete": False,
                "assetPath": params.get("assetPath", ""),
                "diagnosticCount": 0,
                "loadedBlueprintCount": 1,
            },
            "editor.inspectAssetLive": {
                "assetPath": params.get("assetPath", ""),
                "assetRegistry": {"found": True},
                "memory": {"loaded": False, "loadedByBridge": False, "state": "not-loaded"},
            },
            "editor.getEditorContext": getattr(
                self.server,
                "context_result",
                {
                    "source": "live-editor-memory",
                    "state": "available",
                    "editor": {
                        "state": "available",
                        "pluginVersion": self.server.version,
                        "projectName": self.server.project_name,
                        "sessionId": "session-test",
                        "pieState": "stopped",
                        "dirtyPackageCount": 1,
                    },
                    "world": {
                        "available": True,
                        "worldPath": "/Game/Maps/Test.Test",
                        "worldType": "Editor",
                        "currentLevelPath": "/Game/Maps/Test.Test:PersistentLevel",
                        "packageDirty": True,
                        "worldPartitioned": False,
                    },
                    "selection": {
                        "count": 1,
                        "truncated": False,
                        "items": [
                            {
                                "kind": "Actor",
                                "name": "TestActor",
                                "objectPath": "/Game/Maps/Test.Test:PersistentLevel.TestActor",
                            }
                        ],
                    },
                    "openAssets": {"count": 0, "truncated": False, "items": []},
                    "dirtyPackages": {
                        "count": 1,
                        "truncated": False,
                        "items": [{"packageName": "/Game/Maps/Test", "assetPaths": ["/Game/Maps/Test.Test"]}],
                    },
                    "blueprintGraphSelection": {
                        "available": False,
                        "reasonCode": "no-ordinary-blueprint-editor",
                    },
                    "compileErrors": {
                        "diagnosticSource": "captured-output-log",
                        "diagnosticCount": 0,
                        "diagnosticsTruncated": False,
                        "nextSequence": 8,
                        "loadedBlueprintCount": 1,
                    },
                    "outputLogCursor": {
                        "available": True,
                        "oldestSequence": 1,
                        "newestSequence": 7,
                        "nextSequence": 8,
                        "droppedCount": 0,
                        "truncated": False,
                    },
                    "durationMs": 3,
                    "stageDurationsMs": {
                        "editor": 1,
                        "world": 0,
                        "selection": 0,
                        "openAssets": 0,
                        "dirtyPackages": 0,
                        "blueprintGraphSelection": 0,
                        "compileErrors": 1,
                        "outputLogCursor": 1,
                    },
                    "nextActions": [
                        {"tool": "ue_get_dirty_assets", "reason": "dirty-packages-present"},
                        {"tool": "ue_get_output_log", "reason": "incremental-log-available"},
                    ],
                },
            ),
            "editor.openAsset": {
                "action": "open-asset",
                "assetPath": params.get("assetPath", ""),
                "openAfter": True,
                "saved": False,
            },
            "editor.focusAsset": {"action": "focus-asset", "focused": True, "saved": False},
            "editor.syncContentBrowser": {
                "action": "sync-content-browser",
                "loadedByBridge": False,
                "saved": False,
            },
            "editor.focusActor": {
                "action": "focus-actor",
                "actorGuid": params.get("actorGuid", ""),
                "selected": True,
                "saved": False,
            },
            "editor.compileBlueprint": {
                "action": "compile-blueprint",
                "compiled": True,
                "saved": False,
            },
            "editor.validateAsset": {
                "action": "validate-assets",
                "numRequested": 1,
                "saved": False,
                "validationEvidence": {
                    "schemaVersion": "1.0",
                    "evidenceId": "validation:test-asset",
                    "source": "tool-observed",
                    "scope": "asset",
                    "projectName": "TestProject",
                    "projectPathHash": "sha1:test",
                    "editorSessionId": "session-test",
                    "startedAtUtc": "2026-07-28T00:00:00Z",
                    "completedAtUtc": "2026-07-28T00:00:01Z",
                    "observedAtUtc": "2026-07-28T00:00:01Z",
                    "revisionCoverage": "complete",
                    "revisionSet": [{"assetPath": params.get("assetPath", ""), "revision": "sha256:a", "revisionStable": True}],
                },
            },
            "editor.validateFolder": {
                "action": "validate-assets",
                "numRequested": 2,
                "saved": False,
                "validationEvidence": {
                    "schemaVersion": "1.0",
                    "evidenceId": "validation:test-folder",
                    "source": "tool-observed",
                    "scope": "folder",
                    "projectName": "TestProject",
                    "projectPathHash": "sha1:test",
                    "editorSessionId": "session-test",
                    "startedAtUtc": "2026-07-28T00:00:00Z",
                    "completedAtUtc": "2026-07-28T00:00:01Z",
                    "observedAtUtc": "2026-07-28T00:00:01Z",
                    "revisionCoverage": "complete",
                    "revisionSet": [
                        {"assetPath": "/Game/Test/A.A", "revision": "sha256:a", "revisionStable": True},
                        {"assetPath": "/Game/Test/B.B", "revision": "sha256:b", "revisionStable": True},
                    ],
                },
            },
            "editor.runAutomationTest": {
                "action": "run-automation-test",
                "testName": params.get("testName", ""),
                "state": "success",
                "successful": True,
                "saved": False,
                "validationEvidence": {
                    "schemaVersion": "1.0",
                    "evidenceId": "validation:test-automation",
                    "source": "tool-observed",
                    "scope": "automation",
                    "projectName": "TestProject",
                    "projectPathHash": "sha1:test",
                    "editorSessionId": "session-test",
                    "startedAtUtc": "2026-07-28T00:00:00Z",
                    "completedAtUtc": "2026-07-28T00:00:01Z",
                    "observedAtUtc": "2026-07-28T00:00:01Z",
                    "revisionCoverage": "not-applicable",
                    "revisionSet": [],
                },
            },
            "editor.batchTask.start": {
                "taskId": "11111111-1111-1111-1111-111111111111",
                "operation": params.get("operation", "scanCurrentWorld"),
                "state": "running",
                "editorSessionId": "session-test",
                "startedAtUtc": "2026-08-01T00:00:00.000Z",
                "progress": {
                    "processedActors": 0,
                    "totalActors": 3,
                    "completedPercent": 0,
                    "elapsedSeconds": 0.0,
                    "estimatedRemainingSeconds": 0.0,
                },
                "world": {
                    "name": "Test",
                    "path": "/Game/Maps/Test.Test:PersistentLevel",
                    "worldId": 1,
                    "worldType": "Editor",
                },
                "summary": {
                    "actorCount": 0,
                    "totalComponentCount": 0,
                    "actorClassCounts": [],
                    "actorClassCountsTruncated": False,
                    "limits": {
                        "maxActors": params.get("maxActors", 2000),
                        "maxComponentsPerActor": params.get("maxComponentsPerActor", 200),
                        "actorLimitReached": False,
                        "componentLimitActorCount": 0,
                    },
                },
                "durationMs": 1,
            },
            "editor.batchTask.status": {
                "taskId": params.get("taskId", ""),
                "operation": "scanCurrentWorld",
                "state": "completed",
                "editorSessionId": "session-test",
                "startedAtUtc": "2026-08-01T00:00:00.000Z",
                "completedAtUtc": "2026-08-01T00:00:01.000Z",
                "progress": {
                    "processedActors": 3,
                    "totalActors": 3,
                    "completedPercent": 100,
                    "elapsedSeconds": 1.0,
                    "estimatedRemainingSeconds": 0.0,
                },
                "world": {
                    "name": "Test",
                    "path": "/Game/Maps/Test.Test:PersistentLevel",
                    "worldId": 1,
                    "worldType": "Editor",
                },
                "summary": {
                    "actorCount": 3,
                    "totalComponentCount": 4,
                    "actorClassCounts": [
                        {"classPath": "/Script/Engine.StaticMeshActor", "count": 3}
                    ],
                    "actorClassCountsTruncated": False,
                    "limits": {
                        "maxActors": 2000,
                        "maxComponentsPerActor": 200,
                        "actorLimitReached": False,
                        "componentLimitActorCount": 0,
                    },
                },
                "details": {
                    "actorCount": 1,
                    "truncated": False,
                    "items": [
                        {
                            "actorGuid": "22222222-2222-2222-2222-222222222222",
                            "label": "SM_Test",
                            "classPath": "/Script/Engine.StaticMeshActor",
                            "actorPath": "/Game/Maps/Test.Test:PersistentLevel.SM_Test",
                            "levelPath": "/Game/Maps/Test.Test:PersistentLevel",
                            "componentCount": 2,
                            "componentsTruncated": False,
                            "components": [
                                {
                                    "name": "StaticMeshComponent0",
                                    "classPath": "/Script/Engine.StaticMeshComponent",
                                    "nativeClass": True,
                                }
                            ],
                        }
                    ],
                },
                "durationMs": 1000,
            },
            "editor.batchTask.cancel": {
                "taskId": params.get("taskId", ""),
                "operation": "scanCurrentWorld",
                "state": "cancelled",
                "editorSessionId": "session-test",
                "startedAtUtc": "2026-08-01T00:00:00.000Z",
                "completedAtUtc": "2026-08-01T00:00:00.500Z",
                "progress": {
                    "processedActors": 1,
                    "totalActors": 3,
                    "completedPercent": 33,
                    "elapsedSeconds": 0.5,
                    "estimatedRemainingSeconds": 1.0,
                },
                "world": {
                    "name": "Test",
                    "path": "/Game/Maps/Test.Test:PersistentLevel",
                    "worldId": 1,
                    "worldType": "Editor",
                },
                "summary": {
                    "actorCount": 1,
                    "totalComponentCount": 1,
                    "actorClassCounts": [
                        {"classPath": "/Script/Engine.StaticMeshActor", "count": 1}
                    ],
                    "actorClassCountsTruncated": False,
                    "limits": {
                        "maxActors": 2000,
                        "maxComponentsPerActor": 200,
                        "actorLimitReached": False,
                        "componentLimitActorCount": 0,
                    },
                },
                "details": {"actorCount": 1, "truncated": False, "items": []},
                "durationMs": 500,
            },
        }
        result = results.get(method)
        if result is None:
            self._write_error(request.get("requestId", ""), "live-editor-capability-unavailable")
            return
        self._write_result(request["requestId"], result)

    def _write_result(self, request_id: str, result: dict[str, Any]) -> None:
        payload = {
            "schemaVersion": "1.0",
            "requestId": request_id,
            "ok": True,
            "result": result,
        }
        self.wfile.write(json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n")

    def _write_error(self, request_id: str, code: str) -> None:
        payload = {
            "schemaVersion": "1.0",
            "requestId": request_id,
            "ok": False,
            "error": {"code": code, "message": code},
        }
        self.wfile.write(json.dumps(payload).encode("utf-8") + b"\n")


class _BridgeServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True


class EditorBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="ueak_live_editor_")
        self.root = Path(self.temporary_directory.name)
        self.project_path = self.root / "测试项目.uproject"
        self.project_path.write_text("{}", encoding="utf-8")
        self.config = LiveEditorBridgeConfig(self.project_path, timeout_seconds=1.0)
        self.service = LiveEditorBridgeService(self.config, server_version=__version__)
        self.token = "a" * 64
        self.capabilities = [
            "editor.status",
            "editor.getSelection",
            "editor.getOpenAssets",
            "editor.getDirtyAssets",
            "editor.getCurrentLevel",
            "editor.getPieState",
            "editor.getOutputLog",
            "editor.getCompileErrors",
            "editor.inspectAssetLive",
            "editor.getBlueprintGraphSelection",
            "editor.getEditorContext",
            "editor.batchTask.start",
            "editor.batchTask.status",
            "editor.batchTask.cancel",
            "editor.openAsset",
            "editor.focusAsset",
            "editor.syncContentBrowser",
            "editor.focusActor",
            "editor.compileBlueprint",
            "editor.validateAsset",
            "editor.validateFolder",
            "editor.runAutomationTest",
        ]
        self.server = _BridgeServer(("127.0.0.1", 0), _BridgeHandler)
        self.server.auth_token = self.token  # type: ignore[attr-defined]
        self.server.version = __version__  # type: ignore[attr-defined]
        self.server.project_hash = self.config.project_path_hash  # type: ignore[attr-defined]
        self.server.project_name = self.config.project_name  # type: ignore[attr-defined]
        self.server.capabilities = self.capabilities  # type: ignore[attr-defined]
        self.server.requests = []  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temporary_directory.cleanup()

    def _write_descriptor(self, **overrides: Any) -> None:
        descriptor = {
            "schemaVersion": "1.0",
            "address": "127.0.0.1",
            "port": self.server.server_address[1],
            "authToken": self.token,
            "projectName": self.config.project_name,
            "projectPathHash": self.config.project_path_hash,
            "pluginVersion": __version__,
            "processId": 1234,
            "sessionId": "session-test",
            "capabilities": self.capabilities,
        }
        descriptor.update(overrides)
        self.config.descriptor_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.descriptor_path.write_text(
            json.dumps(descriptor, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_authenticated_status_and_selection(self) -> None:
        self._write_descriptor()
        status = self.service.status()
        self.assertEqual(status["state"], "available")
        self.assertEqual(status["pluginVersion"], __version__)
        self.assertEqual(status["projectName"], "测试项目")
        self.assertEqual(status["pieState"], "stopped")
        self.assertEqual(status["dirtyPackageCount"], 1)

        selection = self.service.call_tool("ue_get_selection")
        self.assertTrue(selection["ok"])
        self.assertEqual(selection["source"], "live-editor-memory")
        self.assertEqual(selection["result"]["items"][0]["kind"], "Actor")

    def test_bounded_log_compile_and_live_asset_parameters(self) -> None:
        self._write_descriptor()
        output = self.service.call_tool(
            "ue_get_output_log",
            {
                "category": "LogTest",
                "minimumVerbosity": "warning",
                "keyword": "compile",
                "sinceSequence": 5,
                "sinceUtc": "2026-07-23T00:00:00Z",
                "untilUtc": "2026-07-24T00:00:00+00:00",
                "pieSessionId": 2,
                "limit": 20,
            },
        )
        self.assertEqual(output["result"]["nextSequence"], 8)
        request = self.server.requests[-1]  # type: ignore[attr-defined]
        self.assertEqual(request["params"]["minimumVerbosity"], "warning")
        self.assertEqual(request["params"]["sinceUtc"], "2026-07-23T00:00:00.000Z")

        compile_result = self.service.call_tool(
            "ue_get_compile_errors",
            {"assetPath": "/Game/Test/BP_Test.BP_Test", "limit": 10},
        )
        self.assertFalse(compile_result["result"]["historyComplete"])
        self.assertEqual(compile_result["result"]["assetPath"], "/Game/Test/BP_Test.BP_Test")

        live_asset = self.service.call_tool(
            "ue_inspect_asset_live",
            {"assetPath": "/Game/Test/BP_Test.BP_Test"},
        )
        self.assertFalse(live_asset["result"]["memory"]["loadedByBridge"])

        invalid_cases = (
            ("ue_get_output_log", {"minimumVerbosity": "trace"}),
            ("ue_get_output_log", {"limit": 101}),
            ("ue_get_output_log", {"sinceUtc": "2026-07-24", "untilUtc": "2026-07-23T00:00:00Z"}),
            ("ue_get_compile_errors", {"assetPath": "C:/Project/Test.uasset"}),
            ("ue_get_compile_errors", {"limit": 101}),
            ("ue_inspect_asset_live", {"assetPath": "/Game/Test/BP_Test"}),
            ("ue_get_selection", {"unexpected": True}),
        )
        for tool_name, params in invalid_cases:
            with self.subTest(tool=tool_name, params=params):
                with self.assertRaises(LiveEditorError) as context:
                    self.service.call_tool(tool_name, params)
                self.assertEqual(context.exception.code, "live-editor-invalid-parameters")

    def test_live_action_parameters_are_bounded_and_non_read_only(self) -> None:
        self._write_descriptor()
        opened = self.service.call_tool(
            "ue_open_asset",
            {"assetPath": "/Game/Test/BP_Test.BP_Test"},
        )
        self.assertFalse(opened["readOnly"])
        self.assertTrue(opened["result"]["openAfter"])

        actor = self.service.call_tool(
            "ue_focus_actor",
            {"actorGuid": "AAAAAAAA-BBBB-CCCC-DDDD-EEEEEEEEEEEE"},
        )
        self.assertEqual(
            actor["result"]["actorGuid"],
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        )
        request = self.server.requests[-1]  # type: ignore[attr-defined]
        self.assertEqual(request["params"]["actorGuid"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

        folder = self.service.call_tool(
            "ue_validate_folder",
            {
                "packagePath": "/Game/Test/",
                "recursive": False,
                "maxAssets": 500,
                "maxIssues": 200,
            },
        )
        self.assertFalse(folder["readOnly"])
        self.assertEqual(folder["result"]["validationEvidence"]["scope"], "folder")
        self.assertEqual(folder["result"]["validationEvidence"]["revisionCoverage"], "complete")
        self.assertEqual(len(folder["result"]["validationEvidence"]["revisionSet"]), 2)
        request = self.server.requests[-1]  # type: ignore[attr-defined]
        self.assertEqual(request["params"]["packagePath"], "/Game/Test")
        self.assertFalse(request["params"]["recursive"] )

        automation = self.service.call_tool(
            "ue_run_automation_test",
            {
                "testName": "UEAgentKit.EditorBridge.LiveActionSmoke",
                "timeoutSeconds": 300,
                "maxEntries": 200,
            },
        )
        self.assertFalse(automation["readOnly"])
        self.assertTrue(automation["result"]["successful"])
        self.assertEqual(automation["result"]["validationEvidence"]["scope"], "automation")
        self.assertEqual(automation["result"]["validationEvidence"]["revisionCoverage"], "not-applicable")
        self.assertEqual(automation["result"]["validationEvidence"]["revisionSet"], [])
        request = self.server.requests[-1]  # type: ignore[attr-defined]
        self.assertEqual(request["params"]["timeoutSeconds"], 300)
        self.assertEqual(request["params"]["maxEntries"], 200)

        invalid_cases = (
            ("ue_focus_actor", {"actorGuid": "not-a-guid"}),
            ("ue_validate_folder", {"packagePath": "/Game"}),
            ("ue_validate_folder", {"packagePath": "/Game/Test.Asset"}),
            ("ue_validate_folder", {"packagePath": "/Game/Test", "recursive": 1}),
            ("ue_validate_folder", {"packagePath": "/Game/Test", "maxAssets": 501}),
            ("ue_validate_folder", {"packagePath": "/Game/Test", "maxIssues": 201}),
            ("ue_validate_asset", {"assetPath": "/Game/Test/A.A", "maxIssues": 0}),
            ("ue_run_automation_test", {"testName": ""}),
            ("ue_run_automation_test", {"testName": " Test.Name"}),
            ("ue_run_automation_test", {"testName": "Test.Name", "timeoutSeconds": 301}),
            ("ue_run_automation_test", {"testName": "Test.Name", "maxEntries": 201}),
        )
        for tool_name, params in invalid_cases:
            with self.subTest(tool=tool_name, params=params):
                with self.assertRaises(LiveEditorError) as context:
                    self.service.call_tool(tool_name, params)
                self.assertEqual(context.exception.code, "live-editor-invalid-parameters")

    def test_editor_context_aggregates_bounded_sections(self) -> None:
        self._write_descriptor()
        context = self.service.call_tool("ue_get_editor_context")
        self.assertTrue(context["ok"])
        self.assertTrue(context["readOnly"])
        self.assertEqual(context["source"], "live-editor-memory")
        result = context["result"]
        for section in (
            "source",
            "editor",
            "world",
            "selection",
            "openAssets",
            "dirtyPackages",
            "blueprintGraphSelection",
            "compileErrors",
            "outputLogCursor",
            "durationMs",
            "stageDurationsMs",
            "nextActions",
        ):
            self.assertIn(section, result)
        self.assertEqual(result["state"], "available")
        self.assertEqual(result["editor"]["sessionId"], "session-test")
        self.assertTrue(result["world"]["available"])
        self.assertIsInstance(result["durationMs"], (int, float))
        self.assertIn("editor", result["stageDurationsMs"])
        self.assertIn("outputLogCursor", result["stageDurationsMs"])
        self.assertEqual(result["outputLogCursor"]["newestSequence"], 7)
        self.assertEqual(result["nextActions"][0]["reason"], "dirty-packages-present")
        request = self.server.requests[-1]  # type: ignore[attr-defined]
        self.assertEqual(request["method"], "editor.getEditorContext")
        self.assertEqual(request["params"], {})

    def test_editor_context_rejects_unknown_parameters(self) -> None:
        self._write_descriptor()
        invalid_cases = (
            {"section": "selection"},
            {"limit": 10},
            {"assetPath": "/Game/Test/BP_Test.BP_Test"},
        )
        for params in invalid_cases:
            with self.subTest(params=params):
                with self.assertRaises(LiveEditorError) as context:
                    self.service.call_tool("ue_get_editor_context", params)
                self.assertEqual(context.exception.code, "live-editor-invalid-parameters")

    def test_editor_context_rejects_missing_capability(self) -> None:
        self._write_descriptor(
            capabilities=[capability for capability in self.capabilities if capability != "editor.getEditorContext"]
        )
        with self.assertRaises(LiveEditorError) as context:
            self.service.call_tool("ue_get_editor_context")
        self.assertEqual(context.exception.code, "live-editor-capability-unavailable")

    def test_editor_context_reports_truncated_section(self) -> None:
        self._write_descriptor()
        self.server.context_result = {  # type: ignore[attr-defined]
            "source": "live-editor-memory",
            "state": "available",
            "editor": {"state": "available", "sessionId": "session-test", "pieState": "stopped", "dirtyPackageCount": 1},
            "world": {"available": True},
            "selection": {
                "count": 60,
                "truncated": True,
                "items": [
                    {
                        "kind": "Actor",
                        "name": f"Actor{index}",
                        "objectPath": f"/Game/Maps/Test.Test:PersistentLevel.Actor{index}",
                    }
                    for index in range(50)
                ],
            },
            "openAssets": {"count": 0, "truncated": False, "items": []},
            "dirtyPackages": {"count": 0, "truncated": False, "items": []},
            "blueprintGraphSelection": {"available": False, "reasonCode": "no-ordinary-blueprint-editor"},
            "compileErrors": {"diagnosticCount": 0, "diagnosticsTruncated": False, "nextSequence": 8},
            "outputLogCursor": {"available": True, "oldestSequence": 1, "newestSequence": 7, "nextSequence": 8, "droppedCount": 0},
            "durationMs": 4,
            "stageDurationsMs": {"editor": 1, "selection": 2, "outputLogCursor": 1},
            "nextActions": [{"tool": "ue_get_selection", "reason": "selection-truncated"}],
        }
        result = self.service.call_tool("ue_get_editor_context")["result"]
        self.assertTrue(result["selection"]["truncated"])
        self.assertEqual(len(result["selection"]["items"]), 50)
        self.assertEqual(result["nextActions"][0]["reason"], "selection-truncated")

    def test_batch_task_start_normalizes_params_and_reports_running(self) -> None:
        self._write_descriptor()
        result = self.service.call_tool(
            "ue_start_batch_task",
            {
                "operation": "scanCurrentWorld",
                "maxActors": 5000,
                "maxComponentsPerActor": 300,
                "timeoutSeconds": 90,
            },
        )["result"]
        self.assertEqual(result["state"], "running")
        self.assertEqual(result["progress"]["totalActors"], 3)
        self.assertEqual(result["summary"]["limits"]["maxActors"], 5000)
        request = self.server.requests[-1]  # type: ignore[attr-defined]
        self.assertEqual(request["method"], "editor.batchTask.start")
        self.assertEqual(
            request["params"],
            {
                "operation": "scanCurrentWorld",
                "maxActors": 5000,
                "maxComponentsPerActor": 300,
                "timeoutSeconds": 90,
            },
        )

    def test_batch_task_status_and_cancel_roundtrip(self) -> None:
        self._write_descriptor()
        task_id = "11111111-2222-3333-4444-555555555555"
        status = self.service.call_tool("ue_get_batch_task", {"taskId": task_id})["result"]
        self.assertEqual(status["taskId"], task_id)
        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["details"]["actorCount"], 1)
        self.assertEqual(status["summary"]["actorCount"], 3)
        cancel = self.service.call_tool("ue_cancel_batch_task", {"taskId": task_id})["result"]
        self.assertEqual(cancel["state"], "cancelled")
        self.assertEqual(cancel["progress"]["completedPercent"], 33)
        requests = self.server.requests[-2:]  # type: ignore[attr-defined]
        self.assertEqual(requests[0]["method"], "editor.batchTask.status")
        self.assertEqual(requests[0]["params"], {"taskId": task_id})
        self.assertEqual(requests[1]["method"], "editor.batchTask.cancel")
        self.assertEqual(requests[1]["params"], {"taskId": task_id})

    def test_batch_task_rejects_invalid_parameters(self) -> None:
        self._write_descriptor()
        invalid_cases = (
            ("ue_start_batch_task", {"operation": "scanAllAssets"}),
            ("ue_start_batch_task", {"maxActors": 0}),
            ("ue_start_batch_task", {"maxActors": 10001}),
            ("ue_start_batch_task", {"maxComponentsPerActor": 0}),
            ("ue_start_batch_task", {"maxComponentsPerActor": 501}),
            ("ue_start_batch_task", {"timeoutSeconds": 4}),
            ("ue_start_batch_task", {"timeoutSeconds": 301}),
            ("ue_start_batch_task", {"unknown": 1}),
            ("ue_get_batch_task", {}),
            ("ue_get_batch_task", {"taskId": "not-a-guid"}),
            ("ue_cancel_batch_task", {"taskId": "123"}),
            ("ue_cancel_batch_task", {"taskId": "11111111-2222-3333-4444-55555555555!"}),
            ("ue_cancel_batch_task", {"taskId": "11111111-2222-3333-4444-555555555555", "extra": 1}),
        )
        for tool_name, params in invalid_cases:
            with self.subTest(tool=tool_name, params=params):
                with self.assertRaises(LiveEditorError) as context:
                    self.service.call_tool(tool_name, params)
                self.assertEqual(context.exception.code, "live-editor-invalid-parameters")

    def test_batch_task_requires_registered_capabilities(self) -> None:
        for tool_name, method in (
            ("ue_start_batch_task", "editor.batchTask.start"),
            ("ue_get_batch_task", "editor.batchTask.status"),
            ("ue_cancel_batch_task", "editor.batchTask.cancel"),
        ):
            with self.subTest(tool=tool_name):
                self._write_descriptor(
                    capabilities=[capability for capability in self.capabilities if capability != method]
                )
                params = (
                    {}
                    if method == "editor.batchTask.start"
                    else {"taskId": "11111111-2222-3333-4444-555555555555"}
                )
                with self.assertRaises(LiveEditorError) as context:
                    self.service.call_tool(tool_name, params)
                self.assertEqual(context.exception.code, "live-editor-capability-unavailable")

    def test_missing_descriptor_degrades_without_exposing_path(self) -> None:
        status = self.service.status()
        self.assertEqual(status["state"], "unavailable")
        self.assertEqual(status["reasonCode"], "live-editor-unavailable")
        self.assertNotIn(str(self.config.descriptor_path), json.dumps(status, ensure_ascii=False))

    def test_descriptor_rejects_version_project_and_non_local_address(self) -> None:
        for overrides, code in (
            ({"pluginVersion": "9.9.9"}, "live-editor-version-mismatch"),
            ({"projectPathHash": "sha1:" + "0" * 40}, "live-editor-project-mismatch"),
            ({"address": "0.0.0.0"}, "live-editor-protocol-error"),
        ):
            with self.subTest(code=code):
                self._write_descriptor(**overrides)
                with self.assertRaises(LiveEditorError) as context:
                    self.service.call_method("editor.status")
                self.assertEqual(context.exception.code, code)

    def test_authentication_failure_is_stable(self) -> None:
        self._write_descriptor(authToken="b" * 64)
        with self.assertRaises(LiveEditorError) as context:
            self.service.call_method("editor.status")
        self.assertEqual(context.exception.code, "live-editor-authentication-failed")


if __name__ == "__main__":
    unittest.main()

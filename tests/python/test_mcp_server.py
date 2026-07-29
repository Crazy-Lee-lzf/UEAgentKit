from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit import __version__  # noqa: E402
from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    GENERIC_ASSET,
    GENERIC_TARGET,
    REVISION_A,
    make_asset,
    make_generic_asset,
    write_export,
)
from ue_agent_kit.agent_api import (  # noqa: E402
    MAX_MCP_SEARCH_LIMIT,
    IndexQueryService,
)
from ue_agent_kit.agent_workflow import WorkflowError  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.mcp_server import create_mcp_server, main as mcp_main  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_DEFINITIONS_BY_NAME,
    tool_names_for_mode,
)


MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


class FakeWorkflowService:
    def __init__(self) -> None:
        self.config = SimpleNamespace(
            commit_enabled=True,
            engine_root=Path("missing-engine"),
            project_path=Path("C:/Projects/TestProject/TestProject.uproject"),
        )

    def status(self):
        return {
            "schemaVersion": "1.0",
            "tool": "ue_workflow_status",
            "ok": True,
            "projectName": "TestProject",
            "writeToolsEnabled": True,
            "commitToolsEnabled": True,
        }

    def freshness_status(self):
        return {
            "state": "fresh",
            "indexFresh": True,
            "indexStale": False,
            "comparedAssetCount": 1,
            "freshAssetCount": 1,
            "staleAssetCount": 0,
            "unavailableAssetCount": 0,
        }

    def prepare_high_level_change(self, **kwargs):
        mode = kwargs.get("mode", "Plan")
        response = {
            "ok": True,
            "tool": kwargs["tool_name"],
            "mode": mode,
            "planId": "plan_test",
            "underlyingOperation": kwargs["operation"],
            "target": kwargs["target"],
            "value": kwargs["value"],
        }
        if mode == "DryRun":
            response["dryRunReceipt"] = "dry_test"
        return response

    def plan_patch(self, **kwargs):
        return {"ok": True, "tool": "ue_plan_patch", "planId": "plan_test", **kwargs}

    def dry_run_patch(self, plan_id):
        return {"ok": True, "tool": "ue_dry_run_patch", "planId": plan_id, "dryRunReceipt": "dry_test"}

    def apply_patch(self, plan_id, dry_run_receipt, confirmation):
        return {"ok": True, "tool": "ue_apply_patch", "planId": plan_id, "applyReceipt": "apply_test"}

    def verify_asset(self, apply_receipt):
        report_id = "report_verify_test"
        revision = f"sha256:{REVISION_A}"
        return {
            "ok": True,
            "tool": "ue_verify_asset",
            "applyReceipt": apply_receipt,
            "verified": True,
            "reportId": report_id,
            "memoryTaskEvidence": {
                "schemaVersion": "1.0",
                "tool": "ue_memory_record_task",
                "arguments": {
                    "task_key": "patch:plan_test",
                    "title": "Verified patch plan_test",
                    "conclusion": (
                        f"The committed asset {ASSET_A} was independently reloaded "
                        f"and matched Revision {revision}."
                    ),
                    "outcome": "succeeded",
                    "patch_ref": "patch:sha256:test",
                    "backup_manifest_ref": "backup-manifest:plan_test.manifest.json",
                    "validation_evidence_ref": f"validation-evidence:{report_id}",
                    "revision_set": [
                        {
                            "assetPath": ASSET_A,
                            "revision": revision,
                            "revisionStable": True,
                        }
                    ],
                    "scopes": [{"scopeType": "asset", "scopeKey": ASSET_A}],
                    "confidence": 1.0,
                    "patch_details": {"planId": "plan_test", "patchDigest": "sha256:test"},
                    "backup_manifest_details": {"manifestId": "plan_test.manifest.json"},
                    "validation_evidence_details": {
                        "reportId": report_id,
                        "independentReload": True,
                        "verified": True,
                        "expectedRevision": revision,
                        "actualRevision": revision,
                    },
                    "details": {
                        "workflowEvidenceSchemaVersion": "1.0",
                        "workflowTool": "ue_verify_asset",
                    },
                },
            },
        }

    def get_asset_state(self, asset_path):
        return {
            "schemaVersion": "1.0",
            "ok": True,
            "tool": "ue_get_asset_state",
            "readOnly": True,
            "assetPath": asset_path,
            "state": "synchronized",
            "sources": {
                "memory": {"state": "unavailable", "revisionAvailable": False},
                "disk": {"state": "available", "revision": REVISION_A},
                "revisionExport": {"state": "available", "revision": REVISION_A},
                "sqlite": {"state": "available", "revision": REVISION_A},
            },
            "saveRequired": False,
            "indexRefreshRequired": False,
            "recommendedAction": "none",
        }

    def refresh_asset_index(self, asset_path, *, mode="Preview"):
        return {
            "ok": True,
            "tool": "ue_refresh_asset_index",
            "assetPath": asset_path,
            "mode": mode,
            "applied": mode == "Apply",
            "restartRequired": mode == "Apply",
        }

    def rollback_patch(self, apply_receipt, **kwargs):
        return {"ok": True, "tool": "ue_rollback_patch", "applyReceipt": apply_receipt, "mode": kwargs.get("mode", "DryRun")}


class FakeLiveEditorService:
    def __init__(self, *, available: bool = True) -> None:
        self.available = available
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.config = SimpleNamespace(
            project_path=Path("C:/Projects/TestProject/TestProject.uproject"),
            project_name="TestProject",
        )

    def status(self):
        if not self.available:
            return {
                "configured": True,
                "state": "unavailable",
                "reasonCode": "live-editor-unavailable",
                "reason": "The fixed Editor is offline.",
                "retryable": True,
            }
        return {
            "configured": True,
            "state": "available",
            "pluginVersion": __version__,
            "projectName": "TestProject",
            "engineVersion": "5.6.1",
            "processId": 1234,
            "sessionId": "session-test",
            "capabilities": [
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
                "editor.openAsset",
                "editor.focusAsset",
                "editor.syncContentBrowser",
                "editor.focusActor",
                "editor.compileBlueprint",
                "editor.validateAsset",
                "editor.validateFolder",
            ],
            "pieState": "stopped",
            "currentLevel": "/Game/Maps/Test.Test:PersistentLevel",
            "dirtyPackageCount": 1,
        }

    def call_tool(self, tool_name: str, params: dict[str, object] | None = None):
        if not self.available:
            raise LiveEditorError("live-editor-unavailable", "The fixed Editor is offline.")
        normalized_params = params or {}
        self.calls.append((tool_name, normalized_params))
        results = {
            "ue_get_selection": {"count": 1, "truncated": False, "items": [{"kind": "Actor"}]},
            "ue_get_open_assets": {"count": 0, "truncated": False, "items": []},
            "ue_get_dirty_assets": {"count": 1, "truncated": False, "items": [{"packageName": "/Game/Test"}]},
            "ue_get_current_level": {"available": True, "currentLevelPath": "/Game/Maps/Test.Test:PersistentLevel"},
            "ue_get_pie_state": {"state": "stopped", "playing": False, "simulating": False},
            "ue_get_output_log": {
                "available": True,
                "resultCount": 1,
                "nextSequence": 12,
                "items": [{"sequence": 11, "category": "LogTest", "verbosity": "Warning"}],
            },
            "ue_get_compile_errors": {
                "diagnosticSource": "captured-output-log",
                "historyComplete": False,
                "diagnosticCount": 1,
                "loadedBlueprintCount": 1,
            },
            "ue_inspect_asset_live": {
                "assetPath": normalized_params.get("assetPath", ""),
                "assetRegistry": {"found": True},
                "memory": {
                    "loaded": True,
                    "loadedByBridge": False,
                    "packageDirty": False,
                    "openInAssetEditor": True,
                    "selected": False,
                    "state": "loaded-saved",
                },
            },
            "ue_get_blueprint_graph_selection": {
                "scope": "ordinary-blueprint-editor",
                "available": True,
                "loadedByBridge": False,
                "blueprintPath": "/Game/Test/BP_Test.BP_Test",
                "graph": {
                    "graphPath": "/Game/Test/BP_Test.BP_Test:EventGraph",
                    "graphName": "EventGraph",
                    "graphGuid": "11111111-1111-1111-1111-111111111111",
                    "editable": True,
                },
                "selectedNodeCount": 1,
                "selectedNodesTruncated": False,
                "selectedNodes": [
                    {
                        "nodeGuid": "22222222-2222-2222-2222-222222222222",
                        "title": "BeginPlay",
                    }
                ],
            },
            "ue_open_asset": {"action": "open-asset", "openedNewEditor": True, "saved": False},
            "ue_focus_asset": {"action": "focus-asset", "focused": True, "saved": False},
            "ue_sync_content_browser": {
                "action": "sync-content-browser",
                "synchronized": True,
                "loadedByBridge": False,
                "saved": False,
            },
            "ue_focus_actor": {
                "action": "focus-actor",
                "actorGuid": normalized_params.get("actorGuid", ""),
                "selected": True,
                "viewportFocused": True,
                "saved": False,
            },
            "ue_compile_blueprint": {
                "action": "compile-blueprint",
                "assetPath": normalized_params.get("assetPath", ""),
                "result": "success",
                "compiled": True,
                "succeeded": True,
                "saved": False,
                "diagnostics": [],
            },
            "ue_validate_asset": {
                "action": "validate-assets",
                "scope": normalized_params.get("assetPath", ""),
                "result": "valid",
                "numRequested": 1,
                "numChecked": 1,
                "saved": False,
                "validationEvidence": {
                    "schemaVersion": "1.0",
                    "source": "tool-observed",
                    "scope": "asset",
                    "projectPathHash": "sha1:test",
                    "editorSessionId": "session-test",
                    "revisionCoverage": "complete",
                    "revisionSet": [{"assetPath": normalized_params.get("assetPath", ""), "revision": "sha256:a", "revisionStable": True}],
                },
            },
            "ue_validate_folder": {
                "action": "validate-assets",
                "scope": normalized_params.get("packagePath", ""),
                "result": "valid",
                "matchedAssetCount": 2,
                "numChecked": 2,
                "saved": False,
                "validationEvidence": {
                    "schemaVersion": "1.0",
                    "source": "tool-observed",
                    "scope": "folder",
                    "projectPathHash": "sha1:test",
                    "editorSessionId": "session-test",
                    "revisionCoverage": "complete",
                    "revisionSet": [
                        {"assetPath": "/Game/Test/A.A", "revision": "sha256:a", "revisionStable": True},
                        {"assetPath": "/Game/Test/B.B", "revision": "sha256:b", "revisionStable": True},
                    ],
                },
            },
            "ue_run_automation_test": {
                "action": "run-automation-test",
                "testName": normalized_params.get("testName", ""),
                "state": "success",
                "successful": True,
                "entryCount": 1,
                "saved": False,
                "validationEvidence": {
                    "schemaVersion": "1.0",
                    "source": "tool-observed",
                    "scope": "automation",
                    "projectPathHash": "sha1:test",
                    "editorSessionId": "session-test",
                    "revisionCoverage": "not-applicable",
                    "revisionSet": [],
                },
            },
        }
        return {
            "schemaVersion": "1.0",
            "tool": tool_name,
            "ok": True,
            "readOnly": TOOL_DEFINITIONS_BY_NAME[tool_name].read_only,
            "source": "live-editor-memory",
            "result": results[tool_name],
        }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="ueak_mcp_")
        self.temp_root = Path(self.temporary_directory.name)
        self.database_path = self.temp_root / "索引" / "ueak.sqlite3"
        export_root = self.temp_root / "export"
        write_export(
            export_root,
            [
                make_generic_asset(),
                make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True),
            ],
        )
        with open_database(self.database_path) as connection:
            result = build_index(connection, export_root, self.database_path)
        self.assertEqual((result.added, result.failed), (2, 0))
        self.service = IndexQueryService(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_index_service_is_read_only_and_returns_stable_envelopes(self) -> None:
        before_hash = sha256(self.database_path)
        status = self.service.check()
        assets = self.service.search("StaticMesh")
        symbols = self.service.search("生命值", scope="symbols", kind="variable")
        asset = self.service.get_asset(ASSET_A)
        references = self.service.find_references(target_asset_path=GENERIC_TARGET)
        after_hash = sha256(self.database_path)

        self.assertEqual(before_hash, after_hash)
        self.assertEqual(status["schemaVersion"], "1.0")
        self.assertEqual(status["tool"], "ue_index_status")
        self.assertTrue(status["readOnly"])
        self.assertTrue(status["indexMetadata"]["immutable"])
        self.assertEqual(status["stats"]["counts"]["assets"], 2)
        self.assertEqual(assets["tool"], "ue_search")
        self.assertEqual(assets["scope"], "assets")
        self.assertEqual([item["asset_path"] for item in assets["results"]], [GENERIC_ASSET])
        self.assertEqual(symbols["scope"], "symbols")
        self.assertEqual(symbols["results"][0]["name"], "生命值")
        self.assertTrue(asset["found"])
        self.assertEqual(asset["asset"]["asset_path"], ASSET_A)
        self.assertEqual(asset["limits"]["graphs"], 100)
        self.assertEqual(references["results"][0]["target_asset_path"], GENERIC_TARGET)

    def test_search_continuation_path_prefix_and_output_budget(self) -> None:
        first = self.service.search(
            "",
            scope="symbols",
            asset_path=ASSET_A,
            limit=1,
        )
        token = first["pagination"]["continuationToken"]
        self.assertTrue(first["pagination"]["hasMore"])
        self.assertTrue(token.startswith("ct_"))

        second = self.service.search(continuation_token=token)
        self.assertEqual(second["pagination"]["offset"], 1)
        self.assertEqual(second["pagination"]["source"], "continuation-token")
        self.assertNotEqual(first["results"][0]["stable_id"], second["results"][0]["stable_id"])

        by_prefix = self.service.search("", path_prefix="/Game/Environment")
        self.assertEqual([item["asset_path"] for item in by_prefix["results"]], [GENERIC_ASSET])

        budgeted = self.service.search(
            "",
            scope="symbols",
            asset_path=ASSET_A,
            limit=3,
            include_details=True,
            max_output_tokens=256,
        )
        self.assertTrue(budgeted["outputBudget"]["truncated"])
        self.assertTrue(budgeted["outputBudget"]["truncationReason"])
        self.assertTrue(budgeted["pagination"]["continuationToken"])

        with self.assertRaisesRegex(ValueError, "unknown or expired"):
            self.service.search(continuation_token=token + "x")
        with self.assertRaisesRegex(ValueError, "another Tool"):
            self.service.find_references(continuation_token=token)

    def test_asset_sections_have_independent_continuations(self) -> None:
        first = self.service.get_asset(
            ASSET_A,
            sections=["identity", "symbols"],
            symbol_limit=1,
        )
        self.assertEqual(first["requestedSections"], ["identity", "symbols"])
        self.assertIn("asset_path", first["asset"])
        self.assertIn("symbols", first["asset"])
        self.assertNotIn("summary", first["asset"])
        self.assertNotIn("revision_value", first["asset"])
        symbol_page = first["sectionPagination"]["symbols"]
        self.assertTrue(symbol_page["hasMore"])
        self.assertTrue(symbol_page["continuationToken"])

        second = self.service.get_asset(continuation_token=symbol_page["continuationToken"])
        self.assertEqual(second["sectionPagination"]["symbols"]["offset"], 1)
        self.assertEqual(second["sectionPagination"]["symbols"]["source"], "continuation-token")
        self.assertEqual(second["requestedSections"], ["identity", "symbols"])

        with self.assertRaisesRegex(ValueError, "canonical GUID"):
            self.service.get_asset(ASSET_A, graph_guid="not-a-guid")
        with self.assertRaisesRegex(ValueError, "beginning with /"):
            self.service.get_asset("Game/BP_Invalid.BP_Invalid")

    def test_service_requires_bounded_and_filtered_queries(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            self.service.search("", limit=MAX_MCP_SEARCH_LIMIT + 1)
        with self.assertRaisesRegex(ValueError, "at least one reference filter"):
            self.service.find_references()
        with self.assertRaisesRegex(ValueError, "graph_limit must not exceed"):
            self.service.get_asset(ASSET_A, graph_limit=501)
        maximum_reference_page = self.service.get_asset(
            ASSET_A,
            sections=["references"],
            reference_limit=1000,
        )
        self.assertTrue(maximum_reference_page["found"])
        with self.assertRaisesRegex(ValueError, "only for symbol search"):
            self.service.search("x", asset_path=ASSET_A)
        with self.assertRaisesRegex(ValueError, "only for asset search"):
            self.service.search("x", scope="symbols", asset_class="Blueprint")

    def test_missing_asset_is_not_an_exception(self) -> None:
        result = self.service.get_asset("/Game/Missing/BP_None.BP_None")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertNotIn("asset", result)

    def test_check_mode_outputs_json_without_starting_protocol(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = mcp_main(["--database", str(self.database_path), "--check"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["tool"], "ue_agent_kit_mcp_status")
        self.assertFalse(payload["writeToolsEnabled"])
        self.assertEqual(payload["index"]["tool"], "ue_index_status")
        self.assertEqual(payload["index"]["stats"]["counts"]["assets"], 2)
        self.assertFalse(payload["projectMemoryEnabled"])

        memory_path = self.temp_root / "check-memory.sqlite3"
        memory_output = io.StringIO()
        with contextlib.redirect_stdout(memory_output):
            memory_exit_code = mcp_main(
                [
                    "--database",
                    str(self.database_path),
                    "--enable-project-memory",
                    "--memory-database",
                    str(memory_path),
                    "--check",
                ]
            )
        self.assertEqual(memory_exit_code, 0)
        memory_payload = json.loads(memory_output.getvalue())
        self.assertTrue(memory_payload["projectMemoryEnabled"])
        self.assertEqual(memory_payload["projectMemory"]["projectKey"], "测试项目")
        self.assertEqual(memory_payload["projectMemory"]["recordCount"], 0)
        self.assertTrue(memory_path.is_file())

        error_output = io.StringIO()
        with contextlib.redirect_stderr(error_output):
            invalid_memory_exit = mcp_main(
                [
                    "--database",
                    str(self.database_path),
                    "--memory-database",
                    str(self.temp_root / "invalid-memory.sqlite3"),
                    "--check",
                ]
            )
        self.assertEqual(invalid_memory_exit, 1)
        self.assertEqual(
            json.loads(error_output.getvalue())["error"]["code"],
            "invalid-arguments",
        )

        project_path = self.temp_root / "TestProject.uproject"
        project_path.write_text("{}", encoding="utf-8")
        live_output = io.StringIO()
        with contextlib.redirect_stdout(live_output):
            live_exit_code = mcp_main(
                [
                    "--database",
                    str(self.database_path),
                    "--enable-live-editor",
                    "--project",
                    str(project_path),
                    "--check",
                ]
            )
        self.assertEqual(live_exit_code, 0)
        live_payload = json.loads(live_output.getvalue())
        self.assertTrue(live_payload["liveEditorEnabled"])
        self.assertEqual(live_payload["liveEditor"]["state"], "unavailable")
        self.assertEqual(live_payload["liveEditor"]["reasonCode"], "live-editor-unavailable")

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_registers_read_only_status_and_query_tools(self) -> None:
        server = create_mcp_server(self.database_path)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(
            [tool.name for tool in tools],
            ["ue_get_capabilities", "ue_get_project_status", "ue_search", "ue_get_asset", "ue_find_references"],
        )
        for tool in tools:
            self.assertNotIn("database", tool.inputSchema.get("properties", {}))
        search_tool = next(tool for tool in tools if tool.name == "ue_search")
        self.assertIn("path_prefix", search_tool.inputSchema["properties"])
        self.assertIn("continuation_token", search_tool.inputSchema["properties"])
        self.assertIn("max_output_tokens", search_tool.inputSchema["properties"])
        asset_tool = next(tool for tool in tools if tool.name == "ue_get_asset")
        self.assertIn("sections", asset_tool.inputSchema["properties"])
        self.assertIn("graph_guid", asset_tool.inputSchema["properties"])
        reference_tool = next(tool for tool in tools if tool.name == "ue_find_references")
        self.assertIn("direction", reference_tool.inputSchema["properties"])
        self.assertIn("depth", reference_tool.inputSchema["properties"])

        _, capabilities = asyncio.run(server.call_tool("ue_get_capabilities", {}))
        self.assertEqual(capabilities["server"]["version"], __version__)
        self.assertEqual(capabilities["server"]["mode"], "read-only")
        self.assertFalse(capabilities["operations"]["available"])
        self.assertFalse(capabilities["freshness"]["available"])
        self.assertFalse(capabilities["freshness"]["planRequiresFreshIndex"])
        self.assertEqual(
            [item["name"] for item in capabilities["tools"]],
            [tool.name for tool in tools],
        )

        _, project_status = asyncio.run(server.call_tool("ue_get_project_status", {}))
        self.assertEqual(project_status["project"]["projectKey"], "测试项目")
        self.assertEqual(project_status["engine"]["state"], "unavailable")
        self.assertEqual(project_status["freshness"]["state"], "unknown")
        self.assertEqual(project_status["liveEditor"]["state"], "unavailable")

        content, payload = asyncio.run(
            server.call_tool(
                "ue_search",
                {"query": "生命值", "scope": "symbols", "kind": "variable"},
            )
        )
        self.assertEqual(len(content), 1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["results"][0]["name"], "生命值")

        rejected_content, error_payload = asyncio.run(server.call_tool("ue_find_references", {}))
        self.assertEqual(len(rejected_content), 1)
        self.assertFalse(error_payload["ok"])
        self.assertEqual(error_payload["error"]["code"], "invalid-arguments")
        self.assertFalse(error_payload["error"]["retryable"])
        self.assertEqual(error_payload["error"]["details"], {})
        self.assertTrue(error_payload["error"]["suggestedAction"])

        _, token_error = asyncio.run(
            server.call_tool("ue_search", {"continuation_token": "ct_unknown"})
        )
        self.assertEqual(token_error["error"]["code"], "invalid-continuation-token")

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_project_memory_mode_is_fixed_and_revision_aware(self) -> None:
        memory_path = self.temp_root / "memory" / "project-memory.sqlite3"
        memory_service = ProjectMemoryService(
            database_path=memory_path,
            project_key="测试项目",
        )
        server = create_mcp_server(
            self.database_path,
            memory_service=memory_service,
        )
        tools = asyncio.run(server.list_tools())
        expected_names = tool_names_for_mode(memory_enabled=True)
        self.assertEqual([tool.name for tool in tools], expected_names)
        self.assertEqual(len(tools), 12)
        forbidden = {
            "database",
            "database_path",
            "memory_database",
            "index_database",
            "project",
            "project_key",
            "project_path",
            "filesystem_path",
        }
        for tool in tools:
            properties = set(tool.inputSchema.get("properties", {}))
            self.assertFalse(properties.intersection(forbidden), (tool.name, properties))
            definition = TOOL_DEFINITIONS_BY_NAME[tool.name]
            self.assertEqual(bool(tool.annotations.readOnlyHint), definition.read_only, tool.name)
            self.assertEqual(bool(tool.annotations.destructiveHint), definition.destructive, tool.name)

        _, capabilities = asyncio.run(server.call_tool("ue_get_capabilities", {}))
        self.assertEqual(capabilities["server"]["mode"], "fixed-project-memory")
        memory_contract = capabilities["projectMemory"]
        self.assertTrue(memory_contract["configured"])
        self.assertTrue(memory_contract["persistent"])
        self.assertEqual(memory_contract["projectKey"], "测试项目")
        self.assertEqual(memory_contract["tools"], expected_names[5:])
        self.assertFalse(memory_contract["arbitraryDatabaseArguments"])
        self.assertFalse(memory_contract["arbitraryProjectArguments"])
        self.assertFalse(memory_contract["vectorDatabase"])
        self.assertFalse(memory_contract["workflowEvidenceHandoff"])
        self.assertEqual(memory_contract["workflowEvidenceSourceTool"], "")
        self.assertEqual(capabilities["limits"]["memorySearchResults"], 100)

        _, project_status = asyncio.run(server.call_tool("ue_get_project_status", {}))
        self.assertEqual(project_status["serverMode"], "fixed-project-memory")
        self.assertTrue(project_status["project"]["fixedProject"])
        self.assertTrue(project_status["projectMemory"]["configured"])
        self.assertEqual(project_status["projectMemory"]["recordCount"], 0)

        _, rule = asyncio.run(
            server.call_tool(
                "ue_memory_add_rule",
                {
                    "subject_key": "rule:code:newline",
                    "title": "Text files use CRLF",
                    "body": "All tracked text files use UTF-8 without BOM and CRLF.",
                    "source_ref": "conversation:user-confirmed",
                    "scopes": [
                        {
                            "scopeType": "project",
                            "scopeKey": "测试项目",
                        }
                    ],
                },
            )
        )
        self.assertTrue(rule["ok"])
        rule_record = rule["record"]
        self.assertEqual(rule_record["projectKey"], "测试项目")
        self.assertEqual(rule_record["recordType"], "projectRule")
        self.assertEqual(rule_record["sourceKind"], "user-confirmed")
        self.assertEqual(rule_record["status"], "valid")

        _, finding = asyncio.run(
            server.call_tool(
                "ue_memory_record_finding",
                {
                    "record_type": "projectFact",
                    "subject_key": "asset:player:revision",
                    "title": "Player asset revision",
                    "body": "The player asset matches the indexed revision.",
                    "source_kind": "tool-observed",
                    "source_ref": "sqlite:index",
                    "revision_set": [
                        {
                            "assetPath": ASSET_A,
                            "revision": f"sha256:{REVISION_A}",
                            "revisionStable": True,
                        }
                    ],
                },
            )
        )
        self.assertTrue(finding["ok"])
        finding_record = finding["record"]
        self.assertEqual(finding_record["sourceKind"], "tool-observed")
        self.assertEqual(finding_record["status"], "valid")

        _, task = asyncio.run(
            server.call_tool(
                "ue_memory_record_task",
                {
                    "task_key": "player-revision-validation",
                    "title": "Validate player revision",
                    "conclusion": "The player asset revision was validated and retained.",
                    "outcome": "succeeded",
                    "patch_ref": "patch:player_revision",
                    "backup_manifest_ref": "backup-manifest:player_revision",
                    "validation_evidence_ref": "validation-evidence:player_revision",
                    "revision_set": [
                        {
                            "assetPath": ASSET_A,
                            "revision": f"sha256:{REVISION_A}",
                            "revisionStable": True,
                        }
                    ],
                    "scopes": [
                        {
                            "scopeType": "asset",
                            "scopeKey": ASSET_A,
                        }
                    ],
                    "patch_details": {"operationCount": 1},
                    "backup_manifest_details": {"backupVerified": True},
                    "validation_evidence_details": {"result": "passed"},
                },
            )
        )
        self.assertTrue(task["ok"])
        task_record = task["record"]
        self.assertEqual(task_record["recordType"], "taskRecord")
        self.assertEqual(task_record["subjectKey"], "task:player-revision-validation")
        self.assertEqual(task_record["details"]["taskOutcome"], "succeeded")
        self.assertEqual(
            [artifact["artifactKind"] for artifact in task_record["artifacts"]],
            ["patch", "backupManifest", "validationEvidence"],
        )
        self.assertEqual(task_record["status"], "valid")

        _, invalid_task = asyncio.run(
            server.call_tool(
                "ue_memory_record_task",
                {
                    "task_key": "invalid-path",
                    "title": "Invalid task",
                    "conclusion": "This task must be rejected.",
                    "outcome": "failed",
                    "patch_ref": "C:\\Temp\\patch.json",
                    "backup_manifest_ref": "backup-manifest:invalid",
                    "validation_evidence_ref": "validation-evidence:invalid",
                    "revision_set": [
                        {
                            "assetPath": ASSET_A,
                            "revision": f"sha256:{REVISION_A}",
                            "revisionStable": True,
                        }
                    ],
                },
            )
        )
        self.assertFalse(invalid_task["ok"])
        self.assertEqual(invalid_task["error"]["code"], "invalid-arguments")

        _, search = asyncio.run(
            server.call_tool(
                "ue_memory_search",
                {"query": "CRLF", "record_types": ["projectRule"]},
            )
        )
        self.assertEqual(search["resultCount"], 1)
        self.assertEqual(search["items"][0]["record"]["recordId"], rule_record["recordId"])
        _, empty_statuses = asyncio.run(
            server.call_tool(
                "ue_memory_search",
                {"query": "CRLF", "statuses": []},
            )
        )
        self.assertFalse(empty_statuses["ok"])
        self.assertEqual(empty_statuses["error"]["code"], "invalid-arguments")

        _, fetched = asyncio.run(
            server.call_tool("ue_memory_get", {"record_id": finding_record["recordId"]})
        )
        self.assertEqual(
            fetched["record"]["revisionSet"][0]["revision"],
            f"sha256:{REVISION_A}",
        )
        self.assertRegex(fetched["record"]["evidenceSha256"], r"^sha256:[0-9a-f]{64}$")

        _, validated = asyncio.run(server.call_tool("ue_memory_validate", {}))
        self.assertTrue(validated["ok"])
        self.assertIn(finding_record["recordId"], validated["checkedRecordIds"])
        self.assertIn(task_record["recordId"], validated["checkedRecordIds"])
        self.assertEqual(validated["staleRecordIds"], [])

        _, replacement = asyncio.run(
            server.call_tool(
                "ue_memory_record_finding",
                {
                    "record_type": "knownIssue",
                    "subject_key": "issue:test",
                    "title": "Replacement issue",
                    "body": "Replacement issue state.",
                },
            )
        )
        _, original = asyncio.run(
            server.call_tool(
                "ue_memory_record_finding",
                {
                    "record_type": "knownIssue",
                    "subject_key": "issue:test",
                    "title": "Original issue",
                    "body": "Original issue state.",
                },
            )
        )
        _, superseded = asyncio.run(
            server.call_tool(
                "ue_memory_mark_superseded",
                {
                    "record_id": original["record"]["recordId"],
                    "replacement_record_id": replacement["record"]["recordId"],
                    "reason": "issue-state-updated",
                },
            )
        )
        self.assertEqual(superseded["record"]["status"], "superseded")
        self.assertEqual(
            superseded["record"]["supersededByRecordId"],
            replacement["record"]["recordId"],
        )

        _, missing = asyncio.run(
            server.call_tool("ue_memory_get", {"record_id": "mem_00000000000000000000000000000000"})
        )
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["error"]["code"], "memory-record-not-found")

        with self.assertRaisesRegex(ValueError, "same fixed project"):
            create_mcp_server(
                self.database_path,
                memory_service=ProjectMemoryService(
                    database_path=self.temp_root / "other-memory.sqlite3",
                    project_key="OtherProject",
                ),
            )

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_live_editor_mode_registers_bounded_read_tools(self) -> None:
        live_service = FakeLiveEditorService()
        server = create_mcp_server(self.database_path, live_editor_service=live_service)
        tools = asyncio.run(server.list_tools())
        expected_names = tool_names_for_mode(live_editor_enabled=True)
        self.assertEqual([tool.name for tool in tools], expected_names)
        forbidden = {
            "address",
            "port",
            "auth_token",
            "endpoint",
            "project",
            "project_path",
            "filesystem_path",
            "uobject",
            "console",
            "python",
            "shell",
        }
        for tool in tools:
            properties = set(tool.inputSchema.get("properties", {}))
            self.assertFalse(properties.intersection(forbidden), (tool.name, properties))
            definition = TOOL_DEFINITIONS_BY_NAME[tool.name]
            self.assertEqual(tool.annotations.readOnlyHint, definition.read_only, tool.name)
            self.assertEqual(tool.annotations.destructiveHint, definition.destructive, tool.name)

        _, capabilities = asyncio.run(server.call_tool("ue_get_capabilities", {}))
        self.assertTrue(capabilities["liveEditor"]["configured"])
        self.assertEqual(capabilities["liveEditor"]["transport"], "localhost-tcp")
        self.assertEqual(capabilities["liveEditor"]["tools"], expected_names[5:])
        self.assertFalse(capabilities["liveEditor"]["arbitraryEndpointArguments"])
        self.assertFalse(capabilities["liveEditor"]["arbitraryUObject"])
        graph_contract = capabilities["liveEditor"]["graphSelection"]
        self.assertTrue(graph_contract["available"])
        self.assertEqual(graph_contract["scope"], "ordinary-blueprint-editor")
        self.assertEqual(graph_contract["maxSelectedNodes"], 100)
        self.assertFalse(graph_contract["materialEditorSupported"])
        self.assertFalse(graph_contract["editingSupported"])
        action_contract = capabilities["liveEditor"]["editorActions"]
        self.assertTrue(action_contract["available"])
        self.assertEqual(action_contract["tools"], expected_names[15:23])
        self.assertFalse(action_contract["saveSupported"])
        self.assertFalse(action_contract["pieSupported"])
        self.assertEqual(action_contract["actorIdentity"], "current-editor-world-actor-guid")
        self.assertEqual(action_contract["folderValidationMaxAssets"], 500)
        self.assertEqual(action_contract["returnedValidationIssueLimit"], 200)
        self.assertTrue(action_contract["automationExactTestNameOnly"])
        self.assertTrue(action_contract["automationSingleParticipantOnly"])
        self.assertEqual(action_contract["automationTimeoutSecondsMax"], 300)
        self.assertEqual(action_contract["automationReturnedEntryLimit"], 200)
        self.assertEqual(action_contract["validationEvidenceSchemaVersion"], "1.0")
        self.assertTrue(action_contract["validationEvidenceProjectBound"])
        self.assertTrue(action_contract["validationEvidenceRevisionSetBound"])
        self.assertEqual(action_contract["automationRevisionCoverage"], "not-applicable")
        self.assertTrue(capabilities["freshness"]["liveEditorMemorySeparate"])

        _, project_status = asyncio.run(server.call_tool("ue_get_project_status", {}))
        self.assertTrue(project_status["project"]["fixedProject"])
        self.assertEqual(project_status["project"]["projectName"], "TestProject")
        self.assertEqual(project_status["liveEditor"]["state"], "available")

        _, editor_status = asyncio.run(server.call_tool("ue_editor_status", {}))
        self.assertTrue(editor_status["ok"])
        self.assertEqual(editor_status["result"]["state"], "available")
        _, selection = asyncio.run(server.call_tool("ue_get_selection", {}))
        self.assertEqual(selection["source"], "live-editor-memory")
        self.assertEqual(selection["result"]["items"][0]["kind"], "Actor")

        _, output_log = asyncio.run(
            server.call_tool(
                "ue_get_output_log",
                {
                    "category": "LogTest",
                    "minimum_verbosity": "warning",
                    "keyword": "compile",
                    "since_sequence": 10,
                    "pie_session_id": 2,
                    "limit": 25,
                },
            )
        )
        self.assertEqual(output_log["result"]["nextSequence"], 12)
        self.assertEqual(live_service.calls[-1][0], "ue_get_output_log")
        self.assertEqual(live_service.calls[-1][1]["minimumVerbosity"], "warning")
        self.assertEqual(live_service.calls[-1][1]["sinceSequence"], 10)

        _, compile_errors = asyncio.run(
            server.call_tool(
                "ue_get_compile_errors",
                {
                    "asset_path": "/Game/Test/BP_Test.BP_Test",
                    "since_sequence": 4,
                    "pie_session_id": -1,
                    "limit": 30,
                },
            )
        )
        self.assertFalse(compile_errors["result"]["historyComplete"])
        self.assertEqual(live_service.calls[-1][1]["assetPath"], "/Game/Test/BP_Test.BP_Test")

        _, live_asset = asyncio.run(
            server.call_tool(
                "ue_inspect_asset_live",
                {"asset_path": "/Game/Test/BP_Test.BP_Test"},
            )
        )
        self.assertTrue(live_asset["result"]["assetRegistry"]["found"])
        self.assertFalse(live_asset["result"]["memory"]["loadedByBridge"])

        graph_tool = next(tool for tool in tools if tool.name == "ue_get_blueprint_graph_selection")
        self.assertEqual(graph_tool.inputSchema.get("properties", {}), {})
        _, graph_selection = asyncio.run(
            server.call_tool("ue_get_blueprint_graph_selection", {})
        )
        self.assertTrue(graph_selection["result"]["available"])
        self.assertEqual(
            graph_selection["result"]["graph"]["graphGuid"],
            "11111111-1111-1111-1111-111111111111",
        )
        self.assertEqual(
            graph_selection["result"]["selectedNodes"][0]["nodeGuid"],
            "22222222-2222-2222-2222-222222222222",
        )

        _, opened = asyncio.run(
            server.call_tool("ue_open_asset", {"asset_path": "/Game/Test/BP_Test.BP_Test"})
        )
        self.assertTrue(opened["result"]["openedNewEditor"])
        self.assertFalse(opened["readOnly"])
        self.assertEqual(live_service.calls[-1][1]["assetPath"], "/Game/Test/BP_Test.BP_Test")

        _, focused_actor = asyncio.run(
            server.call_tool(
                "ue_focus_actor",
                {"actor_guid": "33333333-3333-3333-3333-333333333333"},
            )
        )
        self.assertTrue(focused_actor["result"]["viewportFocused"])
        self.assertEqual(
            live_service.calls[-1][1]["actorGuid"],
            "33333333-3333-3333-3333-333333333333",
        )

        _, compiled = asyncio.run(
            server.call_tool("ue_compile_blueprint", {"asset_path": "/Game/Test/BP_Test.BP_Test"})
        )
        self.assertTrue(compiled["result"]["succeeded"])
        self.assertFalse(compiled["result"]["saved"])

        _, validated = asyncio.run(
            server.call_tool(
                "ue_validate_folder",
                {
                    "package_path": "/Game/Test",
                    "recursive": False,
                    "max_assets": 20,
                    "max_issues": 30,
                },
            )
        )
        self.assertEqual(validated["result"]["matchedAssetCount"], 2)
        self.assertEqual(validated["result"]["validationEvidence"]["scope"], "folder")
        self.assertEqual(len(validated["result"]["validationEvidence"]["revisionSet"]), 2)
        self.assertEqual(live_service.calls[-1][1]["packagePath"], "/Game/Test")
        self.assertEqual(live_service.calls[-1][1]["maxAssets"], 20)

        _, automation = asyncio.run(
            server.call_tool(
                "ue_run_automation_test",
                {
                    "test_name": "UEAgentKit.EditorBridge.LiveActionSmoke",
                    "timeout_seconds": 45,
                    "max_entries": 25,
                },
            )
        )
        self.assertTrue(automation["result"]["successful"])
        self.assertEqual(automation["result"]["validationEvidence"]["revisionCoverage"], "not-applicable")
        self.assertEqual(live_service.calls[-1][0], "ue_run_automation_test")
        self.assertEqual(live_service.calls[-1][1]["testName"], "UEAgentKit.EditorBridge.LiveActionSmoke")
        self.assertEqual(live_service.calls[-1][1]["timeoutSeconds"], 45)
        self.assertEqual(live_service.calls[-1][1]["maxEntries"], 25)

        offline_server = create_mcp_server(
            self.database_path,
            live_editor_service=FakeLiveEditorService(available=False),
        )
        _, offline_status = asyncio.run(offline_server.call_tool("ue_editor_status", {}))
        self.assertTrue(offline_status["ok"])
        self.assertEqual(offline_status["result"]["state"], "unavailable")
        _, offline_selection = asyncio.run(offline_server.call_tool("ue_get_selection", {}))
        self.assertFalse(offline_selection["ok"])
        self.assertEqual(offline_selection["error"]["code"], "live-editor-unavailable")
        self.assertTrue(offline_selection["error"]["retryable"])

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_workflow_memory_mode_hands_off_verified_evidence(self) -> None:
        memory_service = ProjectMemoryService(
            database_path=self.temp_root / "workflow-memory.sqlite3",
            project_key="测试项目",
        )
        server = create_mcp_server(
            self.database_path,
            workflow_service=FakeWorkflowService(),
            memory_service=memory_service,
        )
        tools = asyncio.run(server.list_tools())
        self.assertEqual(
            [tool.name for tool in tools],
            tool_names_for_mode(workflow_enabled=True, memory_enabled=True),
        )
        self.assertEqual(len(tools), 32)

        _, capabilities = asyncio.run(server.call_tool("ue_get_capabilities", {}))
        memory_contract = capabilities["projectMemory"]
        self.assertTrue(memory_contract["workflowEvidenceHandoff"])
        self.assertEqual(memory_contract["workflowEvidenceSourceTool"], "ue_verify_asset")
        self.assertEqual(memory_contract["workflowEvidenceTargetTool"], "ue_memory_record_task")
        self.assertEqual(
            memory_contract["workflowEvidenceArgumentsPath"],
            "memoryTaskEvidence.arguments",
        )

        _, verified = asyncio.run(
            server.call_tool("ue_verify_asset", {"apply_receipt": "apply_test"})
        )
        evidence = verified["memoryTaskEvidence"]
        self.assertEqual(evidence["tool"], "ue_memory_record_task")
        arguments = evidence["arguments"]
        self.assertEqual(arguments["validation_evidence_ref"], "validation-evidence:report_verify_test")
        self.assertNotIn("apply_receipt", arguments)
        self.assertNotIn("database", json.dumps(arguments, ensure_ascii=False).lower())

        _, recorded = asyncio.run(server.call_tool(evidence["tool"], arguments))
        self.assertTrue(recorded["ok"])
        record = recorded["record"]
        self.assertEqual(record["recordType"], "taskRecord")
        self.assertEqual(record["subjectKey"], "task:patch:plan_test")
        self.assertEqual(record["status"], "valid")
        self.assertEqual(
            [artifact["artifactKind"] for artifact in record["artifacts"]],
            ["patch", "backupManifest", "validationEvidence"],
        )

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_combined_live_and_workflow_mode_has_exact_tool_order(self) -> None:
        workflow_service = FakeWorkflowService()
        live_service = FakeLiveEditorService()
        server = create_mcp_server(
            self.database_path,
            workflow_service=workflow_service,
            live_editor_service=live_service,
        )
        tools = asyncio.run(server.list_tools())
        expected_names = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)
        self.assertEqual([tool.name for tool in tools], expected_names)
        self.assertEqual(len(tools), 43)
        for tool in tools:
            definition = TOOL_DEFINITIONS_BY_NAME[tool.name]
            self.assertEqual(bool(tool.annotations.readOnlyHint), definition.read_only, tool.name)
            self.assertEqual(bool(tool.annotations.destructiveHint), definition.destructive, tool.name)
            self.assertEqual(bool(tool.annotations.idempotentHint), definition.idempotent, tool.name)

        mismatched_live = FakeLiveEditorService()
        mismatched_live.config = SimpleNamespace(
            project_path=Path("C:/Projects/OtherProject/OtherProject.uproject"),
            project_name="OtherProject",
        )
        with self.assertRaisesRegex(ValueError, "same fixed project"):
            create_mcp_server(
                self.database_path,
                workflow_service=workflow_service,
                live_editor_service=mismatched_live,
            )

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_full_workflow_registers_status_query_and_workflow_tools(self) -> None:
        server = create_mcp_server(self.database_path, workflow_service=FakeWorkflowService())
        tools = asyncio.run(server.list_tools())
        self.assertEqual(
            [tool.name for tool in tools],
            tool_names_for_mode(workflow_enabled=True),
        )
        forbidden = {"database", "project", "project_path", "engine_root", "policy", "revision_export", "work_root", "backup_root", "command"}
        for tool in tools:
            properties = set(tool.inputSchema.get("properties", {}))
            self.assertFalse(properties.intersection(forbidden), (tool.name, properties))
        high_level_tool = next(tool for tool in tools if tool.name == "ue_set_asset_property")
        self.assertIn("mode", high_level_tool.inputSchema["properties"])
        self.assertEqual(high_level_tool.inputSchema["properties"]["mode"]["default"], "Plan")
        self.assertFalse(high_level_tool.annotations.destructiveHint)
        self.assertFalse(high_level_tool.annotations.readOnlyHint)
        material_tool = next(tool for tool in tools if tool.name == "ue_set_material_parameter")
        self.assertIn("parameter_type", material_tool.inputSchema["properties"])

        apply_tool = next(tool for tool in tools if tool.name == "ue_apply_patch")
        self.assertTrue(apply_tool.annotations.destructiveHint)
        self.assertFalse(apply_tool.annotations.readOnlyHint)
        state_tool = next(tool for tool in tools if tool.name == "ue_get_asset_state")
        self.assertEqual(set(state_tool.inputSchema["properties"]), {"asset_path"})
        self.assertTrue(state_tool.annotations.readOnlyHint)
        self.assertFalse(state_tool.annotations.destructiveHint)
        refresh_tool = next(tool for tool in tools if tool.name == "ue_refresh_asset_index")
        self.assertEqual(set(refresh_tool.inputSchema["properties"]), {"asset_path", "mode"})
        self.assertFalse(refresh_tool.annotations.readOnlyHint)
        self.assertFalse(refresh_tool.annotations.destructiveHint)
        verify_tool = next(tool for tool in tools if tool.name == "ue_verify_asset")
        self.assertFalse(verify_tool.annotations.readOnlyHint)
        self.assertFalse(verify_tool.annotations.destructiveHint)

        _, capabilities = asyncio.run(server.call_tool("ue_get_capabilities", {}))
        self.assertEqual(capabilities["server"]["mode"], "fixed-project-commit")
        self.assertTrue(capabilities["operations"]["available"])
        self.assertTrue(capabilities["freshness"]["available"])
        self.assertTrue(capabilities["freshness"]["planRequiresFreshIndex"])
        self.assertTrue(capabilities["highLevelChanges"]["available"])
        self.assertEqual(capabilities["highLevelChanges"]["defaultMode"], "Plan")
        self.assertFalse(capabilities["highLevelChanges"]["commitSupportedDirectly"])
        self.assertEqual(len(capabilities["highLevelChanges"]["tools"]), 12)
        self.assertTrue(capabilities["assetState"]["available"])
        self.assertEqual(
            capabilities["assetState"]["sources"],
            ["editor-memory", "disk-package", "revision-export", "sqlite"],
        )
        self.assertFalse(capabilities["assetState"]["memoryRevisionAvailable"])
        self.assertFalse(capabilities["assetState"]["memoryCleanIsRevisionProof"])
        self.assertTrue(capabilities["snapshotRefresh"]["available"])
        self.assertEqual(capabilities["snapshotRefresh"]["modes"], ["Preview", "Apply"])
        self.assertTrue(capabilities["snapshotRefresh"]["pairedGeneration"])
        self.assertTrue(capabilities["snapshotRefresh"]["restartRequiredAfterApply"])
        self.assertGreater(len(capabilities["operations"]["items"]), 0)
        _, project_status = asyncio.run(server.call_tool("ue_get_project_status", {}))
        self.assertEqual(project_status["project"]["projectName"], "TestProject")
        self.assertEqual(project_status["engine"]["state"], "unknown")
        self.assertEqual(project_status["freshness"]["state"], "fresh")
        self.assertTrue(project_status["freshness"]["indexFresh"])

        high_level_cases = [
            ("ue_set_blueprint_default", {"asset_path": ASSET_A, "variable_name": "Health", "value": 10}, "setVariableDefault"),
            ("ue_set_component_property", {"asset_path": ASSET_A, "component_name": "Root", "property_path": "Mobility", "value": "Movable"}, "setComponentProperty"),
            ("ue_set_pin_default", {"asset_path": ASSET_A, "graph_guid": "11111111-1111-1111-1111-111111111111", "node_guid": "22222222-2222-2222-2222-222222222222", "pin_name": "Value", "value": "1"}, "setPinDefault"),
            ("ue_set_asset_property", {"asset_path": GENERIC_ASSET, "property_path": "BoolValue", "value": True}, "setAssetProperty"),
            ("ue_set_asset_reference_property", {"asset_path": GENERIC_ASSET, "property_path": "ObjectValue", "value": {"referenceType": "Object", "path": "/Game/UEAgentKitTests/T_Test.T_Test"}}, "setAssetReferenceProperty"),
            ("ue_set_asset_structured_property", {"asset_path": GENERIC_ASSET, "property_path": "ArrayValue", "value": {"valueType": "Array", "items": [1, 2]}}, "setAssetStructuredProperty"),
            ("ue_set_material_parameter", {"asset_path": GENERIC_ASSET, "parameter_name": "Roughness", "parameter_type": "Scalar", "value": 0.5}, "setMaterialInstanceScalarParameter"),
            ("ue_set_datatable_cell", {"asset_path": GENERIC_ASSET, "row_name": "Default", "field_name": "Value", "value": 7}, "setDataTableCell"),
            ("ue_set_datatable_row_fields", {"asset_path": GENERIC_ASSET, "row_name": "Default", "values": {"Value": 7, "Enabled": True}}, "setDataTableRowFields"),
            ("ue_add_datatable_row", {"asset_path": GENERIC_ASSET, "row_name": "Added", "values": {"Value": 7}}, "addDataTableRow"),
            ("ue_remove_datatable_row", {"asset_path": GENERIC_ASSET, "row_name": "Default"}, "removeDataTableRow"),
            ("ue_rename_datatable_row", {"asset_path": GENERIC_ASSET, "row_name": "Default", "new_row_name": "Renamed"}, "renameDataTableRow"),
        ]
        for tool_name, arguments, operation in high_level_cases:
            _, high_level = asyncio.run(server.call_tool(tool_name, arguments))
            self.assertTrue(high_level["ok"], high_level)
            self.assertEqual(high_level["mode"], "Plan")
            self.assertEqual(high_level["underlyingOperation"], operation)

        _, asset_state = asyncio.run(
            server.call_tool(
                "ue_get_asset_state",
                {"asset_path": "/Game/UEAgentKitWriteTests/Test.Test"},
            )
        )
        self.assertEqual(asset_state["state"], "synchronized")
        self.assertFalse(asset_state["sources"]["memory"]["revisionAvailable"])

        _, refresh_preview = asyncio.run(
            server.call_tool(
                "ue_refresh_asset_index",
                {"asset_path": "/Game/UEAgentKitWriteTests/Test.Test", "mode": "Preview"},
            )
        )
        self.assertTrue(refresh_preview["ok"])
        self.assertFalse(refresh_preview["applied"])

        _, high_level_dry = asyncio.run(
            server.call_tool(
                "ue_set_asset_property",
                {"asset_path": GENERIC_ASSET, "property_path": "BoolValue", "value": True, "mode": "DryRun"},
            )
        )
        self.assertEqual(high_level_dry["dryRunReceipt"], "dry_test")

        _, planned = asyncio.run(
            server.call_tool(
                "ue_plan_patch",
                {
                    "asset_path": ASSET_A,
                    "operation": "setBlueprintDescription",
                    "target": {},
                    "value": "MCP",
                },
            )
        )
        self.assertTrue(planned["ok"])
        _, applied = asyncio.run(
            server.call_tool(
                "ue_apply_patch",
                {"plan_id": "plan_test", "dry_run_receipt": "dry_test", "confirmation": "COMMIT plan_test"},
            )
        )
        self.assertEqual(applied["applyReceipt"], "apply_test")

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_workflow_diagnostics_use_stable_redacted_error_envelope(self) -> None:
        class CrashingWorkflow(FakeWorkflowService):
            def prepare_high_level_change(self, **kwargs):
                del kwargs
                raise WorkflowError(
                    "ue-process-crashed",
                    "The Unreal workflow process crashed.",
                    details={
                        "stage": "patch-dry-run",
                        "diagnosticId": "diag_test",
                        "reportId": "report_test",
                        "stderrTail": "Fatal error: <configured-path>",
                    },
                )

        server = create_mcp_server(self.database_path, workflow_service=CrashingWorkflow())
        _, payload = asyncio.run(
            server.call_tool(
                "ue_set_asset_property",
                {"asset_path": GENERIC_ASSET, "property_path": "BoolValue", "value": True, "mode": "DryRun"},
            )
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "ue-process-crashed")
        self.assertFalse(payload["error"]["retryable"])
        self.assertEqual(payload["error"]["details"]["diagnosticId"], "diag_test")
        self.assertEqual(payload["error"]["details"]["reportId"], "report_test")
        self.assertIn("new Plan", payload["error"]["suggestedAction"])

    def test_active_sqlite_sidecar_is_rejected(self) -> None:
        sidecar = Path(str(self.database_path) + "-wal")
        sidecar.write_bytes(b"active-writer")
        with self.assertRaisesRegex(RuntimeError, "active SQLite sidecar"):
            self.service.check()

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_returns_quiescent_snapshot_error(self) -> None:
        server = create_mcp_server(self.database_path)
        sidecar = Path(str(self.database_path) + "-wal")
        sidecar.write_bytes(b"active-writer")
        content, payload = asyncio.run(server.call_tool("ue_search", {"query": "Actor"}))
        self.assertEqual(len(content), 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "index-not-quiescent")
        self.assertTrue(payload["error"]["retryable"])
        self.assertIn("SQLite writer", payload["error"]["suggestedAction"])
        self.assertNotIn(str(self.database_path), json.dumps(payload, ensure_ascii=False))

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_runtime_database_loss_returns_sanitized_error(self) -> None:
        database_text = str(self.database_path)
        server = create_mcp_server(self.database_path)
        self.database_path.unlink()
        content, payload = asyncio.run(server.call_tool("ue_search", {"query": "Actor"}))
        self.assertEqual(len(content), 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "database-not-found")
        self.assertEqual(
            payload["error"]["message"],
            "The configured UE Agent Kit database was not found.",
        )
        self.assertNotIn(database_text, json.dumps(payload, ensure_ascii=False))

    def test_mcp_packaging_and_stdio_boundary_are_explicit(self) -> None:
        pyproject = (TOOL_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (TOOL_ROOT / "requirements-mcp.txt").read_text(encoding="utf-8")
        server_source = (SRC_ROOT / "ue_agent_kit" / "mcp_server.py").read_text(encoding="utf-8")
        setup_source = (TOOL_ROOT / "scripts" / "setup_python.ps1").read_text(encoding="utf-8")
        runner_source = (TOOL_ROOT / "scripts" / "RunMcp.ps1").read_text(encoding="utf-8")

        self.assertIn(f'version = "{__version__}"', pyproject)
        self.assertIn('mcp = ["mcp>=1.27,<2"]', pyproject)
        self.assertIn("mcp>=1.27,<2", requirements)
        self.assertIn('server.run(transport="stdio")', server_source)
        service_source = (SRC_ROOT / "ue_agent_kit" / "agent_api.py").read_text(encoding="utf-8")
        database_source = (SRC_ROOT / "ue_agent_kit" / "database.py").read_text(encoding="utf-8")
        self.assertIn("immutable=True", service_source)
        self.assertIn("active SQLite sidecar", service_source)
        self.assertIn("immutable=1", database_source)
        self.assertNotIn('transport="streamable-http"', server_source)
        self.assertNotIn(".sse_app(", server_source)
        self.assertIn("[switch]$WithMcp", setup_source)
        self.assertIn("requirements-mcp.txt", setup_source)
        self.assertIn("Do not write informational text to stdout", runner_source)
        self.assertNotIn("Write-Host", runner_source)
        integration_source = (
            TOOL_ROOT / "tests" / "integration" / "mcp_stdio_smoke.py"
        ).read_text(encoding="utf-8")
        integration_runner = (TOOL_ROOT / "scripts" / "TestMcpStdio.ps1").read_text(encoding="utf-8")
        for token in (
            "StdioServerParameters",
            "ClientSession",
            "databaseHashUnchanged",
            "indexDirectoryUnchanged",
            "invalidArgumentsRejected",
        ):
            self.assertIn(token, integration_source)
        self.assertIn("mcp_stdio_smoke.py", integration_runner)

        compatibility_source = (
            TOOL_ROOT / "tests" / "integration" / "mcp_client_compatibility.py"
        ).read_text(encoding="utf-8")
        compatibility_runner = (TOOL_ROOT / "scripts" / "TestMcpClients.ps1").read_text(encoding="utf-8")
        for token in (
            "RawJsonRpcClient",
            "officialPythonClient",
            "rawJsonRpcClient",
            "claudeCodeContract",
            "chatGptProtocolContract",
            "structuredContent",
            "fixedConfigurationHidden",
        ):
            self.assertIn(token, compatibility_source)
        self.assertIn("mcp_client_compatibility.py", compatibility_runner)

        live_integration_source = (
            TOOL_ROOT / "tests" / "integration" / "mcp_live_editor_smoke.py"
        ).read_text(encoding="utf-8")
        live_integration_runner = (TOOL_ROOT / "scripts" / "TestMcpLiveEditor.ps1").read_text(encoding="utf-8")
        for token in (
            "ue_editor_status",
            "ue_get_selection",
            "ue_get_open_assets",
            "ue_get_dirty_assets",
            "ue_get_current_level",
            "ue_get_pie_state",
            "ue_get_output_log",
            "ue_get_compile_errors",
            "ue_inspect_asset_live",
            "ue_get_blueprint_graph_selection",
            "liveAssetLoadedByBridge",
            "compileHistoryComplete",
            "secretsRedacted",
            "databaseHashUnchanged",
        ):
            self.assertIn(token, live_integration_source)
        self.assertIn("mcp_live_editor_smoke.py", live_integration_runner)
        self.assertIn("[switch]$EnableLiveEditor", runner_source)
        self.assertIn("--enable-live-editor", server_source)
        self.assertIn("[switch]$EnableProjectMemory", runner_source)
        self.assertIn("--enable-project-memory", runner_source)
        self.assertIn("--enable-project-memory", server_source)
        self.assertIn("UEAK_MEMORY_DATABASE", (SRC_ROOT / "ue_agent_kit" / "config.py").read_text(encoding="utf-8"))

        example = json.loads(
            (TOOL_ROOT / "examples" / "mcp" / "claude-code.example.json").read_text(encoding="utf-8")
        )
        config = example["mcpServers"]["ue-agent-kit"]
        self.assertEqual(config["type"], "stdio")
        self.assertEqual(config["command"], "powershell.exe")
        self.assertIn("RunMcp.ps1", " ".join(config["args"]))
        self.assertIn("-Database", config["args"])
        self.assertNotIn("-ProjectPath", config["args"])
        self.assertNotIn("-Policy", config["args"])


if __name__ == "__main__":
    unittest.main()

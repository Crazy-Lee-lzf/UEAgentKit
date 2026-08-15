from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = Path(__file__).resolve().parent
for root in (SRC_ROOT, TESTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from test_indexer_queries import make_asset, write_export  # noqa: E402
from ue_agent_kit.active_work import WorkItemDraft  # noqa: E402
from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.agent_workflow import WorkflowError  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.freshness import IndexFreshnessTracker  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemorySourceKind,
)
from ue_agent_kit.task_context import (  # noqa: E402
    MAX_TASK_CONTEXT_ASSETS,
    TaskContextService,
)

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None
if MCP_AVAILABLE:
    from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402
    from test_mcp_server import FakeLiveEditorService, FakeWorkflowService  # noqa: E402

PROJECT = "测试项目"
ASSET_A = "/Game/Characters/BP_TestActor.BP_TestActor"
ASSET_B = "/Game/Other/BP_SecondActor.BP_SecondActor"
PACKAGE_A = "/Game/Characters/BP_TestActor"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class FakeLiveEditor:
    """Duck-typed Live Editor Bridge with a controllable aggregated editor context."""

    def __init__(
        self,
        *,
        available: bool = True,
        dirty_packages: tuple[str, ...] = (),
        open_assets: tuple[str, ...] = (),
        session_id: str = "session-test",
    ) -> None:
        self.available = available
        self.dirty_packages = list(dirty_packages)
        self.open_assets = list(open_assets)
        self.session_id = session_id
        self.config = SimpleNamespace(project_name="TestProject")
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def call_tool(self, tool_name: str, params: dict[str, Any] | None = None):
        if tool_name != "ue_get_editor_context":
            raise AssertionError(f"unexpected tool call: {tool_name}")
        self.calls.append((tool_name, params or {}))
        if not self.available:
            raise LiveEditorError("live-editor-unavailable", "The fixed Editor is offline.")
        return {
            "schemaVersion": "1.0",
            "tool": tool_name,
            "ok": True,
            "readOnly": True,
            "source": "live-editor-memory",
            "result": {
                "source": "live-editor-memory",
                "state": "available",
                "editor": {
                    "state": "available",
                    "sessionId": self.session_id,
                    "pieState": "stopped",
                    "dirtyPackageCount": len(self.dirty_packages),
                },
                "world": {"available": True, "currentLevelPath": "/Game/Maps/Test.Test:PersistentLevel"},
                "selection": {"count": 0, "truncated": False, "items": []},
                "openAssets": {
                    "count": len(self.open_assets),
                    "truncated": False,
                    "items": [{"path": path} for path in self.open_assets],
                },
                "dirtyPackages": {
                    "count": len(self.dirty_packages),
                    "truncated": False,
                    "items": [
                        {"packageName": package, "assetPaths": [package]} for package in self.dirty_packages
                    ],
                },
                "blueprintGraphSelection": {"available": False},
                "compileErrors": {"diagnosticSource": "captured-output-log", "diagnosticCount": 0},
                "outputLogCursor": {"available": False},
                "durationMs": 1,
                "stageDurationsMs": {},
                "nextActions": [],
            },
        }


class FakeChangeSetWorkflow:
    def __init__(self, *, known: dict[str, dict[str, Any]] | None = None) -> None:
        self.known = known or {}
        self.project_name = "TestProject"

    def get_change_set(self, change_set_id: str):
        if change_set_id in self.known:
            return self.known[change_set_id]
        raise WorkflowError("change-set-not-found", "Change Set not found.", details={"changeSetId": change_set_id})


class TaskContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_task_context_")
        root = Path(self.temporary.name)
        self.content_a = b"package-a-content"
        self.content_b = b"package-b-content"
        self.revision_a = sha256_bytes(self.content_a)
        self.revision_b = sha256_bytes(self.content_b)
        self.database_path = root / "index.sqlite3"
        self.export_root = root / "export"
        self.project_root = root / "TestProject"
        self.project_path = self.project_root / "TestProject.uproject"
        content_a = self.project_root / "Content" / "Characters" / "BP_TestActor.uasset"
        content_b = self.project_root / "Content" / "Other" / "BP_SecondActor.uasset"
        content_a.parent.mkdir(parents=True, exist_ok=True)
        content_b.parent.mkdir(parents=True, exist_ok=True)
        content_a.write_bytes(self.content_a)
        content_b.write_bytes(self.content_b)
        write_export(
            self.export_root,
            [
                make_asset(ASSET_A, profile="logic", revision=self.revision_a, rich=False),
                make_asset(ASSET_B, profile="logic", revision=self.revision_b, rich=False),
            ],
        )
        with open_database(self.database_path) as connection:
            result = build_index(connection, self.export_root, self.database_path)
            self.assertEqual((result.added, result.failed), (2, 0))
        self.index_service = IndexQueryService(self.database_path)
        self.freshness = IndexFreshnessTracker(self.index_service, self.project_path, self.export_root)
        self.memory_database_path = root / "memory.sqlite3"
        self.memory_service = ProjectMemoryService(database_path=self.memory_database_path, project_key=PROJECT)
        self.memory_service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="用于测试任务上下文的项目。",
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_service(self, **kwargs: Any) -> TaskContextService:
        defaults: dict[str, Any] = {
            "index_service": self.index_service,
            "memory_service": self.memory_service,
            "freshness_tracker": self.freshness,
        }
        defaults.update(kwargs)
        return TaskContextService(**defaults)

    def test_t1_single_explicit_asset_with_memory_and_live_disabled(self) -> None:
        service = self.make_service(memory_service=None)
        context = service.get_task_context(query="检查 BP_TestActor 的伤害", asset_paths=[ASSET_A])

        self.assertTrue(context["ok"])
        self.assertTrue(context["readOnly"])
        self.assertEqual(context["tool"], "ue_get_task_context")
        self.assertEqual(context["request"]["query"], "检查 BP_TestActor 的伤害")
        self.assertEqual(context["project"]["projectKey"], PROJECT)
        self.assertEqual(context["relevantAssets"], [])

        self.assertEqual(len(context["targetAssets"]), 1)
        target = context["targetAssets"][0]
        self.assertTrue(target["found"])
        self.assertEqual(target["whyIncluded"], "explicit-asset-path")
        self.assertEqual(target["source"], "immutable-sqlite-index")
        self.assertEqual(target["identity"]["asset_path"], ASSET_A)
        self.assertIn("summary", target)

        self.assertTrue(context["revisionState"]["available"])
        self.assertEqual(context["revisionState"]["overall"], "fresh")
        self.assertEqual(context["revisionState"]["assets"][ASSET_A]["state"], "fresh")
        self.assertNotEqual(context["revisionState"]["assets"][ASSET_A]["diskRevision"], "")

        self.assertFalse(context["memory"]["available"])
        self.assertEqual(context["memory"]["reason"], "memory-disabled")
        self.assertFalse(context["activeWork"]["included"])
        self.assertFalse(context["liveEditor"]["available"])
        self.assertEqual(context["liveEditor"]["reason"], "live-editor-disabled")
        self.assertFalse(context["changeSet"]["requested"])

        self.assertEqual(context["riskSummary"]["highCount"], 0)
        self.assertFalse(context["outputBudget"]["truncated"])
        self.assertIn("memory", [item["section"] for item in context["degradedSources"]])
        self.assertIn("liveEditor", [item["section"] for item in context["degradedSources"]])
        self.assertIn("activeWork", [item["section"] for item in context["degradedSources"]])

    def test_t2_multiple_explicit_assets(self) -> None:
        service = self.make_service()
        context = service.get_task_context(query="检查角色和载具", asset_paths=[ASSET_A, ASSET_B])
        paths = [item["assetPath"] for item in context["targetAssets"]]
        self.assertEqual(paths, [ASSET_A, ASSET_B])
        self.assertTrue(all(item["found"] for item in context["targetAssets"]))
        self.assertEqual(context["revisionState"]["overall"], "fresh")
        self.assertEqual(context["revisionState"]["assets"][ASSET_B]["state"], "fresh")

    def test_t3_memory_excluded_by_request_flag(self) -> None:
        self.memory_service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectFact",
                subject_key="combat:damage",
                title="Damage rule",
                body="Base damage is 10.",
                source_kind=MemorySourceKind.TOOL_OBSERVED,
                revision_set=(MemoryRevision(ASSET_A, f"sha256:{self.revision_a}"),),
                scopes=(MemoryScope("asset", ASSET_A),),
            )
        )
        service = self.make_service()
        context = service.get_task_context(query="检查伤害", asset_paths=[ASSET_A], include_memory=False)
        self.assertFalse(context["memory"]["included"])
        self.assertEqual(context["memory"]["reason"], "include-memory-false")
        self.assertFalse(context["activeWork"]["included"])
        self.assertEqual(context["activeWork"]["reason"], "include-memory-false")
        self.assertTrue(context["revisionState"]["available"])

    def test_t4_live_editor_excluded_by_request_flag(self) -> None:
        service = self.make_service(live_editor_service=FakeLiveEditor())
        context = service.get_task_context(query="检查伤害", asset_paths=[ASSET_A], include_live_context=False)
        self.assertFalse(context["liveEditor"]["included"])
        self.assertEqual(context["liveEditor"]["reason"], "include-live-context-false")
        self.assertEqual(len(context["targetAssets"]), 1)

    def test_t5_target_dirty_in_editor_is_a_high_risk(self) -> None:
        service = self.make_service(
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)),
        )
        context = service.get_task_context(query="检查 BP_TestActor", asset_paths=[ASSET_A])
        self.assertTrue(context["liveEditor"]["included"])
        self.assertEqual(context["liveEditor"]["editorSessionId"], "session-test")
        dirty_kinds = [risk["kind"] for risk in context["risks"] if risk["assetPath"] == ASSET_A]
        self.assertIn("target-dirty-in-editor", dirty_kinds)
        self.assertGreaterEqual(context["riskSummary"]["highCount"], 1)

    def test_t6_stale_revision_and_stale_memory_are_reported(self) -> None:
        self.memory_service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectFact",
                subject_key="combat:damage",
                title="BP_TestActor damage rule",
                body="BP_TestActor damage rules are outdated and need review.",
                source_kind=MemorySourceKind.TOOL_OBSERVED,
                revision_set=(MemoryRevision(ASSET_A, "sha256:" + "f" * 64),),
                scopes=(MemoryScope("asset", ASSET_A),),
            )
        )
        validation = self.memory_service.validate_against_index(self.database_path)
        self.assertGreaterEqual(len(validation.invalidation.stale_record_ids), 1)

        package_file = self.project_root / "Content" / "Characters" / "BP_TestActor.uasset"
        package_file.write_bytes(b"changed-after-index")

        service = self.make_service(live_editor_service=FakeLiveEditor())
        context = service.get_task_context(
            query="BP_TestActor damage rule",
            asset_paths=[ASSET_A],
        )
        self.assertEqual(context["revisionState"]["overall"], "stale")
        self.assertEqual(context["revisionState"]["assets"][ASSET_A]["state"], "stale")
        kinds = {risk["kind"]: risk for risk in context["risks"]}
        self.assertIn("asset-stale", kinds)
        self.assertEqual(kinds["asset-stale"]["assetPath"], ASSET_A)
        self.assertIn("memory-stale-records", kinds)
        self.assertIn(
            validation.invalidation.stale_record_ids[0],
            kinds["memory-stale-records"]["details"]["recordIds"],
        )

    def test_t7_change_set_id_valid(self) -> None:
        known = {
            "cs_valid": {
                "ok": True,
                "tool": "ue_get_change_set",
                "changeSetId": "cs_valid",
                "taskId": "task_valid",
                "title": "Damage tuning",
                "status": "planned",
                "operations": [],
                "affectedAssets": [ASSET_A],
                "transactionIds": [],
                "validation": {"state": "not-run"},
                "saveState": {"state": "unsaved"},
                "receiptCount": 0,
            }
        }
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
        )
        context = service.get_task_context(query="检查伤害", asset_paths=[ASSET_A], change_set_id="cs_valid")
        self.assertTrue(context["changeSet"]["requested"])
        self.assertTrue(context["changeSet"]["found"])
        self.assertEqual(context["changeSet"]["summary"]["status"], "planned")
        kinds = [risk["kind"] for risk in context["risks"]]
        self.assertNotIn("change-set-not-found", kinds)
        expansion_tools = [item["tool"] for item in context["nextExpansions"]]
        self.assertIn("ue_get_change_set", expansion_tools)

    def test_t8_change_set_id_invalid_degrades_section_and_adds_risk(self) -> None:
        service = self.make_service(workflow_service=FakeChangeSetWorkflow())
        context = service.get_task_context(query="检查伤害", asset_paths=[ASSET_A], change_set_id="cs_missing")
        self.assertTrue(context["ok"])
        self.assertTrue(context["changeSet"]["requested"])
        self.assertFalse(context["changeSet"]["found"])
        self.assertEqual(context["changeSet"]["reason"], "change-set-not-found")
        kinds = {risk["kind"] for risk in context["risks"]}
        self.assertIn("change-set-not-found", kinds)
        self.assertEqual(len(context["targetAssets"]), 1)
        self.assertIn("changeSet", [item["section"] for item in context["degradedSources"]])

    def test_t9_budget_truncation_keeps_target_identity(self) -> None:
        for index in range(4):
            self.memory_service.add_record(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key=f"combat:rule-{index}",
                    title=f"Damage rule {index}",
                    body="Base damage is " + "10" * (index + 2) + " and validated by automation.",
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    revision_set=(MemoryRevision(ASSET_A, f"sha256:{self.revision_a}"),),
                    scopes=(MemoryScope("asset", ASSET_A),),
                )
            )
        service = self.make_service(live_editor_service=FakeLiveEditor())
        context = service.get_task_context(
            query="damage rules audit",
            asset_paths=[ASSET_A, ASSET_B],
            max_output_tokens=600,
        )
        budget = context["outputBudget"]
        self.assertEqual(budget["maxTokens"], 600)
        self.assertTrue(budget["truncated"])
        self.assertNotEqual(budget["truncationReason"], "")
        self.assertLessEqual(budget["estimatedTokens"], 600)
        for target in context["targetAssets"]:
            self.assertTrue(target["found"])
            self.assertIn("identity", target)
            self.assertIn("asset_path", target["identity"])
            self.assertIn("assetPath", target)

        tiny = service.get_task_context(
            query="damage rules audit",
            asset_paths=[ASSET_A, ASSET_B],
            max_output_tokens=256,
        )
        tiny_budget = tiny["outputBudget"]
        self.assertTrue(tiny_budget["truncated"])
        self.assertIn("minimal-envelope-exceeds-token-budget", tiny_budget["truncationReason"])
        for target in tiny["targetAssets"]:
            self.assertTrue(target["found"])
            self.assertIn("assetPath", target)

    def test_t10_optional_source_failure_keeps_other_sections(self) -> None:
        service = self.make_service(live_editor_service=FakeLiveEditor(available=False))
        context = service.get_task_context(query="检查伤害", asset_paths=[ASSET_A])
        self.assertTrue(context["ok"])
        self.assertFalse(context["liveEditor"]["included"])
        self.assertEqual(context["liveEditor"]["reason"], "live-editor-unavailable")
        kinds = {risk["kind"] for risk in context["risks"]}
        self.assertIn("live-editor-unavailable", kinds)
        self.assertEqual(len(context["targetAssets"]), 1)
        self.assertEqual(context["revisionState"]["assets"][ASSET_A]["state"], "fresh")

    def test_request_validation_rejects_invalid_arguments(self) -> None:
        service = self.make_service()
        with self.assertRaisesRegex(ValueError, "query is required"):
            service.get_task_context(query="   ", asset_paths=[ASSET_A])
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            service.get_task_context(
                query="检查",
                asset_paths=[f"/Game/Folder/Asset_{index}.Asset_{index}" for index in range(MAX_TASK_CONTEXT_ASSETS + 1)],
            )
        with self.assertRaisesRegex(ValueError, "duplicates"):
            service.get_task_context(query="检查", asset_paths=[ASSET_A, ASSET_A])
        with self.assertRaisesRegex(ValueError, "must be an exact /Game Object Path"):
            service.get_task_context(query="检查", asset_paths=["Relative/Path.Asset"])
        with self.assertRaisesRegex(ValueError, "must be boolean"):
            service.get_task_context(query="检查", include_memory=1)  # type: ignore[arg-type]
        with self.assertRaisesRegex(ValueError, "max_output_tokens"):
            service.get_task_context(query="检查", max_output_tokens=128)

    def test_requested_work_item_is_reported(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage after the latest asset change.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A,),
            )
        )
        service = self.make_service()
        context = service.get_task_context(query="检查伤害", asset_paths=[ASSET_A], work_item_id=work.work_item_id)
        requested = context["activeWork"]["requestedWorkItem"]
        self.assertTrue(requested["requested"])
        self.assertTrue(requested["found"])
        self.assertEqual(requested["work"]["workItemId"], work.work_item_id)
        self.assertEqual(requested["work"]["assetPaths"], [ASSET_A])
        missing = service.get_task_context(query="检查伤害", work_item_id="work_missing")
        self.assertFalse(missing["activeWork"]["requestedWorkItem"]["found"])
        kinds = {risk["kind"] for risk in missing["risks"]}
        self.assertIn("work-item-not-found", kinds)


@unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
class TaskContextMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_task_context_mcp_")
        root = Path(self.temporary.name)
        self.temp_root = root
        self.database_path = root / "index.sqlite3"
        export_root = root / "export"
        write_export(export_root, [make_asset(ASSET_A, profile="logic", revision="a" * 64, rich=False)])
        with open_database(self.database_path) as connection:
            build_index(connection, export_root, self.database_path)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_task_context_tool_registers_in_every_mode_and_degrades(self) -> None:
        memory_service = ProjectMemoryService(
            database_path=self.temp_root / "memory.sqlite3",
            project_key=PROJECT,
        )
        server = create_mcp_server(
            self.database_path,
            memory_service=memory_service,
            live_editor_service=FakeLiveEditorService(),
        )
        tools = asyncio.run(server.list_tools())
        names = [tool.name for tool in tools]
        self.assertIn("ue_get_task_context", names)
        tool = next(item for item in tools if item.name == "ue_get_task_context")
        properties = tool.inputSchema["properties"]
        self.assertIn("query", properties)
        self.assertIn("asset_paths", properties)
        self.assertIn("work_item_id", properties)
        self.assertIn("change_set_id", properties)
        self.assertIn("include_live_context", properties)
        self.assertIn("include_memory", properties)
        self.assertIn("max_output_tokens", properties)
        self.assertNotIn("database", properties)

        _, capabilities = asyncio.run(server.call_tool("ue_get_capabilities", {}))
        contract = capabilities["taskContext"]
        self.assertTrue(contract["available"])
        self.assertEqual(contract["tool"], "ue_get_task_context")
        self.assertTrue(contract["risksAreDeterministicOnly"])
        self.assertFalse(contract["modelInference"])
        self.assertFalse(contract["autoRelevantAssetExpansion"])
        self.assertEqual(contract["sourceAvailability"]["memory"], True)
        self.assertEqual(contract["sourceAvailability"]["liveEditor"], True)
        self.assertEqual(capabilities["limits"]["taskContextMaxAssets"], MAX_TASK_CONTEXT_ASSETS)

        _, payload = asyncio.run(
            server.call_tool(
                "ue_get_task_context",
                {"query": "检查角色伤害", "asset_paths": [ASSET_A]},
            )
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertTrue(payload["targetAssets"][0]["found"])
        self.assertTrue(payload["liveEditor"]["included"])
        self.assertEqual(payload["liveEditor"]["editorSessionId"], "session-test")
        self.assertTrue(payload["memory"]["included"])
        self.assertFalse(payload["revisionState"]["available"])
        self.assertEqual(payload["revisionState"]["reason"], "revision-export-not-configured")

        with self.assertRaisesRegex(Exception, "Extra inputs are not permitted"):
            asyncio.run(
                server.call_tool(
                    "ue_get_task_context",
                    {"query": "检查", "database": "nope.sqlite3"},
                )
            )

    def test_task_context_tool_with_workflow_change_set(self) -> None:
        server = create_mcp_server(self.database_path, workflow_service=FakeWorkflowService())
        _, payload = asyncio.run(
            server.call_tool(
                "ue_get_task_context",
                {"query": "检查角色伤害", "change_set_id": "cs_fake"},
            )
        )
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["changeSet"]["requested"])
        self.assertTrue(payload["changeSet"]["found"])
        self.assertEqual(payload["changeSet"]["summary"]["status"], "planned")
        self.assertFalse(payload["memory"]["available"])
        self.assertFalse(payload["liveEditor"]["available"])


if __name__ == "__main__":
    unittest.main()

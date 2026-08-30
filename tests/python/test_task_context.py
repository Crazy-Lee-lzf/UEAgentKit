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
from ue_agent_kit.memory_context import (  # noqa: E402
    RECALL_MAX_CONTENT_CHARS,
    RECALL_MAX_ESTIMATED_TOKENS,
    RECALL_MAX_ITEMS,
)
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemorySourceKind,
)
from ue_agent_kit.task_context import (  # noqa: E402
    MAX_CORRELATION_LINKS,
    MAX_TASK_CONTEXT_ASSETS,
    MAX_TASK_CONTEXT_CANDIDATES,
    MAX_TASK_CONTEXT_EXPANSIONS,
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

    def make_change_set(
        self,
        *,
        change_set_id: str,
        editor_session_id: str = "",
        affected_assets: tuple[str, ...] = (),
        status: str = "planned",
    ) -> dict[str, dict[str, Any]]:
        return {
            change_set_id: {
                "ok": True,
                "tool": "ue_get_change_set",
                "changeSetId": change_set_id,
                "taskId": "task-1",
                "editorSessionId": editor_session_id,
                "title": "Damage tuning",
                "status": status,
                "operations": [],
                "affectedAssets": list(affected_assets),
                "transactionIds": [],
                "validation": {"state": "not-run"},
                "saveState": {"state": "unsaved"},
                "receiptCount": 0,
            }
        }

    def add_asset_record(self, *, asset_path: str, body: str, title: str = "Evidence record") -> str:
        record = self.memory_service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="runtimeEvidence",
                subject_key=f"evidence:{asset_path}",
                title=title,
                body=body,
                source_kind=MemorySourceKind.TOOL_OBSERVED,
                scopes=(MemoryScope("asset", asset_path),),
            )
        )
        return record.record_id

    @staticmethod
    def correlation_links(context: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        section = context["correlation"]
        links = section.get("links", [])
        return [link for link in links if link.get("kind") == kind]

    def test_t1_single_explicit_asset_with_memory_and_live_disabled(self) -> None:
        service = self.make_service(memory_service=None)
        context = service.get_task_context(query="检查 BP_TestActor 的伤害", asset_paths=[ASSET_A])

        self.assertTrue(context["ok"])
        self.assertTrue(context["readOnly"])
        self.assertEqual(context["tool"], "ue_get_task_context")
        self.assertEqual(context["request"]["query"], "检查 BP_TestActor 的伤害")
        self.assertEqual(context["project"]["projectKey"], PROJECT)
        # R0.2: the only query term matching the index ("BP_TestActor") belongs
        # to the explicit target, so it must never be duplicated as a candidate.
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

    def test_m1_task_context_memory_cannot_bypass_automatic_recall_caps(self) -> None:
        for index in range(10):
            self.memory_service.add_record(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key=f"combat:m1-{index}",
                    title=f"Damage memory {index}",
                    body=("Damage memory benchmark content. " * 10) + str(index),
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    revision_set=(MemoryRevision(ASSET_A, f"sha256:{self.revision_a}"),),
                    scopes=(MemoryScope("asset", ASSET_A),),
                )
            )
        for index in range(8):
            self.memory_service.create_work(
                WorkItemDraft(
                    project_key=PROJECT,
                    title=f"Damage audit {index}",
                    description="Audit damage memory evidence.",
                    next_action="Review damage evidence.",
                    asset_paths=(ASSET_A,),
                )
            )
        context = self.make_service().get_task_context(
            query="damage memory audit",
            asset_paths=[ASSET_A],
        )
        summary = context["memory"]["summary"]
        active = context["activeWork"].get("items", [])
        item_count = len(summary["nodes"]) + len(summary["records"]) + len(active)
        self.assertEqual(summary["recalledItemCount"], item_count)
        self.assertLessEqual(item_count, RECALL_MAX_ITEMS)
        self.assertLessEqual(summary["contentChars"], RECALL_MAX_CONTENT_CHARS)
        self.assertLessEqual(summary["estimatedTokens"], RECALL_MAX_ESTIMATED_TOKENS)
        effective = summary["recallBudget"]["effective"]
        self.assertEqual(effective["maxItems"], RECALL_MAX_ITEMS)
        self.assertEqual(effective["maxContentChars"], RECALL_MAX_CONTENT_CHARS)
        self.assertEqual(effective["maxEstimatedTokens"], RECALL_MAX_ESTIMATED_TOKENS)

    def test_m1_task_context_no_hit_has_no_placeholder_recall_content(self) -> None:
        context = self.make_service().get_task_context(query="zzz-no-such-memory-token")
        summary = context["memory"]["summary"]
        self.assertEqual(summary["recalledItemCount"], 0)
        self.assertEqual(summary["contentChars"], 0)
        self.assertEqual(summary["nodes"], [])
        self.assertEqual(summary["records"], [])
        self.assertEqual(context["activeWork"].get("items", []), [])

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
        self.assertIn("ue_analyze_semantic_diff", expansion_tools)
        self.assertIn("ue_build_verification_plan", expansion_tools)
        self.assertIn("ue_evaluate_trust_verdict", expansion_tools)
        verification_expansions = {
            item["tool"]: item
            for item in context["nextExpansions"]
            if item["tool"] in {"ue_build_verification_plan", "ue_evaluate_trust_verdict"}
        }
        self.assertEqual(
            verification_expansions["ue_build_verification_plan"],
            {
                "tool": "ue_build_verification_plan",
                "reason": "verification-plan-explicit-change-set",
                "arguments": {"change_set_id": "cs_valid"},
            },
        )
        self.assertEqual(
            verification_expansions["ue_evaluate_trust_verdict"],
            {
                "tool": "ue_evaluate_trust_verdict",
                "reason": "trust-verdict-explicit-change-set",
                "arguments": {"change_set_id": "cs_valid"},
            },
        )

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

    def _fleet_service(self) -> tuple[TaskContextService, list[str]]:
        root = Path(self.temporary.name)
        fleet_paths = [
            f"/Game/Fleet/BP_Vehicle_{index:02d}.BP_Vehicle_{index:02d}"
            for index in range(1, 11)
        ]
        fleet_assets = [
            make_asset(path, profile="logic", revision=f"{index:064x}", rich=False)
            for index, path in enumerate(fleet_paths, start=1)
        ]
        export_root = root / "fleet-export"
        database_path = root / "fleet.sqlite3"
        write_export(export_root, fleet_assets)
        with open_database(database_path) as connection:
            result = build_index(connection, export_root, database_path)
            self.assertEqual((result.added, result.failed), (10, 0))
        return TaskContextService(index_service=IndexQueryService(database_path)), fleet_paths

    def test_r2_1_query_only_returns_stable_relevant_assets(self) -> None:
        service, _ = self._fleet_service()
        context = service.get_task_context(query="vehicle customization")
        candidates = context["relevantAssets"]
        self.assertTrue(context["ok"])
        self.assertEqual(len(candidates), MAX_TASK_CONTEXT_CANDIDATES)
        repeated = service.get_task_context(query="vehicle customization")["relevantAssets"]
        self.assertEqual(
            [candidate["assetPath"] for candidate in candidates],
            [candidate["assetPath"] for candidate in repeated],
        )

    def test_r2_2_explicit_target_is_not_duplicated_in_candidates(self) -> None:
        service, fleet_paths = self._fleet_service()
        target = fleet_paths[0]
        context = service.get_task_context(query="vehicle", asset_paths=[target])
        candidate_paths = [candidate["assetPath"] for candidate in context["relevantAssets"]]
        self.assertNotIn(target, candidate_paths)
        self.assertEqual(context["targetAssets"][0]["assetPath"], target)

    def test_r2_3_no_search_results_is_empty_not_error(self) -> None:
        service = self.make_service(memory_service=None, freshness_tracker=None)
        context = service.get_task_context(query="zzz absent term")
        self.assertTrue(context["ok"])
        self.assertEqual(context["relevantAssets"], [])
        kinds = {risk["kind"] for risk in context["risks"]}
        self.assertNotIn("relevant-assets-search-failed", kinds)

    def test_r2_4_same_input_ordering_is_deterministic(self) -> None:
        service, _ = self._fleet_service()
        first = service.get_task_context(query="vehicle module")
        second = service.get_task_context(query="vehicle module")
        self.assertEqual(first["relevantAssets"], second["relevantAssets"])

    def test_r2_5_candidate_count_is_bounded(self) -> None:
        service, _ = self._fleet_service()
        context = service.get_task_context(query="vehicle")
        self.assertEqual(len(context["relevantAssets"]), MAX_TASK_CONTEXT_CANDIDATES)
        self.assertLessEqual(len(context["relevantAssets"]), MAX_TASK_CONTEXT_CANDIDATES)

    def test_r2_6_symbol_hits_dedupe_and_supplement_asset_candidates(self) -> None:
        service = self.make_service(memory_service=None, freshness_tracker=None)
        context = service.get_task_context(query="BP_SecondActor")
        candidate_paths = [candidate["assetPath"] for candidate in context["relevantAssets"]]
        self.assertEqual(candidate_paths.count(ASSET_B), 1)
        self.assertEqual(context["relevantAssets"][0]["whyIncluded"], "asset-search-query-term")

        root = Path(self.temporary.name)
        asset = make_asset(ASSET_B, profile="logic", revision=self.revision_b, rich=False)
        asset["symbols"].append(
            {
                "id": f"variable|{ASSET_B}|wheel-controller",
                "kind": "variable",
                "name": "WheelController",
                "assetPath": ASSET_B,
                "ownerSymbolId": f"asset|{ASSET_B}",
            }
        )
        export_root = root / "symbol-export"
        database_path = root / "symbol.sqlite3"
        write_export(export_root, [asset])
        with open_database(database_path) as connection:
            result = build_index(connection, export_root, database_path)
            self.assertEqual((result.added, result.failed), (1, 0))
        symbol_service = TaskContextService(index_service=IndexQueryService(database_path))
        context = symbol_service.get_task_context(query="WheelController")
        self.assertEqual(len(context["relevantAssets"]), 1)
        candidate = context["relevantAssets"][0]
        self.assertEqual(candidate["assetPath"], ASSET_B)
        self.assertEqual(candidate["whyIncluded"], "symbol-search-query-term")
        self.assertEqual(candidate["matchKind"], "symbol-name-exact")
        self.assertEqual(candidate["matchedSymbol"]["name"], "WheelController")
        self.assertEqual(candidate["assetClass"], "/Script/Engine.Blueprint")

    def test_r2_7_candidate_fields_are_complete(self) -> None:
        service = self.make_service(memory_service=None, freshness_tracker=None)
        context = service.get_task_context(query="BP_SecondActor")
        candidate = context["relevantAssets"][0]
        for field in ("assetPath", "assetClass", "source", "whyIncluded", "matchKind"):
            self.assertIn(field, candidate)
        self.assertEqual(candidate["assetClass"], "/Script/Engine.Blueprint")
        self.assertEqual(candidate["source"], "immutable-sqlite-index")
        self.assertIn("matchedTerms", candidate)
        self.assertEqual(candidate["matchCount"], 1)

    def test_r2_8_low_budget_reduces_candidates_but_keeps_core(self) -> None:
        service, fleet_paths = self._fleet_service()
        target = fleet_paths[0]
        full = service.get_task_context(query="vehicle", asset_paths=[target], max_output_tokens=4096)
        tight = service.get_task_context(query="vehicle", asset_paths=[target], max_output_tokens=600)
        budget = tight["outputBudget"]
        self.assertTrue(budget["truncated"])
        self.assertLess(len(tight["relevantAssets"]), len(full["relevantAssets"]))
        reasons = budget["truncationReason"].split(",")
        self.assertTrue(any(reason.startswith("relevant-assets-") for reason in reasons))
        self.assertTrue(tight["targetAssets"][0]["found"])
        self.assertIn("identity", tight["targetAssets"][0])
        self.assertIn("riskSummary", tight)

    def test_r2_9_memory_and_live_disabled_do_not_affect_discovery(self) -> None:
        service = self.make_service(memory_service=None, freshness_tracker=None)
        context = service.get_task_context(query="BP_SecondActor")
        self.assertFalse(context["memory"]["available"])
        self.assertFalse(context["liveEditor"]["available"])
        self.assertEqual(len(context["relevantAssets"]), 1)
        self.assertEqual(context["relevantAssets"][0]["assetPath"], ASSET_B)

    def test_r2_10_search_failure_follows_error_model_without_fake_results(self) -> None:
        class FlakyIndexService:
            def __init__(self, inner: IndexQueryService, fail_scopes: set[str]) -> None:
                self.inner = inner
                self.fail_scopes = fail_scopes

            def search(self, query: str = "", *, scope: str = "assets", **kwargs: Any):
                if scope in self.fail_scopes:
                    raise RuntimeError("search unavailable")
                return self.inner.search(query=query, scope=scope, **kwargs)

            def __getattr__(self, name: str):
                return getattr(self.inner, name)

        flaky_all = FlakyIndexService(self.index_service, {"assets", "symbols"})
        service = self.make_service(index_service=flaky_all, memory_service=None, freshness_tracker=None)
        context = service.get_task_context(query="BP_SecondActor")
        self.assertTrue(context["ok"])
        self.assertEqual(context["relevantAssets"], [])
        kinds = {risk["kind"] for risk in context["risks"]}
        self.assertIn("relevant-assets-search-failed", kinds)
        degraded_sections = [item["section"] for item in context["degradedSources"]]
        self.assertIn("relevantAssets", degraded_sections)

        flaky_symbols = FlakyIndexService(self.index_service, {"symbols"})
        service = self.make_service(index_service=flaky_symbols, memory_service=None, freshness_tracker=None)
        context = service.get_task_context(query="BP_SecondActor")
        self.assertEqual(
            [candidate["assetPath"] for candidate in context["relevantAssets"]],
            [ASSET_B],
        )
        kinds = {risk["kind"] for risk in context["risks"]}
        self.assertNotIn("relevant-assets-search-failed", kinds)
        reasons = [
            item["reason"]
            for item in context["degradedSources"]
            if item["section"] == "relevantAssets"
        ]
        self.assertEqual(reasons, ["symbol-search-failed"])

    def test_r3_1_change_set_editor_session_match_is_linked(self) -> None:
        known = self.make_change_set(change_set_id="cs_sess", editor_session_id="session-test")
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(session_id="session-test"),
        )
        context = service.get_task_context(query="检查伤害", change_set_id="cs_sess")
        section = context["correlation"]
        self.assertTrue(section["available"])
        self.assertEqual(section["method"], "deterministic-key-matching")
        session_links = self.correlation_links(context, "change-set-editor-session")
        self.assertEqual(len(session_links), 1)
        link = session_links[0]
        self.assertEqual(link["changeSetId"], "cs_sess")
        self.assertEqual(link["sources"], ["change-set-journal", "live-editor-memory"])
        self.assertTrue(link["details"]["matches"])
        self.assertEqual(link["details"]["changeSetEditorSessionId"], "session-test")
        self.assertEqual(link["details"]["liveEditorSessionId"], "session-test")
        kinds = {risk["kind"] for risk in context["risks"]}
        self.assertNotIn("change-set-editor-session-mismatch", kinds)

    def test_r3_2_change_set_editor_session_mismatch_is_a_medium_risk(self) -> None:
        known = self.make_change_set(change_set_id="cs_sess", editor_session_id="session-other")
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(session_id="session-test"),
        )
        context = service.get_task_context(query="检查伤害", change_set_id="cs_sess")
        session_links = self.correlation_links(context, "change-set-editor-session")
        self.assertEqual(len(session_links), 1)
        self.assertFalse(session_links[0]["details"]["matches"])
        mismatch = next(
            risk for risk in context["risks"] if risk["kind"] == "change-set-editor-session-mismatch"
        )
        self.assertEqual(mismatch["severity"], "medium")
        self.assertEqual(mismatch["source"], "cross-source-correlation")
        self.assertEqual(mismatch["details"]["changeSetEditorSessionId"], "session-other")
        self.assertEqual(mismatch["details"]["liveEditorSessionId"], "session-test")

    def test_r3_3_change_set_affected_assets_in_editor_are_linked(self) -> None:
        known = self.make_change_set(change_set_id="cs_dirty", affected_assets=(ASSET_A,))
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,), open_assets=(ASSET_A,)),
        )
        context = service.get_task_context(query="检查伤害", change_set_id="cs_dirty")
        links = self.correlation_links(context, "change-set-asset-in-editor")
        observed = {(link["assetPath"], link["details"]["observedVia"]) for link in links}
        self.assertIn((ASSET_A, "editor-dirty-packages"), observed)
        self.assertIn((ASSET_A, "editor-open-assets"), observed)

    def test_r3_4_change_set_affected_assets_correlate_with_memory_evidence(self) -> None:
        record_id = self.add_asset_record(
            asset_path=ASSET_A,
            body="BP_TestActor damage rules were verified by automation.",
        )
        known = self.make_change_set(change_set_id="cs_evid", affected_assets=(ASSET_A,))
        service = self.make_service(workflow_service=FakeChangeSetWorkflow(known=known))
        context = service.get_task_context(query="检查伤害", change_set_id="cs_evid")
        links = self.correlation_links(context, "change-set-asset-memory-evidence")
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link["assetPath"], ASSET_A)
        self.assertEqual(link["changeSetId"], "cs_evid")
        self.assertEqual(link["sources"], ["change-set-journal", "project-memory"])
        self.assertEqual(link["details"]["recordId"], record_id)
        self.assertEqual(link["details"]["recordType"], "runtimeEvidence")
        self.assertIn("status", link["details"])

    def test_r3_5_work_item_assets_overlapping_change_set_are_linked(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A,),
            )
        )
        known = self.make_change_set(change_set_id="cs_over", affected_assets=(ASSET_A,))
        service = self.make_service(workflow_service=FakeChangeSetWorkflow(known=known))
        context = service.get_task_context(
            query="检查伤害",
            change_set_id="cs_over",
            work_item_id=work.work_item_id,
        )
        links = self.correlation_links(context, "work-change-set-asset-overlap")
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link["workItemId"], work.work_item_id)
        self.assertEqual(link["changeSetId"], "cs_over")
        self.assertEqual(link["assetPath"], ASSET_A)

    def test_r3_6_work_item_referencing_change_set_literal_is_linked(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Resume cs_ref",
                description="Continue after change set cs_ref is saved.",
                next_action="Verify.",
                asset_paths=(ASSET_A,),
            )
        )
        known = self.make_change_set(change_set_id="cs_ref", affected_assets=(ASSET_A,))
        service = self.make_service(workflow_service=FakeChangeSetWorkflow(known=known))
        context = service.get_task_context(
            query="检查伤害",
            change_set_id="cs_ref",
            work_item_id=work.work_item_id,
        )
        links = self.correlation_links(context, "work-references-change-set")
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link["workItemId"], work.work_item_id)
        self.assertEqual(link["changeSetId"], "cs_ref")
        self.assertEqual(link["details"]["matchedIn"], ["title", "description"])

    def test_r3_7_work_item_assets_in_editor_are_linked(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A,),
            )
        )
        service = self.make_service(live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)))
        context = service.get_task_context(query="检查伤害", work_item_id=work.work_item_id)
        links = self.correlation_links(context, "work-asset-in-editor")
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual(link["workItemId"], work.work_item_id)
        self.assertEqual(link["assetPath"], ASSET_A)
        self.assertEqual(link["details"]["observedVia"], "editor-dirty-packages")

    def test_r3_8_work_item_assets_correlate_with_memory_evidence(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify second actor",
                description="Verify BP_SecondActor.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_B,),
            )
        )
        record_id = self.add_asset_record(
            asset_path=ASSET_B,
            body="BP_SecondActor was verified by the latest automation run.",
        )
        service = self.make_service()
        context = service.get_task_context(query="检查伤害", work_item_id=work.work_item_id)
        links = self.correlation_links(context, "work-asset-memory-evidence")
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["workItemId"], work.work_item_id)
        self.assertEqual(links[0]["assetPath"], ASSET_B)
        self.assertEqual(links[0]["details"]["recordId"], record_id)

    def test_r3_9_without_change_set_id_no_change_set_links_are_produced(self) -> None:
        self.add_asset_record(
            asset_path=ASSET_A,
            body="BP_TestActor damage rules were verified.",
        )
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(
                known=self.make_change_set(change_set_id="cs_idle", affected_assets=(ASSET_A,))
            ),
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)),
        )
        context = service.get_task_context(query="检查伤害")
        expansion_tools = {item["tool"] for item in context["nextExpansions"]}
        self.assertNotIn("ue_build_verification_plan", expansion_tools)
        self.assertNotIn("ue_evaluate_trust_verdict", expansion_tools)
        kinds = {link["kind"] for link in context["correlation"].get("links", [])}
        self.assertFalse(any(kind.startswith("change-set-") for kind in kinds))
        self.assertFalse(any(kind.startswith("work-change-set") for kind in kinds))

        bare = self.make_service(memory_service=None, freshness_tracker=None)
        bare_context = bare.get_task_context(query="检查伤害")
        self.assertFalse(bare_context["correlation"]["available"])
        self.assertEqual(
            bare_context["correlation"]["reason"],
            "insufficient-correlatable-sources",
        )

    def test_r3_10_degraded_sources_produce_no_fake_correlation(self) -> None:
        known = self.make_change_set(change_set_id="cs_gone", affected_assets=(ASSET_A,))
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)),
        )
        missing = service.get_task_context(query="检查伤害", change_set_id="cs_missing")
        self.assertFalse(missing["changeSet"]["found"])
        link_kinds = [link["kind"] for link in missing["correlation"].get("links", [])]
        self.assertFalse(any(kind.startswith("change-set-") for kind in link_kinds))

        no_memory = service.get_task_context(
            query="检查伤害",
            change_set_id="cs_gone",
            include_memory=False,
        )
        link_kinds = [link["kind"] for link in no_memory["correlation"].get("links", [])]
        self.assertFalse(any("memory-evidence" in kind for kind in link_kinds))

        no_live = service.get_task_context(
            query="检查伤害",
            change_set_id="cs_gone",
            include_live_context=False,
        )
        link_kinds = [link["kind"] for link in no_live["correlation"].get("links", [])]
        self.assertFalse(any("in-editor" in kind or kind == "change-set-editor-session" for kind in link_kinds))

    def test_r3_11_correlation_is_deterministic_for_identical_inputs(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage cs_det",
                description="Verify combat damage after cs_det.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A,),
            )
        )
        self.add_asset_record(
            asset_path=ASSET_A,
            body="BP_TestActor damage rules were verified.",
        )
        known = self.make_change_set(
            change_set_id="cs_det",
            editor_session_id="session-other",
            affected_assets=(ASSET_A,),
        )
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)),
        )
        first = service.get_task_context(query="检查伤害", change_set_id="cs_det", work_item_id=work.work_item_id)
        second = service.get_task_context(query="检查伤害", change_set_id="cs_det", work_item_id=work.work_item_id)
        self.assertEqual(first["correlation"], second["correlation"])

    def test_r3_12_correlation_is_bounded_and_reports_honest_counts(self) -> None:
        affected = tuple(f"/Game/Many/BP_M_{index:02d}.BP_M_{index:02d}" for index in range(12))
        known = self.make_change_set(change_set_id="cs_bound", affected_assets=affected)
        for index in range(10):
            self.memory_service.create_work(
                WorkItemDraft(
                    project_key=PROJECT,
                    title=f"damage audit item {index}",
                    description="Audit vehicle damage rules.",
                    next_action="Review evidence.",
                    asset_paths=(affected[index],),
                )
            )
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(
                dirty_packages=tuple(path.rsplit(".", 1)[0] for path in affected)
            ),
        )
        context = service.get_task_context(query="damage audit", change_set_id="cs_bound")
        section = context["correlation"]
        self.assertTrue(section["available"])
        summary = section["summary"]
        # M1 automatic recall caps total recalled items (nodes + activeWork +
        # records) at 5, so only the bounded activeWork subset is visible here.
        self.assertEqual(summary["workItemsTotal"], 5)
        self.assertEqual(summary["workItemsConsidered"], 5)
        self.assertEqual(summary["changeSetAffectedAssetsTotal"], 12)
        self.assertEqual(summary["changeSetAffectedAssetsSampled"], 8)
        self.assertTrue(summary["changeSetAffectedAssetsTruncated"])
        self.assertEqual(summary["evidenceLookups"], 12)
        self.assertTrue(summary["evidenceLookupsTruncated"])
        self.assertTrue(summary["linksTruncated"])
        self.assertEqual(len(section["links"]), MAX_CORRELATION_LINKS)
        self.assertEqual(summary["linkCount"], MAX_CORRELATION_LINKS)
        repeated = service.get_task_context(query="damage audit", change_set_id="cs_bound")
        self.assertEqual(section["links"], repeated["correlation"]["links"])

    def test_r3_13_correlation_is_non_persistent(self) -> None:
        self.add_asset_record(
            asset_path=ASSET_A,
            body="BP_TestActor damage rules were verified.",
        )
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A,),
            )
        )
        known = self.make_change_set(change_set_id="cs_nop", affected_assets=(ASSET_A,))
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)),
        )
        before = self.memory_service.status()
        service.get_task_context(
            query="检查伤害",
            change_set_id="cs_nop",
            work_item_id=work.work_item_id,
        )
        after = self.memory_service.status()
        self.assertEqual(before.record_count, after.record_count)
        self.assertEqual(before.active_work_count, after.active_work_count)
        self.assertEqual(before.counts_by_status, after.counts_by_status)

    def test_r3_14_low_budget_trims_correlation_before_target_metadata(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage cs_trim",
                description="Verify combat damage after cs_trim.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A,),
            )
        )
        for index in range(4):
            self.add_asset_record(
                asset_path=ASSET_A,
                title=f"Damage evidence {index}",
                body="BP_TestActor base damage is " + "10" * (index + 2) + " and validated by automation.",
            )
        known = self.make_change_set(
            change_set_id="cs_trim",
            editor_session_id="session-test",
            affected_assets=(ASSET_A,),
        )
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)),
        )
        full = service.get_task_context(
            query="检查伤害",
            asset_paths=[ASSET_A],
            change_set_id="cs_trim",
            work_item_id=work.work_item_id,
        )
        self.assertGreater(len(full["correlation"]["links"]), 0)
        tight = service.get_task_context(
            query="检查伤害",
            asset_paths=[ASSET_A],
            change_set_id="cs_trim",
            work_item_id=work.work_item_id,
            max_output_tokens=600,
        )
        budget = tight["outputBudget"]
        self.assertTrue(budget["truncated"])
        reasons = budget["truncationReason"].split(",")
        self.assertTrue(any(reason.startswith("correlation-") for reason in reasons))
        full_kinds = {link["kind"] for link in full["correlation"]["links"]}
        tight_kinds = {link["kind"] for link in tight["correlation"].get("links", [])}
        self.assertTrue(tight_kinds <= full_kinds)
        self.assertTrue(tight["targetAssets"][0]["found"])
        self.assertIn("identity", tight["targetAssets"][0])
        self.assertIn("riskSummary", tight)

    def test_r3_15_requested_work_item_duplicated_in_items_is_considered_once(self) -> None:
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A,),
            )
        )
        known = self.make_change_set(change_set_id="cs_dup", affected_assets=(ASSET_A,))
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(dirty_packages=(PACKAGE_A,)),
        )
        context = service.get_task_context(
            query="damage verify",
            change_set_id="cs_dup",
            work_item_id=work.work_item_id,
        )
        summary = context["correlation"]["summary"]
        self.assertEqual(summary["workItemsTotal"], 1)
        self.assertEqual(summary["workItemsConsidered"], 1)
        overlap = self.correlation_links(context, "work-change-set-asset-overlap")
        self.assertEqual(len(overlap), 1)
        self.assertEqual(overlap[0]["workItemId"], work.work_item_id)

    def test_r3_16_work_item_asset_paths_are_bounded_for_correlation(self) -> None:
        synthetic = tuple(f"/Game/Work/W_{index}.W_{index}" for index in range(1, 6))
        work = self.memory_service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage.",
                next_action="Run automation tests.",
                asset_paths=(ASSET_A, *synthetic),
            )
        )
        service = self.make_service(
            live_editor_service=FakeLiveEditor(
                dirty_packages=(PACKAGE_A, *tuple(path.rsplit(".", 1)[0] for path in synthetic)),
            )
        )
        context = service.get_task_context(query="damage verify", work_item_id=work.work_item_id)
        links = self.correlation_links(context, "work-asset-in-editor")
        linked = {link["assetPath"] for link in links}
        self.assertEqual(len(links), 4)
        self.assertIn(ASSET_A, linked)
        self.assertNotIn(synthetic[4], linked)

    def test_r3_17_change_set_without_editor_session_produces_no_session_link(self) -> None:
        known = self.make_change_set(change_set_id="cs_nosess", affected_assets=(ASSET_A,))
        service = self.make_service(
            workflow_service=FakeChangeSetWorkflow(known=known),
            live_editor_service=FakeLiveEditor(session_id="session-test"),
        )
        context = service.get_task_context(query="检查伤害", change_set_id="cs_nosess")
        self.assertTrue(context["correlation"]["available"])
        session_links = self.correlation_links(context, "change-set-editor-session")
        self.assertEqual(session_links, [])
        kinds = {risk["kind"] for risk in context["risks"]}
        self.assertNotIn("change-set-editor-session-mismatch", kinds)

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

    def test_r4_1_explicit_targets_suggest_impact_analysis(self) -> None:
        service = self.make_service()
        context = service.get_task_context(query="检查伤害", asset_paths=[ASSET_A, ASSET_B])
        expansions = context["nextExpansions"]
        impact = [
            item
            for item in expansions
            if item["tool"] == "ue_analyze_change_impact"
        ]
        self.assertEqual(len(impact), 1)
        self.assertEqual(impact[0]["reason"], "impact-analysis-explicit-targets")
        self.assertEqual(impact[0]["arguments"]["target_asset_paths"], [ASSET_A, ASSET_B])
        self.assertEqual(impact[0]["arguments"]["max_depth"], 2)
        self.assertIn("ue_find_references", [item["tool"] for item in expansions])
        self.assertLessEqual(len(expansions), MAX_TASK_CONTEXT_EXPANSIONS)

    def test_r4_2_relevant_assets_only_give_bounded_impact_hint(self) -> None:
        service, _ = self._fleet_service()
        context = service.get_task_context(query="vehicle customization", asset_paths=[])
        first_relevant = context["relevantAssets"][0]["assetPath"]
        expansions = context["nextExpansions"]
        impact = [
            item
            for item in expansions
            if item["tool"] == "ue_analyze_change_impact"
        ]
        self.assertEqual(len(impact), 1)
        self.assertEqual(impact[0]["reason"], "impact-analysis-relevant-asset-hint")
        self.assertEqual(impact[0]["arguments"]["target_asset_paths"], [first_relevant])
        self.assertEqual(impact[0]["arguments"]["max_depth"], 2)


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
        self.assertTrue(contract["autoRelevantAssetExpansion"])
        self.assertEqual(contract["relevantAssets"]["maxAssets"], MAX_TASK_CONTEXT_CANDIDATES)
        self.assertTrue(contract["relevantAssets"]["deterministic"])
        self.assertFalse(contract["relevantAssets"]["modelInference"])
        correlation_contract = contract["crossSourceCorrelation"]
        self.assertTrue(correlation_contract["available"])
        self.assertTrue(correlation_contract["deterministic"])
        self.assertFalse(correlation_contract["modelInference"])
        self.assertTrue(correlation_contract["readOnly"])
        self.assertFalse(correlation_contract["persistent"])
        self.assertEqual(correlation_contract["maxLinks"], MAX_CORRELATION_LINKS)
        self.assertTrue(correlation_contract["changeSetExplicitOnly"])
        self.assertFalse(correlation_contract["changeSetAutoDiscovery"])
        self.assertEqual(
            set(correlation_contract["sources"]),
            {
                "project-memory-active-work",
                "change-set-journal",
                "live-editor-memory",
                "project-memory",
            },
        )
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
        self.assertIn("correlation", payload)
        self.assertEqual(payload["correlation"]["method"], "deterministic-key-matching")

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
        self.assertFalse(payload["correlation"]["available"])
        self.assertEqual(
            payload["correlation"]["reason"],
            "insufficient-correlatable-sources",
        )


if __name__ == "__main__":
    unittest.main()

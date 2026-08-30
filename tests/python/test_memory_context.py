from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.active_work import WorkItemDraft  # noqa: E402
from ue_agent_kit.memory_context import (  # noqa: E402
    RECALL_MAX_CONTENT_CHARS,
    RECALL_MAX_ESTIMATED_TOKENS,
    RECALL_MAX_ITEMS,
    ContextBudget,
    RecallBudget,
    build_memory_context,
    estimate_tokens,
)
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryArtifact,
    MemoryRecordDraft,
    MemoryRevision,
    MemorySourceKind,
    invalidate_memory_revisions,
    open_project_memory_database,
)


PROJECT = "测试项目"
ASSET = "/Game/Characters/BP_Player.BP_Player"


class MemoryContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_context_")
        self.database_path = Path(self.temporary.name) / "memory.sqlite3"
        self.service = ProjectMemoryService(database_path=self.database_path, project_key=PROJECT)
        self.root = self.service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="用于测试渐进式上下文的项目。",
            )
        )
        self.combat = self.service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project/combat",
                node_type="system",
                title="Combat",
                summary="战斗系统负责伤害、生命值和武器规则。",
                details={"implementationOverview": "伤害由武器基础值和 Buff 共同计算。"},
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_record(
        self,
        *,
        title: str = "Damage rule",
        body: str = "Base damage is 10 and validated by automation.",
        source_kind: MemorySourceKind = MemorySourceKind.TOOL_OBSERVED,
        revision: str = "sha256:a",
        subject_key: str = "",
    ):
        return self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectFact",
                subject_key=subject_key or f"combat:{title.casefold().replace(' ', '-')}",
                title=title,
                body=body,
                source_kind=source_kind,
                source_ref="test:automation",
                confidence=0.9,
                revision_set=(MemoryRevision(ASSET, revision),) if source_kind == MemorySourceKind.TOOL_OBSERVED else (),
                artifacts=(MemoryArtifact("validationEvidence", f"validation:{title}"),),
                node_id=self.combat.node_id,
            )
        )

    def test_levels_progress_from_summary_to_exact_evidence(self) -> None:
        record = self.add_record()
        self.service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage after the latest asset change.",
                next_action="Run automation tests.",
                node_ids=(self.combat.node_id,),
                asset_paths=(ASSET,),
            )
        )

        level0 = self.service.expand_node(path=self.combat.path, detail_level=0)
        self.assertNotIn("summary", level0["nodes"][0])
        self.assertEqual(level0["records"], [])
        self.assertTrue(level0["nodes"][0]["hasActiveWork"])

        level1 = self.service.expand_node(path=self.combat.path, detail_level=1)
        self.assertEqual(level1["nodes"][0]["summary"], self.combat.summary)
        self.assertEqual(level1["records"], [])

        level2 = self.service.expand_node(path=self.combat.path, detail_level=2)
        self.assertEqual(level2["records"][0]["recordId"], record.record_id)
        self.assertIn("summary", level2["records"][0])
        self.assertNotIn("body", level2["records"][0])
        self.assertEqual(
            level2["nodes"][0]["implementationOverview"],
            "伤害由武器基础值和 Buff 共同计算。",
        )

        level3 = self.service.expand_node(path=self.combat.path, detail_level=3)
        self.assertEqual(level3["records"][0]["body"], record.body)
        self.assertNotIn("evidence", level3["records"][0])

        level4 = self.service.expand_node(path=self.combat.path, detail_level=4)
        evidence = level4["records"][0]["evidence"]
        self.assertEqual(evidence["sourceRef"], "test:automation")
        self.assertEqual(evidence["revisionSet"][0]["assetPath"], ASSET)
        self.assertEqual(evidence["artifacts"][0]["artifactKind"], "validationEvidence")
        exact = self.service.get_evidence(record.record_id)
        self.assertEqual(exact["evidenceSha256"], record.evidence_sha256)

    def test_default_context_excludes_stale_and_superseded_records(self) -> None:
        stale = self.add_record(title="Stale damage", body="Old damage value.")
        current = self.add_record(
            title="Current damage",
            body="Current damage value.",
            source_kind=MemorySourceKind.USER_CONFIRMED,
        )
        old = self.add_record(
            title="Superseded note",
            body="Old task conclusion.",
            source_kind=MemorySourceKind.USER_CONFIRMED,
            subject_key="combat:task-note",
        )
        replacement = self.add_record(
            title="Replacement note",
            body="New task conclusion.",
            source_kind=MemorySourceKind.USER_CONFIRMED,
            subject_key="combat:task-note",
        )
        with open_project_memory_database(self.database_path) as connection:
            invalidate_memory_revisions(
                connection,
                project_key=PROJECT,
                current_revisions={ASSET: "sha256:b"},
            )
        self.service.mark_superseded(
            record_id=old.record_id,
            replacement_record_id=replacement.record_id,
            reason="updated",
        )

        context = self.service.expand_node(path=self.combat.path, detail_level=2)
        record_ids = {item["recordId"] for item in context["records"]}
        self.assertNotIn(stale.record_id, record_ids)
        self.assertNotIn(old.record_id, record_ids)
        self.assertIn(current.record_id, record_ids)
        self.assertIn(replacement.record_id, record_ids)

    def test_character_budget_truncates_with_structured_next_action(self) -> None:
        for index in range(12):
            self.add_record(
                title=f"Damage rule {index}",
                body=("Detailed combat implementation and validation evidence. " * 8) + str(index),
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        context = self.service.expand_node(
            path=self.combat.path,
            detail_level=3,
            budget=ContextBudget(max_chars=1500, max_nodes=10, max_records=50, max_depth=2),
        )
        serialized = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertTrue(context["truncated"])
        self.assertLessEqual(len(serialized), 1500)
        self.assertTrue(context["nextActions"])
        self.assertIn(
            context["nextActions"][0]["tool"],
            {"ue_memory_get", "ue_memory_get_evidence", "ue_memory_expand_node"},
        )
        self.assertEqual(context["usage"]["estimatedTokens"], estimate_tokens(context["usage"]["usedChars"]))

    def test_budget_and_detail_level_are_strictly_validated(self) -> None:
        with self.assertRaisesRegex(ValueError, "max_chars"):
            self.service.get_context(budget=ContextBudget(max_chars=511))
        with self.assertRaisesRegex(ValueError, "max_chars"):
            self.service.get_context(budget=ContextBudget(max_chars=100_001))
        with self.assertRaisesRegex(ValueError, "detail_level"):
            self.service.get_context(detail_level=5)
        with self.assertRaisesRegex(ValueError, "depth"):
            self.service.expand_node(
                path=self.combat.path,
                depth=3,
                budget=ContextBudget(max_depth=2),
            )

    def test_asset_context_uses_revision_bindings_and_keeps_node_linked_work(self) -> None:
        record = self.add_record(title="Revision-only asset fact")
        work = self.service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Inspect combat implementation",
                description="This text intentionally does not contain the query token.",
                next_action="Open the combat node.",
                node_ids=(self.combat.node_id,),
            )
        )

        context = self.service.get_context(
            query="unmatched-token",
            node_path=self.combat.path,
            asset_paths=(ASSET,),
            detail_level=2,
        )
        self.assertIn(self.combat.node_id, {item["nodeId"] for item in context["nodes"]})
        self.assertIn(record.record_id, {item["recordId"] for item in context["records"]})
        self.assertIn(work.work_item_id, {item["workItemId"] for item in context["activeWork"]})

    def test_final_usage_matches_complete_serialized_response(self) -> None:
        for index in range(8):
            self.add_record(
                title=f"Budget record {index}",
                body=("Long evidence body. " * 16) + str(index),
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        context = self.service.expand_node(
            path=self.combat.path,
            detail_level=3,
            budget=ContextBudget(max_chars=1200, max_nodes=10, max_records=50, max_depth=2),
        )
        serialized = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertLessEqual(len(serialized), 1200)
        self.assertEqual(context["usage"]["usedChars"], len(serialized))
        self.assertEqual(
            context["usage"]["estimatedTokens"],
            estimate_tokens(len(serialized)),
        )

    def test_automatic_recall_is_hard_capped_at_5_items_2000_chars_and_800_tokens(self) -> None:
        for index in range(12):
            self.add_record(
                title=f"Recall rule {index}",
                body=("Detailed combat implementation and validation evidence. " * 8) + str(index),
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        self.service.create_work(
            WorkItemDraft(
                project_key=PROJECT,
                title="Verify damage",
                description="Verify combat damage after the latest asset change.",
                next_action="Run automation tests.",
                node_ids=(self.combat.node_id,),
                asset_paths=(ASSET,),
            )
        )
        context = self.service.get_context(
            query="Damage",
            node_path=self.combat.path,
            asset_paths=(ASSET,),
            detail_level=2,
        )
        item_count = len(context["nodes"]) + len(context["activeWork"]) + len(context["records"])
        self.assertLessEqual(item_count, RECALL_MAX_ITEMS)
        self.assertLessEqual(context["contentChars"], RECALL_MAX_CONTENT_CHARS)
        self.assertLessEqual(context["estimatedTokens"], RECALL_MAX_ESTIMATED_TOKENS)
        self.assertTrue(context["truncated"])
        self.assertTrue(context["truncationReasons"])
        self.assertEqual(context["recalledItemCount"], item_count)
        serialized = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertEqual(context["usage"]["usedChars"], len(serialized))
        self.assertEqual(context["usage"]["estimatedTokens"], estimate_tokens(len(serialized)))
        self.assertEqual(context["estimatedTokens"], context["usage"]["estimatedTokens"])

    def test_large_requested_recall_bounds_are_clamped_not_rejected(self) -> None:
        context = self.service.get_context(
            query="Damage",
            node_path=self.combat.path,
            detail_level=2,
            recall_budget=RecallBudget(
                max_items=999,
                max_content_chars=999_999,
                max_estimated_tokens=999_999,
                deadline_ms=999_999,
            ),
        )
        effective = context["recallBudget"]["effective"]
        self.assertEqual(effective["maxItems"], RECALL_MAX_ITEMS)
        self.assertEqual(effective["maxContentChars"], RECALL_MAX_CONTENT_CHARS)
        self.assertEqual(effective["maxEstimatedTokens"], RECALL_MAX_ESTIMATED_TOKENS)
        self.assertEqual(effective["deadlineMs"], 300)
        requested = context["recallBudget"]["requested"]
        self.assertEqual(requested["maxItems"], 999)
        self.assertEqual(requested["maxContentChars"], 999_999)
        self.assertEqual(requested["maxEstimatedTokens"], 999_999)
        self.assertEqual(requested["deadlineMs"], 999_999)

    def test_smaller_caller_recall_bounds_remain_respected(self) -> None:
        for index in range(8):
            self.add_record(
                title=f"Small budget {index}",
                body="Small budget record body.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        context = self.service.get_context(
            query="Damage",
            node_path=self.combat.path,
            detail_level=2,
            recall_budget=RecallBudget(max_items=2, max_content_chars=4000, max_estimated_tokens=300),
        )
        item_count = len(context["nodes"]) + len(context["activeWork"]) + len(context["records"])
        self.assertLessEqual(item_count, 2)
        self.assertLessEqual(context["contentChars"], 2000)
        self.assertLessEqual(context["estimatedTokens"], 300)
        self.assertEqual(context["recallBudget"]["effective"]["maxItems"], 2)
        self.assertEqual(context["recallBudget"]["effective"]["maxContentChars"], 2000)
        self.assertEqual(context["recallBudget"]["effective"]["maxEstimatedTokens"], 300)

    def test_no_hit_automatic_recall_has_zero_items_and_zero_content(self) -> None:
        context = self.service.get_context(query="zzz-no-such-token", asset_paths=(), detail_level=2)
        item_count = len(context["nodes"]) + len(context["activeWork"]) + len(context["records"])
        self.assertEqual(item_count, 0)
        self.assertEqual(context["recalledItemCount"], 0)
        self.assertEqual(context["contentChars"], 0)
        self.assertFalse(context["truncated"])
        self.assertEqual(context["truncationReasons"], [])

    def test_expired_deadline_returns_empty_bounded_recall_and_cleans_progress_handler(self) -> None:
        requested = RecallBudget(
            max_items=999,
            max_content_chars=999_999,
            max_estimated_tokens=999_999,
            deadline_ms=999_999,
        )
        with open_project_memory_database(self.database_path) as connection:
            context = build_memory_context(
                connection,
                project_key=PROJECT,
                query="Damage",
                detail_level=2,
                recall_budget=requested,
                start_deadline=time.monotonic() - 1.0,
            )
            self.assertTrue(context["truncated"])
            self.assertEqual(context["truncationReasons"], ["recall-deadline"])
            self.assertEqual(context["recalledItemCount"], 0)
            self.assertEqual(context["contentChars"], 0)
            self.assertLessEqual(context["estimatedTokens"], RECALL_MAX_ESTIMATED_TOKENS)
            self.assertEqual(context["recallBudget"]["requested"]["maxItems"], 999)
            self.assertEqual(context["recallBudget"]["effective"]["maxItems"], RECALL_MAX_ITEMS)
            serialized = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            self.assertEqual(context["usage"]["usedChars"], len(serialized))
            self.assertEqual(context["usage"]["estimatedTokens"], estimate_tokens(len(serialized)))
            self.assertEqual(context["estimatedTokens"], context["usage"]["estimatedTokens"])
            # The recall progress handler must be removed in finally. If the
            # expired handler leaked, this trivial statement would be interrupted.
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)

    def test_non_timeout_sqlite_operational_error_is_not_converted_to_deadline_success(self) -> None:
        connection = sqlite3.connect(":memory:")
        try:
            with self.assertRaises(sqlite3.OperationalError):
                build_memory_context(
                    connection,
                    project_key=PROJECT,
                    query="Damage",
                    detail_level=2,
                    recall_budget=RecallBudget(),
                )
            # The progress handler is also removed on genuine SQL failure.
            self.assertEqual(connection.execute("SELECT 1").fetchone()[0], 1)
        finally:
            connection.close()

    def test_explicit_expand_node_remains_deeper_than_automatic_recall_caps(self) -> None:
        for index in range(8):
            self.add_record(
                title=f"Expand record {index}",
                body="Explicit expand evidence body " + str(index),
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        expanded = self.service.expand_node(
            path=self.combat.path,
            detail_level=3,
            depth=1,
            budget=ContextBudget(max_chars=12_000, max_nodes=30, max_records=50, max_depth=4),
        )
        item_count = len(expanded["nodes"]) + len(expanded["activeWork"]) + len(expanded["records"])
        self.assertGreater(item_count, RECALL_MAX_ITEMS)
        self.assertNotIn("recalledItemCount", expanded)
        self.assertNotIn("recallBudget", expanded)


if __name__ == "__main__":
    unittest.main()

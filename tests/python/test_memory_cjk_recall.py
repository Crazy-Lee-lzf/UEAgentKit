from __future__ import annotations

import asyncio
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = Path(__file__).resolve().parent
for root in (SRC_ROOT, TESTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from ue_agent_kit.active_work import WorkItemDraft, create_work_item  # noqa: E402
from ue_agent_kit.memory_context import (  # noqa: E402
    ContextBudget,
    RecallBudget,
)
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemoryStatus,
    create_memory_record,
    invalidate_memory_revisions,
    mark_memory_record_superseded,
    open_project_memory_database,
)

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None
if MCP_AVAILABLE:
    from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402

PROJECT = "测试项目"
COORDINATION_PROJECT = "UEAgentKit-Coordination"
ASSET_INVENTORY = "/Game/Inventory/WBP_Inventory.WBP_Inventory"
ASSET_WEAPON = "/Game/Weapons/BP_Rifle.BP_Rifle"


class _ChineseRecallFixture(unittest.TestCase):
    """A small but realistic product-Memory corpus for one UE game project."""

    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ueak_cjk_recall_")
        self.root = Path(self._temporary.name)
        self.memory_path = self.root / "memory" / "project-memory.sqlite3"
        self.ids: dict[str, str] = {}
        self._build_corpus()
        # Fresh service instance on a second open: nothing is cached in-process.
        self.service = ProjectMemoryService(
            database_path=self.memory_path,
            project_key=PROJECT,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _build_corpus(self) -> None:
        with open_project_memory_database(self.memory_path) as connection:
            self.ids["inventory_rule"] = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectRule",
                    subject_key="design.inventory.slot-color",
                    title="背包格子颜色按稀有度分级",
                    body=(
                        "背包格子颜色必须按稀有度分级：白/绿/蓝/紫/橙，禁止在蓝图里硬编码颜色值。"
                        "颜色数据来自 DataTable DT_ItemRarity。"
                    ),
                    source_kind="user-confirmed",
                    source_ref="design/inventory.md#slot-color",
                    scopes=(MemoryScope("asset", ASSET_INVENTORY),),
                    details={"assetPath": ASSET_INVENTORY, "module": "Inventory"},
                ),
            ).record_id
            self.ids["weapon_old"] = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectRule",
                    subject_key="design.weapon.left-hand-ik",
                    title="换枪左手IK旧方案（已废弃）",
                    body="换枪时左手IK直接用世界坐标硬切，不跟随武器socket。",
                    source_kind="user-confirmed",
                    source_ref="design/weapon.md#old",
                    scopes=(MemoryScope("asset", ASSET_WEAPON),),
                ),
            ).record_id
            self.ids["weapon_rule"] = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectRule",
                    subject_key="design.weapon.left-hand-ik",
                    title="换枪左手IK重新绑定武器socket",
                    body=(
                        "换枪时左手IK必须重新绑定到新武器的 socket，"
                        "禁止继续沿用世界坐标硬切；换枪动画结束帧再恢复默认IK。"
                    ),
                    source_kind="user-confirmed",
                    source_ref="design/weapon.md#new",
                    scopes=(MemoryScope("asset", ASSET_WEAPON),),
                ),
            ).record_id
            mark_memory_record_superseded(
                connection,
                record_id=self.ids["weapon_old"],
                replacement_record_id=self.ids["weapon_rule"],
                reason="换枪IK方案改版",
            )
            self.ids["stale_stack"] = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="design.inventory.stack-limit",
                    title="背包格子堆叠上限旧缓存",
                    body="背包格子堆叠上限 99，来自旧资产 revision 的缓存结论。",
                    source_kind="tool-observed",
                    source_ref="asset-scan/old",
                    scopes=(MemoryScope("asset", ASSET_INVENTORY),),
                    revision_set=(
                        MemoryRevision(asset_path=ASSET_INVENTORY, revision="sha256:old-revision"),
                    ),
                ),
            ).record_id
            invalidate_memory_revisions(
                connection,
                project_key=PROJECT,
                current_revisions={ASSET_INVENTORY: "sha256:new-revision"},
            )
            self.ids["cpp_symbol"] = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="code.AInventoryComponent.GetSlotColor",
                    title="AInventoryComponent::GetSlotColor 返回格子颜色",
                    body=(
                        "C++ 符号 AInventoryComponent::GetSlotColor 位于 "
                        "Source/Game/Inventory/InventoryComponent.h，供 WBP_Inventory 读取格子颜色。"
                    ),
                    source_kind="user-confirmed",
                    source_ref="Source/Game/Inventory/InventoryComponent.h",
                    scopes=(MemoryScope("asset", ASSET_INVENTORY),),
                    details={
                        "symbol": "AInventoryComponent::GetSlotColor",
                        "class": "AInventoryComponent",
                    },
                ),
            ).record_id
            self.ids["known_issue"] = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="knownIssue",
                    subject_key="issue.inventory.slot-color-red-conflict",
                    title="已知问题：格子颜色与错误提示红冲突",
                    body="背包格子颜色曾与错误提示红色冲突，现已改用描边而不是整格纯红。",
                    source_kind="user-confirmed",
                    source_ref="issues/INV-42",
                    scopes=(MemoryScope("asset", ASSET_INVENTORY),),
                ),
            ).record_id
            create_work_item(
                connection,
                WorkItemDraft(
                    project_key=PROJECT,
                    title="修复背包格子颜色不一致",
                    description="WBP_Inventory 的格子颜色与 DT_ItemRarity 不一致，需要按稀有度重新绑定。",
                    next_action="在 WBP_Inventory 中替换硬编码颜色为 DT_ItemRarity 查表。",
                    asset_paths=(ASSET_INVENTORY,),
                ),
            )
            create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=COORDINATION_PROJECT,
                    record_type="projectFact",
                    subject_key="coordination.dsh.session",
                    title="背包格子颜色 DSH 会话（内部协调）",
                    body=(
                        "内部协调记录：DSH session session-bb93a6bb，"
                        "背包格子颜色只是开发协调任务名，不得进入 UE 项目产品 Memory。"
                    ),
                    source_kind="tool-observed",
                    source_ref="coordination-goal-cli",
                ),
            )

    def _hits(self, query: str, **kwargs):
        return self.service.search_records(query=query, **kwargs)


class ChineseSearchTests(_ChineseRecallFixture):
    def test_chinese_rule_phrase_is_recalled_by_fts(self) -> None:
        result = self._hits("背包格子颜色", limit=10)
        returned = [hit.record.record_id for hit in result.hits]
        self.assertIn(self.ids["inventory_rule"], returned)
        # Schema v7 indexes the CJK-expanded projection, so a normal FTS hit
        # replaces the former substring fallback for this query.
        self.assertEqual(result.retrieval_mode, "fts")
        self.assertFalse(result.substring_fallback)
        self.assertEqual(result.scanned_records, 0)

    def test_chinese_cpp_symbol_phrase_is_recalled(self) -> None:
        result = self._hits("GetSlotColor", limit=10)
        returned = [hit.record.record_id for hit in result.hits]
        self.assertIn(self.ids["cpp_symbol"], returned)

    def test_mixed_ascii_and_chinese_query_is_recalled(self) -> None:
        for query in ("DT_ItemRarity 背包", "背包格子颜色，"):
            result = self._hits(query, limit=10)
            returned = [hit.record.record_id for hit in result.hits]
            self.assertIn(self.ids["inventory_rule"], returned, query)

    def test_superseded_conclusion_is_not_returned(self) -> None:
        result = self._hits("换枪左手IK", limit=10)
        returned = [hit.record.record_id for hit in result.hits]
        self.assertIn(self.ids["weapon_rule"], returned)
        self.assertNotIn(self.ids["weapon_old"], returned)
        # Creating a same-subject replacement marks the pair conflicted before
        # the old record is superseded (existing product semantics). The
        # guarantee under test is that no stale/superseded conclusion leaks.
        statuses = {hit.record.status for hit in result.hits}
        self.assertFalse(statuses & {MemoryStatus.SUPERSEDED, MemoryStatus.STALE})

    def test_stale_revision_record_is_not_returned(self) -> None:
        result = self._hits("背包格子堆叠上限", limit=10)
        returned = [hit.record.record_id for hit in result.hits]
        self.assertNotIn(self.ids["stale_stack"], returned)

    def test_coordination_project_records_never_leak(self) -> None:
        result = self._hits("背包格子颜色", limit=10)
        self.assertGreaterEqual(len(result.hits), 1)
        self.assertNotIn(
            COORDINATION_PROJECT,
            {hit.record.project_key for hit in result.hits},
        )
        self.assertNotIn("DSH session", " ".join(hit.record.body for hit in result.hits))

    def test_like_wildcards_are_literal_not_scan_everything(self) -> None:
        baseline = {hit.record.record_id for hit in self._hits("背包格子颜色", limit=10).hits}
        self.assertTrue(baseline)
        for query in ("背包格子颜色%", "背包格子颜色_"):
            result = self._hits(query, limit=10)
            self.assertEqual(
                {hit.record.record_id for hit in result.hits},
                baseline,
                query,
            )
        # Backslash is a token separator for the FTS query builder, never a
        # SQL LIKE escape; both literal CJK terms must still be present.
        slash = self._hits("背包\\格子颜色", limit=10)
        self.assertEqual({hit.record.record_id for hit in slash.hits}, baseline)


class ContextRecallTests(_ChineseRecallFixture):
    def test_context_by_asset_recalls_rule_work_and_cpp_symbol(self) -> None:
        context = self.service.get_context(
            query="背包格子颜色",
            asset_paths=(ASSET_INVENTORY,),
            detail_level=2,
            budget=ContextBudget(max_chars=8_000, max_nodes=12, max_records=20, max_depth=2),
            recall_budget=RecallBudget(),
        )
        record_ids = [item["recordId"] for item in context["records"]]
        self.assertIn(self.ids["inventory_rule"], record_ids)
        titles = " ".join(item["title"] for item in context["records"])
        self.assertIn("背包格子颜色", titles)
        work_titles = " ".join(item["title"] for item in context["activeWork"])
        self.assertIn("修复背包格子颜色不一致", work_titles)
        self.assertNotIn("DSH session", str(context))

    def test_context_query_only_chinese_recall(self) -> None:
        context = self.service.get_context(
            query="换枪左手IK",
            detail_level=2,
            budget=ContextBudget(max_chars=8_000, max_nodes=12, max_records=20, max_depth=2),
            recall_budget=RecallBudget(),
        )
        record_ids = [item["recordId"] for item in context["records"]]
        self.assertIn(self.ids["weapon_rule"], record_ids)
        self.assertNotIn(self.ids["weapon_old"], record_ids)


class FallbackBoundaryTests(_ChineseRecallFixture):
    def test_missing_fts_index_is_rejected_not_silently_scanned(self) -> None:
        raw = sqlite3.connect(str(self.memory_path))
        try:
            raw.execute("DROP TABLE memory_records_fts")
            raw.commit()
            from ue_agent_kit.project_memory import search_memory_records_bounded

            with self.assertRaisesRegex(RuntimeError, "FTS5"):
                search_memory_records_bounded(
                    raw,
                    project_key=PROJECT,
                    query="背包格子颜色",
                )
        finally:
            raw.close()

    def test_scan_limit_and_deadline_are_hard_bounds(self) -> None:
        from ue_agent_kit.project_memory import search_memory_records_bounded

        with open_project_memory_database(self.memory_path) as connection:
            # Simulate a legacy/partial v7 row set whose expanded projection was
            # never populated, forcing the bounded literal fallback branch.
            connection.execute("UPDATE memory_records SET search_text = ''")
            connection.commit()
            outcome = search_memory_records_bounded(
                connection,
                project_key=PROJECT,
                query="背包",
                limit=10,
                scan_limit=1,
            )
            self.assertLessEqual(outcome.scanned, 1)
            self.assertTrue(outcome.truncated)
            self.assertLessEqual(len(outcome.hits), 10)

            timed_out = search_memory_records_bounded(
                connection,
                project_key=PROJECT,
                query="背包格子颜色",
                limit=10,
                scan_limit=50,
                deadline_ms=0,
            )
            self.assertTrue(timed_out.timed_out)
            self.assertEqual(timed_out.hits, ())

    def test_result_limit_caps_fallback_hits(self) -> None:
        with open_project_memory_database(self.memory_path) as connection:
            connection.execute("UPDATE memory_records SET search_text = ''")
            connection.commit()
        result = self._hits("背包", limit=1)
        self.assertEqual(len(result.hits), 1)
        self.assertEqual(result.retrieval_mode, "cjk-substring")
        self.assertTrue(result.substring_fallback)

    def test_overlong_query_is_clipped_and_reported(self) -> None:
        from ue_agent_kit.project_memory import (
            MAX_SUBSTRING_QUERY_CHARS,
            search_memory_records_bounded,
        )

        with open_project_memory_database(self.memory_path) as connection:
            outcome = search_memory_records_bounded(
                connection,
                project_key=PROJECT,
                query="背" * (MAX_SUBSTRING_QUERY_CHARS + 50),
                limit=5,
                scan_limit=10,
            )
            self.assertTrue(outcome.query_clipped)
            self.assertLessEqual(outcome.scanned, 10)


@unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
class McpChineseRecallTests(_ChineseRecallFixture):
    def setUp(self) -> None:
        super().setUp()
        from test_indexer_queries import make_asset, write_export
        from ue_agent_kit.database import open_database
        from ue_agent_kit.indexer import build_index

        self.index_path = self.root / "index" / "ueak.sqlite3"
        export_root = self.root / "export"
        write_export(
            export_root,
            [
                make_asset(
                    ASSET_INVENTORY,
                    profile="index",
                    revision="a" * 64,
                    rich=True,
                    project_name=PROJECT,
                )
            ],
        )
        with open_database(self.index_path) as connection:
            result = build_index(connection, export_root, self.index_path)
            self.assertGreaterEqual(result.added, 1)
        self.server = create_mcp_server(
            self.index_path,
            memory_service=self.service,
        )

    def test_ue_memory_search_returns_chinese_rule_with_source_and_status(self) -> None:
        _, payload = asyncio.run(
            self.server.call_tool("ue_memory_search", {"query": "背包格子颜色", "limit": 5})
        )
        self.assertTrue(payload["ok"], payload)
        self.assertGreaterEqual(payload["resultCount"], 1)
        self.assertEqual(payload["retrieval"]["retrievalMode"], "fts")
        self.assertFalse(payload["retrieval"]["substringFallback"])
        record_ids = [item["record"]["recordId"] for item in payload["items"]]
        self.assertIn(self.ids["inventory_rule"], record_ids)
        self.assertNotIn("DSH session", str(payload))

    def test_ue_memory_get_context_recalls_asset_rule_and_active_work(self) -> None:
        _, payload = asyncio.run(
            self.server.call_tool(
                "ue_memory_get_context",
                {
                    "query": "背包格子颜色",
                    "asset_paths": [ASSET_INVENTORY],
                    "detail_level": 2,
                },
            )
        )
        self.assertTrue(payload["ok"], payload)
        context = payload["context"]
        record_ids = [item["recordId"] for item in context["records"]]
        self.assertIn(self.ids["inventory_rule"], record_ids)
        self.assertTrue(any("背包格子颜色" in item["title"] for item in context["activeWork"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)

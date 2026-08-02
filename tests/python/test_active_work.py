from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.active_work import (  # noqa: E402
    WorkItemDraft,
    WorkStatus,
    add_work_todo,
    block_work_item,
    complete_work_item,
    create_work_item,
    list_work_items,
    resume_work_item,
    set_work_links,
    set_work_next_action,
    start_work_item,
)
from ue_agent_kit.memory_tree import (  # noqa: E402
    KnowledgeNodeDraft,
    create_knowledge_node,
    delete_knowledge_node,
)
from ue_agent_kit.project_memory import open_project_memory_database  # noqa: E402


PROJECT = "测试项目"
ASSET = "/Game/Characters/BP_Player.BP_Player"


class ActiveWorkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_active_work_")
        self.database_path = Path(self.temporary.name) / "memory.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def create_node(self, connection):
        create_knowledge_node(
            connection,
            KnowledgeNodeDraft(PROJECT, "/project", "project", PROJECT, "项目。"),
        )
        return create_knowledge_node(
            connection,
            KnowledgeNodeDraft(PROJECT, "/project/combat", "system", "Combat", "战斗系统。"),
        )

    def test_create_work_persists_normalized_links_and_todos(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            node = self.create_node(connection)
            work = create_work_item(
                connection,
                WorkItemDraft(
                    project_key=PROJECT,
                    title="调整伤害",
                    description="检查战斗伤害。",
                    next_action="运行测试。",
                    priority=80,
                    owner="agent",
                    node_ids=(node.node_id,),
                    asset_paths=(ASSET,),
                    details={"taskKey": "combat-balance"},
                ),
            )
            self.assertEqual(work.status, WorkStatus.IN_PROGRESS)
            self.assertEqual(work.node_ids, (node.node_id,))
            self.assertEqual(work.asset_paths, (ASSET,))
            self.assertEqual(work.details, {"taskKey": "combat-balance"})

            work = add_work_todo(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                text="确认基础伤害。",
            )
            self.assertEqual(len(work.todos), 1)
            self.assertEqual(work.todos[0].text, "确认基础伤害。")
            work = set_work_next_action(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                next_action="检查 DataTable。",
            )
            self.assertEqual(work.next_action, "检查 DataTable。")

    def test_state_machine_rejects_invalid_transitions(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.create_node(connection)
            work = create_work_item(
                connection,
                WorkItemDraft(
                    project_key=PROJECT,
                    title="计划任务",
                    description="尚未开始。",
                    next_action="开始。",
                    status=WorkStatus.PLANNED,
                ),
            )
            with self.assertRaisesRegex(ValueError, "planned to blocked"):
                block_work_item(
                    connection,
                    project_key=PROJECT,
                    work_item_id=work.work_item_id,
                    blocked_reason="错误阻塞。",
                )
            work = start_work_item(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
            )
            self.assertEqual(work.status, WorkStatus.IN_PROGRESS)
            with self.assertRaisesRegex(ValueError, "blocked_reason"):
                block_work_item(
                    connection,
                    project_key=PROJECT,
                    work_item_id=work.work_item_id,
                    blocked_reason="",
                )
            work = block_work_item(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                blocked_reason="等待资产。",
            )
            self.assertEqual(work.status, WorkStatus.BLOCKED)
            self.assertEqual(work.blocked_reason, "等待资产。")
            work = resume_work_item(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                next_action="继续验证。",
            )
            work = complete_work_item(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
            )
            self.assertEqual(work.status, WorkStatus.DONE)
            self.assertTrue(work.completed_at_utc)
            with self.assertRaisesRegex(ValueError, "done to blocked"):
                block_work_item(
                    connection,
                    project_key=PROJECT,
                    work_item_id=work.work_item_id,
                    blocked_reason="不允许。",
                )
            with self.assertRaisesRegex(ValueError, "completed or cancelled"):
                add_work_todo(
                    connection,
                    project_key=PROJECT,
                    work_item_id=work.work_item_id,
                    text="不允许。",
                )

    def test_links_validate_assets_nodes_and_protect_node_delete(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            node = self.create_node(connection)
            with self.assertRaisesRegex(ValueError, "/Game/"):
                create_work_item(
                    connection,
                    WorkItemDraft(
                        project_key=PROJECT,
                        title="Bad asset",
                        description="Bad asset path.",
                        next_action="Fix.",
                        asset_paths=("C:/Asset.uasset",),
                    ),
                )
            work = create_work_item(
                connection,
                WorkItemDraft(
                    project_key=PROJECT,
                    title="Linked work",
                    description="Linked to node.",
                    next_action="Continue.",
                    node_ids=(node.node_id,),
                ),
            )
            with self.assertRaisesRegex(ValueError, "Active Work"):
                delete_knowledge_node(connection, project_key=PROJECT, node_id=node.node_id)
            work = set_work_links(
                connection,
                project_key=PROJECT,
                work_item_id=work.work_item_id,
                node_ids=(),
                asset_paths=(ASSET,),
            )
            self.assertEqual(work.node_ids, ())
            self.assertEqual(work.asset_paths, (ASSET,))
            delete_knowledge_node(connection, project_key=PROJECT, node_id=node.node_id)

    def test_list_filters_active_status_node_asset_and_query(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            node = self.create_node(connection)
            expected = create_work_item(
                connection,
                WorkItemDraft(
                    project_key=PROJECT,
                    title="Combat validation",
                    description="Validate combat asset.",
                    next_action="Run test.",
                    node_ids=(node.node_id,),
                    asset_paths=(ASSET,),
                ),
            )
            create_work_item(
                connection,
                WorkItemDraft(
                    project_key=PROJECT,
                    title="Other task",
                    description="Other system.",
                    next_action="Wait.",
                ),
            )
            filtered = list_work_items(
                connection,
                project_key=PROJECT,
                node_ids=(node.node_id,),
                asset_paths=(ASSET,),
                query="Combat",
            )
            self.assertEqual([item.work_item_id for item in filtered], [expected.work_item_id])


if __name__ == "__main__":
    unittest.main()

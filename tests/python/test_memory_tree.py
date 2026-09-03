from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_schema import CURRENT_MEMORY_SCHEMA_VERSION  # noqa: E402
from ue_agent_kit.memory_tree import (  # noqa: E402
    KnowledgeNodeDraft,
    attach_memory_record_to_node,
    create_knowledge_node,
    delete_knowledge_node,
    expand_knowledge_tree,
    get_knowledge_node_by_path,
    normalize_knowledge_path,
    update_knowledge_node,
)
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemorySourceKind,
    create_memory_record,
    get_memory_record,
    open_project_memory_database,
)


PROJECT = "测试项目"
OTHER_PROJECT = "其他项目"


class MemoryTreeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_tree_")
        self.database_path = Path(self.temporary.name) / "memory.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def root(self, connection, project_key: str = PROJECT):
        return create_knowledge_node(
            connection,
            KnowledgeNodeDraft(
                project_key=project_key,
                path="/Project",
                node_type="project",
                title=project_key,
                summary="项目根节点。",
            ),
        )

    def test_schema_v5_preserves_tree_and_nullable_record_binding(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                5,
            )
            self.assertEqual(CURRENT_MEMORY_SCHEMA_VERSION, 5)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("knowledge_nodes", tables)
            self.assertIn("active_work_items", tables)
            self.assertIn("memory_l0_events", tables)
            self.assertIn("memory_evidence_chains", tables)
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(memory_records)").fetchall()
            }
            self.assertIn("node_id", columns)

    def test_paths_normalize_and_require_existing_structural_parent(self) -> None:
        self.assertEqual(normalize_knowledge_path("/Project/Combat/Weapons/"), "/project/combat/weapons")
        with self.assertRaisesRegex(ValueError, "absolute knowledge path"):
            normalize_knowledge_path("project/combat")
        with self.assertRaisesRegex(ValueError, "descendant of /project"):
            normalize_knowledge_path("/combat")

        with open_project_memory_database(self.database_path) as connection:
            self.root(connection)
            with self.assertRaisesRegex(KeyError, "Knowledge node not found"):
                create_knowledge_node(
                    connection,
                    KnowledgeNodeDraft(
                        project_key=PROJECT,
                        path="/project/combat/weapons",
                        node_type="component",
                        title="Weapons",
                        summary="武器。",
                    ),
                )
            combat = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/Project/Combat",
                    node_type="system",
                    title="Combat",
                    summary="战斗系统。",
                ),
            )
            self.assertEqual(combat.path, "/project/combat")
            with self.assertRaisesRegex(ValueError, "already exists"):
                create_knowledge_node(
                    connection,
                    KnowledgeNodeDraft(
                        project_key=PROJECT,
                        path="/project/COMBAT",
                        node_type="system",
                        title="Duplicate",
                        summary="重复。",
                    ),
                )

    def test_parent_cannot_cross_project_or_create_cycle(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.root(connection)
            other_root = self.root(connection, OTHER_PROJECT)
            with self.assertRaisesRegex(KeyError, "Knowledge node not found"):
                create_knowledge_node(
                    connection,
                    KnowledgeNodeDraft(
                        project_key=PROJECT,
                        path="/project/cross",
                        parent_node_id=other_root.node_id,
                        node_type="feature",
                        title="Cross",
                        summary="跨项目。",
                    ),
                )

            parent = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/project/a",
                    node_type="system",
                    title="A",
                    summary="A。",
                ),
            )
            child = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    project_key=PROJECT,
                    path="/project/a/b",
                    node_type="component",
                    title="B",
                    summary="B。",
                ),
            )
            with self.assertRaisesRegex(ValueError, "cycle"):
                update_knowledge_node(
                    connection,
                    project_key=PROJECT,
                    node_id=parent.node_id,
                    path="/project/a/b/a",
                    parent_node_id=child.node_id,
                )

    def test_moving_node_updates_descendant_paths(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.root(connection)
            source = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(PROJECT, "/project/source", "system", "Source", "Source。"),
            )
            child = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(PROJECT, "/project/source/child", "component", "Child", "Child。"),
            )
            target = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(PROJECT, "/project/target", "system", "Target", "Target。"),
            )
            moved = update_knowledge_node(
                connection,
                project_key=PROJECT,
                node_id=source.node_id,
                path="/project/target/source",
                parent_node_id=target.node_id,
            )
            self.assertEqual(moved.path, "/project/target/source")
            self.assertEqual(
                get_knowledge_node_by_path(
                    connection,
                    project_key=PROJECT,
                    path="/project/target/source/child",
                ).node_id,
                child.node_id,
            )
            expanded = expand_knowledge_tree(
                connection,
                project_key=PROJECT,
                path="/project/target",
                max_depth=3,
            )
            self.assertEqual([depth for _, depth in expanded], [0, 1, 2])

    def test_delete_rejects_children_and_attached_records(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.root(connection)
            node = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(PROJECT, "/project/system", "system", "System", "System。"),
            )
            child = create_knowledge_node(
                connection,
                KnowledgeNodeDraft(
                    PROJECT,
                    "/project/system/component",
                    "component",
                    "Component",
                    "Component。",
                ),
            )
            with self.assertRaisesRegex(ValueError, "child nodes"):
                delete_knowledge_node(connection, project_key=PROJECT, node_id=node.node_id)
            delete_knowledge_node(connection, project_key=PROJECT, node_id=child.node_id)

            record = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="system:fact",
                    title="Fact",
                    body="Stable fact.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                ),
            )
            self.assertEqual(record.node_id, "")
            attach_memory_record_to_node(
                connection,
                project_key=PROJECT,
                record_id=record.record_id,
                node_id=node.node_id,
            )
            self.assertEqual(get_memory_record(connection, record.record_id).node_id, node.node_id)
            with self.assertRaisesRegex(ValueError, "memory records"):
                delete_knowledge_node(connection, project_key=PROJECT, node_id=node.node_id)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_schema import CURRENT_MEMORY_SCHEMA_VERSION  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryArtifact,
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRelationKind,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
    MemoryStatus,
    create_memory_record,
    get_memory_record,
    invalidate_memory_revisions,
    list_memory_records,
    mark_memory_record_superseded,
    open_project_memory_database,
)


PROJECT = "测试项目"
ASSET = "/Game/Characters/BP_Player.BP_Player"


class ProjectMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_")
        self.database_path = Path(self.temporary.name) / "中文目录" / "memory.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def draft(
        self,
        *,
        record_type: MemoryRecordType = MemoryRecordType.PROJECT_FACT,
        subject_key: str = "asset:player:max-health",
        body: str = "玩家默认生命值为 100。",
        source_kind: MemorySourceKind = MemorySourceKind.USER_CONFIRMED,
        revision_set: tuple[MemoryRevision, ...] = (),
    ) -> MemoryRecordDraft:
        return MemoryRecordDraft(
            project_key=PROJECT,
            record_type=record_type,
            subject_key=subject_key,
            title="玩家生命值",
            body=body,
            source_kind=source_kind,
            source_ref="conversation:user-confirmation",
            confidence=0.95,
            scopes=(
                MemoryScope(MemoryScopeType.PROJECT, PROJECT),
                MemoryScope(MemoryScopeType.ASSET, ASSET, {"assetClass": "Blueprint"}),
            ),
            revision_set=revision_set,
            artifacts=(
                MemoryArtifact(
                    "validationEvidence",
                    "Output/Validation/player-health.json",
                    {"result": "passed"},
                ),
            ),
            details={"owner": "design"},
        )

    def test_schema_migration_and_fts_round_trip(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                CURRENT_MEMORY_SCHEMA_VERSION,
            )
            record = create_memory_record(connection, self.draft())
            matches = connection.execute(
                "SELECT title FROM memory_records_fts WHERE memory_records_fts MATCH ?",
                ("玩家生命值",),
            ).fetchall()
            self.assertEqual([str(row[0]) for row in matches], [record.title])

        with open_project_memory_database(self.database_path, readonly=True) as connection:
            self.assertEqual(get_memory_record(connection, record.record_id).body, record.body)

    def test_user_confirmed_record_is_valid_and_round_trips_bindings(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            record = create_memory_record(connection, self.draft())

            self.assertRegex(record.record_id, r"^mem_[0-9a-f]{32}$")
            self.assertEqual(record.record_type, MemoryRecordType.PROJECT_FACT)
            self.assertEqual(record.source_kind, MemorySourceKind.USER_CONFIRMED)
            self.assertEqual(record.status, MemoryStatus.VALID)
            self.assertEqual(record.scopes[1].scope_type, MemoryScopeType.ASSET)
            self.assertEqual(record.scopes[1].details, {"assetClass": "Blueprint"})
            self.assertEqual(record.artifacts[0].artifact_kind, "validationEvidence")
            self.assertEqual(record.details, {"owner": "design"})
            self.assertTrue(record.content_sha256.startswith("sha256:"))

    def test_model_inferred_record_starts_unverified(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            record = create_memory_record(
                connection,
                self.draft(source_kind=MemorySourceKind.MODEL_INFERRED),
            )
            self.assertEqual(record.status, MemoryStatus.UNVERIFIED)

    def test_tool_observation_requires_stable_revision_to_start_valid(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            stable = create_memory_record(
                connection,
                self.draft(
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    revision_set=(MemoryRevision(ASSET, "sha256:a", True),),
                ),
            )
            unstable = create_memory_record(
                connection,
                self.draft(
                    record_type=MemoryRecordType.RUNTIME_EVIDENCE,
                    subject_key="run:automation:player",
                    body="Automation 测试通过。",
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    revision_set=(MemoryRevision(ASSET, "session:dirty", False),),
                ),
            )

            self.assertEqual(stable.status, MemoryStatus.VALID)
            self.assertEqual(unstable.status, MemoryStatus.UNVERIFIED)

    def test_matching_revision_remains_valid(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            record = create_memory_record(
                connection,
                self.draft(
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    revision_set=(MemoryRevision(ASSET, "sha256:a", True),),
                ),
            )
            result = invalidate_memory_revisions(
                connection,
                project_key=PROJECT,
                current_revisions={ASSET: "sha256:a"},
            )

            self.assertEqual(result.checked_record_ids, (record.record_id,))
            self.assertEqual(result.stale_record_ids, ())
            self.assertEqual(get_memory_record(connection, record.record_id).status, MemoryStatus.VALID)

    def test_revision_mismatch_marks_record_stale_and_records_reason(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            record = create_memory_record(
                connection,
                self.draft(
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    revision_set=(MemoryRevision(ASSET, "sha256:a", True),),
                ),
            )
            result = invalidate_memory_revisions(
                connection,
                project_key=PROJECT,
                current_revisions={ASSET: "sha256:b"},
            )

            self.assertEqual(result.stale_record_ids, (record.record_id,))
            self.assertEqual(
                result.reasons[record.record_id]["mismatchedRevisions"][0],
                {
                    "assetPath": ASSET,
                    "expectedRevision": "sha256:a",
                    "currentRevision": "sha256:b",
                },
            )
            self.assertEqual(get_memory_record(connection, record.record_id).status, MemoryStatus.STALE)
            event = connection.execute(
                """
                SELECT from_status, to_status, reason
                FROM memory_status_events
                WHERE record_id = ?
                ORDER BY event_id DESC
                LIMIT 1
                """,
                (record.record_id,),
            ).fetchone()
            self.assertEqual(tuple(event), ("valid", "stale", "revision-set-mismatch"))

    def test_conflicting_records_coexist_without_overwrite(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            first = create_memory_record(connection, self.draft(body="玩家生命值为 100。"))
            second = create_memory_record(connection, self.draft(body="玩家生命值为 120。"))

            first = get_memory_record(connection, first.record_id)
            second = get_memory_record(connection, second.record_id)
            self.assertEqual(first.status, MemoryStatus.CONFLICTED)
            self.assertEqual(second.status, MemoryStatus.CONFLICTED)
            self.assertNotEqual(first.content_sha256, second.content_sha256)
            self.assertEqual(len(list_memory_records(connection, project_key=PROJECT)), 2)
            self.assertEqual(first.relations[0].relation_kind, MemoryRelationKind.CONFLICTS_WITH)
            self.assertEqual(first.relations[0].target_record_id, second.record_id)
            self.assertEqual(second.relations[0].target_record_id, first.record_id)

    def test_task_records_do_not_auto_conflict(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            first = create_memory_record(
                connection,
                self.draft(
                    record_type=MemoryRecordType.TASK_RECORD,
                    subject_key="task:memory-schema",
                    body="任务尚未开始。",
                ),
            )
            second = create_memory_record(
                connection,
                self.draft(
                    record_type=MemoryRecordType.TASK_RECORD,
                    subject_key="task:memory-schema",
                    body="任务正在进行。",
                ),
            )

            self.assertEqual(first.status, MemoryStatus.VALID)
            self.assertEqual(second.status, MemoryStatus.VALID)
            self.assertEqual(first.relations, ())
            self.assertEqual(second.relations, ())

    def test_supersede_preserves_both_records_and_links_replacement(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            first = create_memory_record(
                connection,
                self.draft(
                    record_type=MemoryRecordType.TASK_RECORD,
                    subject_key="task:memory-schema",
                    body="旧任务结论。",
                ),
            )
            replacement = create_memory_record(
                connection,
                self.draft(
                    record_type=MemoryRecordType.TASK_RECORD,
                    subject_key="task:memory-schema",
                    body="新任务结论。",
                ),
            )
            superseded = mark_memory_record_superseded(
                connection,
                record_id=first.record_id,
                replacement_record_id=replacement.record_id,
                reason="task-result-updated",
            )

            self.assertEqual(superseded.status, MemoryStatus.SUPERSEDED)
            self.assertEqual(superseded.superseded_by_record_id, replacement.record_id)
            replacement = get_memory_record(connection, replacement.record_id)
            self.assertEqual(replacement.relations[0].relation_kind, MemoryRelationKind.SUPERSEDES)
            self.assertEqual(replacement.relations[0].target_record_id, first.record_id)
            self.assertEqual(len(list_memory_records(connection, project_key=PROJECT)), 2)

    def test_invalid_drafts_are_rejected_before_storage(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            with self.assertRaisesRegex(ValueError, "confidence"):
                create_memory_record(
                    connection,
                    MemoryRecordDraft(
                        project_key=PROJECT,
                        record_type=MemoryRecordType.PROJECT_FACT,
                        subject_key="fact:x",
                        title="x",
                        body="x",
                        source_kind=MemorySourceKind.USER_CONFIRMED,
                        confidence=1.1,
                    ),
                )
            with self.assertRaisesRegex(ValueError, "finite number"):
                create_memory_record(
                    connection,
                    MemoryRecordDraft(
                        project_key=PROJECT,
                        record_type=MemoryRecordType.PROJECT_FACT,
                        subject_key="fact:nan",
                        title="x",
                        body="x",
                        source_kind=MemorySourceKind.USER_CONFIRMED,
                        confidence=float("nan"),
                    ),
                )
            with self.assertRaisesRegex(ValueError, "/Game/"):
                create_memory_record(
                    connection,
                    self.draft(
                        revision_set=(MemoryRevision("C:/Asset.uasset", "sha256:a"),),
                    ),
                )
            with self.assertRaisesRegex(ValueError, "must be boolean"):
                create_memory_record(
                    connection,
                    self.draft(
                        revision_set=(MemoryRevision(ASSET, "sha256:a", "false"),),  # type: ignore[arg-type]
                    ),
                )
            with self.assertRaisesRegex(ValueError, "one JSON object"):
                create_memory_record(
                    connection,
                    MemoryRecordDraft(
                        project_key=PROJECT,
                        record_type=MemoryRecordType.PROJECT_FACT,
                        subject_key="fact:details",
                        title="x",
                        body="x",
                        source_kind=MemorySourceKind.USER_CONFIRMED,
                        details=[],  # type: ignore[arg-type]
                    ),
                )
            with self.assertRaisesRegex(ValueError, "duplicate"):
                create_memory_record(
                    connection,
                    MemoryRecordDraft(
                        project_key=PROJECT,
                        record_type=MemoryRecordType.PROJECT_RULE,
                        subject_key="rule:x",
                        title="x",
                        body="x",
                        source_kind=MemorySourceKind.USER_CONFIRMED,
                        scopes=(
                            MemoryScope(MemoryScopeType.PROJECT, PROJECT),
                            MemoryScope(MemoryScopeType.PROJECT, PROJECT),
                        ),
                    ),
                )
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0], 0)

    def test_newer_memory_schema_is_rejected(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            connection.execute(f"PRAGMA user_version = {CURRENT_MEMORY_SCHEMA_VERSION + 1}")
            connection.commit()

        with self.assertRaises(RuntimeError):
            with open_project_memory_database(self.database_path):
                pass

    def test_readonly_open_rejects_uninitialized_database(self) -> None:
        path = Path(self.temporary.name) / "uninitialized.sqlite3"
        sqlite3.connect(path).close()
        with self.assertRaises(RuntimeError):
            with open_project_memory_database(path, readonly=True):
                pass


if __name__ == "__main__":
    unittest.main()

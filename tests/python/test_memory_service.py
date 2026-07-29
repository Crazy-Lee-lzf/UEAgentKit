from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import open_database, set_metadata  # noqa: E402
from ue_agent_kit.memory_service import (  # noqa: E402
    ProjectMemoryService,
    ProjectMemoryServiceError,
)
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
    MemoryStatus,
    create_memory_record,
    invalidate_memory_revisions,
    open_project_memory_database,
)


PROJECT = "测试项目"
OTHER_PROJECT = "其他项目"
ASSET = "/Game/Characters/BP_Player.BP_Player"


class ProjectMemoryServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_service_")
        root = Path(self.temporary.name)
        self.memory_path = root / "memory.sqlite3"
        self.index_path = root / "index.sqlite3"
        self.service = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def draft(
        self,
        *,
        project_key: str = PROJECT,
        record_type: MemoryRecordType = MemoryRecordType.PROJECT_FACT,
        subject_key: str = "asset:player:max-health",
        body: str = "Player max health is 100.",
        source_kind: MemorySourceKind = MemorySourceKind.USER_CONFIRMED,
        revision_set: tuple[MemoryRevision, ...] = (),
    ) -> MemoryRecordDraft:
        return MemoryRecordDraft(
            project_key=project_key,
            record_type=record_type,
            subject_key=subject_key,
            title="Player max health",
            body=body,
            source_kind=source_kind,
            source_ref="test",
            scopes=(MemoryScope(MemoryScopeType.ASSET, ASSET),),
            revision_set=revision_set,
        )

    def write_index(self, *, project_key: str = PROJECT, revision: str = "sha256:a") -> None:
        with open_database(self.index_path) as connection:
            set_metadata(connection, "project_key", project_key)
            connection.execute("DELETE FROM assets")
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path,
                    asset_name,
                    package_name,
                    revision_value,
                    schema_version,
                    exporter_version,
                    profile,
                    canonical_sha256,
                    canonical_relpath,
                    indexed_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ASSET,
                    "BP_Player",
                    "/Game/Characters/BP_Player",
                    revision,
                    "1.1",
                    "0.5.5",
                    "index",
                    "canonical",
                    "canonical/player.json",
                    "2026-07-29T00:00:00.000Z",
                ),
            )
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def test_status_initializes_database_and_reports_counts(self) -> None:
        empty = self.service.status()
        self.assertEqual(empty.project_key, PROJECT)
        self.assertEqual(empty.record_count, 0)
        self.assertEqual(empty.counts_by_type, {})
        self.assertEqual(empty.counts_by_status, {})

        record = self.service.add_record(self.draft())
        status = self.service.status()
        self.assertEqual(status.record_count, 1)
        self.assertEqual(status.counts_by_type, {MemoryRecordType.PROJECT_FACT.value: 1})
        self.assertEqual(status.counts_by_status, {MemoryStatus.VALID.value: 1})
        self.assertEqual(self.service.get_record(record.record_id).record_id, record.record_id)

    def test_add_record_rejects_non_fixed_project(self) -> None:
        with self.assertRaisesRegex(TypeError, "MemoryRecordDraft"):
            self.service.add_record(object())  # type: ignore[arg-type]
        with self.assertRaisesRegex(ProjectMemoryServiceError, "fixed project") as raised:
            self.service.add_record(self.draft(project_key=OTHER_PROJECT))
        self.assertEqual(raised.exception.code, "memory-project-mismatch")
        self.assertFalse(self.memory_path.exists())

    def test_get_record_rejects_record_from_another_project(self) -> None:
        with open_project_memory_database(self.memory_path) as connection:
            other = create_memory_record(connection, self.draft(project_key=OTHER_PROJECT))
        with self.assertRaisesRegex(ProjectMemoryServiceError, "another project") as raised:
            self.service.get_record(other.record_id)
        self.assertEqual(raised.exception.code, "memory-record-project-mismatch")

    def test_search_is_fixed_project_filtered_and_supports_scope(self) -> None:
        expected = self.service.add_record(self.draft())
        with open_project_memory_database(self.memory_path) as connection:
            create_memory_record(
                connection,
                self.draft(project_key=OTHER_PROJECT, body="Player max health is 999."),
            )

        hits = self.service.search_records(
            query="max health",
            scope_type=MemoryScopeType.ASSET,
            scope_key=ASSET,
        )
        self.assertEqual([hit.record.record_id for hit in hits], [expected.record_id])
        self.assertIsInstance(hits[0].rank, float)
        quoted = self.service.search_records(query='max "health"')
        self.assertEqual([hit.record.record_id for hit in quoted], [expected.record_id])
        with self.assertRaisesRegex(ValueError, "searchable token"):
            self.service.search_records(query='""')

    def test_search_excludes_stale_by_default_but_can_include_it(self) -> None:
        record = self.service.add_record(
            self.draft(
                source_kind=MemorySourceKind.TOOL_OBSERVED,
                revision_set=(MemoryRevision(ASSET, "sha256:a"),),
            )
        )
        with open_project_memory_database(self.memory_path) as connection:
            invalidate_memory_revisions(
                connection,
                project_key=PROJECT,
                current_revisions={ASSET: "sha256:b"},
            )

        self.assertEqual(self.service.search_records(query="max health"), ())
        hits = self.service.search_records(query="max health", statuses=(MemoryStatus.STALE,))
        self.assertEqual([hit.record.record_id for hit in hits], [record.record_id])

    def test_validate_against_index_marks_mismatched_revision_stale(self) -> None:
        record = self.service.add_record(
            self.draft(
                source_kind=MemorySourceKind.TOOL_OBSERVED,
                revision_set=(MemoryRevision(ASSET, "sha256:a"),),
            )
        )
        self.write_index(revision="sha256:a")
        matching = self.service.validate_against_index(self.index_path)
        self.assertEqual(matching.indexed_asset_count, 1)
        self.assertEqual(matching.invalidation.checked_record_ids, (record.record_id,))
        self.assertEqual(matching.invalidation.stale_record_ids, ())

        self.write_index(revision="sha256:b")
        mismatched = self.service.validate_against_index(self.index_path)
        self.assertEqual(mismatched.invalidation.stale_record_ids, (record.record_id,))
        self.assertEqual(self.service.get_record(record.record_id).status, MemoryStatus.STALE)

    def test_validate_against_index_rejects_other_project(self) -> None:
        self.write_index(project_key=OTHER_PROJECT)
        with self.assertRaisesRegex(ProjectMemoryServiceError, "does not match") as raised:
            self.service.validate_against_index(self.index_path)
        self.assertEqual(raised.exception.code, "memory-index-project-mismatch")
        self.assertFalse(self.memory_path.exists())

    def test_service_supersede_keeps_fixed_project_boundary(self) -> None:
        first = self.service.add_record(
            self.draft(
                record_type=MemoryRecordType.TASK_RECORD,
                subject_key="task:memory-service",
                body="Old result.",
            )
        )
        replacement = self.service.add_record(
            self.draft(
                record_type=MemoryRecordType.TASK_RECORD,
                subject_key="task:memory-service",
                body="New result.",
            )
        )
        superseded = self.service.mark_superseded(
            record_id=first.record_id,
            replacement_record_id=replacement.record_id,
            reason="result-updated",
        )
        self.assertEqual(superseded.status, MemoryStatus.SUPERSEDED)
        self.assertEqual(superseded.superseded_by_record_id, replacement.record_id)


if __name__ == "__main__":
    unittest.main()

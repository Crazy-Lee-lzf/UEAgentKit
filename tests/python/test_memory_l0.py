from __future__ import annotations

import hashlib
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_l0 import (  # noqa: E402
    MAX_L0_CAPTURE_BATCH_EVENTS,
    MemoryEvidenceChainDraft,
    MemoryL0CaptureService,
    MemoryL0EventDraft,
    MemoryL0Error,
)
from ue_agent_kit.memory_schema import (  # noqa: E402
    CURRENT_MEMORY_SCHEMA_VERSION,
    MEMORY_MIGRATIONS,
)
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.project_memory import open_project_memory_database  # noqa: E402


PROJECT = "测试项目"
ASSET = "/Game/Characters/BP_Player.BP_Player"


class MemoryL0Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_l0_")
        self.root = Path(self.temporary.name)
        self.artifact_root = self.root / "workflow"
        self.artifact_root.mkdir()
        self.database_path = self.root / "memory.sqlite3"
        self.service = MemoryL0CaptureService(
            database_path=self.database_path,
            project_key=PROJECT,
            artifact_root=self.artifact_root,
        )
        self.artifact = self.artifact_root / "live" / "receipt" / "record.json"
        self.artifact.parent.mkdir(parents=True)
        self.artifact.write_bytes(b'{"state":"applied"}')

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def artifact_draft(
        self,
        *,
        event_kind: str = "live_write",
        artifact: Path | None = None,
        hypothesis_id: str = "",
    ) -> MemoryL0EventDraft:
        return self.service.artifact_draft(
            artifact_path=artifact or self.artifact,
            event_kind=event_kind,
            lifecycle_state="applied",
            outcome="success",
            asset_paths=(ASSET,),
            change_set_id="cs_test",
            hypothesis_id=hypothesis_id,
            details={"operationCount": 1},
        )

    def test_artifact_capture_is_relative_exact_and_idempotent(self) -> None:
        draft = self.artifact_draft()
        first = self.service.append_event(draft)
        second = self.service.append_event(draft)
        event = self.service.get_event(first.event_ids[0])

        self.assertEqual(first.captured_count, 1)
        self.assertEqual(second.captured_count, 0)
        self.assertEqual(second.existing_count, 1)
        self.assertEqual(event.artifact_ref, "live/receipt/record.json")
        self.assertNotIn(str(self.root), event.artifact_ref)
        self.assertEqual(
            event.artifact_digest,
            hashlib.sha256(self.artifact.read_bytes()).hexdigest(),
        )
        self.assertEqual(event.asset_paths, (ASSET,))

    def test_artifact_digest_streams_without_reading_whole_file(self) -> None:
        expected = hashlib.sha256(self.artifact.read_bytes()).hexdigest()
        with patch.object(Path, "read_bytes", side_effect=AssertionError("whole-file read forbidden")):
            draft = self.artifact_draft()
        self.assertEqual(draft.artifact_digest, expected)

    def test_realistic_v3_migrates_in_place_and_preserves_data(self) -> None:
        connection = sqlite3.connect(self.database_path)
        try:
            for migration in MEMORY_MIGRATIONS[:3]:
                connection.executescript(migration.sql)
                connection.execute(
                    """
                    INSERT INTO memory_schema_migrations(
                        version, description, applied_at_utc
                    ) VALUES (?, ?, ?)
                    """,
                    (
                        migration.version,
                        migration.description,
                        "2026-08-30T00:00:00Z",
                    ),
                )
            connection.execute("PRAGMA user_version = 3")
            node_id = "kn_" + "1" * 32
            record_id = "mem_" + "2" * 32
            work_id = "work_" + "3" * 32
            connection.execute(
                """
                INSERT INTO knowledge_nodes(
                    node_id, project_key, path, parent_node_id, node_type,
                    title, summary, created_at_utc, updated_at_utc,
                    details_json
                ) VALUES (?, ?, '/project', NULL, 'project', ?, '', ?, ?, '{}')
                """,
                (
                    node_id,
                    PROJECT,
                    PROJECT,
                    "2026-08-30T00:00:00Z",
                    "2026-08-30T00:00:00Z",
                ),
            )
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, project_key, record_type, subject_key,
                    title, body, source_kind, source_ref, confidence, status,
                    content_sha256, created_at_utc, observed_at_utc,
                    updated_at_utc, details_json, evidence_sha256, node_id
                ) VALUES (
                    ?, ?, 'projectFact', 'legacy:v3', 'Legacy', 'Preserved',
                    'tool-observed', 'legacy:v3', 1.0, 'valid', ?, ?, ?, ?,
                    '{}', ?, ?
                )
                """,
                (
                    record_id,
                    PROJECT,
                    "sha256:" + "a" * 64,
                    "2026-08-30T00:00:00Z",
                    "2026-08-30T00:00:00Z",
                    "2026-08-30T00:00:00Z",
                    "sha256:" + "b" * 64,
                    node_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO active_work_items(
                    work_item_id, project_key, title, status, priority,
                    description, next_action, blocked_reason, owner,
                    created_at_utc, updated_at_utc, completed_at_utc,
                    details_json
                ) VALUES (
                    ?, ?, 'Legacy work', 'in_progress', 50, '', 'Continue',
                    '', 'agent', ?, ?, '', '{}'
                )
                """,
                (
                    work_id,
                    PROJECT,
                    "2026-08-30T00:00:00Z",
                    "2026-08-30T00:00:00Z",
                ),
            )
            connection.commit()
        finally:
            connection.close()

        with open_project_memory_database(self.database_path) as migrated:
            self.assertEqual(
                int(migrated.execute("PRAGMA user_version").fetchone()[0]),
                CURRENT_MEMORY_SCHEMA_VERSION,
            )
            self.assertEqual(
                migrated.execute(
                    "SELECT COUNT(*) FROM memory_records"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                migrated.execute(
                    "SELECT COUNT(*) FROM knowledge_nodes"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                migrated.execute(
                    "SELECT COUNT(*) FROM active_work_items"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                migrated.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )
        with open_project_memory_database(self.database_path) as reopened:
            self.assertEqual(
                reopened.execute(
                    "SELECT COUNT(*) FROM memory_schema_migrations"
                ).fetchone()[0],
                4,
            )

    def test_changed_artifact_at_same_path_appends_new_state(self) -> None:
        first = self.service.append_event(self.artifact_draft())
        self.artifact.write_bytes(b'{"state":"verified"}')
        second = self.service.append_event(self.artifact_draft())
        self.assertNotEqual(first.event_ids, second.event_ids)
        self.assertEqual(len(self.service.list_events()), 2)

    def test_restart_replay_dedupes_exact_state(self) -> None:
        draft = self.artifact_draft()
        first = self.service.append_event(draft)
        restarted = MemoryL0CaptureService(
            database_path=self.database_path,
            project_key=PROJECT,
            artifact_root=self.artifact_root,
        )
        replay = restarted.append_event(draft)
        self.assertEqual(replay.captured_count, 0)
        self.assertEqual(replay.existing_count, 1)
        self.assertEqual(replay.event_ids, first.event_ids)

    def test_artifact_outside_fixed_root_is_rejected(self) -> None:
        outside = self.root / "outside.json"
        outside.write_text("{}", encoding="utf-8")
        with self.assertRaisesRegex(MemoryL0Error, "escaped"):
            self.artifact_draft(artifact=outside)

    def test_batch_is_atomic_and_bounded(self) -> None:
        drafts = tuple(
            replace(
                self.artifact_draft(),
                event_kind="change_set",
                source_ref=f"artifact:state/{index}.json",
            )
            for index in range(MAX_L0_CAPTURE_BATCH_EVENTS)
        )
        with self.assertRaisesRegex(ValueError, "batch bound"):
            self.service.append_events(drafts + (drafts[0],))

        invalid_fk = replace(drafts[1], hypothesis_id="chain_" + "f" * 32)
        with self.assertRaises(sqlite3.IntegrityError):
            self.service.append_events((drafts[0], invalid_fk))
        self.assertEqual(self.service.list_events(), ())

    def test_asset_paths_truncate_and_details_are_bounded(self) -> None:
        paths = tuple(f"/Game/Test/A{index}.A{index}" for index in range(20))
        result = self.service.append_event(
            replace(self.artifact_draft(), asset_paths=paths)
        )
        event = self.service.get_event(result.event_ids[0])
        self.assertEqual(len(event.asset_paths), 16)
        self.assertTrue(event.details["assetPathsTruncated"])

        with self.assertRaisesRegex(ValueError, "4096"):
            self.service.append_event(
                replace(
                    self.artifact_draft(event_kind="change_set"),
                    details={"value": "x" * 4096},
                )
            )

    def test_list_filters_are_fixed_project_and_bounded(self) -> None:
        event_id = self.service.append_event(self.artifact_draft()).event_ids[0]
        self.assertEqual(
            [item.event_id for item in self.service.list_events(
                event_kinds=("live_write",),
                change_set_id="cs_test",
                distilled=False,
            )],
            [event_id],
        )
        self.assertEqual(
            self.service.list_events(event_kinds=("checkpoint",)),
            (),
        )
        with self.assertRaisesRegex(ValueError, "between 1 and 100"):
            self.service.list_events(limit=101)

    def test_inline_rejection_is_bounded_and_deterministic(self) -> None:
        first = self.service.capture_rejection(
            operation="apply_asset_property_live",
            error_code="revision-mismatch",
            asset_paths=(ASSET,),
            change_set_id="cs_test",
            target_identity="asset-property:IntValue",
        )
        second = self.service.capture_rejection(
            operation="apply_asset_property_live",
            error_code="revision-mismatch",
            asset_paths=(ASSET,),
            change_set_id="cs_test",
            target_identity="asset-property:IntValue",
        )
        event = self.service.get_event(first.event_ids[0])
        self.assertEqual(second.existing_count, 1)
        self.assertEqual(event.artifact_ref, "")
        self.assertEqual(
            event.details,
            {
                "errorCode": "revision-mismatch",
                "operation": "apply_asset_property_live",
                "targetIdentity": "asset-property:IntValue",
            },
        )

    def test_evidence_chain_foundation_and_optional_event_fk(self) -> None:
        chain = self.service.create_evidence_chain(
            MemoryEvidenceChainDraft(
                project_key=PROJECT,
                hypothesis="The Writer preserved the requested value.",
                context={"changeSetId": "cs_test"},
            )
        )
        self.assertEqual(chain.verdict, "inconclusive")
        self.assertEqual(chain.confidence, "low")
        self.assertEqual(
            self.service.get_evidence_chain(chain.chain_id),
            chain,
        )
        self.assertEqual(self.service.list_evidence_chains(), (chain,))

        event = self.service.append_event(
            self.artifact_draft(hypothesis_id=chain.chain_id)
        )
        self.assertEqual(
            self.service.get_event(event.event_ids[0]).hypothesis_id,
            chain.chain_id,
        )
        replacement = self.service.create_evidence_chain(
            MemoryEvidenceChainDraft(
                project_key=PROJECT,
                hypothesis="Replacement hypothesis.",
            )
        )
        superseded = self.service.create_evidence_chain(
            MemoryEvidenceChainDraft(
                project_key=PROJECT,
                hypothesis="Older hypothesis.",
                superseded_by=replacement.chain_id,
            )
        )
        self.assertEqual(superseded.superseded_by, replacement.chain_id)

    def test_evidence_chain_rejects_cross_project_supersession(self) -> None:
        other = MemoryL0CaptureService(
            database_path=self.database_path,
            project_key="Other",
            artifact_root=self.artifact_root,
        ).create_evidence_chain(
            MemoryEvidenceChainDraft(
                project_key="Other",
                hypothesis="Other project hypothesis.",
            )
        )
        with self.assertRaisesRegex(ValueError, "fixed project"):
            self.service.create_evidence_chain(
                MemoryEvidenceChainDraft(
                    project_key=PROJECT,
                    hypothesis="Replacement hypothesis.",
                    superseded_by=other.chain_id,
                )
            )
        with self.assertRaisesRegex(MemoryL0Error, "another project"):
            self.service.append_event(
                self.artifact_draft(hypothesis_id=other.chain_id)
            )

    def test_project_memory_status_exposes_l0_counts(self) -> None:
        memory = ProjectMemoryService(
            database_path=self.database_path,
            project_key=PROJECT,
        )
        empty = memory.status()
        self.assertEqual(empty.l0_event_count, 0)
        self.assertEqual(empty.pending_l0_event_count, 0)
        self.assertEqual(empty.evidence_chain_count, 0)

        self.service.append_event(self.artifact_draft())
        self.service.create_evidence_chain(
            MemoryEvidenceChainDraft(
                project_key=PROJECT,
                hypothesis="Counted chain.",
            )
        )
        populated = memory.status()
        self.assertEqual(populated.l0_event_count, 1)
        self.assertEqual(populated.pending_l0_event_count, 1)
        self.assertEqual(populated.evidence_chain_count, 1)

    def test_l0_surface_has_no_update_or_delete_api(self) -> None:
        names = set(dir(self.service))
        self.assertNotIn("update_event", names)
        self.assertNotIn("delete_event", names)
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(
                connection.execute("PRAGMA foreign_key_check").fetchall(),
                [],
            )


if __name__ == "__main__":
    unittest.main()

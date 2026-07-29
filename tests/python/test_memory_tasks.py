from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tasks import (  # noqa: E402
    TASK_ARTIFACT_KINDS,
    TASK_CONTRACT_VERSION,
    TaskOutcome,
    TaskOutcomeDraft,
    build_task_outcome_record,
)
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
    MemoryStatus,
)


PROJECT = "测试项目"
ASSET = "/Game/Characters/BP_Player.BP_Player"


class TaskMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_task_")
        self.database_path = Path(self.temporary.name) / "memory.sqlite3"
        self.service = ProjectMemoryService(
            database_path=self.database_path,
            project_key=PROJECT,
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def draft(self, **changes: object) -> TaskOutcomeDraft:
        values: dict[str, object] = {
            "task_key": "player-health-adjustment",
            "title": "Adjust player health",
            "conclusion": "Player health was updated, independently validated, and retained.",
            "outcome": TaskOutcome.SUCCEEDED,
            "patch_ref": "patch:plan_player_health",
            "backup_manifest_ref": "backup-manifest:apply_player_health",
            "validation_evidence_ref": "validation-evidence:player_health_passed",
            "revision_set": (MemoryRevision(ASSET, "sha256:" + "a" * 64),),
            "scopes": (MemoryScope(MemoryScopeType.ASSET, ASSET),),
            "patch_details": {"operationCount": 1},
            "backup_manifest_details": {"backupVerified": True},
            "validation_evidence_details": {"result": "passed"},
            "details": {"owner": "gameplay"},
        }
        values.update(changes)
        return TaskOutcomeDraft(**values)  # type: ignore[arg-type]

    def test_service_records_evidence_bound_task_outcome(self) -> None:
        record = self.service.record_task_outcome(self.draft())

        self.assertEqual(record.record_type, MemoryRecordType.TASK_RECORD)
        self.assertEqual(record.subject_key, "task:player-health-adjustment")
        self.assertEqual(record.source_kind, MemorySourceKind.TOOL_OBSERVED)
        self.assertEqual(record.source_ref, "validation-evidence:player_health_passed")
        self.assertEqual(record.status, MemoryStatus.VALID)
        self.assertEqual(record.body, "Player health was updated, independently validated, and retained.")
        self.assertEqual([scope.scope_type for scope in record.scopes], [MemoryScopeType.PROJECT, MemoryScopeType.ASSET])
        self.assertEqual(record.scopes[0].scope_key, PROJECT)
        self.assertEqual(
            [artifact.artifact_kind for artifact in record.artifacts],
            list(TASK_ARTIFACT_KINDS),
        )
        self.assertEqual(record.artifacts[0].details, {"operationCount": 1})
        self.assertEqual(record.artifacts[1].details, {"backupVerified": True})
        self.assertEqual(record.artifacts[2].details, {"result": "passed"})
        self.assertEqual(record.details["taskContractVersion"], TASK_CONTRACT_VERSION)
        self.assertEqual(record.details["taskOutcome"], "succeeded")
        self.assertEqual(record.details["owner"], "gameplay")
        self.assertRegex(record.evidence_sha256, r"^sha256:[0-9a-f]{64}$")

        loaded = self.service.get_record(record.record_id)
        self.assertEqual(loaded.evidence_sha256, record.evidence_sha256)
        self.assertEqual(loaded.revision_set[0].revision, "sha256:" + "a" * 64)

    def test_task_key_prefix_and_all_terminal_outcomes_are_supported(self) -> None:
        for outcome in TaskOutcome:
            with self.subTest(outcome=outcome):
                record_draft = build_task_outcome_record(
                    project_key=PROJECT,
                    draft=self.draft(
                        task_key=f"task:{outcome.value}",
                        outcome=outcome,
                    ),
                )
                self.assertEqual(record_draft.subject_key, f"task:{outcome.value}")
                self.assertEqual(record_draft.details["taskOutcome"], outcome.value)

    def test_task_evidence_changes_digest_without_changing_semantic_content(self) -> None:
        first = self.service.record_task_outcome(self.draft())
        second = self.service.record_task_outcome(
            replace(
                self.draft(),
                validation_evidence_ref="validation-evidence:player_health_second_pass",
            )
        )

        self.assertEqual(first.content_sha256, second.content_sha256)
        self.assertNotEqual(first.evidence_sha256, second.evidence_sha256)
        self.assertEqual(first.status, MemoryStatus.VALID)
        self.assertEqual(second.status, MemoryStatus.VALID)

    def test_task_outcome_requires_stable_revision_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one stable"):
            build_task_outcome_record(
                project_key=PROJECT,
                draft=self.draft(revision_set=()),
            )
        with self.assertRaisesRegex(ValueError, "must all be stable"):
            build_task_outcome_record(
                project_key=PROJECT,
                draft=self.draft(
                    revision_set=(MemoryRevision(ASSET, "sha256:a", False),),
                ),
            )

    def test_task_artifact_refs_reject_absolute_and_parent_paths(self) -> None:
        invalid = (
            ("patch_ref", "C:\\Temp\\patch.json"),
            ("backup_manifest_ref", "/tmp/manifest.json"),
            ("validation_evidence_ref", "Output/../Secrets/evidence.json"),
        )
        for field_name, value in invalid:
            with self.subTest(field=field_name):
                with self.assertRaisesRegex(ValueError, "absolute path|parent directories"):
                    build_task_outcome_record(
                        project_key=PROJECT,
                        draft=self.draft(**{field_name: value}),
                    )

    def test_task_scope_and_reserved_details_are_fixed(self) -> None:
        with self.assertRaisesRegex(ValueError, "fixed Project Key"):
            build_task_outcome_record(
                project_key=PROJECT,
                draft=self.draft(
                    scopes=(MemoryScope(MemoryScopeType.PROJECT, "OtherProject"),),
                ),
            )
        with self.assertRaisesRegex(ValueError, "reserved Task Outcome fields"):
            build_task_outcome_record(
                project_key=PROJECT,
                draft=self.draft(details={"taskOutcome": "forged"}),
            )

    def test_task_conclusion_allows_normalized_multiline_text(self) -> None:
        record_draft = build_task_outcome_record(
            project_key=PROJECT,
            draft=self.draft(conclusion="First result.\r\nSecond result."),
        )
        self.assertEqual(record_draft.body, "First result.\nSecond result.")

    def test_task_input_rejects_invalid_outcome_and_control_characters(self) -> None:
        with self.assertRaisesRegex(ValueError, "outcome must be one of"):
            build_task_outcome_record(
                project_key=PROJECT,
                draft=self.draft(outcome="unknown"),
            )
        with self.assertRaisesRegex(ValueError, "control or newline"):
            build_task_outcome_record(
                project_key=PROJECT,
                draft=self.draft(task_key="bad\ntask"),
            )


if __name__ == "__main__":
    unittest.main()

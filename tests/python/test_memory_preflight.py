from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
PYTHON_TESTS = ROOT / "tests" / "python"
for path in (SRC_ROOT, PYTHON_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ue_agent_kit import memory_injection, memory_preflight  # noqa: E402
from ue_agent_kit.database import open_database, set_metadata  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryScope,
    MemorySourceKind,
    open_project_memory_database,
)


PROJECT = "preflight-project"


class MemoryPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_preflight_")
        self.root = Path(self.temporary.name)
        self.memory_path = self.root / "memory.sqlite3"
        self.index_path = self.root / "index.sqlite3"
        self.service = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)
        self.service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="Preflight tests.",
            )
        )
        with open_database(self.index_path) as connection:
            set_metadata(connection, "project_key", PROJECT)
            connection.commit()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def add_rule(self, *, subject: str, title: str, body: str) -> str:
        record = self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectRule",
                subject_key=subject,
                title=title,
                body=body,
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        )
        return record.record_id

    def add_asset(self, asset_path: str, asset_class: str = "Blueprint") -> None:
        with open_database(self.index_path) as connection:
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path, package_name, asset_name, asset_class,
                    status, revision_value, schema_version, exporter_version,
                    profile, canonical_sha256, canonical_relpath, indexed_at_utc
                ) VALUES (?, ?, ?, ?, 0, '', 'test', 'test', 'logic',
                          'sha256:fixture', ?, '2026-10-28T00:00:00.000Z')
                """,
                (
                    asset_path,
                    asset_path.rsplit(".", 1)[0],
                    asset_path.rsplit(".", 1)[-1],
                    asset_class,
                    asset_path,
                ),
            )
            connection.commit()

    def preflight(
        self,
        *,
        enabled: bool = True,
        include_text: bool = True,
        query: str = "",
    ) -> dict:
        return self.service.preflight_context(
            index_database=self.index_path,
            enabled=enabled,
            include_text=include_text,
            query=query,
        ).to_payload()

    def test_fresh_preflight_is_bounded_and_idempotent(self) -> None:
        self.add_rule(subject="preflight-rule", title="Preflight rule", body="Always keep the rule.")
        first = self.preflight()
        self.assertEqual(first["state"], "fresh")
        self.assertTrue(first["available"])
        self.assertFalse(first["stale"])
        self.assertEqual(first["reason"], "preflight-fresh")
        self.assertIn("Preflight rule", first["text"])
        self.assertFalse(first["textOmitted"])
        self.assertLessEqual(first["contentChars"], 3_200)
        self.assertLessEqual(first["estimatedTokens"], 800)
        self.assertRegex(first["snapshotId"], r"^ctxsnap_[0-9a-f]{32}$")
        self.assertEqual(first["maintenance"]["action"], "rebuild")

        second = self.preflight()
        self.assertEqual(second["state"], "fresh")
        self.assertEqual(second["maintenance"]["action"], "none")
        self.assertEqual(second["snapshotId"], first["snapshotId"])

    def test_preflight_can_match_bounded_l2_for_goal_query(self) -> None:
        asset_path = "/Game/Combat/BP_Hero.BP_Hero"
        self.add_asset(asset_path)
        for index in range(3):
            self.service.add_record(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key=f"verified-write-{index}",
                    title=f"Verified write {index}",
                    body=f"Verified durable write {index}.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                    scopes=(MemoryScope("asset", asset_path),),
                    details={
                        "operation": "setVariableDefault",
                        "stableTargetKey": "variable-default",
                    },
                )
            )

        payload = self.preflight(query="setVariableDefault")
        self.assertEqual(payload["state"], "fresh")
        self.assertGreaterEqual(payload["l2Entries"], 1)
        self.assertIn("L2: Blueprint setVariableDefault: 3 verified writes", payload["text"])
        self.assertLessEqual(payload["estimatedTokens"], 800)
        self.assertLessEqual(payload["contentChars"], 3_200)

    def test_preflight_handoff_is_visible_to_a_new_session(self) -> None:
        self.add_rule(subject="session-rule", title="Session rule", body="A new session sees this.")
        preflight = self.preflight(include_text=False)
        self.assertTrue(preflight["available"])
        self.assertEqual(preflight["text"], "")
        self.assertTrue(preflight["textOmitted"])
        self.assertEqual(preflight["l3Entries"], 1)

        session_b = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)
        payload = session_b.get_injection_context(
            index_snapshot_id=preflight["indexSnapshotId"]
        )
        self.assertTrue(payload["available"])
        self.assertIn("Session rule", payload["text"])

    def test_disabled_preflight_does_not_read_or_write(self) -> None:
        self.add_rule(subject="disabled-rule", title="Disabled rule", body="Never read here.")
        payload = self.preflight(enabled=False)
        self.assertEqual(payload["state"], "disabled")
        self.assertFalse(payload["available"])
        self.assertEqual(payload["reason"], "maintenance-disabled")
        self.assertEqual(payload["text"], "")
        with open_project_memory_database(self.memory_path) as connection:
            state_count = connection.execute(
                "SELECT COUNT(*) FROM memory_context_state WHERE project_key = ?",
                (PROJECT,),
            ).fetchone()[0]
        self.assertEqual(state_count, 0)

    def test_missing_memory_or_index_is_unavailable_without_side_effects(self) -> None:
        missing_memory = self.root / "missing-memory.sqlite3"
        service = ProjectMemoryService(database_path=missing_memory, project_key=PROJECT)
        missing = service.preflight_context(
            index_database=self.index_path,
            enabled=True,
            include_text=True,
        ).to_payload()
        self.assertEqual(missing["state"], "unavailable")
        self.assertEqual(missing["reason"], "memory-database-missing")
        self.assertFalse(missing["available"])
        self.assertEqual(missing["text"], "")
        self.assertFalse(missing_memory.exists())

        missing_index = self.root / "missing-index.sqlite3"
        payload = self.service.preflight_context(
            index_database=missing_index,
            enabled=True,
            include_text=True,
        ).to_payload()
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["reason"], "index-database-missing")
        self.assertEqual(payload["text"], "")

    def test_preflight_failure_fails_closed_and_preserves_old_snapshot(self) -> None:
        self.add_rule(subject="old-rule", title="Old rule", body="Old rule remains.")
        first = self.preflight()
        self.assertEqual(first["state"], "fresh")
        self.add_rule(subject="new-rule", title="New rule", body="New rule must not leak as stale.")
        with open_project_memory_database(self.memory_path) as connection:
            before = [
                str(row[0])
                for row in connection.execute(
                    "SELECT body FROM memory_context_entries WHERE layer = 'L3'"
                ).fetchall()
            ]

        with mock.patch.object(
            memory_injection,
            "build_memory_context_snapshot",
            side_effect=RuntimeError("injected preflight maintenance failure"),
        ):
            failed = self.preflight()

        self.assertEqual(failed["state"], "stale")
        self.assertFalse(failed["available"])
        self.assertEqual(failed["reason"], "maintenance-failed")
        self.assertEqual(failed["errorCode"], "maintenance-build-failed")
        self.assertEqual(failed["text"], "")
        with open_project_memory_database(self.memory_path) as connection:
            after = [
                str(row[0])
                for row in connection.execute(
                    "SELECT body FROM memory_context_entries WHERE layer = 'L3'"
                ).fetchall()
            ]
        self.assertEqual(after, before)

        recovered = self.preflight()
        self.assertEqual(recovered["state"], "fresh")
        self.assertIn("New rule must not leak as stale.", recovered["text"])
        self.assertIn("Old rule remains.", recovered["text"])

    def test_read_path_stale_never_returns_text(self) -> None:
        self.add_rule(subject="read-stale", title="Read stale", body="Do not return this.")
        self.preflight()
        with mock.patch.object(
            memory_preflight,
            "get_injection_context",
            return_value={
                "available": False,
                "stale": True,
                "reason": "index-snapshot-mismatch",
                "text": "STALE_TEXT_MUST_NOT_LEAK",
            },
        ):
            payload = self.preflight()
        self.assertEqual(payload["state"], "stale")
        self.assertFalse(payload["available"])
        self.assertEqual(payload["text"], "")
        self.assertNotIn("STALE_TEXT_MUST_NOT_LEAK", payload["text"])

    def test_preflight_rejects_non_boolean_flags(self) -> None:
        with self.assertRaisesRegex(ValueError, "enabled"):
            self.service.preflight_context(index_database=self.index_path, enabled=1)
        with self.assertRaisesRegex(ValueError, "include_text"):
            self.service.preflight_context(
                index_database=self.index_path,
                enabled=True,
                include_text="yes",
            )


if __name__ == "__main__":
    unittest.main()

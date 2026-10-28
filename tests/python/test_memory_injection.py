from __future__ import annotations

import json
import os
import sqlite3
import subprocess
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

from ue_agent_kit.database import open_database, set_metadata  # noqa: E402
from ue_agent_kit.memory_injection import (  # noqa: E402
    L2_BODY_MAX_CHARS,
    L3_MAX_ESTIMATED_TOKENS,
    MAX_L2_GROUPS,
    snapshot_id_for,
)
from ue_agent_kit.memory_schema import CURRENT_MEMORY_SCHEMA_VERSION, MEMORY_MIGRATIONS  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryScope,
    MemorySourceKind,
    open_project_memory_database,
)


PROJECT = "injection-project"

_BLUEPRINT_PATHS = [
    ("/Game/Combat/BP_Hero.BP_Hero", "Blueprint", "BP_Hero"),
    ("/Game/Combat/BP_Villain.BP_Villain", "Blueprint", "BP_Villain"),
    ("/Game/Combat/BP_Turret.BP_Turret", "Blueprint", "BP_Turret"),
    ("/Game/Combat/BP_Armor.BP_Armor", "Blueprint", "BP_Armor"),
    ("/Game/Combat/BP_Chest.BP_Chest", "Blueprint", "BP_Chest"),
    ("/Game/Systems/BP_Gate.BP_Gate", "Blueprint", "BP_Gate"),
    ("/Game/Systems/DA_Config.DA_Config", "DataAsset", "DA_Config"),
]

ASSET_A = "/Game/Combat/BP_Hero.BP_Hero"
ASSET_B = "/Game/Combat/BP_Villain.BP_Villain"
ASSET_C = "/Game/Systems/DA_Config.DA_Config"


def _make_index(path: Path, project_key: str, assets: list[tuple[str, str, str]]) -> None:
    if path.exists():
        path.unlink()
    with open_database(path) as connection:
        set_metadata(connection, "project_key", project_key)
        set_metadata(connection, "last_indexed_at_utc", "2026-09-03T00:00:00.000Z")
        for asset_path, asset_class, asset_name in assets:
            connection.execute(
                """
                INSERT INTO assets(
                    asset_path, package_name, asset_name, asset_class,
                    status, revision_value, schema_version, exporter_version,
                    profile, canonical_sha256, canonical_relpath, indexed_at_utc
                ) VALUES (?, ?, ?, ?, 0, '', 'test', 'test', 'logic',
                          'sha256:fixture', ?, '2026-09-03T00:00:00.000Z')
                """,
                (asset_path, asset_path.rsplit(".", 1)[0], asset_name, asset_class, asset_path),
            )
        connection.commit()


class MemoryInjectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_injection_")
        self.root = Path(self.temporary.name)
        self.memory_path = self.root / "memory.sqlite3"
        self.index_path = self.root / "index.sqlite3"
        self._make_index(assets=_BLUEPRINT_PATHS)
        self.service = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)
        self.service.create_node(
            KnowledgeNodeDraft(
                project_key=PROJECT,
                path="/project",
                node_type="project",
                title=PROJECT,
                summary="Injection tests.",
            )
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _make_index(self, *, assets: list[tuple[str, str, str]]) -> None:
        _make_index(self.index_path, PROJECT, assets)

    def _rule(self, *, subject: str, body: str, source_kind: str = "user-confirmed") -> str:
        record = self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectRule",
                subject_key=subject,
                title=subject,
                body=body,
                source_kind=source_kind,
                details={"distillation": {"ruleId": "l1.policy-rejection.v1"}}
                if source_kind == "tool-observed"
                else {},
            )
        )
        return record.record_id

    def _verified_write(
        self,
        *,
        asset_path: str,
        operation: str,
        index: int,
        target: str = "",
    ) -> str:
        record = self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectFact",
                subject_key=f"verified-write:{operation}:{index}",
                title=f"Verified write {index}",
                body=f"Verified durable write {index} for {operation}.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
                scopes=(MemoryScope("asset", asset_path),),
                details={
                    "operation": operation,
                    "stableTargetKey": target or "variable-default",
                },
            )
        )
        return record.record_id

    def build(self) -> dict:
        return self.service.build_context(index_database=self.index_path).to_payload()

    def injection(self, *, query: str = "", asset_classes=(), index_snapshot_id: str | None = None) -> dict:
        if index_snapshot_id is None:
            index_snapshot_id = self.current_index_snapshot_id()
        return self.service.get_injection_context(
            query=query,
            asset_classes=list(asset_classes),
            index_snapshot_id=index_snapshot_id,
        )

    def current_index_snapshot_id(self) -> str:
        import hashlib

        with open_database(self.index_path, readonly=True, migrate=False, immutable=True) as connection:
            from ue_agent_kit.database import get_metadata, get_schema_version

            stat = self.index_path.stat()
            payload = {
                "size": stat.st_size,
                "modifiedNs": stat.st_mtime_ns,
                "schema": get_schema_version(connection),
                "projectKey": get_metadata(connection, "project_key", ""),
                "lastIndexedAtUtc": get_metadata(connection, "last_indexed_at_utc", ""),
            }
            canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def maintain(self, *, enabled: bool = True) -> dict:
        return self.service.maintain_context(
            index_database=self.index_path,
            enabled=enabled,
        ).to_payload()

    def context_state(self) -> dict | None:
        with open_project_memory_database(self.memory_path) as connection:
            row = connection.execute(
                "SELECT source_generation, built_generation, snapshot_id, index_snapshot_id "
                "FROM memory_context_state WHERE project_key = ?",
                (PROJECT,),
            ).fetchone()
        if row is None:
            return None
        return {
            "sourceGeneration": int(row[0]),
            "builtGeneration": int(row[1]),
            "snapshotId": str(row[2]),
            "indexSnapshotId": str(row[3]),
        }

    def context_entries(self) -> list[tuple[str, str, str]]:
        with open_project_memory_database(self.memory_path) as connection:
            return [
                (str(row[0]), str(row[1]), str(row[2]))
                for row in connection.execute(
                    "SELECT layer, context_key, source_bindings_json "
                    "FROM memory_context_entries WHERE project_key = ? "
                    "ORDER BY layer, ordinal",
                    (PROJECT,),
                ).fetchall()
            ]

    # ------------------------------------------------------------------ schema

    def test_fresh_database_reaches_v7_with_context_tables_and_triggers(self) -> None:
        with open_project_memory_database(self.memory_path) as connection:
            self.assertEqual(CURRENT_MEMORY_SCHEMA_VERSION, 7)
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertIn("memory_context_state", tables)
            self.assertIn("memory_context_entries", tables)
            triggers = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                ).fetchall()
            }
            self.assertIn("memory_records_ctx_gen_ai", triggers)
            self.assertIn("memory_records_ctx_gen_ad", triggers)
            self.assertIn("memory_records_ctx_gen_au", triggers)

    def test_v5_database_migrates_additively_to_v6_preserving_records(self) -> None:
        legacy = Path(self.root) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy)
        try:
            for migration in MEMORY_MIGRATIONS[:5]:
                connection.executescript(migration.sql)
                connection.execute(
                    "INSERT INTO memory_schema_migrations(version, description, applied_at_utc)"
                    " VALUES (?, ?, '2026-09-01T00:00:00Z')",
                    (migration.version, migration.description),
                )
                connection.execute(f"PRAGMA user_version = {migration.version}")
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, project_key, record_type, subject_key, title, body,
                    source_kind, source_ref, confidence, status, content_sha256,
                    created_at_utc, observed_at_utc, updated_at_utc, details_json
                ) VALUES (?, ?, 'projectFact', 'legacy:subject', 'Legacy title',
                          'Legacy body.', 'user-confirmed', '', 1.0, 'valid',
                          'sha256:legacy', '2026-09-01T00:00:00Z',
                          '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '{}')
                """,
                ("mem_" + "b" * 32, PROJECT),
            )
            connection.commit()
        finally:
            connection.close()
        service = ProjectMemoryService(database_path=legacy, project_key=PROJECT)
        with open_project_memory_database(legacy) as migrated:
            self.assertEqual(
                int(migrated.execute("PRAGMA user_version").fetchone()[0]),
                CURRENT_MEMORY_SCHEMA_VERSION,
            )
            self.assertEqual(
                int(migrated.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0]),
                1,
            )
        # v5 DB is migrated lazily on first writable open; build context works.
        payload = service.build_context(index_database=self.index_path).to_payload()
        self.assertEqual(payload["builtGeneration"], payload["sourceGeneration"])
        self.assertRegex(payload["snapshotId"], r"^ctxsnap_[0-9a-f]{32}$")

    # ------------------------------------------------------------- build + reuse

    def test_build_is_deterministic_and_reused_when_unchanged(self) -> None:
        self._rule(subject="utf8", body="Text files use UTF-8.")
        first = self.build()
        second = self.build()
        self.assertEqual(first["reused"], False)
        self.assertEqual(second["reused"], True)
        self.assertEqual(first["snapshotId"], second["snapshotId"])
        self.assertEqual(first["l3Entries"], second["l3Entries"])
        text_one = self.injection(query="anything")["text"]
        text_two = self.injection(query="anything")["text"]
        self.assertEqual(text_one, text_two)

    def test_snapshot_id_is_deterministic_for_same_inputs(self) -> None:
        sid = snapshot_id_for(
            project_key=PROJECT,
            source_generation=3,
            index_snapshot_id="sha256:abc",
            ordered_content_digests=(("L3", 0, "sha256:x"), ("L2", 1, "sha256:y")),
        )
        again = snapshot_id_for(
            project_key=PROJECT,
            source_generation=3,
            index_snapshot_id="sha256:abc",
            ordered_content_digests=(("L3", 0, "sha256:x"), ("L2", 1, "sha256:y")),
        )
        self.assertEqual(sid, again)
        changed = snapshot_id_for(
            project_key=PROJECT,
            source_generation=4,
            index_snapshot_id="sha256:abc",
            ordered_content_digests=(("L3", 0, "sha256:x"), ("L2", 1, "sha256:y")),
        )
        self.assertNotEqual(sid, changed)

    # --------------------------------------------------------- L3 / conventions

    def test_l3_rule_and_convention_injection_is_bounded_and_stable(self) -> None:
        self._rule(subject="format", body="Always use UTF-8 and CRLF.")
        self._rule(subject="damage", body="Base damage lives on the DataAsset.")
        first = self.build()
        # 2 user-confirmed rules + 1 Blueprint naming convention.
        self.assertEqual(first["l3Entries"], 3)
        payload = self.injection(query="any task")
        self.assertTrue(payload["available"])
        self.assertFalse(payload["stale"])
        self.assertIn("L3: Rule: format. Always use UTF-8 and CRLF.", payload["text"])
        self.assertLessEqual(payload["contentChars"], L3_MAX_ESTIMATED_TOKENS * 4)
        self.assertLessEqual(payload["estimatedTokens"], L3_MAX_ESTIMATED_TOKENS)
        # Byte-identical across identical requests.
        self.assertEqual(payload["injectionHash"], self.injection(query="any task")["injectionHash"])

    def test_l3_naming_convention_from_fixed_index(self) -> None:
        self._rule(subject="placeholder", body="One rule to keep L3 non-empty.")
        payload = self.build()
        self.assertGreaterEqual(payload["l3Entries"], 1)
        text = self.injection(query="unrelated")["text"]
        self.assertIn("Convention: Blueprint assets use BP_ prefix", text)

    def test_duplicate_identical_rule_text_is_deduped_not_fatal(self) -> None:
        record_ids: list[str] = []
        for subject in ("duplicate-rule-a", "duplicate-rule-b"):
            record = self.service.add_record(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectRule",
                    subject_key=subject,
                    title="Shared rule title",
                    body="Shared rule body.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                )
            )
            record_ids.append(record.record_id)

        # Two records with different Subject Keys can project byte-identical
        # L3 text. The build must merge them instead of violating the
        # memory_context_entries (projectKey, layer, contextKey) uniqueness.
        payload = self.build()
        self.assertEqual(payload["l3Entries"], 2)  # one deduped rule + one convention
        rebuilt = self.build()
        self.assertTrue(rebuilt["reused"])
        self.assertEqual(rebuilt["snapshotId"], payload["snapshotId"])

        injection = self.injection(query="anything")
        rule_line = "L3: Rule: Shared rule title. Shared rule body."
        self.assertEqual(injection["text"].count(rule_line), 1)

        with open_project_memory_database(self.memory_path) as connection:
            record_count = connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE record_type = 'projectRule'"
            ).fetchone()[0]
            entry = connection.execute(
                """
                SELECT source_bindings_json
                FROM memory_context_entries
                WHERE layer = 'L3' AND body = ?
                """,
                ("Rule: Shared rule title. Shared rule body.",),
            ).fetchone()
            self.assertEqual(record_count, 2)
            self.assertIsNotNone(entry)
            bindings = json.loads(str(entry[0]))
        self.assertEqual({str(binding["recordId"]) for binding in bindings}, set(record_ids))

    # --------------------------------------------------------------- L2 recipes

    def test_l2_recipe_requires_three_verified_successes(self) -> None:
        for index in range(3):
            self._verified_write(asset_path=ASSET_A, operation="setVariableDefault", index=index)
        payload = self.build()
        self.assertEqual(payload["l2Entries"], 1)
        injection = self.injection(query="set variable default", asset_classes=["Blueprint"])
        self.assertEqual(injection["l2Count"], 1)
        self.assertIn("L2: Blueprint setVariableDefault: 3 verified writes", injection["text"])

    def test_l2_below_threshold_forms_no_recipe(self) -> None:
        for index in range(2):
            self._verified_write(asset_path=ASSET_A, operation="setVariableDefault", index=index)
        payload = self.build()
        self.assertEqual(payload["l2Entries"], 0)

    def test_l2_unrelated_task_never_injects_recipe(self) -> None:
        for index in range(3):
            self._verified_write(asset_path=ASSET_A, operation="setVariableDefault", index=index)
        self.build()
        unrelated = self.injection(query="combat damage audit", asset_classes=["Blueprint"])
        self.assertEqual(unrelated["l2Count"], 0)
        self.assertNotIn("L2:", unrelated["text"])

    def test_l2_recipe_body_respects_200_char_bound(self) -> None:
        for index in range(3):
            self._verified_write(
                asset_path=ASSET_A,
                operation="setVariableDefault",
                index=index,
                target="variable-default-" + "x" * 300,
            )
        self.build()
        stored = None
        with open_project_memory_database(self.memory_path) as connection:
            stored = connection.execute(
                "SELECT body FROM memory_context_entries WHERE layer = 'L2'"
            ).fetchall()
        for row in stored:
            self.assertLessEqual(len(str(row[0])), L2_BODY_MAX_CHARS)

    def test_l2_snapshot_keeps_max_groups(self) -> None:
        for operation in ("opA", "opB", "opC", "opD", "opE", "opF", "opG", "opH", "opI"):
            for index in range(3):
                self._verified_write(
                    asset_path=ASSET_A,
                    operation=operation,
                    index=index + hash(operation) % 7,
                )
        payload = self.build()
        self.assertLessEqual(payload["l2Entries"], MAX_L2_GROUPS)

    # -------------------------------------------------- generation / staleness

    def test_record_change_invalidates_snapshot_via_generation(self) -> None:
        self._rule(subject="format", body="Always use UTF-8.")
        self.build()
        self.assertTrue(self.injection()["available"])
        # A source Memory record change must invalidate the old snapshot.
        self._rule(subject="second", body="Second rule after build.")
        stale = self.injection()
        self.assertFalse(stale["available"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["reason"], "context-snapshot-stale")
        self.assertEqual(stale["text"], "")
        self.assertEqual(stale["contentChars"], 0)

    def test_stale_request_never_rebuilds_synchronously(self) -> None:
        self._rule(subject="format", body="Always use UTF-8.")
        self.build()
        self._rule(subject="new", body="New rule.")
        with open_project_memory_database(self.memory_path) as connection:
            state_before = dict(
                connection.execute(
                    "SELECT source_generation, built_generation FROM memory_context_state "
                    "WHERE project_key = ?",
                    (PROJECT,),
                ).fetchone()
            )
        self.assertGreater(state_before["source_generation"], state_before["built_generation"])
        # Request path must not rebuild: state must be unchanged afterwards.
        self.assertFalse(self.injection()["available"])
        with open_project_memory_database(self.memory_path) as connection:
            state_after = dict(
                connection.execute(
                    "SELECT source_generation, built_generation FROM memory_context_state "
                    "WHERE project_key = ?",
                    (PROJECT,),
                ).fetchone()
            )
        self.assertEqual(state_after, state_before)

    def test_index_snapshot_mismatch_prevents_old_injection(self) -> None:
        self._rule(subject="format", body="Always use UTF-8.")
        payload = self.build()
        index_id = payload["indexSnapshotId"]
        injection = self.injection(index_snapshot_id=index_id)
        self.assertTrue(injection["available"])
        # New index metadata (different index snapshot id) must block injection.
        with open_database(self.index_path) as connection:
            set_metadata(connection, "last_indexed_at_utc", "2026-09-04T00:00:00.000Z")
            connection.commit()
        mismatched = self.injection(index_snapshot_id=self.current_index_snapshot_id())
        self.assertFalse(mismatched["available"])
        self.assertEqual(mismatched["reason"], "index-snapshot-mismatch")
        self.assertEqual(mismatched["text"], "")
        # Rebuild against the current index restores a valid snapshot.
        rebuilt = self.build()
        self.assertTrue(rebuilt["reused"] or rebuilt["snapshotId"] != index_id)
        restored = self.injection(index_snapshot_id=self.current_index_snapshot_id())
        self.assertTrue(restored["available"])

    def test_no_snapshot_is_missing_reason_with_empty_text(self) -> None:
        payload = self.injection()
        self.assertFalse(payload["available"])
        self.assertEqual(payload["reason"], "context-snapshot-missing")
        self.assertEqual(payload["text"], "")
        self.assertEqual(payload["contentChars"], 0)
        self.assertEqual(payload["estimatedTokens"], 0)

    def test_model_inferred_records_never_enter_l3(self) -> None:
        self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectRule",
                subject_key="inferred-rule",
                title="Model inferred rule",
                body="Must never be injected.",
                source_kind=MemorySourceKind.MODEL_INFERRED,
            )
        )
        payload = self.build()
        text = self.injection()["text"]
        self.assertNotIn("Model inferred rule", text)
        self.assertNotIn("Must never be injected", text)
        # Only the deterministic index naming convention is eligible.
        self.assertEqual(payload["l3Entries"], 1)

    # ------------------------------------------------------ explicit maintenance

    def test_maintenance_disabled_is_a_noop(self) -> None:
        payload = self.maintain(enabled=False)
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["state"], "disabled")
        self.assertEqual(payload["action"], "none")
        self.assertEqual(payload["reason"], "maintenance-disabled")
        self.assertFalse(payload["stale"])
        self.assertIsNone(self.context_state())

    def test_maintenance_requires_an_existing_memory_database(self) -> None:
        missing = self.root / "absent-memory.sqlite3"
        service = ProjectMemoryService(database_path=missing, project_key=PROJECT)
        payload = service.maintain_context(
            index_database=self.index_path,
            enabled=True,
        ).to_payload()
        self.assertEqual(payload["state"], "unavailable")
        self.assertEqual(payload["reason"], "memory-database-missing")
        self.assertEqual(payload["action"], "none")
        self.assertFalse(payload["stale"])
        self.assertFalse(missing.exists())

    def test_two_sessions_recover_only_after_explicit_maintenance(self) -> None:
        self._rule(subject="baseline", body="Baseline rule.")
        self.build()
        self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectRule",
                subject_key="session-a-rule",
                title="Session A rule",
                body="Discovered during session A.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        )

        session_b = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)
        stale = session_b.get_injection_context(
            index_snapshot_id=self.current_index_snapshot_id()
        )
        self.assertFalse(stale["available"])
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["reason"], "context-snapshot-stale")
        self.assertEqual(stale["text"], "")
        with open_project_memory_database(self.memory_path) as connection:
            preserved = [
                str(row[0])
                for row in connection.execute(
                    "SELECT body FROM memory_context_entries WHERE layer = 'L3'"
                ).fetchall()
            ]
        self.assertTrue(any("Baseline rule" in body for body in preserved))

        maintained = self.maintain()
        self.assertEqual(maintained["state"], "current")
        self.assertEqual(maintained["action"], "rebuild")
        self.assertEqual(maintained["reason"], "source-generation-changed")
        self.assertEqual(
            maintained["builtGeneration"],
            maintained["sourceGeneration"],
        )

        recovered = session_b.get_injection_context(
            index_snapshot_id=self.current_index_snapshot_id()
        )
        self.assertTrue(recovered["available"])
        self.assertFalse(recovered["stale"])
        self.assertIn("Session A rule", recovered["text"])

    def test_maintenance_coalesces_identical_duplicate_updates(self) -> None:
        record_ids: list[str] = []
        for subject in ("duplicate-maintenance-a", "duplicate-maintenance-b"):
            record = self.service.add_record(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectRule",
                    subject_key=subject,
                    title="Shared maintenance title",
                    body="Shared maintenance body.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                )
            )
            record_ids.append(record.record_id)

        first = self.maintain()
        self.assertEqual(first["state"], "current")
        self.assertEqual(first["action"], "rebuild")
        self.assertEqual(first["reason"], "snapshot-missing")
        self.assertEqual(first["l3Entries"], 2)  # one deduped rule + one convention
        self.assertTrue(first["rebuilt"])
        self.assertFalse(first["reused"])
        snapshot_id = first["snapshotId"]

        with open_project_memory_database(self.memory_path) as connection:
            entry = connection.execute(
                "SELECT source_bindings_json FROM memory_context_entries "
                "WHERE layer = 'L3' AND body = ?",
                ("Rule: Shared maintenance title. Shared maintenance body.",),
            ).fetchone()
            record_count = connection.execute(
                "SELECT COUNT(*) FROM memory_records WHERE record_type = 'projectRule'"
            ).fetchone()[0]
        self.assertIsNotNone(entry)
        bindings = json.loads(str(entry[0]))
        self.assertEqual({str(item["recordId"]) for item in bindings}, set(record_ids))
        self.assertEqual(record_count, 2)

        second = self.maintain()
        self.assertEqual(second["state"], "current")
        self.assertEqual(second["action"], "none")
        self.assertEqual(second["reason"], "snapshot-current")
        self.assertEqual(second["snapshotId"], snapshot_id)
        self.assertFalse(second["rebuilt"])
        self.assertFalse(second["reused"])

    def test_maintenance_rebuilds_when_index_snapshot_changes(self) -> None:
        self._rule(subject="index-rule", body="Index-bound rule.")
        self.build()
        self.assertTrue(self.injection()["available"])
        with open_database(self.index_path) as connection:
            set_metadata(connection, "last_indexed_at_utc", "2026-10-28T00:00:00.000Z")
            connection.commit()

        stale = self.injection()
        self.assertFalse(stale["available"])
        self.assertEqual(stale["reason"], "index-snapshot-mismatch")

        maintained = self.maintain()
        self.assertEqual(maintained["state"], "current")
        self.assertEqual(maintained["action"], "rebuild")
        self.assertEqual(maintained["reason"], "index-snapshot-changed")
        self.assertTrue(self.injection()["available"])

    def test_maintenance_failure_preserves_old_snapshot_and_reports_stale(self) -> None:
        self._rule(subject="old-rule", body="Old rule remains.")
        self.build()
        self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectRule",
                subject_key="new-rule",
                title="New rule",
                body="New rule must not be lost.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        )
        before_state = self.context_state()
        before_entries = self.context_entries()

        from ue_agent_kit import memory_injection as memory_injection_module

        original = memory_injection_module._entry_content_sha256
        calls: list[int] = []

        def fail_mid_build(*args, **kwargs):
            calls.append(1)
            if len(calls) >= 2:
                raise RuntimeError("injected mid-build failure")
            return original(*args, **kwargs)

        with mock.patch.object(
            memory_injection_module,
            "_entry_content_sha256",
            side_effect=fail_mid_build,
        ):
            failed = self.maintain()

        self.assertEqual(failed["state"], "stale")
        self.assertEqual(failed["action"], "refuse")
        self.assertEqual(failed["reason"], "maintenance-failed")
        self.assertEqual(failed["errorCode"], "maintenance-build-failed")
        self.assertTrue(failed["stale"])
        self.assertEqual(self.context_state(), before_state)
        self.assertEqual(self.context_entries(), before_entries)

        stale = self.injection()
        self.assertFalse(stale["available"])
        self.assertEqual(stale["reason"], "context-snapshot-stale")
        self.assertEqual(stale["text"], "")

        recovered = self.maintain()
        self.assertEqual(recovered["action"], "rebuild")
        self.assertIn("New rule must not be lost.", self.injection()["text"])
        self.assertIn("Old rule remains.", self.injection()["text"])

    def test_two_process_maintenance_recovers_new_rule(self) -> None:
        self._rule(subject="process-old", body="Process old rule.")
        self.build()
        self.service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT,
                record_type="projectRule",
                subject_key="process-new",
                title="Process new rule",
                body="Written before the child process runs.",
                source_kind=MemorySourceKind.USER_CONFIRMED,
            )
        )

        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from ue_agent_kit.memory_service import ProjectMemoryService\n"
            "service = ProjectMemoryService(database_path=Path(sys.argv[1]), project_key=sys.argv[2])\n"
            "result = service.maintain_context(index_database=Path(sys.argv[3]), enabled=True)\n"
            "print(json.dumps(result.to_payload(), ensure_ascii=False))\n"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(SRC_ROOT) + os.pathsep + environment.get("PYTHONPATH", "")
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, "-c", script, str(self.memory_path), PROJECT, str(self.index_path)],
            cwd=str(ROOT),
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["state"], "current")
        self.assertEqual(payload["action"], "rebuild")

        session_b = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)
        recovered = session_b.get_injection_context(
            index_snapshot_id=self.current_index_snapshot_id()
        )
        self.assertTrue(recovered["available"])
        self.assertIn("Process new rule", recovered["text"])

    def test_concurrent_maintenance_never_duplicates_entries(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        self._rule(subject="concurrent", body="Concurrent maintenance rule.")

        def run_once() -> dict:
            service = ProjectMemoryService(database_path=self.memory_path, project_key=PROJECT)
            return service.maintain_context(
                index_database=self.index_path,
                enabled=True,
            ).to_payload()

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _: run_once(), range(2)))

        for outcome in outcomes:
            self.assertEqual(outcome["state"], "current", outcome)
            self.assertIn(outcome["action"], {"rebuild", "reuse", "none"})
        state = self.context_state()
        self.assertIsNotNone(state)
        self.assertEqual(state["builtGeneration"], state["sourceGeneration"])
        with open_project_memory_database(self.memory_path) as connection:
            duplicates = connection.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT layer, context_key FROM memory_context_entries "
                "GROUP BY layer, context_key HAVING COUNT(*) > 1)"
            ).fetchone()[0]
        self.assertEqual(duplicates, 0)
        self.assertTrue(self.injection()["available"])
        self.assertIn("Concurrent maintenance rule.", self.injection()["text"])

    # ---------------------------------------------------------- direct helpers

    def test_entry_ids_and_snapshot_ids_match_contract(self) -> None:
        from ue_agent_kit.memory_injection import entry_id_for

        entry = entry_id_for(PROJECT, "L3", "ctxkey_x")
        self.assertRegex(entry, r"^ctx_[0-9a-f]{32}$")
        snap = snapshot_id_for(
            project_key=PROJECT,
            source_generation=0,
            index_snapshot_id="sha256:idx",
            ordered_content_digests=[],
        )
        self.assertRegex(snap, r"^ctxsnap_[0-9a-f]{32}$")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

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

    # ------------------------------------------------------------------ schema

    def test_fresh_database_reaches_v6_with_context_tables_and_triggers(self) -> None:
        with open_project_memory_database(self.memory_path) as connection:
            self.assertEqual(CURRENT_MEMORY_SCHEMA_VERSION, 6)
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
            self.assertEqual(int(migrated.execute("PRAGMA user_version").fetchone()[0]), 6)
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

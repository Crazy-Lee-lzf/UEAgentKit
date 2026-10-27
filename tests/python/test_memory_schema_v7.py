from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import ue_agent_kit.project_memory as project_memory  # noqa: E402
from ue_agent_kit.database import get_schema_version, utc_now_iso  # noqa: E402
from ue_agent_kit.memory_schema import (  # noqa: E402
    CURRENT_MEMORY_SCHEMA_VERSION,
    MEMORY_MIGRATIONS,
)
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryArtifact,
    MemoryRecordDraft,
    MemoryRecordType,
    MemoryRevision,
    MemoryScope,
    MemorySourceKind,
    MemoryStatus,
    _content_sha256,
    _evidence_sha256,
    create_memory_record,
    get_memory_record,
    open_project_memory_database,
    search_memory_records,
)
from ue_agent_kit.text_search import build_search_text  # noqa: E402

PROJECT = "测试项目"
ASSET = "/Game/Test/BP_Armor.BP_Armor"
NODE_ID = "node_memory_cjk_v7"
EMBEDDING_BYTES = b"\x00\x01\x02\x03\x04\x05\x06\x07"
CONTEXT_GENERATION = 42

RECORD_IDS = {
    "han": "mem_" + "1" * 32,
    "japanese": "mem_" + "2" * 32,
    "korean": "mem_" + "3" * 32,
    "english_inventory": "mem_" + "4" * 32,
    "english_weapon": "mem_" + "5" * 32,
    "superseded": "mem_" + "6" * 32,
}


def _quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_blueprint() -> dict[str, dict[str, object]]:
    scope = MemoryScope("asset", ASSET, {"assetClass": "Blueprint"})
    revision = MemoryRevision(ASSET, "sha256:v6-revision")
    artifact = MemoryArtifact("validationEvidence", "Output/v6-validation.json", {"result": "passed"})
    return {
        "han": {
            "record_type": MemoryRecordType.PROJECT_FACT,
            "subject_key": "design.armor.upgrade-chain",
            "title": "护甲升级链",
            "body": "AK47护甲升级后获得额外减伤，升级链共三级。",
            "source_kind": MemorySourceKind.USER_CONFIRMED,
            "source_ref": "design/armor.md#upgrade-chain",
            "confidence": 1.0,
            "status": "valid",
            "scopes": (scope,),
            "revisions": (revision,),
            "artifacts": (artifact,),
            "details": {"owner": "design", "assetPath": ASSET},
            "node_id": NODE_ID,
            "superseded_by": None,
        },
        "japanese": {
            "record_type": MemoryRecordType.PROJECT_FACT,
            "subject_key": "design.weapon.japanese",
            "title": "武器強化計画",
            "body": "鋼の武器を強化する計画は第2段階に入った。",
            "source_kind": MemorySourceKind.USER_CONFIRMED,
            "source_ref": "design/weapon-ja.md",
            "confidence": 0.95,
            "status": "valid",
            "scopes": (),
            "revisions": (),
            "artifacts": (),
            "details": {},
            "node_id": None,
            "superseded_by": None,
        },
        "korean": {
            "record_type": MemoryRecordType.PROJECT_RULE,
            "subject_key": "design.armor.korean",
            "title": "방어구 강화",
            "body": "방어구를 강화하려면 재료가 필요하다.",
            "source_kind": MemorySourceKind.USER_CONFIRMED,
            "source_ref": "design/armor-ko.md",
            "confidence": 0.9,
            "status": "valid",
            "scopes": (),
            "revisions": (),
            "artifacts": (),
            "details": {},
            "node_id": None,
            "superseded_by": None,
        },
        "english_inventory": {
            "record_type": MemoryRecordType.PROJECT_RULE,
            "subject_key": "design.inventory.slot-color",
            "title": "Inventory slot color rule",
            "body": "Inventory slot color comes from the rarity table; AK47 damage is unrelated.",
            "source_kind": MemorySourceKind.USER_CONFIRMED,
            "source_ref": "design/inventory.md",
            "confidence": 1.0,
            "status": "valid",
            "scopes": (),
            "revisions": (),
            "artifacts": (),
            "details": {"note": "配方备注"},
            "node_id": None,
            "superseded_by": None,
        },
        "english_weapon": {
            "record_type": MemoryRecordType.DECISION_RECORD,
            "subject_key": "design.weapon.socket",
            "title": "Weapon socket binding",
            "body": "The weapon socket fix also updates the inventory module reference.",
            "source_kind": MemorySourceKind.MODEL_INFERRED,
            "source_ref": "design/weapon.md",
            "confidence": 0.8,
            "status": "unverified",
            "scopes": (),
            "revisions": (),
            "artifacts": (),
            "details": {},
            "node_id": None,
            "superseded_by": None,
        },
        "superseded": {
            "record_type": MemoryRecordType.PROJECT_FACT,
            "subject_key": "design.armor.old",
            "title": "Deprecated armor note",
            "body": "Old armor note replaced by the upgrade chain.",
            "source_kind": MemorySourceKind.TOOL_OBSERVED,
            "source_ref": "legacy/armor-old",
            "confidence": 1.0,
            "status": "superseded",
            "scopes": (),
            "revisions": (),
            "artifacts": (),
            "details": {},
            "node_id": None,
            "superseded_by": RECORD_IDS["han"],
        },
    }


def create_v6_fixture(path: Path) -> dict[str, object]:
    """Create a real on-disk schema-v6 database with six legacy records."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA foreign_keys = ON")
        for migration in MEMORY_MIGRATIONS[:6]:
            script = (
                "BEGIN IMMEDIATE;\n"
                + migration.sql
                + "\nINSERT INTO memory_schema_migrations(version, description, applied_at_utc) "
                + f"VALUES ({migration.version}, {_quote(migration.description)}, {_quote(utc_now_iso())});\n"
                + f"PRAGMA user_version = {migration.version};\n"
                + "COMMIT;\n"
            )
            connection.executescript(script)
        connection.execute(
            """
            INSERT INTO knowledge_nodes(
                node_id, project_key, path, parent_node_id, node_type, title, summary,
                created_at_utc, updated_at_utc, details_json
            ) VALUES (?, ?, '/project', NULL, 'project', ?, '', ?, ?, '{}')
            """,
            (
                NODE_ID,
                PROJECT,
                "项目根节点",
                "2026-09-01T00:00:00Z",
                "2026-09-01T00:00:00Z",
            ),
        )
        blueprints = _record_blueprint()
        for index, key in enumerate(("han", "japanese", "korean", "english_inventory", "english_weapon", "superseded")):
            item = blueprints[key]
            content = _content_sha256(
                record_type=item["record_type"],
                subject_key=item["subject_key"],
                title=item["title"],
                body=item["body"],
                scopes=item["scopes"],
                details=item["details"],
            )
            evidence = _evidence_sha256(
                project_key=PROJECT,
                content_sha256=content,
                source_kind=item["source_kind"],
                source_ref=item["source_ref"],
                confidence=item["confidence"],
                revisions=item["revisions"],
                artifacts=item["artifacts"],
            )
            timestamp = f"2026-09-01T00:00:0{index}Z"
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, project_key, record_type, subject_key, title, body,
                    source_kind, source_ref, confidence, status, content_sha256,
                    evidence_sha256, created_at_utc, observed_at_utc, updated_at_utc,
                    superseded_by_record_id, details_json, node_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    RECORD_IDS[key],
                    PROJECT,
                    item["record_type"].value,
                    item["subject_key"],
                    item["title"],
                    item["body"],
                    item["source_kind"].value,
                    item["source_ref"],
                    item["confidence"],
                    item["status"],
                    content,
                    evidence,
                    timestamp,
                    timestamp,
                    timestamp,
                    item["superseded_by"],
                    _canonical_json(item["details"]),
                    item["node_id"],
                ),
            )
            for ordinal, scope in enumerate(item["scopes"]):
                connection.execute(
                    "INSERT INTO memory_scopes(record_id, ordinal, scope_type, scope_key, details_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        RECORD_IDS[key],
                        ordinal,
                        scope.scope_type,
                        scope.scope_key,
                        _canonical_json(scope.details),
                    ),
                )
            for ordinal, revision in enumerate(item["revisions"]):
                connection.execute(
                    "INSERT INTO memory_revisions(record_id, ordinal, asset_path, revision, revision_stable) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (RECORD_IDS[key], ordinal, revision.asset_path, revision.revision),
                )
            for ordinal, artifact in enumerate(item["artifacts"]):
                connection.execute(
                    "INSERT INTO memory_artifacts(record_id, ordinal, artifact_kind, artifact_ref, details_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        RECORD_IDS[key],
                        ordinal,
                        artifact.artifact_kind,
                        artifact.artifact_ref,
                        _canonical_json(artifact.details),
                    ),
                )
        connection.execute(
            "INSERT INTO memory_embeddings(record_id, model_id, dim, content_sha256, embedding, "
            "created_at_utc, updated_at_utc) VALUES (?, 'stub-model', 8, ?, ?, ?, ?)",
            (
                RECORD_IDS["han"],
                _content_sha256(
                    record_type=MemoryRecordType.PROJECT_FACT,
                    subject_key="design.armor.upgrade-chain",
                    title="护甲升级链",
                    body="AK47护甲升级后获得额外减伤，升级链共三级。",
                    scopes=(MemoryScope("asset", ASSET, {"assetClass": "Blueprint"}),),
                    details={"owner": "design", "assetPath": ASSET},
                ),
                sqlite3.Binary(EMBEDDING_BYTES),
                "2026-09-01T00:00:00Z",
                "2026-09-01T00:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO memory_status_events(record_id, from_status, to_status, reason, "
            "changed_at_utc, details_json) VALUES (?, 'valid', 'superseded', 'legacy-supersede', ?, '{}')",
            (RECORD_IDS["superseded"], "2026-09-01T00:00:06Z"),
        )
        connection.execute(
            """
            INSERT INTO memory_context_state(
                project_key, source_generation, built_generation, snapshot_id,
                index_snapshot_id, source_digest, built_at_utc
            ) VALUES (?, ?, ?, 'ctxsnap_legacy', 'indexsnap_legacy', 'sha256:legacy', ?)
            """,
            (PROJECT, CONTEXT_GENERATION, CONTEXT_GENERATION, "2026-09-01T00:00:00Z"),
        )
        connection.execute(
            """
            INSERT INTO memory_context_entries(
                entry_id, project_key, layer, context_key, ordinal, title, body,
                match_json, source_bindings_json, content_sha256
            ) VALUES ('entry_legacy', ?, 'L2', 'legacy-key', 0, '旧上下文', '旧上下文正文', '{}', '[]', ?)
            """,
            (PROJECT, "sha256:" + "a" * 64),
        )
        connection.commit()
    finally:
        connection.close()
    return blueprints


class _V6Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_v7_")
        self.root = Path(self._temporary.name)
        self.database_path = self.root / "memory" / "project-memory.sqlite3"
        self.blueprints = create_v6_fixture(self.database_path)
        self.backup_path = self.root / "backups" / "project-memory-v6.sqlite3"
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.database_path, self.backup_path)

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _ids(self, connection: sqlite3.Connection, query: str, **kwargs) -> list[str]:
        hits = search_memory_records(
            connection,
            project_key=PROJECT,
            query=query,
            limit=20,
            **kwargs,
        )
        return [hit.record.record_id for hit in hits]

    def _raw_fts_ids(self, connection: sqlite3.Connection, match: str) -> list[str]:
        rows = connection.execute(
            "SELECT r.record_id FROM memory_records_fts "
            "JOIN memory_records AS r ON r.rowid = memory_records_fts.rowid "
            "WHERE memory_records_fts MATCH ? ORDER BY r.record_id",
            (match,),
        ).fetchall()
        return [str(row[0]) for row in rows]


class V6ToV7MigrationTests(_V6Fixture):
    def test_migration_preserves_six_legacy_records_and_linked_data(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(get_schema_version(connection), CURRENT_MEMORY_SCHEMA_VERSION)
            self.assertEqual(CURRENT_MEMORY_SCHEMA_VERSION, 7)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0],
                6,
            )
            self.assertEqual(
                {str(row[0]) for row in connection.execute("SELECT record_id FROM memory_records")},
                set(RECORD_IDS.values()),
            )
            han = get_memory_record(connection, RECORD_IDS["han"])
            self.assertEqual(han.title, "护甲升级链")
            self.assertEqual(han.status, MemoryStatus.VALID)
            self.assertEqual(han.node_id, NODE_ID)
            self.assertEqual(len(han.scopes), 1)
            self.assertEqual(han.scopes[0].scope_key, ASSET)
            self.assertEqual(han.revision_set[0].revision, "sha256:v6-revision")
            self.assertEqual(han.artifacts[0].artifact_ref, "Output/v6-validation.json")
            self.assertEqual(han.details["owner"], "design")

            superseded = get_memory_record(connection, RECORD_IDS["superseded"])
            self.assertEqual(superseded.status, MemoryStatus.SUPERSEDED)
            self.assertEqual(superseded.superseded_by_record_id, RECORD_IDS["han"])

            embedding = connection.execute(
                "SELECT model_id, dim, embedding FROM memory_embeddings WHERE record_id = ?",
                (RECORD_IDS["han"],),
            ).fetchone()
            self.assertEqual(str(embedding[0]), "stub-model")
            self.assertEqual(int(embedding[1]), 8)
            self.assertEqual(bytes(embedding[2]), EMBEDDING_BYTES)

            state = connection.execute(
                "SELECT source_generation, built_generation, snapshot_id FROM memory_context_state "
                "WHERE project_key = ?",
                (PROJECT,),
            ).fetchone()
            self.assertEqual(int(state[0]), CONTEXT_GENERATION)
            self.assertEqual(int(state[1]), CONTEXT_GENERATION)
            self.assertEqual(str(state[2]), "ctxsnap_legacy")
            entry = connection.execute(
                "SELECT title, body FROM memory_context_entries WHERE entry_id = 'entry_legacy'"
            ).fetchone()
            self.assertEqual((str(entry[0]), str(entry[1])), ("旧上下文", "旧上下文正文"))

            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(
                [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()],
                ["ok"],
            )

    def test_migration_populates_search_text_for_every_row(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            rows = connection.execute(
                "SELECT record_id, search_text FROM memory_records ORDER BY record_id"
            ).fetchall()
            self.assertEqual(len(rows), 6)
            for row in rows:
                text = str(row[1])
                self.assertTrue(text, row[0])
                self.assertEqual(text, text.strip())
                self.assertNotIn("  ", text)
            han_text = dict((str(row[0]), str(row[1])) for row in rows)[RECORD_IDS["han"]]
            self.assertIn("护 甲 升 级 链", han_text)
            self.assertIn("AK47 护 甲", han_text)

    def test_han_kana_hangul_mixed_and_reverse_queries(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(self._ids(connection, "\u62a4\u7532"), [RECORD_IDS["han"]])
            self.assertEqual(self._ids(connection, "\u7532\u62a4"), [])
            self.assertEqual(self._ids(connection, "AK47\u62a4\u7532"), [RECORD_IDS["han"]])
            self.assertEqual(self._ids(connection, "\uff21\uff2b\uff14\uff17\u62a4\u7532"), [RECORD_IDS["han"]])
            self.assertEqual(self._ids(connection, "\u5f37\u5316"), [RECORD_IDS["japanese"]])
            self.assertEqual(self._ids(connection, "\u5316\u5f37"), [])
            self.assertEqual(self._ids(connection, "\ubc29\uc5b4\uad6c"), [RECORD_IDS["korean"]])
            self.assertEqual(self._ids(connection, "\uad6c\ubc29"), [])
            # CJK present only in details_json still reaches the index.
            self.assertEqual(self._ids(connection, "\u914d\u65b9"), [RECORD_IDS["english_inventory"]])

    def test_ascii_ranking_order_is_unchanged_from_v6(self) -> None:
        legacy = sqlite3.connect(self.backup_path)
        try:
            legacy_rows = [
                (str(row[0]), float(row[1]))
                for row in legacy.execute(
                    "SELECT r.record_id, bm25(memory_records_fts) FROM memory_records_fts "
                    "JOIN memory_records AS r ON r.rowid = memory_records_fts.rowid "
                    "WHERE memory_records_fts MATCH ? AND r.project_key = ? "
                    "ORDER BY 2, r.record_id",
                    ('"inventory"', PROJECT),
                ).fetchall()
            ]
        finally:
            legacy.close()
        self.assertGreaterEqual(len(legacy_rows), 2)
        with open_project_memory_database(self.database_path) as connection:
            current_rows = [
                (str(row[0]), float(row[1]))
                for row in connection.execute(
                    "SELECT r.record_id, bm25(memory_records_fts, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0) "
                    "FROM memory_records_fts "
                    "JOIN memory_records AS r ON r.rowid = memory_records_fts.rowid "
                    "WHERE memory_records_fts MATCH ? AND r.project_key = ? "
                    "ORDER BY 2, r.record_id",
                    ('"inventory"', PROJECT),
                ).fetchall()
            ]
            # Adding a sixth FTS column changes BM25 corpus statistics slightly,
            # so the absolute scores are not frozen; the ranked order is the
            # public behavior and must stay identical.
            self.assertEqual(
                [record_id for record_id, _ in current_rows],
                [record_id for record_id, _ in legacy_rows],
            )
            for _, score in current_rows:
                self.assertLess(score, 0.0)
            self.assertEqual(
                self._ids(connection, "inventory"),
                [record_id for record_id, _ in legacy_rows],
            )

    def test_forced_second_rebuild_keeps_cjk_and_integrity(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            for _ in range(2):
                connection.execute(
                    "INSERT INTO memory_records_fts(memory_records_fts) VALUES('rebuild')"
                )
                connection.commit()
                self.assertEqual(
                    [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()],
                    ["ok"],
                )
                self.assertEqual(self._ids(connection, "\u62a4\u7532"), [RECORD_IDS["han"]])
                self.assertEqual(
                    self._raw_fts_ids(connection, '"\u62a4 \u7532"'),
                    [RECORD_IDS["han"]],
                )


class V7TriggerAndReopenTests(_V6Fixture):
    def test_insert_update_delete_triggers_keep_external_content_consistent(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            created = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="design.armor.enchant",
                    title="护甲附魔规则",
                    body="附魔后护甲获得额外属性。",
                    source_kind="user-confirmed",
                    source_ref="design/enchant.md",
                ),
            )
            self.assertEqual(self._ids(connection, "\u9644\u9b54"), [created.record_id])

            # Status-only UPDATE must delete the exact old FTS payload and
            # reinsert it; content digests stay valid because status is not
            # covered by them.
            connection.execute(
                "UPDATE memory_records SET status = 'stale' WHERE record_id = ?",
                (created.record_id,),
            )
            connection.commit()
            self.assertEqual(self._ids(connection, "\u9644\u9b54"), [])
            self.assertEqual(
                self._ids(connection, "\u9644\u9b54", statuses=(MemoryStatus.STALE,)),
                [created.record_id],
            )
            self.assertEqual(
                [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()],
                ["ok"],
            )

            new_title = "重铸护甲词条"
            new_body = "重铸后的护甲描述包含新词条。"
            new_search_text = build_search_text(
                subject_key="design.armor.enchant",
                title=new_title,
                body=new_body,
                source_ref="design/enchant.md",
                details_json="{}",
            )
            new_content = _content_sha256(
                record_type=MemoryRecordType.PROJECT_FACT,
                subject_key="design.armor.enchant",
                title=new_title,
                body=new_body,
                scopes=(),
                details={},
            )
            new_evidence = _evidence_sha256(
                project_key=PROJECT,
                content_sha256=new_content,
                source_kind=MemorySourceKind.USER_CONFIRMED,
                source_ref="design/enchant.md",
                confidence=1.0,
                revisions=(),
                artifacts=(),
            )
            connection.execute(
                "UPDATE memory_records SET title = ?, body = ?, content_sha256 = ?, "
                "evidence_sha256 = ?, search_text = ?, status = 'valid', updated_at_utc = ? "
                "WHERE record_id = ?",
                (
                    new_title,
                    new_body,
                    new_content,
                    new_evidence,
                    new_search_text,
                    "2026-09-02T00:00:00Z",
                    created.record_id,
                ),
            )
            connection.commit()
            self.assertEqual(self._ids(connection, "\u91cd\u94f8"), [created.record_id])
            self.assertEqual(self._ids(connection, "\u9644\u9b54"), [])
            self.assertEqual(get_memory_record(connection, created.record_id).title, new_title)

            connection.execute(
                "DELETE FROM memory_records WHERE record_id = ?",
                (created.record_id,),
            )
            connection.commit()
            self.assertEqual(self._ids(connection, "\u91cd\u94f8"), [])
            self.assertEqual(self._raw_fts_ids(connection, '"\u91cd \u94f8"'), [])
            self.assertEqual(
                [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()],
                ["ok"],
            )

    def test_v7_reopen_is_idempotent_and_readonly_immutable_works(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            first = connection.execute(
                "SELECT record_id, search_text FROM memory_records ORDER BY record_id"
            ).fetchall()
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM memory_schema_migrations").fetchone()[0],
                7,
            )
        with open_project_memory_database(self.database_path) as reopened:
            second = reopened.execute(
                "SELECT record_id, search_text FROM memory_records ORDER BY record_id"
            ).fetchall()
            self.assertEqual([tuple(row) for row in second], [tuple(row) for row in first])
            self.assertEqual(
                reopened.execute("SELECT COUNT(*) FROM memory_schema_migrations").fetchone()[0],
                7,
            )
            self.assertEqual(reopened.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        with open_project_memory_database(
            self.database_path, readonly=True, immutable=True
        ) as frozen:
            self.assertEqual(get_schema_version(frozen), 7)
            self.assertEqual(
                self._ids(frozen, "\u62a4\u7532"),
                [RECORD_IDS["han"]],
            )

    def test_v6_backup_file_remains_intact(self) -> None:
        before = hashlib.sha256(self.backup_path.read_bytes()).hexdigest()
        with open_project_memory_database(self.database_path):
            pass
        after = hashlib.sha256(self.backup_path.read_bytes()).hexdigest()
        self.assertEqual(after, before)
        legacy = sqlite3.connect(self.backup_path)
        try:
            self.assertEqual(int(legacy.execute("PRAGMA user_version").fetchone()[0]), 6)
            record_columns = {str(row[1]) for row in legacy.execute("PRAGMA table_info(memory_records)")}
            self.assertNotIn("search_text", record_columns)
            fts_columns = {str(row[1]) for row in legacy.execute("PRAGMA table_info(memory_records_fts)")}
            self.assertEqual(
                fts_columns,
                {"subject_key", "title", "body", "source_ref", "details_json"},
            )
            self.assertEqual(legacy.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0], 6)
        finally:
            legacy.close()


class FailClosedTests(_V6Fixture):
    def test_newer_schema_is_rejected_without_writes(self) -> None:
        with open_project_memory_database(self.database_path):
            pass
        raw = sqlite3.connect(self.database_path)
        try:
            raw.execute("PRAGMA user_version = 8")
            raw.commit()
        finally:
            raw.close()
        with self.assertRaisesRegex(RuntimeError, "newer than supported"):
            with open_project_memory_database(self.database_path):
                pass
        with self.assertRaisesRegex(RuntimeError, "does not match supported"):
            with open_project_memory_database(self.database_path, readonly=True):
                pass
        raw = sqlite3.connect(self.database_path)
        try:
            self.assertEqual(int(raw.execute("PRAGMA user_version").fetchone()[0]), 8)
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0], 6)
        finally:
            raw.close()

    def test_partial_v6_schema_is_rejected_not_repaired(self) -> None:
        partial = self.root / "partial-v6.sqlite3"
        shutil.copy2(self.backup_path, partial)
        raw = sqlite3.connect(partial)
        try:
            raw.execute("DROP TRIGGER memory_records_ai")
            raw.commit()
        finally:
            raw.close()
        with self.assertRaisesRegex(RuntimeError, "schema 6 is incomplete"):
            with open_project_memory_database(partial):
                pass
        raw = sqlite3.connect(partial)
        try:
            self.assertEqual(int(raw.execute("PRAGMA user_version").fetchone()[0]), 6)
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0], 6)
            self.assertTrue(
                raw.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'memory_records_fts'"
                ).fetchone()
            )
        finally:
            raw.close()

    def test_partial_v6_missing_table_is_rejected(self) -> None:
        partial = self.root / "partial-table-v6.sqlite3"
        shutil.copy2(self.backup_path, partial)
        raw = sqlite3.connect(partial)
        try:
            raw.execute("DROP TABLE memory_context_entries")
            raw.commit()
        finally:
            raw.close()
        with self.assertRaisesRegex(RuntimeError, "memory_context_entries"):
            with open_project_memory_database(partial):
                pass

    def test_unversioned_non_empty_database_is_rejected(self) -> None:
        unversioned = self.root / "unversioned.sqlite3"
        raw = sqlite3.connect(unversioned)
        try:
            raw.execute("CREATE TABLE memory_records(record_id TEXT PRIMARY KEY)")
            raw.commit()
        finally:
            raw.close()
        with self.assertRaisesRegex(RuntimeError, "no schema version"):
            with open_project_memory_database(unversioned):
                pass

    def test_interrupted_migration_rolls_back_to_v6_and_can_retry(self) -> None:
        interrupted = self.root / "interrupted.sqlite3"
        shutil.copy2(self.backup_path, interrupted)

        def _failing_hook(connection: sqlite3.Connection) -> None:
            raise RuntimeError("injected migration failure")

        patched = tuple(
            replace(migration, apply=_failing_hook) if migration.version == 7 else migration
            for migration in project_memory.MEMORY_MIGRATIONS
        )
        with mock.patch.object(project_memory, "MEMORY_MIGRATIONS", patched):
            with self.assertRaisesRegex(RuntimeError, "injected migration failure"):
                with open_project_memory_database(interrupted):
                    pass

        raw = sqlite3.connect(interrupted)
        try:
            self.assertEqual(int(raw.execute("PRAGMA user_version").fetchone()[0]), 6)
            record_columns = {str(row[1]) for row in raw.execute("PRAGMA table_info(memory_records)")}
            self.assertNotIn("search_text", record_columns)
            fts_columns = {str(row[1]) for row in raw.execute("PRAGMA table_info(memory_records_fts)")}
            self.assertEqual(
                fts_columns,
                {"subject_key", "title", "body", "source_ref", "details_json"},
            )
            self.assertEqual(raw.execute("SELECT COUNT(*) FROM memory_records").fetchone()[0], 6)
            triggers = {
                str(row[0])
                for row in raw.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            }
            self.assertTrue({"memory_records_ai", "memory_records_ad", "memory_records_au"} <= triggers)
        finally:
            raw.close()

        with open_project_memory_database(interrupted) as connection:
            self.assertEqual(get_schema_version(connection), 7)
            self.assertEqual(self._ids(connection, "\u62a4\u7532"), [RECORD_IDS["han"]])


class FreshSchemaV7Tests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_v7_fresh_")
        self.root = Path(self._temporary.name)
        self.database_path = self.root / "memory" / "project-memory.sqlite3"

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_fresh_database_is_v7_with_search_text_and_external_content_fts(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(get_schema_version(connection), 7)
            record_columns = [str(row[1]) for row in connection.execute("PRAGMA table_info(memory_records)")]
            self.assertIn("search_text", record_columns)
            self.assertEqual(
                [str(row[1]) for row in connection.execute("PRAGMA table_info(memory_records_fts)")],
                ["subject_key", "title", "body", "source_ref", "details_json", "search_text"],
            )
            triggers = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
                    "AND name IN ('memory_records_ai', 'memory_records_ad', 'memory_records_au')"
                )
            }
            self.assertEqual(set(triggers), {"memory_records_ai", "memory_records_ad", "memory_records_au"})
            for sql in triggers.values():
                self.assertIn("search_text", sql)
            self.assertEqual(
                [str(row[0]) for row in connection.execute("PRAGMA quick_check").fetchall()],
                ["ok"],
            )
            record = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectRule",
                    subject_key="fresh.cjk",
                    title="护甲升级链",
                    body="新的护甲升级链规则。",
                    source_kind="user-confirmed",
                ),
            )
            hits = search_memory_records(
                connection, project_key=PROJECT, query="护甲", limit=5
            )
            self.assertEqual([hit.record.record_id for hit in hits], [record.record_id])
            self.assertEqual(search_memory_records(connection, project_key=PROJECT, query="甲护", limit=5), ())

    def test_cjk_only_hits_have_deterministic_recency_order(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            older = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="order.older",
                    title="护甲旧记录",
                    body="护甲旧记录正文。",
                    source_kind="user-confirmed",
                ),
            )
            newer = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="order.newer",
                    title="护甲新记录",
                    body="护甲新记录正文。",
                    source_kind="user-confirmed",
                ),
            )
            connection.execute(
                "UPDATE memory_records SET updated_at_utc = '2026-01-01T00:00:00Z' WHERE record_id = ?",
                (older.record_id,),
            )
            connection.execute(
                "UPDATE memory_records SET updated_at_utc = '2026-02-01T00:00:00Z' WHERE record_id = ?",
                (newer.record_id,),
            )
            connection.commit()
            hits = search_memory_records(connection, project_key=PROJECT, query="护甲", limit=5)
            self.assertEqual(
                [hit.record.record_id for hit in hits],
                [newer.record_id, older.record_id],
            )
            self.assertTrue(all(hit.rank == 0.0 for hit in hits))

    def test_writable_open_uses_wal_and_rejects_immutable(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(str(connection.execute("PRAGMA journal_mode").fetchone()[0]), "wal")
        with self.assertRaisesRegex(ValueError, "read-only"):
            with open_project_memory_database(self.database_path, immutable=True):
                pass


if __name__ == "__main__":
    unittest.main(verbosity=2)

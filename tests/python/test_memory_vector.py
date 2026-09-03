from __future__ import annotations

import hashlib
import math
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_schema import (  # noqa: E402
    CURRENT_MEMORY_SCHEMA_VERSION,
    MEMORY_MIGRATIONS,
)
from ue_agent_kit.memory_vector import (  # noqa: E402
    EMBEDDING_TEXT_BOUND_CHARS,
    RRF_K,
    BackfillReport,
    MemoryVectorError,
    Model2VecProvider,
    VectorProvider,
    backfill_embeddings,
    canonical_embedding_text,
    canonical_query_text,
    deserialize_embedding,
    ensure_embeddings_for_records,
    fuse_with_recency,
    get_shared_provider,
    hybrid_search_memory_records,
    load_sqlite_vec,
    memory_embeddings_table_exists,
    reset_shared_provider_cache,
    rrf_fuse,
    serialize_embedding,
    vector_search_record_ids,
)
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    MemorySourceKind,
    MemoryStatus,
    create_memory_record,
    invalidate_memory_revisions,
    open_project_memory_database,
    search_memory_records,
)

PROJECT = "M4向量测试项目"
VECTOR_MODEL_ENV = "UEAGENTKIT_MEMORY_VECTOR_MODEL"


def _sqlite_vec_available() -> bool:
    probe = sqlite3.connect(":memory:")
    try:
        return load_sqlite_vec(probe)
    finally:
        probe.close()


SQLITE_VEC_AVAILABLE = _sqlite_vec_available()


class _StubProvider(VectorProvider):
    """Deterministic token-bucket provider for mechanics tests.

    Tokens are hashed into fixed dimension buckets so that a query sharing
    literal tokens with a record produces a higher cosine score in a fully
    deterministic way (unrelated to real model quality, which is covered by the
    separately-gated real-model tests and the benchmark script).
    """

    def __init__(self, *, dimension: int = 8, model_id: str = "stub:test-model:sha256:" + "a" * 64) -> None:
        self.dimension = dimension
        self.model_id = model_id
        self.embedded_texts: list[str] = []
        self.fail_texts: set[str] = set()

    def embed_text(self, text: str) -> tuple[float, ...]:
        self.embedded_texts.append(text)
        if text in self.fail_texts:
            raise MemoryVectorError("stub embedding failure")
        vector = [0.0] * self.dimension
        for token in re.findall(r"\w+", text.lower()):
            bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest()[:8], 16) % self.dimension
            vector[bucket] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return tuple(value / norm for value in vector)


def _canonical_record_text(record: MemoryRecordDraft) -> str:
    return canonical_embedding_text(
        record_type=str(record.record_type),
        subject_key=record.subject_key,
        title=record.title,
        body=record.body,
    )


class MemoryVectorSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_vector_")
        self.database_path = Path(self.temporary.name) / "memory.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_fresh_database_reaches_v5_with_ordinary_embeddings_table(self) -> None:
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(CURRENT_MEMORY_SCHEMA_VERSION, 5)
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                5,
            )
            self.assertTrue(memory_embeddings_table_exists(connection))
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            self.assertNotIn("memory_embeddings_vec0", tables)
            for name in tables:
                self.assertFalse(name.startswith("vec0_"), name)

    def test_v5_embeddings_table_sql_is_fixed(self) -> None:
        expected_sql = (
            "CREATE TABLE memory_embeddings (\n"
            "    record_id           TEXT PRIMARY KEY\n"
            "                        REFERENCES memory_records(record_id) ON DELETE CASCADE,\n"
            "    model_id            TEXT NOT NULL,\n"
            "    dim                 INTEGER NOT NULL CHECK (dim > 0),\n"
            "    content_sha256      TEXT NOT NULL,\n"
            "    embedding           BLOB NOT NULL,\n"
            "    created_at_utc      TEXT NOT NULL,\n"
            "    updated_at_utc      TEXT NOT NULL\n"
            ")"
        )
        with open_project_memory_database(self.database_path) as connection:
            row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'memory_embeddings'"
            ).fetchone()
            self.assertEqual(str(row[0]), expected_sql)

    def test_v4_database_migrates_to_v5_without_optional_extras(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        try:
            for migration in MEMORY_MIGRATIONS[:4]:
                connection.executescript(migration.sql)
                connection.execute(
                    "INSERT INTO memory_schema_migrations(version, description, applied_at_utc)"
                    " VALUES (?, ?, '2026-09-01T00:00:00Z')",
                    (migration.version, migration.description),
                )
                connection.execute(f"PRAGMA user_version = {migration.version}")
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                4,
            )
            record_id = "mem_" + "4" * 32
            connection.execute(
                """
                INSERT INTO memory_records(
                    record_id, project_key, record_type, subject_key, title, body,
                    source_kind, source_ref, confidence, status, content_sha256,
                    created_at_utc, observed_at_utc, updated_at_utc, details_json
                ) VALUES (?, ?, 'projectFact', 'legacy-subject', 'Legacy title',
                          'Legacy body.', 'user-confirmed', '', 1.0, 'valid',
                          'sha256:legacy', '2026-09-01T00:00:00Z',
                          '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z', '{}')
                """,
                (record_id, PROJECT),
            )
            connection.commit()
        finally:
            connection.close()

        with open_project_memory_database(self.database_path) as migrated:
            self.assertEqual(
                int(migrated.execute("PRAGMA user_version").fetchone()[0]),
                5,
            )
            self.assertTrue(memory_embeddings_table_exists(migrated))
            remaining = migrated.execute(
                "SELECT COUNT(*) FROM memory_records WHERE record_id = ?", (record_id,)
            ).fetchone()[0]
            self.assertEqual(int(remaining), 1)
            self.assertEqual(
                migrated.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0],
                0,
            )

    def test_v5_reopen_is_idempotent(self) -> None:
        with open_project_memory_database(self.database_path):
            pass
        with open_project_memory_database(self.database_path) as connection:
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                5,
            )
            migration_rows = connection.execute(
                "SELECT COUNT(*) FROM memory_schema_migrations WHERE version = 5"
            ).fetchone()[0]
            self.assertEqual(int(migration_rows), 1)

    def test_v5_readonly_open_accepted(self) -> None:
        with open_project_memory_database(self.database_path):
            pass
        with open_project_memory_database(self.database_path, readonly=True) as connection:
            self.assertEqual(
                int(connection.execute("PRAGMA user_version").fetchone()[0]),
                5,
            )

    def test_record_delete_cascades_embedding_row(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            record = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="cascade-subject",
                    title="Cascade title",
                    body="Cascade body.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                ),
            )
            backfill_embeddings(connection, provider, project_key=PROJECT)
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0],
                1,
            )
            connection.execute("DELETE FROM memory_records WHERE record_id = ?", (record.record_id,))
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM memory_embeddings").fetchone()[0],
                0,
            )


class CanonicalTextAndSerializationTests(unittest.TestCase):
    def test_canonical_embedding_text_template_is_stable(self) -> None:
        text = canonical_embedding_text(
            record_type="projectFact",
            subject_key="/Game/M_A",
            title="Title",
            body="Body text.",
        )
        self.assertEqual(text, "recordType=projectFact\nsubject=/Game/M_A\ntitle=Title\nBody text.")
        again = canonical_embedding_text(
            record_type="projectFact",
            subject_key="/Game/M_A",
            title="Title",
            body="Body text.",
        )
        self.assertEqual(text, again)

    def test_canonical_embedding_text_is_bounded(self) -> None:
        text = canonical_embedding_text(
            record_type="projectFact",
            subject_key="s",
            title="t",
            body="x" * (EMBEDDING_TEXT_BOUND_CHARS + 5000),
        )
        self.assertEqual(len(text), EMBEDDING_TEXT_BOUND_CHARS)

    def test_canonical_query_text_is_bounded_and_raw(self) -> None:
        self.assertEqual(canonical_query_text("why was this chosen"), "why was this chosen")
        self.assertEqual(len(canonical_query_text("q" * 9999)), EMBEDDING_TEXT_BOUND_CHARS)

    def test_serialize_deserialize_round_trip(self) -> None:
        blob = serialize_embedding([1.0, -0.5, 0.25])
        expected = b"\x00\x00\x80\x3f" + b"\x00\x00\x00\xbf" + b"\x00\x00\x80\x3e"
        self.assertEqual(blob, expected)
        values = deserialize_embedding(blob)
        self.assertEqual(values, (1.0, -0.5, 0.25))

    def test_serialize_rejects_non_finite_and_empty(self) -> None:
        with self.assertRaises(MemoryVectorError):
            serialize_embedding([])
        with self.assertRaises(MemoryVectorError):
            serialize_embedding([float("nan")])
        with self.assertRaises(MemoryVectorError):
            serialize_embedding([float("inf")])

    def test_deserialize_rejects_bad_blob(self) -> None:
        with self.assertRaises(MemoryVectorError):
            deserialize_embedding(b"\x01\x02\x03")
        with self.assertRaises(MemoryVectorError):
            deserialize_embedding(b"")


class RrfFuseTests(unittest.TestCase):
    def test_rrf_uses_k60_and_one_based_ranks(self) -> None:
        fused = rrf_fuse(["a"], [])
        self.assertEqual(RRF_K, 60)
        self.assertAlmostEqual(fused[0][1], 1.0 / 61.0)
        fused = rrf_fuse(["x"], ["x"])
        self.assertAlmostEqual(fused[0][1], 1.0 / 61.0 + 1.0 / 61.0)

    def test_rrf_order_and_best_rank(self) -> None:
        fused = rrf_fuse(["a", "b", "c"], ["b", "d", "a"])
        scores = {record_id: score for record_id, score, _ in fused}
        self.assertAlmostEqual(scores["b"], 1.0 / 61.0 + 1.0 / 62.0)
        self.assertAlmostEqual(scores["a"], 1.0 / 61.0 + 1.0 / 63.0)
        self.assertAlmostEqual(scores["c"], 1.0 / 63.0)
        self.assertAlmostEqual(scores["d"], 1.0 / 62.0)
        order = [record_id for record_id, _, _ in fused]
        self.assertEqual(order, ["b", "a", "d", "c"])
        best = {record_id: rank for record_id, _, rank in fused}
        self.assertEqual(best["a"], 1)
        self.assertEqual(best["b"], 1)
        self.assertEqual(best["d"], 2)

    def test_rrf_deduplicates_by_record_id(self) -> None:
        fused = rrf_fuse(["a", "a"], ["a"])
        self.assertEqual(len(fused), 1)


class BackfillTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_backfill_")
        self.database_path = Path(self.temporary.name) / "memory.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _add_records(self, connection, count: int) -> list[str]:
        ids = []
        for index in range(count):
            record = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key=f"subject-{index:03d}",
                    title=f"Title {index}",
                    body=f"Body {index} about materials and recovery.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                ),
            )
            ids.append(record.record_id)
        return ids

    def test_backfill_creates_rows_and_reports(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            ids = self._add_records(connection, 5)
            report = backfill_embeddings(connection, provider, project_key=PROJECT)
            self.assertIsInstance(report, BackfillReport)
            self.assertEqual(report.selected, 5)
            self.assertEqual(report.created, 5)
            self.assertEqual(report.rebuilt, 0)
            self.assertEqual(report.failed, 0)
            self.assertEqual(report.remaining, 0)
            self.assertEqual(report.model_id, provider.model_id)
            self.assertGreater(report.elapsed_ms, 0.0)
            rows = connection.execute(
                "SELECT record_id, model_id, dim, content_sha256, embedding FROM memory_embeddings ORDER BY record_id"
            ).fetchall()
            self.assertEqual([str(row[0]) for row in rows], sorted(ids))
            for row in rows:
                self.assertEqual(str(row[1]), provider.model_id)
                self.assertEqual(int(row[2]), provider.dimension)
                self.assertRegex(str(row[3]), r"^sha256:[0-9a-f]{64}$")
                self.assertTrue(isinstance(row[4], bytes))
                self.assertEqual(len(row[4]), provider.dimension * 4)

    def test_backfill_is_idempotent_second_run_zero_rewrites(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            self._add_records(connection, 4)
            first = backfill_embeddings(connection, provider, project_key=PROJECT)
            self.assertEqual(first.created, 4)
            snapshots = connection.execute(
                "SELECT record_id, embedding, updated_at_utc FROM memory_embeddings ORDER BY record_id"
            ).fetchall()
            second = backfill_embeddings(connection, provider, project_key=PROJECT)
            self.assertEqual(second.selected, 0)
            self.assertEqual(second.created, 0)
            self.assertEqual(second.rebuilt, 0)
            self.assertEqual(second.reused, 4)
            self.assertEqual(second.remaining, 0)
            after = connection.execute(
                "SELECT record_id, embedding, updated_at_utc FROM memory_embeddings ORDER BY record_id"
            ).fetchall()
            self.assertEqual(snapshots, after)

    def test_backfill_rebuilds_on_model_change(self) -> None:
        first_provider = _StubProvider(model_id="stub:model-one:sha256:" + "1" * 64)
        second_provider = _StubProvider(model_id="stub:model-two:sha256:" + "2" * 64)
        with open_project_memory_database(self.database_path) as connection:
            self._add_records(connection, 3)
            backfill_embeddings(connection, first_provider, project_key=PROJECT)
            report = backfill_embeddings(connection, second_provider, project_key=PROJECT)
            self.assertEqual(report.rebuilt, 3)
            self.assertEqual(report.created, 0)
            rows = connection.execute(
                "SELECT DISTINCT model_id FROM memory_embeddings"
            ).fetchall()
            self.assertEqual([str(row[0]) for row in rows], [second_provider.model_id])

    def test_backfill_rebuilds_on_content_change_and_binding_holds(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            ids = self._add_records(connection, 2)
            backfill_embeddings(connection, provider, project_key=PROJECT)
            # Simulate an external content change: the stored embedding must no longer
            # be considered current (content_sha256 binding).
            connection.execute(
                "UPDATE memory_records SET content_sha256 = 'sha256:changed' WHERE record_id = ?",
                (ids[0],),
            )
            report = backfill_embeddings(connection, provider, project_key=PROJECT)
            self.assertEqual(report.selected, 1)
            self.assertEqual(report.rebuilt, 1)
            row = connection.execute(
                "SELECT content_sha256 FROM memory_embeddings WHERE record_id = ?",
                (ids[0],),
            ).fetchone()
            self.assertEqual(str(row[0]), "sha256:changed")

    def test_backfill_bounded_by_max_records_and_reports_remaining(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            self._add_records(connection, 6)
            report = backfill_embeddings(
                connection, provider, project_key=PROJECT, max_records=2
            )
            self.assertEqual(report.selected, 2)
            self.assertEqual(report.created, 2)
            self.assertEqual(report.remaining, 4)
            report = backfill_embeddings(
                connection, provider, project_key=PROJECT, max_records=2
            )
            self.assertEqual(report.selected, 2)
            self.assertEqual(report.remaining, 2)
            report = backfill_embeddings(connection, provider, project_key=PROJECT)
            self.assertEqual(report.created, 2)
            self.assertEqual(report.remaining, 0)

    def test_backfill_batch_size_bound_and_stable_order(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            ids = self._add_records(connection, 5)
            report = backfill_embeddings(
                connection, provider, project_key=PROJECT, batch_size=2
            )
            self.assertEqual(report.created, 5)
            stored = [
                str(row[0])
                for row in connection.execute(
                    "SELECT record_id FROM memory_embeddings ORDER BY rowid"
                ).fetchall()
            ]
            self.assertEqual(stored, sorted(ids))
            with self.assertRaises(ValueError):
                backfill_embeddings(
                    connection, provider, project_key=PROJECT, batch_size=0
                )
            with self.assertRaises(ValueError):
                backfill_embeddings(
                    connection, provider, project_key=PROJECT, batch_size=501
                )

    def test_backfill_failure_is_bounded_and_visible(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            ids = self._add_records(connection, 3)
            provider.fail_texts.add(_canonical_record_text(
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="subject-001",
                    title="Title 1",
                    body="Body 1 about materials and recovery.",
                    source_kind=MemorySourceKind.USER_CONFIRMED,
                )
            ))
            report = backfill_embeddings(connection, provider, project_key=PROJECT)
            self.assertEqual(report.selected, 3)
            self.assertEqual(report.created, 2)
            self.assertEqual(report.failed, 1)
            self.assertEqual(len(report.failed_record_ids), 1)
            self.assertEqual(report.remaining, 1)
            committed = {
                str(row[0])
                for row in connection.execute("SELECT record_id FROM memory_embeddings").fetchall()
            }
            self.assertEqual(committed, set(ids) - {ids[1]})

    def test_ensure_embeddings_for_records_is_strictly_bounded(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            ids = self._add_records(connection, 4)
            report = ensure_embeddings_for_records(
                connection, provider, project_key=PROJECT, record_ids=[ids[0], ids[2]]
            )
            self.assertEqual(report.selected, 2)
            self.assertEqual(report.created, 2)
            stored = {
                str(row[0])
                for row in connection.execute("SELECT record_id FROM memory_embeddings").fetchall()
            }
            self.assertEqual(stored, {ids[0], ids[2]})
            with self.assertRaises(KeyError):
                ensure_embeddings_for_records(
                    connection, provider, project_key=PROJECT, record_ids=["mem_" + "f" * 32]
                )


@unittest.skipUnless(SQLITE_VEC_AVAILABLE, "optional sqlite-vec extension is not installed")
class VectorBranchAndHybridTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_memory_hybrid_")
        self.database_path = Path(self.temporary.name) / "memory.sqlite3"
        self._saved_env = os.environ.get(VECTOR_MODEL_ENV)
        os.environ.pop(VECTOR_MODEL_ENV, None)
        reset_shared_provider_cache()

    def tearDown(self) -> None:
        if self._saved_env is None:
            os.environ.pop(VECTOR_MODEL_ENV, None)
        else:
            os.environ[VECTOR_MODEL_ENV] = self._saved_env
        reset_shared_provider_cache()
        self.temporary.cleanup()

    def _record_draft(self, *, subject: str, body: str, record_type: str = "projectFact") -> MemoryRecordDraft:
        return MemoryRecordDraft(
            project_key=PROJECT,
            record_type=record_type,
            subject_key=subject,
            title=f"Title for {subject}",
            body=body,
            source_kind=MemorySourceKind.USER_CONFIRMED,
            scopes=(MemoryScope(MemoryScopeType.ASSET, subject),),
        )

    def _build_corpus(self, connection, provider: VectorProvider) -> dict[str, str]:
        # Direct vector-branch tests call the SQL primitive without the hybrid
        # layer, so the optional sqlite-vec extension must be loaded explicitly.
        self.assertTrue(load_sqlite_vec(connection))
        records = {
            "tint": self._record_draft(
                subject="/Game/Materials/M_Master",
                body="The master material exposes a TintColor parameter with a fixed default value.",
            ),
            "camera": self._record_draft(
                subject="/Game/Camera/BP_Arm",
                body="Camera arm length is 40 centimetres because the capsule clipped framing.",
            ),
            "audio": self._record_draft(
                subject="/Game/Audio/Reverb",
                body="Interior reverb zones attenuate exterior ambience by 12 decibels.",
            ),
        }
        ids = {}
        for key, draft in records.items():
            ids[key] = create_memory_record(connection, draft).record_id
        backfill_embeddings(connection, provider, project_key=PROJECT)
        return ids

    def test_vector_branch_ranks_semantically_related_record_first(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            ids = self._build_corpus(connection, provider)
            query_blob = serialize_embedding(provider.embed_text(canonical_query_text("material color parameter")))
            rows = vector_search_record_ids(
                connection,
                model_id=provider.model_id,
                query_embedding=query_blob,
                project_key=PROJECT,
                limit=3,
            )
            self.assertTrue(rows)
            returned_ids = [record_id for record_id, _ in rows]
            self.assertIn(ids["tint"], returned_ids)
            self.assertEqual(returned_ids[0], ids["tint"])

    def test_vector_branch_default_status_filter_excludes_stale(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
            stale_record = create_memory_record(
                connection,
                MemoryRecordDraft(
                    project_key=PROJECT,
                    record_type="projectFact",
                    subject_key="/Game/Materials/M_Stale",
                    title="Stale material fact",
                    body="The master material exposes a TintColor parameter with a fixed default value.",
                    source_kind=MemorySourceKind.TOOL_OBSERVED,
                    revision_set=(MemoryRevision("/Game/Materials/M_Stale", "sha256:old"),),
                ),
            )
            backfill_embeddings(connection, provider, project_key=PROJECT)
            invalidate_memory_revisions(
                connection,
                project_key=PROJECT,
                current_revisions={"/Game/Materials/M_Stale": "sha256:new"},
            )
            query_blob = serialize_embedding(provider.embed_text(canonical_query_text("material color parameter")))
            rows = vector_search_record_ids(
                connection,
                model_id=provider.model_id,
                query_embedding=query_blob,
                project_key=PROJECT,
                limit=10,
            )
            self.assertNotIn(stale_record.record_id, [record_id for record_id, _ in rows])

    def test_vector_branch_filter_parity_with_fts(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
            query_blob = serialize_embedding(provider.embed_text(canonical_query_text("material color parameter")))
            # Type filter parity: corpus has no knownIssue records; both branches agree.
            fts_ids = {
                hit.record.record_id
                for hit in search_memory_records(
                    connection,
                    project_key=PROJECT,
                    query="material color parameter",
                    record_types=("knownIssue",),
                    limit=10,
                )
            }
            self.assertEqual(fts_ids, set())
            vector_ids = {
                record_id
                for record_id, _ in vector_search_record_ids(
                    connection,
                    model_id=provider.model_id,
                    query_embedding=query_blob,
                    project_key=PROJECT,
                    record_types=("knownIssue",),
                    limit=10,
                )
            }
            self.assertEqual(vector_ids, set())
            # Scope filter parity: only the audio record carries the Reverb scope.
            vector_ids = {
                record_id
                for record_id, _ in vector_search_record_ids(
                    connection,
                    model_id=provider.model_id,
                    query_embedding=query_blob,
                    project_key=PROJECT,
                    scope_type=MemoryScopeType.ASSET,
                    scope_key="/Game/Audio/Reverb",
                    limit=10,
                )
            }
            self.assertEqual(len(vector_ids), 1)
            # Status filter parity: stale-only status filter excludes all valid records.
            vector_ids = {
                record_id
                for record_id, _ in vector_search_record_ids(
                    connection,
                    model_id=provider.model_id,
                    query_embedding=query_blob,
                    project_key=PROJECT,
                    statuses=(MemoryStatus.STALE,),
                    limit=10,
                )
            }
            self.assertEqual(vector_ids, set())

    def test_vector_branch_project_isolation(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
            create_memory_record(
                connection,
                self._record_draft(
                    subject="/Game/Materials/M_OtherProject",
                    body="The master material exposes a TintColor parameter with a fixed default value.",
                ),
            )
            # Move the extra record to another project directly to prove project filtering.
            connection.execute(
                "UPDATE memory_records SET project_key = 'OtherProject' "
                "WHERE subject_key = '/Game/Materials/M_OtherProject'"
            )
            query_blob = serialize_embedding(provider.embed_text(canonical_query_text("material color parameter")))
            rows = vector_search_record_ids(
                connection,
                model_id=provider.model_id,
                query_embedding=query_blob,
                project_key=PROJECT,
                limit=10,
            )
            for record_id, _ in rows:
                project = connection.execute(
                    "SELECT project_key FROM memory_records WHERE record_id = ?", (record_id,)
                ).fetchone()
                self.assertEqual(str(project[0]), PROJECT)

    def test_hybrid_generates_exactly_one_query_embedding_and_zero_corpus_embeddings(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
            corpus_texts = set(provider.embedded_texts)
            self.assertEqual(len(corpus_texts), 3)
            provider.embedded_texts.clear()
            outcome = hybrid_search_memory_records(
                connection,
                provider=provider,
                project_key=PROJECT,
                query="why the camera arm was shortened",
                limit=5,
            )
            self.assertEqual(outcome.retrieval_mode, "hybrid")
            self.assertEqual(outcome.query_embedding_count, 1)
            self.assertEqual(outcome.vector_fallback, "")
            self.assertEqual(len(provider.embedded_texts), 1)
            self.assertEqual(
                provider.embedded_texts[0],
                canonical_query_text("why the camera arm was shortened"),
            )
            payload = outcome.to_payload()
            self.assertEqual(payload["corpusEmbeddingCount"], 0)
            self.assertEqual(payload["retrievalMode"], "hybrid")

    def test_hybrid_fusion_includes_fts_only_records(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            ids = self._build_corpus(connection, provider)
            unembedded = create_memory_record(
                connection,
                self._record_draft(
                    subject="/Game/Materials/M_NoEmbedding",
                    body="TintColor parameter default value documentation.",
                ),
            )
            outcome = hybrid_search_memory_records(
                connection,
                provider=provider,
                project_key=PROJECT,
                query="TintColor parameter default value",
                limit=5,
            )
            returned = [hit.record.record_id for hit in outcome.hits]
            self.assertIn(unembedded.record_id, returned)
            self.assertIn(ids["tint"], returned)

    def test_hybrid_fallback_when_extension_unavailable(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
            with mock.patch(
                "ue_agent_kit.memory_vector.load_sqlite_vec", return_value=False
            ):
                outcome = hybrid_search_memory_records(
                    connection,
                    provider=provider,
                    project_key=PROJECT,
                    query="TintColor parameter",
                    limit=5,
                )
            self.assertEqual(outcome.retrieval_mode, "fts")
            self.assertFalse(outcome.vector_available)
            self.assertEqual(outcome.vector_fallback, "vector-extension-unavailable")
            self.assertEqual(outcome.query_embedding_count, 0)
            self.assertTrue(outcome.hits)

    def test_hybrid_fallback_when_embedding_fails(self) -> None:
        provider = _StubProvider(dimension=16)
        provider.fail_texts.add("broken query")
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
            outcome = hybrid_search_memory_records(
                connection,
                provider=provider,
                project_key=PROJECT,
                query="broken query",
                limit=5,
            )
            self.assertEqual(outcome.retrieval_mode, "fts")
            self.assertTrue(outcome.vector_available)
            self.assertEqual(outcome.vector_fallback, "vector-embedding-failed")
            self.assertEqual(outcome.query_embedding_count, 1)

    def test_hybrid_fallback_when_sql_rejects_vectors(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
            with mock.patch(
                "ue_agent_kit.memory_vector.vector_search_record_ids",
                side_effect=sqlite3.OperationalError("no such function: vec_distance_cosine"),
            ):
                outcome = hybrid_search_memory_records(
                    connection,
                    provider=provider,
                    project_key=PROJECT,
                    query="TintColor parameter",
                    limit=5,
                )
            self.assertEqual(outcome.retrieval_mode, "fts")
            self.assertEqual(outcome.vector_fallback, "vector-extension-unavailable")
            self.assertEqual(outcome.query_embedding_count, 1)

    def test_hybrid_tie_break_uses_recency_then_record_id(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            first = create_memory_record(
                connection,
                self._record_draft(subject="/Game/A/First", body="Unique alpha content."),
            )
            second = create_memory_record(
                connection,
                self._record_draft(subject="/Game/A/Second", body="Totally different beta content."),
            )
            backfill_embeddings(connection, provider, project_key=PROJECT)
            # Force a cross-branch tie: both records are retrieved by exactly one branch
            # at rank 1 and share the same updated_at_utc.
            connection.execute(
                "UPDATE memory_records SET updated_at_utc = '2026-09-02T00:00:00.000Z' "
                "WHERE record_id IN (?, ?)",
                (first.record_id, second.record_id),
            )
            fts_ranked = [first.record_id]
            vector_ranked = [second.record_id]
            fused = rrf_fuse(fts_ranked, vector_ranked)
            self.assertEqual(fused[0][1], fused[1][1])
            ordered = fuse_with_recency(connection, fused, limit=5)
            # Equal recency: the final record_id ASC tie-break decides.
            self.assertEqual(
                [record_id for record_id, _ in ordered],
                sorted([first.record_id, second.record_id]),
            )
            # Later updated_at_utc wins the tie.
            connection.execute(
                "UPDATE memory_records SET updated_at_utc = '2026-09-02T12:00:00.000Z' "
                "WHERE record_id = ?",
                (second.record_id,),
            )
            ordered = fuse_with_recency(connection, fused, limit=5)
            self.assertEqual(
                [record_id for record_id, _ in ordered],
                [second.record_id, first.record_id],
            )

    def test_service_search_falls_back_without_env_configuration(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
        service = ProjectMemoryService(database_path=self.database_path, project_key=PROJECT)
        result = service.search_records(query="TintColor parameter", limit=5)
        self.assertEqual(result.retrieval_mode, "fts")
        self.assertFalse(result.vector_available)
        self.assertEqual(result.vector_fallback, "vector-model-not-configured")
        self.assertEqual(result.query_embedding_count, 0)
        self.assertTrue(result.hits)
        payload = result.to_payload()
        self.assertEqual(payload["retrievalMode"], "fts")
        self.assertEqual(payload["corpusEmbeddingCount"], 0)

    def test_service_search_uses_env_configured_provider(self) -> None:
        model_root = Path(self.temporary.name) / "stub-model"
        model_root.mkdir()
        os.environ[VECTOR_MODEL_ENV] = str(model_root)
        real_provider = _StubProvider(dimension=16, model_id="stub:env-model:sha256:" + "3" * 64)

        def fake_from_local_dir(model_dir: Path) -> VectorProvider:
            self.assertEqual(Path(model_dir), model_root.resolve())
            return real_provider

        with open_project_memory_database(self.database_path) as connection:
            ids = self._build_corpus(connection, real_provider)
        with mock.patch(
            "ue_agent_kit.memory_vector.Model2VecProvider.from_local_dir",
            staticmethod(fake_from_local_dir),
        ):
            service = ProjectMemoryService(database_path=self.database_path, project_key=PROJECT)
            result = service.search_records(query="material color parameter", limit=5)
        self.assertEqual(result.retrieval_mode, "hybrid")
        self.assertTrue(result.vector_available)
        self.assertEqual(result.vector_fallback, "")
        self.assertEqual(result.query_embedding_count, 1)
        self.assertEqual([hit.record.record_id for hit in result.hits][0], ids["tint"])

    def test_automatic_context_never_resolves_provider(self) -> None:
        provider = _StubProvider(dimension=16)
        with open_project_memory_database(self.database_path) as connection:
            self._build_corpus(connection, provider)
        service = ProjectMemoryService(database_path=self.database_path, project_key=PROJECT)
        with mock.patch(
            "ue_agent_kit.memory_vector.get_shared_provider",
            side_effect=AssertionError("automatic recall must stay FTS-only in M4"),
        ):
            context = service.get_context(query="material color parameter")
        self.assertTrue(context)

    def test_get_shared_provider_reason_codes(self) -> None:
        os.environ.pop(VECTOR_MODEL_ENV, None)
        reset_shared_provider_cache()
        provider, reason = get_shared_provider()
        self.assertIsNone(provider)
        self.assertEqual(reason, "vector-model-not-configured")

        missing_root = Path(self.temporary.name) / "missing-model"
        os.environ[VECTOR_MODEL_ENV] = str(missing_root)
        reset_shared_provider_cache()
        provider, reason = get_shared_provider()
        self.assertIsNone(provider)
        self.assertIn(reason, {"vector-model-load-failed", "vector-extra-not-installed"})
        # Failed resolution is cached with a stable reason and does not retry.
        self.assertFalse(os.path.exists(missing_root))


@unittest.skipUnless(
    os.environ.get(VECTOR_MODEL_ENV, "").strip() and os.environ.get("UEAGENTKIT_TEST_REAL_VECTOR_MODEL") == "1",
    "real local model2vec model directory not configured for tests",
)
class Model2VecProviderRealModelTests(unittest.TestCase):
    def test_real_model_loads_with_stable_identity_and_deterministic_output(self) -> None:
        model_dir = Path(os.environ[VECTOR_MODEL_ENV])
        provider = Model2VecProvider.from_local_dir(model_dir)
        self.assertTrue(provider.model_id.startswith("model2vec:"))
        self.assertRegex(provider.model_id, r"^model2vec:[^:]+:sha256:[0-9a-f]{64}$")
        self.assertEqual(provider.dimension, 256)
        first = provider.embed_text("why was this material parameter chosen")
        second = provider.embed_text("why was this material parameter chosen")
        self.assertEqual(first, second)
        self.assertTrue(all(math.isfinite(value) for value in first))
        blob = serialize_embedding(first)
        self.assertEqual(len(blob), provider.dimension * 4)

    def test_real_model_batch_matches_single(self) -> None:
        model_dir = Path(os.environ[VECTOR_MODEL_ENV])
        provider = Model2VecProvider.from_local_dir(model_dir)
        texts = ["camera arm length", "material TintColor value", "recovery ordering"]
        batch = provider.embed_batch(texts)
        self.assertEqual(len(batch), 3)
        for text, row in zip(texts, batch):
            self.assertEqual(row, provider.embed_text(text))

    def test_provider_requires_existing_local_directory(self) -> None:
        with self.assertRaises(MemoryVectorError):
            Model2VecProvider.from_local_dir(Path(self.id() + "-does-not-exist"))


if __name__ == "__main__":
    unittest.main()

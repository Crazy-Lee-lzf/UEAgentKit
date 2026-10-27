from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_vector import hybrid_search_memory_records  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    create_memory_record,
    open_project_memory_database,
)

PROJECT = "测试项目"


class _StubProvider:
    model_id = "stub-cjk-provider"

    def embed_text(self, text: str) -> tuple[float, ...]:
        return (0.25, 0.5, 0.75)

    def embed_batch(self, texts):
        return [self.embed_text(text) for text in texts]


class CjkServiceSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="ueak_cjk_v7_search_")
        self.root = Path(self._temporary.name)
        self.database_path = self.root / "memory" / "project-memory.sqlite3"
        with open_project_memory_database(self.database_path) as connection:
            self.han_id = (
                _create(
                    connection,
                    subject="design.armor",
                    title="护甲升级链",
                    body="AK47护甲升级后获得额外减伤。",
                ).record_id
            )
            self.english_id = (
                _create(
                    connection,
                    subject="design.inventory",
                    title="Inventory slot color",
                    body="Inventory slot color uses the rarity table.",
                ).record_id
            )
        self.service = ProjectMemoryService(
            database_path=self.database_path,
            project_key=PROJECT,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def _ids(self, query: str, **kwargs) -> list[str]:
        result = self.service.search_records(query=query, limit=10, **kwargs)
        return [hit.record.record_id for hit in result.hits]

    def test_service_cjk_substring_is_a_normal_fts_hit(self) -> None:
        result = self.service.search_records(query="护甲", limit=10)
        self.assertEqual([hit.record.record_id for hit in result.hits], [self.han_id])
        self.assertEqual(result.retrieval_mode, "fts")
        self.assertFalse(result.substring_fallback)
        self.assertEqual(result.scanned_records, 0)

    def test_service_reverse_and_pure_ascii_behavior(self) -> None:
        self.assertEqual(self._ids("甲护"), [])
        self.assertEqual(self._ids("inventory"), [self.english_id])
        self.assertEqual(self._ids("AK47护甲"), [self.han_id])

    def test_hybrid_rrf_keeps_vector_branch_and_cjk_fts_branch(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            with mock.patch(
                "ue_agent_kit.memory_vector.load_sqlite_vec", return_value=True
            ), mock.patch(
                "ue_agent_kit.memory_vector.vector_search_record_ids",
                return_value=[(self.english_id, 0.9)],
            ):
                outcome = hybrid_search_memory_records(
                    connection,
                    provider=provider,
                    project_key=PROJECT,
                    query="护甲",
                    limit=10,
                )
        self.assertEqual(outcome.retrieval_mode, "hybrid")
        returned = [hit.record.record_id for hit in outcome.hits]
        self.assertIn(self.han_id, returned)
        self.assertIn(self.english_id, returned)
        self.assertEqual(outcome.query_embedding_count, 1)
        self.assertEqual(outcome.vector_fallback, "")
        # Both branches contribute one rank-1 result, so RRF scores tie and the
        # frozen best-rank / recency / record-id tie-break decides.
        scores = {hit.record.record_id: hit.rank for hit in outcome.hits}
        self.assertAlmostEqual(scores[self.han_id], 1.0 / 61.0)
        self.assertAlmostEqual(scores[self.english_id], 1.0 / 61.0)

    def test_hybrid_fts_branch_uses_bounded_cjk_fallback_when_projection_is_empty(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            connection.execute("UPDATE memory_records SET search_text = ''")
            connection.commit()
            with mock.patch(
                "ue_agent_kit.memory_vector.load_sqlite_vec", return_value=True
            ), mock.patch(
                "ue_agent_kit.memory_vector.vector_search_record_ids",
                return_value=[],
            ):
                outcome = hybrid_search_memory_records(
                    connection,
                    provider=provider,
                    project_key=PROJECT,
                    query="护甲",
                    limit=10,
                )
        self.assertEqual(outcome.retrieval_mode, "hybrid")
        self.assertEqual([hit.record.record_id for hit in outcome.hits], [self.han_id])

    def test_hybrid_degrades_to_cjk_fts_without_vector_stack(self) -> None:
        provider = _StubProvider()
        with open_project_memory_database(self.database_path) as connection:
            with mock.patch(
                "ue_agent_kit.memory_vector.load_sqlite_vec", return_value=False
            ):
                outcome = hybrid_search_memory_records(
                    connection,
                    provider=provider,
                    project_key=PROJECT,
                    query="护甲",
                    limit=10,
                )
        self.assertEqual(outcome.retrieval_mode, "fts")
        self.assertFalse(outcome.vector_available)
        self.assertEqual([hit.record.record_id for hit in outcome.hits], [self.han_id])


def _create(
    connection,
    *,
    subject: str,
    title: str,
    body: str,
):
    return create_memory_record(
        connection,
        MemoryRecordDraft(
            project_key=PROJECT,
            record_type="projectFact",
            subject_key=subject,
            title=title,
            body=body,
            source_kind="user-confirmed",
        ),
    )


if __name__ == "__main__":
    unittest.main(verbosity=2)

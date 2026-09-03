"""M4 deterministic 20-query hybrid recall quality/latency benchmark.

Builds the frozen relevance corpus (benchmarks/memory/m4_frozen_relevance_corpus_20260902.json),
backfills embeddings with the real local model2vec provider, and compares FTS5-only
against hybrid (FTS5 + vector + RRF K=60) retrieval over the 20 frozen queries.

Writes a JSON report (default: benchmarks/memory/m4_hybrid_recall_20260902.json) and
exits non-zero when the frozen acceptance gates fail. The provider must already exist
as a local model directory; no network access happens in this script.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_vector import (  # noqa: E402
    VectorProvider,
    backfill_embeddings,
    hybrid_search_memory_records,
)
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemoryScopeType,
    create_memory_record,
    invalidate_memory_revisions,
    mark_memory_record_superseded,
    open_project_memory_database,
    search_memory_records,
)

DEFAULT_CORPUS = TOOL_ROOT / "benchmarks" / "memory" / "m4_frozen_relevance_corpus_20260902.json"
DEFAULT_OUTPUT = TOOL_ROOT / "benchmarks" / "memory" / "m4_hybrid_recall_20260902.json"


class _CountingProvider(VectorProvider):
    def __init__(self, inner: VectorProvider) -> None:
        self._inner = inner
        self.embed_calls = 0

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    @property
    def dimension(self) -> int:
        return self._inner.dimension

    def embed_text(self, text: str) -> tuple[float, ...]:
        self.embed_calls += 1
        return self._inner.embed_text(text)

    def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        self.embed_calls += len(texts)
        return self._inner.embed_batch(texts)

    def reset(self) -> None:
        self.embed_calls = 0


def _percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((pct / 100.0) * len(ordered) + 0.5) - 1))
    return ordered[index]


def _build_corpus(connection, corpus: dict[str, Any]) -> None:
    project_key = corpus["projectKey"]
    records = corpus["records"]
    by_index = {index + 1: record for index, record in enumerate(records)}
    stale_records: list[str] = []
    for record in records:
        scopes = (MemoryScope(MemoryScopeType.PROJECT, project_key),)
        draft = MemoryRecordDraft(
            project_key=project_key,
            record_type=record["record_type"],
            subject_key=record["subject_key"],
            title=record["title"],
            body=record["body"],
            source_kind=record["source_kind"],
            confidence=float(record.get("confidence", 1.0)),
            scopes=scopes,
            record_id=record["record_id"],
        )
        if record.get("makeStale"):
            draft = MemoryRecordDraft(
                project_key=project_key,
                record_type=record["record_type"],
                subject_key=record["subject_key"],
                title=record["title"],
                body=record["body"],
                source_kind=record["source_kind"],
                confidence=float(record.get("confidence", 1.0)),
                scopes=scopes,
                record_id=record["record_id"],
                revision_set=(MemoryRevision("/Game/Environment/NaniteCliffs", "sha256:stale-old"),),
            )
            stale_records.append(record["record_id"])
        create_memory_record(connection, draft)
    for record in records:
        if record.get("makeSupersededByIndex"):
            mark_memory_record_superseded(
                connection,
                record_id=record["record_id"],
                replacement_record_id=by_index[record["makeSupersededByIndex"]]["record_id"],
                reason="frozen-corpus-supersession",
            )
    if stale_records:
        invalidate_memory_revisions(
            connection,
            project_key=project_key,
            current_revisions={"/Game/Environment/NaniteCliffs": "sha256:stale-new"},
        )


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def subset(kind: str) -> dict[str, Any]:
        selected = [row for row in rows if row["kind"] == kind]
        return {
            "queries": len(selected),
            "recallAt5": round(sum(row["recallAt5"] for row in selected) / len(selected), 4),
            "mrr": round(sum(row["mrr"] for row in selected) / len(selected), 4),
        }

    return {
        "lexical": subset("lexical"),
        "semantic": subset("semantic"),
        "aggregate": {
            "queries": len(rows),
            "recallAt5": round(sum(row["recallAt5"] for row in rows) / len(rows), 4),
            "mrr": round(sum(row["mrr"] for row in rows) / len(rows), 4),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--model-dir", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()

    from ue_agent_kit.memory_vector import Model2VecProvider

    model_dir = args.model_dir
    if model_dir is None:
        model_dir = Path(os.environ.get("UEAGENTKIT_MEMORY_VECTOR_MODEL", "").strip() or "")
    if not str(model_dir).strip():
        print("ERROR: --model-dir or UEAGENTKIT_MEMORY_VECTOR_MODEL is required.", file=sys.stderr)
        return 2

    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    project_key = corpus["projectKey"]
    queries = corpus["queries"]

    with tempfile.TemporaryDirectory(prefix="ueak_m4_recall_") as scratch:
        database_path = Path(scratch) / "memory.sqlite3"

        cold_load_ms: float | None = None
        load_started = time.perf_counter()
        real_provider = Model2VecProvider.from_local_dir(model_dir)
        cold_load_ms = (time.perf_counter() - load_started) * 1000.0

        provider = _CountingProvider(real_provider)

        with open_project_memory_database(database_path) as connection:
            _build_corpus(connection, corpus)
            backfill_report = backfill_embeddings(connection, provider, project_key=project_key)
            corpus_embed_calls = provider.embed_calls
            provider.reset()

            # FTS baseline over the frozen corpus.
            fts_rows: list[dict[str, Any]] = []
            fts_latency: list[float] = []
            for query in queries:
                best_rank = None
                samples: list[float] = []
                top5: list[str] = []
                for _ in range(args.rounds):
                    started = time.perf_counter()
                    hits = search_memory_records(
                        connection, project_key=project_key, query=query["text"], limit=5
                    )
                    samples.append((time.perf_counter() - started) * 1000.0)
                    top5 = [hit.record.record_id for hit in hits]
                for rank, record_id in enumerate(top5, start=1):
                    if record_id in query["relevant_ids"] and best_rank is None:
                        best_rank = rank
                fts_latency.extend(samples)
                fts_rows.append(
                    {
                        "queryId": query["id"],
                        "kind": query["kind"],
                        "text": query["text"],
                        "relevantIds": query["relevant_ids"],
                        "top5": top5,
                        "firstRelevantRank": best_rank,
                        "recallAt5": 1 if best_rank is not None else 0,
                        "mrr": 0.0 if best_rank is None else 1.0 / best_rank,
                    }
                )

            # Hybrid phase: fresh counting, every query must embed the query exactly once.
            hybrid_rows: list[dict[str, Any]] = []
            hybrid_latency: list[float] = []
            hybrid_modes: set[str] = set()
            total_query_embed_calls = 0
            for query in queries:
                best_rank = None
                top5: list[str] = []
                for _ in range(args.rounds):
                    provider.reset()
                    started = time.perf_counter()
                    outcome = hybrid_search_memory_records(
                        connection,
                        provider=provider,
                        project_key=project_key,
                        query=query["text"],
                        limit=5,
                    )
                    hybrid_latency.append((time.perf_counter() - started) * 1000.0)
                    hybrid_modes.add(outcome.retrieval_mode)
                    query_calls = provider.embed_calls
                    if query_calls != 1:
                        print(
                            f"ERROR: query {query['id']} generated {query_calls} query embeddings.",
                            file=sys.stderr,
                        )
                        return 1
                    total_query_embed_calls += query_calls
                    top5 = [hit.record.record_id for hit in outcome.hits]
                for rank, record_id in enumerate(top5, start=1):
                    if record_id in query["relevant_ids"] and best_rank is None:
                        best_rank = rank
                hybrid_rows.append(
                    {
                        "queryId": query["id"],
                        "kind": query["kind"],
                        "text": query["text"],
                        "relevantIds": query["relevant_ids"],
                        "top5": top5,
                        "firstRelevantRank": best_rank,
                        "recallAt5": 1 if best_rank is not None else 0,
                        "mrr": 0.0 if best_rank is None else 1.0 / best_rank,
                        "retrievalMode": outcome.retrieval_mode,
                        "vectorFallback": outcome.vector_fallback,
                    }
                )
            # The per-query "exactly one embedding" check is enforced above after a
            # fresh reset per round, so any corpus-side embed during a query would
            # have already failed the run. Zero is therefore proven, not assumed.
            corpus_calls_during_query = 0

            fts_summary = _metrics(fts_rows)
            hybrid_summary = _metrics(hybrid_rows)

            lexical_safety_violations = [
                row["queryId"]
                for row, hybrid_row in zip(fts_rows, hybrid_rows)
                if row["firstRelevantRank"] is not None
                and hybrid_row["firstRelevantRank"] is None
            ]

            acceptance = {
                "semanticRecallAt5HybridGreaterThanFts": (
                    hybrid_summary["semantic"]["recallAt5"] > fts_summary["semantic"]["recallAt5"]
                ),
                "aggregateMrrHybridNotBelowFts": (
                    hybrid_summary["aggregate"]["mrr"] >= fts_summary["aggregate"]["mrr"]
                ),
                "lexicalTop5SafetyHolds": not lexical_safety_violations,
                "hybridP95Under300ms": _percentile(hybrid_latency, 95) < 300.0,
                "queryEmbeddingCallsExactlyOnePerQuery": total_query_embed_calls
                == len(queries) * args.rounds,
                "corpusEmbeddingCallsDuringQueryZero": corpus_calls_during_query == 0,
                "hybridModeActive": "hybrid" in hybrid_modes,
            }

            report = {
                "stage": "M4 hybrid recall quality benchmark (frozen 20-query corpus)",
                "corpusPath": str(args.corpus),
                "modelDir": str(model_dir),
                "modelId": real_provider.model_id,
                "corpus": {
                    "projectKey": project_key,
                    "recordCount": len(corpus["records"]),
                    "queryCount": len(queries),
                },
                "backfill": backfill_report.to_payload(),
                "corpusEmbedCallsDuringBackfill": corpus_embed_calls,
                "fts": {
                    "summary": fts_summary,
                    "latencyMs": {
                        "p50": round(_percentile(fts_latency, 50), 3),
                        "p95": round(_percentile(fts_latency, 95), 3),
                        "samples": len(fts_latency),
                    },
                    "perQuery": fts_rows,
                },
                "hybrid": {
                    "summary": hybrid_summary,
                    "latencyMs": {
                        "p50": round(_percentile(hybrid_latency, 50), 3),
                        "p95": round(_percentile(hybrid_latency, 95), 3),
                        "samples": len(hybrid_latency),
                    },
                    "retrievalModes": sorted(hybrid_modes),
                    "perQuery": hybrid_rows,
                },
                "providerLoad": {
                    "coldLoadMs": round(cold_load_ms or 0.0, 3),
                    "note": "one-time per process; automatic Task Context never loads the model",
                },
                "lexicalSafetyViolations": lexical_safety_violations,
                "acceptance": acceptance,
            }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print("FTS    ", json.dumps(fts_summary))
    print("HYBRID ", json.dumps(hybrid_summary))
    print(
        "LATENCY fts p50/p95:",
        report["fts"]["latencyMs"]["p50"],
        "/",
        report["fts"]["latencyMs"]["p95"],
        "ms   hybrid p50/p95:",
        report["hybrid"]["latencyMs"]["p50"],
        "/",
        report["hybrid"]["latencyMs"]["p95"],
        "ms",
    )
    print("COLD LOAD ms:", report["providerLoad"]["coldLoadMs"])
    print("ACCEPTANCE:", json.dumps(acceptance))
    return 0 if all(acceptance.values()) else 1


if __name__ == "__main__":
    sys.exit(main())

"""M4 optional local vector support for Project Memory.

Zero required dependencies contract:
- ``model2vec`` and ``sqlite-vec`` are lazy, optional imports. Missing extras never
  break Project Memory; explicit search degrades to FTS with a stable reason code.
- The persistent schema never depends on the optional extras: embeddings live in
  the ordinary ``memory_embeddings`` table (schema v5) and vector distance is
  computed with the sqlite-vec scalar ``vec_distance_cosine`` function directly
  against the stored BLOB column. No ``vec0`` virtual table is ever created.
- No implicit network access: the embedding provider only loads a model that is
  already present in an explicit local directory, and offline-mode environment
  guards are applied before the optional hub-backed import.
- Automatic Task Context recall never touches this module; hybrid retrieval is
  only used by the explicit Memory Search facade.
"""

from __future__ import annotations

import hashlib
import math
import os
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from .memory_schema import CURRENT_MEMORY_SCHEMA_VERSION
from .project_memory import (
    MemoryRecordType,
    MemoryScopeType,
    MemorySearchHit,
    MemoryStatus,
    get_memory_record,
    search_memory_records,
)

EMBEDDING_TEXT_BOUND_CHARS = 4096
RRF_K = 60
_DEFAULT_INIT_BUDGET_MS = 300
_DEFAULT_BACKFILL_BATCH_SIZE = 64
_MAX_BACKFILL_BATCH_SIZE = 500
_FAILED_ID_REPORT_LIMIT = 20

VECTOR_MODEL_ENV = "UEAGENTKIT_MEMORY_VECTOR_MODEL"
VECTOR_INIT_BUDGET_ENV = "UEAGENTKIT_MEMORY_VECTOR_INIT_BUDGET_MS"

FALLBACK_MODEL_NOT_CONFIGURED = "vector-model-not-configured"
FALLBACK_EXTRA_NOT_INSTALLED = "vector-extra-not-installed"
FALLBACK_MODEL_LOAD_FAILED = "vector-model-load-failed"
FALLBACK_INIT_BUDGET = "vector-init-budget"
FALLBACK_EXTENSION_UNAVAILABLE = "vector-extension-unavailable"
FALLBACK_EMBEDDING_FAILED = "vector-embedding-failed"


class MemoryVectorError(RuntimeError):
    """Raised for invalid vector inputs; never leaks loader internals to MCP payloads."""


# ---------------------------------------------------------------------------
# Canonical embedding text and deterministic storage
# ---------------------------------------------------------------------------


def canonical_embedding_text(*, record_type: str, subject_key: str, title: str, body: str) -> str:
    """Deterministic bounded embedding input for one memory record.

    Arbitrary artifact payloads, stack traces, ``details_json`` and local paths are
    deliberately excluded. The bound is applied before any provider invocation so
    the provider input for identical record content is byte-identical.
    """
    text = (
        f"recordType={record_type}\n"
        f"subject={subject_key}\n"
        f"title={title}\n"
        f"{body}"
    )
    if len(text) > EMBEDDING_TEXT_BOUND_CHARS:
        text = text[:EMBEDDING_TEXT_BOUND_CHARS]
    return text


def canonical_query_text(query: str) -> str:
    """Deterministic bounded embedding input for one explicit search query."""
    return query[:EMBEDDING_TEXT_BOUND_CHARS]


def serialize_embedding(values: Sequence[float]) -> bytes:
    """Serialize one embedding to a little-endian float32 BLOB.

    All values must be finite; the dimension must be positive and bounded so a
    corrupted provider output can never enter the persistent schema.
    """
    if not values:
        raise MemoryVectorError("embedding must contain at least one value.")
    if len(values) > 65536:
        raise MemoryVectorError("embedding dimension exceeds the supported bound of 65536.")
    normalized: list[float] = []
    for value in values:
        numeric = float(value)
        if not math.isfinite(numeric):
            raise MemoryVectorError("embedding values must be finite.")
        normalized.append(numeric)
    return struct.pack("<" + "f" * len(normalized), *normalized)


def deserialize_embedding(blob: bytes) -> tuple[float, ...]:
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise MemoryVectorError("stored embedding must be a BLOB.")
    raw = bytes(blob)
    if len(raw) == 0 or len(raw) % 4 != 0:
        raise MemoryVectorError("stored embedding BLOB length must be a positive multiple of 4.")
    return struct.unpack("<" + "f" * (len(raw) // 4), raw)


# ---------------------------------------------------------------------------
# Optional local embedding provider (model2vec, local dir only)
# ---------------------------------------------------------------------------


class VectorProvider:
    """Narrow provider interface implemented without requiring NumPy in the base package."""

    model_id: str
    dimension: int

    def embed_text(self, text: str) -> tuple[float, ...]:
        raise NotImplementedError

    def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        return [self.embed_text(text) for text in texts]


class Model2VecProvider(VectorProvider):
    """model2vec static-embedding provider restricted to an existing local model directory."""

    def __init__(self, *, model_dir: Path, model_id: str, dimension: int, model: Any) -> None:
        self.model_dir = model_dir
        self.model_id = model_id
        self.dimension = dimension
        self._model = model

    @classmethod
    def from_local_dir(cls, model_dir: Path) -> "Model2VecProvider":
        resolved = Path(model_dir).expanduser().resolve()
        model_file = resolved / "model.safetensors"
        if not resolved.is_dir() or not model_file.is_file():
            raise MemoryVectorError("vector model directory with model.safetensors not found locally.")
        digest = _sha256_file(model_file)
        # Offline guards must be applied before the lazy hub-backed import so the
        # accepted runtime path can never attempt a model download.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
        try:
            from model2vec import StaticModel
        except ImportError as exc:
            raise MemoryVectorError("model2vec optional dependency is not installed.") from exc
        model = StaticModel.from_pretrained(str(resolved))
        dimension = int(model.dim)
        if dimension <= 0:
            raise MemoryVectorError("vector model reported a non-positive dimension.")
        model_id = f"model2vec:{resolved.name}:sha256:{digest}"
        return cls(model_dir=resolved, model_id=model_id, dimension=dimension, model=model)

    def embed_text(self, text: str) -> tuple[float, ...]:
        vector = self._model.encode(text)
        return tuple(float(value) for value in vector)

    def embed_batch(self, texts: Sequence[str]) -> list[tuple[float, ...]]:
        if not texts:
            return []
        matrix = self._model.encode(list(texts))
        return [tuple(float(value) for value in row) for row in matrix]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


_SHARED_PROVIDER_STATE: dict[str, tuple[str, Any]] = {}


def vector_model_path_from_env() -> Path | None:
    raw = os.environ.get(VECTOR_MODEL_ENV, "").strip()
    return Path(raw) if raw else None


def vector_init_budget_ms() -> int:
    raw = os.environ.get(VECTOR_INIT_BUDGET_ENV, "").strip()
    if not raw:
        return _DEFAULT_INIT_BUDGET_MS
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_INIT_BUDGET_MS
    return value if value > 0 else _DEFAULT_INIT_BUDGET_MS


def reset_shared_provider_cache() -> None:
    """Test helper: clear the process-level provider cache."""
    _SHARED_PROVIDER_STATE.clear()


def get_shared_provider(*, init_budget_ms: int | None = None) -> tuple[VectorProvider | None, str]:
    """Resolve the shared optional provider for this process.

    Returns ``(provider, "")`` when vector mode is usable, or ``(None, reason)``
    with a stable fallback reason code otherwise. A successful load is cached for
    the process; a failed load is cached as permanently unavailable with its
    reason so failing query paths never retry the load on every call. When the
    one-time load exceeds the init budget, this call fails open to the caller's
    FTS fallback while the loaded provider stays cached for later calls.
    """
    model_path = vector_model_path_from_env()
    if model_path is None:
        return None, FALLBACK_MODEL_NOT_CONFIGURED
    cache_key = str(model_path)
    cached = _SHARED_PROVIDER_STATE.get(cache_key)
    if cached is not None:
        if cached[0] == "ok":
            return cached[1], ""
        return None, str(cached[1])
    budget = vector_init_budget_ms() if init_budget_ms is None else init_budget_ms
    try:
        started = time.perf_counter()
        provider: VectorProvider = Model2VecProvider.from_local_dir(model_path)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
    except MemoryVectorError as exc:
        reason = (
            FALLBACK_EXTRA_NOT_INSTALLED
            if "optional dependency is not installed" in str(exc)
            else FALLBACK_MODEL_LOAD_FAILED
        )
        _SHARED_PROVIDER_STATE[cache_key] = ("failed", reason)
        return None, reason
    except Exception:
        _SHARED_PROVIDER_STATE[cache_key] = ("failed", FALLBACK_MODEL_LOAD_FAILED)
        return None, FALLBACK_MODEL_LOAD_FAILED
    _SHARED_PROVIDER_STATE[cache_key] = ("ok", provider)
    if elapsed_ms > budget:
        return None, FALLBACK_INIT_BUDGET
    return provider, ""


# ---------------------------------------------------------------------------
# sqlite-vec scalar functions on ordinary BLOB columns
# ---------------------------------------------------------------------------


def load_sqlite_vec(connection: sqlite3.Connection) -> bool:
    """Best-effort load of the optional sqlite-vec extension; False when unavailable."""
    try:
        import sqlite_vec
    except ImportError:
        return False
    try:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
    except Exception:
        return False
    return True


def _scope_clause(scope_type: MemoryScopeType | str | None, scope_key: str) -> tuple[str, list[Any]]:
    if scope_type is None:
        if scope_key:
            raise ValueError("scope_key requires scope_type.")
        return "", []
    normalized_scope_type = MemoryScopeType(scope_type)
    clause = (
        "EXISTS (SELECT 1 FROM memory_scopes AS s "
        "WHERE s.record_id = r.record_id AND s.scope_type = ?"
    )
    parameters: list[Any] = [normalized_scope_type.value]
    if scope_key:
        clause += " AND s.scope_key = ?"
        parameters.append(scope_key)
    clause += ")"
    return clause, parameters


def _filter_clauses(
    *,
    project_key: str,
    record_types: Sequence[MemoryRecordType | str],
    statuses: Sequence[MemoryStatus | str],
    scope_type: MemoryScopeType | str | None,
    scope_key: str,
) -> tuple[list[str], list[Any]]:
    clauses = ["r.project_key = ?"]
    parameters: list[Any] = [project_key]
    if record_types:
        normalized_types = [MemoryRecordType(value).value for value in record_types]
        clauses.append("r.record_type IN (" + ",".join("?" for _ in normalized_types) + ")")
        parameters.extend(normalized_types)
    if statuses:
        normalized_statuses = [MemoryStatus(value).value for value in statuses]
        clauses.append("r.status IN (" + ",".join("?" for _ in normalized_statuses) + ")")
        parameters.extend(normalized_statuses)
    scope_sql, scope_parameters = _scope_clause(scope_type, scope_key)
    if scope_sql:
        clauses.append(scope_sql)
        parameters.extend(scope_parameters)
    return clauses, parameters


def vector_search_record_ids(
    connection: sqlite3.Connection,
    *,
    model_id: str,
    query_embedding: bytes,
    project_key: str,
    record_types: Sequence[MemoryRecordType | str] = (),
    statuses: Sequence[MemoryStatus | str] = (
        MemoryStatus.VALID,
        MemoryStatus.UNVERIFIED,
        MemoryStatus.CONFLICTED,
    ),
    scope_type: MemoryScopeType | str | None = None,
    scope_key: str = "",
    limit: int = 20,
) -> tuple[tuple[str, float], ...]:
    """Vector branch over the ordinary ``memory_embeddings`` BLOB column.

    Filter parity with the FTS primitive is structural: the same project/type/
    status/scope predicates are applied to ``memory_records`` in the join, and
    stale/superseded records stay excluded by the same default status filter.
    Rows whose ``content_sha256`` no longer matches the current record content
    are never returned; missing embeddings simply do not participate.
    """
    clauses, parameters = _filter_clauses(
        project_key=project_key,
        record_types=record_types,
        statuses=statuses,
        scope_type=scope_type,
        scope_key=scope_key,
    )
    sql = (
        "SELECT e.record_id, vec_distance_cosine(e.embedding, ?) AS distance "
        "FROM memory_embeddings AS e "
        "JOIN memory_records AS r ON r.record_id = e.record_id AND "
        + " AND ".join(clauses)
        + " WHERE e.model_id = ? AND e.content_sha256 = r.content_sha256 "
        "AND vec_distance_cosine(e.embedding, ?) IS NOT NULL "
        "ORDER BY distance ASC, e.record_id ASC LIMIT ?"
    )
    rows = connection.execute(
        sql,
        (query_embedding, *parameters, model_id, query_embedding, limit),
    ).fetchall()
    return tuple((str(row[0]), float(row[1])) for row in rows)


# ---------------------------------------------------------------------------
# Deterministic RRF fusion
# ---------------------------------------------------------------------------


def rrf_fuse(
    fts_ranked_ids: Sequence[str],
    vector_ranked_ids: Sequence[str],
) -> list[tuple[str, float, int]]:
    """Fuse two 1-based ranked ID lists with the frozen RRF K=60 contract.

    Score: ``sum(1 / (RRF_K + rank_i))`` over the branches containing the record.
    Returned order: RRF score DESC, best branch rank ASC, then input record ID ASC
    as the final deterministic tie-break (updated_at_utc DESC is applied by the
    hybrid layer which has database access).
    """
    scores: dict[str, float] = {}
    best_rank: dict[str, int] = {}
    for ranked in (fts_ranked_ids, vector_ranked_ids):
        for rank, record_id in enumerate(ranked, start=1):
            scores[record_id] = scores.get(record_id, 0.0) + 1.0 / (RRF_K + rank)
            previous = best_rank.get(record_id)
            if previous is None or rank < previous:
                best_rank[record_id] = rank
    ordered = sorted(scores, key=lambda record_id: (-scores[record_id], best_rank[record_id], record_id))
    return [(record_id, scores[record_id], best_rank[record_id]) for record_id in ordered]


def fuse_with_recency(
    connection: sqlite3.Connection,
    fused: Sequence[tuple[str, float, int]],
    limit: int,
) -> list[tuple[str, float]]:
    """Apply the ``updated_at_utc DESC`` tie-break stage using database timestamps.

    Multi-pass stable sorting composes the frozen tie-break exactly:
    RRF score DESC, best branch rank ASC, updated_at_utc DESC, record_id ASC.
    """
    if not fused:
        return []
    placeholders = ",".join("?" for _ in fused)
    rows = connection.execute(
        "SELECT record_id, updated_at_utc FROM memory_records "
        f"WHERE record_id IN ({placeholders})",
        [record_id for record_id, _, _ in fused],
    ).fetchall()
    updated_at = {str(row[0]): str(row[1]) for row in rows}
    ordered = sorted(fused, key=lambda item: item[0])
    ordered = sorted(ordered, key=lambda item: updated_at.get(item[0], ""), reverse=True)
    ordered = sorted(ordered, key=lambda item: item[2])
    ordered = sorted(ordered, key=lambda item: -item[1])
    return [(record_id, score) for record_id, score, _ in ordered[:limit]]


# ---------------------------------------------------------------------------
# Hybrid explicit search
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HybridSearchOutcome:
    hits: tuple[MemorySearchHit, ...]
    retrieval_mode: str
    vector_available: bool
    vector_fallback: str
    query_embedding_count: int

    def to_payload(self) -> dict[str, Any]:
        return {
            "retrievalMode": self.retrieval_mode,
            "vectorAvailable": self.vector_available,
            "vectorFallback": self.vector_fallback,
            "queryEmbeddingCount": self.query_embedding_count,
            "corpusEmbeddingCount": 0,
        }


def hybrid_search_memory_records(
    connection: sqlite3.Connection,
    *,
    provider: VectorProvider,
    project_key: str,
    query: str,
    record_types: Sequence[MemoryRecordType | str] = (),
    statuses: Sequence[MemoryStatus | str] = (
        MemoryStatus.VALID,
        MemoryStatus.UNVERIFIED,
        MemoryStatus.CONFLICTED,
    ),
    scope_type: MemoryScopeType | str | None = None,
    scope_key: str = "",
    limit: int = 20,
) -> HybridSearchOutcome:
    """Explicit hybrid retrieval: FTS branch + vector branch + deterministic RRF.

    Exactly one query embedding is generated; stored record embeddings are never
    recomputed and missing embeddings never trigger backfill. Any vector-layer
    failure degrades to the FTS primitive with a stable reason code.
    """
    fts_hits = search_memory_records(
        connection,
        project_key=project_key,
        query=query,
        record_types=record_types,
        statuses=statuses,
        scope_type=scope_type,
        scope_key=scope_key,
        limit=limit,
    )
    if not load_sqlite_vec(connection):
        return HybridSearchOutcome(
            hits=fts_hits,
            retrieval_mode="fts",
            vector_available=False,
            vector_fallback=FALLBACK_EXTENSION_UNAVAILABLE,
            query_embedding_count=0,
        )
    try:
        query_vector = provider.embed_text(canonical_query_text(query))
        query_embedding = serialize_embedding(query_vector)
    except Exception:
        return HybridSearchOutcome(
            hits=fts_hits,
            retrieval_mode="fts",
            vector_available=True,
            vector_fallback=FALLBACK_EMBEDDING_FAILED,
            query_embedding_count=1,
        )
    try:
        vector_rows = vector_search_record_ids(
            connection,
            model_id=provider.model_id,
            query_embedding=query_embedding,
            project_key=project_key,
            record_types=record_types,
            statuses=statuses,
            scope_type=scope_type,
            scope_key=scope_key,
            limit=limit,
        )
    except sqlite3.Error:
        return HybridSearchOutcome(
            hits=fts_hits,
            retrieval_mode="fts",
            vector_available=True,
            vector_fallback=FALLBACK_EXTENSION_UNAVAILABLE,
            query_embedding_count=1,
        )
    fused = rrf_fuse(
        [hit.record.record_id for hit in fts_hits],
        [record_id for record_id, _ in vector_rows],
    )
    ordered = fuse_with_recency(connection, fused, limit)
    records_by_id = {hit.record.record_id: hit.record for hit in fts_hits}
    scores_by_id = dict(ordered)
    missing = [record_id for record_id, _ in ordered if record_id not in records_by_id]
    for record_id in missing:
        records_by_id[record_id] = get_memory_record(connection, record_id)
    hits = tuple(
        MemorySearchHit(records_by_id[record_id], scores_by_id[record_id])
        for record_id, _ in ordered
    )
    return HybridSearchOutcome(
        hits=hits,
        retrieval_mode="hybrid",
        vector_available=True,
        vector_fallback="",
        query_embedding_count=1,
    )


# ---------------------------------------------------------------------------
# Deterministic bounded backfill
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackfillReport:
    selected: int
    created: int
    reused: int
    rebuilt: int
    failed: int
    remaining: int
    model_id: str
    elapsed_ms: float
    failed_record_ids: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict[str, Any]:
        return {
            "selected": self.selected,
            "created": self.created,
            "reused": self.reused,
            "rebuilt": self.rebuilt,
            "failed": self.failed,
            "remaining": self.remaining,
            "modelId": self.model_id,
            "elapsedMs": round(self.elapsed_ms, 3),
            "failedRecordIds": list(self.failed_record_ids),
        }


def _mismatch_count(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    model_id: str,
) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM memory_records AS r
        LEFT JOIN memory_embeddings AS e ON e.record_id = r.record_id
        WHERE r.project_key = ?
          AND (e.record_id IS NULL OR e.model_id <> ? OR e.content_sha256 <> r.content_sha256)
        """,
        (project_key, model_id),
    ).fetchone()
    return int(row[0])


def _reused_count(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    model_id: str,
) -> int:
    row = connection.execute(
        """
        SELECT COUNT(*)
        FROM memory_records AS r
        JOIN memory_embeddings AS e ON e.record_id = r.record_id
        WHERE r.project_key = ?
          AND e.model_id = ?
          AND e.content_sha256 = r.content_sha256
        """,
        (project_key, model_id),
    ).fetchone()
    return int(row[0])


def _select_backfill_batch(
    connection: sqlite3.Connection,
    *,
    project_key: str,
    model_id: str,
    batch_size: int,
) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT r.record_id, r.record_type, r.subject_key, r.title, r.body, r.content_sha256
        FROM memory_records AS r
        LEFT JOIN memory_embeddings AS e ON e.record_id = r.record_id
        WHERE r.project_key = ?
          AND (e.record_id IS NULL OR e.model_id <> ? OR e.content_sha256 <> r.content_sha256)
        ORDER BY r.record_id
        LIMIT ?
        """,
        (project_key, model_id, batch_size),
    ).fetchall()


def _timestamp_utc() -> str:
    from .database import utc_now_iso

    return utc_now_iso()


def backfill_embeddings(
    connection: sqlite3.Connection,
    provider: VectorProvider,
    *,
    project_key: str,
    batch_size: int = _DEFAULT_BACKFILL_BATCH_SIZE,
    max_records: int = 0,
) -> BackfillReport:
    """Deterministic, bounded, restart-safe, idempotent embedding backfill.

    Stable ``ORDER BY record_id`` selection, bounded batches with per-batch
    commits, exact-skip of current ``model_id + content_sha256`` rows, and
    deterministic replacement of stale model/content rows. One failed record is
    counted and reported without corrupting already committed batches. No
    background thread and no request-path execution.
    """
    if batch_size < 1 or batch_size > _MAX_BACKFILL_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {_MAX_BACKFILL_BATCH_SIZE}.")
    if max_records < 0:
        raise ValueError("max_records must be >= 0 (0 = unbounded).")
    started = time.perf_counter()
    selected = 0
    created = 0
    rebuilt = 0
    failed = 0
    failed_ids: list[str] = []
    failed_in_run: set[str] = set()
    while True:
        if max_records and selected >= max_records:
            break
        remaining_budget = (max_records - selected) if max_records else batch_size
        chunk = min(batch_size, remaining_budget)
        rows = _select_backfill_batch(
            connection,
            project_key=project_key,
            model_id=provider.model_id,
            batch_size=chunk,
        )
        if not rows:
            break
        # A permanently failing record stays mismatched and would otherwise be
        # reselected forever; only process rows not already failed in this run
        # and stop when no row can make progress.
        pending = [row for row in rows if str(row[0]) not in failed_in_run]
        if not pending:
            break
        timestamp = _timestamp_utc()
        for row in pending:
            record_id = str(row[0])
            selected += 1
            try:
                text = canonical_embedding_text(
                    record_type=str(row[1]),
                    subject_key=str(row[2]),
                    title=str(row[3]),
                    body=str(row[4]),
                )
                values = provider.embed_text(text)
                if len(values) != provider.dimension:
                    raise MemoryVectorError("provider returned an unexpected embedding dimension.")
                blob = serialize_embedding(values)
            except Exception:
                failed += 1
                failed_in_run.add(record_id)
                if len(failed_ids) < _FAILED_ID_REPORT_LIMIT:
                    failed_ids.append(record_id)
                continue
            existing = connection.execute(
                "SELECT 1 FROM memory_embeddings WHERE record_id = ?",
                (record_id,),
            ).fetchone()
            with connection:
                connection.execute(
                    """
                    INSERT INTO memory_embeddings(
                        record_id, model_id, dim, content_sha256, embedding,
                        created_at_utc, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(record_id) DO UPDATE SET
                        model_id = excluded.model_id,
                        dim = excluded.dim,
                        content_sha256 = excluded.content_sha256,
                        embedding = excluded.embedding,
                        updated_at_utc = excluded.updated_at_utc
                    """,
                    (
                        record_id,
                        provider.model_id,
                        len(values),
                        str(row[5]),
                        blob,
                        timestamp,
                        timestamp,
                    ),
                )
            if existing is None:
                created += 1
            else:
                rebuilt += 1
    remaining = _mismatch_count(
        connection,
        project_key=project_key,
        model_id=provider.model_id,
    )
    reused = _reused_count(
        connection,
        project_key=project_key,
        model_id=provider.model_id,
    )
    return BackfillReport(
        selected=selected,
        created=created,
        reused=reused,
        rebuilt=rebuilt,
        failed=failed,
        remaining=remaining,
        model_id=provider.model_id,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        failed_record_ids=tuple(failed_ids),
    )


def ensure_embeddings_for_records(
    connection: sqlite3.Connection,
    provider: VectorProvider,
    *,
    project_key: str,
    record_ids: Sequence[str],
) -> BackfillReport:
    """Bounded post-distill embedding ensure for explicitly produced record IDs.

    Used by the offline distill integration layer only when vector mode is
    explicitly available. Strictly bounded: only the provided record IDs that
    belong to the fixed project and are currently mismatched are embedded;
    nothing else in the corpus is touched.
    """
    started = time.perf_counter()
    record_ids = list(dict.fromkeys(str(record_id) for record_id in record_ids))
    if len(record_ids) > 500:
        raise ValueError("record_ids must contain at most 500 entries.")
    if not record_ids:
        return BackfillReport(
            selected=0,
            created=0,
            reused=0,
            rebuilt=0,
            failed=0,
            remaining=0,
            model_id=provider.model_id,
            elapsed_ms=0.0,
        )
    placeholders = ",".join("?" for _ in record_ids)
    rows = connection.execute(
        f"""
        SELECT r.record_id, r.record_type, r.subject_key, r.title, r.body, r.content_sha256
        FROM memory_records AS r
        LEFT JOIN memory_embeddings AS e ON e.record_id = r.record_id
        WHERE r.project_key = ?
          AND r.record_id IN ({placeholders})
          AND (e.record_id IS NULL OR e.model_id <> ? OR e.content_sha256 <> r.content_sha256)
        ORDER BY r.record_id
        """,
        (project_key, *record_ids, provider.model_id),
    ).fetchall()
    found_ids = {str(row[0]) for row in rows}
    missing = [record_id for record_id in record_ids if record_id not in found_ids]
    # A requested ID that does not exist in this project is a caller bug: fail closed.
    if missing:
        probe = connection.execute(
            "SELECT project_key FROM memory_records WHERE record_id = ?",
            (missing[0],),
        ).fetchone()
        if probe is None:
            raise KeyError(f"Project Memory record not found: {missing[0]}")
        raise ValueError(f"Project Memory record belongs to another project: {missing[0]}")
    created = 0
    rebuilt = 0
    failed = 0
    failed_ids: list[str] = []
    timestamp = _timestamp_utc()
    for row in rows:
        record_id = str(row[0])
        try:
            text = canonical_embedding_text(
                record_type=str(row[1]),
                subject_key=str(row[2]),
                title=str(row[3]),
                body=str(row[4]),
            )
            values = provider.embed_text(text)
            if len(values) != provider.dimension:
                raise MemoryVectorError("provider returned an unexpected embedding dimension.")
            blob = serialize_embedding(values)
        except Exception:
            failed += 1
            if len(failed_ids) < _FAILED_ID_REPORT_LIMIT:
                failed_ids.append(record_id)
            continue
        existing = connection.execute(
            "SELECT 1 FROM memory_embeddings WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        with connection:
            connection.execute(
                """
                INSERT INTO memory_embeddings(
                    record_id, model_id, dim, content_sha256, embedding,
                    created_at_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    model_id = excluded.model_id,
                    dim = excluded.dim,
                    content_sha256 = excluded.content_sha256,
                    embedding = excluded.embedding,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    record_id,
                    provider.model_id,
                    len(values),
                    str(row[5]),
                    blob,
                    timestamp,
                    timestamp,
                ),
            )
        if existing is None:
            created += 1
        else:
            rebuilt += 1
    remaining = _mismatch_count(
        connection,
        project_key=project_key,
        model_id=provider.model_id,
    )
    reused = _reused_count(
        connection,
        project_key=project_key,
        model_id=provider.model_id,
    )
    return BackfillReport(
        selected=len(rows),
        created=created,
        reused=reused,
        rebuilt=rebuilt,
        failed=failed,
        remaining=remaining,
        model_id=provider.model_id,
        elapsed_ms=(time.perf_counter() - started) * 1000.0,
        failed_record_ids=tuple(failed_ids),
    )


# ---------------------------------------------------------------------------
# Schema helper used by tests and the CLI status surface
# ---------------------------------------------------------------------------


def memory_embeddings_table_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'memory_embeddings'"
    ).fetchone()
    return row is not None


__all__ = [
    "CURRENT_MEMORY_SCHEMA_VERSION",
    "BackfillReport",
    "FALLBACK_EMBEDDING_FAILED",
    "FALLBACK_EXTENSION_UNAVAILABLE",
    "FALLBACK_EXTRA_NOT_INSTALLED",
    "FALLBACK_INIT_BUDGET",
    "FALLBACK_MODEL_LOAD_FAILED",
    "FALLBACK_MODEL_NOT_CONFIGURED",
    "HybridSearchOutcome",
    "MemoryVectorError",
    "Model2VecProvider",
    "RRF_K",
    "VectorProvider",
    "backfill_embeddings",
    "canonical_embedding_text",
    "canonical_query_text",
    "deserialize_embedding",
    "ensure_embeddings_for_records",
    "get_shared_provider",
    "hybrid_search_memory_records",
    "load_sqlite_vec",
    "memory_embeddings_table_exists",
    "reset_shared_provider_cache",
    "rrf_fuse",
    "serialize_embedding",
    "vector_model_path_from_env",
    "vector_search_record_ids",
]

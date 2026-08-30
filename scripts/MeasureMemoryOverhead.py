"""Measure UEAgentKit Memory overhead and enforce M1/M2 hard performance gates.

M1-0 measurement harness. Standard-library only, U0, no network, no external
project. It creates temporary deterministic SQLite fixtures and measures:

  B0  Memory-disabled first Task Context call
  B1  Memory-enabled first Task Context call with an empty/no-hit Memory DB
  B2  Memory-enabled first Task Context call with a populated fixture
  B3  direct automatic recall (ProjectMemoryService.get_context)
  B4  task-end append (record_task_outcome)
  B5  single artifact-backed L0 capture
  B6  four-event L0 capture batch in one transaction
  B7  exact-state duplicate L0 replay

The report uses stable JSON schema/key ordering and machine/timing metadata
without absolute user paths. In --gate mode a non-zero exit is returned when a
hard M1 gate fails.

Usage:
  python scripts/MeasureMemoryOverhead.py [--samples 20] [--out ...] [--gate]

Baseline run (current v3 behavior, before M1 budget changes):
  python scripts/MeasureMemoryOverhead.py --samples 20 \
      --out benchmarks/memory/m1_memory_overhead_before_20260830.json
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Sequence

TOOL_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = TOOL_ROOT / "tests" / "python"
for root in (SRC_ROOT, TESTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from test_indexer_queries import make_asset, write_export  # noqa: E402
from ue_agent_kit.active_work import WorkItemDraft  # noqa: E402
from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.memory_context import (  # noqa: E402
    RECALL_MAX_CONTENT_CHARS,
    RECALL_MAX_ESTIMATED_TOKENS,
    RECALL_MAX_ITEMS,
)
from ue_agent_kit.memory_l0 import MemoryL0CaptureService  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.memory_tasks import TaskOutcomeDraft  # noqa: E402
from ue_agent_kit.memory_tree import KnowledgeNodeDraft  # noqa: E402
from ue_agent_kit.project_memory import (  # noqa: E402
    MemoryRecordDraft,
    MemoryRevision,
    MemoryScope,
    MemorySourceKind,
)
from ue_agent_kit.task_context import TaskContextService  # noqa: E402

REPORT_SCHEMA = "ueagentkit-memory-overhead/2.0"
PROJECT_KEY = "benchmark-project"
MEMORY_ASSET = "/Game/Perf/MemorySubject.MemorySubject"
MEMORY_ASSET2 = "/Game/Perf/MemorySubject2.MemorySubject2"
RECALL_QUERY = "memory benchmark subject"

# Hard gates from the M1 Detailed Plan section 7.4.
FIRST_TOOL_MEMORY_INCREMENTAL_P95_MS = 200.0
DIRECT_RECALL_P95_MS = 300.0
TASK_END_APPEND_P95_MS = 100.0
L0_CAPTURE_BATCH_P95_MS = 100.0


def _hash_asset(path: str, index: int) -> str:
    return f"{index:064x}"


def _build_index_database(root: Path) -> Path:
    database_path = root / "index.sqlite3"
    export_root = root / "export"
    assets = [
        make_asset(
            MEMORY_ASSET,
            profile="logic",
            revision=_hash_asset(MEMORY_ASSET, 1),
            rich=False,
            project_name=PROJECT_KEY,
        ),
        make_asset(
            MEMORY_ASSET2,
            profile="logic",
            revision=_hash_asset(MEMORY_ASSET2, 2),
            rich=False,
            project_name=PROJECT_KEY,
        ),
    ]
    write_export(export_root, assets)
    with open_database(database_path) as connection:
        result = build_index(
            connection,
            export_root,
            database_path,
            project_key=PROJECT_KEY,
        )
        if result.failed or result.errors:
            raise RuntimeError(f"failed to build deterministic index: {result.errors}")
    return database_path


def _build_empty_memory(root: Path) -> ProjectMemoryService:
    memory_path = root / "memory_empty.sqlite3"
    service = ProjectMemoryService(database_path=memory_path, project_key=PROJECT_KEY)
    service.create_node(
        KnowledgeNodeDraft(
            project_key=PROJECT_KEY,
            path="/project",
            node_type="project",
            title=PROJECT_KEY,
            summary="确定性基准项目根",
        )
    )
    return service


def _build_populated_memory(root: Path) -> ProjectMemoryService:
    memory_path = root / "memory_populated.sqlite3"
    service = ProjectMemoryService(database_path=memory_path, project_key=PROJECT_KEY)
    root_node = service.create_node(
        KnowledgeNodeDraft(
            project_key=PROJECT_KEY,
            path="/project",
            node_type="project",
            title=PROJECT_KEY,
            summary="确定性基准项目根",
        )
    )
    nodes: list[Any] = []
    for index in range(12):
        nodes.append(
            service.create_node(
                KnowledgeNodeDraft(
                    project_key=PROJECT_KEY,
                    path=f"/project/n{index}",
                    node_type="system",
                    title=f"Memory node {index}",
                    summary=f"Deterministic benchmark node {index} with memory subject content.",
                    parent_node_id=root_node.node_id,
                )
            )
        )
    for index in range(30):
        node = nodes[index % len(nodes)]
        service.add_record(
            MemoryRecordDraft(
                project_key=PROJECT_KEY,
                record_type="projectFact",
                subject_key=f"benchmark:subject:{index}",
                title=f"Memory record {index}",
                body=(
                    "Memory benchmark subject content with deterministic evidence. "
                    + " ".join(f"token{token}" for token in range(20))
                    + f" record-{index}"
                ),
                source_kind=MemorySourceKind.TOOL_OBSERVED,
                source_ref="benchmark:fixture",
                confidence=0.9,
                revision_set=(
                    MemoryRevision(MEMORY_ASSET, f"sha256:{_hash_asset(MEMORY_ASSET, index)}"),
                    MemoryRevision(MEMORY_ASSET2, f"sha256:{_hash_asset(MEMORY_ASSET2, index)}"),
                )
                if index % 2 == 0
                else (MemoryRevision(MEMORY_ASSET, f"sha256:{_hash_asset(MEMORY_ASSET, index)}"),),
                scopes=(
                    MemoryScope("asset", MEMORY_ASSET),
                    MemoryScope("asset", MEMORY_ASSET2),
                )
                if index % 3 == 0
                else (MemoryScope("asset", MEMORY_ASSET),),
                node_id=node.node_id,
            )
        )
    work_ids: list[str] = []
    for index in range(20):
        work_ids.append(
            service.create_work(
                WorkItemDraft(
                    project_key=PROJECT_KEY,
                    title=f"Memory benchmark work {index}",
                    description="Deterministic active work item for memory benchmark.",
                    next_action="Review memory benchmark evidence.",
                    priority=100 - index,
                    node_ids=(nodes[index % len(nodes)].node_id,),
                    asset_paths=(MEMORY_ASSET,),
                )
            ).work_item_id
        )
    return service


def _build_task_context_service(
    *,
    index_database_path: Path,
    memory_service: ProjectMemoryService | None,
) -> TaskContextService:
    return TaskContextService(
        index_service=IndexQueryService(index_database_path),
        memory_service=memory_service,
        freshness_tracker=None,
        live_editor_service=None,
        workflow_service=None,
    )


def _first_task_context_call(
    *,
    index_database_path: Path,
    memory_service: ProjectMemoryService | None,
) -> tuple[float, dict[str, Any]]:
    service = _build_task_context_service(
        index_database_path=index_database_path,
        memory_service=memory_service,
    )
    started = time.perf_counter()
    response = service.get_task_context(
        query="memory benchmark subject",
        asset_paths=(MEMORY_ASSET,),
        include_memory=memory_service is not None,
        max_output_tokens=4096,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if not response.get("ok"):
        raise RuntimeError("task context call returned not ok")
    return elapsed_ms, response


def _direct_recall(
    *,
    memory_service: ProjectMemoryService,
    query: str = RECALL_QUERY,
    asset_paths: Sequence[str] = (MEMORY_ASSET,),
) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    response = memory_service.get_context(
        query=query,
        asset_paths=tuple(asset_paths),
        detail_level=2,
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return elapsed_ms, response


def _task_end_append(
    *,
    memory_service: ProjectMemoryService,
    index: int,
) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    record = memory_service.record_task_outcome(
        TaskOutcomeDraft(
            task_key=f"benchmark-task-{index}",
            title="Memory benchmark task outcome",
            conclusion="Completed deterministic memory overhead benchmark.",
            outcome="succeeded",
            patch_ref=f"patch:{index}",
            backup_manifest_ref=f"backup:{index}",
            validation_evidence_ref=f"validation:{index}",
            revision_set=(
                MemoryRevision(MEMORY_ASSET, f"sha256:{_hash_asset(MEMORY_ASSET, index)}"),
            ),
            scopes=(MemoryScope("asset", MEMORY_ASSET),),
            confidence=1.0,
        )
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    if not record.record_id:
        raise RuntimeError("task outcome append returned no record id")
    return elapsed_ms, {"recordId": record.record_id, "recordType": record.record_type.value}


def _percentile(sorted_samples: Sequence[float], percentile: float) -> float:
    if not sorted_samples:
        raise ValueError("cannot compute percentile from an empty sample set")
    if percentile <= 0.0:
        return sorted_samples[0]
    if percentile >= 100.0:
        return sorted_samples[-1]
    rank = (len(sorted_samples) - 1) * percentile / 100.0
    lower = int(rank)
    upper = min(lower + 1, len(sorted_samples) - 1)
    fraction = rank - lower
    return sorted_samples[lower] + (sorted_samples[upper] - sorted_samples[lower]) * fraction


def _sample_stats(samples: Sequence[float]) -> dict[str, float]:
    if not samples:
        raise ValueError("cannot summarize an empty sample set")
    sorted_samples = sorted(samples)
    return {
        "n": len(samples),
        "minMs": round(min(samples), 3),
        "maxMs": round(max(samples), 3),
        "meanMs": round(statistics.fmean(samples), 3),
        "medianMs": round(statistics.median(samples), 3),
        "p95Ms": round(_percentile(sorted_samples, 95.0), 3),
        "stddevMs": round(statistics.stdev(samples), 3) if len(samples) >= 2 else 0.0,
    }


def _environment() -> dict[str, str]:
    # No absolute user paths; only portable identifiers.
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "implementation": platform.python_implementation(),
        "hostnameHash": "",
    }


def _measure(
    *,
    samples: int,
    index_database_path: Path,
    empty_memory: ProjectMemoryService,
    populated_memory: ProjectMemoryService,
) -> dict[str, Any]:
    # B0/B1 are measured as adjacent OFF/ON pairs so their derived delta is not
    # biased by running all disabled samples before all enabled samples.
    b0_first_ms: list[float] = []
    b1_first_ms: list[float] = []
    b1_recalled_item_counts: list[int] = []
    b1_content_chars: list[int] = []
    b1_estimated_tokens: list[int] = []
    for _ in range(samples):
        b0_elapsed_ms, _b0_response = _first_task_context_call(
            index_database_path=index_database_path,
            memory_service=None,
        )
        b0_first_ms.append(b0_elapsed_ms)

        b1_elapsed_ms, b1_response = _first_task_context_call(
            index_database_path=index_database_path,
            memory_service=empty_memory,
        )
        b1_first_ms.append(b1_elapsed_ms)
        b1_summary = b1_response.get("memory", {}).get("summary", {})
        b1_recalled_item_counts.append(int(b1_summary.get("recalledItemCount", 0)))
        b1_content_chars.append(int(b1_summary.get("contentChars", 0)))
        b1_estimated_tokens.append(int(b1_summary.get("estimatedTokens", 0)))

    # B2: Memory enabled, populated. Fresh service instance per sample.
    b2_first_ms: list[float] = []
    b2_recalled_item_counts: list[int] = []
    b2_content_chars: list[int] = []
    b2_estimated_tokens: list[int] = []
    for _ in range(samples):
        elapsed_ms, response = _first_task_context_call(
            index_database_path=index_database_path,
            memory_service=populated_memory,
        )
        b2_first_ms.append(elapsed_ms)
        summary = response.get("memory", {}).get("summary", {})
        b2_recalled_item_counts.append(int(summary.get("recalledItemCount", 0)))
        b2_content_chars.append(int(summary.get("contentChars", 0)))
        b2_estimated_tokens.append(int(summary.get("estimatedTokens", 0)))

    # B3: Direct automatic recall on the populated fixture. Fresh DB connection
    # is opened inside get_context for every sample.
    b3_ms: list[float] = []
    b3_recalled_item_counts: list[int] = []
    b3_content_chars: list[int] = []
    b3_estimated_tokens: list[int] = []
    b3_truncated: list[bool] = []
    b3_truncation_reasons: list[list[str]] = []
    for _ in range(samples):
        elapsed_ms, response = _direct_recall(memory_service=populated_memory)
        b3_ms.append(elapsed_ms)
        nodes = response.get("nodes", [])
        active = response.get("activeWork", [])
        records = response.get("records", [])
        reconstructed_items = len(nodes) + len(active) + len(records)
        reconstructed_content = len(
            json.dumps(
                {"nodes": nodes, "activeWork": active, "records": records},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        ) if reconstructed_items else 0
        if int(response.get("recalledItemCount", -1)) != reconstructed_items:
            raise RuntimeError("direct recall recalledItemCount metadata mismatch")
        if int(response.get("contentChars", -1)) != reconstructed_content:
            raise RuntimeError("direct recall contentChars metadata mismatch")
        b3_recalled_item_counts.append(reconstructed_items)
        b3_content_chars.append(reconstructed_content)
        b3_estimated_tokens.append(int(response.get("estimatedTokens", 0)))
        b3_truncated.append(bool(response.get("truncated")))
        reasons = response.get("truncationReasons")
        b3_truncation_reasons.append(
            list(reasons) if isinstance(reasons, list) else (["truncated"] if response.get("truncated") else [])
        )

    # B4: Task-end append. Deterministic task outcome appends into the populated DB.
    b4_ms: list[float] = []
    for index in range(samples):
        elapsed_ms, _record = _task_end_append(memory_service=populated_memory, index=index)
        b4_ms.append(elapsed_ms)

    artifact_root = populated_memory.database_path.parent / "workflow"
    artifact_root.mkdir(parents=True, exist_ok=True)
    l0_service = MemoryL0CaptureService(
        database_path=populated_memory.database_path,
        project_key=populated_memory.project_key,
        artifact_root=artifact_root,
    )

    # B5: Exact persisted bytes are digested and one artifact-backed event is appended.
    b5_ms: list[float] = []
    for index in range(samples):
        artifact = artifact_root / "single" / f"{index}.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(
            json.dumps({"sample": index}, separators=(",", ":")),
            encoding="utf-8",
        )
        started = time.perf_counter()
        result = l0_service.append_event(
            l0_service.artifact_draft(
                artifact_path=artifact,
                event_kind="live_write",
                lifecycle_state="applied",
                outcome="success",
                asset_paths=(MEMORY_ASSET,),
                change_set_id=f"cs_b5_{index}",
                details={"operationCount": 1},
            )
        )
        b5_ms.append((time.perf_counter() - started) * 1000.0)
        if result.captured_count != 1:
            raise RuntimeError("single L0 capture did not append exactly one row")

    # B6: Four evidence pointers are digested and committed in one SQLite transaction.
    b6_ms: list[float] = []
    replay_drafts = ()
    for index in range(samples):
        artifacts = []
        for ordinal in range(4):
            artifact = artifact_root / "batch" / f"{index}-{ordinal}.json"
            artifact.parent.mkdir(parents=True, exist_ok=True)
            artifact.write_text(
                json.dumps(
                    {"sample": index, "ordinal": ordinal},
                    separators=(",", ":"),
                ),
                encoding="utf-8",
            )
            artifacts.append(artifact)
        started = time.perf_counter()
        drafts = tuple(
            l0_service.artifact_draft(
                artifact_path=artifact,
                event_kind=event_kind,
                lifecycle_state="verified",
                outcome="success",
                asset_paths=(MEMORY_ASSET, MEMORY_ASSET2),
                change_set_id=f"cs_b6_{index}",
                details={"artifactOrdinal": ordinal},
            )
            for ordinal, (artifact, event_kind) in enumerate(
                zip(
                    artifacts,
                    ("checkpoint_set", "semantic_diff", "trust", "change_set"),
                    strict=True,
                )
            )
        )
        result = l0_service.append_events(drafts)
        b6_ms.append((time.perf_counter() - started) * 1000.0)
        if result.captured_count != 4:
            raise RuntimeError("four-event L0 batch did not append exactly four rows")
        replay_drafts = drafts

    # B7: Replaying the same exact durable states must allocate zero new rows.
    b7_ms: list[float] = []
    for _ in range(samples):
        started = time.perf_counter()
        result = l0_service.append_events(replay_drafts)
        b7_ms.append((time.perf_counter() - started) * 1000.0)
        if result.captured_count != 0 or result.existing_count != 4:
            raise RuntimeError("exact-state duplicate replay created new L0 rows")

    b0_stats = _sample_stats(b0_first_ms)
    b1_stats = _sample_stats(b1_first_ms)
    b2_stats = _sample_stats(b2_first_ms)
    b3_stats = _sample_stats(b3_ms)
    b4_stats = _sample_stats(b4_ms)
    b5_stats = _sample_stats(b5_ms)
    b6_stats = _sample_stats(b6_ms)
    b7_stats = _sample_stats(b7_ms)
    first_incrementals = [
        max(0.0, b1 - b0) for b1, b0 in zip(b1_first_ms, b0_first_ms, strict=True)
    ]
    first_incremental_stats = _sample_stats(first_incrementals)

    return {
        "scenarios": {
            "B0_memory_disabled_first_tool": {
                "description": "Memory-disabled first Task Context call on a fresh service instance.",
                "elapsedMs": b0_stats,
                "recalledItemCount": {
                    "n": samples,
                    "min": 0,
                    "max": 0,
                    "median": 0,
                },
                "contentChars": {"n": samples, "min": 0, "max": 0, "median": 0},
            },
            "B1_memory_enabled_empty": {
                "description": "Memory-enabled first Task Context call with an empty/no-hit Memory DB.",
                "elapsedMs": b1_stats,
                "recalledItemCount": {
                    "n": samples,
                    "min": min(b1_recalled_item_counts),
                    "max": max(b1_recalled_item_counts),
                    "median": int(statistics.median(b1_recalled_item_counts)),
                },
                "contentChars": {
                    "n": samples,
                    "min": min(b1_content_chars),
                    "max": max(b1_content_chars),
                    "median": int(statistics.median(b1_content_chars)),
                },
                "estimatedTokens": {
                    "n": samples,
                    "min": min(b1_estimated_tokens),
                    "max": max(b1_estimated_tokens),
                    "median": int(statistics.median(b1_estimated_tokens)),
                },
            },
            "B2_memory_enabled_populated": {
                "description": "Memory-enabled first Task Context call with a populated fixture.",
                "elapsedMs": b2_stats,
                "recalledItemCount": {
                    "n": samples,
                    "min": min(b2_recalled_item_counts),
                    "max": max(b2_recalled_item_counts),
                    "median": int(statistics.median(b2_recalled_item_counts)),
                },
                "contentChars": {
                    "n": samples,
                    "min": min(b2_content_chars),
                    "max": max(b2_content_chars),
                    "median": int(statistics.median(b2_content_chars)),
                },
                "estimatedTokens": {
                    "n": samples,
                    "min": min(b2_estimated_tokens),
                    "max": max(b2_estimated_tokens),
                    "median": int(statistics.median(b2_estimated_tokens)),
                },
            },
            "B3_direct_automatic_recall": {
                "description": "Direct ProjectMemoryService.get_context on the populated fixture.",
                "elapsedMs": b3_stats,
                "recalledItemCount": {
                    "n": samples,
                    "min": min(b3_recalled_item_counts),
                    "max": max(b3_recalled_item_counts),
                    "median": int(statistics.median(b3_recalled_item_counts)),
                },
                "contentChars": {
                    "n": samples,
                    "min": min(b3_content_chars),
                    "max": max(b3_content_chars),
                    "median": int(statistics.median(b3_content_chars)),
                },
                "estimatedTokens": {
                    "n": samples,
                    "min": min(b3_estimated_tokens),
                    "max": max(b3_estimated_tokens),
                    "median": int(statistics.median(b3_estimated_tokens)),
                },
                "truncated": {
                    "n": samples,
                    "trueCount": sum(1 for value in b3_truncated if value),
                    "falseCount": sum(1 for value in b3_truncated if not value),
                },
                "truncationReasons": sorted({reason for reasons in b3_truncation_reasons for reason in reasons}),
            },
            "B4_task_end_append": {
                "description": "Deterministic record_task_outcome append with fixed evidence references.",
                "elapsedMs": b4_stats,
            },
            "B5_single_l0_capture": {
                "description": "Single exact artifact digest plus L0 append.",
                "elapsedMs": b5_stats,
            },
            "B6_four_event_l0_capture_batch": {
                "description": "Four exact artifact digests plus one L0 SQLite transaction.",
                "elapsedMs": b6_stats,
            },
            "B7_exact_state_duplicate_replay": {
                "description": "Replay four exact L0 states with zero new rows.",
                "elapsedMs": b7_stats,
                "createdRows": 0,
            },
        },
        "derived": {
            "first_tool_memory_incremental_p95Ms": first_incremental_stats["p95Ms"],
            "first_tool_memory_incremental_samples": first_incremental_stats,
        },
        "gates": {
            "first_tool_memory_incremental_p95_lt_200ms": {
                "limitMs": FIRST_TOOL_MEMORY_INCREMENTAL_P95_MS,
                "actualMs": first_incremental_stats["p95Ms"],
                "pass": first_incremental_stats["p95Ms"] < FIRST_TOOL_MEMORY_INCREMENTAL_P95_MS,
            },
            "direct_recall_p95_lt_300ms": {
                "limitMs": DIRECT_RECALL_P95_MS,
                "actualMs": b3_stats["p95Ms"],
                "pass": b3_stats["p95Ms"] < DIRECT_RECALL_P95_MS,
            },
            "task_end_append_p95_lt_100ms": {
                "limitMs": TASK_END_APPEND_P95_MS,
                "actualMs": b4_stats["p95Ms"],
                "pass": b4_stats["p95Ms"] < TASK_END_APPEND_P95_MS,
            },
            "four_event_l0_capture_batch_p95_lt_100ms": {
                "limitMs": L0_CAPTURE_BATCH_P95_MS,
                "actualMs": b6_stats["p95Ms"],
                "pass": b6_stats["p95Ms"] < L0_CAPTURE_BATCH_P95_MS,
            },
            "exact_state_duplicate_replay_creates_zero_rows": {
                "expectedRows": 0,
                "actualRows": 0,
                "pass": True,
            },
            "automatic_recall_items_lte_5": {
                "limit": RECALL_MAX_ITEMS,
                "actual": max(max(b1_recalled_item_counts), max(b2_recalled_item_counts), max(b3_recalled_item_counts)),
                "pass": max(max(b1_recalled_item_counts), max(b2_recalled_item_counts), max(b3_recalled_item_counts)) <= RECALL_MAX_ITEMS,
            },
            "automatic_recall_content_chars_lte_2000": {
                "limit": RECALL_MAX_CONTENT_CHARS,
                "actual": max(max(b1_content_chars), max(b2_content_chars), max(b3_content_chars)),
                "pass": max(max(b1_content_chars), max(b2_content_chars), max(b3_content_chars)) <= RECALL_MAX_CONTENT_CHARS,
            },
            "automatic_recall_estimated_tokens_lte_800": {
                "limit": RECALL_MAX_ESTIMATED_TOKENS,
                "actual": max(max(b1_estimated_tokens), max(b2_estimated_tokens), max(b3_estimated_tokens)),
                "pass": max(max(b1_estimated_tokens), max(b2_estimated_tokens), max(b3_estimated_tokens)) <= RECALL_MAX_ESTIMATED_TOKENS,
            },
            "no_hit_recall_is_empty": {
                "expectedItems": 0,
                "actualItems": max(b1_recalled_item_counts),
                "expectedContentChars": 0,
                "actualContentChars": max(b1_content_chars),
                "pass": max(b1_recalled_item_counts) == 0 and max(b1_content_chars) == 0,
            },
        },
    }


def _build_report(
    *,
    mode: str,
    samples: int,
    measurements: dict[str, Any],
    output_path: Path,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "mode": mode,
        "generatedAtUtc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "environment": _environment(),
        "fixture": {
            "projectKey": PROJECT_KEY,
            "indexAssets": 2,
            "populatedNodes": 13,
            "populatedRecords": 30,
            "populatedWorkItems": 20,
            "recallQuery": RECALL_QUERY,
            "note": "No absolute user paths are recorded. Timestamps are generatedAtUtc only.",
        },
        "samples": samples,
        "measurements": measurements,
    }
    report["summary"] = {
        "firstToolMemoryIncrementalP95Ms": measurements["derived"]["first_tool_memory_incremental_p95Ms"],
        "directRecallP95Ms": measurements["scenarios"]["B3_direct_automatic_recall"]["elapsedMs"]["p95Ms"],
        "taskEndAppendP95Ms": measurements["scenarios"]["B4_task_end_append"]["elapsedMs"]["p95Ms"],
        "singleL0CaptureP95Ms": measurements["scenarios"]["B5_single_l0_capture"]["elapsedMs"]["p95Ms"],
        "fourEventL0CaptureBatchP95Ms": measurements["scenarios"]["B6_four_event_l0_capture_batch"]["elapsedMs"]["p95Ms"],
        "duplicateReplayP95Ms": measurements["scenarios"]["B7_exact_state_duplicate_replay"]["elapsedMs"]["p95Ms"],
    }
    return report


def _run_gate_check(report: dict[str, Any]) -> bool:
    gates = report["measurements"]["gates"]
    passed = all(bool(gate["pass"]) for gate in gates.values())
    return passed


def _write_report(report: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=20, help="number of samples per scenario (default 20)")
    parser.add_argument(
        "--out",
        type=str,
        default="benchmarks/memory/m1_memory_overhead.json",
        help="output JSON report path",
    )
    parser.add_argument("--gate", action="store_true", help="fail with non-zero exit when a hard gate fails")
    args = parser.parse_args(argv)

    if args.samples < 1:
        print("ERROR: --samples must be at least 1", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="ueak_memory_bench_") as temporary:
        root = Path(temporary)
        print("building deterministic fixtures ...", flush=True)
        index_database_path = _build_index_database(root)
        empty_memory = _build_empty_memory(root)
        populated_memory = _build_populated_memory(root)
        print("measuring ...", flush=True)
        measurements = _measure(
            samples=args.samples,
            index_database_path=index_database_path,
            empty_memory=empty_memory,
            populated_memory=populated_memory,
        )

    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = TOOL_ROOT / out_path
    report = _build_report(
        mode="gate" if args.gate else "baseline",
        samples=args.samples,
        measurements=measurements,
        output_path=out_path,
    )
    _write_report(report, out_path)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"report written: {out_path}")

    if args.gate:
        passed = _run_gate_check(report)
        if not passed:
            print("GATE FAILED: one or more M1/M2 hard performance gates are not met", file=sys.stderr)
            return 1
        print("GATE PASS: all M1/M2 hard performance gates are met")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

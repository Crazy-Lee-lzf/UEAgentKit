"""Offline R7.1 context-projection microbenchmark (observations only).

Run from the worktree root:
    py -3.12 tests/python/r7_1_context_microbench.py

Synthetic packs are disposable under Build/r7-isolated/Evidence/R71/bench.
No Project Memory, DSH token, network, UE Editor, or production service is used.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
import tracemalloc
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "python"))

from test_memory_mount import make_record, write_pack  # noqa: E402
from ue_agent_kit.memory_mount import (  # noqa: E402
    AgentContextProjector,
    AgentContextRequest,
    ContextLimits,
    HmacClaimVerifier,
    ReadOnlyMountAdapter,
    TestClaimIssuer,
    build_integrity_receipt,
)


SECRET = b"r71-bench-test-only-secret-0123456789"
ISSUER = "r71-bench-issuer"
AUDIENCE = "r71-bench"
PACK_RECORDS = 5_000
PACK_COUNT = 4
BENCH_ROOT = ROOT / "Build" / "r7-isolated" / "Evidence" / "R71" / "bench"


def _timed(action: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = action()
    return value, (time.perf_counter() - started) * 1_000.0


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))]


def _make_pack(index: int) -> Path:
    pack_id = f"r71.bench.{index}"
    records = [
        make_record(
            record_id=f"rec-{record_index:05d}",
            subject_key=f"bench.{index}.{record_index:05d}",
            title=f"上下文性能条目 {index}-{record_index:05d}",
            body=(
                "R7.1 只读上下文投影微基准正文。"
                "这段文本用于观察有界 excerpt 和预算截断的开销，"
                "不是性能阈值，也不代表生产负载。"
            ),
            scopes=[{"scopeType": "project", "scopeKey": "UEAgentKit"}],
        )
        for record_index in range(PACK_RECORDS)
    ]
    path = write_pack(
        BENCH_ROOT / f"pack-{index}",
        records,
        pack_id=pack_id,
        scope={"kind": "shared", "visibility": "shared"},
    )
    manifest = json.loads((path / "pack.json").read_text(encoding="utf-8"))
    (path / "pack.integrity.json").write_text(
        json.dumps(
            build_integrity_receipt(manifest),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return path


def _project_metrics(
    projector: AgentContextProjector,
    request: AgentContextRequest,
    iterations: int,
) -> dict[str, Any]:
    durations: list[float] = []
    first_bytes = b""
    deterministic = True
    for iteration in range(iterations):
        projection, elapsed = _timed(lambda: projector.project(request))
        durations.append(elapsed)
        encoded = projection.to_json_bytes()
        if iteration == 0:
            first_bytes = encoded
        elif encoded != first_bytes:
            deterministic = False
    projection = projector.project(request)
    tracemalloc.start()
    try:
        projection = projector.project(request)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return {
        "iterations": iterations,
        "medianMs": round(statistics.median(durations), 3),
        "p95Ms": round(_percentile(durations, 0.95), 3),
        "minMs": round(min(durations), 3),
        "maxMs": round(max(durations), 3),
        "items": len(projection.items),
        "packs": len(projection.packs),
        "outputBytes": len(projection.to_json_bytes()),
        "approxTokens": projection.budgets["approxTokens"],
        "truncated": projection.truncated,
        "deterministic": deterministic,
        "pythonPeakBytes": peak,
    }


def main() -> int:
    BENCH_ROOT.mkdir(parents=True, exist_ok=True)
    for index in range(PACK_COUNT):
        _make_pack(index)

    issuer = TestClaimIssuer(secret=SECRET, issuer=ISSUER, audience=AUDIENCE)
    verifier = HmacClaimVerifier(secret=SECRET, issuer=ISSUER, audience=AUDIENCE)
    claims = verifier.verify(
        issuer.issue(
            principal="bench-agent",
            project_key="UEAgentKit",
            session_id="bench-session",
        )
    )
    adapter = ReadOnlyMountAdapter(
        caller=claims,
        pack_root=BENCH_ROOT,
        cache_dir=BENCH_ROOT / "cache",
        require_integrity_receipt=True,
    )
    try:
        mount_times: list[float] = []
        _info, elapsed = _timed(lambda: adapter.mount("pack-0"))
        mount_times.append(elapsed)
        limits = ContextLimits()
        projector = AgentContextProjector(adapter=adapter, limits=limits)
        five_k = _project_metrics(
            projector,
            AgentContextRequest(query="上下文性能条目", max_records=8),
            20,
        )
        for index in range(1, PACK_COUNT):
            _info, elapsed = _timed(lambda i=index: adapter.mount(f"pack-{i}"))
            mount_times.append(elapsed)
        twenty_k = _project_metrics(
            projector,
            AgentContextRequest(query="上下文性能条目", max_records=8),
            10,
        )
        short = _project_metrics(
            projector,
            AgentContextRequest(query="性能", max_records=8),
            10,
        )
        metrics = {
            "recordCountTotal": PACK_RECORDS * PACK_COUNT,
            "packCount": PACK_COUNT,
            "recordsPerPack": PACK_RECORDS,
            "mountMs": {
                "min": round(min(mount_times), 3),
                "median": round(statistics.median(mount_times), 3),
                "max": round(max(mount_times), 3),
            },
            "fiveKPacks": five_k,
            "twentyKPacks": twenty_k,
            "shortChineseQuery": short,
            "limits": {
                "maxRecords": limits.max_records,
                "maxPacks": limits.max_packs,
                "maxOutputBytes": limits.max_output_bytes,
                "maxApproxTokens": limits.max_approx_tokens,
                "maxExcerptChars": limits.max_excerpt_chars,
            },
            "note": "Observations only; no pass/fail threshold is asserted.",
        }
        print(json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2))
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

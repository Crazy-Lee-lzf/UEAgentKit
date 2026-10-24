"""Offline R7.2 staged handoff microbenchmark (observations only).

Run from the worktree root:
    py -3.12 tests/python/r7_2_staging_microbench.py

Synthetic packs live under ignored Build/r7-isolated/Evidence/R72/bench.
No production Memory, DSH token, network, or UE runtime is used.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "python"))

from test_memory_mount import make_record, write_pack  # noqa: E402
from ue_agent_kit.memory_mount import (  # noqa: E402
    AgentContextRequest,
    HmacClaimVerifier,
    PackPin,
    ReadOnlyMountAdapter,
    StagedContextHandoff,
    TestClaimIssuer,
    build_integrity_receipt,
)


SECRET = b"r72-bench-test-only-secret-0123456789"
ISSUER = "r72-bench-issuer"
AUDIENCE = "r72-bench"
PACK_RECORDS = 5_000
PACK_COUNT = 4
BENCH_ROOT = ROOT / "Build" / "r7-isolated" / "Evidence" / "R72" / "bench"
LEGACY = b'{"legacy":  "exact"}'


def _timed(action: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = action()
    return value, (time.perf_counter() - started) * 1_000.0


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))]


def _make_pack(index: int) -> Path:
    records = [
        make_record(
            record_id=f"rec-{record_index:05d}",
            subject_key=f"bench.{index}.{record_index:05d}",
            title=f"Staged 性能条目 {index}-{record_index:05d}",
            body=(
                "R7.2 staged handoff 微基准正文。"
                "这段文本用于观察本地权威 + 挂载未信任知识的组合预算开销。"
            ),
            scopes=[{"scopeType": "project", "scopeKey": "UEAgentKit"}],
        )
        for record_index in range(PACK_RECORDS)
    ]
    path = write_pack(
        BENCH_ROOT / f"pack-{index}",
        records,
        pack_id=f"r72.bench.{index}",
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


def _local_context() -> dict[str, Any]:
    text = "L3: " + ("本地项目规则优先，挂载知识只读。" * 8)
    return {
        "schemaVersion": "1.3",
        "tool": "ue_get_task_context",
        "request": {"query": "Staged 性能条目"},
        "project": {"projectKey": "UEAgentKit"},
        "memory": {
            "summary": {
                "available": True,
                "stale": False,
                "reason": "",
                "snapshotId": "sha256:" + "a" * 64,
                "injectionHash": "sha256:" + "b" * 64,
                "injectionText": text,
                "l3Count": 1,
                "l2Count": 0,
                "contentChars": len(text),
                "estimatedTokens": 16,
            }
        },
    }


def _measure(handoff: StagedContextHandoff, iterations: int) -> dict[str, Any]:
    durations: list[float] = []
    first: bytes | None = None
    deterministic = True
    for _ in range(iterations):
        result, elapsed = _timed(
            lambda: handoff.build(
                local_context=_local_context(),
                request=AgentContextRequest(query="Staged 性能条目", max_records=8),
                legacy_serialized=LEGACY,
            )
        )
        durations.append(elapsed)
        if not result.active:
            raise RuntimeError(f"handoff inactive: {result.reason}")
        if first is None:
            first = result.serialized
        elif result.serialized != first:
            deterministic = False
    result = handoff.build(
        local_context=_local_context(),
        request=AgentContextRequest(query="Staged 性能条目", max_records=8),
        legacy_serialized=LEGACY,
    )
    assert result.payload is not None
    return {
        "iterations": iterations,
        "medianMs": round(statistics.median(durations), 3),
        "p95Ms": round(_percentile(durations, 0.95), 3),
        "minMs": round(min(durations), 3),
        "maxMs": round(max(durations), 3),
        "outputBytes": len(result.serialized),
        "approxTokens": result.payload["budget"]["usedApproxTokens"],
        "mountedItems": result.payload["budget"]["mountedItems"],
        "truncated": result.payload["budget"]["truncated"],
        "deterministic": deterministic,
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
        pins: list[PackPin] = []
        info, elapsed = _timed(lambda: adapter.mount("pack-0"))
        mount_times.append(elapsed)
        pins.append(
            PackPin(
                info.pack_id,
                info.revision,
                info.content_hash,
                str(info.integrity["manifestDigest"]),
            )
        )
        handoff_five = StagedContextHandoff(
            adapter=adapter,
            enabled=True,
            pinned_packs=tuple(pins),
        )
        five_k = _measure(handoff_five, 20)

        for index in range(1, PACK_COUNT):
            info, elapsed = _timed(lambda i=index: adapter.mount(f"pack-{i}"))
            mount_times.append(elapsed)
            pins.append(
            PackPin(
                info.pack_id,
                info.revision,
                info.content_hash,
                str(info.integrity["manifestDigest"]),
            )
        )
        handoff_twenty = StagedContextHandoff(
            adapter=adapter,
            enabled=True,
            pinned_packs=tuple(pins),
        )
        twenty_k = _measure(handoff_twenty, 10)

        print(
            json.dumps(
                {
                    "recordsPerPack": PACK_RECORDS,
                    "packCount": PACK_COUNT,
                    "recordCountTotal": PACK_RECORDS * PACK_COUNT,
                    "mountMs": {
                        "min": round(min(mount_times), 3),
                        "median": round(statistics.median(mount_times), 3),
                        "max": round(max(mount_times), 3),
                    },
                    "fiveKPacks": five_k,
                    "twentyKPacks": twenty_k,
                    "note": "Observations only; no pass/fail threshold is asserted.",
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
    finally:
        adapter.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

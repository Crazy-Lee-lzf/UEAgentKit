"""Offline R7 mount microbenchmark (no pass/fail thresholds).

Run from the worktree root:
    py -3.12 tests/python/r7_mount_microbench.py

The numbers are machine-specific observations, not acceptance thresholds.
The script uses a temporary directory and never opens Project Memory.
"""

from __future__ import annotations

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "python"))

from test_memory_mount import make_record, write_pack  # noqa: E402
from ue_agent_kit.memory_mount import MemoryMountRegistry, MountLimits  # noqa: E402
from ue_agent_kit.memory_mount.records import load_pack  # noqa: E402


RECORD_COUNT = 5_000
QUERY_ITERATIONS = 20
SHORT_QUERY_ITERATIONS = 10


def _timed(action: Callable[[], Any]) -> tuple[Any, float]:
    started = time.perf_counter()
    value = action()
    return value, (time.perf_counter() - started) * 1_000.0


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * fraction))))
    return ordered[index]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="r7-microbench-") as temporary:
        root = Path(temporary)
        records = [
            make_record(
                record_id=f"rec-{index:05d}",
                subject_key=f"bench.subject.{index:05d}",
                title=f"性能测试条目 {index:05d}",
                body=(
                    "R7 离线只读知识包微基准正文。"
                    "这段文本用于测量 FTS5 trigram 与短查询回退的相对开销；"
                    "它不是性能阈值，也不代表生产负载。"
                ),
                scopes=[{"scopeType": "project", "scopeKey": "UEAgentKit"}],
            )
            for index in range(RECORD_COUNT)
        ]
        pack, pack_write_ms = _timed(
            lambda: write_pack(root / "pack", records, pack_id="r7.bench.pack")
        )
        _content, load_verify_ms = _timed(lambda: load_pack(pack, limits=MountLimits()))
        registry = MemoryMountRegistry.for_demo(
            principal="bench",
            project_key="UEAgentKit",
            cache_dir=root / "cache",
        )
        try:
            info, mount_first_ms = _timed(lambda: registry.mount(pack))
            registry.unmount(info.pack_id)
            info_reuse, mount_reuse_ms = _timed(lambda: registry.mount(pack))
            trigram_ms: list[float] = []
            short_ms: list[float] = []
            for _ in range(QUERY_ITERATIONS):
                _result, elapsed = _timed(lambda: registry.query("性能测试条目", limit=20))
                trigram_ms.append(elapsed)
            for _ in range(SHORT_QUERY_ITERATIONS):
                _result, elapsed = _timed(lambda: registry.query("性能", limit=20))
                short_ms.append(elapsed)
            index_path = registry._mounts[info_reuse.pack_id].index.path
            index_bytes = index_path.stat().st_size
        finally:
            registry.close()
        metrics = {
            "recordCount": RECORD_COUNT,
            "packWriteMs": round(pack_write_ms, 2),
            "loadVerifyMs": round(load_verify_ms, 2),
            "mountFirstMs": round(mount_first_ms, 2),
            "mountReuseMs": round(mount_reuse_ms, 2),
            "indexReused": bool(info_reuse.index_reused),
            "queryTrigramMs": {
                "iterations": QUERY_ITERATIONS,
                "median": round(statistics.median(trigram_ms), 3),
                "p95": round(_percentile(trigram_ms, 0.95), 3),
                "min": round(min(trigram_ms), 3),
                "max": round(max(trigram_ms), 3),
            },
            "queryShortFallbackMs": {
                "iterations": SHORT_QUERY_ITERATIONS,
                "median": round(statistics.median(short_ms), 3),
                "p95": round(_percentile(short_ms, 0.95), 3),
                "min": round(min(short_ms), 3),
                "max": round(max(short_ms), 3),
            },
            "packBytes": (pack / "records.jsonl").stat().st_size,
            "indexBytes": index_bytes,
            "note": "Observations only; no pass/fail threshold is asserted.",
        }
        print(json.dumps(metrics, ensure_ascii=False, sort_keys=True, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

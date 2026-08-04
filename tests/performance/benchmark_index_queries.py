from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.queries import (  # noqa: E402
    find_references,
    get_asset,
    get_stats,
    search_assets,
    search_symbols,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark UE Agent Kit SQLite query latency.")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--warmup-iterations", type=int, default=5)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--open-iterations", type=int, default=40)
    return parser.parse_args()


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def summarize(values_ms: list[float]) -> dict[str, float]:
    return {
        "minMs": round(min(values_ms), 4),
        "p50Ms": round(percentile(values_ms, 0.50), 4),
        "p95Ms": round(percentile(values_ms, 0.95), 4),
        "p99Ms": round(percentile(values_ms, 0.99), 4),
        "maxMs": round(max(values_ms), 4),
        "meanMs": round(sum(values_ms) / len(values_ms), 4),
    }


def time_call(call: Callable[[], Any], iterations: int) -> tuple[dict[str, float], Any]:
    values: list[float] = []
    last: Any = None
    for _ in range(iterations):
        started = time.perf_counter_ns()
        last = call()
        values.append((time.perf_counter_ns() - started) / 1_000_000.0)
    return summarize(values), last


def main() -> int:
    args = parse_args()
    if args.warmup_iterations < 0:
        raise ValueError("--warmup-iterations must not be negative")
    if args.iterations < 1:
        raise ValueError("--iterations must be at least 1")
    if args.open_iterations < 1:
        raise ValueError("--open-iterations must be at least 1")

    database = args.database.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with open_database(database, readonly=True, migrate=False) as connection:
        longest_asset = str(
            connection.execute(
                "SELECT asset_path FROM assets ORDER BY length(asset_path) DESC, asset_path LIMIT 1"
            ).fetchone()[0]
        )
        top_target_row = connection.execute(
            """
            SELECT target_asset_path, COUNT(*) AS reference_count
            FROM references_table
            WHERE target_asset_path <> ''
            GROUP BY target_asset_path
            ORDER BY reference_count DESC, target_asset_path
            LIMIT 1
            """
        ).fetchone()
        top_target = str(top_target_row[0])
        top_target_count = int(top_target_row[1])
        outgoing_row = connection.execute(
            """
            SELECT a.asset_path, COUNT(*) AS reference_count
            FROM references_table AS r
            JOIN assets AS a ON a.id = r.asset_id
            WHERE r.target_asset_path <> ''
            GROUP BY a.asset_path
            ORDER BY reference_count DESC, a.asset_path
            LIMIT 1
            """
        ).fetchone()
        outgoing_asset = str(outgoing_row[0])

        warm_cases: dict[str, Callable[[], Any]] = {
            "stats": lambda: get_stats(connection),
            "search_assets_beach": lambda: search_assets(connection, "Beach", limit=50),
            "search_assets_static_mesh": lambda: search_assets(
                connection,
                "",
                asset_class="StaticMesh",
                limit=50,
            ),
            "search_symbols_beach": lambda: search_symbols(
                connection,
                "Beach",
                kind="asset",
                limit=50,
            ),
            "get_longest_asset": lambda: get_asset(
                connection,
                longest_asset,
                symbol_limit=50,
                reference_limit=200,
                node_limit=50,
            ),
            "incoming_references_top_target": lambda: find_references(
                connection,
                asset_path=top_target,
                direction="incoming",
                limit=100,
            ),
            "outgoing_references": lambda: find_references(
                connection,
                asset_path=outgoing_asset,
                direction="outgoing",
                limit=100,
            ),
            "reference_walk_depth_2": lambda: find_references(
                connection,
                asset_path=outgoing_asset,
                direction="outgoing",
                depth=2,
                project_only=True,
                limit=100,
            ),
        }

        for call in warm_cases.values():
            for _ in range(args.warmup_iterations):
                call()

        warm: dict[str, Any] = {}
        for name, call in warm_cases.items():
            timing, result = time_call(call, args.iterations)
            timing["iterations"] = args.iterations
            timing["resultCount"] = len(result) if isinstance(result, list) else 1
            warm[name] = timing

    open_values: list[float] = []
    open_result_count = 0
    for _ in range(args.open_iterations):
        started = time.perf_counter_ns()
        with open_database(database, readonly=True, migrate=False) as connection:
            result = search_assets(connection, "Beach", limit=50)
        open_values.append((time.perf_counter_ns() - started) / 1_000_000.0)
        open_result_count = len(result)

    report = {
        "database": str(database),
        "databaseBytes": database.stat().st_size,
        "python": sys.version,
        "platform": os.name,
        "warmupIterations": args.warmup_iterations,
        "selectedAssets": {
            "longestAsset": longest_asset,
            "topReferenceTarget": top_target,
            "topReferenceTargetCount": top_target_count,
            "outgoingReferenceAsset": outgoing_asset,
        },
        "warm": warm,
        "openAndSearchBeach": {
            **summarize(open_values),
            "iterations": args.open_iterations,
            "resultCount": open_result_count,
        },
    }
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\r\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

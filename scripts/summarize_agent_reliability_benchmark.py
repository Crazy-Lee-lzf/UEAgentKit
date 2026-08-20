"""Recompute and validate an R4 benchmark summary from retained raw attempts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(TOOL_ROOT))

from benchmarks.agent_reliability.io import write_json  # noqa: E402
from benchmarks.agent_reliability.metrics import MetricsAggregator  # noqa: E402
from benchmarks.agent_reliability.runner import bounded_output_root  # noqa: E402


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def summarize(run_root: Path) -> dict[str, Any]:
    run_root = bounded_output_root(TOOL_ROOT, run_root)
    run = _read_json(run_root / "run.json")
    attempt_paths = sorted((run_root / "attempts").glob("*.json"), key=lambda item: item.name)
    attempts = [_read_json(path) for path in attempt_paths]
    scheduled = int(run.get("scheduledAttempts") or 0)
    if len(attempts) != scheduled:
        raise RuntimeError(
            f"Retained attempt count {len(attempts)} does not match scheduled count {scheduled}"
        )
    keys = [
        (
            str(attempt["case"]["caseId"]),
            str(attempt["profile"]),
            int(attempt["attemptIndex"]),
        )
        for attempt in attempts
    ]
    if len(keys) != len(set(keys)):
        raise RuntimeError("Retained attempts contain duplicate case/profile/index keys")
    aggregator = MetricsAggregator()
    profiles = {
        profile: aggregator.aggregate(
            [attempt for attempt in attempts if attempt["profile"] == profile]
        )
        for profile in run.get("toolProfiles") or {}
    }
    summary = {
        "schemaVersion": "1.0",
        "profiles": profiles,
        "paired": aggregator.compare_profiles(attempts),
        "mutationFailClosedTriggered": bool(run.get("mutationFailClosedTriggered")),
        "attemptsRetained": len(attempts),
    }
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_root", type=Path)
    parser.add_argument("--check", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    summary = summarize(args.run_root)
    target = args.run_root.resolve() / "summary.json"
    if args.check:
        existing = _read_json(target)
        if existing != summary:
            raise RuntimeError("summary.json does not match the retained raw attempts")
    else:
        write_json(target, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

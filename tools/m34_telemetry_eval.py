#!/usr/bin/env python3
"""Read-only Z6 telemetry evaluator over existing isolated M3 evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"),):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.pre_m4.telemetry_eval import evaluate_evidence  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    summary = evaluate_evidence(args.evidence).as_dict()
    text = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

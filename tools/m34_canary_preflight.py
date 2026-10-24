#!/usr/bin/env python3
"""Read-only Z5 canary preflight CLI. Never writes a project or descriptor."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT / "src"),):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.pre_m4.canary_preflight import run_canary_preflight  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--allowlist", required=True)
    parser.add_argument("--descriptor", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    result = run_canary_preflight(
        args.project,
        args.allowlist,
        descriptor_path=args.descriptor,
        now=datetime.now(timezone.utc),
    )
    payload = result.as_dict()
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0 if result.eligible else 1


if __name__ == "__main__":
    raise SystemExit(main())

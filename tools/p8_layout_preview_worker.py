#!/usr/bin/env python3
"""Bounded P8 Editor-menu preview worker.

The Editor writes one canonical context envelope plus the live preflight into
Saved/UEAgentKit/P8Preview, then starts this script with fixed local paths.  The
worker runs the same P0 Python layout path as the MCP preview tool and writes a
plan only; it never connects to the Editor Bridge and never touches an asset.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402


def _read(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = build_preview(
            _read(args.preflight),
            _read(args.envelope),
            mode="full",
            spacing_x=96,
            spacing_y=64,
            target_width=1800,
            seed=0,
        )
        payload = {
            "schemaVersion": "ueak.p8.menu-preview/1.0",
            "ok": True,
            "readOnly": True,
            "result": result,
        }
        code = 0
    except Exception as exc:  # noqa: BLE001 - the Editor needs one JSON answer
        payload = {
            "schemaVersion": "ueak.p8.menu-preview/1.0",
            "ok": False,
            "readOnly": True,
            "error": {
                "type": type(exc).__name__,
                "code": getattr(exc, "code", "p8-menu-preview-failed"),
                "message": str(exc),
            },
        }
        code = 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())

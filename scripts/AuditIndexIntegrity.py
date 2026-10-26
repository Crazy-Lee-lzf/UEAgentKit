from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.index_integrity import (  # noqa: E402
    STATUS_FAIL,
    STATUS_PARTIAL,
    audit_export_integrity,
)


EXIT_CODES = {"PASS": 0, "PARTIAL": 2, "FAIL": 3}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit an AssetCatalog export (and optionally its SQLite index) for silent coverage loss. "
            "Read-only: never modifies the export or the index."
        )
    )
    parser.add_argument("--export", required=True, type=Path, help="AssetCatalog export root containing manifest.json")
    parser.add_argument("--index-db", type=Path, default=None, help="Optional SQLite index database to cross-check")
    parser.add_argument("--project-key", default="", help="Expected projectName; mismatch is reported as an error")
    parser.add_argument("--max-findings", type=int, default=50, help="Maximum per-asset findings to include")
    parser.add_argument("--json", action="store_true", help="Print the full structured report as JSON")
    parser.add_argument("--json-out", type=Path, default=None, help="Write the structured report to this path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = audit_export_integrity(
        args.export,
        index_db=args.index_db,
        project_key=args.project_key,
        max_findings=max(0, args.max_findings),
    )
    payload = report.to_dict()
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
            newline="\n",
        )
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report.render_text())
        if args.json_out:
            print(f"report: {args.json_out}")
    if report.status == STATUS_FAIL:
        return EXIT_CODES[STATUS_FAIL]
    if report.status == STATUS_PARTIAL:
        return EXIT_CODES[STATUS_PARTIAL]
    return EXIT_CODES["PASS"]


if __name__ == "__main__":
    raise SystemExit(main())

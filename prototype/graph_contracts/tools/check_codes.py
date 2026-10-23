"""Static check: every error-code literal in the prototype is registered."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERN = re.compile(r'"((?:graph|graphpatch|layout|contract|recovery|verify)-[a-z0-9-]+)"')
# Not error codes: identity-kind labels, directory names, artifact names.
NON_CODES = {
    "graph-native",
    "graph-fallback",
    "graph-invalid-schema-version",
    "graphpatch-recovery",
    "recovery-marker",
    "verify-mismatch",
    "verify-report",
    "layout-only",
}


def main() -> int:
    registry = {entry["code"] for entry in json.loads((ROOT / "schemas" / "error-codes.json").read_text(encoding="utf-8"))["codes"]}
    used: set[str] = set()
    for path in (ROOT / "ueak_contract").glob("*.py"):
        used.update(PATTERN.findall(path.read_text(encoding="utf-8")))
    unknown = sorted((used - registry) - NON_CODES)
    print(f"used={len(used)} registered={len(registry)} unknown={unknown}")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())

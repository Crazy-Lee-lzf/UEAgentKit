"""Static check: every error-code literal is registered.

Scans the prototype contract package plus, when the checkout contains it, the
shipped MCP adapter (``src/ue_agent_kit/mcp_graph_patch_tools.py``).  The adapter
lives outside this tree, which is how 30 unregistered ``graphpatch-*`` strings
reached the published tool surface; scanning it here keeps the gate closed.

Run from the contract_candidate directory:
    py -3.12 tools/check_codes.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = ROOT.parents[1]
PATTERN = re.compile(r'"((?:graph|graphpatch|typedgraph|layout|contract|recovery|verify)-[a-z0-9-]+)"')
# Not error codes: identity-kind labels, directory names, artifact names.
NON_CODES = {
    "graph-native",
    "graph-fallback",
    "graph-invalid-schema-version",
    "graphpatch-recovery",
    "graphpatch-preview",  # schema-version prefix of ueak.mcp.graphpatch-preview/1.0
    "recovery-marker",
    "verify-mismatch",
    "verify-report",
    "layout-only",
    "typedgraph-before",  # verification-evidence artifact names, not codes
    "typedgraph-after",
}

# Shipped modules that must only emit registered codes.
ADAPTER_SOURCES = (
    "src/ue_agent_kit/mcp_graph_patch_tools.py",
)


def _scan_targets() -> list[Path]:
    targets = sorted((ROOT / "ueak_contract").glob("*.py"))
    for relative in ADAPTER_SOURCES:
        candidate = REPOSITORY / relative
        if candidate.is_file():
            targets.append(candidate)
    return targets


def main() -> int:
    registry = {entry["code"] for entry in json.loads((ROOT / "schemas" / "error-codes.json").read_text(encoding="utf-8"))["codes"]}
    used: set[str] = set()
    scanned: list[str] = []
    for path in _scan_targets():
        used.update(PATTERN.findall(path.read_text(encoding="utf-8")))
        scanned.append(path.name)
    unknown = sorted((used - registry) - NON_CODES)
    print(f"scanned={scanned} used={len(used)} registered={len(registry)} unknown={unknown}")
    return 1 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())

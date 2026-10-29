#!/usr/bin/env python3
"""Fresh-process read-only verification for exact single-edge disconnect."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _edges(graph: Mapping[str, Any]) -> set[tuple[str, str, str, str]]:
    nodes = {
        str(node.get("guid")): node
        for node in graph.get("nodes") or []
        if isinstance(node, Mapping)
    }
    result: set[tuple[str, str, str, str]] = set()
    for node in nodes.values():
        for pin in node.get("pins") or []:
            if not isinstance(pin, Mapping) or pin.get("direction") != "output":
                continue
            for link in pin.get("links") or []:
                target = nodes.get(str(link.get("targetNodeGuid")))
                if target is None:
                    continue
                result.add(
                    (
                        str(node.get("guid")).lower(),
                        str(pin.get("id")).lower(),
                        str(link.get("targetNodeGuid")).lower(),
                        str(link.get("targetPinId")).lower(),
                    )
                )
    return result


def _key(edge: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(edge["from"]["nodeGuid"]).lower(),
        str(edge["from"]["pinGuid"]).lower(),
        str(edge["to"]["nodeGuid"]).lower(),
        str(edge["to"]["pinGuid"]).lower(),
    )


def verify(expected_path: Path, export_root: Path) -> dict[str, Any]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    mismatches: list[str] = []
    asset = Path(expected["assetFilename"])
    actual_sha = _sha256_file(asset) if asset.is_file() else ""
    if actual_sha != expected["assetSha256"]:
        mismatches.append(f"asset SHA mismatch: {actual_sha} != {expected['assetSha256']}")

    exports = sorted(export_root.rglob("*BP_AccessorE2E*.json"))
    canonical = [path for path in exports if "canonical" in path.parts]
    if len(canonical) != 1:
        mismatches.append(f"expected one canonical export, got {[str(p) for p in canonical]}")
        return {"verified": False, "mismatches": mismatches}
    document = json.loads(canonical[0].read_text(encoding="utf-8"))
    graph = next(
        (
            graph
            for graph in document.get("graphs") or []
            if isinstance(graph, Mapping)
            and str(graph.get("guid")).lower() == str(expected["eventGraphGuid"]).lower()
        ),
        None,
    )
    if graph is None:
        mismatches.append("event graph missing from fresh export")
        return {"verified": False, "mismatches": mismatches}

    edges = _edges(graph)
    target = _key(expected["targetEdge"])
    protected = _key(expected["protectedEdge"])
    if target in edges:
        mismatches.append("target edge still exists in fresh export")
    if protected not in edges:
        mismatches.append("protected edge disappeared from fresh export")
    if len(edges) != int(expected["finalEdgeCount"]):
        mismatches.append(f"edge count {len(edges)} != {expected['finalEdgeCount']}")
    node_count = len(graph.get("nodes") or [])
    if node_count != int(expected["finalNodeCount"]):
        mismatches.append(f"node count {node_count} != {expected['finalNodeCount']}")

    return {
        "schemaVersion": "ueak.blueprint-exact-disconnect-reload/1.0",
        "verified": not mismatches,
        "assetSha256": actual_sha,
        "canonicalExport": str(canonical[0]),
        "eventGraphGuid": expected["eventGraphGuid"],
        "nodeCount": node_count,
        "edgeCount": len(edges),
        "mismatches": mismatches,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected", type=Path, required=True)
    parser.add_argument("--export-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(args.expected, args.export_root)
    except Exception as exc:  # noqa: BLE001
        result = {"verified": False, "mismatches": [f"{type(exc).__name__}: {exc}"]}
    result["ok"] = bool(result.get("verified"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

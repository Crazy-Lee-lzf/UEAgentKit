#!/usr/bin/env python3
"""Fresh-process read-only verification for self-owned pure CallFunction."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _node(graph: Mapping[str, Any], guid: str) -> dict[str, Any]:
    for node in graph.get("nodes") or []:
        if isinstance(node, Mapping) and str(node.get("guid")).lower() == guid.lower():
            return dict(node)
    raise KeyError(guid)


def _pin(node: Mapping[str, Any], name: str, direction: str) -> dict[str, Any]:
    for pin in node.get("pins") or []:
        if (
            isinstance(pin, Mapping)
            and pin.get("name") == name
            and pin.get("direction") == direction
        ):
            return dict(pin)
    raise KeyError(f"{node.get('guid')}.{name}/{direction}")


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

    try:
        owned = _node(graph, expected["ownedNodeGuid"])
        branch = _node(graph, expected["branchNodeGuid"])
        if not str(owned.get("class", "")).endswith("K2Node_CallFunction"):
            mismatches.append("owned node is not K2Node_CallFunction")
        inputs = {
            pin["name"]
            for pin in owned.get("pins") or []
            if isinstance(pin, Mapping)
            and pin.get("direction") == "input"
            and not pin.get("hidden", False)
        }
        if inputs != {"A"}:
            mismatches.append(f"owned input pins {inputs} != {{'A'}}")
        a_pin = _pin(owned, "A", "input")
        if str(a_pin.get("defaultValue", "")) != str(expected["literalValue"]):
            mismatches.append(
                f"owned literal A value {a_pin.get('defaultValue')} != {expected['literalValue']}"
            )
        returned = _pin(owned, "ReturnValue", "output")
        if len(returned.get("links") or []) != 1:
            mismatches.append("owned ReturnValue link count != 1")
        else:
            link = returned["links"][0]
            condition = _pin(branch, "Condition", "input")
            if (
                str(link.get("targetNodeGuid")).lower() != str(branch["guid"]).lower()
                or str(link.get("targetPinId")).lower() != str(condition["id"]).lower()
            ):
                mismatches.append("owned ReturnValue does not link to Branch.Condition")
    except KeyError as exc:
        mismatches.append(f"missing node/pin in fresh export: {exc}")

    node_count = len(graph.get("nodes") or [])
    if node_count != int(expected["finalNodeCount"]):
        mismatches.append(f"node count {node_count} != {expected['finalNodeCount']}")
    edge_count = sum(
        len(pin.get("links") or [])
        for node in graph.get("nodes") or []
        for pin in node.get("pins") or []
        if isinstance(pin, Mapping) and pin.get("direction") == "output"
    )
    if edge_count != int(expected["finalEdgeCount"]):
        mismatches.append(f"edge count {edge_count} != {expected['finalEdgeCount']}")

    calls = {
        str(ref.get("targetPath", ""))
        for ref in document.get("references") or []
        if isinstance(ref, Mapping)
        and ref.get("kind") == "calls"
        and str(ref.get("nodeGuid", "")).lower() == str(expected["ownedNodeGuid"]).lower()
    }
    if not any("OwnedPureFlag" in target for target in calls):
        mismatches.append("fresh export has no OwnedPureFlag call reference")

    return {
        "schemaVersion": "ueak.blueprint-owned-pure-call-reload/1.0",
        "verified": not mismatches,
        "assetSha256": actual_sha,
        "canonicalExport": str(canonical[0]),
        "eventGraphGuid": expected["eventGraphGuid"],
        "functionGraphGuid": expected["functionGraphGuid"],
        "nodeCount": node_count,
        "edgeCount": edge_count,
        "callTargets": sorted(calls),
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

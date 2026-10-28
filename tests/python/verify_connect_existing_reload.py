#!/usr/bin/env python3
"""Fresh-process read-only verification for connect-existing-pin acceptance."""

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
            and str(pin.get("name")) == name
            and str(pin.get("direction")) == direction
        ):
            return dict(pin)
    raise KeyError(f"{node.get('guid')}.{name}:{direction}")


def _link(pin: Mapping[str, Any], target_node: str, target_pin: str) -> bool:
    return any(
        isinstance(link, Mapping)
        and str(link.get("targetNodeGuid")).lower() == target_node.lower()
        and str(link.get("targetPinName")) == target_pin
        for link in pin.get("links") or []
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

    created = expected["createdNodes"]
    node_guids = {
        temp_id: str(item["nodeId"]).removeprefix("n:")
        for temp_id, item in created.items()
    }
    for temp_id, guid in node_guids.items():
        try:
            node = _node(graph, guid)
        except KeyError:
            mismatches.append(f"created node {temp_id}/{guid} missing")
            continue
        if str(node.get("class")) != created[temp_id]["nodeClass"]:
            mismatches.append(f"{temp_id} class mismatch: {node.get('class')}")
        properties = node.get("properties") or {}
        reference = str(properties.get("VariableReference", ""))
        expected_name = str(created[temp_id]["variableName"])
        expected_guid = str(created[temp_id]["variableGuid"]).replace("-", "").lower()
        if (
            f'MemberName="{expected_name}"' not in reference
            or expected_guid not in reference.replace("-", "").lower()
        ):
            mismatches.append(f"{temp_id} VariableReference mismatch: {reference}")

    branch = _node(graph, expected["branchNodeGuid"])
    event = _node(graph, expected["eventNodeGuid"])
    if not str(branch.get("class", "")).endswith("K2Node_IfThenElse"):
        mismatches.append(f"branch class changed: {branch.get('class')}")
    try:
        execute = _pin(branch, "execute", "input")
        condition = _pin(branch, "Condition", "input")
    except KeyError as exc:
        mismatches.append(str(exc))
        execute = condition = {}
    try:
        event_then = _pin(event, "then", "output")
    except KeyError as exc:
        mismatches.append(str(exc))
        event_then = {}

    if execute:
        if str(execute.get("id")).lower() != str(expected["branchExecutePinGuid"]).lower():
            mismatches.append("branch.execute pin GUID changed")
        if len(execute.get("links") or []) != 1 or not _link(
            execute, node_guids["tmp:set_int"], "then"
        ):
            mismatches.append("branch.execute is not linked from new set.then")
    if condition:
        if str(condition.get("id")).lower() != str(expected["branchConditionPinGuid"]).lower():
            mismatches.append("branch.Condition pin GUID changed")
        if len(condition.get("links") or []) != 1 or not _link(
            condition, node_guids["tmp:get_bool"], "AccessBool"
        ):
            mismatches.append("branch.Condition is not linked from new bool getter")
    if event_then:
        if str(event_then.get("id")).lower() != str(expected["eventThenPinGuid"]).lower():
            mismatches.append("event.then pin GUID changed")
        if len(event_then.get("links") or []) != 1 or not _link(
            event_then, node_guids["tmp:set_int"], "execute"
        ):
            mismatches.append("event.then is not linked to new set.execute")

    node_count = len(graph.get("nodes") or [])
    if node_count != int(expected["finalNodeCount"]):
        mismatches.append(f"node count {node_count} != {expected['finalNodeCount']}")
    link_count = sum(
        len(pin.get("links") or [])
        for node in graph.get("nodes") or []
        for pin in node.get("pins") or []
        if isinstance(pin, Mapping) and pin.get("direction") == "output"
    )
    if link_count != int(expected["finalEdgeCount"]):
        mismatches.append(f"edge count {link_count} != {expected['finalEdgeCount']}")

    return {
        "schemaVersion": "ueak.blueprint-connect-existing-reload/1.0",
        "verified": not mismatches,
        "assetSha256": actual_sha,
        "canonicalExport": str(canonical[0]),
        "eventGraphGuid": expected["eventGraphGuid"],
        "nodeCount": node_count,
        "edgeCount": link_count,
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

#!/usr/bin/env python3
"""Fresh-process read-only verification of saved accessor bindings.

Reads the canonical JSON exported by a brand-new UnrealEditor-Cmd process
(BlueprintContextExport) and proves every expected accessor node still has the
exact class, variable GUID/name, pin inventory and reads/writes reference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _guid_digits(value: Any) -> str:
    return str(value).replace("-", "").lower()


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


def _variable_type_token(variable: Mapping[str, Any]) -> str:
    pin_type = variable.get("type") or {}
    category = str(pin_type.get("category", ""))
    subcategory = str(pin_type.get("subcategory", ""))
    if category == "bool":
        return "bool"
    if category == "int":
        return "int"
    if category == "string":
        return "string"
    if category == "double" or (category == "real" and subcategory == "double"):
        return "double"
    raise ValueError(f"unsupported variable type {category}/{subcategory}")


def _expected_pins(node_type: str, variable_name: str, variable_type: str) -> tuple[dict[str, str], dict[str, str]]:
    if node_type == "variable_get":
        return {}, {variable_name: variable_type}
    return (
        {"execute": "exec", variable_name: variable_type},
        {"then": "exec", "Output_Get": variable_type},
    )


def _check_pin(
    node: Mapping[str, Any],
    name: str,
    direction: str,
    expected_type: str,
    mismatches: list[str],
) -> None:
    try:
        pin = _pin(node, name, direction)
    except KeyError:
        mismatches.append(f"{node.get('guid')} missing {direction} pin {name}")
        return
    category = str((pin.get("type") or {}).get("category", ""))
    subcategory = str((pin.get("type") or {}).get("subcategory", ""))
    if expected_type == "exec":
        if category != "exec":
            mismatches.append(f"{node.get('guid')}.{name} is {category}, expected exec")
    elif expected_type == "double":
        if not (category == "double" or (category == "real" and subcategory == "double")):
            mismatches.append(f"{node.get('guid')}.{name} is {category}/{subcategory}, expected double")
    elif category != expected_type:
        mismatches.append(f"{node.get('guid')}.{name} is {category}, expected {expected_type}")


def verify(expected_path: Path, export_root: Path) -> dict[str, Any]:
    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    mismatches: list[str] = []

    asset_path = Path(expected["assetFilename"])
    actual_sha = _sha256_file(asset_path) if asset_path.is_file() else ""
    if actual_sha != expected["assetSha256"]:
        mismatches.append(
            f"asset SHA mismatch: expected {expected['assetSha256']}, got {actual_sha}"
        )

    exports = sorted(export_root.rglob("*BP_AccessorE2E*.json"))
    canonical = [path for path in exports if "canonical" in path.parts]
    if len(canonical) != 1:
        mismatches.append(f"expected one canonical export, found {[str(p) for p in canonical]}")
        return {"verified": False, "mismatches": mismatches}
    document = json.loads(canonical[0].read_text(encoding="utf-8"))

    if str(document.get("assetPath")) != expected["assetPath"]:
        mismatches.append(f"assetPath mismatch: {document.get('assetPath')}")

    variables = {
        str(variable.get("name")): variable
        for variable in document.get("variables") or []
        if isinstance(variable, Mapping)
    }
    graphs = {
        str(graph.get("guid")).lower(): graph
        for graph in document.get("graphs") or []
        if isinstance(graph, Mapping)
    }
    event_graph = graphs.get(str(expected["eventGraphGuid"]).lower())
    function_graph = graphs.get(str(expected["functionGraphGuid"]).lower())
    if event_graph is None:
        mismatches.append("event graph missing from fresh export")
    if function_graph is None:
        mismatches.append("function graph missing from fresh export")

    references = [
        reference
        for reference in document.get("references") or []
        if isinstance(reference, Mapping)
    ]
    checks = 0
    for graph_key, key in (("event", "eventAccessors"), ("function", "functionAccessors")):
        graph = event_graph if graph_key == "event" else function_graph
        if graph is None:
            continue
        for accessor in expected[key]:
            checks += 1
            node_guid = str(accessor["nodeGuid"])
            try:
                node = _node(graph, node_guid)
            except KeyError:
                mismatches.append(f"{graph_key}: node {node_guid} missing")
                continue
            if str(node.get("class")) != accessor["nodeClass"]:
                mismatches.append(
                    f"{node_guid} class {node.get('class')} != {accessor['nodeClass']}"
                )
            properties = node.get("properties") or {}
            reference_text = str(properties.get("VariableReference", ""))
            if (
                f'MemberName="{accessor["variableName"]}"' not in reference_text
                or _guid_digits(accessor["variableGuid"]) not in _guid_digits(reference_text)
            ):
                mismatches.append(
                    f"{node_guid} VariableReference mismatch: {reference_text}"
                )
            variable = variables.get(str(accessor["variableName"]))
            if variable is None:
                mismatches.append(f"variable {accessor['variableName']} missing")
                continue
            variable_type = _variable_type_token(variable)
            if variable_type != accessor["variableType"]:
                mismatches.append(
                    f"{accessor['variableName']} type {variable_type} != {accessor['variableType']}"
                )
            inputs, outputs = _expected_pins(
                str(accessor["nodeType"]), str(accessor["variableName"]), variable_type
            )
            for name, pin_type in inputs.items():
                _check_pin(node, name, "input", pin_type, mismatches)
            for name, pin_type in outputs.items():
                _check_pin(node, name, "output", pin_type, mismatches)
            expected_kind = "reads" if accessor["nodeType"] == "variable_get" else "writes"
            matches = [
                reference
                for reference in references
                if str(reference.get("nodeGuid")) == node_guid
                and str(reference.get("kind")) == expected_kind
                and str(reference.get("targetName")) == accessor["variableName"]
            ]
            if len(matches) != 1:
                mismatches.append(
                    f"{node_guid} expected one {expected_kind} reference, got {len(matches)}"
                )

    if event_graph is not None and len(event_graph.get("nodes") or []) != expected["eventNodeCount"]:
        mismatches.append(
            f"event node count {len(event_graph.get('nodes') or [])} != {expected['eventNodeCount']}"
        )
    if function_graph is not None and len(function_graph.get("nodes") or []) != expected["functionNodeCount"]:
        mismatches.append(
            f"function node count {len(function_graph.get('nodes') or [])} != {expected['functionNodeCount']}"
        )
    total_links = 0
    for graph in document.get("graphs") or []:
        if not isinstance(graph, Mapping):
            continue
        for node in graph.get("nodes") or []:
            for pin in node.get("pins") or []:
                if isinstance(pin, Mapping) and pin.get("direction") == "output":
                    total_links += len(pin.get("links") or [])
    if total_links != int(expected["eventEdgeCount"]) + int(expected["functionEdgeCount"]):
        mismatches.append(
            f"total link count {total_links} != "
            f"{expected['eventEdgeCount']} + {expected['functionEdgeCount']}"
        )

    return {
        "schemaVersion": "ueak.blueprint-accessor-reload/1.0",
        "verified": not mismatches,
        "checkCount": checks,
        "assetSha256": actual_sha,
        "canonicalExport": str(canonical[0]),
        "eventGraphGuid": expected["eventGraphGuid"],
        "functionGraphGuid": expected["functionGraphGuid"],
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
    except Exception as exc:  # noqa: BLE001 - write the failure as evidence
        result = {
            "schemaVersion": "ueak.blueprint-accessor-reload/1.0",
            "verified": False,
            "mismatches": [f"{type(exc).__name__}: {exc}"],
        }
    result["ok"] = bool(result.get("verified"))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

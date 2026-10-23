#!/usr/bin/env python3
"""P6 live read probe plus the real exporter -> Python -> apply-request handoff.

This script talks to the fixed-project UEAgentKit Editor Bridge over localhost
TCP.  In success mode it reads ``editor.getBlueprintGraphLayoutPreflight``
through the registered MCP Tool path, verifies the response against the real
exporter context and the fixture's in-memory expected state, then runs the P4/P5
algorithm and request builder.  It never writes a Blueprint and never opens,
focuses or saves an asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit import __version__  # noqa: E402
from ue_agent_kit.editor_bridge import (  # noqa: E402
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from ue_agent_kit.graph_contracts import (  # noqa: E402
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
    preview_graph_layout,
    typed_graph_from_blueprint_context,
)
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402

TOOL_NAME = "ue_get_blueprint_graph_layout_preflight"
PLAN_OPTIONS = {"spacingX": 96, "spacingY": 64, "seed": 0}
PACKING = {"targetWidth": 1800}


class ProbeError(RuntimeError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProbeError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ProbeError(f"invalid JSON file: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProbeError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _service(project: Path) -> LiveEditorBridgeService:
    return LiveEditorBridgeService(
        LiveEditorBridgeConfig(project_path=project, timeout_seconds=10.0),
        server_version=__version__,
    )


def _require(value: bool, message: str) -> None:
    if not value:
        raise ProbeError(message)


def _success_probe(args: argparse.Namespace) -> dict[str, Any]:
    service = _service(args.project)
    result = service.call_tool(TOOL_NAME, {})
    _write_json(args.out / "p6-bridge-preflight.json", result)

    payload = result.get("result")
    _require(isinstance(payload, dict), "bridge result has no result object")
    preflight = payload
    _require(result.get("ok") is True, "bridge result is not ok")
    _require(result.get("readOnly") is True, "bridge result is not read-only")
    _require(result.get("tool") == TOOL_NAME, "bridge tool name is not the P6 read tool")
    _require(preflight.get("schemaVersion") == "ueak.graphlayoutpreflight/1.0", "preflight schema is invalid")
    _require(preflight.get("readOnly") is True, "preflight is not read-only")
    _require(preflight.get("source") == "focused-blueprint-editor", "preflight source is not the focused editor")
    _require(
        preflight.get("selectionPolicy") == "most-recently-active-blueprint-editor",
        "preflight selection policy is not the documented most-recently-active policy",
    )
    _require(preflight.get("focusScope") == "toolkit-graph-tab", "preflight focus scope is not the toolkit graph tab")
    _require(preflight.get("complete") is True, "P6 preflight is unexpectedly truncated")
    _require(preflight.get("packageDirty") is False, "P6 fixture is not clean")
    _require(isinstance(preflight.get("sessionId"), str) and preflight["sessionId"], "missing bridge session id")
    _require(
        isinstance(preflight.get("pluginBuildHash"), str)
        and preflight["pluginBuildHash"].startswith("sha256:"),
        "missing plugin build hash",
    )
    _require(preflight.get("pieState") == "stopped", "P6 probe requires a stopped PIE state")

    context = _read_json(args.context)
    metadata = _read_json(args.metadata)
    expected = _read_json(args.expected)

    _require(_sha256_file(args.context) == metadata.get("contextSha256"), "exporter context hash changed")
    _require(_sha256_file(args.preflight) == metadata.get("preflightSha256"), "preflight evidence hash changed")
    _require(preflight.get("assetPath") == metadata.get("assetPath"), "bridge/export assetPath mismatch")
    _require(preflight.get("graphGuid") == metadata.get("graphGuid"), "bridge/export graphGuid mismatch")
    _require(preflight.get("packageRevision") == metadata.get("packageRevision"), "bridge/export revision mismatch")
    _require(preflight.get("graphFingerprint") == metadata.get("graphFingerprint"), "bridge/export fingerprint mismatch")
    _require(preflight.get("nodeCount") == metadata.get("nodeCount"), "bridge/export node count mismatch")
    _require(preflight.get("pluginBuildHash") == metadata.get("pluginBuildHash"), "bridge/export plugin build mismatch")

    _require(preflight.get("assetPath") == expected.get("assetPath"), "fixture expected assetPath mismatch")
    _require(preflight.get("graphGuid") == expected.get("graphGuid"), "fixture expected graphGuid mismatch")
    _require(preflight.get("nodeCount") == expected.get("nodeCount"), "fixture expected node count mismatch")
    _require(preflight.get("packageRevision") == expected.get("packageRevision"), "fixture expected revision mismatch")
    _require(preflight.get("graphFingerprint") == expected.get("graphFingerprint"), "fixture expected fingerprint mismatch")

    expected_nodes = {str(item["nodeId"]): item for item in expected["nodes"]}
    preflight_nodes = {str(item["nodeId"]): item for item in preflight["nodes"]}
    _require(set(expected_nodes) == set(preflight_nodes), "bridge node identities do not equal the in-memory fixture")
    for node_id, expected_node in expected_nodes.items():
        actual = preflight_nodes[node_id]
        _require(actual.get("guid") == node_id[2:], f"bridge node guid is not canonical for {node_id}")
        _require(actual.get("x") == expected_node["x"], f"bridge x differs from memory for {node_id}")
        _require(actual.get("y") == expected_node["y"], f"bridge y differs from memory for {node_id}")

    provenance = {
        "schemaVersion": "ueak.p4.exportprovenance/1.0",
        "kind": "live-editor",
        "sessionId": preflight["sessionId"],
        "pluginBuildHash": preflight["pluginBuildHash"],
        "exportedAtUtc": metadata["exportedAtUtc"],
        "engineVersion": metadata["engineVersion"],
        "projectName": metadata["projectName"],
        "exporterSchemaVersion": metadata["exporterSchemaVersion"],
        "exporterVersion": metadata["exporterVersion"],
        "profile": metadata["profile"],
        "assetPath": metadata["assetPath"],
        "graphGuid": metadata["graphGuid"],
        "graphName": metadata["graphName"],
        "contextFile": metadata["contextFile"],
        "contextSha256": metadata["contextSha256"],
        "preflightFile": "p6-bridge-preflight.json",
        "preflightSha256": _sha256_file(args.out / "p6-bridge-preflight.json"),
        "nodeCount": metadata["nodeCount"],
    }
    provenance_path = args.out / "p6-export-provenance.json"
    _write_json(provenance_path, provenance)

    source_metadata = {
        "kind": "live-editor",
        "sessionId": provenance["sessionId"],
        "pluginBuildHash": provenance["pluginBuildHash"],
        "exportedAtUtc": provenance["exportedAtUtc"],
    }
    graph = typed_graph_from_blueprint_context(
        context,
        graph_guid=str(provenance["graphGuid"]),
        source_metadata=source_metadata,
    )
    _require(graph["asset"]["path"] == preflight["assetPath"], "imported graph asset mismatch")
    _require(graph["asset"]["revision"] == preflight["packageRevision"], "imported graph revision mismatch")
    _require(graph["graph"]["guid"] == preflight["graphGuid"], "imported graph GUID mismatch")
    imported_nodes = {str(node["nodeId"]): node for node in graph["nodes"]}
    _require(set(imported_nodes) == set(preflight_nodes), "imported graph nodes do not equal the bridge preflight")
    for node_id, imported in imported_nodes.items():
        _require(
            imported["nodeGuid"] == preflight_nodes[node_id]["guid"],
            f"imported guid differs for {node_id}",
        )
        _require(
            imported["position"]["x"] == preflight_nodes[node_id]["x"]
            and imported["position"]["y"] == preflight_nodes[node_id]["y"],
            f"imported position differs for {node_id}",
        )

    plan_result = preview_graph_layout(
        graph,
        {"mode": "full", "options": dict(PLAN_OPTIONS), "packing": dict(PACKING)},
    )
    _require(plan_result.status == "ok", f"layout rejected: {plan_result.errors}")
    _require(plan_result.plan is not None, "layout returned no plan")
    plan = plan_result.plan
    _require(plan["layoutHash"] == recompute_layout_hash(plan), "plan layoutHash is not canonical")
    _require(plan["baseGraphHash"] == graph["semanticHash"], "plan semantic base is not the imported graph")
    _require(plan["movedNodeIds"], "layout did not move any node")
    _require(len(plan["positions"]) == len(graph["nodes"]), "layout did not position every node")

    try:
        request = build_graph_layout_apply_request(plan, preflight)
    except GraphLayoutApplyError as exc:
        raise ProbeError(f"apply request rejected: {exc.code}: {exc}") from exc
    _require(request["assetPath"] == preflight["assetPath"], "request asset mismatch")
    _require(request["graphGuid"] == preflight["graphGuid"], "request graph GUID mismatch")
    _require(request["expectedPackageRevision"] == preflight["packageRevision"], "request revision mismatch")
    _require(request["expectedGraphFingerprint"] == preflight["graphFingerprint"], "request fingerprint mismatch")

    plan_path = args.out / "p6-layout-plan.json"
    request_path = args.out / "p6-apply-request.json"
    _write_json(plan_path, plan)
    _write_json(request_path, request)

    selection = service.call_tool("ue_get_blueprint_graph_selection", {})
    selection_payload = selection.get("result", {})
    _require(selection_payload.get("available") is True, "graph selection is not available")
    _require(
        selection_payload.get("graph", {}).get("graphGuid") == preflight["graphGuid"],
        "legacy graph selection GUID disagrees with the layout preflight",
    )

    summary = {
        "schemaVersion": "ueak.p6.probe/1.0",
        "tool": TOOL_NAME,
        "ok": True,
        "readOnly": True,
        "transport": "localhost-tcp",
        "assetPath": preflight["assetPath"],
        "graphGuid": preflight["graphGuid"],
        "packageRevision": preflight["packageRevision"],
        "graphFingerprint": preflight["graphFingerprint"],
        "nodeCount": preflight["nodeCount"],
        "sessionId": preflight["sessionId"],
        "pluginBuildHash": preflight["pluginBuildHash"],
        "pluginVersion": preflight["pluginVersion"],
        "planId": plan["planId"],
        "layoutHash": plan["layoutHash"],
        "movedNodeCount": len(plan["movedNodeIds"]),
        "requestSha256": _sha256_file(request_path),
        "planSha256": _sha256_file(plan_path),
        "selectionGraphGuid": selection_payload["graph"]["graphGuid"],
        "legacySelectionSemanticsPreserved": True,
    }
    _write_json(args.out / "p6-live-read-result.json", summary)
    return summary


def _error_probe(args: argparse.Namespace) -> dict[str, Any]:
    service = _service(args.project)
    try:
        service.call_tool(TOOL_NAME, {})
    except LiveEditorError as exc:
        result = {
            "schemaVersion": "ueak.p6.probe/1.0",
            "tool": TOOL_NAME,
            "ok": False,
            "readOnly": True,
            "transport": "localhost-tcp",
            "errorCode": exc.code,
            "errorMessage": str(exc),
            "details": exc.details,
        }
        _write_json(args.out / "p6-live-failure.json", result)
        if args.expect_code and exc.code != args.expect_code:
            raise ProbeError(f"expected failure code {args.expect_code}, got {exc.code}") from exc
        return result
    raise ProbeError("expected the live read probe to fail closed, but it succeeded")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--context", type=Path)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--expected", type=Path)
    parser.add_argument("--expect-failure", action="store_true")
    parser.add_argument("--expect-code", default="")
    args = parser.parse_args(argv)
    try:
        if args.expect_failure:
            result = _error_probe(args)
            print(json.dumps({"ok": False, "expectedFailure": True, "errorCode": result["errorCode"]}))
            return 0
        for name in ("context", "preflight", "metadata", "expected"):
            if getattr(args, name) is None:
                raise ProbeError(f"--{name} is required for a success probe")
        summary = _success_probe(args)
        print(json.dumps({key: summary[key] for key in ("ok", "assetPath", "graphGuid", "planId", "layoutHash")}, sort_keys=True))
        return 0
    except (ProbeError, LiveEditorError) as exc:
        print(f"P6 live probe failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

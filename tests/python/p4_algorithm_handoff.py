#!/usr/bin/env python3
"""P4 offline handoff: real UE exporter JSON -> TypedGraph -> LayoutPlan -> request.

This script is deliberately read-only.  It consumes the canonical
``BlueprintContextExporter`` full export and the live ``BuildPreflight`` snapshot
produced by a real UE 5.6 Editor automation run, imports the graph strictly, runs
the verified P0 layout algorithm for full/selection/incremental modes, and writes
the exact P1 apply requests that the internal C++ ``ApplyToGraph`` consumes.

It never invents links, revisions or fingerprints.  Missing exporter provenance
is rejected before a plan is built, and the preflight is required to describe the
same package revision, graph GUID, node identities and positions as the export.
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

from ue_agent_kit.graph_contracts import (  # noqa: E402
    BlueprintContextImportError,
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
    preview_graph_layout,
    typed_graph_from_blueprint_context,
)
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402

HANDOFF_VERSION = "ueak.p4.handoff/1.0"
PLAN_OPTIONS = {"spacingX": 96, "spacingY": 64, "seed": 0}
PACKING = {"targetWidth": 1800}
MODES = ("full", "selection", "incremental")


class HandoffError(RuntimeError):
    """A fail-closed P4 handoff error; no partial plan is written."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise HandoffError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise HandoffError(f"invalid JSON file: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise HandoffError(f"expected a JSON object: {path}")
    return value


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _source_metadata(provenance: Mapping[str, Any]) -> dict[str, str]:
    if provenance.get("schemaVersion") != "ueak.p4.exportprovenance/1.0":
        raise HandoffError("provenance schemaVersion is not ueak.p4.exportprovenance/1.0")
    if provenance.get("kind") != "live-editor":
        raise HandoffError("provenance kind must be live-editor")
    metadata = {
        "kind": "live-editor",
        "sessionId": provenance.get("sessionId"),
        "pluginBuildHash": provenance.get("pluginBuildHash"),
        "exportedAtUtc": provenance.get("exportedAtUtc"),
    }
    for key, value in metadata.items():
        if not isinstance(value, str) or not value:
            raise HandoffError(f"provenance {key} is missing")
    return metadata


def _preflight_consistency(
    graph: Mapping[str, Any],
    preflight: Mapping[str, Any],
    provenance: Mapping[str, Any],
) -> None:
    if preflight.get("schemaVersion") != "ueak.graphlayoutpreflight/1.0":
        raise HandoffError("preflight schemaVersion is not supported")
    if preflight.get("assetPath") != graph["asset"]["path"]:
        raise HandoffError("preflight and export disagree on assetPath")
    if preflight.get("assetPath") != provenance.get("assetPath"):
        raise HandoffError("provenance and export disagree on assetPath")
    if preflight.get("graphGuid") != graph["graph"]["guid"]:
        raise HandoffError("preflight and export disagree on graphGuid")
    if preflight.get("graphGuid") != provenance.get("graphGuid"):
        raise HandoffError("provenance and export disagree on graphGuid")
    if preflight.get("packageRevision") != graph["asset"]["revision"]:
        raise HandoffError("preflight and export disagree on packageRevision")
    if preflight.get("packageDirty") is not False:
        raise HandoffError("preflight does not prove a clean package")
    if preflight.get("evidenceLevel") != "same-process":
        raise HandoffError("preflight evidenceLevel is not same-process")
    if preflight.get("acceptanceEligible") is not False:
        raise HandoffError("preflight must not be acceptance-eligible")

    nodes = {str(node["nodeId"]): node for node in graph["nodes"]}
    raw_nodes = preflight.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise HandoffError("preflight has no nodes")
    if len(raw_nodes) != len(nodes):
        raise HandoffError("preflight and export disagree on node count")
    seen: set[str] = set()
    for entry in raw_nodes:
        if not isinstance(entry, Mapping):
            raise HandoffError("preflight node is not an object")
        node_id = entry.get("nodeId")
        if not isinstance(node_id, str) or node_id not in nodes:
            raise HandoffError(f"preflight node {node_id!r} is not in the imported graph")
        if node_id in seen:
            raise HandoffError(f"preflight contains duplicate node {node_id}")
        seen.add(node_id)
        imported = nodes[node_id]
        if entry.get("guid") != imported["nodeGuid"]:
            raise HandoffError(f"preflight guid disagrees with export for {node_id}")
        position = imported["position"]
        if entry.get("x") != position["x"] or entry.get("y") != position["y"]:
            raise HandoffError(f"preflight position disagrees with export for {node_id}")
        expected_kind = (
            "comment"
            if imported["kind"] == "comment"
            else "reroute"
            if imported["kind"] == "reroute"
            else "node"
        )
        if entry.get("kind") != expected_kind:
            raise HandoffError(f"preflight kind disagrees with export for {node_id}")


def _plan_request(
    graph: dict[str, Any],
    preflight: dict[str, Any],
    mode: str,
    scope: list[str] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    request_doc: dict[str, Any] = {
        "mode": mode,
        "options": dict(PLAN_OPTIONS),
        "packing": dict(PACKING),
    }
    if scope is not None:
        request_doc["scope"] = list(scope)
    result = preview_graph_layout(graph, request_doc)
    if result.status != "ok" or not result.plan:
        raise HandoffError(f"{mode} layout was rejected: {result.errors}")
    plan = result.plan
    if plan.get("layoutHash") != recompute_layout_hash(plan):
        raise HandoffError(f"{mode} layoutHash is not canonical")
    if plan.get("baseGraphHash") != graph.get("semanticHash"):
        raise HandoffError(f"{mode} plan changed the semantic graph hash")
    try:
        request = build_graph_layout_apply_request(plan, preflight)
    except GraphLayoutApplyError as exc:
        raise HandoffError(f"{mode} apply request was rejected: {exc.code}: {exc}") from exc
    return plan, request


def _select_scope(
    graph: dict[str, Any],
    preflight: dict[str, Any],
    full_plan: Mapping[str, Any],
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    """Pick a deterministic downstream scope that the algorithm can lay out.

    The candidate order is fixed (full-plan x descending, then nodeId), and the
    first scope accepted by both the planner and the P1 request builder is used.
    No position is edited to force acceptance.
    """

    content = [node for node in graph["nodes"] if node["kind"] != "comment"]
    positions = {
        str(item["nodeId"]): int(item["x"])
        for item in full_plan["positions"]
    }
    ordered = sorted(content, key=lambda node: (-positions[str(node["nodeId"])], str(node["nodeId"])))
    node_ids = [str(node["nodeId"]) for node in ordered]

    for size in range(2, len(node_ids)):
        scope = node_ids[:size]
        for mode in ("selection", "incremental"):
            try:
                plan, request = _plan_request(graph, preflight, mode, scope)
            except HandoffError:
                continue
            moved = set(plan["movedNodeIds"])
            if not moved:
                continue
            if not moved.issubset(set(scope)):
                continue
            fixed = set(plan["fixedNodeIds"])
            if not fixed:
                continue
            if not (fixed & {str(node["nodeId"]) for node in content}):
                continue
            return scope, plan, request
    raise HandoffError("no selection/incremental scope produced a real scoped plan")


def build_handoff(
    context_path: Path,
    preflight_path: Path,
    provenance_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    context = _read_json(context_path)
    preflight = _read_json(preflight_path)
    provenance = _read_json(provenance_path)

    metadata = _source_metadata(provenance)
    if provenance.get("contextSha256") != _sha256_file(context_path):
        raise HandoffError("provenance contextSha256 does not match the exported context")
    if provenance.get("preflightSha256") != _sha256_file(preflight_path):
        raise HandoffError("provenance preflightSha256 does not match the preflight file")

    # The strict importer must reject the raw exporter output without recorded
    # provenance.  This is the missing-metadata gate, not a warning.
    missing_metadata_code = ""
    try:
        typed_graph_from_blueprint_context(context)
    except BlueprintContextImportError as exc:
        missing_metadata_code = exc.code
    if missing_metadata_code != "blueprint-context-source-invalid":
        raise HandoffError(
            "the importer did not reject the export without source metadata: "
            f"{missing_metadata_code or 'accepted'}"
        )

    graph = typed_graph_from_blueprint_context(
        context,
        graph_guid=str(provenance["graphGuid"]),
        source_metadata=metadata,
    )
    if graph["source"]["sessionId"] != metadata["sessionId"]:
        raise HandoffError("imported graph did not retain the recorded session id")
    if graph["source"]["pluginBuildHash"] != metadata["pluginBuildHash"]:
        raise HandoffError("imported graph did not retain the recorded plugin build hash")
    _preflight_consistency(graph, preflight, provenance)

    full_plan, full_request = _plan_request(graph, preflight, "full", None)
    if not full_plan["movedNodeIds"]:
        raise HandoffError("full layout produced no movement")
    if len({item["nodeId"] for item in full_plan["positions"]}) != len(graph["nodes"]):
        raise HandoffError("full layout did not position every imported node")

    scope, selection_plan, selection_request = _select_scope(graph, preflight, full_plan)
    incremental_plan, incremental_request = _plan_request(
        graph, preflight, "incremental", scope
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, dict[str, Any]] = {}
    for mode, plan, request in (
        ("full", full_plan, full_request),
        ("selection", selection_plan, selection_request),
        ("incremental", incremental_plan, incremental_request),
    ):
        plan_name = f"layout-{mode}.json"
        request_name = f"apply-request-{mode}.json"
        plan_path = output_dir / plan_name
        request_path = output_dir / request_name
        plan_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        request_path.write_text(
            json.dumps(request, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        written[mode] = {
            "planFile": plan_name,
            "planSha256": _sha256_file(plan_path),
            "planId": plan["planId"],
            "layoutHash": plan["layoutHash"],
            "movedNodeCount": len(plan["movedNodeIds"]),
            "scopeNodeIds": plan["scopeNodeIds"],
            "requestFile": request_name,
            "requestSha256": _sha256_file(request_path),
        }

    summary = {
        "schemaVersion": HANDOFF_VERSION,
        "assetPath": graph["asset"]["path"],
        "graphGuid": graph["graph"]["guid"],
        "packageRevision": graph["asset"]["revision"],
        "preflightFingerprint": preflight["graphFingerprint"],
        "preflightSha256": _sha256_file(preflight_path),
        "contextSha256": _sha256_file(context_path),
        "provenanceSha256": _sha256_file(provenance_path),
        "source": dict(graph["source"]),
        "import": {
            "nodeCount": len(graph["nodes"]),
            "pinCount": len(graph["pins"]),
            "edgeCount": len(graph["edges"]),
            "commentCount": len(graph["comments"]),
            "semanticHash": graph["semanticHash"],
        },
        "missingMetadataRejected": {
            "code": missing_metadata_code,
            "accepted": False,
        },
        "selectedScope": scope,
        "plans": written,
    }
    (output_dir / "handoff-summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--preflight", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = build_handoff(args.context, args.preflight, args.provenance, args.out)
    except HandoffError as exc:
        print(f"P4 handoff failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "graphGuid": summary["graphGuid"],
                "plans": {
                    mode: {
                        "layoutHash": value["layoutHash"],
                        "movedNodeCount": value["movedNodeCount"],
                    }
                    for mode, value in summary["plans"].items()
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

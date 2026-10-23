#!/usr/bin/env python3
"""Build the canonical P10 apply requests from the real UE fixture export.

Input (written by the P10FixtureContract automation test):

    Build/P10IntegrationHost/Evidence/p10-context.json     canonical exporter JSON
    Build/P10IntegrationHost/Evidence/p10-preflight.json   live BuildPreflight JSON

Output:

    p10-plan.json              P0 LayoutPlan from the ported layered engine
    p10-apply-request.json     canonical position-only ApplyRequest (full mode)
    p10-negative-request.json  same plan moving only the broken 0-input knot
    p10-plan-summary.json      hashes, knot shapes, acceptance facts

The script is read-only with respect to Unreal: it never opens the Editor, an
asset, a transaction or a socket.  Where the Python client gate rejects the
negative plan, the rejected request is still written so the C++ live gate can
be proven to reject it independently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.graph_contracts.layout_apply import (  # noqa: E402
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
)
from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402

DEFAULT_EVIDENCE = ROOT / "Build" / "P10IntegrationHost" / "Evidence"
PLUGIN_BUILD_HASH = "sha256:" + hashlib.sha256(b"P10IntegrationHost").hexdigest()


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("P10 probe failed: " + message)


def _node_positions(preflight: dict[str, Any]) -> dict[str, tuple[int, int]]:
    return {node["nodeId"]: (int(node["x"]), int(node["y"])) for node in preflight["nodes"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args(argv)
    evidence: Path = args.evidence

    context_path = evidence / "p10-context.json"
    preflight_path = evidence / "p10-preflight.json"
    _require(context_path.is_file(), f"missing {context_path}")
    _require(preflight_path.is_file(), f"missing {preflight_path}")
    document = json.loads(context_path.read_text(encoding="utf-8"))
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))

    preflight.setdefault("available", True)
    preflight["available"] = True
    preflight["complete"] = True
    preflight["nodesTruncated"] = False
    preflight["pieState"] = "stopped"
    preflight["packageDirty"] = False
    preflight["editable"] = True
    preflight["sessionId"] = "p10-file-export"
    preflight["pluginBuildHash"] = PLUGIN_BUILD_HASH
    # The Bridge wrapper normally supplies the focused graph name; the raw
    # BuildPreflight object does not, so take it from the exported document.
    preflight["graphName"] = document["graphs"][0]["name"]

    envelope = {
        "available": True,
        "readOnly": True,
        "assetPath": preflight["assetPath"],
        "graphGuid": preflight["graphGuid"],
        "packageRevision": preflight["packageRevision"],
        "graphFingerprint": preflight["graphFingerprint"],
        "packageDirty": False,
        "exportedAtUtc": "2026-10-23T00:00:00Z",
        "sessionId": preflight["sessionId"],
        "pluginBuildHash": preflight["pluginBuildHash"],
        "context": document,
    }

    result = build_preview(preflight, envelope, mode="full", spacing_x=96, spacing_y=64, target_width=1800, seed=0)
    plan = result["plan"]
    _require(plan["status"] == "ok", f"plan status is {plan['status']}")
    _require(plan["layoutHash"] == recompute_layout_hash(plan), "layout hash mismatch")
    _require(len(plan["positions"]) == preflight["nodeCount"], "plan does not cover every node")

    request = build_graph_layout_apply_request(plan, preflight)

    # Negative: the separate broken-knot asset.  It is never part of the
    # positive plan because the layout engine correctly refuses a 0-input knot.
    broken_path = evidence / "p10-broken-preflight.json"
    _require(broken_path.is_file(), f"missing {broken_path}")
    broken_preflight = json.loads(broken_path.read_text(encoding="utf-8"))
    broken_nodes = broken_preflight["nodes"]
    broken_knots = [node for node in broken_nodes if node["kind"] == "reroute"]
    _require(len(broken_knots) == 1, "expected exactly one broken knot")
    broken = broken_knots[0]
    _require(broken["inputLinkCount"] == 0, "the broken knot unexpectedly has an input")

    knots = [node for node in preflight["nodes"] if node["kind"] == "reroute"]
    fanout = [n for n in knots if n["inputLinkCount"] == 1 and n["outputLinkCount"] == 2]
    merge = [n for n in knots if n["inputLinkCount"] == 2 and n["outputLinkCount"] == 1]
    _require(len(fanout) == 1, "expected exactly one data fan-out knot")
    _require(len(merge) == 1, "expected exactly one exec merge knot")

    exposed = set(plan["movedNodeIds"])
    for label, node in (("fan-out", fanout[0]), ("merge", merge[0])):
        _require(node["nodeId"] in exposed, f"{label} knot was not moved by the full plan")

    broken_positions = _node_positions(broken_preflight)
    negative_positions = []
    for node in broken_nodes:
        x, y = broken_positions[node["nodeId"]]
        if node["nodeId"] == broken["nodeId"]:
            x += 600
            y += 300
        negative_positions.append(
            {
                "nodeId": node["nodeId"],
                "x": x,
                "y": y,
                "size": {"width": node["width"], "height": node["height"], "source": "stored"},
            }
        )
    negative_plan = {
        "schemaVersion": "ueak.layoutplan/1.0",
        "planId": "layout:p10negative",
        "status": "ok",
        "errorCode": "",
        "mode": "full",
        "baseGraphHash": broken_preflight["packageRevision"],
        "layoutHash": "",
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "positions": negative_positions,
        "movedNodeIds": [broken["nodeId"]],
        "scopeNodeIds": sorted(n["nodeId"] for n in broken_nodes if n["kind"] != "comment"),
        "fixedNodeIds": [],
        "islands": [],
        "warnings": [],
    }
    negative_plan["layoutHash"] = recompute_layout_hash(negative_plan)

    python_gate_code = ""
    try:
        build_graph_layout_apply_request(negative_plan, broken_preflight)
    except GraphLayoutApplyError as exc:
        python_gate_code = exc.code
    _require(
        python_gate_code == "layout-apply-reroute-broken",
        f"Python gate did not reject the broken knot (got {python_gate_code!r})",
    )

    # Hand-built request for the C++ live gate.  It is deliberately not routed
    # through the Python gate: the point is to prove the C++ side rejects the
    # same topology on its own.
    negative_request = {
        "schemaVersion": "ueak.graphlayoutapply/1.0",
        "assetPath": broken_preflight["assetPath"],
        "graphGuid": broken_preflight["graphGuid"],
        "expectedPackageRevision": broken_preflight["packageRevision"],
        "expectedGraphFingerprint": broken_preflight["graphFingerprint"],
        "plan": negative_plan,
        "requestedMode": "position-only",
        "allowSave": False,
        "allowCompile": False,
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
    }

    # Read-only incremental no-op evidence: one sink with everything else fixed
    # must stay within its fixed scope and still be an accepted plan.
    sink = next(
        node
        for node in preflight["nodes"]
        if node["kind"] == "node" and node["inputLinkCount"] > 0 and node["outputLinkCount"] == 0
    )
    incremental = build_preview(
        preflight,
        envelope,
        mode="incremental",
        scope_node_ids=[sink["nodeId"]],
        spacing_x=96,
        spacing_y=64,
        target_width=1800,
        seed=0,
    )["plan"]

    plan_path = evidence / "p10-plan.json"
    request_path = evidence / "p10-apply-request.json"
    negative_path = evidence / "p10-negative-request.json"
    plan_sha = _write_json(plan_path, plan)
    request_sha = _write_json(request_path, request)
    negative_sha = _write_json(negative_path, negative_request)

    summary = {
        "schemaVersion": "ueak.p10.planprobe/1.0",
        "ok": True,
        "readOnly": True,
        "assetPath": preflight["assetPath"],
        "graphGuid": preflight["graphGuid"],
        "nodeCount": len(plan["positions"]),
        "movedCount": len(plan["movedNodeIds"]),
        "planId": plan["planId"],
        "layoutHash": plan["layoutHash"],
        "knotShapes": {
            "fanOut": fanout[0]["nodeId"],
            "merge": merge[0]["nodeId"],
            "broken": broken["nodeId"],
        },
        "fanOutMoved": fanout[0]["nodeId"] in exposed,
        "mergeMoved": merge[0]["nodeId"] in exposed,
        "pythonNegativeGateCode": python_gate_code,
        "incrementalStatus": incremental["status"],
        "incrementalMovedCount": len(incremental.get("movedNodeIds", [])),
        "incrementalScopedNode": sink["nodeId"],
        "files": {
            "plan": {"path": str(plan_path), "sha256": plan_sha},
            "request": {"path": str(request_path), "sha256": request_sha},
            "negativeRequest": {"path": str(negative_path), "sha256": negative_sha},
        },
        "engineLayoutSha256": "4D364E1DCAB828E56926E86EF54E4A4396CFB289FCC925D44F1C62CB4BC3BCD8",
        "layeredSha256": "2B0C6B0A3FDFFE5626A334DDF54D2E87A7B9F92B0AD78F459DBCB2FDF90A0316",
    }
    summary_sha = _write_json(evidence / "p10-plan-summary.json", summary)
    print(json.dumps({**summary, "summarySha256": summary_sha}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

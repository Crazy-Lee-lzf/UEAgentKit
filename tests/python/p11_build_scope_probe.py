#!/usr/bin/env python3
"""Build canonical P11 selection and incremental apply requests.

Inputs (written by the P11FixtureContract automation test):

    Evidence/p11-context.json        canonical exporter JSON for BP_LayoutP11
    Evidence/p11-preflight.json      live preflight for the same graph
    Evidence/p11-inc-context.json    canonical exporter JSON for BP_LayoutP11Inc
    Evidence/p11-inc-preflight.json  live preflight for the same graph

Outputs: p11-selection-request.json, p11-incremental-request.json,
p11-plan-summary.json.

A real scope is discovered from the exported graph: for each node, a bounded
two-hop neighbourhood is tried as a selection scope and the first scope that
moves at least one node (while leaving the rest fixed) is kept.  Nothing is
hand-placed; the request positions come from the ported layered engine.
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
from ue_agent_kit.graph_contracts.layout_apply import build_graph_layout_apply_request  # noqa: E402
from ue_agent_kit.graph_contracts import typed_graph_from_blueprint_context  # noqa: E402
from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402

DEFAULT_EVIDENCE = ROOT / "Build" / "P11AutomationHost" / "Evidence"
PLUGIN_BUILD_HASH = "sha256:" + hashlib.sha256(b"P11AutomationHost").hexdigest()


def _write_json(path: Path, value: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit("P11 probe failed: " + message)


def _load_pair(evidence: Path, context_name: str, preflight_name: str) -> tuple[dict, dict, dict]:
    context_path = evidence / context_name
    preflight_path = evidence / preflight_name
    _require(context_path.is_file(), f"missing {context_path}")
    _require(preflight_path.is_file(), f"missing {preflight_path}")
    document = json.loads(context_path.read_text(encoding="utf-8"))
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    preflight["available"] = True
    preflight["complete"] = True
    preflight["nodesTruncated"] = False
    preflight["pieState"] = "stopped"
    preflight["packageDirty"] = False
    preflight["editable"] = True
    preflight["sessionId"] = "p11-file-export"
    preflight["pluginBuildHash"] = PLUGIN_BUILD_HASH
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
    graph = typed_graph_from_blueprint_context(
        document,
        graph_guid=preflight["graphGuid"],
        source_metadata={
            "kind": "live-editor",
            "sessionId": preflight["sessionId"],
            "pluginBuildHash": preflight["pluginBuildHash"],
            "exportedAtUtc": envelope["exportedAtUtc"],
        },
        graph_editable=True,
    )
    return preflight, envelope, graph


def _neighbourhoods(graph: dict[str, Any], limit: int = 8) -> list[list[str]]:
    adjacency: dict[str, set[str]] = {}
    for edge in graph["edges"]:
        source = edge["from"]["nodeId"]
        target = edge["to"]["nodeId"]
        adjacency.setdefault(source, set()).add(target)
        adjacency.setdefault(target, set()).add(source)
    content = [node["nodeId"] for node in graph["nodes"] if node["kind"] != "comment"]
    result: list[list[str]] = []
    seen: set[frozenset[str]] = set()
    for seed in content:
        scope = {seed}
        frontier = {seed}
        for _ in range(2):
            nxt: set[str] = set()
            for node_id in frontier:
                nxt |= adjacency.get(node_id, set())
            scope |= nxt
            frontier = nxt
        scope &= set(content)
        if len(scope) < 3:
            continue
        if len(scope) > limit:
            scope = set(sorted(scope)[:limit])
        key = frozenset(scope)
        if key in seen or len(scope) == len(content):
            continue
        seen.add(key)
        result.append(sorted(scope))
    return result


def _plan_for_scope(preflight: dict, envelope: dict, mode: str, scope: list[str]) -> dict | None:
    try:
        return build_preview(
            preflight,
            envelope,
            mode=mode,
            scope_node_ids=scope,
            spacing_x=96,
            spacing_y=64,
            target_width=1800,
            seed=0,
        )["plan"]
    except Exception:  # noqa: BLE001 - rejected candidate scopes are expected
        return None


def _pick(preflight: dict, envelope: dict, graph: dict, mode: str) -> tuple[dict, list[str]]:
    original = {
        node["nodeId"]: (node["position"]["x"], node["position"]["y"]) for node in graph["nodes"]
    }
    best: tuple[dict, list[str]] | None = None
    for scope in _neighbourhoods(graph):
        plan = _plan_for_scope(preflight, envelope, mode, scope)
        if plan is None or plan["status"] != "ok":
            continue
        moved = set(plan["movedNodeIds"])
        if not moved or not moved <= set(scope):
            continue
        outside_held = all(
            (p["x"], p["y"]) == original[p["nodeId"]]
            for p in plan["positions"]
            if p["nodeId"] not in scope
        )
        if not outside_held:
            continue
        if best is None or len(moved) > len(best[0]["movedNodeIds"]):
            best = (plan, scope)
    _require(best is not None, f"no {mode} scope produced a real move")
    plan, scope = best
    _require(plan["layoutHash"] == recompute_layout_hash(plan), "layout hash mismatch")
    return plan, scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    args = parser.parse_args(argv)
    evidence: Path = args.evidence

    selection_preflight, selection_envelope, selection_graph = _load_pair(
        evidence, "p11-context.json", "p11-preflight.json"
    )
    inc_preflight, inc_envelope, inc_graph = _load_pair(
        evidence, "p11-inc-context.json", "p11-inc-preflight.json"
    )

    selection_plan, selection_scope = _pick(selection_preflight, selection_envelope, selection_graph, "selection")
    incremental_plan, incremental_scope = _pick(inc_preflight, inc_envelope, inc_graph, "incremental")

    selection_request = build_graph_layout_apply_request(selection_plan, selection_preflight)
    incremental_request = build_graph_layout_apply_request(incremental_plan, inc_preflight)

    selection_path = evidence / "p11-selection-request.json"
    incremental_path = evidence / "p11-incremental-request.json"
    selection_sha = _write_json(selection_path, selection_request)
    incremental_sha = _write_json(incremental_path, incremental_request)

    summary = {
        "schemaVersion": "ueak.p11.planprobe/1.0",
        "ok": True,
        "readOnly": True,
        "selection": {
            "assetPath": selection_preflight["assetPath"],
            "graphGuid": selection_preflight["graphGuid"],
            "mode": selection_plan["mode"],
            "planId": selection_plan["planId"],
            "layoutHash": selection_plan["layoutHash"],
            "scopeCount": len(selection_scope),
            "movedCount": len(selection_plan["movedNodeIds"]),
            "scope": selection_scope,
            "moved": selection_plan["movedNodeIds"],
            "requestSha256": selection_sha,
        },
        "incremental": {
            "assetPath": inc_preflight["assetPath"],
            "graphGuid": inc_preflight["graphGuid"],
            "mode": incremental_plan["mode"],
            "planId": incremental_plan["planId"],
            "layoutHash": incremental_plan["layoutHash"],
            "scopeCount": len(incremental_scope),
            "movedCount": len(incremental_plan["movedNodeIds"]),
            "scope": incremental_scope,
            "moved": incremental_plan["movedNodeIds"],
            "requestSha256": incremental_sha,
        },
        "engine": {
            "layoutSha256": hashlib.sha256((SRC / "ue_agent_kit/graph_contracts/layout.py").read_bytes()).hexdigest(),
            "layeredSha256": hashlib.sha256((SRC / "ue_agent_kit/graph_contracts/_layout_layered.py").read_bytes()).hexdigest(),
        },
    }
    summary_sha = _write_json(evidence / "p11-plan-summary.json", summary)
    print(json.dumps({**summary, "summarySha256": summary_sha}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

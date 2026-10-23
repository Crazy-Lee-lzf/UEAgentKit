"""M2.0 offline fixture builders using the real exporter documents.

The envelope shape mirrors ``BuildP9MenuPreviewInputs`` in
``EditorBridgeGraphHandlers.cpp`` (ten top-level fields; no sessionId or
pluginBuildHash at the envelope level).  The preflight mirrors the fields from
``BuildPreflight`` plus the transport metadata added by
``BuildBlueprintGraphLayoutPreflightResult``.  No live P11 capture exists in
this worktree, so these are source-shaped rather than runtime-captured; the
``context`` member is the actual canonical exporter output.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.layout_broker.descriptor import build_project_path_hash  # noqa: E402

PROJECT_NAME = "M2ConformanceHost"
PROJECT_PATH = "C:/Projects/M2ConformanceHost/M2ConformanceHost.uproject"
PROJECT_PATH_HASH = build_project_path_hash(PROJECT_PATH)
SESSION_ID = "m2-conformance-session"
PLUGIN_BUILD_HASH = "sha256:" + hashlib.sha256(b"m2-conformance").hexdigest()
GOLDEN_PATH = ROOT / "tests" / "fixtures" / "layout_broker_m2" / "golden-cases.json"

ENVELOPE_KEYS = frozenset(
    {
        "schemaVersion",
        "available",
        "readOnly",
        "assetPath",
        "graphGuid",
        "packageRevision",
        "packageDirty",
        "graphFingerprint",
        "exportedAtUtc",
        "context",
    }
)
PREFLIGHT_KEYS = frozenset(
    {
        "schemaVersion",
        "assetPath",
        "graphGuid",
        "graphClassPath",
        "schemaClassPath",
        "packageRevision",
        "packageDirty",
        "graphFingerprint",
        "graphFingerprintAlgorithm",
        "evidenceLevel",
        "acceptanceEligible",
        "requiredAcceptanceEvidence",
        "nodeCount",
        "commentCount",
        "rerouteCount",
        "nodes",
        "source",
        "readOnly",
        "editorName",
        "blueprintPath",
        "graphName",
        "editable",
        "lastActivationTime",
        "totalNodeCount",
        "nodesReturned",
        "nodesTruncated",
        "complete",
        "available",
        "selectionPolicy",
        "focusScope",
        "sessionId",
        "pluginVersion",
        "projectName",
        "pieState",
        "pluginBuildHash",
    }
)


def load_golden_cases() -> dict[str, Any]:
    return json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))


def load_context(case: dict[str, Any]) -> dict[str, Any]:
    return json.loads((ROOT / case["contextFile"]).read_text(encoding="utf-8"))


def _preflight_nodes(graph: dict[str, Any]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for node in graph["nodes"]:
        pins = node.get("pins", [])
        inputs = [pin for pin in pins if pin.get("direction") == "input"]
        outputs = [pin for pin in pins if pin.get("direction") == "output"]
        kind = (
            "comment"
            if "Comment" in node["class"]
            else ("reroute" if "Knot" in node["class"] else "node")
        )
        entry = {
            "nodeId": "n:" + node["guid"],
            "guid": node["guid"],
            "classPath": node["class"],
            "kind": kind,
            "x": node["layout"]["x"],
            "y": node["layout"]["y"],
            "width": node["layout"]["width"],
            "height": node["layout"]["height"],
            "inputPinCount": len(inputs),
            "outputPinCount": len(outputs),
            "inputLinkCount": sum(len(pin.get("links", [])) for pin in inputs),
            "outputLinkCount": sum(len(pin.get("links", [])) for pin in outputs),
        }
        nodes.append(entry)
    return nodes


def actual_inputs(case: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return (preflight, contextEnvelope, canonicalContext) for one golden case."""

    document = copy.deepcopy(load_context(case))
    graph = document["graphs"][0]
    revision = document["revision"]["value"]
    fingerprint = "sha1:" + hashlib.sha1(
        json.dumps(graph, sort_keys=True).encode("utf-8"), usedforsecurity=False
    ).hexdigest()
    nodes = _preflight_nodes(graph)
    preflight = {
        "schemaVersion": "ueak.graphlayoutpreflight/1.0",
        "assetPath": document["assetPath"],
        "graphGuid": graph["guid"],
        "graphClassPath": graph["schema"],
        "schemaClassPath": graph["schema"],
        "packageRevision": revision,
        "packageDirty": False,
        "graphFingerprint": fingerprint,
        "graphFingerprintAlgorithm": "sha1-length-prefixed-graph-state-v1",
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "nodeCount": len(graph["nodes"]),
        "commentCount": sum(1 for node in graph["nodes"] if "Comment" in node["class"]),
        "rerouteCount": sum(1 for node in graph["nodes"] if "Knot" in node["class"]),
        "nodes": nodes,
        "source": "focused-blueprint-editor",
        "readOnly": True,
        "editorName": "BlueprintEditor",
        "blueprintPath": document["assetPath"],
        "graphName": graph["name"],
        "editable": True,
        "lastActivationTime": 1.0,
        "totalNodeCount": len(nodes),
        "nodesReturned": len(nodes),
        "nodesTruncated": False,
        "complete": True,
        "available": True,
        "selectionPolicy": "most-recently-active-blueprint-editor",
        "focusScope": "toolkit-graph-tab",
        "sessionId": SESSION_ID,
        "pluginVersion": "0.8.0",
        "projectName": document["projectName"],
        "pieState": "stopped",
        "pluginBuildHash": PLUGIN_BUILD_HASH,
    }
    envelope = {
        "schemaVersion": "ueak.graphlayoutcontext/1.0",
        "available": True,
        "readOnly": True,
        "assetPath": document["assetPath"],
        "graphGuid": graph["guid"],
        "packageRevision": revision,
        "packageDirty": False,
        "graphFingerprint": fingerprint,
        "exportedAtUtc": "2026-10-24T00:00:00Z",
        "context": document,
    }
    return preflight, envelope, document


def case_by_id(case_id: str) -> dict[str, Any]:
    for case in load_golden_cases()["cases"]:
        if case["id"] == case_id:
            return case
    raise KeyError(case_id)


__all__ = [
    "ENVELOPE_KEYS",
    "GOLDEN_PATH",
    "PLUGIN_BUILD_HASH",
    "PREFLIGHT_KEYS",
    "PROJECT_NAME",
    "PROJECT_PATH",
    "PROJECT_PATH_HASH",
    "SESSION_ID",
    "actual_inputs",
    "case_by_id",
    "load_context",
    "load_golden_cases",
]

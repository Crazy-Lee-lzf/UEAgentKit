"""Deterministic layout-projection fixtures for the large-graph regression tests.

The 134-node graph mirrors the shape that exceeded the 700 KiB canonical context:
one node per EventGraph call site, two pins per node and one edge per adjacent
pair.  It is intentionally generated rather than stored as a 700 KiB blob.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.graph_layout_projection import (  # noqa: E402
    PROJECTION_METHOD,
    PROJECTION_SCHEMA,
    PROJECTION_VERSION,
    projection_hashes,
)

ASSET_PATH = "/Game/Large/BP_Large.BP_Large"
GRAPH_GUID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
REVISION = "sha256:" + "1" * 64
FINGERPRINT = "sha1:" + "2" * 40
PREFLIGHT_METHOD = "editor.getBlueprintGraphLayoutPreflight"


def node_guid(index: int) -> str:
    return f"{index + 1:08x}-0000-4000-8000-{index + 1:012x}"


def pin_guid(index: int, direction: str) -> str:
    offset = 0x10000000 if direction == "input" else 0x20000000
    return f"{offset + index:08x}-0000-4000-8000-{offset + index:012x}"


def build_projection(node_count: int = 134) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    pins: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    for index in range(node_count):
        guid = node_guid(index)
        node_id = "n:" + guid
        nodes.append(
            {
                "nodeId": node_id,
                "guid": guid,
                "classPath": "/Script/BlueprintGraph.K2Node_CallFunction",
                "kind": "node",
                "x": (index % 12) * 300,
                "y": (index // 12) * 180,
                "width": 220,
                "height": 120,
            }
        )
        for direction in ("input", "output"):
            pin_id = pin_guid(index, direction)
            pins.append(
                {
                    "pinId": pin_id,
                    "nodeId": node_id,
                    "nodeGuid": guid,
                    "direction": direction,
                    "kind": "data",
                    "typeCategory": "int",
                    "typeKey": "int",
                    "hidden": False,
                    "advanced": False,
                }
            )
        if index + 1 < node_count:
            next_guid = node_guid(index + 1)
            edges.append(
                {
                    "sourcePinId": pin_guid(index, "output"),
                    "sourceNodeId": node_id,
                    "targetPinId": pin_guid(index + 1, "input"),
                    "targetNodeId": "n:" + next_guid,
                    "kind": "data",
                }
            )
    hashes = projection_hashes(nodes, pins, edges)
    return {
        "schemaVersion": PROJECTION_SCHEMA,
        "projectionVersion": PROJECTION_VERSION,
        "available": True,
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "assetPath": ASSET_PATH,
        "assetClass": "/Script/Engine.Blueprint",
        "graphGuid": GRAPH_GUID,
        "graphName": "EventGraph",
        "graphKind": "uber",
        "graphClassPath": "/Script/Engine.EdGraph",
        "schemaClassPath": "/Script/BlueprintGraph.EdGraphSchema_K2",
        "packageRevision": REVISION,
        "packageDirty": False,
        "graphFingerprint": FINGERPRINT,
        "nodeCount": node_count,
        "pinCount": len(pins),
        "edgeCount": len(edges),
        "commentCount": 0,
        **hashes,
        "nodes": nodes,
        "pins": pins,
        "edges": edges,
        "comments": [],
    }


def preflight(node_count: int = 134, **overrides: Any) -> dict[str, Any]:
    value = {
        "available": True,
        "complete": True,
        "nodesTruncated": False,
        "assetPath": ASSET_PATH,
        "graphGuid": GRAPH_GUID,
        "graphName": "EventGraph",
        "packageRevision": REVISION,
        "packageDirty": False,
        "graphFingerprint": FINGERPRINT,
        "nodeCount": node_count,
        "sessionId": "session-large",
        "pluginBuildHash": "sha256:" + "3" * 64,
        "editable": True,
        "pieState": "stopped",
    }
    value.update(overrides)
    return value


def make_page(
    full: Mapping[str, Any],
    offset: int,
    limit: int,
    *,
    nodes_override: list[dict[str, Any]] | None = None,
    hashes_override: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    nodes = list(full["nodes"])
    page_nodes = nodes[offset : offset + limit] if nodes_override is None else nodes_override
    page_ids = {str(node["nodeId"]) for node in page_nodes}
    page_pins = [pin for pin in full["pins"] if str(pin["nodeId"]) in page_ids]
    page_edges = [edge for edge in full["edges"] if str(edge["sourceNodeId"]) in page_ids]
    page_comments = [comment for comment in full["comments"] if str(comment["nodeId"]) in page_ids]
    end = min(len(nodes), offset + limit) if nodes_override is None else offset + len(page_nodes)
    has_more = bool(page_nodes) and end < len(nodes)
    return {
        **{key: full[key] for key in (
            "schemaVersion",
            "projectionVersion",
            "available",
            "readOnly",
            "source",
            "assetPath",
            "assetClass",
            "graphGuid",
            "graphName",
            "graphKind",
            "graphClassPath",
            "schemaClassPath",
            "packageRevision",
            "packageDirty",
            "graphFingerprint",
            "nodeCount",
            "pinCount",
            "edgeCount",
            "commentCount",
            "nodeSetHash",
            "pinSetHash",
            "edgeSetHash",
            "projectionHash",
        )},
        "offset": offset,
        "limit": limit,
        "returnedNodeCount": len(page_nodes),
        "hasMore": has_more,
        "nextOffset": end,
        "nodes": page_nodes,
        "pins": page_pins,
        "edges": page_edges,
        "comments": page_comments,
        **(hashes_override or {}),
    }


class FakeProjectionService:
    """Small stand-in for the Live Editor Bridge's projection endpoint."""

    def __init__(
        self,
        full: Mapping[str, Any] | None = None,
        *,
        page_limit_cap: int = 256,
        after_preflight: Mapping[str, Any] | None = None,
    ) -> None:
        self.full = dict(full or build_projection())
        self.page_limit_cap = page_limit_cap
        self.after_preflight = dict(after_preflight) if after_preflight else None
        self.preflight_calls = 1
        self.method_calls: list[tuple[str, dict[str, Any]]] = []

    def status(self) -> dict[str, Any]:
        return {
            "state": "available",
            "reasonCode": "",
            "reason": "",
            "capabilities": [PREFLIGHT_METHOD, PROJECTION_METHOD],
        }

    def call_method(self, name: str, params: dict[str, Any], **_: Any) -> dict[str, Any]:
        self.method_calls.append((name, params))
        if name == PREFLIGHT_METHOD:
            self.preflight_calls += 1
            if self.after_preflight is not None and self.preflight_calls > 1:
                return dict(self.after_preflight)
            return preflight(len(self.full["nodes"]))
        if name != PROJECTION_METHOD:
            raise LiveEditorError("live-editor-unsupported", name)
        limit = int(params.get("limit", 64))
        if limit > self.page_limit_cap:
            raise LiveEditorError(
                "graph-layout-projection-page-too-large",
                "page exceeds the native byte budget",
            )
        return make_page(self.full, int(params.get("offset", 0)), limit)

    def call_tool(self, name: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"ok": True, "result": {"selectedNodes": []}}

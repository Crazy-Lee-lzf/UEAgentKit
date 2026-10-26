"""Bounded, paginated layout projection for large Blueprint graphs.

The full canonical focused-graph context remains deliberately capped at 700 KiB.
This module consumes a separate, read-only Bridge endpoint that carries only the
fields the layout planner needs: graph identity, node geometry, pin/link
topology and comment bounds.  Pages are bounded by the Editor, concatenated by
this client, and checked against whole-graph node/pin/edge hashes before a plan
is built.

The projection is not a general graph-editing surface.  Apply remains gated by
the existing focused-graph, clean-package, revision/fingerprint and layout-hash
checks in the Editor.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .graph_layout_preview import build_preview

PROJECTION_METHOD = "editor.getFocusedBlueprintGraphLayoutProjection"
PROJECTION_VERSION = 1
PROJECTION_SCHEMA = "ueak.graphlayoutprojection/1.0"
DEFAULT_PAGE_LIMIT = 64
MIN_PAGE_LIMIT = 8
MAX_PAGES = 1024
MAX_PAGE_BYTES = 512 * 1024

_NODE_HASH_DOMAIN = "ueak.layout-projection.nodes/1"
_PIN_HASH_DOMAIN = "ueak.layout-projection.pins/1"
_EDGE_HASH_DOMAIN = "ueak.layout-projection.edges/1"
_PROJECTION_HASH_DOMAIN = "ueak.layout-projection/1"


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise LiveEditorError(code, message)


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _hash_lines(domain: str, lines: list[str]) -> str:
    return _sha256_text(domain + "\n" + "".join(line + "\n" for line in sorted(lines)))


def projection_hashes(
    nodes: list[Mapping[str, Any]],
    pins: list[Mapping[str, Any]],
    edges: list[Mapping[str, Any]],
) -> dict[str, str]:
    """Recompute the page-independent hashes carried by the native projection."""

    node_lines = [
        "N\t{guid}\t{classPath}\t{kind}\t{x}\t{y}\t{width}\t{height}".format(
            guid=str(node.get("guid", "")),
            classPath=str(node.get("classPath", "")),
            kind=str(node.get("kind", "")),
            x=int(node.get("x", 0)),
            y=int(node.get("y", 0)),
            width=int(node.get("width", 0)),
            height=int(node.get("height", 0)),
        )
        for node in nodes
    ]
    pin_lines = [
        "P\t{pinId}\t{nodeGuid}\t{direction}\t{kind}\t{typeCategory}\t{typeKey}\t{hidden}\t{advanced}".format(
            pinId=str(pin.get("pinId", "")),
            nodeGuid=str(pin.get("nodeGuid", "")),
            direction=str(pin.get("direction", "")),
            kind=str(pin.get("kind", "")),
            typeCategory=str(pin.get("typeCategory", "")),
            typeKey=str(pin.get("typeKey", "")),
            hidden=1 if pin.get("hidden") is True else 0,
            advanced=1 if pin.get("advanced") is True else 0,
        )
        for pin in pins
    ]
    edge_lines = [
        "E\t{sourcePinId}\t{targetPinId}\t{kind}".format(
            sourcePinId=str(edge.get("sourcePinId", "")),
            targetPinId=str(edge.get("targetPinId", "")),
            kind=str(edge.get("kind", "")),
        )
        for edge in edges
    ]
    node_hash = _hash_lines(_NODE_HASH_DOMAIN, node_lines)
    pin_hash = _hash_lines(_PIN_HASH_DOMAIN, pin_lines)
    edge_hash = _hash_lines(_EDGE_HASH_DOMAIN, edge_lines)
    projection_hash = _sha256_text(
        _PROJECTION_HASH_DOMAIN + "\n" + node_hash + "\n" + pin_hash + "\n" + edge_hash
    )
    return {
        "nodeSetHash": node_hash,
        "pinSetHash": pin_hash,
        "edgeSetHash": edge_hash,
        "projectionHash": projection_hash,
    }


def _page_size(page: Mapping[str, Any]) -> int:
    # Native enforces the byte budget; this mirrors its compact JSON encoding
    # closely enough to report the largest page in evidence.
    return len(json.dumps(page, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def fetch_layout_projection(
    service: LiveEditorBridgeService,
    preflight: Mapping[str, Any],
    *,
    page_limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Fetch and verify every page of the exact focused graph projection."""

    _require(
        isinstance(page_limit, int) and not isinstance(page_limit, bool) and 1 <= page_limit <= 256,
        "graph-layout-projection-page-invalid",
        "page_limit must be an integer from 1 through 256.",
    )
    revision = str(preflight.get("packageRevision") or "")
    fingerprint = str(preflight.get("graphFingerprint") or "")
    asset_path = str(preflight.get("assetPath") or "")
    graph_guid = str(preflight.get("graphGuid") or "")
    _require(bool(revision), "graph-layout-projection-invalid", "preflight has no package revision.")
    _require(bool(fingerprint), "graph-layout-projection-invalid", "preflight has no graph fingerprint.")
    _require(bool(asset_path), "graph-layout-projection-invalid", "preflight has no asset path.")
    _require(bool(graph_guid), "graph-layout-projection-invalid", "preflight has no graph GUID.")

    nodes: dict[str, dict[str, Any]] = {}
    pins: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    comments: dict[str, dict[str, Any]] = {}
    expected_identity: dict[str, Any] | None = None
    expected_hashes: dict[str, str] | None = None
    first_page_meta: dict[str, Any] = {}
    total_nodes = total_pins = total_edges = total_comments = -1
    cursor = 0
    page_count = 0
    limit = min(page_limit, 256)
    max_page_bytes = 0

    while True:
        page_count += 1
        _require(page_count <= MAX_PAGES, "graph-layout-projection-too-many-pages", "Projection exceeded the page bound.")
        params = {
            "offset": cursor,
            "limit": limit,
            "expectedPackageRevision": revision,
            "expectedGraphFingerprint": fingerprint,
        }
        try:
            page = service.call_method(PROJECTION_METHOD, params)
        except LiveEditorError as exc:
            if exc.code == "graph-layout-projection-page-too-large" and limit > MIN_PAGE_LIMIT:
                limit = max(MIN_PAGE_LIMIT, limit // 2)
                page_count -= 1
                continue
            raise

        _require(
            isinstance(page, Mapping) and page.get("available") is True and page.get("readOnly") is True,
            "graph-layout-projection-invalid",
            "The layout projection page is unavailable or not read-only.",
        )
        _require(
            page.get("packageDirty") is False,
            "graph-layout-package-dirty",
            "The package became dirty while reading the layout projection.",
        )
        _require(
            page.get("schemaVersion") == PROJECTION_SCHEMA
            and page.get("projectionVersion") == PROJECTION_VERSION,
            "graph-layout-projection-version-unsupported",
            "The layout projection schema/version is unsupported.",
        )
        page_identity = {
            "assetPath": page.get("assetPath"),
            "graphGuid": page.get("graphGuid"),
            "packageRevision": page.get("packageRevision"),
            "graphFingerprint": page.get("graphFingerprint"),
            "nodeCount": page.get("nodeCount"),
            "pinCount": page.get("pinCount"),
            "edgeCount": page.get("edgeCount"),
            "commentCount": page.get("commentCount"),
        }
        if expected_identity is None:
            expected_identity = page_identity
            first_page_meta = {
                "assetClass": page.get("assetClass"),
                "graphName": page.get("graphName"),
                "graphKind": page.get("graphKind"),
                "graphClassPath": page.get("graphClassPath"),
                "schemaClassPath": page.get("schemaClassPath"),
            }
            _require(
                page_identity["assetPath"] == asset_path
                and page_identity["graphGuid"] == graph_guid
                and page_identity["packageRevision"] == revision
                and page_identity["graphFingerprint"] == fingerprint,
                "graph-layout-projection-identity-mismatch",
                "The layout projection does not belong to the focused preflight.",
            )
            total_nodes = int(page.get("nodeCount", -1))
            total_pins = int(page.get("pinCount", -1))
            total_edges = int(page.get("edgeCount", -1))
            total_comments = int(page.get("commentCount", -1))
            _require(
                total_nodes > 0,
                "graph-layout-projection-incomplete",
                "The focused graph has no nodes.",
            )
            expected_hashes = {
                "nodeSetHash": str(page.get("nodeSetHash") or ""),
                "pinSetHash": str(page.get("pinSetHash") or ""),
                "edgeSetHash": str(page.get("edgeSetHash") or ""),
                "projectionHash": str(page.get("projectionHash") or ""),
            }
        else:
            _require(page_identity == expected_identity, "graph-layout-projection-stale", "Projection pages disagree on identity or counts.")
            page_hashes = {
                "nodeSetHash": str(page.get("nodeSetHash") or ""),
                "pinSetHash": str(page.get("pinSetHash") or ""),
                "edgeSetHash": str(page.get("edgeSetHash") or ""),
                "projectionHash": str(page.get("projectionHash") or ""),
            }
            _require(page_hashes == expected_hashes, "graph-layout-projection-stale", "Projection hashes changed between pages.")

        _require(
            int(page.get("offset", -1)) == cursor,
            "graph-layout-projection-page-invalid",
            "The projection page offset is not the requested cursor.",
        )
        page_nodes = page.get("nodes")
        page_pins = page.get("pins")
        page_edges = page.get("edges")
        page_comments = page.get("comments")
        _require(
            isinstance(page_nodes, list) and isinstance(page_pins, list) and isinstance(page_edges, list) and isinstance(page_comments, list),
            "graph-layout-projection-invalid",
            "A projection page is missing an array field.",
        )
        _require(
            int(page.get("returnedNodeCount", -1)) == len(page_nodes),
            "graph-layout-projection-incomplete",
            "The projection page returnedNodeCount does not equal its node array.",
        )
        max_page_bytes = max(max_page_bytes, _page_size(page))
        for node in page_nodes:
            _require(isinstance(node, Mapping), "graph-layout-projection-invalid", "A projected node is not an object.")
            node_id = str(node.get("nodeId") or "")
            _require(bool(node_id) and node_id not in nodes, "graph-layout-projection-incomplete", "Projected node IDs are empty or duplicated.")
            nodes[node_id] = dict(node)
        for pin in page_pins:
            _require(isinstance(pin, Mapping), "graph-layout-projection-invalid", "A projected pin is not an object.")
            pin_id = str(pin.get("pinId") or "")
            _require(bool(pin_id) and pin_id not in pins, "graph-layout-projection-incomplete", "Projected pin IDs are empty or duplicated.")
            pins[pin_id] = dict(pin)
        for edge in page_edges:
            _require(isinstance(edge, Mapping), "graph-layout-projection-invalid", "A projected edge is not an object.")
            edge_key = str(edge.get("sourcePinId") or "") + "|" + str(edge.get("targetPinId") or "")
            _require(edge_key not in edges, "graph-layout-projection-incomplete", "Projected edges are duplicated.")
            edges[edge_key] = dict(edge)
        for comment in page_comments:
            _require(isinstance(comment, Mapping), "graph-layout-projection-invalid", "A projected comment is not an object.")
            node_id = str(comment.get("nodeId") or "")
            _require(node_id not in comments, "graph-layout-projection-incomplete", "Projected comments are duplicated.")
            comments[node_id] = dict(comment)

        has_more = page.get("hasMore")
        _require(isinstance(has_more, bool), "graph-layout-projection-invalid", "Projection hasMore is not boolean.")
        next_offset = int(page.get("nextOffset", -1))
        if has_more:
            _require(next_offset > cursor and next_offset <= total_nodes, "graph-layout-projection-page-invalid", "Projection nextOffset made no progress.")
            cursor = next_offset
            continue
        _require(len(page_nodes) <= limit, "graph-layout-projection-page-invalid", "Projection page exceeded its requested limit.")
        break

    _require(len(nodes) == total_nodes, "graph-layout-projection-incomplete", "Projection node set is incomplete.")
    _require(len(pins) == total_pins, "graph-layout-projection-incomplete", "Projection pin set is incomplete.")
    _require(len(edges) == total_edges, "graph-layout-projection-incomplete", "Projection edge set is incomplete.")
    _require(len(comments) == total_comments, "graph-layout-projection-incomplete", "Projection comment set is incomplete.")

    for pin in pins.values():
        _require(str(pin.get("nodeId") or "") in nodes, "graph-layout-projection-incomplete", "A projected pin refers to a missing node.")
    for edge in edges.values():
        source = str(edge.get("sourcePinId") or "")
        target = str(edge.get("targetPinId") or "")
        _require(source in pins and target in pins, "graph-layout-projection-incomplete", "A projected edge refers to a missing pin.")
    for comment in comments.values():
        _require(str(comment.get("nodeId") or "") in nodes, "graph-layout-projection-incomplete", "A projected comment refers to a missing node.")

    recomputed = projection_hashes(list(nodes.values()), list(pins.values()), list(edges.values()))
    _require(recomputed == expected_hashes, "graph-layout-projection-hash-mismatch", "The concatenated projection does not match the native whole-graph hashes.")

    # Re-read the exact focused preflight after paging.  This closes the window
    # where the graph or package changes between page 0 and the last page.
    after = service.call_method("editor.getBlueprintGraphLayoutPreflight", {})
    _require(
        isinstance(after, Mapping) and after.get("available") is True,
        "graph-layout-projection-stale",
        "The focused graph preflight disappeared while paging.",
    )
    _require(
        after.get("assetPath") == asset_path
        and after.get("graphGuid") == graph_guid
        and after.get("packageRevision") == revision
        and after.get("graphFingerprint") == fingerprint
        and int(after.get("nodeCount", -1)) == total_nodes,
        "graph-layout-projection-stale",
        "The focused graph identity, revision, fingerprint or node count changed while paging.",
    )
    _require(
        after.get("packageDirty") is False,
        "graph-layout-package-dirty",
        "The package became dirty while reading the layout projection.",
    )
    _require(
        after.get("pieState") == "stopped",
        "graph-layout-pie-active",
        "PIE/SIE started while reading the layout projection.",
    )

    assert expected_identity is not None and expected_hashes is not None
    return {
        "schemaVersion": PROJECTION_SCHEMA,
        "projectionVersion": PROJECTION_VERSION,
        "readOnly": True,
        "assetPath": asset_path,
        "assetClass": first_page_meta.get("assetClass") or "",
        "graphGuid": graph_guid,
        "graphName": first_page_meta.get("graphName") or "",
        "graphKind": first_page_meta.get("graphKind") or "function",
        "graphClassPath": first_page_meta.get("graphClassPath") or "",
        "schemaClassPath": first_page_meta.get("schemaClassPath") or "",
        "packageRevision": revision,
        "packageDirty": False,
        "graphFingerprint": fingerprint,
        "nodeCount": total_nodes,
        "pinCount": total_pins,
        "edgeCount": total_edges,
        "commentCount": total_comments,
        **expected_hashes,
        "pageCount": page_count,
        "pageLimit": limit,
        "maxPageBytes": max_page_bytes,
        "nodes": [nodes[key] for key in sorted(nodes)],
        "pins": [pins[key] for key in sorted(pins)],
        "edges": [edges[key] for key in sorted(edges)],
        "comments": [comments[key] for key in sorted(comments)],
    }


def _pin_type(pin: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(pin.get("kind") or "data")
    return {
        "category": "exec" if kind == "exec" else "data",
        "subcategory": str(pin.get("typeCategory") or ""),
        "subcategoryObject": str(pin.get("typeKey") or ""),
        "container": "none",
        "isReference": False,
        "isConst": False,
        "isWeakPointer": False,
    }


def projection_to_canonical_document(projection: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruct the minimal canonical export document accepted by the importer."""

    node_by_id = {str(node["nodeId"]): node for node in projection["nodes"]}
    pin_by_id = {str(pin["pinId"]): pin for pin in projection["pins"]}
    links: dict[str, list[dict[str, str]]] = {pin_id: [] for pin_id in pin_by_id}
    for edge in projection["edges"]:
        source_id = str(edge["sourcePinId"])
        target_id = str(edge["targetPinId"])
        source = pin_by_id[source_id]
        target = pin_by_id[target_id]
        links[source_id].append(
            {"targetPinId": target_id, "targetNodeGuid": str(target["nodeGuid"])}
        )
        links[target_id].append(
            {"targetPinId": source_id, "targetNodeGuid": str(source["nodeGuid"])}
        )

    pins_by_node: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in node_by_id}
    for pin in projection["pins"]:
        pins_by_node[str(pin["nodeId"])].append(
            {
                "id": str(pin["pinId"]),
                "name": "pin_" + str(pin["pinId"])[:8],
                "friendlyName": "",
                "direction": str(pin["direction"]),
                "type": _pin_type(pin),
                "defaultValue": "",
                "autogeneratedDefaultValue": "",
                "defaultObject": "",
                "defaultTextValue": "",
                "hidden": bool(pin.get("hidden")),
                "notConnectable": False,
                "defaultValueReadOnly": False,
                "defaultValueIgnored": False,
                "orphaned": False,
                "advancedView": bool(pin.get("advanced")),
                "links": sorted(links[str(pin["pinId"])], key=lambda item: item["targetPinId"]),
            }
        )
    for values in pins_by_node.values():
        values.sort(key=lambda item: item["id"])

    comment_by_node = {str(item["nodeId"]): item for item in projection.get("comments", [])}
    raw_nodes: list[dict[str, Any]] = []
    for node_id in sorted(node_by_id):
        node = node_by_id[node_id]
        raw_nodes.append(
            {
                "guid": str(node["guid"]),
                "name": "Node_" + str(node["guid"])[:8],
                "class": str(node["classPath"]),
                "title": "",
                "comment": str(comment_by_node.get(node_id, {}).get("text", "")),
                "layout": {
                    "x": int(node["x"]),
                    "y": int(node["y"]),
                    "width": int(node["width"]),
                    "height": int(node["height"]),
                },
                "pins": pins_by_node[node_id],
            }
        )

    graph = {
        "guid": str(projection["graphGuid"]),
        "name": str(projection["graphName"]),
        "kind": str(projection.get("graphKind") or "function"),
        "schema": str(projection.get("schemaClassPath") or "/Script/BlueprintGraph.EdGraphSchema_K2"),
        "nodeCount": len(raw_nodes),
        "nodes": raw_nodes,
    }
    return {
        "schemaVersion": "1.1",
        "engineVersion": "5.6.1",
        "assetPath": str(projection["assetPath"]),
        "assetClass": str(projection.get("assetClass") or "/Script/Engine.Blueprint"),
        "revision": {
            "available": True,
            "value": str(projection["packageRevision"]),
            "packageDirty": False,
            "strategy": "package-sha256-v1",
        },
        "graphs": [graph],
    }


def build_preview_from_projection(
    preflight: Mapping[str, Any],
    projection: Mapping[str, Any],
    *,
    mode: str = "full",
    scope_node_ids: list[str] | None = None,
    selection_payload: Mapping[str, Any] | None = None,
    spacing_x: int = 96,
    spacing_y: int = 64,
    target_width: int = 1800,
    seed: int = 0,
) -> dict[str, Any]:
    """Build the normal LayoutPlan from the verified projection pages."""

    _require(
        projection.get("nodeCount") == len(projection.get("nodes", [])),
        "graph-layout-projection-incomplete",
        "The projection node count is not the concatenated node set.",
    )
    document = projection_to_canonical_document(projection)
    envelope = {
        "available": True,
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "assetPath": projection["assetPath"],
        "graphGuid": projection["graphGuid"],
        "packageRevision": projection["packageRevision"],
        "packageDirty": False,
        "graphFingerprint": projection["graphFingerprint"],
        "exportedAtUtc": datetime.now(timezone.utc).isoformat(),
        "context": document,
    }
    result = build_preview(
        preflight,
        envelope,
        mode=mode,
        scope_node_ids=scope_node_ids,
        selection_payload=selection_payload,
        spacing_x=spacing_x,
        spacing_y=spacing_y,
        target_width=target_width,
        seed=seed,
    )
    result["contextMode"] = "layout-projection"
    result["projection"] = {
        "version": PROJECTION_VERSION,
        "pageCount": projection.get("pageCount"),
        "pageLimit": projection.get("pageLimit"),
        "maxPageBytes": projection.get("maxPageBytes"),
        "nodeSetHash": projection.get("nodeSetHash"),
        "pinSetHash": projection.get("pinSetHash"),
        "edgeSetHash": projection.get("edgeSetHash"),
        "projectionHash": projection.get("projectionHash"),
    }
    return result


__all__ = [
    "DEFAULT_PAGE_LIMIT",
    "MAX_PAGE_BYTES",
    "PROJECTION_METHOD",
    "PROJECTION_SCHEMA",
    "PROJECTION_VERSION",
    "build_preview_from_projection",
    "fetch_layout_projection",
    "projection_hashes",
    "projection_to_canonical_document",
]

"""Strict importer for ``BlueprintContextExporter`` canonical JSON.

The exporter's *Graph Selection* payload is intentionally not accepted here:
it contains only selected node ids and ``NodePosX/Y`` and has no pins or links,
so it cannot prove graph topology.  The full canonical export (``Profile=full``)
does contain node GUIDs, layout, pins, pin links and the package revision.  This
module converts that payload into TypedGraph 1.0 without inventing an edge:

* nodes come from ``nodes[].guid`` + ``layout``;
* pins come from ``nodes[].pins[].id``;
* edges are derived only from real output-pin ``links[]``;
* every link must resolve to an input pin owned by the declared target node;
* input and output link lists must mirror exactly;
* missing, duplicate, truncated or ambiguous data is rejected.

The exporter does not currently expose a node-enabled bit or pin-id provenance.
The imported graph therefore uses ``enabled=true`` and ``pinIdSource="native"``
as *transport* defaults.  Its ``semanticHash`` is stable for the exported data
but is **not** proof of the live UE object; a future Apply must re-read and
verify those fields in the Editor.  This importer is read-only and never opens
Unreal or writes an asset.
"""

from __future__ import annotations

import copy
import hashlib
from typing import Any, Mapping

from . import ids, typed_graph

EXPORTER_SCHEMA_VERSION = "1.1"
GRAPH_KIND_MAP = {
    "uber": "ubergraph",
    "ubergraph": "ubergraph",
    "function": "function",
    "macro": "macro",
    "delegate-signature": "delegate-signature",
    "event": "event",
    "other": "other",
}
SOURCE_KINDS = ("live-editor", "commandlet-export", "test-fixture")


class BlueprintContextImportError(ValueError):
    """A fail-closed importer diagnostic; callers must not build a plan."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str = "$",
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.path = path
        self.context = context or {}
        # Import errors are input-boundary failures, not contract diagnostics
        # embedded in a graph.  Keep the same shape without requiring every
        # transport error to become a frozen contract error code.
        self.diagnostic = {
            "code": code,
            "severity": "error",
            "message": message,
            "path": path,
        }
        if self.context:
            self.diagnostic["context"] = self.context


def _fail(code: str, message: str, path: str = "$", context: dict[str, Any] | None = None) -> None:
    raise BlueprintContextImportError(code, message, path=path, context=context)


def _dict(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("blueprint-context-shape-invalid", "expected a JSON object", path)
    return value


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        _fail("blueprint-context-shape-invalid", "expected a non-empty string", path)
    return value


def _integer(value: Any, path: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        _fail("blueprint-context-shape-invalid", "expected an integer", path)
    if minimum is not None and value < minimum:
        _fail("blueprint-context-layout-invalid", f"value must be >= {minimum}", path)
    if maximum is not None and value > maximum:
        _fail("blueprint-context-layout-invalid", f"value must be <= {maximum}", path)
    return value


def _guid(value: Any, path: str) -> str:
    try:
        return ids.normalize_guid(_string(value, path))
    except ValueError:
        _fail("blueprint-context-identity-invalid", "expected a lowercase GUID", path)


def _pin_id(value: Any, path: str) -> str:
    return ids.pin_id(_guid(value, path))


def _node_id(value: Any, path: str) -> str:
    return ids.node_id(_guid(value, path))


def _bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _source_metadata(
    document: Mapping[str, Any],
    supplied: Mapping[str, Any] | None,
) -> dict[str, Any]:
    source = dict(supplied or {})
    for key in ("sessionId", "pluginBuildHash", "exportedAtUtc"):
        if key not in source and isinstance(document.get(key), str):
            source[key] = document[key]
    kind = source.get("kind", "commandlet-export")
    if kind not in SOURCE_KINDS:
        _fail("blueprint-context-source-invalid", f"unknown source kind: {kind!r}", "$.source.kind")
    if not isinstance(source.get("sessionId"), str) or not source["sessionId"]:
        _fail("blueprint-context-source-invalid", "source.sessionId is required", "$.source.sessionId")
    if not isinstance(source.get("pluginBuildHash"), str) or not source["pluginBuildHash"]:
        _fail("blueprint-context-source-invalid", "source.pluginBuildHash is required", "$.source.pluginBuildHash")
    if not isinstance(source.get("exportedAtUtc"), str) or not source["exportedAtUtc"]:
        _fail("blueprint-context-source-invalid", "source.exportedAtUtc is required", "$.source.exportedAtUtc")
    session_id = source["sessionId"]
    build_hash = source["pluginBuildHash"]
    if not (
        len(build_hash) == 16
        and all(character in "0123456789abcdef" for character in build_hash)
    ) and not typed_graph.is_sha256(build_hash):
        _fail(
            "blueprint-context-source-invalid",
            "pluginBuildHash must be 16 lowercase hex chars or sha256:<64 lowercase hex>",
            "$.source.pluginBuildHash",
        )
    exported_at = _string(source.get("exportedAtUtc"), "$.source.exportedAtUtc")
    return {
        "kind": kind,
        "sessionId": session_id,
        "exporterSchemaVersion": EXPORTER_SCHEMA_VERSION,
        "pluginBuildHash": build_hash,
        "engineVersion": _string(document.get("engineVersion"), "$.engineVersion"),
        "exportedAtUtc": exported_at,
    }


def _select_graph(document: dict[str, Any], graph_guid: str | None) -> dict[str, Any]:
    graphs = document.get("graphs")
    if not isinstance(graphs, list) or not graphs:
        _fail("blueprint-context-graph-missing", "canonical export has no graphs array", "$.graphs")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(graphs):
        graph = _dict(raw, f"$.graphs[{index}]")
        guid = _guid(graph.get("guid"), f"$.graphs[{index}].guid")
        if guid in seen:
            _fail("blueprint-context-graph-ambiguous", "duplicate graph GUID", f"$.graphs[{index}].guid")
        seen.add(guid)
        records.append(graph)
    if graph_guid is None:
        if len(records) != 1:
            _fail(
                "blueprint-context-graph-ambiguous",
                "multiple graphs; pass graph_guid to choose exactly one",
                "$.graphs",
                {"graphCount": len(records)},
            )
        return records[0]
    try:
        wanted = ids.normalize_guid(graph_guid[2:] if graph_guid.startswith("g:") else graph_guid)
    except ValueError:
        _fail("blueprint-context-identity-invalid", "graph_guid must be a GUID", "$.graph_guid")
    matches = [item for item in records if item["guid"] == wanted]
    if not matches:
        _fail("blueprint-context-graph-not-found", "graph_guid is not present in the export", "$.graph_guid")
    return matches[0]


def _layout(raw: dict[str, Any], path: str) -> tuple[int, int, int, int]:
    layout = _dict(raw.get("layout"), path + ".layout")
    x = _integer(layout.get("x"), path + ".layout.x")
    y = _integer(layout.get("y"), path + ".layout.y")
    # UE can export NodeWidth/NodeHeight as 0 before a widget has measured the
    # node.  0 is the contract's "unknown" value; layout will estimate it.
    width = _integer(layout.get("width"), path + ".layout.width", minimum=0)
    height = _integer(layout.get("height"), path + ".layout.height", minimum=0)
    return x, y, width, height


def _pin_type(raw: dict[str, Any], path: str) -> dict[str, Any]:
    pin_type = _dict(raw.get("type"), path + ".type")
    return {
        "category": _string(pin_type.get("category", ""), path + ".type.category", allow_empty=True),
        "subCategory": _string(pin_type.get("subcategory", ""), path + ".type.subcategory", allow_empty=True),
        "subCategoryObject": _string(
            pin_type.get("subcategoryObject", ""),
            path + ".type.subcategoryObject",
            allow_empty=True,
        ),
        "container": _string(pin_type.get("container", "none"), path + ".type.container", allow_empty=True),
        "isReference": _bool(pin_type.get("isReference")),
        "isConst": _bool(pin_type.get("isConst")),
        "isWeakPointer": _bool(pin_type.get("isWeakPointer")),
    }


def _pin_flags(raw: dict[str, Any]) -> dict[str, bool]:
    return {
        "hidden": _bool(raw.get("hidden")),
        "advanced": _bool(raw.get("advancedView")),
        "connectable": not _bool(raw.get("notConnectable")),
        "orphaned": _bool(raw.get("orphaned")),
        "defaultReadOnly": _bool(raw.get("defaultValueReadOnly")),
        "defaultIgnored": _bool(raw.get("defaultValueIgnored")),
    }


def _pin_defaults(raw: dict[str, Any]) -> dict[str, str]:
    return {
        "value": str(raw.get("defaultValue", "")) if isinstance(raw.get("defaultValue"), str) else "",
        "object": str(raw.get("defaultObject", "")) if isinstance(raw.get("defaultObject"), str) else "",
        "text": str(raw.get("defaultTextValue", "")) if isinstance(raw.get("defaultTextValue"), str) else "",
    }


def _contained_node_ids(bounds: tuple[int, int, int, int], nodes: dict[str, dict[str, Any]], comment_id: str) -> list[str]:
    x, y, width, height = bounds
    contained: list[str] = []
    for node_id, node in nodes.items():
        if node_id == comment_id or node.get("kind") == "comment":
            continue
        position = node["position"]
        size = node["size"]
        center_x = position["x"] + max(1, size["width"]) // 2
        center_y = position["y"] + max(1, size["height"]) // 2
        if x <= center_x <= x + width and y <= center_y <= y + height:
            contained.append(node_id)
    return sorted(contained)


def typed_graph_from_blueprint_context(
    document: Mapping[str, Any],
    *,
    graph_guid: str | None = None,
    source_metadata: Mapping[str, Any] | None = None,
    graph_editable: bool = False,
) -> dict[str, Any]:
    """Convert one canonical export graph to a hashed TypedGraph 1.0.

    ``Profile=full`` is required because ``layout`` is otherwise absent.  The
    caller must supply real export provenance via ``source_metadata`` (or the
    document's own ``sessionId`` / ``pluginBuildHash`` / ``exportedAtUtc``):
    fabricating a session or plugin build would defeat the Apply gates.

    ``graph_editable`` defaults to ``False`` because the canonical JSON does not
    prove editability.  A future Editor Apply must establish it separately.
    """

    if not isinstance(document, Mapping):
        _fail("blueprint-context-shape-invalid", "document must be a JSON object", "$")
    doc = copy.deepcopy(dict(document))
    version = _string(doc.get("schemaVersion"), "$.schemaVersion")
    if version != EXPORTER_SCHEMA_VERSION:
        _fail(
            "blueprint-context-version-unsupported",
            f"only exporter schemaVersion {EXPORTER_SCHEMA_VERSION!r} is supported",
            "$.schemaVersion",
            {"actual": version},
        )
    if "truncated" in doc and not isinstance(doc.get("truncated"), bool):
        _fail("blueprint-context-shape-invalid", "truncated must be boolean", "$.truncated")
    if doc.get("truncated") is True:
        _fail("blueprint-context-truncated", "truncated exports are not layout inputs", "$.truncated")
    if not isinstance(graph_editable, bool):
        _fail("blueprint-context-shape-invalid", "graph_editable must be boolean", "$.graph_editable")

    graph_raw = _select_graph(doc, graph_guid)
    graph_guid_value = _guid(graph_raw.get("guid"), "$.graphs[].guid")
    if graph_raw.get("truncated") is True:
        _fail("blueprint-context-truncated", "truncated graphs are not layout inputs", "$.graphs[].truncated")
    graph_name = _string(graph_raw.get("name"), "$.graphs[].name")
    graph_schema = _string(graph_raw.get("schema"), "$.graphs[].schema")
    graph_kind_raw = _string(graph_raw.get("kind"), "$.graphs[].kind")
    if graph_kind_raw not in GRAPH_KIND_MAP:
        _fail("blueprint-context-graph-kind-unsupported", f"unknown graph kind: {graph_kind_raw!r}", "$.graphs[].kind")

    nodes_raw = graph_raw.get("nodes")
    if not isinstance(nodes_raw, list):
        _fail(
            "blueprint-context-nodes-missing",
            "canonical export has no nodes; re-export with Profile=full",
            "$.graphs[].nodes",
        )
    node_count = graph_raw.get("nodeCount")
    if isinstance(node_count, int) and not isinstance(node_count, bool) and node_count != len(nodes_raw):
        _fail(
            "blueprint-context-truncated",
            "nodeCount does not match the exported node array",
            "$.graphs[].nodeCount",
            {"nodeCount": node_count, "exported": len(nodes_raw)},
        )

    asset_path_value = _string(doc.get("assetPath"), "$.assetPath")
    if not ids.is_asset_path(asset_path_value):
        _fail("blueprint-context-identity-invalid", "assetPath must be a /Game/... object path", "$.assetPath")
    asset_class = _string(doc.get("assetClass"), "$.assetClass")
    revision = _dict(doc.get("revision"), "$.revision")
    if revision.get("available") is not True:
        _fail("blueprint-context-revision-missing", "package revision is unavailable", "$.revision.available")
    revision_value = _string(revision.get("value"), "$.revision.value")
    if not typed_graph.is_sha256(revision_value):
        _fail(
            "blueprint-context-revision-weak",
            "layout input requires a sha256 package revision; guid-only revisions are rejected",
            "$.revision.value",
        )
    dirty = revision.get("packageDirty")
    if not isinstance(dirty, bool):
        _fail("blueprint-context-shape-invalid", "revision.packageDirty must be boolean", "$.revision.packageDirty")

    source = _source_metadata(doc, source_metadata)
    source["exporterSchemaVersion"] = version

    nodes: dict[str, dict[str, Any]] = {}
    pins_by_id: dict[str, dict[str, Any]] = {}
    pins_by_node: dict[str, list[dict[str, Any]]] = {}
    comments: list[dict[str, Any]] = []

    for node_index, raw_node_value in enumerate(nodes_raw):
        path = f"$.graphs[].nodes[{node_index}]"
        raw_node = _dict(raw_node_value, path)
        node_guid = _guid(raw_node.get("guid"), path + ".guid")
        node_id = _node_id(node_guid, path + ".guid")
        if node_id in nodes:
            _fail("blueprint-context-duplicate-node", "duplicate node GUID", path + ".guid")
        class_path = _string(raw_node.get("class"), path + ".class")
        x, y, width, height = _layout(raw_node, path)
        kind = typed_graph.classify_node_class(class_path)
        node = {
            "nodeId": node_id,
            "nodeGuid": node_guid,
            "classPath": class_path,
            "title": raw_node.get("title", "") if isinstance(raw_node.get("title"), str) else "",
            "kind": kind,
            "nodeIdSource": "guid",
            "flags": {
                "enabled": True,
                "pure": False,
                "reroute": kind == "reroute",
                "comment": kind == "comment",
                "latent": False,
            },
            "position": {"x": x, "y": y},
            "size": {
                "width": width,
                "height": height,
                "source": "stored" if width > 0 and height > 0 else "unknown",
            },
            "pins": [],
        }
        nodes[node_id] = node
        pins_by_node[node_id] = []

        raw_pins = raw_node.get("pins")
        if not isinstance(raw_pins, list):
            _fail("blueprint-context-pins-missing", "node has no pins array", path + ".pins")
        for pin_index, raw_pin_value in enumerate(raw_pins):
            pin_path = f"{path}.pins[{pin_index}]"
            raw_pin = _dict(raw_pin_value, pin_path)
            pin_id = _pin_id(raw_pin.get("id"), pin_path + ".id")
            if pin_id in pins_by_id:
                _fail(
                    "blueprint-context-duplicate-pin",
                    "duplicate pin GUID; the exporter cannot disambiguate this for layout",
                    pin_path + ".id",
                )
            name = _string(raw_pin.get("name"), pin_path + ".name")
            direction = _string(raw_pin.get("direction"), pin_path + ".direction")
            if direction not in ("input", "output"):
                _fail("blueprint-context-shape-invalid", "pin.direction must be input or output", pin_path + ".direction")
            pin_type = _pin_type(raw_pin, pin_path)
            pin_kind = "exec" if pin_type["category"] == "exec" else "data"
            links = raw_pin.get("links")
            if not isinstance(links, list):
                _fail("blueprint-context-links-missing", "pin has no links array", pin_path + ".links")
            normalized_links: list[tuple[str, str]] = []
            seen_targets: set[str] = set()
            for link_index, link_value in enumerate(links):
                link_path = f"{pin_path}.links[{link_index}]"
                link = _dict(link_value, link_path)
                target_pin = _pin_id(link.get("targetPinId"), link_path + ".targetPinId")
                target_node = _node_id(link.get("targetNodeGuid"), link_path + ".targetNodeGuid")
                if target_pin in seen_targets:
                    _fail("blueprint-context-duplicate-link", "duplicate link target", link_path)
                seen_targets.add(target_pin)
                normalized_links.append((target_node, target_pin))
            pin = {
                "pinId": pin_id,
                "nodeId": node_id,
                "name": name,
                "friendlyName": raw_pin.get("friendlyName", "")
                if isinstance(raw_pin.get("friendlyName"), str)
                else "",
                "direction": direction,
                "kind": pin_kind,
                "type": pin_type,
                "index": pin_index,
                "defaults": _pin_defaults(raw_pin),
                "flags": _pin_flags(raw_pin),
                "links": [],
                "_rawLinks": normalized_links,
            }
            pins_by_id[pin_id] = pin
            pins_by_node[node_id].append(pin)
            node["pins"].append(pin_id)

    for node in nodes.values():
        node["pins"].sort()

    edges: list[dict[str, Any]] = []
    edge_keys: set[tuple[str, str]] = set()
    for pin in sorted(pins_by_id.values(), key=lambda item: item["pinId"]):
        if pin["direction"] != "output":
            continue
        for target_node_id, target_pin_id in pin["_rawLinks"]:
            if target_pin_id not in pins_by_id:
                _fail(
                    "blueprint-context-link-dangling",
                    "link target pin is not present in the exported graph",
                    "$.graphs[].nodes[].pins[].links",
                    {"from": pin["pinId"], "targetPinId": target_pin_id},
                )
            target = pins_by_id[target_pin_id]
            if target["nodeId"] != target_node_id:
                _fail(
                    "blueprint-context-link-node-mismatch",
                    "link targetNodeGuid does not own targetPinId",
                    "$.graphs[].nodes[].pins[].links",
                    {"targetPinId": target_pin_id, "targetNodeId": target_node_id},
                )
            if target["direction"] != "input":
                _fail(
                    "blueprint-context-link-direction",
                    "output pins may link only to input pins",
                    "$.graphs[].nodes[].pins[].links",
                    {"from": pin["pinId"], "targetPinId": target_pin_id},
                )
            if target["kind"] != pin["kind"]:
                _fail(
                    "blueprint-context-link-kind",
                    "linked pins must have the same exec/data kind",
                    "$.graphs[].nodes[].pins[].links",
                    {"from": pin["pinId"], "targetPinId": target_pin_id},
                )
            key = (pin["pinId"], target_pin_id)
            if key in edge_keys:
                _fail("blueprint-context-duplicate-edge", "duplicate pin link produced the same edge", "$.edges")
            edge_keys.add(key)
            edges.append(
                {
                    "edgeId": ids.edge_id(pin["pinId"], target_pin_id),
                    "from": {"nodeId": pin["nodeId"], "pinId": pin["pinId"]},
                    "to": {"nodeId": target["nodeId"], "pinId": target_pin_id},
                    "kind": pin["kind"],
                    "origin": "pin-link",
                    "status": "resolved",
                }
            )

    # Reconcile every exported link in both directions.  This rejects a
    # one-sided link list rather than silently creating or dropping an edge.
    edge_pairs = {(edge["from"]["pinId"], edge["to"]["pinId"]) for edge in edges}
    for pin in pins_by_id.values():
        expected = {
            target_pin
            for source_pin, target_pin in edge_pairs
            if source_pin == pin["pinId"]
        } | {
            source_pin
            for source_pin, target_pin in edge_pairs
            if target_pin == pin["pinId"]
        }
        raw_targets = {target_pin for _, target_pin in pin.pop("_rawLinks")}
        if raw_targets != expected:
            _fail(
                "blueprint-context-link-asymmetry",
                "pin links are not mirrored by the derived edge set",
                "$.graphs[].nodes[].pins[].links",
                {"pinId": pin["pinId"], "exported": sorted(raw_targets), "derived": sorted(expected)},
            )
        pin["links"] = sorted(expected)

    for node_id, node in sorted(nodes.items()):
        if node["kind"] != "comment":
            continue
        raw_node = next(
            raw for raw in nodes_raw
            if isinstance(raw, dict) and isinstance(raw.get("guid"), str)
            and ids.normalize_guid(raw["guid"]) == node["nodeGuid"]
        )
        bounds = (
            node["position"]["x"],
            node["position"]["y"],
            node["size"]["width"],
            node["size"]["height"],
        )
        comments.append(
            {
                "nodeId": node_id,
                "text": raw_node.get("comment", "") if isinstance(raw_node.get("comment"), str) else "",
                "bounds": {"x": bounds[0], "y": bounds[1], "width": bounds[2], "height": bounds[3]},
                "containedNodeIds": _contained_node_ids(bounds, nodes, node_id),
            }
        )

    graph = {
        "schemaVersion": typed_graph.TYPEDGRAPH_VERSION,
        "graphId": ids.graph_id(graph_guid_value),
        "asset": {
            "path": asset_path_value,
            "class": asset_class,
            "revision": revision_value,
            "revisionPolicy": "index-snapshot",
            "dirty": dirty,
        },
        "graph": {
            "name": graph_name,
            "guid": graph_guid_value,
            "kind": GRAPH_KIND_MAP[graph_kind_raw],
            "schema": graph_schema,
            "editable": bool(graph_editable),
        },
        "source": source,
        "limits": {
            "maxNodes": max(1, len(nodes)),
            "maxPins": max(1, len(pins_by_id)),
            "includedNodeCount": len(nodes),
            "omittedNodeCount": 0,
            "selection": "nodeId-ascending",
            "truncated": False,
        },
        "nodes": [nodes[key] for key in sorted(nodes)],
        "pins": [pins_by_id[key] for key in sorted(pins_by_id)],
        "edges": sorted(edges, key=lambda item: item["edgeId"]),
        "comments": sorted(comments, key=lambda item: item["nodeId"]),
        "diagnostics": [],
    }
    frozen = typed_graph.freeze_hashes(graph)
    report = typed_graph.validate(frozen, require_hashes=True, require_apply_ready=False)
    if not report.ok:
        _fail(
            "blueprint-context-typedgraph-invalid",
            "imported data failed TypedGraph validation",
            "$",
            {"diagnostics": report.diagnostics},
        )
    return frozen


__all__ = [
    "BlueprintContextImportError",
    "EXPORTER_SCHEMA_VERSION",
    "GRAPH_KIND_MAP",
    "typed_graph_from_blueprint_context",
]
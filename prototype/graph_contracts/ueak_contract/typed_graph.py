"""TypedGraph 1.0 candidate: validation, builder invariants and hashes.

Clause IDs: TG-HASH-WHITELIST, TG-REVISION-POLICY, TG-PIN-INDEX, TG-DERIVED-PIN,
TG-TRUNCATION, TG-EXPORTER-VERSION, TG-EDGE-STATUS, ID-PIN-BUILDER.

Hash contract (TG-HASH-WHITELIST): only the whitelisted projection below is
hashed.  In particular ``title``, ``menuName``, ``friendlyName``, ``category``,
``keywords``, ``objectPath``, coordinates, sizes, timestamps, ``source`` and
``diagnostics`` never influence ``semanticHash``.  This is what makes the hash
stable across Chinese/English editors.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import ids
from .canonical import (
    DOMAIN_COMMENT,
    DOMAIN_LAYOUT,
    DOMAIN_NODE,
    DOMAIN_SEMANTIC,
    DOMAIN_SIZE,
    domain_hash,
    is_sha256,
)
from .errors import load_registry, make_diagnostic
from .schema import SchemaError, negotiate_version

TYPEDGRAPH_VERSION = "ueak.typedgraph/1.0"
TYPEDGRAPH_NAMESPACE = "ueak.typedgraph"
TYPEDGRAPH_SUPPORTED_MINOR = 0

NODE_KINDS = (
    "event",
    "call",
    "variable-get",
    "variable-set",
    "macro",
    "cast",
    "comment",
    "reroute",
    "other",
)
EDGE_KINDS = ("exec", "data")
EDGE_STATUSES = ("resolved", "dangling", "duplicate", "wrong-kind")
PIN_DIRECTIONS = ("input", "output")
PIN_ID_SOURCES = ("native", "derived")
REVISION_POLICIES = ("executor-computed", "index-snapshot")
SIZE_SOURCES = ("measured", "stored", "estimated", "unknown")

PIN_TYPE_FIELDS = (
    "category",
    "subCategory",
    "subCategoryObject",
    "container",
    "isReference",
    "isConst",
    "isWeakPointer",
)
PIN_DEFAULT_FIELDS = ("value", "object", "text")
PIN_FLAG_FIELDS = (
    "hidden",
    "advanced",
    "connectable",
    "orphaned",
    "defaultReadOnly",
    "defaultIgnored",
)


@dataclass
class ValidationReport:
    diagnostics: list[dict[str, Any]] = field(default_factory=list)

    @property
    def errors(self) -> list[dict[str, Any]]:
        return [item for item in self.diagnostics if item.get("severity") == "error"]

    @property
    def warnings(self) -> list[dict[str, Any]]:
        return [item for item in self.diagnostics if item.get("severity") == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def codes(self) -> list[str]:
        return [item["code"] for item in self.diagnostics]

    @property
    def primary_code(self) -> str:
        errors = self.errors
        return errors[0]["code"] if errors else ""

    def add(
        self,
        code: str,
        message: str,
        *,
        path: str = "$",
        severity: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.diagnostics.append(make_diagnostic(code, message, path=path, severity=severity, context=context))


def _pin_type(pin: dict[str, Any]) -> dict[str, Any]:
    raw = pin.get("type") if isinstance(pin.get("type"), dict) else {}
    result: dict[str, Any] = {}
    for name in PIN_TYPE_FIELDS:
        value = raw.get(name, False if name.startswith("is") else "")
        result[name] = bool(value) if name.startswith("is") else (value if isinstance(value, str) else "")
    return result


def _pin_defaults(pin: dict[str, Any]) -> dict[str, str]:
    raw = pin.get("defaults") if isinstance(pin.get("defaults"), dict) else {}
    return {name: raw.get(name, "") if isinstance(raw.get(name, ""), str) else "" for name in PIN_DEFAULT_FIELDS}


def _pin_flags(pin: dict[str, Any]) -> dict[str, bool]:
    raw = pin.get("flags") if isinstance(pin.get("flags"), dict) else {}
    defaults = {
        "hidden": False,
        "advanced": False,
        "connectable": True,
        "orphaned": False,
        "defaultReadOnly": False,
        "defaultIgnored": False,
    }
    return {name: bool(raw.get(name, defaults[name])) for name in PIN_FLAG_FIELDS}


def _node_flags(node: dict[str, Any]) -> dict[str, bool]:
    raw = node.get("flags") if isinstance(node.get("flags"), dict) else {}
    return {
        "enabled": bool(raw.get("enabled", True)),
        "pure": bool(raw.get("pure", False)),
        "reroute": bool(raw.get("reroute", False)),
        "comment": bool(raw.get("comment", False)),
        "latent": bool(raw.get("latent", False)),
    }


def pin_semantic_projection(pin: dict[str, Any]) -> dict[str, Any]:
    return {
        "pinId": pin.get("pinId", ""),
        "nodeId": pin.get("nodeId", ""),
        "name": pin.get("name", ""),
        "direction": pin.get("direction", ""),
        "kind": pin.get("kind", ""),
        "type": _pin_type(pin),
        "defaults": _pin_defaults(pin),
        "flags": _pin_flags(pin),
    }


def node_semantic_projection(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodeId": node.get("nodeId", ""),
        "nodeGuid": node.get("nodeGuid", ""),
        "classPath": node.get("classPath", ""),
        "kind": node.get("kind", ""),
        "flags": {"enabled": _node_flags(node)["enabled"]},
    }


def edge_semantic_projection(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "edgeId": edge.get("edgeId", ""),
        "fromPinId": (edge.get("from") or {}).get("pinId", ""),
        "toPinId": (edge.get("to") or {}).get("pinId", ""),
        "kind": edge.get("kind", ""),
    }


def _sorted_by(items: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(items, key=lambda item: str(item.get(key, "")))


def semantic_projection(graph: dict[str, Any]) -> dict[str, Any]:
    """The only projection allowed to feed ``semanticHash`` (TG-HASH-WHITELIST)."""

    return {
        "graphId": graph.get("graphId", ""),
        "graphSchema": (graph.get("graph") or {}).get("schema", ""),
        "nodes": [node_semantic_projection(node) for node in _sorted_by(graph.get("nodes", []), "nodeId")],
        "pins": [pin_semantic_projection(pin) for pin in _sorted_by(graph.get("pins", []), "pinId")],
        "edges": [edge_semantic_projection(edge) for edge in _sorted_by(graph.get("edges", []), "edgeId")],
    }


def node_projection(graph: dict[str, Any], node_id_value: str) -> dict[str, Any] | None:
    node = next((item for item in graph.get("nodes", []) if item.get("nodeId") == node_id_value), None)
    if node is None:
        return None
    pins = [pin_semantic_projection(pin) for pin in graph.get("pins", []) if pin.get("nodeId") == node_id_value]
    incident = [
        edge_semantic_projection(edge)
        for edge in graph.get("edges", [])
        if (edge.get("from") or {}).get("nodeId") == node_id_value
        or (edge.get("to") or {}).get("nodeId") == node_id_value
    ]
    return {
        "node": node_semantic_projection(node),
        "pins": sorted(pins, key=lambda item: item["pinId"]),
        "edges": sorted(incident, key=lambda item: item["edgeId"]),
    }


def semantic_hash(graph: dict[str, Any]) -> str:
    return domain_hash(semantic_projection(graph), DOMAIN_SEMANTIC)


def node_hash(graph: dict[str, Any], node_id_value: str) -> str:
    projection = node_projection(graph, node_id_value)
    if projection is None:
        raise KeyError(f"Unknown node: {node_id_value}")
    return domain_hash(projection, DOMAIN_NODE)


def layout_projection(graph: dict[str, Any]) -> list[dict[str, Any]]:
    positions = []
    for node in _sorted_by(graph.get("nodes", []), "nodeId"):
        position = node.get("position") if isinstance(node.get("position"), dict) else {}
        positions.append(
            {
                "nodeId": node.get("nodeId", ""),
                "x": int(position.get("x", 0)),
                "y": int(position.get("y", 0)),
            }
        )
    return positions


def layout_hash(graph: dict[str, Any]) -> str:
    return domain_hash(layout_projection(graph), DOMAIN_LAYOUT)


def size_projection(graph: dict[str, Any]) -> list[dict[str, Any]]:
    sizes = []
    for node in _sorted_by(graph.get("nodes", []), "nodeId"):
        size = node.get("size") if isinstance(node.get("size"), dict) else {}
        sizes.append(
            {
                "nodeId": node.get("nodeId", ""),
                "width": int(size.get("width", 0)),
                "height": int(size.get("height", 0)),
                "source": size.get("source", "unknown"),
            }
        )
    return sizes


def size_hash(graph: dict[str, Any]) -> str:
    return domain_hash(size_projection(graph), DOMAIN_SIZE)


def comment_projection(graph: dict[str, Any]) -> dict[str, Any]:
    """D-09: comment text is verified independently from semanticHash."""

    nodes = [
        {"nodeId": node.get("nodeId", ""), "comment": str(node.get("comment", ""))}
        for node in _sorted_by(graph.get("nodes", []), "nodeId")
    ]
    comments = [
        {"nodeId": comment.get("nodeId", ""), "text": str(comment.get("text", ""))}
        for comment in _sorted_by(graph.get("comments", []), "nodeId")
    ]
    return {"nodes": nodes, "comments": comments}


def comment_hash(graph: dict[str, Any]) -> str:
    return domain_hash(comment_projection(graph), DOMAIN_COMMENT)


def computed_hashes(graph: dict[str, Any]) -> dict[str, str]:
    hashes = {
        "semanticHash": semantic_hash(graph),
        "layoutHash": layout_hash(graph),
    }
    for node in graph.get("nodes", []):
        hashes["node:" + str(node.get("nodeId", ""))] = node_hash(graph, str(node.get("nodeId", "")))
    return hashes


def freeze_hashes(graph: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with all derived hash fields populated."""

    frozen = copy.deepcopy(graph)
    frozen["semanticHash"] = semantic_hash(frozen)
    frozen["layoutHash"] = layout_hash(frozen)
    for node in frozen.get("nodes", []):
        node["semanticHash"] = node_hash(frozen, str(node.get("nodeId", "")))
    return frozen


def classify_node_class(class_path: str) -> str:
    """CR-ID-04 node-kind determination table, shared by all consumers."""

    text = class_path or ""
    if text.endswith("EdGraphNode_Comment") or text.endswith("K2Node_Comment"):
        return "comment"
    if text.endswith("K2Node_Knot"):
        return "reroute"
    if text.endswith("K2Node_Event") or text.endswith("K2Node_CustomEvent"):
        return "event"
    if text.endswith("K2Node_CallFunction") or text.endswith("K2Node_CallParentFunction"):
        return "call"
    if text.endswith("K2Node_VariableGet"):
        return "variable-get"
    if text.endswith("K2Node_VariableSet"):
        return "variable-set"
    if text.endswith("K2Node_MacroInstance"):
        return "macro"
    if text.endswith("K2Node_DynamicCast"):
        return "cast"
    return "other"


def _check_string(value: Any, field_name: str, path: str, report: ValidationReport, *, allow_empty: bool = False) -> None:
    if not isinstance(value, str) or (not allow_empty and not value):
        report.add("contract-schema-type-mismatch", f"{field_name} must be a non-empty string", path=path)


def validate(
    graph: dict[str, Any],
    *,
    require_hashes: bool = True,
    require_apply_ready: bool = False,
) -> ValidationReport:
    report = ValidationReport()
    if not isinstance(graph, dict):
        report.add("graph-invalid-schema-version", "TypedGraph must be a JSON object", path="$")
        return report

    version = graph.get("schemaVersion")
    try:
        decision = negotiate_version(
            version,
            namespace=TYPEDGRAPH_NAMESPACE,
            supported_minor=TYPEDGRAPH_SUPPORTED_MINOR,
            unsupported_code="graph-schema-version-unsupported",
        )
        report.diagnostics.extend(decision.diagnostics)
    except SchemaError as exc:
        code = "graph-schema-version-unsupported" if exc.code == "contract-schema-invalid" else exc.code
        report.add(code, exc.message, path=exc.path)
        return report

    graph_id_value = graph.get("graphId")
    if not ids.is_graph_id(graph_id_value):
        report.add("graph-invalid-id-format", f"Invalid graphId: {graph_id_value!r}", path="$.graphId")

    asset = graph.get("asset") if isinstance(graph.get("asset"), dict) else {}
    _check_string(asset.get("path"), "asset.path", "$.asset.path", report)
    if not ids.is_asset_path(asset.get("path")):
        report.add("graph-invalid-id-format", "asset.path must be an Unreal object path (/Game/...)", path="$.asset.path")
    _check_string(asset.get("class"), "asset.class", "$.asset.class", report)
    revision = asset.get("revision", "")
    if revision != "" and not is_sha256(revision):
        report.add("graph-invalid-revision", "asset.revision must be empty or sha256:<64 lowercase hex>", path="$.asset.revision")
    if asset.get("revisionPolicy") not in REVISION_POLICIES:
        report.add("graph-invalid-revision", "asset.revisionPolicy is missing or unknown", path="$.asset.revisionPolicy")
    if not isinstance(asset.get("dirty"), bool):
        report.add("contract-schema-type-mismatch", "asset.dirty must be boolean", path="$.asset.dirty")

    graph_meta = graph.get("graph") if isinstance(graph.get("graph"), dict) else {}
    _check_string(graph_meta.get("name"), "graph.name", "$.graph.name", report)
    if not ids.is_guid(graph_meta.get("guid", "")):
        report.add("graph-invalid-id-format", "graph.guid must be a lowercase GUID", path="$.graph.guid")
    elif graph_id_value != "g:" + graph_meta.get("guid", ""):
        # A name fallback is allowed for reading, but must not masquerade as native.
        if ids.is_native_graph_id(graph_id_value):
            report.add("graph-id-guid-mismatch", "graphId does not match graph.guid", path="$.graphId")
    if graph_meta.get("kind") not in ("event", "function", "macro", "ubergraph", "delegate-signature", "other"):
        report.add("graph-invalid-kind", "graph.kind is missing or unknown", path="$.graph.kind")
    _check_string(graph_meta.get("schema"), "graph.schema", "$.graph.schema", report)
    if not isinstance(graph_meta.get("editable"), bool):
        report.add("contract-schema-type-mismatch", "graph.editable must be boolean", path="$.graph.editable")

    source = graph.get("source") if isinstance(graph.get("source"), dict) else {}
    if source.get("kind") not in ("live-editor", "commandlet-export", "test-fixture"):
        report.add("graph-invalid-source", "source.kind is missing or unknown", path="$.source.kind")
    _check_string(source.get("exporterSchemaVersion"), "source.exporterSchemaVersion", "$.source.exporterSchemaVersion", report)
    _check_string(source.get("engineVersion"), "source.engineVersion", "$.source.engineVersion", report)
    if source.get("pluginBuildHash", "") and not (
        is_sha256(source.get("pluginBuildHash")) or len(str(source.get("pluginBuildHash"))) == 16
    ):
        report.add("graph-invalid-source", "source.pluginBuildHash must be 16 hex chars or sha256:<64>", path="$.source.pluginBuildHash")

    limits = graph.get("limits") if isinstance(graph.get("limits"), dict) else {}
    for name in ("maxNodes", "maxPins"):
        if not isinstance(limits.get(name), int) or isinstance(limits.get(name), bool) or limits.get(name) < 0:
            report.add("contract-schema-type-mismatch", f"limits.{name} must be a non-negative integer", path=f"$.limits.{name}")
    if not isinstance(limits.get("truncated"), bool):
        report.add("contract-schema-type-mismatch", "limits.truncated must be boolean", path="$.limits.truncated")
    if limits.get("truncated"):
        if limits.get("selection") != "nodeId-ascending":
            report.add("graph-truncated", "Truncated graphs must use limits.selection='nodeId-ascending'", path="$.limits.selection")
        if not isinstance(limits.get("omittedNodeCount"), int) or limits.get("omittedNodeCount", 0) <= 0:
            report.add("graph-truncated", "Truncated graphs must report limits.omittedNodeCount > 0", path="$.limits.omittedNodeCount")
        if not any(item.get("code") == "graph-truncated" for item in graph.get("diagnostics", []) if isinstance(item, dict)):
            report.add("graph-truncated", "Truncated graphs must carry a graph-truncated diagnostic", path="$.diagnostics")

    nodes = graph.get("nodes")
    pins = graph.get("pins")
    edges = graph.get("edges")
    comments = graph.get("comments", [])
    if not isinstance(nodes, list) or not isinstance(pins, list) or not isinstance(edges, list) or not isinstance(comments, list):
        report.add("contract-schema-type-mismatch", "nodes/pins/edges/comments must be arrays", path="$")
        return report

    node_by_id: dict[str, dict[str, Any]] = {}
    for index, node in enumerate(nodes):
        path = f"$.nodes[{index}]"
        if not isinstance(node, dict):
            report.add("contract-schema-type-mismatch", "node must be an object", path=path)
            continue
        node_id_value = node.get("nodeId")
        if not ids.is_node_id(node_id_value):
            report.add("graph-invalid-id-format", f"Invalid nodeId: {node_id_value!r}", path=f"{path}.nodeId")
            continue
        if node_id_value in node_by_id:
            report.add("graph-duplicate-node-id", f"Duplicate nodeId: {node_id_value}", path=f"{path}.nodeId")
        node_by_id[node_id_value] = node
        guid = node.get("nodeGuid", "")
        if not ids.is_guid(guid):
            report.add("graph-invalid-id-format", "nodeGuid must be a lowercase GUID", path=f"{path}.nodeGuid")
        elif ids.is_native_node_id(node_id_value) and ids.node_guid_from_id(node_id_value) != guid:
            report.add("graph-node-guid-mismatch", "nodeId does not match nodeGuid", path=f"{path}.nodeId")
        _check_string(node.get("classPath"), "classPath", f"{path}.classPath", report)
        if node.get("kind") not in NODE_KINDS:
            report.add("graph-invalid-kind", f"Unknown node kind: {node.get('kind')!r}", path=f"{path}.kind")
        elif node.get("classPath"):
            expected_kind = classify_node_class(str(node.get("classPath")))
            if node.get("kind") != expected_kind and not (
                expected_kind == "other" and node.get("kind") in NODE_KINDS
            ):
                report.add(
                    "graph-node-kind-mismatch",
                    f"node.kind {node.get('kind')!r} does not match classPath classification {expected_kind!r}",
                    path=f"{path}.kind",
                )
        flags = _node_flags(node)
        if flags["comment"] and node.get("kind") != "comment":
            report.add("graph-node-kind-mismatch", "flags.comment requires kind=comment", path=f"{path}.flags.comment")
        if flags["reroute"] and node.get("kind") != "reroute":
            report.add("graph-node-kind-mismatch", "flags.reroute requires kind=reroute", path=f"{path}.flags.reroute")
        position = node.get("position") if isinstance(node.get("position"), dict) else {}
        for axis in ("x", "y"):
            if not isinstance(position.get(axis), int) or isinstance(position.get(axis), bool):
                report.add("graph-invalid-coordinate", f"position.{axis} must be an integer", path=f"{path}.position.{axis}")
        size = node.get("size") if isinstance(node.get("size"), dict) else {}
        for axis in ("width", "height"):
            if not isinstance(size.get(axis), int) or isinstance(size.get(axis), bool) or size.get(axis, -1) < 0:
                report.add(
                    "contract-schema-type-mismatch",
                    f"size.{axis} must be a non-negative integer (0 = unknown)",
                    path=f"{path}.size.{axis}",
                )
        if size.get("source") not in SIZE_SOURCES:
            report.add("graph-invalid-size-source", "size.source is missing or unknown", path=f"{path}.size.source")
        if not isinstance(node.get("pins"), list):
            report.add("contract-schema-type-mismatch", "node.pins must be an array of pin ids", path=f"{path}.pins")

    pin_by_id: dict[str, dict[str, Any]] = {}
    pins_by_node: dict[str, list[dict[str, Any]]] = {}
    for index, pin in enumerate(pins):
        path = f"$.pins[{index}]"
        if not isinstance(pin, dict):
            report.add("contract-schema-type-mismatch", "pin must be an object", path=path)
            continue
        pin_id_value = pin.get("pinId")
        if not ids.is_pin_id(pin_id_value):
            report.add("graph-invalid-id-format", f"Invalid pinId: {pin_id_value!r}", path=f"{path}.pinId")
            continue
        if pin_id_value in pin_by_id:
            report.add("graph-duplicate-pin-id", f"Duplicate pinId: {pin_id_value}", path=f"{path}.pinId")
        pin_by_id[pin_id_value] = pin
        owner = pin.get("nodeId")
        if owner not in node_by_id:
            report.add("graph-pin-node-mismatch", f"pin.nodeId {owner!r} does not exist", path=f"{path}.nodeId")
        pins_by_node.setdefault(str(owner), []).append(pin)
        _check_string(pin.get("name"), "pin.name", f"{path}.name", report)
        if pin.get("direction") not in PIN_DIRECTIONS:
            report.add("graph-invalid-pin-direction", "pin.direction must be 'input' or 'output'", path=f"{path}.direction")
        kind = pin.get("kind")
        if kind not in EDGE_KINDS:
            report.add("graph-invalid-edge-kind", "pin.kind must be 'exec' or 'data'", path=f"{path}.kind")
        elif _pin_type(pin)["category"] == "exec" and kind != "exec":
            report.add("graph-invalid-edge-kind", "type.category='exec' requires pin.kind='exec'", path=f"{path}.kind")
        elif _pin_type(pin)["category"] != "exec" and kind == "exec":
            report.add("graph-invalid-edge-kind", "pin.kind='exec' requires type.category='exec'", path=f"{path}.kind")
        if not isinstance(pin.get("index"), int) or isinstance(pin.get("index"), bool) or pin.get("index", -1) < 0:
            report.add("graph-invalid-pin-index", "pin.index must be a non-negative integer (export-time array index)", path=f"{path}.index")
        if pin.get("pinIdSource", "native") not in PIN_ID_SOURCES:
            report.add("graph-invalid-pin-source", "pin.pinIdSource must be 'native' or 'derived'", path=f"{path}.pinIdSource")
        if pin.get("pinIdSource") == "derived" and pin.get("pinGuidTransient") is not True:
            report.add("graph-invalid-pin-source", "derived pin ids require pinGuidTransient=true", path=f"{path}.pinIdSource")
        _pin_type(pin)
        _pin_defaults(pin)
        _pin_flags(pin)
        if not isinstance(pin.get("links", []), list):
            report.add("contract-schema-type-mismatch", "pin.links must be an array", path=f"{path}.links")

    for node in nodes:
        if not isinstance(node, dict):
            continue
        declared = [value for value in node.get("pins", []) if isinstance(value, str)]
        actual = sorted(pin.get("pinId", "") for pin in pins_by_node.get(str(node.get("nodeId")), []))
        if sorted(declared) != actual:
            report.add(
                "graph-pin-node-mismatch",
                "node.pins does not match the pins declared with pin.nodeId",
                path=f"$.nodes[?(@.nodeId=='{node.get('nodeId')}')].pins",
            )

    edge_by_id: dict[str, dict[str, Any]] = {}
    seen_pairs: dict[tuple[str, str], str] = {}
    for index, edge in enumerate(edges):
        path = f"$.edges[{index}]"
        if not isinstance(edge, dict):
            report.add("contract-schema-type-mismatch", "edge must be an object", path=path)
            continue
        edge_id_value = edge.get("edgeId")
        source_ref = edge.get("from") if isinstance(edge.get("from"), dict) else {}
        target_ref = edge.get("to") if isinstance(edge.get("to"), dict) else {}
        source_pin = source_ref.get("pinId")
        target_pin = target_ref.get("pinId")
        if not ids.is_edge_id(edge_id_value):
            report.add("graph-invalid-edge-id", f"Invalid edgeId: {edge_id_value!r}", path=f"{path}.edgeId")
        elif ids.is_pin_id(source_pin) and ids.is_pin_id(target_pin):
            expected = ids.edge_id(source_pin, target_pin)
            if edge_id_value != expected:
                report.add("graph-invalid-edge-id", f"edgeId must equal {expected!r}", path=f"{path}.edgeId")
        if edge_id_value in edge_by_id:
            report.add("graph-duplicate-edge", f"Duplicate edgeId: {edge_id_value}", path=f"{path}.edgeId")
        edge_by_id[str(edge_id_value)] = edge
        if source_ref.get("nodeId") not in node_by_id or target_ref.get("nodeId") not in node_by_id:
            report.add("graph-dangling-edge", "edge endpoint node does not exist", path=path)
        if source_pin not in pin_by_id or target_pin not in pin_by_id:
            report.add("graph-dangling-edge", "edge endpoint pin does not exist", path=path)
            continue
        source = pin_by_id[source_pin]
        target = pin_by_id[target_pin]
        if source.get("nodeId") != source_ref.get("nodeId") or target.get("nodeId") != target_ref.get("nodeId"):
            report.add("graph-pin-node-mismatch", "edge nodeId does not match the pin owner", path=path)
        if source.get("direction") != "output" or target.get("direction") != "input":
            report.add("graph-invalid-pin-direction", "edges must be output -> input", path=path)
        if edge.get("kind") not in EDGE_KINDS:
            report.add("graph-invalid-edge-kind", "edge.kind must be 'exec' or 'data'", path=f"{path}.kind")
        if edge.get("kind") != source.get("kind") or edge.get("kind") != target.get("kind"):
            report.add("graph-invalid-edge-kind", "edge.kind must match both pin kinds", path=f"{path}.kind")
        pair = (str(source_pin), str(target_pin))
        if pair in seen_pairs:
            report.add("graph-duplicate-edge", "The same pin pair may only have one edge", path=path)
        seen_pairs[pair] = str(edge_id_value)
        if edge.get("status") not in EDGE_STATUSES:
            report.add("graph-invalid-edge-status", "edge.status is missing or unknown", path=f"{path}.status")
        elif edge.get("status") != "resolved" and require_apply_ready:
            report.add("graph-status-not-resolved", "Apply requires every edge status to be 'resolved'", path=f"{path}.status")
        for endpoint in (source, target):
            if endpoint.get("nodeId") in {node.get("nodeId") for node in nodes if node.get("kind") == "comment"}:
                report.add("graph-comment-has-edges", "Comment nodes must not appear in edges", path=path)

    # links must exactly mirror the edge set (TG-LINK-MIRROR).
    link_map: dict[str, set[str]] = {str(pin_id_value): set() for pin_id_value in pin_by_id}
    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_ref = edge.get("from") or {}
        target_ref = edge.get("to") or {}
        if source_ref.get("pinId") in link_map:
            link_map[source_ref["pinId"]].add(str(target_ref.get("pinId", "")))
        if target_ref.get("pinId") in link_map:
            link_map[target_ref["pinId"]].add(str(source_ref.get("pinId", "")))
    for pin_id_value, pin in pin_by_id.items():
        declared_links = {value for value in pin.get("links", []) if isinstance(value, str)}
        if declared_links != link_map[pin_id_value]:
            report.add(
                "graph-links-mirror-mismatch",
                "pin.links must mirror the edge set",
                path=f"$.pins[?(@.pinId=='{pin_id_value}')].links",
            )
        for link in declared_links:
            if link not in pin_by_id:
                report.add("graph-dangling-edge", f"pin.links references a missing pin: {link}", path="$")

    comment_ids: set[str] = set()
    for index, comment in enumerate(comments):
        path = f"$.comments[{index}]"
        if not isinstance(comment, dict):
            report.add("contract-schema-type-mismatch", "comment must be an object", path=path)
            continue
        node_id_value = comment.get("nodeId")
        comment_ids.add(str(node_id_value))
        if node_id_value not in node_by_id:
            report.add("graph-dangling-edge", "comment node does not exist in nodes", path=f"{path}.nodeId")
        elif node_by_id[node_id_value].get("kind") != "comment":
            report.add("graph-node-kind-mismatch", "comments[] must reference a kind=comment node", path=f"{path}.nodeId")
        bounds = comment.get("bounds") if isinstance(comment.get("bounds"), dict) else {}
        for axis in ("x", "y", "width", "height"):
            if not isinstance(bounds.get(axis), int) or isinstance(bounds.get(axis), bool):
                report.add("graph-invalid-coordinate", f"comment.bounds.{axis} must be an integer", path=f"{path}.bounds.{axis}")
        if bounds.get("width", -1) < 0 or bounds.get("height", -1) < 0:
            report.add("graph-invalid-coordinate", "comment bounds must be non-negative", path=f"{path}.bounds")
        if not isinstance(comment.get("containedNodeIds", []), list):
            report.add("contract-schema-type-mismatch", "comment.containedNodeIds must be an array", path=f"{path}.containedNodeIds")
        if not isinstance(comment.get("text"), str):
            report.add("contract-schema-type-mismatch", "comment.text must be a string", path=f"{path}.text")

    if not isinstance(graph.get("diagnostics", []), list):
        report.add("contract-schema-type-mismatch", "diagnostics must be an array", path="$.diagnostics")
    else:
        registry = load_registry()
        for index, diagnostic in enumerate(graph.get("diagnostics", [])):
            if not isinstance(diagnostic, dict):
                report.add("contract-schema-type-mismatch", "diagnostic must be an object", path=f"$.diagnostics[{index}]")
                continue
            if not registry.is_known(str(diagnostic.get("code", ""))):
                report.add("contract-error-code-unknown", f"Unknown diagnostic code: {diagnostic.get('code')!r}", path=f"$.diagnostics[{index}].code")
            if diagnostic.get("severity") not in ("info", "warning", "error"):
                report.add("contract-schema-type-mismatch", "diagnostic.severity must be info/warning/error", path=f"$.diagnostics[{index}].severity")

    if require_hashes:
        expected_semantic = semantic_hash(graph)
        if graph.get("semanticHash") != expected_semantic:
            report.add(
                "graph-semantic-hash-mismatch",
                "semanticHash does not match the whitelisted projection",
                path="$.semanticHash",
                context={"expected": expected_semantic, "actual": graph.get("semanticHash")},
            )
        expected_layout = layout_hash(graph)
        if graph.get("layoutHash") != expected_layout:
            report.add(
                "graph-layout-hash-mismatch",
                "layoutHash does not match nodeId+x+y",
                path="$.layoutHash",
                context={"expected": expected_layout, "actual": graph.get("layoutHash")},
            )
        for node in nodes:
            if not isinstance(node, dict) or "semanticHash" not in node:
                continue
            expected_node = node_hash(graph, str(node.get("nodeId", "")))
            if node.get("semanticHash") != expected_node:
                report.add(
                    "graph-node-hash-mismatch",
                    "node.semanticHash does not match its node projection",
                    path=f"$.nodes[?(@.nodeId=='{node.get('nodeId')}')].semanticHash",
                    context={"expected": expected_node, "actual": node.get("semanticHash")},
                )

    if require_apply_ready:
        if not ids.is_native_graph_id(graph_id_value):
            report.add("graph-node-identity-unstable", "Apply requires a native GraphGuid (g:<guid>)", path="$.graphId")
        for node in nodes:
            if isinstance(node, dict) and not ids.is_native_node_id(node.get("nodeId")):
                report.add(
                    "graph-node-identity-unstable",
                    "Apply requires native node ids (n:<guid>)",
                    path=f"$.nodes[?(@.nodeId=='{node.get('nodeId')}')].nodeId",
                )
        for pin_id_value in pin_by_id:
            if ids.PIN_DUP_RE.fullmatch(pin_id_value):
                report.add(
                    "graph-pin-guid-duplicate",
                    "Apply is rejected while a duplicate pin id fallback is present; re-export required",
                    path=f"$.pins[?(@.pinId=='{pin_id_value}')].pinId",
                )
        if limits.get("truncated"):
            report.add("graph-truncated", "A truncated graph must not be used for Apply", path="$.limits.truncated")
        if any(item.get("severity") in ("error", "warning") for item in graph.get("diagnostics", []) if isinstance(item, dict)):
            report.add("graph-diagnostics-error", "Apply requires diagnostics to be empty or all severity=info", path="$.diagnostics")

    return report


def build_typed_graph(raw: dict[str, Any]) -> dict[str, Any]:
    """Builder invariants: normalise duplicate pin ids, mirror links, freeze hashes.

    Duplicate native PinIds across nodes are rewritten to
    ``p:dup:<node-guid>:<pin-guid>`` and marked with ``graph-pin-guid-duplicate``.
    The resulting graph is a read model; Apply is rejected until re-export.
    """

    graph = copy.deepcopy(raw)
    graph.setdefault("schemaVersion", TYPEDGRAPH_VERSION)
    nodes = graph.get("nodes", [])
    pins = graph.get("pins", [])
    node_guid_by_id = {node.get("nodeId"): node.get("nodeGuid", "") for node in nodes if isinstance(node, dict)}
    counts: dict[str, int] = {}
    for pin in pins:
        if isinstance(pin, dict):
            counts[str(pin.get("pinId", ""))] = counts.get(str(pin.get("pinId", "")), 0) + 1
    duplicate_ids = {value for value, count in counts.items() if count > 1}

    rewrite: dict[tuple[str, str], str] = {}
    if duplicate_ids:
        for pin in pins:
            if not isinstance(pin, dict) or pin.get("pinId") not in duplicate_ids:
                continue
            raw_id = str(pin.get("pinId", ""))
            pin_guid = ids.pin_guid_from_id(raw_id)
            node_id_value = str(pin.get("nodeId", ""))
            node_guid = str(node_guid_by_id.get(node_id_value, ""))
            if not pin_guid or not ids.is_guid(node_guid):
                continue
            new_id = ids.duplicate_pin_id(node_guid, pin_guid)
            rewrite[(node_id_value, raw_id)] = new_id
            pin["pinId"] = new_id
        graph.setdefault("diagnostics", []).append(
            make_diagnostic(
                "graph-pin-guid-duplicate",
                "Duplicate native PinIds were disambiguated as p:dup:<node-guid>:<pin-guid>; Apply is disabled",
                path="$.pins",
                severity="error",
                context={"duplicateCount": len(duplicate_ids)},
            )
        )

    for pin in pins:
        if isinstance(pin, dict):
            pin["links"] = []
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        for endpoint in ("from", "to"):
            ref = edge.get(endpoint)
            if not isinstance(ref, dict):
                continue
            key = (str(ref.get("nodeId", "")), str(ref.get("pinId", "")))
            if key in rewrite:
                ref["pinId"] = rewrite[key]
        try:
            edge["edgeId"] = ids.edge_id((edge.get("from") or {}).get("pinId", ""), (edge.get("to") or {}).get("pinId", ""))
        except ValueError:
            edge["edgeId"] = str(edge.get("edgeId", ""))

    pin_ids = {str(pin.get("pinId", "")) for pin in pins if isinstance(pin, dict)}
    for edge in graph.get("edges", []):
        if not isinstance(edge, dict):
            continue
        source = (edge.get("from") or {}).get("pinId")
        target = (edge.get("to") or {}).get("pinId")
        if source in pin_ids and target in pin_ids:
            for pin in pins:
                if pin.get("pinId") == source:
                    pin.setdefault("links", []).append(target)
                elif pin.get("pinId") == target:
                    pin.setdefault("links", []).append(source)
    for pin in pins:
        if isinstance(pin, dict) and isinstance(pin.get("links"), list):
            pin["links"] = sorted(set(pin["links"]))

    graph["semanticHash"] = semantic_hash(graph)
    graph["layoutHash"] = layout_hash(graph)
    for node in nodes:
        if isinstance(node, dict):
            node["semanticHash"] = node_hash(graph, str(node.get("nodeId", "")))
    return graph


def apply_ready(graph: dict[str, Any]) -> bool:
    return validate(graph, require_hashes=True, require_apply_ready=True).ok

"""LayoutPlan 1.0: deterministic, pure, semantic-preserving layout preview.

This module extends the G0.3 baseline retained in
``prototype/graph_contracts/ueak_contract/layout.py`` with iterative SCC ranks,
weighted bidirectional ordering, transitive pure attribution, immutable comment
compounds and conservative scoped placement. :func:`preview_graph_layout`
retains the public fail-closed request / output checks.  It never opens Unreal, mutates an
asset, writes a file, or claims Editor evidence.

Clause IDs: LP-PURE-FUNCTION, LP-ISLANDS, LP-PURE-NODES, LP-SCC, LP-PACKING,
LP-HASH, LP-SEED, LP-METRICS, LP-OVERLAP, LP-WARNINGS, LP-SCOPE, LP-SIZE.

The algorithm is intentionally small but complete enough to exercise the
contract: WCC islands over Exec edges, iterative SCC condensation, pure-data
attribution without duplication, deterministic tie-breaks, shelf packing,
integer coordinates and exact metrics.  It uses no random source; ``seed`` is
reserved for future versions (CR-LP-02).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable

from . import typed_graph
from . import _layout_layered as layered_layout
from .canonical import DOMAIN_LAYOUT, DOMAIN_LAYOUT_PLAN, DOMAIN_SIZE, domain_hash, short_digest
from .errors import make_diagnostic

LAYOUT_PLAN_VERSION = "ueak.layoutplan/1.0"
LAYOUT_MODES = ("incremental", "selection", "full")
LAYOUT_WARNING_CODES = (
    "layout-shared-pure-node",
    "layout-comment-partial-scope",
    "layout-reroute-broken",
    "layout-size-mismatch",
    "layout-no-space",
    "layout-overlap-unresolved",
    "layout-cross-island-data-edge",
    "layout-scope-violation",
)
BLOCKING_CODES = {
    "layout-reroute-broken",
    "layout-no-space",
    "layout-overlap-unresolved",
    "layout-scope-violation",
}
COMMENT_PADDING = 24
DEFAULT_TARGET_WIDTH = 1600
DEFAULT_SPACING_X = 80
DEFAULT_SPACING_Y = 40
# UE stores NodePosX / NodePosY as int32.  A plan outside this range can never
# be applied without overflow, so preview rejects it instead of relying on a
# later cast.
MAX_ABS_POSITION = 2_147_483_647
# D-06 layout-only profile: at most 1000 setNodePosition operations.  A layout
# plan that would need more moved nodes cannot become one apply batch.
MAX_LAYOUT_POSITIONS = 1000


@dataclass
class LayoutPlan:
    status: str
    plan: dict[str, Any]
    errors: list[dict[str, Any]] = field(default_factory=list)


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def estimate_size(node: dict[str, Any], pins: list[dict[str, Any]]) -> tuple[int, int]:
    """Contract constants from CR-LP-07; visible = not hidden and not advanced."""

    title = str(node.get("title", ""))
    visible = [pin for pin in pins if not (pin.get("flags") or {}).get("hidden") and not (pin.get("flags") or {}).get("advanced")]
    max_pin_name = max((len(str(pin.get("name", ""))) for pin in visible), default=0)
    width = _clamp(160, 120 + 7 * max(len(title), max_pin_name), 480)
    comment_lines = str(node.get("comment", "")).count("\n") + (1 if node.get("comment") else 0)
    height = 40 + 22 * len(visible) + 18 * comment_lines
    return width, height


def node_box(node: dict[str, Any]) -> tuple[int, int, int, int]:
    position = node.get("position") or {}
    size = node.get("size") or {}
    return (
        int(position.get("x", 0)),
        int(position.get("y", 0)),
        int(size.get("width", 0)),
        int(size.get("height", 0)),
    )


def _size_for(node: dict[str, Any], pins_by_node: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    size = node.get("size") or {}
    width = int(size.get("width", 0))
    height = int(size.get("height", 0))
    source = size.get("source", "unknown")
    if width <= 0 or height <= 0:
        estimate_width, estimate_height = estimate_size(node, pins_by_node.get(str(node.get("nodeId")), []))
        width = width if width > 0 else estimate_width
        height = height if height > 0 else estimate_height
        source = "estimated"
    return {"width": width, "height": height, "source": source}


def _tarjan(nodes: list[str], adjacency: dict[str, list[str]]) -> list[list[str]]:
    # The historical helper name is retained for private callers, but the
    # implementation is iterative so valid long graphs cannot exhaust recursion.
    return layered_layout.components(nodes, adjacency)


def _wcc(nodes: list[str], adjacency: dict[str, list[str]]) -> list[list[str]]:
    allowed = set(nodes)
    undirected: dict[str, set[str]] = {node: set() for node in nodes}
    for source, targets in adjacency.items():
        if source not in allowed:
            continue
        for target in targets:
            if target in allowed:
                undirected[source].add(target)
                undirected[target].add(source)
    visited: set[str] = set()
    components: list[list[str]] = []
    for node in sorted(nodes):
        if node in visited:
            continue
        stack = [node]
        visited.add(node)
        component: list[str] = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbour in sorted(undirected.get(current, set()), reverse=True):
                if neighbour not in visited:
                    visited.add(neighbour)
                    stack.append(neighbour)
        components.append(sorted(component))
    return components


def _longest_layers(nodes: list[str], adjacency: dict[str, list[str]]) -> dict[str, int]:
    indegree = {node: 0 for node in nodes}
    for source in nodes:
        for target in adjacency.get(source, []):
            if target in indegree:
                indegree[target] += 1
    ready = sorted(node for node in nodes if indegree[node] == 0)
    layers = {node: 0 for node in nodes}
    processed = 0
    while ready:
        node = ready.pop(0)
        processed += 1
        for target in sorted(adjacency.get(node, [])):
            if target not in layers:
                continue
            layers[target] = max(layers[target], layers[node] + 1)
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort()
    if processed != len(nodes):
        for node in nodes:
            layers.setdefault(node, 0)
    return layers


def _rect_overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    return min(lx + lw, rx + rw) - max(lx, rx) > 0 and min(ly + lh, ry + rh) - max(ly, ry) > 0


def _crossings(edges: list[dict[str, Any]], positions: dict[str, tuple[int, int, int, int]], layers: dict[str, int]) -> int:
    by_pair: dict[int, list[tuple[int, int, str, str]]] = {}
    for edge in edges:
        source = edge["source"]
        target = edge["target"]
        if source not in positions or target not in positions or source not in layers or target not in layers:
            continue
        if layers[target] != layers[source] + 1:
            continue
        source_order = (positions[source][1], positions[source][0], source)
        target_order = (positions[target][1], positions[target][0], target)
        by_pair.setdefault(layers[source], []).append((source_order[0], target_order[0], source, target))
    crossings = 0
    for layer_edges in by_pair.values():
        for index, first in enumerate(layer_edges):
            for second in layer_edges[index + 1 :]:
                if first[2] == second[2] or first[3] == second[3]:
                    continue
                if (first[0] < second[0]) != (first[1] < second[1]):
                    crossings += 1
    return crossings


def compute_metrics(
    edges: list[dict[str, Any]],
    positions: dict[str, tuple[int, int, int, int]],
    layers: dict[str, int],
    islands: list[dict[str, Any]],
) -> dict[str, Any]:
    crossing_count = _crossings(edges, positions, layers)
    edge_length = 0
    for edge in edges:
        source = positions.get(edge["source"])
        target = positions.get(edge["target"])
        if source is None or target is None:
            continue
        edge_length += abs((source[0] + source[2] // 2) - (target[0] + target[2] // 2)) + abs(
            (source[1] + source[3] // 2) - (target[1] + target[3] // 2)
        )
    node_ids = sorted(positions)
    overlap_count = 0
    for index, left in enumerate(node_ids):
        for right in node_ids[index + 1 :]:
            if _rect_overlap(positions[left], positions[right]):
                overlap_count += 1
    area = sum(int(island["bbox"]["width"]) * int(island["bbox"]["height"]) for island in islands)
    if positions:
        min_x = min(box[0] for box in positions.values())
        min_y = min(box[1] for box in positions.values())
        max_x = max(box[0] + box[2] for box in positions.values())
        max_y = max(box[1] + box[3] for box in positions.values())
        width = max_x - min_x
        height = max_y - min_y
        aspect_ratio = round(width / height, 6) if height > 0 else 0.0
    else:
        width = height = 0
        aspect_ratio = 0.0
    return {
        "crossings": crossing_count,
        "edgeLength": edge_length,
        "overlapCount": overlap_count,
        "area": area,
        "aspectRatio": aspect_ratio,
        "bbox": {"width": width, "height": height},
    }



def _pack_islands(
    island_positions: list[tuple[str, dict[str, tuple[int, int]], dict[str, dict[str, Any]]]],
    target_width: int,
    spacing: int,
) -> tuple[dict[str, tuple[int, int]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    for island_id, positions, size_by_node in island_positions:
        if not positions:
            continue
        min_x = min(value[0] for value in positions.values())
        min_y = min(value[1] for value in positions.values())
        width = max(positions[node][0] + size_by_node[node]["width"] for node in positions) - min_x
        height = max(positions[node][1] + size_by_node[node]["height"] for node in positions) - min_y
        entries.append(
            {
                "islandId": island_id,
                "positions": positions,
                "sizeByNode": size_by_node,
                "minX": min_x,
                "minY": min_y,
                "width": width,
                "height": height,
                "nodeIds": sorted(positions),
            }
        )
    entries.sort(key=lambda item: (-item["height"], -(item["width"] * item["height"]), item["islandId"]))
    offsets: dict[str, tuple[int, int]] = {}
    cursor_x = 0
    cursor_y = 0
    row_height = 0
    for entry in entries:
        if cursor_x > 0 and cursor_x + entry["width"] > target_width:
            cursor_x = 0
            cursor_y += row_height + spacing
            row_height = 0
        offsets[entry["islandId"]] = (cursor_x - entry["minX"], cursor_y - entry["minY"])
        cursor_x += entry["width"] + spacing
        row_height = max(row_height, entry["height"])
    final_positions: dict[str, tuple[int, int]] = {}
    islands: list[dict[str, Any]] = []
    for entry in sorted(entries, key=lambda item: item["islandId"]):
        dx, dy = offsets[entry["islandId"]]
        for node, position in entry["positions"].items():
            final_positions[node] = (position[0] + dx, position[1] + dy)
        islands.append(
            {
                "islandId": entry["islandId"],
                "nodeIds": entry["nodeIds"],
                "bbox": {
                    "x": entry["minX"] + dx,
                    "y": entry["minY"] + dy,
                    "width": entry["width"],
                    "height": entry["height"],
                },
            }
        )
    return final_positions, islands


def _layout_hash(positions: list[dict[str, Any]]) -> str:
    return domain_hash(
        [{"nodeId": item["nodeId"], "x": item["x"], "y": item["y"]} for item in sorted(positions, key=lambda item: item["nodeId"])],
        DOMAIN_LAYOUT,
    )


def layout_graph(graph: dict[str, Any], request: dict[str, Any] | None = None) -> LayoutPlan:
    request = dict(request or {})
    mode = request.get("mode", "full")
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if mode not in LAYOUT_MODES:
        errors.append(make_diagnostic("contract-schema-enum-mismatch", "mode must be incremental|selection|full", path="$.mode"))
        return LayoutPlan(status="rejected", plan={}, errors=errors)
    options = request.get("options") if isinstance(request.get("options"), dict) else {}
    spacing_x = int(options.get("spacingX", DEFAULT_SPACING_X))
    spacing_y = int(options.get("spacingY", DEFAULT_SPACING_Y))
    target_width = int((request.get("packing") or {}).get("targetWidth", DEFAULT_TARGET_WIDTH))
    seed = int(options.get("seed", 0))

    validation = typed_graph.validate(graph, require_hashes=True, require_apply_ready=False)
    if not validation.ok:
        for diagnostic in validation.errors:
            errors.append(diagnostic)
        return LayoutPlan(status="rejected", plan={}, errors=errors)

    nodes = {str(node["nodeId"]): node for node in graph.get("nodes", [])}
    pins_by_node: dict[str, list[dict[str, Any]]] = {}
    for pin in graph.get("pins", []):
        pins_by_node.setdefault(str(pin.get("nodeId", "")), []).append(pin)
    size_by_node = {node_id: _size_for(node, pins_by_node) for node_id, node in nodes.items()}
    comment_nodes = {node_id for node_id, node in nodes.items() if node.get("kind") == "comment"}
    # TypedGraph keeps comment bounds in ``comments[]``, not in the node
    # record.  The candidate fixture carried an informal node-level ``bounds``
    # field, so use the contract field first and only fall back to position /
    # size.  Without this, a full layout silently moved every comment to 0,0.
    comment_bounds: dict[str, dict[str, Any]] = {}
    for comment in graph.get("comments", []):
        if not isinstance(comment, dict):
            continue
        node_id_value = str(comment.get("nodeId", ""))
        bounds_value = comment.get("bounds")
        if node_id_value in comment_nodes and isinstance(bounds_value, dict):
            if node_id_value in comment_bounds:
                return _rejected('layout-size-mismatch', 'Comment node has duplicate frame records', '$.comments')
            comment_bounds[node_id_value] = dict(bounds_value)
    for comment_id, bounds in comment_bounds.items():
        raw_size = nodes[comment_id].get('size') or {}
        original = nodes[comment_id].get('position') or {}
        if any(int(original.get(axis, 0)) != int(bounds.get(axis, 0)) for axis in ('x', 'y')):
            return _rejected('layout-size-mismatch', 'Comment bounds position conflicts with its node position', '$.comments')
        for dimension in ('width', 'height'):
            stored = int(raw_size.get(dimension, 0))
            bound_size = int(bounds.get(dimension, 0))
            if stored > 0 and stored != bound_size:
                return _rejected('layout-size-mismatch', 'Comment bounds dimensions conflict with its node size', '$.comments')
            if stored <= 0 and bound_size > 0:
                size_by_node[comment_id][dimension] = bound_size
                size_by_node[comment_id]['source'] = 'stored'
    reroute_nodes = {node_id for node_id, node in nodes.items() if node.get("kind") == "reroute"}
    event_nodes = {node_id for node_id, node in nodes.items() if node.get("kind") == "event"}
    content_nodes = set(nodes) - comment_nodes

    pins = {str(pin['pinId']): pin for pin in graph.get('pins', [])}
    pin_offsets: dict[str, float] = {}
    for node_id in sorted(pins_by_node):
        for direction in ('input', 'output'):
            siblings = sorted((p for p in pins_by_node[node_id] if p.get('direction') == direction),
                              key=lambda p: (p.get('index', 0), p['pinId']))
            for index, pin in enumerate(siblings):
                pin_offsets[pin['pinId']] = (index + 1) * size_by_node[node_id]['height'] / (len(siblings) + 1)
    all_edges: list[dict[str, Any]] = []
    exec_adjacency: dict[str, list[str]] = {node: [] for node in content_nodes}
    data_adjacency: dict[str, list[str]] = {node: [] for node in content_nodes}
    incoming_edges: dict[str, list[dict[str, Any]]] = {node: [] for node in content_nodes}
    outgoing_edges: dict[str, list[dict[str, Any]]] = {node: [] for node in content_nodes}
    for edge in sorted(graph.get('edges', []), key=lambda e: e['edgeId']):
        source_pin = pins.get((edge.get('from') or {}).get('pinId', ''), {})
        target_pin = pins.get((edge.get('to') or {}).get('pinId', ''), {})
        source_node, target_node = str(source_pin.get('nodeId', '')), str(target_pin.get('nodeId', ''))
        if source_node not in content_nodes or target_node not in content_nodes:
            continue
        link = {'source': source_node, 'target': target_node, 'kind': edge.get('kind'),
                'edgeId': edge['edgeId'], 'sourceIndex': int(source_pin.get('index', 0)),
                'targetIndex': int(target_pin.get('index', 0)),
                'sourceY': pin_offsets[source_pin['pinId']], 'targetY': pin_offsets[target_pin['pinId']]}
        all_edges.append(link)
        incoming_edges[target_node].append(link)
        outgoing_edges[source_node].append(link)
        adjacency = exec_adjacency if link['kind'] == 'exec' else data_adjacency
        adjacency[source_node].append(target_node)

    # A K2 Knot has two pins, but link multiplicity depends on the flow type:
    # data fans out from one producer; Exec may merge several incoming paths.
    # Keep malformed, dangling, mixed-kind and anchorless knot networks blocked.
    for reroute in sorted(reroute_nodes):
        node_pins = pins_by_node.get(reroute, [])
        inputs = [p for p in node_pins if p.get('direction') == 'input']
        outputs = [p for p in node_pins if p.get('direction') == 'output']
        incoming, outgoing = incoming_edges[reroute], outgoing_edges[reroute]
        kinds = {p.get('kind') for p in node_pins} | {e['kind'] for e in incoming + outgoing}
        flow = next(iter(kinds)) if len(kinds) == 1 else None
        valid = len(node_pins) == 2 and len(inputs) == 1 and len(outputs) == 1
        valid = valid and inputs[0].get('type') == outputs[0].get('type')
        valid = valid and ((flow == 'data' and len(incoming) == 1 and len(outgoing) >= 1)
                           or (flow == 'exec' and len(incoming) >= 1 and len(outgoing) == 1))
        if not valid:
            errors.append(make_diagnostic(
                'layout-reroute-broken',
                'Knot requires two same-kind pins: data one-in/many-out or Exec many-in/one-out',
                path=f"$.nodes[?(@.nodeId=='{reroute}')]",
                context={'incoming': len(incoming), 'outgoing': len(outgoing), 'flowKind': flow},
            ))
    reroute_adjacency = {n: [e['target'] for e in outgoing_edges[n] if e['target'] in reroute_nodes]
                         for n in reroute_nodes}
    for network in _wcc(sorted(reroute_nodes), reroute_adjacency):
        members = set(network)
        has_source = any(e['source'] not in members for n in network for e in incoming_edges[n])
        has_target = any(e['target'] not in members for n in network for e in outgoing_edges[n])
        if not has_source or not has_target:
            errors.append(make_diagnostic(
                'layout-reroute-broken', 'Reroute network has no external source or consumer anchor',
                path=f"$.nodes[?(@.nodeId=='{network[0]}')]",
                context={'nodeIds': network, 'hasSourceAnchor': has_source, 'hasConsumerAnchor': has_target},
            ))

    exec_nodes = {
        node for node in content_nodes
        if any(pin.get("kind") == "exec" for pin in pins_by_node.get(node, []))
    }
    pure_nodes = content_nodes - exec_nodes
    islands = _wcc(sorted(exec_nodes), exec_adjacency)
    island_of: dict[str, str] = {}
    island_members: dict[str, list[str]] = {}
    for index, members in enumerate(sorted(islands, key=lambda group: group[0])):
        island_id = f"island:{index}"
        for member in members:
            island_of[member] = island_id
        island_members[island_id] = members

    pure_assignments: dict[str, str] = {}
    data_only: list[str] = []
    # Propagate downstream consumer islands through entire pure/Knot chains.
    # Direct-only attribution stranded upstream producers in unrelated shelves.
    consumers = {n: {island_of[e['target']] for e in outgoing_edges[n]
                     if e['kind'] == 'data' and e['target'] in island_of} for n in pure_nodes}
    for _ in range(len(pure_nodes)):
        changed = False
        for n in sorted(pure_nodes):
            reached = set(consumers[n])
            for target in data_adjacency[n]:
                reached.update(consumers.get(target, ()))
            if reached != consumers[n]:
                consumers[n] = reached
                changed = True
        if not changed:
            break
    # Pure data derived from an Exec node can also be a terminal output: use
    # upstream attribution only when no downstream Exec consumer exists.
    upstream = {n: {island_of[e['source']] for e in incoming_edges[n]
                    if e['kind'] == 'data' and e['source'] in island_of} for n in pure_nodes}
    for _ in range(len(pure_nodes)):
        changed = False
        for n in sorted(pure_nodes):
            reached = set(upstream[n])
            for edge in incoming_edges[n]:
                if edge['kind'] == 'data':
                    reached.update(upstream.get(edge['source'], ()))
            if reached != upstream[n]:
                upstream[n] = reached
                changed = True
        if not changed:
            break
    for pure in sorted(pure_nodes):
        choices = sorted(consumers[pure] or upstream[pure])
        if not choices:
            data_only.append(pure)
            continue
        # Prefer the closest authored consumer island, with stable id tie-breaks.
        py = int((nodes[pure].get('position') or {}).get('y', 0))
        best = min(choices, key=lambda i: (min(abs(py - int((nodes[n].get('position') or {}).get('y', 0)))
                                                   for n in island_members[i] if n in exec_nodes), i))
        pure_assignments[pure] = best
        island_members[best].append(pure)
        if len(choices) > 1:
            for code, message in (
                ('layout-shared-pure-node', 'Shared pure chain is assigned once to its nearest Exec island'),
                ('layout-cross-island-data-edge', 'Data edges cross islands; they do not merge Exec islands'),
            ):
                warnings.append(make_diagnostic(code, message, path=f"$.nodes[?(@.nodeId=='{pure}')]",
                    severity='warning', context={'primaryIsland': best, 'consumerIslands': choices}))

    # Comment size is immutable in a position-only plan. Use contract bounds
    # where present; T3D previews have only node bounds, whose fidelity is limited.
    for comment_id in sorted(comment_nodes):
        n = nodes[comment_id]
        if comment_id not in comment_bounds:
            box = n.get('bounds') if isinstance(n.get('bounds'), dict) else None
            comment_bounds[comment_id] = dict(box) if box else {
                'x': int((n.get('position') or {}).get('x', 0)),
                'y': int((n.get('position') or {}).get('y', 0)),
                'width': size_by_node[comment_id]['width'], 'height': size_by_node[comment_id]['height'],
            }
        b = comment_bounds[comment_id]
        # Prefer the actual node size. A comment bounds record does not grant
        # permission to resize a node in the returned LayoutPlan.
        b['width'], b['height'] = size_by_node[comment_id]['width'], size_by_node[comment_id]['height']
    memberships = {c: {n for n in content_nodes if _contains_point(b, nodes[n])
                         or _contains_point(b, {**nodes[n], 'size': size_by_node[n]})}
                   for c, b in comment_bounds.items()}
    scope = request.get('scope')
    scope_set = set(str(item) for item in scope) if isinstance(scope, list) else None
    fixed_nodes: set[str] = set()
    layers: dict[str, int] = {}
    local_positions: dict[str, tuple[int, int]] = {}
    island_records: list[dict[str, Any]] = []
    for index, group in enumerate(_wcc(sorted(data_only), data_adjacency)):
        island_id = 'island:data-only' if index == 0 else f'island:data-only:{index}'
        island_members[island_id] = group
    if mode == 'full':
        island_layouts = []
        for island_id in sorted(island_members):
            positions, local_layers = layered_layout.layered(
                island_members[island_id], all_edges, size_by_node, nodes, spacing_x, spacing_y)
            layers.update(local_layers)
            island_layouts.append((island_id, positions, size_by_node))
        local_positions, island_records = _pack_islands(island_layouts, target_width, spacing_x)
        local_positions, failed_comments = layered_layout.compound(
            local_positions, nodes, size_by_node, all_edges, comment_bounds, memberships,
            spacing_x, spacing_y, target_width)
        for c in failed_comments:
            errors.append(make_diagnostic('layout-no-space',
                'Cannot arrange original comment members without overlap inside its unchanged frame',
                path=f"$.nodes[?(@.nodeId=='{c}')]", context={'memberCount': len(memberships[c])}))
    else:
        # Scoped layout is deliberately conservative: an already clear scope is
        # unchanged. Resolve only collisions touching scoped nodes near their
        # original location; unrelated nodes and every unselected frame stay fixed.
        scope_set = scope_set or set()
        # V1 Apply defines fixedNodeIds over content only. Unselected comments
        # also keep their coordinates, enforced independently by the scope gate.
        fixed_nodes = content_nodes - scope_set
        local_positions = {n: (int((nodes[n].get('position') or {}).get('x', 0)),
                               int((nodes[n].get('position') or {}).get('y', 0))) for n in nodes}
        for c in sorted(comment_nodes):
            if memberships[c] & scope_set and not memberships[c].issubset(scope_set):
                warnings.append(make_diagnostic('layout-comment-partial-scope',
                    'Comment partially overlaps scope and keeps its position and dimensions',
                    path=f"$.nodes[?(@.nodeId=='{c}')]", severity='warning'))
        placed = set(content_nodes) - scope_set
        for n in sorted(content_nodes & scope_set, key=lambda n: (local_positions[n][1], local_positions[n][0], n)):
            ox, oy = local_positions[n]
            w, h = size_by_node[n]['width'], size_by_node[n]['height']
            frames = [comment_bounds[c] for c in sorted(comment_bounds) if n in memberships[c]]
            low_x, high_x = -MAX_ABS_POSITION - 1, MAX_ABS_POSITION
            low_y, high_y = -MAX_ABS_POSITION - 1, MAX_ABS_POSITION
            raw_size = nodes[n].get('size') or {}
            raw_x, raw_y = max(1, int(raw_size.get('width', 0))) // 2, max(1, int(raw_size.get('height', 0))) // 2
            for b in frames:
                low_x, high_x = max(low_x, b['x'] - min(w // 2, raw_x)), min(high_x, b['x'] + b['width'] - max(w // 2, raw_x))
                low_y, high_y = max(low_y, b['y'] - min(h // 2, raw_y)), min(high_y, b['y'] + b['height'] - max(h // 2, raw_y))
            if low_x <= ox <= high_x and low_y <= oy <= high_y and not any(
                _rect_overlap((ox, oy, w, h), (local_positions[m][0], local_positions[m][1],
                    size_by_node[m]['width'], size_by_node[m]['height'])) for m in placed
            ):
                placed.add(n)
                continue
            xs = {ox, max(low_x, min(high_x, ox))}
            ys = {oy, max(low_y, min(high_y, oy))}
            for other in sorted(placed):
                px, py = local_positions[other]
                xs.update((px + size_by_node[other]['width'] + spacing_x, px - w - spacing_x))
                ys.update((py + size_by_node[other]['height'] + spacing_y, py - h - spacing_y))
            for c, b in comment_bounds.items():
                if n not in memberships[c]:
                    xs.update((b['x'] + b['width'] + spacing_x, b['x'] - w - spacing_x))
                    ys.update((b['y'] + b['height'] + spacing_y, b['y'] - h - spacing_y))
            trials = layered_layout.nearest_candidates(
                {x for x in xs if low_x <= x <= high_x}, {y for y in ys if low_y <= y <= high_y}, ox, oy)
            for x, y in trials:
                if any(n not in memberships[c] and _rect_overlap((x, y, w, h),
                    (b['x'], b['y'], b['width'], b['height'])) for c, b in comment_bounds.items()):
                    continue
                if not any(_rect_overlap((x, y, w, h),
                    (local_positions[m][0], local_positions[m][1], size_by_node[m]['width'], size_by_node[m]['height']))
                    for m in placed):
                    local_positions[n] = (x, y)
                    break
            else:
                errors.append(make_diagnostic('layout-no-space', 'No nearby collision-free scoped position inside fixed comment constraints',
                    path=f"$.nodes[?(@.nodeId=='{n}')]"))
            placed.add(n)

    if mode == 'full':
        local_positions = layered_layout.refine(
            local_positions, content_nodes, all_edges, size_by_node, memberships, spacing_y, nodes)

    if mode != 'full':
        island_records = [{'islandId': island_id, 'nodeIds': sorted(members), 'bbox': {}}
                          for island_id, members in sorted(island_members.items()) if members]
        # Metrics retain ranks for scoped graphs even though their authored X
        # coordinates are preserved. They remain the contract's rank surrogate.
        for island_id, members in sorted(island_members.items()):
            _, local_layers = layered_layout.layered(
                members, all_edges, size_by_node, nodes, spacing_x, spacing_y, rank_only=True)
            layers.update(local_layers)

    # Recompute post-constraint island bboxes rather than reporting stale
    # pre-comment/pre-scope packing areas.
    for record in island_records:
        selected = {n: local_positions[n] for n in record['nodeIds']}
        bx, by, width, height = layered_layout.bounds(selected, size_by_node)
        record['bbox'] = {'x': bx, 'y': by, 'width': width, 'height': height}

    positions: list[dict[str, Any]] = []
    for node_id in sorted(nodes):
        if node_id not in local_positions:
            original = nodes[node_id].get("position") or {}
            local_positions[node_id] = (int(original.get("x", 0)), int(original.get("y", 0)))
        x, y = local_positions[node_id]
        positions.append(
            {
                "nodeId": node_id,
                "x": int(x),
                "y": int(y),
                "size": dict(size_by_node[node_id]),
            }
        )

    position_boxes = {
        item["nodeId"]: (item["x"], item["y"], item["size"]["width"], item["size"]["height"])
        for item in positions
    }
    content_position_boxes = {node: position_boxes[node] for node in content_nodes}
    overlap_pairs: list[tuple[str, str]] = []
    ordered_nodes = sorted(content_position_boxes)
    for index, left in enumerate(ordered_nodes):
        for right in ordered_nodes[index + 1 :]:
            if mode != 'full' and left not in (scope_set or set()) and right not in (scope_set or set()):
                continue
            if _rect_overlap(content_position_boxes[left], content_position_boxes[right]):
                overlap_pairs.append((left, right))
    for left, right in overlap_pairs[:8]:
        errors.append(
            make_diagnostic(
                "layout-overlap-unresolved",
                "Node rectangles overlap after layout",
                path="$.positions",
                context={"left": left, "right": right},
            )
        )

    metrics = compute_metrics(all_edges, content_position_boxes, layers, island_records)
    layout_hash_value = _layout_hash(positions)
    projection = {
        "baseGraphHash": graph.get("semanticHash", ""),
        "mode": mode,
        "positions": [{"nodeId": item["nodeId"], "x": item["x"], "y": item["y"]} for item in positions],
        "options": {"spacingX": spacing_x, "spacingY": spacing_y, "seed": seed},
        "packing": {"targetWidth": target_width, "spacing": spacing_x, "algorithm": "skyline-v1"},
    }
    plan_id = "layout:" + short_digest(projection, DOMAIN_LAYOUT_PLAN, 16)
    moved_node_ids = sorted(
        node_id
        for node_id, node in nodes.items()
        if (int((node.get("position") or {}).get("x", 0)), int((node.get("position") or {}).get("y", 0)))
        != local_positions[node_id]
    )
    size_before = typed_graph.size_hash(graph)
    size_after = domain_hash(
        [
            {
                "nodeId": item["nodeId"],
                "width": item["size"]["width"],
                "height": item["size"]["height"],
                "source": item["size"]["source"],
            }
            for item in positions
        ],
        DOMAIN_SIZE,
    )
    status = "rejected" if errors else "ok"
    plan = {
        "schemaVersion": LAYOUT_PLAN_VERSION,
        "planId": plan_id,
        "baseGraphHash": graph.get("semanticHash", ""),
        "mode": mode,
        "seed": seed,
        "scopeNodeIds": sorted(scope_set) if scope_set is not None else sorted(content_nodes),
        "fixedNodeIds": sorted(fixed_nodes),
        "positions": positions,
        "movedNodeIds": moved_node_ids,
        "islands": island_records,
        "sccs": _scc_records(island_members, exec_adjacency, exec_nodes),
        "warnings": sorted(warnings, key=lambda item: (item["code"], item["path"])),
        "metrics": metrics,
        "packing": {"targetWidth": target_width, "spacing": spacing_x, "algorithm": "skyline-v1"},
        "layoutHash": layout_hash_value,
        "sizeBeforeHash": size_before,
        "sizeAfterHash": size_after,
        "status": status,
        "errorCode": errors[0]["code"] if errors else "",
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "timingMs": 0,
    }
    return LayoutPlan(status=status, plan=plan, errors=errors)


def _contains_point(bounds: dict[str, Any], node: dict[str, Any]) -> bool:
    if not bounds:
        return False
    position = node.get("position") or {}
    size = node.get("size") or {}
    center_x = int(position.get("x", 0)) + max(1, int(size.get("width", 0))) // 2
    center_y = int(position.get("y", 0)) + max(1, int(size.get("height", 0))) // 2
    x = int(bounds.get("x", 0))
    y = int(bounds.get("y", 0))
    width = int(bounds.get("width", 0))
    height = int(bounds.get("height", 0))
    return x <= center_x <= x + width and y <= center_y <= y + height


def _first_overlap(
    moved: Iterable[str],
    fixed: Iterable[str],
    positions: dict[str, tuple[int, int]],
    size_by_node: dict[str, dict[str, Any]],
) -> tuple[str, str] | None:
    def box(node: str) -> tuple[int, int, int, int]:
        position = positions[node]
        return (position[0], position[1], size_by_node[node]["width"], size_by_node[node]["height"])

    for left in sorted(moved):
        for right in sorted(fixed):
            if _rect_overlap(box(left), box(right)):
                return left, right
    return None


def _scc_records(
    island_members: dict[str, list[str]],
    exec_adjacency: dict[str, list[str]],
    exec_nodes: set[str],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    index = 0
    for island_id in sorted(island_members):
        members = [member for member in island_members[island_id] if member in exec_nodes]
        components = _tarjan(members, exec_adjacency)
        for component in components:
            if len(component) <= 1 and component[0] not in exec_adjacency.get(component[0], []):
                continue
            back_edges = []
            member_set = set(component)
            for source in component:
                for target in exec_adjacency.get(source, []):
                    if target in member_set and source >= target:
                        back_edges.append(f"{source}->{target}")
            records.append(
                {
                    "sccId": f"scc:{index}",
                    "islandId": island_id,
                    "nodeIds": sorted(component),
                    "backEdges": sorted(back_edges),
                }
            )
            index += 1
    return records


def recompute_layout_hash(plan: dict[str, Any]) -> str:
    return _layout_hash(plan.get("positions", []))


def _rejected(
    code: str,
    message: str,
    path: str = "$",
    context: dict[str, Any] | None = None,
) -> LayoutPlan:
    return LayoutPlan(
        status="rejected",
        plan={},
        errors=[make_diagnostic(code, message, path=path, context=context)],
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _request_int(
    container: dict[str, Any],
    name: str,
    default: int,
    minimum: int,
    maximum: int,
    path: str,
) -> tuple[int, LayoutPlan | None]:
    value = container.get(name, default)
    if not _is_int(value) or value < minimum or value > maximum:
        return default, _rejected(
            "contract-schema-type-mismatch",
            f"{name} must be an integer in [{minimum}, {maximum}]",
            path,
        )
    return value, None


def preview_graph_layout(
    typed_graph_document: dict[str, Any],
    request: dict[str, Any] | None = None,
) -> LayoutPlan:
    """Return a deterministic LayoutPlan for a complete TypedGraph.

    This is a **read-only** API: the input graph and request are deep-copied,
    the deterministic layout algorithm runs in memory, and neither Unreal nor the
    filesystem is touched.  A successful plan is still only ``same-process``
    evidence; independent UE reload remains mandatory before acceptance
    (D-12).

    Request constraints enforced here (in addition to the algorithm's checks):

    * ``mode`` is one of ``full``, ``selection``, ``incremental``.
    * ``selection``/``incremental`` require a non-empty, duplicate-free
      ``scope`` of existing node ids; ``full`` must not carry a scope.
    * spacing is ``1..4096``, target width is ``256..16384`` and seed is a
      non-negative int32.
    * every output coordinate is an int32 and the returned layout hash is
      recomputed and checked.  A post-layout semantic hash change is rejected.

    A rejected preview returns ``LayoutPlan(plan={})`` and diagnostics; callers
    must not build a write plan from it.
    """

    if not isinstance(typed_graph_document, dict):
        return _rejected("layout-invalid-graph", "TypedGraph must be a JSON object", "$")
    graph = copy.deepcopy(typed_graph_document)
    if request is None:
        request_value: dict[str, Any] = {}
    else:
        try:
            request_value = copy.deepcopy(request)
        except Exception:
            return _rejected("contract-schema-type-mismatch", "request must be a JSON object", "$.request")
    if not isinstance(request_value, dict):
        return _rejected("contract-schema-type-mismatch", "request must be a JSON object", "$.request")

    mode = request_value.get("mode", "full")
    if mode not in LAYOUT_MODES:
        return _rejected(
            "contract-schema-enum-mismatch",
            "mode must be incremental|selection|full",
            "$.mode",
        )

    options = request_value.get("options", {})
    if not isinstance(options, dict):
        return _rejected("contract-schema-type-mismatch", "options must be an object", "$.options")
    _, error = _request_int(options, "spacingX", DEFAULT_SPACING_X, 1, 4096, "$.options.spacingX")
    if error:
        return error
    _, error = _request_int(options, "spacingY", DEFAULT_SPACING_Y, 1, 4096, "$.options.spacingY")
    if error:
        return error
    _, error = _request_int(options, "seed", 0, 0, 2_147_483_647, "$.options.seed")
    if error:
        return error

    packing = request_value.get("packing", {})
    if not isinstance(packing, dict):
        return _rejected("contract-schema-type-mismatch", "packing must be an object", "$.packing")
    _, error = _request_int(packing, "targetWidth", DEFAULT_TARGET_WIDTH, 256, 16384, "$.packing.targetWidth")
    if error:
        return error

    graph_nodes = graph.get("nodes")
    node_ids = {
        str(node.get("nodeId"))
        for node in graph_nodes
        if isinstance(node, dict) and isinstance(node.get("nodeId"), str)
    } if isinstance(graph_nodes, list) else set()
    for node in graph_nodes if isinstance(graph_nodes, list) else []:
        if not isinstance(node, dict):
            continue
        position = node.get('position') or {}
        if not isinstance(position, dict):
            return _rejected('graph-invalid-coordinate', 'Node position must be an object', '$.nodes')
        for axis in ('x', 'y'):
            coordinate = position.get(axis, 0)
            if not _is_int(coordinate) or not -MAX_ABS_POSITION - 1 <= coordinate <= MAX_ABS_POSITION:
                return _rejected('graph-invalid-coordinate', 'Input position must be a signed int32', '$.nodes')
    scope = request_value.get("scope")
    if mode == "full":
        if scope is not None:
            return _rejected("layout-scope-violation", "full mode must not carry a partial scope", "$.scope")
    else:
        if not isinstance(scope, list) or not scope:
            return _rejected(
                "layout-scope-violation",
                "selection/incremental mode requires a non-empty scope",
                "$.scope",
            )
        if any(not isinstance(item, str) for item in scope):
            return _rejected("layout-scope-violation", "scope entries must be node id strings", "$.scope")
        if len(set(scope)) != len(scope):
            return _rejected("layout-scope-violation", "scope must not contain duplicate node ids", "$.scope")
        unknown = sorted(set(scope) - node_ids)
        if unknown:
            return _rejected(
                "layout-scope-violation",
                "scope contains unknown node ids: " + ", ".join(unknown),
                "$.scope",
            )

    # The algorithm validates hashes and returns candidate diagnostics.  A
    # malformed but hash-shaped graph must still fail closed, never escape as a
    # traceback from a read-only planning API.
    try:
        result = layout_graph(graph, request_value)
    except Exception as exc:  # pragma: no cover - defensive contract boundary
        return _rejected(
            "layout-invalid-graph",
            f"layout rejected malformed input: {type(exc).__name__}: {exc}",
            "$",
        )
    if result.status != "ok":
        return result

    plan = result.plan
    expected_positions = sorted(node_ids)
    actual_positions = sorted(
        str(item.get("nodeId"))
        for item in plan.get("positions", [])
        if isinstance(item, dict)
    )
    if actual_positions != expected_positions:
        return _rejected(
            "layout-nondeterministic",
            "LayoutPlan must contain exactly one position per input node",
            "$.positions",
        )
    for item in plan.get("positions", []):
        x = item.get("x")
        y = item.get("y")
        if not _is_int(x) or not _is_int(y):
            return _rejected("graph-invalid-coordinate", "positions must be integer coordinates", "$.positions")
        if not (-MAX_ABS_POSITION - 1 <= x <= MAX_ABS_POSITION) or not (-MAX_ABS_POSITION - 1 <= y <= MAX_ABS_POSITION):
            return _rejected(
                "graph-invalid-coordinate",
                "positions exceed the UE int32 NodePos range",
                "$.positions",
            )
        size = item.get("size")
        if not isinstance(size, dict) or not _is_int(size.get("width")) or not _is_int(size.get("height")):
            return _rejected("contract-schema-type-mismatch", "position size must be integers", "$.positions")
    if plan.get("layoutHash") != recompute_layout_hash(plan):
        return _rejected("layout-nondeterministic", "layoutHash does not match positions", "$.layoutHash")
    expected_semantic = typed_graph.semantic_hash(graph)
    if plan.get("baseGraphHash") != expected_semantic:
        return _rejected("layout-semantic-changed", "Layout must preserve baseGraphHash", "$.baseGraphHash")
    moved = plan.get("movedNodeIds")
    if not isinstance(moved, list) or any(not isinstance(item, str) for item in moved):
        return _rejected("contract-schema-type-mismatch", "movedNodeIds must be a string array", "$.movedNodeIds")
    if mode != "full" and not set(moved).issubset(set(scope or [])):
        return _rejected("layout-scope-violation", "Layout moved a node outside the requested scope", "$.movedNodeIds")
    if len(moved) > MAX_LAYOUT_POSITIONS:
        return _rejected(
            "graphpatch-limit-exceeded",
            f"layout-only allows at most {MAX_LAYOUT_POSITIONS} position operations; plan has {len(moved)}",
            "$.movedNodeIds",
            {"movedNodeCount": len(moved), "limit": MAX_LAYOUT_POSITIONS},
        )

    # D-12: same-process preview is never acceptance evidence.  Keep these
    # fields fixed even if a future algorithm accidentally changes them.
    plan["evidenceLevel"] = "same-process"
    plan["acceptanceEligible"] = False
    plan["requiredAcceptanceEvidence"] = "independent-reload"
    return result


__all__ = [
    "BLOCKING_CODES",
    "COMMENT_PADDING",
    "DEFAULT_SPACING_X",
    "DEFAULT_SPACING_Y",
    "DEFAULT_TARGET_WIDTH",
    "LAYOUT_MODES",
    "LAYOUT_PLAN_VERSION",
    "LAYOUT_WARNING_CODES",
    "MAX_ABS_POSITION",
    "MAX_LAYOUT_POSITIONS",
    "LayoutPlan",
    "estimate_size",
    "layout_graph",
    "node_box",
    "preview_graph_layout",
    "recompute_layout_hash",
]

"""LayoutPlan 1.0 candidate: deterministic, pure, semantic-preserving layout.

Clause IDs: LP-PURE-FUNCTION, LP-ISLANDS, LP-PURE-NODES, LP-SCC, LP-PACKING,
LP-HASH, LP-SEED, LP-METRICS, LP-OVERLAP, LP-WARNINGS, LP-SCOPE, LP-SIZE.

The algorithm is intentionally small but complete enough to exercise the
contract: WCC islands over Exec edges, Tarjan SCC condensation, pure-data
attribution without duplication, deterministic tie-breaks, shelf packing,
integer coordinates and exact metrics.  It uses no random source; ``seed`` is
reserved for future versions (CR-LP-02).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from . import typed_graph
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
    index_counter = [0]
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    low: dict[str, int] = {}
    components: list[list[str]] = []

    def visit(node: str) -> None:
        indexes[node] = index_counter[0]
        low[node] = index_counter[0]
        index_counter[0] += 1
        stack.append(node)
        on_stack.add(node)
        for neighbour in adjacency.get(node, []):
            if neighbour not in indexes:
                visit(neighbour)
                low[node] = min(low[node], low[neighbour])
            elif neighbour in on_stack:
                low[node] = min(low[node], indexes[neighbour])
        if low[node] == indexes[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            components.append(sorted(component))

    for node in sorted(nodes):
        if node not in indexes:
            visit(node)
    return components


def _wcc(nodes: list[str], adjacency: dict[str, list[str]]) -> list[list[str]]:
    undirected: dict[str, set[str]] = {node: set() for node in nodes}
    for source, targets in adjacency.items():
        for target in targets:
            undirected.setdefault(source, set()).add(target)
            undirected.setdefault(target, set()).add(source)
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


def _layout_island(
    island_id: str,
    node_ids: list[str],
    exec_adjacency: dict[str, list[str]],
    size_by_node: dict[str, dict[str, Any]],
    event_nodes: set[str],
) -> tuple[dict[str, tuple[int, int]], list[dict[str, Any]]]:
    components = _tarjan(node_ids, exec_adjacency)
    component_of: dict[str, int] = {}
    for index, component in enumerate(components):
        for node in component:
            component_of[node] = index
    condensation: dict[int, list[int]] = {index: [] for index in range(len(components))}
    for source in node_ids:
        for target in exec_adjacency.get(source, []):
            if target not in component_of:
                continue
            left = component_of[source]
            right = component_of[target]
            if left != right and right not in condensation[left]:
                condensation[left].append(right)
    ordered_components = [str(index) for index in range(len(components))]
    component_layers = _longest_layers(ordered_components, {str(key): [str(v) for v in value] for key, value in condensation.items()})
    layers: dict[str, int] = {}
    for node in node_ids:
        layers[node] = component_layers.get(str(component_of[node]), 0)
    # Entry nodes of cycles first, then stable nodeId ordering.
    layer_nodes: dict[int, list[str]] = {}
    for node in node_ids:
        layer_nodes.setdefault(layers[node], []).append(node)
    for layer in layer_nodes:
        layer_nodes[layer].sort(key=lambda node: (0 if node in event_nodes else 1, node))
    # Deterministic barycenter sweeps on the condensation layout.
    predecessors: dict[str, list[str]] = {node: [] for node in node_ids}
    for source in node_ids:
        for target in exec_adjacency.get(source, []):
            if target in predecessors:
                predecessors[target].append(source)
    order_index = {node: index for index, node in enumerate(node_ids)}
    for _ in range(2):
        for layer in sorted(layer_nodes):
            if layer == 0:
                continue
            def barycenter(node: str) -> tuple[float, int, str]:
                values = [order_index[pred] for pred in predecessors.get(node, []) if pred in order_index]
                return (sum(values) / len(values) if values else 1e9, order_index[node], node)
            layer_nodes[layer].sort(key=barycenter)
    positions: dict[str, tuple[int, int]] = {}
    x = 0
    for layer in sorted(layer_nodes):
        width = max(size_by_node[node]["width"] for node in layer_nodes[layer])
        y = 0
        for node in layer_nodes[layer]:
            positions[node] = (x, y)
            y += size_by_node[node]["height"] + DEFAULT_SPACING_Y
        x += width + DEFAULT_SPACING_X
    return positions, []


def _layout_data_only(
    node_ids: list[str],
    data_adjacency: dict[str, list[str]],
    size_by_node: dict[str, dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    layers = _longest_layers(node_ids, data_adjacency)
    layer_nodes: dict[int, list[str]] = {}
    for node in node_ids:
        layer_nodes.setdefault(layers[node], []).append(node)
    positions: dict[str, tuple[int, int]] = {}
    x = 0
    for layer in sorted(layer_nodes):
        width = max(size_by_node[node]["width"] for node in layer_nodes[layer])
        y = 0
        for node in sorted(layer_nodes[layer]):
            positions[node] = (x, y)
            y += size_by_node[node]["height"] + DEFAULT_SPACING_Y
        x += width + DEFAULT_SPACING_X
    return positions


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
    reroute_nodes = {node_id for node_id, node in nodes.items() if node.get("kind") == "reroute"}
    event_nodes = {node_id for node_id, node in nodes.items() if node.get("kind") == "event"}
    content_nodes = set(nodes) - comment_nodes

    def pin_owner(pin_id_value: str) -> str:
        pin = next((item for item in graph.get("pins", []) if item.get("pinId") == pin_id_value), None)
        return str(pin.get("nodeId", "")) if pin else ""

    all_edges: list[dict[str, Any]] = []
    exec_adjacency: dict[str, list[str]] = {node: [] for node in content_nodes}
    data_adjacency: dict[str, list[str]] = {node: [] for node in content_nodes}
    for edge in graph.get("edges", []):
        source = (edge.get("from") or {}).get("pinId", "")
        target = (edge.get("to") or {}).get("pinId", "")
        source_node = pin_owner(source)
        target_node = pin_owner(target)
        if source_node not in content_nodes or target_node not in content_nodes:
            continue
        all_edges.append(
            {"source": source_node, "target": target_node, "kind": edge.get("kind"), "edgeId": edge.get("edgeId", "")}
        )
        if edge.get("kind") == "exec":
            exec_adjacency[source_node].append(target_node)
        else:
            data_adjacency[source_node].append(target_node)

    for reroute in sorted(reroute_nodes):
        incoming = sum(1 for edge in all_edges if edge["target"] == reroute)
        outgoing = sum(1 for edge in all_edges if edge["source"] == reroute)
        if incoming != 1 or outgoing != 1:
            errors.append(
                make_diagnostic(
                    "layout-reroute-broken",
                    "A Reroute must have exactly one incoming and one outgoing link",
                    path=f"$.nodes[?(@.nodeId=='{reroute}')]",
                    context={"incoming": incoming, "outgoing": outgoing},
                )
            )

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
    for pure in sorted(pure_nodes):
        consumers = sorted({island_of[target] for target in data_adjacency.get(pure, []) if target in island_of})
        if not consumers:
            data_only.append(pure)
            continue
        counts = {island: sum(1 for target in data_adjacency.get(pure, []) if island_of.get(target) == island) for island in consumers}
        best = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]
        pure_assignments[pure] = best
        island_members[best].append(pure)
        if len(consumers) > 1:
            warnings.append(
                make_diagnostic(
                    "layout-shared-pure-node",
                    "Shared pure node is assigned to one island and never duplicated",
                    path=f"$.nodes[?(@.nodeId=='{pure}')]",
                    severity="warning",
                    context={"primaryIsland": best, "consumerIslands": consumers},
                )
            )
            warnings.append(
                make_diagnostic(
                    "layout-cross-island-data-edge",
                    "Data edges cross islands; they do not merge Exec islands",
                    path=f"$.nodes[?(@.nodeId=='{pure}')]",
                    severity="warning",
                    context={"consumerIslands": consumers},
                )
            )

    local_positions: dict[str, tuple[int, int]] = {}
    island_layouts: list[tuple[str, dict[str, tuple[int, int]], dict[str, dict[str, Any]]]] = []
    for island_id in sorted(island_members):
        members = island_members[island_id]
        exec_members = sorted(member for member in members if member in exec_nodes)
        positions, _ = _layout_island(island_id, exec_members, exec_adjacency, size_by_node, event_nodes)
        exec_layer = _longest_layers(exec_members, exec_adjacency)
        max_exec_layer = max(exec_layer.values(), default=0)
        pure_members = sorted(member for member in members if member in pure_assignments)
        pure_layers: dict[str, int] = {}
        for _ in range(len(pure_members) + 1):
            changed = False
            for pure in pure_members:
                values: list[int] = []
                for target in data_adjacency.get(pure, []):
                    if target in exec_layer:
                        values.append(int(exec_layer[target]))
                    elif target in pure_layers:
                        values.append(int(pure_layers[target]))
                if values:
                    candidate = max(0, min(values) - 1)
                    if pure_layers.get(pure) != candidate:
                        pure_layers[pure] = candidate
                        changed = True
            if not changed:
                break
        layer_x: dict[int, int] = {}
        layer_next_y: dict[int, int] = {}
        for node, position in positions.items():
            layer = int(exec_layer.get(node, 0))
            layer_x[layer] = min(layer_x.get(layer, position[0]), position[0])
            layer_next_y[layer] = max(
                layer_next_y.get(layer, 0),
                position[1] + size_by_node[node]["height"] + spacing_y,
            )
        for pure in sorted(pure_members, key=lambda node: (pure_layers.get(node, 0), node)):
            layer = min(max(int(pure_layers.get(pure, 0)), 0), max(max_exec_layer, 0))
            x = layer_x.get(layer, layer * (size_by_node[pure]["width"] + spacing_x))
            y = layer_next_y.get(layer, 0)
            positions[pure] = (x, y)
            layer_next_y[layer] = y + size_by_node[pure]["height"] + spacing_y
        island_layouts.append((island_id, positions, size_by_node))

    if data_only:
        data_positions = _layout_data_only(sorted(data_only), data_adjacency, size_by_node)
        island_layouts.append(("island:data-only", data_positions, size_by_node))

    packed_positions, island_records = _pack_islands(island_layouts, target_width, spacing_x)
    local_positions.update(packed_positions)

    # Comment handling: move only when all contained nodes are inside the scope.
    scope = request.get("scope")
    scope_set = set(str(item) for item in scope) if isinstance(scope, list) else None
    for comment_id in sorted(comment_nodes):
        comment = nodes[comment_id]
        bounds = (comment.get("bounds") or {})
        contained = [
            node_id
            for node_id in content_nodes
            if _contains_point(bounds, nodes[node_id]) is True
        ]
        in_scope = mode == "full" or scope_set is None or all(node in scope_set for node in contained)
        if not in_scope:
            warnings.append(
                make_diagnostic(
                    "layout-comment-partial-scope",
                    "Comment partially overlaps the layout scope and was not moved",
                    path=f"$.nodes[?(@.nodeId=='{comment_id}')]",
                    severity="warning",
                )
            )
            local_positions[comment_id] = (int(bounds.get("x", 0)), int(bounds.get("y", 0)))
            size_by_node[comment_id] = {
                "width": int(bounds.get("width", 0)),
                "height": int(bounds.get("height", 0)),
                "source": "stored",
            }
            continue
        if not contained:
            local_positions[comment_id] = (int(bounds.get("x", 0)), int(bounds.get("y", 0)))
            continue
        min_x = min(local_positions[node][0] for node in contained)
        min_y = min(local_positions[node][1] for node in contained)
        max_x = max(local_positions[node][0] + size_by_node[node]["width"] for node in contained)
        max_y = max(local_positions[node][1] + size_by_node[node]["height"] for node in contained)
        local_positions[comment_id] = (min_x - COMMENT_PADDING, min_y - COMMENT_PADDING)
        size_by_node[comment_id] = {
            "width": (max_x - min_x) + 2 * COMMENT_PADDING,
            "height": (max_y - min_y) + 2 * COMMENT_PADDING,
            "source": "stored",
        }

    # Scope handling for incremental/selection: fixed nodes keep their bytes.
    fixed_nodes = set()
    if mode != "full":
        if scope_set is None or not scope_set:
            scope_set = set()
        fixed_nodes = content_nodes - scope_set
        for node_id in fixed_nodes:
            original = nodes[node_id].get("position") or {}
            local_positions[node_id] = (int(original.get("x", 0)), int(original.get("y", 0)))
        moved = {node for node in scope_set if node in local_positions}
        conflict = _first_overlap(moved, fixed_nodes, local_positions, size_by_node)
        if conflict:
            for attempt in range(1, 64):
                dy = attempt * 64
                shifted = {node: (local_positions[node][0], local_positions[node][1] + dy) for node in moved}
                trial = dict(local_positions)
                trial.update(shifted)
                if not _first_overlap(moved, fixed_nodes, trial, size_by_node):
                    local_positions.update(shifted)
                    conflict = None
                    break
        if conflict:
            errors.append(make_diagnostic("layout-no-space", "No space for the layout scope without overlapping fixed nodes", path="$.scope"))
        for node_id in scope_set:
            if node_id not in content_nodes and node_id not in comment_nodes:
                errors.append(make_diagnostic("layout-scope-violation", f"scope contains unknown node {node_id}", path="$.scope"))

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

    layers: dict[str, int] = {}
    for island_id, members in island_members.items():
        exec_members = [member for member in members if member in exec_nodes]
        member_layers = _longest_layers(sorted(exec_members), exec_adjacency)
        layers.update({node: int(member_layers.get(node, 0)) for node in exec_members})
    for node in data_only:
        layers.setdefault(node, 0)

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

"""Private deterministic layout helpers; coordinates only, no graph mutation.

Exec edges carry eight times the ordering weight of data edges. SCCs are
condensed before ranking; feedback edges remain visible rather than being
removed. Comment frames are fixed-size compound obstacles. Their inferred
memberships use the same original node centres as the position-only Apply gate.
"""
from __future__ import annotations

from fractions import Fraction
from heapq import heappop, heappush
from typing import Any


def components(nodes: list[str], adjacency: dict[str, list[str]]) -> list[list[str]]:
    """Iterative Kosaraju: a long authored chain must not exhaust Python's stack."""
    allowed = set(nodes)
    outgoing = {n: sorted(set(adjacency.get(n, ())) & allowed) for n in allowed}
    reverse: dict[str, list[str]] = {n: [] for n in allowed}
    for n in sorted(allowed):
        for target in outgoing[n]:
            reverse[target].append(n)
    visited: set[str] = set()
    finished: list[str] = []
    for root in sorted(allowed):
        if root in visited:
            continue
        visited.add(root)
        stack = [(root, iter(outgoing[root]))]
        while stack:
            current, iterator = stack[-1]
            target = next(iterator, None)
            if target is None:
                finished.append(current)
                stack.pop()
            elif target not in visited:
                visited.add(target)
                stack.append((target, iter(outgoing[target])))
    result: list[list[str]] = []
    visited.clear()
    for root in reversed(finished):
        if root in visited:
            continue
        visited.add(root)
        stack = [root]
        members = []
        while stack:
            current = stack.pop()
            members.append(current)
            for target in reversed(reverse[current]):
                if target not in visited:
                    visited.add(target)
                    stack.append(target)
        result.append(sorted(members))
    return sorted(result, key=lambda c: c[0])


def layered(
    node_ids: list[str], edges: list[dict[str, Any]], sizes: dict[str, dict[str, Any]],
    nodes: dict[str, dict[str, Any]], spacing_x: int, spacing_y: int, rank_only: bool = False,
) -> tuple[dict[str, tuple[int, int]], dict[str, int]]:
    """SCC ranking and bidirectional weighted median ordering with updated ranks."""
    if not node_ids:
        return {}, {}
    node_ids = sorted(node_ids)
    members = set(node_ids)
    links = sorted((e for e in edges if e['source'] in members and e['target'] in members),
                   key=lambda e: (e['source'], e.get('sourceIndex', 0), e['target'], e.get('targetIndex', 0)))
    adjacency: dict[str, list[str]] = {n: [] for n in node_ids}
    predecessors: dict[str, list[dict[str, Any]]] = {n: [] for n in node_ids}
    successors: dict[str, list[dict[str, Any]]] = {n: [] for n in node_ids}
    for edge in links:
        adjacency[edge['source']].append(edge['target'])
        predecessors[edge['target']].append(edge)
        successors[edge['source']].append(edge)
    sccs = components(node_ids, adjacency)
    component_of = {n: index for index, group in enumerate(sccs) for n in group}
    outgoing: dict[int, set[int]] = {i: set() for i in range(len(sccs))}
    indegree = {i: 0 for i in outgoing}
    for edge in links:
        left, right = component_of[edge['source']], component_of[edge['target']]
        if left != right and right not in outgoing[left]:
            outgoing[left].add(right)
            indegree[right] += 1
    starts = {i: 0 for i in outgoing}
    ready = sorted(i for i in outgoing if not indegree[i])
    layers: dict[str, int] = {}
    original_y = lambda n: int((nodes.get(n, {}).get('position') or {}).get('y', 0))
    while ready:
        current = ready.pop(0)
        group = sccs[current]
        # Only feedback edges point left inside a cycle. Walk Exec edges first,
        # then data, preserving output-pin order and authored Y as tie-breaks.
        order: list[str] = []
        visited: set[str] = set()
        roots = sorted(group, key=lambda n: (nodes.get(n, {}).get('kind') != 'event', original_y(n), n))
        for root in roots:
            stack = [root]
            while stack:
                n = stack.pop()
                if n in visited:
                    continue
                visited.add(n)
                order.append(n)
                targets = sorted((e for e in successors[n] if component_of[e['target']] == current),
                                 key=lambda e: (e['kind'] != 'exec', e.get('sourceIndex', 0),
                                                original_y(e['target']), e['target']))
                stack.extend(e['target'] for e in reversed(targets))
        for offset, n in enumerate(order):
            layers[n] = starts[current] + offset
        for target in sorted(outgoing[current]):
            starts[target] = max(starts[target], starts[current] + len(group))
            indegree[target] -= 1
            if not indegree[target]:
                ready.append(target)
                ready.sort()
    # Right-align pure producers toward their earliest consumer. Exec ranks
    # stay fixed, while upstream pure chains retain a strictly forward edge.
    exec_members = {e['source'] for e in links if e['kind'] == 'exec'} | {e['target'] for e in links if e['kind'] == 'exec'}
    for component in sorted(sccs, key=lambda c: (-max(layers[n] for n in c), c[0])):
        if any(n in exec_members for n in component):
            continue
        exits = [layers[e['target']] for n in component for e in successors[n]
                 if component_of[e['target']] != component_of[n]]
        if exits:
            shift = max(0, min(exits) - 1 - max(layers[n] for n in component))
            for n in component:
                layers[n] += shift
    if rank_only:
        return {}, layers
    columns: dict[int, list[str]] = {}
    exec_nodes = {e['source'] for e in links if e['kind'] == 'exec'} | {e['target'] for e in links if e['kind'] == 'exec'}
    for n in node_ids:
        columns.setdefault(layers[n], []).append(n)
    for column in columns.values():
        column.sort(key=lambda n: (n not in exec_nodes, original_y(n), n))

    def rank_map() -> dict[str, int]:
        return {n: index for column in columns.values() for index, n in enumerate(column)}

    def cost() -> tuple[int, int, tuple[str, ...]]:
        rank = rank_map()
        crossing = 0
        # Adjacent-layer inversions are a deterministic surrogate, not an
        # Editor spline-intersection measurement. Long edges participate in
        # ordering sweeps and edge-span tie-breaking below.
        by_layer: dict[int, list[dict[str, Any]]] = {}
        for edge in links:
            if layers[edge['target']] == layers[edge['source']] + 1:
                by_layer.setdefault(layers[edge['source']], []).append(edge)
        for edge_list in by_layer.values():
            for i, first in enumerate(edge_list):
                for second in edge_list[i + 1:]:
                    a, b, c, d = first['source'], first['target'], second['source'], second['target']
                    if a != c and b != d and (rank[a] - rank[c]) * (rank[b] - rank[d]) < 0:
                        crossing += (8 if first['kind'] == 'exec' else 1) * (8 if second['kind'] == 'exec' else 1)
        span = sum(abs(rank[e['source']] - rank[e['target']]) * (8 if e['kind'] == 'exec' else 1) for e in links)
        return crossing, span, tuple(n for layer in sorted(columns) for n in columns[layer])

    best = {layer: list(column) for layer, column in columns.items()}
    best_cost = cost()
    for _ in range(4):
        for forward in (True, False):
            rank = rank_map()
            for layer in sorted(columns, reverse=not forward):
                def barycenter(n: str) -> tuple[Any, ...]:
                    weighted = []
                    for edge in predecessors[n] if forward else successors[n]:
                        other = edge['source'] if forward else edge['target']
                        if (layers[other] < layer) != forward or layers[other] == layer:
                            continue
                        weight = 8 if edge['kind'] == 'exec' else 1
                        pin_order = edge.get('sourceIndex' if forward else 'targetIndex', 0)
                        weighted.append((Fraction(rank[other] * 1024 + pin_order, 1024), weight))
                    value = sum((v * w for v, w in weighted), Fraction(0)) / sum(w for _, w in weighted) if weighted else Fraction(rank[n])
                    return value, n not in exec_nodes, original_y(n), rank[n], n
                columns[layer].sort(key=barycenter)
                rank.update({n: i for i, n in enumerate(columns[layer])})
            candidate = cost()
            if candidate < best_cost:
                best_cost = candidate
                best = {layer: list(column) for layer, column in columns.items()}
    columns = best
    positions: dict[str, tuple[int, int]] = {}
    x = 0
    for layer in sorted(columns):
        y = 0
        for n in columns[layer]:
            positions[n] = (x, y)
            y += sizes[n]['height'] + spacing_y
        x += max(sizes[n]['width'] for n in columns[layer]) + spacing_x
    # Align a singleton continuation to its weighted predecessor ordinate;
    # each column retains the selected order and a strict separation bound.
    for layer in sorted(columns):
        cursor = 0
        for n in columns[layer]:
            desired = [(positions[e['source']][1], 8 if e['kind'] == 'exec' else 1)
                       for e in predecessors[n] if layers[e['source']] < layer]
            wanted = sum(y * w for y, w in desired) // sum(w for _, w in desired) if desired else positions[n][1]
            y = max(cursor, wanted)
            positions[n] = (positions[n][0], y)
            cursor = y + sizes[n]['height'] + spacing_y
    return positions, layers


def overlap(left: tuple[int, int, int, int], right: tuple[int, int, int, int]) -> bool:
    return min(left[0] + left[2], right[0] + right[2]) > max(left[0], right[0]) and min(left[1] + left[3], right[1] + right[3]) > max(left[1], right[1])


def bounds(positions: dict[str, tuple[int, int]], sizes: dict[str, dict[str, Any]]) -> tuple[int, int, int, int]:
    x = min(p[0] for p in positions.values())
    y = min(p[1] for p in positions.values())
    return x, y, max(p[0] + sizes[n]['width'] for n, p in positions.items()) - x, max(p[1] + sizes[n]['height'] for n, p in positions.items()) - y


def nearest_candidates(xs: set[int], ys: set[int], ox: int, oy: int, limit: int = 4096):
    """Yield nearest XY pairs without materialising their Cartesian product."""
    xs_order = sorted(xs, key=lambda x: (abs(x - ox), x))
    ys_order = sorted(ys, key=lambda y: (abs(y - oy), y))
    if not xs_order or not ys_order:
        return
    heap = []
    visited = {(0, 0)}
    def push(i: int, j: int) -> None:
        x, y = xs_order[i], ys_order[j]
        heappush(heap, (abs(x - ox) + abs(y - oy), abs(y - oy), y, x, i, j))
    push(0, 0)
    for _ in range(limit):
        if not heap:
            return
        _, _, y, x, i, j = heappop(heap)
        yield x, y
        for ni, nj in ((i + 1, j), (i, j + 1)):
            if ni < len(xs_order) and nj < len(ys_order) and (ni, nj) not in visited:
                visited.add((ni, nj))
                push(ni, nj)


def constrained(
    members: list[str], nodes: dict[str, dict[str, Any]], sizes: dict[str, dict[str, Any]],
    frames: dict[str, tuple[int, int, int, int]], memberships: dict[str, set[str]], spacing: int,
) -> dict[str, tuple[int, int]] | None:
    """Nearest feasible rect placement in immutable frames; no synthetic resize.

    First require complete rectangles inside frames. If estimated geometry is
    larger than the authored frame, the second pass follows Apply's original
    centre containment rule; the surrounding compound bbox includes overhangs.
    """
    originals = {n: (int(nodes[n]['position']['x']), int(nodes[n]['position']['y'])) for n in members}
    orders = [sorted(members, key=lambda n: (originals[n][1], originals[n][0], n)),
              sorted(members, key=lambda n: (-sizes[n]['height'] * sizes[n]['width'], originals[n][1], n))]
    best = None
    best_cost = None
    for whole_rect in (True, False):
        for order in orders:
            placed: dict[str, tuple[int, int]] = {}
            for n in order:
                w, h = sizes[n]['width'], sizes[n]['height']
                containing = [frames[c] for c in sorted(frames) if n in memberships[c]]
                if whole_rect:
                    low_x = max(f[0] for f in containing)
                    high_x = min(f[0] + f[2] - w for f in containing)
                    low_y = max(f[1] for f in containing)
                    high_y = min(f[1] + f[3] - h for f in containing)
                else:
                    raw = nodes[n].get('size') or {}
                    raw_x = max(1, int(raw.get('width', 0))) // 2
                    raw_y = max(1, int(raw.get('height', 0))) // 2
                    low_x = max(f[0] - min(w // 2, raw_x) for f in containing)
                    high_x = min(f[0] + f[2] - max(w // 2, raw_x) for f in containing)
                    low_y = max(f[1] - min(h // 2, raw_y) for f in containing)
                    high_y = min(f[1] + f[3] - max(h // 2, raw_y) for f in containing)
                if low_x > high_x or low_y > high_y:
                    break
                ox, oy = originals[n]
                xs = {max(low_x, min(high_x, ox)), low_x, high_x}
                ys = {max(low_y, min(high_y, oy)), low_y, high_y}
                for other, (px, py) in placed.items():
                    xs.update((px + sizes[other]['width'] + spacing, px - w - spacing))
                    ys.update((py + sizes[other]['height'] + spacing, py - h - spacing))
                trials = nearest_candidates({x for x in xs if low_x <= x <= high_x},
                                            {y for y in ys if low_y <= y <= high_y}, ox, oy)
                for x, y in trials:
                    box = (x, y, w, h)
                    if any(n not in memberships[c] and overlap(box, frame) for c, frame in frames.items()):
                        continue
                    if not any(overlap(box, (p[0], p[1], sizes[m]['width'], sizes[m]['height'])) for m, p in placed.items()):
                        placed[n] = (x, y)
                        break
                else:
                    break
            if len(placed) == len(members):
                displacement = sum(abs(placed[n][0] - originals[n][0]) + abs(placed[n][1] - originals[n][1]) for n in members)
                candidate_cost = (displacement, tuple((n, *placed[n]) for n in sorted(placed)))
                if best_cost is None or candidate_cost < best_cost:
                    best, best_cost = placed, candidate_cost
        if best is not None:
            return best
    return None


def compound(
    positions: dict[str, tuple[int, int]], nodes: dict[str, dict[str, Any]],
    sizes: dict[str, dict[str, Any]], edges: list[dict[str, Any]],
    comment_bounds: dict[str, dict[str, Any]], memberships: dict[str, set[str]],
    spacing_x: int, spacing_y: int, target_width: int,
) -> tuple[dict[str, tuple[int, int]], list[str]]:
    """Pack immutable comment groups as units, retaining their authored membership."""
    if not comment_bounds:
        return positions, []
    content = set(positions)
    owner = {n: n for n in content}
    frames = {c: (b['x'], b['y'], b['width'], b['height']) for c, b in comment_bounds.items()}
    parent = {c: c for c in frames}
    def find(c: str) -> str:
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c
    def union(left: str, right: str) -> None:
        a, b = sorted((find(left), find(right)))
        parent[b] = a
    # Nested frames are a compound even when the inner frame is empty. Keep
    # their authored offset; geometric containment, not mere intersection, is
    # the evidence for this relationship.
    ordered_frames = sorted(frames)
    for index, left in enumerate(ordered_frames):
        lx, ly, lw, lh = frames[left]
        for right in ordered_frames[index + 1:]:
            rx, ry, rw, rh = frames[right]
            left_contains = lx <= rx and ly <= ry and rx + rw <= lx + lw and ry + rh <= ly + lh
            right_contains = rx <= lx and ry <= ly and lx + lw <= rx + rw and ly + lh <= ry + rh
            if left_contains or right_contains:
                union(left, right)
    # A member constrained by overlapping/nested frames belongs to one compound;
    # preserve all those frame offsets together instead of arbitrarily assigning it.
    for n in sorted(content):
        comments = sorted(c for c in frames if n in memberships[c])
        if comments:
            for c in comments[1:]:
                union(comments[0], c)
    groups: dict[str, list[str]] = {}
    for c in sorted(frames):
        groups.setdefault(find(c), []).append(c)
    entity_positions: dict[str, dict[str, tuple[int, int]]] = {}
    entity_sizes: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for root, comments in sorted(groups.items()):
        members = sorted(set().union(*(memberships[c] for c in comments)))
        local_frames = {c: frames[c] for c in comments}
        local: dict[str, tuple[int, int]] = {}
        if members and len(comments) == 1:
            candidate = {n: positions[n] for n in members}
            bx, by, width, height = bounds(candidate, sizes)
            frame = frames[comments[0]]
            if width + 48 <= frame[2] and height + 48 <= frame[3]:
                local = {n: (p[0] - bx + 24, p[1] - by + 24) for n, p in candidate.items()}
                local[comments[0]] = (0, 0)
        if not local:
            for padding in (min(spacing_y, 16), 0):
                candidate = constrained(members, nodes, sizes, local_frames, memberships, padding) if members else {}
                if candidate is not None:
                    local = candidate
                    local.update({c: (frames[c][0], frames[c][1]) for c in comments})
                    break
            else:
                failed.extend(comments)
                local = {n: (nodes[n]['position']['x'], nodes[n]['position']['y']) for n in members}
                local.update({c: (frames[c][0], frames[c][1]) for c in comments})
        bx, by, width, height = bounds(local, sizes)
        entity_positions[root] = {n: (p[0] - bx, p[1] - by) for n, p in local.items()}
        entity_sizes[root] = {'width': width, 'height': height}
        for n in members:
            owner[n] = root
    for n in sorted(content):
        if owner[n] == n:
            entity_positions[n] = {n: (0, 0)}
            entity_sizes[n] = sizes[n]
    collapsed = []
    for edge in edges:
        left, right = owner[edge['source']], owner[edge['target']]
        if left != right:
            collapsed.append({**edge, 'source': left, 'target': right})
    adjacency: dict[str, list[str]] = {n: [] for n in entity_positions}
    for edge in collapsed:
        adjacency[edge['source']].append(edge['target'])
        adjacency[edge['target']].append(edge['source'])
    remaining = set(entity_positions)
    entries = []
    while remaining:
        root = min(remaining)
        stack = [root]
        remaining.remove(root)
        group = []
        while stack:
            n = stack.pop()
            group.append(n)
            for target in sorted(adjacency[n]):
                if target in remaining:
                    remaining.remove(target)
                    stack.append(target)
        outer, _ = layered(group, collapsed, entity_sizes, nodes, spacing_x, spacing_y)
        bx, by, width, height = bounds(outer, entity_sizes)
        entries.append((root, outer, bx, by, width, height))
    result = {}
    cursor_x = cursor_y = row_height = 0
    for _, outer, bx, by, width, height in sorted(entries, key=lambda e: (-e[5], -e[4] * e[5], e[0])):
        if cursor_x and cursor_x + width > target_width:
            cursor_x = 0
            cursor_y += row_height + spacing_y
            row_height = 0
        for entity, p in outer.items():
            for n, local in entity_positions[entity].items():
                # The 24px origin margin keeps first-frame contents at the
                # historical content origin while accounting for frame bounds.
                result[n] = (cursor_x + p[0] - bx + local[0] - 24,
                             cursor_y + p[1] - by + local[1] - 24)
        cursor_x += width + spacing_x
        row_height = max(row_height, height)
    return result, failed


def refine(
    positions: dict[str, tuple[int, int]], content: set[str], edges: list[dict[str, Any]],
    sizes: dict[str, dict[str, Any]], memberships: dict[str, set[str]], spacing_y: int,
    nodes: dict[str, dict[str, Any]],
) -> dict[str, tuple[int, int]]:
    """Bounded geometric search over estimated pin segments.

    The public contract metric remains its historical adjacent-layer inversion
    count. This private objective also sees long edges. It never claims Slate
    spline geometry, and never trades overlaps or frame containment for a score.
    Large graphs use the linear-time rank/coordinate phase only; 200 nodes and
    400 edges are an explicit quality-search budget rather than an input limit.
    """
    if len(content) > 200 or len(edges) > 400 or len(edges) < 2:
        return positions
    result = dict(positions)
    ordered = sorted(content)
    edges = sorted(edges, key=lambda e: e['edgeId'])
    incident: dict[str, set[int]] = {n: set() for n in content}
    for i, edge in enumerate(edges):
        incident[edge['source']].add(i)
        incident[edge['target']].add(i)
    pairs = [(i, j) for i, a in enumerate(edges) for j in range(i + 1, len(edges))
             if {a['source'], a['target']}.isdisjoint({edges[j]['source'], edges[j]['target']})]
    pair_indices: dict[int, set[int]] = {i: set() for i in range(len(edges))}
    for k, (i, j) in enumerate(pairs):
        pair_indices[i].add(k)
        pair_indices[j].add(k)
    def line(edge: dict[str, Any], changed: dict[str, tuple[int, int]]) -> tuple[tuple[float, float], tuple[float, float]]:
        source, target = edge['source'], edge['target']
        sx, sy = changed.get(source, result[source])
        tx, ty = changed.get(target, result[target])
        return ((sx + sizes[source]['width'], sy + edge.get('sourceY', sizes[source]['height'] / 2)),
                (tx, ty + edge.get('targetY', sizes[target]['height'] / 2)))
    def crosses(first: Any, second: Any) -> bool:
        a, b = first
        c, d = second
        def orient(p: Any, q: Any, r: Any) -> float:
            return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return orient(a, b, c) * orient(a, b, d) < 0 and orient(c, d, a) * orient(c, d, b) < 0
    def length(segment: Any) -> float:
        return abs(segment[0][0] - segment[1][0]) + abs(segment[0][1] - segment[1][1])
    lines = [line(e, {}) for e in edges]
    pair_crosses = [int(crosses(lines[i], lines[j])) for i, j in pairs]
    cross_total = sum(pair_crosses)
    line_lengths = [length(segment) for segment in lines]
    length_total = sum(line_lengths)
    min_y = min(result[n][1] for n in content)
    max_y = max(result[n][1] + sizes[n]['height'] for n in content)
    frames_by_node = {n: [c for c in sorted(memberships) if n in memberships[c]] for n in content}
    def legal(changed: dict[str, tuple[int, int]]) -> bool:
        for n, (x, y) in changed.items():
            w, h = sizes[n]['width'], sizes[n]['height']
            if not -2147483648 <= x <= 2147483647 or not -2147483648 <= y <= 2147483647:
                return False
            if y < min_y or y + h > max_y:
                return False
            for c in frames_by_node[n]:
                cx, cy = result[c]
                raw = nodes[n].get('size') or {}
                raw_x, raw_y = max(1, int(raw.get('width', 0))) // 2, max(1, int(raw.get('height', 0))) // 2
                if not all(cx <= x + dx <= cx + sizes[c]['width'] for dx in (w // 2, raw_x)):
                    return False
                if not all(cy <= y + dy <= cy + sizes[c]['height'] for dy in (h // 2, raw_y)):
                    return False
            for c in memberships:
                if n in memberships[c]:
                    continue
                cx, cy = result[c]
                if overlap((x, y, w, h), (cx, cy, sizes[c]['width'], sizes[c]['height'])):
                    return False
            for m in content:
                if n == m:
                    continue
                px, py = changed.get(m, result[m])
                if overlap((x, y, w, h), (px, py, sizes[m]['width'], sizes[m]['height'])):
                    return False
        return True
    def trial(changed: dict[str, tuple[int, int]], apply: bool = False) -> tuple[int, float] | None:
        nonlocal cross_total, length_total
        if not legal(changed):
            return None
        affected = set().union(*(incident[n] for n in changed))
        relevant_pairs = set().union(*(pair_indices[i] for i in affected)) if affected else set()
        new_lines = {i: line(edges[i], changed) for i in affected}
        new_crosses = {k: int(crosses(new_lines.get(pairs[k][0], lines[pairs[k][0]]),
                                       new_lines.get(pairs[k][1], lines[pairs[k][1]]))) for k in relevant_pairs}
        new_count = cross_total + sum(new_crosses[k] - pair_crosses[k] for k in relevant_pairs)
        new_lengths = {i: length(segment) for i, segment in new_lines.items()}
        new_length = length_total + sum(new_lengths[i] - line_lengths[i] for i in affected)
        score = new_count, round(new_length, 6)
        if apply:
            result.update(changed)
            for i, segment in new_lines.items():
                lines[i] = segment
                line_lengths[i] = new_lengths[i]
            for k, crossing in new_crosses.items():
                pair_crosses[k] = crossing
            cross_total, length_total = new_count, new_length
        return score
    for _ in range(2):
        improved = False
        # Adjacent transpositions permit a crowded column to change order;
        # coordinate-only motion cannot cross an occupied neighbour.
        columns: dict[int, list[str]] = {}
        for n in ordered:
            columns.setdefault(result[n][0], []).append(n)
        for x, column in sorted(columns.items()):
            column.sort(key=lambda n: (result[n][1], n))
            for index in range(len(column) - 1):
                a, b = column[index:index + 2]
                top = result[a][1]
                old_gap = result[b][1] - top - sizes[a]['height']
                gap = max(0, min(spacing_y, old_gap))
                candidate = {b: (x, top), a: (x, top + sizes[b]['height'] + gap)}
                score = trial(candidate)
                if score is not None and score < (cross_total, round(length_total, 6)):
                    trial(candidate, apply=True)
                    column[index:index + 2] = [b, a]
                    improved = True
        for n in ordered:
            if not incident[n]:
                continue
            x, y = result[n]
            desired = set()
            for i in sorted(incident[n]):
                edge = edges[i]
                if edge['source'] == n:
                    target = edge['target']
                    wanted = result[target][1] + edge.get('targetY', 0) - edge.get('sourceY', 0)
                else:
                    source = edge['source']
                    wanted = result[source][1] + edge.get('sourceY', 0) - edge.get('targetY', 0)
                desired.add(round(wanted))
            nearby = [m for m in ordered if m != n and result[m][0] < x + sizes[n]['width']
                      and x < result[m][0] + sizes[m]['width']]
            for m in nearby:
                desired.add(result[m][1] + sizes[m]['height'] + spacing_y)
                desired.add(result[m][1] - sizes[n]['height'] - spacing_y)
            # Bound search work on high-degree hubs; use closest deterministic
            # candidates and always consider the weighted-median ordinate.
            candidates = sorted(desired, key=lambda yy: (abs(yy - y), yy))[:12]
            if desired:
                candidates.append(sorted(desired)[len(desired) // 2])
            best_score = cross_total, round(length_total, 6)
            best = None
            for yy in sorted(set(candidates)):
                if yy == y:
                    continue
                candidate = {n: (x, yy)}
                score = trial(candidate)
                if score is not None and score < best_score:
                    best_score, best = score, candidate
            if best:
                trial(best, apply=True)
                improved = True
        if not improved:
            break
    return result

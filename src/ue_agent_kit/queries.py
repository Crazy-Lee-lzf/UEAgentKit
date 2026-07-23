from __future__ import annotations

import json
import sqlite3

from .database import get_metadata
from collections.abc import Iterable
from typing import Any


MAX_QUERY_LIMIT = 10000


def normalize_pagination(limit: int, offset: int) -> tuple[int, int]:
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > MAX_QUERY_LIMIT:
        raise ValueError(f"limit must not exceed {MAX_QUERY_LIMIT}")
    if offset < 0:
        raise ValueError("offset must not be negative")
    return limit, offset


def _parse_json(value: str, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def _fts_phrase(query: str) -> str:
    return '"' + query.replace('"', '""') + '"'


def _merge_ranked_rows(
    ranked_rows: Iterable[tuple[float, sqlite3.Row]],
    *,
    key_column: str,
    limit: int,
    offset: int,
) -> list[dict[str, Any]]:
    best: dict[Any, tuple[float, sqlite3.Row]] = {}
    for rank, row in ranked_rows:
        key = row[key_column]
        current = best.get(key)
        if current is None or rank < current[0]:
            best[key] = (rank, row)

    ordered = sorted(
        best.values(),
        key=lambda item: (item[0], str(item[1][key_column]).casefold()),
    )
    return [_row_to_dict(row) for _, row in ordered[offset : offset + limit]]


def search_assets(
    connection: sqlite3.Connection,
    query: str,
    *,
    asset_class: str = "",
    path_prefix: str = "",
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit, offset = normalize_pagination(limit, offset)
    query = query.strip()
    asset_class = asset_class.strip()
    path_prefix = path_prefix.strip()
    class_like = f"%{asset_class}%"
    path_like = path_prefix + "%"
    fetch_limit = limit + offset + 200

    if not query:
        clauses: list[str] = []
        parameters: list[Any] = []
        if asset_class:
            clauses.append("asset_class LIKE ?")
            parameters.append(class_like)
        if path_prefix:
            clauses.append("asset_path LIKE ?")
            parameters.append(path_like)
        where_sql = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            f"""
            SELECT id, asset_path, package_name, asset_name, asset_class, blueprint_type,
                   parent_class, generated_class, revision_value, schema_version,
                   exporter_version, profile, summary_json
            FROM assets
            {where_sql}
            ORDER BY asset_path
            LIMIT ? OFFSET ?
            """,
            [*parameters, limit, offset],
        ).fetchall()
        results = [_row_to_dict(row) for row in rows]
    else:
        ranked: list[tuple[float, sqlite3.Row]] = []
        filter_clauses: list[str] = []
        filter_parameters: list[Any] = []
        if asset_class:
            filter_clauses.append("a.asset_class LIKE ?")
            filter_parameters.append(class_like)
        if path_prefix:
            filter_clauses.append("a.asset_path LIKE ?")
            filter_parameters.append(path_like)
        class_filter_sql = " AND " + " AND ".join(filter_clauses) if filter_clauses else ""
        try:
            fts_rows = connection.execute(
                f"""
                SELECT a.id, a.asset_path, a.package_name, a.asset_name, a.asset_class,
                       a.blueprint_type, a.parent_class, a.generated_class, a.revision_value,
                       a.schema_version, a.exporter_version, a.profile, a.summary_json,
                       bm25(assets_fts) AS search_rank
                FROM assets_fts
                JOIN assets AS a ON a.id = assets_fts.rowid
                WHERE assets_fts MATCH ?{class_filter_sql}
                ORDER BY search_rank, a.asset_path
                LIMIT ?
                """,
                [_fts_phrase(query), *filter_parameters, fetch_limit],
            ).fetchall()
            ranked.extend((float(row["search_rank"]), row) for row in fts_rows)
        except sqlite3.OperationalError:
            pass

        like_value = f"%{query}%"
        like_rows = connection.execute(
            f"""
            SELECT id, asset_path, package_name, asset_name, asset_class, blueprint_type,
                   parent_class, generated_class, revision_value, schema_version,
                   exporter_version, profile, summary_json
            FROM assets AS a
            WHERE (asset_path LIKE ? OR package_name LIKE ? OR asset_name LIKE ?
               OR asset_class LIKE ? OR parent_class LIKE ? OR generated_class LIKE ?)
               {class_filter_sql}
            ORDER BY
                CASE
                    WHEN asset_path = ? OR asset_name = ? THEN 0
                    WHEN asset_path LIKE ? OR asset_name LIKE ? THEN 1
                    ELSE 2
                END,
                asset_path
            LIMIT ?
            """,
            [
                like_value,
                like_value,
                like_value,
                like_value,
                like_value,
                like_value,
                *filter_parameters,
                query,
                query,
                query + "%",
                query + "%",
                fetch_limit,
            ],
        ).fetchall()
        for row in like_rows:
            if row["asset_path"] == query or row["asset_name"] == query:
                rank = -100.0
            elif str(row["asset_path"]).startswith(query) or str(row["asset_name"]).startswith(query):
                rank = -50.0
            else:
                rank = 50.0
            ranked.append((rank, row))

        results = _merge_ranked_rows(ranked, key_column="id", limit=limit, offset=offset)

    for result in results:
        result.pop("search_rank", None)
        result.pop("id", None)
        result["summary"] = _parse_json(str(result.pop("summary_json", "")), {})
    return results

def search_symbols(
    connection: sqlite3.Connection,
    query: str,
    *,
    kind: str = "",
    asset_path: str = "",
    path_prefix: str = "",
    limit: int = 50,
    offset: int = 0,
    include_details: bool = False,
) -> list[dict[str, Any]]:
    limit, offset = normalize_pagination(limit, offset)
    query = query.strip()
    path_prefix = path_prefix.strip()
    fetch_limit = limit + offset + 200
    filters: list[str] = []
    parameters: list[Any] = []
    if kind:
        filters.append("s.kind = ?")
        parameters.append(kind)
    if asset_path:
        filters.append("a.asset_path = ?")
        parameters.append(asset_path)
    if path_prefix:
        filters.append("a.asset_path LIKE ?")
        parameters.append(path_prefix + "%")
    filter_sql = " AND " + " AND ".join(filters) if filters else ""

    ranked: list[tuple[float, sqlite3.Row]] = []
    if query:
        try:
            fts_rows = connection.execute(
                f"""
                SELECT s.id, s.stable_id, s.kind, s.name, a.asset_path,
                       s.symbol_asset_path, s.guid, s.owner_symbol_id, s.parent_symbol_id,
                       s.class_path, s.graph_guid, s.details_json,
                       bm25(symbols_fts) AS search_rank
                FROM symbols_fts
                JOIN symbols AS s ON s.id = symbols_fts.rowid
                JOIN assets AS a ON a.id = s.asset_id
                WHERE symbols_fts MATCH ?{filter_sql}
                ORDER BY search_rank, a.asset_path, s.kind, s.name
                LIMIT ?
                """,
                [_fts_phrase(query), *parameters, fetch_limit],
            ).fetchall()
            ranked.extend((float(row["search_rank"]), row) for row in fts_rows)
        except sqlite3.OperationalError:
            pass

        like_value = f"%{query}%"
        like_rows = connection.execute(
            f"""
            SELECT s.id, s.stable_id, s.kind, s.name, a.asset_path,
                   s.symbol_asset_path, s.guid, s.owner_symbol_id, s.parent_symbol_id,
                   s.class_path, s.graph_guid, s.details_json
            FROM symbols AS s
            JOIN assets AS a ON a.id = s.asset_id
            WHERE (s.stable_id LIKE ? OR s.name LIKE ? OR s.symbol_asset_path LIKE ?
                   OR s.class_path LIKE ?){filter_sql}
            ORDER BY
                CASE
                    WHEN s.stable_id = ? OR s.name = ? THEN 0
                    WHEN s.name LIKE ? THEN 1
                    ELSE 2
                END,
                a.asset_path, s.kind, s.name
            LIMIT ?
            """,
            [
                like_value,
                like_value,
                like_value,
                like_value,
                *parameters,
                query,
                query,
                query + "%",
                fetch_limit,
            ],
        ).fetchall()
        for row in like_rows:
            if row["stable_id"] == query or row["name"] == query:
                rank = -100.0
            elif str(row["name"]).startswith(query):
                rank = -50.0
            else:
                rank = 50.0
            ranked.append((rank, row))

        results = _merge_ranked_rows(ranked, key_column="id", limit=limit, offset=offset)
    else:
        rows = connection.execute(
            f"""
            SELECT s.id, s.stable_id, s.kind, s.name, a.asset_path,
                   s.symbol_asset_path, s.guid, s.owner_symbol_id, s.parent_symbol_id,
                   s.class_path, s.graph_guid, s.details_json
            FROM symbols AS s
            JOIN assets AS a ON a.id = s.asset_id
            WHERE 1 = 1{filter_sql}
            ORDER BY a.asset_path, s.kind, s.name
            LIMIT ? OFFSET ?
            """,
            [*parameters, limit, offset],
        ).fetchall()
        results = [_row_to_dict(row) for row in rows]

    for result in results:
        result.pop("search_rank", None)
        result.pop("id", None)
        details_json = str(result.pop("details_json", ""))
        if include_details:
            result["details"] = _parse_json(details_json, {})
    return results


def _reference_filter_parts(
    *,
    query: str,
    kind: str,
    project_only: bool,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    parameters: list[Any] = []
    if query:
        like_value = f"%{query}%"
        clauses.append(
            "(r.stable_id LIKE ? OR r.target_name LIKE ? OR r.target_path LIKE ? "
            "OR r.node_title LIKE ? OR r.node_class LIKE ?)"
        )
        parameters.extend([like_value] * 5)
    if kind:
        clauses.append("r.kind = ?")
        parameters.append(kind)
    if project_only:
        clauses.append("EXISTS (SELECT 1 FROM assets AS target_asset WHERE target_asset.asset_path = r.target_asset_path)")
    return clauses, parameters


def _decorate_reference_rows(
    rows: Iterable[sqlite3.Row],
    *,
    include_details: bool,
    depth: int,
    direction: str,
) -> list[dict[str, Any]]:
    results = [_row_to_dict(row) for row in rows]
    for result in results:
        result.pop("id", None)
        details_json = str(result.pop("details_json", ""))
        result["depth"] = depth
        result["direction"] = direction
        if include_details:
            result["details"] = _parse_json(details_json, {})
    return results


def _find_direct_references(
    connection: sqlite3.Connection,
    *,
    query: str,
    kind: str,
    asset_path: str,
    source_symbol_id: str,
    target_symbol_id: str,
    target_asset_path: str,
    direction: str,
    project_only: bool,
    limit: int,
    offset: int,
    include_details: bool,
) -> list[dict[str, Any]]:
    clauses, parameters = _reference_filter_parts(query=query, kind=kind, project_only=project_only)
    if asset_path:
        if direction == "outgoing":
            clauses.append("a.asset_path = ?")
            parameters.append(asset_path)
        elif direction == "incoming":
            clauses.append("r.target_asset_path = ?")
            parameters.append(asset_path)
        else:
            clauses.append("(a.asset_path = ? OR r.target_asset_path = ?)")
            parameters.extend([asset_path, asset_path])
    if source_symbol_id:
        clauses.append("r.source_symbol_id = ?")
        parameters.append(source_symbol_id)
    if target_symbol_id:
        clauses.append("r.target_symbol_id = ?")
        parameters.append(target_symbol_id)
    if target_asset_path:
        clauses.append("r.target_asset_path = ?")
        parameters.append(target_asset_path)

    where_sql = " AND ".join(clauses) if clauses else "1 = 1"
    rows = connection.execute(
        f"""
        SELECT r.id, r.stable_id, r.kind, a.asset_path,
               r.source_symbol_id, r.target_symbol_id, r.target_kind, r.target_name,
               r.target_asset_path, r.target_path, r.graph_guid, r.graph_name,
               r.node_guid, r.node_class, r.node_title, r.details_json
        FROM references_table AS r
        JOIN assets AS a ON a.id = r.asset_id
        WHERE {where_sql}
        ORDER BY a.asset_path, r.graph_name, r.node_title, r.kind, r.target_name, r.stable_id
        LIMIT ? OFFSET ?
        """,
        [*parameters, limit, offset],
    ).fetchall()
    results = _decorate_reference_rows(
        rows,
        include_details=include_details,
        depth=1,
        direction=direction,
    )
    if direction == "both" and asset_path:
        for result in results:
            result["direction"] = "outgoing" if result["asset_path"] == asset_path else "incoming"
    return results


def _find_traversed_references(
    connection: sqlite3.Connection,
    *,
    query: str,
    kind: str,
    asset_path: str,
    direction: str,
    depth: int,
    project_only: bool,
    limit: int,
    offset: int,
    include_details: bool,
) -> list[dict[str, Any]]:
    project_paths = {
        str(row["asset_path"])
        for row in connection.execute("SELECT asset_path FROM assets ORDER BY asset_path")
    }
    visited_assets = {asset_path}
    frontier = {asset_path}
    collected: list[dict[str, Any]] = []
    seen_edges: set[tuple[str, str, int]] = set()
    base_clauses, base_parameters = _reference_filter_parts(
        query=query,
        kind=kind,
        project_only=project_only,
    )

    for current_depth in range(1, depth + 1):
        if not frontier:
            break
        ordered_frontier = sorted(frontier)
        placeholders = ",".join("?" for _ in ordered_frontier)
        if direction == "outgoing":
            anchor_clause = f"a.asset_path IN ({placeholders})"
            anchor_parameters = ordered_frontier
        elif direction == "incoming":
            anchor_clause = f"r.target_asset_path IN ({placeholders})"
            anchor_parameters = ordered_frontier
        else:
            anchor_clause = (
                f"(a.asset_path IN ({placeholders}) OR r.target_asset_path IN ({placeholders}))"
            )
            anchor_parameters = [*ordered_frontier, *ordered_frontier]
        where_sql = " AND ".join([anchor_clause, *base_clauses])
        rows = connection.execute(
            f"""
            SELECT r.id, r.stable_id, r.kind, a.asset_path,
                   r.source_symbol_id, r.target_symbol_id, r.target_kind, r.target_name,
                   r.target_asset_path, r.target_path, r.graph_guid, r.graph_name,
                   r.node_guid, r.node_class, r.node_title, r.details_json
            FROM references_table AS r
            JOIN assets AS a ON a.id = r.asset_id
            WHERE {where_sql}
            ORDER BY a.asset_path, r.graph_name, r.node_title, r.kind, r.target_name, r.stable_id
            """,
            [*anchor_parameters, *base_parameters],
        ).fetchall()

        next_frontier: set[str] = set()
        for row in rows:
            source_path = str(row["asset_path"])
            target_path = str(row["target_asset_path"])
            edge_directions: list[str] = []
            if direction in ("outgoing", "both") and source_path in frontier:
                edge_directions.append("outgoing")
            if direction in ("incoming", "both") and target_path in frontier:
                edge_directions.append("incoming")
            for edge_direction in edge_directions:
                edge_key = (str(row["stable_id"]), edge_direction, current_depth)
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                result = _decorate_reference_rows(
                    [row],
                    include_details=include_details,
                    depth=current_depth,
                    direction=edge_direction,
                )[0]
                collected.append(result)
                next_path = target_path if edge_direction == "outgoing" else source_path
                if next_path in project_paths and next_path not in visited_assets:
                    next_frontier.add(next_path)
        visited_assets.update(next_frontier)
        frontier = next_frontier

    collected.sort(
        key=lambda item: (
            int(item["depth"]),
            str(item["direction"]),
            str(item["asset_path"]).casefold(),
            str(item["graph_name"]).casefold(),
            str(item["node_title"]).casefold(),
            str(item["kind"]).casefold(),
            str(item["target_name"]).casefold(),
            str(item["stable_id"]).casefold(),
        )
    )
    return collected[offset : offset + limit]


def find_references(
    connection: sqlite3.Connection,
    *,
    query: str = "",
    kind: str = "",
    asset_path: str = "",
    source_symbol_id: str = "",
    target_symbol_id: str = "",
    target_asset_path: str = "",
    direction: str = "outgoing",
    depth: int = 1,
    project_only: bool = False,
    limit: int = 100,
    offset: int = 0,
    include_details: bool = False,
) -> list[dict[str, Any]]:
    limit, offset = normalize_pagination(limit, offset)
    query = query.strip()
    kind = kind.strip()
    asset_path = asset_path.strip()
    source_symbol_id = source_symbol_id.strip()
    target_symbol_id = target_symbol_id.strip()
    target_asset_path = target_asset_path.strip()
    if direction not in {"outgoing", "incoming", "both"}:
        raise ValueError("direction must be outgoing, incoming, or both")
    if depth < 1 or depth > 3:
        raise ValueError("depth must be between 1 and 3")
    if direction in {"incoming", "both"} and not asset_path:
        raise ValueError("asset_path is required for incoming or both reference direction")
    if depth > 1:
        if not asset_path:
            raise ValueError("asset_path is required when depth is greater than 1")
        if any((source_symbol_id, target_symbol_id, target_asset_path)):
            raise ValueError("symbol and target endpoint filters are available only when depth is 1")
        return _find_traversed_references(
            connection,
            query=query,
            kind=kind,
            asset_path=asset_path,
            direction=direction,
            depth=depth,
            project_only=project_only,
            limit=limit,
            offset=offset,
            include_details=include_details,
        )
    return _find_direct_references(
        connection,
        query=query,
        kind=kind,
        asset_path=asset_path,
        source_symbol_id=source_symbol_id,
        target_symbol_id=target_symbol_id,
        target_asset_path=target_asset_path,
        direction=direction,
        project_only=project_only,
        limit=limit,
        offset=offset,
        include_details=include_details,
    )

def get_asset(
    connection: sqlite3.Connection,
    asset_path: str,
    *,
    symbol_limit: int = 200,
    reference_limit: int = 500,
    graph_limit: int | None = None,
    node_limit: int = 200,
    symbol_offset: int = 0,
    reference_offset: int = 0,
    graph_offset: int = 0,
    node_offset: int = 0,
    sections: Iterable[str] | None = None,
    graph_guid: str = "",
    node_guid: str = "",
    include_details: bool = False,
) -> dict[str, Any] | None:
    normalize_pagination(symbol_limit, symbol_offset)
    normalize_pagination(reference_limit, reference_offset)
    if graph_limit is not None:
        normalize_pagination(graph_limit, graph_offset)
    elif graph_offset < 0:
        raise ValueError("offset must not be negative")
    normalize_pagination(node_limit, node_offset)

    requested_sections = set(sections or {"identity", "summary", "metadata", "symbols", "references", "graphs", "nodes"})
    row = connection.execute("SELECT * FROM assets WHERE asset_path = ?", (asset_path,)).fetchone()
    if row is None:
        return None

    asset_id = int(row["id"])
    result = _row_to_dict(row)
    result.pop("id", None)
    summary_json = str(result.pop("summary_json", ""))
    if "summary" in requested_sections:
        result["summary"] = _parse_json(summary_json, {})

    if "symbols" in requested_sections:
        result["symbols"] = search_symbols(
            connection,
            "",
            asset_path=asset_path,
            limit=symbol_limit,
            offset=symbol_offset,
            include_details=include_details,
        )
    if "references" in requested_sections:
        result["references"] = find_references(
            connection,
            asset_path=asset_path,
            direction="outgoing",
            depth=1,
            limit=reference_limit,
            offset=reference_offset,
            include_details=include_details,
        )

    if "graphs" in requested_sections:
        graph_clauses = ["asset_id = ?"]
        graph_parameters: list[Any] = [asset_id]
        if graph_guid:
            graph_clauses.append("guid = ?")
            graph_parameters.append(graph_guid)
        graph_sql = f"""
            SELECT id, guid, name, kind, schema_path, node_count, details_json
            FROM graphs
            WHERE {' AND '.join(graph_clauses)}
            ORDER BY kind, name, guid
        """
        if graph_limit is None:
            graph_sql += "\nLIMIT -1 OFFSET ?"
            graph_parameters.append(graph_offset)
        else:
            graph_sql += "\nLIMIT ? OFFSET ?"
            graph_parameters.extend([graph_limit, graph_offset])
        graph_rows = connection.execute(graph_sql, graph_parameters).fetchall()
        result["graphs"] = []
        for graph_row in graph_rows:
            graph = _row_to_dict(graph_row)
            graph.pop("id", None)
            details_json = str(graph.pop("details_json", ""))
            if include_details:
                graph["details"] = _parse_json(details_json, {})
            result["graphs"].append(graph)

    if "nodes" in requested_sections:
        node_clauses = ["n.asset_id = ?"]
        node_parameters: list[Any] = [asset_id]
        if graph_guid:
            node_clauses.append("n.graph_guid = ?")
            node_parameters.append(graph_guid)
        if node_guid:
            node_clauses.append("n.guid = ?")
            node_parameters.append(node_guid)
        node_rows = connection.execute(
            f"""
            SELECT n.id, n.graph_guid, n.guid, n.object_name, n.node_class, n.title, n.comment, n.details_json
            FROM nodes AS n
            WHERE {' AND '.join(node_clauses)}
            ORDER BY n.graph_guid, n.title, n.guid
            LIMIT ? OFFSET ?
            """,
            [*node_parameters, node_limit, node_offset],
        ).fetchall()
        result["nodes"] = []
        for node_row in node_rows:
            node = _row_to_dict(node_row)
            node.pop("id", None)
            details_json = str(node.pop("details_json", ""))
            if include_details:
                node["details"] = _parse_json(details_json, {})
            result["nodes"].append(node)

    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM symbols WHERE asset_id = ?) AS symbols,
            (SELECT COUNT(*) FROM references_table WHERE asset_id = ?) AS reference_count,
            (SELECT COUNT(*) FROM graphs WHERE asset_id = ?) AS graphs,
            (SELECT COUNT(*) FROM nodes WHERE asset_id = ?) AS nodes
        """,
        (asset_id, asset_id, asset_id, asset_id),
    ).fetchone()
    result["indexed_counts"] = _row_to_dict(counts)
    result["indexed_counts"]["references"] = result["indexed_counts"].pop("reference_count")
    return result

def get_stats(connection: sqlite3.Connection) -> dict[str, Any]:
    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM assets) AS assets,
            (SELECT COUNT(*) FROM symbols) AS symbols,
            (SELECT COUNT(*) FROM references_table) AS reference_count,
            (SELECT COUNT(*) FROM graphs) AS graphs,
            (SELECT COUNT(*) FROM nodes) AS nodes
        """
    ).fetchone()
    profiles = {
        str(row["profile"]): int(row["count"])
        for row in connection.execute(
            "SELECT profile, COUNT(*) AS count FROM assets GROUP BY profile ORDER BY profile"
        )
    }
    asset_classes = {
        str(row["asset_class"]): int(row["count"])
        for row in connection.execute(
            "SELECT asset_class, COUNT(*) AS count FROM assets GROUP BY asset_class ORDER BY asset_class"
        )
    }
    symbol_kinds = {
        str(row["kind"]): int(row["count"])
        for row in connection.execute(
            "SELECT kind, COUNT(*) AS count FROM symbols GROUP BY kind ORDER BY kind"
        )
    }
    reference_kinds = {
        str(row["kind"]): int(row["count"])
        for row in connection.execute(
            "SELECT kind, COUNT(*) AS count FROM references_table GROUP BY kind ORDER BY kind"
        )
    }
    count_values = _row_to_dict(counts)
    count_values["references"] = count_values.pop("reference_count")
    return {
        "projectKey": get_metadata(connection, "project_key", ""),
        "counts": count_values,
        "profiles": profiles,
        "assetClasses": asset_classes,
        "symbolKinds": symbol_kinds,
        "referenceKinds": reference_kinds,
    }

from __future__ import annotations

import json
import sqlite3

from .database import get_metadata
from collections.abc import Iterable
from typing import Any


MAX_QUERY_LIMIT = 1000


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
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    limit, offset = normalize_pagination(limit, offset)
    query = query.strip()
    asset_class = asset_class.strip()
    class_like = f"%{asset_class}%"
    fetch_limit = min(MAX_QUERY_LIMIT, limit + offset + 200)

    if not query:
        where_sql = "WHERE asset_class LIKE ?" if asset_class else ""
        parameters: list[Any] = [class_like] if asset_class else []
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
        class_filter_sql = " AND a.asset_class LIKE ?" if asset_class else ""
        class_parameters: list[Any] = [class_like] if asset_class else []
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
                [_fts_phrase(query), *class_parameters, fetch_limit],
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
                *class_parameters,
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
    limit: int = 50,
    offset: int = 0,
    include_details: bool = False,
) -> list[dict[str, Any]]:
    limit, offset = normalize_pagination(limit, offset)
    query = query.strip()
    fetch_limit = min(MAX_QUERY_LIMIT, limit + offset + 200)
    filters: list[str] = []
    parameters: list[Any] = []
    if kind:
        filters.append("s.kind = ?")
        parameters.append(kind)
    if asset_path:
        filters.append("a.asset_path = ?")
        parameters.append(asset_path)
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


def find_references(
    connection: sqlite3.Connection,
    *,
    query: str = "",
    kind: str = "",
    asset_path: str = "",
    source_symbol_id: str = "",
    target_symbol_id: str = "",
    target_asset_path: str = "",
    limit: int = 100,
    offset: int = 0,
    include_details: bool = False,
) -> list[dict[str, Any]]:
    limit, offset = normalize_pagination(limit, offset)
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
    if asset_path:
        clauses.append("a.asset_path = ?")
        parameters.append(asset_path)
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
        ORDER BY a.asset_path, r.graph_name, r.node_title, r.kind, r.target_name
        LIMIT ? OFFSET ?
        """,
        [*parameters, limit, offset],
    ).fetchall()

    results = [_row_to_dict(row) for row in rows]
    for result in results:
        result.pop("id", None)
        details_json = str(result.pop("details_json", ""))
        if include_details:
            result["details"] = _parse_json(details_json, {})
    return results


def get_asset(
    connection: sqlite3.Connection,
    asset_path: str,
    *,
    symbol_limit: int = 200,
    reference_limit: int = 500,
    graph_limit: int | None = None,
    node_limit: int = 200,
    include_details: bool = False,
) -> dict[str, Any] | None:
    normalize_pagination(symbol_limit, 0)
    normalize_pagination(reference_limit, 0)
    if graph_limit is not None:
        normalize_pagination(graph_limit, 0)
    normalize_pagination(node_limit, 0)

    row = connection.execute("SELECT * FROM assets WHERE asset_path = ?", (asset_path,)).fetchone()
    if row is None:
        return None

    asset_id = int(row["id"])
    result = _row_to_dict(row)
    result.pop("id", None)
    result["summary"] = _parse_json(str(result.pop("summary_json", "")), {})
    result["symbols"] = search_symbols(
        connection,
        "",
        asset_path=asset_path,
        limit=symbol_limit,
        include_details=include_details,
    )
    result["references"] = find_references(
        connection,
        asset_path=asset_path,
        limit=reference_limit,
        include_details=include_details,
    )

    graph_sql = """
        SELECT id, guid, name, kind, schema_path, node_count, details_json
        FROM graphs
        WHERE asset_id = ?
        ORDER BY kind, name
    """
    graph_parameters: list[Any] = [asset_id]
    if graph_limit is not None:
        graph_sql += "\nLIMIT ?"
        graph_parameters.append(graph_limit)
    graph_rows = connection.execute(graph_sql, graph_parameters).fetchall()
    result["graphs"] = []
    for graph_row in graph_rows:
        graph = _row_to_dict(graph_row)
        graph.pop("id", None)
        details_json = str(graph.pop("details_json", ""))
        if include_details:
            graph["details"] = _parse_json(details_json, {})
        result["graphs"].append(graph)

    node_rows = connection.execute(
        """
        SELECT n.id, n.graph_guid, n.guid, n.object_name, n.node_class, n.title, n.comment, n.details_json
        FROM nodes AS n
        WHERE n.asset_id = ?
        ORDER BY n.graph_guid, n.title, n.guid
        LIMIT ?
        """,
        (asset_id, node_limit),
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

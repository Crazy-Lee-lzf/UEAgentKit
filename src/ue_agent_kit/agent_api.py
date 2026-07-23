from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal

from .database import assert_fts5_available, get_metadata, get_schema_version, open_database
from .queries import find_references, get_asset, get_stats, search_assets, search_symbols
from .schema import CURRENT_SCHEMA_VERSION


AGENT_API_SCHEMA_VERSION = "1.0"
MAX_MCP_SEARCH_LIMIT = 100
MAX_MCP_SYMBOL_LIMIT = 500
MAX_MCP_REFERENCE_LIMIT = 1000
MAX_MCP_GRAPH_LIMIT = 500
MAX_MCP_NODE_LIMIT = 500
SearchScope = Literal["assets", "symbols"]


class IndexSnapshotError(RuntimeError):
    """Raised when a database is not a quiescent immutable snapshot."""


def _bounded_limit(value: int, *, maximum: int, name: str) -> int:
    if value < 1:
        raise ValueError(f"{name} must be at least 1")
    if value > maximum:
        raise ValueError(f"{name} must not exceed {maximum}")
    return value


def _bounded_offset(value: int) -> int:
    if value < 0:
        raise ValueError("offset must not be negative")
    return value


class IndexQueryService:
    """Stable read-only query contract shared by MCP and future agent adapters."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()

    def _assert_quiescent_snapshot(self) -> None:
        sidecars = [
            Path(str(self.database_path) + suffix)
            for suffix in ("-wal", "-shm", "-journal")
        ]
        if any(path.exists() for path in sidecars):
            raise IndexSnapshotError(
                "The configured index has an active SQLite sidecar. "
                "Finish indexing and close all writers before starting the MCP server."
            )

    @contextmanager
    def _open(self) -> Iterator[sqlite3.Connection]:
        self._assert_quiescent_snapshot()
        with open_database(
            self.database_path,
            readonly=True,
            migrate=False,
            immutable=True,
        ) as connection:
            version = get_schema_version(connection)
            if version != CURRENT_SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema is {version}; UE Agent Kit requires schema {CURRENT_SCHEMA_VERSION}."
                )
            assert_fts5_available(connection)
            try:
                yield connection
            finally:
                self._assert_quiescent_snapshot()

    @staticmethod
    def _base_response(connection: sqlite3.Connection, tool: str) -> dict[str, Any]:
        return {
            "schemaVersion": AGENT_API_SCHEMA_VERSION,
            "tool": tool,
            "ok": True,
            "projectKey": get_metadata(connection, "project_key", ""),
            "databaseSchemaVersion": get_schema_version(connection),
            "readOnly": True,
        }

    def check(self) -> dict[str, Any]:
        with self._open() as connection:
            response = self._base_response(connection, "ue_index_status")
            response["indexMetadata"] = {
                "lastIndexedAtUtc": get_metadata(connection, "last_indexed_at_utc", ""),
                "manifestSchemaVersion": get_metadata(connection, "last_manifest_schema", ""),
                "exporterVersion": get_metadata(connection, "last_exporter_version", ""),
                "profile": get_metadata(connection, "last_profile", ""),
                "immutable": True,
                "quiescent": True,
            }
            response["stats"] = get_stats(connection)
            return response

    def search(
        self,
        query: str = "",
        *,
        scope: SearchScope = "assets",
        asset_class: str = "",
        kind: str = "",
        asset_path: str = "",
        limit: int = 20,
        offset: int = 0,
        include_details: bool = False,
    ) -> dict[str, Any]:
        limit = _bounded_limit(limit, maximum=MAX_MCP_SEARCH_LIMIT, name="limit")
        offset = _bounded_offset(offset)
        if scope not in ("assets", "symbols"):
            raise ValueError("scope must be assets or symbols")
        if scope == "assets" and (kind or asset_path or include_details):
            raise ValueError("kind, asset_path, and include_details are available only for symbol search")
        if scope == "symbols" and asset_class:
            raise ValueError("asset_class is available only for asset search")

        with self._open() as connection:
            if scope == "assets":
                results = search_assets(
                    connection,
                    query,
                    asset_class=asset_class,
                    limit=limit,
                    offset=offset,
                )
            else:
                results = search_symbols(
                    connection,
                    query,
                    kind=kind,
                    asset_path=asset_path,
                    limit=limit,
                    offset=offset,
                    include_details=include_details,
                )
            response = self._base_response(connection, "ue_search")
            response.update(
                {
                    "scope": scope,
                    "query": query,
                    "filters": {
                        "assetClass": asset_class,
                        "kind": kind,
                        "assetPath": asset_path,
                        "includeDetails": include_details,
                    },
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "resultCount": len(results),
                        "mayHaveMore": len(results) == limit,
                    },
                    "results": results,
                }
            )
            return response

    def get_asset(
        self,
        asset_path: str,
        *,
        symbol_limit: int = 100,
        reference_limit: int = 200,
        graph_limit: int = 100,
        node_limit: int = 100,
        include_details: bool = False,
    ) -> dict[str, Any]:
        asset_path = asset_path.strip()
        if not asset_path:
            raise ValueError("asset_path is required")
        symbol_limit = _bounded_limit(symbol_limit, maximum=MAX_MCP_SYMBOL_LIMIT, name="symbol_limit")
        reference_limit = _bounded_limit(
            reference_limit,
            maximum=MAX_MCP_REFERENCE_LIMIT,
            name="reference_limit",
        )
        graph_limit = _bounded_limit(graph_limit, maximum=MAX_MCP_GRAPH_LIMIT, name="graph_limit")
        node_limit = _bounded_limit(node_limit, maximum=MAX_MCP_NODE_LIMIT, name="node_limit")

        with self._open() as connection:
            asset = get_asset(
                connection,
                asset_path,
                symbol_limit=symbol_limit,
                reference_limit=reference_limit,
                graph_limit=graph_limit,
                node_limit=node_limit,
                include_details=include_details,
            )
            response = self._base_response(connection, "ue_get_asset")
            response.update(
                {
                    "assetPath": asset_path,
                    "found": asset is not None,
                    "limits": {
                        "symbols": symbol_limit,
                        "references": reference_limit,
                        "graphs": graph_limit,
                        "nodes": node_limit,
                        "includeDetails": include_details,
                    },
                }
            )
            if asset is not None:
                response["asset"] = asset
            return response

    def find_references(
        self,
        *,
        query: str = "",
        kind: str = "",
        asset_path: str = "",
        source_symbol_id: str = "",
        target_symbol_id: str = "",
        target_asset_path: str = "",
        limit: int = 50,
        offset: int = 0,
        include_details: bool = False,
    ) -> dict[str, Any]:
        limit = _bounded_limit(limit, maximum=MAX_MCP_SEARCH_LIMIT, name="limit")
        offset = _bounded_offset(offset)
        if not any((query, kind, asset_path, source_symbol_id, target_symbol_id, target_asset_path)):
            raise ValueError("at least one reference filter is required")

        with self._open() as connection:
            results = find_references(
                connection,
                query=query,
                kind=kind,
                asset_path=asset_path,
                source_symbol_id=source_symbol_id,
                target_symbol_id=target_symbol_id,
                target_asset_path=target_asset_path,
                limit=limit,
                offset=offset,
                include_details=include_details,
            )
            response = self._base_response(connection, "ue_find_references")
            response.update(
                {
                    "filters": {
                        "query": query,
                        "kind": kind,
                        "assetPath": asset_path,
                        "sourceSymbolId": source_symbol_id,
                        "targetSymbolId": target_symbol_id,
                        "targetAssetPath": target_asset_path,
                        "includeDetails": include_details,
                    },
                    "pagination": {
                        "limit": limit,
                        "offset": offset,
                        "resultCount": len(results),
                        "mayHaveMore": len(results) == limit,
                    },
                    "results": results,
                }
            )
            return response

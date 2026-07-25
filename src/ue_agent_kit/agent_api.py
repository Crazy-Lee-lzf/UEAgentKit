from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Literal, Sequence

from .database import assert_fts5_available, get_metadata, get_schema_version, open_database
from .queries import find_references, get_asset as query_get_asset, get_stats, search_assets, search_symbols
from .query_protocol import (
    DEFAULT_OUTPUT_TOKEN_BUDGET,
    ContinuationTokenStore,
    estimate_json_tokens,
    fit_sequence_to_budget,
    normalize_output_token_budget,
)
from .schema import CURRENT_SCHEMA_VERSION


AGENT_API_SCHEMA_VERSION = "1.0"
MAX_MCP_SEARCH_LIMIT = 100
MAX_MCP_SYMBOL_LIMIT = 500
MAX_MCP_REFERENCE_LIMIT = 1000
MAX_MCP_GRAPH_LIMIT = 500
MAX_MCP_NODE_LIMIT = 500
SearchScope = Literal["assets", "symbols"]
ReferenceDirection = Literal["outgoing", "incoming", "both"]
ASSET_SECTION_ORDER = ("identity", "summary", "metadata", "symbols", "references", "graphs", "nodes")
PAGED_ASSET_SECTIONS = ("symbols", "references", "graphs", "nodes")
IDENTITY_FIELDS = (
    "asset_path",
    "package_name",
    "asset_name",
    "asset_class",
    "blueprint_type",
    "parent_class",
    "generated_class",
    "skeleton_generated_class",
)
GUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


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


def _clean_text(value: str, *, name: str, maximum: int = 1024) -> str:
    cleaned = value.strip()
    if len(cleaned) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters")
    if any(ord(character) < 32 for character in cleaned):
        raise ValueError(f"{name} must not contain control characters")
    return cleaned


def _asset_path(value: str, *, name: str = "asset_path", required: bool = False) -> str:
    cleaned = _clean_text(value, name=name)
    if required and not cleaned:
        raise ValueError(f"{name} is required")
    if cleaned and not cleaned.startswith("/"):
        raise ValueError(f"{name} must be an Unreal object or package path beginning with /")
    return cleaned


def _stable_id(value: str, *, name: str) -> str:
    return _clean_text(value, name=name, maximum=2048)


def _guid(value: str, *, name: str) -> str:
    cleaned = _clean_text(value, name=name, maximum=36)
    if cleaned and GUID_PATTERN.fullmatch(cleaned) is None:
        raise ValueError(f"{name} must be a canonical GUID")
    return cleaned


def _normalize_sections(sections: Sequence[str] | None) -> tuple[str, ...]:
    if sections is None:
        return ASSET_SECTION_ORDER
    normalized: list[str] = []
    for section in sections:
        cleaned = _clean_text(str(section), name="section", maximum=32).lower()
        if cleaned not in ASSET_SECTION_ORDER:
            raise ValueError(
                "sections may contain only identity, summary, metadata, symbols, references, graphs, or nodes"
            )
        if cleaned not in normalized:
            normalized.append(cleaned)
    if not normalized:
        raise ValueError("sections must contain at least one section")
    return tuple(normalized)


class IndexQueryService:
    """Stable read-only query contract shared by MCP and future agent adapters."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self._continuations = ContinuationTokenStore()

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

    def _snapshot_id(self, connection: sqlite3.Connection) -> str:
        stat = self.database_path.stat()
        payload = {
            "size": stat.st_size,
            "modifiedNs": stat.st_mtime_ns,
            "schema": get_schema_version(connection),
            "projectKey": get_metadata(connection, "project_key", ""),
            "lastIndexedAtUtc": get_metadata(connection, "last_indexed_at_utc", ""),
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def check(self) -> dict[str, Any]:
        with self._open() as connection:
            response = self._base_response(connection, "ue_index_status")
            response["indexMetadata"] = {
                "lastIndexedAtUtc": get_metadata(connection, "last_indexed_at_utc", ""),
                "manifestSchemaVersion": get_metadata(connection, "last_manifest_schema", ""),
                "exporterVersion": get_metadata(connection, "last_exporter_version", ""),
                "profile": get_metadata(connection, "last_profile", ""),
                "snapshotId": self._snapshot_id(connection),
                "immutable": True,
                "quiescent": True,
            }
            response["stats"] = get_stats(connection)
            return response

    def get_revision_records(self) -> list[dict[str, Any]]:
        """Return the immutable per-asset Revision fields needed by freshness checks."""
        with self._open() as connection:
            rows = connection.execute(
                """
                SELECT asset_path, package_name, asset_class, revision_value,
                       file_size, modified_utc, content_sha256, package_dirty,
                       canonical_relpath
                FROM assets
                ORDER BY asset_path
                """
            ).fetchall()
            return [{key: row[key] for key in row.keys()} for row in rows]

    def get_revision_record(self, asset_path: str) -> dict[str, Any] | None:
        """Return immutable Revision metadata for one exact Unreal object path."""
        asset_path = _asset_path(asset_path, required=True)
        with self._open() as connection:
            row = connection.execute(
                """
                SELECT asset_path, package_name, asset_class, revision_value,
                       file_size, modified_utc, content_sha256, package_dirty,
                       canonical_relpath
                FROM assets
                WHERE asset_path = ?
                """,
                (asset_path,),
            ).fetchone()
            return None if row is None else {key: row[key] for key in row.keys()}

    def get_data_table_row_reference_impact(
        self,
        asset_path: str,
        row_name: str,
        *,
        sample_limit: int = 20,
    ) -> dict[str, Any]:
        """Return exact Searchable Name referencers for one DataTable row."""
        asset_path = _asset_path(asset_path, required=True)
        row_name = _clean_text(row_name, name="row_name", maximum=256)
        sample_limit = _bounded_limit(sample_limit, maximum=100, name="sample_limit")
        target_path = f"{asset_path}::{row_name}"
        with self._open() as connection:
            reference_count = int(
                connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM references_table AS r
                    WHERE r.kind = 'depends-searchable-name'
                      AND r.target_asset_path = ?
                      AND r.target_path = ?
                    """,
                    (asset_path, target_path),
                ).fetchone()[0]
            )
            rows = connection.execute(
                """
                SELECT a.asset_path AS source_asset_path,
                       r.stable_id,
                       r.source_symbol_id,
                       r.target_symbol_id,
                       r.target_name,
                       r.target_path,
                       r.graph_guid,
                       r.graph_name,
                       r.node_guid,
                       r.node_class,
                       r.node_title
                FROM references_table AS r
                JOIN assets AS a ON a.id = r.asset_id
                WHERE r.kind = 'depends-searchable-name'
                  AND r.target_asset_path = ?
                  AND r.target_path = ?
                ORDER BY a.asset_path, r.graph_name, r.node_title, r.stable_id
                LIMIT ?
                """,
                (asset_path, target_path, sample_limit),
            ).fetchall()
            return {
                "checked": True,
                "source": "immutable-sqlite-searchable-name",
                "assetPath": asset_path,
                "rowName": row_name,
                "targetPath": target_path,
                "referenceCount": reference_count,
                "sampleLimit": sample_limit,
                "sampleTruncated": reference_count > len(rows),
                "referencers": [{key: row[key] for key in row.keys()} for row in rows],
            }

    def _paged_response(
        self,
        *,
        connection: sqlite3.Connection,
        tool: str,
        base: dict[str, Any],
        raw_results: list[dict[str, Any]],
        limit: int,
        offset: int,
        max_output_tokens: int,
        token_state: dict[str, Any],
        source: str,
    ) -> dict[str, Any]:
        page_results = raw_results[:limit]
        query_has_more = len(raw_results) > limit

        def build_payload(selected: list[dict[str, Any]]) -> dict[str, Any]:
            candidate = dict(base)
            has_more = query_has_more or len(selected) < len(page_results)
            candidate["pagination"] = {
                "limit": limit,
                "offset": offset,
                "resultCount": len(selected),
                "hasMore": has_more,
                "mayHaveMore": has_more,
                "continuationToken": "ct_placeholder________________________" if has_more else "",
                "source": source,
            }
            candidate["results"] = selected
            return candidate

        selected, _ = fit_sequence_to_budget(
            page_results,
            max_output_tokens=max_output_tokens,
            build_payload=build_payload,
            force_one=True,
        )
        has_more = query_has_more or len(selected) < len(page_results)
        continuation_token = ""
        if has_more:
            continuation_token = self._continuations.issue(
                tool=tool,
                snapshot_id=self._snapshot_id(connection),
                state={**token_state, "offset": offset + len(selected)},
            )
        response = dict(base)
        response["pagination"] = {
            "limit": limit,
            "offset": offset,
            "resultCount": len(selected),
            "hasMore": has_more,
            "mayHaveMore": has_more,
            "continuationToken": continuation_token,
            "source": source,
        }
        response["results"] = selected
        estimated_tokens = estimate_json_tokens(response)
        if len(selected) < len(page_results):
            reason = "token-budget"
        elif query_has_more:
            reason = "page-limit"
        elif estimated_tokens > max_output_tokens:
            reason = "single-result-exceeds-token-budget"
        else:
            reason = ""
        response["outputBudget"] = {
            "maxTokens": max_output_tokens,
            "estimatedTokens": estimate_json_tokens(response),
            "truncated": has_more or estimated_tokens > max_output_tokens,
            "truncationReason": reason,
        }
        return response

    def search(
        self,
        query: str = "",
        *,
        scope: SearchScope = "assets",
        asset_class: str = "",
        kind: str = "",
        asset_path: str = "",
        path_prefix: str = "",
        limit: int = 20,
        offset: int = 0,
        include_details: bool = False,
        continuation_token: str = "",
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        with self._open() as connection:
            snapshot_id = self._snapshot_id(connection)
            source = "parameters"
            if continuation_token:
                state = self._continuations.resolve(
                    continuation_token,
                    tool="ue_search",
                    snapshot_id=snapshot_id,
                )
                query = str(state["query"])
                scope = state["scope"]
                asset_class = str(state["assetClass"])
                kind = str(state["kind"])
                asset_path = str(state["assetPath"])
                path_prefix = str(state["pathPrefix"])
                limit = int(state["limit"])
                offset = int(state["offset"])
                include_details = bool(state["includeDetails"])
                max_output_tokens = int(state["maxOutputTokens"])
                source = "continuation-token"
            else:
                query = _clean_text(query, name="query", maximum=2048)
                asset_class = _clean_text(asset_class, name="asset_class", maximum=512)
                kind = _clean_text(kind, name="kind", maximum=256)
                asset_path = _asset_path(asset_path)
                path_prefix = _asset_path(path_prefix, name="path_prefix")
                limit = _bounded_limit(limit, maximum=MAX_MCP_SEARCH_LIMIT, name="limit")
                offset = _bounded_offset(offset)
                max_output_tokens = normalize_output_token_budget(max_output_tokens)
                if scope not in ("assets", "symbols"):
                    raise ValueError("scope must be assets or symbols")
                if scope == "assets" and (kind or asset_path or include_details):
                    raise ValueError("kind, asset_path, and include_details are available only for symbol search")
                if scope == "symbols" and asset_class:
                    raise ValueError("asset_class is available only for asset search")

            if scope == "assets":
                results = search_assets(
                    connection,
                    query,
                    asset_class=asset_class,
                    path_prefix=path_prefix,
                    limit=limit + 1,
                    offset=offset,
                )
            else:
                results = search_symbols(
                    connection,
                    query,
                    kind=kind,
                    asset_path=asset_path,
                    path_prefix=path_prefix,
                    limit=limit + 1,
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
                        "pathPrefix": path_prefix,
                        "includeDetails": include_details,
                    },
                }
            )
            state = {
                "query": query,
                "scope": scope,
                "assetClass": asset_class,
                "kind": kind,
                "assetPath": asset_path,
                "pathPrefix": path_prefix,
                "limit": limit,
                "includeDetails": include_details,
                "maxOutputTokens": max_output_tokens,
            }
            return self._paged_response(
                connection=connection,
                tool="ue_search",
                base=response,
                raw_results=results,
                limit=limit,
                offset=offset,
                max_output_tokens=max_output_tokens,
                token_state=state,
                source=source,
            )

    def get_asset(
        self,
        asset_path: str = "",
        *,
        sections: Sequence[str] | None = None,
        symbol_limit: int = 100,
        reference_limit: int = 200,
        graph_limit: int = 100,
        node_limit: int = 100,
        graph_guid: str = "",
        node_guid: str = "",
        include_details: bool = False,
        continuation_token: str = "",
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        with self._open() as connection:
            snapshot_id = self._snapshot_id(connection)
            offsets = {section: 0 for section in PAGED_ASSET_SECTIONS}
            source = "parameters"
            continuation_section = ""
            if continuation_token:
                state = self._continuations.resolve(
                    continuation_token,
                    tool="ue_get_asset",
                    snapshot_id=snapshot_id,
                )
                asset_path = str(state["assetPath"])
                continuation_section = str(state["section"])
                sections = ("identity", continuation_section)
                symbol_limit = int(state["symbolLimit"])
                reference_limit = int(state["referenceLimit"])
                graph_limit = int(state["graphLimit"])
                node_limit = int(state["nodeLimit"])
                offsets[continuation_section] = int(state["offset"])
                graph_guid = str(state["graphGuid"])
                node_guid = str(state["nodeGuid"])
                include_details = bool(state["includeDetails"])
                max_output_tokens = int(state["maxOutputTokens"])
                source = "continuation-token"
            else:
                asset_path = _asset_path(asset_path, required=True)
                sections = _normalize_sections(sections)
                symbol_limit = _bounded_limit(symbol_limit, maximum=MAX_MCP_SYMBOL_LIMIT, name="symbol_limit")
                reference_limit = _bounded_limit(
                    reference_limit,
                    maximum=MAX_MCP_REFERENCE_LIMIT,
                    name="reference_limit",
                )
                graph_limit = _bounded_limit(graph_limit, maximum=MAX_MCP_GRAPH_LIMIT, name="graph_limit")
                node_limit = _bounded_limit(node_limit, maximum=MAX_MCP_NODE_LIMIT, name="node_limit")
                graph_guid = _guid(graph_guid, name="graph_guid")
                node_guid = _guid(node_guid, name="node_guid")
                max_output_tokens = normalize_output_token_budget(max_output_tokens)
            assert sections is not None
            normalized_sections = _normalize_sections(sections)
            limits = {
                "symbols": symbol_limit,
                "references": reference_limit,
                "graphs": graph_limit,
                "nodes": node_limit,
            }
            asset = query_get_asset(
                connection,
                asset_path,
                symbol_limit=symbol_limit + 1,
                reference_limit=reference_limit + 1,
                graph_limit=graph_limit + 1,
                node_limit=node_limit + 1,
                symbol_offset=offsets["symbols"],
                reference_offset=offsets["references"],
                graph_offset=offsets["graphs"],
                node_offset=offsets["nodes"],
                sections=normalized_sections,
                graph_guid=graph_guid,
                node_guid=node_guid,
                include_details=include_details,
            )
            response = self._base_response(connection, "ue_get_asset")
            response.update(
                {
                    "assetPath": asset_path,
                    "found": asset is not None,
                    "requestedSections": list(normalized_sections),
                    "limits": {
                        "symbols": symbol_limit,
                        "references": reference_limit,
                        "graphs": graph_limit,
                        "nodes": node_limit,
                        "includeDetails": include_details,
                        "maxOutputTokens": max_output_tokens,
                    },
                    "filters": {
                        "graphGuid": graph_guid,
                        "nodeGuid": node_guid,
                    },
                    "sectionPagination": {},
                }
            )
            if asset is None:
                response["outputBudget"] = {
                    "maxTokens": max_output_tokens,
                    "estimatedTokens": estimate_json_tokens(response),
                    "truncated": False,
                    "truncationReason": "",
                }
                return response

            payload: dict[str, Any] = {}
            if "identity" in normalized_sections:
                for field in IDENTITY_FIELDS:
                    if field in asset:
                        payload[field] = asset[field]
            if "summary" in normalized_sections and "summary" in asset:
                payload["summary"] = asset["summary"]
            if "metadata" in normalized_sections:
                excluded = {*IDENTITY_FIELDS, "summary", *PAGED_ASSET_SECTIONS}
                for key, value in asset.items():
                    if key not in excluded:
                        payload[key] = value
            response["asset"] = payload

            truncation_reasons: list[str] = []
            for section in PAGED_ASSET_SECTIONS:
                if section not in normalized_sections:
                    continue
                raw_items = list(asset.get(section, []))
                section_limit = limits[section]
                page_items = raw_items[:section_limit]
                query_has_more = len(raw_items) > section_limit
                section_offset = offsets[section]

                def build_payload(selected: list[dict[str, Any]], *, section_name: str = section) -> dict[str, Any]:
                    candidate = dict(response)
                    candidate_asset = dict(payload)
                    candidate_asset[section_name] = selected
                    candidate["asset"] = candidate_asset
                    return candidate

                force_one = bool(continuation_token) or not any(
                    isinstance(payload.get(existing), list) and payload.get(existing)
                    for existing in PAGED_ASSET_SECTIONS
                )
                selected, _ = fit_sequence_to_budget(
                    page_items,
                    max_output_tokens=max_output_tokens,
                    build_payload=build_payload,
                    force_one=force_one,
                )
                payload[section] = selected
                has_more = query_has_more or len(selected) < len(page_items)
                section_token = ""
                if has_more:
                    section_token = self._continuations.issue(
                        tool="ue_get_asset",
                        snapshot_id=snapshot_id,
                        state={
                            "assetPath": asset_path,
                            "section": section,
                            "offset": section_offset + len(selected),
                            "symbolLimit": symbol_limit,
                            "referenceLimit": reference_limit,
                            "graphLimit": graph_limit,
                            "nodeLimit": node_limit,
                            "graphGuid": graph_guid,
                            "nodeGuid": node_guid,
                            "includeDetails": include_details,
                            "maxOutputTokens": max_output_tokens,
                        },
                    )
                if len(selected) < len(page_items):
                    reason = "token-budget"
                elif query_has_more:
                    reason = "section-limit"
                elif estimate_json_tokens(response) > max_output_tokens:
                    reason = "single-result-exceeds-token-budget"
                else:
                    reason = ""
                if reason and reason not in truncation_reasons:
                    truncation_reasons.append(reason)
                response["sectionPagination"][section] = {
                    "limit": section_limit,
                    "offset": section_offset,
                    "resultCount": len(selected),
                    "hasMore": has_more,
                    "mayHaveMore": has_more,
                    "continuationToken": section_token,
                    "source": source,
                    "truncationReason": reason,
                }

            response["asset"] = payload
            estimated_tokens = estimate_json_tokens(response)
            truncated = any(
                bool(section_info["hasMore"])
                for section_info in response["sectionPagination"].values()
            ) or estimated_tokens > max_output_tokens
            if estimated_tokens > max_output_tokens and "single-result-exceeds-token-budget" not in truncation_reasons:
                truncation_reasons.append("single-result-exceeds-token-budget")
            response["outputBudget"] = {
                "maxTokens": max_output_tokens,
                "estimatedTokens": estimate_json_tokens(response),
                "truncated": truncated,
                "truncationReason": ",".join(truncation_reasons),
            }
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
        direction: ReferenceDirection = "outgoing",
        depth: int = 1,
        project_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        include_details: bool = False,
        continuation_token: str = "",
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        with self._open() as connection:
            snapshot_id = self._snapshot_id(connection)
            source = "parameters"
            if continuation_token:
                state = self._continuations.resolve(
                    continuation_token,
                    tool="ue_find_references",
                    snapshot_id=snapshot_id,
                )
                query = str(state["query"])
                kind = str(state["kind"])
                asset_path = str(state["assetPath"])
                source_symbol_id = str(state["sourceSymbolId"])
                target_symbol_id = str(state["targetSymbolId"])
                target_asset_path = str(state["targetAssetPath"])
                direction = state["direction"]
                depth = int(state["depth"])
                project_only = bool(state["projectOnly"])
                limit = int(state["limit"])
                offset = int(state["offset"])
                include_details = bool(state["includeDetails"])
                max_output_tokens = int(state["maxOutputTokens"])
                source = "continuation-token"
            else:
                query = _clean_text(query, name="query", maximum=2048)
                kind = _clean_text(kind, name="kind", maximum=256)
                asset_path = _asset_path(asset_path)
                source_symbol_id = _stable_id(source_symbol_id, name="source_symbol_id")
                target_symbol_id = _stable_id(target_symbol_id, name="target_symbol_id")
                target_asset_path = _asset_path(target_asset_path, name="target_asset_path")
                if direction not in ("outgoing", "incoming", "both"):
                    raise ValueError("direction must be outgoing, incoming, or both")
                if depth < 1 or depth > 3:
                    raise ValueError("depth must be between 1 and 3")
                limit = _bounded_limit(limit, maximum=MAX_MCP_SEARCH_LIMIT, name="limit")
                offset = _bounded_offset(offset)
                max_output_tokens = normalize_output_token_budget(max_output_tokens)
                if not any((query, kind, asset_path, source_symbol_id, target_symbol_id, target_asset_path)):
                    raise ValueError("at least one reference filter is required")

            results = find_references(
                connection,
                query=query,
                kind=kind,
                asset_path=asset_path,
                source_symbol_id=source_symbol_id,
                target_symbol_id=target_symbol_id,
                target_asset_path=target_asset_path,
                direction=direction,
                depth=depth,
                project_only=project_only,
                limit=limit + 1,
                offset=offset,
                include_details=include_details,
            )
            response = self._base_response(connection, "ue_find_references")
            response["filters"] = {
                "query": query,
                "kind": kind,
                "assetPath": asset_path,
                "sourceSymbolId": source_symbol_id,
                "targetSymbolId": target_symbol_id,
                "targetAssetPath": target_asset_path,
                "direction": direction,
                "depth": depth,
                "projectOnly": project_only,
                "includeDetails": include_details,
            }
            state = {
                "query": query,
                "kind": kind,
                "assetPath": asset_path,
                "sourceSymbolId": source_symbol_id,
                "targetSymbolId": target_symbol_id,
                "targetAssetPath": target_asset_path,
                "direction": direction,
                "depth": depth,
                "projectOnly": project_only,
                "limit": limit,
                "includeDetails": include_details,
                "maxOutputTokens": max_output_tokens,
            }
            return self._paged_response(
                connection=connection,
                tool="ue_find_references",
                base=response,
                raw_results=results,
                limit=limit,
                offset=offset,
                max_output_tokens=max_output_tokens,
                token_state=state,
                source=source,
            )

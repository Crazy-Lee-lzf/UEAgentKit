from __future__ import annotations

import sqlite3
from typing import Any, Literal

from .agent_api import IndexQueryService
from .agent_workflow import PatchWorkflowService, WorkflowError
from .query_protocol import DEFAULT_OUTPUT_TOKEN_BUDGET


def register_query_tools(
    *,
    server: Any,
    index_service: IndexQueryService,
    workflow_service: PatchWorkflowService | None,
    live_editor_service: Any,
    read_annotations: Any,
    error_response: Any,
    capabilities_response: Any,
    project_status_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_get_capabilities() -> dict[str, Any]:
        """Return the active MCP mode, Tool contract, operation registry, limits, and safety guarantees."""
        return capabilities_response(workflow_service, live_editor_service)

    @server.tool(annotations=read_annotations)
    def ue_get_project_status() -> dict[str, Any]:
        """Return the fixed project, Engine, immutable index, workflow, freshness, and live-state summary."""
        try:
            return project_status_response(index_service, workflow_service, live_editor_service)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_get_project_status", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_search(
        query: str = "",
        scope: Literal["assets", "symbols"] = "assets",
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
        """Search assets or symbols with filters, offset compatibility, opaque continuation, and output budgeting."""
        try:
            return index_service.search(
                query,
                scope=scope,
                asset_class=asset_class,
                kind=kind,
                asset_path=asset_path,
                path_prefix=path_prefix,
                limit=limit,
                offset=offset,
                include_details=include_details,
                continuation_token=continuation_token,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_search", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_asset(
        asset_path: str = "",
        sections: list[str] | None = None,
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
        """Get selected asset sections with independent section pagination and output budgeting."""
        try:
            return index_service.get_asset(
                asset_path,
                sections=sections,
                symbol_limit=symbol_limit,
                reference_limit=reference_limit,
                graph_limit=graph_limit,
                node_limit=node_limit,
                graph_guid=graph_guid,
                node_guid=node_guid,
                include_details=include_details,
                continuation_token=continuation_token,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_get_asset", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_find_references(
        query: str = "",
        kind: str = "",
        asset_path: str = "",
        source_symbol_id: str = "",
        target_symbol_id: str = "",
        target_asset_path: str = "",
        direction: Literal["outgoing", "incoming", "both"] = "outgoing",
        depth: int = 1,
        project_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        include_details: bool = False,
        continuation_token: str = "",
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Find direct or bounded transitive reference edges with explicit direction and pagination."""
        try:
            return index_service.find_references(
                query=query,
                kind=kind,
                asset_path=asset_path,
                source_symbol_id=source_symbol_id,
                target_symbol_id=target_symbol_id,
                target_asset_path=target_asset_path,
                direction=direction,
                depth=depth,
                project_only=project_only,
                limit=limit,
                offset=offset,
                include_details=include_details,
                continuation_token=continuation_token,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_find_references", exc, read_only=True)

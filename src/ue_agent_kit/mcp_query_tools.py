from __future__ import annotations

import sqlite3
from typing import Any, Literal

from .agent_api import IndexQueryService
from .agent_workflow import PatchWorkflowService, WorkflowError
from .impact_analysis import (
    DEFAULT_IMPACT_DEPTH,
    DEFAULT_IMPACT_EDGES,
    DEFAULT_IMPACT_PATHS,
    MAX_IMPACT_CONSUMERS,
)
from .query_protocol import DEFAULT_OUTPUT_TOKEN_BUDGET
from .semantic_diff_workflow import SemanticDiffEvidenceError
from .verification_trust import VerificationTrustError


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

    @server.tool(annotations=read_annotations)
    def ue_analyze_change_impact(
        target_asset_paths: list[str],
        subject_kind: Literal[
            "asset-level",
            "blueprint-symbol",
            "data-table-row",
            "searchable-name",
            "data-asset-object",
            "material-instance-parent",
            "material-instance-parameter",
            "blueprint-member",
        ] = "asset-level",
        subject: str = "",
        max_depth: int = DEFAULT_IMPACT_DEPTH,
        max_consumers: int = MAX_IMPACT_CONSUMERS,
        max_edges: int = DEFAULT_IMPACT_EDGES,
        max_paths: int = DEFAULT_IMPACT_PATHS,
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Analyze deterministic bounded reverse-reference impact for one or more exact /Game targets."""
        try:
            return index_service.analyze_change_impact(
                target_asset_paths,
                subject_kind=subject_kind,
                subject=subject,
                max_depth=max_depth,
                max_consumers=max_consumers,
                max_edges=max_edges,
                max_paths=max_paths,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_analyze_change_impact", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_analyze_semantic_diff(
        change_set_id: str,
        stage: Literal["auto", "live", "persisted", "verified"] = "auto",
        asset_paths: list[str] | None = None,
        include_unchanged: bool = True,
        max_changes: int = 64,
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Align explicit Change Set intent with bounded live, persisted, or independently verified evidence."""
        try:
            if workflow_service is None:
                raise SemanticDiffEvidenceError(
                    "insufficient-evidence",
                    "Semantic Diff requires the fixed project Workflow evidence service.",
                )
            return workflow_service.analyze_semantic_diff(
                change_set_id,
                stage=stage,
                asset_paths=asset_paths,
                include_unchanged=include_unchanged,
                max_changes=max_changes,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_analyze_semantic_diff", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_build_verification_plan(
        change_set_id: str,
        impact_depth: int = 1,
        required_automation_tests: list[str] | None = None,
        extra_validation_assets: list[str] | None = None,
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Build deterministic verification obligations for one explicit Change Set without executing actions."""
        try:
            if workflow_service is None:
                raise VerificationTrustError(
                    "insufficient-evidence",
                    "Verification Plan requires the fixed project Workflow evidence service.",
                )
            return workflow_service.build_verification_plan(
                change_set_id,
                impact_depth=impact_depth,
                required_automation_tests=required_automation_tests,
                extra_validation_assets=extra_validation_assets,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_build_verification_plan", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_evaluate_trust_verdict(
        change_set_id: str,
        impact_depth: int = 1,
        required_automation_tests: list[str] | None = None,
        extra_validation_assets: list[str] | None = None,
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Evaluate deterministic evidence applicability without executing Compile, Validate, Verify, or writes."""
        try:
            if workflow_service is None:
                raise VerificationTrustError(
                    "insufficient-evidence",
                    "Trust Verdict requires the fixed project Workflow evidence service.",
                )
            return workflow_service.evaluate_trust_verdict(
                change_set_id,
                impact_depth=impact_depth,
                required_automation_tests=required_automation_tests,
                extra_validation_assets=extra_validation_assets,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_evaluate_trust_verdict", exc, read_only=True)

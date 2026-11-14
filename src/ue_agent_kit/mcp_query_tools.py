from __future__ import annotations

import sqlite3
from typing import Any, Literal

from .live_asset_overlay import get_session_overlay

OVERLAY_SECTION = "overlay"


def _overlay_record(asset_path: str) -> dict[str, Any] | None:
    """The session overlay entry for one asset, if a proven refresh installed one."""
    if not asset_path:
        return None
    return get_session_overlay().get(asset_path)


def _overlay_metadata(record: dict[str, Any]) -> dict[str, Any]:
    """Provenance only: never a field the index would also have claimed."""
    return {
        "revision": record.get("revision", ""),
        "assetClass": record.get("assetClass", ""),
        "capturedAtUtc": record.get("capturedAtUtc", ""),
        "editorSessionId": record.get("editorSessionId", ""),
        "recordSource": record.get("source", ""),
    }


def _overlay_only_response(
    asset_path: str,
    record: dict[str, Any],
    *,
    include_canonical: bool,
    degraded_reason: str = "",
) -> dict[str, Any]:
    """Answer from the overlay alone: used when the caller asked only for it, or
    when the index is unavailable but a proven live record exists."""
    metadata = _overlay_metadata(record)
    if include_canonical:
        metadata["canonical"] = record.get("canonical")
    payload: dict[str, Any] = {
        "schemaVersion": "1.0",
        "tool": "ue_get_asset",
        "ok": True,
        "readOnly": True,
        "source": "overlay",
        "assetPath": asset_path,
        "indexFresh": False,
        "overlay": metadata,
    }
    if degraded_reason:
        payload["degradedReason"] = degraded_reason
    return payload

from .agent_api import IndexQueryService  # noqa: E402
from .agent_workflow import PatchWorkflowService, WorkflowError  # noqa: E402
from .impact_analysis import (  # noqa: E402
    DEFAULT_IMPACT_DEPTH,
    DEFAULT_IMPACT_EDGES,
    DEFAULT_IMPACT_PATHS,
    MAX_IMPACT_CONSUMERS,
)
from .query_protocol import DEFAULT_OUTPUT_TOKEN_BUDGET  # noqa: E402
from .semantic_diff_workflow import SemanticDiffEvidenceError  # noqa: E402
from .verification_trust import VerificationTrustError  # noqa: E402


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
        profile: str = "",
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
                profile=profile,
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
        record = _overlay_record(asset_path)
        requested = sections
        wants_overlay = False
        include_canonical = False
        if requested:
            wants_overlay = OVERLAY_SECTION in requested
            if wants_overlay:
                trimmed = [item for item in requested if item != OVERLAY_SECTION]
                if not trimmed:
                    # The caller asked for the overlay only: do not touch the index.
                    if record is None:
                        return error_response(
                            "ue_get_asset",
                            ValueError(
                                "No proven live overlay record exists for this asset."
                            ),
                            read_only=True,
                        )
                    return _overlay_only_response(
                        asset_path, record, include_canonical=True
                    )
                requested = trimmed
                include_canonical = True
        try:
            response = index_service.get_asset(
                asset_path,
                sections=requested,
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
            if record is not None:
                # A proven live record can still answer when the index is absent,
                # but the response says so instead of pretending they agree.
                return _overlay_only_response(
                    asset_path,
                    record,
                    include_canonical=True,
                    degraded_reason=f"index-unavailable: {type(exc).__name__}",
                )
            return error_response("ue_get_asset", exc, read_only=True)
        if record is None:
            return response
        merged = dict(response) if isinstance(response, dict) else {"result": response}
        metadata = _overlay_metadata(record)
        if wants_overlay and include_canonical:
            metadata["canonical"] = record.get("canonical")
        # Explicit provenance: index-derived fields stay index-derived, the overlay
        # is always its own key so nothing silently swaps source.
        merged["source"] = "index+overlay"
        merged["indexFresh"] = False
        merged["overlay"] = metadata
        return merged

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
    def ue_find_world_actors(
        world: str = "",
        class_path: str = "",
        class_contains: str = "",
        tag: str = "",
        data_layer: str = "",
        label_contains: str = "",
        include_component_classes: bool = False,
        limit: int = 50,
        offset: int = 0,
        continuation_token: str = "",
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Find map actors by world/class/tag/data-layer with coverage flags and bounded pagination."""
        try:
            return index_service.find_world_actors(
                world=world,
                class_path=class_path,
                class_contains=class_contains,
                tag=tag,
                data_layer=data_layer,
                label_contains=label_contains,
                include_component_classes=include_component_classes,
                limit=limit,
                offset=offset,
                continuation_token=continuation_token,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_find_world_actors", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_find_world_partition_actors(
        world: str = "",
        base_class_path: str = "",
        class_contains: str = "",
        tag: str = "",
        data_layer: str = "",
        folder_prefix: str = "",
        limit: int = 50,
        offset: int = 0,
        continuation_token: str = "",
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Find World Partition actor descriptors (unloaded metadata and bounds) with coverage flags."""
        try:
            return index_service.find_world_partition_actors(
                world=world,
                base_class_path=base_class_path,
                class_contains=class_contains,
                tag=tag,
                data_layer=data_layer,
                folder_prefix=folder_prefix,
                limit=limit,
                offset=offset,
                continuation_token=continuation_token,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_find_world_partition_actors", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_world_summary(
        world: str = "",
        limit: int = 50,
        continuation_token: str = "",
        max_output_tokens: int = DEFAULT_OUTPUT_TOKEN_BUDGET,
    ) -> dict[str, Any]:
        """Report map world coverage: indexed revisions, ingestion timestamps, truncation and staleness."""
        try:
            return index_service.get_world_summary(
                world=world,
                limit=limit,
                continuation_token=continuation_token,
                max_output_tokens=max_output_tokens,
            )
        except (FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_get_world_summary", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_analyze_change_impact(
        target_asset_paths: list[str],
        subject_kind: Literal[
            "asset-level",
            "blueprint-symbol",
            "code-symbol",
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
        """Analyze deterministic bounded reverse-reference impact for indexed asset or source targets."""
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

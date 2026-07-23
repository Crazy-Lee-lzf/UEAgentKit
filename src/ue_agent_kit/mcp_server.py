from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, Literal, Sequence

from . import __version__
from .agent_api import (
    MAX_MCP_GRAPH_LIMIT,
    MAX_MCP_NODE_LIMIT,
    MAX_MCP_REFERENCE_LIMIT,
    MAX_MCP_SEARCH_LIMIT,
    MAX_MCP_SYMBOL_LIMIT,
    IndexQueryService,
    IndexSnapshotError,
)
from .agent_workflow import (
    MATERIAL_PARAMETER_OPERATIONS,
    PatchWorkflowConfig,
    PatchWorkflowService,
    WorkflowError,
)
from .config import DEFAULT_DATABASE
from .editor_bridge import (
    LIVE_EDITOR_METHODS,
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from .patches import get_operation_registry
from .query_protocol import (
    DEFAULT_OUTPUT_TOKEN_BUDGET,
    MAX_OUTPUT_TOKEN_BUDGET,
    MIN_OUTPUT_TOKEN_BUDGET,
    ContinuationTokenError,
)

try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
except ModuleNotFoundError as exc:  # pragma: no cover - exercised in dependency-free installs
    FastMCP = None  # type: ignore[assignment,misc]
    ToolAnnotations = None  # type: ignore[assignment,misc]
    _MCP_IMPORT_ERROR: ModuleNotFoundError | None = exc
else:
    _MCP_IMPORT_ERROR = None


MCP_SERVER_NAME = "UE Agent Kit"
TOOL_ROOT = Path(__file__).resolve().parents[2]
READ_TOOL_NAMES = ["ue_get_capabilities", "ue_get_project_status", "ue_search", "ue_get_asset", "ue_find_references"]
LIVE_EDITOR_TOOL_NAMES = list(LIVE_EDITOR_METHODS)
HIGH_LEVEL_WRITE_TOOL_NAMES = [
    "ue_set_blueprint_default",
    "ue_set_component_property",
    "ue_set_pin_default",
    "ue_set_asset_property",
    "ue_set_material_parameter",
    "ue_set_datatable_cell",
]
LOW_LEVEL_WRITE_TOOL_NAMES = ["ue_plan_patch", "ue_dry_run_patch", "ue_apply_patch", "ue_verify_asset", "ue_rollback_patch"]
WRITE_TOOL_NAMES = HIGH_LEVEL_WRITE_TOOL_NAMES + LOW_LEVEL_WRITE_TOOL_NAMES


def _server_instructions(
    write_tools_enabled: bool,
    commit_enabled: bool,
    live_editor_enabled: bool,
) -> str:
    base = (
        "Use ue_get_capabilities to inspect the active server contract and ue_get_project_status for the fixed "
        "project and index state. Use ue_search to locate assets or symbols, ue_get_asset for one exact asset path, "
        "and ue_find_references for dependency and Blueprint reference edges. "
    )
    live_text = (
        "The ue_editor_* and ue_get_* live tools read registered state from the fixed local Unreal Editor Bridge; "
        "they never accept endpoints, tokens, filesystem paths, arbitrary UObject calls, Console commands, Python, or Shell. "
        if live_editor_enabled
        else "Live Editor access is not configured. "
    )
    if not write_tools_enabled:
        return "Read-only access to the UE Agent Kit SQLite index. " + base + live_text + (
            "This server cannot execute shell commands or write assets."
        )
    commit_text = (
        "Explicit Commit and rollback Commit are enabled, but each requires a successful prior Dry Run, "
        "a one-time session receipt, and an exact confirmation phrase."
        if commit_enabled
        else "Planning, Dry Run, and independent verification are enabled; Commit and rollback Commit are disabled."
    )
    return (
        "Fixed-project UE Agent Kit workflow. " + base + live_text +
        "Prefer the ue_set_* tools for common Blueprint, asset, Material Instance, and DataTable changes. "
        "They create a strict Plan by default and may run Plan plus Dry Run, but never Commit. Use ue_apply_patch only "
        "with the returned planId and one-time Dry Run receipt. The low-level ue_plan_patch remains available for "
        "registered Operations not covered by a high-level Tool. Tool arguments cannot choose filesystem paths, policies, projects, "
        "engines, databases, Editor Bridge endpoints, or arbitrary Unreal commands. " + commit_text
    )


def _server_mode(write_tools_enabled: bool, commit_enabled: bool) -> str:
    if not write_tools_enabled:
        return "read-only"
    return "fixed-project-commit" if commit_enabled else "fixed-project-dry-run"


def _tool_descriptors(
    write_tools_enabled: bool,
    live_editor_enabled: bool,
) -> list[dict[str, Any]]:
    traits = {
        "ue_get_capabilities": (True, False),
        "ue_get_project_status": (True, False),
        "ue_search": (True, False),
        "ue_get_asset": (True, False),
        "ue_find_references": (True, False),
        "ue_editor_status": (True, False),
        "ue_get_selection": (True, False),
        "ue_get_open_assets": (True, False),
        "ue_get_dirty_assets": (True, False),
        "ue_get_current_level": (True, False),
        "ue_get_pie_state": (True, False),
        "ue_get_output_log": (True, False),
        "ue_get_compile_errors": (True, False),
        "ue_inspect_asset_live": (True, False),
        "ue_set_blueprint_default": (False, False),
        "ue_set_component_property": (False, False),
        "ue_set_pin_default": (False, False),
        "ue_set_asset_property": (False, False),
        "ue_set_material_parameter": (False, False),
        "ue_set_datatable_cell": (False, False),
        "ue_plan_patch": (False, False),
        "ue_dry_run_patch": (False, False),
        "ue_apply_patch": (False, True),
        "ue_verify_asset": (False, False),
        "ue_rollback_patch": (False, True),
    }
    names = list(READ_TOOL_NAMES)
    if live_editor_enabled:
        names += LIVE_EDITOR_TOOL_NAMES
    if write_tools_enabled:
        names += WRITE_TOOL_NAMES
    return [
        {
            "name": name,
            "readOnly": traits[name][0],
            "destructive": traits[name][1],
        }
        for name in names
    ]


def _read_engine_status(workflow_service: PatchWorkflowService | None) -> dict[str, Any]:
    if workflow_service is None:
        return {"configured": False, "state": "unavailable"}
    build_version = workflow_service.config.engine_root / "Engine" / "Build" / "Build.version"
    try:
        payload = json.loads(build_version.read_text(encoding="utf-8-sig"))
        major = int(payload["MajorVersion"])
        minor = int(payload["MinorVersion"])
        patch = int(payload["PatchVersion"])
    except (FileNotFoundError, OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return {"configured": True, "state": "unknown"}
    return {
        "configured": True,
        "state": "available",
        "version": f"{major}.{minor}.{patch}",
        "changelist": payload.get("Changelist"),
        "compatibleChangelist": payload.get("CompatibleChangelist"),
        "isLicenseeVersion": payload.get("IsLicenseeVersion"),
    }


def _capabilities_response(
    workflow_service: PatchWorkflowService | None,
    live_editor_service: LiveEditorBridgeService | None,
) -> dict[str, Any]:
    write_tools_enabled = workflow_service is not None
    commit_enabled = bool(workflow_service and workflow_service.config.commit_enabled)
    live_editor_enabled = live_editor_service is not None
    return {
        "schemaVersion": "1.0",
        "tool": "ue_get_capabilities",
        "ok": True,
        "readOnly": True,
        "server": {
            "name": MCP_SERVER_NAME,
            "version": __version__,
            "transport": "stdio",
            "mode": _server_mode(write_tools_enabled, commit_enabled),
        },
        "tools": _tool_descriptors(write_tools_enabled, live_editor_enabled),
        "operations": {
            "available": write_tools_enabled,
            "items": get_operation_registry() if write_tools_enabled else [],
        },
        "liveEditor": {
            "configured": live_editor_enabled,
            "transport": "localhost-tcp" if live_editor_enabled else "unavailable",
            "tools": LIVE_EDITOR_TOOL_NAMES if live_editor_enabled else [],
            "source": "editor-memory" if live_editor_enabled else "unavailable",
            "fixedProject": live_editor_enabled,
            "fixedEndpoint": live_editor_enabled,
            "projectVersionHandshake": live_editor_enabled,
            "arbitraryEndpointArguments": False,
            "arbitraryUObject": False,
            "arbitraryConsole": False,
            "arbitraryPython": False,
            "arbitraryShell": False,
            "writeSupported": False,
        },
        "highLevelChanges": {
            "available": write_tools_enabled,
            "tools": HIGH_LEVEL_WRITE_TOOL_NAMES if write_tools_enabled else [],
            "modes": ["Plan", "DryRun"],
            "defaultMode": "Plan",
            "commitSupportedDirectly": False,
            "commitUsesApplyReceiptWorkflow": True,
        },
        "limits": {
            "searchResults": MAX_MCP_SEARCH_LIMIT,
            "assetSymbols": MAX_MCP_SYMBOL_LIMIT,
            "assetReferences": MAX_MCP_REFERENCE_LIMIT,
            "assetGraphs": MAX_MCP_GRAPH_LIMIT,
            "assetNodes": MAX_MCP_NODE_LIMIT,
            "referenceTraversalDepth": 3,
            "outputTokenBudgetMinimum": MIN_OUTPUT_TOKEN_BUDGET,
            "outputTokenBudgetDefault": DEFAULT_OUTPUT_TOKEN_BUDGET,
            "outputTokenBudgetMaximum": MAX_OUTPUT_TOKEN_BUDGET,
            "singleAssetPerPatch": 1,
            "singleOperationPerAsset": 1,
            "liveSelectionItems": 200,
            "liveOpenAssets": 200,
            "liveDirtyPackages": 200,
            "liveLogEntries": 100,
            "liveLogBufferEntries": 4096,
            "liveLogEntryCharacters": 1024,
            "liveCompileBlueprintStates": 100,
            "liveAssetPathLength": 512,
        },
        "responseContract": {
            "schemaVersion": "1.0",
            "errorFields": ["code", "message", "retryable", "details", "suggestedAction"],
            "pagination": {
                "offsetCompatible": True,
                "continuationTokens": True,
                "continuationTokensOpaque": True,
                "continuationTokensSessionLocal": True,
                "continuationTokensBoundToIndexSnapshot": True,
            },
            "assetSections": ["identity", "summary", "metadata", "symbols", "references", "graphs", "nodes"],
            "outputTokenBudget": True,
            "liveSource": "live-editor-memory",
            "diagnostics": {
                "fields": ["diagnosticId", "reportId", "stage", "exitCode", "stdoutTail", "stderrTail"],
                "localPathsRedacted": True,
                "reportPathsExposed": False,
            },
        },
        "freshness": {
            "available": write_tools_enabled,
            "sources": ["sqlite", "revision-export", "disk-package"] if write_tools_enabled else ["sqlite"],
            "states": ["fresh", "stale", "partial", "unavailable", "unknown"],
            "planRequiresFreshIndex": write_tools_enabled,
            "commitMarksFixedSnapshotsStale": write_tools_enabled,
            "rollbackMayRestoreFreshState": write_tools_enabled,
            "liveEditorMemorySeparate": live_editor_enabled,
        },
        "safety": {
            "fixedServerConfiguration": True,
            "arbitrarySql": False,
            "arbitraryShell": False,
            "arbitraryFilesystem": False,
            "arbitraryConsole": False,
            "arbitraryPython": False,
            "arbitraryUObject": False,
            "dryRunRequiredForCommit": True,
            "oneTimeReceiptRequired": True,
            "explicitConfirmationRequired": True,
            "unboundedSaveAll": False,
        },
        "session": {
            "plansPersistent": False,
            "receiptsPersistent": False,
            "liveEditorConnectionPersistent": False,
        },
    }


def _project_status_response(
    index_service: IndexQueryService,
    workflow_service: PatchWorkflowService | None,
    live_editor_service: LiveEditorBridgeService | None,
) -> dict[str, Any]:
    index_status = index_service.check()
    index_metadata = index_status.get("indexMetadata", {})
    write_tools_enabled = workflow_service is not None
    commit_enabled = bool(workflow_service and workflow_service.config.commit_enabled)
    workflow_status = workflow_service.status() if workflow_service is not None else None
    freshness_reader = getattr(workflow_service, "freshness_status", None) if workflow_service is not None else None
    freshness_status = freshness_reader() if callable(freshness_reader) else {
        "state": "unknown",
        "indexFresh": None,
        "indexStale": None,
        "reason": "Revision Export and disk Package comparison is unavailable in this server mode.",
    }
    live_editor_status = (
        live_editor_service.status()
        if live_editor_service is not None
        else {
            "configured": False,
            "state": "unavailable",
            "reasonCode": "live-editor-disabled",
            "reason": "Live Editor Bridge is not enabled for this MCP Server.",
            "retryable": False,
        }
    )
    project_key = str(index_status.get("projectKey", ""))
    if workflow_status:
        project_name = str(workflow_status.get("projectName", project_key))
    elif live_editor_service is not None:
        project_name = live_editor_service.config.project_name
    else:
        project_name = project_key
    return {
        "schemaVersion": "1.0",
        "tool": "ue_get_project_status",
        "ok": True,
        "readOnly": True,
        "serverVersion": __version__,
        "serverMode": _server_mode(write_tools_enabled, commit_enabled),
        "project": {
            "projectKey": project_key,
            "projectName": project_name,
            "fixedProject": write_tools_enabled or live_editor_service is not None,
        },
        "engine": _read_engine_status(workflow_service),
        "database": {
            "configured": True,
            "state": "available",
            "schemaVersion": index_status.get("databaseSchemaVersion"),
            "immutable": bool(index_metadata.get("immutable")),
            "quiescent": bool(index_metadata.get("quiescent")),
            "lastIndexedAtUtc": index_metadata.get("lastIndexedAtUtc", ""),
            "manifestSchemaVersion": index_metadata.get("manifestSchemaVersion", ""),
            "exporterVersion": index_metadata.get("exporterVersion", ""),
            "profile": index_metadata.get("profile", ""),
            "stats": index_status.get("stats", {}),
        },
        "revisionExport": {
            "configured": write_tools_enabled,
            "state": "available" if write_tools_enabled else "unavailable",
        },
        "workflow": workflow_status or {
            "available": False,
            "writeToolsEnabled": False,
            "commitToolsEnabled": False,
        },
        "freshness": freshness_status,
        "liveEditor": live_editor_status,
    }


_RETRYABLE_ERROR_CODES = {
    "index-not-quiescent",
    "filesystem-error",
    "database-error",
    "workflow-timeout",
    "dry-run-failed",
    "commit-failed",
    "verify-export-failed",
    "rollback-dry-run-failed",
    "rollback-commit-failed",
    "workflow-report-missing",
    "live-editor-unavailable",
    "live-editor-timeout",
    "live-editor-connection-closed",
}


def _suggested_action(code: str) -> str:
    exact = {
        "database-not-found": "Check the fixed --database configuration and rebuild the index if it was moved.",
        "index-not-quiescent": "Finish indexing, close every SQLite writer, then retry with a quiescent snapshot.",
        "invalid-arguments": "Correct the Tool arguments using the published input schema and retry.",
        "invalid-continuation-token": "Restart pagination from the first request and use a continuation Token returned by this server session.",
        "database-error": "Validate the immutable index and rebuild it if the database is damaged or incompatible.",
        "filesystem-error": "Check access to the fixed server resources, then retry.",
        "workflow-timeout": "Check the Unreal process state and retry after the fixed workflow is responsive.",
        "commit-disabled": "Restart the server with explicitly authorized Commit tools and a Commit-enabled Policy.",
        "index-stale": "Refresh the target asset export and immutable SQLite index, then create a new plan.",
        "index-freshness-unavailable": "Restore the fixed Revision Export or package file so all Revision sources can be compared.",
        "policy-rejected": "Review the fixed Project Write Policy authorization for this exact asset, operation, and target.",
        "revision-conflict": "Refresh the Revision Export and immutable SQLite index, then create a new plan from the current Revision.",
        "dirty-package": "Save or discard the Dirty package, rebuild the fixed Revision snapshot, and create a new plan.",
        "ue-process-crashed": "Inspect the sanitized diagnostic and Unreal log tail, fix the crash cause, then repeat from a new Plan.",
        "workflow-report-missing": "Inspect the diagnosticId and Unreal process output; repeat the workflow only after confirming why no report was created.",
        "workflow-report-invalid": "Inspect the reportId and diagnostic details; fix the producer or corrupted report before retrying.",
        "live-editor-unavailable": "Start the fixed Unreal Editor project with the UE Agent Kit plugin enabled, then retry.",
        "live-editor-timeout": "Check whether the fixed Unreal Editor is responsive, then retry the read-only request.",
        "live-editor-connection-closed": "Retry after the fixed Unreal Editor Bridge publishes a new active session descriptor.",
        "live-editor-version-mismatch": "Use matching UE Agent Kit plugin and MCP Server versions, then restart both sessions.",
        "live-editor-project-mismatch": "Start the exact project fixed at MCP Server startup; endpoint arguments cannot override it.",
        "live-editor-authentication-failed": "Restart the Editor and MCP Server so a new local authenticated session is negotiated.",
        "live-editor-capability-unavailable": "Use only the registered Live Editor capabilities reported by ue_get_capabilities.",
        "live-editor-invalid-parameters": "Use the bounded Live Editor Tool schema and an exact /Game Object Path where required.",
    }
    if code in exact:
        return exact[code]
    if "receipt" in code or code.endswith("-not-found") or code.endswith("-consumed"):
        return "Repeat the required planning or Dry Run step in the current MCP session to obtain a fresh receipt."
    if "policy" in code or code in {"commit-not-allowed", "patch-plan-rejected", "patch-validation-failed"}:
        return "Review the fixed Project Write Policy and target authorization before creating a new plan."
    if "revision" in code or code in {"asset-not-indexed", "plan-tampered"}:
        return "Refresh the Revision Export and SQLite index, then create a new plan from the current asset Revision."
    return "Review the sanitized error details and fixed server configuration before retrying."


def _error_response(tool: str, error: Exception, *, read_only: bool) -> dict[str, Any]:
    message = str(error)
    details: dict[str, Any] = {}
    if isinstance(error, WorkflowError):
        code = error.code
        details = error.details
    elif isinstance(error, LiveEditorError):
        code = error.code
        details = error.details
    elif isinstance(error, FileNotFoundError):
        code = "database-not-found"
        message = "The configured UE Agent Kit database was not found."
    elif isinstance(error, OSError):
        code = "filesystem-error"
        message = "A configured UE Agent Kit resource could not be accessed."
    elif isinstance(error, IndexSnapshotError):
        code = "index-not-quiescent"
    elif isinstance(error, ContinuationTokenError):
        code = "invalid-continuation-token"
    elif isinstance(error, ValueError):
        code = "invalid-arguments"
    elif isinstance(error, sqlite3.Error):
        code = "database-error"
    else:
        code = "ue-agent-kit-error"
    response: dict[str, Any] = {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {
            "code": code,
            "type": type(error).__name__,
            "message": message,
            "retryable": code in _RETRYABLE_ERROR_CODES,
            "details": details,
            "suggestedAction": _suggested_action(code),
        },
    }
    return response


def create_mcp_server(
    database_path: Path,
    *,
    workflow_config: PatchWorkflowConfig | None = None,
    workflow_service: PatchWorkflowService | None = None,
    live_editor_service: LiveEditorBridgeService | None = None,
):
    if FastMCP is None or ToolAnnotations is None:
        raise RuntimeError(
            "MCP support is not installed. Run scripts\\setup_python.cmd -WithMcp "
            "or install the mcp optional dependency."
        ) from _MCP_IMPORT_ERROR

    index_service = IndexQueryService(database_path)
    index_service.check()
    if workflow_service is not None and workflow_config is not None:
        raise ValueError("Provide workflow_config or workflow_service, not both.")
    if workflow_service is None and workflow_config is not None:
        workflow_service = PatchWorkflowService(index_service, workflow_config)
    if (
        workflow_service is not None
        and live_editor_service is not None
        and workflow_service.config.project_path.resolve() != live_editor_service.config.project_path.resolve()
    ):
        raise ValueError("Workflow and Live Editor services must use the same fixed project")
    write_tools_enabled = workflow_service is not None
    commit_enabled = bool(workflow_service and workflow_service.config.commit_enabled)
    live_editor_enabled = live_editor_service is not None
    server = FastMCP(
        MCP_SERVER_NAME,
        instructions=_server_instructions(write_tools_enabled, commit_enabled, live_editor_enabled),
        json_response=True,
    )

    read_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    @server.tool(annotations=read_annotations)
    def ue_get_capabilities() -> dict[str, Any]:
        """Return the active MCP mode, Tool contract, operation registry, limits, and safety guarantees."""
        return _capabilities_response(workflow_service, live_editor_service)

    @server.tool(annotations=read_annotations)
    def ue_get_project_status() -> dict[str, Any]:
        """Return the fixed project, Engine, immutable index, workflow, freshness, and live-state summary."""
        try:
            return _project_status_response(index_service, workflow_service, live_editor_service)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _error_response("ue_get_project_status", exc, read_only=True)

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
            return _error_response("ue_search", exc, read_only=True)

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
            return _error_response("ue_get_asset", exc, read_only=True)

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
            return _error_response("ue_find_references", exc, read_only=True)

    if live_editor_service is not None:
        @server.tool(annotations=read_annotations)
        def ue_editor_status() -> dict[str, Any]:
            """Return fixed-project Live Editor availability, version, session, PIE, level, and Dirty summary."""
            return {
                "schemaVersion": "1.0",
                "tool": "ue_editor_status",
                "ok": True,
                "readOnly": True,
                "source": "live-editor-memory",
                "result": live_editor_service.status(),
            }

        @server.tool(annotations=read_annotations)
        def ue_get_selection() -> dict[str, Any]:
            """Return the bounded Actor, Component, Asset, and Object selection from the fixed Editor session."""
            try:
                return live_editor_service.call_tool("ue_get_selection")
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_get_selection", exc, read_only=True)

        @server.tool(annotations=read_annotations)
        def ue_get_open_assets() -> dict[str, Any]:
            """Return assets currently opened in registered asset editors in the fixed Editor session."""
            try:
                return live_editor_service.call_tool("ue_get_open_assets")
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_get_open_assets", exc, read_only=True)

        @server.tool(annotations=read_annotations)
        def ue_get_dirty_assets() -> dict[str, Any]:
            """Return bounded Dirty /Game packages and their Asset Registry paths from Editor memory."""
            try:
                return live_editor_service.call_tool("ue_get_dirty_assets")
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_get_dirty_assets", exc, read_only=True)

        @server.tool(annotations=read_annotations)
        def ue_get_current_level() -> dict[str, Any]:
            """Return the fixed Editor world, persistent/current level, World Partition, and Dirty state."""
            try:
                return live_editor_service.call_tool("ue_get_current_level")
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_get_current_level", exc, read_only=True)

        @server.tool(annotations=read_annotations)
        def ue_get_pie_state() -> dict[str, Any]:
            """Return whether the fixed Editor is stopped, playing, or simulating in editor."""
            try:
                return live_editor_service.call_tool("ue_get_pie_state")
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_get_pie_state", exc, read_only=True)

        @server.tool(annotations=read_annotations)
        def ue_get_output_log(
            category: str = "",
            minimum_verbosity: Literal[
                "fatal",
                "error",
                "warning",
                "display",
                "log",
                "verbose",
                "veryverbose",
            ] = "log",
            keyword: str = "",
            since_sequence: int = 0,
            since_utc: str = "",
            until_utc: str = "",
            pie_session_id: int = -1,
            limit: int = 100,
        ) -> dict[str, Any]:
            """Read bounded current-session Output Log entries using category, severity, text, UTC, PIE, and sequence filters."""
            try:
                return live_editor_service.call_tool(
                    "ue_get_output_log",
                    {
                        "category": category,
                        "minimumVerbosity": minimum_verbosity,
                        "keyword": keyword,
                        "sinceSequence": since_sequence,
                        "sinceUtc": since_utc,
                        "untilUtc": until_utc,
                        "pieSessionId": pie_session_id,
                        "limit": limit,
                    },
                )
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_get_output_log", exc, read_only=True)

        @server.tool(annotations=read_annotations)
        def ue_get_compile_errors(
            asset_path: str = "",
            since_sequence: int = 0,
            pie_session_id: int = -1,
            limit: int = 100,
        ) -> dict[str, Any]:
            """Return captured compiler-related warnings/errors plus current loaded Blueprint compile status."""
            try:
                return live_editor_service.call_tool(
                    "ue_get_compile_errors",
                    {
                        "assetPath": asset_path,
                        "sinceSequence": since_sequence,
                        "pieSessionId": pie_session_id,
                        "limit": limit,
                    },
                )
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_get_compile_errors", exc, read_only=True)

        @server.tool(annotations=read_annotations)
        def ue_inspect_asset_live(asset_path: str) -> dict[str, Any]:
            """Inspect one exact /Game asset in Asset Registry and current Editor memory without loading or modifying it."""
            try:
                return live_editor_service.call_tool(
                    "ue_inspect_asset_live",
                    {"assetPath": asset_path},
                )
            except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
                return _error_response("ue_inspect_asset_live", exc, read_only=True)

    if workflow_service is not None:
        planning_annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
        dry_run_annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
        destructive_annotations = ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=False,
            openWorldHint=False,
        )

        def _run_high_level_change(
            *,
            tool_name: str,
            mode: Literal["Plan", "DryRun"],
            asset_path: str,
            operation: str,
            target: dict[str, Any],
            value: Any,
            description: str,
        ) -> dict[str, Any]:
            return workflow_service.prepare_high_level_change(
                tool_name=tool_name,
                mode=mode,
                asset_path=asset_path,
                operation=operation,
                target=target,
                value=value,
                description=description,
            )

        @server.tool(annotations=planning_annotations)
        def ue_set_blueprint_default(
            asset_path: str,
            variable_name: str,
            value: Any,
            mode: Literal["Plan", "DryRun"] = "Plan",
            description: str = "",
        ) -> dict[str, Any]:
            """Plan or Dry Run one policy-authorized Blueprint variable default change."""
            try:
                return _run_high_level_change(
                    tool_name="ue_set_blueprint_default",
                    mode=mode,
                    asset_path=asset_path,
                    operation="setVariableDefault",
                    target={"variableName": variable_name},
                    value=value,
                    description=description,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_set_blueprint_default", exc, read_only=False)

        @server.tool(annotations=planning_annotations)
        def ue_set_component_property(
            asset_path: str,
            component_name: str,
            property_path: str,
            value: Any,
            mode: Literal["Plan", "DryRun"] = "Plan",
            description: str = "",
        ) -> dict[str, Any]:
            """Plan or Dry Run one policy-authorized Blueprint component property change."""
            try:
                return _run_high_level_change(
                    tool_name="ue_set_component_property",
                    mode=mode,
                    asset_path=asset_path,
                    operation="setComponentProperty",
                    target={"componentName": component_name, "propertyPath": property_path},
                    value=value,
                    description=description,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_set_component_property", exc, read_only=False)

        @server.tool(annotations=planning_annotations)
        def ue_set_pin_default(
            asset_path: str,
            graph_guid: str,
            node_guid: str,
            pin_name: str,
            value: Any,
            mode: Literal["Plan", "DryRun"] = "Plan",
            description: str = "",
        ) -> dict[str, Any]:
            """Plan or Dry Run one policy-authorized Blueprint pin default change."""
            try:
                return _run_high_level_change(
                    tool_name="ue_set_pin_default",
                    mode=mode,
                    asset_path=asset_path,
                    operation="setPinDefault",
                    target={"graphGuid": graph_guid, "nodeGuid": node_guid, "pinName": pin_name},
                    value=value,
                    description=description,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_set_pin_default", exc, read_only=False)

        @server.tool(annotations=planning_annotations)
        def ue_set_asset_property(
            asset_path: str,
            property_path: str,
            value: Any,
            mode: Literal["Plan", "DryRun"] = "Plan",
            description: str = "",
        ) -> dict[str, Any]:
            """Plan or Dry Run one policy-authorized non-Blueprint asset property change."""
            try:
                return _run_high_level_change(
                    tool_name="ue_set_asset_property",
                    mode=mode,
                    asset_path=asset_path,
                    operation="setAssetProperty",
                    target={"propertyPath": property_path},
                    value=value,
                    description=description,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_set_asset_property", exc, read_only=False)

        @server.tool(annotations=planning_annotations)
        def ue_set_material_parameter(
            asset_path: str,
            parameter_name: str,
            parameter_type: Literal["Scalar", "Vector", "Texture", "StaticSwitch"],
            value: Any,
            mode: Literal["Plan", "DryRun"] = "Plan",
            description: str = "",
        ) -> dict[str, Any]:
            """Plan or Dry Run one authorized Material Instance parameter change."""
            try:
                operation = MATERIAL_PARAMETER_OPERATIONS.get(parameter_type)
                if operation is None:
                    raise ValueError("parameter_type must be Scalar, Vector, Texture, or StaticSwitch")
                return _run_high_level_change(
                    tool_name="ue_set_material_parameter",
                    mode=mode,
                    asset_path=asset_path,
                    operation=operation,
                    target={"parameterName": parameter_name},
                    value=value,
                    description=description,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_set_material_parameter", exc, read_only=False)

        @server.tool(annotations=planning_annotations)
        def ue_set_datatable_cell(
            asset_path: str,
            row_name: str,
            field_name: str,
            value: Any,
            mode: Literal["Plan", "DryRun"] = "Plan",
            description: str = "",
        ) -> dict[str, Any]:
            """Plan or Dry Run one authorized existing DataTable row field change."""
            try:
                return _run_high_level_change(
                    tool_name="ue_set_datatable_cell",
                    mode=mode,
                    asset_path=asset_path,
                    operation="setDataTableCell",
                    target={"rowName": row_name, "fieldName": field_name},
                    value=value,
                    description=description,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_set_datatable_cell", exc, read_only=False)

        @server.tool(annotations=planning_annotations)
        def ue_plan_patch(
            asset_path: str,
            operation: str,
            target: dict[str, Any] | None = None,
            value: Any = None,
            description: str = "",
        ) -> dict[str, Any]:
            """Create and validate one policy-gated single-asset, single-operation patch plan."""
            try:
                return workflow_service.plan_patch(
                    asset_path=asset_path,
                    operation=operation,
                    target=target,
                    value=value,
                    description=description,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_plan_patch", exc, read_only=False)

        @server.tool(annotations=dry_run_annotations)
        def ue_dry_run_patch(plan_id: str) -> dict[str, Any]:
            """Run the stored plan through Unreal, restore memory state, and require unchanged disk Revision."""
            try:
                return workflow_service.dry_run_patch(plan_id)
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_dry_run_patch", exc, read_only=False)

        @server.tool(annotations=destructive_annotations)
        def ue_apply_patch(plan_id: str, dry_run_receipt: str, confirmation: str) -> dict[str, Any]:
            """Explicitly commit a plan using a fresh one-time Dry Run receipt and exact confirmation phrase."""
            try:
                return workflow_service.apply_patch(plan_id, dry_run_receipt, confirmation)
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_apply_patch", exc, read_only=False)

        @server.tool(annotations=planning_annotations)
        def ue_verify_asset(apply_receipt: str) -> dict[str, Any]:
            """Independently reload the committed asset in Unreal and verify its saved SHA-256 Revision."""
            try:
                return workflow_service.verify_asset(apply_receipt)
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_verify_asset", exc, read_only=True)

        @server.tool(annotations=destructive_annotations)
        def ue_rollback_patch(
            apply_receipt: str,
            mode: Literal["DryRun", "Commit"] = "DryRun",
            rollback_dry_run_receipt: str = "",
            confirmation: str = "",
        ) -> dict[str, Any]:
            """Validate rollback, then explicitly restore only with a fresh receipt and exact confirmation phrase."""
            try:
                return workflow_service.rollback_patch(
                    apply_receipt,
                    mode=mode,
                    rollback_dry_run_receipt=rollback_dry_run_receipt,
                    confirmation=confirmation,
                )
            except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                return _error_response("ue_rollback_patch", exc, read_only=mode == "DryRun")

    return server


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ue-agent-mcp",
        description="Run UE Agent Kit MCP over local stdio.",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=DEFAULT_DATABASE,
        help=f"Immutable read-only SQLite index path. Default: {DEFAULT_DATABASE}",
    )
    parser.add_argument("--enable-write-tools", action="store_true")
    parser.add_argument("--enable-commit-tools", action="store_true")
    parser.add_argument("--enable-live-editor", action="store_true")
    parser.add_argument("--engine-root", type=Path)
    parser.add_argument("--project", dest="project_path", type=Path)
    parser.add_argument("--policy", dest="policy_path", type=Path)
    parser.add_argument("--revision-export", type=Path)
    parser.add_argument("--work-root", type=Path, default=TOOL_ROOT / "Output" / "McpWorkflow")
    parser.add_argument("--backup-root", type=Path, default=TOOL_ROOT / "Backups" / "McpWorkflow")
    parser.add_argument("--process-timeout-seconds", type=int, default=1800)
    parser.add_argument("--live-editor-timeout-seconds", type=float, default=2.0)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate fixed configuration and print status without starting MCP.",
    )
    return parser


def _build_workflow_config(args: argparse.Namespace) -> PatchWorkflowConfig | None:
    if args.enable_commit_tools and not args.enable_write_tools:
        raise ValueError("--enable-commit-tools requires --enable-write-tools")
    if not args.enable_write_tools:
        configured = [args.engine_root, args.policy_path, args.revision_export]
        if any(item is not None for item in configured):
            raise ValueError("Engine, Policy, and Revision Export paths require --enable-write-tools")
        if args.project_path is not None and not args.enable_live_editor:
            raise ValueError("--project without write tools requires --enable-live-editor")
        return None
    required = {
        "--engine-root": args.engine_root,
        "--project": args.project_path,
        "--policy": args.policy_path,
        "--revision-export": args.revision_export,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError("Missing fixed workflow arguments: " + ", ".join(missing))
    if not 60 <= args.process_timeout_seconds <= 7200:
        raise ValueError("--process-timeout-seconds must be from 60 through 7200")
    return PatchWorkflowConfig(
        tool_root=TOOL_ROOT,
        engine_root=args.engine_root,
        project_path=args.project_path,
        policy_path=args.policy_path,
        revision_export=args.revision_export,
        work_root=args.work_root,
        backup_root=args.backup_root,
        commit_enabled=args.enable_commit_tools,
        process_timeout_seconds=args.process_timeout_seconds,
    )


def _build_live_editor_service(args: argparse.Namespace) -> LiveEditorBridgeService | None:
    if not args.enable_live_editor:
        return None
    if args.project_path is None:
        raise ValueError("--enable-live-editor requires --project")
    return LiveEditorBridgeService(
        LiveEditorBridgeConfig(
            project_path=args.project_path,
            timeout_seconds=args.live_editor_timeout_seconds,
        ),
        server_version=__version__,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        index_service = IndexQueryService(args.database)
        index_status = index_service.check()
        workflow_config = _build_workflow_config(args)
        workflow_service = PatchWorkflowService(index_service, workflow_config) if workflow_config is not None else None
        live_editor_service = _build_live_editor_service(args)
        if args.check:
            payload: dict[str, Any] = {
                "schemaVersion": "1.0",
                "tool": "ue_agent_kit_mcp_status",
                "ok": True,
                "index": index_status,
                "writeToolsEnabled": workflow_service is not None,
                "commitToolsEnabled": bool(workflow_service and workflow_service.config.commit_enabled),
                "liveEditorEnabled": live_editor_service is not None,
            }
            if workflow_service is not None:
                payload["workflow"] = workflow_service.status()
            if live_editor_service is not None:
                payload["liveEditor"] = live_editor_service.status()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        server = create_mcp_server(
            args.database,
            workflow_service=workflow_service,
            live_editor_service=live_editor_service,
        )
        server.run(transport="stdio")
        return 0
    except (LiveEditorError, WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(
            json.dumps(_error_response("ue_agent_kit_mcp", exc, read_only=False), ensure_ascii=False),
            file=sys.stderr,
        )
        return 2 if isinstance(exc, FileNotFoundError) else 1


if __name__ == "__main__":
    raise SystemExit(main())

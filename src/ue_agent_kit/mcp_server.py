from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence

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
from .animation_scale_fix_batch import (
    MAX_BATCH_SCALE_FIX_ASSETS,
    MAX_BATCH_SCALE_FIX_PLANS,
    MAX_BATCH_LIVE_STEP,
    SUPPORTED_BATCH_CLASSIFICATIONS,
)
from .agent_workflow import (
    PatchWorkflowConfig,
    PatchWorkflowService,
    WorkflowError,
)
from .change_sets import register_change_set_tools
from .config import DEFAULT_DATABASE, DEFAULT_MEMORY_DATABASE
from .editor_bridge import (
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from .mcp_live_action_tools import register_live_action_tools
from .mcp_live_tools import register_live_read_tools
from .mcp_memory_tools import register_memory_tools
from .mcp_query_tools import register_query_tools
from .mcp_realtime_tools import register_realtime_tools
from .mcp_workflow_tools import register_workflow_tools
from .realtime_tasks import register_batch_task_tools
from .memory_service import ProjectMemoryService, ProjectMemoryServiceError
from .patches import get_operation_registry
from .query_protocol import (
    DEFAULT_OUTPUT_TOKEN_BUDGET,
    MAX_OUTPUT_TOKEN_BUDGET,
    MIN_OUTPUT_TOKEN_BUDGET,
    ContinuationTokenError,
)
from .tool_registry import (
    HIGH_LEVEL_WRITE_TOOL_NAMES,
    LIVE_EDITOR_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    tool_descriptors_for_mode,
)
from .snapshot_lifecycle import (
    FrozenSessionSnapshot,
    SnapshotLifecycleError,
    freeze_active_snapshot,
    resolve_active_snapshot,
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
STRICT_MEMORY_ARGUMENT_TOOL_NAMES = (
    "ue_memory_get_context",
    "ue_memory_expand_node",
    "ue_memory_get_evidence",
    "ue_memory_update_knowledge",
    "ue_memory_update_work",
)


def _enforce_strict_tool_arguments(server: Any, tool_names: Sequence[str]) -> None:
    tool_manager = getattr(server, "_tool_manager", None)
    if tool_manager is None:
        raise RuntimeError("FastMCP Tool Manager is unavailable.")
    for tool_name in tool_names:
        tool = tool_manager.get_tool(tool_name)
        argument_model = getattr(getattr(tool, "fn_metadata", None), "arg_model", None)
        if tool is None or argument_model is None:
            raise RuntimeError(f"FastMCP argument model is unavailable for {tool_name}.")
        argument_model.model_config = {
            **argument_model.model_config,
            "extra": "forbid",
        }
        argument_model.model_rebuild(force=True)
        tool.parameters = argument_model.model_json_schema()


def _server_instructions(
    write_tools_enabled: bool,
    commit_enabled: bool,
    live_editor_enabled: bool,
    memory_enabled: bool,
) -> str:
    base = (
        "Use ue_get_capabilities to inspect the active server contract and ue_get_project_status for the fixed "
        "project and index state. Use ue_search to locate assets or symbols, ue_get_asset for one exact asset path, "
        "and ue_find_references for dependency and Blueprint reference edges. "
    )
    live_text = (
        "The ue_editor_*, ue_get_*, bounded Batch Task, and journaled Change Set live tools operate "
        "on the fixed local Unreal Editor Bridge; they never accept endpoints, tokens, filesystem "
        "paths, arbitrary UObject calls, Console commands, Python, or Shell. "
        if live_editor_enabled
        else "Live Editor access is not configured. "
    )
    memory_text = (
        "The ue_memory_* tools use one persistent database and Project Key fixed at server startup; "
        "Tool arguments cannot select another database or project. Query valid Project Memory before "
        "planning related work and never treat stale or superseded records as current facts. "
        if memory_enabled
        else "Persistent Project Memory is not configured. "
    )
    memory_workflow_text = (
        "After ue_verify_asset succeeds or ue_rollback_patch completes a verified Commit, call "
        "ue_memory_record_task with memoryTaskEvidence.arguments unchanged; never invent or edit "
        "its Patch, Backup Manifest, Validation Evidence, or Revision references. "
        if memory_enabled and write_tools_enabled
        else ""
    )
    if not write_tools_enabled:
        return "Read-only access to the UE Agent Kit SQLite index. " + base + live_text + memory_text + (
            "This server cannot execute shell commands or write assets."
        )
    commit_text = (
        "Explicit Commit and rollback Commit are enabled, but each requires a successful prior Dry Run, "
        "a one-time session receipt, and an exact confirmation phrase."
        if commit_enabled
        else "Planning, Dry Run, and independent verification are enabled; Commit and rollback Commit are disabled."
    )
    return (
        "Fixed-project UE Agent Kit workflow. " + base + live_text + memory_text +
        "Prefer the ue_set_* tools for common Blueprint, asset, Material Instance, and DataTable changes. "
        "They create a strict Plan by default and may run Plan plus Dry Run, but never Commit. Use ue_apply_patch only "
        "with the returned planId and one-time Dry Run receipt. The low-level ue_plan_patch remains available for "
        "registered Operations not covered by a high-level Tool. Tool arguments cannot choose filesystem paths, policies, projects, "
        "engines, databases, Editor Bridge endpoints, or arbitrary Unreal commands. "
        + memory_workflow_text
        + commit_text
    )


def _server_mode(
    write_tools_enabled: bool,
    commit_enabled: bool,
    memory_enabled: bool = False,
) -> str:
    if not write_tools_enabled:
        return "fixed-project-memory" if memory_enabled else "read-only"
    return "fixed-project-commit" if commit_enabled else "fixed-project-dry-run"


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
    memory_service: ProjectMemoryService | None,
) -> dict[str, Any]:
    write_tools_enabled = workflow_service is not None
    commit_enabled = bool(workflow_service and workflow_service.config.commit_enabled)
    live_editor_enabled = live_editor_service is not None
    memory_enabled = memory_service is not None
    memory_status = memory_service.status() if memory_service is not None else None
    return {
        "schemaVersion": "1.0",
        "tool": "ue_get_capabilities",
        "ok": True,
        "readOnly": True,
        "server": {
            "name": MCP_SERVER_NAME,
            "version": __version__,
            "transport": "stdio",
            "mode": _server_mode(write_tools_enabled, commit_enabled, memory_enabled),
        },
        "tools": tool_descriptors_for_mode(
            live_editor_enabled=live_editor_enabled,
            workflow_enabled=write_tools_enabled,
            memory_enabled=memory_enabled,
        ),
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
            "writeSupported": bool(live_editor_enabled and write_tools_enabled and commit_enabled),
            "assetWriteSupported": bool(live_editor_enabled and write_tools_enabled and commit_enabled),
            "editorActions": {
                "available": live_editor_enabled,
                "tools": [
                    "ue_open_asset",
                    "ue_focus_asset",
                    "ue_sync_content_browser",
                    "ue_focus_actor",
                    "ue_compile_blueprint",
                    "ue_validate_asset",
                    "ue_validate_folder",
                    "ue_run_automation_test",
                ] if live_editor_enabled else [],
                "saveSupported": bool(live_editor_enabled and write_tools_enabled and commit_enabled),
                "pieSupported": False,
                "assetPathsAreExactGameObjectPaths": True,
                "actorIdentity": "current-editor-world-actor-guid",
                "folderValidationMaxAssets": 500,
                "returnedValidationIssueLimit": 200,
                "automationExactTestNameOnly": True,
                "automationSingleParticipantOnly": True,
                "automationTimeoutSecondsMax": 300,
                "automationReturnedEntryLimit": 200,
                "validationEvidenceSchemaVersion": "1.0",
                "validationEvidenceProjectBound": True,
                "validationEvidenceRevisionSetBound": True,
                "automationRevisionCoverage": "not-applicable",
            },
            "authorizedSave": {
                "available": bool(live_editor_enabled and write_tools_enabled),
                "tool": "ue_save_authorized_asset" if write_tools_enabled else "",
                "commitEnabled": bool(live_editor_enabled and commit_enabled),
                "modes": ["Preview", "Commit"],
                "singleExactLoadedAssetOnly": True,
                "dirtyPackageRequired": True,
                "mapsSupported": False,
                "policyRequired": True,
                "revisionRequired": True,
                "oneTimeReceiptRequired": True,
                "exactConfirmationRequired": True,
                "backupBeforeSave": True,
                "independentVerification": True,
                "saveAllSupported": False,
            },
            "graphSelection": {
                "available": live_editor_enabled,
                "tool": "ue_get_blueprint_graph_selection" if live_editor_enabled else "",
                "scope": "ordinary-blueprint-editor",
                "materialEditorSupported": False,
                "niagaraEditorSupported": False,
                "controlRigEditorSupported": False,
                "editingSupported": False,
                "maxSelectedNodes": 100,
            },
            "editorContext": {
                "available": live_editor_enabled,
                "tool": "ue_get_editor_context" if live_editor_enabled else "",
                "readOnly": True,
                "singleRequestAggregation": True,
                "truncatedSectionsReported": True,
                "stageTimingsReported": True,
                "durationMsReported": True,
                "suggestedNextActions": True,
            },
            "animationScaleAudit": {
                "available": live_editor_enabled,
                "startTool": "ue_start_animation_scale_audit" if live_editor_enabled else "",
                "statusTool": "ue_get_animation_scale_audit" if live_editor_enabled else "",
                "cancelTool": "ue_cancel_animation_scale_audit" if live_editor_enabled else "",
                "reportTool": "ue_export_animation_scale_audit_report" if live_editor_enabled else "",
                "sourceTool": "ue_diagnose_animation_scale" if live_editor_enabled else "",
                "readOnlyAssets": True,
                "reportFormat": "json",
                "reportWorkRootBound": True,
                "reportArbitraryPathArguments": False,
                "explicitLoadIfNeeded": True,
                "candidateSources": ["explicit-list", "immutable-index-path-prefix"],
                "indexCandidateClass": "/Script/Engine.AnimSequence",
                "detailClassificationFilters": [
                    "normal",
                    "scale-too-small",
                    "scale-too-large",
                    "root-lock-candidate",
                    "root-track-candidate",
                    "root-motion-review",
                    "additive-requires-base-pose",
                    "unsupported-composite",
                    "load-failed",
                ],
                "detailSortOrders": ["processed-order", "asset-path", "classification"],
                "maxAssets": 1000,
                "maxBatchSize": 8,
                "maxPageSize": 50,
                "pollAdvancesOneBatch": True,
                "savesPackages": False,
            },
            "batchTasks": {
                "available": live_editor_enabled,
                "startTool": "ue_start_batch_task" if live_editor_enabled else "",
                "statusTool": "ue_get_batch_task" if live_editor_enabled else "",
                "cancelTool": "ue_cancel_batch_task" if live_editor_enabled else "",
                "concurrentTasks": 1,
                "operations": ["scanCurrentWorld"],
                "maxActors": 10000,
                "maxComponentsPerActor": 500,
                "maxDetailedActors": 200,
                "maxActorClassesReported": 50,
                "timeoutSecondsMax": 300,
                "frameStepped": True,
                "cancellable": True,
                "worldInvalidationDetected": True,
                "loadsAssets": False,
                "savesPackages": False,
                "modifiesSelection": False,
                "perActorMcpCalls": False,
            },
            "liveWriteChangeSets": {
                "available": bool(live_editor_enabled and write_tools_enabled),
                "createTool": "ue_create_change_set" if live_editor_enabled and write_tools_enabled else "",
                "getTool": "ue_get_change_set" if live_editor_enabled and write_tools_enabled else "",
                "maxChangeSets": 50,
                "maxReceiptsPerChangeSet": 100,
                "journaled": True,
                "workRootBound": True,
                "bindableTools": [
                    "ue_apply_asset_property_live",
                    "ue_undo_asset_property_live",
                    "ue_discard_asset_property_live",
                    "ue_save_authorized_asset",
                    "ue_verify_live_write",
                ],
            },
        },
        "projectMemory": {
            "configured": memory_enabled,
            "persistent": memory_enabled,
            "fixedProject": memory_enabled,
            "fixedDatabase": memory_enabled,
            "projectKey": memory_service.project_key if memory_service is not None else "",
            "schemaVersion": memory_status.schema_version if memory_status is not None else None,
            "recordCount": memory_status.record_count if memory_status is not None else 0,
            "nodeCount": memory_status.node_count if memory_status is not None else 0,
            "activeWorkCount": memory_status.active_work_count if memory_status is not None else 0,
            "tools": MEMORY_TOOL_NAMES if memory_enabled else [],
            "recordTypes": [
                "projectFact",
                "projectRule",
                "decisionRecord",
                "knownIssue",
                "taskRecord",
                "runtimeEvidence",
            ],
            "sourceKinds": ["user-confirmed", "tool-observed", "model-inferred"],
            "statuses": ["valid", "stale", "conflicted", "superseded", "unverified"],
            "knowledgeNodeTypes": [
                "project",
                "system",
                "feature",
                "component",
                "entity",
                "implementation",
            ],
            "activeWorkStatuses": ["planned", "in_progress", "blocked", "done", "cancelled"],
            "progressiveContextLevels": [0, 1, 2, 3, 4],
            "contextTokenEstimateRule": "approximately 4 chars per token",
            "revisionAware": True,
            "workflowEvidenceHandoff": bool(memory_enabled and write_tools_enabled),
            "workflowEvidenceSourceTools": (
                ["ue_verify_asset", "ue_rollback_patch"]
                if memory_enabled and write_tools_enabled
                else []
            ),
            "workflowEvidenceTargetTool": (
                "ue_memory_record_task" if memory_enabled and write_tools_enabled else ""
            ),
            "workflowEvidenceArgumentsPath": (
                "memoryTaskEvidence.arguments" if memory_enabled and write_tools_enabled else ""
            ),
            "vectorDatabase": False,
            "arbitraryDatabaseArguments": False,
            "arbitraryProjectArguments": False,
        },
        "animationScaleFixBatch": {
            "available": write_tools_enabled,
            "planningOnly": False,
            "planTool": "ue_plan_animation_scale_fix_batch" if write_tools_enabled else "",
            "getTool": "ue_get_animation_scale_fix_batch" if write_tools_enabled else "",
            "applyTool": "ue_apply_animation_scale_fix_batch_live" if write_tools_enabled else "",
            "undoTool": "ue_undo_animation_scale_fix_batch" if write_tools_enabled else "",
            "sourceReportTool": "ue_export_animation_scale_audit_report",
            "maxAssets": MAX_BATCH_SCALE_FIX_ASSETS,
            "maxSessionPlans": MAX_BATCH_SCALE_FIX_PLANS,
            "maxLiveStep": MAX_BATCH_LIVE_STEP,
            "supportedClassifications": sorted(SUPPORTED_BATCH_CLASSIFICATIONS),
            "expectedFinalScaleDefault": "audit-root-skeleton-reference-component-scale",
            "explicitFinalScaleOverrides": True,
            "duplicateAssetsRejected": True,
            "unsupportedSelectedClassificationRejected": True,
            "childPlansUseSingleAssetPolicyAndRevisionValidation": True,
            "liveApplyAvailable": bool(write_tools_enabled and live_editor_enabled and commit_enabled),
            "undoAvailable": bool(write_tools_enabled and live_editor_enabled and commit_enabled),
            "liveApplyConfirmation": "LIVE APPLY BATCH <batchPlanId>",
            "undoConfirmation": "UNDO BATCH <batchPlanId>",
            "getAdvancesWrites": False,
            "changeSetBound": True,
        },
        "highLevelChanges": {
            "available": write_tools_enabled,
            "tools": HIGH_LEVEL_WRITE_TOOL_NAMES if write_tools_enabled else [],
            "modes": ["Plan", "DryRun"],
            "defaultMode": "Plan",
            "commitSupportedDirectly": False,
            "commitUsesApplyReceiptWorkflow": True,
        },
        "assetState": {
            "available": write_tools_enabled,
            "tool": "ue_get_asset_state" if write_tools_enabled else "",
            "sources": ["editor-memory", "disk-package", "revision-export", "sqlite"],
            "memoryOptional": True,
            "memoryRevisionAvailable": False,
            "memoryCleanIsRevisionProof": False,
            "persistentRevisionsUseSha256": True,
            "readOnly": True,
        },
        "snapshotRefresh": {
            "available": write_tools_enabled,
            "tool": "ue_refresh_asset_index" if write_tools_enabled else "",
            "modes": ["Preview", "Apply"],
            "singleExactAsset": True,
            "policyAuthorized": True,
            "pairedGeneration": True,
            "atomicPointerSwitch": True,
            "currentSessionFrozen": True,
            "restartRequiredAfterApply": True,
            "arbitraryPaths": False,
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
            "liveBlueprintSelectedNodes": 100,
            "liveAssetPathLength": 512,
            "liveBatchConcurrentTasks": 1,
            "liveAnimationScaleAuditMaxAssets": 1000,
            "liveAnimationScaleAuditMaxBatchSize": 8,
            "liveAnimationScaleAuditMaxPageSize": 50,
            "liveBatchMaxActors": 10000,
            "liveBatchMaxComponentsPerActor": 500,
            "liveBatchMaxDetailedActors": 200,
            "liveBatchMaxActorClasses": 50,
            "liveBatchTimeoutSecondsMax": 300,
            "liveChangeSets": 50,
            "liveChangeSetMaxReceipts": 100,
            "memorySearchResults": 100,
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
            "singleAssetRefreshAvailable": write_tools_enabled,
            "pairedSnapshotGeneration": write_tools_enabled,
            "newSessionRequiredAfterRefresh": write_tools_enabled,
            "liveEditorMemorySeparate": live_editor_enabled,
        },
        "safety": {
            "fixedServerConfiguration": True,
            "fixedProjectMemoryConfiguration": memory_enabled,
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
            "workflowSnapshotFrozen": write_tools_enabled,
            "refreshInvalidatesWorkflowRecords": write_tools_enabled,
            "projectMemoryPersistent": memory_enabled,
        },
    }


def _project_status_response(
    index_service: IndexQueryService,
    workflow_service: PatchWorkflowService | None,
    live_editor_service: LiveEditorBridgeService | None,
    memory_service: ProjectMemoryService | None,
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
    memory_status = memory_service.status() if memory_service is not None else None
    return {
        "schemaVersion": "1.0",
        "tool": "ue_get_project_status",
        "ok": True,
        "readOnly": True,
        "serverVersion": __version__,
        "serverMode": _server_mode(
            write_tools_enabled,
            commit_enabled,
            memory_service is not None,
        ),
        "project": {
            "projectKey": project_key,
            "projectName": project_name,
            "fixedProject": (
                write_tools_enabled
                or live_editor_service is not None
                or memory_service is not None
            ),
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
        "projectMemory": {
            "configured": memory_status is not None,
            "state": "available" if memory_status is not None else "unavailable",
            "projectKey": memory_status.project_key if memory_status is not None else "",
            "schemaVersion": memory_status.schema_version if memory_status is not None else None,
            "recordCount": memory_status.record_count if memory_status is not None else 0,
            "nodeCount": memory_status.node_count if memory_status is not None else 0,
            "activeWorkCount": memory_status.active_work_count if memory_status is not None else 0,
            "countsByType": memory_status.counts_by_type if memory_status is not None else {},
            "countsByStatus": memory_status.counts_by_status if memory_status is not None else {},
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
    "live-editor-batch-task-busy",
    "live-editor-batch-task-failed",
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
        "live-editor-batch-task-busy": "Poll the active Batch Task with ue_get_batch_task; only one task runs at a time.",
        "live-editor-batch-task-not-found": "Start a Batch Task with ue_start_batch_task and use its returned taskId for status or cancel.",
        "live-editor-batch-task-world-invalidated": "Restart the Batch Task after the World or session stabilized.",
        "live-editor-batch-task-timeout": "Restart the Batch Task with a larger timeoutSeconds or a smaller scan bound.",
        "live-editor-batch-task-failed": "Retry with a smaller bounded scan after reviewing the sanitized task error.",
        "change-set-invalid": "Use the exact changeSetId returned by ue_create_change_set.",
        "change-set-not-found": "Create a fresh Change Set with ue_create_change_set and use its exact changeSetId.",
        "change-set-full": "Finish or revert the Change Set first; a Change Set holds at most 100 bound receipts.",
        "change-set-transaction-not-member": "Bind the live write to the Change Set at apply time before reverting, saving, or verifying it.",
        "snapshot-refresh-restart-required": "Restart the MCP server so the new paired snapshot generation becomes the frozen session snapshot.",
        "memory-project-mismatch": "Use the Project Memory service fixed to the active index Project Key.",
        "memory-record-project-mismatch": "Use a record ID that belongs to the fixed Project Memory project.",
        "memory-index-project-mismatch": "Validate against the index configured for the same fixed Project Key.",
        "memory-record-not-found": "Search the fixed Project Memory database for a current record ID, then retry.",
        "asset-state-invalid-asset": "Use one exact /Game Object Path from the fixed project.",
        "snapshot-refresh-invalid-asset": "Use one exact policy-authorized /Game Object Path.",
        "snapshot-refresh-revision-mismatch": "Save or revert the asset, then retry after its disk Package Revision is stable.",
        "snapshot-refresh-disk-space": "Free disk space under the fixed workflow root before retrying snapshot refresh.",
        "live-editor-asset-dirty": "Save or revert the target asset in Unreal Editor before refreshing its disk-backed index record.",
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
    elif isinstance(error, SnapshotLifecycleError):
        code = error.code
        details = error.details
    elif isinstance(error, LiveEditorError):
        code = error.code
        details = error.details
    elif isinstance(error, ProjectMemoryServiceError):
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
    memory_service: ProjectMemoryService | None = None,
    audit_report_root: Path | None = None,
):
    if FastMCP is None or ToolAnnotations is None:
        raise RuntimeError(
            "MCP support is not installed. Run scripts\\setup_python.cmd -WithMcp "
            "or install the mcp optional dependency."
        ) from _MCP_IMPORT_ERROR

    index_service = IndexQueryService(database_path)
    index_status = index_service.check()
    if memory_service is not None and memory_service.project_key != str(index_status.get("projectKey", "")):
        raise ValueError("Project Memory and index services must use the same fixed project")
    if workflow_service is not None and workflow_config is not None:
        raise ValueError("Provide workflow_config or workflow_service, not both.")
    if workflow_service is None and workflow_config is not None:
        workflow_service = PatchWorkflowService(index_service, workflow_config)
    if live_editor_service is not None:
        if audit_report_root is None:
            audit_report_root = (
                getattr(workflow_service.config, "work_root", TOOL_ROOT / "Output" / "McpWorkflow")
                if workflow_service is not None
                else TOOL_ROOT / "Output" / "McpWorkflow"
            )
        audit_report_root = audit_report_root.expanduser().resolve()
        output_root = (TOOL_ROOT / "Output").resolve()
        try:
            audit_report_root.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("Animation audit report root must be inside the UE Agent Kit Output directory") from exc
        if audit_report_root == output_root:
            raise ValueError("Animation audit report root must be a child of the UE Agent Kit Output directory")
    else:
        audit_report_root = None
    if (
        workflow_service is not None
        and live_editor_service is not None
        and workflow_service.config.project_path.resolve() != live_editor_service.config.project_path.resolve()
    ):
        raise ValueError("Workflow and Live Editor services must use the same fixed project")
    write_tools_enabled = workflow_service is not None
    commit_enabled = bool(workflow_service and workflow_service.config.commit_enabled)
    live_editor_enabled = live_editor_service is not None
    memory_enabled = memory_service is not None
    server = FastMCP(
        MCP_SERVER_NAME,
        instructions=_server_instructions(
            write_tools_enabled,
            commit_enabled,
            live_editor_enabled,
            memory_enabled,
        ),
        json_response=True,
    )

    read_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )

    register_query_tools(
        server=server,
        index_service=index_service,
        workflow_service=workflow_service,
        live_editor_service=live_editor_service,
        read_annotations=read_annotations,
        error_response=_error_response,
        capabilities_response=lambda workflow, live: _capabilities_response(
            workflow,
            live,
            memory_service,
        ),
        project_status_response=lambda index, workflow, live: _project_status_response(
            index,
            workflow,
            live,
            memory_service,
        ),
    )
    if memory_service is not None:
        register_memory_tools(
            server=server,
            memory_service=memory_service,
            index_database_path=index_service.database_path,
            read_annotations=read_annotations,
            tool_annotations_type=ToolAnnotations,
            error_response=_error_response,
        )
        _enforce_strict_tool_arguments(server, STRICT_MEMORY_ARGUMENT_TOOL_NAMES)
    if live_editor_service is not None:
        register_live_read_tools(
            server=server,
            live_editor_service=live_editor_service,
            index_service=index_service,
            audit_report_root=audit_report_root,
            read_annotations=read_annotations,
            tool_annotations_type=ToolAnnotations,
            error_response=_error_response,
        )
        register_live_action_tools(
            server=server,
            live_editor_service=live_editor_service,
            tool_annotations_type=ToolAnnotations,
            error_response=_error_response,
        )
        register_realtime_tools(
            server=server,
            live_editor_service=live_editor_service,
            read_annotations=read_annotations,
            error_response=_error_response,
        )
        register_batch_task_tools(
            server=server,
            live_editor_service=live_editor_service,
            read_annotations=read_annotations,
            tool_annotations_type=ToolAnnotations,
            error_response=_error_response,
        )
    if workflow_service is not None:
        register_workflow_tools(
            server=server,
            workflow_service=workflow_service,
            read_annotations=read_annotations,
            tool_annotations_type=ToolAnnotations,
            error_response=_error_response,
        )
        register_change_set_tools(
            server=server,
            workflow_service=workflow_service,
            read_annotations=read_annotations,
            tool_annotations_type=ToolAnnotations,
            error_response=_error_response,
        )

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
    parser.add_argument("--enable-project-memory", action="store_true")
    parser.add_argument(
        "--memory-database",
        type=Path,
        default=None,
        help=f"Persistent Project Memory SQLite path. Default when enabled: {DEFAULT_MEMORY_DATABASE}",
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
            policy_path=args.policy_path,
        ),
        server_version=__version__,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    frozen_snapshot: FrozenSessionSnapshot | None = None
    try:
        base_workflow_config = _build_workflow_config(args)
        live_editor_service = _build_live_editor_service(args)
        workflow_config: PatchWorkflowConfig | None = None
        database_path = args.database
        if base_workflow_config is not None:
            active_snapshot = resolve_active_snapshot(
                args.database,
                base_workflow_config.revision_export,
                base_workflow_config.work_root,
                base_workflow_config.project_path.stem,
            )
            validation_index = IndexQueryService(active_snapshot.database)
            validation_config = replace(
                base_workflow_config,
                revision_export=active_snapshot.revision_export,
                active_snapshot=active_snapshot,
            )
            PatchWorkflowService(
                validation_index,
                validation_config,
                live_editor_service=live_editor_service,
            )
            frozen_snapshot = freeze_active_snapshot(active_snapshot)
            database_path = frozen_snapshot.database
            workflow_config = replace(
                base_workflow_config,
                revision_export=frozen_snapshot.revision_export,
                active_snapshot=active_snapshot,
            )

        index_service = IndexQueryService(database_path)
        index_status = index_service.check()
        memory_service: ProjectMemoryService | None = None
        if args.enable_project_memory:
            project_key = str(index_status.get("projectKey", "")).strip()
            if not project_key:
                raise ValueError("The fixed index has no valid Project Key for Project Memory.")
            memory_service = ProjectMemoryService(
                database_path=args.memory_database or DEFAULT_MEMORY_DATABASE,
                project_key=project_key,
            )
            memory_service.status()
        elif args.memory_database is not None:
            raise ValueError("--memory-database requires --enable-project-memory")
        workflow_service = (
            PatchWorkflowService(
                index_service,
                workflow_config,
                live_editor_service=live_editor_service,
            )
            if workflow_config is not None
            else None
        )
        if args.check:
            payload: dict[str, Any] = {
                "schemaVersion": "1.0",
                "tool": "ue_agent_kit_mcp_status",
                "ok": True,
                "index": index_status,
                "writeToolsEnabled": workflow_service is not None,
                "commitToolsEnabled": bool(workflow_service and workflow_service.config.commit_enabled),
                "liveEditorEnabled": live_editor_service is not None,
                "projectMemoryEnabled": memory_service is not None,
            }
            if workflow_service is not None:
                payload["workflow"] = workflow_service.status()
            if live_editor_service is not None:
                payload["liveEditor"] = live_editor_service.status()
            if memory_service is not None:
                memory_status = memory_service.status()
                payload["projectMemory"] = {
                    "projectKey": memory_status.project_key,
                    "schemaVersion": memory_status.schema_version,
                    "recordCount": memory_status.record_count,
                    "nodeCount": memory_status.node_count,
                    "activeWorkCount": memory_status.active_work_count,
                    "countsByType": memory_status.counts_by_type,
                    "countsByStatus": memory_status.counts_by_status,
                }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        server = create_mcp_server(
            database_path,
            workflow_service=workflow_service,
            live_editor_service=live_editor_service,
            memory_service=memory_service,
            audit_report_root=args.work_root,
        )
        server.run(transport="stdio")
        return 0
    except (
        LiveEditorError,
        SnapshotLifecycleError,
        WorkflowError,
        FileNotFoundError,
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
    ) as exc:
        print(
            json.dumps(_error_response("ue_agent_kit_mcp", exc, read_only=False), ensure_ascii=False),
            file=sys.stderr,
        )
        return 2 if isinstance(exc, FileNotFoundError) else 1
    finally:
        if frozen_snapshot is not None:
            frozen_snapshot.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())

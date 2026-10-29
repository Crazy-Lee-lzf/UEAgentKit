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
    MAX_BATCH_SAVE_STEP,
    MAX_BATCH_VERIFY_STEP,
    MAX_BATCH_INDEX_REFRESH_STEP,
    MAX_BATCH_ROLLBACK_STEP,
    SUPPORTED_BATCH_CLASSIFICATIONS,
)
from .agent_workflow import (
    PatchWorkflowConfig,
    PatchWorkflowService,
    WorkflowError,
)
from .change_sets import register_change_set_tools
from .config import DEFAULT_DATABASE, DEFAULT_MEMORY_DATABASE, resolve_project_policy
from .editor_bridge import (
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from .impact_analysis import (
    MAX_IMPACT_CONSUMERS,
    MAX_IMPACT_DEPTH,
    MAX_IMPACT_EDGES,
    MAX_IMPACT_PATHS,
    MAX_IMPACT_TARGETS,
    ImpactAnalysisError,
    SUPPORTED_SUBJECT_KINDS,
    UNSUPPORTED_SUBJECT_KINDS,
)
from .index_snapshot import apply_editor_freshness
from .mcp_live_action_tools import register_live_action_tools
from .mcp_live_tools import GRAPH_CONTEXT_TOOL_NAME, register_live_read_tools
from .mcp_pure_live_tools import (
    PURE_LIVE_READ_TOOL_NAMES,
    register_pure_live_meta_tools,
)
from .mcp_graph_layout_test_only import register_graph_layout_test_only_tool
from .mcp_graph_layout_p8_test_only import register_graph_layout_p8_test_only_tool
from .mcp_graph_layout_experimental import register_graph_layout_experimental_tools
from .mcp_graph_edit_experimental import register_graph_edit_experimental_tools
from .mcp_pin_default_edit_experimental import register_pin_default_edit_experimental_tools
from .mcp_variable_write_experimental import register_variable_write_experimental_tools
from .mcp_rewire_write_experimental import register_rewire_write_experimental_tools
from .mcp_node_remove_write_experimental import register_node_remove_write_experimental_tools
from .mcp_focus_blueprint_graph_experimental import register_focus_blueprint_graph_tool
from .experimental_rewire_write import (
    APPLY_TOOL_NAME as EXPERIMENTAL_REWIRE_APPLY_TOOL_NAME,
    PREVIEW_TOOL_NAME as EXPERIMENTAL_REWIRE_PREVIEW_TOOL_NAME,
    SAVE_TOOL_NAME as EXPERIMENTAL_REWIRE_SAVE_TOOL_NAME,
)
from .experimental_node_remove_write import (
    APPLY_TOOL_NAME as EXPERIMENTAL_NODE_REMOVE_APPLY_TOOL_NAME,
    PREVIEW_TOOL_NAME as EXPERIMENTAL_NODE_REMOVE_PREVIEW_TOOL_NAME,
    SAVE_TOOL_NAME as EXPERIMENTAL_NODE_REMOVE_SAVE_TOOL_NAME,
)
from .experimental_variable_write import (
    APPLY_TOOL_NAME as EXPERIMENTAL_VARIABLE_APPLY_TOOL_NAME,
    PREVIEW_TOOL_NAME as EXPERIMENTAL_VARIABLE_PREVIEW_TOOL_NAME,
    SAVE_TOOL_NAME as EXPERIMENTAL_VARIABLE_SAVE_TOOL_NAME,
)
from .experimental_graph_edit import (
    APPLY_TOOL_NAME as EXPERIMENTAL_GRAPH_EDIT_APPLY_TOOL_NAME,
    PREVIEW_TOOL_NAME as EXPERIMENTAL_GRAPH_EDIT_PREVIEW_TOOL_NAME,
    SAVE_TOOL_NAME as EXPERIMENTAL_GRAPH_EDIT_SAVE_TOOL_NAME,
    GraphEditError,
)
from .experimental_pin_default_edit import (
    APPLY_TOOL_NAME as EXPERIMENTAL_PIN_DEFAULT_APPLY_TOOL_NAME,
    PREVIEW_TOOL_NAME as EXPERIMENTAL_PIN_DEFAULT_PREVIEW_TOOL_NAME,
    SAVE_TOOL_NAME as EXPERIMENTAL_PIN_DEFAULT_SAVE_TOOL_NAME,
)
from .experimental_graph_layout import (
    APPLY_TOOL_NAME as EXPERIMENTAL_APPLY_TOOL_NAME,
    SAVE_TOOL_NAME as EXPERIMENTAL_SAVE_TOOL_NAME,
)
from .p7_test_only import APPLY_TOOL_NAME, FIXED_ASSET_PATH, FIXED_PROJECT_PATH, is_fixed_project
from .p8_test_only import (
    APPLY_TOOL_NAME as P8_APPLY_TOOL_NAME,
    FIXED_ASSET_PATH as P8_FIXED_ASSET_PATH,
    FIXED_PROJECT_PATH as P8_FIXED_PROJECT_PATH,
    is_fixed_project as is_fixed_p8_project,
)
from .mcp_memory_tools import register_memory_tools
from .mcp_graph_patch_tools import (
    GRAPH_PATCH_PREVIEW_TOOL_NAME,
    register_graph_patch_tools,
)
from .mcp_query_tools import register_query_tools
from .mcp_realtime_tools import register_realtime_tools
from .mcp_source_control_tools import register_source_control_tools
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
from .semantic_diff import (
    MAX_SEMANTIC_DIFF_ASSETS,
    MAX_SEMANTIC_DIFF_CHANGES,
    MAX_SEMANTIC_DIFF_GAPS,
    MAX_SEMANTIC_DIFF_UNCHANGED,
    SEMANTIC_DIFF_SCHEMA_VERSION,
)
from .semantic_diff_workflow import SemanticDiffEvidenceError
from .verification_trust import (
    ASSERTION_FAMILIES,
    ASSERTION_REQUIREMENTS,
    ASSERTION_STATUSES,
    MAX_EXTRA_VALIDATION_ASSETS,
    MAX_REQUIRED_AUTOMATION_TESTS,
    MAX_VERIFICATION_ASSERTIONS,
    MAX_VERIFICATION_ASSETS,
    MAX_VERIFICATION_EVIDENCE_REFS,
    TRUST_VERDICT_STATES,
    VERIFICATION_RULE_VERSION,
    VERIFICATION_TRUST_SCHEMA_VERSION,
    VerificationTrustError,
)
from .source_control import P4SourceControlService
from .tool_registry import (
    HIGH_LEVEL_WRITE_TOOL_NAMES,
    LIVE_EDITOR_TOOL_NAMES,
    MEMORY_TOOL_NAMES,
    SOURCE_CONTROL_TOOL_NAMES,
    TOOL_DEFINITIONS_BY_NAME,
    tool_descriptors_for_mode,
)
from .snapshot_lifecycle import (
    FrozenSessionSnapshot,
    SnapshotLifecycleError,
    freeze_active_snapshot,
    resolve_active_snapshot,
)
from .task_context import (
    MAX_CANDIDATE_SEARCH_TERMS,
    MAX_CORRELATION_LINKS,
    MAX_TASK_CONTEXT_ASSETS,
    MAX_TASK_CONTEXT_CANDIDATES,
    TASK_CONTEXT_SCHEMA_VERSION,
    TaskContextService,
    register_task_context_tools,
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
    "ue_memory_list_l0_events",
    "ue_memory_get_l0_event",
    "ue_memory_get_context",
    "ue_memory_expand_node",
    "ue_memory_get_evidence",
    "ue_memory_update_knowledge",
    "ue_memory_update_work",
)
STRICT_TASK_CONTEXT_ARGUMENT_TOOL_NAMES = ("ue_get_task_context",)
STRICT_IMPACT_ARGUMENT_TOOL_NAMES = ("ue_analyze_change_impact",)
STRICT_SEMANTIC_DIFF_ARGUMENT_TOOL_NAMES = ("ue_analyze_semantic_diff",)
STRICT_VERIFICATION_TRUST_ARGUMENT_TOOL_NAMES = (
    "ue_build_verification_plan",
    "ue_evaluate_trust_verdict",
)
STRICT_SOURCE_CONTROL_ARGUMENT_TOOL_NAMES = (
    "ue_source_control_status",
    "ue_source_control_prepare_write",
    "ue_source_control_changelists",
    "ue_source_control_prepare_changelist",
    "ue_source_control_resolve_status",
    "ue_source_control_resolve_text",
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
        "Use ue_get_task_context with the task query and explicit target asset paths to obtain bounded Index, "
        "Revision, Memory, and Live Editor facts plus deterministic risks in one request; its correlation "
        "section deterministically joins Active Work, an explicitly requested Change Set, the Live Editor "
        "session, and Memory Evidence without model inference or persistence. "
        "Use ue_analyze_change_impact with exact /Game target paths for asset-level analysis, or "
        "with a safe project-relative indexed source owner plus subject_kind=code-symbol and an exact "
        "cpp:type stable ID for Code Knowledge analysis. The bounded reverse-reference result includes "
        "direct and indirect consumers, validation targets, and deterministic risks; static references "
        "never prove runtime breakage. "
        "Use ue_analyze_semantic_diff only with an explicit change_set_id to align Plan intent with "
        "live, persisted, or independently verified evidence; it is read-only, never discovers private "
        "Change Sets, and does not issue an R3 trust verdict. "
        "Use ue_build_verification_plan and ue_evaluate_trust_verdict only with an explicit change_set_id; "
        "they are read-only, deterministic, never ingest arbitrary evidence, and never auto-execute Compile, "
        "Validation, Automation, Save, Verify, rollback, or a Writer. A verified verdict is scoped to the "
        "generated plan and does not claim universal gameplay, visual, performance, or runtime correctness. "
        "For a persistent real write, first create a Change Set, then pass the same change_set_id through write, "
        "authorized save, independent verify, verified Semantic Diff, Verification Plan, every missing Required "
        "evidence action, and Trust Verdict. For an exact-rollback task, independently verify the transient write "
        "and complete rollback before optional actions that can load the asset. Once independent evidence proves "
        "package bytes, canonical state, frozen revision, dirty state, and package inventory exactly match the "
        "baseline, the rollback task is successful: do not evaluate Trust against the transient Change Set revision, "
        "and report trustVerdict not-evaluated. Harness cleanup is not Agent recovery. Do not refresh the frozen "
        "asset index or "
        "restart the MCP server before that scoped verdict; captured Compile, Validation, and Automation evidence is "
        "session-local and must be re-run after a restart. Persistence verified is not Trust verified. Stale, Dirty, "
        "policy-blocked, or Required-evidence-missing work is not successful merely because the block was detected. "
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
        "with the returned planId and one-time Dry Run receipt. When a request contains multiple W4 operations "
        "(setAssetProperty, setVariableDefault, setComponentProperty, setPinDefault), prefer the read-only "
        "ue_plan_live_write_batch planner. The low-level ue_plan_patch remains available for "
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
    source_control_service: P4SourceControlService | None = None,
    *,
    p7_test_only_graph_layout_apply: bool = False,
    p8_test_only_graph_layout_apply: bool = False,
    experimental_graph_layout_apply: bool = False,
    experimental_graphpatch_edit: bool = False,
    live_editor_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    write_tools_enabled = workflow_service is not None
    commit_enabled = bool(workflow_service and workflow_service.config.commit_enabled)
    live_editor_enabled = live_editor_service is not None
    memory_enabled = memory_service is not None
    source_control_enabled = source_control_service is not None
    experimental_registered = bool(live_editor_enabled and experimental_graph_layout_apply)
    observed_capabilities = (
        live_editor_status.get("capabilities")
        if isinstance(live_editor_status, dict)
        and live_editor_status.get("state") == "available"
        else []
    )
    experimental_plugin_opt_in = bool(
        isinstance(observed_capabilities, list)
        and "editor.applyBlueprintGraphLayoutExperimental" in observed_capabilities
        and "editor.saveBlueprintGraphLayoutExperimental" in observed_capabilities
    )
    graph_edit_registered = bool(live_editor_enabled and experimental_graphpatch_edit)
    graph_edit_plugin_opt_in = bool(
        isinstance(observed_capabilities, list)
        and "editor.previewBlueprintGraphEditExperimental" in observed_capabilities
        and "editor.applyBlueprintGraphEditExperimental" in observed_capabilities
        and "editor.saveBlueprintGraphEditExperimental" in observed_capabilities
    )
    pin_default_registered = bool(live_editor_enabled and experimental_graphpatch_edit)
    pin_default_plugin_opt_in = bool(
        isinstance(observed_capabilities, list)
        and "editor.previewBlueprintPinDefaultsExperimental" in observed_capabilities
        and "editor.applyBlueprintPinDefaultsExperimental" in observed_capabilities
        and "editor.saveBlueprintPinDefaultsExperimental" in observed_capabilities
    )
    variable_write_registered = bool(live_editor_enabled and experimental_graphpatch_edit)
    variable_write_plugin_opt_in = bool(
        isinstance(observed_capabilities, list)
        and "editor.previewBlueprintVariableCreateExperimental" in observed_capabilities
        and "editor.applyBlueprintVariableCreateExperimental" in observed_capabilities
        and "editor.saveBlueprintVariableCreateExperimental" in observed_capabilities
    )
    rewire_write_registered = bool(live_editor_enabled and experimental_graphpatch_edit)
    rewire_write_plugin_opt_in = bool(
        isinstance(observed_capabilities, list)
        and "editor.previewBlueprintRewireExperimental" in observed_capabilities
        and "editor.applyBlueprintRewireExperimental" in observed_capabilities
        and "editor.saveBlueprintRewireExperimental" in observed_capabilities
    )
    node_remove_write_registered = bool(live_editor_enabled and experimental_graphpatch_edit)
    node_remove_write_plugin_opt_in = bool(
        isinstance(observed_capabilities, list)
        and "editor.previewBlueprintNodeRemoveExperimental" in observed_capabilities
        and "editor.applyBlueprintNodeRemoveExperimental" in observed_capabilities
        and "editor.saveBlueprintNodeRemoveExperimental" in observed_capabilities
    )
    memory_status = memory_service.status() if memory_service is not None else None
    verification_evidence_status = (
        workflow_service.verification_evidence_store.status()
        if workflow_service is not None
        else {
            "persistent": False,
            "arbitraryIngest": False,
            "projectBound": True,
            "bounded": True,
            "recordCount": 0,
            "maxRecords": 256,
            "capturedTools": [],
        }
    )
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
        "tools": (
            tool_descriptors_for_mode(
                live_editor_enabled=live_editor_enabled,
                workflow_enabled=write_tools_enabled,
                memory_enabled=memory_enabled,
                source_control_enabled=source_control_enabled,
            )
            + (
                [
                    {
                        "name": APPLY_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                    }
                ]
                if live_editor_enabled and p7_test_only_graph_layout_apply
                else []
            )
            + (
                [
                    {
                        "name": EXPERIMENTAL_APPLY_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_SAVE_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                        "experimental": True,
                    },
                ]
                if live_editor_enabled and experimental_graph_layout_apply
                else []
            )
            + (
                [
                    {
                        "name": EXPERIMENTAL_GRAPH_EDIT_PREVIEW_TOOL_NAME,
                        "readOnly": True,
                        "destructive": False,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_GRAPH_EDIT_APPLY_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_GRAPH_EDIT_SAVE_TOOL_NAME,
                        "readOnly": False,
                        "destructive": False,
                        "experimental": True,
                    },
                ]
                if live_editor_enabled and experimental_graphpatch_edit
                else []
            )
            + (
                [
                    {
                        "name": EXPERIMENTAL_PIN_DEFAULT_PREVIEW_TOOL_NAME,
                        "readOnly": True,
                        "destructive": False,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_PIN_DEFAULT_APPLY_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_PIN_DEFAULT_SAVE_TOOL_NAME,
                        "readOnly": False,
                        "destructive": False,
                        "experimental": True,
                    },
                ]
                if live_editor_enabled and experimental_graphpatch_edit
                else []
            )
            + (
                [
                    {
                        "name": EXPERIMENTAL_VARIABLE_PREVIEW_TOOL_NAME,
                        "readOnly": True,
                        "destructive": False,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_VARIABLE_APPLY_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_VARIABLE_SAVE_TOOL_NAME,
                        "readOnly": False,
                        "destructive": False,
                        "experimental": True,
                    },
                ]
                if live_editor_enabled and experimental_graphpatch_edit
                else []
            )
            + (
                [
                    {
                        "name": EXPERIMENTAL_REWIRE_PREVIEW_TOOL_NAME,
                        "readOnly": True,
                        "destructive": False,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_REWIRE_APPLY_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_REWIRE_SAVE_TOOL_NAME,
                        "readOnly": False,
                        "destructive": False,
                        "experimental": True,
                    },
                ]
                if live_editor_enabled and experimental_graphpatch_edit
                else []
            )
            + (
                [
                    {
                        "name": EXPERIMENTAL_NODE_REMOVE_PREVIEW_TOOL_NAME,
                        "readOnly": True,
                        "destructive": False,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_NODE_REMOVE_APPLY_TOOL_NAME,
                        "readOnly": False,
                        "destructive": True,
                        "experimental": True,
                    },
                    {
                        "name": EXPERIMENTAL_NODE_REMOVE_SAVE_TOOL_NAME,
                        "readOnly": False,
                        "destructive": False,
                        "experimental": True,
                    },
                ]
                if live_editor_enabled and experimental_graphpatch_edit
                else []
            )
        ),
        "operations": {
            "available": write_tools_enabled,
            "items": get_operation_registry() if write_tools_enabled else [],
        },
        "codeKnowledge": {
            "supported": True,
            "sourceProfile": "code",
            "sourceAssetClass": "CppSourceFile",
            "sourceSemanticKinds": ["class", "struct", "enum"],
            "sourceReferenceKinds": ["inherits", "include"],
            "reflectionSupported": True,
            "reflectionKinds": ["class", "struct", "enum", "function", "property"],
            "reflectionReferenceKinds": ["inherits", "implements"],
            "impactSubjectKind": "code-symbol",
            "impactTrustedReferenceKinds": ["inherits", "include", "implements"],
            "queryTools": ["ue_search", "ue_get_asset", "ue_find_references", "ue_analyze_change_impact"],
            "workflowScript": "scripts/RunCodeKnowledge.ps1",
            "fullCppParser": False,
            "callGraph": False,
        },
        "sourceControl": {
            "configured": source_control_enabled,
            "advisory": True,
            "tools": SOURCE_CONTROL_TOOL_NAMES if source_control_enabled else [],
            "submitCapability": False,
            "revertCapability": False,
            "deleteCapability": False,
            "arbitraryCommandExecution": False,
            "shellPassthrough": False,
            "providerUnavailableDegradesToAdvisory": True,
            "pendingChangelistPreparation": source_control_enabled,
            "boundedTextResolve": source_control_enabled,
            "binaryAutomaticResolve": False,
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
            "pieRuntimeInspection": {
                "available": live_editor_enabled,
                "readOnly": True,
                "requiresDatabase": False,
                "serverStartupRequiresIndexDatabase": True,
                "requiresPieStopped": False,
                "pieControlSupported": False,
                "tools": [
                    "ue_get_pie_world_contexts",
                    "ue_inspect_pie_actor",
                ] if live_editor_enabled else [],
                "bridgeMethods": [
                    "editor.getPieWorldContexts",
                    "editor.inspectPieActor",
                ] if live_editor_enabled else [],
                "maxWorldContexts": 32,
                "maxActorComponents": 128,
                "maxComponentAssets": 32,
                "maxAmbiguousCandidates": 8,
                "actorIdentityFields": ["actorPath", "actorGuid", "actorName", "actorLabel"],
                "actorIdentityPolicy": "exactly-one-field-and-reject-ambiguous-names",
                "serverClientIdentification": True,
                "errorCodes": [
                    "pie-not-running",
                    "pie-instance-not-found",
                    "pie-instance-stale",
                    "pie-instance-ambiguous",
                    "pie-world-stale",
                    "pie-actor-not-found",
                    "pie-actor-ambiguous",
                    "pie-actor-stale",
                    "pie-actor-scan-truncated",
                    "pie-context-scan-truncated",
                    "pie-invalid-parameters",
                ],
            },
            "pieScalarInspection": {
                "available": live_editor_enabled,
                "tool": "ue_get_pie_scalars" if live_editor_enabled else "",
                "bridgeMethod": "editor.getPieScalars" if live_editor_enabled else "",
                "readOnly": True,
                "requiresDatabase": False,
                "source": "pie-live",
                "maxProperties": 8,
                "maxPropertyNameChars": 64,
                "defaultStringChars": 256,
                "maxStringChars": 512,
                "supportedTypes": [
                    "bool",
                    "int32",
                    "int64",
                    "uint32",
                    "uint8",
                    "float32",
                    "float64",
                    "name",
                    "string",
                    "enum",
                ],
                "rejects": [
                    "dotted-paths",
                    "arrays",
                    "maps",
                    "sets",
                    "structs",
                    "text",
                    "object-references",
                    "methods",
                    "native-private",
                    "deprecated",
                    "not-edit-or-blueprint-visible",
                ],
                "identityPolicy": "exact-pie-instance-and-single-actor-identity",
                "componentPolicy": "at-most-one-owned-component-path-or-name-with-optional-class-check",
                "perPropertyStatus": ["ok", "unknown", "not-exposed", "unsupported-type"],
                "propertyProvenance": {
                    "onlyForResolvedProperties": True,
                    "fields": [
                        "declaringClassPath",
                        "declaringClassName",
                        "declaringClassKind",
                        "declaringClassPathTruncated",
                        "declaringBlueprintPath",
                        "declaringBlueprintPathTruncated",
                    ],
                    "kinds": ["native", "blueprint", "unknown"],
                    "source": "UE reflection: FProperty::GetOwnerClass()",
                    "policy": (
                        "reflected declaring owner class only; no filesystem path, no source-file "
                        "guessing, no raw object address; blueprint asset path only when "
                        "UClass::ClassGeneratedBy is a UBlueprint"
                    ),
                    "maxClassPathChars": 512,
                },
                "errorCodes": [
                    "pie-component-not-found",
                    "pie-component-ambiguous",
                    "pie-component-stale",
                ],
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
            "graphLayoutPreflight": {
                "available": live_editor_enabled,
                "tool": "ue_get_blueprint_graph_layout_preflight" if live_editor_enabled else "",
                "bridgeMethod": "editor.getBlueprintGraphLayoutPreflight" if live_editor_enabled else "",
                "readOnly": True,
                "selectionPolicy": "most-recently-active-blueprint-editor",
                "scope": "focused-graph-tab",
                "opensOrFocusesAssets": False,
                "writesBlueprint": False,
                "savesPackages": False,
                "maxReturnedNodes": 512,
                "truncationReported": True,
                "writeToolRegistered": False,
                "storage": "live-editor-memory",
            },
            "graphLayoutPreview": {
                "available": live_editor_enabled,
                "tool": "ue_preview_blueprint_graph_layout" if live_editor_enabled else "",
                "bridgeMethods": [
                    "editor.getBlueprintGraphLayoutPreflight",
                    "editor.getFocusedBlueprintGraphContext",
                ]
                if live_editor_enabled
                else [],
                "readOnly": True,
                "selectionPolicy": "most-recently-active-blueprint-editor",
                "scope": "focused-graph-tab",
                "modes": ["full", "selection", "incremental"],
                "canonicalAlgorithm": "python-p0-layout",
                "opensOrFocusesAssets": False,
                "writesBlueprint": False,
                "savesPackages": False,
                "requiresCleanPackage": True,
                "requiresPieStopped": True,
                "completeGraphOnly": True,
                "maxContextBytes": 716800,
                "sameProcessEvidenceOnly": True,
                "acceptanceEligible": False,
            },
            "p7TestOnlyGraphLayoutApply": {
                "available": bool(live_editor_enabled and p7_test_only_graph_layout_apply),
                "registered": bool(live_editor_enabled and p7_test_only_graph_layout_apply),
                "tool": APPLY_TOOL_NAME if live_editor_enabled and p7_test_only_graph_layout_apply else "",
                "bridgeMethod": "editor.applyBlueprintGraphLayoutTestOnly"
                if live_editor_enabled and p7_test_only_graph_layout_apply
                else "",
                "readOnly": False,
                "destructive": True,
                "testOnly": True,
                "fixedProject": str(FIXED_PROJECT_PATH),
                "fixedAsset": FIXED_ASSET_PATH,
                "publicGeneralWrite": False,
                "acceptsProjectParameter": False,
                "acceptsAssetPathParameter": False,
                "acceptsSaveParameter": False,
                "acceptsCompileParameter": False,
                "explicitConfirmation": True,
                "undoRedoVerifiedInCall": True,
                "autoSave": False,
                "requiresExplicitSeparateSave": True,
            },
            "p8TestOnlyGraphLayoutApply": {
                "available": bool(live_editor_enabled and p8_test_only_graph_layout_apply),
                "registered": bool(live_editor_enabled and p8_test_only_graph_layout_apply),
                "tool": P8_APPLY_TOOL_NAME
                if live_editor_enabled and p8_test_only_graph_layout_apply
                else "",
                "bridgeMethod": "editor.applyBlueprintGraphLayoutP8TestOnly"
                if live_editor_enabled and p8_test_only_graph_layout_apply
                else "",
                "readOnly": False,
                "destructive": True,
                "testOnly": True,
                "fixedProject": str(P8_FIXED_PROJECT_PATH),
                "fixedAsset": P8_FIXED_ASSET_PATH,
                "publicGeneralWrite": False,
                "acceptsProjectParameter": False,
                "acceptsAssetPathParameter": False,
                "acceptsSaveParameter": False,
                "acceptsCompileParameter": False,
                "explicitConfirmation": True,
                "undoRedoVerifiedInCall": True,
                "autoSave": False,
                "requiresExplicitSeparateSave": True,
            },
            "experimentalGraphLayoutApply": {
                "available": bool(experimental_registered and experimental_plugin_opt_in),
                "registered": experimental_registered,
                "pluginCapabilityObserved": experimental_plugin_opt_in,
                "applyTool": EXPERIMENTAL_APPLY_TOOL_NAME
                if experimental_registered
                else "",
                "saveTool": EXPERIMENTAL_SAVE_TOOL_NAME
                if experimental_registered
                else "",
                "bridgeMethods": [
                    "editor.applyBlueprintGraphLayoutExperimental",
                    "editor.saveBlueprintGraphLayoutExperimental",
                ]
                if experimental_registered
                else [],
                "readOnly": False,
                "destructive": True,
                "experimental": True,
                "productionDefault": False,
                "productionEnabled": False,
                "productionApprovalRequired": False,
                "authorizationModel": (
                    "project-level experimental opt-in plus exact per-call confirmation; "
                    "this does not claim OwnerTrust/T10 production enablement"
                ),
                "explicitOptIn": {
                    "serverFlag": "--enable-experimental-graph-layout-apply",
                    "projectConfig": "[UEAgentKit] ExperimentalGraphLayoutApply=True",
                    "requiresRunningEditorCapability": True,
                },
                "sourcePreviewTool": "ue_preview_blueprint_graph_layout",
                "requiresCompletePreviewResult": True,
                "acceptsCallerBuiltRequest": False,
                "modes": ["full", "selection", "incremental"],
                "exactFocusedGraphOnly": True,
                "requiresCleanPackage": True,
                "requiresPieStopped": True,
                "requiresCurrentRevisionAndFingerprint": True,
                "saveRequiresPostApplyGraphFingerprint": True,
                "explicitApplyConfirmation": "APPLY EXPERIMENTAL LAYOUT <planId>",
                "explicitSaveConfirmation": "SAVE EXPERIMENTAL LAYOUT <layoutHash>",
                "undoRedoVerifiedInCall": True,
                "singleTransaction": True,
                "autoSave": False,
                "requiresExplicitSeparateSave": True,
                "saveReadback": "asset-sha256+live-layout-hash+package-revision",
                "independentReloadVerified": False,
                "maxRequestBytes": 61440,
            },
            "experimentalGraphPatchEdit": {
                "available": bool(graph_edit_registered and graph_edit_plugin_opt_in),
                "registered": graph_edit_registered,
                "pluginCapabilityObserved": graph_edit_plugin_opt_in,
                "previewTool": EXPERIMENTAL_GRAPH_EDIT_PREVIEW_TOOL_NAME
                if graph_edit_registered
                else "",
                "applyTool": EXPERIMENTAL_GRAPH_EDIT_APPLY_TOOL_NAME
                if graph_edit_registered
                else "",
                "saveTool": EXPERIMENTAL_GRAPH_EDIT_SAVE_TOOL_NAME
                if graph_edit_registered
                else "",
                "bridgeMethods": [
                    "editor.previewBlueprintGraphEditExperimental",
                    "editor.applyBlueprintGraphEditExperimental",
                    "editor.saveBlueprintGraphEditExperimental",
                ]
                if graph_edit_registered
                else [],
                "readOnlyPreview": True,
                "destructiveApply": True,
                "experimental": True,
                "productionDefault": False,
                "productionEnabled": False,
                "productionApprovalRequired": False,
                "authorizationModel": (
                    "project-level experimental opt-in plus exact per-call confirmation; "
                    "this is not a general GraphPatch executor"
                ),
                "explicitOptIn": {
                    "serverFlag": "--enable-experimental-graphpatch-edit",
                    "projectConfig": "[UEAgentKit] ExperimentalGraphPatchEdit=True",
                    "requiresRunningEditorCapability": True,
                },
                "requiresCompletePreviewResult": True,
                "acceptsCallerBuiltRequest": False,
                "supportedNodeTypes": [
                    "branch",
                    "sequence",
                    "not_bool",
                    "bool_or",
                    "int_add",
                    "int_equal",
                    "double_add",
                    "string_concat",
                    "variable_get",
                    "variable_set",
                ],
                "supportedConnections": [
                    "exec: then/else/then_0/then_1 -> execute",
                    "bool: bool-producing ReturnValue/condition -> bool input",
                    "int: int-producing ReturnValue -> int input",
                    "double: double-producing ReturnValue -> double input",
                    "string: string-producing ReturnValue -> string input",
                    "variable_get: <VariableName> -> bool/int/double/string input",
                    "variable_set: then/Output_Get -> bool/int/double/string input; execute accepts one exec source",
                    "new output -> existing unlinked exact input (nodeGuid + pinGuid + name/direction/category)",
                    "existing exact output -> new input (fan-out preserved; no existing edge is broken)",
                    "one existing output -> one existing unlinked input (nodes=[], exact single edge)",
                    "operation=disconnect: remove one exact existing edge (nodes=[], exact endpoints)",
                    "one output pin may fan out to distinct input pins",
                ],
                "existingPinConnections": {
                    "operations": ["connect", "disconnect"],
                    "maxExistingEndpointsPerConnection": 2,
                    "maxPureExistingToExistingPerPlan": 1,
                    "maxDisconnectPerPlan": 1,
                    "pureExistingRequiresZeroNodes": True,
                    "pureExistingRequiresExactlyOneConnection": True,
                    "disconnectRequiresZeroNodes": True,
                    "disconnectRequiresExistingExactEdge": True,
                    "disconnectPreservesOtherEdgesAndNodes": True,
                    "newToNewConnectionsRemainSupported": True,
                    "existingInputMustBeUnlinked": True,
                    "existingOutputFanOutPreserved": True,
                    "requiresMakeResponse": True,
                    "rejectsBreakOthers": True,
                    "rejectsConversionAndPromotion": True,
                    "bindings": [
                        "graphFingerprint",
                        "packageRevision",
                        "beforeEdgeSetHash",
                        "nodeGuid",
                        "pinGuid",
                        "pinName",
                        "direction",
                        "category",
                        "subCategory",
                    ],
                },
                "variableAccessorNodes": {
                    "nodeTypes": ["variable_get", "variable_set"],
                    "scope": "existing top-level member variables only, bool/int/double/string, bound by GUID + name",
                    "graphs": ["event (uber)", "function", "macro"],
                    "createsVariables": False,
                    "renamesOrRetypesVariables": False,
                    "compiles": False,
                },
                "callFunctionNodes": {
                    "nodeType": "call_function",
                    "ownerClassPath": "/Script/Engine.KismetMathLibrary",
                    "allowlist": ["Greater_IntInt", "Less_IntInt", "Clamp", "Abs_Int"],
                    "requiresOwnerFunctionAndSignatureDigest": True,
                    "requiresExactPinSchema": True,
                    "staticPureOnly": True,
                    "rejectsWorldLatentNetRpcRefOutConstContainers": True,
                    "rejectsArbitraryFunctionPath": True,
                    "staticContractOnly": True,
                    "nativePending": True,
                    "compiles": False,
                },
                "strictPinTypeCheck": True,
                "saveRequiresPostApplyGraphFingerprint": True,
                "explicitSaveConfirmation": "SAVE EXPERIMENTAL GRAPH NODES <planId>",
                "saveRequiresExactNodeAndLinkCounts": True,
                "exactFocusedGraphOnly": True,
                "requiresCleanPackage": True,
                "requiresPieStopped": True,
                "requiresCurrentRevisionAndFingerprint": True,
                "explicitApplyConfirmation": "CREATE EXPERIMENTAL GRAPH NODES <planId>",
                "undoRedoVerifiedInCall": True,
                "singleTransaction": True,
                "autoSave": False,
                "requiresExplicitSeparateSave": True,
                "maxNodes": 16,
                "maxConnections": 24,
                "maxConnectionsPerNode": 8,
                "maxRequestBytes": 61440,
            },
            "experimentalPinDefaultEdit": {
                "available": bool(pin_default_registered and pin_default_plugin_opt_in),
                "registered": pin_default_registered,
                "pluginCapabilityObserved": pin_default_plugin_opt_in,
                "previewTool": EXPERIMENTAL_PIN_DEFAULT_PREVIEW_TOOL_NAME
                if pin_default_registered
                else "",
                "applyTool": EXPERIMENTAL_PIN_DEFAULT_APPLY_TOOL_NAME
                if pin_default_registered
                else "",
                "saveTool": EXPERIMENTAL_PIN_DEFAULT_SAVE_TOOL_NAME
                if pin_default_registered
                else "",
                "bridgeMethods": [
                    "editor.previewBlueprintPinDefaultsExperimental",
                    "editor.applyBlueprintPinDefaultsExperimental",
                    "editor.saveBlueprintPinDefaultsExperimental",
                ]
                if pin_default_registered
                else [],
                "readOnlyPreview": True,
                "destructiveApply": True,
                "experimental": True,
                "productionDefault": False,
                "productionEnabled": False,
                "productionApprovalRequired": False,
                "authorizationModel": (
                    "project-level experimental opt-in plus exact per-call confirmation; "
                    "this edits only existing unlinked input pin defaults"
                ),
                "explicitOptIn": {
                    "serverFlag": "--enable-experimental-graphpatch-edit",
                    "projectConfig": "[UEAgentKit] ExperimentalGraphPatchEdit=True",
                    "requiresRunningEditorCapability": True,
                },
                "requiresCompletePreviewResult": True,
                "acceptsCallerBuiltRequest": False,
                "supportedPinTypes": ["bool", "int", "double", "string"],
                "strictDefaultEncoding": {
                    "bool": "true|false",
                    "int": "canonical signed 32-bit decimal",
                    "double": "canonical finite decimal without exponent",
                    "string": "at most 1024 non-control characters; empty allowed",
                },
                "existingInputPinsOnly": True,
                "requiresUnlinkedPin": True,
                "rejectsHiddenReadOnlyIgnoredContainerByRef": True,
                "requiresExactNodePinAndPinGuidIdentity": True,
                "exactFocusedGraphOnly": True,
                "requiresCleanPackage": True,
                "requiresPieStopped": True,
                "requiresCurrentRevisionAndFingerprint": True,
                "requiresPinDefaultStateFingerprint": True,
                "explicitApplyConfirmation": "SET EXPERIMENTAL BLUEPRINT PIN DEFAULTS <planId>",
                "explicitSaveConfirmation": "SAVE EXPERIMENTAL BLUEPRINT PIN DEFAULTS <planId>",
                "saveRequiresPostApplyGraphAndDefaultFingerprints": True,
                "saveRequiresExactNodeAndLinkCounts": True,
                "undoRedoVerifiedInCall": True,
                "singleTransaction": True,
                "autoSave": False,
                "requiresExplicitSeparateSave": True,
                "maxDefaults": 16,
                "maxStringLength": 1024,
                "maxRequestBytes": 61440,
            },
            "experimentalVariableWrite": {
                "available": bool(variable_write_registered and variable_write_plugin_opt_in),
                "registered": variable_write_registered,
                "pluginCapabilityObserved": variable_write_plugin_opt_in,
                "previewTool": EXPERIMENTAL_VARIABLE_PREVIEW_TOOL_NAME
                if variable_write_registered
                else "",
                "applyTool": EXPERIMENTAL_VARIABLE_APPLY_TOOL_NAME
                if variable_write_registered
                else "",
                "saveTool": EXPERIMENTAL_VARIABLE_SAVE_TOOL_NAME
                if variable_write_registered
                else "",
                "bridgeMethods": [
                    "editor.previewBlueprintVariableCreateExperimental",
                    "editor.applyBlueprintVariableCreateExperimental",
                    "editor.saveBlueprintVariableCreateExperimental",
                ]
                if variable_write_registered
                else [],
                "readOnlyPreview": True,
                "destructiveApply": True,
                "experimental": True,
                "productionDefault": False,
                "productionEnabled": False,
                "productionApprovalRequired": False,
                "authorizationModel": (
                    "project-level experimental opt-in plus exact per-call confirmation; "
                    "this appends new member variables to one exact saved Blueprint"
                ),
                "explicitOptIn": {
                    "serverFlag": "--enable-experimental-graphpatch-edit",
                    "projectConfig": "[UEAgentKit] ExperimentalGraphPatchEdit=True",
                    "requiresRunningEditorCapability": True,
                },
                "requiresCompletePreviewResult": True,
                "acceptsCallerBuiltRequest": False,
                "supportedVariableTypes": ["bool", "int", "double", "string"],
                "strictDefaultEncoding": {
                    "bool": "true|false",
                    "int": "canonical signed 32-bit decimal",
                    "double": "canonical finite decimal without exponent or negative zero",
                    "string": "at most 1024 non-control characters; empty allowed",
                },
                "strictAsciiVariableName": True,
                "rejectsDuplicateAndInheritedNames": True,
                "rejectsContainersByRefAndCompositeTypes": True,
                "exactFocusedBlueprintOnly": True,
                "requiresCleanPackage": True,
                "requiresPieStopped": True,
                "requiresCurrentRevisionAndVariableFingerprint": True,
                "explicitApplyConfirmation": "CREATE EXPERIMENTAL BLUEPRINT VARIABLES <planId>",
                "explicitSaveConfirmation": "SAVE EXPERIMENTAL BLUEPRINT VARIABLES <planId>",
                "saveRequiresPostApplyFingerprintRevisionAndCount": True,
                "undoRedoVerifiedInCall": True,
                "singleTransaction": True,
                "autoSave": False,
                "compiled": False,
                "skeletonRegenerated": False,
                "requiresExplicitCompileAfterApply": True,
                "requiresExplicitSeparateSave": True,
                "maxVariables": 8,
                "maxVariableNameLength": 64,
                "maxStringLength": 1024,
                "maxRequestBytes": 61440,
            },
            "experimentalBlueprintRewire": {
                "available": bool(rewire_write_registered and rewire_write_plugin_opt_in),
                "registered": rewire_write_registered,
                "pluginCapabilityObserved": rewire_write_plugin_opt_in,
                "previewTool": EXPERIMENTAL_REWIRE_PREVIEW_TOOL_NAME
                if rewire_write_registered
                else "",
                "applyTool": EXPERIMENTAL_REWIRE_APPLY_TOOL_NAME
                if rewire_write_registered
                else "",
                "saveTool": EXPERIMENTAL_REWIRE_SAVE_TOOL_NAME
                if rewire_write_registered
                else "",
                "bridgeMethods": [
                    "editor.previewBlueprintRewireExperimental",
                    "editor.applyBlueprintRewireExperimental",
                    "editor.saveBlueprintRewireExperimental",
                ]
                if rewire_write_registered
                else [],
                "readOnlyPreview": True,
                "destructiveApply": True,
                "experimental": True,
                "productionDefault": False,
                "productionEnabled": False,
                "productionApprovalRequired": False,
                "authorizationModel": (
                    "project-level experimental opt-in plus exact per-call confirmation; "
                    "this replaces or removes exactly one existing edge in one exact "
                    "focused top-level K2 event graph"
                ),
                "explicitOptIn": {
                    "serverFlag": "--enable-experimental-graphpatch-edit",
                    "projectConfig": "[UEAgentKit] ExperimentalGraphPatchEdit=True",
                    "requiresRunningEditorCapability": True,
                },
                "requiresCompletePreviewResult": True,
                "acceptsCallerBuiltRequest": False,
                "operations": ["rewire", "disconnect"],
                "supportedPinKinds": ["exec", "bool", "int", "double", "string"],
                "rejectedPinTraits": [
                    "wildcard",
                    "delegate",
                    "container",
                    "by-reference",
                    "const",
                    "weak-pointer",
                    "struct-or-object",
                    "hidden",
                    "orphaned",
                    "split",
                ],
                "singleEdgeOnly": True,
                "bindsExactEdgeSetHash": True,
                "bindsExactNodeAndPinGuids": True,
                "bindsExactOldSourcePin": True,
                "untouchedEdgesPreserved": True,
                "neverBreakAllPinLinks": True,
                "rejectsSchemaBreakOtherLinks": True,
                "rejectsSchemaConversions": True,
                "explicitApplyConfirmation": "REWIRE EXPERIMENTAL BLUEPRINT PIN <planId>",
                "explicitSaveConfirmation": "SAVE EXPERIMENTAL BLUEPRINT REWIRE <planId>",
                "saveRequiresPostApplyEdgeHashRevisionAndCounts": True,
                "undoRedoVerifiedInCall": True,
                "singleTransaction": True,
                "autoSave": False,
                "compiled": False,
                "skeletonRegenerated": False,
                "requiresExplicitSeparateSave": True,
                "maxRequestBytes": 61440,
            },
            "experimentalBlueprintNodeRemove": {
                "available": bool(
                    node_remove_write_registered and node_remove_write_plugin_opt_in
                ),
                "registered": node_remove_write_registered,
                "pluginCapabilityObserved": node_remove_write_plugin_opt_in,
                "previewTool": EXPERIMENTAL_NODE_REMOVE_PREVIEW_TOOL_NAME
                if node_remove_write_registered
                else "",
                "applyTool": EXPERIMENTAL_NODE_REMOVE_APPLY_TOOL_NAME
                if node_remove_write_registered
                else "",
                "saveTool": EXPERIMENTAL_NODE_REMOVE_SAVE_TOOL_NAME
                if node_remove_write_registered
                else "",
                "bridgeMethods": [
                    "editor.previewBlueprintNodeRemoveExperimental",
                    "editor.applyBlueprintNodeRemoveExperimental",
                    "editor.saveBlueprintNodeRemoveExperimental",
                ]
                if node_remove_write_registered
                else [],
                "readOnlyPreview": True,
                "destructiveApply": True,
                "experimental": True,
                "productionDefault": False,
                "productionEnabled": False,
                "productionApprovalRequired": False,
                "authorizationModel": (
                    "project-level experimental opt-in plus exact per-call confirmation; "
                    "this deletes exactly one allowlisted deterministic node from one "
                    "exact focused top-level K2 event graph"
                ),
                "explicitOptIn": {
                    "serverFlag": "--enable-experimental-graphpatch-edit",
                    "projectConfig": "[UEAgentKit] ExperimentalGraphPatchEdit=True",
                    "requiresRunningEditorCapability": True,
                },
                "requiresCompletePreviewResult": True,
                "acceptsCallerBuiltRequest": False,
                "maxNodesPerPlan": 1,
                "maxIncidentEdges": 1,
                "removableNodeClasses": [
                    "/Script/BlueprintGraph.K2Node_CallFunction",
                    "/Script/BlueprintGraph.K2Node_IfThenElse",
                ],
                "removableFunctions": [
                    "Add_IntInt",
                    "BooleanAND",
                    "BooleanOR",
                    "EqualEqual_BoolBool",
                    "Greater_IntInt",
                    "Not_PreBool",
                ],
                "rejectedNodeClasses": [
                    "EdGraphNode_Comment",
                    "K2Node_Event",
                    "K2Node_CustomEvent",
                    "K2Node_Timeline",
                    "K2Node_Knot",
                    "K2Node_Tunnel",
                    "K2Node_MacroInstance",
                    "K2Node_VariableGet",
                    "K2Node_VariableSet",
                    "K2Node_ExecutionSequence",
                    "unknown custom classes",
                ],
                "supportedPinKinds": ["exec", "bool", "int", "double", "string"],
                "rejectedPinTraits": [
                    "wildcard",
                    "delegate",
                    "container",
                    "by-reference",
                    "const",
                    "weak-pointer",
                    "struct-or-object",
                    "hidden",
                    "orphaned",
                    "split",
                ],
                "singleNodeOnly": True,
                "explicitRemovedEdgesRequired": True,
                "bindsExactNodeClass": True,
                "bindsExactNodeAndPinGuids": True,
                "bindsExactEdgeSetHash": True,
                "bindsNodePinIdentityHashes": True,
                "untouchedEdgesPreserved": True,
                "neverBreakAllPinLinks": True,
                "refusesUnplannedLinks": True,
                "explicitApplyConfirmation": "REMOVE EXPERIMENTAL BLUEPRINT NODE <planId>",
                "explicitSaveConfirmation": "SAVE EXPERIMENTAL BLUEPRINT NODE REMOVE <planId>",
                "saveRequiresPostApplyHashesRevisionAndCounts": True,
                "undoRedoVerifiedInCall": True,
                "undoRestoresNodeAndLinks": True,
                "singleTransaction": True,
                "autoSave": False,
                "compiled": False,
                "skeletonRegenerated": False,
                "requiresExplicitSeparateSave": True,
                "maxRequestBytes": 61440,
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
            "retargetPostprocess": {
                "available": bool(live_editor_enabled and write_tools_enabled),
                "startTool": "ue_start_animation_retarget_postprocess" if live_editor_enabled and write_tools_enabled else "",
                "statusTool": "ue_get_animation_retarget_postprocess" if live_editor_enabled and write_tools_enabled else "",
                "planTool": "ue_plan_animation_retarget_postprocess" if live_editor_enabled and write_tools_enabled else "",
                "reopenTool": "ue_reopen_animation_retarget_postprocess" if live_editor_enabled and write_tools_enabled else "",
                "source": "exact-completed-retarget-batch-outputs",
                "acceptedRetargetStates": ["completed", "saved"],
                "animSequenceAuditTool": "ue_diagnose_animation_scale",
                "assetWrites": False,
                "workRootPlanOnly": True,
                "autoApplyAllowed": False,
                "requiresUserReview": True,
                "scaleFixCandidateClassifications": ["root-lock-candidate", "root-track-candidate"],
                "referenceOutputTypes": ["BlendSpace", "AimOffset", "AnimMontage"],
                "referenceAssetMutationImplemented": False,
                "requiresRetargetOutputIndexRefreshBeforeP2Plan": True,
                "indexRefreshTool": "ue_refresh_animation_retarget_postprocess_index" if live_editor_enabled and write_tools_enabled else "",
                "indexRefreshModes": ["Preview", "Apply"],
                "indexRefreshRequiresSavedAndVerified": True,
                "indexRefreshEligibleClassifications": ["root-lock-candidate", "root-track-candidate"],
                "indexRefreshAtomicPairedGeneration": True,
                "indexRefreshApplyRequiresRestart": True,
                "p2Workflow": "animation-scale-fix-batch",
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
                    "ue_verify_live_write_fast",
                    "ue_verify_live_write_checkpoint",
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
            "l0EventCount": memory_status.l0_event_count if memory_status is not None else 0,
            "pendingL0EventCount": (
                memory_status.pending_l0_event_count if memory_status is not None else 0
            ),
            "evidenceChainCount": (
                memory_status.evidence_chain_count if memory_status is not None else 0
            ),
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
            "saveTool": "ue_save_animation_scale_fix_batch" if write_tools_enabled else "",
            "verifyTool": "ue_verify_animation_scale_fix_batch" if write_tools_enabled else "",
            "indexRefreshTool": "ue_refresh_animation_scale_fix_batch_index" if write_tools_enabled else "",
            "rollbackTool": "ue_rollback_animation_scale_fix_batch" if write_tools_enabled else "",
            "undoTool": "ue_undo_animation_scale_fix_batch" if write_tools_enabled else "",
            "sourceReportTool": "ue_export_animation_scale_audit_report",
            "maxAssets": MAX_BATCH_SCALE_FIX_ASSETS,
            "maxSessionPlans": MAX_BATCH_SCALE_FIX_PLANS,
            "maxLiveStep": MAX_BATCH_LIVE_STEP,
            "maxSaveStep": MAX_BATCH_SAVE_STEP,
            "maxVerifyStep": MAX_BATCH_VERIFY_STEP,
            "maxIndexRefreshStep": MAX_BATCH_INDEX_REFRESH_STEP,
            "maxRollbackStep": MAX_BATCH_ROLLBACK_STEP,
            "supportedClassifications": sorted(SUPPORTED_BATCH_CLASSIFICATIONS),
            "expectedFinalScaleDefault": "audit-root-skeleton-reference-component-scale",
            "explicitFinalScaleOverrides": True,
            "duplicateAssetsRejected": True,
            "unsupportedSelectedClassificationRejected": True,
            "childPlansUseSingleAssetPolicyAndRevisionValidation": True,
            "liveApplyAvailable": bool(write_tools_enabled and live_editor_enabled and commit_enabled),
            "saveAvailable": bool(write_tools_enabled and live_editor_enabled and commit_enabled),
            "verifyAvailable": bool(write_tools_enabled and live_editor_enabled and commit_enabled),
            "indexRefreshAvailable": bool(write_tools_enabled and live_editor_enabled and commit_enabled),
            "undoAvailable": bool(write_tools_enabled and live_editor_enabled and commit_enabled),
            "liveApplyConfirmation": "LIVE APPLY BATCH <batchPlanId>",
            "saveConfirmation": "SAVE BATCH <batchPlanId>",
            "indexRefreshConfirmation": "REFRESH BATCH <batchPlanId>",
            "undoConfirmation": "UNDO BATCH <batchPlanId>",
            "persistedRollbackAvailable": bool(write_tools_enabled and commit_enabled),
            "rollbackConfirmation": "ROLLBACK BATCH <batchPlanId>",
            "rollbackDryRunWritesDisk": False,
            "rollbackCommitRequiresEditorClosed": True,
            "indexRefreshPreviewMaxAssets": MAX_BATCH_INDEX_REFRESH_STEP,
            "indexRefreshAtomicPairedGeneration": True,
            "indexRefreshApplyRequiresRestart": True,
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
        "taskContext": {
            "available": True,
            "tool": "ue_get_task_context",
            "schemaVersion": TASK_CONTEXT_SCHEMA_VERSION,
            "readOnly": True,
            "queryRequired": True,
            "maxAssets": MAX_TASK_CONTEXT_ASSETS,
            "sources": [
                "immutable-sqlite-index",
                "sqlite-revision-export-disk-sha256",
                "project-memory",
                "project-memory-active-work",
                "live-editor-memory",
                "change-set-journal",
            ],
            "sourceAvailability": {
                "index": True,
                "revisionFreshness": write_tools_enabled,
                "memory": memory_enabled,
                "liveEditor": live_editor_enabled,
                "changeSet": write_tools_enabled,
            },
            "unavailableSectionsDegrade": True,
            "risksAreDeterministicOnly": True,
            "modelInference": False,
            "autoRelevantAssetExpansion": True,
            "relevantAssets": {
                "available": True,
                "deterministic": True,
                "source": "immutable-sqlite-index",
                "maxAssets": MAX_TASK_CONTEXT_CANDIDATES,
                "maxSearchTerms": MAX_CANDIDATE_SEARCH_TERMS,
                "symbolSupplement": True,
                "codeProfileSupplement": True,
                "profiles": ["default-non-code", "code"],
                "targetPathKinds": [
                    "game-object-path",
                    "project-relative-indexed-path",
                ],
                "modelInference": False,
            },
            "crossSourceCorrelation": {
                "available": True,
                "deterministic": True,
                "modelInference": False,
                "readOnly": True,
                "persistent": False,
                "sources": [
                    "project-memory-active-work",
                    "change-set-journal",
                    "live-editor-memory",
                    "project-memory",
                ],
                "maxLinks": MAX_CORRELATION_LINKS,
                "changeSetExplicitOnly": True,
                "changeSetAutoDiscovery": False,
            },
            "outputBudgetBounded": True,
            "evidenceOnDemand": True,
        },
        "impactAnalysis": {
            "available": True,
            "tool": "ue_analyze_change_impact",
            "readOnly": True,
            "deterministic": True,
            "modelInference": False,
            "direction": "consumer-to-target",
            "method": "reverse-reference-bfs-exact-key",
            "source": "immutable-sqlite-index",
            "maxTargets": MAX_IMPACT_TARGETS,
            "maxDepth": MAX_IMPACT_DEPTH,
            "defaultDepth": 2,
            "supportsIndirect": True,
            "supportsValidationTargets": True,
            "supportsRuntimeSensitivityClassification": False,
            "runtimeSensitivityState": "not-proven-with-current-evidence",
            "maxConsumers": MAX_IMPACT_CONSUMERS,
            "maxEdges": MAX_IMPACT_EDGES,
            "maxPaths": MAX_IMPACT_PATHS,
            "subjectKinds": list(SUPPORTED_SUBJECT_KINDS),
            "unsupportedSubjectKinds": list(UNSUPPORTED_SUBJECT_KINDS),
            "highFanoutThreshold": 15,
        },
        "semanticDiff": {
            "available": True,
            "workflowEvidenceAvailable": write_tools_enabled,
            "tool": "ue_analyze_semantic_diff",
            "schemaVersion": SEMANTIC_DIFF_SCHEMA_VERSION,
            "readOnly": True,
            "deterministic": True,
            "modelInference": False,
            "changeSetExplicitOnly": True,
            "changeSetAutoDiscovery": False,
            "stages": ["auto", "live", "persisted", "verified"],
            "verifiedStageIsTrustVerdict": False,
            "domains": {
                "blueprintNarrowWrite": ["property", "component", "pin-default"],
                "dataAssetProperty": ["scalar", "object-class-ref", "soft-ref", "struct", "array", "set", "map"],
                "dataTable": ["cell", "row-fields", "row-add", "row-remove", "row-rename"],
                "materialInstance": ["scalar", "vector", "texture", "static-switch"],
            },
            "maxAssets": MAX_SEMANTIC_DIFF_ASSETS,
            "maxChanges": MAX_SEMANTIC_DIFF_CHANGES,
            "maxUnchangedCriticalFields": MAX_SEMANTIC_DIFF_UNCHANGED,
            "maxAnalysisGaps": MAX_SEMANTIC_DIFF_GAPS,
        },
        "verificationTrust": {
            "available": True,
            "workflowEvidenceAvailable": write_tools_enabled,
            "schemaVersion": VERIFICATION_TRUST_SCHEMA_VERSION,
            "ruleVersion": VERIFICATION_RULE_VERSION,
            "readOnly": True,
            "deterministic": True,
            "modelInference": False,
            "changeSetExplicitOnly": True,
            "changeSetAutoDiscovery": False,
            "planTool": "ue_build_verification_plan",
            "verdictTool": "ue_evaluate_trust_verdict",
            "verdictStates": list(TRUST_VERDICT_STATES),
            "assertionStatuses": list(ASSERTION_STATUSES),
            "assertionRequirements": list(ASSERTION_REQUIREMENTS),
            "assertionFamilies": list(ASSERTION_FAMILIES),
            "autoExecutesValidation": False,
            "autoExecutesCompile": False,
            "autoExecutesAutomation": False,
            "autoExecutesSaveOrVerify": False,
            "verifiedMeansUniversalCorrectness": False,
            "evidenceCapture": {
                **verification_evidence_status,
                "available": bool(write_tools_enabled and live_editor_enabled),
                "restartBehavior": "session-local-evidence-cleared-rerun-registered-tools",
            },
            "indexRefreshTiming": "after-scoped-trust-verdict",
            "maxAssets": MAX_VERIFICATION_ASSETS,
            "maxAssertions": MAX_VERIFICATION_ASSERTIONS,
            "maxEvidenceRefs": MAX_VERIFICATION_EVIDENCE_REFS,
            "maxAutomationTests": MAX_REQUIRED_AUTOMATION_TESTS,
            "maxExtraValidationAssets": MAX_EXTRA_VALIDATION_ASSETS,
            "maxImpactDepth": 2,
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
            "taskContextMaxAssets": MAX_TASK_CONTEXT_ASSETS,
            "impactTargets": MAX_IMPACT_TARGETS,
            "impactDepth": MAX_IMPACT_DEPTH,
            "impactConsumers": MAX_IMPACT_CONSUMERS,
            "impactEdges": MAX_IMPACT_EDGES,
            "impactPaths": MAX_IMPACT_PATHS,
            "semanticDiffAssets": MAX_SEMANTIC_DIFF_ASSETS,
            "semanticDiffChanges": MAX_SEMANTIC_DIFF_CHANGES,
            "semanticDiffUnchangedCriticalFields": MAX_SEMANTIC_DIFF_UNCHANGED,
            "semanticDiffAnalysisGaps": MAX_SEMANTIC_DIFF_GAPS,
            "verificationAssets": MAX_VERIFICATION_ASSETS,
            "verificationAssertions": MAX_VERIFICATION_ASSERTIONS,
            "verificationEvidenceRefs": MAX_VERIFICATION_EVIDENCE_REFS,
            "verificationAutomationTests": MAX_REQUIRED_AUTOMATION_TESTS,
            "verificationExtraValidationAssets": MAX_EXTRA_VALIDATION_ASSETS,
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
            "indexSnapshotStates": ["fresh", "stale", "unknown"],
            "indexSnapshotFields": [
                "indexSnapshotState",
                "exportSnapshotState",
                "indexSnapshotReasons",
                "lastExportUtc",
                "databasePath",
                "manifestMatches",
            ],
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
    snapshot = apply_editor_freshness(
        dict(index_status.get("indexSnapshot", {})),
        freshness_status,
    )
    index_status["indexSnapshot"] = snapshot
    index_metadata = dict(index_metadata)
    index_metadata.update(
        {
            "indexSnapshotState": snapshot.get("state", "unknown"),
            "indexSnapshotReasons": snapshot.get("reasons", []),
            "exportSnapshotState": snapshot.get("exportSnapshotState", "unknown"),
            "editorStateCompared": snapshot.get("editorStateCompared", False),
            "lastExportUtc": snapshot.get("lastExportUtc", ""),
            "databasePath": snapshot.get("databasePath", ""),
            "lastDatabasePath": snapshot.get("lastDatabasePath", ""),
            "databasePathMatches": snapshot.get("databasePathMatches"),
            "manifestMatches": snapshot.get("manifestMatches"),
        }
    )
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
        "indexSnapshot": snapshot,
        "database": {
            "configured": True,
            "state": "available",
            "schemaVersion": index_status.get("databaseSchemaVersion"),
            "immutable": bool(index_metadata.get("immutable")),
            "quiescent": bool(index_metadata.get("quiescent")),
            "lastIndexedAtUtc": index_metadata.get("lastIndexedAtUtc", ""),
            "lastExportUtc": index_metadata.get("lastExportUtc", ""),
            "databasePath": index_metadata.get("databasePath", ""),
            "lastDatabasePath": index_metadata.get("lastDatabasePath", ""),
            "databasePathMatches": index_metadata.get("databasePathMatches"),
            "indexSnapshotState": index_metadata.get("indexSnapshotState", "unknown"),
            "indexSnapshotReasons": index_metadata.get("indexSnapshotReasons", []),
            "exportSnapshotState": index_metadata.get("exportSnapshotState", "unknown"),
            "editorStateCompared": index_metadata.get("editorStateCompared", False),
            "manifestMatches": index_metadata.get("manifestMatches"),
            "manifestSchemaVersion": index_metadata.get("manifestSchemaVersion", ""),
            "exporterVersion": index_metadata.get("exporterVersion", ""),
            "profile": index_metadata.get("profile", ""),
            "stats": index_status.get("stats", {}),
        },
        "codeKnowledge": index_status.get(
            "codeKnowledge",
            {
                "available": False,
                "profile": "code",
                "assetClass": "CppSourceFile",
                "assets": 0,
                "types": 0,
                "functions": 0,
                "properties": 0,
                "references": {"include": 0, "inherits": 0, "implements": 0},
                "lastCodeIndexedAtUtc": "",
                "reflection": {
                    "imported": False,
                    "lastIndexedAtUtc": "",
                    "project": "",
                    "schemaVersion": "",
                    "exporterVersion": "",
                    "matchedTypes": 0,
                    "unmatchedTypes": 0,
                },
            },
        ),
        "projectMemory": {
            "configured": memory_status is not None,
            "state": "available" if memory_status is not None else "unavailable",
            "projectKey": memory_status.project_key if memory_status is not None else "",
            "schemaVersion": memory_status.schema_version if memory_status is not None else None,
            "recordCount": memory_status.record_count if memory_status is not None else 0,
            "nodeCount": memory_status.node_count if memory_status is not None else 0,
            "activeWorkCount": memory_status.active_work_count if memory_status is not None else 0,
            "l0EventCount": memory_status.l0_event_count if memory_status is not None else 0,
            "pendingL0EventCount": (
                memory_status.pending_l0_event_count if memory_status is not None else 0
            ),
            "evidenceChainCount": (
                memory_status.evidence_chain_count if memory_status is not None else 0
            ),
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
        "taskContext": {
            "available": True,
            "tool": "ue_get_task_context",
            "revisionFreshness": write_tools_enabled,
            "memory": memory_service is not None,
            "liveEditor": live_editor_service is not None,
            "changeSet": write_tools_enabled,
            "relevantAssets": {
                "maxAssets": MAX_TASK_CONTEXT_CANDIDATES,
                "deterministic": True,
                "codeProfileSupplement": True,
                "targetPathKinds": [
                    "game-object-path",
                    "project-relative-indexed-path",
                ],
            },
            "crossSourceCorrelation": {
                "available": True,
                "maxLinks": MAX_CORRELATION_LINKS,
                "changeSetExplicitOnly": True,
            },
        },
        "impactAnalysis": {
            "available": True,
            "tool": "ue_analyze_change_impact",
            "readOnly": True,
            "deterministic": True,
            "modelInference": False,
            "method": "reverse-reference-bfs-exact-key",
            "maxTargets": MAX_IMPACT_TARGETS,
            "maxDepth": MAX_IMPACT_DEPTH,
            "defaultDepth": 2,
            "maxConsumers": MAX_IMPACT_CONSUMERS,
            "maxEdges": MAX_IMPACT_EDGES,
            "maxPaths": MAX_IMPACT_PATHS,
            "supportsIndirect": True,
            "supportsValidationTargets": True,
            "supportsRuntimeSensitivityClassification": False,
            "runtimeSensitivityState": "not-proven-with-current-evidence",
        },
        "semanticDiff": {
            "available": True,
            "workflowEvidenceAvailable": write_tools_enabled,
            "tool": "ue_analyze_semantic_diff",
            "schemaVersion": SEMANTIC_DIFF_SCHEMA_VERSION,
            "readOnly": True,
            "deterministic": True,
            "modelInference": False,
            "changeSetExplicitOnly": True,
            "changeSetAutoDiscovery": False,
            "stages": ["auto", "live", "persisted", "verified"],
        },
        "verificationTrust": {
            "available": True,
            "workflowEvidenceAvailable": write_tools_enabled,
            "schemaVersion": VERIFICATION_TRUST_SCHEMA_VERSION,
            "ruleVersion": VERIFICATION_RULE_VERSION,
            "planTool": "ue_build_verification_plan",
            "verdictTool": "ue_evaluate_trust_verdict",
            "readOnly": True,
            "deterministic": True,
            "modelInference": False,
            "changeSetExplicitOnly": True,
            "changeSetAutoDiscovery": False,
            "autoExecutesLiveActions": False,
            "verifiedMeansUniversalCorrectness": False,
            "evidenceCapture": (
                workflow_service.verification_evidence_store.status()
                if workflow_service is not None
                else {
                    "persistent": False,
                    "arbitraryIngest": False,
                    "projectBound": True,
                    "bounded": True,
                    "recordCount": 0,
                    "maxRecords": 256,
                    "capturedTools": [],
                }
            ),
        },
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

# Transport failures that can happen *after* a mutating request was written to the
# Editor Bridge socket.  For a destructive Tool the Editor may already have applied
# the change, so the outcome is indeterminate and an automatic retry can duplicate
# or mask a write.  Read-only and non-destructive Tools keep the plain retryable
# behaviour: repeating ue_open_asset / ue_validate_asset is harmless.
_INDETERMINATE_WRITE_ERROR_CODES = frozenset(
    {
        "live-editor-timeout",
        "live-editor-connection-closed",
    }
)

_INDETERMINATE_WRITE_SUGGESTION = (
    "Do not repeat this write. The Editor may have applied it before the transport failed: "
    "read the current asset state (ue_get_dirty_assets, ue_get_asset_state, "
    "ue_verify_live_write_fast) and undo or discard the transaction before retrying."
)


def _is_destructive_tool(tool: str) -> bool:
    definition = TOOL_DEFINITIONS_BY_NAME.get(tool)
    return bool(definition is not None and definition.destructive)


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
        "unsupported-impact-subject": "Use subject_kind asset-level, blueprint-symbol, or code-symbol, or repeat the analysis without a structured subject.",
        "impact-subject-not-found": "Pass the exact symbol stable ID as reported by ue_search or ue_get_asset symbols.",
        "impact-subject-asset-mismatch": "Use the exact indexed asset/source path that owns the structured subject.",
        "impact-subject-kind-mismatch": "Use a symbol stable ID whose owner profile matches the requested subject_kind.",
        "insufficient-evidence": "Use an explicit Change Set with bound operations and retain its fixed Workflow evidence artifacts.",
        "semantic-diff-stage-unavailable": "Request one of the availableStages reported in details, or complete save and independent verify first.",
        "semantic-diff-evidence-stale": "Refresh or independently verify the exact affected assets before re-running Semantic Diff.",
        "verification-trust-invalid-arguments": "Use the strict R3 request schema with one explicit Change Set and bounded exact inputs.",
        "verification-plan-empty-change-set": "Bind at least one controlled operation to the explicit Change Set before building its Verification Plan.",
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
    elif isinstance(error, ImpactAnalysisError):
        code = error.code
    elif isinstance(error, SemanticDiffEvidenceError):
        code = error.code
        details = error.details
    elif isinstance(error, VerificationTrustError):
        code = error.code
    elif isinstance(error, GraphEditError):
        code = error.code
    elif isinstance(error, ValueError):
        code = "invalid-arguments"
    elif isinstance(error, sqlite3.Error):
        code = "database-error"
    else:
        code = "ue-agent-kit-error"
    indeterminate = _is_destructive_tool(tool) and code in _INDETERMINATE_WRITE_ERROR_CODES
    if indeterminate:
        details = dict(details) if isinstance(details, dict) else {}
        details.setdefault("indeterminate", True)
        details.setdefault("dispatchState", "unknown")
        suggested_action = _INDETERMINATE_WRITE_SUGGESTION
    else:
        suggested_action = _suggested_action(code)
    response: dict[str, Any] = {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {
            "code": code,
            "type": type(error).__name__,
            "message": message,
            "retryable": code in _RETRYABLE_ERROR_CODES and not indeterminate,
            "details": details,
            "suggestedAction": suggested_action,
        },
    }
    return response


def _pure_live_server_instructions() -> str:
    return (
        "Pure-live mode: this server has no SQLite index and exposes only bounded, read-only "
        "Live Editor tools. Use ue_get_capabilities for the exact exposed tool list. Offline "
        "search/reference, Project Memory, workflow, write, source-control, and graph-edit tools "
        "are intentionally absent. Every fact comes from the fixed local Unreal Editor Bridge; "
        "when the Editor is unavailable the read tools return a clear error instead of cached or "
        "offline data."
    )


def _create_pure_live_mcp_server(
    *,
    database_path: Path | None,
    workflow_config: PatchWorkflowConfig | None,
    workflow_service: PatchWorkflowService | None,
    live_editor_service: LiveEditorBridgeService | None,
    memory_service: ProjectMemoryService | None,
    source_control_service: P4SourceControlService | None,
    p7_test_only_graph_layout_apply: bool,
    p8_test_only_graph_layout_apply: bool,
    experimental_graph_layout_apply: bool,
    experimental_graphpatch_edit: bool,
):
    """Bounded, explicitly opt-in pure-live server (no index, no SQLite)."""
    if database_path is not None:
        raise ValueError("--pure-live does not accept --database; no index is opened in this mode.")
    if live_editor_service is None:
        raise ValueError("--pure-live requires --enable-live-editor and a fixed --project.")
    if workflow_config is not None or workflow_service is not None:
        raise ValueError("--pure-live does not support write/workflow tools.")
    if memory_service is not None:
        raise ValueError("--pure-live does not support Project Memory.")
    if source_control_service is not None:
        raise ValueError("--pure-live does not support source-control tools.")
    if (
        p7_test_only_graph_layout_apply
        or p8_test_only_graph_layout_apply
        or experimental_graph_layout_apply
        or experimental_graphpatch_edit
    ):
        raise ValueError("--pure-live does not support experimental graph tools.")

    server = FastMCP(
        MCP_SERVER_NAME,
        instructions=_pure_live_server_instructions(),
        json_response=True,
    )
    read_annotations = ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )
    register_pure_live_meta_tools(
        server=server,
        live_editor_service=live_editor_service,
        server_name=MCP_SERVER_NAME,
        server_version=__version__,
        read_annotations=read_annotations,
        exposed_tools=PURE_LIVE_READ_TOOL_NAMES,
    )
    register_live_read_tools(
        server=server,
        live_editor_service=live_editor_service,
        index_service=None,
        audit_report_root=None,
        read_annotations=read_annotations,
        tool_annotations_type=ToolAnnotations,
        error_response=_error_response,
        pure_live=True,
    )
    _enforce_strict_tool_arguments(server, (GRAPH_CONTEXT_TOOL_NAME,))
    return server


def create_mcp_server(
    database_path: Path | None,
    *,
    workflow_config: PatchWorkflowConfig | None = None,
    workflow_service: PatchWorkflowService | None = None,
    live_editor_service: LiveEditorBridgeService | None = None,
    memory_service: ProjectMemoryService | None = None,
    source_control_service: P4SourceControlService | None = None,
    audit_report_root: Path | None = None,
    p7_test_only_graph_layout_apply: bool = False,
    p8_test_only_graph_layout_apply: bool = False,
    experimental_graph_layout_apply: bool = False,
    experimental_graphpatch_edit: bool = False,
    pure_live: bool = False,
):
    if FastMCP is None or ToolAnnotations is None:
        raise RuntimeError(
            "MCP support is not installed. Run scripts\\setup_python.cmd -WithMcp "
            "or install the mcp optional dependency."
        ) from _MCP_IMPORT_ERROR

    if pure_live:
        return _create_pure_live_mcp_server(
            database_path=database_path,
            workflow_config=workflow_config,
            workflow_service=workflow_service,
            live_editor_service=live_editor_service,
            memory_service=memory_service,
            source_control_service=source_control_service,
            p7_test_only_graph_layout_apply=p7_test_only_graph_layout_apply,
            p8_test_only_graph_layout_apply=p8_test_only_graph_layout_apply,
            experimental_graph_layout_apply=experimental_graph_layout_apply,
            experimental_graphpatch_edit=experimental_graphpatch_edit,
        )
    if database_path is None:
        raise ValueError("A fixed index database path is required unless --pure-live is set.")

    index_service = IndexQueryService(database_path)
    index_status = index_service.check()
    if memory_service is not None and memory_service.project_key != str(index_status.get("projectKey", "")):
        raise ValueError("Project Memory and index services must use the same fixed project")
    if workflow_service is not None and workflow_config is not None:
        raise ValueError("Provide workflow_config or workflow_service, not both.")
    if workflow_service is None and workflow_config is not None:
        workflow_service = PatchWorkflowService(index_service, workflow_config)
    if workflow_service is not None and memory_service is not None:
        bind_capture = getattr(
            workflow_service,
            "bind_memory_l0_capture_service",
            None,
        )
        work_root = getattr(workflow_service.config, "work_root", None)
        if callable(bind_capture) and isinstance(work_root, Path):
            capture_service = memory_service.l0_capture_service(
                artifact_root=work_root
            )
            bind_capture(capture_service)
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
    if p7_test_only_graph_layout_apply:
        if live_editor_service is None:
            raise ValueError("--enable-p7-test-only-graph-layout-apply requires --enable-live-editor")
        if not is_fixed_project(live_editor_service.config.project_path):
            raise ValueError(
                "P7 test-only graph layout is restricted to the exact disposable P7AutomationHost project"
            )
    if p8_test_only_graph_layout_apply:
        if live_editor_service is None:
            raise ValueError("--enable-p8-test-only-graph-layout-apply requires --enable-live-editor")
        if not is_fixed_p8_project(live_editor_service.config.project_path):
            raise ValueError(
                "P8 test-only graph layout is restricted to the exact disposable P8AutomationHost project"
            )
    if experimental_graph_layout_apply and live_editor_service is None:
        raise ValueError("--enable-experimental-graph-layout-apply requires --enable-live-editor")
    if experimental_graphpatch_edit and live_editor_service is None:
        raise ValueError("--enable-experimental-graphpatch-edit requires --enable-live-editor")
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
            source_control_service,
            p7_test_only_graph_layout_apply=p7_test_only_graph_layout_apply,
            p8_test_only_graph_layout_apply=p8_test_only_graph_layout_apply,
            experimental_graph_layout_apply=experimental_graph_layout_apply,
            experimental_graphpatch_edit=experimental_graphpatch_edit,
            live_editor_status=(
                live.status()
                if (experimental_graph_layout_apply or experimental_graphpatch_edit)
                and live is not None
                else None
            ),
        ),
        project_status_response=lambda index, workflow, live: _project_status_response(
            index,
            workflow,
            live,
            memory_service,
        ),
    )
    register_graph_patch_tools(server=server, read_annotations=read_annotations)
    _enforce_strict_tool_arguments(server, (GRAPH_PATCH_PREVIEW_TOOL_NAME,))
    _enforce_strict_tool_arguments(server, STRICT_IMPACT_ARGUMENT_TOOL_NAMES)
    _enforce_strict_tool_arguments(server, STRICT_SEMANTIC_DIFF_ARGUMENT_TOOL_NAMES)
    _enforce_strict_tool_arguments(server, STRICT_VERIFICATION_TRUST_ARGUMENT_TOOL_NAMES)
    register_task_context_tools(
        server=server,
        task_context_service=TaskContextService(
            index_service=index_service,
            memory_service=memory_service,
            live_editor_service=live_editor_service,
            workflow_service=workflow_service,
            freshness_tracker=(
                getattr(workflow_service, "freshness", None) if workflow_service is not None else None
            ),
        ),
        read_annotations=read_annotations,
        error_response=_error_response,
    )
    _enforce_strict_tool_arguments(server, STRICT_TASK_CONTEXT_ARGUMENT_TOOL_NAMES)
    if source_control_service is not None:
        register_source_control_tools(
            server=server,
            source_control_service=source_control_service,
            read_annotations=read_annotations,
            tool_annotations_type=ToolAnnotations,
            error_response=_error_response,
        )
        _enforce_strict_tool_arguments(server, STRICT_SOURCE_CONTROL_ARGUMENT_TOOL_NAMES)
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
        _enforce_strict_tool_arguments(server, (GRAPH_CONTEXT_TOOL_NAME,))
        register_live_action_tools(
            server=server,
            live_editor_service=live_editor_service,
            verification_evidence_store=(
                workflow_service.verification_evidence_store
                if workflow_service is not None
                else None
            ),
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
        if p7_test_only_graph_layout_apply:
            register_graph_layout_test_only_tool(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
        if p8_test_only_graph_layout_apply:
            register_graph_layout_p8_test_only_tool(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
        if experimental_graph_layout_apply:
            register_graph_layout_experimental_tools(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
            _enforce_strict_tool_arguments(
                server,
                (EXPERIMENTAL_APPLY_TOOL_NAME, EXPERIMENTAL_SAVE_TOOL_NAME),
            )
        if experimental_graphpatch_edit:
            register_focus_blueprint_graph_tool(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
            _enforce_strict_tool_arguments(server, ("ue_focus_blueprint_graph",))
            register_graph_edit_experimental_tools(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
            _enforce_strict_tool_arguments(
                server,
                (
                    EXPERIMENTAL_GRAPH_EDIT_PREVIEW_TOOL_NAME,
                    EXPERIMENTAL_GRAPH_EDIT_APPLY_TOOL_NAME,
                    EXPERIMENTAL_GRAPH_EDIT_SAVE_TOOL_NAME,
                ),
            )
            register_pin_default_edit_experimental_tools(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
            _enforce_strict_tool_arguments(
                server,
                (
                    EXPERIMENTAL_PIN_DEFAULT_PREVIEW_TOOL_NAME,
                    EXPERIMENTAL_PIN_DEFAULT_APPLY_TOOL_NAME,
                    EXPERIMENTAL_PIN_DEFAULT_SAVE_TOOL_NAME,
                ),
            )
            register_variable_write_experimental_tools(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
            _enforce_strict_tool_arguments(
                server,
                (
                    EXPERIMENTAL_VARIABLE_PREVIEW_TOOL_NAME,
                    EXPERIMENTAL_VARIABLE_APPLY_TOOL_NAME,
                    EXPERIMENTAL_VARIABLE_SAVE_TOOL_NAME,
                ),
            )
            register_rewire_write_experimental_tools(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
            _enforce_strict_tool_arguments(
                server,
                (
                    EXPERIMENTAL_REWIRE_PREVIEW_TOOL_NAME,
                    EXPERIMENTAL_REWIRE_APPLY_TOOL_NAME,
                    EXPERIMENTAL_REWIRE_SAVE_TOOL_NAME,
                ),
            )
            register_node_remove_write_experimental_tools(
                server=server,
                live_editor_service=live_editor_service,
                tool_annotations_type=ToolAnnotations,
                error_response=_error_response,
            )
            _enforce_strict_tool_arguments(
                server,
                (
                    EXPERIMENTAL_NODE_REMOVE_PREVIEW_TOOL_NAME,
                    EXPERIMENTAL_NODE_REMOVE_APPLY_TOOL_NAME,
                    EXPERIMENTAL_NODE_REMOVE_SAVE_TOOL_NAME,
                ),
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
        default=None,
        help=(
            "Immutable read-only SQLite index path. Required unless --pure-live is set. "
            f"Default in index mode: {DEFAULT_DATABASE}"
        ),
    )
    parser.add_argument(
        "--pure-live",
        action="store_true",
        help=(
            "Explicit opt-in pure-live mode: no SQLite index is opened or created and only a "
            "bounded read-only Live Editor tool set is registered. Requires --enable-live-editor "
            "and --project."
        ),
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
    parser.add_argument(
        "--enable-p7-test-only-graph-layout-apply",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--enable-p8-test-only-graph-layout-apply",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--enable-experimental-graph-layout-apply",
        action="store_true",
        help=(
            "Opt in to the general experimental Blueprint graph-layout Apply/Save tools. "
            "The running Editor project must also set "
            "[UEAgentKit] ExperimentalGraphLayoutApply=True."
        ),
    )
    parser.add_argument(
        "--enable-experimental-graphpatch-edit",
        action="store_true",
        help=(
            "Opt in to the bounded experimental Blueprint createNode/connectPins tools. "
            "The running Editor project must also set "
            "[UEAgentKit] ExperimentalGraphPatchEdit=True."
        ),
    )
    parser.add_argument("--enable-source-control", action="store_true")
    parser.add_argument("--engine-root", type=Path)
    parser.add_argument("--project", dest="project_path", type=Path)
    parser.add_argument("--policy", dest="policy_path", type=Path)
    parser.add_argument(
        "--policy-profile",
        default=None,
        help=(
            "Project-level Policy profile to resolve from --project when --policy "
            "is omitted (e.g. 'animation-write'). Ignored when --policy is explicit."
        ),
    )
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
    if getattr(args, "enable_p7_test_only_graph_layout_apply", False):
        if not is_fixed_project(args.project_path):
            raise ValueError(
                "P7 test-only graph layout is restricted to the exact disposable P7AutomationHost project"
            )
    if getattr(args, "enable_p8_test_only_graph_layout_apply", False):
        if not is_fixed_p8_project(args.project_path):
            raise ValueError(
                "P8 test-only graph layout is restricted to the exact disposable P8AutomationHost project"
            )
    return LiveEditorBridgeService(
        LiveEditorBridgeConfig(
            project_path=args.project_path,
            timeout_seconds=args.live_editor_timeout_seconds,
            policy_path=args.policy_path,
        ),
        server_version=__version__,
    )


def _resolve_policy_argument(args: argparse.Namespace) -> None:
    """Auto-resolve the project-level Policy when --policy was omitted.

    Resolution only applies to the write-tools path (Policy is irrelevant in
    read-only mode) and requires --project. An explicit --policy always wins.
    """
    if args.policy_path is not None:
        return
    if not args.enable_write_tools:
        return
    if args.project_path is None:
        return
    args.policy_path = resolve_project_policy(args.project_path, profile=args.policy_profile)


def _validate_pure_live_arguments(args: argparse.Namespace) -> None:
    """Reject every option that would need an index, workflow, memory, or writes."""
    if args.database is not None:
        raise ValueError("--pure-live does not accept --database; no index is opened in this mode.")
    if not args.enable_live_editor:
        raise ValueError("--pure-live requires --enable-live-editor.")
    if args.project_path is None:
        raise ValueError("--pure-live requires --project with an exact .uproject path.")
    rejected = {
        "--enable-write-tools": args.enable_write_tools,
        "--enable-commit-tools": args.enable_commit_tools,
        "--enable-project-memory": args.enable_project_memory,
        "--enable-source-control": args.enable_source_control,
        "--enable-p7-test-only-graph-layout-apply": getattr(
            args, "enable_p7_test_only_graph_layout_apply", False
        ),
        "--enable-p8-test-only-graph-layout-apply": getattr(
            args, "enable_p8_test_only_graph_layout_apply", False
        ),
        "--enable-experimental-graph-layout-apply": getattr(
            args, "enable_experimental_graph_layout_apply", False
        ),
        "--enable-experimental-graphpatch-edit": getattr(
            args, "enable_experimental_graphpatch_edit", False
        ),
    }
    enabled = [name for name, value in rejected.items() if value]
    if enabled:
        raise ValueError(
            "--pure-live does not support " + ", ".join(enabled) + "."
        )
    paths = {
        "--engine-root": args.engine_root,
        "--policy": args.policy_path,
        "--policy-profile": args.policy_profile,
        "--revision-export": args.revision_export,
        "--memory-database": args.memory_database,
    }
    configured = [name for name, value in paths.items() if value]
    if configured:
        raise ValueError(
            "--pure-live does not support " + ", ".join(configured) + "."
        )


def _run_pure_live(args: argparse.Namespace) -> int:
    """Explicit opt-in pure-live startup: no index, no SQLite, bounded read-only tools."""
    try:
        _validate_pure_live_arguments(args)
        live_editor_service = _build_live_editor_service(args)
        if live_editor_service is None:
            raise ValueError("--pure-live requires --enable-live-editor.")
        if args.check:
            payload: dict[str, Any] = {
                "schemaVersion": "1.0",
                "tool": "ue_agent_kit_mcp_status",
                "ok": True,
                "mode": "pure-live",
                "index": {
                    "configured": False,
                    "required": False,
                    "opened": False,
                    "state": "not-opened",
                },
                "writeToolsEnabled": False,
                "commitToolsEnabled": False,
                "liveEditorEnabled": True,
                "projectMemoryEnabled": False,
                "sourceControlEnabled": False,
                "liveEditor": live_editor_service.status(),
            }
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        server = create_mcp_server(
            None,
            live_editor_service=live_editor_service,
            pure_live=True,
        )
        server.run(transport="stdio")
        return 0
    except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(
            json.dumps(_error_response("ue_agent_kit_mcp", exc, read_only=False), ensure_ascii=False),
            file=sys.stderr,
        )
        return 2 if isinstance(exc, FileNotFoundError) else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "pure_live", False):
        return _run_pure_live(args)
    frozen_snapshot: FrozenSessionSnapshot | None = None
    try:
        _resolve_policy_argument(args)
        base_workflow_config = _build_workflow_config(args)
        live_editor_service = _build_live_editor_service(args)
        workflow_config: PatchWorkflowConfig | None = None
        database_path = args.database if args.database is not None else DEFAULT_DATABASE
        if base_workflow_config is not None:
            active_snapshot = resolve_active_snapshot(
                database_path,
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
        source_control_service: P4SourceControlService | None = None
        if args.enable_source_control:
            source_control_service = P4SourceControlService(
                project_root=args.project_path,
                audit_report_root=args.work_root,
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
                "sourceControlEnabled": source_control_service is not None,
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
                    "l0EventCount": memory_status.l0_event_count,
                    "pendingL0EventCount": memory_status.pending_l0_event_count,
                    "evidenceChainCount": memory_status.evidence_chain_count,
                    "countsByType": memory_status.counts_by_type,
                    "countsByStatus": memory_status.counts_by_status,
                }
            if source_control_service is not None:
                payload["sourceControl"] = source_control_service.provider_capabilities()
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 0
        server = create_mcp_server(
            database_path,
            workflow_service=workflow_service,
            live_editor_service=live_editor_service,
            memory_service=memory_service,
            source_control_service=source_control_service,
            audit_report_root=args.work_root,
            p7_test_only_graph_layout_apply=bool(
                getattr(args, "enable_p7_test_only_graph_layout_apply", False)
            ),
            p8_test_only_graph_layout_apply=bool(
                getattr(args, "enable_p8_test_only_graph_layout_apply", False)
            ),
            experimental_graph_layout_apply=bool(
                getattr(args, "enable_experimental_graph_layout_apply", False)
            ),
            experimental_graphpatch_edit=bool(
                getattr(args, "enable_experimental_graphpatch_edit", False)
            ),
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

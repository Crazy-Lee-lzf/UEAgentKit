from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolGroup = Literal["query", "memory", "live-read", "live-action", "realtime", "workflow", "source-control"]
AnnotationKind = Literal["read", "planning", "destructive"]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    group: ToolGroup
    annotation: AnnotationKind
    live_method: str = ""
    high_level_change: bool = False

    @property
    def read_only(self) -> bool:
        return self.annotation == "read"

    @property
    def destructive(self) -> bool:
        return self.annotation == "destructive"

    @property
    def idempotent(self) -> bool:
        return self.annotation == "read"


TOOL_REGISTRY: tuple[ToolDefinition, ...] = (
    ToolDefinition("ue_pie", "live-read", "read"),
    ToolDefinition("ue_get_capabilities", "query", "read"),
    ToolDefinition("ue_get_project_status", "query", "read"),
    ToolDefinition("ue_search", "query", "read"),
    ToolDefinition("ue_get_asset", "query", "read"),
    ToolDefinition("ue_get_subprocess_ledger", "query", "read"),
    ToolDefinition("ue_find_references", "query", "read"),
    ToolDefinition("ue_find_world_actors", "query", "read"),
    ToolDefinition("ue_find_world_partition_actors", "query", "read"),
    ToolDefinition("ue_get_world_summary", "query", "read"),
    ToolDefinition("ue_analyze_change_impact", "query", "read"),
    ToolDefinition("ue_analyze_semantic_diff", "query", "read"),
    ToolDefinition("ue_build_verification_plan", "query", "read"),
    ToolDefinition("ue_evaluate_trust_verdict", "query", "read"),
    ToolDefinition("ue_preview_graph_patch", "query", "read"),
    ToolDefinition("ue_get_task_context", "query", "read"),
    ToolDefinition("ue_memory", "memory", "read"),
    ToolDefinition("ue_memory_search", "memory", "read"),
    ToolDefinition("ue_memory_get", "memory", "read"),
    ToolDefinition("ue_memory_list_l0_events", "memory", "read"),
    ToolDefinition("ue_memory_get_l0_event", "memory", "read"),
    ToolDefinition("ue_memory_write", "memory", "planning"),
    ToolDefinition("ue_memory_add_rule", "memory", "planning"),
    ToolDefinition("ue_memory_record_finding", "memory", "planning"),
    ToolDefinition("ue_memory_record_task", "memory", "planning"),
    ToolDefinition("ue_memory_mark_superseded", "memory", "planning"),
    ToolDefinition("ue_memory_validate", "memory", "planning"),
    ToolDefinition("ue_memory_get_context", "memory", "read"),
    ToolDefinition("ue_memory_expand_node", "memory", "read"),
    ToolDefinition("ue_memory_get_evidence", "memory", "read"),
    ToolDefinition("ue_memory_update_knowledge", "memory", "planning"),
    ToolDefinition("ue_memory_update_work", "memory", "planning"),
    ToolDefinition("ue_editor_status", "live-read", "read", "editor.status"),
    ToolDefinition("ue_get_selection", "live-read", "read", "editor.getSelection"),
    ToolDefinition("ue_get_open_assets", "live-read", "read", "editor.getOpenAssets"),
    ToolDefinition("ue_get_dirty_assets", "live-read", "read", "editor.getDirtyAssets"),
    ToolDefinition("ue_get_current_level", "live-read", "read", "editor.getCurrentLevel"),
    ToolDefinition("ue_get_pie_state", "live-read", "read", "editor.getPieState"),
    ToolDefinition("ue_get_pie_world_contexts", "live-read", "read", "editor.getPieWorldContexts"),
    ToolDefinition("ue_list_pie_actors", "live-read", "read", "editor.listPieActors"),
    ToolDefinition("ue_inspect_pie_actor", "live-read", "read", "editor.inspectPieActor"),
    ToolDefinition("ue_get_pie_scalars", "live-read", "read", "editor.getPieScalars"),
    ToolDefinition(
        "ue_list_pie_object_properties",
        "live-read",
        "read",
        "editor.listPieObjectProperties",
    ),
    ToolDefinition(
        "ue_read_pie_object_properties",
        "live-read",
        "read",
        "editor.readPieObjectProperties",
    ),
    ToolDefinition("ue_get_output_log", "live-read", "read", "editor.getOutputLog"),
    ToolDefinition("ue_get_compile_errors", "live-read", "read", "editor.getCompileErrors"),
    ToolDefinition("ue_inspect_asset_live", "live-read", "read", "editor.inspectAssetLive"),
    ToolDefinition("ue_get_niagara_module_stack", "live-read", "read", "editor.getNiagaraModuleStack"),
    ToolDefinition(
        "ue_get_blueprint_graph_selection",
        "live-read",
        "read",
        "editor.getBlueprintGraphSelection",
    ),
    ToolDefinition(
        "ue_get_blueprint_graph_layout_preflight",
        "live-read",
        "read",
        "editor.getBlueprintGraphLayoutPreflight",
    ),
    ToolDefinition(
        "ue_get_focused_blueprint_graph_context",
        "live-read",
        "read",
        "editor.getFocusedBlueprintGraphContext",
    ),
    ToolDefinition(
        "ue_get_blueprint_graph_context",
        "live-read",
        "read",
        "editor.getBlueprintGraphContext",
    ),
    ToolDefinition(
        "ue_preview_blueprint_graph_layout",
        "live-read",
        "read",
    ),
    ToolDefinition(
        "ue_analyze_animation_retarget",
        "live-read",
        "read",
        "editor.analyzeAnimationRetarget",
    ),
    ToolDefinition(
        "ue_diagnose_animation_scale",
        "live-read",
        "read",
        "editor.diagnoseAnimationScale",
    ),
    ToolDefinition(
        "ue_diagnose_additive_animation",
        "live-read",
        "read",
        "editor.diagnoseAdditiveAnimation",
    ),
    ToolDefinition(
        "ue_evaluate_animation_with_base_pose",
        "live-read",
        "read",
        "editor.evaluateAnimationWithBasePose",
    ),
    ToolDefinition(
        "ue_plan_additive_base_pose_fix",
        "live-read",
        "read",
        "editor.diagnoseAdditiveAnimation",
    ),
    ToolDefinition(
        "ue_diagnose_character_ground_contact",
        "live-read",
        "read",
        "editor.diagnoseCharacterGroundContact",
    ),
    ToolDefinition(
        "ue_inspect_skeletal_secondary_motion",
        "live-read",
        "read",
        "editor.inspectSkeletalSecondaryMotion",
    ),
    ToolDefinition("ue_start_animation_scale_audit", "realtime", "planning"),
    ToolDefinition("ue_get_animation_scale_audit", "realtime", "read"),
    ToolDefinition("ue_cancel_animation_scale_audit", "realtime", "planning"),
    ToolDefinition("ue_export_animation_scale_audit_report", "realtime", "planning"),
    # Node-spawn phase A: thin read-only wrapper over native.invoke +
    # graph.nodeRegistry.catalog.  Registered with the other live read tools.
    ToolDefinition("ue_search_node_actions", "live-read", "read"),
    ToolDefinition("ue_open_asset", "live-action", "planning", "editor.openAsset"),
    ToolDefinition("ue_focus_asset", "live-action", "planning", "editor.focusAsset"),
    ToolDefinition("ue_sync_content_browser", "live-action", "planning", "editor.syncContentBrowser"),
    ToolDefinition("ue_focus_actor", "live-action", "planning", "editor.focusActor"),
    ToolDefinition("ue_compile_blueprint", "live-action", "planning", "editor.compileBlueprint"),
    ToolDefinition("ue_validate_asset", "live-action", "planning", "editor.validateAsset"),
    ToolDefinition("ue_validate_folder", "live-action", "planning", "editor.validateFolder"),
    ToolDefinition("ue_run_automation_test", "live-action", "planning", "editor.runAutomationTest"),
    ToolDefinition("ue_set_niagara_module_input", "live-action", "planning", "editor.setNiagaraModuleInput"),
    ToolDefinition(
        "ue_refresh_asset_index_live",
        "live-action",
        "planning",
        "editor.getBlueprintGraphContext",
    ),
    ToolDefinition("ue_get_editor_context", "realtime", "read", "editor.getEditorContext"),
    ToolDefinition("ue_start_batch_task", "realtime", "planning", "editor.batchTask.start"),
    ToolDefinition("ue_get_batch_task", "realtime", "read", "editor.batchTask.status"),
    ToolDefinition("ue_cancel_batch_task", "realtime", "planning", "editor.batchTask.cancel"),
    ToolDefinition("ue_set_blueprint_default", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_component_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_pin_default", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_asset_property", "workflow", "destructive", high_level_change=True),
    ToolDefinition("ue_set_asset_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_animation_scale_fix", "workflow", "destructive", high_level_change=True),
    ToolDefinition("ue_plan_animation_scale_fix", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_plan_additive_base_pose_fix_apply", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_plan_animation_scale_fix_batch", "workflow", "planning"),
    ToolDefinition("ue_get_animation_scale_fix_batch", "workflow", "read"),
    ToolDefinition("ue_apply_animation_scale_fix_batch_live", "workflow", "destructive"),
    ToolDefinition("ue_save_animation_scale_fix_batch", "workflow", "destructive"),
    ToolDefinition("ue_verify_animation_scale_fix_batch", "workflow", "planning"),
    ToolDefinition("ue_refresh_animation_scale_fix_batch_index", "workflow", "planning"),
    ToolDefinition("ue_rollback_animation_scale_fix_batch", "workflow", "destructive"),
    ToolDefinition("ue_undo_animation_scale_fix_batch", "workflow", "destructive"),
    ToolDefinition("ue_apply_asset_property_live", "workflow", "destructive"),
    ToolDefinition("ue_undo_asset_property_live", "workflow", "destructive"),
    ToolDefinition("ue_discard_asset_property_live", "workflow", "destructive"),
    ToolDefinition("ue_set_asset_reference_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_asset_structured_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_material_parameter", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_datatable", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_datatable_cell", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_datatable_row_fields", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_add_datatable_row", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_remove_datatable_row", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_rename_datatable_row", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_plan_patch", "workflow", "planning"),
    ToolDefinition("ue_plan_live_write_batch", "workflow", "planning"),
    ToolDefinition("ue_apply_live_write_batch", "workflow", "destructive"),
    ToolDefinition("ue_save_change_set_checkpoint", "workflow", "destructive"),
    ToolDefinition("ue_verify_change_set_checkpoint", "workflow", "read"),
    ToolDefinition("ue_recover_live_write_batch", "workflow", "destructive"),
    ToolDefinition("ue_dry_run_patch", "workflow", "planning"),
    ToolDefinition("ue_apply_patch", "workflow", "destructive"),
    ToolDefinition("ue_animation_retarget", "workflow", "destructive"),
    ToolDefinition("ue_plan_animation_retarget", "workflow", "planning"),
    ToolDefinition("ue_apply_animation_retarget_setup", "workflow", "destructive"),
    ToolDefinition("ue_start_animation_retarget_batch", "workflow", "destructive"),
    ToolDefinition("ue_get_animation_retarget_batch", "workflow", "read"),
    ToolDefinition("ue_start_animation_retarget_postprocess", "workflow", "planning"),
    ToolDefinition("ue_get_animation_retarget_postprocess", "workflow", "read"),
    ToolDefinition("ue_plan_animation_retarget_postprocess", "workflow", "planning"),
    ToolDefinition("ue_reopen_animation_retarget_postprocess", "workflow", "read"),
    ToolDefinition("ue_refresh_animation_retarget_postprocess_index", "workflow", "planning"),
    ToolDefinition("ue_cancel_animation_retarget_batch", "workflow", "destructive"),
    ToolDefinition("ue_save_animation_retarget_batch", "workflow", "destructive"),
    ToolDefinition("ue_validate_animation_retarget", "workflow", "read"),
    ToolDefinition("ue_verify_animation_retarget_batch", "workflow", "read"),
    ToolDefinition("ue_rollback_animation_retarget_batch", "workflow", "destructive"),
    ToolDefinition("ue_verify_asset", "workflow", "planning"),
    ToolDefinition("ue_verify_live_write", "workflow", "planning"),
    ToolDefinition("ue_verify_live_write_fast", "workflow", "read"),
    ToolDefinition("ue_verify_live_write_checkpoint", "workflow", "read"),
    ToolDefinition("ue_get_asset_state", "workflow", "read"),
    ToolDefinition("ue_refresh_asset_index", "workflow", "planning"),
    ToolDefinition("ue_begin_blueprint_edit_session", "workflow", "destructive"),
    ToolDefinition("ue_end_blueprint_edit_session", "workflow", "destructive"),
    ToolDefinition("ue_move_blueprint_node", "workflow", "destructive"),
    ToolDefinition("ue_move_blueprint_nodes", "workflow", "destructive"),
    ToolDefinition("ue_save_authorized_asset", "workflow", "destructive"),
    ToolDefinition("ue_rollback_patch", "workflow", "destructive"),
    ToolDefinition("ue_create_change_set", "workflow", "planning"),
    ToolDefinition("ue_get_change_set", "workflow", "read"),
    ToolDefinition("ue_source_control_status", "source-control", "read"),
    ToolDefinition("ue_source_control_prepare_write", "source-control", "planning"),
    ToolDefinition("ue_source_control_changelists", "source-control", "read"),
    ToolDefinition("ue_source_control_prepare_changelist", "source-control", "planning"),
    ToolDefinition("ue_source_control_resolve_status", "source-control", "read"),
    ToolDefinition("ue_source_control_resolve_text", "source-control", "planning"),
)

TOOL_DEFINITIONS_BY_NAME = {definition.name: definition for definition in TOOL_REGISTRY}

# Tools that one consolidated tool replaces.  When the consolidated surface is
# on, the replaced names are never registered or advertised, so a client never
# sees both.  Defined before the name lists below because they filter on it.
CONSOLIDATED_REPLACED_TOOLS: dict[str, tuple[str, ...]] = {
    "ue_datatable": (
        "ue_set_datatable_cell",
        "ue_set_datatable_row_fields",
        "ue_add_datatable_row",
        "ue_remove_datatable_row",
        "ue_rename_datatable_row",
    ),
    "ue_animation_retarget": (
        "ue_plan_animation_retarget",
        "ue_apply_animation_retarget_setup",
        "ue_start_animation_retarget_batch",
        "ue_get_animation_retarget_batch",
        "ue_start_animation_retarget_postprocess",
        "ue_get_animation_retarget_postprocess",
        "ue_plan_animation_retarget_postprocess",
        "ue_reopen_animation_retarget_postprocess",
        "ue_refresh_animation_retarget_postprocess_index",
        "ue_cancel_animation_retarget_batch",
        "ue_save_animation_retarget_batch",
        "ue_validate_animation_retarget",
        "ue_verify_animation_retarget_batch",
        "ue_rollback_animation_retarget_batch",
    ),
    "ue_asset_property": (
        "ue_set_asset_property",
        "ue_set_asset_reference_property",
        "ue_set_asset_structured_property",
        "ue_apply_asset_property_live",
        "ue_undo_asset_property_live",
        "ue_discard_asset_property_live",
    ),
    "ue_animation_scale_fix": (
        "ue_plan_animation_scale_fix",
        "ue_plan_animation_scale_fix_batch",
        "ue_get_animation_scale_fix_batch",
        "ue_apply_animation_scale_fix_batch_live",
        "ue_save_animation_scale_fix_batch",
        "ue_verify_animation_scale_fix_batch",
        "ue_refresh_animation_scale_fix_batch_index",
        "ue_rollback_animation_scale_fix_batch",
        "ue_undo_animation_scale_fix_batch",
    ),
    "ue_memory": (
        "ue_memory_search",
        "ue_memory_get",
        "ue_memory_list_l0_events",
        "ue_memory_get_l0_event",
        "ue_memory_get_context",
        "ue_memory_expand_node",
        "ue_memory_get_evidence",
    ),
    "ue_memory_write": (
        "ue_memory_add_rule",
        "ue_memory_record_finding",
        "ue_memory_record_task",
        "ue_memory_mark_superseded",
        "ue_memory_validate",
        "ue_memory_update_knowledge",
        "ue_memory_update_work",
    ),
    "ue_pie": (
        "ue_get_pie_state",
        "ue_get_pie_world_contexts",
        "ue_list_pie_actors",
        "ue_inspect_pie_actor",
        "ue_get_pie_scalars",
        "ue_list_pie_object_properties",
        "ue_read_pie_object_properties",
    ),
}

CONSOLIDATED_TOOL_NAMES: tuple[str, ...] = tuple(CONSOLIDATED_REPLACED_TOOLS)


def replaced_tool_names() -> frozenset[str]:
    """Every tool name a consolidated tool takes over."""

    return frozenset(name for names in CONSOLIDATED_REPLACED_TOOLS.values() for name in names)


QUERY_TOOL_NAMES = [definition.name for definition in TOOL_REGISTRY if definition.group == "query"]
MEMORY_TOOL_NAMES = [
    definition.name
    for definition in TOOL_REGISTRY
    if definition.group == "memory" and definition.name not in replaced_tool_names()
]
LIVE_EDITOR_TOOL_NAMES = [
    definition.name
    for definition in TOOL_REGISTRY
    if definition.group in {"live-read", "live-action", "realtime"}
    and definition.name not in replaced_tool_names()
]
WORKFLOW_TOOL_NAMES = [definition.name for definition in TOOL_REGISTRY if definition.group == "workflow"]
SOURCE_CONTROL_TOOL_NAMES = [
    definition.name for definition in TOOL_REGISTRY if definition.group == "source-control"
]
HIGH_LEVEL_WRITE_TOOL_NAMES = [
    definition.name
    for definition in TOOL_REGISTRY
    if definition.high_level_change and definition.name not in replaced_tool_names()
]
LIVE_EDITOR_METHODS = {
    definition.name: definition.live_method
    for definition in TOOL_REGISTRY
    if definition.group in {"live-read", "live-action", "realtime"}
    and definition.live_method
}


def tool_definitions_for_mode(
    *,
    live_editor_enabled: bool,
    workflow_enabled: bool,
    memory_enabled: bool = False,
    source_control_enabled: bool = False,
) -> list[ToolDefinition]:
    enabled_groups: set[ToolGroup] = {"query"}
    if memory_enabled:
        enabled_groups.add("memory")
    if live_editor_enabled:
        enabled_groups.update({"live-read", "live-action", "realtime"})
    if workflow_enabled:
        enabled_groups.add("workflow")
    if source_control_enabled:
        enabled_groups.add("source-control")
    return [definition for definition in TOOL_REGISTRY if definition.group in enabled_groups]


ToolProfile = Literal["core", "read", "live", "live-write", "full"]

TOOL_PROFILE_NAMES: tuple[ToolProfile, ...] = ("core", "read", "live", "live-write", "full")

# A profile is an UPPER BOUND on the tool groups a session may see.  It never
# enables a group that the run flags left off: the effective set is
# profile groups intersected with what the server actually registered.
TOOL_PROFILE_GROUPS: dict[ToolProfile, frozenset[ToolGroup]] = {
    "core": frozenset({"query"}),
    "read": frozenset({"query", "memory"}),
    "live": frozenset({"query", "memory", "live-read", "realtime"}),
    "live-write": frozenset({"query", "memory", "live-read", "live-action", "realtime"}),
    "full": frozenset(
        {"query", "memory", "live-read", "live-action", "realtime", "workflow", "source-control"}
    ),
}


def profile_groups(profile: str) -> frozenset[ToolGroup]:
    """The tool groups one profile may expose.  Unknown profiles are rejected."""

    groups = TOOL_PROFILE_GROUPS.get(profile)  # type: ignore[arg-type]
    if groups is None:
        raise ValueError(
            f"Unknown tool profile {profile!r}; expected one of {TOOL_PROFILE_NAMES}."
        )
    return groups


def tool_group_for_name(name: str) -> ToolGroup | None:
    """The registry group of one tool name, or None when the registry does not know it."""

    definition = TOOL_DEFINITIONS_BY_NAME.get(name)
    return definition.group if definition is not None else None


def tool_profile_allows(profile: str, tool_name: str) -> bool:
    """Whether a profile may expose one tool.

    Tools the registry does not know (experimental surfaces registered outside it,
    legacy helpers) are allowed: the profile must not silently hide them.
    """

    group = tool_group_for_name(tool_name)
    if group is None:
        return True
    return group in profile_groups(profile)


def tool_names_for_profile(
    profile: str,
    *,
    live_editor_enabled: bool = False,
    workflow_enabled: bool = False,
    memory_enabled: bool = False,
    source_control_enabled: bool = False,
) -> list[str]:
    """Mode-filtered tool names, further narrowed by one profile."""

    allowed = profile_groups(profile)
    return [
        name
        for name in tool_names_for_mode(
            live_editor_enabled=live_editor_enabled,
            workflow_enabled=workflow_enabled,
            memory_enabled=memory_enabled,
            source_control_enabled=source_control_enabled,
        )
        if (tool_group_for_name(name) or "query") in allowed
    ]


def tool_names_for_mode(
    *,
    live_editor_enabled: bool = False,
    workflow_enabled: bool = False,
    memory_enabled: bool = False,
    source_control_enabled: bool = False,
) -> list[str]:
    return [
        definition.name
        for definition in tool_definitions_for_mode(
            live_editor_enabled=live_editor_enabled,
            workflow_enabled=workflow_enabled,
            memory_enabled=memory_enabled,
            source_control_enabled=source_control_enabled,
        )
    ]


def consolidated_tool_names_for_mode(
    *,
    live_editor_enabled: bool = False,
    workflow_enabled: bool = False,
    memory_enabled: bool = False,
    source_control_enabled: bool = False,
) -> list[str]:
    """Registered names for one mode with every replaced per-verb name removed.

    This is what an unprofiled server exposes: the mode filter, minus the names a
    consolidated tool took over, in registry order.
    """

    replaced = replaced_tool_names()
    return [
        name
        for name in tool_names_for_mode(
            live_editor_enabled=live_editor_enabled,
            workflow_enabled=workflow_enabled,
            memory_enabled=memory_enabled,
            source_control_enabled=source_control_enabled,
        )
        if name not in replaced
    ]


def effective_tool_names_for_profile(
    profile: str,
    *,
    live_editor_enabled: bool = False,
    workflow_enabled: bool = False,
    memory_enabled: bool = False,
    source_control_enabled: bool = False,
) -> list[str]:
    """What a client actually receives: profile minus replaced names plus consolidated tools."""

    allowed = profile_groups(profile)
    names: list[str] = []
    for name in consolidated_tool_names_for_mode(
        live_editor_enabled=live_editor_enabled,
        workflow_enabled=workflow_enabled,
        memory_enabled=memory_enabled,
        source_control_enabled=source_control_enabled,
    ):
        group = tool_group_for_name(name)
        if group is None or group in allowed:
            names.append(name)
    return names


def tool_descriptors_for_mode(
    *,
    live_editor_enabled: bool,
    workflow_enabled: bool,
    memory_enabled: bool = False,
    source_control_enabled: bool = False,
) -> list[dict[str, object]]:
    """Descriptors for what a client can actually call: replaced names excluded."""

    replaced = replaced_tool_names()
    return [
        {
            "name": definition.name,
            "readOnly": definition.read_only,
            "destructive": definition.destructive,
        }
        for definition in tool_definitions_for_mode(
            live_editor_enabled=live_editor_enabled,
            workflow_enabled=workflow_enabled,
            memory_enabled=memory_enabled,
            source_control_enabled=source_control_enabled,
        )
        if definition.name not in replaced
    ]

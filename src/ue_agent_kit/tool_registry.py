from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ToolGroup = Literal["query", "memory", "live-read", "live-action", "realtime", "workflow"]
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
    ToolDefinition("ue_get_capabilities", "query", "read"),
    ToolDefinition("ue_get_project_status", "query", "read"),
    ToolDefinition("ue_search", "query", "read"),
    ToolDefinition("ue_get_asset", "query", "read"),
    ToolDefinition("ue_find_references", "query", "read"),
    ToolDefinition("ue_memory_search", "memory", "read"),
    ToolDefinition("ue_memory_get", "memory", "read"),
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
    ToolDefinition("ue_get_output_log", "live-read", "read", "editor.getOutputLog"),
    ToolDefinition("ue_get_compile_errors", "live-read", "read", "editor.getCompileErrors"),
    ToolDefinition("ue_inspect_asset_live", "live-read", "read", "editor.inspectAssetLive"),
    ToolDefinition(
        "ue_get_blueprint_graph_selection",
        "live-read",
        "read",
        "editor.getBlueprintGraphSelection",
    ),
    ToolDefinition("ue_open_asset", "live-action", "planning", "editor.openAsset"),
    ToolDefinition("ue_focus_asset", "live-action", "planning", "editor.focusAsset"),
    ToolDefinition("ue_sync_content_browser", "live-action", "planning", "editor.syncContentBrowser"),
    ToolDefinition("ue_focus_actor", "live-action", "planning", "editor.focusActor"),
    ToolDefinition("ue_compile_blueprint", "live-action", "planning", "editor.compileBlueprint"),
    ToolDefinition("ue_validate_asset", "live-action", "planning", "editor.validateAsset"),
    ToolDefinition("ue_validate_folder", "live-action", "planning", "editor.validateFolder"),
    ToolDefinition("ue_run_automation_test", "live-action", "planning", "editor.runAutomationTest"),
    ToolDefinition("ue_get_editor_context", "realtime", "read", "editor.getEditorContext"),
    ToolDefinition("ue_start_batch_task", "realtime", "planning", "editor.batchTask.start"),
    ToolDefinition("ue_get_batch_task", "realtime", "read", "editor.batchTask.status"),
    ToolDefinition("ue_cancel_batch_task", "realtime", "planning", "editor.batchTask.cancel"),
    ToolDefinition("ue_set_blueprint_default", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_component_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_pin_default", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_asset_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_apply_asset_property_live", "workflow", "destructive"),
    ToolDefinition("ue_undo_asset_property_live", "workflow", "destructive"),
    ToolDefinition("ue_discard_asset_property_live", "workflow", "destructive"),
    ToolDefinition("ue_set_asset_reference_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_asset_structured_property", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_material_parameter", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_datatable_cell", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_set_datatable_row_fields", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_add_datatable_row", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_remove_datatable_row", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_rename_datatable_row", "workflow", "planning", high_level_change=True),
    ToolDefinition("ue_plan_patch", "workflow", "planning"),
    ToolDefinition("ue_dry_run_patch", "workflow", "planning"),
    ToolDefinition("ue_apply_patch", "workflow", "destructive"),
    ToolDefinition("ue_verify_asset", "workflow", "planning"),
    ToolDefinition("ue_verify_live_write", "workflow", "planning"),
    ToolDefinition("ue_get_asset_state", "workflow", "read"),
    ToolDefinition("ue_refresh_asset_index", "workflow", "planning"),
    ToolDefinition("ue_save_authorized_asset", "workflow", "destructive"),
    ToolDefinition("ue_rollback_patch", "workflow", "destructive"),
    ToolDefinition("ue_create_change_set", "workflow", "planning"),
    ToolDefinition("ue_get_change_set", "workflow", "read"),
)

TOOL_DEFINITIONS_BY_NAME = {definition.name: definition for definition in TOOL_REGISTRY}
QUERY_TOOL_NAMES = [definition.name for definition in TOOL_REGISTRY if definition.group == "query"]
MEMORY_TOOL_NAMES = [definition.name for definition in TOOL_REGISTRY if definition.group == "memory"]
LIVE_EDITOR_TOOL_NAMES = [
    definition.name for definition in TOOL_REGISTRY if definition.group in {"live-read", "live-action", "realtime"}
]
WORKFLOW_TOOL_NAMES = [definition.name for definition in TOOL_REGISTRY if definition.group == "workflow"]
HIGH_LEVEL_WRITE_TOOL_NAMES = [definition.name for definition in TOOL_REGISTRY if definition.high_level_change]
LIVE_EDITOR_METHODS = {
    definition.name: definition.live_method
    for definition in TOOL_REGISTRY
    if definition.group in {"live-read", "live-action", "realtime"}
}


def tool_definitions_for_mode(
    *,
    live_editor_enabled: bool,
    workflow_enabled: bool,
    memory_enabled: bool = False,
) -> list[ToolDefinition]:
    enabled_groups: set[ToolGroup] = {"query"}
    if memory_enabled:
        enabled_groups.add("memory")
    if live_editor_enabled:
        enabled_groups.update({"live-read", "live-action", "realtime"})
    if workflow_enabled:
        enabled_groups.add("workflow")
    return [definition for definition in TOOL_REGISTRY if definition.group in enabled_groups]


def tool_names_for_mode(
    *,
    live_editor_enabled: bool = False,
    workflow_enabled: bool = False,
    memory_enabled: bool = False,
) -> list[str]:
    return [
        definition.name
        for definition in tool_definitions_for_mode(
            live_editor_enabled=live_editor_enabled,
            workflow_enabled=workflow_enabled,
            memory_enabled=memory_enabled,
        )
    ]


def tool_descriptors_for_mode(
    *,
    live_editor_enabled: bool,
    workflow_enabled: bool,
    memory_enabled: bool = False,
) -> list[dict[str, object]]:
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
        )
    ]

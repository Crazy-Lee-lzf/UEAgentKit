from __future__ import annotations

from typing import Iterable

from ue_agent_kit.tool_registry import tool_names_for_mode

HIGH_LEVEL_R0_R3_TOOLS = frozenset(
    {
        "ue_get_task_context",
        "ue_analyze_change_impact",
        "ue_analyze_semantic_diff",
        "ue_build_verification_plan",
        "ue_evaluate_trust_verdict",
    }
)

REQUIRED_LEGACY_SAFETY_TOOLS = frozenset(
    {
        "ue_get_capabilities",
        "ue_get_project_status",
        "ue_search",
        "ue_get_asset",
        "ue_find_references",
        "ue_create_change_set",
        "ue_get_change_set",
        "ue_compile_blueprint",
        "ue_validate_asset",
        "ue_run_automation_test",
        "ue_verify_asset",
        "ue_verify_live_write",
        "ue_save_authorized_asset",
        "ue_rollback_patch",
    }
)


def tools_for_profile(
    profile: str,
    *,
    live_editor_enabled: bool,
    workflow_enabled: bool,
    memory_enabled: bool = False,
    production_tools: Iterable[str] | None = None,
) -> tuple[str, ...]:
    tools = tuple(
        production_tools
        if production_tools is not None
        else tool_names_for_mode(
            live_editor_enabled=live_editor_enabled,
            workflow_enabled=workflow_enabled,
            memory_enabled=memory_enabled,
        )
    )
    if profile == "full-r0-r3":
        return tools
    if profile != "legacy-low-level":
        raise ValueError(f"Unknown tool profile: {profile}")
    filtered = tuple(tool for tool in tools if tool not in HIGH_LEVEL_R0_R3_TOOLS)
    required = REQUIRED_LEGACY_SAFETY_TOOLS.intersection(tools)
    missing = required.difference(filtered)
    if missing:
        raise RuntimeError(f"Legacy profile removed safety tools: {sorted(missing)}")
    return filtered

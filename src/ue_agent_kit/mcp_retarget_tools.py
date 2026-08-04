from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError

RETARGET_INSPECT_CAPABILITY = "retarget.inspect"
RETARGET_PLAN_CAPABILITY = "retarget.plan"
RETARGET_CONFIGURE_CAPABILITY = "retarget.configure"

_CAPABILITY_FOR_TOOL = {
    "ue_analyze_animation_retarget": RETARGET_INSPECT_CAPABILITY,
    "ue_plan_animation_retarget": RETARGET_PLAN_CAPABILITY,
    "ue_apply_animation_retarget_setup": RETARGET_CONFIGURE_CAPABILITY,
}


def _assert_retarget_policy_capability(
    *,
    policy_path: Any,
    tool_name: str,
) -> None:
    if policy_path is None:
        raise LiveEditorError(
            "retarget_capability_unavailable",
            "The fixed Project Write Policy is unavailable for retarget capability checks.",
        )
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise LiveEditorError(
            "retarget_capability_unavailable",
            "The fixed Project Write Policy could not be read for retarget capability checks.",
        ) from exc
    capabilities = policy.get("retargetCapabilities")
    if not isinstance(capabilities, list):
        raise LiveEditorError(
            "retarget_capability_unavailable",
            "The fixed Policy does not declare a retargetCapabilities list.",
        )
    required = _CAPABILITY_FOR_TOOL.get(tool_name)
    if required is not None and required not in capabilities:
        raise LiveEditorError(
            "retarget_capability_unavailable",
            f"The fixed Policy does not enable the {required} capability.",
        )


def register_retarget_tools(
    *,
    server: Any,
    live_editor_service: LiveEditorBridgeService,
    read_annotations: Any,
    error_response: Any,
) -> None:
    @server.tool(annotations=read_annotations)
    def ue_analyze_animation_retarget(
        sourceMesh: str,
        targetMesh: str,
        includeOptionalChains: bool = True,
        maxBoneDetails: int = 512,
    ) -> dict[str, Any]:
        """Read-only retarget compatibility analysis between two loaded Skeletal Meshes."""
        try:
            _assert_retarget_policy_capability(
                policy_path=getattr(live_editor_service.config, "policy_path", None),
                tool_name="ue_analyze_animation_retarget",
            )
            return live_editor_service.call_tool(
                "ue_analyze_animation_retarget",
                {
                    "sourceMesh": sourceMesh,
                    "targetMesh": targetMesh,
                    "includeOptionalChains": includeOptionalChains,
                    "maxBoneDetails": maxBoneDetails,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_analyze_animation_retarget", exc, read_only=True)


def register_retarget_workflow_tools(
    *,
    server: Any,
    workflow_service: Any,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    planning_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    destructive_annotations = tool_annotations_type(
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=planning_annotations)
    def ue_plan_animation_retarget(
        sourceMesh: str,
        targetMesh: str,
        includeOptionalChains: bool = True,
        outputDirectory: str = "",
    ) -> dict[str, Any]:
        """Create an immutable retarget-plan-v1 for a source/target mesh pair without modifying assets."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_plan_animation_retarget",
            )
            return workflow_service.plan_animation_retarget(
                source_mesh=sourceMesh,
                target_mesh=targetMesh,
                include_optional_chains=includeOptionalChains,
                output_directory=outputDirectory,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_plan_animation_retarget", exc, read_only=True)

    @server.tool(annotations=destructive_annotations)
    def ue_apply_animation_retarget_setup(
        planId: str,
        confirmation: str,
        changeSetId: str = "",
        updateExisting: bool = False,
        allowLargePoseOffset: bool = False,
    ) -> dict[str, Any]:
        """Apply the confirmed retarget plan: create or update the Source and Target IK Rigs, the IK Retargeter, chain mappings and retarget pose."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_apply_animation_retarget_setup",
            )
            return workflow_service.apply_animation_retarget_setup(
                plan_id=planId,
                confirmation=confirmation,
                change_set_id=changeSetId,
                update_existing=updateExisting,
                allow_large_pose_offset=allowLargePoseOffset,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_apply_animation_retarget_setup", exc, read_only=False)

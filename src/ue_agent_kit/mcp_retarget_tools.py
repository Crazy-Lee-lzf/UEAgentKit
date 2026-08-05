from __future__ import annotations

import json
from typing import Any

from .editor_bridge import LiveEditorBridgeService, LiveEditorError

RETARGET_INSPECT_CAPABILITY = "retarget.inspect"
RETARGET_PLAN_CAPABILITY = "retarget.plan"
RETARGET_CONFIGURE_CAPABILITY = "retarget.configure"
RETARGET_BATCH_CAPABILITY = "retarget.batch"
RETARGET_VALIDATE_CAPABILITY = "retarget.validate"

_CAPABILITY_FOR_TOOL = {
    "ue_analyze_animation_retarget": RETARGET_INSPECT_CAPABILITY,
    "ue_plan_animation_retarget": RETARGET_PLAN_CAPABILITY,
    "ue_apply_animation_retarget_setup": RETARGET_CONFIGURE_CAPABILITY,
    "ue_start_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_get_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_cancel_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_save_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_validate_animation_retarget": RETARGET_VALIDATE_CAPABILITY,
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
    action_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )
    read_annotations = tool_annotations_type(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
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

    @server.tool(annotations=action_annotations)
    def ue_start_animation_retarget_batch(
        planId: str,
        retargeter: str,
        sourceAssets: list[str],
        outputDirectory: str = "",
        naming: dict[str, Any] | None = None,
        overwriteExisting: bool = False,
        includeReferencedAssets: bool = True,
        exportOnlyAnimatedBones: bool = True,
        retainAdditiveFlags: bool = True,
    ) -> dict[str, Any]:
        """Queue an animation batch retarget task; poll ue_get_animation_retarget_batch to run it."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_start_animation_retarget_batch",
            )
            return workflow_service.start_animation_retarget_batch(
                plan_id=planId,
                retargeter=retargeter,
                source_assets=sourceAssets,
                output_directory=outputDirectory,
                naming=naming,
                overwrite_existing=overwriteExisting,
                include_referenced_assets=includeReferencedAssets,
                export_only_animated_bones=exportOnlyAnimatedBones,
                retain_additive_flags=retainAdditiveFlags,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_start_animation_retarget_batch", exc, read_only=False)

    @server.tool(annotations=read_annotations)
    def ue_get_animation_retarget_batch(taskId: str) -> dict[str, Any]:
        """Advance and return the state of an animation retarget batch task."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_get_animation_retarget_batch",
            )
            return workflow_service.get_animation_retarget_batch(task_id=taskId)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_animation_retarget_batch", exc, read_only=True)

    @server.tool(annotations=action_annotations)
    def ue_cancel_animation_retarget_batch(taskId: str) -> dict[str, Any]:
        """Cancel a queued animation retarget batch task before it runs."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_cancel_animation_retarget_batch",
            )
            return workflow_service.cancel_animation_retarget_batch(task_id=taskId)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_cancel_animation_retarget_batch", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_save_animation_retarget_batch(taskId: str, confirmation: str) -> dict[str, Any]:
        """Save the retargeted animation outputs of a completed batch task to disk."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_save_animation_retarget_batch",
            )
            return workflow_service.save_animation_retarget_batch(task_id=taskId, confirmation=confirmation)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_save_animation_retarget_batch", exc, read_only=False)

    @server.tool(annotations=read_annotations)
    def ue_validate_animation_retarget(
        retargeter: str,
        animationPaths: list[str],
    ) -> dict[str, Any]:
        """Validate the IK Retargeter structure and retargeted animation metadata and motion."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_validate_animation_retarget",
            )
            return workflow_service.validate_animation_retarget(
                retargeter=retargeter,
                animation_paths=animationPaths,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_validate_animation_retarget", exc, read_only=True)

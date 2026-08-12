from __future__ import annotations

import json
from typing import Any, Literal

from .animation_scale_audit import AnimationScaleAuditService
from .editor_bridge import LiveEditorBridgeService, LiveEditorError
from .retarget_postprocess import RetargetPostprocessService

RETARGET_INSPECT_CAPABILITY = "retarget.inspect"
RETARGET_PLAN_CAPABILITY = "retarget.plan"
RETARGET_CONFIGURE_CAPABILITY = "retarget.configure"
RETARGET_BATCH_CAPABILITY = "retarget.batch"
RETARGET_VALIDATE_CAPABILITY = "retarget.validate"

_CAPABILITY_FOR_TOOL = {
    "ue_analyze_animation_retarget": RETARGET_INSPECT_CAPABILITY,
    "ue_diagnose_animation_scale": RETARGET_INSPECT_CAPABILITY,
    "ue_start_animation_scale_audit": RETARGET_INSPECT_CAPABILITY,
    "ue_get_animation_scale_audit": RETARGET_INSPECT_CAPABILITY,
    "ue_cancel_animation_scale_audit": RETARGET_INSPECT_CAPABILITY,
    "ue_export_animation_scale_audit_report": RETARGET_INSPECT_CAPABILITY,
    "ue_plan_animation_retarget": RETARGET_PLAN_CAPABILITY,
    "ue_apply_animation_retarget_setup": RETARGET_CONFIGURE_CAPABILITY,
    "ue_start_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_get_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_start_animation_retarget_postprocess": RETARGET_INSPECT_CAPABILITY,
    "ue_get_animation_retarget_postprocess": RETARGET_INSPECT_CAPABILITY,
    "ue_plan_animation_retarget_postprocess": RETARGET_INSPECT_CAPABILITY,
    "ue_reopen_animation_retarget_postprocess": RETARGET_INSPECT_CAPABILITY,
    "ue_refresh_animation_retarget_postprocess_index": RETARGET_BATCH_CAPABILITY,
    "ue_cancel_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_save_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
    "ue_validate_animation_retarget": RETARGET_VALIDATE_CAPABILITY,
    "ue_verify_animation_retarget_batch": RETARGET_VALIDATE_CAPABILITY,
    "ue_rollback_animation_retarget_batch": RETARGET_BATCH_CAPABILITY,
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
    tool_annotations_type: Any = None,
    index_service: Any = None,
    report_root: Any = None,
) -> None:
    annotations_type = tool_annotations_type or type(read_annotations)
    planning_annotations = annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    audit_service = AnimationScaleAuditService(
        live_editor_service,
        index_service=index_service,
        report_root=report_root,
    )

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

    @server.tool(annotations=read_annotations)
    def ue_diagnose_animation_scale(
        animationPaths: list[str],
        boneNames: list[str],
        loadIfNeeded: bool = False,
    ) -> dict[str, Any]:
        """Read raw animation scale tracks and matching Skeleton reference scales."""
        try:
            _assert_retarget_policy_capability(
                policy_path=getattr(live_editor_service.config, "policy_path", None),
                tool_name="ue_diagnose_animation_scale",
            )
            return live_editor_service.call_tool(
                "ue_diagnose_animation_scale",
                {
                    "animationPaths": animationPaths,
                    "boneNames": boneNames,
                    "loadIfNeeded": loadIfNeeded,
                },
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_diagnose_animation_scale", exc, read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_start_animation_scale_audit(
        animationPaths: list[str] | None = None,
        pathPrefix: str = "",
        boneNames: list[str] | None = None,
        loadIfNeeded: bool = False,
        batchSize: int = 1,
    ) -> dict[str, Any]:
        """Start a bounded read-only AnimSequence scale audit over explicit Object Paths."""
        try:
            _assert_retarget_policy_capability(
                policy_path=getattr(live_editor_service.config, "policy_path", None),
                tool_name="ue_start_animation_scale_audit",
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_start_animation_scale_audit",
                "ok": True,
                "readOnly": False,
                "result": audit_service.start(
                    animation_paths=animationPaths,
                    path_prefix=pathPrefix,
                    bone_names=boneNames,
                    load_if_needed=loadIfNeeded,
                    batch_size=batchSize,
                ),
            }
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_start_animation_scale_audit", exc, read_only=False)

    @server.tool(annotations=read_annotations)
    def ue_get_animation_scale_audit(
        taskId: str,
        detailOffset: int = 0,
        detailLimit: int = 20,
        classificationFilter: list[str] | None = None,
        sortBy: Literal["processed-order", "asset-path", "classification"] = "processed-order",
    ) -> dict[str, Any]:
        """Advance one bounded audit batch and return progress plus paged read-only results."""
        try:
            _assert_retarget_policy_capability(
                policy_path=getattr(live_editor_service.config, "policy_path", None),
                tool_name="ue_get_animation_scale_audit",
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_get_animation_scale_audit",
                "ok": True,
                "readOnly": True,
                "result": audit_service.get(
                    task_id=taskId,
                    detail_offset=detailOffset,
                    detail_limit=detailLimit,
                    classification_filter=classificationFilter,
                    sort_by=sortBy,
                ),
            }
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_animation_scale_audit", exc, read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_cancel_animation_scale_audit(taskId: str) -> dict[str, Any]:
        """Cancel the active in-memory animation scale audit without modifying Unreal assets."""
        try:
            _assert_retarget_policy_capability(
                policy_path=getattr(live_editor_service.config, "policy_path", None),
                tool_name="ue_cancel_animation_scale_audit",
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_cancel_animation_scale_audit",
                "ok": True,
                "readOnly": False,
                "result": audit_service.cancel(task_id=taskId),
            }
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_cancel_animation_scale_audit", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_export_animation_scale_audit_report(
        taskId: str,
        classificationFilter: list[str] | None = None,
        sortBy: Literal["processed-order", "asset-path", "classification"] = "asset-path",
    ) -> dict[str, Any]:
        """Export a finished animation scale audit to the fixed MCP WorkRoot as deterministic JSON."""
        try:
            _assert_retarget_policy_capability(
                policy_path=getattr(live_editor_service.config, "policy_path", None),
                tool_name="ue_export_animation_scale_audit_report",
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_export_animation_scale_audit_report",
                "ok": True,
                "readOnly": False,
                "result": audit_service.export_report(
                    task_id=taskId,
                    classification_filter=classificationFilter,
                    sort_by=sortBy,
                ),
            }
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_export_animation_scale_audit_report", exc, read_only=False)


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

    postprocess_service = RetargetPostprocessService(workflow_service)

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

    @server.tool(annotations=planning_annotations)
    def ue_start_animation_retarget_postprocess(
        retargetTaskId: str,
        loadIfNeeded: bool = True,
        batchSize: int = 1,
    ) -> dict[str, Any]:
        """Start read-only post-processing over the exact outputs of one completed retarget batch."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_start_animation_retarget_postprocess",
            )
            return {
                "tool": "ue_start_animation_retarget_postprocess",
                "ok": True,
                "readOnly": True,
                "result": postprocess_service.start(
                    retarget_task_id=retargetTaskId,
                    load_if_needed=loadIfNeeded,
                    batch_size=batchSize,
                ),
            }
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_start_animation_retarget_postprocess", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_get_animation_retarget_postprocess(postprocessId: str) -> dict[str, Any]:
        """Advance at most one bounded AnimSequence audit batch and return post-process suggestions."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_get_animation_retarget_postprocess",
            )
            return {
                "tool": "ue_get_animation_retarget_postprocess",
                "ok": True,
                "readOnly": True,
                "result": postprocess_service.get(postprocess_id=postprocessId),
            }
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_get_animation_retarget_postprocess", exc, read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_plan_animation_retarget_postprocess(
        postprocessId: str,
        description: str = "",
    ) -> dict[str, Any]:
        """Persist an immutable read-only suggested post-process Plan under the fixed WorkRoot."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_plan_animation_retarget_postprocess",
            )
            return postprocess_service.plan(postprocess_id=postprocessId, description=description)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_plan_animation_retarget_postprocess", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_reopen_animation_retarget_postprocess(planRelativePath: str) -> dict[str, Any]:
        """Reopen a persisted immutable retarget post-process Plan after an MCP restart to rebuild trusted read-only context."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_reopen_animation_retarget_postprocess",
            )
            return postprocess_service.reopen(plan_relative_path=planRelativePath)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_reopen_animation_retarget_postprocess", exc, read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_refresh_animation_retarget_postprocess_index(
        postprocessId: str,
        mode: Literal["Preview", "Apply"] = "Preview",
        confirmation: str = "",
        refreshReceipt: str = "",
        maxAssets: int = 1,
    ) -> dict[str, Any]:
        """Prepare or atomically activate one paired snapshot generation for a saved and verified retarget batch's eligible AnimSequence outputs."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_refresh_animation_retarget_postprocess_index",
            )
            response = postprocess_service.refresh_index(
                postprocess_id=postprocessId,
                mode=mode,
                confirmation=confirmation,
                refresh_receipt=refreshReceipt,
                max_assets=maxAssets,
            )
            return {
                "tool": "ue_refresh_animation_retarget_postprocess_index",
                "ok": True,
                "readOnly": False,
                "result": response,
                "nextStep": (
                    "Continue Preview with refreshReceipt until indexRefreshState is ready; then Apply with confirmation "
                    "'REFRESH RETARGET POSTPROCESS <postprocessId>'."
                    if mode == "Preview"
                    else "Restart the MCP server after a successful Apply so the new paired snapshot becomes the frozen session snapshot."
                ),
            }
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_refresh_animation_retarget_postprocess_index", exc, read_only=False)

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
    def ue_save_animation_retarget_batch(
        taskId: str,
        confirmation: str,
        changeSetId: str = "",
    ) -> dict[str, Any]:
        """Save the retargeted animation outputs of a completed batch task to disk."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_save_animation_retarget_batch",
            )
            return workflow_service.save_animation_retarget_batch(
                task_id=taskId,
                confirmation=confirmation,
                change_set_id=changeSetId,
            )
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

    @server.tool(annotations=read_annotations)
    def ue_verify_animation_retarget_batch(taskId: str) -> dict[str, Any]:
        """Independently reload saved retarget batch outputs in a fresh Unreal process and compare their SHA-256 Revisions."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_verify_animation_retarget_batch",
            )
            return workflow_service.verify_animation_retarget_batch(task_id=taskId)
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_verify_animation_retarget_batch", exc, read_only=True)

    @server.tool(annotations=action_annotations)
    def ue_rollback_animation_retarget_batch(
        taskId: str,
        mode: str = "DryRun",
        rollbackDryRunReceipt: str = "",
        confirmation: str = "",
    ) -> dict[str, Any]:
        """Roll back a retarget batch: restore overwritten outputs from the pre-batch Backup and delete newly created outputs."""
        try:
            _assert_retarget_policy_capability(
                policy_path=workflow_service.config.policy_path,
                tool_name="ue_rollback_animation_retarget_batch",
            )
            return workflow_service.rollback_animation_retarget_batch(
                task_id=taskId,
                mode=mode,
                rollback_dry_run_receipt=rollbackDryRunReceipt,
                confirmation=confirmation,
            )
        except (LiveEditorError, FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            return error_response("ue_rollback_animation_retarget_batch", exc, read_only=False)

from __future__ import annotations

import re
import sqlite3
from typing import Any, Literal

from .agent_workflow import MATERIAL_PARAMETER_OPERATIONS, PatchWorkflowService, WorkflowError
from .mcp_consolidated_tools import (
    register_animation_scale_fix_tool,
    register_asset_property_tool,
    register_blueprint_session_tool,
    register_datatable_tool,
    register_live_write_tool,
    register_patch_tool,
)
from .animation_scale_fix_batch import AnimationScaleFixBatchService
from .batch_recovery import BatchRecoveryService
from .bounded_batch import BoundedBatchService
from .checkpoint_sets import CheckpointSetService

_MAX_NODE_MOVE_COORDINATE = 1_000_000
_MAX_NODE_MOVE_BATCH = 128
_NODE_MOVE_ENTRY_FIELDS = frozenset({"nodeGuid", "expectedX", "expectedY", "x", "y"})
_CANONICAL_GUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
)
# Two concatenated lowercase hex GUIDs; the Bridge owns the value and this
# wrapper only proves its shape before forwarding it.
_CANONICAL_EDIT_SESSION_TOKEN = re.compile(r"[0-9a-f]{64}")


def _is_exact_game_object_path(value: object) -> bool:
    """True only for one exact /Game Package.Object Object Path."""
    if not isinstance(value, str) or not value.startswith("/Game/"):
        return False
    if len(value) > 512 or "\\" in value or ":" in value or ".." in value:
        return False
    if any(ord(character) < 32 for character in value):
        return False
    package_path, separator, object_name = value.rpartition(".")
    if not separator or not object_name or "/" in object_name:
        return False
    if "." in package_path:
        # A second dot means an Object Path sub-path, not one exact asset.
        return False
    return package_path.startswith("/Game/") and not package_path.endswith("/")


def _normalize_node_move_batch(nodes: object) -> list[dict[str, Any]]:
    """Validate one exact node-move batch without touching the Editor."""
    if not isinstance(nodes, list):
        raise WorkflowError(
            "node-move-batch-invalid-parameters",
            "nodes must be a list of node move entries.",
        )
    if not nodes:
        raise WorkflowError(
            "node-move-batch-invalid-parameters",
            "nodes must contain at least one entry.",
        )
    if len(nodes) > _MAX_NODE_MOVE_BATCH:
        raise WorkflowError(
            "node-move-batch-limit-exceeded",
            f"nodes must contain at most {_MAX_NODE_MOVE_BATCH} entries.",
        )
    normalized: list[dict[str, Any]] = []
    seen_guids: set[str] = set()
    for index, entry in enumerate(nodes):
        if not isinstance(entry, dict) or set(entry) != _NODE_MOVE_ENTRY_FIELDS:
            raise WorkflowError(
                "node-move-batch-invalid-parameters",
                f"nodes[{index}] must contain exactly nodeGuid, expectedX, "
                "expectedY, x, y.",
            )
        node_guid = entry["nodeGuid"]
        if (
            not isinstance(node_guid, str)
            or _CANONICAL_GUID.fullmatch(node_guid) is None
        ):
            raise WorkflowError(
                "node-move-batch-invalid-parameters",
                f"nodes[{index}].nodeGuid must be one canonical lowercase "
                "hyphenated GUID.",
            )
        if node_guid in seen_guids:
            raise WorkflowError(
                "node-move-batch-duplicate-node",
                f"nodes[{index}] repeats nodeGuid {node_guid}.",
            )
        seen_guids.add(node_guid)
        for key in ("expectedX", "expectedY", "x", "y"):
            value = entry[key]
            if (
                type(value) is not int
                or abs(value) > _MAX_NODE_MOVE_COORDINATE
            ):
                raise WorkflowError(
                    "node-move-batch-coordinate-invalid",
                    f"nodes[{index}].{key} must be an integer in "
                    f"[-{_MAX_NODE_MOVE_COORDINATE}, {_MAX_NODE_MOVE_COORDINATE}].",
                )
        # Overlapping node positions are legal in a UE graph, so two entries may
        # legitimately share one destination; only duplicate GUIDs are invalid.
        normalized.append(
            {
                "nodeGuid": node_guid,
                "expectedX": entry["expectedX"],
                "expectedY": entry["expectedY"],
                "x": entry["x"],
                "y": entry["y"],
            }
        )
    return normalized


def register_workflow_tools(
    *,
    server: Any,
    workflow_service: PatchWorkflowService,
    read_annotations: Any,
    tool_annotations_type: Any,
    error_response: Any,
    handler_sink: dict[str, Any] | None = None,
) -> None:
    planning_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    dry_run_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    destructive_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=True,
        idempotentHint=False,
        openWorldHint=False,
    )

    animation_scale_fix_batch_service = AnimationScaleFixBatchService(workflow_service)
    bounded_batch_service = BoundedBatchService(workflow_service)
    checkpoint_set_service = CheckpointSetService(workflow_service, bounded_batch_service)
    batch_recovery_service = BatchRecoveryService(workflow_service, bounded_batch_service, checkpoint_set_service)

    # A0/P1: consolidated facades dispatch to these exact callables.  handlers is
    # filled at the end of registration; facades resolve it lazily per call.
    handlers: dict[str, Any] = handler_sink if handler_sink is not None else {}

    def _captured_error_response(
        tool_name: str,
        error: Exception,
        *,
        read_only: bool,
        asset_paths: tuple[str, ...] = (),
        change_set_id: str = "",
        target_identity: str = "",
    ) -> dict[str, Any]:
        response = error_response(tool_name, error, read_only=read_only)
        details = getattr(error, "details", {})
        durable_failure = bool(
            isinstance(details, dict)
            and {
                "batchExecutionId",
                "checkpointId",
                "checkpointSetId",
                "recoveryId",
            }.intersection(details)
        )
        if not durable_failure:
            capture_rejection = getattr(
                workflow_service,
                "capture_memory_l0_rejection",
                None,
            )
            if callable(capture_rejection):
                capture = capture_rejection(
                    operation=tool_name,
                    error=error,
                    asset_paths=asset_paths,
                    change_set_id=change_set_id,
                    target_identity=target_identity,
                )
                if capture is not None:
                    response["memoryCapture"] = capture
        return response

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
            return error_response("ue_set_blueprint_default", exc, read_only=False)

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
            return error_response("ue_set_component_property", exc, read_only=False)

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
            return error_response("ue_set_pin_default", exc, read_only=False)

    # A0/P1: one tool replaces the six asset-property tools.
    register_asset_property_tool(
        server=server,
        handlers=handlers,
        annotations=destructive_annotations,
        error_response=error_response,
    )

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
            return error_response("ue_set_asset_property", exc, read_only=False)

    # A0/P1: one tool replaces the nine animation-scale-fix tools.
    register_animation_scale_fix_tool(
        server=server,
        handlers=handlers,
        annotations=destructive_annotations,
        error_response=error_response,
    )

    @server.tool(annotations=planning_annotations)
    def ue_plan_animation_scale_fix(
        asset_path: str,
        root_bone: str,
        expected_final_scale: float,
        force_root_lock: bool | None = None,
        enable_root_motion: bool | None = None,
        use_normalized_root_motion_scale: bool | None = None,
        root_motion_root_lock: Literal["RefPose", "AnimFirstFrame", "Zero"] | None = None,
        root_track_scale_mode: Literal["Keep", "ReferenceLocal", "Uniform"] = "Keep",
        uniform_scale: float | None = None,
        final_scale_tolerance: float | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Create a Live-only plan that repairs one AnimSequence Root Scale path and verifies final evaluated scale."""
        value: dict[str, Any] = {
            "rootTrackScaleMode": root_track_scale_mode,
            "expectedFinalScale": expected_final_scale,
        }
        optional_values = {
            "forceRootLock": force_root_lock,
            "enableRootMotion": enable_root_motion,
            "useNormalizedRootMotionScale": use_normalized_root_motion_scale,
            "rootMotionRootLock": root_motion_root_lock,
            "uniformScale": uniform_scale,
            "finalScaleTolerance": final_scale_tolerance,
        }
        value.update({key: item for key, item in optional_values.items() if item is not None})
        try:
            return _run_high_level_change(
                tool_name="ue_plan_animation_scale_fix",
                mode="Plan",
                asset_path=asset_path,
                operation="setAnimationScaleFix",
                target={"rootBone": root_bone},
                value=value,
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_plan_animation_scale_fix", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_plan_additive_base_pose_fix_apply(
        asset_path: str,
        ref_sequence_path: str,
        ref_frame_index: int,
        additive_anim_type: Literal["None", "LocalSpaceBase", "RotationOffsetMeshSpace"] | None = None,
        additive_base_pose_type: Literal[
            "None", "ReferencePose", "AnimationScaled", "AnimationFrame", "LocalAnimationFrame"
        ] | None = None,
        expected_combined_root_scale: float | None = None,
        combined_scale_tolerance: float | None = None,
        root_bone: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Plan a policy-authorized Additive Base Pose fix that replaces RefPoseSeq and RefFrameIndex and verifies the combined Root Scale."""
        value: dict[str, Any] = {
            "refSequencePath": ref_sequence_path,
            "refFrameIndex": ref_frame_index,
        }
        optional_values = {
            "additiveAnimType": additive_anim_type,
            "additiveBasePoseType": additive_base_pose_type,
            "expectedCombinedRootScale": expected_combined_root_scale,
            "combinedScaleTolerance": combined_scale_tolerance,
            "rootBone": root_bone,
        }
        value.update({key: item for key, item in optional_values.items() if item is not None})
        try:
            return _run_high_level_change(
                tool_name="ue_plan_additive_base_pose_fix_apply",
                mode="Plan",
                asset_path=asset_path,
                operation="setAdditiveBasePoseFix",
                target={},
                value=value,
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_plan_additive_base_pose_fix_apply", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_plan_animation_scale_fix_batch(
        audit_task_id: str,
        audit_report_id: str,
        asset_paths: list[str],
        expected_final_scale_overrides: dict[str, float] | None = None,
        final_scale_tolerance: float | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        """Create an immutable multi-asset scale-fix Plan from one fixed WorkRoot Audit Report without touching Editor memory."""
        try:
            response = animation_scale_fix_batch_service.plan(
                audit_task_id=audit_task_id,
                audit_report_id=audit_report_id,
                asset_paths=asset_paths,
                expected_final_scale_overrides=expected_final_scale_overrides,
                final_scale_tolerance=final_scale_tolerance,
                description=description,
            )
            return {
                **response,
                "tool": "ue_plan_animation_scale_fix_batch",
                "readOnly": False,
                "nextStep": "Review the immutable Batch Plan, then call ue_apply_animation_scale_fix_batch_live with exact confirmation when ready.",
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_plan_animation_scale_fix_batch", exc, read_only=False)

    @server.tool(annotations=read_annotations)
    def ue_get_animation_scale_fix_batch(batch_plan_id: str) -> dict[str, Any]:
        """Return one immutable animation scale-fix Batch Plan created in the current MCP session."""
        try:
            response = animation_scale_fix_batch_service.get(batch_plan_id=batch_plan_id)
            return {
                **response,
                "tool": "ue_get_animation_scale_fix_batch",
                "readOnly": True,
                "nextStep": "Inspect execution state. Start or continue bounded Live Apply, or Undo already applied items when needed.",
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_get_animation_scale_fix_batch", exc, read_only=True)

    @server.tool(annotations=destructive_annotations)
    def ue_apply_animation_scale_fix_batch_live(
        batch_plan_id: str,
        confirmation: str = "",
        batch_apply_receipt: str = "",
        max_assets: int = 1,
    ) -> dict[str, Any]:
        """Start or continue bounded Live Apply for an immutable animation scale-fix Batch Plan."""
        try:
            response = animation_scale_fix_batch_service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=confirmation,
                batch_apply_receipt=batch_apply_receipt,
                max_assets=max_assets,
            )
            return {
                **response,
                "tool": "ue_apply_animation_scale_fix_batch_live",
                "readOnly": False,
                "nextStep": "Use ue_get_animation_scale_fix_batch to inspect state. Continue with batchApplyReceipt or Undo applied items if needed.",
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_apply_animation_scale_fix_batch_live", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_save_animation_scale_fix_batch(
        batch_plan_id: str,
        confirmation: str = "",
        batch_save_receipt: str = "",
        max_assets: int = 1,
    ) -> dict[str, Any]:
        """Start or continue bounded authorized Save for changed items in an animation scale-fix Batch."""
        try:
            response = animation_scale_fix_batch_service.save(
                batch_plan_id=batch_plan_id,
                confirmation=confirmation,
                batch_save_receipt=batch_save_receipt,
                max_assets=max_assets,
            )
            return {
                **response,
                "tool": "ue_save_animation_scale_fix_batch",
                "readOnly": False,
                "nextStep": "Continue with batchSaveReceipt until state is saved, then call ue_verify_animation_scale_fix_batch.",
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_save_animation_scale_fix_batch", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_verify_animation_scale_fix_batch(
        batch_plan_id: str,
        batch_verify_receipt: str = "",
        max_assets: int = 1,
    ) -> dict[str, Any]:
        """Start or continue bounded independent persisted-value verification for a saved scale-fix Batch."""
        try:
            response = animation_scale_fix_batch_service.verify(
                batch_plan_id=batch_plan_id,
                batch_verify_receipt=batch_verify_receipt,
                max_assets=max_assets,
            )
            return {
                **response,
                "tool": "ue_verify_animation_scale_fix_batch",
                "readOnly": False,
                "nextStep": "Continue with batchVerifyReceipt until state is verified; Index Refresh remains a separate explicit step.",
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_verify_animation_scale_fix_batch", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_refresh_animation_scale_fix_batch_index(
        batch_plan_id: str,
        mode: Literal["Preview", "Apply"] = "Preview",
        confirmation: str = "",
        batch_index_refresh_receipt: str = "",
        max_assets: int = 1,
    ) -> dict[str, Any]:
        """Prepare or atomically activate one paired snapshot generation for a verified scale-fix Batch."""
        try:
            response = animation_scale_fix_batch_service.refresh_index(
                batch_plan_id=batch_plan_id,
                mode=mode,
                confirmation=confirmation,
                batch_index_refresh_receipt=batch_index_refresh_receipt,
                max_assets=max_assets,
            )
            return {
                **response,
                "tool": "ue_refresh_animation_scale_fix_batch_index",
                "readOnly": False,
                "nextStep": (
                    "Continue Preview with batchIndexRefreshReceipt until state is index_refresh_ready; then Apply with confirmation "
                    "'REFRESH BATCH <batchPlanId>'."
                    if mode == "Preview"
                    else "Restart the MCP server after a successful Apply so the new paired snapshot becomes the frozen session snapshot."
                ),
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_refresh_animation_scale_fix_batch_index", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_rollback_animation_scale_fix_batch(
        batch_plan_id: str,
        mode: str = "DryRun",
        confirmation: str = "",
        batch_rollback_receipt: str = "",
        max_assets: int = 1,
    ) -> dict[str, Any]:
        """Dry-run or commit bounded reverse-order rollback for persisted animation scale-fix Batch items."""
        try:
            response = animation_scale_fix_batch_service.rollback(
                batch_plan_id=batch_plan_id,
                mode=mode,
                confirmation=confirmation,
                batch_rollback_receipt=batch_rollback_receipt,
                max_assets=max_assets,
            )
            return {
                **response,
                "tool": "ue_rollback_animation_scale_fix_batch",
                "readOnly": False,
                "nextStep": (
                    "Continue DryRun with batchRollbackReceipt until state is rollback_ready; then close the target Editor "
                    "and Commit with confirmation 'ROLLBACK BATCH <batchPlanId>'."
                    if mode == "DryRun"
                    else "Continue Commit with batchRollbackReceipt until state is rolled_back."
                ),
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_rollback_animation_scale_fix_batch", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_undo_animation_scale_fix_batch(
        batch_plan_id: str,
        confirmation: str = "",
        batch_undo_receipt: str = "",
        max_assets: int = 1,
    ) -> dict[str, Any]:
        """Start or continue bounded reverse-order Undo for a Live-applied animation scale-fix Batch Plan."""
        try:
            response = animation_scale_fix_batch_service.undo_live(
                batch_plan_id=batch_plan_id,
                confirmation=confirmation,
                batch_undo_receipt=batch_undo_receipt,
                max_assets=max_assets,
            )
            return {
                **response,
                "tool": "ue_undo_animation_scale_fix_batch",
                "readOnly": False,
                "nextStep": "Use ue_get_animation_scale_fix_batch to inspect Undo progress. Continue with batchUndoReceipt until state is undone.",
            }
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_undo_animation_scale_fix_batch", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_apply_asset_property_live(plan_id: str, confirmation: str, change_set_id: str = "") -> dict[str, Any]:
        """Apply one authorized setAssetProperty plan to Editor memory without saving the package."""
        try:
            return workflow_service.apply_asset_property_live(plan_id, confirmation, change_set_id=change_set_id)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_apply_asset_property_live",
                exc,
                read_only=False,
                change_set_id=change_set_id,
                target_identity=plan_id,
            )

    @server.tool(annotations=destructive_annotations)
    def ue_undo_asset_property_live(
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        """Undo exactly the most recent confirmed live write for one asset in Editor memory (redoable)."""
        try:
            return workflow_service.undo_asset_property_live(
                asset_path,
                transaction_id,
                editor_session_id,
                change_set_id=change_set_id,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_undo_asset_property_live",
                exc,
                read_only=False,
                asset_paths=(asset_path,),
                change_set_id=change_set_id,
                target_identity=transaction_id,
            )

    @server.tool(annotations=destructive_annotations)
    def ue_discard_asset_property_live(
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        """Discard the most recent confirmed live write for one asset back to its pre-write Editor state."""
        try:
            return workflow_service.discard_asset_property_live(
                asset_path,
                transaction_id,
                editor_session_id,
                change_set_id=change_set_id,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_discard_asset_property_live",
                exc,
                read_only=False,
                asset_paths=(asset_path,),
                change_set_id=change_set_id,
                target_identity=transaction_id,
            )

    @server.tool(annotations=planning_annotations)
    def ue_set_asset_reference_property(
        asset_path: str,
        property_path: str,
        value: Any,
        mode: Literal["Plan", "DryRun"] = "Plan",
        description: str = "",
    ) -> dict[str, Any]:
        """Plan or Dry Run one authorized Data Asset Object/Class or soft-reference change."""
        try:
            return _run_high_level_change(
                tool_name="ue_set_asset_reference_property",
                mode=mode,
                asset_path=asset_path,
                operation="setAssetReferenceProperty",
                target={"propertyPath": property_path},
                value=value,
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_set_asset_reference_property", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_set_asset_structured_property(
        asset_path: str,
        property_path: str,
        value: Any,
        mode: Literal["Plan", "DryRun"] = "Plan",
        description: str = "",
    ) -> dict[str, Any]:
        """Plan or Dry Run one authorized Data Asset Struct, Array, Set, or Map change."""
        try:
            return _run_high_level_change(
                tool_name="ue_set_asset_structured_property",
                mode=mode,
                asset_path=asset_path,
                operation="setAssetStructuredProperty",
                target={"propertyPath": property_path},
                value=value,
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_set_asset_structured_property", exc, read_only=False)

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
            return error_response("ue_set_material_parameter", exc, read_only=False)

    # A0/P1: one tool replaces the five per-verb DataTable row tools.
    register_datatable_tool(
        server=server,
        workflow_service=workflow_service,
        planning_annotations=planning_annotations,
        error_response=error_response,
    )

    # A0/P2: one tool replaces plan/dry_run/apply/verify/rollback plus the
    # asset-state helpers.
    register_patch_tool(
        server=server,
        handlers=handlers,
        annotations=destructive_annotations,
        error_response=error_response,
    )

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
            return error_response("ue_plan_patch", exc, read_only=False)

    # A0/P2: one tool replaces the six live-write tools.
    register_live_write_tool(
        server=server,
        handlers=handlers,
        annotations=destructive_annotations,
        error_response=error_response,
    )

    @server.tool(annotations=planning_annotations)
    def ue_plan_live_write_batch(
        assets: list[dict[str, Any]],
        description: str = "",
    ) -> dict[str, Any]:
        """Create one read-only, policy-gated bounded batch plan for W4 supported operations."""
        try:
            return bounded_batch_service.plan(assets=assets, description=description)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_plan_live_write_batch", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_apply_live_write_batch(
        batch_plan_id: str,
        confirmation: str,
        change_set_id: str,
    ) -> dict[str, Any]:
        """Apply one immutable one-asset W4 Batch Plan through the existing resident Writer."""
        try:
            return bounded_batch_service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation=confirmation,
                change_set_id=change_set_id,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_apply_live_write_batch",
                exc,
                read_only=False,
                change_set_id=change_set_id,
                target_identity=batch_plan_id,
            )

    @server.tool(annotations=destructive_annotations)
    def ue_save_change_set_checkpoint(
        batch_execution_id: str,
        mode: Literal["Preview", "Commit", "Get"],
        confirmation: str = "",
    ) -> dict[str, Any]:
        """Preview, Commit, or Get an aggregate checkpoint-set for one W4 Batch Execution.

        In Preview mode `batch_execution_id` is a W4 Batch Execution id.
        In Commit/Get mode `batch_execution_id` is the checkpoint-set id returned by Preview.
        """
        try:
            if mode == "Preview":
                return checkpoint_set_service.preview(batch_execution_id=batch_execution_id)
            if mode == "Get":
                return checkpoint_set_service.get(checkpoint_set_id=batch_execution_id)
            return checkpoint_set_service.commit(checkpoint_set_id=batch_execution_id, confirmation=confirmation)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_save_change_set_checkpoint",
                exc,
                read_only=False,
                target_identity=batch_execution_id,
            )

    @server.tool(annotations=read_annotations)
    def ue_verify_change_set_checkpoint(checkpoint_set_id: str) -> dict[str, Any]:
        """Run aggregate Strong Verify / Semantic Diff / Verification Plan / Trust for one saved W4 Checkpoint Set."""
        try:
            return checkpoint_set_service.verify(checkpoint_set_id=checkpoint_set_id)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_verify_change_set_checkpoint",
                exc,
                read_only=True,
                target_identity=checkpoint_set_id,
            )

    @server.tool(annotations=destructive_annotations)
    def ue_recover_live_write_batch(
        batch_execution_id: str,
        mode: Literal["Preview", "Commit", "Get"],
        confirmation: str = "",
    ) -> dict[str, Any]:
        """Preview, Commit, or Get durable recovery for one W4 Batch Execution.

        In Preview/Get mode `batch_execution_id` is the Batch Execution or Recovery id.
        In Commit mode `batch_execution_id` is the Recovery id returned by Preview.
        """
        try:
            if mode == "Preview":
                return batch_recovery_service.preview(batch_execution_id=batch_execution_id)
            if mode == "Get":
                return batch_recovery_service.get(recovery_id=batch_execution_id)
            return batch_recovery_service.commit(recovery_id=batch_execution_id, confirmation=confirmation)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_recover_live_write_batch",
                exc,
                read_only=False,
                target_identity=batch_execution_id,
            )

    @server.tool(annotations=dry_run_annotations)
    def ue_dry_run_patch(plan_id: str) -> dict[str, Any]:
        """Run the stored plan through Unreal, restore memory state, and require unchanged disk Revision."""
        try:
            return workflow_service.dry_run_patch(plan_id)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_dry_run_patch", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_apply_patch(
        plan_id: str,
        dry_run_receipt: str,
        confirmation: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        """Explicitly commit a plan using a fresh one-time Dry Run receipt and exact confirmation phrase."""
        try:
            return workflow_service.apply_patch(plan_id, dry_run_receipt, confirmation, change_set_id=change_set_id)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_apply_patch", exc, read_only=False)

    from .mcp_retarget_tools import register_retarget_workflow_tools

    register_retarget_workflow_tools(
        server=server,
        workflow_service=workflow_service,
        tool_annotations_type=tool_annotations_type,
        error_response=error_response,
    )

    @server.tool(annotations=planning_annotations)
    def ue_verify_asset(apply_receipt: str, change_set_id: str = "") -> dict[str, Any]:
        """Independently reload the committed asset in Unreal and verify its saved SHA-256 Revision."""
        try:
            return workflow_service.verify_asset(apply_receipt, change_set_id=change_set_id)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_verify_asset", exc, read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_verify_live_write(
        asset_path: str,
        live_apply_receipt: str = "",
        change_set_id: str = "",
    ) -> dict[str, Any]:
        """Verify one exact live write, or the latest pending write for the asset when no receipt is supplied."""
        try:
            return workflow_service.verify_live_write(
                asset_path,
                live_apply_receipt,
                change_set_id=change_set_id,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_verify_live_write", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_verify_live_write_fast(
        asset_path: str,
        live_apply_receipt: str = "",
        change_set_id: str = "",
    ) -> dict[str, Any]:
        """Verify one exact pending live write against the current Editor session without any independent reload or Commandlet."""
        try:
            return workflow_service.verify_live_write_fast(
                asset_path,
                live_apply_receipt,
                change_set_id=change_set_id,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_verify_live_write_fast", exc, read_only=True)

    @server.tool(annotations=read_annotations)
    def ue_verify_live_write_checkpoint(
        checkpoint_id: str,
        change_set_id: str = "",
        asset_path: str = "",
    ) -> dict[str, Any]:
        """Run exactly one independent export to verify all effective writes bound to a saved asset checkpoint."""
        try:
            return workflow_service.verify_live_write_checkpoint(
                checkpoint_id,
                change_set_id=change_set_id,
                asset_path=asset_path,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_verify_live_write_checkpoint",
                exc,
                read_only=True,
                asset_paths=(asset_path,) if asset_path else (),
                change_set_id=change_set_id,
                target_identity=checkpoint_id,
            )

    @server.tool(annotations=read_annotations)
    def ue_get_asset_state(asset_path: str) -> dict[str, Any]:
        """Compare Editor memory, disk Package, Revision Export, and frozen SQLite state for one exact asset."""
        try:
            return workflow_service.get_asset_state(asset_path)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_get_asset_state", exc, read_only=True)

    @server.tool(annotations=planning_annotations)
    def ue_refresh_asset_index(
        asset_path: str,
        mode: Literal["Preview", "Apply"] = "Preview",
    ) -> dict[str, Any]:
        """Preview or atomically activate one policy-authorized paired SQLite and Revision Export generation."""
        try:
            return workflow_service.refresh_asset_index(asset_path, mode=mode)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_refresh_asset_index", exc, read_only=False)

    # A0/P2: one tool replaces the begin/end edit-session pair.
    register_blueprint_session_tool(
        server=server,
        handlers=handlers,
        annotations=destructive_annotations,
        error_response=error_response,
    )

    @server.tool(annotations=destructive_annotations)
    def ue_begin_blueprint_edit_session(
        asset_path: str,
        graph_guid: str,
        experimental: bool,
    ) -> dict[str, Any]:
        """Begin an experimental continuous Agent edit session on one clean, focused Blueprint graph; supports repeated token batches and position-only rollback, never saves, compiles, or blocks human editing."""
        try:
            if not workflow_service.config.commit_enabled:
                raise WorkflowError(
                    "blueprint-edit-session-disabled",
                    "Blueprint edit sessions require explicit startup authorization "
                    "of Commit tools.",
                )
            if workflow_service.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Blueprint edit sessions require the connected, running Editor Bridge.",
                )
            if not _is_exact_game_object_path(asset_path):
                raise WorkflowError(
                    "blueprint-edit-session-invalid-parameters",
                    "asset_path must be one exact /Game Package.Object Object Path.",
                )
            if (
                not isinstance(graph_guid, str)
                or _CANONICAL_GUID.fullmatch(graph_guid) is None
            ):
                raise WorkflowError(
                    "blueprint-edit-session-invalid-parameters",
                    "graph_guid must be one canonical lowercase hyphenated GUID.",
                )
            if type(experimental) is not bool:
                raise WorkflowError(
                    "blueprint-edit-session-invalid-parameters",
                    "experimental must be a boolean.",
                )
            if not experimental:
                # Continuous editing still has no UI lock, so it must be an
                # explicit per-task opt-in even though position-only rollback
                # exists for the same unsaved session.
                raise WorkflowError(
                    "blueprint-edit-session-experimental-opt-in-required",
                    "Continuous Blueprint editing is experimental and has no "
                    "human/UI edit lock; pass experimental=True and do not edit "
                    "the graph until the session is ended. Rollback is limited to "
                    "node positions this session moved while the package is unsaved.",
                )
            result = workflow_service.live_editor_service.call_method(
                "editor.beginBlueprintEditSession",
                {
                    "assetPath": asset_path,
                    "graphGuid": graph_guid,
                    "experimentalContinuous": True,
                },
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_begin_blueprint_edit_session",
                "ok": True,
                "mode": "ResidentEditor",
                "readOnly": False,
                "childUnrealProcessCount": 0,
                "saved": False,
                "compiled": False,
                "exclusiveUI": False,
                "humanEditingBlocked": False,
                "result": result,
            }
        except (WorkflowError, ValueError, RuntimeError, OSError) as exc:
            return error_response(
                "ue_begin_blueprint_edit_session", exc, read_only=False
            )

    @server.tool(annotations=destructive_annotations)
    def ue_end_blueprint_edit_session(
        edit_session_token: str,
        mode: Literal["keep", "rollback"] = "keep",
    ) -> dict[str, Any]:
        """End an Agent edit session without saving: mode=keep only releases ownership, mode=rollback restores this session's node positions in one transaction when every safety proof still holds."""
        try:
            if not workflow_service.config.commit_enabled:
                raise WorkflowError(
                    "blueprint-edit-session-disabled",
                    "Blueprint edit sessions require explicit startup authorization "
                    "of Commit tools.",
                )
            if workflow_service.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Blueprint edit sessions require the connected, running Editor Bridge.",
                )
            if (
                not isinstance(edit_session_token, str)
                or _CANONICAL_EDIT_SESSION_TOKEN.fullmatch(edit_session_token)
                is None
            ):
                raise WorkflowError(
                    "blueprint-edit-session-token-invalid",
                    "edit_session_token must be the exact 64-character lowercase "
                    "hex token returned by begin.",
                )
            if mode not in ("keep", "rollback"):
                raise WorkflowError(
                    "blueprint-edit-session-invalid-parameters",
                    "mode must be keep or rollback.",
                )
            result = workflow_service.live_editor_service.call_method(
                "editor.endBlueprintEditSession",
                {
                    "editSessionToken": edit_session_token,
                    "mode": mode,
                },
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_end_blueprint_edit_session",
                "ok": True,
                "mode": "ResidentEditor",
                "readOnly": False,
                "childUnrealProcessCount": 0,
                "saved": False,
                "compiled": False,
                "exclusiveUI": False,
                "humanEditingBlocked": False,
                "result": result,
            }
        except (WorkflowError, ValueError, RuntimeError, OSError) as exc:
            return error_response(
                "ue_end_blueprint_edit_session", exc, read_only=False
            )

    @server.tool(annotations=destructive_annotations)
    def ue_move_blueprint_node(
        asset_path: str,
        graph_guid: str,
        node_guid: str,
        x: int,
        y: int,
        expected_x: int,
        expected_y: int,
        expected_dirty: bool,
    ) -> dict[str, Any]:
        """Move one already-open, clean Blueprint node by GUID; never save, compile, or launch Unreal."""
        try:
            if not workflow_service.config.commit_enabled:
                raise WorkflowError(
                    "node-move-disabled",
                    "Node movement requires explicit startup authorization of Commit tools.",
                )
            if workflow_service.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Node movement requires the connected, running Editor Bridge.",
                )
            if type(expected_dirty) is not bool:
                raise WorkflowError(
                    "node-move-invalid-parameters",
                    "expected_dirty must be a boolean.",
                )
            if expected_dirty:
                raise WorkflowError(
                    "node-move-dirty-ownership-unproven",
                    "Only the first clean-to-dirty move is supported; the Bridge cannot prove "
                    "that this session owns an already-dirty package. Save or revert the "
                    "Blueprint, then move from a clean package.",
                )
            if not _is_exact_game_object_path(asset_path):
                raise WorkflowError(
                    "node-move-invalid-parameters",
                    "asset_path must be one exact /Game Package.Object Object Path.",
                )
            for label, value in (("graph_guid", graph_guid), ("node_guid", node_guid)):
                if not isinstance(value, str) or _CANONICAL_GUID.fullmatch(value) is None:
                    raise WorkflowError(
                        "node-move-invalid-parameters",
                        f"{label} must be one canonical lowercase hyphenated GUID.",
                    )
            for label, value in (
                ("x", x),
                ("y", y),
                ("expected_x", expected_x),
                ("expected_y", expected_y),
            ):
                if (
                    type(value) is not int
                    or abs(value) > _MAX_NODE_MOVE_COORDINATE
                ):
                    raise WorkflowError(
                        "node-move-coordinate-invalid",
                        f"{label} must be an integer in "
                        f"[-{_MAX_NODE_MOVE_COORDINATE}, {_MAX_NODE_MOVE_COORDINATE}].",
                    )
            result = workflow_service.live_editor_service.call_method(
                "editor.moveBlueprintNode",
                {
                    "assetPath": asset_path,
                    "graphGuid": graph_guid,
                    "nodeGuid": node_guid,
                    "x": x,
                    "y": y,
                    "expectedX": expected_x,
                    "expectedY": expected_y,
                    "expectedDirty": expected_dirty,
                },
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_move_blueprint_node",
                "ok": True,
                "mode": "ResidentEditor",
                "readOnly": False,
                "childUnrealProcessCount": 0,
                "saved": False,
                "compiled": False,
                "result": result,
            }
        except (WorkflowError, ValueError, RuntimeError, OSError) as exc:
            return error_response("ue_move_blueprint_node", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_move_blueprint_nodes(
        asset_path: str,
        graph_guid: str,
        nodes: list[dict[str, Any]],
        edit_session_token: str = "",
    ) -> dict[str, Any]:
        """Atomically move up to 128 nodes in one open Blueprint graph; a valid edit-session token allows repeated batches on one Dirty graph. Never saves, compiles, or launches Unreal."""
        try:
            if not workflow_service.config.commit_enabled:
                raise WorkflowError(
                    "node-move-disabled",
                    "Node movement requires explicit startup authorization of Commit tools.",
                )
            if workflow_service.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Node movement requires the connected, running Editor Bridge.",
                )
            if not _is_exact_game_object_path(asset_path):
                raise WorkflowError(
                    "node-move-batch-invalid-parameters",
                    "asset_path must be one exact /Game Package.Object Object Path.",
                )
            if (
                not isinstance(graph_guid, str)
                or _CANONICAL_GUID.fullmatch(graph_guid) is None
            ):
                raise WorkflowError(
                    "node-move-batch-invalid-parameters",
                    "graph_guid must be one canonical lowercase hyphenated GUID.",
                )
            if edit_session_token:
                if (
                    not isinstance(edit_session_token, str)
                    or _CANONICAL_EDIT_SESSION_TOKEN.fullmatch(edit_session_token)
                    is None
                ):
                    raise WorkflowError(
                        "blueprint-edit-session-token-invalid",
                        "edit_session_token must be the exact 64-character "
                        "lowercase hex token returned by begin.",
                    )
            normalized = _normalize_node_move_batch(nodes)
            params: dict[str, Any] = {
                "assetPath": asset_path,
                "graphGuid": graph_guid,
                "nodes": normalized,
            }
            if edit_session_token:
                params["editSessionToken"] = edit_session_token
            result = workflow_service.live_editor_service.call_method(
                "editor.moveBlueprintNodes",
                params,
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_move_blueprint_nodes",
                "ok": True,
                "mode": "ResidentEditor",
                "readOnly": False,
                "childUnrealProcessCount": 0,
                "saved": False,
                "compiled": False,
                "result": result,
            }
        except (WorkflowError, ValueError, RuntimeError, OSError) as exc:
            return error_response("ue_move_blueprint_nodes", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_save_authorized_asset(
        asset_path: str,
        mode: Literal["Preview", "Commit"] = "Preview",
        save_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
        verification_mode: Literal["immediate", "checkpoint", "resident"] = "resident",
    ) -> dict[str, Any]:
        """Save via the resident Editor by default; independent reload requires explicit immediate mode."""
        try:
            return workflow_service.save_authorized_asset(
                asset_path,
                mode=mode,
                save_receipt=save_receipt,
                confirmation=confirmation,
                change_set_id=change_set_id,
                verification_mode=verification_mode,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return _captured_error_response(
                "ue_save_authorized_asset",
                exc,
                read_only=False,
                asset_paths=(asset_path,),
                change_set_id=change_set_id,
                target_identity=save_receipt,
            )

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
            return error_response("ue_rollback_patch", exc, read_only=mode == "DryRun")

    # Every per-verb tool above is a local callable; the facades dispatch to
    # these exact objects, including ones a profile kept out of registration.
    handlers.update(
        {
            name: value
            for name, value in list(locals().items())
            if name.startswith("ue_") and callable(value)
        }
    )

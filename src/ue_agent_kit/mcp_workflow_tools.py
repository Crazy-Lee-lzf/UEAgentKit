from __future__ import annotations

import sqlite3
from typing import Any, Literal

from .agent_workflow import MATERIAL_PARAMETER_OPERATIONS, PatchWorkflowService, WorkflowError


def register_workflow_tools(
    *,
    server: Any,
    workflow_service: PatchWorkflowService,
    read_annotations: Any,
    tool_annotations_type: Any,
    error_response: Any,
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

    @server.tool(annotations=destructive_annotations)
    def ue_apply_asset_property_live(plan_id: str, confirmation: str, change_set_id: str = "") -> dict[str, Any]:
        """Apply one authorized setAssetProperty plan to Editor memory without saving the package."""
        try:
            return workflow_service.apply_asset_property_live(plan_id, confirmation, change_set_id=change_set_id)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_apply_asset_property_live", exc, read_only=False)

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
            return error_response("ue_undo_asset_property_live", exc, read_only=False)

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
            return error_response("ue_discard_asset_property_live", exc, read_only=False)

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
            return error_response("ue_set_datatable_cell", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_set_datatable_row_fields(
        asset_path: str,
        row_name: str,
        values: dict[str, Any],
        mode: Literal["Plan", "DryRun"] = "Plan",
        description: str = "",
    ) -> dict[str, Any]:
        """Plan or Dry Run one atomic authorized update to fields in an existing DataTable row."""
        try:
            return _run_high_level_change(
                tool_name="ue_set_datatable_row_fields",
                mode=mode,
                asset_path=asset_path,
                operation="setDataTableRowFields",
                target={"rowName": row_name},
                value=values,
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_set_datatable_row_fields", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_add_datatable_row(
        asset_path: str,
        row_name: str,
        values: dict[str, Any] | None = None,
        mode: Literal["Plan", "DryRun"] = "Plan",
        description: str = "",
    ) -> dict[str, Any]:
        """Plan or Dry Run one authorized DataTable row creation."""
        try:
            return _run_high_level_change(
                tool_name="ue_add_datatable_row",
                mode=mode,
                asset_path=asset_path,
                operation="addDataTableRow",
                target={"rowName": row_name},
                value=values or {},
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_add_datatable_row", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_remove_datatable_row(
        asset_path: str,
        row_name: str,
        mode: Literal["Plan", "DryRun"] = "Plan",
        description: str = "",
    ) -> dict[str, Any]:
        """Plan or Dry Run one authorized DataTable row removal."""
        try:
            return _run_high_level_change(
                tool_name="ue_remove_datatable_row",
                mode=mode,
                asset_path=asset_path,
                operation="removeDataTableRow",
                target={"rowName": row_name},
                value=True,
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_remove_datatable_row", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_rename_datatable_row(
        asset_path: str,
        row_name: str,
        new_row_name: str,
        mode: Literal["Plan", "DryRun"] = "Plan",
        description: str = "",
    ) -> dict[str, Any]:
        """Plan or Dry Run one authorized DataTable row rename."""
        try:
            return _run_high_level_change(
                tool_name="ue_rename_datatable_row",
                mode=mode,
                asset_path=asset_path,
                operation="renameDataTableRow",
                target={"rowName": row_name, "newRowName": new_row_name},
                value=True,
                description=description,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_rename_datatable_row", exc, read_only=False)

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

    @server.tool(annotations=dry_run_annotations)
    def ue_dry_run_patch(plan_id: str) -> dict[str, Any]:
        """Run the stored plan through Unreal, restore memory state, and require unchanged disk Revision."""
        try:
            return workflow_service.dry_run_patch(plan_id)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_dry_run_patch", exc, read_only=False)

    @server.tool(annotations=destructive_annotations)
    def ue_apply_patch(plan_id: str, dry_run_receipt: str, confirmation: str) -> dict[str, Any]:
        """Explicitly commit a plan using a fresh one-time Dry Run receipt and exact confirmation phrase."""
        try:
            return workflow_service.apply_patch(plan_id, dry_run_receipt, confirmation)
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_apply_patch", exc, read_only=False)

    @server.tool(annotations=planning_annotations)
    def ue_verify_asset(apply_receipt: str) -> dict[str, Any]:
        """Independently reload the committed asset in Unreal and verify its saved SHA-256 Revision."""
        try:
            return workflow_service.verify_asset(apply_receipt)
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

    @server.tool(annotations=destructive_annotations)
    def ue_save_authorized_asset(
        asset_path: str,
        mode: Literal["Preview", "Commit"] = "Preview",
        save_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
    ) -> dict[str, Any]:
        """Preview or explicitly save one policy-authorized loaded Dirty asset with backup and verification."""
        try:
            return workflow_service.save_authorized_asset(
                asset_path,
                mode=mode,
                save_receipt=save_receipt,
                confirmation=confirmation,
                change_set_id=change_set_id,
            )
        except (WorkflowError, FileNotFoundError, OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            return error_response("ue_save_authorized_asset", exc, read_only=False)

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

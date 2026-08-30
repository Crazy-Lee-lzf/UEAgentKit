from __future__ import annotations

import secrets
import shutil
from pathlib import Path
from typing import Any
from .workflow_common import (
    LIVE_WRITE_JOURNAL_SCHEMA_VERSION,
    LiveApplyRecord,
    LiveWriteCheckpointRecord,
    WORKFLOW_SCHEMA_VERSION,
    WorkflowError,
    _is_guid_with_hyphens,
    _live_write_expected_exported_value,
    _live_write_exported_matches,
    _live_write_exported_value,
    _live_write_memory_task_evidence,
    _live_write_runtime_verification,
    _live_write_value_kind,
    _read_json,
    _report_id,
)
from .change_sets import (
    MAX_CHANGE_SET_RECEIPTS,
)
from .patches import (
    LIVE_WRITE_OPERATION_REGISTRY,
)
from .snapshot_lifecycle import (
    sha256_file,
    utc_now_iso,
)

def _write_json_atomic(*args: Any, **kwargs: Any) -> Any:
    from . import agent_workflow as _agent_workflow_compat
    return _agent_workflow_compat._write_json_atomic(*args, **kwargs)


class WorkflowLiveMixin:
    """D1 workflow split mixin/base; method bodies are pure moves from agent_workflow.py."""

    def _live_write_journal_root(self) -> Path:
        return self._safe_work_path("live-write-journal")


    @staticmethod
    def _validate_live_apply_receipt(receipt: str) -> str:
        if (
            not isinstance(receipt, str)
            or not receipt.startswith("live_")
            or len(receipt) > 96
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in receipt)
        ):
            raise WorkflowError("live-write-receipt-invalid", "liveApplyReceipt is not a valid internal receipt.")
        return receipt


    def _live_write_journal_path(self, receipt: str) -> Path:
        receipt = self._validate_live_apply_receipt(receipt)
        return self._live_write_journal_root() / f"{receipt}.json"


    def _serialize_live_apply_record(self, record: LiveApplyRecord) -> dict[str, Any]:
        return {
            "schemaVersion": LIVE_WRITE_JOURNAL_SCHEMA_VERSION,
            "projectName": self.project_name,
            "receipt": record.receipt,
            "planId": record.plan_id,
            "planDigest": record.plan_digest,
            "assetPath": record.asset_path,
            "operation": record.operation,
            "valueKind": record.value_kind,
            "editorSessionId": record.editor_session_id,
            "transactionId": record.transaction_id,
            "beforeValue": record.before_value,
            "afterValue": record.after_value,
            "target": record.target,
            "appliedAtUtc": record.applied_at_utc,
            "saved": record.saved,
            "saveReceipt": record.save_receipt,
            "verified": record.verified,
            "checkpointId": record.checkpoint_id,
        }


    def _deserialize_live_apply_record(self, value: dict[str, Any], expected_receipt: str) -> LiveApplyRecord:
        if value.get("schemaVersion") != LIVE_WRITE_JOURNAL_SCHEMA_VERSION or value.get("projectName") != self.project_name:
            raise ValueError("journal identity mismatch")
        receipt = self._validate_live_apply_receipt(str(value.get("receipt", "")))
        if receipt != expected_receipt:
            raise ValueError("journal receipt mismatch")
        operation = str(value.get("operation", ""))
        spec = LIVE_WRITE_OPERATION_REGISTRY.get(operation)
        if spec is None or value.get("valueKind") != spec.live_write_value_kind:
            raise ValueError("journal operation mismatch")
        asset_path = self._validate_refresh_asset_path(str(value.get("assetPath", "")))
        target = value.get("target")
        if not isinstance(target, dict):
            raise ValueError("journal target invalid")
        for target_field in spec.target_fields:
            validator = spec.target_validators.get(target_field)
            if validator is None or not validator(target.get(target_field)):
                raise ValueError("journal target field invalid")
        transaction_id = str(value.get("transactionId", ""))
        editor_session_id = str(value.get("editorSessionId", ""))
        if not _is_guid_with_hyphens(transaction_id) or not editor_session_id:
            raise ValueError("journal editor identity invalid")
        plan_id = str(value.get("planId", ""))
        plan_digest = str(value.get("planDigest", ""))
        applied_at_utc = str(value.get("appliedAtUtc", ""))
        saved = value.get("saved")
        verified = value.get("verified")
        save_receipt = str(value.get("saveReceipt", ""))
        if not plan_id or not plan_digest.startswith("sha256:") or not applied_at_utc:
            raise ValueError("journal plan identity invalid")
        if not isinstance(saved, bool) or not isinstance(verified, bool) or verified:
            raise ValueError("journal lifecycle invalid")
        if saved and not save_receipt.startswith("save_"):
            raise ValueError("journal save identity invalid")
        return LiveApplyRecord(
            receipt=receipt,
            plan_id=plan_id,
            plan_digest=plan_digest,
            asset_path=asset_path,
            operation=operation,
            value_kind=spec.live_write_value_kind,
            editor_session_id=editor_session_id,
            transaction_id=transaction_id,
            before_value=value.get("beforeValue"),
            after_value=value.get("afterValue"),
            target=dict(target),
            applied_at_utc=applied_at_utc,
            saved=saved,
            save_receipt=save_receipt,
            verified=False,
            checkpoint_id=str(value.get("checkpointId", "")),
        )


    def _record_live_write_journal_error(self, receipt: str) -> None:
        if receipt not in self._live_write_journal_errors:
            self._live_write_journal_errors.append(receipt)


    def _persist_live_apply(self, record: LiveApplyRecord) -> bool:
        try:
            _write_json_atomic(self._live_write_journal_path(record.receipt), self._serialize_live_apply_record(record))
        except (OSError, TypeError, ValueError):
            self._record_live_write_journal_error(record.receipt)
            return False
        if record.receipt in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(record.receipt)
        return True


    def _delete_live_apply_journal(self, receipt: str) -> bool:
        path = self._live_write_journal_path(receipt)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            self._record_live_write_journal_error(receipt)
            return False
        if receipt in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(receipt)
        return True


    def _rebuild_live_apply_index(self) -> None:
        latest: dict[str, tuple[str, str]] = {}
        for receipt, record in self._live_applies.items():
            current = latest.get(record.asset_path)
            if current is None or (record.applied_at_utc, receipt) > current:
                latest[record.asset_path] = (record.applied_at_utc, receipt)
        self._live_apply_by_asset = {asset_path: receipt for asset_path, (_, receipt) in latest.items()}


    def _load_live_write_journal(self) -> None:
        root = self._live_write_journal_root()
        if not root.is_dir():
            return
        for path in sorted(root.glob("live_*.json")):
            try:
                value = _read_json(path)
                record = self._deserialize_live_apply_record(value, path.stem)
            except (WorkflowError, OSError, ValueError):
                self._live_write_journal_errors.append(path.stem)
                continue
            self._live_applies[record.receipt] = record
            self._live_write_recovered_count += 1
        self._prune_records()


    def _remove_live_apply(self, receipt: str) -> None:
        self._live_applies.pop(receipt, None)
        self._delete_live_apply_journal(receipt)
        self._rebuild_live_apply_index()


    def _resolve_live_apply(self, asset_path: str, live_apply_receipt: str = "") -> tuple[str, LiveApplyRecord]:
        if live_apply_receipt:
            receipt = self._validate_live_apply_receipt(live_apply_receipt)
        else:
            receipt = self._live_apply_by_asset.get(asset_path, "")
        record = self._live_applies.get(receipt)
        if record is None or record.asset_path != asset_path:
            raise WorkflowError(
                "live-write-verify-not-found",
                "No matching confirmed live write is pending for this asset.",
            )
        return receipt, record


    def apply_asset_property_live(self, plan_id: str, confirmation: str, change_set_id: str = "") -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Live Editor writes require Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Live Editor mode is required for an in-editor asset property write.",
                )
            if change_set_id:
                change_set = self._resolve_change_set(change_set_id)
                self._reconcile_change_set(change_set, persist=True)
                if change_set.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
                    raise WorkflowError(
                        "change-set-closed",
                        f"The Change Set is in {change_set.status} state and cannot accept another live write.",
                    )
                if len(change_set.operations) >= MAX_CHANGE_SET_RECEIPTS:
                    raise WorkflowError(
                        "change-set-full",
                        f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
                    )
                editor_available, current_session_id = self._current_editor_session()
                if (
                    change_set.editor_session_id
                    and editor_available
                    and current_session_id != change_set.editor_session_id
                ):
                    raise WorkflowError(
                        "change-set-editor-session-mismatch",
                        "The Change Set belongs to a different Editor session.",
                    )
            record = self._plans.get(plan_id)
            if record is None:
                raise WorkflowError("plan-not-found", "The live write plan is not active in this MCP server session.")
            if confirmation != f"LIVE APPLY {plan_id}":
                raise WorkflowError(
                    "live-editor-write-confirmation-required",
                    "Live write confirmation did not exactly match the required planId phrase.",
                )
            validation = self._validate_plan_file(record)
            if not validation.get("commitAllowedByPolicy"):
                raise WorkflowError("live-editor-write-not-allowed", "The fixed Policy does not enable this write.")
            assets = record.patch.get("assets", [])
            if not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
                raise WorkflowError("plan-invalid", "The live write plan no longer contains exactly one asset.")
            operations = assets[0].get("operations", [])
            if not isinstance(operations, list) or len(operations) != 1 or not isinstance(operations[0], dict):
                raise WorkflowError("plan-invalid", "The live write plan no longer contains exactly one operation.")
            operation = operations[0]
            operation_name = str(operation.get("operation", ""))
            operation_spec = LIVE_WRITE_OPERATION_REGISTRY.get(operation_name)
            if operation_spec is None:
                supported = ", ".join(sorted(LIVE_WRITE_OPERATION_REGISTRY))
                raise WorkflowError(
                    "live-editor-write-operation-unsupported",
                    f"Unsupported Live Editor write operation. Registered operations: {supported}.",
                )
            target = operation.get("target", {})
            if not isinstance(target, dict):
                raise WorkflowError("plan-invalid", "The live write plan target must be an object.")
            bridge_parameters: dict[str, Any] = {
                "operation": operation_name,
                "assetPath": str(assets[0].get("assetPath", "")),
                "target": target,
                "value": operation.get("value"),
            }
            for target_field in operation_spec.target_fields:
                target_value = target.get(target_field)
                validator = operation_spec.target_validators.get(target_field)
                if validator is None or not validator(target_value):
                    raise WorkflowError(
                        "plan-invalid",
                        f"The live write plan has no valid exact {target_field}.",
                    )
                bridge_parameters[target_field] = target_value
            property_path = target.get("propertyPath")
            parameter_name = target.get("parameterName")
            row_name = target.get("rowName")
            new_row_name = target.get("newRowName")
            field_name = target.get("fieldName")
            asset_path = str(assets[0].get("assetPath", ""))
            expected_revision = str(assets[0].get("expectedRevision", ""))
            bridge_parameters["assetPath"] = asset_path
            if change_set_id and change_set.operations:
                previous_operation = change_set.operations[-1]
                if (
                    previous_operation.asset_path == asset_path
                    and previous_operation.status == "applied"
                    and previous_operation.transaction_id
                ):
                    bridge_parameters["previousTransactionId"] = previous_operation.transaction_id
            try:
                live_result = self.live_editor_service.call_method(
                    "editor.applyAssetPropertyLive",
                    bridge_parameters,
                )
            except Exception as exc:
                if hasattr(exc, "code"):
                    raise WorkflowError(str(exc.code), str(exc), details=getattr(exc, "details", {})) from exc
                raise
            changed = bool(live_result.get("changed"))
            live_apply_receipt = ""
            change_set_operation_id = ""
            change_set_bound = False
            change_set_journal_persisted = True
            if changed:
                live_apply_receipt = "live_" + secrets.token_urlsafe(16)
                self._live_applies[live_apply_receipt] = LiveApplyRecord(
                    receipt=live_apply_receipt,
                    plan_id=plan_id,
                    plan_digest=record.digest,
                    asset_path=asset_path,
                    operation=operation_name,
                    value_kind=_live_write_value_kind(operation_name),
                    editor_session_id=str(live_result.get("editorSessionId", "")),
                    transaction_id=str(live_result.get("transactionId", "")),
                    before_value=live_result.get("beforeValue"),
                    after_value=live_result.get("afterValue"),
                    target=target,
                    applied_at_utc=utc_now_iso(),
                )
                self._live_apply_by_asset[asset_path] = live_apply_receipt
                journal_persisted = self._persist_live_apply(self._live_applies[live_apply_receipt])
                if change_set_id:
                    change_set_bound = True
                    change_set_journal_persisted = self._bind_apply_operation(
                        change_set_id, self._live_applies[live_apply_receipt]
                    )
                    change_set_operation_id = live_apply_receipt
                self._prune_records()
            else:
                journal_persisted = False
                if change_set_id:
                    change_set_operation_id, change_set_journal_persisted = self._bind_noop_operation(
                        change_set_id, record, asset_path, operation_name, live_result
                    )
                    change_set_bound = True
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_apply_asset_property_live",
                "ok": True,
                "mode": "LiveApply",
                "planId": plan_id,
                "patchDigest": record.digest,
                "projectName": self.project_name,
                "assetPath": asset_path,
                "expectedDiskRevision": expected_revision,
                "operation": operation_name,
                "valueKind": _live_write_value_kind(operation_name),
                "propertyPath": property_path,
                "parameterName": parameter_name,
                "rowName": row_name,
                "newRowName": new_row_name,
                "fieldName": field_name,
                "changed": changed,
                "saved": False,
                "diskRevisionChanged": False,
                "undoAvailableInEditor": bool(live_result.get("transactionRecorded")),
                "liveApplyReceipt": live_apply_receipt,
                "journalPersisted": journal_persisted,
                "result": live_result,
                "nextStep": (
                    "Verify, Save, or Undo the in-editor change. To persist it, preview ue_save_authorized_asset for this exact asset."
                    if changed
                    else "No value change was required."
                ),
                "nextActions": (
                    [
                        {
                            "tool": "ue_save_authorized_asset",
                            "arguments": {
                                "asset_path": asset_path,
                                "mode": "Preview",
                                **({"change_set_id": change_set_id} if change_set_id else {}),
                            },
                            "reason": "Authorize persistence for the exact live-written asset.",
                        }
                    ]
                    if changed
                    else []
                ),
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetBound"] = change_set_bound
                response["changeSetOperationId"] = change_set_operation_id
                response["changeSetJournalPersisted"] = change_set_journal_persisted
            if not self._memory_l0_capture_enabled():
                return response
            artifacts: list[dict[str, Any]] = []
            if changed and journal_persisted:
                artifacts.append(
                    {
                        "artifact_path": self._live_write_journal_path(
                            live_apply_receipt
                        ),
                        "event_kind": "live_write",
                        "lifecycle_state": "applied",
                        "outcome": "success",
                        "asset_paths": (asset_path,),
                        "change_set_id": change_set_id,
                        "details": {
                            "operation": operation_name,
                            "liveApplyReceipt": live_apply_receipt,
                        },
                    }
                )
            change_set_artifact = self.memory_l0_change_set_artifact(
                change_set_id
            )
            if change_set_artifact is not None:
                artifacts.append(change_set_artifact)
            self.capture_memory_l0_artifacts(artifacts, response=response)
            return response


    def undo_asset_property_live(
        self,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        return self._revert_asset_property_live(
            "undo",
            asset_path,
            transaction_id,
            editor_session_id,
            change_set_id,
        )


    def discard_asset_property_live(
        self,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        return self._revert_asset_property_live(
            "discard",
            asset_path,
            transaction_id,
            editor_session_id,
            change_set_id,
        )


    def _revert_asset_property_live(
        self,
        action: str,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Live Editor write reverts require Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Live Editor mode is required to revert an in-editor asset property write.",
                )
            asset_path = self._validate_refresh_asset_path(asset_path)
            if (
                not isinstance(transaction_id, str)
                or len(transaction_id) != 36
                or not _is_guid_with_hyphens(transaction_id)
            ):
                raise WorkflowError(
                    "live-editor-write-undo-invalid-transaction-id",
                    "transactionId must be the exact transactionId returned by the confirmed live write.",
                )
            if not isinstance(editor_session_id, str) or not editor_session_id:
                raise WorkflowError(
                    "live-editor-write-undo-session-required",
                    "editorSessionId must be the exact editorSessionId returned by the confirmed live write.",
                )
            receipt = next(
                (
                    candidate_receipt
                    for candidate_receipt, candidate in self._live_applies.items()
                    if candidate.asset_path == asset_path
                    and candidate.transaction_id == transaction_id
                    and candidate.editor_session_id == editor_session_id
                ),
                "",
            )
            if change_set_id:
                self._assert_change_set_member(change_set_id, receipt)
            try:
                live_result = self.live_editor_service.call_method(
                    f"editor.{action}AssetPropertyLive",
                    {
                        "assetPath": asset_path,
                        "transactionId": transaction_id,
                        "sessionId": editor_session_id,
                    },
                )
            except Exception as exc:
                if hasattr(exc, "code"):
                    raise WorkflowError(str(exc.code), str(exc), details=getattr(exc, "details", {})) from exc
                raise
            change_set_updated = False
            change_set_operation_status = "undone" if action == "undo" else "discarded"
            if receipt:
                if change_set_id:
                    change_set_updated = self._update_change_set_operation(
                        change_set_id,
                        receipt,
                        change_set_operation_status,
                    )
                self._remove_live_apply(receipt)
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": f"ue_{action}_asset_property_live",
                "ok": True,
                "mode": "LiveUndo" if action == "undo" else "LiveDiscard",
                "assetPath": asset_path,
                "transactionId": transaction_id,
                "editorSessionId": editor_session_id,
                "operation": live_result.get("operation"),
                "valueKind": live_result.get("valueKind"),
                "changed": bool(live_result.get("changed")),
                "saved": False,
                "diskRevisionChanged": False,
                "result": live_result,
                "nextStep": (
                    "The live write was reverted in Editor memory without saving the package. "
                    "Re-plan the write to re-apply it."
                ),
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
                response["changeSetOperationStatus"] = change_set_operation_status
            if not self._memory_l0_capture_enabled():
                return response
            change_set_artifact = self.memory_l0_change_set_artifact(
                change_set_id
            )
            self.capture_memory_l0_artifacts(
                [change_set_artifact] if change_set_artifact is not None else [],
                response=response,
            )
            return response


    def _fast_verify_live_record(self, record: LiveApplyRecord) -> dict[str, Any]:
        bridge_parameters: dict[str, Any] = {
            "operation": record.operation,
            "assetPath": record.asset_path,
            "target": record.target,
        }
        try:
            inspection = self.live_editor_service.call_method(
                "editor.verifyAssetPropertyLiveFast",
                bridge_parameters,
            )
        except Exception as exc:
            raise WorkflowError(
                "live-fast-verify-failed",
                "The Editor could not complete Fast Resident Verify.",
                details=getattr(exc, "details", {}),
            ) from exc
        result = inspection if isinstance(inspection, dict) else {}
        if not isinstance(result, dict) or result.get("targetResolved") is not True:
            raise WorkflowError(
                "live-fast-verify-target-not-found",
                "Fast Resident Verify could not re-resolve the exact target in the current Editor session.",
            )
        bridge_session = str(result.get("editorSessionId", ""))
        if bridge_session != record.editor_session_id:
            raise WorkflowError(
                "live-fast-verify-session-mismatch",
                "The current Editor session does not match the live write session; resident evidence is no longer applicable.",
                details={"expected_session": record.editor_session_id, "actual_session": bridge_session},
            )
        actual_value = result.get("value")
        if not _live_write_exported_matches(record.after_value, actual_value):
            raise WorkflowError(
                "live-fast-verify-value-mismatch",
                "The current resident value does not match the live write after-value.",
                details={"expectedValue": record.after_value, "actualValue": actual_value},
            )
        return result


    def verify_live_write_fast(self, asset_path: str, live_apply_receipt: str = "", change_set_id: str = "") -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Fast Resident Verify requires Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Live Editor mode is required to run Fast Resident Verify.",
                )
            asset_path = self._validate_refresh_asset_path(asset_path)
            receipt, record = self._resolve_live_apply(asset_path, live_apply_receipt)
            if change_set_id:
                self._assert_change_set_member(change_set_id, receipt)

            bridge_parameters: dict[str, Any] = {
                "operation": record.operation,
                "assetPath": asset_path,
                "target": record.target,
            }
            try:
                inspection = self.live_editor_service.call_method(
                    "editor.verifyAssetPropertyLiveFast",
                    bridge_parameters,
                )
            except Exception as exc:
                raise WorkflowError(
                    "live-fast-verify-failed",
                    "The Editor could not complete Fast Resident Verify.",
                    details=getattr(exc, "details", {}),
                ) from exc
            result = inspection if isinstance(inspection, dict) else {}
            if not isinstance(result, dict) or result.get("targetResolved") is not True:
                raise WorkflowError(
                    "live-fast-verify-target-not-found",
                    "Fast Resident Verify could not re-resolve the exact target in the current Editor session.",
                )
            bridge_session = str(result.get("editorSessionId", ""))
            if bridge_session != record.editor_session_id:
                raise WorkflowError(
                    "live-fast-verify-session-mismatch",
                    "The current Editor session does not match the live write session; resident evidence is no longer applicable.",
                    details={"expected_session": record.editor_session_id, "actual_session": bridge_session},
                )
            actual_value = result.get("value")
            value_matched = _live_write_exported_matches(record.after_value, actual_value)
            if not value_matched:
                raise WorkflowError(
                    "live-fast-verify-value-mismatch",
                    "The current resident value does not match the live write after-value.",
                    details={"expectedValue": record.after_value, "actualValue": actual_value},
                )

            package_dirty = bool(result.get("packageDirty", False))
            compile_required = bool(result.get("compileRequired", False))
            compile_attempted = bool(result.get("compileAttempted", False))
            compile_succeeded = bool(result.get("compileSucceeded", False))
            response: dict[str, Any] = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_verify_live_write_fast",
                "ok": True,
                "mode": "FastResidentVerify",
                "status": "success",
                "verificationKind": "resident-fast",
                "verified": True,
                "assetPath": asset_path,
                "operation": record.operation,
                "valueKind": record.value_kind,
                "target": record.target,
                "expectedValue": record.after_value,
                "actualValue": actual_value,
                "editorSessionId": bridge_session,
                "editorProcessId": result.get("editorProcessId"),
                "changeSetId": change_set_id,
                "liveApplyReceipt": receipt,
                "transactionId": record.transaction_id,
                "packageDirty": package_dirty,
                "compileRequired": compile_required,
                "compileAttempted": compile_attempted,
                "compileSucceeded": compile_succeeded,
                "targetResolved": True,
                "valueMatched": True,
                "transactionApplicable": bool(record.transaction_id),
                "changeSetApplicable": bool(change_set_id),
                "validationAttempted": False,
                "validationSucceeded": False,
                "failureCode": "",
                "saved": record.saved,
                "diskRevisionChanged": False,
                "nextAction": "authorized-save-at-checkpoint" if not record.saved else "continue-resident-editing",
                "nextActions": [],
            }
            if change_set_id:
                response["changeSetUpdated"] = False
            return response


    def _capture_checkpoint_failure_l0(
        self,
        checkpoint: LiveWriteCheckpointRecord,
        *,
        lifecycle_state: str | None = None,
        outcome: str | None = None,
    ) -> dict[str, Any] | None:
        if not self._memory_l0_capture_enabled():
            return None
        state = lifecycle_state or checkpoint.state
        artifacts: list[dict[str, Any]] = [
            {
                "artifact_path": self._checkpoint_journal_path(checkpoint.checkpoint_id),
                "event_kind": "checkpoint",
                "lifecycle_state": state,
                "outcome": outcome or self.memory_l0_outcome(state),
                "asset_paths": (checkpoint.asset_path,),
                "change_set_id": checkpoint.change_set_id,
                "details": {
                    "checkpointId": checkpoint.checkpoint_id,
                    "effectiveOperationCount": len(checkpoint.effective_receipts),
                },
            }
        ]
        change_set_artifact = self.memory_l0_change_set_artifact(checkpoint.change_set_id)
        if change_set_artifact is not None:
            artifacts.append(change_set_artifact)
        return self.capture_memory_l0_artifacts(artifacts)


    def verify_live_write_checkpoint(
        self,
        checkpoint_id: str,
        change_set_id: str = "",
        asset_path: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Live Editor write checkpoint verification requires Commit tools to be enabled when the MCP server starts.",
                )
            checkpoint = self._resolve_checkpoint(checkpoint_id)
            if change_set_id and checkpoint.change_set_id != change_set_id:
                raise WorkflowError(
                    "checkpoint-invalid",
                    "The supplied changeSetId does not match the checkpoint binding.",
                )
            if asset_path and checkpoint.asset_path != asset_path:
                raise WorkflowError(
                    "checkpoint-invalid",
                    "The supplied assetPath does not match the checkpoint binding.",
                )
            if checkpoint.state == "verified":
                return self._checkpoint_verified_response(checkpoint, child_unreal_process_count=0)
            if checkpoint.state != "saved":
                raise WorkflowError(
                    "checkpoint-not-saved",
                    "A Strong Checkpoint Verify requires a saved checkpoint.",
                    details={"checkpointState": checkpoint.state},
                )

            package_file = self._package_file(
                self.config.project_path,
                checkpoint.package_name,
                checkpoint.asset_class,
            )
            current_disk_revision = "sha256:" + sha256_file(package_file)
            if current_disk_revision != checkpoint.after_disk_revision:
                checkpoint.state = "stale"
                self._persist_checkpoint(checkpoint)
                memory_capture = self._capture_checkpoint_failure_l0(checkpoint)
                details = {
                    "checkpointId": checkpoint.checkpoint_id,
                    "expectedRevision": checkpoint.after_disk_revision,
                    "actualRevision": current_disk_revision,
                }
                if memory_capture is not None:
                    details["memoryCapture"] = memory_capture
                raise WorkflowError(
                    "checkpoint-revision-stale",
                    "The current disk Package Revision no longer matches the saved checkpoint Revision.",
                    details=details,
                )

            output = self._safe_work_path("W3CheckpointVerify", checkpoint.checkpoint_id)
            if output.exists():
                shutil.rmtree(output)
            output.mkdir(parents=True, exist_ok=False)
            asset_package = checkpoint.asset_path.split(".", 1)[0]
            blueprint_operations = {"setVariableDefault", "setComponentProperty", "setPinDefault"}
            is_blueprint_checkpoint = "Blueprint" in checkpoint.asset_class or any(
                operation.get("operation") in blueprint_operations
                for operation in checkpoint.effective_operations
            )
            if is_blueprint_checkpoint:
                verify_script = "RunExport.ps1"
                verify_arguments = [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Asset", asset_package,
                    "-Output", str(output),
                    "-Profile", "full",
                    "-Format", "json",
                    "-IncludeUnchangedDefaults",
                ]
            else:
                verify_script = "RunAssetCatalog.ps1"
                verify_arguments = [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Asset", asset_package,
                    "-Output", str(output),
                ]
            result = self._run_script(
                verify_script,
                verify_arguments,
                stage="checkpoint-verify-export",
                report_path=output / "manifest.json",
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="checkpoint-verify-export",
                    result=result,
                    report_path=output / "manifest.json",
                    fallback_code="checkpoint-export-failed",
                    fallback_message="The independent Unreal reload export failed for the checkpoint.",
                )
            canonical_files = list((output / "canonical").rglob("*.json"))
            if len(canonical_files) != 1:
                raise WorkflowError(
                    "checkpoint-export-invalid",
                    "Independent checkpoint reload did not produce exactly one Canonical asset.",
                )
            canonical_path = canonical_files[0]
            canonical = _read_json(canonical_path, stage="checkpoint-verify-canonical")
            revision = canonical.get("revision", {})
            canonical_revision = str(revision.get("value", "")) if isinstance(revision, dict) else ""
            if (
                canonical.get("projectName") != self.project_name
                or canonical.get("assetPath") != checkpoint.asset_path
                or canonical.get("packageName") != checkpoint.package_name
                or canonical.get("assetClass") != checkpoint.asset_class
                or not isinstance(revision, dict)
                or not revision.get("available")
                or revision.get("packageDirty")
                or not canonical_revision.startswith("sha256:")
            ):
                raise WorkflowError(
                    "checkpoint-export-invalid",
                    "The independent checkpoint artifact does not match the exact asset identity.",
                )
            if canonical_revision != checkpoint.after_disk_revision:
                checkpoint.state = "stale"
                self._persist_checkpoint(checkpoint)
                memory_capture = self._capture_checkpoint_failure_l0(checkpoint)
                details = {
                    "checkpointId": checkpoint.checkpoint_id,
                    "canonicalRevision": canonical_revision,
                    "expectedRevision": checkpoint.after_disk_revision,
                }
                if memory_capture is not None:
                    details["memoryCapture"] = memory_capture
                raise WorkflowError(
                    "checkpoint-revision-stale",
                    "The independent canonical Revision does not match the checkpoint afterRevision.",
                    details=details,
                )
            after_export_disk_revision = "sha256:" + sha256_file(package_file)
            if after_export_disk_revision != checkpoint.after_disk_revision:
                checkpoint.state = "stale"
                self._persist_checkpoint(checkpoint)
                memory_capture = self._capture_checkpoint_failure_l0(checkpoint)
                details = {"checkpointId": checkpoint.checkpoint_id}
                if memory_capture is not None:
                    details["memoryCapture"] = memory_capture
                raise WorkflowError(
                    "checkpoint-revision-stale",
                    "The disk Package Revision changed while the independent checkpoint export ran.",
                    details=details,
                )

            coverage: list[dict[str, Any]] = []
            for operation in checkpoint.effective_operations:
                record = self._restore_live_record_from_checkpoint_operation(checkpoint, operation)
                expected_value = _live_write_expected_exported_value(record)
                exported_value = _live_write_exported_value(canonical, record)
                matched = _live_write_exported_matches(expected_value, exported_value)
                coverage.append(
                    {
                        "receipt": operation["receipt"],
                        "operation": operation["operation"],
                        "target": operation["target"],
                        "expectedValue": expected_value,
                        "exportedValue": exported_value,
                        "matched": matched,
                    }
                )
            mismatches = [item for item in coverage if not item["matched"]]
            if mismatches:
                checkpoint.mismatch_diagnostics = mismatches
                self._persist_checkpoint(checkpoint)
                memory_capture = self._capture_checkpoint_failure_l0(
                    checkpoint,
                    lifecycle_state="value_mismatch",
                    outcome="failed",
                )
                details = {
                    "checkpointId": checkpoint.checkpoint_id,
                    "mismatches": mismatches,
                    "state": checkpoint.state,
                }
                if memory_capture is not None:
                    details["memoryCapture"] = memory_capture
                raise WorkflowError(
                    "checkpoint-value-mismatch",
                    "One or more effective checkpoint operations did not match the independently reloaded asset.",
                    details=details,
                )

            checkpoint.state = "verified"
            checkpoint.verified_at_utc = utc_now_iso()
            checkpoint.strong_verification_kind = "independent-verified"
            checkpoint.strong_verification_report_id = _report_id(
                "checkpoint-verify-export",
                output / "manifest.json",
            )
            checkpoint.strong_artifact_root = str(output)
            checkpoint.strong_artifact_revision = canonical_revision
            checkpoint.strong_artifact_digest = "sha256:" + sha256_file(canonical_path)
            checkpoint.child_unreal_process_count = 1
            checkpoint.mismatch_diagnostics = []
            checkpoint.verified_operation_coverage = coverage
            self._persist_checkpoint(checkpoint)

            change_set_updated = False
            for operation in checkpoint.effective_operations:
                receipt = str(operation["receipt"])
                change_set = self._change_sets.get(checkpoint.change_set_id)
                if change_set is not None:
                    change_set_updated = (
                        self._update_change_set_operation(
                            checkpoint.change_set_id,
                            receipt,
                            "verified",
                            save_receipt=checkpoint.save_receipt,
                            checkpoint_id=checkpoint.checkpoint_id,
                        )
                        or change_set_updated
                    )
                live_record = self._live_applies.get(receipt)
                if live_record is not None:
                    live_record.verified = True
                    live_record.checkpoint_id = checkpoint.checkpoint_id
                    self._persist_live_apply(live_record)
                    self._remove_live_apply(receipt)

            return self._checkpoint_verified_response(checkpoint, child_unreal_process_count=1)


    def _checkpoint_verified_response(
        self,
        checkpoint: LiveWriteCheckpointRecord,
        *,
        child_unreal_process_count: int,
    ) -> dict[str, Any]:
        response = {
            "schemaVersion": WORKFLOW_SCHEMA_VERSION,
            "tool": "ue_verify_live_write_checkpoint",
            "ok": True,
            "status": "success",
            "verificationKind": checkpoint.strong_verification_kind or "independent-verified",
            "checkpointId": checkpoint.checkpoint_id,
            "changeSetId": checkpoint.change_set_id,
            "assetPath": checkpoint.asset_path,
            "afterRevision": checkpoint.after_disk_revision,
            "independentReload": True,
            "verified": True,
            "effectiveOperationCount": len(checkpoint.effective_receipts),
            "verifiedOperationCount": len(checkpoint.verified_operation_coverage),
            "supersededOperationCount": len(checkpoint.superseded_receipts),
            "reportId": checkpoint.strong_verification_report_id,
            "artifactRevision": checkpoint.strong_artifact_revision,
            "artifactDigest": checkpoint.strong_artifact_digest,
            "childUnrealProcessCount": child_unreal_process_count,
            "perOperationCoverage": checkpoint.verified_operation_coverage,
            "nextStep": (
                "Call ue_analyze_semantic_diff at stage=verified, build the Verification Plan, "
                "close every Required assertion, and evaluate the scoped Trust verdict before refreshing the frozen index."
            ),
            "nextActions": [
                {
                    "tool": "ue_analyze_semantic_diff",
                    "arguments": {
                        "change_set_id": checkpoint.change_set_id,
                        "stage": "verified",
                    },
                    "reason": "Compare the independently verified checkpoint semantics before planning Trust obligations.",
                }
            ],
        }
        if not self._memory_l0_capture_enabled():
            return response
        artifacts = [
            {
                "artifact_path": self._checkpoint_journal_path(
                    checkpoint.checkpoint_id
                ),
                "event_kind": "checkpoint",
                "lifecycle_state": checkpoint.state,
                "outcome": self.memory_l0_outcome(checkpoint.state),
                "asset_paths": (checkpoint.asset_path,),
                "change_set_id": checkpoint.change_set_id,
                "details": {
                    "checkpointId": checkpoint.checkpoint_id,
                    "effectiveOperationCount": len(
                        checkpoint.effective_receipts
                    ),
                },
            }
        ]
        change_set_artifact = self.memory_l0_change_set_artifact(
            checkpoint.change_set_id
        )
        if change_set_artifact is not None:
            artifacts.append(change_set_artifact)
        self.capture_memory_l0_artifacts(artifacts, response=response)
        return response


    def verify_live_write(self, asset_path: str, live_apply_receipt: str = "", change_set_id: str = "") -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Live Editor write verification requires Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Live Editor mode is required to verify an in-editor asset property write.",
                )
            asset_path = self._validate_refresh_asset_path(asset_path)
            receipt, record = self._resolve_live_apply(asset_path, live_apply_receipt)
            if change_set_id:
                self._assert_change_set_member(change_set_id, receipt)
            try:
                inspection = self.live_editor_service.call_tool("ue_inspect_asset_live", {"assetPath": asset_path})
            except Exception as exc:
                raise WorkflowError("live-editor-status-unavailable", "The target asset could not be inspected before verification.") from exc
            result = inspection.get("result", {}) if isinstance(inspection, dict) else {}
            memory = result.get("memory", {}) if isinstance(result, dict) else {}
            if not isinstance(memory, dict) or memory.get("loaded") is not True:
                raise WorkflowError(
                    "live-editor-write-verify-not-loaded",
                    "The exact asset is no longer loaded in the Editor; re-open it before verification.",
                )
            if memory.get("packageDirty") is True:
                # The live write is still unsaved in Editor memory: the closed loop
                # must not fake success. Report the terminal not-saved state with
                # Undo/Discard still available and an unchanged Revision.
                freshness = self.freshness.inspect_asset(asset_path)
                current_revision = str(freshness.get("diskRevision", ""))
                report_id = f"live-write-not-saved:{receipt}"
                memory_task_evidence = _live_write_memory_task_evidence(
                    record,
                    state="not-saved",
                    conclusion=(
                        f"The live write {record.plan_id} on {asset_path} is still unsaved in "
                        f"Editor memory; the package is Dirty and Undo/Discard remain available. "
                        f"No disk Revision changed."
                    ),
                    outcome="cancelled",
                    revision=current_revision,
                    report_id=report_id,
                    undo_available=True,
                    independent_reload=False,
                )
                not_saved_response = {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_verify_live_write",
                    "ok": True,
                    "mode": "LiveVerify",
                    "state": "not-saved",
                    "assetPath": asset_path,
                    "planId": record.plan_id,
                    "patchDigest": record.plan_digest,
                    "operation": record.operation,
                    "valueKind": record.value_kind,
                    "liveApplyReceipt": receipt,
                    "transactionId": record.transaction_id,
                    "undoAvailable": True,
                    "saved": False,
                    "verified": False,
                    "diskRevision": current_revision,
                    "reportId": report_id,
                    "memoryTaskEvidence": memory_task_evidence,
                    "memoryRecorded": False,
                    "indexFreshness": freshness,
                    "nextStep": (
                        "The write is not persisted. Persist it with ue_save_authorized_asset "
                        "(Preview then Commit), or revert it with ue_undo_asset_property_live / "
                        "ue_discard_asset_property_live. A successful revert closes this pending live write."
                    ),
                    "nextActions": [
                        {
                            "tool": "ue_save_authorized_asset",
                            "arguments": {
                                "asset_path": asset_path,
                                "mode": "Preview",
                                **({"change_set_id": change_set_id} if change_set_id else {}),
                            },
                            "reason": "Persist the exact pending live write before independent verification.",
                        }
                    ],
                }
                if change_set_id:
                    not_saved_response["changeSetId"] = change_set_id
                return not_saved_response

            if not record.saved:
                raise WorkflowError(
                    "live-write-verify-save-unauthorized",
                    "The target package became clean without a confirmed authorized save; the "
                    "asset or Session state diverged after the live write. Re-plan the write.",
                    details={"liveApplyReceipt": receipt},
                )
            output = self._safe_work_path("verify-live-write", receipt)
            if output.exists():
                shutil.rmtree(output)
            output.mkdir(parents=True, exist_ok=False)
            asset_package = asset_path.split(".", 1)[0]
            is_blueprint_live_write = record.operation in {"setVariableDefault", "setComponentProperty", "setPinDefault"}
            if is_blueprint_live_write:
                verify_script = "RunExport.ps1"
                verify_arguments = [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Asset", asset_package,
                    "-Output", str(output),
                    "-Profile", "full",
                    "-Format", "json",
                    "-IncludeUnchangedDefaults",
                ]
            else:
                verify_script = "RunAssetCatalog.ps1"
                verify_arguments = [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Asset", asset_package,
                    "-Output", str(output),
                ]
            result = self._run_script(
                verify_script,
                verify_arguments,
                stage="live-write-verify-export",
                report_path=output / "manifest.json",
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="live-write-verify-export",
                    result=result,
                    report_path=output / "manifest.json",
                    fallback_code="live-write-verify-export-failed",
                    fallback_message="The independent Unreal reload export failed for the live write.",
                )
            canonical_files = list((output / "canonical").rglob("*.json"))
            if len(canonical_files) != 1:
                raise WorkflowError("live-write-verify-export-invalid", "Independent reload did not produce exactly one Canonical asset.")
            canonical = _read_json(canonical_files[0], stage="live-write-verify-canonical")
            revision = canonical.get("revision", {})
            actual_revision = revision.get("value", "") if isinstance(revision, dict) else ""
            if canonical.get("assetPath") != asset_path or not actual_revision.startswith("sha256:"):
                raise WorkflowError(
                    "live-write-verify-revision-mismatch",
                    "Independent Unreal reload did not match the live write target asset and Revision.",
                    details={"expectedAsset": asset_path, "actualAsset": canonical.get("assetPath", "")},
                )
            freshness = self.freshness.inspect_asset(asset_path)
            if str(freshness.get("diskRevision", "")) != actual_revision:
                raise WorkflowError(
                    "live-write-verify-revision-mismatch",
                    "The independent reload Revision does not match the current disk Package Revision.",
                    details={"diskRevision": freshness.get("diskRevision", ""), "actualRevision": actual_revision},
                )
            if str(freshness.get("indexRevision", "")) == actual_revision:
                raise WorkflowError(
                    "live-write-verify-revision-unchanged",
                    "The disk Package Revision is unchanged from the frozen index; the live write was not persisted.",
                )
            exported_value = _live_write_exported_value(canonical, record)
            expected_exported_value = _live_write_expected_exported_value(record)
            runtime_verification = _live_write_runtime_verification(record)
            if not _live_write_exported_matches(expected_exported_value, exported_value):
                raise WorkflowError(
                    "live-write-verify-value-mismatch",
                    "The independently reloaded asset value does not match the applied live write value; "
                    "the asset changed after the live write.",
                    details={
                        "persistedExpectedValue": expected_exported_value,
                        "exportedPersistedValue": exported_value,
                        "expectedValue": expected_exported_value,
                        "exportedValue": exported_value,
                    },
                )
            record.verified = True
            verification_report_id = _report_id("live-write-verify-export", output / "manifest.json")
            memory_task_evidence = _live_write_memory_task_evidence(
                record,
                state="verified",
                conclusion=(
                    f"The live write {record.plan_id} on {asset_path} was authorized-saved and "
                    f"independently reloaded at Revision {actual_revision}; the exported value "
                    f"matches the applied value and the live write is no longer undoable in the Editor."
                ),
                outcome="succeeded",
                revision=actual_revision,
                report_id=verification_report_id,
                undo_available=False,
                independent_reload=True,
            )
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_verify_live_write",
                "ok": True,
                "mode": "LiveVerify",
                "state": "verified",
                "assetPath": asset_path,
                "planId": record.plan_id,
                "patchDigest": record.plan_digest,
                "operation": record.operation,
                "valueKind": record.value_kind,
                "liveApplyReceipt": receipt,
                "transactionId": record.transaction_id,
                "undoAvailable": False,
                "saved": True,
                "verified": True,
                "appliedValue": record.after_value,
                "persistedExpectedValue": expected_exported_value,
                "exportedPersistedValue": exported_value,
                "runtimeVerification": runtime_verification,
                "expectedValue": expected_exported_value,
                "exportedValue": exported_value,
                "expectedDiskRevision": str(freshness.get("diskRevision", "")),
                "actualRevision": actual_revision,
                "assetClass": canonical.get("assetClass", ""),
                "packageDirty": False,
                "reportId": verification_report_id,
                "memoryTaskEvidence": memory_task_evidence,
                "memoryRecorded": False,
                "indexFreshness": freshness,
                "nextStep": (
                    "Call ue_analyze_semantic_diff at stage=verified, build the Verification Plan, close every "
                    "Required assertion, and evaluate the scoped Trust verdict before refreshing the frozen index."
                    if change_set_id
                    else "The persisted live write is independently verified. Bind future writes to a Change Set "
                    "to continue through Semantic Diff and scoped Trust evaluation."
                ),
                "nextActions": (
                    [
                        {
                            "tool": "ue_analyze_semantic_diff",
                            "arguments": {"change_set_id": change_set_id, "stage": "verified"},
                            "reason": "Compare independently verified semantics before planning Trust obligations.",
                        }
                    ]
                    if change_set_id
                    else []
                ),
            }
            if change_set_id:
                change_set_updated = self._update_change_set_operation(
                    change_set_id,
                    receipt,
                    "verified",
                    save_receipt=record.save_receipt,
                )
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
                response["changeSetOperationStatus"] = "verified"
            self._remove_live_apply(receipt)
            return response


    def _inspect_refresh_live_state(self, asset_path: str) -> dict[str, Any]:
        descriptor = self.config.project_path.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"
        if self.live_editor_service is None:
            if descriptor.is_file():
                raise WorkflowError(
                    "live-editor-status-required",
                    "An Editor Bridge descriptor exists, so safe refresh requires Live Editor mode to verify that the asset is not Dirty.",
                )
            return {"state": "offline", "loaded": False, "packageDirty": False}
        try:
            status = self.live_editor_service.status()
        except Exception as exc:
            raise WorkflowError("live-editor-status-unavailable", "Live Editor state could not be checked before snapshot refresh.") from exc
        if status.get("state") != "available":
            if descriptor.is_file():
                raise WorkflowError(
                    "live-editor-status-unavailable",
                    "The fixed Editor Bridge is not available, so the target Dirty state cannot be trusted.",
                )
            return {"state": "offline", "loaded": False, "packageDirty": False}
        try:
            payload = self.live_editor_service.call_tool("ue_inspect_asset_live", {"assetPath": asset_path})
        except Exception as exc:
            raise WorkflowError("live-editor-status-unavailable", "The target asset could not be inspected in the fixed Editor session.") from exc
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        memory = result.get("memory", {}) if isinstance(result, dict) else {}
        if not isinstance(memory, dict):
            memory = {}
        if memory.get("packageDirty") is True:
            raise WorkflowError(
                "live-editor-asset-dirty",
                "The target asset has unsaved Editor memory changes and cannot be added to a disk-backed snapshot.",
            )
        return {
            "state": str(memory.get("state", "unknown")),
            "loaded": bool(memory.get("loaded")),
            "packageDirty": bool(memory.get("packageDirty")),
        }


    def _inspect_rollback_live_state(self, asset_path: str) -> dict[str, Any]:
        descriptor = self.config.project_path.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"
        if self.live_editor_service is None:
            return {
                "state": "offline",
                "allowOpenEditor": False,
                "assetPath": asset_path,
            }
        try:
            status = self.live_editor_service.status()
        except Exception as exc:
            raise WorkflowError(
                "rollback-live-editor-status-unavailable",
                "Live Editor state could not be checked before rollback Commit.",
            ) from exc
        if not isinstance(status, dict) or status.get("state") != "available":
            if descriptor.is_file():
                raise WorkflowError(
                    "rollback-live-editor-status-unavailable",
                    "The fixed Editor Bridge descriptor exists but cannot prove the rollback target is unloaded and clean.",
                )
            return {
                "state": "offline",
                "allowOpenEditor": False,
                "assetPath": asset_path,
            }
        try:
            payload = self.live_editor_service.call_tool(
                "ue_inspect_asset_live",
                {"assetPath": asset_path},
            )
        except Exception as exc:
            raise WorkflowError(
                "rollback-live-editor-status-unavailable",
                "The rollback target could not be inspected in the fixed Editor session.",
            ) from exc
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        memory = result.get("memory", {}) if isinstance(result, dict) else {}
        registry = result.get("assetRegistry", {}) if isinstance(result, dict) else {}
        if not isinstance(memory, dict) or not isinstance(registry, dict) or registry.get("found") is not True:
            raise WorkflowError(
                "rollback-live-editor-status-unavailable",
                "The fixed Editor session did not return exact target asset state for rollback.",
            )
        if (
            not isinstance(memory.get("loaded"), bool)
            or not isinstance(memory.get("packageDirty"), bool)
            or not isinstance(memory.get("openInAssetEditor"), bool)
            or not isinstance(memory.get("state"), str)
        ):
            raise WorkflowError(
                "rollback-live-editor-status-unavailable",
                "The fixed Editor session did not explicitly prove loaded, dirty, and Asset Editor state for rollback.",
            )
        if memory.get("packageDirty") is True:
            raise WorkflowError(
                "rollback-live-editor-asset-dirty",
                "Rollback Commit is blocked because the exact target has unsaved Editor memory changes.",
            )
        if (
            memory.get("loaded") is not False
            or memory.get("openInAssetEditor") is not False
            or memory.get("state") != "not-loaded"
        ):
            raise WorkflowError(
                "rollback-live-editor-asset-loaded",
                "Rollback Commit while the project is open requires the exact target to be not-loaded and not open in an Asset Editor.",
            )
        return {
            "state": "verified-unloaded-clean",
            "allowOpenEditor": True,
            "assetPath": asset_path,
            "editorSessionId": str(status.get("sessionId", "")),
            "editorProcessId": int(status.get("processId", 0) or 0),
            "loaded": False,
            "packageDirty": False,
            "openInAssetEditor": False,
        }

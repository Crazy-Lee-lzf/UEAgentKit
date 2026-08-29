from __future__ import annotations

import hashlib
import secrets
import shutil
from pathlib import Path
from typing import Any, Literal
from .workflow_common import (
    AuthorizedSaveRollbackDryRunRecord,
    CHECKPOINT_RECORD_SCHEMA_VERSION,
    DEVELOPMENT_LINE,
    LiveApplyRecord,
    LiveWriteCheckpointRecord,
    SaveAuthorizationRecord,
    WORKFLOW_SCHEMA_VERSION,
    WorkflowError,
    _is_within,
    _json_bytes,
    _live_write_stable_target_key,
    _read_json,
    _report_id,
    _safe_report,
    _sha256_bytes,
)
from .backups import (
    create_backup_manifest,
    rollback_backup,
)
from .change_sets import (
    validate_change_set_id,
)
from .semantic_diff_workflow import (
    analyze_workflow_semantic_diff,
)
from .snapshot_lifecycle import (
    sha256_file,
    utc_now_iso,
)
from .verification_trust import (
    build_verification_plan,
    evaluate_trust_verdict,
)

def _write_json_atomic(*args: Any, **kwargs: Any) -> Any:
    from . import agent_workflow as _agent_workflow_compat
    return _agent_workflow_compat._write_json_atomic(*args, **kwargs)


class WorkflowVerifyMixin:
    """D1 workflow split mixin/base; method bodies are pure moves from agent_workflow.py."""

    def _checkpoint_journal_root(self) -> Path:
        return self._safe_work_path("checkpoints")


    @staticmethod
    def _validate_checkpoint_id(checkpoint_id: str) -> str:
        if (
            not isinstance(checkpoint_id, str)
            or not checkpoint_id.startswith("cp_")
            or len(checkpoint_id) <= 3
            or len(checkpoint_id) > 96
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in checkpoint_id)
        ):
            raise WorkflowError("checkpoint-invalid", "checkpointId is not a valid UEAgentKit checkpoint identifier.")
        return checkpoint_id


    def _checkpoint_journal_path(self, checkpoint_id: str) -> Path:
        checkpoint_id = self._validate_checkpoint_id(checkpoint_id)
        return self._checkpoint_journal_root() / f"{checkpoint_id}.json"


    @staticmethod
    def _serialize_checkpoint_record(record: LiveWriteCheckpointRecord) -> dict[str, Any]:
        return {
            "schemaVersion": CHECKPOINT_RECORD_SCHEMA_VERSION,
            "projectName": None,  # filled by instance method below
            "checkpointId": record.checkpoint_id,
            "changeSetId": record.change_set_id,
            "assetPath": record.asset_path,
            "assetClass": record.asset_class,
            "packageName": record.package_name,
            "state": record.state,
            "createdAtUtc": record.created_at_utc,
            "savedAtUtc": record.saved_at_utc,
            "verifiedAtUtc": record.verified_at_utc,
            "editorSessionIdAtPrepare": record.editor_session_id_at_prepare,
            "editorProcessIdAtPrepare": record.editor_process_id_at_prepare,
            "beforeDiskRevision": record.before_disk_revision,
            "afterDiskRevision": record.after_disk_revision,
            "saveReceipt": record.save_receipt,
            "backupManifestId": record.backup_manifest_id,
            "includedReceipts": list(record.included_receipts),
            "effectiveReceipts": list(record.effective_receipts),
            "supersededReceipts": list(record.superseded_receipts),
            "effectiveOperations": list(record.effective_operations),
            "effectiveOperationDigest": record.effective_operation_digest,
            "strongVerificationKind": record.strong_verification_kind,
            "strongVerificationReportId": record.strong_verification_report_id,
            "strongArtifactRoot": record.strong_artifact_root,
            "strongArtifactRevision": record.strong_artifact_revision,
            "strongArtifactDigest": record.strong_artifact_digest,
            "childUnrealProcessCount": record.child_unreal_process_count,
            "mismatchDiagnostics": list(record.mismatch_diagnostics),
            "verifiedOperationCoverage": list(record.verified_operation_coverage),
        }


    def _persist_checkpoint(self, record: LiveWriteCheckpointRecord) -> bool:
        payload = self._serialize_checkpoint_record(record)
        payload["projectName"] = self.project_name
        try:
            _write_json_atomic(
                self._checkpoint_journal_path(record.checkpoint_id),
                payload,
            )
        except (OSError, TypeError, ValueError):
            self._record_checkpoint_journal_error(record.checkpoint_id)
            return False
        if record.checkpoint_id in self._checkpoint_journal_errors:
            self._checkpoint_journal_errors.remove(record.checkpoint_id)
        return True


    def _record_checkpoint_journal_error(self, checkpoint_id: str) -> None:
        if checkpoint_id not in self._checkpoint_journal_errors:
            self._checkpoint_journal_errors.append(checkpoint_id)


    def _delete_checkpoint_journal(self, checkpoint_id: str) -> bool:
        path = self._checkpoint_journal_path(checkpoint_id)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            self._record_checkpoint_journal_error(checkpoint_id)
            return False
        if checkpoint_id in self._checkpoint_journal_errors:
            self._checkpoint_journal_errors.remove(checkpoint_id)
        return True


    def _deserialize_checkpoint_record(self, value: dict[str, Any], expected_id: str) -> LiveWriteCheckpointRecord:
        if (
            value.get("schemaVersion") != CHECKPOINT_RECORD_SCHEMA_VERSION
            or value.get("projectName") != self.project_name
        ):
            raise ValueError("checkpoint journal identity mismatch")
        checkpoint_id = self._validate_checkpoint_id(str(value.get("checkpointId", "")))
        if checkpoint_id != expected_id:
            raise ValueError("checkpoint journal id mismatch")
        change_set_id = validate_change_set_id(str(value.get("changeSetId", "")))
        asset_path = self._validate_refresh_asset_path(str(value.get("assetPath", "")))
        asset_class = str(value.get("assetClass", ""))
        package_name = str(value.get("packageName", ""))
        state = str(value.get("state", ""))
        created_at_utc = str(value.get("createdAtUtc", ""))
        if (
            not asset_class
            or not package_name
            or state not in {"prepared", "saved", "verified", "failed", "stale"}
            or not created_at_utc
        ):
            raise ValueError("checkpoint journal lifecycle invalid")
        included = [self._validate_live_apply_receipt(str(item)) for item in value.get("includedReceipts", [])]
        effective = [self._validate_live_apply_receipt(str(item)) for item in value.get("effectiveReceipts", [])]
        superseded = [self._validate_live_apply_receipt(str(item)) for item in value.get("supersededReceipts", [])]
        operations_value = value.get("effectiveOperations")
        if not isinstance(operations_value, list) or not all(isinstance(item, dict) for item in operations_value):
            raise ValueError("checkpoint effective operations invalid")
        save_receipt = str(value.get("saveReceipt", ""))
        after_disk_revision = str(value.get("afterDiskRevision", ""))
        before_disk_revision = str(value.get("beforeDiskRevision", ""))
        if state in {"saved", "verified", "stale", "failed"}:
            if not save_receipt.startswith("save_") or not after_disk_revision.startswith("sha256:"):
                raise ValueError("checkpoint saved identity invalid")
        return LiveWriteCheckpointRecord(
            checkpoint_id=checkpoint_id,
            change_set_id=change_set_id,
            asset_path=asset_path,
            asset_class=asset_class,
            package_name=package_name,
            state=state,
            created_at_utc=created_at_utc,
            saved_at_utc=str(value.get("savedAtUtc", "")),
            verified_at_utc=str(value.get("verifiedAtUtc", "")),
            editor_session_id_at_prepare=str(value.get("editorSessionIdAtPrepare", "")),
            editor_process_id_at_prepare=int(value.get("editorProcessIdAtPrepare", 0) or 0),
            before_disk_revision=before_disk_revision,
            after_disk_revision=after_disk_revision,
            save_receipt=save_receipt,
            backup_manifest_id=str(value.get("backupManifestId", "")),
            included_receipts=included,
            effective_receipts=effective,
            superseded_receipts=superseded,
            effective_operations=[dict(item) for item in operations_value],
            effective_operation_digest=str(value.get("effectiveOperationDigest", "")),
            strong_verification_kind=str(value.get("strongVerificationKind", "")),
            strong_verification_report_id=str(value.get("strongVerificationReportId", "")),
            strong_artifact_root=str(value.get("strongArtifactRoot", "")),
            strong_artifact_revision=str(value.get("strongArtifactRevision", "")),
            strong_artifact_digest=str(value.get("strongArtifactDigest", "")),
            child_unreal_process_count=int(value.get("childUnrealProcessCount", 0) or 0),
            mismatch_diagnostics=[dict(item) for item in value.get("mismatchDiagnostics", []) if isinstance(item, dict)],
            verified_operation_coverage=[
                dict(item) for item in value.get("verifiedOperationCoverage", []) if isinstance(item, dict)
            ],
        )


    def _load_checkpoint_journal(self) -> None:
        root = self._checkpoint_journal_root()
        if not root.is_dir():
            return
        for path in sorted(root.glob("cp_*.json")):
            try:
                record = self._deserialize_checkpoint_record(_read_json(path), path.stem)
            except (WorkflowError, OSError, ValueError):
                self._checkpoint_journal_errors.append(path.stem)
                continue
            self._checkpoints[record.checkpoint_id] = record
            self._checkpoint_recovered_count += 1


    def _resolve_checkpoint(self, checkpoint_id: str) -> LiveWriteCheckpointRecord:
        checkpoint_id = self._validate_checkpoint_id(checkpoint_id)
        record = self._checkpoints.get(checkpoint_id)
        if record is None:
            raise WorkflowError(
                "checkpoint-invalid",
                "The checkpoint is not present in this MCP server session.",
            )
        return record


    def _derive_checkpoint_operations(
        self,
        change_set_id: str,
        asset_path: str,
        editor_session_id: str,
    ) -> dict[str, Any]:
        change_set = self._resolve_change_set(change_set_id)
        candidates: list[tuple[str, str, LiveApplyRecord]] = []
        for operation in change_set.operations:
            live_record = self._live_applies.get(operation.receipt)
            if (
                live_record is None
                or live_record.asset_path != asset_path
                or live_record.editor_session_id != editor_session_id
                or operation.status != "applied"
                or live_record.saved
                or live_record.verified
            ):
                continue
            candidates.append((live_record.applied_at_utc, operation.receipt, live_record))
        candidates.sort(key=lambda item: (item[0], item[1]))
        grouped: dict[str, list[tuple[str, str, LiveApplyRecord]]] = {}
        for applied_at, receipt, live_record in candidates:
            key = _live_write_stable_target_key(live_record.operation, live_record.target)
            grouped.setdefault(key, []).append((applied_at, receipt, live_record))
        effective_receipts: list[str] = []
        superseded_receipts: list[str] = []
        effective_operations: list[dict[str, Any]] = []
        for entries in grouped.values():
            entries.sort(key=lambda item: (item[0], item[1]))
            last = entries[-1]
            effective_receipts.append(last[1])
            effective_operations.append(
                {
                    "receipt": last[1],
                    "operation": last[2].operation,
                    "valueKind": last[2].value_kind,
                    "stableTargetKey": _live_write_stable_target_key(last[2].operation, last[2].target),
                    "target": last[2].target,
                    "expectedValue": last[2].after_value,
                    "transactionId": last[2].transaction_id,
                    "appliedAtUtc": last[2].applied_at_utc,
                }
            )
            superseded_receipts.extend(receipt for _, receipt, _ in entries[:-1])
        included_receipts = [receipt for _, receipt, _ in candidates]
        effective_receipts.sort()
        superseded_receipts.sort()
        included_receipts.sort()
        effective_operations.sort(key=lambda item: item["stableTargetKey"])
        digest = _sha256_bytes(
            _json_bytes(
                {
                    "assetPath": asset_path,
                    "changeSetId": change_set_id,
                    "includedReceipts": included_receipts,
                    "effectiveReceipts": effective_receipts,
                    "supersededReceipts": superseded_receipts,
                    "effectiveOperations": effective_operations,
                }
            )
        )
        return {
            "includedReceipts": included_receipts,
            "effectiveReceipts": effective_receipts,
            "supersededReceipts": superseded_receipts,
            "effectiveOperations": effective_operations,
            "effectiveOperationDigest": digest,
        }


    def _restore_live_record_from_checkpoint_operation(
        self,
        checkpoint: LiveWriteCheckpointRecord,
        operation: dict[str, Any],
    ) -> LiveApplyRecord:
        return LiveApplyRecord(
            receipt=str(operation["receipt"]),
            plan_id="",
            plan_digest="",
            asset_path=checkpoint.asset_path,
            operation=str(operation["operation"]),
            value_kind=str(operation.get("valueKind", "")),
            editor_session_id=checkpoint.editor_session_id_at_prepare,
            transaction_id=str(operation.get("transactionId", "")),
            before_value=None,
            after_value=operation.get("expectedValue"),
            target=dict(operation.get("target") or {}),
            applied_at_utc=str(operation.get("appliedAtUtc", checkpoint.created_at_utc)),
            checkpoint_id=checkpoint.checkpoint_id,
        )


    def _checkpoint_authorized_save(
        self,
        asset_path: str,
        asset_class: str,
        package_name: str,
        expected_revision: str,
        editor_session_id: str,
        editor_process_id: int,
        *,
        mode: Literal["Preview", "Commit"],
        save_receipt: str,
        confirmation: str,
        change_set_id: str,
    ) -> dict[str, Any]:
        if not change_set_id:
            raise WorkflowError(
                "checkpoint-invalid",
                "checkpoint verification_mode requires an exact change_set_id.",
            )
        derived = self._derive_checkpoint_operations(change_set_id, asset_path, editor_session_id)
        if not derived["effectiveReceipts"]:
            raise WorkflowError(
                "checkpoint-invalid",
                "The Change Set has no active effective live writes for this asset in the current Editor session.",
                details={"changeSetId": change_set_id, "assetPath": asset_path},
            )

        if mode == "Preview":
            checkpoint_id = "cp_" + secrets.token_urlsafe(16)
            for receipt in derived["effectiveReceipts"]:
                record = self._live_applies.get(receipt)
                if record is None:
                    raise WorkflowError("checkpoint-invalid", "A checkpoint effective receipt is no longer active.")
                self._fast_verify_live_record(record)
            now = utc_now_iso()
            checkpoint = LiveWriteCheckpointRecord(
                checkpoint_id=checkpoint_id,
                change_set_id=change_set_id,
                asset_path=asset_path,
                asset_class=asset_class,
                package_name=package_name,
                state="prepared",
                created_at_utc=now,
                editor_session_id_at_prepare=editor_session_id,
                editor_process_id_at_prepare=editor_process_id,
                before_disk_revision=expected_revision,
                included_receipts=derived["includedReceipts"],
                effective_receipts=derived["effectiveReceipts"],
                superseded_receipts=derived["supersededReceipts"],
                effective_operations=derived["effectiveOperations"],
                effective_operation_digest=derived["effectiveOperationDigest"],
            )
            self._checkpoints[checkpoint_id] = checkpoint
            self._persist_checkpoint(checkpoint)
            receipt = "save_" + secrets.token_urlsafe(24)
            self._save_authorizations[receipt] = SaveAuthorizationRecord(
                receipt,
                asset_path,
                asset_class,
                package_name,
                expected_revision,
                editor_session_id,
                editor_process_id,
                verification_mode="checkpoint",
                change_set_id=change_set_id,
                checkpoint_id=checkpoint_id,
                included_receipts=tuple(derived["includedReceipts"]),
                effective_receipts=tuple(derived["effectiveReceipts"]),
                superseded_receipts=tuple(derived["supersededReceipts"]),
                effective_operation_digest=derived["effectiveOperationDigest"],
            )
            self._prune_records()
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_save_authorized_asset",
                "ok": True,
                "mode": "Preview",
                "verificationMode": "checkpoint",
                "assetPath": asset_path,
                "assetClass": asset_class,
                "changeSetId": change_set_id,
                "checkpointId": checkpoint_id,
                "expectedDiskRevision": expected_revision,
                "editorSessionId": editor_session_id,
                "editorProcessId": editor_process_id,
                "loaded": True,
                "packageDirty": True,
                "saveReceipt": receipt,
                "includedReceiptCount": len(derived["includedReceipts"]),
                "effectiveReceiptCount": len(derived["effectiveReceipts"]),
                "supersededReceiptCount": len(derived["supersededReceipts"]),
                "effectiveReceipts": derived["effectiveReceipts"],
                "supersededReceipts": derived["supersededReceipts"],
                "saved": False,
                "verified": False,
                "commitToolsEnabled": self.config.commit_enabled,
                "nextStep": f"To save exactly this checkpoint, call ue_save_authorized_asset with mode=Commit, verification_mode=checkpoint, confirmation 'SAVE {receipt}', and the same change_set_id.",
            }

        if not self.config.commit_enabled:
            raise WorkflowError("commit-disabled", "Commit tools were not enabled when this MCP server started.")
        authorization = self._save_authorizations.get(save_receipt)
        if authorization is None or authorization.consumed:
            raise WorkflowError("save-receipt-invalid", "A fresh one-time saveReceipt is required.")
        if authorization.asset_path != asset_path:
            raise WorkflowError("save-receipt-invalid", "The saveReceipt belongs to another asset.")
        if confirmation != f"SAVE {save_receipt}":
            raise WorkflowError("save-confirmation-required", "Save confirmation did not exactly match the required receipt phrase.")
        if (
            authorization.verification_mode != "checkpoint"
            or authorization.change_set_id != change_set_id
            or authorization.asset_class != asset_class
            or authorization.package_name != package_name
            or authorization.expected_disk_revision != expected_revision
            or authorization.editor_session_id != editor_session_id
            or authorization.editor_process_id != editor_process_id
        ):
            raise WorkflowError("save-receipt-stale", "The checkpoint asset, disk Revision, or Editor session changed after Preview.")
        if not authorization.checkpoint_id:
            raise WorkflowError("save-receipt-stale", "The authorized save does not bind a checkpoint.")
        checkpoint = self._checkpoints.get(authorization.checkpoint_id)
        if checkpoint is None or checkpoint.state != "prepared":
            raise WorkflowError("checkpoint-stale", "The checkpoint is not in prepared state for Commit.")

        current_derived = self._derive_checkpoint_operations(change_set_id, asset_path, editor_session_id)
        if (
            list(current_derived["includedReceipts"]) != list(authorization.included_receipts)
            or list(current_derived["effectiveReceipts"]) != list(authorization.effective_receipts)
            or list(current_derived["supersededReceipts"]) != list(authorization.superseded_receipts)
            or current_derived["effectiveOperationDigest"] != authorization.effective_operation_digest
        ):
            raise WorkflowError(
                "checkpoint-membership-changed",
                "The Change Set receipt membership or effective operation set changed after checkpoint Preview.",
            )
        for receipt in authorization.effective_receipts:
            record = self._live_applies.get(receipt)
            if record is None:
                raise WorkflowError("checkpoint-membership-changed", "A checkpoint effective receipt is no longer active.")
            self._fast_verify_live_record(record)

        package_file = self._package_file(self.config.project_path, package_name, asset_class)
        before_revision = "sha256:" + sha256_file(package_file)
        if before_revision != expected_revision:
            raise WorkflowError("revision-conflict", "The disk Package changed after checkpoint Preview.")
        backup_directory = self.config.backup_root / "live-save" / save_receipt
        if backup_directory.exists():
            raise WorkflowError("backup-exists", "The fixed authorized-save backup directory already exists.")
        backup_directory.mkdir(parents=True, exist_ok=False)
        backup_file = backup_directory / package_file.name
        shutil.copy2(package_file, backup_file)
        manifest_path = backup_directory / "manifest.json"
        _write_json_atomic(
            manifest_path,
            {
                "schemaVersion": "1.0",
                "operation": "authorized-live-save-checkpoint",
                "projectName": self.project_name,
                "assetPath": asset_path,
                "assetClass": asset_class,
                "packageName": package_name,
                "changeSetId": change_set_id,
                "checkpointId": checkpoint.checkpoint_id,
                "beforeRevision": before_revision,
                "backupFileName": backup_file.name,
                "createdUtc": utc_now_iso(),
            },
        )
        try:
            bridge_result = self.live_editor_service.call_method(
                "editor.saveAuthorizedAsset",
                {"assetPath": asset_path},
                timeout_seconds=30.0,
            )
        except Exception as exc:
            raise WorkflowError(
                "authorized-save-failed",
                "The fixed Editor rejected or failed the exact checkpoint authorized save.",
                details={"backupManifestId": manifest_path.name},
            ) from exc
        if bridge_result.get("saved") is not True or bridge_result.get("assetPath") != asset_path:
            raise WorkflowError("authorized-save-report-invalid", "The Editor did not confirm the exact saved asset.")

        after_revision = "sha256:" + sha256_file(package_file)
        checkpoint.after_disk_revision = after_revision
        checkpoint.saved_at_utc = utc_now_iso()
        checkpoint.save_receipt = save_receipt
        checkpoint.backup_manifest_id = manifest_path.name
        checkpoint.state = "saved"
        self._persist_checkpoint(checkpoint)
        authorization.consumed = True

        journal_persisted = True
        change_set_updated = False
        for receipt in authorization.effective_receipts:
            live_record = self._live_applies.get(receipt)
            if live_record is not None:
                live_record.saved = True
                live_record.save_receipt = save_receipt
                live_record.checkpoint_id = checkpoint.checkpoint_id
                journal_persisted = self._persist_live_apply(live_record) and journal_persisted
            if change_set_id:
                change_set_updated = (
                    self._update_change_set_operation(
                        change_set_id,
                        receipt,
                        "saved",
                        save_receipt=save_receipt,
                        checkpoint_id=checkpoint.checkpoint_id,
                    )
                    or change_set_updated
                )
        for receipt in authorization.superseded_receipts:
            live_record = self._live_applies.get(receipt)
            if live_record is not None:
                live_record.checkpoint_id = checkpoint.checkpoint_id
                self._persist_live_apply(live_record)
            if change_set_id:
                change_set_updated = (
                    self._update_change_set_operation(
                        change_set_id,
                        receipt,
                        "superseded",
                        save_receipt=save_receipt,
                        checkpoint_id=checkpoint.checkpoint_id,
                    )
                    or change_set_updated
                )
            self._remove_live_apply(receipt)

        freshness_after = (
            self.freshness.mark_commit(asset_path, before_revision, after_revision)
            if after_revision != before_revision
            else self.freshness.inspect_asset(asset_path)
        )
        return {
            "schemaVersion": WORKFLOW_SCHEMA_VERSION,
            "tool": "ue_save_authorized_asset",
            "ok": True,
            "mode": "Commit",
            "verificationMode": "checkpoint",
            "assetPath": asset_path,
            "assetClass": asset_class,
            "saveReceipt": save_receipt,
            "saved": True,
            "verified": False,
            "verificationKind": "persisted-action",
            "checkpointId": checkpoint.checkpoint_id,
            "changeSetId": change_set_id,
            "beforeRevision": before_revision,
            "afterRevision": after_revision,
            "revisionChanged": before_revision != after_revision,
            "backupManifestId": manifest_path.name,
            "editorSessionId": editor_session_id,
            "editorProcessId": editor_process_id,
            "includedReceiptCount": len(authorization.included_receipts),
            "effectiveReceiptCount": len(authorization.effective_receipts),
            "supersededReceiptCount": len(authorization.superseded_receipts),
            "effectiveReceipts": list(authorization.effective_receipts),
            "supersededReceipts": list(authorization.superseded_receipts),
            "journalPersisted": journal_persisted,
            "changeSetUpdated": change_set_updated,
            "bridge": _safe_report(bridge_result, configured_paths=self.configured_paths),
            "indexFreshness": freshness_after,
            "nextStep": "Call ue_verify_live_write_checkpoint with this checkpointId to perform exactly one independent export and verify all effective operations.",
            "nextActions": [
                {
                    "tool": "ue_verify_live_write_checkpoint",
                    "arguments": {"checkpoint_id": checkpoint.checkpoint_id},
                    "reason": "Run exactly one independent Unreal export and verify all effective writes covered by this checkpoint.",
                }
            ],
        }


    def preflight_checkpoint_commit(
        self,
        asset_path: str,
        save_receipt: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        """Read-only all-assets preflight for W4 checkpoint-set Commit.

        It validates one prepared W3 checkpoint exactly as Commit would,
        without invoking the Editor save and without consuming the receipt.
        """
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Commit tools were not enabled when this MCP server started.")
            if not change_set_id:
                raise WorkflowError("checkpoint-invalid", "checkpoint preflight requires an exact change_set_id.")
            authorization = self._save_authorizations.get(save_receipt)
            if authorization is None or authorization.consumed:
                raise WorkflowError("save-receipt-invalid", "A fresh one-time saveReceipt is required.")
            if authorization.asset_path != asset_path:
                raise WorkflowError("save-receipt-invalid", "The saveReceipt belongs to another asset.")
            if (
                authorization.verification_mode != "checkpoint"
                or authorization.change_set_id != change_set_id
            ):
                raise WorkflowError("save-receipt-stale", "The checkpoint asset or Change Set changed after Preview.")
            checkpoint = self._checkpoints.get(authorization.checkpoint_id)
            if checkpoint is None or checkpoint.state != "prepared":
                raise WorkflowError("checkpoint-stale", "The checkpoint is not in prepared state for Commit.")
            if self.live_editor_service is None:
                raise WorkflowError("live-editor-required", "Checkpoint preflight requires Live Editor mode.")
            try:
                status = self.live_editor_service.status()
            except Exception as exc:
                raise WorkflowError("live-editor-status-unavailable", "The fixed Editor session could not be inspected.") from exc
            if status.get("state") != "available" or status.get("pieState") != "stopped":
                raise WorkflowError("live-editor-unavailable", "The fixed Editor must be available and stopped before Commit.")
            editor_session_id = str(status.get("sessionId", ""))
            editor_process_id = int(status.get("processId") or 0)
            if (
                not editor_session_id
                or editor_process_id <= 0
                or editor_session_id != authorization.editor_session_id
                or editor_process_id != authorization.editor_process_id
            ):
                raise WorkflowError("save-receipt-stale", "The Editor session changed after checkpoint Preview.")
            freshness = self._assert_asset_fresh(asset_path)
            expected_revision = str(freshness.get("diskRevision", ""))
            if not expected_revision.startswith("sha256:") or expected_revision != authorization.expected_disk_revision:
                raise WorkflowError("revision-conflict", "The disk Revision changed after checkpoint Preview.")
            current_derived = self._derive_checkpoint_operations(change_set_id, asset_path, editor_session_id)
            if (
                list(current_derived["includedReceipts"]) != list(authorization.included_receipts)
                or list(current_derived["effectiveReceipts"]) != list(authorization.effective_receipts)
                or list(current_derived["supersededReceipts"]) != list(authorization.superseded_receipts)
                or current_derived["effectiveOperationDigest"] != authorization.effective_operation_digest
            ):
                raise WorkflowError(
                    "checkpoint-membership-changed",
                    "The Change Set receipt membership or effective operation set changed after checkpoint Preview.",
                )
            for receipt in authorization.effective_receipts:
                record = self._live_applies.get(receipt)
                if record is None:
                    raise WorkflowError("checkpoint-membership-changed", "A checkpoint effective receipt is no longer active.")
                self._fast_verify_live_record(record)
            package_file = self._package_file(self.config.project_path, authorization.package_name, authorization.asset_class)
            before_revision = "sha256:" + sha256_file(package_file)
            if before_revision != expected_revision:
                raise WorkflowError("revision-conflict", "The disk Package changed after checkpoint Preview.")
            return {
                "ok": True,
                "checkpointId": checkpoint.checkpoint_id,
                "assetPath": asset_path,
                "beforeRevision": before_revision,
                "editorSessionId": editor_session_id,
            }


    def save_authorized_asset(
        self,
        asset_path: str,
        *,
        mode: Literal["Preview", "Commit"] = "Preview",
        save_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
        verification_mode: Literal["immediate", "checkpoint"] = "immediate",
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            if mode not in {"Preview", "Commit"}:
                raise WorkflowError("authorized-save-invalid-mode", "mode must be Preview or Commit.")
            if verification_mode not in {"immediate", "checkpoint"}:
                raise WorkflowError("authorized-save-invalid-mode", "verification_mode must be immediate or checkpoint.")
            if change_set_id:
                change_set = self._resolve_change_set(change_set_id)
                member_assets = {
                    self._live_applies[receipt].asset_path
                    for receipt in change_set.receipts
                    if self._live_applies.get(receipt) is not None
                }
                if asset_path not in member_assets:
                    raise WorkflowError(
                        "change-set-transaction-not-member",
                        "The target asset has no live write bound to this Change Set.",
                        details={"changeSetId": change_set_id, "assetPath": asset_path},
                    )
            if self.live_editor_service is None:
                raise WorkflowError("live-editor-required", "Authorized save requires Live Editor mode for the fixed project.")

            record = self.index_service.get_revision_record(asset_path)
            if record is None:
                raise WorkflowError("asset-not-indexed", "The requested asset is not present in the fixed SQLite index.")
            asset_class = str(record.get("asset_class", ""))
            package_name = str(record.get("package_name", ""))
            if not asset_class or not package_name:
                raise WorkflowError("asset-identity-unavailable", "The indexed asset has no stable Class and Package identity.")
            if asset_class == "/Script/Engine.World":
                raise WorkflowError("authorized-save-map-unsupported", "Authorized save does not save maps or external-actor packages.")
            policy = self._assert_refresh_policy(asset_path, asset_class)
            if policy.get("commitEnabled") is not True:
                raise WorkflowError("commit-not-allowed", "The fixed Policy does not enable Commit.")
            freshness = self._assert_asset_fresh(asset_path)
            expected_revision = str(freshness.get("diskRevision", ""))
            if not expected_revision.startswith("sha256:"):
                raise WorkflowError("revision-unavailable", "The current disk Package has no usable SHA-256 Revision.")

            try:
                status = self.live_editor_service.status()
            except Exception as exc:
                raise WorkflowError("live-editor-status-unavailable", "The fixed Editor session could not be inspected before save.") from exc
            if status.get("state") != "available" or status.get("pieState") != "stopped":
                raise WorkflowError("live-editor-unavailable", "The fixed Editor must be available and stopped before authorized save.")
            editor_session_id = str(status.get("sessionId", ""))
            editor_process_id = int(status.get("processId") or 0)
            if not editor_session_id or editor_process_id <= 0:
                raise WorkflowError("live-editor-status-unavailable", "The fixed Editor session identity is incomplete.")
            try:
                inspection = self.live_editor_service.call_tool("ue_inspect_asset_live", {"assetPath": asset_path})
            except Exception as exc:
                raise WorkflowError("live-editor-status-unavailable", "The target asset could not be inspected before save.") from exc
            result = inspection.get("result", {}) if isinstance(inspection, dict) else {}
            memory = result.get("memory", {}) if isinstance(result, dict) else {}
            registry = result.get("assetRegistry", {}) if isinstance(result, dict) else {}
            if not isinstance(memory, dict) or not isinstance(registry, dict):
                raise WorkflowError("live-editor-protocol-error", "The target asset inspection result is incomplete.")
            if registry.get("classPath") not in {None, "", asset_class}:
                raise WorkflowError("asset-class-mismatch", "The live Asset Registry Class does not match the fixed snapshot.")
            if memory.get("loaded") is not True:
                raise WorkflowError("live-editor-save-asset-not-loaded", "Authorized save only accepts an already loaded exact asset.")
            if memory.get("packageDirty") is not True:
                raise WorkflowError("live-editor-save-not-dirty", "The exact loaded package is not Dirty.")

            if verification_mode == "checkpoint":
                return self._checkpoint_authorized_save(
                    asset_path,
                    asset_class,
                    package_name,
                    expected_revision,
                    editor_session_id,
                    editor_process_id,
                    mode=mode,
                    save_receipt=save_receipt,
                    confirmation=confirmation,
                    change_set_id=change_set_id,
                )

            if mode == "Preview":
                receipt = "save_" + secrets.token_urlsafe(24)
                self._save_authorizations[receipt] = SaveAuthorizationRecord(
                    receipt,
                    asset_path,
                    asset_class,
                    package_name,
                    expected_revision,
                    editor_session_id,
                    editor_process_id,
                )
                self._prune_records()
                preview_response = {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_save_authorized_asset",
                    "ok": True,
                    "mode": "Preview",
                    "assetPath": asset_path,
                    "assetClass": asset_class,
                    "expectedDiskRevision": expected_revision,
                    "editorSessionId": editor_session_id,
                    "editorProcessId": editor_process_id,
                    "loaded": True,
                    "packageDirty": True,
                    "saveReceipt": receipt,
                    "saved": False,
                    "commitToolsEnabled": self.config.commit_enabled,
                    "nextStep": f"To save exactly this asset, call ue_save_authorized_asset with mode=Commit and confirmation 'SAVE {receipt}'.",
                }
                if change_set_id:
                    preview_response["changeSetId"] = change_set_id
                return preview_response

            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Commit tools were not enabled when this MCP server started.")
            authorization = self._save_authorizations.get(save_receipt)
            if authorization is None or authorization.consumed:
                raise WorkflowError("save-receipt-invalid", "A fresh one-time saveReceipt is required.")
            if authorization.asset_path != asset_path:
                raise WorkflowError("save-receipt-invalid", "The saveReceipt belongs to another asset.")
            if confirmation != f"SAVE {save_receipt}":
                raise WorkflowError("save-confirmation-required", "Save confirmation did not exactly match the required receipt phrase.")
            if (
                authorization.asset_class != asset_class
                or authorization.package_name != package_name
                or authorization.expected_disk_revision != expected_revision
                or authorization.editor_session_id != editor_session_id
                or authorization.editor_process_id != editor_process_id
            ):
                raise WorkflowError("save-receipt-stale", "The asset, disk Revision, or Editor session changed after Preview.")

            package_file = self._package_file(self.config.project_path, package_name, asset_class)
            before_revision = "sha256:" + sha256_file(package_file)
            if before_revision != expected_revision:
                raise WorkflowError("revision-conflict", "The disk Package changed after save Preview.")
            backup_directory = self.config.backup_root / "live-save" / save_receipt
            if backup_directory.exists():
                raise WorkflowError("backup-exists", "The fixed authorized-save backup directory already exists.")
            backup_directory.mkdir(parents=True, exist_ok=False)
            backup_file = backup_directory / package_file.name
            shutil.copy2(package_file, backup_file)
            manifest_path = backup_directory / "manifest.json"
            _write_json_atomic(
                manifest_path,
                {
                    "schemaVersion": "1.0",
                    "operation": "authorized-live-save",
                    "projectName": self.project_name,
                    "assetPath": asset_path,
                    "assetClass": asset_class,
                    "packageName": package_name,
                    "beforeRevision": before_revision,
                    "backupFileName": backup_file.name,
                    "createdUtc": utc_now_iso(),
                },
            )

            try:
                bridge_result = self.live_editor_service.call_method(
                    "editor.saveAuthorizedAsset",
                    {"assetPath": asset_path},
                    timeout_seconds=30.0,
                )
            except Exception as exc:
                raise WorkflowError(
                    "authorized-save-failed",
                    "The fixed Editor rejected or failed the exact authorized save.",
                    details={"backupManifestId": manifest_path.name},
                ) from exc
            if bridge_result.get("saved") is not True or bridge_result.get("assetPath") != asset_path:
                raise WorkflowError("authorized-save-report-invalid", "The Editor did not confirm the exact saved asset.")

            after_revision = "sha256:" + sha256_file(package_file)
            verification_root = self._safe_work_path("authorized-save", save_receipt, "verify")
            candidate = self._export_refresh_candidate(
                asset_path,
                verification_root,
                include_blueprint=("Blueprint" in asset_class),
            )
            if candidate.get("revision") != after_revision:
                raise WorkflowError(
                    "authorized-save-verification-failed",
                    "Independent Unreal export did not match the saved disk Revision.",
                    details={"beforeRevision": before_revision, "afterRevision": after_revision},
                )
            authorization.consumed = True
            freshness_after = (
                self.freshness.mark_commit(asset_path, before_revision, after_revision)
                if after_revision != before_revision
                else self.freshness.inspect_asset(asset_path)
            )
            change_set_receipts = set(change_set.receipts) if change_set_id else set()
            live_candidates = [
                (candidate.applied_at_utc, candidate_receipt, candidate)
                for candidate_receipt, candidate in self._live_applies.items()
                if candidate.asset_path == asset_path
                and candidate.editor_session_id == editor_session_id
                and not candidate.saved
                and (not change_set_id or candidate_receipt in change_set_receipts)
            ]
            live_receipt = ""
            journal_persisted = True
            change_set_updated = False
            if live_candidates:
                _, live_receipt, live_record = max(live_candidates)
                live_record.saved = True
                live_record.save_receipt = save_receipt
                journal_persisted = self._persist_live_apply(live_record)
                if change_set_id:
                    change_set_updated = self._update_change_set_operation(
                        change_set_id,
                        live_receipt,
                        "saved",
                        save_receipt=save_receipt,
                    )
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_save_authorized_asset",
                "ok": True,
                "mode": "Commit",
                "assetPath": asset_path,
                "assetClass": asset_class,
                "saveReceipt": save_receipt,
                "saved": True,
                "verified": True,
                "beforeRevision": before_revision,
                "afterRevision": after_revision,
                "revisionChanged": before_revision != after_revision,
                "backupManifestId": manifest_path.name,
                "editorSessionId": editor_session_id,
                "editorProcessId": editor_process_id,
                "liveApplyReceipt": live_receipt,
                "liveWriteSaved": bool(live_receipt),
                "journalPersisted": journal_persisted,
                "bridge": _safe_report(bridge_result, configured_paths=self.configured_paths),
                "indexFreshness": freshness_after,
                "nextStep": (
                    "Call ue_verify_live_write for this exact asset to close the loop with an "
                    "independent reload and Revision. Do not refresh the frozen index before the scoped Trust verdict."
                ),
                "nextActions": [
                    {
                        "tool": "ue_verify_live_write",
                        "arguments": {
                            "asset_path": asset_path,
                            **({"change_set_id": change_set_id} if change_set_id else {}),
                        },
                        "reason": "Independently reload and verify the exact saved live write.",
                    }
                ],
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
                response["changeSetOperationStatus"] = "saved" if live_receipt else "unknown"
            return response


    def create_authorized_save_rollback_manifest(
        self,
        save_receipt: str,
        live_apply_receipt: str,
    ) -> dict[str, Any]:
        """Promote an authorized live-save backup to the standard rollback manifest format."""
        with self._lock:
            self._assert_session_current()
            self._assert_policy_unchanged()
            if not isinstance(save_receipt, str) or not save_receipt.startswith("save_"):
                raise WorkflowError("save-receipt-invalid", "saveReceipt is not a valid authorized-save receipt.")
            live_apply_receipt = self._validate_live_apply_receipt(live_apply_receipt)
            live_record = self._live_applies.get(live_apply_receipt)
            if live_record is None:
                raise WorkflowError(
                    "live-write-verify-not-found",
                    "The authorized save no longer has its pending live write record.",
                )
            if not live_record.saved or live_record.save_receipt != save_receipt:
                raise WorkflowError(
                    "authorized-save-rollback-not-ready",
                    "The live write was not saved by the requested authorized-save receipt.",
                )
            plan_record = self._plans.get(live_record.plan_id)
            if plan_record is None:
                raise WorkflowError(
                    "authorized-save-plan-not-found",
                    "The child Plan required to authorize rollback is no longer active in this MCP session.",
                )
            stored_patch = _read_json(plan_record.patch_path)
            if _sha256_bytes(_json_bytes(stored_patch)) != plan_record.digest or stored_patch != plan_record.patch:
                raise WorkflowError(
                    "plan-tampered",
                    "The child Plan changed before the authorized-save rollback manifest was created.",
                )

            backup_directory = (self.config.backup_root / "live-save" / save_receipt).resolve()
            if not _is_within(backup_directory, self.config.backup_root):
                raise WorkflowError("workflow-path-invalid", "The authorized-save backup escaped the fixed backup root.")
            legacy_manifest_path = backup_directory / "manifest.json"
            legacy_manifest = _read_json(legacy_manifest_path, stage="authorized-save-backup")
            if (
                legacy_manifest.get("assetPath") != live_record.asset_path
                or legacy_manifest.get("projectName") != self.project_name
            ):
                raise WorkflowError(
                    "authorized-save-backup-invalid",
                    "The authorized-save backup identity does not match the pending live write.",
                )
            before_revision = str(legacy_manifest.get("beforeRevision", ""))
            backup_file_name = str(legacy_manifest.get("backupFileName", ""))
            asset_class = str(legacy_manifest.get("assetClass", ""))
            package_name = str(legacy_manifest.get("packageName", ""))
            backup_file = (backup_directory / backup_file_name).resolve()
            if not backup_file.is_file() or not _is_within(backup_file, backup_directory):
                raise WorkflowError("authorized-save-backup-invalid", "The authorized-save backup file is missing or invalid.")
            package_file = self._package_file(self.config.project_path, package_name, asset_class)
            after_revision = "sha256:" + sha256_file(package_file)
            if before_revision == after_revision:
                raise WorkflowError(
                    "authorized-save-revision-unchanged",
                    "A rollback manifest requires a real disk Revision transition.",
                )

            rollback_manifest_path = backup_directory / "rollback-manifest.json"
            if rollback_manifest_path.is_file():
                validation = rollback_backup(
                    rollback_manifest_path,
                    self.config.policy_path,
                    self.config.project_path,
                    self.config.backup_root,
                    commit=False,
                )
                if validation.get("valid") is not True:
                    raise WorkflowError(
                        "authorized-save-rollback-manifest-invalid",
                        "The existing authorized-save rollback manifest no longer validates.",
                        details={"errors": validation.get("errors", [])},
                    )
                return {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "ok": True,
                    "saveReceipt": save_receipt,
                    "liveApplyReceipt": live_apply_receipt,
                    "assetPath": live_record.asset_path,
                    "beforeRevision": validation.get("expectedBackupRevision", before_revision),
                    "afterRevision": validation.get("expectedCurrentRevision", after_revision),
                    "rollbackManifestId": validation.get("manifestId", ""),
                    "rollbackAvailable": True,
                    "created": False,
                }

            commit_report_path = backup_directory / "commit-report.json"
            _write_json_atomic(
                commit_report_path,
                {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "mode": "Commit",
                    "saved": True,
                    "patchId": plan_record.patch.get("patchId", ""),
                    "projectName": self.project_name,
                    "assetPath": live_record.asset_path,
                    "assetClass": asset_class,
                    "operation": live_record.operation,
                    "target": live_record.target,
                    "beforeValue": live_record.before_value,
                    "afterValue": live_record.after_value,
                    "beforeRevision": before_revision,
                    "afterRevision": after_revision,
                    "backupPath": str(backup_file),
                    "executorVersion": DEVELOPMENT_LINE,
                },
            )
            try:
                created = create_backup_manifest(
                    plan_record.patch_path,
                    self.config.policy_path,
                    commit_report_path,
                    self.config.backup_root,
                    output_path=rollback_manifest_path,
                )
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise WorkflowError(
                    "authorized-save-rollback-manifest-failed",
                    "The authorized-save backup could not be promoted to a rollback-safe manifest.",
                ) from exc
            manifest = created.get("manifest", {}) if isinstance(created, dict) else {}
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "ok": True,
                "saveReceipt": save_receipt,
                "liveApplyReceipt": live_apply_receipt,
                "assetPath": live_record.asset_path,
                "beforeRevision": before_revision,
                "afterRevision": after_revision,
                "rollbackManifestId": str(manifest.get("manifestId", "")),
                "rollbackAvailable": True,
                "created": True,
            }


    def rollback_authorized_live_save(
        self,
        save_receipt: str,
        *,
        mode: Literal["DryRun", "Commit"] = "DryRun",
        rollback_dry_run_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
        live_apply_receipt: str = "",
    ) -> dict[str, Any]:
        """Rollback one persisted authorized live save through the standard backup engine."""
        with self._lock:
            self._assert_session_current()
            self._assert_policy_unchanged()
            if not isinstance(save_receipt, str) or not save_receipt.startswith("save_"):
                raise WorkflowError("save-receipt-invalid", "saveReceipt is not a valid authorized-save receipt.")
            if mode not in {"DryRun", "Commit"}:
                raise WorkflowError("authorized-save-rollback-invalid-mode", "mode must be DryRun or Commit.")
            manifest_path = (self.config.backup_root / "live-save" / save_receipt / "rollback-manifest.json").resolve()
            if not manifest_path.is_file() or not _is_within(manifest_path, self.config.backup_root):
                raise WorkflowError(
                    "authorized-save-rollback-manifest-missing",
                    "The authorized save has no rollback-safe standard manifest.",
                )

            if mode == "DryRun":
                receipt = "live_save_rollback_dry_" + secrets.token_urlsafe(20)
                report_path = self._safe_work_path("authorized-save-rollback", save_receipt, receipt, "dry-run.json")
                try:
                    report = rollback_backup(
                        manifest_path,
                        self.config.policy_path,
                        self.config.project_path,
                        self.config.backup_root,
                        commit=False,
                        report_path=report_path,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    raise WorkflowError(
                        "authorized-save-rollback-dry-run-failed",
                        "The authorized-save rollback Dry Run failed.",
                    ) from exc
                if report.get("valid") is not True or report.get("wroteDisk") is not False:
                    raise WorkflowError(
                        "authorized-save-rollback-dry-run-invalid",
                        "Rollback Dry Run did not confirm a valid zero-write restore.",
                        details={"errors": report.get("errors", [])},
                    )
                self._authorized_save_rollback_dry_runs[receipt] = AuthorizedSaveRollbackDryRunRecord(
                    receipt=receipt,
                    save_receipt=save_receipt,
                    report_path=report_path,
                    report=report,
                )
                self._prune_records()
                return {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "ok": True,
                    "mode": "DryRun",
                    "saveReceipt": save_receipt,
                    "rollbackDryRunReceipt": receipt,
                    "assetPath": report.get("assetPath", ""),
                    "beforeRollbackRevision": report.get("currentRevision", ""),
                    "expectedRestoredRevision": report.get("expectedBackupRevision", ""),
                    "wroteDisk": False,
                }

            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Authorized-save rollback Commit is disabled for this MCP session.")
            dry_run = self._authorized_save_rollback_dry_runs.get(rollback_dry_run_receipt)
            if dry_run is None or dry_run.save_receipt != save_receipt or dry_run.consumed:
                raise WorkflowError(
                    "authorized-save-rollback-receipt-invalid",
                    "A fresh authorized-save rollback Dry Run receipt is required.",
                )
            if confirmation != f"ROLLBACK LIVE SAVE {save_receipt}":
                raise WorkflowError(
                    "authorized-save-rollback-confirmation-required",
                    "Rollback confirmation did not exactly match the required saveReceipt phrase.",
                )
            operation_root = self._safe_work_path(
                "authorized-save-rollback",
                save_receipt,
                rollback_dry_run_receipt,
                "commit",
            )
            report_path = operation_root / "report.json"
            verification_key = hashlib.sha256(
                f"{save_receipt}:{rollback_dry_run_receipt}".encode("utf-8")
            ).hexdigest()[:16]
            verification_root = self._safe_work_path("rollback-verify", verification_key)
            verification_output = verification_root / "export"
            verification_report = verification_root / "verification.json"
            rollback_asset_path = str(dry_run.report.get("assetPath", ""))
            live_editor_safety = self._inspect_rollback_live_state(rollback_asset_path)
            script_arguments = [
                "-EngineRoot", str(self.config.engine_root),
                "-ProjectPath", str(self.config.project_path),
                "-Manifest", str(manifest_path),
                "-Policy", str(self.config.policy_path),
                "-BackupRoot", str(self.config.backup_root),
                "-Mode", "Commit",
                "-Report", str(report_path),
                "-VerificationOutput", str(verification_output),
                "-VerificationReport", str(verification_report),
            ]
            if live_editor_safety["allowOpenEditor"]:
                script_arguments.append("-AllowOpenEditorForVerifiedUnloadedAsset")
            result = self._run_script(
                "RunRollback.ps1",
                script_arguments,
                stage="authorized-save-rollback-commit",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="authorized-save-rollback-commit",
                    result=result,
                    report_path=report_path,
                    fallback_code="authorized-save-rollback-commit-failed",
                    fallback_message="Authorized-save rollback Commit or independent verification failed.",
                )
            report = _read_json(report_path, stage="authorized-save-rollback-commit")
            verification = _read_json(verification_report, stage="authorized-save-rollback-verification")
            if report.get("restored") is not True or verification.get("verified") is not True:
                raise WorkflowError(
                    "authorized-save-rollback-report-invalid",
                    "Rollback reports did not confirm restore and independent verification.",
                )
            restored_revision = str(
                verification.get("actualRevision", verification.get("expectedRevision", ""))
            )
            if restored_revision != str(dry_run.report.get("expectedBackupRevision", "")):
                raise WorkflowError(
                    "authorized-save-rollback-revision-mismatch",
                    "Rollback verification did not match the pre-save Revision.",
                )
            dry_run.consumed = True
            asset_path = str(report.get("assetPath", dry_run.report.get("assetPath", "")))
            freshness = self.freshness.mark_rollback(asset_path, restored_revision)
            change_set_updated = False
            if change_set_id and live_apply_receipt:
                change_set_updated = self._update_change_set_operation(
                    change_set_id,
                    live_apply_receipt,
                    "undone",
                )
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "ok": True,
                "mode": "Commit",
                "saveReceipt": save_receipt,
                "rollbackDryRunReceipt": rollback_dry_run_receipt,
                "assetPath": asset_path,
                "restored": True,
                "restoredRevision": restored_revision,
                "reportId": _report_id("authorized-save-rollback-commit", report_path),
                "verificationReportId": _report_id(
                    "authorized-save-rollback-verification",
                    verification_report,
                ),
                "changeSetId": change_set_id,
                "changeSetUpdated": change_set_updated,
                "indexFreshness": freshness,
            }


    def analyze_semantic_diff(
        self,
        change_set_id: str,
        *,
        stage: str = "auto",
        asset_paths: list[str] | None = None,
        include_unchanged: bool = True,
        max_changes: int = 64,
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Analyze only the evidence reachable from one explicit project-bound Change Set."""
        return analyze_workflow_semantic_diff(
            self,
            change_set_id,
            stage=stage,
            asset_paths=asset_paths,
            include_unchanged=include_unchanged,
            max_changes=max_changes,
            max_output_tokens=max_output_tokens,
        )


    def build_verification_plan(
        self,
        change_set_id: str,
        *,
        impact_depth: int = 1,
        required_automation_tests: list[str] | None = None,
        extra_validation_assets: list[str] | None = None,
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        return build_verification_plan(
            self,
            change_set_id,
            impact_depth=impact_depth,
            required_automation_tests=required_automation_tests,
            extra_validation_assets=extra_validation_assets,
            max_output_tokens=max_output_tokens,
        )


    def evaluate_trust_verdict(
        self,
        change_set_id: str,
        *,
        impact_depth: int = 1,
        required_automation_tests: list[str] | None = None,
        extra_validation_assets: list[str] | None = None,
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        return evaluate_trust_verdict(
            self,
            self.verification_evidence_store,
            change_set_id,
            impact_depth=impact_depth,
            required_automation_tests=required_automation_tests,
            extra_validation_assets=extra_validation_assets,
            max_output_tokens=max_output_tokens,
        )

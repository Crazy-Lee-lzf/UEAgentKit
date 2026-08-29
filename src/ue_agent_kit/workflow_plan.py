from __future__ import annotations

import secrets
import shutil
import uuid
from pathlib import Path
from typing import Any, Literal
from .workflow_common import (
    ApplyRecord,
    DryRunRecord,
    HIGH_LEVEL_CHANGE_MODES,
    LiveApplyRecord,
    PlanRecord,
    RollbackDryRunRecord,
    WORKFLOW_SCHEMA_VERSION,
    WorkflowError,
    _json_bytes,
    _read_json,
    _report_id,
    _rollback_memory_task_evidence,
    _safe_report,
    _sha256_bytes,
    _validation_error,
    _verified_memory_task_evidence,
)
from .change_sets import (
    ChangeSetError,
    ChangeSetOperationRecord,
    ChangeSetRecord,
    MAX_CHANGE_SETS,
    MAX_CHANGE_SET_RECEIPTS,
    derive_change_set_status,
    deserialize_change_set_record,
    is_terminal_change_set,
    serialize_change_set_record,
    validate_change_set_id,
    validate_change_set_operation_receipt,
    validate_change_set_task_id,
    validate_change_set_title,
)
from .patches import (
    OPERATION_REGISTRY,
)
from .snapshot_lifecycle import (
    utc_now_iso,
)

def _write_json_atomic(*args: Any, **kwargs: Any) -> Any:
    from . import agent_workflow as _agent_workflow_compat
    return _agent_workflow_compat._write_json_atomic(*args, **kwargs)

def validate_patch(*args: Any, **kwargs: Any) -> Any:
    from . import agent_workflow as _agent_workflow_compat
    return _agent_workflow_compat.validate_patch(*args, **kwargs)


class WorkflowPlanMixin:
    """D1 workflow split mixin/base; method bodies are pure moves from agent_workflow.py."""

    def _plan_directory(self, plan_id: str) -> Path:
        return self._safe_work_path("plans", plan_id)


    def _validate_plan_file(self, record: PlanRecord) -> dict[str, Any]:
        self._assert_policy_unchanged()
        stored_patch = _read_json(record.patch_path)
        stored_digest = _sha256_bytes(_json_bytes(stored_patch))
        if stored_digest != record.digest or stored_patch != record.patch:
            raise WorkflowError("plan-tampered", "The stored MCP patch plan changed after it was created.")
        assets = stored_patch.get("assets", [])
        if not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
            raise WorkflowError("plan-invalid", "The stored MCP patch no longer contains exactly one asset.")
        asset_path = str(assets[0].get("assetPath", ""))
        self._assert_asset_fresh(asset_path)
        validation = validate_patch(record.patch_path, self.config.policy_path, self.config.revision_export)
        if not validation.get("valid"):
            raise _validation_error(
                validation,
                default_code="patch-validation-failed",
                default_message="The stored patch no longer passes Policy and Revision validation.",
                phase="stored-plan-validation",
            )
        return validation


    def plan_patch(
        self,
        *,
        asset_path: str,
        operation: str,
        target: dict[str, Any] | None,
        value: Any,
        description: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_policy_unchanged()
            if operation not in OPERATION_REGISTRY:
                raise WorkflowError("unsupported-operation", "The requested operation is not supported by UE Agent Kit.")
            asset_result = self.index_service.get_asset(asset_path, symbol_limit=1, reference_limit=1, graph_limit=1, node_limit=1)
            if not asset_result.get("found"):
                raise WorkflowError("asset-not-indexed", "The requested asset is not present in the fixed SQLite index.")
            asset = asset_result["asset"]
            self._assert_asset_fresh(asset_path)
            revision = asset.get("revision_value")
            asset_class = asset.get("asset_class")
            if not isinstance(revision, str) or not revision.startswith("sha256:"):
                raise WorkflowError("revision-unavailable", "The indexed asset has no usable SHA-256 Revision.")
            if not isinstance(asset_class, str) or not asset_class:
                raise WorkflowError("asset-class-unavailable", "The indexed asset has no usable Asset Class.")
            target_value = target if target is not None else {}
            patch_id = f"mcp-{uuid.uuid4().hex}"
            patch = {
                "schemaVersion": "1.0",
                "patchId": patch_id,
                "projectName": self.project_name,
                "description": description,
                "assets": [
                    {
                        "assetPath": asset_path,
                        "expectedRevision": revision,
                        "expectedAssetClass": asset_class,
                        "operations": [
                            {
                                "operationId": f"op-{uuid.uuid4().hex}",
                                "operation": operation,
                                "target": target_value,
                                "value": value,
                            }
                        ],
                    }
                ],
            }
            reference_impact: dict[str, Any] | None = None
            digest = _sha256_bytes(_json_bytes(patch))
            plan_id = "plan_" + secrets.token_urlsafe(18)
            directory = self._plan_directory(plan_id)
            patch_path = directory / "patch.json"
            _write_json_atomic(patch_path, patch)
            validation = validate_patch(patch_path, self.config.policy_path, self.config.revision_export)
            if not validation.get("valid"):
                raise _validation_error(
                    validation,
                    default_code="patch-plan-rejected",
                    default_message="The proposed patch was rejected by Policy or Revision validation.",
                    phase="plan-validation",
                )
            if operation in {"removeDataTableRow", "renameDataTableRow"}:
                row_name = target_value.get("rowName") if isinstance(target_value, dict) else None
                if not isinstance(row_name, str) or not row_name:
                    raise WorkflowError(
                        "data-table-row-name-invalid",
                        "The structural DataTable operation has no valid source row name.",
                    )
                reference_impact = self.index_service.get_data_table_row_reference_impact(
                    asset_path,
                    row_name,
                )
                if int(reference_impact.get("referenceCount", 0)) > 0:
                    shutil.rmtree(directory, ignore_errors=True)
                    raise WorkflowError(
                        "data-table-row-referenced",
                        "The DataTable row has indexed Searchable Name referencers and cannot be removed or renamed by a single-asset patch.",
                        details=reference_impact,
                    )
            record = PlanRecord(plan_id, digest, patch, patch_path, validation)
            self._plans[plan_id] = record
            self._prune_records()
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_plan_patch",
                "ok": True,
                "planId": plan_id,
                "patchDigest": digest,
                "projectName": self.project_name,
                "assetPath": asset_path,
                "assetClass": asset_class,
                "expectedRevision": revision,
                "operation": operation,
                "target": target_value,
                "value": value,
                "risk": OPERATION_REGISTRY[operation].risk,
                "commitAllowedByPolicy": bool(validation.get("commitAllowedByPolicy")),
                "commitToolsEnabled": self.config.commit_enabled,
                "nextStep": "Call ue_dry_run_patch with this planId.",
            }
            if reference_impact is not None:
                response["referenceImpact"] = reference_impact
            return response


    def discard_unconsumed_plans(self, plan_ids: list[str]) -> None:
        """Remove newly created session-local Plans that were never exposed for execution."""
        with self._lock:
            records: list[PlanRecord] = []
            for plan_id in plan_ids:
                record = self._plans.get(plan_id)
                if record is None:
                    continue
                if record.consumed:
                    raise WorkflowError(
                        "plan-cleanup-consumed",
                        "A child Plan was already consumed and cannot be removed during Batch Plan cleanup.",
                    )
                records.append(record)
            for record in records:
                self._plans.pop(record.plan_id, None)
                shutil.rmtree(record.patch_path.parent, ignore_errors=True)


    def prepare_high_level_change(
        self,
        *,
        tool_name: str,
        mode: Literal["Plan", "DryRun"],
        asset_path: str,
        operation: str,
        target: dict[str, Any],
        value: Any,
        description: str = "",
    ) -> dict[str, Any]:
        if mode not in HIGH_LEVEL_CHANGE_MODES:
            raise ValueError("mode must be Plan or DryRun")
        plan = self.plan_patch(
            asset_path=asset_path,
            operation=operation,
            target=target,
            value=value,
            description=description,
        )
        if mode == "Plan":
            response = dict(plan)
            response.update(
                {
                    "tool": tool_name,
                    "mode": "Plan",
                    "underlyingTool": "ue_plan_patch",
                    "underlyingOperation": operation,
                }
            )
            return response
        dry_run = self.dry_run_patch(str(plan["planId"]))
        response = dict(dry_run)
        response.update(
            {
                "tool": tool_name,
                "mode": "DryRun",
                "assetPath": asset_path,
                "underlyingTools": ["ue_plan_patch", "ue_dry_run_patch"],
                "underlyingOperation": operation,
                "risk": plan.get("risk", ""),
                "commitToolsEnabled": plan.get("commitToolsEnabled", False),
            }
        )
        return response


    def dry_run_patch(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._plans.get(plan_id)
            if record is None:
                raise WorkflowError("plan-not-found", "The requested planId is not active in this MCP server session.")
            if record.consumed:
                raise WorkflowError("plan-consumed", "The requested plan has already been committed.")
            validation = self._validate_plan_file(record)
            directory = self._plan_directory(plan_id) / "dry-run"
            report_path = directory / "report.json"
            validation_report = directory / "validation.json"
            result = self._run_script(
                "RunPatch.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Patch", str(record.patch_path),
                    "-Policy", str(self.config.policy_path),
                    "-RevisionExport", str(self.config.revision_export),
                    "-Mode", "DryRun",
                    "-Report", str(report_path),
                    "-ValidationReport", str(validation_report),
                    "-BackupDir", str(self.config.backup_root),
                ],
                stage="patch-dry-run",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="patch-dry-run",
                    result=result,
                    report_path=report_path,
                    fallback_code="dry-run-failed",
                    fallback_message="The Unreal Dry Run failed.",
                )
            report = _read_json(report_path, stage="patch-dry-run")
            gates = {
                "modeDryRun": report.get("mode") == "DryRun",
                "notSaved": report.get("saved") is False,
                "rolledBack": report.get("rolledBack") is True,
                "rollbackValueMatch": report.get("rollbackValueMatch") is True,
                "diskUnchanged": report.get("diskUnchanged") is True,
                "revisionUnchanged": report.get("beforeRevision") == report.get("afterRevision"),
            }
            if not all(gates.values()):
                raise WorkflowError("dry-run-gate-failed", "The Dry Run report did not satisfy every safety gate.", details=gates)
            receipt = "dry_" + secrets.token_urlsafe(24)
            self._dry_runs[receipt] = DryRunRecord(receipt, plan_id, record.digest, report_path, report)
            self._prune_records()
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_dry_run_patch",
                "ok": True,
                "planId": plan_id,
                "patchDigest": record.digest,
                "dryRunReceipt": receipt,
                "reportId": _report_id("patch-dry-run", report_path),
                "gates": gates,
                "report": _safe_report(report, configured_paths=self.configured_paths),
                "validationSummary": validation.get("summary", {}),
                "nextStep": f"To commit, call ue_apply_patch with confirmation 'COMMIT {plan_id}'.",
            }


    def apply_patch(
        self,
        plan_id: str,
        dry_run_receipt: str,
        confirmation: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Commit tools were not enabled when this MCP server started.")
            record = self._plans.get(plan_id)
            dry_run = self._dry_runs.get(dry_run_receipt)
            if record is None or dry_run is None:
                raise WorkflowError("receipt-not-found", "The plan or Dry Run receipt is not active in this MCP server session.")
            if confirmation != f"COMMIT {plan_id}":
                raise WorkflowError("commit-confirmation-required", "Commit confirmation did not exactly match the required planId phrase.")
            if dry_run.consumed or dry_run.plan_id != plan_id or dry_run.plan_digest != record.digest:
                raise WorkflowError("receipt-invalid", "The Dry Run receipt is used, stale, or belongs to another plan.")
            if change_set_id:
                change_set = self._resolve_change_set(change_set_id)
                self._reconcile_change_set(change_set, persist=True)
                if change_set.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
                    raise WorkflowError(
                        "change-set-closed",
                        f"The Change Set is in {change_set.status} state and cannot accept this patch.",
                    )
            validation = self._validate_plan_file(record)
            if not validation.get("commitAllowedByPolicy"):
                raise WorkflowError("commit-not-allowed", "The fixed Policy does not enable Commit.")
            directory = self._plan_directory(plan_id) / "commit"
            report_path = directory / "report.json"
            validation_report = directory / "validation.json"
            manifest_path = self.config.backup_root / f"{plan_id}.manifest.json"
            if manifest_path.exists():
                raise WorkflowError("manifest-exists", "The fixed manifest output already exists for this plan.")
            result = self._run_script(
                "RunPatch.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Patch", str(record.patch_path),
                    "-Policy", str(self.config.policy_path),
                    "-RevisionExport", str(self.config.revision_export),
                    "-Mode", "Commit",
                    "-Report", str(report_path),
                    "-ValidationReport", str(validation_report),
                    "-BackupDir", str(self.config.backup_root),
                    "-Manifest", str(manifest_path),
                ],
                stage="patch-commit",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="patch-commit",
                    result=result,
                    report_path=report_path,
                    fallback_code="commit-failed",
                    fallback_message="The Unreal Commit failed.",
                )
            report = _read_json(report_path, stage="patch-commit")
            if report.get("mode") != "Commit" or report.get("saved") is not True:
                raise WorkflowError("commit-report-invalid", "The Commit report did not confirm a saved asset.")
            if not manifest_path.is_file():
                raise WorkflowError("manifest-missing", "Commit succeeded but the fixed Backup Manifest was not created.")
            before_revision = str(report.get("beforeRevision", ""))
            after_revision = str(report.get("afterRevision", ""))
            if not before_revision.startswith("sha256:") or not after_revision.startswith("sha256:") or before_revision == after_revision:
                raise WorkflowError("commit-revision-invalid", "The Commit report did not contain a valid Revision transition.")
            receipt = "apply_" + secrets.token_urlsafe(24)
            committed_asset_path = str(report.get("assetPath", ""))
            self._applies[receipt] = ApplyRecord(
                receipt,
                plan_id,
                record.digest,
                committed_asset_path,
                before_revision,
                after_revision,
                manifest_path,
                report_path,
                report,
            )
            freshness = self.freshness.mark_commit(
                committed_asset_path,
                before_revision,
                after_revision,
            )
            dry_run.consumed = True
            record.consumed = True
            change_set_updated = (
                self._bind_committed_apply(change_set_id, self._applies[receipt], record)
                if change_set_id
                else False
            )
            self._prune_records()
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_apply_patch",
                "ok": True,
                "planId": plan_id,
                "patchDigest": record.digest,
                "applyReceipt": receipt,
                "assetPath": report.get("assetPath", ""),
                "beforeRevision": before_revision,
                "afterRevision": after_revision,
                "manifestId": manifest_path.name,
                "reportId": _report_id("patch-commit", report_path),
                "indexFreshness": freshness,
                "report": _safe_report(report, configured_paths=self.configured_paths),
                "nextStep": "Call ue_verify_asset with this applyReceipt. The fixed index remains stale until refreshed or rolled back.",
                "nextActions": [
                    {
                        "tool": "ue_verify_asset",
                        "arguments": {
                            "apply_receipt": receipt,
                            **({"change_set_id": change_set_id} if change_set_id else {}),
                        },
                        "reason": "Independently reload and verify the exact persisted revision.",
                    }
                ],
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
            return response


    def verify_asset(self, apply_receipt: str, change_set_id: str = "") -> dict[str, Any]:
        with self._lock:
            self._assert_session_current()
            apply = self._applies.get(apply_receipt)
            if apply is None:
                raise WorkflowError("apply-receipt-not-found", "The applyReceipt is not active in this MCP server session.")
            if change_set_id:
                self._assert_change_set_member(change_set_id, apply_receipt)
            output = self._safe_work_path("verify", apply_receipt)
            if output.exists():
                shutil.rmtree(output)
            output.mkdir(parents=True, exist_ok=False)
            asset_package = apply.asset_path.split(".", 1)[0]
            asset_class = str(apply.report.get("assetClass", ""))
            if asset_class == "/Script/Engine.Blueprint":
                verify_script = "RunExport.ps1"
                verify_arguments = [
                    "-EngineRoot",
                    str(self.config.engine_root),
                    "-ProjectPath",
                    str(self.config.project_path),
                    "-Asset",
                    asset_package,
                    "-Output",
                    str(output),
                    "-Profile",
                    "full",
                    "-Format",
                    "json",
                    "-IncludeUnchangedDefaults",
                ]
            else:
                verify_script = "RunAssetCatalog.ps1"
                verify_arguments = [
                    "-EngineRoot",
                    str(self.config.engine_root),
                    "-ProjectPath",
                    str(self.config.project_path),
                    "-Asset",
                    asset_package,
                    "-Output",
                    str(output),
                ]
            result = self._run_script(
                verify_script,
                verify_arguments,
                stage="verify-export",
                report_path=output / "manifest.json",
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="verify-export",
                    result=result,
                    report_path=output / "manifest.json",
                    fallback_code="verify-export-failed",
                    fallback_message="The independent Unreal verification export failed.",
                )
            canonical_files = list((output / "canonical").rglob("*.json"))
            if len(canonical_files) != 1:
                raise WorkflowError("verify-export-invalid", "Independent verification did not produce exactly one Canonical asset.")
            canonical = _read_json(canonical_files[0], stage="verify-canonical")
            revision = canonical.get("revision", {})
            actual_revision = revision.get("value", "") if isinstance(revision, dict) else ""
            verified = canonical.get("assetPath") == apply.asset_path and actual_revision == apply.after_revision
            if not verified:
                raise WorkflowError(
                    "verify-revision-mismatch",
                    "Independent Unreal reload did not match the committed asset and Revision.",
                    details={"expectedRevision": apply.after_revision, "actualRevision": actual_revision},
                )
            apply.verified = True
            change_set_updated = (
                self._update_change_set_operation(change_set_id, apply_receipt, "verified")
                if change_set_id
                else False
            )
            freshness = self.freshness.inspect_asset(apply.asset_path)
            verification_report_id = _report_id("verify-export", output / "manifest.json")
            memory_task_evidence = _verified_memory_task_evidence(
                apply,
                validation_report_id=verification_report_id,
                actual_revision=actual_revision,
            )
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_verify_asset",
                "ok": True,
                "applyReceipt": apply_receipt,
                "assetPath": apply.asset_path,
                "expectedRevision": apply.after_revision,
                "actualRevision": actual_revision,
                "verified": True,
                "assetClass": canonical.get("assetClass", ""),
                "packageDirty": revision.get("packageDirty", False) if isinstance(revision, dict) else False,
                "reportId": verification_report_id,
                "memoryTaskEvidence": memory_task_evidence,
                "indexFreshness": freshness,
                "nextStep": (
                    "Call ue_analyze_semantic_diff at stage=verified, build the Verification Plan, close every "
                    "Required assertion, and evaluate the scoped Trust verdict before refreshing the frozen index."
                    if change_set_id
                    else "The persisted revision is independently verified. Bind future writes to a Change Set "
                    "to continue through Semantic Diff and scoped Trust evaluation."
                ),
                "nextActions": (
                    [
                        {
                            "tool": "ue_analyze_semantic_diff",
                            "arguments": {"change_set_id": change_set_id, "stage": "verified"},
                            "reason": "Compare the independently verified semantics before planning Trust obligations.",
                        }
                    ]
                    if change_set_id
                    else []
                ),
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
            return response


    def rollback_patch(
        self,
        apply_receipt: str,
        *,
        mode: Literal["DryRun", "Commit"] = "DryRun",
        rollback_dry_run_receipt: str = "",
        confirmation: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_policy_unchanged()
            apply = self._applies.get(apply_receipt)
            if apply is None:
                raise WorkflowError("apply-receipt-not-found", "The applyReceipt is not active in this MCP server session.")
            if apply.rolled_back:
                raise WorkflowError("already-rolled-back", "This applyReceipt has already been rolled back.")
            if mode == "DryRun":
                directory = self._safe_work_path("rollback", apply_receipt, "dry-run")
                report_path = directory / "report.json"
                result = self._run_script(
                    "RunRollback.ps1",
                    [
                        "-EngineRoot", str(self.config.engine_root),
                        "-ProjectPath", str(self.config.project_path),
                        "-Manifest", str(apply.manifest_path),
                        "-Policy", str(self.config.policy_path),
                        "-BackupRoot", str(self.config.backup_root),
                        "-Mode", "DryRun",
                        "-Report", str(report_path),
                    ],
                    stage="rollback-dry-run",
                    report_path=report_path,
                )
                if result.exit_code != 0:
                    self._raise_process_failure(
                        stage="rollback-dry-run",
                        result=result,
                        report_path=report_path,
                        fallback_code="rollback-dry-run-failed",
                        fallback_message="Rollback Dry Run failed.",
                    )
                report = _read_json(report_path, stage="rollback-dry-run")
                if report.get("valid") is not True or report.get("wroteDisk") is not False:
                    raise WorkflowError("rollback-dry-run-invalid", "Rollback Dry Run did not confirm a valid zero-write result.")
                receipt = "rollback_dry_" + secrets.token_urlsafe(24)
                self._rollback_dry_runs[receipt] = RollbackDryRunRecord(receipt, apply_receipt, report_path, report)
                self._prune_records()
                return {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_rollback_patch",
                    "ok": True,
                    "mode": "DryRun",
                    "applyReceipt": apply_receipt,
                    "rollbackDryRunReceipt": receipt,
                    "reportId": _report_id("rollback-dry-run", report_path),
                    "report": _safe_report(report, configured_paths=self.configured_paths),
                    "nextStep": f"To restore, call ue_rollback_patch with mode Commit and confirmation 'ROLLBACK {apply_receipt}'.",
                }

            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Rollback Commit was not enabled when this MCP server started.")
            dry_run = self._rollback_dry_runs.get(rollback_dry_run_receipt)
            if dry_run is None or dry_run.apply_receipt != apply_receipt or dry_run.consumed:
                raise WorkflowError("rollback-receipt-invalid", "A fresh rollback Dry Run receipt is required.")
            if confirmation != f"ROLLBACK {apply_receipt}":
                raise WorkflowError("rollback-confirmation-required", "Rollback confirmation did not exactly match the required applyReceipt phrase.")
            live_editor_safety = self._inspect_rollback_live_state(apply.asset_path)
            directory = self._safe_work_path("rollback", apply_receipt, "commit")
            report_path = directory / "report.json"
            verification_output = directory / "verify"
            verification_report = directory / "verification.json"
            script_arguments = [
                "-EngineRoot", str(self.config.engine_root),
                "-ProjectPath", str(self.config.project_path),
                "-Manifest", str(apply.manifest_path),
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
                stage="rollback-commit",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="rollback-commit",
                    result=result,
                    report_path=report_path,
                    fallback_code="rollback-commit-failed",
                    fallback_message="Rollback Commit or independent verification failed.",
                )
            report = _read_json(report_path, stage="rollback-commit")
            verification = _read_json(verification_report, stage="rollback-verification")
            if report.get("restored") is not True or verification.get("verified") is not True:
                raise WorkflowError("rollback-report-invalid", "Rollback reports did not confirm restore and independent verification.")
            restored_revision = str(
                verification.get(
                    "actualRevision",
                    verification.get("restoredRevision", verification.get("expectedRevision", "")),
                )
            )
            if restored_revision != apply.before_revision:
                raise WorkflowError("rollback-revision-mismatch", "Rollback verification did not match the pre-Commit Revision.")
            dry_run.consumed = True
            apply.rolled_back = True
            freshness = self.freshness.mark_rollback(apply.asset_path, restored_revision)
            rollback_report_id = _report_id("rollback-commit", report_path)
            verification_report_id = _report_id(
                "rollback-verification",
                verification_report,
            )
            memory_task_evidence = _rollback_memory_task_evidence(
                apply,
                rollback_report_id=rollback_report_id,
                verification_report_id=verification_report_id,
                restored_revision=restored_revision,
            )
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_rollback_patch",
                "ok": True,
                "mode": "Commit",
                "applyReceipt": apply_receipt,
                "assetPath": apply.asset_path,
                "restored": True,
                "expectedRevision": apply.before_revision,
                "reportId": rollback_report_id,
                "verificationReportId": verification_report_id,
                "memoryTaskEvidence": memory_task_evidence,
                "indexFreshness": freshness,
                "verification": _safe_report(verification, configured_paths=self.configured_paths),
                "report": _safe_report(report, configured_paths=self.configured_paths),
                "liveEditorSafety": live_editor_safety,
                "nextStep": (
                    "If Project Memory is enabled, pass memoryTaskEvidence.arguments unchanged to "
                    "ue_memory_record_task."
                ),
            }


    def _change_set_journal_root(self) -> Path:
        return self._safe_work_path("change-sets")


    def _change_set_journal_path(self, change_set_id: str) -> Path:
        try:
            change_set_id = validate_change_set_id(change_set_id)
        except ChangeSetError as exc:
            raise WorkflowError(exc.code, str(exc)) from exc
        return self._change_set_journal_root() / f"{change_set_id}.json"


    def _persist_change_set(self, record: ChangeSetRecord) -> bool:
        try:
            _write_json_atomic(
                self._change_set_journal_path(record.change_set_id),
                serialize_change_set_record(record, self.project_name),
            )
        except (OSError, TypeError, ValueError):
            self._record_live_write_journal_error(record.change_set_id)
            return False
        if record.change_set_id in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(record.change_set_id)
        return True


    def _delete_change_set_journal(self, change_set_id: str) -> bool:
        path = self._change_set_journal_path(change_set_id)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            self._record_live_write_journal_error(change_set_id)
            return False
        if change_set_id in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(change_set_id)
        return True


    def _load_change_set_journal(self) -> None:
        root = self._change_set_journal_root()
        if not root.is_dir():
            return
        for path in sorted(root.glob("cs_*.json")):
            try:
                record = deserialize_change_set_record(_read_json(path), self.project_name)
            except (WorkflowError, OSError, ValueError):
                self._live_write_journal_errors.append(path.stem)
                continue
            for operation in record.operations:
                if operation.status == "applied":
                    operation.status = "unknown"
            record.status = derive_change_set_status(record.operations)
            self._change_sets[record.change_set_id] = record


    def _resolve_change_set(self, change_set_id: str) -> ChangeSetRecord:
        try:
            change_set_id = validate_change_set_id(change_set_id)
        except ChangeSetError as exc:
            raise WorkflowError(exc.code, str(exc)) from exc
        record = self._change_sets.get(change_set_id)
        if record is None:
            raise WorkflowError(
                "change-set-not-found",
                "The Change Set is not present in this MCP server session.",
            )
        return record


    def _current_editor_session(self) -> tuple[bool, str]:
        if self.live_editor_service is None:
            return False, ""
        try:
            status = self.live_editor_service.status()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False, ""
        session_id = str(status.get("sessionId", "")) if isinstance(status, dict) else ""
        return bool(isinstance(status, dict) and status.get("state") == "available" and session_id), session_id


    def _reconcile_change_set(self, record: ChangeSetRecord, *, persist: bool) -> None:
        editor_available, current_session_id = self._current_editor_session()
        changed = False
        for operation in record.operations:
            live_record = self._live_applies.get(operation.receipt)
            apply_record = self._applies.get(operation.receipt)
            desired_status = operation.status
            if operation.status == "superseded" and live_record is not None:
                if not operation.plan_id:
                    operation.plan_id = live_record.plan_id
                    changed = True
                if not operation.asset_path:
                    operation.asset_path = live_record.asset_path
                    changed = True
                if not operation.operation:
                    operation.operation = live_record.operation
                    changed = True
                if not operation.transaction_id:
                    operation.transaction_id = live_record.transaction_id
                    changed = True
                if not operation.editor_session_id:
                    operation.editor_session_id = live_record.editor_session_id
                    changed = True
                if not operation.save_receipt and live_record.save_receipt:
                    operation.save_receipt = live_record.save_receipt
                    changed = True
            elif live_record is not None:
                if not operation.plan_id:
                    operation.plan_id = live_record.plan_id
                    changed = True
                if not operation.asset_path:
                    operation.asset_path = live_record.asset_path
                    changed = True
                if not operation.operation:
                    operation.operation = live_record.operation
                    changed = True
                if not operation.transaction_id:
                    operation.transaction_id = live_record.transaction_id
                    changed = True
                if not operation.editor_session_id:
                    operation.editor_session_id = live_record.editor_session_id
                    changed = True
                if not operation.save_receipt and live_record.save_receipt:
                    operation.save_receipt = live_record.save_receipt
                    changed = True
                if live_record.verified:
                    desired_status = "verified"
                elif live_record.saved:
                    desired_status = "saved"
                elif editor_available and current_session_id == live_record.editor_session_id:
                    desired_status = "applied"
                elif operation.status in {"applied", "unknown"}:
                    desired_status = "unknown"
            elif operation.status == "applied":
                desired_status = "unknown"
            if apply_record is not None:
                desired_status = "verified" if apply_record.verified else "saved"
            if desired_status != operation.status:
                operation.status = desired_status
                operation.updated_at_utc = utc_now_iso()
                changed = True

        session_ids = {operation.editor_session_id for operation in record.operations if operation.editor_session_id}
        if not record.editor_session_id and len(session_ids) == 1:
            record.editor_session_id = next(iter(session_ids))
            changed = True
        derived_status = derive_change_set_status(record.operations)
        if record.status != derived_status:
            record.status = derived_status
            changed = True
        if changed:
            record.updated_at_utc = utc_now_iso()
            if persist:
                self._persist_change_set(record)


    def _prune_terminal_change_sets(self, maximum: int) -> None:
        while len(self._change_sets) > maximum:
            removable_id = next(
                (
                    change_set_id
                    for change_set_id, record in self._change_sets.items()
                    if is_terminal_change_set(record)
                ),
                "",
            )
            if not removable_id:
                break
            self._change_sets.pop(removable_id)
            self._delete_change_set_journal(removable_id)


    def create_change_set(self, *, title: str = "Live Editor Change Set", task_id: str = "") -> dict[str, Any]:
        with self._lock:
            try:
                title = validate_change_set_title(title)
                task_id = validate_change_set_task_id(task_id) if task_id else "task_" + secrets.token_urlsafe(16)
            except ChangeSetError as exc:
                raise WorkflowError(exc.code, str(exc)) from exc
            self._prune_terminal_change_sets(MAX_CHANGE_SETS - 1)
            if len(self._change_sets) >= MAX_CHANGE_SETS:
                raise WorkflowError(
                    "change-set-capacity-reached",
                    "All Change Set slots are active or non-terminal; close or verify an existing Change Set before creating another.",
                )
            change_set_id = "cs_" + secrets.token_urlsafe(16)
            now = utc_now_iso()
            editor_available, editor_session_id = self._current_editor_session()
            record = ChangeSetRecord(
                change_set_id=change_set_id,
                task_id=task_id,
                editor_session_id=editor_session_id if editor_available else "",
                title=title,
                status="planned",
                created_at_utc=now,
                updated_at_utc=now,
                operations=[],
            )
            self._change_sets[change_set_id] = record
            journal_persisted = self._persist_change_set(record)
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_create_change_set",
                "ok": True,
                "projectName": self.project_name,
                "changeSetId": change_set_id,
                "taskId": task_id,
                "editorSessionId": record.editor_session_id,
                "title": title,
                "status": record.status,
                "createdAtUtc": record.created_at_utc,
                "updatedAtUtc": record.updated_at_utc,
                "operationCount": 0,
                "receiptCount": 0,
                "maxReceiptsPerChangeSet": MAX_CHANGE_SET_RECEIPTS,
                "journalPersisted": journal_persisted,
                "bindingContract": {
                    "changeSetIdArgument": "change_set_id",
                    "sameIdRequired": True,
                    "passToApplicableTools": [
                        "ue_apply_asset_property_live",
                        "ue_apply_patch",
                        "ue_save_authorized_asset",
                        "ue_verify_live_write",
                        "ue_verify_asset",
                        "ue_analyze_semantic_diff",
                        "ue_build_verification_plan",
                        "ue_evaluate_trust_verdict",
                    ],
                },
                "nextStep": (
                    "Pass this same changeSetId unchanged through the applicable write, save, independent verify, "
                    "Semantic Diff, Verification Plan, and Trust Verdict tools."
                ),
            }


    def discard_empty_change_set(self, change_set_id: str) -> bool:
        """Delete an internal Change Set only when no live write was ever bound to it."""
        with self._lock:
            record = self._resolve_change_set(change_set_id)
            self._reconcile_change_set(record, persist=True)
            if record.operations:
                return False
            self._change_sets.pop(change_set_id, None)
            self._delete_change_set_journal(change_set_id)
            return True


    def _change_set_operation_payload(self, operation: ChangeSetOperationRecord) -> dict[str, Any]:
        live_record = self._live_applies.get(operation.receipt)
        apply_record = self._applies.get(operation.receipt)
        return {
            "operationId": operation.receipt,
            "receipt": operation.receipt,
            "liveApplyReceipt": operation.receipt if operation.receipt.startswith("live_") else "",
            "active": live_record is not None or apply_record is not None,
            "planId": operation.plan_id,
            "assetPath": operation.asset_path,
            "operation": operation.operation,
            "transactionId": operation.transaction_id,
            "editorSessionId": operation.editor_session_id,
            "status": operation.status,
            "saved": operation.status in {"saved", "verified"},
            "verified": operation.status == "verified",
            "noOp": operation.status == "no-op",
            "saveReceipt": operation.save_receipt,
            "checkpointId": operation.checkpoint_id,
            "superseded": operation.status == "superseded",
            "failureCode": operation.failure_code,
            "createdAtUtc": operation.created_at_utc,
            "updatedAtUtc": operation.updated_at_utc,
        }


    @staticmethod
    def _change_set_validation(record: ChangeSetRecord) -> dict[str, Any]:
        statuses = [operation.status for operation in record.operations]
        verified_count = sum(status == "verified" for status in statuses)
        no_op_count = sum(status == "no-op" for status in statuses)
        superseded_count = sum(status == "superseded" for status in statuses)
        neutral_count = no_op_count + superseded_count
        effective_count = len(statuses) - neutral_count
        if any(status == "unknown" for status in statuses):
            state = "unknown"
        elif statuses and neutral_count == len(statuses):
            state = "no-op"
        elif statuses and verified_count == effective_count:
            state = "verified"
        elif verified_count:
            state = "partial"
        else:
            state = "not-run"
        return {
            "state": state,
            "verifiedOperationCount": verified_count,
            "noOpOperationCount": no_op_count,
            "supersededOperationCount": superseded_count,
            "operationCount": len(statuses),
        }


    @staticmethod
    def _change_set_save_state(record: ChangeSetRecord) -> dict[str, Any]:
        statuses = [operation.status for operation in record.operations]
        saved_count = sum(status in {"saved", "verified"} for status in statuses)
        no_op_count = sum(status == "no-op" for status in statuses)
        superseded_count = sum(status == "superseded" for status in statuses)
        neutral_count = no_op_count + superseded_count
        if any(status == "unknown" for status in statuses):
            state = "unknown"
        elif statuses and neutral_count == len(statuses):
            state = "not-required"
        elif statuses and saved_count + neutral_count == len(statuses):
            state = "saved"
        elif saved_count:
            state = "partial"
        else:
            state = "unsaved"
        return {
            "state": state,
            "savedOperationCount": saved_count,
            "noOpOperationCount": no_op_count,
            "supersededOperationCount": superseded_count,
            "operationCount": len(statuses),
        }


    @staticmethod
    def _change_set_next_step(record: ChangeSetRecord) -> str:
        if is_terminal_change_set(record):
            return "This Change Set is terminal; create a new Change Set for further writes."
        status = derive_change_set_status(record.operations)
        if status == "planned":
            return "Bind a confirmed live write with ue_apply_asset_property_live."
        if status in {"applied", "partially_applied"}:
            return "Save and verify the remaining applied operations, or undo/discard them."
        if status == "saved":
            return "Run ue_verify_live_write for each saved operation that is not yet verified."
        if status == "unknown":
            return "Inspect the Editor session and affected assets; do not assume the missing in-memory state is still valid."
        return "Review the remaining non-terminal operations before continuing."


    def get_change_set(self, change_set_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._resolve_change_set(change_set_id)
            self._reconcile_change_set(record, persist=True)
            operations = [self._change_set_operation_payload(operation) for operation in record.operations]
            affected_assets = sorted({operation.asset_path for operation in record.operations if operation.asset_path})
            transaction_ids = sorted(
                {operation.transaction_id for operation in record.operations if operation.transaction_id}
            )
            active_count = sum(operation["active"] for operation in operations)
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_get_change_set",
                "ok": True,
                "projectName": self.project_name,
                "changeSetId": record.change_set_id,
                "taskId": record.task_id,
                "editorSessionId": record.editor_session_id,
                "title": record.title,
                "status": record.status,
                "operations": operations,
                "affectedAssets": affected_assets,
                "transactionIds": transaction_ids,
                "validation": self._change_set_validation(record),
                "saveState": self._change_set_save_state(record),
                "createdAtUtc": record.created_at_utc,
                "updatedAtUtc": record.updated_at_utc,
                "operationCount": len(operations),
                "receiptCount": len(operations),
                "activeReceiptCount": active_count,
                "receipts": operations,
                "nextStep": self._change_set_next_step(record),
            }


    def _bind_apply_operation(self, change_set_id: str, live_record: LiveApplyRecord) -> bool:
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        if record.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
            raise WorkflowError(
                "change-set-closed",
                f"The Change Set is in {record.status} state and cannot accept another live write.",
            )
        if len(record.operations) >= MAX_CHANGE_SET_RECEIPTS:
            raise WorkflowError(
                "change-set-full",
                f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
            )
        if record.editor_session_id and record.editor_session_id != live_record.editor_session_id:
            raise WorkflowError(
                "change-set-editor-session-mismatch",
                "The confirmed live write belongs to a different Editor session than the Change Set.",
            )
        if live_record.receipt in record.receipts:
            return True
        now = utc_now_iso()
        record.editor_session_id = live_record.editor_session_id
        record.operations.append(
            ChangeSetOperationRecord(
                receipt=live_record.receipt,
                plan_id=live_record.plan_id,
                asset_path=live_record.asset_path,
                operation=live_record.operation,
                transaction_id=live_record.transaction_id,
                editor_session_id=live_record.editor_session_id,
                status="saved" if live_record.saved else "applied",
                created_at_utc=now,
                updated_at_utc=now,
                save_receipt=live_record.save_receipt,
            )
        )
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = now
        return self._persist_change_set(record)


    def _bind_noop_operation(
        self,
        change_set_id: str,
        plan_record: PlanRecord,
        asset_path: str,
        operation_name: str,
        live_result: dict[str, Any],
    ) -> tuple[str, bool]:
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        if record.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
            raise WorkflowError(
                "change-set-closed",
                f"The Change Set is in {record.status} state and cannot accept another live write.",
            )
        if len(record.operations) >= MAX_CHANGE_SET_RECEIPTS:
            raise WorkflowError(
                "change-set-full",
                f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
            )
        editor_session_id = str(live_result.get("editorSessionId", ""))
        if record.editor_session_id and editor_session_id and record.editor_session_id != editor_session_id:
            raise WorkflowError(
                "change-set-editor-session-mismatch",
                "The confirmed no-op belongs to a different Editor session than the Change Set.",
            )
        receipt = "noop_" + secrets.token_urlsafe(16)
        now = utc_now_iso()
        record.editor_session_id = record.editor_session_id or editor_session_id
        record.operations.append(
            ChangeSetOperationRecord(
                receipt=receipt,
                plan_id=plan_record.plan_id,
                asset_path=asset_path,
                operation=operation_name,
                transaction_id="",
                editor_session_id=editor_session_id,
                status="no-op",
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = now
        return receipt, self._persist_change_set(record)


    def _bind_committed_apply(
        self,
        change_set_id: str,
        apply_record: ApplyRecord,
        plan_record: PlanRecord,
    ) -> bool:
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        if record.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
            raise WorkflowError(
                "change-set-closed",
                f"The Change Set is in {record.status} state and cannot accept another committed patch.",
            )
        if len(record.operations) >= MAX_CHANGE_SET_RECEIPTS:
            raise WorkflowError(
                "change-set-full",
                f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound workflow operations.",
            )
        if apply_record.receipt in record.receipts:
            return True
        assets = plan_record.patch.get("assets", [])
        patch_operations = assets[0].get("operations", []) if len(assets) == 1 and isinstance(assets[0], dict) else []
        operation_name = (
            str(patch_operations[0].get("operation", ""))
            if len(patch_operations) == 1 and isinstance(patch_operations[0], dict)
            else "multiOperationTransaction"
        )
        now = utc_now_iso()
        record.operations.append(
            ChangeSetOperationRecord(
                receipt=apply_record.receipt,
                plan_id=apply_record.plan_id,
                asset_path=apply_record.asset_path,
                operation=operation_name,
                transaction_id="",
                editor_session_id="",
                status="verified" if apply_record.verified else "saved",
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = now
        return self._persist_change_set(record)


    def _assert_change_set_member(self, change_set_id: str, receipt: str) -> ChangeSetOperationRecord:
        try:
            receipt = validate_change_set_operation_receipt(receipt)
        except ValueError as exc:
            raise WorkflowError("change-set-transaction-not-member", str(exc)) from exc
        record = self._resolve_change_set(change_set_id)
        operation = next((candidate for candidate in record.operations if candidate.receipt == receipt), None)
        if operation is None:
            raise WorkflowError(
                "change-set-transaction-not-member",
                "The target live write is not bound to this Change Set.",
                details={"liveApplyReceipt": receipt},
            )
        return operation


    def _update_change_set_operation(
        self,
        change_set_id: str,
        receipt: str,
        status: str,
        *,
        save_receipt: str = "",
        failure_code: str = "",
        checkpoint_id: str = "",
    ) -> bool:
        operation = self._assert_change_set_member(change_set_id, receipt)
        operation.status = status
        if save_receipt:
            operation.save_receipt = save_receipt
        if checkpoint_id:
            operation.checkpoint_id = checkpoint_id
        if failure_code:
            operation.failure_code = failure_code
        operation.updated_at_utc = utc_now_iso()
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = operation.updated_at_utc
        return self._persist_change_set(record)

from __future__ import annotations

import json
import secrets
import shutil
from pathlib import Path
from typing import Any

from .change_sets import ChangeSetOperationRecord, MAX_CHANGE_SET_RECEIPTS, derive_change_set_status
from .database import utc_now_iso
from .snapshot_lifecycle import sha256_file
from .retarget_models import (
    CONFIRMATION_PREFIX,
    MAX_RETARGET_PLAN_CHAINS,
    build_retarget_plan,
    plan_digest,
    pick_retarget_root,
    pick_rig_name,
    select_chains_from_analysis,
)
from . import retarget_models

MAX_RETARGET_PLANS = 32
MAX_RETARGET_BATCH_ASSETS = 100
RETARGET_SETUP_RECEIPT_PREFIX = "rtg_"
MAX_INDEPENDENT_VERIFY_SAMPLE = 3
RETARGET_BACKUP_SCHEMA_VERSION = "1.0"
RETARGET_ROLLBACK_REPORT_SCHEMA_VERSION = "1.0"


def _revision_for_mesh(service: Any, mesh_path: str) -> str:
    package_name = mesh_path.rsplit(".", 1)[0]
    package_file = service._package_file(service.config.project_path, package_name, "SkeletalMesh")
    return "sha256:" + sha256_file(package_file)


def _retarget_output_object_path(source_asset: str, output_directory: str, naming: dict[str, Any]) -> str:
    """Predict the batch output object path for one source asset.

    Mirrors the engine naming rule (EditorAnimUtils::FNameDuplicationRule) used
    by RunRetargetBatchStep so the pre-batch backup can capture the exact files
    that an overwrite will replace.
    """
    package_path, separator, _ = source_asset.rpartition(".")
    if not separator:
        package_path = source_asset
    folder = (output_directory.rstrip("/") if output_directory else package_path.rsplit("/", 1)[0])
    short_name = package_path.rsplit("/", 1)[-1]
    search = naming.get("search", "") or ""
    replace = naming.get("replace", "") or ""
    new_name = short_name.replace(search, replace)
    new_name = (naming.get("prefix", "") or "") + new_name + (naming.get("suffix", "") or "")
    return f"{folder}/{new_name}.{new_name}"


class RetargetWorkflowMixin:
    def _workflow_error(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> Any:
        from .agent_workflow import WorkflowError

        return WorkflowError(code, message, details=details)

    def plan_animation_retarget(
        self,
        *,
        source_mesh: str,
        target_mesh: str,
        include_optional_chains: bool = True,
        output_directory: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if self.live_editor_service is None:
                raise self._workflow_error(
                    "live-editor-required",
                    "Live Editor mode is required for retarget planning.",
                )
            self._assert_policy_unchanged()
            self._assert_live_retarget_capability("retarget.plan")
            live_result = self.live_editor_service.call_method(
                "editor.planAnimationRetarget",
                {
                    "sourceMesh": source_mesh,
                    "targetMesh": target_mesh,
                    "includeOptionalChains": include_optional_chains,
                },
            )
            bridge_result = live_result
            analysis = dict(bridge_result.get("analysis", {}))
            existing = bridge_result.get("existingAssets", {})
            analysis["existingAssets"] = existing

            source_chains, source_warnings, source_blocking = select_chains_from_analysis(
                {"chainCandidates": analysis.get("sourceChainCandidates", [])},
                include_optional=include_optional_chains,
            )
            target_chains, target_warnings, target_blocking = select_chains_from_analysis(
                analysis,
                include_optional=include_optional_chains,
            )
            warnings = list(analysis.get("warnings", [])) + source_warnings + target_warnings
            blocking_issues = list(analysis.get("blockingIssues", [])) + source_blocking + target_blocking

            if not output_directory:
                blocking_issues.append("A Policy-authorized /Game outputDirectory is required for the batch step.")
            else:
                policy_value = json.loads(self.config.policy_path.read_text(encoding="utf-8-sig"))
                roots = policy_value.get("allowedAssetRoots", [])
                if not any(
                    output_directory == root or output_directory.startswith(str(root).rstrip("/") + "/")
                    for root in roots
                    if isinstance(root, str)
                ):
                    blocking_issues.append(
                        f"outputDirectory {output_directory} is not covered by the Policy allowedAssetRoots."
                    )
            if len(source_chains) > MAX_RETARGET_PLAN_CHAINS or len(target_chains) > MAX_RETARGET_PLAN_CHAINS:
                raise self._workflow_error(
                    "retarget-plan-invalid",
                    f"A Retarget Plan is limited to {MAX_RETARGET_PLAN_CHAINS} chains per side.",
                )

            source_root = pick_retarget_root(analysis, "source")
            target_root = pick_retarget_root(analysis, "target")
            root_chain_source = next((c for c in source_chains if c["chain"] == "Root"), None)
            root_chain_target = next((c for c in target_chains if c["chain"] == "Root"), None)
            if root_chain_source is not None:
                source_root = root_chain_source["startBone"]
            if root_chain_target is not None:
                target_root = root_chain_target["startBone"]

            plan_id = "plan_" + secrets.token_urlsafe(18)
            now = utc_now_iso()
            editor_session_id = str(bridge_result.get("editorSessionId", ""))
            project_id = getattr(self.config, "project_name", "") or self.project_name

            source_rig_name = pick_rig_name(source_mesh, "IKRig_")
            target_rig_name = pick_rig_name(target_mesh, "IKRig_")
            affected_assets = [source_mesh, target_mesh]
            for rig_state in existing.values():
                if isinstance(rig_state, dict) and rig_state.get("exists"):
                    affected_assets.append(str(rig_state.get("assetPath", "")))

            revisions = {
                "sourceMesh": _revision_for_mesh(self, source_mesh),
                "targetMesh": _revision_for_mesh(self, target_mesh),
                "sourceMeshPath": source_mesh,
                "targetMeshPath": target_mesh,
            }

            plan = build_retarget_plan(
                plan_id=plan_id,
                project_id=project_id,
                editor_session_id=editor_session_id,
                created_at_utc=now,
                source_mesh=source_mesh,
                target_mesh=target_mesh,
                chain_profile="humanoid-v1",
                analysis=analysis,
                source_rig_name=source_rig_name,
                target_rig_name=target_rig_name,
                source_retarget_root=source_root,
                target_retarget_root=target_root,
                source_chains=source_chains,
                target_chains=target_chains,
                revisions=revisions,
                affected_assets=affected_assets,
                warnings=warnings,
                blocking_issues=blocking_issues,
                output_directory=output_directory,
            )
            digest = plan_digest(plan)
            record = retarget_models.RetargetPlanRecord(plan_id, digest, plan, now)
            self._retarget_plans = getattr(self, "_retarget_plans", {})
            self._retarget_plans[plan_id] = record
            while len(self._retarget_plans) > MAX_RETARGET_PLANS:
                self._retarget_plans.pop(next(iter(self._retarget_plans)))

            return {
                "schemaVersion": "1.0",
                "tool": "ue_plan_animation_retarget",
                "ok": True,
                "mode": "RetargetPlan",
                "planId": plan_id,
                "planDigest": digest,
                "projectName": self.project_name,
                "sourceMesh": source_mesh,
                "targetMesh": target_mesh,
                "confirmationText": f"{CONFIRMATION_PREFIX} {plan_id}",
                "compatibility": analysis.get("compatibility", ""),
                "sourceChains": source_chains,
                "targetChains": target_chains,
                "mappings": plan.get("mappings", []),
                "revisions": revisions,
                "warnings": warnings,
                "blockingIssues": blocking_issues,
                "affectedAssets": affected_assets,
                "result": plan,
                "nextStep": (
                    "Review the Plan, then call ue_apply_animation_retarget_setup with the exact confirmationText."
                    if not blocking_issues
                    else "Resolve the blocking issues before applying the Plan."
                ),
            }

    def apply_animation_retarget_setup(
        self,
        *,
        plan_id: str,
        confirmation: str,
        change_set_id: str = "",
        update_existing: bool = False,
        allow_large_pose_offset: bool = False,
    ) -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise self._workflow_error(
                    "live-editor-write-disabled",
                    "Retarget setup writes require Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise self._workflow_error(
                    "live-editor-required",
                    "Live Editor mode is required for retarget setup.",
                )
            plans = getattr(self, "_retarget_plans", {})
            record = plans.get(plan_id)
            if record is None:
                raise self._workflow_error(
                    "plan-not-found",
                    "The retarget plan is not active in this MCP server session.",
                )
            if confirmation != f"{CONFIRMATION_PREFIX} {plan_id}":
                raise self._workflow_error(
                    "retarget-confirmation-required",
                    "Retarget setup confirmation did not exactly match the required planId phrase.",
                )
            self._assert_policy_unchanged()
            plan = record.plan
            blocking = plan.get("blockingIssues", [])
            if blocking:
                raise self._workflow_error(
                    "retarget-plan-blocked",
                    "The retarget Plan has unresolved blocking issues.",
                    details={"blockingIssues": blocking},
                )
            current_source_revision = _revision_for_mesh(self, str(plan["source"]["mesh"]))
            current_target_revision = _revision_for_mesh(self, str(plan["target"]["mesh"]))
            if current_source_revision != plan["revisions"].get("sourceMesh") or current_target_revision != plan["revisions"].get("targetMesh"):
                raise self._workflow_error(
                    "retarget_revision_conflict",
                    "The Source or Target Skeletal Mesh changed after the Plan was created.",
                    details={
                        "expectedSource": plan["revisions"].get("sourceMesh"),
                        "currentSource": current_source_revision,
                        "expectedTarget": plan["revisions"].get("targetMesh"),
                        "currentTarget": current_target_revision,
                    },
                )

            source_chains = plan["chains"]["source"]
            target_chains = plan["chains"]["target"]
            retargeter_name = str(plan.get("retargeter", {}).get("name", "")) or str(plan.get("retargeterName", ""))
            try:
                self._assert_live_retarget_capability("retarget.configure")
                live_result = self.live_editor_service.call_method(
                    "editor.applyAnimationRetargetSetup",
                    {
                        "sourceMesh": plan["source"]["mesh"],
                        "targetMesh": plan["target"]["mesh"],
                        "sourceRigName": plan["source"]["ikRigName"],
                        "targetRigName": plan["target"]["ikRigName"],
                        "sourceRetargetRoot": plan["source"]["retargetRoot"],
                        "targetRetargetRoot": plan["target"]["retargetRoot"],
                        "sourceChains": source_chains,
                        "targetChains": target_chains,
                        "retargeterName": retargeter_name,
                        "mappings": plan.get("mappings", []),
                        "pose": plan.get("pose", {}),
                        "allowLargePoseOffset": allow_large_pose_offset,
                        "updateExisting": update_existing,
                    },
                )
            except Exception as exc:
                if hasattr(exc, "code"):
                    raise self._workflow_error(str(exc.code), str(exc), details=getattr(exc, "details", {})) from exc
                raise
            bridge_result = live_result
            changes = bridge_result.get("changes", [])
            changed = bool(bridge_result.get("transactionCreated"))
            changed_assets = [
                str(change.get("assetPath", ""))
                for change in changes
                if change.get("action") in {"create", "update"}
            ]
            all_assets = [str(change.get("assetPath", "")) for change in changes]

            setup_receipt = ""
            if changed:
                setup_receipt = RETARGET_SETUP_RECEIPT_PREFIX + secrets.token_urlsafe(16)
                editor_session_id = str(bridge_result.get("editorSessionId", ""))
                if change_set_id:
                    change_set = self._resolve_change_set(change_set_id)
                    self._reconcile_change_set(change_set, persist=True)
                    if change_set.status in {"undone", "discarded", "verified", "failed", "unknown"}:
                        raise self._workflow_error(
                            "change-set-closed",
                            f"The Change Set is in {change_set.status} state and cannot accept another live write.",
                        )
                    if len(change_set.operations) >= MAX_CHANGE_SET_RECEIPTS:
                        raise self._workflow_error(
                            "change-set-full",
                            f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
                        )
                    if change_set.editor_session_id and editor_session_id and change_set.editor_session_id != editor_session_id:
                        raise self._workflow_error(
                            "change-set-editor-session-mismatch",
                            "The retarget setup belongs to a different Editor session than the Change Set.",
                        )
                    now = utc_now_iso()
                    change_set.editor_session_id = editor_session_id
                    for asset_path in all_assets:
                        change_set.operations.append(
                            ChangeSetOperationRecord(
                                receipt="live_rtgsetup_" + secrets.token_urlsafe(16),
                                plan_id=plan_id,
                                asset_path=asset_path,
                                operation="retarget-setup",
                                transaction_id="",
                                editor_session_id=editor_session_id,
                                status="applied",
                                created_at_utc=now,
                                updated_at_utc=now,
                            )
                        )
                    change_set.status = derive_change_set_status(change_set.operations)
                    self._persist_change_set(change_set)

            return {
                "schemaVersion": "1.0",
                "tool": "ue_apply_animation_retarget_setup",
                "ok": True,
                "mode": "RetargetSetupApply",
                "planId": plan_id,
                "planDigest": record.digest,
                "projectName": self.project_name,
                "sourceMesh": plan["source"]["mesh"],
                "targetMesh": plan["target"]["mesh"],
                "changed": changed,
                "changes": changes,
                "changedAssets": changed_assets,
                "setupReceipt": setup_receipt,
                "assetDirty": bool(bridge_result.get("assetDirty")),
                "transactionCreated": changed,
                "mappingReport": bridge_result.get("mappingReport", {}),
                "poseApplied": bool(bridge_result.get("poseApplied")),
                "poseName": bridge_result.get("poseName", ""),
                "result": bridge_result,
                "nextStep": (
                    "Verify the IK Rig and IK Retargeter state, then run the animation batch retarget step."
                    if changed
                    else "No configuration change was required."
                ),
            }


    def start_animation_retarget_batch(
        self,
        *,
        plan_id: str,
        retargeter: str,
        source_assets: list[str],
        output_directory: str = "",
        naming: dict[str, Any] | None = None,
        overwrite_existing: bool = False,
        include_referenced_assets: bool = True,
        export_only_animated_bones: bool = True,
        retain_additive_flags: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            if self.live_editor_service is None:
                raise self._workflow_error(
                    "live-editor-required",
                    "Live Editor mode is required for the animation batch retarget.",
                )
            self._assert_policy_unchanged()
            self._assert_live_retarget_capability("retarget.batch")
            plans = getattr(self, "_retarget_plans", {})
            record = plans.get(plan_id)
            if record is None:
                raise self._workflow_error(
                    "plan-not-found",
                    "The retarget plan is not active in this MCP server session.",
                )
            plan = record.plan
            blocking = plan.get("blockingIssues", [])
            if blocking:
                raise self._workflow_error(
                    "retarget-plan-blocked",
                    "The retarget Plan has unresolved blocking issues.",
                    details={"blockingIssues": blocking},
                )
            if not source_assets or not isinstance(source_assets, list):
                raise self._workflow_error(
                    "retarget-batch-invalid",
                    "sourceAssets must list at least one animation asset object path.",
                )
            if len(source_assets) > MAX_RETARGET_BATCH_ASSETS:
                raise self._workflow_error(
                    "retarget-batch-invalid",
                    f"A batch retarget is limited to {MAX_RETARGET_BATCH_ASSETS} source assets.",
                )
            if not output_directory:
                raise self._workflow_error(
                    "retarget-batch-invalid",
                    "A Policy-authorized /Game outputDirectory is required for the batch step.",
                )
            policy_value = json.loads(self.config.policy_path.read_text(encoding="utf-8-sig"))
            roots = policy_value.get("allowedAssetRoots", [])
            if not any(
                output_directory == root or output_directory.startswith(str(root).rstrip("/") + "/")
                for root in roots
                if isinstance(root, str)
            ):
                raise self._workflow_error(
                    "retarget_output_path_denied",
                    f"outputDirectory {output_directory} is not covered by the Policy allowedAssetRoots.",
                )
            reference_roots = policy_value.get("allowedReferenceRoots", [])
            for asset_path in source_assets:
                if not any(
                    asset_path.startswith(str(root).rstrip("/") + "/") or asset_path == str(root).rstrip("/")
                    for root in reference_roots
                    if isinstance(root, str)
                ):
                    raise self._workflow_error(
                        "retarget-batch-invalid",
                        f"Source animation {asset_path} is not covered by the Policy allowedReferenceRoots.",
                    )

            task_id = "rtg_batch_" + secrets.token_urlsafe(16)
            tasks = getattr(self, "_retarget_batch_tasks", {})
            tasks[task_id] = {
                "taskId": task_id,
                "status": "queued",
                "step": "queued",
                "planId": plan_id,
                "planDigest": record.digest,
                "retargeter": retargeter,
                "sourceAssets": list(source_assets),
                "sourceMesh": plan["source"]["mesh"],
                "targetMesh": plan["target"]["mesh"],
                "sourceRevision": plan["revisions"].get("sourceMesh", ""),
                "targetRevision": plan["revisions"].get("targetMesh", ""),
                "outputDirectory": output_directory,
                "naming": dict(naming or {}),
                "overwriteExisting": overwrite_existing,
                "includeReferencedAssets": include_referenced_assets,
                "exportOnlyAnimatedBones": export_only_animated_bones,
                "retainAdditiveFlags": retain_additive_flags,
                "outputs": [],
                "createdAssets": [],
                "updatedAssets": [],
                "changeSetId": "",
                "backupDir": "",
                "backupManifest": {"schemaVersion": RETARGET_BACKUP_SCHEMA_VERSION, "entries": []},
                "history": [{"step": "queued", "status": "queued", "atUtc": utc_now_iso()}],
                "error": None,
            }
            self._retarget_batch_tasks = tasks
            return {
                "schemaVersion": "1.0",
                "tool": "ue_start_animation_retarget_batch",
                "ok": True,
                "mode": "RetargetBatchTask",
                "taskId": task_id,
                "status": "queued",
                "step": "queued",
                "sourceAssetCount": len(source_assets),
                "outputDirectory": output_directory,
                "nextStep": "Poll ue_get_animation_retarget_batch to advance the task.",
            }

    def get_animation_retarget_batch(self, *, task_id: str) -> dict[str, Any]:
        with self._lock:
            tasks = getattr(self, "_retarget_batch_tasks", {})
            task = tasks.get(task_id)
            if task is None:
                raise self._workflow_error(
                    "retarget-batch-task-not-found",
                    "The retarget batch task is not active in this MCP server session.",
                )
            if task["status"] in {"cancelled", "completed", "failed"}:
                return self._retarget_batch_task_result(task)
            if task["status"] != "queued":
                raise self._workflow_error(
                    "retarget-batch-task-invalid-state",
                    f"The retarget batch task is in state {task['status']}.",
                )

            task["status"] = "validating"
            task["step"] = "validating"
            task["history"].append({"step": "validating", "status": "running", "atUtc": utc_now_iso()})
            # Capture the pre-batch Backup of any output the batch may overwrite.
            # The engine overwrite path persists directly to disk, so the original
            # must be copied before RunRetarget runs.
            predicted_outputs = [
                _retarget_output_object_path(source_asset, task["outputDirectory"], task["naming"])
                for source_asset in task["sourceAssets"]
            ]
            backup_dir, backup_manifest = self._capture_retarget_backup(task_id, predicted_outputs)
            task["backupDir"] = str(backup_dir)
            task["backupManifest"] = backup_manifest
            try:
                self._assert_live_retarget_capability("retarget.batch")
                live_result = self.live_editor_service.call_method(
                    "editor.retargetBatchStep",
                    {
                        "sourceMesh": task["sourceMesh"],
                        "targetMesh": task["targetMesh"],
                        "retargeter": task["retargeter"],
                        "sourceAssetPaths": task["sourceAssets"],
                        "outputDirectory": task["outputDirectory"],
                        "naming": task["naming"],
                        "overwriteExisting": task["overwriteExisting"],
                        "includeReferencedAssets": task["includeReferencedAssets"],
                        "exportOnlyAnimatedBones": task["exportOnlyAnimatedBones"],
                        "retainAdditiveFlags": task["retainAdditiveFlags"],
                    },
                )
            except Exception as exc:
                code = str(getattr(exc, "code", "retarget_batch_partial_failure"))
                if not code.startswith("retarget_") and not code.startswith("live-"):
                    code = "retarget_batch_partial_failure"
                task["status"] = "failed"
                task["step"] = "failed"
                task["error"] = {"code": code, "message": str(exc)}
                task["history"].append({"step": "retargeting", "status": "failed", "code": code, "atUtc": utc_now_iso()})
                return self._retarget_batch_task_result(task)

            outputs = live_result.get("outputs", []) if isinstance(live_result, dict) else []
            task["status"] = "completed"
            task["step"] = "completed"
            task["outputs"] = outputs
            task["createdAssets"] = [str(output.get("outputPath", "")) for output in outputs if isinstance(output, dict)]
            task["history"].append({"step": "retargeting", "status": "completed", "atUtc": utc_now_iso()})
            task["history"].append({"step": "validating_outputs", "status": "completed", "atUtc": utc_now_iso()})
            return self._retarget_batch_task_result(task)

    def cancel_animation_retarget_batch(self, *, task_id: str) -> dict[str, Any]:
        with self._lock:
            tasks = getattr(self, "_retarget_batch_tasks", {})
            task = tasks.get(task_id)
            if task is None:
                raise self._workflow_error(
                    "retarget-batch-task-not-found",
                    "The retarget batch task is not active in this MCP server session.",
                )
            if task["status"] == "queued":
                task["status"] = "cancelled"
                task["step"] = "cancelled"
                task["history"].append({"step": "cancelled", "status": "cancelled", "atUtc": utc_now_iso()})
            else:
                raise self._workflow_error(
                    "retarget-batch-task-invalid-state",
                    f"A task in state {task['status']} cannot be cancelled.",
                )
            return self._retarget_batch_task_result(task)

    def save_animation_retarget_batch(
        self,
        *,
        task_id: str,
        confirmation: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise self._workflow_error(
                    "live-editor-write-disabled",
                    "Retarget batch saves require Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise self._workflow_error(
                    "live-editor-required",
                    "Live Editor mode is required to save the animation batch retarget outputs.",
                )
            tasks = getattr(self, "_retarget_batch_tasks", {})
            task = tasks.get(task_id)
            if task is None:
                raise self._workflow_error(
                    "retarget-batch-task-not-found",
                    "The retarget batch task is not active in this MCP server session.",
                )
            if task["status"] != "completed":
                raise self._workflow_error(
                    "retarget-batch-task-invalid-state",
                    f"Only a completed retarget batch task can be saved (current state {task['status']}).",
                )
            if confirmation != f"SAVE RETARGET BATCH {task_id}":
                raise self._workflow_error(
                    "retarget-confirmation-required",
                    "Retarget batch save confirmation did not exactly match the required taskId phrase.",
                )
            self._assert_policy_unchanged()
            created_assets = task["createdAssets"]
            if not created_assets:
                raise self._workflow_error(
                    "retarget-batch-invalid",
                    "The completed retarget batch task has no created assets to save.",
                )
            saved_assets: list[str] = []
            save_receipts: list[str] = []
            for asset_path in created_assets:
                package_name = asset_path.rsplit(".", 1)[0]
                package_file = self._output_package_file(package_name)
                if package_file.is_file():
                    # The batch overwrite path already persisted this output to
                    # disk, so the package is clean; record it as saved.
                    saved_assets.append(asset_path)
                    save_receipts.append("live_rtsave_" + secrets.token_urlsafe(16))
                    continue
                save_result = self.live_editor_service.call_method(
                    "editor.saveAuthorizedAsset",
                    {
                        "assetPath": asset_path,
                        "createMissing": True,
                    },
                )
                if not isinstance(save_result, dict) or not save_result.get("saved", False):
                    code = str((save_result or {}).get("code", "retarget-save-failed"))
                    raise self._workflow_error(
                        code,
                        f"Authorized save failed for {asset_path}.",
                        details={"assetPath": asset_path},
                    )
                saved_assets.append(asset_path)
                save_receipts.append("live_rtsave_" + secrets.token_urlsafe(16))
            task["status"] = "saved"
            task["savedAssets"] = saved_assets
            task["saveReceipts"] = save_receipts
            task["updatedAssets"] = [
                entry.get("outputPath", "")
                for entry in task.get("backupManifest", {}).get("entries", [])
                if entry.get("kind") == "overwrite"
            ]
            task["history"].append({"step": "saved", "status": "saved", "atUtc": utc_now_iso()})

            change_set_updated = False
            if change_set_id:
                change_set_updated = self._bind_retarget_save_change_set(
                    plan_id=task["planId"],
                    change_set_id=change_set_id,
                    saved_assets=saved_assets,
                    save_receipts=save_receipts,
                    editor_session_id=self._current_editor_session_id(),
                )
                task["changeSetId"] = change_set_id
            backup_manifest = task.get("backupManifest", {})
            backup_manifest_ref = (
                f"backup-manifest:{task_id}"
                if backup_manifest.get("entries")
                else "backup-manifest:not-applicable"
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_save_animation_retarget_batch",
                "ok": True,
                "mode": "RetargetBatchSave",
                "taskId": task_id,
                "status": "saved",
                "savedAssets": saved_assets,
                "saveReceipts": save_receipts,
                "updatedAssets": task["updatedAssets"],
                "createdAssets": created_assets,
                "outputDirectory": task["outputDirectory"],
                "backupManifestRef": backup_manifest_ref,
                "backupDir": task.get("backupDir", ""),
                "changeSetId": change_set_id or "",
                "changeSetUpdated": change_set_updated,
                "nextStep": (
                    "The retargeted animations are saved to disk; run ue_verify_animation_retarget_batch "
                    "to independently reload the outputs, then ue_rollback_animation_retarget_batch if a restore is needed."
                ),
            }

    def validate_animation_retarget(
        self,
        *,
        retargeter: str,
        animation_paths: list[str],
    ) -> dict[str, Any]:
        with self._lock:
            if self.live_editor_service is None:
                raise self._workflow_error(
                    "live-editor-required",
                    "Live Editor mode is required to validate the retarget.",
                )
            self._assert_policy_unchanged()
            self._assert_live_retarget_capability("retarget.validate")
            if not animation_paths or not isinstance(animation_paths, list):
                raise self._workflow_error(
                    "retarget-batch-invalid",
                    "animationPaths must list at least one animation object path.",
                )
            live_result = self.live_editor_service.call_method(
                "editor.validateAnimationRetarget",
                {
                    "retargeter": retargeter,
                    "animationPaths": animation_paths,
                },
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_validate_animation_retarget",
                "ok": True,
                "mode": "RetargetValidation",
                "retargeter": retargeter,
                "verdict": str(live_result.get("verdict", "")),
                "animationCount": len(animation_paths),
                "issues": live_result.get("issues", []),
                "result": live_result,
                "nextStep": (
                    "The retargeted animations are valid; they can be assigned to the target character and saved."
                    if live_result.get("verdict") in {"passed", "passed_with_warnings"}
                    else "Resolve the validation errors before using the retargeted animations."
                ),
            }

    def _retarget_work_root(self) -> Path:
        work_root = getattr(self.config, "work_root", None)
        if work_root is None:
            work_root = self.config.project_path.parent
        return Path(work_root).resolve()

    def _retarget_backup_root(self) -> Path:
        configured = getattr(self.config, "backup_root", None)
        root = Path(configured) / "Retarget" if configured is not None else self._retarget_work_root() / "retarget-backups"
        return root.resolve()

    def _content_root(self) -> Path:
        project_path = Path(self.config.project_path).resolve()
        if project_path.is_dir():
            # Unit-test harness: project_path is the project directory itself.
            return project_path / "Content"
        # Production: project_path is the .uproject file, Content is its sibling.
        return project_path.parent / "Content"

    def _output_package_file(self, package_name: str) -> Path:
        if not package_name.startswith("/Game/"):
            raise self._workflow_error(
                "retarget-batch-invalid",
                "Output package must be under /Game.",
            )
        relative_parts = [part for part in package_name[len("/Game/") :].split("/") if part]
        if not relative_parts or any(part in {".", ".."} for part in relative_parts):
            raise self._workflow_error(
                "retarget-batch-invalid",
                "Output package path is invalid.",
            )
        return self._content_root().joinpath(*relative_parts).with_suffix(".uasset")

    def _capture_retarget_backup(
        self,
        task_id: str,
        predicted_outputs: list[str],
    ) -> tuple[Path, dict[str, Any]]:
        backup_dir = self._retarget_backup_root() / task_id
        if backup_dir.exists():
            shutil.rmtree(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=False)
        entries: list[dict[str, Any]] = []
        for index, output_path in enumerate(predicted_outputs):
            package_path = output_path.rsplit(".", 1)[0]
            package_file = self._output_package_file(package_path)
            entry: dict[str, Any] = {
                "index": index,
                "outputPath": output_path,
                "packageFile": str(package_file),
                "backupRelativePath": "",
                "revision": "",
            }
            if package_file.is_file():
                backup_file = backup_dir / f"{index:03d}-{package_file.name}"
                shutil.copy2(package_file, backup_file)
                entry["kind"] = "overwrite"
                entry["backupRelativePath"] = backup_file.relative_to(backup_dir).as_posix()
                entry["revision"] = "sha256:" + sha256_file(backup_file)
            else:
                entry["kind"] = "create"
            entries.append(entry)
        manifest: dict[str, Any] = {
            "schemaVersion": RETARGET_BACKUP_SCHEMA_VERSION,
            "taskId": task_id,
            "createdUtc": utc_now_iso(),
            "entries": entries,
        }
        (backup_dir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return backup_dir, manifest

    def _load_retarget_backup(self, task_id: str) -> tuple[Path, dict[str, Any]]:
        backup_dir = self._retarget_backup_root() / task_id
        manifest_path = backup_dir / "manifest.json"
        if not manifest_path.is_file():
            raise self._workflow_error(
                "retarget-rollback-backup-missing",
                "The retarget batch has no pre-batch Backup manifest; rollback is not available.",
                details={"backupDir": str(backup_dir)},
            )
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise self._workflow_error(
                "retarget-rollback-backup-invalid",
                "The retarget batch Backup manifest is unreadable.",
            ) from exc
        if not isinstance(manifest, dict) or manifest.get("schemaVersion") != RETARGET_BACKUP_SCHEMA_VERSION:
            raise self._workflow_error(
                "retarget-rollback-backup-invalid",
                "The retarget batch Backup manifest schema is unsupported.",
            )
        return backup_dir, manifest

    def _current_editor_session_id(self) -> str:
        session_reader = getattr(self, "_current_editor_session", None)
        if session_reader is None:
            return ""
        try:
            available, session_id = session_reader()
            return session_id if available else ""
        except (AttributeError, TypeError, ValueError):
            return ""

    def _bind_retarget_save_change_set(
        self,
        *,
        plan_id: str,
        change_set_id: str,
        saved_assets: list[str],
        save_receipts: list[str],
        editor_session_id: str,
    ) -> bool:
        change_set = self._resolve_change_set(change_set_id)
        self._reconcile_change_set(change_set, persist=True)
        if change_set.status in {"undone", "discarded", "verified", "failed", "unknown"}:
            raise self._workflow_error(
                "change-set-closed",
                f"The Change Set is in {change_set.status} state and cannot accept another live write.",
            )
        if len(change_set.operations) + len(saved_assets) > MAX_CHANGE_SET_RECEIPTS:
            raise self._workflow_error(
                "change-set-full",
                f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
            )
        if change_set.editor_session_id and editor_session_id and change_set.editor_session_id != editor_session_id:
            raise self._workflow_error(
                "change-set-editor-session-mismatch",
                "The retarget save belongs to a different Editor session than the Change Set.",
            )
        now = utc_now_iso()
        if editor_session_id:
            change_set.editor_session_id = editor_session_id
        for asset_path, save_receipt in zip(saved_assets, save_receipts, strict=True):
            change_set.operations.append(
                ChangeSetOperationRecord(
                    receipt=save_receipt,
                    plan_id=plan_id,
                    asset_path=asset_path,
                    operation="retarget-save",
                    transaction_id="",
                    editor_session_id=editor_session_id,
                    status="saved",
                    created_at_utc=now,
                    updated_at_utc=now,
                    save_receipt=save_receipt,
                )
            )
        change_set.status = derive_change_set_status(change_set.operations)
        change_set.updated_at_utc = now
        return self._persist_change_set(change_set)

    def _run_independent_catalog(self, package_path: str, output_dir: Path, *, stage: str) -> dict[str, Any]:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=False)
        result = self._run_script(
            "RunAssetCatalog.ps1",
            [
                "-EngineRoot", str(self.config.engine_root),
                "-ProjectPath", str(self.config.project_path),
                "-Asset", package_path,
                "-Output", str(output_dir),
            ],
            stage=stage,
            report_path=output_dir / "manifest.json",
        )
        exit_code = int(getattr(result, "exit_code", 1))
        manifest: dict[str, Any] = {}
        manifest_path = output_dir / "manifest.json"
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                manifest = {}
        canonical: dict[str, Any] = {}
        canonical_files = list((output_dir / "canonical").rglob("*.json"))
        if len(canonical_files) == 1:
            try:
                canonical = json.loads(canonical_files[0].read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                canonical = {}
        return {"exitCode": exit_code, "manifest": manifest, "canonical": canonical}

    def verify_animation_retarget_batch(self, *, task_id: str) -> dict[str, Any]:
        with self._lock:
            tasks = getattr(self, "_retarget_batch_tasks", {})
            task = tasks.get(task_id)
            if task is None:
                raise self._workflow_error(
                    "retarget-batch-task-not-found",
                    "The retarget batch task is not active in this MCP server session.",
                )
            if task["status"] not in {"completed", "saved"}:
                raise self._workflow_error(
                    "retarget-batch-task-invalid-state",
                    f"Only a completed or saved retarget batch task can be verified (current state {task['status']}).",
                )
            self._assert_policy_unchanged()
            self._assert_live_retarget_capability("retarget.validate")
            outputs = task.get("savedAssets") or task.get("createdAssets") or []
            if not outputs:
                raise self._workflow_error(
                    "retarget-batch-invalid",
                    "The retarget batch task has no outputs to verify.",
                )
            sample = outputs[:MAX_INDEPENDENT_VERIFY_SAMPLE]
            verified_assets: list[dict[str, Any]] = []
            failures: list[dict[str, Any]] = []
            for asset_path in sample:
                package_path = asset_path.rsplit(".", 1)[0]
                output_dir = self._retarget_work_root() / "retarget-verify" / task_id / package_path.rsplit("/", 1)[-1]
                export = self._run_independent_catalog(package_path, output_dir, stage="retarget-verify-export")
                canonical = export["canonical"]
                manifest = export["manifest"]
                revision = canonical.get("revision", {}) if isinstance(canonical, dict) else {}
                actual_revision = revision.get("value", "") if isinstance(revision, dict) else ""
                package_file = self._output_package_file(package_path)
                disk_revision = "sha256:" + sha256_file(package_file) if package_file.is_file() else ""
                checks = {
                    "exportExitCodeZero": export["exitCode"] == 0,
                    "manifestSuccess": (
                        int(manifest.get("successCount", -1)) == 1 and int(manifest.get("failureCount", -1)) == 0
                    ),
                    "canonicalAssetMatch": canonical.get("assetPath") == asset_path,
                    "revisionMatchesDisk": bool(actual_revision) and actual_revision == disk_revision,
                    "packageNotDirty": isinstance(revision, dict) and revision.get("packageDirty") is False,
                }
                if all(checks.values()):
                    verified_assets.append({"assetPath": asset_path, "revision": actual_revision, "verified": True})
                else:
                    failures.append(
                        {
                            "assetPath": asset_path,
                            "checks": checks,
                            "actualRevision": actual_revision,
                            "diskRevision": disk_revision,
                        }
                    )
            verified = not failures and len(verified_assets) == len(sample)
            validation_report_id = "report_" + secrets.token_urlsafe(20)
            task["independentValidation"] = {
                "sampleCount": len(sample),
                "verifiedCount": len(verified_assets),
                "verifiedAssets": verified_assets,
                "failures": failures,
                "verified": verified,
                "reportId": validation_report_id,
            }
            revision_set = [
                {
                    "assetPath": entry["assetPath"],
                    "revision": entry["revision"],
                    "revisionStable": True,
                }
                for entry in verified_assets
            ]
            evidence = self._retarget_memory_task_evidence(
                task,
                conclusion=(
                    f"The retarget batch {task_id} outputs were saved and {len(verified_assets)}/{len(sample)} "
                    f"were independently reloaded and matched their disk Revision."
                    if verified
                    else f"The retarget batch {task_id} independent verification found {len(failures)} failure(s)."
                ),
                outcome="succeeded" if verified else "failed",
                revision_set=revision_set,
                validation_report_id=validation_report_id,
                workflow_tool="ue_verify_animation_retarget_batch",
                state="verified" if verified else "failed",
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_verify_animation_retarget_batch",
                "ok": True,
                "mode": "RetargetBatchVerify",
                "taskId": task_id,
                "status": task["status"],
                "verified": verified,
                "sampleCount": len(sample),
                "verifiedCount": len(verified_assets),
                "verifiedAssets": verified_assets,
                "failures": failures,
                "reportId": validation_report_id,
                "memoryTaskEvidence": evidence,
                "nextStep": (
                    "The saved outputs were independently reloaded and match disk. If Memory is enabled, "
                    "pass memoryTaskEvidence.arguments unchanged to ue_memory_record_task."
                    if verified
                    else "Resolve the independent verification failures before trusting the saved outputs."
                ),
            }

    def rollback_animation_retarget_batch(
        self,
        *,
        task_id: str,
        mode: str = "DryRun",
        rollback_dry_run_receipt: str = "",
        confirmation: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            tasks = getattr(self, "_retarget_batch_tasks", {})
            task = tasks.get(task_id)
            if task is None:
                raise self._workflow_error(
                    "retarget-batch-task-not-found",
                    "The retarget batch task is not active in this MCP server session.",
                )
            if task["status"] not in {"completed", "saved"}:
                raise self._workflow_error(
                    "retarget-batch-task-invalid-state",
                    f"Only a completed or saved retarget batch task can be rolled back (current state {task['status']}).",
                )
            if mode not in {"DryRun", "Commit"}:
                raise self._workflow_error(
                    "retarget-rollback-invalid-mode",
                    "mode must be DryRun or Commit.",
                )
            backup_dir, manifest = self._load_retarget_backup(task_id)
            entries = manifest.get("entries", [])
            if not isinstance(entries, list) or not entries:
                raise self._workflow_error(
                    "retarget-rollback-backup-missing",
                    "The retarget batch Backup manifest has no captured entries.",
                )
            restore_plan = [entry for entry in entries if entry.get("kind") == "overwrite"]
            delete_plan = [entry for entry in entries if entry.get("kind") == "create"]

            if mode == "DryRun":
                receipt = "rtgrb_dry_" + secrets.token_urlsafe(16)
                registry = getattr(self, "_retarget_rollback_dry_runs", {})
                registry[receipt] = {"taskId": task_id, "receipt": receipt, "consumed": False}
                self._retarget_rollback_dry_runs = registry
                report = {
                    "schemaVersion": RETARGET_ROLLBACK_REPORT_SCHEMA_VERSION,
                    "mode": "DryRun",
                    "taskId": task_id,
                    "valid": True,
                    "willWriteDisk": False,
                    "wroteDisk": False,
                    "restoreCount": len(restore_plan),
                    "deleteCount": len(delete_plan),
                    "restorePlan": restore_plan,
                    "deletePlan": delete_plan,
                    "backupDir": str(backup_dir),
                }
                return {
                    "schemaVersion": "1.0",
                    "tool": "ue_rollback_animation_retarget_batch",
                    "ok": True,
                    "mode": "DryRun",
                    "taskId": task_id,
                    "rollbackDryRunReceipt": receipt,
                    "restoreCount": len(restore_plan),
                    "deleteCount": len(delete_plan),
                    "report": report,
                    "nextStep": (
                        f"To restore, call ue_rollback_animation_retarget_batch with mode Commit and "
                        f"confirmation 'ROLLBACK RETARGET BATCH {task_id}'."
                    ),
                }

            if not self.config.commit_enabled:
                raise self._workflow_error(
                    "live-editor-write-disabled",
                    "Retarget rollback requires Commit tools to be enabled when the MCP server starts.",
                )
            registry = getattr(self, "_retarget_rollback_dry_runs", {})
            dry_run = registry.get(rollback_dry_run_receipt)
            if dry_run is None or dry_run["taskId"] != task_id or dry_run["consumed"]:
                raise self._workflow_error(
                    "retarget-rollback-receipt-invalid",
                    "A fresh retarget rollback Dry Run receipt is required.",
                )
            if confirmation != f"ROLLBACK RETARGET BATCH {task_id}":
                raise self._workflow_error(
                    "retarget-confirmation-required",
                    "Retarget rollback confirmation did not exactly match the required taskId phrase.",
                )
            dry_run["consumed"] = True

            restored: list[dict[str, Any]] = []
            deleted: list[dict[str, Any]] = []
            failures: list[dict[str, Any]] = []
            for entry in entries:
                output_path = entry["outputPath"]
                package_path = output_path.rsplit(".", 1)[0]
                package_file = self._output_package_file(package_path)
                if entry.get("kind") == "overwrite":
                    backup_file = backup_dir / str(entry.get("backupRelativePath", ""))
                    expected_revision = str(entry.get("revision", ""))
                    if not backup_file.is_file() or expected_revision != "sha256:" + sha256_file(backup_file):
                        failures.append(
                            {
                                "outputPath": output_path,
                                "kind": "overwrite",
                                "error": "retarget-rollback-backup-mismatch",
                            }
                        )
                        continue
                    shutil.copy2(backup_file, package_file)
                    actual_revision = "sha256:" + sha256_file(package_file)
                    if actual_revision != expected_revision:
                        failures.append(
                            {
                                "outputPath": output_path,
                                "kind": "overwrite",
                                "error": "retarget-rollback-restore-mismatch",
                            }
                        )
                        continue
                    restored.append(
                        {
                            "outputPath": output_path,
                            "revision": actual_revision,
                            "restored": True,
                        }
                    )
                else:
                    if package_file.is_file():
                        package_file.unlink()
                    if package_file.exists():
                        failures.append(
                            {
                                "outputPath": output_path,
                                "kind": "create",
                                "error": "retarget-rollback-delete-failed",
                            }
                        )
                        continue
                    deleted.append({"outputPath": output_path, "deleted": True})

            # Independent reload verification of a bounded sample.
            verify_sample = (restored + deleted)[:MAX_INDEPENDENT_VERIFY_SAMPLE]
            verification: list[dict[str, Any]] = []
            for item in verify_sample:
                output_path = item["outputPath"]
                package_path = output_path.rsplit(".", 1)[0]
                output_dir = self._retarget_work_root() / "retarget-rollback" / task_id / package_path.rsplit("/", 1)[-1]
                export = self._run_independent_catalog(package_path, output_dir, stage="retarget-rollback-verify")
                canonical = export["canonical"]
                manifest_export = export["manifest"]
                if item.get("restored"):
                    revision = canonical.get("revision", {}) if isinstance(canonical, dict) else {}
                    actual = revision.get("value", "") if isinstance(revision, dict) else ""
                    verified_restore = (
                        export["exitCode"] == 0
                        and canonical.get("assetPath") == output_path
                        and actual == item.get("revision", "")
                        and revision.get("packageDirty") is False
                    )
                    verification.append(
                        {
                            "outputPath": output_path,
                            "kind": "overwrite",
                            "verified": verified_restore,
                            "revision": actual,
                        }
                    )
                else:
                    # A deleted asset must not produce a canonical asset export.
                    verified_delete = (
                        export["exitCode"] != 0
                        or int(manifest_export.get("successCount", 0)) == 0
                        or not canonical
                    )
                    verification.append(
                        {
                            "outputPath": output_path,
                            "kind": "create",
                            "verified": verified_delete,
                            "revision": "",
                        }
                    )
            verification_failed = any(not entry["verified"] for entry in verification)
            rollback_ok = not failures and not verification_failed
            report = {
                "schemaVersion": RETARGET_ROLLBACK_REPORT_SCHEMA_VERSION,
                "mode": "Commit",
                "taskId": task_id,
                "valid": rollback_ok,
                "willWriteDisk": True,
                "wroteDisk": True,
                "restoredCount": len(restored),
                "deletedCount": len(deleted),
                "restored": restored,
                "deleted": deleted,
                "failures": failures,
                "independentVerification": verification,
                "verificationFailed": verification_failed,
                "backupDir": str(backup_dir),
                "completedUtc": utc_now_iso(),
            }
            task["rollbackReport"] = report
            revision_set = [
                {"assetPath": entry["outputPath"], "revision": entry["revision"], "revisionStable": True}
                for entry in restored
            ]
            evidence = self._retarget_memory_task_evidence(
                task,
                conclusion=(
                    f"The retarget batch {task_id} was rolled back: {len(restored)} overwritten output(s) restored "
                    f"to their pre-batch Revision and {len(deleted)} newly created output(s) removed."
                    if rollback_ok
                    else f"The retarget batch {task_id} rollback finished with failures and was not fully verified."
                ),
                outcome="rolledBack" if rollback_ok else "failed",
                revision_set=revision_set,
                validation_report_id="",
                workflow_tool="ue_rollback_animation_retarget_batch",
                state="rolled-back" if rollback_ok else "failed",
            )
            return {
                "schemaVersion": "1.0",
                "tool": "ue_rollback_animation_retarget_batch",
                "ok": True,
                "mode": "Commit",
                "taskId": task_id,
                "rollbackDryRunReceipt": rollback_dry_run_receipt,
                "valid": rollback_ok,
                "restoredCount": len(restored),
                "deletedCount": len(deleted),
                "restored": restored,
                "deleted": deleted,
                "failures": failures,
                "independentVerification": verification,
                "verificationFailed": verification_failed,
                "report": report,
                "memoryTaskEvidence": evidence,
                "nextStep": (
                    "Rollback was independently verified; the project state matches the pre-batch Backup."
                    if rollback_ok
                    else "Rollback did not fully verify; inspect failures before continuing."
                ),
            }

    def _retarget_memory_task_evidence(
        self,
        task: dict[str, Any],
        *,
        conclusion: str,
        outcome: str,
        revision_set: list[dict[str, Any]],
        validation_report_id: str,
        workflow_tool: str,
        state: str,
    ) -> dict[str, Any]:
        backup_manifest = task.get("backupManifest", {}) or {}
        entries = backup_manifest.get("entries", []) if isinstance(backup_manifest, dict) else []
        backup_manifest_ref = f"backup-manifest:{task['taskId']}" if entries else "backup-manifest:not-applicable"
        return {
            "schemaVersion": "1.0",
            "tool": "ue_memory_record_task",
            "arguments": {
                "task_key": f"retarget:{task['planId']}",
                "title": f"Animation retarget batch {task['taskId']}",
                "conclusion": conclusion,
                "outcome": outcome,
                "patch_ref": f"patch:{task.get('planDigest', '')}",
                "backup_manifest_ref": backup_manifest_ref,
                "validation_evidence_ref": (
                    f"validation-evidence:{validation_report_id}" if validation_report_id else "validation-evidence:not-applicable"
                ),
                "revision_set": revision_set,
                "scopes": [
                    {"scopeType": "asset", "scopeKey": asset_path}
                    for asset_path in (task.get("savedAssets") or task.get("createdAssets") or [])
                ],
                "confidence": 1.0,
                "patch_details": {
                    "planId": task.get("planId", ""),
                    "planDigest": task.get("planDigest", ""),
                    "retargeter": task.get("retargeter", ""),
                    "outputDirectory": task.get("outputDirectory", ""),
                    "overwriteExisting": task.get("overwriteExisting", False),
                },
                "backup_manifest_details": {
                    "manifestId": task.get("taskId", ""),
                    "backupDir": task.get("backupDir", ""),
                    "entryCount": len(entries),
                },
                "validation_evidence_details": {
                    "state": state,
                    "reportId": validation_report_id or task.get("taskId", ""),
                    "independentReload": True,
                },
                "details": {
                    "retargetTaskId": task.get("taskId", ""),
                    "changeSetId": task.get("changeSetId", ""),
                    "planDigest": task.get("planDigest", ""),
                    "sourceRevision": task.get("sourceRevision", ""),
                    "targetRevision": task.get("targetRevision", ""),
                    "createdAssets": task.get("createdAssets", []),
                    "updatedAssets": task.get("updatedAssets", []),
                    "outputAnimations": task.get("outputs", []),
                    "saveReceipts": task.get("saveReceipts", []),
                    "finalRevisionSet": revision_set,
                    "outcome": outcome,
                    "workflowEvidenceSchemaVersion": "1.0",
                    "workflowTool": workflow_tool,
                },
            },
        }

    def get_animation_retarget_postprocess_context(self, *, task_id: str) -> dict[str, Any]:
        """Return one completed retarget batch context without advancing or mutating the task."""
        with self._lock:
            tasks = getattr(self, "_retarget_batch_tasks", {})
            task = tasks.get(task_id)
            if task is None:
                raise self._workflow_error(
                    "retarget-batch-task-not-found",
                    "The retarget batch task is not active in this MCP server session.",
                )
            if task["status"] not in {"completed", "saved"}:
                raise self._workflow_error(
                    "retarget-postprocess-task-invalid-state",
                    f"Only a completed or saved retarget batch can be post-processed (current state {task['status']}).",
                )
            rollback_report = task.get("rollbackReport")
            if isinstance(rollback_report, dict) and rollback_report.get("valid") is True:
                raise self._workflow_error(
                    "retarget-postprocess-task-rolled-back",
                    "The retarget batch was already rolled back and no longer has valid outputs to post-process.",
                )
            outputs = task.get("outputs", [])
            if not isinstance(outputs, list) or any(not isinstance(item, dict) for item in outputs):
                raise self._workflow_error(
                    "retarget-postprocess-invalid-outputs",
                    "The retarget batch output records are invalid.",
                )
            independent_validation = task.get("independentValidation")
            verification = (
                {
                    "verified": bool(independent_validation.get("verified")),
                    "sampleCount": int(independent_validation.get("sampleCount", 0)),
                    "verifiedCount": int(independent_validation.get("verifiedCount", 0)),
                    "verifiedAssets": [
                        {
                            "assetPath": str(entry.get("assetPath", "")),
                            "revision": str(entry.get("revision", "")),
                        }
                        for entry in independent_validation.get("verifiedAssets", [])
                        if isinstance(entry, dict) and entry.get("assetPath")
                    ],
                }
                if isinstance(independent_validation, dict)
                else {"verified": False, "sampleCount": 0, "verifiedCount": 0, "verifiedAssets": []}
            )
            return {
                "taskId": task["taskId"],
                "status": task["status"],
                "planId": task["planId"],
                "planDigest": task.get("planDigest", ""),
                "retargeter": task["retargeter"],
                "sourceMesh": task["sourceMesh"],
                "targetMesh": task["targetMesh"],
                "outputDirectory": task["outputDirectory"],
                "outputs": [dict(item) for item in outputs],
                "savedAssets": list(task.get("savedAssets", [])),
                "verification": verification,
            }

    @staticmethod
    def _retarget_batch_task_result(task: dict[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": "1.0",
            "tool": "ue_get_animation_retarget_batch",
            "ok": task["status"] not in {"failed"},
            "mode": "RetargetBatchTask",
            "taskId": task["taskId"],
            "status": task["status"],
            "step": task["step"],
            "planId": task["planId"],
            "planDigest": task.get("planDigest", ""),
            "retargeter": task["retargeter"],
            "sourceAssetCount": len(task["sourceAssets"]),
            "outputDirectory": task["outputDirectory"],
            "outputs": task["outputs"],
            "createdAssets": task["createdAssets"],
            "updatedAssets": task.get("updatedAssets", []),
            "backupDir": task.get("backupDir", ""),
            "backupManifest": task.get("backupManifest", {}),
            "history": task["history"],
            "error": task["error"],
            "nextStep": (
                "Retarget batch is complete; the outputs can now be validated, saved and played."
                if task["status"] == "completed"
                else ("The retarget batch failed; inspect error and resolve before retrying."
                      if task["status"] == "failed" else
                      "The retarget batch was cancelled before any animation asset was created.")
            ),
        }

    def _assert_live_retarget_capability(self, capability: str) -> None:
        descriptor = self.live_editor_service._read_descriptor()
        capabilities = descriptor.get("capabilities", [])
        if capability not in capabilities:
            raise self._workflow_error(
                "live-editor-capability-unavailable",
                f"The registered Editor Bridge does not expose the {capability} capability.",
            )

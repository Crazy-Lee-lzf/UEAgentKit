from __future__ import annotations

import json
import secrets
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
RETARGET_SETUP_RECEIPT_PREFIX = "rtg_"


def _chain_name_sorted(chains: list[dict[str, Any]]) -> str:
    return ",".join(f"{c.get('chain', '')}:{c.get('startBone', '')}..{c.get('endBone', '')}" for c in chains)


def _revision_for_mesh(service: Any, mesh_path: str) -> str:
    package_name = mesh_path.rsplit(".", 1)[0]
    package_file = service._package_file(service.config.project_path, package_name, "SkeletalMesh")
    return "sha256:" + sha256_file(package_file)


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
                                receipt=f"{setup_receipt}:{_chain_name_sorted(changes)}",
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
                "result": bridge_result,
                "nextStep": (
                    "Verify the IK Rig state, then create the IK Retargeter in the next step."
                    if changed
                    else "No configuration change was required."
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

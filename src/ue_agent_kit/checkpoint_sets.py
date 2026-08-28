from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_workflow import WorkflowError

CHECKPOINT_SET_SCHEMA_VERSION = "1.0"
CHECKPOINT_SET_PREFIX = "cps_"
CHECKPOINT_SET_CONFIRMATION_PREFIX = "SAVE CHANGE SET CHECKPOINT "
MAX_CHECKPOINT_SETS = 100
MAX_CHECKPOINT_ASSETS = 4


@dataclass(frozen=True)
class ChangeSetCheckpointSetRecord:
    checkpoint_set_id: str
    digest: str
    payload: dict[str, Any]
    path: Path


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _digest(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "checkpointSetDigest"}
    return "sha256:" + hashlib.sha256(_json_bytes(canonical)).hexdigest()


def _utc_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _safe_error(exc: Exception) -> dict[str, Any]:
    return {
        "code": getattr(exc, "code", exc.__class__.__name__),
        "message": str(exc),
    }


class CheckpointSetService:
    """W4-4 aggregate CheckpointSet orchestration over existing W3 child checkpoints."""

    def __init__(self, workflow_service: Any, bounded_batch_service: Any) -> None:
        self.workflow_service = workflow_service
        self.bounded_batch_service = bounded_batch_service
        configured_work_root = getattr(workflow_service.config, "work_root", None)
        self.work_root = Path(configured_work_root).expanduser().resolve() if configured_work_root is not None else None
        self._checkpoint_sets: dict[str, ChangeSetCheckpointSetRecord] = {}
        self._lock = threading.RLock()
        # Private test-only fault seam; never exposed as a public parameter.
        self._fault_after_saved_asset = ""
        self._fail_next_save = False
        self._fault_verify_asset = ""

    def _directory(self, checkpoint_set_id: str) -> Path:
        if self.work_root is None:
            raise WorkflowError("checkpoint-set-rejected", "Checkpoint Set requires a fixed Work Root.")
        path = self.work_root / "checkpoint-sets" / checkpoint_set_id
        if not _is_within(path, self.work_root):
            raise WorkflowError("checkpoint-set-rejected", "Checkpoint Set path escaped the fixed Work Root.")
        return path

    def _persist(self, payload: dict[str, Any]) -> ChangeSetCheckpointSetRecord:
        checkpoint_set_id = str(payload["checkpointSetId"])
        digest = _digest(payload)
        payload["checkpointSetDigest"] = digest
        path = self._directory(checkpoint_set_id) / "checkpoint-set.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        record = ChangeSetCheckpointSetRecord(checkpoint_set_id, digest, payload, path)
        self._checkpoint_sets[checkpoint_set_id] = record
        return record

    def _load(self, checkpoint_set_id: str) -> ChangeSetCheckpointSetRecord:
        with self._lock:
            path = self._directory(checkpoint_set_id) / "checkpoint-set.json"
            if not path.exists():
                raise WorkflowError("checkpoint-set-not-found", f"Checkpoint Set {checkpoint_set_id} was not found.")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise WorkflowError("checkpoint-set-tampered", "The stored Checkpoint Set could not be decoded.") from exc
            if str(payload.get("checkpointSetId") or "") != checkpoint_set_id:
                raise WorkflowError("checkpoint-set-tampered", "The stored Checkpoint Set identity does not match its path.")
            expected_digest = str(payload.get("checkpointSetDigest") or "")
            actual_digest = _digest(payload)
            if expected_digest != actual_digest:
                raise WorkflowError("checkpoint-set-tampered", "The stored Checkpoint Set digest does not match its payload.")
            record = ChangeSetCheckpointSetRecord(checkpoint_set_id, actual_digest, payload, path)
            self._checkpoint_sets[checkpoint_set_id] = record
            return record

    def _require_applied_execution(self, batch_execution_id: str) -> dict[str, Any]:
        execution = self.bounded_batch_service.get_batch_execution(batch_execution_id)
        payload = execution.payload
        if payload.get("state") != "applied":
            raise WorkflowError(
                "checkpoint-set-execution-not-applied",
                "Checkpoint Set accepts only a fully applied Batch Execution.",
                details={"batchExecutionId": batch_execution_id, "state": payload.get("state")},
            )
        return payload

    def _execution_asset_order(self, exec_payload: dict[str, Any]) -> list[str]:
        asset_order = exec_payload.get("assetOrder")
        if not isinstance(asset_order, list) or not asset_order:
            asset_path = exec_payload.get("assetPath")
            asset_order = [asset_path] if isinstance(asset_path, str) and asset_path else []
        if not 1 <= len(asset_order) <= MAX_CHECKPOINT_ASSETS:
            raise WorkflowError(
                "checkpoint-set-asset-count-invalid",
                "Batch Execution asset order is outside the bounded W4 range.",
                details={"assetCount": len(asset_order)},
            )
        return [str(asset) for asset in asset_order]

    def _preview_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": CHECKPOINT_SET_SCHEMA_VERSION,
            "tool": "ue_save_change_set_checkpoint",
            "ok": True,
            "checkpointSetId": payload["checkpointSetId"],
            "batchExecutionId": payload["batchExecutionId"],
            "changeSetId": payload["changeSetId"],
            "state": payload["state"],
            "assetCount": len(payload["assetOrder"]),
            "assets": [
                {
                    "assetPath": child["assetPath"],
                    "checkpointId": child["checkpointId"],
                    "state": child["state"],
                }
                for child in payload["childCheckpoints"]
            ],
            "confirmationRequired": payload["confirmationRequired"],
            "savePerformed": False,
        }

    def _commit_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        response: dict[str, Any] = {
            "schemaVersion": CHECKPOINT_SET_SCHEMA_VERSION,
            "tool": "ue_save_change_set_checkpoint",
            "ok": True,
            "checkpointSetId": payload["checkpointSetId"],
            "batchExecutionId": payload["batchExecutionId"],
            "changeSetId": payload["changeSetId"],
            "state": payload["state"],
            "assetCount": len(payload["assetOrder"]),
            "savedCount": payload.get("savedCount", 0),
            "assets": [
                {
                    "assetPath": child["assetPath"],
                    "checkpointId": child["checkpointId"],
                    "state": child["state"],
                    "afterRevision": child.get("afterRevision", ""),
                }
                for child in payload["childCheckpoints"]
            ],
            "strongVerifyPerformed": False,
            "nextActions": [
                {
                    "tool": "ue_verify_change_set_checkpoint",
                    "arguments": {"checkpoint_set_id": payload["checkpointSetId"]},
                    "reason": "Run aggregate Strong Verify after a saved checkpoint set (W4-5).",
                }
            ],
        }
        if payload["state"] == "partially_saved":
            response["persistedAssets"] = payload.get("persistedAssets", [])
            response["failedAsset"] = payload.get("failedAsset", "")
            response["pendingAssets"] = payload.get("pendingAssets", [])
            response["failureBoundary"] = payload.get("failureBoundary", {})
        return response

    def preview(self, *, batch_execution_id: str) -> dict[str, Any]:
        with self._lock:
            exec_payload = self._require_applied_execution(batch_execution_id)
            change_set_id = str(exec_payload["changeSetId"])
            asset_order = self._execution_asset_order(exec_payload)
            child_checkpoints: list[dict[str, Any]] = []
            failed_asset = ""
            failure: dict[str, Any] = {}
            for asset_index, asset_path in enumerate(asset_order):
                try:
                    preview = self.workflow_service.save_authorized_asset(
                        asset_path,
                        mode="Preview",
                        verification_mode="checkpoint",
                        change_set_id=change_set_id,
                    )
                except Exception as exc:
                    failed_asset = asset_path
                    failure = _safe_error(exc)
                    break
                child_checkpoints.append(
                    {
                        "assetIndex": asset_index,
                        "assetPath": asset_path,
                        "checkpointId": str(preview["checkpointId"]),
                        "saveReceipt": str(preview["saveReceipt"]),
                        "state": "prepared",
                        "beforeRevision": str(preview["expectedDiskRevision"]),
                        "afterRevision": "",
                        "preparedAtUtc": _utc_now(),
                        "savedAtUtc": "",
                    }
                )
            if failure:
                checkpoint_set_id = CHECKPOINT_SET_PREFIX + secrets.token_urlsafe(18)
                payload: dict[str, Any] = {
                    "schemaVersion": CHECKPOINT_SET_SCHEMA_VERSION,
                    "checkpointSetId": checkpoint_set_id,
                    "batchExecutionId": batch_execution_id,
                    "batchPlanId": exec_payload["batchPlanId"],
                    "batchPlanDigest": exec_payload["batchPlanDigest"],
                    "changeSetId": change_set_id,
                    "state": "failed",
                    "assetOrder": asset_order,
                    "childCheckpoints": child_checkpoints,
                    "savedCount": 0,
                    "failedAsset": failed_asset,
                    "pendingAssets": [asset for asset in asset_order if asset not in {child["assetPath"] for child in child_checkpoints}],
                    "persistedAssets": [],
                    "failureBoundary": {"phase": "preflight", **failure},
                    "confirmationRequired": "",
                    "preparedAtUtc": _utc_now(),
                    "saveStartedAtUtc": "",
                    "savedAtUtc": "",
                    "updatedAtUtc": _utc_now(),
                }
                self._persist(payload)
                raise WorkflowError(
                    "checkpoint-set-preview-failed",
                    "Checkpoint Set Preview failed before any package Save.",
                    details={
                        "checkpointSetId": checkpoint_set_id,
                        "failedAsset": failed_asset,
                        "failureBoundary": payload["failureBoundary"],
                        "childCheckpointCount": len(child_checkpoints),
                    },
                )
            checkpoint_set_id = CHECKPOINT_SET_PREFIX + secrets.token_urlsafe(18)
            payload = {
                "schemaVersion": CHECKPOINT_SET_SCHEMA_VERSION,
                "checkpointSetId": checkpoint_set_id,
                "batchExecutionId": batch_execution_id,
                "batchPlanId": exec_payload["batchPlanId"],
                "batchPlanDigest": exec_payload["batchPlanDigest"],
                "changeSetId": change_set_id,
                "state": "checkpoint_prepared",
                "assetOrder": asset_order,
                "childCheckpoints": child_checkpoints,
                "savedCount": 0,
                "failedAsset": "",
                "pendingAssets": [],
                "persistedAssets": [],
                "failureBoundary": {},
                "confirmationRequired": CHECKPOINT_SET_CONFIRMATION_PREFIX + checkpoint_set_id,
                "preparedAtUtc": _utc_now(),
                "saveStartedAtUtc": "",
                "savedAtUtc": "",
                "updatedAtUtc": _utc_now(),
            }
            self._persist(payload)
            return self._preview_response(payload)

    def commit(
        self,
        *,
        checkpoint_set_id: str,
        confirmation: str,
    ) -> dict[str, Any]:
        with self._lock:
            record = self._load(checkpoint_set_id)
            payload = record.payload
            if payload["state"] == "saved":
                return self._commit_response(payload)
            if payload["state"] != "checkpoint_prepared":
                raise WorkflowError(
                    "checkpoint-set-commit-invalid-state",
                    "Checkpoint Set Commit requires the exact checkpoint_prepared state.",
                    details={"checkpointSetId": checkpoint_set_id, "state": payload["state"]},
                )
            if confirmation != payload["confirmationRequired"]:
                raise WorkflowError(
                    "checkpoint-set-confirmation-required",
                    "Checkpoint Set Commit confirmation did not exactly match the required phrase.",
                )
            exec_payload = self._require_applied_execution(payload["batchExecutionId"])
            change_set_id = str(payload["changeSetId"])
            if change_set_id != str(exec_payload["changeSetId"]):
                raise WorkflowError("checkpoint-set-execution-mismatch", "Batch Execution Change Set changed after Preview.")

            # Commit-time all-assets revalidation before the first Save.
            for child in payload["childCheckpoints"]:
                try:
                    self.workflow_service.preflight_checkpoint_commit(
                        child["assetPath"],
                        child["saveReceipt"],
                        change_set_id=change_set_id,
                    )
                except Exception as exc:
                    payload["state"] = "failed"
                    payload["savedCount"] = 0
                    payload["failedAsset"] = child["assetPath"]
                    payload["pendingAssets"] = [c["assetPath"] for c in payload["childCheckpoints"]]
                    payload["persistedAssets"] = []
                    payload["failureBoundary"] = {"phase": "commit-preflight", **_safe_error(exc)}
                    payload["updatedAtUtc"] = _utc_now()
                    self._persist(payload)
                    raise WorkflowError(
                        "checkpoint-set-commit-preflight-failed",
                        "Commit-time global revalidation failed before any package Save.",
                        details={
                            "checkpointSetId": checkpoint_set_id,
                            "failedAsset": child["assetPath"],
                            "failureBoundary": payload["failureBoundary"],
                        },
                    ) from exc

            payload["state"] = "saving"
            payload["saveStartedAtUtc"] = _utc_now()
            payload["updatedAtUtc"] = _utc_now()
            self._persist(payload)

            persisted_assets: list[str] = []
            for child in payload["childCheckpoints"]:
                asset_path = child["assetPath"]
                try:
                    if self._fail_next_save:
                        raise WorkflowError(
                            "injected-mid-save-failure",
                            "Test-only fault seam injected before this child Save.",
                        )
                    committed = self.workflow_service.save_authorized_asset(
                        asset_path,
                        mode="Commit",
                        verification_mode="checkpoint",
                        save_receipt=child["saveReceipt"],
                        confirmation=f"SAVE {child['saveReceipt']}",
                        change_set_id=change_set_id,
                    )
                except Exception as exc:
                    child["state"] = "failed"
                    child["failure"] = _safe_error(exc)
                    payload["failedAsset"] = asset_path
                    payload["pendingAssets"] = [
                        c["assetPath"]
                        for c in payload["childCheckpoints"]
                        if c["assetPath"] not in persisted_assets
                    ]
                    payload["persistedAssets"] = list(persisted_assets)
                    payload["failureBoundary"] = {"phase": "save", **_safe_error(exc)}
                    payload["state"] = "partially_saved" if persisted_assets else "failed"
                    payload["savedCount"] = len(persisted_assets)
                    payload["updatedAtUtc"] = _utc_now()
                    self._persist(payload)
                    raise WorkflowError(
                        "checkpoint-set-save-failed",
                        "A child checkpoint Save failed; the checkpoint set is not fully saved.",
                        details={
                            "checkpointSetId": checkpoint_set_id,
                            "failedAsset": asset_path,
                            "persistedAssets": payload["persistedAssets"],
                            "pendingAssets": payload["pendingAssets"],
                            "failureBoundary": payload["failureBoundary"],
                        },
                    ) from exc
                if str(committed.get("checkpointId") or "") != child["checkpointId"]:
                    raise WorkflowError("checkpoint-set-child-mismatch", "The W3 child checkpoint identity did not match.")
                effective_receipts = committed.get("effectiveReceipts") or []
                live_receipt = str(effective_receipts[0]) if effective_receipts else ""
                try:
                    promoted = self.workflow_service.create_authorized_save_rollback_manifest(
                        str(committed.get("saveReceipt") or child["saveReceipt"]),
                        live_receipt,
                    )
                except Exception as exc:
                    child["state"] = "saved_unrecoverable"
                    child["rollbackPromotionFailure"] = _safe_error(exc)
                    payload["failedAsset"] = asset_path
                    payload["pendingAssets"] = [
                        c["assetPath"]
                        for c in payload["childCheckpoints"]
                        if c["assetPath"] not in persisted_assets
                    ]
                    payload["persistedAssets"] = list(persisted_assets)
                    payload["failureBoundary"] = {"phase": "rollback-promotion", **_safe_error(exc)}
                    payload["state"] = "partially_saved" if persisted_assets else "failed"
                    payload["savedCount"] = len(persisted_assets)
                    payload["updatedAtUtc"] = _utc_now()
                    self._persist(payload)
                    raise WorkflowError(
                        "checkpoint-set-rollback-promotion-failed",
                        "The package Save succeeded but rollback-manifest promotion failed; recovery readiness is incomplete.",
                        details={
                            "checkpointSetId": checkpoint_set_id,
                            "failedAsset": asset_path,
                            "persistedAssets": payload["persistedAssets"],
                            "pendingAssets": payload["pendingAssets"],
                            "failureBoundary": payload["failureBoundary"],
                        },
                    ) from exc
                child["state"] = "saved"
                child["saveReceipt"] = str(committed.get("saveReceipt") or child["saveReceipt"])
                child["afterRevision"] = str(committed.get("afterRevision") or "")
                child["rollbackManifestId"] = str(promoted.get("rollbackManifestId", ""))
                child["rollbackManifestPath"] = str(
                    self.workflow_service.config.backup_root / "live-save" / child["saveReceipt"] / "rollback-manifest.json"
                )
                child["rollbackState"] = "ready"
                child["savedAtUtc"] = _utc_now()
                persisted_assets.append(asset_path)
                payload["savedCount"] = len(persisted_assets)
                payload["updatedAtUtc"] = _utc_now()
                self._persist(payload)
                if self._fault_after_saved_asset == asset_path:
                    self._fail_next_save = True

            payload["state"] = "saved"
            payload["savedCount"] = len(payload["childCheckpoints"])
            payload["persistedAssets"] = list(persisted_assets)
            payload["pendingAssets"] = []
            payload["failedAsset"] = ""
            payload["savedAtUtc"] = _utc_now()
            payload["updatedAtUtc"] = _utc_now()
            self._persist(payload)
            return self._commit_response(payload)

    def _run_live_action(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        live_editor = getattr(self.workflow_service, "live_editor_service", None)
        if live_editor is None:
            raise WorkflowError("live-editor-required", "W4-5 validation actions require Live Editor mode.")
        evidence_store = getattr(self.workflow_service, "verification_evidence_store", None)
        token = None
        if evidence_store is not None:
            token = evidence_store.begin_registered_tool(tool_name, params)
        response = live_editor.call_tool(tool_name, params)
        if evidence_store is not None:
            evidence_store.finish_registered_tool(token, response)
        return response

    def verify(self, *, checkpoint_set_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._load(checkpoint_set_id)
            payload = record.payload
            if payload["state"] != "saved":
                raise WorkflowError(
                    "checkpoint-set-verify-not-saved",
                    "Aggregate verification requires a fully saved Checkpoint Set.",
                    details={"checkpointSetId": checkpoint_set_id, "state": payload["state"]},
                )
            existing = payload.get("verification")
            if isinstance(existing, dict) and existing.get("state") == "verified":
                return self._verification_response(payload, strong_verify_process_count=0)

            verification: dict[str, Any] = {
                "state": "verifying",
                "startedAtUtc": (existing or {}).get("startedAtUtc") or _utc_now(),
                "updatedAtUtc": _utc_now(),
                "completedAtUtc": "",
                "childResults": list((existing or {}).get("childResults", [])),
                "verifiedCount": int((existing or {}).get("verifiedCount", 0)),
                "failedCount": int((existing or {}).get("failedCount", 0)),
                "semanticDiff": {},
                "verificationPlan": {},
                "validationActions": [],
                "unsupportedRequiredActions": [],
                "trust": {},
                "failureBoundary": {},
            }
            payload["verification"] = verification
            self._persist(payload)

            change_set_id = str(payload["changeSetId"])
            existing_results = {str(item["assetPath"]): item for item in verification["childResults"]}
            child_results: list[dict[str, Any]] = []
            strong_verify_process_count = 0
            for child in payload["childCheckpoints"]:
                asset_path = str(child["assetPath"])
                previous = existing_results.get(asset_path, {})
                if previous.get("verified") is True:
                    child_results.append(previous)
                    continue
                try:
                    if self._fault_verify_asset == asset_path:
                        raise WorkflowError("checkpoint-canonical-mismatch", "Test-only canonical mismatch seam.")
                    result = self.workflow_service.verify_live_write_checkpoint(
                        str(child["checkpointId"]),
                        change_set_id=change_set_id,
                        asset_path=asset_path,
                    )
                    strong_verify_process_count += int(result.get("childUnrealProcessCount") or 0)
                    child_results.append(
                        {
                            "assetPath": asset_path,
                            "checkpointId": child["checkpointId"],
                            "verified": True,
                            "state": "verified",
                            "verificationKind": result.get("verificationKind", "independent-verified"),
                            "afterRevision": child.get("afterRevision", ""),
                            "strongVerificationRevision": result.get("artifactRevision", ""),
                            "evidenceId": result.get("reportId", ""),
                            "failure": {},
                        }
                    )
                except Exception as exc:
                    child_results.append(
                        {
                            "assetPath": asset_path,
                            "checkpointId": child["checkpointId"],
                            "verified": False,
                            "state": "failed",
                            "verificationKind": "",
                            "afterRevision": child.get("afterRevision", ""),
                            "strongVerificationRevision": "",
                            "evidenceId": "",
                            "failure": _safe_error(exc),
                        }
                    )
            verification["childResults"] = child_results
            verification["verifiedCount"] = sum(1 for item in child_results if item["verified"])
            verification["failedCount"] = sum(1 for item in child_results if not item["verified"])
            verification["updatedAtUtc"] = _utc_now()
            self._persist(payload)

            semantic_diff: dict[str, Any] = {}
            semantic_failure: dict[str, Any] = {}
            try:
                semantic_diff = self.workflow_service.analyze_semantic_diff(change_set_id, stage="verified")
            except Exception as exc:
                semantic_failure = _safe_error(exc)
            verification["semanticDiff"] = {
                "stage": semantic_diff.get("evidenceStage", {}).get("selected", ""),
                "verified": bool(
                    semantic_diff
                    and semantic_diff.get("evidenceStage", {}).get("selected") == "verified"
                    and semantic_diff.get("summary", {}).get("missingExpectedCount", 0) == 0
                    and semantic_diff.get("summary", {}).get("unexpectedCount", 0) == 0
                    and semantic_diff.get("summary", {}).get("analysisGapCount", 0) == 0
                ),
                "missingExpectedCount": semantic_diff.get("summary", {}).get("missingExpectedCount", 0),
                "unexpectedCount": semantic_diff.get("summary", {}).get("unexpectedCount", 0),
                "analysisGapCount": semantic_diff.get("summary", {}).get("analysisGapCount", 0),
                "totalAssetCount": semantic_diff.get("summary", {}).get("totalAssetCount", 0),
                "returnedAssetCount": semantic_diff.get("summary", {}).get("returnedAssetCount", 0),
                "failure": semantic_failure,
            }

            plan: dict[str, Any] = {}
            plan_failure: dict[str, Any] = {}
            try:
                plan = self.workflow_service.build_verification_plan(change_set_id)
            except Exception as exc:
                plan_failure = _safe_error(exc)
            verification["verificationPlan"] = {
                "planId": plan.get("planId", ""),
                "planFingerprint": plan.get("planFingerprint", ""),
                "requiredAssertionCount": plan.get("summary", {}).get("required", 0),
                "failure": plan_failure,
            }

            unsupported_required: list[dict[str, Any]] = []
            validation_actions: list[dict[str, Any]] = []
            if plan:
                allowed_assets = set(payload["assetOrder"])
                for assertion in plan.get("assertions", []):
                    if assertion.get("requirement") != "required":
                        continue
                    next_action = assertion.get("nextAction") or {}
                    tool_name = str(next_action.get("tool", ""))
                    subject = str((next_action.get("arguments") or {}).get("asset_path", ""))
                    if tool_name in {"ue_analyze_semantic_diff", "ue_verify_asset"}:
                        continue
                    if tool_name not in {"ue_compile_blueprint", "ue_validate_asset"} or subject not in allowed_assets:
                        unsupported_required.append(
                            {
                                "tool": tool_name,
                                "subject": subject,
                                "reason": "Required action is outside the bounded W4-5 automatic closure set.",
                            }
                        )
                seen: set[tuple[str, str]] = set()
                for assertion in plan.get("assertions", []):
                    if assertion.get("requirement") != "required":
                        continue
                    next_action = assertion.get("nextAction") or {}
                    tool_name = str(next_action.get("tool", ""))
                    if tool_name not in {"ue_compile_blueprint", "ue_validate_asset"}:
                        continue
                    subject = str((next_action.get("arguments") or {}).get("asset_path", ""))
                    if subject not in allowed_assets:
                        continue
                    key = (tool_name, subject)
                    if key in seen:
                        continue
                    seen.add(key)
                    try:
                        response = self._run_live_action(tool_name, {"assetPath": subject})
                        result = response.get("result") if isinstance(response.get("result"), dict) else {}
                        validation = result.get("validationEvidence") if isinstance(result.get("validationEvidence"), dict) else {}
                        validation_actions.append(
                            {
                                "tool": tool_name,
                                "subject": subject,
                                "state": "success",
                                "evidenceId": result.get("evidenceId", "") or validation.get("evidenceId", ""),
                            }
                        )
                    except Exception as exc:
                        validation_actions.append(
                            {
                                "tool": tool_name,
                                "subject": subject,
                                "state": "failed",
                                "evidenceId": "",
                                "failure": _safe_error(exc),
                            }
                        )
            verification["validationActions"] = validation_actions
            verification["unsupportedRequiredActions"] = unsupported_required

            trust: dict[str, Any] = {}
            trust_failure: dict[str, Any] = {}
            try:
                trust = self.workflow_service.evaluate_trust_verdict(change_set_id)
            except Exception as exc:
                trust_failure = _safe_error(exc)
            verdict = trust.get("verdict") if isinstance(trust.get("verdict"), dict) else {}
            verification["trust"] = {
                "state": verdict.get("state", ""),
                "reasonCodes": verdict.get("reasonCodes", []),
                "statement": verdict.get("statement", ""),
                "verifiedAssets": trust.get("verificationScope", {}).get("verifiedAssets", []),
                "unresolvedRiskCount": trust.get("summary", {}).get("unresolvedRiskCount", 0),
                "analysisGapCount": trust.get("summary", {}).get("analysisGapCount", 0),
                "unexpectedChangeCount": trust.get("summary", {}).get("unexpectedChangeCount", 0),
                "failure": trust_failure,
            }

            all_children_verified = verification["verifiedCount"] == len(payload["childCheckpoints"])
            semantic_ok = bool(verification["semanticDiff"].get("verified"))
            actions_ok = not unsupported_required and all(item["state"] == "success" for item in validation_actions)
            trust_ok = verification["trust"].get("state") == "verified"
            if all_children_verified and semantic_ok and actions_ok and trust_ok:
                verification["state"] = "verified"
            elif verification["verifiedCount"] > 0:
                verification["state"] = "partially_verified"
            else:
                verification["state"] = "failed"
            verification["completedAtUtc"] = _utc_now()
            verification["updatedAtUtc"] = _utc_now()
            payload["verification"] = verification
            self._persist(payload)
            return self._verification_response(payload, strong_verify_process_count=strong_verify_process_count)

    def _verification_response(
        self,
        payload: dict[str, Any],
        *,
        strong_verify_process_count: int,
    ) -> dict[str, Any]:
        verification = payload.get("verification", {})
        child_results = verification.get("childResults", [])
        return {
            "schemaVersion": CHECKPOINT_SET_SCHEMA_VERSION,
            "tool": "ue_verify_change_set_checkpoint",
            "ok": True,
            "checkpointSetId": payload["checkpointSetId"],
            "changeSetId": payload["changeSetId"],
            "state": verification.get("state", "failed"),
            "assetCount": len(payload["assetOrder"]),
            "savedCount": payload.get("savedCount", 0),
            "verifiedCount": verification.get("verifiedCount", 0),
            "children": [
                {
                    "assetPath": item.get("assetPath", ""),
                    "checkpointId": item.get("checkpointId", ""),
                    "verified": item.get("verified", False),
                    "state": item.get("state", ""),
                    "verificationKind": item.get("verificationKind", ""),
                    "afterRevision": item.get("afterRevision", ""),
                    "strongVerificationRevision": item.get("strongVerificationRevision", ""),
                    "failure": item.get("failure", {}),
                }
                for item in child_results
            ],
            "semanticDiff": verification.get("semanticDiff", {}),
            "verificationPlan": verification.get("verificationPlan", {}),
            "validationActions": verification.get("validationActions", []),
            "unsupportedRequiredActions": verification.get("unsupportedRequiredActions", []),
            "trust": verification.get("trust", {}),
            "strongVerifyProcessCount": strong_verify_process_count,
            "nextActions": [
                {
                    "tool": "ue_verify_change_set_checkpoint",
                    "arguments": {"checkpoint_set_id": payload["checkpointSetId"]},
                    "reason": "Re-run aggregate verification only after resolving any incomplete required evidence.",
                }
            ] if verification.get("state") != "verified" else [],
        }

    def load_payload(self, checkpoint_set_id: str) -> dict[str, Any]:
        with self._lock:
            return self._load(checkpoint_set_id).payload

    def get(self, *, checkpoint_set_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._load(checkpoint_set_id).payload
            if payload["state"] in {"checkpoint_prepared", "saved", "partially_saved", "failed"}:
                return {
                    "schemaVersion": CHECKPOINT_SET_SCHEMA_VERSION,
                    "tool": "ue_save_change_set_checkpoint",
                    "ok": True,
                    "checkpointSetId": payload["checkpointSetId"],
                    "batchExecutionId": payload["batchExecutionId"],
                    "changeSetId": payload["changeSetId"],
                    "state": payload["state"],
                    "assetCount": len(payload["assetOrder"]),
                    "savedCount": payload.get("savedCount", 0),
                    "assets": [
                        {
                            "assetPath": child["assetPath"],
                            "checkpointId": child["checkpointId"],
                            "state": child["state"],
                            "afterRevision": child.get("afterRevision", ""),
                        }
                        for child in payload["childCheckpoints"]
                    ],
                    "strongVerifyPerformed": False,
                }
            return self._commit_response(payload)

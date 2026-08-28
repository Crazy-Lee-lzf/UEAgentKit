from __future__ import annotations

import hashlib
import json
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_workflow import WorkflowError
from .checkpoint_sets import CheckpointSetService

RECOVERY_SCHEMA_VERSION = "1.0"
RECOVERY_PREFIX = "lwbr_"
RECOVERY_CONFIRMATION_PREFIX = "RECOVER LIVE WRITE BATCH "
MAX_RECOVERIES = 100


@dataclass(frozen=True)
class LiveWriteBatchRecoveryRecord:
    recovery_id: str
    digest: str
    payload: dict[str, Any]
    path: Path


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(payload: dict[str, Any]) -> str:
    canonical = {key: value for key, value in payload.items() if key != "recoveryDigest"}
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


class BatchRecoveryService:
    def __init__(self, workflow_service: Any, bounded_batch_service: Any, checkpoint_set_service: CheckpointSetService) -> None:
        self.workflow_service = workflow_service
        self.bounded_batch_service = bounded_batch_service
        self.checkpoint_set_service = checkpoint_set_service
        configured_work_root = getattr(workflow_service.config, "work_root", None)
        self.work_root = Path(configured_work_root).expanduser().resolve() if configured_work_root is not None else None
        self._recoveries: dict[str, LiveWriteBatchRecoveryRecord] = {}
        self._lock = threading.RLock()

    def _directory(self, recovery_id: str) -> Path:
        if self.work_root is None:
            raise WorkflowError("batch-recovery-rejected", "Batch Recovery requires a fixed Work Root.")
        path = self.work_root / "batch-recoveries" / recovery_id
        if not _is_within(path, self.work_root):
            raise WorkflowError("batch-recovery-rejected", "Batch Recovery path escaped the fixed Work Root.")
        return path

    def _persist(self, payload: dict[str, Any]) -> LiveWriteBatchRecoveryRecord:
        recovery_id = str(payload["recoveryId"])
        payload["recoveryDigest"] = _digest(payload)
        path = self._directory(recovery_id) / "recovery.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        record = LiveWriteBatchRecoveryRecord(recovery_id, payload["recoveryDigest"], payload, path)
        self._recoveries[recovery_id] = record
        return record

    def _load(self, recovery_id: str) -> LiveWriteBatchRecoveryRecord:
        with self._lock:
            path = self._directory(recovery_id) / "recovery.json"
            if not path.exists():
                raise WorkflowError("batch-recovery-not-found", f"Batch Recovery {recovery_id} was not found.")
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                raise WorkflowError("batch-recovery-tampered", "The stored Batch Recovery could not be decoded.") from exc
            if str(payload.get("recoveryId") or "") != recovery_id:
                raise WorkflowError("batch-recovery-tampered", "The stored Batch Recovery identity does not match its path.")
            expected = str(payload.get("recoveryDigest") or "")
            actual = _digest(payload)
            if expected != actual:
                raise WorkflowError("batch-recovery-tampered", "The stored Batch Recovery digest does not match its payload.")
            record = LiveWriteBatchRecoveryRecord(recovery_id, actual, payload, path)
            self._recoveries[recovery_id] = record
            return record

    def _execution_asset_order(self, exec_payload: dict[str, Any]) -> list[str]:
        order = exec_payload.get("assetOrder")
        if not isinstance(order, list) or not order:
            order = [exec_payload.get("assetPath", "")]
        return [str(item) for item in order]

    def _find_checkpoint_set_for_execution(self, batch_execution_id: str) -> dict[str, Any] | None:
        if self.work_root is None:
            return None
        root = self.work_root / "checkpoint-sets"
        if not root.is_dir():
            return None
        for path in sorted(root.glob("cps_*/checkpoint-set.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(payload.get("batchExecutionId") or "") == batch_execution_id:
                return payload
        return None

    def _build_plan(
        self,
        *,
        batch_execution_id: str,
        checkpoint_set_id: str = "",
    ) -> dict[str, Any]:
        exec_record = self.bounded_batch_service.get_batch_execution(batch_execution_id)
        exec_payload = exec_record.payload
        checkpoint_payload = None
        if checkpoint_set_id:
            checkpoint_payload = self.checkpoint_set_service.load_payload(checkpoint_set_id)
        else:
            checkpoint_payload = self._find_checkpoint_set_for_execution(batch_execution_id)
        source_state = str(exec_payload.get("state", ""))
        saved_assets: list[dict[str, Any]] = []
        if checkpoint_payload and checkpoint_payload.get("state") in {"saved", "partially_saved", "failed"}:
            saved_assets = [
                dict(child)
                for child in checkpoint_payload.get("childCheckpoints", [])
                if child.get("state") in {"saved", "saved_unrecoverable"}
            ]
        persisted_asset_paths = set(checkpoint_payload.get("persistedAssets", [])) if checkpoint_payload else set()
        persisted_assets = [item for item in saved_assets if item["assetPath"] in persisted_asset_paths]
        resident_operations: list[dict[str, Any]] = []
        for operation in exec_payload.get("operations", []):
            if operation.get("assetPath") in persisted_asset_paths:
                continue
            if operation.get("state") in {"verified", "applied", "applied_unverified"} and operation.get("transactionId"):
                resident_operations.append(operation)
        # Preserve strict global reverse order from the execution record.
        resident_by_id = {op["batchOperationId"]: op for op in resident_operations}
        recovery_order = [
            item
            for item in exec_payload.get("recoveryOrder", [])
            if item in resident_by_id
        ]
        resident_operations = [resident_by_id[item] for item in recovery_order]
        return {
            "batchExecutionId": batch_execution_id,
            "batchPlanId": exec_payload.get("batchPlanId", ""),
            "changeSetId": exec_payload.get("changeSetId", ""),
            "checkpointSetId": checkpoint_payload.get("checkpointSetId", "") if checkpoint_payload else "",
            "sourceState": source_state,
            "editorSessionId": str(exec_payload.get("operations", [{}])[0].get("editorSessionId", "")) if exec_payload.get("operations") else "",
            "savedAssets": persisted_assets,
            "residentOperations": resident_operations,
            "recoveryOrder": recovery_order,
        }

    def _preflight(self, plan: dict[str, Any]) -> list[str]:
        blocked: list[str] = []
        if plan["checkpointSetId"]:
            for child in plan["savedAssets"]:
                if child.get("rollbackManifestId") and child.get("rollbackManifestPath"):
                    manifest_path = Path(child["rollbackManifestPath"])
                    if not manifest_path.is_file():
                        blocked.append(f"missing-rollback-manifest:{child['assetPath']}")
                else:
                    blocked.append(f"rollback-not-ready:{child['assetPath']}")
        if plan["residentOperations"]:
            editor_session_id = plan["editorSessionId"]
            current = None
            try:
                current = self.workflow_service.live_editor_service.status().get("sessionId", "")
            except Exception:
                current = ""
            if not current or current != editor_session_id:
                blocked.append("editor-session-unavailable")
        return blocked

    def preview(self, *, batch_execution_id: str) -> dict[str, Any]:
        with self._lock:
            plan = self._build_plan(batch_execution_id=batch_execution_id)
            blocked = self._preflight(plan)
            recovery_id = RECOVERY_PREFIX + secrets.token_urlsafe(18)
            payload: dict[str, Any] = {
                "schemaVersion": RECOVERY_SCHEMA_VERSION,
                "recoveryId": recovery_id,
                "batchExecutionId": plan["batchExecutionId"],
                "batchPlanId": plan["batchPlanId"],
                "changeSetId": plan["changeSetId"],
                "checkpointSetId": plan["checkpointSetId"],
                "state": "blocked" if blocked else "recovery_prepared",
                "sourceState": plan["sourceState"],
                "editorSessionId": plan["editorSessionId"],
                "savedAssets": plan["savedAssets"],
                "residentOperations": plan["residentOperations"],
                "recoveryOrder": plan["recoveryOrder"],
                "completedSteps": [],
                "failedStep": "",
                "pendingSteps": list(plan["recoveryOrder"]),
                "blockedReasons": blocked,
                "failureBoundary": {},
                "confirmationRequired": RECOVERY_CONFIRMATION_PREFIX + recovery_id,
                "preparedAtUtc": _utc_now(),
                "updatedAtUtc": _utc_now(),
                "completedAtUtc": "",
            }
            self._persist(payload)
            return self._preview_response(payload)

    def _preview_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": RECOVERY_SCHEMA_VERSION,
            "tool": "ue_recover_live_write_batch",
            "ok": True,
            "recoveryId": payload["recoveryId"],
            "batchExecutionId": payload["batchExecutionId"],
            "checkpointSetId": payload["checkpointSetId"],
            "state": payload["state"],
            "sourceState": payload["sourceState"],
            "savedAssetCount": len(payload["savedAssets"]),
            "residentOperationCount": len(payload["residentOperations"]),
            "recoveryOrder": payload["recoveryOrder"],
            "blockedReasons": payload["blockedReasons"],
            "confirmationRequired": payload["confirmationRequired"],
        }

    def commit(self, *, recovery_id: str, confirmation: str) -> dict[str, Any]:
        with self._lock:
            record = self._load(recovery_id)
            payload = record.payload
            if payload["state"] == "recovered":
                return self._commit_response(payload)
            if payload["state"] not in {"recovery_prepared", "blocked", "partially_recovered"}:
                raise WorkflowError(
                    "batch-recovery-commit-invalid-state",
                    "Batch Recovery Commit requires recovery_prepared, blocked, or explicitly resumed partially_recovered state.",
                    details={"recoveryId": recovery_id, "state": payload["state"]},
                )
            if confirmation != payload["confirmationRequired"]:
                raise WorkflowError(
                    "batch-recovery-confirmation-required",
                    "Batch Recovery confirmation did not exactly match the required phrase.",
                )
            plan = self._build_plan(
                batch_execution_id=payload["batchExecutionId"],
                checkpoint_set_id=payload["checkpointSetId"],
            )
            completed_steps = list(payload.get("completedSteps", []))
            completed_resident = {
                str(step.get("batchOperationId", ""))
                for step in completed_steps
                if step.get("kind") == "resident-undo"
            }
            completed_disk = {
                str(step.get("assetPath", ""))
                for step in completed_steps
                if step.get("kind") == "disk-rollback"
            }
            blocked: list[str] = []
            for step in completed_steps:
                if step.get("kind") != "disk-rollback":
                    continue
                asset_path = str(step.get("assetPath", ""))
                expected_revision = str(step.get("restoredRevision", ""))
                try:
                    freshness = self.workflow_service.freshness.inspect_asset(asset_path)
                except Exception:
                    blocked.append(f"completed-disk-step-unverifiable:{asset_path}")
                    continue
                if str(freshness.get("diskRevision", "")) != expected_revision:
                    blocked.append(f"completed-disk-step-stale:{asset_path}")
            remaining_plan = dict(plan)
            remaining_plan["residentOperations"] = [
                operation
                for operation in plan["residentOperations"]
                if str(operation.get("batchOperationId", "")) not in completed_resident
            ]
            remaining_plan["recoveryOrder"] = [
                item for item in plan["recoveryOrder"] if item not in completed_resident
            ]
            remaining_plan["savedAssets"] = [
                child for child in plan["savedAssets"] if str(child.get("assetPath", "")) not in completed_disk
            ]
            blocked.extend(self._preflight(remaining_plan))
            if blocked:
                payload["state"] = "blocked"
                payload["blockedReasons"] = blocked
                payload["updatedAtUtc"] = _utc_now()
                self._persist(payload)
                raise WorkflowError(
                    "batch-recovery-blocked",
                    "Recovery Commit global preflight failed before any recovery mutation.",
                    details={"recoveryId": recovery_id, "blockedReasons": blocked},
                )
            payload["state"] = "recovering"
            payload["updatedAtUtc"] = _utc_now()
            self._persist(payload)

            for operation in remaining_plan["residentOperations"]:
                try:
                    undone = self.workflow_service.undo_asset_property_live(
                        operation["assetPath"],
                        operation["transactionId"],
                        operation["editorSessionId"],
                        change_set_id=payload["changeSetId"],
                    )
                    completed_steps.append(
                        {
                            "kind": "resident-undo",
                            "assetPath": operation["assetPath"],
                            "batchOperationId": operation["batchOperationId"],
                            "transactionId": operation["transactionId"],
                            "result": undone,
                        }
                    )
                    payload["completedSteps"] = list(completed_steps)
                    payload["pendingSteps"] = [item for item in payload["recoveryOrder"] if item not in {step.get("batchOperationId", "") for step in completed_steps}]
                    payload["updatedAtUtc"] = _utc_now()
                    self._persist(payload)
                except Exception as exc:
                    payload["state"] = "partially_recovered" if completed_steps else "blocked"
                    payload["failedStep"] = operation["batchOperationId"]
                    payload["failureBoundary"] = {"phase": "resident-undo", **_safe_error(exc)}
                    payload["updatedAtUtc"] = _utc_now()
                    self._persist(payload)
                    raise WorkflowError(
                        "batch-recovery-resident-undo-failed",
                        "A resident Undo failed during Batch Recovery.",
                        details={
                            "recoveryId": recovery_id,
                            "failedStep": operation["batchOperationId"],
                            "completedSteps": payload["completedSteps"],
                            "pendingSteps": payload["pendingSteps"],
                            "failureBoundary": payload["failureBoundary"],
                        },
                    ) from exc

            # UPackageTools::UnloadPackages resets the global Editor transaction buffer by default.
            # Recover resident unsaved transactions first so package unload cannot invalidate
            # unrelated transaction identities that still require exact Undo.
            for child in reversed(remaining_plan["savedAssets"]):
                asset_path = child["assetPath"]
                try:
                    rollback_preparation = self.workflow_service.prepare_asset_for_disk_rollback(asset_path)
                    dry = self.workflow_service.rollback_authorized_live_save(
                        child["saveReceipt"],
                        mode="DryRun",
                        change_set_id=payload["changeSetId"],
                        live_apply_receipt="",
                    )
                    committed = self.workflow_service.rollback_authorized_live_save(
                        child["saveReceipt"],
                        mode="Commit",
                        rollback_dry_run_receipt=dry["rollbackDryRunReceipt"],
                        confirmation=f"ROLLBACK LIVE SAVE {child['saveReceipt']}",
                        change_set_id=payload["changeSetId"],
                    )
                    completed_steps.append(
                        {
                            "kind": "disk-rollback",
                            "assetPath": asset_path,
                            "restoredRevision": committed["restoredRevision"],
                            "saveReceipt": child["saveReceipt"],
                            "rollbackPreparation": rollback_preparation,
                        }
                    )
                    payload["completedSteps"] = list(completed_steps)
                    payload["pendingSteps"] = [item for item in payload["recoveryOrder"] if item not in {step["assetPath"] for step in completed_steps}]
                    payload["updatedAtUtc"] = _utc_now()
                    self._persist(payload)
                except Exception as exc:
                    payload["state"] = "partially_recovered" if completed_steps else "failed"
                    payload["failedStep"] = asset_path
                    payload["failureBoundary"] = {"phase": "disk-rollback", **_safe_error(exc)}
                    payload["updatedAtUtc"] = _utc_now()
                    self._persist(payload)
                    raise WorkflowError(
                        "batch-recovery-disk-rollback-failed",
                        "A persisted asset disk rollback failed during Batch Recovery.",
                        details={
                            "recoveryId": recovery_id,
                            "failedAsset": asset_path,
                            "completedSteps": payload["completedSteps"],
                            "pendingSteps": payload["pendingSteps"],
                            "failureBoundary": payload["failureBoundary"],
                        },
                    ) from exc

            payload["state"] = "recovered"
            payload["completedSteps"] = completed_steps
            payload["pendingSteps"] = []
            payload["failedStep"] = ""
            payload["failureBoundary"] = {}
            payload["blockedReasons"] = []
            payload["completedAtUtc"] = _utc_now()
            payload["updatedAtUtc"] = _utc_now()
            self._persist(payload)
            return self._commit_response(payload)

    def _commit_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "schemaVersion": RECOVERY_SCHEMA_VERSION,
            "tool": "ue_recover_live_write_batch",
            "ok": True,
            "recoveryId": payload["recoveryId"],
            "batchExecutionId": payload["batchExecutionId"],
            "checkpointSetId": payload["checkpointSetId"],
            "state": payload["state"],
            "sourceState": payload["sourceState"],
            "recoveredSavedAssets": [
                step for step in payload["completedSteps"] if step.get("kind") == "disk-rollback"
            ],
            "recoveredResidentOperations": [
                step for step in payload["completedSteps"] if step.get("kind") == "resident-undo"
            ],
            "restoredRevisions": [
                step.get("restoredRevision", "") for step in payload["completedSteps"] if step.get("kind") == "disk-rollback"
            ],
            "pendingSteps": payload["pendingSteps"],
            "failedStep": payload["failedStep"],
            "failureBoundary": payload["failureBoundary"],
            "fullyRecovered": payload["state"] == "recovered",
        }

    def get(self, *, recovery_id: str) -> dict[str, Any]:
        with self._lock:
            payload = self._load(recovery_id).payload
            if payload["state"] == "recovery_prepared":
                return self._preview_response(payload)
            return self._commit_response(payload)

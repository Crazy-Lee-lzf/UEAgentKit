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
                child["state"] = "saved"
                child["saveReceipt"] = str(committed.get("saveReceipt") or child["saveReceipt"])
                child["afterRevision"] = str(committed.get("afterRevision") or "")
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

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ue_agent_kit.agent_workflow import WorkflowError
from ue_agent_kit.checkpoint_sets import CheckpointSetService

BP_ASSET = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint"
DA_ASSET = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset"


def make_execution_payload(
    *,
    batch_execution_id: str = "lwbe_test",
    state: str = "applied",
    change_set_id: str = "cs_test",
    asset_order: list[str] | None = None,
) -> dict[str, Any]:
    asset_order = asset_order or [BP_ASSET, DA_ASSET]
    operations: list[dict[str, Any]] = []
    for asset_index, asset_path in enumerate(asset_order):
        operations.append(
            {
                "sequenceIndex": len(operations),
                "assetIndex": asset_index,
                "assetPath": asset_path,
                "batchOperationId": f"bop_{len(operations) + 1:04d}",
                "childPlanId": f"plan_{len(operations) + 1}",
                "operation": "setAssetProperty",
                "stableTargetKey": f"asset:{asset_path}",
                "state": "verified",
                "liveApplyReceipt": f"live_{len(operations) + 1}",
                "transactionId": f"tx_{len(operations) + 1}",
                "previousTransactionId": "",
                "fastVerifyResult": {"ok": True},
                "failure": {},
            }
        )
    return {
        "schemaVersion": "1.0",
        "batchExecutionId": batch_execution_id,
        "batchPlanId": "lwbp_test",
        "batchPlanDigest": "sha256:plan",
        "changeSetId": change_set_id,
        "state": state,
        "assetPath": asset_order[0],
        "assetOrder": asset_order,
        "assets": [
            {"assetPath": asset, "state": "verified", "appliedCount": 1}
            for asset in asset_order
        ],
        "appliedCount": len(operations),
        "operations": operations,
        "lastSuccessfulOperation": operations[-1]["batchOperationId"] if operations else "",
        "failedOperation": "",
        "notStarted": [],
        "recoveryOrder": [],
    }


class _FakeBoundedBatch:
    def __init__(self, execution_payload: dict[str, Any]) -> None:
        self.execution_payload = execution_payload

    def get_batch_execution(self, batch_execution_id: str) -> Any:
        if batch_execution_id != self.execution_payload["batchExecutionId"]:
            raise WorkflowError("live-write-batch-execution-not-found", "not found")
        return SimpleNamespace(payload=self.execution_payload, digest="sha256:exec")


class _FakeWorkflow:
    def __init__(self, work_root: Path) -> None:
        self.config = SimpleNamespace(
            work_root=work_root,
            backup_root=work_root / "backups",
            project_path=work_root / "project",
            commit_enabled=True,
        )
        self.preview_calls: list[dict[str, Any]] = []
        self.commit_calls: list[dict[str, Any]] = []
        self.preflight_calls: list[dict[str, Any]] = []
        self.checkpoints: dict[str, dict[str, Any]] = {}
        self.save_receipt_counter = 0
        self.fail_preview_asset = ""
        self.fail_preflight_asset = ""
        self.fail_commit_asset = ""

    def save_authorized_asset(
        self,
        asset_path: str,
        *,
        mode: str,
        save_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
        verification_mode: str = "immediate",
    ) -> dict[str, Any]:
        if mode == "Preview":
            self.preview_calls.append(
                {
                    "asset_path": asset_path,
                    "mode": mode,
                    "change_set_id": change_set_id,
                    "verification_mode": verification_mode,
                }
            )
            if asset_path == self.fail_preview_asset:
                raise WorkflowError("injected-preview-failure", "Preview failed.")
            self.save_receipt_counter += 1
            receipt = f"save_{self.save_receipt_counter}"
            checkpoint_id = f"cp_{self.save_receipt_counter}"
            self.checkpoints[checkpoint_id] = {
                "asset_path": asset_path,
                "state": "prepared",
                "save_receipt": receipt,
            }
            return {
                "checkpointId": checkpoint_id,
                "saveReceipt": receipt,
                "expectedDiskRevision": f"sha256:before:{asset_path}",
            }
        if mode == "Commit":
            self.commit_calls.append(
                {
                    "asset_path": asset_path,
                    "mode": mode,
                    "save_receipt": save_receipt,
                    "confirmation": confirmation,
                    "change_set_id": change_set_id,
                    "verification_mode": verification_mode,
                }
            )
            if asset_path == self.fail_commit_asset:
                raise WorkflowError("injected-commit-failure", "Commit failed.")
            checkpoint_id = ""
            for cid, record in self.checkpoints.items():
                if record["save_receipt"] == save_receipt:
                    checkpoint_id = cid
                    record["state"] = "saved"
                    break
            if not checkpoint_id:
                raise WorkflowError("save-receipt-invalid", "receipt invalid")
            return {
                "checkpointId": checkpoint_id,
                "saveReceipt": save_receipt,
                "afterRevision": f"sha256:after:{asset_path}",
            }
        raise WorkflowError("invalid-mode", "mode invalid")

    def preflight_checkpoint_commit(
        self,
        asset_path: str,
        save_receipt: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        self.preflight_calls.append(
            {"asset_path": asset_path, "save_receipt": save_receipt, "change_set_id": change_set_id}
        )
        if asset_path == self.fail_preflight_asset:
            raise WorkflowError("injected-preflight-failure", "Preflight failed.")
        checkpoint_id = next(
            (cid for cid, record in self.checkpoints.items() if record["save_receipt"] == save_receipt),
            "",
        )
        if not checkpoint_id:
            raise WorkflowError("save-receipt-invalid", "receipt invalid")
        return {"ok": True, "checkpointId": checkpoint_id, "assetPath": asset_path}


class CheckpointSetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_checkpoint_sets_")
        self.root = Path(self.temporary.name)
        self.work_root = self.root / "Output" / "McpWorkflow"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.workflow = _FakeWorkflow(self.work_root)
        self.bounded = _FakeBoundedBatch(make_execution_payload())
        self.service = CheckpointSetService(self.workflow, self.bounded)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _preview(self) -> dict[str, Any]:
        return self.service.preview(batch_execution_id="lwbe_test")

    def _commit(self, preview: dict[str, Any], confirmation: str = "") -> dict[str, Any]:
        return self.service.commit(
            checkpoint_set_id=preview["checkpointSetId"],
            confirmation=confirmation or preview["confirmationRequired"],
        )

    def test_preview_creates_two_w3_prepared_checkpoints_and_zero_save(self) -> None:
        result = self._preview()
        self.assertEqual(result["state"], "checkpoint_prepared")
        self.assertEqual(result["assetCount"], 2)
        self.assertEqual(len(result["assets"]), 2)
        self.assertEqual(result["savePerformed"], False)
        self.assertEqual(len(self.workflow.preview_calls), 2)
        self.assertEqual(self.workflow.commit_calls, [])
        self.assertEqual(
            [call["asset_path"] for call in self.workflow.preview_calls],
            [BP_ASSET, DA_ASSET],
        )
        self.assertTrue(all(call["verification_mode"] == "checkpoint" for call in self.workflow.preview_calls))

    def test_commit_revalidates_all_assets_before_first_save_and_saves_in_order(self) -> None:
        preview = self._preview()
        result = self._commit(preview)
        self.assertEqual(result["state"], "saved")
        self.assertEqual(result["savedCount"], 2)
        self.assertEqual(
            [call["asset_path"] for call in self.workflow.preflight_calls],
            [BP_ASSET, DA_ASSET],
        )
        self.assertEqual(
            [call["asset_path"] for call in self.workflow.commit_calls],
            [BP_ASSET, DA_ASSET],
        )

    def test_partially_applied_execution_rejected(self) -> None:
        self.bounded.execution_payload["state"] = "partially_applied"
        with self.assertRaises(WorkflowError) as caught:
            self.service.preview(batch_execution_id="lwbe_test")
        self.assertEqual(caught.exception.code, "checkpoint-set-execution-not-applied")
        self.assertEqual(self.workflow.preview_calls, [])

    def test_bad_confirmation_zero_save(self) -> None:
        preview = self._preview()
        with self.assertRaises(WorkflowError) as caught:
            self.service.commit(checkpoint_set_id=preview["checkpointSetId"], confirmation="wrong")
        self.assertEqual(caught.exception.code, "checkpoint-set-confirmation-required")
        self.assertEqual(self.workflow.commit_calls, [])
        self.assertEqual(self.workflow.preflight_calls, [])

    def test_tampered_checkpoint_set_zero_save(self) -> None:
        preview = self._preview()
        path = self.work_root / "checkpoint-sets" / preview["checkpointSetId"] / "checkpoint-set.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["state"] = "tampered"
        path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(WorkflowError) as caught:
            self._commit(preview)
        self.assertEqual(caught.exception.code, "checkpoint-set-tampered")
        self.assertEqual(self.workflow.commit_calls, [])

    def test_commit_preflight_failure_on_asset_two_zero_save(self) -> None:
        self.workflow.fail_preflight_asset = DA_ASSET
        preview = self._preview()
        with self.assertRaises(WorkflowError) as caught:
            self._commit(preview)
        self.assertEqual(caught.exception.code, "checkpoint-set-commit-preflight-failed")
        self.assertEqual(self.workflow.commit_calls, [])
        path = self.work_root / "checkpoint-sets" / preview["checkpointSetId"] / "checkpoint-set.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["savedCount"], 0)
        self.assertEqual(payload["failedAsset"], DA_ASSET)

    def test_replay_saved_is_idempotent_no_duplicate_save(self) -> None:
        preview = self._preview()
        first = self._commit(preview)
        self.assertEqual(first["state"], "saved")
        before = len(self.workflow.commit_calls)
        second = self._commit(preview)
        self.assertEqual(second["state"], "saved")
        self.assertEqual(len(self.workflow.commit_calls), before)

    def test_first_save_failure_failed_zero_saved(self) -> None:
        self.workflow.fail_commit_asset = BP_ASSET
        preview = self._preview()
        with self.assertRaises(WorkflowError) as caught:
            self._commit(preview)
        self.assertEqual(caught.exception.code, "checkpoint-set-save-failed")
        details = caught.exception.details
        self.assertEqual(details["persistedAssets"], [])
        self.assertEqual(details["failedAsset"], BP_ASSET)
        path = self.work_root / "checkpoint-sets" / preview["checkpointSetId"] / "checkpoint-set.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["savedCount"], 0)

    def test_second_save_failure_partially_saved(self) -> None:
        self.workflow.fail_commit_asset = DA_ASSET
        preview = self._preview()
        with self.assertRaises(WorkflowError) as caught:
            self._commit(preview)
        self.assertEqual(caught.exception.code, "checkpoint-set-save-failed")
        details = caught.exception.details
        self.assertEqual(details["persistedAssets"], [BP_ASSET])
        self.assertEqual(details["failedAsset"], DA_ASSET)
        self.assertEqual(details["pendingAssets"], [DA_ASSET])
        path = self.work_root / "checkpoint-sets" / preview["checkpointSetId"] / "checkpoint-set.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["state"], "partially_saved")
        self.assertEqual(payload["savedCount"], 1)
        self.assertEqual(payload["persistedAssets"], [BP_ASSET])
        self.assertEqual(payload["failedAsset"], DA_ASSET)
        self.assertEqual(payload["pendingAssets"], [DA_ASSET])

    def test_private_fault_seam_produces_partial_saved_after_real_first_save(self) -> None:
        preview = self._preview()
        self.service._fault_after_saved_asset = BP_ASSET
        with self.assertRaises(WorkflowError) as caught:
            self._commit(preview)
        self.assertEqual(caught.exception.code, "checkpoint-set-save-failed")
        details = caught.exception.details
        self.assertEqual(details["persistedAssets"], [BP_ASSET])
        self.assertEqual(details["failedAsset"], DA_ASSET)
        path = self.work_root / "checkpoint-sets" / preview["checkpointSetId"] / "checkpoint-set.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["state"], "partially_saved")
        self.assertEqual(payload["savedCount"], 1)

    def test_uncertain_persistence_boundary_never_claims_saved(self) -> None:
        from unittest.mock import patch

        preview = self._preview()
        original_persist = self.service._persist
        persist_count = 0

        def failing_persist(payload: dict[str, Any]) -> Any:
            nonlocal persist_count
            persist_count += 1
            if persist_count == 2:
                raise RuntimeError("persist failed")
            return original_persist(payload)

        with patch.object(self.service, "_persist", side_effect=failing_persist):
            with self.assertRaises(RuntimeError):
                self._commit(preview)
        path = self.work_root / "checkpoint-sets" / preview["checkpointSetId"] / "checkpoint-set.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["state"], "saving")
        self.assertEqual(payload["savedCount"], 0)

    def test_get_reloads_after_restart(self) -> None:
        preview = self._preview()
        self._commit(preview)
        new_service = CheckpointSetService(_FakeWorkflow(self.work_root), _FakeBoundedBatch(make_execution_payload()))
        loaded = new_service.get(checkpoint_set_id=preview["checkpointSetId"])
        self.assertEqual(loaded["state"], "saved")
        self.assertEqual(loaded["savedCount"], 2)
        self.assertEqual(
            [asset["assetPath"] for asset in loaded["assets"]],
            [BP_ASSET, DA_ASSET],
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
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


class _FakeLiveEditor:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fail_asset = ""

    def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"tool": tool_name, "params": params})
        asset_path = str(params.get("assetPath", ""))
        if asset_path == self.fail_asset:
            return {"ok": False, "tool": tool_name, "error": {"code": "injected-validation-failure", "message": "failed"}}
        return {
            "ok": True,
            "tool": tool_name,
            "result": {
                "assetPath": asset_path,
                "validationEvidence": {"evidenceId": f"ev_{tool_name}_{asset_path}"},
                "evidenceId": f"ev_{tool_name}_{asset_path}",
            },
        }


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
        self.verify_calls: list[dict[str, Any]] = []
        self.fail_verify_asset = ""
        self.semantic_diff_incomplete = False
        self.trust_state = "verified"
        self.live_editor_service = _FakeLiveEditor()
        self.verification_evidence_store = None
        self.plan_assertions: list[dict[str, Any]] | None = None
        self.capture_batches: list[list[dict[str, Any]]] = []
        self.capture_suppression_depth = 0
        self.unsuppressed_child_capture_attempts = 0

    @contextmanager
    def suppress_memory_l0_capture(self) -> Iterator[None]:
        self.capture_suppression_depth += 1
        try:
            yield
        finally:
            self.capture_suppression_depth -= 1

    @staticmethod
    def memory_l0_outcome(state: str) -> str:
        if "partial" in state:
            return "partial"
        return "failed" if state == "failed" else "success"

    def memory_l0_change_set_artifact(
        self,
        change_set_id: str,
    ) -> dict[str, Any]:
        return {
            "artifact_path": self.config.work_root
            / "change-sets"
            / f"{change_set_id}.json",
            "event_kind": "change_set",
            "lifecycle_state": "saved",
            "outcome": "success",
            "change_set_id": change_set_id,
        }

    def capture_memory_l0_artifacts(
        self,
        artifacts: list[dict[str, Any]],
        *,
        response: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.capture_batches.append(artifacts)
        result = {
            "enabled": True,
            "capturedCount": len(artifacts),
            "existingCount": 0,
            "failedCount": 0,
            "eventIds": [f"event_{len(self.capture_batches)}"],
        }
        if response is not None:
            response["memoryCapture"] = result
        return result

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
            if self.capture_suppression_depth == 0:
                self.unsuppressed_child_capture_attempts += 1
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
                "effectiveReceipts": [f"live_{asset_path}"],
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

    def create_authorized_save_rollback_manifest(self, save_receipt: str, live_apply_receipt: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "saveReceipt": save_receipt,
            "liveApplyReceipt": live_apply_receipt,
            "assetPath": "asset",
            "beforeRevision": "sha256:before",
            "afterRevision": "sha256:after",
            "rollbackManifestId": f"rm_{save_receipt}",
            "rollbackAvailable": True,
        }

    def verify_live_write_checkpoint(
        self,
        checkpoint_id: str,
        change_set_id: str = "",
        asset_path: str = "",
    ) -> dict[str, Any]:
        if self.capture_suppression_depth == 0:
            self.unsuppressed_child_capture_attempts += 1
        self.verify_calls.append(
            {"checkpointId": checkpoint_id, "changeSetId": change_set_id, "assetPath": asset_path}
        )
        if asset_path == self.fail_verify_asset:
            raise WorkflowError("checkpoint-canonical-mismatch", "Canonical mismatch.")
        return {
            "ok": True,
            "verified": True,
            "checkpointId": checkpoint_id,
            "changeSetId": change_set_id,
            "assetPath": asset_path,
            "verificationKind": "independent-verified",
            "artifactRevision": f"sha256:artifact:{asset_path}",
            "reportId": f"report_{checkpoint_id}",
            "childUnrealProcessCount": 1,
        }

    def analyze_semantic_diff(
        self,
        change_set_id: str,
        *,
        stage: str = "auto",
        **_: Any,
    ) -> dict[str, Any]:
        incomplete = self.semantic_diff_incomplete
        return {
            "ok": True,
            "evidenceStage": {"requested": stage, "selected": "verified" if not incomplete else "persisted"},
            "summary": {
                "totalAssetCount": 2,
                "returnedAssetCount": 2 if not incomplete else 1,
                "missingExpectedCount": 1 if incomplete else 0,
                "unexpectedCount": 0,
                "analysisGapCount": 1 if incomplete else 0,
            },
        }

    def build_verification_plan(self, change_set_id: str, **_: Any) -> dict[str, Any]:
        assertions = self.plan_assertions
        if assertions is None:
            assertions = [
                {
                    "requirement": "required",
                    "kind": "compile",
                    "subject": BP_ASSET,
                    "nextAction": {"tool": "ue_compile_blueprint", "arguments": {"asset_path": BP_ASSET}},
                },
                {
                    "requirement": "required",
                    "kind": "data-validation",
                    "subject": BP_ASSET,
                    "nextAction": {"tool": "ue_validate_asset", "arguments": {"asset_path": BP_ASSET}},
                },
                {
                    "requirement": "required",
                    "kind": "data-validation",
                    "subject": DA_ASSET,
                    "nextAction": {"tool": "ue_validate_asset", "arguments": {"asset_path": DA_ASSET}},
                },
            ]
        return {
            "ok": True,
            "planId": "verification_plan_test",
            "planFingerprint": "sha256:plan",
            "assertions": assertions,
            "summary": {"required": sum(a["requirement"] == "required" for a in assertions)},
        }

    def evaluate_trust_verdict(self, change_set_id: str, **_: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "verificationScope": {
                "verifiedAssets": [BP_ASSET, DA_ASSET] if self.trust_state == "verified" else [],
                "affectedAssets": [BP_ASSET, DA_ASSET],
            },
            "verdict": {
                "state": self.trust_state,
                "reasonCodes": [] if self.trust_state == "verified" else ["trust-required-evidence-missing"],
                "statement": "verified" if self.trust_state == "verified" else "not verified",
            },
            "summary": {
                "unresolvedRiskCount": 0,
                "analysisGapCount": 0,
                "unexpectedChangeCount": 0,
            },
        }


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
        self.assertEqual(self.workflow.unsuppressed_child_capture_attempts, 0)
        self.assertEqual(
            [item["event_kind"] for item in self.workflow.capture_batches[-1]],
            ["checkpoint_set", "change_set"],
        )
        self.assertEqual(result["memoryCapture"]["capturedCount"], 2)

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


    def test_verify_fully_saved_set_uses_children_semantic_plan_trust(self) -> None:
        preview = self._preview()
        self._commit(preview)
        result = self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertEqual(result["state"], "verified")
        self.assertEqual(result["verifiedCount"], 2)
        self.assertEqual(result["strongVerifyProcessCount"], 2)
        self.assertEqual(
            [call["assetPath"] for call in self.workflow.verify_calls],
            [BP_ASSET, DA_ASSET],
        )
        self.assertEqual(
            [action["tool"] for action in result["validationActions"]],
            ["ue_compile_blueprint", "ue_validate_asset", "ue_validate_asset"],
        )
        self.assertEqual(result["trust"]["state"], "verified")
        self.assertEqual(self.workflow.unsuppressed_child_capture_attempts, 0)
        self.assertEqual(
            [item["event_kind"] for item in self.workflow.capture_batches[-1]],
            ["checkpoint_set", "semantic_diff", "trust", "change_set"],
        )
        self.assertEqual(result["memoryCapture"]["capturedCount"], 4)

    def test_verify_replay_is_idempotent_no_new_strong_process(self) -> None:
        preview = self._preview()
        self._commit(preview)
        self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        before = len(self.workflow.verify_calls)
        second = self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertEqual(second["state"], "verified")
        self.assertEqual(second["strongVerifyProcessCount"], 0)
        self.assertEqual(len(self.workflow.verify_calls), before)

    def test_verify_partially_saved_checkpoint_set_rejected(self) -> None:
        self.workflow.fail_commit_asset = DA_ASSET
        preview = self._preview()
        with self.assertRaises(WorkflowError):
            self._commit(preview)
        with self.assertRaises(WorkflowError) as verify_caught:
            self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertEqual(verify_caught.exception.code, "checkpoint-set-verify-not-saved")
        self.assertEqual(self.workflow.verify_calls, [])

    def test_verify_child_two_failure_produces_partial_verified(self) -> None:
        self.workflow.fail_verify_asset = DA_ASSET
        preview = self._preview()
        self._commit(preview)
        result = self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertEqual(result["state"], "partially_verified")
        self.assertEqual(result["verifiedCount"], 1)
        bp = next(child for child in result["children"] if child["assetPath"] == BP_ASSET)
        da = next(child for child in result["children"] if child["assetPath"] == DA_ASSET)
        self.assertTrue(bp["verified"])
        self.assertFalse(da["verified"])
        self.assertEqual(da["failure"]["code"], "checkpoint-canonical-mismatch")

    def test_verify_incomplete_semantic_diff_blocks_verified(self) -> None:
        self.workflow.semantic_diff_incomplete = True
        preview = self._preview()
        self._commit(preview)
        result = self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertNotEqual(result["state"], "verified")
        self.assertFalse(result["semanticDiff"]["verified"])

    def test_verify_trust_not_verified_blocks_verified(self) -> None:
        self.workflow.trust_state = "insufficient-evidence"
        preview = self._preview()
        self._commit(preview)
        result = self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertNotEqual(result["state"], "verified")
        self.assertEqual(result["trust"]["state"], "insufficient-evidence")

    def test_verify_private_fault_seam_makes_child_two_mismatch(self) -> None:
        preview = self._preview()
        self._commit(preview)
        self.service._fault_verify_asset = DA_ASSET
        result = self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertEqual(result["state"], "partially_verified")
        da = next(child for child in result["children"] if child["assetPath"] == DA_ASSET)
        self.assertEqual(da["failure"]["code"], "checkpoint-canonical-mismatch")

    def test_verify_unsupported_required_action_not_executed(self) -> None:
        self.workflow.plan_assertions = [
            {
                "requirement": "required",
                "kind": "automation",
                "subject": "Test.Something",
                "nextAction": {"tool": "ue_run_automation_test", "arguments": {"test_name": "Test.Something"}},
            }
        ]
        preview = self._preview()
        self._commit(preview)
        result = self.service.verify(checkpoint_set_id=preview["checkpointSetId"])
        self.assertEqual(result["validationActions"], [])
        self.assertNotEqual(result["state"], "verified")


if __name__ == "__main__":
    unittest.main()

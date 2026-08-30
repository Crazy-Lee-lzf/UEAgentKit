from __future__ import annotations

import tempfile
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ue_agent_kit.agent_workflow import WorkflowError
from ue_agent_kit.batch_recovery import BatchRecoveryService

BP = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint"
DA = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset"


def make_execution(
    *,
    state: str = "applied",
    ops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if ops is None:
        ops = [
            {"sequenceIndex": 0, "batchOperationId": "bop_0001", "assetIndex": 0, "assetPath": BP, "state": "verified", "transactionId": "tx_1", "liveApplyReceipt": "live_1", "editorSessionId": "session-1"},
            {"sequenceIndex": 1, "batchOperationId": "bop_0002", "assetIndex": 0, "assetPath": BP, "state": "verified", "transactionId": "tx_2", "liveApplyReceipt": "live_2", "editorSessionId": "session-1"},
            {"sequenceIndex": 2, "batchOperationId": "bop_0003", "assetIndex": 0, "assetPath": BP, "state": "verified", "transactionId": "tx_3", "liveApplyReceipt": "live_3", "editorSessionId": "session-1"},
            {"sequenceIndex": 3, "batchOperationId": "bop_0004", "assetIndex": 1, "assetPath": DA, "state": "verified", "transactionId": "tx_4", "liveApplyReceipt": "live_4", "editorSessionId": "session-1"},
        ]
    return {
        "schemaVersion": "1.0",
        "batchExecutionId": "lwbe_test",
        "batchPlanId": "lwbp_test",
        "batchPlanDigest": "sha256:plan",
        "changeSetId": "cs_test",
        "state": state,
        "assetPath": BP,
        "assetOrder": [BP, DA],
        "operations": ops,
        "recoveryOrder": ["bop_0004", "bop_0003", "bop_0002", "bop_0001"],
        "lastSuccessfulOperation": "bop_0004",
    }


class _FakeBounded:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def get_batch_execution(self, batch_execution_id: str) -> Any:
        return SimpleNamespace(payload=self.payload, digest="sha256:exec")


class _FakeCheckpointSets:
    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload

    def load_payload(self, checkpoint_set_id: str) -> dict[str, Any]:
        if self.payload is None or self.payload.get("checkpointSetId") != checkpoint_set_id:
            raise WorkflowError("checkpoint-set-not-found", "not found")
        return self.payload


class _FakeWorkflow:
    def __init__(self, work_root: Path, *, session: str = "session-1") -> None:
        self.config = SimpleNamespace(
            work_root=work_root,
            backup_root=work_root / "backups",
            project_path=work_root / "project",
            commit_enabled=True,
        )
        self.live_editor_service = SimpleNamespace(status=lambda: {"sessionId": session})
        self.undo_calls: list[tuple[str, str, str, str]] = []
        self.prepare_calls: list[str] = []
        self.rollback_dry_calls: list[str] = []
        self.rollback_commit_calls: list[str] = []
        self.events: list[tuple[str, str]] = []
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
        if state == "recovered":
            return "recovered"
        return "failed" if state in {"failed", "blocked"} else "success"

    @staticmethod
    def memory_l0_change_set_artifact(
        change_set_id: str,
    ) -> dict[str, Any] | None:
        return None

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

    def prepare_asset_for_disk_rollback(self, asset_path: str) -> dict[str, Any]:
        self.prepare_calls.append(asset_path)
        self.events.append(("prepare", asset_path))
        return {"state": "prepared", "assetPath": asset_path, "prepared": True}

    def undo_asset_property_live(
        self,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        if self.capture_suppression_depth == 0:
            self.unsuppressed_child_capture_attempts += 1
        self.undo_calls.append((asset_path, transaction_id, editor_session_id, change_set_id))
        self.events.append(("undo", asset_path))
        return {"ok": True, "assetPath": asset_path, "transactionId": transaction_id}

    def rollback_authorized_live_save(
        self,
        save_receipt: str,
        *,
        mode: str,
        rollback_dry_run_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
        live_apply_receipt: str = "",
    ) -> dict[str, Any]:
        if mode == "DryRun":
            self.rollback_dry_calls.append(save_receipt)
            self.events.append(("rollback-dry", save_receipt))
            return {"rollbackDryRunReceipt": f"dry_{save_receipt}", "expectedBackupRevision": "sha256:before"}
        self.rollback_commit_calls.append(save_receipt)
        self.events.append(("rollback-commit", save_receipt))
        return {"restored": True, "restoredRevision": "sha256:before", "assetPath": "asset"}


class BatchRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_batch_recovery_")
        self.root = Path(self.temporary.name)
        self.work_root = self.root / "Output" / "McpWorkflow"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.workflow = _FakeWorkflow(self.work_root)
        self.bounded = _FakeBounded(make_execution())
        self.cps = _FakeCheckpointSets()
        self.service = BatchRecoveryService(self.workflow, self.bounded, self.cps)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_resident_only_preview_and_commit_undo_reverse(self) -> None:
        preview = self.service.preview(batch_execution_id="lwbe_test")
        self.assertEqual(preview["state"], "recovery_prepared")
        self.assertEqual(preview["savedAssetCount"], 0)
        self.assertEqual(preview["residentOperationCount"], 4)
        result = self.service.commit(
            recovery_id=preview["recoveryId"],
            confirmation=preview["confirmationRequired"],
        )
        self.assertEqual(result["state"], "recovered")
        self.assertEqual(
            [call[0] for call in self.workflow.undo_calls],
            [DA, BP, BP, BP],
        )
        self.assertEqual(result["fullyRecovered"], True)
        self.assertEqual(self.workflow.unsuppressed_child_capture_attempts, 0)
        self.assertEqual(len(self.workflow.capture_batches), 1)
        self.assertEqual(
            self.workflow.capture_batches[0][0]["event_kind"],
            "recovery",
        )
        self.assertEqual(result["memoryCapture"]["capturedCount"], 1)

    def test_editor_session_mismatch_blocks_zero_undo(self) -> None:
        self.workflow = _FakeWorkflow(self.root, session="session-other")
        self.service = BatchRecoveryService(self.workflow, self.bounded, self.cps)
        preview = self.service.preview(batch_execution_id="lwbe_test")
        self.assertEqual(preview["state"], "blocked")
        self.assertIn("editor-session-unavailable", preview["blockedReasons"])
        with self.assertRaises(WorkflowError) as caught:
            self.service.commit(
                recovery_id=preview["recoveryId"],
                confirmation=preview["confirmationRequired"],
            )
        self.assertEqual(caught.exception.code, "batch-recovery-blocked")
        self.assertEqual(self.workflow.undo_calls, [])

    def test_recovery_record_reload_after_restart(self) -> None:
        preview = self.service.preview(batch_execution_id="lwbe_test")
        self.service.commit(
            recovery_id=preview["recoveryId"],
            confirmation=preview["confirmationRequired"],
        )
        new_service = BatchRecoveryService(
            _FakeWorkflow(self.work_root),
            self.bounded,
            self.cps,
        )
        loaded = new_service.get(recovery_id=preview["recoveryId"])
        self.assertEqual(loaded["state"], "recovered")
        self.assertEqual(loaded["fullyRecovered"], True)

    def test_partially_saved_recovery_resident_first_then_disk(self) -> None:
        cps_payload = {
            "checkpointSetId": "cps_partial",
            "batchExecutionId": "lwbe_test",
            "state": "partially_saved",
            "persistedAssets": [BP],
            "pendingAssets": [DA],
            "childCheckpoints": [
                {
                    "assetPath": BP,
                    "checkpointId": "cp_bp",
                    "saveReceipt": "save_bp",
                    "state": "saved",
                    "afterRevision": "sha256:after_bp",
                    "rollbackManifestId": "rm_bp",
                    "rollbackManifestPath": str(self.root / "backups" / "live-save" / "save_bp" / "rollback-manifest.json"),
                },
            ],
        }
        backup_dir = self.root / "backups" / "live-save" / "save_bp"
        backup_dir.mkdir(parents=True, exist_ok=True)
        (backup_dir / "rollback-manifest.json").write_text("{}", encoding="utf-8")
        cps_dir = self.work_root / "checkpoint-sets" / "cps_partial"
        cps_dir.mkdir(parents=True, exist_ok=True)
        (cps_dir / "checkpoint-set.json").write_text(__import__("json").dumps(cps_payload), encoding="utf-8")
        self.cps = _FakeCheckpointSets(cps_payload)
        self.service = BatchRecoveryService(self.workflow, self.bounded, self.cps)
        preview = self.service.preview(batch_execution_id="lwbe_test")
        self.assertEqual(preview["state"], "recovery_prepared")
        self.assertEqual(preview["savedAssetCount"], 1)
        self.assertEqual(preview["residentOperationCount"], 1)
        result = self.service.commit(
            recovery_id=preview["recoveryId"],
            confirmation=preview["confirmationRequired"],
        )
        self.assertEqual(result["state"], "recovered")
        self.assertEqual(self.workflow.prepare_calls, [BP])
        self.assertEqual(self.workflow.rollback_commit_calls, ["save_bp"])
        self.assertEqual([call[0] for call in self.workflow.undo_calls], [DA])
        self.assertEqual(
            self.workflow.events,
            [
                ("undo", DA),
                ("prepare", BP),
                ("rollback-dry", "save_bp"),
                ("rollback-commit", "save_bp"),
            ],
        )


    def test_partial_recovery_reload_resumes_without_replaying_completed_undo(self) -> None:
        preview = self.service.preview(batch_execution_id="lwbe_test")
        original_undo = self.workflow.undo_asset_property_live

        def fail_after_first(
            asset_path: str,
            transaction_id: str,
            editor_session_id: str,
            change_set_id: str = "",
        ) -> dict[str, Any]:
            if self.workflow.undo_calls:
                raise WorkflowError("controlled-stop", "controlled recovery stop")
            return original_undo(asset_path, transaction_id, editor_session_id, change_set_id)

        self.workflow.undo_asset_property_live = fail_after_first  # type: ignore[method-assign]
        with self.assertRaises(WorkflowError):
            self.service.commit(
                recovery_id=preview["recoveryId"],
                confirmation=preview["confirmationRequired"],
            )
        partial = self.service.get(recovery_id=preview["recoveryId"])
        self.assertEqual(partial["state"], "partially_recovered")
        self.assertEqual(len(partial["recoveredResidentOperations"]), 1)
        self.assertEqual(partial["recoveredResidentOperations"][0]["batchOperationId"], "bop_0004")

        resumed_workflow = _FakeWorkflow(self.work_root)
        resumed = BatchRecoveryService(resumed_workflow, self.bounded, self.cps)
        result = resumed.commit(
            recovery_id=preview["recoveryId"],
            confirmation=preview["confirmationRequired"],
        )
        self.assertEqual(result["state"], "recovered")
        self.assertEqual(result["failedStep"], "")
        self.assertEqual(result["failureBoundary"], {})
        self.assertEqual([call[0] for call in resumed_workflow.undo_calls], [BP, BP, BP])
        self.assertEqual(
            [step["batchOperationId"] for step in result["recoveredResidentOperations"]],
            ["bop_0004", "bop_0003", "bop_0002", "bop_0001"],
        )


if __name__ == "__main__":
    unittest.main()

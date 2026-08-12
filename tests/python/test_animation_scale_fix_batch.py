from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_workflow import WorkflowError  # noqa: E402
from ue_agent_kit.animation_scale_fix_batch import AnimationScaleFixBatchService  # noqa: E402

AUDIT_TASK_ID = "26083bd4-d963-4f64-93f3-0a8be296879d"
ANIMATION_A = "/Game/Animations/A_Idle.A_Idle"
ANIMATION_B = "/Game/Animations/A_Walk.A_Walk"
ANIMATION_C = "/Game/Animations/A_Run.A_Run"


def _audit_item(asset_path: str, classification: str, reference_scale: float) -> dict[str, Any]:
    return {
        "assetPath": asset_path,
        "classification": classification,
        "rootBone": "Root",
        "rootTrack": {
            "referenceComponentScale": {
                "x": reference_scale,
                "y": reference_scale,
                "z": reference_scale,
            },
        },
    }


def _write_report(work_root: Path, items: list[dict[str, Any]], *, state: str = "completed") -> str:
    report = {
        "schemaVersion": "1.0",
        "reportType": "animation-scale-audit",
        "task": {
            "taskId": AUDIT_TASK_ID,
            "state": state,
        },
        "summary": {},
        "items": items,
    }
    payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    directory = work_root / "animation-scale-audits" / AUDIT_TASK_ID
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "report.json").write_bytes(payload)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


class _FakeWorkflowService:
    def __init__(
        self,
        work_root: Path,
        *,
        fail_asset: str = "",
        fail_apply_asset: str = "",
        no_op_assets: set[str] | None = None,
        fail_undo_asset_once: str = "",
    ) -> None:
        self.config = SimpleNamespace(work_root=work_root, commit_enabled=True)
        self.live_editor_service = object()
        self.project_name = "TestProject"
        self.fail_asset = fail_asset
        self.fail_apply_asset = fail_apply_asset
        self.no_op_assets = set(no_op_assets or set())
        self.fail_undo_asset_once = fail_undo_asset_once
        self.undo_failed_once = False
        self.calls: list[dict[str, Any]] = []
        self.discarded: list[str] = []
        self.plan_assets: dict[str, str] = {}
        self.plan_values: dict[str, dict[str, Any]] = {}
        self.change_sets: list[str] = []
        self.discarded_change_sets: list[str] = []
        self.changed_change_sets: set[str] = set()
        self.apply_calls: list[dict[str, str]] = []
        self.undo_calls: list[dict[str, str]] = []
        self.save_receipt_assets: dict[str, str] = {}
        self.save_preview_calls: list[str] = []
        self.save_commit_calls: list[str] = []
        self.rollback_manifest_calls: list[str] = []
        self.verify_calls: list[str] = []
        self.index_refresh_prepare_calls: list[str] = []
        self.index_refresh_candidate_assets: dict[str, str] = {}
        self.index_refresh_discarded: list[str] = []
        self.index_refresh_apply_calls: list[list[str]] = []
        self.fail_manifest_once_asset = ""
        self.manifest_failed_once = False
        self.fail_save_asset = ""
        self.rollback_dry_run_calls: list[str] = []
        self.rollback_commit_calls: list[str] = []
        self.fail_rollback_commit_asset_once = ""
        self.rollback_commit_failed_once = False

    def prepare_high_level_change(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if kwargs["asset_path"] == self.fail_asset:
            raise WorkflowError("test-child-plan-failed", "Injected child Plan failure.")
        index = len(self.calls)
        self.plan_assets[f"plan_{index}"] = str(kwargs["asset_path"])
        self.plan_values[f"plan_{index}"] = dict(kwargs["value"])
        return {
            "planId": f"plan_{index}",
            "patchDigest": "sha256:" + str(index) * 64,
            "expectedRevision": "sha256:" + str(index + 1) * 64,
            "assetClass": "/Script/Engine.AnimSequence",
            "risk": "medium",
            "commitAllowedByPolicy": True,
        }

    def discard_unconsumed_plans(self, plan_ids: list[str]) -> None:
        self.discarded.extend(plan_ids)

    def create_change_set(self, *, title: str = "", task_id: str = "") -> dict[str, Any]:
        change_set_id = f"cs_{len(self.change_sets) + 1}"
        self.change_sets.append(change_set_id)
        return {"changeSetId": change_set_id, "title": title, "taskId": task_id}

    def discard_empty_change_set(self, change_set_id: str) -> bool:
        if change_set_id in self.changed_change_sets:
            return False
        self.discarded_change_sets.append(change_set_id)
        return True

    def apply_asset_property_live(self, plan_id: str, confirmation: str, change_set_id: str = "") -> dict[str, Any]:
        asset_path = self.plan_assets[plan_id]
        self.apply_calls.append(
            {
                "planId": plan_id,
                "assetPath": asset_path,
                "confirmation": confirmation,
                "changeSetId": change_set_id,
            }
        )
        if asset_path == self.fail_apply_asset:
            raise WorkflowError("test-child-live-apply-failed", "Injected Live Apply failure.")
        if asset_path in self.no_op_assets:
            return {
                "changed": False,
                "liveApplyReceipt": "",
                "result": {"transactionId": "", "editorSessionId": "session-1"},
            }
        self.changed_change_sets.add(change_set_id)
        expected_scale = float(self.plan_values[plan_id]["expectedFinalScale"])
        suffix = str(len(self.apply_calls))
        return {
            "changed": True,
            "liveApplyReceipt": f"live_{suffix}",
            "result": {
                "transactionId": f"tx_{suffix}",
                "editorSessionId": "session-1",
                "afterValue": {
                    "referenceLocalScale": {"x": expected_scale, "y": expected_scale, "z": expected_scale},
                    "finalEvaluationStatus": "success",
                    "finalRootScale": {"x": expected_scale, "y": expected_scale, "z": expected_scale},
                },
            },
        }

    def undo_asset_property_live(
        self,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        self.undo_calls.append(
            {
                "assetPath": asset_path,
                "transactionId": transaction_id,
                "editorSessionId": editor_session_id,
                "changeSetId": change_set_id,
            }
        )
        if asset_path == self.fail_undo_asset_once and not self.undo_failed_once:
            self.undo_failed_once = True
            raise WorkflowError("test-child-live-undo-failed", "Injected Live Undo failure.")
        return {"ok": True, "reverted": True}


    def save_authorized_asset(
        self,
        asset_path: str,
        *,
        mode: str = "Preview",
        save_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
    ) -> dict[str, Any]:
        del change_set_id
        if mode == "Preview":
            self.save_preview_calls.append(asset_path)
            receipt = f"save_{len(self.save_preview_calls)}"
            self.save_receipt_assets[receipt] = asset_path
            return {"mode": "Preview", "saveReceipt": receipt, "saved": False}
        if mode != "Commit":
            raise AssertionError(f"unexpected save mode: {mode}")
        if self.save_receipt_assets.get(save_receipt) != asset_path:
            raise AssertionError("save receipt does not belong to asset")
        if confirmation != f"SAVE {save_receipt}":
            raise AssertionError("invalid save confirmation")
        self.save_commit_calls.append(asset_path)
        if asset_path == self.fail_save_asset:
            raise WorkflowError("test-child-save-failed", "Injected child Save failure.")
        return {
            "mode": "Commit",
            "assetPath": asset_path,
            "saveReceipt": save_receipt,
            "saved": True,
            "verified": True,
            "beforeRevision": "sha256:" + "a" * 64,
            "afterRevision": "sha256:" + "b" * 64,
            "revisionChanged": True,
        }

    def create_authorized_save_rollback_manifest(
        self,
        save_receipt: str,
        live_apply_receipt: str,
    ) -> dict[str, Any]:
        del live_apply_receipt
        asset_path = self.save_receipt_assets[save_receipt]
        self.rollback_manifest_calls.append(asset_path)
        if asset_path == self.fail_manifest_once_asset and not self.manifest_failed_once:
            self.manifest_failed_once = True
            raise WorkflowError("test-rollback-manifest-failed", "Injected rollback-manifest failure.")
        return {
            "rollbackAvailable": True,
            "rollbackManifestId": f"manifest_{len(self.rollback_manifest_calls)}",
            "assetPath": asset_path,
        }

    def verify_live_write(
        self,
        asset_path: str,
        live_apply_receipt: str = "",
        change_set_id: str = "",
    ) -> dict[str, Any]:
        del change_set_id
        self.verify_calls.append(asset_path)
        return {
            "state": "verified",
            "verified": True,
            "assetPath": asset_path,
            "liveApplyReceipt": live_apply_receipt,
            "actualRevision": "sha256:" + "b" * 64,
            "reportId": f"verify_{len(self.verify_calls)}",
            "persistedExpectedValue": {"rootTrackFirstScale": {"x": 100.0, "y": 100.0, "z": 100.0}},
            "exportedPersistedValue": {"rootTrackFirstScale": {"x": 100.0, "y": 100.0, "z": 100.0}},
            "runtimeVerification": {"finalEvaluationStatus": "success"},
        }

    def rollback_authorized_live_save(
        self,
        save_receipt: str,
        *,
        mode: str = "DryRun",
        rollback_dry_run_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
        live_apply_receipt: str = "",
    ) -> dict[str, Any]:
        del change_set_id, live_apply_receipt
        asset_path = self.save_receipt_assets[save_receipt]
        if mode == "DryRun":
            self.rollback_dry_run_calls.append(asset_path)
            return {
                "mode": "DryRun",
                "assetPath": asset_path,
                "rollbackDryRunReceipt": f"dry_{len(self.rollback_dry_run_calls)}",
                "beforeRollbackRevision": "sha256:" + "b" * 64,
                "expectedRestoredRevision": "sha256:" + "a" * 64,
                "wroteDisk": False,
            }
        if mode != "Commit":
            raise AssertionError(f"unexpected rollback mode: {mode}")
        if not rollback_dry_run_receipt.startswith("dry_"):
            raise AssertionError("rollback Commit requires child DryRun receipt")
        if confirmation != f"ROLLBACK LIVE SAVE {save_receipt}":
            raise AssertionError("invalid child rollback confirmation")
        self.rollback_commit_calls.append(asset_path)
        if asset_path == self.fail_rollback_commit_asset_once and not self.rollback_commit_failed_once:
            self.rollback_commit_failed_once = True
            raise WorkflowError("test-child-rollback-failed", "Injected persisted Rollback failure.")
        return {
            "mode": "Commit",
            "assetPath": asset_path,
            "restored": True,
            "restoredRevision": "sha256:" + "a" * 64,
        }

    def prepare_batch_index_refresh_candidate(self, asset_path: str) -> dict[str, Any]:
        self.index_refresh_prepare_calls.append(asset_path)
        candidate_id = f"irc_{len(self.index_refresh_prepare_calls)}"
        self.index_refresh_candidate_assets[candidate_id] = asset_path
        return {
            "candidateId": candidate_id,
            "assetPath": asset_path,
            "revision": "sha256:" + "b" * 64,
        }

    def discard_batch_index_refresh_candidates(self, candidate_ids: list[str]) -> None:
        for candidate_id in candidate_ids:
            asset_path = self.index_refresh_candidate_assets.pop(candidate_id, "")
            if asset_path:
                self.index_refresh_discarded.append(asset_path)

    def apply_batch_index_refresh(self, candidate_ids: list[str]) -> dict[str, Any]:
        self.index_refresh_apply_calls.append(list(candidate_ids))
        assets = [self.index_refresh_candidate_assets[candidate_id] for candidate_id in candidate_ids]
        for candidate_id in candidate_ids:
            self.index_refresh_candidate_assets.pop(candidate_id, None)
        return {
            "applied": True,
            "restartRequired": True,
            "newGeneration": {
                "generationId": "gen_20260812T120000Z_123456abcdef",
                "refreshedAssetCount": len(assets),
                "targetRevisions": [
                    {"assetPath": asset_path, "revision": "sha256:" + "b" * 64}
                    for asset_path in assets
                ],
            },
        }

class AnimationScaleFixBatchTests(unittest.TestCase):
    def test_batch_plan_derives_each_expected_scale_from_audit_reference(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-lock-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            result = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B],
            )

            self.assertEqual(result["state"], "planned")
            self.assertEqual(result["assetCount"], 2)
            self.assertEqual([item["expectedFinalScale"] for item in result["items"]], [50.0, 75.0])
            self.assertEqual(result["items"][0]["value"]["rootTrackScaleMode"], "Keep")
            self.assertTrue(result["items"][0]["value"]["forceRootLock"])
            self.assertEqual(result["items"][0]["value"]["rootMotionRootLock"], "RefPose")
            self.assertEqual(result["items"][1]["value"]["rootTrackScaleMode"], "ReferenceLocal")
            self.assertNotEqual(result["items"][0]["expectedFinalScale"], 100.0)
            self.assertNotEqual(result["items"][1]["expectedFinalScale"], 100.0)
            self.assertEqual(len(workflow.calls), 2)
            self.assertEqual(workflow.discarded, [])
            plan_path = work_root / "animation-scale-fix-batches" / str(result["batchPlanId"]) / "plan.json"
            self.assertTrue(plan_path.is_file())
            self.assertEqual(
                result["batchPlanDigest"],
                "sha256:" + hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            )

            fetched = service.get(batch_plan_id=str(result["batchPlanId"]))
            self.assertEqual(fetched["batchPlanDigest"], result["batchPlanDigest"])
            self.assertEqual(fetched["items"], result["items"])

    def test_root_track_explicit_override_switches_to_uniform_strategy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_override_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_B, "root-track-candidate", 75.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            result = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_B],
                expected_final_scale_overrides={ANIMATION_B: 80.0},
                final_scale_tolerance=0.5,
            )

            item = result["items"][0]
            self.assertEqual(item["expectedFinalScale"], 80.0)
            self.assertEqual(item["expectedFinalScaleSource"], "explicit-override")
            self.assertEqual(item["strategy"], "uniform-root-track-override")
            self.assertEqual(item["value"]["rootTrackScaleMode"], "Uniform")
            self.assertEqual(item["value"]["uniformScale"], 80.0)
            self.assertEqual(item["value"]["finalScaleTolerance"], 0.5)

    def test_unsupported_selected_classification_is_rejected_without_child_plans(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_unsupported_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "normal", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            with self.assertRaisesRegex(WorkflowError, "not safe for automatic Batch Plan"):
                service.plan(
                    audit_task_id=AUDIT_TASK_ID,
                    audit_report_id=report_id,
                    asset_paths=[ANIMATION_A],
                )

            self.assertEqual(workflow.calls, [])
            self.assertEqual(workflow.discarded, [])

    def test_root_lock_override_must_match_reference_scale(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_root_lock_override_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            with self.assertRaisesRegex(WorkflowError, "must match the Root reference scale"):
                service.plan(
                    audit_task_id=AUDIT_TASK_ID,
                    audit_report_id=report_id,
                    asset_paths=[ANIMATION_A],
                    expected_final_scale_overrides={ANIMATION_A: 60.0},
                )

            self.assertEqual(workflow.calls, [])

    def test_duplicate_selection_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_duplicate_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            with self.assertRaisesRegex(WorkflowError, "duplicate"):
                service.plan(
                    audit_task_id=AUDIT_TASK_ID,
                    audit_report_id=report_id,
                    asset_paths=[ANIMATION_A, ANIMATION_A],
                )

            self.assertEqual(workflow.calls, [])

    def test_child_plan_failure_cleans_previously_created_children(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_cleanup_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-lock-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root, fail_asset=ANIMATION_B)
            service = AnimationScaleFixBatchService(workflow)

            with self.assertRaisesRegex(WorkflowError, "Injected child Plan failure"):
                service.plan(
                    audit_task_id=AUDIT_TASK_ID,
                    audit_report_id=report_id,
                    asset_paths=[ANIMATION_A, ANIMATION_B],
                )

            self.assertEqual(len(workflow.calls), 2)
            self.assertEqual(workflow.discarded, ["plan_1"])
            self.assertFalse((work_root / "animation-scale-fix-batches").exists())

    def test_partial_audit_report_is_rejected_before_child_plans(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_partial_report_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)],
                state="cancelled",
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            with self.assertRaisesRegex(WorkflowError, "requires a completed Audit Report"):
                service.plan(
                    audit_task_id=AUDIT_TASK_ID,
                    audit_report_id=report_id,
                    asset_paths=[ANIMATION_A],
                )
            self.assertEqual(workflow.calls, [])

    def test_nonuniform_reference_scale_is_rejected_before_child_plans(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_nonuniform_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            item = _audit_item(ANIMATION_A, "root-lock-candidate", 50.0)
            item["rootTrack"]["referenceComponentScale"]["y"] = 60.0
            report_id = _write_report(work_root, [item])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            with self.assertRaisesRegex(WorkflowError, "non-uniform"):
                service.plan(
                    audit_task_id=AUDIT_TASK_ID,
                    audit_report_id=report_id,
                    asset_paths=[ANIMATION_A],
                )
            self.assertEqual(workflow.calls, [])

    def test_get_rejects_tampered_batch_plan_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_tamper_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            result = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A],
            )
            plan_path = work_root / "animation-scale-fix-batches" / str(result["batchPlanId"]) / "plan.json"
            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            payload["description"] = "tampered"
            plan_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(WorkflowError, "changed after it was created"):
                service.get(batch_plan_id=str(result["batchPlanId"]))

    def test_report_digest_mismatch_is_rejected_before_child_plans(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_report_digest_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)

            with self.assertRaisesRegex(WorkflowError, "does not match"):
                service.plan(
                    audit_task_id=AUDIT_TASK_ID,
                    audit_report_id="sha256:" + "0" * 64,
                    asset_paths=[ANIMATION_A],
                )

            self.assertEqual(workflow.calls, [])


    def test_live_apply_is_receipt_continued_and_get_does_not_advance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_live_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-lock-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B],
            )
            batch_plan_id = str(plan["batchPlanId"])

            first = service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=1,
            )
            execution = first["execution"]
            receipt = str(execution["batchApplyReceipt"])
            self.assertEqual(execution["state"], "applying")
            self.assertEqual(execution["progress"]["processedAssets"], 1)
            self.assertEqual(execution["progress"]["appliedAssets"], 1)
            self.assertEqual(execution["items"][0]["runtimeVerification"]["finalRootScale"]["x"], 50.0)
            self.assertEqual(len(workflow.apply_calls), 1)

            polled = service.get(batch_plan_id=batch_plan_id)
            self.assertEqual(polled["execution"]["state"], "applying")
            self.assertEqual(len(workflow.apply_calls), 1)

            second = service.apply_live(
                batch_plan_id=batch_plan_id,
                batch_apply_receipt=receipt,
                max_assets=1,
            )
            self.assertEqual(second["execution"]["state"], "applied")
            self.assertEqual(second["execution"]["progress"]["appliedAssets"], 2)
            self.assertEqual(len(workflow.change_sets), 1)
            self.assertEqual(len(workflow.apply_calls), 2)
            self.assertEqual(
                [call["confirmation"] for call in workflow.apply_calls],
                ["LIVE APPLY plan_1", "LIVE APPLY plan_2"],
            )
            self.assertEqual(
                {call["changeSetId"] for call in workflow.apply_calls},
                {workflow.change_sets[0]},
            )

    def test_live_apply_requires_exact_confirmation_and_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_live_confirm_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A],
            )
            batch_plan_id = str(plan["batchPlanId"])

            with self.assertRaisesRegex(WorkflowError, "confirmation"):
                service.apply_live(batch_plan_id=batch_plan_id, confirmation="LIVE APPLY BATCH wrong")
            self.assertEqual(workflow.change_sets, [])
            self.assertEqual(workflow.apply_calls, [])

            started = service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=1,
            )
            self.assertEqual(started["execution"]["state"], "applied")
            with self.assertRaisesRegex(WorkflowError, "batch_apply_receipt"):
                service.apply_live(batch_plan_id=batch_plan_id, batch_apply_receipt="asfba_wrong")
            self.assertEqual(len(workflow.apply_calls), 1)

    def test_partial_failure_is_fail_stop_and_undo_reverts_applied_items(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_partial_live_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            animation_c = "/Game/Animations/A_Run.A_Run"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-lock-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                    _audit_item(animation_c, "root-track-candidate", 90.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root, fail_apply_asset=ANIMATION_B)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B, animation_c],
            )
            batch_plan_id = str(plan["batchPlanId"])

            result = service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=8,
            )
            execution = result["execution"]
            self.assertEqual(execution["state"], "partially_applied")
            self.assertEqual([item["state"] for item in execution["items"]], ["applied", "failed", "pending"])
            self.assertEqual(execution["failureCode"], "test-child-live-apply-failed")
            self.assertEqual(len(workflow.apply_calls), 2)

            with self.assertRaisesRegex(WorkflowError, "stopped"):
                service.apply_live(
                    batch_plan_id=batch_plan_id,
                    batch_apply_receipt=str(execution["batchApplyReceipt"]),
                )
            self.assertEqual(len(workflow.apply_calls), 2)

            undone = service.undo_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"UNDO BATCH {batch_plan_id}",
                max_assets=8,
            )
            self.assertEqual(undone["execution"]["state"], "undone")
            self.assertEqual(
                [item["state"] for item in undone["execution"]["items"]],
                ["undone", "failed", "not-applied"],
            )
            self.assertEqual([call["assetPath"] for call in workflow.undo_calls], [ANIMATION_A])

    def test_batch_undo_is_reverse_order_and_receipt_continued(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_undo_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-lock-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B],
            )
            batch_plan_id = str(plan["batchPlanId"])
            applied = service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=8,
            )
            self.assertEqual(applied["execution"]["state"], "applied")

            first = service.undo_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"UNDO BATCH {batch_plan_id}",
                max_assets=1,
            )
            undo_receipt = str(first["execution"]["undo"]["batchUndoReceipt"])
            self.assertEqual(first["execution"]["state"], "undoing")
            self.assertEqual([call["assetPath"] for call in workflow.undo_calls], [ANIMATION_B])

            second = service.undo_live(
                batch_plan_id=batch_plan_id,
                batch_undo_receipt=undo_receipt,
                max_assets=1,
            )
            self.assertEqual(second["execution"]["state"], "undone")
            self.assertEqual([call["assetPath"] for call in workflow.undo_calls], [ANIMATION_B, ANIMATION_A])

    def test_all_no_op_batch_discards_empty_change_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_noop_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root, no_op_assets={ANIMATION_A})
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A],
            )
            batch_plan_id = str(plan["batchPlanId"])
            result = service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
            )
            execution = result["execution"]
            self.assertEqual(execution["state"], "applied")
            self.assertEqual(execution["progress"]["noOpAssets"], 1)
            self.assertTrue(execution["emptyChangeSetDiscarded"])
            self.assertEqual(execution["changeSetId"], "")
            self.assertEqual(workflow.discarded_change_sets, [workflow.change_sets[0]])

    def test_first_live_apply_failure_discards_empty_change_set(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_first_fail_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root, fail_apply_asset=ANIMATION_A)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A],
            )
            batch_plan_id = str(plan["batchPlanId"])

            result = service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
            )

            execution = result["execution"]
            self.assertEqual(execution["state"], "failed")
            self.assertEqual(execution["progress"]["failedAssets"], 1)
            self.assertTrue(execution["emptyChangeSetDiscarded"])
            self.assertEqual(execution["changeSetId"], "")
            self.assertEqual(execution["discardedEmptyChangeSetId"], workflow.change_sets[0])
            self.assertEqual(workflow.discarded_change_sets, [workflow.change_sets[0]])

    def test_undo_failure_retries_same_transaction_with_same_receipt(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_undo_retry_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-lock-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root, fail_undo_asset_once=ANIMATION_B)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=8,
            )

            failed = service.undo_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"UNDO BATCH {batch_plan_id}",
                max_assets=1,
            )
            undo_receipt = str(failed["execution"]["undo"]["batchUndoReceipt"])
            self.assertEqual(failed["execution"]["state"], "undo_failed")
            self.assertEqual(failed["execution"]["undo"]["processedAssets"], 0)
            self.assertEqual([call["assetPath"] for call in workflow.undo_calls], [ANIMATION_B])

            retried = service.undo_live(
                batch_plan_id=batch_plan_id,
                batch_undo_receipt=undo_receipt,
                max_assets=1,
            )
            self.assertEqual(retried["execution"]["state"], "undoing")
            self.assertEqual(retried["execution"]["undo"]["processedAssets"], 1)
            self.assertEqual([call["assetPath"] for call in workflow.undo_calls], [ANIMATION_B, ANIMATION_B])

            completed = service.undo_live(
                batch_plan_id=batch_plan_id,
                batch_undo_receipt=undo_receipt,
                max_assets=1,
            )
            self.assertEqual(completed["execution"]["state"], "undone")
            self.assertEqual(
                [call["assetPath"] for call in workflow.undo_calls],
                [ANIMATION_B, ANIMATION_B, ANIMATION_A],
            )

    def test_live_step_is_bounded_to_eight_assets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_step_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-lock-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A],
            )

            with self.assertRaisesRegex(WorkflowError, "1 through 8"):
                service.apply_live(
                    batch_plan_id=str(plan["batchPlanId"]),
                    confirmation=f"LIVE APPLY BATCH {plan['batchPlanId']}",
                    max_assets=9,
                )
            self.assertEqual(workflow.change_sets, [])

    def test_batch_save_and_verify_are_bounded_to_two_assets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_persist_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-track-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                    _audit_item(ANIMATION_C, "root-track-candidate", 125.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B, ANIMATION_C],
            )
            batch_plan_id = str(plan["batchPlanId"])
            applied = service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=8,
            )
            self.assertEqual(applied["execution"]["state"], "applied")

            first_save = service.save(
                batch_plan_id=batch_plan_id,
                confirmation=f"SAVE BATCH {batch_plan_id}",
                max_assets=2,
            )
            batch_save_receipt = str(first_save["execution"]["save"]["batchSaveReceipt"])
            self.assertEqual(first_save["execution"]["state"], "saving")
            self.assertEqual(first_save["execution"]["save"]["processedAssets"], 2)
            self.assertEqual(workflow.save_commit_calls, [ANIMATION_A, ANIMATION_B])

            second_save = service.save(
                batch_plan_id=batch_plan_id,
                batch_save_receipt=batch_save_receipt,
                max_assets=2,
            )
            self.assertEqual(second_save["execution"]["state"], "saved")
            self.assertEqual(second_save["execution"]["save"]["processedAssets"], 3)
            self.assertEqual(workflow.save_commit_calls, [ANIMATION_A, ANIMATION_B, ANIMATION_C])
            self.assertEqual(second_save["execution"]["progress"]["savedAssets"], 3)
            self.assertTrue(all(item["rollbackAvailable"] for item in second_save["execution"]["items"]))

            with self.assertRaisesRegex(WorkflowError, "persisted Batch Rollback"):
                service.undo_live(
                    batch_plan_id=batch_plan_id,
                    confirmation=f"UNDO BATCH {batch_plan_id}",
                )

            first_verify = service.verify(batch_plan_id=batch_plan_id, max_assets=2)
            batch_verify_receipt = str(first_verify["execution"]["verify"]["batchVerifyReceipt"])
            self.assertEqual(first_verify["execution"]["state"], "verifying")
            self.assertEqual(first_verify["execution"]["verify"]["processedAssets"], 2)
            self.assertEqual(workflow.verify_calls, [ANIMATION_A, ANIMATION_B])

            second_verify = service.verify(
                batch_plan_id=batch_plan_id,
                batch_verify_receipt=batch_verify_receipt,
                max_assets=2,
            )
            self.assertEqual(second_verify["execution"]["state"], "verified")
            self.assertEqual(second_verify["execution"]["verify"]["processedAssets"], 3)
            self.assertEqual(workflow.verify_calls, [ANIMATION_A, ANIMATION_B, ANIMATION_C])
            self.assertEqual(second_verify["execution"]["progress"]["verifiedAssets"], 3)

    def test_batch_index_refresh_preview_is_bounded_then_apply_switches_once(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_index_refresh_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-track-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                    _audit_item(ANIMATION_C, "root-track-candidate", 125.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B, ANIMATION_C],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=8,
            )
            first_save = service.save(
                batch_plan_id=batch_plan_id,
                confirmation=f"SAVE BATCH {batch_plan_id}",
                max_assets=2,
            )
            service.save(
                batch_plan_id=batch_plan_id,
                batch_save_receipt=str(first_save["execution"]["save"]["batchSaveReceipt"]),
                max_assets=2,
            )
            first_verify = service.verify(batch_plan_id=batch_plan_id, max_assets=2)
            service.verify(
                batch_plan_id=batch_plan_id,
                batch_verify_receipt=str(first_verify["execution"]["verify"]["batchVerifyReceipt"]),
                max_assets=2,
            )

            first_preview = service.refresh_index(batch_plan_id=batch_plan_id, mode="Preview", max_assets=2)
            refresh_receipt = str(first_preview["execution"]["indexRefresh"]["batchIndexRefreshReceipt"])
            self.assertEqual(first_preview["execution"]["state"], "index_refresh_preparing")
            self.assertEqual(first_preview["execution"]["indexRefresh"]["processedAssets"], 2)
            self.assertEqual(workflow.index_refresh_prepare_calls, [ANIMATION_A, ANIMATION_B])

            ready = service.refresh_index(
                batch_plan_id=batch_plan_id,
                mode="Preview",
                batch_index_refresh_receipt=refresh_receipt,
                max_assets=2,
            )
            self.assertEqual(ready["execution"]["state"], "index_refresh_ready")
            self.assertEqual(ready["execution"]["indexRefresh"]["preparedAssets"], 3)
            self.assertEqual(workflow.index_refresh_prepare_calls, [ANIMATION_A, ANIMATION_B, ANIMATION_C])
            with self.assertRaisesRegex(WorkflowError, "confirmation"):
                service.refresh_index(
                    batch_plan_id=batch_plan_id,
                    mode="Apply",
                    batch_index_refresh_receipt=refresh_receipt,
                    confirmation="wrong",
                )
            self.assertEqual(workflow.index_refresh_apply_calls, [])

            applied = service.refresh_index(
                batch_plan_id=batch_plan_id,
                mode="Apply",
                batch_index_refresh_receipt=refresh_receipt,
                confirmation=f"REFRESH BATCH {batch_plan_id}",
            )
            self.assertEqual(applied["execution"]["state"], "index_refreshed")
            self.assertTrue(applied["execution"]["indexRefresh"]["applied"])
            self.assertTrue(applied["execution"]["indexRefresh"]["restartRequired"])
            self.assertEqual(applied["execution"]["progress"]["indexRefreshedAssets"], 3)
            self.assertEqual(len(workflow.index_refresh_apply_calls), 1)
            self.assertEqual(len(workflow.index_refresh_apply_calls[0]), 3)
            self.assertEqual(
                applied["execution"]["indexRefresh"]["generation"]["refreshedAssetCount"],
                3,
            )

    def test_batch_rollback_discards_prepared_index_refresh_candidates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_index_revert_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-track-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=8,
            )
            saved = service.save(
                batch_plan_id=batch_plan_id,
                confirmation=f"SAVE BATCH {batch_plan_id}",
                max_assets=2,
            )
            self.assertEqual(saved["execution"]["state"], "saved")
            verified = service.verify(batch_plan_id=batch_plan_id, max_assets=2)
            self.assertEqual(verified["execution"]["state"], "verified")
            preview = service.refresh_index(batch_plan_id=batch_plan_id, mode="Preview", max_assets=2)
            self.assertEqual(preview["execution"]["state"], "index_refresh_ready")

            rollback = service.rollback(batch_plan_id=batch_plan_id, mode="DryRun", max_assets=1)
            self.assertEqual(rollback["execution"]["state"], "rollback_dry_run")
            self.assertEqual(workflow.index_refresh_discarded, [ANIMATION_A, ANIMATION_B])
            self.assertTrue(
                all(item["indexRefreshState"] == "discarded" for item in rollback["execution"]["items"])
            )

    def test_rollback_manifest_retry_does_not_save_asset_twice(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_manifest_retry_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-track-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            workflow.fail_manifest_once_asset = ANIMATION_A
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
            )

            failed = service.save(
                batch_plan_id=batch_plan_id,
                confirmation=f"SAVE BATCH {batch_plan_id}",
            )
            batch_save_receipt = str(failed["execution"]["save"]["batchSaveReceipt"])
            self.assertEqual(failed["execution"]["state"], "save_failed")
            self.assertEqual(failed["execution"]["save"]["processedAssets"], 0)
            self.assertEqual(failed["execution"]["items"][0]["saveState"], "saved")
            self.assertFalse(failed["execution"]["items"][0]["rollbackAvailable"])
            self.assertEqual(workflow.save_commit_calls, [ANIMATION_A])

            retried = service.save(
                batch_plan_id=batch_plan_id,
                batch_save_receipt=batch_save_receipt,
            )
            self.assertEqual(retried["execution"]["state"], "saved")
            self.assertEqual(retried["execution"]["save"]["processedAssets"], 1)
            self.assertTrue(retried["execution"]["items"][0]["rollbackAvailable"])
            self.assertEqual(workflow.save_commit_calls, [ANIMATION_A])
            self.assertEqual(workflow.rollback_manifest_calls, [ANIMATION_A, ANIMATION_A])

    def test_save_and_verify_steps_reject_more_than_two_assets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_persist_step_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(work_root, [_audit_item(ANIMATION_A, "root-track-candidate", 50.0)])
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
            )
            with self.assertRaisesRegex(WorkflowError, "1 through 2"):
                service.save(
                    batch_plan_id=batch_plan_id,
                    confirmation=f"SAVE BATCH {batch_plan_id}",
                    max_assets=3,
                )
            self.assertEqual(workflow.save_commit_calls, [])

    def test_persisted_rollback_is_bounded_reverse_order_dry_run_then_commit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_rollback_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-track-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                    _audit_item(ANIMATION_C, "root-track-candidate", 125.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B, ANIMATION_C],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
                max_assets=8,
            )
            first_save = service.save(
                batch_plan_id=batch_plan_id,
                confirmation=f"SAVE BATCH {batch_plan_id}",
                max_assets=2,
            )
            service.save(
                batch_plan_id=batch_plan_id,
                batch_save_receipt=str(first_save["execution"]["save"]["batchSaveReceipt"]),
                max_assets=2,
            )

            first_dry_run = service.rollback(batch_plan_id=batch_plan_id, mode="DryRun", max_assets=2)
            batch_rollback_receipt = str(first_dry_run["execution"]["rollback"]["batchRollbackReceipt"])
            self.assertEqual(first_dry_run["execution"]["state"], "rollback_dry_run")
            self.assertEqual(first_dry_run["execution"]["rollback"]["dryRunProcessedAssets"], 2)
            self.assertEqual(workflow.rollback_dry_run_calls, [ANIMATION_C, ANIMATION_B])

            ready = service.rollback(
                batch_plan_id=batch_plan_id,
                mode="DryRun",
                batch_rollback_receipt=batch_rollback_receipt,
                max_assets=2,
            )
            self.assertEqual(ready["execution"]["state"], "rollback_ready")
            self.assertEqual(workflow.rollback_dry_run_calls, [ANIMATION_C, ANIMATION_B, ANIMATION_A])
            with self.assertRaisesRegex(WorkflowError, "confirmation"):
                service.rollback(
                    batch_plan_id=batch_plan_id,
                    mode="Commit",
                    batch_rollback_receipt=batch_rollback_receipt,
                    confirmation="wrong",
                    max_assets=2,
                )
            self.assertEqual(workflow.rollback_commit_calls, [])

            first_commit = service.rollback(
                batch_plan_id=batch_plan_id,
                mode="Commit",
                batch_rollback_receipt=batch_rollback_receipt,
                confirmation=f"ROLLBACK BATCH {batch_plan_id}",
                max_assets=2,
            )
            self.assertEqual(first_commit["execution"]["state"], "rollback_committing")
            self.assertEqual(first_commit["execution"]["rollback"]["commitProcessedAssets"], 2)
            self.assertEqual(workflow.rollback_commit_calls, [ANIMATION_C, ANIMATION_B])

            completed = service.rollback(
                batch_plan_id=batch_plan_id,
                mode="Commit",
                batch_rollback_receipt=batch_rollback_receipt,
                confirmation=f"ROLLBACK BATCH {batch_plan_id}",
                max_assets=2,
            )
            self.assertEqual(completed["execution"]["state"], "rolled_back")
            self.assertEqual(workflow.rollback_commit_calls, [ANIMATION_C, ANIMATION_B, ANIMATION_A])
            self.assertEqual(completed["execution"]["progress"]["rolledBackAssets"], 3)
            self.assertEqual(completed["execution"]["progress"]["savedAssets"], 0)
            self.assertTrue(all(item["rollbackState"] == "rolled-back" for item in completed["execution"]["items"]))

    def test_persisted_rollback_commit_failure_retries_same_child(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_rollback_retry_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-track-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            workflow.fail_rollback_commit_asset_once = ANIMATION_B
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
            )
            applied = service.apply_live(
                batch_plan_id=batch_plan_id,
                batch_apply_receipt=str(service.get(batch_plan_id=batch_plan_id)["execution"]["batchApplyReceipt"]),
                max_assets=8,
            )
            saved = service.save(
                batch_plan_id=batch_plan_id,
                confirmation=f"SAVE BATCH {batch_plan_id}",
                max_assets=2,
            )
            self.assertEqual(saved["execution"]["state"], "saved")
            dry_run = service.rollback(batch_plan_id=batch_plan_id, mode="DryRun", max_assets=2)
            batch_rollback_receipt = str(dry_run["execution"]["rollback"]["batchRollbackReceipt"])
            failed = service.rollback(
                batch_plan_id=batch_plan_id,
                mode="Commit",
                batch_rollback_receipt=batch_rollback_receipt,
                confirmation=f"ROLLBACK BATCH {batch_plan_id}",
                max_assets=2,
            )
            self.assertEqual(failed["execution"]["state"], "rollback_failed")
            self.assertEqual(failed["execution"]["rollback"]["commitProcessedAssets"], 0)
            self.assertEqual(workflow.rollback_commit_calls, [ANIMATION_B])

            retried = service.rollback(
                batch_plan_id=batch_plan_id,
                mode="Commit",
                batch_rollback_receipt=batch_rollback_receipt,
                confirmation=f"ROLLBACK BATCH {batch_plan_id}",
                max_assets=1,
            )
            self.assertEqual(retried["execution"]["state"], "rollback_committing")
            self.assertEqual(retried["execution"]["rollback"]["commitProcessedAssets"], 1)
            self.assertEqual(workflow.rollback_commit_calls, [ANIMATION_B, ANIMATION_B])
            completed = service.rollback(
                batch_plan_id=batch_plan_id,
                mode="Commit",
                batch_rollback_receipt=batch_rollback_receipt,
                confirmation=f"ROLLBACK BATCH {batch_plan_id}",
            )
            self.assertEqual(completed["execution"]["state"], "rolled_back")
            self.assertEqual(workflow.rollback_commit_calls, [ANIMATION_B, ANIMATION_B, ANIMATION_A])
            self.assertEqual(applied["execution"]["progress"]["appliedAssets"], 2)

    def test_partial_save_failure_can_undo_unsaved_then_rollback_persisted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_scale_fix_batch_partial_save_rollback_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            report_id = _write_report(
                work_root,
                [
                    _audit_item(ANIMATION_A, "root-track-candidate", 50.0),
                    _audit_item(ANIMATION_B, "root-track-candidate", 75.0),
                ],
            )
            workflow = _FakeWorkflowService(work_root)
            workflow.fail_save_asset = ANIMATION_B
            service = AnimationScaleFixBatchService(workflow)
            plan = service.plan(
                audit_task_id=AUDIT_TASK_ID,
                audit_report_id=report_id,
                asset_paths=[ANIMATION_A, ANIMATION_B],
            )
            batch_plan_id = str(plan["batchPlanId"])
            service.apply_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"LIVE APPLY BATCH {batch_plan_id}",
            )
            applied = service.apply_live(
                batch_plan_id=batch_plan_id,
                batch_apply_receipt=str(service.get(batch_plan_id=batch_plan_id)["execution"]["batchApplyReceipt"]),
                max_assets=8,
            )
            self.assertEqual(applied["execution"]["state"], "applied")
            save_failed = service.save(
                batch_plan_id=batch_plan_id,
                confirmation=f"SAVE BATCH {batch_plan_id}",
                max_assets=2,
            )
            self.assertEqual(save_failed["execution"]["state"], "save_failed")
            self.assertEqual(save_failed["execution"]["items"][0]["saveState"], "saved")
            self.assertEqual(save_failed["execution"]["items"][1]["saveState"], "unsaved")

            live_recovery = service.undo_live(
                batch_plan_id=batch_plan_id,
                confirmation=f"UNDO BATCH {batch_plan_id}",
                max_assets=8,
            )
            self.assertEqual(live_recovery["execution"]["state"], "persisted_partial")
            self.assertEqual([call["assetPath"] for call in workflow.undo_calls], [ANIMATION_B])
            self.assertEqual(live_recovery["execution"]["items"][0]["saveState"], "saved")
            self.assertEqual(live_recovery["execution"]["items"][1]["state"], "undone")

            dry_run = service.rollback(batch_plan_id=batch_plan_id, mode="DryRun")
            batch_rollback_receipt = str(dry_run["execution"]["rollback"]["batchRollbackReceipt"])
            self.assertEqual(workflow.rollback_dry_run_calls, [ANIMATION_A])
            restored = service.rollback(
                batch_plan_id=batch_plan_id,
                mode="Commit",
                batch_rollback_receipt=batch_rollback_receipt,
                confirmation=f"ROLLBACK BATCH {batch_plan_id}",
            )
            self.assertEqual(restored["execution"]["state"], "rolled_back")
            self.assertEqual(workflow.rollback_commit_calls, [ANIMATION_A])

if __name__ == "__main__":
    unittest.main()

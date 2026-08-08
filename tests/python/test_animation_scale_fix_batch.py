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

if __name__ == "__main__":
    unittest.main()

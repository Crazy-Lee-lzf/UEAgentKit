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
    def __init__(self, work_root: Path, *, fail_asset: str = "") -> None:
        self.config = SimpleNamespace(work_root=work_root)
        self.project_name = "TestProject"
        self.fail_asset = fail_asset
        self.calls: list[dict[str, Any]] = []
        self.discarded: list[str] = []

    def prepare_high_level_change(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if kwargs["asset_path"] == self.fail_asset:
            raise WorkflowError("test-child-plan-failed", "Injected child Plan failure.")
        index = len(self.calls)
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


if __name__ == "__main__":
    unittest.main()

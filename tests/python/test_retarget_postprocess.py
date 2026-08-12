from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_workflow import WorkflowError  # noqa: E402
from ue_agent_kit.retarget_postprocess import RetargetPostprocessService  # noqa: E402

ANIM_A = "/Game/Retargeted/RTG_Idle.RTG_Idle"
ANIM_B = "/Game/Retargeted/RTG_Additive.RTG_Additive"
BLEND = "/Game/Retargeted/BS_Locomotion.BS_Locomotion"
MONTAGE = "/Game/Retargeted/AM_Attack.AM_Attack"


def _diagnosis(asset_path: str, *, additive: bool = False) -> dict[str, Any]:
    return {
        "assetPath": asset_path,
        "status": "success",
        "loadedBefore": True,
        "loadedByBridge": False,
        "skeletonPath": "/Game/Characters/Test/SK_Target_Skeleton.SK_Target_Skeleton",
        "additiveAnimType": 1 if additive else 0,
        "additiveBasePoseType": 0,
        "additiveRefSequencePath": "" if not additive else "/Game/Animations/Base.Base",
        "enableRootMotion": False,
        "forceRootLock": False,
        "useNormalizedRootMotionScale": True,
        "rootMotionRootLock": 0,
        "previewEvaluationStatus": "unsupported-additive-requires-base-pose" if additive else "success",
        "previewMeshPath": "/Game/Characters/Test/SK_Target.SK_Target",
        "tracks": [
            {
                "bone": "Root",
                "boneExists": True,
                "trackExists": True,
                "referenceComponentScale": {"x": 100.0, "y": 100.0, "z": 100.0},
                "firstScale": {"x": 1.0, "y": 1.0, "z": 1.0},
                "compressedFirstScale": {"x": 1.0, "y": 1.0, "z": 1.0},
            }
        ],
        "previewSamples": [
            {
                "fraction": fraction,
                "time": fraction,
                "bones": [
                    {
                        "bone": "Root",
                        "boneExists": True,
                        "componentScale": {"x": 1.0, "y": 1.0, "z": 1.0},
                        "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    },
                    {
                        "bone": "pelvis",
                        "boneExists": True,
                        "componentScale": {"x": 1.0, "y": 1.0, "z": 1.0},
                        "componentLocation": {"x": 0.0, "y": 0.0, "z": 90.0},
                    },
                ],
            }
            for fraction in (0.0, 0.5, 1.0)
        ],
    }


class _FakeWorkflow:
    def __init__(self, work_root: Path, outputs: list[dict[str, Any]]) -> None:
        self.config = SimpleNamespace(work_root=work_root)
        self.project_name = "我的项目"
        self.index_service = None
        self.live_editor_service = MagicMock()
        self.live_editor_service.status.return_value = {"state": "available", "sessionId": "editor-session-1"}
        self._outputs = outputs
        self.context_calls = 0

    @staticmethod
    def _workflow_error(code: str, message: str, *, details: dict[str, Any] | None = None) -> WorkflowError:
        return WorkflowError(code, message, details=details)

    def get_animation_retarget_postprocess_context(self, *, task_id: str) -> dict[str, Any]:
        self.context_calls += 1
        if task_id != "rtg_batch_test":
            raise self._workflow_error("retarget-batch-task-not-found", "not found")
        return {
            "taskId": task_id,
            "status": "completed",
            "planId": "plan_test",
            "planDigest": "sha256:" + "a" * 64,
            "retargeter": "/Game/Retarget/RTG_Test.RTG_Test",
            "sourceMesh": "/Game/Source.SK_Source",
            "targetMesh": "/Game/Target.SK_Target",
            "outputDirectory": "/Game/Retargeted",
            "outputs": [dict(item) for item in self._outputs],
            "savedAssets": [],
        }


class RetargetPostprocessTests(unittest.TestCase):
    def test_only_anim_sequences_are_audited_and_composites_become_followups(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_") as temporary_root:
            outputs = [
                {"outputPath": ANIM_A, "assetType": "AnimSequence", "assetClass": "/Script/Engine.AnimSequence"},
                {"outputPath": BLEND, "assetType": "BlendSpace", "assetClass": "/Script/Engine.BlendSpace"},
                {"outputPath": MONTAGE, "assetType": "AnimMontage", "assetClass": "/Script/Engine.AnimMontage"},
            ]
            workflow = _FakeWorkflow(Path(temporary_root) / "Work", outputs)
            workflow.live_editor_service.call_tool.return_value = {
                "ok": True,
                "result": {"assets": [_diagnosis(ANIM_A)]},
            }
            service = RetargetPostprocessService(workflow)

            started = service.start(retarget_task_id="rtg_batch_test", batch_size=1)
            self.assertEqual(started["state"], "auditing")
            self.assertEqual(started["outputSummary"]["animationSequencePaths"], [ANIM_A])
            self.assertEqual(started["outputSummary"]["referenceOutputCount"], 2)

            analyzed = service.get(postprocess_id=str(started["postprocessId"]))
            self.assertEqual(analyzed["state"], "analyzed")
            suggestions = analyzed["suggestions"]
            self.assertEqual(suggestions["scaleFixCandidateCount"], 1)
            self.assertEqual(suggestions["scaleFixCandidates"][0]["assetPath"], ANIM_A)
            self.assertEqual(suggestions["referenceFollowupCount"], 2)
            workflow.live_editor_service.call_tool.assert_called_once()
            diagnosis_params = workflow.live_editor_service.call_tool.call_args.args[1]
            self.assertEqual(diagnosis_params["animationPaths"], [ANIM_A])

    def test_additive_output_remains_manual_review(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_additive_") as temporary_root:
            workflow = _FakeWorkflow(
                Path(temporary_root) / "Work",
                [{"outputPath": ANIM_B, "assetType": "AnimSequence", "assetClass": "/Script/Engine.AnimSequence"}],
            )
            workflow.live_editor_service.call_tool.return_value = {
                "ok": True,
                "result": {"assets": [_diagnosis(ANIM_B, additive=True)]},
            }
            service = RetargetPostprocessService(workflow)
            started = service.start(retarget_task_id="rtg_batch_test")
            analyzed = service.get(postprocess_id=str(started["postprocessId"]))

            self.assertEqual(analyzed["suggestions"]["scaleFixCandidateCount"], 0)
            self.assertEqual(analyzed["suggestions"]["manualReviewCount"], 1)
            self.assertEqual(
                analyzed["suggestions"]["manualReview"][0]["classification"],
                "additive-requires-base-pose",
            )

    def test_suggested_plan_is_work_root_bound_non_applying_and_tamper_checked(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_plan_") as temporary_root:
            work_root = Path(temporary_root) / "Work"
            workflow = _FakeWorkflow(
                work_root,
                [{"outputPath": ANIM_A, "assetType": "AnimSequence", "assetClass": "/Script/Engine.AnimSequence"}],
            )
            workflow.live_editor_service.call_tool.return_value = {
                "ok": True,
                "result": {"assets": [_diagnosis(ANIM_A)]},
            }
            service = RetargetPostprocessService(workflow)
            started = service.start(retarget_task_id="rtg_batch_test")
            analyzed = service.get(postprocess_id=str(started["postprocessId"]))

            planned = service.plan(postprocess_id=str(analyzed["postprocessId"]), description="review retarget outputs")
            boundary = planned["result"]["executionBoundary"]
            self.assertFalse(boundary["modifiesAssets"])
            self.assertFalse(boundary["autoApplyAllowed"])
            self.assertTrue(boundary["requiresUserReview"])
            self.assertTrue(boundary["requiresRetargetOutputIndexRefreshBeforeP2Plan"])
            plan_path = work_root / str(planned["planRelativePath"])
            self.assertTrue(plan_path.is_file())
            self.assertTrue(str(planned["planRelativePath"]).startswith("retarget-postprocess/"))
            first_digest = planned["planDigest"]
            repeated = service.plan(postprocess_id=str(analyzed["postprocessId"]))
            self.assertEqual(repeated["planDigest"], first_digest)

            payload = json.loads(plan_path.read_text(encoding="utf-8"))
            payload["description"] = "tampered"
            plan_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "no longer matches"):
                service.plan(postprocess_id=str(analyzed["postprocessId"]))

    def test_output_class_fallback_detects_aim_offset_without_auditing_it(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_aim_") as temporary_root:
            aim = "/Game/Retargeted/AO_Aim.AO_Aim"
            workflow = _FakeWorkflow(
                Path(temporary_root) / "Work",
                [{"outputPath": aim, "assetClass": "/Script/Engine.AimOffsetBlendSpace"}],
            )
            service = RetargetPostprocessService(workflow)
            started = service.start(retarget_task_id="rtg_batch_test")

            self.assertEqual(started["state"], "analyzed")
            self.assertEqual(started["outputSummary"]["animationSequenceCount"], 0)
            self.assertEqual(started["suggestions"]["referenceFollowupCount"], 1)
            self.assertEqual(started["suggestions"]["referenceFollowups"][0]["assetType"], "AimOffset")
            workflow.live_editor_service.call_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()

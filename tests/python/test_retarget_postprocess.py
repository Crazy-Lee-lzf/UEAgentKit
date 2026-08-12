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
    def __init__(
        self,
        work_root: Path,
        outputs: list[dict[str, Any]],
        *,
        status: str = "completed",
        verification: dict[str, Any] | None = None,
    ) -> None:
        self.config = SimpleNamespace(work_root=work_root)
        self.project_name = "我的项目"
        self.index_service = None
        self.live_editor_service = MagicMock()
        self.live_editor_service.status.return_value = {"state": "available", "sessionId": "editor-session-1"}
        self._outputs = outputs
        self.context_calls = 0
        self.status = status
        self.verification = verification or {"verified": False, "sampleCount": 0, "verifiedCount": 0, "verifiedAssets": []}

    @staticmethod
    def _workflow_error(code: str, message: str, *, details: dict[str, Any] | None = None) -> WorkflowError:
        return WorkflowError(code, message, details=details)

    def get_animation_retarget_postprocess_context(self, *, task_id: str) -> dict[str, Any]:
        self.context_calls += 1
        if task_id != "rtg_batch_test":
            raise self._workflow_error("retarget-batch-task-not-found", "not found")
        return {
            "taskId": task_id,
            "status": self.status,
            "planId": "plan_test",
            "planDigest": "sha256:" + "a" * 64,
            "retargeter": "/Game/Retarget/RTG_Test.RTG_Test",
            "sourceMesh": "/Game/Source.SK_Source",
            "targetMesh": "/Game/Target.SK_Target",
            "outputDirectory": "/Game/Retargeted",
            "outputs": [dict(item) for item in self._outputs],
            "savedAssets": [],
            "verification": dict(self.verification),
        }

    def prepare_batch_index_refresh_candidate(self, asset_path: str) -> dict[str, Any]:
        return {
            "candidateId": "irc_" + asset_path.rsplit("/", 1)[-1].replace(".", "_"),
            "assetPath": asset_path,
            "assetClass": "/Script/Engine.AnimSequence",
            "revision": "sha256:" + "b" * 64,
            "diskFileSize": 1234,
            "liveState": {"state": "offline", "loaded": False, "packageDirty": False},
        }

    def apply_batch_index_refresh(self, candidate_ids: list[str]) -> dict[str, Any]:
        return {
            "applied": True,
            "activeSnapshotChanged": True,
            "newGeneration": {"generationId": "gen_test"},
            "restartRequired": True,
        }

    def discard_batch_index_refresh_candidates(self, candidate_ids: list[str]) -> None:
        return None


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


def _analyzed_and_planned(workflow: _FakeWorkflow) -> tuple[RetargetPostprocessService, str]:
    workflow.live_editor_service.call_tool.return_value = {
        "ok": True,
        "result": {"assets": [_diagnosis(ANIM_A)]},
    }
    service = RetargetPostprocessService(workflow)
    started = service.start(retarget_task_id="rtg_batch_test", batch_size=1)
    analyzed = service.get(postprocess_id=str(started["postprocessId"]))
    service.plan(postprocess_id=str(analyzed["postprocessId"]), description="review retarget outputs")
    return service, str(analyzed["postprocessId"])


def _verified_revision(revision: str) -> dict[str, Any]:
    return {
        "verified": True,
        "sampleCount": 1,
        "verifiedCount": 1,
        "verifiedAssets": [{"assetPath": ANIM_A, "revision": revision}],
    }


class RetargetPostprocessIndexRefreshTests(unittest.TestCase):
    def _saved_workflow(self, work_root: Path, revision: str) -> _FakeWorkflow:
        outputs = [{"outputPath": ANIM_A, "assetType": "AnimSequence", "assetClass": "/Script/Engine.AnimSequence"}]
        return _FakeWorkflow(work_root, outputs, status="saved", verification=_verified_revision(revision))

    def test_index_refresh_requires_saved_and_verified(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_ir_gate_") as temporary_root:
            outputs = [{"outputPath": ANIM_A, "assetType": "AnimSequence", "assetClass": "/Script/Engine.AnimSequence"}]

            not_saved = _FakeWorkflow(Path(temporary_root) / "WorkA", outputs, status="completed")
            service, postprocess_id = _analyzed_and_planned(not_saved)
            with self.assertRaisesRegex(WorkflowError, "saved first"):
                service.refresh_index(postprocess_id=postprocess_id, mode="Preview")

            saved_unverified = _FakeWorkflow(Path(temporary_root) / "WorkB", outputs, status="saved")
            service2, postprocess_id2 = _analyzed_and_planned(saved_unverified)
            with self.assertRaisesRegex(WorkflowError, "independent verification"):
                service2.refresh_index(postprocess_id=postprocess_id2, mode="Preview")

    def test_index_refresh_preview_reaches_ready(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_ir_ready_") as temporary_root:
            workflow = self._saved_workflow(Path(temporary_root) / "Work", "sha256:" + "b" * 64)
            service, postprocess_id = _analyzed_and_planned(workflow)

            ready = service.refresh_index(postprocess_id=postprocess_id, mode="Preview", max_assets=1)
            self.assertEqual(ready["indexRefresh"]["state"], "ready")
            self.assertTrue(ready["indexRefresh"]["receipt"])
            self.assertEqual(ready["indexRefresh"]["orderedAssetCount"], 1)
            self.assertEqual(ready["indexRefresh"]["preparedCount"], 1)
            self.assertEqual(ready["indexRefresh"]["candidateAssetPaths"], [ANIM_A])

    def test_index_refresh_apply_requires_confirmation_and_refreshes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_ir_apply_") as temporary_root:
            workflow = self._saved_workflow(Path(temporary_root) / "Work", "sha256:" + "b" * 64)
            service, postprocess_id = _analyzed_and_planned(workflow)

            ready = service.refresh_index(postprocess_id=postprocess_id, mode="Preview", max_assets=1)
            self.assertEqual(ready["indexRefresh"]["state"], "ready")
            receipt = ready["indexRefresh"]["receipt"]

            with self.assertRaisesRegex(WorkflowError, "confirmation"):
                service.refresh_index(
                    postprocess_id=postprocess_id,
                    mode="Apply",
                    confirmation="wrong",
                    refresh_receipt=receipt,
                )

            applied = service.refresh_index(
                postprocess_id=postprocess_id,
                mode="Apply",
                confirmation=f"REFRESH RETARGET POSTPROCESS {postprocess_id}",
                refresh_receipt=receipt,
            )
            self.assertEqual(applied["indexRefresh"]["state"], "refreshed")
            self.assertTrue(applied["indexRefresh"]["restartRequired"])
            self.assertEqual(applied["indexRefresh"]["generation"]["generationId"], "gen_test")

    def test_index_refresh_revision_mismatch_fails_prepare(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_ir_mismatch_") as temporary_root:
            workflow = self._saved_workflow(Path(temporary_root) / "Work", "sha256:" + "c" * 64)
            service, postprocess_id = _analyzed_and_planned(workflow)

            result = service.refresh_index(postprocess_id=postprocess_id, mode="Preview", max_assets=1)
            self.assertEqual(result["indexRefresh"]["state"], "prepare_failed")
            self.assertEqual(
                result["indexRefresh"]["failureCode"],
                "retarget-postprocess-index-refresh-revision-mismatch",
            )


class RetargetPostprocessReopenTests(unittest.TestCase):
    def test_reopen_reads_persisted_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_reopen_") as temporary_root:
            workflow = _FakeWorkflow(
                Path(temporary_root) / "Work",
                [{"outputPath": ANIM_A, "assetType": "AnimSequence", "assetClass": "/Script/Engine.AnimSequence"}],
            )
            workflow.live_editor_service.call_tool.return_value = {
                "ok": True,
                "result": {"assets": [_diagnosis(ANIM_A)]},
            }
            service = RetargetPostprocessService(workflow)
            started = service.start(retarget_task_id="rtg_batch_test")
            analyzed = service.get(postprocess_id=str(started["postprocessId"]))
            planned = service.plan(postprocess_id=str(analyzed["postprocessId"]), description="review")

            reopened = service.reopen(plan_relative_path=str(planned["planRelativePath"]))
            self.assertTrue(reopened["ok"])
            self.assertTrue(reopened["readOnly"])
            self.assertEqual(reopened["postprocessId"], str(analyzed["postprocessId"]))
            self.assertEqual(reopened["outputSummary"]["animationSequencePaths"], [ANIM_A])
            self.assertEqual(reopened["suggestions"]["scaleFixCandidateCount"], 1)
            self.assertTrue(reopened["planDigest"].startswith("sha256:"))
            self.assertFalse(reopened["executionBoundary"]["modifiesAssets"])

    def test_reopen_rejects_invalid_or_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ueak_rtpp_reopen_bad_") as temporary_root:
            workflow = _FakeWorkflow(
                Path(temporary_root) / "Work",
                [{"outputPath": ANIM_A, "assetType": "AnimSequence", "assetClass": "/Script/Engine.AnimSequence"}],
            )
            service = RetargetPostprocessService(workflow)
            for bad in ("../outside/plan.json", "retarget-postprocess/x/other.json", "plan.json"):
                with self.assertRaisesRegex(WorkflowError, "exact relative path"):
                    service.reopen(plan_relative_path=bad)
            with self.assertRaisesRegex(WorkflowError, "missing"):
                service.reopen(plan_relative_path="retarget-postprocess/nope/plan.json")


if __name__ == "__main__":
    unittest.main()

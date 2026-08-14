from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.additive_evaluation import (  # noqa: E402
    EVALUATION_STATUSES,
    build_additive_evaluation_item,
    evaluation_feasible,
)


def _asset(**overrides: object) -> dict[str, object]:
    asset: dict[str, object] = {
        "assetPath": "/Game/Animations/A_Add.A_Add",
        "status": "success",
        "skeletonPath": "/Game/Characters/SK_Skeleton.SK_Skeleton",
        "additiveAnimType": 1,
        "additiveTypeName": "LocalSpaceBase",
        "additiveBasePoseType": 3,
        "basePoseTypeName": "AnimationFrame",
        "additiveRefFrameIndex": 0,
        "additiveRefSequencePath": "/Game/Animations/A_Base.A_Base",
        "basePose": {
            "refSequenceResolved": True,
            "skeletonPath": "/Game/Characters/SK_Skeleton.SK_Skeleton",
            "skeletonCompatible": True,
            "frameCount": 30,
            "refFrameValid": True,
        },
        "evaluation": {
            "status": "evaluated",
            "source": "editor-bone-pose-additive-accumulate",
            "refFrameClamped": False,
            "samples": [],
        },
    }
    asset.update(overrides)
    return asset


class AdditiveEvaluationTests(unittest.TestCase):
    def test_evaluation_feasible_only_when_evaluated(self) -> None:
        self.assertTrue(evaluation_feasible(_asset()))
        self.assertFalse(evaluation_feasible(_asset(evaluation={"status": "skipped-non-additive"})))
        self.assertFalse(evaluation_feasible(_asset(evaluation=None)))

    def test_build_item_evaluated_reuses_classification(self) -> None:
        item = build_additive_evaluation_item(_asset())
        self.assertEqual(item["classification"], "additive-valid")
        self.assertTrue(item["combinedEvaluationFeasible"])
        self.assertTrue(item["evaluationFeasible"])
        self.assertEqual(item["evaluation"]["status"], "evaluated")
        self.assertTrue(item["suggestedNextStep"])

    def test_build_item_ref_frame_invalid_is_feasible_but_flagged(self) -> None:
        asset = _asset(
            basePose={
                "refSequenceResolved": True,
                "skeletonCompatible": True,
                "frameCount": 30,
                "refFrameValid": False,
            },
            evaluation={"status": "evaluated", "refFrameClamped": True, "samples": []},
        )
        item = build_additive_evaluation_item(asset)
        self.assertEqual(item["classification"], "additive-base-pose-ref-frame-invalid")
        self.assertFalse(item["combinedEvaluationFeasible"])
        self.assertTrue(item["evaluationFeasible"])
        self.assertIn("clamped", item["suggestedNextStep"])

    def test_build_item_skipped_non_additive_is_not_feasible(self) -> None:
        asset = _asset(
            additiveTypeName="None",
            basePoseTypeName="None",
            evaluation={"status": "skipped-non-additive"},
        )
        item = build_additive_evaluation_item(asset)
        self.assertEqual(item["classification"], "non-additive")
        self.assertFalse(item["evaluationFeasible"])

    def test_build_item_skipped_missing_base_pose_is_not_feasible(self) -> None:
        asset = _asset(
            basePose={"refSequenceResolved": False},
            evaluation={"status": "skipped-missing-base-pose"},
        )
        item = build_additive_evaluation_item(asset)
        self.assertEqual(item["classification"], "additive-missing-base-pose")
        self.assertFalse(item["evaluationFeasible"])

    def test_build_item_skipped_skeleton_mismatch_is_not_feasible(self) -> None:
        asset = _asset(
            basePose={
                "refSequenceResolved": True,
                "skeletonPath": "/Game/Other/SK_Other.SK_Other",
                "skeletonCompatible": False,
                "frameCount": 30,
                "refFrameValid": True,
            },
            evaluation={"status": "skipped-skeleton-mismatch"},
        )
        item = build_additive_evaluation_item(asset)
        self.assertEqual(item["classification"], "additive-base-pose-skeleton-mismatch")
        self.assertFalse(item["evaluationFeasible"])

    def test_statuses_are_stable(self) -> None:
        self.assertIsInstance(EVALUATION_STATUSES, tuple)
        self.assertIn("evaluated", EVALUATION_STATUSES)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.additive_diagnose import (  # noqa: E402
    ADDITIVE_CLASSIFICATIONS,
    build_additive_diagnosis_item,
    classify_additive_animation,
    combined_evaluation_feasible,
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
    }
    asset.update(overrides)
    return asset


class AdditiveDiagnoseTests(unittest.TestCase):
    def test_non_additive(self) -> None:
        asset = _asset(
            additiveAnimType=0,
            additiveTypeName="None",
            additiveBasePoseType=0,
            basePoseTypeName="None",
            additiveRefSequencePath="",
        )
        self.assertEqual(classify_additive_animation(asset), "non-additive")

    def test_reference_pose_needs_no_sequence(self) -> None:
        asset = _asset(
            additiveBasePoseType=1,
            basePoseTypeName="ReferencePose",
            additiveRefSequencePath="",
            basePose=None,
        )
        self.assertEqual(classify_additive_animation(asset), "additive-valid")

    def test_resolved_sequence_is_valid(self) -> None:
        self.assertEqual(classify_additive_animation(_asset()), "additive-valid")

    def test_missing_base_pose_sequence(self) -> None:
        asset = _asset(basePose={"refSequenceResolved": False})
        self.assertEqual(classify_additive_animation(asset), "additive-missing-base-pose")

    def test_base_pose_skeleton_mismatch(self) -> None:
        asset = _asset(
            basePose={
                "refSequenceResolved": True,
                "skeletonPath": "/Game/Other/SK_Other.SK_Other",
                "skeletonCompatible": False,
                "frameCount": 30,
                "refFrameValid": True,
            }
        )
        self.assertEqual(classify_additive_animation(asset), "additive-base-pose-skeleton-mismatch")

    def test_base_pose_ref_frame_invalid(self) -> None:
        asset = _asset(
            basePose={
                "refSequenceResolved": True,
                "skeletonCompatible": True,
                "frameCount": 30,
                "refFrameValid": False,
            }
        )
        self.assertEqual(classify_additive_animation(asset), "additive-base-pose-ref-frame-invalid")

    def test_scaled_base_pose_ignores_ref_frame(self) -> None:
        asset = _asset(
            additiveBasePoseType=2,
            basePoseTypeName="AnimationScaled",
            basePose={
                "refSequenceResolved": True,
                "skeletonCompatible": True,
                "frameCount": 30,
                "refFrameValid": False,
            },
        )
        self.assertEqual(classify_additive_animation(asset), "additive-valid")

    def test_unsupported_composite(self) -> None:
        self.assertEqual(
            classify_additive_animation(_asset(status="not-an-animation-sequence")),
            "unsupported-composite",
        )

    def test_load_failed(self) -> None:
        self.assertEqual(classify_additive_animation(_asset(status="missing-skeleton")), "load-failed")

    def test_feasible_only_for_valid(self) -> None:
        self.assertTrue(combined_evaluation_feasible("additive-valid"))
        self.assertFalse(combined_evaluation_feasible("non-additive"))
        self.assertFalse(combined_evaluation_feasible("additive-missing-base-pose"))
        self.assertFalse(combined_evaluation_feasible("additive-base-pose-skeleton-mismatch"))

    def test_build_item_includes_classification_fields(self) -> None:
        item = build_additive_diagnosis_item(_asset())
        self.assertEqual(item["classification"], "additive-valid")
        self.assertTrue(item["combinedEvaluationFeasible"])
        self.assertTrue(item["suggestedNextStep"])

    def test_classifications_are_stable(self) -> None:
        self.assertIsInstance(ADDITIVE_CLASSIFICATIONS, tuple)
        self.assertIn("additive-valid", ADDITIVE_CLASSIFICATIONS)


if __name__ == "__main__":
    unittest.main()

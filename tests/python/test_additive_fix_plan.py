from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.additive_fix_plan import (  # noqa: E402
    FIXABLE_FIELDS,
    build_additive_fix_plan,
)


def _item(**overrides: object) -> dict[str, object]:
    item: dict[str, object] = {
        "assetPath": "/Game/Animations/A_Add.A_Add",
        "status": "success",
        "additiveTypeName": "LocalSpaceBase",
        "basePoseTypeName": "AnimationFrame",
        "additiveRefFrameIndex": 99,
        "additiveRefSequencePath": "/Game/Animations/A_Add.A_Add",
        "basePose": {
            "refSequenceResolved": True,
            "skeletonCompatible": True,
            "frameCount": 30,
            "refFrameValid": False,
        },
        "classification": "additive-base-pose-ref-frame-invalid",
    }
    item.update(overrides)
    return item


class AdditiveFixPlanTests(unittest.TestCase):
    def test_no_fix_for_non_additive(self) -> None:
        plan = build_additive_fix_plan(_item(classification="non-additive", additiveTypeName="None"))
        self.assertFalse(plan["fixesNeeded"])
        self.assertEqual(plan["fixItems"], [])

    def test_no_fix_for_valid_additive(self) -> None:
        plan = build_additive_fix_plan(_item(classification="additive-valid"))
        self.assertFalse(plan["fixesNeeded"])

    def test_ref_frame_invalid_self_referential_requires_user_selection(self) -> None:
        plan = build_additive_fix_plan(_item())
        self.assertTrue(plan["fixesNeeded"])
        self.assertTrue(plan["referenceMutationRequired"])
        self.assertTrue(plan["requiresUserReview"])
        self.assertFalse(plan["autoApplyAllowed"])
        self.assertFalse(plan["compositeRebuildRequired"])
        fields = {entry["field"] for entry in plan["fixItems"]}
        self.assertEqual(fields, {"additiveRefFrameIndex", "additiveRefSequencePath"})
        frame_fix = next(e for e in plan["fixItems"] if e["field"] == "additiveRefFrameIndex")
        self.assertTrue(frame_fix["canAutoDerive"])
        self.assertFalse(frame_fix["requiresUserSelection"])
        self.assertEqual(frame_fix["proposedValue"], 0)
        ref_fix = next(e for e in plan["fixItems"] if e["field"] == "additiveRefSequencePath")
        self.assertFalse(ref_fix["canAutoDerive"])
        self.assertTrue(ref_fix["requiresUserSelection"])

    def test_ref_frame_invalid_non_self_referential_is_frame_only(self) -> None:
        plan = build_additive_fix_plan(
            _item(
                additiveRefSequencePath="/Game/Animations/A_Base.A_Base",
                basePose={"refSequenceResolved": True, "skeletonCompatible": True, "frameCount": 30, "refFrameValid": False},
            )
        )
        self.assertTrue(plan["fixesNeeded"])
        self.assertFalse(plan["referenceMutationRequired"])
        self.assertEqual([e["field"] for e in plan["fixItems"]], ["additiveRefFrameIndex"])

    def test_local_anim_frame_self_reference_is_not_flagged(self) -> None:
        plan = build_additive_fix_plan(
            _item(
                basePoseTypeName="LocalAnimationFrame",
                additiveRefSequencePath="/Game/Animations/A_Add.A_Add",
            )
        )
        self.assertFalse(plan["referenceMutationRequired"])
        self.assertEqual([e["field"] for e in plan["fixItems"]], ["additiveRefFrameIndex"])

    def test_missing_base_pose_requires_reference_selection(self) -> None:
        plan = build_additive_fix_plan(
            _item(
                classification="additive-missing-base-pose",
                additiveRefSequencePath="",
                basePose={"refSequenceResolved": False},
            )
        )
        self.assertTrue(plan["fixesNeeded"])
        self.assertTrue(plan["referenceMutationRequired"])
        self.assertEqual([e["field"] for e in plan["fixItems"]], ["additiveRefSequencePath"])

    def test_skeleton_mismatch_requires_reference_selection(self) -> None:
        plan = build_additive_fix_plan(
            _item(
                classification="additive-base-pose-skeleton-mismatch",
                additiveRefSequencePath="/Game/Other/A_Base.A_Base",
                basePose={"refSequenceResolved": True, "skeletonCompatible": False, "frameCount": 30, "refFrameValid": True},
            )
        )
        self.assertTrue(plan["referenceMutationRequired"])
        self.assertEqual([e["field"] for e in plan["fixItems"]], ["additiveRefSequencePath"])

    def test_unresolvable_classifications_have_no_fix(self) -> None:
        for classification in ("unsupported-composite", "load-failed"):
            with self.subTest(classification=classification):
                plan = build_additive_fix_plan(_item(classification=classification))
                self.assertFalse(plan["fixesNeeded"])

    def test_fixable_fields_are_stable(self) -> None:
        self.assertIsInstance(FIXABLE_FIELDS, tuple)
        self.assertIn("additiveRefSequencePath", FIXABLE_FIELDS)
        self.assertIn("additiveRefFrameIndex", FIXABLE_FIELDS)


if __name__ == "__main__":
    unittest.main()

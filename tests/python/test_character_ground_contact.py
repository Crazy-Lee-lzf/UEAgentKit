from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.character_ground_contact import (  # noqa: E402
    GROUND_CONTACT_CLASSIFICATIONS,
    build_ground_contact_item,
    classify_ground_contact,
)


def _result() -> dict[str, object]:
    return {
        "character": {
            "path": "/Game/C/BP_C.BP_C",
            "classPath": "/Game/C/BP_C.BP_C_C",
            "capsule": {
                "present": True,
                "radius": 34.0,
                "halfHeight": 88.0,
                "relativeLocation": {"x": 0.0, "y": 0.0, "z": -88.0},
            },
            "mesh": {
                "present": True,
                "skeletalMeshPath": "/Game/SK.SK",
                "skeletonPath": "/Game/SK_Skel.SK_Skel",
                "relativeTransform": {
                    "translation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {},
                    "scale": {},
                },
            },
        },
        "boneNames": {
            "root": "Root",
            "pelvis": "Bip001Pelvis",
            "leftFoot": "Bip001LFoot",
            "rightFoot": "Bip001RFoot",
        },
        "skeletonReference": {
            "status": "evaluated",
            "bones": [
                {"bone": "Root", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0}},
                {"bone": "Bip001Pelvis", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 88.0}},
                {"bone": "Bip001LFoot", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0}},
                {"bone": "Bip001RFoot", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0}},
            ],
        },
        "animation": {
            "path": "/Game/A/A.A",
            "status": "evaluated",
            "skeletonCompatible": True,
            "rootMotionZ": 0.0,
            "samples": [
                {
                    "fraction": 0.0,
                    "leftFootToCapsuleBottom": 0.0,
                    "rightFootToCapsuleBottom": 0.0,
                    "bones": [
                        {"bone": "Root", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0}},
                        {"bone": "Bip001Pelvis", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 88.0}},
                        {"bone": "Bip001LFoot", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0}},
                        {"bone": "Bip001RFoot", "boneExists": True, "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0}},
                    ],
                }
            ],
        },
    }


class GroundContactClassifyTests(unittest.TestCase):
    def test_missing_character_is_insufficient(self) -> None:
        result = _result()
        result["character"] = {}
        self.assertEqual(classify_ground_contact(result), "insufficient-context")

    def test_missing_capsule_is_insufficient(self) -> None:
        result = _result()
        result["character"]["capsule"]["present"] = False  # type: ignore[index]
        self.assertEqual(classify_ground_contact(result), "insufficient-context")

    def test_mesh_offset(self) -> None:
        result = _result()
        result["character"]["mesh"]["relativeTransform"]["translation"]["z"] = 20.0  # type: ignore[index]
        self.assertEqual(classify_ground_contact(result), "mesh-offset-candidate")

    def test_foot_ik_needed(self) -> None:
        result = _result()
        result["animation"]["samples"][0]["leftFootToCapsuleBottom"] = 20.0  # type: ignore[index]
        self.assertEqual(classify_ground_contact(result), "foot-ik-needed")

    def test_animation_root_z_candidate(self) -> None:
        result = _result()
        result["animation"]["samples"][0]["leftFootToCapsuleBottom"] = 20.0  # type: ignore[index]
        result["animation"]["rootMotionZ"] = 50.0  # type: ignore[index]
        self.assertEqual(classify_ground_contact(result), "animation-root-z-candidate")

    def test_pelvis_offset_candidate(self) -> None:
        result = _result()
        result["animation"]["samples"][0]["leftFootToCapsuleBottom"] = 20.0  # type: ignore[index]
        result["animation"]["samples"][0]["bones"][1]["componentLocation"]["z"] = 100.0  # type: ignore[index]
        self.assertEqual(classify_ground_contact(result), "pelvis-offset-candidate")

    def test_capsule_size_candidate(self) -> None:
        # Capsule bottom (-88 - 88 = -176) sits far below the foot (z=0) would be
        # "floating"; instead raise the capsule so the foot pokes below it.
        result = _result()
        result["character"]["capsule"]["relativeLocation"]["z"] = 200.0  # type: ignore[index]
        result["character"]["capsule"]["halfHeight"] = 88.0  # type: ignore[index]
        # capsule bottom = 200 - 88 = 112; foot at 0 -> foot 112cm below capsule bottom.
        self.assertEqual(classify_ground_contact(result), "capsule-size-candidate")

    def test_no_animation_ref_pose_grounded(self) -> None:
        result = _result()
        result["animation"]["status"] = "skipped-no-animation"  # type: ignore[index]
        # Ref foot at z=0, capsule bottom -176 -> foot well above capsule bottom (floating).
        self.assertEqual(classify_ground_contact(result), "foot-ik-needed")

    def test_build_item_includes_classification(self) -> None:
        item = build_ground_contact_item(_result())
        self.assertIn(item["classification"], GROUND_CONTACT_CLASSIFICATIONS)
        self.assertTrue(item["suggestedNextStep"])

    def test_classifications_are_stable(self) -> None:
        self.assertIsInstance(GROUND_CONTACT_CLASSIFICATIONS, tuple)
        for name in (
            "mesh-offset-candidate",
            "capsule-size-candidate",
            "animation-root-z-candidate",
            "pelvis-offset-candidate",
            "foot-ik-needed",
            "insufficient-context",
        ):
            self.assertIn(name, GROUND_CONTACT_CLASSIFICATIONS)


if __name__ == "__main__":
    unittest.main()

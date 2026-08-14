from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.skeletal_secondary_motion import (  # noqa: E402
    SECONDARY_MOTION_CLASSIFICATIONS,
    build_secondary_motion_item,
    classify_secondary_motion,
)


def _result() -> dict[str, object]:
    return {
        "skeletalMesh": {
            "path": "/Game/SK/SK.SK",
            "skeletonPath": "/Game/SK/SK_Skel.SK_Skel",
            "physicsAssetPath": "",
            "lodCount": 1,
            "vertexCount": 1024,
            "maxBoneInfluences": 4,
            "hasSkinWeights": True,
        },
        "skeleton": {
            "boneCount": 3,
            "rootBoneName": "Root",
            "bones": [
                {"index": 0, "name": "Root", "parentIndex": -1, "parentName": ""},
                {"index": 1, "name": "Bip001Pelvis", "parentIndex": 0, "parentName": "Root"},
                {"index": 2, "name": "tail_01", "parentIndex": 1, "parentName": "Bip001Pelvis"},
            ],
        },
        "physics": {"present": False, "path": "", "bodyCount": 0, "constraintCount": 0},
        "cloth": {"assetCount": 0, "assets": []},
        "animation": {
            "path": "/Game/A/A.A",
            "status": "evaluated",
            "skeletonCompatible": True,
            "animatedBoneCount": 3,
            "totalBoneCount": 3,
        },
        "animationBlueprint": {
            "path": "/Game/ABP/ABP.ABP",
            "status": "evaluated",
            "secondaryMotionNodeCount": 1,
            "springBoneCount": 0,
            "rigidBodyCount": 0,
            "animDynamicsCount": 1,
            "nodes": [{"title": "Anim Dynamics 'tail_01'", "className": "AnimGraphNode_AnimDynamics"}],
        },
    }


class SecondaryMotionClassifyTests(unittest.TestCase):
    def test_missing_bones(self) -> None:
        result = _result()
        result["skeleton"]["boneCount"] = 0  # type: ignore[index]
        self.assertEqual(classify_secondary_motion(result), "missing-bones")

    def test_missing_skin_weights(self) -> None:
        result = _result()
        result["skeletalMesh"]["hasSkinWeights"] = False  # type: ignore[index]
        self.assertEqual(classify_secondary_motion(result), "missing-skin-weights")

    def test_no_animation_tracks(self) -> None:
        result = _result()
        result["animation"]["animatedBoneCount"] = 0  # type: ignore[index]
        self.assertEqual(classify_secondary_motion(result), "no-animation-tracks")

    def test_no_secondary_motion_node(self) -> None:
        result = _result()
        result["animationBlueprint"]["secondaryMotionNodeCount"] = 0  # type: ignore[index]
        self.assertEqual(classify_secondary_motion(result), "no-secondary-motion-node")

    def test_no_physics_bodies(self) -> None:
        result = _result()
        # No cloth, no AnimBP nodes, no physics bodies.
        result["animationBlueprint"]["secondaryMotionNodeCount"] = 0  # type: ignore[index]
        result["animationBlueprint"]["status"] = "skipped-no-animation-blueprint"  # type: ignore[index]
        self.assertEqual(classify_secondary_motion(result), "no-physics-bodies")

    def test_cloth_data_present(self) -> None:
        result = _result()
        result["cloth"]["assetCount"] = 1  # type: ignore[index]
        result["cloth"]["assets"] = [  # type: ignore[index]
            {"name": "ClothA", "className": "ChaosClothingAsset", "numLods": 1}
        ]
        self.assertEqual(classify_secondary_motion(result), "cloth-data-present")

    def test_cloth_binding_incomplete(self) -> None:
        result = _result()
        result["skeletalMesh"]["lodCount"] = 3  # type: ignore[index]
        result["cloth"]["assetCount"] = 1  # type: ignore[index]
        result["cloth"]["assets"] = [  # type: ignore[index]
            {"name": "ClothA", "className": "ChaosClothingAsset", "numLods": 1}
        ]
        self.assertEqual(classify_secondary_motion(result), "cloth-binding-incomplete")

    def test_cloth_data_missing_fallback(self) -> None:
        result = _result()
        # No cloth, but physics bodies present and no AnimBP input -> not "no-physics-bodies".
        result["physics"]["bodyCount"] = 4  # type: ignore[index]
        result["animationBlueprint"]["status"] = "skipped-no-animation-blueprint"  # type: ignore[index]
        result["animationBlueprint"]["secondaryMotionNodeCount"] = 0  # type: ignore[index]
        self.assertEqual(classify_secondary_motion(result), "cloth-data-missing")

    def test_build_item_includes_classification(self) -> None:
        item = build_secondary_motion_item(_result())
        self.assertIn(item["classification"], SECONDARY_MOTION_CLASSIFICATIONS)
        self.assertTrue(item["suggestedNextStep"])

    def test_classifications_are_stable(self) -> None:
        self.assertIsInstance(SECONDARY_MOTION_CLASSIFICATIONS, tuple)
        for name in (
            "missing-bones",
            "missing-skin-weights",
            "no-animation-tracks",
            "no-secondary-motion-node",
            "no-physics-bodies",
            "cloth-data-present",
            "cloth-data-missing",
            "cloth-binding-incomplete",
        ):
            self.assertIn(name, SECONDARY_MOTION_CLASSIFICATIONS)


if __name__ == "__main__":
    unittest.main()

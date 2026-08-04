from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))



_REQUIRED_CHAIN_BONES = {
    "Root": ("Root", "Root"),
    "Spine": ("Spine1", "Spine3"),
    "Neck": ("Neck", "Neck"),
    "Head": ("Head", "Head"),
    "LeftArm": ("LeftUpperArm", "LeftHand"),
    "RightArm": ("RightUpperArm", "RightHand"),
    "LeftLeg": ("LeftThigh", "LeftFoot"),
    "RightLeg": ("RightThigh", "RightFoot"),
}


def _full_required_analysis(extra: list[dict[str, object]] | None = None) -> dict[str, object]:
    reports = [
        _candidate(chain, *bones, 0.95, side=("Left" if chain.startswith("Left") else "Right" if chain.startswith("Right") else "Center"))
        for chain, bones in _REQUIRED_CHAIN_BONES.items()
    ]
    if extra:
        reports.extend(extra)
    return {"chainCandidates": reports}


from ue_agent_kit.retarget_models import (  # noqa: E402
    HIGH_RISK_BONE_KEYWORDS,
    build_chain_mappings,
    build_retarget_plan,
    pick_retargeter_name,
    plan_digest,
    select_chains_from_analysis,
)


REQUIRED_CHAIN_NAMES_TEST = {
    "Root",
    "Spine",
    "Neck",
    "Head",
    "LeftArm",
    "RightArm",
    "LeftLeg",
    "RightLeg",
}


def _candidate(chain: str, start: str, end: str, confidence: float, *, side: str = "Center") -> dict[str, object]:
    return {
        "chain": chain,
        "required": "required" if chain in REQUIRED_CHAIN_NAMES_TEST else "optional",
        "ambiguous": False,
        "candidates": [
            {
                "startBone": start,
                "endBone": end,
                "side": side,
                "confidence": confidence,
                "nameScore": confidence,
                "hierarchyScore": 1.0,
                "sideScore": 1.0,
                "positionScore": 1.0,
                "lengthScore": 1.0,
                "parentContextScore": 1.0,
            }
        ],
    }


class ChainSelectionTests(unittest.TestCase):
    def test_high_confidence_required_and_optional_are_selected(self) -> None:
        analysis = _full_required_analysis(
            [_candidate("LeftClavicle", "LeftClavicle", "LeftClavicle", 0.91, side="Left")]
        )
        chains, warnings, blocking = select_chains_from_analysis(analysis, include_optional=True)
        self.assertIn("LeftArm", [chain["chain"] for chain in chains])
        self.assertIn("LeftClavicle", [chain["chain"] for chain in chains])
        self.assertEqual(blocking, [])
        self.assertEqual(warnings, [])

    def test_low_confidence_required_is_blocking(self) -> None:
        analysis = _full_required_analysis()
        analysis["chainCandidates"][1]["candidates"][0]["confidence"] = 0.60
        chains, _, blocking = select_chains_from_analysis(analysis, include_optional=True)
        self.assertNotIn("Spine", [chain["chain"] for chain in chains])
        self.assertTrue(any("Spine" in issue for issue in blocking))

    def test_ambiguous_chain_is_blocking(self) -> None:
        analysis = _full_required_analysis()
        analysis["chainCandidates"][2]["ambiguous"] = True
        _, _, blocking = select_chains_from_analysis(analysis, include_optional=True)
        self.assertTrue(any("Neck" in issue for issue in blocking))

    def test_medium_confidence_requires_review_warning(self) -> None:
        analysis = _full_required_analysis()
        analysis["chainCandidates"][3]["candidates"][0]["confidence"] = 0.82
        _, warnings, _ = select_chains_from_analysis(analysis, include_optional=True)
        self.assertTrue(any("Head" in warning for warning in warnings))

    def test_high_risk_accessory_chain_is_never_mapped(self) -> None:
        for keyword in HIGH_RISK_BONE_KEYWORDS:
            analysis = _full_required_analysis()
            analysis["chainCandidates"][1]["candidates"][0]["startBone"] = f"spine_{keyword}"
            chains, _, blocking = select_chains_from_analysis(analysis, include_optional=True)
            self.assertNotIn("Spine", [chain["chain"] for chain in chains], keyword)
            self.assertTrue(any("Spine" in issue for issue in blocking), keyword)

    def test_optional_excluded_when_not_requested(self) -> None:
        analysis = _full_required_analysis(
            [_candidate("LeftThumb", "LeftThumb", "LeftThumb", 0.95, side="Left")]
        )
        chains, _, _ = select_chains_from_analysis(analysis, include_optional=False)
        self.assertEqual(len(chains), 8)
        self.assertNotIn("LeftThumb", [chain["chain"] for chain in chains])

    def test_digest_stability(self) -> None:
        plan = build_retarget_plan(
            plan_id="plan_x",
            project_id="p",
            editor_session_id="s",
            created_at_utc="2026-01-01T00:00:00Z",
            source_mesh="/Game/A/A.A",
            target_mesh="/Game/B/B.B",
            chain_profile="humanoid-v1",
            analysis={},
            source_rig_name="IKRig_A",
            target_rig_name="IKRig_B",
            source_retarget_root="Root",
            target_retarget_root="Root",
            source_chains=[{"chain": "Root", "required": "required", "side": "Center", "startBone": "Root", "endBone": "Root"}],
            target_chains=[],
            revisions={"sourceMesh": "sha256:a"},
            affected_assets=["/Game/A/A.A"],
            warnings=[],
            blocking_issues=[],
            output_directory="/Game/Retargeted",
        )
        first = plan_digest(plan)
        second = plan_digest(plan)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("sha256:"))
        self.assertEqual(plan["confirmationText"], "APPLY RETARGET SETUP plan_x")
        self.assertEqual(plan["schemaVersion"], "retarget-plan-v1")


class ChainMappingTests(unittest.TestCase):
    def _source_chains(self, names: list[str]) -> list[dict[str, str]]:
        return [
            {"chain": name, "required": "required" if name in REQUIRED_CHAIN_NAMES_TEST else "optional",
             "side": "Center", "startBone": name, "endBone": name}
            for name in names
        ]

    def test_maps_chains_present_on_both_sides(self) -> None:
        source = self._source_chains(["Root", "Spine", "Head"])
        target = self._source_chains(["Root", "Spine", "Head", "LeftHand"])
        mappings = build_chain_mappings(source, target)
        self.assertEqual(
            {(m["targetChain"], m["sourceChain"]) for m in mappings},
            {("Root", "Root"), ("Spine", "Spine"), ("Head", "Head")},
        )

    def test_skips_target_only_chains(self) -> None:
        source = self._source_chains(["Root", "Spine"])
        target = self._source_chains(["Root", "Spine", "LeftHand"])
        mappings = build_chain_mappings(source, target)
        self.assertNotIn("LeftHand", [m["targetChain"] for m in mappings])

    def test_required_flag_is_carried(self) -> None:
        source = self._source_chains(["Root", "LeftHand"])
        target = self._source_chains(["Root", "LeftHand"])
        mappings = build_chain_mappings(source, target)
        by_name = {m["targetChain"]: m for m in mappings}
        self.assertEqual(by_name["Root"]["required"], "Required")
        self.assertEqual(by_name["LeftHand"]["required"], "Optional")

    def test_pick_retargeter_name(self) -> None:
        self.assertEqual(
            pick_retargeter_name("/Game/A/SKM_Manny.SKM_Manny", "/Game/B/SK_XinYueHu.SK_XinYueHu"),
            "IKRetargeter_SKM_Manny_to_SK_XinYueHu",
        )


if __name__ == "__main__":
    unittest.main()

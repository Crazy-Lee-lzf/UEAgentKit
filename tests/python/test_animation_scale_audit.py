from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.animation_scale_audit import AnimationScaleAuditService  # noqa: E402

ANIMATION_A = "/Game/Animations/A_Idle.A_Idle"
ANIMATION_B = "/Game/Animations/A_Walk.A_Walk"


def _diagnosis(
    asset_path: str,
    *,
    final_scale: float = 1.0,
    reference_scale: float = 100.0,
    track_scale: float = 1.0,
    force_root_lock: bool = False,
    enable_root_motion: bool = False,
    additive_type: int = 0,
    preview_status: str = "success",
) -> dict[str, object]:
    return {
        "assetPath": asset_path,
        "status": "success",
        "loadedBefore": False,
        "loadedByBridge": True,
        "skeletonPath": "/Game/Characters/Test/SK_Test_Skeleton.SK_Test_Skeleton",
        "additiveAnimType": additive_type,
        "additiveBasePoseType": 0,
        "additiveRefSequencePath": "",
        "enableRootMotion": enable_root_motion,
        "forceRootLock": force_root_lock,
        "useNormalizedRootMotionScale": True,
        "rootMotionRootLock": 0,
        "previewEvaluationStatus": preview_status,
        "previewMeshPath": "/Game/Characters/Test/SK_Test.SK_Test",
        "tracks": [
            {
                "bone": "Root",
                "boneExists": True,
                "trackExists": True,
                "referenceComponentScale": {"x": reference_scale, "y": reference_scale, "z": reference_scale},
                "firstScale": {"x": track_scale, "y": track_scale, "z": track_scale},
                "compressedFirstScale": {"x": track_scale, "y": track_scale, "z": track_scale},
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
                        "componentScale": {"x": final_scale, "y": final_scale, "z": final_scale},
                        "componentLocation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    },
                    {
                        "bone": "pelvis",
                        "boneExists": True,
                        "componentScale": {"x": final_scale, "y": final_scale, "z": final_scale},
                        "componentLocation": {"x": 0.0, "y": 0.0, "z": 90.0},
                    },
                ],
            }
            for fraction in (0.0, 0.5, 1.0)
        ],
    }


def _service() -> tuple[AnimationScaleAuditService, MagicMock]:
    live = MagicMock()
    live.status.return_value = {
        "state": "available",
        "sessionId": "session-1",
    }
    return AnimationScaleAuditService(live), live


class AnimationScaleAuditTests(unittest.TestCase):
    def test_root_lock_candidate_is_classified_from_bounded_diagnosis(self) -> None:
        service, live = _service()
        started = service.start(animation_paths=[ANIMATION_A], load_if_needed=True)
        task_id = str(started["taskId"])
        live.call_tool.return_value = {
            "ok": True,
            "result": {"assets": [_diagnosis(ANIMATION_A)]},
        }

        result = service.get(task_id=task_id)

        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["progress"]["processedAssets"], 1)
        item = result["details"]["items"][0]
        self.assertEqual(item["classification"], "root-lock-candidate")
        self.assertEqual(item["rootBone"], "Root")
        self.assertEqual(item["pelvisBone"], "pelvis")
        self.assertEqual(len(item["poseSamples"]), 3)
        live.call_tool.assert_called_once_with(
            "ue_diagnose_animation_scale",
            {
                "animationPaths": [ANIMATION_A],
                "boneNames": ["Root", "pelvis"],
                "loadIfNeeded": True,
            },
        )

    def test_audit_advances_one_bounded_batch_per_get(self) -> None:
        service, live = _service()
        started = service.start(
            animation_paths=[ANIMATION_A, ANIMATION_B],
            batch_size=1,
        )
        task_id = str(started["taskId"])
        live.call_tool.side_effect = [
            {"ok": True, "result": {"assets": [_diagnosis(ANIMATION_A, final_scale=100.0)]}},
            {"ok": True, "result": {"assets": [_diagnosis(ANIMATION_B, final_scale=100.0)]}},
        ]

        first = service.get(task_id=task_id)
        second = service.get(task_id=task_id)

        self.assertEqual(first["state"], "running")
        self.assertEqual(first["progress"]["processedAssets"], 1)
        self.assertEqual(second["state"], "completed")
        self.assertEqual(second["progress"]["processedAssets"], 2)
        self.assertEqual(second["summary"]["classificationCounts"], {"normal": 2})
        self.assertEqual(live.call_tool.call_count, 2)

    def test_additive_is_never_suggested_for_direct_scale_repair(self) -> None:
        service, live = _service()
        started = service.start(animation_paths=[ANIMATION_A])
        live.call_tool.return_value = {
            "ok": True,
            "result": {
                "assets": [
                    _diagnosis(
                        ANIMATION_A,
                        additive_type=1,
                        preview_status="unsupported-additive-requires-base-pose",
                    )
                ]
            },
        }

        result = service.get(task_id=str(started["taskId"]))

        item = result["details"]["items"][0]
        self.assertEqual(item["classification"], "additive-requires-base-pose")
        self.assertIn("Base Pose", item["suggestedFix"])

    def test_cancelled_task_does_not_touch_editor(self) -> None:
        service, live = _service()
        started = service.start(animation_paths=[ANIMATION_A, ANIMATION_B])
        task_id = str(started["taskId"])

        cancelled = service.cancel(task_id=task_id)
        result = service.get(task_id=task_id)

        self.assertEqual(cancelled["state"], "cancelled")
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual(result["progress"]["processedAssets"], 0)
        live.call_tool.assert_not_called()

    def test_editor_session_change_fails_without_processing_more_assets(self) -> None:
        service, live = _service()
        started = service.start(animation_paths=[ANIMATION_A])
        live.status.return_value = {"state": "available", "sessionId": "session-2"}

        result = service.get(task_id=str(started["taskId"]))

        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errorCode"], "animation-scale-audit-session-invalidated")
        self.assertEqual(result["progress"]["processedAssets"], 0)
        live.call_tool.assert_not_called()


if __name__ == "__main__":
    unittest.main()

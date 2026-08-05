from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.retarget_workflow import RetargetWorkflowMixin  # noqa: E402
from ue_agent_kit.retarget_models import CONFIRMATION_PREFIX  # noqa: E402

SOURCE_MESH = "/Game/Characters/Source/SK_Source.SK_Source"
TARGET_MESH = "/Game/Characters/Target/SK_Target.SK_Target"


class _StubService(RetargetWorkflowMixin):
    def __init__(self, *, policy: dict[str, object], project_path: Path) -> None:
        self.config = MagicMock()
        self.config.project_path = project_path
        self.config.project_name = "HostProject"
        self.config.commit_enabled = True
        self.config.policy_path = project_path / "policy.json"
        self.config.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self._lock = _Lock()
        self._plans: dict[str, object] = {}
        self.live_editor_service = MagicMock()
        self._retarget_plans = {}
        self.project_name = "HostProject"

    def _assert_policy_unchanged(self) -> None:
        return None

    @staticmethod
    def _package_file(project_path: Path, package_name: str, asset_class: str) -> Path:
        del asset_class
        relative_parts = [part for part in package_name[len("/Game/") :].split("/") if part]
        return (project_path / "Content").joinpath(*relative_parts).with_suffix(".uasset")


class _Lock:
    def __enter__(self) -> "_Lock":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _analysis_payload() -> dict[str, object]:
    return {
        "action": "plan-animation-retarget",
        "sourceMesh": SOURCE_MESH,
        "targetMesh": TARGET_MESH,
        "analysis": {
            "compatibility": "compatible",
            "sourceChainCandidates": [
                {
                    "chain": "Root",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "Root",
                            "endBone": "Root",
                            "side": "Center",
                            "confidence": 0.95,
                            "nameScore": 0.95,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "Spine",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "Spine1",
                            "endBone": "Spine3",
                            "side": "Center",
                            "confidence": 0.93,
                            "nameScore": 0.93,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "LeftArm",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "LeftUpperArm",
                            "endBone": "LeftHand",
                            "side": "Left",
                            "confidence": 0.91,
                            "nameScore": 0.91,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "Neck",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "Neck",
                            "endBone": "Neck",
                            "side": "Center",
                            "confidence": 0.92,
                            "nameScore": 0.92,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "Head",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "Head",
                            "endBone": "Head",
                            "side": "Center",
                            "confidence": 0.95,
                            "nameScore": 0.95,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "RightArm",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "RightUpperArm",
                            "endBone": "RightHand",
                            "side": "Right",
                            "confidence": 0.90,
                            "nameScore": 0.90,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "LeftLeg",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "LeftThigh",
                            "endBone": "LeftFoot",
                            "side": "Left",
                            "confidence": 0.90,
                            "nameScore": 0.90,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "RightLeg",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "RightThigh",
                            "endBone": "RightFoot",
                            "side": "Right",
                            "confidence": 0.90,
                            "nameScore": 0.90,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
            ],
            "chainCandidates": [
                {
                    "chain": "Root",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "Root",
                            "endBone": "Root",
                            "side": "Center",
                            "confidence": 0.95,
                            "nameScore": 0.95,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "Spine",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "pelvis",
                            "endBone": "chest",
                            "side": "Center",
                            "confidence": 0.93,
                            "nameScore": 0.93,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "Neck",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "neck",
                            "endBone": "neck_end",
                            "side": "Center",
                            "confidence": 0.92,
                            "nameScore": 0.92,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "Head",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "head",
                            "endBone": "head",
                            "side": "Center",
                            "confidence": 0.95,
                            "nameScore": 0.95,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "LeftArm",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "upperarm_l",
                            "endBone": "hand_l",
                            "side": "Left",
                            "confidence": 0.91,
                            "nameScore": 0.91,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "RightArm",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "upperarm_r",
                            "endBone": "hand_r",
                            "side": "Right",
                            "confidence": 0.90,
                            "nameScore": 0.90,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "LeftLeg",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "thigh_l",
                            "endBone": "foot_l",
                            "side": "Left",
                            "confidence": 0.90,
                            "nameScore": 0.90,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
                {
                    "chain": "RightLeg",
                    "required": "required",
                    "ambiguous": False,
                    "candidates": [
                        {
                            "startBone": "thigh_r",
                            "endBone": "foot_r",
                            "side": "Right",
                            "confidence": 0.90,
                            "nameScore": 0.90,
                            "hierarchyScore": 1.0,
                            "sideScore": 1.0,
                            "positionScore": 1.0,
                            "lengthScore": 1.0,
                            "parentContextScore": 1.0,
                        }
                    ],
                },
            ],
            "sourceRetargetRootCandidates": ["Root"],
            "targetRetargetRootCandidates": ["Root"],
            "warnings": [],
            "blockingIssues": [],
        },
        "existingAssets": {
            "sourceIKRig": {"exists": False},
            "targetIKRig": {"exists": False},
        },
        "editorSessionId": "session-1",
    }


class RetargetWorkflowTests(unittest.TestCase):
    def _make_service(self) -> tuple[_StubService, Path]:
        temporary = tempfile.TemporaryDirectory(prefix="ueak_rtwf_")
        root = Path(temporary.name)
        service = _StubService(
            policy={
                "retargetCapabilities": [
                    "retarget.plan",
                    "retarget.configure",
                    "retarget.inspect",
                    "retarget.batch",
                ],
                "allowedAssetRoots": ["/Game/Retargeted"],
                "allowedReferenceRoots": ["/Game/Characters/Mannequins"],
            },
            project_path=root,
        )
        self.addCleanup(temporary.cleanup)
        content_dir = root / "Content" / "Characters" / "Source"
        content_dir.mkdir(parents=True, exist_ok=True)
        (content_dir / "SK_Source.uasset").write_bytes(b"source")
        content_dir = root / "Content" / "Characters" / "Target"
        content_dir.mkdir(parents=True, exist_ok=True)
        (content_dir / "SK_Target.uasset").write_bytes(b"target")
        service.live_editor_service.call_method.return_value = _analysis_payload()
        service.live_editor_service._read_descriptor.return_value = {
            "capabilities": [
                "retarget.plan",
                "retarget.configure",
                "retarget.inspect",
                "retarget.batch",
            ]
        }
        return service, root

    def test_plan_creates_immutable_plan_with_digest(self) -> None:
        service, root = self._make_service()
        result = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH,
            target_mesh=TARGET_MESH,
            include_optional_chains=True,
            output_directory="/Game/Retargeted",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["planId"].startswith("plan_"))
        self.assertEqual(result["confirmationText"], f"{CONFIRMATION_PREFIX} {result['planId']}")
        self.assertEqual(result["compatibility"], "compatible")
        self.assertIn("sourceMesh", result["revisions"])
        self.assertTrue(result["revisions"]["sourceMesh"].startswith("sha256:"))
        self.assertEqual(result["blockingIssues"], [])
        self.assertEqual(len(result["sourceChains"]), 8)
        record = service._retarget_plans[result["planId"]]
        self.assertEqual(record.plan["schemaVersion"], "retarget-plan-v1")

    def test_plan_requires_output_directory(self) -> None:
        service, _ = self._make_service()
        result = service.plan_animation_retarget(source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH)
        self.assertTrue(any("outputDirectory" in issue for issue in result["blockingIssues"]))

    def test_plan_blocked_by_required_chain_absence(self) -> None:
        service, _ = self._make_service()
        payload = _analysis_payload()
        payload["analysis"]["sourceChainCandidates"] = []
        service.live_editor_service.call_method.return_value = payload
        result = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH,
            target_mesh=TARGET_MESH,
            output_directory="/Game/Retargeted",
        )
        self.assertTrue(result["blockingIssues"])

    def test_apply_requires_exact_confirmation(self) -> None:
        service, _ = self._make_service()
        result = service.plan_animation_retarget(source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted")
        with self.assertRaises(Exception) as ctx:
            service.apply_animation_retarget_setup(plan_id=result["planId"], confirmation="wrong")
        self.assertEqual(getattr(ctx.exception, "code", ""), "retarget-confirmation-required")
        apply_calls = [call for call in service.live_editor_service.call_method.call_args_list if call.args[0] == "editor.applyAnimationRetargetSetup"]
        self.assertEqual(apply_calls, [])

    def test_apply_rejects_plan_not_found(self) -> None:
        service, _ = self._make_service()
        with self.assertRaises(Exception) as ctx:
            service.apply_animation_retarget_setup(
                plan_id="plan_missing",
                confirmation="APPLY RETARGET SETUP plan_missing",
            )
        self.assertEqual(getattr(ctx.exception, "code", ""), "plan-not-found")

    def test_apply_rejects_revision_conflict(self) -> None:
        service, _ = self._make_service()
        result = service.plan_animation_retarget(source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted")
        source_package = service.config.project_path / "Content" / "Characters" / "Source" / "SK_Source.uasset"
        source_package.write_bytes(b"changed")
        with self.assertRaises(Exception) as ctx:
            service.apply_animation_retarget_setup(
                plan_id=result["planId"],
                confirmation=f"{CONFIRMATION_PREFIX} {result['planId']}",
            )
        self.assertEqual(getattr(ctx.exception, "code", ""), "retarget_revision_conflict")

    def test_apply_blocked_by_plan_issues(self) -> None:
        service, _ = self._make_service()
        result = service.plan_animation_retarget(source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH)
        with self.assertRaises(Exception) as ctx:
            service.apply_animation_retarget_setup(
                plan_id=result["planId"],
                confirmation=f"{CONFIRMATION_PREFIX} {result['planId']}",
            )
        self.assertEqual(getattr(ctx.exception, "code", ""), "retarget-plan-blocked")

    def test_apply_success_returns_changes(self) -> None:
        service, _ = self._make_service()
        result = service.plan_animation_retarget(source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted")
        service.live_editor_service.call_method.return_value = {
            "action": "apply-animation-retarget-setup",
            "sourceMesh": SOURCE_MESH,
            "targetMesh": TARGET_MESH,
            "changes": [
                {
                    "assetPath": "/Game/Characters/Source/IKRig_SK_Source.IKRig_SK_Source",
                    "action": "create",
                    "details": ["required Root (Root..Root)"],
                },
                {
                    "assetPath": "/Game/Characters/Target/IKRig_SK_Target.IKRig_SK_Target",
                    "action": "create",
                    "details": ["required Root (Root..Root)"],
                },
            ],
            "transactionCreated": True,
            "assetDirty": True,
            "editorSessionId": "session-1",
        }
        apply_result = service.apply_animation_retarget_setup(
            plan_id=result["planId"],
            confirmation=f"{CONFIRMATION_PREFIX} {result['planId']}",
        )
        self.assertTrue(apply_result["ok"])
        self.assertTrue(apply_result["changed"])
        self.assertEqual(len(apply_result["changes"]), 2)
        self.assertTrue(apply_result["setupReceipt"].startswith("rtg_"))
        self.assertTrue(apply_result["transactionCreated"])

    def test_apply_passes_retargeter_config_to_bridge(self) -> None:
        service, _ = self._make_service()
        result = service.plan_animation_retarget(source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted")
        service.live_editor_service.call_method.return_value = {
            "action": "apply-animation-retarget-setup",
            "sourceMesh": SOURCE_MESH,
            "targetMesh": TARGET_MESH,
            "changes": [
                {"assetPath": "/Game/Characters/Source/IKRig_SK_Source.IKRig_SK_Source", "action": "no_op", "details": []},
                {"assetPath": "/Game/Characters/Target/IKRig_SK_Target.IKRig_SK_Target", "action": "update", "details": ["required Root"]},
                {"assetPath": "/Game/Characters/Source/IKRetargeter_SK_Source_to_SK_Target.IKRetargeter_SK_Source_to_SK_Target", "action": "create", "details": ["required Root", "pose TargetPose_A"]},
            ],
            "mappingReport": {
                "mappedRequiredChains": ["Root", "Spine", "Neck", "Head"],
                "mappedOptionalChains": [],
                "unmappedSourceChains": [],
                "unmappedTargetChains": [],
                "duplicateMappings": [],
                "mappingConfidence": 1.0,
            },
            "poseApplied": True,
            "poseName": "TargetPose_A",
            "transactionCreated": True,
            "assetDirty": True,
            "editorSessionId": "session-1",
        }
        apply_result = service.apply_animation_retarget_setup(
            plan_id=result["planId"],
            confirmation=f"{CONFIRMATION_PREFIX} {result['planId']}",
            allow_large_pose_offset=True,
        )
        self.assertTrue(apply_result["ok"])
        self.assertTrue(apply_result["changed"])
        self.assertEqual(len(apply_result["changes"]), 3)
        self.assertTrue(apply_result["poseApplied"])
        self.assertEqual(apply_result["poseName"], "TargetPose_A")
        self.assertEqual(apply_result["mappingReport"]["mappedRequiredChains"], ["Root", "Spine", "Neck", "Head"])
        apply_calls = [
            call for call in service.live_editor_service.call_method.call_args_list
            if call.args[0] == "editor.applyAnimationRetargetSetup"
        ]
        self.assertEqual(len(apply_calls), 1)
        sent = apply_calls[0].args[1]
        self.assertEqual(sent["retargeterName"], "IKRetargeter_SK_Source_to_SK_Target")
        self.assertIsInstance(sent["mappings"], list)
        self.assertIn("targetChain", sent["mappings"][0])
        self.assertEqual(sent["pose"]["poseName"], "TargetPose_A")
        self.assertTrue(sent["allowLargePoseOffset"])

    def test_batch_start_queues_and_get_runs_to_completion(self) -> None:
        service, _ = self._make_service()
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        service.live_editor_service.call_method.return_value = {
            "action": "retarget-batch-step",
            "outputs": [
                {"inputPath": "/Game/Characters/Mannequins/Idle", "outputPath": "/Game/Retargeted/Ret_Idle.Ret_Idle"},
                {"inputPath": "/Game/Characters/Mannequins/Walk", "outputPath": "/Game/Retargeted/Ret_Walk.Ret_Walk"},
            ],
            "transactionCreated": True,
            "assetDirty": True,
            "editorSessionId": "session-1",
        }
        started = service.start_animation_retarget_batch(
            plan_id=plan["planId"],
            retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
            source_assets=["/Game/Characters/Mannequins/Idle", "/Game/Characters/Mannequins/Walk"],
            output_directory="/Game/Retargeted",
            naming={"search": "", "replace": "", "prefix": "Ret_", "suffix": ""},
        )
        self.assertTrue(started["ok"])
        self.assertEqual(started["status"], "queued")
        task_id = started["taskId"]
        finished = service.get_animation_retarget_batch(task_id=task_id)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(len(finished["createdAssets"]), 2)
        self.assertEqual(finished["outputs"][0]["outputPath"], "/Game/Retargeted/Ret_Idle.Ret_Idle")
        batch_calls = [
            call for call in service.live_editor_service.call_method.call_args_list
            if call.args[0] == "editor.retargetBatchStep"
        ]
        self.assertEqual(len(batch_calls), 1)
        sent = batch_calls[0].args[1]
        self.assertEqual(sent["outputDirectory"], "/Game/Retargeted")
        self.assertEqual(sent["naming"]["prefix"], "Ret_")
        self.assertTrue(sent["retainAdditiveFlags"])

    def test_batch_cancel_before_start(self) -> None:
        service, _ = self._make_service()
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        started = service.start_animation_retarget_batch(
            plan_id=plan["planId"],
            retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
            source_assets=["/Game/Characters/Mannequins/Idle"],
            output_directory="/Game/Retargeted",
        )
        task_id = started["taskId"]
        cancelled = service.cancel_animation_retarget_batch(task_id=task_id)
        self.assertEqual(cancelled["status"], "cancelled")
        fetched = service.get_animation_retarget_batch(task_id=task_id)
        self.assertEqual(fetched["status"], "cancelled")

    def test_batch_rejects_uncovered_output_directory(self) -> None:
        service, _ = self._make_service()
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        with self.assertRaises(Exception) as ctx:
            service.start_animation_retarget_batch(
                plan_id=plan["planId"],
                retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
                source_assets=["/Game/Characters/Mannequins/Idle"],
                output_directory="/Game/Uncovered",
            )
        self.assertEqual(getattr(ctx.exception, "code", ""), "retarget_output_path_denied")

    def test_batch_rejects_source_outside_reference_roots(self) -> None:
        service, _ = self._make_service()
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        with self.assertRaises(Exception) as ctx:
            service.start_animation_retarget_batch(
                plan_id=plan["planId"],
                retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
                source_assets=["/Game/Unrelated/Anim"],
                output_directory="/Game/Retargeted",
            )
        self.assertEqual(getattr(ctx.exception, "code", ""), "retarget-batch-invalid")

    def test_batch_save_persists_created_outputs(self) -> None:
        service, _ = self._make_service()
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        service.live_editor_service.call_method.return_value = {
            "action": "retarget-batch-step",
            "outputs": [
                {"inputPath": "/Game/Characters/Mannequins/Idle", "outputPath": "/Game/Retargeted/RTG_Idle.RTG_Idle"},
                {"inputPath": "/Game/Characters/Mannequins/Walk", "outputPath": "/Game/Retargeted/RTG_Walk.RTG_Walk"},
            ],
            "transactionCreated": True,
            "assetDirty": True,
            "editorSessionId": "session-1",
        }
        started = service.start_animation_retarget_batch(
            plan_id=plan["planId"],
            retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
            source_assets=["/Game/Characters/Mannequins/Idle", "/Game/Characters/Mannequins/Walk"],
            output_directory="/Game/Retargeted",
        )
        task_id = started["taskId"]
        finished = service.get_animation_retarget_batch(task_id=task_id)
        self.assertEqual(finished["status"], "completed")
        service.live_editor_service.call_method.return_value = {
            "assetPath": "/Game/Retargeted/RTG_Idle.RTG_Idle",
            "saved": True,
            "editorSessionId": "session-1",
        }
        saved = service.save_animation_retarget_batch(
            task_id=task_id,
            confirmation=f"SAVE RETARGET BATCH {task_id}",
        )
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(len(saved["savedAssets"]), 2)
        self.assertTrue(saved["saveReceipts"])
        save_calls = [
            call for call in service.live_editor_service.call_method.call_args_list
            if call.args[0] == "editor.saveAuthorizedAsset"
        ]
        self.assertEqual(len(save_calls), 2)

    def test_batch_save_requires_exact_confirmation(self) -> None:
        service, _ = self._make_service()
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        service.live_editor_service.call_method.return_value = {
            "action": "retarget-batch-step",
            "outputs": [{"inputPath": "/Game/Characters/Mannequins/Idle", "outputPath": "/Game/Retargeted/RTG_Idle.RTG_Idle"}],
            "transactionCreated": True,
            "assetDirty": True,
            "editorSessionId": "session-1",
        }
        started = service.start_animation_retarget_batch(
            plan_id=plan["planId"],
            retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
            source_assets=["/Game/Characters/Mannequins/Idle"],
            output_directory="/Game/Retargeted",
        )
        task_id = started["taskId"]
        service.get_animation_retarget_batch(task_id=task_id)
        with self.assertRaises(Exception) as ctx:
            service.save_animation_retarget_batch(task_id=task_id, confirmation="wrong")
        self.assertEqual(getattr(ctx.exception, "code", ""), "retarget-confirmation-required")


if __name__ == "__main__":
    unittest.main()

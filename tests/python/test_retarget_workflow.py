from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.change_sets import ChangeSetRecord  # noqa: E402
from ue_agent_kit.retarget_workflow import RetargetWorkflowMixin, _retarget_output_object_path  # noqa: E402
from ue_agent_kit.retarget_models import CONFIRMATION_PREFIX  # noqa: E402

SOURCE_MESH = "/Game/Characters/Source/SK_Source.SK_Source"
TARGET_MESH = "/Game/Characters/Target/SK_Target.SK_Target"


class _FakeProcessResult:
    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code
        self.stdout = ""
        self.stderr = ""


class _StubService(RetargetWorkflowMixin):
    def __init__(self, *, policy: dict[str, object], project_path: Path) -> None:
        self.config = MagicMock()
        self.config.project_path = project_path
        self.config.project_name = "HostProject"
        self.config.commit_enabled = True
        self.config.policy_path = project_path / "policy.json"
        self.config.policy_path.write_text(json.dumps(policy), encoding="utf-8")
        self.config.backup_root = project_path / "Backups"
        self.config.work_root = project_path / "work"
        self._lock = _Lock()
        self._plans: dict[str, object] = {}
        self.live_editor_service = MagicMock()
        self._retarget_plans = {}
        self._change_sets: dict[str, ChangeSetRecord] = {}
        self.project_name = "HostProject"

    def _assert_policy_unchanged(self) -> None:
        return None

    @staticmethod
    def _package_file(project_path: Path, package_name: str, asset_class: str) -> Path:
        del asset_class
        relative_parts = [part for part in package_name[len("/Game/") :].split("/") if part]
        return (project_path / "Content").joinpath(*relative_parts).with_suffix(".uasset")

    def _current_editor_session(self) -> tuple[bool, str]:
        return True, "session-1"

    def _resolve_change_set(self, change_set_id: str) -> ChangeSetRecord:
        record = self._change_sets.get(change_set_id)
        if record is None:
            raise ValueError("change-set-not-found: " + change_set_id)
        return record

    def _reconcile_change_set(self, record: ChangeSetRecord, *, persist: bool) -> None:
        del persist
        return None

    def _persist_change_set(self, record: ChangeSetRecord) -> bool:
        self._change_sets[record.change_set_id] = record
        return True

    def _run_script(
        self,
        script_name: str,
        script_arguments: list[str],
        *,
        stage: str,
        report_path: Path,
    ) -> _FakeProcessResult:
        del script_name, stage, report_path
        asset = script_arguments[script_arguments.index("-Asset") + 1]
        output = Path(script_arguments[script_arguments.index("-Output") + 1])
        output.mkdir(parents=True, exist_ok=True)
        package_file = self._output_package_file(asset)
        if package_file.is_file():
            revision = "sha256:" + hashlib.sha256(package_file.read_bytes()).hexdigest()
            canonical = {
                "assetPath": asset + "." + asset.rsplit("/", 1)[-1],
                "projectName": "HostProject",
                "assetClass": "/Script/Engine.AnimSequence",
                "revision": {"value": revision, "available": True, "packageDirty": False},
            }
            (output / "canonical").mkdir(parents=True, exist_ok=True)
            (output / "canonical" / (asset.rsplit("/", 1)[-1] + ".json")).write_text(
                json.dumps(canonical), encoding="utf-8"
            )
            manifest = {"projectName": "HostProject", "assetCount": 1, "successCount": 1, "failureCount": 0}
            (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            return _FakeProcessResult(0)
        manifest = {"projectName": "HostProject", "assetCount": 1, "successCount": 0, "failureCount": 1}
        (output / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return _FakeProcessResult(4)


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
                    "retarget.validate",
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
                "retarget.validate",
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

    def test_validate_returns_verdict_and_issues(self) -> None:
        service, _ = self._make_service()
        service.live_editor_service.call_method.return_value = {
            "action": "validate-animation-retarget",
            "retargeter": "/Game/Retargeted/RTG.RTG",
            "verdict": "passed_with_warnings",
            "animationCount": 1,
            "issues": [
                {"level": "warning", "code": "retarget_metadata_missing_curve", "message": "No curves.", "scope": "metadata"}
            ],
            "editorSessionId": "session-1",
        }
        result = service.validate_animation_retarget(
            retargeter="/Game/Retargeted/RTG.RTG",
            animation_paths=["/Game/Retargeted/RTG_Idle.RTG_Idle"],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["verdict"], "passed_with_warnings")
        self.assertEqual(len(result["issues"]), 1)
        validate_calls = [
            call for call in service.live_editor_service.call_method.call_args_list
            if call.args[0] == "editor.validateAnimationRetarget"
        ]
        self.assertEqual(len(validate_calls), 1)
        self.assertEqual(validate_calls[0].args[1]["retargeter"], "/Game/Retargeted/RTG.RTG")

    def test_predict_output_path_matches_engine_naming_rule(self) -> None:
        named = _retarget_output_object_path(
            "/Game/Characters/Mannequins/Idle",
            "/Game/Retargeted",
            {"search": "", "replace": "", "prefix": "RTG_", "suffix": ""},
        )
        self.assertEqual(named, "/Game/Retargeted/RTG_Idle.RTG_Idle")
        replaced = _retarget_output_object_path(
            "/Game/Characters/Mannequins/MF_Unarmed_Walk_Fwd",
            "/Game/Characters/XinYueHu/Animations/Retargeted",
            {"search": "MF_Unarmed", "replace": "XinYueHu", "prefix": "", "suffix": "_XinYueHu"},
        )
        self.assertEqual(
            replaced,
            "/Game/Characters/XinYueHu/Animations/Retargeted/XinYueHu_Walk_Fwd_XinYueHu.XinYueHu_Walk_Fwd_XinYueHu",
        )

    def _completed_overwrite_task(self, service: _StubService, overwrite_path: str, existing: bytes) -> dict[str, Any]:
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        output_package = overwrite_path.rsplit(".", 1)[0]
        output_file = service._output_package_file(output_package)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(existing)
        service.live_editor_service.call_method.return_value = {
            "action": "retarget-batch-step",
            "outputs": [{"inputPath": "/Game/Characters/Mannequins/Idle", "outputPath": overwrite_path}],
            "transactionCreated": True,
            "assetDirty": True,
            "editorSessionId": "session-1",
        }
        started = service.start_animation_retarget_batch(
            plan_id=plan["planId"],
            retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
            source_assets=["/Game/Characters/Mannequins/Idle"],
            output_directory="/Game/Retargeted",
            naming={"prefix": "RTG_", "suffix": "", "search": "", "replace": ""},
            overwrite_existing=True,
        )
        return service.get_animation_retarget_batch(task_id=started["taskId"])

    def test_batch_captures_pre_batch_backup_of_overwrite_target(self) -> None:
        service, root = self._make_service()
        output_path = "/Game/Retargeted/RTG_Idle.RTG_Idle"
        before = b"pre-batch-output"
        finished = self._completed_overwrite_task(service, output_path, before)
        self.assertEqual(finished["status"], "completed")
        manifest = finished["backupManifest"]
        self.assertEqual(len(manifest["entries"]), 1)
        entry = manifest["entries"][0]
        self.assertEqual(entry["kind"], "overwrite")
        self.assertEqual(entry["revision"], "sha256:" + hashlib.sha256(before).hexdigest())
        backup_file = root / "Backups" / "Retarget" / finished["taskId"] / entry["backupRelativePath"]
        self.assertTrue(backup_file.is_file())
        self.assertEqual(backup_file.read_bytes(), before)

    def test_batch_captures_new_output_as_create(self) -> None:
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
            naming={"prefix": "RTG_", "suffix": "", "search": "", "replace": ""},
        )
        finished = service.get_animation_retarget_batch(task_id=started["taskId"])
        self.assertEqual(finished["backupManifest"]["entries"][0]["kind"], "create")

    def test_batch_save_returns_backup_manifest_ref(self) -> None:
        service, _ = self._make_service()
        output_path = "/Game/Retargeted/RTG_Idle.RTG_Idle"
        finished = self._completed_overwrite_task(service, output_path, b"old")
        saved = service.save_animation_retarget_batch(
            task_id=finished["taskId"],
            confirmation=f"SAVE RETARGET BATCH {finished['taskId']}",
        )
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(saved["updatedAssets"], [output_path])
        self.assertEqual(saved["backupManifestRef"], f"backup-manifest:{finished['taskId']}")

    def test_batch_rollback_dry_run_reports_plan(self) -> None:
        service, _ = self._make_service()
        finished = self._completed_overwrite_task(service, "/Game/Retargeted/RTG_Idle.RTG_Idle", b"old")
        dry_run = service.rollback_animation_retarget_batch(task_id=finished["taskId"], mode="DryRun")
        self.assertEqual(dry_run["mode"], "DryRun")
        self.assertEqual(dry_run["restoreCount"], 1)
        self.assertEqual(dry_run["deleteCount"], 0)
        self.assertTrue(dry_run["rollbackDryRunReceipt"].startswith("rtgrb_dry_"))

    def test_batch_rollback_commit_restores_overwrite_and_verifies(self) -> None:
        service, root = self._make_service()
        output_path = "/Game/Retargeted/RTG_Idle.RTG_Idle"
        before = b"pre-batch-output"
        finished = self._completed_overwrite_task(service, output_path, before)
        output_file = service._output_package_file(output_path.rsplit(".", 1)[0])
        output_file.write_bytes(b"retargeted-output")
        service.save_animation_retarget_batch(
            task_id=finished["taskId"],
            confirmation=f"SAVE RETARGET BATCH {finished['taskId']}",
        )
        dry_run = service.rollback_animation_retarget_batch(task_id=finished["taskId"], mode="DryRun")
        committed = service.rollback_animation_retarget_batch(
            task_id=finished["taskId"],
            mode="Commit",
            rollback_dry_run_receipt=dry_run["rollbackDryRunReceipt"],
            confirmation=f"ROLLBACK RETARGET BATCH {finished['taskId']}",
        )
        self.assertTrue(committed["valid"])
        self.assertEqual(committed["restoredCount"], 1)
        self.assertEqual(committed["deletedCount"], 0)
        self.assertEqual(committed["restored"][0]["revision"], "sha256:" + hashlib.sha256(before).hexdigest())
        self.assertEqual(output_file.read_bytes(), before)
        self.assertTrue(committed["independentVerification"][0]["verified"])
        self.assertEqual(committed["memoryTaskEvidence"]["arguments"]["outcome"], "rolledBack")

    def test_batch_rollback_commit_deletes_created_output(self) -> None:
        service, _ = self._make_service()
        output_path = "/Game/Retargeted/RTG_Idle.RTG_Idle"
        # Run the batch without a pre-existing output so the entry is "create".
        plan = service.plan_animation_retarget(
            source_mesh=SOURCE_MESH, target_mesh=TARGET_MESH, output_directory="/Game/Retargeted"
        )
        service.live_editor_service.call_method.return_value = {
            "action": "retarget-batch-step",
            "outputs": [{"inputPath": "/Game/Characters/Mannequins/Idle", "outputPath": output_path}],
            "transactionCreated": True,
            "assetDirty": True,
            "editorSessionId": "session-1",
        }
        started = service.start_animation_retarget_batch(
            plan_id=plan["planId"],
            retargeter="/Game/Retargeted/IKRetargeter_A.IKRetargeter_A",
            source_assets=["/Game/Characters/Mannequins/Idle"],
            output_directory="/Game/Retargeted",
            naming={"prefix": "RTG_", "suffix": "", "search": "", "replace": ""},
        )
        created = service.get_animation_retarget_batch(task_id=started["taskId"])
        output_file = service._output_package_file(output_path.rsplit(".", 1)[0])
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_bytes(b"new-output")
        service.save_animation_retarget_batch(
            task_id=created["taskId"],
            confirmation=f"SAVE RETARGET BATCH {created['taskId']}",
        )
        dry_run = service.rollback_animation_retarget_batch(task_id=created["taskId"], mode="DryRun")
        self.assertEqual(dry_run["deleteCount"], 1)
        committed = service.rollback_animation_retarget_batch(
            task_id=created["taskId"],
            mode="Commit",
            rollback_dry_run_receipt=dry_run["rollbackDryRunReceipt"],
            confirmation=f"ROLLBACK RETARGET BATCH {created['taskId']}",
        )
        self.assertTrue(committed["valid"])
        self.assertEqual(committed["deletedCount"], 1)
        self.assertFalse(output_file.exists())
        self.assertTrue(committed["independentVerification"][0]["verified"])

    def test_batch_verify_independent_reload_matches_disk(self) -> None:
        service, _ = self._make_service()
        output_path = "/Game/Retargeted/RTG_Idle.RTG_Idle"
        finished = self._completed_overwrite_task(service, output_path, b"old")
        service.save_animation_retarget_batch(
            task_id=finished["taskId"],
            confirmation=f"SAVE RETARGET BATCH {finished['taskId']}",
        )
        verified = service.verify_animation_retarget_batch(task_id=finished["taskId"])
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["verifiedCount"], 1)
        self.assertEqual(verified["failures"], [])
        self.assertTrue(verified["memoryTaskEvidence"]["arguments"]["outcome"] == "succeeded")

    def test_save_with_change_set_binds_retarget_save_operations(self) -> None:
        service, _ = self._make_service()
        change_set_id = "cs_" + "a" * 20
        service._change_sets[change_set_id] = ChangeSetRecord(
            change_set_id=change_set_id,
            task_id="task_test",
            editor_session_id="session-1",
            title="Retarget closed loop",
            status="planned",
            created_at_utc="2026-08-05T00:00:00.000Z",
            updated_at_utc="2026-08-05T00:00:00.000Z",
            operations=[],
        )
        output_path = "/Game/Retargeted/RTG_Idle.RTG_Idle"
        finished = self._completed_overwrite_task(service, output_path, b"old")
        saved = service.save_animation_retarget_batch(
            task_id=finished["taskId"],
            confirmation=f"SAVE RETARGET BATCH {finished['taskId']}",
            change_set_id=change_set_id,
        )
        self.assertTrue(saved["changeSetUpdated"])
        self.assertEqual(saved["changeSetId"], change_set_id)
        change_set = service._change_sets[change_set_id]
        self.assertEqual(len(change_set.operations), 1)
        self.assertEqual(change_set.operations[0].operation, "retarget-save")
        self.assertEqual(change_set.operations[0].status, "saved")
        self.assertTrue(change_set.operations[0].receipt.startswith("live_rtsave_"))


if __name__ == "__main__":
    unittest.main()

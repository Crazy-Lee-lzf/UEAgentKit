from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from mcp.server.fastmcp import FastMCP

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.mcp_retarget_tools import register_retarget_tools  # noqa: E402

try:
    from mcp.types import ToolAnnotations
except ImportError:  # pragma: no cover
    ToolAnnotations = None  # type: ignore[assignment,misc]

SOURCE_MESH = "/Game/Characters/Source/SK_Source.SK_Source"
TARGET_MESH = "/Game/Characters/Target/SK_Target.SK_Target"
ANIMATION = "/Game/Animations/A_Idle.A_Idle"


def _read_only_annotations() -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=False,
    )


def _error_response(tool: str, error: Exception, *, read_only: bool) -> dict[str, object]:
    del tool, read_only
    code = error.code if hasattr(error, "code") else "retarget_capability_unavailable"
    return {
        "schemaVersion": "1.0",
        "tool": "ue_analyze_animation_retarget",
        "ok": False,
        "readOnly": True,
        "error": {"code": code, "message": str(error)},
    }


_POLICY_DIRECTORIES: list[tempfile.TemporaryDirectory[str]] = []


def _live_service(policy: dict[str, object] | None) -> MagicMock:
    service = MagicMock()
    service.config = SimpleNamespace(
        project_path=Path("C:/Projects/TestProject/TestProject.uproject"),
        project_name="TestProject",
    )
    if policy is None:
        service.config.policy_path = None
    else:
        temporary = tempfile.TemporaryDirectory(prefix="ueak_retarget_")
        _POLICY_DIRECTORIES.append(temporary)
        policy_path = Path(temporary.name) / "policy.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        service.config.policy_path = policy_path
    return service


@unittest.skipUnless(ToolAnnotations is not None, "optional mcp dependency is not installed")
class RetargetToolTests(unittest.TestCase):
    def tearDown(self) -> None:
        while _POLICY_DIRECTORIES:
            _POLICY_DIRECTORIES.pop().cleanup()

    def test_analyze_tool_rejects_missing_policy_capability(self) -> None:
        service = _live_service({})
        server = FastMCP("probe")
        register_retarget_tools(
            server=server,
            live_editor_service=service,
            read_annotations=_read_only_annotations(),
            error_response=_error_response,
        )
        result = asyncio.run(
            server.call_tool(
                "ue_analyze_animation_retarget",
                {
                    "sourceMesh": SOURCE_MESH,
                    "targetMesh": TARGET_MESH,
                },
            )
        )
        _, payload = result
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "retarget_capability_unavailable")

    def test_analyze_tool_rejects_unlisted_capability(self) -> None:
        service = _live_service({"retargetCapabilities": ["retarget.plan"]})
        server = FastMCP("probe")
        register_retarget_tools(
            server=server,
            live_editor_service=service,
            read_annotations=_read_only_annotations(),
            error_response=_error_response,
        )
        result = asyncio.run(
            server.call_tool(
                "ue_analyze_animation_retarget",
                {
                    "sourceMesh": SOURCE_MESH,
                    "targetMesh": TARGET_MESH,
                },
            )
        )
        _, payload = result
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "retarget_capability_unavailable")

    def test_analyze_tool_passes_authorized_request_through(self) -> None:
        service = _live_service({"retargetCapabilities": ["retarget.inspect"]})
        service.call_tool.return_value = {
            "schemaVersion": "1.0",
            "tool": "ue_analyze_animation_retarget",
            "ok": True,
            "readOnly": True,
            "result": {
                "action": "analyze-animation-retarget",
                "sourceMesh": SOURCE_MESH,
                "targetMesh": TARGET_MESH,
                "analysis": {"compatibility": "compatible"},
                "editorSessionId": "session-1",
            },
        }
        server = FastMCP("probe")
        register_retarget_tools(
            server=server,
            live_editor_service=service,
            read_annotations=_read_only_annotations(),
            error_response=_error_response,
        )
        result = asyncio.run(
            server.call_tool(
                "ue_analyze_animation_retarget",
                {
                    "sourceMesh": SOURCE_MESH,
                    "targetMesh": TARGET_MESH,
                    "includeOptionalChains": False,
                },
            )
        )
        _, payload = result
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["analysis"]["compatibility"], "compatible")
        service.call_tool.assert_called_once_with(
            "ue_analyze_animation_retarget",
            {
                "sourceMesh": SOURCE_MESH,
                "targetMesh": TARGET_MESH,
                "includeOptionalChains": False,
                "maxBoneDetails": 512,
            },
        )


    def test_diagnose_animation_scale_passes_authorized_request_through(self) -> None:
        service = _live_service({"retargetCapabilities": ["retarget.inspect"]})
        service.call_tool.return_value = {
            "schemaVersion": "1.0",
            "tool": "ue_diagnose_animation_scale",
            "ok": True,
            "readOnly": True,
            "result": {
                "action": "diagnose-animation-scale",
                "assets": [
                    {
                        "assetPath": ANIMATION,
                        "previewEvaluationStatus": "success",
                    }
                ],
                "editorSessionId": "session-1",
            },
        }
        server = FastMCP("probe")
        register_retarget_tools(
            server=server,
            live_editor_service=service,
            read_annotations=_read_only_annotations(),
            error_response=_error_response,
        )
        result = asyncio.run(
            server.call_tool(
                "ue_diagnose_animation_scale",
                {
                    "animationPaths": [ANIMATION],
                    "boneNames": ["root", "Bip001Pelvis"],
                    "loadIfNeeded": True,
                },
            )
        )
        _, payload = result
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["assets"][0]["previewEvaluationStatus"], "success")
        service.call_tool.assert_called_once_with(
            "ue_diagnose_animation_scale",
            {
                "animationPaths": [ANIMATION],
                "boneNames": ["root", "Bip001Pelvis"],
                "loadIfNeeded": True,
            },
        )

    def test_diagnose_additive_animation_passes_through_and_classifies(self) -> None:
        service = _live_service({"retargetCapabilities": ["retarget.inspect"]})
        service.call_tool.return_value = {
            "schemaVersion": "1.0",
            "tool": "ue_diagnose_additive_animation",
            "ok": True,
            "readOnly": True,
            "result": {
                "action": "diagnose-additive-animation",
                "assets": [
                    {
                        "assetPath": ANIMATION,
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
                ],
                "editorSessionId": "session-1",
            },
        }
        server = FastMCP("probe")
        register_retarget_tools(
            server=server,
            live_editor_service=service,
            read_annotations=_read_only_annotations(),
            error_response=_error_response,
        )
        result = asyncio.run(
            server.call_tool(
                "ue_diagnose_additive_animation",
                {
                    "animationPaths": [ANIMATION],
                    "loadIfNeeded": True,
                },
            )
        )
        _, payload = result
        self.assertTrue(payload["ok"])
        asset = payload["result"]["assets"][0]
        self.assertEqual(asset["classification"], "additive-valid")
        self.assertTrue(asset["combinedEvaluationFeasible"])
        service.call_tool.assert_called_once_with(
            "ue_diagnose_additive_animation",
            {
                "animationPaths": [ANIMATION],
                "loadIfNeeded": True,
            },
        )

    def test_evaluate_animation_with_base_pose_passes_through_and_enriches(self) -> None:
        service = _live_service({"retargetCapabilities": ["retarget.inspect"]})
        service.call_tool.return_value = {
            "schemaVersion": "1.0",
            "tool": "ue_evaluate_animation_with_base_pose",
            "ok": True,
            "readOnly": True,
            "result": {
                "action": "evaluate-animation-with-base-pose",
                "assets": [
                    {
                        "assetPath": ANIMATION,
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
                            "samples": [
                                {
                                    "fraction": 0.0,
                                    "time": 0.0,
                                    "bones": [
                                        {"bone": "root", "boneExists": True, "combinedComponentScale": {"x": 100, "y": 100, "z": 100}}
                                    ],
                                }
                            ],
                        },
                    }
                ],
                "editorSessionId": "session-1",
            },
        }
        server = FastMCP("probe")
        register_retarget_tools(
            server=server,
            live_editor_service=service,
            read_annotations=_read_only_annotations(),
            error_response=_error_response,
        )
        result = asyncio.run(
            server.call_tool(
                "ue_evaluate_animation_with_base_pose",
                {
                    "animationPaths": [ANIMATION],
                    "boneNames": ["root"],
                    "loadIfNeeded": True,
                },
            )
        )
        _, payload = result
        self.assertTrue(payload["ok"])
        asset = payload["result"]["assets"][0]
        self.assertEqual(asset["classification"], "additive-valid")
        self.assertTrue(asset["combinedEvaluationFeasible"])
        self.assertTrue(asset["evaluationFeasible"])
        self.assertEqual(asset["evaluation"]["status"], "evaluated")
        service.call_tool.assert_called_once_with(
            "ue_evaluate_animation_with_base_pose",
            {
                "animationPaths": [ANIMATION],
                "boneNames": ["root"],
                "loadIfNeeded": True,
            },
        )

if __name__ == "__main__":
    unittest.main()

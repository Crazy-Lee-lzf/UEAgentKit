from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]

ADDITIVE_SAMPLES = [
    (
        "/Game/Characters/Mannequins/Anims/Pistol/Jump/"
        "MM_Pistol_Jump_RecoveryAdditive.MM_Pistol_Jump_RecoveryAdditive"
    ),
    (
        "/Game/Characters/Mannequins/Anims/Rifle/Jump/"
        "MM_Rifle_Jump_RecoveryAdditive.MM_Rifle_Jump_RecoveryAdditive"
    ),
    (
        "/Game/Characters/XinYueHu/Animations/Retargeted/"
        "MM_Pistol_Jump_RecoveryAdditive_XinYueHu.MM_Pistol_Jump_RecoveryAdditive_XinYueHu"
    ),
    (
        "/Game/Characters/XinYueHu/Animations/Retargeted/"
        "MM_Rifle_Jump_RecoveryAdditive_XinYueHu.MM_Rifle_Jump_RecoveryAdditive_XinYueHu"
    ),
]

NON_ADDITIVE_CONTROL = (
    "/Game/Characters/XinYueHu/Animations/Retargeted/"
    "MM_Idle_XinYueHu.MM_Idle_XinYueHu"
)

BONE_NAMES = ["root", "pelvis", "Root", "Bip001Pelvis"]


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


def _finite(vec: Any) -> bool:
    return (
        isinstance(vec, dict)
        and all(isinstance(vec.get(axis), (int, float)) and math.isfinite(vec[axis]) for axis in ("x", "y", "z"))
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(args.database),
            "-EnableLiveEditor",
            "-ProjectPath",
            str(args.project),
            "-LiveEditorTimeoutSeconds",
            "60",
            "-EnableWriteTools",
            "-EngineRoot",
            str(args.engine_root),
            "-Policy",
            str(args.policy),
            "-RevisionExport",
            str(args.revision_export),
            "-WorkRoot",
            str(args.work_root),
            "-BackupRoot",
            str(args.backup_root),
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    animation_paths = ADDITIVE_SAMPLES + [NON_ADDITIVE_CONTROL]
    args.error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = payload(
                    await session.call_tool(
                        "ue_evaluate_animation_with_base_pose",
                        {
                            "animationPaths": animation_paths,
                            "boneNames": BONE_NAMES,
                            "loadIfNeeded": True,
                        },
                    ),
                    "ue_evaluate_animation_with_base_pose",
                )

    assets = response.get("result", {}).get("assets", [])
    if not isinstance(assets, list) or len(assets) != len(animation_paths):
        raise RuntimeError(
            f"expected {len(animation_paths)} assets, got {len(assets)}; raw response: {json.dumps(response, ensure_ascii=False)}"
        )

    by_path: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise RuntimeError("additive evaluation returned a non-object asset")
        by_path[str(asset.get("assetPath", ""))] = asset

    failures: list[str] = []
    for sample in ADDITIVE_SAMPLES:
        asset = by_path.get(sample, {})
        classification = asset.get("classification")
        if classification == "non-additive":
            failures.append(f"{sample} was classified non-additive")
        evaluation = asset.get("evaluation") or {}
        status = evaluation.get("status")
        if status != "evaluated":
            failures.append(f"{sample} evaluation.status={status}, expected evaluated")
            continue
        samples = evaluation.get("samples", [])
        if not isinstance(samples, list) or len(samples) != 3:
            failures.append(f"{sample} expected 3 evaluation samples, got {len(samples) if isinstance(samples, list) else 'non-list'}")
            continue
        saw_combined_scale = False
        saw_delta_scale = False
        for sample in samples:
            for bone in sample.get("bones", []):
                if bone.get("boneExists") and _finite(bone.get("combinedComponentScale")):
                    saw_combined_scale = True
                if _finite(bone.get("additiveDeltaLocalScale")):
                    saw_delta_scale = True
        if not saw_combined_scale:
            failures.append(f"{sample} produced no finite combinedComponentScale")
        if not saw_delta_scale:
            failures.append(f"{sample} produced no finite additiveDeltaLocalScale")

    control = by_path.get(NON_ADDITIVE_CONTROL, {})
    if control.get("classification") != "non-additive":
        failures.append(f"{NON_ADDITIVE_CONTROL} was not classified non-additive")
    if (control.get("evaluation") or {}).get("status") != "skipped-non-additive":
        failures.append(f"{NON_ADDITIVE_CONTROL} was not skipped as non-additive")

    def _first_existing_bone(asset: dict[str, Any]) -> dict[str, Any] | None:
        samples = (asset.get("evaluation") or {}).get("samples", [])
        for sample in samples:
            for bone in sample.get("bones", []):
                if bone.get("boneExists") and _finite(bone.get("combinedComponentScale")):
                    return {
                        "bone": bone.get("bone"),
                        "baseComponentScale": bone.get("baseComponentScale"),
                        "additiveDeltaLocalScale": bone.get("additiveDeltaLocalScale"),
                        "combinedComponentScale": bone.get("combinedComponentScale"),
                    }
        return None

    summary = [
        {
            "assetPath": asset.get("assetPath", ""),
            "status": asset.get("status", ""),
            "additiveTypeName": asset.get("additiveTypeName", ""),
            "basePoseTypeName": asset.get("basePoseTypeName", ""),
            "additiveRefSequencePath": asset.get("additiveRefSequencePath", ""),
            "basePose": asset.get("basePose"),
            "classification": asset.get("classification", ""),
            "combinedEvaluationFeasible": asset.get("combinedEvaluationFeasible"),
            "evaluationFeasible": asset.get("evaluationFeasible"),
            "evaluationStatus": (asset.get("evaluation") or {}).get("status", ""),
            "refFrameClamped": (asset.get("evaluation") or {}).get("refFrameClamped"),
            "sampleCount": len((asset.get("evaluation") or {}).get("samples", [])),
            "sampleBone": _first_existing_bone(asset),
            "suggestedNextStep": asset.get("suggestedNextStep", ""),
        }
        for asset in assets
    ]

    return {
        "ok": not failures,
        "failures": failures,
        "assets": summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only additive combined evaluation smoke.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\r\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

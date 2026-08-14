from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]

SECONDARY_MOTION_CLASSIFICATIONS = (
    "missing-bones",
    "missing-skin-weights",
    "no-animation-tracks",
    "no-secondary-motion-node",
    "no-physics-bodies",
    "cloth-data-present",
    "cloth-data-missing",
    "cloth-binding-incomplete",
)

MESH = "/Game/Characters/XinYueHu/Mesh/SK_XinYueHu.SK_XinYueHu"
ANIMATION = (
    "/Game/Characters/XinYueHu/Animations/Retargeted/"
    "MM_Idle_XinYueHu.MM_Idle_XinYueHu"
)
ANIM_BLUEPRINT = "/Game/Characters/XinYueHu/Animations/ABP_XinYueHu.ABP_XinYueHu"


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


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
    args.error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                full = payload(
                    await session.call_tool(
                        "ue_inspect_skeletal_secondary_motion",
                        {
                            "skeletalMeshPath": MESH,
                            "animationPath": ANIMATION,
                            "animationBlueprintPath": ANIM_BLUEPRINT,
                            "loadIfNeeded": True,
                        },
                    ),
                    "ue_inspect_skeletal_secondary_motion",
                ).get("result", {})
                mesh_only = payload(
                    await session.call_tool(
                        "ue_inspect_skeletal_secondary_motion",
                        {"skeletalMeshPath": MESH, "loadIfNeeded": True},
                    ),
                    "ue_inspect_skeletal_secondary_motion",
                ).get("result", {})

    failures: list[str] = []

    def check(label: str, item: dict[str, Any]) -> None:
        mesh = item.get("skeletalMesh") or {}
        skeleton = item.get("skeleton") or {}
        physics = item.get("physics") or {}
        cloth = item.get("cloth") or {}

        if not mesh.get("skeletonPath"):
            failures.append(f"{label}: skeletalMesh.skeletonPath missing")
        if not isinstance(mesh.get("lodCount"), int) or mesh.get("lodCount") < 1:
            failures.append(f"{label}: skeletalMesh.lodCount = {mesh.get('lodCount')}")
        if not isinstance(mesh.get("vertexCount"), int) or mesh.get("vertexCount") <= 0:
            failures.append(f"{label}: skeletalMesh.vertexCount = {mesh.get('vertexCount')}")
        if not isinstance(skeleton.get("boneCount"), int) or skeleton.get("boneCount") <= 0:
            failures.append(f"{label}: skeleton.boneCount = {skeleton.get('boneCount')}")
        bones = skeleton.get("bones")
        if not isinstance(bones, list) or len(bones) != skeleton.get("boneCount"):
            failures.append(f"{label}: skeleton.bones length mismatch")
        if "bodyCount" not in physics:
            failures.append(f"{label}: physics.bodyCount missing")
        if "assetCount" not in cloth:
            failures.append(f"{label}: cloth.assetCount missing")
        classification = item.get("classification")
        if classification not in SECONDARY_MOTION_CLASSIFICATIONS:
            failures.append(f"{label}: unexpected classification {classification}")

    check("full", full)
    check("mesh-only", mesh_only)

    full_animation = full.get("animation") or {}
    if full_animation.get("status") != "evaluated":
        failures.append(f"full: animation status = {full_animation.get('status')}")
    if full_animation.get("skeletonCompatible") is not True:
        failures.append("full: animation skeletonCompatible not true")
    if not isinstance(full_animation.get("animatedBoneCount"), int):
        failures.append("full: animation.animatedBoneCount missing")

    full_abp = full.get("animationBlueprint") or {}
    if full_abp.get("status") != "evaluated":
        failures.append(f"full: animationBlueprint status = {full_abp.get('status')}")
    if not isinstance(full_abp.get("secondaryMotionNodeCount"), int):
        failures.append("full: animationBlueprint.secondaryMotionNodeCount missing")

    mesh_only_animation = mesh_only.get("animation") or {}
    if mesh_only_animation.get("status") != "skipped-no-animation":
        failures.append(f"mesh-only: animation status = {mesh_only_animation.get('status')}")
    mesh_only_abp = mesh_only.get("animationBlueprint") or {}
    if mesh_only_abp.get("status") != "skipped-no-animation-blueprint":
        failures.append(f"mesh-only: animationBlueprint status = {mesh_only_abp.get('status')}")

    def summarize(item: dict[str, Any]) -> dict[str, Any]:
        mesh = item.get("skeletalMesh") or {}
        skeleton = item.get("skeleton") or {}
        physics = item.get("physics") or {}
        cloth = item.get("cloth") or {}
        animation = item.get("animation") or {}
        anim_blueprint = item.get("animationBlueprint") or {}
        return {
            "skeletalMeshPath": mesh.get("path"),
            "skeletonPath": mesh.get("skeletonPath"),
            "physicsAssetPath": mesh.get("physicsAssetPath"),
            "lodCount": mesh.get("lodCount"),
            "vertexCount": mesh.get("vertexCount"),
            "maxBoneInfluences": mesh.get("maxBoneInfluences"),
            "hasSkinWeights": mesh.get("hasSkinWeights"),
            "boneCount": skeleton.get("boneCount"),
            "rootBoneName": skeleton.get("rootBoneName"),
            "physicsBodyCount": physics.get("bodyCount"),
            "physicsConstraintCount": physics.get("constraintCount"),
            "clothAssetCount": cloth.get("assetCount"),
            "clothAssets": [
                {"name": a.get("name"), "className": a.get("className"), "numLods": a.get("numLods")}
                for a in (cloth.get("assets") or []) if isinstance(a, dict)
            ],
            "animationStatus": animation.get("status"),
            "animatedBoneCount": animation.get("animatedBoneCount"),
            "totalBoneCount": animation.get("totalBoneCount"),
            "animBlueprintStatus": anim_blueprint.get("status"),
            "secondaryMotionNodeCount": anim_blueprint.get("secondaryMotionNodeCount"),
            "springBoneCount": anim_blueprint.get("springBoneCount"),
            "rigidBodyCount": anim_blueprint.get("rigidBodyCount"),
            "animDynamicsCount": anim_blueprint.get("animDynamicsCount"),
            "secondaryMotionNodes": [
                {"title": n.get("title"), "className": n.get("className")}
                for n in (anim_blueprint.get("nodes") or []) if isinstance(n, dict)
            ],
            "classification": item.get("classification"),
            "suggestedNextStep": item.get("suggestedNextStep"),
        }

    return {
        "ok": not failures,
        "failures": failures,
        "full": summarize(full),
        "meshOnly": summarize(mesh_only),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only skeletal secondary-motion inspection smoke.")
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

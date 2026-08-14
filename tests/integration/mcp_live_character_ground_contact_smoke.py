from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]

GROUND_CONTACT_CLASSIFICATIONS = (
    "mesh-offset-candidate",
    "capsule-size-candidate",
    "animation-root-z-candidate",
    "pelvis-offset-candidate",
    "foot-ik-needed",
    "insufficient-context",
)

CHARACTER = (
    "/Game/Characters/XinYueHu/Blueprints/"
    "BP_XinYueHu_Character.BP_XinYueHu_Character"
)
ANIMATION = (
    "/Game/Characters/XinYueHu/Animations/Retargeted/"
    "MM_Idle_XinYueHu.MM_Idle_XinYueHu"
)
BONES = {
    "rootBone": "Root",
    "pelvisBone": "Bip001Pelvis",
    "leftFootBone": "Bip001LFoot",
    "rightFootBone": "Bip001RFoot",
}


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

                animated = payload(
                    await session.call_tool(
                        "ue_diagnose_character_ground_contact",
                        {**BONES, "characterPath": CHARACTER, "animationPath": ANIMATION, "loadIfNeeded": True},
                    ),
                    "ue_diagnose_character_ground_contact",
                ).get("result", {})
                no_animation = payload(
                    await session.call_tool(
                        "ue_diagnose_character_ground_contact",
                        {**BONES, "characterPath": CHARACTER, "loadIfNeeded": True},
                    ),
                    "ue_diagnose_character_ground_contact",
                ).get("result", {})

    failures: list[str] = []

    def check(label: str, item: dict[str, Any]) -> None:
        capsule = item.get("capsule") or {}
        mesh = item.get("mesh") or {}
        skeleton_ref = item.get("skeletonReference") or {}

        if capsule.get("present") is not True:
            failures.append(f"{label}: capsule not present")
        if not float(capsule.get("radius") or 0.0) > 0:
            failures.append(f"{label}: capsule radius missing")
        if not float(capsule.get("halfHeight") or 0.0) > 0:
            failures.append(f"{label}: capsule halfHeight missing")
        if mesh.get("present") is not True:
            failures.append(f"{label}: mesh not present")
        if not mesh.get("skeletalMeshPath"):
            failures.append(f"{label}: mesh skeletalMeshPath missing")
        if skeleton_ref.get("status") != "evaluated":
            failures.append(f"{label}: skeletonReference status = {skeleton_ref.get('status')}")
        bones = skeleton_ref.get("bones")
        if not isinstance(bones, list) or len(bones) != 4:
            failures.append(f"{label}: expected 4 reference bones")
        elif any(not isinstance(b, dict) or b.get("boneExists") is not True for b in bones):
            failures.append(f"{label}: a reference bone did not resolve: {[b.get('bone') for b in bones]}")
        classification = item.get("classification")
        if classification not in GROUND_CONTACT_CLASSIFICATIONS:
            failures.append(f"{label}: unexpected classification {classification}")

    check("animated", animated)
    check("no-animation", no_animation)

    animated_animation = animated.get("animation") or {}
    if animated_animation.get("status") != "evaluated":
        failures.append(f"animated: animation status = {animated_animation.get('status')}")
    if animated_animation.get("skeletonCompatible") is not True:
        failures.append("animated: skeletonCompatible not true")
    samples = animated_animation.get("samples")
    if not isinstance(samples, list) or len(samples) != 3:
        failures.append(f"animated: expected 3 samples, got {len(samples) if isinstance(samples, list) else 'n/a'}")

    no_animation_animation = no_animation.get("animation") or {}
    if no_animation_animation.get("status") != "skipped-no-animation":
        failures.append(f"no-animation: animation status = {no_animation_animation.get('status')}")

    def summarize(item: dict[str, Any]) -> dict[str, Any]:
        capsule = item.get("capsule") or {}
        mesh = item.get("mesh") or {}
        skeleton_ref = item.get("skeletonReference") or {}
        animation = item.get("animation") or {}
        sample = (animation.get("samples") or [{}])[0] if isinstance(animation.get("samples"), list) and animation.get("samples") else {}
        return {
            "characterPath": item.get("characterPath"),
            "classPath": item.get("classPath"),
            "capsuleRadius": capsule.get("radius"),
            "capsuleHalfHeight": capsule.get("halfHeight"),
            "meshSkeletalMeshPath": mesh.get("skeletalMeshPath"),
            "meshSkeletonPath": mesh.get("skeletonPath"),
            "meshRelativeTranslation": (mesh.get("relativeTransform") or {}).get("translation"),
            "skeletonReferenceStatus": skeleton_ref.get("status"),
            "referenceBones": [
                {"bone": b.get("bone"), "boneExists": b.get("boneExists"), "componentLocation": b.get("componentLocation")}
                for b in (skeleton_ref.get("bones") or []) if isinstance(b, dict)
            ],
            "animationStatus": animation.get("status"),
            "skeletonCompatible": animation.get("skeletonCompatible"),
            "rootMotionZ": animation.get("rootMotionZ"),
            "sampleCount": len(animation.get("samples") or []),
            "firstSample": {
                "leftFootToCapsuleBottom": sample.get("leftFootToCapsuleBottom"),
                "rightFootToCapsuleBottom": sample.get("rightFootToCapsuleBottom"),
                "leftFootLowestZ": sample.get("leftFootLowestZ"),
                "rightFootLowestZ": sample.get("rightFootLowestZ"),
            },
            "classification": item.get("classification"),
            "suggestedNextStep": item.get("suggestedNextStep"),
        }

    return {
        "ok": not failures,
        "failures": failures,
        "animated": summarize(animated),
        "noAnimation": summarize(no_animation),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only character ground-contact diagnosis smoke.")
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

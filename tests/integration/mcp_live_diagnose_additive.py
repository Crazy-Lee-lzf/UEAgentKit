from __future__ import annotations

import argparse
import asyncio
import json
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
    animation_paths = ADDITIVE_SAMPLES + [NON_ADDITIVE_CONTROL]
    args.error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                response = payload(
                    await session.call_tool(
                        "ue_diagnose_additive_animation",
                        {
                            "animationPaths": animation_paths,
                            "loadIfNeeded": True,
                        },
                    ),
                    "ue_diagnose_additive_animation",
                )

    assets = response.get("result", {}).get("assets", [])
    if not isinstance(assets, list) or len(assets) != len(animation_paths):
        raise RuntimeError(f"expected {len(animation_paths)} assets, got {len(assets)}")

    by_path: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise RuntimeError("additive diagnosis returned a non-object asset")
        by_path[str(asset.get("assetPath", ""))] = asset

    failures: list[str] = []
    for sample in ADDITIVE_SAMPLES:
        asset = by_path.get(sample, {})
        if asset.get("classification") == "non-additive":
            failures.append(f"{sample} was classified non-additive")
        if "additiveTypeName" not in asset or not asset.get("additiveTypeName"):
            failures.append(f"{sample} missing additiveTypeName")
        if not isinstance(asset.get("combinedEvaluationFeasible"), bool):
            failures.append(f"{sample} missing combinedEvaluationFeasible bool")

    control = by_path.get(NON_ADDITIVE_CONTROL, {})
    if control.get("classification") != "non-additive":
        failures.append(f"{NON_ADDITIVE_CONTROL} was not classified non-additive")

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
    parser = argparse.ArgumentParser(description="Read-only additive animation diagnosis smoke.")
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

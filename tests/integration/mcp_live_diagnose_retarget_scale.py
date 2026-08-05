from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ANIMATIONS = [
    "/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle.MM_Idle",
    "/Game/Characters/Mannequins/Anims/Unarmed/Walk/MF_Unarmed_Walk_Fwd.MF_Unarmed_Walk_Fwd",
    "/Game/Characters/Mannequins/Anims/Unarmed/Jog/MF_Unarmed_Jog_Fwd.MF_Unarmed_Jog_Fwd",
    "/Game/Characters/Mannequins/Anims/Unarmed/Jump/MM_Jump.MM_Jump",
    "/Game/UEAgentKitRetargetTests/Retargeted/RTG_MM_Idle.RTG_MM_Idle",
    "/Game/UEAgentKitRetargetTests/Retargeted/RTG_MF_Unarmed_Walk_Fwd.RTG_MF_Unarmed_Walk_Fwd",
    "/Game/UEAgentKitRetargetTests/Retargeted/RTG_MF_Unarmed_Jog_Fwd.RTG_MF_Unarmed_Jog_Fwd",
    "/Game/UEAgentKitRetargetTests/Retargeted/RTG_MM_Jump.RTG_MM_Jump",
    "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu.MM_Idle_XinYueHu",
    (
        "/Game/Characters/XinYueHu/Animations/Retargeted/"
        "MF_Unarmed_Jog_Fwd_XinYueHu.MF_Unarmed_Jog_Fwd_XinYueHu"
    ),
    "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Jump_XinYueHu.MM_Jump_XinYueHu",
]


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
    animations = args.animation or DEFAULT_ANIMATIONS
    args.error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                return payload(
                    await session.call_tool(
                        "ue_diagnose_animation_scale",
                        {
                            "animationPaths": animations,
                            "boneNames": ["root", "pelvis", "Root", "Bip001Pelvis"],
                            "loadIfNeeded": True,
                        },
                    ),
                    "ue_diagnose_animation_scale",
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only retarget scale diagnosis.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--animation", action="append")
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\r\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

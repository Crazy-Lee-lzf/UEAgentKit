from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402

EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

SOURCE_MESH = "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple.SKM_Manny_Simple"
TARGET_MESH = "/Game/Characters/XinYueHu/Mesh/SK_XinYueHu.SK_XinYueHu"
OUTPUT_DIRECTORY = "/Game/Characters/XinYueHu/Animations/Retargeted"

SAMPLE_ANIMATIONS = [
    "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu.MM_Idle_XinYueHu",
    "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Jump_XinYueHu.MM_Jump_XinYueHu",
    "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Land_XinYueHu.MM_Land_XinYueHu",
    "/Game/Characters/XinYueHu/Animations/Retargeted/MF_Unarmed_Walk_Fwd_XinYueHu.MF_Unarmed_Walk_Fwd_XinYueHu",
    "/Game/Characters/XinYueHu/Animations/Retargeted/MF_Unarmed_Jog_Fwd_XinYueHu.MF_Unarmed_Jog_Fwd_XinYueHu",
    "/Game/Characters/XinYueHu/Animations/Retargeted/BS_Idle_Walk_Run_XinYueHu.BS_Idle_Walk_Run_XinYueHu",
]


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


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
            "30",
            "-EnableWriteTools",
            "-EnableCommitTools",
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
                if args.session_marker is not None:
                    args.session_marker.write_text("session-initialized\n", encoding="utf-8")
                listed = await session.list_tools()
                if [tool.name for tool in listed.tools] != EXPECTED_TOOLS:
                    raise RuntimeError("Unexpected combined Tool list")

                planned = await call(
                    session,
                    "ue_plan_animation_retarget",
                    {
                        "sourceMesh": SOURCE_MESH,
                        "targetMesh": TARGET_MESH,
                        "includeOptionalChains": True,
                        "outputDirectory": OUTPUT_DIRECTORY,
                    },
                )
                plan_id = planned.get("planId")
                if not planned.get("ok") or not plan_id or planned.get("blockingIssues"):
                    raise RuntimeError(f"Retarget plan contract is broken: {planned}")
                applied = await call(
                    session,
                    "ue_apply_animation_retarget_setup",
                    {
                        "planId": plan_id,
                        "confirmation": f"APPLY RETARGET SETUP {plan_id}",
                        "updateExisting": True,
                    },
                )
                if not applied.get("ok"):
                    raise RuntimeError(f"Retarget setup apply failed: {applied}")
                retargeter_change = next(
                    (c for c in applied.get("changes", []) if "IKRetargeter_" in str(c.get("assetPath", ""))), None
                )
                if retargeter_change is None:
                    raise RuntimeError(f"No IK Retargeter was created: {applied}")
                retargeter_path = str(retargeter_change["assetPath"])

                validated = await call(
                    session,
                    "ue_validate_animation_retarget",
                    {"retargeter": retargeter_path, "animationPaths": SAMPLE_ANIMATIONS},
                )
                if not validated.get("ok"):
                    raise RuntimeError(f"Validation failed: {validated}")

                issues_by_level: dict[str, list[str]] = {"error": [], "warning": []}
                for issue in validated.get("issues", []):
                    issues_by_level.setdefault(issue.get("level", ""), []).append(
                        f"{issue.get('code')}:{issue.get('assetPath', '')}:{issue.get('bone', '')}"
                    )

                return {
                    "planId": plan_id,
                    "retargeterAsset": retargeter_path,
                    "verdict": validated.get("verdict"),
                    "animationCount": validated.get("animationCount"),
                    "errorCount": len(issues_by_level.get("error", [])),
                    "warningCount": len(issues_by_level.get("warning", [])),
                    "errors": issues_by_level.get("error", [])[:10],
                    "warnings": issues_by_level.get("warning", [])[:10],
                }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--session-marker", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

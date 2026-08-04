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
OUTPUT_DIRECTORY = "/Game/UEAgentKitRetargetTests/Retargeted"

SOURCE_ANIMATIONS = [
    "/Game/Characters/Mannequins/Anims/Pistol/MM_Pistol_Equip.MM_Pistol_Equip",
    "/Game/Characters/Mannequins/Anims/Pistol/MM_Pistol_Fire.MM_Pistol_Fire",
    "/Game/Characters/Mannequins/Anims/Pistol/MM_Pistol_DryFire.MM_Pistol_DryFire",
]
NAMING = {"search": "MM_", "replace": "RTG_", "prefix": "", "suffix": ""}


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
                initialized = await session.initialize()
                if args.session_marker is not None:
                    args.session_marker.write_text("session-initialized\n", encoding="utf-8")
                listed = await session.list_tools()
                tool_names = [tool.name for tool in listed.tools]
                if tool_names != EXPECTED_TOOLS:
                    raise RuntimeError(f"Unexpected combined Tool list: {tool_names}")
                for asset_path in (SOURCE_MESH, TARGET_MESH):
                    opened = await call(session, "ue_open_asset", {"asset_path": asset_path})
                    if not opened.get("ok") or not opened["result"].get("openAfter"):
                        raise RuntimeError(f"The fixture {asset_path} was not opened: {opened}")

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
                if not applied.get("ok") or not applied.get("changed"):
                    raise RuntimeError(f"Retarget setup apply failed before batch: {applied}")
                retargeter_change = next(
                    (c for c in applied.get("changes", []) if "IKRetargeter_" in str(c.get("assetPath", ""))), None
                )
                if retargeter_change is None:
                    raise RuntimeError(f"No IK Retargeter was created: {applied}")
                retargeter_path = str(retargeter_change["assetPath"])

                started = await call(
                    session,
                    "ue_start_animation_retarget_batch",
                    {
                        "planId": plan_id,
                        "retargeter": retargeter_path,
                        "sourceAssets": SOURCE_ANIMATIONS,
                        "outputDirectory": OUTPUT_DIRECTORY,
                        "naming": NAMING,
                        "overwriteExisting": False,
                        "includeReferencedAssets": True,
                        "exportOnlyAnimatedBones": True,
                        "retainAdditiveFlags": True,
                    },
                )
                task_id = started.get("taskId")
                if not started.get("ok") or not task_id or started.get("status") != "queued":
                    raise RuntimeError(f"Retarget batch start contract is broken: {started}")

                finished = await call(session, "ue_get_animation_retarget_batch", {"taskId": task_id})
                if finished.get("status") != "completed":
                    raise RuntimeError(f"Retarget batch did not complete: {finished}")
                created_assets = finished.get("createdAssets", [])
                if len(created_assets) != 3:
                    raise RuntimeError(f"Retarget batch produced {len(created_assets)} outputs: {finished}")
                for output_path in created_assets:
                    if "RTG_" not in output_path:
                        raise RuntimeError(f"Batch naming rule was not applied: {output_path}")
                    if not output_path.startswith(OUTPUT_DIRECTORY):
                        raise RuntimeError(f"Batch output is outside the output directory: {output_path}")

                # Second batch with the same outputs and no overwrite must be denied.
                started_again = await call(
                    session,
                    "ue_start_animation_retarget_batch",
                    {
                        "planId": plan_id,
                        "retargeter": retargeter_path,
                        "sourceAssets": SOURCE_ANIMATIONS,
                        "outputDirectory": OUTPUT_DIRECTORY,
                        "naming": NAMING,
                        "overwriteExisting": False,
                    },
                )
                again_task = started_again.get("taskId")
                again_result = await call(session, "ue_get_animation_retarget_batch", {"taskId": again_task})
                if again_result.get("status") != "failed":
                    raise RuntimeError(f"Overwrite was not denied: {again_result}")
                if again_result.get("error", {}).get("code") != "retarget_overwrite_denied":
                    raise RuntimeError(f"Overwrite denial code is wrong: {again_result}")

                # A fresh batch that is cancelled before running must stay cancelled.
                to_cancel = await call(
                    session,
                    "ue_start_animation_retarget_batch",
                    {
                        "planId": plan_id,
                        "retargeter": retargeter_path,
                        "sourceAssets": SOURCE_ANIMATIONS,
                        "outputDirectory": "/Game/UEAgentKitRetargetTests/Cancelled",
                        "naming": NAMING,
                    },
                )
                cancel_task = to_cancel.get("taskId")
                cancelled = await call(session, "ue_cancel_animation_retarget_batch", {"taskId": cancel_task})
                if cancelled.get("status") != "cancelled":
                    raise RuntimeError(f"Retarget batch was not cancelled: {cancelled}")

                return {
                    "protocolVersion": initialized.protocolVersion,
                    "toolCount": len(tool_names),
                    "planId": plan_id,
                    "retargeterAsset": retargeter_path,
                    "batchTaskId": task_id,
                    "batchStatus": finished.get("status"),
                    "createdAssets": created_assets,
                    "overwriteDenied": again_result.get("error", {}).get("code"),
                    "cancelStatus": cancelled.get("status"),
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

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

BASIC_ANIMATIONS = [
    "/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle.MM_Idle",
    "/Game/Characters/Mannequins/Anims/Unarmed/Walk/MF_Unarmed_Walk_Fwd.MF_Unarmed_Walk_Fwd",
    "/Game/Characters/Mannequins/Anims/Unarmed/Jog/MF_Unarmed_Jog_Fwd.MF_Unarmed_Jog_Fwd",
    "/Game/Characters/Mannequins/Anims/Unarmed/Jump/MM_Jump.MM_Jump",
]
NAMING = {"search": "", "replace": "", "prefix": "RTG_", "suffix": ""}


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


def project_asset_file(project_path: Path, object_path: str) -> Path:
    # project_path is the .uproject file; the Content folder sits next to it.
    package = object_path.rsplit(".", 1)[0]
    relative = package[len("/Game/") :]
    return (project_path.parent / "Content").joinpath(*relative.split("/")).with_suffix(".uasset")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    project_path: Path = args.project
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
                        "sourceAssets": BASIC_ANIMATIONS,
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
                if len(created_assets) != len(BASIC_ANIMATIONS):
                    raise RuntimeError(f"Retarget batch produced {len(created_assets)} outputs: {finished}")

                saved = await call(
                    session,
                    "ue_save_animation_retarget_batch",
                    {"taskId": task_id, "confirmation": f"SAVE RETARGET BATCH {task_id}"},
                )
                if saved.get("status") != "saved" or len(saved.get("savedAssets", [])) != len(created_assets):
                    raise RuntimeError(f"Retarget batch save failed: {saved}")

                on_disk = []
                for output_path in created_assets:
                    disk_file = project_asset_file(project_path, str(output_path))
                    if not disk_file.exists() or disk_file.stat().st_size <= 0:
                        raise RuntimeError(f"Saved animation is missing on disk: {disk_file}")
                    on_disk.append(str(disk_file))

                return {
                    "protocolVersion": initialized.protocolVersion,
                    "toolCount": len(tool_names),
                    "planId": plan_id,
                    "retargeterAsset": retargeter_path,
                    "batchTaskId": task_id,
                    "batchStatus": finished.get("status"),
                    "saveStatus": saved.get("status"),
                    "createdAssets": created_assets,
                    "savedOnDisk": on_disk,
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

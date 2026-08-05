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
NAMING = {"search": "", "replace": "", "prefix": "", "suffix": "_XinYueHu"}
# The Live Editor Bridge request timeout is capped at 30s; a retarget of ~100
# animations takes ~31s, so keep each batch comfortably under the cap.
BATCH_LIMIT = 40

ABP_PATHS = [
    "/Game/Characters/XinYueHu/Animations/Retargeted/ABP_Manny_Combat_XinYueHu.ABP_Manny_Combat_XinYueHu",
    "/Game/Characters/XinYueHu/Animations/Retargeted/ABP_Unarmed_XinYueHu.ABP_Unarmed_XinYueHu",
]


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


def project_asset_file(project_path: Path, object_path: str) -> Path:
    package = object_path.rsplit(".", 1)[0]
    relative = package[len("/Game/") :]
    return (project_path.parent / "Content").joinpath(*relative.split("/")).with_suffix(".uasset")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    project_path: Path = args.project
    sources: list[str] = json.loads(Path(args.sources_file).read_text(encoding="utf-8"))
    if not sources:
        raise RuntimeError("Source list is empty")
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
            "600",
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
    summary: dict[str, Any] = {}
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

                all_saved: list[str] = []
                chunks = [sources[i : i + BATCH_LIMIT] for i in range(0, len(sources), BATCH_LIMIT)]
                for chunk_index, chunk in enumerate(chunks):
                    if not chunk:
                        continue
                    started = await call(
                        session,
                        "ue_start_animation_retarget_batch",
                        {
                            "planId": plan_id,
                            "retargeter": retargeter_path,
                            "sourceAssets": chunk,
                            "outputDirectory": OUTPUT_DIRECTORY,
                            "naming": NAMING,
                            "overwriteExisting": True,
                            "includeReferencedAssets": True,
                            "exportOnlyAnimatedBones": True,
                            "retainAdditiveFlags": True,
                        },
                    )
                    task_id = started.get("taskId")
                    if not started.get("ok") or not task_id:
                        raise RuntimeError(f"Batch {chunk_index} start failed: {started}")
                    finished = await call(session, "ue_get_animation_retarget_batch", {"taskId": task_id})
                    if finished.get("status") != "completed":
                        raise RuntimeError(f"Batch {chunk_index} did not complete: {finished}")
                    saved = await call(
                        session,
                        "ue_save_animation_retarget_batch",
                        {"taskId": task_id, "confirmation": f"SAVE RETARGET BATCH {task_id}"},
                    )
                    if saved.get("status") != "saved":
                        raise RuntimeError(f"Batch {chunk_index} save failed: {saved}")
                    all_saved.extend(saved.get("savedAssets", []))
                    summary[f"batch{chunk_index + 1}"] = {
                        "taskId": task_id,
                        "count": len(saved.get("savedAssets", [])),
                    }

                compile_results = []
                for abp in ABP_PATHS:
                    compiled = await call(session, "ue_compile_blueprint", {"asset_path": abp})
                    compile_results.append({"abp": abp, "ok": compiled.get("ok"), "result": compiled.get("result")})

                missing = []
                for output_path in all_saved:
                    disk = project_asset_file(project_path, str(output_path))
                    if not disk.exists() or disk.stat().st_size <= 0:
                        missing.append(str(output_path))
                if missing:
                    raise RuntimeError(f"Missing on-disk outputs: {missing[:10]}")

                summary["planId"] = plan_id
                summary["retargeterAsset"] = retargeter_path
                summary["totalSaved"] = len(all_saved)
                summary["compileResults"] = compile_results
                summary["savedAssets"] = all_saved
                print(json.dumps(summary, indent=2, sort_keys=True))
    return summary


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
    parser.add_argument("--sources-file", required=True, type=Path)
    args = parser.parse_args()
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

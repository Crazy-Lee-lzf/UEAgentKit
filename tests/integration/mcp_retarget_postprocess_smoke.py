from __future__ import annotations

import argparse
import asyncio
import json
import secrets
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

SOURCE_MESH = "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple.SKM_Manny_Simple"
TARGET_MESH = "/Game/Characters/XinYueHu/Mesh/SK_XinYueHu.SK_XinYueHu"
SOURCE_ANIMATION = "/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle.MM_Idle"
RETARGETER = (
    "/Game/Characters/Mannequins/Meshes/IKRetargeter_SKM_Manny_Simple_to_SK_XinYueHu."
    "IKRetargeter_SKM_Manny_Simple_to_SK_XinYueHu"
)
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)


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
    nonce = secrets.token_hex(4)
    prefix = f"P3_{nonce}_"
    output_name = f"{prefix}MM_Idle"
    output_path = f"{args.output_directory}/{output_name}.{output_name}"
    output_file = project_asset_file(args.project, output_path)
    if output_file.exists():
        raise RuntimeError(f"Fresh P3 smoke output unexpectedly exists before the test: {output_file}")

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

    task_id = ""
    args.error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                listed = await session.list_tools()
                tool_names = [tool.name for tool in listed.tools]
                if tool_names != EXPECTED_TOOLS:
                    raise RuntimeError(f"Unexpected combined Tool list: {tool_names}")

                for asset_path in (SOURCE_MESH, TARGET_MESH):
                    opened = await call(session, "ue_open_asset", {"asset_path": asset_path})
                    if not opened.get("ok") or not opened.get("result", {}).get("openAfter"):
                        raise RuntimeError(f"Retarget fixture was not opened: {asset_path}: {opened}")

                planned = await call(
                    session,
                    "ue_plan_animation_retarget",
                    {
                        "sourceMesh": SOURCE_MESH,
                        "targetMesh": TARGET_MESH,
                        "includeOptionalChains": True,
                        "outputDirectory": args.output_directory,
                    },
                )
                plan_id = str(planned.get("planId") or "")
                if not planned.get("ok") or not plan_id or planned.get("blockingIssues"):
                    raise RuntimeError(f"Retarget plan is not usable: {planned}")

                started = await call(
                    session,
                    "ue_start_animation_retarget_batch",
                    {
                        "planId": plan_id,
                        "retargeter": RETARGETER,
                        "sourceAssets": [SOURCE_ANIMATION],
                        "outputDirectory": args.output_directory,
                        "naming": {"search": "", "replace": "", "prefix": prefix, "suffix": ""},
                        "overwriteExisting": False,
                        "includeReferencedAssets": False,
                        "exportOnlyAnimatedBones": True,
                        "retainAdditiveFlags": True,
                    },
                )
                task_id = str(started.get("taskId") or "")
                if not started.get("ok") or not task_id or started.get("status") != "queued":
                    raise RuntimeError(f"Retarget batch did not queue: {started}")
                finished = await call(session, "ue_get_animation_retarget_batch", {"taskId": task_id})
                if finished.get("status") != "completed":
                    raise RuntimeError(f"Retarget batch did not complete: {finished}")
                outputs = finished.get("outputs", [])
                if len(outputs) != 1 or outputs[0].get("outputPath") != output_path:
                    raise RuntimeError(f"Retarget batch returned unexpected outputs: {outputs}")
                output = outputs[0]
                if output.get("assetType") != "AnimSequence":
                    raise RuntimeError(f"Retarget output type metadata is wrong: {output}")
                if "AnimSequence" not in str(output.get("assetClass") or ""):
                    raise RuntimeError(f"Retarget output class metadata is wrong: {output}")
                if not str(output.get("skeletonPath") or ""):
                    raise RuntimeError(f"Retarget output skeleton metadata is missing: {output}")

                postprocess_started = await call(
                    session,
                    "ue_start_animation_retarget_postprocess",
                    {"retargetTaskId": task_id, "loadIfNeeded": True, "batchSize": 1},
                )
                if not postprocess_started.get("ok"):
                    raise RuntimeError(f"P3 post-process did not start: {postprocess_started}")
                postprocess_id = str(postprocess_started.get("result", {}).get("postprocessId") or "")
                if not postprocess_id:
                    raise RuntimeError(f"P3 post-process returned no ID: {postprocess_started}")

                analyzed = await call(
                    session,
                    "ue_get_animation_retarget_postprocess",
                    {"postprocessId": postprocess_id},
                )
                result = analyzed.get("result", {})
                if result.get("state") != "analyzed":
                    raise RuntimeError(f"P3 post-process did not finish one bounded audit step: {analyzed}")
                suggestions = result.get("suggestions", {})
                if suggestions.get("scaleFixCandidateCount", 0) + suggestions.get("manualReviewCount", 0) < 1:
                    raise RuntimeError(f"P3 post-process produced no actionable classification: {analyzed}")

                suggested_plan = await call(
                    session,
                    "ue_plan_animation_retarget_postprocess",
                    {"postprocessId": postprocess_id, "description": "P3 real UE5.6 post-process smoke"},
                )
                boundary = suggested_plan.get("result", {}).get("executionBoundary", {})
                if (
                    boundary.get("modifiesAssets") is not False
                    or boundary.get("autoApplyAllowed") is not False
                    or boundary.get("requiresUserReview") is not True
                    or boundary.get("referenceAssetMutationImplemented") is not False
                ):
                    raise RuntimeError(f"P3 Suggested Plan execution boundary is unsafe: {suggested_plan}")
                plan_relative_path = str(suggested_plan.get("planRelativePath") or "")
                if not plan_relative_path.startswith("retarget-postprocess/"):
                    raise RuntimeError(f"P3 Suggested Plan escaped the fixed WorkRoot contract: {suggested_plan}")

                saved = await call(
                    session,
                    "ue_save_animation_retarget_batch",
                    {"taskId": task_id, "confirmation": f"SAVE RETARGET BATCH {task_id}"},
                )
                if saved.get("status") != "saved" or output_path not in saved.get("savedAssets", []):
                    raise RuntimeError(f"Retarget output save failed before rollback: {saved}")
                if not output_file.is_file():
                    raise RuntimeError(f"Saved P3 smoke output is missing: {output_file}")

                dry_run = await call(
                    session,
                    "ue_rollback_animation_retarget_batch",
                    {"taskId": task_id, "mode": "DryRun"},
                )
                if dry_run.get("deleteCount") != 1 or dry_run.get("restoreCount") != 0:
                    raise RuntimeError(f"P3 smoke rollback DryRun is wrong: {dry_run}")
                rollback_receipt = str(dry_run.get("rollbackDryRunReceipt") or "")
                rolled_back = await call(
                    session,
                    "ue_rollback_animation_retarget_batch",
                    {
                        "taskId": task_id,
                        "mode": "Commit",
                        "rollbackDryRunReceipt": rollback_receipt,
                        "confirmation": f"ROLLBACK RETARGET BATCH {task_id}",
                    },
                )
                if not rolled_back.get("valid") or rolled_back.get("verificationFailed"):
                    raise RuntimeError(f"P3 smoke rollback failed: {rolled_back}")

    if output_file.exists():
        raise RuntimeError(f"P3 smoke left the temporary retarget output on disk: {output_file}")
    return {
        "outputPath": output_path,
        "retargetTaskId": task_id,
        "assetType": "AnimSequence",
        "postprocessAnalyzed": True,
        "suggestedPlanCreated": True,
        "autoApplyAllowed": False,
        "temporaryOutputDeleted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Real UE5.6 retarget post-process read-only suggestion smoke test.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--output-directory", default="/Game/UEAgentKitRetargetTests/Postprocess")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

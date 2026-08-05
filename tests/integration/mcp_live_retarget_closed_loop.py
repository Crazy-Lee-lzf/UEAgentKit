from __future__ import annotations

import argparse
import asyncio
import hashlib
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

# Phase 1 creates A and B. Phase 2 overwrites A and creates C.
SOURCE_A = "/Game/Characters/Mannequins/Anims/Unarmed/MM_Idle.MM_Idle"
SOURCE_B = "/Game/Characters/Mannequins/Anims/Unarmed/Walk/MF_Unarmed_Walk_Fwd.MF_Unarmed_Walk_Fwd"
SOURCE_C = "/Game/Characters/Mannequins/Anims/Unarmed/Jump/MM_Jump.MM_Jump"
NAMING = {"search": "", "replace": "", "prefix": "CL_", "suffix": ""}


def output_paths(output_directory: str) -> tuple[str, str, str]:
    # Restored in phase 2 rollback; created in phase 2 then deleted.
    output_a = f"{output_directory}/CL_MM_Idle.CL_MM_Idle"
    output_b = f"{output_directory}/CL_MF_Unarmed_Walk_Fwd.CL_MF_Unarmed_Walk_Fwd"
    output_c = f"{output_directory}/CL_MM_Jump.CL_MM_Jump"
    return output_a, output_b, output_c


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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def object_path_of(output: dict[str, Any]) -> str:
    return str(output.get("outputPath", ""))


async def run(args: argparse.Namespace) -> dict[str, Any]:
    project_path: Path = args.project
    output_directory = args.output_directory
    output_a, output_b, output_c = output_paths(output_directory)
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
                        "outputDirectory": output_directory,
                    },
                )
                plan_id = planned.get("planId")
                if not planned.get("ok") or not plan_id or planned.get("blockingIssues"):
                    raise RuntimeError(f"Retarget plan contract is broken: {planned}")

                change_set = await call(
                    session,
                    "ue_create_change_set",
                    {"title": "Retarget closed-loop", "task_id": "task_retarget_closed_loop"},
                )
                change_set_id = change_set.get("changeSetId")
                if not change_set.get("ok") or not change_set_id:
                    raise RuntimeError(f"Change Set creation failed: {change_set}")

                applied = await call(
                    session,
                    "ue_apply_animation_retarget_setup",
                    {
                        "planId": plan_id,
                        "confirmation": f"APPLY RETARGET SETUP {plan_id}",
                        "changeSetId": change_set_id,
                        "updateExisting": True,
                    },
                )
                if not applied.get("ok"):
                    raise RuntimeError(f"Retarget setup apply failed before closed loop: {applied}")
                retargeter_change = next(
                    (c for c in applied.get("changes", []) if "IKRetargeter_" in str(c.get("assetPath", ""))), None
                )
                if retargeter_change is None:
                    raise RuntimeError(f"No IK Retargeter was created: {applied}")
                retargeter_path = str(retargeter_change["assetPath"])

                async def run_batch(
                    source_assets: list[str],
                    overwrite: bool,
                ) -> dict[str, Any]:
                    started = await call(
                        session,
                        "ue_start_animation_retarget_batch",
                        {
                            "planId": plan_id,
                            "retargeter": retargeter_path,
                            "sourceAssets": source_assets,
                            "outputDirectory": output_directory,
                            "naming": NAMING,
                            "overwriteExisting": overwrite,
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
                    return {"taskId": task_id, "finished": finished}

                # Phase 1: create A and B, then save so they exist on disk for the
                # phase 2 overwrite Backup to capture.
                phase1 = await run_batch([SOURCE_A, SOURCE_B], overwrite=False)
                phase1_outputs = sorted(object_path_of(o) for o in phase1["finished"].get("outputs", []))
                if set(phase1_outputs) != {output_a, output_b}:
                    raise RuntimeError(f"Phase 1 outputs are wrong: {phase1_outputs}")
                phase1_save = await call(
                    session,
                    "ue_save_animation_retarget_batch",
                    {"taskId": phase1["taskId"], "confirmation": f"SAVE RETARGET BATCH {phase1['taskId']}"},
                )
                if phase1_save.get("status") != "saved" or len(phase1_save.get("savedAssets", [])) != 2:
                    raise RuntimeError(f"Phase 1 save failed: {phase1_save}")
                phase1_a_sha = sha256(project_asset_file(project_path, output_a))
                phase1_b_sha = sha256(project_asset_file(project_path, output_b))

                # Phase 2: overwrite A, create C. The pre-batch Backup must capture A.
                phase2 = await run_batch([SOURCE_A, SOURCE_C], overwrite=True)
                phase2_outputs = sorted(object_path_of(o) for o in phase2["finished"].get("outputs", []))
                if set(phase2_outputs) != {output_a, output_c}:
                    raise RuntimeError(f"Phase 2 outputs are wrong: {phase2_outputs}")
                backup_manifest = phase2["finished"].get("backupManifest", {})
                entries = {str(e.get("outputPath", "")): e for e in backup_manifest.get("entries", [])}
                if entries[output_a].get("kind") != "overwrite":
                    raise RuntimeError(f"Phase 2 did not back up the overwritten output A: {backup_manifest}")
                if entries[output_c].get("kind") != "create":
                    raise RuntimeError(f"Phase 2 output C must be a new asset: {backup_manifest}")
                backup_a_sha = str(entries[output_a].get("revision", "")).removeprefix("sha256:")
                if backup_a_sha != phase1_a_sha:
                    raise RuntimeError("Phase 2 Backup did not capture the phase 1 output A revision.")

                validated = await call(
                    session,
                    "ue_validate_animation_retarget",
                    {"retargeter": retargeter_path, "animationPaths": phase2_outputs},
                )
                if not validated.get("ok"):
                    raise RuntimeError(f"Retarget validation failed: {validated}")

                saved = await call(
                    session,
                    "ue_save_animation_retarget_batch",
                    {
                        "taskId": phase2["taskId"],
                        "confirmation": f"SAVE RETARGET BATCH {phase2['taskId']}",
                        "changeSetId": change_set_id,
                    },
                )
                if saved.get("status") != "saved" or len(saved.get("savedAssets", [])) != 2:
                    raise RuntimeError(f"Retarget batch save failed: {saved}")
                if saved.get("updatedAssets") != [output_a]:
                    raise RuntimeError(f"Save did not report the overwritten asset: {saved}")

                verified = await call(
                    session,
                    "ue_verify_animation_retarget_batch",
                    {"taskId": phase2["taskId"]},
                )
                if not verified.get("verified"):
                    raise RuntimeError(f"Independent reload verification failed: {verified}")

                dry_run = await call(
                    session,
                    "ue_rollback_animation_retarget_batch",
                    {"taskId": phase2["taskId"], "mode": "DryRun"},
                )
                if dry_run.get("restoreCount") != 1 or dry_run.get("deleteCount") != 1:
                    raise RuntimeError(f"Rollback Dry Run plan is wrong: {dry_run}")
                rollback_receipt = dry_run.get("rollbackDryRunReceipt")
                rolled_back = await call(
                    session,
                    "ue_rollback_animation_retarget_batch",
                    {
                        "taskId": phase2["taskId"],
                        "mode": "Commit",
                        "rollbackDryRunReceipt": rollback_receipt,
                        "confirmation": f"ROLLBACK RETARGET BATCH {phase2['taskId']}",
                    },
                )
                if not rolled_back.get("valid") or rolled_back.get("verificationFailed"):
                    raise RuntimeError(f"Rollback Commit did not verify: {rolled_back}")

                # Gates: A restored to phase 1 state, C deleted, B untouched.
                restored_a_sha = sha256(project_asset_file(project_path, output_a))
                if restored_a_sha != phase1_a_sha:
                    raise RuntimeError(f"Rollback did not restore A to phase 1: {restored_a_sha} != {phase1_a_sha}")
                c_file = project_asset_file(project_path, output_c)
                if c_file.exists():
                    raise RuntimeError(f"Rollback did not delete the created output C: {c_file}")
                b_file = project_asset_file(project_path, output_b)
                if sha256(b_file) != phase1_b_sha:
                    raise RuntimeError("Rollback changed the untouched phase 1 output B.")

                return {
                    "protocolVersion": initialized.protocolVersion,
                    "toolCount": len(tool_names),
                    "planId": plan_id,
                    "changeSetId": change_set_id,
                    "retargeterAsset": retargeter_path,
                    "phase1TaskId": phase1["taskId"],
                    "phase2TaskId": phase2["taskId"],
                    "backupKinds": {k: v.get("kind") for k, v in entries.items()},
                    "validationVerdict": validated.get("verdict"),
                    "saveStatus": saved.get("status"),
                    "verifyVerified": verified.get("verified"),
                    "rollbackRestoredCount": rolled_back.get("restoredCount"),
                    "rollbackDeletedCount": rolled_back.get("deletedCount"),
                    "rollbackIndependentVerified": all(
                        item.get("verified") for item in rolled_back.get("independentVerification", [])
                    ),
                    "restoredAShaMatchesPhase1": restored_a_sha == phase1_a_sha,
                    "deletedC": not c_file.exists(),
                    "untouchedB": sha256(b_file) == phase1_b_sha,
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
    parser.add_argument(
        "--output-directory",
        default="/Game/UEAgentKitRetargetTests/ClosedLoop",
        help="Fresh /Game output directory for the closed-loop outputs.",
    )
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
ASSET_PATH = "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu.MM_Idle_XinYueHu"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


def scale_x(value: Any) -> float:
    if not isinstance(value, dict) or isinstance(value.get("x"), bool) or not isinstance(value.get("x"), (int, float)):
        raise RuntimeError(f"Expected scale object, received: {value!r}")
    return float(value["x"])


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


async def run(args: argparse.Namespace) -> dict[str, Any]:
    package_hash_before = sha256(args.package_file)
    database_hash_before = sha256(args.database)
    if args.root_track_scale_mode == "Uniform" and (args.uniform_scale is None or args.uniform_scale <= 0.0):
        raise RuntimeError("--uniform-scale must be greater than 0 for Uniform mode")
    root_track_tolerance = max(0.01, abs(args.expected_root_track_scale) * 0.01)

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
    applied_result: dict[str, Any] | None = None
    saved = False
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                try:
                    status = await call(session, "ue_editor_status", {})
                    if not status.get("ok") or status.get("result", {}).get("pieState") != "stopped":
                        raise RuntimeError(f"Live Editor is not ready: {status}")

                    opened = await call(session, "ue_open_asset", {"asset_path": ASSET_PATH})
                    if not opened.get("ok") or not opened.get("result", {}).get("openAfter"):
                        raise RuntimeError(f"Animation was not opened: {opened}")

                    inspected = await call(session, "ue_inspect_asset_live", {"asset_path": ASSET_PATH})
                    memory = inspected.get("result", {}).get("memory", {})
                    if not inspected.get("ok") or memory.get("packageDirty") is not False:
                        raise RuntimeError(f"Animation must be Clean before the save workflow: {inspected}")

                    plan_params: dict[str, Any] = {
                        "asset_path": ASSET_PATH,
                        "root_bone": "Root",
                        "expected_final_scale": 100.0,
                        "root_motion_root_lock": "RefPose",
                        "root_track_scale_mode": args.root_track_scale_mode,
                        "final_scale_tolerance": 1.0,
                        "description": args.description,
                    }
                    if args.root_track_scale_mode == "Uniform":
                        plan_params["uniform_scale"] = args.uniform_scale
                    plan = await call(
                        session,
                        "ue_plan_animation_scale_fix",
                        plan_params,
                    )
                    if not plan.get("ok") or plan.get("operation") != "setAnimationScaleFix":
                        raise RuntimeError(f"Animation scale fix Plan failed: {plan}")
                    plan_id = str(plan["planId"])

                    applied = await call(
                        session,
                        "ue_apply_asset_property_live",
                        {"plan_id": plan_id, "confirmation": f"LIVE APPLY {plan_id}"},
                    )
                    applied_result = applied.get("result", {})
                    after = applied_result.get("afterValue", {})
                    if (
                        not applied.get("ok")
                        or applied.get("changed") is not True
                        or applied_result.get("transactionRecorded") is not True
                        or after.get("forceRootLock") is not True
                        or after.get("rootMotionRootLock") != "RefPose"
                        or abs(scale_x(after.get("finalRootScale")) - 100.0) > 1.0
                        or abs(scale_x(after.get("rootTrackFirstScale")) - args.expected_root_track_scale) > root_track_tolerance
                        or applied_result.get("saved") is not False
                    ):
                        raise RuntimeError(f"Animation scale fix LiveApply result is invalid: {applied}")
                    if sha256(args.package_file) != package_hash_before:
                        raise RuntimeError("LiveApply changed the animation package before authorized save")

                    not_saved = await call(session, "ue_verify_live_write", {"asset_path": ASSET_PATH})
                    if (
                        not not_saved.get("ok")
                        or not_saved.get("state") != "not-saved"
                        or not_saved.get("saved") is not False
                        or not_saved.get("verified") is not False
                    ):
                        raise RuntimeError(f"Pre-save verification contract failed: {not_saved}")

                    preview = await call(
                        session,
                        "ue_save_authorized_asset",
                        {"asset_path": ASSET_PATH, "mode": "Preview"},
                    )
                    save_receipt = str(preview.get("saveReceipt", ""))
                    if not preview.get("ok") or not save_receipt:
                        raise RuntimeError(f"Authorized Save Preview failed: {preview}")

                    committed = await call(
                        session,
                        "ue_save_authorized_asset",
                        {
                            "asset_path": ASSET_PATH,
                            "mode": "Commit",
                            "save_receipt": save_receipt,
                            "confirmation": f"SAVE {save_receipt}",
                        },
                    )
                    if not committed.get("ok") or committed.get("saved") is not True:
                        raise RuntimeError(f"Authorized Save Commit failed: {committed}")
                    saved = True

                    package_hash_after = sha256(args.package_file)
                    if package_hash_after == package_hash_before:
                        raise RuntimeError("Authorized Save reported success but the package SHA did not change")
                    if sha256(args.database) != database_hash_before:
                        raise RuntimeError("Authorized Save modified the immutable SQLite index")

                    verified = await call(session, "ue_verify_live_write", {"asset_path": ASSET_PATH})
                    expected = verified.get("persistedExpectedValue", {})
                    exported = verified.get("exportedPersistedValue", {})
                    applied = verified.get("appliedValue", {})
                    runtime = verified.get("runtimeVerification", {})
                    persisted_fields = (
                        "rootBone",
                        "forceRootLock",
                        "enableRootMotion",
                        "useNormalizedRootMotionScale",
                        "rootMotionRootLock",
                        "additive",
                        "rootTrackExists",
                        "rootTrackKeyCount",
                        "rootTrackFirstScale",
                        "rootTrackMiddleScale",
                        "rootTrackLastScale",
                    )
                    if (
                        not verified.get("ok")
                        or verified.get("state") != "verified"
                        or verified.get("saved") is not True
                        or verified.get("verified") is not True
                        or any(expected.get(field) != exported.get(field) for field in persisted_fields)
                        or applied.get("finalEvaluationStatus") != "success"
                        or runtime.get("finalEvaluationStatus") != "success"
                        or abs(scale_x(runtime.get("finalRootScale")) - 100.0) > 1.0
                        or exported.get("forceRootLock") is not True
                        or exported.get("rootMotionRootLock") != "RefPose"
                        or abs(scale_x(exported.get("rootTrackFirstScale")) - args.expected_root_track_scale) > root_track_tolerance
                    ):
                        raise RuntimeError(f"Independent saved animation verification failed: {verified}")

                    return {
                        "assetPath": ASSET_PATH,
                        "planId": plan_id,
                        "saveReceipt": save_receipt,
                        "forceRootLock": exported.get("forceRootLock"),
                        "rootMotionRootLock": exported.get("rootMotionRootLock"),
                        "rootTrackFirstScale": scale_x(exported.get("rootTrackFirstScale")),
                        "liveFinalRootScale": scale_x(after.get("finalRootScale")),
                        "saved": True,
                        "verified": True,
                        "packageSha256Before": package_hash_before,
                        "packageSha256After": package_hash_after,
                        "actualRevision": verified.get("actualRevision"),
                        "backupManifest": committed.get("backupManifest") or committed.get("backupManifestPath"),
                        "databaseHashUnchanged": True,
                    }
                except BaseException:
                    if applied_result is not None and not saved:
                        transaction_id = str(applied_result.get("transactionId", ""))
                        editor_session_id = str(applied_result.get("editorSessionId", ""))
                        if transaction_id and editor_session_id:
                            await call(
                                session,
                                "ue_undo_asset_property_live",
                                {
                                    "asset_path": ASSET_PATH,
                                    "transaction_id": transaction_id,
                                    "editor_session_id": editor_session_id,
                                },
                            )
                    raise


def main() -> int:
    parser = argparse.ArgumentParser(description="Persist the verified XinYueHu Idle animation scale fix.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--package-file", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--root-track-scale-mode", choices=("ReferenceLocal", "Uniform"), required=True)
    parser.add_argument("--uniform-scale", type=float)
    parser.add_argument("--expected-root-track-scale", required=True, type=float)
    parser.add_argument("--description", default="Persist verified XinYueHu animation scale fix")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

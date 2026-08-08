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
    if not isinstance(value, dict):
        raise RuntimeError(f"Expected scale object, received: {value!r}")
    return float(value["x"])


async def run(args: argparse.Namespace) -> dict[str, Any]:
    package_hash_before = sha256(args.package_file)
    database_hash_before = sha256(args.database)
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
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                status = payload(await session.call_tool("ue_editor_status", {}), "ue_editor_status")
                if not status.get("ok") or status.get("result", {}).get("pieState") != "stopped":
                    raise RuntimeError(f"Live Editor is not ready: {status}")

                opened = payload(
                    await session.call_tool("ue_open_asset", {"asset_path": ASSET_PATH}),
                    "ue_open_asset",
                )
                if not opened.get("ok") or not opened.get("result", {}).get("openAfter"):
                    raise RuntimeError(f"Animation was not opened: {opened}")

                plan = payload(
                    await session.call_tool(
                        "ue_plan_animation_scale_fix",
                        {
                            "asset_path": ASSET_PATH,
                            "root_bone": "Root",
                            "expected_final_scale": 1.0,
                            "force_root_lock": False,
                            "root_motion_root_lock": "RefPose",
                            "root_track_scale_mode": "Keep",
                            "final_scale_tolerance": 1.0,
                            "description": "Live-only XinYueHu Root Lock inverse smoke test",
                        },
                    ),
                    "ue_plan_animation_scale_fix",
                )
                if not plan.get("ok") or plan.get("operation") != "setAnimationScaleFix":
                    raise RuntimeError(f"Animation scale fix Plan failed: {plan}")
                plan_id = str(plan["planId"])

                applied = payload(
                    await session.call_tool(
                        "ue_apply_asset_property_live",
                        {"plan_id": plan_id, "confirmation": f"LIVE APPLY {plan_id}"},
                    ),
                    "ue_apply_asset_property_live",
                )
                applied_evidence = args.error_log.with_name("animation-scale-fix-applied.json")
                applied_evidence.write_text(
                    json.dumps(applied, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                result = applied.get("result", {})
                before = result.get("beforeValue", {})
                after = result.get("afterValue", {})
                before_scale_value = before.get("finalRootScale")
                before_scale = scale_x(before_scale_value) if isinstance(before_scale_value, dict) else None
                after_scale = scale_x(after.get("finalRootScale"))
                if (
                    not applied.get("ok")
                    or applied.get("changed") is not True
                    or result.get("transactionRecorded") is not True
                    or before.get("forceRootLock") is not True
                    or after.get("forceRootLock") is not False
                    or abs(after_scale - 1.0) > 1.0
                    or after.get("rootTrackFirstScale", {}).get("x") != 1
                    or result.get("saved") is not False
                ):
                    raise RuntimeError(f"Animation scale fix LiveApply result is invalid: {applied}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("LiveApply changed the animation package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("LiveApply changed the immutable SQLite index")

                transaction_id = str(result.get("transactionId") or applied.get("transactionId") or "")
                editor_session_id = str(result.get("editorSessionId") or applied.get("editorSessionId") or "")
                if not transaction_id or not editor_session_id:
                    raise RuntimeError(f"LiveApply did not return Undo identity: {applied}")
                undone = payload(
                    await session.call_tool(
                        "ue_undo_asset_property_live",
                        {
                            "asset_path": ASSET_PATH,
                            "transaction_id": transaction_id,
                            "editor_session_id": editor_session_id,
                        },
                    ),
                    "ue_undo_asset_property_live",
                )
                undo_result = undone.get("result", {})
                undo_restored = undo_result.get("afterValue", {})
                if (
                    not undone.get("ok")
                    or undone.get("mode") != "LiveUndo"
                    or undone.get("changed") is not True
                    or undo_result.get("packageDirtyAfter") is not False
                    or undo_restored.get("forceRootLock") is not True
                    or abs(scale_x(undo_restored.get("finalRootScale")) - 100.0) > 1.0
                ):
                    raise RuntimeError(f"Animation scale fix Undo failed: {undone}")

                dirty = payload(await session.call_tool("ue_get_dirty_assets", {}), "ue_get_dirty_assets")
                dirty_items = dirty.get("result", {}).get("items", [])
                if any(
                    ASSET_PATH in item.get("assetPaths", [])
                    for item in dirty_items
                    if isinstance(item, dict) and isinstance(item.get("assetPaths"), list)
                ):
                    raise RuntimeError(f"Animation remained Dirty after Undo: {dirty}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("Undo test changed the animation package on disk")

                track_plan = payload(
                    await session.call_tool(
                        "ue_plan_animation_scale_fix",
                        {
                            "asset_path": ASSET_PATH,
                            "root_bone": "Root",
                            "expected_final_scale": 100.0,
                            "root_track_scale_mode": "ReferenceLocal",
                            "final_scale_tolerance": 1.0,
                            "description": "Live-only XinYueHu Root Scale Track smoke test",
                        },
                    ),
                    "ue_plan_animation_scale_fix root track",
                )
                if not track_plan.get("ok") or track_plan.get("operation") != "setAnimationScaleFix":
                    raise RuntimeError(f"Root Scale Track Plan failed: {track_plan}")
                track_plan_id = str(track_plan["planId"])

                track_applied = payload(
                    await session.call_tool(
                        "ue_apply_asset_property_live",
                        {
                            "plan_id": track_plan_id,
                            "confirmation": f"LIVE APPLY {track_plan_id}",
                        },
                    ),
                    "ue_apply_asset_property_live root track",
                )
                track_result = track_applied.get("result", {})
                track_before = track_result.get("beforeValue", {})
                track_after = track_result.get("afterValue", {})
                track_before_scale = scale_x(track_before.get("rootTrackFirstScale"))
                track_after_scale = scale_x(track_after.get("rootTrackFirstScale"))
                track_final_scale = scale_x(track_after.get("finalRootScale"))
                if (
                    not track_applied.get("ok")
                    or track_applied.get("changed") is not True
                    or track_result.get("transactionRecorded") is not True
                    or track_after.get("forceRootLock") is not True
                    or abs(track_before_scale - 1.0) > 0.01
                    or abs(track_after_scale - 100.0) > 1.0
                    or abs(scale_x(track_after.get("rootTrackMiddleScale")) - 100.0) > 1.0
                    or abs(scale_x(track_after.get("rootTrackLastScale")) - 100.0) > 1.0
                    or abs(track_final_scale - 100.0) > 1.0
                    or track_result.get("saved") is not False
                ):
                    raise RuntimeError(f"Root Scale Track LiveApply result is invalid: {track_applied}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("Root Scale Track LiveApply changed the package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Root Scale Track LiveApply changed the immutable SQLite index")

                track_transaction_id = str(track_result.get("transactionId") or "")
                track_editor_session_id = str(track_result.get("editorSessionId") or "")
                if not track_transaction_id or not track_editor_session_id:
                    raise RuntimeError(f"Root Scale Track LiveApply did not return Undo identity: {track_applied}")
                track_undone = payload(
                    await session.call_tool(
                        "ue_undo_asset_property_live",
                        {
                            "asset_path": ASSET_PATH,
                            "transaction_id": track_transaction_id,
                            "editor_session_id": track_editor_session_id,
                        },
                    ),
                    "ue_undo_asset_property_live root track",
                )
                track_undo_result = track_undone.get("result", {})
                track_restored = track_undo_result.get("afterValue", {})
                if (
                    not track_undone.get("ok")
                    or track_undone.get("mode") != "LiveUndo"
                    or track_undone.get("changed") is not True
                    or track_undo_result.get("packageDirtyAfter") is not False
                    or abs(scale_x(track_restored.get("rootTrackFirstScale")) - 1.0) > 0.01
                    or abs(scale_x(track_restored.get("finalRootScale")) - 100.0) > 1.0
                ):
                    raise RuntimeError(f"Root Scale Track Undo failed: {track_undone}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("Root Scale Track Undo changed the package on disk")

    return {
        "assetPath": ASSET_PATH,
        "planIdPresent": bool(plan_id),
        "beforeFinalRootScale": before_scale,
        "afterFinalRootScale": after_scale,
        "rootTrackScaleStayedAtOne": True,
        "forceRootLockChanged": True,
        "finalScaleVerifiedDuringApply": True,
        "undoSucceeded": True,
        "rootTrackBeforeScale": track_before_scale,
        "rootTrackAfterScale": track_after_scale,
        "rootTrackFinalRootScale": track_final_scale,
        "rootTrackUndoSucceeded": True,
        "saved": False,
        "diskPackageHashUnchanged": True,
        "databaseHashUnchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live-only Animation Scale Fix smoke test.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--package-file", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

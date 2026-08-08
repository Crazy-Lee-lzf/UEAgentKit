from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit import __version__  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorBridgeConfig, LiveEditorBridgeService  # noqa: E402

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


def root_track(asset: dict[str, Any]) -> dict[str, Any]:
    tracks = asset.get("tracks", [])
    if not isinstance(tracks, list):
        raise RuntimeError(f"Animation diagnosis has no track list: {asset}")
    for item in tracks:
        if isinstance(item, dict) and item.get("bone") == "Root":
            return item
    raise RuntimeError(f"Animation diagnosis has no Root track: {asset}")


def evaluated_root_scale(asset: dict[str, Any]) -> float:
    samples = asset.get("previewSamples", [])
    if not isinstance(samples, list) or not samples:
        raise RuntimeError(f"Animation diagnosis has no preview samples: {asset}")
    middle = samples[len(samples) // 2]
    if not isinstance(middle, dict):
        raise RuntimeError(f"Animation diagnosis has invalid preview sample: {middle}")
    bones = middle.get("bones", [])
    if not isinstance(bones, list):
        raise RuntimeError(f"Animation diagnosis has no preview bones: {middle}")
    for bone in bones:
        if isinstance(bone, dict) and bone.get("bone") == "Root":
            return scale_x(bone.get("componentScale"))
    raise RuntimeError(f"Animation diagnosis has no evaluated Root: {middle}")


def write_audit_report(work_root: Path, asset: dict[str, Any]) -> tuple[str, str]:
    task_id = str(uuid.uuid4())
    track = root_track(asset)
    report = {
        "schemaVersion": "1.0",
        "reportType": "animation-scale-audit",
        "task": {
            "taskId": task_id,
            "state": "completed",
        },
        "summary": {
            "fixture": "batch-live-smoke",
        },
        "items": [
            {
                "assetPath": ASSET_PATH,
                "classification": "root-track-candidate",
                "rootBone": "Root",
                "rootTrack": {
                    "referenceComponentScale": track.get("referenceComponentScale"),
                },
            }
        ],
    }
    data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    directory = work_root / "animation-scale-audits" / task_id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "report.json"
    path.write_bytes(data)
    return task_id, "sha256:" + hashlib.sha256(data).hexdigest()


async def call(session: ClientSession, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, arguments), tool)


def diagnose(bridge: LiveEditorBridgeService) -> dict[str, Any]:
    result = bridge.call_tool(
        "ue_diagnose_animation_scale",
        {
            "animationPaths": [ASSET_PATH],
            "boneNames": ["Root", "pelvis"],
            "loadIfNeeded": True,
        },
    )
    assets = result.get("result", {}).get("assets", [])
    if not result.get("ok") or not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
        raise RuntimeError(f"Animation diagnosis failed: {result}")
    return assets[0]


async def run(args: argparse.Namespace) -> dict[str, Any]:
    package_hash_before = sha256(args.package_file)
    database_hash_before = sha256(args.database)
    bridge = LiveEditorBridgeService(
        LiveEditorBridgeConfig(project_path=args.project, timeout_seconds=60.0),
        server_version=__version__,
    )
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
                status = await call(session, "ue_editor_status", {})
                if not status.get("ok") or status.get("result", {}).get("pieState") != "stopped":
                    raise RuntimeError(f"Live Editor is not ready: {status}")

                opened = await call(session, "ue_open_asset", {"asset_path": ASSET_PATH})
                if not opened.get("ok") or not opened.get("result", {}).get("openAfter"):
                    raise RuntimeError(f"Animation was not opened: {opened}")

                baseline = diagnose(bridge)
                baseline_track_scale = scale_x(root_track(baseline).get("firstScale"))
                reference_scale = scale_x(root_track(baseline).get("referenceComponentScale"))
                baseline_final_scale = evaluated_root_scale(baseline)
                if abs(baseline_track_scale - 1.0) > 0.01 or abs(baseline_final_scale - reference_scale) > 1.0:
                    raise RuntimeError(
                        "Unexpected persisted smoke baseline: "
                        f"track={baseline_track_scale}, reference={reference_scale}, final={baseline_final_scale}"
                    )

                audit_task_id, audit_report_id = write_audit_report(args.work_root, baseline)
                plan = await call(
                    session,
                    "ue_plan_animation_scale_fix_batch",
                    {
                        "audit_task_id": audit_task_id,
                        "audit_report_id": audit_report_id,
                        "asset_paths": [ASSET_PATH],
                        "final_scale_tolerance": 1.0,
                        "description": "Live-only XinYueHu animation scale fix Batch smoke test",
                    },
                )
                if not plan.get("ok") or plan.get("assetCount") != 1:
                    raise RuntimeError(f"Batch Plan failed: {plan}")
                batch_plan_id = str(plan.get("batchPlanId") or "")
                plan_item = plan.get("items", [{}])[0]
                if (
                    not batch_plan_id
                    or plan_item.get("strategy") != "reference-local-root-track"
                    or abs(float(plan_item.get("expectedFinalScale")) - reference_scale) > 0.01
                ):
                    raise RuntimeError(f"Batch Plan derived an unexpected repair: {plan}")

                applied = await call(
                    session,
                    "ue_apply_animation_scale_fix_batch_live",
                    {
                        "batch_plan_id": batch_plan_id,
                        "confirmation": f"LIVE APPLY BATCH {batch_plan_id}",
                        "max_assets": 1,
                    },
                )
                execution = applied.get("execution", {})
                execution_items = execution.get("items", [])
                if (
                    not applied.get("ok")
                    or execution.get("state") != "applied"
                    or execution.get("progress", {}).get("appliedAssets") != 1
                    or not execution.get("changeSetId")
                    or not execution.get("batchApplyReceipt")
                    or len(execution_items) != 1
                    or execution_items[0].get("runtimeVerified") is not True
                    or execution_items[0].get("runtimeVerification", {}).get("finalEvaluationStatus") != "success"
                    or abs(scale_x(execution_items[0].get("runtimeVerification", {}).get("finalRootScale")) - reference_scale) > 1.0
                ):
                    raise RuntimeError(f"Batch Live Apply result is invalid: {applied}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("Batch Live Apply changed the animation package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Batch Live Apply changed the immutable SQLite index")

                polled = await call(
                    session,
                    "ue_get_animation_scale_fix_batch",
                    {"batch_plan_id": batch_plan_id},
                )
                if polled.get("execution", {}).get("state") != "applied":
                    raise RuntimeError(f"Read-only Batch Get lost execution state: {polled}")

                after_apply = diagnose(bridge)
                applied_track_scale = scale_x(root_track(after_apply).get("firstScale"))
                applied_final_scale = evaluated_root_scale(after_apply)
                if (
                    abs(applied_track_scale - reference_scale) > 1.0
                    or abs(applied_final_scale - reference_scale) > 1.0
                ):
                    raise RuntimeError(
                        "Batch Live Apply did not produce the expected Root Track / final scale: "
                        f"track={applied_track_scale}, final={applied_final_scale}, reference={reference_scale}"
                    )

                undone = await call(
                    session,
                    "ue_undo_animation_scale_fix_batch",
                    {
                        "batch_plan_id": batch_plan_id,
                        "confirmation": f"UNDO BATCH {batch_plan_id}",
                        "max_assets": 1,
                    },
                )
                undo_execution = undone.get("execution", {})
                if (
                    not undone.get("ok")
                    or undo_execution.get("state") != "undone"
                    or undo_execution.get("progress", {}).get("undoneAssets") != 1
                    or not undo_execution.get("undo", {}).get("batchUndoReceipt")
                ):
                    raise RuntimeError(f"Batch Undo result is invalid: {undone}")

                restored = diagnose(bridge)
                restored_track_scale = scale_x(root_track(restored).get("firstScale"))
                restored_final_scale = evaluated_root_scale(restored)
                if (
                    abs(restored_track_scale - baseline_track_scale) > 0.01
                    or abs(restored_final_scale - baseline_final_scale) > 1.0
                ):
                    raise RuntimeError(
                        "Batch Undo did not restore the animation: "
                        f"track={restored_track_scale}, final={restored_final_scale}"
                    )

                dirty = await call(session, "ue_get_dirty_assets", {})
                dirty_items = dirty.get("result", {}).get("items", [])
                if any(
                    ASSET_PATH in item.get("assetPaths", [])
                    for item in dirty_items
                    if isinstance(item, dict) and isinstance(item.get("assetPaths"), list)
                ):
                    raise RuntimeError(f"Animation remained Dirty after Batch Undo: {dirty}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("Batch Apply/Undo changed the animation package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Batch Apply/Undo changed the immutable SQLite index")

    return {
        "assetPath": ASSET_PATH,
        "referenceScale": reference_scale,
        "baselineRootTrackScale": baseline_track_scale,
        "appliedRootTrackScale": applied_track_scale,
        "restoredRootTrackScale": restored_track_scale,
        "runtimeFinalScale": applied_final_scale,
        "batchPlanId": batch_plan_id,
        "changeSetId": execution.get("changeSetId"),
        "batchUndoSucceeded": True,
        "saved": False,
        "diskPackageHashUnchanged": True,
        "databaseHashUnchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live-only Animation Scale Fix Batch Apply/Undo smoke test.")
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

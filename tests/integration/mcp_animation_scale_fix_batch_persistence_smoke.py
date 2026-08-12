from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import subprocess
import sys
import time
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
    if value.get("ok") is False:
        raise RuntimeError(f"{tool} failed: {value}")
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
    bones = middle.get("bones", []) if isinstance(middle, dict) else []
    for bone in bones if isinstance(bones, list) else []:
        if isinstance(bone, dict) and bone.get("bone") == "Root":
            return scale_x(bone.get("componentScale"))
    raise RuntimeError(f"Animation diagnosis has no evaluated Root: {middle}")


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


def write_audit_report(work_root: Path, asset: dict[str, Any]) -> tuple[str, str]:
    task_id = str(uuid.uuid4())
    track = root_track(asset)
    report = {
        "schemaVersion": "1.0",
        "reportType": "animation-scale-audit",
        "task": {"taskId": task_id, "state": "completed"},
        "summary": {"fixture": "batch-persistence-smoke"},
        "items": [
            {
                "assetPath": ASSET_PATH,
                "classification": "root-track-candidate",
                "rootBone": "Root",
                "rootTrack": {"referenceComponentScale": track.get("referenceComponentScale")},
            }
        ],
    }
    data = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    directory = work_root / "animation-scale-audits" / task_id
    directory.mkdir(parents=True, exist_ok=False)
    path = directory / "report.json"
    path.write_bytes(data)
    return task_id, "sha256:" + hashlib.sha256(data).hexdigest()


def close_editor(process_id: int) -> None:
    if process_id <= 0:
        return
    subprocess.run(
        ["taskkill.exe", "/PID", str(process_id), "/T", "/F"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(40):
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {process_id}", "/NH"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if str(process_id) not in result.stdout:
            return
        time.sleep(0.25)
    raise RuntimeError(f"Unreal Editor PID {process_id} did not exit")


async def call(session: ClientSession, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, arguments), tool)


async def emergency_rollback(
    session: ClientSession,
    batch_plan_id: str,
    editor_process_id: int,
) -> None:
    if not batch_plan_id:
        return
    close_editor(editor_process_id)
    try:
        state = await call(session, "ue_get_animation_scale_fix_batch", {"batch_plan_id": batch_plan_id})
        execution = state.get("execution", {})
        if execution.get("progress", {}).get("unsavedAssets", 0):
            undo = await call(
                session,
                "ue_undo_animation_scale_fix_batch",
                {
                    "batch_plan_id": batch_plan_id,
                    "confirmation": f"UNDO BATCH {batch_plan_id}",
                    "max_assets": 8,
                },
            )
            undo_receipt = str(undo.get("execution", {}).get("undo", {}).get("batchUndoReceipt") or "")
            while undo.get("execution", {}).get("state") == "undoing":
                undo = await call(
                    session,
                    "ue_undo_animation_scale_fix_batch",
                    {
                        "batch_plan_id": batch_plan_id,
                        "batch_undo_receipt": undo_receipt,
                        "max_assets": 8,
                    },
                )
        state = await call(session, "ue_get_animation_scale_fix_batch", {"batch_plan_id": batch_plan_id})
        saved = state.get("execution", {}).get("progress", {}).get("savedAssets", 0)
        if not saved:
            return
        rollback = await call(
            session,
            "ue_rollback_animation_scale_fix_batch",
            {"batch_plan_id": batch_plan_id, "mode": "DryRun", "max_assets": 2},
        )
        receipt = str(rollback.get("execution", {}).get("rollback", {}).get("batchRollbackReceipt") or "")
        while rollback.get("execution", {}).get("state") == "rollback_dry_run":
            rollback = await call(
                session,
                "ue_rollback_animation_scale_fix_batch",
                {
                    "batch_plan_id": batch_plan_id,
                    "mode": "DryRun",
                    "batch_rollback_receipt": receipt,
                    "max_assets": 2,
                },
            )
        while rollback.get("execution", {}).get("state") != "rolled_back":
            rollback = await call(
                session,
                "ue_rollback_animation_scale_fix_batch",
                {
                    "batch_plan_id": batch_plan_id,
                    "mode": "Commit",
                    "batch_rollback_receipt": receipt,
                    "confirmation": f"ROLLBACK BATCH {batch_plan_id}",
                    "max_assets": 2,
                },
            )
    except Exception:
        return


async def run(args: argparse.Namespace) -> dict[str, Any]:
    package_hash_before = sha256(args.package_file)
    expected_baseline = args.expected_package_sha256.lower().removeprefix("sha256:")
    if expected_baseline and package_hash_before.lower() != expected_baseline:
        raise RuntimeError(
            f"Unexpected package baseline: expected {expected_baseline}, got {package_hash_before}"
        )
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
    batch_plan_id = ""
    editor_process_id = 0
    temporary_revision = ""
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                try:
                    status = await call(session, "ue_editor_status", {})
                    editor_process_id = int(status.get("result", {}).get("processId") or 0)
                    if not editor_process_id or status.get("result", {}).get("pieState") != "stopped":
                        raise RuntimeError(f"Live Editor is not ready: {status}")
                    opened = await call(session, "ue_open_asset", {"asset_path": ASSET_PATH})
                    if not opened.get("result", {}).get("openAfter"):
                        raise RuntimeError(f"Animation was not opened: {opened}")

                    baseline = diagnose(bridge)
                    baseline_track = scale_x(root_track(baseline).get("firstScale"))
                    reference_scale = scale_x(root_track(baseline).get("referenceComponentScale"))
                    if abs(baseline_track - 1.0) > 0.01:
                        raise RuntimeError(f"Unexpected baseline Root Track scale: {baseline_track}")
                    audit_task_id, audit_report_id = write_audit_report(args.work_root, baseline)
                    plan = await call(
                        session,
                        "ue_plan_animation_scale_fix_batch",
                        {
                            "audit_task_id": audit_task_id,
                            "audit_report_id": audit_report_id,
                            "asset_paths": [ASSET_PATH],
                            "final_scale_tolerance": 1.0,
                            "description": "XinYueHu Batch persistence and rollback smoke",
                        },
                    )
                    batch_plan_id = str(plan.get("batchPlanId") or "")
                    if not batch_plan_id:
                        raise RuntimeError(f"Batch Plan returned no id: {plan}")
                    applied = await call(
                        session,
                        "ue_apply_animation_scale_fix_batch_live",
                        {
                            "batch_plan_id": batch_plan_id,
                            "confirmation": f"LIVE APPLY BATCH {batch_plan_id}",
                            "max_assets": 1,
                        },
                    )
                    if applied.get("execution", {}).get("state") != "applied":
                        raise RuntimeError(f"Batch Apply did not complete: {applied}")
                    if sha256(args.package_file) != package_hash_before:
                        raise RuntimeError("Live Apply changed package bytes before Save")

                    saved = await call(
                        session,
                        "ue_save_animation_scale_fix_batch",
                        {
                            "batch_plan_id": batch_plan_id,
                            "confirmation": f"SAVE BATCH {batch_plan_id}",
                            "max_assets": 1,
                        },
                    )
                    save_execution = saved.get("execution", {})
                    if save_execution.get("state") != "saved":
                        raise RuntimeError(f"Batch Save did not complete: {saved}")
                    item = save_execution.get("items", [{}])[0]
                    if item.get("rollbackAvailable") is not True or item.get("saveState") != "saved":
                        raise RuntimeError(f"Batch Save did not create rollback safety: {saved}")
                    temporary_revision = str(item.get("afterRevision") or "")
                    package_hash_saved = sha256(args.package_file)
                    if package_hash_saved == package_hash_before:
                        raise RuntimeError("Batch Save did not change the package Revision")
                    if temporary_revision != "sha256:" + package_hash_saved:
                        raise RuntimeError(
                            f"Batch Save Revision mismatch: {temporary_revision} vs sha256:{package_hash_saved}"
                        )
                    if sha256(args.database) != database_hash_before:
                        raise RuntimeError("Batch Save changed the immutable SQLite index")

                    verified = await call(
                        session,
                        "ue_verify_animation_scale_fix_batch",
                        {"batch_plan_id": batch_plan_id, "max_assets": 1},
                    )
                    verify_execution = verified.get("execution", {})
                    if verify_execution.get("state") != "verified":
                        raise RuntimeError(f"Batch Verify did not complete: {verified}")
                    verified_item = verify_execution.get("items", [{}])[0]
                    if (
                        verified_item.get("verifyState") != "verified"
                        or verified_item.get("actualRevision") != temporary_revision
                    ):
                        raise RuntimeError(f"Batch Verify returned inconsistent Revision: {verified}")


                    index_preview = await call(
                        session,
                        "ue_refresh_animation_scale_fix_batch_index",
                        {
                            "batch_plan_id": batch_plan_id,
                            "mode": "Preview",
                            "max_assets": 1,
                        },
                    )
                    index_execution = index_preview.get("execution", {})
                    index_state = index_execution.get("indexRefresh", {})
                    if (
                        index_execution.get("state") != "index_refresh_ready"
                        or index_state.get("processedAssets") != 1
                        or index_state.get("preparedAssets") != 1
                        or index_state.get("applied") is not False
                        or index_state.get("restartRequired") is not False
                        or not index_state.get("batchIndexRefreshReceipt")
                    ):
                        raise RuntimeError(f"Batch Index Refresh Preview did not become ready: {index_preview}")
                    if sha256(args.package_file) != package_hash_saved:
                        raise RuntimeError("Batch Index Refresh Preview changed package bytes")
                    if sha256(args.database) != database_hash_before:
                        raise RuntimeError("Batch Index Refresh Preview changed the immutable SQLite index")
                    close_editor(editor_process_id)
                    rollback = await call(
                        session,
                        "ue_rollback_animation_scale_fix_batch",
                        {"batch_plan_id": batch_plan_id, "mode": "DryRun", "max_assets": 1},
                    )
                    rollback_execution = rollback.get("execution", {})
                    rollback_receipt = str(
                        rollback_execution.get("rollback", {}).get("batchRollbackReceipt") or ""
                    )
                    if rollback_execution.get("state") != "rollback_ready" or not rollback_receipt:
                        raise RuntimeError(f"Batch Rollback DryRun did not become ready: {rollback}")
                    if sha256(args.package_file) != package_hash_saved:
                        raise RuntimeError("Rollback DryRun wrote package bytes")
                    rollback_item = rollback_execution.get("items", [{}])[0]
                    if rollback_item.get("indexRefreshState") != "discarded":
                        raise RuntimeError(f"Rollback did not discard Index Refresh Preview candidate: {rollback}")

                    restored = await call(
                        session,
                        "ue_rollback_animation_scale_fix_batch",
                        {
                            "batch_plan_id": batch_plan_id,
                            "mode": "Commit",
                            "batch_rollback_receipt": rollback_receipt,
                            "confirmation": f"ROLLBACK BATCH {batch_plan_id}",
                            "max_assets": 1,
                        },
                    )
                    restored_execution = restored.get("execution", {})
                    if restored_execution.get("state") != "rolled_back":
                        raise RuntimeError(f"Batch Rollback Commit did not complete: {restored}")
                    restored_item = restored_execution.get("items", [{}])[0]
                    if (
                        restored_item.get("rollbackState") != "rolled-back"
                        or restored_item.get("restoredRevision") != "sha256:" + package_hash_before
                    ):
                        raise RuntimeError(f"Batch Rollback restored the wrong Revision: {restored}")
                    if sha256(args.package_file) != package_hash_before:
                        raise RuntimeError("Batch Rollback did not restore exact package bytes")
                    if sha256(args.database) != database_hash_before:
                        raise RuntimeError("Batch persistence/rollback changed the immutable SQLite index")
                except Exception:
                    if batch_plan_id and sha256(args.package_file) != package_hash_before:
                        await emergency_rollback(session, batch_plan_id, editor_process_id)
                    raise

    if sha256(args.package_file) != package_hash_before:
        raise RuntimeError("Persistence smoke exited without restoring the package baseline")
    return {
        "assetPath": ASSET_PATH,
        "batchPlanId": batch_plan_id,
        "referenceScale": reference_scale,
        "temporaryRevision": temporary_revision,
        "restoredRevision": "sha256:" + package_hash_before,
        "saved": True,
        "independentVerifySucceeded": True,
        "indexRefreshPreviewSucceeded": True,
        "indexRefreshPreviewZeroDatabaseWrite": True,
        "rollbackDryRunZeroWrite": True,
        "rollbackCommitSucceeded": True,
        "diskPackageRestoredExactly": True,
        "databaseHashUnchanged": True,
        "editorClosedBeforeRollbackCommit": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Animation Scale Fix Batch Save/Verify/Index Preview/Rollback smoke test.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--package-file", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--expected-package-sha256", default="")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

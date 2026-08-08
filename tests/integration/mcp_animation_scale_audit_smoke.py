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
DEFAULT_ASSET = "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu.MM_Idle_XinYueHu"


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
    x = value.get("x")
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        raise RuntimeError(f"Expected numeric x scale, received: {value!r}")
    return float(x)


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


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
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                required = {
                    "ue_start_animation_scale_audit",
                    "ue_get_animation_scale_audit",
                    "ue_cancel_animation_scale_audit",
                    "ue_export_animation_scale_audit_report",
                }
                if not required.issubset(names):
                    raise RuntimeError(f"Animation scale audit tools are missing: {sorted(required - names)}")

                started = await call(
                    session,
                    "ue_start_animation_scale_audit",
                    {
                        "pathPrefix": "/Game/Characters/XinYueHu/Animations/Retargeted",
                        "boneNames": ["Root", "pelvis"],
                        "loadIfNeeded": True,
                        "batchSize": 1,
                    },
                )
                if not started.get("ok"):
                    raise RuntimeError(f"Animation scale audit start failed: {started}")
                task = started.get("result", {})
                task_id = str(task.get("taskId") or "")
                candidate_selection = task.get("candidateSelection", {})
                if (
                    not task_id
                    or task.get("state") != "running"
                    or task.get("candidateSource") != "immutable-index"
                    or not candidate_selection.get("indexSnapshotId")
                ):
                    raise RuntimeError(f"Animation scale audit start result is invalid: {started}")

                completed = await call(
                    session,
                    "ue_get_animation_scale_audit",
                    {
                        "taskId": task_id,
                        "detailOffset": 0,
                        "detailLimit": 20,
                        "classificationFilter": ["normal"],
                        "sortBy": "asset-path",
                    },
                )
                result = completed.get("result", {})
                items = result.get("details", {}).get("items", [])
                if (
                    not completed.get("ok")
                    or result.get("state") != "completed"
                    or result.get("progress", {}).get("processedAssets") != 1
                    or len(items) != 1
                ):
                    raise RuntimeError(f"Animation scale audit did not complete: {completed}")
                item = items[0]
                if item.get("assetPath") != args.asset or item.get("classification") != "normal":
                    raise RuntimeError(f"Unexpected animation audit classification: {item}")
                root_track = item.get("rootTrack", {})
                pose_samples = item.get("poseSamples", [])
                if abs(scale_x(root_track.get("firstScale")) - 1.0) > 0.01:
                    raise RuntimeError(f"Unexpected persisted Root Track scale: {root_track}")
                if not pose_samples or abs(scale_x(pose_samples[0].get("rootScale")) - 100.0) > 1.0:
                    raise RuntimeError(f"Unexpected evaluated Root scale: {pose_samples}")

                exported = await call(
                    session,
                    "ue_export_animation_scale_audit_report",
                    {
                        "taskId": task_id,
                        "classificationFilter": ["normal"],
                        "sortBy": "asset-path",
                    },
                )
                report_result = exported.get("result", {})
                report_relative_path = str(report_result.get("reportRelativePath") or "")
                report_path = args.work_root / report_relative_path
                if (
                    not exported.get("ok")
                    or report_result.get("itemCount") != 1
                    or not report_relative_path.startswith("animation-scale-audits/")
                    or not report_path.is_file()
                    or report_result.get("reportId") != f"sha256:{sha256(report_path)}"
                ):
                    raise RuntimeError(f"Animation scale audit report export failed: {exported}")
                report_payload = json.loads(report_path.read_text(encoding="utf-8"))
                if report_payload.get("items", [{}])[0].get("assetPath") != args.asset:
                    raise RuntimeError(f"Animation scale audit report has unexpected content: {report_payload}")

                cancel_started = await call(
                    session,
                    "ue_start_animation_scale_audit",
                    {
                        "animationPaths": [args.asset],
                        "boneNames": ["Root"],
                        "loadIfNeeded": True,
                        "batchSize": 1,
                    },
                )
                cancel_task_id = str(cancel_started.get("result", {}).get("taskId") or "")
                cancelled = await call(
                    session,
                    "ue_cancel_animation_scale_audit",
                    {"taskId": cancel_task_id},
                )
                cancel_result = cancelled.get("result", {})
                if (
                    not cancelled.get("ok")
                    or cancel_result.get("state") != "cancelled"
                    or cancel_result.get("progress", {}).get("processedAssets") != 0
                ):
                    raise RuntimeError(f"Animation scale audit cancel failed: {cancelled}")

    if sha256(args.package_file) != package_hash_before:
        raise RuntimeError("Read-only animation audit changed the animation package on disk")
    if sha256(args.database) != database_hash_before:
        raise RuntimeError("Read-only animation audit changed the immutable SQLite index")

    return {
        "assetPath": args.asset,
        "classification": item.get("classification"),
        "rootTrackFirstScale": scale_x(root_track.get("firstScale")),
        "evaluatedRootScale": scale_x(pose_samples[0].get("rootScale")),
        "cancelSucceeded": True,
        "reportId": report_result.get("reportId"),
        "reportRelativePath": report_relative_path,
        "diskPackageHashUnchanged": True,
        "databaseHashUnchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only animation scale audit MCP smoke test.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--package-file", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--asset", default=DEFAULT_ASSET)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

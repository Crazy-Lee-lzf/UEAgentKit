from __future__ import annotations

import argparse
import asyncio
import json
import math
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]

ADDITIVE_PATHS = [
    (
        "/Game/Characters/XinYueHu/Animations/Retargeted/"
        "MM_Pistol_Jump_RecoveryAdditive_XinYueHu.MM_Pistol_Jump_RecoveryAdditive_XinYueHu"
    ),
    (
        "/Game/Characters/XinYueHu/Animations/Retargeted/"
        "MM_Rifle_Jump_RecoveryAdditive_XinYueHu.MM_Rifle_Jump_RecoveryAdditive_XinYueHu"
    ),
]

ROOT_BONE = "Root"


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


def scale_x(value: Any) -> float:
    if not isinstance(value, dict) or not all(
        isinstance(value.get(axis), (int, float)) and math.isfinite(value[axis]) for axis in ("x", "y", "z")
    ):
        raise RuntimeError(f"Expected a finite scale object, received: {value!r}")
    return float(value["x"])


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

    per_asset: list[dict[str, Any]] = []
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                for asset_path in ADDITIVE_PATHS:
                    opened = payload(
                        await session.call_tool("ue_open_asset", {"asset_path": asset_path}),
                        "ue_open_asset",
                    )
                    if not opened.get("ok") or not opened.get("result", {}).get("openAfter"):
                        raise RuntimeError(f"Animation was not opened: {opened}")

                    plan = payload(
                        await session.call_tool(
                            "ue_plan_animation_scale_fix",
                            {
                                "asset_path": asset_path,
                                "root_bone": ROOT_BONE,
                                "expected_final_scale": 1.0,
                                "root_track_scale_mode": "Uniform",
                                "uniform_scale": 1.0,
                                "final_scale_tolerance": 0.5,
                                "description": "Additive XinYueHu root scale fix smoke",
                            },
                        ),
                        "ue_plan_animation_scale_fix",
                    )
                    if not plan.get("ok") or plan.get("operation") != "setAnimationScaleFix":
                        raise RuntimeError(f"Additive scale fix Plan failed: {plan}")
                    plan_id = str(plan["planId"])

                    applied = payload(
                        await session.call_tool(
                            "ue_apply_asset_property_live",
                            {"plan_id": plan_id, "confirmation": f"LIVE APPLY {plan_id}"},
                        ),
                        "ue_apply_asset_property_live",
                    )
                    result = applied.get("result", {})
                    before = result.get("beforeValue", {})
                    after = result.get("afterValue", {})
                    before_scale = scale_x(before.get("finalRootScale"))
                    after_scale = scale_x(after.get("finalRootScale"))
                    if (
                        not applied.get("ok")
                        or applied.get("changed") is not True
                        or result.get("transactionRecorded") is not True
                        or abs(after_scale - 1.0) > 0.5
                        or result.get("saved") is not False
                    ):
                        raise RuntimeError(f"Additive scale fix LiveApply result is invalid: {applied}")

                    transaction_id = str(result.get("transactionId") or applied.get("transactionId") or "")
                    editor_session_id = str(result.get("editorSessionId") or applied.get("editorSessionId") or "")
                    if not transaction_id or not editor_session_id:
                        raise RuntimeError(f"LiveApply did not return Undo identity: {applied}")

                    undone = payload(
                        await session.call_tool(
                            "ue_undo_asset_property_live",
                            {
                                "asset_path": asset_path,
                                "transaction_id": transaction_id,
                                "editor_session_id": editor_session_id,
                            },
                        ),
                        "ue_undo_asset_property_live",
                    )
                    undo_result = undone.get("result", {})
                    if (
                        not undone.get("ok")
                        or undone.get("mode") != "LiveUndo"
                        or undone.get("changed") is not True
                        or undo_result.get("packageDirtyAfter") is not False
                    ):
                        raise RuntimeError(f"Additive scale fix Undo failed: {undone}")

                    per_asset.append(
                        {
                            "assetPath": asset_path,
                            "beforeFinalRootScale": before_scale,
                            "afterFinalRootScale": after_scale,
                            "rootTrackFirstScaleAfter": after.get("rootTrackFirstScale"),
                            "finalEvaluationStatusAfter": after.get("finalEvaluationStatus"),
                            "undoSucceeded": True,
                            "saved": False,
                        }
                    )

    return {"assets": per_asset, "ok": len(per_asset) == len(ADDITIVE_PATHS)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Live-only Additive scale fix smoke.")
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\r\n",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]

ADDITIVE = (
    "/Game/Characters/XinYueHu/Animations/Retargeted/"
    "MM_Rifle_Jump_RecoveryAdditive_XinYueHu.MM_Rifle_Jump_RecoveryAdditive_XinYueHu"
)
IDLE = (
    "/Game/Characters/XinYueHu/Animations/Retargeted/"
    "MM_Idle_XinYueHu.MM_Idle_XinYueHu"
)


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


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
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()

                opened = payload(await session.call_tool("ue_open_asset", {"asset_path": ADDITIVE}), "ue_open_asset")
                if not opened.get("ok") or not opened.get("result", {}).get("openAfter"):
                    raise RuntimeError(f"additive was not opened: {opened}")

                plan = payload(
                    await session.call_tool(
                        "ue_plan_additive_base_pose_fix_apply",
                        {
                            "asset_path": ADDITIVE,
                            "ref_sequence_path": IDLE,
                            "ref_frame_index": 0,
                            "description": "replace self-referential base with retargeted idle",
                        },
                    ),
                    "ue_plan_additive_base_pose_fix_apply",
                )
                if not plan.get("ok") or plan.get("operation") != "setAdditiveBasePoseFix":
                    raise RuntimeError(f"base pose fix Plan failed: {plan}")
                plan_id = str(plan["planId"])

                applied = payload(
                    await session.call_tool(
                        "ue_apply_asset_property_live",
                        {"plan_id": plan_id, "confirmation": f"LIVE APPLY {plan_id}"},
                    ),
                    "ue_apply_asset_property_live",
                )
                if not applied.get("ok") or applied.get("changed") is not True:
                    raise RuntimeError(f"base pose fix apply failed: {applied}")
                result = applied.get("result", {})
                before = result.get("beforeValue", {})
                after = result.get("afterValue", {})
                transaction_id = str(result.get("transactionId") or applied.get("transactionId") or "")
                editor_session_id = str(result.get("editorSessionId") or applied.get("editorSessionId") or "")
                if not transaction_id or not editor_session_id:
                    raise RuntimeError(f"apply did not return undo identity: {applied}")

                undone = payload(
                    await session.call_tool(
                        "ue_undo_asset_property_live",
                        {
                            "asset_path": ADDITIVE,
                            "transaction_id": transaction_id,
                            "editor_session_id": editor_session_id,
                        },
                    ),
                    "ue_undo_asset_property_live",
                )
                if not undone.get("ok") or undone.get("mode") != "LiveUndo":
                    raise RuntimeError(f"base pose fix undo failed: {undone}")

    return {
        "assetPath": ADDITIVE,
        "basePosePath": IDLE,
        "beforeRefSequencePath": before.get("refSequencePath"),
        "afterRefSequencePath": after.get("refSequencePath"),
        "beforeRefFrameIndex": before.get("refFrameIndex"),
        "afterRefFrameIndex": after.get("refFrameIndex"),
        "beforeBasePoseType": before.get("additiveBasePoseType"),
        "afterBasePoseType": after.get("additiveBasePoseType"),
        "saved": result.get("saved"),
        "undoSucceeded": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Additive base pose fix write smoke.")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

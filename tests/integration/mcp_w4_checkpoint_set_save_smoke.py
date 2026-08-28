from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

BP_ASSET = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint"
DA_ASSET = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset"
PIN_GRAPH = "12345678-9abc-def0-1234-56789abcdef0"
PIN_NODE = "11111111-2222-2222-3333-333344444444"

BP_OPERATIONS = [
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 42},
    {
        "operation": "setComponentProperty",
        "target": {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        "value": 10,
    },
    {
        "operation": "setPinDefault",
        "target": {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": "A"},
        "value": 7,
    },
]
DA_OPERATIONS = [
    {"operation": "setAssetProperty", "target": {"propertyPath": "IntValue"}, "value": 142}
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def call_raw(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(tool, params)
    payload = result.structuredContent
    if not isinstance(payload, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return payload


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    payload = await call_raw(session, tool, params)
    require(payload.get("ok") is True, f"{tool} failed: {payload}")
    return payload


async def _apply_batch(session: ClientSession, title: str, task_id: str) -> dict[str, Any]:
    await call(session, "ue_open_asset", {"asset_path": BP_ASSET})
    await call(session, "ue_open_asset", {"asset_path": DA_ASSET})
    change_set = await call(
        session,
        "ue_create_change_set",
        {"title": title, "task_id": task_id},
    )
    change_set_id = str(change_set["changeSetId"])
    plan = await call(
        session,
        "ue_plan_live_write_batch",
        {
            "assets": [
                {"assetPath": BP_ASSET, "operations": BP_OPERATIONS},
                {"assetPath": DA_ASSET, "operations": DA_OPERATIONS},
            ],
            "description": title,
        },
    )
    applied = await call(
        session,
        "ue_apply_live_write_batch",
        {
            "batch_plan_id": plan["batchPlanId"],
            "confirmation": f"APPLY LIVE WRITE BATCH {plan['batchPlanId']}",
            "change_set_id": change_set_id,
        },
    )
    require(applied["state"] == "applied", f"apply state wrong: {applied}")
    return {"changeSetId": change_set_id, "plan": plan, "applied": applied}


async def run_c5(session: ClientSession, args: argparse.Namespace) -> dict[str, Any]:
    batch = await _apply_batch(session, "W4-4 C5 checkpoint save", "task_w4_4_c5")
    preview = await call(
        session,
        "ue_save_change_set_checkpoint",
        {"batch_execution_id": batch["applied"]["batchExecutionId"], "mode": "Preview"},
    )
    require(preview["state"] == "checkpoint_prepared", f"preview state wrong: {preview}")
    committed = await call(
        session,
        "ue_save_change_set_checkpoint",
        {
            "batch_execution_id": preview["checkpointSetId"],
            "mode": "Commit",
            "confirmation": preview["confirmationRequired"],
        },
    )
    require(committed["state"] == "saved", f"commit state wrong: {committed}")
    return {
        "case": "C5",
        "batch": batch,
        "preview": preview,
        "commit": committed,
    }


async def run_c6(session: ClientSession, args: argparse.Namespace) -> dict[str, Any]:
    batch = await _apply_batch(session, "W4-4 C6 preflight failure", "task_w4_4_c6")
    preview = await call(
        session,
        "ue_save_change_set_checkpoint",
        {"batch_execution_id": batch["applied"]["batchExecutionId"], "mode": "Preview"},
    )
    # Add one extra DA operation to the same Change Set after Preview. This changes the
    # effective receipt set for DA and must make Commit-time global revalidation fail.
    extra_plan = await call(
        session,
        "ue_plan_patch",
        {
            "asset_path": DA_ASSET,
            "operation": "setAssetProperty",
            "target": {"propertyPath": "IntValue"},
            "value": 777,
            "description": "W4-4 C6 post-preview membership change",
        },
    )
    await call(
        session,
        "ue_apply_asset_property_live",
        {
            "plan_id": extra_plan["planId"],
            "confirmation": f"LIVE APPLY {extra_plan['planId']}",
            "change_set_id": batch["changeSetId"],
        },
    )
    failed = await call_raw(
        session,
        "ue_save_change_set_checkpoint",
        {
            "batch_execution_id": preview["checkpointSetId"],
            "mode": "Commit",
            "confirmation": preview["confirmationRequired"],
        },
    )
    require(failed.get("ok") is False, f"expected C6 Commit to fail: {failed}")
    return {
        "case": "C6",
        "batch": batch,
        "preview": preview,
        "failedCommit": failed,
    }


async def run_c8(session: ClientSession, args: argparse.Namespace) -> dict[str, Any]:
    require(args.checkpoint_set_id, "C8 requires --checkpoint-set-id")
    loaded = await call(
        session,
        "ue_save_change_set_checkpoint",
        {"batch_execution_id": args.checkpoint_set_id, "mode": "Get"},
    )
    require(loaded["state"] == "saved", f"loaded state wrong: {loaded}")
    return {"case": "C8", "checkpointSetId": args.checkpoint_set_id, "loaded": loaded}


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
    started = time.perf_counter()
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                if args.case == "c5":
                    result = await run_c5(session, args)
                elif args.case == "c6":
                    result = await run_c6(session, args)
                elif args.case == "c8":
                    result = await run_c8(session, args)
                else:
                    raise RuntimeError(f"Unknown case: {args.case}")
    result["elapsedMs"] = round((time.perf_counter() - started) * 1000.0, 3)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--case", choices=["c5", "c6", "c8"], required=True)
    parser.add_argument("--checkpoint-set-id", default="")
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

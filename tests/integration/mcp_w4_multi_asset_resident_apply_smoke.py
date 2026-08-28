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


async def plan_apply_single(
    session: ClientSession,
    asset_path: str,
    operation: str,
    target: dict[str, Any],
    value: Any,
    change_set_id: str,
    description: str,
) -> dict[str, Any]:
    plan = await call(
        session,
        "ue_plan_patch",
        {
            "asset_path": asset_path,
            "operation": operation,
            "target": target,
            "value": value,
            "description": description,
        },
    )
    applied = await call(
        session,
        "ue_apply_asset_property_live",
        {
            "plan_id": plan["planId"],
            "confirmation": f"LIVE APPLY {plan['planId']}",
            "change_set_id": change_set_id,
        },
    )
    await call(
        session,
        "ue_verify_live_write_fast",
        {
            "asset_path": asset_path,
            "live_apply_receipt": applied["liveApplyReceipt"],
            "change_set_id": change_set_id,
        },
    )
    return applied


def load_execution(work_root: Path, execution_id: str) -> dict[str, Any]:
    path = Path(work_root) / "batch-executions" / execution_id / "execution.json"
    require(path.exists(), f"execution record not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


async def run_c2(session: ClientSession, args: argparse.Namespace) -> dict[str, Any]:
    await call(session, "ue_open_asset", {"asset_path": BP_ASSET})
    await call(session, "ue_open_asset", {"asset_path": DA_ASSET})
    change_set = await call(
        session,
        "ue_create_change_set",
        {"title": "W4-3 C2 multi-asset apply", "task_id": "task_w4_3_c2"},
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
            "description": "W4-3 C2",
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
    execution = load_execution(args.work_root, applied["batchExecutionId"])
    undo_results: list[dict[str, Any]] = []
    for batch_operation_id in applied["recoveryOrder"]:
        operation = next(op for op in execution["operations"] if op["batchOperationId"] == batch_operation_id)
        undo = await call(
            session,
            "ue_undo_asset_property_live",
            {
                "asset_path": operation["assetPath"],
                "transaction_id": operation["transactionId"],
                "editor_session_id": operation["editorSessionId"],
                "change_set_id": change_set_id,
            },
        )
        undo_results.append({"batchOperationId": batch_operation_id, "result": undo})
    change_set_view = await call(session, "ue_get_change_set", {"change_set_id": change_set_id})
    return {
        "case": "C2",
        "changeSetId": change_set_id,
        "plan": {
            "batchPlanId": plan["batchPlanId"],
            "assetCount": plan["assetCount"],
            "operationCount": plan["operationCount"],
        },
        "apply": applied,
        "executionRecord": {
            "state": execution["state"],
            "assetOrder": execution.get("assetOrder", []),
            "previousTransactionIds": [op["previousTransactionId"] for op in execution["operations"]],
            "transactionIds": [op["transactionId"] for op in execution["operations"]],
            "recoveryOrder": execution["recoveryOrder"],
        },
        "undoResults": undo_results,
        "changeSetOperationCount": change_set_view.get("operationCount"),
    }


async def run_c3c4(session: ClientSession, args: argparse.Namespace) -> dict[str, Any]:
    await call(session, "ue_open_asset", {"asset_path": BP_ASSET})
    await call(session, "ue_open_asset", {"asset_path": DA_ASSET})

    unrelated_cs = await call(
        session,
        "ue_create_change_set",
        {"title": "W4-3 C3 unrelated DA dirty", "task_id": "task_w4_3_c3_unrelated"},
    )
    unrelated_change_set_id = str(unrelated_cs["changeSetId"])
    unrelated_apply = await plan_apply_single(
        session,
        DA_ASSET,
        "setAssetProperty",
        {"propertyPath": "IntValue"},
        999,
        unrelated_change_set_id,
        "W4-3 C3 unrelated DA dirty pre-batch",
    )
    unrelated_receipt = str(unrelated_apply["liveApplyReceipt"])
    unrelated_result = unrelated_apply.get("result", {})
    unrelated_transaction = str(unrelated_result.get("transactionId") or "")
    unrelated_session = str(unrelated_result.get("editorSessionId") or "")

    batch_cs = await call(
        session,
        "ue_create_change_set",
        {"title": "W4-3 C3 later-asset failure", "task_id": "task_w4_3_c3_batch"},
    )
    batch_change_set_id = str(batch_cs["changeSetId"])
    plan = await call(
        session,
        "ue_plan_live_write_batch",
        {
            "assets": [
                {"assetPath": BP_ASSET, "operations": BP_OPERATIONS},
                {"assetPath": DA_ASSET, "operations": DA_OPERATIONS},
            ],
            "description": "W4-3 C3",
        },
    )
    failed = await call_raw(
        session,
        "ue_apply_live_write_batch",
        {
            "batch_plan_id": plan["batchPlanId"],
            "confirmation": f"APPLY LIVE WRITE BATCH {plan['batchPlanId']}",
            "change_set_id": batch_change_set_id,
        },
    )
    require(failed.get("ok") is False, f"expected batch apply to fail: {failed}")
    error_payload = failed.get("error", {})
    batch_execution_id = str((error_payload.get("details") or {}).get("batchExecutionId") or "")
    require(batch_execution_id.startswith("lwbe_"), f"missing batchExecutionId in failure: {failed}")
    execution = load_execution(args.work_root, batch_execution_id)
    require(execution["state"] == "partially_applied", f"expected partially_applied: {execution}")

    batch_undo_results: list[dict[str, Any]] = []
    for batch_operation_id in execution["recoveryOrder"]:
        operation = next(op for op in execution["operations"] if op["batchOperationId"] == batch_operation_id)
        undo = await call(
            session,
            "ue_undo_asset_property_live",
            {
                "asset_path": operation["assetPath"],
                "transaction_id": operation["transactionId"],
                "editor_session_id": operation["editorSessionId"],
                "change_set_id": batch_change_set_id,
            },
        )
        batch_undo_results.append({"batchOperationId": batch_operation_id, "result": undo})

    unrelated_still_valid = await call(
        session,
        "ue_verify_live_write_fast",
        {
            "asset_path": DA_ASSET,
            "live_apply_receipt": unrelated_receipt,
            "change_set_id": unrelated_change_set_id,
        },
    )
    unrelated_undo = await call(
        session,
        "ue_undo_asset_property_live",
        {
            "asset_path": DA_ASSET,
            "transaction_id": unrelated_transaction,
            "editor_session_id": unrelated_session,
            "change_set_id": unrelated_change_set_id,
        },
    )
    return {
        "case": "C3-C4",
        "unrelated": {
            "changeSetId": unrelated_change_set_id,
            "liveApplyReceipt": unrelated_receipt,
            "transactionId": unrelated_transaction,
            "editorSessionId": unrelated_session,
        },
        "batch": {
            "batchPlanId": plan["batchPlanId"],
            "changeSetId": batch_change_set_id,
            "batchExecutionId": batch_execution_id,
            "failedApplyPayload": failed,
            "executionRecord": {
                "state": execution["state"],
                "appliedCount": execution.get("appliedCount", 0),
                "lastSuccessfulOperation": execution.get("lastSuccessfulOperation", ""),
                "failedOperation": execution.get("failedOperation", ""),
                "notStarted": execution.get("notStarted", []),
                "recoveryOrder": execution["recoveryOrder"],
                "operations": execution["operations"],
            },
            "undoResults": batch_undo_results,
        },
        "unrelatedStillValidAfterBatchRecovery": unrelated_still_valid,
        "unrelatedUndoResult": unrelated_undo,
    }


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
                if args.case == "c2":
                    result = await run_c2(session, args)
                elif args.case == "c3c4":
                    result = await run_c3c4(session, args)
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
    parser.add_argument("--case", choices=["c2", "c3c4"], required=True)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
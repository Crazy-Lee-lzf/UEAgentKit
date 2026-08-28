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


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(tool, params)
    payload = result.structuredContent
    if not isinstance(payload, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    require(payload.get("ok") is True, f"{tool} failed: {payload}")
    return payload


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
                opened = await call(session, "ue_open_asset", {"asset_path": BP_ASSET})
                change_set = await call(
                    session,
                    "ue_create_change_set",
                    {"title": "W4-2 C1 single BP multi-op apply", "task_id": "task_w4_2_c1"},
                )
                change_set_id = str(change_set["changeSetId"])
                plan = await call(
                    session,
                    "ue_plan_live_write_batch",
                    {
                        "assets": [{"assetPath": BP_ASSET, "operations": BP_OPERATIONS}],
                        "description": "W4-2 C1 real apply",
                    },
                )
                batch_plan_id = str(plan["batchPlanId"])
                applied = await call(
                    session,
                    "ue_apply_live_write_batch",
                    {
                        "batch_plan_id": batch_plan_id,
                        "confirmation": f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                        "change_set_id": change_set_id,
                    },
                )
                require(applied.get("state") == "applied", f"Apply state wrong: {applied}")
                change_set_view = await call(
                    session,
                    "ue_get_change_set",
                    {"change_set_id": change_set_id},
                )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    execution_id = str(applied["batchExecutionId"])
    execution_path = Path(args.work_root) / "batch-executions" / execution_id / "execution.json"
    execution_record: dict[str, Any] = {}
    if execution_path.exists():
        execution_record = json.loads(execution_path.read_text(encoding="utf-8"))
    return {
        "elapsedMs": elapsed_ms,
        "opened": opened,
        "changeSetId": change_set_id,
        "changeSetOperationCount": change_set_view.get("operationCount"),
        "changeSetOperations": change_set_view.get("operations", []),
        "plan": {
            "batchPlanId": batch_plan_id,
            "requestDigest": plan["requestDigest"],
            "assetCount": plan["assetCount"],
            "operationCount": plan["operationCount"],
        },
        "apply": {
            "batchExecutionId": execution_id,
            "state": applied["state"],
            "appliedCount": applied["appliedCount"],
            "operations": applied["operations"],
            "lastTransactionId": applied["lastTransactionId"],
            "savePerformed": applied["savePerformed"],
        },
        "executionRecord": {
            "state": execution_record.get("state"),
            "previousTransactionIds": [
                operation.get("previousTransactionId", "") for operation in execution_record.get("operations", [])
            ],
            "transactionIds": [
                operation.get("transactionId", "") for operation in execution_record.get("operations", [])
            ],
            "recoveryOrder": execution_record.get("recoveryOrder", []),
        },
    }


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
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report:
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
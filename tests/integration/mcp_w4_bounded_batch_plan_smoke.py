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


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(tool, params)
    payload = result.structuredContent
    if not isinstance(payload, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    require(payload.get("ok") is True, f"{tool} failed: {payload}")
    return payload


async def run_b0(session: ClientSession) -> dict[str, Any]:
    result = await call(
        session,
        "ue_plan_live_write_batch",
        {
            "assets": [{"assetPath": BP_ASSET, "operations": BP_OPERATIONS}],
            "description": "W4-1 S1 B0 planning payload",
        },
    )
    require(result.get("assetCount") == 1 and result.get("operationCount") == 3, f"B0 counts wrong: {result}")
    require(
        [op["sequenceIndex"] for op in result["assets"][0]["operations"]] == [0, 1, 2],
        f"B0 sequence wrong: {result}",
    )
    require(result.get("state") == "planned", f"B0 state wrong: {result}")
    require(result.get("bounds", {}).get("effective", {}).get("maxAssets") >= 1, f"B0 bounds wrong: {result}")
    return {
        "case": "S1-B0",
        "batchPlanId": result["batchPlanId"],
        "requestDigest": result["requestDigest"],
        "assetCount": result["assetCount"],
        "operationCount": result["operationCount"],
        "childPlanIds": [op["childPlanId"] for op in result["assets"][0]["operations"]],
        "confirmationRequired": result["confirmationRequired"],
    }


async def run_b1(session: ClientSession) -> dict[str, Any]:
    result = await call(
        session,
        "ue_plan_live_write_batch",
        {
            "assets": [
                {"assetPath": BP_ASSET, "operations": BP_OPERATIONS},
                {"assetPath": DA_ASSET, "operations": DA_OPERATIONS},
            ],
            "description": "W4-1 S2 B1 planning payload",
        },
    )
    require(result.get("assetCount") == 2 and result.get("operationCount") == 4, f"B1 counts wrong: {result}")
    all_ops = [op for asset in result["assets"] for op in asset["operations"]]
    require([op["sequenceIndex"] for op in all_ops] == [0, 1, 2, 3], f"B1 sequence wrong: {result}")
    require([asset["assetPath"] for asset in result["assets"]] == [BP_ASSET, DA_ASSET], f"B1 order wrong: {result}")
    return {
        "case": "S2-B1",
        "batchPlanId": result["batchPlanId"],
        "requestDigest": result["requestDigest"],
        "assetCount": result["assetCount"],
        "operationCount": result["operationCount"],
        "assetOrder": [asset["assetPath"] for asset in result["assets"]],
        "childPlanIds": [op["childPlanId"] for op in all_ops],
        "confirmationRequired": result["confirmationRequired"],
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
            "-EnableWriteTools",
            "-EnableCommitTools",
            "-EngineRoot",
            str(args.engine_root),
            "-ProjectPath",
            str(args.project),
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
                b0 = await run_b0(session)
                b1 = await run_b1(session)
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return {"elapsedMs": elapsed_ms, "cases": [b0, b1], "liveEditorEnabled": False}


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
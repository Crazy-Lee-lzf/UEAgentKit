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

DA_ASSET = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset"
BP_ASSET = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint"
PIN_GRAPH = "12345678-9abc-def0-1234-56789abcdef0"
PIN_NODE = "11111111-2222-2222-3333-333344444444"
PIN_NAME = "A"


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


async def create_change_set(session: ClientSession, title: str, task_id: str) -> str:
    created = await call(session, "ue_create_change_set", {"title": title, "task_id": task_id})
    require(created.get("ok") is True and created.get("changeSetId", "").startswith("cs_"), f"create change set failed: {created}")
    return str(created["changeSetId"])


async def plan_bp(
    session: ClientSession,
    operation: str,
    target: dict[str, Any],
    value: Any,
    description: str,
) -> dict[str, Any]:
    if operation == "setVariableDefault":
        return await call(
            session,
            "ue_set_blueprint_default",
            {
                "asset_path": BP_ASSET,
                "variable_name": target["variableName"],
                "value": value,
                "mode": "Plan",
                "description": description,
            },
        )
    if operation == "setComponentProperty":
        return await call(
            session,
            "ue_set_component_property",
            {
                "asset_path": BP_ASSET,
                "component_name": target["componentName"],
                "property_path": target["propertyPath"],
                "value": value,
                "mode": "Plan",
                "description": description,
            },
        )
    if operation == "setPinDefault":
        return await call(
            session,
            "ue_set_pin_default",
            {
                "asset_path": BP_ASSET,
                "graph_guid": target["graphGuid"],
                "node_guid": target["nodeGuid"],
                "pin_name": target["pinName"],
                "value": value,
                "mode": "Plan",
                "description": description,
            },
        )
    raise RuntimeError(f"Unsupported BP operation: {operation}")


async def apply_live(session: ClientSession, plan: dict[str, Any], change_set_id: str) -> dict[str, Any]:
    plan_id = str(plan["planId"])
    applied = await call(
        session,
        "ue_apply_asset_property_live",
        {
            "plan_id": plan_id,
            "confirmation": f"LIVE APPLY {plan_id}",
            "change_set_id": change_set_id,
        },
    )
    require(applied.get("ok") is True and applied.get("changed") is True, f"apply failed: {applied}")
    return applied


async def open_asset(session: ClientSession, asset_path: str) -> None:
    opened = await call(session, "ue_open_asset", {"asset_path": asset_path})
    require(opened.get("ok") is True, f"open asset failed: {opened}")


async def fast_verify(session: ClientSession, asset_path: str, receipt: str, change_set_id: str) -> dict[str, Any]:
    verified = await call(
        session,
        "ue_verify_live_write_fast",
        {
            "asset_path": asset_path,
            "live_apply_receipt": receipt,
            "change_set_id": change_set_id,
        },
    )
    require(
        verified.get("ok") is True
        and verified.get("verified") is True
        and verified.get("verificationKind") == "resident-fast",
        f"fast verify failed: {verified}",
    )
    return verified


async def checkpoint_save(
    session: ClientSession,
    asset_path: str,
    change_set_id: str,
) -> dict[str, Any]:
    preview = await call(
        session,
        "ue_save_authorized_asset",
        {
            "asset_path": asset_path,
            "mode": "Preview",
            "change_set_id": change_set_id,
            "verification_mode": "checkpoint",
        },
    )
    require(preview.get("ok") is True and preview.get("checkpointId", "").startswith("cp_"), f"checkpoint preview failed: {preview}")
    commit = await call(
        session,
        "ue_save_authorized_asset",
        {
            "asset_path": asset_path,
            "mode": "Commit",
            "save_receipt": preview["saveReceipt"],
            "confirmation": f"SAVE {preview['saveReceipt']}",
            "change_set_id": change_set_id,
            "verification_mode": "checkpoint",
        },
    )
    require(
        commit.get("ok") is True
        and commit.get("saved") is True
        and commit.get("verified") is False
        and commit.get("verificationKind") == "persisted-action",
        f"checkpoint commit failed: {commit}",
    )
    return {"preview": preview, "commit": commit, "checkpointId": preview["checkpointId"]}


async def checkpoint_verify(session: ClientSession, checkpoint_id: str) -> dict[str, Any]:
    verified = await call(session, "ue_verify_live_write_checkpoint", {"checkpoint_id": checkpoint_id})
    require(
        verified.get("ok") is True
        and verified.get("verified") is True
        and verified.get("verificationKind") == "independent-verified"
        and verified.get("childUnrealProcessCount") == 1,
        f"checkpoint verify failed: {verified}",
    )
    return verified


async def close_chain(session: ClientSession, change_set_id: str, asset_path: str) -> dict[str, Any]:
    diff = await call(
        session,
        "ue_analyze_semantic_diff",
        {
            "change_set_id": change_set_id,
            "stage": "verified",
            "asset_paths": [asset_path],
            "include_unchanged": True,
            "max_changes": 64,
        },
    )
    require(diff.get("ok") is True, f"semantic diff verified failed: {diff}")
    require(
        diff.get("summary", {}).get("unexpectedCount") == 0
        and diff.get("summary", {}).get("missingExpectedCount") == 0,
        f"semantic diff reported unexpected/missing expected changes: {diff}",
    )
    plan_result = await call(session, "ue_build_verification_plan", {"change_set_id": change_set_id})
    require(plan_result.get("ok") is True, f"verification plan failed: {plan_result}")
    if asset_path == BP_ASSET:
        compiled = await call(session, "ue_compile_blueprint", {"asset_path": asset_path})
        require(compiled.get("ok") is True, f"compile failed: {compiled}")
    validated = await call(session, "ue_validate_asset", {"asset_path": asset_path})
    require(validated.get("ok") is True, f"data validation failed: {validated}")
    trust = await call(session, "ue_evaluate_trust_verdict", {"change_set_id": change_set_id})
    require(trust.get("ok") is True and trust.get("verdict", {}).get("state") == "verified", f"trust failed: {trust}")
    return {"semanticDiff": diff, "verificationPlan": plan_result, "trust": trust}


async def run_nonbp_checkpoint(session: ClientSession) -> dict[str, Any]:
    change_set_id = await create_change_set(session, "W3 C0 non-BP checkpoint", "task_w3_c0_nonbp")
    await open_asset(session, DA_ASSET)
    plan = await call(
        session,
        "ue_plan_patch",
        {
            "asset_path": DA_ASSET,
            "operation": "setAssetProperty",
            "target": {"propertyPath": "IntValue"},
            "value": 142,
            "description": "W3 C0 non-BP checkpoint",
        },
    )
    require(plan.get("ok") is True, f"DA plan failed: {plan}")
    applied = await apply_live(session, plan, change_set_id)
    await fast_verify(session, DA_ASSET, str(applied["liveApplyReceipt"]), change_set_id)
    saved = await checkpoint_save(session, DA_ASSET, change_set_id)
    verified = await checkpoint_verify(session, saved["checkpointId"])
    chain = await close_chain(session, change_set_id, DA_ASSET)
    return {
        "case": "C0-nonbp",
        "changeSetId": change_set_id,
        "checkpointId": saved["checkpointId"],
        "fastVerify": verified["verificationKind"],
        "verified": verified["verified"],
        "childUnrealProcessCount": verified["childUnrealProcessCount"],
        "trust": chain["trust"].get("verdict", {}).get("state"),
        "afterRevision": saved["commit"]["afterRevision"],
    }


async def run_bp_multi(session: ClientSession) -> dict[str, Any]:
    await open_asset(session, BP_ASSET)
    cs2 = await create_change_set(session, "W3 C2 BP multi-op checkpoint", "task_w3_c2_bp_multi")
    operations = [
        ("setVariableDefault", {"variableName": "TransactionInt"}, 100),
        ("setComponentProperty", {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"}, 10),
        ("setPinDefault", {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": PIN_NAME}, 5),
    ]
    receipts: list[str] = []
    for operation, target, value in operations:
        plan = await plan_bp(session, operation, target, value, f"W3 C2 {operation}")
        require(plan.get("ok") is True and plan.get("planId"), f"{operation} plan failed: {plan}")
        applied = await apply_live(session, plan, cs2)
        receipts.append(str(applied["liveApplyReceipt"]))
        await fast_verify(session, BP_ASSET, receipts[-1], cs2)
    save2 = await checkpoint_save(session, BP_ASSET, cs2)
    require(save2["preview"].get("effectiveReceiptCount") == 3, f"C2 expected 3 effective receipts: {save2['preview']}")
    verify2 = await checkpoint_verify(session, save2["checkpointId"])
    require(verify2.get("effectiveOperationCount") == 3 and verify2.get("verifiedOperationCount") == 3, f"C2 coverage failed: {verify2}")
    chain2 = await close_chain(session, cs2, BP_ASSET)
    return {
        "case": "C2-bp-multi",
        "changeSetId": cs2,
        "checkpointId": save2["checkpointId"],
        "effectiveOperationCount": verify2["effectiveOperationCount"],
        "verifiedOperationCount": verify2["verifiedOperationCount"],
        "childUnrealProcessCount": verify2["childUnrealProcessCount"],
        "trust": chain2["trust"].get("verdict", {}).get("state"),
    }


async def run_bp_checkpoints(session: ClientSession) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []

    # C1: Blueprint single operation checkpoint (variable 42).
    await open_asset(session, BP_ASSET)
    cs1 = await create_change_set(session, "W3 C1 BP variable checkpoint", "task_w3_c1_bp_var")
    plan1 = await plan_bp(
        session,
        "setVariableDefault",
        {"variableName": "TransactionInt"},
        42,
        "W3 C1 BP variable checkpoint",
    )
    applied1 = await apply_live(session, plan1, cs1)
    await fast_verify(session, BP_ASSET, str(applied1["liveApplyReceipt"]), cs1)
    save1 = await checkpoint_save(session, BP_ASSET, cs1)
    verify1 = await checkpoint_verify(session, save1["checkpointId"])
    chain1 = await close_chain(session, cs1, BP_ASSET)
    results.append(
        {
            "case": "C1-bp-variable",
            "changeSetId": cs1,
            "checkpointId": save1["checkpointId"],
            "verified": verify1["verified"],
            "childUnrealProcessCount": verify1["childUnrealProcessCount"],
            "trust": chain1["trust"].get("verdict", {}).get("state"),
        }
    )
    results.append(await run_bp_multi(session))
    return results


async def run_supersession(session: ClientSession) -> dict[str, Any]:
    await open_asset(session, BP_ASSET)
    cs3 = await create_change_set(session, "W3 C3 supersession checkpoint", "task_w3_c3_supersession")
    receipts: list[str] = []
    for value in (10, 20, 42):
        plan = await plan_bp(session, "setVariableDefault", {"variableName": "TransactionInt"}, value, f"W3 C3 {value}")
        require(plan.get("ok") is True and plan.get("planId"), f"supersession plan failed: {plan}")
        applied = await apply_live(session, plan, cs3)
        receipts.append(str(applied["liveApplyReceipt"]))
        await fast_verify(session, BP_ASSET, receipts[-1], cs3)
    preview = await call(
        session,
        "ue_save_authorized_asset",
        {
            "asset_path": BP_ASSET,
            "mode": "Preview",
            "change_set_id": cs3,
            "verification_mode": "checkpoint",
        },
    )
    require(preview.get("ok") is True, f"supersession preview failed: {preview}")
    require(preview.get("effectiveReceiptCount") == 1, f"supersession expected 1 effective: {preview}")
    require(preview.get("supersededReceiptCount") == 2, f"supersession expected 2 superseded: {preview}")
    commit = await call(
        session,
        "ue_save_authorized_asset",
        {
            "asset_path": BP_ASSET,
            "mode": "Commit",
            "save_receipt": preview["saveReceipt"],
            "confirmation": f"SAVE {preview['saveReceipt']}",
            "change_set_id": cs3,
            "verification_mode": "checkpoint",
        },
    )
    require(commit.get("ok") is True and commit.get("saved") is True, f"supersession commit failed: {commit}")
    verified = await checkpoint_verify(session, preview["checkpointId"])
    require(verified.get("verifiedOperationCount") == 1, f"supersession verify failed: {verified}")
    change_set = await call(session, "ue_get_change_set", {"change_set_id": cs3})
    statuses = {op["status"] for op in change_set.get("operations", [])}
    require(statuses == {"verified", "superseded"}, f"supersession statuses wrong: {change_set}")
    chain3 = await close_chain(session, cs3, BP_ASSET)
    return {
        "case": "C3-supersession",
        "changeSetId": cs3,
        "checkpointId": preview["checkpointId"],
        "effectiveOperationCount": verified["effectiveOperationCount"],
        "supersededOperationCount": verified["supersededOperationCount"],
        "childUnrealProcessCount": verified["childUnrealProcessCount"],
        "trust": chain3["trust"].get("verdict", {}).get("state"),
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
    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                if args.case in {"nonbp", "all"}:
                    results.append(await run_nonbp_checkpoint(session))
                if args.case == "c2":
                    results.append(await run_bp_multi(session))
                if args.case == "c3":
                    results.append(await run_supersession(session))
                if args.case in {"bp", "all"}:
                    results.extend(await run_bp_checkpoints(session))
                if args.case == "all":
                    results.append(await run_supersession(session))
    return {"elapsedMs": round((time.perf_counter() - started) * 1000.0, 3), "cases": results}


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
    parser.add_argument("--case", choices=["nonbp", "bp", "c2", "c3", "all"], default="all")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.report:
        args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
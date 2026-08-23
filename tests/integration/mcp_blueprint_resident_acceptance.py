from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


ASSET_PATH = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint"
PIN_GRAPH = "12345678-9abc-def0-1234-56789abcdef0"
PIN_NODE = "11111111-2222-2222-3333-333344444444"
PIN_NAME = "A"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


async def plan_for_operation(
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
                "asset_path": ASSET_PATH,
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
                "asset_path": ASSET_PATH,
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
                "asset_path": ASSET_PATH,
                "graph_guid": target["graphGuid"],
                "node_guid": target["nodeGuid"],
                "pin_name": target["pinName"],
                "value": value,
                "mode": "Plan",
                "description": description,
            },
        )
    raise RuntimeError(f"Unsupported operation: {operation}")


async def apply_live(session: ClientSession, plan: dict[str, Any], change_set_id: str) -> dict[str, Any]:
    plan_id = str(plan["planId"])
    return await call(
        session,
        "ue_apply_asset_property_live",
        {
            "plan_id": plan_id,
            "confirmation": f"LIVE APPLY {plan_id}",
            "change_set_id": change_set_id,
        },
    )


async def run_success(args: argparse.Namespace, session: ClientSession) -> dict[str, Any]:
    change_set = await call(
        session,
        "ue_create_change_set",
        {"title": f"W1 Acceptance {args.operation} success", "task_id": f"task_w1_{args.operation}_success"},
    )
    change_set_id = str(change_set["changeSetId"])
    plan = await plan_for_operation(session, args.operation, args.target, args.value, "W1 acceptance success")
    applied = await apply_live(session, plan, change_set_id)
    result = applied.get("result", {})
    require(applied.get("ok") is True, f"Apply failed: {applied}")
    require(applied.get("changed") is True, "Expected changed==true")
    require(applied.get("liveApplyReceipt"), "Expected liveApplyReceipt")
    require(result.get("changed") is True, "result.changed missing")
    require(result.get("compileAttempted") is True, "compileAttempted missing")
    require(result.get("compileSucceeded") is True, "compileSucceeded not true")
    require(result.get("transactionRecorded") is True, "transactionRecorded not true")
    require(result.get("packageDirtyAfter") is True, "packageDirtyAfter not true")

    live_diff = await call(
        session,
        "ue_analyze_semantic_diff",
        {
            "change_set_id": change_set_id,
            "stage": "live",
            "asset_paths": [ASSET_PATH],
            "include_unchanged": True,
            "max_changes": 64,
        },
    )
    require(live_diff.get("ok") is True, f"Semantic Diff live failed: {live_diff}")

    preview = await call(
        session,
        "ue_save_authorized_asset",
        {
            "asset_path": ASSET_PATH,
            "mode": "Preview",
            "change_set_id": change_set_id,
        },
    )
    require(preview.get("ok") is True, f"Save Preview failed: {preview}")
    save_receipt = str(preview["saveReceipt"])
    commit = await call(
        session,
        "ue_save_authorized_asset",
        {
            "asset_path": ASSET_PATH,
            "mode": "Commit",
            "save_receipt": save_receipt,
            "confirmation": f"SAVE {save_receipt}",
            "change_set_id": change_set_id,
        },
    )
    require(commit.get("ok") is True and commit.get("saved") is True, f"Save Commit failed: {commit}")

    verified = await call(
        session,
        "ue_verify_live_write",
        {
            "asset_path": ASSET_PATH,
            "change_set_id": change_set_id,
        },
    )
    require(verified.get("ok") is True and verified.get("state") == "verified", f"Verify failed: {verified}")

    verified_diff = await call(
        session,
        "ue_analyze_semantic_diff",
        {
            "change_set_id": change_set_id,
            "stage": "verified",
            "asset_paths": [ASSET_PATH],
            "include_unchanged": True,
            "max_changes": 64,
        },
    )
    require(verified_diff.get("ok") is True, f"Semantic Diff verified failed: {verified_diff}")

    plan_result = await call(
        session,
        "ue_build_verification_plan",
        {"change_set_id": change_set_id},
    )
    require(plan_result.get("ok") is True, f"Verification Plan failed: {plan_result}")

    compile_evidence = await call(
        session,
        "ue_compile_blueprint",
        {"asset_path": ASSET_PATH},
    )
    require(compile_evidence.get("ok") is True, f"Explicit compile failed: {compile_evidence}")

    validation_evidence = await call(
        session,
        "ue_validate_asset",
        {"asset_path": ASSET_PATH},
    )
    require(validation_evidence.get("ok") is True, f"Data validation failed: {validation_evidence}")

    trust = await call(
        session,
        "ue_evaluate_trust_verdict",
        {"change_set_id": change_set_id},
    )
    require(trust.get("ok") is True, f"Trust Verdict failed: {trust}")
    require(trust.get("verdict", {}).get("state") == "verified", f"Trust did not close verified: {trust.get('verdict')}")

    return {
        "mode": "success",
        "operation": args.operation,
        "changeSetId": change_set_id,
        "planId": plan.get("planId"),
        "liveApplyReceipt": applied.get("liveApplyReceipt"),
        "transactionId": result.get("transactionId"),
        "editorSessionId": result.get("editorSessionId"),
        "beforeValue": result.get("beforeValue"),
        "afterValue": result.get("afterValue"),
        "compileAttempted": result.get("compileAttempted"),
        "compileSucceeded": result.get("compileSucceeded"),
        "packageDirtyAfter": result.get("packageDirtyAfter"),
        "trust": trust,
    }


async def run_undo_discard(args: argparse.Namespace, session: ClientSession, action: str) -> dict[str, Any]:
    change_set = await call(
        session,
        "ue_create_change_set",
        {"title": f"W1 Acceptance {args.operation} {action}", "task_id": f"task_w1_{args.operation}_{action}"},
    )
    change_set_id = str(change_set["changeSetId"])
    plan = await plan_for_operation(session, args.operation, args.target, args.value, f"W1 acceptance {action}")
    applied = await apply_live(session, plan, change_set_id)
    result = applied.get("result", {})
    require(applied.get("ok") is True and applied.get("changed") is True, f"Apply failed for {action}: {applied}")
    transaction_id = str(result.get("transactionId", ""))
    editor_session_id = str(result.get("editorSessionId", ""))
    if action == "undo":
        reverted = await call(
            session,
            "ue_undo_asset_property_live",
            {
                "asset_path": ASSET_PATH,
                "transaction_id": transaction_id,
                "editor_session_id": editor_session_id,
                "change_set_id": change_set_id,
            },
        )
    else:
        reverted = await call(
            session,
            "ue_discard_asset_property_live",
            {
                "asset_path": ASSET_PATH,
                "transaction_id": transaction_id,
                "editor_session_id": editor_session_id,
                "change_set_id": change_set_id,
            },
        )
    require(reverted.get("ok") is True, f"{action} failed: {reverted}")
    return {
        "mode": action,
        "operation": args.operation,
        "changeSetId": change_set_id,
        "transactionId": transaction_id,
        "editorSessionId": editor_session_id,
        "reverted": reverted,
    }


async def run_noop(args: argparse.Namespace, session: ClientSession) -> dict[str, Any]:
    change_set = await call(
        session,
        "ue_create_change_set",
        {"title": f"W1 Acceptance {args.operation} noop", "task_id": f"task_w1_{args.operation}_noop"},
    )
    change_set_id = str(change_set["changeSetId"])
    plan = await plan_for_operation(session, args.operation, args.target, args.value, "W1 acceptance no-op")
    applied = await apply_live(session, plan, change_set_id)
    require(applied.get("ok") is True, f"No-op apply failed: {applied}")
    require(applied.get("changed") is False, "No-op should have changed==false")
    require(not applied.get("liveApplyReceipt"), "No-op must not create a liveApplyReceipt")
    return {
        "mode": "noop",
        "operation": args.operation,
        "changeSetId": change_set_id,
        "changed": False,
        "liveApplyReceipt": "",
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
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                opened = await call(session, "ue_open_asset", {"asset_path": ASSET_PATH})
                require(opened.get("ok") is True and opened.get("result", {}).get("openAfter") is True, f"Open failed: {opened}")

                if args.mode == "success":
                    return await run_success(args, session)
                if args.mode == "noop":
                    return await run_noop(args, session)
                if args.mode == "undo":
                    return await run_undo_discard(args, session, "undo")
                if args.mode == "discard":
                    return await run_undo_discard(args, session, "discard")
                raise RuntimeError(f"Unsupported mode: {args.mode}")


def build_target(operation: str) -> dict[str, Any]:
    if operation == "setVariableDefault":
        return {"variableName": "TransactionInt"}
    if operation == "setComponentProperty":
        return {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"}
    if operation == "setPinDefault":
        return {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": PIN_NAME}
    raise RuntimeError(f"Unsupported operation: {operation}")


def default_value(operation: str, mode: str) -> Any:
    if mode == "noop":
        if operation == "setVariableDefault":
            return 0
        if operation == "setComponentProperty":
            return 0.0
        return 0
    if operation == "setVariableDefault":
        return 42
    if operation == "setComponentProperty":
        return 10.0
    return 5


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--revision-export", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--error-log", type=Path, required=True)
    parser.add_argument("--operation", required=True, choices=["setVariableDefault", "setComponentProperty", "setPinDefault"])
    parser.add_argument("--mode", required=True, choices=["success", "noop", "undo", "discard"])
    parser.add_argument("--value", type=json.loads)
    args = parser.parse_args()
    args.target = build_target(args.operation)
    if args.value is None:
        args.value = default_value(args.operation, args.mode)
    report = asyncio.run(run(args))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402

DATA_TABLES_ROOT = "/Game/UEAgentKitWriteTests/DataTables"
DATA_TABLE_CLASS = "/Script/Engine.DataTable"
ROW_STRUCT = "/Script/UEAgentKitEditor.UEAgentKitDataTableFixtureRow"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

FIXTURE_IDS = {
    "cell": "data-table-cell-asset",
    "fields": "data-table-fields-asset",
    "add": "data-table-add-asset",
    "rename": "data-table-rename-asset",
    "remove": "data-table-remove-asset",
}

FIXTURE_ASSETS = {
    "cell": f"{DATA_TABLES_ROOT}/DT_CellTarget.DT_CellTarget",
    "fields": f"{DATA_TABLES_ROOT}/DT_FieldsTarget.DT_FieldsTarget",
    "add": f"{DATA_TABLES_ROOT}/DT_AddTarget.DT_AddTarget",
    "rename": f"{DATA_TABLES_ROOT}/DT_RenameTarget.DT_RenameTarget",
    "remove": f"{DATA_TABLES_ROOT}/DT_RemoveTarget.DT_RemoveTarget",
}

ROW_ALPHA = "RowAlpha"
ROW_BETA = "RowBeta"
ROW_GAMMA = "RowGamma"
ROW_DELTA = "RowDelta"
ROW_RENAMED = "RowGammaRenamed"

ALPHA_INITIAL = {"Count": 1, "Label": "Alpha", "bEnabled": True}
BETA_INITIAL = {"Count": 2, "Label": "Beta", "bEnabled": False}
GAMMA_INITIAL = {"Count": 3, "Label": "Gamma", "bEnabled": True}
ALPHA_NEW = {"Count": 42, "Label": "Alpha", "bEnabled": True}
BETA_NEW = {"Count": 22, "Label": "Beta New", "bEnabled": False}
DELTA_NEW = {"Count": 4, "Label": "Delta", "bEnabled": True}

# One entry per existing DataTable MCP write family.  Every family is applied,
# undone, and re-applied so the test can prove restoration using the pre-write
# row that the re-apply reads back from live Editor memory.
OPERATIONS: list[dict[str, Any]] = [
    {
        "name": "cell",
        "asset": FIXTURE_ASSETS["cell"],
        "operation": "setDataTableCell",
        "target": {"rowName": ROW_ALPHA, "fieldName": "Count"},
        "value": 42,
        "valueKind": "data-table-cell",
        "dataTableKind": "cell",
        "rowName": ROW_ALPHA,
        "before": ALPHA_INITIAL,
        "after": ALPHA_NEW,
    },
    {
        "name": "fields",
        "asset": FIXTURE_ASSETS["fields"],
        "operation": "setDataTableRowFields",
        "target": {"rowName": ROW_BETA},
        "value": {"Count": 22, "Label": "Beta New"},
        "valueKind": "data-table-row-fields",
        "dataTableKind": "row-fields",
        "rowName": ROW_BETA,
        "before": BETA_INITIAL,
        "after": BETA_NEW,
    },
    {
        "name": "add",
        "asset": FIXTURE_ASSETS["add"],
        "operation": "addDataTableRow",
        "target": {"rowName": ROW_DELTA},
        "value": DELTA_NEW,
        "valueKind": "data-table-row-add",
        "dataTableKind": "row-add",
        "rowName": ROW_DELTA,
        "before": None,
        "after": DELTA_NEW,
    },
    {
        "name": "rename",
        "asset": FIXTURE_ASSETS["rename"],
        "operation": "renameDataTableRow",
        "target": {"rowName": ROW_GAMMA, "newRowName": ROW_RENAMED},
        "value": True,
        "valueKind": "data-table-row-rename",
        "dataTableKind": "row-rename",
        "rowName": ROW_GAMMA,
        "newRowName": ROW_RENAMED,
        "before": GAMMA_INITIAL,
        "after": GAMMA_INITIAL,
    },
    {
        "name": "remove",
        "asset": FIXTURE_ASSETS["remove"],
        "operation": "removeDataTableRow",
        "target": {"rowName": ROW_BETA},
        "value": True,
        "valueKind": "data-table-row-remove",
        "dataTableKind": "row-remove",
        "rowName": ROW_BETA,
        "before": BETA_INITIAL,
        "after": None,
    },
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directory_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (path for path in root.rglob("*.json") if path.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


def error_code(result: dict[str, Any]) -> str:
    return str(result.get("error", {}).get("code", ""))


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


WRAPPER_BY_OPERATION = {
    "setDataTableCell": "ue_set_datatable_cell",
    "setDataTableRowFields": "ue_set_datatable_row_fields",
    "addDataTableRow": "ue_add_datatable_row",
    "removeDataTableRow": "ue_remove_datatable_row",
    "renameDataTableRow": "ue_rename_datatable_row",
}


async def plan_write(
    session: ClientSession,
    asset_path: str,
    operation: str,
    target: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    common = {
        "asset_path": asset_path,
        "row_name": target["rowName"],
        "mode": "Plan",
        "description": "Real UE5.6 Live DataTable Undo/Save/Reload regression",
    }
    if operation == "setDataTableCell":
        params = {**common, "field_name": target["fieldName"], "value": value}
    elif operation in {"setDataTableRowFields", "addDataTableRow"}:
        params = {**common, "values": value}
    elif operation == "removeDataTableRow":
        params = common
    elif operation == "renameDataTableRow":
        params = {**common, "new_row_name": target["newRowName"]}
    else:
        raise ValueError(f"Unsupported DataTable operation: {operation}")
    return await call(session, WRAPPER_BY_OPERATION[operation], params)


async def apply_live(
    session: ClientSession,
    plan_id: str,
    change_set_id: str = "",
) -> dict[str, Any]:
    params = {"plan_id": plan_id, "confirmation": f"LIVE APPLY {plan_id}"}
    if change_set_id:
        params["change_set_id"] = change_set_id
    return await call(session, "ue_apply_asset_property_live", params)


async def undo_live(
    session: ClientSession,
    asset_path: str,
    transaction_id: str,
    editor_session_id: str,
) -> dict[str, Any]:
    return await call(
        session,
        "ue_undo_asset_property_live",
        {
            "asset_path": asset_path,
            "transaction_id": transaction_id,
            "editor_session_id": editor_session_id,
        },
    )


async def inspect_live(session: ClientSession, asset_path: str) -> dict[str, Any]:
    return await call(session, "ue_inspect_asset_live", {"asset_path": asset_path})


async def dirty_paths(session: ClientSession) -> set[str]:
    dirty = await call(session, "ue_get_dirty_assets", {})
    paths: set[str] = set()
    for item in dirty.get("result", {}).get("items", []):
        if isinstance(item, dict) and isinstance(item.get("assetPaths"), list):
            paths.update(str(path) for path in item["assetPaths"])
    return paths


def assert_plan_receipt(plan: dict[str, Any], op: dict[str, Any], revision: str) -> None:
    if (
        not plan.get("ok")
        or plan.get("operation") != op["operation"]
        or not str(plan.get("planId", "")).startswith("plan_")
        or not str(plan.get("patchDigest", "")).startswith("sha256:")
        or plan.get("expectedRevision") != revision
        or plan.get("target") != op["target"]
        or plan.get("value") != op["value"]
        or plan.get("commitAllowedByPolicy") is not True
        or plan.get("commitToolsEnabled") is not True
    ):
        raise RuntimeError(f"Plan receipt is incomplete for {op['name']}: {plan}")


def assert_apply_contract(applied: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    result = applied.get("result", {})
    checks = {
        "ok": applied.get("ok") is True,
        "mode": applied.get("mode") == "LiveApply",
        "operation": applied.get("operation") == op["operation"],
        "valueKind": applied.get("valueKind") == op["valueKind"],
        "changed": applied.get("changed") is True,
        "undoAvailable": applied.get("undoAvailableInEditor") is True,
        "resultValueKind": result.get("valueKind") == op["valueKind"],
        "dataTableKind": result.get("dataTableKind") == op["dataTableKind"],
        "rowName": result.get("rowName") == op["rowName"],
        "rowStruct": result.get("rowStructPath") == ROW_STRUCT,
        "noPropertyPath": "propertyPath" not in result,
        "resultChanged": result.get("changed") is True,
        "transactionRecorded": result.get("transactionRecorded") is True,
        "dirtyAfter": result.get("packageDirtyAfter") is True,
        "saved": result.get("saved") is False,
        "before": result.get("beforeValue") == op["before"],
        "after": result.get("afterValue") == op["after"],
        "transactionId": isinstance(result.get("transactionId"), str)
        and len(result["transactionId"]) == 36,
        "editorSessionId": isinstance(result.get("editorSessionId"), str)
        and bool(result["editorSessionId"]),
    }
    if op["name"] == "rename":
        checks["newRowName"] = result.get("newRowName") == op["newRowName"]
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise RuntimeError(f"LiveApply contract failed for {op['name']}: {failed}: {applied}")
    return result


async def apply_operation(
    session: ClientSession,
    op: dict[str, Any],
    revision: str,
    *,
    dry_run: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan = await plan_write(session, op["asset"], op["operation"], op["target"], op["value"])
    assert_plan_receipt(plan, op, revision)
    plan_id = str(plan["planId"])
    dry_result: dict[str, Any] | None = None
    if dry_run:
        dry_result = await call(session, "ue_dry_run_patch", {"plan_id": plan_id})
        gates = dry_result.get("gates", {})
        if (
            not dry_result.get("ok")
            or not isinstance(gates, dict)
            or not all(gates.values())
            or dry_result.get("report", {}).get("mode") != "DryRun"
            or dry_result.get("report", {}).get("saved") is not False
            or dry_result.get("report", {}).get("rolledBack") is not True
            or dry_result.get("report", {}).get("diskUnchanged") is not True
        ):
            raise RuntimeError(f"Dry Run gates failed for {op['name']}: {dry_result}")
    applied = await apply_live(session, plan_id)
    assert_apply_contract(applied, op)
    return applied, {"plan": plan, "dryRun": dry_result}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    fixture_report = json.loads(args.fixture_report.read_text(encoding="utf-8"))
    fixtures_by_id = {str(item["id"]): item for item in fixture_report["fixtures"]}
    package_files: dict[str, Path] = {}
    initial_revisions: dict[str, str] = {}
    for name, fixture_id in FIXTURE_IDS.items():
        entry = fixtures_by_id[fixture_id]
        package_files[name] = Path(str(entry["packageFilename"]))
        initial_revisions[name] = str(entry["revision"])
    package_hashes_before = {name: sha256(path) for name, path in package_files.items()}
    database_hash_before = sha256(args.database)
    revision_export_hash_before = directory_sha256(args.revision_export)

    parameters = StdioServerParameters(
        command="powershell.exe",
        env={**os.environ},
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
    undo_cases: list[dict[str, Any]] = []
    undo_changed_flags: list[bool] = []
    tx_by_name: dict[str, tuple[str, str]] = {}
    save_receipt = ""
    save_after_revision = ""
    no_op_verified = False
    rejected_undo_identities: list[str] = []
    chain_verified = False
    chain_ordered_undo = False

    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                if args.session_marker is not None:
                    args.session_marker.write_text("session-initialized\n", encoding="utf-8")
                listed = await session.list_tools()
                tool_names = [tool.name for tool in listed.tools]
                if tool_names != EXPECTED_TOOLS:
                    raise RuntimeError(f"Unexpected combined Tool list: {tool_names}")
                status = await call(session, "ue_editor_status", {})
                if not status.get("ok") or status["result"].get("pieState") != "stopped":
                    raise RuntimeError(f"Live Editor is not ready for writes: {status}")

                for asset_path in FIXTURE_ASSETS.values():
                    opened = await call(session, "ue_open_asset", {"asset_path": asset_path})
                    if not opened.get("ok") or not opened["result"].get("openAfter"):
                        raise RuntimeError(f"The fixture {asset_path} was not opened: {opened}")

                # A clean package must never be saveable: the authorized save
                # path requires an actual Dirty live write.
                clean_save = await call(
                    session,
                    "ue_save_authorized_asset",
                    {"asset_path": FIXTURE_ASSETS["remove"], "mode": "Preview"},
                )
                if clean_save.get("ok") or error_code(clean_save) != "live-editor-save-not-dirty":
                    raise RuntimeError(f"Clean-package save was not rejected: {clean_save}")

                # No-op on a clean package: the exact pre/post full-table snapshots
                # must compare equal, no transaction must be recorded, and nothing
                # may become Dirty.
                noop_op = {
                    "name": "cell-noop",
                    "asset": FIXTURE_ASSETS["cell"],
                    "operation": "setDataTableCell",
                    "target": {"rowName": ROW_ALPHA, "fieldName": "Count"},
                    "value": 1,
                    "valueKind": "data-table-cell",
                    "dataTableKind": "cell",
                    "rowName": ROW_ALPHA,
                    "before": ALPHA_INITIAL,
                    "after": ALPHA_INITIAL,
                }
                noop_plan = await plan_write(
                    session,
                    noop_op["asset"],
                    noop_op["operation"],
                    noop_op["target"],
                    noop_op["value"],
                )
                assert_plan_receipt(noop_plan, noop_op, initial_revisions["cell"])
                noop_applied = await apply_live(session, str(noop_plan["planId"]))
                noop_result = noop_applied.get("result", {})
                if (
                    not noop_applied.get("ok")
                    or noop_applied.get("changed") is not False
                    or noop_applied.get("undoAvailableInEditor") is not False
                    or noop_result.get("transactionRecorded") is not False
                    or noop_result.get("beforeValue") != ALPHA_INITIAL
                    or noop_result.get("afterValue") != ALPHA_INITIAL
                    or noop_result.get("packageDirtyAfter") is not False
                    or noop_result.get("saved") is not False
                ):
                    raise RuntimeError(f"DataTable no-op contract failed: {noop_applied}")
                noop_memory = (
                    await inspect_live(session, FIXTURE_ASSETS["cell"])
                ).get("result", {}).get("memory", {})
                if noop_memory.get("packageDirty") is not False:
                    raise RuntimeError(f"No-op marked the DataTable package Dirty: {noop_memory}")
                if sha256(package_files["cell"]) != package_hashes_before["cell"]:
                    raise RuntimeError("No-op changed the DataTable package on disk")
                no_op_verified = True

                # Multi-write chain on one asset: the second write must verify
                # the exact post-write table before continuing, and the two
                # retained records must undo in stack order back to the original.
                created_change_set = await call(
                    session,
                    "ue_create_change_set",
                    {
                        "title": "DataTable chained Undo regression",
                        "task_id": "task_datatable_chain_undo",
                    },
                )
                chain_id = str(created_change_set.get("changeSetId", ""))
                if (
                    not created_change_set.get("ok")
                    or created_change_set.get("status") != "planned"
                    or not chain_id
                ):
                    raise RuntimeError(f"Chain Change Set creation failed: {created_change_set}")

                chain_first_op = {
                    "name": "chain-first",
                    "asset": FIXTURE_ASSETS["cell"],
                    "operation": "setDataTableCell",
                    "target": {"rowName": ROW_ALPHA, "fieldName": "Count"},
                    "value": 42,
                    "valueKind": "data-table-cell",
                    "dataTableKind": "cell",
                    "rowName": ROW_ALPHA,
                    "before": ALPHA_INITIAL,
                    "after": ALPHA_NEW,
                }
                chain_first_plan = await plan_write(
                    session,
                    chain_first_op["asset"],
                    chain_first_op["operation"],
                    chain_first_op["target"],
                    chain_first_op["value"],
                )
                assert_plan_receipt(chain_first_plan, chain_first_op, initial_revisions["cell"])
                chain_first = await apply_live(
                    session, str(chain_first_plan["planId"]), chain_id
                )
                assert_apply_contract(chain_first, chain_first_op)
                chain_first_tx = str(chain_first["result"]["transactionId"])
                chain_first_session = str(chain_first["result"]["editorSessionId"])

                chain_second_op = {
                    "name": "chain-second",
                    "asset": FIXTURE_ASSETS["cell"],
                    "operation": "setDataTableCell",
                    "target": {"rowName": ROW_ALPHA, "fieldName": "Label"},
                    "value": "Alpha42",
                    "valueKind": "data-table-cell",
                    "dataTableKind": "cell",
                    "rowName": ROW_ALPHA,
                    "before": ALPHA_NEW,
                    "after": {**ALPHA_NEW, "Label": "Alpha42"},
                }
                chain_second_plan = await plan_write(
                    session,
                    chain_second_op["asset"],
                    chain_second_op["operation"],
                    chain_second_op["target"],
                    chain_second_op["value"],
                )
                assert_plan_receipt(chain_second_plan, chain_second_op, initial_revisions["cell"])
                chain_second = await apply_live(
                    session, str(chain_second_plan["planId"]), chain_id
                )
                assert_apply_contract(chain_second, chain_second_op)
                chain_second_tx = str(chain_second["result"]["transactionId"])
                chain_second_session = str(chain_second["result"]["editorSessionId"])
                if chain_second["result"].get("previousTransactionId") != chain_first_tx:
                    raise RuntimeError(f"Chained write did not bind the previous transaction: {chain_second}")

                chain_undo_second = await undo_live(
                    session,
                    FIXTURE_ASSETS["cell"],
                    chain_second_tx,
                    chain_second_session,
                )
                if (
                    not chain_undo_second.get("ok")
                    or chain_undo_second.get("changed") is not True
                    or chain_undo_second.get("result", {}).get("afterValue") != ALPHA_NEW
                ):
                    raise RuntimeError(f"Top chained Undo failed: {chain_undo_second}")
                chain_undo_first = await undo_live(
                    session,
                    FIXTURE_ASSETS["cell"],
                    chain_first_tx,
                    chain_first_session,
                )
                if (
                    not chain_undo_first.get("ok")
                    or chain_undo_first.get("changed") is not True
                    or chain_undo_first.get("result", {}).get("afterValue") != ALPHA_INITIAL
                ):
                    raise RuntimeError(f"Older chained Undo failed: {chain_undo_first}")
                chain_memory = (
                    await inspect_live(session, FIXTURE_ASSETS["cell"])
                ).get("result", {}).get("memory", {})
                if chain_memory.get("packageDirty") is not False:
                    raise RuntimeError(f"Chained Undo did not restore a clean package: {chain_memory}")
                if sha256(package_files["cell"]) != package_hashes_before["cell"]:
                    raise RuntimeError("Chained Undo changed the DataTable package on disk")
                chain_verified = True
                chain_ordered_undo = True

                for index, op in enumerate(OPERATIONS):
                    applied, receipts = await apply_operation(
                        session,
                        op,
                        initial_revisions[op["name"]],
                        dry_run=index == 0,
                    )
                    applied_result = applied["result"]
                    transaction_id = str(applied_result["transactionId"])
                    editor_session_id = str(applied_result["editorSessionId"])
                    if sha256(package_files[op["name"]]) != package_hashes_before[op["name"]]:
                        raise RuntimeError(f"LiveApply changed {op['name']} on disk before Save")

                    if op["name"] == "cell":
                        # Rejected Undo identities must not consume or mutate the
                        # pending transaction; the correct Undo follows.
                        wrong_transaction = await undo_live(
                            session,
                            op["asset"],
                            "11111111-2222-3333-4444-555555555555",
                            editor_session_id,
                        )
                        if (
                            wrong_transaction.get("ok")
                            or error_code(wrong_transaction) != "live-editor-write-undo-transaction-mismatch"
                        ):
                            raise RuntimeError(f"Wrong Undo transaction was not rejected: {wrong_transaction}")
                        rejected_undo_identities.append("transaction-mismatch")
                        wrong_session = await undo_live(
                            session,
                            op["asset"],
                            transaction_id,
                            "stale-session",
                        )
                        if (
                            wrong_session.get("ok")
                            or error_code(wrong_session) != "live-editor-write-undo-session-mismatch"
                        ):
                            raise RuntimeError(f"Wrong Undo session was not rejected: {wrong_session}")
                        rejected_undo_identities.append("session-mismatch")
                        still_dirty = (
                            await inspect_live(session, op["asset"])
                        ).get("result", {}).get("memory", {})
                        if still_dirty.get("packageDirty") is not True:
                            raise RuntimeError(f"Rejected Undo changed the package state: {still_dirty}")

                    undone = await undo_live(session, op["asset"], transaction_id, editor_session_id)
                    undone_result = undone.get("result", {})
                    undo_checks = {
                        "ok": undone.get("ok") is True,
                        "mode": undone.get("mode") == "LiveUndo",
                        "operation": undone.get("operation") == op["operation"],
                        "valueKind": undone.get("valueKind") == op["valueKind"],
                        "action": undone_result.get("action") == "undo-asset-property-live",
                        "transactionId": undone_result.get("transactionId") == transaction_id,
                        "transactionClosed": undone_result.get("transactionRecorded") is False,
                        "packageClean": undone_result.get("packageDirtyAfter") is False,
                        "dirtyAfter": undone_result.get("dirtyAfter") is False,
                        "saved": undone_result.get("saved") is False,
                        "truthfulChanged": undone.get("changed") is True,
                    }
                    if op["name"] == "add":
                        # Written state has the new row; restored state has none.
                        undo_checks["beforeValue"] = (
                            undone_result.get("beforeValue") == applied_result.get("afterValue")
                        )
                        undo_checks["afterValue"] = undone_result.get("afterValue") is None
                    else:
                        undo_checks["beforeValue"] = (
                            undone_result.get("beforeValue") == applied_result.get("afterValue")
                        )
                        undo_checks["afterValue"] = (
                            undone_result.get("afterValue") == applied_result.get("beforeValue")
                        )
                    failed = [name for name, passed in undo_checks.items() if not passed]
                    if failed:
                        raise RuntimeError(f"Undo contract failed for {op['name']}: {failed}: {undone}")
                    undo_changed_flags.append(bool(undone.get("changed")))
                    memory = (await inspect_live(session, op["asset"])).get("result", {}).get("memory", {})
                    if memory.get("packageDirty") is not False:
                        raise RuntimeError(f"Undo left {op['name']} Dirty: {memory}")
                    if sha256(package_files[op["name"]]) != package_hashes_before[op["name"]]:
                        raise RuntimeError(f"Undo changed {op['name']} on disk")

                    # Re-applying is the reverse proof: the new pre-write value is
                    # read live after Undo.  AddRow instead proves the row is gone,
                    # because a still-present row is rejected as row-exists.
                    reapplied, _ = await apply_operation(
                        session, op, initial_revisions[op["name"]]
                    )
                    reapplied_result = reapplied["result"]
                    tx_by_name[op["name"]] = (
                        str(reapplied_result["transactionId"]),
                        str(reapplied_result["editorSessionId"]),
                    )
                    if op["name"] != "add":
                        if reapplied_result.get("beforeValue") != applied_result.get("beforeValue"):
                            raise RuntimeError(
                                f"Re-apply did not observe the restored row for {op['name']}: {reapplied}"
                            )
                    undo_cases.append(
                        {
                            "operation": op["operation"],
                            "valueKind": op["valueKind"],
                            "undoRestored": True,
                            "reapplied": True,
                            "undoChangedFlag": undone.get("changed"),
                            "dryRun": receipts["dryRun"] is not None,
                        }
                    )

                # Save must require the one-time receipt confirmation phrase.
                # fields stays dirty and is never saved: it is the negative
                # control proving Preview alone cannot persist a live write.
                fields_preview = await call(
                    session,
                    "ue_save_authorized_asset",
                    {"asset_path": FIXTURE_ASSETS["fields"], "mode": "Preview"},
                )
                if not fields_preview.get("ok") or not str(fields_preview.get("saveReceipt", "")).startswith("save_"):
                    raise RuntimeError(f"Fields save Preview failed: {fields_preview}")
                wrong_save = await call(
                    session,
                    "ue_save_authorized_asset",
                    {
                        "asset_path": FIXTURE_ASSETS["fields"],
                        "mode": "Commit",
                        "save_receipt": fields_preview["saveReceipt"],
                        "confirmation": "SAVE wrong",
                    },
                )
                if wrong_save.get("ok") or error_code(wrong_save) != "save-confirmation-required":
                    raise RuntimeError(f"Wrong save confirmation was not rejected: {wrong_save}")
                if sha256(package_files["fields"]) != package_hashes_before["fields"]:
                    raise RuntimeError("Rejected save confirmation changed the fields package on disk")

                # Verify before Save: the write is Dirty and the independent
                # reload path must report not-saved instead of faking success.
                not_saved = await call(
                    session, "ue_verify_live_write", {"asset_path": FIXTURE_ASSETS["cell"]}
                )
                if (
                    not not_saved.get("ok")
                    or not_saved.get("state") != "not-saved"
                    or not_saved.get("saved") is not False
                    or not_saved.get("verified") is not False
                    or not_saved.get("undoAvailable") is not True
                    or not_saved.get("diskRevision") != initial_revisions["cell"]
                ):
                    raise RuntimeError(f"Pre-Save verification did not report not-saved: {not_saved}")

                async def save_one(name: str, expected_after: Any, check_wrong: bool) -> dict[str, Any]:
                    asset_path = FIXTURE_ASSETS[name]
                    expected_revision = initial_revisions[name]
                    preview = await call(
                        session,
                        "ue_save_authorized_asset",
                        {"asset_path": asset_path, "mode": "Preview"},
                    )
                    if (
                        not preview.get("ok")
                        or preview.get("mode") != "Preview"
                        or preview.get("packageDirty") is not True
                        or not str(preview.get("saveReceipt", "")).startswith("save_")
                        or preview.get("expectedDiskRevision") != expected_revision
                    ):
                        raise RuntimeError(f"{name} save Preview failed: {preview}")
                    receipt = str(preview["saveReceipt"])
                    if check_wrong:
                        wrong = await call(
                            session,
                            "ue_save_authorized_asset",
                            {
                                "asset_path": asset_path,
                                "mode": "Commit",
                                "save_receipt": receipt,
                                "confirmation": "SAVE wrong",
                            },
                        )
                        if wrong.get("ok") or error_code(wrong) != "save-confirmation-required":
                            raise RuntimeError(f"Wrong {name} save confirmation was not rejected: {wrong}")
                        if sha256(package_files[name]) != package_hashes_before[name]:
                            raise RuntimeError(f"Rejected {name} save confirmation changed the package on disk")
                    saved = await call(
                        session,
                        "ue_save_authorized_asset",
                        {
                            "asset_path": asset_path,
                            "mode": "Commit",
                            "save_receipt": receipt,
                            "confirmation": f"SAVE {receipt}",
                        },
                    )
                    if (
                        not saved.get("ok")
                        or saved.get("mode") != "Commit"
                        or saved.get("saved") is not True
                        or saved.get("verified") is not True
                        or saved.get("revisionChanged") is not True
                        or saved.get("beforeRevision") != expected_revision
                        or str(saved.get("afterRevision", "")) == expected_revision
                        or saved.get("liveWriteSaved") is not True
                    ):
                        raise RuntimeError(f"{name} authorized Save contract failed: {saved}")
                    verified = await call(
                        session, "ue_verify_live_write", {"asset_path": asset_path}
                    )
                    after_revision = str(saved["afterRevision"])
                    if (
                        not verified.get("ok")
                        or verified.get("state") != "verified"
                        or verified.get("saved") is not True
                        or verified.get("verified") is not True
                        or verified.get("undoAvailable") is not False
                        or verified.get("expectedValue") != expected_after
                        or verified.get("exportedValue") != expected_after
                        or verified.get("actualRevision") != after_revision
                        or verified.get("expectedDiskRevision") != after_revision
                        or verified.get("packageDirty") is not False
                    ):
                        raise RuntimeError(f"{name} post-Save independent verification failed: {verified}")
                    return {
                        "asset": asset_path,
                        "saveReceipt": receipt,
                        "beforeRevision": expected_revision,
                        "afterRevision": after_revision,
                        "appliedValue": expected_after,
                    }

                saves: dict[str, dict[str, Any]] = {}
                saves["cell"] = await save_one("cell", ALPHA_NEW, check_wrong=True)
                saves["remove"] = await save_one("remove", None, check_wrong=False)
                saves["rename"] = await save_one("rename", GAMMA_INITIAL, check_wrong=False)
                save_receipt = str(saves["cell"]["saveReceipt"])
                save_after_revision = str(saves["cell"]["afterRevision"])

                for name in ("cell", "remove"):
                    structure_transaction_id, structure_session_id = tx_by_name[name]
                    rejected = await undo_live(
                        session,
                        FIXTURE_ASSETS[name],
                        structure_transaction_id,
                        structure_session_id,
                    )
                    if rejected.get("ok") or error_code(rejected) != "live-editor-write-undo-package-saved":
                        raise RuntimeError(
                            f"Save did not make {name} non-undoable: {rejected}"
                        )

                stale_plan = await plan_write(
                    session,
                    FIXTURE_ASSETS["cell"],
                    "setDataTableCell",
                    {"rowName": ROW_ALPHA, "fieldName": "Count"},
                    43,
                )
                if stale_plan.get("ok") or error_code(stale_plan) != "index-stale":
                    raise RuntimeError(f"Stale Revision was not rejected: {stale_plan}")

                dirty = await dirty_paths(session)
                expected_dirty = {
                    FIXTURE_ASSETS["fields"],
                    FIXTURE_ASSETS["add"],
                }
                for saved_name in ("cell", "remove", "rename"):
                    if FIXTURE_ASSETS[saved_name] in dirty:
                        raise RuntimeError(f"Saved {saved_name} asset is still Dirty: {sorted(dirty)}")
                if not expected_dirty.issubset(dirty):
                    raise RuntimeError(f"Unsaved writes are not Dirty: {sorted(dirty)}")

                expected_changed = {"cell", "remove", "rename"}
                changed = {
                    name
                    for name, path in package_files.items()
                    if sha256(path) != package_hashes_before[name]
                }
                if changed != expected_changed:
                    raise RuntimeError(f"Unexpected disk package changes: {sorted(changed)}")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("The immutable SQLite index changed during the live test")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("The frozen Revision Export changed during the live test")

    if len(undo_cases) != len(OPERATIONS):
        raise RuntimeError(f"Only {len(undo_cases)} of {len(OPERATIONS)} undo families were verified")
    if not all(undo_changed_flags):
        raise RuntimeError("One or more Undo results did not report changed=true")
    if not no_op_verified:
        raise RuntimeError("The DataTable no-op case was not verified")
    if len(rejected_undo_identities) != 2:
        raise RuntimeError(f"Rejected Undo identity count is unexpected: {rejected_undo_identities}")
    if not chain_verified or not chain_ordered_undo:
        raise RuntimeError("Chained DataTable writes/Undo were not verified")

    known_defects: list[str] = []
    if not all(undo_changed_flags):
        known_defects.append(
            "One or more DataTable Undo results did not report changed=true; "
            "the truthful changed flag must be fixed before accepting Undo."
        )
    known_defects.append(
        "Redo still has no MCP Tool: Undo is GEditor->UndoTransaction(true) and is redoable "
        "inside the Editor, but no editor.redoAssetPropertyLive method or Tool exists. "
        "No unsafe redo surface was added."
    )
    report = {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(EXPECTED_TOOLS),
        "successCases": len(OPERATIONS),
        "undoVerified": len(undo_cases),
        "undoCases": undo_cases,
        "undoChangedFlags": undo_changed_flags,
        "undoChangedAllTrue": all(undo_changed_flags),
        "noOpVerified": no_op_verified,
        "rejectedUndoIdentities": rejected_undo_identities,
        "chainedWritesVerified": chain_verified,
        "chainedUndoOrdered": chain_ordered_undo,
        "dryRunGatesVerified": True,
        "saves": saves,
        "save": {
            "asset": FIXTURE_ASSETS["cell"],
            "saveReceipt": save_receipt,
            "afterRevision": save_after_revision,
            "independentVerify": True,
            "confirmedOnly": True,
        },
        "staleRevisionRejected": True,
        "diskPackageChanged": sorted(changed),
        "unsavedPackages": sorted(expected_dirty),
        "databaseHashUnchanged": True,
        "revisionExportHashUnchanged": True,
        "knownDefects": known_defects,
    }
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--revision-export", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--fixture-report", type=Path, required=True)
    parser.add_argument("--error-log", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--session-marker", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

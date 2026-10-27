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


async def apply_live(session: ClientSession, plan_id: str) -> dict[str, Any]:
    return await call(
        session,
        "ue_apply_asset_property_live",
        {"plan_id": plan_id, "confirmation": f"LIVE APPLY {plan_id}"},
    )


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
    undo_blocked: list[dict[str, Any]] = []
    undo_changed_flags: list[bool] = []
    save_receipt = ""
    save_after_revision = ""
    cell_transaction_id = ""
    cell_editor_session_id = ""

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

                    undone = await undo_live(session, op["asset"], transaction_id, editor_session_id)
                    if op["name"] in {"rename", "remove"} and not undone.get("ok"):
                        # Known product defect: RevertLiveWriteTransaction calls
                        # ReadBefore on the source RowName, which no longer exists
                        # after rename/remove.  The Undo is refused before the
                        # Editor transaction is touched.
                        if error_code(undone) != "live-editor-write-undo-verify-failed":
                            raise RuntimeError(
                                f"Expected the documented {op['name']} Undo blocker but got: {undone}"
                            )
                        memory = (await inspect_live(session, op["asset"])).get("result", {}).get("memory", {})
                        if memory.get("packageDirty") is not True:
                            raise RuntimeError(
                                f"Refused {op['name']} Undo unexpectedly cleaned the package: {memory}"
                            )
                        if sha256(package_files[op["name"]]) != package_hashes_before[op["name"]]:
                            raise RuntimeError(f"Refused {op['name']} Undo changed the package on disk")
                        undo_blocked.append(
                            {
                                "operation": op["operation"],
                                "errorCode": error_code(undone),
                                "reason": "Revert ReadBefore(sourceRowName) cannot resolve a renamed/removed row.",
                            }
                        )
                        continue
                    if op["name"] in {"rename", "remove"}:
                        # Forward-compatible path: if the product learns to undo
                        # structural row operations, prove restoration by
                        # re-applying and reading the restored source row.
                        if undone.get("mode") != "LiveUndo":
                            raise RuntimeError(f"Undo mode is unexpected for {op['name']}: {undone}")
                        memory = (await inspect_live(session, op["asset"])).get("result", {}).get("memory", {})
                        if memory.get("packageDirty") is not False:
                            raise RuntimeError(f"Undo left {op['name']} Dirty: {memory}")
                        reapplied, _ = await apply_operation(
                            session, op, initial_revisions[op["name"]]
                        )
                        if reapplied.get("result", {}).get("beforeValue") != applied_result.get("beforeValue"):
                            raise RuntimeError(
                                f"Re-apply did not observe the restored row for {op['name']}: {reapplied}"
                            )
                        undo_changed_flags.append(bool(undone.get("changed")))
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
                        continue

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
                    }
                    if op["name"] != "add":
                        undo_checks["beforeValue"] = (
                            undone_result.get("beforeValue") == applied_result.get("afterValue")
                        )
                        undo_checks["afterValue"] = (
                            undone_result.get("afterValue") == applied_result.get("beforeValue")
                        )
                    else:
                        # AddRow ReadBefore is defined as null on both sides.
                        undo_checks["beforeValue"] = undone_result.get("beforeValue") is None
                        undo_checks["afterValue"] = undone_result.get("afterValue") is None
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
                    if op["name"] == "cell":
                        cell_transaction_id = str(reapplied_result["transactionId"])
                        cell_editor_session_id = str(reapplied_result["editorSessionId"])
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

                cell_preview = await call(
                    session,
                    "ue_save_authorized_asset",
                    {"asset_path": FIXTURE_ASSETS["cell"], "mode": "Preview"},
                )
                if (
                    not cell_preview.get("ok")
                    or cell_preview.get("mode") != "Preview"
                    or cell_preview.get("packageDirty") is not True
                    or not str(cell_preview.get("saveReceipt", "")).startswith("save_")
                    or cell_preview.get("expectedDiskRevision") != initial_revisions["cell"]
                ):
                    raise RuntimeError(f"Cell save Preview failed: {cell_preview}")
                cell_receipt = str(cell_preview["saveReceipt"])
                wrong_cell_save = await call(
                    session,
                    "ue_save_authorized_asset",
                    {
                        "asset_path": FIXTURE_ASSETS["cell"],
                        "mode": "Commit",
                        "save_receipt": cell_receipt,
                        "confirmation": "SAVE wrong",
                    },
                )
                if wrong_cell_save.get("ok") or error_code(wrong_cell_save) != "save-confirmation-required":
                    raise RuntimeError(f"Wrong cell save confirmation was not rejected: {wrong_cell_save}")
                if sha256(package_files["cell"]) != package_hashes_before["cell"]:
                    raise RuntimeError("Rejected cell save confirmation changed the package on disk")

                saved = await call(
                    session,
                    "ue_save_authorized_asset",
                    {
                        "asset_path": FIXTURE_ASSETS["cell"],
                        "mode": "Commit",
                        "save_receipt": cell_receipt,
                        "confirmation": f"SAVE {cell_receipt}",
                    },
                )
                if (
                    not saved.get("ok")
                    or saved.get("mode") != "Commit"
                    or saved.get("saved") is not True
                    or saved.get("verified") is not True
                    or saved.get("revisionChanged") is not True
                    or saved.get("beforeRevision") != initial_revisions["cell"]
                    or str(saved.get("afterRevision", "")) == initial_revisions["cell"]
                    or saved.get("liveWriteSaved") is not True
                ):
                    raise RuntimeError(f"Cell authorized Save contract failed: {saved}")
                save_receipt = cell_receipt
                save_after_revision = str(saved["afterRevision"])

                verified = await call(
                    session, "ue_verify_live_write", {"asset_path": FIXTURE_ASSETS["cell"]}
                )
                cell_after_value = ALPHA_NEW
                if (
                    not verified.get("ok")
                    or verified.get("state") != "verified"
                    or verified.get("saved") is not True
                    or verified.get("verified") is not True
                    or verified.get("undoAvailable") is not False
                    or verified.get("expectedValue") != cell_after_value
                    or verified.get("exportedValue") != cell_after_value
                    or verified.get("actualRevision") != save_after_revision
                    or verified.get("expectedDiskRevision") != save_after_revision
                    or verified.get("packageDirty") is not False
                ):
                    raise RuntimeError(f"Post-Save independent verification failed: {verified}")

                undo_after_save = await undo_live(
                    session,
                    FIXTURE_ASSETS["cell"],
                    cell_transaction_id,
                    cell_editor_session_id,
                )
                if undo_after_save.get("ok") or error_code(undo_after_save) != "live-editor-write-undo-package-saved":
                    raise RuntimeError(
                        f"Save did not make the live write non-undoable: {undo_after_save}"
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
                    FIXTURE_ASSETS["rename"],
                    FIXTURE_ASSETS["remove"],
                }
                if FIXTURE_ASSETS["cell"] in dirty:
                    raise RuntimeError(f"Saved cell asset is still Dirty: {sorted(dirty)}")
                if not expected_dirty.issubset(dirty):
                    raise RuntimeError(f"Unsaved writes are not Dirty: {sorted(dirty)}")

                expected_changed = {"cell"}
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

    known_defects: list[str] = []
    if undo_blocked:
        known_defects.append(
            "Undo is unreachable for renameDataTableRow and removeDataTableRow: "
            "RevertLiveWriteTransaction first calls FLiveWriteDataTableIO::ReadBefore on the "
            "source RowName, which no longer exists after the rename/remove, so the Undo is "
            "refused with live-editor-write-undo-verify-failed."
        )
    if undo_changed_flags and all(flag is False for flag in undo_changed_flags):
        known_defects.append(
            "DataTable Undo reports changed=false on every supported row family because "
            "FLiveWriteDataTableIO::SemanticEqual re-captures the restored table in the "
            "second ReadBefore; restoration is instead proven by the Undo afterValue and "
            "by the re-apply beforeValue read live from the Editor."
        )
    known_defects.append(
        "Redo has no MCP Tool: Undo is GEditor->UndoTransaction(true) and is redoable "
        "inside the Editor, but no editor.redoAssetPropertyLive method or Tool exists."
    )
    report = {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(EXPECTED_TOOLS),
        "successCases": len(OPERATIONS),
        "undoVerified": len(undo_cases),
        "undoBlocked": undo_blocked,
        "undoCases": undo_cases,
        "undoChangedFlags": undo_changed_flags,
        "undoChangedAllFalse": all(flag is False for flag in undo_changed_flags),
        "dryRunGatesVerified": True,
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

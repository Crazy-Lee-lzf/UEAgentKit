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
    "non_table": "data-table-non-table-asset",
}

FIXTURE_ASSETS = {
    "cell": f"{DATA_TABLES_ROOT}/DT_CellTarget.DT_CellTarget",
    "fields": f"{DATA_TABLES_ROOT}/DT_FieldsTarget.DT_FieldsTarget",
    "add": f"{DATA_TABLES_ROOT}/DT_AddTarget.DT_AddTarget",
    "rename": f"{DATA_TABLES_ROOT}/DT_RenameTarget.DT_RenameTarget",
    "remove": f"{DATA_TABLES_ROOT}/DT_RemoveTarget.DT_RemoveTarget",
    "non_table": f"{DATA_TABLES_ROOT}/DA_ScalarNonTable.DA_ScalarNonTable",
}

ROW_ALPHA = "RowAlpha"
ROW_BETA = "RowBeta"
ROW_GAMMA = "RowGamma"
ROW_DELTA = "RowDelta"
ROW_RENAMED = "RowGammaRenamed"
ROW_MISSING = "RowMissing"

ALPHA_INITIAL = {"Count": 1, "Label": "Alpha", "bEnabled": True}
BETA_INITIAL = {"Count": 2, "Label": "Beta", "bEnabled": False}
GAMMA_INITIAL = {"Count": 3, "Label": "Gamma", "bEnabled": True}
ALPHA_NEW = {"Count": 42, "Label": "Alpha", "bEnabled": True}
BETA_NEW = {"Count": 22, "Label": "Beta New", "bEnabled": True}
DELTA_NEW = {"Count": 4, "Label": "Delta", "bEnabled": True}


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


def error_issue_codes(result: dict[str, Any]) -> set[str]:
    details = result.get("error", {}).get("details", {})
    issues = details.get("issueCodes", [])
    return {str(issue) for issue in issues} if isinstance(issues, list) else set()


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


async def plan_data_table(
    session: ClientSession,
    asset_path: str,
    operation: str,
    target: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    return await call(
        session,
        "ue_plan_patch",
        {
            "asset_path": asset_path,
            "operation": operation,
            "target": target,
            "value": value,
            "description": "Real UE5.6 Live Editor DataTable write regression",
        },
    )


async def apply_live(session: ClientSession, plan_id: str, confirmation: str) -> dict[str, Any]:
    return await call(
        session,
        "ue_apply_asset_property_live",
        {"plan_id": plan_id, "confirmation": confirmation},
    )


async def expect_plan_rejection(
    session: ClientSession,
    asset_path: str,
    operation: str,
    target: dict[str, Any],
    value: Any,
    expected_issue: str,
) -> dict[str, Any]:
    rejected = await plan_data_table(session, FIXTURE_ASSETS["cell"], operation, target, value)
    if rejected.get("ok") or expected_issue not in error_issue_codes(rejected):
        raise RuntimeError(
            f"Expected Plan rejection issue {expected_issue} for {operation} but got: {rejected}"
        )
    return rejected


async def assert_clean_failure_invariants(
    session: ClientSession,
    asset_path: str,
    package_hashes_before: dict[str, str],
    database_hash_before: str,
    revision_export_hash_before: str,
    package_files: dict[str, Path],
    database: Path,
    revision_export: Path,
) -> None:
    inspected = await call(session, "ue_inspect_asset_live", {"asset_path": asset_path})
    memory = inspected.get("result", {}).get("memory", {})
    if (
        not inspected.get("ok")
        or memory.get("loaded") is not True
        or memory.get("packageDirty") is not False
        or memory.get("openInAssetEditor") is not True
    ):
        raise RuntimeError(f"Failed apply left the asset in an unexpected memory state: {inspected}")
    dirty = await call(session, "ue_get_dirty_assets", {})
    dirty_items = dirty.get("result", {}).get("items", [])
    dirty_paths: set[str] = set()
    for item in dirty_items:
        if isinstance(item, dict) and isinstance(item.get("assetPaths"), list):
            dirty_paths.update(str(path) for path in item["assetPaths"])
    if dirty_paths.intersection(FIXTURE_ASSETS.values()):
        raise RuntimeError(f"Failed apply left a fixture package Dirty: {dirty}")
    for name, path in package_files.items():
        if sha256(path) != package_hashes_before[name]:
            raise RuntimeError(f"Failed apply changed the fixture package {name} on disk")
    if sha256(database) != database_hash_before:
        raise RuntimeError("Failed apply modified the immutable SQLite index")
    if directory_sha256(revision_export) != revision_export_hash_before:
        raise RuntimeError("Failed apply modified the frozen Revision Export")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    fixture_report = json.loads(args.fixture_report.read_text(encoding="utf-8"))
    package_files: dict[str, Path] = {}
    for fixture in fixture_report["fixtures"]:
        package_files[str(fixture["id"])] = Path(str(fixture["packageFilename"]))
    package_hashes_before = {
        name: sha256(path) for name, path in package_files.items()
    }
    database_hash_before = sha256(args.database)
    revision_export_hash_before = directory_sha256(args.revision_export)

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
    success_cases: list[dict[str, Any]] = []
    plan_rejections = 0
    bridge_rejections = 0
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

                for name, asset_path in FIXTURE_ASSETS.items():
                    opened = await call(session, "ue_open_asset", {"asset_path": asset_path})
                    if not opened.get("ok") or not opened["result"].get("openAfter"):
                        raise RuntimeError(f"The fixture {name} was not opened: {opened}")

                # Plan-time rejections (nothing reaches the Editor Bridge).
                plan_failures: list[tuple[str, dict[str, Any], Any, str]] = [
                    (
                        "setDataTableCell",
                        {"rowName": ROW_ALPHA, "fieldName": "Count"},
                        {"Count": 5},
                        "operation-value-type",
                    ),
                    (
                        "setDataTableCell",
                        {"rowName": ROW_ALPHA, "fieldName": "NotAField"},
                        5,
                        "data-table-field-not-allowed",
                    ),
                    (
                        "addDataTableRow",
                        {"rowName": ROW_ALPHA},
                        DELTA_NEW,
                        "data-table-row-exists",
                    ),
                    (
                        "removeDataTableRow",
                        {"rowName": ROW_MISSING},
                        True,
                        "data-table-row-missing",
                    ),
                    (
                        "renameDataTableRow",
                        {"rowName": ROW_GAMMA, "newRowName": ROW_GAMMA},
                        True,
                        "data-table-row-name-unchanged",
                    ),
                    (
                        "renameDataTableRow",
                        {"rowName": ROW_ALPHA, "newRowName": ROW_BETA},
                        True,
                        "data-table-row-exists",
                    ),
                    (
                        "removeDataTableRow",
                        {"rowName": ROW_BETA},
                        False,
                        "operation-value-type",
                    ),
                ]
                for operation, target, value, expected_issue in plan_failures:
                    rejected = await expect_plan_rejection(
                        session,
                        FIXTURE_ASSETS["cell"],
                        operation,
                        target,
                        value,
                        expected_issue,
                    )
                    if rejected.get("ok"):
                        raise RuntimeError(f"Plan unexpectedly succeeded: {operation}")
                    plan_rejections += 1
                await assert_clean_failure_invariants(
                    session,
                    FIXTURE_ASSETS["cell"],
                    package_hashes_before,
                    database_hash_before,
                    revision_export_hash_before,
                    package_files,
                    args.database,
                    args.revision_export,
                )

                # Bridge rejection: the row does not exist on this instance even
                # though the fixed Policy authorizes the field.
                missing_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["rename"],
                    "setDataTableCell",
                    {"rowName": ROW_MISSING, "fieldName": "Count"},
                    5,
                )
                if not missing_plan.get("ok"):
                    raise RuntimeError(
                        f"Missing-row Plan unexpectedly rejected before the Bridge: {missing_plan}"
                    )
                missing_plan_id = str(missing_plan["planId"])
                rejected = await apply_live(session, missing_plan_id, f"LIVE APPLY {missing_plan_id}")
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-data-table-row-not-found":
                    raise RuntimeError(
                        f"Expected live-editor-write-data-table-row-not-found but got: {rejected}"
                    )
                bridge_rejections += 1

                # No-op: setting a cell to its seeded value must not change the row,
                # create Undo, or dirty the package.
                noop_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["cell"],
                    "setDataTableCell",
                    {"rowName": ROW_ALPHA, "fieldName": "Label"},
                    "Alpha",
                )
                if not noop_plan.get("ok") or noop_plan.get("operation") != "setDataTableCell":
                    raise RuntimeError(f"No-op DataTable Plan failed: {noop_plan}")
                noop_plan_id = str(noop_plan["planId"])
                wrong_confirmation = await apply_live(session, noop_plan_id, "LIVE APPLY wrong")
                if wrong_confirmation.get("ok") or error_code(wrong_confirmation) != "live-editor-write-confirmation-required":
                    raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {wrong_confirmation}")
                noop = await apply_live(session, noop_plan_id, f"LIVE APPLY {noop_plan_id}")
                noop_result = noop.get("result", {})
                if (
                    not noop.get("ok")
                    or noop.get("mode") != "LiveApply"
                    or noop.get("operation") != "setDataTableCell"
                    or noop.get("valueKind") != "data-table-cell"
                    or noop.get("changed") is not False
                    or noop.get("saved") is not False
                    or noop.get("diskRevisionChanged") is not False
                    or noop.get("undoAvailableInEditor") is not False
                    or noop_result.get("operation") != "setDataTableCell"
                    or noop_result.get("valueKind") != "data-table-cell"
                    or noop_result.get("rowName") != ROW_ALPHA
                    or noop_result.get("fieldName") != "Label"
                    or noop_result.get("dataTableKind") != "cell"
                    or noop_result.get("rowStructPath") != ROW_STRUCT
                    or "propertyPath" in noop_result
                    or noop_result.get("changed") is not False
                    or noop_result.get("transactionRecorded") is not False
                    or noop_result.get("packageDirtyAfter") is not False
                    or noop_result.get("dirtyAfter") is not False
                    or noop_result.get("saved") is not False
                    or noop_result.get("dirtyBefore") is not False
                    or noop_result.get("packageDirtyBefore") is not False
                    or noop_result.get("beforeValue") != ALPHA_INITIAL
                    or noop_result.get("afterValue") != ALPHA_INITIAL
                ):
                    raise RuntimeError(f"No-op DataTable LiveApply contract is broken: {noop}")
                inspected = await call(
                    session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["cell"]}
                )
                memory = inspected.get("result", {}).get("memory", {})
                if memory.get("packageDirty") is not False:
                    raise RuntimeError(f"No-op marked the package Dirty: {inspected}")

                # Real writes: cell, row fields, add, rename, remove.
                cell_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["cell"],
                    "setDataTableCell",
                    {"rowName": ROW_ALPHA, "fieldName": "Count"},
                    42,
                )
                if not cell_plan.get("ok") or cell_plan.get("operation") != "setDataTableCell":
                    raise RuntimeError(f"DataTable cell Plan failed: {cell_plan}")
                cell_plan_id = str(cell_plan["planId"])
                rejected = await apply_live(session, cell_plan_id, "LIVE APPLY wrong")
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-confirmation-required":
                    raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {rejected}")
                applied = await apply_live(session, cell_plan_id, f"LIVE APPLY {cell_plan_id}")
                result = applied.get("result", {})
                if (
                    not applied.get("ok")
                    or applied.get("mode") != "LiveApply"
                    or applied.get("operation") != "setDataTableCell"
                    or applied.get("valueKind") != "data-table-cell"
                    or applied.get("changed") is not True
                    or applied.get("undoAvailableInEditor") is not True
                    or result.get("operation") != "setDataTableCell"
                    or result.get("valueKind") != "data-table-cell"
                    or result.get("rowName") != ROW_ALPHA
                    or result.get("fieldName") != "Count"
                    or result.get("dataTableKind") != "cell"
                    or result.get("rowStructPath") != ROW_STRUCT
                    or "propertyPath" in result
                    or result.get("changed") is not True
                    or result.get("transactionRecorded") is not True
                    or result.get("transactionTitle") != "UE Agent Kit: Set DataTable Value"
                    or result.get("packageDirtyAfter") is not True
                    or result.get("dirtyAfter") is not True
                    or result.get("saved") is not False
                    or result.get("assetOpen") is not True
                    or result.get("loadedByBridge") is not False
                    or result.get("dirtyBefore") is not False
                    or result.get("packageDirtyBefore") is not False
                    or result.get("beforeValue") != ALPHA_INITIAL
                    or result.get("afterValue") != ALPHA_NEW
                ):
                    raise RuntimeError(f"DataTable cell LiveApply result is incomplete: {applied}")
                success_cases.append({"operation": "setDataTableCell", "rowName": ROW_ALPHA})

                fields_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["fields"],
                    "setDataTableRowFields",
                    {"rowName": ROW_BETA},
                    {"Count": 22, "Label": "Beta New", "bEnabled": True},
                )
                if not fields_plan.get("ok") or fields_plan.get("operation") != "setDataTableRowFields":
                    raise RuntimeError(f"DataTable row-fields Plan failed: {fields_plan}")
                fields_plan_id = str(fields_plan["planId"])
                applied = await apply_live(session, fields_plan_id, f"LIVE APPLY {fields_plan_id}")
                result = applied.get("result", {})
                if (
                    not applied.get("ok")
                    or applied.get("operation") != "setDataTableRowFields"
                    or applied.get("valueKind") != "data-table-row-fields"
                    or applied.get("changed") is not True
                    or result.get("valueKind") != "data-table-row-fields"
                    or result.get("rowName") != ROW_BETA
                    or result.get("dataTableKind") != "row-fields"
                    or result.get("changed") is not True
                    or result.get("transactionRecorded") is not True
                    or result.get("beforeValue") != BETA_INITIAL
                    or result.get("afterValue") != BETA_NEW
                ):
                    raise RuntimeError(f"DataTable row-fields LiveApply result is incomplete: {applied}")
                success_cases.append({"operation": "setDataTableRowFields", "rowName": ROW_BETA})

                add_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["add"],
                    "addDataTableRow",
                    {"rowName": ROW_DELTA},
                    DELTA_NEW,
                )
                if not add_plan.get("ok") or add_plan.get("operation") != "addDataTableRow":
                    raise RuntimeError(f"DataTable add-row Plan failed: {add_plan}")
                add_plan_id = str(add_plan["planId"])
                applied = await apply_live(session, add_plan_id, f"LIVE APPLY {add_plan_id}")
                result = applied.get("result", {})
                if (
                    not applied.get("ok")
                    or applied.get("operation") != "addDataTableRow"
                    or applied.get("valueKind") != "data-table-row-add"
                    or applied.get("changed") is not True
                    or result.get("valueKind") != "data-table-row-add"
                    or result.get("rowName") != ROW_DELTA
                    or result.get("dataTableKind") != "row-add"
                    or result.get("changed") is not True
                    or result.get("transactionRecorded") is not True
                    or result.get("beforeValue") is not None
                    or result.get("afterValue") != DELTA_NEW
                ):
                    raise RuntimeError(f"DataTable add-row LiveApply result is incomplete: {applied}")
                success_cases.append({"operation": "addDataTableRow", "rowName": ROW_DELTA})

                rename_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["rename"],
                    "renameDataTableRow",
                    {"rowName": ROW_GAMMA, "newRowName": ROW_RENAMED},
                    True,
                )
                if not rename_plan.get("ok") or rename_plan.get("operation") != "renameDataTableRow":
                    raise RuntimeError(f"DataTable rename Plan failed: {rename_plan}")
                rename_plan_id = str(rename_plan["planId"])
                applied = await apply_live(session, rename_plan_id, f"LIVE APPLY {rename_plan_id}")
                result = applied.get("result", {})
                if (
                    not applied.get("ok")
                    or applied.get("operation") != "renameDataTableRow"
                    or applied.get("valueKind") != "data-table-row-rename"
                    or applied.get("changed") is not True
                    or result.get("valueKind") != "data-table-row-rename"
                    or result.get("rowName") != ROW_GAMMA
                    or result.get("newRowName") != ROW_RENAMED
                    or result.get("dataTableKind") != "row-rename"
                    or result.get("changed") is not True
                    or result.get("transactionRecorded") is not True
                    or result.get("beforeValue") != GAMMA_INITIAL
                    or result.get("afterValue") != GAMMA_INITIAL
                ):
                    raise RuntimeError(f"DataTable rename LiveApply result is incomplete: {applied}")
                success_cases.append({"operation": "renameDataTableRow", "rowName": ROW_GAMMA})

                remove_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["remove"],
                    "removeDataTableRow",
                    {"rowName": ROW_BETA},
                    True,
                )
                if not remove_plan.get("ok") or remove_plan.get("operation") != "removeDataTableRow":
                    raise RuntimeError(f"DataTable remove Plan failed: {remove_plan}")
                remove_plan_id = str(remove_plan["planId"])
                applied = await apply_live(session, remove_plan_id, f"LIVE APPLY {remove_plan_id}")
                result = applied.get("result", {})
                if (
                    not applied.get("ok")
                    or applied.get("operation") != "removeDataTableRow"
                    or applied.get("valueKind") != "data-table-row-remove"
                    or applied.get("changed") is not True
                    or result.get("valueKind") != "data-table-row-remove"
                    or result.get("rowName") != ROW_BETA
                    or result.get("dataTableKind") != "row-remove"
                    or result.get("changed") is not True
                    or result.get("transactionRecorded") is not True
                    or result.get("beforeValue") != BETA_INITIAL
                    or result.get("afterValue") is not None
                ):
                    raise RuntimeError(f"DataTable remove LiveApply result is incomplete: {applied}")
                success_cases.append({"operation": "removeDataTableRow", "rowName": ROW_BETA})

                inspected = await call(
                    session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["cell"]}
                )
                memory = inspected.get("result", {}).get("memory", {})
                if (
                    not inspected.get("ok")
                    or memory.get("loaded") is not True
                    or memory.get("packageDirty") is not True
                    or memory.get("openInAssetEditor") is not True
                    or memory.get("loadedByBridge") is not False
                ):
                    raise RuntimeError(f"Live memory state did not become Dirty: {inspected}")

                # Bridge rejection: the target package is already Dirty.
                dirty_plan = await plan_data_table(
                    session,
                    FIXTURE_ASSETS["cell"],
                    "setDataTableCell",
                    {"rowName": ROW_ALPHA, "fieldName": "Label"},
                    "Alpha",
                )
                if not dirty_plan.get("ok"):
                    raise RuntimeError(f"Dirty-case Plan unexpectedly rejected before the Bridge: {dirty_plan}")
                dirty_plan_id = str(dirty_plan["planId"])
                rejected = await apply_live(session, dirty_plan_id, f"LIVE APPLY {dirty_plan_id}")
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-package-dirty":
                    raise RuntimeError(
                        f"Expected live-editor-write-package-dirty but got: {rejected}"
                    )
                bridge_rejections += 1
                inspected = await call(
                    session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["cell"]}
                )
                memory = inspected.get("result", {}).get("memory", {})
                if memory.get("packageDirty") is not True:
                    raise RuntimeError(f"Dirty-package rejection cleared the package flag: {inspected}")
                if sha256(package_files[FIXTURE_IDS["cell"]]) != package_hashes_before[FIXTURE_IDS["cell"]]:
                    raise RuntimeError("Dirty-package rejection changed the fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the frozen Revision Export")

                if any(sha256(path) != package_hashes_before[name] for name, path in package_files.items()):
                    raise RuntimeError("DataTable LiveApply changed a fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("DataTable LiveApply modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("DataTable LiveApply modified the frozen Revision Export")

    return {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(EXPECTED_TOOLS),
        "successCases": len(success_cases),
        "planRejections": plan_rejections,
        "bridgeRejections": bridge_rejections,
        "noopVerified": True,
        "diskPackageHashesUnchanged": True,
        "databaseHashUnchanged": True,
        "revisionExportHashUnchanged": True,
    }


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
    parser.add_argument("--session-marker", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

UNDO_ROOT = "/Game/UEAgentKitWriteTests/UndoDiscard"
SCALAR_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
REFERENCE_CLASS = "/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset"
STRUCTURED_CLASS = "/Script/UEAgentKitEditor.UEAgentKitStructuredWriteFixtureAsset"
MATERIAL_CLASS = "/Script/Engine.MaterialInstanceConstant"
DATA_TABLE_CLASS = "/Script/Engine.DataTable"
ROW_STRUCT = "/Script/UEAgentKitEditor.UEAgentKitDataTableFixtureRow"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

FIXTURE_ASSETS = {
    "scalar": f"{UNDO_ROOT}/DA_Scalar.DA_Scalar",
    "reference": f"{UNDO_ROOT}/DA_Reference.DA_Reference",
    "structured": f"{UNDO_ROOT}/DA_Structured.DA_Structured",
    "material": f"{UNDO_ROOT}/MI_Scalar.MI_Scalar",
    "datatable": f"{UNDO_ROOT}/DT_Target.DT_Target",
}

TEXTURE_PATH = f"{UNDO_ROOT}/T_Target.T_Target"
SCALAR_INITIAL = {"Count": 1, "Label": "Alpha", "bEnabled": True}
ROW_ALPHA = "RowAlpha"

# The scalar fixture is the designated "saved after write" asset: the smoke test
# saves it through the authorized save flow to prove the package-saved rejection,
# so its disk package is expected to change (and is excluded from unchanged hashes).
SAVED_FIXTURE_ID = "undo-reference-asset"


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


async def plan_write(
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
            "description": "Real UE5.6 Live Editor Undo/Discard regression",
        },
    )


async def apply_live(session: ClientSession, plan_id: str, confirmation: str) -> dict[str, Any]:
    return await call(
        session,
        "ue_apply_asset_property_live",
        {"plan_id": plan_id, "confirmation": confirmation},
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


async def discard_live(
    session: ClientSession,
    asset_path: str,
    transaction_id: str,
    editor_session_id: str,
) -> dict[str, Any]:
    return await call(
        session,
        "ue_discard_asset_property_live",
        {
            "asset_path": asset_path,
            "transaction_id": transaction_id,
            "editor_session_id": editor_session_id,
        },
    )


async def apply_and_capture(
    session: ClientSession,
    asset_path: str,
    operation: str,
    target: dict[str, Any],
    value: Any,
) -> dict[str, Any]:
    plan = await plan_write(session, asset_path, operation, target, value)
    if not plan.get("ok"):
        raise RuntimeError(f"Plan failed before Undo/Discard test: {plan}")
    plan_id = str(plan["planId"])
    applied = await apply_live(session, plan_id, f"LIVE APPLY {plan_id}")
    if not applied.get("ok") or not applied.get("changed"):
        raise RuntimeError(f"LiveApply failed before Undo/Discard test: {applied}")
    result = applied.get("result", {})
    transaction_id = result.get("transactionId")
    editor_session_id = result.get("editorSessionId")
    if not isinstance(transaction_id, str) or not isinstance(editor_session_id, str):
        raise RuntimeError(f"LiveApply did not return transactionId/editorSessionId: {applied}")
    return {
        "applied": applied,
        "result": result,
        "transactionId": transaction_id,
        "editorSessionId": editor_session_id,
    }


async def assert_no_dirty_packages(session: ClientSession, label: str) -> None:
    dirty = await call(session, "ue_get_dirty_assets", {})
    dirty_items = dirty.get("result", {}).get("items", [])
    dirty_paths: set[str] = set()
    for item in dirty_items:
        if isinstance(item, dict) and isinstance(item.get("assetPaths"), list):
            dirty_paths.update(str(path) for path in item["assetPaths"])
    if dirty_paths.intersection(FIXTURE_ASSETS.values()):
        raise RuntimeError(f"{label} left a fixture package Dirty: {dirty}")


async def assert_memory_clean(session: ClientSession, asset_path: str, label: str) -> None:
    inspected = await call(session, "ue_inspect_asset_live", {"asset_path": asset_path})
    memory = inspected.get("result", {}).get("memory", {})
    if (
        not inspected.get("ok")
        or memory.get("loaded") is not True
        or memory.get("packageDirty") is not False
        or memory.get("openInAssetEditor") is not True
    ):
        raise RuntimeError(f"{label} left the asset in an unexpected memory state: {inspected}")


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
    bridge_rejections: list[str] = []
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

                # Rejections first, so records exist but must be refused.

                # 1. Wrong Editor session: the write is valid but the session id is stale.
                scalar_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    "setAssetProperty",
                    {"propertyPath": "IntValue"},
                    5,
                )
                rejected = await undo_live(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    scalar_write["transactionId"],
                    "stale-session-id",
                )
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-undo-session-mismatch":
                    raise RuntimeError(f"Expected session-mismatch but got: {rejected}")
                bridge_rejections.append("session-mismatch")

                # 2. Wrong transaction id: the record exists but the id does not match.
                rejected = await undo_live(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    "11111111-2222-3333-4444-555555555555",
                    scalar_write["editorSessionId"],
                )
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-undo-transaction-mismatch":
                    raise RuntimeError(f"Expected transaction-mismatch but got: {rejected}")
                bridge_rejections.append("transaction-mismatch")

                # 3. Wrong asset: no confirmed write is pending for this path.
                rejected = await undo_live(
                    session,
                    f"{UNDO_ROOT}/DA_Nonexistent.DA_Nonexistent",
                    scalar_write["transactionId"],
                    scalar_write["editorSessionId"],
                )
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-undo-not-found":
                    raise RuntimeError(f"Expected not-found but got: {rejected}")
                bridge_rejections.append("not-found")

                # Success cases: representative writes across all value kinds.
                # The reference success runs before the saved-package rejection so
                # the saved fixture stays plan-fresh for the authorized save.

                # Clean up the first scalar write so the package is clean again;
                # this also proves the earlier rejections did not consume the record.
                cleaned = await undo_live(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    scalar_write["transactionId"],
                    scalar_write["editorSessionId"],
                )
                if not cleaned.get("ok") or cleaned.get("mode") != "LiveUndo":
                    raise RuntimeError(f"Cleanup Undo after rejections failed: {cleaned}")

                # 4. Stack mismatch: another confirmed write sits on top, so undoing
                #    the older transaction must be refused without touching either.
                structured_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["structured"],
                    "setAssetStructuredProperty",
                    {"propertyPath": "StructValue"},
                    {"valueType": "Struct", "fields": {"Count": 42, "Label": "Undo", "bEnabled": True}},
                )
                scalar_second = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    "setAssetProperty",
                    {"propertyPath": "IntValue"},
                    -7,
                )
                rejected = await undo_live(
                    session,
                    FIXTURE_ASSETS["structured"],
                    structured_write["transactionId"],
                    structured_write["editorSessionId"],
                )
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-undo-stack-mismatch":
                    raise RuntimeError(f"Expected stack-mismatch but got: {rejected}")
                bridge_rejections.append("stack-mismatch")
                # Undo the top write first, then the older one; both must succeed.
                reverted_top = await undo_live(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    scalar_second["transactionId"],
                    scalar_second["editorSessionId"],
                )
                if not reverted_top.get("ok") or reverted_top.get("mode") != "LiveUndo":
                    raise RuntimeError(f"Top-of-stack Undo failed: {reverted_top}")
                reverted_older = await undo_live(
                    session,
                    FIXTURE_ASSETS["structured"],
                    structured_write["transactionId"],
                    structured_write["editorSessionId"],
                )
                if not reverted_older.get("ok") or reverted_older.get("mode") != "LiveUndo":
                    raise RuntimeError(f"Older Undo failed after top undo: {reverted_older}")
                success_cases.append({"operation": "stack-ordered-undo", "revert": "undo"})

                # 5. Saved package: saving the dirty package must make Undo impossible.
                reference_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["reference"],
                    "setAssetReferenceProperty",
                    {"propertyPath": "ObjectValue"},
                    {"referenceType": "Object", "path": TEXTURE_PATH},
                )
                preview = await call(
                    session,
                    "ue_save_authorized_asset",
                    {"asset_path": FIXTURE_ASSETS["reference"], "mode": "Preview"},
                )
                if not preview.get("ok") or not preview.get("saveReceipt"):
                    raise RuntimeError(f"Save Preview failed: {preview}")
                saved = await call(
                    session,
                    "ue_save_authorized_asset",
                    {
                        "asset_path": FIXTURE_ASSETS["reference"],
                        "mode": "Commit",
                        "save_receipt": preview["saveReceipt"],
                        "confirmation": f"SAVE {preview['saveReceipt']}",
                    },
                )
                if not saved.get("ok") or saved.get("saved") is not True:
                    raise RuntimeError(f"Save Commit failed: {saved}")
                rejected = await undo_live(
                    session,
                    FIXTURE_ASSETS["reference"],
                    reference_write["transactionId"],
                    reference_write["editorSessionId"],
                )
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-undo-package-saved":
                    raise RuntimeError(f"Expected package-saved but got: {rejected}")
                bridge_rejections.append("package-saved")

                # Success cases: representative writes across all value kinds.

                scalar_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    "setAssetProperty",
                    {"propertyPath": "IntValue"},
                    9,
                )
                undone = await undo_live(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    scalar_write["transactionId"],
                    scalar_write["editorSessionId"],
                )
                undone_result = undone.get("result", {})
                if (
                    not undone.get("ok")
                    or undone.get("mode") != "LiveUndo"
                    or undone.get("operation") != "setAssetProperty"
                    or undone.get("valueKind") != "scalar"
                    or undone.get("changed") is not True
                    or undone_result.get("action") != "undo-asset-property-live"
                    or undone_result.get("operation") != "setAssetProperty"
                    or undone_result.get("transactionId") != scalar_write["transactionId"]
                    or undone_result.get("beforeValue") != scalar_write["result"].get("afterValue")
                    or undone_result.get("afterValue") != scalar_write["result"].get("beforeValue")
                    or undone_result.get("transactionRecorded") is not False
                    or undone_result.get("dirtyBefore") is not True
                    or undone_result.get("dirtyAfter") is not False
                    or undone_result.get("packageDirtyAfter") is not False
                    or undone_result.get("saved") is not False
                ):
                    raise RuntimeError(f"Scalar Undo contract is broken: {undone}")
                await assert_memory_clean(session, FIXTURE_ASSETS["scalar"], "Scalar Undo")
                double_undo = await undo_live(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    scalar_write["transactionId"],
                    scalar_write["editorSessionId"],
                )
                if double_undo.get("ok") or error_code(double_undo) != "live-editor-write-undo-not-found":
                    raise RuntimeError(f"Expected double-undo not-found but got: {double_undo}")
                bridge_rejections.append("double-undo")
                success_cases.append({"operation": "setAssetProperty", "revert": "undo"})

                structured_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["structured"],
                    "setAssetStructuredProperty",
                    {"propertyPath": "StructValue"},
                    {"valueType": "Struct", "fields": {"Count": 42, "Label": "Undo", "bEnabled": True}},
                )
                undone = await undo_live(
                    session,
                    FIXTURE_ASSETS["structured"],
                    structured_write["transactionId"],
                    structured_write["editorSessionId"],
                )
                undone_result = undone.get("result", {})
                if (
                    not undone.get("ok")
                    or undone.get("operation") != "setAssetStructuredProperty"
                    or undone_result.get("action") != "undo-asset-property-live"
                    or undone_result.get("beforeValue") != structured_write["result"].get("afterValue")
                    or undone_result.get("afterValue") != structured_write["result"].get("beforeValue")
                    or undone_result.get("dirtyAfter") is not False
                ):
                    raise RuntimeError(f"Structured Undo contract is broken: {undone}")
                await assert_memory_clean(session, FIXTURE_ASSETS["structured"], "Structured Undo")
                success_cases.append({"operation": "setAssetStructuredProperty", "revert": "undo"})

                material_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["material"],
                    "setMaterialInstanceScalarParameter",
                    {"parameterName": "EmissiveIntensity"},
                    0.5,
                )
                discarded = await discard_live(
                    session,
                    FIXTURE_ASSETS["material"],
                    material_write["transactionId"],
                    material_write["editorSessionId"],
                )
                discarded_result = discarded.get("result", {})
                if (
                    not discarded.get("ok")
                    or discarded.get("mode") != "LiveDiscard"
                    or discarded.get("operation") != "setMaterialInstanceScalarParameter"
                    or discarded.get("valueKind") != "material-scalar"
                    or discarded_result.get("action") != "discard-asset-property-live"
                    or discarded_result.get("beforeValue") != material_write["result"].get("afterValue")
                    or discarded_result.get("afterValue") != material_write["result"].get("beforeValue")
                    or discarded_result.get("transactionRecorded") is not False
                    or discarded_result.get("dirtyAfter") is not False
                    or discarded_result.get("saved") is not False
                ):
                    raise RuntimeError(f"Material Discard contract is broken: {discarded}")
                await assert_memory_clean(session, FIXTURE_ASSETS["material"], "Material Discard")
                success_cases.append({"operation": "setMaterialInstanceScalarParameter", "revert": "discard"})

                datatable_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["datatable"],
                    "setDataTableCell",
                    {"rowName": ROW_ALPHA, "fieldName": "Count"},
                    42,
                )
                discarded = await discard_live(
                    session,
                    FIXTURE_ASSETS["datatable"],
                    datatable_write["transactionId"],
                    datatable_write["editorSessionId"],
                )
                discarded_result = discarded.get("result", {})
                if (
                    not discarded.get("ok")
                    or discarded.get("mode") != "LiveDiscard"
                    or discarded.get("operation") != "setDataTableCell"
                    or discarded.get("valueKind") != "data-table-cell"
                    or discarded_result.get("action") != "discard-asset-property-live"
                    or discarded_result.get("operation") != "setDataTableCell"
                    or discarded_result.get("beforeValue") != datatable_write["result"].get("afterValue")
                    or discarded_result.get("afterValue") != datatable_write["result"].get("beforeValue")
                    or discarded_result.get("dirtyAfter") is not False
                    or discarded_result.get("saved") is not False
                ):
                    raise RuntimeError(f"DataTable Discard contract is broken: {discarded}")
                await assert_memory_clean(session, FIXTURE_ASSETS["datatable"], "DataTable Discard")
                success_cases.append({"operation": "setDataTableCell", "revert": "discard"})

                await assert_no_dirty_packages(session, "Final")

    unchanged = True
    for name, path in package_files.items():
        if name == SAVED_FIXTURE_ID:
            continue
        if sha256(path) != package_hashes_before[name]:
            unchanged = False
    if not unchanged:
        raise RuntimeError("Undo/Discard regression changed a fixture package on disk")
    saved_changed = sha256(package_files[SAVED_FIXTURE_ID]) != package_hashes_before[SAVED_FIXTURE_ID]
    if not saved_changed:
        raise RuntimeError("The saved-package rejection fixture was not actually saved")
    if sha256(args.database) != database_hash_before:
        raise RuntimeError("Undo/Discard regression modified the immutable SQLite index")
    if directory_sha256(args.revision_export) != revision_export_hash_before:
        raise RuntimeError("Undo/Discard regression modified the frozen Revision Export")

    return {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(tool_names),
        "successCases": len(success_cases),
        "revertKinds": [case["revert"] for case in success_cases],
        "bridgeRejections": bridge_rejections,
        "diskPackageHashesUnchanged": unchanged,
        "savedFixtureDiskHashChanged": saved_changed,
        "databaseHashUnchanged": True,
        "revisionExportHashUnchanged": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--fixture-report", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--session-marker", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

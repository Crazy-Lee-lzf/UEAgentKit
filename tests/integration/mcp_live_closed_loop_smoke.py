from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))
from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402

CLOSED_LOOP_ROOT = "/Game/UEAgentKitWriteTests/ClosedLoop"
SCALAR_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
MATERIAL_CLASS = "/Script/Engine.MaterialInstanceConstant"
DATA_TABLE_CLASS = "/Script/Engine.DataTable"
ROW_STRUCT = "/Script/UEAgentKitEditor.UEAgentKitDataTableFixtureRow"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

FIXTURE_ASSETS = {
    "scalar": f"{CLOSED_LOOP_ROOT}/DA_Scalar.DA_Scalar",
    "material": f"{CLOSED_LOOP_ROOT}/MI_Scalar.MI_Scalar",
    "datatable": f"{CLOSED_LOOP_ROOT}/DT_Target.DT_Target",
    "datatable_rename": f"{CLOSED_LOOP_ROOT}/DT_RenameTarget.DT_RenameTarget",
}

SAVED_FIXTURE_IDS = {
    "closedloop-scalar-asset",
    "closedloop-material-asset",
    "closedloop-datatable-asset",
    "closedloop-datatable-rename-asset",
}

ROW_ALPHA = "RowAlpha"
ROW_RENAMED = "RowRenamed"
ROW_NEW = {"Count": 42, "Label": "Alpha", "bEnabled": True}
ROW_RENAME_VALUE = {"Count": 1, "Label": "Alpha", "bEnabled": True}


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


async def create_change_set(session: ClientSession, name: str) -> str:
    created = await call(
        session,
        "ue_create_change_set",
        {
            "title": f"Real UE5.6 {name} Semantic Diff closed loop",
            "task_id": f"task_r2-{name}-semantic-diff",
        },
    )
    change_set_id = str(created.get("changeSetId", ""))
    if not created.get("ok") or created.get("status") != "planned" or not change_set_id:
        raise RuntimeError(f"Change Set creation failed for {name}: {created}")
    return change_set_id


async def capture_semantic_diff(
    session: ClientSession,
    change_set_id: str,
    asset_path: str,
    stage: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    result = await call(
        session,
        "ue_analyze_semantic_diff",
        {
            "change_set_id": change_set_id,
            "stage": stage,
            "asset_paths": [asset_path],
            "include_unchanged": True,
            "max_changes": 64,
            "max_output_tokens": 4096,
        },
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    summary = result.get("summary", {})
    assets = result.get("assets", [])
    budget = result.get("outputBudget", {})
    if (
        not result.get("ok")
        or result.get("readOnly") is not True
        or result.get("evidenceStage", {}).get("selected") != stage
        or len(assets) != 1
        or assets[0].get("assetPath") != asset_path
        or int(summary.get("expectedCount", 0)) < 1
        or summary.get("actualCount") != summary.get("expectedCount")
        or summary.get("matchedCount") != summary.get("expectedCount")
        or summary.get("unexpectedCount") != 0
        or summary.get("missingExpectedCount") != 0
        or int(budget.get("estimatedTokens", 0)) <= 0
    ):
        raise RuntimeError(
            f"Semantic Diff {stage} contract failed for {asset_path}: {result}"
        )
    asset = assets[0]
    return {
        "stage": stage,
        "elapsedMs": elapsed_ms,
        "beforeRevision": asset.get("beforeRevision", ""),
        "afterRevision": asset.get("afterRevision", ""),
        "stageEvidenceRevision": asset.get("stageEvidenceRevision", ""),
        "expectedCount": summary.get("expectedCount", 0),
        "actualCount": summary.get("actualCount", 0),
        "matchedCount": summary.get("matchedCount", 0),
        "unexpectedCount": summary.get("unexpectedCount", 0),
        "missingExpectedCount": summary.get("missingExpectedCount", 0),
        "unchangedCriticalCount": summary.get("unchangedCriticalCount", 0),
        "analysisGapCount": summary.get("analysisGapCount", 0),
        "estimatedTokens": budget.get("estimatedTokens", 0),
        "truncated": budget.get("truncated", False),
    }


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
            "description": "Real UE5.6 Live Editor closed loop regression",
        },
    )


async def apply_live(
    session: ClientSession,
    plan_id: str,
    confirmation: str,
    change_set_id: str = "",
) -> dict[str, Any]:
    params = {"plan_id": plan_id, "confirmation": confirmation}
    if change_set_id:
        params["change_set_id"] = change_set_id
    return await call(session, "ue_apply_asset_property_live", params)


async def verify_live(session: ClientSession, asset_path: str, change_set_id: str = "") -> dict[str, Any]:
    params = {"asset_path": asset_path}
    if change_set_id:
        params["change_set_id"] = change_set_id
    return await call(session, "ue_verify_live_write", params)


async def save_authorized(
    session: ClientSession,
    asset_path: str,
    change_set_id: str = "",
) -> dict[str, Any]:
    preview_params = {"asset_path": asset_path, "mode": "Preview"}
    if change_set_id:
        preview_params["change_set_id"] = change_set_id
    preview = await call(session, "ue_save_authorized_asset", preview_params)
    if not preview.get("ok") or not preview.get("saveReceipt"):
        raise RuntimeError(f"Save Preview failed for {asset_path}: {preview}")
    commit_params = {
        "asset_path": asset_path,
        "mode": "Commit",
        "save_receipt": preview["saveReceipt"],
        "confirmation": f"SAVE {preview['saveReceipt']}",
    }
    if change_set_id:
        commit_params["change_set_id"] = change_set_id
    saved = await call(session, "ue_save_authorized_asset", commit_params)
    if not saved.get("ok") or saved.get("saved") is not True:
        raise RuntimeError(f"Save Commit failed for {asset_path}: {saved}")
    return saved


async def apply_and_capture(
    session: ClientSession,
    asset_path: str,
    operation: str,
    target: dict[str, Any],
    value: Any,
    change_set_id: str = "",
) -> dict[str, Any]:
    plan = await plan_write(session, asset_path, operation, target, value)
    if not plan.get("ok"):
        raise RuntimeError(f"Plan failed for {asset_path}: {plan}")
    plan_id = str(plan["planId"])
    applied = await apply_live(session, plan_id, f"LIVE APPLY {plan_id}", change_set_id)
    if not applied.get("ok") or not applied.get("changed"):
        raise RuntimeError(f"LiveApply failed for {asset_path}: {applied}")
    return applied


async def run(args: argparse.Namespace) -> dict[str, Any]:
    fixture_report = json.loads(args.fixture_report.read_text(encoding="utf-8"))
    package_files: dict[str, Path] = {}
    for fixture in fixture_report["fixtures"]:
        package_files[str(fixture["id"])] = Path(str(fixture["packageFilename"]))
    package_hashes_before = {name: sha256(path) for name, path in package_files.items()}
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
    closed_loops: list[dict[str, Any]] = []
    semantic_diffs: list[dict[str, Any]] = []
    rejections: list[str] = []
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

                editor_context = await call(session, "ue_get_editor_context", {})
                context_result = editor_context.get("result", {})
                if (
                    not editor_context.get("ok")
                    or context_result.get("editor", {}).get("sessionId") != status["result"].get("sessionId")
                    or "selection" not in context_result
                    or "outputLogCursor" not in context_result
                ):
                    raise RuntimeError(f"Editor Context contract is broken: {editor_context}")

                batch_started = await call(
                    session,
                    "ue_start_batch_task",
                    {
                        "operation": "scanCurrentWorld",
                        "max_actors": 500,
                        "max_components_per_actor": 25,
                        "timeout_seconds": 30,
                    },
                )
                batch_task_id = str(batch_started.get("result", {}).get("taskId", ""))
                if not batch_started.get("ok") or not batch_task_id:
                    raise RuntimeError(f"Batch Task did not start: {batch_started}")
                batch_status: dict[str, Any] = {}
                for _ in range(200):
                    batch_status = await call(session, "ue_get_batch_task", {"task_id": batch_task_id})
                    if batch_status.get("result", {}).get("state") != "running":
                        break
                    await asyncio.sleep(0.05)
                batch_result = batch_status.get("result", {})
                if batch_result.get("state") != "completed" or "details" in batch_result:
                    raise RuntimeError(f"Batch Task summary contract is broken: {batch_status}")
                batch_page = await call(
                    session,
                    "ue_get_batch_task",
                    {
                        "task_id": batch_task_id,
                        "include_details": True,
                        "detail_offset": 0,
                        "detail_limit": 2,
                    },
                )
                page_result = batch_page.get("result", {})
                page_items = page_result.get("details", {}).get("items", [])
                if (
                    not batch_page.get("ok")
                    or len(page_items) > 2
                    or len(json.dumps(batch_page, ensure_ascii=False).encode("utf-8")) >= 1024 * 1024
                ):
                    raise RuntimeError(f"Batch Task paging contract is broken: {batch_page}")

                for asset_path in FIXTURE_ASSETS.values():
                    opened = await call(session, "ue_open_asset", {"asset_path": asset_path})
                    if not opened.get("ok") or not opened["result"].get("openAfter"):
                        raise RuntimeError(f"The fixture {asset_path} was not opened: {opened}")

                # 1. Data Asset scalar: Change Set -> Apply -> Verify(not-saved) -> Save -> Verify(verified).
                change_set_id = await create_change_set(session, "data-asset")
                scalar_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    "setAssetProperty",
                    {"propertyPath": "IntValue"},
                    7,
                    change_set_id,
                )
                scalar_result = scalar_write.get("result", {})
                scalar_expected = scalar_result.get("afterValue")
                scalar_plan_id = scalar_write["planId"]
                semantic_diffs.append(
                    {
                        "domain": "data-asset",
                        **await capture_semantic_diff(
                            session,
                            change_set_id,
                            FIXTURE_ASSETS["scalar"],
                            "live",
                        ),
                    }
                )
                not_saved = await verify_live(session, FIXTURE_ASSETS["scalar"], change_set_id)
                if (
                    not not_saved.get("ok")
                    or not_saved.get("mode") != "LiveVerify"
                    or not_saved.get("state") != "not-saved"
                    or not_saved.get("undoAvailable") is not True
                    or not_saved.get("saved") is not False
                    or not_saved.get("verified") is not False
                    or not_saved.get("liveApplyReceipt") != scalar_write["liveApplyReceipt"]
                    or not_saved.get("planId") != scalar_plan_id
                    or not_saved.get("memoryRecorded") is not False
                    or not_saved["memoryTaskEvidence"]["arguments"]["outcome"] != "cancelled"
                    or not_saved["memoryTaskEvidence"]["arguments"]["validation_evidence_details"]["independentReload"] is not False
                ):
                    raise RuntimeError(f"not-saved verification contract is broken: {not_saved}")
                applied_set = await call(session, "ue_get_change_set", {"change_set_id": change_set_id})
                if applied_set.get("status") != "applied" or applied_set.get("operationCount") != 1:
                    raise RuntimeError(f"Applied Change Set contract is broken: {applied_set}")
                saved = await save_authorized(session, FIXTURE_ASSETS["scalar"], change_set_id)
                if saved.get("liveWriteSaved") is not True or not saved.get("liveApplyReceipt"):
                    raise RuntimeError(f"Save did not link the live write record: {saved}")
                saved_set = await call(session, "ue_get_change_set", {"change_set_id": change_set_id})
                if saved_set.get("status") != "saved" or saved_set.get("saveState", {}).get("state") != "saved":
                    raise RuntimeError(f"Saved Change Set contract is broken: {saved_set}")
                semantic_diffs.append(
                    {
                        "domain": "data-asset",
                        **await capture_semantic_diff(
                            session,
                            change_set_id,
                            FIXTURE_ASSETS["scalar"],
                            "persisted",
                        ),
                    }
                )
                # After the authorized save, Undo/Discard must refuse the transaction.
                rejected = await call(
                    session,
                    "ue_undo_asset_property_live",
                    {
                        "asset_path": FIXTURE_ASSETS["scalar"],
                        "transaction_id": scalar_result.get("transactionId"),
                        "editor_session_id": scalar_result.get("editorSessionId"),
                        "change_set_id": change_set_id,
                    },
                )
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-undo-package-saved":
                    raise RuntimeError(f"Expected package-saved Undo rejection but got: {rejected}")
                rejections.append("undo-after-save")
                verified = await verify_live(session, FIXTURE_ASSETS["scalar"], change_set_id)
                if (
                    not verified.get("ok")
                    or verified.get("mode") != "LiveVerify"
                    or verified.get("state") != "verified"
                    or verified.get("undoAvailable") is not False
                    or verified.get("saved") is not True
                    or verified.get("verified") is not True
                    or verified.get("expectedValue") != scalar_expected
                    or verified.get("exportedValue") != scalar_expected
                    or verified.get("planId") != scalar_plan_id
                    or verified.get("memoryRecorded") is not False
                ):
                    raise RuntimeError(f"Data Asset verified loop contract is broken: {verified}")
                verified_set = await call(session, "ue_get_change_set", {"change_set_id": change_set_id})
                if (
                    verified_set.get("status") != "verified"
                    or verified_set.get("validation", {}).get("state") != "verified"
                    or verified_set.get("operations", [{}])[0].get("status") != "verified"
                ):
                    raise RuntimeError(f"Verified Change Set contract is broken: {verified_set}")
                semantic_diffs.append(
                    {
                        "domain": "data-asset",
                        **await capture_semantic_diff(
                            session,
                            change_set_id,
                            FIXTURE_ASSETS["scalar"],
                            "verified",
                        ),
                    }
                )
                evidence = verified["memoryTaskEvidence"]["arguments"]
                if (
                    evidence["task_key"] != f"live-write:{scalar_plan_id}"
                    or evidence["outcome"] != "succeeded"
                    or evidence["revision_set"][0]["assetPath"] != FIXTURE_ASSETS["scalar"]
                    or evidence["revision_set"][0]["revision"] != verified.get("actualRevision")
                    or evidence["backup_manifest_ref"]
                    != f"backup-manifest:live-save:{saved['saveReceipt']}"
                    or evidence["patch_details"]["undoAvailable"] is not False
                    or evidence["patch_details"]["saved"] is not True
                    or evidence["patch_details"]["verified"] is not True
                ):
                    raise RuntimeError(f"Data Asset memory Task Record evidence is broken: {evidence}")
                closed_loops.append({"asset": "scalar", "state": "verified", "revision": verified.get("actualRevision")})

                # 2. Material Instance: Apply -> Save -> Verify (exported parameter value).
                material_change_set_id = await create_change_set(session, "material-instance")
                material_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["material"],
                    "setMaterialInstanceScalarParameter",
                    {"parameterName": "EmissiveIntensity"},
                    0.5,
                    material_change_set_id,
                )
                material_expected = material_write.get("result", {}).get("afterValue")
                semantic_diffs.append(
                    {
                        "domain": "material-instance",
                        **await capture_semantic_diff(
                            session,
                            material_change_set_id,
                            FIXTURE_ASSETS["material"],
                            "live",
                        ),
                    }
                )
                await save_authorized(
                    session, FIXTURE_ASSETS["material"], material_change_set_id
                )
                semantic_diffs.append(
                    {
                        "domain": "material-instance",
                        **await capture_semantic_diff(
                            session,
                            material_change_set_id,
                            FIXTURE_ASSETS["material"],
                            "persisted",
                        ),
                    }
                )
                verified = await verify_live(
                    session, FIXTURE_ASSETS["material"], material_change_set_id
                )
                if (
                    not verified.get("ok")
                    or verified.get("state") != "verified"
                    or verified.get("expectedValue") != material_expected
                    or verified.get("exportedValue") != material_expected
                ):
                    raise RuntimeError(f"Material verified loop contract is broken: {verified}")
                semantic_diffs.append(
                    {
                        "domain": "material-instance",
                        **await capture_semantic_diff(
                            session,
                            material_change_set_id,
                            FIXTURE_ASSETS["material"],
                            "verified",
                        ),
                    }
                )
                closed_loops.append({"asset": "material", "state": "verified", "revision": verified.get("actualRevision")})

                # 3. DataTable: Apply -> Save -> Verify (exported row value).
                datatable_change_set_id = await create_change_set(session, "data-table-cell")
                datatable_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["datatable"],
                    "setDataTableCell",
                    {"rowName": ROW_ALPHA, "fieldName": "Count"},
                    42,
                    datatable_change_set_id,
                )
                datatable_expected = datatable_write.get("result", {}).get("afterValue")
                semantic_diffs.append(
                    {
                        "domain": "data-table",
                        **await capture_semantic_diff(
                            session,
                            datatable_change_set_id,
                            FIXTURE_ASSETS["datatable"],
                            "live",
                        ),
                    }
                )
                await save_authorized(
                    session, FIXTURE_ASSETS["datatable"], datatable_change_set_id
                )
                semantic_diffs.append(
                    {
                        "domain": "data-table",
                        **await capture_semantic_diff(
                            session,
                            datatable_change_set_id,
                            FIXTURE_ASSETS["datatable"],
                            "persisted",
                        ),
                    }
                )
                verified = await verify_live(
                    session, FIXTURE_ASSETS["datatable"], datatable_change_set_id
                )
                if (
                    not verified.get("ok")
                    or verified.get("state") != "verified"
                    or verified.get("expectedValue") != datatable_expected
                    or verified.get("exportedValue") != datatable_expected
                ):
                    raise RuntimeError(f"DataTable verified loop contract is broken: {verified}")
                semantic_diffs.append(
                    {
                        "domain": "data-table",
                        **await capture_semantic_diff(
                            session,
                            datatable_change_set_id,
                            FIXTURE_ASSETS["datatable"],
                            "verified",
                        ),
                    }
                )
                closed_loops.append({"asset": "datatable", "state": "verified", "revision": verified.get("actualRevision")})

                # 4. DataTable rename: verification must read the destination row name.
                rename_change_set_id = await create_change_set(session, "data-table-rename")
                rename_write = await apply_and_capture(
                    session,
                    FIXTURE_ASSETS["datatable_rename"],
                    "renameDataTableRow",
                    {"rowName": ROW_ALPHA, "newRowName": ROW_RENAMED},
                    True,
                    rename_change_set_id,
                )
                rename_expected = rename_write.get("result", {}).get("afterValue")
                if rename_expected != ROW_RENAME_VALUE:
                    raise RuntimeError(f"DataTable rename LiveApply result is incomplete: {rename_write}")
                semantic_diffs.append(
                    {
                        "domain": "data-table-rename",
                        **await capture_semantic_diff(
                            session,
                            rename_change_set_id,
                            FIXTURE_ASSETS["datatable_rename"],
                            "live",
                        ),
                    }
                )
                await save_authorized(
                    session,
                    FIXTURE_ASSETS["datatable_rename"],
                    rename_change_set_id,
                )
                semantic_diffs.append(
                    {
                        "domain": "data-table-rename",
                        **await capture_semantic_diff(
                            session,
                            rename_change_set_id,
                            FIXTURE_ASSETS["datatable_rename"],
                            "persisted",
                        ),
                    }
                )
                verified = await verify_live(
                    session,
                    FIXTURE_ASSETS["datatable_rename"],
                    rename_change_set_id,
                )
                if (
                    not verified.get("ok")
                    or verified.get("state") != "verified"
                    or verified.get("expectedValue") != ROW_RENAME_VALUE
                    or verified.get("exportedValue") != ROW_RENAME_VALUE
                ):
                    raise RuntimeError(f"DataTable rename verified loop contract is broken: {verified}")
                semantic_diffs.append(
                    {
                        "domain": "data-table-rename",
                        **await capture_semantic_diff(
                            session,
                            rename_change_set_id,
                            FIXTURE_ASSETS["datatable_rename"],
                            "verified",
                        ),
                    }
                )
                closed_loops.append({"asset": "datatable-rename", "state": "verified", "revision": verified.get("actualRevision")})

                # 5. Rejection: verify without any confirmed live write.
                rejected = await verify_live(session, f"{CLOSED_LOOP_ROOT}/DA_Nonexistent.DA_Nonexistent")
                if rejected.get("ok") or error_code(rejected) != "live-write-verify-not-found":
                    raise RuntimeError(f"Expected verify-not-found but got: {rejected}")
                rejections.append("verify-not-found")

    changed_ids = {
        name for name, path in package_files.items() if sha256(path) != package_hashes_before[name]
    }
    if changed_ids != SAVED_FIXTURE_IDS:
        raise RuntimeError(f"Unexpected disk package changes: {sorted(changed_ids)}")
    if sha256(args.database) != database_hash_before:
        raise RuntimeError("Closed loop regression modified the immutable SQLite index")
    if directory_sha256(args.revision_export) != revision_export_hash_before:
        raise RuntimeError("Closed loop regression modified the frozen Revision Export")

    return {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(tool_names),
        "closedLoops": closed_loops,
        "semanticDiffs": semantic_diffs,
        "savedPackageDiskHashesChanged": sorted(changed_ids),
        "rejections": rejections,
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
    parser.add_argument("--summary-report", type=Path)
    args = parser.parse_args()
    try:
        summary = asyncio.run(run(args))
    except BaseException:
        print(traceback.format_exc(), flush=True)
        return 1
    else:
        if args.summary_report is not None:
            args.summary_report.parent.mkdir(parents=True, exist_ok=True)
            args.summary_report.write_text(
                json.dumps(summary, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())

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

REFERENCE_CLASS = "/Script/UEAgentKitEditor.UEAgentKitReferenceWriteFixtureAsset"
SCALAR_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
TEXTURE_PATH = "/Game/UEAgentKitWriteTests/References/T_Target.T_Target"
TEXTURE_CLASS = "/Script/Engine.Texture2D"
ACTOR_CLASS_PATH = "/Game/UEAgentKitWriteTests/References/BP_ReferenceTarget.BP_ReferenceTarget_C"
ACTOR_CLASS = "/Script/Engine.Actor"
NOT_ACTOR_CLASS_PATH = "/Game/UEAgentKitWriteTests/References/BP_NotActorTarget.BP_NotActorTarget_C"
MISSING_PATH = "/Game/UEAgentKitWriteTests/References/Missing.Missing"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

FIXTURE_IDS = {
    "object": "reference-object-asset",
    "class": "reference-class-asset",
    "soft_object": "reference-soft-object-asset",
    "soft_class": "reference-soft-class-asset",
    "null": "reference-null-asset",
    "non_reference": "reference-non-reference-asset",
}

FIXTURE_ASSETS = {
    "object": "/Game/UEAgentKitWriteTests/References/DA_Ref_Object.DA_Ref_Object",
    "class": "/Game/UEAgentKitWriteTests/References/DA_Ref_Class.DA_Ref_Class",
    "soft_object": "/Game/UEAgentKitWriteTests/References/DA_Ref_SoftObject.DA_Ref_SoftObject",
    "soft_class": "/Game/UEAgentKitWriteTests/References/DA_Ref_SoftClass.DA_Ref_SoftClass",
    "null": "/Game/UEAgentKitWriteTests/References/DA_Ref_Null.DA_Ref_Null",
    "non_reference": "/Game/UEAgentKitWriteTests/References/DA_ScalarNonReference.DA_ScalarNonReference",
}


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


async def plan_reference(
    session: ClientSession,
    asset_path: str,
    property_path: str,
    value: Any,
) -> dict[str, Any]:
    return await call(
        session,
        "ue_set_asset_reference_property",
        {
            "asset_path": asset_path,
            "property_path": property_path,
            "value": value,
            "mode": "Plan",
            "description": "Real UE5.6 Live Editor reference write regression",
        },
    )


async def apply_live(session: ClientSession, plan_id: str, confirmation: str) -> dict[str, Any]:
    return await call(
        session,
        "ue_apply_asset_property_live",
        {"plan_id": plan_id, "confirmation": confirmation},
    )


async def expect_bridge_apply_failure(
    session: ClientSession,
    asset_path: str,
    property_path: str,
    value: Any,
    expected_error: str,
) -> dict[str, Any]:
    plan = await plan_reference(session, asset_path, property_path, value)
    if not plan.get("ok"):
        raise RuntimeError(f"Failure-case Plan unexpectedly rejected before the Bridge: {plan}")
    plan_id = str(plan["planId"])
    rejected = await apply_live(session, plan_id, f"LIVE APPLY {plan_id}")
    if rejected.get("ok") or error_code(rejected) != expected_error:
        raise RuntimeError(
            f"Expected Bridge error {expected_error} for {property_path} but got: {rejected}"
        )
    return rejected


async def expect_plan_rejection(
    session: ClientSession,
    asset_path: str,
    property_path: str,
    value: Any,
    expected_issue: str,
) -> dict[str, Any]:
    rejected = await plan_reference(session, asset_path, property_path, value)
    if rejected.get("ok") or expected_issue not in error_issue_codes(rejected):
        raise RuntimeError(
            f"Expected Plan rejection issue {expected_issue} for {property_path} but got: {rejected}"
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

                # Failure: referenceType does not match the property type (rejected at Plan time).
                await expect_plan_rejection(
                    session,
                    FIXTURE_ASSETS["object"],
                    "ObjectValue",
                    {"referenceType": "SoftObject", "path": TEXTURE_PATH},
                    "asset-reference-type-mismatch",
                )

                # Failure: non-reference property used with setAssetReferenceProperty (Plan time).
                await expect_plan_rejection(
                    session,
                    FIXTURE_ASSETS["non_reference"],
                    "BoolValue",
                    {"referenceType": "Object", "path": TEXTURE_PATH},
                    "asset-reference-property-type",
                )

                # Failure: referenced class is not a child of the constraint class (Bridge time).
                await expect_bridge_apply_failure(
                    session,
                    FIXTURE_ASSETS["class"],
                    "ClassValue",
                    {"referenceType": "Class", "path": NOT_ACTOR_CLASS_PATH},
                    "live-editor-write-value-invalid",
                )
                await assert_clean_failure_invariants(
                    session,
                    FIXTURE_ASSETS["class"],
                    package_hashes_before,
                    database_hash_before,
                    revision_export_hash_before,
                    package_files,
                    args.database,
                    args.revision_export,
                )

                # Failure: reference path does not exist (Bridge time).
                await expect_bridge_apply_failure(
                    session,
                    FIXTURE_ASSETS["object"],
                    "ObjectValue",
                    {"referenceType": "Object", "path": MISSING_PATH},
                    "live-editor-write-value-invalid",
                )
                await assert_clean_failure_invariants(
                    session,
                    FIXTURE_ASSETS["object"],
                    package_hashes_before,
                    database_hash_before,
                    revision_export_hash_before,
                    package_files,
                    args.database,
                    args.revision_export,
                )

                success: list[tuple[str, str, Any, str, str, bool]] = [
                    (
                        "object",
                        "ObjectValue",
                        {"referenceType": "Object", "path": TEXTURE_PATH},
                        "Object",
                        TEXTURE_CLASS,
                        False,
                    ),
                    (
                        "class",
                        "ClassValue",
                        {"referenceType": "Class", "path": ACTOR_CLASS_PATH},
                        "Class",
                        ACTOR_CLASS_PATH,
                        False,
                    ),
                    (
                        "soft_object",
                        "SoftObjectValue",
                        {"referenceType": "SoftObject", "path": TEXTURE_PATH},
                        "SoftObject",
                        TEXTURE_CLASS,
                        False,
                    ),
                    (
                        "soft_class",
                        "SoftClassValue",
                        {"referenceType": "SoftClass", "path": ACTOR_CLASS_PATH},
                        "SoftClass",
                        ACTOR_CLASS_PATH,
                        False,
                    ),
                    (
                        "null",
                        "SoftObjectValue",
                        None,
                        "SoftObject",
                        "",
                        True,
                    ),
                ]
                for name, property_path, value, expected_type, resolved_class, is_null in success:
                    asset_path = FIXTURE_ASSETS[name]
                    plan = await plan_reference(session, asset_path, property_path, value)
                    if not plan.get("ok") or plan.get("underlyingOperation") != "setAssetReferenceProperty":
                        raise RuntimeError(f"Reference write Plan failed: {plan}")
                    plan_id = str(plan["planId"])
                    rejected = await apply_live(session, plan_id, "LIVE APPLY wrong")
                    if rejected.get("ok") or error_code(rejected) != "live-editor-write-confirmation-required":
                        raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {rejected}")
                    applied = await apply_live(session, plan_id, f"LIVE APPLY {plan_id}")
                    result = applied.get("result", {})
                    if (
                        not applied.get("ok")
                        or applied.get("mode") != "LiveApply"
                        or applied.get("operation") != "setAssetReferenceProperty"
                        or applied.get("valueKind") != "reference"
                        or applied.get("changed") is not True
                        or applied.get("saved") is not False
                        or applied.get("diskRevisionChanged") is not False
                        or applied.get("undoAvailableInEditor") is not True
                        or result.get("operation") != "setAssetReferenceProperty"
                        or result.get("valueKind") != "reference"
                        or result.get("referenceType") != expected_type
                        or result.get("changed") is not True
                        or result.get("transactionRecorded") is not True
                        or result.get("packageDirtyAfter") is not True
                        or result.get("dirtyAfter") is not True
                        or result.get("saved") is not False
                        or result.get("assetOpen") is not True
                        or result.get("loadedByBridge") is not False
                        or result.get("dirtyBefore") is not False
                        or result.get("packageDirtyBefore") is not False
                    ):
                        raise RuntimeError(f"Reference LiveApply result is incomplete: {applied}")
                    if is_null:
                        if result.get("beforeValue") != TEXTURE_PATH or result.get("afterValue") is not None:
                            raise RuntimeError(f"Null clear did not report the expected before/after values: {applied}")
                        if result.get("referencePath") is not None:
                            raise RuntimeError(f"Null clear did not report a null referencePath: {applied}")
                        if result.get("resolvedReferenceClass") != "":
                            raise RuntimeError(f"Null clear resolved class should be empty: {applied}")
                    else:
                        if result.get("beforeValue") is not None:
                            raise RuntimeError(f"Fixture reference was not initially empty: {applied}")
                        if result.get("afterValue") != value.get("path"):
                            raise RuntimeError(f"LiveApply did not report the new reference path: {applied}")
                        if result.get("referencePath") != value.get("path"):
                            raise RuntimeError(f"LiveApply referencePath mismatch: {applied}")
                        if result.get("resolvedReferenceClass") != resolved_class:
                            raise RuntimeError(
                                f"LiveApply resolved class {result.get('resolvedReferenceClass')} != {resolved_class}: {applied}"
                            )
                    inspected = await call(session, "ue_inspect_asset_live", {"asset_path": asset_path})
                    memory = inspected.get("result", {}).get("memory", {})
                    if (
                        not inspected.get("ok")
                        or memory.get("loaded") is not True
                        or memory.get("packageDirty") is not True
                        or memory.get("openInAssetEditor") is not True
                        or memory.get("loadedByBridge") is not False
                    ):
                        raise RuntimeError(f"Live memory state did not become Dirty: {inspected}")

                # Failure: target package is already Dirty (Bridge time).
                rejected = await expect_bridge_apply_failure(
                    session,
                    FIXTURE_ASSETS["object"],
                    "SoftObjectValue",
                    {"referenceType": "SoftObject", "path": TEXTURE_PATH},
                    "live-editor-write-package-dirty",
                )
                inspected = await call(
                    session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["object"]}
                )
                memory = inspected.get("result", {}).get("memory", {})
                if memory.get("packageDirty") is not True:
                    raise RuntimeError(f"Dirty-package rejection cleared the package flag: {inspected}")
                object_fixture_id = FIXTURE_IDS["object"]
                if sha256(package_files[object_fixture_id]) != package_hashes_before[object_fixture_id]:
                    raise RuntimeError("Dirty-package rejection changed the fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the frozen Revision Export")

                if any(sha256(path) != package_hashes_before[name] for name, path in package_files.items()):
                    raise RuntimeError("Reference LiveApply changed a fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Reference LiveApply modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("Reference LiveApply modified the frozen Revision Export")

    return {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(EXPECTED_TOOLS),
        "successCases": len(success),
        "planRejections": 2,
        "bridgeRejections": 3,
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

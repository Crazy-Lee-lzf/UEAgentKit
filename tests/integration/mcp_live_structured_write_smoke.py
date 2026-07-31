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

STRUCTURED_CLASS = "/Script/UEAgentKitEditor.UEAgentKitStructuredWriteFixtureAsset"
SCALAR_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
STRUCTURED_ROOT = "/Game/UEAgentKitWriteTests/Structured"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

FIXTURE_IDS = {
    "struct": "structured-struct-asset",
    "array": "structured-array-asset",
    "set": "structured-set-asset",
    "map": "structured-map-asset",
    "noop": "structured-noop-asset",
    "non_structured": "structured-scalar-non-structured",
}

FIXTURE_ASSETS = {
    "struct": f"{STRUCTURED_ROOT}/DA_Structured_Struct.DA_Structured_Struct",
    "array": f"{STRUCTURED_ROOT}/DA_Structured_Array.DA_Structured_Array",
    "set": f"{STRUCTURED_ROOT}/DA_Structured_Set.DA_Structured_Set",
    "map": f"{STRUCTURED_ROOT}/DA_Structured_Map.DA_Structured_Map",
    "noop": f"{STRUCTURED_ROOT}/DA_Structured_Noop.DA_Structured_Noop",
    "non_structured": f"{STRUCTURED_ROOT}/DA_ScalarNonStructured.DA_ScalarNonStructured",
}

STRUCT_VALUE = {
    "valueType": "Struct",
    "fields": {"Count": 42, "Label": "Live Write", "bEnabled": True},
}
INITIAL_STRUCT_VALUE = {
    "valueType": "Struct",
    "fields": {"Count": 1, "Label": "Initial", "bEnabled": True},
}
DEFAULT_STRUCT_VALUE = {
    "valueType": "Struct",
    "fields": {"Count": 0, "Label": "", "bEnabled": False},
}
INITIAL_ARRAY_VALUE = {"valueType": "Array", "items": [1, 2, 3]}
INITIAL_SET_VALUE = {"valueType": "Set", "items": ["Alpha", "Beta"]}
INITIAL_MAP_VALUE = {
    "valueType": "Map",
    "entries": [
        {
            "key": "Primary",
            "value": {
                "valueType": "Struct",
                "fields": {"Count": 10, "Label": "Primary", "bEnabled": True},
            },
        },
        {
            "key": "Secondary",
            "value": {
                "valueType": "Struct",
                "fields": {"Count": 20, "Label": "Secondary", "bEnabled": False},
            },
        },
    ],
}
NOOP_CASES = [
    ("StructValue", INITIAL_STRUCT_VALUE, "Struct"),
    ("ArrayValue", INITIAL_ARRAY_VALUE, "Array"),
    ("SetValue", INITIAL_SET_VALUE, "Set"),
    ("MapValue", INITIAL_MAP_VALUE, "Map"),
]
ARRAY_VALUE = {"valueType": "Array", "items": [4, 5, 6]}
SET_VALUE = {"valueType": "Set", "items": ["Alpha", "Beta", "Gamma"]}
MAP_VALUE = {
    "valueType": "Map",
    "entries": [
        {"key": "First", "value": STRUCT_VALUE},
        {"key": "Second", "value": DEFAULT_STRUCT_VALUE},
    ],
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


async def plan_structured(
    session: ClientSession,
    asset_path: str,
    property_path: str,
    value: Any,
) -> dict[str, Any]:
    return await call(
        session,
        "ue_set_asset_structured_property",
        {
            "asset_path": asset_path,
            "property_path": property_path,
            "value": value,
            "mode": "Plan",
            "description": "Real UE5.6 Live Editor structured write regression",
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
    property_path: str,
    value: Any,
    expected_issue: str,
) -> dict[str, Any]:
    rejected = await plan_structured(session, asset_path, property_path, value)
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
                plan_failures: list[tuple[str, str, Any, str]] = [
                    (
                        "struct",
                        "StructValue",
                        {"valueType": "Array", "items": []},
                        "operation-value-type",
                    ),
                    (
                        "struct",
                        "StructValue",
                        {"valueType": "Struct", "fields": {"Count": 1}},
                        "operation-value-type",
                    ),
                    (
                        "set",
                        "SetValue",
                        {"valueType": "Set", "items": ["Beta", "Alpha"]},
                        "operation-value-type",
                    ),
                    (
                        "map",
                        "MapValue",
                        {
                            "valueType": "Map",
                            "entries": [
                                {"key": "First", "value": DEFAULT_STRUCT_VALUE},
                                {"key": "First", "value": DEFAULT_STRUCT_VALUE},
                            ],
                        },
                        "operation-value-type",
                    ),
                    (
                        "non_structured",
                        "BoolValue",
                        {"valueType": "Struct", "fields": {}},
                        "asset-structured-property-type",
                    ),
                    (
                        "struct",
                        "FixedArrayValue",
                        {"valueType": "Array", "items": []},
                        "asset-structured-property-type",
                    ),
                    (
                        "struct",
                        "StructValue.Count",
                        STRUCT_VALUE,
                        "operation-target-value",
                    ),
                ]
                for name, property_path, value, expected_issue in plan_failures:
                    rejected = await expect_plan_rejection(
                        session,
                        FIXTURE_ASSETS[name],
                        property_path,
                        value,
                        expected_issue,
                    )
                    if rejected.get("ok"):
                        raise RuntimeError(f"Plan unexpectedly succeeded: {property_path}")
                    plan_rejections += 1
                await assert_clean_failure_invariants(
                    session,
                    FIXTURE_ASSETS["struct"],
                    package_hashes_before,
                    database_hash_before,
                    revision_export_hash_before,
                    package_files,
                    args.database,
                    args.revision_export,
                )

                # No-op: applying each fixture's initial value must not change the
                # in-memory property, create Undo, or dirty the package. ImportValue
                # clears and rebuilds Array/Set/Map containers, so the Bridge must
                # restore the pre-write snapshot and the original Dirty state.
                for property_path, noop_value, expected_kind in NOOP_CASES:
                    noop_plan = await plan_structured(
                        session,
                        FIXTURE_ASSETS["noop"],
                        property_path,
                        noop_value,
                    )
                    if not noop_plan.get("ok") or noop_plan.get("underlyingOperation") != "setAssetStructuredProperty":
                        raise RuntimeError(f"No-op structured Plan failed: {noop_plan}")
                    noop_plan_id = str(noop_plan["planId"])
                    wrong_confirmation = await apply_live(session, noop_plan_id, "LIVE APPLY wrong")
                    if wrong_confirmation.get("ok") or error_code(wrong_confirmation) != "live-editor-write-confirmation-required":
                        raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {wrong_confirmation}")
                    noop = await apply_live(session, noop_plan_id, f"LIVE APPLY {noop_plan_id}")
                    noop_result = noop.get("result", {})
                    if (
                        not noop.get("ok")
                        or noop.get("mode") != "LiveApply"
                        or noop.get("operation") != "setAssetStructuredProperty"
                        or noop.get("valueKind") != "structured"
                        or noop.get("changed") is not False
                        or noop.get("saved") is not False
                        or noop.get("diskRevisionChanged") is not False
                        or noop.get("undoAvailableInEditor") is not False
                        or noop_result.get("operation") != "setAssetStructuredProperty"
                        or noop_result.get("valueKind") != "structured"
                        or noop_result.get("structuredKind") != expected_kind
                        or noop_result.get("changed") is not False
                        or noop_result.get("transactionRecorded") is not False
                        or noop_result.get("packageDirtyAfter") is not False
                        or noop_result.get("dirtyAfter") is not False
                        or noop_result.get("saved") is not False
                        or noop_result.get("dirtyBefore") is not False
                        or noop_result.get("packageDirtyBefore") is not False
                        or not isinstance(noop_result.get("diff"), list)
                        or noop_result.get("diff") != []
                    ):
                        raise RuntimeError(
                            f"No-op LiveApply contract is broken for {property_path}: {noop}"
                        )
                    inspected = await call(
                        session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["noop"]}
                    )
                    memory = inspected.get("result", {}).get("memory", {})
                    if memory.get("packageDirty") is not False:
                        raise RuntimeError(
                            f"No-op marked the package Dirty for {property_path}: {inspected}"
                        )

                # Real structured writes: each property on its own clean fixture.
                success: list[tuple[str, str, Any, str]] = [
                    ("struct", "StructValue", STRUCT_VALUE, "Struct"),
                    ("array", "ArrayValue", ARRAY_VALUE, "Array"),
                    ("set", "SetValue", SET_VALUE, "Set"),
                    ("map", "MapValue", MAP_VALUE, "Map"),
                ]
                for name, property_path, value, structured_kind in success:
                    asset_path = FIXTURE_ASSETS[name]
                    plan = await plan_structured(session, asset_path, property_path, value)
                    if not plan.get("ok") or plan.get("underlyingOperation") != "setAssetStructuredProperty":
                        raise RuntimeError(f"Structured write Plan failed: {plan}")
                    plan_id = str(plan["planId"])
                    rejected = await apply_live(session, plan_id, "LIVE APPLY wrong")
                    if rejected.get("ok") or error_code(rejected) != "live-editor-write-confirmation-required":
                        raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {rejected}")
                    applied = await apply_live(session, plan_id, f"LIVE APPLY {plan_id}")
                    result = applied.get("result", {})
                    if (
                        not applied.get("ok")
                        or applied.get("mode") != "LiveApply"
                        or applied.get("operation") != "setAssetStructuredProperty"
                        or applied.get("valueKind") != "structured"
                        or applied.get("changed") is not True
                        or applied.get("saved") is not False
                        or applied.get("diskRevisionChanged") is not False
                        or applied.get("undoAvailableInEditor") is not True
                        or result.get("operation") != "setAssetStructuredProperty"
                        or result.get("valueKind") != "structured"
                        or result.get("structuredKind") != structured_kind
                        or result.get("structuredSchema") is None
                        or result.get("changed") is not True
                        or result.get("transactionRecorded") is not True
                        or result.get("transactionTitle") != "UE Agent Kit: Set Asset Structured Property"
                        or result.get("packageDirtyAfter") is not True
                        or result.get("dirtyAfter") is not True
                        or result.get("saved") is not False
                        or result.get("assetOpen") is not True
                        or result.get("loadedByBridge") is not False
                        or result.get("dirtyBefore") is not False
                        or result.get("packageDirtyBefore") is not False
                        or not isinstance(result.get("diff"), list)
                        or not result.get("diff")
                        or result.get("diffTruncated") is not False
                    ):
                        raise RuntimeError(f"Structured LiveApply result is incomplete: {applied}")
                    if result.get("afterValue") != value:
                        raise RuntimeError(f"LiveApply did not report the new structured value: {applied}")
                    if structured_kind == "Struct" and result.get("beforeValue") != INITIAL_STRUCT_VALUE:
                        raise RuntimeError(f"Struct fixture was not initially at the seeded value: {applied}")
                    inspected = await call(
                        session, "ue_inspect_asset_live", {"asset_path": asset_path}
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
                    success_cases.append(
                        {"asset": name, "propertyPath": property_path, "structuredKind": structured_kind}
                    )

                # Failure: target package is already Dirty (Bridge time).
                dirty_plan = await plan_structured(
                    session,
                    FIXTURE_ASSETS["struct"],
                    "ArrayValue",
                    {"valueType": "Array", "items": [9, 8, 7]},
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
                    session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["struct"]}
                )
                memory = inspected.get("result", {}).get("memory", {})
                if memory.get("packageDirty") is not True:
                    raise RuntimeError(f"Dirty-package rejection cleared the package flag: {inspected}")
                struct_fixture_id = FIXTURE_IDS["struct"]
                if sha256(package_files[struct_fixture_id]) != package_hashes_before[struct_fixture_id]:
                    raise RuntimeError("Dirty-package rejection changed the fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the frozen Revision Export")

                if any(sha256(path) != package_hashes_before[name] for name, path in package_files.items()):
                    raise RuntimeError("Structured LiveApply changed a fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Structured LiveApply modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("Structured LiveApply modified the frozen Revision Export")

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

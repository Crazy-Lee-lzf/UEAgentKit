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

MATERIALS_ROOT = "/Game/UEAgentKitWriteTests/Materials"
MATERIAL_INSTANCE_CLASS = "/Script/Engine.MaterialInstanceConstant"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

FIXTURE_IDS = {
    "scalar": "material-scalar-asset",
    "vector": "material-vector-asset",
    "texture": "material-texture-asset",
    "switch": "material-switch-asset",
    "noop": "material-noop-asset",
    "missing": "material-missing-parameter-asset",
    "non_instance": "material-non-instance-asset",
}

FIXTURE_ASSETS = {
    "scalar": f"{MATERIALS_ROOT}/MI_Scalar.MI_Scalar",
    "vector": f"{MATERIALS_ROOT}/MI_Vector.MI_Vector",
    "texture": f"{MATERIALS_ROOT}/MI_Texture.MI_Texture",
    "switch": f"{MATERIALS_ROOT}/MI_Switch.MI_Switch",
    "noop": f"{MATERIALS_ROOT}/MI_Noop.MI_Noop",
    "missing": f"{MATERIALS_ROOT}/MI_MissingParameter.MI_MissingParameter",
    "non_instance": f"{MATERIALS_ROOT}/DA_ScalarNonMaterial.DA_ScalarNonMaterial",
}

TEXTURE_TARGET = f"{MATERIALS_ROOT}/T_TextureTarget.T_TextureTarget"
TEXTURE_ALT = f"{MATERIALS_ROOT}/T_TextureAlt.T_TextureAlt"

SCALAR_PARAMETER = "EmissiveIntensity"
VECTOR_PARAMETER = "TintColor"
TEXTURE_PARAMETER = "BaseTexture"
SWITCH_PARAMETER = "EnableDetail"

INITIAL_SCALAR_VALUE = 0.25
SCALAR_VALUE = 0.75
INITIAL_VECTOR_VALUE = {"r": 0.0, "g": 0.5, "b": 0.25, "a": 1.0}
VECTOR_VALUE = {"r": 1.0, "g": 0.75, "b": 0.5, "a": 0.25}
INITIAL_TEXTURE_VALUE = TEXTURE_TARGET
TEXTURE_VALUE = TEXTURE_ALT
INITIAL_SWITCH_VALUE = False
SWITCH_VALUE = True
INITIAL_NOOP_VALUE = 0.5


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


async def plan_material(
    session: ClientSession,
    asset_path: str,
    parameter_name: str,
    parameter_type: str,
    value: Any,
) -> dict[str, Any]:
    return await call(
        session,
        "ue_set_material_parameter",
        {
            "asset_path": asset_path,
            "parameter_name": parameter_name,
            "parameter_type": parameter_type,
            "value": value,
            "mode": "Plan",
            "description": "Real UE5.6 Live Editor material write regression",
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
    parameter_name: str,
    parameter_type: str,
    value: Any,
    expected_issue: str,
) -> dict[str, Any]:
    rejected = await plan_material(
        session, asset_path, parameter_name, parameter_type, value
    )
    if rejected.get("ok") or expected_issue not in error_issue_codes(rejected):
        raise RuntimeError(
            f"Expected Plan rejection issue {expected_issue} for {parameter_name} but got: {rejected}"
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
                plan_failures: list[tuple[str, str, str, Any, str]] = [
                    (
                        "scalar",
                        SCALAR_PARAMETER,
                        "Scalar",
                        True,
                        "operation-value-type",
                    ),
                    (
                        "scalar",
                        VECTOR_PARAMETER,
                        "Scalar",
                        0.5,
                        "material-parameter-not-allowed",
                    ),
                    (
                        "texture",
                        TEXTURE_PARAMETER,
                        "Texture",
                        "/Game/UEAgentKitWriteTests/OtherRoot/T_None.T_None",
                        "reference-not-allowed",
                    ),
                    (
                        "switch",
                        SWITCH_PARAMETER,
                        "StaticSwitch",
                        "true",
                        "operation-value-type",
                    ),
                    (
                        "vector",
                        VECTOR_PARAMETER,
                        "Vector",
                        {"r": 0.5, "g": 0.5},
                        "operation-value-type",
                    ),
                    (
                        "non_instance",
                        SCALAR_PARAMETER,
                        "Scalar",
                        0.5,
                        "operation-asset-type",
                    ),
                ]
                for name, parameter_name, parameter_type, value, expected_issue in plan_failures:
                    rejected = await expect_plan_rejection(
                        session,
                        FIXTURE_ASSETS[name],
                        parameter_name,
                        parameter_type,
                        value,
                        expected_issue,
                    )
                    if rejected.get("ok"):
                        raise RuntimeError(f"Plan unexpectedly succeeded: {parameter_name}")
                    plan_rejections += 1
                await assert_clean_failure_invariants(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    package_hashes_before,
                    database_hash_before,
                    revision_export_hash_before,
                    package_files,
                    args.database,
                    args.revision_export,
                )

                # No-op: applying the seeded value must not change the parameter,
                # create Undo, or dirty the package.
                noop_plan = await plan_material(
                    session,
                    FIXTURE_ASSETS["noop"],
                    SCALAR_PARAMETER,
                    "Scalar",
                    INITIAL_NOOP_VALUE,
                )
                if (
                    not noop_plan.get("ok")
                    or noop_plan.get("underlyingOperation") != "setMaterialInstanceScalarParameter"
                ):
                    raise RuntimeError(f"No-op material Plan failed: {noop_plan}")
                noop_plan_id = str(noop_plan["planId"])
                wrong_confirmation = await apply_live(session, noop_plan_id, "LIVE APPLY wrong")
                if wrong_confirmation.get("ok") or error_code(wrong_confirmation) != "live-editor-write-confirmation-required":
                    raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {wrong_confirmation}")
                noop = await apply_live(session, noop_plan_id, f"LIVE APPLY {noop_plan_id}")
                noop_result = noop.get("result", {})
                if (
                    not noop.get("ok")
                    or noop.get("mode") != "LiveApply"
                    or noop.get("operation") != "setMaterialInstanceScalarParameter"
                    or noop.get("valueKind") != "material-scalar"
                    or noop.get("changed") is not False
                    or noop.get("saved") is not False
                    or noop.get("diskRevisionChanged") is not False
                    or noop.get("undoAvailableInEditor") is not False
                    or noop_result.get("operation") != "setMaterialInstanceScalarParameter"
                    or noop_result.get("valueKind") != "material-scalar"
                    or noop_result.get("parameterName") != SCALAR_PARAMETER
                    or noop_result.get("parameterType") != "Scalar"
                    or noop_result.get("parameterAssociation") != "Global"
                    or "propertyPath" in noop_result
                    or noop_result.get("changed") is not False
                    or noop_result.get("transactionRecorded") is not False
                    or noop_result.get("packageDirtyAfter") is not False
                    or noop_result.get("dirtyAfter") is not False
                    or noop_result.get("saved") is not False
                    or noop_result.get("dirtyBefore") is not False
                    or noop_result.get("packageDirtyBefore") is not False
                    or noop_result.get("afterValue") != INITIAL_NOOP_VALUE
                ):
                    raise RuntimeError(f"No-op material LiveApply contract is broken: {noop}")
                inspected = await call(
                    session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["noop"]}
                )
                memory = inspected.get("result", {}).get("memory", {})
                if memory.get("packageDirty") is not False:
                    raise RuntimeError(f"No-op marked the package Dirty: {inspected}")

                # Real material writes: each parameter on its own clean fixture.
                success: list[tuple[str, str, str, Any, Any]] = [
                    ("scalar", SCALAR_PARAMETER, "Scalar", SCALAR_VALUE, INITIAL_SCALAR_VALUE),
                    ("vector", VECTOR_PARAMETER, "Vector", VECTOR_VALUE, INITIAL_VECTOR_VALUE),
                    ("texture", TEXTURE_PARAMETER, "Texture", TEXTURE_VALUE, INITIAL_TEXTURE_VALUE),
                    ("switch", SWITCH_PARAMETER, "StaticSwitch", SWITCH_VALUE, INITIAL_SWITCH_VALUE),
                ]
                for name, parameter_name, parameter_type, value, initial_value in success:
                    asset_path = FIXTURE_ASSETS[name]
                    plan = await plan_material(
                        session, asset_path, parameter_name, parameter_type, value
                    )
                    if not plan.get("ok") or plan.get("underlyingOperation") != (
                        f"setMaterialInstance{parameter_type}Parameter"
                    ):
                        raise RuntimeError(f"Material write Plan failed: {plan}")
                    plan_id = str(plan["planId"])
                    rejected = await apply_live(session, plan_id, "LIVE APPLY wrong")
                    if rejected.get("ok") or error_code(rejected) != "live-editor-write-confirmation-required":
                        raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {rejected}")
                    applied = await apply_live(session, plan_id, f"LIVE APPLY {plan_id}")
                    result = applied.get("result", {})
                    operation = f"setMaterialInstance{parameter_type}Parameter"
                    value_kind = {
                        "Scalar": "material-scalar",
                        "Vector": "material-vector",
                        "Texture": "material-texture",
                        "StaticSwitch": "material-static-switch",
                    }[parameter_type]
                    if (
                        not applied.get("ok")
                        or applied.get("mode") != "LiveApply"
                        or applied.get("operation") != operation
                        or applied.get("valueKind") != value_kind
                        or applied.get("changed") is not True
                        or applied.get("saved") is not False
                        or applied.get("diskRevisionChanged") is not False
                        or applied.get("undoAvailableInEditor") is not True
                        or result.get("operation") != operation
                        or result.get("valueKind") != value_kind
                        or result.get("parameterName") != parameter_name
                        or result.get("parameterType") != parameter_type
                        or result.get("parameterAssociation") != "Global"
                        or "propertyPath" in result
                        or result.get("changed") is not True
                        or result.get("transactionRecorded") is not True
                        or result.get("transactionTitle") != "UE Agent Kit: Set Material Instance Parameter"
                        or result.get("packageDirtyAfter") is not True
                        or result.get("dirtyAfter") is not True
                        or result.get("saved") is not False
                        or result.get("assetOpen") is not True
                        or result.get("loadedByBridge") is not False
                        or result.get("dirtyBefore") is not False
                        or result.get("packageDirtyBefore") is not False
                        or result.get("beforeValue") != initial_value
                        or result.get("afterValue") != value
                    ):
                        raise RuntimeError(f"Material LiveApply result is incomplete: {applied}")
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
                        {"asset": name, "parameterName": parameter_name, "parameterType": parameter_type}
                    )

                # Bridge rejection: the parameter does not exist on this instance
                # even though the fixed Policy authorizes it.
                missing_plan = await plan_material(
                    session,
                    FIXTURE_ASSETS["missing"],
                    VECTOR_PARAMETER,
                    "Vector",
                    VECTOR_VALUE,
                )
                if not missing_plan.get("ok"):
                    raise RuntimeError(
                        f"Missing-parameter Plan unexpectedly rejected before the Bridge: {missing_plan}"
                    )
                missing_plan_id = str(missing_plan["planId"])
                rejected = await apply_live(session, missing_plan_id, f"LIVE APPLY {missing_plan_id}")
                if rejected.get("ok") or error_code(rejected) != "live-editor-write-material-parameter-not-found":
                    raise RuntimeError(
                        f"Expected live-editor-write-material-parameter-not-found but got: {rejected}"
                    )
                bridge_rejections += 1

                # Bridge rejection: the target package is already Dirty.
                dirty_plan = await plan_material(
                    session,
                    FIXTURE_ASSETS["scalar"],
                    SCALAR_PARAMETER,
                    "Scalar",
                    INITIAL_SCALAR_VALUE,
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
                    session, "ue_inspect_asset_live", {"asset_path": FIXTURE_ASSETS["scalar"]}
                )
                memory = inspected.get("result", {}).get("memory", {})
                if memory.get("packageDirty") is not True:
                    raise RuntimeError(f"Dirty-package rejection cleared the package flag: {inspected}")
                scalar_fixture_id = FIXTURE_IDS["scalar"]
                if sha256(package_files[scalar_fixture_id]) != package_hashes_before[scalar_fixture_id]:
                    raise RuntimeError("Dirty-package rejection changed the fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("Dirty-package rejection modified the frozen Revision Export")

                if any(sha256(path) != package_hashes_before[name] for name, path in package_files.items()):
                    raise RuntimeError("Material LiveApply changed a fixture package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("Material LiveApply modified the immutable SQLite index")
                if directory_sha256(args.revision_export) != revision_export_hash_before:
                    raise RuntimeError("Material LiveApply modified the frozen Revision Export")

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

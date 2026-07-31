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
ASSET_PATH = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)
def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value
async def run(args: argparse.Namespace) -> dict[str, Any]:
    package_hash_before = sha256(args.package_file)
    database_hash_before = sha256(args.database)
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
                status = payload(await session.call_tool("ue_editor_status", {}), "ue_editor_status")
                if not status.get("ok") or status["result"].get("pieState") != "stopped":
                    raise RuntimeError(f"Live Editor is not ready for writes: {status}")
                opened = payload(
                    await session.call_tool("ue_open_asset", {"asset_path": ASSET_PATH}),
                    "ue_open_asset",
                )
                if not opened.get("ok") or not opened["result"].get("openAfter"):
                    raise RuntimeError(f"The scalar fixture was not opened: {opened}")
                plan = payload(
                    await session.call_tool(
                        "ue_set_asset_property",
                        {
                            "asset_path": ASSET_PATH,
                            "property_path": "BoolValue",
                            "value": True,
                            "mode": "Plan",
                            "description": "Real UE5.6 Live Editor Write regression",
                        },
                    ),
                    "ue_set_asset_property",
                )
                if not plan.get("ok") or plan.get("underlyingOperation") != "setAssetProperty":
                    raise RuntimeError(f"Live write Plan failed: {plan}")
                plan_id = str(plan["planId"])
                rejected = payload(
                    await session.call_tool(
                        "ue_apply_asset_property_live",
                        {"plan_id": plan_id, "confirmation": "LIVE APPLY wrong"},
                    ),
                    "ue_apply_asset_property_live invalid confirmation",
                )
                if rejected.get("ok") or rejected.get("error", {}).get("code") != "live-editor-write-confirmation-required":
                    raise RuntimeError(f"Invalid LiveApply confirmation was not rejected: {rejected}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("Rejected LiveApply changed the asset package on disk")
                applied = payload(
                    await session.call_tool(
                        "ue_apply_asset_property_live",
                        {"plan_id": plan_id, "confirmation": f"LIVE APPLY {plan_id}"},
                    ),
                    "ue_apply_asset_property_live",
                )
                result = applied.get("result", {})
                if (
                    not applied.get("ok")
                    or applied.get("mode") != "LiveApply"
                    or applied.get("changed") is not True
                    or applied.get("saved") is not False
                    or applied.get("diskRevisionChanged") is not False
                    or applied.get("undoAvailableInEditor") is not True
                    or result.get("beforeValue") is not False
                    or result.get("afterValue") is not True
                    or result.get("packageDirtyAfter") is not True
                    or result.get("transactionRecorded") is not True
                    or result.get("saved") is not False
                ):
                    raise RuntimeError(f"LiveApply result is incomplete: {applied}")
                inspected = payload(
                    await session.call_tool("ue_inspect_asset_live", {"asset_path": ASSET_PATH}),
                    "ue_inspect_asset_live",
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
                dirty = payload(await session.call_tool("ue_get_dirty_assets", {}), "ue_get_dirty_assets")
                dirty_items = dirty.get("result", {}).get("items", [])
                if not any(
                    ASSET_PATH in item.get("assetPaths", [])
                    for item in dirty_items
                    if isinstance(item, dict) and isinstance(item.get("assetPaths"), list)
                ):
                    raise RuntimeError(f"Dirty asset list does not contain the live-written asset: {dirty}")
                if sha256(args.package_file) != package_hash_before:
                    raise RuntimeError("LiveApply changed the asset package on disk")
                if sha256(args.database) != database_hash_before:
                    raise RuntimeError("LiveApply modified the immutable SQLite index")
    return {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(EXPECTED_TOOLS),
        "assetPath": ASSET_PATH,
        "planIdPresent": bool(plan_id),
        "invalidConfirmationRejected": True,
        "memoryValueChanged": True,
        "packageDirtyAfter": True,
        "undoTransactionRecorded": True,
        "saved": False,
        "diskPackageHashUnchanged": True,
        "databaseHashUnchanged": True,
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
    parser.add_argument("--package-file", type=Path, required=True)
    parser.add_argument("--error-log", type=Path, required=True)
    parser.add_argument("--session-marker", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())

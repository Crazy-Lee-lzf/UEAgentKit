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

from ue_agent_kit.editor_bridge import (  # noqa: E402
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
)
from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402
from ue_agent_kit import __version__  # noqa: E402

ASSET_PATH = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    project = args.project.resolve()
    package_file = args.package_file.resolve()
    descriptor_path = project.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"
    bridge = LiveEditorBridgeService(
        LiveEditorBridgeConfig(project, timeout_seconds=10.0),
        server_version=__version__,
    )
    status_before = bridge.status()
    if status_before.get("state") != "available":
        raise RuntimeError(f"Editor Bridge is unavailable: {status_before}")
    descriptor_before = json.loads(descriptor_path.read_text(encoding="utf-8-sig"))
    before_revision = _sha256(package_file)

    bridge.call_tool("ue_open_asset", {"assetPath": ASSET_PATH})
    prepared = bridge.call_method(
        "editor.prepareAuthorizedSaveFixture",
        {"assetPath": ASSET_PATH},
        timeout_seconds=10.0,
    )
    if prepared.get("packageDirty") is not True or prepared.get("saved") is not False:
        raise RuntimeError(f"Fixture test hook did not produce a Dirty package: {prepared}")

    parameters = StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(args.database.resolve()),
            "-EnableWriteTools",
            "-EnableCommitTools",
            "-EnableLiveEditor",
            "-EngineRoot",
            str(args.engine_root.resolve()),
            "-ProjectPath",
            str(project),
            "-Policy",
            str(args.policy.resolve()),
            "-RevisionExport",
            str(args.revision_export.resolve()),
            "-WorkRoot",
            str(args.work_root.resolve()),
            "-BackupRoot",
            str(args.backup_root.resolve()),
            "-LiveEditorTimeoutSeconds",
            "10",
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )

    with args.error_log.resolve().open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                expected = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)
                tool_names = [tool.name for tool in tools.tools]
                if tool_names != expected or len(tool_names) != 37:
                    raise RuntimeError(f"Unexpected combined Tool list: {tool_names}")

                capabilities = (await session.call_tool("ue_get_capabilities", {})).structuredContent
                if not capabilities:
                    raise RuntimeError("Capabilities response is empty")
                save_contract = capabilities.get("liveEditor", {}).get("authorizedSave", {})
                if (
                    save_contract.get("available") is not True
                    or save_contract.get("commitEnabled") is not True
                    or save_contract.get("saveAllSupported") is not False
                    or save_contract.get("backupBeforeSave") is not True
                    or save_contract.get("independentVerification") is not True
                ):
                    raise RuntimeError(f"Authorized-save capability contract is invalid: {save_contract}")

                preview = (
                    await session.call_tool(
                        "ue_save_authorized_asset",
                        {"asset_path": ASSET_PATH, "mode": "Preview"},
                    )
                ).structuredContent
                if (
                    not preview
                    or preview.get("ok") is not True
                    or preview.get("mode") != "Preview"
                    or preview.get("saved") is not False
                    or preview.get("expectedDiskRevision") != before_revision
                    or preview.get("packageDirty") is not True
                ):
                    raise RuntimeError(f"Authorized-save Preview failed: {preview}")
                receipt = str(preview.get("saveReceipt", ""))
                if not receipt.startswith("save_"):
                    raise RuntimeError(f"Authorized-save Preview returned an invalid receipt: {preview}")

                wrong = (
                    await session.call_tool(
                        "ue_save_authorized_asset",
                        {
                            "asset_path": ASSET_PATH,
                            "mode": "Commit",
                            "save_receipt": receipt,
                            "confirmation": "SAVE wrong",
                        },
                    )
                ).structuredContent
                if not wrong or wrong.get("ok") is not False or wrong.get("error", {}).get("code") != "save-confirmation-required":
                    raise RuntimeError(f"Invalid save confirmation was not rejected: {wrong}")
                if _sha256(package_file) != before_revision:
                    raise RuntimeError("Invalid confirmation changed the package on disk")

                committed = (
                    await session.call_tool(
                        "ue_save_authorized_asset",
                        {
                            "asset_path": ASSET_PATH,
                            "mode": "Commit",
                            "save_receipt": receipt,
                            "confirmation": f"SAVE {receipt}",
                        },
                    )
                ).structuredContent
                if (
                    not committed
                    or committed.get("ok") is not True
                    or committed.get("mode") != "Commit"
                    or committed.get("saved") is not True
                    or committed.get("verified") is not True
                    or committed.get("beforeRevision") != before_revision
                    or committed.get("indexFreshness", {}).get("state") != "stale"
                ):
                    raise RuntimeError(f"Authorized-save Commit failed: {committed}")
                after_revision = _sha256(package_file)
                if committed.get("afterRevision") != after_revision or after_revision == before_revision:
                    raise RuntimeError(f"Authorized save did not create the expected Revision transition: {committed}")

                reused = (
                    await session.call_tool(
                        "ue_save_authorized_asset",
                        {
                            "asset_path": ASSET_PATH,
                            "mode": "Commit",
                            "save_receipt": receipt,
                            "confirmation": f"SAVE {receipt}",
                        },
                    )
                ).structuredContent
                if not reused or reused.get("ok") is not False or reused.get("error", {}).get("code") != "index-stale":
                    raise RuntimeError(f"Consumed/stale save could be replayed: {reused}")

                state = (
                    await session.call_tool("ue_get_asset_state", {"asset_path": ASSET_PATH})
                ).structuredContent
                if not state or state.get("ok") is not True or state.get("state") != "disk-newer-than-snapshots":
                    raise RuntimeError(f"Post-save four-source state is invalid: {state}")

    status_after = bridge.status()
    descriptor_after = json.loads(descriptor_path.read_text(encoding="utf-8-sig"))
    if (
        status_after.get("state") != "available"
        or status_after.get("processId") != status_before.get("processId")
        or status_after.get("sessionId") != status_before.get("sessionId")
        or descriptor_after.get("processId") != descriptor_before.get("processId")
        or descriptor_after.get("sessionId") != descriptor_before.get("sessionId")
    ):
        raise RuntimeError("Main Editor Bridge changed or stopped during authorized save")

    backup_root = args.backup_root.resolve() / "live-save" / receipt
    manifest_path = backup_root / "manifest.json"
    backup_file = backup_root / package_file.name
    if not manifest_path.is_file() or not backup_file.is_file() or _sha256(backup_file) != before_revision:
        raise RuntimeError("Authorized save did not create a valid pre-save backup")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    if manifest.get("assetPath") != ASSET_PATH or manifest.get("beforeRevision") != before_revision:
        raise RuntimeError(f"Authorized-save backup Manifest is invalid: {manifest}")

    return {
        "toolCount": 37,
        "assetPath": ASSET_PATH,
        "previewReceiptCreated": True,
        "invalidConfirmationRejected": True,
        "saved": True,
        "verified": True,
        "beforeRevision": before_revision,
        "afterRevision": _sha256(package_file),
        "revisionChanged": True,
        "backupVerified": True,
        "receiptReplayRejected": True,
        "indexMarkedStale": True,
        "mainEditorSurvived": True,
        "descriptorUnchanged": True,
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
    args = parser.parse_args()
    args.error_log.resolve().parent.mkdir(parents=True, exist_ok=True)
    report = asyncio.run(_run(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

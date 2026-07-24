from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402
ASSET_PATH = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"
EXPECTED_TOOLS = tool_names_for_mode(workflow_enabled=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def require_payload(result: Any, tool: str) -> dict[str, Any]:
    payload = result.structuredContent
    if not payload or not isinstance(payload, dict):
        raise RuntimeError(f"{tool} returned no structured response: {result}")
    if not payload.get("ok"):
        raise RuntimeError(f"{tool} failed: {payload}")
    return payload


def server_parameters(args: argparse.Namespace) -> StdioServerParameters:
    return StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(args.database),
            "-EnableWriteTools",
            "-EnableCommitTools",
            "-EngineRoot",
            str(args.engine_root),
            "-ProjectPath",
            str(args.project),
            "-Policy",
            str(args.policy),
            "-RevisionExport",
            str(args.revision_export),
            "-WorkRoot",
            str(args.work_root),
            "-BackupRoot",
            str(args.backup_root),
            "-ProcessTimeoutSeconds",
            "1800",
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )


@asynccontextmanager
async def open_session(
    args: argparse.Namespace,
    error_log: Path,
) -> AsyncIterator[tuple[ClientSession, Any]]:
    error_log.parent.mkdir(parents=True, exist_ok=True)
    with error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(server_parameters(args), errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                yield session, initialized


async def first_session(args: argparse.Namespace) -> dict[str, Any]:
    package_before_hash = sha256(args.package_file)
    pointer = args.work_root / "active-snapshot.json"
    if pointer.exists():
        raise RuntimeError("Snapshot refresh smoke WorkRoot is not clean before the first session")

    async with open_session(args, args.error_log_first) as (session, initialized):
        listed = await session.list_tools()
        tool_names = [tool.name for tool in listed.tools]
        if tool_names != EXPECTED_TOOLS:
            raise RuntimeError(f"Unexpected snapshot refresh Tool list: {tool_names}")
        refresh_tool = next(tool for tool in listed.tools if tool.name == "ue_refresh_asset_index")
        if set(refresh_tool.inputSchema.get("properties", {})) != {"asset_path", "mode"}:
            raise RuntimeError(f"Refresh Tool exposes unexpected parameters: {refresh_tool.inputSchema}")
        if not refresh_tool.annotations or refresh_tool.annotations.readOnlyHint or refresh_tool.annotations.destructiveHint:
            raise RuntimeError(f"Refresh Tool annotations are incorrect: {refresh_tool.annotations}")

        capabilities = require_payload(
            await session.call_tool("ue_get_capabilities", {}),
            "ue_get_capabilities",
        )
        refresh_contract = capabilities.get("snapshotRefresh", {})
        if not refresh_contract.get("pairedGeneration") or not refresh_contract.get("restartRequiredAfterApply"):
            raise RuntimeError(f"Snapshot refresh capability contract is incomplete: {capabilities}")

        initial_status = require_payload(
            await session.call_tool("ue_get_project_status", {}),
            "ue_get_project_status initial",
        )
        if initial_status["freshness"]["state"] != "fresh":
            raise RuntimeError(f"Initial snapshot is not fresh: {initial_status}")
        initial_lifecycle = initial_status["workflow"]["indexLifecycle"]
        if not initial_lifecycle.get("sessionUsesFrozenSnapshot"):
            raise RuntimeError(f"Workflow session did not freeze its active snapshot: {initial_status}")

        old_asset = require_payload(
            await session.call_tool(
                "ue_get_asset",
                {"asset_path": ASSET_PATH, "sections": ["identity", "metadata"]},
            ),
            "ue_get_asset before Commit",
        )
        old_revision = str(old_asset["asset"]["revision_value"])
        initial_asset_state = require_payload(
            await session.call_tool("ue_get_asset_state", {"asset_path": ASSET_PATH}),
            "ue_get_asset_state before Commit",
        )
        if initial_asset_state.get("state") != "synchronized":
            raise RuntimeError(f"Initial four-source state is not synchronized: {initial_asset_state}")

        prepared = require_payload(
            await session.call_tool(
                "ue_set_asset_property",
                {
                    "asset_path": ASSET_PATH,
                    "property_path": "BoolValue",
                    "value": True,
                    "mode": "DryRun",
                    "description": "UE Agent Kit paired snapshot refresh smoke test.",
                },
            ),
            "ue_set_asset_property DryRun",
        )
        plan_id = str(prepared["planId"])
        dry_receipt = str(prepared["dryRunReceipt"])
        if sha256(args.package_file) != package_before_hash:
            raise RuntimeError("Dry Run changed the fixture package before snapshot refresh")

        applied = require_payload(
            await session.call_tool(
                "ue_apply_patch",
                {
                    "plan_id": plan_id,
                    "dry_run_receipt": dry_receipt,
                    "confirmation": f"COMMIT {plan_id}",
                },
            ),
            "ue_apply_patch",
        )
        apply_receipt = str(applied["applyReceipt"])
        committed_revision = str(applied["afterRevision"])
        if committed_revision == old_revision or sha256(args.package_file) == package_before_hash:
            raise RuntimeError(f"Commit did not create a new Package Revision: {applied}")

        verified = require_payload(
            await session.call_tool("ue_verify_asset", {"apply_receipt": apply_receipt}),
            "ue_verify_asset",
        )
        if verified["actualRevision"] != committed_revision:
            raise RuntimeError(f"Committed Revision verification failed: {verified}")
        committed_asset_state = require_payload(
            await session.call_tool("ue_get_asset_state", {"asset_path": ASSET_PATH}),
            "ue_get_asset_state after Commit",
        )
        if committed_asset_state.get("state") != "disk-newer-than-snapshots":
            raise RuntimeError(f"Committed four-source state is incorrect: {committed_asset_state}")
        if not committed_asset_state.get("indexRefreshRequired"):
            raise RuntimeError(f"Committed state did not require index refresh: {committed_asset_state}")

        preview = require_payload(
            await session.call_tool(
                "ue_refresh_asset_index",
                {"asset_path": ASSET_PATH, "mode": "Preview"},
            ),
            "ue_refresh_asset_index Preview",
        )
        if preview.get("applied") or preview.get("targetRevision") != committed_revision or pointer.exists():
            raise RuntimeError(f"Refresh Preview changed the active snapshot or reported the wrong Revision: {preview}")

        refreshed = require_payload(
            await session.call_tool(
                "ue_refresh_asset_index",
                {"asset_path": ASSET_PATH, "mode": "Apply"},
            ),
            "ue_refresh_asset_index Apply",
        )
        if not refreshed.get("applied") or not refreshed.get("restartRequired"):
            raise RuntimeError(f"Refresh Apply did not switch a new generation: {refreshed}")
        if not refreshed.get("currentSessionUsesPreviousSnapshot") or not pointer.is_file():
            raise RuntimeError(f"Refresh Apply did not preserve the old session boundary: {refreshed}")
        generation = refreshed.get("newGeneration", {})
        generation_id = str(generation.get("generationId", ""))
        generation_root = args.work_root / "snapshots" / generation_id
        if not generation_root.is_dir():
            raise RuntimeError(f"The paired snapshot generation was not created: {refreshed}")

        old_session_asset = require_payload(
            await session.call_tool(
                "ue_get_asset",
                {"asset_path": ASSET_PATH, "sections": ["identity", "metadata"]},
            ),
            "ue_get_asset in previous session",
        )
        if old_session_asset["asset"]["revision_value"] != old_revision:
            raise RuntimeError(f"The old MCP session switched snapshots in place: {old_session_asset}")
        old_session_state = require_payload(
            await session.call_tool("ue_get_asset_state", {"asset_path": ASSET_PATH}),
            "ue_get_asset_state in previous session",
        )
        if old_session_state.get("state") != "disk-newer-than-snapshots" or not old_session_state.get("restartRequired"):
            raise RuntimeError(f"Old-session four-source state lost its frozen boundary: {old_session_state}")

        post_refresh_status = require_payload(
            await session.call_tool("ue_get_project_status", {}),
            "ue_get_project_status after refresh",
        )
        lifecycle = post_refresh_status["workflow"]["indexLifecycle"]
        if not lifecycle.get("refreshAppliedInSession") or not lifecycle.get("restartRequired"):
            raise RuntimeError(f"Workflow status did not require a restart after refresh: {post_refresh_status}")

        rejected = await session.call_tool(
            "ue_plan_patch",
            {
                "asset_path": ASSET_PATH,
                "operation": "setAssetProperty",
                "target": {"propertyPath": "BoolValue"},
                "value": False,
            },
        )
        rejected_payload = rejected.structuredContent
        if not rejected_payload or rejected_payload.get("ok"):
            raise RuntimeError("The old session accepted a new workflow Plan after snapshot refresh")
        if rejected_payload["error"]["code"] != "snapshot-refresh-restart-required":
            raise RuntimeError(f"Unexpected post-refresh workflow rejection: {rejected_payload}")

        return {
            "protocolVersion": initialized.protocolVersion,
            "oldRevision": old_revision,
            "committedRevision": committed_revision,
            "generationId": generation_id,
            "oldSessionStayedOld": True,
            "restartRequired": True,
            "initialAssetState": initial_asset_state["state"],
            "committedAssetState": committed_asset_state["state"],
            "oldSessionAssetState": old_session_state["state"],
            "packageBeforeHash": package_before_hash,
        }


async def second_session(args: argparse.Namespace, first: dict[str, Any]) -> dict[str, Any]:
    async with open_session(args, args.error_log_second) as (session, initialized):
        listed = await session.list_tools()
        if [tool.name for tool in listed.tools] != EXPECTED_TOOLS:
            raise RuntimeError("The new snapshot session exposed an unexpected Tool set")
        status = require_payload(
            await session.call_tool("ue_get_project_status", {}),
            "ue_get_project_status new session",
        )
        if status["freshness"]["state"] != "fresh":
            raise RuntimeError(f"The new snapshot generation is not fresh: {status}")
        lifecycle = status["workflow"]["indexLifecycle"]
        if lifecycle.get("activeSnapshotGenerationId") != first["generationId"]:
            raise RuntimeError(f"The new session did not select the active generation: {status}")
        if lifecycle.get("refreshAppliedInSession") or lifecycle.get("restartRequired"):
            raise RuntimeError(f"The new session inherited the previous session refresh state: {status}")

        asset = require_payload(
            await session.call_tool(
                "ue_get_asset",
                {"asset_path": ASSET_PATH, "sections": ["identity", "metadata"]},
            ),
            "ue_get_asset new session",
        )
        if asset["asset"]["revision_value"] != first["committedRevision"]:
            raise RuntimeError(f"The new session did not read the refreshed Revision: {asset}")
        new_asset_state = require_payload(
            await session.call_tool("ue_get_asset_state", {"asset_path": ASSET_PATH}),
            "ue_get_asset_state new session",
        )
        if new_asset_state.get("state") != "synchronized":
            raise RuntimeError(f"The new session four-source state is not synchronized: {new_asset_state}")
        return {
            "protocolVersion": initialized.protocolVersion,
            "newSessionFresh": True,
            "newSessionRevision": asset["asset"]["revision_value"],
            "generationId": lifecycle["activeSnapshotGenerationId"],
            "assetState": new_asset_state["state"],
        }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    database_before = tree_snapshot(args.database.parent)
    revision_export_before = tree_snapshot(args.revision_export)
    first = await first_session(args)
    if tree_snapshot(args.database.parent) != database_before:
        raise RuntimeError("Snapshot refresh modified the configured immutable SQLite source")
    if tree_snapshot(args.revision_export) != revision_export_before:
        raise RuntimeError("Snapshot refresh modified the configured Revision Export source")
    second = await second_session(args, first)
    if tree_snapshot(args.database.parent) != database_before:
        raise RuntimeError("The new session modified the configured immutable SQLite source")
    if tree_snapshot(args.revision_export) != revision_export_before:
        raise RuntimeError("The new session modified the configured Revision Export source")
    return {
        "protocolVersion": first["protocolVersion"],
        "tools": EXPECTED_TOOLS,
        "previewZeroMutation": True,
        "pairedGenerationApplied": True,
        "oldSessionStayedOld": first["oldSessionStayedOld"],
        "postRefreshWorkflowRejected": True,
        "newSessionFresh": second["newSessionFresh"],
        "fourSourceStateTransitions": {
            "initial": first["initialAssetState"],
            "committed": first["committedAssetState"],
            "oldSessionAfterRefresh": first["oldSessionAssetState"],
            "newSession": second["assetState"],
        },
        "generationId": second["generationId"],
        "oldRevision": first["oldRevision"],
        "newRevision": second["newSessionRevision"],
        "configuredDatabaseUnchanged": True,
        "configuredRevisionExportUnchanged": True,
        "firstServerLogLines": len(args.error_log_first.read_text(encoding="utf-8").splitlines()),
        "secondServerLogLines": len(args.error_log_second.read_text(encoding="utf-8").splitlines()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--policy", type=Path, required=True)
    parser.add_argument("--revision-export", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--package-file", type=Path, required=True)
    parser.add_argument("--error-log-first", type=Path, required=True)
    parser.add_argument("--error-log-second", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    result = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

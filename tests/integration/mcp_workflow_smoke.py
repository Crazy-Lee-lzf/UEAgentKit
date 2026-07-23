from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]
ASSET_PATH = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"
EXPECTED_TOOLS = [
    "ue_get_capabilities",
    "ue_get_project_status",
    "ue_search",
    "ue_get_asset",
    "ue_find_references",
    "ue_plan_patch",
    "ue_dry_run_patch",
    "ue_apply_patch",
    "ue_verify_asset",
    "ue_rollback_patch",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directory_snapshot(directory: Path) -> dict[str, str]:
    return {
        path.name: sha256(path)
        for path in sorted(directory.iterdir())
        if path.is_file()
    }


def require_payload(result, tool: str) -> dict[str, object]:
    payload = result.structuredContent
    if not payload or not isinstance(payload, dict):
        raise RuntimeError(f"{tool} returned no structured response: {result}")
    if not payload.get("ok"):
        raise RuntimeError(f"{tool} failed: {payload}")
    return payload


async def run_workflow(args: argparse.Namespace) -> dict[str, object]:
    database_before = directory_snapshot(args.database.parent)
    package_before_hash = sha256(args.package_file)
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
    args.error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.error_log.open("w", encoding="utf-8", newline="\n") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                tool_names = [tool.name for tool in tools.tools]
                if tool_names != EXPECTED_TOOLS:
                    raise RuntimeError(f"Unexpected full MCP tool set: {tool_names}")
                forbidden = {
                    "database",
                    "project",
                    "project_path",
                    "engine_root",
                    "policy",
                    "revision_export",
                    "work_root",
                    "backup_root",
                    "command",
                }
                for tool in tools.tools:
                    properties = set(tool.inputSchema.get("properties", {}))
                    if properties.intersection(forbidden):
                        raise RuntimeError(f"Tool exposes fixed configuration: {tool.name} {properties}")

                capabilities = require_payload(
                    await session.call_tool("ue_get_capabilities", {}),
                    "ue_get_capabilities",
                )
                if capabilities["server"]["mode"] != "fixed-project-commit":
                    raise RuntimeError(f"Unexpected MCP mode: {capabilities}")
                if not capabilities["operations"]["available"]:
                    raise RuntimeError(f"Write operations were not reported as available: {capabilities}")

                project_status = require_payload(
                    await session.call_tool("ue_get_project_status", {}),
                    "ue_get_project_status",
                )
                if project_status["project"]["projectName"] != args.project.stem:
                    raise RuntimeError(f"Fixed project identity mismatch: {project_status}")
                if not str(project_status["engine"].get("version", "")).startswith("5.6"):
                    raise RuntimeError(f"Unexpected Engine version: {project_status}")
                if project_status["revisionExport"]["state"] != "available":
                    raise RuntimeError(f"Revision Export status mismatch: {project_status}")

                search = require_payload(
                    await session.call_tool(
                        "ue_search",
                        {"query": "DA_ScalarPatchTarget", "scope": "assets"},
                    ),
                    "ue_search",
                )
                if search["pagination"]["resultCount"] != 1:
                    raise RuntimeError(f"Scalar fixture search failed: {search}")

                planned = require_payload(
                    await session.call_tool(
                        "ue_plan_patch",
                        {
                            "asset_path": ASSET_PATH,
                            "operation": "setAssetProperty",
                            "target": {"propertyPath": "BoolValue"},
                            "value": True,
                            "description": "UE Agent Kit 0.5.0 MCP full workflow smoke test.",
                        },
                    ),
                    "ue_plan_patch",
                )
                plan_id = str(planned["planId"])

                dry_run = require_payload(
                    await session.call_tool("ue_dry_run_patch", {"plan_id": plan_id}),
                    "ue_dry_run_patch",
                )
                if not all(dry_run["gates"].values()):
                    raise RuntimeError(f"Dry Run gates failed: {dry_run}")
                if sha256(args.package_file) != package_before_hash:
                    raise RuntimeError("MCP Dry Run changed the scalar fixture package")
                dry_receipt = str(dry_run["dryRunReceipt"])

                rejected_apply = await session.call_tool(
                    "ue_apply_patch",
                    {
                        "plan_id": plan_id,
                        "dry_run_receipt": dry_receipt,
                        "confirmation": "COMMIT wrong",
                    },
                )
                rejected_payload = rejected_apply.structuredContent
                if not rejected_payload or rejected_payload.get("ok"):
                    raise RuntimeError(f"Invalid Commit confirmation was accepted: {rejected_payload}")
                if rejected_payload["error"]["code"] != "commit-confirmation-required":
                    raise RuntimeError(f"Unexpected Commit rejection: {rejected_payload}")

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
                if applied["beforeRevision"] == applied["afterRevision"]:
                    raise RuntimeError(f"Commit Revision did not change: {applied}")
                package_after_commit_hash = sha256(args.package_file)
                if package_after_commit_hash == package_before_hash:
                    raise RuntimeError("Commit did not change the scalar fixture package hash")

                reused = await session.call_tool(
                    "ue_apply_patch",
                    {
                        "plan_id": plan_id,
                        "dry_run_receipt": dry_receipt,
                        "confirmation": f"COMMIT {plan_id}",
                    },
                )
                reused_payload = reused.structuredContent
                if not reused_payload or reused_payload.get("ok"):
                    raise RuntimeError("A one-time Dry Run receipt was reused")

                verified = require_payload(
                    await session.call_tool("ue_verify_asset", {"apply_receipt": apply_receipt}),
                    "ue_verify_asset",
                )
                if not verified["verified"] or verified["actualRevision"] != applied["afterRevision"]:
                    raise RuntimeError(f"Independent Commit verification failed: {verified}")

                rollback_dry = require_payload(
                    await session.call_tool(
                        "ue_rollback_patch",
                        {"apply_receipt": apply_receipt, "mode": "DryRun"},
                    ),
                    "ue_rollback_patch DryRun",
                )
                rollback_receipt = str(rollback_dry["rollbackDryRunReceipt"])
                if sha256(args.package_file) != package_after_commit_hash:
                    raise RuntimeError("Rollback Dry Run changed the committed package")

                rejected_rollback = await session.call_tool(
                    "ue_rollback_patch",
                    {
                        "apply_receipt": apply_receipt,
                        "mode": "Commit",
                        "rollback_dry_run_receipt": rollback_receipt,
                        "confirmation": "ROLLBACK wrong",
                    },
                )
                rejected_rollback_payload = rejected_rollback.structuredContent
                if not rejected_rollback_payload or rejected_rollback_payload.get("ok"):
                    raise RuntimeError("Invalid rollback confirmation was accepted")
                if rejected_rollback_payload["error"]["code"] != "rollback-confirmation-required":
                    raise RuntimeError(f"Unexpected rollback rejection: {rejected_rollback_payload}")

                restored = require_payload(
                    await session.call_tool(
                        "ue_rollback_patch",
                        {
                            "apply_receipt": apply_receipt,
                            "mode": "Commit",
                            "rollback_dry_run_receipt": rollback_receipt,
                            "confirmation": f"ROLLBACK {apply_receipt}",
                        },
                    ),
                    "ue_rollback_patch Commit",
                )
                if not restored["restored"]:
                    raise RuntimeError(f"Rollback did not restore the package: {restored}")

    package_final_hash = sha256(args.package_file)
    if package_final_hash != package_before_hash:
        raise RuntimeError(
            f"Final package hash was not restored: before={package_before_hash} after={package_final_hash}"
        )
    database_after = directory_snapshot(args.database.parent)
    if database_after != database_before:
        raise RuntimeError(
            f"MCP workflow changed immutable index files: before={database_before}, after={database_after}"
        )
    return {
        "protocolVersion": initialized.protocolVersion,
        "serverName": initialized.serverInfo.name,
        "tools": EXPECTED_TOOLS,
        "capabilitiesChecked": True,
        "projectStatusChecked": True,
        "engineVersion": project_status["engine"]["version"],
        "planId": plan_id,
        "dryRunReceiptIssued": True,
        "invalidCommitConfirmationRejected": True,
        "commitReceiptSingleUse": True,
        "applyReceiptIssued": True,
        "committedRevision": applied["afterRevision"],
        "independentCommitVerification": True,
        "rollbackDryRunReceiptIssued": True,
        "invalidRollbackConfirmationRejected": True,
        "rollbackVerified": True,
        "packageHashRestored": True,
        "indexDirectoryUnchanged": True,
        "serverLogLines": len(args.error_log.read_text(encoding="utf-8").splitlines()),
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
    parser.add_argument("--error-log", type=Path, required=True)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = asyncio.run(run_workflow(args))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

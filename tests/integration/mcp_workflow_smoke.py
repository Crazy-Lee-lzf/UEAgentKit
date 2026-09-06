from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.memory_reports import build_memory_audit_report  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402
ASSET_PATH = "/Game/UEAgentKitWriteTests/ScalarRegression/DA_ScalarPatchTarget.DA_ScalarPatchTarget"
EXPECTED_TOOLS = tool_names_for_mode(workflow_enabled=True, memory_enabled=True)


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
            "-EnableProjectMemory",
            "-MemoryDatabase",
            str(args.memory_database),
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
                if not capabilities["highLevelChanges"]["available"]:
                    raise RuntimeError(f"High-level changes were not reported as available: {capabilities}")
                if capabilities["highLevelChanges"]["defaultMode"] != "Plan":
                    raise RuntimeError(f"Unexpected high-level default mode: {capabilities}")
                memory_contract = capabilities["projectMemory"]
                if not memory_contract["configured"] or not memory_contract["workflowEvidenceHandoff"]:
                    raise RuntimeError(f"Workflow Memory evidence handoff is unavailable: {capabilities}")
                if memory_contract["workflowEvidenceSourceTools"] != [
                    "ue_verify_asset",
                    "ue_rollback_patch",
                ]:
                    raise RuntimeError(f"Workflow Memory source Tools mismatch: {capabilities}")

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
                if project_status["freshness"]["state"] != "fresh":
                    raise RuntimeError(f"Initial index freshness mismatch: {project_status}")
                initial_asset_state = require_payload(
                    await session.call_tool("ue_get_asset_state", {"asset_path": ASSET_PATH}),
                    "ue_get_asset_state initial",
                )
                if initial_asset_state.get("state") != "synchronized":
                    raise RuntimeError(f"Initial four-source asset state is not synchronized: {initial_asset_state}")

                search = require_payload(
                    await session.call_tool(
                        "ue_search",
                        {"query": "DA_ScalarPatchTarget", "scope": "assets"},
                    ),
                    "ue_search",
                )
                if search["pagination"]["resultCount"] != 1:
                    raise RuntimeError(f"Scalar fixture search failed: {search}")

                prepared = require_payload(
                    await session.call_tool(
                        "ue_set_asset_property",
                        {
                            "asset_path": ASSET_PATH,
                            "property_path": "BoolValue",
                            "value": True,
                            "mode": "DryRun",
                            "description": "UE Agent Kit 0.8.0 high-level MCP workflow smoke test.",
                        },
                    ),
                    "ue_set_asset_property DryRun",
                )
                if prepared["mode"] != "DryRun" or prepared["underlyingOperation"] != "setAssetProperty":
                    raise RuntimeError(f"High-level operation mapping failed: {prepared}")
                if not all(prepared["gates"].values()):
                    raise RuntimeError(f"High-level Dry Run gates failed: {prepared}")
                if sha256(args.package_file) != package_before_hash:
                    raise RuntimeError("High-level MCP Dry Run changed the scalar fixture package")
                plan_id = str(prepared["planId"])
                dry_receipt = str(prepared["dryRunReceipt"])

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
                if applied["indexFreshness"]["state"] != "stale":
                    raise RuntimeError(f"Commit did not mark the fixed index stale: {applied}")
                stale_status = require_payload(
                    await session.call_tool("ue_get_project_status", {}),
                    "ue_get_project_status after Commit",
                )
                if stale_status["freshness"]["state"] != "stale":
                    raise RuntimeError(f"Project status did not report stale after Commit: {stale_status}")

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
                if verified["indexFreshness"]["state"] != "stale":
                    raise RuntimeError(f"Independent Verify incorrectly cleared stale state: {verified}")
                evidence = verified.get("memoryTaskEvidence")
                if not isinstance(evidence, dict) or evidence.get("tool") != "ue_memory_record_task":
                    raise RuntimeError(f"Workflow Memory evidence handoff is missing: {verified}")
                evidence_arguments = evidence.get("arguments")
                if not isinstance(evidence_arguments, dict):
                    raise RuntimeError(f"Workflow Memory evidence arguments are missing: {evidence}")
                if evidence_arguments.get("patch_ref") != f"patch:{applied['patchDigest']}":
                    raise RuntimeError(f"Workflow Patch evidence mismatch: {evidence}")
                if evidence_arguments.get("backup_manifest_ref") != f"backup-manifest:{applied['manifestId']}":
                    raise RuntimeError(f"Workflow Backup Manifest evidence mismatch: {evidence}")
                if evidence_arguments.get("validation_evidence_ref") != f"validation-evidence:{verified['reportId']}":
                    raise RuntimeError(f"Workflow Validation evidence mismatch: {evidence}")
                if evidence_arguments.get("revision_set") != [
                    {
                        "assetPath": ASSET_PATH,
                        "revision": applied["afterRevision"],
                        "revisionStable": True,
                    }
                ]:
                    raise RuntimeError(f"Workflow Revision evidence mismatch: {evidence}")
                evidence_json = json.dumps(evidence, ensure_ascii=False)
                if apply_receipt in evidence_json:
                    raise RuntimeError("Workflow Memory evidence exposed the one-time Apply Receipt")
                if str(args.work_root) in evidence_json or str(args.backup_root) in evidence_json:
                    raise RuntimeError("Workflow Memory evidence exposed a configured local path")
                commit_memory = require_payload(
                    await session.call_tool(evidence["tool"], evidence_arguments),
                    "ue_memory_record_task after Verify",
                )
                commit_memory_record = commit_memory["record"]
                if commit_memory_record["status"] != "valid":
                    raise RuntimeError(f"Verified Commit Task Record is not valid: {commit_memory}")
                if commit_memory_record["details"]["taskOutcome"] != "succeeded":
                    raise RuntimeError(f"Verified Commit Task outcome is invalid: {commit_memory}")
                commit_memory_record_id = str(commit_memory_record["recordId"])

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
                if restored["indexFreshness"]["state"] != "fresh":
                    raise RuntimeError(f"Rollback did not restore fresh index state: {restored}")
                rollback_evidence = restored.get("memoryTaskEvidence")
                if not isinstance(rollback_evidence, dict) or rollback_evidence.get("tool") != "ue_memory_record_task":
                    raise RuntimeError(f"Rollback Memory evidence handoff is missing: {restored}")
                rollback_arguments = rollback_evidence.get("arguments")
                if not isinstance(rollback_arguments, dict):
                    raise RuntimeError(f"Rollback Memory evidence arguments are missing: {rollback_evidence}")
                if rollback_arguments.get("outcome") != "rolledBack":
                    raise RuntimeError(f"Rollback Memory outcome is invalid: {rollback_evidence}")
                if rollback_arguments.get("patch_ref") != f"patch:{applied['patchDigest']}":
                    raise RuntimeError(f"Rollback Patch evidence mismatch: {rollback_evidence}")
                if rollback_arguments.get("backup_manifest_ref") != f"backup-manifest:{applied['manifestId']}":
                    raise RuntimeError(f"Rollback Backup Manifest evidence mismatch: {rollback_evidence}")
                if rollback_arguments.get("validation_evidence_ref") != f"validation-evidence:{restored['verificationReportId']}":
                    raise RuntimeError(f"Rollback Validation evidence mismatch: {rollback_evidence}")
                if rollback_arguments.get("revision_set") != [
                    {
                        "assetPath": ASSET_PATH,
                        "revision": applied["beforeRevision"],
                        "revisionStable": True,
                    }
                ]:
                    raise RuntimeError(f"Rollback Revision evidence mismatch: {rollback_evidence}")
                rollback_evidence_json = json.dumps(rollback_evidence, ensure_ascii=False)
                if apply_receipt in rollback_evidence_json:
                    raise RuntimeError("Rollback Memory evidence exposed the one-time Apply Receipt")
                if str(args.work_root) in rollback_evidence_json or str(args.backup_root) in rollback_evidence_json:
                    raise RuntimeError("Rollback Memory evidence exposed a configured local path")
                rollback_memory = require_payload(
                    await session.call_tool(rollback_evidence["tool"], rollback_arguments),
                    "ue_memory_record_task after Rollback",
                )
                rollback_memory_record = rollback_memory["record"]
                if rollback_memory_record["status"] != "valid":
                    raise RuntimeError(f"Rollback Task Record is not valid: {rollback_memory}")
                if rollback_memory_record["details"]["taskOutcome"] != "rolledBack":
                    raise RuntimeError(f"Rollback Task outcome is invalid: {rollback_memory}")
                rollback_memory_record_id = str(rollback_memory_record["recordId"])

                memory_validation = require_payload(
                    await session.call_tool("ue_memory_validate", {}),
                    "ue_memory_validate after Rollback",
                )
                if memory_validation["staleRecordIds"] != [commit_memory_record_id]:
                    raise RuntimeError(f"Commit Task was not invalidated after Rollback: {memory_validation}")
                if rollback_memory_record_id not in memory_validation["checkedRecordIds"]:
                    raise RuntimeError(f"Rollback Task Revision was not checked: {memory_validation}")
                commit_memory_after = require_payload(
                    await session.call_tool(
                        "ue_memory_get",
                        {"record_id": commit_memory_record_id},
                    ),
                    "ue_memory_get Commit Task after Rollback",
                )
                rollback_memory_after = require_payload(
                    await session.call_tool(
                        "ue_memory_get",
                        {"record_id": rollback_memory_record_id},
                    ),
                    "ue_memory_get Rollback Task after Rollback",
                )
                if commit_memory_after["record"]["status"] != "stale":
                    raise RuntimeError(f"Commit Task did not become stale: {commit_memory_after}")
                if rollback_memory_after["record"]["status"] != "valid":
                    raise RuntimeError(f"Rollback Task did not remain valid: {rollback_memory_after}")

                restored_status = require_payload(
                    await session.call_tool("ue_get_project_status", {}),
                    "ue_get_project_status after Rollback",
                )
                if restored_status["freshness"]["state"] != "fresh":
                    raise RuntimeError(f"Project status did not return to fresh after Rollback: {restored_status}")

    memory_service = ProjectMemoryService(
        database_path=args.memory_database,
        project_key=args.project.stem,
    )
    memory_audit = build_memory_audit_report(memory_service)
    if memory_audit["recordCount"] != 2 or memory_audit["statusEventCount"] != 3:
        raise RuntimeError(f"Workflow Memory audit counts are invalid: {memory_audit}")
    if memory_audit["countsByStatus"] != {"stale": 1, "valid": 1}:
        raise RuntimeError(f"Workflow Memory audit states are invalid: {memory_audit}")
    if not memory_audit["integrity"]["allRecordDigestsVerified"]:
        raise RuntimeError(f"Workflow Memory audit digest verification failed: {memory_audit}")

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
        "initialIndexFresh": True,
        "initialAssetStateSynchronized": initial_asset_state["state"] == "synchronized",
        "commitMarkedIndexStale": True,
        "verifyPreservedIndexStale": True,
        "rollbackRestoredIndexFresh": True,
        "engineVersion": project_status["engine"]["version"],
        "planId": plan_id,
        "highLevelDryRun": True,
        "dryRunReceiptIssued": True,
        "invalidCommitConfirmationRejected": True,
        "commitReceiptSingleUse": True,
        "applyReceiptIssued": True,
        "committedRevision": applied["afterRevision"],
        "independentCommitVerification": True,
        "memoryTaskEvidenceVerified": True,
        "rollbackMemoryTaskEvidenceVerified": True,
        "commitTaskRecordPersisted": True,
        "rollbackTaskRecordPersisted": True,
        "commitTaskInvalidatedAfterRollback": True,
        "rollbackTaskRemainedValid": True,
        "memoryAuditDigestVerified": True,
        "memoryAuditSnapshotSha256": memory_audit["integrity"]["snapshotSha256"],
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
    parser.add_argument("--memory-database", type=Path, required=True)
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

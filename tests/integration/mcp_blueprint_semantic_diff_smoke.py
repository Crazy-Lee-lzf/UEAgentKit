from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402

ASSET_PATH = (
    "/Game/UEAgentKitWriteTests/Transactions/"
    "BP_TransactionBlueprint.BP_TransactionBlueprint"
)
EXPECTED_TOOLS = tool_names_for_mode(workflow_enabled=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict) or not value.get("ok"):
        raise RuntimeError(f"{tool} failed: {value}")
    return value


async def call(
    session: ClientSession, tool: str, params: dict[str, Any]
) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


async def semantic_diff(
    session: ClientSession, change_set_id: str, stage: str
) -> dict[str, Any]:
    started = time.perf_counter()
    result = await call(
        session,
        "ue_analyze_semantic_diff",
        {
            "change_set_id": change_set_id,
            "stage": stage,
            "asset_paths": [ASSET_PATH],
            "include_unchanged": True,
            "max_changes": 64,
            "max_output_tokens": 4096,
        },
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    summary = result["summary"]
    asset = result["assets"][0]
    budget = result["outputBudget"]
    if (
        result["evidenceStage"]["selected"] != stage
        or summary["expectedCount"] != 1
        or summary["actualCount"] != 1
        or summary["matchedCount"] != 1
        or summary["unexpectedCount"] != 0
        or summary["missingExpectedCount"] != 0
        or asset["assetPath"] != ASSET_PATH
        or int(budget["estimatedTokens"]) <= 0
    ):
        raise RuntimeError(f"Blueprint Semantic Diff {stage} failed: {result}")
    return {
        "stage": stage,
        "elapsedMs": elapsed_ms,
        "beforeRevision": asset.get("beforeRevision", ""),
        "afterRevision": asset.get("afterRevision", ""),
        "stageEvidenceRevision": asset.get("stageEvidenceRevision", ""),
        "expectedCount": summary["expectedCount"],
        "actualCount": summary["actualCount"],
        "matchedCount": summary["matchedCount"],
        "unexpectedCount": summary["unexpectedCount"],
        "missingExpectedCount": summary["missingExpectedCount"],
        "unchangedCriticalCount": summary["unchangedCriticalCount"],
        "analysisGapCount": summary["analysisGapCount"],
        "estimatedTokens": budget["estimatedTokens"],
        "truncated": budget["truncated"],
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    package_hash_before = sha256(args.package_file)
    revision_hash_before = {
        path.relative_to(args.revision_export).as_posix(): sha256(path)
        for path in sorted(args.revision_export.rglob("*.json"))
        if path.is_file()
    }
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
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (
            read_stream,
            write_stream,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                tool_names = [tool.name for tool in listed.tools]
                if tool_names != EXPECTED_TOOLS:
                    raise RuntimeError(f"Unexpected workflow Tool list: {tool_names}")

                change_set = await call(
                    session,
                    "ue_create_change_set",
                    {
                        "title": "Real UE5.6 Blueprint Semantic Diff",
                        "task_id": "task_r2-blueprint-semantic-diff",
                    },
                )
                change_set_id = str(change_set["changeSetId"])
                prepared = await call(
                    session,
                    "ue_set_blueprint_default",
                    {
                        "asset_path": ASSET_PATH,
                        "variable_name": "TransactionInt",
                        "value": 42,
                        "mode": "DryRun",
                        "description": "R2 real Blueprint narrow-write Semantic Diff smoke",
                    },
                )
                plan_id = str(prepared["planId"])
                applied = await call(
                    session,
                    "ue_apply_patch",
                    {
                        "plan_id": plan_id,
                        "dry_run_receipt": prepared["dryRunReceipt"],
                        "confirmation": f"COMMIT {plan_id}",
                        "change_set_id": change_set_id,
                    },
                )
                apply_receipt = str(applied["applyReceipt"])
                if sha256(args.package_file) == package_hash_before:
                    raise RuntimeError("Blueprint Commit did not change the package hash")
                persisted = await semantic_diff(session, change_set_id, "persisted")
                verified = await call(
                    session,
                    "ue_verify_asset",
                    {
                        "apply_receipt": apply_receipt,
                        "change_set_id": change_set_id,
                    },
                )
                if not verified.get("verified"):
                    raise RuntimeError(f"Blueprint independent Verify failed: {verified}")
                verified_diff = await semantic_diff(session, change_set_id, "verified")

                rollback_dry = await call(
                    session,
                    "ue_rollback_patch",
                    {"apply_receipt": apply_receipt, "mode": "DryRun"},
                )
                restored = await call(
                    session,
                    "ue_rollback_patch",
                    {
                        "apply_receipt": apply_receipt,
                        "mode": "Commit",
                        "rollback_dry_run_receipt": rollback_dry[
                            "rollbackDryRunReceipt"
                        ],
                        "confirmation": f"ROLLBACK {apply_receipt}",
                    },
                )
                if not restored.get("restored"):
                    raise RuntimeError(f"Blueprint rollback failed: {restored}")

    if sha256(args.package_file) != package_hash_before:
        raise RuntimeError("Blueprint package hash was not restored after rollback")
    revision_hash_after = {
        path.relative_to(args.revision_export).as_posix(): sha256(path)
        for path in sorted(args.revision_export.rglob("*.json"))
        if path.is_file()
    }
    if revision_hash_after != revision_hash_before:
        raise RuntimeError("Blueprint Smoke modified the frozen Revision Export")
    return {
        "protocolVersion": initialized.protocolVersion,
        "toolCount": len(tool_names),
        "assetPath": ASSET_PATH,
        "operation": "setVariableDefault",
        "semanticDiffs": [persisted, verified_diff],
        "packageHashRestored": True,
        "revisionExportUnchanged": True,
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
    parser.add_argument("--summary-report", type=Path, required=True)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
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

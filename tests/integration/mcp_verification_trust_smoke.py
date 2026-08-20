from __future__ import annotations

import argparse
import asyncio
import copy
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
from ue_agent_kit.verification_trust import (  # noqa: E402
    _record_applicability,
    _verdict,
)

S1_ASSET = "/Game/UEAgentKitWriteTests/ClosedLoop/DA_Scalar.DA_Scalar"
S2_ASSET = (
    "/Game/UEAgentKitWriteTests/Transactions/"
    "BP_TransactionBlueprint.BP_TransactionBlueprint"
)
AUTOMATION_TEST = "UEAgentKit.EditorBridge.LiveActionSmoke"
EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def directory_sha256(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(
        (item for item in root.rglob("*.json") if item.is_file()),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()


def payload(result: Any, tool: str, *, require_ok: bool = True) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict) or (require_ok and value.get("ok") is not True):
        raise RuntimeError(f"{tool} failed: {value}")
    return value


async def call(
    session: ClientSession,
    tool: str,
    params: dict[str, Any],
    *,
    require_ok: bool = True,
) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool, require_ok=require_ok)


def server_parameters(args: argparse.Namespace, prefix: str) -> StdioServerParameters:
    return StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(getattr(args, f"{prefix}_database")),
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
            str(getattr(args, f"{prefix}_policy")),
            "-RevisionExport",
            str(getattr(args, f"{prefix}_revision_export")),
            "-WorkRoot",
            str(getattr(args, f"{prefix}_work_root")),
            "-BackupRoot",
            str(getattr(args, f"{prefix}_backup_root")),
            "-ProcessTimeoutSeconds",
            "1800",
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )


def assertion_counts(assertions: list[dict[str, Any]]) -> dict[str, int]:
    keys = (
        "required",
        "recommended",
        "informational",
        "pass",
        "fail",
        "unknown",
        "not-applicable",
    )
    return {
        key: sum(
            item.get("requirement") == key or item.get("status") == key
            for item in assertions
        )
        for key in keys
    }


def compact_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "evidenceId": item.get("evidenceId", ""),
            "kind": item.get("kind", ""),
            "subject": item.get("subject", ""),
            "applicability": item.get("applicability", ""),
            "editorSessionId": item.get("editorSessionId", ""),
            "revision": item.get("revision", item.get("afterRevision", "")),
            "succeeded": item.get("succeeded"),
        }
        for item in evidence
    ]


async def timed_call(
    session: ClientSession,
    tool: str,
    params: dict[str, Any],
) -> tuple[dict[str, Any], float]:
    started = time.perf_counter()
    result = await call(session, tool, params)
    return result, round((time.perf_counter() - started) * 1000.0, 3)


def summarize_assessment(
    assessment: dict[str, Any],
    *,
    elapsed_ms: float,
) -> dict[str, Any]:
    assertions = [item for item in assessment.get("assertions", []) if isinstance(item, dict)]
    evidence = [item for item in assessment.get("evidence", []) if isinstance(item, dict)]
    budget = assessment.get("outputBudget", {})
    return {
        "planFingerprint": assessment.get("planFingerprint", ""),
        "assertionStatus": assertion_counts(assertions),
        "assertions": [
            {
                "assertionId": item.get("assertionId", ""),
                "kind": item.get("kind", ""),
                "subject": item.get("subject", ""),
                "requirement": item.get("requirement", ""),
                "status": item.get("status", ""),
                "applicability": item.get("applicability", ""),
                "reasonCode": item.get("reasonCode", ""),
            }
            for item in assertions
        ],
        "verdict": assessment.get("verdict", {}),
        "reasonCodes": assessment.get("verdict", {}).get("reasonCodes", []),
        "evidenceCount": len(evidence),
        "evidence": compact_evidence(evidence),
        "recommendedNextActions": assessment.get("recommendedNextActions", []),
        "elapsedMs": elapsed_ms,
        "estimatedTokens": budget.get("estimatedTokens", 0),
        "truncated": budget.get("truncated", False),
    }


def assert_plan(plan: dict[str, Any], change_set_id: str) -> None:
    if (
        plan.get("tool") != "ue_build_verification_plan"
        or plan.get("readOnly") is not True
        or plan.get("request", {}).get("changeSetId") != change_set_id
        or not str(plan.get("planFingerprint", "")).startswith("sha256:")
        or not plan.get("assertions")
        or int(plan.get("outputBudget", {}).get("estimatedTokens", 0)) <= 0
    ):
        raise RuntimeError(f"Verification Plan contract failed: {plan}")


def assert_verdict(
    assessment: dict[str, Any],
    expected: set[str],
    *,
    required_kinds: set[str] = frozenset(),
) -> None:
    state = str(assessment.get("verdict", {}).get("state", ""))
    assertions = assessment.get("assertions", [])
    kinds = {
        str(item.get("kind", ""))
        for item in assertions
        if isinstance(item, dict) and item.get("requirement") == "required"
    }
    if (
        assessment.get("tool") != "ue_evaluate_trust_verdict"
        or assessment.get("readOnly") is not True
        or state not in expected
        or not required_kinds.issubset(kinds)
        or int(assessment.get("outputBudget", {}).get("estimatedTokens", 0)) <= 0
    ):
        raise RuntimeError(f"Trust Verdict contract failed for {expected}: {assessment}")


async def create_change_set(session: ClientSession, suffix: str) -> str:
    result = await call(
        session,
        "ue_create_change_set",
        {
            "title": f"R3 Verification Trust Smoke {suffix}",
            "task_id": f"task_r3-verification-{suffix}",
        },
    )
    change_set_id = str(result.get("changeSetId", ""))
    if not change_set_id:
        raise RuntimeError(f"Change Set creation failed: {result}")
    return change_set_id


async def save_live_asset(
    session: ClientSession,
    asset_path: str,
    change_set_id: str,
) -> dict[str, Any]:
    preview = await call(
        session,
        "ue_save_authorized_asset",
        {"asset_path": asset_path, "mode": "Preview", "change_set_id": change_set_id},
    )
    save_receipt = str(preview.get("saveReceipt", ""))
    if not save_receipt:
        raise RuntimeError(f"Authorized Save Preview failed: {preview}")
    saved = await call(
        session,
        "ue_save_authorized_asset",
        {
            "asset_path": asset_path,
            "mode": "Commit",
            "save_receipt": save_receipt,
            "confirmation": f"SAVE {save_receipt}",
            "change_set_id": change_set_id,
        },
    )
    if saved.get("saved") is not True or not saved.get("liveApplyReceipt"):
        raise RuntimeError(f"Authorized Save Commit failed: {saved}")
    return saved


async def initialize_combined_session(
    session: ClientSession,
) -> tuple[Any, list[str], dict[str, Any]]:
    initialized = await session.initialize()
    listed = await session.list_tools()
    names = [tool.name for tool in listed.tools]
    if names != EXPECTED_TOOLS:
        raise RuntimeError(f"Unexpected combined Tool list: {names}")
    status = await call(session, "ue_editor_status", {})
    if status.get("result", {}).get("pieState") != "stopped":
        raise RuntimeError(f"Live Editor is not ready: {status}")
    return initialized, names, status


async def run_s1(
    args: argparse.Namespace,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    args.s1_error_log.parent.mkdir(parents=True, exist_ok=True)
    with args.s1_error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(server_parameters(args, "s1"), errlog=stderr) as streams:
            async with ClientSession(*streams) as session:
                initialized, tool_names, status = await initialize_combined_session(session)
                opened = await call(session, "ue_open_asset", {"asset_path": S1_ASSET})
                if opened.get("result", {}).get("openAfter") is not True:
                    raise RuntimeError(f"S1 asset did not open: {opened}")
                change_set_id = await create_change_set(session, "s1")
                planned = await call(
                    session,
                    "ue_plan_patch",
                    {
                        "asset_path": S1_ASSET,
                        "operation": "setAssetProperty",
                        "target": {"propertyPath": "IntValue"},
                        "value": 7,
                        "description": "R3 S1 clean trusted Data Asset write",
                    },
                )
                plan_id = str(planned["planId"])
                applied = await call(
                    session,
                    "ue_apply_asset_property_live",
                    {
                        "plan_id": plan_id,
                        "confirmation": f"LIVE APPLY {plan_id}",
                        "change_set_id": change_set_id,
                    },
                )
                if applied.get("changed") is not True:
                    raise RuntimeError(f"S1 Live Apply did not change the fixture: {applied}")
                saved = await save_live_asset(session, S1_ASSET, change_set_id)
                verified = await call(
                    session,
                    "ue_verify_live_write",
                    {"asset_path": S1_ASSET, "change_set_id": change_set_id},
                )
                if verified.get("verified") is not True:
                    raise RuntimeError(f"S1 independent Verify failed: {verified}")

                plan, plan_elapsed = await timed_call(
                    session,
                    "ue_build_verification_plan",
                    {
                        "change_set_id": change_set_id,
                        "required_automation_tests": [AUTOMATION_TEST],
                        "max_output_tokens": 8192,
                    },
                )
                assert_plan(plan, change_set_id)
                before, before_elapsed = await timed_call(
                    session,
                    "ue_evaluate_trust_verdict",
                    {
                        "change_set_id": change_set_id,
                        "required_automation_tests": [AUTOMATION_TEST],
                        "max_output_tokens": 8192,
                    },
                )
                assert_verdict(before, {"insufficient-evidence"})
                validation = await call(session, "ue_validate_asset", {"asset_path": S1_ASSET})
                automation = await call(
                    session,
                    "ue_run_automation_test",
                    {"test_name": AUTOMATION_TEST, "timeout_seconds": 120},
                )
                if automation.get("result", {}).get("successful") is not True:
                    raise RuntimeError(f"S1 exact Automation Test failed: {automation}")
                after, after_elapsed = await timed_call(
                    session,
                    "ue_evaluate_trust_verdict",
                    {
                        "change_set_id": change_set_id,
                        "required_automation_tests": [AUTOMATION_TEST],
                        "max_output_tokens": 8192,
                    },
                )
                assert_verdict(
                    after,
                    {"verified"},
                    required_kinds={"persistence", "semantic", "freshness", "data-validation", "automation"},
                )
                evidence_kinds = {
                    item.get("kind") for item in after.get("evidence", []) if isinstance(item, dict)
                }
                if not {"semantic-diff", "data-validation", "automation"}.issubset(evidence_kinds):
                    raise RuntimeError(f"S1 did not retain required Evidence kinds: {after}")

                failed_automation = await call(
                    session,
                    "ue_run_automation_test",
                    {"test_name": AUTOMATION_TEST, "timeout_seconds": 1},
                )
                failed_result = failed_automation.get("result", {})
                if failed_result.get("timedOut") is not True or failed_result.get("successful") is not False:
                    raise RuntimeError(f"S3 real UE controlled timeout did not fail: {failed_automation}")
                failed, failed_elapsed = await timed_call(
                    session,
                    "ue_evaluate_trust_verdict",
                    {
                        "change_set_id": change_set_id,
                        "required_automation_tests": [AUTOMATION_TEST],
                        "max_output_tokens": 8192,
                    },
                )
                assert_verdict(failed, {"failed"}, required_kinds={"automation"})
                automation_assertion = next(
                    item for item in failed["assertions"] if item.get("kind") == "automation"
                )
                if automation_assertion.get("status") != "fail":
                    raise RuntimeError(f"S3 real UE failure did not close as Required FAIL: {failed}")
                return (
                    {
                        "case": "S1-clean-success",
                        "source": "real-ue5.6-directhost",
                        "changeSetId": change_set_id,
                        "planFingerprint": plan["planFingerprint"],
                        "planElapsedMs": plan_elapsed,
                        "planEstimatedTokens": plan["outputBudget"]["estimatedTokens"],
                        "beforeEvidence": summarize_assessment(before, elapsed_ms=before_elapsed),
                        "afterEvidence": summarize_assessment(after, elapsed_ms=after_elapsed),
                        "validationResult": validation.get("result", {}).get("result", ""),
                        "automationResult": automation.get("result", {}).get("state", ""),
                        "editorSessionId": status.get("result", {}).get("sessionId", ""),
                        "protocolVersion": initialized.protocolVersion,
                        "toolCount": len(tool_names),
                        "recovery": {
                            "method": "final-fixture-reset",
                            "saveReceipt": saved.get("saveReceipt", ""),
                            "requested": True,
                        },
                    },
                    copy.deepcopy(after),
                    {
                        "case": "S3-real-ue-deterministic-failure",
                        "source": "real-ue5.6-isolated-automation-timeout",
                        "changeSetId": change_set_id,
                        "planFingerprint": failed.get("planFingerprint", ""),
                        **summarize_assessment(failed, elapsed_ms=failed_elapsed),
                        "automationResult": {
                            "state": failed_result.get("state", ""),
                            "successful": failed_result.get("successful"),
                            "timedOut": failed_result.get("timedOut"),
                            "isolatedProcess": failed_result.get("isolatedProcess"),
                        },
                        "recovery": {"method": "final-fixture-reset", "requested": True},
                    },
                )
    raise RuntimeError("S1 MCP session exited without a result")


async def run_s2(args: argparse.Namespace) -> dict[str, Any]:
    package_hash_before = sha256(args.s2_package_file)
    revision_hash_before = directory_sha256(args.s2_revision_export)
    args.s2_error_log.parent.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] = {}
    with args.s2_error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(server_parameters(args, "s2"), errlog=stderr) as streams:
            async with ClientSession(*streams) as session:
                initialized, tool_names, status = await initialize_combined_session(session)
                change_set_id = await create_change_set(session, "s2")
                prepared = await call(
                    session,
                    "ue_set_blueprint_default",
                    {
                        "asset_path": S2_ASSET,
                        "variable_name": "TransactionInt",
                        "value": 42,
                        "mode": "DryRun",
                        "description": "R3 S2 Blueprint evidence progression",
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
                verified = await call(
                    session,
                    "ue_verify_asset",
                    {"apply_receipt": apply_receipt, "change_set_id": change_set_id},
                )
                if verified.get("verified") is not True:
                    raise RuntimeError(f"S2 independent Verify failed: {verified}")
                plan, plan_elapsed = await timed_call(
                    session,
                    "ue_build_verification_plan",
                    {"change_set_id": change_set_id, "max_output_tokens": 8192},
                )
                assert_plan(plan, change_set_id)
                before, before_elapsed = await timed_call(
                    session,
                    "ue_evaluate_trust_verdict",
                    {"change_set_id": change_set_id, "max_output_tokens": 8192},
                )
                assert_verdict(before, {"insufficient-evidence"}, required_kinds={"compile"})
                compile_result = await call(
                    session,
                    "ue_compile_blueprint",
                    {"asset_path": S2_ASSET},
                )
                if compile_result.get("result", {}).get("compiled") is not True:
                    raise RuntimeError(f"S2 Blueprint compile did not execute: {compile_result}")
                validation = await call(session, "ue_validate_asset", {"asset_path": S2_ASSET})
                after, after_elapsed = await timed_call(
                    session,
                    "ue_evaluate_trust_verdict",
                    {"change_set_id": change_set_id, "max_output_tokens": 8192},
                )
                assert_verdict(
                    after,
                    {"verified", "suspicious"},
                    required_kinds={"compile", "data-validation", "semantic", "persistence", "freshness"},
                )
                compile_assertion = next(
                    item for item in after["assertions"] if item.get("kind") == "compile"
                )
                if compile_assertion.get("status") != "pass":
                    raise RuntimeError(f"S2 compile Evidence did not close the assertion: {after}")

                result = {
                    "case": "S2-blueprint-evidence-progression",
                    "source": "real-ue5.6-directhost",
                    "changeSetId": change_set_id,
                    "planFingerprint": plan["planFingerprint"],
                    "planElapsedMs": plan_elapsed,
                    "planEstimatedTokens": plan["outputBudget"]["estimatedTokens"],
                    "beforeCompile": summarize_assessment(before, elapsed_ms=before_elapsed),
                    "afterCompile": summarize_assessment(after, elapsed_ms=after_elapsed),
                    "compileResult": compile_result.get("result", {}).get("result", ""),
                    "validationResult": validation.get("result", {}).get("result", ""),
                    "editorSessionId": status.get("result", {}).get("sessionId", ""),
                    "protocolVersion": initialized.protocolVersion,
                    "toolCount": len(tool_names),
                    "recovery": {
                        "method": "final-fixture-reset-after-editor-stop",
                        "requested": True,
                        "packageHashBefore": package_hash_before,
                    },
                }
    if directory_sha256(args.s2_revision_export) != revision_hash_before:
        raise RuntimeError("S2 modified the frozen Revision Export")

    return result


def service_cases(real_assessment: dict[str, Any]) -> list[dict[str, Any]]:
    real_evidence = [
        item for item in real_assessment.get("evidence", []) if isinstance(item, dict)
    ]
    validation = next(
        (item for item in real_evidence if item.get("kind") == "data-validation"),
        None,
    )
    if validation is None:
        raise RuntimeError("S5 requires the real S1 captured Data Validation Evidence")

    passed_required = [
        {
            "assertionId": "controlled-required-pass",
            "kind": "semantic",
            "subject": S1_ASSET,
            "requirement": "required",
            "status": "pass",
            "applicability": "exact-change-set",
            "reasonCode": "semantic-expected-actual-matched",
        }
    ]
    high_fanout = {
        "code": "high-fanout-target",
        "severity": "medium",
        "blocking": False,
        "subject": S1_ASSET,
        "message": "Controlled R1 service fixture has bounded high fanout.",
    }
    suspicious_state, suspicious_reasons, suspicious_statement = _verdict(
        passed_required,
        [high_fanout],
    )
    if suspicious_state != "suspicious":
        raise RuntimeError("S4 non-blocking high fanout did not produce suspicious")

    actual_session = str(validation.get("editorSessionId", ""))
    revision_set = validation.get("revisionSet", [])
    actual_revision = ""
    if isinstance(revision_set, list):
        match = next(
            (
                item for item in revision_set
                if isinstance(item, dict) and item.get("assetPath") == S1_ASSET
            ),
            {},
        )
        actual_revision = str(match.get("revisionAfter") or match.get("revision") or "")
    wrong_session = _record_applicability(
        validation,
        subject=S1_ASSET,
        expected_session="wrong-session-r3",
        expected_revision=actual_revision,
    )
    wrong_revision = _record_applicability(
        validation,
        subject=S1_ASSET,
        expected_session=actual_session,
        expected_revision="sha256:" + "f" * 64,
    )
    if wrong_session[2] != "trust-evidence-session-mismatch":
        raise RuntimeError(f"S5 wrong session was accepted: {wrong_session}")
    if wrong_revision[2] != "trust-evidence-revision-mismatch":
        raise RuntimeError(f"S5 wrong revision was accepted: {wrong_revision}")
    stale_assertions = [
        {
            "assertionId": f"controlled-{reason}",
            "kind": "data-validation",
            "subject": S1_ASSET,
            "requirement": "required",
            "status": "unknown",
            "applicability": applicability,
            "reasonCode": reason,
        }
        for _, applicability, reason in (wrong_session, wrong_revision)
    ]
    stale_state, stale_reasons, stale_statement = _verdict(stale_assertions, [])
    if stale_state != "insufficient-evidence":
        raise RuntimeError("S5 invalid applicability did not produce insufficient-evidence")

    return [
        {
            "case": "S4-non-blocking-high-fanout",
            "source": "controlled-r1-service-level-fixture",
            "planFingerprint": real_assessment.get("planFingerprint", ""),
            "assertionStatus": assertion_counts(passed_required),
            "verdict": {
                "state": suspicious_state,
                "reasonCodes": suspicious_reasons,
                "statement": suspicious_statement,
            },
            "reasonCodes": suspicious_reasons,
            "evidenceCount": 1,
            "evidence": [{
                "evidenceId": "controlled-high-fanout-impact",
                "kind": "reference-impact",
                "subject": S1_ASSET,
                "applicability": "exact-change-set",
            }],
            "recommendedNextActions": [{
                "tool": "ue_analyze_change_impact",
                "arguments": {"target_asset_paths": [S1_ASSET], "max_depth": 2},
                "reason": "Review bounded high-fanout consumers.",
            }],
            "elapsedMs": 0.0,
            "estimatedTokens": 0,
            "recovery": {"required": False, "reason": "read-only service fixture"},
        },
        {
            "case": "S5-session-and-revision-mismatch",
            "source": "real-s1-evidence-with-controlled-applicability-mismatch",
            "planFingerprint": real_assessment.get("planFingerprint", ""),
            "assertionStatus": assertion_counts(stale_assertions),
            "verdict": {"state": stale_state, "reasonCodes": stale_reasons, "statement": stale_statement},
            "reasonCodes": stale_reasons,
            "evidenceCount": 1,
            "evidence": compact_evidence([validation]),
            "applicabilityChecks": {
                "wrongSession": {
                    "applies": wrong_session[0],
                    "applicability": wrong_session[1],
                    "reasonCode": wrong_session[2],
                },
                "wrongRevision": {
                    "applies": wrong_revision[0],
                    "applicability": wrong_revision[1],
                    "reasonCode": wrong_revision[2],
                },
            },
            "recommendedNextActions": [{
                "tool": "ue_validate_asset",
                "arguments": {"asset_path": S1_ASSET},
                "reason": "Capture validation in the current session for the exact final revision.",
            }],
            "elapsedMs": 0.0,
            "estimatedTokens": 0,
            "recovery": {"required": False, "reason": "read-only applicability check"},
        },
    ]


async def run(args: argparse.Namespace) -> dict[str, Any]:
    s1, real_assessment, s3 = await run_s1(args)
    s2 = await run_s2(args)
    s4, s5 = service_cases(real_assessment)
    states = [
        s1["afterEvidence"]["verdict"]["state"],
        s2["beforeCompile"]["verdict"]["state"],
        s3["verdict"]["state"],
        s4["verdict"]["state"],
        s5["verdict"]["state"],
    ]
    return {
        "schemaVersion": "1.0",
        "title": "R3 Verification Plan and Trust Verdict S1-S5 Smoke",
        "engineSource": "real UE5.6 DirectHost for S1/S2/S3; controlled service fixture for S4; real S1 Evidence mismatch for S5",
        "cases": [s1, s2, s3, s4, s5],
        "verdictStatesObserved": sorted(set(states)),
        "recovery": {
            "clientCompleted": True,
            "finalFixtureResetPending": True,
        },
    }


def add_path(parser: argparse.ArgumentParser, name: str) -> None:
    parser.add_argument(f"--{name.replace('_', '-')}", type=Path, required=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    add_path(parser, "engine_root")
    add_path(parser, "project")
    for prefix in ("s1", "s2"):
        for suffix in (
            "database",
            "policy",
            "revision_export",
            "work_root",
            "backup_root",
            "error_log",
        ):
            add_path(parser, f"{prefix}_{suffix}")
    add_path(parser, "s2_package_file")
    add_path(parser, "session_marker")
    add_path(parser, "summary_report")
    args = parser.parse_args()
    args.session_marker.parent.mkdir(parents=True, exist_ok=True)
    args.session_marker.write_text("client-started\n", encoding="utf-8")
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

"""W5 benchmark runner.

The runner can execute resident W4 workflows against a real DirectHost
project through the product services, or run pure offline summary/validation
commands for tests and report generation.

Real UE execution requires the same service wiring used by the W4/D1
acceptance harnesses. The runner intentionally keeps no UE dependency in
the module top-level so `tests/python/test_w5_benchmark.py` can import it.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from benchmarks.w5.metrics import noise_groups, stage_contribution, summarize_attempts  # noqa: E402
from benchmarks.w5.workloads import W5Workload, cold_commandlet_specs, scenario_for_id  # noqa: E402


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def require(condition: bool, message: Any) -> None:
    if not condition:
        raise RuntimeError(message)


def build_services(
    args: argparse.Namespace,
) -> tuple[Any, Any, Any, Any]:
    """Build W4 services against the real DirectHost project.

    Imported lazily so offline commands do not require the MCP/server stack.
    """
    from ue_agent_kit.agent_api import IndexQueryService
    from ue_agent_kit.agent_workflow import PatchWorkflowConfig, PatchWorkflowService
    from ue_agent_kit.batch_recovery import BatchRecoveryService
    from ue_agent_kit.bounded_batch import BoundedBatchService
    from ue_agent_kit.checkpoint_sets import CheckpointSetService
    from ue_agent_kit.editor_bridge import LiveEditorBridgeConfig, LiveEditorBridgeService
    from ue_agent_kit.mcp_server import __version__
    from ue_agent_kit.snapshot_lifecycle import freeze_active_snapshot, resolve_active_snapshot

    workflow_config = PatchWorkflowConfig(
        tool_root=TOOL_ROOT,
        engine_root=args.engine_root,
        project_path=args.project,
        policy_path=args.policy,
        revision_export=args.revision_export,
        work_root=args.work_root,
        backup_root=args.backup_root,
        commit_enabled=True,
    )
    live_editor_service = LiveEditorBridgeService(
        LiveEditorBridgeConfig(
            project_path=args.project,
            timeout_seconds=30.0,
            policy_path=args.policy,
        ),
        server_version=__version__,
    )
    active_snapshot = resolve_active_snapshot(
        args.database,
        workflow_config.revision_export,
        workflow_config.work_root,
        workflow_config.project_path.stem,
    )
    validation_index = IndexQueryService(active_snapshot.database)
    validation_config = replace(
        workflow_config,
        revision_export=active_snapshot.revision_export,
        active_snapshot=active_snapshot,
    )
    PatchWorkflowService(validation_index, validation_config, live_editor_service=live_editor_service)
    frozen_snapshot = freeze_active_snapshot(active_snapshot)
    final_config = replace(
        workflow_config,
        revision_export=frozen_snapshot.revision_export,
        active_snapshot=active_snapshot,
    )
    index_service = IndexQueryService(frozen_snapshot.database)
    workflow = PatchWorkflowService(index_service, final_config, live_editor_service=live_editor_service)
    bounded = BoundedBatchService(workflow)
    checkpoint_sets = CheckpointSetService(workflow, bounded)
    recovery = BatchRecoveryService(workflow, bounded, checkpoint_sets)
    return workflow, bounded, checkpoint_sets, recovery


def _elapsed(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _stages_apply(
    workflow: Any,
    bounded: Any,
    checkpoint_sets: Any,
    workload: W5Workload,
    *,
    title_prefix: str,
    task_prefix: str,
    before_revisions: dict[str, str],
) -> dict[str, Any]:
    """Run one full resident scenario (all batches) and record stage timings."""
    stages: dict[str, float | None] = {
        "planMs": 0.0,
        "policyRevisionMs": None,
        "applyMs": 0.0,
        "fastVerifyMs": 0.0,
        "compileMs": None,
        "checkpointPreviewMs": 0.0,
        "saveMs": 0.0,
        "strongVerifyMs": 0.0,
        "semanticDiffMs": 0.0,
        "validationMs": 0.0,
        "trustMs": 0.0,
        "totalMs": 0.0,
    }

    change_set_count = 0
    checkpoint_set_count = 0
    public_mcp_call_count = 0
    resident_bridge_call_count = 0
    child_unreal_process_count = 0
    result_bytes = 0
    change_set_ids: list[str] = []
    checkpoint_set_ids: list[str] = []
    after_revisions: dict[str, str] = {}

    for batch in workload.batches:
        change_set_id = ""
        started = time.perf_counter()
        change_set = workflow.create_change_set(
            title=f"{title_prefix} batch {batch.batch_index}",
            task_id=f"{task_prefix}_b{batch.batch_index}",
        )
        change_set_id = str(change_set["changeSetId"])
        change_set_ids.append(change_set_id)
        change_set_count += 1
        public_mcp_call_count += 1

        plan_started = time.perf_counter()
        plan = bounded.plan(
            assets=[
                {"assetPath": group.asset_path, "operations": list(group.operations)}
                for group in batch.assets
            ],
            description=f"{title_prefix} batch {batch.batch_index}",
        )
        stages["planMs"] = (stages["planMs"] or 0.0) + _elapsed(plan_started)
        public_mcp_call_count += 1

        apply_started = time.perf_counter()
        applied = bounded.apply_live_write_batch(
            batch_plan_id=plan["batchPlanId"],
            confirmation=f"APPLY LIVE WRITE BATCH {plan['batchPlanId']}",
            change_set_id=change_set_id,
        )
        require(applied["state"] == "applied", applied)
        require(applied["savePerformed"] is False, applied)
        stages["applyMs"] = (stages["applyMs"] or 0.0) + _elapsed(apply_started)
        public_mcp_call_count += 1
        resident_bridge_call_count += len(applied.get("operations") or [])
        fast_verified = sum(1 for op in applied.get("operations") or [] if op.get("fastVerified") is True)
        stages["fastVerifyMs"] = (stages["fastVerifyMs"] or 0.0) + 0.0
        # Fast Verify wall time is inside apply in the product path; keep the
        # per-op fastVerify count as evidence and leave fastVerifyMs as 0 unless
        # the product exposes a separate timing value.
        if fast_verified != len(applied.get("operations") or []):
            raise RuntimeError(f"fast verify count mismatch: {applied}")

        preview_started = time.perf_counter()
        preview = checkpoint_sets.preview(batch_execution_id=applied["batchExecutionId"])
        require(preview["state"] == "checkpoint_prepared", preview)
        stages["checkpointPreviewMs"] = (stages["checkpointPreviewMs"] or 0.0) + _elapsed(preview_started)
        public_mcp_call_count += 1

        save_started = time.perf_counter()
        committed = checkpoint_sets.commit(
            checkpoint_set_id=preview["checkpointSetId"],
            confirmation=preview["confirmationRequired"],
        )
        require(committed["state"] == "saved", committed)
        stages["saveMs"] = (stages["saveMs"] or 0.0) + _elapsed(save_started)
        checkpoint_set_ids.append(preview["checkpointSetId"])
        checkpoint_set_count += 1
        public_mcp_call_count += 1
        child_unreal_process_count += 0  # checkpoint save starts no child UE
        for asset in committed.get("assets") or []:
            after_revisions[str(asset["assetPath"])] = str(asset.get("afterRevision") or "")

        verify_started = time.perf_counter()
        verified = checkpoint_sets.verify(checkpoint_set_id=preview["checkpointSetId"])
        require(verified["state"] == "verified", verified)
        stages["strongVerifyMs"] = (stages["strongVerifyMs"] or 0.0) + _elapsed(verify_started)
        child_unreal_process_count += int(verified.get("strongVerifyProcessCount") or 0)
        public_mcp_call_count += 1

        semantic_started = time.perf_counter()
        semantic = workflow.analyze_semantic_diff(change_set_id, stage="verified")
        require(semantic.get("ok") is True, semantic)
        stages["semanticDiffMs"] = (stages["semanticDiffMs"] or 0.0) + _elapsed(semantic_started)
        public_mcp_call_count += 1

        validation_started = time.perf_counter()
        plan_doc = workflow.build_verification_plan(change_set_id)
        require(plan_doc.get("ok") is True, plan_doc)
        stages["validationMs"] = (stages["validationMs"] or 0.0) + _elapsed(validation_started)
        public_mcp_call_count += 1

        trust_started = time.perf_counter()
        trust = workflow.evaluate_trust_verdict(change_set_id)
        require(trust.get("ok") is True, trust)
        require(trust.get("verdict", {}).get("state") == "verified", trust)
        stages["trustMs"] = (stages["trustMs"] or 0.0) + _elapsed(trust_started)
        public_mcp_call_count += 1

        # resultBytes: best available public-ish serialized size.
        result_bytes += len(json.dumps(trust, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        total_elapsed = _elapsed(started)
        stages["totalMs"] = (stages.get("totalMs") or 0.0) + total_elapsed

    return {
        "stages": stages,
        "changeSetCount": change_set_count,
        "checkpointSetCount": checkpoint_set_count,
        "publicMcpCallCount": public_mcp_call_count,
        "residentBridgeCallCount": resident_bridge_call_count,
        "childUnrealProcessCount": child_unreal_process_count,
        "resultBytes": result_bytes,
        "batchCount": workload.batch_count,
        "operationsPerBatch": workload.operations_per_batch,
        "changeSetIds": change_set_ids,
        "checkpointSetIds": checkpoint_set_ids,
        "finalTrustState": "verified",
        "afterRevisions": after_revisions,
    }


def run_resident(args: argparse.Namespace) -> dict[str, Any]:
    workload = scenario_for_id(args.scenario)
    workflow, bounded, checkpoint_sets, _ = build_services(args)
    before_revisions: dict[str, str] = {}
    # Open assets first if WarmLoaded.
    if args.cache_state == "WarmLoaded":
        for asset_path in workload.asset_paths:
            workflow.live_editor_service.call_tool("ue_open_asset", {"assetPath": asset_path})
    started = time.perf_counter()
    result = _stages_apply(
        workflow,
        bounded,
        checkpoint_sets,
        workload,
        title_prefix=f"W5 {args.scenario} sample {args.sample_index}",
        task_prefix=f"task_w5_{args.scenario.lower()}",
        before_revisions=before_revisions,
    )
    total_ms = _elapsed(started)
    attempt = {
        "schemaVersion": "1.0",
        "runId": args.run_id,
        "scenarioId": args.scenario,
        "sampleIndex": args.sample_index,
        "cacheState": args.cache_state,
        "projectPath": str(args.project),
        "projectScale": "DirectHost",
        "storageProfile": "NativeSSD",
        "assetPaths": workload.asset_paths,
        "logicalOperationCount": workload.logical_operation_count,
        "batchCount": result["batchCount"],
        "operationsPerBatch": result["operationsPerBatch"],
        "changeSetCount": result["changeSetCount"],
        "checkpointSetCount": result["checkpointSetCount"],
        "publicMcpCallCount": result["publicMcpCallCount"],
        "residentBridgeCallCount": result["residentBridgeCallCount"],
        "childUnrealProcessCount": result["childUnrealProcessCount"],
        "resultBytes": result["resultBytes"],
        "stages": result["stages"],
        "totalMs": total_ms,
        "success": True,
        "errorCode": None,
        "finalTrustState": result["finalTrustState"],
        "beforeRevisions": before_revisions,
        "afterRevisions": result["afterRevisions"],
        "measurementGaps": ["policyRevisionMs", "compileMs", "fastVerifyMs"],
    }
    return attempt


def run_cold_pair(args: argparse.Namespace) -> dict[str, Any]:
    """Run one cold commandlet pair attempt for a scenario.

    This command is a thin wrapper around RunPatch.ps1 for a single asset group.
    It writes per-launch evidence and returns a cold attempt object.
    """
    import subprocess

    workload = scenario_for_id(args.scenario)
    specs = cold_commandlet_specs(workload)
    if args.launch_index < 0 or args.launch_index >= len(specs):
        raise ValueError("launch_index out of range")
    spec = specs[args.launch_index]
    patch_path = args.patch_path
    policy_path = args.policy
    revision_export = args.revision_export
    report_path = args.output_dir / f"cold-{args.scenario}-{args.launch_index}-report.json"
    started = time.perf_counter()
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunPatch.ps1"),
            "-EngineRoot",
            str(args.engine_root),
            "-ProjectPath",
            str(args.project),
            "-Patch",
            str(patch_path),
            "-Policy",
            str(policy_path),
            "-RevisionExport",
            str(revision_export),
            "-Mode",
            "Commit",
            "-Report",
            str(report_path),
        ],
        check=True,
        cwd=TOOL_ROOT,
    )
    total_ms = _elapsed(started)
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    return {
        "schemaVersion": "1.0",
        "runId": args.run_id,
        "scenarioId": args.scenario,
        "sampleIndex": args.sample_index,
        "launchIndex": args.launch_index,
        "coldCommandlet": spec["commandlet"],
        "assetPath": spec["assetPath"],
        "projectPath": str(args.project),
        "projectScale": "DirectHost",
        "storageProfile": "NativeSSD",
        "cacheState": "Cold",
        "logicalOperationCount": len(spec["operations"]),
        "stages": {
            "mutationMs": total_ms,
            "totalMs": total_ms,
        },
        "totalMs": total_ms,
        "success": bool(report.get("saved")),
        "errorCode": None if report.get("saved") else report.get("error", "cold-commandlet-failed"),
        "processLaunches": 1,
        "report": report_path.name,
        "measurementGaps": ["saveMs", "strongVerifyMs", "semanticDiffMs", "validationMs", "trustMs"],
    }


def summarize(args: argparse.Namespace) -> dict[str, Any]:
    attempts = []
    for path in args.attempts:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                attempts.append(json.loads(line))
    summary = summarize_attempts(attempts)
    summary["noise"] = noise_groups(attempts)
    summary["stageContribution"] = stage_contribution(attempts)
    write_json(args.output, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="W5 benchmark runner")
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-id", default=f"w5-{utc_stamp()}")
    common.add_argument("--output", type=Path, required=True)

    resident = subparsers.add_parser("run-resident", parents=[common])
    resident.add_argument("--scenario", choices=("R1", "R5", "R20"), required=True)
    resident.add_argument("--sample-index", type=int, required=True)
    resident.add_argument("--cache-state", choices=("WarmLoaded", "WarmUnloaded"), required=True)
    resident.add_argument("--database", type=Path, required=True)
    resident.add_argument("--revision-export", type=Path, required=True)
    resident.add_argument("--policy", type=Path, required=True)
    resident.add_argument("--work-root", type=Path, required=True)
    resident.add_argument("--backup-root", type=Path, required=True)
    resident.add_argument("--project", type=Path, required=True)
    resident.add_argument("--engine-root", type=Path, required=True)

    cold = subparsers.add_parser("run-cold", parents=[common])
    cold.add_argument("--scenario", choices=("R1", "R5", "R20"), required=True)
    cold.add_argument("--sample-index", type=int, required=True)
    cold.add_argument("--launch-index", type=int, required=True)
    cold.add_argument("--patch-path", type=Path, required=True)
    cold.add_argument("--policy", type=Path, required=True)
    cold.add_argument("--revision-export", type=Path, required=True)
    cold.add_argument("--project", type=Path, required=True)
    cold.add_argument("--engine-root", type=Path, required=True)

    sum_parser = subparsers.add_parser("summarize")
    sum_parser.add_argument("--attempts", nargs="+", type=Path, required=True)
    sum_parser.add_argument("--output", type=Path, required=True)

    args = parser.parse_args()
    if args.command == "run-resident":
        attempt = run_resident(args)
        write_json(args.output, attempt)
        print(json.dumps(attempt, ensure_ascii=False, indent=2))
    elif args.command == "run-cold":
        attempt = run_cold_pair(args)
        write_json(args.output, attempt)
        print(json.dumps(attempt, ensure_ascii=False, indent=2))
    elif args.command == "summarize":
        summary = summarize(args)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

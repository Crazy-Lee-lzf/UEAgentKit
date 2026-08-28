from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.agent_workflow import PatchWorkflowConfig, PatchWorkflowService  # noqa: E402
from ue_agent_kit.batch_recovery import BatchRecoveryService  # noqa: E402
from ue_agent_kit.bounded_batch import BoundedBatchService  # noqa: E402
from ue_agent_kit.checkpoint_sets import CheckpointSetService  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorBridgeConfig, LiveEditorBridgeService  # noqa: E402
from ue_agent_kit.mcp_server import __version__  # noqa: E402
from ue_agent_kit.snapshot_lifecycle import freeze_active_snapshot, resolve_active_snapshot  # noqa: E402

BP_ASSET = "/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint"
DA_ASSET = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset"
PIN_GRAPH = "12345678-9abc-def0-1234-56789abcdef0"
PIN_NODE = "11111111-2222-2222-3333-333344444444"
BP_OPERATIONS = [
    {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 42},
    {
        "operation": "setComponentProperty",
        "target": {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        "value": 10,
    },
    {
        "operation": "setPinDefault",
        "target": {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": "A"},
        "value": 7,
    },
]
DA_OPERATIONS = [{"operation": "setAssetProperty", "target": {"propertyPath": "IntValue"}, "value": 142}]


def require(condition: bool, message: Any) -> None:
    if not condition:
        raise RuntimeError(message)


def build_services(
    args: argparse.Namespace,
) -> tuple[PatchWorkflowService, BoundedBatchService, CheckpointSetService, BatchRecoveryService]:
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
        LiveEditorBridgeConfig(project_path=args.project, timeout_seconds=30.0, policy_path=args.policy),
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


def apply_batch(
    workflow: PatchWorkflowService,
    bounded: BoundedBatchService,
    title: str,
    task_id: str,
) -> dict[str, Any]:
    workflow.live_editor_service.call_tool("ue_open_asset", {"assetPath": BP_ASSET})
    workflow.live_editor_service.call_tool("ue_open_asset", {"assetPath": DA_ASSET})
    cs = workflow.create_change_set(title=title, task_id=task_id)
    plan = bounded.plan(
        assets=[
            {"assetPath": BP_ASSET, "operations": BP_OPERATIONS},
            {"assetPath": DA_ASSET, "operations": DA_OPERATIONS},
        ],
        description=title,
    )
    applied = bounded.apply_live_write_batch(
        batch_plan_id=plan["batchPlanId"],
        confirmation=f"APPLY LIVE WRITE BATCH {plan['batchPlanId']}",
        change_set_id=cs["changeSetId"],
    )
    require(applied["state"] == "applied", applied)
    return {
        "changeSetId": cs["changeSetId"],
        "batchPlanId": plan["batchPlanId"],
        "batchExecutionId": applied["batchExecutionId"],
    }


def execution_payload(bounded: BoundedBatchService, batch_execution_id: str) -> dict[str, Any]:
    return bounded.get_batch_execution(batch_execution_id).payload


def h2_prepare(args: argparse.Namespace) -> dict[str, Any]:
    workflow, bounded, _, _ = build_services(args)
    batch = apply_batch(workflow, bounded, "W4-6 H2", "task_w4_6_h2")
    payload = execution_payload(bounded, batch["batchExecutionId"])
    return {
        "case": "H2-prepare",
        "batch": batch,
        "editorSessionId": payload["operations"][0]["editorSessionId"],
        "recoveryOrder": payload["recoveryOrder"],
    }


def h2_check(args: argparse.Namespace) -> dict[str, Any]:
    require(bool(args.batch_execution_id), "--batch-execution-id is required for h2-check")
    _, _, _, recovery = build_services(args)
    preview = recovery.preview(batch_execution_id=args.batch_execution_id)
    require(preview["state"] == "blocked", preview)
    require("editor-session-unavailable" in preview["blockedReasons"], preview)
    return {
        "case": "H2",
        "batchExecutionId": args.batch_execution_id,
        "preview": preview,
        "undoCount": 0,
    }


def h5_stack_mismatch(args: argparse.Namespace) -> dict[str, Any]:
    workflow, bounded, _, recovery = build_services(args)
    batch = apply_batch(workflow, bounded, "W4-6 H5", "task_w4_6_h5")
    payload = execution_payload(bounded, batch["batchExecutionId"])
    da_operation = next(op for op in payload["operations"] if op["assetPath"] == DA_ASSET)
    unrelated = workflow.live_editor_service.call_method(
        "editor.applyAssetPropertyLive",
        {
            "operation": "setAssetProperty",
            "assetPath": DA_ASSET,
            "target": {"propertyPath": "IntValue"},
            "propertyPath": "IntValue",
            "value": 143,
            "previousTransactionId": da_operation["transactionId"],
        },
    )
    unrelated_tx = str(unrelated["transactionId"])
    session_id = str(unrelated["editorSessionId"])
    preview = recovery.preview(batch_execution_id=batch["batchExecutionId"])
    require(preview["state"] == "recovery_prepared", preview)
    failure: dict[str, Any] = {}
    try:
        recovery.commit(
            recovery_id=preview["recoveryId"],
            confirmation=preview["confirmationRequired"],
        )
    except Exception as exc:
        failure = {
            "code": getattr(exc, "code", exc.__class__.__name__),
            "message": str(exc),
        }
    require(failure.get("code") == "batch-recovery-resident-undo-failed", failure)
    after_failure = recovery.get(recovery_id=preview["recoveryId"])
    require(after_failure["state"] == "blocked", after_failure)
    require(after_failure["failedStep"] == da_operation["batchOperationId"], after_failure)
    cleanup = workflow.live_editor_service.call_method(
        "editor.undoAssetPropertyLive",
        {
            "assetPath": DA_ASSET,
            "transactionId": unrelated_tx,
            "sessionId": session_id,
        },
    )
    require(bool(cleanup.get("changed")), cleanup)
    preview2 = recovery.preview(batch_execution_id=batch["batchExecutionId"])
    commit2 = recovery.commit(
        recovery_id=preview2["recoveryId"],
        confirmation=preview2["confirmationRequired"],
    )
    require(commit2["state"] == "recovered", commit2)
    return {
        "case": "H5",
        "batch": batch,
        "unrelatedTransactionId": unrelated_tx,
        "failure": failure,
        "blockedRecovery": after_failure,
        "unrelatedCleanup": cleanup,
        "finalRecovery": commit2,
    }


def h6_durable_resume(args: argparse.Namespace) -> dict[str, Any]:
    workflow, bounded, _, recovery = build_services(args)
    batch = apply_batch(workflow, bounded, "W4-6 H6", "task_w4_6_h6")
    preview = recovery.preview(batch_execution_id=batch["batchExecutionId"])
    require(preview["state"] == "recovery_prepared", preview)
    original_undo = workflow.undo_asset_property_live
    calls = {"count": 0}

    def injected_undo(*call_args: Any, **call_kwargs: Any) -> dict[str, Any]:
        calls["count"] += 1
        if calls["count"] == 2:
            from ue_agent_kit.agent_workflow import WorkflowError

            raise WorkflowError(
                "w4-6-test-controlled-stop",
                "Controlled H6 stop before the second resident Undo.",
            )
        return original_undo(*call_args, **call_kwargs)

    workflow.undo_asset_property_live = injected_undo  # type: ignore[method-assign]
    failure: dict[str, Any] = {}
    try:
        recovery.commit(
            recovery_id=preview["recoveryId"],
            confirmation=preview["confirmationRequired"],
        )
    except Exception as exc:
        failure = {
            "code": getattr(exc, "code", exc.__class__.__name__),
            "message": str(exc),
        }
    require(failure.get("code") == "batch-recovery-resident-undo-failed", failure)
    partial = recovery.get(recovery_id=preview["recoveryId"])
    require(partial["state"] == "partially_recovered", partial)
    require(len(partial["recoveredResidentOperations"]) == 1, partial)
    require(partial["recoveredResidentOperations"][0]["batchOperationId"] == "bop_0004", partial)
    _, _, _, restarted = build_services(args)
    resumed = restarted.commit(
        recovery_id=preview["recoveryId"],
        confirmation=preview["confirmationRequired"],
    )
    require(resumed["state"] == "recovered", resumed)
    require(resumed["failedStep"] == "", resumed)
    require(resumed["failureBoundary"] == {}, resumed)
    ids = [step["batchOperationId"] for step in resumed["recoveredResidentOperations"]]
    require(ids == ["bop_0004", "bop_0003", "bop_0002", "bop_0001"], ids)
    require(sum(1 for item in ids if item == "bop_0004") == 1, ids)
    return {
        "case": "H6",
        "batch": batch,
        "failure": failure,
        "partial": partial,
        "resumed": resumed,
    }


def run_h1_h4(args: argparse.Namespace) -> dict[str, Any]:
    workflow, bounded, _, _ = build_services(args)
    h1_batch = apply_batch(workflow, bounded, "W4-6 H1", "task_w4_6_h1")
    _, _, _, recovery_restarted = build_services(args)
    h1_preview = recovery_restarted.preview(batch_execution_id=h1_batch["batchExecutionId"])
    require(h1_preview["state"] == "recovery_prepared", h1_preview)
    h1_commit = recovery_restarted.commit(
        recovery_id=h1_preview["recoveryId"],
        confirmation=h1_preview["confirmationRequired"],
    )
    require(h1_commit["state"] == "recovered", h1_commit)

    workflow3, bounded3, cps3, _ = build_services(args)
    h3_batch = apply_batch(workflow3, bounded3, "W4-6 H3", "task_w4_6_h3")
    preview3 = cps3.preview(batch_execution_id=h3_batch["batchExecutionId"])
    cps3._fault_after_saved_asset = BP_ASSET
    try:
        cps3.commit(
            checkpoint_set_id=preview3["checkpointSetId"],
            confirmation=preview3["confirmationRequired"],
        )
    except Exception:
        pass
    cps_payload = cps3.load_payload(preview3["checkpointSetId"])
    require(cps_payload["state"] == "partially_saved", cps_payload)
    _, _, _, recovery4 = build_services(args)
    h4_preview = recovery4.preview(batch_execution_id=h3_batch["batchExecutionId"])
    require(h4_preview["state"] == "recovery_prepared", h4_preview)
    h4_commit = recovery4.commit(
        recovery_id=h4_preview["recoveryId"],
        confirmation=h4_preview["confirmationRequired"],
    )
    require(h4_commit["state"] == "recovered", h4_commit)
    return {
        "case": "H1-H4",
        "h1": {"batch": h1_batch, "preview": h1_preview, "commit": h1_commit},
        "h3h4": {
            "batch": h3_batch,
            "checkpointSet": {
                "state": cps_payload["state"],
                "persistedAssets": cps_payload["persistedAssets"],
            },
            "preview": h4_preview,
            "commit": h4_commit,
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.case == "h2-prepare":
        return h2_prepare(args)
    if args.case == "h2-check":
        return h2_check(args)
    if args.case == "h5":
        return h5_stack_mismatch(args)
    if args.case == "h6":
        return h6_durable_resume(args)
    return run_h1_h4(args)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument(
        "--case",
        choices=["h1-h4", "h2-prepare", "h2-check", "h5", "h6"],
        default="h1-h4",
    )
    parser.add_argument("--batch-execution-id", default="")
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


if __name__ == "__main__":
    main()

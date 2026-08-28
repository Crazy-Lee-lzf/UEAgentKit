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
from ue_agent_kit.agent_workflow import PatchWorkflowConfig, PatchWorkflowService, WorkflowError  # noqa: E402
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
DA_OPERATIONS = [
    {"operation": "setAssetProperty", "target": {"propertyPath": "IntValue"}, "value": 142}
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def build_services(args: argparse.Namespace) -> tuple[PatchWorkflowService, BoundedBatchService, CheckpointSetService]:
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
    PatchWorkflowService(
        validation_index,
        validation_config,
        live_editor_service=live_editor_service,
    )
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
    return workflow, bounded, checkpoint_sets


def run(args: argparse.Namespace) -> dict[str, Any]:
    workflow, bounded, checkpoint_sets = build_services(args)
    workflow.live_editor_service.call_tool("ue_open_asset", {"assetPath": BP_ASSET})
    workflow.live_editor_service.call_tool("ue_open_asset", {"assetPath": DA_ASSET})
    change_set = workflow.create_change_set(title="W4-4 C7 mid-save failure", task_id="task_w4_4_c7")
    change_set_id = str(change_set["changeSetId"])
    plan = bounded.plan(
        assets=[
            {"assetPath": BP_ASSET, "operations": BP_OPERATIONS},
            {"assetPath": DA_ASSET, "operations": DA_OPERATIONS},
        ],
        description="W4-4 C7",
    )
    applied = bounded.apply_live_write_batch(
        batch_plan_id=plan["batchPlanId"],
        confirmation=f"APPLY LIVE WRITE BATCH {plan['batchPlanId']}",
        change_set_id=change_set_id,
    )
    require(applied["state"] == "applied", f"apply state wrong: {applied}")
    preview = checkpoint_sets.preview(batch_execution_id=applied["batchExecutionId"])
    checkpoint_sets._fault_after_saved_asset = BP_ASSET
    failed: dict[str, Any] = {}
    try:
        checkpoint_sets.commit(
            checkpoint_set_id=preview["checkpointSetId"],
            confirmation=preview["confirmationRequired"],
        )
    except WorkflowError as exc:
        failed = {
            "code": exc.code,
            "message": str(exc),
            "details": exc.details,
        }
    require(bool(failed), "expected C7 commit to fail")
    return {
        "case": "C7",
        "batchPlanId": plan["batchPlanId"],
        "batchExecutionId": applied["batchExecutionId"],
        "changeSetId": change_set_id,
        "preview": preview,
        "failedCommit": failed,
    }


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
    args = parser.parse_args()
    summary = run(args)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
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

EXPECTED_TOOLS = tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True)

SOURCE_MESH = "/Game/Characters/Mannequins/Meshes/SKM_Manny_Simple.SKM_Manny_Simple"
TARGET_MESH = "/Game/Characters/XinYueHu/Mesh/SK_XinYueHu.SK_XinYueHu"
OUTPUT_DIRECTORY = "/Game/UEAgentKitRetargetTests/Retargeted"

REQUIRED_CHAIN_NAMES = {"Root", "Spine", "Neck", "Head", "LeftArm", "RightArm", "LeftLeg", "RightLeg"}


def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


async def call(session: ClientSession, tool: str, params: dict[str, Any]) -> dict[str, Any]:
    return payload(await session.call_tool(tool, params), tool)


async def run(args: argparse.Namespace) -> dict[str, Any]:
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
                for asset_path in (SOURCE_MESH, TARGET_MESH):
                    opened = await call(session, "ue_open_asset", {"asset_path": asset_path})
                    if not opened.get("ok") or not opened["result"].get("openAfter"):
                        raise RuntimeError(f"The fixture {asset_path} was not opened: {opened}")

                planned = await call(
                    session,
                    "ue_plan_animation_retarget",
                    {
                        "sourceMesh": SOURCE_MESH,
                        "targetMesh": TARGET_MESH,
                        "includeOptionalChains": True,
                        "outputDirectory": OUTPUT_DIRECTORY,
                    },
                )
                plan_id = planned.get("planId")
                if (
                    not planned.get("ok")
                    or not plan_id
                    or planned.get("compatibility") not in {"compatible", "compatible_with_warnings"}
                    or planned.get("blockingIssues")
                    or planned.get("confirmationText") != f"APPLY RETARGET SETUP {plan_id}"
                    or not planned.get("result", {}).get("revisions", {}).get("sourceMesh")
                ):
                    raise RuntimeError(f"Retarget plan contract is broken: {planned}")
                planned_chains = {
                    str(chain.get("chain")): chain for chain in planned.get("sourceChains", [])
                }
                missing_required = REQUIRED_CHAIN_NAMES - set(planned_chains)
                if missing_required:
                    raise RuntimeError(f"Plan is missing required source chains: {sorted(missing_required)}")

                wrong_confirmation = await call(
                    session,
                    "ue_apply_animation_retarget_setup",
                    {
                        "planId": plan_id,
                        "confirmation": "wrong phrase",
                    },
                )
                if wrong_confirmation.get("ok") or wrong_confirmation.get("error", {}).get("code") != "retarget-confirmation-required":
                    raise RuntimeError(f"Retarget apply accepted a wrong confirmation: {wrong_confirmation}")

                conflicting = await call(
                    session,
                    "ue_apply_animation_retarget_setup",
                    {
                        "planId": plan_id,
                        "confirmation": f"APPLY RETARGET SETUP {plan_id}",
                    },
                )
                if conflicting.get("ok") or conflicting.get("error", {}).get("code") != "retarget_asset_conflict":
                    raise RuntimeError(
                        f"Retarget apply did not block an existing unowned rig: {conflicting}"
                    )

                applied = await call(
                    session,
                    "ue_apply_animation_retarget_setup",
                    {
                        "planId": plan_id,
                        "confirmation": f"APPLY RETARGET SETUP {plan_id}",
                        "updateExisting": True,
                    },
                )
                changes = applied.get("changes", [])
                actions = sorted(change.get("action") for change in changes)
                # The fixtures already ship an IK Rig for both meshes, so the
                # Manny source rig with an identical root/chains is a no_op,
                # the XinYueHu rig is updated to the Plan chains, and the IK
                # Retargeter is created. At least one asset must be created or
                # updated and the retargeter must be created.
                if (
                    not applied.get("ok")
                    or not applied.get("changed")
                    or not applied.get("transactionCreated")
                    or not applied.get("assetDirty")
                    or not applied.get("setupReceipt")
                    or not actions
                    or not set(actions).issubset({"create", "update", "no_op"})
                    or not ({"create", "update"} & set(actions))
                ):
                    raise RuntimeError(f"Retarget setup apply contract is broken: {applied}")
                target_rig_updated = any(
                    change.get("action") == "update"
                    and "IKRig_XinYueHu" in str(change.get("assetPath", ""))
                    for change in changes
                )
                retargeter_created = any(
                    change.get("action") == "create"
                    and "IKRetargeter_" in str(change.get("assetPath", ""))
                    for change in changes
                )
                if not target_rig_updated or not retargeter_created:
                    raise RuntimeError(
                        f"Retarget setup apply did not update the XinYueHu IK Rig and create the IK Retargeter: {applied}"
                    )
                mapping_report = applied.get("mappingReport", {})
                mapped_required = set(mapping_report.get("mappedRequiredChains", []))
                if not mapped_required.issuperset(REQUIRED_CHAIN_NAMES):
                    raise RuntimeError(
                        f"Retarget mapping is missing required chains: {sorted(REQUIRED_CHAIN_NAMES - mapped_required)}"
                    )
                if not applied.get("poseApplied") or applied.get("poseName") != "TargetPose_A":
                    raise RuntimeError(f"Retarget pose was not applied: {applied}")
                retargeter_change = next(
                    (c for c in changes if "IKRetargeter_" in str(c.get("assetPath", ""))), None
                )
                if not retargeter_change or not retargeter_change.get("details"):
                    raise RuntimeError(f"Retargeter change has no chain details: {retargeter_change}")

                re_applied = await call(
                    session,
                    "ue_apply_animation_retarget_setup",
                    {
                        "planId": plan_id,
                        "confirmation": f"APPLY RETARGET SETUP {plan_id}",
                        "updateExisting": True,
                    },
                )
                if not re_applied.get("ok") or re_applied.get("changed"):
                    raise RuntimeError(f"Retarget setup apply is not idempotent: {re_applied}")

                return {
                    "protocolVersion": initialized.protocolVersion,
                    "toolCount": len(tool_names),
                    "planId": plan_id,
                    "compatibility": planned.get("compatibility"),
                    "sourceChainCount": len(planned.get("sourceChains", [])),
                    "mappingCount": len(planned.get("mappings", [])),
                    "createdAssets": [change.get("assetPath") for change in changes],
                    "retargeterAsset": retargeter_change.get("assetPath"),
                    "mappedRequired": sorted(mapped_required),
                    "poseApplied": applied.get("poseApplied"),
                    "conflictBlocked": not conflicting.get("ok"),
                    "idempotentThirdApply": not re_applied.get("changed"),
                }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-root", required=True, type=Path)
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--revision-export", required=True, type=Path)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--backup-root", required=True, type=Path)
    parser.add_argument("--error-log", required=True, type=Path)
    parser.add_argument("--session-marker", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run(args))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

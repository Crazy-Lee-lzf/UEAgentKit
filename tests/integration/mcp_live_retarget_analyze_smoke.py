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

                analyzed = await call(
                    session,
                    "ue_analyze_animation_retarget",
                    {
                        "sourceMesh": SOURCE_MESH,
                        "targetMesh": TARGET_MESH,
                        "includeOptionalChains": True,
                        "maxBoneDetails": 512,
                    },
                )
                result = analyzed.get("result", {})
                analysis = result.get("analysis", {})
                if (
                    not analyzed.get("ok")
                    or not analyzed.get("readOnly")
                    or result.get("sourceMesh") != SOURCE_MESH
                    or result.get("targetMesh") != TARGET_MESH
                    or analysis.get("compatibility") != "compatible_with_warnings"
                    or not analysis.get("sourceRetargetRootCandidates")
                    or not analysis.get("targetRetargetRootCandidates")
                    or analysis.get("unmatchedRequiredChains") != []
                    or not analysis.get("chainCandidates")
                ):
                    raise RuntimeError(f"Retarget analysis contract is broken: {analyzed}")
                required_chains = {
                    "Root", "Spine", "Neck", "Head", "LeftArm", "RightArm", "LeftLeg", "RightLeg"
                }
                chain_report = {
                    str(chain.get("chain")): chain for chain in analysis.get("chainCandidates", [])
                }
                for chain_name in required_chains:
                    chain = chain_report.get(chain_name)
                    if chain is None or not chain.get("candidates") or chain.get("ambiguous"):
                        raise RuntimeError(f"Required chain {chain_name} is missing or ambiguous: {analyzed}")
                # The optional accessory bones of XinYueHu must never be mapped as
                # humanoid chains.
                for chain_name, chain in chain_report.items():
                    if chain.get("required") != "optional":
                        continue
                    if not chain.get("candidates"):
                        continue
                    start = str(chain["candidates"][0].get("startBone", "")).lower()
                    for keyword in ("hair", "tail", "ear", "skirt", "cloth", "piao"):
                        if keyword in start:
                            raise RuntimeError(f"Accessory bone was mapped as a chain: {chain_name} -> {start}")

                return {
                    "protocolVersion": initialized.protocolVersion,
                    "toolCount": len(tool_names),
                    "compatibility": analysis.get("compatibility"),
                    "unmatchedRequiredChains": analysis.get("unmatchedRequiredChains"),
                    "unmatchedOptionalChains": analysis.get("unmatchedOptionalChains"),
                    "mappedRequiredChains": sorted(required_chains & set(chain_report)),
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

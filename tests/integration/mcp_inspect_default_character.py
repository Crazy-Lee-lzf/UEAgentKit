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

INSPECT_TARGETS = [
    "/Game/Characters/XinYueHu/Blueprints/BP_XinYueHu_Player.BP_XinYueHu_Player",
    "/Game/Characters/XinYueHu/Blueprints/BP_XinYueHu_Character.BP_XinYueHu_Character",
    "/Game/ThirdPerson/Blueprints/BP_ThirdPersonGameMode.BP_ThirdPersonGameMode",
    "/Game/ThirdPerson/Blueprints/BP_ThirdPersonCharacter.BP_ThirdPersonCharacter",
]


async def payload(result: Any, tool: str) -> dict[str, Any]:
    value = result.structuredContent
    if not isinstance(value, dict):
        raise RuntimeError(f"{tool} returned no structured object: {result}")
    return value


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
    report: dict[str, Any] = {}
    with args.error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                if args.session_marker is not None:
                    args.session_marker.write_text("session-initialized\n", encoding="utf-8")
                for target in INSPECT_TARGETS:
                    result = await session.call_tool(
                        "ue_inspect_asset_live", {"asset_path": target}
                    )
                    data = await payload(result, "ue_inspect_asset_live")
                    report[target] = {
                        "ok": data.get("ok"),
                        "classPath": data.get("result", {}).get("assetRegistry", {}).get("classPath"),
                        "memory": data.get("result", {}).get("memory"),
                    }
                context = await session.call_tool("ue_get_editor_context", {})
                report["editorContext"] = await payload(context, "ue_get_editor_context")
                print(json.dumps(report, indent=2, sort_keys=True))
    return report


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
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

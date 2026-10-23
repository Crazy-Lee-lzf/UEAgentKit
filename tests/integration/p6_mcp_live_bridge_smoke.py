#!/usr/bin/env python3
"""P6 real MCP stdio round-trip for the read-only graph-layout preflight tool.

The MCP server is launched as a child process with a fixed P6 host project and a
disposable, empty SQLite index.  This script never uses a production database,
never changes the fixed project, and never calls a write tool.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402


async def run(args: argparse.Namespace) -> dict[str, object]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "ue_agent_kit.mcp_server",
            "--database",
            str(args.database),
            "--enable-live-editor",
            "--project",
            str(args.project),
        ],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    error_log = args.out / "p6-mcp-server.stderr.log"
    error_log.parent.mkdir(parents=True, exist_ok=True)
    with error_log.open("w", encoding="utf-8", newline="\n") as log:
        async with stdio_client(parameters, errlog=log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()
                capabilities = await session.call_tool("ue_get_capabilities", {})
                preflight = await session.call_tool(
                    "ue_get_blueprint_graph_layout_preflight", {}
                )
                legacy_selection = (
                    None
                    if args.expect_failure
                    else await session.call_tool("ue_get_blueprint_graph_selection", {})
                )
    tool_names = [tool.name for tool in tools.tools]
    if "ue_get_blueprint_graph_layout_preflight" not in tool_names:
        raise RuntimeError("P6 read tool was not registered by the MCP server")
    if "ue_apply_blueprint_graph_layout" in tool_names:
        raise RuntimeError("A public graph-layout write tool was unexpectedly registered")
    payload = preflight.structuredContent
    if not isinstance(payload, dict):
        raise RuntimeError(f"P6 MCP preflight returned no structured payload: {payload}")
    if args.expect_failure:
        if payload.get("ok") is not False:
            raise RuntimeError(f"P6 MCP failure probe unexpectedly succeeded: {payload}")
        code = payload.get("error", {}).get("code", "")
        if args.expect_code and code != args.expect_code:
            raise RuntimeError(f"P6 MCP failure code {code!r} != {args.expect_code!r}")
        summary = {
            "schemaVersion": "ueak.p6.mcp/1.0",
            "transport": "stdio-mcp",
            "toolCount": len(tool_names),
            "toolNames": tool_names,
            "preflightTool": "ue_get_blueprint_graph_layout_preflight",
            "writeToolRegistered": False,
            "readOnly": True,
            "ok": False,
            "errorCode": code,
            "error": payload.get("error", {}),
        }
        (args.out / "p6-mcp-failure.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return summary
    if payload.get("ok") is not True:
        raise RuntimeError(f"P6 MCP preflight failed: {payload}")
    if payload.get("readOnly") is not True:
        raise RuntimeError("P6 MCP preflight is not marked read-only")
    result = payload.get("result")
    if not isinstance(result, dict) or result.get("available") is not True:
        raise RuntimeError(f"P6 MCP preflight result is not available: {payload}")
    capability_payload = capabilities.structuredContent
    if not isinstance(capability_payload, dict):
        raise RuntimeError("P6 MCP capabilities are missing")
    contract = capability_payload.get("liveEditor", {}).get("graphLayoutPreflight", {})
    if contract.get("writeToolRegistered") is not False:
        raise RuntimeError("P6 capability contract does not deny a write tool")
    legacy_payload = legacy_selection.structuredContent
    if not isinstance(legacy_payload, dict) or legacy_payload.get("ok") is not True:
        raise RuntimeError("Legacy graph selection regressed")
    summary = {
        "schemaVersion": "ueak.p6.mcp/1.0",
        "transport": "stdio-mcp",
        "serverName": getattr(initialized, "serverInfo", None).name
        if getattr(initialized, "serverInfo", None) is not None
        else "",
        "toolCount": len(tool_names),
        "toolNames": tool_names,
        "preflightTool": "ue_get_blueprint_graph_layout_preflight",
        "writeToolRegistered": False,
        "readOnly": True,
        "result": result,
        "capabilitiesGraphLayout": contract,
        "legacySelectionGraphGuid": legacy_payload.get("result", {}).get("graph", {}).get("graphGuid", ""),
    }
    (args.out / "p6-mcp-result.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect-failure", action="store_true")
    parser.add_argument("--expect-code", default="")
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - CLI must report the exact boundary
        print(f"P6 MCP smoke failed: {exc}", file=sys.stderr)
        return 1
    if args.expect_failure:
        print(
            json.dumps(
                {
                    "ok": False,
                    "expectedFailure": True,
                    "errorCode": summary["errorCode"],
                    "toolCount": summary["toolCount"],
                },
                sort_keys=True,
            )
        )
        return 0
    print(
        json.dumps(
            {
                "ok": True,
                "toolCount": summary["toolCount"],
                "assetPath": summary["result"].get("assetPath"),
                "graphGuid": summary["result"].get("graphGuid"),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

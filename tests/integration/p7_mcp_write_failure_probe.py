#!/usr/bin/env python3
"""P7 negative MCP probe on a real P7 Editor with no focused Blueprint graph."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

from ue_agent_kit.p7_test_only import APPLY_TOOL_NAME, confirmation_for_plan  # noqa: E402


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    request = json.loads(args.request.read_text(encoding="utf-8"))
    confirmation = confirmation_for_plan(str(request["plan"]["planId"]))
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
            "--enable-p7-test-only-graph-layout-apply",
        ],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    error_log = args.out / "p7-mcp-failure-server.stderr.log"
    with error_log.open("w", encoding="utf-8", newline="\n") as log:
        async with stdio_client(parameters, errlog=log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = [tool.name for tool in tools.tools]
                if APPLY_TOOL_NAME not in names:
                    raise RuntimeError("P7 test-only apply tool is not registered")
                result = await session.call_tool(
                    APPLY_TOOL_NAME,
                    {"request": request, "confirmation": confirmation},
                )
                payload = result.structuredContent
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP failure returned no structured payload: {payload}")
    if payload.get("ok") is not False:
        raise RuntimeError(f"P7 no-focused-graph probe unexpectedly succeeded: {payload}")
    error = payload.get("error", {})
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    if args.expect_code and code != args.expect_code:
        raise RuntimeError(f"P7 failure code {code!r} != {args.expect_code!r}: {payload}")
    summary = {
        "schemaVersion": "ueak.p7.mcpfailure/1.0",
        "ok": False,
        "transport": "stdio-mcp",
        "toolCount": len(names),
        "applyToolRegistered": True,
        "errorCode": code,
        "error": error,
        "expectedCode": args.expect_code,
        "success": True,
    }
    _write_json(args.out / "p7-mcp-failure.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--expect-code", default="graph-layout-editor-not-open")
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001
        print(f"P7 failure probe failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "errorCode": summary["errorCode"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

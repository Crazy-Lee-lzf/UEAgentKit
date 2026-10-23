#!/usr/bin/env python3
"""Prove the P7 write tool is conditionally absent and project-gated."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402

from ue_agent_kit.p7_test_only import APPLY_TOOL_NAME, FIXED_PROJECT_PATH  # noqa: E402


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


async def _list_tools(project: Path, database: Path, out: Path) -> list[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "ue_agent_kit.mcp_server",
            "--database",
            str(database),
            "--enable-live-editor",
            "--project",
            str(project),
        ],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    with (out / "p7-registration-gate-server.log").open("w", encoding="utf-8", newline="\n") as log:
        async with stdio_client(parameters, errlog=log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                return [tool.name for tool in tools.tools]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--p6-project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        names = asyncio.run(_list_tools(args.p6_project, args.database, args.out))
        if APPLY_TOOL_NAME in names:
            raise RuntimeError("P7 apply tool was registered for a non-P7 project without the lab flag")
        if "ue_apply_blueprint_graph_layout" in names:
            raise RuntimeError("A general graph-layout write tool exists")
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "ue_agent_kit.mcp_server",
                "--database",
                str(args.database),
                "--enable-live-editor",
                "--project",
                str(args.p6_project),
                "--enable-p7-test-only-graph-layout-apply",
                "--check",
            ],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(SRC)},
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode == 0:
            raise RuntimeError("P7 lab flag unexpectedly accepted a non-P7 project")
        summary = {
            "schemaVersion": "ueak.p7.registrationgate/1.0",
            "ok": True,
            "nonP7ToolCount": len(names),
            "applyToolRegisteredWithoutFlag": False,
            "generalWriteToolRegistered": False,
            "labFlagRejectedOnNonP7Project": True,
            "labFlagExitCode": completed.returncode,
            "labFlagStderr": completed.stderr[-1000:],
            "fixedProject": str(FIXED_PROJECT_PATH),
            "success": True,
        }
        _write_json(args.out / "p7-registration-gate.json", summary)
    except Exception as exc:  # noqa: BLE001
        print(f"P7 registration gate failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "nonP7ToolCount": summary["nonP7ToolCount"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Bounded real-MCP negative: a valid Comment move must hit the P8 guard.

The canonical plan is derived from the already-saved P8 layout plan.  Every
content node is reset to its current live position; only the Comment moves.
The layout hash is recomputed, so the request cannot fail early on stale
revision or an invalid hash.  The expected stop is the P8 Comment guard.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
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

from ue_agent_kit.graph_contracts import (  # noqa: E402
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
)
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.p8_test_only import (  # noqa: E402
    APPLY_TOOL_NAME,
    confirmation_for_plan,
)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _structured(result: Any) -> dict[str, Any]:
    payload = getattr(result, "structuredContent", None)
    if not isinstance(payload, dict):
        raise RuntimeError(f"MCP returned no structured content: {payload}")
    return payload


def _positions(preflight: dict[str, Any]) -> dict[str, tuple[int, int]]:
    return {
        str(node["nodeId"]): (int(node["x"]), int(node["y"]))
        for node in preflight.get("nodes", [])
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
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
            "--enable-p8-test-only-graph-layout-apply",
        ],
        cwd=ROOT,
        env=env,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    error_log = args.out / "p8-comment-guard-server.stderr.log"
    with error_log.open("w", encoding="utf-8", newline="\n") as log:
        async with stdio_client(parameters, errlog=log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                preflight_payload = _structured(
                    await session.call_tool("ue_get_blueprint_graph_layout_preflight", {})
                )
                if preflight_payload.get("ok") is not True:
                    raise RuntimeError(f"preflight failed: {preflight_payload}")
                preflight = preflight_payload["result"]
                if preflight.get("packageDirty") is not False:
                    raise RuntimeError("Comment guard probe requires a clean package")
                before = _positions(preflight)
                plan = json.loads(args.plan.read_text(encoding="utf-8"))

                comment_ids = [
                    node["nodeId"]
                    for node in preflight["nodes"]
                    if node.get("kind") == "comment"
                ]
                if len(comment_ids) != 1:
                    raise RuntimeError(f"expected exactly one comment, got {comment_ids}")
                comment_id = comment_ids[0]
                comment_guid = comment_id[2:]

                isolated = copy.deepcopy(plan)
                for item in isolated["positions"]:
                    node_id = str(item["nodeId"])
                    item["x"], item["y"] = before[node_id]
                comment_position = next(
                    item for item in isolated["positions"] if item["nodeId"] == comment_id
                )
                comment_position["x"] = 0
                comment_position["y"] = -500
                isolated["movedNodeIds"] = [comment_id]
                isolated["layoutHash"] = recompute_layout_hash(isolated)
                isolated["planId"] = plan["planId"]

                try:
                    request = build_graph_layout_apply_request(isolated, preflight)
                except GraphLayoutApplyError as exc:
                    raise RuntimeError(
                        f"comment request was rejected before the Editor guard: {exc.code}: {exc}"
                    ) from exc

                payload = _structured(
                    await session.call_tool(
                        APPLY_TOOL_NAME,
                        {
                            "request": request,
                            "confirmation": confirmation_for_plan(isolated["planId"]),
                        },
                    )
                )
                after_payload = _structured(
                    await session.call_tool("ue_get_blueprint_graph_layout_preflight", {})
                )
                after = after_payload.get("result")
                if not isinstance(after, dict):
                    raise RuntimeError("post-probe preflight missing")

    error = payload.get("error", {})
    code = str(error.get("code", "")) if isinstance(error, dict) else ""
    if payload.get("ok") is not False or code != "graph-layout-test-only-comment-move-unsupported":
        raise RuntimeError(f"comment guard did not fire as expected: {payload}")
    if _positions(after) != before:
        raise RuntimeError("comment guard changed live positions")
    if after.get("packageDirty") is not False:
        raise RuntimeError("comment guard dirtied the package")

    summary = {
        "schemaVersion": "ueak.p8.commentguard/1.0",
        "ok": True,
        "transport": "stdio-mcp",
        "tool": APPLY_TOOL_NAME,
        "commentNodeId": comment_id,
        "commentGuid": comment_guid,
        "beforeComment": {"x": before[comment_id][0], "y": before[comment_id][1]},
        "requestedComment": {"x": 0, "y": -500},
        "requestedMovedNodeIds": [comment_id],
        "recomputedLayoutHash": isolated["layoutHash"],
        "errorCode": code,
        "error": error,
        "positionsUnchanged": True,
        "packageDirty": False,
        "success": True,
    }
    _write_json(args.out / "p8-comment-guard.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        summary = asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001
        print(f"P8 comment guard probe failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "errorCode": summary["errorCode"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Real live-Editor acceptance probe for the bounded layout projection.

This script only reads the focused graph.  It does not apply a layout, save a
package, compile a Blueprint, or launch an Editor.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit import __version__  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.editor_bridge import (  # noqa: E402
    LiveEditorBridgeConfig,
    LiveEditorBridgeService,
    LiveEditorError,
)
from ue_agent_kit.graph_layout_projection import (  # noqa: E402
    PROJECTION_METHOD,
    fetch_layout_projection,
)
from ue_agent_kit.mcp_graph_layout_preview import (  # noqa: E402
    PREFLIGHT_METHOD,
    PREVIEW_TOOL_NAME,
)
from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    service = LiveEditorBridgeService(
        LiveEditorBridgeConfig(project_path=args.project.resolve()),
        server_version=__version__,
    )
    status = service.status()
    if status.get("state") != "available":
        print(json.dumps(status, indent=2))
        return 2
    capabilities = status.get("capabilities") or []
    if PROJECTION_METHOD not in capabilities:
        print("projection capability missing", file=sys.stderr)
        return 3

    preflight = service.call_method(PREFLIGHT_METHOD, {})
    if preflight.get("available") is not True:
        print(json.dumps(preflight, indent=2))
        return 4

    old_context_error: dict[str, Any] | None = None
    try:
        service.call_method("editor.getFocusedBlueprintGraphContext", {})
    except LiveEditorError as exc:
        old_context_error = {"code": exc.code, "message": str(exc)}

    with tempfile.TemporaryDirectory() as temporary:
        database = Path(temporary) / "ueak.sqlite3"
        with open_database(database):
            pass
        server = create_mcp_server(database, live_editor_service=service)
        tools = asyncio.run(server.list_tools())
        if PREVIEW_TOOL_NAME not in {tool.name for tool in tools}:
            print("preview tool missing from MCP server", file=sys.stderr)
            return 9
        _, payload = asyncio.run(server.call_tool(PREVIEW_TOOL_NAME, {}))
    if payload.get("ok") is not True:
        print(json.dumps(payload, indent=2))
        return 5
    preview = payload["result"]

    projection = fetch_layout_projection(service, preflight)
    expected_nodes = {
        "n:" + str(node["guid"]).lower()
        for node in preflight.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("guid"), str)
    }
    positions = {str(item["nodeId"]) for item in preview.get("positions", [])}
    if expected_nodes != positions:
        print(
            f"position identity mismatch preflight={len(expected_nodes)} preview={len(positions)}",
            file=sys.stderr,
        )
        return 6
    preflight_positions = {
        "n:" + str(node["guid"]).lower(): (int(node["x"]), int(node["y"]))
        for node in preflight.get("nodes", [])
        if isinstance(node, dict) and isinstance(node.get("guid"), str)
    }
    for item in preview.get("positions", []):
        before = preflight_positions[str(item["nodeId"])]
        if (int(item["beforeX"]), int(item["beforeY"])) != before:
            print(f"position mismatch for {item['nodeId']}", file=sys.stderr)
            return 8
    if int(preview.get("nodeCount", -1)) != int(preflight.get("nodeCount", -2)):
        print("preview nodeCount mismatch", file=sys.stderr)
        return 7

    evidence = {
        "schemaVersion": "ueak.layout-large-projection.evidence/1.0",
        "processId": status.get("processId"),
        "project": str(args.project.resolve()),
        "assetPath": preflight.get("assetPath"),
        "graphGuid": preflight.get("graphGuid"),
        "graphName": preflight.get("graphName"),
        "packageRevision": preflight.get("packageRevision"),
        "graphFingerprint": preflight.get("graphFingerprint"),
        "nodeCount": preflight.get("nodeCount"),
        "preflightOutputLinkCount": sum(
            int(node.get("outputLinkCount", 0))
            for node in preflight.get("nodes", [])
            if isinstance(node, dict)
        ),
        "oldFullContextError": old_context_error,
        "projection": {
            key: projection.get(key)
            for key in (
                "pageCount",
                "pageLimit",
                "maxPageBytes",
                "nodeCount",
                "pinCount",
                "edgeCount",
                "commentCount",
                "nodeSetHash",
                "pinSetHash",
                "edgeSetHash",
                "projectionHash",
            )
        },
        "preview": {
            key: preview.get(key)
            for key in (
                "contextMode",
                "nodeCount",
                "movedCount",
                "planId",
                "layoutHash",
                "baseGraphHash",
                "applyEligible",
            )
        },
        "positionCount": len(preview.get("positions", [])),
        "payload": payload,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "nodeCount": preview.get("nodeCount"),
                "positionCount": len(preview.get("positions", [])),
                "pageCount": projection.get("pageCount"),
                "maxPageBytes": projection.get("maxPageBytes"),
                "projectionHash": projection.get("projectionHash"),
                "layoutHash": preview.get("layoutHash"),
                "movedCount": preview.get("movedCount"),
                "oldFullContextError": old_context_error,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

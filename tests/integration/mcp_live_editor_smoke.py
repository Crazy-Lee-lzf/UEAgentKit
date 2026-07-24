from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
PYTHON_TESTS = TOOL_ROOT / "tests" / "python"
for search_path in (SRC_ROOT, PYTHON_TESTS):
    if str(search_path) not in sys.path:
        sys.path.insert(0, str(search_path))

from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    REVISION_A,
    make_asset,
    make_generic_asset,
    write_export,
)
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402

LIVE_TOOLS = [
    "ue_editor_status",
    "ue_get_selection",
    "ue_get_open_assets",
    "ue_get_dirty_assets",
    "ue_get_current_level",
    "ue_get_pie_state",
    "ue_get_output_log",
    "ue_get_compile_errors",
    "ue_inspect_asset_live",
    "ue_get_blueprint_graph_selection",
]
EXPECTED_TOOLS = [
    "ue_get_capabilities",
    "ue_get_project_status",
    "ue_search",
    "ue_get_asset",
    "ue_find_references",
    *LIVE_TOOLS,
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(directory: Path) -> dict[str, str]:
    return {path.name: _sha256(path) for path in directory.iterdir() if path.is_file()}


async def _run(database: Path, project: Path, error_log: Path) -> dict[str, Any]:
    before_hash = _sha256(database)
    before_files = _snapshot(database.parent)
    descriptor_path = project.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8-sig"))
    secret_token = str(descriptor["authToken"])
    secret_port = str(descriptor["port"])
    parameters = StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(database),
            "-EnableLiveEditor",
            "-ProjectPath",
            str(project),
            "-LiveEditorTimeoutSeconds",
            "5",
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    with error_log.open("w", encoding="utf-8", newline="\n") as stderr:
        async with stdio_client(parameters, errlog=stderr) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                capabilities_result = await session.call_tool("ue_get_capabilities", {})
                project_status_result = await session.call_tool("ue_get_project_status", {})
                live_results = {
                    tool: (await session.call_tool(tool, {})).structuredContent
                    for tool in LIVE_TOOLS[:6]
                }
                live_results["ue_get_output_log"] = (
                    await session.call_tool(
                        "ue_get_output_log",
                        {"minimum_verbosity": "warning", "since_sequence": 0, "limit": 50},
                    )
                ).structuredContent
                live_results["ue_get_compile_errors"] = (
                    await session.call_tool(
                        "ue_get_compile_errors",
                        {"since_sequence": 0, "limit": 50},
                    )
                ).structuredContent
                live_results["ue_inspect_asset_live"] = (
                    await session.call_tool(
                        "ue_inspect_asset_live",
                        {"asset_path": "/Game/UEAgentKitWriteTests/BP_PatchTarget.BP_PatchTarget"},
                    )
                ).structuredContent
                live_results["ue_get_blueprint_graph_selection"] = (
                    await session.call_tool("ue_get_blueprint_graph_selection", {})
                ).structuredContent

    tool_names = [tool.name for tool in listed.tools]
    if tool_names != EXPECTED_TOOLS:
        raise RuntimeError(f"Unexpected Live Editor Tool list: {tool_names}")
    for tool in listed.tools:
        properties = set(tool.inputSchema.get("properties", {}))
        forbidden = {
            "address",
            "port",
            "auth_token",
            "token",
            "endpoint",
            "project",
            "project_path",
            "filesystem_path",
            "uobject",
            "console",
            "python",
            "shell",
        }
        if properties.intersection(forbidden):
            raise RuntimeError(f"Tool exposes fixed or arbitrary bridge configuration: {tool.name} {properties}")
        if tool.name in LIVE_TOOLS:
            if not tool.annotations or not tool.annotations.readOnlyHint or tool.annotations.destructiveHint:
                raise RuntimeError(f"Live Tool annotations are unsafe: {tool.name} {tool.annotations}")

    capabilities = capabilities_result.structuredContent
    project_status = project_status_result.structuredContent
    if not capabilities or not capabilities["liveEditor"]["configured"]:
        raise RuntimeError(f"Live Editor capability discovery failed: {capabilities}")
    if capabilities["liveEditor"]["tools"] != LIVE_TOOLS:
        raise RuntimeError(f"Live Editor capability Tool list mismatch: {capabilities}")
    if capabilities["liveEditor"]["arbitraryEndpointArguments"]:
        raise RuntimeError("Live Editor capabilities claim arbitrary endpoint arguments")
    if not project_status or project_status["liveEditor"]["state"] != "available":
        raise RuntimeError(f"Live Editor project status failed: {project_status}")
    editor_status = live_results["ue_editor_status"]
    if not editor_status or not editor_status["ok"] or editor_status["result"]["state"] != "available":
        raise RuntimeError(f"ue_editor_status failed: {editor_status}")
    if editor_status["result"]["projectName"] != project.stem:
        raise RuntimeError(f"Live Editor project mismatch: {editor_status}")
    if editor_status["result"]["pluginVersion"] != capabilities["server"]["version"]:
        raise RuntimeError(f"Live Editor version mismatch: {editor_status}")
    for tool in LIVE_TOOLS[1:]:
        payload = live_results[tool]
        if not payload or not payload["ok"] or payload["source"] != "live-editor-memory":
            raise RuntimeError(f"Live Tool failed: {tool} {payload}")
    current_level = live_results["ue_get_current_level"]["result"]
    if not current_level.get("available") or not current_level.get("currentLevelPath"):
        raise RuntimeError(f"Current level result is incomplete: {current_level}")
    pie_state = live_results["ue_get_pie_state"]["result"]
    if pie_state.get("state") not in {"stopped", "playing", "simulating"}:
        raise RuntimeError(f"PIE state is invalid: {pie_state}")

    output_log = live_results["ue_get_output_log"]["result"]
    if not output_log.get("available") or output_log.get("nextSequence") is None:
        raise RuntimeError(f"Output Log result is incomplete: {output_log}")
    if output_log.get("resultCount", 0) > 50 or output_log.get("matchedCount", 0) < output_log.get("resultCount", 0):
        raise RuntimeError(f"Output Log bounds are invalid: {output_log}")

    compile_errors = live_results["ue_get_compile_errors"]["result"]
    if compile_errors.get("diagnosticSource") != "captured-output-log" or compile_errors.get("historyComplete") is not False:
        raise RuntimeError(f"Compile diagnostic provenance is invalid: {compile_errors}")

    live_asset = live_results["ue_inspect_asset_live"]["result"]
    if live_asset.get("assetPath") != "/Game/UEAgentKitWriteTests/BP_PatchTarget.BP_PatchTarget":
        raise RuntimeError(f"Live asset path is invalid: {live_asset}")
    if live_asset.get("memory", {}).get("loadedByBridge") is not False:
        raise RuntimeError(f"Live asset inspection claimed it loaded the asset: {live_asset}")

    graph_selection = live_results["ue_get_blueprint_graph_selection"]["result"]
    if graph_selection.get("scope") != "ordinary-blueprint-editor":
        raise RuntimeError(f"Graph selection scope is invalid: {graph_selection}")
    if graph_selection.get("loadedByBridge") is not False:
        raise RuntimeError(f"Graph selection claimed it loaded an asset: {graph_selection}")
    if graph_selection.get("available"):
        graph = graph_selection.get("graph", {})
        if not graph.get("graphGuid") or graph_selection.get("selectedNodeCount", 0) > 100:
            raise RuntimeError(f"Graph selection result is incomplete or unbounded: {graph_selection}")
    elif graph_selection.get("reasonCode") not in {
        "no-ordinary-blueprint-editor",
        "no-focused-blueprint-graph",
        "blueprint-asset-unavailable",
    }:
        raise RuntimeError(f"Graph selection fallback is invalid: {graph_selection}")

    response_text = json.dumps(
        {
            "capabilities": capabilities,
            "projectStatus": project_status,
            "live": live_results,
        },
        ensure_ascii=False,
    )
    for secret in (secret_token, secret_port, str(descriptor_path), str(project.resolve())):
        if secret and secret in response_text:
            raise RuntimeError("MCP Live Editor responses exposed fixed endpoint, token, or local path")
    if "authToken" in response_text or "projectPathHash" in response_text:
        raise RuntimeError("MCP Live Editor responses exposed descriptor authentication fields")
    if _sha256(database) != before_hash or _snapshot(database.parent) != before_files:
        raise RuntimeError("Live Editor MCP reads modified the immutable SQLite index")

    return {
        "protocolVersion": initialized.protocolVersion,
        "serverName": initialized.serverInfo.name,
        "serverVersion": capabilities["server"]["version"],
        "tools": tool_names,
        "projectName": editor_status["result"]["projectName"],
        "engineVersion": editor_status["result"]["engineVersion"],
        "editorProcessId": editor_status["result"]["processId"],
        "liveSessionIdPresent": bool(editor_status["result"]["sessionId"]),
        "currentLevelAvailable": bool(current_level["available"]),
        "pieState": pie_state["state"],
        "selectionCount": live_results["ue_get_selection"]["result"]["count"],
        "openAssetCount": live_results["ue_get_open_assets"]["result"]["count"],
        "dirtyPackageCount": live_results["ue_get_dirty_assets"]["result"]["count"],
        "outputLogResultCount": output_log["resultCount"],
        "outputLogNextSequence": output_log["nextSequence"],
        "compileDiagnosticCount": compile_errors["diagnosticCount"],
        "compileHistoryComplete": compile_errors["historyComplete"],
        "liveAssetRegistryFound": live_asset["assetRegistry"]["found"],
        "liveAssetLoaded": live_asset["memory"]["loaded"],
        "liveAssetLoadedByBridge": live_asset["memory"]["loadedByBridge"],
        "blueprintGraphSelectionAvailable": bool(graph_selection.get("available")),
        "blueprintGraphSelectionReason": graph_selection.get("reasonCode", ""),
        "blueprintSelectedNodeCount": graph_selection.get("selectedNodeCount", 0),
        "secretsRedacted": True,
        "databaseHashUnchanged": True,
        "indexDirectoryUnchanged": True,
        "serverLogLines": len(error_log.read_text(encoding="utf-8").splitlines()),
    }


def _build_temporary_index(root: Path) -> Path:
    index_root = root / "index"
    index_root.mkdir(parents=True)
    database = index_root / "ueak-live.sqlite3"
    export_root = root / "export"
    write_export(
        export_root,
        [
            make_generic_asset(),
            make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True),
        ],
    )
    with open_database(database) as connection:
        result = build_index(connection, export_root, database)
    if (result.added, result.failed) != (2, 0):
        raise RuntimeError(f"Unexpected temporary index result: {result}")
    return database


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--error-log", type=Path)
    args = parser.parse_args()
    project = args.project.resolve()
    with tempfile.TemporaryDirectory(prefix="ueak_mcp_live_") as temporary_root:
        root = Path(temporary_root)
        database = args.database.resolve() if args.database is not None else _build_temporary_index(root)
        error_log = (
            args.error_log.resolve()
            if args.error_log is not None
            else root / "logs" / "server-stderr.log"
        )
        error_log.parent.mkdir(parents=True, exist_ok=True)
        report = asyncio.run(_run(database, project, error_log))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

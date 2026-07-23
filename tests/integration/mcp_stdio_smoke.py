from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
PYTHON_TESTS = TOOL_ROOT / "tests" / "python"
for path in (SRC_ROOT, PYTHON_TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from mcp import ClientSession  # noqa: E402
from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: E402
from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    GENERIC_TARGET,
    REVISION_A,
    make_asset,
    make_generic_asset,
    write_export,
)
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_snapshot(directory: Path) -> dict[str, str]:
    return {
        path.name: _sha256(path)
        for path in directory.iterdir()
        if path.is_file()
    }


async def _run_client(database_path: Path, error_log_path: Path) -> dict[str, object]:
    before_hash = _sha256(database_path)
    before_files = _file_snapshot(database_path.parent)
    parameters = StdioServerParameters(
        command="powershell.exe",
        args=[
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
            "-Database",
            str(database_path),
        ],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    with error_log_path.open("w", encoding="utf-8", newline="\n") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialize_result = await session.initialize()
                tools_result = await session.list_tools()
                capabilities_result = await session.call_tool("ue_get_capabilities", {})
                project_status_result = await session.call_tool("ue_get_project_status", {})
                search_result = await session.call_tool(
                    "ue_search",
                    {"query": "生命值", "scope": "symbols", "kind": "variable"},
                )
                paged_search_result = await session.call_tool(
                    "ue_search",
                    {"scope": "symbols", "asset_path": ASSET_A, "limit": 1},
                )
                paged_search = paged_search_result.structuredContent
                if not paged_search or not paged_search["pagination"]["continuationToken"]:
                    raise RuntimeError(f"Search continuation was not returned: {paged_search}")
                continued_search_result = await session.call_tool(
                    "ue_search",
                    {"continuation_token": paged_search["pagination"]["continuationToken"]},
                )
                asset_result = await session.call_tool("ue_get_asset", {"asset_path": ASSET_A})
                section_asset_result = await session.call_tool(
                    "ue_get_asset",
                    {"asset_path": ASSET_A, "sections": ["identity", "symbols"], "symbol_limit": 1},
                )
                section_asset = section_asset_result.structuredContent
                if not section_asset or not section_asset["sectionPagination"]["symbols"]["continuationToken"]:
                    raise RuntimeError(f"Asset section continuation was not returned: {section_asset}")
                continued_section_result = await session.call_tool(
                    "ue_get_asset",
                    {
                        "continuation_token": section_asset["sectionPagination"]["symbols"]["continuationToken"]
                    },
                )
                references_result = await session.call_tool(
                    "ue_find_references",
                    {"target_asset_path": GENERIC_TARGET},
                )
                rejected_result = await session.call_tool("ue_find_references", {})

    capabilities = capabilities_result.structuredContent
    project_status = project_status_result.structuredContent
    search = search_result.structuredContent
    paged_search = paged_search_result.structuredContent
    continued_search = continued_search_result.structuredContent
    asset = asset_result.structuredContent
    section_asset = section_asset_result.structuredContent
    continued_section = continued_section_result.structuredContent
    references = references_result.structuredContent
    rejected = rejected_result.structuredContent
    tool_names = [tool.name for tool in tools_result.tools]
    if tool_names != ["ue_get_capabilities", "ue_get_project_status", "ue_search", "ue_get_asset", "ue_find_references"]:
        raise RuntimeError(f"Unexpected MCP tools: {tool_names}")
    if not capabilities or capabilities["server"]["mode"] != "read-only":
        raise RuntimeError(f"Capabilities lookup failed: {capabilities}")
    if not project_status or project_status["freshness"]["state"] != "unknown":
        raise RuntimeError(f"Project status lookup failed: {project_status}")
    if not search or search["results"][0]["name"] != "生命值":
        raise RuntimeError(f"Unicode symbol search failed: {search}")
    if not continued_search or continued_search["pagination"]["source"] != "continuation-token":
        raise RuntimeError(f"Search continuation failed: {continued_search}")
    if paged_search["results"][0]["stable_id"] == continued_search["results"][0]["stable_id"]:
        raise RuntimeError("Search continuation repeated the first result")
    if not asset or not asset["found"] or asset["asset"]["asset_path"] != ASSET_A:
        raise RuntimeError(f"Asset lookup failed: {asset}")
    if not section_asset or set(section_asset["requestedSections"]) != {"identity", "symbols"}:
        raise RuntimeError(f"Asset section selection failed: {section_asset}")
    if not continued_section or continued_section["sectionPagination"]["symbols"]["source"] != "continuation-token":
        raise RuntimeError(f"Asset section continuation failed: {continued_section}")
    if not references or references["results"][0]["target_asset_path"] != GENERIC_TARGET:
        raise RuntimeError(f"Reference lookup failed: {references}")
    if not rejected or rejected["ok"] or rejected["error"]["code"] != "invalid-arguments":
        raise RuntimeError(f"Invalid-argument rejection failed: {rejected}")
    after_files = _file_snapshot(database_path.parent)
    if _sha256(database_path) != before_hash:
        raise RuntimeError("MCP queries changed the SQLite database")
    if after_files != before_files:
        raise RuntimeError(
            f"MCP queries changed index-directory files: before={before_files}, after={after_files}"
        )

    return {
        "protocolVersion": initialize_result.protocolVersion,
        "serverName": initialize_result.serverInfo.name,
        "tools": tool_names,
        "serverVersion": capabilities["server"]["version"],
        "projectStatusAvailable": True,
        "searchResultCount": search["pagination"]["resultCount"],
        "searchContinuationPassed": True,
        "assetFound": asset["found"],
        "assetSectionContinuationPassed": True,
        "referenceResultCount": references["pagination"]["resultCount"],
        "invalidArgumentsRejected": True,
        "databaseHashUnchanged": True,
        "indexDirectoryUnchanged": True,
        "serverLogLines": len(error_log_path.read_text(encoding="utf-8").splitlines()),
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ueak_mcp_stdio_") as temporary_root:
        root = Path(temporary_root)
        database_path = root / "ueak.sqlite3"
        export_root = root / "export"
        error_log_path = root / "logs" / "server-stderr.log"
        error_log_path.parent.mkdir(parents=True)
        write_export(
            export_root,
            [
                make_generic_asset(),
                make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True),
            ],
        )
        with open_database(database_path) as connection:
            result = build_index(connection, export_root, database_path)
        if (result.added, result.failed) != (2, 0):
            raise RuntimeError(f"Unexpected index result: {result}")
        report = asyncio.run(_run_client(database_path, error_log_path))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

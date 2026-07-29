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
from test_indexer_queries import ASSET_A, REVISION_A, make_asset, write_export  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.memory_service import ProjectMemoryService  # noqa: E402
from ue_agent_kit.tool_registry import tool_names_for_mode  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_snapshot(directory: Path) -> dict[str, str]:
    return {
        path.name: _sha256(path)
        for path in directory.iterdir()
        if path.is_file()
    }


async def _run_client(
    database_path: Path,
    memory_path: Path,
    error_log_path: Path,
) -> dict[str, object]:
    before_hash = _sha256(database_path)
    before_files = _file_snapshot(database_path.parent)
    parameters = StdioServerParameters(
        command=sys.executable,
        args=[
            str(TOOL_ROOT / "scripts" / "ue-agent-mcp.py"),
            "--database",
            str(database_path),
            "--enable-project-memory",
            "--memory-database",
            str(memory_path),
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
                status_result = await session.call_tool("ue_get_project_status", {})
                rule_result = await session.call_tool(
                    "ue_memory_add_rule",
                    {
                        "subject_key": "rule:text-format",
                        "title": "Text format",
                        "body": "Tracked text files use UTF-8 without BOM and CRLF.",
                        "source_ref": "integration:user-confirmed",
                        "scopes": [
                            {
                                "scopeType": "project",
                                "scopeKey": "测试项目",
                            }
                        ],
                    },
                )
                rule = rule_result.structuredContent
                if not rule or not rule["ok"]:
                    raise RuntimeError(f"Memory rule creation failed: {rule}")
                search_result = await session.call_tool(
                    "ue_memory_search",
                    {"query": "CRLF", "record_types": ["projectRule"]},
                )
                get_result = await session.call_tool(
                    "ue_memory_get",
                    {"record_id": rule["record"]["recordId"]},
                )
                finding_result = await session.call_tool(
                    "ue_memory_record_finding",
                    {
                        "record_type": "projectFact",
                        "subject_key": "asset:test:revision",
                        "title": "Indexed test asset revision",
                        "body": "The test asset revision was observed in the immutable index.",
                        "source_kind": "tool-observed",
                        "source_ref": "integration:index",
                        "revision_set": [
                            {
                                "assetPath": ASSET_A,
                                "revision": f"sha256:{REVISION_A}",
                                "revisionStable": True,
                            }
                        ],
                    },
                )
                task_result = await session.call_tool(
                    "ue_memory_record_task",
                    {
                        "task_key": "memory-smoke",
                        "title": "Validate Project Memory stdio",
                        "conclusion": "The fixed-project Memory stdio workflow passed.",
                        "outcome": "succeeded",
                        "patch_ref": "patch:memory-smoke",
                        "backup_manifest_ref": "backup-manifest:memory-smoke",
                        "validation_evidence_ref": "validation-evidence:memory-smoke",
                        "revision_set": [
                            {
                                "assetPath": ASSET_A,
                                "revision": f"sha256:{REVISION_A}",
                                "revisionStable": True,
                            }
                        ],
                        "scopes": [
                            {
                                "scopeType": "asset",
                                "scopeKey": ASSET_A,
                            }
                        ],
                    },
                )
                validate_result = await session.call_tool("ue_memory_validate", {})
                missing_result = await session.call_tool(
                    "ue_memory_get",
                    {"record_id": "mem_00000000000000000000000000000000"},
                )

    capabilities = capabilities_result.structuredContent
    project_status = status_result.structuredContent
    search = search_result.structuredContent
    fetched = get_result.structuredContent
    finding = finding_result.structuredContent
    task = task_result.structuredContent
    validated = validate_result.structuredContent
    missing = missing_result.structuredContent
    tool_names = [tool.name for tool in tools_result.tools]
    expected_tools = tool_names_for_mode(memory_enabled=True)
    if tool_names != expected_tools:
        raise RuntimeError(f"Unexpected Memory MCP tools: {tool_names}")
    if not capabilities or capabilities["server"]["mode"] != "fixed-project-memory":
        raise RuntimeError(f"Memory capabilities failed: {capabilities}")
    if capabilities["projectMemory"]["tools"] != expected_tools[5:]:
        raise RuntimeError(f"Memory Tool contract mismatch: {capabilities}")
    if not project_status or project_status["projectMemory"]["state"] != "available":
        raise RuntimeError(f"Memory project status failed: {project_status}")
    if not search or search["resultCount"] != 1:
        raise RuntimeError(f"Memory search failed: {search}")
    if not fetched or fetched["record"]["recordId"] != rule["record"]["recordId"]:
        raise RuntimeError(f"Memory exact get failed: {fetched}")
    if not finding or finding["record"]["status"] != "valid":
        raise RuntimeError(f"Tool-observed Memory finding failed: {finding}")
    if not task or task["record"]["status"] != "valid":
        raise RuntimeError(f"Evidence-bound Task Record failed: {task}")
    if [item["artifactKind"] for item in task["record"]["artifacts"]] != [
        "patch",
        "backupManifest",
        "validationEvidence",
    ]:
        raise RuntimeError(f"Task Record Artifact contract failed: {task}")
    if not validated or validated["staleRecordIds"]:
        raise RuntimeError(f"Memory Revision validation failed: {validated}")
    if not missing or missing["ok"] or missing["error"]["code"] != "memory-record-not-found":
        raise RuntimeError(f"Memory missing-record error failed: {missing}")
    if _sha256(database_path) != before_hash:
        raise RuntimeError("Project Memory MCP changed the immutable SQLite index")
    if _file_snapshot(database_path.parent) != before_files:
        raise RuntimeError("Project Memory MCP changed files beside the immutable index")

    memory_status = ProjectMemoryService(
        database_path=memory_path,
        project_key="测试项目",
    ).status()
    if memory_status.record_count != 3:
        raise RuntimeError(f"Unexpected persistent Memory record count: {memory_status}")

    return {
        "protocolVersion": initialize_result.protocolVersion,
        "serverName": initialize_result.serverInfo.name,
        "serverMode": capabilities["server"]["mode"],
        "tools": tool_names,
        "projectKey": capabilities["projectMemory"]["projectKey"],
        "ruleCreated": True,
        "findingCreated": True,
        "taskCreated": True,
        "searchPassed": True,
        "exactGetPassed": True,
        "revisionValidationPassed": True,
        "missingRecordRejected": True,
        "persistentRecordCount": memory_status.record_count,
        "indexHashUnchanged": True,
        "indexDirectoryUnchanged": True,
        "serverLogLines": len(error_log_path.read_text(encoding="utf-8").splitlines()),
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ueak_mcp_memory_") as temporary_root:
        root = Path(temporary_root)
        database_path = root / "index" / "ueak.sqlite3"
        memory_path = root / "memory" / "project-memory.sqlite3"
        export_root = root / "export"
        error_log_path = root / "logs" / "server-stderr.log"
        error_log_path.parent.mkdir(parents=True)
        write_export(
            export_root,
            [make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True)],
        )
        with open_database(database_path) as connection:
            result = build_index(connection, export_root, database_path)
        if (result.added, result.failed) != (1, 0):
            raise RuntimeError(f"Unexpected index result: {result}")
        report = asyncio.run(_run_client(database_path, memory_path, error_log_path))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


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
    REVISION_A,
    make_asset,
    make_generic_asset,
    write_export,
)
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402


PROTOCOL_VERSION = "2025-11-25"
EXPECTED_READ_TOOLS = [
    "ue_get_capabilities",
    "ue_get_project_status",
    "ue_search",
    "ue_get_asset",
    "ue_find_references",
]
FORBIDDEN_ARGUMENTS = {
    "database",
    "project",
    "project_path",
    "engine_root",
    "policy",
    "revision_export",
    "work_root",
    "backup_root",
    "command",
}


def _server_command(database_path: Path) -> list[str]:
    return [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(TOOL_ROOT / "scripts" / "RunMcp.ps1"),
        "-Database",
        str(database_path),
    ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _validate_tool_schemas(tools: list[dict[str, Any]]) -> dict[str, object]:
    names = [str(tool.get("name", "")) for tool in tools]
    if names != EXPECTED_READ_TOOLS:
        raise RuntimeError(f"Unexpected Tool order: {names}")
    for tool in tools:
        name = str(tool.get("name", ""))
        description = tool.get("description")
        schema = tool.get("inputSchema")
        annotations = tool.get("annotations")
        if not name.startswith("ue_") or len(name) > 64:
            raise RuntimeError(f"Tool name is not host-safe: {name!r}")
        if not isinstance(description, str) or not description.strip():
            raise RuntimeError(f"Tool description is missing: {name}")
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise RuntimeError(f"Tool inputSchema is not a JSON object schema: {name}: {schema}")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise RuntimeError(f"Tool properties are invalid: {name}: {properties}")
        leaked = set(properties).intersection(FORBIDDEN_ARGUMENTS)
        if leaked:
            raise RuntimeError(f"Tool exposes fixed server configuration: {name}: {sorted(leaked)}")
        if not isinstance(annotations, dict):
            raise RuntimeError(f"Tool annotations are missing: {name}")
        for field in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
            if not isinstance(annotations.get(field), bool):
                raise RuntimeError(f"Tool annotation {field} is missing: {name}: {annotations}")
    return {
        "toolCount": len(tools),
        "toolNames": names,
        "jsonSchemaObjects": True,
        "annotationsComplete": True,
        "fixedConfigurationHidden": True,
    }


def _decode_text_content(result: dict[str, Any]) -> dict[str, Any]:
    content = result.get("content")
    if not isinstance(content, list) or len(content) != 1:
        raise RuntimeError(f"Unexpected Tool text content: {content}")
    item = content[0]
    if not isinstance(item, dict) or item.get("type") != "text" or not isinstance(item.get("text"), str):
        raise RuntimeError(f"Unexpected Tool content item: {item}")
    decoded = json.loads(item["text"])
    if not isinstance(decoded, dict):
        raise RuntimeError(f"Tool text fallback is not a JSON object: {decoded}")
    return decoded


async def _official_client(database_path: Path, error_log_path: Path) -> dict[str, object]:
    command = _server_command(database_path)
    parameters = StdioServerParameters(
        command=command[0],
        args=command[1:],
        cwd=TOOL_ROOT,
        encoding="utf-8",
        encoding_error_handler="replace",
    )
    with error_log_path.open("w", encoding="utf-8", newline="\n") as error_log:
        async with stdio_client(parameters, errlog=error_log) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                initialized = await session.initialize()
                listed = await session.list_tools()
                called = await session.call_tool("ue_get_capabilities", {})
    tools = [tool.model_dump(by_alias=True, exclude_none=True) for tool in listed.tools]
    schema_report = _validate_tool_schemas(tools)
    structured = called.structuredContent
    if not isinstance(structured, dict) or structured.get("ok") is not True:
        raise RuntimeError(f"Official ClientSession lost structuredContent: {structured}")
    if structured.get("tool") != "ue_get_capabilities":
        raise RuntimeError(f"Official ClientSession returned the wrong Tool payload: {structured}")
    return {
        "protocolVersion": initialized.protocolVersion,
        "serverName": initialized.serverInfo.name,
        "serverVersion": structured["server"]["version"],
        "structuredContent": True,
        "schema": schema_report,
        "serverLogLines": len(error_log_path.read_text(encoding="utf-8").splitlines()),
    }


class RawJsonRpcClient:
    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self.process = process
        self.next_id = 1

    async def send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        await self._write(message)
        while True:
            response = await self._read()
            if response.get("id") != request_id:
                continue
            if "error" in response:
                raise RuntimeError(f"JSON-RPC request failed: {method}: {response['error']}")
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"JSON-RPC result is not an object: {method}: {result}")
            return result

    async def _write(self, message: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise RuntimeError("Raw MCP stdin is unavailable")
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.process.stdin.write(payload.encode("utf-8"))
        await self.process.stdin.drain()

    async def _read(self) -> dict[str, Any]:
        if self.process.stdout is None:
            raise RuntimeError("Raw MCP stdout is unavailable")
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=15)
        if not line:
            raise RuntimeError("Raw MCP server closed stdout before returning a response")
        response = json.loads(line.decode("utf-8"))
        if not isinstance(response, dict):
            raise RuntimeError(f"Raw MCP response is not an object: {response}")
        return response


async def _raw_jsonrpc_client(database_path: Path) -> dict[str, object]:
    command = _server_command(database_path)
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(TOOL_ROOT),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    client = RawJsonRpcClient(process)
    try:
        initialized = await client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "ue-agent-kit-raw-compatibility", "version": "1.0"},
            },
        )
        await client.send_notification("notifications/initialized")
        listed = await client.request("tools/list")
        tools = listed.get("tools")
        if not isinstance(tools, list) or not all(isinstance(tool, dict) for tool in tools):
            raise RuntimeError(f"Raw tools/list returned invalid tools: {listed}")
        schema_report = _validate_tool_schemas(tools)

        capabilities_result = await client.request(
            "tools/call",
            {"name": "ue_get_capabilities", "arguments": {}},
        )
        structured = capabilities_result.get("structuredContent")
        if not isinstance(structured, dict) or structured.get("ok") is not True:
            raise RuntimeError(f"Raw tools/call lost structuredContent: {capabilities_result}")
        text_fallback = _decode_text_content(capabilities_result)
        if text_fallback != structured:
            raise RuntimeError("Raw tools/call text fallback differs from structuredContent")

        rejected_result = await client.request(
            "tools/call",
            {"name": "ue_find_references", "arguments": {}},
        )
        rejected = rejected_result.get("structuredContent")
        if not isinstance(rejected, dict) or rejected.get("ok") is not False:
            raise RuntimeError(f"Raw Tool error envelope was not returned: {rejected_result}")
        if rejected.get("error", {}).get("code") != "invalid-arguments":
            raise RuntimeError(f"Raw Tool error code was unstable: {rejected}")
        if _decode_text_content(rejected_result) != rejected:
            raise RuntimeError("Raw Tool error text fallback differs from structuredContent")
    finally:
        if process.stdin is not None:
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass
        try:
            await asyncio.wait_for(process.wait(), timeout=10)
        except TimeoutError:
            process.terminate()
            await process.wait()
    stderr = b""
    if process.stderr is not None:
        stderr = await process.stderr.read()
    if process.returncode not in (0, None):
        raise RuntimeError(f"Raw MCP server exited with {process.returncode}: {stderr.decode('utf-8', errors='replace')}")
    return {
        "protocolVersion": initialized.get("protocolVersion"),
        "serverName": initialized.get("serverInfo", {}).get("name"),
        "serverVersion": structured["server"]["version"],
        "newlineDelimitedJsonRpc": True,
        "structuredContent": True,
        "jsonTextFallback": True,
        "stableErrorEnvelope": True,
        "schema": schema_report,
        "serverLogLines": len(stderr.decode("utf-8", errors="replace").splitlines()),
    }


async def _run_matrix(database_path: Path, log_root: Path) -> dict[str, object]:
    before_hash = _sha256(database_path)
    official = await _official_client(database_path, log_root / "official-client-stderr.log")
    raw = await _raw_jsonrpc_client(database_path)
    if official["protocolVersion"] != raw["protocolVersion"]:
        raise RuntimeError(f"Client protocol negotiation differed: official={official}, raw={raw}")
    if official["schema"] != raw["schema"]:
        raise RuntimeError(f"Client Tool schemas differed: official={official['schema']}, raw={raw['schema']}")
    if _sha256(database_path) != before_hash:
        raise RuntimeError("MCP compatibility clients changed the immutable SQLite database")
    return {
        "matrixVersion": "1.0",
        "protocolVersion": official["protocolVersion"],
        "serverVersion": official["serverVersion"],
        "officialPythonClient": official,
        "rawJsonRpcClient": raw,
        "claudeCodeContract": {
            "localStdioTransport": True,
            "jsonSchemaTools": True,
            "toolAnnotations": True,
            "fixedConfigurationHidden": True,
        },
        "chatGptProtocolContract": {
            "scope": "MCP protocol response compatibility; hosted ChatGPT UI was not exercised.",
            "toolsList": True,
            "structuredContent": True,
            "jsonTextFallback": True,
            "stableErrorEnvelope": True,
        },
        "databaseHashUnchanged": True,
    }


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="ueak_mcp_clients_") as temporary_root:
        root = Path(temporary_root)
        database_path = root / "ueak.sqlite3"
        export_root = root / "export"
        log_root = root / "logs"
        log_root.mkdir(parents=True)
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
        report = asyncio.run(_run_matrix(database_path, log_root))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

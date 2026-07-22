from __future__ import annotations

import asyncio
import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from test_indexer_queries import (  # noqa: E402
    ASSET_A,
    GENERIC_ASSET,
    GENERIC_TARGET,
    REVISION_A,
    make_asset,
    make_generic_asset,
    write_export,
)
from ue_agent_kit.agent_api import (  # noqa: E402
    MAX_MCP_SEARCH_LIMIT,
    IndexQueryService,
)
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.mcp_server import create_mcp_server, main as mcp_main  # noqa: E402


MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class McpServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="ueak_mcp_")
        self.temp_root = Path(self.temporary_directory.name)
        self.database_path = self.temp_root / "索引" / "ueak.sqlite3"
        export_root = self.temp_root / "export"
        write_export(
            export_root,
            [
                make_generic_asset(),
                make_asset(ASSET_A, profile="logic", revision=REVISION_A, rich=True),
            ],
        )
        with open_database(self.database_path) as connection:
            result = build_index(connection, export_root, self.database_path)
        self.assertEqual((result.added, result.failed), (2, 0))
        self.service = IndexQueryService(self.database_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_index_service_is_read_only_and_returns_stable_envelopes(self) -> None:
        before_hash = sha256(self.database_path)
        status = self.service.check()
        assets = self.service.search("StaticMesh")
        symbols = self.service.search("生命值", scope="symbols", kind="variable")
        asset = self.service.get_asset(ASSET_A)
        references = self.service.find_references(target_asset_path=GENERIC_TARGET)
        after_hash = sha256(self.database_path)

        self.assertEqual(before_hash, after_hash)
        self.assertEqual(status["schemaVersion"], "1.0")
        self.assertEqual(status["tool"], "ue_index_status")
        self.assertTrue(status["readOnly"])
        self.assertEqual(status["stats"]["counts"]["assets"], 2)
        self.assertEqual(assets["tool"], "ue_search")
        self.assertEqual(assets["scope"], "assets")
        self.assertEqual([item["asset_path"] for item in assets["results"]], [GENERIC_ASSET])
        self.assertEqual(symbols["scope"], "symbols")
        self.assertEqual(symbols["results"][0]["name"], "生命值")
        self.assertTrue(asset["found"])
        self.assertEqual(asset["asset"]["asset_path"], ASSET_A)
        self.assertEqual(asset["limits"]["graphs"], 100)
        self.assertEqual(references["results"][0]["target_asset_path"], GENERIC_TARGET)

    def test_service_requires_bounded_and_filtered_queries(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            self.service.search("", limit=MAX_MCP_SEARCH_LIMIT + 1)
        with self.assertRaisesRegex(ValueError, "at least one reference filter"):
            self.service.find_references()
        with self.assertRaisesRegex(ValueError, "graph_limit must not exceed"):
            self.service.get_asset(ASSET_A, graph_limit=501)
        with self.assertRaisesRegex(ValueError, "only for symbol search"):
            self.service.search("x", asset_path=ASSET_A)
        with self.assertRaisesRegex(ValueError, "only for asset search"):
            self.service.search("x", scope="symbols", asset_class="Blueprint")

    def test_missing_asset_is_not_an_exception(self) -> None:
        result = self.service.get_asset("/Game/Missing/BP_None.BP_None")
        self.assertTrue(result["ok"])
        self.assertFalse(result["found"])
        self.assertNotIn("asset", result)

    def test_check_mode_outputs_json_without_starting_protocol(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = mcp_main(["--database", str(self.database_path), "--check"])
        self.assertEqual(exit_code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["tool"], "ue_index_status")
        self.assertEqual(payload["stats"]["counts"]["assets"], 2)

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_registers_only_three_read_only_tools(self) -> None:
        server = create_mcp_server(self.database_path)
        tools = asyncio.run(server.list_tools())
        self.assertEqual(
            [tool.name for tool in tools],
            ["ue_search", "ue_get_asset", "ue_find_references"],
        )
        for tool in tools:
            self.assertNotIn("database", tool.inputSchema.get("properties", {}))

        content, payload = asyncio.run(
            server.call_tool(
                "ue_search",
                {"query": "生命值", "scope": "symbols", "kind": "variable"},
            )
        )
        self.assertEqual(len(content), 1)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["results"][0]["name"], "生命值")

        rejected_content, error_payload = asyncio.run(server.call_tool("ue_find_references", {}))
        self.assertEqual(len(rejected_content), 1)
        self.assertFalse(error_payload["ok"])
        self.assertEqual(error_payload["error"]["code"], "invalid-arguments")

    def test_active_sqlite_sidecar_is_rejected(self) -> None:
        sidecar = Path(str(self.database_path) + "-wal")
        sidecar.write_bytes(b"active-writer")
        with self.assertRaisesRegex(RuntimeError, "active SQLite sidecar"):
            self.service.check()

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_fastmcp_returns_quiescent_snapshot_error(self) -> None:
        server = create_mcp_server(self.database_path)
        sidecar = Path(str(self.database_path) + "-wal")
        sidecar.write_bytes(b"active-writer")
        content, payload = asyncio.run(server.call_tool("ue_search", {"query": "Actor"}))
        self.assertEqual(len(content), 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "index-not-quiescent")
        self.assertNotIn(str(self.database_path), json.dumps(payload, ensure_ascii=False))

    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_runtime_database_loss_returns_sanitized_error(self) -> None:
        database_text = str(self.database_path)
        server = create_mcp_server(self.database_path)
        self.database_path.unlink()
        content, payload = asyncio.run(server.call_tool("ue_search", {"query": "Actor"}))
        self.assertEqual(len(content), 1)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "database-not-found")
        self.assertEqual(
            payload["error"]["message"],
            "The configured UE Agent Kit database was not found.",
        )
        self.assertNotIn(database_text, json.dumps(payload, ensure_ascii=False))

    def test_mcp_packaging_and_stdio_boundary_are_explicit(self) -> None:
        pyproject = (TOOL_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (TOOL_ROOT / "requirements-mcp.txt").read_text(encoding="utf-8")
        server_source = (SRC_ROOT / "ue_agent_kit" / "mcp_server.py").read_text(encoding="utf-8")
        setup_source = (TOOL_ROOT / "scripts" / "setup_python.ps1").read_text(encoding="utf-8")
        runner_source = (TOOL_ROOT / "scripts" / "RunMcp.ps1").read_text(encoding="utf-8")

        self.assertIn('mcp = ["mcp>=1.27,<2"]', pyproject)
        self.assertIn("mcp>=1.27,<2", requirements)
        self.assertIn('server.run(transport="stdio")', server_source)
        service_source = (SRC_ROOT / "ue_agent_kit" / "agent_api.py").read_text(encoding="utf-8")
        database_source = (SRC_ROOT / "ue_agent_kit" / "database.py").read_text(encoding="utf-8")
        self.assertIn("immutable=True", service_source)
        self.assertIn("active SQLite sidecar", service_source)
        self.assertIn("immutable=1", database_source)
        self.assertNotIn('transport="streamable-http"', server_source)
        self.assertNotIn(".sse_app(", server_source)
        self.assertIn("[switch]$WithMcp", setup_source)
        self.assertIn("requirements-mcp.txt", setup_source)
        self.assertIn("Do not write informational text to stdout", runner_source)
        self.assertNotIn("Write-Host", runner_source)
        integration_source = (
            TOOL_ROOT / "tests" / "integration" / "mcp_stdio_smoke.py"
        ).read_text(encoding="utf-8")
        integration_runner = (TOOL_ROOT / "scripts" / "TestMcpStdio.ps1").read_text(encoding="utf-8")
        for token in (
            "StdioServerParameters",
            "ClientSession",
            "databaseHashUnchanged",
            "indexDirectoryUnchanged",
            "invalidArgumentsRejected",
        ):
            self.assertIn(token, integration_source)
        self.assertIn("mcp_stdio_smoke.py", integration_runner)

        example = json.loads(
            (TOOL_ROOT / "examples" / "mcp" / "claude-code.example.json").read_text(encoding="utf-8")
        )
        config = example["mcpServers"]["ue-agent-kit"]
        self.assertEqual(config["type"], "stdio")
        self.assertEqual(config["command"], "powershell.exe")
        self.assertIn("RunMcp.ps1", " ".join(config["args"]))
        self.assertIn("-Database", config["args"])
        self.assertNotIn("-ProjectPath", config["args"])
        self.assertNotIn("-Policy", config["args"])


if __name__ == "__main__":
    unittest.main()

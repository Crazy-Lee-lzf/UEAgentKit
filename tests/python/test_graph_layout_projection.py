from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import layout_projection_fixtures as fixtures  # noqa: E402
from ue_agent_kit.database import open_database  # noqa: E402
from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.experimental_graph_layout import build_apply_request  # noqa: E402
from ue_agent_kit.graph_layout_projection import (  # noqa: E402
    build_preview_from_projection,
    fetch_layout_projection,
    projection_hashes,
    projection_to_canonical_document,
)
from ue_agent_kit.mcp_graph_layout_preview import (  # noqa: E402
    PREVIEW_TOOL_NAME,
    register_graph_layout_preview_tool,
)
from ue_agent_kit.mcp_live_tools import MAX_GRAPH_CONTEXT_BYTES  # noqa: E402
from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


class FakeServer:
    def __init__(self) -> None:
        self.tools = {}

    def tool(self, *, annotations):
        def decorate(fn):
            self.tools[fn.__name__] = fn
            return fn

        return decorate


def _error_response(tool, error, *, read_only):
    return {
        "schemaVersion": "1.0",
        "tool": tool,
        "ok": False,
        "readOnly": read_only,
        "error": {"code": getattr(error, "code", "error"), "message": str(error)},
    }


def _preview_tool(service):
    server = FakeServer()
    register_graph_layout_preview_tool(
        server=server,
        live_editor_service=service,
        read_annotations=object(),
        error_response=_error_response,
    )
    return server.tools[PREVIEW_TOOL_NAME]


class LargeProjectionTests(unittest.TestCase):
    def test_134_node_projection_pages_and_builds_a_complete_plan(self) -> None:
        full = fixtures.build_projection(134)
        service = fixtures.FakeProjectionService(full)
        projection = fetch_layout_projection(service, fixtures.preflight())
        self.assertEqual(projection["pageCount"], 3)
        self.assertEqual(projection["nodeCount"], 134)
        self.assertEqual(projection["pinCount"], 268)
        self.assertEqual(projection["edgeCount"], 133)
        self.assertEqual(len(projection["nodes"]), 134)
        self.assertEqual(projection["pageLimit"], 64)
        self.assertGreater(projection["maxPageBytes"], 0)

        preview = build_preview_from_projection(fixtures.preflight(), projection)
        self.assertEqual(preview["contextMode"], "layout-projection")
        self.assertEqual(preview["nodeCount"], 134)
        self.assertEqual(len(preview["positions"]), 134)
        self.assertFalse(preview["wroteBlueprint"])
        self.assertFalse(preview["savedPackage"])
        self.assertEqual(preview["graphGuid"], fixtures.GRAPH_GUID)
        self.assertEqual(preview["packageRevision"], fixtures.REVISION)
        self.assertEqual(preview["projection"]["nodeSetHash"], projection["nodeSetHash"])
        self.assertTrue(preview["plan"]["planId"].startswith("layout:"))

        request = build_apply_request(preview)
        self.assertEqual(request["graphGuid"], fixtures.GRAPH_GUID)
        self.assertEqual(request["expectedPackageRevision"], fixtures.REVISION)
        self.assertEqual(request["expectedGraphFingerprint"], fixtures.FINGERPRINT)
        self.assertEqual(len(request["plan"]["positions"]), 134)

    def test_two_node_projection_still_works(self) -> None:
        full = fixtures.build_projection(2)
        service = fixtures.FakeProjectionService(full)
        projection = fetch_layout_projection(service, fixtures.preflight(2))
        self.assertEqual(projection["pageCount"], 1)
        preview = build_preview_from_projection(fixtures.preflight(2), projection)
        self.assertEqual(preview["nodeCount"], 2)
        self.assertEqual(len(preview["positions"]), 2)

    def test_page_budget_exhaustion_retries_a_smaller_page(self) -> None:
        full = fixtures.build_projection(134)
        service = fixtures.FakeProjectionService(full, page_limit_cap=32)
        projection = fetch_layout_projection(service, fixtures.preflight())
        self.assertEqual(projection["pageLimit"], 32)
        self.assertEqual(projection["nodeCount"], 134)
        self.assertGreater(projection["pageCount"], 3)

    def test_stale_preflight_after_paging_is_rejected(self) -> None:
        full = fixtures.build_projection(8)
        stale = fixtures.preflight(8, packageRevision="sha256:" + "9" * 64)
        service = fixtures.FakeProjectionService(full, after_preflight=stale)
        with self.assertRaises(LiveEditorError) as raised:
            fetch_layout_projection(service, fixtures.preflight(8))
        self.assertEqual(raised.exception.code, "graph-layout-projection-stale")

    def test_hash_mismatch_is_rejected(self) -> None:
        full = fixtures.build_projection(4)
        service = fixtures.FakeProjectionService(full)
        original = service.call_method

        def broken_page(name, params, **kwargs):
            if name != fixtures.PROJECTION_METHOD:
                return original(name, params, **kwargs)
            page = fixtures.make_page(full, int(params.get("offset", 0)), int(params.get("limit", 64)))
            page["nodeSetHash"] = "sha256:" + "0" * 64
            return page

        service.call_method = broken_page  # type: ignore[method-assign]
        with self.assertRaises(LiveEditorError) as raised:
            fetch_layout_projection(service, fixtures.preflight(4))
        self.assertEqual(raised.exception.code, "graph-layout-projection-hash-mismatch")

    def test_incomplete_node_set_is_rejected(self) -> None:
        full = fixtures.build_projection(4)
        service = fixtures.FakeProjectionService(full)
        original = service.call_method

        def missing_page(name, params, **kwargs):
            if name != fixtures.PROJECTION_METHOD:
                return original(name, params, **kwargs)
            return fixtures.make_page(full, 0, 64, nodes_override=[])

        service.call_method = missing_page  # type: ignore[method-assign]
        with self.assertRaises(LiveEditorError) as raised:
            fetch_layout_projection(service, fixtures.preflight(4))
        self.assertEqual(raised.exception.code, "graph-layout-projection-incomplete")

    def test_recomputed_hashes_match_the_native_contract(self) -> None:
        full = fixtures.build_projection(134)
        hashes = projection_hashes(full["nodes"], full["pins"], full["edges"])
        self.assertEqual(hashes["nodeSetHash"], full["nodeSetHash"])
        self.assertEqual(hashes["pinSetHash"], full["pinSetHash"])
        self.assertEqual(hashes["edgeSetHash"], full["edgeSetHash"])
        self.assertEqual(hashes["projectionHash"], full["projectionHash"])

    def test_canonical_reconstruction_has_no_truncation(self) -> None:
        full = fixtures.build_projection(134)
        document = projection_to_canonical_document(full)
        self.assertEqual(document["schemaVersion"], "1.1")
        self.assertEqual(len(document["graphs"]), 1)
        graph = document["graphs"][0]
        self.assertEqual(graph["nodeCount"], 134)
        self.assertEqual(len(graph["nodes"]), 134)
        self.assertEqual(sum(len(node["pins"]) for node in graph["nodes"]), 268)

    def test_old_full_context_limit_is_unchanged(self) -> None:
        self.assertEqual(MAX_GRAPH_CONTEXT_BYTES, 700 * 1024)

    def test_preview_tool_uses_projection_when_the_bridge_exposes_it(self) -> None:
        service = fixtures.FakeProjectionService(fixtures.build_projection(134))
        tool = _preview_tool(service)
        payload = tool()
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["readOnly"])
        self.assertEqual(payload["result"]["contextMode"], "layout-projection")
        self.assertEqual(payload["result"]["nodeCount"], 134)
        self.assertEqual(len(payload["result"]["positions"]), 134)
        methods = [name for name, _ in service.method_calls]
        self.assertIn(fixtures.PROJECTION_METHOD, methods)
        self.assertNotIn("editor.getFocusedBlueprintGraphContext", methods)

    def test_preview_tool_does_not_accept_a_truncated_projection(self) -> None:
        full = fixtures.build_projection(4)
        service = fixtures.FakeProjectionService(full)
        original = service.call_method

        def truncated_page(name, params, **kwargs):
            if name != fixtures.PROJECTION_METHOD:
                return original(name, params, **kwargs)
            return fixtures.make_page(full, 0, 64, nodes_override=[])

        service.call_method = truncated_page  # type: ignore[method-assign]
        payload = _preview_tool(service)()
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "graph-layout-projection-incomplete")


    @unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
    def test_actual_mcp_server_calls_the_projection_preview_tool(self) -> None:
        service = fixtures.FakeProjectionService(fixtures.build_projection(8))
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "ueak.sqlite3"
            with open_database(database):
                pass
            server = create_mcp_server(database, live_editor_service=service)
            tools = asyncio.run(server.list_tools())
            self.assertIn(PREVIEW_TOOL_NAME, {tool.name for tool in tools})
            _, payload = asyncio.run(
                server.call_tool(PREVIEW_TOOL_NAME, {})
            )
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["contextMode"], "layout-projection")
        self.assertEqual(payload["result"]["nodeCount"], 8)
        self.assertEqual(len(payload["result"]["positions"]), 8)


if __name__ == "__main__":
    unittest.main()

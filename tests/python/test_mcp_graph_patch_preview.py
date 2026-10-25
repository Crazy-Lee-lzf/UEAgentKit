from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "src"
CANDIDATE = ROOT / "prototype" / "graph_contracts"
for entry in (str(TOOLS), str(CANDIDATE)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit.mcp_graph_patch_tools import (  # noqa: E402
    GRAPH_PATCH_PREVIEW_TOOL_NAME,
    MAX_DEPTH,
    MAX_GRAPH_BYTES,
    MAX_PATCH_BYTES,
    preview_graph_patch_document,
    register_graph_patch_tools,
)
from ueak_contract import fixtures  # noqa: E402

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None


class FakeServer:
    def __init__(self) -> None:
        self.fn = None
        self.annotations = None

    def tool(self, *, annotations):
        def decorate(fn):
            self.fn = fn
            self.annotations = annotations
            return fn

        return decorate


def _fixture(fixture_id: str):
    manifest = {item["fixtureId"]: item for item in fixtures.load_manifest()["fixtures"]}
    item = manifest[fixture_id]
    return (
        fixtures.load_fixture(item),
        fixtures.load_graph_for(item),
        item.get("environment"),
        fixtures.load_descriptors_for(item),
    )


class GraphPatchPreviewTests(unittest.TestCase):
    def test_create_branch_is_previewed_read_only(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        snapshot = copy.deepcopy((patch, graph, environment, descriptors))
        payload = preview_graph_patch_document(patch, graph, environment, descriptors)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["previewStatus"], "previewed")
        self.assertTrue(payload["readOnly"])
        self.assertTrue(payload["previewOnly"])
        self.assertFalse(payload["writeAuthorized"])
        self.assertFalse(payload["editorVerified"])
        result = payload["result"]
        self.assertEqual(result["semanticHashBefore"], result["semanticHashAfter"])
        self.assertEqual(result["dirtyBefore"], result["dirtyAfter"])
        self.assertTrue(all(value is False for value in result["writeEffects"].values()))
        self.assertEqual((patch, graph, environment, descriptors), snapshot)

    def test_compile_warning_is_previewed(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-compile-warning")
        payload = preview_graph_patch_document(patch, graph, environment, descriptors)
        self.assertTrue(payload["ok"], payload)
        self.assertEqual(payload["previewStatus"], "previewed")

    def test_empty_operations_is_rejected_not_success(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-empty-operations")
        payload = preview_graph_patch_document(patch, graph, environment, descriptors)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["previewStatus"], "rejected")
        self.assertFalse(payload["writeAuthorized"])
        self.assertFalse(payload["editorVerified"])
        self.assertIn(payload["result"]["status"], {"rejected"})

    def test_delete_node_is_rejected_before_schema(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        invalid = copy.deepcopy(patch)
        invalid["operations"] = [
            {"operationId": "op-delete", "type": "deleteNode", "nodeId": "n:" + "f" * 32}
        ]
        payload = preview_graph_patch_document(invalid, graph, environment, descriptors)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errorCode"], "graphpatch-delete-node-unsupported")
        self.assertFalse(payload["writeAuthorized"])

    def test_schema_invalid_patch_returns_diagnostics(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        invalid = copy.deepcopy(patch)
        invalid["unknownExtension"] = "not allowed"
        payload = preview_graph_patch_document(invalid, graph, environment, descriptors)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["errorCode"], "graphpatch-schema-invalid")
        self.assertGreaterEqual(len(payload["diagnostics"]), 1)

    def test_oversize_inputs_fail_closed(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        cases = [
            ({"operations": [], "padding": "x" * (MAX_PATCH_BYTES + 1)}, graph, environment, descriptors, "graphpatch-patch-too-large"),
            (patch, {"padding": "x" * (MAX_GRAPH_BYTES + 1)}, environment, descriptors, "graphpatch-graph-too-large"),
            (patch, graph, {"padding": "x" * (70 * 1024)}, descriptors, "graphpatch-environment-too-large"),
        ]
        for bad_patch, bad_graph, bad_env, bad_desc, expected in cases:
            with self.subTest(code=expected):
                payload = preview_graph_patch_document(bad_patch, bad_graph, bad_env, bad_desc)
                self.assertFalse(payload["ok"])
                self.assertEqual(payload["errorCode"], expected)

    def test_depth_and_operation_limits(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        deep = {"operations": []}
        cursor = deep
        for _ in range(MAX_DEPTH + 2):
            cursor["nested"] = {}
            cursor = cursor["nested"]
        payload = preview_graph_patch_document(deep, graph, environment, descriptors)
        self.assertEqual(payload["errorCode"], "graphpatch-patch-too-deep")

        too_many = copy.deepcopy(patch)
        too_many["operations"] = [{"operationId": str(index)} for index in range(301)]
        payload = preview_graph_patch_document(too_many, graph, environment, descriptors)
        self.assertEqual(payload["errorCode"], "graphpatch-too-many-operations")

    def test_no_filesystem_activity_in_temp_cwd(self) -> None:
        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        with tempfile.TemporaryDirectory() as temp:
            previous = os.getcwd()
            try:
                os.chdir(temp)
                payload = preview_graph_patch_document(patch, graph, environment, descriptors)
                self.assertTrue(payload["ok"], payload)
                self.assertEqual(os.listdir("."), [])
            finally:
                os.chdir(previous)

    def test_registration_is_read_only(self) -> None:
        server = FakeServer()
        annotations = object()
        register_graph_patch_tools(server=server, read_annotations=annotations)
        self.assertIs(server.annotations, annotations)
        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        payload = server.fn(patch, graph, environment, descriptors)
        self.assertTrue(payload["ok"], payload)
        self.assertFalse(payload["writeAuthorized"])

    @unittest.skipUnless(MCP_AVAILABLE, "mcp package is not installed")
    def test_actual_mcp_schema_rejects_extra_arguments(self) -> None:
        from mcp.server.fastmcp import FastMCP
        from mcp.types import ToolAnnotations

        from ue_agent_kit.mcp_server import _enforce_strict_tool_arguments

        server = FastMCP("graph-patch-test")
        annotations = ToolAnnotations(
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
        register_graph_patch_tools(server=server, read_annotations=annotations)
        _enforce_strict_tool_arguments(server, (GRAPH_PATCH_PREVIEW_TOOL_NAME,))
        tools = asyncio.run(server.list_tools())
        tool = next(item for item in tools if item.name == GRAPH_PATCH_PREVIEW_TOOL_NAME)
        self.assertTrue(tool.annotations.readOnlyHint)
        self.assertFalse(tool.annotations.destructiveHint)
        self.assertNotIn("asset_path", tool.inputSchema.get("properties", {}))

        patch, graph, environment, descriptors = _fixture("gp-create-branch")
        _, payload = asyncio.run(
            server.call_tool(
                GRAPH_PATCH_PREVIEW_TOOL_NAME,
                {
                    "patch": patch,
                    "graph": graph,
                    "environment": environment,
                    "descriptors": descriptors,
                },
            )
        )
        self.assertTrue(payload["readOnly"])
        self.assertTrue(payload["previewOnly"])
        self.assertFalse(payload["writeAuthorized"])
        self.assertEqual(payload["previewStatus"], "previewed")

        with self.assertRaises(Exception):
            asyncio.run(
                server.call_tool(
                    GRAPH_PATCH_PREVIEW_TOOL_NAME,
                    {
                        "patch": patch,
                        "graph": graph,
                        "environment": environment,
                        "descriptors": descriptors,
                        "asset_path": "/Game/No/Write",
                    },
                )
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.editor_bridge import LiveEditorError  # noqa: E402
from ue_agent_kit.graph_layout_preview import build_preview  # noqa: E402
from ue_agent_kit.mcp_graph_layout_p8_test_only import (  # noqa: E402
    register_graph_layout_p8_test_only_tool,
)
from ue_agent_kit.mcp_graph_layout_preview import PREVIEW_TOOL_NAME  # noqa: E402
from ue_agent_kit.p8_test_only import (  # noqa: E402
    APPLY_TOOL_NAME,
    FIXED_ASSET_PATH,
    FIXED_PROJECT_FILE_SHA256,
    confirmation_for_plan,
)
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_DEFINITIONS_BY_NAME,
    tool_descriptors_for_mode,
)

CONTEXT = ROOT / "tests" / "fixtures" / "graph_contracts" / "bpctx_export_linear.json"
FINGERPRINT = "sha1:" + "c" * 40


def _document() -> dict:
    return json.loads(CONTEXT.read_text(encoding="utf-8"))


def _graph_guid(document: dict) -> str:
    return document["graphs"][0]["guid"]


def _preflight(document: dict, **overrides) -> dict:
    graph = document["graphs"][0]
    value = {
        "schemaVersion": "ueak.graphlayoutpreflight/1.0",
        "available": True,
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "selectionPolicy": "most-recently-active-blueprint-editor",
        "focusScope": "toolkit-graph-tab",
        "assetPath": document["assetPath"],
        "graphGuid": graph["guid"],
        "graphName": graph["name"],
        "packageRevision": document["revision"]["value"],
        "packageDirty": False,
        "graphFingerprint": FINGERPRINT,
        "nodeCount": len(graph["nodes"]),
        "complete": True,
        "nodesTruncated": False,
        "pieState": "stopped",
        "editable": True,
        "sessionId": "11111111-2222-4333-8444-555555555555",
        "pluginBuildHash": "sha256:" + "d" * 64,
    }
    value.update(overrides)
    return value


def _envelope(document: dict, **overrides) -> dict:
    value = {
        "schemaVersion": "ueak.graphlayoutcontext/1.0",
        "available": True,
        "readOnly": True,
        "source": "focused-blueprint-editor",
        "assetPath": document["assetPath"],
        "graphGuid": _graph_guid(document),
        "packageRevision": document["revision"]["value"],
        "packageDirty": False,
        "graphFingerprint": FINGERPRINT,
        "exportedAtUtc": "2026-10-22T12:00:00Z",
        "context": copy.deepcopy(document),
    }
    value.update(overrides)
    return value


class GraphLayoutP8ContractTest(unittest.TestCase):
    def test_preview_is_read_only_and_uses_the_canonical_p0_plan(self) -> None:
        document = _document()
        before = copy.deepcopy(document)
        result = build_preview(_preflight(document), _envelope(document))
        self.assertEqual(document, before)
        self.assertTrue(result["readOnly"])
        self.assertFalse(result["wroteBlueprint"])
        self.assertFalse(result["savedPackage"])
        self.assertEqual(result["assetPath"], document["assetPath"])
        self.assertEqual(result["graphGuid"], _graph_guid(document))
        self.assertEqual(result["nodeCount"], len(document["graphs"][0]["nodes"]))
        self.assertEqual(result["movedCount"], len(result["movedNodeIds"]))
        self.assertGreater(result["nodeCount"], 0)
        self.assertTrue(result["planId"].startswith("layout:"))
        self.assertTrue(result["layoutHash"].startswith("sha256:"))
        self.assertEqual(result["plan"]["layoutHash"], result["layoutHash"])
        self.assertFalse(result["acceptanceEligible"])
        self.assertEqual(result["requiredAcceptanceEvidence"], "independent-reload")
        self.assertIn("overlapCount", result["metrics"])
        self.assertIn("crossings", result["metrics"])

    def test_selection_mode_uses_explicit_scope_and_keeps_other_nodes_fixed(self) -> None:
        document = _document()
        nodes = document["graphs"][0]["nodes"]
        scope = ["n:" + nodes[-1]["guid"]]
        result = build_preview(
            _preflight(document),
            _envelope(document),
            mode="selection",
            scope_node_ids=scope,
        )
        self.assertEqual(result["mode"], "selection")
        self.assertEqual(result["scopeNodeIds"], scope)
        positions = {item["nodeId"]: item for item in result["positions"]}
        for node in nodes[:-1]:
            item = positions["n:" + node["guid"]]
            self.assertEqual(item["beforeX"], item["afterX"])
            self.assertEqual(item["beforeY"], item["afterY"])
        self.assertTrue(set(result["movedNodeIds"]) <= set(scope))

    def test_dirty_or_mismatched_context_fails_closed(self) -> None:
        document = _document()
        with self.assertRaises(LiveEditorError) as dirty:
            build_preview(
                _preflight(document, packageDirty=True),
                _envelope(document),
            )
        self.assertEqual(dirty.exception.code, "graph-layout-package-dirty")

        with self.assertRaises(LiveEditorError) as mismatch:
            build_preview(
                _preflight(document),
                _envelope(document, graphGuid="99999999-9999-4999-8999-999999999999"),
            )
        self.assertEqual(mismatch.exception.code, "graph-layout-context-identity-mismatch")

        with self.assertRaises(LiveEditorError) as revision:
            build_preview(
                _preflight(document),
                _envelope(document, packageRevision="sha256:" + "e" * 64),
            )
        self.assertEqual(revision.exception.code, "graph-layout-revision-conflict")

    def test_truncated_preflight_cannot_become_a_plan(self) -> None:
        document = _document()
        with self.assertRaises(LiveEditorError) as truncated:
            build_preview(
                _preflight(document, complete=False),
                _envelope(document),
            )
        self.assertEqual(truncated.exception.code, "graph-layout-context-incomplete")
        self.assertEqual(truncated.exception.details, {})

    def test_preview_tool_is_public_read_only_but_write_tool_is_not(self) -> None:
        self.assertIn(PREVIEW_TOOL_NAME, TOOL_DEFINITIONS_BY_NAME)
        definition = TOOL_DEFINITIONS_BY_NAME[PREVIEW_TOOL_NAME]
        self.assertTrue(definition.read_only)
        self.assertFalse(definition.destructive)
        names = {
            item["name"]
            for item in tool_descriptors_for_mode(live_editor_enabled=True, workflow_enabled=True)
        }
        self.assertIn(PREVIEW_TOOL_NAME, names)
        self.assertNotIn(APPLY_TOOL_NAME, names)
        self.assertNotIn("ue_apply_blueprint_graph_layout", names)

    def test_p8_write_policy_is_exact_and_conditional(self) -> None:
        self.assertEqual(FIXED_ASSET_PATH, "/Game/UEAgentKitP8/BP_LayoutP8.BP_LayoutP8")
        self.assertEqual(
            FIXED_PROJECT_FILE_SHA256,
            "3c9ea038dd3cb184ef4b6c1f76cf4c2de8c1c08e34536a43b7618493dace1624",
        )
        self.assertEqual(
            confirmation_for_plan("layout:abc"),
            "APPLY P8 TEST-ONLY layout:abc",
        )
        self.assertTrue(callable(register_graph_layout_p8_test_only_tool))
        server_source = (ROOT / "src/ue_agent_kit/mcp_server.py").read_text(encoding="utf-8")
        self.assertIn("p8_test_only_graph_layout_apply", server_source)
        self.assertIn("register_graph_layout_p8_test_only_tool(", server_source)
        self.assertIn("if p8_test_only_graph_layout_apply:", server_source)
        tool_source = (ROOT / "src/ue_agent_kit/mcp_graph_layout_p8_test_only.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("destructiveHint=True", tool_source)
        self.assertIn("readOnlyHint=False", tool_source)


if __name__ == "__main__":
    unittest.main()

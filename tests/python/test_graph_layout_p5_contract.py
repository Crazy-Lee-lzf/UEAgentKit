from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
P5_TEST = PRIVATE / "BlueprintGraphLayoutP5Test.cpp"
APPLY = PRIVATE / "BlueprintGraphLayoutApply.cpp"
APPLY_HEADER = PRIVATE / "BlueprintGraphLayoutApply.h"
BRIDGE = PRIVATE / "EditorBridge.cpp"
MCP_LIVE_TOOLS = ROOT / "src" / "ue_agent_kit" / "mcp_live_tools.py"
TOOL_REGISTRY = ROOT / "src" / "ue_agent_kit" / "tool_registry.py"


class GraphLayoutP5SourceContractTests(unittest.TestCase):
    """Source-only guards around the real Editor-focus acceptance test.

    The runtime evidence lives under the gitignored P5 host.  These tests keep
    the checked-in contract honest: a new disposable fixture, a real open and
    focused Blueprint Editor, exact TryApply, and no public write surface.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.test = P5_TEST.read_text(encoding="utf-8")
        cls.apply = APPLY.read_text(encoding="utf-8")
        cls.apply_header = APPLY_HEADER.read_text(encoding="utf-8")
        cls.bridge = BRIDGE.read_text(encoding="utf-8")
        cls.mcp_live_tools = MCP_LIVE_TOOLS.read_text(encoding="utf-8")
        cls.tool_registry = TOOL_REGISTRY.read_text(encoding="utf-8")

    def test_p5_uses_a_new_isolated_disposable_fixture(self) -> None:
        self.assertIn('"UEAgentKit.GraphLayout.P5EditorFocusContract"', self.test)
        self.assertIn('"UEAgentKit.GraphLayout.P5ReloadContract"', self.test)
        self.assertIn('TEXT("/Game/UEAgentKitP5/BP_LayoutP5")', self.test)
        self.assertIn("UEAgentKitP5Fixture", self.test)
        self.assertNotIn("/Game/UEAgentKitP4/", self.test)
        self.assertNotIn("/Game/UEAgentKitP3/", self.test)
        self.assertNotIn("Lyra", self.test)
        self.assertNotIn("Reforge", self.test)

    def test_p5_opens_and_focuses_a_real_blueprint_editor(self) -> None:
        self.assertIn("OpenEditorForAsset", self.test)
        self.assertIn("FindEditorForAsset", self.test)
        self.assertIn('Instance->GetEditorName() != FName(TEXT("BlueprintEditor"))', self.test)
        self.assertIn("OpenGraphAndBringToFront", self.test)
        self.assertIn("GetFocusedGraph()", self.test)
        self.assertIn("BuildFocusedEditorPreflight", self.test)
        self.assertIn("focused-editor read-only preflight succeeds", self.test)
        self.assertIn("follows the exact focused graph", self.test)

    def test_p5_try_apply_is_position_only_and_verified(self) -> None:
        self.assertIn("UEAgentKitBlueprintGraphLayout::TryApply", self.test)
        self.assertIn("GEditor->UndoTransaction()", self.test)
        self.assertIn("GEditor->RedoTransaction()", self.test)
        self.assertIn("UPackage::SavePackage", self.test)
        self.assertIn("StructuralState", self.test)
        self.assertIn("TopologyHash", self.test)
        self.assertIn("TryApply did not change topology", self.test)
        self.assertIn("P5 save revision equals the on-disk asset hash", self.test)
        self.assertIn("independentReloadVerified", self.test)

    def test_p5_exercises_the_fail_closed_editor_paths(self) -> None:
        for code in (
            "graph-layout-editor-not-open",
            "graph-layout-graph-not-focused",
            "graph-layout-package-dirty",
            "graph-layout-transaction-active",
            "graph-layout-revision-conflict",
            "graph-layout-layout-hash-mismatch",
            "graph-layout-graph-not-found",
        ):
            self.assertIn(code, self.test)

    def test_p6_exports_only_the_read_surface_and_keeps_write_internal(self) -> None:
        self.assertIn("BuildFocusedEditorPreflight", self.apply_header)
        self.assertIn("BuildFocusedEditorPreflight", self.apply)
        self.assertIn("focused-blueprint-editor", self.apply)
        self.assertIn('SetBoolField(TEXT("readOnly"), true)', self.apply)
        self.assertIn("BuildBlueprintGraphLayoutPreflightResult", self.bridge)
        self.assertIn("editor.getBlueprintGraphLayoutPreflight", self.bridge)
        self.assertNotIn("UEAgentKitBlueprintGraphLayout::TryApply", self.bridge)
        self.assertNotIn('TEXT("editor.applyBlueprintGraphLayout")', self.bridge)
        self.assertIn("ue_get_blueprint_graph_layout_preflight", self.mcp_live_tools)
        self.assertIn("ue_get_blueprint_graph_layout_preflight", self.tool_registry)
        self.assertNotIn('TEXT("editor.applyBlueprintGraphLayout")', self.bridge)


if __name__ == "__main__":
    unittest.main()

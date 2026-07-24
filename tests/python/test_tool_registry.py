from __future__ import annotations

import unittest
from pathlib import Path

from ue_agent_kit.tool_registry import (
    LIVE_EDITOR_METHODS,
    TOOL_DEFINITIONS_BY_NAME,
    TOOL_REGISTRY,
    tool_descriptors_for_mode,
    tool_names_for_mode,
)

ROOT = Path(__file__).resolve().parents[2]

EXPECTED_ALL_TOOLS = [
    "ue_get_capabilities",
    "ue_get_project_status",
    "ue_search",
    "ue_get_asset",
    "ue_find_references",
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
    "ue_set_blueprint_default",
    "ue_set_component_property",
    "ue_set_pin_default",
    "ue_set_asset_property",
    "ue_set_material_parameter",
    "ue_set_datatable_cell",
    "ue_plan_patch",
    "ue_dry_run_patch",
    "ue_apply_patch",
    "ue_verify_asset",
    "ue_get_asset_state",
    "ue_refresh_asset_index",
    "ue_rollback_patch",
]


class ToolRegistryTests(unittest.TestCase):
    def test_mode_counts_and_public_order_are_stable(self) -> None:
        self.assertEqual(
            tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True),
            EXPECTED_ALL_TOOLS,
        )
        self.assertEqual(len(tool_names_for_mode()), 5)
        self.assertEqual(len(tool_names_for_mode(live_editor_enabled=True)), 15)
        self.assertEqual(len(tool_names_for_mode(workflow_enabled=True)), 18)

    def test_mcp_registration_and_editor_readers_remain_split(self) -> None:
        mcp_root = ROOT / "src" / "ue_agent_kit"
        self.assertNotIn("@server.tool", (mcp_root / "mcp_server.py").read_text(encoding="utf-8"))
        for filename in ("mcp_query_tools.py", "mcp_live_tools.py", "mcp_workflow_tools.py"):
            self.assertIn("@server.tool", (mcp_root / filename).read_text(encoding="utf-8"), filename)

        private_root = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
        core = (private_root / "EditorBridge.cpp").read_text(encoding="utf-8")
        handlers = {
            "BuildStatusResult": "EditorBridgeStatusHandlers.cpp",
            "BuildOutputLogResult": "EditorBridgeDiagnosticHandlers.cpp",
            "BuildInspectAssetLiveResult": "EditorBridgeAssetHandlers.cpp",
            "BuildBlueprintGraphSelectionResult": "EditorBridgeGraphHandlers.cpp",
        }
        for symbol, filename in handlers.items():
            self.assertNotIn(f"FUEAgentKitEditorBridge::{symbol}", core, symbol)
            self.assertIn(
                f"FUEAgentKitEditorBridge::{symbol}",
                (private_root / filename).read_text(encoding="utf-8"),
                filename,
            )

    def test_registry_drives_live_methods_annotations_and_descriptors(self) -> None:
        names = [definition.name for definition in TOOL_REGISTRY]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), set(TOOL_DEFINITIONS_BY_NAME))
        self.assertEqual(list(LIVE_EDITOR_METHODS), EXPECTED_ALL_TOOLS[5:15])
        descriptors = tool_descriptors_for_mode(
            live_editor_enabled=True,
            workflow_enabled=True,
        )
        self.assertEqual([item["name"] for item in descriptors], EXPECTED_ALL_TOOLS)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_get_asset_state"].read_only)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_apply_patch"].destructive)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_verify_asset"].read_only)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.tool_registry import (  # noqa: E402
    LIVE_EDITOR_METHODS,
    TOOL_DEFINITIONS_BY_NAME,
    TOOL_REGISTRY,
    tool_descriptors_for_mode,
    tool_names_for_mode,
)

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
    "ue_open_asset",
    "ue_focus_asset",
    "ue_sync_content_browser",
    "ue_focus_actor",
    "ue_compile_blueprint",
    "ue_validate_asset",
    "ue_validate_folder",
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
        self.assertEqual(len(tool_names_for_mode(live_editor_enabled=True)), 22)
        self.assertEqual(len(tool_names_for_mode(workflow_enabled=True)), 18)

    def test_mcp_registration_and_editor_readers_remain_split(self) -> None:
        mcp_root = ROOT / "src" / "ue_agent_kit"
        self.assertNotIn("@server.tool", (mcp_root / "mcp_server.py").read_text(encoding="utf-8"))
        for filename in ("mcp_query_tools.py", "mcp_live_tools.py", "mcp_live_action_tools.py", "mcp_workflow_tools.py"):
            self.assertIn("@server.tool", (mcp_root / filename).read_text(encoding="utf-8"), filename)

        private_root = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
        core = (private_root / "EditorBridge.cpp").read_text(encoding="utf-8")
        handlers = {
            "BuildStatusResult": "EditorBridgeStatusHandlers.cpp",
            "BuildOutputLogResult": "EditorBridgeDiagnosticHandlers.cpp",
            "BuildInspectAssetLiveResult": "EditorBridgeAssetHandlers.cpp",
            "BuildBlueprintGraphSelectionResult": "EditorBridgeGraphHandlers.cpp",
            "TryOpenAssetResult": "EditorBridgeNavigationHandlers.cpp",
            "TryCompileBlueprintResult": "EditorBridgeValidationHandlers.cpp",
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
        self.assertEqual(list(LIVE_EDITOR_METHODS), EXPECTED_ALL_TOOLS[5:22])
        descriptors = tool_descriptors_for_mode(
            live_editor_enabled=True,
            workflow_enabled=True,
        )
        self.assertEqual([item["name"] for item in descriptors], EXPECTED_ALL_TOOLS)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_get_asset_state"].read_only)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_apply_patch"].destructive)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_verify_asset"].read_only)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_open_asset"].read_only)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_validate_folder"].destructive)

    def test_live_action_handlers_keep_bounded_execution_surface(self) -> None:
        private_root = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
        )
        navigation = (private_root / "EditorBridgeNavigationHandlers.cpp").read_text(
            encoding="utf-8"
        )
        validation = (private_root / "EditorBridgeValidationHandlers.cpp").read_text(
            encoding="utf-8"
        )
        combined = navigation + validation
        for forbidden in (
            "LoadObject",
            "StaticLoadObject",
            "UPackage::SavePackage",
            "SavePackage",
            "ConsoleCommand",
            "ProcessEvent",
            "CallFunctionByName",
            "FPlatformProcess::CreateProc",
        ):
            self.assertNotIn(forbidden, combined)
        for required in (
            "OpenEditorForAsset",
            "FindEditorForAsset",
            "SyncBrowserToObjects",
            "MoveViewportCamerasToActor",
            "CompileBlueprint",
            "ValidateAssetsWithSettings",
            'TEXT("saved")',
        ):
            self.assertIn(required, combined)

        build_rules = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "UEAgentKitEditor.Build.cs"
        ).read_text(encoding="utf-8")
        plugin_descriptor = (
            ROOT / "Plugin" / "UEAgentKit" / "UEAgentKit.uplugin"
        ).read_text(encoding="utf-8")
        self.assertIn('"DataValidation"', build_rules)
        self.assertIn('"Name": "DataValidation"', plugin_descriptor)


if __name__ == "__main__":
    unittest.main()

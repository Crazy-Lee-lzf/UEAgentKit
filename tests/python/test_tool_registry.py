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

EXPECTED_MEMORY_TOOLS = [
    "ue_memory_search",
    "ue_memory_get",
    "ue_memory_add_rule",
    "ue_memory_record_finding",
    "ue_memory_record_task",
    "ue_memory_mark_superseded",
    "ue_memory_validate",
]


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
    "ue_run_automation_test",
    "ue_set_blueprint_default",
    "ue_set_component_property",
    "ue_set_pin_default",
    "ue_set_asset_property",
    "ue_apply_asset_property_live",
    "ue_undo_asset_property_live",
    "ue_discard_asset_property_live",
    "ue_set_asset_reference_property",
    "ue_set_asset_structured_property",
    "ue_set_material_parameter",
    "ue_set_datatable_cell",
    "ue_set_datatable_row_fields",
    "ue_add_datatable_row",
    "ue_remove_datatable_row",
    "ue_rename_datatable_row",
    "ue_plan_patch",
    "ue_dry_run_patch",
    "ue_apply_patch",
    "ue_verify_asset",
    "ue_verify_live_write",
    "ue_get_asset_state",
    "ue_refresh_asset_index",
    "ue_save_authorized_asset",
    "ue_rollback_patch",
]


class ToolRegistryTests(unittest.TestCase):
    def test_mode_counts_and_public_order_are_stable(self) -> None:
        self.assertEqual(
            tool_names_for_mode(live_editor_enabled=True, workflow_enabled=True),
            EXPECTED_ALL_TOOLS,
        )
        self.assertEqual(len(tool_names_for_mode()), 5)
        self.assertEqual(len(tool_names_for_mode(live_editor_enabled=True)), 23)
        self.assertEqual(len(tool_names_for_mode(workflow_enabled=True)), 29)
        self.assertEqual(
            tool_names_for_mode(memory_enabled=True),
            EXPECTED_ALL_TOOLS[:5] + EXPECTED_MEMORY_TOOLS,
        )
        self.assertEqual(len(tool_names_for_mode(memory_enabled=True)), 12)
        self.assertEqual(
            len(tool_names_for_mode(live_editor_enabled=True, memory_enabled=True)),
            30,
        )
        self.assertEqual(
            len(tool_names_for_mode(workflow_enabled=True, memory_enabled=True)),
            36,
        )
        self.assertEqual(
            len(
                tool_names_for_mode(
                    live_editor_enabled=True,
                    workflow_enabled=True,
                    memory_enabled=True,
                )
            ),
            54,
        )

    def test_mcp_registration_and_editor_readers_remain_split(self) -> None:
        mcp_root = ROOT / "src" / "ue_agent_kit"
        self.assertNotIn("@server.tool", (mcp_root / "mcp_server.py").read_text(encoding="utf-8"))
        for filename in (
            "mcp_query_tools.py",
            "mcp_memory_tools.py",
            "mcp_live_tools.py",
            "mcp_live_action_tools.py",
            "mcp_workflow_tools.py",
        ):
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
            "TryStartAutomationTest": "EditorBridgeAutomationHandlers.cpp",
            "TrySaveAuthorizedAssetResult": "EditorBridgeSaveHandlers.cpp",
            "TryApplyAssetPropertyLiveResult": "EditorBridgeWriteHandlers.cpp",
            "TryUndoAssetPropertyLiveResult": "EditorBridgeWriteHandlers.cpp",
            "TryDiscardAssetPropertyLiveResult": "EditorBridgeWriteHandlers.cpp",
            "RevertLiveWriteTransaction": "EditorBridgeWriteHandlers.cpp",
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
        self.assertEqual(list(LIVE_EDITOR_METHODS), EXPECTED_ALL_TOOLS[5:23])
        descriptors = tool_descriptors_for_mode(
            live_editor_enabled=True,
            workflow_enabled=True,
        )
        self.assertEqual([item["name"] for item in descriptors], EXPECTED_ALL_TOOLS)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_get_asset_state"].read_only)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_apply_patch"].destructive)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_apply_asset_property_live"].destructive)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_verify_asset"].read_only)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_open_asset"].read_only)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_validate_folder"].destructive)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_save_authorized_asset"].destructive)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_memory_search"].read_only)
        self.assertTrue(TOOL_DEFINITIONS_BY_NAME["ue_memory_get"].read_only)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_memory_add_rule"].read_only)
        self.assertFalse(TOOL_DEFINITIONS_BY_NAME["ue_memory_validate"].destructive)
        memory_descriptors = tool_descriptors_for_mode(
            live_editor_enabled=False,
            workflow_enabled=False,
            memory_enabled=True,
        )
        self.assertEqual(
            [item["name"] for item in memory_descriptors],
            EXPECTED_ALL_TOOLS[:5] + EXPECTED_MEMORY_TOOLS,
        )

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
        automation = (private_root / "EditorBridgeAutomationHandlers.cpp").read_text(
            encoding="utf-8"
        )
        save = (private_root / "EditorBridgeSaveHandlers.cpp").read_text(encoding="utf-8")
        live_write = (private_root / "EditorBridgeWriteHandlers.cpp").read_text(encoding="utf-8")
        live_common = (private_root / "LiveWriteOperationCommon.cpp").read_text(encoding="utf-8")
        live_registry = (private_root / "LiveWriteOperationRegistry.cpp").read_text(encoding="utf-8")
        live_property = (private_root / "LiveWritePropertyOperations.cpp").read_text(encoding="utf-8")
        live_material = (private_root / "LiveWriteMaterialOperations.cpp").read_text(encoding="utf-8")
        live_data_table = (private_root / "LiveWriteDataTableOperations.cpp").read_text(encoding="utf-8")
        live_modules = live_write + live_common + live_registry + live_property + live_material + live_data_table
        combined = navigation + validation + automation
        for forbidden in (
            "LoadObject",
            "StaticLoadObject",
            "UPackage::SavePackage",
            "SavePackage",
            "ConsoleCommand",
            "ProcessEvent",
            "CallFunctionByName",
        ):
            self.assertNotIn(forbidden, combined)
        for required in (
            "OpenEditorForAsset",
            "FindEditorForAsset",
            "SyncBrowserToObjects",
            "MoveViewportCamerasToActor",
            "CompileBlueprint",
            "ValidateAssetsWithSettings",
            "FPlatformProcess::CreateProc",
            "FPlatformProcess::TerminateProc",
            "UnrealEditor-Cmd.exe",
            "UEAgentKitAutomationChild",
            "Automation RunTests %s;Quit",
            "ReportExportPath",
            "FJsonSerializer::Deserialize",
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
        self.assertIn("UPackage::SavePackage", save)
        self.assertNotIn("SaveAll", save)
        self.assertNotIn("PromptForCheckoutAndSave", save)
        reference_helpers = live_property.split("bool SetAssetReferenceFromJson(", 1)[1]
        scalar_section = live_property.split("bool TryApplyScalarPropertyLive(", 1)[1].split(
            "bool TryApplyReferencePropertyLive(", 1
        )[0]
        # Reference verification is documented to load the target for class validation;
        # the scalar live-write path must remain free of arbitrary object loading.
        self.assertIn("StaticLoadObject", reference_helpers)
        self.assertIn("LoadObject<UClass>", reference_helpers)
        self.assertNotIn("LoadObject", scalar_section)
        self.assertNotIn("StaticLoadObject", scalar_section)
        self.assertNotIn("SavePackage", live_modules)
        live_write_frame = (private_root / "LiveWriteTransaction.cpp").read_text(
            encoding="utf-8"
        )
        self.assertIn("FScopedTransaction", live_write_frame)
        self.assertIn("Asset->Modify()", live_write_frame)
        self.assertIn("MarkPackageDirty", live_write_frame)
        self.assertIn("CaptureSnapshot", live_write_frame)
        self.assertIn("IO->RestoreSnapshot()", live_write_frame)
        self.assertIn("IO->NotifyRestored()", live_write_frame)
        self.assertIn("IO->NotifyChanged()", live_write_frame)
        self.assertIn("Property->ArrayDim != 1", live_property)
        self.assertIn("Live Editor writes do not support native fixed-array properties.", live_property)
        self.assertIn("RunLiveWriteTransaction", live_modules)
        # The per-target change notification policy lives in the handler IO classes:
        # scalar/reference/structured notify via PostEditChangeProperty, material
        # parameters via the Material Editing Library (which already marks Dirty and
        # refreshes the material instance).
        self.assertIn("PostEditChangeProperty", live_property)
        self.assertIn("TryApplyMaterialParameterLive", live_material)
        self.assertIn("SetMaterialInstanceScalarParameterValue", live_material)
        self.assertIn("SetMaterialInstanceStaticSwitchParameterValue", live_material)
        self.assertIn("live-editor-write-material-parameter-not-found", live_material)
        self.assertIn("TryApplyDataTableLive", live_data_table)
        self.assertIn("DataTable->AddRow", live_data_table)
        self.assertIn("live-editor-write-data-table-row-not-found", live_data_table)
        self.assertIn("FLiveWriteOperationRegistry::Get()", live_write)
        self.assertNotIn("Operation.Equals(TEXT(", live_write)
        self.assertEqual(
            live_property.count("Registry.Register({TEXT(")
            + live_material.count("Registry.Register({TEXT(")
            + live_data_table.count("Registry.Register({TEXT("),
            12,
        )
        self.assertIn("RegisterPropertyLiveWriteOperations", live_registry)
        self.assertIn("RegisterMaterialLiveWriteOperations", live_registry)
        self.assertIn("RegisterDataTableLiveWriteOperations", live_registry)
        registry_header = (private_root / "LiveWriteOperationRegistry.h").read_text(encoding="utf-8")
        bridge_header = (private_root / "EditorBridge.h").read_text(encoding="utf-8")
        bridge_core = (private_root / "EditorBridge.cpp").read_text(encoding="utf-8")
        self.assertIn("TSharedPtr<FJsonObject> Target", registry_header)
        self.assertIn("TArray<FString> RequiredTargetFields", registry_header)
        self.assertIn('TryGetField(TEXT("target"))', bridge_core)
        bridge_status = (private_root / "EditorBridgeStatusHandlers.cpp").read_text(encoding="utf-8")
        self.assertIn('SetStringField(TEXT("developmentLine"), DevelopmentLine)', bridge_status)
        self.assertIn('TEXT("propertyPath")', bridge_core)
        apply_declaration = bridge_header.split("bool TryApplyAssetPropertyLiveResult(", 1)[1].split(");", 1)[0]
        self.assertIn("const TSharedPtr<FJsonObject>& Target", apply_declaration)
        self.assertNotIn("const FString& PropertyPath", apply_declaration)
        self.assertNotIn("const FString& ParameterName", apply_declaration)
        self.assertNotIn("const FString& RowName", apply_declaration)
        self.assertLess(len(live_write), 20000)

        # Explicit Undo/Discard must reuse the committed Editor transaction and the
        # retained pre-write snapshot; it must never save the package.
        self.assertIn("GEditor->UndoTransaction", live_write)
        self.assertIn("GetUndoContext(false)", live_write)
        self.assertIn("FLiveWriteTransactionRecord", live_write)
        self.assertIn("LiveWriteTransactionRecords", live_write)
        self.assertIn("TMap<FString, TMap<FGuid", bridge_header)
        self.assertIn("live-editor-write-undo-not-found", live_write)
        self.assertIn("live-editor-write-undo-stack-mismatch", live_write)
        self.assertIn("live-editor-write-undo-package-saved", live_write)
        self.assertIn("live-editor-write-undo-session-mismatch", live_write)
        self.assertIn("live-editor-write-undo-target-changed", live_write)
        self.assertIn("live-editor-write-undo-failed", live_write)
        self.assertIn("live-editor-write-undo-verify-failed", live_write)
        self.assertIn("Record->AfterValue", live_write)
        self.assertNotIn("SavePackage", live_write)
        noop_branch = live_write_frame.split("IO->SemanticEqual(BeforeValue, AfterValue))", 1)[1]        # A no-op must restore the captured snapshot before restoring the Dirty flag
        # and cancelling the transaction, because the apply path may already have
        # cleared and rebuilt containers or parameter entries for identical values.
        self.assertLess(
            noop_branch.index("IO->RestoreSnapshot()"),
            noop_branch.index("Transaction.Cancel()"),
        )
        self.assertLess(
            noop_branch.index("Context.Package->SetDirtyFlag(bPackageDirtyBefore)"),
            noop_branch.index("Transaction.Cancel()"),
        )
        self.assertIn('"DataValidation"', build_rules)
        self.assertIn('"Name": "DataValidation"', plugin_descriptor)


if __name__ == "__main__":
    unittest.main()

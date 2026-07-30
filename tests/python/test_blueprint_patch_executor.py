from __future__ import annotations



import json

import sys

import unittest

from pathlib import Path





ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))





class BlueprintPatchExecutorTests(unittest.TestCase):

    def test_operation_registry_reports_executor_support(self) -> None:

        from ue_agent_kit.patches import get_operation_registry



        operations = get_operation_registry()

        self.assertEqual(

            [item["operation"] for item in operations],

            [
                "setVariableDefault",
                "setComponentProperty",
                "setPinDefault",
                "setBlueprintDescription",
                "setAssetProperty",
                "setAssetReferenceProperty",
                "setAssetStructuredProperty",
                "setMaterialInstanceScalarParameter",
                "setMaterialInstanceVectorParameter",
                "setMaterialInstanceTextureParameter",
                "setMaterialInstanceStaticSwitchParameter",
                "setDataTableCell",
                "setDataTableRowFields",
                "addDataTableRow",
                "removeDataTableRow",
                "renameDataTableRow",
            ],

        )

        self.assertTrue(all(item["dryRunSupported"] for item in operations))

        self.assertTrue(all(item["commitSupported"] for item in operations))
        self.assertEqual(
            [item["assetType"] for item in operations],
            [
                "Blueprint",
                "Blueprint",
                "Blueprint",
                "Blueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
                "NonBlueprint",
            ],
        )



    def test_commandlet_contains_required_safety_gates(self) -> None:

        source = (

            ROOT

            / "Plugin"

            / "UEAgentKit"

            / "Source"

            / "UEAgentKitEditor"

            / "Private"

            / "BlueprintPatchCommandlet.cpp"

        ).read_text(encoding="utf-8")



        for token in (

            "setVariableDefault",

            "setComponentProperty",

            "setPinDefault",

            "Revision conflict",

            "Dirty packages are rejected",

            "CreateBackupFilename",

            "CompileBlueprint",

            "TrySetDefaultValue",

            "UPackage::SavePackage",

            "rollbackValueMatch",

            "diskUnchanged",

        ):

            self.assertIn(token, source)



    def test_asset_commandlet_contains_required_safety_gates(self) -> None:
        source = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "AssetPatchCommandlet.cpp"
        ).read_text(encoding="utf-8")
        for token in (
            "setAssetProperty",
            "setAssetReferenceProperty",
            "setAssetStructuredProperty",
            "AllowedAssetProperties",
            "AllowedReferenceRoots",
            "AllowedReferenceClasses",
            "AllowedMaterialParameters",
            "setMaterialInstanceScalarParameter",
            "setMaterialInstanceVectorParameter",
            "setMaterialInstanceTextureParameter",
            "setMaterialInstanceStaticSwitchParameter",
            "setDataTableCell",
            "setDataTableRowFields",
            "AllowedDataTableFields",
            "FStructOnScope",
            "HandleDataTableChanged",
            "appliedStructureMatch",
            "ScalarParameterArraysEqualExact",
            "VectorParameterArraysEqualExact",
            "TextureParameterArraysEqualExact",
            "StaticParameterSetsEqualExact",
            "ExpressionGuid",
            "ReadMaterialParameterMetadata",
            "AddMaterialParameterReport",
            "materialParameter",
            "rollbackMetadataMatch",
            "rollbackStateMatch",
            "AllowedReferenceRoots",
            "AllowedReferenceClasses",
            "rollbackStructureMatch",
            "Policy %s root is invalid or too broad",
            "Patch operation entry is invalid",
            "MaxExclusiveInt64AsDouble",
            "FindExistingPackageSidecar",
            "Packages with sidecar files are not supported yet",
            "Asset->IsA<UBlueprint>()",
            "CPF_Edit",
            "CPF_Transient",
            "GetIntPropertyEnum",
            "CanHoldValue",
            "TruncToDouble",
            "Revision conflict",
            "CreateBackupFilename",
            "UPackage::SavePackage",
            "Disk backup restored",
        ):
            self.assertIn(token, source)

    def test_material_reader_exports_static_switch_identity(self) -> None:
        source = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "AssetReaders"
            / "MaterialAssetReaders.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn('TEXT("expressionGuid")', source)
        self.assertIn("Parameter->ExpressionGUID", source)

    def test_run_patch_validates_before_unreal_execution(self) -> None:

        source = (ROOT / "scripts" / "RunPatch.ps1").read_text(encoding="utf-8")

        validation_index = source.index("patch validate")

        commandlet_index = source.index('$AssetOperations = @')

        self.assertLess(validation_index, commandlet_index)

        self.assertIn("exactly one asset and 1 through 32 operations", source)
        self.assertIn("single-asset transaction cannot mix", source)

        self.assertIn("commitSupported", source)
        for token in (
            "Backup manifest output must stay inside BackupDir",
            "Backup manifest output conflicts with another patch input or report",
            "Backup manifest output already exists",
        ):
            self.assertIn(token, source)
            self.assertLess(source.index(token), source.index("& $EditorCmd @Arguments"))



    def test_material_instance_reader_exports_override_metadata(self) -> None:
        source = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "AssetReaders"
            / "MaterialAssetReaders.cpp"
        ).read_text(encoding="utf-8")
        self.assertIn('TEXT("readerVersion"), 2', source)
        self.assertGreaterEqual(source.count('TEXT("override"), true'), 3)
        self.assertGreaterEqual(source.count('TEXT("expressionGuid")'), 4)


    def test_validation_evidence_is_project_and_revision_bound(self) -> None:
        private_root = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
        bridge = (private_root / "EditorBridge.cpp").read_text(encoding="utf-8")
        validation = (private_root / "EditorBridgeValidationHandlers.cpp").read_text(encoding="utf-8")
        automation = (private_root / "EditorBridgeAutomationHandlers.cpp").read_text(encoding="utf-8")
        for token in (
            "BuildValidationEvidence",
            'TEXT("projectPathHash")',
            'TEXT("editorSessionId")',
            'TEXT("startedAtUtc")',
            'TEXT("completedAtUtc")',
            'TEXT("revisionCoverage")',
            'TEXT("revisionSet")',
        ):
            self.assertIn(token, bridge)
        for token in (
            "CaptureValidationRevisions",
            "CompleteValidationRevisionEvidence",
            'TEXT("revisionStable")',
            'TEXT("packageDirtyBefore")',
            'TEXT("changedDuringActionCount")',
        ):
            self.assertIn(token, validation)
        self.assertIn('TEXT("not-applicable")', automation)
        self.assertIn('TEXT("isolated-unreal-editor-cmd")', automation)


    def test_multi_operation_transaction_regression_contract(self) -> None:
        script = (ROOT / "scripts" / "TestMultiOperationTransactions.ps1").read_text(
            encoding="utf-8"
        )
        rollback = (ROOT / "scripts" / "RunRollback.ps1").read_text(encoding="utf-8")
        self.assertIn("Assert-DryRun", script)
        self.assertIn("Assert-Commit", script)
        self.assertIn("OperationsPerAsset=2", script)
        self.assertIn("asset-transaction", script)
        self.assertIn("blueprint-transaction", script)
        self.assertIn("IncludeBlueprints", rollback)
        self.assertIn("/Script/Engine.Blueprint", rollback)

    def test_transaction_fixture_declares_two_blueprint_variables(self) -> None:
        commandlet = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "WriteFixturePlanCommandlet.cpp"
        ).read_text(encoding="utf-8")
        plan = json.loads(
            (ROOT / "tests" / "fixtures" / "multi_operation_transaction_plan.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertIn("transaction-blueprint", commandlet)
        self.assertIn("TransactionInt", commandlet)
        self.assertIn("TransactionFlag", commandlet)
        self.assertEqual(plan["root"], "/Game/UEAgentKitWriteTests/Transactions")
        self.assertEqual(len(plan["fixtures"]), 2)

    def test_release_version_is_consistent(self) -> None:

        expected_version = "0.6.0"
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        plugin = json.loads(
            (ROOT / "Plugin" / "UEAgentKit" / "UEAgentKit.uplugin").read_text(
                encoding="utf-8"
            )
        )

        self.assertIn(f'version = "{expected_version}"', pyproject)
        self.assertEqual(plugin["VersionName"], expected_version)
        self.assertEqual(plugin["Version"], 26)

        python_version_files = [
            ROOT / "src" / "ue_agent_kit" / "__init__.py",
            ROOT / "src" / "ue_agent_kit" / "backups.py",
            ROOT / "src" / "ue_agent_kit" / "fixtures.py",
        ]
        for path in python_version_files:
            source = path.read_text(encoding="utf-8")
            self.assertIn(expected_version, source, path)
            self.assertNotIn("0.5.0", source, path)

        scalar_regression = (ROOT / "scripts" / "RunScalarPatchRegression.ps1").read_text(encoding="utf-8")
        self.assertIn('toolVersion = "0.6.0"', scalar_regression)

        versioned_cpp_files = [
            "AssetCatalogExportCommandlet.cpp",
            "AssetPatchCommandlet.cpp",
            "BlueprintContextExportCommandlet.cpp",
            "BlueprintContextExporter.cpp",
            "BlueprintPatchCommandlet.cpp",
            "EditorBridge.cpp",
            "WriteFixturePlanCommandlet.cpp",
        ]
        private_root = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
        for filename in versioned_cpp_files:
            source = (private_root / filename).read_text(encoding="utf-8")
            self.assertIn(expected_version, source, filename)
            self.assertNotIn("0.5.0", source, filename)





if __name__ == "__main__":

    unittest.main()

from __future__ import annotations

import json
import unittest
from pathlib import Path

from ue_agent_kit.fixtures import validate_fixture_plan


ROOT = Path(__file__).resolve().parents[2]


class ScalarRegressionTests(unittest.TestCase):
    def test_scalar_fixture_plan_is_valid(self) -> None:
        plan = ROOT / "tests" / "fixtures" / "scalar_patch_regression_plan.json"
        result = validate_fixture_plan(plan)
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["fixtureCount"], 1)
        self.assertEqual(result["fixtures"][0]["kind"], "scalarAsset")

    def test_scalar_fixture_declares_all_supported_types_and_defaults(self) -> None:
        header = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Public"
            / "ScalarWriteFixtureAsset.h"
        ).read_text(encoding="utf-8")
        source = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "ScalarWriteFixtureAsset.cpp"
        ).read_text(encoding="utf-8")
        for token in (
            "bool BoolValue",
            "uint8 ByteValue",
            "int32 IntValue",
            "int64 Int64Value",
            "float FloatValue",
            "double DoubleValue",
            "FString StringValue",
            "FName NameValue",
            "FText TextValue",
            "EUEAgentKitScalarFixtureMode EnumValue",
            "TEnumAsByte<EUEAgentKitLegacyScalarFixtureMode> LegacyEnumValue",
        ):
            self.assertIn(token, header)
        for token in (
            "BoolValue(false)",
            "ByteValue(7)",
            "IntValue(-17)",
            "Int64Value(1234567890123LL)",
            "FloatValue(1.25f)",
            "DoubleValue(-2.5)",
            'StringValue(TEXT("Initial String"))',
            'NameValue(TEXT("InitialName"))',
            'FText::FromString(TEXT("Initial Text"))',
            "EUEAgentKitScalarFixtureMode::Alpha",
            "UEAK_LegacyAlpha",
        ):
            self.assertIn(token, source)

    def test_scalar_regression_script_covers_dry_run_commit_reload_and_reset(self) -> None:
        script = (ROOT / "scripts" / "RunScalarPatchRegression.ps1").read_text(encoding="utf-8")
        for property_name in (
            "BoolValue",
            "ByteValue",
            "IntValue",
            "Int64Value",
            "FloatValue",
            "DoubleValue",
            "StringValue",
            "NameValue",
            "TextValue",
            "EnumValue",
            "LegacyEnumValue",
        ):
            self.assertIn(property_name, script)
        for token in (
            "Mode DryRun",
            "rollbackValueMatch",
            "diskUnchanged",
            "Mode Commit",
            "Test-Path -LiteralPath (([string]$Report.backupPath) + \".manifest.json\")",
            "Read-Utf8Json",
            "[System.IO.File]::ReadAllText",
            "Export-ScalarAsset",
            "Assert-Values",
            "Resetting scalar fixture to baseline",
            "Running expected failure matrix",
            "unauthorized-property",
            "stale-revision",
            "wrong-json-type",
            "byte-out-of-range",
            "invalid-enum-name",
            "missing-property",
            "dirty-package",
            "sidecar-file",
            "save-failure",
            "TestFailureInjection",
            "ExpectedUnrealExitCode",
            "ExpectBackup",
            "asset-property-not-allowed",
            "revision-conflict",
            "Failure case $CaseName changed the scalar fixture package",
            "FixturePlan must stay outside the regression Output directory",
            "Output path must not traverse a Junction or symbolic link",
        ):
            self.assertIn(token, script)
        self.assertIn('$Cases = @(', script)
        self.assertIn("dryRunCount = $DryRunResults.Count", script)
        self.assertIn("commitCount = $CommitResults.Count", script)
        self.assertIn("failureCount = $FailureResults.Count", script)
        self.assertIn("$($FailureResults.Count)/9 rejected with zero disk changes", script)
        self.assertIn("failureMatrixDiskUnchanged = $true", script)

    def test_failure_injection_is_strictly_scoped_to_scalar_fixture(self) -> None:
        commandlet = (
            ROOT
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "AssetPatchCommandlet.cpp"
        ).read_text(encoding="utf-8")
        wrapper = (ROOT / "scripts" / "RunPatch.ps1").read_text(encoding="utf-8")
        for token in (
            "TestFailureInjection",
            "/Game/UEAgentKitWriteTests/ScalarRegression/",
            "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset",
            "scalar-failure-",
            "DirtyPackage",
            "SaveFailure",
            "Backup restoration also failed",
            "Restored Revision does not match",
            "Disk revision was already unchanged",
        ):
            self.assertIn(token, commandlet)
        self.assertIn('$AllowedTestFailureInjections = @("", "DirtyPackage", "SaveFailure")', wrapper)
        self.assertIn('throw "TestFailureInjection is available only for single-operation AssetPatch regression fixtures."', wrapper)
        self.assertIn('$Arguments += "-TestFailureInjection=$TestFailureInjection"', wrapper)

    def test_fixture_schema_accepts_scalar_regression_plan(self) -> None:
        schema_path = ROOT / "spec" / "write-fixture-plan.schema.json"
        plan_path = ROOT / "tests" / "fixtures" / "scalar_patch_regression_plan.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        self.assertEqual(plan["fixtures"][0]["expectedClass"], schema["$defs"]["scalarAsset"]["properties"]["expectedClass"]["const"])


if __name__ == "__main__":
    unittest.main()

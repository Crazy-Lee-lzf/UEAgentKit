from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "ValidateRelease.py"
SPEC = importlib.util.spec_from_file_location("ueak_validate_release", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ReleaseValidationTests(unittest.TestCase):
    def test_current_version_sources_are_consistent(self) -> None:
        self.assertEqual(MODULE.project_version(), "0.9.0.dev1")
        self.assertEqual(MODULE.validate_version_sources(require_release_docs=True), [])

    def test_schemas_and_examples_are_release_ready(self) -> None:
        self.assertEqual(MODULE.validate_schemas_and_examples(), [])

    def test_github_workflow_runs_supported_python_matrix(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release-validation.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('actions/checkout@v7', workflow)
        self.assertIn('actions/setup-python@v7', workflow)
        self.assertIn('actions/upload-artifact@v7', workflow)
        self.assertIn('- "3.11"', workflow)
        self.assertIn('- "3.12"', workflow)
        self.assertIn('- "3.13"', workflow)
        self.assertIn('- "3.14"', workflow)
        self.assertNotIn('- "3.10"', workflow)
        self.assertNotIn('- "3.15"', workflow)
        self.assertIn('python scripts/ValidateRelease.py --require-release-docs', workflow)
        self.assertIn('python -m build --outdir dist', workflow)

    def test_plugin_builders_do_not_create_or_use_sdk_links(self) -> None:
        uat = (ROOT / "scripts" / "BuildPlugin.ps1").read_text(encoding="utf-8")
        direct = (ROOT / "scripts" / "BuildPluginDirect.ps1").read_text(encoding="utf-8")
        common = (ROOT / "scripts" / "Common.ps1").read_text(encoding="utf-8")
        for script in (uat, direct):
            self.assertNotIn("AutoSDK", script)
            self.assertNotIn("Ensure-UeakJunction", script)
            self.assertNotIn("New-Item -ItemType Junction", script)
            self.assertNotIn("= $AutoSdkRoot", script)
        self.assertNotIn("function Ensure-UeakJunction", common)
        self.assertIn("Resolve-UeakMsvcToolchain", uat)
        self.assertIn("Assert-UeakPlainTree", uat)
        self.assertIn("Remove-UeakSafeDirectory", direct)
        self.assertIn(
            '$env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor = "false"',
            uat,
        )
        self.assertIn(
            "Remove-Item Env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor",
            uat,
        )

    def test_release_builder_has_no_unchecked_recursive_deletions(self) -> None:
        script = (ROOT / "scripts" / "BuildRelease.ps1").read_text(encoding="utf-8")
        self.assertIn("Remove-UeakSafeDirectory", script)
        self.assertIn("Remove-UeakSafeFile", script)
        self.assertNotIn("Remove-Item -LiteralPath $OutputDirectory -Recurse", script)
        self.assertNotIn("Remove-Item -LiteralPath $StagingRoot -Recurse", script)
        self.assertIn("Assert-UeakPlainTree -Path $PluginPackage", script)

    def test_editor_module_plugin_dependencies_are_declared(self) -> None:
        import json

        descriptor = json.loads(
            (ROOT / "Plugin" / "UEAgentKit" / "UEAgentKit.uplugin").read_text(
                encoding="utf-8"
            )
        )
        enabled = {
            item["Name"] for item in descriptor.get("Plugins", [])
            if item.get("Enabled") is True
        }
        self.assertTrue({"IKRig", "Niagara", "DataValidation"}.issubset(enabled))

    def test_release_builder_requires_clean_tree_and_emits_hashed_artifacts(self) -> None:
        script = (ROOT / "scripts" / "BuildRelease.ps1").read_text(encoding="utf-8")
        self.assertIn("status --porcelain", script)
        self.assertIn("-Method UAT", script)
        self.assertIn("UEAgentKit-$Version-UE5.6-Win64.zip", script)
        self.assertIn("pip wheel", script)
        self.assertNotIn("--no-build-isolation", script)
        self.assertIn("$PythonOutput = $OutputDirectory", script)
        self.assertNotIn('Join-Path $OutputDirectory "Python"', script)
        self.assertIn("SHA256SUMS.txt", script)
        self.assertIn("release-manifest.json", script)
        self.assertIn("Get-FileHash", script)
        self.assertIn('$TransientPluginDirectories = @("Intermediate", "Saved", "DerivedDataCache", "HostProject")', script)
        self.assertIn("Transient plugin package directory remains after pruning", script)
        self.assertIn('Where-Object { $_.Extension -ieq ".pdb" }', script)
        self.assertIn("Debug symbol remains in plugin release package", script)
        self.assertIn('"Binaries\\Win64\\UnrealEditor-UEAgentKitEditor.dll"', script)
        self.assertIn('"Binaries\\Win64\\UnrealEditor.modules"', script)
        self.assertIn("$AllowedTopLevelNames", script)
        self.assertIn("Unexpected top-level plugin package entries", script)

    def test_pyproject_declares_portable_build_and_dev_dependencies(self) -> None:
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('[build-system]', pyproject)
        self.assertIn('build-backend = "setuptools.build_meta"', pyproject)
        self.assertIn('dev = [', pyproject)
        self.assertIn('"jsonschema>=4.23,<5"', pyproject)
        self.assertIn('"ruff>=0.12,<0.13"', pyproject)


if __name__ == "__main__":
    unittest.main()

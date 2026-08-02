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
        self.assertEqual(MODULE.project_version(), "0.7.0")
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
        self.assertIn('python scripts/ValidateRelease.py --require-release-docs', workflow)
        self.assertIn('python -m build --outdir dist', workflow)

    def test_uat_plugin_builder_uses_the_resolved_autosdk_toolchain(self) -> None:
        script = (ROOT / "scripts" / "BuildPlugin.ps1").read_text(encoding="utf-8")
        self.assertIn("Resolve-UeakMsvcToolchain", script)
        self.assertIn("Ensure-UeakJunction -LinkPath $AutoSdkToolchain", script)
        self.assertIn("$env:UE_SDKS_ROOT = $AutoSdkRoot", script)
        self.assertIn("$env:UE_SDKS_ROOT = $PreviousAutoSdkRoot", script)
        self.assertIn(
            '$env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor = "false"',
            script,
        )
        self.assertIn(
            "Remove-Item Env:UnrealBuildTool_BuildConfiguration__bAllowUBAExecutor",
            script,
        )

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
        self.assertIn('"ruff>=0.12,<1"', pyproject)


if __name__ == "__main__":
    unittest.main()

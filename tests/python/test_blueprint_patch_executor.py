from __future__ import annotations



import json

import unittest

from pathlib import Path





ROOT = Path(__file__).resolve().parents[2]





class BlueprintPatchExecutorTests(unittest.TestCase):

    def test_operation_registry_reports_executor_support(self) -> None:

        from ue_agent_kit.patches import get_operation_registry



        operations = get_operation_registry()

        self.assertEqual(

            [item["operation"] for item in operations],

            ["setVariableDefault", "setComponentProperty", "setPinDefault"],

        )

        self.assertTrue(all(item["dryRunSupported"] for item in operations))

        self.assertTrue(all(item["commitSupported"] for item in operations))



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



    def test_run_patch_validates_before_unreal_execution(self) -> None:

        source = (ROOT / "scripts" / "RunPatch.ps1").read_text(encoding="utf-8")

        validation_index = source.index("patch validate")

        commandlet_index = source.index('"-run=BlueprintPatch"')

        self.assertLess(validation_index, commandlet_index)

        self.assertIn("exactly one asset and one operation", source)

        self.assertIn("commitSupported", source)



    def test_release_version_is_consistent(self) -> None:

        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")

        plugin = json.loads(

            (ROOT / "Plugin" / "UEAgentKit" / "UEAgentKit.uplugin").read_text(

                encoding="utf-8"

            )

        )

        self.assertIn('version = "0.3.1"', pyproject)

        self.assertEqual(plugin["VersionName"], "0.3.1")

        self.assertEqual(plugin["Version"], 8)





if __name__ == "__main__":

    unittest.main()

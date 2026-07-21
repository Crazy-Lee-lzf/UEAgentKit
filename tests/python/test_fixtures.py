from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ue_agent_kit.cli import build_parser
from ue_agent_kit.fixtures import validate_fixture_plan, verify_fixture_export


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\r\n",
    )


class FixturePlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.plan_path = self.root / "plan.json"
        self.plan = {
            "schemaVersion": "1.0",
            "root": "/Game/UEAgentKitWriteTests",
            "fixtures": [
                {
                    "id": "data-table",
                    "kind": "duplicateAsset",
                    "sourceAsset": "/Game/UEAgentKitTests/DT_SearchableNameFixture",
                    "targetAsset": "/Game/UEAgentKitWriteTests/DT_CellPatchTarget",
                    "expectedClass": "/Script/Engine.DataTable",
                },
                {
                    "id": "function-library",
                    "kind": "blueprint",
                    "targetAsset": "/Game/UEAgentKitWriteTests/BFL_PatchTarget",
                    "expectedClass": "/Script/Engine.Blueprint",
                    "parentClass": "/Script/Engine.BlueprintFunctionLibrary",
                    "blueprintType": "FunctionLibrary",
                },
            ],
        }
        write_json(self.plan_path, self.plan)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    @staticmethod
    def codes(result: dict) -> set[str]:
        return {item["code"] for item in result.get("errors", [])}

    def test_cli_exposes_fixture_validate_and_verify(self) -> None:
        parser = build_parser()
        validate = parser.parse_args(["fixtures", "validate", "--plan", "plan.json"])
        self.assertEqual(validate.fixtures_command, "validate")
        verify = parser.parse_args(
            [
                "fixtures",
                "verify",
                "--fixture-report",
                "report.json",
                "--export",
                "Export",
            ]
        )
        self.assertEqual(verify.fixtures_command, "verify")

    def test_valid_plan_is_read_only_validation(self) -> None:
        result = validate_fixture_plan(self.plan_path)
        self.assertTrue(result["valid"])
        self.assertFalse(result["willLoadOrModifyUObjects"])
        self.assertFalse(result["willWriteDisk"])
        self.assertRegex(result["planRevision"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(result["fixtureCount"], 2)

    def test_plan_rejects_target_outside_root_and_duplicate_id(self) -> None:
        self.plan["fixtures"][1]["id"] = "data-table"
        self.plan["fixtures"][1]["targetAsset"] = "/Game/Other/BFL_PatchTarget"
        write_json(self.plan_path, self.plan)
        result = validate_fixture_plan(self.plan_path)
        self.assertFalse(result["valid"])
        self.assertIn("fixture-id-duplicate", self.codes(result))
        self.assertIn("target-invalid", self.codes(result))

    def test_plan_rejects_source_target_overlap(self) -> None:
        self.plan["fixtures"][0]["sourceAsset"] = "/Game/UEAgentKitWriteTests/BFL_PatchTarget"
        write_json(self.plan_path, self.plan)
        result = validate_fixture_plan(self.plan_path)
        self.assertIn("source-target-overlap", self.codes(result))

    def test_plan_rejects_object_suffix_on_target(self) -> None:
        self.plan["fixtures"][0]["targetAsset"] = (
            "/Game/UEAgentKitWriteTests/DT_CellPatchTarget.DT_CellPatchTarget"
        )
        write_json(self.plan_path, self.plan)
        result = validate_fixture_plan(self.plan_path)
        self.assertIn("target-invalid", self.codes(result))

    def test_verify_fixture_export_checks_class_revision_and_dirty_state(self) -> None:
        fixture_report = self.root / "fixture-report.json"
        export_root = self.root / "Export"
        revision = "sha256:" + "a" * 64
        write_json(
            fixture_report,
            {
                "valid": True,
                "status": "completed",
                "projectName": "Project",
                "fixtures": [
                    {
                        "id": "data-table",
                        "assetPath": "/Game/UEAgentKitWriteTests/DT.DT",
                        "assetClass": "/Script/Engine.DataTable",
                        "revision": revision,
                    }
                ],
            },
        )
        canonical = export_root / "canonical" / "Game" / "UEAgentKitWriteTests" / "DT.json"
        write_json(
            canonical,
            {
                "projectName": "Project",
                "assetPath": "/Game/UEAgentKitWriteTests/DT.DT",
                "assetClass": "/Script/Engine.DataTable",
                "revision": {"value": revision, "packageDirty": False},
            },
        )
        accepted = verify_fixture_export(fixture_report, export_root)
        self.assertTrue(accepted["verified"])
        value = json.loads(canonical.read_text(encoding="utf-8"))
        value["revision"]["packageDirty"] = True
        write_json(canonical, value)
        rejected = verify_fixture_export(fixture_report, export_root)
        self.assertIn("asset-dirty", self.codes(rejected))

    def test_commandlet_and_wrapper_contain_safety_gates(self) -> None:
        repository = Path(__file__).resolve().parents[2]
        source = (
            repository
            / "Plugin"
            / "UEAgentKit"
            / "Source"
            / "UEAgentKitEditor"
            / "Private"
            / "WriteFixturePlanCommandlet.cpp"
        ).read_text(encoding="utf-8")
        for token in (
            "MaxFixtures = 64",
            "IsSpecificGameRoot",
            "IsTargetUnderRoot",
            "source-target-overlap",
            "Create mode refuses existing targets",
            "FindPackageSidecars",
            "DeleteAsset",
            "DuplicateAsset",
            "ExpectedPlanRevision",
            "plan-revision-conflict",
            "HashFile",
            "singleFilePackage",
        ):
            self.assertIn(token, source)
        wrapper = (repository / "scripts" / "RunWriteFixturePlan.ps1").read_text(encoding="utf-8")
        self.assertLess(wrapper.index("fixtures validate"), wrapper.index("-run=WriteFixturePlan"))
        self.assertIn("-ExpectedPlanRevision=$PlanRevision", wrapper)
        self.assertIn("VerificationOutput must be a child directory below the tool Output directory", wrapper)
        self.assertIn("VerificationOutput would remove a fixture input or report", wrapper)
        self.assertIn("VerificationOutput path must not traverse a Junction or symbolic link", wrapper)
        self.assertIn("VerificationOutput contains a Junction or symbolic link", wrapper)
        for token in (
            "VerificationOutput must be a child directory below the tool Output directory",
            "VerificationOutput path must not traverse a Junction or symbolic link",
            "VerificationOutput contains a Junction or symbolic link",
        ):
            self.assertLess(wrapper.index(token), wrapper.index("Remove-Item -LiteralPath $VerificationOutput"))
        self.assertIn("Reloading fixtures in an independent Unreal process", wrapper)
        self.assertIn("fixtures verify", wrapper)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.fixtures import validate_fixture_plan  # noqa: E402


class LiveReferenceWriteSmokeContractTests(unittest.TestCase):
    def test_harness_runs_live_reference_workflow_and_preserves_disk(self) -> None:
        powershell = (ROOT / "scripts" / "TestMcpLiveReferenceWrite.ps1").read_text(encoding="utf-8")
        client_path = ROOT / "tests" / "integration" / "mcp_live_reference_write_smoke.py"
        client = client_path.read_text(encoding="utf-8")
        module_spec = importlib.util.spec_from_file_location("mcp_live_reference_write_smoke", client_path)
        self.assertIsNotNone(module_spec)
        self.assertIsNotNone(module_spec.loader)
        smoke = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(smoke)
        wrapper = (ROOT / "scripts" / "TestMcpLiveReferenceWrite.cmd").read_text(encoding="utf-8")
        fixture_plan_path = ROOT / "tests" / "fixtures" / "reference_live_write_plan.json"
        fixture_plan = fixture_plan_path.read_text(encoding="utf-8")
        fixture_validation = validate_fixture_plan(fixture_plan_path)

        self.assertIn("-EnableLiveEditor", client)
        self.assertIn("-EnableWriteTools", client)
        self.assertIn("-EnableCommitTools", client)
        self.assertIn('"ue_apply_asset_property_live"', client)
        self.assertIn("ue_set_asset_reference_property", client)
        self.assertIn('"setAssetReferenceProperty"', client)
        self.assertIn('f"LIVE APPLY {plan_id}"', client)
        self.assertIn('"referenceType"', client)
        self.assertIn('"diskPackageHashesUnchanged"', client)
        self.assertIn('"databaseHashUnchanged"', client)
        self.assertIn('"revisionExportHashUnchanged"', client)
        self.assertIn("--fixture-report", client)
        self.assertEqual(smoke.FIXTURE_IDS["object"], "reference-object-asset")
        self.assertEqual(smoke.error_code({"error": {"code": "expected-code"}}), "expected-code")
        self.assertEqual(
            smoke.error_issue_codes({"error": {"details": {"issueCodes": ["a", "b"]}}}),
            {"a", "b"},
        )

        self.assertIn("reference_live_write_plan.json", powershell)
        self.assertIn("-Root", powershell)
        self.assertIn("Get-FileHash", powershell)
        self.assertIn("Get-RevisionExportHash", powershell)
        self.assertIn("Stop-Process -Id $EditorProcess.Id -Force", powershell)
        self.assertIn("MCP Live Editor reference write smoke test passed", powershell)
        self.assertIn("TestMcpLiveReferenceWrite.ps1", wrapper)

        self.assertTrue(fixture_validation["valid"], fixture_validation.get("errors"))
        self.assertEqual(fixture_validation["fixtureCount"], 9)
        self.assertIn('"kind": "referenceAsset"', fixture_plan)
        self.assertIn('"kind": "duplicateAsset"', fixture_plan)
        self.assertIn('"kind": "blueprint"', fixture_plan)
        self.assertIn('"values"', fixture_plan)
        self.assertIn('"SoftObjectValue"', fixture_plan)


if __name__ == "__main__":
    unittest.main()

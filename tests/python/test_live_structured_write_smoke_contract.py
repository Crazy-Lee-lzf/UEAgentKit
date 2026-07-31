from __future__ import annotations

import hashlib
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.fixtures import validate_fixture_plan  # noqa: E402


def _load_smoke() -> object:
    client_path = ROOT / "tests" / "integration" / "mcp_live_structured_write_smoke.py"
    module_spec = importlib.util.spec_from_file_location("mcp_live_structured_write_smoke", client_path)
    assert module_spec is not None
    assert module_spec.loader is not None
    smoke = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(smoke)
    return smoke


class LiveStructuredWriteSmokeContractTests(unittest.TestCase):
    def test_harness_runs_live_structured_workflow_and_preserves_disk(self) -> None:
        powershell = (ROOT / "scripts" / "TestMcpLiveStructuredWrite.ps1").read_text(encoding="utf-8")
        wrapper = (ROOT / "scripts" / "TestMcpLiveStructuredWrite.cmd").read_text(encoding="utf-8")
        smoke = _load_smoke()
        client_path = ROOT / "tests" / "integration" / "mcp_live_structured_write_smoke.py"
        client = client_path.read_text(encoding="utf-8")
        fixture_plan_path = ROOT / "tests" / "fixtures" / "structured_live_write_plan.json"
        fixture_plan = fixture_plan_path.read_text(encoding="utf-8")
        fixture_validation = validate_fixture_plan(fixture_plan_path)

        self.assertIn("-EnableLiveEditor", client)
        self.assertIn("-EnableWriteTools", client)
        self.assertIn("-EnableCommitTools", client)
        self.assertIn('"ue_apply_asset_property_live"', client)
        self.assertIn("ue_set_asset_structured_property", client)
        self.assertIn('"setAssetStructuredProperty"', client)
        self.assertIn('f"LIVE APPLY {plan_id}"', client)
        self.assertIn('"valueType"', client)
        self.assertIn('"diskPackageHashesUnchanged"', client)
        self.assertIn('"databaseHashUnchanged"', client)
        self.assertIn('"revisionExportHashUnchanged"', client)
        self.assertIn("--fixture-report", client)
        self.assertEqual(smoke.FIXTURE_IDS["struct"], "structured-struct-asset")
        self.assertEqual(
            smoke.FIXTURE_ASSETS["struct"],
            "/Game/UEAgentKitWriteTests/Structured/DA_Structured_Struct.DA_Structured_Struct",
        )
        self.assertEqual(smoke.error_code({"error": {"code": "expected-code"}}), "expected-code")
        self.assertEqual(
            smoke.error_issue_codes({"error": {"details": {"issueCodes": ["a", "b"]}}}),
            {"a", "b"},
        )

        self.assertIn("structured_live_write_plan.json", powershell)
        self.assertIn("-Root", powershell)
        self.assertIn("Get-FileHash", powershell)
        self.assertIn("Get-RevisionExportHash", powershell)
        self.assertIn("Stop-Process -Id $EditorProcess.Id -Force", powershell)
        self.assertIn("MCP Live Editor structured write smoke test passed", powershell)
        self.assertIn("TestMcpLiveStructuredWrite.ps1", wrapper)

        self.assertTrue(fixture_validation["valid"], fixture_validation.get("errors"))
        self.assertEqual(fixture_validation["fixtureCount"], 6)
        self.assertIn('"kind": "structuredAsset"', fixture_plan)
        self.assertIn('"kind": "scalarAsset"', fixture_plan)
        self.assertIn("/Game/UEAgentKitWriteTests/Structured", fixture_plan)
        self.assertIn("DA_Structured_Noop", fixture_plan)

    def test_smoke_hash_helpers_execute_and_detect_changes(self) -> None:
        smoke = _load_smoke()
        temporary = tempfile.mkdtemp(prefix="ueak_structured_contract_")
        try:
            root = Path(temporary)
            first = root / "canonical" / "a.json"
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_text('{"a": 1}\n', encoding="utf-8")
            self.assertEqual(
                smoke.sha256(first),
                hashlib.sha256(first.read_bytes()).hexdigest(),
            )
            second = root / "canonical" / "b.json"
            second.write_text('{"b": 2}\n', encoding="utf-8")
            before = smoke.directory_sha256(root)
            self.assertNotEqual(before, "")
            second.write_text('{"b": 3}\n', encoding="utf-8")
            after = smoke.directory_sha256(root)
            self.assertNotEqual(before, after, "directory_sha256 must detect content changes")
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def test_smoke_payload_and_error_helpers_execute(self) -> None:
        smoke = _load_smoke()

        class Result:
            def __init__(self, value: object) -> None:
                self.structuredContent = value

        self.assertEqual(smoke.payload(Result({"ok": True}), "ue_editor_status"), {"ok": True})
        with self.assertRaises(RuntimeError):
            smoke.payload(Result("not-a-dict"), "ue_editor_status")
        self.assertEqual(smoke.error_code({"ok": True}), "")
        self.assertEqual(smoke.error_issue_codes({"error": {}}), set())

    def test_smoke_fixture_values_match_the_real_structured_schema_shapes(self) -> None:
        smoke = _load_smoke()
        self.assertEqual(
            smoke.STRUCT_VALUE["valueType"],
            "Struct",
        )
        self.assertEqual(sorted(smoke.STRUCT_VALUE["fields"]), ["Count", "Label", "bEnabled"])
        self.assertEqual(
            [item["valueType"] for item in (smoke.ARRAY_VALUE, smoke.SET_VALUE, smoke.MAP_VALUE)],
            ["Array", "Set", "Map"],
        )
        self.assertEqual(smoke.SET_VALUE["items"], sorted(smoke.SET_VALUE["items"]))
        self.assertEqual(smoke.MAP_VALUE["entries"][0]["key"], "First")
        self.assertEqual(
            [entry["key"] for entry in smoke.MAP_VALUE["entries"]],
            sorted(entry["key"] for entry in smoke.MAP_VALUE["entries"]),
        )
        self.assertEqual(
            [case[0] for case in smoke.NOOP_CASES],
            ["StructValue", "ArrayValue", "SetValue", "MapValue"],
        )
        self.assertEqual(
            [case[2] for case in smoke.NOOP_CASES],
            ["Struct", "Array", "Set", "Map"],
        )
        self.assertEqual(smoke.NOOP_CASES[0][1], smoke.INITIAL_STRUCT_VALUE)
        self.assertEqual(smoke.INITIAL_ARRAY_VALUE, {"valueType": "Array", "items": [1, 2, 3]})
        self.assertEqual(
            smoke.INITIAL_SET_VALUE,
            {"valueType": "Set", "items": ["Alpha", "Beta"]},
        )
        self.assertEqual(
            smoke.INITIAL_SET_VALUE["items"],
            sorted(smoke.INITIAL_SET_VALUE["items"]),
        )
        self.assertEqual(
            [entry["key"] for entry in smoke.INITIAL_MAP_VALUE["entries"]],
            ["Primary", "Secondary"],
        )
        self.assertEqual(
            [entry["key"] for entry in smoke.INITIAL_MAP_VALUE["entries"]],
            sorted(entry["key"] for entry in smoke.INITIAL_MAP_VALUE["entries"]),
        )
        self.assertEqual(
            [entry["value"]["valueType"] for entry in smoke.INITIAL_MAP_VALUE["entries"]],
            ["Struct", "Struct"],
        )

    def test_smoke_fixture_assets_are_the_ones_validated_by_the_real_validator(self) -> None:
        smoke = _load_smoke()
        fixture_plan_path = ROOT / "tests" / "fixtures" / "structured_live_write_plan.json"
        validation = validate_fixture_plan(fixture_plan_path)
        self.assertTrue(validation["valid"], validation.get("errors"))
        by_id = {fixture["id"]: fixture for fixture in validation["fixtures"]}
        for key, fixture_id in smoke.FIXTURE_IDS.items():
            self.assertIn(fixture_id, by_id, f"smoke fixture {key} is missing from the plan")
            target = by_id[fixture_id]["targetAsset"]
            expected_asset = f"{target}.{target.rsplit('/', 1)[-1]}"
            self.assertEqual(
                smoke.FIXTURE_ASSETS[key],
                expected_asset,
                f"smoke asset path for {key} does not match the validated fixture plan",
            )


if __name__ == "__main__":
    unittest.main()

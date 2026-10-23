from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
CANDIDATE = ROOT / "prototype" / "graph_contracts"
for directory in (SRC, CANDIDATE):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from ue_agent_kit.change_sets import CHANGE_SET_SCHEMA_VERSION  # noqa: E402
from ue_agent_kit.graph_contracts import graph_patch as runtime_patch  # noqa: E402
from ue_agent_kit.graph_contracts import (  # noqa: E402
    CONTRACT_SCHEMAS,
    preview_graph_patch,
    validate_contract_document,
)
from ueak_contract import fixtures, graph_patch as candidate_patch, layout  # noqa: E402


def _without_runtime_timestamps(value):
    if isinstance(value, dict):
        return {key: _without_runtime_timestamps(item) for key, item in value.items() if key != "createdAtUtc"}
    if isinstance(value, list):
        return [_without_runtime_timestamps(item) for item in value]
    return value


class GraphContractRuntimeTests(unittest.TestCase):
    def _fixture(self, fixture_id: str):
        records = fixtures.load_manifest()["fixtures"]
        return next(item for item in records if item["fixtureId"] == fixture_id)

    def test_all_packaged_schemas_are_available(self) -> None:
        for kind in CONTRACT_SCHEMAS:
            with self.subTest(kind=kind):
                result = validate_contract_document(kind, {})
                self.assertFalse(result["valid"])
                self.assertFalse(result["writeAuthorized"])
                self.assertFalse(result["editorVerified"])
                self.assertIn("diagnostics", result)

    def test_valid_patch_is_accepted_by_packaged_schema(self) -> None:
        valid = fixtures.load_json(fixtures.fixture_path("graphpatch/gp_empty_operations.json"))
        outcome = validate_contract_document("graphpatch", valid)
        self.assertTrue(outcome["valid"], outcome["diagnostics"])
        invalid = copy.deepcopy(valid)
        invalid["operations"].append({"type": "deleteNode", "nodeId": "n:" + "f" * 32})
        bad = validate_contract_document("graphpatch", invalid)
        self.assertFalse(bad["valid"])
        self.assertEqual(CHANGE_SET_SCHEMA_VERSION, "2.0")

    def test_descriptor_namespaces_and_minor_version_negotiation(self) -> None:
        for kind, path in (
            ("node-descriptor", "descriptors/nd_branch.json"),
            ("operation-descriptor", "descriptors/od_set_node_enabled.json"),
        ):
            with self.subTest(kind=kind):
                item = fixtures.load_json(fixtures.fixture_path(path))
                result = validate_contract_document(kind, item)
                self.assertTrue(result["valid"], result["diagnostics"])

        patch = fixtures.load_json(fixtures.fixture_path("graphpatch/gp_empty_operations.json"))
        changed = copy.deepcopy(patch)
        changed["unknownExtension"] = "ignore only on newer minor"
        self.assertFalse(validate_contract_document("graphpatch", changed)["valid"])
        changed["schemaVersion"] = "ueak.graphpatch/1.1"
        forward = validate_contract_document("graphpatch", changed)
        self.assertTrue(forward["valid"], forward["diagnostics"])
        self.assertIn("contract-schema-minor-newer", [item["code"] for item in forward["diagnostics"]])
        changed["schemaVersion"] = "ueak.graphpatch/2.0"
        self.assertFalse(validate_contract_document("graphpatch", changed)["valid"])

    def test_change_set_21_sidecar_validation_does_not_upgrade_20(self) -> None:
        entry = {
            "schemaVersion": "ueak.change-set-idempotency/1.0",
            "changeSetSchemaVersion": "2.1",
            "scopeKey": "sha256:" + "a" * 64,
            "projectName": "TestProject",
            "assetPath": "/Game/Test/BP_Example.BP_Example",
            "graphId": "g:12345678-1234-1234-1234-1234567890ab",
            "idempotencyKey": "test-key",
            "patchId": "g03-test",
            "payloadHash": "sha256:" + "b" * 64,
            "state": "reserved",
            "reservedAtUtc": "2026-10-22T00:00:00Z",
            "updatedAtUtc": "2026-10-22T00:00:00Z",
        }
        ok = validate_contract_document("change-set-idempotency", entry)
        self.assertTrue(ok["valid"], ok["diagnostics"])
        invalid = {**entry, "changeSetSchemaVersion": "2.0"}
        self.assertFalse(validate_contract_document("change-set-idempotency", invalid)["valid"])

    def test_preview_matches_frozen_candidate_and_never_writes(self) -> None:
        for fixture_id in ("gp-empty-operations", "gp-compile-warning", "gp-create-branch"):
            with self.subTest(fixture_id=fixture_id):
                item = self._fixture(fixture_id)
                patch = fixtures.load_fixture(item)
                graph = fixtures.load_graph_for(item)
                descriptors = fixtures.load_descriptors_for(item)
                environment = item.get("environment", {})
                before = copy.deepcopy((patch, graph, descriptors, environment))
                reference = candidate_patch.preview_patch(patch, graph, environment, descriptors)
                actual = preview_graph_patch(patch, graph, environment, descriptors)
                self.assertEqual(actual.pop("editorVerified"), False)
                self.assertEqual(actual.pop("writeAuthorized"), False)
                self.assertEqual(_without_runtime_timestamps(actual), _without_runtime_timestamps(reference))
                self.assertEqual((patch, graph, descriptors, environment), before)
                self.assertTrue(actual["previewOnly"])
                self.assertEqual(actual["writeEffects"]["diskModified"], False)
                self.assertEqual(actual["writeEffects"]["idempotencyReserved"], False)

    def test_layout_plan_keeps_independent_reload_gate(self) -> None:
        item = fixtures.load_json(fixtures.fixture_path("layout/layout_linear.json"))
        graph = fixtures.load_json(fixtures.fixture_path("layout/" + item["graphFixture"]))
        plan = layout.layout_graph(graph, item["request"])
        self.assertEqual(plan.status, "ok")
        decision = validate_contract_document("layout-plan", plan.plan)
        self.assertTrue(decision["valid"], decision["diagnostics"])
        self.assertFalse(plan.plan["acceptanceEligible"])
        self.assertEqual(plan.plan["requiredAcceptanceEvidence"], "independent-reload")

    def test_profile_policy_preserves_d06_limits(self) -> None:
        total_250 = {"operations": ([{"type": "createNode"}] * 100 + [{"type": "connectPins"}] * 150)}
        report = runtime_patch.PatchReport()
        runtime_patch.validate_profile_limits(total_250, "structure-default", report)
        self.assertFalse(report.ok)
        self.assertIn("graphpatch-limit-exceeded", report.codes)
        report = runtime_patch.PatchReport()
        runtime_patch.validate_profile_limits(total_250, "structure-g2", report)
        self.assertTrue(report.ok, report.errors)
        positions = {"operations": [{"type": "setNodePosition"}] * 1000}
        report = runtime_patch.PatchReport()
        runtime_patch.validate_profile_limits(positions, "layout-only", report)
        self.assertTrue(report.ok, report.errors)
        report = runtime_patch.PatchReport()
        runtime_patch.validate_profile_limits({"operations": positions["operations"] + [{"type": "setNodePosition"}]}, "layout-only", report)
        self.assertFalse(report.ok)

    def test_unknown_schema_kind_is_not_a_file_path(self) -> None:
        with self.assertRaises(ValueError):
            validate_contract_document("../graphpatch", {})


if __name__ == "__main__":
    unittest.main()

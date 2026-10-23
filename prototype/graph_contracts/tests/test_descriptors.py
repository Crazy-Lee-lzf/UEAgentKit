from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import descriptors, fixtures  # noqa: E402

DESCRIPTOR_DIR = fixtures.FIXTURE_ROOT / "descriptors"


class DescriptorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.print_doc = fixtures.load_json(DESCRIPTOR_DIR / "nd_print.json")
        self.branch_doc = fixtures.load_json(DESCRIPTOR_DIR / "nd_branch.json")
        self.source_doc = fixtures.load_json(DESCRIPTOR_DIR / "nd_source.json")
        self.operation_doc = fixtures.load_json(DESCRIPTOR_DIR / "od_create_node.json")
        self.enabled_doc = fixtures.load_json(DESCRIPTOR_DIR / "od_set_node_enabled.json")

    def test_node_descriptors_are_valid(self) -> None:
        for document in (self.print_doc, self.branch_doc, self.source_doc):
            with self.subTest(action=document["title"]):
                report = descriptors.validate_node_descriptor(document)
                self.assertTrue(report.ok, report.errors)

    def test_action_id_is_derived_from_stable_discovery_key(self) -> None:
        for document in (self.print_doc, self.branch_doc, self.source_doc):
            with self.subTest(action=document["title"]):
                self.assertEqual(document["actionId"], descriptors.make_action_id(document["discoveryKey"]))
                self.assertTrue(document["actionId"].startswith("action:"))
                self.assertEqual(len(document["actionId"]), len("action:") + 32)

    def test_localized_field_in_discovery_key_is_rejected(self) -> None:
        mutated = copy.deepcopy(self.print_doc)
        mutated["discoveryKey"]["title"] = "打印"
        mutated["actionId"] = descriptors.make_action_id(mutated["discoveryKey"])
        report = descriptors.validate_node_descriptor(mutated)
        self.assertIn("graphpatch-context-mismatch", report.codes)

    def test_descriptor_digest_detects_edit(self) -> None:
        mutated = copy.deepcopy(self.print_doc)
        mutated["title"] = "Changed"
        report = descriptors.validate_node_descriptor(mutated)
        self.assertIn("graphpatch-descriptor-not-found", report.codes)

    def test_action_database_fingerprint_is_order_independent(self) -> None:
        ids = [self.print_doc["actionId"], self.branch_doc["actionId"], self.source_doc["actionId"]]
        self.assertEqual(
            descriptors.action_database_fingerprint(ids),
            descriptors.action_database_fingerprint(list(reversed(ids))),
        )

    def test_operation_descriptor_is_valid_and_codes_registered(self) -> None:
        report = descriptors.validate_operation_descriptor(self.operation_doc)
        self.assertTrue(report.ok, report.errors)
        registry = __import__("ueak_contract.errors", fromlist=["load_registry"]).load_registry()
        for code in self.operation_doc["errorCodes"]:
            self.assertTrue(registry.is_known(code), code)
        conditions = descriptors.load_condition_codes()
        for code in self.operation_doc["preconditions"]:
            self.assertEqual(conditions[code]["kind"], "precondition")
        for code in self.operation_doc["postconditions"]:
            self.assertEqual(conditions[code]["kind"], "postcondition")

    def test_operation_descriptor_rejects_unknown_error_code(self) -> None:
        mutated = copy.deepcopy(self.operation_doc)
        mutated["errorCodes"].append("graphpatch-made-up")
        report = descriptors.validate_operation_descriptor(mutated)
        self.assertIn("contract-error-code-unknown", report.codes)

    def test_set_node_enabled_descriptor_requires_explicit_confirmation(self) -> None:
        report = descriptors.validate_operation_descriptor(self.enabled_doc)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual(self.enabled_doc["name"], "graph.setNodeEnabled")
        self.assertEqual(self.enabled_doc["risk"], "R2")
        self.assertTrue(self.enabled_doc["requiresExplicitConfirmation"])

    def test_pin_template_requires_full_type(self) -> None:
        mutated = copy.deepcopy(self.print_doc)
        del mutated["pins"][0]["type"]["isReference"]
        report = descriptors.validate_node_descriptor(mutated)
        self.assertIn("contract-schema-required", report.codes)

    def test_exec_pin_template_must_be_exec(self) -> None:
        mutated = copy.deepcopy(self.print_doc)
        exec_pin = next(pin for pin in mutated["pins"] if pin["name"] == "execute")
        exec_pin["type"]["category"] = "string"
        report = descriptors.validate_node_descriptor(mutated)
        self.assertIn("graphpatch-pin-type-incompatible", report.codes)


if __name__ == "__main__":
    unittest.main()

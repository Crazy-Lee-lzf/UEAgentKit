from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import fixtures, layout, typed_graph  # noqa: E402
from ueak_contract.errors import load_registry  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_manifest_has_at_least_thirteen_fixtures_and_unique_ids(self) -> None:
        manifest = fixtures.load_manifest()
        self.assertGreaterEqual(len(manifest["fixtures"]), 13)
        ids = [item["fixtureId"] for item in manifest["fixtures"]]
        self.assertEqual(len(ids), len(set(ids)))
        for item in manifest["fixtures"]:
            self.assertFalse(item["requiresEditor"])
            self.assertTrue(fixtures.fixture_path(item["file"]).is_file(), item["file"])
            if item.get("graphFixture"):
                self.assertTrue(fixtures.fixture_path(item["graphFixture"]).is_file(), item["graphFixture"])
            for descriptor in item.get("descriptorFixtures", []):
                self.assertTrue(fixtures.fixture_path(descriptor).is_file(), descriptor)

    def test_manifest_covers_required_scenarios(self) -> None:
        manifest = fixtures.load_manifest()
        ids = {item["fixtureId"] for item in manifest["fixtures"]}
        required = {
            "tg-single-chinese",
            "tg-empty",
            "tg-dynamic-pins",
            "tg-exec-vs-data",
            "gp-replace-connection",
            "gp-stale-revision",
            "gp-recovery-marker",
            "layout-islands-shared",
            "layout-cycle",
        }
        self.assertEqual(required - ids, set())

    def test_hash_vectors_recompute(self) -> None:
        vectors = fixtures.load_json(fixtures.VECTORS_PATH)
        for vector in vectors["semantic"]:
            with self.subTest(vector=vector["id"]):
                graph = fixtures.load_json(fixtures.fixture_path(vector["fixture"]))
                self.assertEqual(typed_graph.semantic_hash(graph), vector["expected"])
        for vector in vectors["layout"]:
            with self.subTest(vector=vector["id"]):
                graph = fixtures.load_json(fixtures.fixture_path(vector["fixture"]))
                self.assertEqual(typed_graph.layout_hash(graph), vector["expected"])
        for vector in vectors["layoutPlans"]:
            with self.subTest(vector=vector["id"]):
                graph = fixtures.load_json(fixtures.fixture_path(vector["fixture"]))
                plan = layout.layout_graph(graph, vector["request"])
                self.assertEqual(plan.plan["layoutHash"], vector["layoutHash"])

    def test_localization_invariance_vector(self) -> None:
        vectors = fixtures.load_json(fixtures.VECTORS_PATH)
        vector = vectors["localizationInvariance"]
        graph = fixtures.load_json(fixtures.fixture_path(vector["fixture"]))
        before = typed_graph.semantic_hash(graph)
        mutated = copy.deepcopy(graph)
        for mutation in vector["mutations"]:
            path = mutation["path"]
            node_index = int(path.split("[")[1].split("]")[0])
            if path.startswith("nodes["):
                node = mutated["nodes"][node_index]
                field = path.split(".")[1]
                node[field] = mutation["value"]
            else:
                pin = mutated["pins"][node_index]
                field = path.split(".")[1]
                if field == "friendlyName":
                    pin["friendlyName"] = mutation["value"]
                elif field == "defaults.value":
                    pin["defaults"]["value"] = mutation["value"]
                else:
                    self.fail(path)
        self.assertEqual(typed_graph.semantic_hash(mutated), before)

    def test_all_expected_error_codes_are_registered(self) -> None:
        registry = load_registry()
        for item in fixtures.load_manifest()["fixtures"]:
            expected = item.get("expected", {})
            for key in ("previewErrorCode", "applyErrorCode", "builderErrorCode", "errorCode"):
                code = expected.get(key)
                if code:
                    self.assertTrue(registry.is_known(code), f"{item['fixtureId']}: {code}")

    def test_error_registry_is_consistent(self) -> None:
        registry = load_registry()
        self.assertGreaterEqual(len(registry), 80)
        for code in registry.codes():
            entry = registry.get(code)
            self.assertTrue(entry.summary)
            self.assertTrue(entry.contract_ref)
            if entry.legacy_alias_of:
                self.assertTrue(registry.is_known(entry.legacy_alias_of))
            else:
                self.assertTrue(code.startswith(entry.domain + "-"))
        with self.assertRaises(KeyError):
            registry.get("graphpatch-not-a-real-code")

    def test_condition_codes_are_consistent(self) -> None:
        document = json.loads((fixtures.SCHEMA_ROOT / "condition-codes.json").read_text(encoding="utf-8"))
        codes = [entry["code"] for entry in document["codes"]]
        self.assertEqual(len(codes), len(set(codes)))
        self.assertTrue(all(entry["kind"] in ("precondition", "postcondition") for entry in document["codes"]))


if __name__ == "__main__":
    unittest.main()

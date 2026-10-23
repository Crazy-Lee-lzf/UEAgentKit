from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import fixtures, typed_graph  # noqa: E402


def graph_by_id(fixture_id: str) -> dict:
    wanted = fixture_id.replace("_", "-")
    for entry in fixtures.load_manifest()["fixtures"]:
        if entry["fixtureId"] == wanted:
            return fixtures.load_fixture(entry)
    raise KeyError(fixture_id)


class TypedGraphTests(unittest.TestCase):
    def test_frozen_semantic_hashes(self) -> None:
        vectors = fixtures.load_json(fixtures.VECTORS_PATH)["semantic"]
        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                graph = fixtures.load_json(fixtures.fixture_path(vector["fixture"]))
                self.assertEqual(typed_graph.semantic_hash(graph), vector["expected"])
                self.assertEqual(graph["semanticHash"], vector["expected"])

    def test_empty_graph_vector(self) -> None:
        graph = graph_by_id("tg_empty")
        self.assertEqual(graph["nodes"], [])
        self.assertEqual(graph["semanticHash"], typed_graph.semantic_hash(graph))
        self.assertEqual(typed_graph.validate(graph).diagnostics, [])

    def test_localization_fields_never_change_semantic_hash(self) -> None:
        graph = graph_by_id("tg_single_chinese")
        original = typed_graph.semantic_hash(graph)
        mutated = copy.deepcopy(graph)
        mutated["nodes"][0]["title"] = "Print String"
        mutated["nodes"][0]["comment"] = "English comment"
        mutated["nodes"][0]["objectPath"] = "/Game/Other.BP:EventGraph.K2Node_0"
        mutated["pins"][2]["friendlyName"] = "In String"
        self.assertEqual(typed_graph.semantic_hash(mutated), original)
        mutated["pins"][2]["defaults"]["value"] = "Hello, UEAgentKit"
        self.assertNotEqual(typed_graph.semantic_hash(mutated), original)
        mutated["pins"][2]["defaults"]["value"] = graph["pins"][2]["defaults"]["value"]
        mutated["nodes"][0]["position"] = {"x": 999, "y": 999}
        self.assertEqual(typed_graph.semantic_hash(mutated), original)
        self.assertNotEqual(typed_graph.layout_hash(mutated), typed_graph.layout_hash(graph))

    def test_size_is_not_in_layout_hash(self) -> None:
        graph = graph_by_id("tg_linear_exec")
        before_layout = typed_graph.layout_hash(graph)
        before_size = typed_graph.size_hash(graph)
        mutated = copy.deepcopy(graph)
        mutated["nodes"][0]["size"] = {"width": 999, "height": 888, "source": "measured"}
        self.assertEqual(typed_graph.layout_hash(mutated), before_layout)
        self.assertNotEqual(typed_graph.size_hash(mutated), before_size)

    def test_default_value_changes_semantic_and_node_hash(self) -> None:
        graph = graph_by_id("tg_linear_exec")
        node_id = next(node["nodeId"] for node in graph["nodes"] if node["title"] == "Print String A")
        before_node = typed_graph.node_hash(graph, node_id)
        mutated = copy.deepcopy(graph)
        pin = next(pin for pin in mutated["pins"] if pin["nodeId"] == node_id and pin["name"] == "InString")
        pin["defaults"]["value"] = "changed"
        self.assertNotEqual(typed_graph.semantic_hash(mutated), typed_graph.semantic_hash(graph))
        self.assertNotEqual(typed_graph.node_hash(mutated, node_id), before_node)

    def test_edge_and_identity_invariants(self) -> None:
        graph = graph_by_id("tg_linear_exec")
        mutated = copy.deepcopy(graph)
        data_edge = next(edge for edge in mutated["edges"] if edge["kind"] == "data")
        data_edge["kind"] = "exec"
        report = typed_graph.validate(mutated)
        self.assertIn("graph-invalid-edge-kind", report.codes)

        mutated = copy.deepcopy(graph)
        mutated["edges"][0]["edgeId"] = "e:p:00000000-0000-4000-8000-000000000000--p:00000000-0000-4000-8000-000000000000"
        report = typed_graph.validate(mutated)
        self.assertIn("graph-invalid-edge-id", report.codes)

        mutated = copy.deepcopy(graph)
        mutated["pins"][0]["links"] = []
        report = typed_graph.validate(mutated)
        self.assertIn("graph-links-mirror-mismatch", report.codes)

    def test_duplicate_pin_builder_invariant(self) -> None:
        entry = next(item for item in fixtures.load_manifest()["fixtures"] if item["fixtureId"] == "tg-duplicate-pin-raw")
        raw = fixtures.load_fixture(entry)
        built = typed_graph.build_typed_graph(raw)
        duplicate_pins = [pin for pin in built["pins"] if pin["pinId"].startswith("p:dup:")]
        self.assertEqual(len(duplicate_pins), 2)
        self.assertTrue(all(item["code"] == "graph-pin-guid-duplicate" for item in built["diagnostics"]))
        self.assertFalse(typed_graph.apply_ready(built))
        self.assertFalse(typed_graph.validate(built, require_apply_ready=True).ok)

    def test_truncation_blocks_apply(self) -> None:
        graph = copy.deepcopy(graph_by_id("tg_linear_exec"))
        graph["limits"].update({"truncated": True, "omittedNodeCount": 3, "selection": "nodeId-ascending"})
        graph["diagnostics"] = [
            {"code": "graph-truncated", "severity": "error", "message": "truncated", "path": "$.limits", "context": {}}
        ]
        report = typed_graph.validate(graph, require_hashes=True, require_apply_ready=True)
        self.assertIn("graph-truncated", report.codes)
        self.assertFalse(typed_graph.apply_ready(graph))

    def test_fallback_identity_blocks_apply(self) -> None:
        graph = copy.deepcopy(graph_by_id("tg_empty"))
        graph["graphId"] = "g:name:EventGraph"
        report = typed_graph.validate(graph, require_hashes=True, require_apply_ready=True)
        self.assertIn("graph-node-identity-unstable", report.codes)

    def test_derived_pin_is_info_not_blocking(self) -> None:
        graph = graph_by_id("tg_dynamic_pins")
        report = typed_graph.validate(graph, require_hashes=True, require_apply_ready=True)
        self.assertTrue(report.ok, report.errors)
        self.assertEqual([item["severity"] for item in graph["diagnostics"]], ["info"])
        derived = [pin for pin in graph["pins"] if pin["pinIdSource"] == "derived"]
        self.assertEqual(len(derived), 1)
        self.assertTrue(derived[0]["pinGuidTransient"])

    def test_node_kind_determination_table(self) -> None:
        cases = {
            "/Script/BlueprintGraph.K2Node_Event": "event",
            "/Script/BlueprintGraph.K2Node_CustomEvent": "event",
            "/Script/BlueprintGraph.K2Node_CallFunction": "call",
            "/Script/BlueprintGraph.K2Node_VariableGet": "variable-get",
            "/Script/BlueprintGraph.K2Node_VariableSet": "variable-set",
            "/Script/BlueprintGraph.K2Node_MacroInstance": "macro",
            "/Script/BlueprintGraph.K2Node_DynamicCast": "cast",
            "/Script/BlueprintGraph.K2Node_Knot": "reroute",
            "/Script/UnrealEd.EdGraphNode_Comment": "comment",
            "/Script/BlueprintGraph.K2Node_UnknownThing": "other",
        }
        for class_path, kind in cases.items():
            with self.subTest(class_path=class_path):
                self.assertEqual(typed_graph.classify_node_class(class_path), kind)

    def test_large_fixture_shape(self) -> None:
        graph = graph_by_id("tg_large_100")
        self.assertEqual(len(graph["nodes"]), 100)
        self.assertEqual(len(graph["edges"]), 90)
        self.assertTrue(typed_graph.validate(graph).ok)
        self.assertTrue(typed_graph.apply_ready(graph))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import fixtures, layout, typed_graph  # noqa: E402


def entry(fixture_id: str) -> dict:
    wanted = fixture_id.replace("_", "-")
    for item in fixtures.load_manifest()["fixtures"]:
        if item["fixtureId"] == wanted:
            return item
    raise KeyError(fixture_id)


def request_for(item: dict) -> dict:
    return fixtures.load_fixture(item)["request"]


class LayoutTests(unittest.TestCase):
    def test_golden_layout_vectors(self) -> None:
        vectors = fixtures.load_json(fixtures.VECTORS_PATH)["layoutPlans"]
        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                graph = fixtures.load_json(fixtures.fixture_path(vector["fixture"]))
                plan = layout.layout_graph(graph, vector["request"])
                self.assertEqual(plan.status, "ok", plan.errors)
                self.assertEqual(plan.plan["layoutHash"], vector["layoutHash"])
                self.assertEqual(
                    [{"nodeId": item["nodeId"], "x": item["x"], "y": item["y"]} for item in plan.plan["positions"]],
                    vector["positions"],
                )
                self.assertEqual(plan.plan["metrics"], vector["metrics"])
                self.assertEqual(len(plan.plan["islands"]), vector["islandCount"])

    def test_layout_invariants_for_all_fixtures(self) -> None:
        manifest = fixtures.load_manifest()
        for item in manifest["fixtures"]:
            if item["kind"] != "layout":
                continue
            with self.subTest(fixture=item["fixtureId"]):
                graph = fixtures.load_graph_for(item)
                before = graph["semanticHash"]
                plan = layout.layout_graph(graph, request_for(item))
                self.assertEqual(plan.status, "ok", plan.errors)
                self.assertEqual(typed_graph.semantic_hash(graph), before, "Layout must not mutate the graph")
                self.assertEqual(plan.plan["metrics"]["overlapCount"], 0)
                self.assertEqual(plan.plan["sizeBeforeHash"], typed_graph.size_hash(graph))
                self.assertEqual(plan.plan["layoutHash"], layout.recompute_layout_hash(plan.plan))
                for position in plan.plan["positions"]:
                    self.assertIsInstance(position["x"], int)
                    self.assertIsInstance(position["y"], int)

    def test_determinism_under_input_reordering(self) -> None:
        item = entry("layout-linear")
        graph = fixtures.load_graph_for(item)
        reordered = copy.deepcopy(graph)
        reordered["nodes"].reverse()
        reordered["pins"].reverse()
        reordered["edges"].reverse()
        first = layout.layout_graph(graph, request_for(item))
        second = layout.layout_graph(reordered, request_for(item))
        self.assertEqual(first.plan["layoutHash"], second.plan["layoutHash"])
        self.assertEqual(first.plan["positions"], second.plan["positions"])

    def test_idempotent_on_its_own_output(self) -> None:
        item = entry("layout-linear")
        graph = fixtures.load_graph_for(item)
        request = request_for(item)
        first = layout.layout_graph(graph, request)
        moved = copy.deepcopy(graph)
        by_id = {position["nodeId"]: position for position in first.plan["positions"]}
        for node in moved["nodes"]:
            position = by_id[node["nodeId"]]
            node["position"] = {"x": position["x"], "y": position["y"]}
            node["size"] = dict(position["size"])
        moved = typed_graph.freeze_hashes(moved)
        second = layout.layout_graph(moved, request)
        self.assertEqual(first.plan["layoutHash"], second.plan["layoutHash"])
        self.assertEqual(second.plan["movedNodeIds"], [])

    def test_seed_is_reserved_and_ignored(self) -> None:
        item = entry("layout-linear")
        graph = fixtures.load_graph_for(item)
        request = copy.deepcopy(request_for(item))
        request["options"]["seed"] = 0
        first = layout.layout_graph(graph, request)
        request["options"]["seed"] = 987654
        second = layout.layout_graph(graph, request)
        self.assertEqual(first.plan["positions"], second.plan["positions"])
        self.assertEqual(first.plan["layoutHash"], second.plan["layoutHash"])
        self.assertEqual(second.plan["seed"], 987654)

    def test_shared_pure_node_is_not_duplicated(self) -> None:
        item = entry("layout-islands-shared")
        graph = fixtures.load_graph_for(item)
        plan = layout.layout_graph(graph, request_for(item))
        self.assertEqual(plan.status, "ok", plan.errors)
        self.assertEqual(len(plan.plan["islands"]), 2)
        node_ids = [position["nodeId"] for position in plan.plan["positions"]]
        self.assertEqual(len(node_ids), len(set(node_ids)))
        pure_node = next(node for node in graph["nodes"] if node["title"] == "Shared Value")
        self.assertEqual(node_ids.count(pure_node["nodeId"]), 1)
        warnings = {warning["code"] for warning in plan.plan["warnings"]}
        self.assertIn("layout-shared-pure-node", warnings)
        self.assertIn("layout-cross-island-data-edge", warnings)
        self.assertFalse(any(island["islandId"].startswith("island:data-only") for island in plan.plan["islands"]))

    def test_cycle_is_scc_not_dependency_cycle(self) -> None:
        item = entry("layout-cycle")
        graph = fixtures.load_graph_for(item)
        plan = layout.layout_graph(graph, request_for(item))
        self.assertEqual(plan.status, "ok", plan.errors)
        self.assertEqual(len(plan.plan["islands"]), 1)
        self.assertEqual(len(plan.plan["sccs"]), 1)
        self.assertEqual(len(plan.plan["sccs"][0]["nodeIds"]), 2)
        self.assertNotIn("graphpatch-dependency-cycle", str(plan.errors))

    def test_incremental_keeps_fixed_nodes(self) -> None:
        item = entry("layout-linear")
        graph = fixtures.load_graph_for(item)
        scope_node = next(node for node in graph["nodes"] if node["title"] == "Print String B")
        request = {"mode": "incremental", "scope": [scope_node["nodeId"]], "options": {"spacingX": 80, "spacingY": 40}}
        plan = layout.layout_graph(graph, request)
        self.assertEqual(plan.status, "ok", plan.errors)
        by_id = {position["nodeId"]: position for position in plan.plan["positions"]}
        for node in graph["nodes"]:
            if node["nodeId"] == scope_node["nodeId"]:
                continue
            self.assertEqual(
                (by_id[node["nodeId"]]["x"], by_id[node["nodeId"]]["y"]),
                (node["position"]["x"], node["position"]["y"]),
            )
            self.assertIn(node["nodeId"], plan.plan["fixedNodeIds"])
        self.assertTrue(set(plan.plan["movedNodeIds"]) <= {scope_node["nodeId"]})

    def test_layout_hash_excludes_size(self) -> None:
        item = entry("layout-linear")
        graph = fixtures.load_graph_for(item)
        plan = layout.layout_graph(graph, request_for(item))
        before = layout.recompute_layout_hash(plan.plan)
        mutated = copy.deepcopy(plan.plan)
        mutated["positions"][0]["size"]["width"] += 100
        self.assertEqual(layout.recompute_layout_hash(mutated), before)

    def test_empty_graph_metrics(self) -> None:
        item = entry("layout-empty")
        graph = fixtures.load_graph_for(item)
        plan = layout.layout_graph(graph, request_for(item))
        self.assertEqual(plan.status, "ok", plan.errors)
        self.assertEqual(plan.plan["positions"], [])
        self.assertEqual(plan.plan["islands"], [])
        for key in ("crossings", "edgeLength", "overlapCount", "area"):
            self.assertEqual(plan.plan["metrics"][key], 0)
        self.assertEqual(plan.plan["metrics"]["aspectRatio"], 0.0)

    def test_comment_bounds_contain_nodes_and_are_not_edges(self) -> None:
        item = entry("layout-linear")
        graph = fixtures.load_graph_for(item)
        plan = layout.layout_graph(graph, request_for(item))
        self.assertEqual(plan.status, "ok", plan.errors)
        self.assertGreater(plan.plan["metrics"]["edgeLength"], 0)
        self.assertFalse(any(edge["kind"] == "comment" for edge in graph["edges"]))
        comment_graph = fixtures.load_json(fixtures.fixture_path("typedgraph/tg_comment_reroute.json"))
        comment_plan = layout.layout_graph(comment_graph, {"mode": "full"})
        self.assertEqual(comment_plan.status, "ok", comment_plan.errors)
        comment_node = next(node for node in comment_graph["nodes"] if node["kind"] == "comment")
        comment_position = next(
            position for position in comment_plan.plan["positions"] if position["nodeId"] == comment_node["nodeId"]
        )
        for node in comment_graph["nodes"]:
            if node["kind"] == "comment":
                continue
            position = next(
                item for item in comment_plan.plan["positions"] if item["nodeId"] == node["nodeId"]
            )
            self.assertGreaterEqual(position["x"], comment_position["x"])
            self.assertGreaterEqual(position["y"], comment_position["y"])


if __name__ == "__main__":
    unittest.main()

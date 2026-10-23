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

from ue_agent_kit.graph_contracts import (  # noqa: E402
    BlueprintContextImportError,
    preview_graph_layout,
    typed_graph_from_blueprint_context,
    validate_contract_document,
)
from ue_agent_kit.graph_contracts import layout as runtime_layout  # noqa: E402
from ue_agent_kit.graph_contracts import typed_graph as runtime_typed_graph  # noqa: E402
from ueak_contract import layout as candidate_layout  # noqa: E402


def _fixture(path: str) -> dict:
    return json.loads((CANDIDATE / "fixtures" / path).read_text(encoding="utf-8"))


def _bpctx() -> dict:
    return json.loads((ROOT / "tests" / "fixtures" / "graph_contracts" / "bpctx_export_linear.json").read_text(encoding="utf-8"))


SOURCE = {
    "sessionId": "fixture-session",
    "pluginBuildHash": "0123456789abcdef",
    "exportedAtUtc": "2026-10-22T00:00:00Z",
}


def _import_bpctx(document: dict | None = None, **kwargs) -> dict:
    return typed_graph_from_blueprint_context(document or _bpctx(), source_metadata=SOURCE, **kwargs)


class LayoutRuntimeTests(unittest.TestCase):
    def test_full_selection_incremental_are_deterministic_and_schema_valid(self) -> None:
        graph = _fixture("typedgraph/tg_linear_exec.json")
        scope = [graph["nodes"][-1]["nodeId"]]
        requests = (
            {"mode": "full", "options": {"spacingX": 80, "spacingY": 40, "seed": 0}},
            {"mode": "selection", "scope": scope, "options": {"spacingX": 80, "spacingY": 40}},
            {"mode": "incremental", "scope": scope, "options": {"spacingX": 80, "spacingY": 40}},
        )
        for request in requests:
            with self.subTest(mode=request["mode"]):
                first = preview_graph_layout(graph, request)
                second = preview_graph_layout(graph, request)
                self.assertEqual(first.status, "ok", first.errors)
                self.assertEqual(first.plan, second.plan)
                self.assertEqual(
                    validate_contract_document("layout-plan", first.plan)["diagnostics"],
                    [],
                )

    def test_layout_is_pure_and_never_claims_acceptance(self) -> None:
        graph = _fixture("typedgraph/tg_linear_exec.json")
        before = copy.deepcopy(graph)
        request = {"mode": "full"}
        result = preview_graph_layout(graph, request)
        self.assertEqual(result.status, "ok", result.errors)
        self.assertEqual(graph, before)
        self.assertEqual(request, {"mode": "full"})
        self.assertFalse(result.plan["acceptanceEligible"])
        self.assertEqual(result.plan["evidenceLevel"], "same-process")
        self.assertEqual(result.plan["requiredAcceptanceEvidence"], "independent-reload")
        self.assertEqual(result.plan["sizeBeforeHash"], runtime_typed_graph.size_hash(graph))
        self.assertEqual(result.plan["layoutHash"], runtime_layout.recompute_layout_hash(result.plan))

    def test_incremental_and_selection_keep_out_of_scope_nodes_fixed(self) -> None:
        graph = _fixture("typedgraph/tg_linear_exec.json")
        scope = [graph["nodes"][-1]["nodeId"]]
        original = {node["nodeId"]: (node["position"]["x"], node["position"]["y"]) for node in graph["nodes"]}
        for mode in ("selection", "incremental"):
            with self.subTest(mode=mode):
                result = preview_graph_layout(graph, {"mode": mode, "scope": scope})
                self.assertEqual(result.status, "ok", result.errors)
                positions = {item["nodeId"]: (item["x"], item["y"]) for item in result.plan["positions"]}
                for node_id, position in original.items():
                    if node_id not in scope:
                        self.assertEqual(positions[node_id], position)
                        self.assertIn(node_id, result.plan["fixedNodeIds"])
                self.assertTrue(set(result.plan["movedNodeIds"]) <= set(scope))

    def test_candidate_golden_geometry_is_unchanged_for_non_comment_graph(self) -> None:
        graph = _fixture("typedgraph/tg_linear_exec.json")
        request = {"mode": "full", "options": {"spacingX": 80, "spacingY": 40}}
        actual = preview_graph_layout(graph, request)
        reference = candidate_layout.layout_graph(graph, request)
        self.assertEqual(actual.status, "ok", actual.errors)
        self.assertEqual(actual.plan["layoutHash"], reference.plan["layoutHash"])
        self.assertEqual(actual.plan["positions"], reference.plan["positions"])

    def test_comment_bounds_come_from_comments_contract_not_zero_fallback(self) -> None:
        graph = _fixture("typedgraph/tg_comment_reroute.json")
        result = preview_graph_layout(graph, {"mode": "full"})
        self.assertEqual(result.status, "ok", result.errors)
        positions = {item["nodeId"]: (item["x"], item["y"]) for item in result.plan["positions"]}
        comment = next(node for node in graph["nodes"] if node["kind"] == "comment")
        comment_position = positions[comment["nodeId"]]
        content_positions = [
            positions[node["nodeId"]]
            for node in graph["nodes"]
            if node["kind"] != "comment"
        ]
        self.assertNotEqual(comment_position, (0, 0))
        self.assertEqual(comment_position, (min(item[0] for item in content_positions) - 24, min(item[1] for item in content_positions) - 24))
        for contained in graph["comments"][0]["containedNodeIds"]:
            self.assertGreaterEqual(positions[contained][0], comment_position[0])
            self.assertGreaterEqual(positions[contained][1], comment_position[1])

    def test_scope_and_options_are_fail_closed(self) -> None:
        graph = _fixture("typedgraph/tg_linear_exec.json")
        invalid_requests = (
            ({"mode": "selection", "scope": []}, "layout-scope-violation"),
            ({"mode": "incremental", "scope": [graph["nodes"][0]["nodeId"], graph["nodes"][0]["nodeId"]]}, "layout-scope-violation"),
            ({"mode": "selection", "scope": ["n:ffffffff-ffff-ffff-ffff-ffffffffffff"]}, "layout-scope-violation"),
            ({"mode": "full", "scope": [graph["nodes"][0]["nodeId"]]}, "layout-scope-violation"),
            ({"mode": "full", "options": {"spacingX": 0}}, "contract-schema-type-mismatch"),
            ({"mode": "full", "options": {"spacingY": True}}, "contract-schema-type-mismatch"),
            ({"mode": "full", "packing": {"targetWidth": 1}}, "contract-schema-type-mismatch"),
            ({"mode": "unknown"}, "contract-schema-enum-mismatch"),
        )
        for request, code in invalid_requests:
            with self.subTest(request=request):
                result = preview_graph_layout(graph, request)
                self.assertEqual(result.status, "rejected")
                self.assertEqual(result.errors[0]["code"], code)

    def test_layout_only_moved_position_limit_is_1000(self) -> None:
        graph = _fixture("typedgraph/tg_linear_exec.json")
        template = graph["nodes"][0]
        graph["nodes"] = [
            {
                **copy.deepcopy(template),
                "nodeId": f"n:{index:08x}-0000-4000-8000-000000000000",
                "nodeGuid": f"{index:08x}-0000-4000-8000-000000000000",
                "position": {"x": 5000, "y": 5000},
                "pins": [],
            }
            for index in range(1001)
        ]
        graph["pins"] = []
        graph["edges"] = []
        graph["comments"] = []
        graph["limits"] = {
            "maxNodes": 5000,
            "maxPins": 50000,
            "includedNodeCount": 1001,
            "omittedNodeCount": 0,
            "selection": "nodeId-ascending",
            "truncated": False,
        }
        graph = runtime_typed_graph.freeze_hashes(graph)
        result = preview_graph_layout(graph, {"mode": "full"})
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.errors[0]["code"], "graphpatch-limit-exceeded")

    def test_non_int32_fixed_coordinate_is_rejected(self) -> None:
        graph = _fixture("typedgraph/tg_linear_exec.json")
        graph["nodes"][0]["position"]["x"] = 2_147_483_648
        graph = runtime_typed_graph.freeze_hashes(graph)
        scope = [graph["nodes"][-1]["nodeId"]]
        result = preview_graph_layout(graph, {"mode": "incremental", "scope": scope})
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.errors[0]["code"], "graph-invalid-coordinate")


class BlueprintContextAdapterTests(unittest.TestCase):
    def test_real_exporter_shape_maps_to_typedgraph_without_synthetic_edges(self) -> None:
        document = _bpctx()
        before = copy.deepcopy(document)
        graph = _import_bpctx(document)
        self.assertEqual(document, before)
        self.assertEqual(graph["graphId"], "g:10000000-0000-4000-8000-000000000001")
        self.assertEqual(graph["graph"]["kind"], "ubergraph")
        self.assertEqual(graph["graph"]["editable"], False)
        self.assertEqual(graph["asset"]["revision"], "sha256:" + "a" * 64)
        self.assertEqual(len(graph["nodes"]), 4)
        self.assertEqual(len(graph["pins"]), 8)
        self.assertEqual(len(graph["edges"]), 3)
        self.assertEqual(len(graph["comments"]), 1)
        self.assertEqual(
            {edge["edgeId"] for edge in graph["edges"]},
            {
                "e:p:21000000-0000-4000-8000-000000000001--p:21000000-0000-4000-8000-000000000002",
                "e:p:21000000-0000-4000-8000-000000000003--p:21000000-0000-4000-8000-000000000006",
                "e:p:21000000-0000-4000-8000-000000000004--p:21000000-0000-4000-8000-000000000008",
            },
        )
        self.assertEqual(validate_contract_document("typedgraph", graph)["diagnostics"], [])
        for pin in graph["pins"]:
            edge_neighbours = {
                edge["to"]["pinId"] if edge["from"]["pinId"] == pin["pinId"] else edge["from"]["pinId"]
                for edge in graph["edges"]
                if pin["pinId"] in (edge["from"]["pinId"], edge["to"]["pinId"])
            }
            self.assertEqual(set(pin["links"]), edge_neighbours)
        self.assertEqual(graph["semanticHash"], runtime_typed_graph.semantic_hash(graph))

    def test_imported_graph_goes_through_layout_and_keeps_comment_bounds(self) -> None:
        graph = _import_bpctx()
        result = preview_graph_layout(graph, {"mode": "full"})
        self.assertEqual(result.status, "ok", result.errors)
        self.assertEqual(validate_contract_document("layout-plan", result.plan)["diagnostics"], [])
        self.assertFalse(result.plan["acceptanceEligible"])
        comment = next(node for node in graph["nodes"] if node["kind"] == "comment")
        position = next(item for item in result.plan["positions"] if item["nodeId"] == comment["nodeId"])
        content = [
            item
            for item in result.plan["positions"]
            if item["nodeId"] != comment["nodeId"]
        ]
        self.assertEqual(position["x"], min(item["x"] for item in content) - 24)
        self.assertEqual(position["y"], min(item["y"] for item in content) - 24)

    def test_graph_selection_is_not_accepted_as_a_typedgraph_source(self) -> None:
        result = {
            "scope": "ordinary-blueprint-editor",
            "available": True,
            "graph": {"graphGuid": "10000000-0000-4000-8000-000000000001"},
            "selectedNodes": [
                {"nodeGuid": "11000000-0000-4000-8000-000000000001", "nodePosX": 0, "nodePosY": 0}
            ],
        }
        with self.assertRaises(BlueprintContextImportError) as caught:
            typed_graph_from_blueprint_context(result, source_metadata=SOURCE)
        self.assertIn(
            caught.exception.code,
            {"blueprint-context-shape-invalid", "blueprint-context-version-unsupported", "blueprint-context-graph-missing"},
        )

    def test_ambiguous_graph_requires_exact_guid(self) -> None:
        document = _bpctx()
        second = copy.deepcopy(document["graphs"][0])
        second["guid"] = "10000000-0000-4000-8000-000000000002"
        second["name"] = "OtherGraph"
        document["graphs"].append(second)
        with self.assertRaises(BlueprintContextImportError) as caught:
            _import_bpctx(document)
        self.assertEqual(caught.exception.code, "blueprint-context-graph-ambiguous")
        graph = _import_bpctx(document, graph_guid="10000000-0000-4000-8000-000000000002")
        self.assertEqual(graph["graph"]["name"], "OtherGraph")

    def test_weak_revision_and_missing_layout_are_rejected(self) -> None:
        document = _bpctx()
        document["revision"]["value"] = "guid:" + document["revision"]["packageGuid"]
        with self.assertRaises(BlueprintContextImportError) as caught:
            _import_bpctx(document)
        self.assertEqual(caught.exception.code, "blueprint-context-revision-weak")

        document = _bpctx()
        del document["graphs"][0]["nodes"][0]["layout"]
        with self.assertRaises(BlueprintContextImportError) as caught:
            _import_bpctx(document)
        self.assertEqual(caught.exception.code, "blueprint-context-shape-invalid")

    def test_dangling_and_asymmetric_links_are_rejected_without_a_fake_edge(self) -> None:
        document = _bpctx()
        document["graphs"][0]["nodes"][0]["pins"][0]["links"][0]["targetPinId"] = "21000000-0000-4000-8000-000000000999"
        with self.assertRaises(BlueprintContextImportError) as caught:
            _import_bpctx(document)
        self.assertEqual(caught.exception.code, "blueprint-context-link-dangling")

        document = _bpctx()
        document["graphs"][0]["nodes"][0]["pins"][0]["links"] = []
        with self.assertRaises(BlueprintContextImportError) as caught:
            _import_bpctx(document)
        self.assertEqual(caught.exception.code, "blueprint-context-link-asymmetry")

    def test_duplicate_pin_link_and_truncation_are_rejected(self) -> None:
        document = _bpctx()
        document["graphs"][0]["nodes"][0]["pins"][0]["links"].append(
            copy.deepcopy(document["graphs"][0]["nodes"][0]["pins"][0]["links"][0])
        )
        with self.assertRaises(BlueprintContextImportError) as caught:
            _import_bpctx(document)
        self.assertEqual(caught.exception.code, "blueprint-context-duplicate-link")

        document = _bpctx()
        document["truncated"] = True
        with self.assertRaises(BlueprintContextImportError) as caught:
            _import_bpctx(document)
        self.assertEqual(caught.exception.code, "blueprint-context-truncated")

    def test_source_metadata_and_dirty_flag_are_not_fabricated(self) -> None:
        document = _bpctx()
        document["revision"]["packageDirty"] = True
        with self.assertRaises(BlueprintContextImportError) as caught:
            typed_graph_from_blueprint_context(document)
        self.assertEqual(caught.exception.code, "blueprint-context-source-invalid")
        graph = _import_bpctx(document)
        self.assertTrue(graph["asset"]["dirty"])
        self.assertFalse(graph["graph"]["editable"])

    def test_selected_graph_only_keeps_selected_graph_nodes(self) -> None:
        document = _bpctx()
        second = copy.deepcopy(document["graphs"][0])
        second["guid"] = "10000000-0000-4000-8000-000000000002"
        second["name"] = "OtherGraph"
        for node in second["nodes"]:
            node["guid"] = node["guid"][:-1] + "9"
            for pin in node["pins"]:
                pin["id"] = pin["id"][:-1] + "9"
            for pin in node["pins"]:
                for link in pin["links"]:
                    link["targetPinId"] = link["targetPinId"][:-1] + "9"
                    link["targetNodeGuid"] = link["targetNodeGuid"][:-1] + "9"
        document["graphs"].append(second)
        graph = _import_bpctx(document, graph_guid="10000000-0000-4000-8000-000000000001")
        self.assertTrue(all(node["nodeGuid"].startswith("11000000") for node in graph["nodes"]))


if __name__ == "__main__":
    unittest.main()

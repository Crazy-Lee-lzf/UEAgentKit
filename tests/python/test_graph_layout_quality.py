from __future__ import annotations

import copy
import json
import random
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ue_agent_kit.graph_contracts import preview_graph_layout, validate_contract_document  # noqa: E402
from ue_agent_kit.graph_contracts import _layout_layered, layout, typed_graph  # noqa: E402

TEMPLATE = json.loads(
    (ROOT / "prototype" / "graph_contracts" / "fixtures" / "typedgraph" / "tg_linear_exec.json").read_text(
        encoding="utf-8",
    ),
)
INT32_MIN, INT32_MAX = -(2**31), 2**31 - 1


def _guid(index: int) -> str:
    return f"{index:08x}-0000-4000-8000-000000000000"


def _graph(
    kinds: dict[str, str], edges: list[tuple[str, str, str]], *, positions=None, sizes=None, comments=None,
) -> dict:
    """Complete mirrored links; Knot fan-out shares one physical output pin.

    Ordinary execution branch outputs use separate pins. Stored fixture
    geometry is synthetic and does not claim real UE Editor measurements.
    """
    graph = copy.deepcopy(TEMPLATE)
    graph.update(nodes=[], pins=[], edges=[], comments=[], diagnostics=[])
    nodes = {}
    classes = {
        "call": "K2Node_CallFunction",
        "event": "K2Node_Event",
        "reroute": "K2Node_Knot",
        "variable-get": "K2Node_VariableGet",
        "comment": "EdGraphNode_Comment",
    }
    for index, (name, kind) in enumerate(kinds.items(), 1):
        node = copy.deepcopy(TEMPLATE["nodes"][1])
        node.update(
            nodeId="n:" + _guid(index),
            nodeGuid=_guid(index),
            classPath="/Script/BlueprintGraph." + classes[kind],
            objectPath=f"{graph['asset']['path']}:EventGraph.{classes[kind]}_{index}",
            title=name,
            kind=kind,
            pins=[],
        )
        node["flags"].update(pure=kind == "variable-get", reroute=kind == "reroute", comment=kind == "comment")
        x, y = (positions or {}).get(name, (index * 320, 0))
        width, height = (sizes or {}).get(name, (24, 24) if kind == "reroute" else (160, 80))
        node["position"] = {"x": x, "y": y}
        node["size"] = {"width": width, "height": height, "source": "stored"}
        nodes[name] = node
        graph["nodes"].append(node)
    pin_records = {}

    def pin(name, direction, kind, branch=0):
        slot = branch if direction == "output" and kinds[name] != "reroute" else 0
        key = name, direction, kind, slot
        if key not in pin_records:
            item = copy.deepcopy(TEMPLATE["pins"][0])
            item.update(
                pinId="p:" + _guid(1_000_000 + len(pin_records)),
                nodeId=nodes[name]["nodeId"],
                name=f"{direction}_{kind}_{slot}",
                direction=direction,
                kind=kind,
                index=len(nodes[name]["pins"]),
                links=[],
            )
            item["type"]["category"] = "exec" if kind == "exec" else "float"
            nodes[name]["pins"].append(item["pinId"])
            graph["pins"].append(item)
            pin_records[key] = item
        return pin_records[key]

    for index, (source, target, kind) in enumerate(edges):
        output, input_ = pin(source, "output", kind, index), pin(target, "input", kind)
        output["links"].append(input_["pinId"])
        input_["links"].append(output["pinId"])
        graph["edges"].append(
            {
                "edgeId": f"e:{output['pinId']}--{input_['pinId']}",
                "from": {"nodeId": output["nodeId"], "pinId": output["pinId"]},
                "to": {"nodeId": input_["nodeId"], "pinId": input_["pinId"]},
                "kind": kind,
                "origin": "pin-link",
                "status": "resolved",
            },
        )
    for name, kind in kinds.items():
        if kind == "reroute":
            existing = next((item["kind"] for item in graph["pins"] if item["nodeId"] == nodes[name]["nodeId"]), "data")
            for direction in ("input", "output"):
                if not any(
                    item["nodeId"] == nodes[name]["nodeId"] and item["direction"] == direction for item in graph["pins"]
                ):
                    pin(name, direction, existing)
    for name, members in (comments or {}).items():
        node = nodes[name]
        graph["comments"].append(
            {
                "nodeId": node["nodeId"],
                "text": f"Preserve comment: {name}",
                "bounds": {**node["position"], "width": node["size"]["width"], "height": node["size"]["height"]},
                "containedNodeIds": [nodes[member]["nodeId"] for member in members],
            }
        )
    graph["limits"].update(includedNodeCount=len(nodes), omittedNodeCount=0, truncated=False)
    return typed_graph.freeze_hashes(graph)


def _id(graph, name):
    return next(node["nodeId"] for node in graph["nodes"] if node["title"] == name)


def _positions(plan):
    return {item["nodeId"]: item for item in plan["positions"]}


def _overlaps(left, right):
    return (
        left["x"] < right["x"] + right["size"]["width"]
        and right["x"] < left["x"] + left["size"]["width"]
        and left["y"] < right["y"] + right["size"]["height"]
        and right["y"] < left["y"] + left["size"]["height"]
    )


class GraphLayoutQualityTests(unittest.TestCase):
    """Behavioral safety and readable geometry regressions for runtime layout."""

    def _preview(self, graph, request=None):
        before, request_before = copy.deepcopy(graph), copy.deepcopy(request)
        result = preview_graph_layout(graph, request)
        self.assertEqual(graph, before, "preview mutated node/pin/link/comment input")
        self.assertEqual(request, request_before)
        self.assertEqual(result.status, "ok", result.errors)
        plan = result.plan
        self.assertEqual(plan["baseGraphHash"], typed_graph.semantic_hash(graph))
        self.assertEqual(plan["layoutHash"], layout.recompute_layout_hash(plan))
        self.assertEqual(len(plan["positions"]), len(graph["nodes"]))
        self.assertEqual(set(_positions(plan)), {node["nodeId"] for node in graph["nodes"]})
        self.assertFalse(plan["acceptanceEligible"])
        self.assertEqual(plan["requiredAcceptanceEvidence"], "independent-reload")
        self.assertEqual(validate_contract_document("layout-plan", plan)["diagnostics"], [])
        for item in plan["positions"]:
            for axis in ("x", "y"):
                self.assertIs(type(item[axis]), int)
                self.assertLessEqual(INT32_MIN, item[axis])
                self.assertLessEqual(item[axis], INT32_MAX)
        return plan

    def _assert_disjoint(self, graph, plan):
        positions = _positions(plan)
        content = [positions[node["nodeId"]] for node in graph["nodes"] if node["kind"] != "comment"]
        for index, left in enumerate(content):
            for right in content[index + 1:]:
                self.assertFalse(_overlaps(left, right), (left["nodeId"], right["nodeId"]))
        self.assertEqual(plan["metrics"]["overlapCount"], 0)

    def _assert_rejected(self, graph, expected_code):
        before = copy.deepcopy(graph)
        result = preview_graph_layout(graph, {"mode": "full"})
        self.assertEqual(graph, before)
        self.assertEqual(result.status, "rejected", result.plan)
        self.assertIn(expected_code, {item["code"] for item in result.errors}, result.errors)

    def test_data_knot_single_output_can_fan_out_without_changing_topology(self):
        graph = _graph(
            {
                "source": "variable-get", "knot": "reroute", "a": "variable-get",
                "b": "variable-get", "c": "variable-get",
            },
            [("source", "knot", "data"), ("knot", "a", "data"), ("knot", "b", "data"), ("knot", "c", "data")],
        )
        knot_id = _id(graph, "knot")
        output = next(pin for pin in graph["pins"] if pin["nodeId"] == knot_id and pin["direction"] == "output")
        self.assertEqual(len(output["links"]), 3)
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        positions = _positions(plan)
        self.assertLess(positions[_id(graph, "source")]["x"], positions[knot_id]["x"])
        self.assertTrue(all(positions[knot_id]["x"] < positions[_id(graph, name)]["x"] for name in ("a", "b", "c")))

    def test_exec_knot_can_merge_multiple_upstreams_into_one_output(self):
        graph = _graph(
            {"entry_a": "event", "entry_b": "event", "knot": "reroute", "call": "call"},
            [("entry_a", "knot", "exec"), ("entry_b", "knot", "exec"), ("knot", "call", "exec")],
        )
        self._assert_disjoint(graph, self._preview(graph))

    def test_exec_knot_fanout_and_data_knot_multiple_sources_are_rejected(self):
        for kind, edges in (
            ("exec", [("a", "knot", "exec"), ("knot", "b", "exec"), ("knot", "c", "exec")]),
            ("data", [("a", "knot", "data"), ("b", "knot", "data"), ("knot", "c", "data")]),
        ):
            with self.subTest(kind=kind):
                self._assert_rejected(
                    _graph({"a": "call", "b": "call", "c": "call", "knot": "reroute"}, edges),
                    "layout-reroute-broken",
                )

    def test_knot_chain_and_shared_branch_have_forward_disjoint_geometry(self):
        graph = _graph(
            {
                "source": "variable-get",
                "k1": "reroute",
                "k2": "reroute",
                "k3": "reroute",
                "a": "variable-get",
                "b": "variable-get",
            },
            [
                ("source", "k1", "data"),
                ("k1", "k2", "data"),
                ("k2", "k3", "data"),
                ("k2", "a", "data"),
                ("k3", "b", "data"),
            ],
        )
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        positions = _positions(plan)
        for edge in graph["edges"]:
            self.assertLess(positions[edge["from"]["nodeId"]]["x"], positions[edge["to"]["nodeId"]]["x"])

    def test_missing_knot_source_sink_or_all_links_remain_rejected(self):
        for edges in ([], [("source", "knot", "data")], [("knot", "sink", "data")]):
            with self.subTest(edges=edges):
                self._assert_rejected(
                    _graph({"source": "variable-get", "knot": "reroute", "sink": "variable-get"}, edges),
                    "layout-reroute-broken",
                )

    def test_knot_mixed_exec_data_and_extra_physical_pin_remain_rejected(self):
        mixed = _graph(
            {"source": "event", "knot": "reroute", "sink": "variable-get"},
            [("source", "knot", "exec"), ("knot", "sink", "data")],
        )
        self._assert_rejected(mixed, "layout-reroute-broken")
        graph = _graph(
            {"source": "variable-get", "knot": "reroute", "sink": "variable-get"},
            [("source", "knot", "data"), ("knot", "sink", "data")],
        )
        knot_id = _id(graph, "knot")
        extra = copy.deepcopy(
            next(pin for pin in graph["pins"] if pin["nodeId"] == knot_id and pin["direction"] == "input")
        )
        extra.update(pinId="p:" + _guid(9_000_000), index=2, links=[])
        graph["pins"].append(extra)
        next(node for node in graph["nodes"] if node["nodeId"] == knot_id)["pins"].append(extra["pinId"])
        self._assert_rejected(typed_graph.freeze_hashes(graph), "layout-reroute-broken")

    def test_malformed_edges_and_pin_link_contracts_do_not_become_success(self):
        source = _graph({"entry": "event", "call": "call"}, [("entry", "call", "exec")])
        for mutation, code in (
            ("dangling", "graph-dangling-edge"),
            ("direction", "graph-invalid-pin-direction"),
            ("asymmetric", "graph-links-mirror-mismatch"),
            ("duplicate", "graph-duplicate-edge"),
        ):
            with self.subTest(mutation=mutation):
                graph = copy.deepcopy(source)
                if mutation == "dangling":
                    graph["edges"][0]["to"]["pinId"] = "p:" + _guid(9_000_001)
                elif mutation == "direction":
                    graph["pins"][1]["direction"] = "output"
                elif mutation == "asymmetric":
                    graph["pins"][1]["links"] = []
                else:
                    graph["edges"].append(copy.deepcopy(graph["edges"][0]))
                self._assert_rejected(typed_graph.freeze_hashes(graph), code)

    def test_exec_cycle_is_one_scc_and_does_not_overlap_its_exit(self):
        graph = _graph(
            {"entry": "event", "a": "call", "b": "call", "c": "call", "exit": "call"},
            [("entry", "a", "exec"), ("a", "b", "exec"), ("b", "c", "exec"), ("c", "a", "exec"), ("c", "exit", "exec")],
        )
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        cycle = {_id(graph, name) for name in ("a", "b", "c")}
        self.assertTrue(any(set(record["nodeIds"]) == cycle for record in plan["sccs"]))
        positions = _positions(plan)
        self.assertLess(positions[_id(graph, "entry")]["x"], min(positions[node]["x"] for node in cycle))
        self.assertGreater(positions[_id(graph, "exit")]["x"], max(positions[node]["x"] for node in cycle))

    def test_data_cycle_is_finite_but_unanchored_knot_cycle_is_rejected(self):
        graph = _graph(
            {"a": "variable-get", "b": "variable-get", "c": "variable-get"},
            [("a", "b", "data"), ("b", "c", "data"), ("c", "a", "data")],
        )
        self._assert_disjoint(graph, self._preview(graph))
        # A closed network of routing elements has no real producer or
        # consumer, so it must not be mistaken for a valid data dependency.
        knots = _graph(
            {"a": "reroute", "b": "reroute", "c": "reroute"},
            [("a", "b", "data"), ("b", "c", "data"), ("c", "a", "data")],
        )
        self._assert_rejected(knots, "layout-reroute-broken")

    def test_multiple_exec_branches_use_layers_and_keep_forward_flow(self):
        graph = _graph(
            {"entry": "event", "a": "call", "b": "call", "a1": "call", "b1": "call", "join": "call"},
            [
                ("entry", "a", "exec"),
                ("entry", "b", "exec"),
                ("a", "a1", "exec"),
                ("b", "b1", "exec"),
                ("a1", "join", "exec"),
                ("b1", "join", "exec"),
            ],
            sizes={"a": (520, 80), "b": (160, 220)},
        )
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        positions = _positions(plan)
        self.assertEqual(positions[_id(graph, "a")]["x"], positions[_id(graph, "b")]["x"])
        self.assertNotEqual(positions[_id(graph, "a")]["y"], positions[_id(graph, "b")]["y"])
        for edge in graph["edges"]:
            left, right = positions[edge["from"]["nodeId"]], positions[edge["to"]["nodeId"]]
            self.assertLessEqual(
                left["x"] + left["size"]["width"],
                right["x"],
                "exec edge folds back through another layer",
            )

    def test_transitive_pure_dependencies_stay_with_exec_consumer(self):
        graph = _graph(
            {
                "entry": "event",
                "call": "call",
                "first": "variable-get",
                "second": "variable-get",
                "unrelated": "variable-get",
            },
            [("entry", "call", "exec"), ("first", "second", "data"), ("second", "call", "data")],
            sizes={"first": (600, 100)},
        )
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        island = next(record for record in plan["islands"] if _id(graph, "call") in record["nodeIds"])
        self.assertTrue({_id(graph, name) for name in ("first", "second")} <= set(island["nodeIds"]))
        all_members = [node for record in plan["islands"] for node in record["nodeIds"]]
        self.assertEqual(len(all_members), len(set(all_members)), "shared pure nodes were duplicated")

    def test_isolated_nodes_pack_without_one_extreme_column(self):
        graph = _graph({f"isolated_{index}": "variable-get" for index in range(80)}, [])
        plan = self._preview(graph, {"mode": "full", "packing": {"targetWidth": 1600}})
        self._assert_disjoint(graph, plan)
        self.assertGreater(len({item["x"] for item in plan["positions"]}), 1)
        self.assertGreater(len({item["y"] for item in plan["positions"]}), 1)
        self.assertLessEqual(plan["metrics"]["bbox"]["width"], 1600)

    def test_chain_beyond_python_recursion_limit_can_preview_in_scope(self):
        count = max(1200, sys.getrecursionlimit() + 50)
        names = [f"node_{index}" for index in range(count)]
        graph = _graph(
            {name: "event" if index == 0 else "call" for index, name in enumerate(names)},
            [(names[index], names[index + 1], "exec") for index in range(count - 1)],
        )
        scope = [_id(graph, names[-1])]
        plan = self._preview(graph, {"mode": "incremental", "scope": scope})
        self.assertEqual(plan["movedNodeIds"], [], "readable scoped tail should remain in place")
        self.assertEqual(len(plan["fixedNodeIds"]), count - 1)

    def test_large_full_layout_preserves_1000_operation_apply_limit(self):
        graph = _graph(
            {f"node_{index}": "variable-get" for index in range(1001)},
            [],
            positions={f"node_{index}": (5000, 5000) for index in range(1001)},
        )
        self._assert_rejected(graph, "graphpatch-limit-exceeded")

    def test_comment_dimensions_and_membership_survive_full_layout(self):
        graph = _graph(
            {"a": "event", "b": "call", "comment": "comment", "outside": "call"},
            [("a", "b", "exec")],
            positions={"a": (100, 100), "b": (360, 100), "comment": (50, 50), "outside": (1200, 0)},
            sizes={"comment": (700, 260)},
            comments={"comment": ["a", "b"]},
        )
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        positions = _positions(plan)
        comment = positions[_id(graph, "comment")]
        self.assertEqual((comment["size"]["width"], comment["size"]["height"]), (700, 260))
        for name in ("a", "b"):
            member = positions[_id(graph, name)]
            self.assertLessEqual(comment["x"], member["x"])
            self.assertLessEqual(comment["y"], member["y"])
            self.assertGreaterEqual(comment["x"] + 700, member["x"] + member["size"]["width"])
            self.assertGreaterEqual(comment["y"] + 260, member["y"] + member["size"]["height"])
        self.assertFalse(
            _overlaps(comment, positions[_id(graph, "outside")]),
            "unrelated content was packed into a comment group",
        )

    def test_tight_comment_moves_as_group_instead_of_resizing_for_layers(self):
        graph = _graph(
            {"a": "event", "b": "call", "comment": "comment"},
            [("a", "b", "exec")],
            positions={"a": (30, 30), "b": (30, 150), "comment": (0, 0)},
            sizes={"comment": (240, 270)},
            comments={"comment": ["a", "b"]},
        )
        positions = _positions(self._preview(graph))
        comment = positions[_id(graph, "comment")]
        self.assertEqual((comment["size"]["width"], comment["size"]["height"]), (240, 270))
        for name, offset in (("a", (30, 30)), ("b", (30, 150))):
            member = positions[_id(graph, name)]
            self.assertEqual((member["x"] - comment["x"], member["y"] - comment["y"]), offset)

    def test_scope_keeps_every_out_of_scope_node_including_comment_fixed(self):
        graph = _graph(
            {"a": "event", "b": "call", "comment": "comment", "other": "call"},
            [("a", "b", "exec")],
            positions={"a": (100, 100), "b": (400, 100), "comment": (50, 50), "other": (1200, 800)},
            sizes={"comment": (700, 260)},
            comments={"comment": ["a", "b"]},
        )
        # Selecting all contents must not implicitly select their comment.
        scope = [_id(graph, "a"), _id(graph, "b")]
        for mode in ("selection", "incremental"):
            with self.subTest(mode=mode):
                plan = self._preview(graph, {"mode": mode, "scope": scope})
                positions = _positions(plan)
                for node in graph["nodes"]:
                    if node["nodeId"] not in scope:
                        item = positions[node["nodeId"]]
                        self.assertEqual((item["x"], item["y"]), (node["position"]["x"], node["position"]["y"]))
                        if node["kind"] != "comment":
                            self.assertIn(node["nodeId"], plan["fixedNodeIds"])
                # V1 Apply metadata enumerates fixed content nodes only;
                # every unselected comment position is still independently fixed.
                self.assertEqual(
                    set(plan["fixedNodeIds"]),
                    {node["nodeId"] for node in graph["nodes"]
                     if node["kind"] != "comment" and node["nodeId"] not in scope},
                )
                self.assertTrue(set(plan["movedNodeIds"]) <= set(scope))

    def test_conflict_free_hand_placed_scope_is_exact_noop(self):
        graph = _graph(
            {"entry": "event", "a": "call", "b": "call", "unrelated": "variable-get"},
            [("entry", "a", "exec"), ("a", "b", "exec")],
            positions={"entry": (-1000, -500), "a": (200, 370), "b": (800, 400), "unrelated": (2000, 1600)},
        )
        scope = [_id(graph, name) for name in ("a", "b")]
        for mode in ("selection", "incremental"):
            with self.subTest(mode=mode):
                plan = self._preview(graph, {"mode": mode, "scope": scope})
                self.assertEqual(plan["movedNodeIds"], [])
                self.assertEqual(plan["layoutHash"], graph["layoutHash"])

    def test_scoped_overlap_moves_scope_without_touching_fixed_nodes(self):
        graph = _graph(
            {"fixed": "call", "selected": "call", "other": "variable-get"},
            [],
            positions={"fixed": (100, 100), "selected": (100, 100), "other": (800, 0)},
        )
        selected = _id(graph, "selected")
        for mode in ("selection", "incremental"):
            with self.subTest(mode=mode):
                plan = self._preview(graph, {"mode": mode, "scope": [selected]})
                self.assertEqual(plan["movedNodeIds"], [selected])
                self._assert_disjoint(graph, plan)
                positions = _positions(plan)
                self.assertEqual((positions[_id(graph, "fixed")]["x"], positions[_id(graph, "fixed")]["y"]), (100, 100))

    def test_node_pin_edge_comment_link_order_do_not_change_plan(self):
        graph = _graph(
            {
                "entry": "event",
                "a": "call",
                "b": "call",
                "data": "variable-get",
                "knot": "reroute",
                "comment": "comment",
            },
            [
                ("entry", "a", "exec"),
                ("entry", "b", "exec"),
                ("data", "knot", "data"),
                ("knot", "a", "data"),
                ("knot", "b", "data"),
            ],
            positions={
                "entry": (100, 100),
                "a": (400, 100),
                "b": (400, 300),
                "data": (100, 450),
                "knot": (300, 460),
                "comment": (0, 0),
            },
            sizes={"comment": (900, 650)},
            comments={"comment": ["entry", "a", "b", "data", "knot"]},
        )
        expected = self._preview(graph)
        for seed in (1, 7, 31):
            with self.subTest(seed=seed):
                shuffled, rng = copy.deepcopy(graph), random.Random(seed)
                for field in ("nodes", "pins", "edges", "comments"):
                    rng.shuffle(shuffled[field])
                for node in shuffled["nodes"]:
                    rng.shuffle(node["pins"])
                for pin in shuffled["pins"]:
                    rng.shuffle(pin["links"])
                for comment in shuffled["comments"]:
                    rng.shuffle(comment["containedNodeIds"])
                self.assertEqual(self._preview(typed_graph.freeze_hashes(shuffled)), expected)

    def test_complete_signed_int32_range_is_valid_for_fixed_coordinates(self):
        graph = _graph(
            {"low": "variable-get", "high": "variable-get", "scope": "variable-get"},
            [],
            positions={"low": (INT32_MIN, INT32_MIN), "high": (INT32_MAX, INT32_MAX), "scope": (0, 0)},
        )
        plan = self._preview(graph, {"mode": "selection", "scope": [_id(graph, "scope")]})
        self.assertEqual(plan["movedNodeIds"], [])
        positions = _positions(plan)
        self.assertEqual(positions[_id(graph, "low")]["x"], INT32_MIN)
        self.assertEqual(positions[_id(graph, "high")]["y"], INT32_MAX)

    def test_outside_int32_rejected_even_when_full_layout_could_normalize(self):
        for coordinate in (INT32_MIN - 1, INT32_MAX + 1):
            with self.subTest(coordinate=coordinate):
                self._assert_rejected(
                    _graph({"invalid": "variable-get"}, [], positions={"invalid": (coordinate, 0)}),
                    "graph-invalid-coordinate",
                )

    def test_extreme_stored_dimensions_are_not_truncated_or_overlapped(self):
        graph = _graph(
            {"wide": "variable-get", "small": "variable-get", "tall": "variable-get"},
            [("wide", "small", "data")],
            sizes={"wide": (2_000_000, 100), "tall": (160, 1_000_000)},
        )
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        positions = _positions(plan)
        self.assertEqual(positions[_id(graph, "wide")]["size"]["width"], 2_000_000)
        self.assertEqual(positions[_id(graph, "tall")]["size"]["height"], 1_000_000)


    def test_direct_layout_graph_is_pure_including_comment_bounds(self):
        graph = _graph(
            {"a": "event", "b": "call", "comment": "comment"},
            [("a", "b", "exec")],
            positions={"a": (100, 100), "b": (360, 100), "comment": (50, 50)},
            sizes={"comment": (700, 260)},
            comments={"comment": ["a", "b"]},
        )
        request = {"mode": "full", "options": {"spacingX": 80, "spacingY": 40}}
        before, request_before = copy.deepcopy(graph), copy.deepcopy(request)
        result = layout.layout_graph(graph, request)
        self.assertEqual(result.status, "ok", result.errors)
        self.assertEqual(graph, before, "direct layout_graph mutated comment bounds through an alias")
        self.assertEqual(request, request_before)

    def test_data_only_component_does_not_absorb_already_assigned_pure_nodes(self):
        graph = _graph(
            {"entry": "event", "consumer": "call", "shared": "variable-get", "terminal": "variable-get"},
            [("entry", "consumer", "exec"), ("shared", "consumer", "data"), ("shared", "terminal", "data")],
        )
        plan = self._preview(graph)
        self._assert_disjoint(graph, plan)
        members = [node_id for island in plan["islands"] for node_id in island["nodeIds"]]
        self.assertCountEqual(
            members,
            [node["nodeId"] for node in graph["nodes"]],
            "islands must partition every content node exactly once",
        )
        assigned = next(island for island in plan["islands"] if _id(graph, "consumer") in island["nodeIds"])
        self.assertIn(_id(graph, "shared"), assigned["nodeIds"])
        self.assertNotIn(_id(graph, "terminal"), assigned["nodeIds"])

    def test_knot_with_mismatched_data_pin_types_is_rejected(self):
        graph = _graph(
            {"source": "variable-get", "knot": "reroute", "sink": "variable-get"},
            [("source", "knot", "data"), ("knot", "sink", "data")],
        )
        output = next(
            pin for pin in graph["pins"]
            if pin["nodeId"] == _id(graph, "knot") and pin["direction"] == "output"
        )
        output["type"]["category"] = "int"
        graph = typed_graph.freeze_hashes(graph)
        self.assertTrue(typed_graph.validate(graph).ok, "fixture must reach Knot-specific validation")
        self._assert_rejected(graph, "layout-reroute-broken")

    def test_scoped_preview_preserves_and_reports_unrelated_fixed_overlap(self):
        graph = _graph(
            {"fixed_a": "call", "fixed_b": "call", "selected": "call"}, [],
            positions={"fixed_a": (100, 100), "fixed_b": (100, 100), "selected": (800, 800)},
        )
        selected = _id(graph, "selected")
        for mode in ("selection", "incremental"):
            with self.subTest(mode=mode):
                plan = self._preview(graph, {"mode": mode, "scope": [selected]})
                self.assertEqual(plan["movedNodeIds"], [])
                self.assertEqual(plan["metrics"]["overlapCount"], 1, "fixed overlap must remain visible in metrics")
                self.assertEqual(plan["layoutHash"], graph["layoutHash"])
                self.assertTrue({_id(graph, "fixed_a"), _id(graph, "fixed_b")} <= set(plan["fixedNodeIds"]))

    def test_unknown_comment_node_size_uses_immutable_stored_bounds(self):
        graph = _graph(
            {"member": "call", "comment": "comment"}, [],
            positions={"member": (100, 100), "comment": (50, 50)},
            sizes={"comment": (0, 0)}, comments={"comment": ["member"]},
        )
        next(node for node in graph["nodes"] if node["kind"] == "comment")["size"]["source"] = "unknown"
        graph["comments"][0]["bounds"].update(width=700, height=260)
        graph = typed_graph.freeze_hashes(graph)
        positions = _positions(self._preview(graph))
        frame, member = positions[_id(graph, "comment")], positions[_id(graph, "member")]
        self.assertEqual(frame["size"], {"width": 700, "height": 260, "source": "stored"})
        self.assertLessEqual(frame["x"], member["x"])
        self.assertLessEqual(frame["y"], member["y"])
        self.assertGreaterEqual(frame["x"] + 700, member["x"] + member["size"]["width"])
        self.assertGreaterEqual(frame["y"] + 260, member["y"] + member["size"]["height"])

    def test_comment_node_and_bounds_position_or_size_conflicts_fail_closed(self):
        original = _graph(
            {"member": "call", "comment": "comment"}, [],
            positions={"member": (100, 100), "comment": (50, 50)},
            sizes={"comment": (700, 260)}, comments={"comment": ["member"]},
        )
        for field, value in (("x", 10000), ("y", -10000), ("width", 701), ("height", 261)):
            with self.subTest(field=field):
                graph = copy.deepcopy(original)
                graph["comments"][0]["bounds"][field] = value
                graph = typed_graph.freeze_hashes(graph)
                self.assertTrue(typed_graph.validate(graph).ok, "fixture must reach layout consistency validation")
                self._assert_rejected(graph, "layout-size-mismatch")

    def test_unknown_member_at_comment_edge_keeps_authored_and_estimated_centers_inside(self):
        graph = _graph(
            {"unknown": "call", "comment": "comment"}, [],
            positions={"unknown": (680, 100), "comment": (0, 0)},
            sizes={"unknown": (0, 0), "comment": (700, 260)},
            comments={"comment": ["unknown"]},
        )
        next(node for node in graph["nodes"] if node["title"] == "unknown")["size"]["source"] = "unknown"
        graph = typed_graph.freeze_hashes(graph)
        for request in ({"mode": "full"}, {"mode": "incremental", "scope": [_id(graph, "unknown")]}):
            with self.subTest(mode=request["mode"]):
                positions = _positions(self._preview(graph, request))
                frame, member = positions[_id(graph, "comment")], positions[_id(graph, "unknown")]
                self.assertEqual((frame["size"]["width"], frame["size"]["height"]), (700, 260))
                # Original zero-size geometry puts the node's center at its
                # position. Estimated geometry must preserve that membership.
                for center in ((member["x"], member["y"]),
                               (member["x"] + member["size"]["width"] // 2,
                                member["y"] + member["size"]["height"] // 2)):
                    self.assertLessEqual(frame["x"], center[0])
                    self.assertLessEqual(center[0], frame["x"] + 700)
                    self.assertLessEqual(frame["y"], center[1])
                    self.assertLessEqual(center[1], frame["y"] + 260)

    def test_quality_refinement_cannot_pull_external_node_into_unrelated_comment(self):
        # Both incoming wires favor moving the external target into the empty
        # right side of the frame. Content collision checks alone permit it.
        graph = _graph(
            {"a": "call", "b": "call", "external": "call", "spacer": "call", "frame": "comment"},
            [("a", "external", "data"), ("b", "external", "data")],
            positions={"a": (50, 50), "b": (50, 150), "external": (400, 400),
                       "spacer": (800, 600), "frame": (0, 0)},
            sizes={"a": (80, 40), "b": (80, 40), "external": (80, 40),
                   "spacer": (80, 40), "frame": (700, 260)},
            comments={"frame": ["a", "b"]},
        )
        nodes = {node["nodeId"]: node for node in graph["nodes"]}
        positions = {node_id: (node["position"]["x"], node["position"]["y"]) for node_id, node in nodes.items()}
        sizes = {node_id: node["size"] for node_id, node in nodes.items()}
        content = {node_id for node_id, node in nodes.items() if node["kind"] != "comment"}
        edges = [{"source": edge["from"]["nodeId"], "target": edge["to"]["nodeId"],
                  "edgeId": edge["edgeId"], "kind": edge["kind"]} for edge in graph["edges"]]
        refined = _layout_layered.refine(
            positions, content, edges, sizes,
            {_id(graph, "frame"): {_id(graph, "a"), _id(graph, "b")}}, 40, nodes,
        )
        external, frame = _id(graph, "external"), _id(graph, "frame")
        self.assertFalse(_overlaps(
            {"x": refined[external][0], "y": refined[external][1], "size": sizes[external]},
            {"x": refined[frame][0], "y": refined[frame][1], "size": sizes[frame]},
        ), "a shorter wire must not create unrelated comment membership")

    def test_150_node_comment_remains_bounded_and_keeps_dimensions(self):
        names = [f"member_{index}" for index in range(150)]
        kinds = {name: "call" for name in names}
        kinds["frame"] = "comment"
        positions = {name: (30 + (index % 10) * 240, 30 + (index // 10) * 130)
                     for index, name in enumerate(names)}
        positions["frame"] = (0, 0)
        graph = _graph(
            kinds, [(names[index], names[index + 1], "exec") for index in range(149)],
            positions=positions, sizes={"frame": (2450, 2100)}, comments={"frame": names},
        )
        started = time.perf_counter()
        plan = self._preview(graph)
        elapsed = time.perf_counter() - started
        self.assertLess(elapsed, 30.0, f"150-node comment preview took {elapsed:.3f}s")
        self._assert_disjoint(graph, plan)
        actual = _positions(plan)
        frame = actual[_id(graph, "frame")]
        self.assertEqual((frame["size"]["width"], frame["size"]["height"]), (2450, 2100))
        for name in names:
            member = actual[_id(graph, name)]
            self.assertLessEqual(frame["x"], member["x"])
            self.assertLessEqual(frame["y"], member["y"])
            self.assertGreaterEqual(frame["x"] + 2450, member["x"] + member["size"]["width"])
            self.assertGreaterEqual(frame["y"] + 2100, member["y"] + member["size"]["height"])

    def test_nested_empty_comment_keeps_its_authored_offset_inside_parent(self):
        graph = _graph(
            {"member": "call", "parent": "comment", "empty_child": "comment"}, [],
            positions={"member": (100, 100), "parent": (0, 0), "empty_child": (200, 200)},
            sizes={"parent": (1000, 600), "empty_child": (200, 150)},
            comments={"parent": ["member"], "empty_child": []},
        )
        actual = _positions(self._preview(graph))
        parent, child = actual[_id(graph, "parent")], actual[_id(graph, "empty_child")]
        self.assertEqual((child["x"] - parent["x"], child["y"] - parent["y"]), (200, 200))
        self.assertEqual((parent["size"]["width"], parent["size"]["height"]), (1000, 600))
        self.assertEqual((child["size"]["width"], child["size"]["height"]), (200, 150))
        member = actual[_id(graph, "member")]
        self.assertLessEqual(parent["x"], member["x"])
        self.assertGreaterEqual(parent["x"] + 1000, member["x"] + member["size"]["width"])
        self.assertFalse(_overlaps(member, child), "empty nested frame acquired unrelated content")

    def test_duplicate_comment_record_is_rejected_regardless_of_array_order(self):
        graph = _graph(
            {"member": "call", "frame": "comment"}, [],
            positions={"member": (100, 100), "frame": (0, 0)},
            sizes={"frame": (700, 260)}, comments={"frame": ["member"]},
        )
        duplicate = copy.deepcopy(graph["comments"][0])
        duplicate["bounds"]["x"] = 10000
        graph["comments"].append(duplicate)
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                candidate = copy.deepcopy(graph)
                if reverse:
                    candidate["comments"].reverse()
                self._assert_rejected(typed_graph.freeze_hashes(candidate), "layout-size-mismatch")

if __name__ == "__main__":
    unittest.main()

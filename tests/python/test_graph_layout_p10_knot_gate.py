"""P10 knot-shape gate: legal data fan-out / exec merge vs fail-closed shapes.

These tests exercise the client-side apply gate offline.  They deliberately do
not claim Editor acceptance: the C++ handler revalidates the live pins and
links, and that path is covered by the P10 disposable-host automation test.
"""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.graph_contracts.layout import recompute_layout_hash  # noqa: E402
from ue_agent_kit.graph_contracts.layout_apply import (  # noqa: E402
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
)

ASSET = "/Game/UEAgentKitP10/BP_LayoutP10.BP_LayoutP10"
GRAPH_GUID = "ae318ca3-4c74-16fa-240d-11a64a8d97d4"
SHA256 = "sha256:" + "a" * 64
SHA1 = "sha1:" + "b" * 40


def _guid(index: int) -> str:
    return f"{index:08x}-1111-2222-3333-{index:012x}"


def _node(
    index: int,
    kind: str = "node",
    *,
    x: int = 0,
    y: int = 0,
    input_pins: int = 0,
    output_pins: int = 0,
    input_links: int = 0,
    output_links: int = 0,
    knot: dict | None = None,
) -> dict:
    item = {
        "nodeId": "n:" + _guid(index),
        "guid": _guid(index),
        "classPath": "/Script/BlueprintGraph.K2Node_Knot" if kind == "reroute" else "/Script/Engine.K2Node_CallFunction",
        "kind": kind,
        "x": x,
        "y": y,
        "width": 200,
        "height": 100,
        "inputPinCount": input_pins,
        "outputPinCount": output_pins,
        "inputLinkCount": input_links,
        "outputLinkCount": output_links,
    }
    if knot is not None:
        item["knot"] = knot
    return item


def _knot(in_type: str, out_type: str, sources: list[str], sinks: list[str]) -> dict:
    return {
        "inputType": in_type,
        "outputType": out_type,
        "inputSources": sources,
        "outputSinks": sinks,
    }


def _preflight(nodes: list[dict]) -> dict:
    return {
        "schemaVersion": "ueak.graphlayoutpreflight/1.0",
        "assetPath": ASSET,
        "graphGuid": GRAPH_GUID,
        "packageRevision": SHA256,
        "packageDirty": False,
        "graphFingerprint": SHA1,
        "nodeCount": len(nodes),
        "nodes": copy.deepcopy(nodes),
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
    }


def _plan(nodes: list[dict], moved: set[str]) -> dict:
    positions = []
    for node in nodes:
        x, y = node["x"], node["y"]
        if node["nodeId"] in moved:
            x += 400
            y += 40
        positions.append(
            {
                "nodeId": node["nodeId"],
                "x": x,
                "y": y,
                "size": {"width": node["width"], "height": node["height"], "source": "stored"},
            }
        )
    plan = {
        "schemaVersion": "ueak.layoutplan/1.0",
        "planId": "layout:p10knot",
        "status": "ok",
        "errorCode": "",
        "mode": "full",
        "baseGraphHash": SHA256,
        "layoutHash": "",
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "positions": positions,
        "movedNodeIds": sorted(moved),
        "scopeNodeIds": sorted(node["nodeId"] for node in nodes if node["kind"] != "comment"),
        "fixedNodeIds": [],
        "islands": [],
        "warnings": [],
    }
    plan["layoutHash"] = recompute_layout_hash(plan)
    return plan


class P10KnotGateTest(unittest.TestCase):
    def _build(self, nodes: list[dict], moved: set[str]) -> dict:
        return build_graph_layout_apply_request(_plan(nodes, moved), _preflight(nodes))

    def _code(self, nodes: list[dict], moved: set[str]) -> str:
        with self.assertRaises(GraphLayoutApplyError) as caught:
            self._build(nodes, moved)
        return caught.exception.code

    # --- legal shapes -------------------------------------------------

    def test_legacy_one_to_one_knot_without_evidence_is_accepted(self) -> None:
        source = _node(1, x=0, output_pins=1, output_links=1)
        knot = _node(2, "reroute", x=300, input_pins=1, output_pins=1, input_links=1, output_links=1)
        sink = _node(3, x=600, input_pins=1, input_links=1)
        request = self._build([source, knot, sink], {knot["nodeId"]})
        self.assertEqual(request["requestedMode"], "position-only")

    def test_data_fanout_with_evidence_is_accepted(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        knot = _node(
            2,
            "reroute",
            x=300,
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=2,
            knot=_knot("string", "string", [source["guid"]], []),
        )
        sink_a = _node(3, x=600, input_pins=1, input_links=1)
        sink_b = _node(4, x=900, input_pins=1, input_links=1)
        knot["knot"]["outputSinks"] = [sink_a["guid"], sink_b["guid"]]
        request = self._build([source, knot, sink_a, sink_b], {knot["nodeId"]})
        self.assertEqual(request["schemaVersion"], "ueak.graphlayoutapply/1.0")

    def test_exec_merge_with_evidence_is_accepted(self) -> None:
        source_a = _node(1, output_pins=1, output_links=1)
        source_b = _node(2, output_pins=1, output_links=1)
        knot = _node(
            3,
            "reroute",
            x=300,
            input_pins=1,
            output_pins=1,
            input_links=2,
            output_links=1,
            knot=_knot("exec", "exec", [source_a["guid"], source_b["guid"]], []),
        )
        sink = _node(4, x=600, input_pins=1, input_links=1)
        knot["knot"]["outputSinks"] = [sink["guid"]]
        self._build([source_a, source_b, knot, sink], {knot["nodeId"]})

    def test_two_knot_chain_fanout_is_accepted(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        first = _node(
            2,
            "reroute",
            x=200,
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("int", "int", [source["guid"]], ["%s" % _guid(3)]),
        )
        second = _node(
            3,
            "reroute",
            x=400,
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=2,
            knot=_knot("int", "int", [first["guid"]], ["%s" % _guid(4), "%s" % _guid(5)]),
        )
        sink_a = _node(4, x=600, input_pins=1, input_links=1)
        sink_b = _node(5, x=800, input_pins=1, input_links=1)
        self._build([source, first, second, sink_a, sink_b], {first["nodeId"], second["nodeId"]})

    # --- rejected shapes ----------------------------------------------

    def test_data_fanout_without_evidence_fails(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        knot = _node(2, "reroute", input_pins=1, output_pins=1, input_links=1, output_links=2)
        self.assertEqual(
            self._code([source, knot], {knot["nodeId"]}),
            "layout-apply-reroute-broken",
        )

    def test_data_merge_is_rejected(self) -> None:
        source_a = _node(1, output_pins=1, output_links=1)
        source_b = _node(2, output_pins=1, output_links=1)
        knot = _node(
            3,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=2,
            output_links=1,
            knot=_knot("string", "string", [source_a["guid"], source_b["guid"]], [_guid(4)]),
        )
        self.assertEqual(self._code([source_a, source_b, knot], {knot["nodeId"]}), "layout-apply-reroute-broken")

    def test_exec_fanout_is_rejected(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        knot = _node(
            2,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=2,
            knot=_knot("exec", "exec", [source["guid"]], [_guid(3), _guid(4)]),
        )
        self.assertEqual(self._code([source, knot], {knot["nodeId"]}), "layout-apply-reroute-broken")

    def test_zero_input_knot_is_rejected(self) -> None:
        knot = _node(
            1,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=0,
            output_links=1,
            knot=_knot("exec", "exec", [], [_guid(2)]),
        )
        self.assertEqual(self._code([knot], {knot["nodeId"]}), "layout-apply-reroute-broken")

    def test_broken_middle_knot_is_rejected(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        middle = _node(
            2,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=0,
            output_links=1,
            knot=_knot("float", "float", [], [_guid(3)]),
        )
        outer = _node(
            3,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("float", "float", [middle["guid"]], [_guid(4)]),
        )
        self.assertEqual(self._code([source, middle, outer], {outer["nodeId"]}), "layout-apply-reroute-broken")

    def test_pin_type_mismatch_is_rejected(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        knot = _node(
            2,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("int", "float", [source["guid"]], [_guid(3)]),
        )
        self.assertEqual(self._code([source, knot], {knot["nodeId"]}), "layout-apply-reroute-broken")

    def test_wildcard_type_is_rejected(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        knot = _node(
            2,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("wildcard", "wildcard", [source["guid"]], [_guid(3)]),
        )
        self.assertEqual(self._code([source, knot], {knot["nodeId"]}), "layout-apply-reroute-broken")

    def test_evidence_link_count_mismatch_is_rejected(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        knot = _node(
            2,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=2,
            knot=_knot("string", "string", [source["guid"]], [_guid(3)]),
        )
        self.assertEqual(self._code([source, knot], {knot["nodeId"]}), "layout-apply-reroute-broken")

    def test_cycle_is_rejected(self) -> None:
        first = _node(
            1,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("string", "string", [_guid(2)], [_guid(2)]),
        )
        second = _node(
            2,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("string", "string", [_guid(1)], [_guid(1)]),
        )
        self.assertEqual(self._code([first, second], {first["nodeId"]}), "layout-apply-reroute-broken")

    # --- preflight evidence parsing -----------------------------------

    def test_self_peer_is_rejected_at_preflight(self) -> None:
        knot = _node(
            1,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("string", "string", [_guid(1)], [_guid(2)]),
        )
        self.assertEqual(self._code([knot], {knot["nodeId"]}), "layout-apply-preflight-invalid")

    def test_repeated_source_node_links_are_allowed(self) -> None:
        # A Sequence can feed an exec merge twice from two different pins of the
        # same node; the evidence lists the peer twice and the link count is 2.
        source = _node(1, output_pins=2, output_links=2)
        sink = _node(3, input_pins=1, input_links=1)
        knot = _node(
            2,
            "reroute",
            input_pins=1,
            output_pins=1,
            input_links=2,
            output_links=1,
            knot=_knot("exec", "exec", [source["guid"], source["guid"]], [sink["guid"]]),
        )
        self._build([source, knot, sink], {knot["nodeId"]})

    def test_evidence_on_non_reroute_is_rejected(self) -> None:
        node = _node(
            1,
            "node",
            input_pins=1,
            output_pins=1,
            input_links=1,
            output_links=1,
            knot=_knot("string", "string", [_guid(2)], [_guid(3)]),
        )
        self.assertEqual(self._code([node], {node["nodeId"]}), "layout-apply-preflight-invalid")

    def test_non_moved_broken_knot_is_not_gated(self) -> None:
        source = _node(1, output_pins=1, output_links=1)
        knot = _node(2, "reroute", input_pins=1, output_pins=1, input_links=0, output_links=1)
        sink = _node(3, x=600, input_pins=1, input_links=1)
        # Move only the sink; the untouched broken knot must not block it.
        self._build([source, knot, sink], {sink["nodeId"]})

    def test_reroute_pin_count_must_stay_two_pins(self) -> None:
        source_a = _node(1, output_pins=1, output_links=1)
        source_b = _node(2, output_pins=1, output_links=1)
        knot = _node(
            3,
            "reroute",
            input_pins=2,
            output_pins=1,
            input_links=2,
            output_links=1,
            knot=_knot("string", "string", [source_a["guid"], source_b["guid"]], [_guid(4)]),
        )
        self.assertEqual(
            self._code([source_a, source_b, knot], {knot["nodeId"]}),
            "layout-apply-reroute-broken",
        )


if __name__ == "__main__":
    unittest.main()

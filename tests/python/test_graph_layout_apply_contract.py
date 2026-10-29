from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from ue_agent_kit.graph_contracts import (
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
    preview_graph_layout,
    validate_contract_document,
)
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash
from ue_agent_kit.graph_contracts.layout_apply import (
    APPLY_SCHEMA_VERSION,
    PREFLIGHT_SCHEMA_VERSION,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "graph_contracts"


def _graph() -> dict:
    return json.loads((FIXTURES / "tg_comment_reroute.json").read_text(encoding="utf-8"))


def _preflight(graph: dict | None = None) -> dict:
    graph = graph or _graph()
    nodes = []
    for node in graph["nodes"]:
        pins = [pin for pin in graph["pins"] if pin["nodeId"] == node["nodeId"]]
        input_pins = [pin for pin in pins if pin["direction"] == "input"]
        output_pins = [pin for pin in pins if pin["direction"] == "output"]
        kind = "comment" if node["kind"] == "comment" else ("reroute" if node["kind"] == "reroute" else "node")
        nodes.append(
            {
                "nodeId": node["nodeId"],
                "guid": node["nodeId"][2:],
                "classPath": node["classPath"],
                "kind": kind,
                "x": node["position"]["x"],
                "y": node["position"]["y"],
                "width": node["size"]["width"],
                "height": node["size"]["height"],
                "inputPinCount": len(input_pins),
                "outputPinCount": len(output_pins),
                "inputLinkCount": sum(len(pin["links"]) for pin in input_pins),
                "outputLinkCount": sum(len(pin["links"]) for pin in output_pins),
            }
        )
    return {
        "schemaVersion": PREFLIGHT_SCHEMA_VERSION,
        "assetPath": graph["asset"]["path"],
        "graphGuid": graph["graph"]["guid"],
        "graphClassPath": "/Script/Engine.EdGraph",
        "schemaClassPath": graph["graph"]["schema"],
        "packageRevision": graph["asset"]["revision"],
        "packageDirty": False,
        "graphFingerprint": "sha1:" + "1" * 40,
        "graphFingerprintAlgorithm": "sha1-length-prefixed-graph-state-v1",
        "nodeCount": len(nodes),
        "commentCount": sum(1 for node in nodes if node["kind"] == "comment"),
        "rerouteCount": sum(1 for node in nodes if node["kind"] == "reroute"),
        "nodes": nodes,
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
    }


def _plan(graph: dict | None = None) -> dict:
    graph = graph or _graph()
    result = preview_graph_layout(graph, {"mode": "full"})
    assert result.status == "ok", result.errors
    return copy.deepcopy(result.plan)


def _recompute_moved(plan: dict, preflight: dict) -> None:
    current = {node["nodeId"]: (node["x"], node["y"]) for node in preflight["nodes"]}
    plan["movedNodeIds"] = sorted(
        item["nodeId"] for item in plan["positions"] if (item["x"], item["y"]) != current[item["nodeId"]]
    )
    plan["layoutHash"] = recompute_layout_hash(plan)


def _expect_error(test: unittest.TestCase, code: str, plan: dict, preflight: dict) -> None:
    with test.assertRaises(GraphLayoutApplyError) as raised:
        build_graph_layout_apply_request(plan, preflight)
    test.assertEqual(raised.exception.code, code)


class GraphLayoutApplyContractTests(unittest.TestCase):
    def test_good_plan_builds_exact_position_only_request_and_schema(self) -> None:
        plan = _plan()
        preflight = _preflight()
        self.assertEqual(validate_contract_document("layout-preflight", preflight)["diagnostics"], [])
        request = build_graph_layout_apply_request(plan, preflight)
        self.assertEqual(request["schemaVersion"], APPLY_SCHEMA_VERSION)
        self.assertEqual(request["assetPath"], preflight["assetPath"])
        self.assertEqual(request["graphGuid"], preflight["graphGuid"])
        self.assertEqual(request["expectedPackageRevision"], preflight["packageRevision"])
        self.assertEqual(request["expectedGraphFingerprint"], preflight["graphFingerprint"])
        self.assertEqual(request["requestedMode"], "position-only")
        self.assertFalse(request["allowSave"])
        self.assertFalse(request["allowCompile"])
        self.assertFalse(request["acceptanceEligible"])
        self.assertEqual(request["requiredAcceptanceEvidence"], "independent-reload")
        self.assertEqual(request["plan"], plan)
        self.assertEqual(validate_contract_document("layout-apply", request)["diagnostics"], [])

        unsafe = copy.deepcopy(request)
        unsafe["allowSave"] = True
        self.assertFalse(validate_contract_document("layout-apply", unsafe)["valid"])
        dirty_schema = _preflight()
        dirty_schema["packageDirty"] = True
        self.assertFalse(validate_contract_document("layout-preflight", dirty_schema)["valid"])

    def test_revision_mismatch_and_dirty_package_are_rejected(self) -> None:
        with self.assertRaises(GraphLayoutApplyError) as raised:
            build_graph_layout_apply_request(_plan(), _preflight(), expected_package_revision="sha256:" + "0" * 64)
        self.assertEqual(raised.exception.code, "layout-apply-revision-mismatch")

        dirty = _preflight()
        dirty["packageDirty"] = True
        _expect_error(self, "layout-apply-package-dirty", _plan(), dirty)

    def test_bad_canonical_guids_are_rejected(self) -> None:
        bad_graph = _preflight()
        bad_graph["graphGuid"] = "0000010A-0000-4000-8000-000000000107"
        _expect_error(self, "layout-apply-invalid-guid", _plan(), bad_graph)

        bad_plan = _plan()
        bad_plan["positions"][0]["nodeId"] = "n:0000000A-0000-4000-8000-000000000001"
        _expect_error(self, "layout-apply-invalid-guid", bad_plan, _preflight())

        bad_node = _preflight()
        bad_node["nodes"][0]["nodeId"] = "obj:not-a-guid"
        _expect_error(self, "layout-apply-invalid-guid", _plan(), bad_node)

        mismatched = _preflight()
        mismatched["nodes"][0]["guid"] = "00000002-0000-4000-8000-000000000002"
        _expect_error(self, "layout-apply-invalid-guid", _plan(), mismatched)

    def test_missing_unknown_and_duplicate_nodes_are_rejected(self) -> None:
        missing = _plan()
        missing["positions"].pop()
        _expect_error(self, "layout-apply-node-missing", missing, _preflight())

        unknown = _plan()
        unknown["positions"][0]["nodeId"] = "n:00000000-0000-4000-8000-00000000ffff"
        _expect_error(self, "layout-apply-node-unknown", unknown, _preflight())

        duplicate = _plan()
        duplicate["positions"].append(copy.deepcopy(duplicate["positions"][0]))
        _expect_error(self, "layout-apply-duplicate-node", duplicate, _preflight())

    def test_coordinates_are_int32_and_negative_is_valid(self) -> None:
        over = _plan()
        over["positions"][0]["x"] = 2_147_483_648
        _recompute_moved(over, _preflight())
        _expect_error(self, "layout-apply-coordinate-out-of-range", over, _preflight())

        negative = _plan()
        negative["positions"][0]["x"] = -120
        negative["positions"][0]["y"] = -80
        _recompute_moved(negative, _preflight())
        request = build_graph_layout_apply_request(negative, _preflight())
        self.assertEqual(request["plan"]["positions"][0]["x"], -120)

    def test_moved_ids_must_exactly_match_changed_positions(self) -> None:
        moved = _plan()
        moved["movedNodeIds"] = []
        _expect_error(self, "layout-apply-moved-mismatch", moved, _preflight())

    def test_moved_comment_may_keep_a_node_inside_its_new_frame(self) -> None:
        comment_plan = _plan()
        preflight = _preflight()
        comment = next(node for node in preflight["nodes"] if node["kind"] == "comment")
        fixed = next(node for node in preflight["nodes"] if node["kind"] != "comment")
        comment_item = next(
            item for item in comment_plan["positions"] if item["nodeId"] == comment["nodeId"]
        )
        # Move the frame by one pixel.  The fixed node keeps its exact
        # coordinates but is still inside the comment after the position-only
        # apply, so this is not a partial-scope corruption.
        comment_item["x"] = comment["x"] + 1
        comment_item["y"] = comment["y"] + 1
        fixed_item = next(
            item for item in comment_plan["positions"] if item["nodeId"] == fixed["nodeId"]
        )
        fixed_item["x"] = fixed["x"]
        fixed_item["y"] = fixed["y"]
        _recompute_moved(comment_plan, preflight)
        self.assertIn(comment["nodeId"], comment_plan["movedNodeIds"])
        self.assertNotIn(fixed["nodeId"], comment_plan["movedNodeIds"])
        request = build_graph_layout_apply_request(comment_plan, preflight)
        self.assertEqual(request["requestedMode"], "position-only")

    def test_scope_and_comment_partial_scope_and_reroute_are_rejected(self) -> None:
        full_scope = _plan()
        content_id = next(node["nodeId"] for node in _preflight()["nodes"] if node["kind"] != "comment")
        full_scope["scopeNodeIds"].remove(content_id)
        _expect_error(self, "layout-apply-scope-invalid", full_scope, _preflight())

        comment_plan = _plan()
        preflight = _preflight()
        comment_id = next(node["nodeId"] for node in preflight["nodes"] if node["kind"] == "comment")
        comment_position = next(item for item in comment_plan["positions"] if item["nodeId"] == comment_id)
        comment_position["x"] += 500
        fixed_node = next(node for node in preflight["nodes"] if node["kind"] != "comment")
        fixed_id = fixed_node["nodeId"]
        fixed_item = next(item for item in comment_plan["positions"] if item["nodeId"] == fixed_id)
        fixed_item["x"] = fixed_node["x"]
        fixed_item["y"] = fixed_node["y"]
        _recompute_moved(comment_plan, preflight)
        _expect_error(self, "layout-apply-comment-partial-scope", comment_plan, preflight)

        reroute_broken = _preflight()
        reroute = next(node for node in reroute_broken["nodes"] if node["kind"] == "reroute")
        reroute["inputLinkCount"] = 0
        reroute_plan = _plan()
        reroute_position = next(item for item in reroute_plan["positions"] if item["nodeId"] == reroute["nodeId"])
        reroute_position["x"] += 40
        _recompute_moved(reroute_plan, reroute_broken)
        _expect_error(self, "layout-apply-reroute-broken", reroute_plan, reroute_broken)

    def test_more_than_one_thousand_moves_is_rejected(self) -> None:
        nodes = []
        positions = []
        for index in range(1, 1002):
            guid = f"00000000-0000-4000-8000-{index:012x}"
            node_id = "n:" + guid
            nodes.append(
                {
                    "nodeId": node_id,
                    "guid": guid,
                    "classPath": "/Script/BlueprintGraph.K2Node_Event",
                    "kind": "node",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 40,
                    "inputPinCount": 0,
                    "outputPinCount": 0,
                    "inputLinkCount": 0,
                    "outputLinkCount": 0,
                }
            )
            positions.append(
                {
                    "nodeId": node_id,
                    "x": index,
                    "y": index,
                    "size": {"width": 100, "height": 40, "source": "stored"},
                }
            )
        preflight = {
            "schemaVersion": PREFLIGHT_SCHEMA_VERSION,
            "assetPath": "/Game/UEAgentKitTests/BP_Large.BP_Large",
            "graphGuid": "00000000-0000-4000-8000-000000000001",
            "packageRevision": "sha256:" + "2" * 64,
            "packageDirty": False,
            "graphFingerprint": "sha1:" + "3" * 40,
            "nodeCount": 1001,
            "nodes": nodes,
            "evidenceLevel": "same-process",
            "acceptanceEligible": False,
            "requiredAcceptanceEvidence": "independent-reload",
        }
        plan = {
            "schemaVersion": "ueak.layoutplan/1.0",
            "planId": "layout:test",
            "baseGraphHash": "sha256:" + "4" * 64,
            "layoutHash": "sha256:" + "5" * 64,
            "mode": "full",
            "positions": positions,
            "movedNodeIds": [item["nodeId"] for item in positions],
            "scopeNodeIds": [item["nodeId"] for item in positions],
            "fixedNodeIds": [],
            "status": "ok",
            "errorCode": "",
            "evidenceLevel": "same-process",
            "acceptanceEligible": False,
            "requiredAcceptanceEvidence": "independent-reload",
        }
        plan["layoutHash"] = recompute_layout_hash(plan)
        _expect_error(self, "layout-apply-limit-exceeded", plan, preflight)

    def test_rejected_plan_and_acceptance_claims_are_rejected(self) -> None:
        rejected = _plan()
        rejected["status"] = "rejected"
        _expect_error(self, "layout-apply-plan-rejected", rejected, _preflight())

        claiming = _plan()
        claiming["acceptanceEligible"] = True
        _expect_error(self, "layout-apply-evidence-invalid", claiming, _preflight())

        bad_evidence = _plan()
        bad_evidence["requiredAcceptanceEvidence"] = "same-process"
        _expect_error(self, "layout-apply-evidence-invalid", bad_evidence, _preflight())


if __name__ == "__main__":
    unittest.main()

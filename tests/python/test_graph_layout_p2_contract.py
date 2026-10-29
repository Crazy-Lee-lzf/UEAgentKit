from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from ue_agent_kit.graph_contracts import (
    GraphLayoutApplyError,
    build_graph_layout_apply_request,
    preview_graph_layout,
)
from ue_agent_kit.graph_contracts.layout import recompute_layout_hash
from ue_agent_kit.graph_contracts.layout_apply import PREFLIGHT_SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "graph_contracts"


def _graph() -> dict:
    return json.loads(
        (FIXTURES / "tg_comment_reroute.json").read_text(encoding="utf-8")
    )


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
        "packageRevision": graph["asset"]["revision"],
        "packageDirty": False,
        "graphFingerprint": "sha1:" + "1" * 40,
        "nodeCount": len(nodes),
        "nodes": nodes,
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
    }


def _plan() -> dict:
    result = preview_graph_layout(_graph(), {"mode": "full"})
    assert result.status == "ok", result.errors
    return copy.deepcopy(result.plan)


def _recompute_moved_and_hash(plan: dict, preflight: dict) -> None:
    current = {node["nodeId"]: (node["x"], node["y"]) for node in preflight["nodes"]}
    plan["movedNodeIds"] = sorted(
        item["nodeId"] for item in plan["positions"] if (item["x"], item["y"]) != current[item["nodeId"]]
    )
    plan["layoutHash"] = recompute_layout_hash(plan)


def _error_code(plan: dict, preflight: dict, **kwargs) -> str:
    with unittest.TestCase().assertRaises(GraphLayoutApplyError) as raised:
        build_graph_layout_apply_request(plan, preflight, **kwargs)
    return raised.exception.code


class GraphLayoutP2ContractTests(unittest.TestCase):
    def test_layout_hash_is_recomputed_and_tampering_is_rejected(self) -> None:
        plan = _plan()
        self.assertEqual(plan["layoutHash"], recompute_layout_hash(plan))

        tampered = copy.deepcopy(plan)
        tampered["positions"][0]["x"] += 1
        tampered["movedNodeIds"] = sorted(
            {
                *tampered["movedNodeIds"],
                tampered["positions"][0]["nodeId"],
            }
        )
        # Deliberately keep the original layoutHash: the adapter must not trust it.
        self.assertEqual(_error_code(tampered, _preflight()), "layout-apply-layout-hash-mismatch")

        bad_base = copy.deepcopy(plan)
        bad_base["baseGraphHash"] = "sha256:not-a-hash"
        self.assertEqual(_error_code(bad_base, _preflight()), "layout-apply-plan-invalid")

    def test_preflight_coordinates_and_counts_must_be_ue_int32(self) -> None:
        huge = _preflight()
        huge["nodes"][0]["x"] = 2_147_483_648
        self.assertEqual(_error_code(_plan(), huge), "layout-apply-preflight-invalid")

        negative_count = _preflight()
        negative_count["nodes"][0]["inputLinkCount"] = -1
        self.assertEqual(_error_code(_plan(), negative_count), "layout-apply-preflight-invalid")

        bool_count = _preflight()
        bool_count["nodeCount"] = True
        self.assertEqual(_error_code(_plan(), bool_count), "layout-apply-preflight-invalid")

    def test_empty_expected_revision_does_not_silently_fallback(self) -> None:
        self.assertEqual(
            _error_code(_plan(), _preflight(), expected_package_revision=""),
            "layout-apply-revision-mismatch",
        )

    def test_plan_id_size_source_and_hash_shape_are_required(self) -> None:
        no_plan_id = _plan()
        no_plan_id["planId"] = ""
        self.assertEqual(_error_code(no_plan_id, _preflight()), "layout-apply-plan-invalid")

        no_source = _plan()
        no_source["positions"][0]["size"].pop("source")
        _recompute_moved_and_hash(no_source, _preflight())
        self.assertEqual(_error_code(no_source, _preflight()), "layout-apply-plan-invalid")

        bad_hash = _plan()
        bad_hash["layoutHash"] = "sha256:" + "0" * 64
        self.assertEqual(_error_code(bad_hash, _preflight()), "layout-apply-layout-hash-mismatch")


if __name__ == "__main__":
    unittest.main()

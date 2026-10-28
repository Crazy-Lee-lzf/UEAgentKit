from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.graph_contracts import blueprint_context  # noqa: E402

FIXTURE = (
    ROOT
    / "tests/fixtures/graph_contracts/bpctx_export_variable_accessor.json"
)


class VariableAccessorReadContextTests(unittest.TestCase):
    def _document(self) -> dict:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_existing_get_and_set_nodes_import_as_typed_graph(self) -> None:
        graph = blueprint_context.typed_graph_from_blueprint_context(
            self._document(),
            source_metadata={"kind": "test-fixture"},
        )
        self.assertEqual(graph["graph"]["kind"], "ubergraph")
        self.assertEqual(len(graph["nodes"]), 2)
        self.assertEqual(len(graph["edges"]), 1)
        self.assertEqual(graph["edges"][0]["kind"], "data")
        self.assertEqual(graph["asset"]["dirty"], False)

        by_kind = {node["kind"]: node for node in graph["nodes"]}
        self.assertEqual(set(by_kind), {"variable-get", "variable-set"})
        self.assertEqual(by_kind["variable-get"]["classPath"], "/Script/BlueprintGraph.K2Node_VariableGet")
        self.assertEqual(by_kind["variable-set"]["classPath"], "/Script/BlueprintGraph.K2Node_VariableSet")

        pins = {pin["pinId"]: pin for pin in graph["pins"]}
        output_pin = next(
            pin
            for pin in pins.values()
            if pin["nodeId"] == by_kind["variable-get"]["nodeId"]
        )
        self.assertEqual(output_pin["name"], "AgentCount")
        self.assertEqual(output_pin["type"]["category"], "int")

        setter_pins = {
            pin["name"]
            for pin in pins.values()
            if pin["nodeId"] == by_kind["variable-set"]["nodeId"]
        }
        # The read context sees the same four-pin inventory the writer declares.
        self.assertEqual(setter_pins, {"execute", "AgentCount", "then", "Output_Get"})

    def test_reference_chain_and_variable_identity_are_visible_to_agents(self) -> None:
        document = self._document()
        references = document["references"]
        self.assertEqual([reference["kind"] for reference in references], ["reads", "writes"])
        self.assertEqual(
            {reference["targetName"] for reference in references},
            {"AgentCount"},
        )
        variable_guids = {variable["guid"] for variable in document["variables"]}
        self.assertEqual(
            variable_guids,
            {"11111111-1111-4111-8111-111111111111"},
        )
        for node in document["graphs"][0]["nodes"]:
            properties = node["properties"]
            self.assertIn('MemberName="AgentCount"', properties["VariableReference"])
            self.assertIn(
                "11111111-1111-4111-8111-111111111111",
                properties["VariableReference"],
            )


if __name__ == "__main__":
    unittest.main()

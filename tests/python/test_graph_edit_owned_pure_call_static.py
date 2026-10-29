from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.experimental_graph_edit import (  # noqa: E402
    GraphEditError,
    owned_pure_call_signature_digest,
    validate_preview_parameters,
)

ASSET = "/Game/Test/BP_OwnedPure.BP_OwnedPure"
GRAPH = "11111111-2222-4333-8444-555555555555"
FUNCTION_GRAPH = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
FUNCTION_NAME = "ComputeFlag"
PINS = [
    {"name": "A", "direction": "input", "type": "int"},
    {"name": "B", "direction": "input", "type": "int"},
    {"name": "ReturnValue", "direction": "output", "type": "bool"},
]
DIGEST = owned_pure_call_signature_digest(ASSET, FUNCTION_GRAPH, FUNCTION_NAME, PINS)


def _owned(
    *,
    owner: str = ASSET,
    graph: str = FUNCTION_GRAPH,
    name: str = FUNCTION_NAME,
    pins=None,
    digest: str = DIGEST,
    temp_id: str = "tmp:owned",
    literals=None,
) -> dict:
    node = {
        "tempId": temp_id,
        "nodeType": "call_owned_pure_function",
        "x": 0,
        "y": 0,
        "ownedCall": {
            "owningBlueprintPath": owner,
            "functionGraphGuid": graph,
            "functionName": name,
            "signatureDigest": digest,
            "pins": [dict(pin) for pin in (pins if pins is not None else PINS)],
        },
    }
    if literals is not None:
        node["inputLiterals"] = literals
    return node


class OwnedPureCallStaticContractTests(unittest.TestCase):
    def test_exact_owned_call_normalizes(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_owned()],
            connections=[],
        )
        node = request["nodes"][0]
        self.assertEqual(node["nodeType"], "call_owned_pure_function")
        self.assertEqual(
            node["ownedCall"]["signatureDigest"],
            DIGEST,
        )
        self.assertEqual(node["ownedCall"]["functionName"], FUNCTION_NAME)

    def test_owned_output_connects_to_existing_condition(self) -> None:
        existing = {
            "nodeGuid": "12345678-1234-4234-8234-123456789abc",
            "pinGuid": "87654321-4321-4321-8321-cba987654321",
            "pinName": "Condition",
            "direction": "input",
            "category": "bool",
            "subCategory": "",
        }
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_owned()],
            connections=[
                {
                    "from": "tmp:owned",
                    "fromPin": "ReturnValue",
                    "toExisting": existing,
                }
            ],
        )
        self.assertEqual(request["connections"][0]["fromPin"], "ReturnValue")

    def test_owner_must_equal_focused_asset(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(owner="/Game/Test/BP_Other.BP_Other")],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-call-owner-mismatch")

    def test_wrong_digest_is_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(digest="sha256:" + "0" * 64)],
                connections=[],
            )
        self.assertEqual(
            raised.exception.code,
            "graph-edit-owned-call-signature-mismatch",
        )

    def test_external_or_object_pins_are_rejected(self) -> None:
        for bad_type in ("exec", "object", "struct", "delegate", "wildcard"):
            pins = [dict(pin) for pin in PINS]
            pins[0]["type"] = bad_type
            with self.assertRaises(GraphEditError) as raised:
                validate_preview_parameters(
                    asset_path=ASSET,
                    graph_guid=GRAPH,
                    nodes=[_owned(pins=pins)],
                    connections=[],
                )
            self.assertEqual(
                raised.exception.code,
                "graph-edit-owned-call-pin-mismatch",
            )

    def test_owned_call_requires_an_output(self) -> None:
        pins = [{"name": "A", "direction": "input", "type": "int"}]
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(pins=pins, digest="sha256:" + "0" * 64)],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-call-pin-mismatch")

    def test_only_one_owned_call_per_plan(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(), _owned(temp_id="tmp:owned2")],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-call-limit")

    def test_owned_call_cannot_mix_with_other_new_nodes(self) -> None:
        branch = {"tempId": "tmp:branch", "nodeType": "branch", "x": 0, "y": 0}
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(), branch],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-call-plan")

    def test_owned_call_rejects_call_function_and_variable_fields(self) -> None:
        bad = _owned()
        bad["callFunction"] = {"ownerClassPath": "/Script/Engine.KismetMathLibrary"}
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[bad],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-call-function-field-not-allowed")

        bad = _owned()
        bad["variableName"] = "Nope"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[bad],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-variable-field-not-allowed")

    def test_call_function_rejects_owned_call_field(self) -> None:
        node = {
            "tempId": "tmp:fixed",
            "nodeType": "call_function",
            "x": 0,
            "y": 0,
            "callFunction": {
                "ownerClassPath": "/Script/Engine.KismetMathLibrary",
                "functionName": "Abs_Int",
                "signatureDigest": "sha256:ea421dabd229f8c16d774b90a49746d56eb3358b4172e4c3796ef7f5de8a9fa7",
                "pins": [
                    {"name": "A", "direction": "input", "type": "int"},
                    {"name": "ReturnValue", "direction": "output", "type": "int"},
                ],
            },
            "ownedCall": _owned()["ownedCall"],
        }
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[node],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-call-field-not-allowed")

    def test_bad_graph_guid_or_name_is_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(graph="not-a-guid")],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-call-invalid")

        with self.assertRaises(GraphEditError):
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(name="bad name")],
                connections=[],
            )


    def test_typed_input_literals_normalize(self) -> None:
        pins = [
            {"name": "A", "direction": "input", "type": "int"},
            {"name": "B", "direction": "input", "type": "double"},
            {"name": "C", "direction": "input", "type": "string"},
            {"name": "D", "direction": "input", "type": "bool"},
            {"name": "ReturnValue", "direction": "output", "type": "bool"},
        ]
        digest = owned_pure_call_signature_digest(
            ASSET,
            FUNCTION_GRAPH,
            FUNCTION_NAME,
            pins,
        )
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[
                _owned(
                    pins=pins,
                    digest=digest,
                    literals=[
                        {"pin": "A", "type": "int", "value": "-7"},
                        {"pin": "B", "type": "double", "value": "1.5"},
                        {"pin": "C", "type": "string", "value": "agent"},
                        {"pin": "D", "type": "bool", "value": "true"},
                    ],
                )
            ],
            connections=[],
        )
        literals = request["nodes"][0]["inputLiterals"]
        self.assertEqual([literal["pin"] for literal in literals], ["A", "B", "C", "D"])

    def test_invalid_literal_values_are_rejected(self) -> None:
        for value in ("-0", "01", "1e3", "2147483648", "1.2.3"):
            with self.assertRaises(GraphEditError) as raised:
                validate_preview_parameters(
                    asset_path=ASSET,
                    graph_guid=GRAPH,
                    nodes=[
                        _owned(literals=[{"pin": "A", "type": "int", "value": value}])
                    ],
                    connections=[],
                )
            self.assertIn(
                raised.exception.code,
                (
                    "graph-edit-owned-literal-value-invalid",
                    "graph-edit-owned-literal-pin-mismatch",
                ),
            )

    def test_bool_double_and_string_literal_bounds(self) -> None:
        for literal in (
            {"pin": "A", "type": "bool", "value": "True"},
            {"pin": "A", "type": "double", "value": "1."},
            {"pin": "A", "type": "double", "value": "-0.0"},
            {"pin": "A", "type": "string", "value": "x" * 257},
            {"pin": "A", "type": "string", "value": "a\nb"},
        ):
            with self.assertRaises(GraphEditError):
                validate_preview_parameters(
                    asset_path=ASSET,
                    graph_guid=GRAPH,
                    nodes=[_owned(literals=[literal])],
                    connections=[],
                )

    def test_literal_must_target_declared_input_not_output_or_hidden_self(self) -> None:
        for pin in ("ReturnValue", "self", "Unknown"):
            with self.assertRaises(GraphEditError) as raised:
                validate_preview_parameters(
                    asset_path=ASSET,
                    graph_guid=GRAPH,
                    nodes=[_owned(literals=[{"pin": pin, "type": "bool", "value": "true"}])],
                    connections=[],
                )
            self.assertEqual(raised.exception.code, "graph-edit-owned-literal-pin-mismatch")

    def test_literal_type_must_match_declared_pin(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[
                    _owned(literals=[{"pin": "A", "type": "double", "value": "1.5"}])
                ],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-literal-pin-mismatch")

    def test_literal_duplicates_and_limit_are_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[
                    _owned(
                        literals=[
                            {"pin": "A", "type": "int", "value": "1"},
                            {"pin": "A", "type": "int", "value": "2"},
                        ]
                    )
                ],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-literal-pin-mismatch")

        pins = [
            {"name": f"P{i}", "direction": "input", "type": "int"}
            for i in range(7)
        ] + [{"name": "ReturnValue", "direction": "output", "type": "bool"}]
        digest = owned_pure_call_signature_digest(
            ASSET,
            FUNCTION_GRAPH,
            FUNCTION_NAME,
            pins,
        )
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[
                    _owned(
                        pins=pins,
                        digest=digest,
                        literals=[
                            {"pin": f"P{i % 7}", "type": "int", "value": "1"}
                            for i in range(9)
                        ],
                    )
                ],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-literal-limit")

    def test_connected_literal_pin_is_rejected(self) -> None:
        source = {
            "nodeGuid": "12345678-1234-4234-8234-123456789abc",
            "pinGuid": "87654321-4321-4321-8321-cba987654321",
            "pinName": "Result",
            "direction": "output",
            "category": "int",
            "subCategory": "",
        }
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_owned(literals=[{"pin": "A", "type": "int", "value": "1"}])],
                connections=[
                    {
                        "fromExisting": source,
                        "to": "tmp:owned",
                        "toPin": "A",
                    }
                ],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-literal-connected")

    def test_literals_are_only_allowed_on_owned_call(self) -> None:
        branch = {
            "tempId": "tmp:branch",
            "nodeType": "branch",
            "x": 0,
            "y": 0,
            "inputLiterals": [{"pin": "Condition", "type": "bool", "value": "true"}],
        }
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[branch],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-owned-literal-field-not-allowed")


if __name__ == "__main__":
    unittest.main()

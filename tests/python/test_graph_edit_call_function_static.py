from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.experimental_graph_edit import (  # noqa: E402
    CALL_FUNCTION_ALLOWLIST,
    GraphEditError,
    call_function_signature_digest,
    validate_preview_parameters,
)

ASSET = "/Game/Test/BP_CallFunction.BP_CallFunction"
GRAPH = "11111111-2222-4333-8444-555555555555"


def _cf(name: str, *, owner: str | None = None, pins=None, digest: str | None = None) -> dict:
    entry = CALL_FUNCTION_ALLOWLIST[name]
    return {
        "tempId": f"tmp:{name.lower()}",
        "nodeType": "call_function",
        "x": 0,
        "y": 0,
        "callFunction": {
            "ownerClassPath": owner or entry["ownerClassPath"],
            "functionName": name,
            "signatureDigest": digest or entry["signatureDigest"],
            "pins": [dict(pin) for pin in (pins if pins is not None else entry["pins"])],
        },
    }


def _branch() -> dict:
    return {"tempId": "tmp:branch", "nodeType": "branch", "x": 300, "y": 0}


class CallFunctionStaticContractTests(unittest.TestCase):
    def test_exact_allowlist_digests(self) -> None:
        expected = {
            "Greater_IntInt": "sha256:0f41d587fab8cfbbc431b8dcf4b6d150b0f3dff6438ae3e5e731a932ad26ea09",
            "Less_IntInt": "sha256:2537b266443bce7147ae6d77e0704479c65c69e4672034fea911909be2aab8ae",
            "Clamp": "sha256:ec7c282e64b63ce136c49c3e364661da32930b121b1b6a08e74df4fdeebcfb9d",
            "Abs_Int": "sha256:ea421dabd229f8c16d774b90a49746d56eb3358b4172e4c3796ef7f5de8a9fa7",
        }
        for name, digest in expected.items():
            entry = CALL_FUNCTION_ALLOWLIST[name]
            self.assertEqual(entry["signatureDigest"], digest)
            self.assertEqual(
                call_function_signature_digest(
                    entry["ownerClassPath"],
                    name,
                    entry["pins"],
                ),
                digest,
            )

    def test_four_allowlisted_nodes_normalize(self) -> None:
        for name in ("Greater_IntInt", "Less_IntInt", "Clamp", "Abs_Int"):
            request = validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_cf(name)],
                connections=[],
            )
            normalized = request["nodes"][0]
            self.assertEqual(normalized["nodeType"], "call_function")
            self.assertEqual(
                normalized["callFunction"]["signatureDigest"],
                CALL_FUNCTION_ALLOWLIST[name]["signatureDigest"],
            )

    def test_call_function_output_connects_to_branch(self) -> None:
        request = validate_preview_parameters(
            asset_path=ASSET,
            graph_guid=GRAPH,
            nodes=[_cf("Greater_IntInt"), _branch()],
            connections=[
                {
                    "from": "tmp:greater_intint",
                    "fromPin": "ReturnValue",
                    "to": "tmp:branch",
                    "toPin": "Condition",
                }
            ],
        )
        self.assertEqual(request["connections"][0]["fromPin"], "ReturnValue")

    def test_arbitrary_owner_or_function_is_rejected(self) -> None:
        for node in (
            _cf("Greater_IntInt", owner="/Script/Engine.KismetStringLibrary"),
            {
                "tempId": "tmp:evil",
                "nodeType": "call_function",
                "x": 0,
                "y": 0,
                "callFunction": {
                    "ownerClassPath": "/Script/Engine.KismetMathLibrary",
                    "functionName": "RandomInteger",
                    "signatureDigest": CALL_FUNCTION_ALLOWLIST["Greater_IntInt"]["signatureDigest"],
                    "pins": CALL_FUNCTION_ALLOWLIST["Greater_IntInt"]["pins"],
                },
            },
        ):
            with self.assertRaises(GraphEditError) as raised:
                validate_preview_parameters(
                    asset_path=ASSET,
                    graph_guid=GRAPH,
                    nodes=[node],
                    connections=[],
                )
            self.assertEqual(
                raised.exception.code,
                "graph-edit-call-function-not-allowlisted",
            )

    def test_wrong_digest_is_rejected(self) -> None:
        bad = "sha256:" + "0" * 64
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_cf("Abs_Int", digest=bad)],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-call-function-signature-mismatch")

    def test_wrong_pin_schema_is_rejected(self) -> None:
        pins = [dict(pin) for pin in CALL_FUNCTION_ALLOWLIST["Abs_Int"]["pins"]]
        pins[0]["type"] = "double"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_cf("Abs_Int", pins=pins)],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-call-function-pin-mismatch")

    def test_missing_call_function_object_is_rejected(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[
                    {
                        "tempId": "tmp:missing",
                        "nodeType": "call_function",
                        "x": 0,
                        "y": 0,
                    }
                ],
                connections=[],
            )
        self.assertIn(
            raised.exception.code,
            ("graph-edit-call-function-invalid", "graph-edit-invalid-parameters"),
        )

    def test_call_function_object_on_other_node_is_rejected(self) -> None:
        node = _branch()
        node["callFunction"] = _cf("Abs_Int")["callFunction"]
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[node],
                connections=[],
            )
        self.assertEqual(
            raised.exception.code,
            "graph-edit-call-function-field-not-allowed",
        )

    def test_variable_fields_and_object_pins_are_rejected(self) -> None:
        node = _cf("Abs_Int")
        node["variableName"] = "Nope"
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[node],
                connections=[],
            )
        self.assertEqual(raised.exception.code, "graph-edit-variable-field-not-allowed")

        pins = [dict(pin) for pin in CALL_FUNCTION_ALLOWLIST["Abs_Int"]["pins"]]
        pins[0].update({"type": "object", "subCategory": "Actor"})
        with self.assertRaises(GraphEditError):
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_cf("Abs_Int", pins=pins)],
                connections=[],
            )

    def test_clamp_int_output_does_not_connect_to_bool_input(self) -> None:
        with self.assertRaises(GraphEditError) as raised:
            validate_preview_parameters(
                asset_path=ASSET,
                graph_guid=GRAPH,
                nodes=[_cf("Clamp"), _branch()],
                connections=[
                    {
                        "from": "tmp:clamp",
                        "fromPin": "ReturnValue",
                        "to": "tmp:branch",
                        "toPin": "Condition",
                    }
                ],
            )
        self.assertIn(
            raised.exception.code,
            ("graph-edit-pin-type-mismatch", "graph-edit-connection-type-mismatch"),
        )


if __name__ == "__main__":
    unittest.main()

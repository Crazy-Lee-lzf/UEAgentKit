"""Gate: every ``ue_preview_graph_patch`` refusal must carry a registered code.

The GraphPatch preview tool lives in the MCP adapter layer, outside the
``prototype/graph_contracts`` tree that the prototype ``check_codes.py`` gate
scans, so its error vocabulary drifted into 30 unregistered strings
(``graphpatch-patch-invalid``, ``graphpatch-*-too-large``,
``graphpatch-schema-invalid`` ...).

The adapter now reuses the registered contract codes for every condition that
already has one (``contract-schema-invalid`` for shape/schema problems,
``graphpatch-limit-exceeded`` for byte/depth/count limits,
``graphpatch-operation-unknown`` for ``deleteNode``) and puts the precise cause
in ``details``.  Only the two preview-only failures are registered in the
official table.  These tests keep that true:

* every rejection path returns an ``errorCode`` the registry resolves;
* the adapter source contains no unregistered code literal and no dynamic code
  construction that a static gate could miss;
* the shipped registry copy and the prototype source of truth stay identical.
"""

from __future__ import annotations

import copy
import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TEST_ROOT = TOOL_ROOT / "tests" / "python"
PROTOTYPE_ROOT = TOOL_ROOT / "prototype" / "graph_contracts"
for entry in (str(SRC_ROOT), str(TEST_ROOT), str(PROTOTYPE_ROOT)):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from ue_agent_kit import mcp_graph_patch_tools as adapter  # noqa: E402
from ue_agent_kit.mcp_graph_patch_tools import preview_graph_patch_document  # noqa: E402
from ueak_contract import fixtures  # noqa: E402
from ueak_contract.errors import load_registry  # noqa: E402

ADAPTER_SOURCE = Path(adapter.__file__).read_text(encoding="utf-8")
REGISTRY = load_registry()

CODE_PATTERN = re.compile(r'"((?:graph|graphpatch|typedgraph|layout|contract|recovery|verify)-[a-z0-9-]+)"')
DYNAMIC_CODE_PATTERN = re.compile(r'f"(?:\{[a-z_]+\}|graphpatch|typedgraph)-(?:[a-z0-9]|-)')
REGISTERED_CODES = REGISTRY.codes()


def _fixture(fixture_id: str = "gp-create-branch"):
    manifest = {item["fixtureId"]: item for item in fixtures.load_manifest()["fixtures"]}
    item = manifest[fixture_id]
    return (
        fixtures.load_fixture(item),
        fixtures.load_graph_for(item),
        item.get("environment"),
        fixtures.load_descriptors_for(item),
    )


def _slice_fixture(name: str = "gp-create-branch"):
    patch, graph, environment, descriptors = _fixture(name)
    return copy.deepcopy(patch), copy.deepcopy(graph), copy.deepcopy(environment), copy.deepcopy(descriptors)


class PreviewErrorCodeRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patch, self.graph, self.environment, self.descriptors = _slice_fixture()

    def _assert_registered_refusal(self, payload: dict) -> None:
        self.assertFalse(payload["ok"], payload)
        self.assertEqual(payload["previewStatus"], "rejected")
        code = payload["errorCode"]
        self.assertTrue(code, payload)
        self.assertIn(code, REGISTERED_CODES, f"{code} is not in graph_contracts/schemas/error-codes.json")
        self.assertTrue(REGISTRY.is_known(code))
        self.assertIsInstance(payload.get("details"), dict)

    def test_shape_refusals_use_the_registered_schema_code(self) -> None:
        cases = {
            "patch": (["not", "an", "object"], self.graph, self.environment, self.descriptors),
            "graph": (self.patch, "not-an-object", self.environment, self.descriptors),
            "environment": (self.patch, self.graph, ["not", "an", "object"], self.descriptors),
            "descriptors": (self.patch, self.graph, self.environment, ["not-an-object"]),
        }
        for argument, args in cases.items():
            with self.subTest(argument=argument):
                payload = preview_graph_patch_document(*args)
                self._assert_registered_refusal(payload)
                self.assertEqual(payload["errorCode"], "contract-schema-invalid")
                self.assertEqual(payload["details"]["argument"], argument)

    def test_non_json_input_uses_the_registered_schema_code(self) -> None:
        bad_patch = {"operations": [], "unsupported": float("nan")}
        payload = preview_graph_patch_document(bad_patch, self.graph, self.environment, self.descriptors)
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "contract-schema-invalid")
        self.assertEqual(payload["details"], {"argument": "patch", "reason": "not-json"})

    def test_byte_limits_use_the_registered_limit_code(self) -> None:
        cases = {
            "patch": (
                {"operations": [], "padding": "x" * (adapter.MAX_PATCH_BYTES + 1)},
                self.graph,
                self.environment,
                self.descriptors,
                adapter.MAX_PATCH_BYTES,
            ),
            "graph": (
                self.patch,
                {"padding": "x" * (adapter.MAX_GRAPH_BYTES + 1)},
                self.environment,
                self.descriptors,
                adapter.MAX_GRAPH_BYTES,
            ),
            "environment": (
                self.patch,
                self.graph,
                {"padding": "x" * (adapter.MAX_ENVIRONMENT_BYTES + 1)},
                self.descriptors,
                adapter.MAX_ENVIRONMENT_BYTES,
            ),
        }
        for argument, (patch, graph, environment, descriptors, limit) in cases.items():
            with self.subTest(argument=argument):
                payload = preview_graph_patch_document(patch, graph, environment, descriptors)
                self._assert_registered_refusal(payload)
                self.assertEqual(payload["errorCode"], "graphpatch-limit-exceeded")
                self.assertEqual(payload["details"]["argument"], argument)
                self.assertEqual(payload["details"]["limit"], limit)
                self.assertGreater(payload["details"]["actualBytes"], limit)

    def test_combined_payload_limit_uses_the_registered_limit_code(self) -> None:
        with mock.patch.object(adapter, "MAX_TOTAL_BYTES", 8):
            payload = preview_graph_patch_document(
                self.patch, self.graph, self.environment, self.descriptors
            )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "graphpatch-limit-exceeded")
        self.assertEqual(payload["details"]["argument"], "combined")

    def test_depth_and_count_limits_use_the_registered_limit_code(self) -> None:
        deep = {"operations": []}
        cursor = deep
        for _ in range(adapter.MAX_DEPTH + 2):
            cursor["nested"] = {}
            cursor = cursor["nested"]
        payload = preview_graph_patch_document(deep, self.graph, self.environment, self.descriptors)
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["details"]["limitName"], "maxDepth")

        too_many_operations = copy.deepcopy(self.patch)
        too_many_operations["operations"] = [
            {"operationId": str(index)} for index in range(adapter.MAX_OPERATIONS + 1)
        ]
        payload = preview_graph_patch_document(
            too_many_operations, self.graph, self.environment, self.descriptors
        )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["details"]["limitName"], "maxOperations")

        not_an_array = copy.deepcopy(self.patch)
        not_an_array["operations"] = {}
        payload = preview_graph_patch_document(not_an_array, self.graph, self.environment, self.descriptors)
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "contract-schema-invalid")
        self.assertEqual(payload["details"]["argument"], "patch.operations")

        too_many_nodes = copy.deepcopy(self.graph)
        too_many_nodes["nodes"] = [{"nodeId": str(index)} for index in range(adapter.MAX_GRAPH_NODES + 1)]
        payload = preview_graph_patch_document(self.patch, too_many_nodes, self.environment, self.descriptors)
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["details"]["limitName"], "maxGraphNodes")

        too_many_descriptors = [{"index": index} for index in range(adapter.MAX_DESCRIPTORS + 1)]
        payload = preview_graph_patch_document(
            self.patch, self.graph, self.environment, too_many_descriptors
        )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["details"]["limitName"], "maxDescriptors")

    def test_delete_node_uses_the_registered_operation_code(self) -> None:
        patch = copy.deepcopy(self.patch)
        patch["operations"] = [{"operationId": "op-delete", "type": "deleteNode", "nodeId": "n:" + "f" * 32}]
        payload = preview_graph_patch_document(patch, self.graph, self.environment, self.descriptors)
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "graphpatch-operation-unknown")
        self.assertEqual(payload["details"]["operation"], "deleteNode")

    def test_schema_invalid_documents_use_the_registered_schema_code(self) -> None:
        patch = copy.deepcopy(self.patch)
        patch["unknownExtension"] = "not allowed"
        payload = preview_graph_patch_document(patch, self.graph, self.environment, self.descriptors)
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "contract-schema-invalid")
        self.assertEqual(payload["details"]["document"], "graphpatch")
        self.assertTrue(payload["diagnostics"])

        graph = copy.deepcopy(self.graph)
        graph["nodes"] = "not-a-node-list"
        payload = preview_graph_patch_document(self.patch, graph, self.environment, self.descriptors)
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "contract-schema-invalid")
        self.assertEqual(payload["details"]["document"], "typedgraph")

    def test_invalid_descriptor_uses_the_registered_schema_code(self) -> None:
        payload = preview_graph_patch_document(
            self.patch, self.graph, self.environment, [{"not": "a descriptor"}]
        )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "contract-schema-invalid")
        self.assertEqual(payload["details"]["argument"], "descriptors[0]")

    def test_validation_unavailable_uses_the_registered_schema_code(self) -> None:
        with mock.patch.object(adapter, "validate_contract_document", side_effect=RuntimeError("boom")):
            payload = preview_graph_patch_document(
                self.patch, self.graph, self.environment, self.descriptors
            )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "contract-schema-invalid")
        self.assertEqual(payload["details"], {"document": "graphpatch", "reason": "validation-unavailable"})

    def test_preview_only_failures_use_registered_codes(self) -> None:
        with mock.patch.object(adapter, "preview_graph_patch", return_value={"status": "previewed", "bad": {1, 2}}):
            payload = preview_graph_patch_document(
                self.patch, self.graph, self.environment, self.descriptors
            )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "graphpatch-preview-not-json")

        with mock.patch.object(adapter, "preview_graph_patch", side_effect=RuntimeError("boom")):
            payload = preview_graph_patch_document(
                self.patch, self.graph, self.environment, self.descriptors
            )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "graphpatch-preview-failed")

        oversized = {"status": "previewed", "padding": "x" * 64}
        with mock.patch.object(adapter, "preview_graph_patch", return_value=oversized), mock.patch.object(
            adapter, "MAX_OUTPUT_BYTES", 16
        ):
            payload = preview_graph_patch_document(
                self.patch, self.graph, self.environment, self.descriptors
            )
        self._assert_registered_refusal(payload)
        self.assertEqual(payload["errorCode"], "graphpatch-limit-exceeded")
        self.assertEqual(payload["details"]["argument"], "previewOutput")

    def test_adapter_source_only_uses_registered_codes(self) -> None:
        declared = sorted({match for match in CODE_PATTERN.findall(ADAPTER_SOURCE)})
        self.assertLessEqual(len(declared), 8, declared)
        for code in declared:
            self.assertIn(code, REGISTERED_CODES, f"{code} is emitted but not registered")
        dynamic = DYNAMIC_CODE_PATTERN.findall(ADAPTER_SOURCE)
        self.assertEqual(dynamic, [], f"error codes must stay static literal strings, found {dynamic}")

    def test_shipped_registry_matches_the_prototype_source_of_truth(self) -> None:
        shipped = json.loads(
            (SRC_ROOT / "ue_agent_kit" / "graph_contracts" / "schemas" / "error-codes.json").read_text(
                encoding="utf-8"
            )
        )
        prototype = json.loads(
            (PROTOTYPE_ROOT / "schemas" / "error-codes.json").read_text(encoding="utf-8")
        )
        self.assertEqual(shipped, prototype)
        self.assertGreaterEqual(len(shipped["codes"]), 130)


if __name__ == "__main__":
    unittest.main()

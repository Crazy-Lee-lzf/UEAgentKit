from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import ids  # noqa: E402

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"
N = "33333333-3333-4333-8333-333333333333"


class IdentityTests(unittest.TestCase):
    def test_edge_id_is_pure_function(self) -> None:
        source = ids.pin_id(A)
        target = ids.pin_id(B)
        self.assertEqual(ids.edge_id(source, target), f"e:{source}--{target}")
        self.assertEqual(ids.edge_id(source, target), f"e:p:{A}--p:{B}")
        self.assertEqual(ids.split_edge_id(f"e:p:{A}--p:{B}"), (source, target))

    def test_edge_id_rejects_bad_input(self) -> None:
        self.assertFalse(ids.is_edge_id("e:p:not-a-guid--p:" + B))
        self.assertFalse(ids.is_edge_id("e:p:" + A))
        self.assertFalse(ids.is_edge_id("e:p:" + A + "--p:" + A + "--p:" + B))
        with self.assertRaises(ValueError):
            ids.edge_id("p:" + A, "n:" + B)

    def test_identity_kinds(self) -> None:
        self.assertEqual(ids.identity_kind(ids.graph_id(A)), "graph-native")
        self.assertEqual(ids.identity_kind(ids.graph_fallback_id("EventGraph")), "graph-fallback")
        self.assertEqual(ids.identity_kind(ids.node_id(N)), "node-native")
        self.assertEqual(ids.identity_kind(ids.node_fallback_id("K2Node_Event_0")), "node-fallback")
        self.assertEqual(ids.identity_kind(ids.pin_id(A)), "pin-native")
        self.assertEqual(ids.identity_kind(ids.duplicate_pin_id(N, A)), "pin-duplicate")
        self.assertEqual(ids.identity_kind("tmp:print"), "temp")

    def test_duplicate_pin_fallback_shape(self) -> None:
        value = ids.duplicate_pin_id(N, A)
        self.assertEqual(value, f"p:dup:{N}:{A}")
        self.assertTrue(ids.is_pin_id(value))
        self.assertEqual(ids.pin_guid_from_id(value), "")

    def test_guid_normalisation_and_rejection(self) -> None:
        self.assertEqual(ids.normalize_guid(A.upper()), A)
        self.assertEqual(ids.normalize_guid("{" + A + "}"), A)
        for bad in ("", "not-a-guid", A.replace("-", ""), "{" + A):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ids.normalize_guid(bad)

    def test_temp_and_operation_id_grammar(self) -> None:
        self.assertTrue(ids.is_temp_id("tmp:print-1"))
        self.assertFalse(ids.is_temp_id("print"))
        self.assertFalse(ids.is_temp_id("tmp:" + "x" * 33))
        self.assertTrue(ids.is_operation_id("op-1"))
        self.assertFalse(ids.is_operation_id("op 1"))

    def test_asset_path_is_object_path_only(self) -> None:
        self.assertTrue(ids.is_asset_path("/Game/Foo/BP_Bar.BP_Bar"))
        self.assertFalse(ids.is_asset_path("E:/Game/Foo/BP_Bar.uasset"))
        self.assertFalse(ids.is_asset_path("/Script/Engine.Blueprint"))


if __name__ == "__main__":
    unittest.main()

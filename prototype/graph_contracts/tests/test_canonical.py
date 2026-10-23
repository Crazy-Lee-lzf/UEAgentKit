from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import canonical  # noqa: E402
from ueak_contract import fixtures  # noqa: E402


class CanonicalTests(unittest.TestCase):
    def test_frozen_canonical_vectors(self) -> None:
        vectors = fixtures.load_json(fixtures.VECTORS_PATH)["canonicalization"]
        for vector in vectors:
            with self.subTest(vector=vector["id"]):
                self.assertEqual(canonical.canonical_json(vector["input"]), vector["canonical"])

    def test_canonical_json_exact_text(self) -> None:
        value = {"z": 1, "a": "中文", "nested": {"b": True, "a": None}}
        self.assertEqual(canonical.canonical_json(value), '{"a":"中文","nested":{"a":null,"b":true},"z":1}')
        self.assertEqual(canonical.canonical_json(-0.0), "0")
        self.assertEqual(canonical.canonical_json(1.5), "1.5")

    def test_parity_with_existing_patch_canonicalization(self) -> None:
        source = ROOT.parents[1] / "UEAgentKit" / "src"
        if not source.exists():
            self.skipTest("UEAgentKit source checkout is not adjacent")
        sys.path.insert(0, str(source))
        try:
            from ue_agent_kit.patches import _canonical_json  # type: ignore
        finally:
            sys.path.pop(0)
        values = [
            {"z": 1, "a": "中文", "nested": {"b": True, "a": None}},
            {"float": 1.25, "negativeZero": -0.0, "list": [1, "x", False]},
            {"": "", "unicode": "执行流孤岛", "emoji": "🧩"},
        ]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(canonical.canonical_json(value), _canonical_json(value))

    def test_hash_domains_are_separated(self) -> None:
        value = {"a": 1}
        semantic = canonical.domain_hash(value, canonical.DOMAIN_SEMANTIC)
        layout = canonical.domain_hash(value, canonical.DOMAIN_LAYOUT)
        self.assertNotEqual(semantic, layout)
        self.assertTrue(canonical.is_sha256(semantic))
        self.assertTrue(canonical.is_fingerprint(canonical.fingerprint(value, canonical.DOMAIN_NODE)))

    def test_unsupported_values_rejected(self) -> None:
        with self.assertRaises(canonical.CanonicalizationError):
            canonical.canonical_json({1: "non-string key"})
        with self.assertRaises(canonical.CanonicalizationError):
            canonical.canonical_json(float("nan"))
        with self.assertRaises(canonical.CanonicalizationError):
            canonical.canonical_json(object())


if __name__ == "__main__":
    unittest.main()

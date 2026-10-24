"""R1.3/T4 two-process parity check.

Compares two independent ``UnrealEditor-Cmd`` catalog exports produced by
``scripts/RunR1TwoProcessParity.ps1`` (Evidence/t4/run-a and run-b):

* exact actionId set equality (order-insensitive, then sorted equality);
* actionDatabaseFingerprint / pluginSetHash / engine+plugin identity equality;
* per-action descriptorDigest equality;
* context export identity equality (when both runs produced one).
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
T4 = ROOT / "Build" / "r1-native" / "Evidence" / "t4"


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


class TwoProcessParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.a_path = T4 / "run-a" / "catalog-export.json"
        cls.b_path = T4 / "run-b" / "catalog-export.json"
        if not cls.a_path.exists() or not cls.b_path.exists():
            raise unittest.SkipTest("two-process exports are missing (run scripts/RunR1TwoProcessParity.ps1)")
        cls.a = _load(cls.a_path)
        cls.b = _load(cls.b_path)

    def test_action_id_sets_are_exact(self) -> None:
        ids_a = sorted(descriptor["actionId"] for descriptor in self.a["descriptors"])
        ids_b = sorted(descriptor["actionId"] for descriptor in self.b["descriptors"])
        self.assertEqual(ids_a, ids_b)
        self.assertGreaterEqual(len(ids_a), 200)

    def test_database_and_plugin_identity_are_identical(self) -> None:
        summary_a = self.a["summary"]
        summary_b = self.b["summary"]
        self.assertEqual(summary_a["actionDatabaseFingerprint"], summary_b["actionDatabaseFingerprint"])
        self.assertEqual(summary_a["pluginSetHash"], summary_b["pluginSetHash"])
        self.assertEqual(summary_a["engineVersion"], summary_b["engineVersion"])
        self.assertEqual(summary_a["engineChangelist"], summary_b["engineChangelist"])
        self.assertEqual(summary_a["pluginBuildHash"], summary_b["pluginBuildHash"])
        self.assertEqual(summary_a["pluginVersion"], summary_b["pluginVersion"])

    def test_descriptor_digests_and_metadata_match(self) -> None:
        digests_a = {d["actionId"]: d["descriptorDigest"] for d in self.a["descriptors"]}
        digests_b = {d["actionId"]: d["descriptorDigest"] for d in self.b["descriptors"]}
        self.assertEqual(digests_a, digests_b)
        for action_id, digest in digests_a.items():
            with self.subTest(actionId=action_id):
                self.assertEqual(digest, digests_b[action_id])

    def test_unsupported_reason_histogram_matches(self) -> None:
        reasons_a = self.a["summary"].get("unsupportedByReason", {})
        reasons_b = self.b["summary"].get("unsupportedByReason", {})
        self.assertEqual(reasons_a, reasons_b)

    def test_context_exports_match_when_present(self) -> None:
        context_a = T4 / "run-a" / "context-export.json"
        context_b = T4 / "run-b" / "context-export.json"
        if not context_a.exists() or not context_b.exists():
            self.skipTest("context exports are missing from one of the runs")
        data_a = _load(context_a)
        data_b = _load(context_b)
        for name in ("palette", "node", "execPin", "dataPin"):
            with self.subTest(context=name):
                self.assertEqual(data_a[name]["actionSetFingerprint"], data_b[name]["actionSetFingerprint"])
                self.assertEqual(
                    [action["actionId"] for action in data_a[name]["actions"]],
                    [action["actionId"] for action in data_b[name]["actions"]],
                )

    def test_no_false_full_coverage(self) -> None:
        summary = self.a["summary"]
        self.assertFalse(summary["complete"])
        self.assertGreater(summary["totalUnsupported"], 0)
        self.assertGreater(summary["descriptorCount"], 0)
        self.assertLessEqual(summary["descriptorCount"], 512)


if __name__ == "__main__":
    unittest.main()

"""R1.3 context-filter export checks (offline).

Validates the ``ueak.native.context-export/1.0`` document produced by
``UEAgentKit.Native.R1ContextFilter``:

* context kinds and K2 event-graph identity;
* deterministic, contract-shaped action ids / digests and sorted order;
* catalog/context identity agreement (same actionId -> same descriptorDigest);
* genuine context sensitivity (palette / node / exec pin / data pin differ);
* the explicit "graphKinds is only a template hint" semantics;
* fail-closed non-K2 evidence.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / "Build" / "r1-native" / "Evidence"
CONTEXT_EXPORT = EVIDENCE / "context-export.json"
CATALOG_EXPORT = EVIDENCE / "catalog-export.json"

ACTION_ID_RE = re.compile(r"^action:[0-9a-f]{32}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{16}$")
FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{16}$")


def _load(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


class ContextExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not CONTEXT_EXPORT.exists():
            raise unittest.SkipTest("no R1.3 context export")
        cls.export = _load(CONTEXT_EXPORT)
        cls.contexts = {name: cls.export[name] for name in ("palette", "node", "execPin", "dataPin")}
        cls.catalog = _load(CATALOG_EXPORT) if CATALOG_EXPORT.exists() else None

    def test_schema_and_context_kinds(self) -> None:
        self.assertEqual(self.export["schemaVersion"], "ueak.native.context-export/1.0")
        self.assertEqual(self.contexts["palette"]["contextKind"], "palette")
        self.assertEqual(self.contexts["node"]["contextKind"], "node")
        self.assertEqual(self.contexts["execPin"]["contextKind"], "pin")
        self.assertEqual(self.contexts["dataPin"]["contextKind"], "pin")
        for name, context in self.contexts.items():
            with self.subTest(context=name):
                self.assertTrue(context["ok"], context.get("errorCode"))
                self.assertTrue(context["readOnly"])
                self.assertFalse(context["saved"])
                self.assertFalse(context["compiled"])
                self.assertEqual(context["graphKind"], "event")
                self.assertEqual(context["graphSchema"], "/Script/BlueprintGraph.EdGraphSchema_K2")
                self.assertEqual(context["completeScope"], "described-actions")
                self.assertRegex(context["actionSetFingerprint"], FINGERPRINT_RE)
                self.assertRegex(context["actionDatabaseFingerprint"], FINGERPRINT_RE)
                self.assertEqual(context["semantics"]["catalogGraphKinds"], "template-context-hint")
                self.assertEqual(context["semantics"]["contextAuthority"], "FBlueprintActionFilter")

    def test_action_ids_are_ordered_and_shaped(self) -> None:
        for name, context in self.contexts.items():
            ids = [action["actionId"] for action in context["actions"]]
            with self.subTest(context=name):
                self.assertEqual(ids, sorted(ids))
                self.assertEqual(len(ids), len(set(ids)))
                for action in context["actions"]:
                    self.assertRegex(action["actionId"], ACTION_ID_RE)
                    self.assertRegex(action["descriptorDigest"], DIGEST_RE)
                    self.assertIn("nodeClass", action["discoveryKey"])
                    self.assertIn("spawnerClass", action["discoveryKey"])

    def test_context_is_sensitive(self) -> None:
        palette = self.contexts["palette"]
        exec_pin = self.contexts["execPin"]
        data_pin = self.contexts["dataPin"]
        self.assertGreater(palette["matchedActions"], data_pin["matchedActions"])
        self.assertNotEqual(exec_pin["actionSetFingerprint"], data_pin["actionSetFingerprint"])
        palette_ids = {action["actionId"] for action in palette["actions"]}
        data_ids = {action["actionId"] for action in data_pin["actions"]}
        # The palette page is capped at 128; only assert the strict-subset relation
        # when the data pin result is fully contained in the palette page.
        if data_ids and data_ids.issubset(palette_ids):
            self.assertLess(len(data_ids), len(palette_ids))
        self.assertGreater(data_pin["rejectedActions"], 0)

    def test_catalog_identity_agrees(self) -> None:
        if self.catalog is None:
            self.skipTest("no catalog export to compare")
        digests = {
            descriptor["actionId"]: descriptor["descriptorDigest"]
            for descriptor in self.catalog["descriptors"]
        }
        for name, context in self.contexts.items():
            for action in context["actions"]:
                with self.subTest(context=name, actionId=action["actionId"]):
                    self.assertIn(action["actionId"], digests)
                    self.assertEqual(action["descriptorDigest"], digests[action["actionId"]])

    def test_graph_kinds_is_only_a_hint(self) -> None:
        exec_pin = self.contexts["execPin"]
        function_hint_actions = [
            action for action in exec_pin["actions"] if "function" in action.get("graphKindsHint", [])
        ]
        self.assertTrue(function_hint_actions, "expected template-hint function actions in the event graph")
        self.assertNotIn("graphKinds", exec_pin["semantics"])

    def test_coverage_is_explicit(self) -> None:
        for name, context in self.contexts.items():
            with self.subTest(context=name):
                self.assertGreater(context["describedActions"], 0)
                self.assertGreater(context["notDescribedActions"], 0)
                self.assertLessEqual(context["describedActions"], 512)
                self.assertLessEqual(context["actions"].__len__(), context["maxItems"])
                if context["actions"].__len__() < context["matchedActions"]:
                    self.assertTrue(context["truncated"])

    def test_non_k2_fails_closed(self) -> None:
        self.assertEqual(self.export["nonK2ErrorCode"], "graphpatch-context-mismatch")


if __name__ == "__main__":
    unittest.main()

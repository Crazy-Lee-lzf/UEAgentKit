from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
APPLY = PRIVATE / "BlueprintGraphLayoutApply.cpp"
HEADER = PRIVATE / "BlueprintGraphLayoutApply.h"


class GraphLayoutP2SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = APPLY.read_text(encoding="utf-8")
        cls.header = HEADER.read_text(encoding="utf-8")
        cls.bridge = (PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        cls.bridge_header = (PRIVATE / "EditorBridge.h").read_text(encoding="utf-8")

    def test_layout_hash_is_recomputed_by_cpp_not_format_only(self) -> None:
        self.assertIn("ComputeLayoutHash", self.source)
        self.assertIn("Sha256Transform", self.source)
        self.assertIn('TEXT("ueak.typedgraph.layout/1\\n")', self.source)
        self.assertIn("graph-layout-layout-hash-mismatch", self.source)
        self.assertIn("9851d00e14f91fb25bc859160a24b6f64cd8a9a593b2ae17c42a8f6f632392a6", self.source)
        self.assertNotIn("FPlatformMisc::GetSHA256Signature(", self.source)

    def test_transaction_buffer_must_be_idle_and_scoped_transaction_must_open(self) -> None:
        self.assertIn("GEditor->CanTransact()", self.source)
        self.assertIn("GEditor->Trans->IsActive()", self.source)
        self.assertIn("graph-layout-transaction-active", self.source)
        self.assertIn("Transaction.IsOutstanding()", self.source)
        self.assertIn("graph-layout-transaction-unavailable", self.source)
        self.assertEqual(self.source.count("FScopedTransaction"), 1)

    def test_pie_ownership_and_live_node_guards_are_present(self) -> None:
        self.assertIn("IsPlaySessionInProgress()", self.source)
        self.assertIn("IsGraphOwnedByBlueprint", self.source)
        self.assertIn("CollectBlueprintGraphs", self.source)
        self.assertIn("graph-layout-invalid-live-node", self.source)
        self.assertIn("graph-layout-duplicate-live-node", self.source)
        self.assertIn("Graph->bEditable", self.source)
        self.assertIn('#include "Editor/Transactor.h"', self.source)

    def test_request_schema_and_acceptance_evidence_are_enforced(self) -> None:
        self.assertIn("ueak.graphlayoutapply/1.", self.source)
        self.assertIn("requiredAcceptanceEvidence", self.source)
        self.assertIn("independent-reload", self.source)
        self.assertIn("requestedMode", self.source)
        self.assertIn("position-only", self.source)

    def test_handler_remains_internal_and_unregistered(self) -> None:
        self.assertNotIn('TEXT("editor.applyBlueprintGraphLayout")', self.bridge)
        self.assertNotIn("UEAgentKitBlueprintGraphLayout::TryApply", self.bridge)
        self.assertNotIn("TryApplyBlueprintGraphLayout", self.bridge)
        self.assertIn("NOT registered", self.header)


if __name__ == "__main__":
    unittest.main()

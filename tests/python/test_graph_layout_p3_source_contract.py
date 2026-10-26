from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
P3_TEST = PRIVATE / "BlueprintGraphLayoutP3Test.cpp"
APPLY = PRIVATE / "BlueprintGraphLayoutApply.cpp"
HEADER = PRIVATE / "BlueprintGraphLayoutApply.h"


class GraphLayoutP3SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test = P3_TEST.read_text(encoding="utf-8")
        cls.apply = APPLY.read_text(encoding="utf-8")
        cls.header = HEADER.read_text(encoding="utf-8")
        cls.bridge = (PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        cls.bridge_header = (PRIVATE / "EditorBridge.h").read_text(encoding="utf-8")

    def test_preflight_contract_is_a_real_editor_automation_test(self) -> None:
        self.assertIn('"UEAgentKit.GraphLayout.PreflightContract"', self.test)
        self.assertIn("EAutomationTestFlags::EditorContext", self.test)
        self.assertIn("UEAgentKitBlueprintGraphLayout::BuildPreflight", self.test)
        self.assertIn("IMPLEMENT_SIMPLE_AUTOMATION_TEST", self.test)

    def test_fixture_is_an_isolated_disposable_k2_blueprint(self) -> None:
        self.assertIn('TEXT("/Game/UEAgentKitP3/BP_LayoutP3")', self.test)
        self.assertIn("FKismetEditorUtilities::CreateBlueprint", self.test)
        self.assertIn("FGraphNodeCreator<UK2Node_ExecutionSequence>", self.test)
        self.assertIn("FGraphNodeCreator<UK2Node_Knot>", self.test)
        self.assertIn("FGraphNodeCreator<UEdGraphNode_Comment>", self.test)
        self.assertIn("TryCreateConnection", self.test)
        self.assertIn("NodeGuid", self.test)
        self.assertIn("PinId", self.test)
        # The disposable fixture must not embed any local absolute path.  The
        # synthetic-build assertions above already prove it is not a copy of an
        # external project's asset.
        self.assertNotRegex(self.test, r"[A-Za-z]:[\\/]")

    def test_preflight_asserts_identity_revision_dirty_and_fingerprint(self) -> None:
        for field in (
            "assetPath",
            "graphGuid",
            "packageRevision",
            "packageDirty",
            "graphFingerprint",
            "nodeCount",
            "commentCount",
            "rerouteCount",
        ):
            self.assertIn(field, self.test)
        self.assertIn("graph-layout-revision-unavailable", self.test)
        self.assertIn("graph-layout-graph-mismatch", self.test)
        self.assertIn("graph-layout-graph-unavailable", self.test)
        self.assertIn("graph-layout-invalid-live-node", self.apply)
        self.assertIn("sha1:", self.test)
        self.assertIn("sha256:", self.test)

    def test_apply_contract_uses_real_transaction_undo_redo_and_save(self) -> None:
        self.assertIn('"UEAgentKit.GraphLayout.ApplyContract"', self.test)
        self.assertIn("UEAgentKitBlueprintGraphLayout::ApplyToGraph", self.test)
        self.assertIn("GEditor->UndoTransaction()", self.test)
        self.assertIn("GEditor->RedoTransaction()", self.test)
        self.assertIn("UPackage::SavePackage", self.test)
        self.assertIn("graph-layout-revision-conflict", self.test)
        self.assertIn("graph-layout-fingerprint-conflict", self.test)
        self.assertIn("graph-layout-package-dirty", self.test)
        self.assertIn("graph-layout-transaction-active", self.test)
        self.assertIn("graph-layout-layout-hash-mismatch", self.test)
        self.assertIn("UEAgentKitBlueprintGraphLayout::TryApply", self.test)
        self.assertIn("graph-layout-editor-not-open", self.test)
        self.assertIn("StructuralState", self.test)
        self.assertIn("apply changed no node identity", self.test)
        self.assertIn("undo/redo changed no node identity", self.test)

    def test_reload_contract_runs_in_a_separate_process(self) -> None:
        self.assertIn('"UEAgentKit.GraphLayout.ReloadContract"', self.test)
        self.assertIn("LoadObject<UBlueprint>", self.test)
        self.assertIn("independentReloadVerified", self.test)
        self.assertIn("postRevision", self.test)
        self.assertIn("postFingerprint", self.test)

    def test_layout_hash_is_shared_with_the_apply_gate(self) -> None:
        self.assertIn("bool ComputeLayoutHash(", self.header)
        self.assertIn("ComputeLayoutHash(", self.apply)
        self.assertIn("ComputeLayoutHash(Plan, LayoutHash)", self.test)
        self.assertIn("ComputeLayoutHash(Plan, RecomputedHash)", self.test)

    def test_write_handler_remains_internal_and_unregistered(self) -> None:
        self.assertNotIn('TEXT("editor.applyBlueprintGraphLayout")', self.bridge)
        self.assertNotIn("UEAgentKitBlueprintGraphLayout::TryApply", self.bridge)
        self.assertIn("BuildBlueprintGraphLayoutPreflightResult", self.bridge)
        self.assertIn("editor.getBlueprintGraphLayoutPreflight", self.bridge)
        self.assertIn("NOT registered", self.header)

    def test_apply_is_position_only_and_does_not_allow_save_or_compile(self) -> None:
        self.assertIn('"requestedMode"', self.test)
        self.assertIn('"position-only"', self.test)
        self.assertIn('"allowSave"', self.test)
        self.assertIn('"allowCompile"', self.test)
        self.assertIn("SetBoolField(TEXT(\"allowSave\"), false)", self.test)
        self.assertIn("SetBoolField(TEXT(\"allowCompile\"), false)", self.test)
        self.assertIn('"requiredAcceptanceEvidence"', self.test)
        self.assertIn('"independent-reload"', self.test)


if __name__ == "__main__":
    unittest.main()

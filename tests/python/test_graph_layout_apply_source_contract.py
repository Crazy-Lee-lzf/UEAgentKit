from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
APPLY = PRIVATE / "BlueprintGraphLayoutApply.cpp"
HEADER = PRIVATE / "BlueprintGraphLayoutApply.h"


class GraphLayoutApplySourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = APPLY.read_text(encoding="utf-8")
        cls.header = HEADER.read_text(encoding="utf-8")
        cls.bridge = (PRIVATE / "EditorBridge.cpp").read_text(encoding="utf-8")
        cls.bridge_header = (PRIVATE / "EditorBridge.h").read_text(encoding="utf-8")

    def test_handler_is_internal_and_not_registered_on_bridge_dispatch(self) -> None:
        self.assertNotIn('TEXT("editor.applyBlueprintGraphLayout")', self.bridge)
        self.assertNotIn("UEAgentKitBlueprintGraphLayout::TryApply", self.bridge)
        self.assertNotIn("TryApplyBlueprintGraphLayout", self.bridge)
        self.assertIn("not registered", self.header.lower())
        self.assertNotIn("Method == TEXT(\"editor.apply", self.source)

    def test_only_position_assignment_is_implemented_in_one_transaction(self) -> None:
        self.assertEqual(self.source.count("FScopedTransaction"), 1)
        self.assertIn("Blueprint->Modify()", self.source)
        self.assertIn("Graph->Modify()", self.source)
        self.assertIn("Node->Modify()", self.source)
        self.assertIn("Node->NodePosX = Target.X", self.source)
        self.assertIn("Node->NodePosY = Target.Y", self.source)
        self.assertIn("FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint)", self.source)
        self.assertIn("Transaction.Cancel()", self.source)
        self.assertIn("Package->SetDirtyFlag(bDirtyBefore)", self.source)
        self.assertIn("BeforePositions", self.source)

    def test_no_topology_default_size_compile_save_or_shell_mutation(self) -> None:
        forbidden = (
            "SavePackage",
            "UPackage::Save",
            "CompileBlueprint",
            "FBlueprintEditorUtils::CompileBlueprint",
            "CreateNode",
            "AddNode",
            "RemoveNode",
            "BreakLinkTo",
            "MakeLinkTo",
            "Pin->DefaultValue =",
            "NodeWidth =",
            "NodeHeight =",
            "GEditor->Exec",
            "ProcessEvent",
            "system(",
            "FPlatformProcess::CreateProc",
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, self.source)
        self.assertIn('SetBoolField(TEXT("saved"), false)', self.source)
        self.assertIn('SetBoolField(TEXT("compiled"), false)', self.source)

    def test_identity_revision_dirty_pie_and_editor_gates_are_present(self) -> None:
        for code in (
            "graph-layout-invalid-parameters",
            "graph-layout-graph-mismatch",
            "graph-layout-asset-not-loaded",
            "graph-layout-graph-not-found",
            "graph-layout-graph-ambiguous",
            "graph-layout-editor-not-open",
            "graph-layout-graph-not-focused",
            "graph-layout-graph-not-editable",
            "graph-layout-graph-not-k2",
            "graph-layout-pie-active",
            "graph-layout-package-dirty",
            "graph-layout-revision-conflict",
            "graph-layout-fingerprint-conflict",
            "graph-layout-transaction-unavailable",
            "graph-layout-limit-exceeded",
            "graph-layout-comment-partial-scope",
            "graph-layout-reroute-broken",
            "graph-layout-moved-mismatch",
            "graph-layout-scope-invalid",
            "graph-layout-coordinate-out-of-range",
            "graph-layout-readback-failed",
            "graph-layout-dirty-not-confirmed",
        ):
            with self.subTest(code=code):
                self.assertIn(code, self.source)
        self.assertIn("AllowSave", self.source)
        self.assertIn("AllowCompile", self.source)
        self.assertIn("position-only", self.source)

    def test_preflight_is_read_only_and_exposes_live_fingerprint(self) -> None:
        self.assertIn("ComputeGraphFingerprint", self.source)
        self.assertIn("sha1-length-prefixed-graph-state-v1", self.source)
        self.assertIn("BuildAssetRevision", self.source)
        self.assertIn('TEXT("packageRevision")', self.source)
        self.assertIn('TEXT("graphFingerprint")', self.source)
        self.assertIn('TEXT("nodeId")', self.source)
        self.assertNotIn("GetAllGraphs", self.source)
        for graph_list in (
            "UbergraphPages",
            "FunctionGraphs",
            "MacroGraphs",
            "DelegateSignatureGraphs",
        ):
            with self.subTest(graph_list=graph_list):
                self.assertIn(graph_list, self.source)

    def test_automation_gate_contract_is_compiled_when_enabled(self) -> None:
        self.assertIn("#if WITH_DEV_AUTOMATION_TESTS", self.source)
        self.assertIn("UEAgentKit.GraphLayout.PlanGateContract", self.source)
        self.assertIn("ValidatePlanShape", self.source)


if __name__ == "__main__":
    unittest.main()

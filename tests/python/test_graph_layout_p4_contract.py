from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
TESTS = Path(__file__).resolve().parent
for directory in (SRC, TESTS):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))

from ue_agent_kit.graph_contracts import (  # noqa: E402
    BlueprintContextImportError,
    typed_graph_from_blueprint_context,
)
from p4_algorithm_handoff import (  # noqa: E402
    HandoffError,
    _preflight_consistency,
    _source_metadata,
    build_handoff,
)

PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
P4_TEST = PRIVATE / "BlueprintGraphLayoutP4Test.cpp"
BRIDGE = PRIVATE / "EditorBridge.cpp"
FIXTURE = ROOT / "tests" / "fixtures" / "graph_contracts" / "bpctx_export_linear.json"
# Real UE 5.6.1 full export from the disposable P4 host fixture.  The two
# independent export runs produced this exact canonical payload, and it contains
# no user or production project data.
REAL_P4_EXPORT = ROOT / "tests" / "fixtures" / "graph_contracts" / "bpctx_export_p4_branch.json"
SOURCE = {
    "sessionId": "p4-offline-unit-session",
    "pluginBuildHash": "sha256:" + "a" * 64,
    "exportedAtUtc": "2026-10-22T00:00:00Z",
}


def _sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _synthetic_preflight(graph: dict) -> dict:
    """Test-only preflight shape; the UE evidence uses the real C++ builder."""

    pins_by_node: dict[str, list[dict]] = {node["nodeId"]: [] for node in graph["nodes"]}
    for pin in graph["pins"]:
        pins_by_node[pin["nodeId"]].append(pin)
    nodes = []
    for node in graph["nodes"]:
        pins = pins_by_node[node["nodeId"]]
        nodes.append(
            {
                "nodeId": node["nodeId"],
                "guid": node["nodeGuid"],
                "classPath": node["classPath"],
                "kind": "comment"
                if node["kind"] == "comment"
                else "reroute"
                if node["kind"] == "reroute"
                else "node",
                "x": node["position"]["x"],
                "y": node["position"]["y"],
                "width": node["size"]["width"],
                "height": node["size"]["height"],
                "inputPinCount": sum(1 for pin in pins if pin["direction"] == "input"),
                "outputPinCount": sum(1 for pin in pins if pin["direction"] == "output"),
                "inputLinkCount": sum(
                    1 for pin in pins if pin["direction"] == "input" and pin["links"]
                ),
                "outputLinkCount": sum(
                    1 for pin in pins if pin["direction"] == "output" and pin["links"]
                ),
            }
        )
    return {
        "schemaVersion": "ueak.graphlayoutpreflight/1.0",
        "assetPath": graph["asset"]["path"],
        "graphGuid": graph["graph"]["guid"],
        "packageRevision": graph["asset"]["revision"],
        "packageDirty": False,
        "graphFingerprint": "sha1:" + "b" * 40,
        "evidenceLevel": "same-process",
        "acceptanceEligible": False,
        "requiredAcceptanceEvidence": "independent-reload",
        "nodeCount": len(nodes),
        "nodes": nodes,
    }


class GraphLayoutP4SourceContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.test = P4_TEST.read_text(encoding="utf-8")
        cls.bridge = BRIDGE.read_text(encoding="utf-8")

    def test_p4_automation_uses_an_isolated_disposable_fixture(self) -> None:
        self.assertIn('"UEAgentKit.GraphLayout.P4ExportContract"', self.test)
        self.assertIn('"UEAgentKit.GraphLayout.P4ApplyContract"', self.test)
        self.assertIn('"UEAgentKit.GraphLayout.P4SelectionApplyContract"', self.test)
        self.assertIn('"UEAgentKit.GraphLayout.P4IncrementalApplyContract"', self.test)
        self.assertIn('"UEAgentKit.GraphLayout.P4ReloadContract"', self.test)
        self.assertIn('TEXT("/Game/UEAgentKitP4/BP_LayoutP4")', self.test)
        self.assertIn("UEAgentKitP4Fixture", self.test)
        self.assertNotIn("/Game/UEAgentKitP3/", self.test)
        self.assertNotIn("Lyra", self.test)
        self.assertNotIn("Reforge", self.test)

    def test_p4_export_is_the_real_canonical_exporter(self) -> None:
        self.assertIn("FBlueprintContextExporter::ExportBlueprint", self.test)
        self.assertIn("EBlueprintContextProfile::Full", self.test)
        self.assertIn("bIncludeLayout = true", self.test)
        self.assertIn("BlueprintContextSha256::HashFile", self.test)
        self.assertIn("export-provenance.json", self.test)
        self.assertIn("blueprint-context.json", self.test)
        self.assertIn('SetStringField(TEXT("kind"), TEXT("live-editor"))', self.test)
        self.assertIn("ModuleDllPath()", self.test)
        self.assertIn("pluginBuildHash", self.test)

    def test_p4_apply_uses_python_algorithm_requests_not_a_uniform_shift(self) -> None:
        self.assertIn("apply-request-full.json", self.test)
        self.assertIn("apply-request-selection.json", self.test)
        self.assertIn("apply-request-incremental.json", self.test)
        self.assertIn("layout-full.json", self.test)
        self.assertNotIn("BuildShiftedPlan", self.test)
        self.assertNotIn("FixtureShift", self.test)
        self.assertNotIn("FixtureShiftX", self.test)
        self.assertIn("algorithm output is not a single uniform shift", self.test)
        self.assertIn("the algorithm actually moves nodes", self.test)
        self.assertIn("ApplyToGraph", self.test)
        self.assertIn("GEditor->UndoTransaction()", self.test)
        self.assertIn("GEditor->RedoTransaction()", self.test)
        self.assertIn("UPackage::SavePackage", self.test)

    def test_p4_proves_position_only_and_independent_reload(self) -> None:
        self.assertIn("StructuralState", self.test)
        self.assertIn("TopologyHash", self.test)
        self.assertIn("apply changed no node identity", self.test)
        self.assertIn("undo restores the exact structural state", self.test)
        self.assertIn("P4ReloadContract", self.test)
        self.assertIn("LoadObject<UBlueprint>", self.test)
        self.assertIn("independentReloadVerified", self.test)
        self.assertIn("reloaded P4 topology", self.test)

    def test_p4_does_not_register_a_bridge_or_mcp_write_surface(self) -> None:
        self.assertIn("BuildBlueprintGraphLayoutPreflightResult", self.bridge)
        self.assertIn("editor.getBlueprintGraphLayoutPreflight", self.bridge)
        self.assertNotIn("UEAgentKitBlueprintGraphLayout::TryApply", self.bridge)
        self.assertNotIn('TEXT("editor.applyBlueprintGraphLayout")', self.bridge)


class P4RealExporterFixtureTests(unittest.TestCase):
    def test_real_ue_export_imports_without_fabricated_links(self) -> None:
        raw = json.loads(REAL_P4_EXPORT.read_text(encoding="utf-8"))
        self.assertEqual(raw["schemaVersion"], "1.1")
        self.assertEqual(raw["profile"], "full")
        self.assertEqual(raw["assetPath"], "/Game/UEAgentKitP4/BP_LayoutP4.BP_LayoutP4")
        with self.assertRaises(BlueprintContextImportError) as caught:
            typed_graph_from_blueprint_context(raw)
        self.assertEqual(caught.exception.code, "blueprint-context-source-invalid")

        graph = typed_graph_from_blueprint_context(raw, source_metadata=SOURCE)
        self.assertEqual(len(graph["nodes"]), 10)
        self.assertEqual(len(graph["pins"]), 25)
        self.assertEqual(len(graph["edges"]), 6)
        self.assertEqual(len(graph["comments"]), 1)
        self.assertEqual(
            sum(1 for node in graph["nodes"] if node["kind"] == "reroute"),
            1,
        )
        node_ids = {node["nodeId"] for node in graph["nodes"]}
        for edge in graph["edges"]:
            self.assertIn(edge["from"]["nodeId"], node_ids)
            self.assertIn(edge["to"]["nodeId"], node_ids)
            self.assertEqual(edge["origin"], "pin-link")
            self.assertEqual(edge["status"], "resolved")
        for node in graph["nodes"]:
            if node["kind"] == "comment":
                self.assertEqual(node["pins"], [])
                continue
            self.assertTrue(node["pins"])
            for pin in graph["pins"]:
                if pin["nodeId"] == node["nodeId"]:
                    self.assertIn(pin["pinId"], node["pins"])


class P4HandoffUnitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.graph = typed_graph_from_blueprint_context(cls.raw, source_metadata=SOURCE)
        cls.preflight = _synthetic_preflight(cls.graph)

    def _write_inputs(
        self,
        directory: Path,
        *,
        context: dict | None = None,
        preflight: dict | None = None,
        provenance: dict | None = None,
    ) -> tuple[Path, Path, Path]:
        context_path = directory / "blueprint-context.json"
        preflight_path = directory / "preflight.json"
        provenance_path = directory / "export-provenance.json"
        context_doc = self.raw if context is None else context
        preflight_doc = self.preflight if preflight is None else preflight
        context_path.write_text(json.dumps(context_doc, indent=2), encoding="utf-8")
        preflight_path.write_text(json.dumps(preflight_doc, indent=2), encoding="utf-8")
        provenance_doc = provenance or {
            "schemaVersion": "ueak.p4.exportprovenance/1.0",
            "kind": "live-editor",
            **SOURCE,
            "assetPath": self.graph["asset"]["path"],
            "graphGuid": self.graph["graph"]["guid"],
            "contextSha256": _sha256(context_path),
            "preflightSha256": _sha256(preflight_path),
        }
        provenance_path.write_text(json.dumps(provenance_doc, indent=2), encoding="utf-8")
        return context_path, preflight_path, provenance_path

    def test_missing_exporter_metadata_is_rejected(self) -> None:
        with self.assertRaises(BlueprintContextImportError) as caught:
            typed_graph_from_blueprint_context(self.raw)
        self.assertEqual(caught.exception.code, "blueprint-context-source-invalid")

    def test_handoff_builds_full_selection_and_incremental_requests(self) -> None:
        # A scoped handoff needs a real reason to move nodes. Make the two
        # synthetic Print String nodes collide while keeping the real export
        # fixture and handoff safety checks unchanged.
        context = copy.deepcopy(self.raw)
        calls = [node for node in context["graphs"][0]["nodes"] if node["title"].startswith("Print String")]
        self.assertEqual(len(calls), 2)
        calls[1]["layout"].update(x=calls[0]["layout"]["x"], y=calls[0]["layout"]["y"])
        graph = typed_graph_from_blueprint_context(context, source_metadata=SOURCE)
        preflight = _synthetic_preflight(graph)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            context_path, preflight_path, provenance_path = self._write_inputs(
                Path(tmp), context=context, preflight=preflight,
            )
            summary = build_handoff(context_path, preflight_path, provenance_path, out)

            self.assertEqual(summary["graphGuid"], self.graph["graph"]["guid"])
            self.assertEqual(summary["packageRevision"], self.graph["asset"]["revision"])
            self.assertEqual(summary["source"]["kind"], "live-editor")
            self.assertEqual(
                summary["missingMetadataRejected"]["code"],
                "blueprint-context-source-invalid",
            )
            self.assertEqual(set(summary["plans"]), {"full", "selection", "incremental"})
            full = summary["plans"]["full"]
            selection = summary["plans"]["selection"]
            incremental = summary["plans"]["incremental"]
            self.assertGreaterEqual(full["movedNodeCount"], 3)
            self.assertGreater(selection["movedNodeCount"], 0)
            self.assertEqual(selection["movedNodeCount"], incremental["movedNodeCount"])
            self.assertTrue((out / "apply-request-full.json").is_file())
            self.assertTrue((out / "apply-request-selection.json").is_file())
            self.assertTrue((out / "apply-request-incremental.json").is_file())
            request = json.loads((out / "apply-request-full.json").read_text(encoding="utf-8"))
            self.assertEqual(request["expectedPackageRevision"], summary["packageRevision"])
            self.assertEqual(
                request["expectedGraphFingerprint"],
                summary["preflightFingerprint"],
            )
            self.assertFalse(request["acceptanceEligible"])
            self.assertEqual(request["requiredAcceptanceEvidence"], "independent-reload")

    def test_handoff_does_not_force_a_scoped_move_on_clean_authored_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context_path, preflight_path, provenance_path = self._write_inputs(directory)
            with self.assertRaisesRegex(
                HandoffError, "no selection/incremental scope produced a real scoped plan",
            ):
                build_handoff(context_path, preflight_path, provenance_path, directory / "out")

    def test_provenance_and_preflight_tampering_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context_path, preflight_path, provenance_path = self._write_inputs(directory)
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance["contextSha256"] = "sha256:" + "c" * 64
            provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
            with self.assertRaises(HandoffError):
                build_handoff(context_path, preflight_path, provenance_path, directory / "out")

        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            context_path, preflight_path, provenance_path = self._write_inputs(directory)
            preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
            preflight["nodes"][0]["x"] += 1
            preflight_path.write_text(json.dumps(preflight), encoding="utf-8")
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            provenance["preflightSha256"] = _sha256(preflight_path)
            provenance_path.write_text(json.dumps(provenance), encoding="utf-8")
            with self.assertRaises(HandoffError):
                build_handoff(context_path, preflight_path, provenance_path, directory / "out")

    def test_preflight_consistency_rejects_a_foreign_graph(self) -> None:
        preflight = json.loads(json.dumps(self.preflight))
        preflight["graphGuid"] = "00000000-0000-4000-8000-000000000000"
        with self.assertRaises(HandoffError):
            _preflight_consistency(
                self.graph,
                preflight,
                {
                    "assetPath": self.graph["asset"]["path"],
                    "graphGuid": self.graph["graph"]["guid"],
                },
            )

    def test_handoff_provenance_requires_live_editor_kind(self) -> None:
        with self.assertRaises(HandoffError):
            _source_metadata({"schemaVersion": "ueak.p4.exportprovenance/1.0", "kind": "fixture"})


if __name__ == "__main__":
    unittest.main()

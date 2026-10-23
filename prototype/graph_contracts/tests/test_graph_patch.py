from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import descriptors as descriptor_module  # noqa: E402
from ueak_contract import fixtures, graph_patch, recovery, typed_graph  # noqa: E402


def entry(fixture_id: str) -> dict:
    wanted = fixture_id.replace("_", "-")
    for item in fixtures.load_manifest()["fixtures"]:
        if item["fixtureId"] == wanted:
            return item
    raise KeyError(fixture_id)


class GraphPatchFixtureTests(unittest.TestCase):
    def test_all_patch_fixtures_match_manifest(self) -> None:
        manifest = fixtures.load_manifest()
        for item in manifest["fixtures"]:
            if item["kind"] != "graphpatch":
                continue
            with self.subTest(fixture=item["fixtureId"]):
                patch = fixtures.load_fixture(item)
                graph = fixtures.load_graph_for(item)
                environment = graph_patch.Environment.from_dict(item["environment"])
                descriptors = fixtures.load_descriptors_for(item)
                expected = item["expected"]

                graph_snapshot = copy.deepcopy(graph)
                patch_snapshot = copy.deepcopy(patch)
                preview = graph_patch.preview_patch(patch, graph, environment, descriptors)
                self.assertEqual(preview["status"], expected["previewStatus"])
                self.assertEqual(preview["errorCode"], expected["previewErrorCode"])
                self.assertEqual(graph, graph_snapshot, "Preview must not mutate the TypedGraph")
                self.assertEqual(patch, patch_snapshot, "Preview must not mutate the Patch")
                self.assertEqual(preview["transactionId"], "")
                self.assertEqual(preview["semanticHashBefore"], preview["semanticHashAfter"])
                self.assertFalse(any(preview["writeEffects"].values()))
                self.assertEqual(preview["dirtyBefore"], preview["dirtyAfter"])

                if expected["applyStatus"]:
                    applied = graph_patch.simulate_apply(
                        patch, graph, environment, descriptors, include_result_graph=True
                    )
                    self.assertEqual(applied["status"], expected["applyStatus"])
                    self.assertEqual(applied["errorCode"], expected["applyErrorCode"])
                    if applied["status"] == "applied":
                        verified = graph_patch.verify_result(applied, applied["resultGraph"])
                        self.assertEqual(verified["status"], "verified", verified.get("errors"))
                        self.assertEqual(applied["semanticHashBefore"], graph["semanticHash"])
                        self.assertNotEqual(applied["semanticHashAfter"], applied["semanticHashBefore"])
                    if applied["status"] == "rolled-back":
                        self.assertEqual(applied["actualGraphHash"], graph["semanticHash"])

    def test_create_branch_apply_maps_and_deltas(self) -> None:
        item = entry("gp-create-branch")
        patch = fixtures.load_fixture(item)
        graph = fixtures.load_graph_for(item)
        environment = graph_patch.Environment.from_dict(item["environment"])
        descriptors = fixtures.load_descriptors_for(item)
        applied = graph_patch.simulate_apply(patch, graph, environment, descriptors, include_result_graph=True)
        self.assertEqual(applied["status"], "applied")
        self.assertEqual(len(applied["nodeIdMap"]), 2)
        self.assertEqual(len(applied["pinIdMap"]), 8)
        self.assertTrue(all(value.startswith("n:") for value in applied["nodeIdMap"].values()))
        self.assertTrue(all(value.startswith("p:") for value in applied["pinIdMap"].values()))
        self.assertEqual(len(applied["resultGraph"]["nodes"]), 2)
        self.assertEqual(len(applied["resultGraph"]["edges"]), 1)
        self.assertEqual(len(applied["operationResults"]), 4)
        self.assertTrue(all(result["receiptSha256"].startswith("sha256:") for result in applied["operationResults"]))
        self.assertTrue(applied["simulation"])
        self.assertFalse(applied["writeEffects"]["diskModified"])
        self.assertTrue(applied["writeEffects"]["dirtyChanged"])
        self.assertEqual(applied["graphpatchProfile"], "structure-default")
        self.assertEqual(applied["operationCounts"]["total"], 4)
        self.assertIn("compiled", applied["stageReceipts"])
        self.assertIn("applied", applied["stageReceipts"])
        self.assertTrue(applied["stageReceipts"]["applied"]["receiptSha256"].startswith("sha256:"))
        for artifact in ("plan", "apply-report", "typedgraph-before", "typedgraph-after", "receipt"):
            self.assertIn(artifact, applied["evidence"]["artifacts"])
            self.assertTrue(applied["evidence"]["artifacts"][artifact]["sha256"].startswith("sha256:"))
            self.assertGreater(applied["evidence"]["artifacts"][artifact]["bytes"], 0)

    def test_compile_failure_rolls_back_without_marker(self) -> None:
        item = entry("gp-compile-failure")
        patch = fixtures.load_fixture(item)
        graph = fixtures.load_graph_for(item)
        environment = graph_patch.Environment.from_dict(item["environment"])
        descriptors = fixtures.load_descriptors_for(item)
        with tempfile.TemporaryDirectory() as directory:
            store = recovery.RecoveryStore(directory)
            applied = graph_patch.simulate_apply(patch, graph, environment, descriptors, recovery=store)
            self.assertEqual(applied["status"], "rolled-back")
            self.assertEqual(applied["errorCode"], "graphpatch-compile-failed")
            self.assertEqual(applied["actualGraphHash"], graph["semanticHash"])
            self.assertIsNone(store.load("FixtureProject", graph["asset"]["path"]))
            self.assertNotIn("recoveryMarkerPath", applied)


class GraphPatchBehaviourTests(unittest.TestCase):
    def setUp(self) -> None:
        self.linear = fixtures.load_json(fixtures.fixture_path("typedgraph/tg_linear_exec.json"))
        self.empty = fixtures.load_json(fixtures.fixture_path("typedgraph/tg_empty.json"))
        self.dynamic = fixtures.load_json(fixtures.fixture_path("typedgraph/tg_dynamic_pins.json"))
        self.single = fixtures.load_json(fixtures.fixture_path("typedgraph/tg_single_chinese.json"))
        self.descriptors = fixtures.load_descriptors_for(entry("gp-create-branch"))
        self.descriptor_map = {item["actionId"]: item for item in self.descriptors}

    def _target(self, graph: dict) -> dict:
        return {
            "assetPath": graph["asset"]["path"],
            "graphId": graph["graphId"],
            "graphGuid": graph["graph"]["guid"],
            "graphName": graph["graph"]["name"],
            "graphKind": graph["graph"]["kind"],
            "baseRevision": graph["asset"]["revision"],
            "baseGraphHash": graph["semanticHash"],
            "revisionPolicy": "index-snapshot",
            "editorSessionId": "editor-session-test",
        }

    def _patch(self, graph: dict, operations: list[dict], *, key: str = "unit/key/01", **extra) -> dict:
        document = {
            "schemaVersion": "ueak.graphpatch/1.0",
            "patchId": "gp-unit-001",
            "idempotencyKey": key,
            "projectName": "FixtureProject",
            "target": self._target(graph),
            "conflictPolicy": "reject-if-changed",
            "externalDependencies": [],
            "operations": operations,
            "metadata": {},
        }
        document.update(extra)
        return document

    def _env(self, graph: dict, **overrides) -> dict:
        environment = {
            "actualRevision": graph["asset"]["revision"],
            "dirty": False,
            "pieActive": False,
            "editorSessionId": "editor-session-test",
            "recoveryPending": False,
            "sidecarFiles": [],
            "compileResult": {"result": "success", "succeeded": True, "diagnostics": []},
        }
        environment.update(overrides)
        return environment

    def _pin(self, graph: dict, node_index: int, name: str) -> dict:
        node_id = graph["nodes"][node_index]["nodeId"]
        return next(pin for pin in graph["pins"] if pin["nodeId"] == node_id and pin["name"] == name)

    def test_malformed_operation_is_rejected_not_crashed(self) -> None:
        item = entry("gp-create-branch")
        patch = copy.deepcopy(fixtures.load_fixture(item))
        patch["operations"].append("not-an-operation")
        preview = graph_patch.preview_patch(
            patch, self.empty, self._env(self.empty), fixtures.load_descriptors_for(item)
        )
        self.assertEqual(preview["status"], "rejected")
        self.assertEqual(preview["errorCode"], "contract-schema-type-mismatch")

    def test_missing_pin_index_is_ambiguous(self) -> None:
        patch = fixtures.load_json(fixtures.fixture_path("graphpatch/gp_dynamic_index.json"))
        patch = copy.deepcopy(patch)
        patch["operations"][0]["from"].pop("pinIndex")
        preview = graph_patch.preview_patch(patch, self.dynamic, self._env(self.dynamic), self.descriptors)
        self.assertEqual(preview["status"], "rejected")
        self.assertEqual(preview["errorCode"], "graphpatch-pin-ambiguous")

    def test_wrong_pin_index_is_not_found(self) -> None:
        patch = fixtures.load_json(fixtures.fixture_path("graphpatch/gp_dynamic_index.json"))
        patch = copy.deepcopy(patch)
        patch["operations"][0]["from"]["pinIndex"] = 99
        preview = graph_patch.preview_patch(patch, self.dynamic, self._env(self.dynamic), self.descriptors)
        self.assertEqual(preview["errorCode"], "graphpatch-pin-not-found")

    def test_explicit_direction_conflict(self) -> None:
        patch = fixtures.load_json(fixtures.fixture_path("graphpatch/gp_dynamic_index.json"))
        patch = copy.deepcopy(patch)
        patch["operations"][0]["from"]["direction"] = "input"
        preview = graph_patch.preview_patch(patch, self.dynamic, self._env(self.dynamic), self.descriptors)
        self.assertEqual(preview["errorCode"], "graphpatch-pin-direction-mismatch")

    def test_self_loop_requires_opt_in(self) -> None:
        sequence = self.dynamic["nodes"][0]
        patch = self._patch(
            self.dynamic,
            [
                {
                    "operationId": "op-1",
                    "type": "connectPins",
                    "from": {"node": sequence["nodeId"], "pin": "then", "direction": "output", "pinIndex": 1},
                    "to": {"node": sequence["nodeId"], "pin": "execute", "direction": "input"},
                }
            ],
        )
        preview = graph_patch.preview_patch(patch, self.dynamic, self._env(self.dynamic))
        self.assertEqual(preview["errorCode"], "graphpatch-self-loop")

    def test_set_default_on_connected_pin_is_rejected(self) -> None:
        print_b = self.linear["nodes"][2]
        patch = self._patch(
            self.linear,
            [
                {
                    "operationId": "op-1",
                    "type": "setPinDefault",
                    "pin": {"node": print_b["nodeId"], "pin": "InString", "direction": "input"},
                    "value": "x",
                    "valueType": "string",
                }
            ],
        )
        preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear))
        self.assertEqual(preview["errorCode"], "graphpatch-pin-connected")

    def test_delete_node_is_rejected_in_v1(self) -> None:
        # D-08 approved: V1 has no deleteNode, including the formerly allowed
        # unconnected/non-comment case and the expectedNodeHash path.
        node = self.single["nodes"][0]
        node_hash = typed_graph.node_hash(self.single, node["nodeId"])
        patch = self._patch(
            self.single,
            [
                {
                    "operationId": "op-1",
                    "type": "deleteNode",
                    "nodeId": node["nodeId"],
                    "expectedNodeHash": node_hash,
                }
            ],
        )
        preview = graph_patch.preview_patch(patch, self.single, self._env(self.single))
        self.assertEqual(preview["status"], "rejected")
        self.assertEqual(preview["errorCode"], "graphpatch-operation-not-allowed")
        self.assertNotIn("deleteNode", graph_patch.OPERATION_TYPES)
        schema = fixtures.load_schema("graphpatch.schema.json")
        branches = schema["properties"]["operations"]["items"]["oneOf"]
        self.assertFalse(any(branch.get("properties", {}).get("type", {}).get("const") == "deleteNode" for branch in branches))

    def test_delete_connected_node_has_no_shortcut(self) -> None:
        print_a = self.linear["nodes"][1]
        node_hash = typed_graph.node_hash(self.linear, print_a["nodeId"])
        patch = self._patch(
            self.linear,
            [{"operationId": "op-1", "type": "deleteNode", "nodeId": print_a["nodeId"], "expectedNodeHash": node_hash}],
        )
        preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear))
        self.assertEqual(preview["errorCode"], "graphpatch-operation-not-allowed")

    def test_expectation_mismatch(self) -> None:
        item = entry("gp-create-branch")
        patch = fixtures.load_fixture(item)
        patch = copy.deepcopy(patch)
        patch["expect"]["edgeCountDelta"] = 5
        preview = graph_patch.preview_patch(
            patch, self.empty, self._env(self.empty), fixtures.load_descriptors_for(item)
        )
        self.assertEqual(preview["errorCode"], "graphpatch-expectation-mismatch")

    def test_limit_exceeded(self) -> None:
        node = self.linear["nodes"][0]
        operations = [
            {"operationId": f"op-{index}", "type": "setNodePosition", "nodeId": node["nodeId"], "x": index, "y": 0}
            for index in range(201)
        ]
        patch = self._patch(self.linear, operations)
        preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear))
        self.assertEqual(preview["errorCode"], "graphpatch-limit-exceeded")

    def test_target_identity_and_session_failures(self) -> None:
        item = entry("gp-stale-revision")
        base = fixtures.load_fixture(item)
        cases = [
            ({"target": dict(base["target"], graphId="g:99999999-0000-4000-8000-000000000099")}, "graphpatch-target-graph-mismatch"),
            ({"target": dict(base["target"], assetPath="/Game/Other/BP.BP")}, "graphpatch-target-asset-mismatch"),
        ]
        for override, code in cases:
            with self.subTest(code=code):
                patch = copy.deepcopy(base)
                patch.update(override)
                preview = graph_patch.preview_patch(
                    patch, self.linear, self._env(self.linear, actualRevision=self.linear["asset"]["revision"])
                )
                self.assertEqual(preview["errorCode"], code)
        patch = copy.deepcopy(base)
        preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear, editorSessionId="other-session"))
        self.assertEqual(preview["errorCode"], "graphpatch-editor-session-mismatch")

    def test_preflight_rejections(self) -> None:
        item = entry("gp-stale-revision")
        patch = fixtures.load_fixture(item)
        cases = [
            ({"pieActive": True}, "graphpatch-pie-active"),
            ({"dirty": True}, "graphpatch-package-dirty"),
            ({"assetLoaded": False}, "graphpatch-asset-not-loaded"),
            ({"graphFound": False}, "graphpatch-graph-not-found"),
            ({"graphSchemaSupported": False}, "graphpatch-graph-schema-unsupported"),
            ({"sidecarFiles": ["BP.uasset.uexp"]}, "graphpatch-target-asset-mismatch"),
        ]
        for overrides, code in cases:
            with self.subTest(code=code):
                preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear, **overrides))
                self.assertEqual(preview["errorCode"], code)

    def test_executor_computed_revision_policy(self) -> None:
        item = entry("gp-stale-revision")
        patch = copy.deepcopy(fixtures.load_fixture(item))
        patch["target"]["revisionPolicy"] = "executor-computed"
        patch["target"]["baseRevision"] = ""
        preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear))
        self.assertEqual(preview["status"], "previewed", preview.get("errors"))

    def test_index_snapshot_requires_revision(self) -> None:
        item = entry("gp-stale-revision")
        patch = copy.deepcopy(fixtures.load_fixture(item))
        patch["target"]["baseRevision"] = ""
        preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear))
        self.assertEqual(preview["errorCode"], "graphpatch-base-revision-stale")

    def test_idempotency_replay_and_conflict(self) -> None:
        item = entry("gp-create-branch")
        patch = fixtures.load_fixture(item)
        environment = graph_patch.Environment.from_dict(item["environment"])
        descriptors = fixtures.load_descriptors_for(item)
        ledger = graph_patch.IdempotencyLedger()
        first = graph_patch.simulate_apply(patch, self.empty, environment, descriptors, ledger=ledger)
        self.assertEqual(first["status"], "applied")
        second = graph_patch.simulate_apply(patch, self.empty, environment, descriptors, ledger=ledger)
        self.assertEqual(second["status"], "applied")
        self.assertTrue(second["replayed"])
        self.assertFalse(any(second["writeEffects"].values()))

        conflicting = copy.deepcopy(patch)
        conflicting["operations"][1]["position"] = {"x": 999, "y": 999}
        third = graph_patch.simulate_apply(conflicting, self.empty, environment, descriptors, ledger=ledger)
        self.assertEqual(third["status"], "rejected")
        self.assertEqual(third["errorCode"], "graphpatch-idempotency-conflict")

    def test_derived_pin_is_only_allowed_for_value_ops_with_opt_in(self) -> None:
        source = next(node for node in self.dynamic["nodes"] if node["title"] == "Get SharedValue")
        promotable = next(node for node in self.dynamic["nodes"] if node["title"] == "Promotable Operator")
        derived = next(pin for pin in self.dynamic["pins"] if pin["nodeId"] == promotable["nodeId"])
        graph = copy.deepcopy(self.dynamic)
        mutable_pin = next(pin for pin in graph["pins"] if pin["pinId"] == derived["pinId"])
        mutable_pin["flags"]["defaultReadOnly"] = False
        mutable_pin["flags"]["hidden"] = False
        graph = typed_graph.freeze_hashes(graph)
        patch = self._patch(
            graph,
            [
                {
                    "operationId": "op-1",
                    "type": "setPinDefault",
                    "pin": {"node": promotable["nodeId"], "pin": "ErrorTolerance", "direction": "input"},
                    "value": "0.0",
                    "valueType": "string",
                }
            ],
        )
        rejected = graph_patch.preview_patch(patch, graph, self._env(graph))
        self.assertEqual(rejected["errorCode"], "graphpatch-pin-identity-unstable")
        patch["allowDerivedPinIdentity"] = True
        allowed = graph_patch.preview_patch(patch, graph, self._env(graph))
        self.assertEqual(allowed["status"], "previewed", allowed.get("errors"))

    def test_recovery_marker_clear_rules_and_save_gate(self) -> None:
        # D-08 removed deleteNode; the Recovery Marker/Save gate is still
        # required for any future manual-recovery path and D-10 remains strict.
        with tempfile.TemporaryDirectory() as directory:
            store = recovery.RecoveryStore(directory)
            marker = recovery.make_marker(
                project_name="FixtureProject",
                asset_path=self.single["asset"]["path"],
                patch_id="gp-legacy-delete",
                transaction_id="txn-legacy-delete",
                base_revision=self.single["asset"]["revision"],
                base_graph_hash=self.single["semanticHash"],
                evidence={"recoveryEvidenceSha256": "sha256:" + "a" * 64},
            )
            store.write(marker)
            with self.assertRaises(recovery.ContractError) as context:
                recovery.authorize_save("FixtureProject", self.single["asset"]["path"], store)
            self.assertEqual(context.exception.code, "graphpatch-recovery-required")
            with self.assertRaises(recovery.ContractError):
                recovery.authorize_apply("FixtureProject", self.single["asset"]["path"], store)

            with self.assertRaises(recovery.ContractError) as clear_error:
                store.clear(
                    "FixtureProject",
                    self.single["asset"]["path"],
                    user_confirmed=False,
                    recovery_evidence_sha256=marker["evidence"]["recoveryEvidenceSha256"],
                    restored_graph_hash=marker["baseGraphHash"],
                )
            self.assertEqual(clear_error.exception.code, "graphpatch-recovery-clear-denied")
            with self.assertRaises(recovery.ContractError):
                store.clear(
                    "FixtureProject",
                    self.single["asset"]["path"],
                    user_confirmed=True,
                    recovery_evidence_sha256="sha256:" + "0" * 64,
                    restored_graph_hash=marker["baseGraphHash"],
                )
            with self.assertRaises(recovery.ContractError):
                store.clear(
                    "FixtureProject",
                    self.single["asset"]["path"],
                    user_confirmed=True,
                    recovery_evidence_sha256=marker["evidence"]["recoveryEvidenceSha256"],
                    restored_graph_hash="sha256:" + "1" * 64,
                )
            self.assertTrue(
                store.clear(
                    "FixtureProject",
                    self.single["asset"]["path"],
                    user_confirmed=True,
                    recovery_evidence_sha256=marker["evidence"]["recoveryEvidenceSha256"],
                    restored_graph_hash=marker["baseGraphHash"],
                )
            )
            self.assertFalse(store.is_pending("FixtureProject", self.single["asset"]["path"]))

    def test_recovery_marker_file_blocks_preview(self) -> None:
        node = self.linear["nodes"][0]
        patch = self._patch(
            self.linear,
            [{"operationId": "op-1", "type": "setNodePosition", "nodeId": node["nodeId"], "x": 1, "y": 2}],
        )
        with tempfile.TemporaryDirectory() as directory:
            store = recovery.RecoveryStore(directory)
            marker = recovery.make_marker(
                project_name="FixtureProject",
                asset_path=self.linear["asset"]["path"],
                patch_id="gp-old",
                transaction_id="txn-old",
                base_revision=self.linear["asset"]["revision"],
                base_graph_hash=self.linear["semanticHash"],
            )
            store.write(marker)
            preview = graph_patch.preview_patch(patch, self.linear, self._env(self.linear), recovery=store)
            self.assertEqual(preview["errorCode"], "graphpatch-recovery-required")

    def test_verify_mismatch(self) -> None:
        item = entry("gp-create-branch")
        patch = fixtures.load_fixture(item)
        environment = graph_patch.Environment.from_dict(item["environment"])
        descriptors = fixtures.load_descriptors_for(item)
        applied = graph_patch.simulate_apply(
            patch, self.empty, environment, descriptors, include_result_graph=True
        )
        tampered = copy.deepcopy(applied["resultGraph"])
        tampered["pins"][0]["defaults"]["value"] = "tampered"
        verified = graph_patch.verify_result(applied, tampered)
        self.assertEqual(verified["status"], "verify-mismatch")
        self.assertEqual(verified["errorCode"], "graphpatch-verify-mismatch")


if __name__ == "__main__":
    unittest.main()

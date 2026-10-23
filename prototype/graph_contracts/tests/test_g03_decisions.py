from __future__ import annotations

import copy
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ueak_contract import fixtures, graph_patch, idempotency, layout, recovery, typed_graph  # noqa: E402
from ueak_contract.schema import validate_document  # noqa: E402


def entry(fixture_id: str) -> dict:
    wanted = fixture_id.replace("_", "-")
    for item in fixtures.load_manifest()["fixtures"]:
        if item["fixtureId"] == wanted:
            return item
    raise KeyError(fixture_id)


def load_graph(name: str) -> dict:
    return fixtures.load_json(fixtures.fixture_path(f"typedgraph/{name}.json"))


def target(graph: dict, profile: str = "structure-default") -> dict:
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
        "graphpatchProfile": profile,
    }


def environment(graph: dict, *, compile_result: dict | None = None) -> dict:
    return {
        "actualRevision": graph["asset"]["revision"],
        "dirty": False,
        "pieActive": False,
        "editorSessionId": "editor-session-test",
        "recoveryPending": False,
        "sidecarFiles": [],
        "compileResult": compile_result
        if compile_result is not None
        else {"result": "success", "succeeded": True, "diagnostics": []},
    }


def patch_for(
    graph: dict,
    operations: list[dict],
    *,
    profile: str = "structure-default",
    patch_id: str = "g03-test",
    key: str = "g03/test/01",
    expect: dict | None = None,
) -> dict:
    document = {
        "schemaVersion": "ueak.graphpatch/1.0",
        "patchId": patch_id,
        "idempotencyKey": key,
        "projectName": "FixtureProject",
        "target": target(graph, profile),
        "conflictPolicy": "reject-if-changed",
        "externalDependencies": [],
        "operations": operations,
        "metadata": {},
    }
    if expect is not None:
        document["expect"] = expect
    return document


def link_descriptor() -> dict:
    pin_type = lambda category: {
        "category": category,
        "subCategory": "",
        "subCategoryObject": "",
        "container": "none",
        "isReference": False,
        "isConst": False,
        "isWeakPointer": False,
    }
    return {
        "actionId": "action:" + "b" * 32,
        "discoveryKey": {"nodeClass": "/Script/BlueprintGraph.K2Node_CallFunction"},
        "title": "G0.3 link node",
        "pins": [
            {"name": "execute", "direction": "input", "kind": "exec", "type": pin_type("exec")},
            {"name": "then", "direction": "output", "kind": "exec", "type": pin_type("exec")},
            {"name": "InString", "direction": "input", "kind": "data", "type": pin_type("string")},
            {"name": "OutString", "direction": "output", "kind": "data", "type": pin_type("string")},
        ],
    }


def structure_250_operations() -> list[dict]:
    descriptor = link_descriptor()
    operations: list[dict] = []
    for index in range(100):
        operations.append(
            {
                "operationId": f"op-create-{index}",
                "type": "createNode",
                "tempId": f"tmp:n{index}",
                "descriptor": {"actionId": descriptor["actionId"]},
                "position": {"x": index * 200, "y": 0},
            }
        )
    for index in range(99):
        operations.append(
            {
                "operationId": f"op-exec-{index}",
                "type": "connectPins",
                "dependsOn": [f"op-create-{index}", f"op-create-{index + 1}"],
                "from": {"node": f"tmp:n{index}", "pin": "then", "direction": "output"},
                "to": {"node": f"tmp:n{index + 1}", "pin": "execute", "direction": "input"},
                "connectionPolicy": "reject-replacement",
            }
        )
    for index in range(51):
        operations.append(
            {
                "operationId": f"op-data-{index}",
                "type": "connectPins",
                "dependsOn": [f"op-create-{index}", f"op-create-{index + 49}"],
                "from": {"node": f"tmp:n{index}", "pin": "OutString", "direction": "output"},
                "to": {"node": f"tmp:n{index + 49}", "pin": "InString", "direction": "input"},
                "connectionPolicy": "reject-replacement",
            }
        )
    return operations


class D06ProfileTests(unittest.TestCase):
    def test_default_200_rejects_g2_250_operation_patch(self) -> None:
        graph = load_graph("tg_empty")
        operations = structure_250_operations()
        patch = patch_for(graph, operations, profile="structure-default", expect={"nodeCountDelta": 100, "edgeCountDelta": 150})
        preview = graph_patch.preview_patch(patch, graph, environment(graph), [link_descriptor()])
        self.assertEqual(preview["status"], "rejected")
        self.assertEqual(preview["errorCode"], "graphpatch-limit-exceeded")
        self.assertEqual(preview["operationCounts"]["total"], 250)

    def test_g2_300_accepts_100_create_150_connect(self) -> None:
        graph = load_graph("tg_empty")
        operations = structure_250_operations()
        patch = patch_for(graph, operations, profile="structure-g2", expect={"nodeCountDelta": 100, "edgeCountDelta": 150})
        preview = graph_patch.preview_patch(patch, graph, environment(graph), [link_descriptor()])
        self.assertEqual(preview["status"], "previewed", preview.get("errors"))
        self.assertEqual(preview["graphpatchProfile"], "structure-g2")
        self.assertEqual(preview["operationCounts"], {"create": 100, "connect": 150, "position": 0, "other": 0, "total": 250})

    def test_layout_only_accepts_1000_positions_and_rejects_1001(self) -> None:
        graph = load_graph("tg_linear_exec")
        node = graph["nodes"][0]
        operations = [
            {"operationId": f"op-{index}", "type": "setNodePosition", "nodeId": node["nodeId"], "x": index, "y": 0}
            for index in range(1000)
        ]
        patch = patch_for(graph, operations, profile="layout-only", patch_id="g03-layout-1000")
        preview = graph_patch.preview_patch(patch, graph, environment(graph))
        self.assertEqual(preview["status"], "previewed", preview.get("errors"))
        self.assertEqual(preview["operationCounts"]["position"], 1000)

        too_many = patch_for(graph, operations + [{"operationId": "op-1000", "type": "setNodePosition", "nodeId": node["nodeId"], "x": 1, "y": 1}], profile="layout-only", patch_id="g03-layout-1001")
        rejected = graph_patch.preview_patch(too_many, graph, environment(graph))
        self.assertEqual(rejected["errorCode"], "graphpatch-limit-exceeded")

    def test_layout_only_rejects_mixed_create(self) -> None:
        graph = load_graph("tg_empty")
        descriptor = link_descriptor()
        operations = [
            {
                "operationId": "op-create",
                "type": "createNode",
                "tempId": "tmp:n0",
                "descriptor": {"actionId": descriptor["actionId"]},
            }
        ] + [
            {"operationId": f"op-{index}", "type": "setNodePosition", "nodeId": "tmp:n0", "x": index, "y": 0}
            for index in range(1000)
        ]
        patch = patch_for(graph, operations, profile="layout-only", patch_id="g03-mixed")
        preview = graph_patch.preview_patch(patch, graph, environment(graph), [descriptor])
        self.assertEqual(preview["errorCode"], "graphpatch-limit-exceeded")
        self.assertIn("layout-only profile forbids", str(preview["errors"]))


class D07IdempotencyStoreTests(unittest.TestCase):
    def _patch(self) -> tuple[dict, dict, dict, list[dict]]:
        item = entry("gp_create_branch")
        return fixtures.load_fixture(item), fixtures.load_graph_for(item), environment(fixtures.load_graph_for(item)), fixtures.load_descriptors_for(item)

    def test_applied_replay_restart_and_conflict(self) -> None:
        patch, graph, env, descriptors = self._patch()
        with tempfile.TemporaryDirectory() as directory:
            store = idempotency.GraphPatchIdempotencyStore(directory)
            first = graph_patch.simulate_apply(patch, graph, env, descriptors, idempotency_store=store)
            self.assertEqual(first["status"], "applied")
            self.assertEqual(first["idempotencyState"], "applied")
            self.assertEqual(store.load(patch)["state"], "applied")

            restarted = idempotency.GraphPatchIdempotencyStore(directory)
            replay = graph_patch.simulate_apply(patch, graph, env, descriptors, idempotency_store=restarted)
            self.assertTrue(replay["replayed"])
            self.assertFalse(any(replay["writeEffects"].values()))

            conflict_patch = copy.deepcopy(patch)
            conflict_patch["operations"][1]["position"] = {"x": 999, "y": 999}
            conflict = graph_patch.simulate_apply(conflict_patch, graph, env, descriptors, idempotency_store=restarted)
            self.assertEqual(conflict["errorCode"], "graphpatch-idempotency-conflict")

    def test_crash_between_apply_and_mark_is_indeterminate(self) -> None:
        patch, graph, env, descriptors = self._patch()
        with tempfile.TemporaryDirectory() as directory:
            class CrashingStore(idempotency.GraphPatchIdempotencyStore):
                def mark_applied(self, patch: dict, payload_hash: str, result: dict) -> dict:
                    raise RuntimeError("simulated crash between Apply and mark_applied")

            store = CrashingStore(directory)
            with self.assertRaises(RuntimeError):
                graph_patch.simulate_apply(patch, graph, env, descriptors, idempotency_store=store)
            restarted = idempotency.GraphPatchIdempotencyStore(directory)
            self.assertEqual(restarted.load(patch)["state"], "reserved")
            retry = graph_patch.simulate_apply(patch, graph, env, descriptors, idempotency_store=restarted)
            self.assertEqual(retry["errorCode"], "graphpatch-idempotency-indeterminate")
            self.assertFalse(any(retry["writeEffects"].values()))

    def test_scope_lock_is_fail_closed(self) -> None:
        patch, _, _, _ = self._patch()
        with tempfile.TemporaryDirectory() as directory:
            store = idempotency.GraphPatchIdempotencyStore(directory)
            with store._scope_lock(patch) as acquired:
                self.assertTrue(acquired)
                outcome = store.reserve(patch, graph_patch.payload_hash(patch))
                self.assertEqual(outcome.state, "locked")

    def test_change_set_2_1_block_schema(self) -> None:
        record = {
            "schemaVersion": "ueak.change-set-idempotency/1.0",
            "changeSetSchemaVersion": "2.1",
            "scopeKey": "sha256:" + "0" * 64,
            "projectName": "FixtureProject",
            "assetPath": "/Game/Test/BP.BP",
            "graphId": "g:11111111-1111-4111-8111-111111111111",
            "idempotencyKey": "g03/test",
            "patchId": "g03",
            "payloadHash": "sha256:" + "1" * 64,
            "state": "reserved",
            "reservedAtUtc": "2026-10-22T00:00:00.000Z",
            "updatedAtUtc": "2026-10-22T00:00:00.000Z",
        }
        schema = fixtures.load_schema("change-set-idempotency.schema.json")
        self.assertEqual(validate_document(record, schema), [])


class D08DeleteTests(unittest.TestCase):
    def test_delete_absent_from_operation_and_schema(self) -> None:
        self.assertNotIn("deleteNode", graph_patch.OPERATION_TYPES)
        schema = fixtures.load_schema("graphpatch.schema.json")
        branches = schema["properties"]["operations"]["items"]["oneOf"]
        self.assertFalse(any(branch["properties"]["type"].get("const") == "deleteNode" for branch in branches))

    def test_legacy_manual_recovery_evidence_retained(self) -> None:
        evidence = fixtures.load_json(Path(__file__).resolve().parents[1] / "evidence" / "G0_2_verification_result.json")
        delete = evidence.get("deleteNodeRecovery", {})
        self.assertEqual(delete.get("status"), "manual-recovery")
        self.assertTrue(delete.get("markerRequired"))
        self.assertTrue(delete.get("saveBlockedByMarker"))


class D11CompileTests(unittest.TestCase):
    def test_warning_does_not_rollback_and_is_recorded(self) -> None:
        item = entry("gp_compile_warning")
        patch = fixtures.load_fixture(item)
        graph = fixtures.load_graph_for(item)
        env = graph_patch.Environment.from_dict(item["environment"])
        result = graph_patch.simulate_apply(patch, graph, env, fixtures.load_descriptors_for(item))
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["compileStatus"], "success-with-warnings")
        self.assertIn("graphpatch-compile-warning", [warning["code"] for warning in result["warnings"]])
        self.assertFalse(result["writeEffects"]["diskModified"])

    def test_missing_compile_result_fails_closed(self) -> None:
        item = entry("gp_create_branch")
        patch = fixtures.load_fixture(item)
        graph = fixtures.load_graph_for(item)
        env = environment(graph, compile_result=None)
        env.pop("compileResult")
        result = graph_patch.simulate_apply(patch, graph, env, fixtures.load_descriptors_for(item))
        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(result["errorCode"], "graphpatch-compile-result-missing")

    def test_error_rolls_back(self) -> None:
        item = entry("gp_compile_failure")
        patch = fixtures.load_fixture(item)
        graph = fixtures.load_graph_for(item)
        result = graph_patch.simulate_apply(
            patch, graph, graph_patch.Environment.from_dict(item["environment"]), fixtures.load_descriptors_for(item)
        )
        self.assertEqual(result["status"], "rolled-back")
        self.assertEqual(result["errorCode"], "graphpatch-compile-failed")


class D09AndIdentityTests(unittest.TestCase):
    def test_set_node_enabled_requires_confirmation(self) -> None:
        graph = load_graph("tg_single_chinese")
        node = graph["nodes"][0]
        base = {
            "operationId": "op-1",
            "type": "setNodeEnabled",
            "nodeId": node["nodeId"],
            "enabled": False,
        }
        rejected = graph_patch.preview_patch(
            patch_for(graph, [dict(base)], patch_id="g03-enabled-no-confirm"), graph, environment(graph)
        )
        self.assertEqual(rejected["errorCode"], "graphpatch-confirmation-required")
        confirmed = graph_patch.preview_patch(
            patch_for(graph, [dict(base, confirmed=True)], patch_id="g03-enabled-confirm"), graph, environment(graph)
        )
        self.assertEqual(confirmed["status"], "previewed", confirmed.get("errors"))

    def test_comment_hash_is_verified_separately(self) -> None:
        graph = load_graph("tg_single_chinese")
        node = graph["nodes"][0]
        patch = patch_for(
            graph,
            [{"operationId": "op-1", "type": "setNodeComment", "nodeId": node["nodeId"], "comment": "G0.3 comment"}],
            patch_id="g03-comment",
        )
        result = graph_patch.simulate_apply(patch, graph, environment(graph), include_result_graph=True)
        self.assertEqual(result["status"], "applied")
        self.assertNotEqual(result["commentHashBefore"], result["commentHash"])
        tampered = copy.deepcopy(result["resultGraph"])
        tampered["nodes"][0]["comment"] = "tampered"
        verified = graph_patch.verify_result(result, tampered)
        self.assertEqual(verified["status"], "verify-mismatch")

    def test_ubergraph_apply_is_rejected(self) -> None:
        item = entry("gp_create_branch")
        patch = fixtures.load_fixture(item)
        graph = fixtures.load_graph_for(item)
        patch["target"]["graphKind"] = "ubergraph"
        preview = graph_patch.preview_patch(patch, graph, environment(graph), fixtures.load_descriptors_for(item))
        self.assertEqual(preview["errorCode"], "graphpatch-graph-kind-not-allowed")

    def test_fallback_target_graph_id_is_rejected(self) -> None:
        item = entry("gp_create_branch")
        patch = fixtures.load_fixture(item)
        graph = fixtures.load_graph_for(item)
        patch["target"]["graphId"] = "g:name:EventGraph"
        preview = graph_patch.preview_patch(patch, graph, environment(graph), fixtures.load_descriptors_for(item))
        self.assertEqual(preview["errorCode"], "graphpatch-target-graph-mismatch")

    def test_runtime_has_no_third_party_import(self) -> None:
        package = ROOT / "ueak_contract"
        for path in package.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("import jsonschema", text, path.name)
            self.assertNotIn("from jsonschema", text, path.name)


class D12LayoutEvidenceTests(unittest.TestCase):
    def test_all_layout_modes_are_not_acceptance_eligible_offline(self) -> None:
        graph = load_graph("tg_linear_exec")
        for mode in ("full", "selection", "incremental"):
            with self.subTest(mode=mode):
                request = {"mode": mode, "scope": [graph["nodes"][1]["nodeId"]], "options": {"spacingX": 80, "spacingY": 40}}
                plan = layout.layout_graph(graph, request)
                self.assertEqual(plan.status, "ok", plan.errors)
                self.assertEqual(plan.plan["evidenceLevel"], "same-process")
                self.assertFalse(plan.plan["acceptanceEligible"])
                self.assertEqual(plan.plan["requiredAcceptanceEvidence"], "independent-reload")

    def test_suggested_reroutes_are_rejected_by_v1_schema(self) -> None:
        graph = load_graph("tg_linear_exec")
        plan = layout.layout_graph(graph, {"mode": "full"})
        mutated = copy.deepcopy(plan.plan)
        mutated["suggestedReroutes"] = []
        issues = validate_document(mutated, fixtures.load_schema("layout-plan.schema.json"))
        self.assertTrue(any("suggestedReroutes" in issue["message"] for issue in issues))


if __name__ == "__main__":
    unittest.main()

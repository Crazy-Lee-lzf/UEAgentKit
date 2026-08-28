from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_workflow import WorkflowError, live_write_stable_target_key  # noqa: E402
from ue_agent_kit.bounded_batch import BoundedBatchService  # noqa: E402

PROJECT = "TestProject"
BP_A = "/Game/UEAgentKitWriteTests/Transactions/BP_A.BP_A"
BP_B = "/Game/UEAgentKitWriteTests/Transactions/BP_B.BP_B"
BP_C = "/Game/UEAgentKitWriteTests/Transactions/BP_C.BP_C"
BP_D = "/Game/UEAgentKitWriteTests/Transactions/BP_D.BP_D"
BP_ASSET = BP_A
DA_ASSET = "/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset"
BP_CLASS = "/Script/Engine.Blueprint"
DA_CLASS = "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset"
REV_BP = "sha256:" + "b" * 64
REV_DA = "sha256:" + "d" * 64
REV_DA_NEW = "sha256:" + "e" * 64

PIN_GRAPH = "12345678-9abc-def0-1234-56789abcdef0"
PIN_NODE = "11111111-2222-2222-3333-333344444444"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def _make_revision_export(root: Path, *, include_all: bool = True, da_revision: str = REV_DA) -> Path:
    revision_export = root / "RevisionExport"
    assets = [BP_A, BP_B, BP_C, BP_D, DA_ASSET] if include_all else [BP_ASSET, DA_ASSET]
    manifest = {
        "projectName": PROJECT,
        "failureCount": 0,
        "successCount": len(assets),
    }
    _write_json(revision_export / "manifest.json", manifest)
    for index, asset_path in enumerate(assets):
        asset_class = DA_CLASS if asset_path == DA_ASSET else BP_CLASS
        revision = da_revision if asset_path == DA_ASSET else REV_BP
        filename = f"asset_{index}.json"
        _write_json(
            revision_export / "canonical" / filename,
            {
                "projectName": PROJECT,
                "assetPath": asset_path,
                "assetClass": asset_class,
                "revision": {
                    "available": True,
                    "packageDirty": False,
                    "value": revision,
                },
            },
        )
    return revision_export


def _make_policy(
    root: Path,
    *,
    max_assets: int = 4,
    max_ops_per_asset: int = 8,
    max_value_bytes: int = 16384,
    allowed_operations: list[str] | None = None,
) -> Path:
    policy = {
        "schemaVersion": "1.0",
        "validationEnabled": True,
        "commitEnabled": True,
        "allowedProjectNames": [PROJECT],
        "allowedAssetRoots": ["/Game/UEAgentKitWriteTests"],
        "allowedReferenceRoots": [],
        "allowedReferenceClasses": [],
        "allowedOperations": allowed_operations
        or ["setAssetProperty", "setVariableDefault", "setComponentProperty", "setPinDefault"],
        "allowedAssetClasses": [BP_CLASS, DA_CLASS],
        "allowedAssetProperties": [f"{DA_CLASS}#IntValue"],
        "allowedMaterialParameters": [],
        "allowedDataTableFields": [],
        "requireRevision": True,
        "rejectDirtyPackages": True,
        "maxAssetsPerPatch": max_assets,
        "maxOperationsPerAsset": max_ops_per_asset,
        "maxValueBytes": max_value_bytes,
    }
    policy_path = root / "policy.json"
    _write_json(policy_path, policy)
    return policy_path


class _FakeWorkflowService:
    def __init__(
        self,
        work_root: Path,
        policy_path: Path,
        revision_export: Path,
        *,
        fail_bind_asset: str = "",
        fail_bind_code: str = "asset-not-indexed",
        fail_plan_on_call: int = 0,
        fail_apply_on_call: int = 0,
        fail_fast_on_call: int = 0,
    ) -> None:
        self.config = SimpleNamespace(
            work_root=work_root,
            policy_path=policy_path,
            revision_export=revision_export,
            commit_enabled=True,
        )
        self.project_name = PROJECT
        self.bind_calls: list[str] = []
        self.plan_calls: list[dict[str, Any]] = []
        self.discarded: list[str] = []
        self.apply_calls: list[dict[str, Any]] = []
        self.verify_calls: list[dict[str, Any]] = []
        self.plan_available_checks: list[str] = []
        self.plan_counter = 0
        self.fail_bind_asset = fail_bind_asset
        self.fail_bind_code = fail_bind_code
        self.fail_plan_on_call = fail_plan_on_call
        self.fail_apply_on_call = fail_apply_on_call
        self.fail_fast_on_call = fail_fast_on_call

    def bind_asset_for_batch(self, asset_path: str) -> dict[str, Any]:
        self.bind_calls.append(asset_path)
        if asset_path == self.fail_bind_asset:
            raise WorkflowError(self.fail_bind_code, "Injected batch bind failure.")
        if asset_path == DA_ASSET:
            return {
                "assetPath": asset_path,
                "assetClass": DA_CLASS,
                "expectedRevision": REV_DA,
            }
        if asset_path.startswith("/Game/UEAgentKitWriteTests/Transactions/BP_"):
            return {
                "assetPath": asset_path,
                "assetClass": BP_CLASS,
                "expectedRevision": REV_BP,
            }
        raise WorkflowError("asset-not-indexed", "The requested asset is not present in the fixed SQLite index.")

    def plan_patch(self, **kwargs: Any) -> dict[str, Any]:
        self.plan_calls.append(kwargs)
        if self.fail_plan_on_call and len(self.plan_calls) == self.fail_plan_on_call:
            raise WorkflowError("injected-child-failure", "Injected child Plan failure.")
        self.plan_counter += 1
        asset_path = str(kwargs["asset_path"])
        operation = str(kwargs["operation"])
        return {
            "planId": f"plan_{self.plan_counter}",
            "patchDigest": f"sha256:{self.plan_counter:064x}",
            "assetClass": DA_CLASS if asset_path == DA_ASSET else BP_CLASS,
            "expectedRevision": REV_DA if asset_path == DA_ASSET else REV_BP,
            "risk": "low" if operation in {"setVariableDefault", "setComponentProperty", "setPinDefault"} else "medium",
            "commitAllowedByPolicy": True,
        }

    def discard_unconsumed_plans(self, plan_ids: list[str]) -> None:
        self.discarded.extend(plan_ids)

    def get_change_set(self, change_set_id: str) -> dict[str, Any]:
        return {"ok": True, "changeSetId": change_set_id, "operations": []}

    def assert_plan_available_for_batch(self, plan_id: str) -> None:
        self.plan_available_checks.append(plan_id)

    def apply_asset_property_live(self, plan_id: str, confirmation: str, change_set_id: str = "") -> dict[str, Any]:
        self.apply_calls.append(
            {
                "planId": plan_id,
                "confirmation": confirmation,
                "changeSetId": change_set_id,
            }
        )
        if self.fail_apply_on_call and len(self.apply_calls) == self.fail_apply_on_call:
            raise WorkflowError("injected-apply-failure", "Injected resident Apply failure.")
        index = len(self.apply_calls)
        return {
            "changed": True,
            "liveApplyReceipt": f"live_{index}",
            "result": {
                "transactionId": f"tx_{index}",
                "editorSessionId": "session-1",
                "afterValue": index,
            },
        }

    def verify_live_write_fast(self, asset_path: str, live_apply_receipt: str, change_set_id: str = "") -> dict[str, Any]:
        self.verify_calls.append(
            {
                "assetPath": asset_path,
                "liveApplyReceipt": live_apply_receipt,
                "changeSetId": change_set_id,
            }
        )
        if self.fail_fast_on_call and len(self.verify_calls) == self.fail_fast_on_call:
            raise WorkflowError("injected-fast-failure", "Injected Fast Verify failure.")
        return {
            "ok": True,
            "verified": True,
            "verificationKind": "resident-fast",
            "assetPath": asset_path,
            "liveApplyReceipt": live_apply_receipt,
            "transactionId": f"tx_{len(self.verify_calls)}",
            "editorSessionId": "session-1",
        }


def _bp_ops() -> list[dict[str, Any]]:
    return [
        {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 42},
        {
            "operation": "setComponentProperty",
            "target": {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
            "value": 10,
        },
        {
            "operation": "setPinDefault",
            "target": {"graphGuid": PIN_GRAPH, "nodeGuid": PIN_NODE, "pinName": "A"},
            "value": 7,
        },
    ]


def _da_ops() -> list[dict[str, Any]]:
    return [{"operation": "setAssetProperty", "target": {"propertyPath": "IntValue"}, "value": 142}]


class BoundedBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_bounded_batch_")
        self.root = Path(self.temporary.name)
        self.work_root = self.root / "Output" / "McpWorkflow"
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.policy_path = _make_policy(self.root)
        self.revision_export = _make_revision_export(self.root)
        self.workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
        )
        self.service = BoundedBatchService(self.workflow)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _request_b1(self) -> dict[str, Any]:
        return {
            "assets": [
                {"assetPath": BP_ASSET, "operations": _bp_ops()},
                {"assetPath": DA_ASSET, "operations": _da_ops()},
            ],
            "description": "W4-1 B1 logical payload",
        }

    def test_happy_path_b1_shape_and_ordering(self) -> None:
        result = self.service.plan(**self._request_b1())
        self.assertTrue(result["ok"])
        self.assertEqual(result["assetCount"], 2)
        self.assertEqual(result["operationCount"], 4)
        self.assertEqual(result["state"], "planned")
        self.assertEqual(result["projectName"], PROJECT)
        self.assertTrue(result["batchPlanId"].startswith("lwbp_"))
        self.assertTrue(result["batchPlanDigest"].startswith("sha256:"))
        self.assertTrue(result["requestDigest"].startswith("sha256:"))
        self.assertEqual(result["confirmationRequired"], f"APPLY LIVE WRITE BATCH {result['batchPlanId']}")
        self.assertEqual(result["nextStep"], "Call ue_apply_live_write_batch after W4-2 is available.")
        self.assertEqual(result["bounds"]["hard"]["maxAssets"], 4)
        self.assertEqual(result["bounds"]["effective"]["maxAssets"], 4)
        ops = [
            operation
            for asset in result["assets"]
            for operation in asset["operations"]
        ]
        self.assertEqual([item["sequenceIndex"] for item in ops], [0, 1, 2, 3])
        self.assertEqual([item["batchOperationId"] for item in ops], ["bop_0001", "bop_0002", "bop_0003", "bop_0004"])
        self.assertEqual([item["assetIndex"] for item in ops], [0, 0, 0, 1])
        self.assertEqual([item["operationIndex"] for item in ops], [0, 1, 2, 0])
        self.assertTrue(all(item["childPlanId"].startswith("plan_") for item in ops))
        self.assertTrue(all(item["childPatchDigest"].startswith("sha256:") for item in ops))
        self.assertEqual(len(self.workflow.plan_calls), 4)
        self.assertEqual(len(self.workflow.bind_calls), 2)

    def test_request_digest_deterministic_and_revision_sensitive(self) -> None:
        first = self.service.plan(**self._request_b1())
        second = self.service.plan(**self._request_b1())
        self.assertEqual(first["requestDigest"], second["requestDigest"])
        self.assertNotEqual(first["batchPlanId"], second["batchPlanId"])

        changed_revision_export = _make_revision_export(
            self.root / "ChangedRevision",
            da_revision=REV_DA_NEW,
        )
        changed_revision_workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            changed_revision_export,
        )
        changed_revision_workflow.plan_counter = 100
        # Override bound DA revision through a small subclass-like object.
        original_bind = changed_revision_workflow.bind_asset_for_batch

        def bind_with_changed_revision(asset_path: str) -> dict[str, Any]:
            binding = original_bind(asset_path)
            if asset_path == DA_ASSET:
                binding = dict(binding)
                binding["expectedRevision"] = REV_DA_NEW
            return binding

        changed_revision_workflow.bind_asset_for_batch = bind_with_changed_revision  # type: ignore[method-assign]
        changed_service = BoundedBatchService(changed_revision_workflow)
        third = changed_service.plan(**self._request_b1())
        self.assertNotEqual(first["requestDigest"], third["requestDigest"])

    def test_duplicate_asset_rejected_before_child_plans(self) -> None:
        request = {
            "assets": [
                {"assetPath": BP_ASSET, "operations": _bp_ops()},
                {"assetPath": BP_ASSET, "operations": _da_ops()},
            ]
        }
        with self.assertRaises(WorkflowError) as caught:
            self.service.plan(**request)
        self.assertEqual(caught.exception.code, "live-write-batch-duplicate-asset")
        self.assertEqual(self.workflow.plan_calls, [])
        self.assertEqual(self.workflow.discarded, [])

    def test_hard_bounds(self) -> None:
        cases = [
            ({"assets": []}, "live-write-batch-request-invalid"),
            (
                {"assets": [{"assetPath": f"/Game/UEAgentKitWriteTests/Transactions/BP_X{i}.BP_X{i}", "operations": _bp_ops()} for i in range(5)]},
                "live-write-batch-asset-count-exceeded",
            ),
            (
                {"assets": [{"assetPath": BP_ASSET, "operations": _bp_ops() + _bp_ops() + _bp_ops()}]},
                "live-write-batch-operation-count-exceeded",
            ),
        ]
        for request, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(WorkflowError) as caught:
                    self.service.plan(**request)
                self.assertEqual(caught.exception.code, expected_code)

        many_ops = _bp_ops() * 3  # 9 operations
        with self.assertRaises(WorkflowError) as caught:
            self.service.plan(assets=[{"assetPath": BP_ASSET, "operations": many_ops}])
        self.assertEqual(caught.exception.code, "live-write-batch-operation-count-exceeded")

        # 3 assets x 6 operations = 18 total, exceeding the 16-operation hard bound.
        request = {
            "assets": [
                {"assetPath": path, "operations": _bp_ops() * 2}
                for path in (BP_A, BP_B, BP_C)
            ]
        }
        with self.assertRaises(WorkflowError) as caught:
            self.service.plan(**request)
        self.assertEqual(caught.exception.code, "live-write-batch-total-operation-count-exceeded")

    def test_request_too_large(self) -> None:
        large_value = "x" * (70 * 1024)
        request = {
            "assets": [
                {
                    "assetPath": BP_ASSET,
                    "operations": [
                        {
                            "operation": "setVariableDefault",
                            "target": {"variableName": "TransactionInt"},
                            "value": large_value,
                        }
                    ],
                }
            ]
        }
        with self.assertRaises(WorkflowError) as caught:
            self.service.plan(**request)
        self.assertEqual(caught.exception.code, "live-write-batch-request-too-large")

    def test_four_eight_sixteen_boundaries_accepted(self) -> None:
        # 2 assets x 8 operations each = 16 total; both per-asset and total boundary accepted.
        eight_bp_ops = _bp_ops() * 2 + [_bp_ops()[0], _bp_ops()[1]]  # 8 Blueprint-compatible operations
        request = {
            "assets": [
                {"assetPath": BP_A, "operations": eight_bp_ops},
                {"assetPath": DA_ASSET, "operations": _da_ops() * 8},
            ]
        }
        result = self.service.plan(**request)
        self.assertEqual(result["operationCount"], 16)
        self.assertEqual(result["assetCount"], 2)
        self.assertEqual([item["sequenceIndex"] for item in result["assets"][0]["operations"]], list(range(8)))

    def test_policy_limits_win_over_hard(self) -> None:
        policy_path = _make_policy(self.root, max_assets=2, max_ops_per_asset=1)
        workflow = _FakeWorkflowService(self.work_root, policy_path, self.revision_export)
        service = BoundedBatchService(workflow)
        request = {
            "assets": [
                {"assetPath": BP_A, "operations": _bp_ops()},
                {"assetPath": BP_B, "operations": _bp_ops()},
                {"assetPath": BP_C, "operations": _bp_ops()},
            ]
        }
        with self.assertRaises(WorkflowError) as caught:
            service.plan(**request)
        self.assertEqual(caught.exception.code, "live-write-batch-plan-rejected")
        self.assertEqual(workflow.plan_calls, [])
        self.assertEqual(workflow.discarded, [])

    def test_policy_max_operations_per_asset_limits_request(self) -> None:
        policy_path = _make_policy(self.root, max_ops_per_asset=1)
        workflow = _FakeWorkflowService(self.work_root, policy_path, self.revision_export)
        service = BoundedBatchService(workflow)
        request = {
            "assets": [
                {"assetPath": BP_ASSET, "operations": _bp_ops()[:2]},
            ]
        }
        with self.assertRaises(WorkflowError) as caught:
            service.plan(**request)
        self.assertEqual(caught.exception.code, "live-write-batch-plan-rejected")
        self.assertEqual(workflow.plan_calls, [])

    def test_malformed_bp_target_rejected(self) -> None:
        request = {
            "assets": [
                {
                    "assetPath": BP_ASSET,
                    "operations": [
                        {
                            "operation": "setVariableDefault",
                            "target": {"variableName": ""},
                            "value": 42,
                        }
                    ],
                }
            ]
        }
        with self.assertRaises(WorkflowError) as caught:
            self.service.plan(**request)
        self.assertEqual(caught.exception.code, "live-write-batch-plan-rejected")
        self.assertEqual(self.workflow.plan_calls, [])

    def test_stale_revision_rejected_before_child_plans(self) -> None:
        workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
            fail_bind_asset=DA_ASSET,
            fail_bind_code="index-stale",
        )
        service = BoundedBatchService(workflow)
        with self.assertRaises(WorkflowError) as caught:
            service.plan(**self._request_b1())
        self.assertEqual(caught.exception.code, "index-stale")
        self.assertEqual(workflow.plan_calls, [])

    def test_validation_failure_leaves_no_batch_plan_directory(self) -> None:
        policy_path = _make_policy(self.root, max_assets=1)
        workflow = _FakeWorkflowService(self.work_root, policy_path, self.revision_export)
        service = BoundedBatchService(workflow)
        with self.assertRaises(WorkflowError):
            service.plan(**self._request_b1())
        self.assertFalse((self.work_root / "batch-plans").exists())

    def test_unsupported_operation_rejected(self) -> None:
        request = {
            "assets": [
                {
                    "assetPath": BP_ASSET,
                    "operations": [
                        {
                            "operation": "setMaterialInstanceScalarParameter",
                            "target": {"parameterName": "X"},
                            "value": 1.0,
                        }
                    ],
                }
            ]
        }
        with self.assertRaises(WorkflowError) as caught:
            self.service.plan(**request)
        self.assertEqual(caught.exception.code, "live-write-batch-operation-unsupported")

    def test_bind_failure_aborts_with_zero_child_plans(self) -> None:
        workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
            fail_bind_asset=DA_ASSET,
        )
        service = BoundedBatchService(workflow)
        with self.assertRaises(WorkflowError) as caught:
            service.plan(**self._request_b1())
        self.assertEqual(caught.exception.code, "asset-not-indexed")
        self.assertEqual(workflow.plan_calls, [])

    def test_child_failure_cleans_up_previous_children_and_no_payload(self) -> None:
        workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
            fail_plan_on_call=2,
        )
        service = BoundedBatchService(workflow)
        with self.assertRaises(WorkflowError) as caught:
            service.plan(**self._request_b1())
        self.assertEqual(caught.exception.code, "live-write-batch-child-plan-failed")
        self.assertEqual(len(workflow.discarded), 1)
        self.assertEqual(workflow.discarded[0], "plan_1")
        self.assertFalse((self.work_root / "batch-plans").exists())

    def test_supersession_preview(self) -> None:
        request = {
            "assets": [
                {
                    "assetPath": BP_ASSET,
                    "operations": [
                        {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 10},
                        {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 20},
                        {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 42},
                    ],
                }
            ]
        }
        result = self.service.plan(**request)
        ops = result["assets"][0]["operations"]
        self.assertEqual([op["expectedEffective"] for op in ops], [False, False, True])
        self.assertEqual(ops[0]["expectedSupersededByBatchOperationId"], ops[2]["batchOperationId"])
        self.assertEqual(ops[1]["expectedSupersededByBatchOperationId"], ops[2]["batchOperationId"])
        self.assertEqual(ops[2]["expectedSupersedesBatchOperationIds"], [ops[0]["batchOperationId"], ops[1]["batchOperationId"]])
        self.assertEqual(
            ops[0]["stableTargetKey"],
            live_write_stable_target_key("setVariableDefault", {"variableName": "TransactionInt"}),
        )

    def test_tamper_and_not_found(self) -> None:
        result = self.service.plan(**self._request_b1())
        batch_plan_id = result["batchPlanId"]
        plan_path = self.work_root / "batch-plans" / batch_plan_id / "plan.json"
        self.assertTrue(plan_path.exists())
        with self.assertRaises(WorkflowError) as caught:
            self.service.get(batch_plan_id="lwbp_unknown")
        self.assertEqual(caught.exception.code, "live-write-batch-plan-not-found")

        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        payload["state"] = "tampered"
        plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(WorkflowError) as caught:
            self.service.get(batch_plan_id=batch_plan_id)
        self.assertEqual(caught.exception.code, "live-write-batch-plan-tampered")

    def test_no_extra_side_effect_methods_called(self) -> None:
        # A successful plan must only bind assets, create child Plans, and persist.
        self.service.plan(**self._request_b1())
        self.assertEqual(
            [call["asset_path"] for call in self.workflow.plan_calls],
            [BP_ASSET, BP_ASSET, BP_ASSET, DA_ASSET],
        )
        self.assertTrue((self.work_root / "batch-plans").exists())


    def _plan_b0(self) -> dict[str, Any]:
        return self.service.plan(
            assets=[{"assetPath": BP_ASSET, "operations": _bp_ops()}],
            description="W4-2 B0 single BP",
        )

    def test_apply_success_single_bp_three_ops(self) -> None:
        plan = self._plan_b0()
        batch_plan_id = plan["batchPlanId"]
        result = self.service.apply_live_write_batch(
            batch_plan_id=batch_plan_id,
            confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
            change_set_id="cs_test",
        )
        self.assertEqual(result["state"], "applied")
        self.assertEqual(result["operationCount"], 3)
        self.assertEqual(result["appliedCount"], 3)
        self.assertEqual(result["savePerformed"], False)
        self.assertTrue(result["batchExecutionId"].startswith("lwbe_"))
        self.assertTrue(all(op["fastVerified"] for op in result["operations"]))
        self.assertEqual(len(self.workflow.apply_calls), 3)
        self.assertEqual(len(self.workflow.verify_calls), 3)
        execution = self.service._executions[batch_plan_id].payload
        self.assertEqual(
            [op["previousTransactionId"] for op in execution["operations"]],
            ["", "tx_1", "tx_2"],
        )
        self.assertEqual(
            [op["transactionId"] for op in execution["operations"]],
            ["tx_1", "tx_2", "tx_3"],
        )

    def test_apply_same_target_repeated_writes_retained(self) -> None:
        plan = self.service.plan(
            assets=[
                {
                    "assetPath": BP_ASSET,
                    "operations": [
                        {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 10},
                        {"operation": "setVariableDefault", "target": {"variableName": "TransactionInt"}, "value": 20},
                    ],
                }
            ],
            description="W4-2 same-target",
        )
        batch_plan_id = plan["batchPlanId"]
        result = self.service.apply_live_write_batch(
            batch_plan_id=batch_plan_id,
            confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
            change_set_id="cs_test",
        )
        self.assertEqual(result["state"], "applied")
        self.assertEqual(result["operationCount"], 2)
        self.assertEqual(len(result["operations"]), 2)
        self.assertTrue(all(op["fastVerified"] for op in result["operations"]))
        self.assertEqual(len(self.workflow.apply_calls), 2)
        self.assertEqual(len(self.workflow.verify_calls), 2)

    def test_apply_bad_confirmation_zero_mutation(self) -> None:
        plan = self._plan_b0()
        batch_plan_id = plan["batchPlanId"]
        with self.assertRaises(WorkflowError) as caught:
            self.service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation="wrong",
                change_set_id="cs_test",
            )
        self.assertEqual(
            caught.exception.code,
            "live-write-batch-apply-confirmation-required",
        )
        self.assertEqual(self.workflow.apply_calls, [])

    def test_apply_multi_asset_success_passes_and_resets_previous_transaction(self) -> None:
        plan = self.service.plan(**self._request_b1())
        batch_plan_id = plan["batchPlanId"]
        result = self.service.apply_live_write_batch(
            batch_plan_id=batch_plan_id,
            confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
            change_set_id="cs_test",
        )
        self.assertEqual(result["state"], "applied")
        self.assertEqual(result["operationCount"], 4)
        self.assertEqual(result["appliedCount"], 4)
        self.assertEqual(result["assetOrder"], [BP_ASSET, DA_ASSET])
        execution = self.service._executions[batch_plan_id].payload
        self.assertEqual(
            [op["previousTransactionId"] for op in execution["operations"]],
            ["", "tx_1", "tx_2", ""],
        )
        self.assertEqual(
            [op["assetIndex"] for op in execution["operations"]],
            [0, 0, 0, 1],
        )
        self.assertEqual(len(self.workflow.apply_calls), 4)
        self.assertEqual(len(self.workflow.verify_calls), 4)

    def test_apply_replay_rejected(self) -> None:
        plan = self._plan_b0()
        batch_plan_id = plan["batchPlanId"]
        self.service.apply_live_write_batch(
            batch_plan_id=batch_plan_id,
            confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
            change_set_id="cs_test",
        )
        with self.assertRaises(WorkflowError) as caught:
            self.service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                change_set_id="cs_test",
            )
        self.assertEqual(caught.exception.code, "live-write-batch-apply-already-started")

    def test_apply_op2_failure_partial_boundary(self) -> None:
        workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
            fail_apply_on_call=2,
        )
        service = BoundedBatchService(workflow)
        plan = service.plan(
            assets=[{"assetPath": BP_ASSET, "operations": _bp_ops()}],
            description="W4-2 partial",
        )
        batch_plan_id = plan["batchPlanId"]
        with self.assertRaises(WorkflowError) as caught:
            service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                change_set_id="cs_test",
            )
        self.assertEqual(caught.exception.code, "live-write-batch-apply-failed")
        execution = service._executions[batch_plan_id].payload
        self.assertEqual(execution["state"], "partially_applied")
        self.assertEqual(execution["lastSuccessfulOperation"], "bop_0001")
        self.assertEqual(execution["failedOperation"], "bop_0002")
        self.assertEqual(execution["notStarted"], ["bop_0003"])
        self.assertEqual(execution["recoveryOrder"], ["bop_0001"])
        self.assertEqual(len(workflow.apply_calls), 2)

    def test_apply_fast_verify_failure_partial_boundary(self) -> None:
        workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
            fail_fast_on_call=2,
        )
        service = BoundedBatchService(workflow)
        plan = service.plan(
            assets=[{"assetPath": BP_ASSET, "operations": _bp_ops()}],
            description="W4-2 fast partial",
        )
        batch_plan_id = plan["batchPlanId"]
        with self.assertRaises(WorkflowError) as caught:
            service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                change_set_id="cs_test",
            )
        self.assertEqual(caught.exception.code, "live-write-batch-apply-fast-verify-failed")
        execution = service._executions[batch_plan_id].payload
        self.assertEqual(execution["state"], "partially_applied")
        self.assertEqual(execution["lastSuccessfulOperation"], "bop_0001")
        self.assertEqual(execution["failedOperation"], "bop_0002")
        self.assertEqual(execution["notStarted"], ["bop_0003"])
        self.assertEqual(execution["recoveryOrder"], ["bop_0002", "bop_0001"])

    def test_apply_later_asset_failure_partial_boundary(self) -> None:
        workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
            fail_apply_on_call=4,
        )
        service = BoundedBatchService(workflow)
        plan = service.plan(**self._request_b1())
        batch_plan_id = plan["batchPlanId"]
        with self.assertRaises(WorkflowError) as caught:
            service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                change_set_id="cs_test",
            )
        self.assertEqual(caught.exception.code, "live-write-batch-apply-failed")
        execution = service._executions[batch_plan_id].payload
        self.assertEqual(execution["state"], "partially_applied")
        self.assertEqual(execution["lastSuccessfulOperation"], "bop_0003")
        self.assertEqual(execution["failedOperation"], "bop_0004")
        self.assertEqual(execution["notStarted"], [])
        self.assertEqual(execution["recoveryOrder"], ["bop_0003", "bop_0002", "bop_0001"])
        self.assertEqual(len(workflow.apply_calls), 4)

    def test_apply_later_asset_fast_verify_failure_partial_boundary(self) -> None:
        workflow = _FakeWorkflowService(
            self.work_root,
            self.policy_path,
            self.revision_export,
            fail_fast_on_call=4,
        )
        service = BoundedBatchService(workflow)
        plan = service.plan(**self._request_b1())
        batch_plan_id = plan["batchPlanId"]
        with self.assertRaises(WorkflowError) as caught:
            service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                change_set_id="cs_test",
            )
        self.assertEqual(caught.exception.code, "live-write-batch-apply-fast-verify-failed")
        execution = service._executions[batch_plan_id].payload
        self.assertEqual(execution["state"], "partially_applied")
        self.assertEqual(execution["lastSuccessfulOperation"], "bop_0003")
        self.assertEqual(execution["failedOperation"], "bop_0004")
        self.assertEqual(execution["notStarted"], [])
        self.assertEqual(
            execution["recoveryOrder"],
            ["bop_0004", "bop_0003", "bop_0002", "bop_0001"],
        )

    def test_apply_tampered_plan_zero_mutation(self) -> None:
        plan = self._plan_b0()
        batch_plan_id = plan["batchPlanId"]
        plan_path = self.work_root / "batch-plans" / batch_plan_id / "plan.json"
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
        payload["state"] = "tampered"
        plan_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with self.assertRaises(WorkflowError) as caught:
            self.service.apply_live_write_batch(
                batch_plan_id=batch_plan_id,
                confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                change_set_id="cs_test",
            )
        self.assertEqual(caught.exception.code, "live-write-batch-plan-tampered")
        self.assertEqual(self.workflow.apply_calls, [])

    def test_apply_persistence_failure_stops_after_mutation(self) -> None:
        from unittest.mock import patch

        plan = self._plan_b0()
        batch_plan_id = plan["batchPlanId"]
        original_persist = self.service._persist_execution
        persist_count = 0

        def failing_persist(payload: dict[str, Any]) -> Any:
            nonlocal persist_count
            persist_count += 1
            if persist_count == 2:
                raise RuntimeError("persist failed")
            return original_persist(payload)

        with patch.object(self.service, "_persist_execution", side_effect=failing_persist):
            with self.assertRaises(RuntimeError):
                self.service.apply_live_write_batch(
                    batch_plan_id=batch_plan_id,
                    confirmation=f"APPLY LIVE WRITE BATCH {batch_plan_id}",
                    change_set_id="cs_test",
                )
        self.assertEqual(len(self.workflow.apply_calls), 1)
        self.assertEqual(len(self.workflow.verify_calls), 1)


if __name__ == "__main__":
    unittest.main()
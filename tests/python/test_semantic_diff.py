from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.semantic_diff import (  # noqa: E402
    SemanticAssetEvidence,
    SemanticOperationEvidence,
    analyze_semantic_evidence,
    normalize_semantic_value,
    semantic_equal,
)
from ue_agent_kit.semantic_diff_workflow import (  # noqa: E402
    SemanticDiffEvidenceError,
    analyze_workflow_semantic_diff,
)


ASSET_A = "/Game/Tests/DA_A.DA_A"
ASSET_B = "/Game/Tests/DA_B.DA_B"
BEFORE_REVISION = "sha256:" + "1" * 64
AFTER_REVISION = "sha256:" + "2" * 64


def operation(
    operation_name: str = "setAssetProperty",
    *,
    operation_id: str = "op-1",
    asset_path: str = ASSET_A,
    target: dict[str, object] | None = None,
    expected: object = 2,
    before: object = 1,
    actual: object = 2,
    expected_available: bool = True,
    before_available: bool = True,
    actual_available: bool = True,
    value_kind: str = "scalar",
    details: dict[str, object] | None = None,
) -> SemanticOperationEvidence:
    return SemanticOperationEvidence(
        operation_id=operation_id,
        asset_path=asset_path,
        operation=operation_name,
        target=target or {"propertyPath": "Damage"},
        expected_value=expected,
        before_value=before,
        actual_value=actual,
        stage="verified",
        source="test-evidence",
        expected_available=expected_available,
        before_available=before_available,
        actual_available=actual_available,
        before_revision=BEFORE_REVISION,
        after_revision=AFTER_REVISION,
        stage_evidence_revision=AFTER_REVISION,
        asset_class="/Script/Engine.DataAsset",
        value_kind=value_kind,
        details=details or {},
    )


def asset(
    *operations: SemanticOperationEvidence,
    asset_path: str = ASSET_A,
    actual_only: tuple[SemanticOperationEvidence, ...] = (),
    gaps: tuple[dict[str, object], ...] = (),
) -> SemanticAssetEvidence:
    return SemanticAssetEvidence(
        asset_path=asset_path,
        asset_class="/Script/Engine.DataAsset",
        before_revision=BEFORE_REVISION,
        after_revision=AFTER_REVISION,
        stage_evidence_revision=AFTER_REVISION,
        operations=tuple(operations),
        unchanged_critical_fields=(
            {
                "invariantId": f"inv-{asset_path}",
                "assetPath": asset_path,
                "semanticPath": "Asset.Class",
                "status": "unchanged",
                "details": {"class": "/Script/Engine.DataAsset"},
            },
        ),
        analysis_gaps=gaps,
        actual_only=actual_only,
    )


def analyze(
    assets: list[SemanticAssetEvidence],
    **kwargs: object,
) -> dict[str, object]:
    arguments: dict[str, object] = {
        "change_set": {"changeSetId": "cs_test", "taskId": "task_test", "status": "verified"},
        "requested_stage": "verified",
        "selected_stage": "verified",
        "selection_reason": "test evidence",
        "sources": [{"kind": "verified-fixture", "id": "fixture"}],
        "assets": assets,
        "max_output_tokens": 8192,
    }
    arguments.update(kwargs)
    return analyze_semantic_evidence(**arguments)  # type: ignore[arg-type]


def write_canonical_export(root: Path, canonical: dict[str, object]) -> None:
    canonical_directory = root / "canonical"
    canonical_directory.mkdir(parents=True)
    canonical_path = canonical_directory / "asset.json"
    canonical_path.write_text(json.dumps(canonical), encoding="utf-8")
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "projectName": canonical["projectName"],
                "assets": [
                    {
                        "assetPath": canonical["assetPath"],
                        "success": True,
                        "jsonPath": "canonical/asset.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def canonical_asset(
    *,
    asset_path: str = ASSET_A,
    revision: str,
    asset_class: str,
    asset_details: dict[str, object] | None = None,
    **top_level: object,
) -> dict[str, object]:
    return {
        "projectName": "Fixture",
        "assetPath": asset_path,
        "assetClass": asset_class,
        "revision": {"value": revision},
        "assetDetails": asset_details or {},
        **top_level,
    }


def write_plan(root: Path, plan_id: str, asset_path: str, asset_class: str, operations: list[dict[str, object]]) -> None:
    plan_directory = root / "plans" / plan_id
    plan_directory.mkdir(parents=True)
    (plan_directory / "patch.json").write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "assetPath": asset_path,
                        "expectedRevision": BEFORE_REVISION,
                        "expectedAssetClass": asset_class,
                        "operations": operations,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )


class SemanticDiffCoreTests(unittest.TestCase):
    def test_t1_single_expected_equals_actual(self) -> None:
        result = analyze([asset(operation())])
        self.assertTrue(result["ok"])
        self.assertTrue(result["readOnly"])
        self.assertEqual(result["summary"]["matchedCount"], 1)
        self.assertEqual(result["summary"]["unexpectedCount"], 0)
        self.assertEqual(result["summary"]["missingExpectedCount"], 0)

    def test_t2_expected_change_missing(self) -> None:
        result = analyze([asset(operation(actual=1))])
        self.assertEqual(result["summary"]["actualCount"], 0)
        self.assertEqual(result["summary"]["missingExpectedCount"], 1)
        self.assertIn("semantic-diff-missing-expected-change", {risk["code"] for risk in result["risks"]})

    def test_t3_unexpected_actual_change(self) -> None:
        extra = operation(
            operation_id="snapshot-extra",
            target={"propertyPath": "Unrequested"},
            expected=None,
            before=False,
            actual=True,
            expected_available=False,
        )
        result = analyze([asset(operation(), actual_only=(extra,))])
        self.assertEqual(result["summary"]["unexpectedCount"], 1)
        self.assertEqual(result["assets"][0]["unexpectedChanges"][0]["semanticPath"], "DataAsset.Property:Unrequested")

    def test_t4_expected_no_op_is_not_missing(self) -> None:
        result = analyze([asset(operation(expected=1, before=1, actual=1))])
        matched = result["assets"][0]["matchedChanges"]
        self.assertEqual(matched[0]["status"], "matched-expected-no-op")
        self.assertEqual(result["summary"]["actualCount"], 0)

    def test_t5_multiple_operations_same_asset(self) -> None:
        result = analyze(
            [
                asset(
                    operation(operation_id="op-a", target={"propertyPath": "Damage"}),
                    operation(operation_id="op-b", target={"propertyPath": "Speed"}, expected=4, before=3, actual=4),
                )
            ]
        )
        self.assertEqual(result["summary"]["matchedCount"], 2)
        self.assertEqual(result["summary"]["returnedAssetCount"], 1)

    def test_t6_same_semantic_path_collapses_operation_chain(self) -> None:
        result = analyze(
            [
                asset(
                    operation(operation_id="op-a", expected=2, before=1, actual=2),
                    operation(operation_id="op-b", expected=3, before=2, actual=3),
                )
            ]
        )
        target = result["assets"][0]
        self.assertEqual(len(target["expectedChanges"]), 1)
        self.assertEqual(target["expectedChanges"][0]["beforeValue"], 1)
        self.assertEqual(target["expectedChanges"][0]["expectedValue"], 3)
        self.assertEqual(len(target["expectedChanges"][0]["details"]["operationChain"]), 2)

    def test_t7_multi_asset_change_set(self) -> None:
        result = analyze(
            [
                asset(operation(asset_path=ASSET_A), asset_path=ASSET_A),
                asset(operation(asset_path=ASSET_B), asset_path=ASSET_B),
            ]
        )
        self.assertEqual(result["summary"]["returnedAssetCount"], 2)
        self.assertEqual(result["summary"]["matchedCount"], 2)
        self.assertEqual(result["changeSet"]["affectedAssets"], [ASSET_A, ASSET_B])

    def test_t8_exact_asset_filter_only_changes_returned_view(self) -> None:
        result = analyze(
            [
                asset(operation(asset_path=ASSET_A), asset_path=ASSET_A),
                asset(operation(asset_path=ASSET_B), asset_path=ASSET_B),
            ],
            asset_paths=[ASSET_B],
            total_asset_count=2,
        )
        self.assertEqual(result["summary"]["totalAssetCount"], 2)
        self.assertEqual(result["summary"]["returnedAssetCount"], 1)
        self.assertEqual(result["assets"][0]["assetPath"], ASSET_B)
        self.assertTrue(result["summary"]["filtered"])

    def test_t9_repeated_output_is_deterministic(self) -> None:
        evidence = [asset(operation(operation_id="stable"))]
        self.assertEqual(analyze(evidence), analyze(evidence))

    def test_t10_low_budget_and_change_limit_are_explicit(self) -> None:
        operations = tuple(
            operation(
                operation_id=f"op-{index}",
                target={"propertyPath": f"Field{index:03d}"},
                expected=index + 1,
                before=index,
                actual=index + 1,
                details={"verbose": "x" * 500},
            )
            for index in range(12)
        )
        result = analyze([asset(*operations)], max_changes=2, max_output_tokens=256)
        self.assertTrue(result["outputBudget"]["truncated"])
        self.assertIn("semantic-diff-truncated", {gap["code"] for gap in result["analysisGaps"]})

    def test_request_bounds_reject_invalid_filters_and_limits(self) -> None:
        evidence = [asset(operation())]
        invalid_arguments = (
            {"asset_paths": [f"/Game/Tests/DA_{index}.DA_{index}" for index in range(9)]},
            {"asset_paths": [ASSET_A, ASSET_A]},
            {"asset_paths": ["/Engine/Tests/DA_A.DA_A"]},
            {"asset_paths": ["/Game/Tests/DA_A"]},
            {"max_changes": 0},
            {"max_changes": 129},
            {"max_output_tokens": 255},
            {"max_output_tokens": 32769},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    analyze(evidence, **arguments)

    def test_t11_unsupported_operation_is_a_gap(self) -> None:
        result = analyze([asset(operation("arbitraryUObjectWrite"))])
        self.assertEqual(result["summary"]["expectedCount"], 0)
        self.assertIn("unsupported-operation", {gap["code"] for gap in result["analysisGaps"]})

    def test_t13_revision_mismatch_and_stale_gap_are_visible(self) -> None:
        stale = {
            "gapId": "gap-stale",
            "code": "revision-evidence-unavailable",
            "assetPath": ASSET_A,
            "operationId": "",
            "message": "No live after revision.",
        }
        result = analyze([asset(operation(), gaps=(stale,))])
        self.assertTrue(result["assets"][0]["revisionChanged"])
        self.assertEqual(result["assets"][0]["stageEvidenceRevision"], AFTER_REVISION)
        self.assertIn("semantic-diff-evidence-stale", {risk["code"] for risk in result["risks"]})

    def test_t14_source_is_explicit_change_set_without_private_discovery(self) -> None:
        result = analyze([asset(operation())])
        self.assertEqual(result["source"], {"kind": "explicit-change-set", "privateDiscovery": False})
        self.assertEqual(result["request"]["changeSetId"], "cs_test")

    def test_t15_analysis_is_read_only_and_does_not_mutate_inputs(self) -> None:
        evidence = [asset(operation(details={"nested": {"items": [3, 2, 1]}}))]
        original = copy.deepcopy(evidence)
        result = analyze(evidence)
        self.assertEqual(evidence, original)
        self.assertTrue(result["readOnly"])

    def test_revision_mismatch_is_automatically_reported_as_stale(self) -> None:
        evidence = replace(asset(operation()), stage_evidence_revision="sha256:" + "3" * 64)

        result = analyze([evidence])

        self.assertIn("semantic-diff-evidence-stale", {risk["code"] for risk in result["risks"]})
        self.assertTrue(
            {
                "semantic-diff-evidence-stale",
                "revision-evidence-mismatch",
            }.intersection({gap["code"] for gap in result["analysisGaps"]})
        )

    def test_more_than_eight_assets_reports_explicit_truncation(self) -> None:
        evidence = []
        for index in range(9):
            asset_path = f"/Game/Tests/DA_{index}.DA_{index}"
            evidence.append(asset(operation(asset_path=asset_path), asset_path=asset_path))

        result = analyze(evidence, total_asset_count=len(evidence))

        self.assertEqual(result["summary"]["returnedAssetCount"], 8)
        self.assertTrue(result["outputBudget"]["truncated"])
        self.assertIn("semantic-diff-truncated", {gap["code"] for gap in result["analysisGaps"]})

    def test_asset_filter_preserves_complete_change_set_affected_assets(self) -> None:
        result = analyze(
            [
                asset(operation(asset_path=ASSET_A), asset_path=ASSET_A),
                asset(operation(asset_path=ASSET_B), asset_path=ASSET_B),
            ],
            asset_paths=[ASSET_B],
            total_asset_count=2,
        )

        self.assertEqual(result["changeSet"]["affectedAssets"], [ASSET_A, ASSET_B])

    def test_unchanged_details_are_not_removed_when_response_is_within_budget(self) -> None:
        result = analyze([asset(operation())], max_output_tokens=8192)

        unchanged = result["assets"][0]["unchangedCriticalFields"][0]
        self.assertIn("details", unchanged)
        self.assertEqual(unchanged["details"], {"class": "/Script/Engine.DataAsset"})
        self.assertFalse(result["outputBudget"]["truncated"])


class SemanticDiffDomainMatrixTests(unittest.TestCase):
    def test_data_asset_real_structured_writer_wrappers_match(self) -> None:
        cases = [
            (
                "StructValue",
                {"valueType": "Struct", "fields": {"Count": 7, "Label": "Updated"}},
                {"valueType": "Struct", "fields": {"Label": "Updated", "Count": 7}},
            ),
            (
                "SetValue",
                {"valueType": "Set", "items": ["Alpha", "Gamma"]},
                {"valueType": "Set", "items": ["Gamma", "Alpha"]},
            ),
            (
                "MapValue",
                {
                    "valueType": "Map",
                    "entries": [{"key": "A", "value": 1}, {"key": "B", "value": 2}],
                },
                {
                    "valueType": "Map",
                    "entries": [{"value": 2, "key": "B"}, {"value": 1, "key": "A"}],
                },
            ),
        ]
        for property_name, expected, actual in cases:
            with self.subTest(property_name=property_name):
                before = copy.deepcopy(expected)
                if property_name == "StructValue":
                    before["fields"]["Count"] = 1
                elif property_name == "SetValue":
                    before["items"] = ["Alpha"]
                else:
                    before["entries"] = [{"key": "A", "value": 0}]
                result = analyze(
                    [
                        asset(
                            operation(
                                "setAssetStructuredProperty",
                                target={"propertyPath": property_name},
                                expected=expected,
                                before=before,
                                actual=actual,
                                value_kind=str(expected["valueType"]),
                            )
                        )
                    ]
                )
                self.assertEqual(result["summary"]["matchedCount"], 1)
                self.assertEqual(result["summary"]["unexpectedCount"], 0)
                self.assertEqual(result["summary"]["missingExpectedCount"], 0)

    def test_data_asset_scalar_ref_struct_array_set_and_map_normalization(self) -> None:
        cases = [
            ("scalar", 1.25, 1.25),
            (
                "object-reference",
                {"referenceType": "object", "path": "/Game/T/T.T"},
                {"path": "/Game/T/T.T", "referenceType": "object"},
            ),
            ("struct", {"B": 2, "A": {"Y": 2, "X": 1}}, {"A": {"X": 1, "Y": 2}, "B": 2}),
            ("array", [1, 2, 3], [1, 2, 3]),
            ("set", [3, 1, 2], [2, 3, 1]),
            (
                "map",
                [{"key": "B", "value": 2}, {"key": "A", "value": 1}],
                [{"value": 1, "key": "A"}, {"value": 2, "key": "B"}],
            ),
        ]
        for value_kind, left, right in cases:
            with self.subTest(value_kind=value_kind):
                self.assertTrue(semantic_equal(left, right, value_type=value_kind))
        self.assertFalse(semantic_equal([1, 2], [2, 1], value_type="array"))
        wrapped_set = {"valueType": "set", "value": ["B", "A"]}
        self.assertEqual(normalize_semantic_value(wrapped_set)["value"], ["A", "B"])

    def test_data_asset_operation_matrix(self) -> None:
        cases = [
            ("setAssetProperty", 2, 1, 2),
            (
                "setAssetReferenceProperty",
                {"referenceType": "Object", "path": "/Game/Items/DA_New.DA_New"},
                "/Game/Items/DA_Old.DA_Old",
                "/Game/Items/DA_New.DA_New",
            ),
            (
                "setAssetStructuredProperty",
                {"valueType": "set", "value": ["B", "A"]},
                ["Old"],
                ["A", "B"],
            ),
        ]
        for name, expected, before, actual in cases:
            with self.subTest(operation=name):
                result = analyze([asset(operation(name, expected=expected, before=before, actual=actual))])
                entry = result["assets"][0]["matchedChanges"][0]
                self.assertEqual(entry["domain"], "data-asset-property")
                self.assertEqual(entry["semanticPath"], "DataAsset.Property:Damage")

    def test_data_table_cell_row_fields_add_remove_and_rename(self) -> None:
        cases = [
            ("setDataTableCell", {"rowName": "Rifle", "fieldName": "Damage"}, 30, 20, 30, "value-changed"),
            (
                "setDataTableRowFields",
                {"rowName": "Rifle"},
                {"Damage": 30},
                {"Damage": 20},
                {"Damage": 30},
                "value-changed",
            ),
            ("addDataTableRow", {"rowName": "Rifle"}, {"Damage": 30}, None, {"Damage": 30}, "row-added"),
            ("removeDataTableRow", {"rowName": "Rifle"}, None, {"Damage": 30}, None, "row-removed"),
            (
                "renameDataTableRow",
                {"rowName": "Rifle", "newRowName": "Carbine"},
                object(),
                {"from": "Rifle", "to": "Rifle"},
                {"from": "Rifle", "to": "Carbine"},
                "row-renamed",
            ),
        ]
        for name, target, expected, before, actual, kind in cases:
            with self.subTest(operation=name):
                result = analyze(
                    [asset(operation(name, target=target, expected=expected, before=before, actual=actual))]
                )
                entry = result["assets"][0]["matchedChanges"][0]
                self.assertEqual(entry["domain"], "data-table")
                self.assertEqual(entry["changeKind"], kind)
        rename = analyze(
            [
                asset(
                    operation(
                        "renameDataTableRow",
                        target={"rowName": "Old", "newRowName": "New"},
                        expected=None,
                        before={"from": "Old", "to": "Old"},
                        actual={"from": "Old", "to": "New"},
                    )
                )
            ]
        )
        self.assertEqual(rename["assets"][0]["expectedChanges"][0]["semanticPath"], "DataTable.Row:Old->Row:New")

    def test_material_instance_four_categories_and_override_identity(self) -> None:
        cases = [
            ("setMaterialInstanceScalarParameter", "Roughness", 0.7),
            ("setMaterialInstanceVectorParameter", "Tint", {"r": 1, "g": 0, "b": 0, "a": 1}),
            ("setMaterialInstanceTextureParameter", "BaseColor", "/Game/T/T.T"),
            ("setMaterialInstanceStaticSwitchParameter", "UseDetail", True),
        ]
        for name, parameter, value in cases:
            with self.subTest(operation=name):
                expected = {"override": True, "value": value}
                result = analyze(
                    [
                        asset(
                            operation(
                                name,
                                target={"parameterName": parameter},
                                expected=expected,
                                before={"override": False, "value": None},
                                actual=expected,
                                value_kind="material-parameter-state",
                            )
                        )
                    ]
                )
                entry = result["assets"][0]["matchedChanges"][0]
                self.assertEqual(entry["domain"], "material-instance")
                self.assertEqual(entry["changeKind"], "override-added")
        changed = analyze(
            [
                asset(
                    operation(
                        "setMaterialInstanceScalarParameter",
                        target={"parameterName": "Roughness"},
                        expected={"override": True, "value": 0.7},
                        before={"override": True, "value": 0.4},
                        actual={"override": True, "value": 0.7},
                    )
                )
            ]
        )
        self.assertEqual(changed["assets"][0]["matchedChanges"][0]["changeKind"], "override-changed")

    def test_blueprint_property_component_and_pin_paths(self) -> None:
        cases = [
            ("setVariableDefault", {"variableName": "MaxSpeed"}, "Blueprint.Defaults.MaxSpeed"),
            (
                "setComponentProperty",
                {"componentName": "CharacterMovement", "propertyPath": "MaxWalkSpeed"},
                "Blueprint.Component:CharacterMovement.MaxWalkSpeed",
            ),
            (
                "setPinDefault",
                {"graphGuid": "graph-guid", "nodeGuid": "node-guid", "pinName": "Speed"},
                "Blueprint.Graph:graph-guid.Node:node-guid.Pin:Speed.DefaultValue",
            ),
        ]
        for name, target, semantic_path in cases:
            with self.subTest(operation=name):
                result = analyze([asset(operation(name, target=target, expected="2", before="1", actual=2.0))])
                entry = result["assets"][0]["matchedChanges"][0]
                self.assertEqual(entry["domain"], "blueprint-narrow-write")
                self.assertEqual(entry["semanticPath"], semantic_path)


class _FakeWorkflow:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._live_applies: dict[str, object] = {}
        self._applies: dict[str, object] = {}
        self.project_name = "Fixture"
        self.reconcile_calls = 0
        self.record = SimpleNamespace(
            operations=[
                SimpleNamespace(
                    receipt="live_missing",
                    plan_id="plan_missing",
                    asset_path=ASSET_A,
                    operation="setAssetProperty",
                    transaction_id="tx",
                    save_receipt="",
                )
            ]
        )

    def _resolve_change_set(self, change_set_id: str) -> object:
        if change_set_id != "cs_explicit":
            raise AssertionError("private discovery attempted")
        return self.record

    def _reconcile_change_set(self, record: object, *, persist: bool) -> None:
        self.assert_same_record = record is self.record
        if persist:
            raise AssertionError("read-only analysis attempted persistence")
        self.reconcile_calls += 1

    @staticmethod
    def _safe_work_path(*parts: str) -> Path:
        return Path("__missing_semantic_diff_evidence__").joinpath(*parts)


class SemanticDiffWorkflowContractTests(unittest.TestCase):
    def test_t12_explicit_requested_stage_unavailable_is_structured(self) -> None:
        service = _FakeWorkflow()
        with self.assertRaises(SemanticDiffEvidenceError) as caught:
            analyze_workflow_semantic_diff(service, "cs_explicit", stage="verified")
        self.assertEqual(caught.exception.code, "semantic-diff-stage-unavailable")
        self.assertEqual(caught.exception.details["requestedStage"], "verified")
        self.assertEqual(caught.exception.details["availableStages"], [])

    def test_workflow_stage_probe_is_read_only_and_explicit(self) -> None:
        service = _FakeWorkflow()
        original = copy.deepcopy(service.record.operations)
        with self.assertRaises(SemanticDiffEvidenceError):
            analyze_workflow_semantic_diff(service, "cs_explicit", stage="persisted")
        self.assertEqual(service.record.operations, original)
        self.assertEqual(service.reconcile_calls, 1)
        self.assertFalse(service.assert_same_record)

    def test_live_workflow_collects_plan_intent_and_transaction_actual(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_id = "plan_explicit"
            plan_directory = root / "plans" / plan_id
            plan_directory.mkdir(parents=True)
            (plan_directory / "patch.json").write_text(
                json.dumps(
                    {
                        "assets": [
                            {
                                "assetPath": ASSET_A,
                                "expectedRevision": BEFORE_REVISION,
                                "expectedAssetClass": "/Script/Engine.DataAsset",
                                "operations": [
                                    {
                                        "operationId": "set-damage",
                                        "operation": "setAssetProperty",
                                        "target": {"propertyPath": "Damage"},
                                        "value": 30,
                                    }
                                ],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            service = _FakeWorkflow()
            service.config = SimpleNamespace(revision_export=root / "revision")
            service.record = SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        receipt="live_explicit",
                        plan_id=plan_id,
                        asset_path=ASSET_A,
                        operation="setAssetProperty",
                        transaction_id="tx-explicit",
                        save_receipt="",
                    )
                ]
            )
            service._live_applies["live_explicit"] = SimpleNamespace(
                target={"propertyPath": "Damage"},
                before_value=20,
                after_value=30,
                operation="setAssetProperty",
                value_kind="scalar",
            )
            service._plan_directory = lambda value: root / "plans" / value
            service._safe_work_path = lambda *parts: root / "work" / Path(*parts)
            service.get_change_set = lambda value: {
                "changeSetId": value,
                "taskId": "task_explicit",
                "status": "applied",
            }

            result = analyze_workflow_semantic_diff(service, "cs_explicit", stage="live")

            self.assertTrue(result["ok"])
            self.assertTrue(result["readOnly"])
            self.assertEqual(result["evidenceStage"]["selected"], "live")
            self.assertEqual(result["summary"]["matchedCount"], 1)
            self.assertEqual(result["assets"][0]["beforeRevision"], BEFORE_REVISION)
            self.assertEqual(result["assets"][0]["afterRevision"], "")
            self.assertIn("revision-evidence-unavailable", {gap["code"] for gap in result["analysisGaps"]})
            self.assertEqual(service.reconcile_calls, 1)

    def test_commandlet_verified_stage_uses_canonical_actual_not_commit_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_id = "plan_commandlet"
            operation_spec = {
                "operationId": "set-damage",
                "operation": "setAssetProperty",
                "target": {"propertyPath": "Damage"},
                "value": 30,
            }
            write_plan(root, plan_id, ASSET_A, "/Script/Engine.DataAsset", [operation_spec])
            revision_root = root / "revision"
            write_canonical_export(
                revision_root,
                canonical_asset(
                    revision=BEFORE_REVISION,
                    asset_class="/Script/Engine.DataAsset",
                    asset_details={"properties": [{"name": "Damage", "value": 10, "valueType": "int32"}]},
                ),
            )
            verify_root = root / "work" / "verify" / "apply_explicit"
            write_canonical_export(
                verify_root,
                canonical_asset(
                    revision=AFTER_REVISION,
                    asset_class="/Script/Engine.DataAsset",
                    asset_details={"properties": [{"name": "Damage", "value": 20, "valueType": "int32"}]},
                ),
            )
            commit_report = {
                "assetPath": ASSET_A,
                "assetClass": "/Script/Engine.DataAsset",
                "beforeRevision": BEFORE_REVISION,
                "afterRevision": AFTER_REVISION,
                "operations": [{**operation_spec, "beforeValue": 10, "afterValue": 30}],
            }
            service = _FakeWorkflow()
            service.config = SimpleNamespace(revision_export=revision_root)
            service.record = SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        receipt="apply_explicit",
                        plan_id=plan_id,
                        asset_path=ASSET_A,
                        operation="setAssetProperty",
                        transaction_id="",
                        save_receipt="",
                    )
                ]
            )
            service._applies["apply_explicit"] = SimpleNamespace(report=commit_report)
            service._plan_directory = lambda value: root / "plans" / value
            service._safe_work_path = lambda *parts: root / "work" / Path(*parts)
            service.get_change_set = lambda value: {
                "changeSetId": value,
                "taskId": "task_commandlet",
                "status": "verified",
            }

            result = analyze_workflow_semantic_diff(service, "cs_explicit", stage="verified")

            self.assertEqual(result["summary"]["matchedCount"], 0)
            self.assertEqual(result["summary"]["missingExpectedCount"], 1)
            self.assertEqual(result["assets"][0]["missingExpectedChanges"][0]["observedValue"], 20)

    def test_blueprint_verified_stage_detects_unexpected_state_or_reports_snapshot_gap(self) -> None:
        blueprint_class = "/Script/Engine.Blueprint"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_id = "plan_blueprint"
            operation_spec = {
                "operationId": "set-int",
                "operation": "setVariableDefault",
                "target": {"variableName": "TransactionInt"},
                "value": 42,
            }
            write_plan(root, plan_id, ASSET_A, blueprint_class, [operation_spec])
            revision_root = root / "revision"
            write_canonical_export(
                revision_root,
                canonical_asset(
                    revision=BEFORE_REVISION,
                    asset_class=blueprint_class,
                    variables=[
                        {"name": "TransactionInt", "defaultValue": "10"},
                        {"name": "UnexpectedFlag", "defaultValue": "False"},
                    ],
                ),
            )
            verify_root = root / "work" / "verify" / "apply_blueprint"
            write_canonical_export(
                verify_root,
                canonical_asset(
                    revision=AFTER_REVISION,
                    asset_class=blueprint_class,
                    variables=[
                        {"name": "TransactionInt", "defaultValue": "42"},
                        {"name": "UnexpectedFlag", "defaultValue": "True"},
                    ],
                ),
            )
            report = {
                "assetPath": ASSET_A,
                "assetClass": blueprint_class,
                "beforeRevision": BEFORE_REVISION,
                "afterRevision": AFTER_REVISION,
                "operations": [{**operation_spec, "beforeValue": "10", "afterValue": "42"}],
            }
            service = _FakeWorkflow()
            service.config = SimpleNamespace(revision_export=revision_root)
            service.record = SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        receipt="apply_blueprint",
                        plan_id=plan_id,
                        asset_path=ASSET_A,
                        operation="setVariableDefault",
                        transaction_id="",
                        save_receipt="",
                    )
                ]
            )
            service._applies["apply_blueprint"] = SimpleNamespace(report=report)
            service._plan_directory = lambda value: root / "plans" / value
            service._safe_work_path = lambda *parts: root / "work" / Path(*parts)
            service.get_change_set = lambda value: {
                "changeSetId": value,
                "taskId": "task_blueprint",
                "status": "verified",
            }

            result = analyze_workflow_semantic_diff(service, "cs_explicit", stage="verified")

            gap_codes = {gap["code"] for gap in result["analysisGaps"]}
            unexpected_paths = {
                entry["semanticPath"]
                for target in result["assets"]
                for entry in target["unexpectedChanges"]
            }
            self.assertTrue(
                "insufficient-domain-snapshot-for-unexpected-change-detection" in gap_codes
                or any("UnexpectedFlag" in path for path in unexpected_paths)
            )

    def test_datatable_rename_reports_accompanying_row_payload_mutation(self) -> None:
        table_class = "/Script/Engine.DataTable"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_id = "plan_rename"
            operation_spec = {
                "operationId": "rename-row",
                "operation": "renameDataTableRow",
                "target": {"rowName": "Old", "newRowName": "New"},
                "value": True,
            }
            write_plan(root, plan_id, ASSET_A, table_class, [operation_spec])
            revision_root = root / "revision"
            write_canonical_export(
                revision_root,
                canonical_asset(
                    revision=BEFORE_REVISION,
                    asset_class=table_class,
                    asset_details={"type": "data-table", "rows": [{"Name": "Old", "Damage": 10}]},
                ),
            )
            verify_root = root / "work" / "verify-live-write" / "live_rename"
            write_canonical_export(
                verify_root,
                canonical_asset(
                    revision=AFTER_REVISION,
                    asset_class=table_class,
                    asset_details={"type": "data-table", "rows": [{"Name": "New", "Damage": 999}]},
                ),
            )
            service = _FakeWorkflow()
            service.config = SimpleNamespace(revision_export=revision_root)
            service.record = SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        receipt="live_rename",
                        plan_id=plan_id,
                        asset_path=ASSET_A,
                        operation="renameDataTableRow",
                        transaction_id="tx-rename",
                        save_receipt="save_rename",
                    )
                ]
            )
            service._live_applies["live_rename"] = SimpleNamespace(
                target={"rowName": "Old", "newRowName": "New"},
                before_value={"from": "Old", "to": "Old"},
                after_value={"from": "Old", "to": "New"},
                operation="renameDataTableRow",
                value_kind="data-table-row-rename",
            )
            service._plan_directory = lambda value: root / "plans" / value
            service._safe_work_path = lambda *parts: root / "work" / Path(*parts)
            service.get_change_set = lambda value: {
                "changeSetId": value,
                "taskId": "task_rename",
                "status": "verified",
            }

            result = analyze_workflow_semantic_diff(service, "cs_explicit", stage="verified")

            self.assertGreaterEqual(result["summary"]["unexpectedCount"], 1)
            self.assertTrue(
                any(
                    "Damage" in entry["semanticPath"]
                    for target in result["assets"]
                    for entry in target["unexpectedChanges"]
                )
            )

    def test_workflow_analysis_does_not_mutate_change_set_during_reconcile(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_id = "plan_read_only"
            write_plan(
                root,
                plan_id,
                ASSET_A,
                "/Script/Engine.DataAsset",
                [
                    {
                        "operationId": "set-damage",
                        "operation": "setAssetProperty",
                        "target": {"propertyPath": "Damage"},
                        "value": 30,
                    }
                ],
            )
            service = _FakeWorkflow()
            service.config = SimpleNamespace(revision_export=root / "revision")
            service.record = SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        receipt="live_read_only",
                        plan_id=plan_id,
                        asset_path=ASSET_A,
                        operation="setAssetProperty",
                        transaction_id="tx-read-only",
                        save_receipt="",
                        status="applied",
                    )
                ],
                status="applied",
            )
            service._live_applies["live_read_only"] = SimpleNamespace(
                target={"propertyPath": "Damage"},
                before_value=10,
                after_value=30,
                operation="setAssetProperty",
                value_kind="scalar",
            )
            service._plan_directory = lambda value: root / "plans" / value
            service._safe_work_path = lambda *parts: root / "work" / Path(*parts)
            service.get_change_set = lambda value: {
                "changeSetId": value,
                "taskId": "task_read_only",
                "status": service.record.status,
            }

            def mutating_reconcile(record: object, *, persist: bool) -> None:
                self.assertFalse(persist)
                record.status = "unknown"
                record.operations[0].status = "unknown"

            service._reconcile_change_set = mutating_reconcile
            original = copy.deepcopy(service.record)

            analyze_workflow_semantic_diff(service, "cs_explicit", stage="live")

            self.assertEqual(service.record, original)

    def test_expected_noop_uses_exact_persisted_baseline_without_live_or_verified_claim(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_id = "plan_noop"
            plan_directory = root / "plans" / plan_id
            plan_directory.mkdir(parents=True)
            plan = {
                "assets": [
                    {
                        "assetPath": ASSET_A,
                        "expectedRevision": BEFORE_REVISION,
                        "expectedAssetClass": "/Script/Engine.DataAsset",
                        "operations": [
                            {
                                "operationId": "noop-damage",
                                "operation": "setAssetProperty",
                                "target": {"propertyPath": "Damage"},
                                "value": 20,
                            }
                        ],
                    }
                ]
            }
            (plan_directory / "patch.json").write_text(json.dumps(plan), encoding="utf-8")
            revision_root = root / "revision"
            canonical_directory = revision_root / "canonical"
            canonical_directory.mkdir(parents=True)
            canonical_path = canonical_directory / "asset.json"
            canonical_path.write_text(
                json.dumps(
                    {
                        "projectName": "Fixture",
                        "assetPath": ASSET_A,
                        "assetClass": "/Script/Engine.DataAsset",
                        "revision": {"value": BEFORE_REVISION},
                        "assetDetails": {
                            "reader": "DataAsset",
                            "properties": [
                                {"name": "Damage", "value": 20, "valueType": "int32"}
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (revision_root / "manifest.json").write_text(
                json.dumps(
                    {
                        "projectName": "Fixture",
                        "assets": [
                            {
                                "assetPath": ASSET_A,
                                "success": True,
                                "jsonPath": "canonical/asset.json",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            service = _FakeWorkflow()
            service.config = SimpleNamespace(revision_export=revision_root)
            service.record = SimpleNamespace(
                operations=[
                    SimpleNamespace(
                        receipt="noop_explicit",
                        plan_id=plan_id,
                        asset_path=ASSET_A,
                        operation="setAssetProperty",
                        transaction_id="",
                        save_receipt="",
                    )
                ]
            )
            service._plan_directory = lambda value: root / "plans" / value
            service.get_change_set = lambda value: {
                "changeSetId": value,
                "taskId": "task_noop",
                "status": "no-op",
            }

            result = analyze_workflow_semantic_diff(service, "cs_explicit", stage="auto")

            self.assertEqual(result["evidenceStage"]["selected"], "persisted")
            self.assertEqual(result["summary"]["matchedCount"], 1)
            self.assertEqual(result["summary"]["actualCount"], 0)
            self.assertEqual(
                result["assets"][0]["matchedChanges"][0]["status"],
                "matched-expected-no-op",
            )
            self.assertIn(
                "baseline-canonical-no-op",
                {source["kind"] for source in result["evidenceStage"]["sources"]},
            )
            for unavailable_stage in ("live", "verified"):
                with self.subTest(stage=unavailable_stage):
                    with self.assertRaises(SemanticDiffEvidenceError) as caught:
                        analyze_workflow_semantic_diff(
                            service,
                            "cs_explicit",
                            stage=unavailable_stage,
                        )
                    self.assertEqual(caught.exception.code, "semantic-diff-stage-unavailable")


if __name__ == "__main__":
    unittest.main()

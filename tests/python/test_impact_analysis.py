from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = Path(__file__).resolve().parent
for root in (SRC_ROOT, TESTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from test_indexer_queries import make_asset, write_export  # noqa: E402
from ue_agent_kit.agent_api import IndexQueryService  # noqa: E402
from ue_agent_kit.database import open_database, set_metadata  # noqa: E402
from ue_agent_kit.impact_analysis import (  # noqa: E402
    MAX_IMPACT_DEPTH,
    MAX_IMPACT_TARGETS,
    ImpactAnalysisError,
)
from ue_agent_kit.indexer import build_index  # noqa: E402
from ue_agent_kit.task_context import TaskContextService  # noqa: E402
from ue_agent_kit.tool_registry import TOOL_DEFINITIONS_BY_NAME  # noqa: E402

MCP_AVAILABLE = importlib.util.find_spec("mcp") is not None
if MCP_AVAILABLE:
    from test_mcp_server import FakeLiveEditorService, FakeWorkflowService  # noqa: E402
    from ue_agent_kit.mcp_server import create_mcp_server  # noqa: E402

PROJECT = "测试项目"
T1 = "/Game/Mod/BP_TargetOne.BP_TargetOne"
T2 = "/Game/Mod/BP_TargetTwo.BP_TargetTwo"
GHOST = "/Game/Mod/BP_Ghost.BP_Ghost"
A = "/Game/Mod/BP_ConsumerA.BP_ConsumerA"
B = "/Game/Mod/BP_ConsumerB.BP_ConsumerB"
C = "/Game/Mod/BP_ConsumerC.BP_ConsumerC"
D = "/Game/Mod/BP_ConsumerD.BP_ConsumerD"
E = "/Game/Mod/BP_ConsumerE.BP_ConsumerE"
X = "/Game/Mod/BP_ConsumerX.BP_ConsumerX"
Y = "/Game/Mod/BP_ConsumerY.BP_ConsumerY"


def make_database(
    path: Path,
    *,
    assets: list[tuple[str, str]] | None = None,
    symbols: list[tuple[str, str, str, str]] | None = None,
    references: list[tuple[str, str, str, str, str]] | None = None,
) -> Path:
    """Build a minimal immutable-shape index database with direct SQL inserts.

    assets: (asset_path, asset_class)
    symbols: (stable_id, kind, name, symbol_asset_path)
    references: (consumer_path, target_asset_path, kind, target_kind, target_name)
    """
    with open_database(path) as connection:
        set_metadata(connection, "project_key", PROJECT)
        set_metadata(connection, "last_indexed_at_utc", "2026-08-18T00:00:00.000Z")
        for index, (asset_path, asset_class) in enumerate(assets or []):
            asset_name = asset_path.rsplit("/", 1)[-1].split(".", 1)[0]
            connection.execute(
                """
                INSERT INTO assets (
                    asset_path, package_name, asset_name, asset_class, blueprint_type,
                    parent_class, generated_class, skeleton_generated_class, status,
                    revision_value, package_guid, file_size, modified_utc, content_sha256,
                    package_dirty, schema_version, exporter_version, profile,
                    canonical_sha256, canonical_relpath, bpctx_relpath, summary_json,
                    indexed_at_utc
                ) VALUES (?, ?, ?, ?, 'normal', '', '', '', 0, '', '', 0, '', '', 0,
                          '1.1', '0.7.0', 'logic', ?, '', '', '{}', '2026-08-18T00:00:00.000Z')
                """,
                (
                    asset_path,
                    asset_path.rsplit(".", 1)[0],
                    asset_name,
                    asset_class,
                    f"canonical-{index}",
                ),
            )
        for index, (stable_id, kind, name, symbol_asset_path) in enumerate(symbols or []):
            connection.execute(
                """
                INSERT INTO symbols (asset_id, stable_id, kind, name, symbol_asset_path,
                                     guid, owner_symbol_id, parent_symbol_id, class_path,
                                     graph_guid, details_json)
                SELECT id, ?, ?, ?, ?, '', '', '', '', '', '{}' FROM assets
                WHERE asset_path = ?
                """,
                (stable_id, kind, name, symbol_asset_path, symbol_asset_path),
            )
        for index, (consumer, target, kind, target_kind, target_name) in enumerate(references or []):
            connection.execute(
                """
                INSERT INTO references_table (
                    asset_id, stable_id, kind, source_symbol_id, target_symbol_id,
                    target_kind, target_name, target_asset_path, target_path,
                    graph_guid, graph_name, node_guid, node_class, node_title, details_json
                )
                SELECT id, ?, ?, '', ?, ?, ?, ?, '', '', 'graph-name', '',
                       'K2Node_Test', 'node-title', '{}' FROM assets
                WHERE asset_path = ?
                """,
                (
                    f"ref-{index}-{kind}-{consumer}",
                    kind,
                    f"target-symbol-{index}",
                    target_kind,
                    target_name,
                    target,
                    consumer,
                ),
            )
        connection.commit()
    return path


class ImpactAnalysisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_impact_")
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "impact.sqlite3"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def make_service(self) -> IndexQueryService:
        return IndexQueryService(self.db_path)

    def analyze(self, **kwargs: Any) -> dict[str, Any]:
        return self.make_service().analyze_change_impact(**kwargs)

    # T1: single target with no consumers
    def test_impact_t1_single_target_no_consumers(self) -> None:
        make_database(self.db_path, assets=[(T1, "/Script/Engine.Blueprint")])
        response = self.analyze(target_asset_paths=[T1])
        self.assertTrue(response["ok"])
        self.assertEqual(response["summary"]["directConsumerCount"], 0)
        self.assertEqual(response["summary"]["indirectConsumerCount"], 0)
        self.assertEqual(response["summary"]["maxDepthReached"], 0)
        self.assertEqual(response["directConsumers"], [])
        self.assertEqual(response["indirectConsumers"], [])
        self.assertEqual(response["targets"][0]["directConsumerCount"], 0)
        gap_kinds = [gap["kind"] for gap in response["analysisGaps"]]
        self.assertIn("no-consumer-evidence-in-index", gap_kinds)
        self.assertEqual(response["validationTargets"][0]["tier"], 0)
        self.assertEqual(response["validationTargets"][0]["reason"], "modified-target-self")

    # T2: single target with one direct consumer
    def test_impact_t2_single_target_one_direct_consumer(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, "/Script/Engine.Blueprint"), (A, "/Script/Engine.Blueprint")],
            references=[(A, T1, "reads", "variable", "Value")],
        )
        response = self.analyze(target_asset_paths=[T1])
        self.assertEqual(response["summary"]["directConsumerCount"], 1)
        consumer = response["directConsumers"][0]
        self.assertEqual(consumer["assetPath"], A)
        self.assertEqual(consumer["depth"], 1)
        self.assertEqual(consumer["impactedTargets"], [T1])
        self.assertEqual(consumer["paths"], [{"targetAssetPath": T1, "depth": 1, "hops": []}])
        self.assertEqual(len(consumer["evidence"]), 1)
        self.assertEqual(consumer["evidence"][0]["kind"], "reads")
        self.assertEqual(response["targets"][0]["directConsumerCount"], 1)
        gap_kinds = [gap["kind"] for gap in response["analysisGaps"]]
        self.assertNotIn("no-consumer-evidence-in-index", gap_kinds)
        tiers = [item["tier"] for item in response["validationTargets"]]
        self.assertEqual(tiers, [0, 1])

    # T3: multiple direct consumers with fixed ordering
    def test_impact_t3_multiple_direct_consumers_fixed_order(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, "/Script/Engine.Blueprint"), (B, ""), (A, "")],
            references=[(B, T1, "reads", "variable", "Value"), (A, T1, "reads", "variable", "Value")],
        )
        response = self.analyze(target_asset_paths=[T1])
        self.assertEqual(
            [item["assetPath"] for item in response["directConsumers"]],
            [A, B],
        )

    # T4: same consumer with multiple reference kinds merges into one entry
    def test_impact_t4_same_consumer_multiple_kinds_merge(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, "/Script/Engine.Blueprint"), (A, "")],
            references=[
                (A, T1, "reads", "variable", "Value"),
                (A, T1, "calls", "function", "Tick"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1])
        self.assertEqual(response["summary"]["directConsumerCount"], 1)
        consumer = response["directConsumers"][0]
        self.assertEqual(
            [item["rawReferenceKind"] for item in consumer["referenceKinds"]],
            ["calls", "reads"],
        )
        self.assertEqual(
            [item["normalizedReferenceKind"] for item in consumer["referenceKinds"]],
            ["blueprint-symbol-reference", "blueprint-symbol-reference"],
        )
        self.assertEqual(len(consumer["evidence"]), 2)

    # T5: multi-target shared consumer dedupe + impactedTargets
    def test_impact_t5_multi_target_shared_consumer_dedupe(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (T2, ""), (A, ""), (B, "")],
            references=[
                (A, T1, "reads", "variable", "One"),
                (A, T2, "reads", "variable", "Two"),
                (B, T2, "reads", "variable", "Two"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1, T2])
        self.assertEqual(response["summary"]["directConsumerCount"], 2)
        by_path = {item["assetPath"]: item for item in response["directConsumers"]}
        self.assertEqual(by_path[A]["impactedTargets"], [T1, T2])
        self.assertEqual(by_path[B]["impactedTargets"], [T2])
        by_target = {item["assetPath"]: item for item in response["targets"]}
        self.assertEqual(by_target[T1]["directConsumerCount"], 1)
        self.assertEqual(by_target[T2]["directConsumerCount"], 2)

    # T6: depth=2 indirect consumer with impact path
    def test_impact_t6_indirect_consumer(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (B, ""), (C, "")],
            references=[
                (B, T1, "reads", "variable", "Value"),
                (C, B, "reads", "variable", "BValue"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1], max_depth=2)
        self.assertEqual(response["summary"]["directConsumerCount"], 1)
        self.assertEqual(response["summary"]["indirectConsumerCount"], 1)
        self.assertEqual(response["summary"]["maxDepthReached"], 2)
        indirect = response["indirectConsumers"][0]
        self.assertEqual(indirect["assetPath"], C)
        self.assertEqual(indirect["depth"], 2)
        self.assertEqual(indirect["paths"], [{"targetAssetPath": T1, "depth": 2, "hops": [B]}])
        tiers = [item["tier"] for item in response["validationTargets"]]
        self.assertEqual(tiers, [0, 1, 2])
        self.assertEqual(response["validationTargets"][2]["reason"], "indirect-consumer-depth-2")

    # T7: cycle does not repeat or expand forever
    def test_impact_t7_cycle_no_infinite_expansion(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (B, ""), (C, "")],
            references=[
                (B, T1, "reads", "variable", "Value"),
                (C, B, "reads", "variable", "BValue"),
                (B, C, "reads", "variable", "CValue"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1], max_depth=3)
        self.assertEqual(response["summary"]["directConsumerCount"], 1)
        self.assertEqual(response["summary"]["indirectConsumerCount"], 1)
        self.assertEqual(response["summary"]["maxDepthReached"], 2)
        self.assertEqual(response["summary"]["visitedEdgeCount"], 3)
        consumers = response["directConsumers"] + response["indirectConsumers"]
        self.assertEqual({item["assetPath"] for item in consumers}, {B, C})
        c_entry = next(item for item in consumers if item["assetPath"] == C)
        self.assertEqual(c_entry["depth"], 2)
        self.assertFalse(response["summary"]["truncated"])

    # T8: shortest depth and path stay stable
    def test_impact_t8_shortest_depth_and_path_stable(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (B, ""), (C, ""), (X, ""), (Y, "")],
            references=[
                (B, T1, "reads", "variable", "Value"),
                (C, B, "reads", "variable", "BValue"),
                (X, T1, "reads", "variable", "Value"),
                (Y, X, "reads", "variable", "XValue"),
                (C, Y, "reads", "variable", "YValue"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1], max_depth=3)
        consumers = response["directConsumers"] + response["indirectConsumers"]
        c_entry = next(item for item in consumers if item["assetPath"] == C)
        self.assertEqual(c_entry["depth"], 2)
        self.assertEqual(c_entry["paths"], [{"targetAssetPath": T1, "depth": 2, "hops": [B]}])
        y_entry = next(item for item in consumers if item["assetPath"] == Y)
        self.assertEqual(y_entry["depth"], 2)
        self.assertEqual(response["summary"]["pathCount"], 4)

    # T9: maxDepth boundary behavior
    def test_impact_t9_max_depth_boundary(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (B, ""), (C, "")],
            references=[
                (B, T1, "reads", "variable", "Value"),
                (C, B, "reads", "variable", "BValue"),
            ],
        )
        depth1 = self.analyze(target_asset_paths=[T1], max_depth=1)
        self.assertEqual(depth1["summary"]["indirectConsumerCount"], 0)
        self.assertEqual(depth1["summary"]["maxDepthReached"], 1)
        depth3 = self.analyze(target_asset_paths=[T1], max_depth=3)
        self.assertEqual(depth3["summary"]["indirectConsumerCount"], 1)
        with self.assertRaisesRegex(ValueError, "max_depth"):
            self.analyze(target_asset_paths=[T1], max_depth=0)
        with self.assertRaisesRegex(ValueError, "max_depth"):
            self.analyze(target_asset_paths=[T1], max_depth=MAX_IMPACT_DEPTH + 1)

    # T10: consumer limit truncation with honest counts
    def test_impact_t10_consumer_limit_truncation(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, ""), (B, ""), (C, ""), (D, "")],
            references=[
                (A, T1, "reads", "variable", "Value"),
                (B, T1, "reads", "variable", "Value"),
                (C, T1, "reads", "variable", "Value"),
                (D, T1, "reads", "variable", "Value"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1], max_consumers=2)
        self.assertEqual(response["summary"]["directConsumerCount"], 2)
        self.assertTrue(response["summary"]["truncated"])
        self.assertIn("consumer-limit", response["summary"]["truncationReasons"])
        self.assertEqual(response["summary"]["frontierOmittedCount"], 2)
        self.assertEqual(
            [item["assetPath"] for item in response["directConsumers"]],
            [A, B],
        )
        risk_kinds = [risk["kind"] for risk in response["risks"]]
        self.assertIn("impact-analysis-truncated", risk_kinds)

    # T10b: edge limit truncation with honest counts
    def test_impact_t10b_edge_limit_truncation(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, ""), (B, "")],
            references=[
                (A, T1, "reads", "variable", "Value"),
                (A, T1, "writes", "variable", "Value"),
                (A, T1, "calls", "function", "Tick"),
                (B, T1, "reads", "variable", "Value"),
                (B, T1, "writes", "variable", "Value"),
                (B, T1, "calls", "function", "Tick"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1], max_edges=2)
        self.assertTrue(response["summary"]["truncated"])
        self.assertIn("edge-limit", response["summary"]["truncationReasons"])
        self.assertEqual(response["summary"]["omittedEdgeCount"], 4)
        total_evidence = sum(
            len(record.get("evidence", [])) for record in response["directConsumers"]
        )
        self.assertEqual(total_evidence, 2)
        self.assertEqual(response["summary"]["directConsumerCount"], 2)

    # T10c: path limit truncation with honest counts
    def test_impact_t10c_path_limit_truncation(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (T2, ""), (A, ""), (B, ""), (C, "")],
            references=[
                (A, T1, "reads", "variable", "One"),
                (B, T2, "reads", "variable", "Two"),
                (C, A, "reads", "variable", "AValue"),
                (C, B, "reads", "variable", "BValue"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1, T2], max_depth=2, max_paths=2)
        self.assertTrue(response["summary"]["truncated"])
        self.assertIn("path-limit", response["summary"]["truncationReasons"])
        self.assertEqual(response["summary"]["omittedPathCount"], 2)
        self.assertEqual(response["summary"]["directConsumerCount"], 2)
        self.assertEqual(response["summary"]["indirectConsumerCount"], 0)

    # T11: unindexed target is explicit, not fabricated
    def test_impact_t11_target_not_indexed(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, "")],
            references=[(A, T1, "reads", "variable", "Value")],
        )
        response = self.analyze(target_asset_paths=[T1, GHOST])
        ghost = next(item for item in response["targets"] if item["assetPath"] == GHOST)
        self.assertFalse(ghost["found"])
        self.assertEqual(ghost["reason"], "target-not-indexed")
        self.assertEqual(response["summary"]["foundTargetCount"], 1)
        self.assertEqual(response["summary"]["notIndexedTargetCount"], 1)
        risk_kinds = [risk["kind"] for risk in response["risks"]]
        self.assertIn("impact-target-not-indexed", risk_kinds)
        self.assertEqual(response["summary"]["directConsumerCount"], 1)

    # T12: unknown reference kinds are preserved, never guessed
    def test_impact_t12_unknown_reference_kind_not_fabricated(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, "")],
            references=[(A, T1, "mystery-kind", "variable", "Value")],
        )
        response = self.analyze(target_asset_paths=[T1])
        consumer = response["directConsumers"][0]
        self.assertEqual(consumer["referenceKinds"][0]["rawReferenceKind"], "mystery-kind")
        self.assertEqual(consumer["referenceKinds"][0]["normalizedReferenceKind"], "unknown-reference")
        self.assertEqual(response["summary"]["unknownReferenceKindCount"], 1)
        self.assertIn("unknown-reference-kind", [gap["kind"] for gap in response["analysisGaps"]])
        self.assertIn("unknown-reference-kind", [risk["kind"] for risk in response["risks"]])

    # T13: unsupported structured subject is explicitly rejected
    def test_impact_t13_unsupported_subject_kind(self) -> None:
        make_database(self.db_path, assets=[(T1, "")])
        with self.assertRaises(ImpactAnalysisError) as context:
            self.analyze(
                target_asset_paths=[T1],
                subject_kind="data-table-row",
                subject="Row_0",
            )
        self.assertEqual(context.exception.code, "unsupported-impact-subject")
        with self.assertRaisesRegex(ValueError, "subject_kind"):
            self.analyze(target_asset_paths=[T1], subject_kind="made-up-kind", subject="x")

    # T14: validationTargets deterministic ordering and reasons
    def test_impact_t14_validation_targets_ordering(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, ""), (C, "")],
            references=[
                (A, T1, "reads", "variable", "Value"),
                (C, A, "reads", "variable", "AValue"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1], max_depth=2)
        targets = response["validationTargets"]
        self.assertEqual([item["assetPath"] for item in targets], [T1, A, C])
        self.assertEqual([item["tier"] for item in targets], [0, 1, 2])
        self.assertEqual([item["priorityOrder"] for item in targets], [0, 1, 2])
        self.assertEqual([item["depth"] for item in targets], [0, 1, 2])

    # T15: runtime sensitivity is never guessed from asset classes
    def test_impact_t15_runtime_sensitivity_not_proven(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, "/Script/Engine.Blueprint"), (A, "/Script/UMGEditor.WidgetBlueprint")],
            references=[(A, T1, "reads", "variable", "Value")],
        )
        response = self.analyze(target_asset_paths=[T1])
        block = response["runtimeSensitiveConsumers"]
        self.assertEqual(block["classificationState"], "not-proven-with-current-evidence")
        self.assertEqual(block["items"], [])
        self.assertEqual(response["summary"]["runtimeSensitiveConsumerCount"], 0)

    # T16: low token budget trims in the fixed priority order
    def test_impact_t16_low_budget_trim_order(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, ""), (C, "")],
            references=[
                (A, T1, "reads", "variable", "Value"),
                (C, A, "reads", "variable", "AValue"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1], max_depth=2, max_output_tokens=256)
        budget = response["outputBudget"]
        self.assertTrue(budget["truncated"])
        self.assertEqual(budget["truncationReasons"][0], "impact-paths")
        self.assertIn("summary", response)
        self.assertIn("risks", response)
        self.assertIn("nextActions", response)
        self.assertFalse(any(item.get("paths") for item in response["directConsumers"]))
        self.assertFalse(any(item.get("paths") for item in response["indirectConsumers"]))

    # T17: identical input yields identical output
    def test_impact_t17_deterministic_output(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (T2, ""), (A, ""), (B, ""), (C, "")],
            references=[
                (A, T1, "reads", "variable", "One"),
                (B, T2, "reads", "variable", "Two"),
                (C, B, "calls", "function", "Tick"),
            ],
        )
        first = self.analyze(target_asset_paths=[T1, T2], max_depth=3)
        second = self.analyze(target_asset_paths=[T1, T2], max_depth=3)
        self.assertEqual(first, second)

    # T20 (service level): identical results regardless of surrounding server mode
    def test_impact_t20_mode_independent_service_result(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, "")],
            references=[(A, T1, "reads", "variable", "Value")],
        )
        response = self.analyze(target_asset_paths=[T1])
        self.assertEqual(response["tool"], "ue_analyze_change_impact")
        self.assertTrue(response["readOnly"])
        self.assertEqual(response["summary"]["directConsumerCount"], 1)

    # Domain: blueprint-symbol subject with exact stable id
    def test_impact_symbol_subject_direct_consumers(self) -> None:
        stable_id = "function|/Game/Mod/BP_TargetOne.BP_TargetOne|guid-1|HitSounds"
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, "")],
            symbols=[(stable_id, "function", "HitSounds", T1)],
            references=[(A, "", "calls", "function", "HitSounds")],
        )
        with open_database(self.db_path) as connection:
            connection.execute(
                """
                UPDATE references_table SET target_symbol_id = ?
                WHERE asset_id IN (SELECT id FROM assets WHERE asset_path = ?)
                """,
                (stable_id, A),
            )
            connection.commit()
        response = self.analyze(
            target_asset_paths=[T1],
            subject_kind="blueprint-symbol",
            subject=stable_id,
        )
        self.assertEqual(response["summary"]["directConsumerCount"], 1)
        consumer = response["directConsumers"][0]
        self.assertEqual(consumer["assetPath"], A)
        self.assertEqual(consumer["whyIncluded"], "reference-edge-to-subject-symbol")
        self.assertEqual(response["targets"][0]["subject"]["stableId"], stable_id)

    def test_impact_symbol_subject_not_found(self) -> None:
        make_database(self.db_path, assets=[(T1, "")])
        with self.assertRaises(ImpactAnalysisError) as context:
            self.analyze(
                target_asset_paths=[T1],
                subject_kind="blueprint-symbol",
                subject="function|missing|guid|Name",
            )
        self.assertEqual(context.exception.code, "impact-subject-not-found")

    def test_impact_symbol_subject_asset_mismatch(self) -> None:
        stable_id = "function|/Game/Mod/BP_TargetOne.BP_TargetOne|guid-1|HitSounds"
        make_database(
            self.db_path,
            assets=[(T1, ""), (T2, "")],
            symbols=[(stable_id, "function", "HitSounds", T1)],
        )
        with self.assertRaises(ImpactAnalysisError) as context:
            self.analyze(
                target_asset_paths=[T2],
                subject_kind="blueprint-symbol",
                subject=stable_id,
            )
        self.assertEqual(context.exception.code, "impact-subject-asset-mismatch")

    def test_impact_symbol_subject_requires_single_target(self) -> None:
        stable_id = "function|/Game/Mod/BP_TargetOne.BP_TargetOne|guid-1|HitSounds"
        make_database(
            self.db_path,
            assets=[(T1, ""), (T2, "")],
            symbols=[(stable_id, "function", "HitSounds", T1)],
        )
        with self.assertRaisesRegex(ValueError, "exactly one"):
            self.analyze(
                target_asset_paths=[T1, T2],
                subject_kind="blueprint-symbol",
                subject=stable_id,
            )

    # Domain: blueprint-symbol subjects also support bounded indirect consumers
    def test_impact_symbol_subject_indirect_consumers(self) -> None:
        stable_id = "function|/Game/Mod/BP_TargetOne.BP_TargetOne|guid-1|HitSounds"
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, ""), (C, "")],
            symbols=[(stable_id, "function", "HitSounds", T1)],
            references=[
                (A, "", "calls", "function", "HitSounds"),
                (C, A, "reads", "variable", "AValue"),
            ],
        )
        with open_database(self.db_path) as connection:
            connection.execute(
                """
                UPDATE references_table SET target_symbol_id = ?
                WHERE asset_id IN (SELECT id FROM assets WHERE asset_path = ?)
                """,
                (stable_id, A),
            )
            connection.commit()
        response = self.analyze(
            target_asset_paths=[T1],
            subject_kind="blueprint-symbol",
            subject=stable_id,
            max_depth=2,
        )
        self.assertEqual(response["summary"]["directConsumerCount"], 1)
        self.assertEqual(response["summary"]["indirectConsumerCount"], 1)
        self.assertEqual(response["summary"]["maxDepthReached"], 2)
        indirect = response["indirectConsumers"][0]
        self.assertEqual(indirect["assetPath"], C)
        self.assertEqual(indirect["depth"], 2)
        self.assertEqual(indirect["paths"], [{"targetAssetPath": T1, "depth": 2, "hops": [A]}])


    # T19: R0 task context integration (expansion entries)
    def test_impact_t19_task_context_expansion_integration(self) -> None:
        asset_path = "/Game/Fleet/BP_Vehicle_01.BP_Vehicle_01"
        export_root = self.root / "ctx-export"
        database_path = self.root / "ctx.sqlite3"
        write_export(
            export_root,
            [make_asset(asset_path, profile="logic", revision="1" * 64, rich=False)],
        )
        with open_database(database_path) as connection:
            build_index(connection, export_root, database_path)
        service = TaskContextService(index_service=IndexQueryService(database_path))
        context = service.get_task_context(query="vehicle tuning", asset_paths=[asset_path])
        impact = [
            item
            for item in context["nextExpansions"]
            if item["tool"] == "ue_analyze_change_impact"
        ]
        self.assertEqual(len(impact), 1)
        self.assertEqual(impact[0]["reason"], "impact-analysis-explicit-targets")
        self.assertEqual(impact[0]["arguments"]["target_asset_paths"], [asset_path])
        hinted = service.get_task_context(query="vehicle tuning", asset_paths=[])
        hint = [
            item
            for item in hinted["nextExpansions"]
            if item["tool"] == "ue_analyze_change_impact"
        ]
        self.assertEqual(len(hint), 1)
        self.assertEqual(hint[0]["reason"], "impact-analysis-relevant-asset-hint")

    # Domain: self references never become self consumers
    def test_impact_self_reference_excluded(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, "")],
            references=[(T1, T1, "reads", "variable", "Self"), (A, A, "reads", "variable", "Self")],
        )
        response = self.analyze(target_asset_paths=[T1])
        self.assertEqual(response["summary"]["directConsumerCount"], 0)
        response_a = self.analyze(target_asset_paths=[A])
        self.assertEqual(response_a["summary"]["directConsumerCount"], 0)

    # Domain: an inter-target edge keeps the other target as a consumer
    def test_impact_inter_target_edge(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (T2, "")],
            references=[(T2, T1, "reads", "variable", "One")],
        )
        response = self.analyze(target_asset_paths=[T1, T2])
        self.assertEqual(response["summary"]["directConsumerCount"], 1)
        consumer = response["directConsumers"][0]
        self.assertEqual(consumer["assetPath"], T2)
        self.assertEqual(consumer["impactedTargets"], [T1])
        tiers = [item["tier"] for item in response["validationTargets"]]
        self.assertEqual(tiers, [0, 0, 1])

    # Domain: reference kind normalization table
    def test_impact_reference_kind_categories(self) -> None:
        make_database(
            self.db_path,
            assets=[(T1, ""), (A, "")],
            references=[
                (A, T1, "inherits", "class", "T1_C"),
                (A, T1, "depends-hard-package", "asset", T1),
                (A, T1, "casts", "class", "T1_C"),
                (A, T1, "implements", "interface", "T1_C"),
            ],
        )
        response = self.analyze(target_asset_paths=[T1])
        consumer = response["directConsumers"][0]
        categories = {
            item["rawReferenceKind"]: item["normalizedReferenceKind"]
            for item in consumer["referenceKinds"]
        }
        self.assertEqual(categories["inherits"], "parent-reference")
        self.assertEqual(categories["depends-hard-package"], "asset-reference")
        self.assertEqual(categories["casts"], "class-reference")
        self.assertEqual(categories["implements"], "class-reference")

    # Argument validation
    def test_impact_argument_validation(self) -> None:
        make_database(self.db_path, assets=[(T1, "")])
        with self.assertRaisesRegex(ValueError, "array of strings"):
            self.analyze(target_asset_paths="not-a-list")
        with self.assertRaisesRegex(ValueError, "at least one"):
            self.analyze(target_asset_paths=[])
        with self.assertRaisesRegex(ValueError, "exact /Game Object Path"):
            self.analyze(target_asset_paths=["/Script/Engine.Pawn"])
        with self.assertRaisesRegex(ValueError, "duplicates"):
            self.analyze(target_asset_paths=[T1, T1])
        with self.assertRaisesRegex(ValueError, "must not exceed"):
            self.analyze(target_asset_paths=[f"/Game/X{i}.X{i}" for i in range(MAX_IMPACT_TARGETS + 1)])
        with self.assertRaisesRegex(ValueError, "structured subject kinds"):
            self.analyze(target_asset_paths=[T1], subject_kind="asset-level", subject="x")
        with self.assertRaisesRegex(ValueError, "required for structured"):
            self.analyze(target_asset_paths=[T1], subject_kind="blueprint-symbol", subject="")
        with self.assertRaisesRegex(ValueError, "max_consumers"):
            self.analyze(target_asset_paths=[T1], max_consumers=0)
        with self.assertRaisesRegex(ValueError, "max_edges"):
            self.analyze(target_asset_paths=[T1], max_edges=10_000)
        with self.assertRaisesRegex(ValueError, "max_paths"):
            self.analyze(target_asset_paths=[T1], max_paths=10_000)
        with self.assertRaisesRegex(ValueError, "max_depth must be an integer"):
            self.analyze(target_asset_paths=[T1], max_depth=True)


@unittest.skipUnless(MCP_AVAILABLE, "optional mcp dependency is not installed")
class ImpactMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="ueak_impact_mcp_")
        self.root = Path(self.temporary.name)
        self.db_path = self.root / "impact.sqlite3"
        make_database(
            self.db_path,
            assets=[(T1, "/Script/Engine.Blueprint"), (A, "/Script/Engine.Blueprint")],
            references=[(A, T1, "reads", "variable", "Value")],
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    # T18: Tool Registry / MCP capabilities / strict arguments contract
    def test_impact_t18_registry_capabilities_and_strict_args(self) -> None:
        definition = TOOL_DEFINITIONS_BY_NAME["ue_analyze_change_impact"]
        self.assertEqual(definition.group, "query")
        self.assertEqual(definition.annotation, "read")
        self.assertTrue(definition.read_only)
        self.assertFalse(definition.destructive)

        server = create_mcp_server(self.db_path)
        tools = asyncio.run(server.list_tools())
        impact_tool = next(tool for tool in tools if tool.name == "ue_analyze_change_impact")
        self.assertFalse(impact_tool.inputSchema.get("additionalProperties", True))
        self.assertIn("target_asset_paths", impact_tool.inputSchema["properties"])
        self.assertIn("subject_kind", impact_tool.inputSchema["properties"])
        self.assertIn("max_depth", impact_tool.inputSchema["properties"])

        _, capabilities = asyncio.run(server.call_tool("ue_get_capabilities", {}))
        contract = capabilities["impactAnalysis"]
        self.assertTrue(contract["available"])
        self.assertEqual(contract["tool"], "ue_analyze_change_impact")
        self.assertTrue(contract["readOnly"])
        self.assertTrue(contract["deterministic"])
        self.assertFalse(contract["modelInference"])
        self.assertEqual(contract["direction"], "consumer-to-target")
        self.assertEqual(contract["maxTargets"], MAX_IMPACT_TARGETS)
        self.assertEqual(contract["maxDepth"], MAX_IMPACT_DEPTH)
        self.assertTrue(contract["supportsIndirect"])
        self.assertTrue(contract["supportsValidationTargets"])
        self.assertFalse(contract["supportsRuntimeSensitivityClassification"])
        self.assertEqual(contract["subjectKinds"], ["asset-level", "blueprint-symbol"])
        self.assertEqual(capabilities["limits"]["impactDepth"], MAX_IMPACT_DEPTH)

        _, project_status = asyncio.run(server.call_tool("ue_get_project_status", {}))
        self.assertTrue(project_status["impactAnalysis"]["available"])
        self.assertTrue(project_status["impactAnalysis"]["deterministic"])

        with self.assertRaisesRegex(Exception, "Extra inputs are not permitted"):
            asyncio.run(
                server.call_tool(
                    "ue_analyze_change_impact",
                    {"target_asset_paths": [T1], "database": "/tmp/x.sqlite3"},
                )
            )

    # T18b: MCP envelope for unsupported subject and invalid arguments
    def test_impact_t18b_mcp_error_envelopes(self) -> None:
        server = create_mcp_server(self.db_path)
        _, payload = asyncio.run(
            server.call_tool(
                "ue_analyze_change_impact",
                {"target_asset_paths": [T1], "subject_kind": "data-table-row", "subject": "Row_0"},
            )
        )
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "unsupported-impact-subject")
        self.assertFalse(payload["error"]["retryable"])
        self.assertTrue(payload["error"]["suggestedAction"])

        _, invalid = asyncio.run(
            server.call_tool("ue_analyze_change_impact", {"target_asset_paths": ["/Script/Engine.Pawn"]})
        )
        self.assertFalse(invalid["ok"])
        self.assertEqual(invalid["error"]["code"], "invalid-arguments")

    # T18c/T20: tool works in query-only and live+workflow modes
    def test_impact_t18c_mode_consistency(self) -> None:
        query_only = create_mcp_server(self.db_path)
        _, query_payload = asyncio.run(
            query_only.call_tool(
                "ue_analyze_change_impact",
                {"target_asset_paths": [T1], "max_depth": 2},
            )
        )
        self.assertTrue(query_payload["ok"])
        self.assertEqual(query_payload["summary"]["directConsumerCount"], 1)

        combined = create_mcp_server(
            self.db_path,
            workflow_service=FakeWorkflowService(),
            live_editor_service=FakeLiveEditorService(),
        )
        _, combined_payload = asyncio.run(
            combined.call_tool(
                "ue_analyze_change_impact",
                {"target_asset_paths": [T1], "max_depth": 2},
            )
        )
        self.assertTrue(combined_payload["ok"])
        self.assertEqual(
            combined_payload["summary"]["directConsumerCount"],
            query_payload["summary"]["directConsumerCount"],
        )


if __name__ == "__main__":
    unittest.main()

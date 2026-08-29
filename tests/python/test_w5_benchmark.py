from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.w5.metrics import (  # noqa: E402
    compute_paired_ratios,
    noise_groups,
    stage_contribution,
    summarize_attempts,
    summarize_numeric,
)
from benchmarks.w5.workloads import (  # noqa: E402
    W4_MAX_ASSETS_PER_BATCH,
    W4_MAX_OPS_PER_ASSET,
    W4_MAX_TOTAL_OPS_PER_BATCH,
    W5AssetGroup,
    cold_commandlet_specs,
    default_workloads,
    scenario_for_id,
)


class WorkloadTests(unittest.TestCase):
    def test_default_workloads_valid_and_respect_w4_bounds(self) -> None:
        for workload in default_workloads():
            workload.validate()
            self.assertGreater(workload.logical_operation_count, 0)
            self.assertLessEqual(workload.batch_count, workload.logical_operation_count)
            for batch in workload.batches:
                self.assertLessEqual(len(batch.assets), W4_MAX_ASSETS_PER_BATCH)
                self.assertLessEqual(batch.operations_per_batch, W4_MAX_TOTAL_OPS_PER_BATCH)
                for group in batch.assets:
                    self.assertLessEqual(len(group.operations), W4_MAX_OPS_PER_ASSET)

    def test_r20_is_split_into_legal_batches(self) -> None:
        r20 = scenario_for_id("R20")
        self.assertEqual(r20.logical_operation_count, 20)
        self.assertGreater(r20.batch_count, 1)
        self.assertEqual(sum(r20.operations_per_batch), 20)
        self.assertLessEqual(max(r20.operations_per_batch), W4_MAX_TOTAL_OPS_PER_BATCH)
        self.assertNotEqual(r20.batch_count, 1)

    def test_cold_commandlet_specs_per_asset(self) -> None:
        specs = cold_commandlet_specs(scenario_for_id("R20"))
        self.assertGreaterEqual(len(specs), 4)
        for spec in specs:
            self.assertIn(spec["commandlet"], {"AssetPatch", "BlueprintPatch"})
            self.assertTrue(spec["operations"])

    def test_over_bounds_raises(self) -> None:
        group = W5AssetGroup(
            asset_path="/Game/T.T",
            operations=tuple(
                {"operation": "setAssetProperty", "target": {"propertyPath": "P"}, "value": i}
                for i in range(W4_MAX_OPS_PER_ASSET + 1)
            ),
        )
        with self.assertRaises(ValueError):
            group.validate()


class MetricsTests(unittest.TestCase):
    def _attempt(self, value: float, *, success: bool = True) -> dict[str, Any]:
        return {
            "success": success,
            "totalMs": value,
            "resultBytes": int(value),
            "errorCode": None if success else "boom",
            "finalTrustState": "verified" if success else None,
            "stages": {
                "planMs": value * 0.1,
                "applyMs": value * 0.4,
                "fastVerifyMs": value * 0.2,
                "saveMs": value * 0.3,
                "strongVerifyMs": 1.0,
                "trustMs": 1.0,
            },
        }

    def test_summarize_numeric_p95_only_when_10(self) -> None:
        small = summarize_numeric([1, 2, 3])
        self.assertIsNone(small["p95"])
        values = [float(i) for i in range(1, 11)]
        summary = summarize_numeric(values)
        self.assertEqual(summary["p95Claimable"], True)
        self.assertIsNotNone(summary["p95"])

    def test_summarize_attempts_separates_failures(self) -> None:
        attempts = [self._attempt(1.0), self._attempt(2.0), self._attempt(3.0, success=False)]
        summary = summarize_attempts(attempts)
        self.assertEqual(summary["attemptCount"], 3)
        self.assertEqual(summary["successCount"], 2)
        self.assertEqual(summary["failureCount"], 1)
        self.assertEqual(summary["failureErrorCodes"], ["boom"])
        self.assertEqual(summary["finalTrustStates"], ["verified"])

    def test_stage_contribution(self) -> None:
        result = stage_contribution([self._attempt(10.0)])
        self.assertEqual(result["samples"], 1)
        self.assertAlmostEqual(sum(result["contributions"].values()), 1.0, places=4)

    def test_noise_groups(self) -> None:
        attempts = [self._attempt(100.0 + i) for i in range(10)]
        result = noise_groups(attempts)
        self.assertTrue(result["evaluable"])
        self.assertEqual(result["label"], "repeatable")

    def test_paired_ratios(self) -> None:
        resident = [self._attempt(10.0)]
        cold = [self._attempt(50.0)]
        cold[0]["stages"]["mutationMs"] = 40.0
        result = compute_paired_ratios(resident, cold)
        self.assertAlmostEqual(result["mutationPathSpeedup"], 40.0 / (4.0 + 2.0), places=6)
        self.assertEqual(result["persistedWorkflowRatio"], 5.0)


class RunnerOfflineTests(unittest.TestCase):
    def test_utc_stamp_and_write_json(self) -> None:
        from benchmarks.w5.runner import utc_stamp, write_json

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "nested" / "x.json"
            write_json(out, {"a": 1})
            self.assertEqual(json.loads(out.read_text(encoding="utf-8")), {"a": 1})
        self.assertRegex(utc_stamp(), r"^\d{8}T\d{6}Z$")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
ROOT = TOOL_ROOT
SRC_ROOT = TOOL_ROOT / "src"
TESTS_ROOT = TOOL_ROOT / "tests" / "python"
for root in (ROOT, SRC_ROOT, TESTS_ROOT):
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

from scripts import MeasureMemoryOverhead  # noqa: E402


class MemoryOverheadReportTests(unittest.TestCase):
    def test_percentile_linear_interpolation(self) -> None:
        samples = [10.0, 20.0, 30.0, 40.0, 50.0]
        # Linear interpolation used by the harness: index = 4 * 0.95 = 3.8
        self.assertAlmostEqual(MeasureMemoryOverhead._percentile(sorted(samples), 95.0), 48.0, places=6)
        self.assertEqual(MeasureMemoryOverhead._percentile(sorted(samples), 0.0), 10.0)
        self.assertEqual(MeasureMemoryOverhead._percentile(sorted(samples), 100.0), 50.0)

    def test_sample_stats_are_consistent(self) -> None:
        stats = MeasureMemoryOverhead._sample_stats([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(stats["n"], 5)
        self.assertEqual(stats["minMs"], 1.0)
        self.assertEqual(stats["maxMs"], 5.0)
        self.assertAlmostEqual(stats["medianMs"], 3.0, places=6)
        self.assertLessEqual(stats["p95Ms"], 5.0)

    def test_report_has_stable_schema_and_no_absolute_paths(self) -> None:
        measurements = {
            "scenarios": {
                "B3_direct_automatic_recall": {"elapsedMs": {"p95Ms": 1.0}},
                "B4_task_end_append": {"elapsedMs": {"p95Ms": 1.0}},
                "B5_single_l0_capture": {"elapsedMs": {"p95Ms": 2.0}},
                "B6_four_event_l0_capture_batch": {"elapsedMs": {"p95Ms": 3.0}},
                "B7_exact_state_duplicate_replay": {"elapsedMs": {"p95Ms": 4.0}},
            },
            "derived": {"first_tool_memory_incremental_p95Ms": 1.0},
        }
        report = MeasureMemoryOverhead._build_report(
            mode="baseline",
            samples=1,
            measurements=measurements,
            output_path=Path("benchmarks/memory/report.json"),
        )
        self.assertEqual(report["schema"], MeasureMemoryOverhead.REPORT_SCHEMA)
        self.assertEqual(report["mode"], "baseline")
        self.assertEqual(report["samples"], 1)
        self.assertEqual(report["fixture"]["projectKey"], MeasureMemoryOverhead.PROJECT_KEY)
        self.assertNotIn("outputPath", report)
        self.assertIn("No absolute user paths are recorded", report["fixture"]["note"])
        self.assertEqual(report["summary"]["fourEventL0CaptureBatchP95Ms"], 3.0)
        environment = report["environment"]
        self.assertEqual(environment["hostnameHash"], "")
        self.assertNotIn("\\", environment["platform"])
        self.assertNotIn(":", environment["machine"])
        self.assertNotIn("C:", environment["platform"])
        self.assertNotIn("C:", environment["machine"])

    def test_gate_evaluation_all_pass_and_fail(self) -> None:
        passing = {
            "g1": {"limitMs": 100.0, "actualMs": 99.9, "pass": True},
            "g2": {"limitMs": 200.0, "actualMs": 199.9, "pass": True},
        }
        failing = {
            "g1": {"limitMs": 100.0, "actualMs": 100.1, "pass": False},
            "g2": {"limitMs": 200.0, "actualMs": 199.9, "pass": True},
        }
        self.assertTrue(MeasureMemoryOverhead._run_gate_check({"measurements": {"gates": passing}}))
        self.assertFalse(MeasureMemoryOverhead._run_gate_check({"measurements": {"gates": failing}}))


if __name__ == "__main__":
    unittest.main()

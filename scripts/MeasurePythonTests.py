from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unittest
from collections import defaultdict
from contextlib import nullcontext
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests" / "python"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class TimingResult(unittest.TextTestResult):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self._started_at = 0.0
        self.timings: list[dict[str, object]] = []

    def startTest(self, test: unittest.case.TestCase) -> None:
        self._started_at = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test: unittest.case.TestCase) -> None:
        elapsed = time.perf_counter() - self._started_at
        test_id = test.id()
        module = test_id.rsplit(".", 2)[0] if test_id.count(".") >= 2 else test_id
        self.timings.append({"id": test_id, "module": module, "elapsedSeconds": elapsed})
        super().stopTest(test)


def discover_suite() -> unittest.TestSuite:
    return unittest.defaultTestLoader.discover(str(TEST_ROOT), pattern="test_*.py")


def build_report(result: TimingResult, elapsed: float) -> dict[str, object]:
    module_seconds: dict[str, float] = defaultdict(float)
    module_counts: dict[str, int] = defaultdict(int)
    for item in result.timings:
        module = str(item["module"])
        module_seconds[module] += float(item["elapsedSeconds"])
        module_counts[module] += 1

    modules = [
        {"module": module, "testCount": module_counts[module], "elapsedSeconds": module_seconds[module]}
        for module in module_seconds
    ]
    modules.sort(key=lambda item: float(item["elapsedSeconds"]), reverse=True)
    slowest = sorted(result.timings, key=lambda item: float(item["elapsedSeconds"]), reverse=True)

    return {
        "schemaVersion": 1,
        "discovery": {"startDirectory": "tests/python", "pattern": "test_*.py"},
        "summary": {
            "testsRun": result.testsRun,
            "failures": len(result.failures),
            "errors": len(result.errors),
            "skipped": len(result.skipped),
            "elapsedSeconds": elapsed,
        },
        "modules": modules,
        "tests": slowest,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure the full Python unittest suite in one process.")
    parser.add_argument("--output", type=Path, required=True, help="JSON report path, relative to the repository root.")
    parser.add_argument("--top", type=int, default=20, help="Number of slowest tests/modules to print.")
    parser.add_argument("--quiet", action="store_true", help="Suppress unittest progress output.")
    args = parser.parse_args()

    with (open(os.devnull, "w", encoding="utf-8") if args.quiet else nullcontext(sys.stderr)) as stream:
        runner = unittest.TextTestRunner(
            stream=stream,
            verbosity=0 if args.quiet else 1,
            resultclass=TimingResult,
        )
        started = time.perf_counter()
        result = runner.run(discover_suite())
        elapsed = time.perf_counter() - started

    report = build_report(result, elapsed)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    top = max(args.top, 0)
    print(f"Tests: {report['summary']['testsRun']}  Elapsed: {elapsed:.3f}s")
    print("Slowest modules:")
    for item in report["modules"][:top]:
        print(f"  {item['elapsedSeconds']:8.3f}s  {item['testCount']:4d}  {item['module']}")
    print("Slowest tests:")
    for item in report["tests"][:top]:
        print(f"  {item['elapsedSeconds']:8.3f}s  {item['id']}")
    print(f"Report: {output.relative_to(ROOT)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

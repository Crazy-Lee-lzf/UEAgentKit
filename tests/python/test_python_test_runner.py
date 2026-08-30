from __future__ import annotations

import unittest

from scripts import MeasurePythonTests, RunPythonTests


class PythonTestRunnerTests(unittest.TestCase):
    def test_full_suite_matches_direct_discovery_count(self) -> None:
        direct = unittest.defaultTestLoader.discover(str(RunPythonTests.TEST_ROOT), pattern="test_*.py")
        via_runner = RunPythonTests.build_suite("full")
        self.assertEqual(via_runner.countTestCases(), direct.countTestCases())

    def test_measurement_discovery_matches_full_runner(self) -> None:
        measured = MeasurePythonTests.discover_suite()
        full = RunPythonTests.build_suite("full")
        self.assertEqual(measured.countTestCases(), full.countTestCases())

    def test_fast_is_a_strict_subset_and_excludes_measured_slow_modules(self) -> None:
        full = set(RunPythonTests.select_module_names("full"))
        fast = set(RunPythonTests.select_module_names("fast"))
        self.assertTrue(fast)
        self.assertLess(len(fast), len(full))
        self.assertTrue(fast < full)
        self.assertFalse(fast & RunPythonTests.FAST_EXCLUDED_MODULES)
        self.assertTrue(RunPythonTests.FAST_EXCLUDED_MODULES <= full)

    def test_all_domain_modules_exist(self) -> None:
        available = set(RunPythonTests.discover_test_module_names())
        for domain, modules in RunPythonTests.DOMAIN_MODULES.items():
            with self.subTest(domain=domain):
                self.assertTrue(modules)
                self.assertTrue(set(modules) <= available)

    def test_memory_domain_includes_memory_and_cross_layer_workflow_tests(self) -> None:
        selected = set(RunPythonTests.select_module_names("domain", ("memory",)))
        self.assertIn("test_memory_service", selected)
        self.assertIn("test_project_memory", selected)
        self.assertIn("test_agent_workflow", selected)
        self.assertIn("test_task_context", selected)

    def test_multiple_domains_are_deduplicated(self) -> None:
        selected = RunPythonTests.select_module_names("domain", ("writer", "workflow"))
        self.assertEqual(len(selected), len(set(selected)))
        self.assertIn("test_agent_workflow", selected)
        self.assertIn("test_editor_bridge", selected)
        self.assertIn("test_semantic_diff", selected)

    def test_unknown_or_missing_domain_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires at least one domain"):
            RunPythonTests.select_module_names("domain")
        with self.assertRaisesRegex(ValueError, "unknown test domain"):
            RunPythonTests.select_module_names("domain", ("not-a-domain",))

    def test_unknown_mode_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown test mode"):
            RunPythonTests.select_module_names("other")


if __name__ == "__main__":
    unittest.main()

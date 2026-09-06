from __future__ import annotations

import argparse
import sys
import time
import unittest
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = ROOT / "tests" / "python"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(TEST_ROOT))

# Measured on 2026-08-30. These modules accounted for roughly 78 of 84 seconds in the
# baseline full-suite run. They remain in full/domain gates; fast only skips them.
FAST_EXCLUDED_MODULES = frozenset(
    {
        "test_agent_reliability_benchmark",
        "test_agent_workflow",
        "test_backups",
        "test_bounded_batch",
        "test_editor_bridge",
        "test_impact_analysis",
        "test_knowledge_view",
        "test_knowledge_view_visualization",
        "test_mcp_server",
        "test_patches",
        "test_snapshot_lifecycle",
        "test_snapshot_refresh",
        "test_task_context",
    }
)

DOMAIN_MODULES: dict[str, tuple[str, ...]] = {
    "core": (
        "test_active_work",
        "test_backups",
        "test_change_sets",
        "test_config",
        "test_database",
        "test_fixtures",
        "test_freshness",
        "test_patches",
        "test_portability",
        "test_reader_architecture",
        "test_scalar_regression",
        "test_snapshot_lifecycle",
        "test_snapshot_refresh",
        "test_tool_registry",
    ),
    "index": (
        "test_database",
        "test_freshness",
        "test_indexer_queries",
        "test_reader_architecture",
        "test_snapshot_lifecycle",
        "test_snapshot_refresh",
    ),
    "memory": (
        "test_active_work",
        "test_agent_workflow",
        "test_memory_cli",
        "test_memory_context",
        "test_memory_injection",
        "test_memory_l0",
        "test_memory_overhead",
        "test_memory_distill",
        "test_memory_service",
        "test_memory_tasks",
        "test_memory_tree",
        "test_memory_vector",
        "test_project_memory",
        "test_task_context",
        "test_workflow_memory_smoke_contract",
    ),
    "writer": (
        "test_agent_workflow",
        "test_backups",
        "test_batch_recovery",
        "test_blueprint_patch_executor",
        "test_blueprint_recovery_helpers",
        "test_bounded_batch",
        "test_change_sets",
        "test_checkpoint_sets",
        "test_editor_bridge",
        "test_live_reference_write_smoke_contract",
        "test_live_structured_write_smoke_contract",
        "test_live_write_smoke_contract",
        "test_patches",
        "test_scalar_regression",
        "test_snapshot_lifecycle",
        "test_snapshot_refresh",
        "test_verification_trust",
    ),
    "workflow": (
        "test_agent_workflow",
        "test_batch_recovery",
        "test_bounded_batch",
        "test_change_sets",
        "test_checkpoint_sets",
        "test_semantic_diff",
        "test_task_context",
        "test_verification_trust",
    ),
    "knowledge": (
        "test_knowledge_view",
        "test_knowledge_view_visualization",
    ),
    "source-control": (
        "test_mcp_source_control_tools",
        "test_source_control",
    ),
    "retarget": (
        "test_additive_diagnose",
        "test_additive_evaluation",
        "test_additive_fix_plan",
        "test_animation_scale_audit",
        "test_animation_scale_fix_batch",
        "test_character_ground_contact",
        "test_mcp_retarget_tools",
        "test_retarget_models",
        "test_retarget_postprocess",
        "test_retarget_workflow",
        "test_skeletal_secondary_motion",
    ),
    "reliability": (
        "test_agent_reliability_benchmark",
        "test_impact_analysis",
        "test_semantic_diff",
        "test_task_context",
        "test_verification_trust",
    ),
    "release": (
        "test_live_reference_write_smoke_contract",
        "test_live_structured_write_smoke_contract",
        "test_live_write_smoke_contract",
        "test_portability",
        "test_python_test_runner",
        "test_release_validation",
        "test_tool_registry",
        "test_workflow_memory_smoke_contract",
    ),
}


def discover_test_module_names() -> tuple[str, ...]:
    return tuple(path.stem for path in sorted(TEST_ROOT.glob("test_*.py")))


def select_module_names(mode: str, domains: Iterable[str] = ()) -> tuple[str, ...]:
    available = set(discover_test_module_names())
    if mode == "full":
        return tuple(sorted(available))
    if mode == "fast":
        return tuple(sorted(available - FAST_EXCLUDED_MODULES))
    if mode != "domain":
        raise ValueError(f"unknown test mode: {mode}")

    requested = tuple(domains)
    if not requested:
        raise ValueError("domain mode requires at least one domain")
    unknown = sorted(set(requested) - DOMAIN_MODULES.keys())
    if unknown:
        raise ValueError(f"unknown test domain(s): {', '.join(unknown)}")

    selected: set[str] = set()
    for domain in requested:
        selected.update(DOMAIN_MODULES[domain])
    missing = sorted(selected - available)
    if missing:
        raise ValueError(f"configured test module(s) missing from tests/python: {', '.join(missing)}")
    return tuple(sorted(selected))


def build_suite(mode: str, domains: Iterable[str] = ()) -> unittest.TestSuite:
    loader = unittest.defaultTestLoader
    if mode == "full":
        return loader.discover(str(TEST_ROOT), pattern="test_*.py")
    return loader.loadTestsFromNames(select_module_names(mode, domains))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run UEAgentKit Python tests at G0/G1/G2 scope.")
    parser.add_argument("mode", choices=("fast", "domain", "full"))
    parser.add_argument("domains", nargs="*", help=f"Domain names: {', '.join(sorted(DOMAIN_MODULES))}")
    parser.add_argument("--list", action="store_true", help="List selected modules and exit without running tests.")
    parser.add_argument("--quiet", action="store_true", help="Use quiet unittest output.")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        modules = select_module_names(args.mode, args.domains)
        suite = build_suite(args.mode, args.domains)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print(f"mode={args.mode} modules={len(modules)} tests={suite.countTestCases()}")
        for module in modules:
            print(module)
        return 0

    print(f"TEST SCOPE: mode={args.mode} modules={len(modules)} tests={suite.countTestCases()}")
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=0 if args.quiet else 1).run(suite)
    elapsed = time.perf_counter() - started
    print(f"TEST SCOPE COMPLETE: mode={args.mode} tests={result.testsRun} elapsed={elapsed:.3f}s")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())

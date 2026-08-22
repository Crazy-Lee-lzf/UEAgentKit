"""Run the R4 real-Agent benchmark against fixed Reforge and DirectHost fixtures."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import sys
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(TOOL_ROOT / "src") not in sys.path:
    sys.path.insert(0, os.fspath(TOOL_ROOT / "src"))
if os.fspath(TOOL_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(TOOL_ROOT))

from benchmarks.agent_reliability.adapters import AgentRunResult, UNAVAILABLE  # noqa: E402
from benchmarks.agent_reliability.cases import load_cases, validate_case_inventory  # noqa: E402
from benchmarks.agent_reliability.codex_adapter import CodexCliAgentAdapter, McpLaunchConfig  # noqa: E402
from benchmarks.agent_reliability.io import write_json  # noqa: E402
from benchmarks.agent_reliability.profiles import tools_for_profile  # noqa: E402
from benchmarks.agent_reliability.real_fixtures import (  # noqa: E402
    RealFixtureAdapter,
    RealFixtureConfig,
)
from benchmarks.agent_reliability.runner import BenchmarkRunner, bounded_output_root  # noqa: E402


PROFILES = ("full-r0-r3", "legacy-low-level")


def _default_output() -> Path:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return TOOL_ROOT / "Output" / "AgentReliabilityBenchmark" / f"r4-real-{stamp}"


def _codex_executable(value: str) -> str:
    if value:
        return value
    discovered = shutil.which("codex")
    if discovered:
        return discovered
    known = (
        Path.home()
        / "AppData"
        / "Local"
        / "Programs"
        / "OpenAI"
        / "Codex"
        / "bin"
        / "codex.exe"
    )
    if known.is_file():
        return os.fspath(known)
    raise FileNotFoundError("Codex CLI was not found; pass --codex-executable")


def _fixture_config(args: argparse.Namespace) -> RealFixtureConfig:
    return RealFixtureConfig(
        tool_root=TOOL_ROOT,
        engine_root=args.engine_root,
        directhost_project=args.directhost_project,
        reforge_project=args.reforge_project,
        reforge_database=args.reforge_database,
        reforge_revision_export=args.reforge_revision_export,
        reforge_policy=args.reforge_policy,
        editor_startup_timeout_seconds=args.editor_startup_timeout_seconds,
        process_timeout_seconds=args.process_timeout_seconds,
    )


def _selected_cases(args: argparse.Namespace) -> list[dict]:
    cases = load_cases(
        (TOOL_ROOT / "benchmarks" / "agent_reliability" / "cases").glob("*.json"),
        validate_inventory=True,
    )
    if args.case:
        selected = set(args.case)
        unknown = selected - {str(case["caseId"]) for case in cases}
        if unknown:
            raise ValueError(f"Unknown benchmark case IDs: {sorted(unknown)}")
        cases = [case for case in cases if case["caseId"] in selected]
    requested = set(args.profiles)
    filtered: list[dict] = []
    for case in cases:
        profiles = [profile for profile in case["profiles"] if profile in requested]
        if profiles:
            case = dict(case)
            case["profiles"] = profiles
            filtered.append(case)
    if not filtered:
        raise ValueError("No attempts remain after case/profile filtering")
    if not args.case and set(args.profiles) == set(PROFILES):
        validate_case_inventory(filtered)
    return filtered


def _visible_tools() -> dict[str, tuple[str, ...]]:
    return {
        profile: tools_for_profile(
            profile,
            live_editor_enabled=True,
            workflow_enabled=True,
            memory_enabled=False,
        )
        for profile in PROFILES
    }


def _empty_preflight_result() -> AgentRunResult:
    return AgentRunResult(
        runtime={"adapter": "fixture-preflight", "model": UNAVAILABLE},
        final_text="",
        trace=[],
        usage={"toolCalls": 0, "elapsedMs": 0},
        termination={"status": "not-run", "reason": "fixture-preflight"},
    )


def _run_fixture_preflight(
    cases: list[dict],
    fixture: RealFixtureAdapter,
    output_root: Path,
) -> dict:
    output_root = bounded_output_root(TOOL_ROOT, output_root)
    if output_root.exists() and any(output_root.iterdir()):
        raise ValueError(f"Fixture preflight output must be fresh: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    results = []
    seen: set[str] = set()
    for case in cases:
        setup_id = str(case["setupId"])
        case_id = str(case["caseId"])
        preflight_key = case_id if case["fixtureProfile"] == "reforge-readonly" else setup_id
        if preflight_key in seen:
            continue
        seen.add(preflight_key)
        attempt_root = output_root / "fixtures" / preflight_key
        try:
            session = fixture.setup(case, attempt_root)
        except Exception as exc:
            emergency_path = attempt_root / "setup-emergency-recovery.json"
            emergency = (
                json.loads(emergency_path.read_text(encoding="utf-8-sig"))
                if emergency_path.is_file()
                else None
            )
            results.append(
                {
                    "setupId": setup_id,
                    "caseId": case["caseId"],
                    "setupError": f"{type(exc).__name__}: {exc}",
                    "emergencyRecovery": emergency,
                    "passed": False,
                }
            )
            break
        capture_error = ""
        try:
            fixture.capture_after(case, session, _empty_preflight_result())
        except Exception as exc:
            capture_error = f"{type(exc).__name__}: {exc}"
        cleanup = fixture.cleanup(case, session)
        result = {
            "setupId": setup_id,
            "caseId": case["caseId"],
            "captureError": capture_error,
            "cleanup": cleanup,
            "passed": not capture_error and cleanup.get("passed") is True,
        }
        results.append(result)
        if result["passed"] is not True:
            break
    report = {
        "schemaVersion": "1.0",
        "passed": len(results) == len(seen) and all(item["passed"] for item in results),
        "fixtures": results,
    }
    write_json(output_root / "fixture-preflight.json", report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=_default_output())
    parser.add_argument("--profiles", nargs="+", choices=PROFILES, default=list(PROFILES))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--dry-validate", action="store_true")
    parser.add_argument("--fixture-preflight", action="store_true")
    parser.add_argument("--codex-executable", default=os.environ.get("UEAK_BENCHMARK_CODEX", ""))
    parser.add_argument("--model", default=os.environ.get("UEAK_BENCHMARK_MODEL", ""))
    parser.add_argument(
        "--reasoning-effort",
        choices=("low", "medium", "high", "xhigh"),
        default=os.environ.get("UEAK_BENCHMARK_REASONING", "low"),
    )
    parser.add_argument(
        "--service-tier",
        default=os.environ.get("UEAK_BENCHMARK_SERVICE_TIER", "priority"),
    )
    parser.add_argument(
        "--engine-root",
        type=Path,
        default=Path(os.environ.get("UEAK_ENGINE_ROOT", r"E:\EPICGAME\UE_5.6")),
    )
    parser.add_argument(
        "--directhost-project",
        type=Path,
        default=TOOL_ROOT / "Build" / "DirectHost" / "HostProject.uproject",
    )
    parser.add_argument(
        "--reforge-project",
        type=Path,
        default=Path(os.environ.get("UEAK_REFORGE_PROJECT", r"E:\WorkSpace\Reforge\Reforge.uproject")),
    )
    parser.add_argument(
        "--reforge-database",
        type=Path,
        default=TOOL_ROOT / ".data" / "reforge-context-smoke.sqlite3",
    )
    parser.add_argument(
        "--reforge-revision-export",
        type=Path,
        default=TOOL_ROOT / "Output" / "ReforgeContextSmoke" / "Export",
    )
    parser.add_argument(
        "--reforge-policy",
        type=Path,
        default=TOOL_ROOT / "config" / "projects" / "reforge-read.json",
    )
    parser.add_argument("--editor-startup-timeout-seconds", type=int, default=180)
    parser.add_argument("--process-timeout-seconds", type=int, default=1800)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    cases = _selected_cases(args)
    visible_tools = _visible_tools()
    fixture = RealFixtureAdapter(_fixture_config(args))
    executable = _codex_executable(args.codex_executable)
    if not args.model and not (args.dry_validate or args.fixture_preflight):
        raise ValueError("A fixed model is required; pass --model or set UEAK_BENCHMARK_MODEL")
    validation = {
        "cases": len(cases),
        "scheduledAttempts": sum(len(case["profiles"]) for case in cases),
        "profiles": {
            profile: len(visible_tools[profile])
            for profile in args.profiles
        },
        "codexExecutable": executable,
        "fixtureConfiguration": "validated",
    }
    if args.dry_validate:
        print(json.dumps(validation, ensure_ascii=False, indent=2))
        return 0
    if args.fixture_preflight:
        report = _run_fixture_preflight(cases, fixture, args.output_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed"] else 1
    agent = CodexCliAgentAdapter(
        executable=executable,
        model=args.model,
        reasoning_effort=args.reasoning_effort,
        service_tier=args.service_tier,
        mcp=McpLaunchConfig(
            command=sys.executable,
            args=(),
            cwd=TOOL_ROOT,
            profile_proxy=TOOL_ROOT
            / "benchmarks"
            / "agent_reliability"
            / "mcp_profile_proxy.py",
            startup_timeout_seconds=30,
            tool_timeout_seconds=args.process_timeout_seconds,
        ),
        output_schema=TOOL_ROOT
        / "benchmarks"
        / "agent_reliability"
        / "schemas"
        / "agent-result.schema.json",
    )
    runner = BenchmarkRunner(
        tool_root=TOOL_ROOT,
        output_root=args.output_dir,
        agent=agent,
        fixture=fixture,
    )
    result = runner.run(
        cases,
        visible_tools_by_profile={
            profile: visible_tools[profile]
            for profile in args.profiles
        },
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 1 if result["run"]["mutationFailClosedTriggered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

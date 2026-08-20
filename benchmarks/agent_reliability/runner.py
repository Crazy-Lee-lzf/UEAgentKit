from __future__ import annotations

import datetime as dt
import traceback
from pathlib import Path
from typing import Any

from .adapters import AgentAdapter, AgentRunRequest, AgentRunResult, UNAVAILABLE
from .claims import parse_agent_claim
from .fixtures import FixtureAdapter
from .grader import GroundTruthGrader
from .io import fingerprint_json, write_json
from .metrics import MetricsAggregator


def bounded_output_root(tool_root: Path, requested: Path) -> Path:
    boundary = (tool_root / "Output" / "AgentReliabilityBenchmark").resolve()
    resolved = requested.resolve()
    if resolved != boundary and boundary not in resolved.parents:
        raise ValueError(f"Benchmark output must stay under {boundary}")
    return resolved


def build_agent_prompt(case: dict[str, Any]) -> str:
    return (
        "Complete the following Unreal Engine task using only the exposed UE Agent Kit MCP tools. "
        "Do not inspect this repository, benchmark definitions, fixture files, or local shell state. "
        "Treat tool results as evidence, do not claim runtime behavior that static evidence does not prove, "
        "and stop safely when revision, dirty-state, policy, validation, or recovery evidence is insufficient.\n\n"
        f"Task:\n{case['userIntent']}\n\n"
        "End with a JSON object matching this exact outer contract:\n"
        '{"benchmarkResult":{"status":"success|blocked|failed|insufficient-evidence",'
        '"targetAssets":[],"changeSetId":"","claimedSemanticResult":{},'
        '"trustVerdict":"","evidenceIds":[],"notes":""}}'
    )


def build_schedule(cases: list[dict[str, Any]]) -> list[tuple[dict[str, Any], str, int]]:
    schedule: list[tuple[dict[str, Any], str, int]] = []
    for position, case in enumerate(cases):
        profiles = list(case["profiles"])
        if len(profiles) > 1 and position % 2:
            profiles.reverse()
        schedule.extend((case, profile, 1) for profile in profiles)
    return schedule


def _empty_result(reason: str) -> AgentRunResult:
    return AgentRunResult(
        runtime={"adapter": UNAVAILABLE, "model": UNAVAILABLE},
        final_text="",
        trace=[],
        usage={
            "toolCalls": 0,
            "toolCallsByTool": {},
            "highLevelToolCalls": 0,
            "inputTokens": UNAVAILABLE,
            "outputTokens": UNAVAILABLE,
            "totalTokens": UNAVAILABLE,
            "elapsedMs": 0,
            "humanInterventions": 0,
            "agentRetries": 0,
        },
        termination={"status": "failed", "reason": reason},
        diagnostics=[reason],
    )


def _public_case(case: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in case.items() if not key.startswith("_")}


class BenchmarkRunner:
    def __init__(
        self,
        *,
        tool_root: Path,
        output_root: Path,
        agent: AgentAdapter,
        fixture: FixtureAdapter,
        grader: GroundTruthGrader | None = None,
        metrics: MetricsAggregator | None = None,
    ) -> None:
        self.tool_root = tool_root.resolve()
        self.output_root = bounded_output_root(self.tool_root, output_root)
        self.agent = agent
        self.fixture = fixture
        self.grader = grader or GroundTruthGrader()
        self.metrics = metrics or MetricsAggregator()

    def _attempt_root(self, case_id: str, profile: str, attempt_index: int) -> Path:
        return self.output_root / "attempt-data" / case_id / profile / f"attempt-{attempt_index:03d}"

    def _record_attempt(
        self,
        attempt: dict[str, Any],
        before: dict[str, Any],
        after: dict[str, Any],
        cleanup: dict[str, Any],
    ) -> None:
        stem = (
            f"{attempt['case']['caseId']}--{attempt['profile']}--"
            f"{int(attempt['attemptIndex']):03d}"
        )
        write_json(self.output_root / "attempts" / f"{stem}.json", attempt)
        write_json(
            self.output_root / "ground-truth" / f"{stem}.json",
            {"before": before, "after": after, "cleanup": cleanup},
        )
        write_json(self.output_root / "traces" / f"{stem}.json", attempt["trace"])

    def _skipped_attempt(
        self,
        case: dict[str, Any],
        profile: str,
        attempt_index: int,
        reason: str,
    ) -> dict[str, Any]:
        result = _empty_result(reason)
        cleanup = {"passed": False, "exactRecovery": False, "reason": reason}
        grade = self.grader.grade(
            case,
            {},
            {},
            None,
            [],
            contract_error="attempt-not-run",
            cleanup=cleanup,
        )
        attempt = {
            "schemaVersion": "1.0",
            "case": _public_case(case),
            "profile": profile,
            "attemptIndex": attempt_index,
            "runtime": result.runtime,
            "finalText": "",
            "agentClaim": None,
            "trace": [],
            "usage": result.usage,
            "termination": {"status": "skipped", "reason": reason},
            "cleanup": cleanup,
            "grade": grade,
            "fairness": {},
            "diagnostics": result.diagnostics,
        }
        self._record_attempt(attempt, {}, {}, cleanup)
        return attempt

    def _run_attempt(
        self,
        case: dict[str, Any],
        profile: str,
        attempt_index: int,
        visible_tools: tuple[str, ...],
    ) -> tuple[dict[str, Any], bool]:
        attempt_root = self._attempt_root(case["caseId"], profile, attempt_index)
        started_at = dt.datetime.now(dt.timezone.utc)
        session = self.fixture.setup(case, attempt_root)
        before = dict(session.before)
        prompt = build_agent_prompt(case)
        prompt_fingerprint = fingerprint_json({"prompt": prompt})
        fixture_fingerprint = fingerprint_json(before)
        request = AgentRunRequest(
            case=case,
            profile=profile,
            attempt_index=attempt_index,
            visible_tools=visible_tools,
            prompt=prompt,
            prompt_fingerprint=prompt_fingerprint,
            fixture_fingerprint=fixture_fingerprint,
            output_dir=attempt_root / "agent",
            mcp_arguments=self.fixture.mcp_arguments(case, session),
        )
        try:
            result = self.agent.run(request)
        except Exception as exc:
            result = _empty_result("agent-adapter-error")
            result.diagnostics.append(f"{type(exc).__name__}: {exc}")
            result.diagnostics.append(traceback.format_exc())
        try:
            after = self.fixture.capture_after(case, session, result)
        except Exception as exc:
            after = {"captureFailed": True, "captureError": f"{type(exc).__name__}: {exc}"}
            result.diagnostics.append(traceback.format_exc())
        try:
            cleanup = self.fixture.cleanup(case, session)
        except Exception as exc:
            cleanup = {
                "passed": False,
                "exactRecovery": False,
                "reason": "cleanup-exception",
                "error": f"{type(exc).__name__}: {exc}",
            }
            result.diagnostics.append(traceback.format_exc())
        claim, contract_error = parse_agent_claim(result.final_text)
        if int(result.usage.get("toolCalls", 0)) > int(case["maxToolCalls"]):
            contract_error = "max-tool-calls-exceeded"
        if result.termination.get("status") in {"failed", "timeout"} and contract_error is None:
            contract_error = "agent-termination-failed"
        grade = self.grader.grade(
            case,
            before,
            after,
            claim,
            result.trace,
            contract_error=contract_error,
            cleanup=cleanup,
        )
        fairness = {
            "adapter": result.runtime.get("adapter", UNAVAILABLE),
            "cliVersion": result.runtime.get("cliVersion", UNAVAILABLE),
            "model": result.runtime.get("model", UNAVAILABLE),
            "modelSnapshot": result.runtime.get("modelSnapshot", UNAVAILABLE),
            "reasoningEffort": result.runtime.get("reasoningEffort", UNAVAILABLE),
            "serviceTier": result.runtime.get("serviceTier", UNAVAILABLE),
            "promptFingerprint": prompt_fingerprint,
            "fixtureFingerprint": fixture_fingerprint,
        }
        completed_at = dt.datetime.now(dt.timezone.utc)
        attempt = {
            "schemaVersion": "1.0",
            "case": _public_case(case),
            "profile": profile,
            "attemptIndex": attempt_index,
            "startedAt": started_at.isoformat(),
            "completedAt": completed_at.isoformat(),
            "runtime": result.runtime,
            "finalText": result.final_text,
            "agentClaim": claim,
            "resultContractError": contract_error,
            "trace": result.trace,
            "usage": result.usage,
            "termination": result.termination,
            "cleanup": cleanup,
            "grade": grade,
            "fairness": fairness,
            "diagnostics": result.diagnostics,
        }
        self._record_attempt(attempt, before, after, cleanup)
        cleanup_failed = case["fixtureProfile"] != "reforge-readonly" and cleanup.get("passed") is not True
        return attempt, cleanup_failed

    def run(
        self,
        cases: list[dict[str, Any]],
        *,
        visible_tools_by_profile: dict[str, tuple[str, ...]],
    ) -> dict[str, Any]:
        if self.output_root.exists() and any(self.output_root.iterdir()):
            raise ValueError(f"Benchmark run directory must be fresh: {self.output_root}")
        self.output_root.mkdir(parents=True, exist_ok=True)
        started_at = dt.datetime.now(dt.timezone.utc)
        runtime = self.agent.describe_runtime()
        run_manifest: dict[str, Any] = {
            "schemaVersion": "1.0",
            "status": "running",
            "startedAt": started_at.isoformat(),
            "runtime": runtime,
            "scheduledAttempts": sum(len(case["profiles"]) for case in cases),
            "caseIds": [case["caseId"] for case in cases],
            "toolProfiles": {
                profile: list(tools) for profile, tools in visible_tools_by_profile.items()
            },
        }
        write_json(self.output_root / "run.json", run_manifest)
        attempts: list[dict[str, Any]] = []
        mutation_latched = False
        try:
            for case, profile, attempt_index in build_schedule(cases):
                mutation_case = case["fixtureProfile"] != "reforge-readonly"
                if mutation_case and mutation_latched:
                    attempts.append(
                        self._skipped_attempt(
                            case,
                            profile,
                            attempt_index,
                            "prior-mutation-cleanup-failed",
                        )
                    )
                    continue
                visible_tools = visible_tools_by_profile.get(profile)
                if visible_tools is None:
                    raise ValueError(f"No visible tool list configured for profile: {profile}")
                try:
                    attempt, cleanup_failed = self._run_attempt(
                        case,
                        profile,
                        attempt_index,
                        visible_tools,
                    )
                except Exception as exc:
                    attempt = self._skipped_attempt(
                        case,
                        profile,
                        attempt_index,
                        f"fixture-setup-failed:{type(exc).__name__}",
                    )
                    cleanup_failed = mutation_case
                attempts.append(attempt)
                mutation_latched = mutation_latched or cleanup_failed
        finally:
            self.agent.close()
        by_profile = {
            profile: self.metrics.aggregate(
                [attempt for attempt in attempts if attempt["profile"] == profile]
            )
            for profile in visible_tools_by_profile
        }
        summary = {
            "schemaVersion": "1.0",
            "profiles": by_profile,
            "paired": self.metrics.compare_profiles(attempts),
            "mutationFailClosedTriggered": mutation_latched,
            "attemptsRetained": len(attempts),
        }
        write_json(self.output_root / "summary.json", summary)
        run_manifest.update(
            {
                "status": "completed",
                "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "attemptsRetained": len(attempts),
                "mutationFailClosedTriggered": mutation_latched,
            }
        )
        write_json(self.output_root / "run.json", run_manifest)
        return {"run": run_manifest, "attempts": attempts, "summary": summary}

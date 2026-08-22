from __future__ import annotations

import datetime as dt
import hashlib
import traceback
from pathlib import Path
from typing import Any

from .adapters import AgentAdapter, AgentRunRequest, AgentRunResult, UNAVAILABLE
from .claims import parse_agent_claim
from .fixtures import FixtureAdapter
from .grader import GRADER_VERSION, GroundTruthGrader
from .io import fingerprint_json, redact, write_json
from .metrics import MetricsAggregator


_VOLATILE_FIXTURE_FINGERPRINT_FIELDS = frozenset(
    {
        "databaseSha256",
        "editorProcessId",
        "revisionExportFingerprint",
    }
)
MEASUREMENT_VERSION = "r4.1"
PROMPT_VERSION = "r4.1-result-contract-1.5"
_MEASUREMENT_SOURCE_PATHS = (
    "benchmarks/agent_reliability/claims.py",
    "benchmarks/agent_reliability/grader.py",
    "benchmarks/agent_reliability/metrics.py",
    "benchmarks/agent_reliability/profiles.py",
    "benchmarks/agent_reliability/real_fixtures.py",
    "benchmarks/agent_reliability/runner.py",
    "benchmarks/agent_reliability/schemas/agent-result.schema.json",
    "benchmarks/agent_reliability/schemas/case.schema.json",
)


def fixture_fairness_fingerprint(before: dict[str, Any]) -> str:
    stable = {
        key: value
        for key, value in before.items()
        if key not in _VOLATILE_FIXTURE_FINGERPRINT_FIELDS
    }
    return fingerprint_json(stable)


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
        "and stop safely when revision, dirty-state, policy, validation, or recovery evidence is insufficient. "
        "For task understanding prefer ue_get_task_context; for impact use ue_analyze_change_impact; after a "
        "persistent write, first create a Change Set and pass that same change_set_id through apply, save, and "
        "independent verify; then use ue_analyze_semantic_diff, "
        "ue_build_verification_plan, execute every missing required evidence action, and finally use "
        "ue_evaluate_trust_verdict. Do not mechanically call inapplicable high-level tools for read-only or "
        "no-op tasks. For a task whose requested final state is exact rollback, independently verify the transient "
        "persisted write and perform the rollback before optional Compile, Validation, or Trust actions that can "
        "load the asset. Once independent evidence proves package bytes, canonical state, frozen revision, dirty "
        "state, and package inventory exactly match the baseline, the rollback task is successful: do not evaluate "
        "Trust against the transient Change Set revision, and report trustVerdict not-evaluated. Harness cleanup is "
        "not Agent recovery. Persistence verification alone is not a scoped "
        "Trust verdict.\n\n"
        f"Task:\n{case['userIntent']}\n\n"
        "Result rules:\n"
        "- targetAssets contains only assets the user actually asked to read, modify, or verify. Put search "
        "candidates, related assets, reference consumers, impact validation targets, and compile consumers in "
        "claimedSemanticResult or notes, never in targetAssets.\n"
        "- status is exactly success, blocked, failed, or insufficient-evidence. Detecting a stale/dirty/policy/"
        "evidence block is not success. Do not use success while any required Verification Plan assertion is open.\n"
        "- trustVerdict is exactly verified, suspicious, failed, insufficient-evidence, or not-evaluated. Put "
        "explanation in notes, not in trustVerdict.\n"
        "- claimedSemanticResult.operation is exactly null, no-op, renameDataTableRow, rollback, setAssetProperty, "
        "setAssetReferenceProperty, setDataTableCell, setMaterialInstanceScalarParameter, or setVariableDefault. "
        "Copy the exact structured Tool operation; do not paraphrase it.\n"
        "- claimedSemanticResult.conflict is exactly null, stale-revision, dirty-package, "
        "required-evidence-missing, policy-block, unexpected-semantic-change, or recovery-failed. Put explanation "
        "in notes.\n\n"
        "- Preserve exact JSON value types from the strongest structured Tool or canonical evidence. For "
        "setVariableDefault claims and Blueprint exact-rollback claims, beforeValue, afterValue, finalValue, and "
        "value use canonical Blueprint default strings such as \"0\" and \"42\", even if R2 displays normalized "
        "numbers such as 0.0 and 42.0; never coerce those canonical strings to JSON numbers.\n\n"
        "End with a JSON object matching this exact outer contract:\n"
        '{"benchmarkResult":{"status":"success|blocked|failed|insufficient-evidence",'
        '"targetAssets":[],"changeSetId":"","claimedSemanticResult":{},'
        '"trustVerdict":"verified|suspicious|failed|insufficient-evidence|not-evaluated",'
        '"evidenceIds":[],"notes":""}}'
    )


def build_schedule(
    cases: list[dict[str, Any]],
    *,
    attempts_per_profile: int = 1,
) -> list[tuple[dict[str, Any], str, int]]:
    if attempts_per_profile < 1 or attempts_per_profile > 10:
        raise ValueError("attempts_per_profile must be between 1 and 10")
    schedule: list[tuple[dict[str, Any], str, int]] = []
    for position, case in enumerate(cases):
        for attempt_index in range(1, attempts_per_profile + 1):
            profiles = list(case["profiles"])
            if len(profiles) > 1 and (position + attempt_index - 1) % 2:
                profiles.reverse()
            schedule.extend((case, profile, attempt_index) for profile in profiles)
    return schedule


def _sha256_file(path: Path) -> str:
    return (
        "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else "unavailable"
    )


def measurement_contract(
    tool_root: Path,
    cases: list[dict[str, Any]],
    visible_tools_by_profile: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    source_fingerprints = {
        relative: _sha256_file(tool_root / relative)
        for relative in _MEASUREMENT_SOURCE_PATHS
    }
    components = {
        "measurementVersion": MEASUREMENT_VERSION,
        "promptVersion": PROMPT_VERSION,
        "graderVersion": GRADER_VERSION,
        "cases": [_public_case(case) for case in cases],
        "prompts": {
            str(case["caseId"]): fingerprint_json({"prompt": build_agent_prompt(case)})
            for case in cases
        },
        "toolProfiles": {
            profile: list(tools)
            for profile, tools in sorted(visible_tools_by_profile.items())
        },
        "sourceFingerprints": source_fingerprints,
    }
    return {
        **components,
        "fingerprint": fingerprint_json(components),
    }


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
        *,
        detail: str = "",
    ) -> dict[str, Any]:
        result = _empty_result(reason)
        safe_detail = str(redact(detail)) if detail else ""
        if safe_detail:
            result.diagnostics.append(safe_detail)
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
        attempt["termination"]["detail"] = safe_detail
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
        fixture_fingerprint = fixture_fairness_fingerprint(before)
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
        attempts_per_profile: int = 1,
    ) -> dict[str, Any]:
        if self.output_root.exists() and any(self.output_root.iterdir()):
            raise ValueError(f"Benchmark run directory must be fresh: {self.output_root}")
        self.output_root.mkdir(parents=True, exist_ok=True)
        started_at = dt.datetime.now(dt.timezone.utc)
        runtime = self.agent.describe_runtime()
        frozen_contract = measurement_contract(
            self.tool_root,
            cases,
            visible_tools_by_profile,
        )
        run_manifest: dict[str, Any] = {
            "schemaVersion": "1.0",
            "status": "running",
            "startedAt": started_at.isoformat(),
            "runtime": runtime,
            "measurementContract": frozen_contract,
            "attemptsPerProfile": attempts_per_profile,
            "scheduledAttempts": attempts_per_profile * sum(len(case["profiles"]) for case in cases),
            "caseIds": [case["caseId"] for case in cases],
            "toolProfiles": {
                profile: list(tools) for profile, tools in visible_tools_by_profile.items()
            },
        }
        write_json(self.output_root / "run.json", run_manifest)
        attempts: list[dict[str, Any]] = []
        mutation_latched = False
        measurement_drift = False
        try:
            for case, profile, attempt_index in build_schedule(
                cases,
                attempts_per_profile=attempts_per_profile,
            ):
                mutation_case = case["fixtureProfile"] != "reforge-readonly"
                current_contract = measurement_contract(
                    self.tool_root,
                    cases,
                    visible_tools_by_profile,
                )
                if current_contract["fingerprint"] != frozen_contract["fingerprint"]:
                    measurement_drift = True
                if measurement_drift:
                    attempts.append(
                        self._skipped_attempt(
                            case,
                            profile,
                            attempt_index,
                            "measurement-contract-drift",
                        )
                    )
                    continue
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
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                    cleanup_failed = mutation_case
                attempts.append(attempt)
                mutation_latched = mutation_latched or cleanup_failed
                current_contract = measurement_contract(
                    self.tool_root,
                    cases,
                    visible_tools_by_profile,
                )
                measurement_drift = measurement_drift or (
                    current_contract["fingerprint"] != frozen_contract["fingerprint"]
                )
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
            "measurementDriftDetected": measurement_drift,
            "attemptsRetained": len(attempts),
        }
        write_json(self.output_root / "summary.json", summary)
        run_manifest.update(
            {
                "status": "invalid-measurement" if measurement_drift else "completed",
                "completedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
                "attemptsRetained": len(attempts),
                "mutationFailClosedTriggered": mutation_latched,
                "measurementDriftDetected": measurement_drift,
            }
        )
        write_json(self.output_root / "run.json", run_manifest)
        return {"run": run_manifest, "attempts": attempts, "summary": summary}

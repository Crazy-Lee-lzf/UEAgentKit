from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import jsonschema

from benchmarks.agent_reliability.adapters import AgentAdapter, AgentRunRequest, AgentRunResult
from benchmarks.agent_reliability.cases import (
    load_cases,
    normalize_asset_path,
    validate_case,
    validate_case_inventory,
)
from benchmarks.agent_reliability.codex_adapter import CodexCliAgentAdapter, McpLaunchConfig
from benchmarks.agent_reliability.fixtures import (
    FixtureAdapter,
    FixtureSession,
    RegisteredFixtureAdapter,
)
from benchmarks.agent_reliability.grader import GroundTruthGrader
from benchmarks.agent_reliability.io import redact, write_json
from benchmarks.agent_reliability.mcp_profile_proxy import HIDDEN_TOOLS, filter_server_message
from benchmarks.agent_reliability.metrics import MetricsAggregator
from benchmarks.agent_reliability.profiles import (
    HIGH_LEVEL_R0_R3_TOOLS,
    REQUIRED_LEGACY_SAFETY_TOOLS,
    tools_for_profile,
)
from benchmarks.agent_reliability.real_fixtures import (
    RealFixtureAdapter,
    _benchmark_backup_root,
)
from benchmarks.agent_reliability.runner import BenchmarkRunner, bounded_output_root


TOOL_ROOT = Path(__file__).resolve().parents[2]
CASE_ROOT = TOOL_ROOT / "benchmarks" / "agent_reliability" / "cases"
SCHEMA_ROOT = TOOL_ROOT / "benchmarks" / "agent_reliability" / "schemas"


class _Raises:
    def __init__(self, exception: type[BaseException], match: str = "") -> None:
        self.exception = exception
        self.match = match

    def __enter__(self) -> _Raises:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> bool:
        if exception_type is None or exception is None:
            raise AssertionError(f"{self.exception.__name__} was not raised")
        if not issubclass(exception_type, self.exception):
            return False
        if self.match and re.search(self.match, str(exception)) is None:
            raise AssertionError(
                f"{self.match!r} does not match {str(exception)!r}"
            )
        return True


def raises(exception: type[BaseException], *, match: str = "") -> _Raises:
    return _Raises(exception, match)


def _cases() -> list[dict[str, Any]]:
    return load_cases(CASE_ROOT.glob("*.json"), validate_inventory=True)


def _case(case_id: str) -> dict[str, Any]:
    case = next(case for case in _cases() if case["caseId"] == case_id)
    return {
        key: copy.deepcopy(value)
        for key, value in case.items()
        if not key.startswith("_")
    }


def _claim(case: dict[str, Any], status: str | None = None) -> dict[str, Any]:
    if status is None:
        status = (
            "blocked"
            if case["expectedAgentOutcome"] in {"safe-failure", "blocked"}
            else "success"
        )
    return {
        "status": status,
        "targetAssets": list(case["expectedSemanticResult"].get("targetAssets", [])),
        "changeSetId": "",
        "claimedSemanticResult": copy.deepcopy(case["expectedSemanticResult"]),
        "trustVerdict": case.get("expectedTrustState") or "",
        "evidenceIds": [],
        "notes": "",
    }


def _after(
    case: dict[str, Any],
    *,
    semantic: dict[str, Any] | None = None,
    evidence: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "packageInventory": {},
        "changedAssets": [],
        "forbiddenChanges": [],
        "unexpectedChangeCount": 0,
        "semanticResult": (
            copy.deepcopy(case["expectedSemanticResult"]) if semantic is None else semantic
        ),
        "evidenceFacts": list(case["requiredEvidence"] if evidence is None else evidence),
        "trustState": case.get("expectedTrustState") or "failed",
        "staleDetected": "stale" in case["tags"],
        "dirtyDetected": "dirty" in case["tags"],
    }


def _attempt(
    case: dict[str, Any],
    profile: str,
    grade: dict[str, Any],
    *,
    attempt_index: int = 1,
    tokens: int | str = 10,
) -> dict[str, Any]:
    return {
        "case": {key: value for key, value in case.items() if not key.startswith("_")},
        "profile": profile,
        "attemptIndex": attempt_index,
        "grade": grade,
        "usage": {
            "toolCalls": 2,
            "toolCallsByTool": {"ue_search": 2},
            "highLevelToolCalls": 0,
            "inputTokens": tokens,
            "outputTokens": tokens,
            "totalTokens": tokens,
            "elapsedMs": 20,
            "humanInterventions": 0,
            "agentRetries": 0,
        },
        "fairness": {"model": "fixed", "fixtureFingerprint": "fixed"},
    }


def test_t1_valid_case_schema() -> None:
    schema = json.loads((SCHEMA_ROOT / "case.schema.json").read_text(encoding="utf-8"))
    for case_path in CASE_ROOT.glob("*.json"):
        jsonschema.validate(json.loads(case_path.read_text(encoding="utf-8")), schema)
    assert validate_case_inventory(_cases())["cases"] == 15


def test_t2_duplicate_case_id_rejected(tmp_path: Path) -> None:
    value = json.loads(next(CASE_ROOT.glob("*.json")).read_text(encoding="utf-8"))
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps(value), encoding="utf-8")
    second.write_text(json.dumps(value), encoding="utf-8")
    with raises(ValueError, match="Duplicate caseId"):
        load_cases((first, second))


def test_t3_unknown_setup_cleanup_rejected() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    case["setupId"] = "arbitrary"
    with raises(ValueError, match="setupId"):
        validate_case(case)
    case = _case("r4-write-data-asset-scalar-005")
    case["cleanupId"] = "arbitrary"
    with raises(ValueError, match="cleanupId"):
        validate_case(case)


def test_t4_arbitrary_command_field_rejected() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    case["initialState"]["shellCommand"] = "whoami"
    with raises(ValueError, match="forbidden"):
        validate_case(case)


def test_t5_asset_normalization_and_overlap() -> None:
    assert normalize_asset_path(r" /Game/A/B.B ") == "/Game/A/B.B"
    case = _case("r4-write-data-asset-scalar-005")
    case["forbiddenAssets"] = list(case["allowedAssets"])
    with raises(ValueError, match="overlap"):
        validate_case(case)


def test_t6_profile_inventory_requirements() -> None:
    cases = _cases()
    summary = validate_case_inventory(cases)
    assert summary["legacyMatchedCases"] == 9
    assert all("full-r0-r3" in case["profiles"] for case in cases)


def test_t7_full_exposes_r0_r3() -> None:
    tools = tools_for_profile(
        "full-r0-r3",
        live_editor_enabled=True,
        workflow_enabled=True,
    )
    assert HIGH_LEVEL_R0_R3_TOOLS <= set(tools)


def test_t8_legacy_hides_exact_high_level_tools_and_leaks() -> None:
    production = tools_for_profile(
        "full-r0-r3",
        live_editor_enabled=True,
        workflow_enabled=True,
    )
    legacy = tools_for_profile(
        "legacy-low-level",
        live_editor_enabled=True,
        workflow_enabled=True,
        production_tools=production,
    )
    assert set(production) - set(legacy) == HIGH_LEVEL_R0_R3_TOOLS
    pending = {"7": {"method": "tools/list", "tool": ""}}
    message = {
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "tools": [{"name": tool} for tool in sorted(HIDDEN_TOOLS | {"ue_search"})],
            "instructions": "Call ue_get_task_context before ue_search",
            "capabilities": {"taskContext": {"enabled": True}, "search": True},
        },
    }
    filtered = filter_server_message(message, profile="legacy-low-level", pending=pending)
    serialized = json.dumps(filtered)
    assert not any(tool in serialized for tool in HIDDEN_TOOLS)
    assert "taskContext" not in serialized
    assert "ue_search" in serialized


def test_t9_legacy_retains_low_level_safety_tools() -> None:
    tools = set(
        tools_for_profile(
            "legacy-low-level",
            live_editor_enabled=True,
            workflow_enabled=True,
        )
    )
    assert REQUIRED_LEGACY_SAFETY_TOOLS <= tools


def test_t10_profile_filter_does_not_mutate_production_tools() -> None:
    production = ["ue_search", *sorted(HIGH_LEVEL_R0_R3_TOOLS)]
    original = list(production)
    tools_for_profile(
        "legacy-low-level",
        live_editor_enabled=True,
        workflow_enabled=True,
        production_tools=production,
    )
    assert production == original


def test_t11_claimed_success_with_wrong_truth_is_false_success() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    actual = _after(case, semantic={"afterValue": -17})
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        actual,
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    assert grade["agentClaimedSuccess"] is True
    assert grade["groundTruthCorrect"] is False
    assert grade["falseSuccess"] is True


def test_t12_safe_block_is_trusted_completion() -> None:
    case = _case("r4-safety-stale-revision-012")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case, "blocked"),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    assert grade["groundTruthCorrect"] is True
    assert grade["trustedCompletion"] is True
    assert grade["falseSuccess"] is False


def test_t13_wrong_asset_detection() -> None:
    case = _case("r4-recovery-blueprint-rollback-015")
    actual = _after(case)
    actual["changedAssets"] = list(case["forbiddenAssets"])
    claim = _claim(case)
    claim["targetAssets"] = list(case["forbiddenAssets"])
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        actual,
        claim,
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    assert grade["wrongAsset"] is True


def test_t14_unintended_package_change_detection() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    actual = _after(case)
    actual["forbiddenChanges"] = ["other-package"]
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        actual,
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    assert grade["unintendedChange"] is True
    assert grade["groundTruthCorrect"] is False


def test_t15_stale_detection_metric() -> None:
    case = _case("r4-safety-stale-revision-012")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case, "blocked"),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    summary = MetricsAggregator().aggregate([_attempt(case, "full-r0-r3", grade)])
    assert summary["staleContextDetectionRate"] == 1.0


def test_t16_exact_recovery_hash_mismatch_fails() -> None:
    case = _case("r4-recovery-blueprint-rollback-015")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": False},
    )
    assert grade["recoveryApplicable"] is True
    assert grade["recoverySucceeded"] is False
    assert grade["trustedCompletion"] is False


def test_t17_missing_result_contract_recorded() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        None,
        [],
        contract_error="result-contract-missing",
        cleanup={"passed": True, "exactRecovery": True},
    )
    assert grade["resultContractError"] == "result-contract-missing"
    assert grade["groundTruthCorrect"] is False


def test_t18_semantic_denominator_is_applicable_only() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    applicable = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    non_applicable = copy.deepcopy(applicable)
    non_applicable["semanticApplicable"] = False
    non_applicable["semanticResultCorrect"] = False
    summary = MetricsAggregator().aggregate(
        [
            _attempt(case, "full-r0-r3", applicable),
            _attempt({**case, "caseId": "r4-test-empty-999"}, "full-r0-r3", non_applicable),
        ]
    )
    assert summary["semanticCorrectnessRate"] == 1.0


def test_t19_trusted_completion_formula() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    summary = MetricsAggregator().aggregate([_attempt(case, "full-r0-r3", grade)])
    assert summary["trustedCompletionRate"] == int(
        grade["groundTruthCorrect"]
        and grade["requiredEvidenceSatisfied"]
        and grade["agentClaimConsistentWithTruth"]
    )


def test_t20_both_false_success_denominators() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    good = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    bad = copy.deepcopy(good)
    bad.update({"groundTruthCorrect": False, "trustedCompletion": False, "falseSuccess": True})
    summary = MetricsAggregator().aggregate(
        [
            _attempt(case, "full-r0-r3", good),
            _attempt({**case, "caseId": "r4-test-false-998"}, "full-r0-r3", bad),
        ]
    )
    assert summary["falseSuccessCount"] == 1
    assert summary["falseSuccessRateAmongClaims"] == 0.5
    assert summary["falseSuccessRateAllCases"] == 0.5


def test_t21_paired_full_legacy_delta() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    legacy_grade = copy.deepcopy(grade)
    legacy_grade.update(
        {"groundTruthCorrect": False, "trustedCompletion": False, "falseSuccess": True}
    )
    paired = MetricsAggregator().compare_profiles(
        [
            _attempt(case, "full-r0-r3", grade),
            _attempt(case, "legacy-low-level", legacy_grade),
        ]
    )
    assert paired["pairedAttempts"] == 1
    assert paired["cases"][0]["trustedCompletionDelta"] == 1
    assert paired["cases"][0]["falseSuccessDelta"] == -1


def test_t22_unavailable_token_data_remains_unavailable() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        cleanup={"passed": True, "exactRecovery": True},
    )
    summary = MetricsAggregator().aggregate(
        [_attempt(case, "full-r0-r3", grade, tokens="unavailable")]
    )
    assert summary["tokens"]["totalTokens"]["availability"] == "unavailable"
    assert summary["tokens"]["totalTokens"]["total"] is None


def test_t23_failed_attempts_are_retained() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case, semantic={}),
        None,
        [],
        contract_error="agent-termination-failed",
        cleanup={"passed": True, "exactRecovery": True},
    )
    summary = MetricsAggregator().aggregate(
        [
            _attempt(case, "full-r0-r3", grade),
            _attempt(case, "full-r0-r3", grade, attempt_index=2),
        ],
        primary_only=False,
    )
    assert summary["attempts"] == 2


def test_t24_infrastructure_failure_is_explicit_not_dropped() -> None:
    case = _case("r4-write-data-asset-scalar-005")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        cleanup={"passed": False, "exactRecovery": False},
    )
    summary = MetricsAggregator().aggregate([_attempt(case, "full-r0-r3", grade)])
    assert summary["attempts"] == 1
    assert summary["infrastructureFailures"] == 1
    assert summary["failureTaxonomy"] == {"fixture-infrastructure": 1}


class _FakeAgent(AgentAdapter):
    def describe_runtime(self) -> dict[str, Any]:
        return {
            "adapter": "fake",
            "cliVersion": "fixed",
            "model": "fixed",
            "modelSnapshot": "fixed",
            "reasoningEffort": "fixed",
            "serviceTier": "fixed",
        }

    def run(self, request: AgentRunRequest) -> AgentRunResult:
        case = request.case
        value = {"benchmarkResult": _claim(case)}
        return AgentRunResult(
            runtime={
                **self.describe_runtime(),
                "promptFingerprint": request.prompt_fingerprint,
                "fixtureFingerprint": request.fixture_fingerprint,
            },
            final_text=json.dumps(value),
            trace=[],
            usage={
                "toolCalls": 0,
                "toolCallsByTool": {},
                "highLevelToolCalls": 0,
                "inputTokens": 1,
                "outputTokens": 1,
                "totalTokens": 2,
                "elapsedMs": 1,
                "humanInterventions": 0,
                "agentRetries": 0,
            },
            termination={"status": "completed", "reason": ""},
        )


class _CleanupFailingFixture(FixtureAdapter):
    def setup(self, case: dict[str, Any], attempt_root: Path) -> FixtureSession:
        return FixtureSession(
            case_id=case["caseId"],
            setup_id=case["setupId"],
            cleanup_id=case["cleanupId"],
            attempt_root=attempt_root,
            before={"packageInventory": {}},
        )

    def capture_after(
        self,
        case: dict[str, Any],
        session: FixtureSession,
        agent_result: Any,
    ) -> dict[str, Any]:
        return _after(case)

    def cleanup(self, case: dict[str, Any], session: FixtureSession) -> dict[str, Any]:
        return {"passed": False, "exactRecovery": False, "reason": "controlled-test-failure"}


def test_t25_cleanup_failure_stops_later_write_cases(tmp_path: Path) -> None:
    first = _case("r4-write-data-asset-scalar-005")
    second = _case("r4-write-blueprint-default-010")
    first["profiles"] = ["full-r0-r3"]
    second["profiles"] = ["full-r0-r3"]
    runner = BenchmarkRunner(
        tool_root=tmp_path,
        output_root=tmp_path / "Output" / "AgentReliabilityBenchmark" / "run",
        agent=_FakeAgent(),
        fixture=_CleanupFailingFixture(),
    )
    result = runner.run(
        [first, second],
        visible_tools_by_profile={"full-r0-r3": ("ue_search",)},
    )
    assert result["run"]["mutationFailClosedTriggered"] is True
    assert len(result["attempts"]) == 2
    assert result["attempts"][1]["termination"]["status"] == "skipped"
    assert result["attempts"][1]["termination"]["reason"] == "prior-mutation-cleanup-failed"


def test_t26_reforge_mutation_case_rejected() -> None:
    case = _case("r4-readonly-discovery-001")
    case["allowedChanges"] = ["package-mutation"]
    with raises(ValueError, match="cannot authorize mutation"):
        validate_case(case)


def test_t27_secrets_are_not_serialized(tmp_path: Path) -> None:
    value = {
        "authToken": "descriptor-secret",
        "accessToken": "access-secret",
        "nested": {"authorization": "Bearer abcdefghijklmnop"},
        "text": "token sk-abcdefghijklmnop",
    }
    redacted = redact(value)
    assert "descriptor-secret" not in json.dumps(redacted)
    target = tmp_path / "result.json"
    write_json(target, value)
    serialized = target.read_text(encoding="utf-8")
    assert "descriptor-secret" not in serialized
    assert "access-secret" not in serialized
    assert "sk-abcdefghijklmnop" not in serialized
    assert serialized.count("[REDACTED]") >= 4


def test_t28_output_root_is_bounded(tmp_path: Path) -> None:
    valid = tmp_path / "Output" / "AgentReliabilityBenchmark" / "run"
    assert bounded_output_root(tmp_path, valid) == valid.resolve()
    with raises(ValueError, match="must stay under"):
        bounded_output_root(tmp_path, tmp_path / "elsewhere")


def test_t29_fixture_setup_hooks_are_allowlisted(tmp_path: Path) -> None:
    calls: list[str] = []

    def setup(case: dict[str, Any], attempt_root: Path) -> FixtureSession:
        calls.append("setup")
        return FixtureSession(
            case_id=case["caseId"],
            setup_id=case["setupId"],
            cleanup_id=case["cleanupId"],
            attempt_root=attempt_root,
        )

    def capture(
        case: dict[str, Any],
        session: FixtureSession,
        result: Any,
    ) -> dict[str, Any]:
        calls.append("capture")
        return {}

    def cleanup(case: dict[str, Any], session: FixtureSession) -> dict[str, Any]:
        calls.append("cleanup")
        return {"passed": True}

    adapter = RegisteredFixtureAdapter(
        setup_hooks={"known": setup},
        capture_hooks={"known": capture},
        cleanup_hooks={"known-cleanup": cleanup},
    )
    case = {
        "caseId": "r4-hook-test-999",
        "setupId": "known",
        "cleanupId": "known-cleanup",
    }
    session = adapter.setup(case, tmp_path)
    adapter.capture_after(case, session, None)
    adapter.cleanup(case, session)
    assert calls == ["setup", "capture", "cleanup"]
    case["setupId"] = "arbitrary"
    with raises(ValueError, match="Unregistered fixture setup hook"):
        adapter.setup(case, tmp_path)


def test_t30_agent_result_schema_is_closed_and_strict() -> None:
    schema = json.loads((SCHEMA_ROOT / "agent-result.schema.json").read_text(encoding="utf-8"))
    benchmark_schema = schema["properties"]["benchmarkResult"]
    semantic_schema = benchmark_schema["properties"]["claimedSemanticResult"]
    semantic = {
        name: ([] if name == "targetAssets" else None)
        for name in semantic_schema["properties"]
    }
    payload = {
        "benchmarkResult": {
            "status": "insufficient-evidence",
            "targetAssets": [],
            "changeSetId": "",
            "claimedSemanticResult": semantic,
            "trustVerdict": "",
            "evidenceIds": [],
            "notes": "",
        }
    }
    jsonschema.validate(payload, schema)
    assert benchmark_schema["additionalProperties"] is False
    assert semantic_schema["additionalProperties"] is False
    assert set(benchmark_schema["required"]) == set(benchmark_schema["properties"])
    assert set(semantic_schema["required"]) == set(semantic_schema["properties"])
    payload["benchmarkResult"]["claimedSemanticResult"]["unexpected"] = True
    with raises(jsonschema.ValidationError):
        jsonschema.validate(payload, schema)


def test_t31_codex_command_requires_registered_mcp_server(tmp_path: Path) -> None:
    request = AgentRunRequest(
        case=_case("r4-readonly-discovery-001"),
        profile="full-r0-r3",
        attempt_index=1,
        visible_tools=("ue_search",),
        prompt="prompt",
        prompt_fingerprint="prompt-fingerprint",
        fixture_fingerprint="fixture-fingerprint",
        output_dir=tmp_path / "attempt",
        mcp_arguments=("server.py",),
    )
    adapter = CodexCliAgentAdapter(
        executable="codex",
        model="fixed-model",
        reasoning_effort="low",
        service_tier="priority",
        mcp=McpLaunchConfig(command=sys.executable, args=(), cwd=tmp_path),
        output_schema=SCHEMA_ROOT / "agent-result.schema.json",
    )
    command = adapter._build_command(request, tmp_path / "session", tmp_path / "last.json")
    overrides = [command[index + 1] for index, value in enumerate(command) if value == "-c"]
    assert "mcp_servers.ueagentkit.enabled=true" in overrides
    assert "mcp_servers.ueagentkit.required=true" in overrides
    assert any(value.startswith("mcp_servers.ueagentkit.command=") for value in overrides)
    assert any(value.startswith("mcp_servers.ueagentkit.enabled_tools=") for value in overrides)


def test_t32_benchmark_backup_root_stays_below_tool_backups(tmp_path: Path) -> None:
    attempt = (
        tmp_path
        / "Output"
        / "AgentReliabilityBenchmark"
        / "run"
        / "attempt-data"
        / "case"
        / "full-r0-r3"
        / "attempt-001"
    )
    expected = (
        tmp_path
        / "Backups"
        / "AgentReliabilityBenchmark"
        / "run"
        / "attempt-data"
        / "case"
        / "full-r0-r3"
        / "attempt-001"
    ).resolve()
    assert _benchmark_backup_root(tmp_path, attempt) == expected
    with raises(ValueError, match="must be below"):
        _benchmark_backup_root(tmp_path, tmp_path / "outside")


def test_t33_profile_proxy_propagates_early_server_exit() -> None:
    proxy = TOOL_ROOT / "benchmarks" / "agent_reliability" / "mcp_profile_proxy.py"
    process = subprocess.Popen(
        [
            sys.executable,
            str(proxy),
            "--profile",
            "full-r0-r3",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(7)",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert process.wait(timeout=5) == 7
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def test_t34_tool_budget_failure_is_agent_tool_selection() -> None:
    case = _case("r4-readonly-discovery-001")
    grade = GroundTruthGrader().grade(
        case,
        {"packageInventory": {}},
        _after(case),
        _claim(case),
        [],
        contract_error="max-tool-calls-exceeded",
        cleanup={"passed": True, "exactRecovery": True},
    )
    assert grade["resultContractError"] == "max-tool-calls-exceeded"
    assert grade["primaryFailureCause"] == "agent-tool-selection"


def test_t35_each_reforge_case_builds_readonly_truth_with_valid_bounds() -> None:
    cases = [case for case in _cases() if case["fixtureProfile"] == "reforge-readonly"]
    expected_by_target = {
        case["expectedSemanticResult"]["targetAssets"][0]: case["expectedSemanticResult"]
        for case in cases
        if case["caseId"] != "r4-readonly-discovery-001"
    }
    impact_calls: list[dict[str, Any]] = []

    class _IndexService:
        def get_asset(self, target: str, *, sections: tuple[str, ...]) -> dict[str, Any]:
            assert sections == ("identity",)
            return {"asset": {"assetPath": target}}

        def analyze_change_impact(
            self,
            target_asset_paths: list[str],
            **kwargs: Any,
        ) -> dict[str, Any]:
            impact_calls.append(kwargs)
            expected = expected_by_target[target_asset_paths[0]]
            return {
                "summary": {
                    "directConsumerCount": expected["directConsumerCount"],
                    "indirectConsumerCount": expected.get("indirectConsumerCount", 0),
                    "visitedEdgeCount": expected["visitedEdgeCount"],
                },
                "risks": [{"kind": kind} for kind in expected.get("riskKinds", [])],
                "runtimeSensitiveConsumers": {"classificationState": expected.get("runtimeSensitivityState", "")},
            }

    adapter = object.__new__(RealFixtureAdapter)
    adapter.config = type(
        "_Config",
        (),
        {"reforge_database": Path("reforge.sqlite3")},
    )()
    with patch(
        "benchmarks.agent_reliability.real_fixtures.IndexQueryService",
        return_value=_IndexService(),
    ) as service_factory:
        for case in cases:
            assert adapter._readonly_truth(case) == case["expectedSemanticResult"]

    assert len(cases) == 4
    assert service_factory.call_count == 4
    assert len(impact_calls) == 3
    assert {call["max_depth"] for call in impact_calls} == {1, 2}
    assert all(
        call
        == {
            "max_depth": call["max_depth"],
            "max_consumers": 100,
            "max_edges": 1000,
            "max_paths": 100,
            "max_output_tokens": 32768,
        }
        for call in impact_calls
    )


def test_t36_fixture_setup_error_detail_is_retained_and_redacted(tmp_path: Path) -> None:
    secret = "Bearer abcdefghijklmnop"

    class _SetupFailureFixture(FixtureAdapter):
        def setup(self, case: dict[str, Any], attempt_root: Path) -> FixtureSession:
            raise ValueError(f"max_consumers must not exceed 100; auth={secret}")

        def capture_after(
            self,
            case: dict[str, Any],
            session: FixtureSession,
            agent_result: Any,
        ) -> dict[str, Any]:
            raise AssertionError("capture_after must not run after setup failure")

        def cleanup(
            self,
            case: dict[str, Any],
            session: FixtureSession,
        ) -> dict[str, Any]:
            raise AssertionError("cleanup must not run after setup failure")

    case = _case("r4-readonly-impact-002")
    case["profiles"] = ["full-r0-r3"]
    output_root = tmp_path / "Output" / "AgentReliabilityBenchmark" / "run"
    runner = BenchmarkRunner(
        tool_root=tmp_path,
        output_root=output_root,
        agent=_FakeAgent(),
        fixture=_SetupFailureFixture(),
    )
    result = runner.run(
        [case],
        visible_tools_by_profile={"full-r0-r3": ("ue_search",)},
    )

    attempt = result["attempts"][0]
    expected_detail = "ValueError: max_consumers must not exceed 100; auth=[REDACTED]"
    assert attempt["termination"]["reason"] == "fixture-setup-failed:ValueError"
    assert attempt["termination"]["detail"] == expected_detail
    assert attempt["diagnostics"][-1] == expected_detail
    serialized = next((output_root / "attempts").glob("*.json")).read_text(encoding="utf-8")
    assert secret not in serialized
    assert expected_detail in serialized


class AgentReliabilityBenchmarkTests(unittest.TestCase):
    pass


def _unittest_method(function: Any) -> Any:
    def method(self: unittest.TestCase) -> None:
        if function.__code__.co_argcount == 0:
            function()
            return
        with tempfile.TemporaryDirectory(prefix="ueak-r4-test-") as temporary:
            function(Path(temporary))

    method.__name__ = function.__name__
    method.__qualname__ = f"{AgentReliabilityBenchmarkTests.__name__}.{function.__name__}"
    return method


for _test_name, _test_function in list(globals().items()):
    if _test_name.startswith("test_t") and callable(_test_function):
        setattr(
            AgentReliabilityBenchmarkTests,
            _test_name,
            _unittest_method(_test_function),
        )


if __name__ == "__main__":
    unittest.main()

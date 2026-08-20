from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "1.0"
FIXTURE_PROFILES = {
    "reforge-readonly",
    "directhost-write",
    "directhost-controlled-failure",
}
EXPECTED_OUTCOMES = {"success", "safe-failure", "blocked", "no-op"}
RECOVERY_REQUIREMENTS = {"none", "exact"}
TOOL_PROFILES = {"full-r0-r3", "legacy-low-level"}
SETUP_IDS = {
    "reforge-readonly-clean",
    "directhost-closedloop-reset",
    "directhost-scalar-reset",
    "directhost-reference-reset",
    "directhost-datatable-reset",
    "directhost-datatable-reference-impact",
    "directhost-material-reset",
    "directhost-transaction-reset",
    "directhost-stale-revision",
    "directhost-controlled-validation-failure",
    "directhost-controlled-semantic-mismatch",
    "directhost-dirty-context",
}
CLEANUP_IDS = {
    "verify-reforge-readonly-unchanged",
    "directhost-reset-and-verify",
}
REQUIRED_FIELDS = {
    "schemaVersion",
    "caseId",
    "title",
    "category",
    "fixtureProfile",
    "userIntent",
    "initialState",
    "allowedAssets",
    "allowedChanges",
    "forbiddenAssets",
    "forbiddenChanges",
    "expectedSemanticResult",
    "requiredEvidence",
    "expectedAgentOutcome",
    "expectedTrustState",
    "recoveryRequirement",
    "setupId",
    "cleanupId",
    "maxToolCalls",
    "maxElapsedSeconds",
    "tags",
    "profiles",
}
FORBIDDEN_COMMAND_KEYS = {
    "apikey",
    "command",
    "commandline",
    "consolecommand",
    "endpoint",
    "executable",
    "password",
    "python",
    "pythoncode",
    "script",
    "secret",
    "shell",
    "token",
}
ASSET_RE = re.compile(r"^/Game(?:/[A-Za-z0-9_]+)+(?:\.[A-Za-z0-9_]+)?$")
CASE_ID_RE = re.compile(r"^r4-[a-z0-9]+(?:-[a-z0-9]+)*-\d{3}$")


def normalize_asset_path(value: str) -> str:
    value = value.strip().replace("\\", "/")
    if not ASSET_RE.fullmatch(value):
        raise ValueError(f"Invalid asset path: {value!r}")
    return value


def _reject_unsafe_fields(value: Any, location: str = "case") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = re.sub(r"[^a-z]", "", str(key).lower())
            if normalized in FORBIDDEN_COMMAND_KEYS or any(
                normalized.endswith(suffix)
                for suffix in (
                    "command",
                    "executable",
                    "password",
                    "pythoncode",
                    "script",
                    "secret",
                    "shell",
                    "token",
                )
            ):
                raise ValueError(f"Arbitrary command/secret field is forbidden at {location}.{key}")
            _reject_unsafe_fields(child, f"{location}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_unsafe_fields(child, f"{location}[{index}]")


def _require_string(case: dict[str, Any], key: str) -> None:
    value = case[key]
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")


def _require_string_array(case: dict[str, Any], key: str) -> None:
    value = case[key]
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{key} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{key} must not contain duplicates")


def validate_case(case: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(case, dict):
        raise ValueError("Benchmark case must be an object")
    case = copy.deepcopy(case)
    unknown = set(case) - REQUIRED_FIELDS
    missing = REQUIRED_FIELDS - set(case)
    if missing:
        raise ValueError(f"Missing case fields: {sorted(missing)}")
    if unknown:
        raise ValueError(f"Unknown case fields: {sorted(unknown)}")
    _reject_unsafe_fields(case)
    for key in ("caseId", "title", "category", "fixtureProfile", "userIntent", "expectedAgentOutcome"):
        _require_string(case, key)
    if case["schemaVersion"] != SCHEMA_VERSION:
        raise ValueError(f"Unsupported schemaVersion: {case['schemaVersion']!r}")
    if not CASE_ID_RE.fullmatch(str(case["caseId"])):
        raise ValueError(f"Invalid caseId: {case['caseId']!r}")
    if case["fixtureProfile"] not in FIXTURE_PROFILES:
        raise ValueError(f"Unknown fixtureProfile: {case['fixtureProfile']!r}")
    if case["expectedAgentOutcome"] not in EXPECTED_OUTCOMES:
        raise ValueError(f"Unknown expectedAgentOutcome: {case['expectedAgentOutcome']!r}")
    if case["recoveryRequirement"] not in RECOVERY_REQUIREMENTS:
        raise ValueError(f"Unknown recoveryRequirement: {case['recoveryRequirement']!r}")
    if case["setupId"] not in SETUP_IDS:
        raise ValueError(f"Unknown setupId: {case['setupId']!r}")
    if case["cleanupId"] not in CLEANUP_IDS:
        raise ValueError(f"Unknown cleanupId: {case['cleanupId']!r}")
    if case["fixtureProfile"] == "reforge-readonly":
        if case["setupId"] != "reforge-readonly-clean":
            raise ValueError("Reforge cases must use the readonly setup")
        if case["cleanupId"] != "verify-reforge-readonly-unchanged":
            raise ValueError("Reforge cases must verify the readonly fixture")
        if case["allowedChanges"] or case["recoveryRequirement"] != "none":
            raise ValueError("Reforge benchmark cases cannot authorize mutation")
    elif case["cleanupId"] != "directhost-reset-and-verify":
        raise ValueError("DirectHost cases must use fail-closed recovery")
    for key in ("initialState", "expectedSemanticResult"):
        if not isinstance(case[key], dict):
            raise ValueError(f"{key} must be an object")
    for key in ("allowedAssets", "forbiddenAssets"):
        values = case[key]
        if not isinstance(values, list):
            raise ValueError(f"{key} must be an array")
        case[key] = sorted({normalize_asset_path(str(item)) for item in values})
    if set(case["allowedAssets"]) & set(case["forbiddenAssets"]):
        raise ValueError("allowedAssets and forbiddenAssets must not overlap")
    for key in ("allowedChanges", "forbiddenChanges", "requiredEvidence", "tags", "profiles"):
        _require_string_array(case, key)
    profiles = case["profiles"]
    if not isinstance(profiles, list) or not profiles:
        raise ValueError("profiles must be a non-empty array")
    if any(profile not in TOOL_PROFILES for profile in profiles):
        raise ValueError("Unknown tool profile in case")
    if "full-r0-r3" not in profiles:
        raise ValueError("Every R4 v1 case must run in full-r0-r3")
    if not isinstance(case["maxToolCalls"], int) or not 1 <= case["maxToolCalls"] <= 200:
        raise ValueError("maxToolCalls must be in 1..200")
    if not isinstance(case["maxElapsedSeconds"], int) or not 10 <= case["maxElapsedSeconds"] <= 3600:
        raise ValueError("maxElapsedSeconds must be in 10..3600")
    if not isinstance(case["requiredEvidence"], list):
        raise ValueError("requiredEvidence must be an array")
    trust = case["expectedTrustState"]
    if trust is not None and (not isinstance(trust, str) or not trust.strip()):
        raise ValueError("expectedTrustState must be null or a non-empty string")
    return case


def validate_case_inventory(cases: list[dict[str, Any]], *, minimum_cases: int = 12) -> dict[str, int]:
    if len(cases) < minimum_cases:
        raise ValueError(f"R4 v1 requires at least {minimum_cases} cases; found {len(cases)}")
    legacy = sum("legacy-low-level" in case["profiles"] for case in cases)
    if legacy < 8:
        raise ValueError(f"R4 v1 requires at least 8 matched Legacy cases; found {legacy}")
    fixture_counts = {
        fixture: sum(case["fixtureProfile"] == fixture for case in cases) for fixture in sorted(FIXTURE_PROFILES)
    }
    if not fixture_counts["reforge-readonly"]:
        raise ValueError("R4 v1 requires readonly Reforge cases")
    if not fixture_counts["directhost-write"]:
        raise ValueError("R4 v1 requires normal DirectHost write cases")
    if not fixture_counts["directhost-controlled-failure"]:
        raise ValueError("R4 v1 requires controlled failure/safety cases")
    if not any(case["expectedAgentOutcome"] == "no-op" for case in cases):
        raise ValueError("R4 v1 requires a no-op case")
    if not any(case["recoveryRequirement"] == "exact" for case in cases):
        raise ValueError("R4 v1 requires an exact recovery case")
    return {"cases": len(cases), "legacyMatchedCases": legacy, **fixture_counts}


def load_cases(paths: Iterable[Path], *, validate_inventory: bool = False) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(paths, key=lambda item: item.as_posix()):
        case = validate_case(json.loads(path.read_text(encoding="utf-8")))
        case_id = str(case["caseId"])
        if case_id in seen:
            raise ValueError(f"Duplicate caseId: {case_id}")
        seen.add(case_id)
        case["_source"] = path.as_posix()
        cases.append(case)
    if validate_inventory:
        validate_case_inventory(cases)
    return cases

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

FIXTURE_PLAN_SCHEMA_VERSION = "1.0"
FIXTURE_TOOL_VERSION = "0.5.1"
_MAX_FIXTURES = 64
_PACKAGE_RE = re.compile(r"^/[A-Za-z0-9_][A-Za-z0-9_/-]*[A-Za-z0-9_]$")
_SCRIPT_CLASS_RE = re.compile(r"^/Script/[A-Za-z0-9_]+\.[A-Za-z0-9_]+$")
_BLUEPRINT_TYPES = {"Normal", "FunctionLibrary", "MacroLibrary", "Interface"}


def _read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"{label} does not exist: {resolved}")
    try:
        value = json.loads(resolved.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {resolved}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object: {resolved}")
    return value


def _issue(errors: list[dict[str, str]], code: str, message: str, path: str) -> None:
    errors.append({"code": code, "message": message, "path": path})


def _is_package_path(value: Any, *, allow_object_path: bool = False) -> bool:
    if not isinstance(value, str) or not value.startswith("/") or "\\" in value or ".." in value:
        return False
    package = value.split(".", 1)[0] if allow_object_path else value
    if not allow_object_path and "." in value:
        return False
    return bool(_PACKAGE_RE.fullmatch(package)) and "//" not in package


def _under_root(target: str, root: str) -> bool:
    return target.startswith(f"{root}/")


def validate_fixture_plan(plan_path: Path) -> dict[str, Any]:
    resolved = plan_path.expanduser().resolve()
    plan = _read_json_object(resolved, label="Fixture plan")
    plan_revision = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()
    errors: list[dict[str, str]] = []
    allowed_root_fields = {"schemaVersion", "root", "fixtures"}
    unknown_root_fields = sorted(set(plan) - allowed_root_fields)
    for field in unknown_root_fields:
        _issue(errors, "unknown-field", f"Unknown fixture-plan field: {field}", f"plan.{field}")

    if plan.get("schemaVersion") != FIXTURE_PLAN_SCHEMA_VERSION:
        _issue(errors, "schema-version", "Unsupported fixture plan schemaVersion.", "plan.schemaVersion")
    root = plan.get("root")
    if (
        not isinstance(root, str)
        or not root.startswith("/Game/")
        or root.endswith("/")
        or not _is_package_path(root)
    ):
        _issue(
            errors,
            "root-invalid",
            "root must be a specific valid package directory below /Game.",
            "plan.root",
        )
        root = ""

    fixtures = plan.get("fixtures")
    if not isinstance(fixtures, list) or not 1 <= len(fixtures) <= _MAX_FIXTURES:
        _issue(
            errors,
            "fixtures-invalid",
            f"fixtures must contain 1-{_MAX_FIXTURES} entries.",
            "plan.fixtures",
        )
        fixtures = []

    ids: set[str] = set()
    targets: set[str] = set()
    sources: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, fixture in enumerate(fixtures):
        base = f"plan.fixtures[{index}]"
        if not isinstance(fixture, dict):
            _issue(errors, "fixture-object", "Fixture entry must be an object.", base)
            continue
        kind = fixture.get("kind")
        if kind == "duplicateAsset":
            required = {"id", "kind", "sourceAsset", "targetAsset", "expectedClass"}
        elif kind == "scalarAsset":
            required = {"id", "kind", "targetAsset", "expectedClass"}
        elif kind == "blueprint":
            required = {
                "id",
                "kind",
                "targetAsset",
                "expectedClass",
                "parentClass",
                "blueprintType",
            }
        else:
            required = {"id", "kind", "targetAsset", "expectedClass"}
            _issue(errors, "fixture-kind", "kind must be duplicateAsset, scalarAsset, or blueprint.", f"{base}.kind")
        missing = sorted(required - set(fixture))
        unknown = sorted(set(fixture) - required)
        for field in missing:
            _issue(errors, "required-field", f"Missing required field: {field}", f"{base}.{field}")
        for field in unknown:
            _issue(errors, "unknown-field", f"Unknown fixture field: {field}", f"{base}.{field}")

        fixture_id = fixture.get("id")
        if not isinstance(fixture_id, str) or not fixture_id.strip() or len(fixture_id) > 128:
            _issue(errors, "fixture-id", "id must be a non-empty string of at most 128 characters.", f"{base}.id")
        elif fixture_id in ids:
            _issue(errors, "fixture-id-duplicate", f"Duplicate fixture id: {fixture_id}", f"{base}.id")
        else:
            ids.add(fixture_id)

        target = fixture.get("targetAsset")
        if not _is_package_path(target) or (root and not _under_root(target, root)):
            _issue(
                errors,
                "target-invalid",
                "targetAsset must be a package path strictly below root and must not contain an object suffix.",
                f"{base}.targetAsset",
            )
        elif target in targets:
            _issue(errors, "target-duplicate", f"Duplicate targetAsset: {target}", f"{base}.targetAsset")
        else:
            targets.add(target)

        expected_class = fixture.get("expectedClass")
        if not isinstance(expected_class, str) or not _SCRIPT_CLASS_RE.fullmatch(expected_class):
            _issue(
                errors,
                "expected-class",
                "expectedClass must use /Script/Module.Class form.",
                f"{base}.expectedClass",
            )

        if kind == "duplicateAsset":
            source = fixture.get("sourceAsset")
            if not _is_package_path(source, allow_object_path=True):
                _issue(errors, "source-invalid", "sourceAsset must be a valid asset package or object path.", f"{base}.sourceAsset")
            else:
                source_package = source.split(".", 1)[0]
                sources.add(source_package)
                if source_package == target:
                    _issue(errors, "source-target-equal", "sourceAsset and targetAsset must differ.", f"{base}.sourceAsset")
        elif kind == "scalarAsset":
            if expected_class != "/Script/UEAgentKitEditor.UEAgentKitScalarWriteFixtureAsset":
                _issue(
                    errors,
                    "scalar-asset-class",
                    "scalarAsset fixtures require the UEAgentKit scalar fixture class.",
                    f"{base}.expectedClass",
                )
        elif kind == "blueprint":
            if expected_class != "/Script/Engine.Blueprint":
                _issue(
                    errors,
                    "blueprint-class",
                    "blueprint fixtures require expectedClass /Script/Engine.Blueprint.",
                    f"{base}.expectedClass",
                )
            parent_class = fixture.get("parentClass")
            if not isinstance(parent_class, str) or not _SCRIPT_CLASS_RE.fullmatch(parent_class):
                _issue(
                    errors,
                    "parent-class",
                    "parentClass must use /Script/Module.Class form.",
                    f"{base}.parentClass",
                )
            blueprint_type = fixture.get("blueprintType")
            if blueprint_type not in _BLUEPRINT_TYPES:
                _issue(
                    errors,
                    "blueprint-type",
                    f"blueprintType must be one of {sorted(_BLUEPRINT_TYPES)}.",
                    f"{base}.blueprintType",
                )
        normalized.append(dict(fixture))

    for source in sorted(sources & targets):
        _issue(
            errors,
            "source-target-overlap",
            f"A sourceAsset cannot also be a targetAsset in the same plan: {source}",
            "plan.fixtures",
        )

    return {
        "schemaVersion": FIXTURE_PLAN_SCHEMA_VERSION,
        "toolVersion": FIXTURE_TOOL_VERSION,
        "valid": not errors,
        "willLoadOrModifyUObjects": False,
        "willWriteDisk": False,
        "planPath": str(resolved),
        "planRevision": plan_revision,
        "root": root,
        "fixtureCount": len(normalized),
        "fixtures": normalized,
        "errors": errors,
    }


def verify_fixture_export(fixture_report_path: Path, export_root: Path) -> dict[str, Any]:
    report_file = fixture_report_path.expanduser().resolve()
    resolved_export = export_root.expanduser().resolve()
    report = _read_json_object(report_file, label="Fixture report")
    errors: list[dict[str, str]] = []
    if report.get("valid") is not True or report.get("status") != "completed":
        _issue(errors, "fixture-report", "Fixture report must describe a completed plan.", "report")
    expected_project = report.get("projectName")
    expected_fixtures = report.get("fixtures")
    if not isinstance(expected_fixtures, list) or not expected_fixtures:
        _issue(errors, "fixture-report", "Fixture report contains no fixture results.", "report.fixtures")
        expected_fixtures = []

    canonical_root = resolved_export / "canonical"
    records: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    if canonical_root.is_dir():
        for path in canonical_root.rglob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("assetPath"), str):
                records.setdefault(value["assetPath"], []).append((path, value))
    else:
        _issue(errors, "export-missing", f"Canonical export directory does not exist: {canonical_root}", "export")

    verified: list[dict[str, Any]] = []
    for index, fixture in enumerate(expected_fixtures):
        base = f"report.fixtures[{index}]"
        if not isinstance(fixture, dict):
            _issue(errors, "fixture-result", "Fixture result must be an object.", base)
            continue
        asset_path = fixture.get("assetPath")
        matches = records.get(asset_path, []) if isinstance(asset_path, str) else []
        if len(matches) != 1:
            _issue(
                errors,
                "asset-export-match",
                f"Expected exactly one canonical match for {asset_path}, found {len(matches)}.",
                f"{base}.assetPath",
            )
            continue
        canonical_path, canonical = matches[0]
        if canonical.get("projectName") != expected_project:
            _issue(errors, "project-name", "Canonical projectName does not match fixture report.", str(canonical_path))
        if canonical.get("assetClass") != fixture.get("assetClass"):
            _issue(errors, "asset-class", "Canonical assetClass does not match fixture report.", str(canonical_path))
        revision = canonical.get("revision")
        actual_revision = revision.get("value") if isinstance(revision, dict) else None
        if actual_revision != fixture.get("revision"):
            _issue(errors, "asset-revision", "Canonical Revision does not match fixture report.", str(canonical_path))
        if not isinstance(revision, dict) or revision.get("packageDirty") is not False:
            _issue(errors, "asset-dirty", "Reloaded fixture package must not be dirty.", str(canonical_path))
        verified.append(
            {
                "id": fixture.get("id"),
                "assetPath": asset_path,
                "canonicalPath": str(canonical_path),
                "assetClass": canonical.get("assetClass"),
                "revision": actual_revision,
            }
        )

    return {
        "schemaVersion": FIXTURE_PLAN_SCHEMA_VERSION,
        "toolVersion": FIXTURE_TOOL_VERSION,
        "verified": not errors,
        "fixtureReportPath": str(report_file),
        "exportRoot": str(resolved_export),
        "expectedCount": len(expected_fixtures),
        "verifiedCount": len(verified),
        "fixtures": verified,
        "errors": errors,
    }

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Iterable

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def project_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or SEMVER_RE.fullmatch(version) is None:
        raise ValueError("pyproject.toml project.version must be semantic x.y.z form")
    return version


def _expect_equal(issues: list[str], label: str, actual: object, expected: object) -> None:
    if actual != expected:
        issues.append(f"{label}: expected {expected!r}, found {actual!r}")


def _expect_text(issues: list[str], path: Path, needle: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        issues.append(f"{label}: missing {needle!r} in {path.relative_to(ROOT)}")


def _version_fields(path: Path, field_name: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'SetStringField\(TEXT\("{re.escape(field_name)}"\),\s*(?:TEXT\()?"([0-9]+\.[0-9]+\.[0-9]+)"\)?\)'
    )
    return pattern.findall(text)


def validate_version_sources(expected_version: str | None = None, *, require_release_docs: bool = False) -> list[str]:
    issues: list[str] = []
    version = project_version()
    if expected_version is not None:
        _expect_equal(issues, "requested release version", version, expected_version)

    plugin = json.loads((ROOT / "Plugin/UEAgentKit/UEAgentKit.uplugin").read_text(encoding="utf-8"))
    _expect_equal(issues, "plugin VersionName", plugin.get("VersionName"), version)
    if not isinstance(plugin.get("Version"), int) or plugin["Version"] < 1:
        issues.append("plugin Version must be a positive integer")

    exact_text_sources = {
        "src/ue_agent_kit/__init__.py": f'__version__ = "{version}"',
        "src/ue_agent_kit/backups.py": f'TOOL_VERSION = "{version}"',
        "src/ue_agent_kit/fixtures.py": f'FIXTURE_TOOL_VERSION = "{version}"',
        "scripts/RunScalarPatchRegression.ps1": f'toolVersion = "{version}"',
        "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/AssetCatalogExportCommandlet.cpp": (
            f'ExporterVersion = TEXT("{version}")'
        ),
        "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintContextExporter.cpp": (
            f'ExporterVersion = TEXT("{version}")'
        ),
        "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridge.cpp": (
            f'PluginVersion = TEXT("{version}")'
        ),
        "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/WriteFixturePlanCommandlet.cpp": (
            f'ToolVersion = TEXT("{version}")'
        ),
    }
    for relative, needle in exact_text_sources.items():
        _expect_text(issues, ROOT / relative, needle, relative)

    field_sources = {
        "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/AssetPatchCommandlet.cpp": "executorVersion",
        "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintPatchCommandlet.cpp": "executorVersion",
        "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintContextExportCommandlet.cpp": "exporterVersion",
    }
    for relative, field_name in field_sources.items():
        values = _version_fields(ROOT / relative, field_name)
        if not values:
            issues.append(f"{relative}: no {field_name} values found")
        elif any(value != version for value in values):
            issues.append(f"{relative}: inconsistent {field_name} values {values!r}, expected {version}")

    _expect_text(issues, ROOT / "README.md", f"当前已发布版本为 **{version}**", "README published version")
    _expect_text(
        issues,
        ROOT / "README_EN.md",
        f"The latest published release is **{version}**",
        "README_EN published version",
    )
    _expect_text(issues, ROOT / "docs/ROADMAP.md", f"当前已发布版本为 **{version}**", "roadmap version")
    _expect_text(
        issues,
        ROOT / "docs/ROADMAP_EN.md",
        f"The latest published release is **{version}**",
        "roadmap EN version",
    )
    _expect_text(issues, ROOT / "CHANGELOG.md", f"## {version} ", "changelog release heading")

    if require_release_docs:
        release_files = [
            ROOT / "docs" / f"RELEASE_{version}.md",
            ROOT / "docs" / f"RELEASE_{version}_EN.md",
        ]
        for path in release_files:
            if not path.is_file():
                issues.append(f"missing release notes: {path.relative_to(ROOT)}")
        _expect_text(
            issues,
            ROOT / "docs/README.md",
            f"RELEASE_{version}.md",
            "documentation index release link",
        )
        _expect_text(
            issues,
            ROOT / "docs/README.md",
            f"RELEASE_{version}_EN.md",
            "documentation index English release link",
        )
    return issues


def validate_schemas_and_examples() -> list[str]:
    issues: list[str] = []
    schema_paths = [
        ROOT / "spec/patch.schema.json",
        ROOT / "spec/write-fixture-plan.schema.json",
        ROOT / "spec/backup-manifest.schema.json",
    ]
    schemas: dict[Path, dict[str, object]] = {}
    for path in schema_paths:
        try:
            schema = json.loads(path.read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            schemas[path] = schema
        except Exception as exc:  # schema diagnostics must retain the original reason
            issues.append(f"{path.relative_to(ROOT)}: {exc}")

    patch_schema = schemas.get(ROOT / "spec/patch.schema.json")
    examples = sorted((ROOT / "examples/patches").glob("*.json"))
    if len(examples) != 16:
        issues.append(f"expected 16 example patches, found {len(examples)}")
    if patch_schema is not None:
        validator = Draft202012Validator(patch_schema)
        for path in examples:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                issues.append(f"{path.relative_to(ROOT)}: {exc}")
                continue
            errors = sorted(validator.iter_errors(value), key=lambda item: list(item.absolute_path))
            if errors:
                issues.append(f"{path.relative_to(ROOT)}: {errors[0].message}")

    try:
        json.loads((ROOT / "config/write-policy.example.json").read_text(encoding="utf-8"))
    except Exception as exc:
        issues.append(f"config/write-policy.example.json: {exc}")
    return issues


def run_checked(command: Iterable[str]) -> None:
    process = subprocess.run(list(command), cwd=ROOT, check=False)
    if process.returncode != 0:
        raise SystemExit(process.returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run portable UE Agent Kit release validation gates.")
    parser.add_argument("--expected-version", default=None)
    parser.add_argument("--require-release-docs", action="store_true")
    parser.add_argument("--skip-ruff", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    args = parser.parse_args()

    issues = validate_version_sources(args.expected_version, require_release_docs=args.require_release_docs)
    issues.extend(validate_schemas_and_examples())
    if issues:
        for issue in issues:
            print(f"ERROR: {issue}", file=sys.stderr)
        return 1

    if not args.skip_ruff:
        run_checked([sys.executable, "-m", "ruff", "check", "src", "tests/python"])
    if not args.skip_tests:
        run_checked([sys.executable, "-m", "unittest", "discover", "-s", "tests/python", "-p", "test_*.py"])

    print(f"RELEASE VALIDATION PASSED: {project_version()}")
    print("Schemas: 3")
    print("Patch examples: 16")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

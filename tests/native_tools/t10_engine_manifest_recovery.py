#!/usr/bin/env python3
"""Offline T10 engine-build-manifest reconciliation prototype.

This script is deliberately independent of the UE process and of the
production FTrustResolver.  It consumes a saved, test-only trust-coverage
snapshot and the engine's ``UnrealEditor.modules`` / ``UnrealEditor.target``
metadata, then produces evidence only.

The current writable engine installation is never treated as an external
trusted root.  A locally consistent manifest is a candidate mechanism, not
proof of plugin/module provenance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

# The CLI is portable: callers supply a project-specific engine/coverage path
# or set explicit environment variables. No machine-specific Windows paths.
DEFAULT_ENGINE_BIN = Path(os.environ.get("UEAGENTKIT_T10_ENGINE_BIN", "Engine/Binaries/Win64"))
DEFAULT_COVERAGE = Path(os.environ.get(
    "UEAGENTKIT_T10_COVERAGE",
    "Build/r1-native/Evidence/t10/T10-allowlist-candidate-v3/trust-coverage.json",
))
DEFAULT_OUTPUT = Path(os.environ.get(
    "UEAGENTKIT_T10_MANIFEST_OUTPUT",
    "Build/r1-native/Evidence/t10/T10-engine-manifest-recovery",
))
DEFAULT_BUILD_ID = "43139311"
DEFAULT_CORE_MODULES = 105
DEFAULT_UNLOADED_MODULES = 26
DEFAULT_DESCRIBED = 512
DEFAULT_MANIFEST_MODULES = 513
_MODULE_DLL_RE = re.compile(r"^UnrealEditor-[A-Za-z0-9_.-]+\.dll$")


class ManifestError(RuntimeError):
    """A manifest/coverage input failed the local reconciliation contract."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def strict_json_load(path: Path) -> Any:
    """Load JSON and reject duplicate object keys instead of silently losing one."""

    if not path.is_file():
        raise ManifestError(f"input file is missing: {path}")

    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ManifestError(f"duplicate JSON key '{key}' in {path}")
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=object_pairs)
    except ManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError(f"invalid JSON in {path}: {exc}") from exc


def _safe_manifest_name(name: str, value: str, path: Path) -> None:
    if not name or name in {".", ".."}:
        raise ManifestError(f"unexpected empty/dot module name in {path}")
    if any(part in name for part in ("/", "\\")) or ".." in name:
        raise ManifestError(f"unexpected module name/path traversal: {name!r}")
    if not value or any(part in value for part in ("/", "\\")) or ".." in value:
        raise ManifestError(f"unexpected module filename/path traversal: {value!r}")
    if not _MODULE_DLL_RE.fullmatch(value):
        raise ManifestError(f"unexpected module filename: {value!r}")


def _manifest_modules(manifest: dict[str, Any], expected_build_id: str, manifest_path: Path) -> tuple[str, dict[str, str]]:
    build_id = str(manifest.get("BuildId", "")).strip()
    if not build_id:
        raise ManifestError(f"{manifest_path} has no BuildId")
    if build_id != str(expected_build_id):
        raise ManifestError(
            f"BuildId mismatch: manifest={build_id!r} expected={expected_build_id!r}"
        )
    modules = manifest.get("Modules")
    if not isinstance(modules, dict) or not modules:
        raise ManifestError(f"{manifest_path} has no non-empty Modules object")

    seen: dict[str, str] = {}
    result: dict[str, str] = {}
    for name, value in modules.items():
        if not isinstance(name, str) or not isinstance(value, str):
            raise ManifestError(f"{manifest_path} Modules must map strings to strings")
        _safe_manifest_name(name, value, manifest_path)
        if value in seen:
            raise ManifestError(
                f"duplicate module filename {value!r}: {seen[value]!r} and {name!r}"
            )
        seen[value] = name
        result[name] = value
    return build_id, result


def _target_data(target_path: Path | None, expected_build_id: str) -> dict[str, Any]:
    if target_path is None:
        return {
            "available": False,
            "buildId": "",
            "buildIdMatch": False,
            "buildProducts": set(),
            "buildPlugins": set(),
            "dynamicLibraries": set(),
            "sha256": "",
        }
    data = strict_json_load(target_path)
    if not isinstance(data, dict):
        raise ManifestError(f"{target_path} is not a JSON object")
    version = data.get("Version")
    target_build = ""
    if isinstance(version, dict):
        target_build = str(version.get("BuildId", "")).strip()
    if not target_build:
        target_build = str(data.get("BuildId", "")).strip()
    if target_build != str(expected_build_id):
        raise ManifestError(
            f"target BuildId mismatch: target={target_build!r} expected={expected_build_id!r}"
        )
    products = data.get("BuildProducts")
    plugins = data.get("BuildPlugins")
    if not isinstance(products, list) or not isinstance(plugins, list):
        raise ManifestError(f"{target_path} must contain BuildProducts and BuildPlugins arrays")

    product_names: set[str] = set()
    dynamic_names: set[str] = set()
    for entry in products:
        if not isinstance(entry, dict):
            continue
        raw = entry.get("Path")
        if not isinstance(raw, str) or not raw:
            continue
        name = raw.replace("\\", "/").rsplit("/", 1)[-1]
        product_names.add(name)
        if entry.get("Type") == "DynamicLibrary":
            dynamic_names.add(name)
    plugin_names = {x for x in plugins if isinstance(x, str) and x}
    return {
        "available": True,
        "buildId": target_build,
        "buildIdMatch": target_build == str(expected_build_id),
        "buildProducts": product_names,
        "dynamicLibraries": dynamic_names,
        "buildPlugins": plugin_names,
        "sha256": sha256_file(target_path),
    }


def _resolve_engine_module(raw: str, engine_bin: Path) -> tuple[Path, bool]:
    """Resolve a coverage module path against the engine binary directory.

    Coverage paths are relative to the engine binary directory (for example
    ``../../../Engine/Binaries/Win64/UnrealEditor-Engine.dll``).  We accept
    internal ``..`` only when the final resolved path is the direct engine
    binary directory; escaping or absolute paths are rejected.
    """

    if not raw:
        raise ManifestError("empty moduleFile")
    path = Path(raw.replace("\\", "/"))
    resolved = path.resolve() if path.is_absolute() else (engine_bin / path).resolve()
    root = engine_bin.resolve()
    if resolved != root and root not in resolved.parents:
        raise ManifestError(f"path traversal escapes engine binaries: {raw!r}")
    if resolved.parent != root:
        raise ManifestError(f"module is not a direct engine binary: {raw!r}")
    return resolved, ".." in path.parts


def reconcile(
    *,
    engine_bin: Path,
    manifest_path: Path,
    coverage_path: Path,
    target_path: Path | None,
    expected_build_id: str = DEFAULT_BUILD_ID,
    expected_total_modules: int = DEFAULT_MANIFEST_MODULES,
    expected_core_modules: int = DEFAULT_CORE_MODULES,
    expected_unloaded_modules: int = DEFAULT_UNLOADED_MODULES,
    expected_described: int = DEFAULT_DESCRIBED,
    externally_trusted_manifest: bool = False,
) -> dict[str, Any]:
    """Reconcile saved coverage against engine metadata without touching UE.

    ``externally_trusted_manifest`` is simulation-only.  It never sets a
    production trust decision and is always recorded separately from the
    candidate score.
    """

    errors: list[str] = []
    manifest: dict[str, Any] = {}
    manifest_hash = ""
    try:
        manifest = strict_json_load(manifest_path)
        if not isinstance(manifest, dict):
            raise ManifestError(f"{manifest_path} is not a JSON object")
        build_id, manifest_modules = _manifest_modules(
            manifest, expected_build_id, manifest_path
        )
        if len(manifest_modules) != expected_total_modules:
            raise ManifestError(
                f"manifest module count {len(manifest_modules)} != {expected_total_modules}"
            )
        manifest_hash = sha256_file(manifest_path)
    except ManifestError as exc:
        errors.append(str(exc))
        build_id = ""
        manifest_modules = {}

    target: dict[str, Any] = {
        "available": False,
        "buildId": "",
        "buildIdMatch": False,
        "buildProducts": set(),
        "buildPlugins": set(),
        "dynamicLibraries": set(),
        "sha256": "",
    }
    try:
        target = _target_data(target_path, expected_build_id)
    except ManifestError as exc:
        errors.append(str(exc))

    coverage: dict[str, Any] = {}
    try:
        coverage = strict_json_load(coverage_path)
        if not isinstance(coverage, dict):
            raise ManifestError(f"{coverage_path} is not a JSON object")
    except ManifestError as exc:
        errors.append(str(exc))

    coverage_modules = coverage.get("modules") if isinstance(coverage, dict) else None
    if not isinstance(coverage_modules, list):
        errors.append(f"{coverage_path} has no modules array")
        coverage_modules = []

    candidate_impact = coverage.get("candidateDescriptorImpact") if isinstance(coverage, dict) else None
    if not isinstance(candidate_impact, dict):
        errors.append(f"{coverage_path} has no candidateDescriptorImpact object")
        candidate_impact = {}

    core_rows: list[dict[str, Any]] = []
    valid_core: dict[str, dict[str, Any]] = {}
    unloaded_rows: list[dict[str, Any]] = []
    path_dotdot_inputs = 0
    rejected_paths: list[dict[str, str]] = []

    for module in coverage_modules:
        if not isinstance(module, dict):
            continue
        category = module.get("candidateCategory")
        if category not in ("B_core_engine_binary", "C_unloaded_core_engine_candidate"):
            continue
        name = module.get("module")
        raw = module.get("moduleFile")
        if not isinstance(name, str) or not isinstance(raw, str):
            rejected_paths.append({"module": str(name), "moduleFile": str(raw), "reason": "missing module/moduleFile"})
            continue
        row: dict[str, Any] = {
            "module": name,
            "candidateCategory": category,
            "moduleLoaded": bool(module.get("moduleLoaded")),
            "owners": int(module.get("owners", 0) or 0),
            "spawners": int(module.get("spawners", 0) or 0),
            "moduleFile": raw,
            "manifestPresent": False,
            "manifestFilename": "",
            "manifestMatch": False,
            "locallyConsistent": False,
            "externallyTrusted": False,
            "pathTraversalSafe": False,
            "directEngineBinary": False,
            "fileExists": False,
            "containsDotDot": ".." in Path(raw.replace("\\", "/")).parts,
        }
        try:
            resolved, has_dotdot = _resolve_engine_module(raw, engine_bin)
            row["resolvedPath"] = str(resolved)
            row["containsDotDot"] = has_dotdot
            row["directEngineBinary"] = True
            row["pathTraversalSafe"] = True
            if has_dotdot:
                path_dotdot_inputs += 1
            if not resolved.is_file():
                raise ManifestError(f"missing DLL: {resolved}")
            row["fileExists"] = True
            filename = resolved.name
            manifest_name = manifest_modules.get(name)
            row["manifestPresent"] = manifest_name is not None
            row["manifestFilename"] = manifest_name or ""
            row["manifestMatch"] = manifest_name == filename
            row["locallyConsistent"] = bool(manifest_name == filename)
            if manifest_name is None:
                raise ManifestError(f"manifest has no module entry for {name!r}")
            if manifest_name != filename:
                raise ManifestError(
                    f"manifest filename mismatch for {name!r}: manifest={manifest_name!r} file={filename!r}"
                )
            if candidate_impact:
                # The manifest is only a local candidate mechanism.  External
                # trust is a separate, caller-supplied simulation flag.
                row["externallyTrusted"] = bool(externally_trusted_manifest)
            row["candidateScore"] = 1.0 if row["locallyConsistent"] else 0.0
        except ManifestError as exc:
            rejected_paths.append({"module": name, "moduleFile": raw, "reason": str(exc)})
            errors.append(f"core module {name!r} rejected: {exc}")
            row["rejectionReason"] = str(exc)
        core_rows.append(row)
        if row["locallyConsistent"]:
            valid_core[name] = row
        if category == "C_unloaded_core_engine_candidate":
            unloaded_rows.append(row)

    if len(core_rows) != expected_core_modules:
        errors.append(
            f"core module count {len(core_rows)} != {expected_core_modules}"
        )
    if len(unloaded_rows) != expected_unloaded_modules:
        errors.append(
            f"unloaded core module count {len(unloaded_rows)} != {expected_unloaded_modules}"
        )
    valid_count = len(valid_core)
    valid_unloaded = sum(1 for row in unloaded_rows if row["locallyConsistent"])
    if valid_count != expected_core_modules:
        errors.append(f"locally consistent core modules {valid_count} != {expected_core_modules}")
    if valid_unloaded != expected_unloaded_modules:
        errors.append(
            f"locally consistent unloaded core modules {valid_unloaded} != {expected_unloaded_modules}"
        )
    if rejected_paths:
        errors.append(f"rejected core module paths/entries: {len(rejected_paths)}")

    described_total = int(candidate_impact.get("describedTotal", 0) or 0)
    baseline_recovered = int(candidate_impact.get("describedCandidateRecovered", 0) or 0)
    denied_project = int(candidate_impact.get("describedCandidateDeniedProject", 0) or 0)
    denied_other = int(candidate_impact.get("describedCandidateDeniedOther", 0) or 0)
    if described_total != expected_described:
        errors.append(f"described total {described_total} != {expected_described}")
    if described_total != baseline_recovered + denied_project + denied_other:
        errors.append("described recovery counts do not add up")

    denied_modules = candidate_impact.get("deniedModules")
    if not isinstance(denied_modules, list):
        denied_modules = []
    denied_total = 0
    denied_valid = 0
    denied_unvalidated: list[dict[str, Any]] = []
    for item in denied_modules:
        if not isinstance(item, dict):
            continue
        module = item.get("module")
        count = int(item.get("descriptors", 0) or 0)
        denied_total += count
        row = valid_core.get(module) if isinstance(module, str) else None
        if row is not None and row["locallyConsistent"]:
            denied_valid += count
        else:
            denied_unvalidated.append({"module": module, "descriptors": count})
    if denied_total != denied_other:
        errors.append(
            f"denied module descriptor sum {denied_total} != candidate denied {denied_other}"
        )
    hypothetical_recovered = baseline_recovered + denied_valid
    remaining = described_total - hypothetical_recovered

    core_product_matches = sum(
        1 for row in core_rows if row["locallyConsistent"] and Path(row["moduleFile"].replace("\\", "/")).name in target["dynamicLibraries"]
    )
    action_plugin_names = {
        str(module.get("pluginName"))
        for module in coverage_modules
        if isinstance(module, dict) and module.get("pluginName")
    }
    engine_action_plugins = {
        name for name in action_plugin_names if name and name != "UEAgentKit"
    }
    plugin_matches = len(engine_action_plugins & target["buildPlugins"]) if target["available"] else 0

    status = "ok" if not errors else "refused"
    return {
        "schemaVersion": "ueak.native.engine-manifest-recovery/1.0",
        "status": status,
        "errors": errors,
        "manifest": {
            "path": str(manifest_path),
            "sha256": manifest_hash,
            "buildId": build_id,
            "moduleCount": len(manifest_modules),
            "externallyTrusted": bool(externally_trusted_manifest),
            "trustStatus": (
                "simulated-externally-trusted"
                if externally_trusted_manifest
                else "locally-consistent-but-untrusted"
            ),
            "productionDecisionAllowed": False,
            "note": (
                "A manifest read from the current writable engine installation is not "
                "an external trust root; the external-trust flag is simulation-only."
            ),
        },
        "target": {
            "path": str(target_path) if target_path else "",
            "sha256": target["sha256"],
            "available": target["available"],
            "buildId": target["buildId"],
            "buildIdMatch": target["buildIdMatch"],
            "buildProducts": len(target["buildProducts"]),
            "dynamicLibraries": len(target["dynamicLibraries"]),
            "buildPlugins": len(target["buildPlugins"]),
            "coreProductMatches": core_product_matches,
            "actionEnginePlugins": len(engine_action_plugins),
            "actionEnginePluginMatches": plugin_matches,
            "independentTrustRoot": False,
            "sameWritableInstallation": True,
            "note": (
                "UnrealEditor.target and UnrealEditor.modules come from the same writable "
                "engine installation.  Agreement is local consistency, not an independent "
                "cryptographic root of trust."
            ),
        },
        "coverage": {
            "path": str(coverage_path),
            "describedTotal": described_total,
            "baselineCandidateRecovered": baseline_recovered,
            "candidateDeniedProject": denied_project,
            "candidateDeniedOther": denied_other,
            "deniedModuleSum": denied_total,
            "deniedModulesValidatedByManifest": denied_valid,
            "deniedModulesNotValidated": denied_unvalidated,
        },
        "moduleReconciliation": {
            "coreModules": len(core_rows),
            "coreManifestExact": valid_count,
            "coreManifestPresent": sum(1 for row in core_rows if row["manifestPresent"]),
            "unloadedCoreModules": len(unloaded_rows),
            "unloadedCoreManifestExact": valid_unloaded,
            "containsDotDotInputs": path_dotdot_inputs,
            "rejected": rejected_paths,
            "rows": core_rows,
        },
        "hypotheticalDescriptorRecovery": {
            "describedTotal": described_total,
            "baselineRecovered": baseline_recovered,
            "deniedCoreModulesValidated": denied_valid,
            "recovered": hypothetical_recovered,
            "remaining": remaining,
            "allDescribedRecovered": remaining == 0,
            "method": (
                "baseline candidate recovered + denied descriptors whose module is "
                "manifest-exact and resolved to a direct engine binary; no templates created"
            ),
            "notAProductionTrustDecision": True,
        },
        "trustAssessment": {
            "externallyTrusted": False,
            "manifestPresent": valid_count > 0,
            "locallyConsistent": valid_count == expected_core_modules,
            "productionDecisionAllowed": False,
            "reason": (
                "local metadata and files are from a writable installation; a user-approved "
                "signed/pinned external manifest or OS trust root is still required"
            ),
        },
    }


def write_report(report: dict[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "manifest-reconciliation.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine-bin", type=Path, default=DEFAULT_ENGINE_BIN)
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--expected-build-id", default=DEFAULT_BUILD_ID)
    parser.add_argument("--expected-total-modules", type=int, default=DEFAULT_MANIFEST_MODULES)
    parser.add_argument("--expected-core-modules", type=int, default=DEFAULT_CORE_MODULES)
    parser.add_argument("--expected-unloaded-modules", type=int, default=DEFAULT_UNLOADED_MODULES)
    parser.add_argument("--expected-described", type=int, default=DEFAULT_DESCRIBED)
    parser.add_argument(
        "--externally-trusted-manifest",
        action="store_true",
        help="simulation-only flag; never enables production trust",
    )
    args = parser.parse_args(argv)

    manifest = args.engine_bin / "UnrealEditor.modules"
    target = args.target if args.target is not None else args.engine_bin / "UnrealEditor.target"
    report = reconcile(
        engine_bin=args.engine_bin,
        manifest_path=manifest,
        coverage_path=args.coverage,
        target_path=target,
        expected_build_id=args.expected_build_id,
        expected_total_modules=args.expected_total_modules,
        expected_core_modules=args.expected_core_modules,
        expected_unloaded_modules=args.expected_unloaded_modules,
        expected_described=args.expected_described,
        externally_trusted_manifest=args.externally_trusted_manifest,
    )
    output = write_report(report, args.output_dir)
    recovery = report["hypotheticalDescriptorRecovery"]
    modules = report["moduleReconciliation"]
    print(f"status={report['status']}")
    print(f"buildId={report['manifest']['buildId']}")
    print(f"core={modules['coreManifestExact']}/{modules['coreModules']}")
    print(f"unloaded={modules['unloadedCoreManifestExact']}/{modules['unloadedCoreModules']}")
    print(f"described={recovery['recovered']}/{recovery['describedTotal']}")
    print(f"remaining={recovery['remaining']}")
    print(f"report={output}")
    if report["errors"]:
        for error in report["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

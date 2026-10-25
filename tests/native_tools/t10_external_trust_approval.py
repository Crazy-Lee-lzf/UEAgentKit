#!/usr/bin/env python3
"""Offline external-approval trust bootstrap tool for T10.

This is a test-only tool.  It never changes UE plugin configuration, the
production registry, or the production trust resolver.  It produces:

* a deterministic candidate manifest of the currently identified core engine
  modules and engine plugins, with local file hashes; and
* a verification report for a separately supplied, externally pinned approval.

Approval authenticity is NOT established by this tool.  The caller must supply
``--expected-approval-sha256`` out of band from a trusted channel.  A hash read
from the same mutable directory as the approval is not an independent root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

from t10_engine_manifest_recovery import (
    DEFAULT_BUILD_ID,
    ManifestError,
    _manifest_modules,
    sha256_file,
    strict_json_load,
)

# Require explicit environment-specific inputs for real engine analysis.
DEFAULT_ENGINE_BIN = Path(os.environ.get("UEAGENTKIT_T10_ENGINE_BIN", "Engine/Binaries/Win64"))
DEFAULT_COVERAGE = Path(os.environ.get(
    "UEAGENTKIT_T10_COVERAGE",
    "Build/r1-native/Evidence/t10/T10-allowlist-candidate-v3/trust-coverage.json",
))
DEFAULT_OUTPUT_DIR = Path(os.environ.get(
    "UEAGENTKIT_T10_APPROVAL_OUTPUT",
    "Build/r1-native/Evidence/t10/T10-external-trust-approval",
))
DEFAULT_CORE_MODULES = 105
DEFAULT_PLUGINS = 95
DEFAULT_PLUGIN_MODULES = 230
DEFAULT_MAX_FILES = 600
DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_SCHEMA_CANDIDATE = "ueak.t10.external-approval-candidate/1.0"
_SCHEMA_APPROVAL = "ueak.t10.external-approval/1.0"
_SCHEMA_VERIFY = "ueak.t10.external-approval-verification/1.0"


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256_hex_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_canonical(value: Any) -> str:
    return _sha256_hex_bytes(_canonical_bytes(value))


def candidate_digest(candidate: dict[str, Any]) -> str:
    payload = {key: value for key, value in candidate.items() if key != "candidateDigest"}
    return _sha256_canonical(payload)


def _normalize_sha(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{label} must be a non-empty SHA256 string")
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise ManifestError(f"{label} is not a valid SHA256: {value!r}")
    return text


def _is_reparse(path: Path) -> bool:
    try:
        info = os.lstat(path)
    except OSError as exc:
        raise ManifestError(f"cannot lstat {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(
        getattr(info, "st_file_attributes", 0) & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _check_no_reparse(path: Path, root: Path) -> None:
    """Reject symlink/junction/reparse components up to root (inclusive)."""

    current = path.absolute()
    root_abs = root.absolute()
    try:
        current.relative_to(root_abs)
    except ValueError as exc:
        raise ManifestError(f"path escapes engine root: {path}") from exc
    while True:
        if _is_reparse(current):
            raise ManifestError(f"symlink/junction/reparse point rejected: {current}")
        if current == root_abs:
            break
        parent = current.parent
        if parent == current:
            raise ManifestError(f"cannot reach engine root from {path}")
        current = parent


def _safe_file(raw: str, base: Path, root: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ManifestError(f"{label} is empty")
    path = Path(raw.replace("\\", "/"))
    if path.is_absolute():
        raise ManifestError(f"{label} must be relative: {raw!r}")
    # Lexical containment first; this rejects a relative path that escapes
    # before resolve() gets a chance to hide the traversal.
    lexical = Path(os.path.abspath(base / path))
    try:
        lexical.relative_to(root.absolute())
    except ValueError as exc:
        raise ManifestError(f"{label} escapes engine root: {raw!r}") from exc
    _check_no_reparse(lexical, root)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ManifestError(f"{label} is missing or unresolvable: {raw!r} ({exc})") from exc
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ManifestError(f"{label} resolves outside engine root: {raw!r}") from exc
    if not resolved.is_file():
        raise ManifestError(f"{label} is not a regular file: {raw!r}")
    return resolved


def _safe_dir(raw: str, base: Path, root: Path, label: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ManifestError(f"{label} is empty")
    path = Path(raw.replace("\\", "/"))
    if path.is_absolute():
        raise ManifestError(f"{label} must be relative: {raw!r}")
    lexical = Path(os.path.abspath(base / path))
    try:
        lexical.relative_to(root.absolute())
    except ValueError as exc:
        raise ManifestError(f"{label} escapes engine root: {raw!r}") from exc
    _check_no_reparse(lexical, root)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise ManifestError(f"{label} is missing or unresolvable: {raw!r} ({exc})") from exc
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ManifestError(f"{label} resolves outside engine root: {raw!r}") from exc
    if not resolved.is_dir():
        raise ManifestError(f"{label} is not a directory: {raw!r}")
    return resolved


def _hash_bounded(path: Path, max_bytes: int) -> tuple[str, int]:
    size = path.stat().st_size
    if size > max_bytes:
        raise ManifestError(
            f"file exceeds per-file limit ({size} > {max_bytes}): {path}"
        )
    return sha256_file(path), size


def _target_version(target: dict[str, Any], expected_build_id: str) -> dict[str, Any]:
    version = target.get("Version")
    if not isinstance(version, dict):
        raise ManifestError("target has no Version object")
    build_id = str(version.get("BuildId", "")).strip()
    if build_id != str(expected_build_id):
        raise ManifestError(
            f"target BuildId mismatch: {build_id!r} != {expected_build_id!r}"
        )
    return {
        "major": int(version.get("MajorVersion", 0) or 0),
        "minor": int(version.get("MinorVersion", 0) or 0),
        "patch": int(version.get("PatchVersion", 0) or 0),
        "changelist": int(version.get("Changelist", 0) or 0),
        "compatibleChangelist": int(version.get("CompatibleChangelist", 0) or 0),
        "branchName": str(version.get("BranchName", "")),
        "buildId": build_id,
    }


def generate_candidate(
    *,
    engine_bin: Path,
    coverage_path: Path,
    output_path: Path,
    expected_build_id: str = DEFAULT_BUILD_ID,
    expected_core_modules: int = DEFAULT_CORE_MODULES,
    expected_plugins: int = DEFAULT_PLUGINS,
    expected_plugin_modules: int = DEFAULT_PLUGIN_MODULES,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> dict[str, Any]:
    """Create a candidate manifest.  No approval or trust is implied."""

    engine_bin = engine_bin.resolve()
    engine_root = engine_bin.parent.parent.resolve()
    if engine_bin != (engine_root / "Binaries" / "Win64").resolve():
        raise ManifestError(f"engine-bin is not <engine>/Binaries/Win64: {engine_bin}")
    manifest_path = engine_bin / "UnrealEditor.modules"
    target_path = engine_bin / "UnrealEditor.target"
    for path in (manifest_path, target_path):
        _check_no_reparse(path, engine_root)
    manifest_data = strict_json_load(manifest_path)
    if not isinstance(manifest_data, dict):
        raise ManifestError("UnrealEditor.modules is not a JSON object")
    build_id, manifest_modules = _manifest_modules(
        manifest_data, expected_build_id, manifest_path
    )
    target_data = strict_json_load(target_path)
    if not isinstance(target_data, dict):
        raise ManifestError("UnrealEditor.target is not a JSON object")
    engine_version = _target_version(target_data, expected_build_id)
    manifest_sha = sha256_file(manifest_path)
    target_sha = sha256_file(target_path)

    coverage = strict_json_load(coverage_path)
    if not isinstance(coverage, dict):
        raise ManifestError("coverage is not a JSON object")
    coverage_modules = coverage.get("modules")
    plugin_manifest = coverage.get("candidateManifest", {}).get("plugins")
    if not isinstance(coverage_modules, list) or not isinstance(plugin_manifest, list):
        raise ManifestError("coverage lacks modules or candidateManifest.plugins")

    plugin_info: dict[str, dict[str, Any]] = {}
    for item in plugin_manifest:
        if not isinstance(item, dict):
            raise ManifestError("coverage candidateManifest plugin entry is not an object")
        name = item.get("plugin")
        if not isinstance(name, str) or not name:
            raise ManifestError("coverage plugin entry has no plugin name")
        if name in plugin_info:
            raise ManifestError(f"duplicate plugin entry: {name}")
        plugin_info[name] = item

    core_rows: list[dict[str, Any]] = []
    plugin_module_rows: dict[str, list[dict[str, Any]]] = {}
    seen_basenames: dict[str, str] = {}
    scanned_files = 0
    scanned_bytes = 0

    def check_budget(size: int, label: str) -> None:
        nonlocal scanned_files, scanned_bytes
        scanned_files += 1
        scanned_bytes += size
        if scanned_files > max_files:
            raise ManifestError(f"maximum file count exceeded at {label}")
        if scanned_bytes > max_total_bytes:
            raise ManifestError(f"maximum total bytes exceeded at {label}")

    for module in coverage_modules:
        if not isinstance(module, dict):
            continue
        category = module.get("candidateCategory")
        if category not in ("B_core_engine_binary", "C_unloaded_core_engine_candidate"):
            continue
        name = module.get("module")
        raw = module.get("moduleFile")
        if not isinstance(name, str) or not name:
            raise ManifestError("core module entry has no module name")
        path = _safe_file(str(raw), engine_bin, engine_root, f"core module {name}")
        if path.parent != engine_bin:
            raise ManifestError(f"core module is not a direct engine binary: {name}")
        if path.name != manifest_modules.get(name):
            raise ManifestError(
                f"core module manifest mismatch: {name} manifest={manifest_modules.get(name)!r} file={path.name!r}"
            )
        if path.name in seen_basenames:
            raise ManifestError(
                f"duplicate module basename {path.name!r}: {seen_basenames[path.name]!r}/{name!r}"
            )
        digest, size = _hash_bounded(path, max_file_bytes)
        check_budget(size, f"core module {name}")
        seen_basenames[path.name] = name
        core_rows.append(
            {
                "module": name,
                "basename": path.name,
                "path": path.relative_to(engine_root).as_posix(),
                "sha256": digest,
                "manifestName": manifest_modules[name],
            }
        )

    for plugin_name in sorted(plugin_info):
        info = plugin_info[plugin_name]
        plugin_base = _safe_dir(
            str(info.get("baseDir", "")), engine_bin, engine_root, f"plugin {plugin_name} baseDir"
        )
        try:
            plugin_base.relative_to(engine_root / "Plugins")
        except ValueError as exc:
            raise ManifestError(f"plugin baseDir is not under Engine/Plugins: {plugin_name}") from exc
        descriptor = _safe_file(
            str(info.get("descriptorFile", "")),
            engine_bin,
            engine_root,
            f"plugin {plugin_name} descriptor",
        )
        try:
            descriptor.relative_to(plugin_base)
        except ValueError as exc:
            raise ManifestError(f"plugin descriptor is outside its baseDir: {plugin_name}") from exc
        descriptor_digest, descriptor_size = _hash_bounded(descriptor, max_file_bytes)
        check_budget(descriptor_size, f"plugin {plugin_name} descriptor")
        expected_descriptor = info.get("descriptorDigest")
        if expected_descriptor:
            if _normalize_sha(expected_descriptor, f"{plugin_name} descriptorDigest") != descriptor_digest:
                raise ManifestError(
                    f"plugin descriptor digest changed: {plugin_name}"
                )

        modules: list[dict[str, Any]] = []
        declared_modules = info.get("modules")
        if not isinstance(declared_modules, list) or not declared_modules:
            raise ManifestError(f"plugin has no declared modules: {plugin_name}")
        declared_module_set = {str(name) for name in declared_modules}
        plugin_bin = _safe_dir(
            "Binaries/Win64", plugin_base, engine_root, f"plugin {plugin_name} binary directory"
        )
        direct_files = sorted(
            [
                path
                for path in plugin_bin.iterdir()
                if path.is_file() and path.name.startswith("UnrealEditor-") and path.suffix.lower() == ".dll"
            ],
            key=lambda path: path.name,
        )
        if not direct_files:
            raise ManifestError(f"plugin has no direct module DLLs: {plugin_name}")
        file_modules = {
            path.name[len("UnrealEditor-") : -len(".dll")] for path in direct_files
        }
        if file_modules != declared_module_set:
            missing = sorted(declared_module_set - file_modules)
            extra = sorted(file_modules - declared_module_set)
            raise ManifestError(
                f"plugin binary/descriptor module mismatch for {plugin_name}: "
                f"missing={missing} extra={extra}"
            )
        for binary_path in direct_files:
            module_name = binary_path.name[len("UnrealEditor-") : -len(".dll")]
            if binary_path.name in seen_basenames:
                raise ManifestError(
                    f"duplicate module basename {binary_path.name!r}: {seen_basenames[binary_path.name]!r}/{module_name!r}"
                )
            path = _safe_file(
                binary_path.relative_to(engine_root).as_posix(),
                engine_root,
                engine_root,
                f"plugin module {plugin_name}/{module_name}",
            )
            if path.parent != plugin_bin:
                raise ManifestError(
                    f"plugin module is not in plugin Binaries/Win64: {plugin_name}/{module_name}"
                )
            try:
                path.relative_to(plugin_base)
            except ValueError as exc:
                raise ManifestError(
                    f"plugin module is outside its plugin baseDir: {plugin_name}/{module_name}"
                ) from exc
            digest, size = _hash_bounded(path, max_file_bytes)
            check_budget(size, f"plugin module {plugin_name}/{module_name}")
            seen_basenames[path.name] = f"{plugin_name}/{module_name}"
            modules.append(
                {
                    "module": module_name,
                    "basename": path.name,
                    "path": path.relative_to(engine_root).as_posix(),
                    "sha256": digest,
                }
            )
        plugin_module_rows[plugin_name] = modules

    if len(core_rows) != expected_core_modules:
        raise ManifestError(
            f"core module count {len(core_rows)} != {expected_core_modules}"
        )
    if len(plugin_info) != expected_plugins:
        raise ManifestError(f"plugin count {len(plugin_info)} != {expected_plugins}")
    if sum(len(rows) for rows in plugin_module_rows.values()) != expected_plugin_modules:
        raise ManifestError(
            f"plugin module count {sum(len(rows) for rows in plugin_module_rows.values())} != {expected_plugin_modules}"
        )

    core_rows.sort(key=lambda row: row["module"])
    plugins: list[dict[str, Any]] = []
    for plugin_name in sorted(plugin_info):
        info = plugin_info[plugin_name]
        descriptor = _safe_file(
            str(info.get("descriptorFile", "")),
            engine_bin,
            engine_root,
            f"plugin {plugin_name} descriptor",
        )
        descriptor_digest = sha256_file(descriptor)
        plugins.append(
            {
                "plugin": plugin_name,
                "version": int(info.get("version", 0) or 0),
                "versionName": str(info.get("versionName", "")),
                "descriptorPath": descriptor.relative_to(engine_root).as_posix(),
                "descriptorSha256": descriptor_digest,
                "declaredModuleCount": len(
                    {str(name) for name in info.get("modules", [])}
                )
                if isinstance(info.get("modules"), list)
                else 0,
                "modules": sorted(plugin_module_rows[plugin_name], key=lambda row: row["module"]),
            }
        )

    candidate: dict[str, Any] = {
        "schemaVersion": _SCHEMA_CANDIDATE,
        "generatedBy": "t10_external_trust_approval.py (offline test-only)",
        "productionEnabled": False,
        "trusted": False,
        "scope": {
            "coreModules": "105 core engine modules from the verified coverage snapshot",
            "plugins": "95 action-owning Engine plugins",
            "pluginModules": (
                "230 direct Binaries/Win64 module DLLs across those 95 plugins; "
                "plugin descriptor declarations are checked for missing/extra files"
            ),
            "coverageCompleteForAllPluginBinaries": True,
        },
        "buildId": build_id,
        "engineVersion": engine_version,
        "modulesManifestSha256": manifest_sha,
        "targetSha256": target_sha,
        "counts": {
            "coreModules": len(core_rows),
            "plugins": len(plugins),
            "pluginModules": sum(len(p["modules"]) for p in plugins),
            "scannedFiles": scanned_files,
        },
        "coreModules": core_rows,
        "plugins": plugins,
        "digestCanonicalization": (
            "sha256 over UTF-8 JSON with sort_keys=True, separators=(',',':'), "
            "and the candidateDigest field removed"
        ),
    }
    candidate["candidateDigest"] = f"sha256:{candidate_digest(candidate)}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(candidate, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return candidate


def _approval_maps(approval: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    core: dict[str, str] = {}
    core_entries = approval.get("coreModules")
    if not isinstance(core_entries, list):
        raise ManifestError("approval has no coreModules array")
    for item in core_entries:
        if not isinstance(item, dict):
            raise ManifestError("approval coreModules entry is not an object")
        module = item.get("module")
        if not isinstance(module, str) or not module:
            raise ManifestError("approval coreModules entry has no module")
        if module in core:
            raise ManifestError(f"duplicate approval core module: {module}")
        core[module] = _normalize_sha(item.get("sha256"), f"approval core {module}")

    plugins: dict[str, dict[str, Any]] = {}
    plugin_entries = approval.get("plugins")
    if not isinstance(plugin_entries, list):
        raise ManifestError("approval has no plugins array")
    for item in plugin_entries:
        if not isinstance(item, dict):
            raise ManifestError("approval plugins entry is not an object")
        plugin = item.get("plugin")
        if not isinstance(plugin, str) or not plugin:
            raise ManifestError("approval plugin entry has no plugin")
        if plugin in plugins:
            raise ManifestError(f"duplicate approval plugin: {plugin}")
        modules: dict[str, str] = {}
        module_entries = item.get("modules")
        if not isinstance(module_entries, list):
            raise ManifestError(f"approval plugin {plugin} has no modules array")
        for module in module_entries:
            if not isinstance(module, dict):
                raise ManifestError(f"approval plugin {plugin} module entry is not an object")
            module_name = module.get("module")
            if not isinstance(module_name, str) or not module_name:
                raise ManifestError(f"approval plugin {plugin} has a module without a name")
            if module_name in modules:
                raise ManifestError(
                    f"duplicate approval plugin module: {plugin}/{module_name}"
                )
            modules[module_name] = _normalize_sha(
                module.get("sha256"), f"approval plugin {plugin}/{module_name}"
            )
        plugins[plugin] = {
            "descriptorSha256": _normalize_sha(
                item.get("descriptorSha256"), f"approval plugin {plugin} descriptor"
            ),
            "modules": modules,
        }
    return core, plugins


def verify_candidate(
    *,
    candidate: dict[str, Any],
    approval: dict[str, Any] | None,
    approval_sha256: str,
    expected_approval_sha256: str,
) -> dict[str, Any]:
    """Strictly compare a candidate to a separately pinned approval file."""

    errors: list[str] = []
    approved_core: list[dict[str, Any]] = []
    approved_plugins: list[dict[str, Any]] = []
    unapproved_core: list[dict[str, Any]] = []
    unapproved_plugins: list[dict[str, Any]] = []
    status = "FORBIDDEN"
    approval_core: dict[str, str] = {}
    approval_plugins: dict[str, dict[str, Any]] = {}

    candidate_digest_value = candidate.get("candidateDigest")
    computed_candidate_digest = f"sha256:{candidate_digest(candidate)}"
    if candidate.get("schemaVersion") != _SCHEMA_CANDIDATE:
        errors.append("unknown candidate schema")
    if candidate.get("productionEnabled") is not False:
        errors.append("candidate must have productionEnabled=false")
    if candidate_digest_value != computed_candidate_digest:
        errors.append("candidateDigest does not match candidate contents")

    if not expected_approval_sha256.strip():
        errors.append("FORBIDDEN: --expected-approval-sha256 is required")
    if not approval_sha256.strip():
        errors.append("FORBIDDEN: approval file digest is missing")
    if approval is None:
        errors.append("FORBIDDEN: no approval file supplied")
    else:
        try:
            if expected_approval_sha256.strip():
                expected = _normalize_sha(expected_approval_sha256, "expected approval digest")
                actual = _normalize_sha(approval_sha256, "approval digest")
                if expected != actual:
                    raise ManifestError("approval file SHA256 does not match out-of-band pin")
            if approval.get("schemaVersion") != _SCHEMA_APPROVAL:
                raise ManifestError("unknown approval schema")
            approval_build = approval.get("engine", {}).get("buildId") if isinstance(approval.get("engine"), dict) else None
            if str(approval_build) != str(candidate.get("buildId")):
                raise ManifestError(
                    f"approval BuildId mismatch: {approval_build!r} != {candidate.get('buildId')!r}"
                )
            approval_candidate_digest = _normalize_sha(
                approval.get("candidateDigest"), "approval candidateDigest"
            )
            if f"sha256:{approval_candidate_digest}" != computed_candidate_digest:
                raise ManifestError("approval candidateDigest does not match candidate")
            if isinstance(approval.get("engine"), dict):
                for key, field in (
                    ("modulesManifestSha256", "modulesManifestSha256"),
                    ("targetSha256", "targetSha256"),
                ):
                    if key in approval["engine"]:
                        if _normalize_sha(approval["engine"][key], f"approval engine {key}") != _normalize_sha(
                            candidate.get(field), f"candidate {field}"
                        ):
                            raise ManifestError(f"approval {key} does not match candidate")
            approval_core, approval_plugins = _approval_maps(approval)
        except ManifestError as exc:
            errors.append(str(exc))

    candidate_core = {
        item["module"]: item
        for item in candidate.get("coreModules", [])
        if isinstance(item, dict) and isinstance(item.get("module"), str)
    }
    candidate_plugins = {
        item["plugin"]: item
        for item in candidate.get("plugins", [])
        if isinstance(item, dict) and isinstance(item.get("plugin"), str)
    }

    # Do not let a malformed candidate collapse duplicate names or smuggle an
    # escaped path into an otherwise digest-pinned approval.
    core_list = candidate.get("coreModules")
    plugin_list = candidate.get("plugins")
    if not isinstance(core_list, list):
        errors.append("candidate coreModules is not an array")
        core_list = []
    if not isinstance(plugin_list, list):
        errors.append("candidate plugins is not an array")
        plugin_list = []
    seen_core: set[str] = set()
    for item in core_list:
        if not isinstance(item, dict) or not isinstance(item.get("module"), str):
            errors.append("candidate coreModules contains a malformed entry")
            continue
        if item["module"] in seen_core:
            errors.append(f"candidate duplicate core module: {item['module']}")
        seen_core.add(item["module"])
        try:
            _normalize_sha(item.get("sha256"), f"candidate core {item['module']} sha256")
        except ManifestError as exc:
            errors.append(str(exc))
        path = item.get("path")
        if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
            errors.append(f"candidate core path is not relative/safe: {item['module']}")
    seen_plugins: set[str] = set()
    for item in plugin_list:
        if not isinstance(item, dict) or not isinstance(item.get("plugin"), str):
            errors.append("candidate plugins contains a malformed entry")
            continue
        if item["plugin"] in seen_plugins:
            errors.append(f"candidate duplicate plugin: {item['plugin']}")
        seen_plugins.add(item["plugin"])
        try:
            _normalize_sha(item.get("descriptorSha256"), f"candidate plugin {item['plugin']} descriptor")
        except ManifestError as exc:
            errors.append(str(exc))
        modules = item.get("modules")
        if not isinstance(modules, list) or not modules:
            errors.append(f"candidate plugin has no modules: {item['plugin']}")
            continue
        seen_modules: set[str] = set()
        for module in modules:
            if not isinstance(module, dict) or not isinstance(module.get("module"), str):
                errors.append(f"candidate plugin {item['plugin']} contains a malformed module")
                continue
            if module["module"] in seen_modules:
                errors.append(
                    f"candidate duplicate plugin module: {item['plugin']}/{module['module']}"
                )
            seen_modules.add(module["module"])
            try:
                _normalize_sha(
                    module.get("sha256"),
                    f"candidate plugin {item['plugin']}/{module['module']} sha256",
                )
            except ManifestError as exc:
                errors.append(str(exc))
            path = module.get("path")
            if not isinstance(path, str) or not path or Path(path).is_absolute() or ".." in Path(path).parts:
                errors.append(
                    f"candidate plugin module path is not relative/safe: {item['plugin']}/{module['module']}"
                )

    if not errors:
        extra_core = sorted(set(approval_core) - set(candidate_core))
        if extra_core:
            errors.append(f"approval contains unknown core modules: {extra_core}")
        extra_plugins = sorted(set(approval_plugins) - set(candidate_plugins))
        if extra_plugins:
            errors.append(f"approval contains unknown plugins: {extra_plugins}")

    if not errors:
        for module, item in candidate_core.items():
            if module not in approval_core:
                unapproved_core.append(item)
            elif approval_core[module] == item.get("sha256"):
                approved_core.append(item)
            else:
                errors.append(f"approval core digest mismatch: {module}")
        for plugin, item in candidate_plugins.items():
            approved_plugin = approval_plugins.get(plugin)
            if approved_plugin is None:
                unapproved_plugins.append(item)
                continue
            if approved_plugin["descriptorSha256"] != item.get("descriptorSha256"):
                errors.append(f"approval plugin descriptor digest mismatch: {plugin}")
                break
            candidate_module_map = {
                module["module"]: module.get("sha256")
                for module in item.get("modules", [])
                if isinstance(module, dict) and isinstance(module.get("module"), str)
            }
            if set(approved_plugin["modules"]) != set(candidate_module_map):
                errors.append(f"approval plugin module set mismatch: {plugin}")
                break
            if any(
                approved_plugin["modules"][name] != digest
                for name, digest in candidate_module_map.items()
            ):
                errors.append(f"approval plugin module digest mismatch: {plugin}")
                break
            approved_plugins.append(item)

    if errors:
        status = "FORBIDDEN"
    elif len(approved_core) == len(candidate_core) and len(approved_plugins) == len(candidate_plugins):
        status = "VERIFIED"
    else:
        status = "PARTIAL"
    approval_kind = ""
    if approval is not None and isinstance(approval.get("status"), str):
        approval_kind = approval["status"]
    return {
        "schemaVersion": _SCHEMA_VERIFY,
        "status": status,
        "verified": status == "VERIFIED",
        "productionEnabled": False,
        "trustedForProduction": False,
        "candidateDigest": computed_candidate_digest,
        "approvalSha256": _normalize_sha(approval_sha256, "approval digest") if approval_sha256.strip() else "",
        "expectedApprovalSha256": expected_approval_sha256,
        "approvalKind": approval_kind,
        "syntheticDemo": bool(approval_kind) and approval_kind != "externally-approved",
        "errors": errors,
        "counts": {
            "candidateCoreModules": len(candidate_core),
            "approvedCoreModules": len(approved_core),
            "candidatePlugins": len(candidate_plugins),
            "approvedPlugins": len(approved_plugins),
            "unapprovedCoreModules": len(unapproved_core),
            "unapprovedPlugins": len(unapproved_plugins),
        },
        "approved": {
            "coreModules": approved_core,
            "plugins": approved_plugins,
        },
        "unapproved": {
            "coreModules": unapproved_core,
            "plugins": unapproved_plugins,
        },
        "note": (
            "VERIFIED means the externally pinned approval digest and all listed item "
            "hashes match. It does not make this a production trust decision; "
            "productionEnabled is always false."
        ),
    }


def _load_optional_json(path: Path | None) -> tuple[dict[str, Any] | None, str, str]:
    if path is None:
        return None, "", "no approval file supplied"
    try:
        raw = path.read_bytes()
        def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ManifestError(f"duplicate JSON key '{key}' in {path}")
                result[key] = value
            return result
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=object_pairs)
        if not isinstance(data, dict):
            return None, "", f"approval file is not a JSON object: {path}"
        # Hash exactly the bytes that were parsed, so a concurrent replacement
        # cannot make the pin refer to different contents.
        return data, _sha256_hex_bytes(raw), ""
    except ManifestError as exc:
        return None, "", str(exc)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, "", f"cannot read approval file {path}: {exc}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    generate = sub.add_parser("generate", help="generate a deterministic unapproved candidate manifest")
    generate.add_argument("--engine-bin", type=Path, default=DEFAULT_ENGINE_BIN)
    generate.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    generate.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR / "candidate-manifest.json")
    generate.add_argument("--expected-build-id", default=DEFAULT_BUILD_ID)
    generate.add_argument("--expected-core-modules", type=int, default=DEFAULT_CORE_MODULES)
    generate.add_argument("--expected-plugins", type=int, default=DEFAULT_PLUGINS)
    generate.add_argument("--expected-plugin-modules", type=int, default=DEFAULT_PLUGIN_MODULES)
    generate.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    generate.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    generate.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)

    verify = sub.add_parser("verify", help="verify candidate against a separately pinned approval")
    verify.add_argument("--candidate", type=Path, required=True)
    verify.add_argument("--approval-file", type=Path, default=None)
    verify.add_argument("--expected-approval-sha256", default="")
    verify.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR / "verification.json")

    args = parser.parse_args(argv)
    if args.command == "generate":
        try:
            candidate = generate_candidate(
                engine_bin=args.engine_bin,
                coverage_path=args.coverage,
                output_path=args.output,
                expected_build_id=args.expected_build_id,
                expected_core_modules=args.expected_core_modules,
                expected_plugins=args.expected_plugins,
                expected_plugin_modules=args.expected_plugin_modules,
                max_files=args.max_files,
                max_file_bytes=args.max_file_bytes,
                max_total_bytes=args.max_total_bytes,
            )
        except ManifestError as exc:
            print(f"FORBIDDEN: {exc}", file=sys.stderr)
            return 2
        print(f"candidate={args.output}")
        print(f"candidateDigest={candidate['candidateDigest']}")
        print(f"core={candidate['counts']['coreModules']} plugins={candidate['counts']['plugins']}")
        print("productionEnabled=false")
        return 0

    candidate_path = args.candidate
    try:
        candidate = strict_json_load(candidate_path)
        if not isinstance(candidate, dict):
            raise ManifestError("candidate is not a JSON object")
    except ManifestError as exc:
        print(f"FORBIDDEN: {exc}", file=sys.stderr)
        return 2
    approval, approval_sha, load_error = _load_optional_json(args.approval_file)
    if load_error:
        print(f"FORBIDDEN: {load_error}", file=sys.stderr)
    result = verify_candidate(
        candidate=candidate,
        approval=approval,
        approval_sha256=approval_sha,
        expected_approval_sha256=args.expected_approval_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"status={result['status']}")
    print(f"productionEnabled={result['productionEnabled']}")
    print(f"verified={result['verified']}")
    print(f"output={args.output}")
    if result["errors"]:
        for error in result["errors"]:
            print(f"ERROR: {error}", file=sys.stderr)
    return 0 if result["status"] == "VERIFIED" else 2


if __name__ == "__main__":
    raise SystemExit(main())

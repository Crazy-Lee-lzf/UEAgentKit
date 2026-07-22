from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BACKUP_MANIFEST_SCHEMA_VERSION = "1.0"
ROLLBACK_REPORT_SCHEMA_VERSION = "1.0"
TOOL_VERSION = "0.4.3"

_REVISION_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_ID_RE = re.compile(r"[^A-Za-z0-9_.-]+")
_PACKAGE_SIDECAR_SUFFIXES = (".uexp", ".ubulk", ".uptnl", ".m.ubulk", ".upayload")
_MATERIAL_OPERATION_TYPES = {
    "setMaterialInstanceScalarParameter": "Scalar",
    "setMaterialInstanceVectorParameter": "Vector",
    "setMaterialInstanceTextureParameter": "Texture",
    "setMaterialInstanceStaticSwitchParameter": "StaticSwitch",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_id(value: str) -> str:
    normalized = _SAFE_ID_RE.sub("-", value).strip("-.")
    return normalized[:96] or "rollback"


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


def _write_json_atomic(path: Path, value: dict[str, Any]) -> Path:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{uuid.uuid4().hex}.tmp")
    output = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    try:
        temporary.write_text(output, encoding="utf-8", newline="\r\n")
        os.replace(temporary, resolved)
    finally:
        if temporary.exists():
            temporary.unlink()
    return resolved


def _sha256_revision(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _probe_directory_write(directory: Path) -> None:
    resolved = directory.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    probe = resolved / f".ueak-write-probe-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_bytes(b"ue-agent-kit")
        with probe.open("rb") as handle:
            if handle.read() != b"ue-agent-kit":
                raise OSError(f"Write probe verification failed: {probe}")
    finally:
        if probe.exists():
            probe.unlink()


def _restore_package_from_safety(
    target: Path,
    safety_path: Path,
    expected_revision: str,
    rollback_id: str,
) -> None:
    restore_temporary = target.with_name(f".{target.name}.{rollback_id}.restore.tmp")
    try:
        shutil.copy2(safety_path, restore_temporary)
        restore_revision = _sha256_revision(restore_temporary)
        if restore_revision != expected_revision:
            raise RuntimeError(
                f"Safety restore source mismatch: expected {expected_revision}, found {restore_revision}."
            )
        os.replace(restore_temporary, target)
        final_revision = _sha256_revision(target)
        if final_revision != expected_revision:
            raise RuntimeError(
                f"Safety restore target mismatch: expected {expected_revision}, found {final_revision}."
            )
    finally:
        if restore_temporary.exists():
            restore_temporary.unlink()


def _require_revision(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not _REVISION_RE.fullmatch(value):
        raise ValueError(f"{label} must use sha256:<64 lowercase hex> form.")
    return value


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolve_under(root: Path, relative_path: str, *, label: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path:
        raise ValueError(f"{label} must be a non-empty forward-slash relative path.")
    candidate_relative = Path(relative_path)
    if candidate_relative.is_absolute() or any(part in {"", ".", ".."} for part in candidate_relative.parts):
        raise ValueError(f"{label} contains an absolute path or traversal segment.")
    resolved_root = root.expanduser().resolve()
    candidate = (resolved_root / candidate_relative).resolve()
    if not _is_relative_to(candidate, resolved_root):
        raise ValueError(f"{label} escapes its allowed root.")
    return candidate


def _normalize_asset_path(asset_path: Any) -> tuple[str, str]:
    if not isinstance(asset_path, str) or not asset_path.startswith("/Game/"):
        raise ValueError("assetPath must start with /Game/.")
    if "\\" in asset_path or "//" in asset_path or ":" in asset_path:
        raise ValueError("assetPath contains an invalid separator or drive marker.")
    package_path, separator, object_name = asset_path.partition(".")
    relative = package_path[len("/Game/") :]
    segments = relative.split("/")
    if not segments or any(not segment or segment in {".", ".."} for segment in segments):
        raise ValueError("assetPath contains an empty or traversal segment.")
    if separator and object_name != segments[-1]:
        raise ValueError("assetPath object name must match the package basename.")
    canonical_asset_path = f"{package_path}.{segments[-1]}"
    return package_path, canonical_asset_path


def _target_package_path(project_path: Path, asset_path: str) -> tuple[Path, str]:
    project_file = project_path.expanduser().resolve()
    if not project_file.is_file() or project_file.suffix.lower() != ".uproject":
        raise FileNotFoundError(f"Unreal project does not exist: {project_file}")
    package_path, canonical_asset_path = _normalize_asset_path(asset_path)
    relative_package = package_path[len("/Game/") :]
    content_root = (project_file.parent / "Content").resolve()
    target = (content_root / f"{relative_package}.uasset").resolve()
    if not _is_relative_to(target, content_root):
        raise ValueError("Resolved asset package escapes the project Content directory.")
    return target, canonical_asset_path


def _existing_sidecars(package_path: Path) -> list[str]:
    results: list[str] = []
    for suffix in _PACKAGE_SIDECAR_SUFFIXES:
        candidate = package_path.with_suffix(suffix)
        if candidate.exists():
            results.append(str(candidate))
    return results


def _asset_under_allowed_root(asset_path: str, root: Any) -> bool:
    if not isinstance(root, str) or not root.startswith("/Game"):
        return False
    normalized = root.rstrip("/")
    return asset_path == normalized or asset_path.startswith(f"{normalized}/")


def _authorization_key(report: dict[str, Any]) -> str:
    operation = report.get("operation")
    asset_class = report.get("assetClass")
    target = report.get("target")
    if not isinstance(operation, str) or not isinstance(asset_class, str) or not isinstance(target, dict):
        return ""
    if operation == "setAssetProperty":
        property_path = target.get("propertyPath")
        return f"{asset_class}#{property_path}" if isinstance(property_path, str) else ""
    material_type = _MATERIAL_OPERATION_TYPES.get(operation)
    if material_type is not None:
        parameter_name = target.get("parameterName")
        return (
            f"{asset_class}#{material_type}#{parameter_name}"
            if isinstance(parameter_name, str)
            else ""
        )
    if operation == "setDataTableCell":
        row_struct_path = report.get("rowStructPath")
        field_name = target.get("fieldName")
        return (
            f"{asset_class}#{row_struct_path}#{field_name}"
            if isinstance(row_struct_path, str) and isinstance(field_name, str)
            else ""
        )
    return ""


def _policy_authorizes_manifest(policy: dict[str, Any], manifest: dict[str, Any]) -> tuple[bool, str]:
    project_name = manifest.get("projectName")
    asset_path = manifest.get("assetPath")
    asset_class = manifest.get("assetClass")
    operation = manifest.get("operation")
    authorization_key = manifest.get("authorizationKey", "")
    if policy.get("commitEnabled") is not True:
        return False, "Rollback requires a policy with commitEnabled=true."
    if project_name not in policy.get("allowedProjectNames", []):
        return False, "Manifest projectName is not authorized by policy."
    if not isinstance(asset_path, str) or not any(
        _asset_under_allowed_root(asset_path, root) for root in policy.get("allowedAssetRoots", [])
    ):
        return False, "Manifest assetPath is not under an allowed policy root."
    if asset_class not in policy.get("allowedAssetClasses", []):
        return False, "Manifest assetClass is not authorized by policy."
    if operation not in policy.get("allowedOperations", []):
        return False, "Manifest operation is not authorized by policy."
    if operation == "setAssetProperty" and authorization_key not in policy.get("allowedAssetProperties", []):
        return False, "Manifest asset property is not authorized by policy."
    if operation in _MATERIAL_OPERATION_TYPES and authorization_key not in policy.get(
        "allowedMaterialParameters", []
    ):
        return False, "Manifest Material Instance parameter is not authorized by policy."
    if operation == "setDataTableCell" and authorization_key not in policy.get("allowedDataTableFields", []):
        return False, "Manifest DataTable field is not authorized by policy."
    return True, ""


def create_backup_manifest(
    patch_path: Path,
    policy_path: Path,
    commit_report_path: Path,
    backup_root: Path,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    patch_file = patch_path.expanduser().resolve()
    policy_file = policy_path.expanduser().resolve()
    report_file = commit_report_path.expanduser().resolve()
    resolved_backup_root = backup_root.expanduser().resolve()
    patch = _read_json_object(patch_file, label="Patch")
    policy = _read_json_object(policy_file, label="Policy")
    report = _read_json_object(report_file, label="Commit report")

    if report.get("mode") != "Commit" or report.get("saved") is not True:
        raise ValueError("Commit report must describe a successful saved Commit.")
    patch_assets = patch.get("assets")
    if not isinstance(patch_assets, list) or len(patch_assets) != 1 or not isinstance(patch_assets[0], dict):
        raise ValueError("Backup manifests currently require exactly one patch asset.")
    patch_asset = patch_assets[0]
    operations = patch_asset.get("operations")
    if not isinstance(operations, list) or len(operations) != 1 or not isinstance(operations[0], dict):
        raise ValueError("Backup manifests currently require exactly one patch operation.")
    patch_operation = operations[0]

    before_revision = _require_revision(report.get("beforeRevision"), label="beforeRevision")
    after_revision = _require_revision(report.get("afterRevision"), label="afterRevision")
    if before_revision == after_revision:
        raise ValueError("A Commit manifest requires different before and after revisions.")
    if patch.get("patchId") != report.get("patchId"):
        raise ValueError("Patch and Commit report patchId values do not match.")
    if patch.get("projectName") != report.get("projectName"):
        raise ValueError("Patch and Commit report projectName values do not match.")
    if patch_asset.get("assetPath") != report.get("assetPath"):
        raise ValueError("Patch and Commit report assetPath values do not match.")
    if patch_asset.get("expectedAssetClass") != report.get("assetClass"):
        raise ValueError("Patch and Commit report assetClass values do not match.")
    if patch_asset.get("expectedRevision") != before_revision:
        raise ValueError("Patch expectedRevision does not match the Commit beforeRevision.")
    if patch_operation.get("operation") != report.get("operation"):
        raise ValueError("Patch and Commit report operation values do not match.")
    if patch_operation.get("target") != report.get("target"):
        raise ValueError("Patch and Commit report target values do not match.")

    backup_path_value = report.get("backupPath")
    if not isinstance(backup_path_value, str) or not backup_path_value:
        raise ValueError("Commit report does not contain a backupPath.")
    backup_path = Path(backup_path_value).expanduser().resolve()
    if not backup_path.is_file():
        raise FileNotFoundError(f"Commit backup does not exist: {backup_path}")
    if not _is_relative_to(backup_path, resolved_backup_root):
        raise ValueError("Commit backup is outside the declared backup root.")
    backup_size = backup_path.stat().st_size
    if backup_size <= 0:
        raise ValueError("Commit backup must be a non-empty file.")
    backup_revision = _sha256_revision(backup_path)
    if backup_revision != before_revision:
        raise ValueError(
            f"Commit backup revision mismatch: expected {before_revision}, found {backup_revision}."
        )

    asset_path = report.get("assetPath")
    _, canonical_asset_path = _normalize_asset_path(asset_path)
    authorization_key = _authorization_key(report)
    manifest_id = f"{report['patchId']}-{after_revision.removeprefix('sha256:')[:12]}"
    manifest: dict[str, Any] = {
        "schemaVersion": BACKUP_MANIFEST_SCHEMA_VERSION,
        "toolVersion": TOOL_VERSION,
        "manifestId": manifest_id,
        "createdUtc": _utc_now(),
        "patchId": report["patchId"],
        "projectName": report["projectName"],
        "assetPath": canonical_asset_path,
        "assetClass": report["assetClass"],
        "operation": report["operation"],
        "target": report.get("target", {}),
        "authorizationKey": authorization_key,
        "beforeRevision": before_revision,
        "afterRevision": after_revision,
        "beforeValue": report.get("beforeValue"),
        "afterValue": report.get("afterValue"),
        "rowStructPath": report.get("rowStructPath", ""),
        "packageKind": "single-uasset",
        "backup": {
            "relativePath": backup_path.relative_to(resolved_backup_root).as_posix(),
            "revision": backup_revision,
            "size": backup_size,
        },
        "source": {
            "patchFileName": patch_file.name,
            "patchSha256": _sha256_revision(patch_file),
            "policyFileName": policy_file.name,
            "policySha256": _sha256_revision(policy_file),
            "commitReportFileName": report_file.name,
            "commitReportSha256": _sha256_revision(report_file),
            "executorVersion": report.get("executorVersion", ""),
        },
    }
    authorized, reason = _policy_authorizes_manifest(policy, manifest)
    if not authorized:
        raise ValueError(reason)

    if output_path is None:
        output = backup_path.with_name(f"{backup_path.name}.manifest.json")
    else:
        output = output_path.expanduser().resolve()
    if not _is_relative_to(output, resolved_backup_root):
        raise ValueError("Backup manifest output must stay inside the declared backup root.")
    if output.exists():
        raise FileExistsError(f"Backup manifest already exists: {output}")
    written = _write_json_atomic(output, manifest)
    return {
        "valid": True,
        "manifestPath": str(written),
        "manifest": manifest,
    }


def validate_rollback(
    manifest_path: Path,
    policy_path: Path,
    project_path: Path,
    backup_root: Path,
) -> dict[str, Any]:
    resolved_manifest = manifest_path.expanduser().resolve()
    resolved_policy = policy_path.expanduser().resolve()
    resolved_project = project_path.expanduser().resolve()
    resolved_backup_root = backup_root.expanduser().resolve()
    errors: list[dict[str, str]] = []

    def issue(code: str, message: str, path: str) -> None:
        errors.append({"code": code, "message": message, "path": path})

    try:
        manifest = _read_json_object(resolved_manifest, label="Backup manifest")
    except (FileNotFoundError, ValueError) as exc:
        return {
            "schemaVersion": ROLLBACK_REPORT_SCHEMA_VERSION,
            "toolVersion": TOOL_VERSION,
            "mode": "DryRun",
            "valid": False,
            "willWriteDisk": False,
            "errors": [{"code": "manifest-invalid", "message": str(exc), "path": "manifest"}],
        }
    try:
        policy = _read_json_object(resolved_policy, label="Policy")
    except (FileNotFoundError, ValueError) as exc:
        return {
            "schemaVersion": ROLLBACK_REPORT_SCHEMA_VERSION,
            "toolVersion": TOOL_VERSION,
            "mode": "DryRun",
            "valid": False,
            "willWriteDisk": False,
            "manifestPath": str(resolved_manifest),
            "errors": [{"code": "policy-invalid", "message": str(exc), "path": "policy"}],
        }

    if manifest.get("schemaVersion") != BACKUP_MANIFEST_SCHEMA_VERSION:
        issue("manifest-schema", "Unsupported backup manifest schemaVersion.", "manifest.schemaVersion")
    if manifest.get("packageKind") != "single-uasset":
        issue(
            "package-kind-not-supported",
            "Rollback currently supports only single-uasset backup manifests.",
            "manifest.packageKind",
        )
    if not _is_relative_to(resolved_manifest, resolved_backup_root):
        issue(
            "manifest-outside-backup-root",
            "Backup manifest must be located inside backupRoot.",
            "manifest",
        )
    source = manifest.get("source")
    expected_policy_revision = source.get("policySha256") if isinstance(source, dict) else None
    actual_policy_revision = _sha256_revision(resolved_policy) if resolved_policy.is_file() else ""
    if expected_policy_revision != actual_policy_revision:
        issue(
            "policy-revision-conflict",
            f"Policy revision mismatch: expected {expected_policy_revision}, found {actual_policy_revision}.",
            "manifest.source.policySha256",
        )

    project_name = manifest.get("projectName")
    if not resolved_project.is_file() or resolved_project.suffix.lower() != ".uproject":
        issue("project-missing", f"Unreal project does not exist: {resolved_project}", "project")
    elif project_name != resolved_project.stem:
        issue(
            "project-name-mismatch",
            f"Manifest projectName {project_name!r} does not match {resolved_project.stem!r}.",
            "manifest.projectName",
        )

    asset_path = manifest.get("assetPath")
    target_package: Path | None = None
    canonical_asset_path = ""
    try:
        target_package, canonical_asset_path = _target_package_path(resolved_project, asset_path)
    except (FileNotFoundError, ValueError) as exc:
        issue("asset-path-invalid", str(exc), "manifest.assetPath")

    backup_path: Path | None = None
    backup = manifest.get("backup")
    if not isinstance(backup, dict):
        issue("backup-invalid", "Manifest backup entry must be an object.", "manifest.backup")
    else:
        try:
            backup_path = _resolve_under(
                resolved_backup_root,
                backup.get("relativePath"),
                label="manifest.backup.relativePath",
            )
        except ValueError as exc:
            issue("backup-path-invalid", str(exc), "manifest.backup.relativePath")

    expected_before_revision = manifest.get("beforeRevision")
    expected_after_revision = manifest.get("afterRevision")
    try:
        expected_before_revision = _require_revision(
            expected_before_revision,
            label="manifest.beforeRevision",
        )
    except ValueError as exc:
        issue("before-revision-invalid", str(exc), "manifest.beforeRevision")
        expected_before_revision = ""
    try:
        expected_after_revision = _require_revision(
            expected_after_revision,
            label="manifest.afterRevision",
        )
    except ValueError as exc:
        issue("after-revision-invalid", str(exc), "manifest.afterRevision")
        expected_after_revision = ""
    if expected_before_revision and expected_before_revision == expected_after_revision:
        issue(
            "manifest-revision-transition",
            "beforeRevision and afterRevision must describe different package states.",
            "manifest.afterRevision",
        )

    current_revision = ""
    sidecars: list[str] = []
    if target_package is not None:
        if asset_path != canonical_asset_path:
            issue(
                "asset-path-not-canonical",
                f"Manifest assetPath must use canonical object-path form: {canonical_asset_path}",
                "manifest.assetPath",
            )
        if not target_package.is_file():
            issue("target-missing", f"Target package does not exist: {target_package}", "targetPackage")
        else:
            current_revision = _sha256_revision(target_package)
            if expected_after_revision and current_revision != expected_after_revision:
                issue(
                    "current-revision-conflict",
                    f"Expected current revision {expected_after_revision}, found {current_revision}.",
                    "manifest.afterRevision",
                )
            sidecars = _existing_sidecars(target_package)
            if sidecars:
                issue(
                    "package-sidecars-not-supported",
                    "Rollback currently supports only single-file .uasset packages.",
                    "targetPackage",
                )

    backup_revision = ""
    if backup_path is not None:
        if not backup_path.is_file():
            issue("backup-missing", f"Backup file does not exist: {backup_path}", "manifest.backup")
        else:
            backup_revision = _sha256_revision(backup_path)
            manifest_backup_revision = backup.get("revision") if isinstance(backup, dict) else None
            if manifest_backup_revision != expected_before_revision:
                issue(
                    "manifest-backup-revision-mismatch",
                    "Manifest backup.revision does not match beforeRevision.",
                    "manifest.backup.revision",
                )
            if expected_before_revision and backup_revision != expected_before_revision:
                issue(
                    "backup-revision-conflict",
                    f"Expected backup revision {expected_before_revision}, found {backup_revision}.",
                    "manifest.backup.revision",
                )
            manifest_backup_size = backup.get("size") if isinstance(backup, dict) else None
            if not isinstance(manifest_backup_size, int) or manifest_backup_size <= 0:
                issue(
                    "manifest-backup-size-invalid",
                    "Manifest backup.size must be a positive integer.",
                    "manifest.backup.size",
                )
            elif backup_path.stat().st_size != manifest_backup_size:
                issue(
                    "backup-size-conflict",
                    f"Expected backup size {manifest_backup_size}, found {backup_path.stat().st_size}.",
                    "manifest.backup.size",
                )

    authorized, authorization_error = _policy_authorizes_manifest(policy, manifest)
    if not authorized:
        issue("policy-not-authorized", authorization_error, "policy")

    return {
        "schemaVersion": ROLLBACK_REPORT_SCHEMA_VERSION,
        "toolVersion": TOOL_VERSION,
        "mode": "DryRun",
        "valid": not errors,
        "willWriteDisk": False,
        "manifestPath": str(resolved_manifest),
        "policyPath": str(resolved_policy),
        "projectPath": str(resolved_project),
        "backupRoot": str(resolved_backup_root),
        "manifestId": manifest.get("manifestId", ""),
        "patchId": manifest.get("patchId", ""),
        "projectName": project_name,
        "assetPath": canonical_asset_path or asset_path,
        "assetClass": manifest.get("assetClass", ""),
        "operation": manifest.get("operation", ""),
        "target": manifest.get("target", {}),
        "targetPackagePath": str(target_package) if target_package is not None else "",
        "backupPath": str(backup_path) if backup_path is not None else "",
        "expectedCurrentRevision": expected_after_revision,
        "currentRevision": current_revision,
        "expectedBackupRevision": expected_before_revision,
        "backupRevision": backup_revision,
        "sidecars": sidecars,
        "errors": errors,
    }


def rollback_backup(
    manifest_path: Path,
    policy_path: Path,
    project_path: Path,
    backup_root: Path,
    *,
    commit: bool = False,
    report_path: Path | None = None,
) -> dict[str, Any]:
    resolved_manifest_path = manifest_path.expanduser().resolve()
    resolved_policy_path = policy_path.expanduser().resolve()
    resolved_project_path = project_path.expanduser().resolve()
    resolved_report_path = report_path.expanduser().resolve() if report_path is not None else None
    if resolved_report_path is not None:
        if resolved_report_path.suffix.lower() != ".json":
            raise ValueError("Rollback report path must use a .json extension.")
        project_content = (resolved_project_path.parent / "Content").resolve()
        if _is_relative_to(resolved_report_path, project_content):
            raise ValueError("Rollback report path must stay outside the Unreal project Content directory.")
        for protected_path, label in (
            (resolved_manifest_path, "backup manifest"),
            (resolved_policy_path, "policy"),
            (resolved_project_path, "project file"),
        ):
            if resolved_report_path == protected_path:
                raise ValueError(f"Rollback report path conflicts with the {label}.")

    result = validate_rollback(
        resolved_manifest_path,
        resolved_policy_path,
        resolved_project_path,
        backup_root,
    )
    if resolved_report_path is not None:
        for protected_value, label in (
            (result.get("targetPackagePath"), "target package"),
            (result.get("backupPath"), "backup file"),
        ):
            if isinstance(protected_value, str) and protected_value:
                if resolved_report_path == Path(protected_value).expanduser().resolve():
                    raise ValueError(f"Rollback report path conflicts with the {label}.")
    result["mode"] = "Commit" if commit else "DryRun"
    result["willWriteDisk"] = commit and result["valid"]
    result["wroteDisk"] = False
    result["restored"] = False
    result["preRollbackBackupPath"] = ""
    result["receiptPath"] = ""
    if not result["valid"] or not commit:
        if report_path is not None:
            _write_json_atomic(report_path, result)
        return result

    target = Path(result["targetPackagePath"]).resolve()
    backup = Path(result["backupPath"]).resolve()
    resolved_backup_root = Path(result["backupRoot"]).resolve()
    rollback_id = f"{_safe_id(str(result.get('manifestId', 'rollback')))}-{uuid.uuid4().hex[:12]}"
    safety_directory = resolved_backup_root / "rollback-safety"
    receipt_directory = resolved_backup_root / "rollback-receipts"
    safety_directory.mkdir(parents=True, exist_ok=True)
    receipt_directory.mkdir(parents=True, exist_ok=True)
    _probe_directory_write(safety_directory)
    _probe_directory_write(receipt_directory)
    if resolved_report_path is not None:
        _probe_directory_write(resolved_report_path.parent)
    safety_path = safety_directory / f"{rollback_id}-{target.name}.pre-rollback.bak"
    receipt_path = receipt_directory / f"{rollback_id}.json"
    temporary_target = target.with_name(f".{target.name}.{rollback_id}.tmp")
    target_replaced = False
    receipt_published = False

    try:
        shutil.copy2(target, safety_path)
        safety_revision = _sha256_revision(safety_path)
        if safety_revision != result["expectedCurrentRevision"]:
            raise RuntimeError(
                f"Pre-rollback safety backup mismatch: expected {result['expectedCurrentRevision']}, "
                f"found {safety_revision}."
            )
        shutil.copy2(backup, temporary_target)
        temporary_revision = _sha256_revision(temporary_target)
        if temporary_revision != result["expectedBackupRevision"]:
            raise RuntimeError(
                f"Temporary rollback package mismatch: expected {result['expectedBackupRevision']}, "
                f"found {temporary_revision}."
            )
        os.replace(temporary_target, target)
        target_replaced = True
        restored_revision = _sha256_revision(target)
        if restored_revision != result["expectedBackupRevision"]:
            raise RuntimeError(
                f"Restored package mismatch: expected {result['expectedBackupRevision']}, "
                f"found {restored_revision}."
            )

        result.update(
            {
                "valid": True,
                "willWriteDisk": False,
                "wroteDisk": True,
                "restored": True,
                "rollbackId": rollback_id,
                "restoredUtc": _utc_now(),
                "beforeRollbackRevision": result["expectedCurrentRevision"],
                "afterRollbackRevision": result["expectedBackupRevision"],
                "currentRevision": restored_revision,
                "preRollbackBackupPath": str(safety_path),
                "preRollbackBackupRevision": safety_revision,
                "receiptPath": str(receipt_path),
                "errors": [],
            }
        )
        _write_json_atomic(receipt_path, result)
        receipt_published = True
        if resolved_report_path is not None and resolved_report_path != receipt_path.resolve():
            _write_json_atomic(resolved_report_path, result)
    except Exception as exc:
        if receipt_published and receipt_path.exists():
            receipt_path.unlink()
        if target_replaced:
            try:
                _restore_package_from_safety(
                    target,
                    safety_path,
                    result["expectedCurrentRevision"],
                    rollback_id,
                )
            except Exception as restore_exc:
                raise RuntimeError(
                    f"Rollback failed after replacing the package: {exc}. "
                    f"Automatic safety restore also failed: {restore_exc}. "
                    f"Manual safety backup: {safety_path}"
                ) from restore_exc
            raise RuntimeError(
                f"Rollback failed after replacing the package: {exc}. "
                "The pre-rollback package was restored automatically."
            ) from exc
        raise
    finally:
        if temporary_target.exists():
            temporary_target.unlink()

    return result


def verify_rollback_export(rollback_report_path: Path, export_root: Path) -> dict[str, Any]:
    report_file = rollback_report_path.expanduser().resolve()
    resolved_export_root = export_root.expanduser().resolve()
    report = _read_json_object(report_file, label="Rollback report")
    errors: list[dict[str, str]] = []

    def issue(code: str, message: str, path: str) -> None:
        errors.append({"code": code, "message": message, "path": path})

    if (
        report.get("restored") is not True
        or report.get("wroteDisk") is not True
        or report.get("mode") != "Commit"
    ):
        issue("rollback-report-invalid", "Rollback report must describe a completed Commit.", "report")
    expected_asset_path = report.get("assetPath")
    expected_asset_class = report.get("assetClass")
    expected_project_name = report.get("projectName")
    expected_revision = report.get("afterRollbackRevision")
    try:
        expected_revision = _require_revision(expected_revision, label="afterRollbackRevision")
    except ValueError as exc:
        issue("rollback-revision-invalid", str(exc), "report.afterRollbackRevision")
        expected_revision = ""

    matching: list[tuple[Path, dict[str, Any]]] = []
    canonical_root = resolved_export_root / "canonical"
    if not canonical_root.is_dir():
        issue("export-missing", f"Canonical export directory does not exist: {canonical_root}", "export")
    else:
        for path in canonical_root.rglob("*.json"):
            try:
                candidate = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(candidate, dict) and candidate.get("assetPath") == expected_asset_path:
                matching.append((path, candidate))
    if len(matching) != 1:
        issue(
            "asset-export-match",
            f"Expected exactly one canonical asset match, found {len(matching)}.",
            "export.canonical",
        )
        canonical_path = ""
        canonical: dict[str, Any] = {}
    else:
        canonical_path = str(matching[0][0])
        canonical = matching[0][1]
        if canonical.get("projectName") != expected_project_name:
            issue("project-name-mismatch", "Export projectName does not match rollback report.", "canonical.projectName")
        if canonical.get("assetClass") != expected_asset_class:
            issue("asset-class-mismatch", "Export assetClass does not match rollback report.", "canonical.assetClass")
        revision = canonical.get("revision")
        actual_revision = revision.get("value") if isinstance(revision, dict) else None
        if actual_revision != expected_revision:
            issue(
                "rollback-verification-revision",
                f"Expected restored revision {expected_revision}, found {actual_revision}.",
                "canonical.revision.value",
            )
        if not isinstance(revision, dict) or revision.get("packageDirty") is not False:
            issue("rollback-verification-dirty", "Reloaded package must not be dirty.", "canonical.revision.packageDirty")

    return {
        "schemaVersion": ROLLBACK_REPORT_SCHEMA_VERSION,
        "toolVersion": TOOL_VERSION,
        "verified": not errors,
        "rollbackReportPath": str(report_file),
        "exportRoot": str(resolved_export_root),
        "assetPath": expected_asset_path,
        "assetClass": expected_asset_class,
        "expectedRevision": expected_revision,
        "canonicalPath": canonical_path,
        "errors": errors,
    }

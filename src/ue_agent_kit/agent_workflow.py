from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from .agent_api import IndexQueryService
from .freshness import IndexFreshnessTracker
from .patches import OPERATION_REGISTRY, validate_patch


WORKFLOW_SCHEMA_VERSION = "1.0"
MAX_WORKFLOW_RECORDS = 128
MAX_PROCESS_OUTPUT_CHARS = 16000
HIGH_LEVEL_CHANGE_MODES = ("Plan", "DryRun")
MATERIAL_PARAMETER_OPERATIONS = {
    "Scalar": "setMaterialInstanceScalarParameter",
    "Vector": "setMaterialInstanceVectorParameter",
    "Texture": "setMaterialInstanceTextureParameter",
    "StaticSwitch": "setMaterialInstanceStaticSwitchParameter",
}
CRASH_MARKERS = (
    "fatal error:",
    "assertion failed:",
    "unhandled exception:",
    "exception_access_violation",
    "lowlevelfatalerror",
    "signal 11 caught",
)
CRASH_EXIT_CODES = {-1073741819, -1073741676, -1073740791, 3221225477, 3221225620, 3221226505}


class WorkflowError(RuntimeError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int
    stdout: str
    stderr: str


ProcessRunner = Callable[[list[str], Path, int], ProcessResult]


@dataclass(frozen=True)
class PatchWorkflowConfig:
    tool_root: Path
    engine_root: Path
    project_path: Path
    policy_path: Path
    revision_export: Path
    work_root: Path
    backup_root: Path
    commit_enabled: bool = False
    process_timeout_seconds: int = 1800


@dataclass
class PlanRecord:
    plan_id: str
    digest: str
    patch: dict[str, Any]
    patch_path: Path
    validation: dict[str, Any]
    consumed: bool = False


@dataclass
class DryRunRecord:
    receipt: str
    plan_id: str
    plan_digest: str
    report_path: Path
    report: dict[str, Any]
    consumed: bool = False


@dataclass
class ApplyRecord:
    receipt: str
    plan_id: str
    plan_digest: str
    asset_path: str
    before_revision: str
    after_revision: str
    manifest_path: Path
    report_path: Path
    report: dict[str, Any]
    verified: bool = False
    rolled_back: bool = False


@dataclass
class RollbackDryRunRecord:
    receipt: str
    apply_receipt: str
    report_path: Path
    report: dict[str, Any]
    consumed: bool = False


def _default_process_runner(arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
    completed = subprocess.run(
        arguments,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr)


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _diagnostic_id(*parts: str) -> str:
    payload = "\n".join(parts).encode("utf-8", errors="replace")
    return "diag_" + hashlib.sha256(payload).hexdigest()[:20]


def _report_id(stage: str, path: Path) -> str:
    return "report_" + hashlib.sha256(f"{stage}:{path.resolve()}".encode("utf-8")).hexdigest()[:20]


def _read_json(path: Path, *, stage: str = "") -> dict[str, Any]:
    report_id = _report_id(stage, path) if stage else ""
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        code = "workflow-report-missing" if stage else "workflow-output-missing"
        details = {"stage": stage, "reportId": report_id} if stage else {}
        raise WorkflowError(code, "A required workflow report was not created.", details=details) from exc
    except json.JSONDecodeError as exc:
        code = "workflow-report-invalid" if stage else "workflow-output-invalid"
        details = (
            {
                "stage": stage,
                "reportId": report_id,
                "jsonError": exc.msg,
                "line": exc.lineno,
                "column": exc.colno,
            }
            if stage
            else {}
        )
        raise WorkflowError(code, "A workflow report was not valid JSON.", details=details) from exc
    if not isinstance(value, dict):
        code = "workflow-report-invalid" if stage else "workflow-output-invalid"
        details = {"stage": stage, "reportId": report_id} if stage else {}
        raise WorkflowError(code, "A workflow report must contain a JSON object.", details=details)
    return value


def _is_ue_crash(result: ProcessResult) -> bool:
    if result.exit_code in CRASH_EXIT_CODES:
        return True
    output = f"{result.stdout}\n{result.stderr}".casefold()
    return any(marker in output for marker in CRASH_MARKERS)


def _validation_error(
    validation: dict[str, Any],
    *,
    default_code: str,
    default_message: str,
    phase: str,
) -> WorkflowError:
    errors = validation.get("errors", [])
    issues = errors if isinstance(errors, list) else []
    issue_codes = {
        str(issue.get("code", ""))
        for issue in issues
        if isinstance(issue, dict)
    }
    policy_codes = {
        code
        for code in issue_codes
        if code.startswith("policy-")
        or code.endswith("-not-allowed")
        or code in {"project-not-allowed", "asset-root-not-allowed", "asset-class-not-allowed", "operation-not-allowed"}
    }
    if "revision-conflict" in issue_codes:
        code = "revision-conflict"
        message = "The asset Revision changed after the plan snapshot was created."
    elif "dirty-package" in issue_codes:
        code = "dirty-package"
        message = "The target package was Dirty when the fixed Revision Export was created."
    elif policy_codes:
        code = "policy-rejected"
        message = "The fixed Project Write Policy rejected this change."
    else:
        code = default_code
        message = default_message
    return WorkflowError(
        code,
        message,
        details={
            "phase": phase,
            "issueCodes": sorted(issue_codes),
            "errors": issues,
            "warnings": validation.get("warnings", []),
        },
    )


def _write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\r\n",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError as exc:
        raise WorkflowError("workflow-path-invalid", "A workflow path could not be inspected safely.") from exc
    return bool(attributes & 0x400) or path.is_symlink()


def _assert_no_reparse_components(path: Path, boundary: Path) -> None:
    boundary = boundary.resolve()
    current = path
    components: list[Path] = []
    while True:
        components.append(current)
        if current == boundary:
            break
        if current.parent == current:
            raise WorkflowError("workflow-path-invalid", "A workflow path escaped its fixed boundary.")
        current = current.parent
    for component in reversed(components):
        if component.exists() and _is_reparse_point(component):
            raise WorkflowError("workflow-path-invalid", "A workflow path contains a Junction or symbolic link.")


def _safe_tail(value: str) -> str:
    return value[-MAX_PROCESS_OUTPUT_CHARS:]


def _safe_report(value: Any, *, configured_paths: tuple[Path, ...]) -> Any:
    roots = [str(path.resolve()) for path in configured_paths]
    roots.extend(item.replace("\\", "/") for item in tuple(roots))

    def sanitize(item: Any, key: str = "") -> Any:
        if isinstance(item, dict):
            return {str(k): sanitize(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [sanitize(entry, key) for entry in item]
        if not isinstance(item, str):
            return item
        if key in {"assetPath", "targetAssetPath", "canonicalPath"}:
            return item
        text = item
        for root in roots:
            if root and root.lower() in text.lower():
                if key.lower().endswith("path"):
                    return Path(text).name
                return "<configured-path>"
        if len(text) >= 3 and text[1:3] in {":\\", ":/"}:
            return Path(text).name if key.lower().endswith("path") else "<local-path>"
        return text

    return sanitize(value)


class PatchWorkflowService:
    """High-level, fixed-path MCP workflow over existing Patch and rollback scripts."""

    def __init__(
        self,
        index_service: IndexQueryService,
        config: PatchWorkflowConfig,
        *,
        process_runner: ProcessRunner | None = None,
        freshness_tracker: IndexFreshnessTracker | None = None,
    ) -> None:
        self.index_service = index_service
        self.config = PatchWorkflowConfig(
            tool_root=config.tool_root.expanduser().resolve(),
            engine_root=config.engine_root.expanduser().resolve(),
            project_path=config.project_path.expanduser().resolve(),
            policy_path=config.policy_path.expanduser().resolve(),
            revision_export=config.revision_export.expanduser().resolve(),
            work_root=config.work_root.expanduser().resolve(),
            backup_root=config.backup_root.expanduser().resolve(),
            commit_enabled=config.commit_enabled,
            process_timeout_seconds=config.process_timeout_seconds,
        )
        self._runner = process_runner or _default_process_runner
        self._lock = threading.RLock()
        self._plans: dict[str, PlanRecord] = {}
        self._dry_runs: dict[str, DryRunRecord] = {}
        self._applies: dict[str, ApplyRecord] = {}
        self._rollback_dry_runs: dict[str, RollbackDryRunRecord] = {}
        self._validate_config()
        self.freshness = freshness_tracker or IndexFreshnessTracker(
            self.index_service,
            self.config.project_path,
            self.config.revision_export,
        )

    @property
    def configured_paths(self) -> tuple[Path, ...]:
        return (
            self.config.tool_root,
            self.config.engine_root,
            self.config.project_path,
            self.config.policy_path,
            self.config.revision_export,
            self.config.work_root,
            self.config.backup_root,
        )

    def _assert_runtime_boundaries(self) -> None:
        output_root = (self.config.tool_root / "Output").resolve()
        backups_root = (self.config.tool_root / "Backups").resolve()
        if not _is_within(self.config.work_root, output_root):
            raise WorkflowError("workflow-path-invalid", "The fixed MCP work root escaped the tool Output directory.")
        if not _is_within(self.config.backup_root, backups_root):
            raise WorkflowError("workflow-path-invalid", "The fixed MCP backup root escaped the tool Backups directory.")
        _assert_no_reparse_components(self.config.work_root, output_root)
        _assert_no_reparse_components(self.config.backup_root, backups_root)

    def _validate_config(self) -> None:
        required_files = (
            self.config.project_path,
            self.config.policy_path,
            self.config.engine_root / "Engine" / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe",
            self.config.tool_root / "scripts" / "RunPatch.ps1",
            self.config.tool_root / "scripts" / "RunRollback.ps1",
            self.config.tool_root / "scripts" / "RunAssetCatalog.ps1",
        )
        for path in required_files:
            if not path.is_file():
                raise WorkflowError("workflow-config-invalid", "A configured workflow file does not exist.")
        if not self.config.revision_export.is_dir():
            raise WorkflowError("workflow-config-invalid", "The configured Revision Export directory does not exist.")
        output_root = (self.config.tool_root / "Output").resolve()
        backups_root = (self.config.tool_root / "Backups").resolve()
        if not _is_within(self.config.work_root, output_root) or self.config.work_root == output_root:
            raise WorkflowError("workflow-config-invalid", "MCP work_root must be a child of the tool Output directory.")
        if not _is_within(self.config.backup_root, backups_root) or self.config.backup_root == backups_root:
            raise WorkflowError("workflow-config-invalid", "MCP backup_root must be a child of the tool Backups directory.")
        if _is_within(self.config.work_root, self.config.project_path.parent / "Content"):
            raise WorkflowError("workflow-config-invalid", "MCP work_root cannot be inside project Content.")
        self.config.work_root.mkdir(parents=True, exist_ok=True)
        self.config.backup_root.mkdir(parents=True, exist_ok=True)
        if not _is_within(self.config.work_root.resolve(), output_root):
            raise WorkflowError("workflow-config-invalid", "MCP work_root resolved outside the tool Output directory.")
        if not _is_within(self.config.backup_root.resolve(), backups_root):
            raise WorkflowError("workflow-config-invalid", "MCP backup_root resolved outside the tool Backups directory.")
        self._assert_runtime_boundaries()
        manifest = _read_json(self.config.revision_export / "manifest.json")
        project_name = manifest.get("projectName")
        if not isinstance(project_name, str) or not project_name:
            raise WorkflowError("workflow-config-invalid", "Revision Export has no valid projectName.")
        actual_project_name = self.config.project_path.stem
        if project_name != actual_project_name:
            raise WorkflowError("workflow-config-invalid", "Revision Export projectName does not match the fixed project file.")
        index_status = self.index_service.check()
        index_project_key = str(index_status.get("projectKey", ""))
        if index_project_key != project_name:
            raise WorkflowError("workflow-config-invalid", "SQLite projectKey does not match the fixed project and Revision Export.")
        self.project_name = project_name
        self.policy_digest = _sha256_bytes(self.config.policy_path.read_bytes())

    def _assert_policy_unchanged(self) -> None:
        current_policy_digest = _sha256_bytes(self.config.policy_path.read_bytes())
        if current_policy_digest != self.policy_digest:
            raise WorkflowError("policy-changed", "The fixed Policy changed after this MCP server started.")

    def status(self) -> dict[str, Any]:
        session_stale = self.freshness.session_stale_assets()
        return {
            "schemaVersion": WORKFLOW_SCHEMA_VERSION,
            "tool": "ue_workflow_status",
            "ok": True,
            "projectName": self.project_name,
            "writeToolsEnabled": True,
            "commitToolsEnabled": self.config.commit_enabled,
            "policyDigest": self.policy_digest,
            "singleAssetSingleOperation": True,
            "receiptRequiredForCommit": True,
            "receiptRequiredForRollbackCommit": True,
            "indexLifecycle": {
                "sessionStale": bool(session_stale),
                "fixedSnapshotsStale": bool(session_stale),
                "sqliteIndexStale": bool(session_stale),
                "revisionExportStale": bool(session_stale),
                "sessionStaleAssetCount": len(session_stale),
                "sessionStaleAssets": session_stale,
            },
        }

    def freshness_status(self) -> dict[str, Any]:
        return self.freshness.project_status()

    def _sanitize_details(self, details: dict[str, Any]) -> dict[str, Any]:
        sanitized = _safe_report(details, configured_paths=self.configured_paths)
        return sanitized if isinstance(sanitized, dict) else {}

    def _prune_records(self) -> None:
        for mapping in (self._plans, self._dry_runs, self._applies, self._rollback_dry_runs):
            while len(mapping) > MAX_WORKFLOW_RECORDS:
                mapping.pop(next(iter(mapping)))

    def _safe_work_path(self, *parts: str) -> Path:
        self._assert_runtime_boundaries()
        path = self.config.work_root.joinpath(*parts)
        if not _is_within(path, self.config.work_root):
            raise WorkflowError("workflow-path-invalid", "Generated workflow path escaped the fixed work root.")
        _assert_no_reparse_components(path, self.config.work_root.resolve())
        return path

    def _plan_directory(self, plan_id: str) -> Path:
        return self._safe_work_path("plans", plan_id)

    def _assert_asset_fresh(self, asset_path: str) -> dict[str, Any]:
        freshness = self.freshness.inspect_asset(asset_path)
        if freshness.get("state") == "stale":
            raise WorkflowError(
                "index-stale",
                "The requested asset differs from the fixed SQLite index or Revision Export.",
                details=self._sanitize_details({"freshness": freshness}),
            )
        if freshness.get("state") != "fresh":
            raise WorkflowError(
                "index-freshness-unavailable",
                "The requested asset could not be compared across SQLite, Revision Export, and disk.",
                details=self._sanitize_details({"freshness": freshness}),
            )
        return freshness

    def _validate_plan_file(self, record: PlanRecord) -> dict[str, Any]:
        self._assert_policy_unchanged()
        stored_patch = _read_json(record.patch_path)
        stored_digest = _sha256_bytes(_json_bytes(stored_patch))
        if stored_digest != record.digest or stored_patch != record.patch:
            raise WorkflowError("plan-tampered", "The stored MCP patch plan changed after it was created.")
        assets = stored_patch.get("assets", [])
        if not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
            raise WorkflowError("plan-invalid", "The stored MCP patch no longer contains exactly one asset.")
        asset_path = str(assets[0].get("assetPath", ""))
        self._assert_asset_fresh(asset_path)
        validation = validate_patch(record.patch_path, self.config.policy_path, self.config.revision_export)
        if not validation.get("valid"):
            raise _validation_error(
                validation,
                default_code="patch-validation-failed",
                default_message="The stored patch no longer passes Policy and Revision validation.",
                phase="stored-plan-validation",
            )
        return validation

    def plan_patch(
        self,
        *,
        asset_path: str,
        operation: str,
        target: dict[str, Any] | None,
        value: Any,
        description: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_policy_unchanged()
            if operation not in OPERATION_REGISTRY:
                raise WorkflowError("unsupported-operation", "The requested operation is not supported by UE Agent Kit.")
            asset_result = self.index_service.get_asset(asset_path, symbol_limit=1, reference_limit=1, graph_limit=1, node_limit=1)
            if not asset_result.get("found"):
                raise WorkflowError("asset-not-indexed", "The requested asset is not present in the fixed SQLite index.")
            asset = asset_result["asset"]
            self._assert_asset_fresh(asset_path)
            revision = asset.get("revision_value")
            asset_class = asset.get("asset_class")
            if not isinstance(revision, str) or not revision.startswith("sha256:"):
                raise WorkflowError("revision-unavailable", "The indexed asset has no usable SHA-256 Revision.")
            if not isinstance(asset_class, str) or not asset_class:
                raise WorkflowError("asset-class-unavailable", "The indexed asset has no usable Asset Class.")
            target_value = target if target is not None else {}
            patch_id = f"mcp-{uuid.uuid4().hex}"
            patch = {
                "schemaVersion": "1.0",
                "patchId": patch_id,
                "projectName": self.project_name,
                "description": description,
                "assets": [
                    {
                        "assetPath": asset_path,
                        "expectedRevision": revision,
                        "expectedAssetClass": asset_class,
                        "operations": [
                            {
                                "operationId": f"op-{uuid.uuid4().hex}",
                                "operation": operation,
                                "target": target_value,
                                "value": value,
                            }
                        ],
                    }
                ],
            }
            digest = _sha256_bytes(_json_bytes(patch))
            plan_id = "plan_" + secrets.token_urlsafe(18)
            directory = self._plan_directory(plan_id)
            patch_path = directory / "patch.json"
            _write_json_atomic(patch_path, patch)
            validation = validate_patch(patch_path, self.config.policy_path, self.config.revision_export)
            if not validation.get("valid"):
                raise _validation_error(
                    validation,
                    default_code="patch-plan-rejected",
                    default_message="The proposed patch was rejected by Policy or Revision validation.",
                    phase="plan-validation",
                )
            record = PlanRecord(plan_id, digest, patch, patch_path, validation)
            self._plans[plan_id] = record
            self._prune_records()
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_plan_patch",
                "ok": True,
                "planId": plan_id,
                "patchDigest": digest,
                "projectName": self.project_name,
                "assetPath": asset_path,
                "assetClass": asset_class,
                "expectedRevision": revision,
                "operation": operation,
                "target": target_value,
                "value": value,
                "risk": OPERATION_REGISTRY[operation].risk,
                "commitAllowedByPolicy": bool(validation.get("commitAllowedByPolicy")),
                "commitToolsEnabled": self.config.commit_enabled,
                "nextStep": "Call ue_dry_run_patch with this planId.",
            }

    def prepare_high_level_change(
        self,
        *,
        tool_name: str,
        mode: Literal["Plan", "DryRun"],
        asset_path: str,
        operation: str,
        target: dict[str, Any],
        value: Any,
        description: str = "",
    ) -> dict[str, Any]:
        if mode not in HIGH_LEVEL_CHANGE_MODES:
            raise ValueError("mode must be Plan or DryRun")
        plan = self.plan_patch(
            asset_path=asset_path,
            operation=operation,
            target=target,
            value=value,
            description=description,
        )
        if mode == "Plan":
            response = dict(plan)
            response.update(
                {
                    "tool": tool_name,
                    "mode": "Plan",
                    "underlyingTool": "ue_plan_patch",
                    "underlyingOperation": operation,
                }
            )
            return response
        dry_run = self.dry_run_patch(str(plan["planId"]))
        response = dict(dry_run)
        response.update(
            {
                "tool": tool_name,
                "mode": "DryRun",
                "assetPath": asset_path,
                "underlyingTools": ["ue_plan_patch", "ue_dry_run_patch"],
                "underlyingOperation": operation,
                "risk": plan.get("risk", ""),
                "commitToolsEnabled": plan.get("commitToolsEnabled", False),
            }
        )
        return response

    def _run_script(
        self,
        script_name: str,
        script_arguments: list[str],
        *,
        stage: str,
        report_path: Path,
    ) -> ProcessResult:
        self._assert_runtime_boundaries()
        script = self.config.tool_root / "scripts" / script_name
        arguments = [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            *script_arguments,
        ]
        try:
            result = self._runner(arguments, self.config.tool_root, self.config.process_timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            details = self._sanitize_details(
                {
                    "stage": stage,
                    "diagnosticId": _diagnostic_id(stage, "timeout", stdout[-1024:], stderr[-1024:]),
                    "reportId": _report_id(stage, report_path),
                    "stdoutTail": _safe_tail(stdout),
                    "stderrTail": _safe_tail(stderr),
                }
            )
            raise WorkflowError(
                "workflow-timeout",
                "The Unreal workflow process exceeded its fixed timeout.",
                details=details,
            ) from exc
        return result

    def _raise_process_failure(
        self,
        *,
        stage: str,
        result: ProcessResult,
        report_path: Path,
        fallback_code: str,
        fallback_message: str,
    ) -> None:
        crashed = _is_ue_crash(result)
        code = "ue-process-crashed" if crashed else fallback_code
        message = "The Unreal workflow process crashed." if crashed else fallback_message
        details = self._sanitize_details(
            {
                "stage": stage,
                "diagnosticId": _diagnostic_id(
                    stage,
                    str(result.exit_code),
                    result.stdout[-2048:],
                    result.stderr[-2048:],
                ),
                "reportId": _report_id(stage, report_path),
                "exitCode": result.exit_code,
                "stdoutTail": _safe_tail(result.stdout),
                "stderrTail": _safe_tail(result.stderr),
            }
        )
        raise WorkflowError(code, message, details=details)

    def dry_run_patch(self, plan_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._plans.get(plan_id)
            if record is None:
                raise WorkflowError("plan-not-found", "The requested planId is not active in this MCP server session.")
            if record.consumed:
                raise WorkflowError("plan-consumed", "The requested plan has already been committed.")
            validation = self._validate_plan_file(record)
            directory = self._plan_directory(plan_id) / "dry-run"
            report_path = directory / "report.json"
            validation_report = directory / "validation.json"
            result = self._run_script(
                "RunPatch.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Patch", str(record.patch_path),
                    "-Policy", str(self.config.policy_path),
                    "-RevisionExport", str(self.config.revision_export),
                    "-Mode", "DryRun",
                    "-Report", str(report_path),
                    "-ValidationReport", str(validation_report),
                    "-BackupDir", str(self.config.backup_root),
                ],
                stage="patch-dry-run",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="patch-dry-run",
                    result=result,
                    report_path=report_path,
                    fallback_code="dry-run-failed",
                    fallback_message="The Unreal Dry Run failed.",
                )
            report = _read_json(report_path, stage="patch-dry-run")
            gates = {
                "modeDryRun": report.get("mode") == "DryRun",
                "notSaved": report.get("saved") is False,
                "rolledBack": report.get("rolledBack") is True,
                "rollbackValueMatch": report.get("rollbackValueMatch") is True,
                "diskUnchanged": report.get("diskUnchanged") is True,
                "revisionUnchanged": report.get("beforeRevision") == report.get("afterRevision"),
            }
            if not all(gates.values()):
                raise WorkflowError("dry-run-gate-failed", "The Dry Run report did not satisfy every safety gate.", details=gates)
            receipt = "dry_" + secrets.token_urlsafe(24)
            self._dry_runs[receipt] = DryRunRecord(receipt, plan_id, record.digest, report_path, report)
            self._prune_records()
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_dry_run_patch",
                "ok": True,
                "planId": plan_id,
                "patchDigest": record.digest,
                "dryRunReceipt": receipt,
                "reportId": _report_id("patch-dry-run", report_path),
                "gates": gates,
                "report": _safe_report(report, configured_paths=self.configured_paths),
                "validationSummary": validation.get("summary", {}),
                "nextStep": f"To commit, call ue_apply_patch with confirmation 'COMMIT {plan_id}'.",
            }

    def apply_patch(self, plan_id: str, dry_run_receipt: str, confirmation: str) -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Commit tools were not enabled when this MCP server started.")
            record = self._plans.get(plan_id)
            dry_run = self._dry_runs.get(dry_run_receipt)
            if record is None or dry_run is None:
                raise WorkflowError("receipt-not-found", "The plan or Dry Run receipt is not active in this MCP server session.")
            if confirmation != f"COMMIT {plan_id}":
                raise WorkflowError("commit-confirmation-required", "Commit confirmation did not exactly match the required planId phrase.")
            if dry_run.consumed or dry_run.plan_id != plan_id or dry_run.plan_digest != record.digest:
                raise WorkflowError("receipt-invalid", "The Dry Run receipt is used, stale, or belongs to another plan.")
            validation = self._validate_plan_file(record)
            if not validation.get("commitAllowedByPolicy"):
                raise WorkflowError("commit-not-allowed", "The fixed Policy does not enable Commit.")
            directory = self._plan_directory(plan_id) / "commit"
            report_path = directory / "report.json"
            validation_report = directory / "validation.json"
            manifest_path = self.config.backup_root / f"{plan_id}.manifest.json"
            if manifest_path.exists():
                raise WorkflowError("manifest-exists", "The fixed manifest output already exists for this plan.")
            result = self._run_script(
                "RunPatch.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Patch", str(record.patch_path),
                    "-Policy", str(self.config.policy_path),
                    "-RevisionExport", str(self.config.revision_export),
                    "-Mode", "Commit",
                    "-Report", str(report_path),
                    "-ValidationReport", str(validation_report),
                    "-BackupDir", str(self.config.backup_root),
                    "-Manifest", str(manifest_path),
                ],
                stage="patch-commit",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="patch-commit",
                    result=result,
                    report_path=report_path,
                    fallback_code="commit-failed",
                    fallback_message="The Unreal Commit failed.",
                )
            report = _read_json(report_path, stage="patch-commit")
            if report.get("mode") != "Commit" or report.get("saved") is not True:
                raise WorkflowError("commit-report-invalid", "The Commit report did not confirm a saved asset.")
            if not manifest_path.is_file():
                raise WorkflowError("manifest-missing", "Commit succeeded but the fixed Backup Manifest was not created.")
            before_revision = str(report.get("beforeRevision", ""))
            after_revision = str(report.get("afterRevision", ""))
            if not before_revision.startswith("sha256:") or not after_revision.startswith("sha256:") or before_revision == after_revision:
                raise WorkflowError("commit-revision-invalid", "The Commit report did not contain a valid Revision transition.")
            receipt = "apply_" + secrets.token_urlsafe(24)
            committed_asset_path = str(report.get("assetPath", ""))
            self._applies[receipt] = ApplyRecord(
                receipt,
                plan_id,
                record.digest,
                committed_asset_path,
                before_revision,
                after_revision,
                manifest_path,
                report_path,
                report,
            )
            freshness = self.freshness.mark_commit(
                committed_asset_path,
                before_revision,
                after_revision,
            )
            dry_run.consumed = True
            record.consumed = True
            self._prune_records()
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_apply_patch",
                "ok": True,
                "planId": plan_id,
                "patchDigest": record.digest,
                "applyReceipt": receipt,
                "assetPath": report.get("assetPath", ""),
                "beforeRevision": before_revision,
                "afterRevision": after_revision,
                "manifestId": manifest_path.name,
                "reportId": _report_id("patch-commit", report_path),
                "indexFreshness": freshness,
                "report": _safe_report(report, configured_paths=self.configured_paths),
                "nextStep": "Call ue_verify_asset with this applyReceipt. The fixed index remains stale until refreshed or rolled back.",
            }

    def verify_asset(self, apply_receipt: str) -> dict[str, Any]:
        with self._lock:
            apply = self._applies.get(apply_receipt)
            if apply is None:
                raise WorkflowError("apply-receipt-not-found", "The applyReceipt is not active in this MCP server session.")
            output = self._safe_work_path("verify", apply_receipt)
            if output.exists():
                shutil.rmtree(output)
            output.mkdir(parents=True, exist_ok=False)
            asset_package = apply.asset_path.split(".", 1)[0]
            result = self._run_script(
                "RunAssetCatalog.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Asset", asset_package,
                    "-Output", str(output),
                ],
                stage="verify-export",
                report_path=output / "manifest.json",
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="verify-export",
                    result=result,
                    report_path=output / "manifest.json",
                    fallback_code="verify-export-failed",
                    fallback_message="The independent Unreal verification export failed.",
                )
            canonical_files = list((output / "canonical").rglob("*.json"))
            if len(canonical_files) != 1:
                raise WorkflowError("verify-export-invalid", "Independent verification did not produce exactly one Canonical asset.")
            canonical = _read_json(canonical_files[0], stage="verify-canonical")
            revision = canonical.get("revision", {})
            actual_revision = revision.get("value", "") if isinstance(revision, dict) else ""
            verified = canonical.get("assetPath") == apply.asset_path and actual_revision == apply.after_revision
            if not verified:
                raise WorkflowError(
                    "verify-revision-mismatch",
                    "Independent Unreal reload did not match the committed asset and Revision.",
                    details={"expectedRevision": apply.after_revision, "actualRevision": actual_revision},
                )
            apply.verified = True
            freshness = self.freshness.inspect_asset(apply.asset_path)
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_verify_asset",
                "ok": True,
                "applyReceipt": apply_receipt,
                "assetPath": apply.asset_path,
                "expectedRevision": apply.after_revision,
                "actualRevision": actual_revision,
                "verified": True,
                "assetClass": canonical.get("assetClass", ""),
                "packageDirty": revision.get("packageDirty", False) if isinstance(revision, dict) else False,
                "reportId": _report_id("verify-export", output / "manifest.json"),
                "indexFreshness": freshness,
                "nextStep": "Keep the change and refresh the asset index, or call ue_rollback_patch in DryRun mode before an explicit rollback Commit.",
            }

    def rollback_patch(
        self,
        apply_receipt: str,
        *,
        mode: Literal["DryRun", "Commit"] = "DryRun",
        rollback_dry_run_receipt: str = "",
        confirmation: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_policy_unchanged()
            apply = self._applies.get(apply_receipt)
            if apply is None:
                raise WorkflowError("apply-receipt-not-found", "The applyReceipt is not active in this MCP server session.")
            if apply.rolled_back:
                raise WorkflowError("already-rolled-back", "This applyReceipt has already been rolled back.")
            if mode == "DryRun":
                directory = self._safe_work_path("rollback", apply_receipt, "dry-run")
                report_path = directory / "report.json"
                result = self._run_script(
                    "RunRollback.ps1",
                    [
                        "-EngineRoot", str(self.config.engine_root),
                        "-ProjectPath", str(self.config.project_path),
                        "-Manifest", str(apply.manifest_path),
                        "-Policy", str(self.config.policy_path),
                        "-BackupRoot", str(self.config.backup_root),
                        "-Mode", "DryRun",
                        "-Report", str(report_path),
                    ],
                    stage="rollback-dry-run",
                    report_path=report_path,
                )
                if result.exit_code != 0:
                    self._raise_process_failure(
                        stage="rollback-dry-run",
                        result=result,
                        report_path=report_path,
                        fallback_code="rollback-dry-run-failed",
                        fallback_message="Rollback Dry Run failed.",
                    )
                report = _read_json(report_path, stage="rollback-dry-run")
                if report.get("valid") is not True or report.get("wroteDisk") is not False:
                    raise WorkflowError("rollback-dry-run-invalid", "Rollback Dry Run did not confirm a valid zero-write result.")
                receipt = "rollback_dry_" + secrets.token_urlsafe(24)
                self._rollback_dry_runs[receipt] = RollbackDryRunRecord(receipt, apply_receipt, report_path, report)
                self._prune_records()
                return {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_rollback_patch",
                    "ok": True,
                    "mode": "DryRun",
                    "applyReceipt": apply_receipt,
                    "rollbackDryRunReceipt": receipt,
                    "reportId": _report_id("rollback-dry-run", report_path),
                    "report": _safe_report(report, configured_paths=self.configured_paths),
                    "nextStep": f"To restore, call ue_rollback_patch with mode Commit and confirmation 'ROLLBACK {apply_receipt}'.",
                }

            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Rollback Commit was not enabled when this MCP server started.")
            dry_run = self._rollback_dry_runs.get(rollback_dry_run_receipt)
            if dry_run is None or dry_run.apply_receipt != apply_receipt or dry_run.consumed:
                raise WorkflowError("rollback-receipt-invalid", "A fresh rollback Dry Run receipt is required.")
            if confirmation != f"ROLLBACK {apply_receipt}":
                raise WorkflowError("rollback-confirmation-required", "Rollback confirmation did not exactly match the required applyReceipt phrase.")
            directory = self._safe_work_path("rollback", apply_receipt, "commit")
            report_path = directory / "report.json"
            verification_output = directory / "verify"
            verification_report = directory / "verification.json"
            result = self._run_script(
                "RunRollback.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Manifest", str(apply.manifest_path),
                    "-Policy", str(self.config.policy_path),
                    "-BackupRoot", str(self.config.backup_root),
                    "-Mode", "Commit",
                    "-Report", str(report_path),
                    "-VerificationOutput", str(verification_output),
                    "-VerificationReport", str(verification_report),
                ],
                stage="rollback-commit",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="rollback-commit",
                    result=result,
                    report_path=report_path,
                    fallback_code="rollback-commit-failed",
                    fallback_message="Rollback Commit or independent verification failed.",
                )
            report = _read_json(report_path, stage="rollback-commit")
            verification = _read_json(verification_report, stage="rollback-verification")
            if report.get("restored") is not True or verification.get("verified") is not True:
                raise WorkflowError("rollback-report-invalid", "Rollback reports did not confirm restore and independent verification.")
            restored_revision = str(
                verification.get(
                    "actualRevision",
                    verification.get("restoredRevision", verification.get("expectedRevision", "")),
                )
            )
            if restored_revision != apply.before_revision:
                raise WorkflowError("rollback-revision-mismatch", "Rollback verification did not match the pre-Commit Revision.")
            dry_run.consumed = True
            apply.rolled_back = True
            freshness = self.freshness.mark_rollback(apply.asset_path, restored_revision)
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_rollback_patch",
                "ok": True,
                "mode": "Commit",
                "applyReceipt": apply_receipt,
                "assetPath": apply.asset_path,
                "restored": True,
                "expectedRevision": apply.before_revision,
                "reportId": _report_id("rollback-commit", report_path),
                "verificationReportId": _report_id("rollback-verification", verification_report),
                "indexFreshness": freshness,
                "verification": _safe_report(verification, configured_paths=self.configured_paths),
                "report": _safe_report(report, configured_paths=self.configured_paths),
            }

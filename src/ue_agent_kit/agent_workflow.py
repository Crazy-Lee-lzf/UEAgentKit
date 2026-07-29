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
from .database import (
    CURRENT_SCHEMA_VERSION,
    assert_fts5_available,
    get_metadata,
    get_schema_version,
    open_database,
    set_metadata,
)
from .freshness import IndexFreshnessTracker
from .indexer import build_index
from .patches import OPERATION_REGISTRY, validate_patch
from .snapshot_lifecycle import (
    ActiveSnapshot,
    SnapshotLifecycleError,
    assert_quiescent_database,
    clone_tree,
    new_generation_id,
    sha256_file,
    utc_now_iso,
    write_active_pointer,
)


WORKFLOW_SCHEMA_VERSION = "1.0"
MEMORY_TASK_EVIDENCE_SCHEMA_VERSION = "1.0"
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
    active_snapshot: ActiveSnapshot | None = None


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


def _verified_memory_task_evidence(
    apply: ApplyRecord,
    *,
    validation_report_id: str,
    actual_revision: str,
) -> dict[str, Any]:
    manifest_id = apply.manifest_path.name
    return {
        "schemaVersion": MEMORY_TASK_EVIDENCE_SCHEMA_VERSION,
        "tool": "ue_memory_record_task",
        "arguments": {
            "task_key": f"patch:{apply.plan_id}",
            "title": f"Verified patch {apply.plan_id}",
            "conclusion": (
                f"The committed asset {apply.asset_path} was independently reloaded "
                f"and matched Revision {actual_revision}."
            ),
            "outcome": "succeeded",
            "patch_ref": f"patch:{apply.plan_digest}",
            "backup_manifest_ref": f"backup-manifest:{manifest_id}",
            "validation_evidence_ref": f"validation-evidence:{validation_report_id}",
            "revision_set": [
                {
                    "assetPath": apply.asset_path,
                    "revision": actual_revision,
                    "revisionStable": True,
                }
            ],
            "scopes": [
                {
                    "scopeType": "asset",
                    "scopeKey": apply.asset_path,
                }
            ],
            "confidence": 1.0,
            "patch_details": {
                "planId": apply.plan_id,
                "patchDigest": apply.plan_digest,
                "beforeRevision": apply.before_revision,
                "afterRevision": apply.after_revision,
            },
            "backup_manifest_details": {
                "manifestId": manifest_id,
            },
            "validation_evidence_details": {
                "reportId": validation_report_id,
                "independentReload": True,
                "verified": True,
                "expectedRevision": apply.after_revision,
                "actualRevision": actual_revision,
            },
            "details": {
                "workflowEvidenceSchemaVersion": MEMORY_TASK_EVIDENCE_SCHEMA_VERSION,
                "workflowTool": "ue_verify_asset",
            },
        },
    }


@dataclass
class RollbackDryRunRecord:
    receipt: str
    apply_receipt: str
    report_path: Path
    report: dict[str, Any]
    consumed: bool = False


@dataclass
class SaveAuthorizationRecord:
    receipt: str
    asset_path: str
    asset_class: str
    package_name: str
    expected_disk_revision: str
    editor_session_id: str
    editor_process_id: int
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
        live_editor_service: Any | None = None,
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
            active_snapshot=config.active_snapshot,
        )
        self._runner = process_runner or _default_process_runner
        self._lock = threading.RLock()
        self._plans: dict[str, PlanRecord] = {}
        self._dry_runs: dict[str, DryRunRecord] = {}
        self._applies: dict[str, ApplyRecord] = {}
        self._rollback_dry_runs: dict[str, RollbackDryRunRecord] = {}
        self._save_authorizations: dict[str, SaveAuthorizationRecord] = {}
        self.live_editor_service = live_editor_service
        self.active_snapshot = self.config.active_snapshot
        self._refresh_applied = False
        self._validate_config()
        self.freshness = freshness_tracker or IndexFreshnessTracker(
            self.index_service,
            self.config.project_path,
            self.config.revision_export,
        )

    @property
    def configured_paths(self) -> tuple[Path, ...]:
        paths = [
            self.config.tool_root,
            self.config.engine_root,
            self.config.project_path,
            self.config.policy_path,
            self.config.revision_export,
            self.config.work_root,
            self.config.backup_root,
        ]
        if self.active_snapshot is not None:
            paths.extend(
                [
                    self.active_snapshot.configured_database,
                    self.active_snapshot.configured_revision_export,
                    self.active_snapshot.database,
                    self.active_snapshot.revision_export,
                    self.active_snapshot.pointer_path,
                ]
            )
        return tuple(dict.fromkeys(paths))

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
        if self.active_snapshot is not None:
            if self.active_snapshot.project_name != self.config.project_path.stem:
                raise WorkflowError("workflow-config-invalid", "The active snapshot project does not match the fixed project file.")
            if self.active_snapshot.work_root != self.config.work_root:
                raise WorkflowError("workflow-config-invalid", "The active snapshot pointer does not use the fixed MCP work root.")
            if not self.active_snapshot.database.is_file() or not self.active_snapshot.revision_export.is_dir():
                raise WorkflowError("workflow-config-invalid", "The active snapshot pair is incomplete.")
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
        self._assert_session_current()
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
                "activeSnapshotGenerationId": self.active_snapshot.generation_id if self.active_snapshot is not None else "",
                "sessionUsesFrozenSnapshot": self.active_snapshot is not None,
                "refreshAppliedInSession": self._refresh_applied,
                "restartRequired": self._refresh_applied,
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
        for mapping in (self._plans, self._dry_runs, self._applies, self._rollback_dry_runs, self._save_authorizations):
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

    def _assert_session_current(self) -> None:
        if self._refresh_applied:
            raise WorkflowError(
                "snapshot-refresh-restart-required",
                "This MCP session already switched the active snapshot generation and must be restarted before more workflow actions.",
            )

    @staticmethod
    def _validate_refresh_asset_path(asset_path: str) -> str:
        if (
            not isinstance(asset_path, str)
            or not asset_path.startswith("/Game/")
            or len(asset_path) > 512
            or "\\" in asset_path
            or ":" in asset_path
            or ".." in asset_path
            or any(ord(character) < 32 for character in asset_path)
        ):
            raise WorkflowError("snapshot-refresh-invalid-asset", "asset_path must be one exact /Game Object Path.")
        package_path, separator, object_name = asset_path.rpartition(".")
        if not separator or not object_name or "/" in object_name or package_path.rfind("/") >= len(package_path) - 1:
            raise WorkflowError("snapshot-refresh-invalid-asset", "asset_path must be one exact /Game Object Path.")
        return asset_path

    def _read_fixed_policy(self) -> dict[str, Any]:
        self._assert_policy_unchanged()
        policy = _read_json(self.config.policy_path)
        if policy.get("schemaVersion") != "1.0":
            raise WorkflowError("policy-rejected", "The fixed Project Write Policy schema is unsupported.")
        projects = policy.get("allowedProjectNames", [])
        if not isinstance(projects, list) or self.project_name not in projects:
            raise WorkflowError("policy-rejected", "The fixed Project Write Policy does not authorize this project.")
        return policy

    @staticmethod
    def _asset_matches_root(asset_path: str, root: str) -> bool:
        normalized = root.rstrip("/")
        return asset_path == normalized or asset_path.startswith(normalized + "/")

    def _assert_refresh_policy(self, asset_path: str, asset_class: str = "") -> dict[str, Any]:
        policy = self._read_fixed_policy()
        roots = policy.get("allowedAssetRoots", [])
        if not isinstance(roots, list) or not any(
            isinstance(root, str) and self._asset_matches_root(asset_path, root)
            for root in roots
        ):
            raise WorkflowError("policy-rejected", "The fixed Project Write Policy does not authorize this asset root.")
        if asset_class:
            classes = policy.get("allowedAssetClasses", [])
            if not isinstance(classes, list) or asset_class not in classes:
                raise WorkflowError("policy-rejected", "The fixed Project Write Policy does not authorize this Asset Class.")
        return policy

    def _inspect_refresh_live_state(self, asset_path: str) -> dict[str, Any]:
        descriptor = self.config.project_path.parent / "Saved" / "UEAgentKit" / "EditorBridge.json"
        if self.live_editor_service is None:
            if descriptor.is_file():
                raise WorkflowError(
                    "live-editor-status-required",
                    "An Editor Bridge descriptor exists, so safe refresh requires Live Editor mode to verify that the asset is not Dirty.",
                )
            return {"state": "offline", "loaded": False, "packageDirty": False}
        try:
            status = self.live_editor_service.status()
        except Exception as exc:
            raise WorkflowError("live-editor-status-unavailable", "Live Editor state could not be checked before snapshot refresh.") from exc
        if status.get("state") != "available":
            if descriptor.is_file():
                raise WorkflowError(
                    "live-editor-status-unavailable",
                    "The fixed Editor Bridge is not available, so the target Dirty state cannot be trusted.",
                )
            return {"state": "offline", "loaded": False, "packageDirty": False}
        try:
            payload = self.live_editor_service.call_tool("ue_inspect_asset_live", {"assetPath": asset_path})
        except Exception as exc:
            raise WorkflowError("live-editor-status-unavailable", "The target asset could not be inspected in the fixed Editor session.") from exc
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        memory = result.get("memory", {}) if isinstance(result, dict) else {}
        if not isinstance(memory, dict):
            memory = {}
        if memory.get("packageDirty") is True:
            raise WorkflowError(
                "live-editor-asset-dirty",
                "The target asset has unsaved Editor memory changes and cannot be added to a disk-backed snapshot.",
            )
        return {
            "state": str(memory.get("state", "unknown")),
            "loaded": bool(memory.get("loaded")),
            "packageDirty": bool(memory.get("packageDirty")),
        }

    @staticmethod
    def _package_file(project_path: Path, package_name: str, asset_class: str) -> Path:
        if not package_name.startswith("/Game/"):
            raise WorkflowError("snapshot-refresh-invalid-export", "The exported asset is outside the /Game mount.")
        relative_parts = [part for part in package_name[len("/Game/") :].split("/") if part]
        if not relative_parts or any(part in {".", ".."} for part in relative_parts):
            raise WorkflowError("snapshot-refresh-invalid-export", "The exported package name is invalid.")
        content_root = (project_path.parent / "Content").resolve()
        base = content_root.joinpath(*relative_parts)
        preferred = ".umap" if asset_class == "/Script/Engine.World" else ".uasset"
        candidates = [base.with_suffix(preferred), base.with_suffix(".uasset" if preferred == ".umap" else ".umap")]
        for candidate in candidates:
            try:
                candidate.resolve().relative_to(content_root)
            except ValueError:
                continue
            if candidate.is_file():
                return candidate.resolve()
        raise WorkflowError("snapshot-refresh-package-missing", "The exported asset Package file is missing from the fixed project.")

    def _export_refresh_candidate(self, asset_path: str, output: Path) -> dict[str, Any]:
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=False)
        package_path = asset_path.split(".", 1)[0]
        result = self._run_script(
            "RunAssetCatalog.ps1",
            [
                "-EngineRoot", str(self.config.engine_root),
                "-ProjectPath", str(self.config.project_path),
                "-Asset", package_path,
                "-Output", str(output),
            ],
            stage="snapshot-refresh-export",
            report_path=output / "manifest.json",
        )
        if result.exit_code != 0:
            self._raise_process_failure(
                stage="snapshot-refresh-export",
                result=result,
                report_path=output / "manifest.json",
                fallback_code="snapshot-refresh-export-failed",
                fallback_message="The independent Unreal export for snapshot refresh failed.",
            )
        manifest = _read_json(output / "manifest.json", stage="snapshot-refresh-export")
        manifest_assets = manifest.get("assets", [])
        if (
            manifest.get("projectName") != self.project_name
            or int(manifest.get("assetCount", -1)) != 1
            or int(manifest.get("successCount", -1)) != 1
            or int(manifest.get("failureCount", -1)) != 0
            or not isinstance(manifest_assets, list)
            or len(manifest_assets) != 1
            or not isinstance(manifest_assets[0], dict)
            or not manifest_assets[0].get("success")
            or manifest_assets[0].get("assetPath") != asset_path
        ):
            raise WorkflowError(
                "snapshot-refresh-export-invalid",
                "The refresh Manifest must confirm exactly the requested asset in the fixed project with zero failures.",
            )
        canonical_files = list((output / "canonical").rglob("*.json"))
        if len(canonical_files) != 1:
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh export must contain exactly one Canonical asset.")
        canonical_path = canonical_files[0]
        canonical = _read_json(canonical_path, stage="snapshot-refresh-canonical")
        if canonical.get("projectName") != self.project_name or canonical.get("assetPath") != asset_path:
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh Canonical asset does not match the fixed project and requested asset.")
        revision = canonical.get("revision", {})
        if not isinstance(revision, dict):
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh Canonical asset has no Revision object.")
        revision_value = str(revision.get("value", ""))
        revision_digest = revision_value.removeprefix("sha256:")
        if (
            not revision.get("available")
            or revision.get("packageDirty")
            or not revision_value.startswith("sha256:")
            or len(revision_digest) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in revision_digest)
        ):
            raise WorkflowError("snapshot-refresh-export-invalid", "The refresh Canonical asset has no clean SHA-256 Package Revision.")
        asset_class = str(canonical.get("assetClass", ""))
        package_name = str(canonical.get("packageName", ""))
        expected_package_name = asset_path.split(".", 1)[0]
        if not asset_class or package_name != expected_package_name:
            raise WorkflowError(
                "snapshot-refresh-export-invalid",
                "The refresh Canonical asset has no matching class and package identity.",
            )
        self._assert_refresh_policy(asset_path, asset_class)
        package_file = self._package_file(self.config.project_path, package_name, asset_class)
        disk_revision = "sha256:" + sha256_file(package_file)
        if revision_value != disk_revision:
            raise WorkflowError(
                "snapshot-refresh-revision-mismatch",
                "The staged Canonical Revision does not match the current disk Package SHA-256.",
                details={"canonicalRevision": revision_value, "diskRevision": disk_revision},
            )
        entry = dict(manifest_assets[0])
        manifest_json_path = Path(str(entry.get("jsonPath", "")))
        if not manifest_json_path.is_absolute():
            manifest_json_path = output / manifest_json_path
        if manifest_json_path.resolve() != canonical_path.resolve():
            raise WorkflowError(
                "snapshot-refresh-export-invalid",
                "The refresh Manifest Canonical path does not match the requested staged asset.",
            )
        bpctx_files = list((output / "bpctx").rglob("*.bpctx")) if (output / "bpctx").is_dir() else []
        bpctx_path = bpctx_files[0] if len(bpctx_files) == 1 else None
        return {
            "manifest": manifest,
            "manifestEntry": entry,
            "canonical": canonical,
            "canonicalPath": canonical_path,
            "bpctxPath": bpctx_path,
            "revision": revision_value,
            "assetClass": asset_class,
            "packageName": package_name,
            "diskFileSize": package_file.stat().st_size,
        }

    @staticmethod
    def _tree_size(root: Path) -> int:
        total = 0
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        return total

    @staticmethod
    def _find_export_canonical(export_root: Path, asset_path: str) -> list[Path]:
        matches: list[Path] = []
        canonical_root = export_root / "canonical"
        if not canonical_root.is_dir():
            return matches
        for candidate in canonical_root.rglob("*.json"):
            try:
                value = json.loads(candidate.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict) and value.get("assetPath") == asset_path:
                matches.append(candidate)
        return matches

    def _merge_refresh_export(self, active_export: Path, next_export: Path, candidate_root: Path, candidate: dict[str, Any]) -> None:
        clone_tree(
            active_export,
            next_export,
            prefer_hardlinks=bool(self.active_snapshot and not self.active_snapshot.legacy),
        )
        asset_path = str(candidate["canonical"].get("assetPath", ""))
        old_canonical = self._find_export_canonical(next_export, asset_path)
        for path in old_canonical:
            try:
                relative = path.relative_to(next_export / "canonical")
            except ValueError:
                relative = None
            path.unlink()
            if relative is not None:
                (next_export / "bpctx" / relative.with_suffix(".bpctx")).unlink(missing_ok=True)

        source_canonical = Path(candidate["canonicalPath"])
        relative = source_canonical.relative_to(candidate_root / "canonical")
        destination_canonical = next_export / "canonical" / relative
        destination_canonical.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_canonical, destination_canonical)
        destination_bpctx: Path | None = None
        if candidate.get("bpctxPath") is not None:
            source_bpctx = Path(candidate["bpctxPath"])
            bpctx_relative = source_bpctx.relative_to(candidate_root / "bpctx")
            destination_bpctx = next_export / "bpctx" / bpctx_relative
            destination_bpctx.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_bpctx, destination_bpctx)

        manifest_path = next_export / "manifest.json"
        manifest = _read_json(manifest_path) if manifest_path.is_file() else {}
        entries = [dict(item) for item in manifest.get("assets", []) if isinstance(item, dict)]
        replacement = dict(candidate["manifestEntry"])
        replacement["assetPath"] = asset_path
        replacement["success"] = True
        replacement["jsonPath"] = str(destination_canonical)
        if destination_bpctx is not None:
            replacement["bpctxPath"] = str(destination_bpctx)
        else:
            replacement.pop("bpctxPath", None)
        replaced = False
        for index, entry in enumerate(entries):
            if entry.get("assetPath") == asset_path:
                entries[index] = replacement
                replaced = True
                break
        if not replaced:
            entries.append(replacement)
        successful = [entry for entry in entries if entry.get("success")]
        manifest.update(
            {
                "projectName": self.project_name,
                "createdUtc": utc_now_iso(),
                "assetCount": len(entries),
                "successCount": len(successful),
                "failureCount": len(entries) - len(successful),
                "readerSuccessCount": len(successful),
                "readerFailureCount": len(entries) - len(successful),
                "assets": entries,
            }
        )
        _write_json_atomic(manifest_path, manifest)

    def _validate_next_database(self, database: Path, asset_path: str, expected_revision: str) -> dict[str, Any]:
        assert_quiescent_database(database)
        with open_database(database, readonly=True, migrate=False, immutable=True) as connection:
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise WorkflowError("snapshot-refresh-database-invalid", "The next SQLite generation failed integrity_check.")
            if get_schema_version(connection) != CURRENT_SCHEMA_VERSION:
                raise WorkflowError("snapshot-refresh-database-invalid", "The next SQLite generation has the wrong schema version.")
            assert_fts5_available(connection)
            if get_metadata(connection, "project_key", "") != self.project_name:
                raise WorkflowError("snapshot-refresh-database-invalid", "The next SQLite generation has the wrong project identity.")
            row = connection.execute(
                "SELECT revision_value, package_dirty, canonical_relpath FROM assets WHERE asset_path = ?",
                (asset_path,),
            ).fetchone()
            if row is None or str(row["revision_value"]) != expected_revision or bool(row["package_dirty"]):
                raise WorkflowError("snapshot-refresh-database-invalid", "The next SQLite generation does not contain the clean refreshed Revision.")
            asset_count = int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        return {"assetCount": asset_count, "targetRevision": expected_revision}

    def _build_snapshot_generation(self, asset_path: str, candidate_root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
        if self.active_snapshot is None:
            raise WorkflowError("snapshot-refresh-unavailable", "This workflow session was not started from a frozen active snapshot pair.")
        active = self.active_snapshot
        generation_id = new_generation_id()
        snapshots_root = self._safe_work_path("snapshots")
        snapshots_root.mkdir(parents=True, exist_ok=True)
        staging = snapshots_root / ("." + generation_id + ".staging")
        final_root = snapshots_root / generation_id
        if staging.exists() or final_root.exists():
            raise WorkflowError("snapshot-refresh-generation-exists", "The generated snapshot ID already exists.")
        required_bytes = self._tree_size(active.revision_export) + active.database.stat().st_size * 2 + 64 * 1024 * 1024
        free_bytes = shutil.disk_usage(snapshots_root).free
        if free_bytes < required_bytes:
            raise WorkflowError(
                "snapshot-refresh-disk-space",
                "There is not enough free disk space to build and validate the next snapshot generation.",
                details={"requiredBytes": required_bytes, "freeBytes": free_bytes},
            )
        staging.mkdir(parents=True, exist_ok=False)
        pointer_written = False
        try:
            next_export = staging / "revision-export"
            self._merge_refresh_export(active.revision_export, next_export, candidate_root, candidate)
            next_database = staging / "index.sqlite3"
            assert_quiescent_database(active.database)
            shutil.copy2(active.database, next_database)
            with open_database(next_database) as connection:
                build_result = build_index(
                    connection,
                    candidate_root,
                    next_database,
                    force=True,
                    project_key=self.project_name,
                )
                if build_result.failed or build_result.errors or build_result.updated + build_result.added != 1:
                    raise WorkflowError(
                        "snapshot-refresh-index-build-failed",
                        "The next SQLite generation did not update exactly one requested asset.",
                        details={"build": build_result.to_dict(include_assets=False)},
                    )
                set_metadata(connection, "last_export_root", str(final_root / "revision-export"))
            database_validation = self._validate_next_database(next_database, asset_path, str(candidate["revision"]))
            manifest_path = next_export / "manifest.json"
            manifest_sha = sha256_file(manifest_path)
            database_sha = sha256_file(next_database)
            os.replace(staging, final_root)
            write_active_pointer(
                active,
                generation_id=generation_id,
                database_sha256=database_sha,
                revision_export_manifest_sha256=manifest_sha,
                refreshed_asset_path=asset_path,
                refreshed_revision=str(candidate["revision"]),
            )
            pointer_written = True
            return {
                "generationId": generation_id,
                "databaseSha256": "sha256:" + database_sha,
                "revisionExportManifestSha256": "sha256:" + manifest_sha,
                "assetCount": database_validation["assetCount"],
                "targetRevision": candidate["revision"],
            }
        except SnapshotLifecycleError as exc:
            raise WorkflowError(exc.code, str(exc), details=exc.details) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if final_root.exists() and not pointer_written:
                shutil.rmtree(final_root, ignore_errors=True)

    def get_asset_state(self, asset_path: str) -> dict[str, Any]:
        """Combine Editor memory with the frozen SQLite, Revision Export, and current disk Package state."""
        with self._lock:
            try:
                asset_path = self._validate_refresh_asset_path(asset_path)
            except WorkflowError as exc:
                raise WorkflowError("asset-state-invalid-asset", "asset_path must be one exact /Game Object Path.") from exc

            record = self.index_service.get_revision_record(asset_path)
            freshness = self.freshness.inspect_asset(asset_path)
            index_revision = str(freshness.get("indexRevision", ""))
            export_revision = str(freshness.get("revisionExportRevision", ""))
            disk_revision = str(freshness.get("diskRevision", ""))
            reasons = [item for item in str(freshness.get("reason", "")).split(",") if item]

            sqlite_state = {
                "state": "available" if record is not None and index_revision else "missing",
                "revision": index_revision,
                "packageName": str(record.get("package_name", "")) if record else "",
                "assetClass": str(record.get("asset_class", "")) if record else "",
                "packageDirty": bool(record.get("package_dirty")) if record else None,
                "snapshotGenerationId": self.active_snapshot.generation_id if self.active_snapshot is not None else "",
            }
            revision_export_state = {
                "state": "available" if export_revision else (
                    "missing" if "revision-export-missing" in reasons else "unavailable"
                ),
                "revision": export_revision,
                "packageDirty": "revision-export-package-dirty" in reasons,
            }
            disk_state = {
                "state": "available" if disk_revision else (
                    "missing" if "package-file-missing" in reasons else "unavailable"
                ),
                "revision": disk_revision,
                "revisionAlgorithm": "sha256" if disk_revision else "",
            }

            memory_state: dict[str, Any] = {
                "configured": self.live_editor_service is not None,
                "state": "unavailable",
                "loaded": None,
                "packageDirty": None,
                "openInAssetEditor": None,
                "selected": None,
                "revisionAvailable": False,
                "reasonCode": "live-editor-disabled" if self.live_editor_service is None else "live-editor-unavailable",
            }
            if self.live_editor_service is not None:
                try:
                    live_status = self.live_editor_service.status()
                    if live_status.get("state") == "available":
                        payload = self.live_editor_service.call_tool(
                            "ue_inspect_asset_live",
                            {"assetPath": asset_path},
                        )
                        result = payload.get("result", {}) if isinstance(payload, dict) else {}
                        memory = result.get("memory", {}) if isinstance(result, dict) else {}
                        registry = result.get("assetRegistry", {}) if isinstance(result, dict) else {}
                        if isinstance(memory, dict):
                            memory_state.update(
                                {
                                    "state": str(memory.get("state", "unknown")),
                                    "loaded": bool(memory.get("loaded")),
                                    "packageDirty": bool(memory.get("packageDirty")),
                                    "openInAssetEditor": bool(memory.get("openInAssetEditor")),
                                    "selected": bool(memory.get("selected")),
                                    "loadedByBridge": bool(memory.get("loadedByBridge")),
                                    "registryFound": bool(registry.get("found")) if isinstance(registry, dict) else None,
                                    "reasonCode": "",
                                }
                            )
                    else:
                        memory_state["reasonCode"] = str(live_status.get("reasonCode", "live-editor-unavailable"))
                except Exception:
                    memory_state["reasonCode"] = "live-editor-status-unavailable"

            memory_dirty = memory_state.get("packageDirty") is True
            if memory_dirty:
                state = "memory-dirty"
                recommended_action = "save-or-revert-memory"
            elif freshness.get("state") == "fresh":
                state = "synchronized"
                recommended_action = "none"
            elif freshness.get("state") == "unavailable":
                state = "incomplete"
                recommended_action = "restore-missing-source"
            elif index_revision == export_revision and disk_revision and disk_revision != index_revision:
                state = "disk-newer-than-snapshots"
                recommended_action = "refresh-asset-index"
            elif disk_revision == export_revision and index_revision != disk_revision:
                state = "sqlite-outdated"
                recommended_action = "refresh-asset-index"
            elif disk_revision == index_revision and export_revision != disk_revision:
                state = "revision-export-outdated"
                recommended_action = "refresh-asset-index"
            else:
                state = "persistent-sources-diverged"
                recommended_action = "inspect-and-refresh"

            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_get_asset_state",
                "ok": True,
                "readOnly": True,
                "assetPath": asset_path,
                "state": state,
                "sources": {
                    "memory": memory_state,
                    "disk": disk_state,
                    "revisionExport": revision_export_state,
                    "sqlite": sqlite_state,
                },
                "comparisons": dict(freshness.get("comparisons", {})),
                "freshness": freshness,
                "saveRequired": memory_dirty,
                "indexRefreshRequired": freshness.get("state") == "stale",
                "refreshBlockedByDirtyMemory": memory_dirty,
                "currentSessionUsesFrozenSnapshot": self.active_snapshot is not None,
                "restartRequired": self._refresh_applied,
                "recommendedAction": recommended_action,
                "limitations": {
                    "memoryRevisionAvailable": False,
                    "memoryCleanMeans": "The loaded package is not Dirty; it is not a cryptographic equality proof against disk.",
                },
            }

    def refresh_asset_index(self, asset_path: str, *, mode: Literal["Preview", "Apply"] = "Preview") -> dict[str, Any]:
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            if mode not in {"Preview", "Apply"}:
                raise WorkflowError("snapshot-refresh-invalid-mode", "mode must be Preview or Apply.")
            if self.active_snapshot is None:
                raise WorkflowError("snapshot-refresh-unavailable", "Snapshot refresh is unavailable because this session has no frozen active snapshot pair.")
            self._assert_refresh_policy(asset_path)
            live_state = self._inspect_refresh_live_state(asset_path)
            operation_root = self._safe_work_path("refresh", uuid.uuid4().hex)
            candidate_root = operation_root / "candidate"
            try:
                candidate = self._export_refresh_candidate(asset_path, candidate_root)
                current_record = self.index_service.get_revision_record(asset_path)
                action = "add" if current_record is None else "update"
                base = {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_refresh_asset_index",
                    "ok": True,
                    "mode": mode,
                    "assetPath": asset_path,
                    "assetClass": candidate["assetClass"],
                    "action": action,
                    "currentSessionGenerationId": self.active_snapshot.generation_id,
                    "targetRevision": candidate["revision"],
                    "diskFileSize": candidate["diskFileSize"],
                    "liveState": live_state,
                    "currentSessionUsesFrozenSnapshot": True,
                }
                if mode == "Preview":
                    base.update(
                        {
                            "applied": False,
                            "activeSnapshotChanged": False,
                            "restartRequired": False,
                            "wouldInvalidate": {
                                "planCount": len(self._plans),
                                "dryRunReceiptCount": len(self._dry_runs),
                                "applyReceiptCount": len(self._applies),
                                "rollbackReceiptCount": len(self._rollback_dry_runs),
                            },
                            "nextStep": "Review the target Revision, then call ue_refresh_asset_index with mode=Apply. No active snapshot changed.",
                        }
                    )
                    return base
                invalidated = {
                    "planCount": len(self._plans),
                    "dryRunReceiptCount": len(self._dry_runs),
                    "applyReceiptCount": len(self._applies),
                    "rollbackReceiptCount": len(self._rollback_dry_runs),
                }
                generation = self._build_snapshot_generation(asset_path, candidate_root, candidate)
                self._plans.clear()
                self._dry_runs.clear()
                self._applies.clear()
                self._rollback_dry_runs.clear()
                self._refresh_applied = True
                base.update(
                    {
                        "applied": True,
                        "activeSnapshotChanged": True,
                        "newGeneration": generation,
                        "invalidated": invalidated,
                        "currentSessionUsesPreviousSnapshot": True,
                        "restartRequired": True,
                        "nextStep": "Restart the MCP server. The new session will freeze and validate the new paired SQLite and Revision Export generation.",
                    }
                )
                return base
            finally:
                shutil.rmtree(operation_root, ignore_errors=True)

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
            reference_impact: dict[str, Any] | None = None
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
            if operation in {"removeDataTableRow", "renameDataTableRow"}:
                row_name = target_value.get("rowName") if isinstance(target_value, dict) else None
                if not isinstance(row_name, str) or not row_name:
                    raise WorkflowError(
                        "data-table-row-name-invalid",
                        "The structural DataTable operation has no valid source row name.",
                    )
                reference_impact = self.index_service.get_data_table_row_reference_impact(
                    asset_path,
                    row_name,
                )
                if int(reference_impact.get("referenceCount", 0)) > 0:
                    shutil.rmtree(directory, ignore_errors=True)
                    raise WorkflowError(
                        "data-table-row-referenced",
                        "The DataTable row has indexed Searchable Name referencers and cannot be removed or renamed by a single-asset patch.",
                        details=reference_impact,
                    )
            record = PlanRecord(plan_id, digest, patch, patch_path, validation)
            self._plans[plan_id] = record
            self._prune_records()
            response = {
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
            if reference_impact is not None:
                response["referenceImpact"] = reference_impact
            return response

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
            self._assert_session_current()
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
            verification_report_id = _report_id("verify-export", output / "manifest.json")
            memory_task_evidence = _verified_memory_task_evidence(
                apply,
                validation_report_id=verification_report_id,
                actual_revision=actual_revision,
            )
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
                "reportId": verification_report_id,
                "memoryTaskEvidence": memory_task_evidence,
                "indexFreshness": freshness,
                "nextStep": (
                    "If Project Memory is enabled, pass memoryTaskEvidence.arguments unchanged to "
                    "ue_memory_record_task. Otherwise keep the change and refresh the asset index, "
                    "or call ue_rollback_patch in DryRun mode before an explicit rollback Commit."
                ),
            }

    def save_authorized_asset(
        self,
        asset_path: str,
        *,
        mode: Literal["Preview", "Commit"] = "Preview",
        save_receipt: str = "",
        confirmation: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            if mode not in {"Preview", "Commit"}:
                raise WorkflowError("authorized-save-invalid-mode", "mode must be Preview or Commit.")
            if self.live_editor_service is None:
                raise WorkflowError("live-editor-required", "Authorized save requires Live Editor mode for the fixed project.")

            record = self.index_service.get_revision_record(asset_path)
            if record is None:
                raise WorkflowError("asset-not-indexed", "The requested asset is not present in the fixed SQLite index.")
            asset_class = str(record.get("asset_class", ""))
            package_name = str(record.get("package_name", ""))
            if not asset_class or not package_name:
                raise WorkflowError("asset-identity-unavailable", "The indexed asset has no stable Class and Package identity.")
            if asset_class == "/Script/Engine.World":
                raise WorkflowError("authorized-save-map-unsupported", "Authorized save does not save maps or external-actor packages.")
            policy = self._assert_refresh_policy(asset_path, asset_class)
            if policy.get("commitEnabled") is not True:
                raise WorkflowError("commit-not-allowed", "The fixed Policy does not enable Commit.")
            freshness = self._assert_asset_fresh(asset_path)
            expected_revision = str(freshness.get("diskRevision", ""))
            if not expected_revision.startswith("sha256:"):
                raise WorkflowError("revision-unavailable", "The current disk Package has no usable SHA-256 Revision.")

            try:
                status = self.live_editor_service.status()
            except Exception as exc:
                raise WorkflowError("live-editor-status-unavailable", "The fixed Editor session could not be inspected before save.") from exc
            if status.get("state") != "available" or status.get("pieState") != "stopped":
                raise WorkflowError("live-editor-unavailable", "The fixed Editor must be available and stopped before authorized save.")
            editor_session_id = str(status.get("sessionId", ""))
            editor_process_id = int(status.get("processId") or 0)
            if not editor_session_id or editor_process_id <= 0:
                raise WorkflowError("live-editor-status-unavailable", "The fixed Editor session identity is incomplete.")
            try:
                inspection = self.live_editor_service.call_tool("ue_inspect_asset_live", {"assetPath": asset_path})
            except Exception as exc:
                raise WorkflowError("live-editor-status-unavailable", "The target asset could not be inspected before save.") from exc
            result = inspection.get("result", {}) if isinstance(inspection, dict) else {}
            memory = result.get("memory", {}) if isinstance(result, dict) else {}
            registry = result.get("assetRegistry", {}) if isinstance(result, dict) else {}
            if not isinstance(memory, dict) or not isinstance(registry, dict):
                raise WorkflowError("live-editor-protocol-error", "The target asset inspection result is incomplete.")
            if registry.get("classPath") not in {None, "", asset_class}:
                raise WorkflowError("asset-class-mismatch", "The live Asset Registry Class does not match the fixed snapshot.")
            if memory.get("loaded") is not True:
                raise WorkflowError("live-editor-save-asset-not-loaded", "Authorized save only accepts an already loaded exact asset.")
            if memory.get("packageDirty") is not True:
                raise WorkflowError("live-editor-save-not-dirty", "The exact loaded package is not Dirty.")

            if mode == "Preview":
                receipt = "save_" + secrets.token_urlsafe(24)
                self._save_authorizations[receipt] = SaveAuthorizationRecord(
                    receipt,
                    asset_path,
                    asset_class,
                    package_name,
                    expected_revision,
                    editor_session_id,
                    editor_process_id,
                )
                self._prune_records()
                return {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_save_authorized_asset",
                    "ok": True,
                    "mode": "Preview",
                    "assetPath": asset_path,
                    "assetClass": asset_class,
                    "expectedDiskRevision": expected_revision,
                    "editorSessionId": editor_session_id,
                    "editorProcessId": editor_process_id,
                    "loaded": True,
                    "packageDirty": True,
                    "saveReceipt": receipt,
                    "saved": False,
                    "commitToolsEnabled": self.config.commit_enabled,
                    "nextStep": f"To save exactly this asset, call ue_save_authorized_asset with mode=Commit and confirmation 'SAVE {receipt}'.",
                }

            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Commit tools were not enabled when this MCP server started.")
            authorization = self._save_authorizations.get(save_receipt)
            if authorization is None or authorization.consumed:
                raise WorkflowError("save-receipt-invalid", "A fresh one-time saveReceipt is required.")
            if authorization.asset_path != asset_path:
                raise WorkflowError("save-receipt-invalid", "The saveReceipt belongs to another asset.")
            if confirmation != f"SAVE {save_receipt}":
                raise WorkflowError("save-confirmation-required", "Save confirmation did not exactly match the required receipt phrase.")
            if (
                authorization.asset_class != asset_class
                or authorization.package_name != package_name
                or authorization.expected_disk_revision != expected_revision
                or authorization.editor_session_id != editor_session_id
                or authorization.editor_process_id != editor_process_id
            ):
                raise WorkflowError("save-receipt-stale", "The asset, disk Revision, or Editor session changed after Preview.")

            package_file = self._package_file(self.config.project_path, package_name, asset_class)
            before_revision = "sha256:" + sha256_file(package_file)
            if before_revision != expected_revision:
                raise WorkflowError("revision-conflict", "The disk Package changed after save Preview.")
            backup_directory = self.config.backup_root / "live-save" / save_receipt
            if backup_directory.exists():
                raise WorkflowError("backup-exists", "The fixed authorized-save backup directory already exists.")
            backup_directory.mkdir(parents=True, exist_ok=False)
            backup_file = backup_directory / package_file.name
            shutil.copy2(package_file, backup_file)
            manifest_path = backup_directory / "manifest.json"
            _write_json_atomic(
                manifest_path,
                {
                    "schemaVersion": "1.0",
                    "operation": "authorized-live-save",
                    "projectName": self.project_name,
                    "assetPath": asset_path,
                    "assetClass": asset_class,
                    "packageName": package_name,
                    "beforeRevision": before_revision,
                    "backupFileName": backup_file.name,
                    "createdUtc": utc_now_iso(),
                },
            )

            try:
                bridge_result = self.live_editor_service.call_method(
                    "editor.saveAuthorizedAsset",
                    {"assetPath": asset_path},
                    timeout_seconds=30.0,
                )
            except Exception as exc:
                raise WorkflowError(
                    "authorized-save-failed",
                    "The fixed Editor rejected or failed the exact authorized save.",
                    details={"backupManifestId": manifest_path.name},
                ) from exc
            if bridge_result.get("saved") is not True or bridge_result.get("assetPath") != asset_path:
                raise WorkflowError("authorized-save-report-invalid", "The Editor did not confirm the exact saved asset.")

            after_revision = "sha256:" + sha256_file(package_file)
            verification_root = self._safe_work_path("authorized-save", save_receipt, "verify")
            candidate = self._export_refresh_candidate(asset_path, verification_root)
            if candidate.get("revision") != after_revision:
                raise WorkflowError(
                    "authorized-save-verification-failed",
                    "Independent Unreal export did not match the saved disk Revision.",
                    details={"beforeRevision": before_revision, "afterRevision": after_revision},
                )
            authorization.consumed = True
            freshness_after = (
                self.freshness.mark_commit(asset_path, before_revision, after_revision)
                if after_revision != before_revision
                else self.freshness.inspect_asset(asset_path)
            )
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_save_authorized_asset",
                "ok": True,
                "mode": "Commit",
                "assetPath": asset_path,
                "assetClass": asset_class,
                "saveReceipt": save_receipt,
                "saved": True,
                "verified": True,
                "beforeRevision": before_revision,
                "afterRevision": after_revision,
                "revisionChanged": before_revision != after_revision,
                "backupManifestId": manifest_path.name,
                "editorSessionId": editor_session_id,
                "editorProcessId": editor_process_id,
                "bridge": _safe_report(bridge_result, configured_paths=self.configured_paths),
                "indexFreshness": freshness_after,
                "nextStep": "Call ue_refresh_asset_index to preview and activate a paired snapshot generation when the Revision changed.",
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

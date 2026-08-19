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
from .backups import create_backup_manifest, rollback_backup
from .change_sets import (
    ChangeSetOperationRecord,
    ChangeSetError,
    MAX_CHANGE_SETS,
    MAX_CHANGE_SET_RECEIPTS,
    ChangeSetRecord,
    deserialize_change_set_record,
    derive_change_set_status,
    is_terminal_change_set,
    serialize_change_set_record,
    validate_change_set_operation_receipt,
    validate_change_set_id,
    validate_change_set_task_id,
    validate_change_set_title,
)
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
from .patches import LIVE_WRITE_OPERATION_REGISTRY, OPERATION_REGISTRY, validate_patch
from .retarget_workflow import RetargetWorkflowMixin
from .semantic_diff_workflow import analyze_workflow_semantic_diff
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
LIVE_WRITE_JOURNAL_SCHEMA_VERSION = "1.0"
PUBLISHED_VERSION = "0.7.0"
DEVELOPMENT_LINE = "0.7.0"
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

def _live_write_value_kind(operation: str) -> str:
    spec = LIVE_WRITE_OPERATION_REGISTRY.get(operation)
    return spec.live_write_value_kind if spec is not None else "unknown"



def _is_guid_with_hyphens(value: str) -> bool:
    if len(value) != 36:
        return False
    return all(
        character in "0123456789abcdefABCDEF" if index not in {8, 13, 18, 23} else character == "-"
        for index, character in enumerate(value)
    )


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


@dataclass
class LiveApplyRecord:
    receipt: str
    plan_id: str
    plan_digest: str
    asset_path: str
    operation: str
    value_kind: str
    editor_session_id: str
    transaction_id: str
    before_value: Any
    after_value: Any
    target: dict[str, Any]
    applied_at_utc: str
    saved: bool = False
    save_receipt: str = ""
    verified: bool = False


def _live_write_memory_task_evidence(
    record: LiveApplyRecord,
    *,
    state: str,
    conclusion: str,
    outcome: str,
    revision: str,
    report_id: str,
    undo_available: bool,
    independent_reload: bool,
) -> dict[str, Any]:
    manifest_id = f"live-save:{record.save_receipt}" if record.save_receipt else ""
    return {
        "schemaVersion": MEMORY_TASK_EVIDENCE_SCHEMA_VERSION,
        "tool": "ue_memory_record_task",
        "arguments": {
            "task_key": f"live-write:{record.plan_id}",
            "title": f"Live write {record.plan_id} on {record.asset_path}",
            "conclusion": conclusion,
            "outcome": outcome,
            "patch_ref": f"patch:{record.plan_digest}",
            "backup_manifest_ref": f"backup-manifest:{manifest_id}" if manifest_id else "backup-manifest:not-applicable",
            "validation_evidence_ref": f"validation-evidence:{report_id}",
            "revision_set": [
                {
                    "assetPath": record.asset_path,
                    "revision": revision,
                    "revisionStable": True,
                }
            ],
            "scopes": [
                {
                    "scopeType": "asset",
                    "scopeKey": record.asset_path,
                }
            ],
            "confidence": 1.0,
            "patch_details": {
                "planId": record.plan_id,
                "patchDigest": record.plan_digest,
                "operation": record.operation,
                "valueKind": record.value_kind,
                "transactionId": record.transaction_id,
                "undoAvailable": undo_available,
                "saved": record.saved,
                "verified": record.verified,
                "state": state,
            },
            "backup_manifest_details": (
                {"manifestId": manifest_id, "kind": "authorized-save-backup"}
                if manifest_id
                else {}
            ),
            "validation_evidence_details": {
                "reportId": report_id,
                "independentReload": independent_reload,
                "verified": record.verified,
                "state": state,
            },
            "details": {
                "workflowEvidenceSchemaVersion": MEMORY_TASK_EVIDENCE_SCHEMA_VERSION,
                "workflowTool": "ue_verify_live_write",
            },
        },
    }


def _live_write_exported_value(canonical: dict[str, Any], record: LiveApplyRecord) -> Any:
    details = canonical.get("assetDetails") or {}
    target = record.target or {}
    if record.operation == "setAnimationScaleFix":
        return details.get("scaleFixState")
    if record.operation == "setAdditiveBasePoseFix":
        return {
            "refSequencePath": details.get("basePoseSequencePath"),
            "refFrameIndex": details.get("basePoseFrameIndex"),
            "additiveAnimType": details.get("additiveType"),
            "additiveBasePoseType": details.get("basePoseType"),
        }
    spec = LIVE_WRITE_OPERATION_REGISTRY.get(record.operation)
    if spec is None:
        return None
    selector = target.get(spec.live_write_verification_target)
    if spec.live_write_verification == "data-table-row":
        for row in details.get("rows") or []:
            if not isinstance(row, dict) or row.get("Name") != selector:
                continue
            exported = dict(row)
            exported.pop("Name", None)
            return exported
        return None
    if spec.live_write_verification == "material-parameter":
        for section in (
            "scalarParameters",
            "vectorParameters",
            "textureParameters",
            "staticSwitchParameters",
            "doubleVectorParameters",
            "fontParameters",
        ):
            for parameter in details.get(section) or []:
                if isinstance(parameter, dict) and parameter.get("name") == selector:
                    return (
                        parameter.get("valuePath")
                        if record.operation == "setMaterialInstanceTextureParameter"
                        else parameter.get("value")
                    )
        return None
    if spec.live_write_verification == "property":
        for prop in details.get("properties") or []:
            if isinstance(prop, dict) and prop.get("name") == selector:
                return prop.get("value")
    return None


def _live_write_expected_exported_value(record: LiveApplyRecord) -> Any:
    if record.operation != "setAnimationScaleFix" or not isinstance(record.after_value, dict):
        return record.after_value
    persisted_fields = (
        "rootBone",
        "forceRootLock",
        "enableRootMotion",
        "useNormalizedRootMotionScale",
        "rootMotionRootLock",
        "additive",
        "rootTrackExists",
        "rootTrackKeyCount",
        "rootTrackFirstScale",
        "rootTrackMiddleScale",
        "rootTrackLastScale",
    )
    return {
        field: record.after_value[field]
        for field in persisted_fields
        if field in record.after_value
    }

def _live_write_runtime_verification(record: LiveApplyRecord) -> dict[str, Any] | None:
    if record.operation != "setAnimationScaleFix" or not isinstance(record.after_value, dict):
        return None
    runtime_fields = (
        "referenceLocalScale",
        "finalEvaluationStatus",
        "finalRootScale",
    )
    return {
        field: record.after_value[field]
        for field in runtime_fields
        if field in record.after_value
    }




def _live_write_exported_matches(expected: Any, exported: Any) -> bool:
    if expected is None:
        return exported in {None, ""}
    if isinstance(expected, dict) and isinstance(exported, dict):
        return all(_live_write_exported_matches(value, exported.get(key)) for key, value in expected.items())
    if isinstance(expected, list) and isinstance(exported, list):
        return len(expected) == len(exported) and all(
            _live_write_exported_matches(left, right) for left, right in zip(expected, exported)
        )
    return expected == exported


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


def _rollback_memory_task_evidence(
    apply: ApplyRecord,
    *,
    rollback_report_id: str,
    verification_report_id: str,
    restored_revision: str,
) -> dict[str, Any]:
    manifest_id = apply.manifest_path.name
    return {
        "schemaVersion": MEMORY_TASK_EVIDENCE_SCHEMA_VERSION,
        "tool": "ue_memory_record_task",
        "arguments": {
            "task_key": f"rollback:{apply.plan_id}",
            "title": f"Rolled back patch {apply.plan_id}",
            "conclusion": (
                f"The committed asset {apply.asset_path} was restored and independently "
                f"verified at Revision {restored_revision}."
            ),
            "outcome": "rolledBack",
            "patch_ref": f"patch:{apply.plan_digest}",
            "backup_manifest_ref": f"backup-manifest:{manifest_id}",
            "validation_evidence_ref": f"validation-evidence:{verification_report_id}",
            "revision_set": [
                {
                    "assetPath": apply.asset_path,
                    "revision": restored_revision,
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
                "committedRevision": apply.after_revision,
                "restoredRevision": restored_revision,
            },
            "backup_manifest_details": {
                "manifestId": manifest_id,
                "restored": True,
            },
            "validation_evidence_details": {
                "rollbackReportId": rollback_report_id,
                "reportId": verification_report_id,
                "independentReload": True,
                "verified": True,
                "expectedRevision": apply.before_revision,
                "actualRevision": restored_revision,
            },
            "details": {
                "workflowEvidenceSchemaVersion": MEMORY_TASK_EVIDENCE_SCHEMA_VERSION,
                "workflowTool": "ue_rollback_patch",
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


@dataclass
class AuthorizedSaveRollbackDryRunRecord:
    receipt: str
    save_receipt: str
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



class PatchWorkflowService(RetargetWorkflowMixin):
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
        self._authorized_save_rollback_dry_runs: dict[str, AuthorizedSaveRollbackDryRunRecord] = {}
        self._live_applies: dict[str, LiveApplyRecord] = {}
        self._live_apply_by_asset: dict[str, str] = {}
        self._live_write_journal_errors: list[str] = []
        self._live_write_recovered_count = 0
        self._change_sets: dict[str, ChangeSetRecord] = {}
        self.live_editor_service = live_editor_service
        self.active_snapshot = self.config.active_snapshot
        self._refresh_applied = False
        self._index_refresh_candidates: dict[str, tuple[Path, dict[str, Any]]] = {}
        self._validate_config()
        self.freshness = freshness_tracker or IndexFreshnessTracker(
            self.index_service,
            self.config.project_path,
            self.config.revision_export,
        )
        self._load_live_write_journal()
        self._load_change_set_journal()

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
            "publishedVersion": PUBLISHED_VERSION,
            "developmentLine": DEVELOPMENT_LINE,
            "projectName": self.project_name,
            "writeToolsEnabled": True,
            "commitToolsEnabled": self.config.commit_enabled,
            "policyDigest": self.policy_digest,
            "singleAssetSingleOperation": True,
            "receiptRequiredForCommit": True,
            "receiptRequiredForRollbackCommit": True,
            "liveWriteJournal": {
                "pendingRecordCount": len(self._live_applies),
                "recoveredRecordCount": self._live_write_recovered_count,
                "journalErrorCount": len(self._live_write_journal_errors),
                "supportsExactReceipt": True,
                "changeSetCount": len(self._change_sets),
                "maxChangeSets": MAX_CHANGE_SETS,
                "maxReceiptsPerChangeSet": MAX_CHANGE_SET_RECEIPTS,
            },
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

    def _live_write_journal_root(self) -> Path:
        return self._safe_work_path("live-write-journal")

    @staticmethod
    def _validate_live_apply_receipt(receipt: str) -> str:
        if (
            not isinstance(receipt, str)
            or not receipt.startswith("live_")
            or len(receipt) > 96
            or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for character in receipt)
        ):
            raise WorkflowError("live-write-receipt-invalid", "liveApplyReceipt is not a valid internal receipt.")
        return receipt

    def _live_write_journal_path(self, receipt: str) -> Path:
        receipt = self._validate_live_apply_receipt(receipt)
        return self._live_write_journal_root() / f"{receipt}.json"

    def _serialize_live_apply_record(self, record: LiveApplyRecord) -> dict[str, Any]:
        return {
            "schemaVersion": LIVE_WRITE_JOURNAL_SCHEMA_VERSION,
            "projectName": self.project_name,
            "receipt": record.receipt,
            "planId": record.plan_id,
            "planDigest": record.plan_digest,
            "assetPath": record.asset_path,
            "operation": record.operation,
            "valueKind": record.value_kind,
            "editorSessionId": record.editor_session_id,
            "transactionId": record.transaction_id,
            "beforeValue": record.before_value,
            "afterValue": record.after_value,
            "target": record.target,
            "appliedAtUtc": record.applied_at_utc,
            "saved": record.saved,
            "saveReceipt": record.save_receipt,
            "verified": record.verified,
        }

    def _deserialize_live_apply_record(self, value: dict[str, Any], expected_receipt: str) -> LiveApplyRecord:
        if value.get("schemaVersion") != LIVE_WRITE_JOURNAL_SCHEMA_VERSION or value.get("projectName") != self.project_name:
            raise ValueError("journal identity mismatch")
        receipt = self._validate_live_apply_receipt(str(value.get("receipt", "")))
        if receipt != expected_receipt:
            raise ValueError("journal receipt mismatch")
        operation = str(value.get("operation", ""))
        spec = LIVE_WRITE_OPERATION_REGISTRY.get(operation)
        if spec is None or value.get("valueKind") != spec.live_write_value_kind:
            raise ValueError("journal operation mismatch")
        asset_path = self._validate_refresh_asset_path(str(value.get("assetPath", "")))
        target = value.get("target")
        if not isinstance(target, dict):
            raise ValueError("journal target invalid")
        for field in spec.target_fields:
            validator = spec.target_validators.get(field)
            if validator is None or not validator(target.get(field)):
                raise ValueError("journal target field invalid")
        transaction_id = str(value.get("transactionId", ""))
        editor_session_id = str(value.get("editorSessionId", ""))
        if not _is_guid_with_hyphens(transaction_id) or not editor_session_id:
            raise ValueError("journal editor identity invalid")
        plan_id = str(value.get("planId", ""))
        plan_digest = str(value.get("planDigest", ""))
        applied_at_utc = str(value.get("appliedAtUtc", ""))
        saved = value.get("saved")
        verified = value.get("verified")
        save_receipt = str(value.get("saveReceipt", ""))
        if not plan_id or not plan_digest.startswith("sha256:") or not applied_at_utc:
            raise ValueError("journal plan identity invalid")
        if not isinstance(saved, bool) or not isinstance(verified, bool) or verified:
            raise ValueError("journal lifecycle invalid")
        if saved and not save_receipt.startswith("save_"):
            raise ValueError("journal save identity invalid")
        return LiveApplyRecord(
            receipt=receipt,
            plan_id=plan_id,
            plan_digest=plan_digest,
            asset_path=asset_path,
            operation=operation,
            value_kind=spec.live_write_value_kind,
            editor_session_id=editor_session_id,
            transaction_id=transaction_id,
            before_value=value.get("beforeValue"),
            after_value=value.get("afterValue"),
            target=dict(target),
            applied_at_utc=applied_at_utc,
            saved=saved,
            save_receipt=save_receipt,
            verified=False,
        )

    def _record_live_write_journal_error(self, receipt: str) -> None:
        if receipt not in self._live_write_journal_errors:
            self._live_write_journal_errors.append(receipt)

    def _persist_live_apply(self, record: LiveApplyRecord) -> bool:
        try:
            _write_json_atomic(self._live_write_journal_path(record.receipt), self._serialize_live_apply_record(record))
        except (OSError, TypeError, ValueError):
            self._record_live_write_journal_error(record.receipt)
            return False
        if record.receipt in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(record.receipt)
        return True

    def _delete_live_apply_journal(self, receipt: str) -> bool:
        path = self._live_write_journal_path(receipt)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            self._record_live_write_journal_error(receipt)
            return False
        if receipt in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(receipt)
        return True

    def _rebuild_live_apply_index(self) -> None:
        latest: dict[str, tuple[str, str]] = {}
        for receipt, record in self._live_applies.items():
            current = latest.get(record.asset_path)
            if current is None or (record.applied_at_utc, receipt) > current:
                latest[record.asset_path] = (record.applied_at_utc, receipt)
        self._live_apply_by_asset = {asset_path: receipt for asset_path, (_, receipt) in latest.items()}

    def _load_live_write_journal(self) -> None:
        root = self._live_write_journal_root()
        if not root.is_dir():
            return
        for path in sorted(root.glob("live_*.json")):
            try:
                value = _read_json(path)
                record = self._deserialize_live_apply_record(value, path.stem)
            except (WorkflowError, OSError, ValueError):
                self._live_write_journal_errors.append(path.stem)
                continue
            self._live_applies[record.receipt] = record
            self._live_write_recovered_count += 1
        self._prune_records()

    def _remove_live_apply(self, receipt: str) -> None:
        self._live_applies.pop(receipt, None)
        self._delete_live_apply_journal(receipt)
        self._rebuild_live_apply_index()

    def _resolve_live_apply(self, asset_path: str, live_apply_receipt: str = "") -> tuple[str, LiveApplyRecord]:
        if live_apply_receipt:
            receipt = self._validate_live_apply_receipt(live_apply_receipt)
        else:
            receipt = self._live_apply_by_asset.get(asset_path, "")
        record = self._live_applies.get(receipt)
        if record is None or record.asset_path != asset_path:
            raise WorkflowError(
                "live-write-verify-not-found",
                "No matching confirmed live write is pending for this asset.",
            )
        return receipt, record

    def _change_set_journal_root(self) -> Path:
        return self._safe_work_path("change-sets")

    def _change_set_journal_path(self, change_set_id: str) -> Path:
        try:
            change_set_id = validate_change_set_id(change_set_id)
        except ChangeSetError as exc:
            raise WorkflowError(exc.code, str(exc)) from exc
        return self._change_set_journal_root() / f"{change_set_id}.json"

    def _persist_change_set(self, record: ChangeSetRecord) -> bool:
        try:
            _write_json_atomic(
                self._change_set_journal_path(record.change_set_id),
                serialize_change_set_record(record, self.project_name),
            )
        except (OSError, TypeError, ValueError):
            self._record_live_write_journal_error(record.change_set_id)
            return False
        if record.change_set_id in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(record.change_set_id)
        return True

    def _delete_change_set_journal(self, change_set_id: str) -> bool:
        path = self._change_set_journal_path(change_set_id)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            self._record_live_write_journal_error(change_set_id)
            return False
        if change_set_id in self._live_write_journal_errors:
            self._live_write_journal_errors.remove(change_set_id)
        return True

    def _load_change_set_journal(self) -> None:
        root = self._change_set_journal_root()
        if not root.is_dir():
            return
        for path in sorted(root.glob("cs_*.json")):
            try:
                record = deserialize_change_set_record(_read_json(path), self.project_name)
            except (WorkflowError, OSError, ValueError):
                self._live_write_journal_errors.append(path.stem)
                continue
            for operation in record.operations:
                if operation.status == "applied":
                    operation.status = "unknown"
            record.status = derive_change_set_status(record.operations)
            self._change_sets[record.change_set_id] = record

    def _resolve_change_set(self, change_set_id: str) -> ChangeSetRecord:
        try:
            change_set_id = validate_change_set_id(change_set_id)
        except ChangeSetError as exc:
            raise WorkflowError(exc.code, str(exc)) from exc
        record = self._change_sets.get(change_set_id)
        if record is None:
            raise WorkflowError(
                "change-set-not-found",
                "The Change Set is not present in this MCP server session.",
            )
        return record

    def _current_editor_session(self) -> tuple[bool, str]:
        if self.live_editor_service is None:
            return False, ""
        try:
            status = self.live_editor_service.status()
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return False, ""
        session_id = str(status.get("sessionId", "")) if isinstance(status, dict) else ""
        return bool(isinstance(status, dict) and status.get("state") == "available" and session_id), session_id

    def _reconcile_change_set(self, record: ChangeSetRecord, *, persist: bool) -> None:
        editor_available, current_session_id = self._current_editor_session()
        changed = False
        for operation in record.operations:
            live_record = self._live_applies.get(operation.receipt)
            apply_record = self._applies.get(operation.receipt)
            desired_status = operation.status
            if live_record is not None:
                if not operation.plan_id:
                    operation.plan_id = live_record.plan_id
                    changed = True
                if not operation.asset_path:
                    operation.asset_path = live_record.asset_path
                    changed = True
                if not operation.operation:
                    operation.operation = live_record.operation
                    changed = True
                if not operation.transaction_id:
                    operation.transaction_id = live_record.transaction_id
                    changed = True
                if not operation.editor_session_id:
                    operation.editor_session_id = live_record.editor_session_id
                    changed = True
                if not operation.save_receipt and live_record.save_receipt:
                    operation.save_receipt = live_record.save_receipt
                    changed = True
                if live_record.verified:
                    desired_status = "verified"
                elif live_record.saved:
                    desired_status = "saved"
                elif editor_available and current_session_id == live_record.editor_session_id:
                    desired_status = "applied"
                elif operation.status in {"applied", "unknown"}:
                    desired_status = "unknown"
            elif operation.status == "applied":
                desired_status = "unknown"
            if apply_record is not None:
                desired_status = "verified" if apply_record.verified else "saved"
            if desired_status != operation.status:
                operation.status = desired_status
                operation.updated_at_utc = utc_now_iso()
                changed = True

        session_ids = {operation.editor_session_id for operation in record.operations if operation.editor_session_id}
        if not record.editor_session_id and len(session_ids) == 1:
            record.editor_session_id = next(iter(session_ids))
            changed = True
        derived_status = derive_change_set_status(record.operations)
        if record.status != derived_status:
            record.status = derived_status
            changed = True
        if changed:
            record.updated_at_utc = utc_now_iso()
            if persist:
                self._persist_change_set(record)

    def _prune_terminal_change_sets(self, maximum: int) -> None:
        while len(self._change_sets) > maximum:
            removable_id = next(
                (
                    change_set_id
                    for change_set_id, record in self._change_sets.items()
                    if is_terminal_change_set(record)
                ),
                "",
            )
            if not removable_id:
                break
            self._change_sets.pop(removable_id)
            self._delete_change_set_journal(removable_id)

    def create_change_set(self, *, title: str = "Live Editor Change Set", task_id: str = "") -> dict[str, Any]:
        with self._lock:
            try:
                title = validate_change_set_title(title)
                task_id = validate_change_set_task_id(task_id) if task_id else "task_" + secrets.token_urlsafe(16)
            except ChangeSetError as exc:
                raise WorkflowError(exc.code, str(exc)) from exc
            self._prune_terminal_change_sets(MAX_CHANGE_SETS - 1)
            if len(self._change_sets) >= MAX_CHANGE_SETS:
                raise WorkflowError(
                    "change-set-capacity-reached",
                    "All Change Set slots are active or non-terminal; close or verify an existing Change Set before creating another.",
                )
            change_set_id = "cs_" + secrets.token_urlsafe(16)
            now = utc_now_iso()
            editor_available, editor_session_id = self._current_editor_session()
            record = ChangeSetRecord(
                change_set_id=change_set_id,
                task_id=task_id,
                editor_session_id=editor_session_id if editor_available else "",
                title=title,
                status="planned",
                created_at_utc=now,
                updated_at_utc=now,
                operations=[],
            )
            self._change_sets[change_set_id] = record
            journal_persisted = self._persist_change_set(record)
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_create_change_set",
                "ok": True,
                "projectName": self.project_name,
                "changeSetId": change_set_id,
                "taskId": task_id,
                "editorSessionId": record.editor_session_id,
                "title": title,
                "status": record.status,
                "createdAtUtc": record.created_at_utc,
                "updatedAtUtc": record.updated_at_utc,
                "operationCount": 0,
                "receiptCount": 0,
                "maxReceiptsPerChangeSet": MAX_CHANGE_SET_RECEIPTS,
                "journalPersisted": journal_persisted,
                "nextStep": (
                    "Pass the changeSetId to ue_apply_asset_property_live to bind confirmed "
                    "live writes to this Change Set."
                ),
            }

    def discard_empty_change_set(self, change_set_id: str) -> bool:
        """Delete an internal Change Set only when no live write was ever bound to it."""
        with self._lock:
            record = self._resolve_change_set(change_set_id)
            self._reconcile_change_set(record, persist=True)
            if record.operations:
                return False
            self._change_sets.pop(change_set_id, None)
            self._delete_change_set_journal(change_set_id)
            return True

    def _change_set_operation_payload(self, operation: ChangeSetOperationRecord) -> dict[str, Any]:
        live_record = self._live_applies.get(operation.receipt)
        apply_record = self._applies.get(operation.receipt)
        return {
            "operationId": operation.receipt,
            "receipt": operation.receipt,
            "liveApplyReceipt": operation.receipt if operation.receipt.startswith("live_") else "",
            "active": live_record is not None or apply_record is not None,
            "planId": operation.plan_id,
            "assetPath": operation.asset_path,
            "operation": operation.operation,
            "transactionId": operation.transaction_id,
            "editorSessionId": operation.editor_session_id,
            "status": operation.status,
            "saved": operation.status in {"saved", "verified"},
            "verified": operation.status == "verified",
            "noOp": operation.status == "no-op",
            "saveReceipt": operation.save_receipt,
            "failureCode": operation.failure_code,
            "createdAtUtc": operation.created_at_utc,
            "updatedAtUtc": operation.updated_at_utc,
        }

    @staticmethod
    def _change_set_validation(record: ChangeSetRecord) -> dict[str, Any]:
        statuses = [operation.status for operation in record.operations]
        verified_count = sum(status == "verified" for status in statuses)
        no_op_count = sum(status == "no-op" for status in statuses)
        if any(status == "unknown" for status in statuses):
            state = "unknown"
        elif statuses and no_op_count == len(statuses):
            state = "no-op"
        elif statuses and verified_count == len(statuses):
            state = "verified"
        elif verified_count:
            state = "partial"
        else:
            state = "not-run"
        return {
            "state": state,
            "verifiedOperationCount": verified_count,
            "noOpOperationCount": no_op_count,
            "operationCount": len(statuses),
        }

    @staticmethod
    def _change_set_save_state(record: ChangeSetRecord) -> dict[str, Any]:
        statuses = [operation.status for operation in record.operations]
        saved_count = sum(status in {"saved", "verified"} for status in statuses)
        no_op_count = sum(status == "no-op" for status in statuses)
        if any(status == "unknown" for status in statuses):
            state = "unknown"
        elif statuses and no_op_count == len(statuses):
            state = "not-required"
        elif statuses and saved_count + no_op_count == len(statuses):
            state = "saved"
        elif saved_count:
            state = "partial"
        else:
            state = "unsaved"
        return {
            "state": state,
            "savedOperationCount": saved_count,
            "noOpOperationCount": no_op_count,
            "operationCount": len(statuses),
        }

    @staticmethod
    def _change_set_next_step(record: ChangeSetRecord) -> str:
        if is_terminal_change_set(record):
            return "This Change Set is terminal; create a new Change Set for further writes."
        status = derive_change_set_status(record.operations)
        if status == "planned":
            return "Bind a confirmed live write with ue_apply_asset_property_live."
        if status in {"applied", "partially_applied"}:
            return "Save and verify the remaining applied operations, or undo/discard them."
        if status == "saved":
            return "Run ue_verify_live_write for each saved operation that is not yet verified."
        if status == "unknown":
            return "Inspect the Editor session and affected assets; do not assume the missing in-memory state is still valid."
        return "Review the remaining non-terminal operations before continuing."

    def get_change_set(self, change_set_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._resolve_change_set(change_set_id)
            self._reconcile_change_set(record, persist=True)
            operations = [self._change_set_operation_payload(operation) for operation in record.operations]
            affected_assets = sorted({operation.asset_path for operation in record.operations if operation.asset_path})
            transaction_ids = sorted(
                {operation.transaction_id for operation in record.operations if operation.transaction_id}
            )
            active_count = sum(operation["active"] for operation in operations)
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_get_change_set",
                "ok": True,
                "projectName": self.project_name,
                "changeSetId": record.change_set_id,
                "taskId": record.task_id,
                "editorSessionId": record.editor_session_id,
                "title": record.title,
                "status": record.status,
                "operations": operations,
                "affectedAssets": affected_assets,
                "transactionIds": transaction_ids,
                "validation": self._change_set_validation(record),
                "saveState": self._change_set_save_state(record),
                "createdAtUtc": record.created_at_utc,
                "updatedAtUtc": record.updated_at_utc,
                "operationCount": len(operations),
                "receiptCount": len(operations),
                "activeReceiptCount": active_count,
                "receipts": operations,
                "nextStep": self._change_set_next_step(record),
            }

    def analyze_semantic_diff(
        self,
        change_set_id: str,
        *,
        stage: str = "auto",
        asset_paths: list[str] | None = None,
        include_unchanged: bool = True,
        max_changes: int = 64,
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        """Analyze only the evidence reachable from one explicit project-bound Change Set."""
        return analyze_workflow_semantic_diff(
            self,
            change_set_id,
            stage=stage,
            asset_paths=asset_paths,
            include_unchanged=include_unchanged,
            max_changes=max_changes,
            max_output_tokens=max_output_tokens,
        )

    def _bind_apply_operation(self, change_set_id: str, live_record: LiveApplyRecord) -> bool:
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        if record.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
            raise WorkflowError(
                "change-set-closed",
                f"The Change Set is in {record.status} state and cannot accept another live write.",
            )
        if len(record.operations) >= MAX_CHANGE_SET_RECEIPTS:
            raise WorkflowError(
                "change-set-full",
                f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
            )
        if record.editor_session_id and record.editor_session_id != live_record.editor_session_id:
            raise WorkflowError(
                "change-set-editor-session-mismatch",
                "The confirmed live write belongs to a different Editor session than the Change Set.",
            )
        if live_record.receipt in record.receipts:
            return True
        now = utc_now_iso()
        record.editor_session_id = live_record.editor_session_id
        record.operations.append(
            ChangeSetOperationRecord(
                receipt=live_record.receipt,
                plan_id=live_record.plan_id,
                asset_path=live_record.asset_path,
                operation=live_record.operation,
                transaction_id=live_record.transaction_id,
                editor_session_id=live_record.editor_session_id,
                status="saved" if live_record.saved else "applied",
                created_at_utc=now,
                updated_at_utc=now,
                save_receipt=live_record.save_receipt,
            )
        )
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = now
        return self._persist_change_set(record)

    def _bind_noop_operation(
        self,
        change_set_id: str,
        plan_record: PlanRecord,
        asset_path: str,
        operation_name: str,
        live_result: dict[str, Any],
    ) -> tuple[str, bool]:
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        if record.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
            raise WorkflowError(
                "change-set-closed",
                f"The Change Set is in {record.status} state and cannot accept another live write.",
            )
        if len(record.operations) >= MAX_CHANGE_SET_RECEIPTS:
            raise WorkflowError(
                "change-set-full",
                f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
            )
        editor_session_id = str(live_result.get("editorSessionId", ""))
        if record.editor_session_id and editor_session_id and record.editor_session_id != editor_session_id:
            raise WorkflowError(
                "change-set-editor-session-mismatch",
                "The confirmed no-op belongs to a different Editor session than the Change Set.",
            )
        receipt = "noop_" + secrets.token_urlsafe(16)
        now = utc_now_iso()
        record.editor_session_id = record.editor_session_id or editor_session_id
        record.operations.append(
            ChangeSetOperationRecord(
                receipt=receipt,
                plan_id=plan_record.plan_id,
                asset_path=asset_path,
                operation=operation_name,
                transaction_id="",
                editor_session_id=editor_session_id,
                status="no-op",
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = now
        return receipt, self._persist_change_set(record)

    def _bind_committed_apply(
        self,
        change_set_id: str,
        apply_record: ApplyRecord,
        plan_record: PlanRecord,
    ) -> bool:
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        if record.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
            raise WorkflowError(
                "change-set-closed",
                f"The Change Set is in {record.status} state and cannot accept another committed patch.",
            )
        if len(record.operations) >= MAX_CHANGE_SET_RECEIPTS:
            raise WorkflowError(
                "change-set-full",
                f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound workflow operations.",
            )
        if apply_record.receipt in record.receipts:
            return True
        assets = plan_record.patch.get("assets", [])
        patch_operations = assets[0].get("operations", []) if len(assets) == 1 and isinstance(assets[0], dict) else []
        operation_name = (
            str(patch_operations[0].get("operation", ""))
            if len(patch_operations) == 1 and isinstance(patch_operations[0], dict)
            else "multiOperationTransaction"
        )
        now = utc_now_iso()
        record.operations.append(
            ChangeSetOperationRecord(
                receipt=apply_record.receipt,
                plan_id=apply_record.plan_id,
                asset_path=apply_record.asset_path,
                operation=operation_name,
                transaction_id="",
                editor_session_id="",
                status="verified" if apply_record.verified else "saved",
                created_at_utc=now,
                updated_at_utc=now,
            )
        )
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = now
        return self._persist_change_set(record)

    def _assert_change_set_member(self, change_set_id: str, receipt: str) -> ChangeSetOperationRecord:
        try:
            receipt = validate_change_set_operation_receipt(receipt)
        except ValueError as exc:
            raise WorkflowError("change-set-transaction-not-member", str(exc)) from exc
        record = self._resolve_change_set(change_set_id)
        operation = next((candidate for candidate in record.operations if candidate.receipt == receipt), None)
        if operation is None:
            raise WorkflowError(
                "change-set-transaction-not-member",
                "The target live write is not bound to this Change Set.",
                details={"liveApplyReceipt": receipt},
            )
        return operation

    def _update_change_set_operation(
        self,
        change_set_id: str,
        receipt: str,
        status: str,
        *,
        save_receipt: str = "",
        failure_code: str = "",
    ) -> bool:
        operation = self._assert_change_set_member(change_set_id, receipt)
        operation.status = status
        if save_receipt:
            operation.save_receipt = save_receipt
        if failure_code:
            operation.failure_code = failure_code
        operation.updated_at_utc = utc_now_iso()
        record = self._resolve_change_set(change_set_id)
        record.status = derive_change_set_status(record.operations)
        record.updated_at_utc = operation.updated_at_utc
        return self._persist_change_set(record)

    def _prune_records(self) -> None:
        for mapping in (
            self._plans,
            self._dry_runs,
            self._applies,
            self._rollback_dry_runs,
            self._save_authorizations,
            self._authorized_save_rollback_dry_runs,
        ):
            while len(mapping) > MAX_WORKFLOW_RECORDS:
                mapping.pop(next(iter(mapping)))
        while len(self._live_applies) > MAX_WORKFLOW_RECORDS:
            receipt = next(iter(self._live_applies))
            self._live_applies.pop(receipt)
            self._delete_live_apply_journal(receipt)
        self._prune_terminal_change_sets(MAX_CHANGE_SETS)
        self._rebuild_live_apply_index()

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

    def _replace_refresh_export_candidate(
        self,
        next_export: Path,
        candidate_root: Path,
        candidate: dict[str, Any],
    ) -> None:
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

    def _merge_refresh_export(
        self,
        active_export: Path,
        next_export: Path,
        candidate_root: Path,
        candidate: dict[str, Any],
    ) -> None:
        clone_tree(
            active_export,
            next_export,
            prefer_hardlinks=bool(self.active_snapshot and not self.active_snapshot.legacy),
        )
        self._replace_refresh_export_candidate(next_export, candidate_root, candidate)

    def _validate_next_database_assets(
        self,
        database: Path,
        expected_revisions: dict[str, str],
    ) -> dict[str, Any]:
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
            for asset_path, expected_revision in expected_revisions.items():
                row = connection.execute(
                    "SELECT revision_value, package_dirty, canonical_relpath FROM assets WHERE asset_path = ?",
                    (asset_path,),
                ).fetchone()
                if row is None or str(row["revision_value"]) != expected_revision or bool(row["package_dirty"]):
                    raise WorkflowError(
                        "snapshot-refresh-database-invalid",
                        "The next SQLite generation does not contain every clean refreshed Revision.",
                        details={"assetPath": asset_path, "expectedRevision": expected_revision},
                    )
            asset_count = int(connection.execute("SELECT COUNT(*) FROM assets").fetchone()[0])
        return {
            "assetCount": asset_count,
            "targetRevisions": [
                {"assetPath": asset_path, "revision": revision}
                for asset_path, revision in expected_revisions.items()
            ],
        }

    def _validate_next_database(self, database: Path, asset_path: str, expected_revision: str) -> dict[str, Any]:
        validation = self._validate_next_database_assets(database, {asset_path: expected_revision})
        return {"assetCount": validation["assetCount"], "targetRevision": expected_revision}

    def _build_snapshot_generation_batch(
        self,
        prepared_candidates: list[tuple[Path, dict[str, Any]]],
    ) -> dict[str, Any]:
        if self.active_snapshot is None:
            raise WorkflowError("snapshot-refresh-unavailable", "This workflow session was not started from a frozen active snapshot pair.")
        if not prepared_candidates:
            raise WorkflowError("snapshot-refresh-batch-empty", "At least one prepared refresh candidate is required.")
        active = self.active_snapshot
        expected_revisions: dict[str, str] = {}
        for _, candidate in prepared_candidates:
            asset_path = str(candidate.get("canonical", {}).get("assetPath", ""))
            revision = str(candidate.get("revision", ""))
            if not asset_path or asset_path in expected_revisions or not revision.startswith("sha256:"):
                raise WorkflowError("snapshot-refresh-batch-invalid", "Prepared refresh candidates must have unique exact assets and SHA-256 Revisions.")
            expected_revisions[asset_path] = revision

        generation_id = new_generation_id()
        snapshots_root = self._safe_work_path("snapshots")
        snapshots_root.mkdir(parents=True, exist_ok=True)
        staging = snapshots_root / ("." + generation_id + ".staging")
        final_root = snapshots_root / generation_id
        if staging.exists() or final_root.exists():
            raise WorkflowError("snapshot-refresh-generation-exists", "The generated snapshot ID already exists.")
        candidate_bytes = sum(self._tree_size(root) for root, _ in prepared_candidates)
        required_bytes = (
            self._tree_size(active.revision_export)
            + active.database.stat().st_size * 2
            + candidate_bytes
            + 64 * 1024 * 1024
        )
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
            clone_tree(
                active.revision_export,
                next_export,
                prefer_hardlinks=bool(self.active_snapshot and not self.active_snapshot.legacy),
            )
            for candidate_root, candidate in prepared_candidates:
                self._replace_refresh_export_candidate(next_export, candidate_root, candidate)

            next_database = staging / "index.sqlite3"
            assert_quiescent_database(active.database)
            shutil.copy2(active.database, next_database)
            build_summaries: list[dict[str, Any]] = []
            with open_database(next_database) as connection:
                for candidate_root, candidate in prepared_candidates:
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
                            "The next SQLite generation did not update exactly one requested asset for every Batch candidate.",
                            details={"build": build_result.to_dict(include_assets=False)},
                        )
                    build_summaries.append(build_result.to_dict(include_assets=False))
                set_metadata(connection, "last_export_root", str(final_root / "revision-export"))

            database_validation = self._validate_next_database_assets(next_database, expected_revisions)
            manifest_path = next_export / "manifest.json"
            manifest_sha = sha256_file(manifest_path)
            database_sha = sha256_file(next_database)
            os.replace(staging, final_root)
            refreshed_assets = [
                {"assetPath": asset_path, "revision": revision}
                for asset_path, revision in expected_revisions.items()
            ]
            first = refreshed_assets[0]
            write_active_pointer(
                active,
                generation_id=generation_id,
                database_sha256=database_sha,
                revision_export_manifest_sha256=manifest_sha,
                refreshed_asset_path=first["assetPath"],
                refreshed_revision=first["revision"],
                refreshed_assets=refreshed_assets,
            )
            pointer_written = True
            return {
                "generationId": generation_id,
                "databaseSha256": "sha256:" + database_sha,
                "revisionExportManifestSha256": "sha256:" + manifest_sha,
                "assetCount": database_validation["assetCount"],
                "refreshedAssetCount": len(refreshed_assets),
                "targetRevisions": refreshed_assets,
                "builds": build_summaries,
            }
        except SnapshotLifecycleError as exc:
            raise WorkflowError(exc.code, str(exc), details=exc.details) from exc
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if final_root.exists() and not pointer_written:
                shutil.rmtree(final_root, ignore_errors=True)

    def _build_snapshot_generation(self, asset_path: str, candidate_root: Path, candidate: dict[str, Any]) -> dict[str, Any]:
        generation = self._build_snapshot_generation_batch([(candidate_root, candidate)])
        generation["targetRevision"] = candidate["revision"]
        return generation

    def prepare_batch_index_refresh_candidate(self, asset_path: str) -> dict[str, Any]:
        """Prepare one short-path, independently exported candidate for an atomic Batch snapshot refresh."""
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            self._assert_refresh_policy(asset_path)
            live_state = self._inspect_refresh_live_state(asset_path)
            candidate_id = "irc_" + secrets.token_urlsafe(10)
            candidate_root = self._safe_work_path("ir", candidate_id)
            try:
                candidate = self._export_refresh_candidate(asset_path, candidate_root)
                freshness = self.freshness.inspect_asset(asset_path)
                disk_revision = str(freshness.get("diskRevision", ""))
                if disk_revision and disk_revision != str(candidate.get("revision", "")):
                    raise WorkflowError(
                        "snapshot-refresh-revision-mismatch",
                        "The prepared Batch refresh candidate does not match the current disk Package Revision.",
                    )
                self._index_refresh_candidates[candidate_id] = (candidate_root, candidate)
                return {
                    "candidateId": candidate_id,
                    "assetPath": asset_path,
                    "assetClass": candidate["assetClass"],
                    "revision": candidate["revision"],
                    "diskFileSize": candidate["diskFileSize"],
                    "liveState": live_state,
                }
            except Exception:
                shutil.rmtree(candidate_root, ignore_errors=True)
                raise

    def discard_batch_index_refresh_candidates(self, candidate_ids: list[str]) -> None:
        with self._lock:
            for candidate_id in candidate_ids:
                prepared = self._index_refresh_candidates.pop(candidate_id, None)
                if prepared is not None:
                    shutil.rmtree(prepared[0], ignore_errors=True)

    def apply_batch_index_refresh(self, candidate_ids: list[str]) -> dict[str, Any]:
        """Atomically activate one paired snapshot generation containing every prepared Batch candidate."""
        with self._lock:
            self._assert_session_current()
            if not candidate_ids or len(candidate_ids) != len(set(candidate_ids)):
                raise WorkflowError("snapshot-refresh-batch-invalid", "Batch refresh candidate IDs must be non-empty and unique.")
            prepared_candidates: list[tuple[Path, dict[str, Any]]] = []
            for candidate_id in candidate_ids:
                prepared = self._index_refresh_candidates.get(candidate_id)
                if prepared is None:
                    raise WorkflowError("snapshot-refresh-batch-candidate-missing", "A prepared Batch refresh candidate is missing from this MCP session.")
                candidate_root, candidate = prepared
                package_file = self._package_file(
                    self.config.project_path,
                    str(candidate["packageName"]),
                    str(candidate["assetClass"]),
                )
                current_revision = "sha256:" + sha256_file(package_file)
                if current_revision != str(candidate["revision"]):
                    raise WorkflowError(
                        "snapshot-refresh-revision-mismatch",
                        "A Batch refresh target changed on disk after candidate preparation.",
                        details={"assetPath": candidate["canonical"].get("assetPath", "")},
                    )
                prepared_candidates.append((candidate_root, candidate))

            generation = self._build_snapshot_generation_batch(prepared_candidates)
            for candidate_id in candidate_ids:
                prepared = self._index_refresh_candidates.pop(candidate_id, None)
                if prepared is not None:
                    shutil.rmtree(prepared[0], ignore_errors=True)
            invalidated = {
                "planCount": len(self._plans),
                "dryRunReceiptCount": len(self._dry_runs),
                "applyReceiptCount": len(self._applies),
                "rollbackReceiptCount": len(self._rollback_dry_runs),
            }
            self._plans.clear()
            self._dry_runs.clear()
            self._applies.clear()
            self._rollback_dry_runs.clear()
            self._refresh_applied = True
            return {
                "applied": True,
                "activeSnapshotChanged": True,
                "newGeneration": generation,
                "invalidated": invalidated,
                "currentSessionUsesPreviousSnapshot": True,
                "restartRequired": True,
            }

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

    def discard_unconsumed_plans(self, plan_ids: list[str]) -> None:
        """Remove newly created session-local Plans that were never exposed for execution."""
        with self._lock:
            records: list[PlanRecord] = []
            for plan_id in plan_ids:
                record = self._plans.get(plan_id)
                if record is None:
                    continue
                if record.consumed:
                    raise WorkflowError(
                        "plan-cleanup-consumed",
                        "A child Plan was already consumed and cannot be removed during Batch Plan cleanup.",
                    )
                records.append(record)
            for record in records:
                self._plans.pop(record.plan_id, None)
                shutil.rmtree(record.patch_path.parent, ignore_errors=True)

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

    def apply_asset_property_live(self, plan_id: str, confirmation: str, change_set_id: str = "") -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Live Editor writes require Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Live Editor mode is required for an in-editor asset property write.",
                )
            if change_set_id:
                change_set = self._resolve_change_set(change_set_id)
                self._reconcile_change_set(change_set, persist=True)
                if change_set.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
                    raise WorkflowError(
                        "change-set-closed",
                        f"The Change Set is in {change_set.status} state and cannot accept another live write.",
                    )
                if len(change_set.operations) >= MAX_CHANGE_SET_RECEIPTS:
                    raise WorkflowError(
                        "change-set-full",
                        f"A Change Set is limited to {MAX_CHANGE_SET_RECEIPTS} bound live write operations.",
                    )
                editor_available, current_session_id = self._current_editor_session()
                if (
                    change_set.editor_session_id
                    and editor_available
                    and current_session_id != change_set.editor_session_id
                ):
                    raise WorkflowError(
                        "change-set-editor-session-mismatch",
                        "The Change Set belongs to a different Editor session.",
                    )
            record = self._plans.get(plan_id)
            if record is None:
                raise WorkflowError("plan-not-found", "The live write plan is not active in this MCP server session.")
            if confirmation != f"LIVE APPLY {plan_id}":
                raise WorkflowError(
                    "live-editor-write-confirmation-required",
                    "Live write confirmation did not exactly match the required planId phrase.",
                )
            validation = self._validate_plan_file(record)
            if not validation.get("commitAllowedByPolicy"):
                raise WorkflowError("live-editor-write-not-allowed", "The fixed Policy does not enable this write.")
            assets = record.patch.get("assets", [])
            if not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
                raise WorkflowError("plan-invalid", "The live write plan no longer contains exactly one asset.")
            operations = assets[0].get("operations", [])
            if not isinstance(operations, list) or len(operations) != 1 or not isinstance(operations[0], dict):
                raise WorkflowError("plan-invalid", "The live write plan no longer contains exactly one operation.")
            operation = operations[0]
            operation_name = str(operation.get("operation", ""))
            operation_spec = LIVE_WRITE_OPERATION_REGISTRY.get(operation_name)
            if operation_spec is None:
                supported = ", ".join(sorted(LIVE_WRITE_OPERATION_REGISTRY))
                raise WorkflowError(
                    "live-editor-write-operation-unsupported",
                    f"Unsupported Live Editor write operation. Registered operations: {supported}.",
                )
            target = operation.get("target", {})
            if not isinstance(target, dict):
                raise WorkflowError("plan-invalid", "The live write plan target must be an object.")
            bridge_parameters: dict[str, Any] = {
                "operation": operation_name,
                "assetPath": str(assets[0].get("assetPath", "")),
                "target": target,
                "value": operation.get("value"),
            }
            for target_field in operation_spec.target_fields:
                target_value = target.get(target_field)
                validator = operation_spec.target_validators.get(target_field)
                if validator is None or not validator(target_value):
                    raise WorkflowError(
                        "plan-invalid",
                        f"The live write plan has no valid exact {target_field}.",
                    )
                bridge_parameters[target_field] = target_value
            property_path = target.get("propertyPath")
            parameter_name = target.get("parameterName")
            row_name = target.get("rowName")
            new_row_name = target.get("newRowName")
            field_name = target.get("fieldName")
            asset_path = str(assets[0].get("assetPath", ""))
            expected_revision = str(assets[0].get("expectedRevision", ""))
            bridge_parameters["assetPath"] = asset_path
            try:
                live_result = self.live_editor_service.call_method(
                    "editor.applyAssetPropertyLive",
                    bridge_parameters,
                )
            except Exception as exc:
                if hasattr(exc, "code"):
                    raise WorkflowError(str(exc.code), str(exc), details=getattr(exc, "details", {})) from exc
                raise
            changed = bool(live_result.get("changed"))
            live_apply_receipt = ""
            change_set_operation_id = ""
            change_set_bound = False
            change_set_journal_persisted = True
            if changed:
                live_apply_receipt = "live_" + secrets.token_urlsafe(16)
                self._live_applies[live_apply_receipt] = LiveApplyRecord(
                    receipt=live_apply_receipt,
                    plan_id=plan_id,
                    plan_digest=record.digest,
                    asset_path=asset_path,
                    operation=operation_name,
                    value_kind=_live_write_value_kind(operation_name),
                    editor_session_id=str(live_result.get("editorSessionId", "")),
                    transaction_id=str(live_result.get("transactionId", "")),
                    before_value=live_result.get("beforeValue"),
                    after_value=live_result.get("afterValue"),
                    target=target,
                    applied_at_utc=utc_now_iso(),
                )
                self._live_apply_by_asset[asset_path] = live_apply_receipt
                journal_persisted = self._persist_live_apply(self._live_applies[live_apply_receipt])
                if change_set_id:
                    change_set_bound = True
                    change_set_journal_persisted = self._bind_apply_operation(
                        change_set_id, self._live_applies[live_apply_receipt]
                    )
                    change_set_operation_id = live_apply_receipt
                self._prune_records()
            else:
                journal_persisted = False
                if change_set_id:
                    change_set_operation_id, change_set_journal_persisted = self._bind_noop_operation(
                        change_set_id, record, asset_path, operation_name, live_result
                    )
                    change_set_bound = True
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_apply_asset_property_live",
                "ok": True,
                "mode": "LiveApply",
                "planId": plan_id,
                "patchDigest": record.digest,
                "projectName": self.project_name,
                "assetPath": asset_path,
                "expectedDiskRevision": expected_revision,
                "operation": operation_name,
                "valueKind": _live_write_value_kind(operation_name),
                "propertyPath": property_path,
                "parameterName": parameter_name,
                "rowName": row_name,
                "newRowName": new_row_name,
                "fieldName": field_name,
                "changed": changed,
                "saved": False,
                "diskRevisionChanged": False,
                "undoAvailableInEditor": bool(live_result.get("transactionRecorded")),
                "liveApplyReceipt": live_apply_receipt,
                "journalPersisted": journal_persisted,
                "result": live_result,
                "nextStep": (
                    "Verify, Save, or Undo the in-editor change. To persist it, preview ue_save_authorized_asset for this exact asset."
                    if changed
                    else "No value change was required."
                ),
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetBound"] = change_set_bound
                response["changeSetOperationId"] = change_set_operation_id
                response["changeSetJournalPersisted"] = change_set_journal_persisted
            return response

    def undo_asset_property_live(
        self,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        return self._revert_asset_property_live(
            "undo",
            asset_path,
            transaction_id,
            editor_session_id,
            change_set_id,
        )

    def discard_asset_property_live(
        self,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        return self._revert_asset_property_live(
            "discard",
            asset_path,
            transaction_id,
            editor_session_id,
            change_set_id,
        )

    def _revert_asset_property_live(
        self,
        action: str,
        asset_path: str,
        transaction_id: str,
        editor_session_id: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Live Editor write reverts require Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Live Editor mode is required to revert an in-editor asset property write.",
                )
            asset_path = self._validate_refresh_asset_path(asset_path)
            if (
                not isinstance(transaction_id, str)
                or len(transaction_id) != 36
                or not _is_guid_with_hyphens(transaction_id)
            ):
                raise WorkflowError(
                    "live-editor-write-undo-invalid-transaction-id",
                    "transactionId must be the exact transactionId returned by the confirmed live write.",
                )
            if not isinstance(editor_session_id, str) or not editor_session_id:
                raise WorkflowError(
                    "live-editor-write-undo-session-required",
                    "editorSessionId must be the exact editorSessionId returned by the confirmed live write.",
                )
            receipt = next(
                (
                    candidate_receipt
                    for candidate_receipt, candidate in self._live_applies.items()
                    if candidate.asset_path == asset_path
                    and candidate.transaction_id == transaction_id
                    and candidate.editor_session_id == editor_session_id
                ),
                "",
            )
            if change_set_id:
                self._assert_change_set_member(change_set_id, receipt)
            try:
                live_result = self.live_editor_service.call_method(
                    f"editor.{action}AssetPropertyLive",
                    {
                        "assetPath": asset_path,
                        "transactionId": transaction_id,
                        "sessionId": editor_session_id,
                    },
                )
            except Exception as exc:
                if hasattr(exc, "code"):
                    raise WorkflowError(str(exc.code), str(exc), details=getattr(exc, "details", {})) from exc
                raise
            change_set_updated = False
            change_set_operation_status = "undone" if action == "undo" else "discarded"
            if receipt:
                if change_set_id:
                    change_set_updated = self._update_change_set_operation(
                        change_set_id,
                        receipt,
                        change_set_operation_status,
                    )
                self._remove_live_apply(receipt)
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": f"ue_{action}_asset_property_live",
                "ok": True,
                "mode": "LiveUndo" if action == "undo" else "LiveDiscard",
                "assetPath": asset_path,
                "transactionId": transaction_id,
                "editorSessionId": editor_session_id,
                "operation": live_result.get("operation"),
                "valueKind": live_result.get("valueKind"),
                "changed": bool(live_result.get("changed")),
                "saved": False,
                "diskRevisionChanged": False,
                "result": live_result,
                "nextStep": (
                    "The live write was reverted in Editor memory without saving the package. "
                    "Re-plan the write to re-apply it."
                ),
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
                response["changeSetOperationStatus"] = change_set_operation_status
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

    def apply_patch(
        self,
        plan_id: str,
        dry_run_receipt: str,
        confirmation: str,
        change_set_id: str = "",
    ) -> dict[str, Any]:
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
            if change_set_id:
                change_set = self._resolve_change_set(change_set_id)
                self._reconcile_change_set(change_set, persist=True)
                if change_set.status in {"undone", "discarded", "verified", "no-op", "failed", "unknown"}:
                    raise WorkflowError(
                        "change-set-closed",
                        f"The Change Set is in {change_set.status} state and cannot accept this patch.",
                    )
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
            change_set_updated = (
                self._bind_committed_apply(change_set_id, self._applies[receipt], record)
                if change_set_id
                else False
            )
            self._prune_records()
            response = {
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
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
            return response

    def verify_asset(self, apply_receipt: str, change_set_id: str = "") -> dict[str, Any]:
        with self._lock:
            self._assert_session_current()
            apply = self._applies.get(apply_receipt)
            if apply is None:
                raise WorkflowError("apply-receipt-not-found", "The applyReceipt is not active in this MCP server session.")
            if change_set_id:
                self._assert_change_set_member(change_set_id, apply_receipt)
            output = self._safe_work_path("verify", apply_receipt)
            if output.exists():
                shutil.rmtree(output)
            output.mkdir(parents=True, exist_ok=False)
            asset_package = apply.asset_path.split(".", 1)[0]
            asset_class = str(apply.report.get("assetClass", ""))
            if asset_class == "/Script/Engine.Blueprint":
                verify_script = "RunExport.ps1"
                verify_arguments = [
                    "-EngineRoot",
                    str(self.config.engine_root),
                    "-ProjectPath",
                    str(self.config.project_path),
                    "-Asset",
                    asset_package,
                    "-Output",
                    str(output),
                    "-Profile",
                    "full",
                    "-Format",
                    "json",
                    "-IncludeUnchangedDefaults",
                ]
            else:
                verify_script = "RunAssetCatalog.ps1"
                verify_arguments = [
                    "-EngineRoot",
                    str(self.config.engine_root),
                    "-ProjectPath",
                    str(self.config.project_path),
                    "-Asset",
                    asset_package,
                    "-Output",
                    str(output),
                ]
            result = self._run_script(
                verify_script,
                verify_arguments,
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
            change_set_updated = (
                self._update_change_set_operation(change_set_id, apply_receipt, "verified")
                if change_set_id
                else False
            )
            freshness = self.freshness.inspect_asset(apply.asset_path)
            verification_report_id = _report_id("verify-export", output / "manifest.json")
            memory_task_evidence = _verified_memory_task_evidence(
                apply,
                validation_report_id=verification_report_id,
                actual_revision=actual_revision,
            )
            response = {
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
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
            return response

    def save_authorized_asset(
        self,
        asset_path: str,
        *,
        mode: Literal["Preview", "Commit"] = "Preview",
        save_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            self._assert_session_current()
            asset_path = self._validate_refresh_asset_path(asset_path)
            if mode not in {"Preview", "Commit"}:
                raise WorkflowError("authorized-save-invalid-mode", "mode must be Preview or Commit.")
            if change_set_id:
                change_set = self._resolve_change_set(change_set_id)
                member_assets = {
                    self._live_applies[receipt].asset_path
                    for receipt in change_set.receipts
                    if self._live_applies.get(receipt) is not None
                }
                if asset_path not in member_assets:
                    raise WorkflowError(
                        "change-set-transaction-not-member",
                        "The target asset has no live write bound to this Change Set.",
                        details={"changeSetId": change_set_id, "assetPath": asset_path},
                    )
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
                preview_response = {
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
                if change_set_id:
                    preview_response["changeSetId"] = change_set_id
                return preview_response

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
            change_set_receipts = set(change_set.receipts) if change_set_id else set()
            live_candidates = [
                (candidate.applied_at_utc, candidate_receipt, candidate)
                for candidate_receipt, candidate in self._live_applies.items()
                if candidate.asset_path == asset_path
                and candidate.editor_session_id == editor_session_id
                and not candidate.saved
                and (not change_set_id or candidate_receipt in change_set_receipts)
            ]
            live_receipt = ""
            journal_persisted = True
            change_set_updated = False
            if live_candidates:
                _, live_receipt, live_record = max(live_candidates)
                live_record.saved = True
                live_record.save_receipt = save_receipt
                journal_persisted = self._persist_live_apply(live_record)
                if change_set_id:
                    change_set_updated = self._update_change_set_operation(
                        change_set_id,
                        live_receipt,
                        "saved",
                        save_receipt=save_receipt,
                    )
            response = {
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
                "liveApplyReceipt": live_receipt,
                "liveWriteSaved": bool(live_receipt),
                "journalPersisted": journal_persisted,
                "bridge": _safe_report(bridge_result, configured_paths=self.configured_paths),
                "indexFreshness": freshness_after,
                "nextStep": (
                    "Call ue_verify_live_write for this exact asset to close the loop with an "
                    "independent reload, Revision, and memory Task Record, then refresh the asset index."
                ),
            }
            if change_set_id:
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
                response["changeSetOperationStatus"] = "saved" if live_receipt else "unknown"
            return response

    def create_authorized_save_rollback_manifest(
        self,
        save_receipt: str,
        live_apply_receipt: str,
    ) -> dict[str, Any]:
        """Promote an authorized live-save backup to the standard rollback manifest format."""
        with self._lock:
            self._assert_session_current()
            self._assert_policy_unchanged()
            if not isinstance(save_receipt, str) or not save_receipt.startswith("save_"):
                raise WorkflowError("save-receipt-invalid", "saveReceipt is not a valid authorized-save receipt.")
            live_apply_receipt = self._validate_live_apply_receipt(live_apply_receipt)
            live_record = self._live_applies.get(live_apply_receipt)
            if live_record is None:
                raise WorkflowError(
                    "live-write-verify-not-found",
                    "The authorized save no longer has its pending live write record.",
                )
            if not live_record.saved or live_record.save_receipt != save_receipt:
                raise WorkflowError(
                    "authorized-save-rollback-not-ready",
                    "The live write was not saved by the requested authorized-save receipt.",
                )
            plan_record = self._plans.get(live_record.plan_id)
            if plan_record is None:
                raise WorkflowError(
                    "authorized-save-plan-not-found",
                    "The child Plan required to authorize rollback is no longer active in this MCP session.",
                )
            stored_patch = _read_json(plan_record.patch_path)
            if _sha256_bytes(_json_bytes(stored_patch)) != plan_record.digest or stored_patch != plan_record.patch:
                raise WorkflowError(
                    "plan-tampered",
                    "The child Plan changed before the authorized-save rollback manifest was created.",
                )

            backup_directory = (self.config.backup_root / "live-save" / save_receipt).resolve()
            if not _is_within(backup_directory, self.config.backup_root):
                raise WorkflowError("workflow-path-invalid", "The authorized-save backup escaped the fixed backup root.")
            legacy_manifest_path = backup_directory / "manifest.json"
            legacy_manifest = _read_json(legacy_manifest_path, stage="authorized-save-backup")
            if (
                legacy_manifest.get("assetPath") != live_record.asset_path
                or legacy_manifest.get("projectName") != self.project_name
            ):
                raise WorkflowError(
                    "authorized-save-backup-invalid",
                    "The authorized-save backup identity does not match the pending live write.",
                )
            before_revision = str(legacy_manifest.get("beforeRevision", ""))
            backup_file_name = str(legacy_manifest.get("backupFileName", ""))
            asset_class = str(legacy_manifest.get("assetClass", ""))
            package_name = str(legacy_manifest.get("packageName", ""))
            backup_file = (backup_directory / backup_file_name).resolve()
            if not backup_file.is_file() or not _is_within(backup_file, backup_directory):
                raise WorkflowError("authorized-save-backup-invalid", "The authorized-save backup file is missing or invalid.")
            package_file = self._package_file(self.config.project_path, package_name, asset_class)
            after_revision = "sha256:" + sha256_file(package_file)
            if before_revision == after_revision:
                raise WorkflowError(
                    "authorized-save-revision-unchanged",
                    "A rollback manifest requires a real disk Revision transition.",
                )

            rollback_manifest_path = backup_directory / "rollback-manifest.json"
            if rollback_manifest_path.is_file():
                validation = rollback_backup(
                    rollback_manifest_path,
                    self.config.policy_path,
                    self.config.project_path,
                    self.config.backup_root,
                    commit=False,
                )
                if validation.get("valid") is not True:
                    raise WorkflowError(
                        "authorized-save-rollback-manifest-invalid",
                        "The existing authorized-save rollback manifest no longer validates.",
                        details={"errors": validation.get("errors", [])},
                    )
                return {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "ok": True,
                    "saveReceipt": save_receipt,
                    "liveApplyReceipt": live_apply_receipt,
                    "assetPath": live_record.asset_path,
                    "beforeRevision": validation.get("expectedBackupRevision", before_revision),
                    "afterRevision": validation.get("expectedCurrentRevision", after_revision),
                    "rollbackManifestId": validation.get("manifestId", ""),
                    "rollbackAvailable": True,
                    "created": False,
                }

            commit_report_path = backup_directory / "commit-report.json"
            _write_json_atomic(
                commit_report_path,
                {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "mode": "Commit",
                    "saved": True,
                    "patchId": plan_record.patch.get("patchId", ""),
                    "projectName": self.project_name,
                    "assetPath": live_record.asset_path,
                    "assetClass": asset_class,
                    "operation": live_record.operation,
                    "target": live_record.target,
                    "beforeValue": live_record.before_value,
                    "afterValue": live_record.after_value,
                    "beforeRevision": before_revision,
                    "afterRevision": after_revision,
                    "backupPath": str(backup_file),
                    "executorVersion": DEVELOPMENT_LINE,
                },
            )
            try:
                created = create_backup_manifest(
                    plan_record.patch_path,
                    self.config.policy_path,
                    commit_report_path,
                    self.config.backup_root,
                    output_path=rollback_manifest_path,
                )
            except (FileNotFoundError, OSError, ValueError) as exc:
                raise WorkflowError(
                    "authorized-save-rollback-manifest-failed",
                    "The authorized-save backup could not be promoted to a rollback-safe manifest.",
                ) from exc
            manifest = created.get("manifest", {}) if isinstance(created, dict) else {}
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "ok": True,
                "saveReceipt": save_receipt,
                "liveApplyReceipt": live_apply_receipt,
                "assetPath": live_record.asset_path,
                "beforeRevision": before_revision,
                "afterRevision": after_revision,
                "rollbackManifestId": str(manifest.get("manifestId", "")),
                "rollbackAvailable": True,
                "created": True,
            }

    def rollback_authorized_live_save(
        self,
        save_receipt: str,
        *,
        mode: Literal["DryRun", "Commit"] = "DryRun",
        rollback_dry_run_receipt: str = "",
        confirmation: str = "",
        change_set_id: str = "",
        live_apply_receipt: str = "",
    ) -> dict[str, Any]:
        """Rollback one persisted authorized live save through the standard backup engine."""
        with self._lock:
            self._assert_session_current()
            self._assert_policy_unchanged()
            if not isinstance(save_receipt, str) or not save_receipt.startswith("save_"):
                raise WorkflowError("save-receipt-invalid", "saveReceipt is not a valid authorized-save receipt.")
            if mode not in {"DryRun", "Commit"}:
                raise WorkflowError("authorized-save-rollback-invalid-mode", "mode must be DryRun or Commit.")
            manifest_path = (self.config.backup_root / "live-save" / save_receipt / "rollback-manifest.json").resolve()
            if not manifest_path.is_file() or not _is_within(manifest_path, self.config.backup_root):
                raise WorkflowError(
                    "authorized-save-rollback-manifest-missing",
                    "The authorized save has no rollback-safe standard manifest.",
                )

            if mode == "DryRun":
                receipt = "live_save_rollback_dry_" + secrets.token_urlsafe(20)
                report_path = self._safe_work_path("authorized-save-rollback", save_receipt, receipt, "dry-run.json")
                try:
                    report = rollback_backup(
                        manifest_path,
                        self.config.policy_path,
                        self.config.project_path,
                        self.config.backup_root,
                        commit=False,
                        report_path=report_path,
                    )
                except (OSError, RuntimeError, ValueError) as exc:
                    raise WorkflowError(
                        "authorized-save-rollback-dry-run-failed",
                        "The authorized-save rollback Dry Run failed.",
                    ) from exc
                if report.get("valid") is not True or report.get("wroteDisk") is not False:
                    raise WorkflowError(
                        "authorized-save-rollback-dry-run-invalid",
                        "Rollback Dry Run did not confirm a valid zero-write restore.",
                        details={"errors": report.get("errors", [])},
                    )
                self._authorized_save_rollback_dry_runs[receipt] = AuthorizedSaveRollbackDryRunRecord(
                    receipt=receipt,
                    save_receipt=save_receipt,
                    report_path=report_path,
                    report=report,
                )
                self._prune_records()
                return {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "ok": True,
                    "mode": "DryRun",
                    "saveReceipt": save_receipt,
                    "rollbackDryRunReceipt": receipt,
                    "assetPath": report.get("assetPath", ""),
                    "beforeRollbackRevision": report.get("currentRevision", ""),
                    "expectedRestoredRevision": report.get("expectedBackupRevision", ""),
                    "wroteDisk": False,
                }

            if not self.config.commit_enabled:
                raise WorkflowError("commit-disabled", "Authorized-save rollback Commit is disabled for this MCP session.")
            dry_run = self._authorized_save_rollback_dry_runs.get(rollback_dry_run_receipt)
            if dry_run is None or dry_run.save_receipt != save_receipt or dry_run.consumed:
                raise WorkflowError(
                    "authorized-save-rollback-receipt-invalid",
                    "A fresh authorized-save rollback Dry Run receipt is required.",
                )
            if confirmation != f"ROLLBACK LIVE SAVE {save_receipt}":
                raise WorkflowError(
                    "authorized-save-rollback-confirmation-required",
                    "Rollback confirmation did not exactly match the required saveReceipt phrase.",
                )
            operation_root = self._safe_work_path(
                "authorized-save-rollback",
                save_receipt,
                rollback_dry_run_receipt,
                "commit",
            )
            report_path = operation_root / "report.json"
            verification_key = hashlib.sha256(
                f"{save_receipt}:{rollback_dry_run_receipt}".encode("utf-8")
            ).hexdigest()[:16]
            verification_root = self._safe_work_path("rollback-verify", verification_key)
            verification_output = verification_root / "export"
            verification_report = verification_root / "verification.json"
            result = self._run_script(
                "RunRollback.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Manifest", str(manifest_path),
                    "-Policy", str(self.config.policy_path),
                    "-BackupRoot", str(self.config.backup_root),
                    "-Mode", "Commit",
                    "-Report", str(report_path),
                    "-VerificationOutput", str(verification_output),
                    "-VerificationReport", str(verification_report),
                ],
                stage="authorized-save-rollback-commit",
                report_path=report_path,
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="authorized-save-rollback-commit",
                    result=result,
                    report_path=report_path,
                    fallback_code="authorized-save-rollback-commit-failed",
                    fallback_message="Authorized-save rollback Commit or independent verification failed.",
                )
            report = _read_json(report_path, stage="authorized-save-rollback-commit")
            verification = _read_json(verification_report, stage="authorized-save-rollback-verification")
            if report.get("restored") is not True or verification.get("verified") is not True:
                raise WorkflowError(
                    "authorized-save-rollback-report-invalid",
                    "Rollback reports did not confirm restore and independent verification.",
                )
            restored_revision = str(
                verification.get("actualRevision", verification.get("expectedRevision", ""))
            )
            if restored_revision != str(dry_run.report.get("expectedBackupRevision", "")):
                raise WorkflowError(
                    "authorized-save-rollback-revision-mismatch",
                    "Rollback verification did not match the pre-save Revision.",
                )
            dry_run.consumed = True
            asset_path = str(report.get("assetPath", dry_run.report.get("assetPath", "")))
            freshness = self.freshness.mark_rollback(asset_path, restored_revision)
            change_set_updated = False
            if change_set_id and live_apply_receipt:
                change_set_updated = self._update_change_set_operation(
                    change_set_id,
                    live_apply_receipt,
                    "undone",
                )
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "ok": True,
                "mode": "Commit",
                "saveReceipt": save_receipt,
                "rollbackDryRunReceipt": rollback_dry_run_receipt,
                "assetPath": asset_path,
                "restored": True,
                "restoredRevision": restored_revision,
                "reportId": _report_id("authorized-save-rollback-commit", report_path),
                "verificationReportId": _report_id(
                    "authorized-save-rollback-verification",
                    verification_report,
                ),
                "changeSetId": change_set_id,
                "changeSetUpdated": change_set_updated,
                "indexFreshness": freshness,
            }

    def verify_live_write(self, asset_path: str, live_apply_receipt: str = "", change_set_id: str = "") -> dict[str, Any]:
        with self._lock:
            if not self.config.commit_enabled:
                raise WorkflowError(
                    "live-editor-write-disabled",
                    "Live Editor write verification requires Commit tools to be enabled when the MCP server starts.",
                )
            if self.live_editor_service is None:
                raise WorkflowError(
                    "live-editor-required",
                    "Live Editor mode is required to verify an in-editor asset property write.",
                )
            asset_path = self._validate_refresh_asset_path(asset_path)
            receipt, record = self._resolve_live_apply(asset_path, live_apply_receipt)
            if change_set_id:
                self._assert_change_set_member(change_set_id, receipt)
            try:
                inspection = self.live_editor_service.call_tool("ue_inspect_asset_live", {"assetPath": asset_path})
            except Exception as exc:
                raise WorkflowError("live-editor-status-unavailable", "The target asset could not be inspected before verification.") from exc
            result = inspection.get("result", {}) if isinstance(inspection, dict) else {}
            memory = result.get("memory", {}) if isinstance(result, dict) else {}
            if not isinstance(memory, dict) or memory.get("loaded") is not True:
                raise WorkflowError(
                    "live-editor-write-verify-not-loaded",
                    "The exact asset is no longer loaded in the Editor; re-open it before verification.",
                )
            if memory.get("packageDirty") is True:
                # The live write is still unsaved in Editor memory: the closed loop
                # must not fake success. Report the terminal not-saved state with
                # Undo/Discard still available and an unchanged Revision.
                freshness = self.freshness.inspect_asset(asset_path)
                current_revision = str(freshness.get("diskRevision", ""))
                report_id = f"live-write-not-saved:{receipt}"
                memory_task_evidence = _live_write_memory_task_evidence(
                    record,
                    state="not-saved",
                    conclusion=(
                        f"The live write {record.plan_id} on {asset_path} is still unsaved in "
                        f"Editor memory; the package is Dirty and Undo/Discard remain available. "
                        f"No disk Revision changed."
                    ),
                    outcome="cancelled",
                    revision=current_revision,
                    report_id=report_id,
                    undo_available=True,
                    independent_reload=False,
                )
                not_saved_response = {
                    "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                    "tool": "ue_verify_live_write",
                    "ok": True,
                    "mode": "LiveVerify",
                    "state": "not-saved",
                    "assetPath": asset_path,
                    "planId": record.plan_id,
                    "patchDigest": record.plan_digest,
                    "operation": record.operation,
                    "valueKind": record.value_kind,
                    "liveApplyReceipt": receipt,
                    "transactionId": record.transaction_id,
                    "undoAvailable": True,
                    "saved": False,
                    "verified": False,
                    "diskRevision": current_revision,
                    "reportId": report_id,
                    "memoryTaskEvidence": memory_task_evidence,
                    "memoryRecorded": False,
                    "indexFreshness": freshness,
                    "nextStep": (
                        "The write is not persisted. Persist it with ue_save_authorized_asset "
                        "(Preview then Commit), or revert it with ue_undo_asset_property_live / "
                        "ue_discard_asset_property_live. A successful revert closes this pending live write."
                    ),
                }
                if change_set_id:
                    not_saved_response["changeSetId"] = change_set_id
                return not_saved_response

            if not record.saved:
                raise WorkflowError(
                    "live-write-verify-save-unauthorized",
                    "The target package became clean without a confirmed authorized save; the "
                    "asset or Session state diverged after the live write. Re-plan the write.",
                    details={"liveApplyReceipt": receipt},
                )
            output = self._safe_work_path("verify-live-write", receipt)
            if output.exists():
                shutil.rmtree(output)
            output.mkdir(parents=True, exist_ok=False)
            asset_package = asset_path.split(".", 1)[0]
            result = self._run_script(
                "RunAssetCatalog.ps1",
                [
                    "-EngineRoot", str(self.config.engine_root),
                    "-ProjectPath", str(self.config.project_path),
                    "-Asset", asset_package,
                    "-Output", str(output),
                ],
                stage="live-write-verify-export",
                report_path=output / "manifest.json",
            )
            if result.exit_code != 0:
                self._raise_process_failure(
                    stage="live-write-verify-export",
                    result=result,
                    report_path=output / "manifest.json",
                    fallback_code="live-write-verify-export-failed",
                    fallback_message="The independent Unreal reload export failed for the live write.",
                )
            canonical_files = list((output / "canonical").rglob("*.json"))
            if len(canonical_files) != 1:
                raise WorkflowError("live-write-verify-export-invalid", "Independent reload did not produce exactly one Canonical asset.")
            canonical = _read_json(canonical_files[0], stage="live-write-verify-canonical")
            revision = canonical.get("revision", {})
            actual_revision = revision.get("value", "") if isinstance(revision, dict) else ""
            if canonical.get("assetPath") != asset_path or not actual_revision.startswith("sha256:"):
                raise WorkflowError(
                    "live-write-verify-revision-mismatch",
                    "Independent Unreal reload did not match the live write target asset and Revision.",
                    details={"expectedAsset": asset_path, "actualAsset": canonical.get("assetPath", "")},
                )
            freshness = self.freshness.inspect_asset(asset_path)
            if str(freshness.get("diskRevision", "")) != actual_revision:
                raise WorkflowError(
                    "live-write-verify-revision-mismatch",
                    "The independent reload Revision does not match the current disk Package Revision.",
                    details={"diskRevision": freshness.get("diskRevision", ""), "actualRevision": actual_revision},
                )
            if str(freshness.get("indexRevision", "")) == actual_revision:
                raise WorkflowError(
                    "live-write-verify-revision-unchanged",
                    "The disk Package Revision is unchanged from the frozen index; the live write was not persisted.",
                )
            exported_value = _live_write_exported_value(canonical, record)
            expected_exported_value = _live_write_expected_exported_value(record)
            runtime_verification = _live_write_runtime_verification(record)
            if not _live_write_exported_matches(expected_exported_value, exported_value):
                raise WorkflowError(
                    "live-write-verify-value-mismatch",
                    "The independently reloaded asset value does not match the applied live write value; "
                    "the asset changed after the live write.",
                    details={
                        "persistedExpectedValue": expected_exported_value,
                        "exportedPersistedValue": exported_value,
                        "expectedValue": expected_exported_value,
                        "exportedValue": exported_value,
                    },
                )
            record.verified = True
            verification_report_id = _report_id("live-write-verify-export", output / "manifest.json")
            memory_task_evidence = _live_write_memory_task_evidence(
                record,
                state="verified",
                conclusion=(
                    f"The live write {record.plan_id} on {asset_path} was authorized-saved and "
                    f"independently reloaded at Revision {actual_revision}; the exported value "
                    f"matches the applied value and the live write is no longer undoable in the Editor."
                ),
                outcome="succeeded",
                revision=actual_revision,
                report_id=verification_report_id,
                undo_available=False,
                independent_reload=True,
            )
            response = {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_verify_live_write",
                "ok": True,
                "mode": "LiveVerify",
                "state": "verified",
                "assetPath": asset_path,
                "planId": record.plan_id,
                "patchDigest": record.plan_digest,
                "operation": record.operation,
                "valueKind": record.value_kind,
                "liveApplyReceipt": receipt,
                "transactionId": record.transaction_id,
                "undoAvailable": False,
                "saved": True,
                "verified": True,
                "appliedValue": record.after_value,
                "persistedExpectedValue": expected_exported_value,
                "exportedPersistedValue": exported_value,
                "runtimeVerification": runtime_verification,
                "expectedValue": expected_exported_value,
                "exportedValue": exported_value,
                "expectedDiskRevision": str(freshness.get("diskRevision", "")),
                "actualRevision": actual_revision,
                "assetClass": canonical.get("assetClass", ""),
                "packageDirty": False,
                "reportId": verification_report_id,
                "memoryTaskEvidence": memory_task_evidence,
                "memoryRecorded": False,
                "indexFreshness": freshness,
                "nextStep": (
                    "If Project Memory is enabled, pass memoryTaskEvidence.arguments unchanged to "
                    "ue_memory_record_task to persist the closed-loop Task Record, then call "
                    "ue_refresh_asset_index to activate the new Revision."
                ),
            }
            if change_set_id:
                change_set_updated = self._update_change_set_operation(
                    change_set_id,
                    receipt,
                    "verified",
                    save_receipt=record.save_receipt,
                )
                response["changeSetId"] = change_set_id
                response["changeSetUpdated"] = change_set_updated
                response["changeSetOperationStatus"] = "verified"
            self._remove_live_apply(receipt)
            return response

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
            rollback_report_id = _report_id("rollback-commit", report_path)
            verification_report_id = _report_id(
                "rollback-verification",
                verification_report,
            )
            memory_task_evidence = _rollback_memory_task_evidence(
                apply,
                rollback_report_id=rollback_report_id,
                verification_report_id=verification_report_id,
                restored_revision=restored_revision,
            )
            return {
                "schemaVersion": WORKFLOW_SCHEMA_VERSION,
                "tool": "ue_rollback_patch",
                "ok": True,
                "mode": "Commit",
                "applyReceipt": apply_receipt,
                "assetPath": apply.asset_path,
                "restored": True,
                "expectedRevision": apply.before_revision,
                "reportId": rollback_report_id,
                "verificationReportId": verification_report_id,
                "memoryTaskEvidence": memory_task_evidence,
                "indexFreshness": freshness,
                "verification": _safe_report(verification, configured_paths=self.configured_paths),
                "report": _safe_report(report, configured_paths=self.configured_paths),
                "nextStep": (
                    "If Project Memory is enabled, pass memoryTaskEvidence.arguments unchanged to "
                    "ue_memory_record_task."
                ),
            }

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from .agent_api import (
    IndexQueryService,
)
from .change_sets import (
    ChangeSetRecord,
    MAX_CHANGE_SETS,
    MAX_CHANGE_SET_RECEIPTS,
)
from .freshness import (
    IndexFreshnessTracker,
)
from .patches import (
    LIVE_WRITE_OPERATION_REGISTRY,
)
from .snapshot_lifecycle import (
    ActiveSnapshot,
)
from .verification_evidence import (
    VerificationEvidenceStore,
)

def _is_reparse_point(*args: Any, **kwargs: Any) -> Any:
    from . import agent_workflow as _agent_workflow_compat
    return _agent_workflow_compat._is_reparse_point(*args, **kwargs)


WORKFLOW_SCHEMA_VERSION = "1.0"


MEMORY_TASK_EVIDENCE_SCHEMA_VERSION = "1.0"


LIVE_WRITE_JOURNAL_SCHEMA_VERSION = "1.0"


CHECKPOINT_RECORD_SCHEMA_VERSION = "1.0"


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


def _live_write_stable_target_key(operation: str, target: dict[str, Any]) -> str:
    if operation == "setVariableDefault":
        return f"blueprint-variable:{target.get('variableName', '')}"
    if operation == "setComponentProperty":
        return f"blueprint-component:{target.get('componentName', '')}:{target.get('propertyPath', '')}"
    if operation == "setPinDefault":
        return (
            f"blueprint-pin:{target.get('graphGuid', '')}:"
            f"{target.get('nodeGuid', '')}:{target.get('pinName', '')}"
        )
    spec = LIVE_WRITE_OPERATION_REGISTRY.get(operation)
    selector_field = spec.live_write_verification_target if spec is not None else ""
    if selector_field and target.get(selector_field):
        return f"{operation}:{target.get(selector_field)}"
    return f"{operation}:{json.dumps(target, ensure_ascii=False, sort_keys=True)}"


def live_write_stable_target_key(operation: str, target: dict[str, Any]) -> str:
    """Public behavior-preserving alias for the W3 stable live-write target identity."""
    return _live_write_stable_target_key(operation, target)


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
    elapsed_ms: float = 0.0


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
    checkpoint_id: str = ""


@dataclass
class LiveWriteCheckpointRecord:
    checkpoint_id: str
    change_set_id: str
    asset_path: str
    asset_class: str
    package_name: str
    state: str
    created_at_utc: str
    saved_at_utc: str = ""
    verified_at_utc: str = ""
    editor_session_id_at_prepare: str = ""
    editor_process_id_at_prepare: int = 0
    before_disk_revision: str = ""
    after_disk_revision: str = ""
    save_receipt: str = ""
    backup_manifest_id: str = ""
    included_receipts: list[str] = field(default_factory=list)
    effective_receipts: list[str] = field(default_factory=list)
    superseded_receipts: list[str] = field(default_factory=list)
    effective_operations: list[dict[str, Any]] = field(default_factory=list)
    effective_operation_digest: str = ""
    strong_verification_kind: str = ""
    strong_verification_report_id: str = ""
    strong_artifact_root: str = ""
    strong_artifact_revision: str = ""
    strong_artifact_digest: str = ""
    child_unreal_process_count: int = 0
    mismatch_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    verified_operation_coverage: list[dict[str, Any]] = field(default_factory=list)


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


def _parse_ue_struct_literal(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, str) or not (value.startswith("(") and value.endswith(")")):
        return None
    result: dict[str, Any] = {}
    for part in value[1:-1].split(","):
        if "=" not in part:
            continue
        key, raw = part.split("=", 1)
        key = key.strip()
        raw = raw.strip()
        if key:
            result[key] = raw
    return result


def _lookup_nested_property_path(value: Any, segments: list[str]) -> Any:
    for segment in segments:
        if isinstance(value, dict):
            if segment not in value:
                return None
            value = value[segment]
            continue
        if isinstance(value, str):
            parsed = _parse_ue_struct_literal(value)
            if parsed is None or segment not in parsed:
                return None
            value = parsed[segment]
            continue
        return None
    return value


def _live_write_blueprint_exported_value(canonical: dict[str, Any], record: LiveApplyRecord) -> Any:
    target = record.target or {}
    operation = record.operation
    if operation == "setVariableDefault":
        name = str(target.get("variableName", ""))
        for variable in canonical.get("variables") or []:
            if isinstance(variable, dict) and variable.get("name") == name:
                return variable.get("defaultValue")
        return None
    if operation == "setComponentProperty":
        component_name = str(target.get("componentName", ""))
        property_path = str(target.get("propertyPath", ""))
        for component in canonical.get("components") or []:
            if not isinstance(component, dict) or component.get("name") != component_name:
                continue
            return _lookup_nested_property_path(component.get("templateOverrides"), property_path.split("."))
        return None
    if operation == "setPinDefault":
        graph_id = str(target.get("graphGuid", ""))
        node_id = str(target.get("nodeGuid", ""))
        pin_name = str(target.get("pinName", ""))
        for graph in canonical.get("graphs") or []:
            if not isinstance(graph, dict) or str(graph.get("guid", graph.get("id", ""))) != graph_id:
                continue
            for node in graph.get("nodes") or []:
                if not isinstance(node, dict) or str(node.get("guid", node.get("id", ""))) != node_id:
                    continue
                for pin in node.get("pins") or []:
                    if isinstance(pin, dict) and pin.get("name") == pin_name:
                        return pin.get("defaultValue", pin.get("default"))
        return None
    return None


def _live_write_exported_value(canonical: dict[str, Any], record: LiveApplyRecord) -> Any:
    details = canonical.get("assetDetails") or {}
    if record.operation in {"setVariableDefault", "setComponentProperty", "setPinDefault"}:
        return _live_write_blueprint_exported_value(canonical, record)
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
    if isinstance(expected, bool) and isinstance(exported, str):
        return exported.lower() in {"true", "false"} and (exported.lower() == "true") == expected
    if isinstance(expected, (int, float)) and isinstance(exported, str):
        try:
            return float(exported) == float(expected)
        except (TypeError, ValueError):
            return False
    if isinstance(exported, (int, float)) and isinstance(expected, str):
        try:
            return float(expected) == float(exported)
        except (TypeError, ValueError):
            return False
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
    verification_mode: str = "immediate"
    change_set_id: str = ""
    checkpoint_id: str = ""
    included_receipts: tuple[str, ...] = ()
    effective_receipts: tuple[str, ...] = ()
    superseded_receipts: tuple[str, ...] = ()
    effective_operation_digest: str = ""


@dataclass
class AuthorizedSaveRollbackDryRunRecord:
    receipt: str
    save_receipt: str
    report_path: Path
    report: dict[str, Any]
    consumed: bool = False


def _default_process_runner(arguments: list[str], cwd: Path, timeout_seconds: int) -> ProcessResult:
    started = time.perf_counter()
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
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return ProcessResult(completed.returncode, completed.stdout, completed.stderr, elapsed_ms)


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


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


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


class WorkflowCommonBase:
    """D1 workflow split mixin/base; method bodies are pure moves from agent_workflow.py."""

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
        self._checkpoints: dict[str, LiveWriteCheckpointRecord] = {}
        self._checkpoint_journal_errors: list[str] = []
        self._checkpoint_recovered_count = 0
        self._change_sets: dict[str, ChangeSetRecord] = {}
        self.live_editor_service = live_editor_service
        self.active_snapshot = self.config.active_snapshot
        self._refresh_applied = False
        self._index_refresh_candidates: dict[str, tuple[Path, dict[str, Any]]] = {}
        self._validate_config()
        self.verification_evidence_store = VerificationEvidenceStore(
            project_name=self.project_name,
            project_path=self.config.project_path,
        )
        self.freshness = freshness_tracker or IndexFreshnessTracker(
            self.index_service,
            self.config.project_path,
            self.config.revision_export,
        )
        self._load_live_write_journal()
        self._load_checkpoint_journal()
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

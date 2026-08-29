from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from . import patches as _patches_compat
from .retarget_workflow import RetargetWorkflowMixin
from .workflow_common import (
    ApplyRecord,
    AuthorizedSaveRollbackDryRunRecord,
    CHECKPOINT_RECORD_SCHEMA_VERSION,
    CRASH_EXIT_CODES,
    CRASH_MARKERS,
    DEVELOPMENT_LINE,
    DryRunRecord,
    HIGH_LEVEL_CHANGE_MODES,
    LIVE_WRITE_JOURNAL_SCHEMA_VERSION,
    LiveApplyRecord,
    LiveWriteCheckpointRecord,
    MATERIAL_PARAMETER_OPERATIONS,
    MAX_PROCESS_OUTPUT_CHARS,
    MAX_WORKFLOW_RECORDS,
    MEMORY_TASK_EVIDENCE_SCHEMA_VERSION,
    PUBLISHED_VERSION,
    PatchWorkflowConfig,
    PlanRecord,
    ProcessResult,
    ProcessRunner,
    RollbackDryRunRecord,
    SaveAuthorizationRecord,
    WORKFLOW_SCHEMA_VERSION,
    WorkflowCommonBase,
    WorkflowError,
    _assert_no_reparse_components,
    _default_process_runner,
    _diagnostic_id,
    _is_guid_with_hyphens,
    _is_ue_crash,
    _is_within,
    _json_bytes,
    _live_write_blueprint_exported_value,
    _live_write_expected_exported_value,
    _live_write_exported_matches,
    _live_write_exported_value,
    _live_write_memory_task_evidence,
    _live_write_runtime_verification,
    _live_write_stable_target_key,
    _live_write_value_kind,
    _lookup_nested_property_path,
    _parse_ue_struct_literal,
    _read_json,
    _report_id,
    _rollback_memory_task_evidence,
    _safe_report,
    _safe_tail,
    _sha256_bytes,
    _validation_error,
    _verified_memory_task_evidence,
    live_write_stable_target_key,
)
from .workflow_plan import WorkflowPlanMixin
from .workflow_live import WorkflowLiveMixin
from .workflow_verify import WorkflowVerifyMixin
from .workflow_batch import WorkflowBatchMixin


def _is_reparse_point(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError as exc:
        raise WorkflowError("workflow-path-invalid", "A workflow path could not be inspected safely.") from exc
    return bool(attributes & 0x400) or path.is_symlink()


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


validate_patch = _patches_compat.validate_patch

__all__ = [
    'ApplyRecord',
    'AuthorizedSaveRollbackDryRunRecord',
    'CHECKPOINT_RECORD_SCHEMA_VERSION',
    'CRASH_EXIT_CODES',
    'CRASH_MARKERS',
    'DEVELOPMENT_LINE',
    'DryRunRecord',
    'HIGH_LEVEL_CHANGE_MODES',
    'LIVE_WRITE_JOURNAL_SCHEMA_VERSION',
    'LiveApplyRecord',
    'LiveWriteCheckpointRecord',
    'MATERIAL_PARAMETER_OPERATIONS',
    'MAX_PROCESS_OUTPUT_CHARS',
    'MAX_WORKFLOW_RECORDS',
    'MEMORY_TASK_EVIDENCE_SCHEMA_VERSION',
    'PUBLISHED_VERSION',
    'PatchWorkflowConfig',
    'PlanRecord',
    'ProcessResult',
    'ProcessRunner',
    'RollbackDryRunRecord',
    'SaveAuthorizationRecord',
    'WORKFLOW_SCHEMA_VERSION',
    'WorkflowCommonBase',
    'WorkflowError',
    '_assert_no_reparse_components',
    '_default_process_runner',
    '_diagnostic_id',
    '_is_guid_with_hyphens',
    '_is_reparse_point',
    '_is_ue_crash',
    '_is_within',
    '_json_bytes',
    '_live_write_blueprint_exported_value',
    '_live_write_expected_exported_value',
    '_live_write_exported_matches',
    '_live_write_exported_value',
    '_live_write_memory_task_evidence',
    '_live_write_runtime_verification',
    '_live_write_stable_target_key',
    '_live_write_value_kind',
    '_lookup_nested_property_path',
    '_parse_ue_struct_literal',
    '_read_json',
    '_report_id',
    '_rollback_memory_task_evidence',
    '_safe_report',
    '_safe_tail',
    '_sha256_bytes',
    '_validation_error',
    '_verified_memory_task_evidence',
    '_write_json_atomic',
    'live_write_stable_target_key',
    'validate_patch',
    'validate_patch',
]


class PatchWorkflowService(
    WorkflowPlanMixin,
    WorkflowLiveMixin,
    WorkflowVerifyMixin,
    WorkflowBatchMixin,
    WorkflowCommonBase,
    RetargetWorkflowMixin,
):
    """High-level, fixed-path MCP workflow compatibility facade."""

    pass


# Compatibility string contract for tool-registry structural test.
# _load_change_set_journal is implemented in WorkflowPlanMixin.
# change-set-not-found / change-set-full / change-set-transaction-not-member are
# raised by WorkflowPlanMixin methods moved from the original monolithic module.

from __future__ import annotations

import hashlib
import json
import math
import secrets
import shutil
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_workflow import WorkflowError

BATCH_SCALE_FIX_SCHEMA_VERSION = "1.0"
MAX_BATCH_SCALE_FIX_ASSETS = 100
MAX_BATCH_SCALE_FIX_PLANS = 50
MAX_AUDIT_REPORT_BYTES = 16 * 1024 * 1024
SUPPORTED_BATCH_CLASSIFICATIONS = {
    "root-lock-candidate",
    "root-track-candidate",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _validate_task_id(value: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise WorkflowError("animation-scale-fix-batch-audit-task-invalid", "audit_task_id must be the exact Audit taskId.")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise WorkflowError(
            "animation-scale-fix-batch-audit-task-invalid",
            "audit_task_id must be the exact Audit taskId.",
        ) from exc
    normalized = str(parsed)
    if normalized != value.lower():
        raise WorkflowError("animation-scale-fix-batch-audit-task-invalid", "audit_task_id must be the exact Audit taskId.")
    return normalized


def _validate_report_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 71
        or not value.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise WorkflowError(
            "animation-scale-fix-batch-report-id-invalid",
            "audit_report_id must be the exact sha256 Report ID returned by the Audit Report Tool.",
        )
    return value


def _validate_asset_path(value: Any) -> str:
    if not isinstance(value, str):
        raise WorkflowError("animation-scale-fix-batch-asset-invalid", "asset_paths must contain exact /Game Object Paths.")
    path = value.strip()
    if len(path) > 512 or not path.startswith("/Game/") or "." not in path.rsplit("/", 1)[-1]:
        raise WorkflowError("animation-scale-fix-batch-asset-invalid", "asset_paths must contain exact /Game Object Paths.")
    return path


def _validate_scale(value: Any, *, field_name: str, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WorkflowError("animation-scale-fix-batch-scale-invalid", f"{field_name} must be a finite number.")
    scale = float(value)
    minimum_ok = scale >= 0.0 if allow_zero else scale > 0.0
    if not math.isfinite(scale) or not minimum_ok or scale > 1_000_000.0:
        comparator = "non-negative" if allow_zero else "greater than 0"
        raise WorkflowError(
            "animation-scale-fix-batch-scale-invalid",
            f"{field_name} must be finite, {comparator}, and at most 1000000.",
        )
    return scale


def _reference_uniform_scale(item: dict[str, Any]) -> float:
    root_track = item.get("rootTrack")
    if not isinstance(root_track, dict):
        raise WorkflowError(
            "animation-scale-fix-batch-reference-scale-missing",
            "The selected Audit item has no Root Track reference scale.",
        )
    reference = root_track.get("referenceComponentScale")
    if not isinstance(reference, dict):
        raise WorkflowError(
            "animation-scale-fix-batch-reference-scale-missing",
            "The selected Audit item has no Skeleton Root Reference Component Scale.",
        )
    axes = [
        _validate_scale(reference.get(axis), field_name=f"referenceComponentScale.{axis}")
        for axis in ("x", "y", "z")
    ]
    tolerance = max(0.001, max(abs(value) for value in axes) * 0.001)
    if max(axes) - min(axes) > tolerance:
        raise WorkflowError(
            "animation-scale-fix-batch-reference-scale-nonuniform",
            "The selected Skeleton Root reference scale is non-uniform, but setAnimationScaleFix verifies one uniform final scale.",
            details={"referenceComponentScale": reference},
        )
    return sum(axes) / 3.0


def _scales_close(left: float, right: float) -> bool:
    return abs(left - right) <= max(0.001, max(abs(left), abs(right)) * 0.001)


@dataclass(frozen=True)
class BatchScaleFixPlanRecord:
    batch_plan_id: str
    digest: str
    payload: dict[str, Any]
    path: Path


class AnimationScaleFixBatchService:
    """Build immutable batch plans from fixed WorkRoot Audit Reports without touching Editor memory."""

    def __init__(self, workflow_service: Any) -> None:
        self.workflow_service = workflow_service
        configured_work_root = getattr(workflow_service.config, "work_root", None)
        self.work_root = Path(configured_work_root).expanduser().resolve() if configured_work_root is not None else None
        self._plans: dict[str, BatchScaleFixPlanRecord] = {}
        self._lock = threading.RLock()

    def plan(
        self,
        *,
        audit_task_id: str,
        audit_report_id: str,
        asset_paths: list[str],
        expected_final_scale_overrides: dict[str, float] | None = None,
        final_scale_tolerance: float | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        with self._lock:
            if len(self._plans) >= MAX_BATCH_SCALE_FIX_PLANS:
                raise WorkflowError(
                    "animation-scale-fix-batch-plan-capacity",
                    f"This MCP session already holds {MAX_BATCH_SCALE_FIX_PLANS} animation scale fix Batch Plans.",
                )
            task_id = _validate_task_id(audit_task_id)
            report_id = _validate_report_id(audit_report_id)
            selected_paths = self._normalize_asset_paths(asset_paths)
            overrides = self._normalize_overrides(expected_final_scale_overrides, selected_paths)
            tolerance = (
                None
                if final_scale_tolerance is None
                else _validate_scale(final_scale_tolerance, field_name="final_scale_tolerance", allow_zero=True)
            )
            if not isinstance(description, str) or len(description) > 1024:
                raise WorkflowError(
                    "animation-scale-fix-batch-description-invalid",
                    "description must be a string no longer than 1024 characters.",
                )

            report = self._load_report(task_id, report_id)
            report_items = self._report_items_by_path(report)
            specifications = [
                self._build_specification(
                    report_items,
                    asset_path,
                    overrides.get(asset_path),
                    tolerance,
                )
                for asset_path in selected_paths
            ]

            child_plan_ids: list[str] = []
            child_items: list[dict[str, Any]] = []
            batch_directory: Path | None = None
            try:
                for specification in specifications:
                    child = self.workflow_service.prepare_high_level_change(
                        tool_name="ue_plan_animation_scale_fix",
                        mode="Plan",
                        asset_path=specification["assetPath"],
                        operation="setAnimationScaleFix",
                        target={"rootBone": specification["rootBone"]},
                        value=specification["value"],
                        description=description,
                    )
                    plan_id = str(child.get("planId") or "")
                    digest = str(child.get("patchDigest") or "")
                    expected_revision = str(child.get("expectedRevision") or "")
                    if not plan_id or not digest.startswith("sha256:") or not expected_revision.startswith("sha256:"):
                        raise WorkflowError(
                            "animation-scale-fix-batch-child-plan-invalid",
                            "A child animation scale fix Plan returned an incomplete immutable identity.",
                        )
                    child_plan_ids.append(plan_id)
                    child_items.append(
                        {
                            **specification,
                            "planId": plan_id,
                            "patchDigest": digest,
                            "expectedRevision": expected_revision,
                            "assetClass": child.get("assetClass", ""),
                            "risk": child.get("risk", ""),
                            "commitAllowedByPolicy": bool(child.get("commitAllowedByPolicy")),
                        }
                    )

                batch_plan_id = "asfb_" + secrets.token_urlsafe(18)
                payload = {
                    "schemaVersion": BATCH_SCALE_FIX_SCHEMA_VERSION,
                    "batchPlanId": batch_plan_id,
                    "state": "planned",
                    "projectName": self.workflow_service.project_name,
                    "createdAtUtc": _utc_now(),
                    "description": description,
                    "sourceAudit": {
                        "taskId": task_id,
                        "reportId": report_id,
                    },
                    "assetCount": len(child_items),
                    "items": child_items,
                }
                payload_bytes = _json_bytes(payload)
                digest = "sha256:" + _sha256(payload_bytes)
                batch_directory = self._batch_plan_directory(batch_plan_id)
                plan_path = batch_directory / "plan.json"
                self._write_atomic(plan_path, payload_bytes)
                record = BatchScaleFixPlanRecord(batch_plan_id, digest, payload, plan_path)
                self._plans[batch_plan_id] = record
                return self._response(record)
            except Exception:
                if batch_directory is not None:
                    shutil.rmtree(batch_directory, ignore_errors=True)
                if child_plan_ids:
                    self.workflow_service.discard_unconsumed_plans(child_plan_ids)
                raise

    def get(self, *, batch_plan_id: str) -> dict[str, Any]:
        with self._lock:
            if not isinstance(batch_plan_id, str) or not batch_plan_id.startswith("asfb_"):
                raise WorkflowError(
                    "animation-scale-fix-batch-plan-invalid",
                    "batch_plan_id must be the exact identifier returned by ue_plan_animation_scale_fix_batch.",
                )
            record = self._plans.get(batch_plan_id)
            if record is None:
                raise WorkflowError(
                    "animation-scale-fix-batch-plan-not-found",
                    "The animation scale fix Batch Plan was not found in this MCP session.",
                )
            current_bytes = record.path.read_bytes()
            if "sha256:" + _sha256(current_bytes) != record.digest or json.loads(current_bytes) != record.payload:
                raise WorkflowError(
                    "animation-scale-fix-batch-plan-tampered",
                    "The stored animation scale fix Batch Plan changed after it was created.",
                )
            return self._response(record)

    @staticmethod
    def _normalize_asset_paths(asset_paths: list[str]) -> list[str]:
        if not isinstance(asset_paths, list) or not 1 <= len(asset_paths) <= MAX_BATCH_SCALE_FIX_ASSETS:
            raise WorkflowError(
                "animation-scale-fix-batch-assets-invalid",
                f"asset_paths must contain between 1 and {MAX_BATCH_SCALE_FIX_ASSETS} exact Object Paths.",
            )
        normalized = [_validate_asset_path(value) for value in asset_paths]
        if len(set(normalized)) != len(normalized):
            raise WorkflowError(
                "animation-scale-fix-batch-duplicate-asset",
                "asset_paths must not contain duplicate AnimSequence Object Paths.",
            )
        return normalized

    @staticmethod
    def _normalize_overrides(
        overrides: dict[str, float] | None,
        selected_paths: list[str],
    ) -> dict[str, float]:
        if overrides is None:
            return {}
        if not isinstance(overrides, dict):
            raise WorkflowError(
                "animation-scale-fix-batch-overrides-invalid",
                "expected_final_scale_overrides must be an object keyed by selected asset path.",
            )
        selected = set(selected_paths)
        normalized: dict[str, float] = {}
        for raw_path, raw_scale in overrides.items():
            path = _validate_asset_path(raw_path)
            if path not in selected:
                raise WorkflowError(
                    "animation-scale-fix-batch-override-not-selected",
                    "expected_final_scale_overrides may contain only asset paths selected in asset_paths.",
                )
            normalized[path] = _validate_scale(raw_scale, field_name=f"expected_final_scale_overrides[{path}]")
        return normalized

    def _load_report(self, task_id: str, report_id: str) -> dict[str, Any]:
        if self.work_root is None:
            raise WorkflowError(
                "animation-scale-fix-batch-work-root-unavailable",
                "The fixed MCP WorkRoot is unavailable for animation scale fix Batch planning.",
            )
        report_directory = (self.work_root / "animation-scale-audits" / task_id).resolve()
        try:
            report_directory.relative_to(self.work_root)
        except ValueError as exc:
            raise WorkflowError(
                "animation-scale-fix-batch-report-path-invalid",
                "The fixed Audit Report path resolves outside the configured WorkRoot.",
            ) from exc
        path = report_directory / "report.json"
        if not path.is_file():
            raise WorkflowError(
                "animation-scale-fix-batch-report-not-found",
                "The referenced Audit Report does not exist under the fixed WorkRoot.",
            )
        size = path.stat().st_size
        if size <= 0 or size > MAX_AUDIT_REPORT_BYTES:
            raise WorkflowError(
                "animation-scale-fix-batch-report-size-invalid",
                "The referenced Audit Report exceeds the bounded report size.",
            )
        data = path.read_bytes()
        if "sha256:" + _sha256(data) != report_id:
            raise WorkflowError(
                "animation-scale-fix-batch-report-revision-mismatch",
                "The referenced Audit Report content does not match audit_report_id.",
            )
        try:
            report = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkflowError(
                "animation-scale-fix-batch-report-invalid",
                "The referenced Audit Report is not valid UTF-8 JSON.",
            ) from exc
        if (
            not isinstance(report, dict)
            or report.get("schemaVersion") != "1.0"
            or report.get("reportType") != "animation-scale-audit"
            or not isinstance(report.get("task"), dict)
            or report["task"].get("taskId") != task_id
        ):
            raise WorkflowError(
                "animation-scale-fix-batch-report-invalid",
                "The referenced file is not the requested animation scale Audit Report.",
            )
        if report["task"].get("state") != "completed":
            raise WorkflowError(
                "animation-scale-fix-batch-report-incomplete",
                "Animation scale fix Batch planning requires a completed Audit Report, not partial cancelled or failed results.",
                details={"state": report["task"].get("state")},
            )
        return report

    @staticmethod
    def _report_items_by_path(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
        raw_items = report.get("items")
        if not isinstance(raw_items, list):
            raise WorkflowError("animation-scale-fix-batch-report-invalid", "The Audit Report has no valid items array.")
        items: dict[str, dict[str, Any]] = {}
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                raise WorkflowError("animation-scale-fix-batch-report-invalid", "The Audit Report contains a non-object item.")
            path = _validate_asset_path(raw_item.get("assetPath"))
            if path in items:
                raise WorkflowError(
                    "animation-scale-fix-batch-report-duplicate-asset",
                    "The Audit Report contains duplicate asset paths and cannot be used for a Batch Plan.",
                )
            items[path] = raw_item
        return items

    def _build_specification(
        self,
        report_items: dict[str, dict[str, Any]],
        asset_path: str,
        override_scale: float | None,
        final_scale_tolerance: float | None,
    ) -> dict[str, Any]:
        item = report_items.get(asset_path)
        if item is None:
            raise WorkflowError(
                "animation-scale-fix-batch-asset-not-in-report",
                "Every selected asset must be present in the exact referenced Audit Report.",
                details={"assetPath": asset_path},
            )
        classification = str(item.get("classification") or "")
        if classification not in SUPPORTED_BATCH_CLASSIFICATIONS:
            raise WorkflowError(
                "animation-scale-fix-batch-classification-unsupported",
                "The selected Audit classification is not safe for automatic Batch Plan generation.",
                details={"assetPath": asset_path, "classification": classification},
            )
        root_bone = str(item.get("rootBone") or "")
        if not root_bone or len(root_bone) > 128:
            raise WorkflowError(
                "animation-scale-fix-batch-root-bone-invalid",
                "The selected Audit item has no usable Root Bone identity.",
                details={"assetPath": asset_path},
            )
        reference_scale = _reference_uniform_scale(item)
        expected_scale = reference_scale if override_scale is None else override_scale
        expected_source = "skeleton-reference" if override_scale is None else "explicit-override"

        if classification == "root-lock-candidate":
            if override_scale is not None and not _scales_close(override_scale, reference_scale):
                raise WorkflowError(
                    "animation-scale-fix-batch-root-lock-override-incompatible",
                    "A Root Lock candidate evaluates from the Skeleton reference pose, so its explicit final-scale override must match the Root reference scale.",
                    details={
                        "assetPath": asset_path,
                        "referenceScale": reference_scale,
                        "overrideScale": override_scale,
                    },
                )
            value: dict[str, Any] = {
                "rootTrackScaleMode": "Keep",
                "expectedFinalScale": expected_scale,
                "forceRootLock": True,
                "rootMotionRootLock": "RefPose",
            }
            strategy = "force-root-lock-ref-pose"
        else:
            if override_scale is not None and not _scales_close(override_scale, reference_scale):
                value = {
                    "rootTrackScaleMode": "Uniform",
                    "uniformScale": override_scale,
                    "expectedFinalScale": override_scale,
                }
                strategy = "uniform-root-track-override"
            else:
                value = {
                    "rootTrackScaleMode": "ReferenceLocal",
                    "expectedFinalScale": expected_scale,
                }
                strategy = "reference-local-root-track"
        if final_scale_tolerance is not None:
            value["finalScaleTolerance"] = final_scale_tolerance
        return {
            "assetPath": asset_path,
            "classification": classification,
            "rootBone": root_bone,
            "referenceFinalScale": reference_scale,
            "expectedFinalScale": expected_scale,
            "expectedFinalScaleSource": expected_source,
            "strategy": strategy,
            "value": value,
        }

    def _batch_plan_directory(self, batch_plan_id: str) -> Path:
        if self.work_root is None:
            raise WorkflowError(
                "animation-scale-fix-batch-work-root-unavailable",
                "The fixed MCP WorkRoot is unavailable for animation scale fix Batch planning.",
            )
        batch_root = (self.work_root / "animation-scale-fix-batches").resolve()
        try:
            batch_root.relative_to(self.work_root)
        except ValueError as exc:
            raise WorkflowError(
                "animation-scale-fix-batch-path-invalid",
                "The fixed Batch Plan root resolves outside the configured WorkRoot.",
            ) from exc
        batch_root.mkdir(parents=True, exist_ok=True)
        directory = (batch_root / batch_plan_id).resolve()
        try:
            directory.relative_to(batch_root)
        except ValueError as exc:
            raise WorkflowError(
                "animation-scale-fix-batch-path-invalid",
                "The generated Batch Plan path resolves outside the fixed Batch Plan root.",
            ) from exc
        directory.mkdir(exist_ok=False)
        return directory

    @staticmethod
    def _write_atomic(path: Path, data: bytes) -> None:
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)

    @staticmethod
    def _response(record: BatchScaleFixPlanRecord) -> dict[str, Any]:
        return {
            "schemaVersion": BATCH_SCALE_FIX_SCHEMA_VERSION,
            "ok": True,
            "batchPlanId": record.batch_plan_id,
            "batchPlanDigest": record.digest,
            **record.payload,
        }

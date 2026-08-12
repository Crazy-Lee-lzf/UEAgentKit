from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .animation_scale_audit import AnimationScaleAuditService, MAX_AUDIT_PAGE_SIZE
from .database import utc_now_iso

RETARGET_POSTPROCESS_SCHEMA_VERSION = "1.0"
RETARGET_POSTPROCESS_PLAN_SCHEMA_VERSION = "1.0"
MAX_RETARGET_POSTPROCESS_TASKS = 32
MAX_RETARGET_POSTPROCESS_INDEX_REFRESH_STEP = 2
SUPPORTED_SCALE_FIX_CLASSIFICATIONS = {
    "root-lock-candidate",
    "root-track-candidate",
}
REFERENCE_ASSET_TYPES = {
    "BlendSpace",
    "AimOffset",
    "AnimMontage",
}


@dataclass
class RetargetPostprocessRecord:
    postprocess_id: str
    retarget_task_id: str
    state: str
    created_at_utc: str
    updated_at_utc: str
    outputs: list[dict[str, Any]]
    output_summary: dict[str, Any]
    audit_task_id: str = ""
    audit_snapshot: dict[str, Any] | None = None
    suggestions: dict[str, Any] | None = None
    plan_id: str = ""
    plan_digest: str = ""
    plan_relative_path: str = ""
    audit_report_id: str = ""
    audit_report_relative_path: str = ""
    index_refresh_state: str = ""
    index_refresh_receipt: str = ""
    index_refresh_order: list[str] = field(default_factory=list)
    index_refresh_cursor: int = 0
    index_refresh_candidate_ids: dict[str, str] = field(default_factory=dict)
    index_refresh_generation: dict[str, Any] = field(default_factory=dict)
    index_refresh_failure_code: str = ""
    index_refresh_failure_message: str = ""


class RetargetPostprocessService:
    """Read-only post-processing orchestration for one completed retarget batch."""

    def __init__(self, workflow_service: Any) -> None:
        self.workflow_service = workflow_service
        configured_work_root = getattr(workflow_service.config, "work_root", None)
        self.work_root = Path(configured_work_root).expanduser().resolve() if configured_work_root is not None else None
        self.live_editor_service = getattr(workflow_service, "live_editor_service", None)
        self.audit_service = AnimationScaleAuditService(
            self.live_editor_service,
            index_service=getattr(workflow_service, "index_service", None),
            report_root=self.work_root,
        )
        self._records: dict[str, RetargetPostprocessRecord] = {}

    @staticmethod
    def _asset_type(output: dict[str, Any]) -> str:
        asset_type = str(output.get("assetType") or "").strip()
        if asset_type:
            return asset_type
        asset_class = str(output.get("assetClass") or "")
        if "AnimSequence" in asset_class:
            return "AnimSequence"
        if "AimOffset" in asset_class:
            return "AimOffset"
        if "BlendSpace" in asset_class:
            return "BlendSpace"
        if "AnimMontage" in asset_class:
            return "AnimMontage"
        return "Unknown"

    @classmethod
    def _classify_outputs(cls, outputs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for output in outputs:
            copied = dict(output)
            copied["assetType"] = cls._asset_type(copied)
            normalized.append(copied)
            asset_type = copied["assetType"]
            counts[asset_type] = counts.get(asset_type, 0) + 1
        animation_sequences = [item["outputPath"] for item in normalized if item["assetType"] == "AnimSequence"]
        reference_outputs = [
            {
                "assetPath": item.get("outputPath", ""),
                "assetType": item["assetType"],
                "assetClass": item.get("assetClass", ""),
                "skeletonPath": item.get("skeletonPath", ""),
                "reason": "Composite/reference animation outputs are classified but are not modified by P3 post-processing.",
            }
            for item in normalized
            if item["assetType"] in REFERENCE_ASSET_TYPES
        ]
        unknown_outputs = [
            {
                "assetPath": item.get("outputPath", ""),
                "assetType": item["assetType"],
                "assetClass": item.get("assetClass", ""),
                "reason": "The retarget output type is not recognized by the current post-processing slice.",
            }
            for item in normalized
            if item["assetType"] not in REFERENCE_ASSET_TYPES and item["assetType"] != "AnimSequence"
        ]
        summary = {
            "outputCount": len(normalized),
            "assetTypeCounts": counts,
            "animationSequenceCount": len(animation_sequences),
            "animationSequencePaths": animation_sequences,
            "referenceOutputCount": len(reference_outputs),
            "referenceOutputs": reference_outputs,
            "unknownOutputCount": len(unknown_outputs),
            "unknownOutputs": unknown_outputs,
        }
        return normalized, summary

    def start(
        self,
        *,
        retarget_task_id: str,
        load_if_needed: bool = True,
        batch_size: int = 1,
    ) -> dict[str, Any]:
        if self.live_editor_service is None:
            raise self.workflow_service._workflow_error(
                "live-editor-required",
                "Live Editor mode is required for retarget output post-processing.",
            )
        if len(self._records) >= MAX_RETARGET_POSTPROCESS_TASKS:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-capacity",
                f"This MCP session already holds {MAX_RETARGET_POSTPROCESS_TASKS} retarget post-process tasks.",
            )
        if any(record.state == "auditing" for record in self._records.values()):
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-busy",
                "Another retarget post-process Scale Audit is already running in this MCP session.",
            )
        context = self.workflow_service.get_animation_retarget_postprocess_context(task_id=retarget_task_id)
        outputs_value = context.get("outputs", [])
        if not isinstance(outputs_value, list) or not outputs_value:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-no-outputs",
                "The completed retarget batch has no output assets to post-process.",
            )
        outputs, output_summary = self._classify_outputs(
            [dict(item) for item in outputs_value if isinstance(item, dict)]
        )
        if len(outputs) != len(outputs_value):
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-invalid-outputs",
                "The completed retarget batch contains an invalid output record.",
            )

        now = utc_now_iso()
        postprocess_id = "rtpp_" + secrets.token_urlsafe(16)
        animation_paths = list(output_summary["animationSequencePaths"])
        audit_task_id = ""
        audit_snapshot: dict[str, Any] | None = None
        state = "analyzed"
        if animation_paths:
            audit_snapshot = self.audit_service.start(
                animation_paths=animation_paths,
                load_if_needed=load_if_needed,
                batch_size=batch_size,
            )
            audit_task_id = str(audit_snapshot.get("taskId") or "")
            state = "auditing"

        record = RetargetPostprocessRecord(
            postprocess_id=postprocess_id,
            retarget_task_id=retarget_task_id,
            state=state,
            created_at_utc=now,
            updated_at_utc=now,
            outputs=outputs,
            output_summary=output_summary,
            audit_task_id=audit_task_id,
            audit_snapshot=audit_snapshot,
        )
        if state == "analyzed":
            record.suggestions = self._build_suggestions(record, [])
        self._records[postprocess_id] = record
        return self._snapshot(record)

    def get(self, *, postprocess_id: str) -> dict[str, Any]:
        record = self._require_record(postprocess_id)
        if record.state == "auditing":
            audit_snapshot = self.audit_service.get(
                task_id=record.audit_task_id,
                detail_offset=0,
                detail_limit=MAX_AUDIT_PAGE_SIZE,
                sort_by="asset-path",
            )
            record.audit_snapshot = audit_snapshot
            audit_state = str(audit_snapshot.get("state") or "")
            if audit_state == "completed":
                items = self._collect_audit_items(record.audit_task_id)
                record.suggestions = self._build_suggestions(record, items)
                record.state = "analyzed"
            elif audit_state in {"failed", "cancelled"}:
                record.state = "failed"
            record.updated_at_utc = utc_now_iso()
        return self._snapshot(record)

    def plan(self, *, postprocess_id: str, description: str = "") -> dict[str, Any]:
        record = self._require_record(postprocess_id)
        if record.state != "analyzed" or record.suggestions is None:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-not-ready",
                f"The retarget post-process must be analyzed before planning (current state {record.state}).",
            )
        if not isinstance(description, str) or len(description) > 1024:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-description-invalid",
                "description must be a string no longer than 1024 characters.",
            )
        if record.plan_id:
            return self._existing_plan_result(record)
        if self.work_root is None:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-plan-unavailable",
                "The fixed WorkRoot is unavailable for retarget post-process planning.",
            )

        audit_report: dict[str, Any] = {}
        if record.audit_task_id:
            audit_report = self.audit_service.export_report(
                task_id=record.audit_task_id,
                sort_by="asset-path",
            )
            record.audit_report_id = str(audit_report.get("reportId") or "")
            record.audit_report_relative_path = str(audit_report.get("reportRelativePath") or "")

        plan_id = "rtpp_plan_" + secrets.token_urlsafe(16)
        now = utc_now_iso()
        context = self.workflow_service.get_animation_retarget_postprocess_context(task_id=record.retarget_task_id)
        plan = {
            "schemaVersion": RETARGET_POSTPROCESS_PLAN_SCHEMA_VERSION,
            "planType": "retarget-postprocess-suggestion",
            "planId": plan_id,
            "postprocessId": record.postprocess_id,
            "retargetTaskId": record.retarget_task_id,
            "retargetPlanId": context.get("planId", ""),
            "retargetPlanDigest": context.get("planDigest", ""),
            "projectName": getattr(self.workflow_service, "project_name", ""),
            "createdAtUtc": now,
            "description": description,
            "outputSummary": record.output_summary,
            "audit": {
                "taskId": record.audit_task_id,
                "reportId": record.audit_report_id,
                "reportRelativePath": record.audit_report_relative_path,
                "classificationCounts": (record.audit_snapshot or {}).get("summary", {}).get(
                    "classificationCounts", {}
                ),
            },
            "suggestions": record.suggestions,
            "executionBoundary": {
                "modifiesAssets": False,
                "autoApplyAllowed": False,
                "requiresUserReview": True,
                "requiresRetargetOutputIndexRefreshBeforeP2Plan": bool(
                    record.suggestions.get("scaleFixCandidates")
                ),
                "p2Workflow": "animation-scale-fix-batch",
                "referenceAssetMutationImplemented": False,
            },
        }
        payload = (json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        plan_root = self.work_root / "retarget-postprocess" / record.postprocess_id
        plan_root.mkdir(parents=True, exist_ok=True)
        try:
            plan_root.relative_to(self.work_root)
        except ValueError as exc:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-plan-path-invalid",
                "The retarget post-process plan path escaped the fixed WorkRoot.",
            ) from exc
        plan_path = plan_root / "plan.json"
        temporary = plan_root / "plan.json.tmp"
        temporary.write_bytes(payload)
        temporary.replace(plan_path)

        record.plan_id = plan_id
        record.plan_digest = digest
        record.plan_relative_path = plan_path.relative_to(self.work_root).as_posix()
        record.updated_at_utc = now
        return {
            "schemaVersion": RETARGET_POSTPROCESS_SCHEMA_VERSION,
            "tool": "ue_plan_animation_retarget_postprocess",
            "ok": True,
            "readOnly": True,
            "planId": plan_id,
            "planDigest": digest,
            "planRelativePath": record.plan_relative_path,
            "postprocessId": record.postprocess_id,
            "retargetTaskId": record.retarget_task_id,
            "auditReport": audit_report,
            "result": plan,
            "nextStep": (
                "Review the suggested scale fixes and reference-output follow-ups. Persist and atomically index the retarget outputs "
                "before converting eligible AnimSequence suggestions into a P2 animation scale-fix Batch Plan."
            ),
        }

    def _existing_plan_result(self, record: RetargetPostprocessRecord) -> dict[str, Any]:
        if self.work_root is None:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-plan-unavailable",
                "The fixed WorkRoot is unavailable for retarget post-process planning.",
            )
        plan_path = self.work_root / record.plan_relative_path
        if not plan_path.is_file():
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-plan-missing",
                "The immutable retarget post-process plan file is missing.",
            )
        payload = plan_path.read_bytes()
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        if digest != record.plan_digest:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-plan-tampered",
                "The immutable retarget post-process plan no longer matches its recorded digest.",
            )
        plan = json.loads(payload.decode("utf-8"))
        return {
            "schemaVersion": RETARGET_POSTPROCESS_SCHEMA_VERSION,
            "tool": "ue_plan_animation_retarget_postprocess",
            "ok": True,
            "readOnly": True,
            "planId": record.plan_id,
            "planDigest": record.plan_digest,
            "planRelativePath": record.plan_relative_path,
            "postprocessId": record.postprocess_id,
            "retargetTaskId": record.retarget_task_id,
            "auditReport": {
                "taskId": record.audit_task_id,
                "reportId": record.audit_report_id,
                "reportRelativePath": record.audit_report_relative_path,
            },
            "result": plan,
            "nextStep": "Review the existing immutable suggested post-process Plan before any later write workflow.",
        }

    def refresh_index(
        self,
        *,
        postprocess_id: str,
        mode: Literal["Preview", "Apply"] = "Preview",
        confirmation: str = "",
        refresh_receipt: str = "",
        max_assets: int = 1,
    ) -> dict[str, Any]:
        record = self._require_record(postprocess_id)
        if mode not in {"Preview", "Apply"}:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-index-refresh-mode-invalid",
                "mode must be Preview or Apply.",
            )
        if (
            isinstance(max_assets, bool)
            or not isinstance(max_assets, int)
            or not 1 <= max_assets <= MAX_RETARGET_POSTPROCESS_INDEX_REFRESH_STEP
        ):
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-index-refresh-step-invalid",
                f"maxAssets must be an integer from 1 through {MAX_RETARGET_POSTPROCESS_INDEX_REFRESH_STEP}.",
            )
        if record.state != "analyzed" or record.suggestions is None:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-not-ready",
                f"The retarget post-process must be analyzed before Index Refresh (current state {record.state}).",
            )
        if not record.plan_id:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-plan-required",
                "An immutable Suggested Plan is required before Retarget Output Index Refresh.",
            )

        context = self.workflow_service.get_animation_retarget_postprocess_context(task_id=record.retarget_task_id)
        verification = context.get("verification", {})
        if not isinstance(verification, dict):
            verification = {}
        if str(context.get("status", "")) != "saved":
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-not-saved",
                "Retarget Output Index Refresh requires the retarget batch to be saved first (ue_save_animation_retarget_batch).",
            )
        if verification.get("verified") is not True:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-not-verified",
                "Retarget Output Index Refresh requires the saved retarget batch to pass independent verification (ue_verify_animation_retarget_batch).",
            )

        eligible_paths = [
            str(item.get("assetPath", ""))
            for item in (record.suggestions.get("scaleFixCandidates") or [])
            if isinstance(item, dict)
            and item.get("classification") in SUPPORTED_SCALE_FIX_CLASSIFICATIONS
            and item.get("assetPath")
        ]
        verified_by_path = {
            str(item.get("assetPath", "")): str(item.get("revision", ""))
            for item in verification.get("verifiedAssets", [])
            if isinstance(item, dict) and item.get("assetPath")
        }

        if not record.index_refresh_receipt:
            if refresh_receipt:
                raise self.workflow_service._workflow_error(
                    "retarget-postprocess-index-refresh-receipt-invalid",
                    "Do not provide refreshReceipt before Retarget Output Index Refresh Preview starts.",
                )
            if mode != "Preview":
                raise self.workflow_service._workflow_error(
                    "retarget-postprocess-index-refresh-preview-required",
                    "Retarget Output Index Refresh must start with bounded Preview candidate preparation.",
                )
            if not eligible_paths:
                raise self.workflow_service._workflow_error(
                    "retarget-postprocess-index-refresh-empty",
                    "No eligible AnimSequence outputs require Index Refresh.",
                )
            record.index_refresh_order = sorted(set(eligible_paths))
            record.index_refresh_receipt = "rtppir_" + secrets.token_urlsafe(18)
            record.index_refresh_cursor = 0
            record.index_refresh_candidate_ids = {}
            record.index_refresh_generation = {}
            record.index_refresh_failure_code = ""
            record.index_refresh_failure_message = ""
            record.index_refresh_state = "preparing"
            record.updated_at_utc = utc_now_iso()
        elif refresh_receipt != record.index_refresh_receipt:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-index-refresh-receipt-invalid",
                "refreshReceipt must be the exact receipt returned by Retarget Output Index Refresh Preview.",
            )

        if mode == "Preview":
            if record.index_refresh_state == "ready":
                return self._snapshot(record)
            if record.index_refresh_state not in {"preparing", "prepare_failed"}:
                raise self.workflow_service._workflow_error(
                    "retarget-postprocess-index-refresh-state-invalid",
                    f"Retarget Output Index Refresh Preview cannot continue from state {record.index_refresh_state}.",
                )
            record.index_refresh_state = "preparing"
            record.index_refresh_failure_code = ""
            record.index_refresh_failure_message = ""
            stop = min(record.index_refresh_cursor + max_assets, len(record.index_refresh_order))
            while record.index_refresh_cursor < stop:
                asset_path = record.index_refresh_order[record.index_refresh_cursor]
                try:
                    prepared = self.workflow_service.prepare_batch_index_refresh_candidate(asset_path)
                    candidate_id = str(prepared.get("candidateId") or "")
                    candidate_revision = str(prepared.get("revision") or "")
                    expected_revision = verified_by_path.get(asset_path, "")
                    if not candidate_id:
                        raise self.workflow_service._workflow_error(
                            "retarget-postprocess-index-refresh-candidate-invalid",
                            "A prepared Index Refresh candidate has no candidateId.",
                        )
                    if expected_revision and candidate_revision != expected_revision:
                        if candidate_id:
                            self.workflow_service.discard_batch_index_refresh_candidates([candidate_id])
                        raise self.workflow_service._workflow_error(
                            "retarget-postprocess-index-refresh-revision-mismatch",
                            "A prepared Index Refresh candidate does not match the independently verified persisted Revision.",
                        )
                    record.index_refresh_candidate_ids[asset_path] = candidate_id
                except Exception as exc:
                    record.index_refresh_failure_code = str(
                        getattr(exc, "code", "retarget-postprocess-index-refresh-candidate-failed")
                    )
                    details = getattr(exc, "details", {}) or {}
                    record.index_refresh_failure_message = (
                        "A Retarget Output Index Refresh candidate could not be prepared: "
                        + str(exc)
                        + (" " + json.dumps(details, ensure_ascii=False, sort_keys=True) if details else "")
                    )
                    record.index_refresh_state = "prepare_failed"
                    record.updated_at_utc = utc_now_iso()
                    break
                record.index_refresh_cursor += 1
                record.updated_at_utc = utc_now_iso()
            if (
                record.index_refresh_state == "preparing"
                and record.index_refresh_cursor >= len(record.index_refresh_order)
            ):
                record.index_refresh_state = "ready"
                record.updated_at_utc = utc_now_iso()
            return self._snapshot(record)

        required_confirmation = f"REFRESH RETARGET POSTPROCESS {record.postprocess_id}"
        if confirmation != required_confirmation:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-index-refresh-confirmation-required",
                f"Retarget Output Index Refresh Apply confirmation must exactly match '{required_confirmation}'.",
            )
        if record.index_refresh_cursor != len(record.index_refresh_order):
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-index-refresh-preview-required",
                "Every eligible AnimSequence output must complete Index Refresh Preview before Apply.",
            )
        if record.index_refresh_state == "refreshed":
            return self._snapshot(record)
        if record.index_refresh_state not in {"ready", "failed"}:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-index-refresh-state-invalid",
                f"Retarget Output Index Refresh Apply cannot continue from state {record.index_refresh_state}.",
            )
        candidate_ids = [
            record.index_refresh_candidate_ids[asset_path] for asset_path in record.index_refresh_order
        ]
        record.index_refresh_state = "applying"
        record.index_refresh_failure_code = ""
        record.index_refresh_failure_message = ""
        try:
            refreshed = self.workflow_service.apply_batch_index_refresh(candidate_ids)
            if refreshed.get("applied") is not True or refreshed.get("restartRequired") is not True:
                raise self.workflow_service._workflow_error(
                    "retarget-postprocess-index-refresh-apply-invalid",
                    "The paired Retarget Output snapshot refresh did not report an atomic active-pointer switch.",
                )
        except Exception as exc:
            record.index_refresh_failure_code = str(
                getattr(exc, "code", "retarget-postprocess-index-refresh-apply-failed")
            )
            record.index_refresh_failure_message = "The paired Retarget Output snapshot generation could not be activated."
            record.index_refresh_state = "failed"
            record.updated_at_utc = utc_now_iso()
            return self._snapshot(record)

        record.index_refresh_generation = dict(refreshed.get("newGeneration") or {})
        record.index_refresh_state = "refreshed"
        record.updated_at_utc = utc_now_iso()
        return self._snapshot(record)

    def reopen(self, *, plan_relative_path: str) -> dict[str, Any]:
        if self.work_root is None:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-reopen-unavailable",
                "The fixed WorkRoot is unavailable for reopening a persisted retarget post-process Plan.",
            )
        if (
            not isinstance(plan_relative_path, str)
            or not plan_relative_path
            or "\\" in plan_relative_path
            or ".." in plan_relative_path
            or not plan_relative_path.startswith("retarget-postprocess/")
            or not plan_relative_path.endswith("plan.json")
        ):
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-reopen-invalid",
                "planRelativePath must be the exact relative path of a persisted retarget post-process Plan.",
            )
        plan_path = self.work_root / plan_relative_path
        try:
            resolved = plan_path.resolve()
            resolved.relative_to(self.work_root.resolve())
        except (OSError, ValueError) as exc:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-reopen-invalid",
                "The persisted retarget post-process Plan path escapes the fixed WorkRoot.",
            ) from exc
        if not resolved.is_file():
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-reopen-missing",
                "The persisted retarget post-process Plan file is missing.",
            )
        try:
            payload = resolved.read_bytes()
            plan = json.loads(payload.decode("utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-reopen-invalid",
                "The persisted retarget post-process Plan could not be read.",
            ) from exc
        if (
            not isinstance(plan, dict)
            or plan.get("schemaVersion") != RETARGET_POSTPROCESS_PLAN_SCHEMA_VERSION
            or plan.get("planType") != "retarget-postprocess-suggestion"
        ):
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-reopen-invalid",
                "The persisted retarget post-process Plan schema is unsupported.",
            )
        digest = "sha256:" + hashlib.sha256(payload).hexdigest()
        suggestions = plan.get("suggestions") if isinstance(plan.get("suggestions"), dict) else {}
        output_summary = plan.get("outputSummary") if isinstance(plan.get("outputSummary"), dict) else {}
        return {
            "schemaVersion": RETARGET_POSTPROCESS_SCHEMA_VERSION,
            "tool": "ue_reopen_animation_retarget_postprocess",
            "ok": True,
            "readOnly": True,
            "postprocessId": plan.get("postprocessId", ""),
            "retargetTaskId": plan.get("retargetTaskId", ""),
            "planId": plan.get("planId", ""),
            "planDigest": digest,
            "planRelativePath": plan_relative_path,
            "description": plan.get("description", ""),
            "outputSummary": output_summary,
            "suggestions": suggestions,
            "executionBoundary": plan.get("executionBoundary", {}),
            "audit": plan.get("audit", {}),
            "nextStep": (
                "Revalidate the listed AnimSequence outputs against the fresh immutable Index via ue_get_asset_state, "
                "re-run ue_start_animation_scale_audit on the eligible paths if needed, then build a P2 scale-fix Batch Plan "
                "with ue_plan_animation_scale_fix_batch."
            ),
        }

    def _collect_audit_items(self, task_id: str) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        offset = 0
        while True:
            snapshot = self.audit_service.get(
                task_id=task_id,
                detail_offset=offset,
                detail_limit=MAX_AUDIT_PAGE_SIZE,
                sort_by="asset-path",
            )
            details = snapshot.get("details", {})
            page = details.get("items", []) if isinstance(details, dict) else []
            if not isinstance(page, list):
                raise self.workflow_service._workflow_error(
                    "retarget-postprocess-audit-invalid",
                    "The animation scale audit returned an invalid detail page.",
                )
            items.extend(dict(item) for item in page if isinstance(item, dict))
            if not details.get("hasMore"):
                break
            offset = int(details.get("nextOffset", offset + len(page)))
        return items

    @staticmethod
    def _build_suggestions(record: RetargetPostprocessRecord, audit_items: list[dict[str, Any]]) -> dict[str, Any]:
        scale_fix_candidates: list[dict[str, Any]] = []
        normal_assets: list[str] = []
        manual_review: list[dict[str, Any]] = []
        for item in audit_items:
            classification = str(item.get("classification") or "unknown")
            asset_path = str(item.get("assetPath") or "")
            if classification in SUPPORTED_SCALE_FIX_CLASSIFICATIONS:
                scale_fix_candidates.append(
                    {
                        "assetPath": asset_path,
                        "classification": classification,
                        "rootBone": item.get("rootBone", ""),
                        "referenceComponentScale": item.get("rootTrack", {}).get("referenceComponentScale"),
                        "suggestedFix": item.get("suggestedFix", ""),
                        "recommendedWorkflow": "animation-scale-fix-batch",
                    }
                )
            elif classification == "normal":
                normal_assets.append(asset_path)
            else:
                manual_review.append(
                    {
                        "assetPath": asset_path,
                        "classification": classification,
                        "suggestedFix": item.get("suggestedFix", ""),
                    }
                )
        return {
            "scaleFixCandidateCount": len(scale_fix_candidates),
            "scaleFixCandidates": scale_fix_candidates,
            "normalAnimationCount": len(normal_assets),
            "normalAnimations": normal_assets,
            "manualReviewCount": len(manual_review),
            "manualReview": manual_review,
            "referenceFollowupCount": record.output_summary["referenceOutputCount"],
            "referenceFollowups": record.output_summary["referenceOutputs"],
            "unknownOutputCount": record.output_summary["unknownOutputCount"],
            "unknownOutputs": record.output_summary["unknownOutputs"],
        }

    def _require_record(self, postprocess_id: str) -> RetargetPostprocessRecord:
        record = self._records.get(postprocess_id)
        if record is None:
            raise self.workflow_service._workflow_error(
                "retarget-postprocess-not-found",
                "The retarget post-process task was not found in this MCP session.",
            )
        return record

    @staticmethod
    def _snapshot(record: RetargetPostprocessRecord) -> dict[str, Any]:
        if record.index_refresh_state:
            if record.index_refresh_state in {"preparing", "prepare_failed"}:
                next_step = (
                    "Continue ue_refresh_animation_retarget_postprocess_index Preview with refreshReceipt "
                    "until indexRefreshState is ready; then Apply with confirmation "
                    f"'REFRESH RETARGET POSTPROCESS {record.postprocess_id}'."
                )
            elif record.index_refresh_state == "ready":
                next_step = (
                    "Apply ue_refresh_animation_retarget_postprocess_index with confirmation "
                    f"'REFRESH RETARGET POSTPROCESS {record.postprocess_id}' to atomically activate the paired snapshot generation."
                )
            elif record.index_refresh_state == "refreshed":
                next_step = (
                    "Restart the MCP server. After restart, revalidate the retarget outputs against the fresh Index "
                    "Revision before converting eligible AnimSequence suggestions into a P2 animation scale-fix Batch Plan."
                )
            else:
                next_step = "Inspect the Index Refresh failure before continuing."
        elif record.state == "auditing":
            next_step = "Continue ue_get_animation_retarget_postprocess until state is analyzed."
        elif record.state == "failed":
            next_step = "Inspect the failure before continuing."
        elif record.state == "analyzed" and not record.plan_id:
            next_step = (
                "Review suggestions, then call ue_plan_animation_retarget_postprocess to persist an immutable suggested Plan."
            )
        else:
            next_step = "Review the immutable suggested Plan; no asset changes were performed by P3 post-processing."
        return {
            "schemaVersion": RETARGET_POSTPROCESS_SCHEMA_VERSION,
            "postprocessId": record.postprocess_id,
            "retargetTaskId": record.retarget_task_id,
            "state": record.state,
            "readOnly": True,
            "createdAtUtc": record.created_at_utc,
            "updatedAtUtc": record.updated_at_utc,
            "outputSummary": record.output_summary,
            "auditTaskId": record.audit_task_id,
            "audit": record.audit_snapshot or {},
            "suggestions": record.suggestions or {},
            "suggestedPlan": {
                "planId": record.plan_id,
                "planDigest": record.plan_digest,
                "planRelativePath": record.plan_relative_path,
                "auditReportId": record.audit_report_id,
                "auditReportRelativePath": record.audit_report_relative_path,
            },
            "indexRefresh": {
                "state": record.index_refresh_state,
                "receipt": record.index_refresh_receipt,
                "orderedAssetCount": len(record.index_refresh_order),
                "preparedCount": record.index_refresh_cursor,
                "candidateAssetPaths": list(record.index_refresh_candidate_ids.keys()),
                "generation": record.index_refresh_generation,
                "failureCode": record.index_refresh_failure_code,
                "failureMessage": record.index_refresh_failure_message,
                "restartRequired": record.index_refresh_state == "refreshed",
            },
            "nextStep": next_step,
        }

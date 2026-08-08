from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .editor_bridge import LiveEditorBridgeService, LiveEditorError

MAX_AUDIT_ASSETS = 1000
MAX_AUDIT_BONES = 16
MAX_AUDIT_BATCH_SIZE = 8
MAX_AUDIT_PAGE_SIZE = 50
DEFAULT_AUDIT_BONES = ("Root", "pelvis")
AUDIT_CLASSIFICATIONS = (
    "normal",
    "scale-too-small",
    "scale-too-large",
    "root-lock-candidate",
    "root-track-candidate",
    "root-motion-review",
    "additive-requires-base-pose",
    "unsupported-composite",
    "load-failed",
)
AUDIT_SORT_ORDERS = (
    "processed-order",
    "asset-path",
    "classification",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _scale_x(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    x = value.get("x")
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x)


def _find_track(asset: dict[str, Any], bone_name: str) -> dict[str, Any]:
    tracks = asset.get("tracks")
    if not isinstance(tracks, list):
        return {}
    for item in tracks:
        if isinstance(item, dict) and item.get("bone") == bone_name:
            return item
    return {}


def _sample_bone(sample: dict[str, Any], bone_name: str) -> dict[str, Any]:
    bones = sample.get("bones")
    if not isinstance(bones, list):
        return {}
    for item in bones:
        if isinstance(item, dict) and item.get("bone") == bone_name:
            return item
    return {}


def _representative_preview_scale(asset: dict[str, Any], bone_name: str) -> float | None:
    samples = asset.get("previewSamples")
    if not isinstance(samples, list):
        return None
    values: list[float] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        value = _scale_x(_sample_bone(sample, bone_name).get("componentScale"))
        if value is not None:
            values.append(value)
    if not values:
        return None
    values.sort()
    return values[len(values) // 2]


def _close_scale(left: float, right: float) -> bool:
    tolerance = max(0.05, abs(right) * 0.05)
    return abs(left - right) <= tolerance


def _compact_pose_samples(asset: dict[str, Any], root_bone: str, pelvis_bone: str) -> list[dict[str, Any]]:
    samples = asset.get("previewSamples")
    if not isinstance(samples, list):
        return []
    compact: list[dict[str, Any]] = []
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        root = _sample_bone(sample, root_bone)
        pelvis = _sample_bone(sample, pelvis_bone) if pelvis_bone else {}
        compact.append(
            {
                "fraction": sample.get("fraction"),
                "time": sample.get("time"),
                "rootScale": root.get("componentScale"),
                "pelvisScale": pelvis.get("componentScale"),
                "pelvisLocation": pelvis.get("componentLocation"),
            }
        )
    return compact


def classify_animation_scale(asset: dict[str, Any], bone_names: list[str]) -> tuple[str, str]:
    status = str(asset.get("status") or "")
    if status == "not-an-animation-sequence":
        return "unsupported-composite", "Use an AnimSequence asset; composite animation types are not handled by this audit."
    if status != "success":
        return "load-failed", "Load the AnimSequence and its Skeleton/preview mesh, then rerun the audit."

    preview_status = str(asset.get("previewEvaluationStatus") or "")
    additive_type = asset.get("additiveAnimType")
    if additive_type not in (0, None) or preview_status == "unsupported-additive-requires-base-pose":
        return "additive-requires-base-pose", "Evaluate this Additive animation together with its Base Pose before planning a scale repair."
    if preview_status != "success":
        return "load-failed", "Resolve the preview evaluation context, then rerun the audit."

    root_bone = bone_names[0]
    root_track = _find_track(asset, root_bone)
    expected = _scale_x(root_track.get("referenceComponentScale"))
    actual = _representative_preview_scale(asset, root_bone)
    raw_track = _scale_x(root_track.get("firstScale"))
    if expected is None or actual is None or abs(expected) < 1e-6:
        return "load-failed", "The Root reference or evaluated Component Scale is unavailable; review the requested root bone and preview mesh."

    if _close_scale(actual, expected):
        return "normal", "No animation scale repair is suggested."

    if asset.get("enableRootMotion") is True:
        return "root-motion-review", "Review Root Motion and Root Lock settings before changing animation scale keys."

    if (
        asset.get("forceRootLock") is False
        and raw_track is not None
        and _close_scale(raw_track, 1.0)
        and not _close_scale(expected, 1.0)
    ):
        return "root-lock-candidate", "Review Force Root Lock with Root Motion Root Lock set to the reference pose before changing Root Scale keys."

    if raw_track is not None and not _close_scale(raw_track, expected):
        return "root-track-candidate", "Review setting the Root Scale Track from the target Skeleton reference scale."

    ratio = actual / expected
    if ratio < 0.5:
        return "scale-too-small", "Review Root Lock first, then the Root Scale Track if the evaluated Root remains too small."
    if ratio > 2.0:
        return "scale-too-large", "Review Root Lock and Root Scale Track; the evaluated Root is substantially larger than the Skeleton reference."
    return "root-track-candidate", "Review Root Lock and Root Scale Track against the target Skeleton reference scale."


def build_audit_item(asset: dict[str, Any], bone_names: list[str]) -> dict[str, Any]:
    root_bone = bone_names[0]
    pelvis_bone = bone_names[1] if len(bone_names) > 1 else ""
    classification, suggested_fix = classify_animation_scale(asset, bone_names)
    root_track = _find_track(asset, root_bone)
    return {
        "assetPath": asset.get("assetPath", ""),
        "assetType": "AnimSequence" if asset.get("status") == "success" else "unknown",
        "status": asset.get("status", ""),
        "classification": classification,
        "suggestedFix": suggested_fix,
        "loadedBefore": asset.get("loadedBefore", False),
        "loadedByBridge": asset.get("loadedByBridge", False),
        "skeletonPath": asset.get("skeletonPath", ""),
        "additiveAnimType": asset.get("additiveAnimType"),
        "additiveBasePoseType": asset.get("additiveBasePoseType"),
        "additiveRefSequencePath": asset.get("additiveRefSequencePath", ""),
        "enableRootMotion": asset.get("enableRootMotion"),
        "forceRootLock": asset.get("forceRootLock"),
        "useNormalizedRootMotionScale": asset.get("useNormalizedRootMotionScale"),
        "rootMotionRootLock": asset.get("rootMotionRootLock"),
        "previewEvaluationStatus": asset.get("previewEvaluationStatus", ""),
        "previewMeshPath": asset.get("previewMeshPath", ""),
        "rootBone": root_bone,
        "pelvisBone": pelvis_bone,
        "rootTrack": root_track,
        "poseSamples": _compact_pose_samples(asset, root_bone, pelvis_bone),
    }


@dataclass
class AnimationScaleAuditTask:
    task_id: str
    animation_paths: list[str]
    bone_names: list[str]
    load_if_needed: bool
    batch_size: int
    editor_session_id: str
    candidate_source: str
    path_prefix: str = ""
    index_snapshot_id: str = ""
    state: str = "running"
    cursor: int = 0
    started_at_utc: str = field(default_factory=_utc_now)
    completed_at_utc: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)
    error_code: str = ""
    error_message: str = ""


class AnimationScaleAuditService:
    def __init__(
        self,
        live_editor_service: LiveEditorBridgeService,
        index_service: Any | None = None,
        report_root: Path | None = None,
    ) -> None:
        self.live_editor_service = live_editor_service
        self.index_service = index_service
        self.report_root = report_root.expanduser().resolve() if report_root is not None else None
        self._task: AnimationScaleAuditTask | None = None

    def start(
        self,
        *,
        animation_paths: list[str] | None = None,
        path_prefix: str = "",
        bone_names: list[str] | None = None,
        load_if_needed: bool = False,
        batch_size: int = 1,
    ) -> dict[str, Any]:
        if self._task is not None and self._task.state == "running":
            raise LiveEditorError("animation-scale-audit-busy", "Another animation scale audit is already running.")
        candidate_source, normalized_prefix, index_snapshot_id, normalized_paths = self._resolve_candidates(
            animation_paths=animation_paths,
            path_prefix=path_prefix,
        )
        if bone_names is not None and not isinstance(bone_names, list):
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                "boneNames must be an array of bone names.",
            )
        normalized_bones = list(DEFAULT_AUDIT_BONES if bone_names is None else bone_names)
        if not 1 <= len(normalized_bones) <= MAX_AUDIT_BONES:
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                f"boneNames must contain between 1 and {MAX_AUDIT_BONES} bone names.",
            )
        normalized_bones = [LiveEditorBridgeService._bounded_string(name, "boneNames", 128) for name in normalized_bones]
        if any(not name for name in normalized_bones):
            raise LiveEditorError("live-editor-invalid-parameters", "boneNames must not contain empty values.")
        if not isinstance(load_if_needed, bool):
            raise LiveEditorError("live-editor-invalid-parameters", "loadIfNeeded must be a boolean.")
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or not 1 <= batch_size <= MAX_AUDIT_BATCH_SIZE:
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                f"batchSize must be an integer from 1 through {MAX_AUDIT_BATCH_SIZE}.",
            )

        status = self.live_editor_service.status()
        if status.get("state") != "available":
            raise LiveEditorError(
                str(status.get("reasonCode") or "live-editor-unavailable"),
                str(status.get("reason") or "The fixed Unreal Editor Bridge is unavailable."),
            )
        task = AnimationScaleAuditTask(
            task_id=str(uuid4()),
            animation_paths=normalized_paths,
            bone_names=normalized_bones,
            load_if_needed=load_if_needed,
            batch_size=batch_size,
            editor_session_id=str(status.get("sessionId") or ""),
            candidate_source=candidate_source,
            path_prefix=normalized_prefix,
            index_snapshot_id=index_snapshot_id,
        )
        self._task = task
        return self._snapshot(task, detail_offset=0, detail_limit=MAX_AUDIT_PAGE_SIZE)

    def _resolve_candidates(
        self,
        *,
        animation_paths: list[str] | None,
        path_prefix: str,
    ) -> tuple[str, str, str, list[str]]:
        normalized_prefix = LiveEditorBridgeService._bounded_string(path_prefix, "pathPrefix", 512)
        if animation_paths is not None and not isinstance(animation_paths, list):
            raise LiveEditorError("live-editor-invalid-parameters", "animationPaths must be an array when provided.")
        has_explicit_paths = isinstance(animation_paths, list) and bool(animation_paths)
        has_prefix = bool(normalized_prefix)
        if has_explicit_paths == has_prefix:
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                "Provide exactly one candidate source: non-empty animationPaths or pathPrefix.",
            )

        if has_explicit_paths:
            assert animation_paths is not None
            if len(animation_paths) > MAX_AUDIT_ASSETS:
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    f"animationPaths must not contain more than {MAX_AUDIT_ASSETS} Object Paths.",
                )
            normalized_paths = [
                LiveEditorBridgeService._bounded_string(path, "animationPaths", 512)
                for path in animation_paths
            ]
            for path in normalized_paths:
                LiveEditorBridgeService._validate_game_object_path(path)
            return "explicit-list", "", "", normalized_paths

        if normalized_prefix != "/Game" and not normalized_prefix.startswith("/Game/"):
            raise LiveEditorError("live-editor-invalid-parameters", "pathPrefix must begin with /Game.")
        if self.index_service is None:
            raise LiveEditorError(
                "animation-scale-audit-index-unavailable",
                "The fixed immutable SQLite index is unavailable for pathPrefix candidate discovery.",
            )
        candidates = self.index_service.list_asset_paths(
            asset_class="/Script/Engine.AnimSequence",
            path_prefix=normalized_prefix,
            limit=MAX_AUDIT_ASSETS,
        )
        if candidates.get("truncated") is True:
            raise LiveEditorError(
                "animation-scale-audit-too-many-candidates",
                f"pathPrefix matches more than {MAX_AUDIT_ASSETS} AnimSequence assets; narrow the prefix.",
            )
        paths = candidates.get("assetPaths", [])
        if not isinstance(paths, list) or not paths:
            raise LiveEditorError("animation-scale-audit-no-candidates", "pathPrefix matched no indexed AnimSequence assets.")
        normalized_paths = [str(path) for path in paths]
        return "immutable-index", normalized_prefix, str(candidates.get("snapshotId") or ""), normalized_paths

    def get(
        self,
        *,
        task_id: str,
        detail_offset: int = 0,
        detail_limit: int = 20,
        classification_filter: list[str] | None = None,
        sort_by: str = "processed-order",
    ) -> dict[str, Any]:
        task = self._require_task(task_id)
        self._validate_page(detail_offset, detail_limit)
        normalized_filter = self._validate_detail_view(classification_filter, sort_by)
        if task.state == "running":
            self._advance(task)
        return self._snapshot(
            task,
            detail_offset=detail_offset,
            detail_limit=detail_limit,
            classification_filter=normalized_filter,
            sort_by=sort_by,
        )

    def cancel(self, *, task_id: str) -> dict[str, Any]:
        task = self._require_task(task_id)
        if task.state == "running":
            task.state = "cancelled"
            task.completed_at_utc = _utc_now()
        return self._snapshot(task, detail_offset=0, detail_limit=MAX_AUDIT_PAGE_SIZE)

    def export_report(
        self,
        *,
        task_id: str,
        classification_filter: list[str] | None = None,
        sort_by: str = "asset-path",
    ) -> dict[str, Any]:
        task = self._require_task(task_id)
        if task.state == "running":
            raise LiveEditorError(
                "animation-scale-audit-report-running",
                "Finish or cancel the animation scale audit before exporting its report.",
            )
        if self.report_root is None:
            raise LiveEditorError(
                "animation-scale-audit-report-unavailable",
                "The fixed animation scale audit report root is unavailable.",
            )
        normalized_filter = self._validate_detail_view(classification_filter, sort_by)
        items = self._select_detail_items(task, normalized_filter, sort_by)
        snapshot = self._snapshot(
            task,
            detail_offset=0,
            detail_limit=1,
            classification_filter=normalized_filter,
            sort_by=sort_by,
        )
        report = {
            "schemaVersion": "1.0",
            "reportType": "animation-scale-audit",
            "task": {
                "taskId": task.task_id,
                "state": task.state,
                "startedAtUtc": task.started_at_utc,
                "completedAtUtc": task.completed_at_utc,
                "editorSessionId": task.editor_session_id,
                "candidateSource": task.candidate_source,
                "candidateSelection": {
                    "pathPrefix": task.path_prefix,
                    "indexSnapshotId": task.index_snapshot_id,
                },
                "loadIfNeeded": task.load_if_needed,
                "boneNames": task.bone_names,
                "batchSize": task.batch_size,
                "progress": snapshot["progress"],
            },
            "summary": {
                "classificationCounts": snapshot["summary"]["classificationCounts"],
                "processedItemCount": snapshot["summary"]["availableDetailCount"],
                "exportedItemCount": len(items),
                "classificationFilter": normalized_filter,
                "sortBy": sort_by,
            },
            "items": items,
        }
        payload = (json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        report_root = self.report_root
        report_root.mkdir(parents=True, exist_ok=True)
        reports_root = report_root / "animation-scale-audits"
        reports_root.mkdir(parents=True, exist_ok=True)
        resolved_reports_root = reports_root.resolve()
        try:
            resolved_reports_root.relative_to(report_root)
        except ValueError as exc:
            raise LiveEditorError(
                "animation-scale-audit-report-path-invalid",
                "The fixed animation scale audit report root resolves outside the configured WorkRoot.",
            ) from exc
        report_directory = resolved_reports_root / task.task_id
        report_directory.mkdir(exist_ok=True)
        resolved_directory = report_directory.resolve()
        try:
            relative_directory = resolved_directory.relative_to(report_root)
        except ValueError as exc:
            raise LiveEditorError(
                "animation-scale-audit-report-path-invalid",
                "The fixed animation scale audit report directory resolves outside the configured WorkRoot.",
            ) from exc
        report_path = resolved_directory / "report.json"
        temporary_path = resolved_directory / "report.json.tmp"
        temporary_path.write_bytes(payload)
        temporary_path.replace(report_path)
        digest = hashlib.sha256(payload).hexdigest()
        return {
            "taskId": task.task_id,
            "state": task.state,
            "format": "json",
            "reportId": f"sha256:{digest}",
            "reportRelativePath": (relative_directory / "report.json").as_posix(),
            "bytes": len(payload),
            "itemCount": len(items),
            "classificationFilter": normalized_filter,
            "sortBy": sort_by,
        }

    def _advance(self, task: AnimationScaleAuditTask) -> None:
        status = self.live_editor_service.status()
        if status.get("state") != "available" or str(status.get("sessionId") or "") != task.editor_session_id:
            task.state = "failed"
            task.completed_at_utc = _utc_now()
            task.error_code = "animation-scale-audit-session-invalidated"
            task.error_message = "The Unreal Editor session changed or became unavailable while the audit was running."
            return
        end = min(task.cursor + task.batch_size, len(task.animation_paths))
        chunk = task.animation_paths[task.cursor:end]
        try:
            response = self.live_editor_service.call_tool(
                "ue_diagnose_animation_scale",
                {
                    "animationPaths": chunk,
                    "boneNames": task.bone_names,
                    "loadIfNeeded": task.load_if_needed,
                },
            )
            result = response.get("result", {})
            assets = result.get("assets", []) if isinstance(result, dict) else []
            if not isinstance(assets, list) or len(assets) != len(chunk):
                raise LiveEditorError(
                    "animation-scale-audit-invalid-result",
                    "Animation scale diagnosis returned an unexpected asset result count.",
                )
            for asset in assets:
                if not isinstance(asset, dict):
                    raise LiveEditorError(
                        "animation-scale-audit-invalid-result",
                        "Animation scale diagnosis returned a non-object asset result.",
                    )
                task.items.append(build_audit_item(asset, task.bone_names))
            task.cursor = end
            if task.cursor >= len(task.animation_paths):
                task.state = "completed"
                task.completed_at_utc = _utc_now()
        except LiveEditorError as exc:
            task.state = "failed"
            task.completed_at_utc = _utc_now()
            task.error_code = exc.code
            task.error_message = str(exc)

    def _require_task(self, task_id: str) -> AnimationScaleAuditTask:
        if not isinstance(task_id, str) or self._task is None or self._task.task_id != task_id:
            raise LiveEditorError("animation-scale-audit-not-found", "The animation scale audit task was not found.")
        return self._task

    @staticmethod
    def _validate_page(detail_offset: int, detail_limit: int) -> None:
        if isinstance(detail_offset, bool) or not isinstance(detail_offset, int) or detail_offset < 0:
            raise LiveEditorError("live-editor-invalid-parameters", "detailOffset must be a non-negative integer.")
        if isinstance(detail_limit, bool) or not isinstance(detail_limit, int) or not 1 <= detail_limit <= MAX_AUDIT_PAGE_SIZE:
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                f"detailLimit must be an integer from 1 through {MAX_AUDIT_PAGE_SIZE}.",
            )

    @staticmethod
    def _validate_detail_view(classification_filter: list[str] | None, sort_by: str) -> list[str]:
        if classification_filter is None:
            normalized_filter: list[str] = []
        else:
            if not isinstance(classification_filter, list) or not classification_filter:
                raise LiveEditorError(
                    "live-editor-invalid-parameters",
                    "classificationFilter must be a non-empty array when provided.",
                )
            normalized_filter = []
            for classification in classification_filter:
                if not isinstance(classification, str) or classification not in AUDIT_CLASSIFICATIONS:
                    raise LiveEditorError(
                        "live-editor-invalid-parameters",
                        f"classificationFilter values must be one of: {', '.join(AUDIT_CLASSIFICATIONS)}.",
                    )
                if classification not in normalized_filter:
                    normalized_filter.append(classification)
        if not isinstance(sort_by, str) or sort_by not in AUDIT_SORT_ORDERS:
            raise LiveEditorError(
                "live-editor-invalid-parameters",
                f"sortBy must be one of: {', '.join(AUDIT_SORT_ORDERS)}.",
            )
        return normalized_filter

    @staticmethod
    def _select_detail_items(
        task: AnimationScaleAuditTask,
        classification_filter: list[str] | None,
        sort_by: str,
    ) -> list[dict[str, Any]]:
        items = task.items
        if classification_filter:
            allowed = set(classification_filter)
            items = [item for item in items if item.get("classification") in allowed]
        if sort_by == "asset-path":
            return sorted(items, key=lambda item: str(item.get("assetPath") or ""))
        if sort_by == "classification":
            return sorted(
                items,
                key=lambda item: (str(item.get("classification") or ""), str(item.get("assetPath") or "")),
            )
        return list(items)

    @staticmethod
    def _snapshot(
        task: AnimationScaleAuditTask,
        *,
        detail_offset: int,
        detail_limit: int,
        classification_filter: list[str] | None = None,
        sort_by: str = "processed-order",
    ) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in task.items:
            classification = str(item.get("classification") or "unknown")
            counts[classification] = counts.get(classification, 0) + 1
        filtered_items = AnimationScaleAuditService._select_detail_items(task, classification_filter, sort_by)
        total = len(task.animation_paths)
        processed = task.cursor
        completed_percent = 100 if task.state == "completed" else int(processed * 100 / total)
        end = min(detail_offset + detail_limit, len(filtered_items))
        details = filtered_items[detail_offset:end]
        snapshot: dict[str, Any] = {
            "taskId": task.task_id,
            "state": task.state,
            "readOnly": True,
            "startedAtUtc": task.started_at_utc,
            "editorSessionId": task.editor_session_id,
            "candidateSource": task.candidate_source,
            "candidateSelection": {
                "pathPrefix": task.path_prefix,
                "indexSnapshotId": task.index_snapshot_id,
            },
            "loadIfNeeded": task.load_if_needed,
            "boneNames": task.bone_names,
            "batchSize": task.batch_size,
            "progress": {
                "processedAssets": processed,
                "totalAssets": total,
                "completedPercent": completed_percent,
            },
            "summary": {
                "classificationCounts": counts,
                "availableDetailCount": len(task.items),
                "filteredDetailCount": len(filtered_items),
            },
            "details": {
                "offset": detail_offset,
                "limit": detail_limit,
                "classificationFilter": classification_filter or [],
                "sortBy": sort_by,
                "returnedCount": len(details),
                "totalAvailable": len(filtered_items),
                "hasMore": end < len(filtered_items),
                "items": details,
            },
        }
        if end < len(filtered_items):
            snapshot["details"]["nextOffset"] = end
        if task.completed_at_utc:
            snapshot["completedAtUtc"] = task.completed_at_utc
        if task.error_code:
            snapshot["errorCode"] = task.error_code
            snapshot["errorMessage"] = task.error_message
        return snapshot

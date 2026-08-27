from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .agent_workflow import WorkflowError
from .patches import validate_patch

LIVE_WRITE_BATCH_PLAN_SCHEMA_VERSION = "1.0"
MAX_BATCH_ASSETS = 4
MAX_BATCH_OPERATIONS_PER_ASSET = 8
MAX_BATCH_OPERATIONS_TOTAL = 16
MAX_BATCH_REQUEST_BYTES = 64 * 1024
MAX_BATCH_PLANS = 100
MAX_BATCH_DESCRIPTION_BYTES = 1024

W4_BATCH_OPERATIONS = {
    "setAssetProperty",
    "setVariableDefault",
    "setComponentProperty",
    "setPinDefault",
}

BATCH_PLAN_PREFIX = "lwbp_"
BATCH_OPERATION_PREFIX = "bop_"
BATCH_CONFIRMATION_PREFIX = "APPLY LIVE WRITE BATCH "


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_request_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_digest(data: bytes) -> str:
    return "sha256:" + _sha256_hex(data)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _validate_asset_path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("/Game/")
        or len(value) > 512
        or "\\" in value
        or ":" in value
        or ".." in value
        or any(ord(character) < 32 for character in value)
    ):
        raise WorkflowError(
            "live-write-batch-request-invalid",
            "assetPath must be one exact /Game Object Path.",
        )
    package_path, separator, object_name = value.rpartition(".")
    if (
        not separator
        or not object_name
        or "/" in object_name
        or package_path.rfind("/") >= len(package_path) - 1
    ):
        raise WorkflowError(
            "live-write-batch-request-invalid",
            "assetPath must be one exact /Game Object Path.",
        )
    return value


def _normalize_request(assets: Any, description: Any) -> dict[str, Any]:
    if not isinstance(assets, list):
        raise WorkflowError(
            "live-write-batch-request-invalid",
            "assets must be a non-empty array of asset groups.",
        )
    if not 1 <= len(assets) <= MAX_BATCH_ASSETS:
        code = (
            "live-write-batch-asset-count-exceeded"
            if len(assets) > MAX_BATCH_ASSETS
            else "live-write-batch-request-invalid"
        )
        message = (
            f"Bounded batch accepts 1..{MAX_BATCH_ASSETS} assets."
            if len(assets) > MAX_BATCH_ASSETS
            else "Bounded batch requires at least one asset."
        )
        raise WorkflowError(code, message)
    if not isinstance(description, str):
        raise WorkflowError(
            "live-write-batch-request-invalid",
            "description must be a string.",
        )
    if len(description.encode("utf-8")) > MAX_BATCH_DESCRIPTION_BYTES:
        raise WorkflowError(
            "live-write-batch-request-invalid",
            "description exceeds the 1024 byte limit.",
        )

    normalized_assets: list[dict[str, Any]] = []
    seen_assets: set[str] = set()
    total_operations = 0
    for asset_index, asset_group in enumerate(assets):
        if not isinstance(asset_group, dict):
            raise WorkflowError(
                "live-write-batch-request-invalid",
                f"assets[{asset_index}] must be an object.",
            )
        unknown_asset_fields = set(asset_group) - {"assetPath", "operations"}
        if unknown_asset_fields:
            raise WorkflowError(
                "live-write-batch-request-invalid",
                f"assets[{asset_index}] contains unknown fields: {sorted(unknown_asset_fields)}",
            )
        asset_path = _validate_asset_path(asset_group.get("assetPath"))
        if asset_path in seen_assets:
            raise WorkflowError(
                "live-write-batch-duplicate-asset",
                f"The same asset appears more than once in one batch: {asset_path}",
            )
        seen_assets.add(asset_path)

        operations = asset_group.get("operations")
        if not isinstance(operations, list):
            raise WorkflowError(
                "live-write-batch-request-invalid",
                f"assets[{asset_index}].operations must be an array.",
            )
        if not 1 <= len(operations) <= MAX_BATCH_OPERATIONS_PER_ASSET:
            code = (
                "live-write-batch-operation-count-exceeded"
                if len(operations) > MAX_BATCH_OPERATIONS_PER_ASSET
                else "live-write-batch-request-invalid"
            )
            message = (
                f"Each asset accepts 1..{MAX_BATCH_OPERATIONS_PER_ASSET} operations."
                if len(operations) > MAX_BATCH_OPERATIONS_PER_ASSET
                else "Each asset requires at least one operation."
            )
            raise WorkflowError(code, message)

        normalized_operations: list[dict[str, Any]] = []
        for operation_index, operation_value in enumerate(operations):
            if not isinstance(operation_value, dict):
                raise WorkflowError(
                    "live-write-batch-request-invalid",
                    f"assets[{asset_index}].operations[{operation_index}] must be an object.",
                )
            unknown_operation_fields = set(operation_value) - {"operation", "target", "value"}
            if unknown_operation_fields:
                raise WorkflowError(
                    "live-write-batch-request-invalid",
                    f"assets[{asset_index}].operations[{operation_index}] contains unknown fields: {sorted(unknown_operation_fields)}",
                )
            operation_name = operation_value.get("operation")
            if operation_name not in W4_BATCH_OPERATIONS:
                raise WorkflowError(
                    "live-write-batch-operation-unsupported",
                    f"Unsupported W4 batch operation: {operation_name!r}. Allowed: {sorted(W4_BATCH_OPERATIONS)}",
                    details={"operation": operation_name},
                )
            target = operation_value.get("target")
            if not isinstance(target, dict):
                raise WorkflowError(
                    "live-write-batch-request-invalid",
                    f"assets[{asset_index}].operations[{operation_index}].target must be an object.",
                )
            normalized_operations.append(
                {
                    "operation": operation_name,
                    "target": target,
                    "value": operation_value.get("value"),
                }
            )
        normalized_assets.append(
            {
                "assetPath": asset_path,
                "operations": normalized_operations,
            }
        )
        total_operations += len(normalized_operations)

    if total_operations > MAX_BATCH_OPERATIONS_TOTAL:
        raise WorkflowError(
            "live-write-batch-total-operation-count-exceeded",
            f"Bounded batch accepts at most {MAX_BATCH_OPERATIONS_TOTAL} total operations.",
        )
    return {
        "assets": normalized_assets,
        "description": description,
    }


def _operation_target_key(operation: str, target: dict[str, Any]) -> str:
    from .agent_workflow import live_write_stable_target_key

    return live_write_stable_target_key(operation, target)


@dataclass(frozen=True)
class LiveWriteBatchPlanRecord:
    batch_plan_id: str
    digest: str
    payload: dict[str, Any]
    path: Path


class BoundedBatchService:
    """Read-only W4 bounded batch planner over existing single-operation child Plans."""

    def __init__(self, workflow_service: Any) -> None:
        self.workflow_service = workflow_service
        configured_work_root = getattr(workflow_service.config, "work_root", None)
        self.work_root = Path(configured_work_root).expanduser().resolve() if configured_work_root is not None else None
        self._plans: dict[str, LiveWriteBatchPlanRecord] = {}
        self._lock = threading.RLock()

    def _batch_plan_directory(self, batch_plan_id: str) -> Path:
        if self.work_root is None:
            raise WorkflowError("live-write-batch-plan-rejected", "Batch planning requires a fixed Work Root.")
        path = self.work_root / "batch-plans" / batch_plan_id
        if not _is_within(path, self.work_root):
            raise WorkflowError("live-write-batch-plan-rejected", "Batch Plan path escaped the fixed Work Root.")
        return path

    def plan(self, *, assets: Any, description: str = "") -> dict[str, Any]:
        with self._lock:
            if len(self._plans) >= MAX_BATCH_PLANS:
                raise WorkflowError(
                    "live-write-batch-plan-rejected",
                    f"This MCP session already holds {MAX_BATCH_PLANS} W4 Batch Plans.",
                )
            normalized = _normalize_request(assets, description)
            request_bytes = _canonical_request_bytes(normalized)
            if len(request_bytes) > MAX_BATCH_REQUEST_BYTES:
                raise WorkflowError(
                    "live-write-batch-request-too-large",
                    f"Canonical request exceeds {MAX_BATCH_REQUEST_BYTES} bytes.",
                    details={"requestBytes": len(request_bytes), "maxRequestBytes": MAX_BATCH_REQUEST_BYTES},
                )

            bindings = self._bind_asset(normalized["assets"])

            aggregate_validation = self._validate_aggregate_patch(normalized, bindings)
            policy = aggregate_validation.get("policy") or {}
            if not isinstance(policy, dict):
                raise WorkflowError(
                    "live-write-batch-plan-rejected",
                    "Batch Plan validation returned no usable Policy.",
                )

            hard_bounds = {
                "maxAssets": MAX_BATCH_ASSETS,
                "maxOperationsPerAsset": MAX_BATCH_OPERATIONS_PER_ASSET,
                "maxTotalOperations": MAX_BATCH_OPERATIONS_TOTAL,
                "maxRequestBytes": MAX_BATCH_REQUEST_BYTES,
            }
            policy_bounds = {
                "maxAssetsPerPatch": int(policy.get("maxAssetsPerPatch") or MAX_BATCH_ASSETS),
                "maxOperationsPerAsset": int(policy.get("maxOperationsPerAsset") or MAX_BATCH_OPERATIONS_PER_ASSET),
                "maxValueBytes": int(policy.get("maxValueBytes") or 0),
            }
            effective_max_assets = min(MAX_BATCH_ASSETS, policy_bounds["maxAssetsPerPatch"])
            effective_max_operations_per_asset = min(
                MAX_BATCH_OPERATIONS_PER_ASSET,
                policy_bounds["maxOperationsPerAsset"],
            )
            effective_bounds = {
                "maxAssets": effective_max_assets,
                "maxOperationsPerAsset": effective_max_operations_per_asset,
                "maxTotalOperations": min(
                    MAX_BATCH_OPERATIONS_TOTAL,
                    effective_max_assets * effective_max_operations_per_asset,
                ),
            }
            bounds = {
                "hard": hard_bounds,
                "policy": policy_bounds,
                "effective": effective_bounds,
                "requestBytes": len(request_bytes),
            }

            child_plan_ids: list[str] = []
            batch_directory: Path | None = None
            try:
                children_by_asset = self._create_child_plans(normalized, bindings, child_plan_ids)
                request_digest = self._request_digest(normalized, bindings)
                batch_plan_id = BATCH_PLAN_PREFIX + secrets.token_urlsafe(18)
                confirmation = BATCH_CONFIRMATION_PREFIX + batch_plan_id
                assets_payload = self._build_assets_payload(bindings, children_by_asset)
                payload = {
                    "schemaVersion": LIVE_WRITE_BATCH_PLAN_SCHEMA_VERSION,
                    "batchPlanId": batch_plan_id,
                    "state": "planned",
                    "projectName": self.workflow_service.project_name,
                    "createdAtUtc": _utc_now(),
                    "description": normalized["description"],
                    "requestDigest": request_digest,
                    "assetCount": len(bindings),
                    "operationCount": sum(len(asset["operations"]) for asset in assets_payload),
                    "assets": assets_payload,
                    "bounds": bounds,
                    "confirmationRequired": confirmation,
                    "commitAllowedByPolicy": bool(aggregate_validation.get("commitAllowedByPolicy")),
                }
                payload_bytes = _json_bytes(payload)
                digest = _sha256_digest(payload_bytes)
                batch_directory = self._batch_plan_directory(batch_plan_id)
                plan_path = batch_directory / "plan.json"
                self._write_atomic(plan_path, payload_bytes)
                record = LiveWriteBatchPlanRecord(batch_plan_id, digest, payload, plan_path)
                self._plans[batch_plan_id] = record
                return self._response(record)
            except Exception as exc:
                if batch_directory is not None:
                    shutil.rmtree(batch_directory, ignore_errors=True)
                if child_plan_ids:
                    try:
                        self.workflow_service.discard_unconsumed_plans(child_plan_ids)
                    except Exception:
                        pass
                if isinstance(exc, WorkflowError) and exc.code == "live-write-batch-child-plan-failed":
                    raise
                raise WorkflowError(
                    "live-write-batch-child-plan-failed",
                    "A child Plan failed after aggregate validation; no Batch Plan was created.",
                    details={
                        "causeCode": getattr(exc, "code", exc.__class__.__name__),
                        "causeMessage": str(exc),
                    },
                ) from exc

    def get(self, *, batch_plan_id: str) -> dict[str, Any]:
        with self._lock:
            if not isinstance(batch_plan_id, str) or not batch_plan_id.startswith(BATCH_PLAN_PREFIX):
                raise WorkflowError(
                    "live-write-batch-plan-not-found",
                    "batch_plan_id must be the exact identifier returned by ue_plan_live_write_batch.",
                )
            record = self._plans.get(batch_plan_id)
            if record is None:
                raise WorkflowError(
                    "live-write-batch-plan-not-found",
                    "The W4 Batch Plan was not found in this MCP session.",
                )
            current_bytes = record.path.read_bytes()
            if _sha256_digest(current_bytes) != record.digest or json.loads(current_bytes) != record.payload:
                raise WorkflowError(
                    "live-write-batch-plan-tampered",
                    "The stored W4 Batch Plan changed after it was created.",
                )
            return self._response(record)

    def _bind_asset(self, normalized_assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        bindings = []
        for asset_group in normalized_assets:
            binding = self.workflow_service.bind_asset_for_batch(str(asset_group["assetPath"]))
            if not isinstance(binding, dict):
                raise WorkflowError(
                    "live-write-batch-plan-rejected",
                    "Batch asset binding returned no asset identity.",
                )
            bindings.append(binding)
        return bindings

    def _validate_aggregate_patch(
        self,
        normalized: dict[str, Any],
        bindings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if self.workflow_service.config is None:
            raise WorkflowError("live-write-batch-plan-rejected", "Batch planning requires a fixed workflow configuration.")
        policy_path = self.workflow_service.config.policy_path
        revision_export = self.workflow_service.config.revision_export
        if policy_path is None or revision_export is None:
            raise WorkflowError("live-write-batch-plan-rejected", "Batch planning requires Policy and Revision Export paths.")
        patch = self._build_aggregate_patch(normalized, bindings)
        if self.work_root is None:
            raise WorkflowError("live-write-batch-plan-rejected", "Batch planning requires a fixed Work Root.")
        self.work_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="batch-aggregate-", dir=self.work_root) as temporary:
            patch_path = Path(temporary) / "patch.json"
            patch_path.write_bytes(_json_bytes(patch))
            validation = validate_patch(patch_path, Path(policy_path), Path(revision_export))
        if not validation.get("valid"):
            # W4 batches intentionally allow same-target repeated writes. The existing
            # single-patch validator treats those as duplicate-transaction-target errors,
            # which are not applicable to the W4 sequence (W3 supersession is authoritative).
            # Any other validation error still rejects the whole Batch Plan.
            errors = validation.get("errors") or []
            non_duplicate_errors = [
                error for error in errors if error.get("code") != "duplicate-transaction-target"
            ]
            if non_duplicate_errors:
                raise WorkflowError(
                    "live-write-batch-plan-rejected",
                    "The aggregate requested batch was rejected by Policy or Revision validation.",
                    details={
                        "validation": {
                            "summary": validation.get("summary"),
                            "errors": validation.get("errors"),
                            "warnings": validation.get("warnings"),
                        }
                    },
                )
        return validation

    def _build_aggregate_patch(
        self,
        normalized: dict[str, Any],
        bindings: list[dict[str, Any]],
    ) -> dict[str, Any]:
        patch_id = "batch-aggregate-" + uuid.uuid4().hex
        assets: list[dict[str, Any]] = []
        operation_index = 0
        for asset_group, binding in zip(normalized["assets"], bindings, strict=True):
            operations = []
            for operation_value in asset_group["operations"]:
                operation_index += 1
                operations.append(
                    {
                        "operationId": f"op-{operation_index}",
                        "operation": operation_value["operation"],
                        "target": operation_value["target"],
                        "value": operation_value["value"],
                    }
                )
            assets.append(
                {
                    "assetPath": binding["assetPath"],
                    "expectedRevision": binding["expectedRevision"],
                    "expectedAssetClass": binding["assetClass"],
                    "operations": operations,
                }
            )
        return {
            "schemaVersion": "1.0",
            "patchId": patch_id,
            "projectName": self.workflow_service.project_name,
            "description": normalized["description"],
            "assets": assets,
        }

    def _create_child_plans(
        self,
        normalized: dict[str, Any],
        bindings: list[dict[str, Any]],
        child_plan_ids: list[str],
    ) -> list[list[dict[str, Any]]]:
        children_by_asset: list[list[dict[str, Any]]] = []
        sequence_index = 0
        for asset_index, (asset_group, binding) in enumerate(
            zip(normalized["assets"], bindings, strict=True)
        ):
            asset_children: list[dict[str, Any]] = []
            for operation_index, operation_value in enumerate(asset_group["operations"]):
                child = self.workflow_service.plan_patch(
                    asset_path=binding["assetPath"],
                    operation=operation_value["operation"],
                    target=operation_value["target"],
                    value=operation_value["value"],
                    description=normalized["description"],
                )
                child_plan_id = str(child.get("planId") or "")
                child_digest = str(child.get("patchDigest") or "")
                if not child_plan_id.startswith("plan_"):
                    raise WorkflowError(
                        "live-write-batch-child-plan-failed",
                        "A child Plan returned an invalid planId.",
                    )
                if not child_digest.startswith("sha256:"):
                    raise WorkflowError(
                        "live-write-batch-child-plan-failed",
                        "A child Plan returned an invalid patchDigest.",
                    )
                batch_operation_id = f"{BATCH_OPERATION_PREFIX}{sequence_index + 1:04d}"
                child_entry = {
                    "batchOperationId": batch_operation_id,
                    "sequenceIndex": sequence_index,
                    "assetIndex": asset_index,
                    "operationIndex": operation_index,
                    "childPlanId": child_plan_id,
                    "childPatchDigest": child_digest,
                    "operation": operation_value["operation"],
                    "target": operation_value["target"],
                    "value": operation_value["value"],
                    "risk": str(child.get("risk") or ""),
                    "stableTargetKey": _operation_target_key(
                        operation_value["operation"],
                        operation_value["target"],
                    ),
                    "expectedEffective": False,
                    "expectedSupersededByBatchOperationId": "",
                    "expectedSupersedesBatchOperationIds": [],
                }
                asset_children.append(child_entry)
                child_plan_ids.append(child_plan_id)
                sequence_index += 1
            self._apply_supersession_preview(asset_children)
            children_by_asset.append(asset_children)
        return children_by_asset

    def _apply_supersession_preview(self, asset_children: list[dict[str, Any]]) -> None:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for child in asset_children:
            grouped.setdefault(child["stableTargetKey"], []).append(child)
        for group in grouped.values():
            for index, child in enumerate(group):
                child["expectedSupersedesBatchOperationIds"] = [
                    prior["batchOperationId"] for prior in group[:index]
                ]
                child["expectedEffective"] = index == len(group) - 1
                child["expectedSupersededByBatchOperationId"] = (
                    "" if child["expectedEffective"] else group[-1]["batchOperationId"]
                )

    def _request_digest(
        self,
        normalized: dict[str, Any],
        bindings: list[dict[str, Any]],
    ) -> str:
        digest_input = {
            "request": normalized,
            "assets": [
                {
                    "assetPath": binding["assetPath"],
                    "assetClass": binding["assetClass"],
                    "expectedRevision": binding["expectedRevision"],
                }
                for binding in bindings
            ],
        }
        return _sha256_digest(_canonical_request_bytes(digest_input))

    def _build_assets_payload(
        self,
        bindings: list[dict[str, Any]],
        children_by_asset: list[list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        return [
            {
                "assetPath": binding["assetPath"],
                "assetClass": binding["assetClass"],
                "expectedRevision": binding["expectedRevision"],
                "operations": children,
            }
            for binding, children in zip(bindings, children_by_asset, strict=True)
        ]

    def _write_atomic(self, path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(payload)
        temporary.replace(path)

    def _response(self, record: LiveWriteBatchPlanRecord) -> dict[str, Any]:
        payload = record.payload
        return {
            "schemaVersion": LIVE_WRITE_BATCH_PLAN_SCHEMA_VERSION,
            "tool": "ue_plan_live_write_batch",
            "ok": True,
            "batchPlanId": payload["batchPlanId"],
            "batchPlanDigest": record.digest,
            "requestDigest": payload["requestDigest"],
            "state": payload["state"],
            "projectName": payload["projectName"],
            "assetCount": payload["assetCount"],
            "operationCount": payload["operationCount"],
            "assets": payload["assets"],
            "bounds": payload["bounds"],
            "commitAllowedByPolicy": payload["commitAllowedByPolicy"],
            "confirmationRequired": payload["confirmationRequired"],
            "nextStep": "Call ue_apply_live_write_batch after W4-2 is available.",
        }
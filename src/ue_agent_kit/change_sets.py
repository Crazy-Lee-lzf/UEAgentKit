from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CHANGE_SET_SCHEMA_VERSION = "2.0"
LEGACY_CHANGE_SET_SCHEMA_VERSION = "1.0"
MAX_CHANGE_SETS = 50
MAX_CHANGE_SET_RECEIPTS = 100
MAX_CHANGE_SET_ID_LENGTH = 64
MAX_CHANGE_SET_TASK_ID_LENGTH = 96
MAX_CHANGE_SET_TITLE_LENGTH = 256

CHANGE_SET_STATUSES = {
    "planned",
    "applied",
    "partially_applied",
    "undone",
    "discarded",
    "saved",
    "verified",
    "no-op",
    "failed",
    "unknown",
}
CHANGE_SET_OPERATION_STATUSES = {
    "applied",
    "undone",
    "discarded",
    "saved",
    "verified",
    "no-op",
    "superseded",
    "failed",
    "unknown",
}
TERMINAL_CHANGE_SET_STATUSES = {
    "undone",
    "discarded",
    "verified",
    "no-op",
    "superseded",
    "failed",
}

_SAFE_ID_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


class ChangeSetError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ChangeSetOperationRecord:
    receipt: str
    plan_id: str
    asset_path: str
    operation: str
    transaction_id: str
    editor_session_id: str
    status: str
    created_at_utc: str
    updated_at_utc: str
    save_receipt: str = ""
    failure_code: str = ""
    checkpoint_id: str = ""


@dataclass
class ChangeSetRecord:
    change_set_id: str
    task_id: str
    editor_session_id: str
    title: str
    status: str
    created_at_utc: str
    updated_at_utc: str
    operations: list[ChangeSetOperationRecord] = field(default_factory=list)

    @property
    def receipts(self) -> list[str]:
        return [operation.receipt for operation in self.operations]


def _validate_safe_id(value: str, *, prefix: str, maximum: int, code: str, field_name: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith(prefix)
        or len(value) <= len(prefix)
        or len(value) > maximum
        or any(character not in _SAFE_ID_CHARS for character in value)
    ):
        raise ChangeSetError(code, f"{field_name} must be the exact identifier returned by UEAgentKit.")
    return value


def validate_change_set_id(value: str) -> str:
    return _validate_safe_id(
        value,
        prefix="cs_",
        maximum=MAX_CHANGE_SET_ID_LENGTH,
        code="change-set-invalid",
        field_name="changeSetId",
    )


def validate_change_set_task_id(value: str) -> str:
    return _validate_safe_id(
        value,
        prefix="task_",
        maximum=MAX_CHANGE_SET_TASK_ID_LENGTH,
        code="change-set-task-id-invalid",
        field_name="taskId",
    )


def validate_change_set_title(value: str) -> str:
    if not isinstance(value, str):
        raise ChangeSetError("change-set-title-invalid", "title must be a string.")
    title = value.strip()
    if not title or len(title) > MAX_CHANGE_SET_TITLE_LENGTH:
        raise ChangeSetError(
            "change-set-title-invalid",
            f"title must contain 1 through {MAX_CHANGE_SET_TITLE_LENGTH} characters.",
        )
    return title


def validate_live_receipt_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("live_")
        or len(value) > 96
        or any(character not in _SAFE_ID_CHARS for character in value)
    ):
        raise ValueError("invalid live apply receipt")
    return value


def validate_change_set_operation_receipt(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(("live_", "apply_", "noop_")):
        raise ValueError("invalid Change Set operation receipt")
    if len(value) > 96 or any(character not in _SAFE_ID_CHARS for character in value):
        raise ValueError("invalid Change Set operation receipt")
    return value


def derive_change_set_status(operations: list[ChangeSetOperationRecord]) -> str:
    if not operations:
        return "planned"
    statuses = [operation.status for operation in operations]
    if any(status == "unknown" for status in statuses):
        return "unknown"
    if any(status == "failed" for status in statuses):
        return "failed"
    if all(status == "verified" for status in statuses):
        return "verified"
    if all(status == "no-op" for status in statuses):
        return "no-op"
    if all(status == "superseded" for status in statuses):
        return "no-op"
    if all(status in {"verified", "no-op", "superseded"} for status in statuses):
        return "verified" if any(status == "verified" for status in statuses) else "no-op"
    if all(status in {"verified", "no-op"} for status in statuses):
        return "verified"
    if all(status in {"saved", "verified", "superseded"} for status in statuses):
        return "saved"
    if all(status in {"saved", "verified"} for status in statuses):
        return "saved"
    if all(status == "undone" for status in statuses):
        return "undone"
    if all(status == "discarded" for status in statuses):
        return "discarded"
    if all(status == "applied" for status in statuses):
        return "applied"
    return "partially_applied"


def is_terminal_change_set(record: ChangeSetRecord) -> bool:
    if not record.operations:
        return False
    return all(operation.status in TERMINAL_CHANGE_SET_STATUSES for operation in record.operations)


def _serialize_operation(operation: ChangeSetOperationRecord) -> dict[str, Any]:
    return {
        "receipt": operation.receipt,
        "planId": operation.plan_id,
        "assetPath": operation.asset_path,
        "operation": operation.operation,
        "transactionId": operation.transaction_id,
        "editorSessionId": operation.editor_session_id,
        "status": operation.status,
        "createdAtUtc": operation.created_at_utc,
        "updatedAtUtc": operation.updated_at_utc,
        "saveReceipt": operation.save_receipt,
        "failureCode": operation.failure_code,
        "checkpointId": operation.checkpoint_id,
    }


def serialize_change_set_record(record: ChangeSetRecord, project_name: str) -> dict[str, Any]:
    status = derive_change_set_status(record.operations)
    return {
        "schemaVersion": CHANGE_SET_SCHEMA_VERSION,
        "projectName": project_name,
        "changeSetId": record.change_set_id,
        "taskId": record.task_id,
        "editorSessionId": record.editor_session_id,
        "title": record.title,
        "status": status,
        "createdAtUtc": record.created_at_utc,
        "updatedAtUtc": record.updated_at_utc,
        "operations": [_serialize_operation(operation) for operation in record.operations],
    }


def _deserialize_operation(value: Any) -> ChangeSetOperationRecord:
    if not isinstance(value, dict):
        raise ValueError("change set operation record invalid")
    try:
        receipt = validate_change_set_operation_receipt(str(value.get("receipt", "")))
    except ValueError:
        raise ValueError("change set journal receipt invalid") from None
    status = str(value.get("status", ""))
    created_at_utc = str(value.get("createdAtUtc", ""))
    updated_at_utc = str(value.get("updatedAtUtc", ""))
    if status not in CHANGE_SET_OPERATION_STATUSES or not created_at_utc or not updated_at_utc:
        raise ValueError("change set operation lifecycle invalid")
    fields = {
        "plan_id": str(value.get("planId", "")),
        "asset_path": str(value.get("assetPath", "")),
        "operation": str(value.get("operation", "")),
        "transaction_id": str(value.get("transactionId", "")),
        "editor_session_id": str(value.get("editorSessionId", "")),
        "save_receipt": str(value.get("saveReceipt", "")),
        "failure_code": str(value.get("failureCode", "")),
        "checkpoint_id": str(value.get("checkpointId", "")),
    }
    if any(len(item) > 512 for item in fields.values()):
        raise ValueError("change set operation field exceeds bounded contract")
    return ChangeSetOperationRecord(
        receipt=receipt,
        status=status,
        created_at_utc=created_at_utc,
        updated_at_utc=updated_at_utc,
        **fields,
    )


def _deserialize_legacy_change_set(value: dict[str, Any]) -> ChangeSetRecord:
    change_set_id = validate_change_set_id(str(value.get("changeSetId", "")))
    created_at_utc = str(value.get("createdAtUtc", ""))
    receipts_value = value.get("receipts")
    if not created_at_utc or not isinstance(receipts_value, list):
        raise ValueError("change set journal record invalid")
    operations: list[ChangeSetOperationRecord] = []
    for receipt_value in receipts_value:
        try:
            receipt = validate_live_receipt_id(str(receipt_value))
        except ValueError:
            raise ValueError("change set journal receipt invalid") from None
        operations.append(
            ChangeSetOperationRecord(
                receipt=receipt,
                plan_id="",
                asset_path="",
                operation="",
                transaction_id="",
                editor_session_id="",
                status="unknown",
                created_at_utc=created_at_utc,
                updated_at_utc=created_at_utc,
            )
        )
    if len(operations) > MAX_CHANGE_SET_RECEIPTS:
        raise ValueError("change set journal receipt count invalid")
    suffix = change_set_id.removeprefix("cs_")
    return ChangeSetRecord(
        change_set_id=change_set_id,
        task_id=f"task_{suffix}",
        editor_session_id="",
        title="Recovered legacy Change Set",
        status=derive_change_set_status(operations),
        created_at_utc=created_at_utc,
        updated_at_utc=created_at_utc,
        operations=operations,
    )


def deserialize_change_set_record(value: dict[str, Any], project_name: str) -> ChangeSetRecord:
    if value.get("projectName") != project_name:
        raise ValueError("change set journal identity mismatch")
    schema_version = value.get("schemaVersion")
    if schema_version == LEGACY_CHANGE_SET_SCHEMA_VERSION:
        return _deserialize_legacy_change_set(value)
    if schema_version != CHANGE_SET_SCHEMA_VERSION:
        raise ValueError("change set journal identity mismatch")

    change_set_id = validate_change_set_id(str(value.get("changeSetId", "")))
    task_id = validate_change_set_task_id(str(value.get("taskId", "")))
    title = validate_change_set_title(str(value.get("title", "")))
    editor_session_id = str(value.get("editorSessionId", ""))
    created_at_utc = str(value.get("createdAtUtc", ""))
    updated_at_utc = str(value.get("updatedAtUtc", ""))
    operations_value = value.get("operations")
    if not created_at_utc or not updated_at_utc or not isinstance(operations_value, list):
        raise ValueError("change set journal record invalid")
    if len(editor_session_id) > 128 or len(operations_value) > MAX_CHANGE_SET_RECEIPTS:
        raise ValueError("change set journal bounds invalid")
    operations = [_deserialize_operation(operation) for operation in operations_value]
    record = ChangeSetRecord(
        change_set_id=change_set_id,
        task_id=task_id,
        editor_session_id=editor_session_id,
        title=title,
        status=derive_change_set_status(operations),
        created_at_utc=created_at_utc,
        updated_at_utc=updated_at_utc,
        operations=operations,
    )
    persisted_status = str(value.get("status", record.status))
    if persisted_status not in CHANGE_SET_STATUSES:
        raise ValueError("change set status invalid")
    return record


def register_change_set_tools(
    *,
    server: Any,
    workflow_service: Any,
    read_annotations: Any,
    tool_annotations_type: Any,
    error_response: Any,
) -> None:
    planning_annotations = tool_annotations_type(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )

    @server.tool(annotations=planning_annotations)
    def ue_create_change_set(title: str = "Live Editor Change Set", task_id: str = "") -> dict[str, Any]:
        """Create one bounded journaled Change Set for grouping confirmed live writes."""
        try:
            return workflow_service.create_change_set(title=title, task_id=task_id)
        except (FileNotFoundError, OSError, ValueError, RuntimeError, TypeError) as exc:
            return error_response("ue_create_change_set", exc, read_only=False)

    @server.tool(annotations=read_annotations)
    def ue_get_change_set(change_set_id: str) -> dict[str, Any]:
        """Return the durable lifecycle, operations, assets, transactions, validation, and save state."""
        try:
            return workflow_service.get_change_set(change_set_id)
        except (FileNotFoundError, OSError, ValueError, RuntimeError, TypeError) as exc:
            return error_response("ue_get_change_set", exc, read_only=True)

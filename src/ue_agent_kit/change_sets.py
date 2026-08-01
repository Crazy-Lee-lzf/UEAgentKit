from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CHANGE_SET_SCHEMA_VERSION = "1.0"
MAX_CHANGE_SETS = 50
MAX_CHANGE_SET_RECEIPTS = 100
MAX_CHANGE_SET_ID_LENGTH = 64

_LIVE_RECEIPT_CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"


class ChangeSetError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ChangeSetRecord:
    change_set_id: str
    created_at_utc: str
    receipts: list[str]


def validate_change_set_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("cs_")
        or len(value) <= 3
        or len(value) > MAX_CHANGE_SET_ID_LENGTH
        or any(character not in _LIVE_RECEIPT_CHARS for character in value)
    ):
        raise ChangeSetError(
            "change-set-invalid",
            "changeSetId must be the exact changeSetId returned by ue_create_change_set.",
        )
    return value


def validate_live_receipt_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value.startswith("live_")
        or len(value) > 96
        or any(character not in _LIVE_RECEIPT_CHARS for character in value)
    ):
        raise ValueError("invalid live apply receipt")
    return value


def serialize_change_set_record(record: ChangeSetRecord, project_name: str) -> dict[str, Any]:
    return {
        "schemaVersion": CHANGE_SET_SCHEMA_VERSION,
        "projectName": project_name,
        "changeSetId": record.change_set_id,
        "createdAtUtc": record.created_at_utc,
        "receipts": list(record.receipts),
    }


def deserialize_change_set_record(value: dict[str, Any], project_name: str) -> ChangeSetRecord:
    if value.get("schemaVersion") != CHANGE_SET_SCHEMA_VERSION or value.get("projectName") != project_name:
        raise ValueError("change set journal identity mismatch")
    change_set_id = validate_change_set_id(str(value.get("changeSetId", "")))
    created_at_utc = str(value.get("createdAtUtc", ""))
    receipts_value = value.get("receipts")
    if not created_at_utc or not isinstance(receipts_value, list):
        raise ValueError("change set journal record invalid")
    receipts: list[str] = []
    for receipt in receipts_value:
        try:
            receipts.append(validate_live_receipt_id(str(receipt)))
        except ValueError:
            raise ValueError("change set journal receipt invalid") from None
    if len(receipts) > MAX_CHANGE_SET_RECEIPTS:
        raise ValueError("change set journal receipt count invalid")
    return ChangeSetRecord(change_set_id=change_set_id, created_at_utc=created_at_utc, receipts=receipts)


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
    def ue_create_change_set() -> dict[str, Any]:
        """Create one bounded journaled Change Set for grouping confirmed live writes."""
        try:
            return workflow_service.create_change_set()
        except (FileNotFoundError, OSError, ValueError, RuntimeError, TypeError) as exc:
            return error_response("ue_create_change_set", exc, read_only=False)

    @server.tool(annotations=read_annotations)
    def ue_get_change_set(change_set_id: str) -> dict[str, Any]:
        """Return the bounded membership and per-receipt state of one journaled Change Set."""
        try:
            return workflow_service.get_change_set(change_set_id)
        except (FileNotFoundError, OSError, ValueError, RuntimeError, TypeError) as exc:
            return error_response("ue_get_change_set", exc, read_only=True)

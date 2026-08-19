from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.change_sets import (  # noqa: E402
    CHANGE_SET_SCHEMA_VERSION,
    LEGACY_CHANGE_SET_SCHEMA_VERSION,
    MAX_CHANGE_SET_ID_LENGTH,
    MAX_CHANGE_SET_RECEIPTS,
    ChangeSetError,
    ChangeSetOperationRecord,
    ChangeSetRecord,
    derive_change_set_status,
    deserialize_change_set_record,
    is_terminal_change_set,
    serialize_change_set_record,
    validate_change_set_id,
    validate_change_set_task_id,
    validate_change_set_title,
    validate_live_receipt_id,
)

PROJECT = "TestProject"
CREATED = "2026-08-01T00:00:00Z"
UPDATED = "2026-08-01T00:01:00Z"


def operation(status: str = "applied", receipt: str = "live_first") -> ChangeSetOperationRecord:
    return ChangeSetOperationRecord(
        receipt=receipt,
        plan_id="plan_first",
        asset_path="/Game/Test/DA_Test.DA_Test",
        operation="setAssetProperty",
        transaction_id="11111111-2222-3333-4444-555555555555",
        editor_session_id="session-1",
        status=status,
        created_at_utc=CREATED,
        updated_at_utc=UPDATED,
        save_receipt="save_first" if status in {"saved", "verified"} else "",
    )


def record(operations: list[ChangeSetOperationRecord] | None = None) -> ChangeSetRecord:
    values = operations or []
    return ChangeSetRecord(
        change_set_id="cs_roundtrip",
        task_id="task_roundtrip",
        editor_session_id="session-1",
        title="Round trip",
        status=derive_change_set_status(values),
        created_at_utc=CREATED,
        updated_at_utc=UPDATED,
        operations=values,
    )


class ChangeSetValidationTests(unittest.TestCase):
    def test_change_set_id_accepts_generated_form(self) -> None:
        value = "cs_AbC-123_xyz"
        self.assertEqual(validate_change_set_id(value), value)

    def test_change_set_id_rejects_bad_prefix_length_and_charset(self) -> None:
        for invalid in ("", "cs_", "CS_abc", "abc_123", "cs_ab cd", "cs_" + "a" * (MAX_CHANGE_SET_ID_LENGTH + 1)):
            with self.assertRaises(ChangeSetError) as raised:
                validate_change_set_id(invalid)
            self.assertEqual(raised.exception.code, "change-set-invalid")

    def test_task_id_and_title_are_bounded(self) -> None:
        self.assertEqual(validate_change_set_task_id("task_abc-123"), "task_abc-123")
        self.assertEqual(validate_change_set_title("  Realtime audit  "), "Realtime audit")
        with self.assertRaises(ChangeSetError):
            validate_change_set_task_id("job_abc")
        with self.assertRaises(ChangeSetError):
            validate_change_set_title(" ")

    def test_live_receipt_id_accepts_generated_form(self) -> None:
        value = "live_AbC-123_xyz"
        self.assertEqual(validate_live_receipt_id(value), value)

    def test_live_receipt_id_rejects_bad_form(self) -> None:
        for invalid in ("", "cs_abc", "live_ab cd", "live_" + "a" * 97):
            with self.assertRaises(ValueError):
                validate_live_receipt_id(invalid)

    def test_commandlet_apply_receipt_roundtrips_as_change_set_member(self) -> None:
        source = record([operation("saved", receipt="apply_commandlet")])
        serialized = serialize_change_set_record(source, PROJECT)
        restored = deserialize_change_set_record(serialized, PROJECT)
        self.assertEqual(restored.operations[0].receipt, "apply_commandlet")

    def test_noop_receipt_roundtrips_as_terminal_change_set_member(self) -> None:
        noop = operation("no-op", receipt="noop_expected")
        noop.transaction_id = ""
        noop.save_receipt = ""
        source = record([noop])
        serialized = serialize_change_set_record(source, PROJECT)
        restored = deserialize_change_set_record(serialized, PROJECT)
        self.assertEqual(restored.operations[0].receipt, "noop_expected")
        self.assertEqual(restored.operations[0].transaction_id, "")
        self.assertEqual(restored.status, "no-op")
        self.assertTrue(is_terminal_change_set(restored))

    def test_serialize_deserialize_roundtrip(self) -> None:
        source = record([operation("verified")])
        serialized = serialize_change_set_record(source, PROJECT)
        self.assertEqual(serialized["schemaVersion"], CHANGE_SET_SCHEMA_VERSION)
        self.assertEqual(serialized["status"], "verified")
        restored = deserialize_change_set_record(serialized, PROJECT)
        self.assertEqual(restored, source)
        self.assertTrue(is_terminal_change_set(restored))

    def test_status_derivation_covers_mixed_and_unknown(self) -> None:
        self.assertEqual(derive_change_set_status([]), "planned")
        self.assertEqual(derive_change_set_status([operation("applied")]), "applied")
        self.assertEqual(derive_change_set_status([operation("saved")]), "saved")
        self.assertEqual(derive_change_set_status([operation("verified")]), "verified")
        self.assertEqual(derive_change_set_status([operation("no-op", "noop_first")]), "no-op")
        self.assertEqual(
            derive_change_set_status(
                [operation("verified"), operation("no-op", "noop_second")]
            ),
            "verified",
        )
        self.assertEqual(
            derive_change_set_status([operation("verified"), operation("applied", "live_second")]),
            "partially_applied",
        )
        self.assertEqual(derive_change_set_status([operation("unknown")]), "unknown")
        mixed_terminal = record([operation("verified"), operation("undone", "live_second")])
        self.assertEqual(derive_change_set_status(mixed_terminal.operations), "partially_applied")
        self.assertTrue(is_terminal_change_set(mixed_terminal))
        self.assertFalse(is_terminal_change_set(record()))

    def test_deserialize_rejects_identity_and_unknown_version(self) -> None:
        serialized = serialize_change_set_record(record(), PROJECT)
        with self.assertRaises(ValueError):
            deserialize_change_set_record(serialized, "OtherProject")
        wrong_version = dict(serialized)
        wrong_version["schemaVersion"] = "3.0"
        with self.assertRaises(ValueError):
            deserialize_change_set_record(wrong_version, PROJECT)

    def test_legacy_record_migrates_to_unknown_operations(self) -> None:
        restored = deserialize_change_set_record(
            {
                "schemaVersion": LEGACY_CHANGE_SET_SCHEMA_VERSION,
                "projectName": PROJECT,
                "changeSetId": "cs_legacy",
                "createdAtUtc": CREATED,
                "receipts": ["live_legacy"],
            },
            PROJECT,
        )
        self.assertEqual(restored.task_id, "task_legacy")
        self.assertEqual(restored.status, "unknown")
        self.assertEqual(restored.operations[0].status, "unknown")

    def test_deserialize_rejects_missing_fields(self) -> None:
        with self.assertRaises(ValueError):
            deserialize_change_set_record(
                {
                    "schemaVersion": CHANGE_SET_SCHEMA_VERSION,
                    "projectName": PROJECT,
                    "changeSetId": "cs_missing",
                    "taskId": "task_missing",
                    "title": "Missing timestamps",
                    "createdAtUtc": "",
                    "updatedAtUtc": "",
                    "operations": [],
                },
                PROJECT,
            )

    def test_deserialize_rejects_invalid_member_receipts(self) -> None:
        serialized = serialize_change_set_record(record([operation()]), PROJECT)
        serialized["operations"][0]["receipt"] = "not-a-receipt"
        with self.assertRaises(ValueError):
            deserialize_change_set_record(serialized, PROJECT)

    def test_deserialize_rejects_oversized_membership(self) -> None:
        serialized = serialize_change_set_record(record(), PROJECT)
        serialized["operations"] = [
            serialize_change_set_record(record([operation(receipt=f"live_member_{index}")]), PROJECT)["operations"][0]
            for index in range(MAX_CHANGE_SET_RECEIPTS + 1)
        ]
        with self.assertRaises(ValueError):
            deserialize_change_set_record(serialized, PROJECT)


if __name__ == "__main__":
    unittest.main()

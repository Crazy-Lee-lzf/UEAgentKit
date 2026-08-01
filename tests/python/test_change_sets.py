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
    MAX_CHANGE_SET_ID_LENGTH,
    MAX_CHANGE_SET_RECEIPTS,
    ChangeSetError,
    ChangeSetRecord,
    deserialize_change_set_record,
    serialize_change_set_record,
    validate_change_set_id,
    validate_live_receipt_id,
)

PROJECT = "TestProject"


class ChangeSetValidationTests(unittest.TestCase):
    def test_change_set_id_accepts_generated_form(self) -> None:
        value = "cs_AbC-123_xyz"
        self.assertEqual(validate_change_set_id(value), value)

    def test_change_set_id_rejects_bad_prefix_length_and_charset(self) -> None:
        for invalid in ("", "cs_", "CS_abc", "abc_123", "cs_ab cd", "cs_" + "a" * (MAX_CHANGE_SET_ID_LENGTH + 1)):
            with self.assertRaises(ChangeSetError) as raised:
                validate_change_set_id(invalid)
            self.assertEqual(raised.exception.code, "change-set-invalid")

    def test_live_receipt_id_accepts_generated_form(self) -> None:
        value = "live_AbC-123_xyz"
        self.assertEqual(validate_live_receipt_id(value), value)

    def test_live_receipt_id_rejects_bad_form(self) -> None:
        for invalid in ("", "cs_abc", "live_ab cd", "live_" + "a" * 97):
            with self.assertRaises(ValueError):
                validate_live_receipt_id(invalid)

    def test_serialize_deserialize_roundtrip(self) -> None:
        record = ChangeSetRecord(
            change_set_id="cs_roundtrip",
            created_at_utc="2026-08-01T00:00:00Z",
            receipts=["live_first", "live_second"],
        )
        restored = deserialize_change_set_record(
            serialize_change_set_record(record, PROJECT),
            PROJECT,
        )
        self.assertEqual(restored, record)

    def test_deserialize_rejects_identity_mismatch(self) -> None:
        record = ChangeSetRecord(
            change_set_id="cs_identity",
            created_at_utc="2026-08-01T00:00:00Z",
            receipts=[],
        )
        serialized = serialize_change_set_record(record, PROJECT)
        with self.assertRaises(ValueError):
            deserialize_change_set_record(serialized, "OtherProject")
        wrong_version = dict(serialized)
        wrong_version["schemaVersion"] = "2.0"
        with self.assertRaises(ValueError):
            deserialize_change_set_record(wrong_version, PROJECT)

    def test_deserialize_rejects_missing_fields(self) -> None:
        with self.assertRaises(ValueError):
            deserialize_change_set_record(
                {
                    "schemaVersion": CHANGE_SET_SCHEMA_VERSION,
                    "projectName": PROJECT,
                    "changeSetId": "cs_missing",
                    "createdAtUtc": "",
                    "receipts": [],
                },
                PROJECT,
            )

    def test_deserialize_rejects_invalid_member_receipts(self) -> None:
        with self.assertRaises(ValueError):
            deserialize_change_set_record(
                {
                    "schemaVersion": CHANGE_SET_SCHEMA_VERSION,
                    "projectName": PROJECT,
                    "changeSetId": "cs_members",
                    "createdAtUtc": "2026-08-01T00:00:00Z",
                    "receipts": ["not-a-receipt"],
                },
                PROJECT,
            )

    def test_deserialize_rejects_oversized_membership(self) -> None:
        with self.assertRaises(ValueError):
            deserialize_change_set_record(
                {
                    "schemaVersion": CHANGE_SET_SCHEMA_VERSION,
                    "projectName": PROJECT,
                    "changeSetId": "cs_oversized",
                    "createdAtUtc": "2026-08-01T00:00:00Z",
                    "receipts": [f"live_member_{index}" for index in range(MAX_CHANGE_SET_RECEIPTS + 1)],
                },
                PROJECT,
            )


if __name__ == "__main__":
    unittest.main()

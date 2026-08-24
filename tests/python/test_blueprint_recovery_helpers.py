from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

TOOL_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = TOOL_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.agent_workflow import LiveApplyRecord, _live_write_blueprint_exported_value  # noqa: E402
from ue_agent_kit.semantic_diff_workflow import _blueprint_value  # noqa: E402


def _record(operation: str, target: dict[str, Any], after_value: Any = None) -> LiveApplyRecord:
    return LiveApplyRecord(
        receipt="receipt",
        plan_id="plan",
        plan_digest="digest",
        asset_path="/Game/Test.Test",
        operation=operation,
        value_kind="blueprint",
        editor_session_id="session",
        transaction_id="transaction",
        before_value=0,
        after_value=after_value,
        target=target,
        applied_at_utc="2026-08-24T00:00:00Z",
    )


class BlueprintRecoveryHelperTests(unittest.TestCase):
    def test_component_nested_struct_literal_is_extracted(self) -> None:
        canonical: dict[str, Any] = {
            "components": [
                {
                    "name": "DefaultSceneRoot",
                    "id": "c0",
                    "templateOverrides": {
                        "RelativeLocation": "(X=10.000000,Y=0.000000,Z=0.000000)",
                    },
                }
            ]
        }
        record = _record(
            "setComponentProperty",
            {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        )
        self.assertEqual(_live_write_blueprint_exported_value(canonical, record), "10.000000")

    def test_component_missing_nested_field_returns_none(self) -> None:
        canonical: dict[str, Any] = {
            "components": [
                {
                    "name": "DefaultSceneRoot",
                    "id": "c0",
                    "templateOverrides": {
                        "RelativeLocation": "(X=0.000000,Y=0.000000,Z=0.000000)",
                    },
                }
            ]
        }
        record = _record(
            "setComponentProperty",
            {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.W"},
        )
        self.assertIsNone(_live_write_blueprint_exported_value(canonical, record))

    def test_semantic_diff_blueprint_value_parses_component_nested_path(self) -> None:
        canonical: dict[str, Any] = {
            "components": [
                {
                    "name": "DefaultSceneRoot",
                    "id": "c0",
                    "templateOverrides": {
                        "RelativeLocation": "(X=10.000000,Y=0.000000,Z=0.000000)",
                    },
                }
            ]
        }
        found, value, kind, metadata = _blueprint_value(
            canonical,
            "setComponentProperty",
            {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation.X"},
        )
        self.assertTrue(found)
        self.assertEqual(value, "10.000000")
        self.assertEqual(kind, "component-property")
        self.assertEqual(metadata.get("componentId"), "c0")

    def test_semantic_diff_blueprint_value_keeps_parent_unexpected_only_when_no_nested_expected(self) -> None:
        # The workflow's snapshot scan should not treat a parent struct field as an
        # unexpected change when a nested subfield is the expected live write path.
        # This test guards the parser used by that scan, not the scan itself.
        canonical: dict[str, Any] = {
            "components": [
                {
                    "name": "DefaultSceneRoot",
                    "id": "c0",
                    "templateOverrides": {
                        "RelativeLocation": "(X=10.000000,Y=0.000000,Z=0.000000)",
                    },
                }
            ]
        }
        found, value, _, _ = _blueprint_value(
            canonical,
            "setComponentProperty",
            {"componentName": "DefaultSceneRoot", "propertyPath": "RelativeLocation"},
        )
        self.assertTrue(found)
        self.assertEqual(value, "(X=10.000000,Y=0.000000,Z=0.000000)")


if __name__ == "__main__":
    unittest.main()
"""0-3.4: the shared envelope unwrapper used by every experimental writer."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.experimental_envelope import unwrap_tool_result  # noqa: E402

PAYLOAD = {"planId": "sha256:" + "a" * 64, "applied": True}


class UnwrapTests(unittest.TestCase):
    def test_returns_a_raw_payload_unchanged(self) -> None:
        self.assertIs(unwrap_tool_result(PAYLOAD, "planId"), PAYLOAD)

    def test_unwraps_a_tool_envelope(self) -> None:
        envelope = {
            "schemaVersion": "1.0",
            "tool": "ue_preview_something",
            "ok": True,
            "result": PAYLOAD,
        }
        self.assertIs(unwrap_tool_result(envelope, "planId"), PAYLOAD)
        self.assertIs(unwrap_tool_result(envelope, "applied"), PAYLOAD)

    def test_uses_each_marker_independently(self) -> None:
        # A dict that only carries one marker is treated as a payload for that
        # marker and as a wrapper for the other.
        preview_only = {"planId": "sha256:" + "b" * 64, "result": PAYLOAD}
        self.assertIs(unwrap_tool_result(preview_only, "planId"), preview_only)
        self.assertIs(unwrap_tool_result(preview_only, "applied"), PAYLOAD)

    def test_leaves_unrecognisable_values_alone(self) -> None:
        for value in ({}, {"result": {"nope": 1}}, {"result": "text"}, [], None, 3, "x"):
            with self.subTest(value=value):
                self.assertEqual(unwrap_tool_result(value, "planId"), value)

    def test_non_dict_is_returned_as_is(self) -> None:
        sentinel = ["not", "a", "dict"]
        self.assertIs(unwrap_tool_result(sentinel, "planId"), sentinel)


if __name__ == "__main__":
    unittest.main()

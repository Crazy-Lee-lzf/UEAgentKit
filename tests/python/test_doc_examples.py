"""Doc-example regressions for DEF-06 / DEF-08 / DEF-09.

These read the shipped user documentation and check it against the real
validator constants, so an example that the plugin would reject fails a test
instead of a user's first attempt.
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.experimental_graph_edit import (  # noqa: E402
    CALL_FUNCTION_NODE_TYPE,
    NODE_TYPES,
    OWNED_CALL_NODE_TYPE,
    VARIABLE_NODE_TYPES,
    _TEMP_ID,
)

E2E_DOC = ROOT / "docs" / "BLUEPRINT_AGENT_E2E_USAGE.md"


def _json_blocks(text: str) -> list[str]:
    return re.findall(r"```json\n(.*?)```", text, re.DOTALL)


class GraphEditDocExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.text = E2E_DOC.read_text(encoding="utf-8")

    def test_every_documented_node_example_uses_valid_temp_ids_and_types(self) -> None:
        blocks = _json_blocks(self.text)
        self.assertTrue(blocks)
        checked = 0
        allowed_types = (
            set(NODE_TYPES)
            | set(VARIABLE_NODE_TYPES)
            | {CALL_FUNCTION_NODE_TYPE, OWNED_CALL_NODE_TYPE}
        )
        for block in blocks:
            try:
                payload = json.loads(block)
            except json.JSONDecodeError:
                continue
            arguments = payload.get("arguments") if isinstance(payload, dict) else None
            if not isinstance(arguments, dict):
                continue
            if "operations" in arguments:
                # Slice 17: node creation is a createNode kind of the atomic
                # patch; the example must still use a real nodeType token.
                for operation in arguments["operations"]:
                    if operation.get("kind") != "createNode":
                        continue
                    checked += 1
                    self.assertIn(operation.get("nodeType"), allowed_types)
                    self.assertNotIn("nodeClass", operation)
                continue
            if "nodes" not in arguments:
                continue
            checked += 1
            for node in arguments["nodes"]:
                temp_id = node.get("tempId")
                self.assertIsInstance(temp_id, str)
                self.assertIsNotNone(
                    _TEMP_ID.fullmatch(temp_id),
                    f"documented tempId is not canonical: {temp_id!r}",
                )
                self.assertIn(node.get("nodeType"), allowed_types)
                self.assertNotIn("nodeClass", node)
            ids = {node["tempId"] for node in arguments["nodes"]}
            for connection in arguments.get("connections", []):
                self.assertIn(connection["from"], ids)
                self.assertIn(connection["to"], ids)
        self.assertGreaterEqual(checked, 1)

    def test_ini_switch_is_documented_in_the_engine_config_layer(self) -> None:
        # The plugin reads ExperimentalGraphPatchEdit through GEngineIni.
        self.assertIn("Config/DefaultEngine.ini", self.text)
        self.assertNotIn("Config/DefaultEditor.ini", self.text)

    def test_write_cli_example_carries_the_three_required_paths(self) -> None:
        for flag in ("--engine-root", "--policy", "--revision-export"):
            self.assertIn(flag, self.text, flag)


if __name__ == "__main__":
    unittest.main()

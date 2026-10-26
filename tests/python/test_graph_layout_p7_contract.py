from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ue_agent_kit.mcp_graph_layout_test_only import register_graph_layout_test_only_tool  # noqa: E402
from ue_agent_kit.p7_test_only import (  # noqa: E402
    APPLY_TOOL_NAME,
    FIXED_ASSET_PATH,
    FIXED_PROJECT_FILE_SHA256,
    FIXED_PROJECT_PATH,
    confirmation_for_plan,
    is_fixed_project,
)
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_DEFINITIONS_BY_NAME,
    tool_descriptors_for_mode,
)


class GraphLayoutP7ContractTest(unittest.TestCase):
    def test_p7_tool_is_not_in_the_global_registry(self) -> None:
        self.assertNotIn(APPLY_TOOL_NAME, TOOL_DEFINITIONS_BY_NAME)
        names = {
            item["name"]
            for item in tool_descriptors_for_mode(live_editor_enabled=True, workflow_enabled=True)
        }
        self.assertNotIn(APPLY_TOOL_NAME, names)
        self.assertNotIn("ue_apply_blueprint_graph_layout", names)

    def test_p7_asset_and_confirmation_are_exact(self) -> None:
        self.assertEqual(FIXED_ASSET_PATH, "/Game/UEAgentKitP7/BP_LayoutP7.BP_LayoutP7")
        self.assertEqual(confirmation_for_plan("layout:abc"), "APPLY P7 TEST-ONLY layout:abc")
        self.assertEqual(
            FIXED_PROJECT_FILE_SHA256,
            "20f86465e2296f232be047c22f7924b24be6c62fd3eeaf13f8c52936964211c2",
        )

    def test_p7_project_gate_rejects_other_projects(self) -> None:
        # The gate must only accept the checkout-relative disposable host; any
        # other synthetic path, including a differently named sibling project,
        # is rejected without needing a machine-specific absolute path.
        fake_root = Path("tests/fixtures/graph_layout_fake")
        self.assertFalse(
            is_fixed_project(
                fake_root / "P6AutomationHost" / "P6AutomationHost.uproject"
            )
        )
        self.assertFalse(
            is_fixed_project(fake_root / "P7AutomationHost" / "NotP7.uproject")
        )
        self.assertFalse(is_fixed_project(fake_root / "does-not-exist.uproject"))
        self.assertEqual(
            FIXED_PROJECT_PATH,
            (ROOT / "Build" / "P7AutomationHost" / "P7AutomationHost.uproject").resolve(),
        )

    def test_p7_register_function_is_conditional_explicit(self) -> None:
        self.assertTrue(callable(register_graph_layout_test_only_tool))
        source = (ROOT / "src/ue_agent_kit/mcp_server.py").read_text(encoding="utf-8")
        self.assertIn("p7_test_only_graph_layout_apply", source)
        self.assertIn("register_graph_layout_test_only_tool(", source)
        self.assertIn("if p7_test_only_graph_layout_apply:", source)
        tool_source = (ROOT / "src/ue_agent_kit/mcp_graph_layout_test_only.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("destructiveHint=True", tool_source)
        self.assertIn("readOnlyHint=False", tool_source)


if __name__ == "__main__":
    unittest.main()

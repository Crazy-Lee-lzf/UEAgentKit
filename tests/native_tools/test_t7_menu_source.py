"""Static T7 menu-binding guards (offline; UE runtime remains PENDING).

These checks do not prove the UE menu works.  They keep the implementation
from silently growing a node-creation/Apply path while the real UE automation
test is unavailable.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BINDING = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/Native/NativeGraphMenuBinding.cpp"
HEADER = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/Native/NativeGraphMenuBinding.h"
MODULE = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/UEAgentKitEditorModule.cpp"
TEST = ROOT / "Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/Native/NativeGraphMenuTests.cpp"


class T7MenuSourceGuardTests(unittest.TestCase):
    def test_menu_is_read_only_dispatch(self) -> None:
        source = BINDING.read_text(encoding="utf-8")
        self.assertIn("TryInvokeShadowTool", source)
        self.assertIn("graph.nodeRegistry.catalog", source)
        for forbidden in (
            "SpawnNode",
            "CreateNode",
            "Modify(",
            "SavePackage",
            "BeginTransaction",
            "FScopedTransaction",
        ):
            self.assertNotIn(forbidden, source)

    def test_off_shadow_do_not_register_and_active_is_gated(self) -> None:
        header = HEADER.read_text(encoding="utf-8")
        source = BINDING.read_text(encoding="utf-8")
        module = MODULE.read_text(encoding="utf-8")
        self.assertIn("InMode == ENativeRegistryMode::Active", source)
        self.assertIn("IsModeEligible(NativeRegistry.GetMode())", module)
        self.assertIn("NativeRegistry.IsAvailable()", module)
        self.assertNotIn("NativePreviewSettings", source)
        self.assertIn("Node != nullptr || Context->Pin != nullptr", source)
        self.assertIn("AddDynamicSection", source)
        self.assertIn("RemoveSection", header + source)

    def test_each_mode_transition_shuts_down_first(self) -> None:
        test = TEST.read_text(encoding="utf-8")
        self.assertIn("const bool bWasInitialized = Registry.IsInitialized()", test)
        self.assertIn("if (bWasInitialized)", test)
        for mode in ("Shadow", "Active"):
            self.assertRegex(
                test,
                re.compile(
                    r"Registry\.Shutdown\(\);\s*\n\s*"
                    r"Registry\.Initialize\(ENativeRegistryMode::" + mode + r"\)"
                ),
                f"{mode} transition must call Shutdown before Initialize",
            )
        self.assertIn("shadow mode state is Ready", test)
        self.assertIn("active mode state is Ready", test)

    def test_shutdown_and_context_tests_exist(self) -> None:
        test = TEST.read_text(encoding="utf-8")
        self.assertIn("IsMenuRegistered", test)
        for token in (
            "off mode leaves the legacy menu unchanged",
            "shadow mode leaves the legacy menu unchanged",
            "blank canvas has no native section",
            "shutdown removes the native section",
            "unknown native menu action fails closed",
            "catalog native menu action succeeds",
        ):
            self.assertIn(token, test)


if __name__ == "__main__":
    unittest.main()

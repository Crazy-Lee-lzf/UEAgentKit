"""Tool profile contract tests (A0 / P0).

A profile is an upper bound on the tool groups one session exposes.  It must
never enable a group the run flags left off, and it must never hide a tool the
registry does not know about (experimental surfaces, legacy helpers).
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from ue_agent_kit.mcp_server import (  # noqa: E402
    _ProfileFilteredServer,
    build_parser,
)
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_PROFILE_GROUPS,
    TOOL_PROFILE_NAMES,
    profile_groups,
    tool_names_for_profile,
    tool_profile_allows,
)


class ToolProfileRegistryTests(unittest.TestCase):
    def test_profiles_are_named_in_escalation_order(self) -> None:
        self.assertEqual(
            TOOL_PROFILE_NAMES, ("core", "read", "live", "live-write", "full")
        )
        self.assertEqual(set(TOOL_PROFILE_GROUPS), set(TOOL_PROFILE_NAMES))

    def test_unknown_profile_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            profile_groups("everything")

    def test_profiles_are_nested(self) -> None:
        ordered = [profile_groups(name) for name in TOOL_PROFILE_NAMES]
        for narrower, wider in zip(ordered, ordered[1:]):
            self.assertTrue(narrower <= wider, f"{narrower} is not inside {wider}")

    def test_profile_counts_match_the_registry(self) -> None:
        everything = dict(
            live_editor_enabled=True,
            workflow_enabled=True,
            memory_enabled=True,
            source_control_enabled=True,
        )
        expected = {"core": 15, "read": 31, "live": 69, "live-write": 79, "full": 149}
        for name, count in expected.items():
            self.assertEqual(
                len(tool_names_for_profile(name, **everything)),
                count,
                f"profile {name} exposes a different number of tools",
            )

    def test_core_is_query_only(self) -> None:
        names = tool_names_for_profile("core", live_editor_enabled=True)
        self.assertIn("ue_get_capabilities", names)
        self.assertNotIn("ue_get_pie_state", names)
        self.assertNotIn("ue_memory_get", names)

    def test_unknown_tool_names_stay_visible(self) -> None:
        # Tools registered outside the registry (experimental surfaces) must not
        # disappear because a profile cannot classify them.
        self.assertTrue(tool_profile_allows("core", "ue_focus_blueprint_graph"))


class ProfileFilteredServerTests(unittest.TestCase):
    class _FakeServer:
        def __init__(self) -> None:
            self.registered: list[str] = []

        def tool(self, *args: object, **kwargs: object):
            def decorate(func):
                self.registered.append(func.__name__)
                return func

            return decorate

    def test_core_hides_live_tools_but_keeps_query(self) -> None:
        fake = self._FakeServer()
        target = _ProfileFilteredServer(fake, "core")

        def ue_get_capabilities() -> None:  # pragma: no cover - not called
            pass

        def ue_get_pie_state() -> None:  # pragma: no cover - not called
            pass

        target.tool()(ue_get_capabilities)
        target.tool()(ue_get_pie_state)

        self.assertEqual(fake.registered, ["ue_get_capabilities"])

    def test_full_still_hides_replaced_tools(self) -> None:
        fake = self._FakeServer()
        target = _ProfileFilteredServer(fake, "full")

        def ue_get_pie_state() -> None:  # pragma: no cover - not called
            pass

        def ue_pie() -> None:  # pragma: no cover - not called
            pass

        target.tool()(ue_get_pie_state)
        target.tool()(ue_pie)
        self.assertEqual(fake.registered, ["ue_pie"])


class CliProfileTests(unittest.TestCase):
    def test_default_profile_is_core(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.tool_profile, "core")

    def test_profile_can_be_widened_from_the_cli(self) -> None:
        args = build_parser().parse_args(["--tool-profile", "full"])
        self.assertEqual(args.tool_profile, "full")


if __name__ == "__main__":
    unittest.main()

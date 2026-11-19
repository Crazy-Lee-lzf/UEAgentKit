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
    _GroupFilteredServer,
    build_parser,
)
from ue_agent_kit.tool_registry import (  # noqa: E402
    TOOL_GROUP_NAMES,
    TOOL_PROFILE_GROUPS,
    TOOL_PROFILE_NAMES,
    parse_tool_groups,
    profile_groups,
    resolve_tool_selection,
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
        expected = {"core": 15, "read": 31, "live": 69, "live-write": 79, "full": 155}
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
        target = _GroupFilteredServer(fake, profile_groups("core"))

        def ue_get_capabilities() -> None:  # pragma: no cover - not called
            pass

        def ue_get_pie_state() -> None:  # pragma: no cover - not called
            pass

        target.tool()(ue_get_capabilities)
        target.tool()(ue_get_pie_state)

        self.assertEqual(fake.registered, ["ue_get_capabilities"])

    def test_full_still_hides_replaced_tools(self) -> None:
        fake = self._FakeServer()
        target = _GroupFilteredServer(fake, profile_groups("full"))

        def ue_get_pie_state() -> None:  # pragma: no cover - not called
            pass

        def ue_pie() -> None:  # pragma: no cover - not called
            pass

        target.tool()(ue_get_pie_state)
        target.tool()(ue_pie)
        self.assertEqual(fake.registered, ["ue_pie"])

    def test_a_custom_selection_accepts_groups_no_profile_offers(self) -> None:
        # query + workflow is unreachable through any preset; an explicit list is
        # the whole point of --tool-groups.
        fake = self._FakeServer()
        target = _GroupFilteredServer(fake, parse_tool_groups("query,workflow"))

        def ue_get_capabilities() -> None:  # pragma: no cover - not called
            pass

        def ue_apply_blueprint_patch() -> None:  # pragma: no cover - not called
            pass

        def ue_set_property() -> None:  # pragma: no cover - not called
            pass

        def ue_get_pie_state() -> None:  # pragma: no cover - not called
            pass

        target.tool()(ue_get_capabilities)
        target.tool()(ue_apply_blueprint_patch)
        target.tool()(ue_set_property)
        target.tool()(ue_get_pie_state)
        self.assertEqual(
            fake.registered, ["ue_get_capabilities", "ue_apply_blueprint_patch", "ue_set_property"]
        )


class ToolGroupSelectionTests(unittest.TestCase):
    def test_every_profiles_groups_are_named(self) -> None:
        self.assertEqual(
            set(TOOL_GROUP_NAMES),
            {"query", "memory", "realtime", "live-read", "live-action", "workflow", "source-control"},
        )
        for name in TOOL_PROFILE_NAMES:
            self.assertLessEqual(profile_groups(name), set(TOOL_GROUP_NAMES))

    def test_groups_parse_ignores_case_spacing_and_duplicates(self) -> None:
        self.assertEqual(
            parse_tool_groups(" Query , live-read ,, QUERY "),
            frozenset({"query", "live-read"}),
        )

    def test_unknown_group_is_rejected(self) -> None:
        with self.assertRaises(ValueError) as caught:
            parse_tool_groups("query,nope")
        self.assertIn("nope", str(caught.exception))

    def test_empty_group_list_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_tool_groups("  ,  ")

    def test_groups_win_over_the_profile(self) -> None:
        self.assertEqual(
            resolve_tool_selection("full", "query"), frozenset({"query"})
        )
        self.assertEqual(resolve_tool_selection("read"), profile_groups("read"))
        self.assertIsNone(resolve_tool_selection(None))


class CliProfileTests(unittest.TestCase):
    def test_default_profile_is_core(self) -> None:
        args = build_parser().parse_args([])
        self.assertEqual(args.tool_profile, "core")
        self.assertIsNone(args.tool_groups)

    def test_profile_can_be_widened_from_the_cli(self) -> None:
        args = build_parser().parse_args(["--tool-profile", "full"])
        self.assertEqual(args.tool_profile, "full")

    def test_groups_can_be_selected_from_the_cli(self) -> None:
        args = build_parser().parse_args(["--tool-groups", "query,memory"])
        self.assertEqual(args.tool_groups, "query,memory")


if __name__ == "__main__":
    unittest.main()

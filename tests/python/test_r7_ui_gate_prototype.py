"""R7 scoped Blueprint UI gate prototype — static contract tests.

The prototype is test-only C++ and has never been compiled or run natively, so
these tests pin exactly the properties that must hold *before* anyone enables
it, and they must never claim a native PASS:

* the whole gate is compiled only under ``WITH_DEV_AUTOMATION_TESTS``;
* it disables exactly one graph widget, never the Editor, never another tab and
  never a global Slate input hook;
* it holds the widget weakly, restores the exact previous enabled attribute,
  and reports ``Lost`` instead of claiming isolation after a rebuild;
* arming happens only from a test-only console command, never from a request
  path, and it refuses when the target graph is not already focused;
* the user-visible ``exclusiveUI`` / ``humanEditingBlocked`` stay false and
  ``requiresUserNotEdit`` stays true;
* the existing token/fingerprint/transaction protections are untouched.

The prototype changing anything above must fail here.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / "Plugin" / "UEAgentKit" / "Source" / "UEAgentKitEditor" / "Private"
SRC = ROOT / "src" / "ue_agent_kit"

GATE_H = PRIVATE / "BlueprintEditSessionUIGate.h"
GATE_CPP = PRIVATE / "BlueprintEditSessionUIGate.cpp"
SESSION_CPP = PRIVATE / "BlueprintEditSession.cpp"
MODULE_CPP = PRIVATE / "UEAgentKitEditorModule.cpp"
BRIDGE_CPP = PRIVATE / "EditorBridge.cpp"
SESSION_HANDLER = PRIVATE / "EditorBridgeBlueprintEditSession.cpp"
WORKFLOW = SRC / "mcp_workflow_tools.py"


def _strip_cpp(source: str) -> str:
    """Remove comments and string/char literals so brace counting is honest."""
    out: list[str] = []
    index = 0
    length = len(source)
    while index < length:
        char = source[index]
        if char == "/" and index + 1 < length and source[index + 1] == "/":
            while index < length and source[index] != "\n":
                index += 1
            continue
        if char == "/" and index + 1 < length and source[index + 1] == "*":
            index += 2
            while index + 1 < length and not (
                source[index] == "*" and source[index + 1] == "/"
            ):
                index += 1
            index += 2
            continue
        if char in "\"'":
            quote = char
            index += 1
            while index < length:
                if source[index] == "\\":
                    index += 2
                    continue
                if source[index] == quote:
                    index += 1
                    break
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


class UIGateGuardPlacementTests(unittest.TestCase):
    def test_gate_files_exist(self) -> None:
        self.assertTrue(GATE_H.is_file())
        self.assertTrue(GATE_CPP.is_file())

    def test_header_class_is_automation_only(self) -> None:
        header = GATE_H.read_text(encoding="utf-8")
        guard = header.index("#if WITH_DEV_AUTOMATION_TESTS")
        class_start = header.index("class FBlueprintEditSessionUIGate")
        self.assertLess(guard, class_start)
        # The guard must close at the very end of the header.
        self.assertTrue(header.rstrip().endswith("#endif // WITH_DEV_AUTOMATION_TESTS"))
        self.assertEqual(header.count("#if WITH_DEV_AUTOMATION_TESTS"), 1)

    def test_implementation_is_automation_only(self) -> None:
        source = GATE_CPP.read_text(encoding="utf-8")
        lines = [line.strip() for line in source.splitlines() if line.strip()]
        self.assertEqual(lines[0], '#include "BlueprintEditSessionUIGate.h"')
        self.assertEqual(lines[1], "#if WITH_DEV_AUTOMATION_TESTS")
        self.assertEqual(lines[-1], "#endif // WITH_DEV_AUTOMATION_TESTS")
        self.assertEqual(source.count("#if WITH_DEV_AUTOMATION_TESTS"), 1)

    def test_every_manager_hook_is_guarded(self) -> None:
        for path in (SESSION_CPP, MODULE_CPP):
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"FBlueprintEditSessionUIGate::Get\(\)", text):
                before = text[: match.start()]
                last_if = before.rfind("#if WITH_DEV_AUTOMATION_TESTS")
                last_endif = before.rfind("#endif")
                self.assertGreater(last_if, last_endif, path.name)


class UIGateScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.header = GATE_H.read_text(encoding="utf-8")
        self.gate = GATE_CPP.read_text(encoding="utf-8")
        self.session = SESSION_CPP.read_text(encoding="utf-8")

    def test_no_global_input_hook_anywhere(self) -> None:
        for path in PRIVATE.rglob("*.cpp"):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for forbidden in (
                "RegisterInputPreProcessor",
                "UnregisterInputPreProcessor",
                "IInputProcessor",
            ):
                if forbidden in text:
                    self.fail(f"{forbidden} must not be registered ({path.name})")

    def test_only_one_widget_is_disabled(self) -> None:
        # Exactly one disable call, and it targets the resolved graph widget.
        disabled = re.findall(r"SetEnabled\(\s*false\s*\)", self.gate)
        self.assertEqual(len(disabled), 1)
        self.assertIn("GraphWidget->SetEnabled(false);", self.gate)
        # No other plugin source may toggle enabled state at all: that is what
        # would turn a scoped gate into an Editor-wide lock.
        users = [
            path.name
            for path in PRIVATE.rglob("*.cpp")
            if "SetEnabled(" in path.read_text(encoding="utf-8", errors="ignore")
        ]
        self.assertEqual(users, ["BlueprintEditSessionUIGate.cpp"])

    def test_widget_is_held_weakly(self) -> None:
        self.assertIn("TWeakPtr<SGraphEditor> Widget;", self.header)
        self.assertIn("TWeakObjectPtr<UBlueprint> Blueprint;", self.header)
        self.assertIn("TWeakObjectPtr<UEdGraph> Graph;", self.header)
        # No raw owning pointer member for the widget.
        self.assertNotIn("SGraphEditor* Widget", self.header)
        self.assertNotIn("SGraphEditor* GraphWidget;", self.header)

    def test_exact_enabled_state_is_captured_and_restored(self) -> None:
        self.assertIn("GetEnabledStateAttribute()", self.gate)
        self.assertIn("ToAttribute()", self.gate)
        self.assertIn("Pinned->SetEnabled(SavedEnabledState);", self.gate)
        # A bound getter must not be frozen into a bool.
        self.assertIn("IsBound()", self.gate)

    def test_arm_refuses_without_proof(self) -> None:
        for reason in (
            "no-active-session-target",
            "blueprint-editor-not-open",
            "blueprint-editor-mismatch",
            "graph-not-focused",
            "graph-widget-unavailable",
            "graph-widget-already-disabled",
            "graph-widget-disable-unverified",
        ):
            self.assertIn(reason, self.gate)

    def test_loss_is_reported_not_hidden(self) -> None:
        for reason in ("widget-destroyed", "graph-changed", "widget-reenabled"):
            self.assertIn(reason, self.gate)
        self.assertIn("EUIGateState::Lost", self.gate)
        # Query must re-validate rather than trust the cached arm state.
        self.assertIn("Pinned.IsValid()", self.gate)
        self.assertIn("GetCurrentGraph() == Graph.Get()", self.gate)

    def test_no_transaction_or_package_mutation_in_the_gate(self) -> None:
        for forbidden in (
            "FScopedTransaction",
            "->Modify(",
            "MarkPackageDirty",
            "SavePackage",
            "MarkBlueprintAsModified",
        ):
            self.assertNotIn(forbidden, self.gate)
            self.assertNotIn(forbidden, self.header)


class UIGateLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SESSION_CPP.read_text(encoding="utf-8")
        self.module = MODULE_CPP.read_text(encoding="utf-8")
        self.gate = GATE_CPP.read_text(encoding="utf-8")

    def test_release_paths_are_traceable(self) -> None:
        # begin records the target, batch observes, end/reset/shutdown release.
        begin = self.session.index("SetSessionTarget(Blueprint, Graph)")
        self.assertGreater(begin, self.session.index("bool FBlueprintEditSessionManager::Begin"))
        self.assertLess(begin, self.session.index("bool FBlueprintEditSessionManager::ValidateBatch"))

        reset = self.session.index("FBlueprintEditSessionManager::Reset()")
        reset_release = self.session.index("ClearSessionTarget()", reset)
        self.assertLess(reset_release, self.session.index("Impl->bActive = false;", reset))

        end = self.session.index("bool FBlueprintEditSessionManager::End(")
        end_release = self.session.index("ClearSessionTarget()", end)
        self.assertLess(end_release, self.session.index("Impl->bActive = false;", end))

        shutdown = self.module.index("ShutdownModule()")
        shutdown_release = self.module.index("ClearSessionTarget()", shutdown)
        self.assertLess(shutdown_release, self.module.index("if (GraphLayoutP11Menu.IsValid())", shutdown))

    def test_batch_only_observes_never_gates(self) -> None:
        batch = self.session.index("bool FBlueprintEditSessionManager::ValidateBatch(")
        region = self.session[batch:]
        self.assertIn("FBlueprintEditSessionUIGate::Get().Query();", region)
        # The gate must not be able to refuse or mutate a batch.
        observe = region.index("FBlueprintEditSessionUIGate::Get().Query();")
        self.assertNotIn("FBlueprintEditSessionUIGate::Get().Arm(", region)
        self.assertLess(observe, region.index("Impl->Blueprint.Get() != Blueprint"))

    def test_no_request_path_arms_the_gate(self) -> None:
        for path in (BRIDGE_CPP, SESSION_HANDLER, WORKFLOW):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("ArmUIGate", text, path.name)
            self.assertNotIn("FBlueprintEditSessionUIGate", text, path.name)
        # Arm is only reachable from the console command wrapper.
        self.assertEqual(self.gate.count(".Arm("), 1)

    def test_console_commands_are_test_only(self) -> None:
        for name in ("ArmUIGate", "ReleaseUIGate", "UIGateStatus"):
            self.assertIn(f'"UEAgentKit.BlueprintEditSession.{name}"', self.gate)
        # They live inside the same WITH_DEV_AUTOMATION_TESTS translation unit.
        self.assertIn("#if WITH_DEV_AUTOMATION_TESTS", self.gate)


class UIGateHonestyTests(unittest.TestCase):
    def test_user_visible_booleans_stay_false(self) -> None:
        handler = SESSION_HANDLER.read_text(encoding="utf-8")
        self.assertIn('TEXT("exclusiveUI"), false', handler)
        self.assertIn('TEXT("humanEditingBlocked"), false', handler)
        self.assertIn('TEXT("requiresUserNotEdit"), true', handler)
        self.assertIn('TEXT("rollbackSupported"), true', handler)

    def test_gate_never_touches_response_booleans(self) -> None:
        # Comments may explain the honest fields; only code must stay free of
        # them, so strip comments and literals first.
        gate = _strip_cpp(GATE_CPP.read_text(encoding="utf-8")) + _strip_cpp(
            GATE_H.read_text(encoding="utf-8")
        )
        for token in ("exclusiveUI", "humanEditingBlocked", "requiresUserNotEdit"):
            if token in gate:
                self.fail(f"the UI gate must not touch the response field {token}")

    def test_existing_session_protections_unchanged(self) -> None:
        session = SESSION_CPP.read_text(encoding="utf-8")
        # Token protocol, fingerprint proof and case-sensitive binding must keep
        # working exactly as in the natively accepted rounds.
        self.assertIn("IsCanonicalEditSessionToken(Impl->Token)", session)
        self.assertIn("ComputeGraphContentFingerprint", session)
        self.assertIn("Token.Equals(Impl->Token, ESearchCase::CaseSensitive)", session)
        self.assertIn("blueprint-edit-session-package-dirty", session)
        self.assertIn("AllowSessionOwnedDirty", session)


class UIGateSyntaxSanityTests(unittest.TestCase):
    """Cheap structural checks; they are not a substitute for compiling."""

    def test_braces_and_parentheses_balance(self) -> None:
        for path in (GATE_H, GATE_CPP, SESSION_CPP, MODULE_CPP):
            stripped = _strip_cpp(path.read_text(encoding="utf-8"))
            self.assertEqual(
                stripped.count("{"), stripped.count("}"), f"unbalanced braces: {path.name}"
            )
            self.assertEqual(
                stripped.count("("), stripped.count(")"), f"unbalanced parens: {path.name}"
            )

    def test_preprocessor_blocks_balance(self) -> None:
        for path in (GATE_H, GATE_CPP, SESSION_CPP, MODULE_CPP):
            text = path.read_text(encoding="utf-8")
            opens = len(re.findall(r"^#if", text, re.MULTILINE))
            closes = len(re.findall(r"^#endif", text, re.MULTILINE))
            self.assertEqual(opens, closes, f"unbalanced #if/#endif: {path.name}")


if __name__ == "__main__":
    unittest.main()

"""R7 gate prototype contract — corrected after N1's native build.

N1 compiled the previous prototype against real UE 5.6.1 and got
`error C2248: SWidget::GetEnabledStateAttribute: cannot access protected
member`.  The earlier version of this test file asserted that accessor was
public and that "exact restore" was proven; both statements were wrong.

This file now pins the fail-closed truth:

* the gate must not reference the protected accessor, ``ToAttribute()`` or
  ``IsBound()``;
* it must not call ``SWidget::SetEnabled`` at all, so it can never disable a
  widget or flatten a bound enabled attribute to a constant;
* ``Arm`` must refuse with ``enabled-attribute-not-restorable`` before touching
  any Editor or widget;
* the rejected variants are pinned as forbidden text: P1's protected accessor
  read and N1's ``SetEnabled(true)`` / constant restore;
* everything else (test-only compilation, no global hook, no request-path
  arming, honest booleans, unchanged session protections) still holds.

None of this is a native PASS: the prototype is not compiled here and cannot be
armed.
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

# Rejected implementations, kept as literal negative fixtures.
REJECTED_P1_PROTECTED_READ = "GraphWidget->GetEnabledStateAttribute()"
REJECTED_N1_CONSTANT_RESTORE = "SavedEnabledState = TAttribute<bool>(bSavedWasEnabled);"
REJECTED_N1_SET_ENABLED_TRUE = "SetEnabled(true)"


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


class UIGateEnabledBindingSafetyTests(unittest.TestCase):
    """The core correction: no protected access, no irreversible disable."""

    def setUp(self) -> None:
        self.header = GATE_H.read_text(encoding="utf-8")
        self.gate = GATE_CPP.read_text(encoding="utf-8")
        self.code = _strip_cpp(self.header + "\n" + self.gate)

    def test_no_protected_attribute_access(self) -> None:
        # SWidget::GetEnabledStateAttribute() is protected in UE 5.6.1
        # (SWidget.h:1678 protected block); N1 reproduced it as C2248.
        for forbidden in (
            "GetEnabledStateAttribute",
            "GetVisibilityAttribute",
            "GetHoveredAttribute",
            "ToAttribute()",
            "IsBound()",
        ):
            if forbidden in self.code:
                self.fail(f"the gate must not use the protected API {forbidden}")

    def test_gate_never_mutates_enabled_state(self) -> None:
        # No SetEnabled at all: no disable, and no constant restore.  Comments
        # may discuss the rejected variants, so strip them first.
        self.assertNotIn("SetEnabled(", self.code)
        users = [
            path.name
            for path in PRIVATE.rglob("*.cpp")
            if "SetEnabled(" in _strip_cpp(path.read_text(encoding="utf-8", errors="ignore"))
        ]
        self.assertEqual(users, [], "no plugin source may toggle widget enabled state")

    def test_rejected_variants_are_forbidden(self) -> None:
        # P1 5c92e0c: read the protected accessor.
        self.assertNotIn(REJECTED_P1_PROTECTED_READ, self.code)
        # N1 compatibility patch: flatten the attribute to a constant.
        self.assertNotIn(REJECTED_N1_CONSTANT_RESTORE, self.code)
        self.assertNotIn(REJECTED_N1_SET_ENABLED_TRUE, self.code)
        self.assertNotIn("SavedEnabledState", self.code)

    def test_arm_fails_closed_with_precise_reason(self) -> None:
        self.assertIn("enabled-attribute-not-restorable", self.gate)
        stripped = _strip_cpp(self.gate)
        arm_start = stripped.index("bool FBlueprintEditSessionUIGate::Arm(")
        arm_end = stripped.index("void FBlueprintEditSessionUIGate::Release()", arm_start)
        arm = stripped[arm_start:arm_end]
        # Refuse before touching the Editor, the widget or Slate.
        for forbidden in (
            "OpenGraphAndBringToFront",
            "FindEditorForAsset",
            "SetEnabled",
            "GEditor",
        ):
            self.assertNotIn(forbidden, arm)
        # The refusal must be the value actually returned on the failure path.
        raw_arm_start = self.gate.index("bool FBlueprintEditSessionUIGate::Arm(")
        raw_arm_end = self.gate.index("void FBlueprintEditSessionUIGate::Release()", raw_arm_start)
        raw_arm = self.gate[raw_arm_start:raw_arm_end]
        self.assertIn('OutError = TEXT("enabled-attribute-not-restorable");', raw_arm)
        self.assertIn("return false;", arm)
        self.assertNotIn("return true;", arm)

    def test_loss_reporting_retained_for_future_arm(self) -> None:
        for reason in ("widget-destroyed", "graph-changed", "widget-reenabled"):
            self.assertIn(reason, self.gate)
        self.assertIn("EUIGateState::Lost", self.gate)
        self.assertIn("Pinned.IsValid()", self.gate)

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


class UIGateScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.header = GATE_H.read_text(encoding="utf-8")
        self.gate = GATE_CPP.read_text(encoding="utf-8")

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

    def test_widget_is_held_weakly(self) -> None:
        self.assertIn("TWeakPtr<SGraphEditor> Widget;", self.header)
        self.assertIn("TWeakObjectPtr<UBlueprint> Blueprint;", self.header)
        self.assertIn("TWeakObjectPtr<UEdGraph> Graph;", self.header)
        self.assertNotIn("SGraphEditor* Widget", self.header)


class UIGateLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.session = SESSION_CPP.read_text(encoding="utf-8")
        self.module = MODULE_CPP.read_text(encoding="utf-8")
        self.gate = GATE_CPP.read_text(encoding="utf-8")

    def test_release_paths_are_traceable(self) -> None:
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
        self.assertLess(
            shutdown_release,
            self.module.index("if (GraphLayoutP11Menu.IsValid())", shutdown),
        )

    def test_batch_only_observes_never_gates(self) -> None:
        batch = self.session.index("bool FBlueprintEditSessionManager::ValidateBatch(")
        region = self.session[batch:]
        self.assertIn("FBlueprintEditSessionUIGate::Get().Query();", region)
        observe = region.index("FBlueprintEditSessionUIGate::Get().Query();")
        self.assertNotIn("FBlueprintEditSessionUIGate::Get().Arm(", region)
        self.assertLess(observe, region.index("Impl->Blueprint.Get() != Blueprint"))

    def test_no_request_path_arms_the_gate(self) -> None:
        for path in (BRIDGE_CPP, SESSION_HANDLER, WORKFLOW):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("ArmUIGate", text, path.name)
            self.assertNotIn("FBlueprintEditSessionUIGate", text, path.name)
        self.assertEqual(self.gate.count(".Arm("), 1)

    def test_console_commands_are_test_only(self) -> None:
        for name in ("ArmUIGate", "ReleaseUIGate", "UIGateStatus"):
            self.assertIn(f'"UEAgentKit.BlueprintEditSession.{name}"', self.gate)
        self.assertIn("#if WITH_DEV_AUTOMATION_TESTS", self.gate)


class UIGateHonestyTests(unittest.TestCase):
    def test_user_visible_booleans_stay_false(self) -> None:
        handler = SESSION_HANDLER.read_text(encoding="utf-8")
        self.assertIn('TEXT("exclusiveUI"), false', handler)
        self.assertIn('TEXT("humanEditingBlocked"), false', handler)
        self.assertIn('TEXT("requiresUserNotEdit"), true', handler)
        self.assertIn('TEXT("rollbackSupported"), true', handler)

    def test_gate_never_touches_response_booleans(self) -> None:
        gate = _strip_cpp(GATE_CPP.read_text(encoding="utf-8")) + _strip_cpp(
            GATE_H.read_text(encoding="utf-8")
        )
        for token in ("exclusiveUI", "humanEditingBlocked", "requiresUserNotEdit"):
            if token in gate:
                self.fail(f"the UI gate must not touch the response field {token}")

    def test_existing_session_protections_unchanged(self) -> None:
        session = SESSION_CPP.read_text(encoding="utf-8")
        self.assertIn("IsCanonicalEditSessionToken(Impl->Token)", session)
        self.assertIn("ComputeGraphContentFingerprint", session)
        self.assertIn("Token.Equals(Impl->Token, ESearchCase::CaseSensitive)", session)
        self.assertIn("blueprint-edit-session-package-dirty", session)
        self.assertIn("AllowSessionOwnedDirty", session)

    def test_write_handlers_still_require_editability(self) -> None:
        # Evidence for rejecting the bEditable alternative: P1's own batch and
        # move handlers refuse when the graph is not editable, so toggling it
        # would block the Agent as well as the human.
        for name in ("EditorBridgeNodeMoveBatch.cpp", "EditorBridgeNodeMove.cpp"):
            text = (PRIVATE / name).read_text(encoding="utf-8")
            self.assertIn("IsEditable(Graph)", text)
            self.assertIn("bEditable", text)


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

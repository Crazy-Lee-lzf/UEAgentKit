# R7 — Enabled-binding safety review and fail-closed decision (2026-11-04)

Verdict: **the scoped UI gate cannot be made reversible with public UE 5.6.1 API.
It is now fail-closed and cannot disable any widget.** The earlier claim that
`SWidget::GetEnabledStateAttribute()` was public, and therefore that "exact
restore" was proven, was wrong. N1's native build disproved it. R7.1–R7.3 remain
**blocked**, and `exclusiveUI` / `humanEditingBlocked` remain **false**.

| Item | Value |
| --- | --- |
| Branch | `feature/p1-node-move-defect-fix-20261030` |
| Base | `5c92e0c` (clean) |
| Wrong claim | `docs/R7_SCOPED_UI_GATE_PROTOTYPE_20261103.md` and the 20 static tests said the accessor was public |
| Native disproof | N1 `docs/N1_R7_NATIVE_BUILD_PROBE_20261103.md`, `Output/n1-acceptance/20261103-r7/build-1.log`: `error C2248` |
| This round | code fails closed, tests and docs corrected, no Unreal process started |

## 1. The contradiction and its resolution

P1's 2026-11-03 prototype did this:

```cpp
const auto EnabledState = GraphWidget->GetEnabledStateAttribute();
SavedEnabledState = EnabledState.ToAttribute();
bSavedStateWasBound = EnabledState.IsBound();
```

The accompanying static tests asserted the accessor was public and that the
snapshot preserved a bound getter. N1 compiled the same file against the real
engine and failed:

```
BlueprintEditSessionUIGate.cpp(133,42): error C2248:
  "SWidget::GetEnabledStateAttribute": cannot access protected member
  (declared in class "SWidget")
```

I re-verified the installed engine (`E:\EPICGAME\UE_5.6`) directly:

| Fact | Evidence |
| --- | --- |
| `GetEnabledStateAttribute()` is declared at `SWidget.h:1690` | `TSlateAttributeRef<bool> GetEnabledStateAttribute() const` |
| The enclosing access section starts at `SWidget.h:1678` with `protected:` | the declaration is inside that block |
| The same block contains `GetHoveredAttribute`, `GetVisibilityAttribute`, `GetRenderTransformAttribute`, `GetRenderTransformPivotAttribute` | all protected |
| `SetEnabled(TAttribute<bool>)` (`:925`) and `IsEnabled()` (`:931`) are public | `public:` at `:916` |
| `EnabledStateAttribute` itself (`:1804`) is not publicly reachable | no public accessor other than the protected one |

So the static test was asserting a property of text, not a property of the API.
**A Python source contract cannot prove C++ accessibility.** That is the process
lesson, not just the code defect.

## 2. Why "restore with a constant true" is not an acceptable fix

N1's local compatibility patch (not written back to P1) replaced the snapshot
with:

```cpp
bSavedWasEnabled = GraphWidget->IsEnabled();
SavedEnabledState = TAttribute<bool>(bSavedWasEnabled);
bSavedStateWasBound = false;
```

and released with `SetEnabled(SavedEnabledState)` — effectively a constant.

The N1 note argues this is safe because `Arm` already refuses an
already-disabled widget, so the replaced value was necessarily `true`. That
argument covers the **value** but not the **binding**. `SWidget::SetEnabled`
performs `EnabledStateAttribute.Assign(...)`, which for a non-bound argument
takes the `Set()` path: it *unregisters* the attribute getter and stores the
value (`SlateAttributeBase.inl:257`, `:350`). If the widget's enabled state was
driven by a delegate — for example read-only/PIE-driven UI — then:

- the original getter is **destroyed**, not suspended;
- release writes a constant `true`, permanently ignoring the original logic;
- the game would have no way to re-disable that graph later, and no public API
  can even detect that the binding existed (`IsBound()` is on the same protected
  ref).

That is a silent, irreversible behaviour change to a user-facing widget. It must
not be shipped, and it must not be laundered through "it compiled natively".

## 3. Alternatives evaluated (all rejected with reasons)

| Mechanism | Why rejected |
| --- | --- |
| Snapshot via `GetEnabledStateAttribute().ToAttribute()` | The accessor is protected (`C2248`). `ToAttribute()` itself is public but the `TSlateAttributeRef` cannot be obtained. |
| Attribute table / reflection (`SlateAttributeMetaData`, `FSlateAttributeDescriptor`) | `GetAttributeNames()` returns names only; every value/bound query needs an `FSlateAttributeBase&` that is itself unreachable. The descriptor is construction-time metadata (name + offset), not a runtime value reader. |
| `SWidget::SetVisibility` to hide/hit-test-disable | `GetVisibilityAttribute()` is in the same protected block, so the original visibility cannot be captured either. Same irreversibility. |
| `UEdGraph::bEditable` / `FBlueprintEditor::IsEditable` | Not an input gate, and P1's own writers depend on it: `EditorBridgeNodeMoveBatch.cpp:324` and `EditorBridgeNodeMove.cpp:277` refuse when `!IsEditable(Graph) || !bEditable`. Toggling it would block **the Agent's** batches as well as the human, and it dirties the Blueprint. |
| Wrap/replace the graph widget's content (overlay) | No public API to reparent an engine-created `SCompoundWidget`; doing it would rebuild the graph panel and is not reversible. |
| Widget-scoped input handler | Requires subclassing the engine-created widget; there is no public attach API. |
| Global `IInputProcessor` | Process-wide; a missed unregister locks the entire Editor. Forbidden by the task and unsafe regardless. |
| `OpenGraphAndBringToFront` as the widget lookup | Public and usable, but it opens/brings the tab forward; acceptable only behind the focused-graph precondition, and irrelevant while arming refuses. |

## 4. The safety fallback that was implemented

`BlueprintEditSessionUIGate::Arm` now returns immediately:

```cpp
OutError = TEXT("enabled-attribute-not-restorable");
State = EUIGateState::Unavailable;
Reason = OutError;
LogUIGateRefused(OutError);
return false;
```

It does not resolve an editor, does not look up a widget, and does not call
`SetEnabled`. Consequently there is no code path in this build that can disable
a widget, so no binding can be flattened.

What deliberately remains (for review and for a future reversible design):

- the session target registration (`Begin`) and release hooks (`End`, `Reset`,
  module shutdown) — all inert, weak pointers only;
- the `Query()` state machine with `Lost` transitions, dormant until a future
  `Arm` can succeed;
- the three host-only console commands, which still refuse to arm. `ArmUIGate`
  now always logs the refusal;
- no global hook, no Editor-window disable, no `bEditable` change, no engine or
  private-header change.

Removed: `SavedEnabledState`, `bSavedStateWasBound`, `bSavedWasEnabled`, the
`GetEnabledStateAttribute`/`ToAttribute` read, the `SetEnabled(false)` call, the
disable verification and the restore-on-release. The 2026-11-03 implementation
stays in git history at `5c92e0c` for review.

## 5. Corrected record

- `docs/R7_SCOPED_UI_GATE_PROTOTYPE_20261103.md` — correction banner; the
  "public accessor", "proven exact restore" and `SetEnabled(false)` statements
  are struck through and marked superseded; the known-risk list now leads with
  the blocking defect.
- `docs/BLUEPRINT_UI_EDIT_GATE_PLAN.md` — the API table row now records
  `protected`, and design steps 3/4/6 are marked not implementable.
- `tests/python/test_r7_ui_gate_prototype.py` — the false assertions are
  replaced by negative ones (see §6).

## 6. Test evidence

`tests/python/test_r7_ui_gate_prototype.py` — **22 tests**, including:

- `test_no_protected_attribute_access` — no `GetEnabledStateAttribute`,
  `ToAttribute()`, `IsBound()`, `GetVisibilityAttribute` or
  `GetHoveredAttribute` anywhere in the gate;
- `test_gate_never_mutates_enabled_state` — no `SetEnabled(` in the gate and,
  by scan, none in any plugin `.cpp`;
- `test_arm_fails_closed_with_precise_reason` — `enabled-attribute-not-restorable`
  is returned, and the `Arm` body contains no `GEditor`, `FindEditorForAsset`,
  `OpenGraphAndBringToFront` or `SetEnabled`;
- `test_rejected_variants_are_forbidden` — both the P1 protected read
  (`GraphWidget->GetEnabledStateAttribute()`) and N1's constant restore
  (`SavedEnabledState = TAttribute<bool>(bSavedWasEnabled);`) and
  `SetEnabled(true)` are pinned as forbidden text;
- `test_write_handlers_still_require_editability` — documents why `bEditable`
  is not a viable alternative;
- plus the untouched guarantees: automation-only compilation, no global hook,
  weak handles, release-path traceability, batch observe-only, no request-path
  arming, test-only console commands, `exclusiveUI`/`humanEditingBlocked` false,
  and the token/fingerprint/Dirty/transaction protections.

Discrimination was verified against the pre-fix source without touching the
worktree: **6/6** rejected variants (four P1 facts, two N1-style facts) are
caught by the corrected contract.

Full `py -3.12 -B -m unittest discover -s tests/python`: **1984 passed,
20 skipped**. `git diff --check` clean.

Not proven: the corrected revision has never been compiled. N1's next build must
confirm `C2248` is gone before anything else. Nothing here is a Native PASS.

## 7. R7.1–R7.3 status and next-round conditions

| Case | Status | Why |
| --- | --- | --- |
| N1-R7.1 (human drag blocked) | **blocked** | arm is impossible; a refusal is not isolation |
| N1-R7.2 (other tabs/console unaffected) | **blocked** | nothing can be armed |
| N1-R7.3 (release cleanliness) | **blocked** | no reversible disable exists |

N1 may merge this commit only to **compile and confirm the C2248 fix**; it must
not attempt an arm-and-measure run, because `ArmUIGate` now always refuses.

Conditions before any future enablement, all required:

1. a **public** way to capture and restore the exact `TAttribute<bool>`
   (including bound state), verified by a real compile and a real run; or a
   different scoped mechanism that blocks human input without replacing the
   enabled attribute;
2. an RAII release object covering `end(keep|rollback)`, tab close, compile,
   asset reload, PIE, Bridge stop, module shutdown and exceptions;
3. a native run that shows the target graph cannot be edited while every other
   tab, the console and PIE stay usable, and that the previous binding is intact
   after release;
4. only then may `exclusiveUI`/`humanEditingBlocked` be considered, in a separate
   reviewed slice.

Until 1–3 exist, the honest position is the current one: **no UI isolation, and
no claim of it.**

# R7 — scoped Blueprint graph input gate prototype (2026-11-03)

Status: **test-only prototype committed; not enabled; not compiled; not native
verified.** No production path arms it. `exclusiveUI`, `humanEditingBlocked` and
`requiresUserNotEdit` are untouched (`false` / `false` / `true`), and nothing in
this round may be reported as a UI lock.

| Item | Value |
| --- | --- |
| Branch | `feature/p1-node-move-defect-fix-20261030` |
| Base | `0967b01` (clean) |
| New code | `BlueprintEditSessionUIGate.h`, `BlueprintEditSessionUIGate.cpp` |
| Touched code | `BlueprintEditSession.cpp`, `UEAgentKitEditorModule.cpp` (guarded hooks only) |
| Tests | `tests/python/test_r7_ui_gate_prototype.py` |
| Compiled / run | **no** — no UBT, no Editor, no commandlet |

## Why a prototype instead of a shipped lock

The previous round established which public UE 5.6.1 APIs exist but could not
prove that a gate could be *released* safely. This round reduces that uncertainty
to a small, test-only measurement vehicle:

- the exact enabled-state restore is now **proven from headers** rather than
  assumed (see below);
- the widget is reached only through the single public lookup and only when the
  target graph is already focused, so no tab is created or reordered;
- everything is compiled only under `WITH_DEV_AUTOMATION_TESTS` and armed only
  by an explicit host console command, so Shipping and normal development builds
  contain none of it;
- the honest response fields are not connected to it at all.

The remaining unknowns are *behavioural* (does disabling this widget block every
human edit path; what happens on compile/tab close/PIE), and those can only be
answered by N1's native run. Until then the gate stays disarmed.

## Verified API basis (UE 5.6.1, `E:\EPICGAME\UE_5.6`)

| Fact | Location |
| --- | --- |
| `SGraphEditor` is public in an UnrealEd header | `Editor/UnrealEd/Public/GraphEditor.h:101` |
| `UEdGraph* GetCurrentGraph() const` public | same header |
| `void SetEnabled(TAttribute<bool>)` / `bool IsEnabled() const` | `Runtime/SlateCore/Public/Widgets/SWidget.h:925`, `:931` |
| `GetEnabledStateAttribute()` returns a public `TSlateAttributeRef<bool>` | `SWidget.h:1690` |
| `TSlateMemberAttributeRef::ToAttribute()` re-creates a **bound** `TAttribute` when the attribute is bound, otherwise the current value | `Runtime/SlateCore/Public/Types/Attributes/SlateAttributeMember.inl:187`, `SlateAttributeBase.inl:466` |
| `Assign()` sets an unbound value synchronously (so verify-after-disable is valid) | `SlateAttributeBase.inl:257`, `:350` |
| `FBlueprintEditor::OpenGraphAndBringToFront(UEdGraph*, bool)` is the only public graph → widget lookup | `Editor/Kismet/Public/BlueprintEditor.h:282` |
| `GetFocusedGraph()`, `GetBlueprintObj()`, `IsEditable()` | same header |
| `FAssetEditorToolkit::GetTabManager()`, `virtual void OnClose()` | `Editor/UnrealEd/Public/Toolkits/AssetEditorToolkit.h:224`, `:426` |
| PIE delegates: `PreBeginPIE`, `BeginPIE`, `EndPIE` | `Editor/UnrealEd/Public/Editor.h:239–259` |
| No `FEditorDelegates::PostSaveAsset` exists (only world-save delegates) | `Editor.h:271`, `:275` |
| `SGraphEditor` does **not** declare a Slate widget type, so a focus-tree type walk is not reliable | no `SLATE_DECLARE_WIDGET` in `GraphEditor.h` |

That last row is why the prototype does not try to identify a graph widget by
walking the focus path: it would require an unsound cast.

## Mechanism

`FBlueprintEditSessionUIGate` is a process-local singleton.

1. `SetSessionTarget(Blueprint, Graph)` — called at the end of a successful
   `Begin`. Stores **weak** object pointers only. It never touches Slate and
   never arms.
2. `Arm(OutError)` — explicit, console-only:
   - refuses without an active session target;
   - resolves `FBlueprintEditor` through `UAssetEditorSubsystem` using the same
     `GetEditorName() == "BlueprintEditor"` + `static_cast` pattern P1 already
     compiles, and re-proves `GetBlueprintObj()`;
   - **refuses unless `GetFocusedGraph() == Graph`** — this is the side-effect
     guard that stops `OpenGraphAndBringToFront` from opening or reordering a
     tab;
   - verifies the returned widget and `GetCurrentGraph() == Graph`;
   - refuses if the widget is already disabled (ownership would be ambiguous);
   - snapshots `GetEnabledStateAttribute().ToAttribute()` and `IsBound()`;
   - calls `SetEnabled(false)` and synchronously re-reads `IsEnabled()`; a still
     enabled widget is restored and reported as a refusal.
3. `Query()` — revalidates every time. Armed → `Lost` when the widget is
   destroyed, re-pointed at another graph, or re-enabled. `Lost` is never
   reported as isolation.
4. `Release()` — restores the exact saved attribute (`SetEnabled(Saved)`), so a
   bound getter is re-bound rather than frozen to a bool, then clears state.

## Fail-closed refusal matrix

| Condition | Result |
| --- | --- |
| No active session target | `no-active-session-target` |
| `GEditor` / asset editor subsystem missing | `asset-editor-subsystem-unavailable` |
| Blueprint editor not open for this asset | `blueprint-editor-not-open` |
| Editor owns a different Blueprint | `blueprint-editor-mismatch` |
| Bound graph is not the focused graph | `graph-not-focused` |
| Widget missing or showing another graph | `graph-widget-unavailable` |
| Widget already disabled by someone else | `graph-widget-already-disabled` |
| `SetEnabled(false)` did not take effect | `graph-widget-disable-unverified` (state restored) |
| Widget destroyed after arming | `Lost` / `widget-destroyed` |
| Widget re-pointed to another graph | `Lost` / `graph-changed` |
| Widget re-enabled by the engine | `Lost` / `widget-reenabled` |

A `Lost` or refused gate never blocks a batch and never changes a response. Write
safety stays exactly where it was: token + fingerprint + Dirty + read-back +
single transaction.

## Release path coverage

| Path | Implemented | Native proven |
| --- | --- | --- |
| `end(mode=keep)` | yes, `ClearSessionTarget()` | no |
| `end(mode=rollback)` | yes, same call before state reset | no |
| `BlueprintEditSessionManager::Reset()` (Bridge stop) | yes | no |
| Module `ShutdownModule()` | yes | no |
| Explicit escape command | yes (`ReleaseUIGate`) | no |
| Tab close / asset reload | implicit: weak pointer dies, next `Query` → `Lost` | no |
| Blueprint compile / editor reconstruct | implicit: widget replaced, weak pointer dies → `Lost` (the new widget is enabled) | no |
| PIE start/end | **not** auto-released; the graph stays non-interactive and `Query` still reports `Armed`. Deliberate: the widget is scoped, so PIE is not harmed beyond this tab, but the behaviour is unmeasured | no |
| Focus loss | **not** auto-released; disabling is per-widget, so leaving the tab keeps the graph non-interactive (correct for isolation, unmeasured for UX) | no |
| Forced exception between arm and release | only covered if it unwinds to `End`/`Reset`; there is no separate RAII object yet | no |
| Editor crash | widget dies with the process | n/a |

The two "not auto-released" rows are the main reason this must not be enabled:
they need native observation before anyone claims isolation.

## Escape hatch

Test-build console commands only, never reachable from MCP/socket parameters:

- `UEAgentKit.BlueprintEditSession.ArmUIGate`
- `UEAgentKit.BlueprintEditSession.ReleaseUIGate`
- `UEAgentKit.BlueprintEditSession.UIGateStatus`

`ArmUIGate` and `ReleaseUIGate` are host-only; the static tests assert they are
absent from `EditorBridge.cpp`, the session handler and `mcp_workflow_tools.py`.

## Deliberately unchanged

- `exclusiveUI=false`, `humanEditingBlocked=false`, `requiresUserNotEdit=true`
  in every response.
- Token protocol, case-sensitive session binding, Dirty gate, content
  fingerprint, single `FScopedTransaction`, in-transaction finalizer and
  rollback semantics — the prototype adds no code inside any of those checks.
- No global `IInputProcessor`, no `FSlateApplication` hook, no
  `RegisterInputPreProcessor`, no Editor-window disable, no `bEditable` change,
  no engine or private-header change.
- The gate stores no `UObject*`. It holds `TWeakObjectPtr` for the Blueprint and
  graph and `TWeakPtr<SGraphEditor>` for the widget.

## Known unverified risks (must be measured next)

1. **Disable actually blocks all human editing of that graph** — mouse drag,
   marquee, context menu, keyboard shortcuts, pin drag, comment resize. The
   static code only proves the widget is disabled.
2. **Other tabs, other Blueprints, the console and PIE stay fully usable.**
3. **`OpenGraphAndBringToFront` on an already-focused graph** is assumed to
   return the existing widget without visible change; unproven.
4. **Release restores the original attribute exactly**, including a bound
   getter. `ToAttribute()` preserves the delegate by header inspection, but the
   `AssignBinding` timing (immediate vs next tick) is unproven.
5. **Compile/reconstruct** must produce `Lost`, never a silently still-disabled
   graph.
6. **Selection/focus side effects** of disabling the widget mid-edit.
7. The prototype has **never been compiled**. A build error is possible; it must
   be fixed and rebuilt before any enablement.

## N1 native acceptance contract for R7.1–R7.3

Prerequisite: a dev/automation build containing this prototype, an isolated
project, a human-started `UnrealEditor.exe` with the Bridge connected, and a
constant Editor PID.

| Case | Steps | Expected |
| --- | --- | --- |
| R7.1 | begin a session on graph G; focus G; run `ArmUIGate`; drag G with the mouse; send one Agent batch | no human position change, no human transaction; Agent batch still succeeds; `UIGateStatus` shows `state=1 (Armed)`; fingerprint unchanged across the drag attempt |
| R7.2 | while G is armed, use a second Blueprint tab, the Content Browser and the console | all remain usable; only G is non-interactive; `UIGateStatus` still armed for G |
| R7.3 | `end(keep)`; re-arm and `end(rollback)`; close G's tab; compile the Blueprint; start/stop PIE; Bridge stop; `ReleaseUIGate` | G is editable again after every path; `UIGateStatus` is `Lost` or `Idle` (never `Armed` after teardown); a fresh begin starts disarmed; no graph left disabled |

Only after all three pass may `exclusiveUI`/`humanEditingBlocked` be considered
for a future change, and even then in a separate reviewed slice.

### Interaction with N1 R5 (Undo)

While the gate is armed, human Ctrl+Z inside graph G is blocked with the rest of
the widget input. Agent-side undo (`GEditor->UndoTransaction`) does not go
through Slate and is unaffected. R5 Undo acceptance must therefore run with the
gate **disarmed**, or record explicitly that the gate was armed.

## Test evidence

- `tests/python/test_r7_ui_gate_prototype.py`: **20 tests** — automation-only
  compilation, single-widget scope, no global hook, weak handles, exact restore,
  refusal reasons, loss reporting, release-path traceability, batch
  observe-only, no request-path arming, test-only console commands, unchanged
  response booleans, unchanged token/fingerprint protections, and
  brace/paren/`#if` balance sanity.
- Full `py -3.12 -B -m unittest discover -s tests/python`: **1982 passed,
  20 skipped** (previous 1962/20; the 20 added tests are new).
- `git diff --check` clean.
- **No C++ compile and no native run were performed**, so nothing here is a
  Native PASS.

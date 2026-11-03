# R7 — scoped Blueprint graph input gate prototype (2026-11-03)

> **Correction (2026-11-04, supersedes the claims below).** N1 compiled this
> prototype against real UE 5.6.1 and it failed with
> `error C2248: SWidget::GetEnabledStateAttribute: cannot access protected
> member`. The accessor is **protected**, not public (it sits in the block that
> starts at `SWidget.h:1678`), so the "exact restore" this document describes
> was never valid and the code never compiled. The prototype is now
> **fail-closed**: `Arm()` returns `enabled-attribute-not-restorable` and never
> calls `SetEnabled`, so it cannot disable any widget. Every statement below
> about a public accessor, a proven exact restore, or `SetEnabled(false)` is
> superseded. Authoritative record:
> [R7_ENABLED_BINDING_SAFETY_REVIEW_20261104.md](R7_ENABLED_BINDING_SAFETY_REVIEW_20261104.md).

Status: **test-only prototype committed; DISABLED (fail-closed); not enabled;
not recompiled after the correction.** No production path arms it, and arming
always refuses. `exclusiveUI`, `humanEditingBlocked` and `requiresUserNotEdit`
are untouched (`false` / `false` / `true`), and nothing here may be reported as
a UI lock.

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

- ~~the exact enabled-state restore is now **proven from headers**~~ — **wrong**:
  the accessor needed for that snapshot is protected, so no exact restore was
  ever proven (corrected 2026-11-04);
- the widget is reached only through the single public lookup and only when the
  target graph is already focused, so no tab is created or reordered (this part
  of the design survives, but it is currently unreachable because `Arm` refuses
  before any lookup);
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
| ~~`GetEnabledStateAttribute()` returns a public `TSlateAttributeRef<bool>`~~ **WRONG: protected** (block at `SWidget.h:1678`); N1 reproduced `C2248` | `SWidget.h:1678–1690` |
| `TSlateMemberAttributeRef::ToAttribute()` re-creates a **bound** `TAttribute` when the attribute is bound, otherwise the current value — the API exists, but the ref is unreachable from outside | `Runtime/SlateCore/Public/Types/Attributes/SlateAttributeMember.inl:187`, `SlateAttributeBase.inl:466` |
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
2. `Arm(OutError)` — **now fail-closed**. It returns
   `enabled-attribute-not-restorable` before resolving an editor, looking up a
   widget or touching Slate. The sequence below is the *superseded 2026-11-03
   design*, kept only as the review record; it never compiled:
   - ~~refuses without an active session target~~;
   - ~~resolves `FBlueprintEditor` through `UAssetEditorSubsystem` …~~;
   - ~~**refuses unless `GetFocusedGraph() == Graph`** …~~;
   - ~~verifies the returned widget and `GetCurrentGraph() == Graph`~~;
   - ~~refuses if the widget is already disabled~~;
   - ~~snapshots `GetEnabledStateAttribute().ToAttribute()` and `IsBound()`~~ —
     **impossible**: that accessor is protected;
   - ~~calls `SetEnabled(false)` and synchronously re-reads `IsEnabled()`~~.
3. `Query()` — revalidates every time. Armed → `Lost` when the widget is
   destroyed, re-pointed at another graph, or re-enabled. `Lost` is never
   reported as isolation. Dormant while `Arm` refuses.
4. `Release()` — clears bookkeeping only; it never restores an attribute because
   nothing is ever disabled.

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

## Known unverified risks (all still open; plus the blocking one)

0. **BLOCKING (found by N1's native build): the enabled attribute cannot be
   captured**, so `SetEnabled(false)` cannot be reversed safely. Until a public
   reversible mechanism exists, `Arm` refuses and none of the items below can
   even be measured. `bEditable` is not an alternative: P1's own
   `EditorBridgeNodeMoveBatch.cpp` / `EditorBridgeNodeMove.cpp` refuse when the
   graph is not editable, so toggling it would block the Agent too.
1. **Disable actually blocks all human editing of that graph** — mouse drag,
   marquee, context menu, keyboard shortcuts, pin drag, comment resize. The
   static code only proves the widget is disabled.
2. **Other tabs, other Blueprints, the console and PIE stay fully usable.**
3. **`OpenGraphAndBringToFront` on an already-focused graph** is assumed to
   return the existing widget without visible change; unproven.
4. ~~**Release restores the original attribute exactly**, including a bound
   getter.~~ **Not achievable**: the accessor is protected; N1's
   `SetEnabled(true)` fallback would flatten a bound getter to a constant.
5. **Compile/reconstruct** must produce `Lost`, never a silently still-disabled
   graph.
6. **Selection/focus side effects** of disabling the widget mid-edit.
7. The **2026-11-03 revision did not compile** (N1: `C2248`). The corrected
   fail-closed revision has never been rebuilt; N1's next build must confirm the
   error is gone before anything else.

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

- `tests/python/test_r7_ui_gate_prototype.py`: **22 tests** after the
  2026-11-04 correction — automation-only compilation, no protected-attribute
  access, no `SetEnabled` anywhere, fail-closed `Arm`, both rejected variants
  forbidden, loss reporting retained for a future arm, weak handles,
  release-path traceability, batch observe-only, no request-path arming,
  test-only console commands, unchanged response booleans and unchanged
  token/fingerprint protections, plus brace/paren/`#if` balance sanity.
- Full `py -3.12 -B -m unittest discover -s tests/python`: **1982 passed,
  20 skipped** (previous 1962/20; the 20 added tests are new).
- `git diff --check` clean.
- **No C++ compile and no native run were performed**, so nothing here is a
  Native PASS.

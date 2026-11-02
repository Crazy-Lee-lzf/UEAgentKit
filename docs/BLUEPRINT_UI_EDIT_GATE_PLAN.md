# Blueprint edit session: human/UI edit isolation plan

Status: **not implemented; gate not passed**. The shipped session keeps
`exclusiveUI=false`, `humanEditingBlocked=false` and `requiresUserNotEdit=true`.
No UI lock is claimed anywhere in code, responses, tests or docs.

This document records the API assessment and the exact native acceptance
criteria for a later slice. It is a plan, not evidence.

## Why nothing was implemented in this slice

- Correcting an earlier statement: this machine **does** have a UE 5.6.1
  installation with headers (`E:\EPICGAME\UE_5.6`, CL 44394996). What it does not
  have is an isolated Editor instance plus authorization to compile or launch
  one, so no input-gate behaviour can be verified. The API rows below were
  re-checked against those headers where applicable; the Slate/widget rows that
  matter for a lock remain *unverified* until an isolated Editor run.
- The task explicitly forbids registering global Slate input hooks that cannot
  be proven to clean up, and forbids launching or restarting an Editor for
  validation. Without an isolated Editor there is no way to prove release on
  focus loss, PIE, tab close, module shutdown, or crash.
- The safe failure mode is the current one: the Agent asks the user not to edit,
  validates a content fingerprint before every write, and fails closed on any
  outside change. That is a cooperative contract, not isolation.

## Candidate mechanisms (assessment only)

1. **Global Slate input preprocessor** —
   `FSlateApplication::Get().RegisterInputPreProcessor(TSharedPtr<IInputProcessor>, int32 Index)`
   / `UnregisterInputPreProcessor`.
   *Unverified signature details.* Process-global: it sees events for every
   window and the console unless it filters by `FSlateApplication::Get().GetUserFocusedWidget`
   and the exact graph widget. Removal must happen on module shutdown, PIE,
   asset reload, Bridge stop and every error path. A missed removal disables
   editing across the whole Editor, which is exactly the failure mode the task
   forbids. **Rejected for this slice.**

2. **Widget-scoped disable of the target graph** —
   `SWidget::SetEnabled(false)` on the `SGraphEditor` that hosts the exact
   `UEdGraph`, restored on release. This is local to one widget, does not block
   the game thread or other windows, and Agent writes are direct
   `UEdGraphNode::NodePosX` writes that do not go through Slate, so the Agent
   would keep working. Open questions that require native verification:
   - how to obtain the `SGraphEditor` for the exact graph without private
     `FBlueprintEditor` members (widget-tree traversal is fragile across
     versions; a per-version accessor must be confirmed);
   - whether disabling also blocks selection, context menus, tooltips, drag
     reconciliation and graph refresh, and whether re-enabling restores the
     exact previous state;
   - interaction with Blueprint compile/reconstruct (widget may be recreated,
     losing the disabled state and the release handle);
   - focus transfer when the tab loses focus while disabled.
   A scoped, reference-counted disable with an explicit widget handle and a
   `TWeakPtr` guard is the most promising direction, but it is **not verified**
   and therefore not implemented.

3. **`UEdGraph::bEditable=false` / `FBlueprintEditor::IsEditable`** — rejected:
   it is a state check used by validation, would also block the Agent's own
   handler gate and Blueprint compile behavior, and it is not an input gate.

4. **Transaction guard (`GEditor->Trans`)** — only prevents nested transactions.

5. **Asset editor interfaces (`UAssetEditorSubsystem`)** — no per-asset
   exclusive-edit lock exists for this use case.

## Required properties for any future implementation

- Scoped to one exact Blueprint editor tab + one exact `UEdGraph`; no effect on
  other tabs, other windows or the console.
- Reference-counted release on: `end(mode=keep|rollback)`, focus loss, tab close,
  asset reload, PIE start/end, Bridge stop, module shutdown, and every failure
  path (RAII handle plus a `TWeakPtr` liveness check).
- Never blocks the game thread; no synchronous waiting on Slate.
- Emergency release that always works: a console command / menu action that
  clears the gate and the session, plus the existing in-process reset on Bridge
  stop and Editor restart.
- Never replaces the fingerprint/position/package proofs; the gate is additive.
- `exclusiveUI=true` and `humanEditingBlocked=true` may only be returned after
  the native acceptance below passes; until then they stay `false`.

## Native acceptance plan (not executed; requires an isolated UE 5.6 copy)

Harness: isolated project copy, a real `UnrealEditor.exe` instance started by a
human, Bridge connected, PID recorded. No `UnrealEditor-Cmd` child may appear;
the PID must stay constant for the whole run.

1. **Begin + direct human drag**: begin session on graph G; attempt a mouse drag
   on a node. Expected: input is blocked, no node position changes, Agent batch
   still succeeds, fingerprint unchanged between batch and drag attempt.
2. **Other windows stay usable**: with the gate active, drag a node in a second
   Blueprint editor tab and use the console. Expected: both work; only graph G
   is gated.
3. **Focus transfer**: switch to another tab, then back. Expected: gate is
   released on focus loss and re-acquired (or explicitly reported lost); no
   stuck disabled widget; user can edit again.
4. **Release paths**: `end(keep)`, `end(rollback)`, tab close, asset reload,
   PIE start/stop, Bridge stop, module shutdown. Expected: every path restores
   input; a second run begins from a clean state.
5. **Failure/crash**: force an exception between acquire and release; expected
   RAII restores input, and a fresh Editor session has no residual gate.
6. **Undo/Redo interplay**: human Ctrl+Z after release works; Agent batch undo
   works; gate does not swallow the Ctrl+Z shortcut outside graph G.
7. **Compile/reconstruct**: compile the Blueprint while a session is active.
   Expected: either the session is invalidated and the gate released, or the
   gate survives with a re-resolved widget handle — never a lost handle with
   input still blocked.
8. **Response honesty**: only after 1–7 pass may `exclusiveUI` /
   `humanEditingBlocked` become `true`; otherwise they must stay `false` and the
   session must remain `experimentalContinuous` with `requiresUserNotEdit=true`.

## Session invalidation gaps this plan does not close

Closing the graph tab does not by itself invalidate the session (the loaded
objects still match). Detection is lazy on the next batch/rollback/`end`; the
PID/session binding and fingerprint proofs remain the only protection. Eager
`PostSaveAsset`/Undo/Redo/PIE/editor-close hooks are a separate follow-up and
must be native-tested for cleanup before shipping.

---

# R7 read-only API verification (2026-11-03)

Status unchanged: **not implemented, no input lock exists, gate not passed.**
`exclusiveUI` and `humanEditingBlocked` remain `false` everywhere. This section
only records what the installed UE 5.6.1 headers actually expose, so a later
slice does not have to guess. No engine source, private header or production
project was modified.

Installation checked: `E:\EPICGAME\UE_5.6` (5.6.1). All rows below were read
from headers in that tree.

## Verified API facts

| Fact | Location |
| --- | --- |
| `class SGraphEditor : public SCompoundWidget` is declared in a **public** UnrealEd header (no private header needed) | `Editor/UnrealEd/Public/GraphEditor.h:101` |
| `UEdGraph* GetCurrentGraph() const` is a public inline accessor — exact-graph identity can be proven on the widget | `Editor/UnrealEd/Public/GraphEditor.h` (`GetCurrentGraph`) |
| `void SetEnabled(TAttribute<bool>)` and `bool IsEnabled() const` exist on every widget | `Runtime/SlateCore/Public/Widgets/SWidget.h:925`, `:931` |
| `TSlateAttributeRef<bool> GetEnabledStateAttribute() const` gives a public reference to the enabled attribute, so a *previous* state can be snapshotted and restored without touching protected members | `Runtime/SlateCore/Public/Widgets/SWidget.h:1690` |
| `TSharedPtr<SGraphEditor> OpenGraphAndBringToFront(UEdGraph*, bool bSetFocus = true)` is public and is the only public graph → widget lookup found | `Editor/Kismet/Public/BlueprintEditor.h:282` |
| `UEdGraph* GetFocusedGraph() const` is public | `Editor/Kismet/Public/BlueprintEditor.h:281` |
| `IAssetEditorInstance::GetAssociatedTabManager()` is public/pure virtual | `Editor/UnrealEd/Public/Subsystems/AssetEditorSubsystem.h:88` |
| `FAssetEditorToolkit::GetTabManager()` is public; `virtual void OnClose()` exists | `Editor/UnrealEd/Public/Toolkits/AssetEditorToolkit.h:224`, `:426` |
| PIE hooks exist: `FEditorDelegates::PreBeginPIE`, `BeginPIE`, `EndPIE`, `OnSwitchBeginPIEAndSIE` | `Editor/UnrealEd/Public/Editor.h:239–259` |
| There is **no** `FEditorDelegates::PostSaveAsset`; only `PostSaveWorldWithContext` and `PostSaveExternalActors` exist | `Editor/UnrealEd/Public/Editor.h:271`, `:275` |
| Global input preprocessor registration/unregistration and `GetUserFocusedWidget(uint32)` exist (still rejected) | `Runtime/Slate/Public/Framework/Application/SlateApplication.h:1502–1532`, `:1640` |

P1 already uses the supported editor-resolution pattern:
`UAssetEditorSubsystem::FindEditorForAsset` → `GetEditorName() ==
FName("BlueprintEditor")` → `static_cast<FBlueprintEditor*>` →
`GetBlueprintObj()` identity check (`BlueprintGraphLayoutApply.cpp:2486–2497`).
Any R7 code should reuse it rather than inventing a cast.

## Narrowest viable mechanism (design only)

Scope: one `SGraphEditor` widget hosting one exact `UEdGraph`, disabled while the
session holds ownership.

1. **Resolve** the `FBlueprintEditor` for the session's `UBlueprint` with the
   existing pattern above; if `GetBlueprintObj()` is not the bound Blueprint,
   report the gate unavailable.
2. **Acquire** `TSharedPtr<SGraphEditor> Widget =
   Editor->OpenGraphAndBringToFront(Graph, /*bSetFocus=*/false)`. This is the
   documented minimum because there is no public "find existing widget for
   graph" API (`FDocumentTracker` has no public graph lookup in this tree).
   Verify `Widget.IsValid() && Widget->GetCurrentGraph() == Graph`; otherwise the
   gate is unavailable and the session must not claim isolation.
3. **Snapshot** `TSlateAttributeRef<bool> Previous = Widget->GetEnabledStateAttribute()`
   and store a `TWeakPtr<SGraphEditor>` plus the previous attribute reference.
4. **Disable** with `Widget->SetEnabled(false)`.
5. **Re-verify on every batch and on `end`**: weak pointer still valid,
   `GetCurrentGraph()` still the bound graph, `IsEnabled() == false`. Any
   mismatch drops the claim instead of assuming it survived.
6. **Release** by restoring the snapshot (`SetEnabled(Previous.Get())` when the
   attribute is static; if it is dynamic, re-`Assign` the saved attribute where
   the API allows, else re-enable only if the pre-state was enabled and record
   that the original dynamic source could not be restored). Then clear the weak
   pointer.

An RAII holder (`FTargetGraphGate`) with a destructor that releases on the game
thread is required, plus an explicit `Release()` on every path below.

### Release paths that must all be proven natively

`end(mode=keep)`; `end(mode=rollback)`; tab close; asset reload/close; Blueprint
compile or editor reconstruct (widget may be destroyed — the weak pointer going
invalid must invalidate the claim, never leave a stale "disabled" belief);
`FEditorDelegates::BeginPIE` / `EndPIE`; Bridge `Stop` (existing
`BlueprintEditSessionManager.Reset()`); module `ShutdownModule`; an exception
between acquire and release; Editor crash (process death releases the widget,
so the next process starts clean).

### Escape hatch

A console command, for example
`UEAgentKit.BlueprintEditSession.ForceReleaseUIGate`, that restores the enabled
state and ends the session, compiled in all configurations (unlike the existing
`WITH_DEV_AUTOMATION_TESTS` finalizer seam) because it is the emergency exit.
It must be advertised in the docs and covered by a native test.

### Honest reporting

Until R7.1–R7.3 pass natively, keep `exclusiveUI=false` and
`humanEditingBlocked=false`. A future slice should add a separate field (for
example `uiGate=unavailable|armed|released`) instead of overloading the existing
booleans, and must never report `armed` from a stale widget handle.

## Why this is still blocked

- Acquisition has a visible side effect: the only public lookup opens and
  brings the graph tab to the front, even with `bSetFocus=false`. Whether that
  is acceptable UX, and whether it disturbs a human mid-edit, needs native
  observation.
- Widget recreation on compile/reconstruct can silently drop the gate and
  re-enable input while the session still believes it is isolated. That
  fail-open mode must be measured before any `humanEditingBlocked=true`.
- Restoring a dynamic enabled attribute is not proven; mistakenly freezing it to
  a bool could permanently disable a graph after release.
- No public tab → graph-widget mapping exists, so a non-focused open tab cannot
  be reached without the side effect above.

Alternatives re-evaluated and still rejected: global `IInputProcessor`
(process-wide; a missed unregister locks the whole Editor), `bEditable` /
`FBlueprintEditor::IsEditable` (a validation state, not an input gate, and it
would also block the Agent's own handler), transaction guards (nested-transaction
protection only), and asset-editor interfaces (no per-asset exclusive-edit lock).

## R7.1–R7.3 test strategy (not executed)

| Case | Setup | Expected |
| --- | --- | --- |
| R7.1 direct human drag blocked | begin on graph G; human drags a node | no position change, no transaction, Agent batch still succeeds, fingerprint unchanged across the drag attempt |
| R7.2 scope isolation | second Blueprint tab and console while G is gated | both remain fully usable; only G is disabled; `IsEnabled()` on G is false and true on the other graph editor |
| R7.3 release cleanliness | `end(keep)`, `end(rollback)`, tab close, compile, PIE start/stop, Bridge stop, forced exception | G is editable again after every path; a second begin starts from a clean state; no graph remains disabled; escape command restores input even with a broken session |

Only after all three pass may the booleans change. This round implements none of
it.

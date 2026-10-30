# Blueprint edit session: human/UI edit isolation plan

Status: **not implemented; gate not passed**. The shipped session keeps
`exclusiveUI=false`, `humanEditingBlocked=false` and `requiresUserNotEdit=true`.
No UI lock is claimed anywhere in code, responses, tests or docs.

This document records the API assessment and the exact native acceptance
criteria for a later slice. It is a plan, not evidence.

## Why nothing was implemented in this slice

- The development environment has **no UE 5.6 engine source or isolated Editor**
  available (no `Engine/` tree, no `UnrealEditor.exe` install). API signatures
  below are therefore marked *unverified* and must be confirmed against the
  target engine version before any code is written.
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

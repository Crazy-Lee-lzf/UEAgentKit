# Blueprint edit sessions and batched node moves

Status: **experimental**. This branch ships one atomic, position-only node move
batch, an opt-in **continuous edit session** that lets an Agent apply several
batches to the same Blueprint without a Save between them, and a **position-only
session rollback** that restores only the nodes the Agent moved and the user has
not touched since.

Contract since 2026-11-05: **both sides may edit the same graph**. A session may
begin on a Dirty package, a user save mid-session does not invalidate it, and
conflict detection is limited to the exact nodes a batch names. The session
ships **no human/UI exclusivity lock** and none is claimed:
`exclusiveUI=false`, `humanEditingBlocked=false`, **`requiresUserNotEdit=false`**.
Rollback is advertised as `rollbackSupported=true` with
`rollbackStatus=supported-position-only-agent-owned-nodes`; user-modified nodes
are skipped and reported, never overwritten. Every claim below is limited to
that scope.

## Shipped contract

C++ (resident Editor Bridge):

- `editor.moveBlueprintNode` / `editor.moveBlueprintNodes`
- `editor.beginBlueprintEditSession`
- `editor.endBlueprintEditSession`

MCP (`workflow` mode, destructive annotations):

- `ue_move_blueprint_node`
- `ue_move_blueprint_nodes` (optional `edit_session_token`)
- `ue_begin_blueprint_edit_session` (`asset_path`, `graph_guid`,
  `experimental`)
- `ue_end_blueprint_edit_session` (`edit_session_token`, `mode=keep|rollback`)

Scope of one batch: one already-loaded Blueprint, one already-focused graph,
1..128 nodes, `nodeGuid` + `expectedX/expectedY` + `x/y` per node (integers in
`[-1000000, 1000000]`). Two nodes **may** target the same `(x, y)`; overlapping
node positions are legal in a UE graph, so the batch deliberately applies no
destination-uniqueness rule.

Apply: all validation completes first, then exactly one
`FScopedTransaction`; `Modify()` on the Blueprint, graph and every changed node;
all positions are written; every position is read back; the package must become
dirty. Any failure restores every changed position, the Blueprint
status/dependency cache and the pre-write Dirty flag, then cancels the
transaction. A fully no-op batch opens no transaction and does not dirty the
package.

`saved=false` and `compiled=false` are fixed for every tool in this slice: Save,
Compile and strong verification are separate explicit actions and are never
implicit effects. No child process is started; everything runs inside the
resident Editor process on the game thread.

Position-only writes are class-agnostic: ordinary K2 nodes, reroute
(`K2Node_Knot`) and comment (`EdGraphNode_Comment`) nodes all move through the
same `UEdGraphNode::NodePosX/NodePosY` path. A moved comment or node does **not**
carry its visually contained nodes with it; each GUID is moved individually.

The shared snapshot/transaction logic lives in
`Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintEditBatch.{h,cpp}`
(`UEAgentKitBlueprintEdit::ApplyNodeMoveBatch`). The session manager lives in
`BlueprintEditSession.{h,cpp}` and is owned by `FUEAgentKitEditorBridge`
(created in `Start`, destroyed in `Stop`).

## Edit session contract

`ue_begin_blueprint_edit_session` is accepted only when **all** of the
following hold; otherwise nothing is bound and nothing is written:

- startup authorized Commit tools and a connected, running Editor Bridge;
- `experimental=true` (a per-task explicit opt-in; `false` is refused with
  `blueprint-edit-session-experimental-opt-in-required`);
- the exact `/Game` Blueprint is loaded and already open in an ordinary
  `FBlueprintEditor` that owns it, the exact `graphGuid` is the focused graph,
  and that graph is editable K2;
- no PIE/SIE session and an idle Editor transaction history;
- no other edit session is active (one session per Bridge process).

The package may be **Dirty**. Unsaved user edits are expected and are no longer
treated as unprovable ownership; the Agent proves ownership per node at write
time instead (see below).

`begin` returns:

- `editSessionToken`: 64 lowercase hex characters (two concatenated GUIDs,
  generated with `EGuidFormats::DigitsLower`). The lowercase-only shape is
  enforced by the canonical validator, by the case-sensitive `end` comparison
  and by the Python wrapper; an uppercase, truncated or otherwise non-canonical
  token is always rejected (`blueprint-edit-session-token-invalid`);
- `expectedGraphFingerprint`: the content fingerprint snapshot taken at `begin`.
  **Informational only** — it is no longer a batch precondition, and it will
  differ after either side edits the graph;
- `nodeCount`, `createdAtUtc`, `editorSessionId`, `editorProcessId`;
- `packageDirtyBefore` / `packageDirtyAfter`: the real package Dirty state at
  `begin` (both equal, since `begin` writes nothing);
- honesty fields: `exclusiveUI=false`, `humanEditingBlocked=false`,
  `requiresUserNotEdit=false`, `experimental=true`,
  `singleTransactionPerBatch=true`, `rollbackSupported=true`,
  `rollbackStatus=supported-position-only-agent-owned-nodes`,
  `rollbackScope=agent-moved-nodes-not-changed-since`,
  `saved=false`, `compiled=false`, `evidenceLevel=same-process`,
  `acceptanceEligible=false`.

The token is bound to this Bridge session id and this process id, plus the
exact `assetPath`, `graphGuid`, Blueprint object and graph object.

## Multi-batch editing (what changed in this slice)

While a session is active, `ue_move_blueprint_nodes` may pass
`edit_session_token`. A token batch is authorized only if the manager re-proves,
on the game thread before any write:

1. the token is canonical and matches the active session;
2. the Bridge session id and Editor process id still match;
3. the bound Blueprint/graph objects are still the exact live objects and the
   graph is still owned by that Blueprint;
4. the request `assetPath`/`graphGuid` equal the bound ones and the live
   `UEdGraph::GraphGuid` still equals them;
5. **only the nodes this batch names** still exist and sit at the exact
   `expectedX`/`expectedY` the request declares. A mismatch is
   `blueprint-edit-session-node-position-conflict` and names the node, the live
   position and the expected position.

Nothing else is checked. A user drag elsewhere in the graph, a node added or
removed, a pin or comment edit, another plugin's change, or a Save between
batches no longer blocks the batch, and the package Dirty bit is not a
condition at all. The per-node check is the conflict gate: it is what prevents
the Agent from overwriting a node the user moved.

Only then is `ApplyNodeMoveBatch` called with
`EPackageDirtyGate::AllowSessionOwnedDirty`. That gate is **not** reachable from
a request parameter: it is chosen solely by the handler after the manager
validated a live token. `CommitBatch` runs **inside the batch's open
transaction** as the helper's finalizer, before the batch is reported committed:
for every node the batch wrote it records the pre-write position (the rollback
baseline, taken on the Agent's first touch of that node) and the position just
written (later user-edit detection). If it fails, the helper restores every
position, the Blueprint status/cache and the pre-write Dirty flag, cancels the
transaction, and returns the finalizer's error code with `writeOccurred=false`,
so an error can no longer describe a batch that stayed applied. A successful
token batch returns `editSessionUsed=true`, `writeOccurred=true`,
`sessionFinalized=true`, `exclusiveUI=false`, `humanEditingBlocked=false`.

Consequences:

- A session may start on a Dirty package, and later batches run while the
  package is Dirty or after the user saved it. Each batch remains its own
  `FScopedTransaction` (one Undo step per batch).
- A batch **without** a token remains clean-only
  (`EPackageDirtyGate::RequireClean`), exactly as before.
- While a session is active for the exact `(assetPath, graphGuid)`, a
  token-less batch is refused with
  `blueprint-edit-session-active-token-required`, so it cannot dirty the graph
  behind the session's back.
- An outside change to a node **this batch names** is refused
  (`*-node-position-conflict`). Changes to any other node do not affect the
  batch. Object identity/reload, `GraphGuid`, process and Bridge session are
  still hard requirements.
- `ue_end_blueprint_edit_session` with `mode=keep` releases ownership. It does
  not Save, Compile, or rewrite anything. Ending also reports `saved=false`,
  `compiled=false`, `exclusiveUI=false`, `humanEditingBlocked=false`,
  `rollbackSupported=true`, the batch counters
  (`batchCount`/`movedBatchCount`), `fingerprintMatchedExpected` (informational
  only — false is normal once either side edited), `packageDirtyAfter` and, for
  rollback, `restoreTransactionOpened`/`restoredNodeCount`/`skippedNodeCount`.
- Bridge `Stop` resets the manager, so a new Editor session always starts with
  no session and a fresh token.

## Session rollback (position-only, Agent-owned nodes)

`mode=rollback` restores only the Agent's own moves and then ends the session:

- **Scope**: only `NodePosX`/`NodePosY` of nodes this session moved, in the same
  Blueprint + graph + Bridge process; no save, no compile, no asset-level
  revert, no cross-save-point history, no crash resilience.
- **Eligibility is per node**: a node is restored only when its live position
  still equals the position the Agent last wrote for it. The restore target is
  the position that node had immediately before the Agent's **first** move of
  that node in this session — not the position at `begin` — so a user move that
  happened before the Agent touched the node is preserved.
- **User-modified nodes are skipped**, listed in `skippedNodeGuids`, counted in
  `skippedNodeCount`, and the rest of the rollback still proceeds. Rollback
  never overwrites a user move and never refuses wholesale because of one.
- **Proof before the first write**: canonical token, Bridge session id, Editor
  process id, live Blueprint/graph identity and ownership, and
  `UEdGraph::GraphGuid`. Package Dirty state, the graph node set and the
  content fingerprint are **not** preconditions.
- **Restore**: reuses `ApplyNodeMoveBatch` with
  `EPackageDirtyGate::AllowSessionOwnedDirty` and
  `NodeMoveRequestLimitUnlimited`, so exactly one `FScopedTransaction` owns the
  whole restore, every position is read back, and any read-back/Dirty
  confirmation failure restores the pre-rollback positions, Blueprint
  status/dependency cache and Dirty flag and cancels the transaction. A
  partially applied rollback cannot be reported as success. The 128-request cap
  is a **caller transport limit only**: a rollback may need to restore more
  nodes than one batch can carry and still uses one transaction. Every restored
  request is still fully validated; only the transport cap is lifted.
- **Race**: if the user moves an eligible node between the eligibility read and
  the helper's own validation, the transaction refuses with a
  `blueprint-edit-session-rollback-node-position-conflict` code and nothing is
  overwritten. Calling `end(mode=rollback)` again re-evaluates eligibility.
- **No-op**: a session whose Agent moves are already back at baseline (or that
  moved nothing) opens no transaction and reports `rolledBack=true`,
  `restoreTransactionOpened=false`, `restoredNodeCount=0`.
- **Refusal is not recovery**: a refused rollback never calls `Cancel()` to
  "undo" already applied UObject writes; the only writes happen inside the
  helper, which restores them on failure. Per-batch Undo still exists for the
  user independently.

ABA (same position written back) remains undetectable: a node restored to the
exact position the Agent last wrote is indistinguishable from one the user moved
away and back. Nodes deleted after the Agent moved them are skipped.

## Session invalidation rules (what is proven, when)

There is **no eager callback** in this slice: the manager does not subscribe to
`PostSaveAsset`, Undo/Redo, PIE or editor-close. Invalidation is proven
lazily on the next token batch, on `end`, or on rollback, plus eagerly on
Bridge `Stop`/restart (manager reset). This is intentional: an unverified
delegate binding could fire at unsafe times, leak, or miss module shutdown.

| Event | Detected by | Effect |
| --- | --- | --- |
| User drags a node **named by the batch** | per-node live position vs the request's `expectedX/expectedY` | batch refused (`*-node-position-conflict`) |
| User drags any **other** node | nothing, by design | batch proceeds; that node is untouched |
| Undo / Redo affecting a batch-named node | per-node live position | batch refused |
| Save by the user or another tool | not checked (Dirty is no longer a condition) | batch proceeds |
| Revert / reload of the package | weak object identity, `GraphGuid` | batch/rollback refused (`*-target-changed`, `*-asset-mismatch`) |
| Blueprint compile rebuilding nodes/graphs | object identity / `GraphGuid` | refused (`*-target-changed`, `*-asset-mismatch`) |
| Graph tab closed (object still loaded) | no focus requirement for token batches; object identity unchanged | session stays valid; a later batch still needs the handler's open/focused target checks |
| PIE/SIE started | `GEditor->IsPlaySessionInProgress()` / `PlayWorld` in the write helper | batch/rollback refused (`*-pie-active`) |
| Bridge `Stop` / Editor session end | `BlueprintEditSessionManager.Reset()` | session and token dropped |
| Another plugin writing a batch-named node | per-node live position | refused if that node moved; ABA remains undetectable |
| Another Editor process / different Bridge session | process id + Bridge session id binding | refused (`*-editor-session-mismatch`) |

The "graph closed" row is a known gap: closing the Blueprint editor tab does not
by itself invalidate the session because the loaded objects still match. The
handler still requires an open, focused target to *begin* a session and to move
nodes, but the manager alone does not observe tab close. A later slice can bind
`FAssetEditorSubsystem` close notifications once the cleanup path is
Native-tested.

## Missing UI exclusivity lock (explicit)

This slice **does not** prevent a person, another Editor, or another C++ plugin
from editing the same graph. `exclusiveUI` and `humanEditingBlocked` are
`false`, and `requiresUserNotEdit` is `true`: the user must be asked, in the
Agent's own words, not to edit the graph until the session is ended. Nothing in
the code, outputs or tests claims otherwise. See
[BLUEPRINT_UI_EDIT_GATE_PLAN.md](BLUEPRINT_UI_EDIT_GATE_PLAN.md) for the API
assessment and the native acceptance plan; nothing in that plan is implemented.

A structural (content) fingerprint is **not** ownership. Specifically:

- **ABA is undetectable.** A writer that restores the exact same content
  (positions, nodes, pins, comments) leaves the fingerprint identical, and the
  session continues as if nothing happened.
- **No change attribution.** There is no engine modification hook; the manager
  knows only that content differs, not who changed it or when.
- **Lazy invalidation only.** There is no `PostSaveAsset`/Undo/Redo/PIE/editor-
  close callback that eagerly closes the session; a change is detected at the
  next token batch or at `end`, never proactively.
- **No pre-existing-ownership proof.** The clean start says the package has no
  unsaved Dirty state; it cannot prove that no plugin mutated memory without
  marking Dirty. The Dirty bit does not identify an author.
- **No cross-process safety.** Only the exact Bridge session id plus process id
  are checked; another Editor process holding the same package is out of scope.

## Dirty ownership (fail closed)

Without a token, only the first **clean -> dirty** batch is possible: the
single-node tool rejects `expectedDirty=true`, and the batch tool rejects any
already-dirty package. There is deliberately no `assetPath + bool` cache: it
would keep authorizing writes after a manual edit, Undo/Redo, Save/reload or a
different Editor session.

With a token, the session's expected Dirty bit, content fingerprint, node set
and positions are re-proved before every batch. What remains unprovable is
listed above; therefore the session is a **cooperative** contract, not a lock.

## UI exclusivity: candidates, risks, decision

UELLMToolkit-style C++ edits demonstrate native graph mutation; they are not
evidence of a reliable human/UI lock or whole-batch rollback. No lock is
claimed here.

Candidate hooks that were assessed (none enabled):

1. **Slate input preprocessor** (`IInputProcessor` registered through
   `FSlateApplication::Get().RegisterInputPreProcessor`). It can intercept
   mouse/keyboard before widgets, but it is process-global, must be removed on
   every exit path, can disrupt unrelated editors and the console, and still
   does not block programmatic `UEdGraph` writes from other plugins.
2. **Graph read-only state** (`FBlueprintEditor::IsEditable`,
   `UEdGraph::bEditable`). This is a state check used for validation, not an
   input gate. Toggling it would also block the Agent and can force UI refresh
   or recompile behavior; it is not a scoped lock.
3. **Transaction guard** (`GEditor->Trans->IsActive()`). This only prevents
   nested transactions; it does not prevent user input.
4. **Asset editor interfaces** (`UAssetEditorSubsystem`,
   `IAssetEditorInstance`). No documented per-asset exclusive-edit lock exists
   for this use case.

Conclusion: a truthful UI lock requires an input gate that is Native-tested for
focus, crash cleanup and interaction with Undo/Redo. Until that exists, this
feature must not claim to block user editing; it only asks the user not to
edit.

Remaining design work for a later slice (not implemented, not verified):

1. **Eager invalidation hooks.** Bind `FEditorDelegates::PostSaveAsset` (and the
   pre-save counterpart), Undo/Redo (`GEditor->Trans` state transitions),
   `FEditorDelegates::BeginPIE`/`EndPIE`, and Blueprint editor close to revoke
   the session immediately instead of on the next batch. Persist nothing across
   reloads.
2. **Scoped UI input gate.** Register an `IInputProcessor` only while the exact
   Blueprint editor tab is focused and a session is active; remove it on every
   exit path (tab close, PIE, asset reload, module shutdown). Acceptance
   requires Native tests for focus loss, console input, crash cleanup and
   Undo/Redo interplay before any lock claim.
3. **True cross-save rollback.** Per-session snapshots that survive Save and
   reload, with a Native test for undoing across save points. Full undo across
   several save points is not achievable through the transaction buffer alone
   (history is process-local and a reload discards it). The current
   position-only, same-unsaved-session rollback is the bounded version of this.
4. **Shared batch model for other writes.** Route node create/remove, pin
   connect/disconnect and variable writes through the same
   `ApplyNodeMoveBatch`-style gate: validate all identities first, then one
   transaction, then read back every mutated object, restoring all of them on
   any mismatch.

Even with a content-bound token, a plugin that edits without marking Dirty
cannot be ruled out; acceptance would need a documented multi-writer policy.

## Verification status

Verified locally (Python 3.12, no Editor launched; `unittest` in
`tests/python`):

- `test_blueprint_edit_session_mcp.py` 17/17
- `test_move_blueprint_nodes_mcp.py` 14/14
- `test_node_move_mcp.py` 10/10
- `test_tool_registry.py` 4/4 (registry order and all mode counts)
- `test_mcp_server.py` 20/20
- `test_agent_workflow.py` 78/78
- `test_editor_bridge.py` 27/27
- `test_reflection_index.py` 13/13
- `test_freshness.py` 7/7
- `test_live_fast_verify_contract.py` 5/5
- `test_cli_duplicate_args.py` 3/3
- `test_index_skip_observability.py` 1/1
- full `unittest discover` over `tests/python`: **1943 passed, 20 skipped**

These cover MCP input validation, forwarding, error propagation, the strict
`TOOL_REGISTRY` order/count agreement (14/28/53/75/89/114/128 depending on
mode), and static source contracts: token shape, bridge ownership/reset,
`RequireClean` vs `AllowSessionOwnedDirty`, rollback reusing the single
transaction helper with an unlimited internal request cap, the in-transaction
session finalizer (`writeOccurred`/`sessionFinalized`), no **production** UI-lock
primitive (a test-only, `WITH_DEV_AUTOMATION_TESTS`-guarded prototype exists,
is never armed by a request path and currently cannot arm at all — it fails
closed with `enabled-attribute-not-restorable`; see
[R7_ENABLED_BINDING_SAFETY_REVIEW_20261104.md](R7_ENABLED_BINDING_SAFETY_REVIEW_20261104.md)),
no process spawning in the wrapper, and the read-only retained-live-write
fast-verify helper.

Not verified: UE C++ compilation, real `FScopedTransaction` Undo/Redo across
several batches, the real rollback transaction and its read-back, save/reload
persistence, PIE behavior, multi-window focus changes, and any UI-lock behaviour
(the test-only prototype has never been compiled or run). This slice is
statically audited only; it has **no** Native build or Editor evidence, so that
remains a required follow-up before any acceptance claim.

Required Native acceptance (not executed here; needs an isolated UE 5.6 copy,
not a cold start of the production Editor): Begin -> 10 consecutive position
batches -> per-batch Undo/Redo -> `end(mode=rollback)` -> Save/Reload; then
cross-graph and outside-edit interference, focus changes, PIE, and Bridge Stop
invalidation, asserting the process PID never changes and no `UnrealEditor-Cmd`
child appears.

Additional Native cases for the 2026-10-31 fixes (all currently **unverified**):

1. **129-node and 256-node graph, one node moved**: `begin`, move exactly one
   node with a token batch, `end(mode=rollback)`. Expected: rollback succeeds
   (no `node-move-batch-limit-exceeded`), exactly one transaction in the Undo
   history, every other node untouched, `restoredNodeCount=1`.
2. **Cross-batch rollback over more than 128 distinct nodes**: on a large graph,
   apply several ≤128-node batches that together move >128 distinct nodes, then
   roll back. Expected: one transaction, all touched nodes restored to their
   `begin` positions, `restoredNodeCount` equals the number of moved nodes, no
   intermediate transaction visible.
3. **Finalize fault injection**: in a dev-automation build, run the console
   command `UEAgentKit.BlueprintEditSession.FailNextFinalize` (test-only,
   `WITH_DEV_AUTOMATION_TESTS` guard, no MCP/env trigger), then send one token
   batch. Expected: the call returns
   `blueprint-edit-session-finalize-fault-injected`, the batch is fully restored,
   `writeOccurred=false`, the session still has its pre-batch expected state, and
   the Dirty flag is back to its pre-batch value. No visible node stays moved.
   This seam is source-reviewed only; it has never been compiled or run.
4. **Finalize success**: normal token batch returns `writeOccurred=true`,
   `sessionFinalized=true`, and one Undo step.
5. **No-op batch**: `writeOccurred=false`, no transaction, no Dirty change.

Round-3 header review and N1 contract alignment are recorded in
[P1_NATIVE_READINESS_REVIEW.md](P1_NATIVE_READINESS_REVIEW.md); nothing there is
Native evidence.

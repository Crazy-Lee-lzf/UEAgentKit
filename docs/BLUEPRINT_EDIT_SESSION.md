# Blueprint edit sessions and batched node moves

Status: **experimental**. This branch ships one atomic, position-only node move
batch. It does not ship a task-level checkpoint, a multi-batch edit session, or
any human/UI exclusivity lock.

## Shipped contract

- C++: `editor.moveBlueprintNodes` in the resident Editor Bridge.
- MCP: `ue_move_blueprint_nodes` (`workflow`, destructive).
- Scope: one already-loaded Blueprint, one already-focused graph, 1..128 nodes.
- Request per node: canonical lowercase `nodeGuid`, `expectedX`, `expectedY`,
  `x`, `y` (integers in `[-1000000, 1000000]`).
- Rejected before any write: unknown fields, invalid GUIDs, duplicate node
  GUIDs, missing nodes, expected-position mismatch, a graph that is not the
  focused editable K2 graph of that exact Blueprint Editor, PIE/SIE, an active
  Editor transaction, or an already-dirty package.
- Two nodes **may** target the same `(x, y)`. Overlapping node positions are
  legal in a UE graph, so the batch deliberately applies no destination
  uniqueness rule.
- Apply: all validation completes first, then exactly one
  `FScopedTransaction`; `Modify()` on the Blueprint, graph and every changed
  node; all positions are written; every position is read back; the package
  must become dirty. Any failure restores every changed position, the
  Blueprint status/dependency cache and the pre-write Dirty flag, then cancels
  the transaction.
- A fully no-op batch opens no transaction and does not dirty the package.
- `saved=false` and `compiled=false` are fixed: Save, Compile and strong
  verification are separate explicit actions and are never implicit effects.
- No child process is started; the operation runs inside the resident Editor
  process on the game thread.
- Position-only writes are class-agnostic: ordinary K2 nodes, reroute
  (`K2Node_Knot`) and comment (`EdGraphNode_Comment`) nodes all move through
  the same `UEdGraphNode::NodePosX/NodePosY` path. A moved comment or node does
  **not** carry its visually contained nodes with it; each GUID is moved
  individually.

The shared snapshot/transaction logic lives in
`Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintEditBatch.{h,cpp}`
(`UEAgentKitBlueprintEdit::ApplyNodeMoveBatch`). It is intentionally narrow:
position-only node moves today, with the same transaction/read-back/rollback
gate available to future node create/remove, pin, and variable operations.

## Not implemented in this slice (explicit)

The following are **absent**; nothing in the code, tests, or tool output claims
otherwise:

1. **No human/UI exclusive lock.** A person (or another plugin) can still drag a
   node in the same graph during or after a batch. There is no input
   preprocessor, graph read-only toggle, or per-asset edit lease.
2. **No session revision / ownership token.** No `(package revision, graph
   fingerprint, token)` record is created, validated, or persisted; only the
   per-node `expectedX/expectedY` optimistic check and the single
   clean -> dirty package gate exist.
3. **No continuous multi-batch editing.** After a successful batch the package
   is dirty, so every later Agent batch is refused until the user Saves or
   reverts. Continuous sessions are not supported or verified.
4. **No undo across multiple save points.** Exactly one batch is one
   `FScopedTransaction`; Undo/Redo round-trips are not Native-tested, and
   reversing edits made across several saves or several batches is not
   implemented.
5. **No strong (reload) verification and no save/compile side effect.** Output
   is labelled `evidenceLevel=same-process` and `acceptanceEligible=false`.

## Dirty ownership (fail closed)

This slice supports only the first **clean -> dirty** batch. It rejects
`expectedDirty=true` for the single-node tool and rejects any already-dirty
package for the batch tool. There is deliberately no `assetPath + bool` cache:
such a cache would keep authorizing writes after a manual edit, Undo/Redo,
Save/reload, or a different Editor session.

What the implementation can prove:

- The request is handled synchronously in one game-thread call that does not
  yield, so Slate input handling cannot interleave *inside* one batch call.
- Exact Blueprint/Graph/Node identity plus expected positions detect any
  out-of-band change that happened before the call.
- All changed nodes either commit together or are restored together.

What it cannot prove:

- That the pre-existing clean state has no unsaved in-memory changes from a
  plugin that mutated without marking the package dirty.
- That human input or another C++ plugin will not modify the graph **after**
  the batch and before a later batch.
- That the Dirty bit identifies the author of a change. It does not.

Therefore continuous multi-batch editing remains **unsupported** in this
slice. Callers must Save or revert before a new Agent batch.

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

Conclusion: a truthful UI lock requires an input gate that is Native-tested
for focus, crash cleanup and interaction with Undo/Redo. Until that exists,
this feature must not claim to block user editing. Continuous sessions stay
disabled.

Design candidate for a later slice (not implemented, not verified):

1. **Session record + ownership token.** Extend
   `UEAgentKitBlueprintEdit` with a `FBlueprintEditSession`
   (`SessionId`, `PackagePath`, `GraphGuid`, graph fingerprint over sorted
   `(NodeGuid, NodePosX, NodePosY)`, `EditorSessionId`, `ProcessId`,
   `bOwnsDirty`). Create it only from a clean package; revalidate the full
   fingerprint on the game thread before **every** batch; invalidate on any
   mismatch. This is what would replace today's single clean -> dirty gate.
2. **Token invalidation hooks.** Bind `FEditorDelegates::PostSaveAsset` (and
   the pre-save counterpart), Undo/Redo (`GEditor->Trans` state transitions),
   `FEditorDelegates::BeginPIE`/`EndPIE`, and Blueprint editor close to revoke
   the session. Persist nothing across reloads: a reloaded package must start a
   new session.
3. **Scoped UI input gate.** Register an `IInputProcessor` only while the
   exact Blueprint editor tab is focused and a session is active; remove it on
   every exit path (tab close, PIE, asset reload, module shutdown). Acceptance
   requires Native tests for focus loss, console input, crash cleanup and
   Undo/Redo interplay before any lock claim.
4. **Undo scope.** One `FScopedTransaction` per batch stays the rule. Full
   undo across several save points is not achievable through the transaction
   buffer alone (history is process-local and package reload discards it);
   a later slice would need explicit per-session snapshots, not a UI lock.
5. **Shared batch model for other writes.** Route node create/remove, pin
   connect/disconnect and variable writes through the same
   `ApplyNodeMoveBatch`-style gate: validate all identities first, then one
   transaction, then read back every mutated object, restoring all of them on
   any mismatch.

Even with a content-bound token, a plugin that edits without marking Dirty
cannot be ruled out; acceptance would need a documented multi-writer policy.

## Verification status

Verified locally (Python 3.12, no Editor launched): MCP wrapper validation,
128-node bound and its agreement with the C++ header, two-node forwarding,
overlapping destinations accepted, duplicate-GUID / bad-coordinate / bad-GUID
rejection, commit and live-bridge gates, bridge error (including dirty-package)
propagation, registry/capability counts, and a static check that the wrapper
imports no process-spawning module and holds no Unreal process-launch string.

Not verified: UE C++ compilation, real `FScopedTransaction` Undo/Redo,
save/reload persistence, PIE behavior, multi-window focus changes, and any UI
lock. This slice is statically audited only; it has **no** Native build or
Editor evidence, so that remains a required follow-up before any acceptance
claim.

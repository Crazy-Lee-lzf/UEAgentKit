# Experimental MCP Graph Edit — Branch / Sequence + Bool Data Pin

> **Status:** experimental, default-off, local development. Not part of the
> published v0.8.0 write surface and not a general GraphPatch executor.

This is the live createNode/connectPins vertical slice. It creates standard K2
**Branch**, **Sequence** and pure **NOT Boolean** (`not_bool`) nodes, connects
exec pins, and connects the exact Bool data link
`not_bool.ReturnValue -> branch.Condition` in one transaction. It is deliberately
smaller than the full GraphPatch 1.0 IR: the existing `ue_preview_graph_patch`
remains the offline contract preview, and this live tool uses the same
`createNode` / `connectPins` nouns with a bounded `nodeType` allowlist instead of
arbitrary action descriptors.

## Opt-in

Both must be true:

```ini
[UEAgentKit]
ExperimentalGraphPatchEdit=True
```

```powershell
ue-agent-mcp --enable-live-editor --project <ExactProject.uproject> `
  --enable-experimental-graphpatch-edit
```

The Project must be a disposable test copy. The tool never falls back to a
Python/Editor console, arbitrary command execution, or a hardcoded project.

## Read-only preview

```text
ue_preview_blueprint_graph_edit_experimental(
  assetPath,
  graphGuid,
  nodes=[{tempId, nodeType, x, y}, ...],
  connections=[{from, fromPin, to, toPin}, ...]
)
```

Supported `nodeType`:

| nodeType | class | inputs | outputs |
|---|---|---|---|
| `branch` | `/Script/BlueprintGraph.K2Node_IfThenElse` | `execute` (exec), `Condition` (bool) | `then`, `else` (exec) |
| `sequence` | `/Script/BlueprintGraph.K2Node_ExecutionSequence` | `execute` (exec) | `then_0`, `then_1` (exec) |
| `not_bool` | `/Script/BlueprintGraph.K2Node_CallFunction` (`KismetMathLibrary.Not_PreBool`) | `A` (bool) | `ReturnValue` (bool) |

Connections are type-checked pin to pin:

- exec: `then` / `else` / `then_0` / `then_1` -> `execute`;
- bool: `not_bool.ReturnValue` -> `branch.Condition`.

Any other pin, direction, or data-kind mismatch is rejected before Apply.
Limits: 1–8 nodes, 0–12 connections, at most 6 connections touching one node,
60 KiB request. Each output and each input may appear at most once.

The preview checks the exact focused Blueprint editor, exact assetPath and
GraphGuid, clean package, stopped PIE/SIE, editable graph, and K2 schema. It
returns a read-only result with `planId`, current revision/fingerprint, node
classes, exact pin names and pin types, and `requiredConfirmation`. It never
opens a transaction, dirties, compiles, or saves. `fromType` / `toType` on each
previewed connection are authoritative; Python Apply validation rejects a
preview whose declared pin types do not match this bounded contract.

## Apply

```text
ue_apply_blueprint_graph_edit_experimental(
  preview=<complete result from the preview tool>,
  confirmation="CREATE EXPERIMENTAL GRAPH NODES <planId>"
)
```

The Editor revalidates the exact focused graph, clean package, stopped PIE/SIE,
current sha256 revision and sha1 graph fingerprint, and recomputes `planId`. It
then:

1. opens one `FScopedTransaction`;
2. spawns only the previewed Branch/Sequence classes, or the one pure
   `KismetMathLibrary.Not_PreBool` CallFunction node;
3. verifies each spawned pin's actual K2 category (`PC_Exec` / `PC_Boolean`)
   against the declared type, then creates only the previewed connection with
   `TryCreateConnection`;
4. verifies both pin directions, node GUIDs, node/link deltas and dirty state;
5. proves Undo restores the exact pre-apply node/link counts and removes the new
   GUIDs, then Redo restores them;
6. returns the created node IDs and the post-apply graph fingerprint.

It never compiles, saves, or calls Save All. A separate explicit save remains
the caller's responsibility and is outside this slice.

## Failure codes

- `graph-edit-experimental-disabled` — project or server opt-in missing.
- `graph-edit-focused-graph-mismatch` — exact focused graph is not the request.
- `graph-edit-package-dirty` / `graph-edit-pie-active` / `graph-edit-graph-not-editable`.
- `graph-edit-node-type-not-allowed` / `graph-edit-pin-not-allowed`.
- `graph-edit-pin-type-mismatch` — declared/actual pin kind differs.
- `graph-edit-connection-duplicate` — same output or same input already used.
- `graph-edit-connection-limit` — per-node connection cap exceeded.
- `graph-edit-plan-stale` — graph or request changed after Preview.
- `graph-edit-confirmation-required` — wrong confirmation phrase.
- `graph-edit-node-spawn-failed` / `graph-edit-connection-failed`.
- `graph-edit-readback-failed` / `graph-edit-undo-redo-failed`.

## Current limitations

- Only Branch, Sequence and the pure NOT Boolean CallFunction node. No
  arbitrary CallFunction, variable, cast, event, macro or comment nodes.
- Only exec pins and the exact Bool `ReturnValue -> Condition` data link. No
  other data pins, pin defaults, or disconnect/replace operations.
- No delete-node and no Save All.
- `independentReloadVerified` is not claimed; the readback is same-process.
- The full GraphPatch profile/idempotency/receipt pipeline is not executed by
  this live slice.

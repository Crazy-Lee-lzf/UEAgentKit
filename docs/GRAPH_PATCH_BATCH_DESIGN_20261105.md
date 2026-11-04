# GraphPatch batching and session-level authorization — design (2026-11-05)

Status: **not implemented; design only.** This is stage 0 item 0-3.4. No writer
was converted in this round; the existing five experimental writers keep their
current per-operation Preview → Apply → Save contracts.

## Goal

One GraphPatch submission creates nodes, connects pins and sets unconnected pin
defaults in a single `FScopedTransaction`, returns one per-operation result list,
rolls the whole patch back if any operation fails, and never silently skips an
operation. The per-operation confirmation phrase is replaced by one authorization
at task/EditSession level.

## Current state (why it is three calls today)

| Writer | Bridge method family | Authorization today |
| --- | --- | --- |
| graph edit (create nodes/edges) | `editor.*BlueprintGraphEditExperimental` | planId + `requiredConfirmation` per Apply and per Save |
| pin defaults | `editor.*BlueprintPinDefaultEditExperimental` | same |
| rewire | `editor.*BlueprintRewireExperimental` | same |
| node remove | `editor.*BlueprintNodeRemoveExperimental` | same |
| variable create | `editor.*BlueprintVariableWriteExperimental` | same |

Only node movement is inside the EditSession token/batch framework
(`BlueprintEditSession` + `ApplyNodeMoveBatch`). The five writers each build
their own transaction and their own confirmation contract.

## Proposed structure

1. **One patch object** (schema `ueak.graphpatch/1.0`), applied atomically:
   ```json
   {"assetPath": "...", "graphGuid": "...",
    "sessionToken": "<64 lowercase hex>",
    "expectedGraphFingerprint": "sha1:...",
    "operations": [
      {"opId": "n1", "kind": "createNode", "nodeType": "branch", "tempId": "tmp:a", "x": 0, "y": 0},
      {"opId": "e1", "kind": "connect", "from": "tmp:a", "fromPin": "then", "toNodeGuid": "...", "toPin": "execute"},
      {"opId": "p1", "kind": "setPinDefault", "nodeGuid": "...", "pinGuid": "...", "type": "bool", "value": "true"}
    ]}
   ```
   `tempId` is resolved to real GUIDs inside the patch; later operations may
   reference earlier `tempId`s.
2. **Single transaction.** The patch is applied inside one
   `FScopedTransaction`; every operation is validated against the live graph
   before the first write, then applied, then read back. Any failure restores the
   pre-patch `UEdGraph` state (nodes removed, pins re-linked, defaults restored),
   cancels the transaction and returns per-operation errors with
   `writeOccurred=false`.
3. **Per-operation results.** `results[]` carries `opId`, `status`
   (`applied`/`failed`), the resolved GUID(s), and the precise error code. A
   failed patch reports every operation's outcome; nothing is skipped.
4. **Session-level authorization.** The confirmation phrase becomes a property of
   the EditSession: `begin` returns a session token bound to asset + graph +
   fingerprint, and the caller authorizes the *task* once
   (`experimental=true` + one explicit grant). Each patch is then authorized by
   the token, not by a per-apply phrase. The grant must be revocable with
   `end()` and must expire on any fingerprint/Dirty/target change that today
   invalidates a session.
5. **Writer migration order** (each behind its own flag, one commit each):
   1. pin defaults — smallest surface, no graph structure change;
   2. variable create — asset-level, no edge rewiring;
   3. graph edit — already the general case; becomes the batch entry point;
   4. rewire — needs exact edge-set proofs;
   5. node remove — most destructive, last.
   Until a writer is migrated it keeps its current confirmation contract; mixing
   is allowed but a patch may not contain both migrated and unmigrated kinds.

## Security invariants that must not be relaxed

- No arbitrary class/`nodeClass` from the request; only the allowlisted node
  types in `experimental_graph_edit.NODE_TYPES` and its siblings.
- Policy (`allowedAssetRoots`, `allowedOperations`, `allowedAssetClasses`) still
  applies to every operation before any transaction opens.
- The session token remains 64 lowercase hex, case-sensitive, bound to the
  Bridge session + process, and never accepted from a non-session path.
- A patch failure must never report success for a partially applied graph.

## Why not implemented in this round

Converting the writers is a large, high-risk change that cannot be compiled or
run here (no UBT/Editor), and the current per-writer confirmation contracts are
part of N1's accepted evidence. Doing half of it would create a mixed
authorization model with no native proof. The design above is the prerequisite
for the next P1 slice, where each writer migration is compiled by N1
incrementally.

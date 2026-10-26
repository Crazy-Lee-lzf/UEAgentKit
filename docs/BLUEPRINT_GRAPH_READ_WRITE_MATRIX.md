# Blueprint Graph Read/Write Capability Matrix

Verified scope: UE 5.6.1, Editor Bridge and Python MCP.
Updated: 2026-10-26.
Scope: UE 5.6.1, Editor bridge/plugin plus Python MCP/index readers.

This matrix describes the product contract on this branch. It deliberately does
not treat fixture-only helpers or the earlier position-only layout acceptance as
Blueprint structure write support.

Legend:

- **Supported** — implemented and covered by tests in this branch.
- **Experimental** — implemented behind an explicit project opt-in and exact
  per-call confirmation; not a production default.
- **Offline-only** — validates or previews data without touching the live
  Editor.
- **Unsupported** — no live product path; fail closed.

## Matrix

| Area | Status | Evidence / boundary | Priority |
|---|---|---|---|
| Canonical BPCTX read of Blueprint graphs, nodes, pins, links, comments | **Supported** | `BlueprintContextExporter`; graph GUID + kind + revision | — |
| Nested `UEdGraph::SubGraphs` discovery | **Supported (read)** | `CollectBlueprintGraphs` classifies nested graphs as `subgraph`; parent names exported | — |
| Focused graph identity, asset path, graph GUID, revision, fingerprint preflight | **Supported** | Shared layout preflight; fail closed on mismatch/dirty/PIE | — |
| Live GraphEdit `createNode` + `connectPins` preview/apply | **Experimental** | One transaction; allowlisted deterministic nodes; exact exec/bool/int/double/string pins; output fan-out; readback + Undo/Redo | P0 complete |
| Allowlisted nodes: `branch`, `sequence`, `not_bool`, `bool_or`, `int_add`, `int_equal`, `double_add`, `string_concat` | **Experimental** | Python + C++ duplicate contracts agree on class/function and pin names/types | P0 complete |
| Exact typed data links and output fan-out to distinct inputs | **Experimental** | One output may feed several distinct input pins; an input pin still accepts one plan link | P0 complete |
| Plan-bound explicit Save | **Experimental** | Separate tool/gate; requires Apply receipt, post-Apply fingerprint, exact node/link counts, dirty package, clean readback, disk SHA-256 | P0 complete |
| Post-commit failure rollback | **Experimental** | `FailCommitted` Undo + original count/link readback; explicit warning if rollback fails | P0 complete |
| Cross-process reload of node/edge GUID sets | **Supported (read verification)** | Independent commandlet verifies saved graph GUIDs, edge strings, counts and structure SHA in a new process | P0 complete |
| Existing unlinked K2 input-pin default edit, bool/int/double/string only | **Experimental** | Exact node GUID + pin name + pin GUID; strict canonical encodings; one transaction with in-call Undo/Redo verification | P1 complete |
| Plan-bound pin-default Save | **Experimental** | Separate gate bound to the Apply receipt, post-Apply graph and pin-default-state fingerprints, exact node/link counts, dirty package, disk SHA-256 readback | P1 complete |
| Cross-process reload of edited pin defaults | **Supported (read verification)** | Fresh-process commandlet verifies the four typed defaults, pin GUIDs, exact node set, exact link set and saved asset SHA-256 | P1 complete |
| Create variables / change pin types / rewire existing pins | **Unsupported** | Pin defaults on existing unlinked inputs are the only pin-level mutation; no variable or link write path | P1 |
| Delete nodes / disconnect links | **Unsupported** | Deliberately excluded from this slice | P1 |
| Arbitrary GraphPatch live apply | **Unsupported** | `ue_preview_graph_patch` is **Offline-only**; no Editor transaction/save | P1 |
| Nested-subgraph live edit | **Unsupported** | Read/discovery only; exact focused graph write currently top-level graph path | P1 |
| Compile / auto-save after GraphEdit | **Unsupported by design** | Apply returns `saved=false`, `compiled=false`; Save never compiles | — |
| Production default enablement | **Unsupported by design** | Requires `[UEAgentKit] ExperimentalGraphPatchEdit=True` and MCP `--enable-experimental-graphpatch-edit` | — |

## Safety invariants

- Exact focused Blueprint graph only; no asset-path-only or arbitrary graph write.
- Clean package before Apply; stopped PIE/SIE; K2 schema only.
- One `FScopedTransaction`; exact readback of node GUIDs, pin connections, counts,
  package dirtiness, Undo and Redo.
- Plan ID binds asset path, graph GUID, package revision, graph fingerprint, and
  canonical node/link request.
- Pin-default Plan ID additionally binds the before pin-default-state
  fingerprint; a linked, hidden, read-only, ignored, container or by-reference
  pin, a type mismatch, or a stale plan fails closed before any mutation.
- Pin-default Apply writes only `UEdGraphPin::DefaultValue` on existing
  top-level unlinked inputs; it creates, deletes, connects and disconnects
  nothing, and it never compiles.
- Save is a second explicit gate bound to the successful Apply receipt and
  post-Apply fingerprint/counts; it cannot save an unrelated dirty package edit.
- Python and native code both reject unknown parameters, node types, pins, types,
  duplicate target pins, and over-limit plans.
- No deletion, disconnection, variable mutation, pin creation, or default
  mutation outside the bounded four-type input-pin path, arbitrary GraphPatch,
  auto-save, or compile in this slice.

## Known limits / handoff

- Save receipts are per-Editor-process; after an Editor restart, Save must follow
  a new Apply in that process.
- Pin-default Save receipts are also per-Editor-process and are consumed by the
  exact Save plan.
- The pin-default slice edits defaults only; it cannot add variables, add or
  remove nodes, change pin types, or rewire links.
- Save readback cannot compensate a failure that happens after `SavePackage`
  has already replaced the file; such failures are reported, not hidden.
- The independent reload verifies structure GUIDs/edges and saved bytes; it is
  not a second write capability.
- Nested-subgraph writes, variable edits, pin creation/retyping,
  delete/disconnect, and live GraphPatch apply remain follow-up work.

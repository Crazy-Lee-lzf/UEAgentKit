# Blueprint Graph Read/Write Capability Matrix

Verified scope: UE 5.6.1, Editor Bridge and Python MCP.
Updated: 2026-10-26.

This matrix describes the implemented Blueprint graph read/write contract. It deliberately does
not treat fixture-only helpers or the earlier position-only layout acceptance as
Blueprint structure write support.

Legend:

- **Supported** — implemented and covered by the cited tests.
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
| Create top-level member variables, bool/int/double/string only | **Experimental** | Exact focused, already-saved Blueprint; strict ASCII name rules; duplicate and inherited-name rejection; engine-standard variable description and flags; one transaction with in-call Undo/Redo readback | P2 complete |
| Plan-bound variable-creation Save | **Experimental** | Separate gate bound to the variable Apply receipt, post-Apply variable-state fingerprint, on-disk revision and exact variable count; pending default strings written to disk | P2 complete |
| Cross-process reload of created variables | **Supported (read verification)** | Fresh-process commandlet verifies variable GUIDs, declared types, pending or generated-class-CDO default values, exact variable/node/link sets and saved asset SHA-256 | P2 complete |
| Rewire one existing connected input pin to one exact new output pin | **Experimental** | Exact focused top-level K2 event graph; binds old source and target node/pin GUIDs, new source node/pin GUID, old graph fingerprint, on-disk package revision and the exact before edge-set hash; breaks only the one planned target link; rejects any K2 response that would break other links | P3 complete |
| Explicit disconnect of one known existing edge | **Experimental** | Same operation gate as rewire; `disconnect` forbids `newSource*`; exactly one planned edge is removed and every other edge is proven untouched | P3 complete |
| Planned no-op / stale topology rejection | **Experimental** | Rejects rewire to the current source, unlinked or multi-link targets, an old source that is not the target's only link, and any edge set that changed after Preview | P3 complete |
| Plan-bound rewire/disconnect Save | **Experimental** | Separate gate bound to the Apply receipt, post-Apply edge-set hash, pin-identity hash, package revision and exact node/link counts; never compiles | P3 complete |
| Cross-process reload of a rewired/disconnected edge set | **Supported (read verification)** | Fresh-process commandlet verifies the final edge set exactly, the two removed edges are absent, the new and untouched edges are present, every node GUID/class and pin name/GUID/direction/category is unchanged, and the saved asset SHA-256 matches | P3 complete |
| Change pin types / rename or delete variables / create or delete nodes or pins | **Unsupported** | Rewire reuses existing pins on both ends; nothing is retyped, renamed, created or deleted | P3 |
| Broad `BreakAllPinLinks` / bulk edge rewiring | **Unsupported** | Rewire breaks exactly the one target link that the plan names; output fan-out on every other pin is preserved | P3 |
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
- Variable creation appends only top-level member variables to the exact focused,
  already-saved Blueprint: strict ASCII names, 1-8 variables per plan, four
  primitive types, no containers, by-ref, const, weak or composite types.
- Variable Plan ID binds asset path, focused graph GUID, on-disk package
  SHA-256, graph fingerprint, the before variable-state fingerprint (GUID, name,
  type, default, flags) and the canonical target list.
- Variable Apply is one `FScopedTransaction` on `UBlueprint::NewVariables`; it
  sets the engine-standard flags (`CPF_Edit | CPF_BlueprintVisible |
  CPF_DisableEditOnInstance`), friendly name, category, replication condition
  and string `MultiLine` metadata, and proves exact before/after Undo/Redo state.
- Variable Apply intentionally does not call
  `FBlueprintEditorUtils::AddMemberVariable`: that engine API synchronously
  regenerates the edited Blueprint's skeleton class and can rewrite child
  Blueprint variables. This slice never compiles, never regenerates a skeleton,
  and never saves another asset.
- Variable Save requires the per-process Apply receipt, the post-Apply variable
  fingerprint, the same on-disk package revision and the exact variable count; it
  never compiles.
- UE 5.6 stores `FBPVariableDescription::DefaultValue` as a compile-time transfer
  field: the first compile copies it onto the generated-class CDO property and
  clears the string. This slice saves the pending string without compiling; the
  independent reload proves the effective persisted defaults through the
  generated-class CDO after the engine's own load-time compile, and reports
  which source proved each value.
- Rewire/disconnect is one exact existing edge on one exact focused top-level
  K2 event graph: the target input pin must currently have exactly one link and
  that link must be the declared old source node/pin GUID; the new source must
  be an exact type-compatible K2 output on the same graph.
- Rewire never calls `UEdGraphSchema::BreakAllPinLinks` and never accepts a
  schema response that would break other links. The Apply manually breaks only
  the planned target link, then requires the post-break `CanCreateConnection`
  response to be exactly `CONNECT_RESPONSE_MAKE` before
  `TryCreateConnection` makes the single new link. `BREAK_OTHERS_A/B/AB`,
  conversions and promotions are rejected before any mutation.
- Rewire/disconnect Plan ID binds asset path, focused graph GUID, on-disk
  package revision, graph fingerprint, the before edge-set hash, the exact old
  source and target node/pin GUIDs, the new source node/pin GUID (rewire only)
  and the operation.
- Apply re-verifies the live edge-set hash and plan ID, performs one
  `FScopedTransaction`, proves the exact after edge-set hash, pin-identity hash
  and node/link counts, then proves Undo and Redo restore the exact before and
  after edge sets. A post-commit mismatch restores the before topology and does
  not leave a half-rewired graph.
- Rewire Save requires the per-process Apply receipt, the post-Apply edge-set
  hash, the caller-echoed package revision and exact node/link counts, and the
  live disk revision; it never compiles and never saves another asset.
- The rewire contract supports only exact `exec` and `bool`/`int`/`double`/
  `string` primitive pins. Wildcard, delegate, container, by-reference, const,
  weak, struct/object/enum, hidden, orphaned and split pins are rejected, as are
  exec/data mismatches and any type mismatch.
- No deletion, disconnection, pin creation, variable rename/retype/removal, or
  default mutation outside the bounded four-type paths, arbitrary GraphPatch,
  auto-save, or compile in this slice.

## Known limits / handoff

- Save receipts are per-Editor-process; after an Editor restart, Save must follow
  a new Apply in that process.
- Pin-default and variable-creation Save receipts are both per-Editor-process
  and are consumed by the exact Save plan.
- The pin-default slice edits defaults only; it cannot add variables, add or
  remove nodes, change pin types, or rewire links.
- The variable slice appends top-level member variables only. It cannot create
  function-local variables, rename, retype or delete existing variables, add
  variable nodes, change pin types, or rewire links.
- Creating a variable leaves the generated class intentionally stale until an
  explicit later compile; this slice never compiles, and the Blueprint is saved
  in a pending-compile state that UE materializes on the next editor compile.
- Save readback cannot compensate a failure that happens after `SavePackage`
  has already replaced the file; such failures are reported, not hidden.
- Rewire/disconnect Save receipts are per-Editor-process as well; the
  `independentReloadVerified` flag stays `false` until the separate
  `BlueprintRewireReload` commandlet proves the saved bytes in a new process.
- The rewire slice edits one edge at a time on top-level K2 event graphs. It
  cannot create or delete nodes/pins, retype pins, rewrite an exec output's
  fan-out in bulk, or edit nested subgraphs; each additional edge needs its own
  Preview/Apply/Save plan.
- The rewire slice reuses existing pins only; it cannot repair a Blueprint whose
  pins or generated class were already stale, and it never compiles.
- The independent reload verifies structure GUIDs/edges, variable identity,
  defaults and saved bytes; it is not a second write capability.
- Nested-subgraph writes, variable rename/retype/removal, pin
  creation/retyping, node deletion, and live GraphPatch apply remain follow-up
  work.

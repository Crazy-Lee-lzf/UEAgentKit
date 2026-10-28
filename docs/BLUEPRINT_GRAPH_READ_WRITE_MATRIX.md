# Blueprint Graph Read/Write Capability Matrix

Verified scope: UE 5.6.1, Editor Bridge and Python MCP.
Updated: 2026-10-28.

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
| Focused graph identity, asset path, graph GUID, revision, fingerprint preflight | **Supported** | Shared layout preflight; fail closed on mismatch/dirty/PIE; reports `focusSource` (`live-slate-focus` or verified `explicit-focus-request`) | — |
| Open and focus one exact Blueprint graph from MCP | **Supported (opt-in)** | `editor.focusBlueprintGraph` + `ue_focus_blueprint_graph`, registered only with `--enable-experimental-graphpatch-edit`; explicit focus fallback is accepted only for the same owning Blueprint | P5 complete |
| Read-only edge-set hashes for rewire/node-remove plans | **Supported** | Layout preflight exposes `rewireEdgeSetHash` and `nodeRemoveEdgeSetHash` computed by the same native helpers used by Apply | P5 complete |
| Five-family MCP → Socket → Apply → Save → fresh-process Reload acceptance | **Supported (experimental)** | `tests/python/p5_agent_e2e_probe.py` + `scripts/VerifyBlueprintAgentEndToEnd.ps1`; the accepted end-to-end run proved GraphEdit, pin defaults, variables, rewire and isolated node removal end to end | P5 complete |
| Live GraphEdit `createNode` + `connectPins` preview/apply | **Experimental** | One transaction; allowlisted deterministic nodes; exact exec/bool/int/double/string pins; output fan-out; readback + Undo/Redo | P0 complete |
| Allowlisted nodes: `branch`, `sequence`, `not_bool`, `bool_or`, `int_add`, `int_equal`, `double_add`, `string_concat` | **Experimental** | Python + C++ duplicate contracts agree on class/function and pin names/types | P0 complete |
| Whitelisted engine `call_function` nodes (`Greater_IntInt`, `Less_IntInt`, `Clamp`, `Abs_Int`) | **Static contract only, NATIVE PENDING** | Fixed `/Script/Engine.KismetMathLibrary` owner + function + signature digest + exact pin schema; arbitrary paths, world/latent/network/receiver/ref/const/container calls rejected. Python/C++ contracts and source-level allowlist implemented, but **no UE build/Editor A-level has been run in this lease window**; do not treat as a usable write capability until native Preview→Apply→Undo/Redo→Save→Reload passes | 2026-10-28 STATIC ONLY |
| Create `variable_get` / `variable_set` nodes for an existing top-level member variable (bool/int/double/string) | **Experimental** | Same GraphEdit Preview/Apply/Save contract; variable bound by GUID + name + exact type; `UBlueprintVariableNodeSpawner` creates the engine-standard pins; post-spawn and post-Redo checks prove class, self-context member identity and exact pins. **A-level** real MCP → Socket → Editor Preview/Apply/Save for four types in the event graph and one int accessor pair in a function graph; fresh-process canonical export Reload verified all 10 accessors, bindings, pins and asset SHA | 2026-10-28 |
| Variable accessor connection targets within the same plan (getter output, setter `execute`/`Output_Get`) | **Experimental** | Exact exec or exact primitive data links between the declared accessor pins, using the same fan-out and type rules as the static nodes. The A-level run connected all four getters to their setters and drove the int setter from a `sequence` exec pin | 2026-10-28 |
| Same-type variable retarget between accessor Apply and Save | **Experimental (native B)** | Receipt-side `variableBindingHash` covers every `UK2Node_Variable` binding/pin. Native automation `UEAgentKit.Blueprint.AccessorRetargetSaveGate` retargets `AccessInt` to `AccessInt2` after Apply and proves Save fails with `graph-edit-save-variable-binding-mismatch`; cleanup Undo restores the graph | 2026-10-28 |
| Accessor Preview/Apply while PIE is active | **Experimental (native B)** | Native automation `UEAgentKit.Blueprint.AccessorPieGate` starts real PIE and proves Preview fails with `graph-edit-pie-active`; no mutation occurs | 2026-10-28 |
| Connect one new allowlisted node/pin to one exact existing K2 pin | **Experimental** | Request uses `fromExisting`/`toExisting` with nodeGuid + pinGuid + pinName + direction + category/subCategory. The other endpoint must be a node created in the same plan. Existing input must be unlinked; existing output fan-out is preserved. **A-level** real MCP → Socket → Editor Preview/Apply/Save: new int getter/setter plus bool getter connected to an existing Branch `execute`/`Condition` and an existing event `then`; 4 edges, Undo/Redo true, fresh-process Reload verified exact links and asset SHA | 2026-10-28 |
| Connect one existing output to one existing unlinked input with `nodes=[]` | **Experimental** | Exactly one existing→existing edge per plan; both endpoints exact `UK2Node`/pin GUID/name/direction/category/subCategory in the same graph, different nodes. Target input must be unlinked; source output fan-out is preserved. **A-level** real MCP → Socket → Editor Preview/Apply/Save: one existing Event `then` → existing Branch `execute`, 4 nodes/1 edge (unchanged node count), Undo/Redo true, fresh-process Reload verified the exact edge and asset SHA | 2026-10-28 |
| Remove one exact existing K2 edge with `operation=disconnect`, `nodes=[]` | **Experimental** | Exactly one existing output→input edge per plan; both endpoints exact node/pin GUID/name/direction/category/subCategory in the same graph, different nodes, target input currently has exactly that one link. Apply breaks only that pair, proves edge-set delta = −1, preserves all other edges/nodes, Undo/Redo hashes and Save receipt. **A-level** real MCP → Socket → Editor Preview/Apply/Save: one of two Event→Branch edges removed, the other preserved, 7 nodes/1 edge, fresh-process Reload verified; disconnect-after-Preview stale/PIE are native B | 2026-10-28 |
| Reject unsafe existing-pin connections | **Experimental** | Disconnect of a non-existent/ambiguous/already-removed edge, disconnect with nodes or a non-existing endpoint, existing→existing beyond the one zero-node edge, linked existing input for connect, unknown node/pin GUID, category/subCategory mismatch, hidden/orphan/split/non-connectable pin, type mismatch, `BREAK_OTHERS_*`, conversion/promotion all fail closed. Native automation also proves zero-node disconnect after graph mutation is stale (`graph-edit-plan-stale`/`graph-edit-edge-set-stale`) and PIE rejects the path | 2026-10-28 |
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
| Remove one isolated allowlisted deterministic node | **Experimental** | Exact focused top-level K2 event graph; only an allowlisted pure Kismet math call or a branch; node GUID + class, revision, graph fingerprint and before edge-set hash bound in the plan; zero incident edges; one transaction with Undo/Redo node-restore proof | P4 complete |
| Remove one allowlisted node with exactly one enumerated incident edge | **Experimental** | The request must list that single edge by both node GUID + pin name + pin GUID; Apply breaks only that enumerated edge, refuses any unplanned link, then removes the node and proves every other edge and pin identity survived | P4 complete |
| Plan-bound node-removal Save | **Experimental** | Separate gate bound to the Apply receipt, post-Apply edge-set/pin-identity/node-identity hashes, package revision and exact node/link counts; never compiles | P4 complete |
| Cross-process reload after node removal | **Supported (read verification)** | Fresh-process commandlet verifies both deleted GUIDs are absent, every remaining node GUID/class and pin inventory is unchanged, the exact final edge set is present, the removed edge is absent, untouched fan-out/exec edges survive and the asset SHA-256 matches | P4 complete |
| Change pin types / rename, retype or delete variables / create variable accessors outside the bounded slice / create pins / delete nodes outside the P4 allowlist | **Unsupported** | Node removal deletes exactly one allowlisted existing node; nothing is retyped, renamed, created or rewired outside the explicit GraphEdit allowlist | P4 |
| Remove comment groups, event entries, timelines, knots, tunnels, macros, variable accessors, sequences or unknown custom nodes | **Unsupported** | Only `K2Node_CallFunction` with an allowlisted pure `UKismetMathLibrary` function and `K2Node_IfThenElse` are removable; every other class fails closed | P4 |
| Remove a node with more than one incident edge / bulk node deletion | **Unsupported** | A plan removes exactly one node and at most one enumerated incident edge; larger deletions need a separate capability and are not silently approximated | P4 |
| Broad `BreakAllPinLinks` / bulk edge rewiring | **Unsupported** | Rewire breaks exactly the one target link that the plan names; output fan-out on every other pin is preserved | P3 |
| Arbitrary GraphPatch live apply | **Unsupported** | `ue_preview_graph_patch` is **Offline-only**; no Editor transaction/save | P1 |
| Nested-subgraph live edit | **Unsupported** | Read/discovery only; exact focused graph write currently top-level graph path | P1 |
| Compile / auto-save after GraphEdit | **Unsupported by design** | Apply returns `saved=false`, `compiled=false`; Save never compiles | — |
| Production default enablement | **Unsupported by design** | Requires `[UEAgentKit] ExperimentalGraphPatchEdit=True` and MCP `--enable-experimental-graphpatch-edit` | — |

## Safety invariants

- Exact focused Blueprint graph only; no asset-path-only or arbitrary graph write.
- `ue_focus_blueprint_graph` opens/focuses one exact asset + optional graph GUID and
  records an explicit-focus request. Preflight prefers live Slate focus and uses the
  recorded request only when Slate reports no focused graph and the graph is still
  owned by the same Blueprint; it never relaxes the asset/graph identity checks.
- Clean package before Apply; stopped PIE/SIE; K2 schema only.
- One `FScopedTransaction`; exact readback of node GUIDs, pin connections, counts,
  package dirtiness, Undo and Redo. GraphEdit marks created nodes
  `RF_Transactional`; PinDefault marks each target node before writing a pin so a
  transaction cannot be discarded as an empty diff after GraphEdit Undo/Redo.
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
  It also compares a receipt-side hash of every `UK2Node_Variable` binding
  (GUID, name, self-context, read/write and accessor pins), so a same-type
  variable retarget between Apply and Save fails closed with
  `graph-edit-save-variable-binding-mismatch`.
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
- Node removal is one exact existing node on one exact focused top-level K2
  event graph. The class must be `K2Node_CallFunction` on an allowlisted pure
  `UKismetMathLibrary` function, or `K2Node_IfThenElse`; the plan binds the node
  GUID, exact class path, on-disk package revision, graph fingerprint, before
  edge-set hash, the exact incident-edge list and the pre/post pin and node
  identity hashes.
- A plan removes exactly one node with at most one incident edge. Every
  incident edge must be enumerated on both ends by node GUID + pin name + pin
  GUID; any unplanned link, a linked hidden pin, a wildcard/container/by-ref/
  const/weak/delegate/struct pin, a comment group, event entry, timeline, knot,
  tunnel, macro, variable accessor, sequence or unknown class fails closed
  before mutation.
- Node-removal Apply breaks only the enumerated edge(s), refuses to delete a
  node that still has any link, removes only that node inside one
  `FScopedTransaction`, and proves the exact after node/pin/edge sets. It then
  verifies Undo restores the node and its links and Redo removes them again; a
  post-commit mismatch tries one Undo restore and reports failure if the before
  topology cannot be proven.
- Node-removal Save requires the per-process Apply receipt, the post-Apply
  edge-set/pin-identity/node-identity hashes, the caller-echoed package revision
  and exact node/link counts, and the live disk revision; it never compiles and
  never saves another asset.
- Variable accessors are `variable_get` / `variable_set` nodes bound to one
  existing top-level member variable by canonical GUID, exact name and one of
  bool/int/double/string. The plan additionally binds the read/write direction.
  Apply re-resolves the generated/skeleton property, requires the same primitive
  type, and uses `UBlueprintVariableNodeSpawner::CreateFromMemberOrParam` so the
  engine itself builds the self-context `K2Node_VariableGet/Set` and its pins.
- Variable accessor creation is limited to the Blueprint's top-level event,
  function and macro graphs. Interface/extension graphs and nested
  `UEdGraph::SubGraphs` fail closed with
  `graph-edit-variable-graph-not-supported`.
- A variable accessor plan never creates, renames, retypes or deletes a
  variable, never edits a variable default, and never compiles. If the variable
  has no generated/skeleton property (pending compile or stale class), Preview
  fails closed with `graph-edit-variable-property-unavailable`.
- Post-spawn and post-Redo verification prove the node class, self-context
  member GUID + name and the exact engine pins (`variable_get`: output
  `<VariableName>`; `variable_set`: input `execute`, input `<VariableName>`,
  output `then`, output `Output_Get`). A mismatch aborts the transaction with
  `graph-edit-variable-pin-contract-failed`.
- A variable-accessor Apply opens one `FScopedTransaction`; the native A-path
  run returns `undoVerified=true` and `redoVerified=true`, leaves the package
  dirty, and requires a separate explicit Save. The Save receipt additionally
  binds `variableBindingHash` over every live `UK2Node_Variable` in the graph.
- Accessor Preview/Apply is rejected while PIE/SIE is active
  (`graph-edit-pie-active`); the native PIE automation test proves no mutation.
- A same-type retarget after Apply is rejected by Save
  (`graph-edit-save-variable-binding-mismatch`); the native retarget test then
  Undo-restores the original graph.
- Connect-existing permits either one existing endpoint plus one new endpoint,
  or exactly one existing→existing edge with `nodes=[]`. A plan may contain at
  most one pure existing→existing connection, and that plan must contain exactly
  one connection and zero new nodes. The two endpoints must be different
  `UK2Node`s in the same focused graph with exact canonical node GUID + pin GUID
  + name + direction + category/subCategory; the existing input must have zero
  links.
- Disconnect is a separate `operation=disconnect` mode: `nodes=[]`, exactly one
  existing→existing connection, and the target input must currently have exactly
  one link from the declared output. Apply removes only that exact pair with
  `BreakLinkTo`; it must not call `CanCreateConnection`, must not touch other
  edges, and must prove edge-set removed=1/added=0 plus unchanged node count.
- The existing pin's actual type must be exactly exec/bool/int/double/string
  with no wildcard, container, by-ref, const, weak, delegate, struct/object/enum
  or split/orphan/hidden flag. The declared request metadata must equal the live
  pin metadata.
- Apply re-resolves the existing pin and revalidates its link count, then calls
  `CanCreateConnection` immediately before mutation. Only
  `CONNECT_RESPONSE_MAKE` is accepted; `BREAK_OTHERS_A/B/AB`,
  `MAKE_WITH_CONVERSION_NODE` and `MAKE_WITH_PROMOTION` are rejected before any
  mutation. Existing output fan-out is never broken.
- Connect-existing Plan ID binds the graph fingerprint, package revision,
  before edge-set hash and the exact existing node/pin identity/type/link state.
  Apply recomputes the before edge-set hash and refuses a stale plan. It records
  before/after edge-set hashes, proves Undo restores the before hash and Redo
  restores the after hash, and stores the after hash in the Save receipt.
- Save additionally compares the receipt edge-set hash for connect-existing
  plans (`graph-edit-save-edge-set-mismatch`) and still requires the explicit
  Apply receipt and post-Apply fingerprint/counts.
- No deletion, disconnection, pin creation, variable rename/retype/removal, or
  default mutation outside the bounded five-type paths, arbitrary GraphPatch,
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
- Node-removal Save receipts are per-Editor-process as well; the
  `independentReloadVerified` flag stays `false` until the separate
  `BlueprintNodeRemoveReload` commandlet proves the saved bytes in a new
  process.
- The node-removal slice deletes one node at a time, only in top-level K2 event
  graphs, only for the allowlisted pure-function/branch classes and only with at
  most one enumerated incident edge. It cannot create nodes or pins, re-route
  the surviving links, delete comment groups or event/tunnel/macro nodes, or
  handle bulk deletions; each additional node needs its own
  Preview/Apply/Save plan.
- The node-removal slice never compiles; it cannot repair a Blueprint whose pins
  or generated class were already stale.
- Save receipts are removed from their per-process TMap only after every result
  field has been copied. Removing them earlier is a use-after-free that can
  silently corrupt returned hashes (fixed for rewire and node removal in P5).
- The independent reload verifies structure GUIDs/edges, node/pin identity,
  variable identity, defaults and saved bytes; it is not a second write
  capability.
- The explicit-focus fallback is process-global for the most recent focus request;
  a stale weak pointer is ignored and live Slate focus always wins. Concurrent
  Blueprint editors are not a supported scenario.
- The live-only MCP server still requires a usable index database at startup even
  when no indexed query is used; P5 uses a disposable empty schema database and
  records this as an open product gap.
- MCP acceptance run18 covers one Actor Blueprint and one ordered
  GraphEdit → pin-default → variable → rewire → isolated-node-remove sequence. It
  does not prove concurrent assets, PIE/dirty-package recovery, disconnect, or
  removal with one incident edge.
- Nested-subgraph writes, variable rename/retype/removal, pin
  creation/retyping, unbounded node deletion, and live GraphPatch apply remain
  follow-up work.
- Variable accessor creation is now accepted at **A level** on the real
  MCP → Socket → Editor → Save → fresh-process Reload path for four primitive
  types in an event graph and for an int getter/setter pair in a function graph
  (10 accessors; `undoVerified=true`, `redoVerified=true`; reload
  `verified=true`, `mismatches=[]`). PIE rejection and same-type-retarget Save
  rejection are **native B** automation evidence. Macro graphs are accepted by
  the same code path but were not exercised in this native run.
- Connect-existing is accepted at **A level** on the real MCP → Socket →
  Editor → Save → fresh-process Reload path: an existing unconnected Branch
  `execute`/`Condition` and an existing event `then` were connected to newly
  created getter/setter nodes (4 connections, Undo/Redo true, 8 nodes / 4 edges,
  reload `verified=true`, `mismatches=[]`). PIE and stale-plan rejection are
  **native B** automation evidence.
- Existing→existing is accepted at **A level** on the real MCP → Socket →
  Editor → Save → fresh-process Reload path for exactly one edge with
  `nodes=[]`: an existing Event `then` was connected to an existing Branch
  `execute` (node count unchanged at 4, edge count 1, Undo/Redo true, reload
  `verified=true`, `mismatches=[]`). Zero-node stale-plan and PIE rejection are
  **native B** automation evidence.
- Exact disconnect is accepted at **A level** on the same path: one of two
  pre-existing Event→Branch edges was removed with `operation=disconnect`,
  `nodes=[]` (7 nodes/2 edges → 7 nodes/1 edge), the other edge and all nodes
  survived, Undo/Redo true, Save receipt/edge hash, reload `verified=true`,
  `mismatches=[]`. Disconnect-after-Preview stale and PIE rejection are
  **native B**.
- The slice still cannot rewire/replace/disconnect multiple edges, change an
  existing pin type, connect multiple existing→existing edges, or mix a pure
  existing plan with new nodes. A pure existing plan has exactly one edge, zero
  new nodes; connect requires an unlinked target input, disconnect requires the
  exact existing link.
- Connect-existing only accepts the four primitive data kinds plus exec. It
  rejects object/struct/enum/delegate/array/set/map/by-ref/const/weak/wildcard,
  hidden/orphan/split/non-connectable pins, inherited/non-K2 nodes and any
  cross-graph node GUID.
- Save receipts remain per-Editor-process; the connect-existing receipt binds
  the after edge-set hash but cannot survive a process restart.
- The Save-side variable binding hash covers every variable accessor in the
  edited graph, not only the newly created ones. It is intentionally stricter
  than the layout fingerprint (which does not include `FMemberReference`); a
  same-type retarget after Apply is rejected rather than saved.
- Function-local variables, component member variables represented only as SCS
  template properties, and variables whose generated property is missing are
  rejected; they are not silently approximated.

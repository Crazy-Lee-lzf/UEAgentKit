# Graph Layout Stage Preview — 2026-10-23

This is a **Graph Layout stage checkpoint**, not UEAgentKit 0.9/1.0 and not a general
production feature release.

## Included

- Blueprint graph context/preflight contracts and a deterministic Python layout engine
  (layered SCC ordering, comment framing, reroute handling, canonical LayoutPlan 1.0).
- UE5.6.1 editor plugin (`UEAgentKitEditor`) with:
  - read-only graph preflight/context export,
  - a ToolMenus node/pin context entry **Auto Arrange (Preview)** in the disposable test host,
  - an internal position-only Apply handler used by tests,
  - selection and incremental scoped layout plans.
- Python MCP graph-layout preview tooling and the contract/regression suites.
- Disposable-host automation tests for menu generation, selection/incremental apply,
  Undo/Redo, Save and independent reload.

## Verified in this stage (headless)

- UE5.6.1 BuildPlugin succeeded; DLL
  `sha256:7EB2DB917A355DD53BC8F95C7987507C824F4837929F1BB051DFDC8186CB8E6A`.
- Real `UToolMenus::GenerateMenu` with real node and pin contexts contains the P11 entry; the
  generated entry's action produces a read-only plan and a receipt with no save/compile/dirty.
- Selection: scope 5 / moved 2, outside scope unchanged.
- Incremental: scope 5 / moved 2, fixed nodes unchanged.
- Undo/Redo/Save in both cases; strict no-op stays clean; a second process reload verifies
  positions, topology and DLL.
- Python suite in the release tree: see `C_LAYOUT_P11_EDITOR_SCOPE_ACCEPTANCE_REPORT_20261023.md`
  and the P12 release record.

## Safety gates (unchanged)

- Graph apply is **test-only**, gated by exact disposable project identity/marker and exact
  asset/graph/revision; no production general Apply endpoint is exposed.
- Reroute (knot) moves require exact pin type/link evidence; dangling/cyclic/mismatched knots
  fail closed.
- Comment frame movement remains fail-closed outside the disposable fixture proof.
- No PIE/SIE/active-transaction/dirty/stale asset write is allowed.
- No user Lyra/Reforge/production assets were modified in this stage.

## Known limitations

- The menu was verified through the UE ToolMenus API and a real generated action, **not** by a
  human-visible click; a one-time manual GUI smoke test is still recommended before claiming
  editor UX availability.
- No Slate screenshot or minimized-window capture is claimed.
- Layout quality/performance has rough edges: Full mode may move most nodes and is slower;
  selection/incremental currently have one positive fixture example per mode.
- Corpus coverage: 238 Blueprint assets scanned, 973 parseable graphs, 246 holdout,
  969/973 production-layout valid; **0 graphs with >=100 content nodes**, so large-graph
  behavior is not yet demonstrated on real samples.
- The layout engine is Python; C++ native tool architecture is a future discussion, not part of
  this stage.

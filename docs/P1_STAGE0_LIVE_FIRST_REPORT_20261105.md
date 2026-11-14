# P1 stage 0 report — contract, live-first, defect closure (2026-11-05)

Branch `feature/p1-node-move-defect-fix-20261030`, base `1a5ad01`. Long task
A→G. **No UE Editor, UnrealEditor-Cmd, UBT or commandlet was started**, and N1,
`main`, the Host project, P4, GitHub and the engine were not written.

C++ changed here is **not compiled**; every C++ item below is marked
`pending N1 compile`. API access levels were checked against
`E:\EPICGAME\UE_5.6` headers (line numbers listed).

## 1. Item status

| Item | Status | Commit | Note |
| --- | --- | --- | --- |
| A / 0-2 both sides may edit | **done, pending N1 compile** | `4e137c1` | Dirty begin allowed; per-node conflict only; selective rollback |
| B / 0-3.1 live write pre-gate | **done, limited** | `b65c156` | Stale index no longer blocks when the bridge answers `inspectAssetLive`; value-level Plan→Apply drift not yet detectable |
| C / 0-3.2 live graph context | **done, pending N1 compile** | `f785804` | `editor.getBlueprintGraphContext` + MCP tool, paginated, read-only |
| D / 0-3.3 index hot update | **not done (design)** | — | `docs/INDEX_LIVE_HOT_UPDATE_DESIGN_20261105.md` |
| E / 0-3.4 GraphPatch batching | **not done (design)** | — | `docs/GRAPH_PATCH_BATCH_DESIGN_20261105.md` |
| F / 0-4 defect closure | **done (audit) + doc fixes** | this commit | DEF-06/08/09 fixed; DEF-07 earlier `ecc3c69`; DEF-02/03/04/11 deferred |
| G / wrap-up | **done** | this commit | Full suite + clean tree |

## 2. A — both sides may edit the same graph

`BlueprintEditSession.cpp/.h`, `EditorBridgeBlueprintEditSession.cpp`,
`EditorBridgeNodeMoveBatch.cpp`.

- `Begin` no longer requires a clean package; a Dirty package with unsaved user
  edits is accepted.
- `ValidateBatch` no longer compares the whole-graph fingerprint, the node set or
  the Dirty bit. It checks only the nodes the batch names: each must still be at
  the request's `expectedX/expectedY`, otherwise
  `blueprint-edit-session-node-position-conflict` with live/expected values.
- `CommitBatch` records, per written node, the pre-Agent baseline (first touch)
  and the just-written position. The old global `ExpectedPositions` /
  `ExpectedFingerprint` / `bExpectedPackageDirty` state is gone.
- `End(mode=rollback)` restores only Agent-moved nodes whose live position still
  equals the Agent's last write. User-modified nodes are skipped, listed in
  `skippedNodeGuids` and counted in `skippedNodeCount`; the rest still restores.
  The package Dirty bit and the content fingerprint are not preconditions.
- Responses: `requiresUserNotEdit=false`; `exclusiveUI=false` and
  `humanEditingBlocked=false` unchanged; `packageDirtyBefore/After` now report
  the real state; `rollbackStatus=supported-position-only-agent-owned-nodes`,
  `rollbackScope=agent-moved-nodes-not-changed-since`.
- R7 UI gate stays inert (never armed, no `SetEnabled`); N1-R7 is retired.

## 3. B — the write pre-gate uses Editor memory

`workflow_common.py`, `workflow_plan.py`, `editor_bridge.py`.

- When the service advertises `supports_live_asset_inspect` (the production
  `LiveEditorBridgeService` does), `_assert_asset_fresh` reads
  `editor.inspectAssetLive` and returns
  `state=live-editor`, `writeGate=live-editor-memory`, `indexFresh=false`,
  `indexStaleForWrite=false`, plus an `offline-index-stale-ignored-under-live-editor`
  warning. It never raises `index-stale` in that mode.
- A live Editor that does not know the asset refuses with
  `live-editor-asset-not-found`.
- `PlanRecord.live_baseline` records the live class/package and Apply re-checks
  it; a different asset identity between Plan and Apply fails with
  `live-baseline-changed`.
- A service without the capability (older bridge, test doubles) keeps the
  previous offline gate byte-for-byte; with no live Editor, `index-stale`
  still refuses.
- **Limit:** no public live read returns the current scalar value, so a value
  changed between Plan and Apply is not rejected by the pre-gate; the apply
  handler still reads the live before-value. Recorded for N1.

## 4. C — read any Blueprint graph live

- `editor.getBlueprintGraphContext`: `assetPath` required, optional `graphGuid`,
  `offset`/`limit` (limit ≤ 500, default 200). It reuses
  `FBlueprintContextExporter::BuildContextObject`, filters one graph when asked,
  slices each graph's `nodes` and reports `totalNodeCount`/`returnedNodeCount`/
  `truncated`. Unknown fields are rejected. An unloaded Blueprint is loaded on
  demand (`loadedByRequest`) and the handler never dirties, saves, opens a tab or
  focuses a graph.
- MCP `ue_get_blueprint_graph_context` (live-read, read-only) validates the asset
  path, GUID, offset and limit, enforces the 700 KiB response bound, and is in
  the pure-live set. Tool registry counts updated (live modes +1).

## 5. F — defect audit

Evidence table is in `docs/P1_DEFECT_STATUS.md` §"Stage 0". Summary:

- **P0-R2 WAL quiesce** — implemented (`agent_api.py:196-205`,
  `database.py:559-560`); no regression found.
- **P0-R3 manifest prune** — root-scoped only (`indexer.py:698-726`,
  `:1076-1084`); no global prune path.
- **P0-R5 projectKey isolation** — enforced at construction, in queries and on
  index metadata (`memory_service.py:155-160`, `:166`, `:172`, `:840-849`,
  `:872-875`).
- **P0-R6 Memory Revision false invalidation** — invalidation input only contains
  non-empty revisions (`memory_service.py:850-864`); evidence-backed, original
  repro unavailable.
- **P0-R7 parse failure** — not reproduced from this worktree; needs the original
  repro.
- **DEF-06** — the MCP write CLI example now shows the required
  `--engine-root` / `--policy` / `--revision-export` plus how to generate the
  Revision Export.
- **DEF-08** — the experimental switch is documented in `Config/DefaultEngine.ini`
  (the plugin reads it through `GEngineIni`), not `DefaultEditor.ini`.
- **DEF-09** — the GraphEdit example uses canonical `tmp:` tempIds (the old
  example used bare `not`/`or`, which the validator rejects) and states that
  `nodeClass` is derived, not a request field.
- **DEF-02 / 03 / 04 / 11** — deferred to stage 1/3 in the status document.

## 6. UE API used and access check

| API | Header | Use |
| --- | --- | --- |
| `FBlueprintContextExporter::BuildContextObject` | `BlueprintContextExporter.h` (public static) | canonical context without filesystem |
| `UBlueprint::UbergraphPages/FunctionGraphs/MacroGraphs/DelegateSignatureGraphs` | `Engine/Blueprint.h:539/543/551/547` | graph GUID resolution |
| `FGuid::Parse`, `UEdGraph::GraphGuid` | `Misc/Guid.h`, `EdGraph/EdGraph.h` | exact graph identity |
| `FJsonObject::TryGetArrayField/SetArrayField` | `Dom/JsonObject.h` | pagination |
| `LoadObject<UBlueprint>` | `UObject/UObjectGlobals.h` | on-demand load |
| `FBlueprintEditor::GetFocusedGraph/GetBlueprintObj/IsEditable` | `BlueprintEditor.h:281/324/426` | session target checks (existing) |
| `IAssetEditorInstance::GetEditorName`, `UAssetEditorSubsystem::FindEditorForAsset` | `AssetEditorSubsystem.h:55/153` | editor resolution (existing) |

No protected member is accessed; the R7 `GetEnabledStateAttribute` access was
removed in `4ab027a`.

## 7. Files that need N1 compile confirmation

- `BlueprintEditSession.h/.cpp` (0-2 contract, rollback)
- `EditorBridgeBlueprintEditSession.cpp` (response fields)
- `EditorBridgeNodeMoveBatch.cpp` (CommitBatch signature)
- `EditorBridgeGraphHandlers.cpp` (new live context handler)
- `EditorBridge.cpp/.h` (dispatch, capability list, declaration)

Python changes need no compile.

## 8. Suggested N1 acceptance (stage 0 §四, 10 alternating rounds)

One Editor PID, one MCP process:

1. Compile the five files above incrementally (0 Editor). Record the new DLL SHA.
2. Open the disposable project; begin an edit session on the 259-node graph while
   the package is **Dirty** (user made an unsaved edit first) — begin must accept.
3. Round loop ×10: user moves node A and saves; Agent moves node B (batch expects
   B's live position); confirm both survive, no `index-stale`, no fingerprint
   rejection.
4. Mid-session, user moves a node the Agent previously moved; then
   `end(mode=rollback)` — the user's node must be skipped and listed, the Agent's
   other nodes restored.
5. Confirm `requiresUserNotEdit=false`, `exclusiveUI=false`,
   `humanEditingBlocked=false` in every response.
6. Call `ue_get_blueprint_graph_context` on a **not open** Blueprint; confirm the
   context matches an offline export and the package stays not-Dirty.
7. Material + DataTable plan→apply→save in the same Editor session (rounds 8–10
   of the loop), confirming the live pre-gate never returns `index-stale`.
8. Record: Editor PID constant, child `UnrealEditor-Cmd` count 0, MCP restarts 0.

## 9. Test evidence

- `test_blueprint_edit_session_mcp.py` 26, `test_r7_ui_gate_prototype.py` 22,
  `test_agent_workflow.py` 85 (7 new live-gate tests),
  `test_live_graph_context.py` 8, `test_doc_examples.py` 3,
  `test_tool_registry.py` and `test_pie_pure_live_indexless.py` green after the
  count update.
- Full `py -3.12 -B -m unittest discover -s tests/python`: see §10.
- `git diff --check` clean.

## 10. Final numbers

- Full `py -3.12 -B -m unittest discover -s tests/python`:
  **2002 passed, 20 skipped** (base `1a5ad01` baseline: 1982 passed, 20 skipped;
  the 20 added tests are this round's live-gate, live-context and doc-example
  regressions).
- `git diff --check` clean; worktree clean after the commits below.
- Commits: `4e137c1` (A), `b65c156` (B), `f785804` (C), plus the F/D/E/G
  documentation commit.

Any C++ statement here is a structural claim only — **no Native PASS is
claimed**, and the five C++ files in §7 still need N1's incremental compile.

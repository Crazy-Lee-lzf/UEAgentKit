# P1 defect status (2026-10-31 slice)

Branch: `feature/p1-node-move-defect-fix-20261030`. Scope: existing P1 write /
save / index defects only; no new asset write domains, no UI-lock claim, no
Editor launch. Status is per item: **fixed**, **limited**, or **not done**, with
the evidence that exists in this worktree.

## Round 2 (2026-10-31, second long task)

### R2-1 Rollback rejected graphs with more than 128 nodes — fixed

`End(mode=rollback)` built one restore request per bound node and passed them to
`ApplyNodeMoveBatch`, which capped requests at `MaxNodeMovesPerBatch = 128`, so
a 129+ node graph could not roll back even if a single node moved. The helper
now takes an explicit `MaxRequestCount` (default 128) plus
`NodeMoveRequestLimitUnlimited` for the session restore path; the rollback still
runs in exactly one `FScopedTransaction` with full validation and read-back.
Transport batches remain capped at 128.

### R2-2 "failed" after the edit was already applied — fixed by in-transaction finalize

The handler used to call `ApplyNodeMoveBatch` (which commits) and only then
`CommitBatch`. A post-apply session failure would return an error while the
nodes stayed moved. `ApplyNodeMoveBatch` now accepts a finalizer that runs
**inside** the open transaction after the read-back and Dirty confirmation;
`CommitBatch` is passed as that finalizer. A finalizer failure restores every
position, Blueprint status/cache and Dirty flag, cancels the transaction, and
returns the session error with `writeOccurred=false`. Successful responses carry
`writeOccurred=true` and `sessionFinalized=true`.

Static contract tests pin both behaviours; the real transaction, 129/256-node
graphs, >128-distinct-node cross-batch rollback and fault injection remain
Native-unverified (cases listed in `docs/BLUEPRINT_EDIT_SESSION.md`).

## DEF-01 — ordinary Resident write/save must not cold-start; continuous editing must not need an MCP restart

Status: **fixed (Python/MCP level); Native E2E still unverified**.

What changed (round 2, reusing `feature/resident-save-no-cold-20261030`
`e88e9eb`):

- MCP `ue_save_authorized_asset` now defaults to `verification_mode=resident`:
  it saves through the connected Editor Bridge, requires exactly one pending
  journaled live apply in the same Editor session, runs
  `editor.verifyAssetPropertyLiveFast` before the save, and reads the on-disk
  SHA after it. It never calls `_export_refresh_candidate`, so routine saves no
  longer launch `RunExport.ps1` / `RunAssetCatalog.ps1` / an Unreal commandlet.
- The response states `saved=true`, `verified=false`,
  `verificationKind=resident-fast+disk-revision`,
  `childUnrealProcessCount=0` and points at `ue_verify_live_write_fast`; strong
  `verified=true` now requires the explicit `verification_mode=immediate`
  (or a checkpoint flow). Manual Dirty assets with no bound live write fail
  closed with `resident-save-receipt-required` instead of silently falling back
  to a commandlet.
- Continuous editing: `IndexFreshnessTracker.mark_commit` records a
  session-scoped revision overlay for the exact revision this MCP session saved
  (`authorized-resident-save`, with the Editor session id).
  `_assert_asset_fresh` accepts a stale frozen index only when that overlay
  revision equals the current disk revision, and the response gate is marked
  `session-owned-save-overlay`. Any external write changes the disk revision,
  the overlay stops applying, and the next action fails `index-stale`. A
  snapshot refresh calls `clear_session_overlays()`; nothing is persisted, so an
  MCP restart starts clean. This does **not** mark the index fresh
  (`indexFresh=false`) and is not a strong verification.
- The `_refresh_applied` generation gate is untouched: after an explicit
  snapshot refresh the session still requires a restart, by design.

Tests: `test_agent_workflow` resident/no-cold tests (no process runner, no
`verified=true`), a two-save continuous edit on one asset through the overlay
with an external-change negative case, plus freshness overlay regressions.
Native E2E (real Editor child-process count and successive write/save timings)
remains unverified.

## DEF-05 — Material / DataTable writers lack Fast Verify

Status: **limited / not fixed** (precise gap; the fix requires new C++ read APIs
and a native build).

Evidence:

- `TryVerifyAssetPropertyLiveFastResult`
  (`Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeWriteHandlers.cpp`)
  supports only `setAssetProperty`, `setVariableDefault`,
  `setComponentProperty`, `setPinDefault`; every other registered operation
  returns `live-fast-verify-operation-unsupported`.
- Material and DataTable operations are registered and writable
  (`LiveWriteMaterialOperations.cpp`, `LiveWriteDataTableOperations.cpp`), but
  their target resolution/read helpers are file-local; there is no stateless
  read entry point the fast-verify handler can reuse.

Not done because: adding those read entry points touches three native files and
cannot be compiled or exercised in this environment (no UE 5.6 install, no
Editor allowed). Shipping uncompiled C++ that claims Fast Verify for these ops
would be worse than the current fail-closed error.

Interim behaviour is honest: Fast Verify on a Material/DataTable operation fails
with a stable unsupported error; it is never reported as verified. Use the full
`ue_verify_live_write` path for those operations.

Next slice: expose `ReadLiveMaterialParameterValue` / `ReadLiveDataTableValue`
(next to the existing writers), branch on them in fast verify, and add native
tests for Scalar/Vector/Texture/StaticSwitch plus cell/row-field reads.

## DEF-10 — Save returns a stale Revision

Status: **fixed**.

- Both authorized-save Commit branches now return the canonical post-save
  Revision: `revision` (= `afterRevision`), `revisionSource`
  (`post-save-disk-sha256`), `revisionCapturedAtUtc`, alongside the existing
  `beforeRevision` / `afterRevision` / `revisionChanged`. Callers no longer have
  to guess between the pre-save Preview revision, the frozen index revision, and
  the post-save file hash.
- Test: `tests/python/test_agent_workflow.py` asserts `revision` equals the
  post-save hash, differs from the pre-save hash, and carries the source/timestamp.

## DEF-12 — duplicate stable_id / duplicate CLI args / index silent ignore

Status: **partially fixed** (duplicate reflection identity fixed; the other two
recorded as limited/not done).

### 12a Duplicate reflection stable_id — fixed

- `reflection_index.py` already rejected duplicate type ids and duplicate
  parameter ids, but a repeated function/property in one export fell through to
  the raw SQLite `UNIQUE(stable_id)` error (or, after a future schema change,
  could overwrite). `_insert_child_symbol` now checks a per-type seen-set and
  raises `Duplicate reflected <kind> stableId: ...`; the existing savepoint then
  rolls the whole type back and records the error in `result.errors`.
- Test: `tests/python/test_reflection_index.py`
  `test_duplicate_child_stable_id_is_reported_and_rolled_back` (no partial rows,
  `matched_types == 0`).

### 12b Duplicate CLI parameters — not done

- `argparse` defaults to last-wins for repeated `store` options
  (`src/ue_agent_kit/cli.py`); a duplicated `--database`, `--project-key` or
  similar silently takes the final value. Rejecting duplicates requires a shared
  custom `Action` applied across every subcommand, plus CLI error-message tests;
  this was not attempted in this slice to avoid a broad parser refactor.

### 12c Index silent ignore — limited

- Candidate silent paths: the fallback asset-path map skips unreadable/invalid
  JSON (`indexer.py:155`), and schema checks skip migration statements whose
  SQLite error text is "unrecognized"/"syntax" (`database.py:151`). No path was
  found that silently drops committed index rows or duplicates a stable id, so
  no code was changed; the next step is to count and report those skips instead
  of only `continue`.

## Test evidence for this slice

Python 3.12, `unittest` in `tests/python`, no Editor launched:

- `test_blueprint_edit_session_mcp` 16, `test_move_blueprint_nodes_mcp` 14,
  `test_node_move_mcp` 10, `test_tool_registry` 4, `test_mcp_server` 20,
  `test_agent_workflow` 74, `test_editor_bridge` 27, `test_reflection_index` 12
  — **177 passed**. Full `unittest discover` over `tests/python`:
  **1926 passed, 20 skipped**, no Editor or commandlet launched.

Not verified: UE C++ compilation, the real save/verify commandlet path, and any
native behaviour behind DEF-01/DEF-05.

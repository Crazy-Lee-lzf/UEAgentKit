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

Status: **fixed for retained live-write transactions; Native unverified.**

What changed (round 2):

- The fast-verify request now accepts the exact `transactionId` of the retained
  live write. `TryVerifyAssetPropertyLiveFastResult` looks that record up in
  `LiveWriteTransactionRecords`, proves it belongs to this Editor session, the
  same operation and the same loaded asset/class, then re-resolves the target
  and reads the current value through `Record->IO->ReadWrittenValueForRevert`.
  That is the same IO path `ue_undo_asset_property_live` /
  `ue_discard_asset_property_live` already trust for Material and DataTable
  writes, so no new per-asset write domain or duplicate read logic was added.
- The record read lives in `EditorBridgeRetainedLiveWrite.{h,cpp}` as a
  read-only helper (no transaction, `Modify`, save or package mutation), keeping
  `EditorBridgeWriteHandlers.cpp` within its bounded-size guard while allowing
  the new `transactionId` plumbing.
- The result reports `targetKind=materialParameter|dataTableRow|property`,
  `targetResolved=true` and the `transactionId`. Missing/foreign/remapped
  records fail closed with `live-fast-verify-transaction-not-found` /
  `-target-changed`; a stateless request without a retained record for an
  unsupported operation still returns
  `live-fast-verify-operation-unsupported` instead of pretending success.
- Python sends `transactionId` on both fast-verify paths
  (`_fast_verify_live_record`, `verify_live_write_fast`).

Limit: verification is only available while the bridge still retains that
transaction (same unsaved Editor session). After a save/reload or a new write
that replaces the record, callers must use the full `ue_verify_live_write`
path. Native compile, the actual Material/DataTable read-back and the
structural DataTable undo shapes remain unverified; no UE build was run.

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

### 12b Duplicate CLI parameters — fixed

- `build_parser()` now uses `StrictArgumentParser`, which rejects a repeated
  plain store option before argparse's last-wins behaviour can hide a mistake.
  The check is generic (every subparser inherits it through `parser_class`) and
  only applies to plain store actions; `append` (e.g. `--source-root`) and
  boolean flags keep their documented repeatable behaviour.
- Tests: `test_cli_duplicate_args.py` covers duplicate `--database` and
  `--project-key`, single values, repeated `--source-root`, and repeated
  `--compact`.

### 12c Index silent ignore — partially fixed

- The canonical fallback scan in `indexer.py` used to `continue` on an
  unreadable/invalid JSON file, which could make an asset look "not found"
  rather than "evidence was skipped". It now records each skipped file
  (`_CanonicalPathResolver.skipped_canonical_files`), appends
  `canonical-fallback-skip:*` warnings plus an explanatory warning to
  `IndexBuildResult.warnings` (new field, included in `to_dict`), and stores
  `last_index_skipped_canonical_files` metadata.
- Test: `test_index_skip_observability.py` verifies the valid file still
  resolves while the invalid one is reported.
- `database.py` still treats FTS5 `integrity-check` statements with an
  "unrecognized/syntax" error as unavailable on older SQLite; this is an
  intentional compatibility fallback (the schema version and `quick_check`
  checks still run), so it was kept and documented rather than removed.

## Test evidence for this slice

Python 3.12, `unittest` in `tests/python`, no Editor launched:

- Round-2 focused modules: `test_blueprint_edit_session_mcp` 16,
  `test_move_blueprint_nodes_mcp` 14, `test_node_move_mcp` 10,
  `test_tool_registry` 4, `test_mcp_server` 20, `test_agent_workflow` 77,
  `test_editor_bridge` 27, `test_reflection_index` 13, `test_freshness` 7,
  `test_live_fast_verify_contract` 3, `test_cli_duplicate_args` 3,
  `test_index_skip_observability` 1.
- Full `unittest discover` over `tests/python`: **1940 passed, 20 skipped**,
  no Editor or commandlet launched.

Not verified: UE C++ compilation, real transaction/rollback/finalize
behaviour, the resident-save child-process count in a real Editor, Material /
DataTable fast-verify read-back, and any native behaviour behind
DEF-01/DEF-05.

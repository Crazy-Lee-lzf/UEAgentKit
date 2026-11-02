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

## Round 3 (2026-11-01, UE5.6.1 header review + N1 alignment)

### R3-1 Native header review — done, no build run

All P1 API usages were checked against the installed
`E:\EPICGAME\UE_5.6` (5.6.1, CL 44394996) headers: `FScopedTransaction`,
`UTransactor`, `UEditorEngine::Trans/CanTransact/IsPlaySessionInProgress`,
`FBlueprintEditor::GetFocusedGraph/IsEditable/GetBlueprintObj`,
`UAssetEditorSubsystem::FindEditorForAsset`, `UPackage::SetDirtyFlag`,
`UEdGraph` fields, `FBlueprintEditorUtils::MarkBlueprintAsModified`,
`UBlueprint` arrays/status, `FGuid::Parse`, `FDateTime::ToIso8601`,
`TFunction`, `FJsonObject`, `FAutoConsoleCommand`. Full matrix:
[P1_NATIVE_READINESS_REVIEW.md](P1_NATIVE_READINESS_REVIEW.md).

One compile-blocking mismatch was found and fixed: the retained fast-verify
helper took `TSharedPtr<FJsonObject>&` while the handler owns
`TSharedRef<FJsonObject>` (a `TSharedRef` cannot bind to that reference). It is
now `const TSharedRef<FJsonObject>&` with a static contract test. This is still
**not** a successful build.

Residual (documented, not claimed atomic): the manager advances its expected
state after the finalizer but before the transaction's destructor commits; UE
offers no commit-failure signal there. If that ever happened, the next
`ValidateBatch` would fail closed on the live fingerprint/positions. No code can
currently prove otherwise without Native instrumentation.

### R3-2 Test-only finalizer fault seam for N1-R4.1 — added

`BlueprintEditSession` now exposes, only under
`#if WITH_DEV_AUTOMATION_TESTS` (UBT sets it 0 for Shipping), a one-shot seam:
the console command `UEAgentKit.BlueprintEditSession.FailNextFinalize` arms a
failure of the next `CommitBatch` with
`blueprint-edit-session-finalize-fault-injected`, before any manager mutation.
There is no MCP/socket parameter, environment variable or bridge method for it;
contract tests assert it is absent from `mcp_workflow_tools.py` and
`EditorBridge.cpp`, and that the fault check precedes the manager mutation.
N1 must add its `test-seam:finalizer-fault` premise and audit this commit before
R4.1 can become ready; N1 was not modified.

### R3-3 N1 fast-verify error contract — fixed

`_fast_verify_live_record` and `verify_live_write_fast` used to collapse every
bridge refusal into `live-fast-verify-failed`. They now preserve a
`live-fast-verify-*` bridge code (for example
`live-fast-verify-transaction-not-found` after a save/reload), which is what
N1-R6.2 expects. Other failures still report `live-fast-verify-failed`.

`_MAX_NODE_MOVE_BATCH = 128` and `node-move-batch-limit-exceeded` already match
N1-R3.0/R3.4 and were not changed.

N1 pending sync items (N1 branch untouched): add the `test-seam:finalizer-fault`
premise, audit and whitelist the new P1 commits in `p1AcceptedRevisions`, and
re-run N1 `selfcheck`/`preflight`; R4.1 stays blocked until a Native run.

## Round 4 (2026-11-02, N1 native blockers)

Two blockers were found by N1 running the P1 plugin in a real UE 5.6.1 Editor
(Editor PID 19432, plugin built from P1 `c53b959`). Both were reproduced
natively, fixed here, and are now covered by Python static/mock regression. Full
record: [P1_N1_NATIVE_BLOCKERS_FIX_20261102.md](P1_N1_NATIVE_BLOCKERS_FIX_20261102.md).

### R4-1 / N1-DEF-01 Edit-session token was uppercase — fixed

`Begin` generated the 64-hex token with `EGuidFormats::Digits` (`%08X`,
uppercase) while `IsCanonicalEditSessionToken`, the case-sensitive `End()`
comparison and the Python `[0-9a-f]{64}` wrapper all require exact lowercase.
The native effect was `node-move-batch-invalid-parameters` for the token that
`begin` itself had just returned; a caller could begin and end a session but
never apply a batch. Fixed in `16f9319` by generating both GUID halves with
`EGuidFormats::DigitsLower` and asserting the result is canonical. No validator
was relaxed: uppercase, truncated, non-hex and foreign tokens still fail closed.
Randomness, session/process/asset/graph binding and the Dirty gate are unchanged.

### R4-2 / N1-DEF-02 Retained fast verify success reported unsupported — fixed

The handler tested the retained read inside a negated compound condition, so a
**successful** read made the `else if` false and control reached the final
`else` that returns `live-fast-verify-operation-unsupported`. Material and
DataTable fast verify could therefore never report `verified=true`. Fixed in
`7cd0820` by splitting the branch: a non-empty `transactionId` enters the
retained path, a failed read returns the helper's own precise code, and a
successful read falls through to the shared value comparison. The
no-transaction case still returns `operation-unsupported`, the four stateless
operations are unchanged, and the helper keeps its
`const TSharedRef<FJsonObject>&` signature. Fail-closed codes
(`-transaction-not-found`, `-target-changed`, `-target-not-found`,
`-read-failed`) are unchanged.

Both fixes are **Python-verified only**; no UE build or Editor was started in
this round, so the native re-verification (N1 R2/R3/R4/R6) is still outstanding.

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

- Round-4 focused modules: `test_blueprint_edit_session_mcp` 23 (16 + 3 token
  source contracts + 3 round-trip/negative cases), `test_live_fast_verify_contract`
  7 (3 + 3 control-flow/fail-closed + 1 preserved), `test_tool_registry` 4
  (bounded-handler guard, file 24 637 < 24 700) — all pass.
- Round-2 focused modules: `test_blueprint_edit_session_mcp` 16,
  `test_move_blueprint_nodes_mcp` 14, `test_node_move_mcp` 10,
  `test_tool_registry` 4, `test_mcp_server` 20, `test_agent_workflow` 77,
  `test_editor_bridge` 27, `test_reflection_index` 13, `test_freshness` 7,
  `test_live_fast_verify_contract` 3, `test_cli_duplicate_args` 3,
  `test_index_skip_observability` 1.
- Full `unittest discover` over `tests/python`: **1951 passed, 20 skipped**
  (round-4 baseline 1943 passed, 20 skipped; the 8 added tests are round 4),
  no Editor or commandlet launched.

Not verified: UE C++ compilation, real transaction/rollback/finalize
behaviour, the resident-save child-process count in a real Editor, Material /
DataTable fast-verify read-back, and any native behaviour behind
DEF-01/DEF-05. Round 4 adds the same limit for R4-1/R4-2: the token round trip
and the retained `verified=true` path are proven only by Python contract/mock
tests, and the native re-run remains N1's R2/R3/R4/R6 gate.

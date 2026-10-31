# Resident Save: no cold Unreal process for routine live edits

Status: feature branch prototype; **not** proof of full native UE5 Editor acceptance.

## Why

The old MCP `ue_save_authorized_asset` default was `verification_mode=immediate`.
After saving through the connected Editor it calls
`_export_refresh_candidate()` / `RunExport.ps1` /
`RunAssetCatalog.ps1` which starts another Unreal process for strong reload.
`ue_verify_live_write` does the same thing when called separately.
This is too slow for interactive one-asset edits.

## Behavior

- MCP `ue_save_authorized_asset` now defaults to `verification_mode=resident`.
- This mode requires **exactly one pending, journaled Live Apply** in the same
  Editor session, a loaded Dirty asset, valid Policy/Revision and a fresh
  one-time authorization receipt. If there is no qualifying receipt it fails
  closed (it does not silently fall back to a cold commandlet).
- Preview and Commit both use `editor.verifyAssetPropertyLiveFast` before
  saving to confirm the exact expected in-memory value. The Editor saves
  through `editor.saveAuthorizedAsset`; after saving the host reads the
  package's disk SHA and records a backup and lifecycle journal.
- Resident Save **does not** perform an independent Unreal reload. The
  return explicitly contains `saved=true`, `verified=false`,
  `verificationKind=resident-fast+disk-revision`,
  `childUnrealProcessCount=0` and suggests `ue_verify_live_write_fast`.
- It retains existing `verification_mode=immediate` for opt-in independent
  export and `verification_mode=checkpoint` for multiple edits grouped by
  Change Set. The latter batches many operations under one authorized Save,
  with one separate Strong Verify at the designated checkpoint.
- Direct Python workflow callers retain the legacy `immediate` default for
  source compatibility; **MCP defaults to `resident`**. Always pass the same
  verification mode in Preview and Commit.
- Manual Dirty assets with no bound Live Apply need `immediate` (explicit
  cold verification); Change Sets should use `checkpoint`.

## Not yet solved

1. ~~Re-editing the same asset after its first Save still requires a new
   trusted Revision baseline / MCP session restart.~~ Solved for the same MCP
   session by a **session revision overlay**: `IndexFreshnessTracker.mark_commit`
   records the exact revision this session saved (with the editor session id),
   and `_assert_asset_fresh` accepts a stale frozen index only when the overlay
   revision equals the current disk revision (`writeGate=session-owned-save-overlay`).
   Any external write changes the disk revision, so the overlay stops applying
   and the next action fails `index-stale`. A snapshot refresh clears all
   overlays, and an MCP restart starts with none. This is a write gate, not an
   index refresh: the response still reports `indexFresh=false`, and a new
   *trusted* baseline still requires an explicit refresh at a milestone.
2. Native plugins requiring a recompiled DLL may still need hot reload,
   Live Coding, or Editor restart when **changing UEAgentKit C++ itself**.
   Routine asset writes should not require that deployment.
3. No strong persisted value assertion is implied by a resident-only
   result. Strong reloading remains an explicit higher-assurance action,
   not a default per-edit cost.
4. This branch has Python unit tests; it still needs one real UE5.6 Editor
   E2E fixture measuring child-process count and elapsed time across multiple
   successive writes, save, and reload.

## Tests

- New regression: one bound Live Apply, Preview and Commit in `resident`
  mode never touch `process_runner`, still backup and read disk SHA, and
  do not set `verified=true`.
- New negative regression: no bound Live Apply fails closed.
- New regression: a second live write + resident save on the same asset in the
  same MCP session succeeds through the session revision overlay without an
  index refresh, and an external revision change makes the next save fail
  `index-stale`.
- New freshness regressions: overlay applies only to the exact saved revision,
  is dropped when the disk changes, is not created by an offline commit, and is
  cleared by `clear_session_overlays()`.
- Legacy `immediate` / `checkpoint` tests must remain unchanged.

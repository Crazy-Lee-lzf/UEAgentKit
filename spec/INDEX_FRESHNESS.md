# Index Freshness and Snapshot Lifecycle

UE Agent Kit 0.5.1 treats the SQLite index and Revision Export as fixed read snapshots. A saved Unreal package can change after those snapshots were built, so the MCP server must never assume that an available index is automatically current.

## Revision sources

Fixed-project mode compares three independent values for every indexed project package:

```text
SQLite revision_value
Revision Export canonical revision.value
Current package SHA-256 on disk
```

A Revision is usable only when it uses the canonical form:

```text
sha256:<64 hexadecimal characters>
```

The comparison does not modify the SQLite database, Revision Export, project package, or Editor memory.

## States

### `fresh`

All three Revision values are available and equal.

```text
SQLite == Revision Export == disk package
```

A new Patch Plan may be created only for a target asset in this state.

### `stale`

All required Revision values are available, but at least one differs. The response identifies the mismatched pairs:

```text
indexMatchesRevisionExport
indexMatchesDisk
revisionExportMatchesDisk
```

Typical causes include an external package edit or a successful MCP Commit that has not yet been re-exported and re-indexed.

### `partial`

Project-level status uses `partial` when at least one asset is fresh but another cannot be compared across all three sources.

### `unavailable`

A complete comparison cannot be performed. Examples:

- Asset is absent from the immutable index.
- Canonical Revision Export file is missing or invalid.
- Package file is missing or unreadable.
- Revision is absent or not SHA-256 based.
- Package was exported or indexed while Dirty.
- Asset uses a mount point that the current project-package resolver does not support.

### `unknown`

Read-only MCP mode has a fixed SQLite database but no fixed Project and Revision Export paths. It therefore reports `unknown` instead of claiming that the index is fresh.

## Package resolution boundary

The current fixed-project implementation compares `/Game/...` packages below the fixed project `Content` directory. It checks the expected `.uasset` or `.umap` package without exposing the local filename in MCP responses.

Plugin mount points and Engine content are not guessed. They remain `unavailable` until an explicit mount-to-package resolver is added.

## Project status

`ue_get_project_status` returns a `freshness` object containing:

```text
state
indexFresh
indexStale
comparisonMode
comparedAssetCount
freshAssetCount
staleAssetCount
unavailableAssetCount
complete
staleAssets
unavailableAssets
sessionStaleAssets
comparedAtUtc
```

Mismatch samples are bounded and contain Unreal Object Paths and Revision values only. Local project, package, export, and database paths are not returned.

File SHA-256 values are cached by file size and nanosecond modification time for the lifetime of the MCP session. Commit and rollback operations invalidate the package cache before the next comparison.

## Write lifecycle

### Plan

`ue_plan_patch` first requires the target asset to be present in SQLite, then requires three-source state `fresh`.

- `index-stale`: all sources were available but did not match.
- `index-freshness-unavailable`: a safe three-source comparison could not be completed.

A stale asset cannot receive a new Plan, even if its previous Plan or Receipt is still present.

### Dry Run

Dry Run must leave the disk Revision unchanged. It does not change freshness state.

### Commit

A successful Commit records the transition:

```text
beforeRevision -> afterRevision
```

The disk package now differs from the fixed SQLite and Revision Export snapshots. The workflow therefore immediately marks:

```text
fixedSnapshotsStale = true
sqliteIndexStale = true
revisionExportStale = true
```

The Commit response includes `indexFreshness.state=stale`.

### Independent Verify

`ue_verify_asset` confirms that a new Unreal process sees the committed Revision. Verification does not update the fixed snapshots and must not clear stale state.

### Rollback

Rollback Commit clears the session stale marker only when independent verification restores the exact pre-Commit Revision and a new three-source comparison returns `fresh`.

If the package is restored to another Revision, or either snapshot changed independently, stale state remains visible.

## Single-asset refresh design

Single-asset refresh is intentionally not implemented as an in-place mutation of a running immutable server. The safe design is:

1. Stop accepting new Plans and wait for active Tool calls to finish.
2. Export exactly one authorized asset into a staging directory below the fixed Work Root.
3. Require the staging Manifest to match the fixed Project, contain exactly the requested asset, report zero failures, and provide a usable SHA-256 Revision.
4. Confirm that the staged Canonical Revision equals the current disk package SHA-256.
5. Build a next Revision Export snapshot in staging. Do not overwrite the active Canonical file or Manifest in place.
6. Build a next SQLite file from the next Revision Export snapshot. The active immutable database remains untouched.
7. Validate Project Key, Schema version, asset count, target Revision, FTS availability, and absence of `-wal`, `-shm`, or `-journal` sidecars.
8. End the current MCP session. All continuation tokens, Plans, and Receipts become invalid.
9. Atomically replace the active snapshot pair, or switch a small fixed snapshot pointer, only after both staged snapshots pass validation.
10. Start a new MCP session and require `ue_get_project_status.freshness.state=fresh` for the target asset.
11. If any step fails, delete staging output and keep the previous snapshot pair active.

The Revision Export and SQLite database form one logical snapshot generation. They must never be switched independently.

A future `ue_refresh_asset_index` Tool should expose only an authorized Unreal Asset Path. It must not accept arbitrary output paths, database paths, shell commands, Commandlet parameters, or filesystem operations.

## Reload boundary

A running server opens SQLite using `mode=ro&immutable=1`. Safe reload therefore means a new server session, not reconnecting the existing immutable connection to a modified file.

A reload invalidates:

```text
continuation tokens
Patch Plans
Dry Run Receipts
Apply Receipts
Rollback Receipts
cached package and Canonical hashes
```

This is deliberate: execution context created against an old snapshot must not survive into a new snapshot generation.

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

## Implemented single-asset refresh lifecycle

`ue_refresh_asset_index` never mutates the SQLite file opened by the running server. A fixed workflow session resolves one active Pair at startup and freezes that selection:

- A legacy configured SQLite + Revision Export Pair is copied into a private session snapshot, so later external rebuilds cannot change the running session.
- An internal immutable Generation is pinned directly without duplicating the entire tree.
- Revision Export and SQLite always move together as one Generation.

The Tool exposes only an exact Policy-authorized Unreal Asset Path and `mode=Preview|Apply`. It cannot accept output paths, database paths, shell commands, Commandlet parameters, or filesystem operations.

### Preview

1. Reject an invalid or unauthorized asset.
2. Reject a Dirty target reported by the fixed Live Editor Bridge. If a Bridge Descriptor exists but Dirty state cannot be read reliably, fail closed.
3. Export exactly one asset below the fixed Work Root.
4. Require fixed-project identity, zero failures, exactly one Canonical record, and a clean SHA-256 Revision.
5. Independently hash the current disk Package and require exact equality.
6. Report add/update action, target Revision, current Generation, and workflow records that Apply would invalidate.
7. Delete staging output without changing the active Pointer.

### Apply

Apply repeats all Preview validation, then:

1. Preflight free disk space.
2. Build a next Revision Export tree in staging. The first internal Generation copies external legacy files; later internal generations may hard-link unchanged immutable files.
3. Copy the active database and update exactly the requested asset from the staged export.
4. Validate `PRAGMA integrity_check`, current Schema, FTS5, Project Key, target Revision, clean Package state, and absence of SQLite Sidecars.
5. Rename the completed staging tree to `snapshots/<generationId>`.
6. Atomically replace `<WorkRoot>/active-snapshot.json` with the paired Generation identity and database/manifest hashes.
7. Invalidate all session Plans and Receipts and mark the current workflow session restart-required.

If any ordinary validation, export, database build, or pointer-write step fails, the previous Pointer remains active. The configured source SQLite and Revision Export are never overwritten.

Published historical generations are retained because an older MCP process may still have one pinned. Runtime refresh never guesses that an old generation is unused and never deletes it automatically. Generation cleanup is an explicit maintenance action that requires all MCP processes to be stopped and must preserve the generation named by `active-snapshot.json`.

## Reload boundary

A running server opens SQLite using `mode=ro&immutable=1`. The session that performs Apply continues serving reads from its previously frozen snapshot and rejects new workflow actions with `snapshot-refresh-restart-required`. A new MCP process resolves the new Pointer and must report `ue_get_project_status.freshness.state=fresh` before new writes.

A refresh invalidates:

```text
continuation tokens
Patch Plans
Dry Run Receipts
Apply Receipts
Rollback Receipts
cached package and Canonical hashes
```

This is deliberate: execution context created against an old snapshot must not survive into a new snapshot generation.

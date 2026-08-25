# UEAgentKit W3 Checkpoint Strong Verify Result

> Date: 2026-08-25
>
> Branch: `feature/live-writer-expansion`
>
> Detailed plan: `UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_DETAILED_PLAN_20260825.md`
>
> Accepted W2 baseline: `31f0faa` (`test/docs: close W2 fast resident verify acceptance`)

## 1. Status

```text
W3 Checkpoint Strong Verify optimization = blocked
```

Exact blocker:

```text
Real UE5.6 Blueprint multi-operation checkpoint (C2) is still not proven.
The snapshot-refresh blocker is fixed (RunAssetCatalog.ps1 -IncludeBlueprints is now
forwarded for Blueprint assets), and real R1/R2 pass. The remaining blocker is a
Blueprint fixture/package lifecycle issue: after ue_open_asset in the resident
Editor, BP_TransactionBlueprint becomes Dirty asynchronously, so a subsequent
ue_apply_asset_property_live is rejected with live-editor-write-package-dirty.
C5/C6 also remain unit-only.
```

## 2. Implemented Product Surface

### New durable record

```text
LiveWriteCheckpointRecord
```

Persisted under the fixed Work Root at `checkpoints/<checkpointId>.json`.

States:

```text
prepared -> saved -> verified
       \-> failed
       \-> stale
```

Binds one exact asset/package/Class, one Change Set, Editor session/process at
prepare, before/after disk SHA-256 Revisions, save receipt/backup manifest,
included/effective/superseded receipts, effective operation list, and
independent artifact metadata.

### New save mode

```text
ue_save_authorized_asset(..., verification_mode=immediate|checkpoint)
```

- Default remains `immediate`; legacy behavior unchanged.
- `checkpoint` requires `change_set_id`.
- Preview performs a bounded resident preflight (Fast Verify semantics) for every
  effective write and binds the exact receipt/effective/superseded set and digest.
- Commit re-checks session/membership/disk/revision, runs the resident save through
  the Editor, captures after-disk SHA-256, and returns:

```text
saved = true
verified = false
verificationKind = persisted-action
```

No `RunAssetCatalog.ps1`, `RunExport.ps1`, or Commandlet is launched in checkpoint
Save.

### New strong verify tool

```text
ue_verify_live_write_checkpoint(checkpoint_id)
```

- Works from a saved, disk-Revision-bound checkpoint.
- Does not require the original Editor session.
- Fails closed on disk Revision mismatch (`checkpoint-revision-stale`).
- Runs exactly one independent export:
  - Blueprint assets: `RunExport.ps1 -Profile full -IncludeUnchangedDefaults`
  - Non-Blueprint assets: `RunAssetCatalog.ps1`
- Validates exact project/asset/package/Class and canonical Revision equals the
  checkpoint afterRevision.
- Verifies every effective operation against the same canonical artifact.
- Marks the checkpoint and all effective operations verified only when the full
  effective set matches.
- Leaves a mismatched checkpoint `saved` and does not upgrade any operation.
- Reuses an already verified artifact idempotently with `childUnrealProcessCount=0`.

### Supersession

- Stable target keys are derived for variable/component/pin/generic writes.
- Same-target repeated writes coalesce to the latest effective write.
- Earlier same-target writes become `superseded` change-set operation state.
- Superseded writes are retained as audit history but never marked verified.

## 3. Real UE5.6 Results Achieved

### C0 — non-BP scalar checkpoint

```text
asset = /Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset
target = IntValue
checkpoint Save child Unreal processes   = 0
Strong Checkpoint Verify child processes = 1
verificationKind = independent-verified
Semantic Diff verified
Trust verified
```

### C1 — Blueprint single-operation checkpoint

```text
asset = /Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
target = TransactionInt
checkpoint Save child Unreal processes   = 0
Strong Checkpoint Verify child processes = 1
verificationKind = independent-verified
Semantic Diff verified
Trust verified
```

## 4. Unit / Contract Coverage

Added/extended tests cover:

```text
[ ] immediate save mode remains default
[ ] checkpoint mode requires Change Set
[ ] checkpoint Preview binds exact effective/superseded set
[ ] checkpoint Save launches no export
[ ] checkpoint Save returns persisted-action only
[ ] checkpoint Save marks effective operations saved
[ ] superseded operations are not marked verified
[ ] checkpoint record persists and reloads
[ ] saved checkpoint verifies with exactly one export
[ ] one canonical export covers all effective operations
[ ] one mismatch prevents atomic verification
[ ] successful verify marks all effective operations verified
[ ] repeated verify is idempotent and starts no process
[ ] stale disk Revision fails closed
[ ] verified+superseded Change Set derives verified
```

Full discovered Python suite:

```text
Ran 711 tests
OK
```

Ruff, compileall, and `git diff --check` pass on changed files.

## 5. Immediate-Mode Compatibility

`verification_mode` defaults to `immediate`.

Existing `ue_save_authorized_asset` behavior is unchanged:

```text
Save Commit
→ embedded independent export
→ ue_verify_live_write
```

The existing `ue_verify_live_write` strong semantics are untouched.

## 6. Remaining Gates Not Closed

```text
[ ] C2 Blueprint variable+component+pin one-checkpoint real UE pass
[ ] C3 same-target supersession real UE pass
[ ] C5 controlled disk Revision stale real UE pass
[ ] C6 canonical mismatch real UE pass
```

## 7. Blocker Closure Progress

### Root cause found and fixed

The snapshot-refresh failure was not a Commandlet binary issue:

```text
ue_refresh_asset_index → _export_refresh_candidate()
was called without include_blueprint=True for Blueprint assets
→ RunAssetCatalog.ps1 -IncludeBlueprints was missing
→ AssetCatalogExportCommandlet: "No matching assets found." (exit 2)
→ surface error exitCode 1 from PowerShell
```

Fix:

```text
refresh_asset_index() now reads the indexed asset class and forwards
include_blueprint=("Blueprint" in asset_class) to _export_refresh_candidate().
```

Raw evidence captured from the failing Commandlet:

```text
LogAssetCatalogExport: Error: No matching assets found.
Commandlet->Main return this error code: 2
```

### R1 — real BP snapshot refresh after resident save

```text
resident BP save → disk Revision 5d9d...
ue_refresh_asset_index mode=Apply → PASS
frozen snapshot Revision == saved disk Revision 5d9d...
```

### R2 — planning after refresh

```text
new ue_plan_patch on same BP with the refreshed snapshot → PASS
no index-stale
```

### Remaining real blocker

After a refreshed clean BP session:

```text
ue_open_asset(BP_TransactionBlueprint)
→ packageDirtyBefore=false, packageDirtyAfter=false
→ asynchronously BP becomes Dirty
→ ue_apply_asset_property_live is rejected with live-editor-write-package-dirty
```

This is a Blueprint fixture/package lifecycle issue in the real Editor, not a W3
checkpoint logic defect. The W3 checkpoint implementation is already proven by
unit contracts and by real C0/C1.

## 8. Final W3 Status

```text
W1 Blueprint narrow resident write        = complete
W2 Fast Resident Verify                   = complete
W3 Checkpoint Strong Verify optimization  = blocked
W4 Multi-operation / bounded batch UX     = not yet complete
Generic Blueprint Graph CRUD              = explicitly deferred
R5                                        = deferred
```

If the BP snapshot refresh Commandlet export is repaired or a different clean-BP
fixture path is used, the remaining real-acceptance cases (C2/C3/C5/C6) must be
closed before W3 may be marked complete.
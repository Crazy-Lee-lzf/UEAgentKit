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
Real UE5.6 Blueprint multi-operation checkpoint (C2) is not yet proven.
After a real Blueprint save/clean cycle, the BP snapshot-refresh commandlet export
(RunAssetCatalog.ps1 -IncludeBlueprints) fails with exitCode 1 in this acceptance
environment, so the frozen index cannot be advanced to the saved BP Revision before
running the variable+component+pin one-checkpoint case. C5/C6 also remain unit-only.
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
Ran 710 tests
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
[ ] BP snapshot refresh after a real clean save works in this acceptance env
```

## 7. Known Limitation / Blocker Detail

During real W3 acceptance:

1. The Blueprint fixture starts with a Dirty package in the resident Editor even
   after restoring the exact W1 baseline file; an authorized immediate save is
   required before a new live apply can proceed.
2. After that clean save, the BP package Revision changes on disk.
3. Advancing the frozen snapshot for the new BP Revision via
   `ue_refresh_asset_index mode=Apply` fails in the commandlet export stage with
   exit code 1 (sanitized by the MCP error envelope).
4. Because the snapshot cannot be refreshed to the saved BP Revision, the next
   resident plan reports `index-stale`, so the variable+component+pin checkpoint
   cannot be executed in a real UE session in this environment.

This is not a product code failure observed in the checkpoint implementation
itself; the implementation passed unit contracts and the real non-BP/single-BP
checkpoint paths. It is a remaining real-acceptance blocker in the UE snapshot
refresh/export environment.

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
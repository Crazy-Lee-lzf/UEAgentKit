# UEAgentKit W3 Checkpoint Strong Verify Result

> Initial date: 2026-08-25
>
> Final closure: 2026-08-26
>
> Branch: `feature/live-writer-expansion`
>
> Detailed plan: `UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_DETAILED_PLAN_20260825.md`
>
> Accepted W2 baseline: `31f0faa` (`test/docs: close W2 fast resident verify acceptance`)

## 1. Final Status

```text
W3 Checkpoint Strong Verify optimization = complete
```

All required real UE5.6 acceptance cases are closed:

```text
C0 non-BP checkpoint                         PASS
C1 Blueprint single-op checkpoint            PASS
C2 Blueprint variable+component+pin          PASS
C3 same-target supersession                  PASS
C4 saved checkpoint is session-independent   PASS
C5 disk Revision stale fail-closed            PASS
C6 canonical value mismatch fail-closed       PASS
```

W3 may now stop at the W4 boundary. No W4 implementation is included here.

## 2. Product Surface

### Durable checkpoint

`LiveWriteCheckpointRecord` is persisted under the fixed Work Root at:

```text
checkpoints/<checkpointId>.json
```

State flow:

```text
prepared -> saved -> verified
       \-> failed
       \-> stale
```

The record binds the exact asset/package/Class, Change Set, prepare-time Editor
session/process, before/after disk SHA-256 Revisions, save receipt, included /
effective / superseded receipts, effective operation digest, and independent
verification artifact metadata.

### Checkpoint save mode

```text
ue_save_authorized_asset(..., verification_mode=immediate|checkpoint)
```

- `immediate` remains the default and preserves legacy behavior.
- `checkpoint` requires `change_set_id`.
- Preview performs resident preflight and freezes the exact receipt/effective /
  superseded set.
- Commit saves only through the resident Editor and captures the after-disk
  Package SHA-256.
- Checkpoint Save starts zero child Unreal verification processes and returns
  `verificationKind=persisted-action`.

### Strong checkpoint verify

```text
ue_verify_live_write_checkpoint(checkpoint_id)
```

- Verifies a persisted saved checkpoint without requiring the original Editor session.
- Rejects current disk Revision mismatch with `checkpoint-revision-stale`.
- Runs exactly one independent export for a new verification artifact.
- Blueprint verification uses `RunExport.ps1 -Profile full -IncludeUnchangedDefaults`.
- Non-Blueprint verification uses `RunAssetCatalog.ps1`.
- All effective operations must match the same independent artifact before any are
  upgraded to verified.
- A value mismatch leaves the checkpoint `saved` and leaves operations unverified.
- Repeated verification of an already verified checkpoint is idempotent.

### Supersession

Same-target repeated live writes retain their audit history but only the latest value
is effective. Earlier writes are marked `superseded` and are never falsely marked
persisted/verified.

## 3. Actual Root Causes Closed

### 3.1 Multi-operation live-write continuation

The earlier diagnosis that a clean Blueprint package became Dirty asynchronously was
not reproducible on a regenerated fixture.

The real sequence was:

```text
first authorized live write succeeds
-> package correctly becomes Dirty
-> second write in the same Change Set reaches the generic dirty-package guard
-> second write is rejected as live-editor-write-package-dirty
```

A successful live write is expected to dirty the package. The safety boundary therefore
must distinguish an exact authorized continuation from unrelated/user dirtiness.

The C++ worktree already contained exact transaction-chain continuation support when
this closure task began. It validates the previous transaction identity/top transaction
and keeps unknown dirtiness fail-closed. This closure did not overwrite or redesign
those pre-existing C++ changes.

The missing Python orchestration was added so the next operation in the same explicit
Change Set and same asset sends the immediately preceding applied transaction as:

```text
previousTransactionId=<exact prior transaction>
```

A different Change Set does not receive that continuation token.

Real UE5.6 proof after rebuilding the existing C++ worktree:

```text
write 1: packageDirtyBefore=false -> success
write 2: packageDirtyBefore=true  -> success
continuedLiveWriteChain=true
previousTransactionId=<exact transaction from write 1>
```

### 3.2 Blueprint snapshot baseline profile

A second integration problem was found during Semantic Diff / Trust closure.

Blueprint snapshot refresh had been fixed to include Blueprint assets, but it still used
`RunAssetCatalog.ps1`, producing an `asset-index` Canonical. The frozen before snapshot
therefore contained no full Blueprint variables/components/graphs, while Strong Verify
produced a `full` Canonical. Semantic Diff then compared asymmetric evidence and reported
false missing/unexpected changes.

Final fix:

```text
Blueprint refresh
-> RunExport.ps1
-> -Profile full
-> -Format json
-> -IncludeUnchangedDefaults

Non-Blueprint refresh
-> existing RunAssetCatalog.ps1 path unchanged
```

The Index already ranks `full` as the strongest supported profile, so this is compatible
with the existing snapshot/index model and aligns before/after evidence quality.

Real refreshed Blueprint baseline confirmed:

```text
profile    = full
variables  = 2
components = 1
graphs     = 2
```

## 4. Real UE5.6 Acceptance

### C0 / C1

Existing acceptance remained valid:

```text
checkpoint Save child Unreal processes   = 0
Strong Checkpoint Verify child processes = 1
verificationKind                         = independent-verified
Semantic Diff                            = verified
Trust                                    = verified
```

### C2 — Blueprint variable + component + pin

Asset:

```text
/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
```

One Change Set contained:

```text
setVariableDefault   TransactionInt = 42
setComponentProperty DefaultSceneRoot.RelativeLocation.X = 10
setPinDefault        stable graph/node/pin A = 7
```

Result:

```text
checkpointId            = cp_j99ZTTkBt8gyMBVEoK_fTQ
effectiveOperationCount = 3
verifiedOperationCount  = 3
Strong Verify Unreal    = 1
Semantic Diff           = verified
Trust                   = verified
```

### C3 — same-target supersession

Sequence:

```text
TransactionInt = 10
TransactionInt = 20
TransactionInt = 42
```

Result:

```text
checkpointId              = cp_9_AIiTZsi4tJvxE2bIzzFA
effectiveOperationCount   = 1
supersededOperationCount  = 2
Strong Verify Unreal      = 1
Trust                     = verified
```

Only the final value is effective; the first two writes remain audit history.

### C4 — durable/session-independent verify

Saved checkpoint records were reloaded and verified after the resident Editor was
stopped. C5/C6 also exercised verification from persisted checkpoint state without the
original resident Editor session.

### C5 — controlled disk Revision stale

Saved checkpoint:

```text
checkpointId     = cp_9030dQcWbbxriwn-Rn8zwQ
expectedRevision = sha256:6e26c6c8503fd16784f6c3372033331098addfd32cc8b2528e4d8ad906a9d826
```

After a controlled one-byte disk mutation:

```text
actualRevision   = sha256:1acbd041182439899fcc22ab0cd1d05ae90643af4ceaf78f42138163c5ec8164
result           = checkpoint-revision-stale
```

The package was then restored byte-for-byte to the saved Revision.

### C6 — canonical value mismatch

Saved checkpoint:

```text
checkpointId = cp_pxo1nU-H5VA-vslLk7mQDQ
Revision     = sha256:bd23ea9135261be924796e91d77f1ac44d5444e46b617f5f363e69a10192f699
```

A deterministic test seam ran the real independent `RunExport.ps1` first, preserved the
real Package Revision, then changed only one target value in the verification artifact.

Result:

```text
real independent Unreal processes = 1
result                             = checkpoint-value-mismatch
checkpoint state                   = saved
strongVerificationKind             = empty
operation status                   = saved
operation verified                 = false
```

No false verification upgrade occurred.

## 5. Regression / Build Gates

Current final regression state:

```text
Python discovered suite   712 / 712 PASS
Ruff                      PASS (scripts/python.cmd -m ruff check src tests/python)
compileall                PASS
git diff --check           PASS
focused bounded guard      PASS
```

UE5.6 Direct Build passed again on the current worktree after final W3 closure; the compiled plugin is up to date.

The bounded execution-surface test was adjusted from 24,000 to 24,500 characters because
the existing exact transaction-chain fail-closed checks brought
`EditorBridgeWriteHandlers.cpp` to 24,110 characters. The guard remains tight; no C++
logic was removed merely to satisfy a historical size budget.

## 6. Final Fixture / Snapshot State

After C5/C6 the transaction fixture was reset again and independently verified.

Final clean Blueprint snapshot:

```text
generationId = gen_20260826T150757Z_45b96b06bbb2
Revision     = sha256:d9c9a0a26adf27fed6fe6d147e8eaa80f38e51807790a0033cabb46481fc8691
profile      = full
```

No resident Unreal Editor was intentionally left running after acceptance.

## 7. Worktree / Scope Boundary

W3 closure is checkpointed in local Git with separate product and test commits:

```text
3280102  fix: close W3 live-write continuation and snapshot refresh
ab731f1  test: cover W3 continuation and full snapshot refresh
```

The Editor Bridge continuation changes were already present when the final W3 closure
work began; they were preserved, verified with the Python orchestration, and included in
the W3 product checkpoint above. No push, rebase, release, or W4 implementation was
performed as part of this closure.

Final stage boundary:

```text
W1 Blueprint narrow resident write        = complete
W2 Fast Resident Verify                   = complete
W3 Checkpoint Strong Verify optimization  = complete
W4 Multi-operation / bounded batch UX     = next large change
Generic Blueprint Graph CRUD              = explicitly deferred
R5                                        = deferred
```

# UEAgentKit W4-4 Multi-Asset Checkpoint Save Detailed Plan

> Date: 2026-08-28
>
> Branch: `feature/live-writer-expansion`
>
> Entry implementation checkpoint: `76f90b3` (`feat: add W4-3 multi-asset resident apply`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> Previous result: `UEAGENTKIT_W4_3_MULTI_ASSET_RESIDENT_APPLY_RESULT_20260828.md`
>
> Scope: add bounded multi-asset checkpoint/save orchestration above the existing W3 one-asset checkpoint primitive. Do not add aggregate Strong Verify, Semantic Diff / Trust, disk rollback, or general restart recovery.

## 1. Goal

W4-4 takes a fully applied W4 Batch Execution and persists all touched packages through the existing W3 authorized checkpoint-save path.

Target flow:

```text
W4 Batch Execution state=applied
→ Preview all touched assets
→ complete all-assets preflight
→ persist ChangeSetCheckpointSetRecord
→ explicit aggregate Save confirmation
→ revalidate all assets before first package save
→ save package 1 through existing W3 checkpoint Commit
→ persist child result
→ save package 2 ... in asset order
→ persist exact saved / partially_saved boundary
→ no Strong Verify
```

The central invariant is:

```text
preflight is all-assets
save is sequential per package
cross-package save is NOT atomic
```

W4-4 is orchestration and durable evidence. It must not replace the proven W3 checkpoint implementation.

## 2. Entry Baseline

W4-3 proved in real UE5.6:

```text
C2 BP 3 ops + DA 1 op                PASS
C3 later-asset Apply failure          PASS
C4 resident-only recovery             PASS
Python discovered suite               740 / 740 PASS
C++ changes                           none
```

Current Apply contract:

```text
ue_apply_live_write_batch(...)
  1..4 assets
  1..8 operations per asset
  <=16 operations total
  exact request ordering
  same-asset previousTransactionId chain
  new-asset previousTransactionId reset
  Fast Verify after every successful Apply
  durable global recoveryOrder
```

W4-4 must consume this durable Batch Execution evidence rather than reconstructing resident state from guesses.

Before implementation, inspect actual Git status / HEAD. `76f90b3` is the expected entry checkpoint; repository facts remain authoritative.

## 3. Product Surface

Add the high-level Tool frozen by the W4 parent plan:

```text
ue_save_change_set_checkpoint(batch_execution_id, mode, confirmation="")
```

Recommended modes:

```text
Preview
Commit
```

### Preview

Preview performs no package save.

It:

```text
loads exact Batch Execution
requires state=applied
binds its exact Change Set / Batch Plan / asset order
preflights every touched asset
creates one existing W3 prepared checkpoint per asset
persists one aggregate checkpoint-set record
returns exact aggregate Commit confirmation
```

### Commit

Commit requires the exact confirmation returned by Preview:

```text
SAVE CHANGE SET CHECKPOINT <checkpointSetId>
```

Commit must revalidate every asset before saving the first package, then commit child W3 checkpoints sequentially in frozen asset order.

Do not silently derive authorization from the fact that Apply succeeded. Save remains an explicit separately authorized action.

## 4. Scope Restrictions

W4-4 accepts only a Batch Execution that is fully:

```text
state = applied
```

Do not save:

```text
failed
partially_applied
recovering
unknown/tampered execution state
```

A partially applied task must first be resolved through the resident recovery boundary; W4-4 must not turn a partial mutation into persisted task state by convenience.

W4-4 remains bounded by the existing W4 plan:

```text
1..4 assets
<=16 operations total
```

It does not widen the mutation or Save surface.

## 5. Durable Aggregate Record

Add a dedicated W4 domain module if practical:

```text
src/ue_agent_kit/checkpoint_sets.py
```

Recommended durable record:

```text
ChangeSetCheckpointSetRecord
```

Persist under:

```text
Output/<WorkRoot>/checkpoint-sets/cps_*/checkpoint-set.json
```

Recommended identity:

```text
checkpointSetId = cps_<token>
```

Minimum record:

```text
schemaVersion
checkpointSetId
checkpointSetDigest
batchExecutionId
batchPlanId
batchPlanDigest
changeSetId
state
assetOrder[]
childCheckpoints[]
  assetIndex
  assetPath
  checkpointId
  saveReceipt
  state
  beforeRevision
  afterRevision
  preparedAtUtc
  savedAtUtc
savedCount
failedAsset
failureBoundary
preparedAtUtc
saveStartedAtUtc
savedAtUtc
updatedAtUtc
```

The aggregate record references existing W3 `LiveWriteCheckpointRecord`s. It must not duplicate their full evidence payload or invent a replacement checkpoint format.

Persist an integrity digest and verify it on reload. Tampered checkpoint-set state fails closed.

## 6. Aggregate State Model

W4-4 needs these durable states:

```text
preparing
checkpoint_prepared
saving
saved
partially_saved
failed
```

Semantics:

```text
failed
  no package is known to have been saved by this checkpoint set

checkpoint_prepared
  every child W3 Preview/preflight succeeded; no package saved yet

saving
  Commit began and the durable record identifies the current save boundary

partially_saved
  one or more packages are known saved, but not all

saved
  every child W3 checkpoint reached saved state
```

Never use generic `failed` when `savedCount > 0`.

Do not introduce `verified` here. Aggregate Strong Verify belongs to W4-5.

## 7. Preview / All-Assets Preflight

Preview must complete for every asset before Commit can save anything.

For every asset in Batch Execution `assetOrder` validate at least:

```text
Batch Execution / Batch Plan integrity
exact Change Set binding
asset belongs to this Batch Execution
all expected operations have successful resident evidence
current Editor session matches resident execution requirements
resident Dirty state is attributable to the exact authorized transaction chain
no unrelated transaction is substituted or skipped
current disk Revision matches expected pre-save Revision
W3 effective / superseded operation set matches the intended asset set
backup/save destination and existing authorization prerequisites are valid
```

Then reuse:

```text
ue_save_authorized_asset
  mode=Preview
  verification_mode=checkpoint
  exact change_set_id
```

for each asset to create one prepared W3 checkpoint.

Asset order is the frozen Batch Plan / Batch Execution order.

If asset N Preview fails:

```text
savedCount = 0
no Commit has run
aggregate state = failed
failedAsset = asset N
failureBoundary.phase = preflight
```

Already-created prepared child checkpoints may remain as truthful W3 audit records and must be referenced by the aggregate failure record. They are not evidence that any package was persisted.

No package Save may occur during Preview.

## 8. Commit-Time Global Revalidation

Preview and Commit are separate user actions, so resident/disk state may change between them.

Therefore Commit must perform a second read-only validation of **all assets before the first save**.

Do not implement this by saving asset 1 and relying on asset 2's W3 Commit to discover staleness later.

Preferred implementation:

```text
extract/reuse the existing W3 checkpoint Commit preflight as a read-only helper
→ validate every child checkpoint
→ only if every child is currently committable may the first Save begin
```

Do not duplicate or weaken the W3 checks merely to create a batch API.

If the existing W3 code does not expose a reusable preflight helper, make the smallest internal refactor necessary while preserving existing `ue_save_authorized_asset` behavior and tests.

Commit-time preflight failure requires:

```text
savedCount = 0
state = failed
failureBoundary.phase = commit-preflight
```

## 9. Sequential Save Algorithm

After global revalidation passes:

```text
persist aggregate state=saving

for asset in assetOrder:
    persist current asset/save boundary
    call existing W3 checkpoint Commit
      mode=Commit
      verification_mode=checkpoint
      confirmation="SAVE <exact child save receipt>"
      same exact change_set_id
    require returned checkpointId matches prepared child checkpoint
    require W3 child state=saved
    capture after disk Revision
    persist aggregate child saved state immediately

if every asset succeeds:
    state=saved
```

There is no cross-package transaction and no rollback inside this algorithm.

The save order must equal the frozen asset order. Do not sort by path/class and do not parallelize package saves.

## 10. Failure Semantics

### F0 — failure before first Save

Examples:

```text
bad aggregate confirmation
checkpoint-set tamper
Batch Execution mismatch
child checkpoint tamper
Commit-time global preflight failure
```

Result:

```text
savedCount = 0
no package saved
state = failed
```

### F1 — first package Save fails before persistence

If no package is known saved:

```text
savedCount = 0
state = failed
failedAsset = first asset
failureBoundary.phase = save
```

### F2 — later package Save fails

Example:

```text
BP Save PASS
DA Save FAIL
```

Required durable state:

```text
state = partially_saved
savedCount = 1
persistedAssets = [BP]
failedAsset = DA
pendingAssets = [DA]
```

Do not automatically undo the already-saved package and do not claim task-level atomicity.

Disk rollback belongs to W4-6.

### F3 — process/persistence uncertainty around a Save

Before each child Commit, persist which asset is being saved.

If the package Save may have occurred but aggregate persistence fails before the result is durably recorded, stop immediately. The aggregate must remain in an explicit `saving` / unknown-boundary condition rather than guessing saved or unsaved.

W4-6 will harden restart/recovery of this boundary. W4-4 must at least avoid false certainty.

## 11. Replay / Resume Rules

Required behavior:

```text
checkpoint_prepared
  exact Commit may start once

saved
  repeated read/Commit may return existing durable saved result idempotently

partially_saved
  do not silently resume remaining saves in W4-4
  fail closed with exact persisted/pending boundary

saving / uncertain
  do not guess or replay Save
```

Automatic resume/recovery after restart belongs to W4-6.

## 12. W3 Child Checkpoint Invariants

One W4 aggregate asset equals one existing W3 checkpoint:

```text
1 touched asset/package
→ 1 LiveWriteCheckpointRecord
```

W4-4 must preserve W3 semantics:

```text
Preview freezes exact included/effective/superseded receipts
Commit saves in resident Editor
Commit captures after disk SHA-256 Revision
Save starts zero child Unreal verification processes
Strong Verify remains separate
```

Same-target supersession inside one asset remains W3-authoritative. Only the effective value is persisted/verified later; superseded writes remain audit-visible.

## 13. Public Result Shape

Keep the MCP result compact.

Preview result should include:

```text
checkpointSetId
batchExecutionId
changeSetId
state=checkpoint_prepared
assetCount
assets[]
  assetPath
  checkpointId
  state=prepared
confirmationRequired
savePerformed=false
```

Commit success:

```text
checkpointSetId
state=saved
assetCount
savedCount
assets[]
  assetPath
  checkpointId
  state=saved
  afterRevision
strongVerifyPerformed=false
nextActions
```

Partial Save additionally includes:

```text
persistedAssets[]
failedAsset
pendingAssets[]
failureBoundary
```

Do not return duplicate full W3 checkpoint payloads unless needed for a failure diagnosis.

## 14. Unit / Contract Test Matrix

Minimum deterministic coverage:

```text
A1  one-asset checkpoint-set compatibility
A2  two-asset Preview creates two W3 prepared checkpoints
A3  asset order preserved
A4  Preview performs zero Save
A5  Commit revalidates all assets before first Save
A6  sequential Save follows exact asset order
A7  successful two-asset Commit -> savedCount=2 / state=saved
A8  zero Strong Verify during Preview/Commit

B1  partially_applied Batch Execution rejected
B2  bad confirmation -> zero Save
B3  checkpoint-set tamper -> zero Save
B4  child checkpoint mismatch/tamper -> zero Save
B5  commit-preflight failure on asset 2 -> zero Save including asset 1
B6  replay saved checkpoint set is idempotent/no duplicate Save

C1  injected first Save failure -> failed / savedCount=0
C2  injected second Save failure -> partially_saved / savedCount=1
C3  exact persistedAssets / failedAsset / pendingAssets
C4  aggregate record persisted after every successful child Save
C5  uncertain persistence boundary never claims saved/unsaved without evidence
C6  partially_saved does not silently resume

D1  each child uses existing W3 checkpoint Preview/Commit
D2  child checkpoint before/after Revision propagated correctly
D3  same-target supersession remains correct
D4  W4-2/W4-3 Apply behavior unchanged
D5  existing ue_save_authorized_asset behavior unchanged
```

Use dependency/fault injection at the Python orchestration boundary for controlled save-failure cases. Do not expose a production unsafe failure switch.

## 15. Real UE5.6 Acceptance C5-C8

Primary pair remains:

```text
BP_TransactionBlueprint
DA_TransactionAsset
```

Use the proven W4 sequence first:

```text
BP variable + component + pin
DA setAssetProperty
→ Batch Apply state=applied
→ Fast Verify all 4
```

### C5 — two-asset checkpoint save

Required evidence:

```text
all-assets Preview PASS
checkpointSet state=checkpoint_prepared
child checkpoint count=2
Commit global revalidation PASS
BP save PASS
DA save PASS
checkpointSet state=saved
savedCount=2
child W3 states=saved,saved
Save order=[BP, DA]
Strong Verify child Unreal=0
```

Capture both after-disk SHA-256 Revisions.

### C6 — preflight failure produces zero persisted packages

Create a deterministic condition where asset 2 no longer satisfies the exact prepared/execution contract before Commit, while asset 1 would otherwise be saveable.

Preferred evidence should exercise a real W3 safety invariant such as Revision/effective-set/session/transaction mismatch rather than an artificial generic exception.

Required result:

```text
asset 2 preflight FAIL
savedCount=0
BP disk Revision unchanged
DA disk Revision unchanged from pre-save baseline
no child Commit invoked
state=failed
```

Restore the controlled condition exactly after evidence.

### C7 — controlled mid-save failure

Use a test-only orchestration fault seam after asset 1 has genuinely completed its real W3 Save and before asset 2 Commit.

Required evidence:

```text
BP real Save PASS
DA Save not completed
state=partially_saved
savedCount=1
persistedAssets=[BP]
failedAsset=DA
pendingAssets=[DA]
Strong Verify=0
```

Do not add a production public failure parameter.

W4-4 does not perform disk rollback here. After evidence capture, stop Editor and use the established deterministic fixture Reset to restore the test project.

### C8 — checkpoint-set survives MCP restart

After a successful C5-style Save:

```text
stop/recreate MCP workflow service
reload checkpointSetId from disk
state remains saved
asset order unchanged
child checkpoint IDs unchanged
after Revisions unchanged
savedCount unchanged
```

Do not Strong Verify as part of C8. W4-5 consumes this durable saved checkpoint set.

## 16. Fixture / Disk Cleanup

Because W4-4 intentionally persists packages, C5/C7 change disk state.

After each acceptance sequence that leaves persisted fixture changes:

```text
stop resident Editor when required
run WriteFixturePlan Reset
independently verify both fixtures
refresh frozen snapshot if required by the existing acceptance workflow
```

Do not call W4-4 rollback because W4-4 does not implement disk rollback.

Record final clean fixture Revisions in the Result document.

## 17. Implementation Structure

Preferred new domain owner:

```text
src/ue_agent_kit/checkpoint_sets.py
  ChangeSetCheckpointSetRecord
  persistence / integrity check
  aggregate Preview
  commit-time global preflight
  sequential child checkpoint Commit
  partial_saved boundary
```

Existing files should receive narrow integration only:

```text
agent_workflow.py
  expose/reuse narrow W3 read-only checkpoint Commit preflight if required

mcp_workflow_tools.py
  register ue_save_change_set_checkpoint

tool_registry.py
  ToolDefinition
```

Do not place the full checkpoint-set state machine into MCP registration code or further grow `agent_workflow.py` unnecessarily.

## 18. Regression / Build Gates

Run:

```text
Ruff
Python full discovered suite
compileall
ValidateRelease 0.7.0
git diff --check
```

Record the actual discovered test count; `740` is the W4-3 baseline, not a permanent expected value.

If no C++ changes occur, a new Direct Build is not required solely for W4-4. Real UE C5-C8 remain mandatory because package Save behavior is exercised.

If C++ changes become necessary, stop and review why before implementation; then check conflicting UE/build processes and run the UE5.6 Direct Build gate.

## 19. Stop Conditions

Stop and diagnose if:

```text
all-assets preflight cannot occur before first Save
W4-4 would need to weaken W3 checkpoint authorization
Save would bypass exact W3 receipt/confirmation
cross-package Save is presented as atomic
partial Save cannot be durably distinguished
checkpoint-set persistence cannot identify an uncertain in-progress Save
Strong Verify is pulled into Save to make the design work
partially_applied execution must be auto-saved
recovery would require guessing disk state
existing W3 save/checkpoint behavior regresses
fixture cannot be exactly restored
```

## 20. Implementation Order

Recommended slices:

```text
1. checkpoint_sets.py record / persistence / digest
2. aggregate Preview + one W3 prepared checkpoint per asset
3. exact aggregate confirmation / replay guards
4. reusable all-assets Commit preflight
5. sequential existing W3 checkpoint Commit
6. saved / partially_saved / uncertain durable boundaries
7. MCP + Tool Registry integration
8. unit + fault-injection tests
9. full regression gates
10. real UE C5
11. real UE C6
12. real UE C7
13. real UE C8 restart reload
14. deterministic fixture Reset / independent verification
15. W4-4 Result document
```

Do not implement W4-5 verification aggregation in these slices.

## 21. Exit Gate

W4-4 is complete only when:

```text
[ ] ue_save_change_set_checkpoint exists
[ ] only fully applied Batch Executions are saveable
[ ] one W3 child checkpoint exists per touched asset
[ ] all-assets Preview/preflight occurs before first Save
[ ] Commit revalidates all assets before first Save
[ ] Save order equals frozen asset order
[ ] each package Save uses existing W3 checkpoint Commit
[ ] cross-package atomicity is never claimed
[ ] exact saved / partially_saved boundary is durable
[ ] zero-save preflight failure is proven
[ ] mid-save partial failure is proven
[ ] saved checkpoint set reloads after MCP restart
[ ] Save starts zero Strong Verify child Unreal processes
[ ] existing W1-W3 and W4-1..W4-3 behavior remains compatible
[ ] real UE C5-C8 pass
[ ] fixture is exactly restored after acceptance
[ ] Ruff / Python / compileall / ValidateRelease / git diff --check pass
[ ] UE5.6 Direct Build passes if C++ changed
```

Only after this Exit Gate is green may W4-5 Aggregate Strong Verify / Semantic Diff / Trust begin.

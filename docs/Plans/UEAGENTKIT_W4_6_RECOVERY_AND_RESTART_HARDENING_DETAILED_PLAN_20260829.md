# UEAgentKit W4-6 Recovery and Restart Hardening Detailed Plan

> Date: 2026-08-29
>
> Branch: `feature/live-writer-expansion`
>
> Entry implementation checkpoint: `f4ba1c4` (`feat: add W4-5 aggregate strong verify semantic diff trust`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> Previous result: `UEAGENTKIT_W4_5_AGGREGATE_STRONG_VERIFY_SEMANTIC_DIFF_TRUST_RESULT_20260828.md`
>
> Scope: harden durable recovery/restart boundaries for W4 batch execution and checkpoint sets. Do not add new mutation operations, generic Editor authority, automatic resume, or cross-package atomicity claims.

## 1. Goal

W4-6 must make every incomplete W4 state truthful and safely actionable after MCP restart, while preserving exact W1-W5 safety rules.

Required coverage:

```text
resident-only applied / partially_applied
→ exact reverse resident recovery when original Editor transaction chain still exists

partially_saved
→ rollback persisted assets in reverse save order
→ recover remaining unsaved resident transactions in reverse execution order
→ independently verify disk rollback

MCP restart
→ durable records reload without guessing state

Editor restart
→ resident transaction recovery fails closed when exact session/stack identity is gone

corruption / stack mismatch
→ no inferred recovery
→ no skipping unrelated transactions
```

W4-6 is primarily recovery orchestration and durability hardening. It is not a new Writer.

## 2. Critical Entry Finding — Rollback Material Must Exist Before Restart

The existing authorized-save rollback path contains an important lifetime constraint:

```text
create_authorized_save_rollback_manifest(...)
→ requires the child Plan that authorized the save to still be active
```

After MCP restart, that in-memory Plan identity may no longer be available for creating rollback material.

Therefore W4-6 must not rely on this sequence:

```text
Save succeeds
→ MCP restarts
→ then attempt to create rollback manifest
```

Instead, every successful W4 child checkpoint Save must leave durable rollback-ready material before the aggregate Save moves to the next asset.

Required strengthened sequence:

```text
child W3 checkpoint Commit succeeds
→ afterRevision known
→ promote its authorized-save backup to rollback manifest immediately
→ persist rollback manifest identity/path in checkpoint-set child record
→ only then mark that child durably saved/recovery-ready
→ continue to next asset
```

If rollback-manifest promotion fails after the package Save succeeded:

```text
disk mutation already happened
state must not be reported as clean saved success
checkpoint set must retain the exact persisted boundary
recovery readiness = incomplete
stop before saving the next package
```

Do not hide this condition behind generic `failed`.

This strengthening may be implemented in W4-6 inside the existing W4-4 save orchestration. Existing W4-4 public Save semantics must otherwise remain compatible.

## 3. Public Recovery Surface

Preferred narrow Tool:

```text
ue_recover_live_write_batch(batch_execution_id, mode, confirmation="")
  mode = Preview | Commit | Get
```

Purpose:

- one durable recovery workflow for resident-only and partially-saved W4 batch states;
- reuse existing exact Undo / authorized-save rollback primitives internally;
- reduce Agent-side manual ordering risk;
- never broaden low-level mutation authority.

### Preview

Read-only. It must:

```text
load/tamper-check Batch Execution
resolve related checkpoint set if one exists
classify persisted vs resident-only assets
verify rollback manifests for every persisted asset
inspect current Editor session / exact transaction recoverability
construct exact recovery order
perform no Undo and no disk rollback
persist or return a recovery plan identity/digest
```

### Commit

Requires an exact successful Preview binding and exact confirmation, recommended:

```text
RECOVER LIVE WRITE BATCH <batchExecutionId>
```

Commit must revalidate the complete recovery plan before the first mutation.

### Get

Read-only durable recovery-state reload.

Do not add automatic recovery on Apply/Save/Verify failure. Recovery remains separately authorized.

## 4. Durable Recovery Record

Add a durable record owned by the W4 batch domain, recommended:

```text
LiveWriteBatchRecoveryRecord
```

Path:

```text
Output/<WorkRoot>/batch-recoveries/lwbr_*/recovery.json
```

Minimum fields:

```text
schemaVersion
recoveryId
recoveryDigest
batchExecutionId
batchPlanId
changeSetId
checkpointSetId
state
preparedAtUtc
updatedAtUtc
completedAtUtc

sourceState
editorSessionId

savedAssets[]
  assetPath
  saveReceipt
  checkpointId
  beforeRevision
  afterRevision
  rollbackManifestId
  rollbackManifestPath
  rollbackState
  restoredRevision
  verification

residentRecovery[]
  sequenceIndex
  batchOperationId
  assetPath
  transactionId
  liveApplyReceipt
  editorSessionId
  state

recoveryOrder[]
completedSteps[]
failedStep
pendingSteps[]
failureBoundary
```

State vocabulary should remain narrow and truthful:

```text
recovery_prepared
recovering
recovered
partially_recovered
blocked
failed
```

Interpretation:

```text
blocked
  = no recovery mutation performed because preflight cannot prove safe execution

partially_recovered
  = at least one recovery mutation completed, but full recovery did not

failed
  = recovery failed before any recovery mutation completed
```

Do not represent a mutated partial recovery as generic failed.

## 5. Recovery Classification

### 5.1 Resident-only `applied` / `partially_applied`

No package has been saved.

Recovery candidates come from the Batch Execution `recoveryOrder` and exact W4 live receipts.

Required execution:

```text
global reverse transaction order
→ existing ue_undo_asset_property_live semantics
→ verify exact restored resident value after every Undo
```

The original Editor session and transaction stack must still prove exact identity.

### 5.2 Fully `saved` / `verified`

W4-6 should not automatically treat a completed saved/verified batch as an error state requiring recovery.

Explicit recovery may be allowed only when all required rollback material exists and the user separately authorizes it. The primary W4-6 acceptance focus is incomplete states, especially `partially_saved`.

If supporting recovery from fully saved state adds substantial scope, defer that convenience; do not weaken partially-saved recovery.

### 5.3 `partially_saved`

Split recovery into two sets:

```text
persisted assets
  = checkpointSet.persistedAssets

resident-only operations
  = successful batch operations whose asset was not persisted
```

### UE5.6 evidence correction — transaction-buffer-safe recovery order

Real UE5.6 acceptance proved that the originally planned disk-first order is unsafe when a persisted package must be unloaded while another asset still has resident transactions to Undo. `UPackageTools::UnloadPackages` resets the global Editor transaction buffer by default because transaction records can retain references to unloaded assets.

Therefore W4-6 freezes the executable order as:

```text
1. resident Undo remaining unsaved transactions in strict global reverse execution order
2. close/unload persisted assets and disk rollback them in reverse save order
3. independently verify each disk rollback
4. report exact recovered / unrecovered boundary
```

This ordering does not treat a persisted asset as recovered by resident Undo. Operations belonging to persisted assets remain excluded from the resident Undo set and must be restored through rollback material. Resident-only Undo runs first solely to preserve transaction identities that package unload would otherwise destroy.

Never Undo an operation from a persisted asset as a substitute for disk rollback.

Never include unrelated/user transactions.

## 6. Full Recovery Preflight Before First Mutation

Recovery Commit must perform a complete global revalidation before executing the first recovery step.

For every persisted asset:

```text
checkpoint-set child identity matches
saveReceipt matches
expected current disk Revision == saved afterRevision
rollback manifest exists
rollback manifest validates
manifest targets the exact asset/package
manifest restores the exact pre-save Revision/material
current Editor live-state satisfies existing rollback safety
```

For every resident Undo step:

```text
exact Editor session still active
live receipt / Change Set binding exists
transactionId matches durable record
top transaction chain permits exact reverse recovery
no unrelated transaction must be skipped
```

If any required preflight fails:

```text
state = blocked
recovery mutations performed = 0
```

This zero-mutation global preflight is especially important for partially-saved recovery. Do not begin resident Undo unless the complete recovery boundary is known, and do not unload a persisted package while any required resident transaction still depends on the global transaction buffer.

## 7. Disk Rollback Execution

This phase executes only after all required resident-only Undo steps have completed successfully. The ordering is required because UE5.6 package unload resets the global Editor transaction buffer by default.

For each persisted asset in reverse save order:

```text
existing authorized-save rollback DryRun
→ exact existing rollback Commit
→ capture restored disk Revision / rollback report
→ independent rollback verification
→ persist completed recovery step
```

Use existing backup/rollback engine and exact confirmation internally. Do not implement raw file-copy rollback in W4.

### Independent verification requirement

A disk rollback is not complete merely because a file copy returned success.

The recovery record must prove the restored disk state through existing independent rollback verification evidence where available, including exact Revision/value verification semantics already produced by the rollback workflow.

If the existing rollback primitive cannot provide sufficient independent verification for the W4 contract, stop and add the narrow missing verification binding; do not claim recovery success from filesystem success alone.

### Restart during disk recovery

Persist after every completed rollback step.

After MCP restart:

```text
completed durable step
→ do not execute it again blindly
→ re-check current disk Revision against expected restored Revision
→ if exact, retain completed state
→ if not exact, mark blocked/stale and require explicit diagnosis
```

No guessed idempotence.

## 8. Resident Undo Execution

Before any persisted package unload/disk rollback, recover remaining resident-only transactions using existing exact Undo semantics.

Rules:

```text
strict global reverse execution order
same Editor session required
exact transactionId required
exact receipt/changeSet binding required
Fast/current value verification after Undo as existing primitive provides
```

If an unrelated Editor transaction is now above the expected transaction:

```text
stop
state = partially_recovered if earlier recovery steps already completed
state = blocked if no recovery mutation occurred
```

Never skip the unrelated transaction.

## 9. MCP Restart Hardening

W4-6 must prove durable reload for these objects:

```text
Batch Plan
Batch Execution
Checkpoint Set
child W3 checkpoints
aggregate verification subrecord
Recovery Record
rollback manifests
```

### Case A — restart after Apply, before Save

If the resident Editor process/session is unchanged:

```text
fresh MCP
→ reload Batch Execution
→ Preview recovery
→ exact transaction chain still valid
→ Commit resident reverse recovery
```

MCP restart alone must not invalidate recoverability.

### Case B — restart after partially_saved

Fresh MCP must be able to reconstruct:

```text
which assets persisted
which operations remain resident-only
rollback manifest identities
exact disk Revisions
exact resident recovery sequence
```

No dependency on old in-memory Python objects may be required for determining this boundary.

### Case C — restart after partial recovery

Fresh MCP must reload the Recovery Record and distinguish:

```text
completed recovery steps
pending steps
failed/blocked step
```

It must not restart the whole sequence from the beginning.

Automatic resume is not required. A new explicit Preview/Commit is acceptable.

## 10. Editor Restart Boundary

If the Editor restarts before resident-only recovery:

```text
editorSessionId no longer matches
resident transaction stack identity is gone
```

Required behavior:

```text
resident recovery = blocked / unavailable
no attempt to infer successful Undo from disk state
no synthetic transaction recreation
no automatic Save/Discard
```

Even if disk files appear unchanged, W4 must not claim the former resident transactions were safely undone without exact evidence.

Return a clear manual recovery boundary and the known durable disk facts.

This is a fail-closed success criterion, not a product failure.

## 11. Corruption / Tamper Hardening

At minimum cover:

```text
Batch Execution tamper
Checkpoint Set tamper
Recovery Record tamper
missing child checkpoint
missing rollback manifest
rollback manifest mismatch
completed-step disk Revision mismatch
```

Required behavior:

```text
quarantine/error or deterministic fail-closed result
0 guessed recovery
0 silent record repair
0 mutation when integrity cannot be established
```

If a durable record digest already exists, reuse its established tamper model.

## 12. Public Result Shape

Keep normal responses compact.

Preview/Get should expose:

```text
recoveryId
batchExecutionId
checkpointSetId
state
sourceState
savedAssetCount
residentOperationCount
recoveryOrder[]
blockedReasons[]
confirmationRequired
```

Commit additionally:

```text
recoveredSavedAssets[]
recoveredResidentOperations[]
restoredRevisions[]
pendingSteps[]
failedStep
failureBoundary
fullyRecovered
```

Do not dump full manifests/child records unless needed for diagnosis.

## 13. Unit / Fault-injection Matrix

Minimum deterministic coverage:

```text
A1 resident-only applied -> reverse recovery success
A2 partially_applied -> recover only successful operations
A3 MCP restart reload preserves resident recovery sequence
A4 Editor session mismatch -> blocked / zero Undo
A5 unrelated top transaction -> blocked / no skipped transaction

B1 successful child Save creates rollback manifest before next Save
B2 rollback-manifest promotion failure stops later package Save
B3 partially_saved derives persisted + resident-only sets exactly
B4 disk rollback order is reverse save order
B5 resident-only Undo completes before any package unload/disk rollback that can reset the transaction buffer
B6 disk rollback failure -> exact partial recovery boundary
B7 resident Undo failure before disk rollback -> blocked / partially_recovered with exact boundary

C1 MCP restart after partially_saved can Preview recovery
C2 MCP restart after one completed recovery step reloads completed/pending exactly
C3 completed rollback replay validates restored Revision instead of blindly repeating

D1 Batch/Checkpoint/Recovery tamper fail-closed
D2 missing rollback manifest fail-closed
D3 rollback manifest asset/revision mismatch fail-closed
D4 zero cross-package atomicity claim
D5 no automatic resume
D6 existing W1-W5 APIs remain compatible
```

Use private fault seams only for deterministic boundary injection. Do not add unsafe public test flags.

## 14. Real UE5.6 Acceptance — W4-6

Do not renumber the frozen W4 C1-C12 matrix. Use W4-6-specific H cases.

### H1 — MCP restart after Apply, before Save

```text
BP 3 ops + DA 1 op → applied
keep same UE Editor/session alive
restart MCP only
reload Batch Execution
Preview + Commit recovery
Undo order = DA op4, BP op3, BP op2, BP op1
resident values return exact pre-batch baseline
Save = 0
```

Proves MCP process lifetime is not incorrectly coupled to resident transaction recovery.

### H2 — Editor restart before resident recovery

```text
apply resident batch
capture durable Batch Execution
restart/replace Editor session before recovery
Preview recovery
```

Expected:

```text
blocked
0 Undo
reason = editor session / transaction identity unavailable
```

No false recovered claim.

After evidence capture, use deterministic fixture reset/cleanup rather than unsafe inferred Undo.

### H3 — partially_saved recovery

Create controlled W4-4 partial save:

```text
BP Save PASS + rollback manifest durable
DA remains resident unsaved
checkpointSet = partially_saved
```

Then recovery:

```text
undo remaining DA resident transaction(s)
rollback BP persisted save
independently verify BP restored Revision/value
state = recovered
```

The exact setup may use the existing private mid-save fault seam; production recovery path must be real.

### H4 — MCP restart from partially_saved

From a durable partial-save state:

```text
restart MCP
reload checkpoint set + rollback material
Preview recovery succeeds without old in-memory Plan objects
Commit recovery
exact baseline restored
```

This is the key proof for the rollback-manifest lifetime correction.

### H5 — unrelated transaction stack mismatch

Before resident recovery, add one unrelated/user-style Editor transaction above the next expected W4 transaction.

Expected:

```text
recovery stops fail-closed
unrelated transaction remains untouched
no older W4 transaction is skipped-to/undone
```

Then clean up the unrelated transaction independently.

### H6 — durable recovery restart

Inject a controlled stop/failure after at least one recovery step has durably completed.

Fresh MCP must show:

```text
completed step remains completed
pending steps remain pending
no blind replay
```

Then explicit recovery may continue if all current state checks still pass.

## 15. Fixture / Disk Cleanup

Every real UE case must end with a known exact fixture state.

For disk rollback cases, evidence should first prove recovery through product primitives. `WriteFixturePlan Reset` is final cleanup/independent fixture confirmation, not a substitute for proving W4 recovery.

Record final:

```text
BP Revision
DA Revision
verified=true
no unintended resident Dirty state
```

## 16. Regression / Build Gates

Run:

```text
Ruff
Python full discovered suite
compileall
ValidateRelease 0.7.0
git diff --check
```

Record actual discovered test count; do not freeze `760` as the future gate.

If no C++ changes are made, Direct Build is not required solely for W4-6. Real UE H1-H6 remain required.

If C++ must change, stop first and justify why existing exact Undo/rollback primitives are insufficient, then run UE5.6 Direct Build.

## 17. Stop Conditions

Stop and diagnose if:

```text
rollback material can only be reconstructed from expired in-memory child Plans
recovery requires skipping an unrelated Editor transaction
Editor restart would require guessing prior resident state
partially_saved recovery requires claiming cross-package atomicity
filesystem restore is treated as independently verified without evidence
completed recovery steps cannot be durably distinguished from pending steps
restart requires blindly replaying already-completed rollback/Undo actions
generic arbitrary Editor mutation is needed
W1-W5 fail-closed behavior regresses
fixture cannot be restored exactly
```

Do not weaken recovery evidence to force W4 closure.

## 18. Recommended Implementation Order

```text
1. make every successful W4 child Save immediately rollback-manifest-ready
2. persist rollback identity/readiness in checkpoint-set child records
3. add LiveWriteBatchRecoveryRecord + tamper-safe persistence
4. add recovery Preview/Get and full global preflight
5. add resident-only recovery through existing exact Undo
6. add partially_saved disk rollback orchestration
7. add durable per-step recovery checkpoints / restart handling
8. register ue_recover_live_write_batch
9. unit/fault-injection tests
10. full regression gates
11. real UE H1-H6
12. fixture exact reset/verification
13. W4-6 Result document
```

Do not start W4-7 until all required recovery/restart boundaries are closed or explicitly documented as a fail-closed supported boundary.

## 19. Exit Gate

W4-6 is complete only when:

```text
[x] saved W4 children are rollback-manifest-ready before MCP lifetime can be lost
[x] resident-only recovery uses exact global reverse transaction order
[x] partially_saved recovery separates persisted and resident-only state correctly
[x] persisted assets roll back in reverse save order
[x] disk rollback is independently verified
[x] resident Undo never skips unrelated Editor transactions
[x] MCP restart after Apply preserves recoverability if Editor session survives
[x] MCP restart after partially_saved preserves exact rollback capability
[x] Editor restart before resident recovery fails closed
[x] recovery record is durable/tamper-checked
[x] partial recovery boundary survives MCP restart
[x] completed recovery steps are not blindly replayed
[x] corruption/missing evidence fails closed
[x] no automatic Save / recovery resume is introduced
[x] no cross-package atomicity is claimed
[x] real UE H1-H6 pass
[x] fixture returns to exact baseline
[x] Ruff / Python / compileall / ValidateRelease / git diff --check pass
[x] UE5.6 Direct Build passes if C++ changed
```

Only after this gate is green may W4-7 Full Acceptance / Documentation begin.

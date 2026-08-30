# UEAgentKit W4-7 Full Acceptance / Documentation Detailed Plan

> Date: 2026-08-29
>
> Branch: `feature/live-writer-expansion`
>
> Entry checkpoint: `55919bd` (`feat: close W4-6 recovery and restart hardening`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> Previous result: `UEAGENTKIT_W4_6_RECOVERY_AND_RESTART_HARDENING_RESULT_20260829.md`
>
> Scope: W4 final acceptance, performance/UX comparison, evidence-contract freeze, and documentation closure only. Do not add new Writer capability in W4-7 unless acceptance exposes a concrete correctness blocker.

## 1. Goal

W4-7 closes W4 as a product milestone rather than adding another implementation slice.

Required outcome:

```text
fresh real UE5.6 C1-C12 acceptance
→ exact fixture recovery
→ W4 vs W3 orchestration/performance comparison
→ final Change Set / Batch evidence contract freeze
→ final W4 Result
→ project handoff/navigation updated
```

W4-7 must prove that the bounded high-level workflow reduces Agent orchestration overhead without weakening W1-W3/W4-6 safety semantics.

## 2. Entry Baseline

Before any W4-7 execution, verify actual repository state.

Expected entry:

```text
HEAD    55919bd
branch  feature/live-writer-expansion
worktree clean except W4-7 planning/documentation changes
```

Frozen W4-6 gate at this checkpoint:

```text
Python discovered suite       766 / 766 PASS
Ruff                          PASS
compileall                    PASS
ValidateRelease 0.7.0         PASS
git diff --check              PASS
UE5.6 Direct Build            PASS
real UE H1-H6                 PASS
fixture Reload verify         2 / 2 PASS
```

W4-6 final clean fixture evidence at entry:

```text
DA Revision
sha256:3f2a344d3259d02d8741aa6c77ae9b9c3d491e02ff13375fac6c3ab7b65fb765

BP Revision
sha256:9f1bbff855089eeb41df37d825fd2eefa448b1c008c431526b3a062d40f64eb5

active paired snapshot generation
gen_20260828T185843Z_2b23096543c9
```

These Revision hashes are the W4-7 entry baseline, not permanent fixture constants. An official Fixture Reset may generate new package Revisions; after any Reset, refresh the active paired snapshot before new planning.

Fixture plan remains:

```text
tests/fixtures/multi_operation_transaction_plan.json
planRevision = sha256:d5062503babf97d1c65f6d46809693cbdd89bc6541a28a89f85cf71032f99b6f
```

## 3. Closure Discipline / Non-goals

W4-7 is not a convenient place for cleanup or API redesign.

Do not:

```text
add new public mutation operations
raise W4 hard bounds
rename existing public W4 Tools merely for aesthetics
replace W3 Strong Verify with resident evidence
change cross-package semantics
refactor agent_workflow.py as D1
start W5
start Memory Track M
start generic Blueprint Graph Writer
```

If acceptance exposes a correctness defect that requires product-code modification:

```text
stop W4-7 closure
→ document exact blocker
→ make the narrowest fix
→ rerun all directly affected C cases
→ rerun H1-H6 if recovery/restart/product behavior changed
→ rerun full final gates
```

Do not silently patch product behavior while still claiming the original W4-7 evidence set is valid.

## 4. Evidence Reuse Rule

### C1-C12

C1-C12 must be executed fresh against `55919bd` or the final W4 closure commit if a narrow acceptance fix becomes necessary.

Do not close W4 by merely citing old W4-2 through W4-5 Result files.

### H1-H6

H1-H6 from W4-6 may be reused as frozen evidence because they were executed on the exact implementation that became `55919bd`.

Do not mechanically rerun H1-H6 if W4-7 changes documentation only.

Rerun H1-H6 when any W4-7 fix changes:

```text
resident Apply transaction semantics
checkpoint-set save / rollback material
Batch Recovery
Editor Bridge rollback preparation
restart persistence
Change Set operation/recovery lifecycle
```

## 5. Real UE5.6 Acceptance Execution

Use the established transaction fixtures:

```text
BP = /Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
DA = /Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset
```

Primary normal values:

```text
BP setVariableDefault   TransactionInt = 42
BP setComponentProperty DefaultSceneRoot.RelativeLocation.X = 10
BP setPinDefault        fixed EventGraph pin A = 7
DA setAssetProperty     IntValue = 142
```

Use existing deterministic acceptance/fault seams already established by W4-2 through W4-6. Do not add unsafe production test switches.

### Group G0 — clean start

Before C execution:

```text
no conflicting UnrealEditor / UnrealEditor-Cmd / UBT process
official Fixture Reset if current fixture cannot be proven clean
independent Reload verification
refresh BP and DA into the active paired snapshot if Reset changed Revisions
restart/freeze the workflow session as required by snapshot refresh semantics
```

Record exact starting fixture Revisions and snapshot generation in the W4 final Result.

### Group G1 — C1 + W4 B0 measurement

One Blueprint / three operations:

```text
plan one bounded batch
→ Apply all 3 operations
→ Fast Verify each internally
```

C1 must prove:

```text
all 3 operations applied
same Editor session
exact transaction chain
op2.previousTransactionId = op1.transactionId
op3.previousTransactionId = op2.transactionId
Change Set contains all 3 operations
no Save during Apply
no Strong Verify child Unreal during Apply
```

After C1 evidence is captured, continue the same logical task through high-level checkpoint Save + aggregate Verify so the complete W4 B0 task can be compared with the W4-0 manual W3 B0 baseline.

Then restore/reset the fixture exactly before the next independent scenario.

### Group G2 — C2 + C5 + C8 + C9 + W4 B1 measurement

Use one multi-asset happy path:

```text
BP 3 ops + DA 1 op
→ C2 multi-asset resident Apply PASS
→ checkpoint Preview/Commit
→ C5 both packages saved
→ rebuild/restart MCP service stack
→ C8 checkpoint-set Get/reload survives restart
→ aggregate Strong Verify
→ C9 aggregate Trust verified
```

This group is also the W4 B1 performance/UX measurement because it represents the same logical 2-asset / 4-operation task as W4-0 B1.

Required C2 evidence:

```text
asset request order preserved
BP operation order preserved
DA operation follows BP group
exact per-asset transaction continuation
all Fast Verifies PASS
batch state = applied
```

Required C5 evidence:

```text
all-assets preflight completed before first Save
BP and DA saved sequentially
one W3 checkpoint per asset
checkpoint set state = saved
no cross-package atomicity claim
```

Required C8 evidence:

```text
fresh MCP/service stack
same durable checkpointSetId reloads
child checkpoint identities unchanged
saved boundary unchanged
no old in-memory Plan object required
```

Required C9 evidence:

```text
all effective child checkpoints Strong Verified
Semantic Diff expected == actual
Verification Plan passes
aggregate Trust = verified
Strong Verify child Unreal count <= effective asset count
```

After evidence/metrics capture, restore/reset exact fixture baseline.

### Group G3 — C3 + C4

Controlled later-asset Apply failure:

```text
BP successful operations
→ DA first operation injected/rejected
```

C3 must prove:

```text
state = partially_applied
lastSuccessfulOperation exact
failedOperation exact
notStarted exact
no Save
recoveryOrder = exact reverse successful execution order
```

Then perform C4 through the product recovery path:

```text
ue_recover_live_write_batch Preview
→ exact confirmation Commit
→ global reverse resident Undo
→ baseline restored
```

C4 must prove no unrelated transaction was skipped and no package Save occurred.

### Group G4 — C6

Create the established all-assets Save preflight failure on asset 2.

Required result:

```text
checkpoint Save preflight fails before first package Save
persistedAssets = []
package save count = 0
resident applied state remains truthful
```

After evidence capture, recover the resident batch or use the established deterministic fixture cleanup procedure.

### Group G5 — C7

Use the established private mid-save fault seam:

```text
asset 1 Save PASS
rollback manifest durable
asset 2 Save controlled failure
```

Required result:

```text
state = partially_saved
persistedAssets exact
pendingDirtyAssets exact
failedAsset exact
successful child rollback material already durable
```

Then use W4 recovery to restore the batch where possible, respecting the W4-6 UE5.6-safe order:

```text
resident-only Undo first
→ persisted package close/unload
→ reverse-save-order disk rollback
→ independent rollback verification
```

Do not use raw file copying as acceptance recovery.

### Group G6 — C10

Reproduce the established W4-5 canonical mismatch case without changing production semantics.

Required result:

```text
one child canonical mismatch
aggregate verification != verified
aggregate Trust != verified
truthful successful child evidence retained
no guessed success
```

Use the same controlled acceptance method already proven in W4-5. Do not invent a new arbitrary mutation surface for this test.

### Group G7 — C11

Reproduce the established stale disk Revision case.

Required result:

```text
stale child rejected/fails closed
aggregate verification != verified
aggregate Trust != verified
stale Revision identified explicitly
```

The test must distinguish stale Revision from canonical mismatch.

### Group G8 — C12

Multi-asset request containing repeated same-target writes inside asset 1 plus a normal asset 2 write.

Required result:

```text
both same-target writes remain audit-visible
both are actually executed in request order
W3 supersession marks the earlier write superseded
final effective set contains only the last same-target value for persistence verification
asset 2 normal write remains independent/effective
aggregate Trust may verify only against the final effective set
```

No request-time coalescing is permitted.

## 6. Performance / UX Comparison

Compare W4-0 manual W3 baseline against the W4-7 high-level workflow for the same logical tasks.

### Frozen W3 B0 baseline

```text
1 BP / 3 operations
public MCP Tool calls                 19
resident Editor Bridge calls         42
resident applies                       3
Fast Verifies                          3
checkpoint Save                        1 Preview + 1 Commit
Strong Verify child Unreal             1
public result bytes                54,120
public tool elapsed ms          10,558.147
wall elapsed ms                 12,339.826
```

### Frozen W3 B1 baseline

```text
2 assets / 4 operations
public MCP Tool calls                 27
resident Editor Bridge calls         63
resident applies                       4
Fast Verifies                          4
checkpoint Save                        2 Preview + 2 Commit
Strong Verify child Unreal             2
public result bytes                79,191
public tool elapsed ms          20,752.335
wall elapsed ms                 22,861.093
```

### Measure for W4 B0/B1

Record:

```text
public MCP Tool calls
resident Editor Bridge calls
resident apply count
Fast Verify count
package save count
Strong Verify child Unreal process count
all Unreal child process count by phase
public serialized result bytes
token-visible/result summary size when available
public tool elapsed ms
wall elapsed ms
recovery public Tool calls for G3/G5
```

Measurement boundary must include the same logical task stages as W4-0:

```text
Change Set creation/binding
→ batch planning
→ resident Apply + internal Fast Verify
→ checkpoint Save
→ Strong Verify / Semantic Diff / Verification Plan / Trust
```

Do not count Fixture Reset, test harness setup, snapshot repair, or diagnostic inspection as normal task-path public Tool calls. Record them separately if they materially affect wall time.

### Performance acceptance interpretation

Required:

```text
W4 public Tool-call count < W3 B0/B1 baseline
no per-operation public plan/apply/Fast-Verify loop is required
resident mutation count remains exactly the logical operation count
Apply starts 0 child Unreal verification processes
checkpoint Save starts 0 Strong Verify child Unreal processes
Strong Verify child Unreal count <= effective asset count
no extra cold Unreal process is introduced before Strong Verify
```

Latency and result-size deltas must be reported factually. Do not hide a regression behind lower Tool-call count.

If public orchestration is not materially reduced in practice, W4 should not be declared a UX success without documenting why.

Do not optimize multi-asset Strong Verify in W4-7 solely to improve benchmark numbers.

## 7. Final W4 Evidence Contract Freeze

W4-7 must freeze the durable identity/reference structure that downstream Memory Track M2 can rely on.

Do not create a second parallel evidence model.

Minimum chain to document:

```text
changeSetId
  ↕
batchPlanId + batchPlanDigest
  ↕
batchExecutionId
  ↕
per-operation batchOperationId
  stableTargetKey
  sequenceIndex
  liveApplyReceipt
  editorSessionId
  transactionId / previousTransactionId
  Change Set operation state / supersession
  ↕
checkpointSetId
  child checkpointId
  saveReceipt
  beforeRevision / afterRevision
  rollback manifest identity/readiness
  ↕
aggregate verification evidence
  child Strong Verify
  Semantic Diff
  Verification Plan
  Trust
  ↕
optional recoveryId
  completedSteps
  pendingSteps
  failedStep / failureBoundary
```

Freeze these semantic rules:

```text
Fast Verify = resident evidence only
Strong Verify = independent persisted evidence
one W3 checkpoint = one asset/package
checkpoint set = aggregate orchestration, not atomic package transaction
same-target supersession remains audit-visible
partial-applied / partial-saved / partial-recovered are durable truthful boundaries
resident recovery requires exact Editor session + exact top transaction
completed recovery steps are not blindly replayed
```

Memory Track M2 should reference durable IDs/revision bindings rather than parse human-readable `nextStep` text or duplicate full payloads.

If current serialized records already satisfy this chain, W4-7 documents/fixes the contract wording only. Do not bump schemas merely for documentation symmetry.

## 8. Tool / Capability Count Freeze

At final closure, compute Tool Registry/capability counts from the actual repository.

Do not carry forward the W4-5 counts by hand because W4-6 added `ue_recover_live_write_batch`.

Record:

```text
workflow-only
workflow + memory
combined live + workflow
combined live + workflow + memory
Patch operation count
W4 public high-level Tool set
```

The count is descriptive evidence, not a product-success metric.

## 9. Final Documentation Closure

Create the final W4 result:

```text
docs/Plans/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md
```

It should contain only final facts:

```text
entry/final commit
C1-C12 table
H1-H6 frozen evidence reference
W4 B0/B1 vs W3 B0/B1 metrics
final fixture Revisions + snapshot generation
final Tool counts
frozen evidence contract
known limitations/non-goals
next stage
```

Update:

```text
docs/Plans/README.md
current project handoff
parent W4 Definition of Done
Master/Midterm stage status only where they explicitly track W4 state
docs/PROJECT_STATUS.md / ROADMAP only if those files are still current authoritative surfaces
```

Do not duplicate the full C/H evidence into every planning document.

W4-2 through W4-6 Result files remain historical per-stage evidence.

## 10. Final Gates

After all real UE acceptance and documentation changes:

```text
Ruff
Python full discovered suite
compileall
ValidateRelease 0.7.0
git diff --check
```

Record the actual discovered Python test count; do not hard-code `766` as the future expected count.

### Direct Build

`55919bd` already has a passing UE5.6 Direct Build from W4-6.

If W4-7 changes only docs/test harness scripts and no C++ product code, a redundant Direct Build is not required by the parent contract.

If C++ product code changes for any acceptance fix:

```text
ensure no conflicting UE/build process
→ UE5.6 Direct Build PASS
→ rerun affected real UE cases
→ rerun H1-H6 if recovery/restart semantics were touched
```

## 11. Final Fixture Cleanup

After the last state-mutating acceptance case:

```text
terminate only the W4-7 test-owned Editor process(es)
prove no stale fixture-holding Editor remains
official WriteFixturePlan Reset
independent Reload verification 2 / 2
capture final BP/DA Revisions
refresh active paired snapshot to those exact final Revisions
restart/freeze the MCP workflow session if refresh requires it
```

Do not leave W4 complete with a stale active snapshot or modified transaction fixture.

## 12. Stop Conditions

Stop W4-7 closure if any of the following occurs:

```text
C1-C12 cannot all be proven fresh
fixture cannot be exactly restored
active snapshot cannot be synchronized to final fixture disk state
one aggregate verified claim is possible with a failed child
same-target supersession becomes invisible/coalesced
cross-package behavior is presented as atomic
recovery needs skipping an unrelated Editor transaction
Strong Verify is substituted by resident read-back
batch/checkpoint/recovery durable evidence cannot be linked deterministically
performance measurement requires per-operation public orchestration again
new product behavior is required beyond a narrow correctness fix
W1-W3 regression appears
```

Do not weaken the acceptance definition to force W4 closure.

## 13. Recommended Execution Order

```text
1. verify actual HEAD / Git status / process state
2. verify or Reset fixture + independent Reload verify
3. synchronize active paired snapshot
4. run focused/unit gates required before UE acceptance
5. G1: C1 + W4 B0 measurement
6. exact cleanup/reset
7. G2: C2 + C5 + C8 + C9 + W4 B1 measurement
8. exact cleanup/reset
9. G3: C3 + C4
10. G4: C6
11. G5: C7 + product recovery
12. G6: C10
13. G7: C11
14. G8: C12
15. final official Fixture Reset + Reload verification
16. refresh active snapshot to final Revisions
17. compute Tool/capability counts
18. run final Python/Ruff/compileall/ValidateRelease/diff gates
19. Direct Build only if C++ changed after 55919bd
20. freeze evidence contract in final W4 Result
21. update README / handoff / parent DoD / current status surfaces
22. write final W4 Result
```

Do not begin W5/D1/Memory implementation inside this sequence.

## 14. Exit Gate

W4-7 and W4 are complete only when:

```text
[ ] C1 fresh PASS
[ ] C2 fresh PASS
[ ] C3 exact partial boundary PASS
[ ] C4 resident recovery exact baseline PASS
[ ] C5 multi-asset save PASS
[ ] C6 zero-save preflight failure PASS
[ ] C7 partially_saved exact boundary PASS
[ ] C8 checkpoint-set restart reload PASS
[ ] C9 aggregate Trust verified PASS
[ ] C10 canonical mismatch fail-closed PASS
[ ] C11 stale Revision fail-closed PASS
[ ] C12 same-target supersession effective set PASS
[ ] H1-H6 remain valid for the final product code
[ ] W4 B0/B1 performance/UX metrics captured against W4-0 baseline
[ ] public orchestration is materially lower without extra mutation/verification shortcuts
[ ] final evidence ID/revision contract frozen
[ ] Tool/capability counts captured from actual registry
[ ] final Fixture Reset + independent Reload verify PASS
[ ] active paired snapshot matches final fixture disk Revisions
[ ] Ruff / full Python / compileall / ValidateRelease / git diff --check PASS
[ ] UE5.6 Direct Build PASS for the final product code when required
[ ] parent W4 Definition of Done closed
[ ] final W4 Result written
[ ] README / current handoff show W4 complete
```

Only after this gate is green may the project move to the post-W4 sequence defined by the Master Plan, beginning with the explicitly scheduled next work such as W5 and/or D1 according to the current dependency order.

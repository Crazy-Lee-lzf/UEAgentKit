# UEAgentKit W4-0 Contract Freeze and Baseline Plan

> Date: 2026-08-27
>
> Branch: `feature/live-writer-expansion`
>
> Product-code baseline: `45e6ea2` (`docs: close W3 checkpoint strong verify`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> Scope: freeze the W4 bounded-batch contract and record the W3 manual orchestration baseline before W4-1 implementation begins.

## 1. Goal

W4-0 is a contract/baseline phase only.

It must answer, before any W4 implementation starts:

```text
what requests W4 accepts
what W4 explicitly refuses
how ordering is defined
how partial state is represented
what cross-package guarantees are NOT provided
which W1-W3 primitives remain authoritative
what current manual W3 workflow costs for representative tasks
```

No new Writer behavior is required in W4-0.

## 2. Source of Truth

Authority order for W4-0:

```text
1. Current repository facts at 45e6ea2
2. W3 result / verified W1-W3 behavior
3. W4 Detailed Plan
4. Master Development Plan / Midterm Execution Spec
5. This W4-0 execution plan
```

If this document conflicts with the W4 Detailed Plan, update this document rather than creating a second W4 contract.

## 3. Frozen W4 Scope

Initial W4 supports only these already-proven resident operations:

```text
setAssetProperty
setVariableDefault
setComponentProperty
setPinDefault
```

W4-0 explicitly does not authorize:

```text
Generic Blueprint Graph CRUD
arbitrary UObject/property reflection batch writing
Material Graph / Niagara / Sequencer generic mutation
unbounded asset lists
cross-package atomic transactions
automatic save without explicit authorization
resident read-back as independent Strong Verify
source-control write behavior
```

## 4. Frozen Bounds

Initial Agent-facing hard bounds:

```text
max assets per batch           4
max operations per asset       8
max total operations          16
max total request size        64 KiB
max individual value size     existing Policy maxValueBytes
```

Effective limit is always the minimum of:

```text
W4 hard bound
Policy maxAssetsPerPatch
Policy maxOperationsPerAsset
Policy maxValueBytes
remaining Change Set capacity
```

These values remain conservative until W5 produces real-project evidence for raising them.

## 5. Request Ordering Contract

Use asset-grouped input.

Required ordering rules:

1. assets execute in request order;
2. operations inside one asset execute in request order;
3. the same asset may appear only once in one batch;
4. no cross-asset dependency is implied;
5. same-target repeated writes remain audit-visible;
6. W3 supersession decides the final effective persisted value;
7. Apply must not silently remove intermediate requested writes.

A flat interleaved request such as:

```text
A.op1 -> B.op1 -> A.op2
```

is outside the initial contract.

## 6. Public Product Surface to Freeze

W4-0 freezes these four high-level responsibilities as the preferred public surface:

```text
ue_plan_live_write_batch
ue_apply_live_write_batch
ue_save_change_set_checkpoint
ue_verify_change_set_checkpoint
```

Exact registry names may still change during W4-1 Tool Registry review, but responsibility boundaries must not drift.

Existing W1-W3 single-operation tools remain supported and unchanged.

## 7. Primitive Reuse Contract

W4 orchestration must reuse existing proven primitives.

```text
Plan validation
  -> existing OperationSpec / Policy / Revision validation

Resident Apply
  -> existing single-operation live apply
  -> exact previousTransactionId chain for same asset

Per-operation verification
  -> W2 Fast Resident Verify

Per-asset persistence
  -> W3 checkpoint save

Per-asset independent verification
  -> W3 Strong Checkpoint Verify

Task-level evidence
  -> existing Change Set / Semantic Diff / Verification Plan / Trust
```

W4 must not add a generic C++ batch mutation endpoint unless the existing primitives are proven insufficient and the plan is explicitly revised.

## 8. State Contract

Initial durable batch states:

```text
planned
applying
applied
partially_applied
failed
recovering
recovered
checkpoint_prepared
partially_saved
saved
partially_verified
verified
stale
```

Per-operation lifecycle continues to use existing Change Set states where possible:

```text
applied
saved
verified
superseded
undone
discarded
failed
unknown
```

W4 must not create a second competing operation lifecycle.

## 9. Failure Semantics to Freeze

### 9.1 Before first mutation

```text
state = failed
appliedCount = 0
no package mutation
no recovery required
```

### 9.2 During Apply

If earlier operations succeeded:

```text
state = partially_applied
lastSuccessfulOperation = exact operation id
failedOperation = exact operation id
notStarted = exact remaining operation ids
```

No automatic Save occurs.

Recovery order is the exact global reverse execution order.

### 9.3 During Save

Cross-package save is explicitly non-atomic.

Before saving the first package, W4 must complete an all-assets preflight.

If one package has already persisted and a later save fails:

```text
state = partially_saved
persistedAssets = exact ordered set
pendingDirtyAssets = exact ordered set
failedAsset = exact asset
```

No result may describe this as an atomic rollback-safe transaction.

### 9.4 During Strong Verify

A failed/stale child checkpoint prevents aggregate `verified`.

Successful child verification evidence remains truthful and is not downgraded.

## 10. Recovery Contract

Resident-only recovery:

```text
reverse execution order
-> exact existing Undo/Discard primitive
-> never skip an unrelated user Editor transaction
-> stack mismatch = fail closed
```

Partially saved recovery:

```text
1. rollback persisted assets in reverse save order
2. independently verify each disk rollback
3. undo/discard unsaved resident transactions in reverse execution order
4. report exact recovered/unrecovered boundary
```

W4-0 does not require a new aggregate recovery Tool. W4 should initially return structured existing-tool recovery actions.

## 11. Checkpoint Aggregation Contract

W3 checkpoint records remain authoritative per asset.

W4 may add an aggregate record, provisionally:

```text
ChangeSetCheckpointSetRecord
```

with one child W3 checkpoint per touched asset.

Initial persistence location:

```text
Output/<WorkRoot>/checkpoint-sets/cps_*.json
```

The aggregate record must contain at minimum:

```text
checkpointSetId
changeSetId
state
assetOrder[]
childCheckpoints[]
failureBoundary
preparedAtUtc
savedAtUtc
verifiedAtUtc
```

W4-0 freezes the semantic requirement, not the final Python class layout.

## 12. Strong Verify Cost Contract

Initial W4 does not optimize multi-asset independent export.

Acceptable W4 cost:

```text
child Unreal Strong Verify processes <= effective asset count
```

Optimization across multiple assets belongs to W5 only if measurements show it is worthwhile.

## 13. W3 Manual Baseline to Record

W4-0 must record the current manual W3 orchestration cost for two representative tasks.

### B0 — one Blueprint / three operations

Use the existing deterministic Blueprint fixture:

```text
setVariableDefault
setComponentProperty
setPinDefault
```

Measure the complete manual W3 path through persisted/verified task state.

### B1 — two assets / four total operations

Use:

```text
BP_TransactionBlueprint   3 operations
DA_TransactionAsset       1 operation
```

This is a manual-orchestration baseline only; no W4 batch implementation is used.

### Required metrics

For B0 and B1 record:

```text
public MCP Tool calls
resident Editor Bridge calls
resident apply count
Fast Verify count
checkpoint save count
Strong Verify child Unreal process count
elapsed wall-clock time
serialized result size where available
final Semantic Diff state
final Trust verdict
fixture recovery result
```

The baseline should preserve raw evidence or exact command/tool outputs sufficient for later W4 comparison.

## 14. Regression Baseline

Current product-code baseline is:

```text
commit                     45e6ea2
Python discovered suite    712 / 712 PASS
ValidateRelease            0.7.0 PASS
Ruff                       PASS
compileall                 PASS
UE5.6 Direct Build         PASS
W3 real acceptance         C0-C6 PASS
git diff --check           PASS
```

The test count is a W4-0 entry observation, not a permanent future expected count.

W4-0 itself should produce no product-code delta.

## 15. Deliverables

W4-0 produces:

1. this frozen execution contract;
2. a W4-0 baseline result document containing B0/B1 measurements;
3. any small correction to the parent W4 plan required by measured repository facts;
4. no Writer implementation code.

Recommended result file:

```text
UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_RESULT_20260827.md
```

## 16. Execution Steps

```text
S0  verify Git baseline / active worktree / no concurrent UE process
S1  re-run lightweight regression gate needed for baseline integrity
S2  run B0 manual W3 workflow and capture evidence
S3  restore fixture exactly
S4  run B1 manual W3 workflow and capture evidence
S5  restore all fixtures exactly
S6  compare observed behavior against sections 3-12
S7  resolve any contract mismatch in docs before implementation
S8  write W4-0 result
S9  run document + release gates
```

Do not start W4-1 until S6/S7 are clean.

## 17. W4-0 Exit Gate

W4-0 is complete only when:

```text
[ ] supported operation set frozen
[ ] hard/Policy bounds frozen
[ ] ordering contract frozen
[ ] no cross-package atomicity explicitly documented
[ ] partial_applied semantics frozen
[ ] partial_saved semantics frozen
[ ] Strong Verify aggregation semantics frozen
[ ] recovery ordering and fail-closed transaction-stack rule frozen
[ ] W3 primitives explicitly remain authoritative
[ ] B0 baseline recorded
[ ] B1 baseline recorded
[ ] fixtures exactly recovered after both baselines
[ ] no product behavior change made
[ ] full current Python suite passes
[ ] ValidateRelease 0.7.0 passes
[ ] git diff --check passes
```

UE5.6 Direct Build is not required merely for documentation changes, but the existing W3 Direct Build PASS remains part of the entry baseline.

## 18. Stop Conditions

Stop W4-0 and resolve the contract before W4-1 if measurement shows any of the following:

```text
manual W3 workflow cannot represent the proposed batch semantics
same-asset continuation is not stable for the B0 sequence
W3 checkpoint cannot cleanly aggregate one checkpoint per touched asset
existing Change Set cannot preserve exact execution order / partial state
recovery requires skipping unrelated Editor transactions
fixture recovery is not exact
current repository facts contradict the parent W4 plan
```

W4-0 is successful when W4-1 can begin without inventing safety semantics during implementation.

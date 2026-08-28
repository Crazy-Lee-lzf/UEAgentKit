# UEAgentKit W4-3 Multi-Asset Resident Apply Detailed Plan

> Date: 2026-08-28
>
> Branch: `feature/live-writer-expansion`
>
> Expected entry implementation checkpoint: `ee5dad1` (`feat: add W4-2 single-asset multi-operation apply`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> Previous result: `UEAGENTKIT_W4_2_SINGLE_ASSET_MULTI_OPERATION_APPLY_RESULT_20260828.md`
>
> Scope: extend the proven W4-2 resident Batch Apply from one asset to bounded multi-asset execution and prove resident-only partial recovery. Do not implement Save, checkpoint-set persistence, aggregate Strong Verify, disk rollback, or restart hardening.

## 1. Goal

W4-3 extends the existing public Tool:

```text
ue_apply_live_write_batch(batch_plan_id, confirmation, change_set_id)
```

from:

```text
1 asset / 1..8 operations
```

to the existing W4 bounded contract:

```text
1..4 assets
1..8 operations per asset
<= 16 total operations
```

Execution remains deterministic:

```text
asset groups in immutable Batch Plan request order
→ operations inside each asset in request order
→ existing resident single-operation Apply
→ Fast Verify after every successful Apply
→ exact durable partial boundary on failure
→ no Save
```

Primary real UE success slice:

```text
BP_TransactionBlueprint
  op1 setVariableDefault
  op2 setComponentProperty
  op3 setPinDefault

DA_TransactionAsset
  op4 setAssetProperty
```

W4-3 must prove C2-C4 before W4-4 starts.

## 2. Entry Baseline

W4-2 is complete with real UE5.6 C1 evidence.

Known baseline at W4-2 closeout:

```text
ue_apply_live_write_batch                       available
LiveWriteBatchExecutionRecord                   durable
single-BP 3-op Apply                            PASS
Fast Verify after every operation               PASS
exact previousTransactionId chain               PASS
failed / partially_applied boundaries           PASS
replay / tamper / confirmation fail-closed      PASS
Save / Strong Verify                            0
Python discovered suite                         738 / 738 PASS at W4-2 closeout
C++ changes                                     none
```

W4-2 real transaction-chain evidence and RESULT are authoritative over older planning text.

Before implementation, inspect actual Git status and HEAD. Do not assume the worktree is unchanged merely because this plan names `ee5dad1` as the expected entry checkpoint.

## 3. Scope Boundary

W4-3 implements only:

```text
multi-asset resident Apply
multi-asset durable execution evidence
exact later-asset failure boundary
resident-only recovery using existing exact Undo primitives
real UE C2-C4
```

W4-3 does NOT implement:

```text
package Save
W3 checkpoint orchestration
ChangeSetCheckpointSetRecord
cross-package save preflight
Strong Verify
Semantic Diff / Trust aggregation
disk rollback
MCP restart recovery
Editor restart recovery
cross-package atomicity
```

All mutations remain unsaved resident Editor state in this phase.

## 4. Product Surface

### 4.1 Apply

Keep the existing Tool name and arguments unchanged:

```text
ue_apply_live_write_batch(batch_plan_id, confirmation, change_set_id)
```

W4-3 removes only the W4-2 `assetCount == 1` execution restriction. It must not introduce a second multi-asset Apply Tool.

The immutable W4-1 hard/Policy bounds remain authoritative.

### 4.2 Recovery

Do not add a new public aggregate recovery Tool by default in W4-3.

For resident-only recovery, the Batch Execution result must provide exact structured recovery metadata over the existing proven primitive:

```text
ue_undo_asset_property_live
```

and recovery must execute in exact global reverse successful-transaction order.

If real C4 shows that the existing primitive cannot safely or deterministically recover the batch from persisted evidence, stop and document the blocker before adding a new aggregate recovery API. A convenience aggregate recovery Tool is not required merely to reduce call count in W4-3; broader recovery/restart hardening belongs to W4-6.

## 5. Core Multi-Asset Ordering Contract

For Batch Plan assets:

```text
A: op1, op2, op3
B: op4
```

execution is exactly:

```text
A.op1
→ Fast Verify
→ A.op2
→ Fast Verify
→ A.op3
→ Fast Verify
→ B.op4
→ Fast Verify
```

Never interleave asset groups.

The same asset already cannot appear twice in one W4 Batch Plan; preserve that W4-1 rule.

No cross-asset dependency is inferred. Request order is execution order only.

## 6. Transaction Continuation Across Assets

This distinction is critical.

### Same asset

Within one asset group, preserve W4-2/W3 continuation:

```text
A.op1 txA1
A.op2 previousTransactionId = txA1
A.op3 previousTransactionId = txA2
```

### New asset

The first operation of the next asset does NOT inherit the previous asset's transaction ID:

```text
B.op1 previousTransactionId = none
```

Do not create a cross-package `previousTransactionId` chain.

The Editor transaction stack is still globally ordered for recovery, but W3 continuation identity is same-asset only.

Example successful global execution order:

```text
txA1
→ txA2
→ txA3
→ txB1
```

Exact resident recovery order is therefore:

```text
txB1
→ txA3
→ txA2
→ txA1
```

These are two different contracts and must not be conflated.

## 7. Editor Session Contract

All successful operations in one Batch Execution must belong to the same live Editor session.

Persist and verify:

```text
editorSessionId
```

for every resident receipt.

If a later operation reports a different session identity, stop immediately and persist an exact partial boundary. Never continue a Batch Execution across an implicit Editor restart/reconnect as though the transaction stack were continuous.

Editor/MCP restart recovery is W4-6 scope.

## 8. Durable Execution Record Evolution

Reuse and extend W4-2 `LiveWriteBatchExecutionRecord`; do not create a second execution journal.

Existing path remains:

```text
Output/<WorkRoot>/batch-executions/lwbe_*/execution.json
```

The schema must support multi-asset evidence without invalidating existing W4-2 one-asset records.

Recommended additions/evolution:

```text
batchExecutionId
batchPlanId / batchPlanDigest
changeSetId
state
editorSessionId
assetOrder[]
assets[]
  assetPath
  assetClass
  expectedRevision
  state
  operationCount
  appliedCount
operations[]
  sequenceIndex
  assetIndex
  assetPath
  batchOperationId
  childPlanId
  operation
  stableTargetKey
  state
  liveApplyReceipt
  transactionId
  previousTransactionId
  fastVerifyResult
  failure
lastSuccessfulOperation
failedOperation
notStarted[]
recoveryOrder[]
startedAtUtc / updatedAtUtc / completedAtUtc
```

If the existing record currently has a singular top-level `assetPath`, evolve it backward-compatibly. Existing W4-2 persisted records must remain readable or produce an explicit supported-version error; do not silently reinterpret old payloads.

Global `sequenceIndex` remains the authoritative recovery ordering key.

## 9. Preflight Before First Mutation

Before consuming the first child Plan:

```text
1. load Batch Plan through W4-1 tamper check
2. verify exact batch confirmation
3. verify Batch Plan is within W4 hard/Policy bounds
4. verify all child Plan IDs/order match immutable Batch Plan
5. verify all required child Plans still exist and are unconsumed
6. bind one existing Change Set
7. reject replay/already-started Batch Plan
8. create/persist Batch Execution state=applying
```

This is an immutable-artifact/execution preflight.

Do not claim an all-assets resident runtime preflight guarantee in W4-3. Runtime Dirty/session/target checks remain authoritative at each existing single-operation resident Apply. Therefore a later asset can truthfully fail after earlier assets have already mutated, producing `partially_applied`.

## 10. Per-Asset / Per-Operation Apply Algorithm

Pseudo-flow:

```text
previousAsset = none
previousTransactionId = none

for asset in BatchPlan.assets in request order:
    previousTransactionId = none

    for operation in asset.operations in request order:
        assert child Plan exact + available

        existing resident Apply(
            childPlan,
            changeSetId,
            previousTransactionId for same asset only
        )

        capture exact receipt / transaction / session

        existing Fast Resident Verify(...)

        persist successful operation evidence

        previousTransactionId = current transactionId
```

A successful Apply is not a successful W4 sequence step until Fast Verify passes.

Do not start the next operation or next asset after a failed Apply or failed Fast Verify.

## 11. Failure Semantics

### F0 — failure before first successful mutation

```text
state = failed
appliedCount = 0
recoveryOrder = []
```

No Save occurs.

### F1 — failure within first asset

Same behavior as W4-2:

```text
A.op1 PASS
A.op2 FAIL
A.op3 / B.* not started

state = partially_applied
lastSuccessfulOperation = A.op1
failedOperation = A.op2
notStarted = [A.op3, B.*]
```

### F2 — later-asset first-operation failure

Primary W4-3 partial case:

```text
A.op1 PASS
A.op2 PASS
A.op3 PASS
B.op1 FAIL
```

Required durable result:

```text
state = partially_applied
lastSuccessfulOperation = A.op3
failedOperation = B.op1
notStarted = []                 # if B.op1 is final batch operation
recoveryOrder = [A.op3, A.op2, A.op1]
```

The failed B operation must not be represented as applied.

### F3 — later-asset Fast Verify failure

If B.op1 Apply succeeds but its Fast Verify fails:

```text
state = partially_applied
failedOperation = B.op1
```

Retain B.op1 mutation receipt/transaction evidence and include it first in `recoveryOrder`, followed by earlier successful transactions in reverse global execution order.

Do not continue to any later operation.

### F4 — durable persistence failure after mutation

Same W4-2 rule remains: stop immediately, report all exact in-memory receipts that can truthfully be reported, and do not apply another operation without durable sequence evidence.

## 12. Recovery Contract for W4-3

W4-3 recovery is resident-only and unsaved.

Use persisted `recoveryOrder[]` and existing exact Undo primitive in strict order.

For a fully applied C2 batch:

```text
DA.op1
→ BP.op3
→ BP.op2
→ BP.op1
```

For C3 where DA.op1 failed before mutation:

```text
BP.op3
→ BP.op2
→ BP.op1
```

Every recovery action must bind exact existing evidence:

```text
assetPath
liveApplyReceipt
transactionId
editorSessionId
changeSetId
batchOperationId
sequenceIndex
```

Never:

```text
skip an unrelated Editor transaction
infer a missing transaction
undo by asset grouping instead of global reverse execution order
undo a failed operation that never mutated
claim recovery success if an expected exact transaction cannot be restored
```

If the transaction stack no longer matches the next expected UEAgentKit transaction, recovery stops fail-closed at that boundary.

Disk rollback is not part of W4-3 because no Save is authorized.

## 13. Controlled Real UE Later-Asset Failure Strategy

C3 should prove a real resident failure without adding an unsafe production fault switch.

Preferred strategy:

```text
1. start from clean deterministic BP + DA fixtures
2. create the W4 Batch Plan for BP 3 ops + DA 1 op
3. before batch Apply, create one known unrelated resident DA transaction/change under a separate receipt/change-set so DA is Dirty
4. execute W4 batch
5. BP three operations Apply + Fast Verify normally
6. DA batch operation reaches existing resident writer
7. existing unrelated Dirty-package safety rejects DA operation
```

Expected error remains the existing fail-closed Dirty-package behavior, not a W4-specific bypass.

This case proves that W4-3 does not weaken W3 Dirty safety merely to finish a batch.

The unrelated pre-existing DA transaction is part of the pre-batch resident baseline. Batch recovery must not undo it.

After C4 evidence is captured, clean the injected unrelated transaction separately using its exact receipt, then run deterministic fixture Reset / independent verification.

If this setup cannot be made deterministic with existing safe public/test primitives, use orchestration-level fault injection for the failure mechanics and document why; do not add production mutation bypasses solely to manufacture C3.

## 14. Unit / Contract Test Matrix

Minimum deterministic coverage:

```text
A1  2 assets / 4 ops executes exact asset + op order
A2  first asset previousTransactionId chain remains exact
A3  second asset first op previousTransactionId resets to none
A4  Fast Verify gates every operation before next op/asset
A5  same Editor session required across all successful receipts
A6  final state applied with 4 successful operations

B1  4-asset W4 boundary remains executable
B2  total operation order follows global sequenceIndex
B3  one Change Set receives all successful cross-asset operations
B4  replay / Batch Plan tamper / child tamper remain fail-closed
B5  existing W4-2 one-asset behavior remains compatible
B6  existing W4-2 execution records remain readable

C1  later asset Apply failure -> exact partially_applied boundary
C2  later asset first op failure does not enter recoveryOrder
C3  later asset Fast Verify failure includes its mutated transaction in recoveryOrder
C4  notStarted contains all operations after failure in global sequence order
C5  recoveryOrder is exact global reverse mutation order
C6  recovery never groups by asset incorrectly
C7  unrelated transaction stack mismatch stops recovery fail-closed

D1  no Save/checkpoint/Strong Verify invoked
D2  zero child Unreal processes from Apply/recovery path
D3  same-target supersession remains audit-visible
D4  stableTargetKey semantics unchanged
D5  Policy / Revision / Dirty-package checks remain unchanged
```

Use Python orchestration fault injection for edge cases that do not need real UE behavior. Do not add unsafe production test switches.

## 15. Real UE5.6 Acceptance C2

### C2 — BP 3 ops + DA 1 op, all applied

Fixture pair:

```text
/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset
```

Sequence:

```text
BP op1 setVariableDefault
BP op2 setComponentProperty
BP op3 setPinDefault
DA op4 setAssetProperty
```

Required evidence:

```text
Batch Plan asset order                  [BP, DA]
global sequence                         0,1,2,3
BP previousTransactionId chain          exact
DA first previousTransactionId          none
same Editor session                     PASS
Apply count                             4
Fast Verify count                       4
Change Set operation count              4
state                                   applied
package Save count                      0
Strong Verify child Unreal              0
```

Capture all transaction IDs and the global recovery order.

After evidence capture, recover all four resident transactions in exact global reverse order and verify both assets return to the pre-batch resident baseline before final fixture cleanup.

## 16. Real UE5.6 Acceptance C3

### C3 — later asset failure produces exact partial boundary

Preferred real setup uses the controlled unrelated Dirty DA precondition from Section 13.

Expected batch execution:

```text
BP op1  PASS + Fast Verify
BP op2  PASS + Fast Verify
BP op3  PASS + Fast Verify
DA op4  FAIL before mutation because unrelated DA Dirty state remains fail-closed
```

Required evidence:

```text
state                     partially_applied
appliedCount              3
lastSuccessfulOperation   BP op3
failedOperation           DA op4
DA op4 mutation receipt   absent
recoveryOrder             [BP op3, BP op2, BP op1]
Save count                0
```

The pre-existing unrelated DA transaction must not appear in batch `recoveryOrder`.

## 17. Real UE5.6 Acceptance C4

### C4 — resident-only partial recovery returns exact pre-batch baseline

Starting from the C3 partial batch:

```text
use Batch Execution recoveryOrder
→ undo BP op3
→ undo BP op2
→ undo BP op1
```

Required evidence:

```text
all batch-created resident transactions removed in exact reverse order
BP variable/component/pin values match pre-batch baseline
BP package resident state matches expected pre-batch state
unrelated DA transaction/value remains untouched
DA pre-existing Dirty state remains exactly as it was before batch Apply
no Save
no disk rollback
```

Then separately recover the deliberately injected unrelated DA transaction with its own exact receipt.

Finally run deterministic `WriteFixturePlan Reset` and independently verify both fixtures are restored exactly.

C4 does not authorize skipping unrelated Editor transactions. If an unexpected transaction sits above the expected batch transaction, C4 must fail closed rather than force recovery.

## 18. Evidence Outputs

Recommended acceptance outputs:

```text
Output/W4Acceptance/w4-c2-multi-asset-apply-report.json
Output/W4Acceptance/w4-c3-later-asset-failure-report.json
Output/W4Acceptance/w4-c4-resident-recovery-report.json
Output/W4Acceptance/ResetAfterW4_3/fixture-report.json
```

Persisted product evidence remains under:

```text
Output/<WorkRoot>/batch-executions/lwbe_*/execution.json
```

Reports should include exact receipts/transaction IDs/session IDs/change-set ID and enough before/after resident values to prove C2-C4 without relying on console prose.

## 19. Regression / Build Gates

After implementation:

```text
Ruff
Python full discovered suite
compileall
ValidateRelease 0.7.0
git diff --check
```

Record the actual discovered test count in the W4-3 RESULT. Do not use `738` as a permanent expected count.

If no C++ changes are made, a new UE5.6 Direct Build is not required solely for W4-3. Real UE C2-C4 are still mandatory.

If C++ changes become necessary:

```text
stop and justify why existing resident primitives are insufficient
check no conflicting UE/build process
run UE5.6 Direct Build
```

A new generic C++ batch mutation endpoint is not the default solution.

## 20. Implementation Order

Recommended slices:

```text
1. evolve LiveWriteBatchExecutionRecord for multi-asset evidence, backward-compatible with W4-2
2. remove only W4-2 one-asset execution restriction
3. execute immutable asset groups in request order
4. reset previousTransactionId at each new asset boundary
5. persist global sequence / per-asset summaries
6. harden later-asset Apply/Fast Verify partial boundaries
7. expose exact global recoveryOrder over existing Undo primitive
8. unit/fault-injection tests
9. full Python/release gates
10. real UE C2
11. real UE C3 controlled later-asset failure
12. real UE C4 resident-only recovery
13. deterministic fixture cleanup + independent verification
14. write W4-3 RESULT
```

Do not implement W4-4 checkpoint-set/Save work in these slices.

## 21. Stop Conditions

Stop and diagnose before continuing if:

```text
multi-asset Apply requires cross-package previousTransactionId chaining
Dirty-package safety must be weakened for a later asset
Fast Verify must be skipped between assets
partial state cannot identify the exact last mutation globally
recovery requires grouping by asset rather than global reverse transaction order
recovery requires skipping unrelated Editor transactions
existing W4-2 records become unreadable without explicit migration/version handling
existing one-asset W4-2 behavior regresses
same-target supersession regresses
implementation requires arbitrary generic C++ batch mutation dispatch
any Save/checkpoint/Strong Verify occurs in W4-3 Apply
fixture cannot be restored exactly
```

W4 convenience must not weaken W1-W3 trust/recovery guarantees.

## 22. Exit Gate

W4-3 is complete only when:

```text
[ ] existing ue_apply_live_write_batch supports 1..4 asset groups within W4 bounds
[ ] assets execute in immutable request order
[ ] operations execute in immutable per-asset request order
[ ] same-asset previousTransactionId chain remains exact
[ ] new-asset first operation resets previousTransactionId to none
[ ] all successful operations bind one exact Editor session
[ ] Fast Verify passes before the next operation/asset starts
[ ] durable execution record represents multi-asset state truthfully
[ ] later-asset failure produces exact partially_applied boundary
[ ] recoveryOrder is exact global reverse mutation order
[ ] resident-only recovery uses existing exact Undo safety
[ ] unrelated Editor transactions are never skipped or included as batch recovery
[ ] W4-2 one-asset behavior and persisted evidence remain compatible
[ ] no Save/checkpoint/Strong Verify occurs
[ ] real UE5.6 C2 passes
[ ] real UE5.6 C3 passes
[ ] real UE5.6 C4 passes
[ ] final fixture Reset + independent verification passes
[ ] Ruff / Python discovered suite / compileall / ValidateRelease / git diff --check pass
[ ] UE5.6 Direct Build passes if C++ changed
```

Only after this Exit Gate is green may W4-4 Multi-Asset Checkpoint Save begin.

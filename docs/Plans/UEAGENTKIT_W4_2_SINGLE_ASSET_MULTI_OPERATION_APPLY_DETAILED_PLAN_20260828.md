# UEAgentKit W4-2 Single-Asset Multi-operation Apply Detailed Plan

> Date: 2026-08-28
>
> Branch: `feature/live-writer-expansion`
>
> Entry implementation checkpoint: `71400c9` (`feat: add W4-1 bounded batch planning`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> Previous result: `UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_RESULT_20260828.md`
>
> Scope: implement only the W4-2 single-asset multi-operation resident Apply slice. Do not enter multi-asset Apply, Save, checkpoint-set, aggregate Strong Verify, or recovery execution.

## 1. Goal

W4-2 turns an immutable W4-1 Batch Plan for one asset into one bounded high-level resident execution while preserving the existing W1-W3 single-operation safety model.

Primary real UE slice:

```text
one Blueprint
  op1 setVariableDefault
  op2 setComponentProperty
  op3 setPinDefault
```

Required execution:

```text
immutable W4-1 Batch Plan
→ exact batch confirmation
→ consume child Plan 1
→ existing resident live apply
→ Fast Verify op1
→ consume child Plan 2 with exact previousTransactionId
→ existing resident live apply
→ Fast Verify op2
→ consume child Plan 3 with exact previousTransactionId
→ existing resident live apply
→ Fast Verify op3
→ persist exact execution evidence / final state
→ no Save
```

W4-2 is orchestration only. It must not introduce a second mutation implementation.

## 2. Entry Preconditions

Before implementation:

```text
inspect actual Git status / HEAD
read docs/Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
read docs/Plans/README.md
read W4 parent Plan
read W4-1 Result
```

Expected implementation entry checkpoint is `71400c9`, but actual repository state is authoritative.

W4-1 invariants that W4-2 consumes unchanged:

```text
Batch Plan is immutable and tamper-checked
asset / operation ordering is frozen
child Plan IDs are existing single-op plan identities
stable target identity matches W3 semantics
same-target repeats are retained
supersession preview is audit metadata, not coalescing
Policy / Revision aggregate validation occurred before child exposure
```

If the Batch Plan or any required child Plan no longer satisfies its frozen integrity/state contract, Apply fails closed before unsafe continuation.

## 3. Product Surface

Add one high-level Tool:

```text
ue_apply_live_write_batch(batch_plan_id, confirmation, change_set_id)
```

Exact argument naming may follow existing MCP conventions, but the public contract must bind:

```text
exact batchPlanId
exact confirmation phrase
one existing Change Set identity
```

Expected confirmation:

```text
APPLY LIVE WRITE BATCH <batchPlanId>
```

W4-2 supports only:

```text
assetCount = 1
operationCount = 1..8
```

A W4-1 Batch Plan containing more than one asset must fail with a clear W4-2 scope error. Multi-asset execution belongs to W4-3.

Existing low-level Tools remain unchanged.

## 4. Architecture

Prefer keeping W4 execution state in the W4 domain module rather than expanding `agent_workflow.py` into a batch state machine.

Likely structure:

```text
src/ue_agent_kit/bounded_batch.py
  BatchExecution record / persistence
  Batch Plan integrity + state loading
  ordered execution helpers
  partial-boundary derivation

src/ue_agent_kit/agent_workflow.py
  narrow reusable single-op orchestration entry points only where needed

src/ue_agent_kit/mcp_workflow_tools.py
  ue_apply_live_write_batch registration

src/ue_agent_kit/tool_registry.py
  ToolDefinition
```

Do not add a generic C++ batch mutation endpoint unless real UE evidence proves the existing single-operation path cannot satisfy W4-2. That condition is a stop-and-review point, not an automatic implementation choice.

## 5. Durable Batch Execution Record

W4-2 needs durable execution evidence because a partial Apply must remain truthful after the MCP call returns and must be usable by later W4 recovery work.

Recommended persisted record:

```text
LiveWriteBatchExecutionRecord
```

Recommended identity/path:

```text
batchExecutionId = lwbe_<token>
Output/<WorkRoot>/batch-executions/lwbe_*.json
```

Minimum record content:

```text
schemaVersion
batchExecutionId
batchPlanId
batchPlanDigest
changeSetId
state
assetPath
editorSessionId
startedAtUtc
updatedAtUtc
completedAtUtc
operations[]
  sequenceIndex
  batchOperationId
  childPlanId
  operation
  stableTargetKey
  state
  liveApplyReceipt
  transactionId
  previousTransactionId
  fastVerifyResult / exact verify evidence reference
  failure
lastSuccessfulOperation
failedOperation
notStarted[]
recoveryOrder[]
```

Do not create a competing operation lifecycle. Per-operation status should reference/reuse existing Change Set receipt semantics wherever possible.

Persist state at safety-relevant boundaries so an already-applied mutation cannot be represented later as merely `planned` because the outer MCP call failed.

## 6. State Contract

W4-2 needs only the states required by this phase:

```text
planned
applying
applied
partially_applied
failed
```

Interpretation:

```text
failed
  = no operation mutation successfully completed

partially_applied
  = at least one operation completed, but the full ordered sequence did not

applied
  = every operation applied and Fast Verified
```

Do not use generic `failed` after resident state has already changed.

Later W4 phases may extend the same record/state model for recovery/checkpoint/save, but W4-2 must not prematurely claim those states.

## 7. Exact Execution Algorithm

### 7.1 Preflight before first mutation

Before consuming child Plan 1:

```text
1. load Batch Plan through its tamper-checking path
2. verify state is executable and not already consumed/incompatible
3. verify exact batch confirmation
4. require assetCount == 1
5. bind exact Change Set
6. ensure all child Plans exist and remain unconsumed
7. verify child Plan identities/order match immutable Batch Plan
8. establish current Editor session through existing live-write path
9. create/persist Batch Execution record as applying
```

Do not mutate any UObject if preflight fails.

### 7.2 Per-operation sequence

For operation `i` in immutable request order:

```text
child Plan i
→ existing single-operation resident Apply
→ exact receipt / transaction binding
→ existing Fast Resident Verify
→ persist successful operation evidence
→ continue to i+1
```

For op1:

```text
previousTransactionId = none
```

For op2+ on the same asset:

```text
previousTransactionId = exact transactionId from immediately previous successful W4 operation
```

Never search for or infer a transaction to continue from.

### 7.3 Fast Verify gate

A write is not considered a successful W4-2 sequence step until its existing Fast Resident Verify passes with exact:

```text
asset
live apply receipt
Editor session
Change Set
transaction binding
current canonical value
```

If Apply succeeds but Fast Verify fails, resident mutation already occurred. The result must therefore preserve the exact changed-state boundary and fail closed; it must not continue to the next operation.

## 8. Child Plan Consumption and Replay Safety

W4-1 child Plans are one-time existing Plan identities. W4-2 must preserve that contract.

Required behavior:

```text
consume children strictly in Batch Plan sequence
never skip a failed child and continue
never recreate a child Plan silently
never substitute a different Plan with equivalent payload
never accept a tampered Batch Plan to reorder children
```

Repeated call behavior must be explicit and fail closed. A previously executed Batch Plan must not replay mutations merely because the same confirmation is supplied again.

If an execution record already exists for the Batch Plan, return/raise a deterministic already-started/already-consumed boundary rather than starting a second execution.

## 9. Failure Semantics

### F0 — failure before first successful mutation

Examples:

```text
bad confirmation
Batch Plan tamper
multi-asset Batch Plan
missing/consumed child Plan
stale/non-executable child state
Editor/session preflight rejection
op1 Apply rejection before mutation
```

Required result:

```text
state = failed
appliedCount = 0
failedOperation = op1 when applicable
notStarted = remaining operations
no Save
```

### F1 — later Apply failure

Example:

```text
op1 PASS + Fast Verify PASS
op2 Apply FAIL
op3 not started
```

Required durable boundary:

```text
state = partially_applied
lastSuccessfulOperation = op1
failedOperation = op2
notStarted = [op3]
recoveryOrder = [op1]
```

### F2 — Fast Verify failure after an Apply

Example:

```text
op1 PASS + Fast Verify PASS
op2 Apply PASS
op2 Fast Verify FAIL
op3 not started
```

The record must distinguish this from an Apply rejection. The op2 mutation/receipt/transaction evidence must be retained if available, and recovery ordering must include every transaction known to have mutated resident state.

Do not claim op2 verified. Do not start op3.

### F3 — persistence failure after resident mutation

If durable batch-state persistence fails after a mutation has occurred, stop immediately and return a fail-closed/manual-recovery boundary containing all in-memory exact receipts that can truthfully be reported. Never continue applying additional operations without durable sequence evidence.

This is a stop condition if the implementation cannot make the boundary deterministic.

## 10. Partial Boundary and Recovery Metadata

W4-2 does not implement aggregate recovery execution, but it must persist enough exact information for W4-3/W4-6 recovery.

For successful resident mutations, recovery order is global reverse execution order:

```text
op3
op2
op1
```

For an op2 failure after op1 success:

```text
recoveryOrder = [op1]
```

For op2 Apply success followed by Fast Verify failure:

```text
recoveryOrder includes op2 then op1
```

Each recovery entry must carry exact existing evidence, especially:

```text
assetPath
transactionId
editorSessionId
liveApplyReceipt
batchOperationId
sequenceIndex
```

Do not attempt to skip unrelated Editor transactions. Later recovery must use existing exact Undo/Discard safety.

## 11. Same-target Supersession

W4-2 must not optimize away repeated writes to the same stable target.

Example:

```text
op1 target X = 10
op2 target X = 20
```

Both operations execute and remain audit-visible.

Expected final semantics:

```text
op1 applied then superseded
op2 effective
```

Use existing W3/Change Set supersession behavior as authority. W4 execution metadata may reference the effective/superseded result but must not invent a second supersession model.

Tests must prove stable target identity parity with W4-1/W3.

## 12. No-save Boundary

W4-2 must perform zero package saves.

After successful C1 the Blueprint is expected to remain resident Dirty under the exact W3 transaction chain.

Do not call:

```text
ue_save_authorized_asset
checkpoint Save
Strong Verify
snapshot refresh
```

as part of W4-2 Apply.

Fixture recovery after acceptance is a test cleanup action, not W4-2 product Save behavior.

## 13. Unit / Contract Test Matrix

Minimum deterministic coverage:

```text
A1  one asset / one op compatibility
A2  one BP / three ordered ops success
A3  child Plans consumed in immutable sequence
A4  op2/op3 exact previousTransactionId chain
A5  Fast Verify occurs after each Apply and before next Apply
A6  successful execution state = applied

B1  bad confirmation → zero mutation
B2  Batch Plan tamper → zero mutation
B3  child Plan tamper/missing/consumed → fail closed
B4  multi-asset Batch Plan → W4-2 scope reject
B5  repeated Apply/replay attempt → reject

C1  injected op1 failure → failed, zero successful operations
C2  injected op2 Apply failure → partially_applied, op3 not started
C3  injected op2 Fast Verify failure → partially_applied, op3 not started
C4  exact lastSuccessful / failedOperation / notStarted fields
C5  exact reverse recovery metadata
C6  persistence failure after mutation stops further execution

D1  Change Set contains/receives every successful operation correctly
D2  same-target repeated writes retained
D3  supersession remains W3-correct
D4  stableTargetKey parity with W4-1
D5  existing ue_apply_asset_property_live behavior unchanged
D6  existing W1-W3 tests unchanged
D7  zero Save / Strong Verify / child Unreal during Apply
```

Prefer fault injection at the Python orchestration boundary for partial-state tests. Do not add production unsafe switches solely for tests.

## 14. Real UE5.6 Acceptance C1

Primary acceptance fixture:

```text
BP_TransactionBlueprint
```

Operations in exact order:

```text
1. setVariableDefault
2. setComponentProperty
3. setPinDefault
```

Acceptance must prove:

```text
Batch Plan integrity                         PASS
exact confirmation                          PASS
same Editor session across all three ops    PASS
op1 Apply + Fast Verify                     PASS
op2 exact previousTransactionId             PASS
op2 Apply + Fast Verify                     PASS
op3 exact previousTransactionId             PASS
op3 Apply + Fast Verify                     PASS
Change Set evidence contains all 3 ops      PASS
batch execution state = applied             PASS
package Save count                          0
Strong Verify child Unreal                  0
```

Capture exact transaction chain:

```text
tx1
→ previousTransactionId(tx2) = tx1
→ previousTransactionId(tx3) = tx2
```

After evidence capture, recover/reset the fixture using the established deterministic fixture procedure and independently confirm exact baseline restoration.

W4-3 is blocked until C1 passes and fixture recovery is exact.

## 15. Controlled Failure Evidence

In addition to happy-path C1, W4-2 must have deterministic evidence for the partial boundary before closure.

Minimum required injected case:

```text
op1 PASS
op2 FAIL
op3 NOT STARTED
```

Evidence must show:

```text
state = partially_applied
lastSuccessful = op1
failedOperation = op2
notStarted = [op3]
recoveryOrder = reverse successful execution order
no Save
```

This may be deterministic orchestration-level evidence if forcing a real UE op2 failure would require weakening or contaminating the production contract. Real UE C1 remains mandatory for the actual three-op resident chain.

## 16. Regression / Build Gates

Run after implementation:

```text
Ruff
Python full discovered suite
compileall
ValidateRelease 0.7.0
git diff --check
```

Record the actual discovered test count; do not freeze `729` as a future constant.

If no C++ changes are made, a new Direct Build is not required solely for W4-2 implementation. Real UE C1 is still mandatory because resident behavior is exercised.

If C++ is changed:

```text
check no conflicting UE/build process
run UE5.6 Direct Build
```

## 17. Expected Public Result Shape

Keep the Agent-facing result compact while retaining exact evidence identifiers.

Recommended success fields:

```text
batchExecutionId
batchPlanId
changeSetId
state = applied
assetPath
operationCount
appliedCount
operations[]
  batchOperationId
  sequenceIndex
  operation
  state
  transactionId
  liveApplyReceipt
  fastVerified
lastTransactionId
savePerformed = false
nextActions
```

Partial result additionally includes:

```text
lastSuccessfulOperation
failedOperation
notStarted[]
recoveryOrder[]
```

Avoid returning duplicate full child Plan payloads when IDs and compact evidence are sufficient. W4 is intended to reduce public orchestration/result overhead.

## 18. Stop Conditions

Stop and diagnose before continuing if:

```text
W4-2 requires weakening Dirty-package fail-closed behavior
exact previousTransactionId cannot be preserved
Fast Verify must be skipped to make sequencing work
Batch Plan/child Plan tamper cannot be detected before unsafe use
partial mutation cannot be durably distinguished from zero-mutation failure
implementation needs a generic arbitrary C++ batch writer
same-target supersession regresses
existing single-operation W1-W3 behavior regresses
unrelated Editor transactions would need to be skipped for recovery
fixture cannot be restored exactly
```

Do not solve these by broadening Writer authority.

## 19. Implementation Order

Recommended small slices:

```text
1. BatchExecution schema / immutable identity / durable persistence
2. W4-2 preflight + one-asset scope + replay guard
3. ordered child Plan consumption using existing single-op Apply
4. exact previousTransactionId chaining
5. Fast Verify gate after every operation
6. partial-boundary persistence / recovery metadata
7. MCP + Tool Registry surface
8. unit / fault-injection tests
9. full Python/release gates
10. real UE C1
11. deterministic fixture recovery + independent baseline confirmation
12. W4-2 Result document
```

Do not combine W4-3 multi-asset behavior into these slices.

## 20. Exit Gate

W4-2 is complete only when:

```text
[ ] ue_apply_live_write_batch exists for one-asset Batch Plans
[ ] immutable W4-1 plan + child identities are enforced
[ ] exact confirmation is enforced
[ ] operations execute strictly in request order
[ ] op2+ use exact previousTransactionId chain
[ ] every successful Apply is Fast Verified before the next operation
[ ] exact durable applied / partially_applied boundary exists
[ ] replay / stale / tamper cases fail closed
[ ] same-target supersession remains correct and audit-visible
[ ] Change Set evidence is correct
[ ] zero package Save occurs
[ ] zero Strong Verify occurs
[ ] existing W1-W3 APIs remain compatible
[ ] deterministic op2-failure evidence passes
[ ] real UE5.6 C1 passes
[ ] fixture is exactly recovered
[ ] Ruff / Python / compileall / ValidateRelease / git diff --check pass
[ ] UE5.6 Direct Build passes if C++ changed
```

Only after this Exit Gate is green may W4-3 Multi-Asset Resident Apply begin.

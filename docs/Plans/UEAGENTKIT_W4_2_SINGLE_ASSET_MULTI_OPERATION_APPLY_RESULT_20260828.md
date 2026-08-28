# UEAgentKit W4-2 Single-Asset Multi-operation Apply Result

> Date: 2026-08-28
>
> Branch: `feature/live-writer-expansion`
>
> Entry implementation checkpoint: `71400c9` (`feat: add W4-1 bounded batch planning`)
>
> Execution plan: `UEAGENTKIT_W4_2_SINGLE_ASSET_MULTI_OPERATION_APPLY_DETAILED_PLAN_20260828.md`
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`

## 1. Final Status

```text
W4-2 Single-Asset Multi-operation Apply = complete
```

W4-2 added:

```text
ue_apply_live_write_batch(batch_plan_id, confirmation, change_set_id)
```

It executes one immutable W4-1 one-asset Batch Plan through the existing
resident single-operation live writer, Fast Verifies every successful write
before the next operation, and persists an exact durable applied /
partially_applied boundary.

No Save, Strong Verify, checkpoint, or multi-asset behavior was added.

## 2. Product / Module Changes

```text
src/ue_agent_kit/bounded_batch.py
  + LiveWriteBatchExecutionRecord
  + durable batch-executions/lwbe_*.json persistence
  + apply_live_write_batch()
  + exact partial boundary / recovery metadata
  + replay guard / tamper checks

src/ue_agent_kit/agent_workflow.py
  + assert_plan_available_for_batch()

src/ue_agent_kit/mcp_workflow_tools.py
  + ue_apply_live_write_batch registration

src/ue_agent_kit/tool_registry.py
  + ToolDefinition (destructive)

tests/python/test_bounded_batch.py
  + W4-2 apply contract / fault-injection tests

tests/integration/mcp_w4_bounded_batch_apply_smoke.py
  + real UE C1 evidence capture
```

No C++ file changed.

## 3. Real UE5.6 Acceptance C1

Primary fixture:

```text
/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
```

Sequence:

```text
1. setVariableDefault   TransactionInt = 42
2. setComponentProperty DefaultSceneRoot.RelativeLocation.X = 10
3. setPinDefault        EventGraph A = 7
```

Result:

```text
Batch Plan integrity                     PASS
exact confirmation                       PASS
same Editor session across all three     PASS
state                                    applied
operationCount                           3
appliedCount                             3
fastVerified                             3
Change Set operationCount                3
package Save count                       0
Strong Verify child Unreal               0
```

Exact transaction chain:

```text
op1 transactionId = 9B5B5471-426E-1700-9E62-FB8D6E74A5C5
op2 previousTransactionId = 9B5B5471-426E-1700-9E62-FB8D6E74A5C5
op2 transactionId = 2A910553-449C-515D-B8E3-F6B7B6AEE41D
op3 previousTransactionId = 2A910553-449C-515D-B8E3-F6B7B6AEE41D
op3 transactionId = 6F03B071-4014-E9A9-6248-FDAD8E97B1B6
```

Evidence file:

```text
Output/W4Acceptance/w4-c1-apply-report.json
Output/W3Acceptance/Workflow/batch-executions/lwbe_K6R4vLma8H4scv2oh3XZfkgR/execution.json
```

## 4. Durable Execution Record

Persisted under:

```text
Output/<WorkRoot>/batch-executions/lwbe_*.json
```

Record contents include:

```text
batchExecutionId
batchPlanId / batchPlanDigest
changeSetId
state (applying / applied / partially_applied / failed)
assetPath
operations[]
  sequenceIndex / batchOperationId / childPlanId
  operation / stableTargetKey
  state / liveApplyReceipt / transactionId / previousTransactionId
  fastVerifyResult / failure
lastSuccessfulOperation
failedOperation
notStarted[]
recoveryOrder[]
```

The record is persisted:

```text
before first mutation (state=applying)
after each successful Apply + Fast Verify
after each failure boundary
after final applied state
```

## 5. Unit / Contract Coverage

New/updated suite: `tests/python/test_bounded_batch.py`

```text
apply success 3-op chain                      PASS
exact previousTransactionId chain             PASS
Fast Verify after every Apply                 PASS
same-target repeated writes retained          PASS
bad confirmation -> zero mutation             PASS
multi-asset scope reject                      PASS
replay / already-started reject               PASS
op2 Apply failure -> partially_applied        PASS
op2 Fast Verify failure -> partially_applied  PASS
exact recoveryOrder metadata                  PASS
tampered Batch Plan -> zero mutation          PASS
persistence failure stops further Apply       PASS
```

Change Set evidence in real C1 contained all 3 operations and no Save occurred.

## 6. Controlled Failure Evidence

The required deterministic op2 failure evidence is covered at Python
orchestration boundary:

```text
op1 PASS + Fast Verify PASS
op2 Apply FAIL
op3 NOT STARTED

state                 partially_applied
lastSuccessful        bop_0001
failedOperation       bop_0002
notStarted            [bop_0003]
recoveryOrder         [bop_0001]
```

Fast Verify failure case:

```text
op1 PASS
op2 Apply PASS, Fast Verify FAIL
op3 NOT STARTED

state                 partially_applied
lastSuccessful        bop_0001
failedOperation       bop_0002
notStarted            [bop_0003]
recoveryOrder         [bop_0002, bop_0001]
```

## 7. Fixture Recovery

After real C1 evidence capture, the editor was stopped and the deterministic
fixture plan ran in `Reset` mode:

```text
Output/W4Acceptance/ResetAfterC1/fixture-report.json
mode                  Reset
deletedCount          2
createdCount          2
verified              true
verifiedCount         2
DA revision           sha256:40a293988ef90f491d41ab8c77551eac84046d2eebb302704f631414f0c95dd8
BP revision           sha256:543ba99016a713da1666e02992a519083ec5f8c1d7fa3f506cd83da9f8980f36
```

## 8. Regression / Release Gates

```text
Python discovered suite   738 / 738 PASS (final after all W4-2 tests)
Ruff                      PASS
compileall                PASS
ValidateRelease 0.7.0     PASS
git diff --check          PASS
UE5.6 Direct Build        not required (no C++ change)
```

## 9. Scope Boundary

W4-2 did not implement:

```text
multi-asset Apply
Save
checkpoint-set
aggregate Strong Verify
recovery execution
restart recovery of Batch Executions
```

Those belong to W4-3, W4-4, W4-5, W4-6.

## 10. Next Step

W4-3 may begin once C1 and fixture recovery are known green:

```text
W4-3 Multi-Asset Resident Apply
  -> BP 3 ops + DA 1 op
  -> later-asset failure partial boundary
  -> resident-only partial recovery to exact baseline
```
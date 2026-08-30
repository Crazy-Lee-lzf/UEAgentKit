# UEAgentKit W4-1 Bounded Batch Plan Result

> Date: 2026-08-28
>
> Branch: `feature/live-writer-expansion`
>
> Entry checkpoint: `90f6a11` (`docs: close W4-0 contract freeze and baseline`)
>
> Execution plan: `UEAGENTKIT_W4_1_BOUNDED_BATCH_PLAN_DETAILED_PLAN_20260828.md`
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`

## 1. Final Status

```text
W4-1 Bounded Batch Plan = complete
```

W4-1 added one read-only Agent-facing planning Tool:

```text
ue_plan_live_write_batch
```

It creates one immutable Batch Plan from an asset-grouped request, reuses the
existing single-operation `plan_patch()` child Plans, enforces W4 hard and Policy
effective bounds, and exposes no mutation, save, Strong Verify, or C++ behavior.

## 2. Product Surface Added

```text
ue_plan_live_write_batch(assets, description="")
```

Success result:

```text
batchPlanId            lwbp_<token>
batchPlanDigest        sha256 integrity digest
requestDigest          deterministic request + bound Class/Revision digest
state                  planned
assetCount / operationCount
assets[]               child Plan IDs + stable target keys + supersession preview
bounds                 hard / policy / effective
confirmationRequired   APPLY LIVE WRITE BATCH <batchPlanId>
```

The Tool is registered as workflow/planning in `tool_registry.py` and is
available through `mcp_workflow_tools.py`. Existing `ue_plan_patch` remains
unchanged.

## 3. Implementation Files

```text
src/ue_agent_kit/bounded_batch.py                  NEW module
src/ue_agent_kit/agent_workflow.py                 narrow additions:
                                                     - public live_write_stable_target_key alias
                                                     - bind_asset_for_batch()
src/ue_agent_kit/mcp_workflow_tools.py             register ue_plan_live_write_batch
src/ue_agent_kit/tool_registry.py                  add ToolDefinition
src/ue_agent_kit/mcp_server.py                     minimal server guidance
tests/python/test_bounded_batch.py                 NEW 17-test contract suite
tests/integration/mcp_w4_bounded_batch_plan_smoke.py  NEW real fixed-project S1/S2 smoke
```

No C++ file changed.

## 4. Contract Behavior Confirmed

```text
W4 allowlist            setAssetProperty, setVariableDefault,
                        setComponentProperty, setPinDefault
hard bounds             4 assets / 8 ops per asset / 16 total / 64 KiB
effective limits        min(W4 hard, Policy maxAssetsPerPatch,
                            Policy maxOperationsPerAsset)
asset order             request order
operation order         request order inside each asset
duplicate asset group   live-write-batch-duplicate-asset
same-target repeats     retained, expected supersession metadata provided
child Plan exposure     all-or-nothing; cleanup uses discard_unconsumed_plans
aggregate validation    before any child Plan is created
requestDigest           deterministic for request + asset Class/Revision
batchPlanDigest         integrity digest over persisted payload
tamper check            get() re-reads file and verifies digest + payload
Zero side effects       no Editor Bridge, no Unreal child, no Save, no Change Set
```

The existing single-patch validator rejects duplicate transaction targets; W4
Batch Plans intentionally allow same-target repeated writes. The aggregate
validation in `bounded_batch.py` therefore ignores only
`duplicate-transaction-target` errors while preserving every other Policy,
Revision, operation, and asset validation failure as a hard rejection.

## 5. Unit / Contract Coverage

New suite: `tests/python/test_bounded_batch.py` (17 tests)

```text
happy path B1 ordering                         PASS
requestDigest deterministic + revision change  PASS
duplicate asset rejected                       PASS
hard asset/op/total bounds                     PASS
64 KiB request size                            PASS
4 / 8 / 16 boundary accepted with W4 Policy    PASS
Policy maxAssets / maxOps effective            PASS
unsupported / unknown operation                PASS
malformed target                               PASS
stale / missing asset binding                  PASS
child failure cleanup / no partial plan        PASS
no batch directory after validation failure    PASS
supersession preview metadata                  PASS
immutable plan tamper / not found              PASS
```

Tool Registry / MCP server order and counts updated:

```text
workflow-only                 63
workflow + memory             75
combined live + workflow      96
combined live + workflow + memory  108
```

## 6. Real Fixed-project Smoke S1 / S2

Command:

```text
tests/integration/mcp_w4_bounded_batch_plan_smoke.py
--policy tests/fixtures/w4_bounded_batch_acceptance_policy.json
```

MCP server started with write/commit tools and **no** Live Editor mode. The
frozen snapshot was refreshed offline after fixture recovery before the smoke.

### S1 — B0 planning payload

```text
1 Blueprint / 3 operations
assetCount       = 1
operationCount   = 3
sequenceIndex    = 0,1,2
state            = planned
childPlans       = 3 existing plan_* identities
confirmation     = APPLY LIVE WRITE BATCH lwbp_...
liveEditorEnabled = false
Bridge calls     = 0
Unreal processes = 0
```

### S2 — B1 planning payload

```text
Blueprint 3 operations + Data Asset 1 operation
assetCount       = 2
operationCount   = 4
asset order      = [BP_TransactionBlueprint, DA_TransactionAsset]
sequenceIndex    = 0,1,2,3
childPlans       = 4 existing plan_* identities
state            = planned
Bridge calls     = 0
Unreal processes = 0
```

Smoke report:

```text
Output/W4Acceptance/w4-plan-smoke-report.json
```

## 7. Regression / Release Gates

```text
Python discovered suite   729 / 729 PASS
Ruff                      PASS
compileall                PASS
ValidateRelease 0.7.0     PASS
git diff --check          PASS
UE5.6 Direct Build        not required (no C++ change)
```

## 8. Scope Boundary

W4-1 did not implement:

```text
ue_apply_live_write_batch
Fast Verify orchestration
Change Set mutation
multi-asset Save
checkpoint-set persistence
Strong Verify aggregation
rollback/recovery execution
restart recovery of Batch Plans
```

Those remain in W4-2 and later phases.

## 9. Next Step

W4-2 may begin from this immutable Batch Plan surface:

```text
ue_apply_live_write_batch
  -> consume child Plan IDs in exact sequence
  -> exact previousTransactionId continuation chain
  -> Fast Verify after each write
  -> precise partial-applied boundary on failure
```
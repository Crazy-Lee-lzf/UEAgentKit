# UEAgentKit W4 Multi-operation / Bounded Batch UX Detailed Plan

> Date: 2026-08-26
>
> Branch: `feature/live-writer-expansion`
>
> Entry baseline: W3 closure checkpoint `45e6ea2` on `feature/live-writer-expansion` (preceded by `3280102` fix + `ab731f1` tests); W3 result is recorded in `UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_RESULT_20260825.md`.
>
> Scope: improve multi-operation and multi-asset Writer UX without widening the mutation surface into arbitrary generic batch execution.

## 1. W4 Goal

W4 should turn the already proven W1-W3 primitives into one bounded task-oriented workflow:

```text
one Change Set
→ plan several compatible narrow writes
→ apply them in the resident Editor
→ Fast Verify each write
→ explicit partial-applied boundary on failure
→ checkpoint all touched assets
→ save each package with existing W3 authorization
→ strong verify all effective persisted values
→ Semantic Diff / Verification Plan / Trust
→ exact recovery when required
```

The important change is orchestration and UX, not a new low-level writer.

W4 is complete only when an Agent can perform a small multi-operation task without manually managing one Plan/Apply/Save/Verify call per operation while all W1-W3 safety properties remain intact.

## 2. Current Baseline and Actual Gap

The repository already has most required primitives:

```text
Patch schema                         supports multiple assets and operations
Policy maxAssetsPerPatch             1..100
Policy maxOperationsPerAsset         1..32
Change Set                           durable, multi-operation, multi-asset capable
Change Set journal hard bound        100 receipts
Live Write                           exact single asset + single operation
Same-asset continuation              exact previousTransactionId chain
Fast Resident Verify                 complete
Checkpoint Save                      complete, one asset per checkpoint
Checkpoint Strong Verify             complete, one asset per checkpoint
Supersession                         complete
Semantic Diff / Trust                complete
```

The main Workflow/MCP limitation is currently deliberate:

```text
ue_plan_patch
  = exactly 1 asset + 1 operation

ue_apply_asset_property_live
  = rejects plans that are not exactly 1 asset + 1 operation

W3 checkpoint
  = exactly 1 asset/package
```

Therefore W4 should primarily add a bounded orchestration layer above these proven primitives.

## 3. Non-goals

W4 must NOT become a generic mutation framework.

Explicitly deferred:

```text
Generic Blueprint Graph CRUD
arbitrary node creation/deletion/rewiring
arbitrary UObject/property batch reflection writer
unbounded asset lists
100-operation Agent-facing batch requests
cross-package atomicity claims
automatic save without explicit authorization
resident Editor self-verification as independent Trust
R5 Value Provenance / Execution Trace
source-control collaboration semantics
```

W4 should initially support only operations already proven through the resident Writer + checkpoint path:

```text
setAssetProperty
setVariableDefault
setComponentProperty
setPinDefault
```

Additional live-capable Material/DataTable operations can be considered later only after this bounded contract is proven.

## 4. Initial Bounds

Do not expose the Change Set journal limit of 100 as the normal batch UX limit.

Recommended first W4 bounds:

```text
max assets per bounded batch          4
max operations per asset              8
max total operations                 16
max individual value bytes           existing Policy maxValueBytes
max total serialized request bytes    64 KiB
```

Effective limits must always be the minimum of:

```text
W4 hard bound
Policy maxAssetsPerPatch
Policy maxOperationsPerAsset
Policy maxValueBytes
Change Set remaining capacity
```

These values are intentionally conservative. Raise them only with W5 real-project evidence.

## 5. Request Shape and Ordering Contract

Prefer an asset-grouped request rather than a flat arbitrary global operation list:

```json
{
  "assets": [
    {
      "assetPath": "/Game/.../BP_A.BP_A",
      "operations": [
        {"operation": "setVariableDefault", "target": {...}, "value": 42},
        {"operation": "setComponentProperty", "target": {...}, "value": 10}
      ]
    },
    {
      "assetPath": "/Game/.../DA_B.DA_B",
      "operations": [
        {"operation": "setAssetProperty", "target": {...}, "value": 7}
      ]
    }
  ]
}
```

Ordering rules:

1. assets execute in request order;
2. operations inside one asset execute in request order;
3. the same asset must appear only once in one batch;
4. no cross-asset ordering dependency is implied or supported;
5. same-target repeated writes are allowed and remain audit-visible;
6. W3 supersession decides the final effective persisted write; W4 must not silently coalesce away requested intermediate writes during Apply.

This avoids ambiguous `A1 → B1 → A2` transaction chains and keeps recovery ordering deterministic.

## 6. Proposed Product Surface

Exact public names can be adjusted during Tool Registry review, but keep the surface narrow.

Preferred four high-level tools:

```text
ue_plan_live_write_batch
ue_apply_live_write_batch
ue_save_change_set_checkpoint
ue_verify_change_set_checkpoint
```

Existing low-level tools remain available and unchanged.

### 6.1 `ue_plan_live_write_batch`

Read/plan only. No UObject mutation and no package save.

Responsibilities:

- validate hard bounds;
- resolve every asset from the frozen SQLite snapshot;
- bind exact asset Class and Revision for every asset;
- validate every operation with existing Policy/Operation Registry logic;
- validate stable target identity;
- detect duplicate/same-target writes and report expected supersession;
- produce deterministic per-asset child plan data;
- return exact risk summary and confirmation phrase;
- create no partially exposed child plans if validation fails midway.

Preferred result fields:

```text
batchPlanId
assetCount
operationCount
assets[]
  assetPath
  assetClass
  expectedRevision
  operations[]
    batchOperationId
    operation
    target
    value
    risk
    stableTargetKey
    expectedToSupersede / expectedToBeSuperseded
commitAllowedByPolicy
bounds
confirmationRequired
```

### 6.2 `ue_apply_live_write_batch`

Mutation occurs only after exact confirmation.

Internally reuse the existing single-operation live-write path rather than adding a new generic C++ batch endpoint.

For each asset group:

```text
operation 1
→ existing resident apply
→ Fast Verify
→ operation 2 with exact previousTransactionId
→ Fast Verify
→ ...
```

The existing Editor Bridge transaction-chain safety remains authoritative.

Do not make one giant FScopedTransaction across multiple packages.

### 6.3 `ue_save_change_set_checkpoint`

High-level multi-asset checkpoint orchestration.

Do not replace W3 checkpoint records. Create one W3 checkpoint per touched asset and aggregate them into a checkpoint set.

Recommended durable aggregate:

```text
ChangeSetCheckpointSetRecord
```

Persist under:

```text
Output/<WorkRoot>/checkpoint-sets/cps_*.json
```

Contains:

```text
checkpointSetId
changeSetId
state
assetOrder[]
childCheckpoints[]
  assetPath
  checkpointId
  state
  beforeRevision
  afterRevision
  saveReceipt
preparedAtUtc
savedAtUtc
verifiedAtUtc
failureBoundary
```

### 6.4 `ue_verify_change_set_checkpoint`

Aggregates W3 Strong Verify results.

Initial implementation may run one independent strong export per effective asset. Do not delay W4 to optimize process count across assets.

Expected cost contract:

```text
Strong Verify child Unreal count <= effective asset count
```

W5 can later optimize multi-asset export if real evidence shows this is worthwhile.

## 7. State Model

### 7.1 Batch execution state

Recommended durable batch states:

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

Do not claim `failed` alone when some mutations already occurred; use `partially_applied` or `partially_saved` with exact boundaries.

### 7.2 Per-operation state

Reuse Change Set operation states whenever possible:

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

Batch-specific metadata should reference these records, not create a second competing operation lifecycle.

## 8. Failure Semantics

This is the core W4 contract.

### 8.1 Failure before first mutation

```text
result = failed
appliedCount = 0
package state unchanged
no recovery required
```

### 8.2 Failure during resident Apply

Example:

```text
A.op1 PASS
A.op2 PASS
B.op1 FAIL
B.op2 not started
```

Result must say:

```text
state = partially_applied
lastSuccessfulOperation = A.op2
failedOperation = B.op1
notStarted = [B.op2]
```

No automatic Save.

Return exact recovery actions in reverse execution order.

### 8.3 Failure during Save

Cross-package save is NOT atomic.

Before saving the first package, perform a complete preflight of every touched asset:

- same Editor session;
- expected dirty/saved state;
- exact Change Set membership;
- checkpoint effective set still matches;
- current disk Revision still matches expected pre-save Revision;
- backup destination valid;
- all assets are save-authorized.

Then save sequentially.

If save fails after one or more assets persisted:

```text
state = partially_saved
persistedAssets = [...]
pendingDirtyAssets = [...]
failedAsset = ...
```

Never claim task-level atomicity.

### 8.4 Failure during Strong Verify

If one child checkpoint fails verification:

```text
checkpoint set != verified
Change Set != verified
successful child checkpoints retain their own verified evidence
aggregate Trust remains not-verified
```

Do not downgrade truthful child evidence, but do not produce an aggregate Verified claim.

## 9. Recovery Contract

W4 needs deterministic recovery, but should not automatically trigger recovery after every failure.

### Unsaved / resident-only partial apply

Recover exact successful transactions in global reverse execution order using existing Undo/Discard primitives.

The batch record must persist the exact execution sequence:

```text
receipt
assetPath
transactionId
editorSessionId
batchOperationId
sequenceIndex
```

If the Editor transaction stack no longer matches, fail closed and report manual recovery boundary; never skip over an unrelated user transaction.

### Partially saved batch

Recovery order:

1. rollback saved assets in reverse save order through existing authorized-save rollback manifests;
2. undo/discard remaining unsaved transactions in reverse execution order;
3. independently verify every disk rollback performed;
4. report exact recovered and unrecovered assets.

Recommended W4 recovery UX can be either:

```text
ue_recover_live_write_batch
```

or a structured `nextActions` sequence over existing rollback/undo tools.

Start with structured existing-tool actions; add an aggregate recovery tool only if acceptance shows Agent reliability suffers from manual orchestration.

## 10. Implementation Phases

## W4-0 — Contract Freeze and Baseline

Dedicated execution plan: [`UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_PLAN_20260827.md`](UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_PLAN_20260827.md).

No product behavior change yet.

Tasks:

1. freeze supported operations and bounds;
2. document no cross-package atomicity;
3. measure current W3 manual-call baseline for:
   - 3 operations on one Blueprint;
   - 2 assets / 4 total operations;
4. record Tool-call count, elapsed time, resident apply count, save count, child Unreal count;
5. freeze current 712/712 Python baseline and W3 real evidence.

Exit gate:

```text
W4 contracts agreed
no C++ implementation change required
baseline recorded
```

## W4-1 — Bounded Batch Plan

Primary files likely:

```text
src/ue_agent_kit/agent_workflow.py
src/ue_agent_kit/change_sets.py or a new bounded_batch.py
src/ue_agent_kit/mcp_server.py / tool registration modules
tests/python/test_agent_workflow.py
tests/python/test_tool_registry.py
```

Prefer creating a dedicated module such as:

```text
bounded_batch.py
```

rather than further growing `agent_workflow.py`.

Required tests:

- 1 asset / 1 op compatibility;
- 1 asset / 3 BP ops;
- 2 assets / 4 ops;
- duplicate asset group rejected;
- unsupported operation rejected;
- hard total-operation bound;
- Policy asset/operation bound;
- stale Revision rejects whole plan;
- one invalid child causes zero exposed child plans;
- deterministic digest and ordering.

Exit gate: batch planning is fully read-only and fail-closed.

## W4-2 — Single-Asset Multi-operation Apply

Implement the smallest real value slice first:

```text
one Blueprint
setVariableDefault
setComponentProperty
setPinDefault
```

Use existing single-operation live apply internally.

Required evidence:

- exact transaction chain for op2/op3;
- Fast Verify after every write;
- Change Set contains all operations;
- same-target supersession remains correct;
- injected op2 failure stops op3;
- failure result reports precise partial-applied boundary;
- no package save occurs.

Real UE acceptance:

```text
C1: BP variable + component + pin batch apply
```

Do not start multi-asset work until C1 passes.

## W4-3 — Multi-Asset Resident Apply

Add up to four asset groups.

First fixture pair:

```text
BP_TransactionBlueprint
DA_TransactionAsset
```

Required cases:

```text
C2: BP 3 ops + Data Asset 1 op → all applied
C3: second asset first op fails → first asset remains accurately partially_applied
C4: recovery of resident-only partial apply → exact baseline
```

No saving yet.

## W4-4 — Multi-Asset Checkpoint Save

Add `ChangeSetCheckpointSetRecord` and high-level checkpoint save orchestration.

Required cases:

```text
C5: 2 assets, all preflight PASS, both saved
C6: preflight failure on asset 2 → zero packages saved
C7: injected save failure after asset 1 → partially_saved with exact boundary
C8: checkpoint set survives MCP restart
```

Important invariant:

```text
preflight is all-assets
save is sequential per package
result never claims cross-package atomicity
```

## W4-5 — Aggregate Strong Verify / Semantic Diff / Trust

For each child checkpoint:

```text
W3 Strong Verify
→ per-asset Semantic Diff evidence
→ aggregate Change Set verification state
→ final Verification Plan / Trust
```

Required cases:

```text
C9: 2 assets all verified → aggregate Trust verified
C10: asset 2 canonical mismatch → aggregate not verified
C11: asset 2 stale Revision → aggregate not verified
C12: same-target supersession inside asset 1 + asset 2 normal write
```

Do not require one Commandlet for the entire batch in W4.

## W4-6 — Recovery and Restart Hardening

Test:

- MCP restart after batch apply but before save;
- Editor restart before save → fail closed if resident transaction identity is gone;
- MCP restart after partial save;
- saved child rollback recovery;
- reverse-order resident recovery;
- user/unrelated Editor transaction inserted before recovery → stack mismatch, no unsafe undo;
- checkpoint-set journal corruption → quarantine/error, no guessed state.

Exit gate: no unknown write can be silently treated as recovered.

## W4-7 — Full Acceptance / Documentation

Run:

```text
Ruff
Python full suite
compileall
git diff --check
UE5.6 Direct Build if C++ changed
real UE W4 C1-C12
fixture exact recovery
process-count / latency measurements
```

Update:

```text
docs/Plans/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260826.md
docs/PROJECT_STATUS.md / ROADMAP only if their current contract requires it
Tool Registry / capabilities counts if new public tools are added
```

## 11. Acceptance Matrix

Minimum real UE matrix:

```text
C1  one BP / 3 compatible operations                    PASS
C2  BP + Data Asset / 4 total operations                PASS
C3  Apply failure on later asset                         partial boundary exact
C4  resident-only partial recovery                       exact baseline
C5  two-asset checkpoint save                            PASS
C6  all-asset preflight catches stale/dirty before save zero persisted
C7  controlled mid-save failure                          partial_saved exact
C8  checkpoint-set reload after MCP restart              PASS
C9  aggregate Strong Verify                              Trust verified
C10 one canonical mismatch                               fail closed
C11 one disk Revision stale                              fail closed
C12 same-target supersession inside multi-asset batch    correct effective set
```

## 12. Performance / UX Measurements

W4 is a UX feature, so acceptance should measure reduction in orchestration overhead.

Compare manual W3 workflow vs W4 batch for the same task:

```text
Agent/MCP public Tool calls
resident Bridge calls
child Unreal processes
elapsed time
serialized result size
token-visible result size
recovery Tool calls on injected failure
```

Expected outcome:

```text
public Tool calls materially lower
resident mutation cost approximately unchanged
Strong Verify cost bounded by effective asset count
no extra cold start during Apply/Save
```

Do not optimize independent multi-asset export until measurements show it is the next dominant cost.

## 13. Recommended Code Structure

Because `agent_workflow.py` is already large, W4 should avoid placing the full state machine there.

Recommended split:

```text
bounded_batch.py
  BatchPlan / BatchExecution records
  bounds / validation
  serialization / journal
  execution ordering helpers

checkpoint_sets.py
  ChangeSetCheckpointSetRecord
  child checkpoint aggregation
  aggregate state derivation

agent_workflow.py
  thin orchestration entry points
  reuse existing single-op and W3 primitives
```

This is not a general M1 refactor. Only extract the new W4 domain so the large feature does not further concentrate logic in `agent_workflow.py`.

## 14. Stop Conditions

Stop implementation and diagnose before continuing if any of these occur:

```text
existing W3 single-operation behavior regresses
same-asset continuation requires weakening dirty-package safety
batch implementation needs arbitrary generic C++ mutation dispatch
cross-package save is accidentally presented as atomic
recovery needs skipping unrelated Editor transactions
Strong Verify is replaced by resident read-back
batch journal cannot reconstruct exact partial state after restart
fixture cannot be exactly recovered
```

A W4 convenience feature is not worth weakening W1-W3 Trust/Recovery guarantees.

## 15. Recommended Execution Order

```text
W4-0 contract + baseline
→ W4-1 bounded batch planning
→ W4-2 single-BP multi-op apply
→ real UE C1
→ W4-3 multi-asset resident apply + recovery
→ real UE C2-C4
→ W4-4 checkpoint set / multi-asset save
→ real UE C5-C8
→ W4-5 aggregate strong verify / Trust
→ real UE C9-C12
→ W4-6 restart/recovery hardening
→ full gates
→ W4 result doc
```

Do not combine all W4 phases into one unreviewable implementation step. Each phase should preserve a runnable and recoverable repository state.

## 16. W4 Definition of Done

W4 can be marked complete only when all of the following are true:

```text
[ ] bounded batch request has explicit hard + Policy bounds
[ ] one BP can apply multiple compatible narrow operations in one high-level workflow
[ ] multiple assets can participate in one Change Set batch
[ ] exact partial-applied boundary is durable and restart-readable
[ ] no cross-package atomicity is claimed
[ ] all-assets save preflight occurs before first package save
[ ] partial-saved state is explicit and recoverable
[ ] each asset uses existing W3 checkpoint safety
[ ] aggregate Strong Verify fails closed if any child fails
[ ] aggregate Trust verified only when all required child evidence is verified
[ ] supersession remains audit-visible and correct
[ ] failure recovery respects exact reverse transaction/save order
[ ] unrelated Editor transactions are never skipped or undone
[ ] existing single-operation W1-W3 APIs remain compatible
[ ] real UE C1-C12 pass
[ ] fixture recovery passes
[ ] Ruff / Python / compileall / git diff --check pass
[ ] UE5.6 Direct Build passes if C++ is touched
```

After this point the next Writer task should be W5 real-project acceptance/performance, not immediate expansion into Generic Blueprint Graph CRUD.

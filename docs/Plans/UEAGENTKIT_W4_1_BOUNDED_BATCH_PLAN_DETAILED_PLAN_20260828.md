# UEAgentKit W4-1 Bounded Batch Plan Detailed Plan

> Date: 2026-08-28
>
> Branch: `feature/live-writer-expansion`
>
> Entry checkpoint: `90f6a11` (`docs: close W4-0 contract freeze and baseline`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> W4-0 result: `UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_RESULT_20260827.md`
>
> Scope: implement the read-only, fail-closed bounded batch planning layer only. No resident mutation, no Save, no Strong Verify, no C++ behavior change.

## 1. Goal

W4-1 adds one Agent-facing planning entry point:

```text
ue_plan_live_write_batch
```

It accepts one bounded, asset-grouped request and produces one immutable Batch Plan that is ready for W4-2 to execute through the existing single-operation resident Writer.

W4-1 is successful when the Agent can replace several independent `ue_plan_patch` calls with one request while preserving all existing Policy, Revision, Operation Registry and Plan tamper guarantees.

The phase is intentionally read-only:

```text
bounded request
→ validate W4 hard bounds
→ bind exact Asset Class + Revision
→ validate aggregate Policy / Revision contract
→ create existing single-op child Plans
→ calculate ordering + stable target identity + expected supersession
→ persist immutable Batch Plan
→ return Batch Plan identity
```

No Editor Bridge call, UObject mutation, package Dirty state, Save, child Unreal process, Change Set operation, or transaction is allowed in W4-1.

## 2. Entry Facts

W4-0 froze the following baseline:

```text
HEAD                                  90f6a11 after W4-0 closeout
W3 product checkpoint                 45e6ea2
Python discovered suite               712 / 712 PASS
W4 supported operations               4
W4 max assets                         4
W4 max operations per asset           8
W4 max total operations               16
W4 max serialized request             64 KiB
cross-package atomicity               NOT claimed
```

W4-0 manual W3 orchestration baseline:

| Metric | B0: 1 BP / 3 ops | B1: 2 assets / 4 ops |
|---|---:|---:|
| public MCP Tool calls | 19 | 27 |
| resident Editor Bridge calls | 42 | 63 |
| resident apply count | 3 | 4 |
| Fast Verify count | 3 | 4 |
| Strong Verify child Unreal | 1 | 2 |
| public result bytes | 54,120 | 79,191 |
| wall elapsed ms | 12,339.826 | 22,861.093 |
| Semantic Diff | verified | verified |
| Trust | verified | verified |

W4-1 does not attempt to improve the resident or verification costs yet. Its measurable UX improvement is planning-call consolidation and a single deterministic task description.

## 3. Non-goals

W4-1 must not implement or imply any of the following:

```text
resident Apply
Fast Verify
Change Set mutation lifecycle
multi-asset Save
checkpoint-set persistence
Strong Verify aggregation
rollback / recovery execution
new C++ Editor Bridge endpoint
new generic UObject writer
new Operation family
Material/DataTable expansion into the W4 allowlist
cross-package transaction semantics
MCP restart recovery of Batch Plans
```

W4-2 starts mutation. W4-4 starts checkpoint-set / multi-package Save. W4-6 owns restart hardening.

## 4. Existing Code to Reuse

### 4.1 `AgentWorkflowService.plan_patch()`

Current single-operation Plan already performs the important child guarantees:

```text
fixed SQLite asset lookup
→ exact indexed Asset Class
→ exact SHA-256 Revision
→ patch construction
→ validate_patch(policy + revision export)
→ immutable patch file + digest
→ session-local PlanRecord
```

W4-1 must reuse it for the executable child Plans consumed later by W4-2. Do not build a second child-plan implementation.

### 4.2 `AgentWorkflowService.discard_unconsumed_plans()`

This method already removes newly created, unconsumed PlanRecords and their directories and rejects cleanup of a consumed Plan.

It exists specifically for safe Batch Plan cleanup and is the required failure cleanup primitive for W4-1.

### 4.3 `validate_patch()` / Policy

Existing Policy already understands:

```text
maxAssetsPerPatch
maxOperationsPerAsset
maxValueBytes
allowedOperations
allowedAssetClasses
allowedAssetProperties / domain-specific target rules
requireRevision
```

Important: validating each child independently is NOT sufficient because a 1-op child cannot enforce the aggregate `maxAssetsPerPatch` or `maxOperationsPerAsset` contract.

W4-1 therefore needs one aggregate validation pass before child Plan exposure.

### 4.4 W3 stable target identity

W3 already has deterministic live-write target identity semantics:

```text
setVariableDefault
  blueprint-variable:<variableName>

setComponentProperty
  blueprint-component:<componentName>:<propertyPath>

setPinDefault
  blueprint-pin:<graphGuid>:<nodeGuid>:<pinName>

setAssetProperty
  setAssetProperty:<propertyPath>
```

W4-1 must use the same identity semantics for expected supersession. Do not invent a second incompatible target key model.

Do not create a circular dependency by importing a private helper from a new module. Preferred options, in order:

1. expose a small behavior-preserving helper from an appropriate shared live-write identity module;
2. expose a narrow `AgentWorkflowService` wrapper;
3. only if neither is clean, keep the W4-1 helper local and add parity tests against the W3 semantics.

No broad `agent_workflow.py` refactor belongs in W4-1.

### 4.5 Animation Batch Planner as a pattern only

`AnimationScaleFixBatchService.plan()` already demonstrates a useful orchestration pattern:

```text
normalize request
→ create immutable child Plans
→ collect child identities
→ persist Batch Plan only after all children succeed
→ on exception: delete partial Batch directory + discard unconsumed child Plans
```

W4-1 should reuse this pattern, not its animation-specific schema, state machine or execution code.

## 5. New Module Boundary

Create a dedicated module:

```text
src/ue_agent_kit/bounded_batch.py
```

Recommended responsibilities:

```text
constants / hard bounds
request normalization
Batch Plan dataclasses / serialization
aggregate patch construction
request digest
stable sequence IDs
supersession analysis
Batch Plan persistence / tamper validation
BoundedBatchService.plan()
BoundedBatchService.get()        internal, for later W4-2 reuse
```

Keep `agent_workflow.py` changes narrow. It should remain the owner of existing single-op child Plans and provide only the child-plan primitives W4-1 needs.

Do not create `checkpoint_sets.py` yet; that belongs to W4-4.

## 6. Frozen Public Request Contract

Public Tool:

```text
ue_plan_live_write_batch
```

Recommended request shape:

```json
{
  "assets": [
    {
      "assetPath": "/Game/.../BP_A.BP_A",
      "operations": [
        {
          "operation": "setVariableDefault",
          "target": {"variableName": "TransactionInt"},
          "value": 42
        },
        {
          "operation": "setComponentProperty",
          "target": {
            "componentName": "DefaultSceneRoot",
            "propertyPath": "RelativeLocation.X"
          },
          "value": 10
        }
      ]
    },
    {
      "assetPath": "/Game/.../DA_B.DA_B",
      "operations": [
        {
          "operation": "setAssetProperty",
          "target": {"propertyPath": "IntValue"},
          "value": 142
        }
      ]
    }
  ],
  "description": "optional bounded task description"
}
```

Rules:

1. `assets` is required and contains 1..4 items.
2. Each asset group contains exactly one non-empty `assetPath` and 1..8 operations.
3. One exact asset path may appear only once in a request.
4. Total operations must be 1..16.
5. Asset order is request order.
6. Operation order inside an asset is request order.
7. W4-1 does not support cross-asset ordering dependencies.
8. Unknown request fields fail closed rather than being silently ignored.
9. `target` must be an object; `value` uses the existing Operation/Policy value validation.
10. The same target may appear repeatedly inside one asset; this is not rejected because supersession is a proven W3 semantic.

## 7. Explicit W4 Operation Allowlist

Do not derive the W4 allowlist from `LIVE_WRITE_OPERATION_REGISTRY`, because that registry contains additional live-capable operations that W4-0 explicitly deferred.

Freeze a W4-specific allowlist:

```text
setAssetProperty
setVariableDefault
setComponentProperty
setPinDefault
```

Any other operation returns a bounded-batch unsupported-operation error before any child Plan is exposed.

This is a deliberate product boundary, not a temporary consequence of current registry contents.

## 8. Bound Enforcement

Hard bounds:

```text
MAX_BATCH_ASSETS                = 4
MAX_BATCH_OPERATIONS_PER_ASSET  = 8
MAX_BATCH_OPERATIONS_TOTAL      = 16
MAX_BATCH_REQUEST_BYTES         = 64 * 1024
```

Effective asset/op limits are the minimum of W4 hard bounds and current Policy values.

The 64 KiB limit must be measured deterministically over the normalized user-controlled request payload encoded as canonical UTF-8 JSON. Generated IDs, timestamps, bound revisions and result metadata do not count toward the request-size limit.

The response must report both hard and Policy/effective limits so an Agent can understand why a request was rejected.

Recommended result section:

```json
"bounds": {
  "hard": {
    "maxAssets": 4,
    "maxOperationsPerAsset": 8,
    "maxTotalOperations": 16,
    "maxRequestBytes": 65536
  },
  "policy": {
    "maxAssetsPerPatch": 4,
    "maxOperationsPerAsset": 8,
    "maxValueBytes": 16384
  },
  "effective": {
    "maxAssets": 4,
    "maxOperationsPerAsset": 8
  },
  "requestBytes": 1234
}
```

Do not hard-code the sample Policy values above; return the actual resolved Policy values.

## 9. Aggregate Validation Before Child Exposure

W4-1 must use a two-level validation strategy.

### Stage A — request and hard bounds

Validate:

```text
request type / fields
asset group count
duplicate asset paths
per-asset op count
total op count
64 KiB request size
W4 operation allowlist
target object shape
```

No child Plan exists yet.

### Stage B — bind all assets

For each asset in request order:

```text
index_service.get_asset(...)
_assert_asset_fresh(assetPath)
bind exact assetClass
bind exact sha256 Revision
```

Any failure aborts the entire request. No child Plan exists yet.

### Stage C — aggregate Policy / Revision validation

Construct one ephemeral aggregate Patch using all bound assets and all requested operations, then call the existing `validate_patch()` once.

This validation exists to enforce aggregate rules that child `plan_patch()` cannot see, especially:

```text
Policy maxAssetsPerPatch
Policy maxOperationsPerAsset
Policy maxValueBytes
allowedOperations
asset class / target restrictions
Revision consistency
```

The aggregate validation artifact is not an executable child Plan and does not need to remain after planning succeeds.

### Stage D — create existing single-op child Plans

Only after aggregate validation passes, create one existing `plan_patch()` child for each requested operation in global execution order.

Expected order for:

```text
A: op1, op2
B: op1, op2
```

is:

```text
sequenceIndex 0 = A.op1
sequenceIndex 1 = A.op2
sequenceIndex 2 = B.op1
sequenceIndex 3 = B.op2
```

If any child creation fails:

```text
Batch Plan is not persisted
no Batch Plan ID is returned
all previously created child Plans are discarded with discard_unconsumed_plans()
underlying failure is reported without pretending partial success
```

This is the W4-1 atomicity boundary: Plan exposure is all-or-nothing even though later mutation/save phases are not cross-package atomic.

## 10. Batch Operation Identity and Ordering

Generated child Plan IDs remain existing `plan_*` identities and are not deterministic across repeated requests.

Add deterministic Batch-local operation IDs derived only from execution position:

```text
bop_0001
bop_0002
...
bop_0016
```

Recommended fields per operation:

```text
batchOperationId
sequenceIndex
assetIndex
operationIndex
childPlanId
childPatchDigest
operation
target
value
risk
stableTargetKey
expectedEffective
expectedSupersededByBatchOperationId
expectedSupersedesBatchOperationIds[]
```

These Batch-local IDs are for ordering/evidence correlation. They do not replace the existing child Plan ID, live receipt or later Change Set receipt.

## 11. Supersession Preview

Supersession analysis is per asset and per `stableTargetKey`.

For a same-target sequence:

```text
bop_0001 TransactionInt = 10
bop_0002 TransactionInt = 20
bop_0003 TransactionInt = 42
```

W4-1 should report:

```text
bop_0001 expectedEffective=false, expectedSupersededBy=bop_0003
bop_0002 expectedEffective=false, expectedSupersededBy=bop_0003
bop_0003 expectedEffective=true,  expectedSupersedes=[bop_0001,bop_0002]
```

This is a planning prediction only. W3/W4 execution receipts remain authoritative for actual supersession after Apply.

Do not remove or coalesce the earlier operations from the Batch Plan.

## 12. Deterministic Digest Contract

Two digests serve different purposes.

### `requestDigest`

Deterministic over the normalized request plus the bound asset Class/Revision facts required to make the Plan meaningful, excluding:

```text
batchPlanId
child Plan IDs
child generated operation IDs
timestamps
filesystem paths
```

The same request against the same asset revisions and classes must produce the same `requestDigest`.

If an asset Revision changes, `requestDigest` must change.

### `batchPlanDigest`

SHA-256 of the exact persisted immutable Batch Plan payload. It includes generated child Plan identities and therefore is an integrity digest, not a cross-invocation equality key.

This distinction avoids a false requirement that random child Plan IDs must be deterministic.

## 13. Immutable Batch Plan Record

Recommended schema version:

```text
LIVE_WRITE_BATCH_PLAN_SCHEMA_VERSION = "1.0"
```

Recommended internal record:

```text
LiveWriteBatchPlanRecord
```

Recommended persistence location under the fixed Work Root:

```text
batch-plans/<batchPlanId>/plan.json
```

Recommended ID prefix:

```text
lwbp_<secure-token>
```

Minimum persisted payload:

```text
schemaVersion
batchPlanId
state = planned
projectName
createdAtUtc
description
requestDigest
assetCount
operationCount
assets[]
bounds
confirmationRequired
```

`get(batchPlanId)` must re-read the file and verify both the stored digest and payload equality, following the existing Plan / animation Batch Plan tamper pattern.

W4-1 does not promise restart recovery of the in-memory service registry. That belongs to W4-6. Persisting the file now gives later phases a stable integrity artifact without overclaiming restart semantics.

## 14. Public Result Contract

Recommended success result:

```json
{
  "schemaVersion": "1.0",
  "tool": "ue_plan_live_write_batch",
  "ok": true,
  "batchPlanId": "lwbp_...",
  "batchPlanDigest": "sha256:...",
  "requestDigest": "sha256:...",
  "state": "planned",
  "projectName": "...",
  "assetCount": 2,
  "operationCount": 4,
  "assets": [...],
  "bounds": {...},
  "commitAllowedByPolicy": true,
  "confirmationRequired": "APPLY LIVE WRITE BATCH lwbp_...",
  "nextStep": "Call ue_apply_live_write_batch after W4-2 is available."
}
```

During W4-1 the `nextStep` may explicitly state that Apply is not yet implemented, but the final field names should already be compatible with W4-2.

`commitAllowedByPolicy` means the aggregate Patch passes the resolved Policy commit gate. It does not authorize mutation and does not bypass later session/freshness checks.

## 15. Error Contract

Use stable batch-specific errors for request/bound failures and preserve useful underlying details for existing Policy/Revision failures.

Recommended codes:

```text
live-write-batch-request-invalid
live-write-batch-request-too-large
live-write-batch-asset-count-exceeded
live-write-batch-operation-count-exceeded
live-write-batch-total-operation-count-exceeded
live-write-batch-duplicate-asset
live-write-batch-operation-unsupported
live-write-batch-plan-rejected
live-write-batch-child-plan-failed
live-write-batch-plan-tampered
live-write-batch-plan-not-found
```

Existing deterministic failures such as these should remain recognizable in structured details or propagate when appropriate:

```text
asset-not-indexed
index-stale
revision-unavailable
asset-class-unavailable
patch-plan-rejected / Policy validation errors
```

Do not convert every failure into one generic `batch-failed` code.

## 16. MCP / Tool Registry Integration

Add the public planning Tool in the workflow tool module:

```text
ue_plan_live_write_batch(assets, description="")
```

Annotations:

```text
workflow / planning
read-only in behavior
no destructive annotation
```

Register it in `tool_registry.py` as a workflow planning Tool.

Do not expose an arbitrary filesystem path, Policy path, project, engine path, database path, Bridge endpoint, or operation registry override.

Update server guidance minimally so Agents prefer the bounded batch planner when a request contains multiple supported W4 operations. Existing `ue_plan_patch` remains available and unchanged.

## 17. Expected File Changes

Primary new file:

```text
src/ue_agent_kit/bounded_batch.py
```

Likely narrow edits:

```text
src/ue_agent_kit/agent_workflow.py
  expose/reuse child Plan cleanup / stable target identity only as needed

src/ue_agent_kit/mcp_workflow_tools.py
  register ue_plan_live_write_batch

src/ue_agent_kit/tool_registry.py
  register Tool metadata

src/ue_agent_kit/mcp_server.py
  minimal guidance/capability text if required
```

Tests:

```text
tests/python/test_bounded_batch.py              new, preferred
 tests/python/test_agent_workflow.py             only for shared primitive regression
 tests/python/test_tool_registry.py              registry/count contract
 relevant MCP workflow/server tests              public Tool request/result contract
```

No C++ file should change in W4-1.

## 18. Required Test Matrix

### A. Compatibility / happy path

```text
A1  1 asset / 1 setAssetProperty
A2  1 BP / variable + component + pin
A3  BP 3 ops + Data Asset 1 op
A4  request order retained exactly
A5  same request + same revisions → same requestDigest
A6  changed bound Revision → changed requestDigest
```

### B. W4 hard bounds

```text
B1  0 assets rejected
B2  5 assets rejected
B3  0 operations in an asset rejected
B4  9 operations in one asset rejected
B5  17 total operations rejected
B6  canonical request bytes >64 KiB rejected
B7  exactly 4 / 8 / 16 boundary accepted when Policy permits
```

### C. Policy / Revision

```text
C1  Policy maxAssetsPerPatch below W4 hard bound wins
C2  Policy maxOperationsPerAsset below W4 hard bound wins
C3  maxValueBytes rejection survives aggregate path
C4  disallowed operation rejected
C5  asset-not-indexed rejects whole Batch Plan
C6  stale asset Revision rejects whole Batch Plan
C7  invalid Asset Class / operation pairing rejects whole Batch Plan
```

### D. Operation surface

```text
D1  four W4 operations accepted
D2  live-capable but W4-deferred operation rejected
D3  unknown operation rejected
D4  malformed variable target rejected
D5  malformed component target rejected
D6  malformed pin GUID rejected
D7  malformed asset property path rejected
```

### E. Duplicate / supersession

```text
E1  duplicate asset group rejected
E2  repeated same-target operations are retained
E3  expectedEffective only true for final same-target operation
E4  stableTargetKey matches W3 semantics
E5  operations on distinct targets remain independently effective
```

### F. Failure cleanup / atomic exposure

```text
F1  aggregate validation failure creates zero child Plans
F2  injected failure creating child 2 removes child 1
F3  injected failure creating final child removes all earlier children
F4  failed request writes no Batch Plan directory
F5  cleanup never deletes a pre-existing unrelated Plan
F6  consumed child Plan cleanup fails closed
```

### G. Immutable plan / tamper

```text
G1  Batch Plan persisted only after all children succeed
G2  stored digest matches exact payload
G3  plan file mutation → live-write-batch-plan-tampered
G4  unknown batchPlanId → plan-not-found
G5  generated Batch-local operation IDs are unique and sequence-stable
```

### H. Zero side effects

For every W4-1 planning test/smoke:

```text
Editor Bridge calls        = 0
resident apply count       = 0
Fast Verify count          = 0
Save count                 = 0
child Unreal processes     = 0
Change Set operation writes = 0
```

## 19. Real Fixed-project Smoke

After unit/contract tests pass, run two read-only MCP smokes using the same logical payloads as W4-0 B0/B1.

### S1 — B0 planning payload

```text
1 Blueprint
3 operations
variable + component + pin
```

Expected:

```text
assetCount       = 1
operationCount   = 3
all child Plans valid
all expectedRevision values match current frozen index
Bridge calls     = 0
Unreal processes = 0
```

### S2 — B1 planning payload

```text
Blueprint 3 ops
Data Asset 1 op
```

Expected:

```text
assetCount       = 2
operationCount   = 4
asset order      = request order
sequenceIndex    = 0..3
Bridge calls     = 0
Unreal processes = 0
```

No Editor startup is required solely for W4-1. If the fixed snapshot is stale because the fixture changed, repair/refresh the fixture/index through the existing controlled workflow rather than weakening freshness checks.

## 20. Gate Sequence

Before implementation:

```text
git status / latest commit
confirm HEAD descends from 90f6a11
confirm no concurrent product-code edits will be overwritten
```

During implementation:

```text
focused W4-1 tests
existing plan_patch / high-level Plan tests
Tool Registry tests
```

Final W4-1 gates:

```text
Ruff
Python discovered suite (current baseline starts at 712; use actual discovered count after new tests)
compileall
ValidateRelease.py --expected-version 0.7.0 --require-release-docs
git diff --check
real fixed-project S1/S2 read-only smoke
```

UE5.6 Direct Build is not required if W4-1 obeys scope and makes no C++ changes. If C++ changes unexpectedly become necessary, stop and reassess the phase boundary before proceeding.

## 21. Stop Conditions

Stop W4-1 and diagnose before continuing if implementation requires any of the following:

```text
weakening existing Policy or Revision validation
creating child Plans before aggregate hard/Policy validation without cleanup
leaving partial child Plans after a failed Batch Plan
accepting operations outside the four-operation W4 allowlist
creating a generic C++ batch mutation endpoint
starting Unreal/Editor for normal Batch planning
silently coalescing same-target requested operations
claiming cross-package atomicity
making random child Plan IDs part of deterministic request equality
expanding into W4-2 Apply behavior
```

## 22. W4-1 Exit Gate

W4-1 is complete only when all are true:

```text
[ ] ue_plan_live_write_batch is publicly registered
[ ] request is asset-grouped and ordering is deterministic
[ ] hard bounds 4 / 8 / 16 / 64 KiB are enforced
[ ] effective limits honor current Policy
[ ] exactly four W4 operations are accepted
[ ] aggregate Policy / Revision validation runs before child exposure
[ ] every operation has an existing immutable child Plan
[ ] any child failure leaves zero newly exposed child Plans
[ ] duplicate asset groups fail closed
[ ] same-target operations remain audit-visible with expected supersession metadata
[ ] requestDigest is deterministic for request + bound revisions/classes
[ ] Batch Plan payload is immutable and tamper-checked
[ ] planning causes zero Editor Bridge calls and zero Unreal child processes
[ ] existing ue_plan_patch and W1-W3 behavior remain compatible
[ ] focused + full Python / Ruff / compileall / release / diff gates pass
[ ] real fixed-project S1/S2 read-only smoke passes
[ ] no product mutation or C++ behavior was added
```

After this gate, W4-2 may implement `ue_apply_live_write_batch` for one Blueprint / three operations using these child Plans and the existing exact `previousTransactionId` continuation chain.

# UEAgentKit D1 Agent Workflow Split Detailed Plan

> Date: 2026-08-29
>
> Branch: `feature/live-writer-expansion`
>
> Entry checkpoint: `24bf088` (`docs: close W4 full acceptance and documentation`)
>
> Prerequisite: W4 complete with fresh real UE5.6 C1-C12 PASS and frozen W4 evidence contract.
>
> Scope: pure structural refactor of `src/ue_agent_kit/agent_workflow.py`; no product behavior, public Tool, serialized evidence, Policy, schema, Writer safety, or C++ change.

## 1. Stage Decision

```text
W4  complete  24bf088
D1  NEXT / current
W5  blocked on D1
M1  blocked on D1 for workflow-touching follow-up
Track X workflow work blocked on D1
```

The Master Plan and Mid-term Execution Spec freeze the mainline order as:

```text
W4 → D1 → W5
       ├→ M/C follow-up on split workflow modules
       └→ Track X workflow-related work
```

D1 must happen immediately after W4. Do not start W5 first and do not allow Memory / Track X to add more call sites to the current monolithic workflow module before this split lands.

## 2. Current Repository Baseline

Repository facts at plan creation:

```text
HEAD                         24bf088
branch                       feature/live-writer-expansion
working tree                 clean
agent_workflow.py            5,625 lines
Python discovered suite      766 / 766 PASS at W4 closeout
W4 real UE C1-C12            PASS
W4 recovery H1-H6            PASS
UE5.6 Direct Build baseline  PASS at 55919bd
published version            0.7.0 unchanged
```

Current modules already owning W4 state machines:

```text
bounded_batch.py
checkpoint_sets.py
batch_recovery.py
```

These remain their domain owners. D1 must not copy or re-implement their state machines inside `workflow_batch.py`.

Current internal consumers importing from `agent_workflow.py` include at least:

```text
animation_scale_fix_batch.py
batch_recovery.py
bounded_batch.py
checkpoint_sets.py
mcp_query_tools.py
mcp_server.py
mcp_workflow_tools.py
retarget_workflow.py
```

The existing import path is therefore part of the compatibility contract.

## 3. Goal

Turn `agent_workflow.py` from the orchestration implementation hotspot into a thin compatibility facade while preserving the exact current behavior.

Target shape:

```text
workflow_common.py
  shared exceptions / records / constants / serialization and path helpers
  common workflow base behavior used across domains

workflow_plan.py
  Plan / DryRun / request validation / plan-consumption behavior

workflow_live.py
  resident Live Apply / Undo / Discard / live-state handling

workflow_verify.py
  Save / checkpoint / Strong Verify / verification-related orchestration

workflow_batch.py
  only PatchWorkflowService hooks shared with W4 orchestration services
  e.g. batch binding / checkpoint preflight / disk-rollback preparation
  NOT the bounded_batch/checkpoint_sets/batch_recovery state machines

agent_workflow.py
  compatibility imports / re-exports
  PatchWorkflowService facade assembled from the split implementations
  no large implementation body
```

Target module size:

```text
each new workflow_*.py < 1,500 lines
agent_workflow.py       thin facade; target << 1,000 lines
```

## 4. Non-negotiable Compatibility Contract

D1 is not a cleanup opportunity. The following must remain unchanged.

### 4.1 Public Python import contract

Existing imports must continue to work, including:

```python
from ue_agent_kit.agent_workflow import PatchWorkflowService
from ue_agent_kit.agent_workflow import WorkflowError
from ue_agent_kit.agent_workflow import MATERIAL_PARAMETER_OPERATIONS
from ue_agent_kit.agent_workflow import live_write_stable_target_key
```

Any currently exported record/helper imported by repository code must remain re-exported from `agent_workflow.py`.

### 4.2 `PatchWorkflowService` contract

Preserve exactly:

```text
class import path
constructor signature
public method names
public method signatures/defaults
return dictionaries / error codes
confirmation phrases
state transitions
receipt/checkpoint/recovery identifiers
file paths
serialization format
lock/session behavior
```

Changing the internal MRO through mixins is allowed only if externally observable behavior remains identical.

### 4.3 W1-W4 safety contract

No change to:

```text
Policy validation
Revision freshness
Dirty-package fail-closed behavior
exact Editor session binding
previousTransactionId continuation
Fast Verify semantics
Strong Verify independence
checkpoint Save authorization
supersession semantics
partial-applied / partial-saved / partial-recovered boundaries
rollback manifest behavior
unrelated transaction protection
recovery ordering
```

### 4.4 W4 frozen evidence contract

The W4 Result freezes the downstream identity/reference chain used by Memory M2. D1 must not alter any field name or persisted relationship in:

```text
changeSetId
batchPlanId / batchPlanDigest
batchExecutionId
batchOperationId / stableTargetKey / sequenceIndex
liveApplyReceipt / editorSessionId / transactionId / previousTransactionId
checkpointSetId / checkpointId / saveReceipt
beforeRevision / afterRevision
aggregate verification evidence
recoveryId / completedSteps / pendingSteps / failedStep / failureBoundary
```

No schema bump is authorized in D1.

## 5. Preferred Refactor Mechanism

Use class mixins/base modules rather than delegation wrappers that rewrite every call path.

Preferred shape:

```python
class PatchWorkflowService(
    WorkflowPlanMixin,
    WorkflowLiveMixin,
    WorkflowVerifyMixin,
    WorkflowBatchMixin,
    WorkflowCommonBase,
):
    pass
```

The exact inheritance order may be adjusted after dependency inventory, but the principle is fixed:

```text
move existing method bodies unchanged
→ retain self-based private helper access
→ keep agent_workflow.PatchWorkflowService as the public facade
```

Do not replace existing calls with a new service graph merely to make the modules look cleaner.

## 6. D1-0 — Baseline and Dependency Inventory

Before moving code, capture deterministic evidence from `24bf088`.

### Required baseline artifacts

Write under uncommitted acceptance output, for example:

```text
Output/D1Acceptance/baseline.json
Output/D1Acceptance/public-imports.json
Output/D1Acceptance/workflow-method-map-before.json
Output/D1Acceptance/tool-registry-before.json
```

Capture:

```text
HEAD / branch
agent_workflow.py line count
all top-level classes/functions/constants used outside the module
PatchWorkflowService method list + signatures
repository imports from agent_workflow
Tool Registry names/counts
Python discovered test count
```

### Method dependency map

Classify every method before moving it:

```text
common
plan
dry-run
live
verify/save/checkpoint
batch-facing hook
facade-only
```

Also record cross-category private calls. This dependency map decides which shared helpers belong in `workflow_common.py`.

Do not start bulk movement until every current method has exactly one destination.

## 7. D1-1 — Extract Common Types and Helpers

Move only genuinely shared definitions first.

Typical candidates:

```text
WorkflowError
configuration / durable record dataclasses owned by this module
shared hashing / JSON / safe-path helpers
shared validation helpers used by 2+ workflow domains
stable identity helper(s)
shared constants
```

Rules:

```text
no rename
no signature change
no error-message cleanup
no new abstraction layer
no combining helpers
no splitting one helper into several helpers
```

`agent_workflow.py` must re-export compatibility symbols immediately after each move so the repository remains runnable at every checkpoint.

## 8. D1-2 — Extract Plan / Live / Verify Domains

Move existing methods in coherent groups.

### `workflow_plan.py`

Own existing Plan / DryRun-facing implementation, including exact stored-plan validation and child-plan lifecycle behavior where currently owned by `PatchWorkflowService`.

### `workflow_live.py`

Own resident Editor workflow primitives:

```text
Apply
Undo
Discard
Fast resident verification support that is inseparable from resident identity checks
live Editor state/session inspection used by those operations
```

Do not move C++ EditorBridge implementation or alter bridge method names.

### `workflow_verify.py`

Own persistence/verification workflow behavior currently implemented in `PatchWorkflowService`:

```text
authorized Save
checkpoint preparation/commit support
Strong Verify orchestration
rollback primitives where they are part of the existing authorized-save workflow
snapshot/index refresh only if dependency mapping proves it belongs to this domain
```

Do not change Strong Verify process-count semantics.

## 9. D1-3 — Extract Batch-facing Hooks and Build the Facade

`workflow_batch.py` is deliberately narrow.

Expected candidates include existing `PatchWorkflowService` hooks consumed by W4 services such as:

```text
bind_asset_for_batch()
preflight_checkpoint_commit()
prepare_asset_for_disk_rollback()
live_write_stable_target_key() binding/support where appropriate
```

Do NOT move these existing domain services into `workflow_batch.py`:

```text
BoundedBatchService
CheckpointSetService
BatchRecoveryService
```

Those modules are already the W4 domain split produced by W4 and remain authoritative.

Finally reduce `agent_workflow.py` to:

```text
imports
compatibility re-exports
PatchWorkflowService facade composition
only unavoidable compatibility glue
```

## 10. Pure-move Proof — AST Hash Gate

A normal diff is insufficient because Git represents moves as deletion/addition. D1 must generate a deterministic method-level proof against the entry commit.

Source baseline:

```text
git show 24bf088:src/ue_agent_kit/agent_workflow.py
```

For every moved top-level function and every moved `PatchWorkflowService` method:

1. parse old and new source with Python `ast`;
2. strip location metadata;
3. hash the normalized AST of the function/method body and signature;
4. write old module/line → new module/class/name mapping;
5. require old hash == new hash.

Acceptance artifact:

```text
Output/D1Acceptance/workflow-ast-move-report.json
```

Required result:

```text
moved definition count   N
AST mismatches            0
missing definitions       0
unexpected replacements   0
```

Allowed non-identical definitions are limited to explicitly documented facade/import composition code created by D1.

If any moved method requires a logical body edit to make the split work, stop and review the design rather than hiding the edit inside the refactor.

## 11. Import / Tool / Serialization Compatibility Gates

### Import snapshot

Before and after D1, verify the repository can import all symbols that were previously consumed from `agent_workflow.py`.

Add a narrow structural regression test if useful, preferably:

```text
tests/python/test_workflow_module_split.py
```

This may test:

```text
legacy imports still resolve
PatchWorkflowService public callable signatures unchanged
required re-export identity remains stable where identity matters
new workflow modules stay under the line-count bound
```

Do not modify existing product tests merely to accommodate the refactor.

### Tool Registry snapshot

Capture Tool Registry output before and after D1.

Required:

```text
Tool names             byte/sequence equivalent
Tool count             unchanged
Patch operation count  unchanged
Live-write op count    unchanged
```

The W4 closeout count baseline is currently:

```text
workflow-only                              67
workflow + memory                          79
combined live + workflow                  100
combined live + workflow + memory         112
Patch operation count                      18
Live-write operation count                 17
```

D1 must not add or remove any Tool or Operation.

### Durable evidence compatibility

No expected-test fixture, JSON schema, persisted record version, confirmation phrase, or error-code expectation may be changed to make D1 pass.

## 12. Real UE5.6 Integration Smoke

Although D1 is intended to be pure Python movement, it relocates the orchestration used by real writes. Therefore run a small real UE smoke after all Python gates are green.

Do not rerun C1-C12 mechanically.

### D1-R1 — full happy-path integration

Use the existing W4 BP + DA fixture and current active snapshot.

```text
one Change Set
→ BP 3 ops + DA 1 op bounded batch Plan
→ resident Apply + Fast Verify all
→ checkpoint-set Preview/Commit
→ aggregate Strong Verify / Semantic Diff / Trust
→ Trust = verified
```

Required invariants:

```text
4 logical resident applies
4 Fast Verifies
2 package Saves
2 Strong Verify child Unreal processes max
same W4 durable record shapes
no extra cold Unreal process before Strong Verify
```

### D1-R2 — resident recovery integration

Create an unsaved bounded batch and recover through the existing product recovery path.

Require:

```text
strict global reverse Undo
state = recovered
unrelated transaction behavior unchanged
Save = 0
```

Finally run official `WriteFixturePlan Reset` + independent Reload verification and refresh the active paired snapshot only if final fixture Revisions changed.

These smokes prove the split is wired correctly; they do not reopen W4 acceptance.

## 13. Regression Gates

Required after final split:

```text
Python discovered suite          all PASS; baseline starts at 766
Ruff                             PASS
compileall                       PASS
ValidateRelease 0.7.0            PASS
git diff --check                 PASS
AST move report                  0 mismatches
Tool Registry before/after       unchanged
legacy import snapshot           unchanged
D1-R1 real UE smoke              PASS
D1-R2 real UE recovery smoke     PASS
fixture independent verification PASS
```

C++ Direct Build:

```text
not required if D1 touches no C++
```

If any C++ file changes, that is a scope violation unless a new blocker is documented and explicitly reviewed; in that case Direct Build becomes mandatory.

## 14. Explicit Prohibitions

D1 must not do any of the following:

```text
rename public methods/classes/constants
change public function signatures/defaults
change error codes/messages intentionally
change return dictionaries
change persisted JSON layout or schema versions
change W4 bounds
change confirmation phrases
change Policy or Revision behavior
change transaction/recovery order
change tool registration
optimize process calls
reduce Bridge calls
cache new state
replace locks
introduce async behavior
add dependencies
modify C++
start W5 measurements
start Memory implementation
perform general code cleanup while files are moving
```

In particular, do not fix unrelated style issues discovered while moving code. Record them for later maintenance.

## 15. Stop Conditions

Stop D1 and diagnose if any of these occur:

```text
an existing test expectation must change
an AST hash mismatch appears for a moved method
legacy import cannot be preserved without API change
one split module cannot stay < 1,500 lines without a new design decision
circular imports require behavioral redesign rather than import relocation
Tool Registry output changes
serialized W4 evidence changes
real UE D1-R1/R2 differs from W4 semantics
unrelated transaction recovery safety regresses
```

Do not weaken the D1 contract to force completion.

## 16. Recommended Execution Order

```text
D1-0  clean HEAD + baseline inventory + method/signature/tool snapshots
→ D1-1  workflow_common.py extraction
→ focused imports/tests
→ D1-2  workflow_plan.py / workflow_live.py / workflow_verify.py
→ focused tests after each domain move
→ D1-3  workflow_batch.py + thin agent_workflow.py facade
→ AST pure-move report
→ full Python/tool/import gates
→ D1-R1 real UE happy-path smoke
→ D1-R2 resident recovery smoke
→ official fixture Reset + independent verify
→ D1 Result document
→ update README / current handoff
```

Do not combine movement with W5 or M1 implementation.

## 17. Deliverables

Expected repository changes:

```text
src/ue_agent_kit/workflow_common.py
src/ue_agent_kit/workflow_plan.py
src/ue_agent_kit/workflow_live.py
src/ue_agent_kit/workflow_verify.py
src/ue_agent_kit/workflow_batch.py
src/ue_agent_kit/agent_workflow.py

optional new structural test only:
tests/python/test_workflow_module_split.py

docs/Plans/UEAGENTKIT_D1_AGENT_WORKFLOW_SPLIT_RESULT_20260829.md
```

Acceptance output, not committed:

```text
Output/D1Acceptance/baseline.json
Output/D1Acceptance/workflow-ast-move-report.json
Output/D1Acceptance/tool-registry-before.json
Output/D1Acceptance/tool-registry-after.json
Output/D1Acceptance/d1-r1-*.json
Output/D1Acceptance/d1-r2-*.json
```

No product asset, Output artifact, backup, local configuration, Tag, Release, or Push belongs in the D1 commit.

## 18. Exit Gate

D1 is complete only when all are true:

```text
[ ] entry baseline is 24bf088 and pre-refactor suite captured
[ ] every old PatchWorkflowService method has one documented destination
[ ] workflow_common/plan/live/verify/batch modules created
[ ] every new workflow_*.py is < 1,500 lines
[ ] agent_workflow.py is a thin compatibility facade
[ ] legacy imports remain valid
[ ] public signatures/defaults remain unchanged
[ ] moved-definition AST report has 0 mismatches
[ ] existing tests pass without changing their behavioral expectations
[ ] Tool Registry / Operation surface unchanged
[ ] W4 persisted evidence contract unchanged
[ ] D1-R1 real UE full happy-path smoke passes
[ ] D1-R2 resident recovery smoke passes
[ ] official fixture Reset + independent verification passes
[ ] Python / Ruff / compileall / ValidateRelease / git diff --check all pass
[ ] no C++ / dependency / Policy / schema / published-version change
[ ] D1 Result document written
```

Only after this gate is green may the mainline proceed to W5, and workflow-touching Memory / Track X work may use the new `workflow_*.py` structure.

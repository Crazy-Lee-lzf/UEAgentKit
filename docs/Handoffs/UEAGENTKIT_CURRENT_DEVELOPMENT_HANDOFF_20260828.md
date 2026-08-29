# UEAgentKit Current Development Handoff

> Date: 2026-08-29
>
> Scope: project-level current development handoff. This is the first document to read when a new Chat / Agent takes over the UEAgentKit development line.
>
> Active worktree: `E:\WorkSpace\UEAgentKit-LiveWriter`
>
> Active branch: `feature/live-writer-expansion`
>
> Current committed HEAD: local D1 closure commit (see section 0)
>
> Latest published product version: `0.7.0` for Unreal Engine 5.6
>
> This document records current repository facts, accepted architecture decisions, Track dependencies, safety rules, and the next execution boundary. It does not authorize Push / Rebase / Tag / Release or future implementation commits.

## 0. 2026-08-29 W5 Blocked/Deferred Supersession Note

This note overrides older takeover wording later in this file where W4-7, D1, or W5 is still described as `next` or `current` without the W5 status below.

```text
W4-0 through W4-7                         complete
D1 agent_workflow split                   complete
W5-R R1 / R5 real DirectHost matrices     complete (R5 n=10 per cache state)
W5-R R20 20-op workload                   blocked by DirectHost fixture/package lifecycle
W5-S 50 GB scale fixture                  blocked/deferred (generator source created, not built/validated)
current committed HEAD                    local W5 closure commits (see below)
current Python discovered suite           766 / 766 PASS (re-run at W5 closure if gates run)
real UE W4 C1-C12                         PASS (frozen valid)
real UE W4-6 H1-H6                        PASS (frozen valid)
D1-R1 / D1-R2 real UE smokes              PASS (fresh)
final transaction fixture                 2 / 2 independently verified
```

Authoritative W5 evidence:

`docs/Plans/UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_RESULT_20260829.md`

Sections later in this file that describe W4-7, D1, or W5 as `NEXT` or list old Tool counts are historical. Where they conflict with this note, the current state above, `docs/Plans/README.md`, and the W5 Result take precedence. No Push / Rebase / Tag / Release is authorized by this handoff update.

## 1. Read This First

The current development state is:

```text
0.8 capability scope                    locally closed
W0 Editor-resident baseline             complete
W1 Blueprint narrow resident write      complete
W2 Fast Resident Verify                 complete
W3 Checkpoint Strong Verify             complete
W4-0 Contract Freeze + Baseline         complete
W4-1 Bounded Batch Plan                 complete
W4-2 Single-Asset Multi-operation Apply complete
W4-3 Multi-Asset Resident Apply         complete
W4-4 Multi-Asset Checkpoint Save        complete
W4-5 Aggregate Strong Verify / Trust    complete
W4-6 Recovery and Restart Hardening     complete
W4-7 Full Acceptance / Documentation    complete
W4                                      complete
D1 agent_workflow split                 complete
```

The current committed implementation checkpoint after D1:

```text
local D1 closure commit (see section 0 / D1 Result)
```

Current validation baseline after D1:

```text
Python discovered suite                    766 / 766 PASS
Ruff                                       PASS
compileall                                 PASS
ValidateRelease 0.7.0                      PASS
git diff --check                           PASS
UE5.6 Direct Build                         PASS (at 55919bd; no C++ change in W4-7 or D1)
real UE W4 C1-C12                          PASS (frozen valid)
real UE W4-6 H1-H6                         PASS (frozen valid)
D1-R1 real UE full happy-path smoke        PASS
D1-R2 real UE resident recovery smoke      PASS
final transaction fixture verify           2 / 2 PASS
```

Current Tool Registry counts after D1:

```text
workflow-only                              67
workflow + memory                          79
combined live + workflow                  100
combined live + workflow + memory         112
Patch operation count                      18
Live-write operation count                 17
```

No Push / Rebase / Tag / Release has been performed for W3, W4-0 through W4-7, or D1.

## 2. Project Positioning

UEAgentKit is not intended to be a generic unrestricted Unreal Editor remote-control layer.

Its current product position is:

```text
UE project knowledge / context
+ deterministic Asset / Blueprint semantics
+ Policy / Revision gated changes
+ resident Editor narrow writes
+ exact Undo / Discard / Save / Verify evidence
+ Revision-aware Memory
+ Agent-facing analysis / trust workflow
```

The core design principle remains:

```text
understand first
→ narrow authorized mutation
→ exact evidence
→ independent verification
→ recoverable state
```

Do not trade W1-W3 safety guarantees for W4 convenience.

Latest published version remains `0.7.0`. The 0.8 capability work and current Writer work are development-line capabilities, not a published 0.8 package release.

## 3. Repository / Worktree Baseline

### Active Writer worktree

```text
Path       E:\WorkSpace\UEAgentKit-LiveWriter
Branch     feature/live-writer-expansion
UE root    E:\EPICGAME\UE_5.6
```

### Other known repo worktree

```text
Path       E:\WorkSpace\UEAgentKit
Branch     feature/agent-reliability
```

Do not assume the two worktrees are interchangeable. Always inspect actual Git status / branch / HEAD before changing files.

### Recent Writer checkpoints

```text
1c68f4d docs: add D1 agent workflow split plan
24bf088 docs: close W4 full acceptance and documentation
55919bd feat: close W4-6 recovery and restart hardening
f4ba1c4 feat: add W4-5 aggregate strong verify semantic diff trust
d277369 feat: add W4-4 multi-asset checkpoint save
76f90b3 feat: add W4-3 multi-asset resident apply
```


Historical 0.8 capability closeout checkpoint:

```text
2aadb66 docs: close 0.8 capability scope
```

## 4. Documentation Authority

Do not reconstruct project direction from old Chat messages when disk documents are available.

Read in this order:

```text
1. docs/Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
   → current project-level takeover state

2. docs/Plans/README.md
   → current plan navigation and stage status

3. docs/Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
   → project priority / Track direction / architecture decisions

4. docs/Plans/UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
   → cross-Track dependencies, task cards, acceptance contracts

5. docs/Plans/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md
   → authoritative W4-0 ... W4-7 parent contract

6. current W4 sub-stage Detailed Plan / Result
   → phase-specific implementation and factual evidence
```

For completed stages, RESULT documents override old PLAN blocker/intermediate wording.

The older:

```text
docs/Handoffs/UEAGENTKIT_W4_MAINLINE_CHAT_HANDOFF_20260828.md
```

is only a W3 → W4 mainline Chat handoff snapshot. It is not the project-wide handoff and predates completion of W4-1.

## 5. 0.8 Capability Closeout

0.8.x Context / Analysis / Agent Reliability capability scope is locally closed.

Completed sequence:

```text
R0 Task Context / deterministic context
R1 Impact Analysis
R2 Semantic Diff
R3 Verification Plan + Trust Verdict
R4 Real Agent Benchmark v1
R4.1 frozen repeat
C0-C6 closeout / reliability / capability gap / release review
```

Capability audit result:

```text
105 public Tools / 18 Patch Operations at the 0.8 closeout snapshot
0 Must-fix new tools
R5 Value Provenance / Execution Trace = deferred by benchmark evidence
```

Important interpretation:

- 0.8 closeout historical Python full-suite value `739` is not a permanent future test-count gate.
- current Writer branch discovered suite is `729` after W4-1.
- R5 must not be restarted unless future real cases repeatedly show provenance / trace as a primary blocker.

## 6. Writer Development History

### W0 — baseline / latency instrumentation

Complete.

Purpose:

- establish cold-path and stage latency baselines;
- instrument process wall time;
- define Fast vs Strong verification boundaries.

### W1 — Blueprint narrow resident write

Complete.

Supported resident Blueprint mutation surface:

```text
setVariableDefault
setComponentProperty
setPinDefault
```

Important recovery fix:

- Blueprint compile invalidated transient CDO / `FProperty*` / ComponentTemplate / GraphPin pointers;
- stable identity re-resolution was introduced;
- Undo / Discard recovery refreshes targets before semantic restore verification;
- do not reintroduce cached transient pointer assumptions.

### W2 — Fast Resident Verify

Complete.

Public API:

```text
ue_verify_live_write_fast(asset_path, live_apply_receipt, change_set_id)
```

Semantics:

```text
verificationKind = resident-fast
verified = true only for exact resident receipt/session/change-set binding
```

Fast Resident Verify is resident evidence only. It is never a replacement for independent Strong Verify.

### W3 — Checkpoint Strong Verify

Complete with real UE5.6 C0-C6 evidence.

Core APIs / behavior:

```text
ue_save_authorized_asset(... verification_mode=immediate|checkpoint)
ue_verify_live_write_checkpoint(checkpoint_id)
```

Checkpoint mode:

- Preview binds exact live receipt / Change Set / effective / superseded operation set;
- Commit saves in resident Editor;
- captures after-save disk SHA-256;
- starts zero child Unreal verification processes during Save;
- independent Strong Verify occurs later from checkpoint evidence.

W3 also closed two key blockers:

1. exact same-asset transaction continuation via `previousTransactionId`;
2. Blueprint snapshot refresh uses full canonical export with unchanged defaults.

W3 invariants used by W4:

```text
same-asset continuation must bind exact previous transaction
unrelated Dirty package state remains fail-closed
one W3 checkpoint = one asset/package
Strong Verify remains independent of resident read-back
same-target superseded operations remain audit-visible
stale Revision / canonical mismatch fail closed
```

W3 commits:

```text
3280102 fix: close W3 live-write continuation and snapshot refresh
ab731f1 test: cover W3 continuation and full snapshot refresh
45e6ea2 docs: close W3 checkpoint strong verify
```

## 7. W4 Goal and Frozen Contract

W4 is an orchestration / Agent UX milestone, not a new generic low-level Writer.

Target workflow:

```text
one Change Set
→ plan several compatible narrow writes
→ resident apply in deterministic order
→ Fast Verify each
→ explicit partial-applied boundary
→ checkpoint touched assets
→ sequential authorized package saves
→ independent Strong Verify per effective asset
→ Semantic Diff / Verification Plan / Trust
→ exact recovery
```

Initial W4 operation allowlist is frozen to:

```text
setAssetProperty
setVariableDefault
setComponentProperty
setPinDefault
```

Do not replace this with `LIVE_WRITE_OPERATION_REGISTRY`. Other live-capable operations are intentionally deferred.

Initial W4 hard bounds:

```text
max assets per batch           4
max operations per asset       8
max total operations          16
max serialized request        64 KiB
per-value limit               existing Policy maxValueBytes
```

Effective limit is the minimum of W4 hard bounds, Policy limits, and relevant capacity limits.

Frozen semantics:

```text
asset-grouped request
assets execute in request order
operations execute in request order inside asset
same asset may appear only once in a batch request
same-target repeated writes are retained / audit-visible
no cross-package atomicity claim
automatic Save is not authorized
Strong Verify is not resident read-back
recovery must never skip unrelated Editor transactions
```

## 8. W4-0 — Contract Freeze and Baseline

Complete and committed:

```text
90f6a11 docs: close W4-0 contract freeze and baseline
```

No product code changed.

### B0 — one Blueprint / three operations

```text
public MCP Tool calls                    19
resident Editor Bridge calls             42
resident apply count                       3
Fast Verify count                          3
checkpoint save                           1 Preview + 1 Commit
Strong Verify child Unreal                1
public result bytes                   54,120
wall elapsed ms                    12,339.826
Semantic Diff                         verified
Trust                                 verified
```

### B1 — two assets / four operations

```text
public MCP Tool calls                    27
resident Editor Bridge calls             63
resident apply count                       4
Fast Verify count                          4
checkpoint save                           2 Preview + 2 Commit
Strong Verify child Unreal                2
public result bytes                   79,191
wall elapsed ms                    22,861.093
Semantic Diff                         verified
Trust                                 verified
```

These B0/B1 numbers are the W4-7 comparison baseline. W4 should reduce public orchestration overhead without weakening write / verify / recovery guarantees.

Both runs were followed by deterministic fixture Reset + independent verification.

## 9. W4-1 — Bounded Batch Plan

Complete and committed:

```text
71400c9 feat: add W4-1 bounded batch planning
```

New public planning Tool:

```text
ue_plan_live_write_batch(assets, description="")
```

New module:

```text
src/ue_agent_kit/bounded_batch.py
```

Responsibilities:

- W4 hard bounds;
- normalized request representation;
- aggregate Policy / Revision validation;
- exact asset Class / Revision binding;
- existing child `plan_patch()` creation;
- all-or-nothing child-plan exposure;
- stable target identity;
- supersession preview;
- immutable Batch Plan persistence;
- tamper detection.

Narrow additions to existing code:

```text
agent_workflow.py
  bind_asset_for_batch()
  public live_write_stable_target_key()

mcp_workflow_tools.py
  ue_plan_live_write_batch registration

tool_registry.py
  ToolDefinition

mcp_server.py
  minimal guidance update
```

### Critical W4-1 validation order

Do not regress this:

```text
validate request + W4 hard bounds
→ bind every Asset Class / Revision
→ build aggregate ephemeral Patch
→ validate aggregate Policy / Revision
→ create single-op child Plans
→ if any child fails: discard all newly-created child Plans
→ only after all children succeed: persist immutable Batch Plan
```

Reason:

Calling `plan_patch()` independently for each child cannot enforce aggregate Policy constraints such as `maxAssetsPerPatch` and `maxOperationsPerAsset`.

### Same-target behavior

The existing generic Patch validator rejects duplicate transaction targets. W4 deliberately permits repeated same-target writes because supersession is a first-class W3 behavior.

Therefore W4-1 aggregate validation ignores only:

```text
duplicate-transaction-target
```

and preserves every other Policy / Revision / target / operation error as a hard failure.

Do not broaden this exception.

### W4-1 result identity

```text
batchPlanId        lwbp_...
requestDigest      deterministic normalized request + bound asset facts
batchPlanDigest    immutable persisted-payload integrity digest
state              planned
```

`requestDigest` and `batchPlanDigest` intentionally have different roles.

### W4-1 acceptance

```text
Python discovered suite             729 / 729 PASS
fixed-project S1 1 BP / 3 ops       PASS
fixed-project S2 BP + DA / 4 ops    PASS
Editor Bridge calls                  0
Unreal child processes               0
C++ changes                          none
```

W4-1 historical records describe the planning-only slice; W4 closure supersedes them.

## 10. W4-2 Historical Task

W4-2 was the then-next mainline stage and is now complete:

```text
W4-2 Single-Asset Multi-operation Apply
```

Its historical entry checkpoint was `71400c9`; the current closure is documented in the final W4 Result.

Primary real UE slice:

```text
one Blueprint
  op1 setVariableDefault
  op2 setComponentProperty
  op3 setPinDefault
```

Required flow:

```text
immutable W4-1 Batch Plan
→ exact confirmation
→ consume child Plan 1
→ existing resident live apply
→ Fast Verify
→ consume child Plan 2 with exact previousTransactionId
→ Fast Verify
→ consume child Plan 3 with exact previousTransactionId
→ Fast Verify
→ record exact sequence / Change Set evidence
→ no Save
```

Required W4-2 behavior:

- use existing single-operation resident Writer internally;
- no new generic C++ batch mutation endpoint unless real evidence proves unavoidable;
- each operation Fast Verifies before the next begins;
- exact previous transaction chain is mandatory for same asset;
- Batch Plan tamper / child Plan tamper / stale state fails closed;
- successful sequence is durable enough for later W4 recovery work;
- same-target supersession remains correct;
- no package save occurs in W4-2.

Failure example:

```text
op1 PASS
op2 FAIL
op3 not started
```

must be represented as an exact partial boundary, not generic `failed`:

```text
state                 partially_applied
lastSuccessful        op1
failedOperation       op2
notStarted            [op3]
recovery order        reverse successful execution order
```

Real UE acceptance C1:

```text
BP variable + component + pin batch apply
```

Do not start W4-3 multi-asset resident Apply until C1 passes.

## 11. Remaining W4 Phases

Authoritative phase list:

```text
W4-0 Contract Freeze and Baseline                       complete
W4-1 Bounded Batch Plan                                 complete
W4-2 Single-Asset Multi-operation Apply                 complete
W4-3 Multi-Asset Resident Apply                         complete
W4-4 Multi-Asset Checkpoint Save                        complete
W4-5 Aggregate Strong Verify / Semantic Diff / Trust    complete
W4-6 Recovery and Restart Hardening                     complete
W4-7 Full Acceptance / Documentation                    complete
```

### W4-3

First pair:

```text
BP_TransactionBlueprint
DA_TransactionAsset
```

Real cases:

```text
C2  BP 3 ops + DA 1 op all applied
C3  later-asset failure produces exact partially_applied boundary
C4  resident-only partial recovery returns exact baseline
```

### W4-4

Add aggregate checkpoint-set orchestration while preserving one W3 checkpoint per asset.

Target durable aggregate:

```text
ChangeSetCheckpointSetRecord
```

Important invariant:

```text
all-assets preflight before first save
save sequentially per package
cross-package save is NOT atomic
partial save state must be explicit
```

Real cases C5-C8 cover full save, zero-save preflight failure, mid-save partial failure, and MCP restart persistence.

### W4-5

Aggregate child W3 Strong Verify results and then Semantic Diff / Verification Plan / Trust.

Aggregate Trust may be `verified` only when all required child evidence is verified.

Real cases C9-C12 include full verified, canonical mismatch, stale Revision, and same-target supersession in a multi-asset batch.

### W4-6

Harden restart / corruption / recovery boundaries.

Never:

- infer recovered state from missing evidence;
- skip unrelated Editor transactions;
- claim rollback success without independent verification for disk rollback.

### W4-7

Complete. Fresh C1-C12 real UE acceptance, fixture exact recovery, performance comparison against W4-0 B0/B1, and final documentation closure are recorded in `docs/Plans/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md`.

The final Change Set / batch receipt structure is frozen there for downstream Memory Track M2.

## 12. Post-W4 Writer / Maintenance Direction

### W5 — real-project acceptance + scale baseline

W5 starts after D1.

Purpose:

- prove W4 behavior in a real project;
- measure end-to-end batch cost;
- collect real failure/performance evidence;
- provide data for later optional symbolic compression M6.

Do not hard-code assumptions about project size/storage as universal acceptance facts. Measure actual project/storage baseline and record optional slow-storage profiles separately.

### D1 — split `agent_workflow.py`

Complete. `agent_workflow.py` is now a thin facade assembled from `workflow_common.py`, `workflow_plan.py`, `workflow_live.py`, `workflow_verify.py`, and `workflow_batch.py`. Pure-move AST proof is 0 mismatches; Tool Registry / public imports / serialized contracts are unchanged; D1-R1 and D1-R2 real UE smokes pass.

Authoritative evidence: `docs/Plans/UEAGENTKIT_D1_AGENT_WORKFLOW_SPLIT_RESULT_20260829.md`.

### D2-D4

```text
D2 Tool count single source
D3 UE Build CI
D4 generated API reference / optionally exposed through read-only Web view
```

## 13. Track M — Memory Automatic Accumulation

Track M implementation must not begin until W4 has frozen the Change Set / batch evidence structure. Design work may exist earlier, but implementation must follow the current Master/Midterm contracts.

### Non-negotiable efficiency constraints

```text
Memory should not materially slow task start/end
startup injection <= 800 tokens
recall <= 5 items / <= 2000 chars / <= 300 ms
end-of-task synchronous overhead < 100 ms
no-memory case returns empty content, no filler
L0→L1 automatic distillation uses zero LLM calls
```

### Schema progression

This was explicitly corrected and is frozen:

```text
v3 → v4    M2: memory_l0_events
v4 → v5    M4: memory_embeddings / vector metadata
```

M4 migration must create the v5 structural table even if the optional vector dependency is not installed. Schema version represents DB shape, not runtime feature availability.

### Query embedding contract

Correct semantic retrieval path:

```text
record/index path
  generate + persist record embeddings

query path
  generate exactly one query embedding
  do NOT regenerate corpus embeddings
  do NOT run backfill / rebuild / training
```

A deterministic resumable backfill is required for L1 records created before vector support was enabled.

### Evidence revision binding

`revision_set` is not one asset hash. It is an evidence-specific multi-element revision binding.

Examples:

```text
asset fact          → asset Revision
Policy-derived rule → Policy digest
Impact result       → index generation + relevant asset revisions
Change Set fact     → checkpoint / Change Set revision set
P4 observation      → provider observation / head revision when available
supersession        → both ends of the replaced-value revision chain
```

If any bound element changes, the memory record becomes stale.

Do not implement M3 from an old single-SHA interpretation.

### M6 dependency

M6 symbolic context compression is optional and requires both:

```text
M5 complete
W5 real-project data available
```

Do not start M6 from M5 alone.

## 14. Track C — P4 / Source Control Awareness

Initial strategy remains conservative:

```text
UE Source Control Provider first
read-only observation first
conflict preflight before write
no automatic checkout
no automatic submit
```

### C1-0 capability probe

Before freezing the public C1 result schema, probe actual UE5.6 + Perforce capabilities.

Classify each desired field as:

```text
generic
provider-specific
unavailable
```

Fields of interest include:

```text
checkedOutBy
locked / lockedBy
depotPath
headRevision
haveRevision
changelist
```

Bounded exit rule:

- unavailable/unproven fields become explicitly nullable with documented reason;
- C1 must not block forever waiting for a perfect environment.

### Naming / ownership boundary

Correct tool naming:

```text
ue_get_asset_checkout_state
```

Do not restore `ue_get_asset_ownership` for observed checkout data.

Checkout/lock history is not evidence that a person is the owner/maintainer of an asset or directory.

Automatic durable ownership claims may only come from explicit project/team configuration or user-confirmed evidence.

Do not store model-inferred ownership as `tool-observed`.

## 15. Track V — Read-only Knowledge Browser

V1 has no dependency on T0/W4 and can run independently if explicitly prioritized.

Permanent architectural constraint:

```text
Web UI is read-only
knowledge DB manual editing is not provided
writes remain Agent-controlled
```

Planned shape:

```text
V1 local browser
  Python stdlib HTTP + sqlite3 mode=ro
  localhost only
  no FastAPI/uvicorn runtime dependency
  no npm build requirement

V2 visualization
  asset/reference/impact/knowledge/stale/change-set views
```

Track V is not the current mainline priority; W4 remains primary.

## 16. Track X — Medium-term Capability Expansion

Track X starts only after:

```text
W4 complete
D1 complete
```

Correct dependency structure is three independent branches, not one serialized chain:

```text
Branch A
X1 Widget Blueprint read
→ X2 Anim Blueprint deeper read
→ X3 Graph Writer + real demand gate

Branch B
X4 Level Actor
→ X6 Asset Performance

Branch C
X5 C++ Symbols
```

X1, X4, and X5 are independent after W4 + D1.

Do not make X4/X5/X6 blockers for X3 unless new concrete technical evidence creates a dependency.

Generic Blueprint Graph CRUD remains demand-gated; do not open arbitrary Graph mutation merely to increase Tool count.

## 17. Code Areas Relevant to the Current Mainline

### `src/ue_agent_kit/patches.py`

Owns:

```text
OperationSpec
OPERATION_REGISTRY
LIVE_WRITE_OPERATION_REGISTRY
Policy schema / hard Policy validation
validate_patch()
```

W4 has its own explicit 4-operation allowlist. Do not infer W4 support from all live-write registry entries.

### `src/ue_agent_kit/agent_workflow.py`

Still a major orchestration hotspot.

Important current reusable APIs:

```text
plan_patch()
discard_unconsumed_plans()
bind_asset_for_batch()
live_write_stable_target_key()
resident live apply / Fast Verify / checkpoint primitives
```

Avoid adding an entire new W4 state machine here if a bounded domain module is cleaner.

### `src/ue_agent_kit/bounded_batch.py`

W4-1 domain owner for immutable bounded batch planning.

Do not duplicate this logic in MCP registration code.

### `src/ue_agent_kit/change_sets.py`

Current Change Set schema:

```text
schemaVersion = 2.0
MAX_CHANGE_SET_RECEIPTS = 100
```

Current operation lifecycle includes applied / saved / verified / superseded / failed / unknown etc.

W4 batch metadata should reuse/reference the existing operation lifecycle instead of inventing a competing per-operation state machine.

### `src/ue_agent_kit/animation_scale_fix_batch.py`

Contains a proven immutable batch-plan / child-plan cleanup pattern.

It is a pattern reference only. Do not couple generic W4 semantics to the animation-specific service.

## 18. Non-negotiable Safety / Product Rules

Do not weaken these to make W4 easier:

```text
Policy validation
Revision freshness
Dirty-package fail-closed behavior
exact Editor session binding
exact previousTransactionId continuation
one-time / exact confirmations where required
independent Strong Verify
truthful partial state
exact recovery ordering
no arbitrary shell / Unreal command injection
no generic UObject method execution
no automatic save outside explicit authorization
no cross-package atomicity claim
```

Recovery rule:

If an unrelated user transaction is above the expected UE transaction, fail closed. Never skip over it to undo an older UEAgentKit transaction.

## 19. Testing / Build Rules

Use the repository runner / supported modern Python, not system Python 3.9.

Typical Python gate:

```text
scripts\python.cmd
```

Current branch discovered suite at `71400c9`:

```text
729 / 729
```

Never encode this number as a permanent future expected count. Future stages must use actual discovered tests and record the count in their RESULT document.

For C++ changes:

- ensure no conflicting UE/build process is running;
- run the UE5.6 Direct Build gate;
- preserve existing C++ warnings as non-blocking unless a new error appears.

For no-C++ stages such as W4-1, a new Direct Build is not mandatory if the last required C++ baseline remains valid.

Always include:

```text
Ruff
Python full discovered suite
compileall
git diff --check
ValidateRelease 0.7.0
```

plus real UE acceptance when the phase mutates or verifies resident/disk state.

## 20. Repo / Agent Operating Rules

Before any change:

```text
1. inspect git status
2. inspect latest commit
3. read this handoff
4. read Plans/README.md
5. read the current stage Detailed Plan / previous Result
```

Do not:

```text
git reset / clean another Agent's files
revert unrelated work
rebase shared work
push
create Tag / Release
change published version
commit unless the user explicitly authorizes it in that Chat
run overlapping Unreal/build/test workloads without checking process state
```

If another Agent is running UE tests/builds, do not start a concurrent conflicting Unreal process.

Prefer small checkpoint commits when explicitly authorized, so regressions can be bisected by phase.

## 21. Current Working Tree at Handoff Creation

Immediately before creating this project-level handoff:

```text
HEAD = 71400c9
working tree = clean
```

This document and any navigation note added alongside it are documentation-only changes made after that clean checkpoint.

No product code is being modified by this handoff task.

## 22. Exact Next Takeover Procedure

A fresh Chat / Agent should do the following:

```text
1. Read this file.
2. Read docs/Plans/README.md.
3. Read W4 parent Detailed Plan.
4. Read final W4 Result.
5. Read final D1 Result.
6. Inspect actual git status / HEAD; do not assume they are unchanged.
7. Confirm W4 and D1 are complete and select the next post-D1 item from the Master Plan (W5 per dependency order).
8. Do not begin W5/Memory implementation until this file and README show W4 and D1 complete.
9. Run relevant regression gates before starting new work.
```

The immediate implementation objective after D1 is therefore:

```text
post-W4/post-D1 sequence defined by the Master Plan
→ W5 real-project acceptance
```

## 23. Key Documents

Current project / execution:

```text
docs/Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
docs/Plans/README.md
docs/Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
docs/Plans/UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
docs/Plans/UEAGENTKIT_MASTER_PLAN_CORRECTION_NOTES_20260827.md
```

Writer current chain:

```text
docs/Plans/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md
docs/Plans/UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md
docs/Plans/UEAGENTKIT_D1_AGENT_WORKFLOW_SPLIT_DETAILED_PLAN_20260829.md
docs/Plans/UEAGENTKIT_D1_AGENT_WORKFLOW_SPLIT_RESULT_20260829.md
docs/Plans/UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_RESULT_20260827.md
```

Historical closeout:

```text
docs/Handoffs/UEAGENTKIT_0_8_CAPABILITY_CLOSEOUT_HANDOFF_20260823.md
docs/Plans/UEAGENTKIT_0_8_RELEASE_REVIEW_20260823.md
docs/Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md
```

Treat current repository facts and these documents as authoritative over stale conversation memory.

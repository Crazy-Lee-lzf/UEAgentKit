# UEAgentKit W4-5 Aggregate Strong Verify / Semantic Diff / Trust Detailed Plan

> Date: 2026-08-28
>
> Branch: `feature/live-writer-expansion`
>
> Entry implementation checkpoint: `d277369` (`feat: add W4-4 multi-asset checkpoint save`)
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`
>
> Previous result: `UEAGENTKIT_W4_4_MULTI_ASSET_CHECKPOINT_SAVE_RESULT_20260828.md`
>
> Scope: consume a fully saved W4-4 checkpoint set, independently Strong Verify each child W3 checkpoint, close the bounded existing Verification Plan obligations, and produce aggregate Semantic Diff / Trust evidence. Do not implement rollback/recovery hardening or restart recovery of incomplete verification; those remain W4-6.

## 1. Goal

W4-5 completes the verification half of the bounded W4 workflow:

```text
saved ChangeSetCheckpointSetRecord
→ verify child W3 checkpoints independently
→ obtain verified-stage Semantic Diff over the complete Change Set
→ build existing Verification Plan
→ execute only the exact bounded Required validation actions needed by this W4 scope
→ evaluate existing Trust verdict
→ persist aggregate verification evidence
```

Primary successful slice:

```text
BP_TransactionBlueprint
  variable + component + pin

DA_TransactionAsset
  setAssetProperty

2 saved child checkpoints
→ 2 independent child Strong Verifies
→ complete Semantic Diff
→ required BP Compile / asset Data Validation evidence
→ Trust = verified
```

The feature is orchestration. Do not weaken or replace W3 Strong Verify, R2 Semantic Diff, Verification Plan, or Trust rules.

## 2. Entry Preconditions

Before implementation:

```text
inspect actual Git status / HEAD
read docs/Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
read docs/Plans/README.md
read W4 parent Plan
read W4-4 Result
read W3 Checkpoint Strong Verify Result
```

Expected entry checkpoint is `d277369`, but actual repository state is authoritative.

Required W4-4 input state:

```text
checkpoint set exists and passes tamper/integrity validation
checkpoint set Save state = saved
savedCount == child checkpoint count
no pending/failed asset remains
all child checkpoint IDs and afterRevisions remain bound to the set
```

`partially_saved`, `failed`, incomplete, or tampered checkpoint sets must not enter aggregate verification.

## 3. Public Product Surface

Add one high-level Tool:

```text
ue_verify_change_set_checkpoint(checkpoint_set_id)
```

The Tool is read/verification orchestration over already-authorized persisted state. It performs no Save and no mutation of asset values.

Existing low-level Tools remain available and unchanged:

```text
ue_verify_live_write_checkpoint
ue_analyze_semantic_diff
ue_build_verification_plan
ue_evaluate_trust_verdict
ue_compile_blueprint
ue_validate_asset
```

Do not add a generic multi-asset Commandlet/export endpoint in W4-5. Initial Strong Verify cost remains bounded per child asset.

## 4. Core Safety Contract

Aggregate verification may be `verified` only if all of the following are true:

```text
1. checkpoint set is fully saved and untampered
2. every required child W3 checkpoint independently verifies
3. verified-stage Semantic Diff covers the exact full Change Set asset scope
4. no required expected change is missing
5. no unexpected semantic change is present
6. Verification Plan Required assertions are closed with applicable evidence
7. existing ue_evaluate_trust_verdict returns verdict.state = verified
```

No aggregate wrapper may override an existing child failure or Trust result.

In particular:

```text
resident Fast Verify != Strong Verify
one verified child != aggregate verified
Semantic Diff complete != Trust verified by itself
Trust insufficient-evidence/suspicious/failed != aggregate verified
```

## 5. Why Verification Plan Closure Is Required

Current Trust rules require more than persistence and Semantic Diff.

For the W4 operation allowlist, current deterministic Required obligations include:

```text
all written assets
  persistence
  semantic
  freshness
  Data Validation

Blueprint narrow writes
  explicit Blueprint Compile
```

Therefore W4-5 must not claim C9 `Trust=verified` after only child checkpoint Strong Verify.

After all child Strong Verifies pass, W4-5 should reuse the existing Verification Plan and execute only its exact bounded Required validation actions that are applicable to the frozen W4 scope.

For the initial C9 fixtures this means:

```text
BP_TransactionBlueprint
  ue_compile_blueprint
  ue_validate_asset

DA_TransactionAsset
  ue_validate_asset
```

Do not automatically run arbitrary caller-named Automation Tests, arbitrary extra assets, or unrelated validation actions. W4-5 has no new generic validation authority.

If the existing Verification Plan later produces a Required action outside the explicitly supported W4-5 closure set, fail closed with `insufficient-evidence` and return that exact action instead of silently broadening execution authority.

## 6. Aggregate Verification Persistence

Extend the durable W4 checkpoint-set evidence rather than inventing a competing Change Set lifecycle.

Recommended durable addition to `ChangeSetCheckpointSetRecord`:

```text
verification
  state
  startedAtUtc
  updatedAtUtc
  completedAtUtc
  childResults[]
    assetPath
    checkpointId
    state
    verified
    verificationKind
    strongVerificationRevision
    artifact / evidence reference
    failure
  verifiedCount
  failedCount
  semanticDiff
    evidenceStage
    summary
    analysisGapCount
    unexpectedChangeCount
  verificationPlan
    planId
    planFingerprint
    requiredAssertionCount
  validationActions[]
    tool
    subject
    state
    evidenceId
    failure
  trust
    state
    reasonCodes
    statement
    verifiedAssets[]
  failureBoundary
```

Keep W4-4 Save truth intact:

```text
savedCount / persistedAssets / afterRevisions remain authoritative
```

Do not rewrite or hide a successfully saved checkpoint merely because verification later fails.

Recommended verification states:

```text
pending
verifying
verified
partially_verified
failed
stale
```

Interpretation:

```text
verified
  = every child verified + complete semantic evidence + final Trust verified

partially_verified
  = at least one child independently verified but aggregate closure failed

failed
  = verification attempted but no child reached verified, or deterministic required evidence failed

stale
  = current persisted Revision invalidates checkpoint applicability
```

The exact persisted representation may use a nested `verification.state`; avoid breaking W4-4 `state=saved` replay/idempotence semantics unnecessarily.

## 7. Child Strong Verify Algorithm

Verify child checkpoints in frozen checkpoint-set asset order.

For each child:

```text
load exact child checkpoint ID from checkpoint set
→ ensure child asset / checkpoint / saved Revision still match set binding
→ call existing W3 ue_verify_live_write_checkpoint behavior internally
→ persist exact child result before proceeding
```

Strong Verify remains one independent artifact per effective asset.

Initial cost contract:

```text
new Strong Verify child Unreal processes <= number of not-yet-verified child checkpoints
```

For first-time C9 with BP + DA:

```text
expected Strong Verify child Unreal = 2
```

Already verified child checkpoints remain idempotent and should not be re-exported solely because the aggregate Tool is called again.

Do not combine BP and DA into one new Commandlet optimization in W4-5.

## 8. Child Failure Behavior

A child failure does not erase truthful evidence from previously verified children.

Example:

```text
BP child Strong Verify PASS
DA child canonical mismatch FAIL
```

Required aggregate truth:

```text
verifiedCount         = 1
BP child              = verified
DA child              = saved / unverified with exact mismatch failure
aggregate             != verified
verification.state    = partially_verified
```

Continue only read-only evidence aggregation as needed to produce a truthful Semantic Diff / Trust failure result. Never upgrade the failed child.

If a child is disk Revision stale, W3 must fail before its independent export as today.

## 9. Semantic Diff Contract

After child verification attempts, run existing Semantic Diff against the exact Change Set.

Successful aggregate closure requires:

```text
stage = verified
exact full Change Set asset scope
returned asset count == expected asset count
no semantic-diff truncation
no stale revision gap
no missing expected change
no unexpected change
```

Do not construct a new W4 semantic comparison implementation. Reuse:

```text
ue_analyze_semantic_diff(change_set_id, stage="verified", ...)
```

The high-level result should persist/return a compact summary and stable evidence identifiers, not duplicate the full Semantic Diff payload unless needed for diagnostics.

If verified-stage evidence is incomplete because one child failed, preserve that as incomplete/insufficient evidence. Do not fall back to resident Fast Verify as a substitute.

## 10. Verification Plan and Bounded Validation Actions

Build the existing plan for the exact Change Set:

```text
ue_build_verification_plan(change_set_id)
```

Use its Required assertions as authority.

W4-5 may automatically close only a narrow explicit action allowlist:

```text
ue_compile_blueprint
ue_validate_asset
```

and only when the action subject is an exact asset already bound to the checkpoint set / Change Set.

Rules:

```text
execute Required actions only
execute each exact action at most once per aggregate verification attempt
capture existing verification evidence normally
stop/fail closed on action failure
never invent successful evidence
never execute arbitrary Automation Tests or arbitrary additional assets
```

If child Strong Verify is incomplete, do not run mutating-looking or unnecessary validation actions merely to chase a verified verdict. The persisted failure should remain the primary blocker.

## 11. Trust Contract

Final Trust must come from existing:

```text
ue_evaluate_trust_verdict(change_set_id)
```

W4-5 does not define a parallel Trust algorithm.

Aggregate mapping:

```text
Trust verified
  + all child Strong Verify PASS
  + Semantic Diff complete
  -> verification.state = verified

Trust failed / insufficient-evidence / suspicious
  -> aggregate not verified
```

Preserve exact Trust fields:

```text
state
reasonCodes
statement
verifiedAssets
unresolved risk count
analysis gap count
unexpected change count
```

Do not collapse `failed`, `suspicious`, and `insufficient-evidence` into one misleading generic error in the public evidence.

## 12. Same-target Supersession

W4-5 must preserve W3 supersession semantics in multi-asset verification.

Example C12:

```text
BP target X
  write 10
  write 20
  write 42

DA normal write
```

Expected:

```text
all requested writes remain audit-visible
BP effective write count for target X = 1
BP superseded write count = 2
only final X=42 participates as effective persisted/verified value
DA write verifies normally
aggregate Semantic Diff reports the intended final value
aggregate Trust may verify when all required evidence passes
```

Never mark superseded operations as independently persisted/verified merely because their asset checkpoint verified.

## 13. Replay / Restart / Tamper

Required behavior:

```text
verified aggregate replay
  -> idempotent, no unnecessary child Unreal process

saved checkpoint set after MCP restart
  -> reload from disk and verify normally

partially_verified aggregate after MCP restart
  -> W4-5 may expose exact persisted state, but automatic resume/recovery hardening belongs to W4-6

checkpoint-set tamper
  -> fail closed

child checkpoint tamper/mismatch
  -> fail closed
```

W4-5 must persist enough aggregate evidence that W4-6 can later reason about incomplete verification without guessing.

Do not implement automatic resume logic beyond safe idempotent replay of already completed child verification.

## 14. Public Result Shape

Keep the Agent-facing result compact.

Recommended fields:

```text
checkpointSetId
changeSetId
state
assetCount
savedCount
verifiedCount
children[]
  assetPath
  checkpointId
  verified
  state
  verificationKind
  afterRevision
  strongVerificationRevision
  failure
semanticDiff
  stage
  verified
  missingExpectedCount
  unexpectedCount
  analysisGapCount
verificationPlan
  planId
  planFingerprint
  requiredAssertionCount
validationActions[]
  tool
  subject
  state
trust
  state
  reasonCodes
  statement
strongVerifyProcessCount
nextActions[]
```

Avoid embedding full independent artifacts or full Semantic Diff/Trust responses by default.

## 15. Deterministic Unit / Contract Tests

Minimum coverage:

```text
A1  fully saved 2-child set verifies in frozen asset order
A2  each child delegates to existing W3 checkpoint verify
A3  first-time two-child verify process bound <=2
A4  already verified child replay is idempotent
A5  fully verified aggregate persists verification.state=verified

B1  partially_saved checkpoint set rejected
B2  failed checkpoint set rejected
B3  checkpoint-set tamper rejected
B4  child checkpoint ID / asset / Revision mismatch rejected
B5  verified replay does not re-run unnecessary Strong Verify

C1  child 1 PASS, child 2 canonical mismatch -> partially_verified
C2  stale child -> aggregate not verified
C3  successful child evidence retained when another child fails
C4  failed child never upgraded in Change Set
C5  incomplete verified-stage Semantic Diff blocks aggregate verified

D1  Verification Plan built for exact Change Set
D2  bounded required BP Compile executed/captured
D3  bounded required Data Validation executed/captured
D4  unsupported Required action is returned, not auto-executed
D5  validation failure prevents aggregate verified
D6  final aggregate verdict uses existing Trust result exactly

E1  same-target superseded operations remain audit-visible
E2  only effective same-target value is independently verified
E3  W4-4 Save state/evidence remains backward compatible
E4  no Save / rollback occurs during W4-5
E5  existing low-level W3/R2/R3 APIs unchanged
```

Use private deterministic seams only for fault injection. Do not expose production parameters that fabricate verification success/failure.

## 16. Real UE5.6 Acceptance C9-C12

### C9 — two assets all verified

Fixture task:

```text
BP variable + component + pin
DA setAssetProperty
→ W4-3 Apply
→ W4-4 Preview + Commit
→ W4-5 aggregate verify
```

Required evidence:

```text
child Strong Verify BP          PASS
child Strong Verify DA          PASS
Strong Verify child Unreal      2 first verification
Semantic Diff stage             verified
Semantic Diff complete scope    PASS
required BP Compile             PASS
required BP Data Validation     PASS / valid or defined not-applicable behavior
required DA Data Validation     PASS / valid or defined not-applicable behavior
Trust verdict                   verified
aggregate verification          verified
```

### C10 — one canonical mismatch

Use the existing W3-style deterministic verification-artifact seam on one child while preserving the real saved Package Revision.

Required result:

```text
one child Strong Verify mismatch
failed child remains unverified
successful child retains verified evidence
aggregate != verified
Trust != verified
no false operation upgrade
```

### C11 — one disk Revision stale

After W4-4 Save, perform a controlled disk mutation on one child package.

Required result:

```text
stale child rejects before independent export
aggregate != verified
Trust != verified
exact expected/actual Revision reported
```

Restore the package byte-for-byte after evidence capture.

### C12 — same-target supersession inside multi-asset batch

Use:

```text
BP same target 10 -> 20 -> 42
DA normal write
```

Required result:

```text
BP superseded history preserved
only final BP value effective
both child checkpoints independently verify
Semantic Diff final intended values complete
Trust verified
aggregate verified
```

## 17. Failure Semantics

### F0 — reject before Strong Verify

Examples:

```text
checkpoint set not fully saved
tamper
child binding mismatch
```

Result:

```text
verifiedCount = 0
Strong Verify process count = 0
no verification upgrade
```

### F1 — first child verify fails

```text
verification.state = failed or stale as appropriate
verifiedCount = 0
remaining read-only diagnostics may be returned
aggregate Trust != verified
```

### F2 — later child verify fails

```text
verification.state = partially_verified
verifiedCount > 0
successful child evidence retained
failed child exact boundary retained
aggregate Trust != verified
```

### F3 — child verification succeeds but later validation/Trust closure fails

```text
children remain truthfully independently verified
aggregate verification != verified
Trust exact non-verified state retained
nextActions expose unresolved Required evidence
```

Do not downgrade valid child Strong Verify evidence merely because aggregate Trust is not closed.

## 18. Process / Side-effect Contract

W4-5 performs no package Save and no value mutation.

Expected side effects are verification-only:

```text
independent W3 child export processes
bounded exact Blueprint Compile / Data Validation actions required by Verification Plan
read-only Semantic Diff / Trust queries
aggregate evidence persistence
```

Strong Verify process count must be reported separately from any resident validation/Bridge calls.

No rollback is performed in W4-5.

## 19. Regression / Build Gates

Run:

```text
Ruff
Python full discovered suite
compileall
ValidateRelease 0.7.0
git diff --check
```

Record the actual discovered suite count; do not freeze `752` as a future constant.

Real UE C9-C12 are mandatory because this phase produces independent verification / Trust claims.

If no C++ changes are made, a new UE5.6 Direct Build is not required solely for W4-5. If C++ changes become necessary, first check for conflicting UE/build processes and run Direct Build.

## 20. Fixture Recovery

After C9-C12:

```text
restore any controlled disk mutation byte-for-byte
stop Editor as required by fixture procedure
run WriteFixturePlan Reset
independently verify exact BP + DA baseline
refresh frozen snapshot if the established acceptance procedure requires it
```

Do not use the final fixture Reset as evidence that a failed aggregate verification was actually verified.

## 21. Stop Conditions

Stop and diagnose before continuing if:

```text
aggregate verification requires weakening W3 independent Strong Verify
Trust must be bypassed/overridden to obtain verified
verified-stage Semantic Diff cannot cover the complete Change Set
W4-5 requires arbitrary validation action execution
superseded writes become falsely marked verified
one child failure causes truthful successful child evidence to be erased
checkpoint-set Save truth is overwritten by verification failure
resident Fast Verify is used as independent persistence evidence
cross-asset verification is presented as one atomic artifact
fixture cannot be restored exactly
```

## 22. Recommended Implementation Order

```text
1. extend checkpoint-set durable verification subrecord/state
2. add ue_verify_change_set_checkpoint entry point
3. child W3 Strong Verify orchestration + idempotent replay
4. exact partial verification boundary persistence
5. verified-stage Semantic Diff aggregation
6. Verification Plan integration
7. bounded Compile/Data Validation closure
8. existing Trust evaluation integration
9. same-target effective/superseded aggregation
10. unit/fault-injection tests
11. full Python/release gates
12. real UE C9
13. real UE C10/C11
14. real UE C12
15. exact fixture recovery
16. W4-5 Result document
```

Do not start W4-6 recovery/restart hardening in the same implementation step.

## 23. Exit Gate

W4-5 is complete only when:

```text
[ ] ue_verify_change_set_checkpoint exists
[ ] only fully saved checkpoint sets may enter verification
[ ] each child reuses existing W3 independent checkpoint Strong Verify
[ ] child verification is durable and idempotent
[ ] partial child success is represented truthfully
[ ] verified-stage Semantic Diff covers the complete Change Set
[ ] existing Verification Plan is used
[ ] only bounded exact Compile/Data Validation actions are auto-closed
[ ] existing Trust evaluator is authoritative
[ ] aggregate verified requires Trust=verified and every child verified
[ ] stale/mismatch child prevents aggregate verified
[ ] same-target supersession remains correct and audit-visible
[ ] no package Save occurs
[ ] no rollback occurs
[ ] C9 real two-asset aggregate Trust verified PASS
[ ] C10 canonical mismatch fail-closed PASS
[ ] C11 disk Revision stale fail-closed PASS
[ ] C12 multi-asset supersession PASS
[ ] fixture exact recovery PASS
[ ] Ruff / Python / compileall / ValidateRelease / git diff --check PASS
[ ] UE5.6 Direct Build PASS if C++ changed
```

Only after this Exit Gate is green may W4-6 Recovery and Restart Hardening begin.

# UEAgentKit W3 Checkpoint Strong Verify Detailed Plan

> Date: 2026-08-25
>
> Implementation line: `feature/live-writer-expansion`
>
> W2 accepted checkpoint: `31f0faa` (`test/docs: close W2 fast resident verify acceptance`)
>
> W1 accepted checkpoint: `8bede6f` (`test/docs: close resident blueprint writer W1 acceptance`)
>
> Parent plans/results:
>
> - `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_W1_DETAILED_PLAN_20260823.md`
> - `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_BASELINE_20260823.md`
> - `UEAGENTKIT_W2_FAST_RESIDENT_VERIFY_DETAILED_PLAN_20260824.md`
> - `UEAGENTKIT_W2_FAST_RESIDENT_VERIFY_RESULT_20260824.md`
>
> Scope: W3 Checkpoint Strong Verify only.
>
> Explicitly out of scope: W4 general multi-operation/batch UX, Generic Blueprint Graph CRUD, new writer families, R5, Performance P1-P5, source-control collaboration, release/version/tag/push work.

## 1. Goal

W1 made narrow writes resident and compile-safe.

W2 made current-session verification resident:

```text
Live Apply
→ Fast Resident Verify
```

with:

```text
UnrealEditor-Cmd.exe starts = 0
```

The remaining high-cost part is checkpoint persistence and independent verification.

Current single-operation flow is mechanically:

```text
Live Apply
→ Fast Resident Verify
→ Authorized Save
    → resident Editor save
    → independent RunAssetCatalog export
→ ue_verify_live_write
    → another independent export/reload
→ Semantic Diff
→ Verification Plan
→ Trust
```

For Blueprint operations, the second export is specifically:

```text
RunExport.ps1 -Profile full
```

because full Blueprint canonical data is required for exact variable/component/pin verification.

W3 must reduce this to:

```text
one asset checkpoint
→ Authorized Save without independent export
→ one Strong Independent Verify export
→ verify all effective writes covered by that checkpoint
→ Semantic Diff
→ Verification Plan
→ Trust
```

Primary performance target:

```text
checkpoint Save child Unreal processes   = 0
checkpoint Strong Verify child processes = 1
total checkpoint strong path             = 1
```

The optimization must not weaken evidence strength.

## 2. Confirmed Current Implementation Facts

### 2.1 `ue_save_authorized_asset`

Current Commit path:

```text
Policy / Revision / Editor Session checks
→ backup exact package
→ editor.saveAuthorizedAsset
→ compute after SHA-256 disk Revision
→ _export_refresh_candidate()
→ RunAssetCatalog.ps1
→ require exported canonical Revision == disk SHA
→ mark live write saved
```

The embedded export is independent Unreal work.

It currently proves useful Revision/identity facts, but for Blueprint exact write verification it is not the final strong semantic proof used by W1/W2.

### 2.2 `ue_verify_live_write`

Current saved Blueprint path:

```text
RunExport.ps1
  -Profile full
  -IncludeUnchangedDefaults
→ exact canonical asset
→ exact disk Revision
→ extract exact persisted value
→ compare with LiveApplyRecord.after_value
→ mark record verified
```

Non-Blueprint live writes currently use:

```text
RunAssetCatalog.ps1
```

for the independent verification export.

### 2.3 Current duplicate work

A normal saved live write therefore pays for:

```text
Save Commit independent export   = 1 child Unreal process
Strong Verify independent export = 1 child Unreal process
```

Total:

```text
2 independent child Unreal process starts
```

W3 should make the checkpoint path use one.

### 2.4 Current lifecycle is operation-oriented

`LiveApplyRecord` currently tracks:

```text
receipt
asset_path
operation
target
before_value
after_value
editor_session_id
transaction_id
saved
save_receipt
verified
```

Change Set operations independently progress through states such as:

```text
applied
saved
verified
no-op
undone
discarded
failed
unknown
```

W3 must preserve per-operation auditability even when one independent canonical export covers multiple effective writes.

## 3. Core W3 Design Decision

### 3.1 Keep existing immediate behavior compatible

Do not silently change all existing `ue_save_authorized_asset` calls.

Recommended extension:

```text
verification_mode = immediate | checkpoint
```

Default:

```text
immediate
```

`immediate` retains current behavior and compatibility.

`checkpoint` is the new W3 optimized path.

Conceptually:

```text
verification_mode=immediate
  Save Commit
  → embedded independent export
  → existing ue_verify_live_write

verification_mode=checkpoint
  Save Commit
  → resident save
  → disk SHA / persistence evidence only
  → NO independent export
  → ue_verify_live_write_checkpoint
       → ONE independent export
       → verify checkpoint coverage
```

Do not change the existing `ue_verify_live_write` strong semantics.

### 3.2 Checkpoint is asset-scoped in W3

W3 checkpoint identity is:

```text
one fixed project
+ one asset/package
+ one Change Set
+ one final disk Revision
+ one bounded effective operation set
```

A multi-asset Change Set therefore has multiple asset checkpoints.

Example:

```text
Change Set CS-1
  Asset A checkpoint
  Asset B checkpoint
```

W3 does **not** promise one Unreal process for an arbitrary multi-asset Change Set.

That broader batch UX/process coalescing belongs to W4 or later.

### 3.3 Why asset-scoped

One canonical asset revision is an unambiguous independent verification unit.

It gives a safe binding:

```text
assetPath
+ package Revision
+ canonical export
+ effective expected operations
```

without requiring atomic multi-package save semantics.

## 4. Evidence Ladder After W3

The Trust ladder remains:

```text
write
→ resident Fast Verify
→ authorized checkpoint save
→ independent checkpoint verify
→ verified Semantic Diff
→ Verification Plan
→ Trust Verdict
```

Evidence strengths remain mechanically different:

```text
resident-fast
persisted-action
independent-verified
```

### `resident-fast`

Proves current Editor-session state only.

### `persisted-action`

Checkpoint Save proves:

```text
authorized exact asset save occurred
backup exists
before disk Revision known
after disk Revision known
exact package persisted
```

It does not prove independent semantic reload.

### `independent-verified`

Checkpoint Strong Verify proves:

```text
fresh independent Unreal load/export
exact asset identity
exact checkpoint disk Revision
canonical persisted state
all effective checkpoint operations match expected final state
```

## 5. New Checkpoint Record

Introduce a durable, bounded record such as:

```text
LiveWriteCheckpointRecord
```

Recommended fields:

```text
checkpointId
schemaVersion
projectName
changeSetId
assetPath
assetClass
packageName

state
createdAtUtc
savedAtUtc
verifiedAtUtc

editorSessionIdAtPrepare
editorProcessIdAtPrepare

beforeDiskRevision
afterDiskRevision

saveReceipt
backupManifestPath / backupManifestId

includedReceipts
effectiveReceipts
supersededReceipts

effectiveOperations[]
  receipt
  operation
  valueKind
  stableTargetKey
  target
  expectedValue
  transactionId
  appliedAtUtc

strongVerificationKind
strongVerificationReportId
strongArtifactRoot
strongArtifactDigest / bounded integrity metadata
```

Allowed checkpoint states should remain narrow, for example:

```text
prepared
saved
verified
failed
stale
```

Do not store raw C++ pointers or transient Editor object addresses.

## 6. Stable Target Key and Final-State Coalescing

This is a hard correctness requirement.

Repeated resident editing may write the same target more than once before checkpoint save.

Example:

```text
write TransactionInt = 10
write TransactionInt = 20
write TransactionInt = 42
Save
```

Disk state can only persist:

```text
TransactionInt = 42
```

It is impossible for the final canonical asset to independently prove that all three intermediate values are persisted simultaneously.

Therefore W3 must distinguish:

```text
audit history
vs
effective persisted final state
```

### 6.1 Group by stable target identity

Use the existing deterministic target-key rules.

Examples:

```text
setVariableDefault
  blueprint-variable:<variableName>

setComponentProperty
  blueprint-component:<componentName>:<propertyPath>

setPinDefault
  blueprint-pin:<graphGuid>:<nodeGuid>:<pinName>

setAssetProperty
  stable asset property target key
```

### 6.2 Effective operation

For multiple pending writes to the same exact stable target:

```text
latest applicable write = effective operation
earlier applicable writes = superseded audit history
```

Checkpoint Strong Verify compares the canonical value only to the latest effective write.

### 6.3 Superseded state

Recommended explicit operation state:

```text
superseded
```

with:

```text
supersededByReceipt
checkpointId
```

A superseded write is:

```text
terminal for persistence accounting
not independently persisted as its intermediate afterValue
still retained for audit/history
```

Change Set derivation should treat `superseded` similarly to a neutral terminal state, not as verified persistence.

Do not falsely mark superseded intermediate writes as `verified`.

### 6.4 If adding `superseded` is too invasive

Fail closed instead of lying.

A temporary W3 fallback may reject a checkpoint containing multiple active writes to the same stable target with:

```text
checkpoint-target-supersession-unsupported
```

But this fallback should be temporary because repeated same-target editing is a normal resident workflow.

Preferred W3 completion includes explicit supersession semantics.

## 7. Checkpoint Preview

Recommended surface:

```text
ue_save_authorized_asset
  mode=Preview
  change_set_id=<id>
  verification_mode=checkpoint
```

W3 checkpoint mode should require a `change_set_id`.

Reason:

```text
a multi-write checkpoint must have an exact bounded operation membership
```

Preview must resolve:

```text
exact Change Set
exact selected asset
all active applicable live receipts for this asset
effective operation set
superseded set
current Editor Session
current disk Revision
current Dirty state
```

### 7.1 Final resident preflight

Before issuing the one-time save receipt, Preview should prove each effective write is still valid.

Reuse W2 resident verification mechanics:

```text
exact receipt
exact session
exact Change Set
exact stable target re-resolve
current resident value == effective expected afterValue
Blueprint compile state non-error when applicable
```

This must start:

```text
0 child Unreal processes
```

### 7.2 Preview receipt binds exact checkpoint intent

The one-time Save authorization must bind:

```text
assetPath
assetClass
packageName
changeSetId
editorSessionId
editorProcessId
beforeDiskRevision
exact included receipt set
effective receipt set
superseded receipt set
effective operation digest
verification_mode=checkpoint
```

If any of these change before Commit:

```text
save-receipt-stale
```

or a checkpoint-specific stable error.

## 8. Checkpoint Save Commit

Commit keeps the existing exact confirmation:

```text
SAVE <saveReceipt>
```

Before writing:

```text
re-check exact Editor Session
re-check disk Revision
re-check package Dirty
re-check Change Set membership
re-run bounded resident verification for effective writes
re-check checkpoint operation digest
```

Then:

```text
backup exact package
→ editor.saveAuthorizedAsset
→ compute after disk SHA-256
→ inspect expected clean saved package state
```

### 8.1 Critical W3 difference

For:

```text
verification_mode=checkpoint
```

do **not** call:

```text
_export_refresh_candidate()
RunAssetCatalog.ps1
RunExport.ps1
```

inside Save Commit.

Checkpoint Save must therefore launch:

```text
0 child UnrealEditor-Cmd.exe
```

### 8.2 Save result

Return explicit persistence-only evidence:

```text
saved = true
verified = false
verificationKind = persisted-action
checkpointId
beforeRevision
afterRevision
includedReceiptCount
effectiveReceiptCount
supersededReceiptCount
nextAction = ue_verify_live_write_checkpoint
```

Do not return a field that can be misread as independent verification success.

## 9. Marking Operations Saved

After checkpoint Save:

- every effective included write belongs to the same saved asset Revision;
- superseded writes are terminal audit history;
- no effective write is `verified` yet.

Recommended operation transitions:

```text
applied → saved
applied → superseded
```

for effective and superseded operations respectively.

Every effective saved operation should record:

```text
checkpointId
saveReceipt
savedRevision = checkpoint.afterDiskRevision
```

The Change Set may be:

```text
saved
partially_applied
partial
```

depending on other assets/operations.

Do not close a multi-asset Change Set merely because one asset checkpoint was saved.

## 10. New Strong Checkpoint Verify API

Recommended explicit Tool:

```text
ue_verify_live_write_checkpoint
```

Input:

```text
checkpoint_id
```

Optional bounded compatibility fields may include:

```text
change_set_id
asset_path
```

but the checkpoint record remains authoritative.

Do not ask the caller to resupply arbitrary expected values.

## 11. Strong Verify Preconditions

A saved checkpoint may be verified even if the original resident Editor session is gone.

This is intentional.

Required checks:

```text
checkpoint exists
checkpoint.state == saved
backup/save identity valid
afterDiskRevision is a valid SHA-256 Revision
current disk Package SHA == checkpoint.afterDiskRevision
Change Set / operation records still bind to checkpoint
no effective receipt already redirected to another checkpoint
```

Unlike W2 Fast Verify:

```text
Editor Session restart does NOT invalidate a saved checkpoint
```

because the strong verification authority is disk Revision + independent reload.

### 11.1 Disk changed after save

If:

```text
current disk Revision != checkpoint.afterDiskRevision
```

return:

```text
checkpoint-revision-stale
```

Do not export and do not verify against a newer package.

## 12. Exactly One Independent Export

For a saved checkpoint:

### Blueprint asset

Use one:

```text
RunExport.ps1
  -Profile full
  -IncludeUnchangedDefaults
```

### Non-Blueprint asset

Use one appropriate canonical export, currently:

```text
RunAssetCatalog.ps1
```

The export must produce exactly one canonical asset for the checkpoint asset.

Hard gate:

```text
Strong checkpoint verify child Unreal process count = 1
```

No per-operation Commandlet loop.

## 13. Independent Artifact Validation

The single export must validate:

```text
fixed project identity
exact assetPath
exact packageName
exact assetClass where applicable
canonical Revision available
canonical Revision clean
canonical Revision == checkpoint.afterDiskRevision
current disk SHA == checkpoint.afterDiskRevision
```

Only after the independent artifact passes identity/Revision checks should value coverage be evaluated.

## 14. Per-Operation Coverage From One Canonical

For each effective operation:

```text
canonical
→ operation-specific persisted-value extractor
→ expected exported value normalization
→ compare with checkpoint expected final value
```

Reuse existing W1/W2 normalization:

```text
string ↔ number/bool normalization
UE struct literal parsing
nested component property paths
parent component-field suppression in Semantic Diff
Blueprint pin canonical normalization
```

One canonical artifact can therefore cover:

```text
variable + component + pin
```

on the same Blueprint asset Revision if all are part of the same checkpoint.

## 15. Atomic Checkpoint Verification Rule

Recommended W3 safety behavior:

```text
validate artifact identity/revision
→ evaluate every effective operation
→ only if ALL effective operations match:
     mark checkpoint verified
     mark every effective operation verified
```

If any effective operation mismatches:

```text
checkpoint remains saved, not verified
no included effective operation is upgraded by this checkpoint attempt
return per-operation mismatch diagnostics
```

This avoids ambiguous partial checkpoint success.

Later work may support partial coverage if a real use case requires it.

W3 should prefer atomic checkpoint verification.

## 16. Successful Strong Verify Result

Recommended result:

```text
verificationKind = independent-verified
checkpointId
changeSetId
assetPath
afterRevision
independentReload = true
verified = true
effectiveOperationCount
verifiedOperationCount
supersededOperationCount
reportId
artifactRevision
childUnrealProcessCount = 1
```

Also return bounded per-operation coverage:

```text
receipt
operation
target
expectedValue
exportedValue
matched
```

Avoid dumping entire canonical JSON into the Tool result.

## 17. Independent Artifact Reuse

Once an exact checkpoint has successfully produced and validated its independent artifact:

```text
checkpointId
+ assetPath
+ afterRevision
+ export profile
```

form an immutable evidence identity.

Subsequent reads of the already-verified checkpoint must not launch another Unreal process.

Return the existing verified result/evidence.

If artifact persistence is used:

```text
store report/artifact path
store bounded SHA-256 integrity metadata
store exact Revision/profile identity
```

Do not reuse an artifact for:

```text
another Revision
another asset
another project
another checkpoint with incompatible effective operation coverage
```

## 18. Save Failure and Verify Failure Semantics

### Save failure before disk mutation

Return failure.

Checkpoint remains `prepared` or failed, depending on retry contract.

### Save succeeds, Strong Verify not yet run

State:

```text
saved
verified = false
```

This is a valid explicit state.

### Strong export process fails

State remains:

```text
saved
```

Do not auto-claim verification.

Offer retry if disk Revision remains exact.

### Canonical value mismatch

State remains:

```text
saved
```

Return:

```text
checkpoint-value-mismatch
```

Do not auto-rollback.

The package has genuinely been saved and must be treated as persisted-but-untrusted until the user/Agent resolves it.

### Disk changes before retry

Checkpoint becomes:

```text
stale
```

Strong verification must fail closed.

## 19. Rollback Boundary

W3 does not redesign rollback.

Checkpoint Save must preserve the existing backup manifest.

If a saved-but-unverified checkpoint needs rollback:

```text
use existing authorized rollback/recovery workflow
→ independently verify restored Revision
```

Do not add automatic rollback on Strong Verify mismatch.

Automatic rollback could overwrite a newer intentional disk state.

## 20. Interaction With Fast Verify

Normal iteration:

```text
Write A
→ Fast Verify A
→ Write B
→ Fast Verify B
→ Write C
→ Fast Verify C
```

At checkpoint Preview/Commit, W3 performs a final bounded resident revalidation of effective operations.

This prevents a stale Fast Verify from being treated as current solely because it succeeded earlier.

Fast Verify evidence remains:

```text
resident-fast
```

Checkpoint Save remains:

```text
persisted-action
```

Checkpoint Strong Verify becomes:

```text
independent-verified
```

## 21. Interaction With Semantic Diff

After checkpoint Strong Verify:

```text
Semantic Diff stage=verified
```

should consume the independently exported canonical artifact/evidence.

It must continue to detect unrelated persisted changes.

Strong checkpoint verification proves that expected effective targets match.

It does not by itself prove that no unrelated changes exist.

Therefore:

```text
Strong Verify
≠ whole-asset semantic approval
```

R2 Semantic Diff remains required where the Verification Plan requires it.

## 22. Interaction With Verification Plan / Trust

Trust must require the correct sequence.

Example persistence assertion:

```text
The saved Blueprint independently reloads with
TransactionInt=42 and RelativeLocation.X=10.
```

Satisfied only by:

```text
checkpoint independent-verified evidence
```

Not by:

```text
resident-fast
persisted-action
```

A checkpoint with:

```text
saved=true
verified=false
```

must keep the relevant Trust assertion open.

Only after Strong Checkpoint Verify and required Semantic Diff/other actions may Trust become:

```text
verified
```

## 23. Change Set Semantics

A Change Set can contain:

```text
multiple operations
multiple assets
no-op operations
superseded operations
saved asset checkpoints
verified asset checkpoints
remaining resident operations
```

W3 should derive Change Set state mechanically.

Recommended neutral terminal statuses:

```text
no-op
superseded
undone
discarded
```

Persistence-positive statuses:

```text
saved
verified
```

Do not count `superseded` as verified.

A Change Set is fully verified only when every non-neutral effective operation is verified.

## 24. Checkpoint Restart Semantics

### Before Save

A prepared checkpoint is resident/session-sensitive.

Editor Session change:

```text
prepared checkpoint = stale
```

Require new Preview.

### After Save

A saved checkpoint is disk-Revision-bound.

Editor restart is allowed.

Strong Checkpoint Verify may run later if:

```text
current disk Revision == checkpoint.afterDiskRevision
```

This is an important distinction from W2 resident evidence.

## 25. Concurrency / External Modification

W3 must fail closed if the package changes outside the checkpoint.

Check at minimum:

```text
Preview:
  current disk Revision

Commit before save:
  disk Revision still equals Preview Revision

After save:
  capture afterRevision

Strong Verify before process:
  disk Revision == afterRevision

Strong Verify canonical:
  canonical Revision == afterRevision

Strong Verify after process:
  current disk Revision still == afterRevision
```

If any comparison fails:

```text
checkpoint-revision-stale
```

or a more specific stable conflict code.

## 26. Scope Limits

W3 must not become a generic batch-save framework.

Do not implement:

```text
atomic multi-package save
Save All
generic asset lifecycle
generic Blueprint Graph CRUD
multi-user/source-control checkout automation
one commandlet for arbitrary project-wide Change Sets
W4 batch UX
```

Asset-scoped checkpoints are sufficient for W3.

## 27. Performance Baseline

Record the current path before product behavior changes.

At minimum measure:

### P0 — non-BP single write current path

```text
Fast Verify
→ Save Commit embedded export
→ Strong Verify export
```

Record:

```text
save_commit_ms
save_embedded_export_ms
strong_verify_ms
child Unreal starts
total checkpoint ms
```

### P1 — Blueprint variable current path

Same measurements.

### P2 — Blueprint multi-operation same asset

Create deterministic final-state sequence on one Blueprint, preferably using distinct targets:

```text
variable
component
pin
```

Record current practical closure cost/limitations.

Do not fabricate a baseline if current implementation cannot correctly close all three operations with one save. Record the limitation explicitly.

## 28. W3 Acceptance Performance Target

After implementation:

### Checkpoint Save

```text
child UnrealEditor-Cmd.exe starts = 0
```

### Strong Checkpoint Verify

```text
child UnrealEditor-Cmd.exe starts = 1
```

### Total optimized persistence/strong-verify path

```text
child UnrealEditor-Cmd.exe starts = 1
```

This is the hard process-count target.

Do not set an arbitrary absolute millisecond gate before measured results exist.

Report actual before/after elapsed times.

## 29. Real UE5.6 Acceptance Matrix

### C0 — non-BP scalar checkpoint

```text
Live Apply
→ Fast Verify
→ Checkpoint Save Preview/Commit
→ verify Save launches 0 child Unreal processes
→ Strong Checkpoint Verify
→ exactly 1 child Unreal process
→ canonical value match
→ Semantic Diff
→ Trust
→ exact fixture recovery
```

### C1 — Blueprint variable checkpoint

Same sequence.

Required:

```text
RunExport full for Strong Verify
exact variable default
exact disk Revision
Trust verified
```

### C2 — Blueprint multi-operation single asset

On `BP_TransactionBlueprint`, use distinct effective targets:

```text
setVariableDefault
setComponentProperty
setPinDefault
```

Flow:

```text
Apply variable
→ Fast Verify
→ Apply component
→ Fast Verify
→ Apply pin
→ Fast Verify
→ one checkpoint Save
→ one Strong Checkpoint Verify
```

Required:

```text
3 effective operations
1 saved Blueprint Revision
1 independent RunExport process
3 exact persisted value matches
all 3 operation records verified
Semantic Diff verified
Trust verified
```

This is the primary W3 acceptance case.

### C3 — same-target supersession

Example:

```text
TransactionInt = 10
TransactionInt = 20
TransactionInt = 42
```

Required:

```text
effective final expected value = 42
earlier writes retained as audit history
earlier writes not falsely marked persisted/verified
final write independently verified
Change Set can reach a correct terminal state
```

### C4 — verify after Editor restart

A saved checkpoint is independently verifiable after the original session is gone.

Do not stop a user-owned active Editor merely for this test.

If restart cannot be safely automated, cover record reload/session replacement through a deterministic integration seam and use the independent Commandlet for the actual Strong Verify.

### C5 — disk revision stale

```text
checkpoint Save
→ controlled fixture disk mutation
→ Strong Checkpoint Verify
→ checkpoint-revision-stale
→ no verified state
→ exact fixture recovery
```

### C6 — canonical mismatch

Use a deterministic test seam or controlled fixture.

Required:

```text
saved checkpoint
→ independent artifact valid Revision
→ one effective expected value mismatch
→ checkpoint remains saved/not verified
→ no operation upgraded to verified
```

## 30. Unit / Contract Tests

At minimum:

```text
[ ] immediate save mode remains default
[ ] immediate mode legacy behavior unchanged
[ ] checkpoint mode requires Change Set
[ ] checkpoint Preview binds exact receipt set
[ ] stale session rejects Preview/Commit
[ ] stale disk Revision rejects Commit
[ ] changed receipt membership rejects Commit
[ ] effective target coalescing
[ ] same-target supersession
[ ] checkpoint Save launches no export
[ ] checkpoint Save marks effective operations saved
[ ] superseded operations are not marked verified
[ ] checkpoint record persists
[ ] saved checkpoint survives service/session restart for Strong Verify
[ ] Strong Verify exact Revision gate
[ ] one export covers all effective operations
[ ] one mismatch prevents atomic verification
[ ] successful verify marks all effective operations verified
[ ] repeated verify is idempotent and starts no new process
[ ] Trust rejects persisted-action without independent verify
[ ] Semantic Diff uses verified canonical evidence
```

## 31. C++ Scope

W3 should be primarily Workflow/Python orchestration.

The resident Editor already knows how to:

```text
save exact authorized asset
Fast Verify exact resident writes
```

Do not add C++ merely to move orchestration into the plugin.

C++ changes are justified only if a real acceptance blocker requires an Editor-side fact that cannot be safely obtained through existing Bridge contracts.

If C++ changes occur:

```text
UE5.6 Direct Build is mandatory again.
```

If W3 remains Python/docs/tests only, retain the latest valid W2 Direct Build evidence and do not rebuild unnecessarily.

## 32. Strong Verification Artifact

Recommended output layout:

```text
Output/
  W3CheckpointVerify/
    <checkpointId>/
      manifest.json
      canonical/
      ...
```

Checkpoint record stores:

```text
reportId
artifactRoot
artifact profile
artifact Revision
bounded digest metadata
```

Do not commit Output artifacts.

Do not treat an unvalidated file in Output as Trust evidence merely because it exists.

## 33. Stable Error Codes

Recommended categories:

```text
checkpoint-invalid
checkpoint-not-saved
checkpoint-stale
checkpoint-session-stale
checkpoint-revision-stale
checkpoint-membership-changed
checkpoint-target-supersession-invalid
checkpoint-export-failed
checkpoint-export-invalid
checkpoint-value-mismatch
checkpoint-verification-failed
```

Use existing generic Workflow errors where they already precisely describe the condition.

Do not create duplicate codes without need.

## 34. Commit Discipline

Recommended checkpoints:

### W3-C0 — baseline / contract

```text
perf/docs: baseline checkpoint strong verify path
```

### W3-C1 — checkpoint persistence model

```text
feat: add live write asset checkpoint records
```

### W3-C2 — checkpoint save mode

```text
feat: add checkpoint authorized save mode
```

### W3-C3 — single-export strong verify

```text
feat: verify live write checkpoints with one export
```

### W3-C4 — supersession / Change Set closure

```text
feat: track effective checkpoint write coverage
```

Only if this is not already naturally part of C1/C3.

### W3-C5 — acceptance

```text
test/docs: close W3 checkpoint strong verify acceptance
```

If acceptance exposes a deterministic bug:

```text
1 evidence-backed gap
→ 1 focused fix
→ focused tests
→ real UE smoke
→ checkpoint commit
```

Do not hide product fixes in the final docs commit.

## 35. W3 Result Document

Create:

```text
docs/Plans/UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_RESULT_20260825.md
```

Record:

1. exact branch / tested commits;
2. current-path baseline;
3. checkpoint schema/identity;
4. immediate-mode compatibility;
5. checkpoint Save process count;
6. Strong Verify process count;
7. non-BP real UE result;
8. Blueprint single-op result;
9. Blueprint variable+component+pin one-checkpoint result;
10. same-target supersession result;
11. stale Revision result;
12. mismatch/fail-closed result;
13. Semantic Diff result;
14. Verification Plan / Trust result;
15. elapsed before/after;
16. full regression/build gates;
17. known limitations;
18. final W3 status.

## 36. Regression Gates

At final W3 checkpoint:

```text
Ruff
full discovered Python suite
compileall
JSON schema/example validation
PowerShell parser validation
real UE5.6 affected-domain smoke
git diff --check
UTF-8 no BOM / CRLF for changed text
tracked Output/Backups/Build/Saved = 0
```

UE5.6 Direct Build:

```text
required if C++ changed
otherwise latest valid W2 build evidence may be referenced
```

Do not hard-code the current 704 Python test count.

Record actual discovered/pass/fail/skip totals.

## 37. W3 Exit Gate

W3 may be marked complete only when all applicable hard gates close.

```text
[ ] current duplicate-export baseline recorded

[ ] immediate save behavior remains compatible
[ ] checkpoint mode is explicit
[ ] checkpoint mode does not silently become default

[ ] checkpoint identity binds one asset + one Change Set
[ ] checkpoint binds exact before disk Revision
[ ] checkpoint binds exact receipt/effective operation set
[ ] final resident preflight uses Fast Verify semantics
[ ] Preview/Commit stale membership fails closed

[ ] stable target-key coalescing works
[ ] same-target intermediate writes are not falsely verified
[ ] supersession semantics are auditable

[ ] checkpoint Save exact confirmation preserved
[ ] backup manifest preserved
[ ] checkpoint Save records after disk Revision
[ ] checkpoint Save produces persisted-action evidence only
[ ] checkpoint Save starts 0 child UnrealEditor-Cmd.exe

[ ] Strong Checkpoint Verify is explicit
[ ] saved checkpoint can be verified independent of original Editor Session
[ ] pre-export disk Revision gate
[ ] canonical Revision == checkpoint afterRevision
[ ] post-export disk Revision still exact

[ ] non-BP checkpoint Strong Verify real UE pass
[ ] Blueprint single-op checkpoint real UE pass
[ ] Blueprint variable+component+pin one-checkpoint pass
[ ] one canonical export covers all effective operations
[ ] Strong Verify starts exactly 1 child Unreal process

[ ] atomic mismatch leaves checkpoint saved/not verified
[ ] stale Revision fails closed
[ ] export failure leaves saved/not verified
[ ] repeated successful verify is idempotent with no extra process

[ ] Semantic Diff verified stage consumes independent checkpoint evidence
[ ] unrelated semantic changes are not hidden
[ ] Verification Plan evidence strength remains correct
[ ] Trust cannot close on persisted-action alone
[ ] full checkpoint chain reaches Trust verified

[ ] full Python regression pass
[ ] compileall/schema/PowerShell gates pass
[ ] UE5.6 Direct Build valid for final C++ state
[ ] real UE5.6 affected-domain smoke pass
[ ] fixture exact recovery pass
[ ] git diff --check pass
[ ] repository hygiene pass

[ ] no W4 generic batch UX implemented early
[ ] no Generic Blueprint Graph CRUD
[ ] R5 remains deferred
[ ] no release/version/tag/push work
```

Final status wording:

```text
W1 Blueprint narrow resident write        = complete
W2 Fast Resident Verify                   = complete
W3 Checkpoint Strong Verify optimization  = complete
W4 Multi-operation / bounded batch UX     = not yet complete
Generic Blueprint Graph CRUD              = explicitly deferred
R5                                        = deferred
```

If a hard verification or persistence boundary remains open:

```text
W3 = blocked
```

Name the exact blocker.

## 38. W4 Entry

Only after W3 closes should broader batch UX begin.

W4 may then build on:

```text
resident writes
Fast Verify
asset-scoped checkpoint Save
single-export Strong Verify
effective write coalescing
```

to improve:

```text
multi-operation UX
bounded multi-asset checkpoint orchestration
fewer manual confirmations where policy safely permits
Agent-facing task/result binding
```

W4 must not weaken the W3 checkpoint evidence model.

## 39. Direct Handoff Prompt

```text
Repository/worktree:
E:\WorkSpace\UEAgentKit-LiveWriter

Branch:
feature/live-writer-expansion

Accepted W2 baseline:
31f0faa test/docs: close W2 fast resident verify acceptance

Execute W3 Checkpoint Strong Verify only.

First mechanically baseline the current persistence path:
- ue_save_authorized_asset Commit performs _export_refresh_candidate /
  RunAssetCatalog.ps1;
- ue_verify_live_write then performs another independent export;
- Blueprint verify uses RunExport.ps1 -Profile full.

Preserve current behavior by default. Add an explicit checkpoint save mode rather
than silently changing legacy/immediate semantics.

Recommended:
ue_save_authorized_asset(... verification_mode=checkpoint, change_set_id=...)

Checkpoint mode must:
- bind one exact asset and one Change Set;
- bind exact Editor Session, disk Revision and live receipt set at Preview;
- derive an effective final operation set by stable target identity;
- handle repeated same-target edits without falsely verifying intermediate values;
- re-run bounded resident verification before Commit;
- preserve exact SAVE <receipt> confirmation and backup;
- save through the resident Editor;
- compute the new disk SHA-256 Revision;
- start zero UnrealEditor-Cmd.exe / Commandlet processes;
- return persisted-action evidence only.

Add an explicit strong checkpoint verify, recommended:
ue_verify_live_write_checkpoint(checkpoint_id)

It must:
- work from a saved disk-Revision-bound checkpoint;
- not require the original resident Editor Session to still exist;
- fail if current disk Revision differs from checkpoint.afterRevision;
- run exactly one independent Unreal export for the checkpoint asset;
- use RunExport -Profile full for Blueprint writes and the existing appropriate
  canonical export for non-Blueprint writes;
- validate exact canonical asset and Revision;
- verify every effective operation against the same canonical artifact;
- mark the checkpoint/effective operations verified only when the full effective
  set matches;
- leave a failed/mismatched checkpoint saved but not verified;
- never auto-rollback;
- reuse an already validated verified artifact idempotently without another
  Unreal process.

Primary real UE5.6 acceptance:
1. non-BP scalar checkpoint;
2. Blueprint single operation checkpoint;
3. one Blueprint checkpoint containing:
   setVariableDefault + setComponentProperty + setPinDefault;
   one save, one independent RunExport, three exact value matches;
4. repeated same-target writes with correct supersession/final-state semantics;
5. stale disk Revision fail-closed;
6. canonical mismatch fail-closed;
7. full:
   Live Apply → Fast Verify → Checkpoint Save → one Strong Checkpoint Verify
   → Semantic Diff verified → Verification Plan → Trust verified.

Hard process-count target:
checkpoint Save child Unreal starts = 0
checkpoint Strong Verify child Unreal starts = 1
total strong checkpoint path = 1

Do not implement W4 generic batch UX, Generic Blueprint Graph CRUD, R5,
release/version/tag/push work, or source-control automation.
Do not Push or Rebase.
```

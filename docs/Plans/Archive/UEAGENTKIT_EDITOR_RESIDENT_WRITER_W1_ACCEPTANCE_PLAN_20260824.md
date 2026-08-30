# UEAgentKit Editor-Resident Writer W1 Acceptance Plan

> Date: 2026-08-24
>
> Implementation branch: `feature/live-writer-expansion`
>
> Current implementation HEAD: `e2c0994` (`feat: add resident blueprint narrow live writes`)
>
> W0 checkpoint: `142ca1e` (`perf/docs: baseline editor-resident writer path`)
>
> Parent execution plan: `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_W1_DETAILED_PLAN_20260823.md`
>
> W0 evidence: `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W0_BASELINE_20260823.md`
>
> Scope of this document: close W0's B3 fixture gap and close the W1 acceptance / exit gate.
>
> Explicitly out of scope: W2 Fast Resident Verify implementation, W3 checkpoint verification optimization, Generic Blueprint Graph CRUD, R5, version/release work, Performance P1-P5.

## 1. Purpose

The W1 implementation is now code-complete enough to build, but it is **not yet accepted**.

The next task is not to add more writer capabilities. The next task is to prove, in a real UE5.6 Editor session, that the three existing narrow Blueprint operations now use the resident Editor path without weakening the 0.8 reliability contract.

Operations under acceptance:

```text
setVariableDefault
setComponentProperty
setPinDefault
```

The acceptance target is:

```text
Plan / Policy / Revision
→ current UnrealEditor.exe
→ exact Blueprint target resolve
→ resident transaction + snapshot
→ mutation
→ Blueprint compile
→ exact resident read-back
→ Dirty package
→ Undo / Discard OR Authorized Save
→ Strong Independent Verify
→ Semantic Diff
→ Verification Plan
→ Trust Verdict
→ exact fixture recovery
```

W1 is complete only when the real UE5.6 evidence proves all of the following:

1. The Apply stage starts **zero** `UnrealEditor-Cmd.exe` child processes.
2. The exact Blueprint target identities remain stable.
3. Compile is part of changed-success semantics.
4. Compile failure cannot leave a falsely successful live receipt.
5. Undo / Discard restore exact pre-write state.
6. Authorized Save remains gated and independent verification remains independent.
7. R2 Semantic Diff and R3 Trust consume the resident-write evidence without relaxing assertions.
8. Every fixture returns to its exact baseline.

## 2. Current State

### 2.1 Branch and commits

Current implementation line:

```text
feature/live-writer-expansion

9917c0a  docs: define resident writer W0 W1 execution plan
142ca1e  perf/docs: baseline editor-resident writer path
e2c0994  feat: add resident blueprint narrow live writes
```

At the start of this acceptance phase, the tracked worktree is expected to be clean.

Do not include the pre-existing untracked `CONOUT$` in any commit.

### 2.2 W0 evidence already obtained

W0 measured the existing cold paths before resident Blueprint Apply acceptance.

#### B0 — existing non-Blueprint live path

```text
attempt 1  34467.5 ms
attempt 2  35041.4 ms
attempt 3  36553.2 ms
recovery   3/3 exact
```

#### B1 — Blueprint variable default cold path

```text
attempt 1  57185.4 ms
attempt 2  57376.4 ms
attempt 3  55242.6 ms
recovery   3/3 exact
```

#### B2 — Blueprint component property cold path

```text
DryRun  8345.8 ms
Commit  8309.8 ms
recovery exact
```

#### B3 — Blueprint pin default cold path

Blocked because the current fixture does not expose a stable editable unlinked input pin.

This is a **fixture gap**, not evidence that `setPinDefault` is unsupported.

### 2.3 W1 implementation already present

Python:

- `setVariableDefault` enters `LIVE_WRITE_OPERATION_REGISTRY`.
- `setComponentProperty` enters `LIVE_WRITE_OPERATION_REGISTRY`.
- `setPinDefault` enters `LIVE_WRITE_OPERATION_REGISTRY`.
- `setBlueprintDescription` remains non-live.
- Blueprint canonical exported-value handling is available for later save/verify stages.

C++:

- `BlueprintWriteCommon.h/.cpp` exists for shared narrow Blueprint write helpers.
- `LiveWriteBlueprintOperations.cpp` exists.
- Blueprint descriptors do not use the global `NonBlueprint` requirement.
- `LiveWriteTransaction` includes a Blueprint compile stage on changed success.
- Compile failure attempts exact snapshot restore and baseline recompile.
- Unproven recovery is reported as `recovery-failed`.

Build / unit status already reported:

```text
UE5.6 Direct Build                         pass

test_patches.py                            pass
test_agent_workflow.py                     pass
test_live_write_smoke_contract.py          pass
test_blueprint_patch_executor.py           pass
test_tool_registry.py                      pass
test_semantic_diff.py                      pass
test_verification_trust.py                 pass
test_mcp_server.py                         pass
```

These results establish implementation plausibility. They do **not** replace the real Editor acceptance required below.

## 3. Acceptance State Model

Use these state labels consistently in docs and handoffs:

```text
implemented
validated-unit
validated-direct-build
validated-real-ue
accepted
blocked
```

Current W1 state at the beginning of this plan:

```text
Blueprint resident writer implementation  implemented
Python contracts                          validated-unit
UE5.6 C++ build                           validated-direct-build
real resident Blueprint Apply             pending
real Undo / Discard                       pending
real Authorized Save                      pending
real Strong Independent Verify            pending
real R2/R3 trust closure                  pending
W1                                        acceptance-pending
```

Do not call W1 `complete` until the final gate in section 12 is closed.

## 4. Execution Rules

### 4.1 Freeze feature scope

During acceptance, do not add another writer family.

Allowed changes are limited to:

- DirectHost fixture additions required for deterministic acceptance;
- smoke / harness code;
- test-only deterministic failure injection when necessary;
- narrow bug fixes discovered by the acceptance tests;
- documentation and result records;
- minimal instrumentation required to prove process counts / timing.

Do not add:

- Generic Blueprint Graph CRUD;
- variable/component lifecycle CRUD;
- graph wiring/layout;
- Material Graph / Niagara / Sequencer / Control Rig mutation;
- Level Actor generic mutation;
- arbitrary Python / Console / Shell execution;
- new MCP Tools for these three operations;
- W2 Fast Verify behavior;
- W3 Save/Verify API redesign;
- R5 features.

### 4.2 Preserve reliability boundaries

The acceptance run must preserve:

```text
fixed project
exact Editor Session
Policy
Revision
Plan digest
exact target identity
Change Set binding
transaction / snapshot
no-op semantics
Authorized Save confirmation
Strong Independent Verify
Semantic Diff
Verification Plan
Trust Verdict
exact recovery
```

### 4.3 Do not optimize Save / Verify yet

Current behavior intentionally remains:

```text
Authorized Save Commit
→ resident save
→ embedded independent export

then

ue_verify_live_write
→ independent export again
```

W1 measures and proves compatibility with this behavior.

W3 decides whether to eliminate duplicate cold-start work.

## 5. A0 — Preflight and Freeze

Before modifying fixtures or launching UE:

```text
[ ] git status inspected
[ ] branch == feature/live-writer-expansion
[ ] HEAD == expected W1 checkpoint or a documented descendant
[ ] no unrelated tracked modifications
[ ] CONOUT$ left untouched
[ ] no R4.1 raw artifact changes
[ ] DirectHost project identity confirmed
[ ] intended UE5.6 engine path confirmed
```

Record the exact baseline commit in the acceptance result document.

If another Agent is already modifying the same fixture/test files, do not overwrite it. Use a separate worktree or coordinate at a stable checkpoint.

## 6. A1 — Close the B3 Fixture Gap

### 6.1 Goal

Add one deterministic Blueprint fixture target that exposes an editable, unlinked input pin suitable for `setPinDefault`.

### 6.2 Fixture requirements

The pin fixture must provide stable identity:

```text
graphGuid
nodeGuid
pinName
```

The target pin must be:

```text
Direction == input
LinkedTo == empty
bDefaultValueIsReadOnly == false
bDefaultValueIsIgnored == false
schema accepts a deterministic default
```

Prefer a simple built-in node with deterministic semantics, for example a narrow arithmetic/function node with one editable scalar input.

Do not make the test depend on:

- display-name search at runtime;
- spatial graph location;
- nondeterministic GUID generation per attempt;
- plugin/example content that may not exist on another test machine.

### 6.3 Fixture baseline contract

Record:

```text
assetPath
graphGuid
nodeGuid
pinName
pin type
baseline default
alternate test default
baseline package revision
```

The reset path must restore the exact package revision/canonical semantics expected by the test fixture contract.

### 6.4 Run B3 cold-path baseline

Before testing resident Apply, complete the missing W0 B3 baseline with the old Commandlet path:

```text
Plan / patch preparation
→ RunPatch DryRun
→ RunPatch Commit
→ exact read-back
→ exact baseline recovery
```

Record at minimum:

```text
dry_run_ms
commit_ms
child_ue_process_count
fixture_recovery
```

This closes the known W0 fixture blocker.

### 6.5 A1 exit

```text
[ ] stable editable pin fixture exists
[ ] exact identity recorded
[ ] B3 cold DryRun passes
[ ] B3 cold Commit passes
[ ] B3 exact recovery passes
[ ] W0 baseline doc/result updated without rewriting historical measurements
```

Recommended checkpoint commit:

```text
test/docs: add deterministic blueprint pin fixture
```

## 7. A2 — Real UE5.6 Resident Apply Harness

### 7.1 Goal

Create or extend one deterministic smoke harness that can execute all three Blueprint resident operations against an already running Editor.

The harness should prefer the same workflow surface an Agent would use rather than directly invoking private C++ helpers.

Required path:

```text
MCP/high-level Plan
→ ue_apply_asset_property_live
→ inspect result
→ optional Undo/Discard
→ Authorized Save
→ ue_verify_live_write
→ Semantic Diff
→ Verification Plan
→ Trust Verdict
→ recovery
```

### 7.2 Required per-attempt diagnostics

Record:

```text
operation
assetPath
target identity
planId
changeSetId
liveApplyReceipt
transactionId
editorSessionId
beforeValue
afterValue
changed
compileAttempted
compileSucceeded
packageDirty
apply elapsed ms
child UnrealEditor-Cmd process count during Apply
save elapsed ms
verify elapsed ms
final revision
recovery result
```

Do not put elapsed time into Trust evidence.

### 7.3 Child process proof

For the **Apply window** only, mechanically prove:

```text
UnrealEditor-Cmd.exe starts == 0
```

The Save and Strong Verify windows are allowed to start Commandlets in W1.

### 7.4 Exact Editor identity

Before and after Apply, record:

```text
editorSessionId
editorProcessId
```

The resident path must remain in the same running `UnrealEditor.exe` session.

## 8. A3 — `setVariableDefault` Real Acceptance

### 8.1 Success path

Run:

```text
Plan
→ Live Apply
→ compile success
→ exact CDO/default read-back
→ Dirty
→ Authorized Save Preview
→ Authorized Save Commit
→ Strong Independent Verify
→ Semantic Diff verified
→ Verification Plan
→ Trust Verdict
→ exact recovery
```

Required assertions:

```text
operation == setVariableDefault
resident Apply child process count == 0
exact variableName preserved
beforeValue exact
afterValue exact
changed == true
transactionRecorded == true
compileAttempted == true
compileSucceeded == true
packageDirty after Apply == true
saved revision changes as expected
independent verify revision == disk revision
Trust == verified only after all required evidence is closed
```

### 8.2 No-op

Apply the exact current value.

Required:

```text
changed == false
no fake liveApplyReceipt
no transaction
no save required
no independent verify required for the no-op
Change Set no-op state remains mechanically correct
```

### 8.3 Undo

```text
Live Apply
→ Undo exact transaction
→ exact baseline value
→ Blueprint remains compilable
→ terminal Change Set / journal state correct
```

### 8.4 Discard

```text
Live Apply
→ Discard snapshot
→ exact baseline value
→ journal closes
→ no disk revision change
```

## 9. A4 — `setComponentProperty` Real Acceptance

Use the existing deterministic SCS component fixture, for example the previously measured `DefaultSceneRoot.RelativeLocation.X` target if it remains the canonical test target.

### 9.1 Success path

Required:

```text
exact componentName
exact propertyPath
ComponentTemplate target
resident Apply child process count == 0
before exact
apply exact
after exact
compile success
Dirty true
Save / reload canonical retains value
Semantic Diff only reports expected component-property change
Trust closes only after Strong Verify
exact recovery
```

### 9.2 No-op / Undo / Discard

All three must be covered.

Specially verify that restoring the ComponentTemplate produces a valid Blueprint state and does not leave an unrelated Dirty semantic delta.

## 10. A5 — `setPinDefault` Real Acceptance

Use the fixture created in A1.

### 10.1 Success path

Required target identity:

```text
graphGuid
nodeGuid
pinName
```

Required assertions:

```text
input pin
unlinked
editable default
Schema->TrySetDefaultValue path
resident Apply child process count == 0
exact default read-back
compile success
Dirty true
save/reload canonical contains expected default
Semantic Diff normalization remains narrow
exact recovery
```

### 10.2 Rejection checks

At least one deterministic rejection should prove that the resident path still rejects invalid targets, such as:

```text
wrong graphGuid
wrong nodeGuid
output pin
connected pin
read-only/ignored pin
schema-rejected value
```

Do not broaden pin matching to make the fixture easier.

### 10.3 No-op / Undo / Discard

All three must be covered because Pin state is not an ordinary `FProperty` snapshot.

Verify exact old default representation after recovery.

## 11. A6 — Compile Failure and Recovery

### 11.1 Why this is a hard gate

W1 changed success semantics from:

```text
mutation succeeded
```

to:

```text
mutation succeeded
+ exact read-back succeeded
+ Blueprint compile succeeded
```

Therefore compile-failure recovery is a core safety property, not an optional edge case.

### 11.2 Required failure semantics

For a deterministic failure after mutation:

```text
mutation attempted
→ compile fails
→ snapshot restored
→ restored value verified
→ baseline compile rerun when required
→ no successful liveApplyReceipt
→ no successful Change Set operation state
```

Expected stable failure:

```text
live-editor-write-compile-failed
```

If exact recovery cannot be proven:

```text
recovery-failed
```

Never convert an unproven recovery into success.

### 11.3 Preferred test design

Do **not** destabilize the DirectHost fixture merely to manufacture a flaky Blueprint compiler error.

Preferred order:

1. Use a naturally deterministic Blueprint case if one exists.
2. Otherwise introduce a narrow test-only deterministic compile-failure seam at the transaction/compile boundary.
3. Keep the production control flow identical except for the test injection point.
4. Use real UE5.6 smoke to prove the normal compile-success path for all three operations.

The test seam must not become a public MCP capability.

### 11.4 Required proof

```text
[ ] before value restored exactly
[ ] package semantic state restored
[ ] baseline compile succeeds after restore, when applicable
[ ] no successful live receipt
[ ] Change Set not falsely successful
[ ] recovery failure returns fail-closed error
```

## 12. A7 — Save, Strong Verify, R2 and R3 Closure

Each success operation must close through the existing trusted workflow.

### 12.1 Authorized Save

Prove:

```text
loaded exact Blueprint
Dirty exact package
Preview produces one-time save receipt
Commit requires exact SAVE <receipt> confirmation
backup manifest created
resident Editor saves exact asset
disk revision changes
Change Set saved state is correct
```

W1 must not add a Blueprint-specific save API.

### 12.2 Strong Independent Verify

Prove that verification is still independent:

```text
fresh Commandlet load/export
exact assetPath
actual canonical revision == disk revision
expected saved semantics present
```

A resident compile/read-back is not enough.

### 12.3 Semantic Diff stages

For representative success cases, validate:

```text
stage=live
stage=persisted
stage=verified
```

The same `changeSetId` must remain mechanically bound across the stages.

Special checks:

- Blueprint typed default materialization normalization remains narrow.
- Unrelated pin/default/component changes remain unexpected.
- Resident evidence does not suppress verified-stage requirements.

### 12.4 Verification Plan / Trust Verdict

Required behavior:

```text
compile evidence may satisfy compile-specific assertion
resident read-back may satisfy resident-state assertion
Authorized Save satisfies persistence action evidence
Strong Independent Verify satisfies independent persistence assertion
Trust only becomes verified after all Required assertions are closed
```

Do not let compile success imply saved or verified.

## 13. A8 — Regression Matrix

### 13.1 Focused Python tests

At minimum rerun the affected tests already used during implementation:

```text
tests/python/test_patches.py
tests/python/test_agent_workflow.py
tests/python/test_live_write_smoke_contract.py
tests/python/test_blueprint_patch_executor.py
tests/python/test_tool_registry.py
tests/python/test_semantic_diff.py
tests/python/test_verification_trust.py
tests/python/test_mcp_server.py
```

Add new focused tests for any fixture / harness / compile-recovery behavior introduced during acceptance.

### 13.2 Full Python regression

Run the actual discovered full suite at the final acceptance checkpoint.

Do not hard-code `739` as a permanent expected count. Record:

```text
discovered count
passed count
failed count
skipped count
```

### 13.3 Portable / schema / parser gates

Run the repository's current equivalents of:

```text
portable unittest
compileall
JSON schema/example validation
PowerShell parser validation
git diff --check
```

### 13.4 UE5.6 Direct Build

A Direct Build already passed at `e2c0994`.

Rule:

- If acceptance changes no C++ code, the existing build evidence may be referenced.
- If any C++ code changes, rerun the Direct Build before closing W1.

### 13.5 Text / repository hygiene

For changed text files:

```text
UTF-8
no BOM
CRLF
```

Final branch hygiene:

```text
tracked Output/Backups/Build/Saved artifacts = 0
staged unrelated files = 0
CONOUT$ untouched
no Push
no Rebase
```

## 14. Performance Evidence to Record

W1 is not the checkpoint optimization stage, but it must prove the key performance objective of the resident migration.

For each of the three Blueprint operations report:

```text
cold-path Apply elapsed baseline
resident Apply elapsed
resident compile elapsed
Apply child UnrealEditor-Cmd count
Save elapsed
Strong Verify elapsed
full closed-loop elapsed
```

Primary W1 performance gate:

```text
resident Blueprint Apply child UnrealEditor-Cmd starts = 0
```

Do not require Save / Strong Verify to be commandlet-free in W1.

The W0 results already show that reducing Commandlet lifecycle cost is materially more important than increasing Tool count.

## 15. Result Document

Create a dedicated acceptance result, recommended path:

```text
docs/Plans/UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_RESULT_20260824.md
```

It should contain:

1. exact branch / commit tested;
2. UE5.6 engine / DirectHost project identity;
3. B3 fixture identity and cold baseline;
4. per-operation real resident success results;
5. no-op / Undo / Discard results;
6. compile-failure recovery evidence;
7. Authorized Save / Strong Verify evidence;
8. Semantic Diff live/persisted/verified evidence;
9. Verification Plan / Trust result;
10. before/after process count and latency comparison;
11. full regression/build gates;
12. exact fixture recovery;
13. known limitations;
14. final W1 status.

Do not rewrite W0 historical measurements merely because W1 is faster. Preserve before/after evidence separately.

## 16. Commit Discipline

Do not amend or squash the existing W0/W1 implementation checkpoints just to make history prettier.

Recommended acceptance commits:

### AC1 — fixture / harness

```text
test: add resident blueprint writer acceptance fixtures
```

### AC2 — narrow bug fix, only if required

Example:

```text
fix: close blueprint resident write recovery gap
```

One deterministic issue per focused fix when practical.

### AC3 — final acceptance evidence

```text
test/docs: close resident blueprint writer W1 acceptance
```

Do not mix W2 implementation into AC3.

## 17. W1 Exit Gate

W1 may be marked complete only when every applicable item below is closed.

```text
[ ] B3 deterministic editable pin fixture exists
[ ] B3 cold-path baseline recorded

[ ] setVariableDefault resident Live Apply real UE pass
[ ] setComponentProperty resident Live Apply real UE pass
[ ] setPinDefault resident Live Apply real UE pass

[ ] all 3 operations use current UnrealEditor.exe
[ ] Apply stage starts 0 child UnrealEditor-Cmd.exe
[ ] exact target identities preserved

[ ] compile is part of changed-success path
[ ] compile success real UE path proven
[ ] compile failure exact recovery proven
[ ] recovery-failed remains fail closed

[ ] no-op creates no fake transaction/save/verify
[ ] variable Undo exact recovery
[ ] component Undo exact recovery
[ ] pin Undo exact recovery
[ ] variable Discard exact recovery
[ ] component Discard exact recovery
[ ] pin Discard exact recovery

[ ] Authorized Save remains gated
[ ] exact save confirmation preserved
[ ] backup/revision evidence preserved
[ ] Strong Independent Verify remains independent

[ ] Semantic Diff live pass
[ ] Semantic Diff persisted pass
[ ] Semantic Diff verified pass
[ ] typed Blueprint normalization remains narrow

[ ] Verification Plan assertions correct
[ ] Trust Verdict closes only with required evidence
[ ] Change Set identity preserved end-to-end

[ ] focused Python tests pass
[ ] full Python regression pass
[ ] compileall / schema / PowerShell gates pass
[ ] UE5.6 Direct Build evidence valid for final C++ state
[ ] real UE5.6 affected-domain smoke pass
[ ] all fixtures exact recovery pass

[ ] no Generic Blueprint Graph CRUD scope expansion
[ ] W2 not implemented early
[ ] W3 Save/Verify redesign not implemented early
[ ] R5 remains deferred
[ ] no release/version/tag/push work
[ ] git diff --check pass
[ ] changed text encoding/line endings pass
```

Final status wording:

```text
Blueprint narrow Editor-resident Live Apply = complete
W1 acceptance = complete
Fast Resident Verify = not yet W2 complete
Checkpoint Strong Verify optimization = not yet W3 complete
Generic Blueprint Graph CRUD = explicitly deferred
R5 = deferred by benchmark evidence
```

If any hard gate remains open, use:

```text
W1 acceptance = blocked
```

and name the exact blocker. Do not use a partial success label that implies W2 can begin.

## 18. After W1

Only after section 17 closes should the implementation line proceed to W2.

### W2 — Fast Resident Verify

Goal:

```text
Live Apply
→ exact resident read-back
→ compile / validation
→ Editor Session / Dirty / transaction applicability
→ fast iteration result
```

Fast Verify remains session-local and does not replace Strong Independent Verify.

### W3 — Checkpoint Strong Verify

Then evaluate the measured duplicate cold-start path:

```text
multiple resident edits
→ checkpoint save
→ one Strong Independent Verify
→ Semantic Diff
→ Verification Plan
→ Trust
```

W3 may redesign Save/Verify triggering, but must not weaken independent verification.

## 19. Direct Handoff Prompt for the Execution Agent

```text
Repository/worktree: E:\WorkSpace\UEAgentKit-LiveWriter
Branch: feature/live-writer-expansion
Current W1 implementation checkpoint: e2c0994

Do not expand writer scope. Close W1 acceptance only.

First close the W0 B3 fixture blocker by adding one deterministic Blueprint
fixture with a stable, editable, unlinked input pin. Record graphGuid/nodeGuid/
pinName and run the missing cold-path B3 DryRun/Commit/recovery baseline.

Then run real UE5.6 resident acceptance for:
- setVariableDefault
- setComponentProperty
- setPinDefault

For each operation prove:
- Apply runs in the current UnrealEditor.exe session;
- Apply starts zero UnrealEditor-Cmd.exe child processes;
- exact target identity and before/after values are preserved;
- Blueprint compile is part of changed-success;
- no-op creates no fake transaction/save/verify;
- Undo and Discard restore exact pre-write state;
- Authorized Save remains gated;
- Strong Independent Verify remains independent;
- Semantic Diff live/persisted/verified stays correct;
- Verification Plan and Trust require the correct evidence;
- fixture recovery is exact.

Prove compile-failure recovery deterministically. Prefer a narrow test-only
compile-failure seam over a flaky malformed Blueprint fixture if no natural
stable failure case exists. Compile failure must restore the exact snapshot,
must not return a successful live receipt, and must return recovery-failed if
baseline recovery cannot be proven.

Do not refactor BlueprintPatchCommandlet merely for cleanup during acceptance.
Do not implement W2 Fast Resident Verify or W3 checkpoint verification changes.
Do not change R4.1 raw artifacts, R5 scope, package version, release/tag/push.
Do not touch CONOUT$.

Record the final evidence in:
docs/Plans/UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_RESULT_20260824.md

Use focused checkpoint commits. If acceptance exposes a deterministic product
bug, fix that bug in a separate focused commit, rerun affected real UE smoke,
and close W1 only after the full exit gate is green.
```

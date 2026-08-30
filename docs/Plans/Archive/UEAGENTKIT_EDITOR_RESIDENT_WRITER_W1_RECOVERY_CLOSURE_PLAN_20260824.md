# UEAgentKit Editor-Resident Writer W1 Recovery Closure Plan

> Date: 2026-08-24
>
> Implementation branch: `feature/live-writer-expansion`
>
> Last verified blocked-status checkpoint: `0a4ee2d` (`test/docs: record W1 acceptance blocked status`)
>
> Last verified product-fix checkpoint before blocked result: `9d52fa8` (`fix: close blueprint resident write recovery and save/verify gaps`)
>
> Parent acceptance plan: `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_PLAN_20260824.md`
>
> Current acceptance result: `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_RESULT_20260824.md`
>
> Scope: diagnose and close the real UE5.6 Blueprint Undo crash, complete Blueprint recovery acceptance, then close the remaining W1 Exit Gate.
>
> Explicitly out of scope: W2 Fast Resident Verify, W3 Save/Verify checkpoint redesign, Generic Blueprint Graph CRUD, R5, release/version/tag/push work, unrelated Commandlet cleanup.

## 1. Current State

W1 is not an implementation-design problem anymore. The resident Blueprint writer is built and has already passed a real UE5.6 success path for `setVariableDefault`, including persistence and Trust closure.

Current status:

```text
W1 implementation = complete enough for acceptance
W1 acceptance     = blocked
Primary blocker   = real Editor crash during Blueprint Undo
W2                = not started
W3                = not started
```

Already proven in real UE5.6:

```text
B3 deterministic pin fixture                  pass
B3 cold-path DryRun / Commit / exact recovery pass
setVariableDefault resident success            pass
setVariableDefault resident no-op              pass
Authorized Save for Blueprint variable         pass
Strong Independent Verify                      pass
Semantic Diff live/verified                    pass
Verification Plan                              pass
Trust Verdict                                  verified
UE5.6 Direct Build after prior fixes           pass
```

Known real acceptance fixes already landed before this plan:

```text
Blueprint post-compile target re-resolution
Blueprint Authorized Save export includes Blueprints
Blueprint Strong Verify uses full canonical export
string <-> number/bool exported-value normalization
resident Blueprint acceptance harness
```

Current exact blocker:

```text
ue_undo_asset_property_live
→ Undo UE Agent Kit: setVariableDefault
→ running Unreal Editor crashes
```

The acceptance result currently points toward stale Blueprint target state retained after compile. That is a valid hypothesis, but it must not be treated as proven until the crash location is mechanically identified.

## 2. Why the Undo Crash Must Be Diagnosed Before the Next Fix

A Blueprint compile can rebuild or replace objects that ordinary non-Blueprint live writes can safely hold as raw pointers.

Potentially ephemeral objects include:

```text
GeneratedClass
CDO
Blueprint-created FProperty
SCS component template
UEdGraph / UEdGraphNode / UEdGraphPin
```

The current Blueprint IO implementation has already needed post-compile re-resolution for successful read-back. Recovery is more demanding because the retained transaction record survives beyond Apply and is later consumed by Undo / Discard.

There are at least three distinct failure classes:

### Class A — stale IO target before native Undo

Example:

```text
Record->IO->ReadBefore(...)
```

uses an old CDO / property / pin after compile.

This is likely fixable by stable identity + target re-resolution before every recovery-time IO access.

### Class B — native UE transaction itself references invalid Blueprint objects

Example:

```text
GEditor->UndoTransaction(...)
```

crashes internally because the transaction buffer captured an object that compile subsequently reconstructed or invalidated.

In this case, refreshing the IO pointer before/after `UndoTransaction()` is not sufficient. The Blueprint revert strategy itself must change.

### Class C — snapshot lifetime is tied to stale reflection metadata

For property writes, the retained snapshot may itself depend on the original `FProperty*` used during capture. If that reflection object is rebuilt by Blueprint compile, later Restore or snapshot destruction/reset may dereference invalid metadata even if the current target has been re-resolved.

Therefore the fix must audit both:

```text
current target lifetime
snapshot metadata lifetime
```

Do not patch only the obvious `ValueAddress` use and declare recovery fixed.

## 3. Fixed Safety Contract

This recovery work must preserve the existing W1 contract:

```text
exact Editor Session
exact assetPath
exact operation
exact target identity
Policy / Revision / Plan digest
Change Set binding
semantic no-op behavior
Authorized Save gate
Strong Independent Verify independence
Semantic Diff stages
Verification Plan
Trust Verdict
fail-closed recovery
```

Recovery-specific rules:

1. No long-lived raw Blueprint target pointer may be assumed valid merely because the Blueprint asset itself is still loaded.
2. Undo / Discard must restore the exact pre-write semantic value for the same stable target identity.
3. Compile after recovery must be part of proving a valid Blueprint state where required.
4. A recovery that cannot be mechanically verified must return failure.
5. No recovery path may silently fall back to display-name guessing or a different target.
6. No crash fix may weaken non-Blueprint live-write behavior.
7. Resident recovery must not be replaced with a hidden Commandlet patch workaround.

## 4. R0 — Preflight and Evidence Freeze

Before changing C++:

```text
[ ] git status inspected
[ ] branch == feature/live-writer-expansion
[ ] exact HEAD recorded
[ ] no unrelated tracked modifications
[ ] current blocked result document preserved
[ ] crash log preserved as untracked evidence
[ ] CONOUT$ untouched
[ ] no R4.1 raw artifact changes
[ ] no W2/W3 implementation mixed in
```

The raw crash log remains under `Output/` and must not be committed.

Extract only the bounded relevant crash stack / assertion into the result or diagnostic document.

Do not edit the historical successful evidence to make the failure look cleaner.

## 5. R1 — Mechanically Localize the Crash

### 5.1 Required instrumentation

Add bounded diagnostic logging around the Blueprint Undo path only as needed.

Record markers immediately before and after:

```text
1. transaction/session/asset validation
2. IO pre-undo target refresh
3. IO ReadBefore(current written value)
4. GEditor->UndoTransaction()
5. IO post-undo target refresh
6. IO ReadBefore(restored value)
7. fallback snapshot restore
8. recovery notification
9. Blueprint compile after recovery, if applicable
10. transaction-record removal / journal close
```

Avoid noisy full-object dumps.

Useful identity fields:

```text
operation
assetPath
transactionId
editorSessionId
Blueprint pointer/object path
GeneratedClass pointer/path
CDO pointer/path
target kind
stable target identity
current resolved owner pointer
current resolved FProperty pointer
current resolved value address
pin/node/graph pointer when applicable
```

### 5.2 Required crash classification

The next crash reproduction must end with one of these findings:

```text
A: crash before GEditor->UndoTransaction
B: crash inside GEditor->UndoTransaction
C: crash after GEditor->UndoTransaction
D: crash during fallback RestoreSnapshot / snapshot destruction
E: no crash after diagnostic-only changes
```

If the stack is ambiguous, do not proceed to broad architecture changes. Narrow it further first.

### 5.3 R1 exit gate

```text
[ ] exact crashing phase known
[ ] relevant top stack frames recorded
[ ] exact stale object/metadata class identified or bounded
[ ] diagnosis distinguishes IO lifetime from native transaction lifetime
```

Recommended documentation checkpoint if useful:

```text
docs/Plans/UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_UNDO_CRASH_DIAGNOSIS_20260824.md
```

Do not create that file if the diagnosis is already concise enough for the final acceptance result.

## 6. R2 — Audit Blueprint IO Lifetime Model

The implementation should be reviewed against this principle:

```text
Stable identity may be retained across compile.
Raw resolved Blueprint target pointers may not.
```

### 6.1 Variable target

Stable identity:

```text
assetPath
operation=setVariableDefault
variableName
```

Ephemeral resolution:

```text
Blueprint->GeneratedClass
FProperty*
CDO
ValueAddress
```

Before any recovery-time read or restore, the exact variable target must be resolvable again from stable identity.

### 6.2 Component target

Stable identity:

```text
assetPath
operation=setComponentProperty
componentName
propertyPath
```

Ephemeral resolution:

```text
SCS node
ComponentTemplate
ValueAddress
```

Even if the component class `FProperty` metadata happens to be stable, do not rely on the old ComponentTemplate or value address surviving compile.

### 6.3 Pin target

Stable identity:

```text
assetPath
operation=setPinDefault
graphGuid
nodeGuid
pinName
```

Ephemeral resolution:

```text
UEdGraph*
UEdGraphNode*
UEdGraphPin*
```

Every post-compile or post-transaction pin access must resolve the exact target again.

### 6.4 Snapshot lifetime audit

For `FLiveWriteBlueprintPropertyIO`, explicitly inspect whether the retained snapshot owns or references the original `FProperty*` after capture.

If snapshot Restore / Reset / destruction requires the old `FProperty*`, it is not safe to retain that snapshot across a Blueprint compile that can recreate GeneratedClass metadata.

Required design property:

```text
Blueprint recovery snapshot remains valid even when the original resolved
FProperty / CDO / ComponentTemplate / Pin pointer is no longer valid.
```

Do not assume `UpdateTarget()` repairs an already-captured snapshot if the snapshot internally retained old metadata.

## 7. R3 — Choose the Revert Strategy from Evidence

Do not choose the implementation before R1 proves where the crash occurs.

### Path R3-A — Native UE transaction remains valid

Use this path only if `GEditor->UndoTransaction()` itself is proven safe and the crash is in surrounding stale IO/snapshot access.

Then implement:

```text
stable Blueprint target identity retained in IO/record
→ RefreshTarget() before pre-undo verification
→ native UndoTransaction
→ RefreshTarget() again after Undo
→ exact restored-value verification
→ compile/validation if required
```

Fallback snapshot restore must also refresh target first and must use a snapshot representation that is independent of invalid old reflection/object pointers.

### Path R3-B — Native UE transaction is unsafe across Blueprint compile

Use this path if the crash occurs inside `GEditor->UndoTransaction()` or if the transaction stack is proven to retain invalid reconstructed Blueprint objects.

Do not keep calling native Undo and wrap it with pointer refresh.

Instead design a Blueprint-specific internal revert strategy while preserving the same public MCP Tool:

```text
ue_undo_asset_property_live
```

The public API must not split into `ue_undo_blueprint_*`.

Conceptual internal flow:

```text
validate exact transaction/session/asset/target
→ re-resolve exact current Blueprint target
→ verify current value == recorded AfterValue
→ restore stable pre-write snapshot semantically
→ notify Blueprint modified
→ compile
→ re-resolve target
→ verify exact BeforeValue
→ restore dirty-state contract
→ close transaction record / journal / Change Set state
```

If this strategy is needed, define an explicit internal revert mode rather than scattering `if(Blueprint)` branches through the generic bridge handler.

Example concept only:

```text
EditorTransaction
StableSnapshotRestore
```

The actual naming must follow current project conventions.

### Native transaction stack requirement

If Blueprint live writes can no longer safely use native Editor Undo after compile, the implementation must also ensure it does not leave a stale dangerous transaction on the global Editor Undo stack.

This is a hard requirement.

Possible designs must be evaluated against UE5.6 behavior; do not invent a custom stack manipulation mechanism without proof.

### R3 exit

```text
[ ] revert strategy selected from crash evidence
[ ] non-Blueprint behavior unchanged
[ ] no stale dangerous native transaction left behind
[ ] public Undo/Discard Tool contract unchanged
```

## 8. R4 — Implement Stable Blueprint Recovery

### 8.1 Internal target resolver ownership

Preferred property of the final implementation:

```text
Blueprint IO owns stable identity + weak Blueprint applicability
and resolves transient target pointers on demand.
```

Do not retain raw target pointers as the sole source of truth across compile.

### 8.2 Recovery operations that must be safe

The IO/revert implementation must support these call sites without stale-pointer assumptions:

```text
pre-Undo written-value verification
post-Undo restored-value verification
Discard restore
compile-failure restore
fallback restore after incomplete native Undo
snapshot cleanup/destruction
```

### 8.3 Exact-value verification

After restore:

```text
re-resolve exact target
→ read current value
→ SemanticEqual(current, BeforeValue)
```

A successful function return without read-back is not enough.

### 8.4 Blueprint compile after recovery

Where the restored Blueprint semantic state requires compilation:

```text
restore
→ notify modified
→ compile
→ re-resolve
→ verify restored value again
```

If compile fails after recovery, return fail closed and retain sufficient diagnostic evidence.

### 8.5 Error semantics

Preserve stable error classes where already defined.

At minimum:

```text
live-editor-write-undo-*         for precondition/Undo failures
live-editor-write-compile-failed for write compile failure with proven recovery
recovery-failed                  when exact recovery cannot be proven
```

Do not return success when the Editor survived but target recovery was not verified.

## 9. R5 — Focused Real UE Recovery Acceptance

Do not run the full W1 matrix immediately after the first crash fix. Close the highest-risk recovery cases first.

### 9.1 Variable Undo — first hard gate

Run:

```text
fixture reset
→ Plan setVariableDefault
→ resident Live Apply
→ compile success
→ exact AfterValue
→ Undo
→ no Editor crash
→ exact BeforeValue
→ Blueprint compile valid
→ package dirty state correct
→ transaction/journal terminal state correct
→ fixture exact recovery
```

Required:

```text
[ ] no crash
[ ] same Editor session survives
[ ] exact variableName preserved
[ ] BeforeValue restored
[ ] no stale transaction record
[ ] no dangerous leftover Undo entry attributable to the live write
```

Do not proceed to broad acceptance until this passes repeatedly enough to rule out a one-off survival.

Recommended minimum: 3 consecutive variable Undo passes in the same acceptance configuration.

### 9.2 Variable Discard

Then run:

```text
Live Apply
→ Discard
→ exact BeforeValue
→ compile valid
→ no disk revision change
→ journal closed
```

### 9.3 Component Undo / Discard

Only after variable recovery is stable:

```text
setComponentProperty
→ Live Apply
→ Undo
→ exact ComponentTemplate property recovery
```

and separately:

```text
Live Apply
→ Discard
→ exact recovery
```

Component recovery must re-resolve the SCS/ComponentTemplate target.

### 9.4 Pin Undo / Discard

Use the deterministic B3 pin identity:

```text
graphGuid = 12345678-9abc-def0-1234-56789abcdef0
nodeGuid  = 11111111-2222-2222-3333-333344444444
pinName   = A
```

Run both Undo and Discard.

Pin recovery must verify the exact old default representation after schema-mediated restore and compile.

## 10. R6 — Complete Remaining Operation Acceptance

Once recovery is safe, complete the acceptance cases that were blocked by the crash.

### 10.1 `setComponentProperty`

Required real UE cases:

```text
[ ] success full chain
[ ] no-op
[ ] Undo
[ ] Discard
[ ] exact fixture recovery
```

Full success chain:

```text
Plan
→ resident Live Apply
→ compile success
→ exact read-back
→ Dirty
→ Authorized Save
→ Strong Independent Verify
→ Semantic Diff live/persisted/verified as applicable
→ Verification Plan
→ Trust Verdict
→ exact recovery
```

### 10.2 `setPinDefault`

Required real UE cases:

```text
[ ] success full chain
[ ] no-op
[ ] Undo
[ ] Discard
[ ] at least one deterministic invalid-target rejection
[ ] exact fixture recovery
```

The rejection case must not broaden target matching to make the test pass.

Preferred rejection examples:

```text
wrong graphGuid
wrong nodeGuid
output pin
connected pin
schema-rejected value
```

## 11. R7 — Compile-Failure Recovery Hard Gate

This remains required even after Undo/Discard is fixed.

### 11.1 Required behavior

```text
mutation succeeds
→ exact read-back succeeds
→ compile fails
→ exact pre-write state restored
→ baseline Blueprint compile succeeds where applicable
→ restored target re-resolved
→ exact BeforeValue verified
→ no successful liveApplyReceipt
→ no falsely successful Change Set state
```

Expected result when recovery succeeds:

```text
live-editor-write-compile-failed
```

Expected result when recovery cannot be proven:

```text
recovery-failed
```

### 11.2 Test design

Prefer a deterministic, test-only compile-failure seam at the transaction/compile boundary if no naturally stable Blueprint compile failure exists.

Requirements:

```text
not exposed as MCP Tool
not enabled in normal product configuration
production control flow remains the same except at the injection point
bounded to test/acceptance use
```

Do not destabilize the DirectHost Blueprint graph merely to force a flaky compiler error.

### 11.3 Snapshot regression requirement

The compile-failure test must specifically exercise the same stable-snapshot/re-resolution mechanism used by later Undo/Discard.

Do not create a special recovery path that bypasses the production IO semantics.

## 12. R8 — Trust / Persistence Closure

`setVariableDefault` has already proven the main Save / Strong Verify / Trust chain once. Do not discard that evidence.

Still required before W1 close:

```text
setComponentProperty representative full trusted closure
setPinDefault representative full trusted closure
```

For each successful persisted operation prove:

```text
resident compile/read-back != independent persistence proof
Authorized Save remains explicitly gated
Strong Verify uses independent Unreal load/export
same Change Set identity survives stages
Semantic Diff remains narrow
Trust becomes verified only after Required assertions close
```

Semantic Diff evidence should cover, where the workflow supports it:

```text
live
persisted
verified
```

Do not weaken verified-stage requirements because resident Apply is fast.

## 13. R9 — Performance Evidence

The crash fix must not accidentally regress the primary W1 performance goal.

For Variable / Component / Pin success Apply windows record:

```text
resident Apply elapsed
resident compile elapsed
UnrealEditor-Cmd.exe child starts during Apply
Editor session before/after
```

Hard performance gate remains:

```text
Apply child UnrealEditor-Cmd.exe starts = 0
```

Undo/Discard should also remain resident. Do not hide recovery through `RunPatch.ps1` or a commandlet fallback.

Save and Strong Verify may still start Commandlets in W1; W3 owns that optimization.

## 14. R10 — Regression and Build Gates

Because recovery changes are expected to touch C++, a fresh UE5.6 Direct Build is required after the final C++ state is known.

### Focused tests

At minimum rerun affected tests:

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

Add focused tests for:

```text
stable Blueprint target re-resolution during recovery
snapshot lifetime across compile
Undo/Discard result contract
compile-failure recovery
fail-closed recovery-failed path
```

### Final repository gates

```text
[ ] focused Python tests pass
[ ] full discovered Python suite pass
[ ] portable test suite pass
[ ] Ruff pass
[ ] compileall pass
[ ] JSON schema/example validation pass
[ ] PowerShell parser pass
[ ] UE5.6 Direct Build pass on final C++
[ ] real UE5.6 affected-domain smoke pass
[ ] git diff --check pass
[ ] changed text UTF-8 no BOM / CRLF
[ ] tracked Output/Backups/Build/Saved artifacts = 0
[ ] CONOUT$ untouched
[ ] no Push
[ ] no Rebase
```

Do not rerun the frozen R4.1 24-attempt benchmark merely for this recovery fix unless the public result/tool contract changes in a benchmark-visible way.

## 15. Result Documentation

Continue updating:

```text
docs/Plans/UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_RESULT_20260824.md
```

Preserve the historical blocked section and append the recovery progression rather than erasing the crash history.

The final result should record:

1. exact crash classification;
2. root cause;
3. recovery architecture chosen;
4. exact fix commit(s);
5. Variable Undo/Discard evidence;
6. Component success/no-op/Undo/Discard evidence;
7. Pin success/no-op/Undo/Discard/rejection evidence;
8. compile-failure recovery evidence;
9. persistence / Strong Verify / Semantic Diff / Trust evidence;
10. Apply process-count and latency evidence;
11. final regression/build gates;
12. exact fixture recovery;
13. final W1 status.

The bounded relevant crash stack may be quoted/paraphrased in the result. Do not commit raw `Output/W1Acceptance` logs.

## 16. Commit Discipline

Keep the crash/recovery changes independently reviewable.

Recommended sequence:

### RC1 — crash diagnosis / test support

Only if code instrumentation or deterministic recovery test support is needed:

```text
test: diagnose resident blueprint undo crash
```

### RC2 — recovery fix

```text
fix: make blueprint resident recovery compile-safe
```

This commit should contain the smallest coherent production fix for stable target/snapshot/revert semantics.

### RC3 — acceptance harness/tests

```text
test: close blueprint resident recovery acceptance
```

### RC4 — final evidence

```text
test/docs: close resident blueprint writer W1 acceptance
```

If diagnosis proves native UE transaction semantics require a larger redesign, do not bury it inside RC2 with unrelated cleanup. Document the decision and keep the implementation focused.

Do not refactor `BlueprintPatchCommandlet.cpp` merely to consume shared helpers during this phase unless the crash fix mechanically requires it.

## 17. Final W1 Exit Gate

W1 may move from `blocked` to `complete` only when all applicable items are green.

```text
[ ] Undo crash root cause mechanically identified
[ ] no stale Blueprint target/snapshot lifetime bug remains
[ ] native transaction or alternate revert strategy proven safe

[ ] variable resident success pass
[ ] variable no-op pass
[ ] variable Undo pass without crash
[ ] variable Discard exact recovery pass

[ ] component resident success pass
[ ] component no-op pass
[ ] component Undo exact recovery pass
[ ] component Discard exact recovery pass

[ ] pin resident success pass
[ ] pin no-op pass
[ ] pin Undo exact recovery pass
[ ] pin Discard exact recovery pass
[ ] pin deterministic rejection pass

[ ] Apply uses current UnrealEditor.exe for all 3 operations
[ ] Apply starts 0 child UnrealEditor-Cmd.exe
[ ] exact target identities preserved
[ ] same Editor Session survives recovery operations

[ ] compile remains part of changed-success
[ ] compile-failure exact recovery proven
[ ] recovery-failed remains fail closed
[ ] no successful receipt on compile failure

[ ] Authorized Save remains gated
[ ] Strong Independent Verify remains independent
[ ] Semantic Diff live/persisted/verified remains correct
[ ] Verification Plan assertions remain correct
[ ] Trust closes only with required evidence
[ ] Change Set identity preserved end-to-end

[ ] focused tests pass
[ ] full Python regression pass
[ ] portable/schema/parser/compileall/Ruff gates pass
[ ] UE5.6 Direct Build pass on final C++ state
[ ] real UE5.6 affected-domain smoke pass
[ ] all fixtures exact recovery pass
[ ] repository hygiene pass

[ ] no Generic Blueprint Graph CRUD scope expansion
[ ] W2 not implemented early
[ ] W3 Save/Verify redesign not implemented early
[ ] R5 remains deferred
[ ] no release/version/tag/push work
```

Final allowed status wording when closed:

```text
Blueprint narrow Editor-resident Live Apply = complete
W1 acceptance = complete
Fast Resident Verify = not yet W2 complete
Checkpoint Strong Verify optimization = not yet W3 complete
Generic Blueprint Graph CRUD = explicitly deferred
R5 = deferred by benchmark evidence
```

If the native UE transaction problem cannot be resolved safely within W1, retain:

```text
W1 acceptance = blocked
```

and name the exact remaining safety blocker. Do not begin W2 on top of an unsafe recovery lifecycle.

## 18. After W1 Closure

Only after section 17 is green:

```text
W2 — Fast Resident Verify
```

Then later:

```text
W3 — Checkpoint Strong Verify / duplicate cold-start reduction
```

Do not use the current Undo crash as a reason to mix W2/W3 into the recovery fix.

## 19. Direct Handoff Prompt for the Execution Agent

```text
Repository/worktree: E:\WorkSpace\UEAgentKit-LiveWriter
Branch: feature/live-writer-expansion
Last verified blocked-status checkpoint: 0a4ee2d

W1 acceptance is blocked by a real UE5.6 Editor crash during
ue_undo_asset_property_live after setVariableDefault.

Do not expand writer scope and do not start W2/W3.

First reproduce the Undo crash with bounded diagnostics and classify whether the
failure occurs before, inside, or after GEditor->UndoTransaction(), or during
fallback snapshot restore/destruction. Do not assume the stale-IO hypothesis is
the complete root cause without the crash stack.

Audit both target lifetime and snapshot lifetime across Blueprint compile.
GeneratedClass/CDO/FProperty, SCS ComponentTemplate, and UEdGraphPin targets may
be reconstructed by compile. Stable target identity may be retained; raw
resolved target pointers must not be the sole recovery source of truth.

If native GEditor->UndoTransaction is proven safe, re-resolve the exact target
before pre-Undo verification and again after Undo, and make the retained
snapshot independent of stale reflection/object pointers.

If the crash is inside GEditor->UndoTransaction, do not keep calling native Undo
with refreshed IO pointers. Introduce a narrow Blueprint-internal revert
strategy behind the existing ue_undo_asset_property_live contract. Restore from
stable snapshot/identity, compile, re-resolve, verify exact BeforeValue, and
ensure no dangerous stale native transaction is left on the Editor Undo stack.
Do not change non-Blueprint Undo behavior.

After the fix, close recovery in this order:
1. setVariableDefault Undo, minimum 3 consecutive real UE passes.
2. setVariableDefault Discard.
3. setComponentProperty success/no-op/Undo/Discard.
4. setPinDefault success/no-op/Undo/Discard plus one rejection case.
5. deterministic compile-failure recovery and recovery-failed fail-closed path.
6. component/pin Authorized Save, Strong Verify, Semantic Diff and Trust closure.
7. final full regression and UE5.6 Direct Build.

Resident Apply must continue to start zero UnrealEditor-Cmd.exe child processes.
Undo/Discard must not be implemented as a hidden RunPatch/Commandlet fallback.

Preserve the existing acceptance result and append the crash diagnosis, fix and
closure evidence. Do not commit raw Output logs, do not touch CONOUT$, do not
modify R4.1 raw artifacts, do not release/tag/push/rebase.

Only mark W1 acceptance complete after every hard gate in this plan is green.
```

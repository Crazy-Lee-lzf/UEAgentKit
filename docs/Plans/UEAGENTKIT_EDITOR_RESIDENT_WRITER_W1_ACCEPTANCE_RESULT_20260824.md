# UEAgentKit Editor-Resident Writer W1 Acceptance Result

> Date: 2026-08-24
>
> Branch: `feature/live-writer-expansion`
>
> Tested commit: `9d52fa8` (`fix: close blueprint resident write recovery and save/verify gaps`) + recovery closure commits
>
> Parent acceptance plan: `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_PLAN_20260824.md`
>
> Recovery closure plan: `UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_RECOVERY_CLOSURE_PLAN_20260824.md`

## 1. Status

```text
W1 acceptance = complete
```

The Blueprint Undo crash is fixed and all real UE5.6 recovery/persistence gates in the Recovery Closure Plan are green.

## 2. Historical Blocked Result

The original blocked status is preserved below.

### 2.1 Original Status

```text
W1 acceptance = blocked
```

Exact blocker:

- `ue_undo_asset_property_live` on a Blueprint variable default crashes the running Unreal Editor after `Undo UE Agent Kit: setVariableDefault`.
- Therefore variable/component/pin Undo, Discard, compile-failure recovery, and full three-operation real acceptance were not yet proven.

### 2.2 Original What Was Proven

#### B3 fixture gap closed

- Deterministic editable input pin fixture added in `WriteFixturePlanCommandlet.cpp`.
- Identity:

```text
assetPath  = /Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
graphGuid  = 12345678-9abc-def0-1234-56789abcdef0
nodeGuid   = 11111111-2222-2222-3333-333344444444
pinName    = A
pin type   = int
baseline   = 0
alternate  = 5
```

- B3 cold-path baseline:

```text
dry_run_ms = 8283.3
commit_ms   = 8269.9
cold-start count = 2
recovery    = exact
```

#### setVariableDefault real resident success

Real UE5.6 run passed the full success path:

```text
Plan → Live Apply → compile success → read-back → Dirty
→ Authorized Save Preview/Commit
→ Strong Independent Verify (RunExport full profile)
→ Semantic Diff live/verified
→ Verification Plan
→ Trust Verdict = verified
```

Captured evidence:

```text
operation            = setVariableDefault
beforeValue          = 0
afterValue           = 42
changed              = true
compileAttempted     = true
compileSucceeded     = true
packageDirtyAfter    = true
liveApplyReceipt     = live_i0LFtyk1LpYZ_eX96YZ1GQ
transactionId        = 77C4560F-488E-4F5F-10CC-0E96E00BBC35
editorSessionId      = e6653333-4f85-7d94-bc35-6c9028ac19d0
trust verdict        = verified
```

#### setVariableDefault no-op

Real UE pass:

```text
changed = false
liveApplyReceipt = ""
```

### 2.3 Original Bugs Fixed During Acceptance

- `LiveWriteBlueprintOperations.cpp`:
  - After Blueprint compile, re-resolve variable/component/pin target before post-compile read-back to avoid stale CDO/property pointers.
- `agent_workflow.py`:
  - Authorized Save independent export now passes `-IncludeBlueprints` for Blueprint assets.
  - `ue_verify_live_write` uses `RunExport.ps1 -Profile full` for Blueprint operations so canonical value extraction works.
  - `_live_write_exported_matches` coerces canonical string scalars back to numeric/boolean for verification.
- Added `tests/integration/mcp_blueprint_resident_acceptance.py` resident acceptance harness.

## 3. Crash Diagnosis

From `CrashContext.runtime-xml` and `HostProject.log` for the exact blocked crash:

- Log marker: `LogEditorTransaction: Undo UE Agent Kit: setVariableDefault`
- Crash: `Unhandled Exception: EXCEPTION_ACCESS_VIOLATION 0x0000000000000000`
- Top project stack: `FUEAgentKitEditorBridge::RevertLiveWriteTransaction()` at `EditorBridgeWriteHandlers.cpp:315`
- Line 315 is the fallback `Record->IO->NotifyRestored()` call after `GEditor->UndoTransaction()` had already returned.
- This is Class A/C: native Undo returned, but the retained Blueprint IO still held transient CDO/FProperty pointers and a raw `FProperty*`-backed snapshot. The post-Undo read could not verify the restored value, then fallback restore/notify used stale reflected metadata and crashed.

## 4. Recovery Fix

Implemented in the worktree:

- `ILiveWriteValueIO::RefreshTarget(FString&)` added with a no-op default.
- `FLiveWriteBlueprintPropertyIO` / `FLiveWriteBlueprintPinIO` now retain stable identity (`UBlueprint` weak pointer, operation, target JSON) and resolve transient CDO / property / SCS ComponentTemplate / pin pointers on demand:
  - before pre-Undo written-value verification
  - before post-Undo restored-value verification
  - before fallback restore / notify
  - before compile-failure restore
- Blueprint property snapshot changed from raw `FProperty*` byte storage to a semantic JSON value, so restore uses the current property type and is independent of stale reflection metadata.
- `RevertLiveWriteTransaction` now calls `RefreshTarget` at every recovery-time boundary and returns `live-editor-write-undo-target-invalid` if the exact target can no longer be resolved.
- `LiveWriteTransaction.cpp` compile-failure paths refresh the target before fallback restore.
- Validation now allows dotted `propertyPath` specifically for `setComponentProperty` (`IsSafeLiveWriteSelector(..., bAllowDots)`).
- Python verification and semantic-diff extraction now parse UE struct literals such as `(X=10.000000,Y=0.000000,Z=0.000000)` for nested component paths.
- Semantic-diff snapshot scan skips a parent component field when a nested subfield is the expected live-write path, preventing a false `trust-semantic-unexpected-change`.
- Added a test-only, default-off one-shot/always compile-failure seam (`UEAK_TEST_FORCE_COMPILE_FAILURE_ONCE` / `UEAK_TEST_FORCE_COMPILE_FAILURE_ALWAYS`) at `CompileBlueprint()` for deterministic acceptance.

## 5. Real UE5.6 Recovery Closure Evidence

All runs used the same DirectHost project and `BP_TransactionBlueprint` fixture, without `-NullRHI`.

### Variable

```text
setVariableDefault real success              pass
setVariableDefault real no-op                pass
setVariableDefault real Undo #1              pass, same session survived
setVariableDefault real Undo #2              pass, same session survived
setVariableDefault real Undo #3              pass, same session survived
setVariableDefault real Discard              pass, exact BeforeValue=0 restored
```

Undo evidence:

```text
beforeValue = 42
afterValue  = 0
changed     = true
packageDirtyAfter = false
diskRevisionChanged = false
```

### Component

```text
setComponentProperty real no-op              pass
setComponentProperty real Undo               pass, exact BeforeValue=0 restored
setComponentProperty real Discard            pass, exact BeforeValue=0 restored
setComponentProperty real success            pass, full Trust closure = verified
```

Component success evidence:

```text
beforeValue = 0
afterValue  = 10
compileAttempted = true
compileSucceeded = true
Trust Verdict = verified
```

### Pin

```text
setPinDefault real no-op                     pass
setPinDefault real Undo                      pass, exact BeforeValue="0" restored
setPinDefault real Discard                   pass, exact BeforeValue="0" restored
setPinDefault deterministic rejection        pass
setPinDefault real success                   pass, full Trust closure = verified
```

Rejection:

```text
wrong graphGuid = 00000000-0000-0000-0000-000000000000
code = live-editor-write-target-resolution-failed
message = Graph was not found: 00000000-0000-0000-0000-000000000000
```

### Compile-failure recovery

```text
forced first compile failure                pass
expected code = live-editor-write-compile-failed
no liveApplyReceipt
post-recovery no-op value=0 changed=false  pass (exact BeforeValue restored)
baseline recompile succeeded after one-shot seam
```

### Fail-closed recovery-failed path

```text
forced every compile failure                pass
expected code = live-editor-write-recovery-failed
no liveApplyReceipt
```

## 6. Performance / Resident Constraint

The Apply path for variable/component/pin remains the same resident `UnrealEditor.exe` path used in the earlier W1 success evidence. No `RunPatch.ps1`/Commandlet fallback was used for Undo, Discard, or recovery. No child `UnrealEditor-Cmd.exe` process was introduced on the Apply/Undo/Discard path.

## 7. Build and Regression Status

- UE5.6 Direct Build: pass on the final C++ state (`BlueprintWriteCommon.cpp`, `EditorBridgeWriteHandlers.cpp`, `LiveWriteBlueprintOperations.cpp`, `LiveWriteOperationCommon.*`, `LiveWriteOperationRegistry.cpp`, `LiveWriteTransaction.*`).
- Full discovered Python suite: **700 tests OK** (`python -m unittest discover -s tests/python -p "test_*.py"`).
- New focused Python tests: `tests/python/test_blueprint_recovery_helpers.py` (4 tests) pass.
- Python `compileall`: pass.
- `git diff --check`: pass.
- No pushed/rebase/version/release work performed.

## 8. Known Evidence Artifacts

- Crash context preserved under `Build\DirectHost\Saved\Crashes\UECC-Windows-2121A2374B4D70A18F7E089E5A51A658_0000\CrashContext.runtime-xml` (not committed).
- Editor stdout logs: `Output\W1Acceptance\Logs\EditorRecovery*.stdout.log` (not committed).
- MCP stderr logs: `Output\W1Acceptance\Logs\mcp-*.stderr.log` (not committed).
- Workflow/backups remain under `Output\W1Acceptance` / `Backups\W1Acceptance` (not committed).

## 9. Final W1 Status

```text
Blueprint narrow Editor-resident Live Apply = complete
W1 acceptance = complete
Fast Resident Verify = not yet W2 complete
Checkpoint Strong Verify optimization = not yet W3 complete
Generic Blueprint Graph CRUD = explicitly deferred
R5 = deferred by benchmark evidence
```
# P1 Native readiness review (UE 5.6.1 headers, read-only)

Date: 2026-11-01. Branch: `feature/p1-node-move-defect-fix-20261030`.
Baseline: `16779af` plus the commits from this round. Engine: `E:\EPICGAME\UE_5.6`
(`Engine/Build/Build.version`: 5.6.1, CL 44394996, branch `++UE5+Release-5.6`).

Status: **header-level static review only.** No UnrealBuildTool run, no
compilation, no Editor start, no UnrealEditor-Cmd. Nothing in this document is
native evidence; every behaviour claim below is source-level.

## 1. Verified API signatures (UE 5.6.1 headers)

| P1 usage | Engine declaration | Verdict |
| --- | --- | --- |
| `FScopedTransaction Transaction(Context, FText, Blueprint)` | `Editor/UnrealEd/Public/ScopedTransaction.h`: `FScopedTransaction(const TCHAR*, const FText&, UObject*, bool = true)` | matches |
| `Transaction.Cancel()`, `Transaction.IsOutstanding()` | same header: `void Cancel()`, `bool IsOutstanding() const` | matches |
| `GEditor->Trans`, `GEditor->Trans->IsActive()`, `GetUndoContext(false)` | `Editor/UnrealEd/Classes/Editor/EditorEngine.h:412` `TObjectPtr<UTransactor> Trans`; `Transactor.h` `virtual bool IsActive()`, `virtual FTransactionContext GetUndoContext(bool = true)` | matches (`TObjectPtr` supports `->`/null check) |
| `GEditor->CanTransact()`, `IsPlaySessionInProgress()`, `PlayWorld` | `EditorEngine.h:1028`, `:1806`, `:516` | matches |
| `BlueprintEditor->GetFocusedGraph()`, `IsEditable(Graph)`, `GetBlueprintObj()` | `Editor/Kismet/Public/BlueprintEditor.h:281,426,324` | matches (`IsEditable` takes `UEdGraph*`) |
| `AssetEditorSubsystem->FindEditorForAsset(Blueprint, false)` | `Editor/UnrealEd/Public/Subsystems/AssetEditorSubsystem.h:153` returns `IAssetEditorInstance*` | matches |
| `Package->SetDirtyFlag(bool)` | `Runtime/CoreUObject/Public/UObject/Package.h:612` | matches |
| `Graph->GraphGuid`, `Graph->bEditable`, `Graph->GetSchema()` | `Runtime/Engine/Classes/EdGraph/EdGraph.h:104,83,115` | matches (`bEditable` is `uint32:1`) |
| `FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint)` | `Editor/UnrealEd/Public/Kismet2/BlueprintEditorUtils.h:314` | matches (default event arg) |
| `Blueprint->Status`, `bCachedDependenciesUpToDate` | `Runtime/Engine/Classes/Engine/Blueprint.h:504,736` (`TEnumAsByte<enum EBlueprintStatus>`) | matches |
| generic iteration over `UbergraphPages` / `FunctionGraphs` / `MacroGraphs` / `DelegateSignatureGraphs` | `Blueprint.h:539-551`, all `TArray<TObjectPtr<UEdGraph>>` | matches (generic lambda avoids array-type aliasing) |
| `FGuid::Parse(Text, Out)`, `EGuidFormats::DigitsWithHyphensLower` | `Runtime/Core/Public/Misc/Guid.h:397,60` | matches |
| `FDateTime::UtcNow().ToIso8601()` | `Runtime/Core/Public/Misc/DateTime.h:493` | matches |
| `TFunction<bool(bool, FString&, FString&)>` | `Runtime/Core/Public/CoreMinimal.h:89` includes `Templates/Function.h` | matches |
| `FJsonObject::Values`, `HasField`, `TryGetStringField`, `TryGetBoolField`, `TryGetArrayField`, `SetField` | `Runtime/Json/Public/Dom/JsonObject.h:26,74,191,264,274,122-123` | matches |
| `FAutoConsoleCommand(Name, Help, FConsoleCommandDelegate, Flags)` | `Runtime/Core/Public/HAL/IConsoleManager.h:1998` | matches |
| `WITH_DEV_AUTOMATION_TESTS` | defined by UBT (`UEBuildTarget.cs:5621`) | valid compile guard |

Build.cs already depends on `Core`, `CoreUObject`, `Engine`, `Json`, `Kismet`,
`UnrealEd`, `BlueprintGraph`, `AssetRegistry`, `Slate`/`SlateCore`; the new code
adds no module dependency, so no Build.cs change is required.

## 2. Defect found and fixed by this review

`ReadRetainedLiveWriteFastValue` (added in `4952117`) declared its result
parameter as `TSharedPtr<FJsonObject>&`, but the fast-verify handler owns
`TSharedRef<FJsonObject>` (`EditorBridgeWriteHandlers.cpp:281`). A `TSharedRef`
cannot bind to a `TSharedPtr<T>&`, so this would have failed to compile. It is
now `const TSharedRef<FJsonObject>&` in both the header and the implementation,
with a static contract test
(`test_live_fast_verify_contract.py::test_helper_result_parameter_accepts_shared_ref`).

No other P1 signature mismatch was found in the reviewed surface. This is a
header review, not a build: template instantiation, generated headers, module
link order and IWYU problems remain unverified until Native compilation is
authorized.

## 3. Transaction/finalizer semantics (source-level)

`ApplyNodeMoveBatch` still owns the only `FScopedTransaction`:

1. validate every request against the live graph (no writes);
2. open one transaction, `Modify()` Blueprint/graph/every changed node;
3. write all positions, read every position back;
4. `MarkBlueprintAsModified` and confirm the package became Dirty;
5. run `FinalizeBeforeSuccess` (the session's `CommitBatch`) **inside the same
   transaction**;
6. on any failure in 3–5, restore every position, Blueprint `Status`,
   `bCachedDependenciesUpToDate` and the pre-write Dirty flag, then `Cancel()`.

`CommitBatch` performs all reads and checks before it mutates manager state, and
mutates it only at the end (`ExpectedPositions`, `ExpectedFingerprint`,
`bExpectedPackageDirty`, counters) — no "updated then failed" path exists inside
the function itself.

Residual window (documented, not claimed fixed): the manager's expected state is
advanced after the finalizer but before `FScopedTransaction`'s destructor
commits. UE exposes no commit-failure signal for that destructor, so if the
engine ever dropped the transaction between those two points, the manager would
be ahead of the graph. That cannot corrupt data: the next `ValidateBatch`
compares the live fingerprint/positions against the expected state and fails
closed, requiring `end(keep)` and a new session. `writeOccurred=false` after a
finalizer failure is accurate for this batch because the helper restores the
positions/status/Dirty flag before cancelling; it is not a claim that no other
writer touched the asset.

## 4. N1 acceptance contract alignment

Read-only comparison against `docs/N1_NATIVE_ACCEPTANCE_PLAN.md`,
`data/n1_acceptance_matrix.json` and `n1_evidence.py` (N1 HEAD `3104889`; N1 was
not modified).

| N1 case | P1 contract | Result |
| --- | --- | --- |
| R1.0 module/Build.cs | modules unchanged; no new dependency | aligned |
| R2.1 10 batches, no MCP restart | token batches + `writeOccurred`/`sessionFinalized`; no restart path in the tool | aligned (behaviour Native-unverified) |
| R3.0 caller cap 128 | `_MAX_NODE_MOVE_BATCH = 128`, `node-move-batch-limit-exceeded` raised before the bridge | aligned |
| R3.1–R3.3 whole-graph rollback | `NodeMoveRequestLimitUnlimited` only on the restore path; `rolledBack`/`restoreTransactionOpened`/`restoredNodeCount` exposed | aligned (Native-unverified) |
| R3.4 129-request rejection | Python validator rejects 129/256 before any bridge call | aligned |
| R4.0 `writeOccurred`/`sessionFinalized` | true only after the transaction/finalizer succeed | aligned (Native-unverified) |
| R4.2 no-op | no transaction, `writeOccurred=false`, `singleTransaction=false`, Dirty unchanged | aligned |
| R4.1 finalizer fault injection | **new test-build-only seam** (below); no MCP/env trigger | partially aligned — N1 must add the `test-seam:finalizer-fault` premise and audit the seam before the case can leave blocked |
| R6.0/R6.1 retained fast verify | `verified=true`, `targetResolved=true`, transactionId echoed | aligned (Native-unverified) |
| R6.2 post-save/reload old transaction | bridge returns `live-fast-verify-transaction-not-found`/`-target-changed`; **fixed** so Python no longer collapses it into `live-fast-verify-failed` | aligned |
| R7.0 honesty | `exclusiveUI=false`, `humanEditingBlocked=false`, `requiresUserNotEdit=true`, no `RegisterInputPreProcessor` | aligned |

### Test-only finalizer fault seam (for R4.1)

- `BlueprintEditSession.h/.cpp` compile the seam only under
  `#if WITH_DEV_AUTOMATION_TESTS` (UBT sets it to 0 for Shipping).
- Operator trigger: console command
  `UEAgentKit.BlueprintEditSession.FailNextFinalize`; it arms a one-shot flag.
- The next `CommitBatch` fails with
  `blueprint-edit-session-finalize-fault-injected` **before** mutating manager
  state, so the in-transaction restore path is exercised exactly as a real
  finalizer failure would be.
- There is no MCP/socket parameter, environment variable or bridge method for it;
  contract tests assert it is absent from `mcp_workflow_tools.py` and
  `EditorBridge.cpp`.
- N1 must update its matrix (`test-seam:finalizer-fault` premise) and
  `p1AcceptedRevisions` (audit for the new commit) before R4.1 can become ready.
  N1 was not modified by this task.

## 5. Native-blocked items (not executed)

- UBT/UE compile of the plugin, module link, DLL SHA-256, Editor load (R1.1–R1.3).
- Real 10-batch/PID/no-restart (R2.1), large-graph rollback (R3.1–R3.4), the
  finalizer seam in a real transaction (R4.1), Undo/Redo/Save/Reload/PIE (R5),
  Material/DataTable read-back (R6), UI gate (R7.1–R7.3).
- The `WITH_DEV_AUTOMATION_TESTS` command itself has never been compiled; its
  registration and the one-shot behaviour are source-reviewed only.

## 6. Resident save / retained fast-verify downstream review

- A resident authorized save reports `saved=true`, `verified=false`,
  `verificationKind=resident-fast+disk-revision`,
  `childUnrealProcessCount=0`, and never calls `_export_refresh_candidate`; only
  `verification_mode=immediate`/`checkpoint` can produce `verified=true`.
- No downstream path consumes the save response's `verified` flag as a strong
  verdict. `checkpoint_sets.py` sets a child `verified=true` only from
  `verify_live_write_checkpoint` (strong, independent export);
  `verification_trust.py` requires a `verified` semantic stage built from
  independent canonical/commit evidence; `create_authorized_save_rollback_manifest`
  uses the backup manifest, revisions and receipt, not `verified`.
  Therefore a resident save cannot masquerade as Strong Verify.
- Retained fast verify fails closed on every mismatch: missing/foreign
  `transactionId` → `live-fast-verify-transaction-not-found`; session,
  operation, asset or class change → `live-fast-verify-target-changed`;
  re-resolution failure → `live-fast-verify-target-not-found`; read failure →
  the IO's own error. Python now preserves these `live-fast-verify-*` codes
  (`a9aa101`) instead of collapsing them, and the value/session checks remain in
  `workflow_live.py`.
- `RefreshTarget` is called only on the retained record's IO, after identity
  checks; it is not a new write path. No new asset domain or parallel writer was
  introduced.
- CLI duplicate rejection and canonical-skip observability were re-read and left
  unchanged; their tests are in `test_cli_duplicate_args.py` and
  `test_index_skip_observability.py`.

## 7. Recommendation

1. When Native execution is authorized: build the plugin from this branch, record
   the DLL SHA, then run R1.0 (offline), R1.1, R1.2, R3.1/R3.2/R3.3, R4.0/R4.1/
   R4.2, R2.1, R6.0–R6.2 in that order.
2. Before R4.1: N1 adds the seam premise and audits this commit; the operator
   arms the command, runs one token batch, and records `writeOccurred=false`,
   restored positions, restored Dirty and the session's pre-batch expected state.
3. Keep `exclusiveUI=false` everywhere until R7.1–R7.3 pass on a real Editor.

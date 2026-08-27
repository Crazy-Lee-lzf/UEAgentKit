# UEAgentKit W4-0 Contract Freeze and Baseline Result

> Date: 2026-08-27
>
> Branch: `feature/live-writer-expansion`
>
> Product-code baseline: `45e6ea2` (`docs: close W3 checkpoint strong verify`)
>
> Execution plan: `UEAGENTKIT_W4_0_CONTRACT_FREEZE_AND_BASELINE_PLAN_20260827.md`
>
> Parent plan: `UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_DETAILED_PLAN_20260826.md`

## 1. Final Status

```text
W4-0 Contract Freeze and Baseline = complete
```

W4-0 produced no Writer implementation code and no product behavior change.
The existing W1-W3 single-operation surface remains authoritative and unchanged.

## 2. S0 / S1 Baseline State

```text
HEAD                   45e6ea2
Branch                 feature/live-writer-expansion
Active UE process      none (checked before and after baselines)
Uncommitted product    none / docs-only working tree (W4 planning docs)
Python discovered suite 712 / 712 PASS
Ruff                   PASS
```

The working tree at execution contained W4 planning documentation changes and
the new untracked W4 plan files; no `src/` or plugin C++ files were modified.

## 3. Frozen Contract Confirmed

The following W4 contract points from the W4 Detailed Plan were confirmed by
actual manual W3 orchestration and are now frozen:

```text
supported operations      setAssetProperty, setVariableDefault,
                          setComponentProperty, setPinDefault
hard asset bound          4 assets per batch
hard op bound             8 operations per asset, 16 total
value bound               existing Policy maxValueBytes
request size bound        64 KiB
ordering                  asset-grouped, request order preserved
duplicate assets          rejected
cross-package atomicity   NOT claimed
automatic save            NOT authorized
resident read-back as Strong Verify  NOT accepted
source-control write      NOT in scope
```

No observed W3 behavior contradicted sections 3-12 of the parent W4 plan.

## 4. Manual W3 Baseline B0 — one Blueprint / three operations

Fixture:

```text
/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
operations:
  setVariableDefault   TransactionInt            = 42
  setComponentProperty DefaultSceneRoot.RelativeLocation.X = 10
  setPinDefault        EventGraph 11111111-... : A = 7
```

Complete path measured: open asset -> create Change Set -> 3 x
(plan/apply/fast-verify) -> checkpoint Preview/Commit -> Strong Verify ->
Semantic Diff -> Verification Plan -> compile -> validate -> Trust.

### B0 metrics

| Metric | Value |
|---|---:|
| public MCP Tool calls | 19 |
| resident Editor Bridge calls | 42 |
| resident apply count | 3 |
| Fast Verify count | 3 |
| checkpoint save count (Preview+Commit) | 1 + 1 |
| checkpoint strong verify count | 1 |
| Strong Verify child Unreal process count | 1 |
| public result serialized bytes | 54,120 |
| public tool elapsed ms | 10,558.147 |
| wall elapsed ms | 12,339.826 |
| final Semantic Diff | verified (expected=3, actual=3, matched=3, unexpected=0) |
| final Trust verdict | verified |

Bridge method breakdown:

```text
editor.openAsset                         1
editor.applyAssetPropertyLive            3
editor.verifyAssetPropertyLiveFast       9
editor.saveAuthorizedAsset               1
editor.status                           19
editor.inspectAssetLive                  7
editor.compileBlueprint                  1
editor.validateAsset                     1
```

Result evidence:

```text
changeSetId   = cs_WPYecznejU6p-dp8CXCO7w
checkpointId  = cp_EWsVxk0GZ-J2OH2FxcnB7Q
effective ops = 3
verified ops  = 3
afterRevision = sha256:9ae1815ab5ae8f6c95e893fc2fb09ed9adef7b9a8d40a87778b40e45db1d5f2b
```

## 5. Manual W3 Baseline B1 — two assets / four total operations

Fixture:

```text
/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
  setVariableDefault   TransactionInt            = 42
  setComponentProperty DefaultSceneRoot.RelativeLocation.X = 10
  setPinDefault        EventGraph 11111111-... : A = 7

/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset
  setAssetProperty     IntValue                  = 142
```

One Change Set contained all four operations; each asset produced its own W3
checkpoint and its own independent Strong Verify.

### B1 metrics

| Metric | Value |
|---|---:|
| public MCP Tool calls | 27 |
| resident Editor Bridge calls | 63 |
| resident apply count | 4 |
| Fast Verify count | 4 |
| checkpoint save count (Preview+Commit) | 2 + 2 |
| checkpoint strong verify count | 2 |
| Strong Verify child Unreal process count | 2 |
| public result serialized bytes | 79,191 |
| public tool elapsed ms | 20,752.335 |
| wall elapsed ms | 22,861.093 |
| final Semantic Diff | verified (expected=4, actual=4, matched=4, unexpected=0) |
| final Trust verdict | verified |

Bridge method breakdown:

```text
editor.openAsset                         2
editor.applyAssetPropertyLive            4
editor.verifyAssetPropertyLiveFast      12
editor.saveAuthorizedAsset               2
editor.status                           27
editor.inspectAssetLive                 13
editor.compileBlueprint                  1
editor.validateAsset                     2
```

Result evidence:

```text
changeSetId    = cs_BKMDeKHik-w-9tUIOcDWhA
checkpointIds  = [cp_SreB1ZgvU7qwvLbQXM9nGg, cp_OpD2HFm7J4Mv1qLmSf52kg]
BP effective   = 3 verified
DA effective   = 1 verified
afterRevisions = [sha256:84b4d84af360fa0e9318a5401121409b14851474552e3706f8802a4339b70aca,
                  sha256:9e755625679b6976d897dd422d9f4ca2348a49107ad5ff4c4d739cf4fd6096fe]
```

## 6. Fixture Recovery

Both baseline runs used the deterministic transaction fixture plan:

```text
tests/fixtures/multi_operation_transaction_plan.json
planRevision = sha256:d5062503babf97d1c65f6d46809693cbdd89bc6541a28a89f85cf71032f99b6f
```

### After B0 (before B1)

```text
mode                 Reset
deletedCount         2
createdCount         2
verified             true
verifiedCount        2
DA revision          sha256:c77f1de119324c32eb3f5696cb054404b2486d56a5caa48ab583045044f9cf48
BP revision          sha256:d64e1505a2adb7318535ea366fb5de1469077bacc1b69cbed9692f7432493fa4
```

### Final recovery (after B1)

```text
mode                 Reset
deletedCount         2
createdCount         2
verified             true
verifiedCount        2
DA revision          sha256:8b2a3632acf6bfd7d8e17812494c042f79a838b75f3bec56bff822249e8ef1b4
BP revision          sha256:a2decfcb3b164db671c57ac320e849f7f30791439c4daef5fd7e95b7763e68b6
```

## 7. Contract Observations

```text
same-asset continuation         stable for B0 (3 operations in one Change Set)
one checkpoint per asset        B1 created exactly 2 child checkpoints
Strong Verify cost              1 process per effective asset (<= asset count)
partial-state representation    unchanged W3 Change Set lifecycle; no batch state added
no cross-package atomicity      not observed or implied
existing Change Set preserves order/audit history     confirmed
fixture recovery                exact, independently verified after both baselines
```

No W4 stop condition was triggered.

## 8. Regression / Release Gates

```text
Python discovered suite   712 / 712 PASS
Ruff                      PASS
compileall                PASS
git diff --check          PASS
ValidateRelease 0.7.0     PASS
```

UE5.6 Direct Build remains the unchanged W3 entry baseline; W4-0 made no C++
change, so no rebuild was required for this documentation/baseline phase.

## 9. Deliverables

```text
[ ] frozen execution contract          included in this result and parent W4 plan
[x] W4-0 baseline result document      this file
[x] small corrections to parent plan   none required
[x] no Writer implementation code      confirmed
```

## 10. Next Step

W4-1 may begin from this frozen baseline. The first implementation slice is
bounded batch planning (`ue_plan_live_write_batch`) with exact hard/Policy
bounds, read-only validation, and fail-closed child plan exposure.
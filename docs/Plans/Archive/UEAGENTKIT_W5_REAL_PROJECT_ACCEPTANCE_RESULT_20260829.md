# UEAgentKit W5 Real-project Acceptance + Scale Baseline Result

> Date: 2026-08-29 (updated by W5 blocker-closure pass 2026-08-30)
>
> Branch: `feature/live-writer-expansion`
>
> Entry: `10760c8` (`refactor: split agent_workflow into workflow domain modules`)
>
> Plan: `UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_AND_SCALE_BASELINE_DETAILED_PLAN_20260829.md`
>
> Blocker closure plan: `UEAGENTKIT_W5_BLOCKER_CLOSURE_R20_AND_50GB_SCALE_DETAILED_PLAN_20260829.md`
>
> Owner revision: DirectHost controlled writes; Reforge strictly read-only; W5-S 50 GB intermediate first.

## 1. Result Summary

W5 is **partial / blocked-deferred** as a whole.

W5-R:
- **R1** complete: 5 measured samples per cache state, all Trust=verified.
- **R5** complete: 10 measured samples per cache state, all Trust=verified, p95 claimable.
- **R20** **blocked by DirectHost fixture/package lifecycle behavior** after bounded blocker-closure diagnosis. The closure pass reproduced the package rewrite deterministically and classified it as **semantic fixture mutation** (DA `IntValue` changed from `-17` to `108`; BP size changed 29793→29995) while the resident Editor was alive. The product freshness/Revision gate correctly rejected the stale disk. No Revision/Policy/Dirty/Recovery check was weakened. Original R20 remains blocked.
- **Fail-closed case** captured: Policy rejection produced zero mutation and zero writes; before/after DA revision identical.
- **Resident-vs-cold paired comparison** remains **deferred** (was coupled to the blocked R20 evidence slot in the original W5 pass; not required to complete the 50 GB W5-S checkpoint).
- Reforge read-only inventory captured; no Reforge asset modified.

W5-S:
- **50 GB intermediate fixture generated and validated** in `E:\WorkSpace\UEAgentKitPerfProject`.
- `PerformanceFixtureCommandlet` completed with `GenerateArtPayload` (deterministic real Texture/StaticMesh package duplication), checkpoint/resume, disk guards, hard cap, and bounded memory collection.
- Project size at validation: **53,715,786,581 bytes** (~50.03 GiB), **13,980 .uasset**, **79 .umap**, free disk > 50 GB.
- Controlled disposable write fixture measured: **3 valid samples**, all Trust=verified; p50/min/max/mean reported, p95 marked unavailable (n=3).
- 160–180 GB / SimulatedHDD50 remain **not authorized / not claimed**.

## 2. DirectHost R1 Results

Scenario R1 = 1 logical DA operation (`setAssetProperty IntValue=142`), one legal W4 batch, checkpoint Save + Strong Verify + Trust.

| Metric | R1 WarmLoaded (n=5) | R1 WarmUnloaded (n=5) |
|---|---|---|
| totalMs p50 | 12294.378 | 11197.520 |
| totalMs mean | 12264.807 | 11313.071 |
| planMs p50 | 66.108 | 67.063 |
| assetLoadMs p50 | — | 96.456 |
| applyMs p50 | 211.127 | 216.368 |
| checkpointPreviewMs p50 | 126.705 | 126.409 |
| saveMs p50 | 312.159 | 314.487 |
| strongVerifyMs p50 | 10709.853 | 9632.554 |
| trustMs p50 | 470.145 | 472.155 |
| Trust verified | 5/5 | 5/5 |
| noise | repeatable | repeatable |

p95 is **not claimable** for R1 groups because n=5.

## 3. DirectHost R5 Results

Scenario R5 = 5 logical BP operations (`setVariableDefault` x3, `setComponentProperty` x1, `setPinDefault` x1), one legal W4 batch, checkpoint Save + Strong Verify + Trust.

| Metric | R5 WarmLoaded (n=10) | R5 WarmUnloaded (n=10) |
|---|---|---|
| totalMs p50 / p95 | 14432.977 / 14641.421 | 14906.011 / 15653.234 |
| totalMs mean | 14022.409 | 14843.552 |
| planMs p50 / p95 | 212.842 / 253.817 | 229.656 / 257.357 |
| assetLoadMs p50 / p95 | 1119.961 / 1202.539 | 1167.506 / 1255.154 |
| applyMs p50 / p95 | 1046.390 / 1083.049 | 1060.376 / 1081.742 |
| checkpointPreviewMs p50 / p95 | 191.967 / 195.208 | 192.821 / 195.793 |
| saveMs p50 / p95 | 592.120 / 604.527 | 598.381 / 622.653 |
| strongVerifyMs p50 / p95 | 11167.524 / 11236.894 | 11632.100 / 12293.249 |
| trustMs p50 / p95 | 680.948 / 697.597 | 691.062 / 703.057 |
| Trust verified | 10/10 | 10/10 |
| noise | repeatable | repeatable |

Stage contribution (mean): Strong Verify dominates (~73–74%), assetLoad ~7%, apply ~7%, save ~4%, trust ~4–5%.

## 4. R20 Blocked Evidence

R20 = 20 logical operations split into two legal W4 batches (16-op batch + 4-op batch).

The W5 blocker-closure pass performed a bounded deterministic diagnosis:

1. Official `WriteFixturePlan Reset` + independent verification.
2. Fresh Editor sessions and `ue_open_asset` on BP/DA targets.
3. Captured disk SHA-256, size, mtime, `packageDirty`, open state, and canonical semantic exports before/after.
4. Reproduced the rewrite in an active runner session: DA package changed on disk while the Editor stayed alive, even though `packageDirty=false` at inspection time.
5. Canonical semantic comparison found a real semantic change: DA `IntValue` `-17 → 108`; BP size `29793 → 29995`.

Classification: **A — semantic-changing fixture mutation** (not merely binary canonicalization). The DirectHost fixture generation/editor lifecycle is not stable for the original BP/DA R20 workload under resident measurement. The product's freshness/Revision gate correctly rejected the stale disk.

No R20 samples are reported. Original R20 remains **blocked**. A separate stable orchestration workload was not used to replace it.

Evidence: `Output/W5Acceptance/r20-fixture-lifecycle/`, `Output/W5Acceptance/r20-closure/`, and `Output/W5Acceptance/w5r20-stabilized/`.

## 5. Reforge Read-only Measurements

Reforge is strictly read-only in W5. No Reforge asset was created/modified.

```text
projectPath      E:\WorkSpace\Reforge\Reforge.uproject
projectDirBytes  8,827,901,400
projectFileCount 5,427
contentBytes     6,054,822,194
contentFileCount 3,047
uassetCount      3,018
umapCount        18
uassetBytes      4,828,442,998
umapBytes        1,225,903,261
```

Evidence: `Output/W5Acceptance/reforge-readonly-inventory.json`.

## 6. W5-S Status

### 6.1 Generator completion

`PerformanceFixtureCommandlet` now supports:

```text
CreateProjectProfile
CopySeedContent
GenerateSmallAssets
GenerateBlueprintSuite
GenerateArtPayload   (new in blocker closure)
ValidateFixture
CleanupFixture
```

The new `GenerateArtPayload` stage uses deterministic legal UE package duplication of large real seed textures/static meshes from DarkRuins into `/Game/PerfArt/AP_*`, with:

- JSONL checkpoint/resume per completed target
- source/target/bytes/elapsed recorded
- free-disk guard before every batch
- project hard cap 200 GB
- target stop boundary 50 GB
- `CollectGarbage` after each duplicate to bound memory

### 6.2 Validated 50 GB fixture

```text
projectPath              E:\WorkSpace\UEAgentKitPerfProject
projectSizeBytes         53,715,786,581
uassetCount              13,980
umapCount                79
seedContentBytes         27,339,598,713
artPayloadGenerated      263
status                   validated
freeDiskBytes            273,224,110,080
```

`E:\WorkSpace\UEAgentKitPerfProject` was built (editor target + plugin link), and the commandlet ran against UE5.6.

### 6.3 Controlled 50 GB disposable write fixture

Fixture: `/Game/PerfWrite/DA_PerfWrite.DA_PerfWrite` (scalar write fixture, 1 logical op). Each sample used official fixture reset + independent verification + snapshot refresh before measurement. 3 measured WarmLoaded samples, all Trust=verified.

| Metric (ms) | min | p50 | max | mean | stddev |
|---|---:|---:|---:|---:|---:|
| totalMs | 12832.642 | 13154.177 | 16610.624 | 14199.148 | 2094.579 |
| planMs | 62.535 | 78.250 | 91.343 | 77.376 | 14.424 |
| applyMs | 222.308 | 235.937 | 294.334 | 250.860 | 38.262 |
| checkpointPreviewMs | 128.453 | 129.055 | 149.411 | 135.640 | 11.930 |
| saveMs | 314.661 | 347.110 | 391.371 | 351.048 | 38.506 |
| strongVerifyMs | 11211.932 | 11401.885 | 14614.950 | 12409.589 | 1912.259 |
| semanticDiffMs | 80.796 | 82.969 | 97.062 | 86.942 | 8.831 |
| validationMs | 38.819 | 44.529 | 44.805 | 42.718 | 3.379 |
| trustMs | 472.654 | 473.833 | 524.991 | 490.493 | 29.882 |
| assetLoadMs | 91.843 | 118.138 | 122.760 | 110.914 | 16.677 |

p95 is **not claimable** for n=3. All 3 samples `Trust=verified`. Raw evidence under `Output/W5Acceptance/w5s-measure/attempt-*.json`.

## 7. Regression Gates

```text
Python discovered suite           776 / 776 PASS
Ruff                              PASS
compileall                        PASS
ValidateRelease 0.7.0             PASS
git diff --check                  PASS
UE5.6 Direct Build                PASS (after PerformanceFixture C++ changes)
UEAgentKitPerfProject editor      PASS (built against UE5.6)
final DirectHost fixture Reset    PASS (2/2 independently verified)
deterministic fail-closed case    PASS (zero mutation)
```

R1/R5 raw evidence under `Output/W5Acceptance/w5r-final-matrix/`; Reforge inventory under `Output/W5Acceptance/`; R20 lifecycle evidence under `Output/W5Acceptance/r20-fixture-lifecycle/`; W5-S evidence under `Output/W5Acceptance/w5s-generate/` and `Output/W5Acceptance/w5s-measure/`.

## 8. M6 Decision Input

- Strong Verify dominates real UE workflow wall time; it is the primary candidate for future scaling/optimization discussion.
- Public result size is not a material bottleneck at R1/R5 sizes.
- R20 remains blocked by DirectHost fixture lifecycle; original R20 evidence remains unavailable.
- Deterministic fail-closed case is now available for failure-corpus/Memory input.
- 50 GB scale checkpoint is validated and measured; expansion to 100 GB / 160–180 GB / SimulatedHDD50 requires separate owner approval.

## 9. Exit Gate Status

```text
[ ] entry baseline documented descendant of 10760c8         PASS (commits 7ba2e18..)
[x] DirectHost + Reforge environment recorded              PASS
[x] DirectHost safe write targets frozen; Reforge read-only PASS
[x] at least 3 real multi-operation tasks Trust=verified   PARTIAL (R1/R5 complete; R20 blocked)
[x] all targets return to independently verified baseline  PASS (official reset before/after)
[x] R1 stage breakdown                                     PASS
[x] R5 stage breakdown                                     PASS
[ ] R20 20-logical-op workload                             BLOCKED (semantic fixture mutation reproduced)
[ ] resident-vs-cold paired comparison                     DEFERRED (not required for 50 GB closure)
[x] raw attempts + p50/p95/sample counts                   PASS for R1/R5
[x] at least one deterministic fail-closed case            PASS (policy rejection, zero mutation)
[x] Reforge read-only scale inventory                      PASS
[x] 50 GB generator validated                             PASS (53.7 GB, 13,980 uassets)
[x] 50 GB checkpoint Save + Strong Verify                 PASS (3/3 Trust=verified; p95 unavailable)
[ ] 160–180 GB / SimulatedHDD50 reported separately       BLOCKED/deferred (owner approval required)
[x] full Python/release gates                             PASS (776/776, Ruff, compileall, ValidateRelease 0.7.0, diff check)
[x] Result document written                               PASS
```

Overall outcome:

```text
W5-R = partial (R1/R5 complete; R20 blocked; fail-closed PASS; cold-pair deferred)
W5-S = 50 GB checkpoint PASS (validated + 3 measured samples); further scale deferred
W5   = partial / blocked-deferred (R20 remains blocked; 50 GB W5-S checkpoint closed)
```

# UEAgentKit W5 Real-project Acceptance + Scale Baseline Result

> Date: 2026-08-29
>
> Branch: `feature/live-writer-expansion`
>
> Entry: `10760c8` (`refactor: split agent_workflow into workflow domain modules`)
>
> Plan: `UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_AND_SCALE_BASELINE_DETAILED_PLAN_20260829.md`
>
> Owner revision: DirectHost controlled writes; Reforge strictly read-only; W5-S 50 GB intermediate first.

## 1. Result Summary

W5 is **blocked/deferred** as a whole.

W5-R:
- **R1** complete: 5 measured samples per cache state, all Trust=verified.
- **R5** complete: 10 measured samples per cache state, all Trust=verified, p95 claimable.
- **R20** **blocked by DirectHost fixture/package lifecycle behavior**: after each official fixture reset and even in a stabilized single-Editor session with exact baseline proof immediately before the run, the Editor asynchronously rewrites the freshly-created fixture packages (BP/DA) so the fixed SQLite/Revision Export no longer matches disk at Plan time. This is an environment/package-lifecycle issue, not a W4 product failure. Evidence preserved in `Output/W5Acceptance/w5r20-stabilized/` and matrix logs.
- Reforge read-only inventory captured; no Reforge asset modified.

W5-S:
- `PerformanceFixtureCommandlet` source and project skeleton created.
- **50 GB generation not completed/validated** in this pass; therefore W5-S is blocked/deferred at the 50 GB checkpoint.
- 160–180 GB and SimulatedHDD50 remain blocked/deferred (no I/O Governor, no large fixture).

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

R20 = 20 logical operations split into two legal W4 batches (16-op batch + 4-op batch). It was attempted multiple ways:

1. Standard matrix per-sample reset/refresh: failed because Editor first-open after commandlet reset rewrites fixture packages.
2. Stabilized single-Editor session (pre-open targets, live-enabled refresh, exact baseline proof immediately before run): baseline matched at proof time, but disk still changed before Plan, failing `The requested asset differs from the fixed SQLite index or Revision Export`.

Per owner instruction, the attempt was bounded and is now **blocked**. No R20 samples are reported.

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

- `PerformanceFixtureCommandlet` (CreateProjectProfile / CopySeedContent / GenerateSmallAssets / GenerateBlueprintSuite / ValidateFixture / CleanupFixture) source committed in `1d13965`.
- `E:\WorkSpace\UEAgentKitPerfProject` skeleton created (uproject + minimal module), but **no commandlet build and no 50 GB generation** was completed.
- W5-S is **blocked/deferred** at the 50 GB checkpoint.
- 160–180 GB / SimulatedHDD50 are **not claimed**.

## 7. Regression Gates

Pending final full gates run (Python/Ruff/compileall/ValidateRelease/git diff --check). R1/R5 raw evidence under `Output/W5Acceptance/w5r-final-matrix/`; Reforge inventory under `Output/W5Acceptance/`.

## 8. M6 Decision Input

- Strong Verify dominates real UE workflow wall time; it is the primary candidate for future scaling/optimization discussion.
- Public result size is not a material bottleneck at R1/R5 sizes.
- No failure corpus beyond the DirectHost fixture lifecycle blocker was collected because R20 blocked the planned failure-path evidence slot.
- Large scale evidence remains blocked; M6 symbolic compression remains data-driven and not yet justified.

## 9. Exit Gate Status

```text
[ ] entry baseline documented descendant of 10760c8         PASS (commits 7ba2e18..)
[x] DirectHost + Reforge environment recorded              PASS
[x] DirectHost safe write targets frozen; Reforge read-only PASS
[x] at least 3 real multi-operation tasks Trust=verified   PARTIAL (R1/R5 complete; R20 blocked)
[x] all targets return to independently verified baseline  PASS (official reset before/after)
[x] R1 stage breakdown                                     PASS
[x] R5 stage breakdown                                     PASS
[ ] R20 20-logical-op workload                             BLOCKED
[ ] resident-vs-cold paired comparison                     BLOCKED (deferred with R20)
[ ] raw attempts + p50/p95/sample counts                   PASS for R1/R5
[ ] at least one deterministic fail-closed case            BLOCKED (deferred)
[ ] Reforge read-only scale inventory                      PASS
[ ] 50 GB generator validated                             BLOCKED
[ ] 50 GB checkpoint Save + Strong Verify                 BLOCKED
[ ] 160–180 GB / SimulatedHDD50 reported separately       BLOCKED/deferred
[ ] full Python/release gates                             PENDING
[ ] Result document written                               PASS
```

Overall outcome:

```text
W5-R = partial (R1/R5 complete, R20 blocked)
W5-S = blocked/deferred at 50 GB checkpoint
W5   = blocked/deferred
```

# UEAgentKit W3 BP Snapshot Refresh Blocker Closure Plan

> Date: 2026-08-25
>
> Branch: `feature/live-writer-expansion`
>
> W3 status: `blocked`
>
> Current HEAD baseline: `6c73dd2`
>
> Scope: close the Blueprint snapshot-refresh blocker only, then resume W3 C2/C3/C5/C6 acceptance.

## 1. Blocker

Current real UE5.6 failure:

```text
Resident Blueprint save
→ disk Revision changes
→ ue_refresh_asset_index mode=Apply
→ RunAssetCatalog.ps1 -IncludeBlueprints
→ exitCode 1
→ frozen snapshot cannot advance
→ next Blueprint plan reports index-stale
```

C0/C1 already prove the W3 checkpoint implementation itself works. Do not redesign W3 unless new evidence directly points to it.

## 2. Get the Real Failure First

Capture the raw failing Commandlet evidence:

```text
RunAssetCatalog.ps1 -IncludeBlueprints
stdout / stderr
Unreal log
manifest/report if created
exact command line
saved Blueprint Revision
```

Do not diagnose from `exitCode=1` alone.

## 3. Minimal Reproduction

Reduce to:

```text
BP_TransactionBlueprint at known baseline
→ load in real Editor
→ inspect Dirty state
→ authorized save if required
→ record disk SHA-256
→ run RunAssetCatalog.ps1 -IncludeBlueprints directly
```

Record separately:

```text
A. Is the Blueprint Dirty immediately after baseline restore/load?
B. Does AssetCatalog export fail after a real Editor save?
```

Do not assume A and B share one root cause.

## 4. Diagnosis Buckets

### A — AssetCatalog Blueprint export bug

Examples: saved Blueprint canonical extraction, IncludeBlueprints path, commandlet load/compile.

### B — Fixture / package lifecycle issue

Examples: fixture starts Dirty, save rewrites generated state, reset is not deterministic.

### C — Resident Editor ↔ Commandlet interaction

Examples: package/file state not fully flushed or independently observed.

Fix only the evidence-backed cause. Do not add sleeps/retries without proof.

## 5. Fix Boundaries

Preserve:

```text
frozen snapshot Revision == current disk Revision
refresh uses an independent Unreal process
Blueprint refresh never trusts resident memory
index-stale continues to fail closed
```

Do not:

```text
skip Revision checks
force-mark snapshot fresh
copy resident canonical into frozen snapshot
special-case W3 to ignore stale index
```

## 6. Closure Sequence

### R1 — Snapshot refresh

```text
real Editor save
→ disk Revision R1
→ ue_refresh_asset_index Apply
→ frozen snapshot Revision == R1
→ PASS
```

### R2 — Planning after refresh

```text
new Plan on same BP
→ no index-stale
→ PASS
```

### R3 — Resume W3 C2

One Blueprint checkpoint:

```text
setVariableDefault
setComponentProperty
setPinDefault
```

Required:

```text
Checkpoint Save child Unreal = 0
Strong Verify child Unreal   = 1
3 effective operations match
Semantic Diff verified
Trust verified
```

### R4 — Resume W3 C3

```text
10 → 20 → 42
```

Required:

```text
42 = effective
10/20 = superseded audit history
no false verified intermediate state
Change Set closes correctly
```

### R5 — C5/C6

Complete controlled real acceptance for:

```text
stale disk Revision
canonical mismatch
```

Recover fixtures exactly.

## 7. Regression

Run:

```text
focused affected tests
full discovered Python suite
ruff
compileall
git diff --check
UE5.6 Direct Build only if C++ changed
real UE5.6 snapshot refresh smoke
```

Do not hard-code the previous test count.

## 8. Result Update

Update:

```text
docs/Plans/UEAGENTKIT_W3_CHECKPOINT_STRONG_VERIFY_RESULT_20260825.md
```

Record root cause, raw evidence, fix, R1/R2, C2/C3/C5/C6, regression/build state.

If all W3 gates pass:

```text
W3 Checkpoint Strong Verify optimization = complete
```

Otherwise keep W3 blocked and name the exact remaining blocker.

## 9. Scope Guard

Do not start W4 while this closure is open.

Do not implement new writer families, Generic Blueprint Graph CRUD, R5, source-control automation, or release/version/tag/push work.

No Push / Rebase / Release.

## 10. Direct Agent Handoff

```text
Worktree:
E:\WorkSpace\UEAgentKit-LiveWriter

Branch:
feature/live-writer-expansion

Current W3 result:
blocked at BP snapshot refresh after resident save.

First obtain the raw RunAssetCatalog.ps1 -IncludeBlueprints failure log and reproduce outside W3 checkpoint logic.

Distinguish:
1. AssetCatalog Blueprint exporter bug;
2. fixture/package lifecycle issue;
3. resident Editor ↔ commandlet interaction.

Fix only the evidence-backed root cause.

Primary closure gate:
real BP Editor save
→ disk Revision changes
→ ue_refresh_asset_index Apply
→ frozen snapshot Revision equals saved disk Revision
→ subsequent BP Plan no longer index-stale.

Then resume W3 C2/C3/C5/C6.

Do not weaken Revision checks or bypass the frozen snapshot.
Do not start W4.
Do not Push/Rebase/Release.
```
# UEAgentKit W5 Blocker Closure — R20 Stable Fixture + 50 GB Scale Checkpoint Detailed Plan

> Date: 2026-08-29
>
> Worktree: `E:\WorkSpace\UEAgentKit-LiveWriter`
>
> Branch: `feature/live-writer-expansion`
>
> Entry HEAD: `afc0b62 docs: update W5 result regression gate status`
>
> Current W5 Result: `E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_RESULT_20260829.md`
>
> Parent W5 Plan: `E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_AND_SCALE_BASELINE_DETAILED_PLAN_20260829.md`
>
> Scope: close the two remaining W5 blockers without rerunning already-valid R1/R5 evidence and without weakening W4 safety bounds.

## 1. Current Frozen Facts

W5 has completed its first execution pass with a truthful blocked/deferred outcome.

```text
W5-R
  R1 WarmLoaded / WarmUnloaded   complete, n=5 each, Trust verified
  R5 WarmLoaded / WarmUnloaded   complete, n=10 each, Trust verified
  R20                            blocked

W5-S
  PerformanceFixture source      exists
  PerfProject skeleton           exists at E:\WorkSpace\UEAgentKitPerfProject
  50 GB fixture                  not generated / not validated
  160–180 GB                     not authorized in this closure
  SimulatedHDD50                 not available
```

R1/R5 results are frozen evidence. **Do not rerun them merely because blocker closure begins.** They may only be rerun if product behavior used by those scenarios changes.

Current measured finding is also frozen:

```text
Strong Verify = ~73–74% of R5 wall time
```

This plan does not optimize Strong Verify. W5 is measurement/acceptance; optimization requires a separate evidence-driven plan.

## 2. The Two Blockers

### B1 — R20 DirectHost fixture/package lifecycle

The existing R20 workload is valid with respect to W4 bounds:

```text
20 logical operations
→ batch 1 = 16 ops
→ batch 2 = 4 ops

max assets / batch       <= 4
max ops / asset          <= 8
max total ops / batch    <= 16
```

The workload itself is not rejected by W4 bounds. It is blocked because the commandlet-reset BP/DA packages can be asynchronously rewritten after Editor first-open, invalidating the fixed SQLite / Revision Export before Plan.

This is a **fixture lifecycle blocker until proven otherwise**. Do not change Revision matching, do not ignore SHA mismatch, and do not classify the rewrite as harmless without deterministic evidence.

### B2 — W5-S 50 GB scale fixture

Current source exists:

```text
E:\WorkSpace\UEAgentKit-LiveWriter\Plugin\UEAgentKit\Source\UEAgentKitEditor\Private\PerformanceFixtureCommandlet.cpp
```

But the 50 GB fixture has not been built or validated.

The current commandlet supports:

```text
CreateProjectProfile
CopySeedContent
GenerateSmallAssets
GenerateBlueprintSuite
ValidateFixture
CleanupFixture
```

The older performance design also called for a large-art-payload stage. The current source has **no `GenerateArtPayload` action**. DarkRuins seed content is only roughly 25 GB, so the implementation must not assume small assets / simple Blueprints alone will reliably and efficiently reach 50 GB.

The C++ commandlet was introduced during W5, therefore **UE5.6 Direct Build is a mandatory precondition before using it**.

## 3. Goals

### Goal A — close R20 with trustworthy samples

Obtain exact-baseline R20 evidence using the **existing R20 workload definition** if at all feasible:

```text
benchmarks/w5/workloads.py
R20 = BP 8 + DA 8, then BP 2 + DA 2
```

The preferred closure is to stabilize the fixture lifecycle, not silently replace the workload.

Target samples:

```text
WarmLoaded    1 warmup + 5 valid measured
WarmUnloaded  1 warmup + 5 valid measured
```

For n=5 report:

```text
p50 / min / max / mean / stddev
p95 = unavailable
```

Every measured sample requires exact pre-sample baseline proof.

### Goal B — complete the owner-authorized 50 GB intermediate scale checkpoint

Build and validate `E:\WorkSpace\UEAgentKitPerfProject` to the 50 GB stage, then run a bounded scale measurement that includes at least:

```text
fixture size / asset counts / generation timing
checkpoint/resume evidence
50 GB controlled checkpoint Save timing
50 GB controlled Strong Verify timing
Trust result
```

This plan **must stop at the 50 GB checkpoint** and report to the owner. It does not authorize 100 GB, 160–180 GB, or SimulatedHDD50 generation.

## 4. Non-goals / Hard Prohibitions

```text
NO Reforge mutation
NO changes to W4 max assets / ops limits
NO revision mismatch bypass
NO weaker Policy
NO treating resident Fast Verify as Strong Verify
NO changing Strong Verify implementation for benchmark convenience
NO accepting baseline drift for R20
NO fabricated scale data
NO claiming 50 GB as 160–180 GB evidence
NO automatic expansion beyond 50 GB
NO push / rebase / tag / release / published-version change
```

Reforge remains strictly read-only.

## 5. Entry Gate

Before product or fixture changes:

```text
cd /d E:\WorkSpace\UEAgentKit-LiveWriter

git status --short --branch
git log --oneline -8
```

Expected baseline:

```text
branch = feature/live-writer-expansion
HEAD   = afc0b62
working tree clean
```

Also confirm no active `UnrealEditor`, `UnrealEditor-Cmd`, or `UnrealBuildTool` process before Direct Build / fixture reset.

Read, in this order:

```text
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_RESULT_20260829.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_AND_SCALE_BASELINE_DETAILED_PLAN_20260829.md
E:\WorkSpace\UEAgentKit-LiveWriter\benchmarks\w5\workloads.py
E:\WorkSpace\UEAgentKit-LiveWriter\tests\fixtures\multi_operation_transaction_plan.json
E:\WorkSpace\UEAgentKit-LiveWriter\Plugin\UEAgentKit\Source\UEAgentKitEditor\Private\PerformanceFixtureCommandlet.cpp
E:\WorkSpace\UEAgentKit-LiveWriter\docs\PERFORMANCE_TEST_PLAN.md
```

## 6. Phase B1-0 — Characterize the First-open Rewrite

Do not begin by modifying W4 product code.

Create a deterministic diagnostic that captures the transaction fixture around first-open.

For both:

```text
/Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint
/Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset
```

capture:

```text
package disk SHA-256
file size
file mtime
canonical exported semantic value(s)
Editor loaded state
open-in-asset-editor
packageDirty
Blueprint compile state where applicable
```

Required sequence:

```text
1 official WriteFixturePlan Reset
2 independent Reload verification
3 capture disk baseline with Editor closed
4 start one clean Editor
5 open/load exact R20 targets
6 observe for at least 30 seconds or until a documented quiescence rule is met
7 capture every detected SHA / Dirty transition
8 if package becomes Dirty, explicitly record when and why as far as UE evidence exposes it
9 save only as part of the diagnostic, then record post-save SHA
10 close Editor
11 reopen once and prove whether the package rewrites again
```

Quiescence must be deterministic. A suggested minimum is:

```text
packageDirty=false
AND disk SHA unchanged for >= 10 seconds
AND no pending compile
```

Do not use a fixed sleep alone as proof.

### B1-0 semantic classification

Compare canonical semantic exports before and after the automatic binary rewrite.

Classify exactly one of:

```text
A semantic-changing rewrite
  → fixture generation correctness blocker

B semantic-identical binary/package canonicalization
  → fixture lifecycle/canonical-save blocker

C no reproducible rewrite
  → rerun exact R20 baseline proof before changing code
```

Preserve diagnostic JSON under:

```text
Output/W5Acceptance/r20-fixture-lifecycle/
```

## 7. Phase B1-1 — Stabilize the Fixture, Not the Revision Gate

Preferred closure order:

### Strategy A — stabilized official test-fixture lifecycle

If B1-0 proves semantic-identical package canonicalization, add a **test-only fixture stabilization path** around the existing fixture Reset.

Conceptual sequence:

```text
WriteFixturePlan Reset
→ independent semantic verification
→ first-open in clean Editor
→ wait for deterministic quiescence
→ explicit Save only if required to canonicalize the generated package
→ close Editor
→ refresh fixed snapshot from the now-stable disk package
→ reopen clean Editor
→ prove SHA remains unchanged through the same observation window
```

This may be implemented in W5 acceptance infrastructure or a narrowly-scoped fixture wrapper. It must not become a public Writer behavior.

The stabilized baseline values must still equal the fixture plan's expected semantic values.

### Strategy B — correct fixture generation itself

If the rewrite changes semantics, diagnose and fix `WriteFixturePlan` / BP fixture generation so Reset produces the expected stable package directly.

Any C++ fixture-generation change requires:

```text
UE5.6 Direct Build
focused fixture reset/reload test
full regression suite later
```

### Strategy C — alternative stable R20 orchestration fixture (fallback only)

Only if the original BP fixture cannot be stabilized without invasive product changes, a dedicated DataAsset-heavy R20 workload may be added to measure **bounded orchestration scale** using stable scalar fixture properties.

But this is a fallback with strict labeling:

```text
R20-original-BP = still blocked
R20-stable-orchestration = measured alternative
```

It does **not** silently convert the original blocked R20 to PASS.

Do not use Strategy C until A/B have deterministic failure evidence.

## 8. Phase B1-2 — R20 Measurement Closure

If Strategy A or B closes the exact original R20 fixture:

Run the unchanged R20 workload.

For every measured sample:

```text
pre-sample exact disk Revision proof
pre-sample expected semantic value proof
Plan uses matching frozen snapshot
2 legal W4 batches only
all resident Apply receipts valid
Fast Verify each effective operation
checkpoint Save explicit
aggregate Strong Verify
Trust = verified
post-run recovery/reset
next sample baseline proven exact again
```

Stop immediately if exact baseline cannot be proven.

Target:

```text
WarmLoaded    5 valid measured samples
WarmUnloaded  5 valid measured samples
```

Do not claim p95 with n=5.

### Preserve R1/R5

Do not rerun R1/R5 unless B1 fixture changes modify behavior shared by their targets. If shared behavior changes, rerun only the affected scenario/cache-state.

## 9. Phase B1-3 — Close the Remaining W5-R Evidence Gaps

The current W5 Result also has two independent gaps that should not remain coupled to R20.

### Resident vs cold paired comparison

Capture this on a stable workload using the same logical target/value transition.

Prefer the already-stable R5 fixture unless the parent W5 contract explicitly requires another target.

Report two separate quantities:

```text
mutation-path speedup
persisted-workflow ratio
```

Never compare resident mutation-only time against cold mutation+Strong-Verify and call it an overall speedup.

### Deterministic fail-closed case

Add one bounded, non-mutating rejection case, for example:

```text
stale Revision
or
Policy rejection
```

Acceptance:

```text
expected stable error code
zero resident Apply
zero Save
zero package Dirty
baseline Revision unchanged
```

This is evidence for W5 and later Memory failure-corpus use; it is not a request to manufacture a product defect.

## 10. B1 Exit Gate

Exact R20 closure succeeds only if:

```text
[ ] first-open rewrite mechanism classified with evidence
[ ] no Revision / Policy gate weakened
[ ] exact R20 workload remains within W4 bounds
[ ] 5 WarmLoaded measured samples valid
[ ] 5 WarmUnloaded measured samples valid
[ ] all valid samples Trust=verified
[ ] p95 not claimed for n=5
[ ] resident-vs-cold paired comparison captured
[ ] deterministic fail-closed case captured with zero mutation
[ ] final official DirectHost fixture Reset + independent verification passes
```

If exact R20 remains impossible after bounded Strategy A/B closure work:

```text
R20-original-BP = blocked
```

Preserve the evidence and continue B2. Do not spend open-ended time trying to make the benchmark pass.

## 11. Phase B2-0 — Build and Audit the Performance Fixture

Before any 50 GB generation, run UE5.6 Direct Build at current source state.

```text
E:\WorkSpace\UEAgentKit-LiveWriter\scripts\BuildPluginDirect.cmd -EngineRoot E:\EPICGAME\UE_5.6
```

This is mandatory because `1d13965` added C++.

Then verify:

```text
PerformanceFixtureCommandlet is present in compiled plugin
E:\WorkSpace\UEAgentKitPerfProject\UEAgentKitPerfProject.uproject exists
project opens/commandlet starts against UE5.6
plugin linkage/junction points at the intended compiled plugin
no second stale plugin copy is being loaded
```

Run tiny action smoke tests before copying/generating tens of GB:

```text
CreateProjectProfile
ValidateFixture on empty/minimal project
CleanupFixture on generated test-only roots
```

Do not start long generation until these are green.

## 12. Phase B2-1 — Make the 50 GB Generator Actually Capable of Reaching 50 GB

The current seed is approximately 25 GB. Complete a large-art-payload stage before relying on small assets.

Add / complete:

```text
GenerateArtPayload
```

Purpose:

```text
duplicate a deterministic, bounded set of real large Texture / StaticMesh / Material-family seed assets
using valid Unreal package duplication
until the fixture approaches the 50 GB stage target
```

Requirements:

```text
fixed seed and deterministic target names
never modify F:\UELecture\DarkRuinsMegascansSample
valid unique package paths
record source asset, target asset, bytes, elapsed time
checkpoint at bounded intervals
resume skips already completed deterministic targets
check free disk before every batch
stop immediately below minimum free-space threshold
never exceed the configured project hard cap
```

Use real UE package operations. Do not create fake `.uasset` files or pad files and count them as UE project scale.

After the art payload, use `GenerateSmallAssets` / `GenerateBlueprintSuite` to add realistic package-count pressure rather than to provide most of the bytes.

### Important audit item

Verify the current `DuplicateSmallAsset` target package/path usage against UE `IAssetTools::DuplicateAsset` semantics before long generation. A path bug multiplied by thousands of assets is unacceptable.

## 13. Phase B2-2 — Checkpoint / Resume / Disk Protection Proof

Before reaching 50 GB, deliberately exercise resume:

```text
run one bounded generation chunk
stop cleanly
restart the same action
prove completed deterministic targets are not regenerated
prove manifest/checkpoint counts advance correctly
```

Also test disk protection without consuming real space to the threshold, using a unit-level or injectable boundary where practical.

Required evidence:

```text
manifest schema/version
seed source
current size
asset counts
completed action/checkpoint counts
resume start point
free-disk value
hard-cap value
```

Do not claim crash recovery unless an actual interrupted/resumed execution is tested.

## 14. Phase B2-3 — Reach and Validate the 50 GB Intermediate Checkpoint

Authorized target for this plan:

```text
E:\WorkSpace\UEAgentKitPerfProject
50 GB intermediate checkpoint
```

Validation must distinguish:

```text
project directory bytes
Content bytes
uasset count
umap count
other generated/test metadata
free disk remaining
```

50 GB must be real project data generated/copied through the documented fixture process.

Validation should include at minimum:

```text
Asset Registry sees generated roots
sample generated assets load successfully
no malformed package flood in log
manifest = consistent with disk
checkpoint files = consistent with manifest
free disk >= configured minimum
```

Do not proceed to 100 GB or 160–180 GB after PASS.

## 15. Phase B2-4 — 50 GB Controlled Write / Save / Strong Verify Measurement

Create/use a **dedicated disposable write fixture inside UEAgentKitPerfProject**. Do not mutate copied DarkRuins seed assets for benchmark convenience.

The write fixture must use the same safe narrow operation families already supported by W4.

At 50 GB project scale measure at least:

```text
Plan
resident Apply
Fast Verify
checkpoint Preview
Save
Strong Verify
Trust
total wall time
child Unreal process count
result bytes
```

Use a bounded representative workload. Three valid measured samples are sufficient for this **intermediate 50 GB checkpoint**; report:

```text
n=3
p50/min/max/mean
p95 unavailable
```

All measured samples must end Trust=verified and return the disposable fixture to an independently verified baseline.

The purpose is to answer whether project scale materially changes checkpoint Save / Strong Verify behavior, not to claim final 160–180 GB performance.

## 16. B2 Exit Gate

```text
[ ] UE5.6 Direct Build PASS on current C++
[ ] PerformanceFixture commandlet smoke PASS
[ ] GenerateArtPayload or equivalent real-package byte-growth stage exists
[ ] 50 GB generation is checkpointed and resumable
[ ] disk protection verified
[ ] 50 GB project checkpoint reached
[ ] fixture validation PASS
[ ] controlled 50 GB write samples valid
[ ] checkpoint Save timing captured
[ ] Strong Verify timing captured
[ ] all 50 GB write samples Trust=verified
[ ] no copied Reforge/DarkRuins seed asset mutated by writer tests
[ ] no 100/160–180 GB expansion performed
```

## 17. Regression Gates

After all product/fixture code changes:

```text
current discovered Python suite = all PASS
Ruff = PASS
compileall = PASS
ValidateRelease 0.7.0 = PASS
git diff --check = PASS
```

If C++ changed after the B2-0 Direct Build, rerun Direct Build again at final source state.

Final DirectHost fixture:

```text
official Reset
independent Reload verification
```

No Unreal / UBT process should remain after final acceptance.

## 18. Result and Status Semantics

Update:

```text
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_RESULT_20260829.md
```

Do not overwrite historical blocked evidence; append a blocker-closure section with new evidence paths and status transitions.

Possible outcomes:

### Outcome 1 — B1 + 50 GB both pass

```text
W5-R = complete
W5-S 50 GB checkpoint = complete
W5-S 160–180 GB / HDD50 = deferred pending owner authorization
W5 overall = owner-authorized current scope complete, full-scale exit still deferred
```

Do **not** call the original full W5 scale contract complete unless the owner explicitly decides the 50 GB checkpoint is sufficient or authorizes the remaining scale stages.

### Outcome 2 — R20 remains blocked, 50 GB passes

```text
W5-R = partial
W5-S 50 GB checkpoint = complete
W5 = blocked/deferred by R20 + later full-scale scope
```

### Outcome 3 — R20 passes, 50 GB cannot be generated/validated

```text
W5-R = complete
W5-S = blocked at 50 GB
W5 = blocked/deferred
```

## 19. Stop Conditions

Stop and report instead of widening scope when any of these occurs:

```text
R20 requires weakening Revision/Policy safety
R20 exact baseline cannot be made stable after bounded A/B fixture work
50 GB generation would drop disk below minimum free-space guard
fixture requires fake/non-UE files to reach target size
PerformanceFixture requires a new runtime dependency
large generation reveals a product correctness change outside fixture scope
Direct Build cannot pass without unrelated refactor
owner authorization is required to expand beyond 50 GB
```

## 20. Expected Commit Boundaries

Suggested logical checkpoints; do not commit until authorized if the current Agent is operating under explicit no-auto-commit instruction.

```text
1 docs/test: characterize W5 R20 fixture lifecycle
2 test/fix: stabilize W5 DirectHost fixture lifecycle (only if needed)
3 test: close W5 R20 / cold-pair / fail-closed evidence
4 feat: complete 50GB performance fixture generation path
5 test: validate 50GB fixture and scale measurement
6 docs: update W5 blocker-closure result
```

No push/rebase/tag/release.

## 21. Definition of Done for This Closure Pass

This blocker-closure pass is done when:

```text
R20 is either deterministically closed or explicitly remains blocked after bounded diagnosis,
AND
50 GB intermediate fixture is either validated with real evidence or explicitly blocked by a concrete reproducible reason,
AND
all regression gates appropriate to changed code are green,
AND
W5 Result truthfully reflects which parts are complete vs deferred.
```

The pass must then stop for owner review before any 100 GB / 160–180 GB / HDD50 expansion.

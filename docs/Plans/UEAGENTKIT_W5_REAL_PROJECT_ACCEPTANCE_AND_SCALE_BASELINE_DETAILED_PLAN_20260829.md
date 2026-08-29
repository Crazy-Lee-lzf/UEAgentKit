# UEAgentKit W5 Real-project Acceptance + Scale Baseline Detailed Plan

> Date: 2026-08-29
>
> Task: W5 — Real-project Acceptance + Scale Baseline
>
> Worktree: `E:\WorkSpace\UEAgentKit-LiveWriter`
>
> Branch: `feature/live-writer-expansion`
>
> Entry HEAD: `10760c8` (`refactor: split agent_workflow into workflow domain modules`)
>
> W4 closure: `24bf088` (`docs: close W4 full acceptance and documentation`)
>
> D1 closure: `10760c8`
>
> UE root: `E:\EPICGAME\UE_5.6`
>
> Latest published product remains `0.7.0` for UE5.6. This plan does not authorize Push / Rebase / Tag / Release or published-version changes.
>
> Owner revision (2026-08-29): **Reforge is strictly read-only for W5.** All controlled write-path acceptance and timing use the existing DirectHost test project at `E:\WorkSpace\UEAgentKit-LiveWriter\Build\DirectHost\HostProject.uproject`. No acceptance namespace or any other test asset is created or modified inside Reforge. Reforge is used only for non-mutating real-project scale/integration measurements. The W5-S large-fixture work starts with a validated **50 GB intermediate** `E:\WorkSpace\UEAgentKitPerfProject`; expansion to 100 GB / 160–180 GB is a separate owner-approved step after the 50 GB checkpoint passes.

## 0. Executive Summary

W5 is the final Writer-line acceptance/measurement stage after W4 + D1.

It is **not** a new Writer feature stage. Its job is to answer, with real data:

```text
Does the W4 bounded Writer remain safe and usable on a real DirectHost project
and on Reforge's real project data (read-only)?
How much orchestration/UE time is spent in each stage?
How much faster is resident Apply than the equivalent cold Commandlet path?
How does checkpoint Save + independent Strong Verify behave as project/storage scale grows?
Which real failures are worth feeding into later Memory distillation?
```

W5 is split into two evidence blocks:

```text
W5-R  DirectHost controlled write-path acceptance + latency
      + Reforge read-only real-project scale/integration measurements
W5-S  large-project / storage-degradation scale baseline (50 GB intermediate first)
```

W5-R may finish before W5-S. However **W5 itself is not complete until the required scale evidence is either produced or explicitly marked blocked/deferred by the project owner**.

Known entry fact:

```text
E:\WorkSpace\UEAgentKitPerfProject   DOES NOT currently exist
```

A separate historical performance worktree exists:

```text
E:\WorkSpace\UEAgentKit-Performance
branch: feature/performance-benchmarks
current observed HEAD: a7c5ae9
```

It currently contains performance plans / DarkRuins baseline work, but no committed `PerformanceFixture` implementation matching the 160–180 GB plan was found at W5 planning time. Do not pretend the large fixture already exists.

## 1. Project Background for a Fresh Agent

UEAgentKit is an Unreal project-understanding and safe Agent-operation toolkit. It is not intended to be unrestricted Unreal Editor remote control.

Core product chain:

```text
understand project state
→ Policy / Revision gated Plan
→ narrow resident mutation
→ Fast resident evidence
→ explicit authorized Save
→ independent Strong Verify
→ Semantic Diff / Verification Plan / Trust
→ exact Recovery
```

Writer development state at W5 entry:

```text
W0  latency / resident baseline                         complete
W1  Blueprint narrow resident write                     complete
W2  Fast Resident Verify                                complete
W3  Checkpoint Strong Verify                            complete
W4  bounded multi-operation / multi-asset orchestration complete
D1  agent_workflow structural split                     complete
W5  real-project + scale acceptance                     CURRENT
```

W4 final real UE5.6 acceptance:

```text
C1-C12                   PASS
W4-6 H1-H6               PASS
Python suite             766 / 766 PASS at D1 closure
Tool Registry            unchanged at D1
final W4 Trust paths      verified
```

D1 did not change product behavior. It moved orchestration into:

```text
src/ue_agent_kit/workflow_common.py
src/ue_agent_kit/workflow_plan.py
src/ue_agent_kit/workflow_live.py
src/ue_agent_kit/workflow_verify.py
src/ue_agent_kit/workflow_batch.py
```

`agent_workflow.py` remains the compatibility facade.

## 2. Exact Documents to Read

The W5 Agent must read from **this exact worktree**. Do not accidentally read another UEAgentKit branch/worktree.

```text
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Handoffs\UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\README.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_D1_AGENT_WORKFLOW_SPLIT_RESULT_20260829.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\PERFORMANCE_TEST_PLAN.md
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_AND_SCALE_BASELINE_DETAILED_PLAN_20260829.md
```

Repository facts and committed Result documents override old chat history or stale sections in older Master/Midterm documents.

## 3. Frozen W4 Safety Contract

W5 is forbidden from widening W4 merely to make a benchmark larger.

W4 public high-level workflow remains:

```text
ue_plan_live_write_batch
ue_apply_live_write_batch
ue_save_change_set_checkpoint
ue_verify_change_set_checkpoint
ue_recover_live_write_batch
```

Initial W4 mutation allowlist remains:

```text
setAssetProperty
setVariableDefault
setComponentProperty
setPinDefault
```

Hard bounds remain:

```text
max assets per batch       4
max ops per asset          8
max total ops per batch   16
max request               64 KiB
```

### 3.1 Important correction: the W5 "20 operations" workload

The Master/Midterm task card asks for:

```text
1 operation / 5 operations / 20 operations
```

A 20-operation **single W4 batch is illegal** because W4 is frozen at 16 total operations.

Therefore W5 defines:

```text
20-op workload = one benchmark user scenario containing 20 logical writes
                 executed as multiple legal W4 bounded workflows
```

The report must explicitly record:

```text
logicalOperationCount = 20
batchCount
operationsPerBatch[]
changeSetCount
checkpointSetCount
```

Never describe this scenario as one atomic batch or one cross-package transaction.

## 4. Scope / Non-goals

W5 may:

```text
add benchmark / measurement harness
add timing/report serialization needed only for measurement
exercise existing Writer paths on explicitly safe targets
record deterministic failure evidence
build/consume a dedicated performance fixture according to PERFORMANCE_TEST_PLAN
```

W5 must not:

```text
add new Writer operations
raise W4 hard bounds
weaken Policy / Revision / Dirty / session / transaction checks
replace Strong Verify with resident read-back
claim cross-package atomicity
modify arbitrary production Reforge assets
optimize before measurement proves a bottleneck
start Memory / P4 / Track X feature implementation
change published version
Push / Rebase / Tag / Release
```

If W5 discovers a real product bug, record the failing evidence and create a narrow blocker-closure plan. Do not silently broaden W5 into a feature rewrite.

## 5. Same-machine Resource Contract

This machine runs multiple Agents. W5 is **UE-heavy** and owns the exclusive Unreal lease while real UE measurement is running.

During W5 UE runs, no other Agent may start:

```text
UnrealEditor.exe
UnrealEditor-Cmd.exe
UnrealBuildTool
BuildPluginDirect.cmd
WriteFixturePlan
another real UE acceptance suite
another fixture/snapshot refresh using Unreal
```

Track V is allowed to continue because its plan explicitly forbids Unreal/UBT and uses only Python/SQLite/browser resources.

Do not modify:

```text
E:\WorkSpace\UEAgentKit-KnowledgeWeb
E:\WorkSpace\UEAgentKit-Performance
```

from the W5 Agent. Cross-worktree code may only be consumed after a committed checkpoint and an explicit merge/cherry-pick decision.

## 6. W5-0 — Entry Baseline and Environment Freeze

Before changing code or starting UE:

```text
1. cd E:\WorkSpace\UEAgentKit-LiveWriter
2. confirm branch == feature/live-writer-expansion
3. confirm HEAD starts from 10760c8 or a documented descendant
4. inspect git status; do not clean/reset other work
5. read all absolute paths from section 2
6. run the actual discovered Python baseline
7. confirm no conflicting Unreal/UBT process
```

### 6.1 Resolve the real Reforge project; do not guess

At plan creation time these environment variables were not defined in the current shell:

```text
UEAK_REFORGE_PROJECT
UEAK_ENGINE
UEAK_PROJECT
```

The Agent must resolve the actual Reforge `.uproject` from existing project config, benchmark config, environment supplied to the execution session, or an explicit user-provided path.

Do **not** invent a path from project name.

Record:

```text
projectPath
projectKey
UE version
engine path
project directory bytes
Content bytes
.uasset count
.umap count
index database path + SHA-256
the active/frozen snapshot generation
policy path + SHA-256
plugin HEAD
```

### 6.2 Safe write-target gate (owner-revised)

**Reforge is strictly read-only.** W5 does not pick any Reforge gameplay/content asset as a write target and does not create `/Game/UEAgentKitW5` or any other acceptance namespace inside Reforge.

All controlled write-path targets are the existing DirectHost acceptance fixtures:

```text
Project          E:\WorkSpace\UEAgentKit-LiveWriter\Build\DirectHost\HostProject.uproject
DA               /Game/UEAgentKitWriteTests/Transactions/DA_TransactionAsset.DA_TransactionAsset
BP               /Game/UEAgentKitWriteTests/Transactions/BP_TransactionBlueprint.BP_TransactionBlueprint
Policy           E:\WorkSpace\UEAgentKit-LiveWriter\Output\W4Acceptance\acceptance-policy.json
Fixture plan     E:\WorkSpace\UEAgentKit-LiveWriter\tests\fixtures\multi_operation_transaction_plan.json
Reset strategy   scripts\RunWriteFixturePlan.ps1 -Mode Reset + independent Reload verify
```

Freeze an explicit W5 write-target manifest for the DirectHost targets containing for every target:

```text
assetPath
assetClass
exact target/property identity
baseline disk Revision
baseline canonical value
reason target is safe/disposable
reset strategy
Policy authorization
```

If the DirectHost fixture is unavailable, W5-R mutation acceptance is `blocked`. Reforge read-only discovery/scale inventory may continue independently.

## 7. W5-1 — Measurement Harness

Measurement must be deterministic and stage-aware. Do not measure only one wall-clock number.

Recommended implementation area:

```text
benchmarks/w5/
  runner.py
  workloads.py
  metrics.py
  README.md

tests/python/test_w5_benchmark.py
scripts/RunW5Acceptance.ps1 or repository-equivalent wrapper
```

Raw runtime evidence should normally stay untracked under:

```text
E:\WorkSpace\UEAgentKit-LiveWriter\Output\W5Acceptance\<run-id>\
```

Do not commit Output artifacts. If the repository already has a tracked benchmark-baseline convention, follow that convention only after inspecting it.

### 7.1 Per-attempt schema

Record at least:

```text
runId
scenarioId
sampleIndex
projectPath / projectScale
storageProfile
assetPaths / assetClasses
cacheState                 WarmLoaded | WarmUnloaded | Cold
logicalOperationCount
batchCount / operationsPerBatch
publicMcpCallCount
residentBridgeCallCount
childUnrealProcessCount
resultBytes
planMs
policyRevisionMs
applyMs
fastVerifyMs
compileMs
checkpointPreviewMs
saveMs
strongVerifyMs
semanticDiffMs
validationMs
trustMs
recoveryOrResetMs
totalMs
success
errorCode
finalTrustState
beforeRevisions
afterRevisions
```

Where existing internals expose finer values, also record:

```text
indexMs
assetLoadMs
mutationMs
bytesRead / bytesWritten
filesOpened
```

Do not fake unavailable stage data. Use `null` plus an explicit measurement-gap field.

### 7.2 Sample/statistics rules

For each core performance scenario:

```text
warmup runs      >= 1
measured runs    >= 10
preferred        20 when runtime is practical
```

Summary:

```text
sample count
min / max
p50
p95 (only claim when measured sample count >= 10)
mean
standard deviation
```

All failed attempts remain in raw evidence; do not delete them from the denominator and pretend the run was clean.

## 8. W5-R — DirectHost Controlled Write-path Acceptance + Reforge Read-only Integration

### 8.1 Controlled write-path acceptance (DirectHost)

W5-R must complete at least **three real multi-operation tasks** on the existing DirectHost acceptance fixtures, all ending with existing Trust `verified`.

The three tasks cover different real target shapes:

```text
DA-only task                    setAssetProperty scalar
BP-only task                    variable + component + pin
multi-asset mixed task          BP 3 ops + DA 1 op
```

Each real task must execute the existing full chain:

```text
read/discover
→ bounded Plan
→ resident Apply
→ Fast Verify each logical write
→ checkpoint Save
→ independent Strong Verify
→ Semantic Diff
→ bounded required validation
→ Trust
→ exact fixture reset (DirectHost)
→ independent baseline verification
```

Required result per task:

```text
Trust = verified
no unexpected semantic change
no unrelated asset mutation
exact final reset to baseline
```

### 8.2 Reforge read-only integration measurements

Reforge is used only for non-mutating real-project measurements. These may include:

```text
project directory bytes / Content bytes
.uasset count / .umap count
AssetCatalog/BlueprintContext read-only export + index build under Output\W5Acceptance
Revision/query latency, index stats, reference/context behavior
no Plan/Apply/Save/Verify/Recovery on Reforge assets
no WriteFixturePlan, no asset modification, no plugin-write acceptance
```

No W5 Result document may claim Reforge production assets were write-tested.

## 9. W5-R1 / R5 / R20 Workload Matrix

### R1 — one logical operation

```text
1 logical op
1 legal W4 batch
1 safe target
```

Measure at least:

```text
WarmLoaded
WarmUnloaded
```

### R5 — five logical operations

Prefer one legal W4 batch if the selected assets/targets satisfy all frozen bounds.

```text
5 logical ops
<= 4 assets
<= 8 ops per asset
<= 16 total
```

Measure WarmLoaded and WarmUnloaded when reproducible.

### R20 — twenty logical operations

Must be split into legal W4 units.

Example only:

```text
batch A  <= 16 ops
batch B  remaining ops
```

The actual split must respect per-asset and request-size limits and should be chosen deterministically from the target manifest.

Report both:

```text
end-to-end user-scenario total
per-batch stage timings
batch-boundary overhead
```

Do not claim one Change Set / checkpoint set if the actual implementation uses more than one.

## 10. W5-R Cold Commandlet Comparison

Measure resident Apply versus the equivalent existing cold Commandlet mutation path on the **same logical target/value transition**.

Use paired runs with exact baseline reset between them.

Report two ratios separately:

```text
mutationPathSpeedup
  cold mutation path
  ---------------------------------
  resident Apply + Fast Verify path

persistedWorkflowRatio
  cold persisted workflow total
  ---------------------------------
  W4 persisted workflow total
```

Do not compare a resident path excluding Strong Verify against a cold path including Strong Verify and label the result "overall speedup".

At minimum record:

```text
process launches
Editor Bridge calls
asset load state
mutation time
Save time
Strong Verify process count
total time
```

Target output is factual. W5 has no required speedup number that must be forced to pass.

## 11. W5-R Failure Evidence

The Midterm contract explicitly requires real failure data because it is high-value future Memory material.

Capture at least one deterministic fail-closed case using only safe acceptance targets.

Preferred existing safety failures include:

```text
stale Revision
Policy rejection
Dirty-package conflict
exact transaction/session mismatch
```

Do not induce filesystem corruption or modify unrelated user transactions simply to manufacture a failure.

For each failure record:

```text
stage
errorCode
asset/change-set/batch identities
whether any mutation occurred
Dirty state
before/after Revision
recovery/reset result
why this case is valuable to later M3 knownIssue/projectRule distillation
```

A correctly rejected operation is valid W5 failure evidence; it must never be counted as a successful write task.

## 12. W5-S — Large-project + Storage Scale Baseline

### 12.1 Current prerequisite status

At W5 planning time:

```text
E:\WorkSpace\UEAgentKitPerfProject    absent
```

The historical performance worktree is:

```text
E:\WorkSpace\UEAgentKit-Performance
feature/performance-benchmarks
```

It must be treated as read-only by the W5 Agent unless the user explicitly authorizes cross-branch integration.

### 12.2 Scale fixture requirement (owner-revised staged plan)

The ultimate target from `E:\WorkSpace\UEAgentKit-LiveWriter\docs\PERFORMANCE_TEST_PLAN.md` remains:

```text
physical test project target   160–180 GB
hard project cap               200 GB
minimum remaining disk          50 GB
NativeSSD profile
SimulatedHDD50 profile
```

However the owner has authorized the following staged W5-S path for this execution:

```text
Step 1   Build a dedicated PerformanceFixture generator + 50 GB intermediate
         E:\WorkSpace\UEAgentKitPerfProject.
Step 2   Validate generator, disk protection, checkpoint/resume, benchmark pipeline,
         and W5-S measurements at 50 GB.
Step 3   Stop and report. Do NOT expand to 100 GB / 160–180 GB in this stage.
         Ask the owner before expanding further.
```

If the 50 GB fixture cannot be produced, record W5-S = blocked with the exact blocker and do not fabricate scale data.

Do not:

```text
create fake .uasset files
relabel Reforge as a 160–180 GB project
claim a 160–180 GB result from a 50 GB fixture
```

### 12.3 W5-S measurements once a 50 GB fixture exists

On the validated 50 GB intermediate project, measure at minimum:

```text
checkpoint Save latency
independent Strong Verify latency
asset load/cache-state contribution
child Unreal startup contribution
result bytes / Bridge call count
project size / asset counts / index build and query behavior
```

Use ordinary bounded W4 test assets inside that fixture; do not scan or rewrite the entire 50 GB just to verify one checkpoint.

### 12.4 HDD50 semantics and 160–180 GB status

`SimulatedHDD50` means simulated compatibility behavior, not a physical HDD benchmark.

Report separately:

```text
NativeSSD actual
SimulatedHDD50 modeled/injected
```

Never publish SSD timings under an HDD label.

Required HDD50 parameters follow the existing performance plan:

```text
sequential read/write cap    50 MB/s
new-file open latency        10 ms default
queue depth                  1
```

If the current implementation has no deterministic I/O Governor, W5-S must report that as a prerequisite gap rather than inventing HDD results. The 160–180 GB scale and SimulatedHDD50 results are **not claimed** in this W5 execution unless a later owner-approved expansion and an I/O Governor are implemented. They are reported as `blocked/deferred` with exact prerequisites. The 50 GB results are labeled NativeSSD actual only.

## 13. Repeatability / Noise Gate

Every summary must include its exact environment and sample count.

For core scenarios, split measured runs into at least three ordered groups and compare group medians.

A run is considered reasonably repeatable when:

```text
no unexplained functional failure
same scenario/configuration used
no competing UE/UBT workload
median group spread <= 20%
```

If spread exceeds 20%, keep the data but label the benchmark `noisy`; investigate cache state, UE compile/load variance, antivirus/indexing, storage contention or another Agent workload before making optimization conclusions.

Do not discard outliers without recording them and the exclusion rule.

## 14. Product-defect Handling

W5 is measurement-first.

If a true W4/D1 regression appears:

```text
1. freeze the failing raw evidence
2. restore the exact target baseline
3. classify product defect vs fixture/environment issue
4. write a narrow blocker-closure plan
5. make the minimal fix only under that plan
6. rerun affected W4/D1 regression + W5 case
```

Any product-code fix during W5 invalidates previously captured affected measurements. Re-measure them on the final code checkpoint.

If C++ is changed, UE5.6 Direct Build becomes mandatory.

## 15. Required Deliverables

Primary Result:

```text
E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_RESULT_20260829.md
```

Recommended harness/evidence:

```text
E:\WorkSpace\UEAgentKit-LiveWriter\benchmarks\w5\...
E:\WorkSpace\UEAgentKit-LiveWriter\Output\W5Acceptance\...
```

The Result document must include:

```text
exact final Git checkpoint
exact DirectHost project/environment identity
exact Reforge read-only project/environment identity
DirectHost safe target manifest summary; Reforge read-only statement
3+ real DirectHost tasks and Trust results
R1/R5/R20 logical workload results
20-op batch segmentation
resident vs cold paired ratios
stage contribution tables
failure corpus summary
50 GB fixture generator/validation status
50 GB NativeSSD checkpoint Save + Strong Verify results
160–180 GB / SimulatedHDD50 status (blocked/deferred or actual)
final fixture/target reset evidence
full regression gates
M6 evidence decision input
```

## 16. Regression Gates

At W5 completion run:

```text
actual discovered Python suite
Ruff
compileall
python scripts\ValidateRelease.py --require-release-docs
git diff --check
```

Also:

```text
no new runtime dependency
published version remains 0.7.0
no Push / Rebase / Tag / Release
```

C++ Direct Build:

```text
required only if W5 changes C++
```

Real UE acceptance is mandatory because W5 is explicitly a real-project Writer stage.

## 17. W5 Exit Gate

W5 may be marked `complete` only if all applicable items are closed truthfully:

```text
[ ] entry baseline is a documented descendant of 10760c8
[ ] exact DirectHost + Reforge project/environment recorded
[ ] DirectHost safe write targets explicitly frozen; Reforge marked read-only
[ ] at least 3 real multi-operation tasks finish Trust=verified (DirectHost)
[ ] all targets return to independently verified baseline
[ ] R1 stage breakdown captured
[ ] R5 stage breakdown captured
[ ] R20 20-logical-op workload captured without violating W4 bounds
[ ] resident-vs-cold paired comparison captured
[ ] raw attempts + p50/p95/sample counts recorded
[ ] at least one deterministic fail-closed case recorded
[ ] failure case performs no unreported mutation
[ ] benchmark repeatability/noise status recorded
[ ] Reforge read-only scale/integration inventory recorded
[ ] 50 GB UEAgentKitPerfProject generator validated with disk protection/checkpoint/resume
[ ] checkpoint Save + Strong Verify measured on 50 GB fixture (NativeSSD)
[ ] 160–180 GB and SimulatedHDD50 reported as blocked/deferred unless separately implemented
[ ] full Python/release gates PASS
[ ] Result document written with no unverified PASS claims
```

If the 50 GB fixture or I/O Governor is unavailable, the correct outcome is:

```text
W5-R = complete
W5-S = blocked/deferred at 50 GB checkpoint
W5   = blocked/deferred
```

Do not weaken the exit gate just to advance the roadmap.

## 18. Downstream Decisions Produced by W5

W5 is an input to later Memory work, especially optional M6 symbolic compression.

At W5 closure explicitly answer:

```text
Are large Change Set / Impact / Semantic Diff JSONs actually a context-size bottleneck?
Does public-result size materially hurt the Agent workflow after W4 compression?
Which failure classes recur in real project work?
Which stages dominate latency: UE-native load/compile/save, UEAgentKit orchestration, or cold process startup?
```

M6 must remain `deferred by benchmark evidence` unless W5 shows a real context bottleneck.

## 19. Exact Takeover Procedure

A fresh W5 Agent should execute exactly:

```text
1. Work only in E:\WorkSpace\UEAgentKit-LiveWriter
2. Confirm branch feature/live-writer-expansion
3. Inspect actual git status and HEAD; expected entry is 10760c8 or a documented descendant
4. Read every absolute document path in section 2
5. Do not touch E:\WorkSpace\UEAgentKit-KnowledgeWeb or E:\WorkSpace\UEAgentKit-Performance
6. Run the actual Python baseline
7. Resolve the real Reforge .uproject path; do not guess it (read-only only)
8. Freeze DirectHost safe write targets before starting Unreal; mark Reforge read-only
9. Acquire the machine's exclusive UE lease
10. Implement measurement harness only as needed
11. Execute W5-R first on DirectHost; run Reforge read-only integration separately
12. Restore and independently verify every DirectHost write target
13. Build/validate the 50 GB UEAgentKitPerfProject generator and intermediate fixture; ask before expanding beyond 50 GB
14. Do not mark W5 complete without the 50 GB scale evidence or an explicit owner-approved deferral
15. Do not commit/push/rebase/tag/release unless explicitly authorized by the user
```

### 19.1 Copy-paste startup prompt for a fresh Agent

```text
You are taking over UEAgentKit task W5: Real-project Acceptance + Scale Baseline.

Work ONLY in:
E:\WorkSpace\UEAgentKit-LiveWriter

Expected branch:
feature/live-writer-expansion

Expected entry checkpoint:
10760c8 refactor: split agent_workflow into workflow domain modules

First inspect actual git status and HEAD. Do not reset/clean/stash/rebase another Agent's work and do not touch other UEAgentKit worktrees.

Read these exact files from this worktree in order:
1. E:\WorkSpace\UEAgentKit-LiveWriter\docs\Handoffs\UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
2. E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\README.md
3. E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
4. E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
5. E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md
6. E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_D1_AGENT_WORKFLOW_SPLIT_RESULT_20260829.md
7. E:\WorkSpace\UEAgentKit-LiveWriter\docs\PERFORMANCE_TEST_PLAN.md
8. E:\WorkSpace\UEAgentKit-LiveWriter\docs\Plans\UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_AND_SCALE_BASELINE_DETAILED_PLAN_20260829.md

Execute W5 according to that Detailed Plan. Preserve all W4 safety bounds: max 4 assets, max 8 operations per asset, max 16 operations per batch. The W5 20-operation workload means 20 logical operations across multiple legal bounded workflows, never one 20-op batch.

Reforge is strictly read-only. All controlled writes use the DirectHost project E:\WorkSpace\UEAgentKit-LiveWriter\Build\DirectHost\HostProject.uproject and its existing safe transaction fixtures. Do not create /Game/UEAgentKitW5 or any acceptance namespace inside Reforge. W5 owns the exclusive Unreal Editor/UBT lease while real UE tests run; Track V may run in parallel only because it does not use Unreal.

Current scale-fixture fact: E:\WorkSpace\UEAgentKitPerfProject does not exist. W5-S starts by building and validating a 50 GB intermediate fixture with checkpoint/resume and disk protection; do not generate 100 GB / 160–180 GB without another owner decision. Do not claim full W5 complete until the 50 GB scale evidence is available or the owner explicitly approves a deferral.

Do not start Memory/P4/X work, do not widen Writer capabilities, and do not optimize before measurements identify a bottleneck. Do not commit, push, rebase, tag, release, or change published version unless explicitly authorized by the user.
```

## 20. Handoff Facts to Preserve

```text
W4 = complete
D1 = complete at 10760c8
W5 = current Writer task
worktree = E:\WorkSpace\UEAgentKit-LiveWriter
branch = feature/live-writer-expansion
W4 hard bounds remain frozen
20 logical ops != one 20-op batch
Reforge is strictly read-only; all controlled writes use DirectHost fixtures
W5 real UE work owns the exclusive UE lease
Track V can continue in parallel because it uses no UE
E:\WorkSpace\UEAgentKitPerfProject is absent; W5-S builds/validates 50 GB intermediate first
large scale evidence must not be fabricated
160–180 GB / SimulatedHDD50 require later owner-approved expansion/governor
W5 output is benchmark evidence, not a mandate to optimize
M6 remains data-driven by W5
```

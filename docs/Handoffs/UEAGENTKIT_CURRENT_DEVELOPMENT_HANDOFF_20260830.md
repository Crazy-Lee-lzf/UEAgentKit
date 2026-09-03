# UEAgentKit Current Development Handoff — 2026-08-30

> **Status: canonical project-development takeover document.**
>
> A new Chat / Agent must read this file before planning or modifying UEAgentKit.
>
> This document supersedes the previous `UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md`, which is preserved under `docs/Handoffs/Archive/`.
>
> Latest published product remains **UEAgentKit 0.7.0 for Unreal Engine 5.6**. Everything described below after the 0.7.0 release boundary is development-line capability unless explicitly released later.

## 0. Executive Takeover Summary

As of 2026-08-30, the previously separate Writer and Knowledge Web lines have been integrated and validated together.

```text
Published product                     0.7.0 / UE5.6 (unchanged)

Track W / Writer                      COMPLETE
  W0 resident baseline               complete
  W1 narrow Blueprint resident write complete
  W2 Fast Resident Verify            complete
  W3 Checkpoint Strong Verify        complete
  W4 bounded multi-op/multi-asset    complete
  D1 workflow-domain split           complete
  W5 authorized execution scope      complete
  W5-S 50 GiB checkpoint             PASS
  R20                                deferred fixture-lifecycle debt

Track V / Knowledge Web              COMPLETE
  V1 read-only browser               complete
  V2 visualization                   complete

W + V integration                    G3 PASS
Current portable full suite          968 / 968 PASS
Track M / M1                         COMPLETE / REVIEWED / G2 PASS / U0
Track M / M2                         COMPLETE / REVIEWED / G2 PASS / U0
Track M / M3                         COMPLETE / REVIEWED / G2 PASS / U0
Track M / M4                         COMPLETE / REVIEWED / G2 PASS / U0
Track M / M5                         COMPLETE / REVIEWED / G2 PASS / U0
M5 implementation                    c0b01aac4201710466ae9c9a5ee39f8965704b36
Track M required usability stages    COMPLETE through M5
Track C / C1+C2                      READY FOR IMPLEMENTATION / U0
Published-version change             none
M1-M5 regression benchmark gates     PASS
```

The key project-management interpretation is:

- **Track W is no longer blocked by R20.** R20 was deterministically classified as a DirectHost fixture lifecycle defect that mutates test semantics while the resident Editor is alive. UEAgentKit's freshness gate correctly rejects that drift.
- **Track V is complete and integrated.** Its Web/SQLite surface remains strictly read-only.
- **W and V have already been combined and portable-regression tested.** Do not rerun the historical W4/W5/V2 heavy matrices merely because a new Chat starts.
- **Repository branch/worktree housekeeping is complete.** Do not reconstruct the old missing-ref/unborn worktree problem from historical sections or old Chat context.

### 0.1 Post-sync repository housekeeping update

Later on 2026-08-30, the owner explicitly authorized remote synchronization and branch/worktree housekeeping. This subsection records that housekeeping checkpoint. Current Track M development refs are superseded by Section 0.4 and Section 2.

Synchronized checkpoint immediately after housekeeping:

```text
main / origin/main / origin/feature/memory-context
  synchronized to the same current post-housekeeping HEAD
  (always run git rev-parse HEAD / git fetch before relying on a hash)

registered worktrees:
E:\WorkSpace\UEAgentKit-Integration   main @ current post-housekeeping HEAD
E:\WorkSpace\UEAgentKit               detached at the same checkpoint (repository backing worktree; retains only historical untracked CONOUT$)
```

Completed local branches/worktrees `feature/live-writer-expansion`, `feature/knowledge-web-view`, `integration/w-v-20260830`, and `docs/closeout-plan-amendment-20260823` were removed after their relevant history was preserved. The old remote `feature/live-editor-realtime-io` was also removed because its history is already contained in `main`.

The old Performance line contained four commits not reachable from `main`; they were preserved remotely as:

```text
origin/archive/performance-benchmarks-legacy-20260830 @ a7c5ae9
```

Do not use that archive branch as a new development base without reviewing/cherry-picking the still-useful pieces. It contains historical registry-only / long-path performance work plus DarkRuins baseline material.

The physical directories `E:\WorkSpace\UEAgentKit-Performance` and `E:\WorkSpace\UEAgentKit-RealtimeIO` may still exist because they contain ignored local `Output` / `Build` / `Backups` acceptance evidence, but they are **no longer registered Git worktrees**. Do not treat them as active repositories.

Two previously untracked planning documents were preserved under `docs/Plans/Archive/` before cleanup: the 0.8 Closeout Plan Amendment and Agentic Game R&D Reference Directions.

### 0.2 Test-suite tiering completion update

The previously deferred test-suite structure/tiering maintenance was selected and completed later on 2026-08-30. This update supersedes older wording in this handoff that still describes test-suite cleanup as only deferred/eligible.

Current portable development entry points are:

```text
python scripts/RunPythonTests.py fast
python scripts/RunPythonTests.py domain <name>
python scripts/RunPythonTests.py full
```

Measured closure evidence:

```text
baseline full before tiering       845 tests / 84.071 s / PASS
fast G0                            396 tests / 4.643 s / PASS
memory G1                          170 tests / 18.562 s / PASS
index G1                            29 tests / 4.629 s / PASS
final full after 8 new tests       853 tests / 84.088 s / PASS
```

Full regression coverage was not reduced. The measured-heavy Knowledge View, Editor Bridge, Agent Workflow, Task Context and related modules remain in `full` and their affected domains; they are only omitted from the broad `fast` gate. The stage also narrowed the development Ruff dependency to `ruff>=0.12,<0.13` after a clean install of Ruff 0.16.5 demonstrated rule-drift against the historical repository while Ruff 0.12.12 passed the repository gate.

Detailed evidence: `docs/Plans/UEAGENTKIT_TEST_SUITE_TIERING_RESULT_20260830.md`. No UE validation was required (U0). Future structural test cleanup is optional/evidence-driven rather than an immediate prerequisite for Track M.

### 0.3 Track M / M1 completion update

Track M is active and **M1 — Memory Efficiency Baseline and Budget Gate is complete**.

Final reviewed state:

```text
normal worktree          E:\WorkSpace\UEAgentKit-Integration
branch                   feature/memory-context
M1 base                  137c3a35e943f2c8e65f13dd8befe95aec3c6612
UE level                 U0
M1 implementation        COMPLETE
M1 review                COMPLETE
final Memory G1          183 / 183 PASS / 25.987 s
final full G2            866 / 866 PASS / 92.518 s
Ruff / compileall        PASS
ValidateRelease 0.7.0    PASS
M1 benchmark gate        PASS
push                     none
```

Frozen M1 automatic-recall contract:

```text
<= 5 recalled items
<= 2000 recalled content chars
<= 800 estimated final-envelope tokens
<= 300 ms real recall deadline
first Tool Memory incremental p95 < 200 ms
recall p95 < 300 ms
task-end append p95 < 100 ms
```

Final measured values are `20.177 / 18.631 / 16.178 ms` respectively. The post-Agent review also hardened exact final-envelope accounting, SQLite deadline handler cleanup/error semantics, true paired first-Tool measurement, and functional benchmark gates.

Detailed evidence:

```text
docs/Plans/Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md
benchmarks/memory/m1_memory_overhead_before_20260830.json
benchmarks/memory/m1_memory_overhead_after_20260830.json
```

At M1 closure, the next Track M stage was **M2 deterministic L0 automatic capture / Evidence Chain foundation**. Its now-historical Detailed Plan is `docs/Plans/Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_DETAILED_PLAN_20260830.md`. M2 later completed and is superseded by the 2026-08-31 update below.

### 0.4 Track M / M2 completion and M3 planning update — 2026-08-31

M2 is now complete, reviewed, locally committed, and archived. The active next stage is **M3 — Deterministic L0 → L1 Distillation**.

Final reviewed M2 state:

```text
branch                    feature/memory-context
M2 planning checkpoint    16fde234aea9004734515b13715720f14148cb2c5
M2 implementation         d38c23c70fdf710117e6bd31f738b20665c20cd9
UE level                  U0
Memory schema             v4
final Memory G1           202 / 202 PASS / 21.483 s
final reviewer G2         885 / 885 PASS / 86.141 s
Ruff / compileall         PASS
ValidateRelease 0.7.0     PASS
M1/M2 benchmark gate      PASS
push                      none
```

The post-Agent review hardened three M2 details before the final checkpoint:

```text
capture suppression       ContextVar-scoped instead of process-global integer state
artifact SHA-256          bounded streaming hash instead of whole-file read_bytes()
checkpoint failures       durable checkpoint failure artifact takes precedence over duplicate inline rejection
```

Final reviewed benchmark snapshot:

```text
first Tool Memory incremental p95     13.525 ms   < 200 ms
direct automatic recall p95           12.247 ms   < 300 ms
task-end append p95                    13.439 ms   < 100 ms
four-event L0 capture p95              14.972 ms   < 100 ms
exact-state duplicate replay rows           0      PASS
```

M2 Plan/Result are now historical evidence under `docs/Plans/Archive/`.

The active M3 contract is:

```text
docs/Plans/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_DETAILED_PLAN_20260831.md
```

M3 is U0 and keeps Memory schema v4. It adds explicit offline deterministic distillation only; no LLM, vector retrieval, prompt injection, P4, C++, UE, idle/startup background scheduler, or new required dependency. Historical Policy rejection events that lack an exact Policy digest must never be promoted to `projectRule`.

### 0.5 Track M / M3 completion and M4 planning update — 2026-09-02

M3 is complete, independently reviewed, locally committed, and archived. The active next stage is **M4 — Hybrid Recall (FTS5 + optional local Vector + RRF)**.

Final M3 state:

```text
branch                    feature/memory-context
M3 planning checkpoint    2f4c85b7a0b6e07d50f91ffc65e3c6ba608dbcbd
M3 implementation         30449274b7d8f417af89be07c36d1a317cdc0390
UE level                  U0
Memory schema             v4
final Memory G1           226 / 226 PASS / 27.360 s
final portable G2         909 / 909 PASS / 95.298 s
Ruff / compileall         PASS
ValidateRelease 0.7.0     PASS
M3 100-event distill      263.852 ms / <5000 ms PASS
push                      none
```

The interrupted first M3 Agent left broad sandbox-only `tempfile -> repo .tmp` edits. The takeover Agent classified each dirty file, reverted 20 unrelated sandbox-only changes, repaired the six actual M3 contract gaps, and closed M3 without a blanket reset. Owner review then removed the untracked `.tmp/` debug/test residue and obsolete 2026-08-31 benchmark drafts.

Final M1/M2 regression snapshot at M3 closure:

```text
first Tool Memory incremental p95     13.924 ms   < 200 ms
direct automatic recall p95           12.983 ms   < 300 ms
task-end append p95                    14.133 ms   < 100 ms
four-event L0 capture p95              15.026 ms   < 100 ms
exact-state duplicate replay rows           0      PASS
```

M3 Plan/Result are now historical evidence under `docs/Plans/Archive/`.

The active M4 contract is:

```text
docs/Plans/UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_DETAILED_PLAN_20260902.md
```

M4 is U0. It upgrades Memory schema v4 -> v5 additively, keeps required dependencies `[]`, and capability-probes the optional local vector stack before freezing it. The v5 persistent schema must be identical whether optional vector dependencies are installed or not. Automatic Task Context remains FTS-only in M4; hybrid retrieval is limited to explicit Memory Search so M1 first-Tool/Recall latency cannot become model-load dependent. No LLM, remote embedding API, implicit model download, P4, C++, or UE work belongs in M4.

### 0.6 Track M / M4 completion and M5 planning update — 2026-09-03

M4 is complete, locally committed, and archived. The active next stage is **M5 — L2/L3 Stable Context Injection**.

```text
branch                    feature/memory-context
M4 planning checkpoint    bc403c0efd7f5c8ff258ef9481b183cc09e977d8
M4 implementation         212f5443bec2e0a4e496bfdf3e1f981f92cfc77a
P4 boundary decision      1952cf1830d7d2323aa2711c60b550cc7d067fab
UE level                  U0
Memory schema             v5
final Memory G1           266 / 266 PASS
final portable G2         949 / 949 PASS / 96.774 s
Ruff / compileall         PASS
ValidateRelease 0.7.0     PASS
push                      none
```

M4 quality/performance evidence:

```text
semantic Recall@5         0.00 -> 0.90
aggregate Recall@5        0.50 -> 0.95
aggregate MRR             0.50 -> 0.8292
hybrid p95                1.388 ms
query embeddings/query    exactly 1
corpus embeddings/query   exactly 0
```

M4 Plan/Result are now historical evidence under `docs/Plans/Archive/`.

The active M5 contract is `docs/Plans/UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_DETAILED_PLAN_20260903.md`.

M5 upgrades Memory schema v5 -> v6 additively and precomputes deterministic L2/L3 injection snapshots. Automatic Task Context reads only persisted L3 plus deterministically matched L2; it must not automatically inject L1/L0 bodies, load the vector model, run distillation, or rebuild a stale snapshot. Explicit Memory context/search tools remain available on demand.

After M5, M6 remains optional/data-driven. The next recommended dogfood step is the minimal P4 path defined by `docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`: C1 awareness, C2 advisory/checkout/local-write assistance, then real-project write-enabled dogfood. P4 collaboration state is advisory; Agent submit/revert/P4-managed delete are permanently human-only, while bounded resolve is allowed.

### 0.7 Track M / M5 completion and C1/C2 P4 planning update — 2026-09-03

M5 is complete, owner-reviewed, locally committed, and archived. The required Track M usability path is now complete through M5; M6 remains optional/data-driven and must not auto-start.

```text
branch                    feature/memory-context
M5 planning checkpoint    fac7b6cca2a4f628062c8ff72beadd9ba6513b10
M5 implementation         c0b01aac4201710466ae9c9a5ee39f8965704b36
UE level                  U0
Memory schema             v6
final Memory G1           285 / 285 PASS / 31.365 s (17 skipped)
final portable G2         968 / 968 PASS / 99.344 s (17 skipped)
M5 injection p95          5.748 ms / <100 ms PASS
first Tool Memory p95     19.354 ms / <200 ms PASS
direct recall p95         15.493 ms / <300 ms PASS
M3 100-event distill      265.432 ms / <5000 ms PASS
M4 semantic Recall@5      0.90 PASS
push                      none
```

M5 automatic Task Context now injects only persisted deterministic L3 plus matched L2; L1/L0 bodies remain explicit/on-demand. Stale/missing snapshots inject empty content and never rebuild synchronously. No vector/model load occurs on the automatic path.

The active next stage is **C1/C2 — P4 Minimum Dogfood**:

```text
docs/Plans/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md
```

The owner-approved P4 authority remains:

```text
docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md
```

Current machine read-only probe before C planning established P4 CLI/P4D 2025.1 and a reachable configured local test server/client. The UEAgentKit Git worktree itself is not depot-backed, so real C2 `p4 edit` acceptance requires an owner-designated safe mapped fixture. Test code must never use Agent-side `p4 revert` for cleanup.

Frozen P4 boundary:

```text
P4 state is advisory; it does not independently hard-block local Writer testing.
Agent may inspect, checkout/edit, use explicit local writable override, and perform proven-safe exact-file sync.
C3 may later add bounded Resolve and changelist preparation.
Agent submit / revert / P4-managed delete are permanently human-only.
```

### 0.8 Main integration and remote-push preparation update — 2026-09-03

The owner authorized merging the completed M1-M5 development line plus C1/C2 planning into `main`, updating current status documentation, and pushing `main` to the remote.

Local integration completed by fast-forward:

```text
merge source              feature/memory-context
source checkpoint         ae307372961345cbe98c594e9cfd469da70e68a1
integration branch        main
merge method              fast-forward
M5 implementation         c0b01aac4201710466ae9c9a5ee39f8965704b36
C1/C2 planning checkpoint 1c7e2ff39b28a9ff6d7a1bbf4d1151dfcc923d42
published product         0.7.0 / UE5.6 unchanged
```

`README.md`, `README_EN.md`, `docs/PROJECT_STATUS.md`, and `docs/ROADMAP.md` were synchronized before integration so development-line M1-M5/C1-C2 status is not confused with the published 0.7.0 release.

The active next implementation is Track C / C1+C2. M6 remains optional and must not auto-start. Future implementation should begin from current `main` (normally on a fresh feature branch) and treat the C1/C2 planning checkpoint as an ancestor, not require HEAD to equal it exactly.

Remote synchronization is separately verified at push time. If `origin/main` has moved, reconcile before push; do not force-push.

## 1. Mandatory Read Order for a New Chat / Agent

Read in this order:

```text
1. docs/Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md
2. docs/DEVELOPMENT_WORKFLOW.md
3. docs/Plans/README.md
4. docs/Plans/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md
5. docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md
6. docs/Plans/Archive/UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_RESULT_20260903.md
7. docs/Plans/Archive/UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_RESULT_20260902.md
8. docs/Plans/Archive/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_RESULT_20260902.md
9. docs/Plans/Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md
10. docs/Plans/Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md
11. docs/Plans/UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
12. docs/Plans/UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
```

Historical completed Plans/Results are now under:

```text
docs/Plans/Archive/
```

Historical handoffs are now under:

```text
docs/Handoffs/Archive/
```

Do not reconstruct current project state from old Chat history when this handoff and current repository facts are available.

## 2. Repository, Branch, and Worktree State

### 2.1 Current development refs

Repository housekeeping and remote synchronization were completed before M1 planning. M1-M5 and C1/C2 planning have now been integrated locally into `main` by fast-forward.

Current local refs before the authorized remote push:

```text
main                           includes ae307372961345cbe98c594e9cfd469da70e68a1 and this post-merge docs sync
origin/main                    137c3a35e943f2c8e65f13dd8befe95aec3c6612  (last-known; refresh before push)
feature/memory-context         ae307372961345cbe98c594e9cfd469da70e68a1  (merge source retained)
origin/feature/memory-context  137c3a35e943f2c8e65f13dd8befe95aec3c6612
```

The active integration worktree is now on `main`. M5 is reviewed at `c0b01aa`; C1/C2 planning is committed at `1c7e2ff`; public/current status docs are synchronized. The next coding work should normally branch from current `main`. Always inspect live refs and fetch before modifying or pushing; never force-push to hide remote drift.

### 2.2 Registered worktrees

Current registered Git worktrees are intentionally minimal:

```text
E:\WorkSpace\UEAgentKit-Integration
  active development worktree
  branch: main

E:\WorkSpace\UEAgentKit
  repository backing worktree
  detached at the synchronized checkpoint
  retains historical untracked CONOUT$
```

The old Writer, Knowledge Web, PlanReview, Performance, and RealtimeIO Git worktrees are no longer registered.

Physical directories `E:\WorkSpace\UEAgentKit-Performance` and `E:\WorkSpace\UEAgentKit-RealtimeIO` may still exist because ignored `Output` / `Build` / `Backups` evidence was deliberately preserved. They are **not active repositories/worktrees** and must not be reused for development without an explicit cleanup decision.

### 2.3 Current remote refs

The intentionally retained remote branches are:

```text
origin/main
origin/feature/memory-context
origin/archive/performance-benchmarks-legacy-20260830
```

The Performance archive preserves four historical commits not reachable from `main`; it is evidence/archive history, not an active development base.

## 3. Important Machine / Project Paths

```text
UEAgentKit development      E:\WorkSpace\UEAgentKit-Integration
Repository backing tree     E:\WorkSpace\UEAgentKit
UE5.6 engine                E:\EPICGAME\UE_5.6
Performance project         E:\WorkSpace\UEAgentKitPerfProject
Reforge                     E:\WorkSpace\Reforge\Reforge.uproject
DarkRuins seed              F:\UELecture\DarkRuinsMegascansSample
Preserved evidence dirs     E:\WorkSpace\UEAgentKit-Performance
                            E:\WorkSpace\UEAgentKit-RealtimeIO
```

Reforge is a real user project and was **strictly read-only** during W5. Do not turn it into a write fixture.

## 4. Product Positioning and Frozen Philosophy

UEAgentKit is not an unrestricted Unreal remote-control shell. The current product/development architecture is:

```text
project knowledge / deterministic index
→ bounded context and impact analysis
→ Policy + Revision gated planning
→ narrow resident Editor mutation
→ Fast resident evidence
→ explicit Save authorization
→ independent Strong Verify
→ Semantic Diff / Verification Plan / Trust
→ exact recovery / restart durability
```

Core rules:

```text
understand first
→ mutate only an explicit narrow target
→ preserve exact evidence
→ independently verify persistence
→ fail closed on stale/dirty/ambiguous state
→ recover exactly or report that recovery cannot be proven
```

Never increase apparent Agent convenience by weakening safety invariants.

## 5. Track W — Writer Status

Track W is project-level **complete** for its currently authorized scope.

### 5.1 W0 — Editor-resident baseline

Established the latency/process baseline and separated:

```text
Fast Verify   = resident Editor evidence
Strong Verify = independent persistence evidence
```

This distinction remains fundamental.

### 5.2 W1 — narrow Blueprint resident write

Resident Blueprint operations proven:

```text
setVariableDefault
setComponentProperty
setPinDefault
```

Critical implementation lesson: Blueprint compile invalidates transient pointers/references. W1 introduced stable-identity re-resolution for CDO/property, SCS ComponentTemplate, and GraphPin targets.

Do not reintroduce cached transient pointer assumptions across compile/Undo/Discard.

### 5.3 W2 — Fast Resident Verify

Public workflow capability:

```text
ue_verify_live_write_fast(asset_path, live_apply_receipt, change_set_id)
```

It binds exact resident session / receipt / transaction / Change Set evidence.

It is **not** disk persistence proof and never substitutes for Strong Verify.

### 5.4 W3 — Checkpoint Strong Verify

Core behavior:

```text
ue_save_authorized_asset(... verification_mode=immediate|checkpoint)
ue_verify_live_write_checkpoint(checkpoint_id)
```

Checkpoint mode keeps Save resident and records exact after-save disk SHA/revision evidence; independent Strong Verify occurs later.

Important W3 guarantees carried into W4:

- exact previous transaction continuation on the same asset;
- one checkpoint corresponds to one asset/package;
- unrelated Dirty state fails closed;
- Blueprint snapshot refresh uses the correct Blueprint-aware canonical export;
- same-target superseded operations remain audit-visible;
- stale Revision/canonical mismatch fails closed.

### 5.5 W4 — bounded multi-operation / multi-asset orchestration

W4 is complete.

Frozen initial Writer batch allowlist:

```text
setAssetProperty
setVariableDefault
setComponentProperty
setPinDefault
```

Do not silently broaden this to every live-capable registry operation.

Frozen hard limits:

```text
max assets per batch        4
max operations per asset    8
max total operations       16
max serialized request     64 KiB
```

Effective limits remain the minimum of hard bounds, Policy limits, and underlying capacity limits.

Frozen semantics:

```text
asset-grouped request
assets execute in request order
ops execute in request order inside asset
same asset appears once in request
repeated same-target writes remain audit-visible
no cross-package atomicity claim
no implicit/automatic Save
Fast Verify != Strong Verify
partial-applied / partial-saved states are explicit
recovery uses strict reverse successful-execution order
```

UE5.6 recovery finding:

`UPackageTools::UnloadPackages` clears the global Editor Transaction Buffer. Therefore persisted-partial recovery ordering is safety-significant.

Frozen safe pattern:

```text
resident-only Undo in strict global reverse order
→ close/unload persisted asset only when exact safety preconditions are proven
→ disk rollback
→ independent verify
```

### 5.6 D1 — workflow-domain split

D1 is complete and integrated.

`agent_workflow.py` was reduced to a thin facade; behavior moved into:

```text
workflow_common.py
workflow_plan.py
workflow_live.py
workflow_verify.py
workflow_batch.py
```

D1 was a refactor, not a semantic rewrite. Public API, Tool Registry, serialized evidence/state-machine behavior were frozen.

### 5.7 W5 — real-project acceptance and scale baseline

W5's currently authorized execution scope is considered complete for project scheduling.

Completed evidence:

```text
R1  1 logical operation         PASS
R5  5 logical operations        PASS
50 GiB scale checkpoint         PASS
fail-closed policy case         PASS
Reforge read-only inventory     PASS
```

R5 result, 10 measured samples per cache state:

```text
WarmLoaded   p50 total 14432.977 ms   p95 14641.421 ms
WarmUnloaded p50 total 14906.011 ms   p95 15653.234 ms
Strong Verify contribution            ~73-74%
```

50 GiB fixture:

```text
project size       53,715,786,581 bytes (~50.03 GiB)
.uasset             13,980
.umap                   79
3 measured samples  all Trust=verified

totalMs p50         13,154.177
min                 12,832.642
max                 16,610.624
mean                14,199.148
stddev               2,094.579
StrongVerify p50    11,401.885
p95                  unavailable (n=3)
```

The 50 GiB PerformanceFixture uses deterministic legal UE package duplication of real DarkRuins Texture/StaticMesh content, checkpoint/resume, free-space guard, 200 GiB hard cap, and GC between copies.

No 100 GiB / 160–180 GiB / SimulatedHDD50 expansion was authorized or claimed.

## 6. R20 — exact meaning and deferred classification

R20 was the W5 workload intended to measure 20 logical write operations using legal W4 batches:

```text
batch 1: BP 8 + DA 8 = 16 ops
batch 2: BP 2 + DA 2 =  4 ops
total                 = 20 logical ops
```

It did **not** obtain valid performance samples because the DirectHost write fixture mutated semantically while the resident Editor was alive.

Deterministic closure evidence:

```text
DataAsset IntValue    -17 -> 108
Blueprint package     29793 -> 29995 bytes
packageDirty          false at inspection
Disk                  changed asynchronously
```

Classification:

```text
A — semantic fixture mutation
```

This is not harmless package canonicalization.

UEAgentKit observed the disk/Revision drift and correctly failed closed before using stale evidence. No Revision, Policy, Dirty, recovery, or W4-bound rule was weakened.

Project-level decision:

> R20 is now a **DirectHost fixture-lifecycle maintenance debt**, not a blocker for Track W or W+V integration.

Do not “make R20 green” by weakening freshness checks.

## 7. Track V — Read-only Knowledge Web

Track V is complete and integrated.

Permanent constraints:

```text
read-only
SQLite mode=ro
localhost only
no POST/PUT/PATCH/DELETE mutation path
zero required runtime dependencies
stdlib HTTP/sqlite + native JS/HTML
no npm/build pipeline
no UE required
```

### V1

Implemented the local read-only knowledge browser.

Historical product checkpoint:

```text
49b8fa6 feat: add V1 read-only knowledge browser
```

### V2

Implemented read-only visualization endpoints:

```text
/api/graph
/api/impact/<asset_path>
/api/coverage
/api/timeline
/api/stale
```

Frontend uses native Canvas for graph display.

Historical product checkpoint:

```text
f2e6875 feat: add V2 knowledge visualization dashboard
```

Performance snapshot:

```text
5000-node graph server query      ~55 ms
5000-node graph HTTP              ~67 ms
JSON                              ~1.4 MB
4913-consumer impact query        ~16 ms server
headless first render             ~6.5 ms
sampled frame                     ~1.8 ms
```

No visualization dependency was needed.

## 8. W + V Integration Result

The separate Writer and V lines diverged at D1 planning history and were combined on:

```text
integration/w-v-20260830
```

Main integration checkpoints:

```text
576d5ec integration:merge-writer-and-knowledge-web
83fc619 docs:close-w-v-integration
dae0b1f docs:record-unborn-worktree-notices
fdaf393 docs:archive-history-and-refresh-handoff
```

Combined G3 portable validation:

```text
Full Python            845 / 845 PASS
                       95.524 seconds
Ruff                   PASS
compileall             PASS
ValidateRelease 0.7.0  PASS
                       --skip-tests --skip-ruff
Schemas                3 PASS
Patch examples         16 PASS
git diff --check       PASS
CLI/knowledge-view     smoke PASS
```

Important process observation:

The full 845-test suite completed in about 95.5 seconds during integration. Therefore the earlier V2 runs that took ~26 minutes for 835 tests were not evidence that the suite inherently requires 26 minutes. Environment/runner/temp-filesystem effects must be profiled before deleting tests.

No C++ delta existed between the validated Writer W5 checkpoint and the W+V merge, so the integration correctly did **not** repeat Direct Build, W4 C1-C12, W5 50 GiB, or V2 5000-node heavy acceptance.

## 9. Development Validation Standard — mandatory

The authoritative execution rules are in:

```text
docs/DEVELOPMENT_WORKFLOW.md
```

Every Detailed Plan must include a **Validation Budget**.

### G0 — development loop

Use focused/domain tests and touched static checks only.

Do not run the full suite after every edit.

### G1 — functional checkpoint

Run the affected domain group, relevant schema/contracts, compile/build/smoke when actually needed.

G1 is not full-repository regression by default.

### G2 — stage/Track closure

Run the portable full suite once, Ruff once, compileall, release-specific checks, diff-check, plus stage-specific acceptance.

If Full Python and Ruff were run explicitly, `ValidateRelease.py` must use:

```text
--skip-tests --skip-ruff
```

Do not deliberately execute the same full suite twice in one closure pass.

### G3 — integration/release boundary

Validate the combined state and only the affected cross-Track/UE surface.

“Integration” does not mean “rerun every historical UE matrix.”

## 10. UE validation levels and machine-wide lease

Plans declare one of:

```text
U0 no UE
U1 narrow smoke
U2 capability acceptance
U3 integration/release UE acceptance
```

Only one owner at a time may hold the machine-wide UE lease covering:

```text
UnrealEditor
UnrealEditor-Cmd
UBT / Direct Build
fixture Reset / WriteFixturePlan
snapshot refresh requiring UE
real UE acceptance
large UE fixture generation
```

When the user owns the UE Editor, Agents should continue only offline work: Python, SQLite, Web, docs, static analysis, planning/review.

Do not hold the UE lease throughout a long implementation task; implement offline first and use a bounded acceptance window.

## 11. Test-suite tiering — first maintenance pass complete

A static audit before W+V integration found:

```text
tests/python files             52
V2-era discovered tests       835
exact duplicate test bodies     0
structurally similar          20 groups / 47 tests
```

The owner selected this maintenance item after W+V integration. The first pass is now complete and intentionally optimized **execution tiering rather than raw test count**:

```text
baseline full before tiering       845 tests / 84.071 s / PASS
fast G0                            396 tests / 4.643 s / PASS
memory G1                          170 tests / 18.562 s / PASS
index G1                            29 tests / 4.629 s / PASS
final full after 8 new tests       853 tests / 84.088 s / PASS
```

Canonical entry points:

```text
python scripts/RunPythonTests.py fast
python scripts/RunPythonTests.py domain <name>
python scripts/RunPythonTests.py full
```

Safety coverage for Policy / Revision / recovery / persistence / Trust / tamper detection was not reduced. The measured-heavy modules remain in `full` and in the relevant domain gates.

Future table-driving, smoke-contract review, fixture-lifecycle optimization, or module splitting is **optional second-pass maintenance**. Do not auto-start another broad cleanup unless timing evidence shows a real development bottleneck.

## 12. Documentation organization after 2026-08-30 cleanup

The old `docs/Plans` and `docs/Handoffs` roots had accumulated completed phase documents.

New rule:

```text
docs/Plans/
  README.md
  UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md
  UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md
  UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
  UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
  UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md
  Archive/

docs/Handoffs/
  README.md
  UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md
  Archive/
```

Historical evidence is preserved, not deleted.

Future normal feature work should default to:

```text
ONE Detailed Plan
ONE Result
```

Only create a separate blocker-closure document when a real technical blocker needs a distinct investigation/exit gate.

Do not recreate W4's micro-document density for ordinary medium/low-risk work.

## 13. Important frozen Writer safety invariants

A new Agent must not weaken any of these without a separate explicit architecture decision:

```text
Policy validation
Revision freshness
fixed index / canonical identity checks
Dirty-package fail-closed behavior
exact Editor session binding
exact previousTransactionId continuation
one-time/exact confirmations where required
Fast Verify is resident-only
Strong Verify is independent persistence evidence
truthful partial_applied / partial_saved states
strict recovery ordering
independent rollback verification
no arbitrary shell/Unreal command injection
no generic UObject method execution
no automatic Save outside explicit authorization
no cross-package atomicity claim
W4 hard bounds remain enforced
```

## 14. Important code ownership areas

### Writer orchestration

```text
src/ue_agent_kit/agent_workflow.py      facade
src/ue_agent_kit/workflow_common.py
src/ue_agent_kit/workflow_plan.py
src/ue_agent_kit/workflow_live.py
src/ue_agent_kit/workflow_verify.py
src/ue_agent_kit/workflow_batch.py
src/ue_agent_kit/bounded_batch.py
src/ue_agent_kit/checkpoint_sets.py
src/ue_agent_kit/batch_recovery.py
```

### Safety / semantics

```text
src/ue_agent_kit/patches.py
src/ue_agent_kit/change_sets.py
src/ue_agent_kit/freshness.py
src/ue_agent_kit/verification_evidence.py
src/ue_agent_kit/verification_trust.py
src/ue_agent_kit/semantic_diff.py
src/ue_agent_kit/semantic_diff_workflow.py
```

### Knowledge Web

```text
src/ue_agent_kit/knowledge_view.py
src/ue_agent_kit/web/index.html
src/ue_agent_kit/cli.py
```

### Memory / M1-M5 completed surface

```text
src/ue_agent_kit/memory_schema.py
src/ue_agent_kit/project_memory.py
src/ue_agent_kit/memory_context.py
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/memory_distill.py
src/ue_agent_kit/cli.py
src/ue_agent_kit/mcp_memory_tools.py
pyproject.toml
```

M4 added `memory_vector.py` and explicit hybrid retrieval. M5 added `memory_injection.py`, schema v6, offline deterministic L2/L3 snapshots, and model-free automatic Task Context injection. Required dependencies remain empty; M1-M5 are now closed and their regression gates remain protected.


### Source Control / active C1+C2 surface

Expected new surface:

```text
src/ue_agent_kit/source_control.py
src/ue_agent_kit/mcp_source_control_tools.py
src/ue_agent_kit/cli.py
tests/python/test_source_control.py
benchmarks/source_control/
```

There is no existing product P4 provider implementation to preserve. The new layer must remain narrow: structured P4 operations only, no shell passthrough, no P4Python required dependency, and no submit/revert/delete capability.

### W5 / scale

```text
benchmarks/w5/
scripts/RunW5Acceptance.ps1
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/PerformanceFixtureCommandlet.cpp
```

## 15. Active Track and deferred project tracks

**Track M required usability stages are complete through M5. Track C1/C2 is active next.**

The Master/Midterm documents still define broader direction, but older Track C fail-closed/no-checkout wording is superseded by the 2026-09-03 P4 boundary decision.

### Track M — automatic Memory accumulation

```text
M1 performance/budget foundation       COMPLETE / U0 / G2 PASS
M2 deterministic L0 capture            COMPLETE / REVIEWED / U0 / G2 PASS
M3 L0 -> L1 deterministic distillation COMPLETE / REVIEWED / U0 / G2 PASS
M4 FTS5 + optional Vector + RRF         COMPLETE / REVIEWED / U0 / G2 PASS
M5 L2/L3 stable context injection       COMPLETE / REVIEWED / U0 / G2 PASS
M6 optional symbolic compression        deferred / data-driven / do not auto-start
```

M1-M5 regression gates remain persistent. M6 is not a prerequisite for dogfood.

### Track C — Source Control / P4 awareness (ACTIVE NEXT)

```text
C1 Source Control Awareness             READY FOR IMPLEMENTATION
C2 Advisory + checkout/local-write      READY FOR IMPLEMENTATION
C3 Changelist Preparation + Resolve     deferred until minimum dogfood layer
C4 optional Memory integration          deferred
```

P4 collaboration state is advisory; checkout/edit and explicit local writable override are allowed. Proven-safe exact-file sync may be assisted. Submit/revert/P4-managed delete are permanently human-only. Resolve is allowed by owner policy but belongs to C3, not the minimum C1/C2 slice.

### Track X — deeper UE capabilities

Deferred while C1/C2 is active and until real-project dogfood identifies the next useful capability.

### Track D — maintenance / engineering quality

Deferred unless a concrete maintenance blocker prevents C1/C2. Test-tiering and Git housekeeping prerequisites are already complete.

## 16. Performance / fixture boundary

Do not mix correctness-fixture and performance-fixture goals.

Correctness fixture:

```text
official Reset
fixed semantic baseline
exact Revision/SHA proof
strict Dirty checks
independent recovery verification
```

Performance fixture:

```text
controlled initialization
stable session when appropriate
product recovery between samples
exact lightweight pre-sample baseline proof
avoid unnecessary cold-reset rebuilds
```

Performance convenience may never weaken product correctness gates.

## 17. Exact takeover procedure for the next Track C1/C2 execution Agent

Before making any change:

```text
1. use E:\WorkSpace\UEAgentKit-Integration.
2. inspect git status --short --branch, HEAD, upstream and worktrees.
3. start from current main (normally create a fresh Track C feature branch); confirm C1/C2 planning checkpoint 1c7e2ff is an ancestor.
4. read this handoff.
5. read docs/DEVELOPMENT_WORKFLOW.md.
6. read docs/Plans/README.md.
7. read docs/Plans/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md.
8. read docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md.
9. confirm M5 checkpoint c0b01aa is an ancestor; do not reopen M6.
10. begin with C1-0 actual P4 capability probe; prefer structured -G output if proven.
11. do not implement arbitrary P4 command passthrough or add P4Python as a required dependency.
12. P4 collaboration state is advisory only; never turn it into a Writer hard-block.
13. submit/revert/delete must remain absent from Agent capabilities, including private runner allowlists.
14. use fake P4 fixtures for mutation matrices; real p4 edit requires an owner-designated safe mapped fixture.
15. real mutation acceptance must stop before cleanup; the human performs any final revert.
16. C1/C2 required closure is U0: do not start UE/UBT.
17. use focused tests during edits, one affected-domain G1, one final G2.
18. do not push/rebase/tag/release/version-change unless separately authorized.
```

The current configured local P4 server/client is suitable for read-only capability smoke, but the UEAgentKit Git worktree itself is not depot-backed.

## 18. Prohibited implicit actions

Unless the owner explicitly authorizes them, do not:

```text
push
rebase shared history
tag
create a release
change published version
publish artifacts
weaken Writer safety gates
mutate Reforge for acceptance
run overlapping UE/UBT workloads against another owner
delete preserved local Output/Build/Backups evidence without review
start 100/160-180 GiB/HDD50 scale expansion
```

Local commits are allowed only when the current task explicitly authorizes them. The 2026-08-30 repository sync/archive housekeeping and the selected test-suite tiering maintenance were both explicitly authorized tasks.

## 19. Historical evidence lookup

Completed detailed Plans and Results are intentionally not in the default root navigation anymore.

Use:

```text
docs/Plans/Archive/
docs/Handoffs/Archive/
```

Important historical filenames include:

```text
UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md
UEAGENTKIT_D1_AGENT_WORKFLOW_SPLIT_RESULT_20260829.md
UEAGENTKIT_W5_REAL_PROJECT_ACCEPTANCE_RESULT_20260829.md
UEAGENTKIT_V1_READ_ONLY_KNOWLEDGE_BROWSER_RESULT_20260829.md
UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_RESULT_20260829.md
AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md
```

Archive documents preserve their historical stage wording. Current status is determined by this handoff, the current Plans README, and current Git facts.

## 20. Next local-Agent handoff

M1-M5 are closed and integrated into `main`; M6 is optional and must not auto-start. The next coding Agent should branch from current `main` and execute **C1/C2 — P4 Minimum Dogfood** from:

```text
docs/Plans/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md
```

Product baseline:

```text
c0b01aac4201710466ae9c9a5ee39f8965704b36
```

The Agent should report only:

```text
verified Git facts
actual P4 CLI/server capability probe facts
structured provider/parser design
warning/readiness behavior
checkout/local writable override/safe-sync evidence
proof that submit/revert/delete capabilities are absent
real P4 read-only smoke and owner-assisted mutation status
G0/G1/G2 counts and elapsed time
UE runs (expected 0)
```

Routine implementation/debugging should proceed autonomously inside the frozen Plan and P4 boundary decision. Stop for owner input only if a safe real-mutation fixture is required or an actual architecture/safety conflict appears.

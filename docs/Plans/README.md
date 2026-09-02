# UEAgentKit Plans — Current Navigation

> Updated: 2026-09-02
>
> Root keeps current project-level navigation plus the stage currently being executed. Completed phase Plans/Results are preserved under `Archive/`.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical repository/Track/worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 gates, U0-U3 UE validation, UE lease, Git/documentation rules |
| 3 | [`UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_DETAILED_PLAN_20260902.md`](UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_DETAILED_PLAN_20260902.md) | **Active M4 implementation contract and Validation Budget** |
| 4 | [`Archive/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_RESULT_20260902.md`](Archive/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_RESULT_20260902.md) | M3 reviewed completion evidence |
| 5 | [`Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md`](Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md) | M2 reviewed completion evidence |
| 6 | [`Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md`](Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md) | M1 reviewed completion evidence |
| 7 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | Project direction / Track architecture; progress wording may be historical |
| 8 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Cross-Track dependencies and historical acceptance contracts |
| 9 | [`UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md`](UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md) | Writer + Knowledge Web integration evidence |

## Current project state

```text
published product                     0.7.0 / UE5.6
Track W / Writer                      COMPLETE
Track V / Knowledge Web              COMPLETE
W + V integration                    G3 PASS
R20                                  deferred DirectHost fixture-lifecycle debt

test-suite tiering                   COMPLETE / U0 / G2 PASS
Track M                              ACTIVE
  M1 Memory efficiency/budget        COMPLETE / REVIEWED / U0
    checkpoint                       6d9cf711
  M2 deterministic L0 capture        COMPLETE / REVIEWED / U0
    implementation checkpoint        d38c23c7
  M3 deterministic L0 -> L1          COMPLETE / REVIEWED / U0 / G2 PASS
    planning checkpoint              2f4c85b7
    implementation checkpoint        30449274
    final Memory G1                  226 / 226 PASS / 27.360 s
    final portable full              909 / 909 PASS / 95.298 s
    M3 100-event distillation        263.852 ms / <5000 ms PASS
  M4 hybrid recall                   READY FOR IMPLEMENTATION / required U0
```

Persistent Track M regression gates:

```text
automatic recall                     <= 5 items / <= 2000 chars / <= 800 estimated tokens
real recall deadline                 <= 300 ms
first Tool Memory delta p95          13.924 ms  (<200 ms, final M3 closure run)
direct recall p95                    12.983 ms  (<300 ms)
task-end append p95                  14.133 ms  (<100 ms)
4-event L0 capture p95               15.026 ms  (<100 ms)
duplicate replay new rows            0
M3 100-event distillation            263.852 ms  (<5000 ms)
```

## Current branch

```text
worktree       E:\WorkSpace\UEAgentKit-Integration
branch         feature/memory-context
M4 baseline    30449274b7d8f417af89be07c36d1a317cdc0390
upstream       origin/feature/memory-context @ 137c3a35e943f2c8e65f13dd8befe95aec3c6612
push           none
```

Always inspect actual Git state before modifying anything. Repository facts beat this navigation if they later differ.

## Documentation rule

Normal stage footprint:

```text
ONE Detailed Plan
ONE Result
```

Create a blocker-closure document only for a genuine technical blocker with a different investigation/exit gate.

Completed/historical phase documents are preserved in [`Archive/`](Archive/). Precedence is:

```text
actual Git/repository facts
→ current handoff
→ DEVELOPMENT_WORKFLOW.md
→ current Plans README
→ active Detailed Plan / latest Result
→ historical documents
```

## Active next work

Execute **M4 — Hybrid Recall (FTS5 + optional local Vector + RRF)** from the active Detailed Plan.

Frozen M4 facts:

```text
baseline                       30449274b7d8f417af89be07c36d1a317cdc0390
required UE                    U0 / no UE
Memory schema                  v4 -> v5 additive
required dependencies          remain []
optional vector stack          capability-probed before freeze
LLM / remote embedding API     forbidden
P4 / C++                       excluded
automatic Task Context         remains FTS-only in M4
explicit Memory Search         hybrid only when local vector stack is valid
vector failure/missing stack   deterministic FTS fallback
persistent sqlite-vec schema   must not depend on optional-extra presence
M5/M6                          deferred
```

Important M4 zero-dependency rule:

```text
v4 -> v5 migration must succeed without vector extra
same v5 persistent SQLite structure with or without vector extra
no implicit model download/network access in MCP/query paths
```

The M4 implementation Agent must start with the capability probe and frozen 20-query relevance benchmark. Do not tune RRF against an unfrozen relevance set. Preserve all M1-M3 performance and provenance contracts and run full only once at final G2.

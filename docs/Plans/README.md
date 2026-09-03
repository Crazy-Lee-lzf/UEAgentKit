# UEAgentKit Plans — Current Navigation

> Updated: 2026-09-03
>
> Root keeps current project-level navigation plus the stage currently being executed. Completed phase Plans/Results are preserved under `Archive/`.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical repository/Track/worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 gates, U0-U3 UE validation, UE lease, Git/documentation rules |
| 3 | [`UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_DETAILED_PLAN_20260903.md`](UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_DETAILED_PLAN_20260903.md) | **Active M5 implementation contract and Validation Budget** |
| 4 | [`Archive/UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_RESULT_20260902.md`](Archive/UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_RESULT_20260902.md) | M4 reviewed completion evidence |
| 5 | [`Archive/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_RESULT_20260902.md`](Archive/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_RESULT_20260902.md) | M3 reviewed completion evidence |
| 6 | [`Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md`](Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md) | M2 reviewed completion evidence |
| 7 | [`Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md`](Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md) | M1 reviewed completion evidence |
| 8 | [`UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`](UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md) | Frozen post-M5 P4 Agent permission/advisory boundary |
| 9 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | Project direction / Track architecture; progress wording may be historical |
| 10 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Cross-Track dependencies and historical acceptance contracts |
| 11 | [`UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md`](UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md) | Writer + Knowledge Web integration evidence |

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
    implementation checkpoint        30449274
  M4 hybrid recall                   COMPLETE / REVIEWED / U0 / G2 PASS
    planning checkpoint              bc403c0e
    implementation checkpoint        212f5443
    Memory G1                        266 / 266 PASS
    portable full                    949 / 949 PASS / 96.774 s
    hybrid semantic Recall@5         0.90
    aggregate Recall@5               0.95
    aggregate MRR                    0.8292
    hybrid p95                       1.388 ms
  M5 L2/L3 stable context injection  READY FOR IMPLEMENTATION / required U0
  M6 symbolic compression            optional / data-driven / do not auto-start
```

Persistent Track M regression gates:

```text
automatic recall                     <= 5 items / <= 2000 chars / <= 800 estimated tokens
real recall deadline                 <= 300 ms
first Tool Memory delta p95          < 200 ms
direct recall p95                    < 300 ms
task-end append p95                  < 100 ms
4-event L0 capture p95               < 100 ms
duplicate replay new rows            0
M3 100-event distillation            < 5000 ms
M4 explicit hybrid p95               < 300 ms
M4 query embeddings/query            exactly 1 in hybrid mode
M4 corpus embeddings/query           exactly 0
```

## Current branch

```text
worktree       E:\WorkSpace\UEAgentKit-Integration
branch         feature/memory-context
M5 baseline    212f5443bec2e0a4e496bfdf3e1f981f92cfc77a
planning HEAD  1952cf1830d7d2323aa2711c60b550cc7d067fab
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

Execute **M5 — L2/L3 Stable Context Injection** from the active Detailed Plan.

Frozen M5 direction:

```text
baseline                       212f5443bec2e0a4e496bfdf3e1f981f92cfc77a
required UE                    U0 / no UE
Memory schema                  v5 -> v6 additive
required dependencies          remain []
LLM / remote inference         forbidden
automatic vector/model load    forbidden
L2/L3 build                    explicit/offline deterministic snapshot
Task Context automatic memory  L3 + deterministically matched L2 only
L1/L0 automatic body injection forbidden
explicit ue_memory_get_context remains backward-compatible
stale/missing snapshot         empty injection; never synchronous rebuild
M1 total automatic budget      remains <=800 estimated tokens
```

M5 is the final required Track M usability stage. After successful M5 closure, **do not auto-start M6**. Move to the minimal P4 dogfood path defined by `UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`: C1 Source Control Awareness, then C2 Advisory + checkout/local-write assistance, then real-project write-enabled dogfood.

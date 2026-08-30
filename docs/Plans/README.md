# UEAgentKit Plans — Current Navigation

> Updated: 2026-08-30
>
> Root keeps current project-level navigation plus the stage currently being executed. Completed phase Plans/Results are preserved under `Archive/`.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical repository/Track/worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 gates, U0-U3 UE validation, UE lease, Git/documentation rules |
| 3 | [`UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_DETAILED_PLAN_20260830.md`](UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_DETAILED_PLAN_20260830.md) | **Active M2 implementation contract and Validation Budget** |
| 4 | [`Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md`](Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md) | M1 reviewed completion evidence |
| 5 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | Project direction / Track architecture; progress wording may be historical |
| 6 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Cross-Track dependencies and acceptance contracts |
| 7 | [`UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md`](UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md) | Writer + Knowledge Web integration evidence |

## Current project state

```text
published product                     0.7.0 / UE5.6
Track W / Writer                      complete
Track V / Knowledge Web              complete
W + V integration                    G3 PASS
R20                                  deferred DirectHost fixture-lifecycle debt

test-suite tiering                   COMPLETE / U0 / G2 PASS
Track M                              ACTIVE
  M1 Memory efficiency/budget        COMPLETE / U0 / REVIEWED / G2 PASS
    checkpoint                       6d9cf711
    final portable full              866 / 866 PASS / 92.518 s
    final memory G1                  183 / 183 PASS / 25.987 s
  M2 deterministic L0 capture        READY FOR IMPLEMENTATION / required U0
```

M1 frozen gates remain mandatory in M2:

```text
automatic recall                    <= 5 items / <= 2000 chars / <= 800 estimated tokens
real recall deadline                <= 300 ms
first Tool Memory delta p95          20.177 ms  (<200 ms)
direct recall p95                    18.631 ms  (<300 ms)
task-end append p95                  16.178 ms  (<100 ms)
```

## Current branch

```text
worktree       E:\WorkSpace\UEAgentKit-Integration
branch         feature/memory-context
M2 baseline    6d9cf711f368f359fc8f2343e1a065942f8f58f5
```

Always inspect actual Git state before modifying anything.

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

Execute **M2 — Deterministic L0 Automatic Capture** from the active Detailed Plan.

M2 facts:

```text
P4 prerequisite            no
required UE                U0 / no UE
schema                     v3 -> v4 additive
LLM                        none
new required dependency    none
M3-M6                      deferred
```

The implementation Agent must not re-plan architecture already frozen in the M2 Plan. It should batch-read files and implement by M2 slice, use focused tests for edits, `domain memory` for G1, and run full only once at final G2.

An optional narrow U1 real-Writer spot-check may be performed later, but it is not part of the local-Agent M2 speed comparison and is not a required implementation gate unless repository facts reveal an offline-proof gap.

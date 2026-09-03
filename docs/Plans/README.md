# UEAgentKit Plans — Current Navigation

> Updated: 2026-09-03
>
> Root keeps current project-level navigation plus the stage currently being executed. Completed phase Plans/Results are preserved under `Archive/`.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical repository/Track/worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 gates, U0-U3 UE validation, UE lease, Git/documentation rules |
| 3 | [`UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md`](UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_DETAILED_PLAN_20260903.md) | **Active C1/C2 implementation contract and Validation Budget** |
| 4 | [`UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`](UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md) | Frozen P4 Agent permission/advisory authority |
| 5 | [`Archive/UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_RESULT_20260903.md`](Archive/UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_RESULT_20260903.md) | M5 reviewed completion evidence |
| 6 | [`Archive/UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_RESULT_20260902.md`](Archive/UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_RESULT_20260902.md) | M4 reviewed completion evidence |
| 7 | [`Archive/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_RESULT_20260902.md`](Archive/UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_RESULT_20260902.md) | M3 reviewed completion evidence |
| 8 | [`Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md`](Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md) | M2 reviewed completion evidence |
| 9 | [`Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md`](Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md) | M1 reviewed completion evidence |
| 10 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | Project direction; Track C permission wording is historical where it conflicts with the 2026-09-03 boundary decision |
| 11 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Historical cross-Track acceptance contracts |

## Current project state

```text
published product                     0.7.0 / UE5.6
Track W / Writer                      COMPLETE
Track V / Knowledge Web              COMPLETE
W + V integration                    G3 PASS
R20                                  deferred DirectHost fixture-lifecycle debt

test-suite tiering                   COMPLETE / U0 / G2 PASS
Track M required usability stages    COMPLETE through M5
  M1 Memory efficiency/budget        COMPLETE / REVIEWED / U0
  M2 deterministic L0 capture        COMPLETE / REVIEWED / U0
  M3 deterministic L0 -> L1          COMPLETE / REVIEWED / U0 / G2 PASS
  M4 hybrid recall                   COMPLETE / REVIEWED / U0 / G2 PASS
    implementation checkpoint        212f5443
    semantic Recall@5                0.90
    aggregate MRR                    0.8292
  M5 L2/L3 stable context injection  COMPLETE / REVIEWED / U0 / G2 PASS
    implementation checkpoint        c0b01aac
    Memory G1                        285 / 285 PASS / 31.365 s
    portable full                    968 / 968 PASS / 99.344 s
    injection p95                    5.748 ms
    first Tool Memory delta p95      19.354 ms
    automatic recall p95             15.493 ms
  M6 symbolic compression            optional / data-driven / do not auto-start

Track C / P4                         ACTIVE NEXT
  C1 Source Control Awareness        READY FOR IMPLEMENTATION
  C2 Advisory + local-write assist   READY FOR IMPLEMENTATION
  C3 CL preparation / Resolve        deferred until after minimum dogfood layer
  C4 Memory integration              optional
```

Persistent Memory regression gates remain required during C1/C2 where affected:

```text
automatic recall                     <= 800 estimated tokens
first Tool Memory delta p95          < 200 ms
direct recall p95                    < 300 ms
task-end append p95                  < 100 ms
4-event L0 capture p95               < 100 ms
duplicate replay new rows            0
M3 100-event distillation            < 5000 ms
M4 explicit hybrid p95               < 300 ms
M5 automatic injection p95           < 100 ms
```

## Current branch

```text
worktree               E:\WorkSpace\UEAgentKit-Integration
integration branch     main
C1/C2 product baseline c0b01aac4201710466ae9c9a5ee39f8965704b36
planning checkpoint    1c7e2ff39b28a9ff6d7a1bbf4d1151dfcc923d42
feature merge source   feature/memory-context @ ae307372961345cbe98c594e9cfd469da70e68a1
origin/main             137c3a35e943f2c8e65f13dd8befe95aec3c6612 (last-known before authorized push; refresh required)
push                    authorized / pending remote refresh
```

Always inspect actual Git state before modifying anything. Repository facts beat this navigation if they later differ.

## P4 owner decision — authoritative boundary

`UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md` supersedes older fail-closed/no-checkout Track C wording.

Frozen rule:

```text
P4 collaboration state
→ advisory / warning / strong warning / readiness
→ never independently hard-blocks local Writer testing

Agent MAY:
  inspect status
  checkout / p4 edit
  local writable override when explicit
  bounded safe sync when cleanliness is proven
  later, bounded resolve / CL preparation in C3

Agent MUST NEVER:
  submit
  revert
  delete P4-managed files
```

Submit/revert/delete remain human-only even if the user asks the Agent directly.

## Active next work

Execute **C1/C2 — P4 Minimum Dogfood** from the active Detailed Plan.

Frozen implementation direction:

```text
required UE                    U0 / no UE
required dependencies          remain []
P4Python                       not required
preferred P4 transport         p4 -G + stdlib marshal after capability probe
generic P4 command passthrough forbidden
universal Task Context P4 call forbidden in C1/C2
P4 hard-block of Writer        forbidden
checkout / p4 edit             allowed
local writable override        explicit + auditable
safe sync                      only exact clean files with proven preconditions
submit / revert / delete       permanently unavailable
C3 Resolve/CL organization     deferred
```

Current machine read-only probe established P4 CLI/P4D 2025.1 and a reachable configured local test server/client. The UEAgentKit Git worktree itself is not depot-backed, so real C2 mutation acceptance requires an owner-designated safe mapped fixture; automated tests must not use Agent-side revert for cleanup.

After C1/C2 closes, begin real-project write-enabled dogfood. Do not auto-start M6.

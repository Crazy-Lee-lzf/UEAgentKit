# UEAgentKit Plans — Current Navigation

> Updated: 2026-08-31
>
> Root keeps current project-level navigation plus the stage currently being executed. Completed phase Plans/Results are preserved under `Archive/`.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical repository/Track/worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 gates, U0-U3 UE validation, UE lease, Git/documentation rules |
| 3 | [`UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_DETAILED_PLAN_20260831.md`](UEAGENTKIT_M3_DETERMINISTIC_L0_TO_L1_DISTILLATION_DETAILED_PLAN_20260831.md) | **Active M3 implementation contract and Validation Budget** |
| 4 | [`Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md`](Archive/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md) | M2 reviewed completion evidence |
| 5 | [`Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md`](Archive/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md) | M1 reviewed completion evidence |
| 6 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | Project direction / Track architecture; progress wording may be historical |
| 7 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Cross-Track dependencies and historical acceptance contracts |
| 8 | [`UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md`](UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md) | Writer + Knowledge Web integration evidence |

## Current project state

```text
published product                     0.7.0 / UE5.6
Track W / Writer                      complete
Track V / Knowledge Web              complete
W + V integration                     G3 PASS
R20                                   deferred DirectHost fixture-lifecycle debt

test-suite tiering                    COMPLETE / U0 / G2 PASS
Track M                               ACTIVE
  M1 Memory efficiency/budget         COMPLETE / REVIEWED / U0
    checkpoint                        6d9cf711
  M2 deterministic L0 capture         COMPLETE / REVIEWED / U0
    planning checkpoint               16fde234
    implementation checkpoint         d38c23c7
    final review full                 885 / 885 PASS / 86.141 s
    final Memory G1                   202 / 202 PASS / 21.483 s
  M3 deterministic L0 -> L1          READY FOR IMPLEMENTATION / required U0
```

M1/M2 persistent regression gates remain mandatory in M3:

```text
automatic recall                      <= 5 items / <= 2000 chars / <= 800 estimated tokens
real recall deadline                  <= 300 ms
first Tool Memory delta p95           13.525 ms  (<200 ms, final M2 review run)
direct recall p95                     12.247 ms  (<300 ms)
task-end append p95                   13.439 ms  (<100 ms)
4-event L0 capture p95                14.972 ms  (<100 ms)
duplicate replay new rows             0
```

## Current branch

```text
worktree       E:\WorkSpace\UEAgentKit-Integration
branch         feature/memory-context
M3 baseline    d38c23c70fdf710117e6bd31f738b20665c20cd9
upstream       origin/feature/memory-context @ 137c3a35e943f2c8e65f13dd8befe95aec3c6612
local state    ahead 3 before the M3 planning checkpoint
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

Execute **M3 — Deterministic L0 → L1 Distillation** from the active Detailed Plan.

Frozen M3 facts:

```text
baseline                    d38c23c70fdf710117e6bd31f738b20665c20cd9
required UE                 U0 / no UE
Memory schema               remains v4
required trigger            explicit offline `ue-agent memory distill`
background scheduler        deferred
LLM/model calls             forbidden
P4 prerequisite             no
vector / M4 work            excluded
prompt injection / M5       excluded
new required dependency     none
100-event distillation gate < 5 s
```

Important provenance boundary:

```text
historical policy rejection without exact Policy digest
→ must NOT become projectRule

future policy rejection with exact fixed Policy digest
→ may become projectRule after source validation
```

The implementation Agent must execute the frozen M3 Plan rather than write a competing architecture plan. Start at M3-0, preserve every M1/M2 gate, use focused tests during edits, run `domain memory` once at G1 after the final functional source state, and run the full suite once at G2.

M3 is explicit/offline maintenance. Do not add idle/startup background threads or synchronous task-path distillation merely to reproduce historical Midterm wording.

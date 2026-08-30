# UEAgentKit Plans — Current Navigation

> Updated: 2026-08-30
>
> Root keeps current project-level navigation and the stage currently being prepared/executed. Completed phase material is preserved under `Archive/` during the next planning transition.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical repository/Track/worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 gates, U0-U3 UE validation, UE lease, Git/documentation rules |
| 3 | [`UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md`](UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md) | Latest completed Track M evidence; M1 reviewed and closed |
| 4 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | Project direction / Track architecture; progress wording may be historical |
| 5 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Cross-Track dependencies and acceptance contracts |
| 6 | [`UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md`](UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md) | Writer + Knowledge Web integration evidence |

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
  current portable full              866 / 866 PASS / 92.518 s
  current memory G1                  183 / 183 PASS / 25.987 s
  M2 deterministic L0 capture        NEXT / plan being prepared
```

M1 final gates:

```text
automatic recall              <= 5 items / <= 2000 chars / <= 800 estimated tokens
real recall deadline          <= 300 ms
first Tool Memory delta p95    20.177 ms  (<200 ms)
direct recall p95              18.631 ms  (<300 ms)
task-end append p95            16.178 ms  (<100 ms)
```

## Current branch

```text
worktree   E:\WorkSpace\UEAgentKit-Integration
branch     feature/memory-context
M1 base    137c3a3
```

Use actual Git facts rather than assuming the base hash remains current after local checkpoints.

## Documentation rule

Normal stage footprint:

```text
ONE Detailed Plan
ONE Result
```

Create a blocker-closure document only for a genuine technical blocker with a different investigation/exit gate. Do not recreate W4-style micro-document density for ordinary Track M work.

Completed/historical phase documents are preserved in [`Archive/`](Archive/). Precedence is:

```text
actual Git/repository facts
→ current handoff
→ DEVELOPMENT_WORKFLOW.md
→ current Plans README
→ current Detailed Plan / latest Result
→ historical documents
```

## Next work

M1 is closed. Prepare and execute **M2 — deterministic L0 automatic capture / Evidence Chain foundation** on the same `feature/memory-context` line.

M2 does not depend on P4. Its implementation is primarily Python/SQLite and should remain U0 until a final narrow Writer integration acceptance is specifically required. M1 performance/budget gates remain mandatory regression gates throughout M2.

Do not begin M3-M6 while M2 is active.

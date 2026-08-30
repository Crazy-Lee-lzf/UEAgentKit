# UEAgentKit Plans — Current Navigation

> Updated: 2026-08-30
>
> This directory now keeps only **current project-level planning/navigation documents** at the root. Completed phase Plans/Results are preserved under `Archive/`.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical current repository/Track/worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 test gates, U0-U3 UE validation, UE lease, documentation/commit rules |
| 3 | [`UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md`](UEAGENTKIT_W_V_INTEGRATION_RESULT_20260830.md) | Writer + Knowledge Web combined G3 evidence and R20 interpretation |
| 4 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) | Project direction / Track architecture; status wording may be historical |
| 5 | [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Cross-Track dependencies and acceptance contracts; current handoff overrides stale progress wording |

## Current project state

```text
published product                     0.7.0 / UE5.6
Track W / Writer                      complete
  W0-W4                              complete
  D1                                 complete
  W5 authorized scope                complete
  W5-S 50 GiB                        PASS
  R20                                deferred DirectHost fixture-lifecycle debt
Track V / Knowledge Web              complete
W + V integration                    G3 PASS
portable integration suite           845 / 845 PASS
```

R20 is not a project-level Writer blocker. The product correctly fails closed on the DirectHost fixture's semantic disk drift.

No 100 GiB / 160–180 GiB / SimulatedHDD50 expansion is currently authorized or claimed.

## What belongs in this root

Keep only documents that are needed to decide or execute the **current** development line:

```text
README.md
current project-level plan/spec
latest cross-Track integration result
```

A normal new stage should add at most:

```text
ONE Detailed Plan
ONE Result
```

After the stage is integrated/closed, move its Plan/Result to `Archive/` during a later documentation-maintenance pass.

Create a separate blocker-closure Plan only for a real blocker with a different investigation/exit gate.

## Historical plans and results

All completed/historical phase documents are preserved at:

[`Archive/`](Archive/)

This includes Agent Reliability R0-R4, 0.8 capability closeout, Writer W0-W5, D1, Track V V1/V2, animation follow-up plans, correction notes, and the executed W+V integration checklist.

Archive documents are historical evidence. When their status wording conflicts with current state, precedence is:

```text
actual Git/repository facts
→ current 2026-08-30 handoff
→ DEVELOPMENT_WORKFLOW.md
→ current Plans README
→ latest Result for the relevant capability
→ historical Plan/Handoff wording
```

## Next work

No next functional Track is automatically selected by this index.

Current candidate families are still described by Master/Midterm:

```text
M  Memory accumulation / retrieval
C  Source Control / P4 awareness
X  deeper UE read/write/analysis capabilities
D  engineering maintenance (tool metadata, CI, API docs, test-suite cleanup, Git worktree cleanup)
```

Before starting one, create/read its current Detailed Plan and include a Validation Budget from `docs/DEVELOPMENT_WORKFLOW.md`.

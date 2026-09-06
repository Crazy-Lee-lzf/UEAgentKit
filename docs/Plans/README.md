# UEAgentKit Plans — Current Navigation

> Updated: 2026-09-06
>
> Root keeps current project-level navigation and still-active authority documents. Completed phase Plans/Results are preserved under `Archive/`.

## Read first

| Order | Document | Purpose |
|---:|---|---|
| 1 | [`../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md`](../Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md) | Canonical repository / Track / worktree takeover state |
| 2 | [`../DEVELOPMENT_WORKFLOW.md`](../DEVELOPMENT_WORKFLOW.md) | Mandatory G0-G3 gates, U0-U3 UE validation, UE lease, Git/documentation rules |
| 3 | [`UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md`](UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md) | Permanent P4 Agent permission/advisory boundary |
| 4 | [`Archive/UEAGENTKIT_C3_CHANGELIST_RESOLVE_AUDIT_RESULT_20260904.md`](Archive/UEAGENTKIT_C3_CHANGELIST_RESOLVE_AUDIT_RESULT_20260904.md) | C3 owner-reviewed completion evidence, read-only real P4 smoke, A27 status |
| 5 | [`Archive/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_RESULT_20260903.md`](Archive/UEAGENTKIT_C1_C2_P4_MINIMUM_DOGFOOD_RESULT_20260903.md) | C1/C2 owner-reviewed completion evidence and A25 ratification |
| 6 | [`Archive/UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_RESULT_20260903.md`](Archive/UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_RESULT_20260903.md) | M5 reviewed completion evidence |
| 7 | [`UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md`](UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md) / [`UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md`](UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md) | Historical project direction / cross-Track contracts; current stage facts and Track C permissions are superseded by this README, the handoff, and the P4 boundary |

## Current project state

```text
published product                     0.7.0 / UE5.6 (unchanged)
Track W / Writer                      COMPLETE
Track V / Knowledge Web              COMPLETE
W + V integration                    G3 PASS
R20                                  deferred DirectHost fixture-lifecycle debt

test-suite tiering                   COMPLETE
Track M required usability stages    COMPLETE through M5
  M1-M5                              COMPLETE / REVIEWED / U0 / G2 PASS
  M6                                 optional / data-driven / do not auto-start

Track C / P4                         COMPLETE through C3
  C1 Source Control Awareness        COMPLETE / OWNER REVIEW PASS / U0
  C2 Advisory + local-write assist   COMPLETE / OWNER REVIEW PASS / U0
    A25 real edit fixture            PASS / owner-ratified preserved evidence
    C1/C2 accepted public checkpoint 5b705a7
  C3 CL preparation / Resolve        COMPLETE / OWNER REVIEW PASS / U0
    C3 accepted public checkpoint    5b705a7
    source-control G1                94 / 94 PASS / 6.699 s
    final portable G2               1062 / 1062 PASS / 101.414 s / 3 skipped
    A27 real CL/reopen/resolve       OWNER-FIXTURE BLOCKED
  C4 Memory integration              optional / deferred

next primary stage                    real-project write-enabled dogfood
Track X / new Writer / C4 / M6       only if dogfood evidence justifies them
```

C3's A27 is not an implementation failure: deterministic fake-P4 mutation coverage and all portable gates pass, while no new real `change -i` / `reopen` / `resolve -am` mutation was executed without a separately owner-designated safe C3 fixture. A27 may be satisfied naturally during dogfood if the owner designates an appropriate real target.

## Current branch

```text
worktree                    E:\WorkSpace\UEAgentKit-TrackC-Publish
branch                      feature/source-control-collaboration-publish
branch baseline             fdf6b5c12aceaefb0e61478bee7a9eefdf5ade76
public Track C checkpoint   5b705a7b693eff4af9ceb808df978f09e329dca9
main / origin/main          fdf6b5c12aceaefb0e61478bee7a9eefdf5ade76 / synchronized before publication branch preparation
branch upstream             none / local-only until first explicit push
branch vs origin/main       ahead 1 / behind 0 at feature checkpoint
push                        none
```

Always inspect actual Git state before modifying anything. Repository facts beat this navigation if they later differ.

## Current development-line Tool counts

Source Control is opt-in and defaults off.

| Mode | Base | + Memory | + Source Control | + Memory + Source Control |
|---|---:|---:|---:|---:|
| Offline | 10 | 24 | 16 | 30 |
| Live | 43 | 57 | 49 | 63 |
| Workflow-only | 67 | 81 | 73 | 87 |
| Live + Workflow | 100 | 114 | 106 | 120 |

The published 0.7.0 release still has its original release-time tool counts; the table above describes the current development line after M5/C3.

## P4 owner decision — authoritative boundary

`UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md` remains authoritative.

```text
P4 collaboration state
→ advisory / warning / strong warning / readiness
→ never independently hard-blocks local Writer testing

Agent MAY:
  inspect source-control state
  p4 edit / checkout
  explicit local writable override
  proven-clean exact-file safe sync
  create/update owned pending changelists
  reopen exact already-opened current-client files
  exact conflict-free text resolve -am within C3 eligibility
  durable audit + human final-action handoff metadata

Agent MUST NEVER:
  submit
  revert / revert -a
  delete P4-managed files
  expose generic P4/shell/argv passthrough
```

`.uasset/.umap` automatic content resolve remains unavailable. Unresolved UE binary packages stay unresolved and require human / future dedicated reconciliation evidence.

## Active next work — real-project write-enabled dogfood

Do **not** auto-start C4, M6, R5, broad Blueprint Graph CRUD, Level Actor CRUD, or another Source Control feature track.

The next stage is to exercise the already-built stack in an owner-designated real commercial UE5 project and let actual blockers set priority:

```text
understand task / inspect project + P4 state
→ Task Context / Impact / Memory as needed
→ exact Plan + Policy + Revision
→ source-control readiness / checkout assistance
→ bounded Writer mutation
→ authorized Save / checkpoint
→ Strong Verify
→ Semantic Diff / Verification Plan / Trust
→ pending CL preparation / audit handoff when useful
→ human Submit / Revert / Delete only
```

Before any real write/mutation, inspect the target project's actual UE/P4 mapping and obtain an owner-designated safe target. Do not reuse the historical A25 Blender fixture for new mutation merely because A25 was ratified.

If dogfood repeatedly exposes a concrete capability gap, create one focused Detailed Plan for that gap. Otherwise keep C4/M6/R5 deferred.

## Historical evidence

Completed Plans/Results belong under:

```text
docs/Plans/Archive/
```

The archived C3 Detailed Plan and Result preserve the implemented C3 contract and acceptance evidence. Historical documents keep their original stage wording; do not rewrite them merely to make old plans look current.

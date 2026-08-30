# UEAgentKit W + V Integration Checklist

> Date: 2026-08-30
>
> Purpose: persistent execution checklist for integrating the completed Writer line and Knowledge Web line after W5 blocker-closure.
>
> This document records execution order only. It does not authorize Push / Rebase / Tag / Release or published-version changes.

## Frozen Inputs

```text
main ref                         cc1f0c9990589d5e90ca4e41a46343b00e465822
Writer branch                   feature/live-writer-expansion
Writer frozen HEAD              71079710e0a6d49240f0296005ff3d93281c1ea6
Knowledge Web branch            feature/knowledge-web-view
Knowledge Web frozen HEAD       f2e687589894618f903b7d8170f32eee056353b2
published version               0.7.0
```

Writer status for integration:

```text
W0-W4                           complete
D1                              complete
W5 authorized execution scope  complete
W5-S 50 GB checkpoint          complete
R20 valid performance samples  deferred
R20 reason                     DirectHost fixture lifecycle semantic mutation
product fail-closed            correct / preserved
```

R20 is an isolated DirectHost fixture-lifecycle debt and is not an integration blocker. Do not weaken Revision / Policy / Dirty / Recovery gates or W4 bounds to close it.

## Mandatory Execution Order

- [ ] 1. Verify real Git refs / commit objects / merge-base / worktree state before writes.
- [ ] 2. Preserve the broken legacy main-worktree symbolic branch (`feature/agent-reliability`) as-is; do not blindly repair/reset it during integration.
- [ ] 3. Commit the current development-workflow documentation changes on the V branch as a documentation checkpoint.
- [ ] 4. Create a dedicated integration branch + worktree from the current `main` ref. Do not experiment directly in the main worktree.
- [ ] 5. Merge Writer `7107971` into integration.
- [ ] 6. Merge Knowledge Web + development-workflow documentation into integration.
- [ ] 7. Resolve only real merge conflicts. Expected overlap: `docs/Plans/README.md`; inspect all other overlaps before choosing either side.
- [ ] 8. Audit combined diff/history: D1, W5, V1/V2, `DEVELOPMENT_WORKFLOW.md`, version 0.7.0, and safety invariants must all be present.
- [ ] 9. Run G3 combined validation once.
- [ ] 10. If G3 passes, update `main` through the integration branch/checkpoint without touching the broken legacy feature worktree.
- [ ] 11. Re-check final refs, commit graph, worktrees, and clean integration state.
- [ ] 12. Leave test-suite structural cleanup deferred until current Track integration is complete.

## G3 Validation Budget

Risk class: medium integration; Writer safety primitives are already frozen and are not being redesigned.

UE level: U0 by default for merge validation. Escalate only if the merge itself changes C++/UE behavior or exposes a concrete composition failure.

Run once:

```text
full discovered Python suite
repository Ruff
compileall
ValidateRelease 0.7.0 with --skip-tests --skip-ruff when full suite/Ruff already ran
git diff --check
narrow combined CLI / Tool Registry / Knowledge View smoke
```

Do not repeat solely for integration:

```text
W4 C1-C12 full real-UE matrix
W4 H1-H6 recovery matrix
W5 50 GB benchmark
V2 5000-node benchmark
R1/R5 performance matrices
160-180 GB / SimulatedHDD50
```

Direct Build is required only if integration introduces a new C++ delta beyond the already-validated Writer commit or if combined-state evidence shows a build-composition risk.

## Prohibitions

```text
no push
no rebase
no tag
no release
no published-version change
no Reforge mutation
no weakening Policy / Revision / Dirty / recovery / Strong Verify invariants
no git reset/clean/revert of another worktree
no repair of missing legacy refs without a separately proven need
```

## Completion Definition

Integration is complete when:

```text
W + V + development workflow docs coexist in one integration history
all declared G3 gates pass
main points to the validated integration checkpoint
no new product safety regression is introduced
R20 remains explicitly deferred as fixture lifecycle debt
```

# UEAgentKit Development Workflow

> Status: project-wide development execution standard.
>
> Applies to: all Tracks, Detailed Plans, blocker-closure plans, implementation tasks, integration work, and release preparation.
>
> This document is a **mandatory pre-read before writing a new Detailed Plan**. A plan that does not classify its validation level and UE usage against this document is incomplete.

## 1. Purpose

UEAgentKit has moved beyond the early Writer bring-up phase. W1-W4 deliberately used unusually heavy validation because transaction safety, package persistence, recovery order, and independent verification were still being proven. That validation intensity must **not** become the default cost for every later Web, Memory, documentation, static-analysis, or maintenance change.

The default rule is now:

```text
fast development loop
→ risk-proportional checkpoint validation
→ one full stage-closure gate
→ integration/release validation only at integration/release boundaries
```

Do not treat every local commit as a release candidate.

## 2. Mandatory plan-author preflight

Before writing or revising any Detailed Plan / blocker-closure plan:

```text
1. Inspect actual git status / branch / HEAD.
2. Read docs/Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md.
3. Read docs/DEVELOPMENT_WORKFLOW.md (this file).
4. Read docs/Plans/README.md.
5. Read the current parent Track plan and latest Result.
6. Classify the planned work by:
   - code surface: docs / Python / Web / SQLite / C++ / UE runtime;
   - safety risk: low / medium / safety-critical;
   - UE requirement: U0 / U1 / U2 / U3;
   - test gate required at development, checkpoint, and closure.
7. State explicitly which full gates are intentionally deferred to stage closure or integration.
```

A Detailed Plan must include a short **Validation Budget** section. It must say what is run at G0/G1/G2, whether UE is required, and whether p95/statistical claims are required.

## 3. Validation gate levels

### G0 — Development loop

Use while actively editing code.

Goal: feedback in minutes, not tens of minutes.

Run only tests directly relevant to the changed domain, plus lightweight static checks for touched files.

Typical examples:

```text
focused unittest module/class/test
small domain test group
Ruff on touched Python files or touched package
syntax / import check
small deterministic fixture
```

Preferred repository entry points after the 2026-08-30 test-tiering maintenance:

```text
python scripts/RunPythonTests.py fast
python scripts/RunPythonTests.py domain <name>
python scripts/RunPythonTests.py full
```

Use `fast` as the broad G0 sanity gate, an affected `domain` group as the default G1 gate, and `full` only at G2/G3 or when diagnosing cross-domain behavior. Domain names are reported by `RunPythonTests.py --help`; multiple domains may be combined in one invocation.

Do **not** run the full discovered Python suite after every edit.
Do **not** run full `ValidateRelease.py` after every edit.
Do **not** start Unreal merely because the final stage will eventually need Unreal evidence.

### G1 — Functional checkpoint

Use when a coherent sub-capability is implemented and ready to freeze locally.

Typical requirements:

```text
all focused/domain tests for the changed capability
Ruff for the affected package / tests
compileall when Python module structure changed
contract/schema tests if serialized/public contracts changed
incremental/Direct Build only when required by C++ changes
one narrow UE smoke only when the changed behavior cannot be proven offline
```

G1 is **not** a full repository regression by default.

A Track may have several G1 checkpoints without repeating G2 each time.

### G2 — Stage / Track closure

Use once when a Detailed Plan reaches its exit gate, or when a significant blocker closure changes the stage result.

Default portable gate:

```text
1. full discovered Python suite — once (`python scripts/RunPythonTests.py full`)
2. repository Ruff — once
3. compileall
4. ValidateRelease version/schema/release checks without duplicating #1/#2
5. git diff --check
6. stage-specific benchmark / acceptance evidence
```

**No duplicate full-suite rule:** if the full Python suite and Ruff were already run explicitly in the same closure pass, run:

```text
python scripts/ValidateRelease.py ... --skip-tests --skip-ruff
```

If using a full `ValidateRelease.py` invocation that already runs Ruff + full Python, do **not** run those two separately beforehand unless there is a specific diagnostic reason. In that case the Result must say why the duplicate run was necessary.

The test count in Result documents is the actual discovered count for that run. Never encode a historical count as a permanent future gate.

### G3 — Integration / release

Use when combining independent Tracks into an integration branch, merging a major integration checkpoint, or preparing an actual release.

G3 validates the **combined state**, not every historical phase again.

Typical requirements:

```text
full portable validation once
integration-specific cross-Track tests
Direct Build when combined C++ changed
real UE acceptance only for capabilities affected by the integration
release-document checks only for an actual release boundary
```

Do not rerun unrelated historical UE matrices merely because two documentation/Python branches were merged.

## 4. Test types and when to use them

### 4.1 Fast unit tests

Pure logic, normalization, schema helpers, deterministic state transitions, parsers, query builders.

Target use:

```text
G0 always when relevant
G1 always when relevant
G2 included through full suite
```

### 4.2 Domain / integration-ish Python tests

Tests that use SQLite, filesystem fixtures, subprocesses, CLI entry points, or larger service composition.

These are more expensive and should not all be in every edit loop.

Target use:

```text
G0 selected relevant cases only
G1 affected domain group
G2 full discovered suite
```

Long-running Python tests should be identifiable so the repository can eventually maintain a fast-suite target separate from full regression.

### 4.3 Real UE acceptance

Use only when offline tests cannot prove the behavior that changed.

Examples requiring real UE evidence:

```text
resident package state
Blueprint compile / transaction behavior
Dirty-state rejection
Save / disk Revision transition
Strong Verify independence
Source Control Provider behavior
Asset/Level/Widget/AnimBP data only available through UE
```

Do not require real UE for:

```text
read-only Web rendering
SQLite-only queries
static C++ source indexing
pure documentation
pure deterministic Python transforms
```

### 4.4 Performance / stress benchmarks

Performance evidence is not correctness evidence. Do not make every performance sample repeat a full correctness bootstrap if the baseline can be proven more cheaply and exactly.

Default sampling rule:

```text
warmup + 5 measured samples per scenario/cache-state
report: p50 / min / max / mean / stddev
```

Use `n >= 10` only when a p95 claim is materially useful. If `n < 10`, explicitly mark p95 unavailable instead of increasing runtime merely to fill a table.

A benchmark plan must state whether it is measuring correctness, orchestration cost, cold-start cost, steady-state cost, or storage-scale degradation. Do not mix these into one number.

## 5. UE validation levels

Every plan must declare one of these levels.

### U0 — No UE

No UnrealEditor, UnrealEditor-Cmd, UBT, Direct Build, fixture Reset, or snapshot refresh.

Examples: V Track Web work, most Memory work, D2/D4, X5 static symbol indexing.

### U1 — Narrow smoke

One representative happy path, plus one failure/rejection case only when the feature changes a safety boundary.

Use for ordinary UE-connected feature checkpoints where the underlying safety primitive is already proven.

### U2 — Capability acceptance

A bounded real-UE matrix proving a new or changed safety-critical capability.

Examples: new Writer mutation semantics, recovery ordering, Source Control write preflight, new persistence behavior.

U2 should cover the changed state machine, not unrelated historical capabilities.

### U3 — Integration / release UE acceptance

Used for major combined integration or release validation. Select only the real-UE scenarios necessary to prove that the integrated surfaces still compose correctly.

U3 is not automatically “rerun every UE test ever written.”

## 6. Single-machine UE lease

Only one owner may hold the machine-wide UE lease at a time.

The lease covers:

```text
UnrealEditor.exe
UnrealEditor-Cmd.exe
UnrealBuildTool / Direct Build
WriteFixturePlan / fixture Reset
snapshot refresh requiring Unreal
real UE acceptance
large UE fixture generation
```

When the user owns the UE lease, Agents may continue:

```text
Python tests
SQLite work
Web work
static analysis
documentation
code review
non-UE benchmarks
```

Plans should separate offline implementation from the short UE acceptance window so an Agent does not reserve the Editor for the whole task.

## 7. Correctness fixtures vs performance fixtures

Do not force one fixture design to serve both goals.

### Correctness fixture

Optimized for exact reproducibility:

```text
official Reset
fixed semantic baseline
exact Revision / SHA proof
strict Dirty-state checks
independent recovery verification
```

Use for Writer safety and state-machine acceptance.

### Performance fixture

Optimized for repeatable measurement:

```text
one controlled initialization
stable Editor/session when appropriate
product recovery between samples
exact lightweight pre-sample baseline proof
no unnecessary cold Reset between every sample
```

A performance fixture may never weaken correctness gates in product code. It only avoids repeatedly rebuilding the entire test environment when the baseline can still be proven exactly.

If a fixture cannot maintain an exact baseline, mark that benchmark blocked rather than accepting drift.

## 8. Documentation granularity

Default for a normal stage:

```text
ONE Detailed Plan
ONE final Result
```

Keep internal substeps/checkpoints in those documents.

Create a separate blocker-closure plan only when a real blocker requires a different technical investigation or exit gate.

Do not copy the W4-0 ... W4-7 documentation density into ordinary low/medium-risk work. W4 was a safety-critical Writer hardening sequence and is an exception, not the template for every Track.

A small documentation-only or mechanical maintenance change does not require a new Detailed Plan unless it changes project direction/contracts.

## 9. Commit and Git safety

Before any modification, inspect actual status / branch / HEAD. Do not overwrite/reset/clean another Agent's worktree.

Do not Push / Rebase / Tag / Release or change the published version without explicit authorization.

A local checkpoint commit also requires explicit user authorization unless the current task already contains that authorization.

### Ref-write anomaly handling

The current development environment has shown cases where a commit object and reflog were written but a branch ref disappeared.

If HEAD/ref behavior is abnormal:

```text
1. stop further commits;
2. verify commit object, parent, tree, reflog, and worktree;
3. do NOT blindly create a second replacement commit;
4. do NOT run aggressive GC to remove dangling commits;
5. preserve evidence and repair/restore refs only after the intended commit is proven.
```

A missing branch ref is an environment/repository-state problem, not a reason to stage the whole repository as a new root commit.

## 10. Plan validation budget template

Every new Detailed Plan should contain something equivalent to:

```text
Validation Budget
-----------------
Risk class: low | medium | safety-critical
UE level: U0 | U1 | U2 | U3

G0 during implementation:
- focused tests: ...
- touched lint/static checks: ...

G1 functional checkpoint:
- domain tests: ...
- build/smoke: ...

G2 final stage closure:
- full discovered Python suite once
- Ruff once
- compileall
- ValidateRelease with duplicate Ruff/tests skipped if already run
- git diff --check
- stage-specific evidence: ...

Performance sampling:
- not required | warmup + 5 | >=10 because p95 is required

Explicitly not repeated:
- unrelated real UE matrices
- full suite at every substep
- release-only checks before release
```

If a plan intentionally exceeds this validation budget, it must state the risk/evidence that justifies the added cost.

## 11. Result reporting

Result documents should distinguish:

```text
implementation complete
focused/domain gates pass
G2 closure pass
UE acceptance pass / not required / blocked
benchmark complete / blocked
integration G3 not yet run
```

Do not label a stage fully complete if its own declared G2/U-level exit gate is still blocked. Conversely, do not keep a stage blocked merely because unrelated G3/release validation has not been run yet.

## 12. Efficiency is part of correctness

A validation process that is so expensive that Agents avoid running the right tests, monopolize the UE Editor, or spend most of a task re-proving unrelated behavior is itself a process defect.

The goal is not fewer tests. The goal is:

```text
right test
at the right risk level
at the right checkpoint
exactly as many times as needed
```

## 13. Deferred test-suite cleanup after current Track integration

Test-suite restructuring was **recorded and deferred until the W + V integration checkpoint**. That checkpoint is now complete, so the maintenance task is eligible to start, but it must still be selected explicitly by the owner. Do not begin broad test refactors merely to reduce raw test count while another selected feature Track is in flight.

Current audit snapshot (2026-08-30):

```text
tests/python files                         52
discovered tests at V2 snapshot           835
exact duplicate test bodies               0 groups
structurally similar candidates            20 groups / 47 tests
```

The audit indicates that the primary cleanup opportunity is **test structure and execution tiering**, not wholesale deletion of coverage. Several validation/error-boundary tests are the same structural template with different operations or parameters and are candidates for table-driven/subTest conversion. Some `*_smoke_contract.py` tests statically assert script text that may overlap with real integration/UE behavior coverage and should be reviewed case by case.

When the owner selects this cleanup task, begin by measuring rather than guessing:

```text
1. produce per-module / per-test timing data;
2. classify tests as fast-unit / domain / full-regression / release-only;
3. identify exact, structural, and cross-layer overlap;
4. convert repeated validation matrices to table-driven tests where coverage is unchanged;
5. review static smoke-contract assertions against actual integration/UE coverage;
6. split large workflow tests along workflow_plan/live/verify/batch domains where useful;
7. expose fast / domain / full suite entry points;
8. preserve all safety-critical Policy / Revision / recovery / persistence / Trust coverage.
```

Do **not** use a smaller raw test count as the success criterion. The target is shorter G0/G1 feedback with unchanged safety coverage and a measured reduction in G2 wall time.

### 13.1 2026-08-30 tiering completion

The first test-suite tiering maintenance pass is complete. The canonical entry points are now `RunPythonTests.py fast`, `RunPythonTests.py domain <name>`, and `RunPythonTests.py full`. Measured closure was 396 tests / 4.643 s for `fast`, 170 tests / 18.562 s for the `memory` domain, and 853 tests / 84.088 s for the final full suite.

This closes the prerequisite tiering task. Do not start a broad second cleanup pass automatically. Further fixture/test rewrites should be selected only when timing data shows that a specific domain is obstructing development.

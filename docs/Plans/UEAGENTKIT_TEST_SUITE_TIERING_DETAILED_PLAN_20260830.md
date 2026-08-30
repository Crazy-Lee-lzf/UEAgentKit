# UEAgentKit Test Suite Tiering — Detailed Plan

> Date: 2026-08-30
>
> Branch: `maintenance/test-suite-tiering`
>
> Baseline: `main@8b3fea0`
>
> Scope: engineering maintenance only. No product behavior change and no Unreal execution.

## 1. Goal

Make the existing Python validation suite usable at the development/checkpoint/closure levels already defined by `docs/DEVELOPMENT_WORKFLOW.md`.

Success is **not** a smaller raw test count. Success is:

```text
measured test cost
+ explicit fast/domain/full entry points
+ unchanged full-suite coverage
+ shorter G0/G1 feedback
+ no regression in G2 correctness coverage
```

## 2. Current baseline

At the W+V integration checkpoint:

```text
tests/python modules       53 currently discovered files
portable suite             845 tests at the integration checkpoint
full wall time             about 95.5 s in the latest recorded integration run
exact duplicate bodies     none in the prior audit
structural candidates      20 groups / 47 tests in the prior audit
```

The current release validator still runs `unittest discover` over all `tests/python/test_*.py` when tests are enabled. That remains the G2/full correctness source of truth.

## 3. Implementation slices

### T1 — Measurement baseline

Add a deterministic Python timing utility that discovers the same suite as the full gate and records per-test/per-module elapsed time in one process.

Required outputs:

```text
total test count
total elapsed time
module elapsed time
individual slowest tests
JSON output suitable for later comparison
```

Use the measured data to decide which tests belong in the default fast loop. Do not classify from filenames alone when timing evidence contradicts intuition.

### T2 — Unified test runner

Add a single repository entry point with three modes:

```text
python scripts/RunPythonTests.py fast
python scripts/RunPythonTests.py domain <name>
python scripts/RunPythonTests.py full
```

Requirements:

- `full` must remain equivalent to current `unittest discover -s tests/python -p test_*.py`.
- `fast` must contain deterministic low-cost tests appropriate for G0.
- `domain` must select coherent repository domains for G1 without requiring per-test decorators.
- unknown domains fail with a clear diagnostic.
- runner selection must be inspectable/tested rather than hidden in shell scripts.

Initial domain families should cover the active architecture rather than every historical Track name. Candidate families include:

```text
core
index
memory
writer
workflow
knowledge
retarget
reliability
release
```

The final mapping may be adjusted after timing measurement.

### T3 — Minimal structural cleanup

Only perform structural changes that are directly justified by T1/T2 evidence.

Allowed:

- table-driven/subTest conversion where assertion coverage is unchanged;
- eliminating repeated expensive fixture setup when the fixture is immutable and isolation remains exact;
- splitting an oversized test module only if it materially improves domain selection or maintenance;
- documenting smoke-contract tests that intentionally remain separate.

Not allowed in this stage:

- broad deletion of tests;
- weakening Policy / Revision / Recovery / Persistence / Trust / tamper coverage;
- changing Writer product semantics;
- changing release/version contracts merely to make tests easier;
- adding Unreal dependencies.

### T4 — Workflow integration

Update development documentation only where needed so future plans can use the new runner directly.

`ValidateRelease.py` must keep its existing full-suite semantics. It may call the unified runner in `full` mode only if the behavior remains equivalent and tests prove it.

## 4. Acceptance

The stage closes when all are true:

1. A timing report establishes actual current baseline and slow-test distribution.
2. `fast`, `domain`, and `full` entry points exist and are covered by focused tests.
3. `full` discovers exactly the same tests as the pre-change discovery contract.
4. Representative domain runs select the intended modules and do not silently omit requested domains.
5. No safety-critical coverage is removed.
6. G2 portable closure passes once.
7. Result document records measured before/after wall times and any intentionally deferred cleanup candidates.

## 5. Validation Budget

```text
Risk class: low-medium
UE level: U0

G0 during implementation:
- focused runner/measurement tests
- representative existing test modules
- Ruff on touched Python files

G1 functional checkpoint:
- runner selection/coverage tests
- fast mode
- representative domain modes
- full discovery-equivalence check without repeating release validation
- compileall if module structure changes

G2 stage closure, once:
- full discovered Python suite once
- repository Ruff once
- compileall
- ValidateRelease.py --skip-tests --skip-ruff
- git diff --check
- timing comparison report

Performance sampling:
- one baseline full timing run before classification
- one final full timing run after implementation
- no p95 claim required

Explicitly not repeated:
- UnrealEditor / UnrealEditor-Cmd / UBT
- W4 real-UE acceptance matrix
- W5 heavy performance fixtures
- V2 5000-node benchmark
- full suite after every edit
```

## 6. Deliverables

Default deliverables for this stage:

```text
docs/Plans/UEAGENTKIT_TEST_SUITE_TIERING_DETAILED_PLAN_20260830.md
scripts/MeasurePythonTests.py
scripts/RunPythonTests.py
tests/python/test_python_test_runner.py
benchmarks/test_suite_timing_20260830_before.json
benchmarks/test_suite_timing_20260830_after.json
docs/Plans/UEAGENTKIT_TEST_SUITE_TIERING_RESULT_20260830.md
```

Additional production/test files may change only when T1 measurement shows a concrete need.

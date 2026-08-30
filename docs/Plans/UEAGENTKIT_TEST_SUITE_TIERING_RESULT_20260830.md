# UEAgentKit Test Suite Tiering — Result

> Date: 2026-08-30
>
> Stage: engineering maintenance / U0
>
> Status: **COMPLETE — G2 PASS**

## 1. Outcome

The Python suite now has explicit risk-proportional entry points without reducing full regression coverage:

```text
python scripts/RunPythonTests.py fast
python scripts/RunPythonTests.py domain <name>
python scripts/RunPythonTests.py full
```

Supported initial domains:

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

No existing product/safety test was deleted or weakened in this stage.

## 2. Measured result

Baseline full timing before the runner/tiering changes:

```text
845 tests
84.071 s
PASS
```

Final full timing after adding 8 runner/measurement regression tests:

```text
853 tests
84.088 s
PASS
```

The full gate therefore retained all previous coverage, added 8 maintenance tests, and remained effectively flat in wall time.

Development/checkpoint scopes:

```text
fast           396 tests     4.643 s     PASS
memory         170 tests    18.562 s     PASS
index           29 tests     4.629 s     PASS
```

The broad G0 `fast` gate is about 94.5% shorter than the measured full baseline while still running 396 tests. The next planned Memory work can use the 170-test `memory` domain as its default G1 checkpoint instead of repeatedly running the entire repository suite.

Timing evidence is stored in:

```text
benchmarks/test_suite_timing_20260830_before.json
benchmarks/test_suite_timing_20260830_after.json
```

## 3. Where the full-suite time actually goes

The baseline showed that runtime is concentrated rather than broadly distributed:

```text
test_knowledge_view_visualization    23.681 s
test_editor_bridge                   13.873 s
test_knowledge_view                  13.824 s
test_agent_workflow                  11.964 s
test_task_context                     4.797 s
test_snapshot_lifecycle               2.899 s
test_mcp_server                       1.925 s
test_patches                          1.435 s
```

Those measured-heavy modules remain fully covered by `full` and their affected domain gates, but are omitted from the broad `fast` gate.

The largest single-test cost remains the bounded startup-freeze retry test at about 2.53 s. Many Knowledge View and Editor Bridge tests have roughly 0.5 s per-case lifecycle cost. These are valid future optimization candidates, but this stage intentionally did not rewrite their product fixtures because tiering already removes that cost from ordinary G0 work.

## 4. Runner contract

`RunPythonTests.py` uses the repository test files as the discoverable source of truth:

- `full` uses the same `unittest discover tests/python/test_*.py` semantics as the historical gate;
- `fast` is the full module inventory minus an explicit measured-heavy module set;
- `domain` uses explicit inspectable module groups;
- multiple domains are de-duplicated;
- unknown/missing domains fail closed with a clear diagnostic;
- new test modules continue to enter `full` automatically.

`test_python_test_runner.py` verifies full discovery equivalence, measurement discovery equivalence, fast subset behavior, domain configuration validity, cross-domain selection, and fail-closed input handling.

## 5. Ruff reproducibility finding

Creating a clean Python 3.12 development environment exposed an unrelated but important development-gate drift:

```text
pyproject before: ruff>=0.12,<1
clean install:    ruff 0.16.5
result:           hundreds of lint findings in pre-existing repository code
```

Re-running the same repository gate with Ruff 0.12.12 passed. The dev dependency was therefore narrowed to:

```text
ruff>=0.12,<0.13
```

This does not change runtime/product dependencies. It makes the already-defined repository Ruff gate reproducible instead of allowing future minor-rule drift to invalidate an otherwise unchanged historical codebase.

## 6. Validation evidence

### G0/G1

```text
Ruff on touched runner/measurement/tests      PASS
runner regression tests                      8 / 8 PASS
fast scope                                   396 / 396 PASS
memory domain                                170 / 170 PASS
index domain                                  29 / 29 PASS
writer/knowledge/reliability selection       configuration resolved PASS
```

### G2

```text
full timed discovery                         853 / 853 PASS, 84.088 s
Ruff 0.12.12: src + tests + scripts          PASS
compileall: src + scripts + tests/python     PASS
ValidateRelease --skip-tests --skip-ruff     PASS (0.7.0 / 3 schemas / 16 examples)
pip check                                    PASS
git diff --check                             PASS
UE validation                                not required (U0)
```

No W4/W5 real-UE matrix or V2 5000-node benchmark was repeated because this stage changes only offline development/test tooling.

## 7. Deferred maintenance

Do not immediately continue into a broad test rewrite merely because hotspots are now measured. The remaining optional cleanup should be evidence-driven and can be revisited when one of these modules becomes an actual development bottleneck:

```text
Knowledge View server lifecycle overhead
Editor Bridge per-test lifecycle overhead
Agent Workflow fixture setup
snapshot retry timing
structurally repeated validation matrices
static smoke-contract overlap
```

The immediate objective of the maintenance stage is already met: G0/G1 feedback is now materially shorter while G2 coverage is unchanged.

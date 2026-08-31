# UEAgentKit M2 Deterministic L0 Automatic Capture — Result

> Date: 2026-08-30
>
> Branch: `feature/memory-context`
>
> Base: `16fde234aea9004734515b13715720f14148cb2c`
>
> Stage state: **COMPLETE / REVIEWED / G2 PASS**
>
> Risk: medium / required UE level: U0

## 1. Scope and outcome

M2 executed the frozen Detailed Plan through all implementation slices:

```text
M2-0 schema v4 + L0/Evidence Chain service foundation
M2-1 bounded L0 read tools + B5/B6/B7 measurements
M2-2 optional capture binding + direct Writer capture
M2-3 W4/checkpoint-set/recovery aggregate coalescing
M2-4 restart idempotence + Evidence Chain foundation
M2-5 G1/G2 closure
```

No M3 distillation, model-generated hypothesis, prompt injection, P4 work, C++, EditorBridge semantic change, new required runtime dependency, version change, commit, push, rebase, tag, release, or Unreal execution was introduced.

## 2. Implemented contract

Project Memory schema v4 adds append-only `memory_l0_events` and `memory_evidence_chains` with the planned indexes, constraints, optional hypothesis foreign key, and additive v3-to-v4 migration. Existing Memory records, Knowledge nodes, and Active Work data are preserved.

Artifact-backed events store only fixed-root-relative references, exact SHA-256 digests, bounded metadata, and deterministic IDs derived from project/event/source/digest identity. Exact-state replay returns the existing event; changed artifact bytes append a new immutable observation. Capture batches are atomic and limited to eight events.

Automatic capture now covers:

```text
direct resident live writes and resulting Change Set state
direct checkpoint Save/Verify and resulting Change Set state
W4 terminal/partial batch execution state
checkpoint-set Save and aggregate Verify evidence
semantic diff + trust pointers from checkpoint-set Verify
terminal/partial/blocked recovery state
bounded deterministic WorkflowError-style pre-artifact rejection
```

W4 child operations, checkpoint-set child Save/Verify operations, and recovery child Undo operations use scoped capture suppression. Each high-level aggregate capture is coalesced into one Memory transaction rather than an N-child commit sequence.

Memory binding is optional and fixed to one project and artifact root. Disabled or suppressed capture exits before artifact allocation/path probing. Capture failure remains visible through a degraded `memoryCapture` result but cannot invalidate an already-successful Writer persistence result.

Two bounded, read-only MCP tools were added:

```text
ue_memory_list_l0_events
ue_memory_get_l0_event
```

They are registered only when Project Memory is enabled, remain fixed-project, return metadata/evidence pointers only, enforce the 100-item ceiling, and do not read referenced artifacts. Project Memory status now reports L0 event, pending L0 event, and Evidence Chain counts.

## 3. Audit corrections during closure

The execution Agent corrected two test/contract issues before its own G2:

1. Registry fixtures still described the pre-M2 public tool order and counts. They now include the two planned L0 read tools and the updated bounded handler count.
2. Memory-disabled Writer and aggregate paths initially built capture metadata before the coordinator returned `None`. Call sites now perform the fixed coordinator-enabled check first, preserving the frozen cheap no-op contract. A regression test proves direct disabled capture does not probe the Change Set artifact path.

The post-Agent review then found and corrected three additional contract-hardening issues:

3. Aggregate child-capture suppression used one process-global integer depth. A concurrent unrelated Writer request could therefore inherit another request's temporary suppression. The suppression depth is now a `ContextVar`, retaining nested semantics while isolating independent execution contexts; a two-thread regression test proves suppression does not leak.
4. Artifact SHA-256 used `Path.read_bytes()`, which read an entire durable artifact into memory. Exact digests are now computed in bounded 1 MiB streaming chunks; a regression test forbids whole-file `read_bytes()` during artifact capture.
5. Direct checkpoint Strong-Verify stale/value-mismatch paths already persisted a durable checkpoint artifact before raising, but the MCP rejection wrapper could also create an inline `workflow_rejection`. Those failure exits now capture the persisted checkpoint state as the authoritative artifact-backed L0 event and expose `checkpointId`/capture metadata; `checkpointId` is recognized as a durable-failure identity so no duplicate inline rejection is created.

No Writer safety invariant, frozen public API, or M2 architecture contract was weakened by the review.

## 4. Benchmark evidence

Canonical final report:

`benchmarks/memory/m2_memory_overhead_after_20260830.json`

```text
samples                                      20
first Tool Memory incremental p95        13.525 ms   < 200 ms  PASS
direct automatic recall p95              12.247 ms   < 300 ms  PASS
task-end append p95                       13.439 ms   < 100 ms  PASS
single L0 capture p95                      8.479 ms   reported
four-event L0 capture batch p95           14.972 ms   < 100 ms  PASS
exact-state duplicate replay p95           3.744 ms   reported
duplicate replay new rows                      0       PASS
```

Recall functional budgets also remain green:

```text
populated Task Context       3 items / 1633 chars / 769 estimated tokens
direct populated recall     3 items / 1633 chars / 748 estimated tokens
no-hit recall               0 items / 0 chars
```

Intermediate G0/G1 benchmark reports were retained as execution evidence; the canonical closure values above come from the post-G2 20-sample run.

## 5. Validation evidence

Focused/G0:

```text
M2 MCP + Memory focused                 53 / 53 PASS / 4.704 s
registry regression                      4 / 4 PASS / 0.019 s
direct/aggregate disabled-path fix    125 / 125 PASS / 13.424 s
fast G0 final                         420 / 420 PASS / 9.146 s
```

The first broad fast run discovered three stale registry fixtures after the two planned tools were added (`420` tests / `3` failures / `8.617 s`). Only the registry module was rerun after its contract update, followed by the successful final G0 above.

G1:

```text
memory domain                          199 / 199 PASS / 31.213 s
workflow/MCP focused                   144 / 144 PASS / 20.711 s
M1+M2 20-sample benchmark gate                     PASS / 6.381 s
touched Ruff                                        PASS
git diff --check                                    PASS
```

Execution-Agent G2, run once after the Agent's final source state:

```text
full Python                           882 / 882 PASS / 85.109 s
Ruff                                               PASS
compileall                                         PASS
ValidateRelease 0.7.0                             PASS (--skip-tests --skip-ruff)
Schemas                                               3 PASS
Patch examples                                        16 PASS
git diff --check                                   PASS
final M1+M2 20-sample benchmark gate               PASS / 3.964 s
UE / UBT                                         not run (required U0)
```

The reviewer subsequently changed final source code for the three issues in Section 3, so the Agent's 882-test G2 could no longer prove the final reviewed tree. One justified reviewer-closure G2 was therefore run after the review fixes:

```text
focused new/review tests                 16 / 16 PASS / 0.986 s
Agent Workflow final                     74 tests PASS
MCP focused final                        19 / 19 PASS / 1.944 s
memory G1 final                         202 / 202 PASS / 21.483 s
full Python final                       885 / 885 PASS / 86.141 s
Ruff 0.12.12                                         PASS
compileall                                           PASS
ValidateRelease 0.7.0                               PASS (--skip-tests --skip-ruff)
Schemas                                                 3 PASS
Patch examples                                          16 PASS
git diff --check                                     PASS
final M1+M2 benchmark gate                           PASS
UE / UBT                                           not run (required U0)
```

The M2 execution Agent itself ran the full suite once and the Memory domain once. The second full/Memory run was reviewer closure after reviewer-authored code changes, not execution-Agent test churn. Unreal runs remain 0.

## 6. Acceptance matrix

```text
A1  schema v3 -> v4 additive migration                              PASS
A2  v3 data preserved                                               PASS
A3  relative artifact ref + exact digest                            PASS
A4  changed digest appends / exact replay dedupes                    PASS
A5  append-only L0 public surface                                    PASS
A6  Evidence Chain storage + optional FK                             PASS
A7  Memory disabled performs zero capture writes                     PASS
A8  direct Writer durable automatic capture                          PASS
A9  W4 child capture coalesced                                        PASS
A10 checkpoint-set aggregate + semantic_diff + trust capture         PASS
A11 recovery terminal/partial/failure-boundary capture                PASS
A12 deterministic bounded rejection capture                          PASS
A13 capture failure non-fatal and visible                             PASS
A14 no absolute paths or unbounded L0 payloads                        PASS
A15 bounded fixed-project read-only L0 tools                          PASS
A16 retained M1 recall/performance gates                              PASS
A17 four-event L0 batch p95 < 100 ms                                  PASS
A18 memory G1                                                         PASS
A19 final reviewed source state has a full G2 proof                    PASS
A20 no new dependency / LLM / P4 / UE requirement                    PASS
```

## 7. Main files

```text
src/ue_agent_kit/memory_schema.py
src/ue_agent_kit/memory_l0.py
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/mcp_memory_tools.py
src/ue_agent_kit/mcp_server.py
src/ue_agent_kit/tool_registry.py
src/ue_agent_kit/workflow_common.py
src/ue_agent_kit/workflow_live.py
src/ue_agent_kit/workflow_verify.py
src/ue_agent_kit/bounded_batch.py
src/ue_agent_kit/checkpoint_sets.py
src/ue_agent_kit/batch_recovery.py
src/ue_agent_kit/mcp_workflow_tools.py
scripts/MeasureMemoryOverhead.py
scripts/RunPythonTests.py
tests/python/test_memory_l0.py
benchmarks/memory/m2_memory_overhead_after_20260830.json
```

## 8. Execution efficiency and next stage

The execution Agent reported **1 h 07 m 51 s** from takeover through its final benchmark and Result drafting. Its longest measured validation steps were the full G2 suite (`85.109 s`), memory G1 (`31.213 s`), and focused workflow/MCP checkpoint (`20.711 s`). The harness did not expose a reliable aggregate tool-call count. Reviewer time is tracked separately and is not part of that Agent-speed comparison.

M2 is closed at U0. M3 may build deterministic distillation and Evidence Chain reasoning on this foundation, but M2 deliberately does not infer verdicts, mutate `distilled`, or inject L0 into prompts.

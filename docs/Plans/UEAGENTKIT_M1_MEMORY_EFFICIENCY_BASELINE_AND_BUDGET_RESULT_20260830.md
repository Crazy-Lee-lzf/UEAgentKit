# UEAgentKit M1 Memory Efficiency Baseline and Budget Gate — Result

> Date: 2026-08-30
>
> Branch: `feature/memory-context`
>
> Base: `137c3a35e943f2c8e65f13dd8befe95aec3c6612`
>
> Stage state: **COMPLETE / REVIEWED / G2 PASS**
>
> Risk: low-medium / UE level: U0

## 1. Scope and outcome

M1 executed the prepared Detailed Plan only:

```text
M1-0 measurement harness + pre-change baseline
M1-1 central hard RecallBudget
M1-2 item/content/token enforcement + no-hit semantics
M1-3 real 300 ms deadline
M1-4 Task Context automatic Memory integration
M1-5 benchmark + portable closure
```

No M2-M6 behavior, Memory schema migration, C++, Unreal, P4, new required runtime dependency, version change, push, rebase, tag, or release was introduced.

## 2. Implemented contract

Automatic Memory recall now has centralized server ceilings:

```text
max recalled items          5
max recalled content chars  2000
max estimated final tokens  800
real elapsed deadline       300 ms
```

Automatic surfaces:

```text
ProjectMemoryService.get_context
ue_memory_get_context
ue_get_task_context -> Memory section
```

Explicit progressive reads remain separate:

```text
ue_memory_expand_node
ue_memory_get
ue_memory_get_evidence
```

They retain the existing `ContextBudget` / explicit-read semantics and are not incorrectly constrained to five automatic-recall items.

No-hit automatic recall produces zero recalled items and zero recalled content. The structured MCP envelope remains available for diagnostics; no placeholder prose such as `No memory found` is generated for future injection.

The 300 ms limit is a real deadline using `time.monotonic()` plus `sqlite3.set_progress_handler`, not a post-hoc elapsed-time warning. Deadline interruption returns a bounded empty/partial result with `recall-deadline`; unrelated SQLite failures remain failures.

## 3. Post-Agent review corrections

The first execution Agent completed the planned functionality and validation. A subsequent repository review found and corrected four closure issues before accepting M1:

1. SQLite progress handling was initially invoked every VM instruction. It is now sampled every `1000` VM steps, preserving deadline interruption without unnecessary Python callback overhead.
2. Final-envelope token accounting initially calculated `usage` before adding recall accounting fields. Final `usage.usedChars` / `usage.estimatedTokens` / top-level `estimatedTokens` are now iterated to the exact serialized response size before enforcing the 800-token ceiling.
3. Focused tests now prove expired-deadline behavior, progress-handler cleanup, and propagation of genuine non-timeout `sqlite3.OperationalError` failures.
4. Task Context and the benchmark now explicitly prove the `5 / 2000 / 800 / no-hit-empty` contract; benchmark OFF/ON first-Tool samples are measured as adjacent pairs rather than two separated batches.

These corrections changed core deadline/accounting code after the execution Agent's first G2, so one fresh final G2 was intentionally run. This is not an accidental duplicate closure run.

## 4. Benchmark evidence

### Pre-change baseline

`benchmarks/memory/m1_memory_overhead_before_20260830.json`

```text
first Tool Memory incremental p95   13.613 ms
direct automatic recall p95          12.079 ms
task-end append p95                  13.065 ms
direct recalled items                14
direct content chars               7156
direct estimated tokens            1968
```

### Final reviewed M1 gate

`benchmarks/memory/m1_memory_overhead_after_20260830.json`

```text
first Tool Memory incremental p95   20.177 ms   < 200 ms  PASS
direct automatic recall p95         18.631 ms   < 300 ms  PASS
task-end append p95                 16.178 ms   < 100 ms  PASS

Task Context populated recall        3 items / 1633 chars / 769 tokens
Direct populated recall              3 items / 1633 chars / 748 tokens
No-hit recall                        0 items / 0 chars
```

All benchmark functional gates also PASS:

```text
items <= 5
content chars <= 2000
estimated final tokens <= 800
no-hit items/content == 0
```

The progress-handler review reduced direct-recall p95 from the execution Agent's interim `24.941 ms` to the final `18.631 ms` while retaining the real deadline.

M1 still does not implement actual L2/L3 prompt injection. These token measurements describe the bounded automatic Memory envelope that later stages may consume.

## 5. Final validation evidence

Execution-Agent closure before review:

```text
fast                   405 tests / 6.875 s   PASS
memory domain          179 tests / 23.646 s  PASS
full                    862 tests / 90.545 s PASS
```

Post-review focused/G1:

```text
focused review tests    62 tests / 6.908 s   PASS
memory domain           183 tests / 25.987 s PASS
reviewed benchmark gate                        PASS
```

Final reviewed G2:

```text
full Python             866 / 866 PASS / 92.518 s
Ruff                    PASS
compileall              PASS
ValidateRelease 0.7.0   PASS (--skip-tests --skip-ruff)
Schemas                  3 PASS
Patch examples          16 PASS
git diff --check        PASS
UE / UBT                not run (U0)
```

## 6. Main files

```text
scripts/MeasureMemoryOverhead.py
src/ue_agent_kit/memory_context.py
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/mcp_memory_tools.py
src/ue_agent_kit/task_context.py
scripts/RunPythonTests.py

tests/python/test_memory_overhead.py
tests/python/test_memory_context.py
tests/python/test_mcp_server.py
tests/python/test_task_context.py

benchmarks/memory/m1_memory_overhead_before_20260830.json
benchmarks/memory/m1_memory_overhead_after_20260830.json
```

## 7. Acceptance

```text
A1  before baseline captured                                  PASS
A2  automatic recall <= 5 items                               PASS
A3  automatic recall <= 2000 content chars                    PASS
A4  final structured automatic context <= 800 est. tokens     PASS
A5  real 300 ms deadline                                      PASS
A6  bounded deadline result + explicit reason                 PASS
A7  caller cannot widen hard server limits                    PASS
A8  explicit progressive reads remain separate                PASS
A9  no-hit injectable content empty/no placeholder            PASS
A10 Task Context cannot bypass RecallBudget                   PASS
A11 first-Tool Memory incremental p95 < 200 ms                PASS
A12 direct recall p95 < 300 ms                                PASS
A13 task-end append p95 < 100 ms                              PASS
A14 Memory-disabled path adds no automatic Memory write       PASS
A15 required runtime dependencies unchanged                   PASS
A16 Memory G1                                                PASS
A17 final reviewed G2                                        PASS
A18 final benchmark functional/performance gates             PASS
```

## 8. Next stage

M1 is closed. The next Track M stage is **M2 — deterministic L0 automatic capture / Evidence Chain foundation**.

M2 must preserve every M1 efficiency gate. M2 is primarily Python/SQLite and can be implemented offline; only a narrow real-Writer acceptance should use UE if required at final integration proof. P4 is not a prerequisite.

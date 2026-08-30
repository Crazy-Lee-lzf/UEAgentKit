# UEAgentKit M1 Memory Efficiency Baseline and Budget Gate — Detailed Plan

> Date: 2026-08-30
>
> Branch: `feature/memory-context`
>
> Baseline: `137c3a35e943f2c8e65f13dd8befe95aec3c6612`
>
> Stage state: **READY FOR IMPLEMENTATION**
>
> Risk: low-medium / UE level: U0
>
> Scope: Memory efficiency measurement and hard recall-budget enforcement only. No L0 auto-capture, distillation, vector retrieval, prompt injection, schema migration, C++ or Unreal execution.

## 1. Authority and current repository facts

Repository facts override historical plan wording.

At M1 start:

```text
published product                         0.7.0 / UE5.6
Track W / Writer                          complete
Track V / Knowledge Web                   complete
W + V integration                        G3 PASS
D1 workflow split                        complete
test-suite tiering                       complete
GitHub Python 3.11 / 3.12 CI             PASS at 137c3a3
main / origin/main                       137c3a3
feature/memory-context / origin branch   137c3a3
working tree                             clean before this Plan
```

M1 is therefore allowed to begin. The historical W4/D1 prerequisites have already been satisfied.

### 1.1 New-Agent execution handoff

This Detailed Plan was prepared in a planning-only Chat. The next coding Agent should **execute this Plan rather than create a second M1 Plan**.

Expected planning-only working-tree changes at takeover are:

```text
M  docs/Handoffs/UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260830.md
M  docs/Plans/README.md
?? docs/Plans/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_DETAILED_PLAN_20260830.md
```

These files are intentional. Do not clean/reset/discard them. No M1 product implementation has started yet.

The first implementation action is M1-0: build the measurement harness and capture the pre-change baseline. Do not alter recall behavior until that baseline evidence exists.

Validation policy comes from `docs/DEVELOPMENT_WORKFLOW.md`. Current preferred Python gates are:

```text
G0  python scripts/RunPythonTests.py fast
G1  python scripts/RunPythonTests.py domain memory
G2  python scripts/RunPythonTests.py full       # once at closure
```

## 2. Goal

M1 implements the principle **“measure first, then add Memory features.”**

The stage has two responsibilities:

1. establish a reproducible measurement harness for current Memory overhead;
2. make the existing automatic Memory recall path incapable of exceeding the frozen latency/content budgets.

M1 does **not** try to improve recall quality or create new memories. It creates the guardrails that M2-M5 must continue to pass.

The stage is successful when the project can answer, with measured evidence:

```text
How much slower is the first normal Tool call when Memory is enabled?
How expensive is one task-end append?
How large can one automatic Memory recall become?
Can a recall run past 300 ms?
What happens when there is no relevant Memory?
```

## 3. Explicit non-goals

The following are outside M1 and must not be pulled forward:

```text
M2  L0 automatic capture / schema v3 -> v4 / Evidence Chain
M3  deterministic L0 -> L1 distillation
M4  Vector / sqlite-vec / model2vec / RRF / schema v4 -> v5
M5  L2/L3 generation and actual prompt injection
M6  symbolic/Mermaid compression
C++ Plugin changes
UnrealEditor / UnrealEditor-Cmd / UBT execution
new required Python runtime dependencies
published version changes
```

`pyproject.toml [project].dependencies` must remain `[]`.

## 4. Current implementation audit

### 4.1 `memory_context.py`

Current internal progressive context budget:

```text
ContextBudget.max_chars     = 8000
ContextBudget.max_nodes     = 12
ContextBudget.max_records   = 20
ContextBudget.max_depth     = 2
```

Current validation allows much larger explicit diagnostic requests:

```text
max_chars       512 .. 100000
max_nodes       1   .. 100
max_records     0   .. 200
max_depth       0   .. 16
```

`build_memory_context()` currently:

```text
selects nodes
-> selects records
-> selects Active Work
-> assembles a structured response
-> enforces character budget only
```

It has no total recalled-item hard cap and no elapsed-time deadline.

The current token estimate is deterministic:

```text
estimate_tokens(chars) = ceil(chars / 4)
```

### 4.2 `ue_memory_get_context`

Current public defaults in `mcp_memory_tools.py` are:

```text
budget_chars = 8000
max_nodes    = 12
max_records  = 20
max_depth    = 2
```

The caller can currently request values far above the intended M1 recall budget. The public tool returns a structured MCP envelope and this return type is already covered by tests.

### 4.3 Explicit progressive read must remain available

`ue_memory_expand_node` is an explicit user/Agent deep-read operation. It is not equivalent to automatic recall.

M1 must **not** globally reduce `ContextBudget` to 2000 chars / 5 items, because doing so would unnecessarily cripple explicit node expansion and evidence-on-demand workflows.

Therefore M1 freezes two separate concepts:

```text
ContextBudget   = internal / explicit progressive browsing budget
RecallBudget    = hard automatic-recall budget used by ue_memory_get_context
                  and Task Context Memory inclusion
```

The existing explicit expand/evidence paths remain bounded by their current explicit budgets.

### 4.4 Task Context currently allows a much larger Memory share

`task_context.py` currently uses:

```text
MEMORY_BUDGET_FRACTION = 0.35
memory chars = max_output_tokens * 4 * 0.35
```

and then calls `memory_service.get_context(detail_level=2)` with the current default node/record limits.

This path is an automatic Memory inclusion surface and must be constrained by the same M1 recall hard limits. Otherwise `ue_memory_get_context` would be bounded while `ue_get_task_context` could still bypass the new contract.

### 4.5 Existing safety/value behavior to preserve

Current tests already cover:

```text
progressive detail levels 0..4
stale/superseded exclusion
Revision bindings
character-budget truncation
structured nextActions
exact usage calculation
explicit evidence-on-demand
Active Work inclusion
```

M1 must preserve these semantics. It adds a stricter automatic-recall envelope; it does not weaken freshness, evidence or status semantics.

## 5. Frozen M1 efficiency contract

### 5.1 Hard automatic-recall limits

One automatic Memory recall has all four limits simultaneously:

```text
RECALL_MAX_ITEMS               = 5
RECALL_MAX_CONTENT_CHARS       = 2000
RECALL_MAX_ESTIMATED_TOKENS    = 800
RECALL_DEADLINE_MS             = 300
```

These are server-side hard ceilings, not recommendations.

A caller may request **less**, but may never widen them. Requests above a hard ceiling are normalized/clamped to the ceiling and reported through effective-budget metadata; they must not silently create an oversized response.

This behavior is preferred over rejecting an otherwise valid read request, because the parent contract requires bounded partial results rather than a budget error.

### 5.2 What counts as an “item”

For M1, recalled items are the concrete entries returned in:

```text
nodes[] + activeWork[] + records[]
```

Their combined count must be `<= 5` for the automatic recall path.

Fixed envelope metadata does not count as an item:

```text
schemaVersion
projectProfile
budget/effectiveBudget
usage
truncated
nextActions
```

This definition is intentionally strict. M4 may later improve ranking, but M1 must not invent a new relevance algorithm merely to decide which five entries are best.

M1 preserves current deterministic selection order and trims to the hard total. Any ranking/relevance redesign belongs to M4.

### 5.3 Character and token definitions

Two distinct measurements are required:

```text
contentChars:
  canonical serialized characters of nodes + activeWork + records only
  hard limit <= 2000

estimatedTokens:
  existing deterministic chars/4 estimate over the final structured context
  hard limit <= 800
```

This resolves the older specification wording that independently mentioned “<=2000 chars” and “<=800 tokens”.

The 2000-character limit controls recalled content; the 800-token limit controls the complete automatic context envelope. Both must pass.

### 5.4 Public-tool compatibility

M1 does **not** change `ue_memory_get_context` from an object response into a raw string.

The existing MCP envelope is part of the public contract and remains structured for diagnostics, budget metadata and continuation actions.

The historical requirement “no Memory -> empty string” is frozen here as an **injection-content semantic**, not a top-level MCP type change:

```text
no eligible recalled items -> candidate injectable Memory content = ""
```

The structured tool may still report diagnostics such as `memory-disabled`, effective budget, usage or project identity. Those diagnostics are not Memory text to be injected into a prompt.

Actual prompt injection does not exist until M5. M1 only makes the future injectable payload measurable and guarantees that an empty hit produces no placeholder prose such as:

```text
"No memory found"
"暂无记忆"
"No relevant project memory"
```

### 5.5 No-hit behavior

Automatic recall must distinguish useful recalled content from navigation metadata.

For a query/asset-based automatic recall with no eligible Memory/Active Work match:

```text
recalled item count = 0
contentChars        = 0
candidate injection = ""
```

An implicit `/project` root fallback must not be treated as a successful Memory hit for future injection.

Explicit navigation remains different:

```text
ue_memory_expand_node(path="/project")
```

may still return the project/root tree because the caller explicitly requested navigation.

### 5.6 Deadline behavior

The 300 ms recall limit is a real elapsed-time deadline measured with `time.perf_counter()` or `time.monotonic()`, not a post-hoc warning.

Implementation requirements:

```text
create deadline at automatic-recall entry
check deadline between deterministic selection stages
install a temporary SQLite progress handler while recall SQL executes
abort long SQL when the deadline expires
remove the progress handler in finally
retain already completed deterministic results
return truncated=true
record a deadline truncation reason
never convert unrelated SQLite failures into timeout success
```

If the deadline expires before any useful result is obtained, return an empty bounded recall plus explicit truncation metadata; do not wait for the original unbounded operation to finish.

### 5.7 Effective-budget metadata

The automatic context should expose enough data to prove the hard limits without duplicating recalled text.

Required usage/effective fields should cover at least:

```text
requested limits
effective hard limits
recalledItemCount
contentChars
usedChars
estimatedTokens
elapsedMs
truncated
truncationReasons[]
```

Exact JSON field placement may follow existing `budget` / `usage` structure, but there must be one canonical source for each number.

## 6. Internal design

### 6.1 Keep `ContextBudget`; add a separate recall budget

Preferred implementation:

```text
ContextBudget
  existing explicit/progressive structural bounds

RecallBudget
  hard ceilings for automatic recall
  max_items
  max_content_chars
  max_estimated_tokens
  deadline_ms
```

`RecallBudget` validation/normalization must enforce the frozen server ceilings centrally.

Do not duplicate magic numbers in `mcp_memory_tools.py`, `memory_service.py` and `task_context.py`.

### 6.2 Automatic and explicit read paths

Target routing:

```text
ue_memory_get_context
  -> ProjectMemoryService.get_context
  -> hard RecallBudget always active

ue_get_task_context Memory section
  -> ProjectMemoryService.get_context
  -> same hard RecallBudget

ue_memory_expand_node
  -> ProjectMemoryService.expand_node
  -> existing explicit ContextBudget
  -> no M1 5-item automatic-recall ceiling

ue_memory_get / ue_memory_get_evidence
  -> exact explicit reads
  -> unchanged
```

This preserves progressive exploration while preventing automatic context growth.

### 6.3 Partial-result assembly

`build_memory_context()` currently calculates most source collections before constructing the response envelope. To support a hard deadline, the implementation should be refactored so that:

```text
response envelope is initialized first
selection stages execute in deterministic order
each completed stage can be retained
deadline can stop later stages
final budget trimming always runs
```

The refactor is allowed only to support bounded execution. It must not change stale/superseded filtering, Revision semantics, evidence semantics or deterministic ordering.

## 7. Measurement harness

### 7.1 New script

Add:

```text
scripts/MeasureMemoryOverhead.py
```

Requirements:

```text
standard library only
no UE
no network
no external project
temporary deterministic SQLite fixtures
stable JSON schema/key ordering
machine/timing metadata without absolute user paths
non-zero exit in --gate mode when a hard gate fails
```

The timing values themselves are naturally non-deterministic; “deterministic report” means stable fixture semantics, field names, ordering and calculations.

### 7.2 Benchmark scenarios

At minimum measure:

#### B0 — Memory disabled first Tool

Construct the same bounded Task Context surface with `memory_service=None` and no Live Editor. Measure the first request on a fresh service instance.

#### B1 — Memory enabled, empty/no-hit

Use a fixed Memory database with no eligible hit for the task query. Measure the first request and verify zero recalled content.

#### B2 — Memory enabled, populated

Use a fixed deterministic fixture containing enough nodes/records/work to force the M1 item/content caps. Measure first request and direct recall.

#### B3 — direct automatic recall

Measure `ProjectMemoryService.get_context()` on the populated fixture and record:

```text
elapsed
item count
content chars
total estimated tokens
truncation state/reasons
```

#### B4 — task-end append

Measure one deterministic `record_task_outcome()` append with fixed evidence references and Revision data.

This is the current closest deterministic equivalent to the M2 task-end append path. M1 must not add automatic capture just to benchmark it.

### 7.3 Sampling and statistics

Use paired/fresh samples so the Memory ON/OFF delta is meaningful.

Recommended default:

```text
fresh first-call samples   >= 20 paired samples
task-end append samples    >= 20
direct recall samples      >= 20
report                     median / p95 / max
```

Fixture creation is performed before the timed operation unless the metric explicitly measures first DB open/read. The timed first Tool call must include the Memory database open/query performed by that Tool call.

### 7.4 Hard performance gates

M1 closure uses:

```text
p95 Memory-enabled first Tool incremental latency < 200 ms
p95 direct automatic recall latency                  < 300 ms
p95 task-end append latency                          < 100 ms
```

The direct runtime deadline remains 300 ms per automatic recall even if benchmark samples are much faster.

If a timing gate fails, M1 is blocked. Do not average away a systematic regression and proceed to M2.

### 7.5 Baseline and closure reports

Expected evidence files:

```text
benchmarks/memory/m1_memory_overhead_before_20260830.json
benchmarks/memory/m1_memory_overhead_after_20260830.json
```

The first report is produced immediately after the measurement harness exists but before M1 budget behavior is changed. It records current v3 behavior and is allowed to show contract failures.

The second report is produced at closure in `--gate` mode and must pass.

The report must explicitly state that **actual L2/L3 startup prompt injection is not implemented in M1**. Its startup token number is the candidate automatic Memory payload/envelope that M5 would be allowed to consume, not a claim that prompt injection already exists.

M5 must repeat actual injection measurement when prompt injection is implemented.

## 8. Test changes

### 8.1 Focused Memory-context tests

Extend `test_memory_context.py` to cover:

```text
public automatic recall <=5 total items
contentChars <=2000
estimatedTokens <=800
large requested bounds are clamped
smaller caller bounds remain respected
no-hit automatic recall has zero recalled items/content
explicit expand_node remains capable of deeper bounded reads
deadline truncation returns partial/empty success rather than a budget error
non-timeout SQLite errors remain errors
usage numbers match canonical serialization
```

### 8.2 MCP contract tests

Update/extend the Memory portions of `test_mcp_server.py` so tool-schema/default/effective limits remain explicit and unsupported hidden bypasses are not introduced.

### 8.3 Task Context tests

Extend `test_task_context.py` to prove:

```text
Task Context cannot allocate > M1 recall hard caps to Memory
include_memory=False does not perform automatic recall
memory_service=None remains a valid degraded mode
Memory diagnostics remain structured
no-hit Memory does not create placeholder recall content
```

### 8.4 Measurement-script tests

Add focused tests for report schema, percentile calculation, gate evaluation and stable no-path metadata.

Do not make ordinary unit tests depend on tight real-time thresholds. Wall-clock thresholds belong to `MeasureMemoryOverhead.py --gate`; unit tests prove deterministic contract logic.

## 9. Expected implementation files

Primary expected delta:

```text
scripts/MeasureMemoryOverhead.py                         new
src/ue_agent_kit/memory_context.py                      hard recall budget/deadline
src/ue_agent_kit/memory_service.py                      automatic recall routing
src/ue_agent_kit/mcp_memory_tools.py                    public effective defaults/metadata
src/ue_agent_kit/task_context.py                        automatic Memory cap integration
tests/python/test_memory_context.py                     contract coverage
tests/python/test_mcp_server.py                         MCP coverage as needed
tests/python/test_task_context.py                       Task Context coverage
tests/python/test_memory_overhead.py                    measurement/report logic if useful
benchmarks/memory/m1_memory_overhead_before_20260830.json
benchmarks/memory/m1_memory_overhead_after_20260830.json
docs/Plans/UEAGENTKIT_M1_MEMORY_EFFICIENCY_BASELINE_AND_BUDGET_RESULT_20260830.md
```

The exact test-file split may be adjusted to avoid duplication. No schema file, C++ file or version file should change in M1.

## 10. Implementation sequence

### M1-0 — Measurement harness and pre-change baseline

```text
implement MeasureMemoryOverhead.py
add focused report/gate tests
run current v3 baseline
freeze before JSON evidence
```

Do not change recall behavior before the baseline is captured.

### M1-1 — Central hard RecallBudget

```text
add hard-limit constants / RecallBudget normalization
add usage/effective-budget accounting
make caller requests able to tighten but not widen limits
```

### M1-2 — Item/content/token enforcement and no-hit semantics

```text
enforce <=5 recalled entries
enforce <=2000 recalled content chars
enforce <=800 estimated final context tokens
separate implicit navigation metadata from a real recall hit
preserve explicit expand_node behavior
```

### M1-3 — 300 ms deadline

```text
monotonic deadline
SQLite progress-handler cancellation
partial-result preservation
explicit deadline truncation reason
cleanup handler in finally
```

### M1-4 — Task Context integration

```text
route automatic Task Context Memory through same hard RecallBudget
prove include_memory=False / memory disabled paths remain cheap and valid
```

### M1-5 — Closure benchmark and G2

```text
run MeasureMemoryOverhead.py --gate
produce after report
run one G2 closure
write Result
```

No separate blocker document is created unless a real technical blocker emerges with a distinct investigation/exit gate.

## 11. Validation Budget

```text
Risk class: low-medium
UE level: U0
UE lease: not required
```

### G0 during implementation

Use after small edits:

```text
focused changed Memory tests only
Ruff on touched Python files
python scripts/RunPythonTests.py fast at meaningful checkpoints, not every edit
```

Do not run full regression repeatedly.

### G1 affected-domain checkpoint

At functional completion before closure:

```text
python scripts/RunPythonTests.py domain memory
python scripts/MeasureMemoryOverhead.py --gate ...
relevant MCP/Task Context focused tests if not already included
compileall only if import/module structure changed materially
```

Current reference cost before M1:

```text
memory domain   170 tests / ~18.6 s
```

### G2 stage closure — once

```text
python scripts/RunPythonTests.py full
python -m ruff check src tests/python scripts
python -m compileall -q src scripts tests/python
python scripts/ValidateRelease.py --skip-tests --skip-ruff
git diff --check
python scripts/MeasureMemoryOverhead.py --gate ...
```

Do not duplicate the full suite or Ruff through `ValidateRelease.py` after running them explicitly.

### Explicitly not run

```text
UnrealEditor / UnrealEditor-Cmd
UBT / Direct Build
W4 real-UE C1-C12 matrix
W5 scale fixtures
V2 5000-node heavy benchmark
Reforge mutation
```

## 12. Acceptance matrix

M1 closes only when all are true:

```text
A1  before baseline report exists and records current v3 behavior
A2  automatic recall hard total items <= 5
A3  recalled content chars <= 2000
A4  final automatic context estimated tokens <= 800
A5  automatic recall has a real 300 ms elapsed deadline
A6  deadline returns bounded partial/empty result with explicit truncation metadata
A7  caller cannot widen server hard limits
A8  explicit expand/evidence reads retain their separate progressive behavior
A9  no-hit candidate injectable Memory content is exactly empty / no placeholder prose
A10 Task Context automatic Memory cannot bypass M1 limits
A11 p95 first Tool Memory incremental latency < 200 ms
A12 p95 direct recall latency < 300 ms
A13 p95 task-end append latency < 100 ms
A14 Memory disabled path performs no automatic Memory write
A15 no new required runtime dependency; project dependencies remain []
A16 memory G1 PASS
A17 full G2 PASS once
A18 after benchmark report exists and all hard gates PASS
```

Any A2-A13 failure blocks M2. Do not waive an exceeded efficiency gate merely because functional tests pass.

## 13. Safety and compatibility invariants

M1 must not weaken:

```text
Memory source provenance
stale/superseded filtering
Revision binding semantics
evidence-on-demand
read-only classification of ue_memory_get_context
fixed project key
no arbitrary SQL/tool/shell input
Writer Policy / Revision / Dirty-package / confirmation / verification gates
Knowledge Web read-only guarantees
```

M1 also must not claim:

```text
actual automatic Memory accumulation          # M2
actual deterministic distillation             # M3
semantic/vector retrieval                     # M4
actual prompt injection                       # M5
```

## 14. Documentation and commit policy

Normal M1 documentation footprint:

```text
ONE Detailed Plan   (this file)
ONE final Result    (created only at closure)
```

The Result must include exact before/after benchmark numbers, G1/G2 evidence, any deferred optimization and the final Git checkpoint.

No local commit is implied by this Plan. Commit/push/rebase/tag/release actions still follow the repository workflow and owner authorization.

## 15. Expected duration

Given the current implementation and new test-tiering entry points, expected effective development effort is:

```text
measurement harness + baseline          0.25-0.5 day
hard budget/deadline implementation     0.25-0.75 day
tests + benchmark + G2 closure          0.25 day

total expected                          ~0.5-1.5 effective dev days
```

The main uncertainty is whether SQLite deadline interruption requires restructuring more of `build_memory_context()` than expected. That is the only likely reason for this stage to exceed the estimate.

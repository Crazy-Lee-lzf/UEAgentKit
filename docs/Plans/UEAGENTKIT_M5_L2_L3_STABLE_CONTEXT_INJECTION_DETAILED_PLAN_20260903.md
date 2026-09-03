# UEAgentKit M5 — L2/L3 Stable Context Injection — Detailed Plan

> Date: 2026-09-03
>
> Branch: `feature/memory-context`
>
> Product baseline: `212f5443bec2e0a4e496bfdf3e1f981f92cfc77a` (M4 COMPLETE)
>
> Planning HEAD: `1952cf1830d7d2323aa2711c60b550cc7d067fab`
>
> State: **READY FOR IMPLEMENTATION**
>
> Required UE level: **U0**
>
> Memory schema: **v5 -> v6 additive**
>
> Required runtime dependencies: **remain `[]`**
>
> LLM / remote inference: **forbidden**
>
> Automatic vector/model loading: **forbidden**

## 1. Goal

M5 turns the already-working Memory stack into a low-noise, low-latency task-start context source.

M1-M4 already provide:

```text
M1  hard recall/token/latency budgets
M2  deterministic L0 auto-capture
M3  deterministic L0 -> L1 facts/rules/issues/decisions
M4  explicit hybrid Memory Search (FTS5 + optional local vector + RRF)
```

M5 adds only the missing automatic-context layer:

```text
L3  compact project-level conventions        stable, always eligible
L2  compact task-domain recipes              only when deterministically matched
L1  atomic facts/issues/rules/decisions      explicit tool lookup only
L0  raw durable evidence                     explicit tool lookup only
```

The primary product requirement is not “recall more”. It is:

> start a task with a very small, stable, relevant project context without recreating the old Memory behavior where every task start/finish consumed large time and Token budgets.

## 2. Frozen non-goals

M5 does **not** implement:

```text
M6 symbolic/Mermaid compression
P4 / Track C
C++ or UE changes
LLM summarization
remote embedding APIs
background Memory daemon
startup-time distillation
request-path distillation
request-path embedding backfill
automatic vector/model loading for Task Context
generic prompt-history summarization
new Writer operations
```

M6 remains optional and data-driven after M5.

## 3. Repository-grounded baseline

At M4 closure:

```text
Memory schema                         v5
required dependencies                 []
explicit Memory Search                hybrid when local vector stack is valid
automatic Task Context                FTS-only
M4 hybrid semantic Recall@5           0.90
M4 aggregate Recall@5                 0.95
M4 aggregate MRR                      0.8292
M4 hybrid p95                         1.388 ms
M3 100-event distillation             332.807 ms / <5000 ms PASS
Memory G1                             266 / 266 PASS
portable G2                           949 / 949 PASS / 96.774 s
Ruff / compileall                     PASS
ValidateRelease 0.7.0                 PASS
UE/UBT                                0
```

Persistent M1/M2 gates remain non-negotiable:

```text
automatic recall                     <= 5 items
automatic recalled content           <= 2000 chars
automatic final envelope              <= 800 estimated tokens
real recall deadline                 <= 300 ms
first Tool Memory incremental p95     < 200 ms
direct automatic recall p95           < 300 ms
task-end append p95                    < 100 ms
four-event L0 capture p95              < 100 ms
exact duplicate replay new rows             0
```

## 4. Architectural correction from the historical M5 sketch

The historical Midterm wording implied that `ue_memory_get_context` itself should become L2/L3-only. The current repository now has a mature explicit context browser and multiple callers relying on its structured node/record/work payload.

M5 therefore freezes this safer boundary:

```text
ue_memory_get_context
  -> remains explicit, bounded, backward-compatible
  -> may continue exposing L1 records/nodes when the Agent asks for them

ue_get_task_context automatic Memory injection
  -> changes to L3 + matched L2 only
  -> does not automatically include L1 record bodies or L0 evidence
  -> may expose compact evidence availability/status metadata only
```

This preserves explicit investigation capability while making automatic prompt injection small and stable.

## 5. Core performance rule: precompute, never derive on request

M5 must never build L2/L3 from L1 records during a task request.

Required shape:

```text
explicit/offline build
    memory_records + fixed index facts
                |
                v
        deterministic L2/L3 snapshot
                |
                v
        ordinary SQLite persisted rows

Task Context request
                |
                v
        read current snapshot only
                |
                +-- valid -> inject bounded text
                +-- stale/missing -> inject empty text
```

No synchronous fallback reconstruction is allowed.

If the snapshot is stale, returning no automatic Memory is preferred over blocking the task.

## 6. Schema v6

M5 uses an additive v5 -> v6 migration with ordinary SQLite tables only.

Required shape:

```sql
CREATE TABLE memory_context_state (
    project_key          TEXT PRIMARY KEY,
    source_generation    INTEGER NOT NULL DEFAULT 0,
    built_generation     INTEGER NOT NULL DEFAULT -1,
    snapshot_id          TEXT NOT NULL DEFAULT '',
    index_snapshot_id    TEXT NOT NULL DEFAULT '',
    source_digest        TEXT NOT NULL DEFAULT '',
    built_at_utc         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE memory_context_entries (
    entry_id             TEXT PRIMARY KEY,
    project_key          TEXT NOT NULL,
    layer                TEXT NOT NULL CHECK (layer IN ('L2', 'L3')),
    context_key          TEXT NOT NULL,
    ordinal              INTEGER NOT NULL CHECK (ordinal >= 0),
    title                TEXT NOT NULL,
    body                 TEXT NOT NULL,
    match_json           TEXT NOT NULL DEFAULT '{}',
    source_bindings_json TEXT NOT NULL DEFAULT '[]',
    content_sha256       TEXT NOT NULL,
    UNIQUE(project_key, layer, context_key)
);

CREATE INDEX memory_context_entries_project_layer_idx
    ON memory_context_entries(project_key, layer, ordinal, context_key);
```

### 6.1 Generation invalidation

Any `memory_records` INSERT / UPDATE / DELETE for a project must increment that project's `source_generation` through deterministic SQLite triggers.

M5 automatic injection is usable only when:

```text
built_generation == source_generation
AND snapshot_id != ''
AND stored index_snapshot_id == current fixed-index snapshot id
```

Otherwise:

```text
injection text = ''
reason = context-snapshot-stale | context-snapshot-missing | index-snapshot-mismatch
```

Do not rebuild synchronously.

### 6.2 Why a generation counter is required

Without a generation counter, Task Context would have to rescan/hash L1 records on every request to decide whether the persisted L2/L3 snapshot is current. That directly conflicts with the M1 latency/KV-cache objective.

A single SQLite row comparison is the desired request-path cost.

## 7. Deterministic identity and atomic rebuild

Entry IDs:

```text
ctx_<first 32 lowercase hex of SHA-256(canonical(projectKey, layer, contextKey))>
```

Snapshot ID:

```text
ctxsnap_<first 32 lowercase hex of SHA-256(canonical(
    projectKey,
    sourceGeneration,
    indexSnapshotId,
    ordered entry content digests
))>
```

A rebuild is transactional:

```text
compute complete draft in memory
BEGIN IMMEDIATE
  replace only this project's memory_context_entries
  write snapshot metadata
  set built_generation = current source_generation
COMMIT
```

If generation changes while the draft is being computed, fail/retry boundedly rather than publishing a snapshot built from mixed generations.

If the process dies before commit, the previous snapshot remains intact.

## 8. Explicit build command

Add the canonical explicit command:

```text
ue-agent memory build-context
```

Required inputs:

```text
--memory-database
--project-key
--index-database
```

Optional bounded knobs may include only deterministic limits such as max L2 groups / max L3 entries. Defaults must be product constants.

Output reports at least:

```text
projectKey
sourceGeneration
builtGeneration
snapshotId
indexSnapshotId
l2Entries
l3Entries
contentChars
estimatedTokens
reused
elapsedMs
```

No LLM, vector model, network, UE, or P4 access is allowed.

`memory distill` may later gain an **opt-in** `--refresh-context` chain, but M5 must not silently change M3's existing default behavior. The standalone build command is authoritative.

## 9. L2 — deterministic task-domain recipes

L2 is not free-form summarization. It is a compact evidence-derived recipe template.

### 9.1 Eligible source records

Use only:

```text
status        valid
sourceKind    user-confirmed | tool-observed
```

Exclude:

```text
unverified
conflicted
stale
superseded
model-inferred
```

Verified-write L1 facts should be identified from M3's deterministic distillation metadata, not title/body heuristics.

### 9.2 Grouping key

Initial L2 grouping key:

```text
(operation, assetClass)
```

`operation` comes from deterministic M3 record details.

`assetClass` is resolved from the fixed immutable index for the exact asset path bound to the source record.

A recipe is eligible only when the group has at least:

```text
3 distinct verified successful source records/events
```

### 9.3 Recipe contents

The recipe body must be deterministic and evidence-bounded. It may contain:

```text
operation
asset class
observed verified success count
stable target kind when present in every/most source records
common durable rejection reason codes for the same operation/class
required proven preconditions already present in durable evidence
```

It must not invent causality or “best practice” language that is not present in sources.

A typical deterministic body may look like:

```text
Blueprint setVariableDefault: 6 verified writes; target=variable-default; common rejection=live-editor-write-package-dirty (2).
```

This is intentionally compact rather than prose-heavy.

### 9.4 L2 body bound

```text
single L2 body <= 200 characters
max injected L2 entries = 2
injected L2 aggregate <= 400 estimated tokens
```

## 10. L3 — stable project conventions

L3 is project-wide and is the most cache-sensitive layer.

Priority order for L3 entries:

```text
1. valid user-confirmed projectRule records
2. valid tool-observed projectRule records with deterministic provenance
3. strong naming conventions proven from fixed index statistics
4. high-frequency knownIssue patterns meeting the frozen threshold
```

No model-inferred record may enter L3 automatic injection.

### 10.1 User/project rules

Use compact deterministic renderings of eligible `projectRule` records. Keep provenance in `source_bindings_json`; do not inject evidence paths/digests into the prompt text.

### 10.2 Naming conventions

Naming convention mining must be conservative.

Initial rule:

```text
group by asset class
extract prefix before first '_'
minimum sample count          5
minimum dominant share        80%
max emitted conventions       3
stable rank                   share DESC, count DESC, assetClass ASC, prefix ASC
```

Example:

```text
Blueprint assets: BP_ prefix (93%, n=214).
```

Anything below the threshold is not a convention and must not be injected.

### 10.3 High-frequency known issues

Only group durable valid `knownIssue` records using deterministic reason/subject keys.

Initial threshold:

```text
count >= 3
max emitted issue patterns = 3
```

Rank by count DESC then stable key ASC.

### 10.4 L3 bound

```text
L3 aggregate <= 400 estimated tokens
```

When over budget, deterministic priority/truncation must remove lower-priority entries; never truncate text in the middle of a semantic entry if dropping a whole lower-priority entry is possible.

## 11. Automatic L2 matching

M5 automatic injection must remain model-free and vector-free.

The Task Context caller already has:

```text
natural-language task query
explicit target asset paths
fixed-index target asset class metadata
```

L2 entries store deterministic `match_json`, expected fields:

```json
{
  "operation": "setVariableDefault",
  "assetClass": "Blueprint",
  "tokens": ["variable", "default", "blueprint"]
}
```

Matching uses only normalized token overlap plus exact asset-class evidence.

Frozen ranking direction:

```text
exact target asset-class match first
token overlap DESC
support count DESC
contextKey ASC
```

Require positive task-token evidence; asset-class match alone must not inject an unrelated recipe into every task touching that asset class.

Return at most two L2 entries.

No query embedding is generated.

## 12. Stable injection payload

Add a narrow service method, expected shape:

```text
ProjectMemoryService.get_injection_context(...)
```

Inputs:

```text
query
target asset classes/current index snapshot id
```

Output should separate prompt content from diagnostics:

```json
{
  "available": true,
  "snapshotId": "ctxsnap_...",
  "injectionHash": "sha256:...",
  "text": "...",
  "l3Count": 3,
  "l2Count": 1,
  "contentChars": 712,
  "estimatedTokens": 178,
  "stale": false,
  "reason": ""
}
```

The `text` field is the only field intended for automatic prompt injection.

It must contain no:

```text
current timestamps
elapsed time
filesystem paths
artifact paths
random IDs
unstable row ordering
vector provider/model state
```

For unchanged sources + same query/assets, `text` and `injectionHash` must be byte-identical across calls and process restarts.

## 13. Total injection budget

Frozen M5 automatic Memory injection budget:

```text
L3 <= 400 estimated tokens
L2 <= 400 estimated tokens
combined L2 + L3 <= 800 estimated tokens
```

This does not permit weakening the existing M1 automatic-memory envelope. The stricter effective bound wins.

If the combined result exceeds any M1 bound, drop whole L2 entries first, then lower-priority L3 entries.

## 14. Cold/no-memory behavior

If any of these is true:

```text
no Memory database
no L2/L3 snapshot
snapshot stale
index snapshot mismatch
no eligible L3 and no L2 match
```

then automatic injection content must be:

```text
text = ''
contentChars = 0
```

Do not inject strings such as:

```text
"No project memory available"
"No relevant memory found"
```

The structured Task Context wrapper may still report diagnostic reason fields, but the prompt-injected Memory text itself is empty.

## 15. Task Context integration

`TaskContextService` currently calls `ProjectMemoryService.get_context(detail_level=2)` and returns L1 record/node bodies in its automatic Memory summary.

M5 changes that automatic path to the new persisted injection snapshot.

Desired Task Context Memory section:

```text
available/included
staleRecordCount
injectionText
injectionHash
snapshotId
l2Count/l3Count
contentChars/estimatedTokens
reason if unavailable/stale
explicitSearchAvailable=true
```

L1/L0 body content is no longer automatically included.

The existing explicit tools remain available for progressive investigation:

```text
ue_memory_search
ue_memory_get_context
ue_memory_expand_node
ue_memory_get
ue_memory_get_evidence
ue_memory_list_l0
```

### 15.1 Preserve risk/correlation semantics

Current Task Context uses Memory record metadata for stale/conflicted risk and cross-source correlation.

M5 must preserve those behaviors without exposing L1 bodies automatically.

Allowed implementation approaches:

```text
narrow internal metadata query
compact non-body evidence refs
separate internal return value not serialized into automatic prompt content
```

Do not keep L1 body payload in the public Task Context merely because an internal risk check needs record IDs/status.

## 16. Backward compatibility

M5 must preserve:

```text
ue_memory_get_context explicit response contract
ue_memory_search explicit hybrid behavior and M4 provenance metadata
M3 deterministic L1 IDs/content/evidence semantics
M2 L0 event semantics
active work CRUD behavior
Knowledge Web read-only behavior
Writer behavior
```

The only intentional behavioral change is what Memory content is automatically included by Task Context.

## 17. M5 benchmark

Add a deterministic benchmark, expected:

```text
scripts/MeasureMemoryInjection.py
benchmarks/memory/m5_memory_injection_<date>.json
```

Required scenarios:

```text
B0 memory disabled                    injected text empty
B1 empty/no snapshot                  injected text empty
B2 valid L3-only snapshot             stable bounded text
B3 matching L2 task                   <=2 L2 + L3
B4 unrelated task                     no unrelated L2
B5 repeated identical request         byte-identical text/hash
B6 source record changes              snapshot becomes stale immediately
B7 stale snapshot request             empty text, no synchronous rebuild
B8 explicit rebuild                   new valid deterministic snapshot
B9 rebuild with unchanged sources     identical semantic content/hash
B10 index snapshot changes            old snapshot not injected
```

Measure at least 20 samples for request-path latency.

M5-specific performance acceptance:

```text
get_injection_context p95 < 100 ms
```

Persistent M1 first-Tool and recall gates remain the authoritative user-visible latency gates.

## 18. M5 execution slices

### M5-0 — contract/baseline freeze

```text
confirm M4 checkpoint and clean tree
freeze v6 schema
freeze L2/L3 deterministic extraction templates
freeze benchmark fixtures
capture M4/M1-M3 regression baseline references
```

### M5-1 — schema v6 + snapshot generation

```text
memory_context_state
memory_context_entries
memory_records generation triggers
deterministic entry/snapshot IDs
atomic rebuild state machine
migration/idempotence tests
```

### M5-2 — deterministic L2/L3 builder

```text
eligible L1 filtering
verified-write grouping
index asset-class binding
L2 evidence templates
L3 projectRule/naming/knownIssue extraction
source binding/content digests
budget enforcement
```

### M5-3 — explicit build command

```text
ue-agent memory build-context
bounded JSON result
restart/atomic behavior
no LLM/vector/network
```

### M5-4 — automatic injection + Task Context integration

```text
get_injection_context
L2 deterministic matching
stable text/hash
Task Context automatic L1-body removal
preserve internal risk/correlation behavior
cold/stale empty injection
```

### M5-5 — performance/closure

```text
M5 injection benchmark
M1/M2 regression benchmark
M3 distillation regression
M4 explicit hybrid regression
Memory G1 once
portable G2 once
Result document
```

## 19. Expected touched files

Expected primary surface:

```text
src/ue_agent_kit/memory_schema.py
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/memory_context.py            only if shared budget helpers are needed
src/ue_agent_kit/memory_injection.py          new, preferred
src/ue_agent_kit/task_context.py
src/ue_agent_kit/cli.py
src/ue_agent_kit/mcp_memory_tools.py          only if status metadata is surfaced
scripts/RunPythonTests.py                     only if new test module/domain registration needed
scripts/MeasureMemoryInjection.py             new
tests/python/test_memory_injection.py         new
tests/python/test_project_memory.py
tests/python/test_memory_service.py
tests/python/test_memory_context.py
tests/python/test_task_context.py
benchmarks/memory/
```

Broad unrelated refactors are out of scope.

No C++/Plugin/UE/P4 files belong in M5.

## 20. Validation Budget

Required UE level: **U0**.

During edits:

```text
focused tests only
focused Ruff when useful
no repeated full suite
```

Meaningful G0:

```text
py -3.12 scripts/RunPythonTests.py fast
```

G1 after final functional source state:

```text
py -3.12 scripts/RunPythonTests.py domain memory
```

Run Memory G1 once.

Stage-specific G1 evidence:

```text
M5 injection benchmark
M1/M2 regression benchmark
M3 100-event distillation benchmark
M4 frozen hybrid-quality benchmark
```

Use the repo `.venv` only when an optional-vector focused check is actually required; automatic M5 injection must not depend on it.

G2 once at closure:

```text
py -3.12 scripts/RunPythonTests.py full
.venv\Scripts\python.exe -m ruff check src tests/python scripts
py -3.12 -m compileall src tests/python scripts
py -3.12 scripts/ValidateRelease.py --expected-version 0.7.0 --skip-tests --skip-ruff
git diff --check
```

Do not run UE/UBT.

## 21. Acceptance matrix

M5 is complete only if all items are proven:

```text
A1  Memory schema upgrades v5 -> v6 additively                         PASS
A2  required dependencies remains []                                   PASS
A3  no LLM / remote inference / network in build or injection path      PASS
A4  no vector/model load in automatic injection path                    PASS
A5  L2/L3 are precomputed; request path never distills                  PASS
A6  memory-record change invalidates snapshot via generation            PASS
A7  index snapshot mismatch prevents stale injection                    PASS
A8  snapshot rebuild is atomic/restart-safe                              PASS
A9  same sources produce deterministic entry/snapshot identity           PASS
A10 L2 requires >=3 verified successes per operation+assetClass         PASS
A11 L2 body <=200 chars; at most 2 automatically injected               PASS
A12 L2 task matching is deterministic and model-free                     PASS
A13 L3 only uses eligible valid deterministic/user-confirmed sources     PASS
A14 naming convention threshold is >=5 samples and >=80% dominance       PASS
A15 L3 <=400 tokens, L2 <=400, combined <=800                            PASS
A16 no-hit/stale automatic injection text is exactly empty               PASS
A17 unchanged snapshot/request yields byte-identical text/hash           PASS
A18 automatic Task Context no longer injects L1/L0 bodies                PASS
A19 explicit ue_memory_get_context remains backward-compatible           PASS
A20 explicit M4 hybrid ue_memory_search remains functional               PASS
A21 Task Context stale/conflict/correlation semantics remain truthful    PASS
A22 get_injection_context p95 <100 ms                                    PASS
A23 all persistent M1/M2 gates remain PASS                               PASS
A24 M3 100-event distillation <5 s remains PASS                          PASS
A25 M4 frozen semantic/lexical quality acceptance remains PASS           PASS
A26 Memory G1 PASS                                                       PASS
A27 portable G2 PASS                                                     PASS
A28 UE/UBT runs = 0                                                      PASS
```

## 22. Stop / owner-decision conditions

Stop before weakening scope if implementation evidence shows any of:

```text
precomputed L2/L3 cannot be kept under existing M1 budget
Task Context requires model/vector loading to select L2
snapshot freshness requires hashing/scanning the whole Memory DB per request
preserving Task Context correctness would require automatically injecting L1 bodies
v6 migration would be destructive
M5 requires adding a required dependency
M1 first-Tool/recall budgets regress beyond their hard thresholds
```

Do not relax M1-M4 gates to make M5 green.

## 23. Post-M5 project direction

When M5 closes successfully:

```text
M1-M5   required Track M usability path COMPLETE
M6      optional / data-driven, do not auto-start
```

The next recommended project step is not broad Track X expansion.

Based on the owner decision recorded in:

```text
docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md
```

move to the minimal P4 dogfood safety/assistance path:

```text
C1 Source Control Awareness
C2 Source Control Advisory + checkout/local-write assistance
then real-project write-enabled dogfood
```

P4 collaboration state is advisory rather than a Writer hard-block policy. Agent-executed `submit`, `revert`, and P4-managed delete remain permanently human-only; resolve is allowed under bounded analysis/verification.

## 24. Git / delivery boundary

M5 implementation may modify only the M5 working tree after verifying live Git state.

Do not push, rebase, tag, release, publish artifacts, or change published version without separate authorization.

At completion produce exactly one normal Result:

```text
docs/Plans/UEAGENTKIT_M5_L2_L3_STABLE_CONTEXT_INJECTION_RESULT_<date>.md
```

Report:

```text
v6 schema and generation semantics
L2/L3 deterministic extraction rules actually implemented
snapshot atomicity/freshness evidence
Task Context before/after automatic Memory payload shape
injection token/character counts
byte-stability/hash evidence
M5 injection p50/p95
M1/M2/M3/M4 regression evidence
G0/G1/G2 counts/times
UE runs (expected 0)
any deviations/blocked item
```

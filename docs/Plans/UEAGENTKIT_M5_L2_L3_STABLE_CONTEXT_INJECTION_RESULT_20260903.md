# UEAgentKit M5 — L2/L3 Stable Context Injection — Result

> Closure date: 2026-09-03
>
> Branch: `feature/memory-context`
>
> Planning HEAD: `fac7b6cca2a4f628062c8ff72beadd9ba6513b10`
>
> Product baseline: `212f5443bec2e0a4e496bfdf3e1f981f92cfc77a` (M4 COMPLETE)
>
> Stage state: **IMPLEMENTED / G2 PASS / U0**
>
> Commit / push / rebase / tag / release / version change: **not performed**

## 1. Closure summary

M5 adds the persisted deterministic L2/L3 automatic-context layer. Automatic
Task Context Memory now reads **only** persisted L3 (project conventions/rules)
plus **deterministically matched** persisted L2 recipes. L1/L0 bodies are no
longer automatically included; explicit Memory tools keep their M1/M4 L1 access
semantics unchanged.

```text
M1-M4 explicit/budget behavior      preserved (regression gates PASS)
Memory schema                       v5 -> v6 additive
required runtime dependencies        [] (unchanged)
LLM / remote inference               none
vector/model load on request path    none (request path is FTS/model-free)
L2/L3 build                          explicit offline command only
stale/missing snapshot               empty injection, never rebuilt synchronously
UE/UBT runs                          0 (U0)
```

## 2. Verified repository facts (before modification)

```text
branch                        feature/memory-context
HEAD                          fac7b6cca2a4f628062c8ff72beadd9ba6513b10  (planning HEAD matches)
worktree                      E:\WorkSpace\UEAgentKit-Integration
working tree                  clean before M5 edits
M5 baseline 212f544           ancestor of HEAD (M4 implementation closed)
```

## 3. Schema v6 (additive)

New ordinary SQLite objects only; no column/table changes to any pre-existing
object, no destructive migration.

```text
memory_context_state
  project_key TEXT PRIMARY KEY
  source_generation INTEGER NOT NULL DEFAULT 0
  built_generation  INTEGER NOT NULL DEFAULT -1
  snapshot_id       TEXT NOT NULL DEFAULT ''
  index_snapshot_id TEXT NOT NULL DEFAULT ''
  source_digest     TEXT NOT NULL DEFAULT ''
  built_at_utc      TEXT NOT NULL DEFAULT ''

memory_context_entries
  entry_id             TEXT PRIMARY KEY          (ctx_<32 hex sha256 canonical key>)
  project_key, layer CHECK IN ('L2','L3'), context_key,
  ordinal, title, body, match_json, source_bindings_json, content_sha256
  UNIQUE(project_key, layer, context_key)

CREATE INDEX memory_context_entries_project_layer_idx
  ON memory_context_entries(project_key, layer, ordinal, context_key)

memory_records generation triggers
  memory_records_ctx_gen_ai / _ad / _au
  -> UPDATE memory_context_state SET source_generation = source_generation + 1
     WHERE project_key = (inserted/deleted/old or new record project)
```

A record INSERT/UPDATE/DELETE therefore invalidates the snapshot through a
single deterministic counter update; freshness is one SQLite row comparison at
request time (no L1 rescan/hash, no rebuild).

## 4. L2 / L3 deterministic extraction rules implemented

### 4.1 Eligible L1 sources

```text
status          valid only
sourceKind      user-confirmed | tool-observed only (model-inferred excluded)
```

### 4.2 L2 — task-domain recipes

```text
grouping key        (operation, assetClass)
operation           deterministic details: details.distillation.operation else details.operation
assetClass          fixed immutable index class of the record's exact /Game asset scope
                    (fallback: distillation primaryAssetPath), unresolved paths skipped
verified success
  user-confirmed    projectFact/decisionRecord/taskRecord with deterministic operation
  tool-observed     requires M3 deterministic distillation ruleId in
                    {l1.verified-write.v1, l1.supersession.v1, l1.semantic-diff.v1}
                    and a deterministic operation
threshold           >= 3 distinct verified successful source records per group
recipe body         "Blueprints setVariableDefault: N verified writes; target=...;
                     common rejection=code (m)."  (<= 200 chars, whole optional parts dropped)
rejection codes     valid knownIssue records for the same (operation, assetClass)
                    with deterministic errorCode, count >= 2, at most 2 codes
stable target kind  deterministic stableTargetKey present in a strict majority
match_json          {operation, assetClass, support, tokens}
build bound         MAX_L2_GROUPS = 8 (CLI knob clamped to hard max)
```

### 4.3 L3 — stable project context

```text
priority 1   valid user-confirmed projectRule records
priority 2   valid tool-observed projectRule records with deterministic provenance
priority 3   naming conventions mined from fixed immutable index:
             prefix before first '_', >= 5 samples, >= 80% dominance,
             max 3 conventions, rank share DESC, count DESC, class ASC, prefix ASC
priority 4   high-frequency knownIssue patterns: deterministic reason/subject key,
             >= 3 occurrences, max 3 patterns
rendering    single-line deterministic text; provenance kept in source_bindings_json
             only (no evidence paths/digests in prompt text)
budget trim  whole-entry drop in priority order until L3 <= 400 estimated tokens
build bound  MAX_L3_ENTRIES = 48 (CLI knob clamped to hard max)
```

## 5. Snapshot atomicity / identity / freshness

```text
entry id      ctx_<first 32 hex of SHA-256(canonical(projectKey, layer, contextKey))>
snapshot id   ctxsnap_<first 32 hex of SHA-256(canonical(
              projectKey, sourceGeneration, indexSnapshotId,
              ordered (layer, ordinal, contentSha256) entries))>
content sha   SHA-256 of canonical(projectKey, layer, contextKey, title, body, match)

rebuild       compute full draft under BEGIN IMMEDIATE; delete + reinsert this
              project's entries and advance memory_context_state in the same
              transaction; crash before COMMIT leaves the previous snapshot intact
index guard   builder requires the fixed immutable index project_key to match the
              memory project (when present) and stores index_snapshot_id derived
              from the index file stat/schema/metadata (byte-identical derivation
              to the request-time index snapshot id)

freshness     usable iff built_generation == source_generation
              AND snapshot_id != '' AND stored index_snapshot_id == current index id
              otherwise: reason context-snapshot-missing | context-snapshot-stale |
              index-snapshot-mismatch with empty injection text
```

## 6. Automatic Task Context behavior

Before M5 the automatic Memory section embedded `nodes`/`records` (L1-derived
summaries) plus active work from `memory_service.get_context(detail_level=2)`.

After M5 `ue_get_task_context` automatic Memory section is:

```json
{
  "available": true,
  "included": true,
  "source": "project-memory",
  "explicitSearchAvailable": true,
  "staleRecordCount": 0,
  "summary": {
    "available": true,
    "stale": false,
    "reason": "",
    "snapshotId": "ctxsnap_...",
    "injectionHash": "sha256:...",
    "injectionText": "L3: ...\nL2: ...",
    "l3Count": 4,
    "l2Count": 1,
    "contentChars": 430,
    "estimatedTokens": 108
  }
}
```

Request-path costs are one `memory_context_state` row read plus bounded entry
reads plus token-overlap L2 matching. No record scan, no model load, no
embedding, no distillation, no synchronous rebuild. Stale/missing snapshots
return `injectionText == ''` with a stable reason and are never rebuilt.

Risk/correlation semantics stay truthful through narrow FTS-only internal
metadata queries (`search_records_fts`) for conflicted/stale/evidence lookups,
so the automatic path cannot load the optional vector model even when the
vector extra is installed. Active work remains a separate bounded section and
feeds the existing correlation/risk logic unchanged.

## 7. Frozen automatic injection budgets

```text
L3                          <= 400 estimated tokens
L2                          <= 400 estimated tokens
L2 automatic recipes        <= 2
single compact L2 recipe    <= 200 chars
combined L2 + L3            <= 800 estimated tokens
(request trim order: drop whole L2 recipes first, then whole lower-priority L3)
M1 automatic-memory envelope unchanged (strictly smaller effective bound wins)
```

The injection `text` contains no timestamps, elapsed times, filesystem paths,
artifact paths, random ids, unstable row ordering, or vector state, and is
byte-identical for unchanged sources and identical requests (verified by hash).

## 8. Explicit build command

```text
ue-agent memory build-context --memory-database ... --project-key ... --index-database ...
                             [--max-l2-groups 8] [--max-l3-entries 48]
```

Deterministic output:

```json
{
  "projectKey": "...", "sourceGeneration": 1, "builtGeneration": 1,
  "snapshotId": "ctxsnap_...", "indexSnapshotId": "sha256:...",
  "l2Entries": 1, "l3Entries": 4, "contentChars": 430,
  "estimatedTokens": 108, "reused": false, "elapsedMs": 12.5, "reason": ""
}
```

Rebuilding with unchanged sources is detected byte-equivalently and reported as
`reused: true` with an identical `snapshotId`. `memory distill` default behavior
was not changed.

## 9. M5 benchmark evidence

`scripts/MeasureMemoryInjection.py` ->
`benchmarks/memory/m5_memory_injection_20260903.json` (U0, model-free,
stdlib-only).

```text
B0  memory disabled                       empty text                 PASS
B1  empty / no snapshot                   empty text, missing        PASS
B2  valid L3-only snapshot                4 entries, 314 chars, 79 est tokens PASS
B3  matching L2 task                      1 L2 recipe injected       PASS
B4  unrelated task                        no L2 injected             PASS
B5  repeated identical request            byte-identical text/hash   PASS
B6  source record change                  stale immediately          PASS
B7  stale snapshot request                empty text, no rebuild     PASS
B8  explicit rebuild                      new valid snapshot         PASS
B9  rebuild unchanged sources             identical id, reused=True  PASS
B10 index snapshot change                 old snapshot not injected  PASS
request-path latency                      n=25  p50 5.212 ms  p95 5.748 ms (< 100 ms)
```

## 10. M1/M2/M3/M4 regression evidence

`benchmarks/memory/m5_m12_regression_20260903.json` (M1/M2 gate):

```text
first Tool Memory incremental p95     19.354 ms   < 200 ms   PASS
direct automatic recall p95           15.493 ms   < 300 ms   PASS
task-end append p95                   14.466 ms   < 100 ms   PASS
four-event L0 capture p95             16.016 ms   < 100 ms   PASS
exact duplicate replay new rows             0                PASS
automatic recall                       748 est tokens <= 800   PASS
no-hit recall                          0 items / 0 chars       PASS
```

`benchmarks/memory/m5_m3_distillation_20260903.json` (M3 gate):

```text
100-event deterministic distillation   265.432 ms  < 5000 ms  PASS
produced / failed / deferred           70 / 0 / 0
```

`benchmarks/memory/m5_m4_hybrid_recall_20260903.json` (M4 frozen hybrid gate,
repo .venv + explicit local model dir):

```text
semantic Recall@5                      0.9
aggregate Recall@5                     0.95
aggregate MRR                          0.8292
hybrid p95                             1.637 ms  < 300 ms  PASS
query embeddings/query                 1  (exactly)
corpus embeddings/query                0  (exactly)
lexical top-5 safety gate              PASS
```

## 11. Validation evidence

```text
Focused new module tests               17 / 17 PASS
Focused task-context module            45 / 45 PASS
Memory G1 (domain memory, once)        285 tests / 31.365 s PASS  (17 skipped)
Portable G2 full suite (once)          968 tests / 99.344 s PASS  (17 skipped)
Ruff (repo)                            PASS
compileall (src tests scripts)         PASS
ValidateRelease 0.7.0                  PASS  (--skip-tests --skip-ruff; Schemas 3, Patch examples 16)
git diff --check                       PASS
UE/UBT runs                            0
```

## 12. Acceptance matrix

```text
A1  schema v5 -> v6 additive                        PASS
A2  required dependencies []                        PASS
A3  no LLM/remote inference/network                 PASS
A4  no vector/model load in automatic path          PASS
A5  L2/L3 precomputed; request never distills       PASS
A6  record change invalidates via generation        PASS
A7  index snapshot mismatch blocks injection        PASS
A8  atomic/restart-safe rebuild                     PASS
A9  deterministic entry/snapshot identity           PASS
A10 L2 >= 3 verified successes per op+class         PASS
A11 L2 body <=200 chars; <=2 injected               PASS
A12 deterministic model-free L2 matching            PASS
A13 L3 valid deterministic/user-confirmed only      PASS
A14 naming threshold >=5 samples and >=80%          PASS
A15 L3<=400 / L2<=400 / combined<=800               PASS
A16 no-hit/stale injection text exactly empty       PASS
A17 byte-identical text/hash for unchanged input    PASS
A18 automatic Task Context has no L1/L0 bodies      PASS
A19 explicit ue_memory_get_context backward-compatible PASS
A20 explicit M4 hybrid ue_memory_search functional  PASS
A21 stale/conflict/correlation semantics truthful   PASS
A22 get_injection_context p95 < 100 ms              PASS (5.748 ms)
A23 persistent M1/M2 gates PASS                     PASS
A24 M3 100-event distillation < 5 s                 PASS (265.432 ms)
A25 M4 frozen quality acceptance PASS               PASS
A26 Memory G1 PASS                                  PASS
A27 portable G2 PASS                                PASS
A28 UE/UBT runs = 0                                 PASS
```

## 13. Changed files

Modified:

```text
src/ue_agent_kit/memory_schema.py         v6 migration (state/entries/index/triggers)
src/ue_agent_kit/memory_service.py        get_injection_context, build_context,
                                          search_records_fts service methods
src/ue_agent_kit/task_context.py          automatic Memory section -> L3/L2 injection;
                                          risk/correlation via FTS-only metadata;
                                          schema version 1.2 -> 1.3
src/ue_agent_kit/cli.py                   memory build-context subcommand
scripts/RunPythonTests.py                 memory domain adds test_memory_injection
tests/python/test_task_context.py         M5 injection shape assertions + caps tests
tests/python/test_memory_cli.py           build-context CLI tests
tests/python/test_memory_vector.py        schema-current assertions v5 -> v6
tests/python/test_memory_tree.py          schema-current assertions v5 -> v6
```

New:

```text
src/ue_agent_kit/memory_injection.py       L2/L3 builder, snapshot freshness,
                                           deterministic matching, injection payload
tests/python/test_memory_injection.py     17 functional tests
scripts/MeasureMemoryInjection.py          M5 B0-B10 + latency benchmark
benchmarks/memory/m5_memory_injection_20260903.json
benchmarks/memory/m5_m12_regression_20260903.json
benchmarks/memory/m5_m3_distillation_20260903.json
benchmarks/memory/m5_m4_hybrid_recall_20260903.json
```

No C++, Plugin, UE, P4/Track C, M6 compression, or Writer surface changes.

## 14. Honest misses and notes

```text
- L2 recipe formation depends on deterministic operation metadata plus >= 3
  verified successes per (operation, assetClass). Today's M3 rules emit stable
  verified-write facts without an operation field, and the op-bearing
  supersession decision records are stored unverified by default, so production
  L2 recipes form mostly from user-confirmed verified facts (and future
  deterministic M3 metadata). This is the intended conservative, evidence-bound
  behavior; the recipe builder and matching logic are fully exercised by tests
  and the benchmark with user-confirmed verified writes.
- M4's frozen hybrid benchmark requires the optional local vector stack (repo
  .venv + local model2vec directory), matching M4's documented procedure. It was
  run only as stage-specific G1 evidence; automatic M5 injection is model-free.
- The repository .venv hard-crash artifact mentioned in the handoff was not
  observed in this session; all heavy portable gates were run with py -3.12.
- No commit was created (no authorization for an M5 implementation checkpoint).
```

## 15. Final verdict

```text
M5 L2/L3 stable context injection        IMPLEMENTED
Required UE                               U0
Memory G1                                 PASS
Portable G2                               PASS
M1/M2/M3/M4 regression gates              PASS
Published version                         0.7.0 unchanged
Commit / push / rebase / tag / release    none
```

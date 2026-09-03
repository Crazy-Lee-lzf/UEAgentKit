# UEAgentKit M4 Hybrid Recall — FTS5 + Optional Vector + RRF — Detailed Plan

> Date: 2026-09-02
>
> Branch: `feature/memory-context`
>
> Product baseline: `30449274b7d8f417af89be07c36d1a317cdc0390` (M3 COMPLETE)
>
> State: **READY FOR IMPLEMENTATION**
>
> Required UE level: **U0**
>
> P4 prerequisite: **No**
>
> Required runtime dependencies: **remain `[]`**
>
> Memory schema: **v4 -> v5 additive**
>
> LLM / remote embedding API: **forbidden**

## 1. Goal

M4 adds optional semantic retrieval to Project Memory without weakening the
zero-required-dependency product or the M1-M3 latency/safety contracts.

The retrieval stack becomes:

```text
FTS5 BM25                           always available
optional local query embedding     vector-enabled explicit search only
optional vector distance           local only
RRF fusion                         deterministic
final RecallBudget                 unchanged
```

Primary user problem:

```text
keyword query      "material TintColor value"
semantic query     "why was this material parameter chosen?"
```

The second query should recover relevant L1 memory even when it shares few
literal terms with the stored record.

## 2. Frozen non-goals

M4 does **not** implement:

```text
M5 L2/L3 prompt injection
M6 symbolic compression
LLM summarization or embedding APIs
remote model serving
GPU requirements
P4 / Track C
C++ or Unreal changes
background daemon/thread embedding
implicit model download during MCP/query execution
changes to M3 deterministic derivation rules
changes to Writer safety semantics
```

No UE/UBT process is required or allowed for normal M4 closure.

## 3. Repository-grounded baseline

At M3 closure:

```text
Memory schema                         v4
memory_records_fts                    already exists and is trigger-maintained
Project Memory FTS search             search_memory_records(...)
FTS rank                              SQLite bm25(memory_records_fts)
required dependencies                 []
optional dependencies                 mcp / dev only
M3 100-event distillation             263.852 ms / PASS
portable full                         909 / 909 PASS
Memory G1                             226 / 226 PASS
```

Persistent M1/M2 gates:

```text
automatic recall                      <= 5 items
recalled content                      <= 2000 chars
final envelope                        <= 800 estimated tokens
real recall deadline                  <= 300 ms
first Tool Memory incremental p95     < 200 ms
direct automatic recall p95           < 300 ms
task-end append p95                   < 100 ms
four-event L0 capture p95             < 100 ms
exact duplicate replay new rows       0
```

M3 remains explicit/offline and zero-LLM.

## 4. Key architecture resolutions

### 4.1 Zero-dependency fallback is the product contract

Vector support is optional. If any vector prerequisite is absent or invalid:

```text
missing sqlite-vec
missing model2vec
missing local model artifact
model load failure
model incompatibility
vector SQL capability unavailable
embedding failure
```

then explicit search must return a valid FTS5 result rather than fail the
Memory capability.

`pyproject.toml [project].dependencies` remains exactly `[]`.

### 4.2 Automatic Task Context remains FTS-only in M4

The historical Midterm says "hybrid recall" but does not require every recall
path to invoke a model. M1 established that task-start latency is a veto-level
constraint.

Therefore M4 freezes:

```text
Task Context / automatic recall       FTS-only
explicit ue_memory_search             hybrid when vector is explicitly available
knowledge-view existing FTS endpoints unchanged unless separately opted in
```

M5 may reconsider automatic hybrid recall only after M4 has cold/warm evidence.
M4 itself must not make first Tool Memory depend on model loading.

### 4.3 No conditional persistent sqlite-vec schema

Schema v5 must mean the same SQLite structure whether the vector extra is
installed or not.

Therefore the v5 migration may create ordinary SQLite tables/indexes only.
It must **not** conditionally create a persistent `vec0` virtual table whose
existence depends on loading the sqlite-vec extension.

M4-0 must probe whether the installed sqlite-vec version exposes a scalar
vector-distance function usable against an ordinary BLOB column. Prefer that
shape because it preserves one deterministic v5 schema.

If the actual sqlite-vec API cannot support a schema-stable design, stop and
report before implementing a conditional database shape.

### 4.4 No implicit network access

No MCP or search path may download a model.

A vector provider is enabled only when its model is already available locally.
A missing/non-local model is treated as vector-unavailable -> FTS fallback.

## 5. M4-0 — capability probe and frozen benchmark corpus

Before implementation, record actual environment facts.

Probe on supported local Python (prefer 3.12; verify metadata remains compatible
with >=3.11,<3.13):

```text
SQLite FTS5 available
sqlite3 extension loading capability
sqlite-vec import/load mechanism
sqlite-vec scalar cosine/distance function availability
model2vec import/API shape
one candidate local static model size
cold model load time
warm single-query embedding p50/p95
embedding dimension
returned dtype/shape
whether first use attempts network access
```

Model acceptance constraints:

```text
CPU only
no GPU requirement
model artifact < 100 MB
single short-text embedding p95 < 10 ms after load
finite deterministic output for identical text
no implicit network access in accepted runtime path
```

If no candidate satisfies these constraints, M4 may still deliver schema v5,
FTS fallback, vector provider abstraction, and deterministic backfill/search
plumbing, but vector-quality acceptance must be marked `blocked` rather than
faking a semantic improvement result.

### 5.1 Freeze 20-query relevance benchmark before ranking tuning

Create a deterministic benchmark fixture with explicit relevant record IDs.
It must include:

```text
10 lexical/easy queries
10 semantic/paraphrase queries with deliberately low literal overlap
multiple plausible distractor records
projectFact / projectRule / knownIssue / decisionRecord coverage
```

The relevance file is frozen before RRF tuning so the implementation cannot
cherry-pick expected answers after seeing scores.

## 6. Schema v5

Add one unconditional migration in `memory_schema.py`.

Required ordinary table shape:

```sql
CREATE TABLE memory_embeddings (
    record_id           TEXT PRIMARY KEY
                        REFERENCES memory_records(record_id) ON DELETE CASCADE,
    model_id            TEXT NOT NULL,
    dim                 INTEGER NOT NULL CHECK (dim > 0),
    content_sha256      TEXT NOT NULL,
    embedding           BLOB NOT NULL,
    created_at_utc      TEXT NOT NULL,
    updated_at_utc      TEXT NOT NULL
);

CREATE INDEX memory_embeddings_model_idx
    ON memory_embeddings(model_id, record_id);
```

`content_sha256` is required even though the historical sketch omitted it. The
current repository already treats `memory_records.content_sha256` as canonical
record identity, and an embedding must never silently survive a content change.

Migration contract:

```text
v4 database + no vector extra       -> upgrades to v5 successfully
v4 database + vector extra          -> identical persistent schema
fresh database                      -> reaches v5
reopen v5 repeatedly                -> idempotent
readonly v5                         -> accepted
v4 readonly                         -> version mismatch, existing behavior
```

Do not create embeddings during schema migration.

## 7. Canonical embedding contract

Add a narrow module, expected name:

```text
src/ue_agent_kit/memory_vector.py
```

### 7.1 Stable embedding text

Embedding input must be deterministic and bounded. Initial frozen text:

```text
recordType=<record_type>
subject=<subject_key>
title=<title>
<body>
```

Do not embed arbitrary artifact payloads, stack traces, full `details_json`, or
local paths.

Bound encoded input before provider invocation (initial hard bound: 4096
characters). Same record content must produce byte-identical provider input.

### 7.2 Stable model identity

`model_id` must identify the actual embedding model, not merely a friendly
name. Prefer:

```text
<provider>:<model-name>:<model-artifact-digest-or-version>
```

Two materially different model artifacts must not share a `model_id`.

### 7.3 Stored representation

Normalize one embedding to finite float32 values and serialize deterministically
(e.g. little-endian float32 BLOB). Validate:

```text
dimension > 0 and bounded
all values finite
stored dim matches decoded vector
content_sha256 matches current record
model_id matches active provider
```

No required NumPy import is allowed in the base package. Optional provider
outputs may be converted through a narrow adapter.

## 8. Embedding generation paths

There are only two legal corpus-embedding paths.

### 8.1 Newly distilled L1 records

M3 rule evaluation remains vector-agnostic. After explicit offline distillation
returns record IDs, the M4 integration layer may ensure embeddings **only if**
vector mode is explicitly available.

Vector-disabled distillation must preserve M3 behavior and performance gates.

### 8.2 Explicit backfill

Add:

```text
ue-agent memory backfill-embeddings
```

Required behavior:

```text
stable ORDER BY record_id
bounded batch size
skip exact current model_id + content_sha256 rows
replace/rebuild stale model/content rows deterministically
commit bounded batches
restart-safe
repeat run creates zero unnecessary rewrites
no background thread
no request-path execution
```

Backfill output reports at least:

```text
selected
created
reused
rebuilt
failed
remaining
modelId
elapsedMs
```

A failure on one record must be bounded and visible; it must not corrupt already
committed batches.

## 9. Explicit hybrid search

Keep existing `search_memory_records(...)` semantics as the FTS primitive.
Do not silently change its rank meaning.

Add a separate hybrid retrieval layer and let the explicit Memory Search facade
use it when vector is available.

### 9.1 Candidate filters

FTS and vector branches must apply equivalent:

```text
project_key
record_type filter
status filter
scope filter
```

Stale/superseded records remain excluded by existing default semantics unless
explicitly requested through an existing supported filter.

### 9.2 Vector branch

One query:

```text
query text -> exactly one query embedding
stored record embeddings -> zero recomputation
local vector distance -> top-k
```

The query path must never backfill missing record embeddings.
Missing embeddings simply mean those records participate through FTS only.

### 9.3 RRF

Use 1-based ranks and frozen constant:

```text
RRF_K = 60
score(record) = sum(1 / (RRF_K + rank_i))
```

Branches:

```text
FTS rank      rank_fts
vector rank   rank_vec
```

Deduplicate by record ID.

Stable final tie-break:

```text
RRF score DESC
best branch rank ASC
updated_at_utc DESC
record_id ASC
```

Do not mix raw BM25 and cosine magnitudes directly.

### 9.4 Fallback envelope

Explicit Memory Search should make retrieval provenance inspectable without
breaking callers. The service-level result should be able to report:

```text
retrievalMode      fts | hybrid
vectorAvailable    bool
vectorFallback     stable reason code or empty
queryEmbeddingCount
```

Do not expose exception text, model filesystem paths, or arbitrary loader errors.

## 10. Latency behavior

Hard rule:

```text
explicit mixed recall < 300 ms
```

Measure separately:

```text
vector disabled FTS fallback
first explicit vector-enabled search after process construction
warm vector-enabled search
```

No record embedding may be regenerated inside any query benchmark.

If vector initialization cannot meet the request budget, fail open to FTS rather
than making the user wait. Do not add a daemon solely to hide model load time.

Automatic Task Context remains FTS-only, so M1 first-Tool and direct-recall
gates must remain effectively unchanged.

## 11. Quality benchmark

Add a deterministic report, expected:

```text
scripts/MeasureMemoryHybridRecall.py
benchmarks/memory/m4_hybrid_recall_<date>.json
```

For the frozen 20-query corpus report:

```text
FTS top-k IDs
hybrid top-k IDs
relevant IDs
Recall@5
MRR
latency
retrieval mode
```

Acceptance when vector mode is available:

```text
semantic subset Recall@5       hybrid > FTS
aggregate MRR                   hybrid >= FTS
lexical safety                  no query that had a relevant FTS top-5 result
                                may lose all relevant top-5 results after fusion
20-query hybrid p95             < 300 ms
query embedding calls/query     exactly 1
corpus embedding calls/query    exactly 0
```

Do not claim vector quality improvement if the optional vector stack is not
actually active.

## 12. M4 execution slices

### M4-0 — capability probe + benchmark freeze

```text
confirm M3 baseline
probe optional stack
freeze 20-query relevance fixture
capture pre-M4 FTS quality/latency baseline
```

### M4-1 — schema v5

```text
memory_embeddings ordinary table
migration/reopen/readonly tests
no optional import required
```

### M4-2 — vector provider + deterministic embedding storage

```text
lazy optional imports
local-only model requirement
canonical embedding text/model identity
finite float32 storage
record content/model mismatch detection
```

### M4-3 — offline backfill + M3 post-distill integration

```text
bounded restart-safe backfill
no query-time corpus embedding
vector-disabled M3 behavior unchanged
```

### M4-4 — explicit hybrid search + RRF

```text
filter parity
one query embedding
vector top-k
FTS top-k
RRF K=60
stable tie-break/fallback metadata
```

### M4-5 — quality/performance closure

```text
20-query quality report
M1/M2/M3 regression gates
Memory G1
one final G2 full suite
Result document
```

## 13. Expected touched files

Primary expected surface:

```text
pyproject.toml
src/ue_agent_kit/memory_schema.py
src/ue_agent_kit/project_memory.py
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/memory_context.py       only if needed to prove FTS-only isolation
src/ue_agent_kit/memory_vector.py        new
src/ue_agent_kit/cli.py
src/ue_agent_kit/mcp_memory_tools.py     only if explicit search metadata requires it
scripts/RunPythonTests.py
scripts/MeasureMemoryHybridRecall.py     new
tests/python/test_project_memory.py
tests/python/test_memory_service.py
tests/python/test_memory_context.py
tests/python/test_memory_cli.py
tests/python/test_memory_vector.py       new
benchmarks/memory/
```

Broad unrelated refactors are out of scope.

## 14. Validation Budget

Required UE level: **U0**.

During edits:

```text
focused tests for touched M4 modules
focused Ruff for touched files when useful
no repeated full suite
```

G0 after coherent core slices:

```text
python scripts/RunPythonTests.py fast
```

Only run G0 when it provides useful integration evidence; not after every small
edit.

G1 after final functional source state:

```text
python scripts/RunPythonTests.py domain memory
```

Run Memory G1 once.

M4 acceptance gates:

```text
vector-disabled fallback tests
v4 -> v5 migration tests without vector extra
optional vector focused tests when vector stack is available
20-query quality benchmark
hybrid latency <300 ms
query embedding count = 1
corpus query-time embedding count = 0
backfill restart/idempotence tests
M1/M2 regression benchmark
M3 100-event distillation regression
```

G2 once at closure:

```text
python scripts/RunPythonTests.py full
ruff check src tests/python scripts
python -m compileall src tests/python scripts
python scripts/ValidateRelease.py 0.7.0 --skip-tests --skip-ruff
git diff --check
```

Do not run UE/UBT.

## 15. Acceptance matrix

M4 is complete only if all applicable items are proven:

```text
A1  Memory schema upgrades v4 -> v5 additively                         PASS
A2  v5 schema identical with/without vector extra                      PASS
A3  required dependencies remains []                                   PASS
A4  no implicit network/model download on query path                   PASS
A5  vector-disabled product/search remains fully functional            PASS
A6  automatic Task Context remains FTS-only                            PASS
A7  M1 first-Tool/Recall/append budgets remain PASS                    PASS
A8  M2 four-event capture/idempotence remains PASS                     PASS
A9  M3 deterministic distillation and <5s gate remain PASS             PASS
A10 canonical embedding text/model identity deterministic              PASS
A11 embedding content_sha256 mismatch is detected/rebuilt              PASS
A12 backfill stable, bounded, restart-safe, idempotent                  PASS
A13 query generates exactly one query embedding in hybrid mode         PASS
A14 query generates zero corpus embeddings                             PASS
A15 FTS/vector filters have parity                                     PASS
A16 RRF uses K=60, 1-based ranks, stable deterministic tie-break        PASS
A17 missing/failing vector stack degrades to FTS with stable reason    PASS
A18 semantic Recall@5 improves over FTS on frozen semantic subset      PASS*
A19 aggregate MRR does not regress                                     PASS*
A20 lexical top-5 safety gate holds                                    PASS*
A21 explicit hybrid p95 <300 ms                                        PASS*
A22 Memory G1 PASS                                                     PASS
A23 final portable G2 PASS                                             PASS
A24 no UE/P4/C++/LLM/M5/M6 scope creep                                PASS
```

`*` applies when an accepted local vector stack is actually available. If M4-0
cannot establish a compliant optional stack, these items are `blocked`, not
silently converted to PASS.

## 16. Stop / owner-decision conditions

Stop and report before broad implementation if repository facts prove any of:

```text
sqlite-vec requires a persistent conditional schema that would make v5 vary
no local static model can satisfy CPU/<100MB/<10ms/no-network constraints
vector integration requires adding a required dependency
hybrid search cannot preserve the 300ms hard budget without weakening it
Task Context must be made model-dependent to implement the design
schema v5 would require destructive migration
```

Do not weaken M1-M3 gates to make M4 green.

## 17. Git / delivery boundary

The implementation Agent may modify the M4 working tree only after verifying
actual Git status and this Plan.

Do not push, rebase, tag, release, or change published version without separate
owner authorization.

At completion produce exactly one normal Result:

```text
docs/Plans/UEAGENTKIT_M4_HYBRID_RECALL_FTS5_VECTOR_RRF_RESULT_<date>.md
```

Report:

```text
capability-probe facts
actual optional package/model identity
schema evidence
quality metrics
latency metrics
G0/G1/G2 counts and elapsed times
full-suite count
Memory-domain count
UE runs (expected 0)
any blocked optional-vector acceptance item
```

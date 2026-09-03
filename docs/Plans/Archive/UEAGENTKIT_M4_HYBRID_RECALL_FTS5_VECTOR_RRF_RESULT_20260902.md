# UEAgentKit M4 Hybrid Recall — FTS5 + Optional Vector + RRF — Result

> Closure date: 2026-09-03
>
> Evidence date: 2026-09-02
>
> Branch: `feature/memory-context`
>
> Planning HEAD: `bc403c0efd7f5c8ff258ef9481b183cc09e977d8`
>
> Product baseline: `30449274b7d8f417af89be07c36d1a317cdc0390`
>
> Stage state: **COMPLETE / G2 PASS / U0**
>
> Commit / push / rebase / tag / release: **not performed**

## 1. Closure summary

M4 adds optional local semantic retrieval to Project Memory while preserving the zero-required-dependency product contract and the M1-M3 latency/safety gates.

The final retrieval model is:

```text
Task Context / automatic recall       FTS5 only
explicit Memory Search                FTS5 + optional local vector + RRF
vector unavailable / failed           deterministic FTS fallback
corpus embedding generation           offline distill/backfill only
query-time corpus embedding            forbidden / zero calls
```

No UE, UBT, P4, C++, LLM, remote embedding API, background embedding daemon, or M5/M6 work was introduced.

## 2. M4-0 capability probe

Canonical evidence:

`benchmarks/memory/m4_capability_probe_20260902.json`

Observed environment:

```text
Python                              CPython 3.12.10
SQLite                              3.49.1
FTS5                                available
sqlite3 extension loading           available
sqlite-vec                          0.1.9
model2vec                           0.9.0
candidate model                     minishlab/potion-base-8M
model files                         30,922,193 bytes total
embedding dimension                 256
embedding dtype                     float32
GPU requirement                     none
Torch requirement                   none
```

Critical architecture proof:

```text
sqlite-vec vec_distance_cosine
works directly on ordinary BLOB columns
```

Measured scalar scans:

```text
5,000 rows x 256 dim                p50 3.313 ms
20,000 rows x 256 dim               p50 12.966 ms
```

Therefore the preferred schema-stable design is viable: schema v5 uses only an ordinary `memory_embeddings` table and does not conditionally create a `vec0` virtual table.

Accepted model path:

```text
model artifact                       < 100 MB       PASS
CPU-only                             yes            PASS
warm short-text embedding p95       0.112 ms       PASS (<10 ms)
cold load                            ~1.4-2.2 s
finite deterministic output          PASS
o implicit runtime download          PASS
```

The cold-load result confirms the M4 architecture decision that automatic Task Context must remain FTS-only.

## 3. Frozen relevance benchmark

Canonical corpus:

`benchmarks/memory/m4_frozen_relevance_corpus_20260902.json`

The frozen fixture contains:

```text
records                              25
queries                              20
  lexical                            10
  semantic/paraphrase                10
```

The corpus and relevant record IDs were frozen before hybrid/RRF tuning.

Pre-M4 FTS baseline:

`benchmarks/memory/m4_fts_baseline_20260902.json`

```text
lexical Recall@5                     1.0
lexical MRR                          1.0
semantic Recall@5                    0.0
semantic MRR                         0.0
aggregate Recall@5                   0.5
aggregate MRR                        0.5
```

## 4. Implemented contract

### 4.1 Schema v5

Memory schema advances additively from v4 to v5 with an unconditional ordinary SQLite table:

```text
memory_embeddings
  record_id
  model_id
  dim
  content_sha256
  embedding
  created_at_utc
  updated_at_utc
```

The persistent v5 schema is identical whether the optional vector extra is installed or not.

`pyproject.toml [project].dependencies` remains exactly `[]`.

### 4.2 Deterministic embedding layer

`src/ue_agent_kit/memory_vector.py` provides:

```text
bounded canonical embedding text
stable model identity
finite float32 BLOB serialization
content_sha256 binding
local-only/offline provider loading
optional sqlite-vec distance path
RRF K=60 with 1-based ranks
deterministic tie-break
stable FTS fallback reason codes
```

No base-package NumPy dependency is required.

### 4.3 Backfill and post-distill embedding

Added explicit offline embedding maintenance:

```text
ue-agent memory backfill-embeddings
```

Behavior is bounded, stable-order, restart-safe and idempotent. Existing embeddings are reused when `model_id` and `content_sha256` match; stale rows are rebuilt.

A real product bug found during M4 was fixed: a permanently failing record could previously keep backfill in an infinite no-progress loop. The final implementation tracks failed records and terminates when no progress is possible.

M3 rule evaluation itself remains vector-agnostic; optional embedding happens only after explicit offline distillation/backfill paths.

### 4.4 Explicit hybrid search

Explicit Memory Search reports retrieval provenance:

```text
retrievalMode
vectorAvailable
vectorFallback
queryEmbeddingCount
```

Hybrid mode performs:

```text
FTS top-k
+
vector top-k
+
RRF K=60
+
stable tie-break
```

The query path generates exactly one query embedding and zero corpus embeddings.

The existing FTS primitive keeps its original BM25 rank semantics. Hybrid `rank` is an RRF score and is always accompanied by explicit retrieval mode metadata.

### 4.5 Automatic recall isolation

Task Context / automatic Project Memory recall remains FTS-only. It does not load the vector model and does not depend on sqlite-vec/model2vec availability.

## 5. Hybrid quality and latency

Canonical evidence:

`benchmarks/memory/m4_hybrid_recall_20260902.json`

```text
                           FTS           Hybrid
lexical Recall@5           1.0           1.0
lexical MRR                1.0           1.0
semantic Recall@5          0.0           0.9
semantic MRR               0.0           0.6583
aggregate Recall@5         0.5           0.95
aggregate MRR              0.5           0.8292
```

Latency:

```text
FTS p95                             0.229 ms
Hybrid p95                          1.388 ms
required limit                    300 ms
```

Embedding-call contract:

```text
query embedding calls/query          1     PASS
corpus embedding calls/query          0     PASS
hybrid mode actually active           yes   PASS
```

Lexical safety violations: none.

One semantic query remains an honest miss:

```text
S09: "editing shader parents breaks downstream variants"
```

It did not recover its relevant record in hybrid top-5. The frozen relevance corpus and RRF were not altered after observing this miss.

## 6. M1/M2/M3 regression evidence

M1/M2 report:

`benchmarks/memory/m4_m12_regression_20260902.json`

```text
first Tool Memory incremental p95     17.283 ms   <200 ms   PASS
direct automatic recall p95           16.003 ms   <300 ms   PASS
task-end append p95                    18.953 ms   <100 ms   PASS
four-event L0 capture p95              16.796 ms   <100 ms   PASS
exact duplicate replay new rows             0                PASS
automatic recall                        3 items / 1633 chars / 769 estimated tokens PASS
no-hit recall                            0 items / 0 chars    PASS
```

M3 report:

`benchmarks/memory/m4_m3_distillation_20260902.json`

```text
selected / evaluated / distilled      100 / 100 / 100
produced records                        70
deferred / failed                        0 / 0
pendingAfter                              0
100-event distillation                332.807 ms <5000 ms PASS
```

## 7. Validation evidence

### Focused / G0 / G1

Implementation evidence before closure:

```text
vector-extra focused                  40 / 40 PASS
G0 fast                              485 tests / 12.4 s PASS
Memory G1                            266 tests / 26.4 s PASS
```

Memory G1 used the repository-standard `py -3.12` zero-extra interpreter.

The repository `.venv` was used only for Ruff and vector-focused tests/benchmarks. Heavy Memory-domain modules were not treated as authoritative under `.venv` because that interpreter/environment can terminate silently on unrelated heavy modules, while the same modules pass under `py -3.12`.

### G2 closure

Executed once after the final functional source state:

```text
Full Python                           949 / 949 PASS
                                      17 skipped
                                      96.774 s
Ruff                                  PASS
compileall                            PASS
ValidateRelease 0.7.0                PASS
Schemas                               3 PASS
Patch examples                       16 PASS
git diff --check                      PASS
UE / UBT                              0 runs (U0)
```

`ValidateRelease.py` in the current repository accepts the version via:

```text
--expected-version 0.7.0
```

The historical Plan example used a positional `0.7.0`; that invocation was rejected by argparse and was immediately corrected to the current script contract. No tests were rerun as part of that correction.

## 8. Acceptance matrix

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
A18 semantic Recall@5 improves over FTS on frozen semantic subset      PASS
A19 aggregate MRR does not regress                                     PASS
A20 lexical top-5 safety gate holds                                    PASS
A21 explicit hybrid p95 <300 ms                                        PASS
A22 Memory G1 PASS                                                     PASS
A23 final portable G2 PASS                                             PASS
A24 no UE/P4/C++/LLM/M5/M6 scope creep                                PASS
```

## 9. Main M4 files

```text
pyproject.toml
scripts/RunPythonTests.py
scripts/MeasureMemoryHybridRecall.py
src/ue_agent_kit/cli.py
src/ue_agent_kit/mcp_memory_tools.py
src/ue_agent_kit/memory_schema.py
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/memory_vector.py
tests/python/test_memory_vector.py
tests/python/test_memory_l0.py
tests/python/test_memory_service.py
tests/python/test_memory_tree.py
tests/python/test_project_memory.py
benchmarks/memory/m4_capability_probe_20260902.json
benchmarks/memory/m4_frozen_relevance_corpus_20260902.json
benchmarks/memory/m4_fts_baseline_20260902.json
benchmarks/memory/m4_hybrid_recall_20260902.json
benchmarks/memory/m4_m12_regression_20260902.json
benchmarks/memory/m4_m3_distillation_20260902.json
```

## 10. Repository / delivery state

M4 is functionally closed and G2 is green, but the implementation remains uncommitted in the working tree because no commit authorization was given.

Current unrelated concurrent document:

```text
docs/Plans/UEAGENTKIT_P4_AGENT_OPERATION_BOUNDARY_DECISION_20260903.md
```

That document is not part of the M4 implementation or acceptance surface and was not used to claim M4 completion.

No push, rebase, tag, release, published-version change, UE run, or UBT run was performed.

## 11. Final verdict

```text
M4 Hybrid Recall                    COMPLETE
Required UE                         U0
G2                                  PASS
Vector quality acceptance           PASS
Zero-extra fallback                 PASS
M1/M2/M3 regression                 PASS
Published version                   0.7.0 unchanged
Commit                              none
Push                                none
```

Next Track M stage may proceed to M5 planning after owner review / optional local checkpoint commit of the completed M4 working tree.

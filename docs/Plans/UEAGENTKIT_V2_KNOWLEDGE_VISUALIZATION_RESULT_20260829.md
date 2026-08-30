# UEAgentKit Track V — V2 Knowledge Visualization Result

> Date: 2026-08-29
>
> Track: V — Knowledge Web / Read-only Visualization (stage V2)
>
> Worktree: `E:\WorkSpace\UEAgentKit-KnowledgeWeb`
>
> Branch: `feature/knowledge-web-view`
>
> Plan (frozen contract): `docs/Plans/UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_DETAILED_PLAN_20260829.md`
>
> Parent plan: `docs/Plans/UEAGENTKIT_TRACK_V_READ_ONLY_KNOWLEDGE_WEB_DETAILED_PLAN_20260829.md` (section 7)
>
> This document records the factual V2 outcome: implementation shape, frozen
> contract compliance, functional acceptance, measured performance, security
> regression, and gate results. V2 product code is complete; a commit is
> created only on user authorization (plan section 13).

## 0. Entry State

```text
branch   feature/knowledge-web-view
HEAD     473ffd7 docs: freeze V2 visualization contract   (parent 49b8fa6 V1 checkpoint)
V1 gates all PASS (792 tests OK / Ruff / compileall / ValidateRelease 0.7.0 / git diff --check)
```

V2 started from a clean tree at the V2-0 contract checkpoint. No Unreal /
UBT / Direct Build was started; `E:\WorkSpace\UEAgentKit-LiveWriter` was not
touched; no push / rebase / tag / release; published version remains 0.7.0.

## 1. Implementation Shape

```text
src/ue_agent_kit/knowledge_view.py                     (modified)
    KnowledgeViewReadService gains: graph / impact / coverage / timeline / stale
    route dispatch + _QUERY_PARAMS whitelist for the 5 new routes
    /api/impact/<asset_path> single-segment validation + unquote
src/ue_agent_kit/web/index.html                        (modified)
    V2 tabs: 依赖图 / 影响 / 覆盖 / 时间线 / 陈旧
    native <canvas> graph renderer (pan/zoom, click node -> impact view)
    table renderers with bounded paging; record links -> /api/record/<id>
    startup still fetches /api/status + bounded tree only (no full-project fetch)
tests/python/test_knowledge_view_visualization.py      (new, 43 tests)
scripts/benchmark_knowledge_view.py                    (new, deterministic perf gate)
benchmarks/knowledge_view_benchmark_5000.json          (new, raw measurements)
docs/Plans/UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_DETAILED_PLAN_20260829.md  (V2-0, committed)
docs/Plans/UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_RESULT_20260829.md         (this document)
```

No change to `pyproject.toml` dependencies (still `[]`). No CLI subcommand
change (same `knowledge-view` server).

## 2. Contract Compliance

Frozen V2 contract sections 4.1-4.8 are implemented as specified, with these
documented interpretation decisions (all consistent with the frozen text):

- `truncated` shape per section 4.7: `null | {"reason": "nodeLimit"|"limit", "limit": int, "count": int}`.
  For `/api/graph` the top-level `truncated` carries the object and
  `meta.truncated` is its boolean presence flag (matches the plan example);
  coverage/timeline/stale mirror the object in `meta.truncated` (matches their
  examples) and also expose the top-level `truncated` field (section 4.7
  applies to every collection route).
- Graph edges are aggregated `references_table` rows grouped by
  (source asset_path, target asset_path); only edges whose BOTH endpoints are
  project assets inside the returned node set are emitted (project-only
  semantics, consistent with `queries.find_references(project_only=True)`).
  Self edges are counted once and flagged `selfLoop: true`.
- `referenceCount` per node = sum of aggregated edge counts incident to the
  node within the returned graph (self-loop counted once).
- Stale `groupBy=scope` uses each record's first scope
  (`MIN(scope_key)` per record) to keep the record->bucket mapping 1:1, so
  `totals` stay consistent and sampleRecordIds stay exact (documented D6
  dependency: the `memory_records`<->`memory_scopes` link exists in the
  current schema).
- Stale age buckets are frozen (`0-7d` / `8-30d` / `31-90d` / `90d+`), computed
  from `updated_at_utc` vs server UTC now (plan D5).
- Timeline emits `recordUpdated` for every matching `memory_records` row and,
  only when `includeStatusEvents=true`, merges `memory_status_events`
  (including creation events) sorted DESC by `(timestampUtc, eventId)` for
  determinism (plan D4).
- No opaque coverage percentage is ever emitted (plan D3); zero-record nodes
  are included in `/api/coverage`.
- `/api/graph` node limits: default 300, normal cap 1000, stress cap 5000 via
  `stress=1` (plan D2); `limit > 1000` without `stress=1` -> 400
  `"stress=1 required above 1000"`.
- Query parameters are whitelisted per route; unknown params -> 400. Path
  segments are validated (single segment, no raw `/`); asset paths arrive
  URL-encoded and are unquoted server-side (no filesystem mapping).
- `assetNotFound(404)` for missing `/api/graph` root and missing
  `/api/impact` asset. Empty consumer results are valid (not an error).

## 3. Functional Acceptance

Frozen plan section 8.1, per view:

| view | acceptance | evidence |
|---|---|---|
| /api/graph | bounded, root required, depth 0..3, direction filter, in-band truncated, stress unlock, 404 root | 14 tests (GraphTests) |
| /api/impact | inbound consumers + countsByKind + bounded expansion + 404/empty | 8 tests (ImpactTests) |
| /api/coverage | zero-record nodes included, no percentage, totals before paging | 5 tests (CoverageTests) |
| /api/timeline | current-read-model facts only; statusChanged opt-in; schema-tolerant | 5 tests (TimelineTests) |
| /api/stale | groupBy nodePath/scope/recordType/ageBucket; sample ids -> record | 6 tests (StaleTests) |

New dedicated suite: `tests/python/test_knowledge_view_visualization.py`
= **43 tests, all PASS**. V1 + V2 knowledge-view suites together
(69 tests) PASS; no V1 regression observed.

## 4. Performance (V2-3, deterministic benchmark)

`scripts/benchmark_knowledge_view.py` builds synthetic fixture DBs
(5221 assets / 10132 references; 20 knowledge nodes / 2000 records), starts
the read-only server on an ephemeral loopback port and records facts.
Raw report: `benchmarks/knowledge_view_benchmark_5000.json`.

Environment: Python 3.12.10, Windows-10-10.0.19045-SP0, AMD64.

| measurement | value |
|---|---|
| /api/status | http 9.8 ms |
| /api/graph root L0/A0 depth=3 outgoing limit=5000 stress=1 | **nodeCount 5000, edgeCount 4999**, server query 55.3 ms, http 66.8 ms, JSON 1,408,676 bytes, truncated={nodeLimit,5000,5000} |
| /api/impact HUB (4913 consumers) | server 16.1 ms, http 18.1 ms, page 200/4913, truncated in-band |
| /api/coverage (20 nodes) | server 7.5 ms, http 8.8 ms |
| /api/timeline (+status events, 2000 records) | server 19.7 ms, http 22.1 ms |
| /api/stale (nodePath) | server 12.0 ms, http 13.1 ms |
| client Canvas (headless Chrome, 5000 nodes) | layout+first-draw render 6.5 ms, frame sample 1.82 ms (~549 fps virtual-time; relative only) |

Plan section 8.2 acceptance:

- [x] default dashboard initial data < 500 ms on acceptance DB (status 9.8 ms)
- [x] record/tree normal interactions never fetch unbounded collections
- [x] 5000-node graph loads and interacts without browser hang (render 6.5 ms,
      frame 1.82 ms in headless Chrome)
- [x] graph request server-side bounded and reports truncation in-band
- [x] no full-project graph automatically fetched at startup (frontend fetches
      /api/status + bounded tree only; graph tab loads on explicit input)
- [x] 5000-node run records node count / edge count / JSON bytes / server query
      ms / client render ms / frame-time sample / browser + machine environment
      (see table above and raw report)

Note: client fps is measured under `--virtual-time-budget`, so absolute fps is
relative; render/frame elapsed values are the meaningful samples.

## 5. Security Regression (plan 8.3, re-run on every V1 assertion)

- [x] localhost only: unchanged loopback binding (127.0.0.1 / ::1 / localhost)
- [x] SQLite mode=ro at connection level for both DBs (unchanged V1 pattern)
- [x] no mutation endpoint: POST/PUT/PATCH/DELETE -> 405 on V1 AND all 5 new
      routes (test_mutation_methods_answer_405_on_v2_routes)
- [x] no new runtime Python dependency (pyproject dependencies = [])
- [x] no npm / no build pipeline
- [x] no Unreal / UBT
- [x] new routes: param whitelist (400), single-segment path validation, no
      traceback leak (test_no_traceback_leak), no arbitrary static serving
- [x] read-only proof: exercising every new route leaves both DB files
      byte-identical (sha256) with unchanged PRAGMA data_version
      (test_exercising_v2_routes_does_not_modify_databases)

## 6. Gate Results (serial, identical to V1)

```text
1. full discovered Python suite        PASS  Ran 835 tests in 1606.5s ... OK
                                        (792 V1 baseline + 43 new V2 tests)
2. ruff check src tests/python         PASS  All checks passed!
3. compileall -q src/ue_agent_kit      PASS
4. ValidateRelease 0.7.0 (full)        PASS  Ran 835 tests in 1571.9s ... OK
                                        + RELEASE VALIDATION PASSED: 0.7.0
                                        (Schemas: 3, Patch examples: 16)
5. git diff --check                    PASS  (informational CRLF notices only)
```

The discovered suite count at V2 end is **835** (792 at V1 end + 43 new V2
tests), recorded from the actual run, never hard-coded (plan section 9).

## 7. Stop Conditions Review (plan section 11)

None triggered: no writable-DB requirement, no migration, no Unreal launch,
no new background pipeline, no arbitrary filesystem exposure, no localhost
conflict, no runtime-dependency need (Canvas met the perf target; no vendored
lib required, plan D7), no foreign-agent work copied, no schema guessing, no
unbounded graph fetch.

## 8. Commit Boundary / Authorization (plan section 13)

- V2 product code and this Result are complete; **no commit has been created**.
- On user authorization a single checkpoint commit will be created with
  message `feat: add V2 knowledge visualization dashboard`.
- No push / rebase / tag / release; published version remains 0.7.0.
- `docs/Plans/README.md` already lists the V2 plan row (V2-0); the V2 result
  row is added together with the authorized commit.

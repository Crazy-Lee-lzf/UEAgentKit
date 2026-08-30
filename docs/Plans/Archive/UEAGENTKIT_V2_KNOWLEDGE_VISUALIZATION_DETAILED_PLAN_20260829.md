# UEAgentKit Track V — V2 Knowledge Visualization Detailed Plan

> Date: 2026-08-29
>
> Track: V — Knowledge Web / Read-only Visualization (stage V2)
>
> Worktree: `E:\WorkSpace\UEAgentKit-KnowledgeWeb`
>
> Branch: `feature/knowledge-web-view` (HEAD `49b8fa61` = V1 checkpoint commit)
>
> Parent plan: `docs/Plans/UEAGENTKIT_TRACK_V_READ_ONLY_KNOWLEDGE_WEB_DETAILED_PLAN_20260829.md` (section 7 = V2 direction)
>
> V1 result: `docs/Plans/UEAGENTKIT_V1_READ_ONLY_KNOWLEDGE_BROWSER_RESULT_20260829.md` (all gates PASS)
>
> This document is the **V2-0 entry checkpoint**: it freezes the V2 read-only data/API contract and turns the Track V plan section 7 direction into an implementation-ready specification. **No V2 product code has been implemented yet.**

## 0. Executive Summary

V2 adds a Visualization / Analysis Dashboard on top of the proven V1 read-only server. Per the Track V plan execution order (`V2-0 visualization data-contract freeze`), this checkpoint freezes:

- the **data/API contract** (section 4): exact routes, parameters, bounds, response JSON, error codes, truncation shape;
- the **query mapping** (section 5): every field sourced from an existing SQLite table/column — no new data source, no hidden crawl of `Output/` JSON;
- the **UI/rendering contract** (section 6): native Canvas for the large-graph path, no npm, no build pipeline;
- the **acceptance, test, and gate plan** (sections 8-10), including the 5000-node measured performance gate and the V1 security regression.

Implementation proceeds in V2-1 (graph/impact), V2-2 (coverage/timeline/stale), V2-3 (5000-node performance gate), then full regression + V2 Result. The commit `feat: add V2 knowledge visualization dashboard` is created only after user authorization.

## 1. Entry Checkpoint State

```text
branch   feature/knowledge-web-view
HEAD     49b8fa61 feat: add V1 read-only knowledge browser   (parent 1c68f4d)
status   working tree clean
V1 gates full discovered suite 792 OK / Ruff / compileall / ValidateRelease 0.7.0 / git diff --check  all PASS
```

V2 starts from a clean tree at the V1 checkpoint commit. No Unreal / UBT / Direct Build will be started. `E:\WorkSpace\UEAgentKit-LiveWriter` is not touched.

## 2. V2 Scope (from Track V plan section 7)

Five read-only views:

```text
Asset Reference Graph        /api/graph
Impact / Consumer view       /api/impact/<asset_path>
Knowledge Coverage           /api/coverage
Change / Trust Timeline      /api/timeline
Stale Distribution           /api/stale
```

Each view must satisfy: strictly read-only, bounded, deterministic, click-through to the exact underlying record where applicable, and schema-tolerant in presentation while schema-strict at connection time.

## 3. Permanent Constraints (unchanged from Track V; non-negotiable)

```text
1. strictly read-only — no Web write path, no DB writes, no migrations
2. SQLite connection-level mode=ro (readonly=True at connect; migrate=False for asset DB)
3. localhost only (loopback addresses; non-loopback host rejected at config)
4. no POST / PUT / PATCH / DELETE application routes (always 405)
5. no Unreal Editor / UnrealEditor-Cmd / UBT / Direct Build
6. no npm / frontend build pipeline (native HTML/JS/CSS; at most one vendored visualization lib
   with license/source registration and only if measured Canvas cannot meet the perf target)
7. zero new required runtime dependencies (pyproject dependencies stays [])
8. do not touch E:\WorkSpace\UEAgentKit-LiveWriter
9. no push / rebase / tag / release; published version stays 0.7.0
```

## 4. Frozen V2 Data / API Contract

### 4.1 General rules (extend V1 conventions)

```text
- every V2 route is GET under /api/ ; mutation verbs continue to answer 405
- response JSON: UTF-8, ensure_ascii=False (Unicode safe)
- every route enforces a query-parameter whitelist; unknown params -> 400 badRequest
- collections are bounded: default limit 50, hard max 200, except /api/graph node limits (section 4.2)
- truncation is always reported in-band via "truncated", never silent
- errors reuse V1 codes: notFound(404) / badRequest(400) / memoryDatabaseMissing(500) /
  memorySchemaMismatch(500) / assetDatabaseMissing(500) / assetSchemaMismatch(500) /
  internalError(500, no traceback leak); new codes: assetNotFound(404), assetUnknown(500) reuse
- route path segments are validated (single segment, no "/")
- connections are short-lived per request (existing V1 pattern)
```

### 4.2 `GET /api/graph` — Asset Reference Graph

Bounded, root-required, direction-aware asset dependency graph. Never fetches the whole project graph.

| param | required | default | bounds | meaning |
|---|---|---|---|---|
| `root` | yes | — | non-empty asset_path (decoded) | BFS seed asset |
| `depth` | no | 1 | 0..3 | BFS hop depth (0 = root only) |
| `direction` | no | `outgoing` | `outgoing` / `incoming` / `both` | reference direction |
| `limit` | no | 300 | 1..1000 (300 default, 1000 normal cap) | max nodes returned |
| `stress` | no | 0 | 0/1 | `stress=1` raises limit max to **5000** (explicit stress path) |

Response:

```json
{
  "meta": {
    "root": "/Game/Maps/Example",
    "depth": 1,
    "direction": "outgoing",
    "nodeLimit": 300,
    "nodeCount": 12,
    "edgeCount": 18,
    "queryMs": 3,
    "truncated": false
  },
  "nodes": [
    {
      "assetPath": "/Game/Maps/Example",
      "assetClass": "World",
      "assetName": "Example",
      "packageName": "/Game/Maps",
      "referenceCount": 5,
      "root": true
    }
  ],
  "edges": [
    {
      "source": "/Game/Maps/Example",
      "target": "/Game/Blueprints/BP_Foo",
      "kinds": ["SoftObjectReference", "PropertyReference"],
      "referenceCount": 2
    }
  ],
  "truncated": null
}
```

Contract details:

```text
- nodes are assets; node identity = asset_path (unique in assets table)
- edges are aggregated reference rows: group references_table by
  (source asset_path, target asset_path), collect distinct kind set + count
- direction: outgoing = references_table.asset_id -> target_asset_path;
  incoming = reverse; both = union (self-edges counted once, flagged selfLoop:true)
- BFS stops at depth or when node limit is reached; reaching nodeLimit sets
  truncated = {"reason": "nodeLimit", "limit": <limit>, "count": <nodes returned>}
- root not found -> 404 assetNotFound
- no incoming/outgoing edges for a valid root -> nodes=[root], edges=[]
- depth=0 returns only the root node
- limit > 1000 without stress=1 -> 400 badRequest ("stress=1 required above 1000")
```

### 4.3 `GET /api/impact/<asset_path>` — Impact / Consumer view

Inbound consumers of one asset with reference kinds.

| param | required | default | bounds | meaning |
|---|---|---|---|---|
| `limit` | no | 50 | 1..200 | consumers per page |
| `offset` | no | 0 | >=0 | paging |
| `kind` | no | — | valid reference kind | filter consumers by reference kind |

Response:

```json
{
  "asset": {"assetPath": "/Game/Blueprints/BP_Foo", "assetClass": "Blueprint", "assetName": "BP_Foo"},
  "consumers": [
    {"assetPath": "/Game/Maps/Example", "assetClass": "World", "kinds": ["SoftObjectReference"], "referenceCount": 1}
  ],
  "countsByKind": {"SoftObjectReference": 1, "PropertyReference": 3},
  "totalConsumerAssets": 4,
  "truncated": null,
  "meta": {"queryMs": 2}
}
```

Contract details:

```text
- consumers = distinct inbound source assets (references_table where target_asset_path = asset_path)
- labeled "indexed dependency/reference impact", never runtime gameplay impact (plan 7.4)
- countsByKind across ALL consumers (not just the page)
- truncated when page (limit) cuts the consumer list: {"reason":"limit","limit":50,"count":50}
- missing asset -> 404 assetNotFound; empty result is valid (no consumers)
```

### 4.4 `GET /api/coverage` — Knowledge Coverage

Aggregate memory records by knowledge node. Goal: reveal blind areas; **no opaque coverage percentage**.

| param | required | default | bounds | meaning |
|---|---|---|---|---|
| `limit` | no | 50 | 1..200 | nodes per page |
| `offset` | no | 0 | >=0 | paging |
| `pathPrefix` | no | — | string | filter knowledge_nodes by path prefix (subtree) |

Response:

```json
{
  "nodes": [
    {
      "nodeId": "kn_...",
      "path": "/project/combat",
      "nodeType": "system",
      "title": "Combat",
      "recordCount": 12,
      "validCount": 8,
      "staleCount": 2,
      "conflictedCount": 0,
      "supersededCount": 1,
      "unverifiedCount": 1,
      "lastUpdatedUtc": "2026-08-29T20:00:00Z"
    }
  ],
  "totals": {"recordCount": 120, "validCount": 95, "staleCount": 10, "conflictedCount": 2, "supersededCount": 8, "unverifiedCount": 5},
  "meta": {"queryMs": 3, "truncated": null}
}
```

Contract details:

```text
- one row per knowledge_nodes entry; counts = memory_records grouped by node_id (status split)
- nodes with zero records are INCLUDED (recordCount 0) so blind areas are visible
- lastUpdatedUtc = MAX(memory_records.updated_at_utc) per node, null when no records
- totals computed over the filtered set (pathPrefix) BEFORE paging; "truncated" mirrors page cuts
- a percentage is never emitted by the server; the UI may show "x/y valid" text when recordCount > 0
```

### 4.5 `GET /api/timeline` — Change / Trust Timeline

Only facts present in the current read model (plan 7.6). Schema-tolerant presentation, strict connection.

| param | required | default | bounds | meaning |
|---|---|---|---|---|
| `limit` | no | 50 | 1..200 | events per page |
| `offset` | no | 0 | >=0 | paging |
| `recordType` | no | — | valid record_type | filter |
| `status` | no | — | valid status | filter |
| `includeStatusEvents` | no | false | true/false | merge memory_status_events |

Response:

```json
{
  "events": [
    {
      "eventId": "rec_abc#updated",
      "kind": "recordUpdated",
      "timestampUtc": "2026-08-29T20:00:00Z",
      "recordId": "rec_abc",
      "recordType": "projectFact",
      "status": "valid",
      "sourceKind": "tool-observed",
      "titlePreview": "Ground character contact...",
      "fromStatus": null,
      "toStatus": null
    }
  ],
  "meta": {"queryMs": 3, "truncated": null}
}
```

Contract details:

```text
- kind "recordUpdated": memory_records ordered updated_at_utc DESC
- kind "statusChanged" (only when includeStatusEvents=true): memory_status_events joined to
  memory_records, timestamp = changed_at_utc; fromStatus/toStatus filled
- events merged and sorted DESC by timestampUtc; deterministic tie-break by recordId
- titlePreview truncated to 280 chars (reuse V1 DEFAULT_RECORD_PREVIEW_CHARS)
- if a desired dimension (e.g. L0 capture) is not in the current SQLite baseline,
  it is simply absent — never synthesized (plan 7.6, M2 dependency documented only)
```

### 4.6 `GET /api/stale` — Stale Distribution

Stale/conflicted/superseded records grouped by a chosen dimension, with click-through.

| param | required | default | bounds | meaning |
|---|---|---|---|---|
| `groupBy` | no | `nodePath` | `nodePath` / `scope` / `recordType` / `ageBucket` | grouping dimension |
| `limit` | no | 50 | 1..200 | buckets per page |
| `offset` | no | 0 | >=0 | paging |

Response:

```json
{
  "buckets": [
    {
      "groupKey": "/project/combat",
      "label": "/project/combat",
      "recordCount": 9,
      "byStatus": {"stale": 6, "conflicted": 2, "superseded": 1},
      "ageBuckets": {"0-7d": 3, "8-30d": 4, "31-90d": 2, "90d+": 0},
      "sampleRecordIds": ["rec_1", "rec_2", "rec_3"]
    }
  ],
  "totals": {"recordCount": 41, "byStatus": {"stale": 30, "conflicted": 5, "superseded": 6}},
  "meta": {"queryMs": 4, "truncated": null}
}
```

Contract details:

```text
- input set = memory_records WHERE status IN ('stale','conflicted','superseded') [for the project]
- groupKey by dimension:
    nodePath   = knowledge_nodes.path via node_id (records without node -> groupKey "<unattached>")
    scope      = memory_scopes.scope_key via memory_records link (scope column per memory schema)
    recordType = record_type
    ageBucket  = bucket(updated_at_utc age): frozen buckets 0-7d / 8-30d / 31-90d / 90d+
- age computed from updated_at_utc vs server now (UTC); bucket boundaries documented
- sampleRecordIds = first 5 record ids per bucket (bounded) -> UI links to /api/record/<id>
- totals computed over the filtered set BEFORE paging; truncated mirrors page cuts
- groupBy=scope requires the memory_records<->memory_scopes link columns as defined in
  memory_schema.py; if a scope link is absent in a known schema version, that grouping
  returns a documented empty result instead of guessing columns (schema-tolerance rule)
```

### 4.7 Truncation & error summary

```text
truncated: null | {"reason": "nodeLimit"|"limit", "limit": int, "count": int}
errors (new): assetNotFound(404)  for /api/graph root / /api/impact asset
errors (reused): badRequest / notFound / memoryDatabaseMissing / memorySchemaMismatch /
                 assetDatabaseMissing / assetSchemaMismatch / internalError
```

### 4.8 Schema-tolerance rule (presentation vs connection)

```text
- connection: strict, unchanged from V1 — memory DB opened readonly=True with
  CURRENT_MEMORY_SCHEMA_VERSION check; asset DB opened readonly=True, migrate=False with
  CURRENT_SCHEMA_VERSION check. Unknown/newer schema version -> memorySchemaMismatch /
  assetSchemaMismatch (existing V1 behavior). No guessing of unknown columns.
- presentation: V2 code reads only columns listed in section 5; any column absent in a
  known schema version is handled explicitly (documented empty/missing), never synthesized.
```

## 5. Data-Source / Query Mapping (all existing SQLite)

| view | source DB | tables/views | columns read |
|---|---|---|---|
| graph | asset index | `assets`, `references_table` | assets(asset_path, asset_name, asset_class, package_name); references_table(asset_id, kind, target_asset_path, source_symbol_id, target_symbol_id) |
| impact | asset index | `references_table`, `assets` | same + target_kind; reuse `queries.find_references` semantics (bounded) |
| coverage | Memory | `knowledge_nodes`, `memory_records` | knowledge_nodes(node_id, path, node_type, title); memory_records(node_id, status, updated_at_utc) |
| timeline | Memory | `memory_records`, `memory_status_events` | memory_records(record_id, record_type, status, source_kind, title, updated_at_utc); memory_status_events(record_id, from_status, to_status, changed_at_utc) |
| stale | Memory | `memory_records`, `knowledge_nodes`, `memory_scopes` | memory_records(status, node_id, record_type, updated_at_utc); knowledge_nodes.path; memory_scopes link |

Existing read primitives reused where possible: `queries.find_references` / `get_asset` (bounded), `project_memory.get_memory_record` / `open_project_memory_database(readonly=True)`, `memory_tree` node helpers, `memory_reports` payload patterns. New aggregation SQL is written only against the columns above, inside short-lived read-only connections.

## 6. UI / Rendering Contract

```text
- single static src/ue_agent_kit/web/index.html, native JS/CSS, no npm/build
- V1 tabs (Tree / Records / Work / Evidence / Search) keep working; V2 adds tabs:
  Graph, Impact, Coverage, Timeline, Stale
- Graph tab: native <canvas> renderer for the node graph (required for the 5000-node path);
  no DOM/SVG node-per-asset rendering at scale. Camera pan/zoom; click node -> Impact view
- Impact/Coverage/Timeline/Stale: table/list renderers with bounded paging controls;
  every record reference links to /api/record/<id>
- startup performs NO full-project fetch: dashboard initial data = /api/status +
  a bounded first tab query only (perf gate, section 8)
- a visualization library is only proposed if measured Canvas cannot meet the perf target;
  then exactly one vendored lib with license/source registration, still no npm
```

## 7. Implementation Shape

```text
src/ue_agent_kit/knowledge_view.py
    extend KnowledgeViewReadService:
      graph(root, depth, direction, limit, stress)
      impact(asset_path, limit, offset, kind)
      coverage(limit, offset, path_prefix)
      timeline(limit, offset, record_type, status, include_status_events)
      stale(group_by, limit, offset)
    extend route dispatch + _QUERY_PARAMS whitelist for the 5 new routes
src/ue_agent_kit/web/index.html
    V2 tabs + Canvas graph renderer (existing V1 file, no new runtime deps)
tests/python/test_knowledge_view_visualization.py   (new)
scripts/benchmark_knowledge_view.py                 (new, deterministic perf gate)
docs/Plans/UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_DETAILED_PLAN_20260829.md  (this document)
docs/Plans/UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_RESULT_<date>.md           (at V2 end)
```

No change to `pyproject.toml` dependencies. No CLI subcommand change required (same `knowledge-view` server).

## 8. Acceptance Criteria

### 8.1 Functional (per view)

```text
[ ] /api/graph bounded: root required, depth 0..3, direction filter, node cap with
    in-band truncated, stress=1 unlocks 5000, root missing -> 404 assetNotFound
[ ] /api/impact returns inbound consumers + countsByKind + bounded expansion,
    labeled indexed impact
[ ] /api/coverage includes zero-record nodes, never emits an opaque percentage,
    totals before paging
[ ] /api/timeline only current-read-model facts; statusChanged only when requested;
    schema-tolerant (no unknown-column guessing)
[ ] /api/stale groups by nodePath/scope/recordType/ageBucket, sampleRecordIds link
    to exact records, click-through works
```

### 8.2 Performance (Track V plan 7.8 — deterministic measurements required)

```text
[ ] default dashboard initial data response < 500 ms on acceptance DB
[ ] record/tree normal interactions do not fetch unbounded collections
[ ] 5000-node graph loads and interacts without browser hang
[ ] graph request is server-side bounded and reports truncation when capped
[ ] no full-project graph automatically fetched at startup
[ ] 5000-node run records: node count, edge count, JSON bytes, server query elapsed ms,
    client render elapsed ms, interaction FPS/frame-time sample, browser/machine environment
```

`scripts/benchmark_knowledge_view.py` produces a deterministic JSON report (repo convention
mirrors `run_agent_reliability_benchmark.py`); raw measurements are recorded in the V2 Result.

### 8.3 Security regression (Track V plan 7.9 — re-run every V1 assertion)

```text
[ ] still localhost only
[ ] still DB mode=ro at connection level
[ ] still no mutation endpoint (405 for POST/PUT/PATCH/DELETE on V1 AND V2 routes)
[ ] still no runtime Python dependency (pyproject dependencies = [])
[ ] no npm
[ ] no Unreal/UBT
[ ] new routes: param whitelist, single-segment path validation, no traceback leak,
    no arbitrary filesystem static serving (route whitelist unchanged)
```

## 9. Test Plan

```text
- new file tests/python/test_knowledge_view_visualization.py
- deterministic fixture DBs (memory + asset) built in test setup, same style as V1
- test matrix:
    graph: bounds (limit/depth/direction/stress), truncation, root-missing 404,
            aggregation correctness (kinds set, counts), self-loop, empty-result,
            read-only proof on every new route (DB unchanged, SHA/row-count/data_version)
    impact: consumers, countsByKind, kind filter, paging truncation, empty consumers
    coverage: status splits, zero-record nodes, pathPrefix, totals-before-paging
    timeline: recordUpdated default, statusChanged opt-in, merge sort, filter, preview truncation
    stale: each groupBy dimension, age buckets, sampleRecordIds bound, unattached records
    security: 405 on every new route, unknown params 400, schema-mismatch codes, Unicode
- the actual discovered suite count is recorded at V2 start and end (never hard-coded)
```

## 10. Gate Plan (identical to V1, serial)

```text
1. .venv/Scripts/python.exe -m unittest discover -s tests/python -p "test_*.py"   (record actual count)
2. .venv/Scripts/python.exe -m ruff check src tests/python
3. .venv/Scripts/python.exe -m compileall -q src/ue_agent_kit
4. PYTHONPATH=src .venv/Scripts/python.exe scripts/ValidateRelease.py              (full, no --skip-*)
5. git diff --check
```

Expected state at V2 end: all five gates PASS; `pyproject.toml` dependencies still `[]`;
`git status` clean; V2 Result document records factual PASS/blocked evidence.

## 11. Stop Conditions (Track V plan section 10, plus V2-specific)

```text
- Web requires writable DB access to satisfy a view
- Web startup requires schema migration
- implementation wants to launch Unreal for data
- V2 needs a new background export/copy pipeline
- route design exposes arbitrary filesystem paths
- localhost-only conflicts with a desired deployment
- zero-runtime-dependency appears impossible
- another Agent's uncommitted work must be copied to continue
- unknown newer Memory schema is being guessed instead of explicitly supported
- a view requires fetching the full project graph (unbounded)
```

A missing view is preferable to weakening the read-only boundary.

## 12. Execution Order

```text
V2-0 contract freeze + this detailed plan            (THIS checkpoint)
    ↓
V2-1 /api/graph + /api/impact + Canvas graph tab
    ↓
V2-2 /api/coverage + /api/timeline + /api/stale + tabs
    ↓
V2-3 5000-node measured performance gate (benchmark script + Result evidence)
    ↓
V2 full regression + V2 Result document + user review
    ↓ (only on user authorization)
commit: feat: add V2 knowledge visualization dashboard
```

V2-1 / V2-2 / V2-3 are implemented and verified incrementally; gates in section 10 run at the end (and after V2-3 before Result).

## 13. Commit Boundary / Authorization

```text
- no commit is created by this checkpoint
- on V2 completion and user authorization: single checkpoint commit
  message: feat: add V2 knowledge visualization dashboard
- no push / rebase / tag / release; published version remains 0.7.0
- docs/Plans/README.md gains the V2 plan + V2 result rows (same pattern as V1)
```

## 14. Explicit Decisions (frozen, change only with user approval)

```text
D1. graph node identity = asset_path (asset-level graph, not symbol-level; symbol-level
     remains reachable via V1 record/evidence detail)
D2. node limits: default 300 / normal cap 1000 / stress cap 5000 (stress=1 required >1000)
D3. coverage emits counts only; no server-side percentage; UI shows "x/y valid" text
D4. timeline is record-update based; status-change events opt-in via includeStatusEvents
D5. stale age buckets frozen: 0-7d / 8-30d / 31-90d / 90d+
D6. "scope" grouping depends on the memory_records<->memory_scopes link as defined in
     memory_schema.py; if absent in a known schema version, documented empty result
D7. no new runtime dependency; Canvas-first rendering; single vendored lib only as
     measured fallback with license registration
```

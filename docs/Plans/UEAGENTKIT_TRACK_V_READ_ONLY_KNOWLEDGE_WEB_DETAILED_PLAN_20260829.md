# UEAgentKit Track V — Read-only Knowledge Web Detailed Plan

> Date: 2026-08-29
>
> Track: V — Knowledge Web / Read-only Visualization
>
> Worktree: `E:\WorkSpace\UEAgentKit-KnowledgeWeb`
>
> Branch: `feature/knowledge-web-view`
>
> Entry HEAD: `1c68f4d` (`docs: add D1 agent workflow split plan`)
>
> W4 closure checkpoint included in this baseline: `24bf088` (`docs: close W4 full acceptance and documentation`)
>
> Latest published product remains `0.7.0` for UE5.6. This Track does not authorize Push / Rebase / Tag / Release or version changes.

## 0. Executive Summary

Track V has **two formal implementation stages**:

```text
V1  Local Read-only Knowledge Browser
→
V2  Read-only Visualization / Analysis Dashboard
```

`D4 Generated API Reference` may later be surfaced inside the same Web UI, but D4 belongs to the horizontal D track and depends on D2. It is **not V3** and must not be pulled into V1 merely to make the UI look more complete.

Track V is intentionally independent from the current Writer/D1 mainline. Its strongest architectural property is:

```text
Track V never needs Unreal Editor.
```

Therefore V may run on the same machine while another Agent owns Unreal for W5/C1/X1/X4 acceptance, as long as V does not start Unreal, UBT, Direct Build, fixture reset, or snapshot refresh.

## 1. Project Background for a New Agent

UEAgentKit is an Unreal Engine project-understanding and safe Agent-operation toolkit. It is **not** intended to be an unrestricted Unreal Editor remote-control layer.

The product direction is:

```text
UE project knowledge / context
+ deterministic Asset / Blueprint semantics
+ Policy / Revision gated changes
+ resident Editor narrow writes
+ exact Undo / Save / Verify / Recovery evidence
+ Revision-aware Project Memory
+ Agent-facing analysis / Trust workflow
```

Core design principle:

```text
understand first
→ narrow authorized mutation
→ exact evidence
→ independent verification
→ recoverable state
```

The Writer line is already mature:

```text
W0  latency / resident baseline                    complete
W1  Blueprint narrow resident write                complete
W2  Fast Resident Verify                           complete
W3  Checkpoint Strong Verify                       complete
W4  Multi-operation / multi-asset bounded batch    complete
```

W4 final real UE5.6 status:

```text
C1-C12                       PASS
W4-6 H1-H6                   PASS
Python discovered suite      766 / 766 PASS at W4 closure
Ruff / compileall            PASS
ValidateRelease 0.7.0        PASS
UE5.6 Direct Build           PASS at 55919bd
final fixture reload         2 / 2 PASS
```

W4 materially reduced Agent orchestration overhead without reducing safety:

```text
B0 public MCP calls  19 → 7
B1 public MCP calls  27 → 8
```

Current post-W4 mainline is D1 (`agent_workflow.py` pure split), followed by W5 real-project acceptance. D1 is being performed in another worktree and is **not a dependency of Track V**.

Track V solves a different problem: the project already has structured Asset/Memory/Active Work data, but a human currently has no convenient local visual entry point for inspecting it.

## 2. Repository / Parallel-work Baseline

This Track has its own worktree:

```text
E:\WorkSpace\UEAgentKit-KnowledgeWeb
feature/knowledge-web-view
HEAD 1c68f4d
```

The Writer/D1 Agent is working separately in:

```text
E:\WorkSpace\UEAgentKit-LiveWriter
feature/live-writer-expansion
```

At Track V creation time, that worktree contains uncommitted D1 implementation files. **Do not modify, reset, clean, stash, checkout, or otherwise manipulate that worktree.**

Cross-Track synchronization rules:

```text
no Rebase of shared branches
no copying uncommitted files between worktrees
only consume another Track after an explicit committed checkpoint
no Push / Tag / Release unless separately authorized
```

Track V should normally need no Writer-side checkpoint until it is ready to consume later Memory schema changes.

## 3. Same-machine Resource Isolation

The machine is shared by multiple Agents. Unreal is an exclusive resource.

Track V resource contract:

```text
MUST NOT start:
  UnrealEditor.exe
  UnrealEditor-Cmd.exe
  UnrealBuildTool
  BuildPluginDirect.cmd
  WriteFixturePlan / fixture Reset
  real UE acceptance scripts
  asset snapshot refresh/export requiring Unreal

MAY use:
  Python
  unittest
  Ruff
  compileall
  SQLite read-only queries
  localhost HTTP server
  browser
  static HTML / JS / CSS
```

This means V1/V2 can continue while W5 or another Track owns the UE Editor lease.

If a future V change unexpectedly requires C++ or Unreal, stop and re-plan. That is a scope violation, not a reason to silently compete for the Editor.

## 4. Permanent Architectural Constraints

These are not temporary V1 compromises.

### 4.1 Web is strictly read-only

The user-facing Knowledge Web must never become a manual database editor.

```text
human browser = inspect / search / navigate / visualize
Agent         = performs authorized knowledge writes through existing Agent surfaces
```

Required implementation properties:

```text
SQLite opened read-only
no schema migration from Web startup
no INSERT / UPDATE / DELETE / REPLACE / writable PRAGMA path
no POST / PUT / PATCH / DELETE application endpoint
no UI edit controls
no "quick fix" database mutation button
```

When data should be changed, UI may show a copyable Agent instruction, but it must not perform the write itself.

### 4.2 Localhost only

```text
bind address = 127.0.0.1
never default to 0.0.0.0 / LAN exposure
```

This is a local development browser, not a production Web service.

### 4.3 Zero new runtime dependencies

Current `pyproject.toml`:

```text
dependencies = []
```

V1 must preserve this.

Use:

```text
Python stdlib http.server / socketserver / sqlite3
native HTML / CSS / JavaScript
```

No FastAPI / Flask / uvicorn / starlette / npm / frontend build tool.

V2 may vendor one small browser-side visualization library only if native Canvas/SVG cannot meet the measured graph target. If vendored, source/license must be registered according to repository reference policy; still no npm/runtime dependency.

### 4.4 No duplicated data store

Do not create a second Web database or export pipeline.

The Web layer reads the existing authoritative SQLite stores directly:

```text
Asset/index DB     DEFAULT_DATABASE / UEAK_DATABASE
Memory DB          DEFAULT_MEMORY_DATABASE / UEAK_MEMORY_DATABASE
```

No periodic JSON mirror, no sync daemon, no cache database.

## 5. Current Code Facts the Agent Must Understand

### 5.1 Existing Memory database

Default path is defined in `src/ue_agent_kit/config.py`:

```text
.data/ue_agent_kit_memory.sqlite3
UEAK_MEMORY_DATABASE override supported
```

Project key comes from existing fixed-project configuration / `UEAK_PROJECT_KEY`.

Existing schema already contains the main V1 data:

```text
memory_records
memory_scopes
memory_revisions
memory_artifacts
memory_relations
memory_status_events
memory_records_fts
knowledge_nodes
active_work_items
active_work_node_links
active_work_asset_links
active_work_todos
```

Memory status already distinguishes:

```text
valid
stale
conflicted
superseded
unverified
```

These states must remain visible in the UI. Never flatten everything into "memory exists".

### 5.2 Important read primitives already exist

Useful modules:

```text
project_memory.py
  get_memory_record
  list_memory_records
  search_memory_records
  open_project_memory_database(..., readonly=True)

memory_tree.py
  get/list/search knowledge nodes

active_work.py
  get/list work items

memory_context.py
  evidence payload / bounded context helpers

memory_reports.py
  stable payload/report serialization patterns

queries.py
  Asset / Symbol / Reference query primitives
```

Important warning:

`ProjectMemoryService` currently opens `open_project_memory_database()` without `readonly=True` in normal methods, because that service also owns write operations and migration behavior.

**V1 must not simply instantiate `ProjectMemoryService` and call its normal methods as the Web backend.**

Preferred design:

```text
new KnowledgeViewReadService
→ open_project_memory_database(path, readonly=True)
→ call existing pure read primitives where possible
→ open asset index with readonly=True, migrate=False
```

This makes read-only behavior true at the SQLite connection level, not merely a promise at the HTTP layer.

## 6. V1 — Local Read-only Knowledge Browser

### 6.1 Goal

Provide a useful local browser for the existing knowledge system without Unreal and without modifying the DB.

Target command:

```text
ue-agent knowledge-view \
  --project-key <fixed-project-key> \
  --memory-database <path> \
  --database <asset-index-path> \
  --port 8765
```

Defaults should reuse existing config/environment variables.

Required default:

```text
host = 127.0.0.1
```

Do not add a public `--host 0.0.0.0` convenience path in V1. If a host option exists for testing, reject non-loopback addresses in product execution.

### 6.2 Suggested code shape

```text
src/ue_agent_kit/knowledge_view.py
  KnowledgeViewConfig
  KnowledgeViewReadService
  route parsing
  JSON response helpers
  HTTP handler/server lifecycle

src/ue_agent_kit/web/index.html
  one static app
  native JS/CSS

src/ue_agent_kit/cli.py
  knowledge-view command only

tests/python/test_knowledge_view.py
  HTTP + read-only + routing tests
```

Keep transport, SQL/read model, and browser UI reasonably separated inside the module; do not put raw SQL directly in request handlers.

### 6.3 V1 views

Four required views:

#### A. Knowledge Tree

```text
left: hierarchical knowledge_nodes tree
right: selected node detail
       summary / type / path
       attached records
       child count
```

Requirements:

- lazy-expand nodes;
- do not load the entire tree on first page load;
- stable ordering by path/title/id;
- show record status chips.

#### B. Memory Records

Filters:

```text
record type
status
source kind
subject/query
node
```

Display:

```text
title
subject
record type
source
status
observed/updated time
short body preview
```

Opening a record shows:

```text
full body
evidence digest
revision set
scopes
artifacts
relations
superseded-by
status history
```

#### C. Active Work

Show existing work state:

```text
goal / title
status
blocked reason
next action
TODOs
linked knowledge nodes
linked asset paths
```

This is inspection only. No "complete", "resume", "edit todo" buttons.

#### D. Evidence

The record detail must make provenance understandable:

```text
source kind
source ref
confidence
contentSha256
evidenceSha256
revision bindings
artifacts
relations
status history
```

If an artifact reference points outside the Web-supported read model, display the reference text; do not turn it into arbitrary filesystem file serving.

### 6.4 V1 HTTP API

Recommended minimum API:

```text
GET /api/status
GET /api/tree?parent=<node_id>&limit=<n>
GET /api/node/<node_id>
GET /api/records?query=&type=&status=&source=&node=&limit=&offset=
GET /api/record/<record_id>
GET /api/work?status=&limit=&offset=
GET /api/work/<work_item_id>
```

Optional if it simplifies UI:

```text
GET /api/search?q=...
```

Do not add mutation endpoints.

Bound every collection route. Recommended:

```text
default page = 50
hard page limit = 200
```

For query parameters:

- explicit allowlist;
- reject unknown enum values;
- no SQL fragments supplied by client;
- all values parameterized.

### 6.5 Static serving / routing safety

Use route whitelist, not a general filesystem handler.

Allowed static resources should be known package files only, e.g.:

```text
/
/index.html
```

If CSS/JS later become separate files, map exact known names.

Never map URL path directly to a disk path.

Required HTTP behavior:

```text
unknown route        404 JSON/text
unsupported method   405
bad input            400
missing record       404
DB missing           clear startup/error response, no traceback leak
schema mismatch      clear read-only compatibility error
```

### 6.6 Live-read semantics

The browser should not hold one long-lived SQLite snapshot forever.

Preferred model:

```text
request
→ open short-lived readonly connection
→ query
→ close
```

This allows Memory Agent writes in another process to become visible on subsequent requests without a reload daemon.

The server must not run migrations if another Track upgrades Memory schema. On unsupported schema:

```text
fail clearly / show schema mismatch
```

Do not auto-upgrade from the Web process.

### 6.7 V1 acceptance

Security / architecture:

```text
[ ] binds only 127.0.0.1
[ ] memory DB connection is readonly at SQLite level
[ ] asset DB connection is readonly / migrate=False
[ ] Web startup performs zero migrations and zero writes
[ ] no application POST/PUT/PATCH/DELETE route
[ ] no arbitrary filesystem static serving
[ ] dependencies remains []
[ ] no npm/build step
[ ] no Unreal/UBT process required
```

Functional:

```text
[ ] Knowledge Tree lazy navigation works
[ ] record list/filter/detail works
[ ] stale/conflicted/superseded/unverified are visibly distinct
[ ] Active Work displays goal/TODO/blocker/next-action/link data
[ ] Evidence view exposes revision/artifact/relation/status-history data
[ ] Unicode / Chinese paths and text round-trip correctly
[ ] pagination is deterministic
[ ] DB missing/schema mismatch produces a useful error rather than crash
```

Read-only proof:

Create a deterministic test DB, record before hashes/counts/`PRAGMA data_version` as appropriate, exercise every V1 route, then prove the DB file contents/rows are unchanged.

At minimum include a test that opens the underlying connection as truly read-only and confirms an attempted write fails with SQLite readonly error.

### 6.8 V1 deliverables

```text
src/ue_agent_kit/knowledge_view.py
src/ue_agent_kit/web/index.html
src/ue_agent_kit/cli.py
tests/python/test_knowledge_view.py
docs/Plans/UEAGENTKIT_V1_READ_ONLY_KNOWLEDGE_BROWSER_RESULT_<date>.md
```

Recommended commit boundary after user authorization:

```text
feat: add V1 read-only knowledge browser
```

Do not start V2 until V1 read-only proof and full Python gates pass.

## 7. V2 — Visualization / Analysis Dashboard

### 7.1 Goal

Add visual analysis on top of the already-proven V1 read-only server. V2 must not introduce a new data source or write path.

Required views from the Master/Midterm direction:

```text
Asset Reference Graph
Impact / Consumer View
Knowledge Coverage
Change / Trust Timeline
Stale Distribution
```

### 7.2 Data-source rule

Use existing SQLite only.

```text
Asset graph / references     asset index DB
Memory coverage/status       Memory DB
Active Work                  Memory DB
Trust/change timeline        data already represented in current DB/memory records
```

Do not crawl `Output/` JSON trees as a hidden second database just to make a chart richer.

If a desired timeline dimension is not represented in the current SQLite baseline, show the subset actually available and document the dependency on later M2. Do not invent a new ingestion pipeline inside Track V.

### 7.3 Reference graph

The UI must not dump the whole project graph by default.

Server-side graph request should require a bounded root/filter:

```text
asset root / search seed
max depth
max nodes
reference direction
```

Suggested limits:

```text
default nodes   <= 300
normal hard cap <= 1000
explicit stress cap = 5000
```

For the 5000-node acceptance path use Canvas/WebGL-like Canvas rendering rather than thousands of DOM/SVG nodes.

Prefer native Canvas first. If measured interaction cannot meet the target, a single vendored lightweight visualization library may be proposed, with license/source registration and no npm. Do not vendor first and justify later.

### 7.4 Impact / Consumer view

Purpose:

```text
select asset
→ show inbound consumers / reference kinds
→ allow bounded expansion
```

Reuse index reference semantics. Do not pretend this view is runtime gameplay impact; label it as indexed dependency/reference impact.

### 7.5 Knowledge Coverage

Aggregate memory records by knowledge node / asset-directory scope:

```text
record count
valid count
stale/conflicted count
last observed/updated time
```

Goal is to reveal blind areas, not produce a fake quality score.

Avoid one opaque "coverage percentage" unless its denominator and formula are explicit.

### 7.6 Change / Trust Timeline

Show only facts present in the current read model:

```text
record timestamp
record type
status
source/evidence
Trust-related record/artifact when available
```

After Track M M2 adds structured L0 capture, this page can become richer without changing the V architecture.

V2 must therefore be schema-tolerant in presentation but schema-strict at connection time: support known schema versions explicitly; never guess unknown columns.

### 7.7 Stale distribution

Show stale/conflicted/superseded records grouped by:

```text
node path
asset/directory scope
record type
age bucket
```

Click-through must land on the exact underlying record/evidence.

### 7.8 V2 performance acceptance

Required deterministic measurements:

```text
[ ] default dashboard initial data response < 500 ms on acceptance DB
[ ] record/tree normal interactions do not fetch unbounded collections
[ ] 5000-node graph can be loaded and interacted with without browser hang
[ ] graph request is server-side bounded and reports truncation when capped
[ ] no full-project graph is automatically fetched at startup
```

For the 5000-node graph record at least:

```text
node count
edge count
JSON bytes
server query elapsed ms
client render elapsed ms
interaction FPS or frame-time sample
browser/machine environment
```

Do not report "smooth" without measurements.

### 7.9 V2 security regression

Re-run every V1 read-only assertion.

```text
[ ] still localhost only
[ ] still DB mode=ro
[ ] still no mutation endpoint
[ ] still no runtime Python dependency
[ ] no npm
[ ] no Unreal/UBT
```

### 7.10 V2 deliverables

Likely:

```text
src/ue_agent_kit/knowledge_view.py
src/ue_agent_kit/web/index.html
tests/python/test_knowledge_view.py
tests/python/test_knowledge_view_visualization.py   optional split
docs/Plans/UEAGENTKIT_V2_KNOWLEDGE_VISUALIZATION_RESULT_<date>.md
benchmarks / deterministic JSON only if repository conventions support a tracked benchmark fixture/report
```

Recommended commit boundary after user authorization:

```text
feat: add V2 knowledge visualization dashboard
```

## 8. D4 Relationship — Do Not Pull Forward

D4 is:

```text
Generated API / Tool Reference
```

It can eventually appear as another tab in Knowledge Web, but its authoritative dependency is D2 Tool-count/single-source metadata.

Therefore:

```text
V1/V2 may reserve navigation space conceptually
V1/V2 must not duplicate Tool Registry documentation generation
D4 starts only after D2 contract is available
```

This prevents V from creating a second manually maintained API catalog.

## 9. Track V Testing / Gates

Track V should remain a no-UE stage.

Before implementation:

```text
git status
git log -1
read current project handoff
read Plans/README
read this plan
run actual discovered Python baseline
```

At V1 and V2 completion:

```text
Python full discovered suite     PASS
Ruff                             PASS
compileall                       PASS
ValidateRelease 0.7.0            PASS
git diff --check                 PASS
pyproject dependencies           still []
UE5.6 Direct Build               not required if no C++ touched
real UE acceptance               not required / must not be introduced
```

Never hard-code `766` as a permanent expected test count. Use the actual discovered suite at the stage start/end and record the result.

Browser-specific tests should start the server on an ephemeral loopback port rather than fixed 8765 to avoid colliding with a developer instance.

## 10. Stop Conditions

Stop implementation and report a blocker instead of broadening scope if any of these occur:

```text
Web requires writable DB access to satisfy a view
Web startup requires schema migration
implementation wants to launch Unreal for data
V2 needs a new background export/copy pipeline
route design exposes arbitrary filesystem paths
localhost-only requirement conflicts with a desired deployment
zero-runtime-dependency requirement appears impossible
another Agent's uncommitted work must be copied to continue
unknown newer Memory schema is being guessed instead of explicitly supported
```

A missing view is preferable to weakening the read-only boundary.

## 11. Recommended Execution Order

```text
V0  baseline + read-only architecture proof
    ↓
V1-1 readonly data service
    ↓
V1-2 HTTP route layer
    ↓
V1-3 Knowledge Tree + Records
    ↓
V1-4 Active Work + Evidence
    ↓
V1-5 security/read-only tests + full gates
    ↓
V1 Result + checkpoint commit (only when authorized)
    ↓
V2-0 visualization data-contract freeze
    ↓
V2-1 reference/impact graph
    ↓
V2-2 coverage/stale/timeline views
    ↓
V2-3 5000-node measured performance gate
    ↓
V2 full regression / Result
```

Do not implement V1 and V2 in one large unreviewable change.

## 12. Track V Definition of Done

Track V is complete only when:

```text
[ ] V1 local browser complete
[ ] V2 visualization dashboard complete
[ ] all DB access is physically read-only
[ ] no manual knowledge edit UI exists
[ ] server binds only to loopback
[ ] no application mutation HTTP methods exist
[ ] no runtime dependency added
[ ] no npm/build pipeline added
[ ] no Unreal/UBT dependency introduced
[ ] Knowledge Tree / Records / Active Work / Evidence usable
[ ] Reference / Impact / Coverage / Timeline / Stale views usable
[ ] large graph has measured bounded behavior
[ ] Unicode paths/content verified
[ ] full Python/release gates green
[ ] V1 and V2 Result documents contain factual PASS/blocked evidence
```

## 13. Exact Takeover Procedure for the New V Agent

The Agent receiving this task should begin with exactly this sequence:

```text
1. cd E:\WorkSpace\UEAgentKit-KnowledgeWeb
2. confirm branch == feature/knowledge-web-view
3. inspect git status and HEAD
4. read:
   E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Handoffs\UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
   E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\README.md
   E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
   E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
   E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md
   E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_TRACK_V_READ_ONLY_KNOWLEDGE_WEB_DETAILED_PLAN_20260829.md
5. inspect current Memory schema/read APIs and CLI before coding
6. record actual Python discovered baseline
7. implement V1 only
8. do not touch E:\WorkSpace\UEAgentKit-LiveWriter or another Agent's files
9. do not start Unreal / UBT / Direct Build
10. stop after V1 Result for review unless user explicitly allows automatic continuation to V2
```

### 13.1 Copy-paste startup prompt for a fresh Agent

```text
You are taking over UEAgentKit Track V (Read-only Knowledge Web).

Work ONLY in:
E:\WorkSpace\UEAgentKit-KnowledgeWeb

Expected branch:
feature/knowledge-web-view

First inspect the actual git status and HEAD. Do not assume they are unchanged, and do not touch any other UEAgentKit worktree, especially E:\WorkSpace\UEAgentKit-LiveWriter.

Then read these files from this exact worktree, in order:
1. E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Handoffs\UEAGENTKIT_CURRENT_DEVELOPMENT_HANDOFF_20260828.md
2. E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\README.md
3. E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_MASTER_DEVELOPMENT_PLAN_20260827.md
4. E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_MIDTERM_EXECUTION_SPEC_20260827.md
5. E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_W4_MULTI_OPERATION_BOUNDED_BATCH_RESULT_20260829.md
6. E:\WorkSpace\UEAgentKit-KnowledgeWeb\docs\Plans\UEAGENTKIT_TRACK_V_READ_ONLY_KNOWLEDGE_WEB_DETAILED_PLAN_20260829.md

Implement V1 according to the Track V Detailed Plan. Track V is strictly read-only, localhost-only, zero-runtime-dependency, and must not start Unreal Editor, UnrealEditor-Cmd, UnrealBuildTool, Direct Build, fixture Reset, or snapshot refresh. The Web backend must open SQLite read-only at the connection level and must not run migrations. Do not implement V2 until V1 acceptance and Result are complete. Do not commit, push, rebase, tag, release, or change the published version unless the user explicitly authorizes it.
```

## 14. Key Context to Preserve in Handoff

If a new Chat takes over mid-Track, preserve these facts:

```text
Track V formal stages = V1 + V2 only
branch = feature/knowledge-web-view
worktree = E:\WorkSpace\UEAgentKit-KnowledgeWeb
baseline contains W4 closure 24bf088
D1 is independent and may be active in another worktree
V does not need D1
V must not use Unreal Editor
Web DB access must be SQLite readonly at connection level
human Web UI never writes knowledge
no new runtime dependency / no npm
D4 is separate and waits for D2
```

Current repository facts and committed Result documents override old conversation history.

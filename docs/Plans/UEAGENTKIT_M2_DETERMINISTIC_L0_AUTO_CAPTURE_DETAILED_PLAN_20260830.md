# UEAgentKit M2 Deterministic L0 Automatic Capture — Detailed Plan

> Date: 2026-08-30
>
> Branch: `feature/memory-context`
>
> Baseline: `6d9cf711f368f359fc8f2343e1a065942f8f58f5` (`feat: close M1 memory efficiency budget`)
>
> Stage state: **READY FOR IMPLEMENTATION**
>
> Risk: medium / required UE level: U0
>
> Scope: schema v3→v4, deterministic L0 event capture, Evidence Chain storage foundation, bounded read-only L0 tools, and Writer durable-artifact hooks. No distillation, vector retrieval, L2/L3 injection, P4, C++, or required UE execution.

## 1. Goal

M1 established the rule that Memory may not make normal work slow or unbounded. M2 now makes Project Memory **accumulate deterministic evidence automatically while UEAgentKit is used**.

M2 does not summarize or infer. It records compact, append-only indexes that point back to Writer artifacts already produced by UEAgentKit.

Target flow:

```text
Writer action / verification / recovery
        ↓
existing durable Writer artifact is persisted first
        ↓
small deterministic L0 event batch
        ↓
Project Memory SQLite v4
        ↓
later M3 deterministic distillation
```

The L0 row is an index/evidence pointer, not a second copy of Change Set / checkpoint / verification JSON.

## 2. Authority and baseline facts

Repository facts at M2 planning:

```text
M1 checkpoint                     6d9cf711
M1 final full                     866 / 866 PASS / 92.518 s
M1 memory G1                      183 / 183 PASS / 25.987 s
M1 first Tool delta p95           20.177 ms
M1 direct recall p95              18.631 ms
M1 task-end append p95            16.178 ms
Memory schema                     v3
Change Set schema                 2.0
Track W                           complete
D1 workflow split                 complete
W4 durable evidence chain         frozen
P4                                not required for M2
```

The W4 frozen identity chain is the downstream evidence contract:

```text
changeSetId
  ↕ batchPlanId / batchPlanDigest
  ↕ batchExecutionId
      batchOperationId / stableTargetKey / sequenceIndex
      liveApplyReceipt / editorSessionId / transaction lineage
  ↕ checkpointSetId
      child checkpointId / saveReceipt / before+after Revision
  ↕ aggregate verification
      Strong Verify / Semantic Diff / Verification Plan / Trust
  ↕ optional recoveryId
      completed/pending/failed recovery boundaries
```

M2 must reference this chain, not redesign it.

## 3. Non-goals

Do not pull forward:

```text
M3 L0→L1 distillation or fact extraction
M3 hypothesis verdict evaluation
M4 embeddings / sqlite-vec / model2vec / RRF
M5 L2/L3 generation or prompt injection
M6 symbolic compression
P4 / Source Control integration
new C++ / EditorBridge handlers
LLM calls of any kind
cloud/network storage
arbitrary filesystem capture
automatic Save / submit / checkout
published version changes
```

`pyproject.toml [project].dependencies` remains `[]`.

## 4. Frozen schema v4

M2 performs one additive migration: `CURRENT_MEMORY_SCHEMA_VERSION = 4`.

### 4.1 `memory_evidence_chains`

Create first so L0 events can optionally reference a hypothesis/chain:

```sql
CREATE TABLE memory_evidence_chains (
    chain_id          TEXT PRIMARY KEY,
    project_key       TEXT NOT NULL,
    hypothesis        TEXT NOT NULL,
    context_json      TEXT NOT NULL DEFAULT '{}',
    verdict           TEXT NOT NULL CHECK (
        verdict IN ('supported', 'rejected', 'inconclusive')
    ),
    confidence        TEXT NOT NULL CHECK (
        confidence IN ('high', 'medium', 'low')
    ),
    created_at_utc    TEXT NOT NULL,
    verified_at_utc   TEXT NOT NULL DEFAULT '',
    superseded_by     TEXT REFERENCES memory_evidence_chains(chain_id) ON DELETE SET NULL
);

CREATE INDEX memory_evidence_chain_project_idx
    ON memory_evidence_chains(project_key, verdict, created_at_utc);
```

M2 only supplies storage/service primitives. Automatically deciding `supported/rejected` from evidence belongs to M3. No model-generated hypothesis is created in M2.

### 4.2 `memory_l0_events`

Use immutable event rows:

```sql
CREATE TABLE memory_l0_events (
    event_id          TEXT PRIMARY KEY,
    project_key       TEXT NOT NULL,
    event_kind        TEXT NOT NULL,
    occurred_at_utc   TEXT NOT NULL,
    source_ref        TEXT NOT NULL,
    artifact_ref      TEXT NOT NULL DEFAULT '',
    artifact_digest   TEXT NOT NULL,
    lifecycle_state   TEXT NOT NULL,
    outcome           TEXT NOT NULL CHECK (
        outcome IN ('success', 'partial', 'failed', 'rejected', 'no-op', 'recovered', 'superseded')
    ),
    asset_paths_json  TEXT NOT NULL DEFAULT '[]',
    change_set_id     TEXT NOT NULL DEFAULT '',
    hypothesis_id     TEXT REFERENCES memory_evidence_chains(chain_id) ON DELETE SET NULL,
    details_json      TEXT NOT NULL DEFAULT '{}',
    distilled         INTEGER NOT NULL DEFAULT 0 CHECK (distilled IN (0, 1)),
    UNIQUE(project_key, event_kind, source_ref, artifact_digest)
);

CREATE INDEX memory_l0_pending_idx
    ON memory_l0_events(project_key, distilled, occurred_at_utc, event_id);
CREATE INDEX memory_l0_change_set_idx
    ON memory_l0_events(project_key, change_set_id, occurred_at_utc, event_id);
CREATE INDEX memory_l0_hypothesis_idx
    ON memory_l0_events(project_key, hypothesis_id, occurred_at_utc, event_id)
    WHERE hypothesis_id IS NOT NULL;
```

Why uniqueness includes digest rather than only artifact path:

Writer journals are lifecycle records and are rewritten at the same path as state progresses. `change-set.json`, checkpoint records, checkpoint-set records and recovery records can therefore have multiple meaningful immutable L0 observations. A path-only UNIQUE constraint would silently discard later verified/failed states.

### 4.3 Event identity

`event_id` is deterministic from canonical event identity, for example:

```text
l0_<sha256(project_key + event_kind + source_ref + artifact_digest) prefix>
```

Replaying capture of the same exact artifact state is idempotent and returns the existing row; a changed artifact digest produces a new append-only event.

No public update/delete API for L0 events is added in M2. Only `distilled` may be changed later by M3 through a dedicated service path.

## 5. L0 event bounds

Central constants; do not scatter magic values:

```text
MAX_L0_CAPTURE_BATCH_EVENTS     8
MAX_L0_ASSET_PATHS             16
MAX_L0_DETAILS_JSON_BYTES      4096
MAX_L0_SOURCE_REF_CHARS         512
MAX_L0_ARTIFACT_REF_CHARS       512
MAX_L0_LIST_RESULTS             100
```

Asset paths beyond the compact index limit are not copied into the row; set `assetPathsTruncated=true` in details and rely on the referenced durable artifact for the complete set.

L0 details contain IDs/counts/status/reason codes only. Do not copy full Writer payloads, canonical exports, stack traces, exception messages, or arbitrary user text into `details_json`.

## 6. Artifact and rejection provenance

M2 supports two deterministic source forms.

### 6.1 Artifact-backed event

For a Writer artifact already written under the fixed workflow `work_root`:

```text
artifact_ref     = normalized path relative to work_root
artifact_digest  = sha256 of the exact persisted file bytes
source_ref       = artifact:<artifact_ref>
```

Capture service must:

```text
resolve fixed artifact root once
require existing regular file
reject paths escaping artifact root
never store absolute local paths
compute digest after Writer persistence succeeds
```

### 6.2 Inline deterministic rejection event

Some valuable fail-closed rejections occur before Writer creates an artifact. For those, M2 may append one small L0 rejection event directly:

```text
source_ref       = workflow:<bounded-operation-name>
artifact_ref     = ""
artifact_digest  = sha256(canonical bounded rejection payload)
outcome          = rejected | failed
```

Allowed inline payload fields:

```text
operation/tool identifier
WorkflowError.code
bounded asset paths
changeSetId when already known
stable target identity when already known
```

Do not persist exception messages, stack traces, arbitrary input blobs, Policy contents, or secrets.

Only deterministic `WorkflowError`-style Writer failures on M2-integrated surfaces are automatically captured. Generic Python/OSError failures are not promoted into durable project facts merely because an exception occurred.

## 7. Required event kinds and capture points

Initial M2 allowlist:

```text
live_write
change_set
batch_execution
checkpoint
checkpoint_set
semantic_diff
trust
recovery
workflow_rejection
```

`impact` is deliberately deferred: current ad-hoc Impact Analysis does not have a frozen durable artifact producer suitable for automatic L0 capture. Do not create noisy writes on every read-only impact query just to satisfy an old conceptual list.

### 7.1 Direct Writer path

Direct low-level mutation outside W4 aggregate orchestration captures a compact batch after existing durable persistence succeeds:

```text
live_write artifact
+ current change_set artifact when bound
```

Meaningful direct checkpoint Save/Verify captures:

```text
checkpoint artifact
+ current change_set artifact when bound
```

Undo/Discard/no-op/supersession transitions capture the resulting durable Change Set state when present.

### 7.2 W4 batch execution

`BoundedBatchService` currently calls the direct live-write method for every child operation. M2 must **not** turn a 16-operation batch into 16 independent SQLite capture commits plus aggregate events.

Add a scoped suppression/coalescing mechanism on the workflow service:

```text
with workflow_service.suppress_memory_l0_capture():
    execute existing child Writer operations unchanged

persist terminal batch execution artifact
capture one batch:
    batch_execution
    + current change_set state
```

Capture terminal/meaningful execution boundaries only:

```text
applied
persisted partial/failure boundary
```

Do not capture `applying` or every intermediate rewrite of `execution.json`.

### 7.3 Checkpoint Set

Checkpoint Set Save/Verify similarly suppresses nested child auto-capture while aggregate orchestration runs.

After successful/partial terminal persistence:

```text
checkpoint_set
+ current change_set state
```

After aggregate verification completes, append in one SQLite transaction:

```text
checkpoint_set
semantic_diff        # points to same checkpoint-set artifact/digest
trust                # points to same checkpoint-set artifact/digest
change_set           # current durable Change Set state when available
```

This uses the existing `checkpoint-set.json` verification section as the durable source. M2 does not invent separate Semantic Diff/Trust files.

### 7.4 Recovery

After a durable recovery commit reaches a meaningful final/partial/blocked failure boundary:

```text
recovery
+ current change_set state when changed/available
```

Preview/Get reads do not create L0 events.

### 7.5 Fail-closed rejection

On the integrated direct/batch/save/verify/recovery paths, when a deterministic `WorkflowError` rejects the action before a durable artifact exists, append one bounded `workflow_rejection` event.

If a durable failure/partial artifact already exists, prefer the artifact-backed event; do not create a duplicate inline rejection for the same failure.

## 8. Capture service architecture

Add a Memory-layer module, preferably:

```text
src/ue_agent_kit/memory_l0.py
```

Core types:

```text
MemoryL0Event
MemoryL0EventDraft
MemoryEvidenceChain
MemoryEvidenceChainDraft
MemoryL0CaptureResult
MemoryL0CaptureBatchResult
MemoryL0CaptureService
```

Required service behavior:

```text
append_event / append_events in one SQLite transaction
capture artifact-backed event(s)
capture bounded rejection event
exact-state idempotence
list/get L0 events
create/get/list evidence chains
no distillation
no LLM
```

`append_events()` is the primary integration primitive so one high-level Writer action uses at most one Memory SQLite transaction for its L0 batch.

### 8.1 Optional binding

Memory remains optional. The workflow layer must not import or construct a database on its own.

Composition rule:

```text
ProjectMemoryService enabled
+ workflow_service enabled
+ same project key
        ↓
MCP composition creates/binds MemoryL0CaptureService
        ↓
workflow/aggregate services use optional capture coordinator
```

When Memory is disabled:

```text
capture service = None
no DB open
no file digest for Memory
no event allocation beyond a cheap None check
Writer behavior/output remains otherwise unchanged
```

Use an explicit one-time bind/configure method or equivalent fixed constructor wiring. Reject project/root mismatch; do not silently rebind an existing workflow to another Memory database.

### 8.2 Failure semantics

Memory capture is observational and must not retroactively fail or roll back a Writer operation that already persisted successfully.

Capture returns a compact result:

```text
enabled
capturedCount
existingCount
failedCount
eventIds[]              # bounded to the capture batch
errorCode               # only when degraded
```

Affected successful Writer/aggregate responses may attach this as `memoryCapture` when Memory capture is enabled. Do not add placeholder `memoryCapture` output when Memory is disabled unless an existing response contract requires it.

Capture failure is truthful/degraded but non-fatal to the primary Writer result.

## 9. Read-only L0 tools

L0 is toolized, not prompt-injected. Add only two bounded read tools:

```text
ue_memory_list_l0_events(
    event_kinds=[],
    change_set_id="",
    distilled=None,
    limit=50,
)

ue_memory_get_l0_event(event_id)
```

Rules:

```text
list max <= 100
fixed project only
metadata/evidence refs only
no arbitrary artifact file reading
no mutation
no prompt injection
```

Expose L0 counts in `ue_memory_status` / `ProjectMemoryStatus`:

```text
l0EventCount
pendingL0EventCount
EvidenceChainCount
```

No public Evidence Chain mutation tool is required in M2. M3 can add only what it actually needs.

## 10. M1 efficiency gates remain mandatory

M2 extends `MeasureMemoryOverhead.py`; it does not replace M1 measurements.

Keep all existing M1 `--gate` checks:

```text
first Tool Memory incremental p95 < 200 ms
direct automatic recall p95 < 300 ms
task-end append p95 < 100 ms
recall <=5 items / <=2000 chars / <=800 tokens
no-hit empty
```

Add measurements:

```text
B5 single artifact-backed L0 capture
B6 four-event capture batch in one transaction
B7 exact-state duplicate replay
```

Hard M2 capture gate:

```text
p95 four-event L0 capture batch < 100 ms
```

Report single-event and duplicate-replay p95 values but do not invent a `<5 ms` hard blocker: M1 already measures SQLite task-end append around 16 ms on this machine. The architectural hard requirement is that bounded capture does not consume the 100 ms task-end budget.

Duplicate replay must create zero new rows.

## 11. Tests

### 11.1 Schema/migration

Prove:

```text
fresh DB creates schema v4
realistic v3 fixture migrates in place to v4
existing memory_records / nodes / work preserved
v4 reopen idempotent
no v5 tables/dependencies introduced
foreign keys valid
```

### 11.2 L0 service

Prove:

```text
artifact ref stored relative, never absolute
artifact outside fixed root rejected
file SHA matches stored digest
same exact artifact state dedupes
same artifact path with changed digest appends a new event
append_events is atomic
batch >8 rejected
bounds on paths/details/source refs enforced
list/get are fixed-project and bounded
no update/delete L0 API
```

### 11.3 Evidence Chain foundation

Prove:

```text
create/get/list chain
optional L0 hypothesis FK
superseded_by valid
invalid cross/missing chain rejected by FK/service validation
M2 does not auto-decide verdicts
```

### 11.4 Workflow integration

Use existing Python fake/resident workflow fixtures; do not require UE.

Required cases:

```text
Memory disabled -> zero capture calls/writes
direct live write -> live_write + change_set capture batch
W4 child live writes suppressed -> one aggregate batch_execution capture batch
batch failure with durable execution -> partial/failed artifact event
checkpoint direct Save/Verify -> checkpoint + change_set
checkpoint-set aggregate -> checkpoint_set/change_set
checkpoint-set verify -> checkpoint_set + semantic_diff + trust + change_set in one transaction
recovery terminal/partial -> recovery event
deterministic pre-artifact WorkflowError -> bounded workflow_rejection
same operation replay/idempotence -> no duplicate exact-state L0
capture failure -> Writer result remains successful but reports degraded capture
```

### 11.5 MCP/read contract

Prove L0 list/get tools are read-only, bounded, fixed-project, registered only with Project Memory enabled, and do not expose absolute filesystem paths.

## 12. Implementation slices

### M2-0 — Schema and service foundation

```text
add v4 migration
add memory_l0.py types/service
migration + append/idempotence tests
extend memory status counts
```

No Writer hooks yet.

### M2-1 — L0 read tools + performance measurement

```text
add list/get service APIs
register bounded read-only MCP tools
extend MeasureMemoryOverhead B5/B6/B7
run focused benchmark before Writer integration
```

### M2-2 — Optional capture binding + direct Writer path

```text
bind capture coordinator in MCP composition
Memory disabled = no-op
integrate direct live-write/change-set/checkpoint paths
add bounded deterministic rejection capture
capture failures remain non-fatal/visible
```

### M2-3 — W4 aggregate coalescing

```text
add scoped nested-capture suppression
batch execution terminal capture
checkpoint-set Save/Verify capture
semantic_diff + trust event batch from checkpoint-set artifact
recovery capture
```

Do not capture every intermediate persistence rewrite.

### M2-4 — Restart/idempotence + Evidence Chain foundation

```text
reopen Memory DB and replay same durable artifacts
prove no duplicate exact-state events
prove later digest/state appends new event
prove chain FK/supersession behavior
```

### M2-5 — G1/G2 closure

```text
final M1+M2 benchmark gate
memory domain
workflow/MCP focused integration tests
full once
Ruff / compileall / ValidateRelease / diff check
Result document
```

No separate blocker document unless a real blocker has a distinct investigation/exit gate.

## 13. Validation Budget

```text
Risk class: medium
Required UE level: U0
P4: not required
```

### G0

After coherent slices, not every function edit:

```text
focused memory_l0/schema tests
touched workflow/MCP tests
touched Ruff
RunPythonTests.py fast only at meaningful checkpoints
```

### G1

```text
python scripts/RunPythonTests.py domain memory
relevant workflow/MCP focused modules
python scripts/MeasureMemoryOverhead.py --gate ...
git diff --check
```

Do not run full at G1.

### G2 — once after final source state

```text
python scripts/RunPythonTests.py full
python -m ruff check src tests/python scripts
python -m compileall -q src scripts tests/python
python scripts/ValidateRelease.py --skip-tests --skip-ruff
git diff --check
python scripts/MeasureMemoryOverhead.py --gate ...
```

### UE acceptance

M2 required closure is U0. No C++ or EditorBridge semantics change.

A later optional U1 spot-check may run one existing DirectHost Writer flow and confirm a real artifact produces L0 rows, but it is **not part of the M2 execution-Agent timing comparison** and is not required to implement/close the Python stage unless repository facts expose a gap that cannot be proven offline.

## 14. Acceptance matrix

M2 closes when all are true:

```text
A1  schema v3 -> v4 additive migration PASS
A2  v3 data preserved PASS
A3  artifact-backed L0 events store relative ref + exact digest PASS
A4  same artifact path/new digest appends; exact replay dedupes PASS
A5  L0 append-only; no arbitrary update/delete API PASS
A6  Evidence Chain storage foundation + optional FK PASS
A7  Memory disabled causes zero capture writes PASS
A8  direct Writer durable path automatically captures PASS
A9  W4 batch child capture is coalesced; no N-child SQLite commit storm PASS
A10 checkpoint-set verify captures aggregate + semantic_diff + trust PASS
A11 recovery partial/failure boundary capture PASS
A12 deterministic pre-artifact rejection capture PASS
A13 capture failure does not invalidate already-successful Writer persistence PASS
A14 no absolute local paths / unbounded payloads in L0 PASS
A15 read-only L0 list/get tools bounded and fixed-project PASS
A16 all M1 recall/performance gates still PASS
A17 four-event capture-batch p95 <100 ms PASS
A18 memory G1 PASS
A19 final full G2 PASS once after final source state
A20 no new required runtime dependency / no LLM / no P4 / no UE required PASS
```

Any A7-A17 regression blocks M3.

## 15. Expected files

Likely primary delta:

```text
src/ue_agent_kit/memory_schema.py
src/ue_agent_kit/memory_l0.py                         new
src/ue_agent_kit/memory_service.py
src/ue_agent_kit/mcp_memory_tools.py
src/ue_agent_kit/mcp_server.py
src/ue_agent_kit/workflow_common.py
src/ue_agent_kit/workflow_live.py
src/ue_agent_kit/workflow_verify.py
src/ue_agent_kit/bounded_batch.py
src/ue_agent_kit/checkpoint_sets.py
src/ue_agent_kit/batch_recovery.py
src/ue_agent_kit/mcp_workflow_tools.py                 only if response wiring needs it
scripts/MeasureMemoryOverhead.py
scripts/RunPythonTests.py                              only for a new focused module mapping if needed

tests/python/test_memory_l0.py                         new preferred module
tests/python/test_memory_service.py
tests/python/test_mcp_server.py
existing workflow/batch/checkpoint/recovery test modules as affected

docs/Plans/UEAGENTKIT_M2_DETERMINISTIC_L0_AUTO_CAPTURE_RESULT_20260830.md
```

Do not broaden into unrelated Writer refactors merely because these files are touched.

## 16. Execution-efficiency rules for the local Agent

This stage will be used to compare local coding-Agent execution speed. Quality gates are unchanged, but avoid unnecessary harness round-trips.

Required execution discipline:

```text
1. Do not re-argue architecture frozen in this Plan unless actual code makes it impossible.
2. At each M2 slice, batch-read the necessary files first.
3. Form one coherent edit set, apply it, then run focused validation.
4. Do not read one function -> edit one function -> rerun status/test repeatedly.
5. Do not rerun full suite before G2.
6. Do not repeatedly regenerate the Detailed Plan or handoff during implementation.
7. Ordinary implementation bugs/tests are solved autonomously; do not stop for approval.
```

Stop and request owner input only if:

```text
Plan conflicts materially with actual repository facts
frozen public API/safety invariant must change
M3+ scope is genuinely required
M1 hard performance gate cannot be met without architecture tradeoff
another Agent's active work would be overwritten
```

At completion, report:

```text
total elapsed time
time spent in G0/G1/G2/benchmark if available
number of full-suite runs
number of memory-domain runs
major implementation slices completed
longest/most expensive steps
tool-call count if the harness exposes it
```

Do not sacrifice validation to improve the timing number.

## 17. Commit/push policy

The execution Agent must not commit, push, rebase, tag, release, or change version unless the owner explicitly authorizes that action for M2.

The normal handoff is a reviewed dirty working tree plus M2 Result, followed by a separate owner/reviewer checkpoint decision.

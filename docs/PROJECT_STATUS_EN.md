# UE Agent Kit Project Status



Updated: 2026-08-01



This document describes the current **0.7.0-dev** development line on `main`. The latest published release remains **0.6.0** for Unreal Engine 5.6. Live Editor Write, explicit Undo/Discard, the authorized-save closeout, the recoverable journal, and the scalable Operation Registry have not yet been published.



## 1. Positioning



UE Agent Kit is not intended to be an unrestricted remote-control layer for Unreal Editor. It is an Unreal Engine **project-intelligence and controlled-change layer** for AI agents:



1. Convert binary assets, Blueprint semantics, references, and live editor state into stable searchable data.

2. Ground changes in traceable project context instead of transient screenshots, logs, or guesses.

3. Gate persistent mutation through Policy, Revision, Plan, Dry Run, explicit confirmation, backup, verification, and rollback.

4. Preserve rules, findings, decisions, task conclusions, and evidence in Revision-aware Project Memory that invalidates stale knowledge when assets change.



The project is therefore closer to a safe project-knowledge layer plus a verified change workflow than a broad editor automation console.



## 2. Current scale



```text

Mode                 Without Memory    With Memory

Offline                     5              12

Live                       23              30

Workflow                   26              33

Combined                   44              51

```



Tool count is not equivalent to Unreal operation count. Workflow currently includes 12 high-level safe-change entry points, the low-level Patch workflow, Live Editor Write, authorized save, verification, index refresh, and rollback.



Current validation baseline:



```text

Python tests                 264/264

JSON Schemas                 3/3

Patch examples               16/16

UE5.6 Direct Build           passed

Real Live Editor Write       passed

Real Live Editor Reference Write  passed

Real Live Editor Structured Write  passed

UTF-8 no BOM / CRLF          passed

```



## 3. Implemented read capabilities



### Offline project reads



- Asset catalog for Asset Registry-visible project assets.

- Blueprint graph, node, pin, connection, variable access, function, macro, interface, cast, and dispatcher semantics.

- Canonical JSON and BPCTX/1 output layers.

- Package SHA-256 Revisions paired with exports and SQLite snapshots.

- Asset, Symbol, Reference, full-text, and path-filtered project search.

- Hard/Soft dependencies, reverse references, and bounded bidirectional reference traversal.

- Four-source asset state across Editor Memory, disk Package, Revision Export, and SQLite.



### Live Editor reads



- Editor, PIE/SIE, level, selection, open-asset, and Dirty Package state.

- Incremental Output Log and Blueprint compile diagnostics.

- Non-loading live asset inspection.

- Focused Graph and selected Node identity in ordinary Blueprint Editors.



### Project Memory reads



- Rules, findings, decisions, known issues, task records, and runtime evidence.

- Source, status, Scope, Revision Set, Artifact, Confidence, time, and evidence digest.

- Automatic stale transitions after Revision changes and coexistence of conflicting conclusions.



## 4. Implemented write and action capabilities



### Non-persistent Live Actions



Open/focus assets, synchronize Content Browser, focus an ActorGuid, compile a Blueprint in memory, run Data Validation, and run exact Automation Tests without directly saving packages.



### Live Editor Write foundation

Current closeout:

```text
Policy / Revision Plan
→ exact LIVE APPLY confirmation
→ registered Operation executor
→ FScopedTransaction / Snapshot / Dirty
→ explicit Undo / Discard, or Authorized Save
→ independent Unreal reload Verify
→ Memory Evidence
```

The current `0.7.0-dev` registry exposes 12 controlled Operations: Data Asset scalar/reference/Struct/Array/Set/Map values, Material Instance Scalar/Vector/Texture/Static Switch parameters, and DataTable Cell/RowFields/Add/Remove/Rename. It still requires an already loaded, open, initially clean, non-Blueprint, non-map `/Game` asset and rejects arbitrary UObject methods, nested property paths, PIE/SIE mutation, automatic saves, and unauthorized writes.

To scale toward hundreds of Operations, the central Bridge now receives generic `operation + assetPath + target + value` requests and dispatches through `LiveWriteOperationRegistry`. Property, Material, and DataTable logic live in separate domain modules; a shared Transaction/Evidence layer owns snapshots, no-op behavior, failure restoration, Dirty state, and Undo. Python `OperationSpec` metadata drives target validation, valueKind, and independent post-save verification instead of maintaining another hard-coded whitelist.

A fixed-work-root journal persists pending Live Apply receipts. A restarted MCP server can recover strictly validated records, Verify can select an exact `liveApplyReceipt`, and successful Undo/Discard/Verify closes the record. Journal I/O failure never turns an already successful Editor mutation into a false failure.

Real regressions are split into Fast (Scalar, Undo/Discard, Closed Loop) and Full (all seven groups). The published protocol/package version remains 0.6.0 while status explicitly reports `developmentLine=0.7.0-dev`.

### Persistent controlled writes



- Blueprint defaults, component properties, pin defaults, and registered description operations.

- Data Asset scalar, Object/Class, Soft Object/Class, Struct, Array, Set, and Map values.

- Material Instance Scalar, Vector, Texture, and Static Switch parameters.

- DataTable cell, multi-field, row add/remove/rename operations.

- Atomic 1–32 compatible operations on one asset.



Persistent workflow:



```text

Plan

→ Dry Run

→ one-time Receipt

→ exact COMMIT confirmation

→ external backup

→ Unreal save

→ independent reload verification

→ Task Evidence

→ verifiable rollback

```



A controlled Dirty asset produced in the live editor can be persisted separately through `ue_save_authorized_asset`.



### Memory writes



Add confirmed rules, record observed or inferred findings, persist evidence-bound task records, explicitly supersede old records, and validate stale state against current Revisions.



## 5. Explicitly unsupported today



- Generic Blueprint Graph node creation/deletion/wiring/layout.

- Anim Blueprint state machines, Montages, Blend Spaces, Anim Sequences.

- Control Rig, IK Retargeter, RigVM Graph.

- Material Graph, Niagara, Sequencer, UMG Widget Tree writes.

- General Level Actor spawn/delete/transform/property mutation.

- PIE input injection, recording, deterministic replay, and viewport capture workflows.

- Asset import, duplicate, rename, delete, and migrate lifecycle operations.

- Arbitrary Console, Python, C++, Shell, or script execution.

- Editor/Visual Studio lifecycle and build dispatch automation.

- Source-control checkout, locks, ownership, and depot-head conflict handling.



These are intentional scope and safety boundaries, not documentation omissions.



## 6. Planned work



### P0A: Realtime Editor CRUD, batch tasks, and diagnostics

The Live Editor Write foundation, Material/DataTable support, Undo/Discard, Save→Verify→Memory closeout, and registry-based extension architecture are complete. Realtime I/O is the primary daily-development path; the next stage prioritizes current Editor Context, batch Query/Task execution, PIE diagnostics, Change Sets, and high-value domain CRUD without expanding the central dispatcher. Every new Operation must add:

1. Python `OperationSpec`, Policy authorization, and Plan schema.
2. A C++ domain executor and Operation Descriptor.
3. Snapshot, no-op, failure restoration, Dirty, Undo, and independent Verify semantics.
4. Real UE5.6 success, rejection, restoration, and closeout regressions.

### P0B: Memory usability and knowledge-tree foundation

The Revision-aware flat record store in 0.6.0 is complete, but maintenance complexity must not be delegated to agent discipline. Before Context Packs, implement:

- An arbitrary-depth Knowledge Tree: Project Profile → System → Feature/Entity → Implementation.
- Existing rules, findings, decisions, issues, tasks, and evidence bound to Knowledge Nodes.
- Separate Active Work for objectives, in-progress work, TODO, blockers, pending decisions, and next actions.
- Five-level progressive disclosure from index summaries to raw evidence.
- Server-enforced token budgets, default status filters, deduplication, and structured `nextActions`.
- One thin `project-memory` Skill instead of separate long read/write/maintenance/TODO Skills.

See [`MEMORY_ARCHITECTURE_EN.md`](MEMORY_ARCHITECTURE_EN.md).

### P1: 0.7.0 Context/Analysis



- Automatic task-scoped Context Packs.

- Value-source and execution-chain tracing.

- Change-impact analysis.

- Semantic asset diffs.

- Evidence-backed hypotheses.

- Automatic Change Plans and Verification Plans.



### P2: demand-driven specialized writes



- Live Blueprint default/component/pin changes with compile evidence.

- Enhanced Input / Input Mapping Context.

- Narrow high-value animation edits.

- Restricted Level Actor transform/property operations.



Full graph mutation will require stable node/pin identity, structural diffs, compile verification, and failure recovery before it is exposed.



### P3: 0.8.0 Collaboration

Use a hybrid deployment: each developer runs a local MCP connected to the local UEAgentKit plugin and editor, while the team shares a separate Knowledge Service. The shared layer is expected to use PostgreSQL/API; local SQLite remains responsible for the asset index, caches, private data, and session data.



Read source-control provider, checkout, lock, owner, and head state; compare local Dirty state, disk Revision, and depot/remote head; model multi-user risk; analyze, warn, or block without stealing locks or overwriting another developer's work.



## 7. Direction principles



1. Understand before modifying.

2. Build narrow verified vertical slices before broad operation counts.

3. Live mutation still requires fixed-project, Policy, Revision, Plan, and confirmation gates.

4. A save is not success without independent verification and evidence.

5. Do not use arbitrary scripting as a shortcut around the safety model.

6. Prioritize repeated real-project needs over feature-count parity.

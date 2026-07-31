# UE Agent Kit Project Status



Updated: 2026-07-31



This document describes the current `main` development snapshot. The latest published release remains **0.6.0** for Unreal Engine 5.6. The first Live Editor Write vertical slice has been completed after 0.6.0 but has not yet been published as a new release.



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

Python tests                 248/248

JSON Schemas                 3/3

Patch examples               16/16

UE5.6 Direct Build           passed

Real Live Editor Write       passed

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



### First Live Editor Write



The current `ue_apply_asset_property_live` vertical slice is:



```text

Policy/Revision Plan

→ exact LIVE APPLY confirmation

→ Game Thread UObject mutation

→ FScopedTransaction / Modify

→ PostEditChangeProperty

→ Package Dirty

→ no automatic save

```



The first version supports one top-level scalar, enum, String, Name, or Text property on an already loaded, open, clean, non-Blueprint, non-map asset. The change enters the Unreal Undo stack while disk Package and SQLite remain unchanged.



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



### P0: complete the Live Editor Write foundation



- Shared Live Transaction/Evidence framework.

- Controlled live Reference and Structured Property changes.

- Material Instance and DataTable live apply.

- Explicit live Undo/Discard workflow.

- Standard Live Apply → Authorized Save → Verify → Memory Task closure.



### P1A: Memory usability and knowledge-tree foundation

The Revision-aware flat record store in 0.6.0 is complete, but maintenance complexity must not be delegated to agent discipline. Before Context Packs, implement:

- An arbitrary-depth Knowledge Tree: Project Profile → System → Feature/Entity → Implementation.
- Existing rules, findings, decisions, issues, tasks, and evidence bound to Knowledge Nodes.
- Separate Active Work for objectives, in-progress work, TODO, blockers, pending decisions, and next actions.
- Five-level progressive disclosure from index summaries to raw evidence.
- Server-enforced token budgets, default status filters, deduplication, and structured `nextActions`.
- One thin `project-memory` Skill instead of separate long read/write/maintenance/TODO Skills.

See [`MEMORY_ARCHITECTURE_EN.md`](MEMORY_ARCHITECTURE_EN.md).

### P1B: 0.7.0 Context/Analysis



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

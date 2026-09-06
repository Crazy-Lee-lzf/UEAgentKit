# UE Agent Kit Project Status



Updated: 2026-09-06

> Current development note (2026-09-06): Track W / Writer, Track V / Knowledge Web, required Track M M1-M5, and Track C C1-C3 are complete and owner-reviewed. C3 closed at `5b705a7`; Source Control G1 is 94/94 PASS and portable full is 1062/1062 PASS (17 skipped). The next primary stage is real-project write-enabled dogfood. M6/C4 remain optional/deferred. [`Plans/README.md`](Plans/README.md), the canonical handoff, and the P4 boundary decision are authoritative for current stage facts; older milestone sections below remain useful historical detail.



The latest published line remains **0.7.0** on `main` for Unreal Engine 5.6. The 0.8.x Context / Analysis / Agent Reliability capability scope is locally closed on `feature/agent-reliability`, while the package/plugin version, tags, release artifacts, and remote branches remain unchanged.



## 1. Positioning



UE Agent Kit is not intended to be an unrestricted remote-control layer for Unreal Editor. It is an Unreal Engine **project-intelligence and controlled-change layer** for AI agents:



1. Convert binary assets, Blueprint semantics, references, and live editor state into stable searchable data.

2. Ground changes in traceable project context instead of transient screenshots, logs, or guesses.

3. Gate persistent mutation through Policy, Revision, Plan, Dry Run, explicit confirmation, backup, verification, and rollback.

4. Preserve rules, findings, decisions, task conclusions, and evidence in Revision-aware Project Memory that invalidates stale knowledge when assets change.



The project is therefore closer to a safe project-knowledge layer plus a verified change workflow than a broad editor automation console.



## 2. Current scale



```text

Current development line, Source Control disabled by default:

Mode                        Base       + Memory

Offline                       10              24

Live                          43              57

Workflow-only                 67              81

Live + Workflow              100             114

Opt-in Source Control adds six tools: Offline 16/30, Live 49/63, Workflow-only 73/87, Live+Workflow 106/120 (without/with Memory). Published 0.7.0 retains its release-time 10/22, 43/55, 60/72, 93/105 counts.

```



Tool count is not equivalent to Unreal operation count. The historical 0.8 capability audit covered 105 public tools and 18 registered Patch Operations and identified zero must-fix new tools. The current development registry can expose up to 120 tools when both Memory and the opt-in Source Control group are enabled; current mode counts are listed above.



Current 0.8 capability-closeout validation baseline:

```text
Portable unittest             696 passed
Full Python suite              739 passed
JSON Schemas / Patch examples  3 / 16
Ruff / compileall              passed
PowerShell parser              61 / 61
R4.1 raw summary --check       passed
Tool / Operation audit         105 / 18
UTF-8 no BOM / CRLF            passed
C++ changed                    0
Direct Build                   not triggered
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

- A Schema v3 Knowledge Tree with normalized `/project/...` paths, same-project parents, cycle prevention, and safe deletion rules.

- Separate Active Work with `planned/in_progress/blocked/done/cancelled`, TODO items, next actions, and normalized node/asset links.

- Levels 0–4 progressive Context, character budgets, default `stale/superseded` filtering, truncation `nextActions`, and on-demand Evidence.

- Five new high-level MCP tools while preserving all seven existing Memory tools.



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

The 0.7.0 registry exposes 12 controlled Operations: Data Asset scalar/reference/Struct/Array/Set/Map values, Material Instance Scalar/Vector/Texture/Static Switch parameters, and DataTable Cell/RowFields/Add/Remove/Rename. It still requires an already loaded, open, initially clean, non-Blueprint, non-map `/Game` asset and rejects arbitrary UObject methods, nested property paths, PIE/SIE mutation, automatic saves, and unauthorized writes.

To scale toward hundreds of Operations, the central Bridge now receives generic `operation + assetPath + target + value` requests and dispatches through `LiveWriteOperationRegistry`. Property, Material, and DataTable logic live in separate domain modules; a shared Transaction/Evidence layer owns snapshots, no-op behavior, failure restoration, Dirty state, and Undo. Python `OperationSpec` metadata drives target validation, valueKind, and independent post-save verification instead of maintaining another hard-coded whitelist.

A fixed-work-root journal persists pending Live Apply receipts. A restarted MCP server can recover strictly validated records, Verify can select an exact `liveApplyReceipt`, and successful Undo/Discard/Verify closes the record. Journal I/O failure never turns an already successful Editor mutation into a false failure.

Real regressions are split into Fast (Scalar, Undo/Discard, Closed Loop) and Full (all seven groups). Release status reports `publishedVersion=0.7.0` and `developmentLine=0.7.0`.

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

- Agent-side P4 Submit/Revert/P4-managed Delete, generic P4 passthrough, blind accept-yours/theirs, and automatic `.uasset/.umap` content resolve. C1-C3 awareness/checkout assistance/pending-CL/bounded text-resolve/audit are implemented on the development line; A27 real C3 mutation awaits an owner-designated fixture.



These are intentional scope and safety boundaries, not documentation omissions.



## 6. Planned work

The current planning entry point is [`Plans/README.md`](Plans/README.md). Required Memory stages M1-M5 and Source Control C1-C3 are closed. The immediate priority is write-enabled dogfood in an owner-designated real commercial project, using observed failures to decide any Track X, C4, or M6 work. Formal package release remains a separate user-authorized track; R5 remains deferred.



### P0A: Realtime Editor CRUD, batch tasks, and diagnostics

The Live Editor Write foundation, Material/DataTable support, Undo/Discard, Save→Verify→Memory closeout, and registry-based extension architecture are complete. The Realtime Foundation now also includes bounded current Editor Context, the first frame-stepped Batch Task, and durable Change Sets:

- `ue_get_editor_context` aggregates Editor, World, Selection, Open Assets, Dirty Packages, Blueprint Graph Selection, Compile Errors, and Output Log Cursor in one read-only request, with stage timings and structured `nextActions`.
- `scanCurrentWorld` inspects only the currently loaded World. Level enumeration and Actor/Component processing are constrained by an approximately 2 ms per-frame budget and count limits. Tasks are bound to the Editor Session and World and support progress, cancellation, timeout, invalidation, and partial results.
- Batch Task status returns summaries by default. Details are retrieved through `include_details/detail_offset/detail_limit`, with at most five Actors per page, keeping responses below the Bridge's 1 MiB limit.
- Change Set schema v2 durably records Task, Editor Session, Operation, Asset, Transaction, Save Receipt, and Validation lifecycle data. It supports `planned/applied/partially_applied/undone/discarded/saved/verified/failed/unknown` and preserves terminal history.
- Active Change Sets are never silently evicted by capacity cleanup. Runtime state that cannot be re-proven after an Editor restart explicitly degrades to `unknown`.

Post-0.8 realtime work remains a primary development track, but the immediate goal is no longer broad Tool-count expansion. It is to migrate already proven narrow Blueprint default/component/pin writes into the resident Editor path, add fast session-local read-back/compile evidence, and move expensive independent verification to explicit task checkpoints without weakening Trust. New Operation families remain demand-driven. Every genuinely new Operation must add:

1. Python `OperationSpec`, Policy authorization, and Plan schema.
2. A C++ domain executor and Operation Descriptor.
3. Snapshot, no-op, failure restoration, Dirty, Undo, and independent Verify semantics.
4. Real UE5.6 success, rejection, restoration, and closeout regressions.

### Completed foundation: cross-cutting Memory and task-context integration

Schema v3 Knowledge Trees, Active Work, five-level progressive disclosure, on-demand Evidence, and five high-level Memory tools are complete. The 0.8 R0/C1 work also completed deterministic Task Context and correlation across Change Sets, Editor Sessions, Revisions, and Evidence. This track no longer owns new high-level Context feature work.

Remaining work is limited to:

- latency/result-size/token-budget benchmarks for Knowledge Tree, FTS, Context Pack, and large Memory databases;
- low-maintenance closure from Change Set / Active Work into Memory-ready Evidence;
- future 0.9 shared Knowledge Service and team-conflict semantics;
- keeping one thin `project-memory` Skill without expanding the base Memory schema.

See [`MEMORY_ARCHITECTURE_EN.md`](MEMORY_ARCHITECTURE_EN.md).

### P1: 0.8.x Context / Analysis / Agent Reliability (capability scope complete)

R0–R4 provide deterministic Task Context, bounded Impact Analysis, Change-Set-bound Semantic Diff, Evidence-gated Verification Plans and Trust Verdicts, and a real-agent deterministic benchmark. The C0–C6 closeout added closed result enums and target semantics, Trust next-action guidance, narrow rollback/reference normalization, R4.1 repeat measurement, a complete Read/Write Gap Audit, and Scope Freeze.

R4.1 retained 24/24 attempts across four Full/Legacy anchors, with 12/12 paired fairness matches, no measurement drift, no infrastructure failure, and 24/24 exact recovery. Full achieved 3/3 Trusted stale safe-stops and 3/3 Trusted Blueprint defaults. It also retained 3/3 high-fanout False Success caused by exceeding the direct-only bound, plus two scalar False Success claims caused by stringifying a numeric before-value. See [`Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](Plans/Archive/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md).

The capability audit covers all 105 public tools and 18 Patch Operations and concludes `0 must-fix new tools`. Generic graph/actor/material-graph/Niagara/Sequencer/Control Rig mutation and arbitrary scripting remain explicitly deferred. See [`Plans/Archive/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](Plans/Archive/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md).

R5 Value Provenance / Execution Trace remains deferred by benchmark evidence and may reopen only after repeated real cases identify either as the primary blocker.



### P2: demand-driven specialized writes



- Existing Blueprint default/component/pin Editor-resident Live Apply is promoted to the post-0.8 W1 track; this pool is only for genuinely new Operation families.

- Enhanced Input / Input Mapping Context.

- Narrow high-value animation edits.

- Restricted Level Actor transform/property operations.



Full graph mutation will require stable node/pin identity, structural diffs, compile verification, and failure recovery before it is exposed.



### P3: 0.9.0 Collaboration

Use a hybrid deployment: each developer runs a local MCP connected to the local UEAgentKit plugin and editor, while the team shares a separate Knowledge Service. The shared layer is expected to use PostgreSQL/API; local SQLite remains responsible for the asset index, caches, private data, and session data.



Read source-control provider, checkout, lock, owner, and head state; compare local Dirty state, disk Revision, and depot/remote head; model multi-user risk; analyze, warn, or block without stealing locks or overwriting another developer's work.



## 7. Direction principles



1. Understand before modifying.

2. Build narrow verified vertical slices before broad operation counts.

3. Live mutation still requires fixed-project, Policy, Revision, Plan, and confirmation gates.

4. A save is not success without independent verification and evidence.

5. Do not use arbitrary scripting as a shortcut around the safety model.

6. Prioritize repeated real-project needs over feature-count parity.

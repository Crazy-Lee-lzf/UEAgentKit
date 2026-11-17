# UE Agent Kit Capability Status

[中文](PROJECT_STATUS.md)

## Release status

| Version | Status | Date | Notes |
|---|---|---|---|
| 0.8.0 | Stable | 2026-09-07 | Previous stable release; Python 3.11–3.12 |
| 0.9.0 Developer Preview 1 | Prerelease (published) | 2026-10-30 | `v0.9.0-dev1`; Python 3.11–3.14 |
| 0.9.0 Developer Preview 2 | Candidate (unreleased) | — | Planned version `0.9.0.dev2`; the subject of this page |

This page describes the **0.9.0 Developer Preview 2 candidate code**. Published behavior is defined by the matching release notes; DP2 has no tag or GitHub Release yet.

UE Agent Kit is a **project knowledge layer plus controlled mutation workflow** for Unreal Engine. It turns assets, Blueprint semantics, references, and Editor state into searchable data, provides agents with context and verification evidence, and executes narrow writes behind Policy / Revision / Transaction / Save / Verify gates.

- The MCP tool surface is bounded by `--tool-profile` (default `core` = 15 tools; `read` / `live` / `live-write` / `full` widen it). A profile is an upper bound: it never enables a group the run flags left off, and `ue_get_capabilities.toolProfile` reports it.
- Read-only PIE reads go through one entry point, `ue_pie(action=...)` (`state` / `worlds` / `list_actors` / `inspect_actor` / `scalars` / `list_properties` / `read_properties`); the seven per-verb tools are no longer registered separately, with unchanged arguments and results.

## Read capabilities

### Offline index

- Asset Registry inventory and package metadata.
- Specialized readers for Static Mesh, Skeletal Mesh, Skeleton, Physics Asset, Material, Material Instance, Texture, Animation, DataTable, Data Asset, Niagara, World, and more.
- Blueprint Graph / Node / Pin, variables, functions, macros, interfaces, casts, and delegates.
- Canonical JSON, BPCTX/1, SQLite/FTS5 indexing, asset/symbol/reference search and reverse references.
- Package SHA-256 revisions and index freshness comparison.

### Live Editor

- Editor / PIE/SIE / World / selection, open assets, and dirty packages.
- Output Log, Blueprint compile diagnostics, and exact live asset inspection.
- Blueprint graph context by asset path, without requiring the offline index.
- Read-only PIE Server/Client actors, transforms, NetRole, components, and explicitly requested scalar properties.
- Reflected properties of any PIE UObject: list the property names and kinds first, then read values by name. Scalars and structs expand, Array/Map/Set page, object references return identity only, and unsupported types report `unsupported-type` with a reason instead of a value. `BlueprintVisible` / `Edit` is not a read gate.
- Object-reference navigation: `objectPath` accepts any UObject inside the exact PIE world and the returned `objectRef.path` can be used as the next hop. Every hop is an explicit call, nothing recurses automatically, and a reference that belongs to another world is flagged `crossWorld` and refused.

### Niagara module stacks

- Read a Module Stack by real execution stage: System/Emitter/Particle Spawn and Update groups, modules in engine order (never name-sorted), and every Input reports its name, type, source (Local / Linked / Dynamic / Data / ObjectAsset / Expression) and a safely expressible current value.
- Bounded edits of a Module Input local constant (Float / Int / Bool / Vector / Color): one Editor transaction per edit with Undo/Redo, never auto-saves, and never modifies the shared Module Script asset. Inputs whose source is not Local are refused with the actual source and linked target.
- Both directions act on the open System; fixtures such as `NS_DevStack` come from `scripts/GenerateNiagaraStackFixture.ps1` and are not committed.

### Live index overlay

- When `ue_get_asset` hits a session overlay record, it verifies the record against the package SHA-256 on disk and re-proves it from the Editor when the disk changed.
- If re-proof fails, it returns `overlayStale=true` plus `staleCause` instead of the old canonical.
- `ue_refresh_asset_index_live` explicitly refreshes one saved Blueprint. It affects only the current MCP session (up to 64 records), never writes SQLite, and never requires a restart.
- Current scope: Blueprints under `/Game` in plain single-file packages. World Partition external actors and multi-file packages are not supported.

### Project Memory / Knowledge

Rules, findings, decisions, known issues, task records, and runtime evidence; revision-aware stale/superseded/conflicted state; FTS5 recall plus optional Vector + RRF; L2/L3 context injection; read-only Knowledge Web.

## Write capabilities

All writes are off by default. The `workflow` group requires `--enable-write-tools` + `--enable-subprocess-workflow`, the project setting `SubprocessWorkflow=True`, and a Write Policy covering the target. Experimental Blueprint writes use separate gates and bypass the Write Policy allowlist.

| Write domain | Entry point | Status | Additional gate |
|---|---|---|---|
| Data Asset (scalar / reference / Struct·Array·Set·Map) | `workflow` | Stable surface | workflow gate |
| Material Instance (Scalar / Vector / Texture / Static Switch) | `workflow` | Stable surface | workflow gate |
| DataTable (cell / row fields / add·remove·rename row) | `workflow` | Stable surface | workflow gate |
| Registered Blueprint operations (variable defaults, component properties, pin defaults, descriptions) | `workflow` | Stable surface | workflow gate |
| Live single-asset property Apply (Editor memory + Undo, explicit Save) | `workflow` | Stable surface | workflow gate |
| AnimSequence diagnosis / fix / retarget assistance | `workflow` / `live-read` | Bounded | Tool-dependent |
| Blueprint atomic patch: `createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode` | `ue_*_blueprint_patch_experimental` | Experimental | `--enable-experimental-graphpatch-edit` + ini |
| Node creation from an Action Registry `actionId` (CallFunction / Cast) | same `createNode` | Experimental | plus `--enable-experimental-node-spawn` + ini |
| Blueprint position batches / edit sessions | `workflow` (`ue_move_blueprint_node*`) | Experimental interface | workflow gate |

Experimental patch limits: up to 16 operations, 16 new nodes, and 8 new variables per patch; edge operations are bounded by the operation total and at most 8 per node; `removeNode` removes at most one node and one incident edge; top-level K2 event graphs only; clean package and stopped PIE; one transaction per Apply with rollback on failure; Save is separate. See [Blueprint experimental usage](BLUEPRINT_AGENT_E2E_USAGE_EN.md).

Runtime modes, gates, and start commands are in [Agent Quick Start](AGENT_QUICKSTART_EN.md); full arguments are in [Build and Run](BUILD_AND_RUN.md). The authoritative tool list is always `ue_get_capabilities`.

## Agent workflow

Task Context, relevant-asset discovery, Impact Analysis, Change Set, Semantic Diff, Verification Plan, Trust Verdict, authorized Save / Strong Verify, and recovery/rollback evidence. A completed write is not a completed task; independent verification and evidence are part of the workflow.

## P4 / Perforce

Source Control is opt-in. Supported: mapping / opened / lock / owner / client / have / head queries, exact-file `p4 edit`, bounded safe sync, pending changelist query/create/description update, exact-file `reopen`, resolve preview, eligible plain-text `resolve -am`, and durable audit records.

The Agent never runs P4 Submit / Revert / P4-managed Delete; there is no generic P4 argv or shell passthrough; `.uasset` / `.umap` are never auto-resolved by choosing yours/theirs.

## Explicit non-goals

| Capability | Boundary |
|---|---|
| Arbitrary node creation by class / general graph CRUD / automatic wiring | Only the seven experimental patch operations and bounded node entry points |
| CustomEvent, event/delegate binding, Timeline, MathExpression | Outside the Action Registry creation scope |
| Creating `Select` / `PromotableOperator` via `createNode` | Only their wildcard pins can be connected; the schema derives the type |
| Nested subgraph writes | The experimental patch verifies top-level K2 event graphs only |
| Arbitrary Level Actor spawn/delete/transform/property | No general level editing |
| General Material Graph / Niagara / Sequencer / Control Rig writes | Only specialized reads or bounded flows |
| PIE input injection / record-replay | Read-only diagnostics |
| Asset Import / Duplicate / Rename / Delete / Migrate | No general asset-lifecycle operations |
| Console / Python / shell / UObject method execution | No generic execution entry point |
| Automatic Save All / automatic compile | Save and Compile are always separate |
| Agent-side P4 Submit / Revert / Delete | Human operation |

## Safety model

- Read-only by default; writes require explicit enablement.
- Revision, dirty-package, session, and target-identity checks fail closed.
- Rollback and Undo evidence take priority over an apparent success.
- A successful Save is not a successful task; independent verification and traceable evidence are required.
- Final P4 Submit / Revert / Delete remain human operations.

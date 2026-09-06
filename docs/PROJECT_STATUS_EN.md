# UE Agent Kit 0.8.0 Capability Status

Latest published release: **0.8.0**
Target environment: **Unreal Engine 5.6 / Windows / Python 3.11–3.12**


## Product position

UE Agent Kit is a **project knowledge layer plus controlled mutation workflow** for Unreal Engine. It is designed to:

1. turn Unreal assets, Blueprint semantics, references, and Editor state into searchable data;
2. provide AI agents with project context, impact analysis, and verification evidence;
3. execute narrow writes behind Policy / Revision / transaction / persistence / verification gates;
4. retain revision-aware project memory and auditable change evidence.

It is not a general remote desktop or an arbitrary Unreal Python / shell execution layer.

## Read capabilities

### Offline project data

- Asset Registry inventory and package metadata.
- Specialized readers for Static Mesh, Skeletal Mesh, Skeleton, Physics Asset, Material, Material Instance, Texture, Animation, DataTable, Data Asset, Niagara, World, and more.
- Blueprint Graph / Node / Pin structure, variables, functions, macros, interfaces, casts, delegates, and semantic links.
- Canonical JSON, BPCTX/1, and SQLite/FTS5 indexing.
- Asset / Symbol / Reference search and reverse-reference queries.
- Package SHA-256 revisions and index freshness comparison.

### Live Editor reads

- Editor / PIE/SIE / World / selection state.
- Open assets and dirty packages.
- Output Log and Blueprint compile diagnostics.
- Exact live asset inspection without using read queries to load unrelated assets.
- Current Blueprint graph and selected nodes.

### Project Memory / Knowledge

- Rules, findings, decisions, known issues, task records, and runtime evidence.
- Revision-aware stale / superseded / conflicted state.
- Deterministic L0 capture and L1 distillation.
- FTS5 recall plus optional Vector + RRF hybrid recall.
- Persisted L2/L3 project context and bounded automatic injection.
- Read-only Knowledge Web.

## Write capabilities

### Blueprint

Registered narrow operations such as variable defaults, component properties, pin defaults, and descriptions. Writes remain gated by Policy, Revision, Editor state, transactions, Undo/Discard, explicit Save, and Verify.

### Data Asset

- scalar properties;
- Object / Class / Soft Object / Soft Class references;
- complete Struct / Array / Set / Map values.

### Material Instance

- Scalar
- Vector
- Texture
- Static Switch

### DataTable

- cells;
- multi-field row updates;
- add / remove / rename row.

### Animation

Narrow AnimSequence diagnosis/fix, additive-base-pose, scale-fix, batch, and retarget-assistance tools. This is not a general animation-editor replacement.

## Agent workflow

- Task Context
- Relevant Asset Discovery
- Impact Analysis
- Change Set
- Semantic Diff
- Verification Plan
- Trust Verdict
- Authorized Save / Strong Verify
- Recovery / rollback evidence

## P4 / Perforce

Source Control support is opt-in.

Supported:

- mapping / opened / lock / owner / client / have / head queries;
- exact-file `p4 edit`;
- bounded safe sync;
- pending changelist query/create/description update;
- exact-file `reopen`;
- resolve preview;
- eligible plain-text `resolve -am`;
- durable audit receipts.

Explicitly unavailable:

- Agent-side P4 Submit;
- P4 Revert;
- P4-managed Delete;
- generic P4 command passthrough;
- automatic `.uasset/.umap` yours/theirs selection or content resolve.

## MCP tool surface

Source Control is disabled by default.

| Mode | Base | + Memory | + Source Control | + Memory + Source Control |
|---|---:|---:|---:|---:|
| Offline | 10 | 24 | 16 | 30 |
| Live | 43 | 57 | 49 | 63 |
| Workflow-only | 67 | 81 | 73 | 87 |
| Live + Workflow | 100 | 114 | 106 | 120 |

Tool counts represent interface surface, not mutation breadth; many tools are read-only queries, planning, verification, or status operations.

## Explicit non-goals in 0.8.0

- Arbitrary Blueprint Graph node CRUD / rewiring.
- Arbitrary Level Actor spawn/delete/transform/property editing.
- General Material Graph / Niagara / Sequencer / Control Rig writes.
- PIE input injection or deterministic recording/replay.
- Arbitrary asset import / duplicate / rename / delete / migrate.
- Arbitrary console, Python, shell, or UObject method execution.
- Automatic Save All.

## Safety model

- Understand before modifying.
- Read-only by default; writes require explicit enablement.
- Write Policy defines the writable scope.
- Revision checks reject stale plans.
- Dirty-package, session, and target-identity failures are rejected.
- Saving is not equivalent to task success; independent verification and evidence are required.
- Final P4 submit/revert/delete actions remain human operations.

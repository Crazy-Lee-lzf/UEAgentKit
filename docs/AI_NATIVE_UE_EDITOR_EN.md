# AI-Usable UE5 Editor Architecture

Updated: 2026-08-01

## 1. Goal

UE Agent Kit is not intended to be only a collection of Unreal MCP tools. Its long-term goal is to make UE5 an environment that an agent can understand, analyze, create, read, update, delete, run, verify, and maintain as durable project knowledge.

```text
Developer task
→ project knowledge and asset index
→ current Editor / PIE context
→ task analysis and Change Set
→ real-time Unreal CRUD
→ compile, validate, undo, or save
→ update the Knowledge Tree
```

The running Unreal Editor is the primary day-to-day development path. Offline exports, SQLite indexes, and commandlets support unloaded assets, project-wide queries, batch processing, independent verification, rollback, and CI.

## 2. Three fact sources

### Live Editor

Answers what is happening now: selection, open editors, Blueprint graphs, actors/components, dirty packages, compile state, PIE objects, logs, traces, collision, and runtime values.

### Project Model

Built from asset exports, Asset Registry, Revision Export, and SQLite. It answers what exists across the project: unloaded assets, symbols, references, inheritance, Blueprint structure, DataTable/Data Asset relationships, and stable revisions.

### Knowledge Tree

Answers why the project is designed this way and what has already been confirmed: architecture, rules, decisions, known issues, verification procedures, task progress, and evidence. Nodes bind to revisions and become stale after their source assets change.

Editor memory, disk packages, index snapshots, and durable knowledge must remain distinct and report provenance and freshness.

## 3. Core capability: CRUD

### Read

The highest priority. It includes live context, Blueprint semantics, runtime diagnostics, project search, reference analysis, map audits, DataTable checks, and value provenance. Batch queries must execute inside the plugin rather than producing one MCP round trip per object.

### Create

Create assets, DataTable rows, Blueprint variables/functions/nodes/components, Montage sections/notifies, animation states, actors, and project configuration. Template-based authoring is a primary use case, such as extracting the structure of Character A and creating a corresponding skill tree for Character B.

### Update

Update properties, references, containers, material parameters, DataTables, Blueprint defaults/pins/graphs, actors/components, animation, and batch configuration. Real-time updates modify Editor memory, enter a task-level transaction, and do not save automatically.

### Delete

Delete rows, nodes, links, components, actors, or assets. Risk determines whether link checks, reference impact analysis, source-control checks, backup, and rollback are required. Asset delete, rename, and migrate operations are not ordinary low-risk actions.

## 4. Agent-facing model

Agents should not receive hundreds of standalone MCP tools. Use three layers:

```text
Task workflow
    diagnoseWeaponHitFailure
    auditCurrentMap
    normalizeDataTable
    cloneSkillTree

Domain operations
    Blueprint / Actor / Animation / Data / Material / PIE

Base CRUD
    create / read / update / delete / compile / validate
```

The MCP surface remains small and stable. The Operation Registry filters the catalog by task, asset domain, and current Editor state to reduce schema size, token use, and incorrect calls.

## 5. Performance principles

### Hot path

Read selection, loaded objects, graphs, dirty state, PIE, and recent logs directly from Editor memory. These calls target millisecond-to-subsecond latency and do not perform backup or independent reload.

### Warm path

Use Asset Registry, SQLite, incremental exports, and caches for unloaded assets, references, and project search. Refresh only affected data after revisions change.

### Cold path

Run complete semantic exports, project rebuilds, independent Unreal reload verification, and broad impact analysis only when required.

Batch tasks must support frame budgeting, progress, cancellation, partial results, and summary-first output. Live Apply never starts a separate Unreal process. Heavy verification is deferred until save and may validate multiple assets together.

## 6. Risk-adaptive safety

| Level | Operation | Default protection |
|---|---|---|
| R0 | Read, search, diagnose, audit | scope, budget, timeout, cancellation |
| R1 | Reversible value update | exact target, local snapshot, transaction, read-back, no save |
| R2 | Structural or batch update | Change Set preview, atomic transaction, compile/validation |
| R3 | Save, delete, rename, migrate | policy, revision, source control, backup, independent verify, rollback |

Standard agent mode does not expose arbitrary Python, console commands, UObject methods, filesystem paths, Save All, or unrestricted property writes. Disabled tools and operations must disappear from discovery and still be rejected at execution time if called from a stale cache.

## 7. Reversibility

Related changes belong to one Change Set and as few Unreal transactions as practical. Results report exact session, transaction, target, before/after, dirty, and save state.

Reversal paths are:

- normal Editor Ctrl+Z;
- exact agent Undo by transaction;
- agent Discard that removes the transaction;
- revision-aware rollback from a Backup Manifest after save.

Undo validates that the transaction still matches, the target was not subsequently edited, and the package was not saved. Failure restores the snapshot and original dirty state instead of leaving partial mutation.

## 8. Initial acceptance scenarios

1. **Weapon Hit Diagnostic**: combine Blueprint/C++, collision, trace, PIE, and logs to identify why a weapon misses, with reversible experiments.
2. **Map Asset Audit**: scan the current map inside the Editor and aggregate asset/component issues by rule.
3. **DataTable Audit/Normalize**: whole-table analysis, table-level diff, selective acceptance, and one-transaction batch update.
4. **Clone Skill Tree**: extract reusable structure from Character A, create Character B's structure, replace identities/references, and detect residual references.
5. **Feature Design/Impact Analysis**: combine Knowledge Tree, project index, and live Editor state to propose architecture, impact, and staged Change Sets.

## 9. Branch ownership

- `feature/live-editor-realtime-io`: live context, real-time CRUD, batch tasks, runtime diagnostics, Change Sets, and transactions.
- `feature/memory-context`: Knowledge Tree, Active Work, Context Packs, evidence, and durable project understanding.
- `main`: stable integration baseline for verified shared contracts and capabilities.

The tracks connect through shared Task Context, Change Set, Evidence, Asset Identity, and Revision contracts. Shared contracts should land in `main` first and then be synchronized into both feature branches.

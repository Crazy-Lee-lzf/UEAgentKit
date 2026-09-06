# Layered Project Memory and Collaboration Architecture

Updated: 2026-08-03

> Version 0.8.0 provides fixed-project Project Memory with Knowledge Trees, Active Work, progressive context disclosure, deterministic L0/L1 processing, optional hybrid recall, persisted L2/L3 context, and on-demand evidence. A shared team Knowledge Service remains a future direction.

## 1. Goals

The current Project Memory architecture addresses four problems:

1. Organize knowledge by project structure instead of relying only on flat full-text records.
2. Use progressive disclosure so ordinary tasks do not load the entire knowledge base.
3. Separate durable knowledge from current work, TODO items, and blockers.
4. Enforce maintenance rules in the MCP server instead of depending on agent discipline.

Target behavior:

```text
Keep complexity inside the service
Expose only a few high-level tools to the agent
Return small summaries for ordinary tasks
Maintain deterministic Task/Revision/Evidence automatically
Prevent weaker agents from polluting the knowledge base
```

## 2. Four core concepts

### 2.1 Knowledge Tree

The tree answers where knowledge belongs. Nodes use stable paths, parent links, and arbitrary depth rather than fixed `level1/level2/level3` columns.

A default hierarchy may begin as:

```text
/project
/project/character
/project/character/skills
/project/character/skills/character-a
/project/character/skills/character-a/skill-1
```

Suggested node kinds:

```text
project
system
feature
component
entity
implementation
```

Node kind is descriptive and never limits depth.

### 2.2 Knowledge Record

Record type answers what kind of knowledge an item represents. The existing six record types remain:

```text
projectFact
projectRule
decisionRecord
knownIssue
taskRecord
runtimeEvidence
```

Future records bind to a node through `nodeId`. The tree becomes the primary navigation structure, while record type remains an audit, provenance, and status dimension.

### 2.3 Active Work

Current work must be stored separately from durable knowledge. Active Work contains:

```text
current objective
in progress
todo
blocked
pending decision
recently completed
```

Recommended states:

```text
planned
in_progress
blocked
done
cancelled
```

A work item should include a stable ID, project, title, status, priority, next action, blocker, related knowledge nodes, assets, owner, and timestamps.

Completion performs result distillation:

```text
Complete Active Work
→ extract durable conclusions
→ update the relevant Knowledge Node
→ persist automatic Task Evidence
→ archive the Work Item
```

Temporary investigation notes and unverified guesses do not become node summaries.

### 2.4 Evidence

Evidence proves conclusions but is not injected by default. It may reference patches, backup manifests, validation reports, automation reports, logs, Blueprint nodes or graphs, revisions, and external documents.

Node summaries and detailed records retain concise explanations and stable references. Raw evidence is expanded only at the final disclosure level.

## 3. Progressive disclosure

Memory reads use five levels:

- **Level 0 — index:** path, title, one-line summary, status, child count, and Active Work presence.
- **Level 1 — node summary:** core system or feature concepts without implementation details.
- **Level 2 — implementation overview:** primary classes, assets, data flow, entry points, dependencies, and known issues.
- **Level 3 — detailed records:** rules, decisions, findings, issues, revisions, and status.
- **Level 4 — raw evidence:** patches, logs, nodes, validation reports, and full artifacts.

Suggested default budgets:

```text
Project Profile        300–500 tokens
System Summary         300–600 tokens
Implementation         400–800 tokens
Related Records        300–1000 tokens
Active Work            100–300 tokens
```

An ordinary task should consume roughly 1,000–2,500 Memory tokens. The server must enforce result count, depth, and budget; a Skill reminder is not a safety boundary.

## 4. MCP and Skill responsibilities

Use **MCP as the primary mechanism and a thin Skill as guidance**.

```text
Agent
├─ one thin Skill for read/write order
└─ local MCP server for retrieval, budgets, maintenance, validation, and writes
```

The MCP server owns the knowledge tree, Active Work, progressive disclosure, token budgets, parent/path validation, duplicate detection, conflict coexistence, supersede, revision invalidation, workflow evidence binding, default status filters, and structured `nextActions`.

The Skill only explains that the agent should read the Project Profile first, expand only relevant systems and evidence, avoid recording ordinary conversation or guesses, place current tasks in Active Work, and distill only durable outcomes into long-term knowledge.

Do not split ordinary reading, writing, maintenance, and TODO behavior into several long Skills. Keep one 400–800 token `project-memory` Skill; load audit or migration Skills only when needed.

## 5. Implemented high-level MCP tools

These tools are available in 0.8.0:

```text
ue_memory_get_context
ue_memory_expand_node
ue_memory_get_evidence
ue_memory_update_knowledge
ue_memory_update_work
```

`ue_memory_get_context` is the normal first entry point and returns only the necessary Project Profile fragment, matched paths, system summaries, related Active Work, and suggested next actions.

`ue_memory_expand_node` expands one stable path to a requested depth and detail level.

`ue_memory_get_evidence` retrieves evidence only when a conclusion must be proven.

`ue_memory_update_knowledge` handles node creation, summary updates, durable conclusion writes, duplicate checks, and confirmation requirements.

`ue_memory_update_work` manages `start`, `add_todo`, `block`, `complete`, `cancel`, and `set_next_action` actions.

Deterministic Task Records, Revision Sets, and Workflow Evidence remain automatic outputs of UEAgentKit workflows rather than free-form agent writes. Active Work, Change Sets, Editor Sessions, and final Evidence are not yet automatically bound into one unified task object; that is cross-cutting integration work and does not block realtime CRUD expansion. Latency benchmarks for the Knowledge Tree, FTS, Context assembly, and large Memory databases are also still pending.

## 6. Single-user deployment

The single-user architecture remains:

```text
Agent
→ local MCP server
→ local SQLite / Memory DB
→ localhost Editor Bridge
→ local UE Editor
```

The local MCP combines the offline index, Memory, current Editor state, and controlled write workflows. The agent should not need multiple MCP connections.

## 7. Multi-user collaboration

Use a hybrid architecture: **one local MCP per developer and one shared knowledge service for the team**.

```text
Developer A: Agent → Local MCP A → Local Plugin/Editor
                              └→ Shared Knowledge Service
Developer B: Agent → Local MCP B → Local Plugin/Editor
                              └→ Shared Knowledge Service
```

Local-only state includes open assets, graph and selection, dirty packages, PIE/SIE, output logs, local workspace, policy, receipts, editor session, and unsaved memory changes.

The shared service stores project and team knowledge trees, public rules and decisions, known issues, durable implementation conclusions, team Active Work, owners, blockers, changelists, and audit references.

Recommended scopes:

```text
/project/...     project shared
/team/...        team shared
/user/...        user private
/session/...     local session
```

## 8. Shared storage and concurrency

Do not place one writable SQLite file on a NAS for concurrent team access.

Recommended storage:

```text
Local SQLite      asset index, cache, personal and session data
PostgreSQL/API    shared project knowledge, team work, and audit
```

Shared node updates use optimistic concurrency with `nodeId`, `expectedRevision`, and new content. A mismatch returns `knowledge-conflict`; Project Profiles and system summaries must never use silent last-write-wins behavior.

## 9. Schema v3 implementation status

The current Project Memory implementation adds these local tables and bindings on top of the base schema:

```text
knowledge_nodes
memory_records.node_id
active_work_items
active_work_node_links
active_work_asset_links
active_work_todos
```

Migration rules:

1. Upgrade Schema v2 databases in place while preserving record IDs, content digests, Evidence digests, Revision Sets, Scopes, Relations, Artifacts, and statuses.
2. Keep migrated legacy records at `node_id IS NULL` instead of guessing a node assignment.
3. Create Schema v3 directly for new databases and make repeated opens idempotent.
4. Enforce normalized absolute paths, same-project parents, cycle prevention, and the `/project` root for Knowledge Nodes.
5. Store Active Work node and asset links in normalized association tables rather than query-hostile JSON only.

## 10. Single-user MVP status

Implemented:

1. An arbitrary-depth Knowledge Tree with a Project Profile root.
2. Optional Knowledge Node bindings for all six existing record types.
3. Separate Active Work, TODO, blocker, and next-action state handling.
4. `ue_memory_get_context`, `ue_memory_expand_node`, and on-demand Evidence retrieval.
5. Levels 0–4 progressive disclosure, deterministic character budgets, default status filters, and structured `nextActions`.
6. Fixed-project high-level Knowledge/Work update tools and backward-compatible audit exports.

Automatic Context Packs, a shared knowledge service, identity and permissions, team scopes, and optimistic concurrency remain future work.

## 11. Non-goals

- Do not turn every conversation into long-term memory.
- Do not require weaker agents to manage six internal record types manually.
- Do not load all rules, recent tasks, or raw logs by default.
- Do not make Skills responsible for database consistency, deduplication, or Revision invalidation.
- Do not use one central MCP to control every developer's local UE editor.

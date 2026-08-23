# UE Agent Kit Roadmap

Updated: 2026-08-23

The latest published release is **0.7.0** for Unreal Engine 5.6 and remains unchanged. The 0.8.x Context / Analysis / Agent Reliability capability scope is locally closed on `feature/agent-reliability`, including R0–R4, R4.1 repeat evidence, a complete Read/Write Gap Audit, and Scope Freeze. This closeout does not change the published package/plugin version, create a tag, build release artifacts, or push.

## Direction

UE Agent Kit is evolving into an Unreal Engine project-intelligence layer for AI agents: traceable project inspection, policy-gated writes, Revision-aware memory, evidence-driven analysis, impact assessment, and verifiable change workflows.

Current server modes:

```text
Offline            10 tools (22 with Memory)
Live               43 tools (55 with Memory)
Workflow-only      60 tools (72 with Memory)
Live + Workflow    93 tools (105 with Memory)
```

## Completed foundation

- Project-wide asset and Blueprint export, Canonical JSON, BPCTX, Revision, Symbol/Reference, and SQLite/FTS5.
- MCP queries, stable pagination, Token Budgets, fixed-project safety, diagnostics, and four-source asset state.
- Restricted localhost Editor Bridge, logs, compile diagnostics, live inspection, Graph/Node location, daily actions, Automation Tests, and authorized one-asset saves.
- Live Editor Write foundation: 12 registered Data Asset, Material Instance, and DataTable Operations; shared Transaction/Evidence handling, explicit Undo/Discard, Authorized Save → Verify, a recoverable journal, Fast/Full real regressions, and registry-driven domain executors are complete.
- Controlled Blueprint, scalar non-Blueprint, Material Instance, DataTable, and Data Asset writes.
- Policy, Revision, Dry Run, explicit Commit, Backup Manifest, independent reload verification, and rollback.
- Atomic DataTable row-field updates, controlled row add/remove/rename, and exact Searchable Name reference-impact gates.
- Data Asset Object/Class and Soft Object/Class references plus stable Struct, Array, Set, and Map values with structured diffs.
- Unified native-JSON state/diff reports, Override/Expression GUID metadata, and real four-type regressions for Material Instance Scalar, Vector, Texture, and Static Switch parameters.
- Project/session/time-bound Data Validation and Automation evidence with stable Asset Revision Sets and explicit `not-applicable` Automation coverage.

## 0.5.x: complete

Version 0.5.5 closes the Live Editor, daily actions, controlled-write extensions, validation evidence, single-asset multi-operation transactions, CI, and release-engineering scope. Specialized Input Mapping Context writes remain demand-driven.

Complete Blueprint Graph, Material Graph, Anim State Machine, Control Rig, Sequencer, Niagara writes, and arbitrary script execution remain out of scope for 0.5.x.

## 0.6.0: Revision-aware project memory (complete)

Project facts, rules, decisions, known issues, task records, and runtime evidence preserve source, scope, time, confidence, and associated Revision sets.

Requirements:

- Separate `user-confirmed`, `tool-observed`, and `model-inferred` sources.
- Support `valid`, `stale`, `conflicted`, `superseded`, and `unverified` states.
- Mark related facts stale when an asset Revision changes.
- Preserve conflicting conclusions instead of silently overwriting them.
- Link Task Records to patches, backup manifests, verification reports, and final conclusions.

## 0.7.0: Live Editor Write foundation (released)

The current `main` line now includes 12 controlled Operations, generic `operation + assetPath + target + value` requests, separate Property/Material/DataTable domain modules, shared Transaction/Evidence handling, exact Undo/Discard, Authorized Save → Independent Verify, Memory Evidence, and a recoverable Live Apply journal. Every new Operation must still register explicit targets, policy, snapshot, undo, failure-restoration, and real UE regression coverage. Registration does not grant write authority or expose arbitrary UObject methods, scripts, or automatic saves.

See [`AI_NATIVE_UE_EDITOR_EN.md`](AI_NATIVE_UE_EDITOR_EN.md) and [`BRANCH_WORKTREES_EN.md`](BRANCH_WORKTREES_EN.md) for track ownership, performance budgets, risk levels, and Worktree integration rules.

## 0.7.0: Memory usability and layered knowledge tree (released)

Version 0.7.0 evolves the 0.6.0 flat record store into a low-maintenance, low-token, single-user usability layer; further Memory work continues on the long-lived feature branch:

- Stable paths and parent links provide an arbitrary-depth Knowledge Tree from Project Profile through systems, features/entities, and implementations.
- Separate durable knowledge, record type, Active Work, and Evidence.
- Store objectives, TODO items, blockers, and next actions in Active Work rather than long-term search records.
- Use five-level progressive disclosure with server-enforced token budgets and summary-first responses.
- MCP owns storage, retrieval, deduplication, Revision invalidation, automatic Evidence, and maintenance rules; one thin Skill only explains usage order.
- Provide high-level `ue_memory_get_context`, `ue_memory_expand_node`, `ue_memory_get_evidence`, `ue_memory_update_knowledge`, and `ue_memory_update_work` tools.

Next, bind task IDs, Active Work, Change Sets, Editor Sessions, and Evidence, and establish large-project latency baselines. This work does not block parallel Realtime readers and writers. See [`MEMORY_ARCHITECTURE_EN.md`](MEMORY_ARCHITECTURE_EN.md).

## 0.8.x: context, analysis, and agent reliability (capability scope complete)

R0–R4 now provide deterministic Task Context, bounded Impact Analysis, Change-Set-bound Semantic Diff, Evidence-gated Verification Plans and Trust Verdicts, and a real-agent benchmark with deterministic ground truth. C0–C6 then closed result enums, target semantics, Trust next-action guidance, narrow Blueprint rollback/reference normalization, repeat measurement, the capability audit, and release scope.

R4.1 retained all 24 attempts across four Full/Legacy paired anchors, with 12/12 fairness matches, zero measurement drift, zero infrastructure failures, and 24/24 exact recovery. Full achieved 3/3 Trusted results for both stale safe-stop and Blueprint default. It still produced 3/3 False Success on the direct-only high-fanout anchor and only 1/3 exact Trusted scalar claims because two claims stringified a numeric before-value. These failures remain visible and define known Agent-boundary limitations; they are not evidence for another UE read or write tool.

The audit covers all 105 public tools and 18 registered Patch Operations and concludes `0 must-fix new tools`. See [`Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md) and [`Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md).

Value Provenance and Execution Trace remain deferred by benchmark evidence. R5 may be reopened only after repeated real cases identify either as the primary blocker and show higher expected value than guidance, Writer, or Index fixes.

## 0.9.0: collaboration and conflict awareness

Read source-control provider, checkout, lock, owner, and head state; compare local dirty state, disk Revision, and depot/remote head; and model ownership boundaries and multi-user asset conflict risk. The first version may analyze, warn, or block, but must not steal locks or overwrite another developer's work.

Deployment uses one local MCP per developer plus a shared Knowledge Service. The local MCP connects to the local Editor Bridge and internally accesses the shared service; the agent should not orchestrate separate local-UE and shared-knowledge MCPs, and one central MCP must not route every developer's editor. Shared `/project` and `/team` knowledge and Active Work live in the service, while `/user`, `/session`, editor state, and asset indexes remain local. Shared updates use optimistic concurrency and explicit `knowledge-conflict` responses.

## Continuous gates

- Ruff, full Python tests, and JSON Schema validation.
- Unreal Engine 5.6 plugin build.
- Real Dry Run/Commit/reload/rollback regressions for affected write capabilities.
- UTF-8 without BOM, CRLF, whitespace, and complete-diff checks.
- Never commit output, backups, test-project assets, logs, caches, or local configuration.

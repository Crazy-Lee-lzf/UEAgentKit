# UE Agent Kit Roadmap

Updated: 2026-07-31

The latest published release is **0.6.0** for Unreal Engine 5.6. Revision-aware Project Memory is complete and in stable maintenance; `main` now contains the first Live Editor Write vertical slice, and active development covers both the Live Write foundation and 0.7.0 Context/Analysis.

## Direction

UE Agent Kit is evolving into an Unreal Engine project-intelligence layer for AI agents: traceable project inspection, policy-gated writes, Revision-aware memory, evidence-driven analysis, impact assessment, and verifiable change workflows.

Current server modes:

```text
Offline             5 tools (12 with Memory)
Live               23 tools (30 with Memory)
Workflow           26 tools (33 with Memory)
Combined           44 tools (51 with Memory)
```

## Completed foundation

- Project-wide asset and Blueprint export, Canonical JSON, BPCTX, Revision, Symbol/Reference, and SQLite/FTS5.
- MCP queries, stable pagination, Token Budgets, fixed-project safety, diagnostics, and four-source asset state.
- Restricted localhost Editor Bridge, logs, compile diagnostics, live inspection, Graph/Node location, daily actions, Automation Tests, and authorized one-asset saves.
- First Live Editor Write slice: after a Policy/Revision Plan, change one top-level scalar property on an open clean non-Blueprint asset, record Undo, mark Dirty, and never save automatically.
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

## Post-0.6 development snapshot: Live Editor Write

The first vertical slice is complete, but it only supports one top-level scalar property on an open, clean, non-Blueprint asset. The next step is not unrestricted UObject access: it is a shared Live Transaction/Evidence layer, explicit Undo/Discard, Reference and Structured Property support, Material Instance and DataTable live apply, and a standard Live Apply → Authorized Save → Verify → Memory Task closure.

## 0.7.0: context and analysis

Planned capabilities include automatic context packs, value-source and execution tracing, impact analysis, semantic asset diffs, evidence-backed hypotheses, change plans, and verification plans. Unsupported conclusions must be marked as inference.

## 0.8.0: collaboration and conflict awareness

Read source-control provider, checkout, lock, owner, and head state; compare local dirty state, disk Revision, and depot/remote head; and model ownership boundaries and multi-user asset conflict risk. The first version may analyze, warn, or block, but must not steal locks or overwrite another developer's work.

## Continuous gates

- Ruff, full Python tests, and JSON Schema validation.
- Unreal Engine 5.6 plugin build.
- Real Dry Run/Commit/reload/rollback regressions for affected write capabilities.
- UTF-8 without BOM, CRLF, whitespace, and complete-diff checks.
- Never commit output, backups, test-project assets, logs, caches, or local configuration.

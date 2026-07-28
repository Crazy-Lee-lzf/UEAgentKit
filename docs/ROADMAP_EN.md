# UE Agent Kit Roadmap

Updated: 2026-07-28

The latest published release is **0.5.1** for Unreal Engine 5.6. The `main` branch already contains unreleased 0.5.2–0.5.4 development work and is now in the 0.5.x consolidation stage.

## Direction

UE Agent Kit is evolving into an Unreal Engine project-intelligence layer for AI agents: traceable project inspection, policy-gated writes, Revision-aware memory, evidence-driven analysis, impact assessment, and verifiable change workflows.

Current server modes:

```text
Offline   5 tools
Live      23 tools
Workflow  25 tools
Combined  43 tools
```

## Completed foundation

- Project-wide asset and Blueprint export, Canonical JSON, BPCTX, Revision, Symbol/Reference, and SQLite/FTS5.
- MCP queries, stable pagination, Token Budgets, fixed-project safety, diagnostics, and four-source asset state.
- Restricted localhost Editor Bridge, logs, compile diagnostics, live inspection, Graph/Node location, daily actions, Automation Tests, and authorized one-asset saves.
- Controlled Blueprint, scalar non-Blueprint, Material Instance, DataTable, and Data Asset writes.
- Policy, Revision, Dry Run, explicit Commit, Backup Manifest, independent reload verification, and rollback.
- Atomic DataTable row-field updates, controlled row add/remove/rename, and exact Searchable Name reference-impact gates.
- Data Asset Object/Class and Soft Object/Class references plus stable Struct, Array, Set, and Map values with structured diffs.

## 0.5.x: daily-development consolidation

Remaining order:

1. **Unify Material Instance parameter workflows and reports** across Scalar, Vector, Texture, and Static Switch targets, values, override state, expression GUIDs, diffs, and reports.
2. **Bind validation evidence** from Automation Tests and Data Validation to the project, Editor session, timestamp, and relevant Asset Revision or Revision Set.
3. **Add atomic multi-operation transactions for one asset** with one Plan, Dry Run, backup, Commit, independent verification, and whole-transaction rollback.
4. **Close an official 0.5.x release** with consistent versions, changelog, release notes, artifacts, and Git tag.
5. Add specialized Input Mapping Context writes only when real project demand justifies them.

Complete Blueprint Graph, Material Graph, Anim State Machine, Control Rig, Sequencer, Niagara writes, and arbitrary script execution remain out of scope for 0.5.x.

## 0.6.0: Revision-aware project memory

Project facts, rules, decisions, known issues, task records, and runtime evidence must preserve source, scope, time, confidence, and associated Revision sets.

Requirements:

- Separate `user-confirmed`, `tool-observed`, and `model-inferred` sources.
- Support `valid`, `stale`, `conflicted`, `superseded`, and `unverified` states.
- Mark related facts stale when an asset Revision changes.
- Preserve conflicting conclusions instead of silently overwriting them.
- Link Task Records to patches, backup manifests, verification reports, and final conclusions.

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

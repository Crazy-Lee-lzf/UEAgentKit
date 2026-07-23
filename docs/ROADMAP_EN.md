# UE Agent Kit Roadmap

Updated: 2026-07-24

The current release is **0.5.1** and targets Unreal Engine 5.6.

UE Agent Kit is evolving into an Unreal Engine project intelligence layer for AI agents: independent project inspection, policy-gated writes, Revision-aware project memory, evidence-driven analysis, impact assessment, and verifiable change workflows.

## Completed checkpoints

```text
0.2.x  Project-wide read-only analysis, Canonical/BPCTX, SQLite/FTS
0.3.x  Blueprint and non-Blueprint safe-write foundations
0.4.0  Common Material Instance and DataTable writes
0.4.x  Backup Manifest, rollback, fixtures, and complete safety regression
0.5.0  First local MCP workflow: query, Plan, Dry Run, Commit, verify, rollback
0.5.1  MCP protocol completion: status, pagination, freshness, high-level writes, diagnostics, client compatibility
```

Version 0.5.1 completes the offline MCP query and controlled-write usability layer. The first 0.5.2 batch now provides an authenticated localhost Editor Bridge and six core live read tools; logs, compile diagnostics, live asset inspection, and safe index refresh remain next.

## 0.5.x: MCP and daily development tools

### 0.5.1: MCP query and protocol completion — completed

- [x] Capability, project, Engine, index, and workflow status.
- [x] Stable error envelopes and retry semantics.
- [x] Opaque continuation tokens, section pagination, filters, and Token Budgets.
- [x] SQLite / Revision Export / disk-package three-source freshness.
- [x] Commit stale lifecycle and exact rollback recovery.
- [x] Six high-level safe-change tools that generate strict Plans or run Dry Runs.
- [x] Separate Policy, Revision, Dirty, timeout, UE crash, and report diagnostics.
- [x] Official Python ClientSession and raw JSON-RPC stdio compatibility matrix, plus Claude Code schema and standard structured-content contracts. Hosted ChatGPT UI is not claimed as an automated end-to-end test.

### 0.5.2: Live Editor Read

Create a restricted local Editor Bridge with registered high-level capabilities only. Do not expose arbitrary UObject, Console, Python, Shell, or filesystem access.

Completed in the first batch:

- [x] Ephemeral `127.0.0.1` listener with a random per-session token.
- [x] Fixed project-path digest, exact Plugin/Server version, and capability handshake.
- [x] Stable offline degradation while SQLite tools remain available.
- [x] `ue_editor_status`.
- [x] `ue_get_selection`.
- [x] `ue_get_open_assets`.
- [x] `ue_get_dirty_assets`.
- [x] `ue_get_current_level`.
- [x] `ue_get_pie_state`.
- [x] Separate Editor memory, disk package Revision, and immutable SQLite snapshot semantics.
- [x] Real UE5.6 Editor plus MCP stdio smoke coverage with endpoint/token redaction.

Remaining 0.5.2 targets:

- [x] `ue_get_output_log`: a 4,096-entry ring buffer with a 1,024-character per-entry cap and sequence cursors and category, verbosity, keyword, UTC, and PIE filters.
- [x] `ue_get_compile_errors`: current-session compiler-related logs plus loaded Blueprint compile status, explicitly marked as incomplete history.
- [x] `ue_inspect_asset_live`: Asset Registry plus already-loaded memory state without triggering `LoadObject`.
- [ ] `ue_refresh_asset_index`: remains a separate follow-up batch because it must stage paired Revision Export and SQLite generations, atomically switch them, and become visible only to a new MCP session.
- [ ] Finer-grained Dirty UObject versus disk/index status.

### 0.5.3: daily actions and validation

Initial low-risk actions:

```text
ue_open_asset
ue_focus_asset
ue_sync_content_browser
ue_focus_actor
ue_compile_blueprint
ue_validate_asset
ue_validate_folder
ue_run_automation_test
ue_save_authorized_asset
```

Every save remains bounded by Policy, Revision, Dry Run, explicit confirmation, backup, and verification. There will be no unbounded `save_all`.

### 0.5.4: common data editing

- DataTable single-row multi-field changes and controlled row operations.
- Data Asset object/soft references, structs, and container value models.
- Unified Material Instance parameter workflows.
- High-level Blueprint default/component/pin editing refinements.
- Single-asset multi-operation atomic transactions.

Complete Blueprint Graph, Anim State Machine, Control Rig, Sequencer, and arbitrary script execution remain out of scope for this stage.

## 0.6.0: Revision-aware project memory

Project facts, rules, decisions, known issues, task records, and runtime evidence must retain sources, scope, timestamps, confidence, and associated Revision sets. Asset changes invalidate verified facts rather than silently preserving stale knowledge.

## 0.7.0: context and analysis

Planned capabilities include value-source tracing, execution tracing, impact analysis, semantic asset diff, evidence-backed hypotheses, change plans, and verification plans. Unsupported conclusions must be marked as inference.

## 0.8.0: collaboration and conflict awareness

Model source-control provider state, checkout/lock ownership, local versus remote Revision divergence, responsibility boundaries, and multi-user asset conflict risk. The system may analyze and warn, but must not steal locks or overwrite another developer's work.

## Version principles

- Production projects remain read-only by default.
- `.uasset` bytes are never edited directly.
- Compilation alone is not runtime validation.
- Every write capability requires bounded schema, Policy, Revision, Dry Run, backup, verification, and negative tests.

# UE Agent Kit Roadmap

Updated: 2026-07-21

The current release is **0.5.0** and targets Unreal Engine 5.6.

UE Agent Kit provides project-wide read-only analysis, SQLite/FTS indexing, low-risk Blueprint patching, generic non-Blueprint scalar properties, Global Scalar/Vector/Texture/Static Switch Material Instance parameters, and one top-level scalar field in one existing DataTable row.

## Current product version: 0.5.0

Version 0.5.0 is complete, with 0.4.0, 0.4.x, and 0.5.0 retained as independently verifiable checkpoints:

```text
0.4.0  Common non-Blueprint asset adapters
0.4.x  Explicit rollback and full safety regression
0.5.0  First MCP / Agent interface
```

## 0.4.0: common non-Blueprint writes (completed)

Completed operations:

- Material Instance Vector parameters: completed in 0.3.5.
- Material Instance Texture parameters: completed in 0.3.6.
- Material Instance Static Switch parameters: completed in 0.3.7.
- One top-level scalar field in one DataTable row: completed in 0.4.0.

Every operation retains exact policy allowlists, revision checks, dry-run rollback, unchanged on-disk hashes, pre-save backups, explicit commits, and independent UE-process reload verification. All 0.4.0 operations completed real-asset dry-run, commit, unique-backup, reload, and stale-revision tests. The active phase is now 0.4.x.

## 0.4.x: rollback and safety regression

Completed in 0.4.1:

- A standalone `rollback` command that defaults to dry run and requires an explicit commit.
- Automatic backup manifests, pre-rollback safety copies, unique receipts, and post-restore revision verification.

Completed in 0.4.2:

- Declarative Write Fixture Plans.
- Create/reset, source-class checks, target boundaries, sidecar rejection, and independent Unreal reload verification.

Completed in 0.4.3:

- Real Unreal coverage for Bool, Byte, Int32, Int64, Float, Double, String, Name, Text, `FEnumProperty`, and enum-backed Byte properties.
- 11 dry runs, 11 commits, per-step backups/manifests/independent reloads, and a final reset.
- Zero-write rejection regressions for unauthorized targets, missing properties, wrong types, stale revisions, numeric range errors, and invalid enum names.

Completed in 0.4.4:

- Dirty Package rejection through a strictly scoped test injection before any property mutation.
- Real temporary `.uexp` sidecar rejection with guaranteed cleanup.
- Save-failure injection after Commit backup creation, verifying an unchanged target revision, an available raw backup, and no success manifest; if an actual save changes the disk, the executor copies the backup and rechecks the restored revision.
- A complete matrix of 11 dry runs, 11 commits, and nine zero-write rejection paths.

The 0.4.x rollback and core safety-regression goals are complete. The next phase is the 0.5.0 MCP / Agent interface.

Arrays, sets, maps, object references, and arbitrary structs will not be exposed through permissive text import. They require a stable JSON value model and verifiable diffs first.

## 0.5.0: first MCP / Agent interface (completed)

Version 0.5.0 provides two local `stdio` modes:

- A default three-tool read-only query mode: `ue_search`, `ue_get_asset`, and `ue_find_references`.
- A fixed-project eight-tool workflow that additionally exposes `ue_plan_patch`, `ue_dry_run_patch`, `ue_apply_patch`, `ue_verify_asset`, and `ue_rollback_patch`.

Full-workflow safety boundaries:

- Database, Engine, Project, Policy, Revision Export, work root, and backup root are fixed at server startup and cannot be overridden by tool arguments.
- SQLite uses an immutable read-only snapshot and rejects active `-wal`, `-shm`, and `-journal` sidecars.
- Patches remain single-asset and single-operation, reusing the existing Policy, Revision, commandlet, backup-manifest, and rollback layers.
- Plan files and the Policy are digest-locked for the server session; external changes reject further execution.
- Commit requires a successful dry run, a one-time receipt, and exact `COMMIT <planId>` confirmation.
- Rollback commit requires a successful rollback dry run, a one-time receipt, and exact `ROLLBACK <applyReceipt>` confirmation.
- `ue_verify_asset` launches an independent Unreal process and checks the saved SHA-256 revision.

A real UE5.6 MCP client verified eight-tool discovery, zero-write dry run, invalid-confirmation rejection, commit, single-use receipts, independent reload, rollback dry run, explicit restore, and exact final `.uasset` hash restoration.

## After 0.5.0

Later stages include Blueprint variable creation/rename/removal, single-asset multi-operation transactions, graph node and pin editing, specialized Widget/Anim/Control Rig/Material/Niagara/Behavior Tree/StateTree adapters, Git/Perforce integration, CI, audit history, editor UI, and additional Unreal Engine versions.

## Version principles

- Each operation must independently complete schema, policy, dry run, commit, backup, reload, and negative tests.
- Compilation alone is not accepted as runtime validation.
- Production projects remain read-only by default.
- `.uasset` binary bytes are never edited directly.

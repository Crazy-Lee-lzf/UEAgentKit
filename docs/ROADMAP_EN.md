# UE Agent Kit Roadmap

Updated: 2026-07-21

The current release is **0.3.7** and targets Unreal Engine 5.6.

UE Agent Kit already provides project-wide read-only analysis, SQLite/FTS indexing, low-risk Blueprint patching, generic non-Blueprint scalar property patching, and Global Scalar, Vector, Texture, and Static Switch parameter patching for Material Instances.

## Next product target: 0.5.0

The next product-level target is 0.5.0, split into independently verifiable checkpoints:

```text
0.4.0  Common non-Blueprint asset adapters
0.4.x  Explicit rollback and full safety regression
0.5.0  First MCP / Agent interface
```

## 0.4.0: common non-Blueprint writes

Planned operations:

- Material Instance Vector parameters: completed in 0.3.5.
- Material Instance Texture parameters: completed in 0.3.6.
- Material Instance Static Switch parameters: completed in 0.3.7.
- One scalar field in one DataTable row.

Every operation must retain exact policy allowlists, revision checks, dry-run rollback, unchanged on-disk hashes, pre-save backups, explicit commits, and independent UE-process reload verification.

## 0.4.x: rollback and safety regression

Planned work:

- A standalone `rollback` command.
- Backup manifests and post-restore revision verification.
- Reproducible write-fixture generation and reset.
- Real UE coverage for all supported scalar types.
- Negative tests for unauthorized targets, missing targets, wrong types, revision conflicts, dirty packages, sidecars, and save failures.

Arrays, sets, maps, object references, and arbitrary structs will not be exposed through permissive text import. They require a stable JSON value model and verifiable diffs first.

## 0.5.0: first MCP / Agent interface

Planned high-level tools:

```text
ue_search
ue_get_asset
ue_find_references
ue_plan_patch
ue_dry_run_patch
ue_apply_patch
ue_verify_asset
ue_rollback_patch
```

The MCP layer will wrap the existing SQLite, patch, policy, revision, commandlet, and rollback layers. It will not expose arbitrary UObject calls, shell execution, or unrestricted file writes.

The 0.5.0 workflow target is:

```text
search
→ inspect structure and references
→ plan a patch
→ dry run
→ inspect structured results
→ explicit commit
→ independent verification
→ rollback when required
```

## After 0.5.0

Later stages include Blueprint variable creation/rename/removal, single-asset multi-operation transactions, graph node and pin editing, specialized Widget/Anim/Control Rig/Material/Niagara/Behavior Tree/StateTree adapters, Git/Perforce integration, CI, audit history, editor UI, and additional Unreal Engine versions.

## Version principles

- Each operation must independently complete schema, policy, dry run, commit, backup, reload, and negative tests.
- Compilation alone is not accepted as runtime validation.
- Production projects remain read-only by default.
- `.uasset` binary bytes are never edited directly.

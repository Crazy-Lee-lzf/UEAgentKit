# UE Agent Kit 0.4.4 Release Notes

UE Agent Kit 0.4.4 targets Unreal Engine 5.6. The formal release baseline commit is `daea768`.

> The current `main` branch also contains the first read-only MCP checkpoint for 0.5.0 development. That checkpoint is not part of the 0.4.4 release and does not yet provide an MCP write workflow.

## Highlights

### Project-wide reading and indexing

- Generic asset catalogs with Asset Registry tags, package metadata, SHA-256 revisions, and dependencies.
- Specialized readers for Static Mesh, Skeletal Mesh, Skeleton, Physics Asset, Material, Texture, Animation, DataTable, Data Asset, Niagara, and World assets.
- Blueprint graph, node, pin, variable, function, interface, macro, dynamic-cast, and dispatcher semantics.
- SQLite/FTS5 indexing for Asset, Symbol, and Reference queries.

### Safe writes

0.4.4 stabilizes the following policy-gated write capabilities:

- Blueprint variable defaults, component properties, pin defaults, and Blueprint descriptions.
- One scalar reflected property on a non-Blueprint asset.
- One unique Global Scalar, Vector, Texture, or Static Switch parameter on a Material Instance.
- One top-level scalar field in one existing DataTable row.

Every write still requires one asset and one operation, exact policy authorization, a matching SHA-256 revision, a clean package, no package sidecars, dry-run disk preservation, and an external backup before commit.

### Rollback and fixtures

- Backup manifests record policy hashes, authorization keys, before/after revisions, backup hashes, and sizes.
- Rollback defaults to validation-only dry run, creates a safety copy before explicit restore, and verifies the result in an independent Unreal process.
- Declarative Write Fixture Plans provide bounded create/reset behavior, plan revision locking, source-class checks, and independent reload verification.

## New 0.4.4 safety regression

0.4.4 adds three real failure paths on top of the complete 0.4.3 scalar matrix:

1. **Dirty Package** rejection before property mutation through a strictly scoped test injection.
2. **Real package sidecar** rejection using a temporary `.uexp`, followed by verified cleanup.
3. **SaveFailure** injection after backup creation, verifying an unchanged target revision, an available raw backup, and no success manifest. The executor also contains a backup-copy and revision-recheck path if disk state changes.

## Verification

```text
UE5.6 plugin build       passed
Python tests             101/101 passed
Scalar Dry Run           11/11 passed
Scalar Commit            11/11 passed
Expected failures         9/9 passed
Independent reload       passed
Final fixture reset      passed
Failure disk SHA-256     unchanged
```

The nine zero-write failure paths are unauthorized property, stale revision, wrong JSON type, Byte overflow, invalid Enum name, missing property, dirty package, package sidecar, and save failure.

## Out of scope for 0.4.4

- Arrays, sets, maps, arbitrary structs, and general object-reference writes.
- Multi-asset transactions or multi-operation atomic transactions.
- Blueprint node creation, deletion, or connection editing.
- Specialized graph editing for Widget Trees, animation state machines, Control Rig, Material Graph, or Niagara Graph.
- MCP write, verification, and rollback tools.

These capabilities require stable JSON value models, structured diffs, and verifiable rollback semantics before they can be exposed safely.

## Upgrading from 0.2.5

The GitHub remote previously remained at 0.2.5. After upgrading, rebuild the UE5.6 plugin, re-export asset and Blueprint canonical data, and rebuild the SQLite index. Obtain fresh revisions from the new export before executing any patch.

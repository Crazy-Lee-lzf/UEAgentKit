# UE Agent Kit 0.8.0 Release Notes

UE Agent Kit 0.8.0 targets **Unreal Engine 5.6 / Win64** and integrates project understanding, controlled writes, verification, Memory, and bounded P4 collaboration into a workflow intended for real UE projects.

## Highlights

### More complete agent workflow

- Task Context and relevant-asset discovery.
- Reverse-reference Impact Analysis.
- Change-Set-bound Semantic Diff.
- Verification Plans and Trust Verdicts.
- Resident Writer Fast Verify, Checkpoint Strong Verify, explicit Save, and recovery.
- Bounded multi-operation and multi-asset planning/execution.

### Project Memory

- Deterministic L0 automatic capture and L1 distillation.
- FTS5 recall plus optional Vector + RRF hybrid recall.
- Persisted L2/L3 context and bounded automatic injection.
- Revision-aware stale / superseded / conflict handling.

### Knowledge Web

A read-only Knowledge Web for navigating assets, references, symbols, Memory, and verification evidence without mutating the Unreal project.

### P4 / Perforce

New opt-in Source Control support includes:

- mapping / opened / lock / have/head inspection;
- exact-file `p4 edit`;
- strict safe sync;
- pending changelist query/create/update;
- exact-file `reopen`;
- resolve preview;
- eligible plain-text `resolve -am`;
- durable audit records.

The Agent still cannot perform P4 Submit, P4 Revert, or P4-managed Delete, and `.uasset/.umap` are never automatically content-resolved.

### Fresh clone and repository hygiene

- Windows launcher line endings no longer depend on a local `.git/info/attributes` override.
- Public P4 capability evidence no longer contains private client/host/server/fixture identifiers.
- Release documentation is product-facing; internal development plans and handoff logs are no longer part of the public release tree.

## Compatibility

```text
Unreal Engine 5.6
Windows 10 / 11
Python 3.11 / 3.12
P4 CLI (optional)
```

0.8.0 keeps the fixed-project MCP model, Write Policy, Revision gating, explicit persistence, and independent verification model.

## Upgrade

Use the `v0.8.0` Git tag for a fixed version, then rebuild local environments and plugin artifacts:

```bat
scripts\setup_python.cmd -WithMcp
scripts\BuildPluginDirect.cmd -EngineRoot "<UE_5.6>"
```

Do not copy an old `.venv`, `Build/Compiled`, Memory SQLite database, or test Output into a new project integration. Build a fresh project index, Memory store, and project-specific Write Policy.

## Known limitations

- No general Blueprint Graph CRUD.
- No arbitrary Level Actor editing.
- No general Material Graph / Niagara / Sequencer / Control Rig mutation.
- No arbitrary Python / console / shell / UObject method execution.
- No automatic Save All.
- P4 submit/revert/delete remain human operations.

## Validation

The 0.8.0 release candidate passes the portable Python full suite, Ruff, compileall, Release Validation, schema/patch-example validation, and fresh-clone checks before packaging. The formal release build additionally produces the UE5.6 Win64 plugin ZIP, Python wheel, SHA-256 checksums, and release manifest.

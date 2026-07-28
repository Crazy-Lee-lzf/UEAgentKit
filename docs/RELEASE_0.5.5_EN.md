# UE Agent Kit 0.5.5 Release Notes

UE Agent Kit 0.5.5 targets Unreal Engine 5.6 and closes the 0.5.x daily-development scope. It consolidates the Live Editor, bounded daily actions, write extensions, validation evidence, and transaction work developed across 0.5.2–0.5.4 into one official release while preserving the fixed-project, Policy, Revision, Dry Run, explicit Commit, package-backup, independent-verification, and rollback boundaries.

## Highlights

### Live Editor and fixed-project workflows

- Authenticated localhost Editor Bridge bound to a fixed Project Path Hash, random session token, exact Plugin/Server version, and capabilities.
- Bounded Output Log, compile diagnostics, non-loading asset inspection, and Blueprint Graph/Node location.
- Non-saving daily actions for asset focus, Content Browser sync, ActorGuid focus, in-memory Blueprint compilation, and Data Validation.
- Four-source asset state across Editor memory, disk package, Revision Export, and SQLite.
- Authorized one-asset save, independent verification, and atomic paired Revision Export/SQLite generation refresh.

Current modes:

```text
Offline   5
Live      23
Workflow  25
Combined  43
```

### Controlled write extensions

- Sixteen registered Patch operations and twelve high-level safe-write entry points.
- Atomic DataTable row-field updates, controlled row add/remove/rename, and exact Searchable Name reference-impact gates.
- Data Asset Object/Class and Soft Object/Class references, including `null` clearing.
- Stable top-level Struct, Array, Set, and Map JSON with Canonical ordering, deep restoration, and structured diffs.
- Unified native-JSON reports for Material Instance Scalar, Vector, Texture, and Static Switch parameters, including Override, source, Expression GUID, and complete rollback state.

### Validation evidence

- Data Validation and Automation results bind Project, Editor Session, UTC time, and an Evidence ID.
- Asset and folder validation include deterministic Revision Sets, before/after package SHA-256, Dirty provenance, and execution stability.
- Automation without asset input explicitly reports `revisionCoverage=not-applicable` and does not invent revisions.

### Atomic multi-operation transactions for one asset

- One asset may contain 1–32 compatible operations.
- Every target is prevalidated; duplicate targets and mixed executors are rejected.
- Commit creates one package backup, compiles a Blueprint once, and saves once.
- Dry Run uses `process-discard`; Commit uses `package-backup`.
- One backup manifest records every operation, before/after value, and exact authorization key in order, then rolls back the whole package.
- Structural DataTable row add/remove/rename operations remain single-operation patches to avoid reference and ordering ambiguity.

## Release engineering

- `scripts/ValidateRelease.py` checks version sources, bilingual release notes, Ruff, the full Python suite, three Schemas, sixteen example patches, and the example Policy.
- GitHub Actions covers Python 3.11 and 3.12 and builds the Python distribution.
- `scripts/BuildRelease.cmd` builds a UE5.6/Win64 plugin ZIP, Python wheel, `SHA256SUMS.txt`, and `release-manifest.json` from a clean worktree.

## Verification

```text
Ruff                                      passed
Python unittest                           201/201 passed
JSON Schema meta-validation               3/3 passed
Example Patch schema validation           16/16 passed
UE5.6 Direct plugin build                 passed
UE5.6 UAT Win64 plugin package            passed
Material four-parameter regression        passed
DataTable field/row/reference regressions passed
Data Asset reference/structured regressions passed
Single-asset transaction regression       passed
Independent reload and rollback           passed
Exact final Package Revision recovery     passed
```

Each transaction regression captures the initial revision after that run's fixture reset and requires the final post-rollback revision to match it byte-for-byte. The exact SHA-256 values are retained in that run's `summary.json`; fixture-reset hashes are not treated as cross-run release constants because resetting resaves the package.

## Upgrade from 0.5.1

1. Stop UE Agent Kit MCP servers and Unreal Editor.
2. Replace the complete `UEAgentKit` plugin directory; do not merge old binaries.
3. Rerun `scripts\setup_python.cmd -WithMcp` or install `ue-agent-kit[mcp]`.
4. Re-export revisions, rebuild SQLite, and require `ue_get_project_status` to report `fresh`.
5. Rediscover the 43 tools and active limits through `ue_get_capabilities`.
6. Multi-operation patches must use one asset, compatible operations, and exact per-target Policy authorization.

## Release artifacts

```text
UEAgentKit-0.5.5-UE5.6-Win64.zip
ue_agent_kit-0.5.5-py3-none-any.whl
SHA256SUMS.txt
release-manifest.json
```

Exact hashes are stored in the adjacent `SHA256SUMS.txt` and `release-manifest.json` files.

## Still excluded

- Multi-asset transactions.
- Arbitrary SQL, Shell, Console, Python, file overwrite, or UObject calls.
- Arbitrary Blueprint, Material, Animation, Control Rig, Sequencer, or Niagara graph editing.
- Writes to packages with external `.uexp/.ubulk/.uptnl/.m.ubulk/.upayload` sidecars.
- Hosted ChatGPT UI end-to-end automation.

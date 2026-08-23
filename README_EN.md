# UE Agent Kit

[中文](README.md)

> Convert Unreal Engine assets and Blueprints into searchable, traceable project knowledge for developers and AI agents.

UE Agent Kit is an open-source Unreal Engine asset analysis, indexing, and policy-gated patch toolkit. Its Editor plugin exports asset catalogs, Asset Registry metadata, dependencies, and Blueprint semantics; a Python CLI and SQLite provide a project-wide index, while Policy, Revision checks, dry runs, and backups protect explicit writes.

The latest published release is **0.7.0** for **Unreal Engine 5.6**. It formally integrates the Realtime Foundation, registry-driven Live Editor Write, Schema v3 Knowledge Trees and Active Work, progressive Context, frame-stepped batch tasks, durable Change Sets, and the complete Transaction/Evidence, Undo/Discard, authorized-save, and independent-verification workflow.

> **Current status**: the latest published release remains 0.7.0. The 0.8.x Context / Analysis / Agent Reliability capability scope is locally closed on `feature/agent-reliability`: R0–R4, the R4.1 repeat, the Read/Write Gap Audit, and Scope Freeze all have deterministic evidence. There are 0 must-fix new tools, and R5 remains deferred by benchmark evidence. Without Memory the modes expose 10/43/60/93 tools for Offline/Live/Workflow-only/Live + Workflow; fixed Project Memory changes them to 22/55/72/105. This closeout does not change the published 0.7.0 version, tags, or release artifacts.

> **AI Generated**: Most code and documentation in this project are AI-generated and reviewed through human inspection, UE 5.6 compilation, automated tests, and real-project regression validation.

## What it can do

- Inventory Static Meshes, Skeletal Meshes, Materials, Textures, Animations, DataTables, Niagara Systems, Worlds, and other assets.
- Search assets by name, path, or Asset Class.
- Query Hard/Soft Package dependencies and reverse references.
- Inspect Asset Registry Tags, package metadata, file size, and SHA-256 revision.
- Find where Blueprint variables are read or written.
- Trace functions, interface messages, macros, Dynamic Casts, and Event Dispatchers.
- Inspect Blueprint graphs, nodes, pins, and connections.
- Validate patches against policy, revision, and export snapshots, then dry-run or explicitly commit authorized Blueprint, non-Blueprint scalar, Data Asset Object/Class/Soft references and Struct/Array/Set/Map values, Material Instance parameters, and DataTable cell, multi-field, or controlled row-structure changes.
- Generate a backup manifest after every successful commit, then explicitly roll back and independently verify the restored revision when the current package still matches.
- Create or reset isolated test assets from a declarative Write Fixture Plan, then independently verify class, revision, and dirty state.
- Use the local MCP server to search assets/symbols, inspect assets and references, and create strict Plans or Dry Runs through 12 high-level safe-change tools without exposing shell, arbitrary SQL, or UObject access.
- Apply 12 controlled live changes to already open, initially clean Data Assets, Material Instances, and DataTables; revert them through exact Undo/Discard, independently verify authorized saves, and recover unfinished closeouts from the fixed journal after an MCP restart.
- Exercise Bool, integer, floating-point, String, Name, Text, and two Enum representations through real dry-run/commit/reload matrices, including zero-write rejections for authorization, stale revisions, wrong types, range errors, invalid enums, missing properties, dirty packages, sidecars, and save failures.

## Main capabilities

### Generic asset catalog

- Exports project assets visible to the Asset Registry.
- Excludes Blueprints and World Partition external Actor/Object packages by default to avoid duplicate or generated records.
- Records asset paths, Asset Classes, packages, chunks, Registry Tags, revisions, and dependency edges.
- The Static Mesh reader adds LOD/section counts, material slots, Nanite, bounds, lightmaps, collision, and sockets.
- The Skeletal Mesh reader adds Skeleton/Physics Asset links, LODs, materials, bounds, bone summaries, morph targets, and sockets; the Skeleton reader adds the full hierarchy, reference pose, virtual bones, sockets, compatibility entries, and curve metadata.
- The Physics Asset reader adds preview mesh, body-to-bone mappings, shape counts, disabled collision pairs, constraint endpoints/reference frames, and profiles.
- The Material reader adds domain, blend mode, shading models, two-sided/thin-surface flags, opacity mask, and expression-class summaries; Material Instance reader version 2 adds parent links, render properties, scalar/vector/texture/font/static-switch overrides, and Override/Expression GUID metadata for the four writable parameter types.
- The Material Function reader adds descriptions, library exposure, stable input/output GUIDs, types, preview defaults, and expression-class summaries.
- The Texture2D reader adds source dimensions/format, platform-data availability, compression, sRGB, LOD group, mip, filtering, addressing, streaming, and virtual-texture settings without reading pixels or BulkData.
- The Anim Sequence reader adds Skeleton, duration/sampling, additive, root-motion, notify, curve, and sync-marker data; the Anim Montage reader adds sections, slots, segments, notifies, and branching-point summaries.
- Blend Space and Aim Offset readers add axis settings and deterministically sorted samples; the DataTable reader adds row-struct, sorted row names, and structured row data.
- The generic Data Asset reader exports Edit/Blueprint/Config/Searchable properties, PrimaryAssetId, and object/soft-object paths for derived assets such as Input Actions, Input Mapping Contexts, and Primary Asset Labels.
- The Niagara System reader adds warmup/fixed-tick/bounds settings, user parameters, emitters, scripts, renderers, event-handler counts, and simulation-stage counts without reading simulation caches or GPU data.
- The World reader adds persistent-level, world-settings, streaming/world-partition, actor/component class counts, bounded actor details, and external-actor descriptor metadata when available, without loading external actors, triggering BeginPlay, or saving levels.
- The Reader Registry uses an Asset-Class binding table; Mesh, Material, Animation/Data, Niagara, and World readers compile as separate modules, while unknown classes safely fall back to the generic Asset Registry record.
- Avoids bulk loading every UObject during project-wide scans.

### Blueprint semantic analysis

- Reads parent classes, interfaces, variables, defaults, components, functions, and graphs.
- Exports nodes, pins, links, and common node properties.
- Models variable reads/writes, function and macro calls, interface messages, Dynamic Casts, delegates, inheritance, and interface implementation.
- Analyzes Hard/Soft Package, Soft Object/Class, Manage, and Searchable Name references.

### SQLite index

- Merges the generic asset catalog and Blueprint semantic exports into one database.
- Uses incremental SQLite/FTS5 indexing for assets, symbols, and references.
- Supports Unicode paths, pagination, and Asset Class filtering.

## Quick start

### Requirements

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio C++ Toolchain
PowerShell 5.1+
Python 3.11 or 3.12
```

### 1. Build the plugin

```bat
scripts\BuildPluginDirect.cmd
```

Default output:

```text
Build\Compiled\UEAgentKit
```

### 2. Export the generic asset catalog

```bat
scripts\RunAssetCatalog.cmd -Root "/Game" -Output "Output\AssetCatalog"
```

Export one asset:

```bat
scripts\RunAssetCatalog.cmd -Asset "/Game/Environment/SM_Wall" -Output "Output\SingleAsset"
```

### 3. Export Blueprint semantics

```bat
scripts\RunExport.cmd -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
```

Export one Blueprint:

```bat
scripts\RunExport.cmd -Asset "/Game/Characters/BP_Player" -Profile logic -Format both
```

### 4. Build a combined index

Both export directories can be imported into the same database:

```bat
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\ue-agent.cmd index stats
```

Query examples:

```bat
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search assets Manny --class Texture2D
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --target-asset /Game/LevelPrototyping/Materials/M_FlatCol.M_FlatCol
```

### 5. Validate and execute a patch

Export the target asset first to capture its current revision. Use deep export for Blueprints and the general asset catalog for non-Blueprint assets:

```bat
scripts\RunExport.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Asset "/Game/UEAgentKitWriteTests/BP_PatchTarget" ^
  -Profile full ^
  -Format json ^
  -Output "Output\PatchRevision"
```

Validate JSON, policy, and revision only:

```bat
scripts\ue-agent.cmd patch validate ^
  --patch examples\patches\set-variable-default.json ^
  --policy config\write-policy.example.json ^
  --export Output\PatchRevision ^
  --report Output\Patch\validation-report.json
```

Run an in-memory dry run. The commandlet mutates the UObject, captures the result, and restores the original value without saving; Blueprint operations compile after mutation and rollback, while non-Blueprint operations issue editor change notifications:

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "examples\patches\set-variable-default.json" ^
  -Policy "config\write-policy.example.json" ^
  -RevisionExport "Output\PatchRevision" ^
  -Mode DryRun
```

For an explicit commit, the policy must also set `commitEnabled=true`. The executor creates an external `.uasset` backup before saving one authorized asset; Blueprint operations must also compile successfully:

```bat
scripts\RunPatch.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Patch "examples\patches\set-variable-default.json" ^
  -Policy "config\write-policy.example.json" ^
  -RevisionExport "Output\PatchRevision" ^
  -Mode Commit ^
  -BackupDir "Backups\Patches"
```

After a successful commit, `RunPatch` automatically creates `<backup>.manifest.json` in the same backup directory. Rollback defaults to validation-only dry run:

```bat
scripts\RunRollback.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" ^
  -Manifest "Backups\Patches\<backup>.manifest.json" ^
  -Policy "config\write-policy.example.json" ^
  -BackupRoot "Backups\Patches" ^
  -Mode DryRun
```

Use `-Mode Commit` for an explicit restore. The target project must be closed; the tool saves a pre-rollback safety copy and launches an independent Unreal process to verify the restored SHA-256 revision.

Run the complete scalar regression:

```bat
scripts\RunScalarPatchRegression.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

The script creates an isolated native Data Asset fixture, runs 11 dry runs, 11 commits, nine expected failures, and resets the fixture to its defaults after a successful run.

Run the atomic DataTable single-row multi-field regression:

```bat
scripts\TestDataTableRowFields.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

The script performs Dry Run, Commit, independent reload, rollback Dry Run, and rollback Commit for two fields in one existing row, then verifies that the original revision and values are restored.

Run the DataTable row-structure regression:

```bat
scripts\TestDataTableRowOperations.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

The script runs Add Dry Run, Add Commit, Rename Commit, and Remove Commit, then rolls back Remove → Rename → Add in reverse order. Every stage is independently re-exported by Unreal, and the final package revision must exactly match the initial revision.

Run the Data Asset Struct/container-property regression:

```bat
scripts\TestDataAssetStructuredProperties.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

The script validates stable JSON, structured diffs, deep Dry Run restoration, Commit reload, and reverse rollback for Struct, Array, Set, and Map properties. The final package revision must exactly match the initial revision.

Run the single-asset multi-operation transaction regression:

```bat
scripts\TestMultiOperationTransactions.cmd ^
  -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject"
```

The regression applies two operations to a Data Asset and a Blueprint. Dry Run uses `process-discard` and preserves the disk revision. Commit creates one package backup, saves once, records every operation and authorization key in one manifest, independently reloads the result, then rolls back the whole transaction and requires exact baseline revision recovery.

The executor supports four Blueprint operations, scalar `setAssetProperty`, Data Asset-specific `setAssetReferenceProperty` and `setAssetStructuredProperty`, four Material Instance parameter operations, and DataTable field/row operations. One execution remains limited to one asset but may contain 1–32 compatible operations in one atomic transaction. Multi-operation execution pre-validates every target, creates one backup, compiles/saves once, and records all operations in one manifest. Exact Policy authorization remains per target. `setAssetStructuredProperty` replaces one top-level Struct, Array, Set, or Map through an explicit `valueType` envelope. Struct values must contain every field, while Set and Map values must be uniquely ordered by Canonical JSON; reports include a recursive structured diff. Only single-file packages without external package sidecars are accepted.

### 6. Run the MCP server (0.7.0)

Install the optional MCP dependency and validate the SQLite index:

```bat
scripts\setup_python.cmd -WithMcp
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
scripts\TestMcpStdio.cmd
scripts\TestMcpClients.cmd
```

Version 0.7.0 connects to a restricted fixed-project Live Editor Bridge and can enable Schema v3 Revision-aware Project Memory. The running Editor path includes bounded Context, frame-stepped Batch Tasks, durable Change Sets, and registry-driven Live Editor Write:

```bat
scripts\TestMcpLiveEditor.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"

scripts\TestMcpLiveWrite.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"

scripts\TestMcpLiveWriteFast.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"

scripts\TestMcpLiveWriteRegression.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"

scripts\TestMcpSnapshotRefresh.cmd ^
  -EngineRoot "<UE_5.6>" ^
  -ProjectPath "<TEST_PROJECT>.uproject"
```

The MCP Client still uses local `stdio` only. Without Memory, Offline, Live, Workflow-only, and Live + Workflow expose 10, 43, 60, and 93 tools; fixed Project Memory changes them to 22, 55, 72, and 105. Realtime paths provide bounded Editor Context, Output Log, compile diagnostics, current Graph/Node selection, frame-stepped `scanCurrentWorld`, paged Batch details, and durable Change Sets; R0–R3 add Task Context, Impact Analysis, Semantic Diff, Verification Plans, and Trust Verdicts. `ue_apply_asset_property_live` routes controlled Operations through registry-driven domain executors while preserving Plan, Policy, Revision, Transaction, exact confirmation, Undo/Discard, authorized one-asset saves, and independent Verify. Arbitrary SQL, Shell, Python, UObject methods, automatic saving, and Save All remain unavailable.

```bat
claude mcp add --transport stdio --scope project ue-agent-kit -- ^
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File ^
  "<TOOL_ROOT>\scripts\RunMcp.ps1" ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

Use `claude mcp list` or `/mcp` inside Claude Code to inspect the connection. Live Editor mode discovers a temporary loopback endpoint from the fixed project's `Saved/UEAgentKit/EditorBridge.json`, then validates a random token, project-path digest, exact version, and registered capabilities. Tool arguments cannot choose a port, token, arbitrary UObject, Console, Python, or Shell. Live reads include a 4,096-entry Output Log ring buffer, compile diagnostics, exact `/Game/...Asset.Asset` inspection that never loads the target, and focused Graph plus up to 100 selected Node GUIDs from ordinary Blueprint Editors. These reads report `loadedByBridge=false`. Daily Actions accept only exact `/Game` identities or a current-Editor-World ActorGuid, reject PIE/SIE, and never save packages; Blueprint compilation and Data Validation report any resulting memory Dirty state explicitly. `ue_get_asset_state` keeps Editor memory, disk Package, Revision Export, and SQLite distinct and never invents a memory Revision. Full write mode requires `-EnableWriteTools`; saving and restore additionally require `-EnableCommitTools`, a commit-enabled Policy, one-time receipts, and exact confirmation phrases. Planning still requires matching SQLite, Revision Export, and disk-package revisions. `ue_refresh_asset_index` accepts one exact policy-authorized asset and uses Preview/Apply to build and atomically activate a paired Revision Export plus SQLite generation. The current session remains frozen on its previous generation and rejects further workflow actions until MCP is restarted. See [`spec/MCP_SERVER.md`](spec/MCP_SERVER.md), [`spec/LIVE_EDITOR_BRIDGE.md`](spec/LIVE_EDITOR_BRIDGE.md), and [`spec/INDEX_FRESHNESS.md`](spec/INDEX_FRESHNESS.md).

### 7. Validate the asset catalog

```bat
python scripts\ValidateAssetCatalog.py --output Output\AssetCatalog --expect-exporter 0.7.0
```

See [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md) for installation and full command details.

## Output layout

Generic asset catalog:

```text
Output\AssetCatalog\
├─ manifest.json
└─ canonical\
```

Blueprint semantic export:

```text
Output\Blueprints\
├─ manifest.json
├─ canonical\
└─ bpctx\
```

- **Canonical JSON**: stable asset facts.
- **BPCTX/1**: compact Blueprint context for AI consumption.
- **SQLite Index**: project-wide Asset, Symbol, and Reference search.

## Documentation

- [`docs/PROJECT_STATUS_EN.md`](docs/PROJECT_STATUS_EN.md): implemented capabilities, explicit gaps, priorities, and direction.
- [`docs/COMPARISON_UE_LLM_TOOLKIT_EN.md`](docs/COMPARISON_UE_LLM_TOOLKIT_EN.md): read, write, and safety-model comparison with ue-llm-toolkit.
- [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md): build, install, export, and query instructions.
- [`docs/AI_USAGE.md`](docs/AI_USAGE.md): using the asset index and Blueprint semantics with AI tools.
- [`docs/MEMORY_ARCHITECTURE_EN.md`](docs/MEMORY_ARCHITECTURE_EN.md): layered knowledge tree, progressive disclosure, MCP/Skill responsibilities, and shared knowledge-service architecture.
- [`docs/MEMORY_ARCHITECTURE.md`](docs/MEMORY_ARCHITECTURE.md): Chinese memory architecture.

- [`docs/RELEASE_0.7.0_EN.md`](docs/RELEASE_0.7.0_EN.md): Realtime Foundation, registry-driven Live Write, Schema v3 Memory, Batch/Change Sets, and local release details.
- [`docs/RELEASE_0.6.0_EN.md`](docs/RELEASE_0.6.0_EN.md): Revision-aware Project Memory, evidence-bound tasks, audit export, and real UE5.6 closure.
- [`docs/RELEASE_0.5.5_EN.md`](docs/RELEASE_0.5.5_EN.md): 0.5.x daily-development capabilities, atomic transactions, validation evidence, and release closeout.
- [`docs/RELEASE_0.5.1_EN.md`](docs/RELEASE_0.5.1_EN.md): 0.5.1 query contract, high-level safe changes, diagnostics, and client compatibility.
- [`docs/RELEASE_0.5.0_EN.md`](docs/RELEASE_0.5.0_EN.md): 0.5.0 fixed-project MCP workflow release notes.
- [`docs/RELEASE_0.4.4_EN.md`](docs/RELEASE_0.4.4_EN.md): 0.4.4 release scope, verification, and upgrade notes.
- [`CHANGELOG.md`](CHANGELOG.md): version history summary.
- [`docs/ROADMAP_EN.md`](docs/ROADMAP_EN.md): released 0.7.0 capabilities, 0.8.0 Context/Analysis, and 0.9.0 collaboration direction.
- [`docs/Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](docs/Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md): full distributions, costs, and known limitations from the 24-attempt R4.1 paired repeat.
- [`docs/Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](docs/Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md): the 0.8 Read/Write Gap Audit and Scope Freeze for every public tool and registered operation.
- [`docs/Plans/UEAGENTKIT_0_8_RELEASE_REVIEW_20260823.md`](docs/Plans/UEAGENTKIT_0_8_RELEASE_REVIEW_20260823.md): 0.8 capability acceptance, formal-release boundaries, and final engineering gates.
- [`docs/Handoffs/UEAGENTKIT_0_8_CAPABILITY_CLOSEOUT_HANDOFF_20260823.md`](docs/Handoffs/UEAGENTKIT_0_8_CAPABILITY_CLOSEOUT_HANDOFF_20260823.md): final 0.8 capability closeout state, gate evidence, and continuation boundaries.
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md): BPCTX/1 format specification.
- [`spec/PATCH_SCHEMA.md`](spec/PATCH_SCHEMA.md): declarative patches, policy, revision checks, and validation-only safety boundaries.
- [`spec/BACKUP_AND_ROLLBACK.md`](spec/BACKUP_AND_ROLLBACK.md): backup manifest, rollback receipt, and restore-verification contract.
- [`spec/WRITE_FIXTURE_PLAN.md`](spec/WRITE_FIXTURE_PLAN.md): fixture plan, create/reset, and independent reload-verification contract.
- [`spec/SCALAR_PATCH_REGRESSION.md`](spec/SCALAR_PATCH_REGRESSION.md): complete scalar-type, positive-write, and rejection-path Unreal regression contract.
- [`spec/MCP_SERVER.md`](spec/MCP_SERVER.md): MCP tools, stdio transport, fixed configuration, and response contract.
- [`spec/LIVE_EDITOR_BRIDGE.md`](spec/LIVE_EDITOR_BRIDGE.md): restricted localhost IPC, fixed-project handshake, live reads, and Daily Actions.
- [`spec/INDEX_FRESHNESS.md`](spec/INDEX_FRESHNESS.md): three-source Revision freshness, stale lifecycle, and safe snapshot reload.

See [`docs/README.md`](docs/README.md) for the documentation index.

## Safety

Read-only exporters, SQLite queries, and `ue-agent patch validate` never modify UObjects or asset files. `ue_apply_asset_property_live` is the single in-editor memory-write entry point: it reuses an existing Policy/Revision Plan, changes only an already open and initially clean non-Blueprint, non-map asset, limits execution to the 12 currently registered Operations, records Undo, and marks the package Dirty without saving automatically. Every actual change is journaled and can be reverted by exact Transaction Undo/Discard; persistence still requires a separate authorized save followed by independent verification, or the isolated `BlueprintPatch`/`AssetPatch` commandlet path.

- `DryRun` is the default and must preserve the disk revision.
- `Commit` requires both an explicit command mode and `commitEnabled=true` in policy.
- Project, asset root, asset class, and operation must all be authorized; generic properties and Material parameters also require their own exact allowlist entries.
- An external backup is created before saving; every successful commit creates a non-overwriting manifest with the authorization key, policy hash, and before/after revisions.
- Rollback defaults to dry run. Commit requires a closed project, an unchanged post-commit revision, matching backup hash and size, and a pre-rollback safety copy.
- Blueprint compile failures, revision conflicts, dirty packages, target resolution failures, parameter lookup failures, and type validation errors prevent saving.
- The executor handles one asset with 1–32 compatible operations. Multi-operation transactions reject duplicate targets and structural DataTable row add/remove/rename operations to avoid partial saves and order-dependent ambiguity.
- Patches modify and save assets through Unreal Editor APIs; rollback only atomically restores a complete package authorized by a validated manifest and never performs partial binary edits.

## Release gates

Run the portable release validation suite with:

```bat
python scripts\ValidateRelease.py --require-release-docs
```

The command checks Python/Plugin/C++ version sources, bilingual release notes, Ruff, the full Python test suite, three JSON Schemas, sixteen Patch examples, and the example Policy. GitHub Actions runs the same gates on Python 3.11 and 3.12 and builds the Python distribution. UE5.6 plugin compilation and real-asset regressions remain local release-machine gates because they require an installed engine.

## License

UE Agent Kit is available under the [MIT License](LICENSE).

The project follows an independent implementation policy. Third-party projects are used only to study architecture, workflows, and Unreal API usage. See [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md).

UE Agent Kit is an independent open-source project and is not affiliated with, sponsored by, or endorsed by Epic Games, Inc. Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc. in the United States and elsewhere.

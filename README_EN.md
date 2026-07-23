# UE Agent Kit

[中文](README.md)

> Convert Unreal Engine assets and Blueprints into searchable, traceable project knowledge for developers and AI agents.

UE Agent Kit is an open-source Unreal Engine asset analysis, indexing, and policy-gated patch toolkit. Its Editor plugin exports asset catalogs, Asset Registry metadata, dependencies, and Blueprint semantics; a Python CLI and SQLite provide a project-wide index, while Policy, Revision checks, dry runs, and backups protect explicit writes.

The current release is **0.5.0** and targets **Unreal Engine 5.6**. Building on the policy, backup, and rollback guarantees in 0.4.4, version 0.5.0 adds a complete local MCP workflow for search, inspection, patch planning, dry run, explicit commit, independent verification, and two-stage rollback.

> **AI Generated**: Most code and documentation in this project are AI-generated and reviewed through human inspection, UE 5.6 compilation, automated tests, and real-project regression validation.

## What it can do

- Inventory Static Meshes, Skeletal Meshes, Materials, Textures, Animations, DataTables, Niagara Systems, Worlds, and other assets.
- Search assets by name, path, or Asset Class.
- Query Hard/Soft Package dependencies and reverse references.
- Inspect Asset Registry Tags, package metadata, file size, and SHA-256 revision.
- Find where Blueprint variables are read or written.
- Trace functions, interface messages, macros, Dynamic Casts, and Event Dispatchers.
- Inspect Blueprint graphs, nodes, pins, and connections.
- Validate patches against policy, revision, and export snapshots, then dry-run or explicitly commit authorized Blueprint, non-Blueprint scalar, Material Instance parameter, or DataTable cell changes.
- Generate a backup manifest after every successful commit, then explicitly roll back and independently verify the restored revision when the current package still matches.
- Create or reset isolated test assets from a declarative Write Fixture Plan, then independently verify class, revision, and dirty state.
- Use the local MCP server to search assets/symbols, inspect assets and references, and create strict Plans or Dry Runs through six high-level safe-change tools without exposing shell, arbitrary SQL, or UObject access.
- Exercise Bool, integer, floating-point, String, Name, Text, and two Enum representations through real dry-run/commit/reload matrices, including zero-write rejections for authorization, stale revisions, wrong types, range errors, invalid enums, missing properties, dirty packages, sidecars, and save failures.

## Main capabilities

### Generic asset catalog

- Exports project assets visible to the Asset Registry.
- Excludes Blueprints and World Partition external Actor/Object packages by default to avoid duplicate or generated records.
- Records asset paths, Asset Classes, packages, chunks, Registry Tags, revisions, and dependency edges.
- The Static Mesh reader adds LOD/section counts, material slots, Nanite, bounds, lightmaps, collision, and sockets.
- The Skeletal Mesh reader adds Skeleton/Physics Asset links, LODs, materials, bounds, bone summaries, morph targets, and sockets; the Skeleton reader adds the full hierarchy, reference pose, virtual bones, sockets, compatibility entries, and curve metadata.
- The Physics Asset reader adds preview mesh, body-to-bone mappings, shape counts, disabled collision pairs, constraint endpoints/reference frames, and profiles.
- The Material reader adds domain, blend mode, shading models, two-sided/thin-surface flags, opacity mask, and expression-class summaries; the Material Instance reader adds parent links, render properties, and scalar/vector/texture/font/static-switch overrides.
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

The executor supports four Blueprint operations, `setAssetProperty`, four Material Instance parameter operations, and `setDataTableCell`. One execution is limited to one asset and one operation. Generic properties, Material parameters, and DataTable fields use exact `allowedAssetProperties`, `allowedMaterialParameters`, and `allowedDataTableFields` authorization. Material Instance writes require one unique Global parameter; DataTable writes target one top-level scalar field in one existing row and restore the complete row during dry runs. Only single-file packages without external package sidecars are accepted.

### 6. Run the MCP server (0.5.0)

Install the optional MCP dependency and validate the SQLite index:

```bat
scripts\setup_python.cmd -WithMcp
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
scripts\TestMcpStdio.cmd
```

The server uses local `stdio` only. Default mode exposes five read-only capability, project-status, and query tools; fixed Engine, Project, Policy, and Revision Export settings enable the complete sixteen-tool workflow:

```bat
claude mcp add --transport stdio --scope project ue-agent-kit -- ^
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File ^
  "<TOOL_ROOT>\scripts\RunMcp.ps1" ^
  -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

Use `claude mcp list` or `/mcp` inside Claude Code to inspect the connection. Full workflow mode requires `-EnableWriteTools`; asset save and restore additionally require `-EnableCommitTools`, a commit-enabled Policy, one-time dry-run receipts, and exact confirmation phrases. Planning requires matching SQLite, Revision Export, and disk-package revisions. Six `ue_set_*` tools create a Plan by default or may run a Dry Run, but cannot commit directly. Commit marks the fixed snapshots stale; exact rollback can restore fresh state. Stop the server before rebuilding the index. See [`spec/MCP_SERVER.md`](spec/MCP_SERVER.md) and [`spec/INDEX_FRESHNESS.md`](spec/INDEX_FRESHNESS.md) for the full contract.

### 7. Validate the asset catalog

```bat
python scripts\ValidateAssetCatalog.py --output Output\AssetCatalog --expect-exporter 0.5.0
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

- [`docs/BUILD_AND_RUN.md`](docs/BUILD_AND_RUN.md): build, install, export, and query instructions.
- [`docs/AI_USAGE.md`](docs/AI_USAGE.md): using the asset index and Blueprint semantics with AI tools.
- [`docs/RELEASE_0.5.0_EN.md`](docs/RELEASE_0.5.0_EN.md): 0.5.0 MCP workflow, verification, and safety boundaries.
- [`docs/RELEASE_0.4.4_EN.md`](docs/RELEASE_0.4.4_EN.md): 0.4.4 release scope, verification, and upgrade notes.
- [`CHANGELOG.md`](CHANGELOG.md): version history summary.
- [`docs/ROADMAP_EN.md`](docs/ROADMAP_EN.md): version goals and safety boundaries for 0.4.0, 0.4.x, and 0.5.0.
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md): BPCTX/1 format specification.
- [`spec/PATCH_SCHEMA.md`](spec/PATCH_SCHEMA.md): declarative patches, policy, revision checks, and validation-only safety boundaries.
- [`spec/BACKUP_AND_ROLLBACK.md`](spec/BACKUP_AND_ROLLBACK.md): backup manifest, rollback receipt, and restore-verification contract.
- [`spec/WRITE_FIXTURE_PLAN.md`](spec/WRITE_FIXTURE_PLAN.md): fixture plan, create/reset, and independent reload-verification contract.
- [`spec/SCALAR_PATCH_REGRESSION.md`](spec/SCALAR_PATCH_REGRESSION.md): complete scalar-type, positive-write, and rejection-path Unreal regression contract.
- [`spec/MCP_SERVER.md`](spec/MCP_SERVER.md): MCP tools, stdio transport, fixed configuration, and response contract.
- [`spec/INDEX_FRESHNESS.md`](spec/INDEX_FRESHNESS.md): three-source Revision freshness, stale lifecycle, and safe snapshot reload.

See [`docs/README.md`](docs/README.md) for the documentation index.

## Safety

Read-only exporters, SQLite queries, the current MCP tools, and `ue-agent patch validate` never modify UObjects or asset files. Actual mutation is isolated in the `BlueprintPatch` or `AssetPatch` commandlet, selected by `RunPatch` only after pre-validation succeeds.

- `DryRun` is the default and must preserve the disk revision.
- `Commit` requires both an explicit command mode and `commitEnabled=true` in policy.
- Project, asset root, asset class, and operation must all be authorized; generic properties and Material parameters also require their own exact allowlist entries.
- An external backup is created before saving; every successful commit creates a non-overwriting manifest with the authorization key, policy hash, and before/after revisions.
- Rollback defaults to dry run. Commit requires a closed project, an unchanged post-commit revision, matching backup hash and size, and a pre-rollback safety copy.
- Blueprint compile failures, revision conflicts, dirty packages, target resolution failures, parameter lookup failures, and type validation errors prevent saving.
- The executor currently handles one asset and one operation to avoid partial saves.
- Patches modify and save assets through Unreal Editor APIs; rollback only atomically restores a complete package authorized by a validated manifest and never performs partial binary edits.

## License

UE Agent Kit is available under the [MIT License](LICENSE).

The project follows an independent implementation policy. Third-party projects are used only to study architecture, workflows, and Unreal API usage. See [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md).

UE Agent Kit is an independent open-source project and is not affiliated with, sponsored by, or endorsed by Epic Games, Inc. Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc. in the United States and elsewhere.

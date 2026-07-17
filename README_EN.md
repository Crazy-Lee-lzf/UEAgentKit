# UE Agent Kit

[中文](README.md)

> Convert Unreal Engine assets and Blueprints into searchable, traceable project knowledge for developers and AI agents.

UE Agent Kit is an open-source, read-only Unreal Engine asset analysis toolkit. Its Editor plugin exports the project asset catalog, Asset Registry metadata, dependencies, and Blueprint semantics. A Python CLI and SQLite then provide a project-wide searchable index.

The current release is **0.3.0** and targets **Unreal Engine 5.6**. It adds a validation-only Safe Write baseline while still avoiding UObject mutation and `.uasset` writes.

> **AI Generated**: Most code and documentation in this project are AI-generated and reviewed through human inspection, UE 5.6 compilation, automated tests, and real-project regression validation.

## What it can do

- Inventory Static Meshes, Skeletal Meshes, Materials, Textures, Animations, DataTables, Niagara Systems, Worlds, and other assets.
- Search assets by name, path, or Asset Class.
- Query Hard/Soft Package dependencies and reverse references.
- Inspect Asset Registry Tags, package metadata, file size, and SHA-256 revision.
- Find where Blueprint variables are read or written.
- Trace functions, interface messages, macros, Dynamic Casts, and Event Dispatchers.
- Inspect Blueprint graphs, nodes, pins, and connections.
- Validate declarative Blueprint patches against policy, revision, and export snapshots without loading or modifying UObjects.

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

### 5. Validate a declarative patch without writing assets

```bat
scripts\ue-agent.cmd patch operations
scripts\ue-agent.cmd patch validate ^
  --patch examples\patches\set-variable-default.json ^
  --policy config\write-policy.example.json ^
  --export Output\Blueprints ^
  --report Output\patch-report.json
```

This command only reads JSON and export snapshots. It always reports `willLoadOrModifyUObjects=false`, `willWriteDisk=false`, and `commitSupported=false`. See [`spec/PATCH_SCHEMA.md`](spec/PATCH_SCHEMA.md).

### 6. Validate the asset catalog

```bat
python scripts\ValidateAssetCatalog.py --output Output\AssetCatalog --expect-exporter 0.3.0
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
- [`spec/BPCTX_FORMAT.md`](spec/BPCTX_FORMAT.md): BPCTX/1 format specification.
- [`spec/PATCH_SCHEMA.md`](spec/PATCH_SCHEMA.md): declarative patches, policy, revision checks, and validation-only safety boundaries.

See [`docs/README.md`](docs/README.md) for the documentation index.

## Safety

The current release still never writes project assets. Commandlets do not save or directly edit `.uasset` files, and the Patch Baseline only validates JSON, policy, and exported revisions without loading or modifying UObjects.

## License

UE Agent Kit is available under the [MIT License](LICENSE).

The project follows an independent implementation policy. Third-party projects are used only to study architecture, workflows, and Unreal API usage. See [`docs/REFERENCE_POLICY.md`](docs/REFERENCE_POLICY.md).

UE Agent Kit is an independent open-source project and is not affiliated with, sponsored by, or endorsed by Epic Games, Inc. Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc. in the United States and elsewhere.

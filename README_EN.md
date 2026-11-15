# UE Agent Kit

[中文](README.md)

UE Agent Kit is an open-source **Unreal Engine 5.6** project-analysis and controlled-mutation toolkit. It turns `.uasset` data, Blueprint semantics, references, and live Editor state into searchable project knowledge, then lets AI agents perform narrow writes behind explicit safety gates. It is not a general graph editor or an arbitrary script executor.

The latest published release is **0.8.0** (stable). The latest published prerelease is **0.9.0 Developer Preview 1 (v0.9.0-dev1 / Python 0.9.0.dev1)** (2026-10-30); the next candidate is **0.9.0 Developer Preview 2** (planned version `0.9.0.dev2`, unreleased). Runtime target: **Unreal Engine 5.6 / Windows x64 / Python 3.11–3.14**.

## What it does

- **Project queries**: Asset Registry inventory; specialized readers for Static Mesh, Material, Texture, Animation, DataTable, Data Asset, Niagara, World, and more; SQLite + FTS5 indexing; forward and reverse references.
- **Blueprint reads**: graphs, nodes, pins, variable read/write sites, function/macro/interface/cast/delegate semantics, with saved snapshots kept distinct from live Editor state.
- **Live Editor**: current World, selection, open/dirty assets, Output Log, compile errors, PIE Server/Client objects, and explicitly requested scalar properties.
- **Controlled writes**: Data Asset, Material Instance, DataTable, AnimSequence assistance, and registered Blueprint operations such as variable defaults, component properties, and pin defaults. Writes stay behind Write Policy, Revision, dirty-package, transaction, explicit Save, and independent Verify.
- **Experimental Blueprint structure writes** (default off): one atomic patch with seven operations — `createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode`. Nodes can be created from an Action Registry `actionId`.
- **Agent workflow**: Task Context, Impact Analysis, Change Set, Semantic Diff, Verification Plan, Trust Verdict, and revision-aware Project Memory.
- **P4 / Perforce** (opt-in): mapping / opened / lock / have / head queries, exact-file `p4 edit`, bounded safe sync, pending changelists, reopen, and plain-text resolve. The Agent never runs Submit, Revert, or Delete.

## Quick start

### 1. Requirements

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio 2022 C++ toolchain
PowerShell 5.1+
Python 3.11–3.14
P4 CLI (only for Source Control)
```

### 2. Install

```bat
git clone git@github.com:Crazy-Lee-lzf/UEAgentKit.git
cd UEAgentKit
scripts\setup_python.cmd -WithMcp
scripts\BuildPluginDirect.cmd -EngineRoot "<UE_5.6>"
```

You can also compile the source plugin inside your project, or install a packaged build:

```bat
scripts\InstallProjectPlugin.cmd -Mode Source  -ProjectPath "<PROJECT>.uproject"
scripts\InstallProjectPlugin.cmd -Mode Package -ProjectPath "<PROJECT>.uproject"
```

### 3. Build the project index

```bat
scripts\RunAssetCatalog.cmd -Root "/Game" -Output "Output\AssetCatalog"
scripts\RunExport.cmd -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
```

### 4. Start a read-only MCP server

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

### 5. Let the Agent discover capabilities first

Call `ue_get_capabilities` after connecting, then query in the order `ue_search` → `ue_get_asset` → `ue_find_references`. A tool that is not registered is unavailable right now; do not infer availability from this README or from source code.

## Writes and experiments

**Stable writes** require all of: `--enable-write-tools`, `--enable-subprocess-workflow`, the project setting `[UEAgentKit] SubprocessWorkflow=True` in `Config/DefaultEngine.ini`, and a Write Policy that covers the target. Without them the `workflow` group does not appear in capabilities. `RunMcp.cmd` currently covers read-only and Pure Live modes only; write and experimental modes must run `.venv\Scripts\python.exe scripts\ue-agent-mcp.py` directly. Full commands are in [Build and Run](docs/BUILD_AND_RUN.md).

**Experimental Blueprint structure writes** additionally require the MCP flag `--enable-experimental-graphpatch-edit` and the project setting `[UEAgentKit] ExperimentalGraphPatchEdit=True`. Creating nodes from the Action Registry also requires `--enable-experimental-node-spawn` and `[UEAgentKit] ExperimentalNodeSpawn=True`. One Apply is one Editor transaction; a failure restores the operations already applied, and Save is always a separate explicit call. These tools bypass the stable Write Policy allowlist and are for disposable test projects only. See [Blueprint experimental usage](docs/BLUEPRINT_AGENT_E2E_USAGE.md).

**Machines without an index or Revision Export** can start the write mode against a live Editor (live-only): SQLite is not required, but offline revision evidence is lost and patch-based writes still require a Revision Export. On the read side, `ue_get_asset` now verifies a session overlay against the package on disk and re-proves it from the Editor when it changed; if it cannot, it returns `overlayStale` / `staleCause` instead of the old canonical. `ue_refresh_asset_index_live` refreshes one Blueprint explicitly without writing the index or requiring a restart.

## Current limitations

- No arbitrary graph CRUD, arbitrary Level Actor editing, or general Material Graph / Niagara / Sequencer / Control Rig writes.
- The experimental patch covers seven operations and top-level K2 event graphs only; it cannot create CustomEvent, event/delegate binding, Timeline, MathExpression, or `Select` / `PromotableOperator` themselves.
- No PIE input injection, arbitrary Asset Import/Duplicate/Rename/Delete/Migrate, or console / Python / shell / UObject method execution.
- No automatic Save All and no automatic compile.
- The Agent never runs P4 Submit / Revert / Delete and never auto-resolves `.uasset` / `.umap` content.

See [capability status](docs/PROJECT_STATUS_EN.md) for the full boundary, alternatives, and release status.

## Documentation

- [AI Agent Quick Start](docs/AGENT_QUICKSTART_EN.md) / [中文](docs/AGENT_QUICKSTART.md)
- [Capability status](docs/PROJECT_STATUS_EN.md) / [中文](docs/PROJECT_STATUS.md)
- [0.9.0 Developer Preview 2 (candidate)](docs/RELEASE_0.9.0_DP2_EN.md) / [中文](docs/RELEASE_0.9.0_DP2.md)
- [0.9.0 Developer Preview 1 (published)](docs/RELEASE_0.9.0_PREVIEW_EN.md) / [中文](docs/RELEASE_0.9.0_PREVIEW.md)
- [0.8.0 Release Notes](docs/RELEASE_0.8.0_EN.md) / [中文](docs/RELEASE_0.8.0.md)
- [Blueprint experimental usage](docs/BLUEPRINT_AGENT_E2E_USAGE_EN.md) / [中文](docs/BLUEPRINT_AGENT_E2E_USAGE.md)
- [Tool catalog](docs/TOOL_CATALOG.md)
- [Build and Run](docs/BUILD_AND_RUN.md)
- [Developer workflows](docs/DEVELOPER_WORKFLOWS_EN.md) / [中文](docs/DEVELOPER_WORKFLOWS.md)
- [Documentation index](docs/README.md)

## License

UE Agent Kit is licensed under the [MIT License](LICENSE).

UE Agent Kit is an independent open-source project and is not affiliated with, sponsored by, or endorsed by Epic Games, Inc. Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc.

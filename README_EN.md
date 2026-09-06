# UE Agent Kit

[中文](README.md)

UE Agent Kit is an open-source **Unreal Engine 5.6** project-analysis and controlled-mutation toolkit for AI agents and developers. It turns Unreal assets, Blueprint semantics, references, and live Editor state into searchable project knowledge, then exposes narrowly scoped write workflows with explicit safety gates.

The latest published release is **0.8.0**, targeting **Unreal Engine 5.6 / Windows / Python 3.11–3.12**.

## What it is for

UE projects keep important information inside `.uasset` packages, Blueprint graphs, Asset Registry metadata, and the running Editor. UE Agent Kit helps answer questions such as:

- What does this asset depend on, and what references it?
- Where is a Blueprint variable read or written?
- What are the current values in a DataTable, Material Instance, or Data Asset?
- What can be affected by a proposed change?
- Did an AI-generated edit actually persist to disk as intended?
- In a P4 workspace, is the file mapped, opened, locked, behind head, or unresolved?

```text
UE Editor / Asset Registry
        ↓
Canonical JSON / Blueprint semantic export
        ↓
SQLite / FTS5 project index
        ↓
MCP / CLI queries
        ↓
Plan → Policy → Revision → Apply → Save → Verify → Diff / Trust
```

## Main capabilities

### Asset and Blueprint queries

- Project-wide Asset Registry export and SQLite/FTS5 indexing.
- Specialized readers for Static Mesh, Skeletal Mesh, Material, Texture, Animation, DataTable, Data Asset, Niagara, World, and more.
- Blueprint Graph / Node / Pin structure, variable reads/writes, function/macro/interface calls, Dynamic Casts, delegates, and related semantics.
- Hard/Soft Package, Soft Object/Class, and Searchable Name references.
- Live Editor context including World, selection, open assets, dirty packages, Output Log, and Blueprint compile diagnostics.

### Agent workflow

- Task Context and relevant-asset discovery.
- Reverse-reference Impact Analysis.
- Change-Set-bound Semantic Diff.
- Evidence-gated Verification Plans and Trust Verdicts.
- Revision-aware Project Memory for rules, findings, decisions, task results, and validation evidence.
- A read-only Knowledge Web for navigating project knowledge and relationships.

### Controlled writes

The current write surface includes:

- Blueprint registered operations for variable defaults, component properties, pin defaults, descriptions, and related narrow edits.
- Data Asset scalar, object/class/soft references, and Struct/Array/Set/Map values.
- Material Instance Scalar / Vector / Texture / Static Switch parameters.
- DataTable cell, row-field, add/remove/rename operations.
- Narrow AnimSequence realtime fixes and retarget-assistance workflows.

Writes are not arbitrary scripting. They remain gated by **Write Policy, Revision, target identity, dirty-package state, transactions, explicit persistence, and independent verification**.

## P4 / Perforce integration

Source Control support is opt-in. 0.8.0 can:

- Inspect mapping, opened files, owner/client, locks, have/head revisions, and pending changelists.
- Run exact-file `p4 edit`.
- Perform strictly bounded safe sync when evidence allows it.
- Create/update pending changelists for the current user/client and `reopen` exact already-opened files.
- Preview resolve state and run bounded conflict-free text `resolve -am` where eligible.
- Persist source-control audit records and provide human final-action handoff metadata.

Safety boundary:

- **The Agent does not submit P4 changelists.**
- **The Agent does not run P4 revert.**
- **The Agent does not run P4-managed delete.**
- No generic P4 argv or shell passthrough is exposed.
- `.uasset` / `.umap` are never auto-resolved by choosing yours/theirs.

## Installation

### Requirements

```text
Windows 10 / 11
Unreal Engine 5.6
Visual Studio 2022 C++ Toolchain
PowerShell 5.1+
Python 3.11 or 3.12
P4 CLI (only when Source Control support is enabled)
```

### 1. Clone

```bash
git clone git@github.com:Crazy-Lee-lzf/UEAgentKit.git
cd UEAgentKit
```

### 2. Python environment

For MCP support:

```bat
scripts\setup_python.cmd -WithMcp
```

### 3. Build the UE5.6 plugin

```bat
scripts\BuildPluginDirect.cmd -EngineRoot "<UE_5.6>"
```

Default output:

```text
Build\Compiled\UEAgentKit
```

You may also link the source plugin into a project and let the project compile it:

```bat
scripts\InstallProjectPlugin.cmd -Mode Source -ProjectPath "<PROJECT>.uproject"
```

Or install the packaged plugin:

```bat
scripts\InstallProjectPlugin.cmd -Mode Package -ProjectPath "<PROJECT>.uproject"
```

## Build a project index

Export non-Blueprint assets:

```bat
scripts\RunAssetCatalog.cmd -Root "/Game" -Output "Output\AssetCatalog"
```

Export Blueprint semantics:

```bat
scripts\RunExport.cmd -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
```

Build the SQLite index:

```bat
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\ue-agent.cmd index stats
```

Example queries:

```bat
scripts\ue-agent.cmd search assets --class StaticMesh
scripts\ue-agent.cmd search symbols MaxWalkSpeed
scripts\ue-agent.cmd references --target-asset /Game/Characters/BP_Player.BP_Player
```

## MCP

UE Agent Kit uses local `stdio` MCP. In fixed-project mode, the database, project, Policy, Revision Export, and work roots are selected when the server starts; individual tools cannot substitute arbitrary paths.

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
```

See:

- [Build and Run](docs/BUILD_AND_RUN.md)
- [AI Usage](docs/AI_USAGE.md)
- [Project-level configuration](docs/PROJECT_LEVEL_CONFIG.md)
- [MCP Server contract](spec/MCP_SERVER.md)
- [Live Editor Bridge contract](spec/LIVE_EDITOR_BRIDGE.md)

## Write Policy

A Write Policy is UE Agent Kit's project-level write allowlist. It controls which projects, `/Game/...` roots, asset classes, operations, properties, DataTable fields, Material parameters, and reference targets may be changed, as well as whether Commit and Revision/clean-package requirements are enabled.

The repository includes an [example policy](config/write-policy.example.json). Real projects should create a minimal project-specific policy rather than opening all of `/Game`.

A recommended first integration is:

```text
Read-only audit
→ build index
→ verify P4 mapping
→ create a minimal Write Policy
→ select a test asset
→ Plan / Dry Run
→ explicit write
→ Save / Verify / Diff
```

## Current limitations

0.8.0 does not expose:

- Arbitrary Blueprint Graph node CRUD or automatic rewiring.
- Arbitrary Level Actor spawn/delete/transform editing.
- Arbitrary Material Graph / Niagara / Sequencer / Control Rig mutation.
- Arbitrary Unreal Python, console commands, shell execution, or UObject method invocation.
- Automatic Save All.
- Agent-side P4 submit, revert, or delete.

These restrictions are part of the product safety model and are not intended to be bypassed through generic scripting interfaces.

## Documentation

- [Documentation index](docs/README.md)
- [Current capability status](docs/PROJECT_STATUS_EN.md)
- [Public roadmap](docs/ROADMAP_EN.md)
- [0.8.0 Release Notes](docs/RELEASE_0.8.0_EN.md)
- [Memory Architecture](docs/MEMORY_ARCHITECTURE_EN.md)
- [Build and Run](docs/BUILD_AND_RUN.md)

## License

UE Agent Kit is licensed under the [MIT License](LICENSE).

UE Agent Kit is an independent open-source project and is not affiliated with, sponsored by, or endorsed by Epic Games, Inc. Unreal and Unreal Engine are trademarks or registered trademarks of Epic Games, Inc.

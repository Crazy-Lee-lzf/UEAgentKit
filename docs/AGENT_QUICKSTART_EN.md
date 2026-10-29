# AI Agent Quick Start

> For users and AI agents accessing UE Agent Kit through MCP. **Check the deployed version and discover the runtime tools before taking action.** This guide does not grant permission to bypass project or write safety checks.

## Version and safety boundary

- The latest published prerelease is [`v0.9.0-dev1`](RELEASE_0.9.0_PREVIEW_EN.md); the previous stable release is [`v0.8.0`](RELEASE_0.8.0_EN.md). See [Capability Status](PROJECT_STATUS_EN.md). Features present only in a local development branch are not necessarily shipped.
- Start read-only. Stable writes require an explicitly enabled server mode, a fixed Unreal project, a scoped Write Policy, and a valid revision/confirmation flow.
- Exception: the default-off `--enable-experimental-graphpatch-edit` Blueprint structure tools do not use Write Policy. They are bounded by the experimental flag, the project ini, exact plan/confirmation, asset/graph identity, and clean/PIE checks, and must not be used on a production project.
- The 0.9.0 DP1 release does not expose unrestricted Blueprint Graph CRUD, arbitrary Actor/asset mutations, arbitrary shell/Python execution, or Save All. Some native tools and Memory Mount staging remain gated or experimental; discover capabilities at runtime.

## 1. Prepare the environment

From the repository root, after installing the UE plugin for your project as described in [Build and Run](BUILD_AND_RUN.md):

```bat
scripts\setup_python.cmd -WithMcp
scripts\RunAssetCatalog.cmd -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" -Root "/Game" -Output "Output\AssetCatalog"
scripts\RunExport.cmd -ProjectPath "<PROJECT_ROOT>\ProjectName.uproject" -Root "/Game" -Profile full -Format both -Output "Output\Blueprints"
scripts\ue-agent.cmd index build Output\AssetCatalog
scripts\ue-agent.cmd index build Output\Blueprints
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3" -Check
```

Replace the example paths with your own project and tool paths. These are placeholders, not literal values.

Start the read-only stdio MCP server and connect it from your MCP client:

```bat
scripts\RunMcp.cmd -Database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

Live Editor and writable Workflow modes require additional server-side startup configuration. An agent cannot switch a session into write mode using a Tool parameter.

## 2. Discover and read first

After connecting, call `ue_get_capabilities` and inspect the actual registered tools and their schemas. Follow the advertised argument types rather than guessing parameters or relying on a fixed tool count.

Suggested read path:

```text
ue_get_capabilities
→ ue_get_project_status
→ ue_search
→ ue_get_asset
→ ue_find_references
→ report exact Asset Path, Revision, evidence, and unknowns
```

Only call Live Editor tools if present and the Editor connection is available. Distinguish indexed snapshots from current in-memory/dirty package state.

## 3. Explicitly authorized write path

Do not write unless the user has requested it and the server exposes the required capability for a fixed, Policy-authorized target.

```text
ue_plan_patch
→ ue_dry_run_patch
→ present the exact target/Diff and obtain explicit approval
→ ue_apply_patch using the server-issued Receipt and confirmation
→ explicit Save, if the workflow requires one
→ ue_verify_asset independently
→ report verified results and any outstanding failures
```

Never invent a Receipt, asset Revision, confirmation phrase, or backup reference. A successful Dry Run is not permission to Commit; an in-memory apply does not prove disk persistence; a Save does not substitute for Verify.

If a guard fails, stop writing and return the error. Follow [Backup and Rollback](../spec/BACKUP_AND_ROLLBACK.md) for an authorized rollback with a new Dry Run, confirmation, commit, and verification. Receipts may become invalid when the server restarts or the snapshot changes.

## Further reference

- [Detailed AI Usage](AI_USAGE.md)
- [Programming and PIE inspection workflows](DEVELOPER_WORKFLOWS_EN.md)
- [MCP server contract and Tool interfaces](../spec/MCP_SERVER.md)
- [Project configuration and Write Policy](PROJECT_LEVEL_CONFIG.md)
- [Build and Run](BUILD_AND_RUN.md)
- [Published Capability Status](PROJECT_STATUS_EN.md)

The actual available Tool surface is defined by the installed release, `ue_get_capabilities`, server safety gates, and verification—not by the existence of development code.

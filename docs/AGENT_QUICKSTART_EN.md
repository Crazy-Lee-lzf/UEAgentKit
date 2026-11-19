# AI Agent Quick Start

> For users and AI agents accessing UE Agent Kit through MCP. **Confirm the deployed mode, then call only the tools returned by `ue_get_capabilities`.** This guide grants no permission to bypass project or write gates.

## 1. Confirm the runtime mode

UE Agent Kit registers tools **dynamically from server flags**. A missing tool usually means the mode is not enabled on the server, not that the agent called it incorrectly.

| Mode | Start | What you should see |
|---|---|---|
| Offline read-only | `RunMcp.cmd -Database ...` | query, offline GraphPatch preview (Memory requires a separate flag) |
| Pure Live read-only | `RunMcp.cmd -EnableLiveEditor -PureLive -ProjectPath ...` | live-read; no index, no writes |
| Live + index | `RunMcp.cmd -EnableLiveEditor -ProjectPath ... -Database ...` | live-read + query |
| Controlled writes | direct `.venv\Scripts\python.exe scripts\ue-agent-mcp.py` with write-tools + subprocess + Policy | `workflow` group |
| Experimental Blueprint | direct `py -m ...` with graphpatch / node-spawn double gates | atomic patch, node creation |
| P4 | direct `py -m ... --enable-source-control --project ...` | Source Control |

`RunMcp.cmd` covers read-only and Pure Live only; full commands for writes and experiments are in [Build and Run](BUILD_AND_RUN.md). Controlled writes require all of:

```text
MCP:     --enable-write-tools --enable-subprocess-workflow
Project: Config/DefaultEngine.ini -> [UEAgentKit] SubprocessWorkflow=True
Policy:  the target asset/operation is covered by the Write Policy
```

Do not forget the profile: launching `ue-agent-mcp.py` directly defaults to `core`
(the query group only). Writes and experiments need `--tool-profile full` or
`--tool-profile live-write`, or name the groups yourself with `--tool-groups`
(for example `query,workflow`).

Experimental Blueprint writes bypass the Write Policy and instead require `--enable-experimental-graphpatch-edit` + `[UEAgentKit] ExperimentalGraphPatchEdit=True`; Action Registry node creation adds `--enable-experimental-node-spawn` + `[UEAgentKit] ExperimentalNodeSpawn=True`.

## 2. One-time user setup

Installation, plugin wiring, export/index, fixed projects, and Policy are documented in [Build and Run](BUILD_AND_RUN.md); the shortest version is in the [README quick start](../README_EN.md). The agent does not repeat those steps; it only confirms that the server is running.

## 3. First agent step: discover capabilities

Call `ue_get_capabilities` and check:

- Which groups are enabled: query / live-read / workflow / memory / source-control.
- Whether `workflow` exists. If not, no stable write tool is available; ask the user to check the double gate.
- Whether the experimental patch and `ue_search_node_actions` appear. Do not pretend to call tools that are absent.

Do not infer availability from README counts, source handlers, or historical release notes.

## 4. Read-only queries

```text
ue_get_capabilities
→ ue_get_project_status
→ ue_search (locate an asset or symbol first)
→ ue_get_asset (only the detail you need)
→ ue_find_references (dependencies and impact)
→ live-read tools only when current Editor state matters
```

Keep three kinds of output separate:

- **Fact**: directly from Canonical JSON, BPCTX, or SQLite.
- **Inference**: interpretation based on references, naming, or call relationships.
- **Unknown**: fields the current reader or index did not export.

SQLite is a saved snapshot; the live Editor is current memory. When `ue_get_asset` carries overlay metadata, read `overlayCause`, `overlayRefreshed`, `overlayStale`, and `staleCause`: if `overlayStale=true`, do not treat that canonical as current disk state.

## 5. Controlled writes

**Do not write by default.** Write only when the user asked for it, the workflow gate is enabled, the target is covered by the Write Policy, and revision/dirty/identity checks pass:

```text
ue_patch(action="plan")
→ ue_patch(action="dry_run")
→ show target asset, diff, and risk; obtain explicit authorization
→ ue_patch(action="apply") (one-time receipt / exact confirmation)
→ explicit Save when needed
→ ue_patch(action="verify") (independent verification)
→ report saved / verified / failed items
```

For one already-open, clean, non-Blueprint asset, `ue_asset_property(action="apply")` can change the Editor memory value. It enters the Undo stack and marks the package dirty, but **does not save**. Save separately with `ue_blueprint_edit(action="save")`; discard with `ue_asset_property(action="undo")` or `ue_asset_property(action="discard")`.

Rules:

- Never guess receipts, revisions, confirmation strings, or backup references from natural language.
- A passing Dry Run does not authorize Commit; a successful live apply does not mean the package was saved; a successful save does not prove independent verification.
- On failure, stop writing and return the error code and current state. For rollback, follow [Backup and rollback](../spec/BACKUP_AND_ROLLBACK.md): Dry Run → confirmation → Commit → verification.
- Receipts may expire after an MCP restart or snapshot refresh; re-plan instead of reusing them.

## 6. Experimental Blueprint structure writes

Experimental, default off, disposable projects only. Shortest path:

```text
ue_search_node_actions (optional; obtain actionId)
→ ue_preview_blueprint_patch_experimental (obtain planId / confirmation)
→ ue_apply_blueprint_patch_experimental (pass the confirmation returned by preview)
→ ue_save_blueprint_patch_experimental (separate explicit save)
```

One Apply is one transaction; a failure returns per-operation results and restores what was already applied. Operations, limits, and error codes are in [Blueprint experimental usage](BLUEPRINT_AGENT_E2E_USAGE_EN.md).

Related: [capability status](PROJECT_STATUS_EN.md) · [Build and Run](BUILD_AND_RUN.md) · [developer workflows](DEVELOPER_WORKFLOWS_EN.md) · [MCP contract](../spec/MCP_SERVER.md).

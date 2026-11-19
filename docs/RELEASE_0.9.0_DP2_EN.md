# UE Agent Kit 0.9.0 Developer Preview 2 (candidate)

**Status: candidate, unreleased.** The planned version is `0.9.0.dev2`; the release commit will bump it consistently, and the current checkout's version is defined by `pyproject.toml` / the plugin. There is no GitHub tag or Release. The latest published prerelease remains [0.9.0 Developer Preview 1](RELEASE_0.9.0_PREVIEW_EN.md); the stable release remains [0.8.0](RELEASE_0.8.0_EN.md). This page describes candidate code, not a production authorization.

## What changed since DP1

- **One atomic Blueprint patch**: seven operations — `createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode` — now live in one tool family (`ue_preview_blueprint_patch_experimental` / `ue_apply_blueprint_patch_experimental` / `ue_save_blueprint_patch_experimental`). One Apply is one Editor transaction with per-operation results, rollback of already-applied work on failure, and Undo/Redo verification; Save remains a separate action.
- **Action Registry node creation**: `createNode` can use an `actionId` returned by `ue_search_node_actions` to create `K2Node_CallFunction` and `K2Node_DynamicCast`. Engine functions, project C++ `BlueprintCallable` functions, and Blueprint-defined functions are eligible; the Cast target class is chosen by the engine spawner. Search reports `canCreate` / `rejectReason` before a write.
- **Wildcard connections**: `Select` / `PromotableOperator` wildcard pins can be connected; the K2 schema derives the type during the write, and `resolvedPins` returns the post-connection pin snapshot. `createNode` still cannot create these node classes.
- **Live-only write startup**: a machine without a SQLite index or Revision Export can start the write mode against a live Editor. Bridge-native writes (such as node positions and edit sessions) need no index; patch-based writes still require a Revision Export at use time. The trade-off is the loss of offline revision evidence.
- **Live index overlay**: when `ue_get_asset` hits a session overlay record, it verifies the record against the package SHA-256 on disk and re-proves it from the Editor when the disk changed. If that fails it returns `overlayStale` / `staleCause` instead of the old canonical. `ue_refresh_asset_index_live` explicitly refreshes one Blueprint without writing the index or requiring a restart.
- **User and Agent can edit the same graph**: position batches / edit sessions may begin on a dirty package, check per-node expected coordinates, and roll back only nodes the Agent wrote last and the user has not changed, reporting skipped nodes. The structural patch still requires a clean package; the two boundaries are different.
- **Narrower subprocess workflow**: the `workflow` group is not registered by default. It requires both `--enable-subprocess-workflow` and the project setting `[UEAgentKit] SubprocessWorkflow=True`. Every spawn attempt is recorded in a bounded session ledger; the read-only `ue_get_subprocess_ledger` exposes it.
- **Old writer tools removed**: standalone pin-default / variable / rewire / node-remove preview/apply/save tools and the graph-edit shell are no longer registered; their capabilities moved into the atomic patch.

## Tool surface (one of the main DP2 changes)

DP1 had one tool per action, so connecting a session pulled in well over a hundred definitions. DP2 reduces that in two ways.

**One — family entry points.** Twelve families became `action` entry points, and **81 old names are no longer registered or advertised**:

| Entry point | Actions |
|---|---|
| `ue_pie` | `state` / `worlds` / `list_actors` / `inspect_actor` / `scalars` / `list_properties` / `read_properties` |
| `ue_datatable` | `set_cell` / `set_row_fields` / `add_row` / `remove_row` / `rename_row` |
| `ue_memory` / `ue_memory_write` | seven read actions / record writes plus the knowledge and work sub-actions |
| `ue_patch` | `plan` / `dry_run` / `apply` / `verify` / `rollback` / `state` / `refresh_index` |
| `ue_change_set` | `create` / `get` / `save_checkpoint` / `verify_checkpoint` |
| `ue_live_write` | `plan_batch` / `apply_batch` / `recover_batch` / `verify` / `verify_fast` / `verify_checkpoint` |
| `ue_asset_property` | `set` / `set_reference` / `set_structured` / `apply` / `undo` / `discard` and related |
| `ue_set_property` | `blueprint_default` / `component_property` / `pin_default` / `material_parameter` |
| `ue_animation_scale_fix` | single-asset and batch scale audit/fix actions |
| `ue_animation_retarget` | retarget analysis and postprocess actions |
| `ue_blueprint_edit` | `move_node` / `move_nodes` / `begin_session` / `end_session` / `save` |

Arguments, defaults, Plan/Dry Run receipts, and result shapes are unchanged; a successful call echoes its `action`.

**Two — profiles and the session surface.** `--tool-profile` bounds how many tools one session exposes; `--tool-groups` names groups directly when no preset fits:

| Selection | Visible tools | Definition size |
|---|---:|---:|
| `core` (CLI default) | 15 | ≈3.0k tokens |
| `read` | 17 | |
| `live` | 48 | |
| `live-write` (`RunMcp.cmd` default) | 58 | |
| `full` | 74 | ≈14.6k tokens |
| `--tool-groups query,workflow` | 25 | combinations no preset offers |

A profile is only an **upper bound**: it never enables a group the run flags left off.
`ue_get_capabilities` reports `toolProfile` (or `custom` when a group list was given) and `toolGroups`, and the tools it lists match what `tools/list` actually registered.

## Caveats

- DP2's headline additions are still **default-off experimental capabilities**, not production defaults.
- The experimental patch bypasses the stable Write Policy allowlist; it requires exact asset/graph identity, a clean package, stopped PIE, revision/fingerprint checks, and bounded operations.
- The verified patch scope is top-level K2 event graphs; nested subgraphs are not written.
- `RunMcp.cmd` currently covers read-only and Pure Live modes only. Writes, experiments, and P4 require running `.venv\Scripts\python.exe scripts\ue-agent-mcp.py` directly; full arguments are in [Build and Run](BUILD_AND_RUN.md).

## Enabling it

```text
Stable writes: --enable-write-tools + --enable-subprocess-workflow
               + project [UEAgentKit] SubprocessWorkflow=True
               + a Write Policy covering the target
               + --tool-profile full (or name the groups with --tool-groups)

Experimental patch: --enable-experimental-graphpatch-edit
               + project [UEAgentKit] ExperimentalGraphPatchEdit=True
               + --tool-profile live-write

Registry creation: add --enable-experimental-node-spawn
               + project [UEAgentKit] ExperimentalNodeSpawn=True
```

Running `ue-agent-mcp.py` directly defaults to the `core` profile (the query group only); without a profile the write tools are not registered.

## Environment

Unreal Engine 5.6 / Windows x64 / Python 3.11–3.14. The frozen build's `ue_get_capabilities` is authoritative. DP2 is unreleased and has no installer or SHA-256 yet.

See [Blueprint experimental usage](BLUEPRINT_AGENT_E2E_USAGE_EN.md) for operations and limits, and [Capability Status](PROJECT_STATUS_EN.md) for the boundary.

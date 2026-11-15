# Blueprint experimental usage

[中文](BLUEPRINT_AGENT_E2E_USAGE.md)

This page documents **experimental** Blueprint structure writes: default off, explicitly double-gated, disposable test projects only, and not a production default. One Apply is one Editor transaction; Save is always a separate action.

## 1. Preconditions

- UE 5.6.1 Editor running the `UEAgentKitEditor` plugin with PIE/SIE stopped.
- The running project opts in through `Config/DefaultEngine.ini` (read via `GEngineIni`, not `DefaultEditor.ini`):

```ini
[UEAgentKit]
ExperimentalGraphPatchEdit=True
; only when creating nodes from the Action Registry:
ExperimentalNodeSpawn=True
```

- Start the MCP server directly with the matching flags:

```powershell
.venv\Scripts\python.exe scripts\ue-agent-mcp.py `
  --enable-live-editor `
  --enable-experimental-graphpatch-edit `
  --enable-experimental-node-spawn `
  --project "<PROJECT_ROOT>\ProjectName.uproject" `
  --database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

`RunMcp.cmd` does not expose these advanced flags. Node spawn requires `--enable-experimental-graphpatch-edit`; both flags must be present.

- The target Blueprint is saved, clean, and not in PIE; the focused graph is the same graph. The MCP `--project` must match the Editor descriptor's `projectName` / `projectPathHash`, otherwise the call fails with `live-editor-project-mismatch`.
- Enable this only on a disposable test project. Never enable the ini switch on a production project.

## 2. The seven operations

| Kind | Key fields | Constraint |
|---|---|---|
| `createNode` | `nodeType` + `x`/`y`, optional `tempId`; or `actionId`; variable accessors also take `variableName`/`variableGuid`/`variableType` | Creates one node and no edge; exactly one of `nodeType` and `actionId` |
| `connect` | `from` / `to` (node GUID or a `tmp:` label declared earlier in this patch) + `fromPin` / `toPin` | Creates one edge; never replaces an existing connection; types must match, wildcard types are derived by the schema |
| `disconnect` | The same four endpoint fields | Removes one existing edge; the target input must have that single link |
| `setPinDefault` | `nodeGuid`, `pinName`, `pinGuid`, `type`, `value` | Only unlinked bool/int/double/string inputs; containers are rejected |
| `createVariable` | `name`, `type`, `defaultValue` | Top-level member variable, bool/int/double/string; ASCII name |
| `rewirePin` | `targetNodeGuid` / `targetPinName` / `targetPinGuid` + `oldSource*` + `newSource*` (three groups) | Moves one input to a new source; never breaks an unplanned link |
| `removeNode` | `nodeGuid`, `expectedNodeClass`, `removedEdges`; each edge carries `fromNodeGuid` / `fromPinName` / `fromPinGuid` / `toNodeGuid` / `toPinName` / `toPinGuid` | One node plus at most one enumerated incident edge |

Per-patch limits:

```text
Operations        16
New nodes         16
New variables     8
Edge operations   bounded by the 16-operation total; at most 8 per node
removeNode        1 node, at most 1 incident edge
Request body      60 KiB
Coordinates       ±100000
```

## 3. The three `createNode` entry points

| Entry | Usage | Scope |
|---|---|---|
| Eight primitive `nodeType`s | `branch`, `sequence`, `not_bool`, `bool_or`, `int_add`, `int_equal`, `double_add`, `string_concat` | Fixed node class / function / pin table |
| Variable accessors | `variable_get` / `variable_set` + `variableName`/`variableGuid`/`variableType` | Existing top-level member variables, bool/int/double/string |
| Action Registry | `actionId` (`action:<32 hex>`) | `K2Node_CallFunction` and `K2Node_DynamicCast` |

Searching the registry:

```text
ue_search_node_actions(query="AddNumbers", limit=20)
ue_search_node_actions(query="Cast To Pawn", node_class="/Script/BlueprintGraph.K2Node_DynamicCast")
```

- Engine functions, project C++ `BlueprintCallable` functions, and Blueprint-defined functions are eligible.
- A Cast target class exists only in the template; searching by target class requires `node_class`.
- Spawners that are `latent`, `requiresWorldContext`, `variadic`, or require a binding/editor selection are refused; check `canCreate` / `rejectReason` before writing.
- The write side resolves an `actionId` from actions described in the current session: search first, then create.
- `Select` / `PromotableOperator` cannot be created by `createNode`; their wildcard pins can be connected, and the K2 schema derives the type during the write.

## 4. One complete call

Focus the graph and read the preflight first:

```json
{"tool": "ue_focus_blueprint_graph",
 "arguments": {"asset_path": "/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E"}}

{"tool": "ue_get_blueprint_graph_layout_preflight", "arguments": {}}
```

Use the returned `assetPath`, `graphGuid`, `packageRevision`, `graphFingerprint`, `rewireEdgeSetHash`, and `nodeRemoveEdgeSetHash`. This patch creates a Branch, sets an unlinked `Condition` default, and wires `then` to an existing node's `execute`:

```json
{"tool": "ue_preview_blueprint_patch_experimental",
 "arguments": {
   "asset_path": "/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E",
   "graph_guid": "<graphGuid>",
   "operations": [
     {"opId": "n1", "kind": "createNode", "nodeType": "branch", "x": -600, "y": 0, "tempId": "tmp:branch"},
     {"opId": "p1", "kind": "setPinDefault", "nodeGuid": "<existing node guid>",
      "pinName": "Condition", "pinGuid": "<pin guid>", "type": "bool", "value": "true"},
     {"opId": "c1", "kind": "connect", "from": "tmp:branch", "fromPin": "then",
      "to": "<existing node guid>", "toPin": "execute"}
   ]
 }}
```

Apply and Save use the confirmation strings returned by Preview and Apply; never hand-write them:

```text
ue_apply_blueprint_patch_experimental(
  plan_id=<preview.result.planId>,
  asset_path=<same>,
  graph_guid=<same>,
  operations=<same array>,
  confirmation=<preview.result.requiredConfirmation>)

ue_save_blueprint_patch_experimental(
  apply_result=<full apply response>,
  confirmation=<apply.result.requiredSaveConfirmation>)
```

The Apply result contains per-operation status. A `connect` additionally returns `resolvedPins` — the actual post-write pin names, directions, and types. Save only persists the dirty package produced by that Apply; it never compiles.

## 5. Failures and rollback

| Symptom | Meaning / action |
|---|---|
| `live-editor-capability-missing` | The running Editor does not advertise the patch capability: check `[UEAgentKit] ExperimentalGraphPatchEdit=True` and the plugin build |
| `graph-patch-experimental-disabled` | The Editor refused it: the project switch is off or this build does not enable it |
| `graph-spawn-experimental-disabled` | MCP node-spawn is off, or the project lacks `ExperimentalNodeSpawn` |
| `live-editor-project-mismatch` | MCP `--project` and the Editor descriptor differ |
| `graph-patch-package-dirty` | The target package is not clean; save it or choose another asset |
| `graph-patch-pie-active` | Stop PIE/SIE |
| `graph-patch-plan-stale` / `graph-patch-focused-graph-mismatch` | The graph or focus changed after Preview; re-read preflight and Preview again |
| Confirmation error | Run Preview again and use the newly returned confirmation; never edit it manually |
| One operation failed | Apply reports `writeOccurred=false`, `rolledBack=true`, and one result per operation; fix and Preview again |

A refused path writes nothing: node count, edge set, package dirty state, and disk SHA-256 must not change.

## 6. Concurrent editing and live reads

- **Position batches / edit sessions** may begin on a dirty package and allow the user to edit the same graph; conflict detection covers only the nodes named by the batch. Rollback restores only nodes the Agent wrote last and the user has not changed; skipped nodes are reported in `skippedNodeCount` / `skippedNodeGuids`.
- **The structural patch still requires a clean package** and does not get that concurrency relaxation.
- On reads, when `ue_get_asset` hits a session overlay record, it verifies the record against the package SHA-256 on disk and re-proves it from the Editor when the disk changed. If that fails it returns `overlayStale=true` plus `staleCause` instead of the old canonical.
- `ue_refresh_asset_index_live(asset_path=...)` explicitly refreshes one saved Blueprint. It affects only the current MCP session, never writes SQLite, and never requires a restart.

## 7. Limits

- Top-level K2 event graphs only; nested subgraphs are not written.
- No arbitrary node CRUD, automatic wiring, automatic layout, or UI drag simulation.
- CustomEvent, event/delegate binding, Timeline, and MathExpression cannot be created; `Select` / `PromotableOperator` cannot be created either.
- No variable/pin rename, retype, or bulk delete; `removeNode` deletes one node at a time.
- No auto-compile and no Save All; Save, Compile, and strong verification are separate actions.
- Experimental tools bypass the stable Write Policy allowlist; do not apply the stable-write wording to them.

## 8. Verification scope

- Action Registry creation has native acceptance for a project `UFUNCTION`, `Cast To Pawn`, the four refusal classes, and wildcard connections.
- The atomic patch has been exercised through Preview → Apply → Undo/Redo → Save → fresh-process reload for all seven operation kinds.
- These results apply only to the UE 5.6.1 isolated projects used at verification time. The capability remains experimental and is not a production default.

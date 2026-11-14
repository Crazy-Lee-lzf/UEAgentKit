# Blueprint Agent end-to-end usage (real UE 5.6 / 真实 UE 5.6 用法)

This page shows the shortest real Agent path for the bounded Blueprint write tools.
It is an opt-in experimental workflow, not a production default.
本页给出 Blueprint 有界写工具最短的真实 Agent 调用路径；它是显式 opt-in 的实验
工作流，不是生产默认能力。

## 1. Preconditions / 前置条件

- UE 5.6.1 Editor is running the `UEAgentKitEditor` plugin, PIE/SIE stopped.
- The **Editor project** opted in in `Config/DefaultEngine.ini` (the plugin reads
  this switch through `GEngineIni`, i.e. the Engine config layer of the running
  project — **not** `DefaultEditor.ini`):

  ```ini
  [UEAgentKit]
  ExperimentalGraphPatchEdit=True
  ```

- The MCP server is started with **both flags** and the three fixed write paths
  (`--engine-root`, `--policy`, `--revision-export` are required whenever
  `--enable-write-tools`/`--enable-commit-tools` are used; omitting them fails
  the startup check with a precise error):

  ```powershell
  py -3.12 -m ue_agent_kit.mcp_server `
    --enable-live-editor `
    --enable-experimental-graphpatch-edit `
    --project E:\Path\To\HostProject\HostProject.uproject `
    --database E:\Path\To\Disposable\ueak-index.sqlite3 `
    --engine-root E:\EPICGAME\UE_5.6 `
    --policy E:\Path\To\HostProject\Config\UEAgentKit\policy.json `
    --revision-export E:\Path\To\HostProject\Saved\UEAgentKit\Revision
  ```

  If the Revision Export directory does not exist yet, generate it once with
  `scripts\RunExport.ps1` (or `ue-agent index build`) before starting a write
  session; the MCP refuses writes that need a Revision without it.

- Use a disposable test project/asset. Do not enable the project switch on a
  production project. 使用 disposable 测试工程/资产，不要在生产工程打开项目开关。

The experiment is guarded three times: MCP process flags, the Editor project ini,
and a `planId` + exact confirmation for every Apply/Save. The Editor descriptor's
`projectName` + `projectPathHash` must match the MCP `--project`; a mismatch fails
with `live-editor-project-mismatch`.

## 2. One real sequence / 一次真实调用序列

Tool names below are the real MCP names. The example uses one Actor Blueprint and
the same order as the P5 acceptance run.

### Step 0 — open and focus the graph / 打开并聚焦

```json
{"tool": "ue_focus_blueprint_graph",
 "arguments": {"asset_path": "/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E"}}
```

Optionally pass `graph_guid` (canonical hyphenated GUID) to select one exact graph.
Then read the resident preflight:

```json
{"tool": "ue_get_blueprint_graph_layout_preflight", "arguments": {}}
```

Use its `assetPath`, `graphGuid`, `packageRevision`, `graphFingerprint`,
`rewireEdgeSetHash` and `nodeRemoveEdgeSetHash`. `focusSource` tells you whether
live Slate focus or the explicit focus request selected the graph.

### Step 1 — create safe nodes and connections / 建节点和连线

```json
{"tool": "ue_preview_blueprint_graph_edit_experimental",
 "arguments": {
   "assetPath": "/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E",
   "graphGuid": "<graphGuid from preflight>",
   "nodes": [
     {"tempId": "tmp:not", "nodeType": "not_bool", "x": -600, "y": 0},
     {"tempId": "tmp:or", "nodeType": "bool_or", "x": -400, "y": 120},
     {"tempId": "tmp:branch", "nodeType": "branch", "x": -200, "y": 0},
     {"tempId": "tmp:seq", "nodeType": "sequence", "x": 100, "y": 0}
   ],
   "connections": [
     {"from": "tmp:not", "fromPin": "ReturnValue", "to": "tmp:branch", "toPin": "Condition"},
     {"from": "tmp:branch", "fromPin": "then", "to": "tmp:seq", "toPin": "execute"}
   ]
 }}
```

Every `tempId` must match `^tmp:[A-Za-z0-9_-]{1,36}$` (the `tmp:` prefix is
required and connections reference the same `tempId`). `nodeType` must be one of
the exact supported names (`not_bool`, `bool_or`, `branch`, `sequence`,
`int_add`, `double_add`, `int_equal`, `string_concat`, `variable_get`,
`variable_set`, `call_function`, `call_owned_pure_function`) — `nodeClass` is
derived by the plugin and is not a request field.

Apply and Save with the **returned** confirmation strings; never invent them:

```text
ue_apply_blueprint_graph_edit_experimental(preview=<full preview result>,
  confirmation=<preview.requiredConfirmation>)
ue_save_blueprint_graph_edit_experimental(apply_result=<full apply result>,
  confirmation=<apply.requiredSaveConfirmation>)
```

### Step 2 — structural writes go through the atomic patch / 结构写入统一走 atomic patch

The standalone pin-default / variable-create / rewire / node-remove preview,
apply, save and one-click tools were removed in slice 16. Every kind they owned
is a `kind` of the cross-writer patch, in one transaction with per-operation
results, rollback and Undo/Redo verification:

```json
{"tool": "ue_preview_blueprint_patch_experimental",
 "arguments": {
   "asset_path": "/Game/.../BP_AgentE2E.BP_AgentE2E",
   "graph_guid": "<graphGuid>",
   "operations": [
     {"opId": "p1", "kind": "setPinDefault", "nodeGuid": "<guid>", "pinName": "Condition",
      "pinGuid": "<pin guid>", "type": "bool", "value": "true"},
     {"opId": "v1", "kind": "createVariable", "name": "AgentCount", "type": "int", "defaultValue": "7"},
     {"opId": "r1", "kind": "rewirePin", "targetNodeGuid": "<guid>", "targetPinName": "Condition",
      "targetPinGuid": "<pin guid>", "oldSourceNodeGuid": "<guid>", "oldSourcePinName": "ReturnValue",
      "oldSourcePinGuid": "<pin guid>", "newSourceNodeGuid": "<guid>", "newSourcePinName": "ReturnValue",
      "newSourcePinGuid": "<pin guid>"},
     {"opId": "n1", "kind": "removeNode", "nodeGuid": "<isolated node guid>",
      "expectedNodeClass": "/Script/BlueprintGraph.K2Node_Not_PreBool", "removedEdges": []}
   ]
 }}
```

Then apply and save with the returned confirmations:

```text
ue_apply_blueprint_patch_experimental(plan_id, asset_path, graph_guid, operations,
  confirmation=<preview.requiredConfirmation>)
ue_save_blueprint_patch_experimental(apply_result=<full apply result>,
  confirmation=<apply.requiredSaveConfirmation>)
```

The patch is one transaction and never saves implicitly; a failed operation
restores the ones already applied (`writeOccurred=false`, one result per
operation). Read-only rules are unchanged: pin defaults only on existing,
unlinked, non-container bool/int/double/string inputs; one node removal with at
most one enumerated incident edge; a rewire never breaks an unplanned link.

### Step 3 — verify in a new process / 新进程只读校验

Run a fresh Editor commandlet (the P5 script does this automatically):

```powershell
pwsh scripts/VerifyBlueprintAgentEndToEnd.ps1 `
  -EngineRoot <UE_5.6> -SkipBuild -Output <AUDIT_OUTPUT>
```

A passing run checks the disk SHA-256, graph GUID, exact node/edge sets, all four
pin defaults, the four variables' effective defaults, the deleted node's absence,
and an empty `mismatches` list in `reload.json`.

## 3. Failure handling / 失败处理

| Symptom | Meaning / action |
|---|---|
| `graph-edit-experimental-disabled` | The running Editor project lacks the ini switch; do not retry writes |
| `live-editor-project-mismatch` | MCP `--project` and the Editor descriptor differ; fix the project path |
| `graph-layout-graph-not-focused` | Call `ue_focus_blueprint_graph` and check preflight `focusSource` |
| `...-dirty-package` / PIE error | Save or stop PIE first; writes require a clean, non-PIE package |
| confirmation error | Re-run Preview and pass the newly returned confirmation; never edit it manually |
| `graph-...-edge-set-mismatch` | Re-read preflight; the graph changed after Preview |

## 4. Limits / 限制

- Only top-level K2 event graphs; no nested subgraph writes.
- Five bounded families only; no pin/variable rename, retype, bulk delete, or
  arbitrary live GraphPatch.
- Experimental tools are dynamically registered with
  `--enable-experimental-graphpatch-edit`, so they do not appear in the static
  offline tool registry.
- The live-only MCP server currently still requires a usable index database; use a
  disposable one for tests.
- Every Save is explicit; nothing auto-saves or compiles.

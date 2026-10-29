# Blueprint Agent end-to-end usage (real UE 5.6 / 真实 UE 5.6 用法)

This page shows the shortest real Agent path for the bounded Blueprint write tools.
It is an opt-in experimental workflow, not a production default.
本页给出 Blueprint 有界写工具最短的真实 Agent 调用路径；它是显式 opt-in 的实验
工作流，不是生产默认能力。

## 1. Preconditions / 前置条件

- UE 5.6.1 Editor is running the `UEAgentKitEditor` plugin, PIE/SIE stopped.
- The **Editor project** opted in in `Config/DefaultEditor.ini` (or the running
  project config):

  ```ini
  [UEAgentKit]
  ExperimentalGraphPatchEdit=True
  ```

- The MCP server is started with **both** flags:

  ```powershell
  py -3.12 -m ue_agent_kit.mcp_server `
    --enable-live-editor `
    --enable-experimental-graphpatch-edit `
    --project E:\Path\To\HostProject\HostProject.uproject `
    --database E:\Path\To\Disposable\ueak-index.sqlite3
  ```

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
     {"tempId": "not", "nodeType": "not_bool", "x": -600, "y": 0},
     {"tempId": "or", "nodeType": "bool_or", "x": -400, "y": 120},
     {"tempId": "branch", "nodeType": "branch", "x": -200, "y": 0},
     {"tempId": "seq", "nodeType": "sequence", "x": 100, "y": 0}
   ],
   "connections": [
     {"from": "not", "fromPin": "ReturnValue", "to": "branch", "toPin": "Condition"},
     {"from": "branch", "fromPin": "then", "to": "seq", "toPin": "execute"}
   ]
 }}
```

Apply and Save with the **returned** confirmation strings; never invent them:

```text
ue_apply_blueprint_graph_edit_experimental(preview=<full preview result>,
  confirmation=<preview.requiredConfirmation>)
ue_save_blueprint_graph_edit_experimental(apply_result=<full apply result>,
  confirmation=<apply.requiredSaveConfirmation>)
```

### Step 2 — edit unlinked pin defaults / 修改未连线 Pin 默认值

Use snake_case parameters and one exact node/pin GUID per target:

```json
{"tool": "ue_preview_blueprint_pin_defaults_experimental",
 "arguments": {
   "asset_path": "/Game/.../BP_AgentE2E.BP_AgentE2E",
   "graph_guid": "<graphGuid>",
   "defaults": [
     {"nodeGuid": "<branch2 guid>", "pinName": "Condition", "pinGuid": "<pin guid>", "type": "bool", "value": "true"},
     {"nodeGuid": "<int_add guid>", "pinName": "A", "pinGuid": "<pin guid>", "type": "int", "value": "7"},
     {"nodeGuid": "<double_add guid>", "pinName": "A", "pinGuid": "<pin guid>", "type": "double", "value": "1.5"},
     {"nodeGuid": "<string_concat guid>", "pinName": "A", "pinGuid": "<pin guid>", "type": "string", "value": "agent"}
   ]
 }}
```

Then call `ue_apply_blueprint_pin_defaults_experimental` and
`ue_save_blueprint_pin_defaults_experimental` with their returned confirmations.
Only existing, unlinked, non-container bool/int/double/string inputs are accepted.

### Step 3 — create top-level member variables / 创建成员变量

```json
{"tool": "ue_preview_blueprint_variable_create_experimental",
 "arguments": {
   "asset_path": "/Game/.../BP_AgentE2E.BP_AgentE2E",
   "variables": [
     {"name": "AgentFlag", "type": "bool", "defaultValue": "true"},
     {"name": "AgentCount", "type": "int", "defaultValue": "7"},
     {"name": "AgentScale", "type": "double", "defaultValue": "1.5"},
     {"name": "AgentLabel", "type": "string", "defaultValue": "agent"}
   ]
 }}
```

Apply/Save: `ue_apply_blueprint_variable_create_experimental` →
`ue_save_blueprint_variable_create_experimental`. This tool intentionally does not
compile; the pending defaults are materialized by UE's later load compile (the P5
reload proves the effective generated-class CDO values).

### Step 4 — precise rewire / 精确改接一条边

```json
{"tool": "ue_preview_blueprint_rewire_experimental",
 "arguments": {
   "asset_path": "/Game/.../BP_AgentE2E.BP_AgentE2E",
   "graph_guid": "<graphGuid>",
   "operation": "rewire",
   "target_node_guid": "<branch guid>",
   "target_pin_name": "Condition",
   "target_pin_guid": "<pin guid>",
   "old_source_node_guid": "<not guid>",
   "old_source_pin_name": "ReturnValue",
   "old_source_pin_guid": "<pin guid>",
   "expected_edge_set_hash": "<preflight.rewireEdgeSetHash>",
   "new_source_node_guid": "<or guid>",
   "new_source_pin_name": "ReturnValue",
   "new_source_pin_guid": "<pin guid>"
 }
}
```

Apply/Save: `ue_apply_blueprint_rewire_experimental` →
`ue_save_blueprint_rewire_experimental`. The tool breaks only the one planned edge
and rejects any schema response that would break other links.

### Step 5 — controlled node removal / 受控删除节点

```json
{"tool": "ue_preview_blueprint_node_remove_experimental",
 "arguments": {
   "asset_path": "/Game/.../BP_AgentE2E.BP_AgentE2E",
   "graph_guid": "<graphGuid>",
   "node_guid": "<isolated not guid>",
   "expected_node_class": "/Script/BlueprintGraph.K2Node_Not_PreBool",
   "expected_edge_set_hash": "<preflight.nodeRemoveEdgeSetHash>",
   "removed_edges": []
 }
}
```

Apply/Save: `ue_apply_blueprint_node_remove_experimental` →
`ue_save_blueprint_node_remove_experimental`. Start with an isolated allowlisted
pure-function or branch node; a plan removes one node and at most one enumerated
incident edge.

### Step 6 — verify in a new process / 新进程只读校验

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

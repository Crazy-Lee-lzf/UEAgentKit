# Blueprint 实验用法

[English](BLUEPRINT_AGENT_E2E_USAGE_EN.md)

本页是 Blueprint 结构写入的**实验用法**：默认关闭、需要显式双门禁、只适用于隔离测试工程，不是生产默认能力。一次 Apply 是一个 Editor 事务；Save 永远是独立动作。

## 1. 前置条件

- UE 5.6.1 Editor 正在运行 `UEAgentKitEditor` 插件，PIE/SIE 已停止。
- 运行中的项目在 `Config/DefaultEngine.ini` 中显式开启（插件通过 `GEngineIni` 读取，不是 `DefaultEditor.ini`）：

```ini
[UEAgentKit]
ExperimentalGraphPatchEdit=True
; 只有使用 Action Registry 创建节点时才需要：
ExperimentalNodeSpawn=True
```

- MCP 直接启动，并带上对应参数：

```powershell
.venv\Scripts\python.exe scripts\ue-agent-mcp.py `
  --enable-live-editor `
  --enable-experimental-graphpatch-edit `
  --enable-experimental-node-spawn `
  --project "<PROJECT_ROOT>\ProjectName.uproject" `
  --database "<TOOL_ROOT>\.data\ue_agent_kit.sqlite3"
```

`RunMcp.cmd` 不支持这些高级开关。若使用 node spawn，`--enable-experimental-node-spawn` 依赖 `--enable-experimental-graphpatch-edit`，两者都要开。

- 目标 Blueprint 已保存、干净、非 PIE；编辑器里聚焦的是同一张图。MCP `--project` 必须与 Editor descriptor 的 `projectName` / `projectPathHash` 一致，否则返回 `live-editor-project-mismatch`。
- 只对可丢弃的测试工程开启。不要在生产工程打开 ini 开关。

## 2. 七种操作

| kind | 关键字段 | 约束 |
|---|---|---|
| `createNode` | `nodeType` + `x`/`y`，可选 `tempId`；或 `actionId`；变量 accessor 另带 `variableName`/`variableGuid`/`variableType` | 创建 1 个节点，不建边；`nodeType` 与 `actionId` 二选一 |
| `connect` | `from` / `to`（节点 GUID 或本 patch 更早声明的 `tmp:` 标签）+ `fromPin` / `toPin` | 建 1 条边；不替换已有连接；类型必须匹配，wildcard 由 schema 推导 |
| `disconnect` | 与 `connect` 相同的四个端点字段 | 删 1 条已有边；目标输入必须只有这一条连接 |
| `setPinDefault` | `nodeGuid`、`pinName`、`pinGuid`、`type`、`value` | 只对未连线的 bool/int/double/string 输入；不接受容器 |
| `createVariable` | `name`、`type`、`defaultValue` | 顶层成员变量，bool/int/double/string；名字为 ASCII |
| `rewirePin` | `targetNodeGuid` / `targetPinName` / `targetPinGuid` + `oldSource*` + `newSource*`（三组） | 把一个输入从旧源移到新源；不破坏计划外的连接 |
| `removeNode` | `nodeGuid`、`expectedNodeClass`、`removedEdges`；每条 edge 含 `fromNodeGuid` / `fromPinName` / `fromPinGuid` / `toNodeGuid` / `toPinName` / `toPinGuid` | 1 个节点 + 最多 1 条枚举的 incident edge |

一次 patch 的上限：

```text
操作总数        16
新建节点        16
新建变量        8
边操作          受总操作数 16 限制；单节点最多 8 条
removeNode      1 个节点，最多 1 条 incident edge
请求体          60 KiB
坐标            ±100000
```

## 3. `createNode` 的三个入口

| 入口 | 用法 | 范围 |
|---|---|---|
| 8 个 primitive `nodeType` | `branch`、`sequence`、`not_bool`、`bool_or`、`int_add`、`int_equal`、`double_add`、`string_concat` | 固定节点类 / 函数 / Pin 表 |
| 变量 accessor | `variable_get` / `variable_set` + `variableName`/`variableGuid`/`variableType` | 已存在的顶层成员变量，bool/int/double/string |
| Action Registry | `actionId`（`action:<32 hex>`） | `K2Node_CallFunction` 与 `K2Node_DynamicCast` |

Action Registry 的搜索与限制：

```text
ue_search_node_actions(query="AddNumbers", limit=20)
ue_search_node_actions(query="Cast To Pawn", node_class="/Script/BlueprintGraph.K2Node_DynamicCast")
```

- 可调用引擎函数、项目 C++ `BlueprintCallable` 和蓝图自建函数。
- Cast 的目标类只在模板里；按目标类搜索时必须同时给 `node_class`。
- `latent` / `requiresWorldContext` / `variadic` / 需要 binding 或编辑器选中的 spawner 会被拒绝；搜索里先看 `canCreate` / `rejectReason`。
- 写侧按本次会话已描述过的 action 解析 `actionId`：正常流程是先搜索、再创建。
- `Select` / `PromotableOperator` 不能由 `createNode` 创建；它们的 wildcard 端点可以在 `connect` 中使用，类型由 K2 schema 在写入时推导。

## 4. 一次完整调用

先聚焦图并读 preflight：

```json
{"tool": "ue_focus_blueprint_graph",
 "arguments": {"asset_path": "/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E"}}

{"tool": "ue_get_blueprint_graph_layout_preflight", "arguments": {}}
```

用 preflight 返回的 `assetPath`、`graphGuid`、`packageRevision`、`graphFingerprint`、`rewireEdgeSetHash`、`nodeRemoveEdgeSetHash` 组装 patch。下面创建一个 Branch、给它未连线的 `Condition` 设默认值，并把 `then` 接到一个已存在节点的 `execute`：

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

Apply 与 Save 都使用 Preview / Apply 返回的确认字符串，不要手写：

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

Apply 结果是逐操作状态；`connect` 额外返回 `resolvedPins`（写入后的实际 pin 名/方向/类型）。`Save` 只保存 Apply 产生的那个 Dirty Package，不编译。

## 5. 失败与回滚

| 现象 | 含义 / 处理 |
|---|---|
| `live-editor-capability-missing` | 运行中的 Editor 没有广播 patch 能力：检查 `[UEAgentKit] ExperimentalGraphPatchEdit=True` 与插件版本 |
| `graph-patch-experimental-disabled` | Editor 侧拒绝：项目开关未开或当前构建未启用 |
| `graph-spawn-experimental-disabled` | MCP 没开 node-spawn，或项目没开 `ExperimentalNodeSpawn` |
| `live-editor-project-mismatch` | MCP `--project` 与 Editor descriptor 不一致 |
| `graph-patch-package-dirty` | 目标 Package 不干净；先保存或换资产 |
| `graph-patch-pie-active` | PIE/SIE 未停止 |
| `graph-patch-plan-stale` / `graph-patch-focused-graph-mismatch` | 图或焦点在 Preview 后变化；重新读 preflight 并重新 Preview |
| confirmation 错误 | 重新 Preview，使用新返回的确认值；不要手改 |
| 单操作失败 | Apply 返回 `writeOccurred=false`、`rolledBack=true` 和逐操作结果；修正后重新 Preview |

拒绝路径不写入任何东西：节点数、边集、Package Dirty 和磁盘 SHA-256 都不应变化。

## 6. 并发编辑与实时读取

- **位置批量移动 / edit session** 可以在 Dirty Package 上开始，并允许用户同时修改同一张图；冲突检测只针对本批命名的节点。rollback 只还原 Agent 最后写入且用户未改的节点，跳过的节点会在 `skippedNodeCount` / `skippedNodeGuids` 中报告。
- **结构 patch 仍要求干净 Package**，不享受上述并发放宽。
- 读取侧：`ue_get_asset` 命中会话 overlay 时，会先用磁盘 Package SHA-256 校验记录；磁盘变化会自动向 Editor 重新取证。失败时返回 `overlayStale=true` + `staleCause`，不会返回旧 canonical。
- `ue_refresh_asset_index_live(asset_path=...)` 可显式刷新一个已保存 Blueprint；它只影响当前 MCP 会话，不写 SQLite、不要求重启。

## 7. 限制

- 只验证顶层 K2 事件图；不写嵌套子图。
- 不做任意节点 CRUD、自动布线、自动布局或 UI 拖拽模拟。
- 不能创建 CustomEvent、事件/委托绑定、Timeline、MathExpression，也不能创建 `Select` / `PromotableOperator` 本身。
- 不支持变量/ Pin 重命名、retype、批量删除；`removeNode` 一次只删一个节点。
- 不自动编译，不自动 Save All；Save、Compile、强验证都是独立动作。
- 实验工具不经过稳定 Write Policy 白名单，不能把稳定写入的限制说明套用到它们身上。

## 8. 验证范围

- Action Registry 创建节点：项目 `UFUNCTION`、`Cast To Pawn`、四类拒绝、wildcard 连接均有原生验收。
- 原子 patch：七种操作在同一真实 Editor 事务中通过 Preview → Apply → Undo/Redo → Save → 新进程 Reload 验证。
- 这些结果只对应验证时的 UE 5.6.1 隔离工程；仍属实验能力，不代表生产默认可用。

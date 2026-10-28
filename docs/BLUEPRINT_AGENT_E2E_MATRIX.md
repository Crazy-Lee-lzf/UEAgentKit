# Blueprint Agent 端到端真实 UE 5.6 读写验收矩阵（P5）

状态：**P5 A 级验收完成**（隔离测试环境）。五类写能力已通过真实
MCP stdio → EditorBridge Socket RPC → UE Editor 原生 Apply → 显式 Save →
新进程 Reload 的完整链路验证；门禁 OFF 负例、跨工程拒绝负例同时通过。

验证环境：UE 5.6.1 + disposable HostProject。本文只描述该隔离测试环境中的
一次真实 MCP 运行，不代表生产默认启用，也不构成发布 GO/NO-GO。

## 0. 结论摘要

- 真实链路可用：GraphEdit 8 类节点 / Pin Default 4 类型 / 用户变量 4 类型 /
  精确 rewire / 受控 node delete 均在同一 Editor 进程内完成
  Preview→Apply→Save，并在**新 UE 进程**中通过只读 Reload 校验。
- 每个 Apply 都返回 `undoVerified=true`、`redoVerified=true`，Save 返回
  `saved=true`、`packageDirty=false`，且磁盘 SHA-256 随每一步变化。
- 门禁 OFF 时正向调用被 `graph-edit-experimental-disabled` 拒绝；使用另一个
  工程路径 + 复制的 descriptor 启动 MCP 时被
  `live-editor-project-mismatch` 拒绝，证明写请求不会跨工程误连。
- 仍然**不是生产默认**：需要 `--enable-experimental-graphpatch-edit` 与工程
  `[UEAgentKit] ExperimentalGraphPatchEdit=True` 双层 opt-in；没有 OwnerTrust
  服务，边界是进程 flag + 工程 ini + 精确 planId/confirmation + descriptor 身份。
- 仍存在产品缺口：live-only MCP 启动仍要求一个可用的 index DB；实验工具不在静态
  `tool_registry`；nested/非顶层图不支持；Save receipt 仅限当前 Editor 进程。

## 1. 真实产品链路

```
MCP client (stdio JSON-RPC)
  → ue_agent_kit.mcp_server (FastMCP，实验工具按 flag 动态注册)
  → LiveEditorBridgeService.call_method(method, params)
  → TCP 127.0.0.1:<descriptor.port>，hello(authToken, projectPathHash, serverVersion)
  → FUEAgentKitEditorBridge::ProcessRequest / dispatch
  → TryPreview*/TryApply*/TrySave*（原生 plan / transaction / readback）
  → 真实 UBlueprint / UEdGraph / UEdGraphPin / 磁盘 .uasset
```

授权与安全边界是三层叠加，而不是 OwnerTrust 服务：

1. 进程级：MCP `--enable-live-editor`；写类还需
   `--enable-experimental-graphpatch-edit`（不开启时实验工具根本不注册）。
2. 项目级：Editor 进程内 `GConfig [UEAgentKit] ExperimentalGraphPatchEdit=True`
   （layout apply 使用独立键 `ExperimentalGraphLayoutApply`）。
3. 调用级：Preview 产出 `planId` + `requiredConfirmation`；Apply/Save 必须回传
   完整 preview/apply 结果与精确确认串。descriptor 的 `projectName` +
   `projectPathHash` 阻止跨工程误连；`authToken` 只写在工程
   `Saved/UEAgentKit/EditorBridge.json`。

本仓库没有名为 OwnerTrust 的服务；`mcp_server.py` 只声明“不声称 OwnerTrust/T10
生产启用”。P5 把上述三层视为当前真实的授权边界，不额外发明信任服务。

## 2. 证据等级

| 等级 | 含义 |
|---|---|
| A | 真实 Agent 路径：MCP stdio → Socket → Editor 原生 Apply → 显式 Save → 新进程 Reload |
| B | 真实 Editor，但绕过 MCP/Socket，直接调用 C++ `Try*`（P1-P4 fixture 测试） |
| C | Python/MCP 假服务单测、JSON contract 测试 |
| D | 只有源码存在，无自动化证据 |

P1-P4 原有 native 证据为 B；P5 在独立的 disposable HostProject 中把它们提升为 A。
P6-P8 layout 有真实 Socket 探针，但不是本轮五类结构写的 MCP 编排证据。

## 3. A 级实测结果（本次验收）

测试工程：一个独立的 disposable HostProject（UE 5.6.1）。
测试资产：`/Game/UEAgentKitAgentE2E/BP_AgentE2E.BP_AgentE2E`（普通 Actor
Blueprint，初始 3 个默认节点、0 条边）。所有写步骤都由
`tests/python/p5_agent_e2e_probe.py` 通过 MCP stdio 发起，经真实 Socket 到达
Editor；每步严格 Preview→Apply→Save，Save 紧随 Apply。

### 3.1 五类写

| 步骤 | MCP 工具族 | 结果 | planId | Undo/Redo | Save 后磁盘 SHA-256 |
|---|---|---|---|---|---|
| 建 8 节点 + 2 连线 | `ue_*_blueprint_graph_edit_experimental` | applied，11 节点 / 2 边 | `sha256:cda66101…55411f4` | true / true | `sha256:43ce2678…c8e65139` |
| Pin 默认值 4 类型 | `ue_*_blueprint_pin_defaults_experimental` | applied，4 个未连线输入 | `sha256:b3e32c3d…d86b7968` | true / true | `sha256:5123c820…8465caa8` |
| 用户变量 4 类型 | `ue_*_blueprint_variable_create_experimental` | 4 个变量创建 | `sha256:72df29f6…57a93e45` | true / true | `sha256:a90e19d1…b76a202f5` |
| 精确 rewire | `ue_*_blueprint_rewire_experimental` | `branch.Condition` 改接 `bool_or.ReturnValue` | `sha256:6fe539dd…47eec093` | true / true | `sha256:1e184b5c…33c779ea` |
| 受控删除孤立节点 | `ue_*_blueprint_node_remove_experimental` | 删除 `Not_PreBool`，0 条 incident edge | `sha256:ec5ec9f9…976fbbf8` | true / true | `sha256:632a014d…21d8f543` |

每一步 Apply 均为 `applied=true, undoVerified=true, redoVerified=true,
saved=false, compiled=false`；Save 为 `saved=true, packageDirty=false,
compiled=false`。完整 planId、每步 SHA、节点 GUID/class、变量 GUID/类型/默认值
完整明细保留在内部运行归档中（不进入公开仓库）。

### 3.2 最终 resident 状态

- 节点：10（初始 3 + 创建 8 − 删除 1）；边：2。
- 变量：`AgentFlag:bool=true`、`AgentCount:int=7`、`AgentScale:double=1.5`、
  `AgentLabel:string=agent`。
- 删除节点 GUID 不存在；未参与节点与边的 GUID 未改变。
- 资产 SHA-256：`sha256:632a014d14db43c3a9626662ba1500830389939b351d203b7e1c1d6421d8f543`。

### 3.3 新进程 Reload（只读）

`-run=BlueprintAgentE2EReload` 在**新的** `UnrealEditor-Cmd` 进程（processId
32600）中重新加载资产，结果：

- `verified=true`、`readOnly=true`、`assetSha256Verified=true`；
- 节点 10、边 2、变量 4、已删除节点 1（不存在于图中）；
- 4 个变量的有效默认值全部由 generated-class CDO 证明为
  `true / 7 / 1.5 / agent`；
- `mismatches=[]`；图的 2 条边与 Apply 后 edge-set 完全一致；
- 证据：`Build/DirectHost/Saved/UEAgentKitAgentE2E/reload.json`。

### 3.4 负例

| 负例 | 真实结果 |
|---|---|
| Editor 工程未设置 `ExperimentalGraphPatchEdit=True` | `graph-edit-experimental-disabled`，Editor 未发生写 |
| MCP 使用另一工程 `.uproject` + 复制的 descriptor | `live-editor-project-mismatch`，请求未到达目标 Editor |
| MCP 未开启 `--enable-experimental-graphpatch-edit` | 实验写工具不注册（静态 registry 不可见） |
| Plan/confirmation/edge-set hash 不匹配 | 各 `TryApply*` 在 mutation 前 fail closed（P1-P4 native + C 级单测） |

## 4. P5 修复的真实产品链路缺口

1. **MCP 级“打开并聚焦指定图”缺失**
   - 新增 bridge `editor.focusBlueprintGraph` 与 opt-in MCP 工具
     `ue_focus_blueprint_graph`（仅在 `--enable-experimental-graphpatch-edit` 时注册）。
   - 不指定 `graph_guid` 时聚焦第一个顶层 Event Graph；指定时必须精确匹配。
   - Slate 焦点在后台/无头 Editor 中可能在两次 RPC 之间丢失，因此 preflight 增加
     `focusSource=live-slate-focus|explicit-focus-request`：只有当显式选中的图
     属于同一 Blueprint 时才作为回退，且写入仍然校验 assetPath + graphGuid。
2. **Preflight 不暴露 rewire/node-remove 所需 edge-set hash**
   - `editor.getBlueprintGraphLayoutPreflight` 新增只读字段
     `rewireEdgeSetHash`、`nodeRemoveEdgeSetHash`，Agent 可从真实 resident
     状态构造合法 plan，而不是依赖 native fixture 内部值。
3. **GraphEdit 后 Pin Default 事务被丢弃**
   - 根因：GraphEdit 创建/Undo-Redo 重建的节点不保证带 `RF_Transactional`；
     `Node->Modify()` 变成 no-op，PinDefault 的 `FScopedTransaction` 没有记录任何
     diff，End 时被当作空事务丢弃，随后 `UndoTransaction()` 误撤销上一条 GraphEdit。
   - 修复：GraphEdit 创建节点后设置 `RF_Transactional`；PinDefault 对每个目标
     `Node` 设置 flag 并 `Modify()`，再写 pin。
4. **Rewire/NodeRemove Save 返回 hash 前缀损坏**
   - 根因：`const FReceipt* Receipt = TMap.Find(PlanId);` 后先
     `TMap.Remove(PlanId)` 再读 `Receipt`，是 use-after-free，字符串前缀被覆写。
   - 修复：先构造完所有 result 字段，最后才移除 receipt（Rewire、NodeRemove）。
5. **GraphEdit / PinDefault 的 Apply 结果缺少 Save 确认串字段**
   - Variable/Rewire/NodeRemove 的 Apply 返回 `requiredSaveConfirmation`，
     GraphEdit/PinDefault 原本只返回 `confirmation`，Agent 必须猜 Save 串。
   - 修复：两族 Apply 结果补齐 `requiredSaveConfirmation`，与其余三族一致；
     探针现在要求该字段存在且前缀精确匹配。
6. **缺少可重复的真实 MCP E2E 编排**
   - `scripts/VerifyBlueprintAgentEndToEnd.ps1`：构建 → 清理/生成 fixture →
     disposable 空索引 → 门禁 OFF 负例 → 开启项目门禁 → gate-ON Editor →
     全序列 MCP 探针 → 跨工程负例 → 杀 Editor → 新进程 Reload → 汇总。
   - `scripts/New-DisposableAgentIndex.py`：创建 schema 当前的空 DB，避免触碰
     3.4GB 生产索引。
   - `BlueprintAgentE2EReloadCommandlet`：只读 fresh-process 校验器。

## 5. 工具矩阵（本轮实测）

### 5.1 只读 / 定位

| 能力 | MCP 工具 | Bridge 方法 | 参数契约 | P5 证据 |
|---|---|---|---|---|
| Editor 状态/capabilities | `ue_editor_status`、`ue_get_capabilities` | `editor.status` | 无 | A（探针启动时读取 66 个工具） |
| 打开并聚焦指定图 | `ue_focus_blueprint_graph` | `editor.focusBlueprintGraph` | `asset_path`、可选 `graph_guid` | A（每步前调用） |
| 结构 preflight | `ue_get_blueprint_graph_layout_preflight` | `editor.getBlueprintGraphLayoutPreflight` | 空对象；返回 focused asset/graph、revision、fingerprint、`focusSource`、两个 edge-set hash | A |
| 聚焦图 full canonical context | `ue_get_focused_blueprint_graph_context` | `editor.getFocusedBlueprintGraphContext` | 空对象；nodes/pins/links/defaults/variables | A |
| Graph summary/layout projection | `ue_get_blueprint_graph_selection` 等 | `editor.getBlueprintGraphSelection` | — | C/D（非本轮重点） |

只读索引（`ue_get_asset` / `ue_search_assets`）与 UE resident 状态是两套事实：
索引是冷库，`freshness` 可能是 `unknown`/`stale`；Editor 内 canonical context
才是 resident 最新态。P5 的写验收只使用 resident context + 磁盘 SHA，不把离线
Canonical JSON 当 UE 写入依据。

### 5.2 结构写（全部实验、默认 OFF）

| 能力 | MCP 工具（Preview / Apply / Save） | Bridge 方法 | 请求要点 | 原生 Apply 约束 | P5 证据 |
|---|---|---|---|---|---|
| 建节点+连线（8 类） | `ue_preview/apply/save_blueprint_graph_edit_experimental` | `editor.*BlueprintGraphEditExperimental` | `assetPath`、`graphGuid`、`nodes[{tempId,nodeType,x,y}]`、`connections`；≤16 节点/24 边 | focused 顶层 K2 图、干净包、非 PIE；一事务；Undo/Redo；Save 需 Apply receipt | **A** |
| Pin 默认值（bool/int/double/string） | `ue_preview/apply/save_blueprint_pin_defaults_experimental` | `editor.*BlueprintPinDefaultsExperimental` | `defaults[{nodeGuid,pinName,pinGuid,type,value}]` | 仅未连线输入 pin；规范值编码；Undo/Redo | **A** |
| 用户变量创建（4 类） | `ue_preview/apply/save_blueprint_variable_create_experimental` | `editor.*BlueprintVariableCreateExperimental` | `variables[{name,type,defaultValue}]`、≤8 | 顶层成员变量；不编译；默认值以 pending 字符串落盘 | **A** |
| 精确 rewire/disconnect | `ue_preview/apply/save_blueprint_rewire_experimental` | `editor.*BlueprintRewireExperimental` | 目标 node/pin/GUID + old source node/pin/GUID + edge-set hash（rewire 还要 new source） | 仅一条边；拒绝 BreakOthers/转换；Undo/Redo | **A（rewire）** |
| 受控 node delete | `ue_preview/apply/save_blueprint_node_remove_experimental` | `editor.*BlueprintNodeRemoveExperimental` | node GUID+class、edge-set hash、`removedEdges`（≤1） | 白名单纯函数/Branch；删除前必须零链接；Undo 恢复节点+连线 | **A（0 边孤立节点）** |
| 变量 Get/Set 访问器（bool/int/double/string + FunctionGraph） | `ue_preview/apply/save_blueprint_graph_edit_experimental`（扩展节点类型） | `editor.*BlueprintGraphEditExperimental` | `variableName/variableGuid/variableType`；同一 plan 内精确连线 | 现有顶层成员变量；GUID+name+type 绑定；精确 pin；Undo/Redo；Save variableBindingHash | **A（正向 MCP）**；PIE/同类型 retarget 为 **B（native automation）** |
| 新节点连接到已存在精确 Pin | `ue_preview/apply/save_blueprint_graph_edit_experimental`（扩展 connection 端点） | `editor.*BlueprintGraphEditExperimental` | `fromExisting`/`toExisting`：nodeGuid+pinGuid+pinName+direction+category/subCategory；另一端点为本 plan 新节点 | 仅一个 existing 端点；existing input 未连线；existing output fan-out 保留；拒绝 BreakOthers/转换/提升；before/after edge-set hash | **A（正向 MCP + Reload）**；PIE/Preview 后 stale 为 **B（native automation）** |
| 已有节点 → 已有未连线输入（`nodes=[]` 单边） | `ue_preview/apply/save_blueprint_graph_edit_experimental`（纯 existing connection） | `editor.*BlueprintGraphEditExperimental` | `fromExisting`+`toExisting` 两端 exact GUID/name/direction/type；`nodes=[]`；仅一条连接 | 同图不同节点；target input 未连线；source fan-out 保留；节点数不变；before/after edge-set hash；拒绝已有边替换 | **A（正向 MCP + Reload）**；zero-node stale/PIE 为 **B（native automation）** |
| 精确断开一条已有边（`operation=disconnect`，`nodes=[]`） | `ue_preview/apply/save_blueprint_graph_edit_experimental`（disconnect 模式） | `editor.*BlueprintGraphEditExperimental` | 两端 exact GUID/name/direction/type；目标 input 当前恰好只有这一条 link；仅一条连接、零新节点 | 只 `BreakLinkTo` 这一对；edge-set 删除=1/新增=0；其它边与节点保留；before/after hash；Undo/Redo/Save receipt；拒绝不存在/歧义边 | **A（正向 MCP + Reload）**；disconnect 后 stale/PIE 为 **B（native automation）** |
| Graph layout apply | `ue_preview_blueprint_graph_layout` + `ue_apply/save`（若开启） | `editor.*BlueprintGraphLayoutApply*` | 位置计划 hash | 只改位置 | P6-P8 Socket 探针（部分 A/B），非本轮回归 |

> 后验补充（不改变 P5 原始五类结论）：2026-10-28 已完成 `variable_get` /
> `variable_set` 的真实 MCP → Socket → Editor Preview/Apply/Save → 新进程
> canonical export Reload 验收（4 类型 + FunctionGraph，10 个 accessor，
> `undoVerified=true`、`redoVerified=true`、`verified=true`）。PIE 拒绝与
> 同类型 retarget Save 拒绝由 native automation 取得 B 级证据。该补充不计入
> P5 原表的 8 类节点计数，但可以作为独立 A/B 证据引用。
>
> 2026-10-28 追加：新节点连接已存在精确 Pin 已取得 A 级真实 MCP 证据（现有
> Branch `execute`/`Condition` + 现有 event `then`，4 条边，Undo/Redo、
> Save、fresh-process Reload、`mismatches=[]`）。PIE 与 Preview 后 stale
> 为 native B。该能力仍只允许“新节点 ↔ 单个 existing pin”。
>
> 同日追加：`nodes=[]` 的 **existing output → existing unlinked input** 单边已取得
> A 级真实 MCP 证据（Event `then` → Branch `execute`；节点数不变 4、1 条边、
> Undo/Redo、Save、Reload `mismatches=[]`）。仅允许一条 existing→existing 边，
> 不支持 rewire/delete/批量/与新节点混用。zero-node stale 与 PIE 为 native B。

disconnect 分支与“1 条 incident edge 的 node remove”本轮没有跑真实 MCP 序列，
保留 P3/P4 的 B 级 native 证据；不要把这三种未跑分支写成 A。

### 5.3 明确不支持 / 不是 UE 写

| 项 | 结论 |
|---|---|
| `ue_preview_graph_patch` / `ue_apply_patch` | 离线 Canonical JSON 编辑与计划，不是 live UE 写；不能冒充 Editor 写证据 |
| `ue_compile_blueprint` | 独立 live action，不属于本轮写序列；本切片不自动编译 |
| 自动保存 | 无；每类写都必须显式 Save |
| 多节点批量删除、注释/事件/Knot/Macro/Tunnel/变量节点删除 | P4 明确拒绝 |
| 二进制/结构体/Object/容器 pin 默认值 | P1 明确拒绝 |
| nested/非顶层图写 | 只读发现支持，写入不支持 |

## 6. 仍未关闭的缺口（按优先级）

| # | 缺口 | 状态 | 影响 / 建议 |
|---|---|---|---|
| 1 | live-only MCP server 仍强制要求 index DB | **OPEN** | `IndexQueryService.check()` 在任何 live 工具可用前执行；本轮用 disposable 空 DB 绕开，未改产品启动契约。需单独设计“live-only 无需索引”的启动路径 |
| 2 | 实验工具不在静态 `tool_registry` | **OPEN（设计选择）** | 静态 `TOOL_DEFINITIONS_BY_NAME` 看不到 15 个五类写工具（layout 工具另有独立开关），仅运行时注册；离线工具目录不完整。新增 focus 工具同样动态注册（避免改变静态工具计数） |
| 3 | 参数命名不一致 | **OPEN** | GraphEdit/Preflight 用 `assetPath/graphGuid`；PinDefault/Variable/Rewire/NodeRemove 用 `asset_path/graph_guid`。Agent 需要按工具族区分；本轮不改契约 |
| 4 | Save receipt 仅进程内 | **OPEN（by design）** | Apply 后必须同进程 Save；跨进程只能只读 Reload |
| 5 | nested/非顶层图写 | **OPEN** | focused 顶层 K2 Event Graph 是硬前提；`GetAllGraphs` 只用于定位，不授予写权限 |
| 6 | 无 Editor 崩溃/超时恢复 | **OPEN** | E2E 脚本按阶段重启 Editor；真实产品需进程监控与 receipt 失效处理 |
| 7 | 单序列覆盖 | **OPEN（部分收窄）** | 原 P5 只覆盖一个 Actor BP；2026-10-28 accessor 验收新增独立 BP（4 类型 + EventGraph + FunctionGraph）与 native PIE/retarget，但仍未覆盖多资产并发、崩溃恢复、undo 栈很深 |
| 8 | 变量 Get/Set 访问器节点 | **A 正向 / B 安全门** | 真实 MCP 正向已通过（10 accessors、Undo/Redo、Save、fresh-process Reload）；PIE 与同类型 retarget 为 native B。macro 图路径未实测 |
| 9 | 新节点连接已存在 Pin | **A 正向 / B 安全门** | 真实 MCP 正向已通过（4 条 existing-pin 连接、edge-set before/after、Undo/Redo、Save、fresh-process Reload）；PIE 与 Preview 后 stale 为 native B。批量、改类型、rewire 仍不支持 |
| 10 | 已有 output → 已有 unlinked input（`nodes=[]`） | **A 正向 / B 安全门** | 真实 MCP 正向已通过（1 条边、节点数不变、Undo/Redo、Save、Reload）；zero-node stale/PIE 为 native B。仅一条边，不支持 rewire/批量/与新节点混用 |
| 11 | 精确断开一条已有 edge（`operation=disconnect`） | **A 正向 / B 安全门** | 真实 MCP 从两条已有 Event→Branch 边中精确删除 1 条，另一个边/全部节点保留；Undo/Redo、Save receipt、Reload 通过。disconnect 后 stale/PIE 为 native B。仅一条边，不支持批量/rewire |
| 12 | 白名单 engine pure static `call_function` | **STATIC ONLY / NATIVE PENDING** | Python/C++ 合同：固定 `/Script/Engine.KismetMathLibrary` owner + function + signatureDigest + exact pin schema；仅 `Greater_IntInt`、`Less_IntInt`、`Clamp`、`Abs_Int`；任意路径/world/latent/network/receiver/ref/const/container 拒绝。**尚未在 UE5.6.1 构建或执行 A 级** |

已在本轮关闭/降级的缺口：

- P1-P4 无 A 级正向证据 → 五类写已在同一真实链路中取得 A。
- 无 MCP 聚焦工具 → `ue_focus_blueprint_graph` + explicit-focus 回退。
- Preflight 缺 edge-set hash → 已暴露两个只读 hash。
- GraphEdit 后 PinDefault 事务被丢弃 → `RF_Transactional` 修复。
- Rewire/NodeRemove Save hash 损坏 → receipt 生命周期修复。

## 7. 复现方式

```powershell
# 完整构建 + 隔离工程 + 门禁负例 + 五类写 + 跨工程负例 + 新进程 Reload
pwsh scripts/VerifyBlueprintAgentEndToEnd.ps1 `
  -EngineRoot <UE_5.6> `
  -Output <AUDIT_OUTPUT>

# 复用已构建插件，只重跑验收
pwsh scripts/VerifyBlueprintAgentEndToEnd.ps1 `
  -EngineRoot <UE_5.6> -SkipBuild `
  -Output <AUDIT_OUTPUT>
```

脚本只操作隔离的 `Build/DirectHost` 与输出目录；不会写用户工程或生产资产/索引。
脚本在 `finally` 中移除项目 ini 的实验开关并停止 Editor。原始运行产物按内部流程
归档，不进入公开仓库；公开仓库只保留可复现脚本和脱敏后的结果说明。

## 8. 通过与阻塞判定

- 只有 A 级证据才记为“真实 Agent 可用”；本轮五类写均达到 A，且 reload
  `verified=true`。
- 门禁默认保持 OFF；只有 disposable HostProject 在脚本运行期间临时开启。
- 本文不声称生产 Ready、不声称 OwnerTrust、不声称 P1-P4 的所有分支（disconnect、
  1 条 incident edge 删除）已有 A 级证据。
- 本文只提供隔离测试环境中的工程证据，不构成 GitHub push/tag/release 或正式
  GO/NO-GO 授权。

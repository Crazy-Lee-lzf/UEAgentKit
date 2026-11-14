# 基于 Action Registry 的节点创建（阶段 A）

状态：**已实现，受实验双门禁约束**。范围：`CallFunction` 与 `DynamicCast` 两类节点。
本文是对外契约说明；实现以插件源码与自动化用例为准。

## 1. 能力边界

> 不做按类名自由创建 / 任意编辑的通用 Graph CRUD；**支持经 Action Registry 发现、由原生 spawner
> 执行的节点创建**（受实验双门禁约束）。

阶段 A 打开的是「项目里凡是 registry 能列出、且不依赖关卡 Actor / 编辑器面板的普通节点」中的两类：

| 能做 | 说明 |
|---|---|
| 调用项目自己的 `UFUNCTION` | 引擎库函数、项目 C++ `BlueprintCallable`、蓝图自建函数都可以，节点由引擎 spawner 创建 |
| Cast 到任意已存在类型 | 不需要 `targetClass` 参数：引擎按目标类各生成一个 action |

不做（本阶段）：

| 不做 | 理由 |
|---|---|
| `K2Node_CustomEvent` | 会新增 `UFunction`，回滚语义与「删自己刚建的节点」不同，另开专项 |
| 事件 / 委托绑定（`K2Node_ActorBoundEvent` 等） | 需要关卡 Actor 或编辑器选中上下文 |
| `Timeline`、`MathExpression` | 内容来自编辑器面板，不在反射里 |
| 未经验证的自定义 `UK2Node` 子类 | pin / 副作用未验证 |
| 按类名自由 spawn、自动布线、自动布局、拖拽模拟 | 形状上就不提供类路径，也不模拟 UI |
| 阶段 B 的接线语义（Wildcard 按槽位连） | 本阶段不碰 `connect` |

## 2. 双门禁

写侧默认关闭，必须**同时**满足：

1. MCP 启动参数 `--enable-experimental-node-spawn`（且必须同时开
   `--enable-experimental-graphpatch-edit`，因为写入口就是该工具族的 `createNode`）；
2. 运行中项目 ini：`[UEAgentKit] ExperimentalNodeSpawn=True`（项目引擎 ini，启动时读取）。

任意一边关闭：请求被拒绝并返回 `graph-spawn-experimental-disabled`，不会静默降级。
读侧（搜索）不受这两把锁约束：registry 默认 `shadow`（只读常驻，不改右键菜单），
ini 仍可显式设 `off|shadow|active`，其中只有 `active` 会改菜单。

两把锁的归属不同，不要混淆：

| 锁 | 位置 | 作用 |
|---|---|---|
| `--enable-experimental-node-spawn` | MCP 进程 | 决定 `createNode` 是否接受 `actionId` |
| `ExperimentalNodeSpawn=True` | 运行中的 Editor 项目 | 决定 Editor 侧是否执行 action spawn |

## 3. 读：`ue_search_node_actions`

只读，一次调用即可拿到「能不能建、为什么不能建」。

| 参数 | 说明 |
|---|---|
| `query` | 关键词（≤256 字符），大小写不敏感，多个词按 AND 匹配 title / menuName / category / 类名 / actionKind / 函数路径 / 目标类 / keywords |
| `node_class` | 精确节点类路径过滤（如 `/Script/BlueprintGraph.K2Node_DynamicCast`） |
| `limit` | 返回条数 1–64 |

实现上它是 `native.invoke`（`toolId=graph.nodeRegistry.catalog`）的薄封装，参数校验 + 结果归一化；
没有第二套 registry，也没有第二个查询工具。

catalog 的普通模式只描述「会话模板预算内」的一小片（实测 762/38375 个 spawner），
所以带过滤条件的检索走**定向描述**：先用不需要模板的廉价身份（函数路径 / 委托名 / 节点类 /
spawner 类）筛出候选，只对候选创建模板，并使用本次调用自己的预算（不占用会话额度、不冻结失败）。
因此：

- 按函数名检索是廉价的（`AddNumbers` 只会描述到那个函数）；
- Cast 的**目标类只存在于模板里**，按目标类检索时必须同时给 `node_class`
  （如 `/Script/BlueprintGraph.K2Node_DynamicCast`），否则关键字检索会跳过 cast 类 action；
- 返回里 `registry.targeted=true` 表示这是候选子集，不是全库枚举。

返回的每个 action：

| 字段 | 说明 |
|---|---|
| `actionId` | 内容哈希（`action:<32 hex>`），写请求唯一标识 |
| `actionKind` / `nodeClass` / `spawnerClass` | 引擎 spawner 身份 |
| `title` / `menuName` / `category` / `payload` | 展示与函数/目标类信息 |
| `trustLevel` | `official` 或 `project`（第三方 spawner 不描述） |
| `pins[]` | registry 从临时模板节点读到的 pin（暴露为参考，不是预测契约） |
| `pure` / `latent` / `variadic` / `requiresTarget` / `requiresWorldContext` | descriptor 自带准入字段 |
| `canCreate` + `rejectReason` | 本阶段能否创建；原因取值为 `latent` / `requiresWorldContext` / `variadic` / `requires-binding` / `action-kind:<kind>` |

## 4. 写：扩展现有 patch 的 `createNode`

请求形状（`actionId` 与 `nodeType` 二选一，同时给出即拒绝）：

```json
{"opId": "n1", "kind": "createNode", "actionId": "action:<32 hex>",
 "x": 9000, "y": 8000, "tempId": "tmp:fn1"}
```

`actionId` 是可选的 `tempId` 唯一标识：与既有 `connect` 的 tempId 语义相同（本阶段不建边）。

### 4.1 执行顺序

1. 解析 `actionId`（形状校验 `action:<32 hex>`）；
2. 在 registry 里解析成引擎 spawner（查不到 → `graph-spawn-action-unknown`）；
3. 用 descriptor 自带字段做准入（不新增规则，见下）；
4. 在**同一事务**内调用 `Spawner->Invoke(Graph, IBlueprintNodeBinder::FBindingSet(), FVector2D(X,Y))` —— **空 bindings**；
5. 创建后读取**实际** `nodeGuid` / `nodeClass` / `pins`（名/方向/类型）与（若可得）`function` / `targetClass`；
6. 沿用既有 patch 契约：逐 op 结果、失败删本批新建节点、双指纹复位、`stateRestored`、Undo/Redo 校验、默认不 Save。

**不做**：手工 `SetFromFunction` / `SetTargetType`。这两件事由 spawner 的 customize delegate 完成
（`BlueprintActionDatabase.cpp` 里 cast 的 lambda 设置 `TargetType`），手工再设会重复并可能覆盖。
**也不写**「预期 pin」：结果是创建后读实际值，计划里不含 pin 契约。

### 4.2 准入字段（全部来自 descriptor）

| 条件 | 错误码 | 说明 |
|---|---|---|
| `latent` | `graph-spawn-action-unsupported` | 消息带上字段名 |
| `requiresWorldContext` | `graph-spawn-action-unsupported` | 消息带上字段名 |
| `variadic` | `graph-spawn-action-unsupported` | 消息带上字段名 |
| spawner 需要 binding / 编辑器选中 | `graph-spawn-requires-binding` | 按 spawner 类判定：`BlueprintBoundEventNodeSpawner` / `BlueprintComponentNodeSpawner` / `BlueprintDelegateNodeSpawner`。理由写进源码注释：不是「节点类型不许」，是「这个 spawner 需要我没有的上下文」 |
| 其它 actionKind（非 `callFunction` / `cast`） | `graph-spawn-action-unsupported` | 消息带上 actionKind |

`requiresTarget` **不拒绝**：非静态函数会得到一个未连线的 self pin，调用方可后续接线。

## 5. 错误码

| 错误码 | 触发条件 |
|---|---|
| `graph-spawn-experimental-disabled` | MCP flag 或项目 ini 未开 |
| `graph-spawn-action-id-invalid` | `actionId` 形状不是 `action:<32 lower hex>` |
| `graph-spawn-action-id-conflict` | 同一 op 同时给了 `nodeType` 与 `actionId` |
| `graph-spawn-action-unknown` | registry 无法把该 id 解析成可描述的 spawner |
| `graph-spawn-action-unsupported` | latent / requiresWorldContext / variadic / 非 callFunction·cast |
| `graph-spawn-requires-binding` | spawner 需要 binding 或编辑器选中状态 |
| `graph-spawn-action-failed` | spawner 返回空节点（引擎侧失败） |
| `graph-edit-node-contract-failed` | 创建后读回与计划不符（类/位置/pin 为空） |

拒绝路径不写任何东西：节点数、四个指纹、package Dirty、磁盘 SHA 均不变（由自动化断言）。

## 6. 证据分级

| 结论 | 证据等级 | 来源 |
|---|---|---|
| 项目 `UFUNCTION` → `K2Node_CallFunction`，pin 与实际一致 | Native 自动化 | `UEAgentKit.NodeSpawn.Function`（真实 Editor，`-unattended -NullRHI`） |
| `Cast To Pawn` → `K2Node_DynamicCast`，`TargetType` 与 pin 正确 | Native 自动化 | `UEAgentKit.NodeSpawn.Cast` |
| 四类拒绝各有错误码且零写入 | Native 自动化 | `UEAgentKit.NodeSpawn.Refusals` |
| 门禁、`actionId` 形状、`canCreate` 归一化 | Python 单元 | `tests/python/test_node_spawn_phase_a.py` |

自动化用的是真实 Editor 进程与真实 patch 事务，但**仍属实验能力**：默认关闭，不代表生产默认可用。
`CustomEvent`、事件绑定、Timeline、MathExpression、阶段 B 的接线都不在本阶段证据范围内。

## 7. 已知限制

- Action Registry 的描述面是「官方 spawner + 项目自己的函数 spawner」；项目/第三方注册的其它
  spawner 类不描述（第三方一律不描述）。
- 需要 binding 的 spawner 只能被「发现」而不能被创建；搜索会返回 `canCreate=false`。
- 普通 catalog 单次最多 512 条（会话模板预算 768），**覆盖不了全部 action database**；
  按关键词/节点类检索走定向描述，只描述候选。Cast 按目标类检索需要带 `node_class`。
- 写侧解析 `actionId` 依赖本次会话已描述的 action：正常流程是「先搜索、再创建」。
  同一 id 在别处拿到但本会话从未搜索过时，返回 `graph-spawn-action-unknown` 并提示先搜索。
- 蓝图自建函数同样走 `BlueprintFunctionNodeSpawner`；它们出现在 action database 中取决于
  该蓝图已编译并已被 registry 枚举。

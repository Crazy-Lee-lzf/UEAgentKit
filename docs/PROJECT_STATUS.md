# UE Agent Kit 能力状态

[English](PROJECT_STATUS_EN.md)

## 版本状态

| 版本 | 状态 | 时间 | 说明 |
|---|---|---|---|
| 0.8.0 | 稳定版 | 2026-09-07 | 上一个稳定发布；Python 3.11–3.12 |
| 0.9.0 Developer Preview 1 | 预发布（已发布） | 2026-10-30 | `v0.9.0-dev1`；Python 3.11–3.14 |
| 0.9.0 Developer Preview 2 | 候选（未发布） | — | 计划版本 `0.9.0.dev2`；本页描述对象 |

本页以 **0.9.0 Developer Preview 2 候选代码**为准。已发布版本的具体行为以对应 Release Notes 为准；DP2 尚未创建 tag 或 GitHub Release。

UE Agent Kit 是 Unreal Engine 的**项目知识层 + 受控修改工作流**：把资产、Blueprint 语义、引用关系和 Editor 状态转换为可搜索数据，为 Agent 提供上下文与验证证据，并通过 Policy / Revision / Transaction / Save / Verify 门禁执行窄范围写入。

- MCP 暴露的工具面用 `--tool-profile` 分档（默认 `core` = 15 个工具；`read` / `live` / `live-write` / `full` 逐步放开）。档位只做**上限**，不会打开被开关关掉的组；`ue_get_capabilities.toolProfile` 回报当前档位。
- PIE 只读读取统一走 `ue_pie(action=...)`（`state` / `worlds` / `list_actors` / `inspect_actor` / `scalars` / `list_properties` / `read_properties`）；原来 7 个逐动词 Tool 不再单独注册。参数与桥接返回不变，成功结果额外带一个 `action` 字段回显本次动作。
- DataTable 行写入统一走 `ue_datatable(action=...)`（`set_cell` / `set_row_fields` / `add_row` / `remove_row` / `rename_row`）；原来 5 个逐动词 Tool 不再单独注册，Plan/DryRun 与 Write Policy 门禁不变。
- Project Memory 收敛为两个入口：`ue_memory(action=...)` 负责 7 个只读动作，`ue_memory_write(action=...)` 负责记录写入与 knowledge/work 子动作；原来 14 个逐动词 Tool 不再单独注册，参数、默认值与返回结构不变。

## 读取能力

### 离线索引

- Asset Registry 资产目录与 Package 信息。
- Static Mesh、Skeletal Mesh、Skeleton、Physics Asset、Material、Material Instance、Texture、Animation、DataTable、Data Asset、Niagara、World 等专用读取。
- Blueprint Graph / Node / Pin、变量、函数、宏、接口、Cast、Delegate 语义。
- Canonical JSON、BPCTX/1、SQLite/FTS5 索引，Asset / Symbol / Reference 搜索与反向引用。
- Package SHA-256 Revision 与索引新鲜度比较。

### Live Editor

- Editor / PIE/SIE / World / Selection、Open Assets、Dirty Packages。
- Output Log、Blueprint 编译诊断、精确资产 Live Inspect。
- 按 asset path 读取任意 Blueprint 图上下文；不依赖离线索引。
- PIE Server/Client 的 Actor、Transform、NetRole、组件及指定标量属性（只读）。
- PIE 任意 UObject 的反射属性：先列属性（名称 + kind + 是否可读），再按名读取值；支持标量与 struct 展开、Array/Map/Set 分页、对象引用只给身份；不支持的类型返回 `unsupported-type` 与原因且不返回值。不以 `BlueprintVisible`/`Edit` 作为读门槛。
- 对象引用导航：`objectPath` 可指向该 PIE 世界内任意 UObject，返回的 `objectRef.path` 可直接作为下一跳输入；链路逐跳显式调用、绝不自动递归，跨世界引用标记 `crossWorld` 并被拒绝。

### Niagara 模块栈

- 按真实执行阶段读取 Module Stack：System/Emitter/Particle 的 Spawn 与 Update 分组，模块按引擎顺序返回（不做名称排序），每个 Input 给名称、类型、来源（Local / Linked / Dynamic / Data / ObjectAsset / Expression）与可安全表达的当前值。
- 受控修改 Module Input 的本地常量（Float / Int / Bool / Vector / Color）：一次修改一个 Editor 事务、可 Undo/Redo、不自动 Save、不修改共享 Module Script 资产；来源不是 Local 时拒绝并返回实际来源与链接目标。
- 读写都作用在已打开的 System 上；`NS_DevStack` 之类的夹具由 `scripts/GenerateNiagaraStackFixture.ps1` 生成，不进版本库。

### 实时索引 overlay

- `ue_get_asset` 命中会话级 overlay 时，会先用磁盘 Package 的 SHA-256 校验记录；磁盘变化则自动向 Editor 重新取证。
- 重新取证失败时返回 `overlayStale=true` + `staleCause`，不再返回旧 canonical。
- `ue_refresh_asset_index_live` 可显式刷新单个已保存 Blueprint；只影响当前 MCP 会话（最多 64 条），不写 SQLite、不要求重启。
- 当前范围：`/Game` 下的 Blueprint、普通单文件 Package；World Partition 外部 Actor 与多文件 Package 不支持。

### Project Memory / Knowledge

Rule、Finding、Decision、Known Issue、Task Record、Runtime Evidence；Revision-aware stale/superseded/conflicted；FTS5 召回与可选 Vector + RRF；L2/L3 上下文注入；只读 Knowledge Web。

## 写入能力

所有写入默认关闭。`workflow` 组需要 MCP `--enable-write-tools` + `--enable-subprocess-workflow`、项目 ini `SubprocessWorkflow=True`、以及覆盖目标的 Write Policy。实验 Blueprint 写入使用独立门禁，不经过 Write Policy 白名单。

| 写入域 | 入口 | 定位 | 额外门禁 |
|---|---|---|---|
| Data Asset（标量 / 引用 / Struct·Array·Set·Map） | `workflow` | 稳定面 | workflow 门禁 |
| Material Instance（Scalar / Vector / Texture / Static Switch） | `workflow` | 稳定面 | workflow 门禁 |
| DataTable（Cell / Row Fields / Add·Remove·Rename Row） | `workflow` | 稳定面 | workflow 门禁 |
| Blueprint 已注册 Operation（变量默认值、组件属性、Pin 默认值、描述） | `workflow` | 稳定面 | workflow 门禁 |
| Live 单资产属性 Apply（Editor 内存 + Undo，显式 Save） | `workflow` | 稳定面 | workflow 门禁 |
| AnimSequence 诊断 / 修复 / 重定向辅助 | `workflow` / `live-read` | 有限能力 | 视 Tool 而定 |
| Blueprint 原子 patch：`createNode` / `connect` / `disconnect` / `setPinDefault` / `createVariable` / `rewirePin` / `removeNode` | `ue_*_blueprint_patch_experimental` | 实验 | `--enable-experimental-graphpatch-edit` + ini |
| 通过 Action Registry `actionId` 创建节点（CallFunction / Cast） | 同上 `createNode` | 实验 | 再加 `--enable-experimental-node-spawn` + ini |
| Blueprint 位置批量移动 / edit session | `workflow`（`ue_move_blueprint_node*`） | 实验接口 | workflow 门禁 |

实验 patch 的限制：一次最多 16 个操作、16 个新节点、8 个新变量；边操作受总操作数限制，单节点最多 8 条连接；`removeNode` 每次最多 1 个节点和 1 条 incident edge；只接受顶层 K2 事件图、干净 Package、非 PIE；一次 Apply 一个事务，失败回滚；Save 独立。完整用法见 [Blueprint 实验用法](BLUEPRINT_AGENT_E2E_USAGE.md)。

运行模式、门禁与启动命令见 [Agent Quickstart](AGENT_QUICKSTART.md)；完整参数见 [构建与运行](BUILD_AND_RUN.md)。实际可用 Tool 始终以 `ue_get_capabilities` 为准。

## Agent 工作流

Task Context、Relevant Asset Discovery、Impact Analysis、Change Set、Semantic Diff、Verification Plan、Trust Verdict、Authorized Save / Strong Verify、Recovery / rollback evidence。写入完成不等于任务成功；独立验证和证据是流程的一部分。

## P4 / Perforce

Source Control 为 opt-in。支持 mapping / opened / lock / owner / client / have / head 查询、精确文件 `p4 edit`、受限 safe sync、pending changelist 查询/创建/描述更新、精确已打开文件 `reopen`、resolve preview、满足条件的普通文本 `resolve -am`，以及持久化审计记录。

Agent 不执行 P4 Submit / Revert / P4-managed Delete；不提供通用 P4 argv / shell 透传；`.uasset` / `.umap` 不自动选择 yours/theirs，也不自动内容 Resolve。

## 明确不支持

| 能力 | 说明 |
|---|---|
| 按类名自由创建节点 / 任意 Graph CRUD / 自动布线 | 只有七种实验 patch 操作和有界节点入口 |
| CustomEvent、事件/委托绑定、Timeline、MathExpression | 不在 Action Registry 创建范围 |
| 用 `createNode` 创建 `Select` / `PromotableOperator` | 只能连接其 wildcard 端点，由 schema 推导类型 |
| 嵌套子图写入 | 实验 patch 只验证顶层 K2 事件图 |
| 任意 Level Actor Spawn/Delete/Transform/Property | 不提供通用关卡编辑 |
| Material Graph / Niagara / Sequencer / Control Rig 通用写入 | 只有专用只读或有限流程 |
| PIE 输入注入 / 录制回放 | 只读诊断 |
| Asset Import / Duplicate / Rename / Delete / Migrate | 不提供通用资产生命周期操作 |
| Console / Python / Shell / UObject Method 执行 | 不提供通用执行入口 |
| 自动 Save All / 自动编译 | Save 与 Compile 都是独立动作 |
| Agent 侧 P4 Submit / Revert / Delete | 由人完成 |

## 安全模型

- 默认只读；写入必须显式启用。
- Revision、Dirty Package、Session、Target Identity 不满足时拒绝。
- 失败回滚与 Undo 证据优先于“看起来成功”。
- Save 成功不等于任务成功；需要独立 Verify 和可追溯证据。

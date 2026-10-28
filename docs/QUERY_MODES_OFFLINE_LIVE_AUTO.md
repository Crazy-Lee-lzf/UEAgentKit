# 查询模式现状：Offline / Live / Auto（差距评估）

> 状态声明：本仓库**尚未实现**统一的 Offline / Live / Auto（Hybrid）查询框架。本文是对当前代码的事实盘点与差距评估，不是对已有能力的宣传。Asset Index schema v2 只增强 Offline 检索，没有新增 Online 查询实现。

## 1. 目标契约（用户架构约定）

- **所有读取/查询都必须同时支持 Offline 与 Online**；
- **Offline**：资产、Blueprint、C++ 代码符号/引用搜索使用本地 SQLite 索引与快照元数据，不依赖 UE Editor 或外部服务；
- **Online**：通过 UE Editor live RPC 读取当前可变的资产状态；可以先做 Offline 宽候选，再用 Live 做详细重读；
- **Auto / Hybrid**：根据 Editor 可用性、索引新鲜度、查询类型自动选择或组合；
- **写入只允许 Online**，且必须经 UE Editor 插件；Offline 可以生成计划，但**绝不直接按字节修改 `.uasset`**。

## 2. 当前能力矩阵

| 能力 | Offline | Live（UE Editor RPC） | Auto / Hybrid |
| --- | --- | --- | --- |
| 资产全文搜索 | ✅ `queries.search_assets` / `ue_search(scope="assets")` | ❌ 无 broad live search | ❌ 无 |
| 符号搜索（含 CJK v2） | ✅ `queries.search_symbols` / `ue_search(scope="symbols")` | ❌ | ❌ |
| 节点搜索（CJK v2 新增离线查询） | ✅ `queries.search_nodes` | ❌ | ❌ |
| 单资产详情 | ✅ `queries.get_asset`（SQLite 快照） | ✅ `ue_inspect_asset_live`（当前 Editor 状态） | ❌ 无统一合并/重读 |
| 引用查询 | ✅ `queries.find_references`（深度 1–3） | ❌ 无引用图 live 查询 | ❌ |
| C++ 代码符号/引用 | ✅ `build_code_index` + `search_symbols(profile="code")` | ❌ | ❌ |
| Editor 选区/打开资产/脏资产/PIE/日志/编译错误 | ❌ | ✅ `ue_get_selection`、`ue_get_open_assets`、`ue_get_dirty_assets`、`ue_get_pie_state`、`ue_get_output_log`、`ue_get_compile_errors` | ❌ |
| Blueprint 图选区/上下文 | ❌（仅索引中的图节点） | ✅ `ue_get_blueprint_graph_selection`、`ue_get_focused_blueprint_graph_context` | ❌ |
| PIE 运行时只读（World/Actor/Component） | ❌ | ✅ `ue_get_pie_world_contexts`、`ue_inspect_pie_actor`（PIE instance + Server/Client/NetMode；有界；stopped fail-closed；只读） | ❌ |
| 快照元数据 | ✅ `IndexQueryService` 返回 snapshot id、数据库 schema 版本等 | ⚠️ 仅 bridge/编辑器状态，不是资产快照 | ❌ 无统一快照语义 |
| 写入 | ❌ 仅允许生成计划 | ✅ 经 Editor 插件的 live write / patch 工具 | ❌ |

## 3. 当前 Offline 实现

入口：

- `ue_agent_kit.queries`：`search_assets`、`search_symbols`、`search_nodes`、`find_references`、`get_asset`、`get_stats`；
- `ue_agent_kit.agent_api.IndexQueryService`：MCP/Agent 的只读查询服务，使用 immutable/read-only SQLite 连接；
- `ue_agent_kit.code_index.build_code_index` + `code_symbols.rebuild_code_semantics`：C++ 类/结构/枚举/函数/属性、include/inherits/implements 引用；
- `ue_agent_kit.index_snapshot` / `freshness`：索引快照与新鲜度信息。

特征：

- 完全不需要 UE Editor、不需要网络、不启动外部进程；
- 数据来自 AssetCatalog Canonical JSON 和源码文件；
- schema v2 的 CJK 子串检索只作用于 `symbols.search_text` / `nodes.search_text`；
- 资产搜索 `assets_fts` 保持 v1 行为；
- 查询只读，不写 SQLite，更不写 `.uasset`；
- 旧 v1 索引被拒绝，必须用 `index build --rebuild` 重建（见 [INDEX_SCHEMA_V2_MIGRATION.md](INDEX_SCHEMA_V2_MIGRATION.md)）。

## 4. 当前 Live 实现

入口：

- `ue_agent_kit.editor_bridge`：与 Editor 插件的请求/响应通道；
- `mcp_live_tools.register_live_read_tools`：`ue_editor_status`、`ue_get_selection`、`ue_get_open_assets`、`ue_get_dirty_assets`、`ue_get_current_level`、`ue_get_pie_state`、`ue_get_output_log`、`ue_get_compile_errors`、`ue_inspect_asset_live`、Blueprint 图选区/上下文等；
- PIE 只读事实：`ue_get_pie_world_contexts`（PIE WorldContext 列表：唯一 `pieInstanceId` + Server/Client/NetMode）与 `ue_inspect_pie_actor`（精确 world 内 Actor 的 path/class/transform/NetRole + 有界组件 class/path/assets）；两者只读，不做 PIE 启停，不做 Gameplay/Animation/GAS 属性，不做跨 world 猜测；
- 写入侧：live write / structured write / material / data table / retarget 等工具，均经过 Editor 插件与验证流程。

特征：

- Live 查询是**按工具、按资产路径**的细粒度读取，不是全项目搜索；
- Live 能力依赖 Editor 在线、插件启用、bridge 可达；
- Live 读取不使用 SQLite 快照，也不返回索引快照 id；
- 上述 PIE 只读工具本身不访问索引/数据库，但**不改变**“没有统一 `mode=offline|live|auto` 入口”的现状，也**不代表** MCP 进程可在完全无索引的情况下启动（见 §5 第 7 条）；
- 写入已有 Online-only 约束，但这是分散在各工具中的实现，不是一个统一的查询/写入模式框架。

## 5. 明确差距（未实现）

1. **没有 query mode 参数/入口**：MCP 工具和 CLI 都是固定的 Offline 或 Live 工具，没有 `mode=offline|live|auto` 的统一选择。
2. **没有可用性探测**：没有“Editor 是否在线、版本是否匹配、bridge 是否健康”的统一探测，也没有超时/降级策略。
3. **没有 Auto 决策**：不知道何时该用索引、何时该用 Live、何时该合并；没有新鲜度阈值、资产类型规则或用户偏好设置。
4. **没有候选合并与重排**：不能先 Offline 取宽候选，再对每个候选做 Live 详细重读并合并字段/冲突。
5. **没有统一结果契约**：Offline 结果包含 `snapshotId`、`databaseSchemaVersion`，Live 结果包含 Editor/bridge 状态；没有共同的 `source`、`freshness`、`snapshot`、`degradedReason` 字段。
6. **Live 侧没有 broad symbol/node 搜索**：现有 Live 只读工具不能替代 `search_symbols` / `search_nodes`。
7. **pureLive 启动仍依赖索引路径**：MCP server 启动时构造 `IndexQueryService` 并绑定数据库路径；没有“完全不用索引、只连 Editor”的启动模式。索引缺失时，pureLive 场景仍会被索引配置挡住。
8. **没有 Offline plan → Online apply 的统一桥**：写入工具各自实现计划/应用/验证；没有一个跨模式的“离线生成计划、在线校验并应用”的通用协议。
9. **没有模式级审计**：无法从一次响应中证明“这是离线快照结果”“这是实时结果”“这是两者合并结果”。

## 6. 本次变更的边界

本分支（Index v2 CJK）只做：

- schema v2：`symbols` / `nodes` 的 `search_text` 与 FTS 触发器；
- Offline 查询的 CJK 子串语义与 `search_nodes`；
- v1 拒绝、v2 原子重建、只读查询校验；
- Offline C++ 代码索引在 v2 上继续工作；
- 测试与迁移文档。

**不包含**：

- 新的 Online EditorQuery 框架；
- Offline/Live 自动选择或合并；
- Live broad search；
- pureLive 无索引启动模式；
- 任何对 `.uasset` 的离线字节写入。

## 7. 建议的后续实现顺序

1. 定义统一查询结果契约（`source`、`snapshotId`、`observedAt`、`freshness`、`degradedReason`）；
2. 增加 Editor 可用性探测与只读 capability 协商；
3. 为 `ue_search` / `ue_get_asset` / `ue_find_references` 增加显式 `mode`，Auto 先只做“可用性选择”，不做复杂合并；
4. 增加“Offline 候选 + Live 单资产重读”的受限 Hybrid 路径，并对冲突字段显式标注来源；
5. 最后再考虑 pureLive 启动和完整 Offline plan → Online apply 协议。

在上述工作完成前，任何文档、MCP capability 或 UI 都不得声称 Auto/Hybrid 已可用。

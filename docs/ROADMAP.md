# UE Agent Kit 路线图

更新时间：2026-08-03

当前已发布版本为 **0.7.0**，支持 Unreal Engine 5.6。Realtime Foundation、注册式 Live Editor Write、Schema v3 Memory/Context MVP、分帧 Batch Task 和持久化 Change Set 已正式进入本地发布。`feature/live-editor-realtime-io` 与 `feature/memory-context` 继续作为长期分支；当前首要目标转为 0.8.0-dev Context/Analysis、Blueprint 常用编辑扩展和大型项目性能基准。

## 总体方向

UE Agent Kit 的长期定位是面向 AI Agent 的 Unreal Engine 项目智能层：提供可追踪的项目读取、Policy 约束写入、Revision-aware 长期记忆、证据驱动分析、影响评估和可验证修改闭环。

当前 Server 模式：

```text
Offline             5 Tool（Memory 17）
Live               27 Tool（Memory 39）
Workflow           31 Tool（Memory 43）
Combined           53 Tool（Memory 65）
```

## 已完成基础

- 项目级资产与 Blueprint 只读导出、Canonical JSON、BPCTX、Revision、Symbol/Reference、SQLite/FTS5。
- MCP 查询、稳定分页、Token Budget、固定项目安全配置、错误模型和四源资产状态。
- 受限 localhost Editor Bridge、日志、编译诊断、资产检查、Graph/Node 定位、Daily Actions、Automation Test 和授权单资产保存。
- Live Editor Write 基础层：当前注册 12 个 Data Asset、Material Instance 与 DataTable Operation；统一 Transaction/Evidence、显式 Undo/Discard、Authorized Save → Verify、可恢复 Journal、Fast/Full 真实回归和注册式资产域执行器均已完成。
- Blueprint、Non-Blueprint 标量、Material Instance、DataTable 和 Data Asset 的受控写入。
- Policy、Revision、Dry Run、显式 Commit、Backup Manifest、独立重载验证和 rollback。
- DataTable 单 Row 多字段、Row 新增/删除/重命名和 Searchable Name 引用影响门禁。
- Data Asset Object/Class、Soft Object/Class、Struct、Array、Set 和 Map 稳定值模型与结构化 Diff。
- Material Instance Scalar、Vector、Texture、Static Switch 统一原生 JSON 状态/Diff 报告、Override/Expression GUID 元数据和真实四类型回归。
- Data Validation 与 Automation Test 结果绑定 Project、Editor Session、UTC 和稳定 Asset Revision Set；Automation 明确使用 `not-applicable` 覆盖语义。

## 0.5.x：已完成

0.5.5 完成了 Live Editor、Daily Actions、受控写入扩展、验证证据、单资产多 Operation 原子事务、CI 和正式发布工程。Input Mapping Context 等专用写入仅在真实项目需求出现后增加。

完整 Blueprint Graph、Material Graph、Anim State Machine、Control Rig、Sequencer、Niagara 写入和任意脚本执行仍不属于 0.5.x 范围。

## 0.6.0：Revision-aware Project Memory（已完成）

项目事实、规则、决策、Known Issue、Task Record 和 Runtime Evidence 记录来源、范围、时间、置信度和关联 Revision Set。

核心要求：

- 区分 `user-confirmed`、`tool-observed` 和 `model-inferred`。
- 支持 `valid`、`stale`、`conflicted`、`superseded`、`unverified`。
- 资产 Revision 改变后自动使相关事实 stale。
- 冲突结论并存，不静默覆盖。
- Task Record 可关联 Patch、Backup Manifest、验证报告和最终结论。

## 0.7.0：Live Editor Write 基础层（已发布）

当前 `main` 已完成 12 个受控 Operation、通用 `operation + assetPath + target + value` 请求、Property/Material/DataTable 资产域模块、统一 Transaction/Evidence、精确 Undo/Discard、Authorized Save → Independent Verify、Memory Evidence 和可恢复 Live Apply Journal。新增 Operation 仍必须注册明确 Target、Policy、Snapshot、Undo、失败恢复与真实 UE 回归；注册本身不授予写权限，也不会开放任意 UObject Method、脚本或自动保存。

Realtime I/O 与 Memory/Context 的完整职责、性能预算、风险分级和 Worktree 协作见 [`AI_NATIVE_UE_EDITOR.md`](AI_NATIVE_UE_EDITOR.md) 与 [`BRANCH_WORKTREES.md`](BRANCH_WORKTREES.md)。

## 0.7.0：Memory 可用性与分层知识树（已发布）

0.7.0 已把 0.6.0 平面记录库升级为低维护、低 Token 的单人可用层；后续 Memory 开发继续在长期分支中进行：

- Knowledge Tree 使用稳定 Path 与 Parent/Child 支持任意深度，默认从 Project Profile、System、Feature/Entity 到 Implementation。
- 长期知识、Record Type、Active Work 与 Evidence 四个概念分离。
- 当前目标、TODO、阻塞和下一步使用独立 Active Work，不污染长期知识搜索。
- 查询采用五级渐进式披露，默认只返回摘要并由 Server 强制 Token Budget。
- MCP 负责存储、检索、去重、Revision stale、自动 Evidence 和维护规则；Skill 只保留约 400–800 Token 的薄使用说明。
- 已提供 `ue_memory_get_context`、`ue_memory_expand_node`、`ue_memory_get_evidence`、`ue_memory_update_knowledge` 和 `ue_memory_update_work` 高层入口。

下一步只补齐任务 ID、Active Work、Change Set、Editor Session 和 Evidence 的横向绑定，并建立大型项目耗时基准；这些工作不阻塞 Realtime Reader/Writer 并行扩展。完整设计见 [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)。

## 横向：大型项目性能基准

性能方案以“首次索引允许较慢、日常修改必须接近改代码体验”为核心。计划使用 Reforge、现成 UE5.6 DarkRuins 样本、E 盘 SSD 上的 160–180 GB 物理性能工程，以及 500k Asset/10m Reference 逻辑数据库；同一基准分别运行原生 SSD 和 50 MB/s HDD 模拟档位。普通变量和少量 Blueprint Node 的 Live Apply、Compile、Undo 与单资产保存作为最高优先级交互门禁。完整方案见 [`PERFORMANCE_TEST_PLAN.md`](PERFORMANCE_TEST_PLAN.md)。

## 0.8.0-dev：上下文与分析

计划能力包括自动 Context Pack、值来源追踪、执行链追踪、影响分析、语义资产 Diff、证据支持的假设、修改计划和验证计划。无法证明的结论必须明确标记为推断。

## 0.9.0：协作与冲突感知

读取 Source Control Provider、Checkout/Lock/Owner/Head，分析 Local Dirty、磁盘 Revision 与 Depot/Remote Head 分歧，并建立资产责任边界和多人冲突风险模型。首版只分析、提示或阻止，不自动抢锁或覆盖他人修改。

部署采用每人一个 Local MCP + 团队共享 Knowledge Service。Local MCP 连接本机 Editor Bridge，并在内部访问共享服务；不让 Agent 同时管理 Local UE MCP 与 Shared Knowledge MCP，也不使用一个中央 MCP 直接路由所有开发者的编辑器。共享服务保存 `/project` 与 `/team` 知识和 Active Work，本地保留 `/user`、`/session`、Editor 状态和资产索引。共享更新使用乐观并发与 `knowledge-conflict`，禁止静默覆盖。

## 持续门禁

- Ruff、Python 全测和 JSON Schema。
- UE5.6 插件编译。
- 受影响写入能力的真实 Dry Run/Commit/reload/rollback。
- UTF-8 无 BOM、CRLF、whitespace 和完整 Diff 检查。
- 小型性能基线进入常规合并门禁；大型 160–180 GB 工程和 500 GB 模型按里程碑运行。
- 不提交 Output、Backups、测试工程资产、日志、缓存和本地配置。

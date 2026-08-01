# UE Agent Kit 路线图

更新时间：2026-08-01

当前已发布版本为 **0.6.0**，支持 Unreal Engine 5.6。Revision-aware Project Memory 已完成并进入稳定维护；`main` 当前为 **0.7.0-dev**，已完成受控 Live Editor Write 基础层，开发重点转向 Memory 可用性、分层知识树与 Context/Analysis。

## 总体方向

UE Agent Kit 的长期定位是面向 AI Agent 的 Unreal Engine 项目智能层：提供可追踪的项目读取、Policy 约束写入、Revision-aware 长期记忆、证据驱动分析、影响评估和可验证修改闭环。

当前 Server 模式：

```text
Offline             5 Tool（Memory 12）
Live               23 Tool（Memory 30）
Workflow           26 Tool（Memory 33）
Combined           44 Tool（Memory 51）
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

## 0.7.0-dev：Live Editor Write 基础层（已完成）

当前 `main` 已完成 12 个受控 Operation、通用 `operation + assetPath + target + value` 请求、Property/Material/DataTable 资产域模块、统一 Transaction/Evidence、精确 Undo/Discard、Authorized Save → Independent Verify、Memory Evidence 和可恢复 Live Apply Journal。新增 Operation 仍必须注册明确 Target、Policy、Snapshot、Undo、失败恢复与真实 UE 回归；注册本身不授予写权限，也不会开放任意 UObject Method、脚本或自动保存。

## 0.7.0 前置：Memory 可用性与分层知识树

在自动 Context Pack 之前，先把 0.6.0 平面记录库升级为低维护、低 Token 的可用层：

- Knowledge Tree 使用稳定 Path 与 Parent/Child 支持任意深度，默认从 Project Profile、System、Feature/Entity 到 Implementation。
- 长期知识、Record Type、Active Work 与 Evidence 四个概念分离。
- 当前目标、TODO、阻塞和下一步使用独立 Active Work，不污染长期知识搜索。
- 查询采用五级渐进式披露，默认只返回摘要并由 Server 强制 Token Budget。
- MCP 负责存储、检索、去重、Revision stale、自动 Evidence 和维护规则；Skill 只保留约 400–800 Token 的薄使用说明。
- 计划提供 `memory_get_context`、`memory_expand_node`、`memory_get_evidence`、`memory_update_knowledge` 和 `memory_update_work` 高层入口。

该阶段完成后，再让 0.7.0 Context Pack 沿知识树逐级收集上下文。完整设计见 [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)。

## 0.7.0：上下文与分析

计划能力包括自动 Context Pack、值来源追踪、执行链追踪、影响分析、语义资产 Diff、证据支持的假设、修改计划和验证计划。无法证明的结论必须明确标记为推断。

## 0.8.0：协作与冲突感知

读取 Source Control Provider、Checkout/Lock/Owner/Head，分析 Local Dirty、磁盘 Revision 与 Depot/Remote Head 分歧，并建立资产责任边界和多人冲突风险模型。首版只分析、提示或阻止，不自动抢锁或覆盖他人修改。

部署采用每人一个 Local MCP + 团队共享 Knowledge Service。Local MCP 连接本机 Editor Bridge，并在内部访问共享服务；不让 Agent 同时管理 Local UE MCP 与 Shared Knowledge MCP，也不使用一个中央 MCP 直接路由所有开发者的编辑器。共享服务保存 `/project` 与 `/team` 知识和 Active Work，本地保留 `/user`、`/session`、Editor 状态和资产索引。共享更新使用乐观并发与 `knowledge-conflict`，禁止静默覆盖。

## 持续门禁

- Ruff、Python 全测和 JSON Schema。
- UE5.6 插件编译。
- 受影响写入能力的真实 Dry Run/Commit/reload/rollback。
- UTF-8 无 BOM、CRLF、whitespace 和完整 Diff 检查。
- 不提交 Output、Backups、测试工程资产、日志、缓存和本地配置。

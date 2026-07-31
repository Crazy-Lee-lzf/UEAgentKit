# UE Agent Kit 路线图

更新时间：2026-07-31

当前已发布版本为 **0.6.0**，支持 Unreal Engine 5.6。Revision-aware Project Memory 已完成并进入稳定维护；`main` 已完成首个 Live Editor Write 纵向闭环，当前开发同时推进 Live Write 基础层与 0.7.0 Context/Analysis。

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
- 首个 Live Editor Write：Policy/Revision Plan 后修改已打开 Clean 非 Blueprint 资产的顶层标量属性，记录 Undo、标记 Dirty 且不自动保存。
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

## 0.6.x 后续开发快照：Live Editor Write

首个纵向闭环已经完成，但当前只支持一个已打开、Clean、非 Blueprint 资产的顶层标量属性。下一步不是立即开放任意 UObject，而是先完成统一 Live Transaction/Evidence、显式 Undo/Discard、Reference/Structured Property、Material Instance、DataTable，以及 Live Apply → Authorized Save → Verify → Memory Task 闭环。

## 0.7.0：上下文与分析

计划能力包括自动 Context Pack、值来源追踪、执行链追踪、影响分析、语义资产 Diff、证据支持的假设、修改计划和验证计划。无法证明的结论必须明确标记为推断。

## 0.8.0：协作与冲突感知

读取 Source Control Provider、Checkout/Lock/Owner/Head，分析 Local Dirty、磁盘 Revision 与 Depot/Remote Head 分歧，并建立资产责任边界和多人冲突风险模型。首版只分析、提示或阻止，不自动抢锁或覆盖他人修改。

## 持续门禁

- Ruff、Python 全测和 JSON Schema。
- UE5.6 插件编译。
- 受影响写入能力的真实 Dry Run/Commit/reload/rollback。
- UTF-8 无 BOM、CRLF、whitespace 和完整 Diff 检查。
- 不提交 Output、Backups、测试工程资产、日志、缓存和本地配置。

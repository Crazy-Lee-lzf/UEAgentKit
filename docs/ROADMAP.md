# UE Agent Kit 路线图

更新时间：2026-08-15

当前已发布版本为 **0.7.0**，支持 Unreal Engine 5.6。Realtime Foundation、注册式 Live Editor Write、Schema v3 Memory/Context MVP、分帧 Batch Task 和持久化 Change Set 已正式进入本地发布。`feature/live-editor-realtime-io` 已 **fast-forward 合并进 `main`**（`5eb1759 → 56afc91`，含完整 Realtime Animation Tools 线）；动画功能扩展暂缓，后续只在真实任务或 Benchmark 证明存在高价值缺口时解冻。`feature/performance-benchmarks` 继续作为长期横向性能分支。当前首要目标转为 **0.8.x Context / Analysis / Agent Reliability**：先把现有 Index、Memory、Live Editor、Revision、Change Set 和验证证据组合成任务上下文、影响分析、语义 Diff 与可信结果判断，再由真实 Agent Benchmark 决定下一批 Writer。

## 总体方向

UE Agent Kit 的长期定位是面向 AI Agent 的 Unreal Engine 项目智能层：提供可追踪的项目读取、Policy 约束写入、Revision-aware 长期记忆、证据驱动分析、影响评估和可验证修改闭环。

当前 Server 模式：

```text
Offline             5 Tool（Memory 17）
Live               38 Tool（Memory 50）
Workflow           88 Tool（Memory 100）
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

0.7.0 已把 0.6.0 平面记录库升级为低维护、低 Token 的单人可用层；Memory 底层 Schema 暂停扩张，后续只在 `feature/agent-reliability` 主线中做 Task Context / Active Work / Change Set / Evidence 的横向整合：

- Knowledge Tree 使用稳定 Path 与 Parent/Child 支持任意深度，默认从 Project Profile、System、Feature/Entity 到 Implementation。
- 长期知识、Record Type、Active Work 与 Evidence 四个概念分离。
- 当前目标、TODO、阻塞和下一步使用独立 Active Work，不污染长期知识搜索。
- 查询采用五级渐进式披露，默认只返回摘要并由 Server 强制 Token Budget。
- MCP 负责存储、检索、去重、Revision stale、自动 Evidence 和维护规则；Skill 只保留约 400–800 Token 的薄使用说明。
- 已提供 `ue_memory_get_context`、`ue_memory_expand_node`、`ue_memory_get_evidence`、`ue_memory_update_knowledge` 和 `ue_memory_update_work` 高层入口。

下一步只补齐任务 ID、Active Work、Change Set、Editor Session 和 Evidence 的横向绑定，并建立大型项目耗时基准；这些工作不阻塞 Realtime Reader/Writer 并行扩展。完整设计见 [`MEMORY_ARCHITECTURE.md`](MEMORY_ARCHITECTURE.md)。

## 横向：大型项目性能基准

性能方案以“首次索引允许较慢、日常修改必须接近改代码体验”为核心。计划使用 Reforge、现成 UE5.6 DarkRuins 样本、E 盘 SSD 上的 160–180 GB 物理性能工程，以及 500k Asset/10m Reference 逻辑数据库；同一基准分别运行原生 SSD 和 50 MB/s HDD 模拟档位。普通变量和少量 Blueprint Node 的 Live Apply、Compile、Undo 与单资产保存作为最高优先级交互门禁。完整方案见 [`PERFORMANCE_TEST_PLAN.md`](PERFORMANCE_TEST_PLAN.md)。

## 0.8.0-dev：Realtime Animation Tools（已完成，已合并 main）

2026-08 在 `feature/live-editor-realtime-io` 上完成动画比例诊断与受控修复的完整纵向闭环，并已 fast-forward 合并进 `main`。逐阶段交付与验收见 [`Plans/ANIMATION_TOOLS_FOLLOWUP_PLAN_20260806.md`](Plans/ANIMATION_TOOLS_FOLLOWUP_PLAN_20260806.md) 与 [`Plans/ANIMATION_TOOLS_P5_P9_DETAILED_PLAN_20260815.md`](Plans/ANIMATION_TOOLS_P5_P9_DETAILED_PLAN_20260815.md)。

- **P0 单资产动画比例修复**：`ue_diagnose_animation_scale` → `ue_plan_animation_scale_fix` → `setAnimationScaleFix`（Force Root Lock / Root Motion / Root Track Scale）+ Undo / Discard / Authorized Save / Independent Verify / Index Refresh 闭环。
- **P1 批量只读审计**：`ue_start_animation_scale_audit` / `ue_get_animation_scale_audit` / `ue_cancel_animation_scale_audit` / `ue_export_animation_scale_audit_report`（显式列表，1000 上限 / Batch 8 / Page 50）。
- **P2 批量修复**：`ue_plan_animation_scale_fix_batch` + Live Apply / Save / Verify / Index Refresh / Rollback（不可变 Batch Plan，分片 8，持久化分片 2）。
- **P3 重定向 + 后处理**：`ue_analyze/plan/apply/save/verify/rollback_animation_retarget*` + `ue_start/get/plan/refresh/reopen_animation_retarget_postprocess`（批重定向闭环 + 输出后处理分类/建议）。
- **P4 Additive / Base Pose**：`ue_diagnose_additive_animation` / `ue_evaluate_animation_with_base_pose` / `ue_plan_additive_base_pose_fix` / `setAdditiveBasePoseFix`（组合求值 + 自引用 Base Pose 修正）。
- **P5 浮空诊断**：`ue_diagnose_character_ground_contact`（只读，Capsule / Mesh Offset / 动画来源分类）。
- **P6 次级运动读取**：`ue_inspect_skeletal_secondary_motion`（只读，附加骨骼链 / Skin Weight / Physics / Cloth / AnimBP 节点）。
- **P7 项目级可写配置**：`resolve_project_policy` + `--policy-profile` + 三个示例 Policy + `retargetCapabilities` 校验。
- **P8 ModelPreview 接入**：只读基线（插件 junction / Editor Status / 104 资产索引 / 比例诊断）验证完成；**写阶段（步骤 5–9）废弃**（接入仅为验证工具正确性，已达成；发现 ModelPreview 98 个重定向 AnimSequence 的 Skeleton 引用损坏，但不再修复）。

只读诊断能力沿用 `retarget.inspect` 门禁（避免重复 policy 字段冲突）；写入走单资产 `setAnimationScaleFix` / `setAdditiveBasePoseFix` + 批量 `*_batch` 的 Policy / Revision / Snapshot / Undo / Save / Verify / Rollback 闭环。

## 0.8.x：Context / Analysis / Agent Reliability（当前主线）

下一阶段不再以 Tool 数量、资产类型数量或 Writer 广度作为主要进度指标。基础设施已经能够提供项目索引、Revision-aware Memory、Live Editor 状态、受控写入、独立验证和 Rollback；当前重点是把这些能力组合成 Agent 可直接使用的高层分析与信任层。

推荐开发分支：`feature/agent-reliability`。详细执行计划见 [`Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md`](Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md)。

里程碑按可独立提交、可中断的方式推进：

```text
R0  Task Context / Context Pack MVP
R1  Impact Analysis
R2  Semantic Diff
R3  Verification Plan + Trust Verdict
R4  Real Agent Benchmark v1
R5  Value Provenance / Execution Trace（由 Benchmark 决定优先级）
```

首批目标不是新增大量 UE 写入，而是回答：Agent 当前应该改什么、修改会影响什么、以及有什么证据证明结果正确。无法证明的结论必须明确标记为推断；保存成功和独立重载成功只属于 Persistence Evidence，不自动等同于整个任务成功。

动画线作为已完成的纵向能力保留，Additive Batch、Composite Mutation、Retarget → P2 一键桥接等非阻塞尾巴默认冻结。Blueprint Graph、Level Actor 通用 CRUD 等新 Writer 同样改为由 Reforge 真实需求或 Agent Benchmark 失败数据驱动。

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

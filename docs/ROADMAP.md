# UE Agent Kit 路线图

更新时间：2026-08-23

当前已发布版本为 **0.7.0**，支持 Unreal Engine 5.6，正式版本保持不变。`feature/agent-reliability` 上的 **0.8.x Context / Analysis / Agent Reliability capability scope** 已完成本地收口：R0-R4、R4.1 repeat、Read/Write Gap Audit 与 Scope Freeze 均已有确定性证据，Must-fix new tools 为 0，R5 继续 `deferred by benchmark evidence`。本次 closeout 不修改 Package/Plugin version，不创建 Tag 或 Release artifact，也不 Push；正式 0.8 package release 如后续启动，应作为独立流程执行。

## 总体方向

UE Agent Kit 的长期定位是面向 AI Agent 的 Unreal Engine 项目智能层：提供可追踪的项目读取、Policy 约束写入、Revision-aware 长期记忆、证据驱动分析、影响评估和可验证修改闭环。

当前 Server 模式：

```text
Offline            10 Tool（Memory 22）
Live               43 Tool（Memory 55）
Workflow-only       60 Tool（Memory 72）
Live + Workflow     93 Tool（Memory 105）
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

### R0 状态（已完成）/ R1 状态（已完成，2026-08-18）

R0.0（现状审计 + 复用矩阵 + 最小 Schema）、R0.1（`ue_get_task_context` 第一条纵向切片）、R0-S（真实 Reforge Context Smoke）、R0.2（Deterministic Relevant Asset Discovery）与 R0.3（只读 Cross-source Correlation）已在 `feature/agent-reliability` 完成并本地提交，R0 里程碑标记完成：

- `ue_get_task_context(query + assetPaths → targetAssets → revisionState → memory/activeWork → liveEditor → changeSet → correlation → risks → nextExpansions)` 在全部模式下注册为只读 query 组 Tool；Memory / Live / Change Set / Revision 任一来源不可用时只降级对应 section，不拖垮整个请求。
- `risks` 仅包含确定性事实（dirty/stale/conflicted/not-found/session-mismatch 等），零模型推断；输出受 `max_output_tokens` 强制裁剪并在 `outputBudget` 中显式报告。
- R0.2：`relevantAssets` 为确定性相关资产候选集——query 分词（≤8 term）+ 复用 Asset Search 与少量 Symbol Search 补充、与显式目标互斥、固定排序、Top N≤8，每条带 `assetPath / assetClass / source / whyIncluded / matchKind`，无 score/confidence；预算不足时先裁候选 metadata 再减候选数量，候选永不优先于 target identity / high risk / revision summary。真实 Reforge Smoke 观察与验证见 [`Plans/AGENT_RELIABILITY_R0_REAL_CONTEXT_SMOKE_20260816.md`](Plans/AGENT_RELIABILITY_R0_REAL_CONTEXT_SMOKE_20260816.md)。
- R0.3：`correlation` 为只读、非持久化、零模型推断的 Cross-source Correlation——精确键联接 Active Work、显式 Change Set、Live Editor Session 与 Memory Evidence（session id 相等、资产路径集合交集、changeSetId 字面量、资产 scope Evidence）；不新增 Memory/ChangeSet Schema、不扫描 workflow 私有 `_change_sets`、不自动发现 Change Set、无引用遍历；链接固定排序上限 16 条，边界计数在 summary 如实报告；预算不足时先裁 correlation links/summary。交接见 [`Handoffs/AGENT_RELIABILITY_R0_SLICE3_HANDOFF_20260816.md`](Handoffs/AGENT_RELIABILITY_R0_SLICE3_HANDOFF_20260816.md)。
- 复用矩阵、Request/Response Schema 与已知边界见 [`Plans/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md`](Plans/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md)。

### R1 状态（已完成）

R1（Impact Analysis）已在 `feature/agent-reliability` 一次性完成并本地提交，**R1 里程碑标记完成**：

- 新增只读 query 组 Tool `ue_analyze_change_impact`：1..8 个精确 `/Game` 目标、`maxDepth` 1..3（默认 2）、bounded consumer/edge/path 上限与 `max_output_tokens`；方向契约固定 consumer → target，BFS 只按精确键（`target_asset_path IN frontier`）分块查询、全局 visited 防环、shortestDepth/Impact Path 稳定。
- Direct Consumers（多 kind 合并、`impactedTargets[]` 去重）+ Bounded Indirect Consumers + 多目标共享 consumer 合并；Reference Kind 确定性归一化（`asset-reference / class-reference / blueprint-symbol-reference / parent-reference / unknown-reference` 等 7 类），未覆盖 kind 原样保留绝不猜测。
- Unknown / Unsupported 一等公民：`targets[].found=false`、`unsupported-impact-subject`（结构化 subject 仅 `asset-level` 与 `blueprint-symbol` 可被现有 Index 机械证明）、`analysisGaps`（no-consumer-evidence / unknown-reference-kind / runtime-sensitivity-not-proven / frontier-truncated）。
- `validationTargets`（Tier 0 目标 / Tier 1 Direct / Tier 2 Indirect，确定排序）；确定性 risks（`high-fanout-target / impact-analysis-truncated / impact-target-not-indexed / unknown-reference-kind`）；`runtimeSensitiveConsumers` 固定 `not-proven-with-current-evidence`，不凭资产类型猜测。
- R0 集成：`ue_get_task_context.nextExpansions` 增加 impact-analysis 渐进入口（显式目标 / relevantAssets hint），默认 Context 不自动展开引用图。
- 真实 Reforge 只读 Smoke（48 资产 immutable 索引）S1–S4 全部通过：S1 fan-out（23 direct / 282 edges / 14.4 ms）、S2 真实 2 跳（Wheel←VehicleBase←CargoBase，24 indirect）、S3 多目标共享 consumer 合并（24 direct / 8 indirect）、S4 零消费者边界语义。设计、复用审计、边界与建议性能指标见 [`Plans/AGENT_RELIABILITY_R1_IMPACT_ANALYSIS_DESIGN_20260818.md`](Plans/AGENT_RELIABILITY_R1_IMPACT_ANALYSIS_DESIGN_20260818.md)；完整执行规范见 [`Handoffs/AGENT_RELIABILITY_R1_FULL_HANDOFF_20260818.md`](Handoffs/AGENT_RELIABILITY_R1_FULL_HANDOFF_20260818.md)。

### R2 状态（已完成，2026-08-19）

R2（Semantic Diff）已在 `feature/agent-reliability` 一次性完成，**R2 里程碑标记完成**：

- 新增只读 query 组 Tool `ue_analyze_semantic_diff`，只接受显式 `change_set_id`；`stage=auto|live|persisted|verified`、精确 `/Game` 资产过滤（最多 8）、`include_unchanged`、有界 `max_changes` 与 Token Budget 均有严格契约，不扫描私有 Change Set，也不接受任意本地 before/after 文件。
- 统一资产中心的 Expected / Actual / Matched / Unexpected / Missing Expected / Unchanged Critical / Analysis Gaps / Risks；稳定 SHA-256 ID、固定排序与裁剪规则覆盖单资产多 Operation、Change Set 多资产和同一路径多次修改（保留 `operationChain`，最终只比较首个 before 与最终 expected/actual）。
- 四个确定性 Adapter 覆盖 Data Asset scalar/reference/struct/array/set/map、DataTable cell/row-fields/add/remove/rename、Material Instance scalar/vector/texture/static-switch，以及 Blueprint property/component/pin-default 窄写入；证据不足时显式保留 Gap，不扩展 Writer。
- Evidence Stage 明确区分 Editor Memory LiveApply、授权保存后的 Canonical/commandlet Persisted、独立重载 Canonical Verified；`auto` 只选择所有返回 Operation 都完整具备的最高阶段，请求不可用阶段返回 `semantic-diff-stage-unavailable`。Verified Semantic Diff 仍不等于 R3 Trust Verdict。
- `changed=false` 的 expected no-op 现在绑定独立 `noop_*` Change Set Operation，并以 `no-op` / `not-required` 终态收束；不制造假 Transaction、LiveApply Receipt、Live Journal、Save 或 Verify 证据。固定基线 Canonical Revision 与 Plan `expectedRevision` 完全一致时才可形成 persisted no-op evidence。
- R0 仅在显式 Change Set 存在时建议 Semantic Diff；R2 在 missing/unexpected 时建议 R1 `ue_analyze_change_impact`，两者都不自动展开。
- 真实 UE5.6 DirectHost Smoke 已覆盖 Data Asset、Material Instance、DataTable cell/rename 的 live/persisted/verified 共 12 个结果，以及 Blueprint `setVariableDefault` 的 commandlet persisted/verified；全部 expected=actual=matched、unexpected=missing=0，并完成恢复验证。无 C++ 变更，因此不要求 UE Direct Build。

R2 设计、复用审计、协议、测试和边界见 [`Plans/AGENT_RELIABILITY_R2_SEMANTIC_DIFF_DESIGN_20260819.md`](Plans/AGENT_RELIABILITY_R2_SEMANTIC_DIFF_DESIGN_20260819.md)；完整执行规范见 [`Handoffs/AGENT_RELIABILITY_R2_FULL_HANDOFF_20260818.md`](Handoffs/AGENT_RELIABILITY_R2_FULL_HANDOFF_20260818.md)。

### R3 状态（已完成，2026-08-20）

R3（Verification Plan + Trust Verdict）已完成公共协议、核心实现、真实 UE Smoke、全量门禁与文档同步。完整执行规范见 [`Handoffs/AGENT_RELIABILITY_R3_FULL_HANDOFF_20260820.md`](Handoffs/AGENT_RELIABILITY_R3_FULL_HANDOFF_20260820.md)，设计与 Evidence Audit 见 [`Plans/AGENT_RELIABILITY_R3_VERIFICATION_TRUST_DESIGN_20260820.md`](Plans/AGENT_RELIABILITY_R3_VERIFICATION_TRUST_DESIGN_20260820.md)。

- 已新增只读 `ue_build_verification_plan` 与 `ue_evaluate_trust_verdict`：前者从显式 Change Set、Operation Domain、R1 Impact 与 R2 Semantic Diff 生成确定性验证义务；后者只消费适用于当前 Change Set / Revision / Editor Session 的已有 Evidence。
- Assertion 固定区分 `required / recommended / informational` 与 `pass / fail / unknown / not-applicable`；Required Evidence 缺失必须保持 UNKNOWN，不能由 Agent 或模型补全。
- Verdict 固定为 `verified / suspicious / failed / insufficient-evidence`。Required FAIL 优先得到 failed；无 FAIL 但 Required UNKNOWN 得到 insufficient-evidence；Required 全关闭但仍有确定性 non-blocking risk 得到 suspicious；只有 Required Assertions 全部通过/不适用且无阻断风险才能得到 scoped verified。
- R3 复用现有 Persistence / Independent Verify、R2 Semantic Diff、R1 Impact、Blueprint Compile、Data Validation、Automation 与 Revision/Freshness Evidence；不得再造 Writer、Diff 或 Reference Graph。
- Trust Tool 不自动执行 Compile / Validate / Automation / Save / Verify，只返回 exact nextActions；Compile、Validation、Automation 由固定项目、无任意 JSON 注入、有界且不持久化的 session-local Evidence Store 捕获。
- `verified` 必须同时返回 verification scope / unverified dimensions，不能被表述成“玩法/视觉/性能/所有运行时行为绝对正确”。R3 完成后停止，不自动进入 R4。
- Registry 当前计数契约为 Offline 10、Offline+Memory 22、Live 43、Live+Memory 55、Workflow 60、Workflow+Memory 72、Live+Workflow 93、Combined+Memory 105。真实 UE5.6 S1–S5 观察到四态并完成 fixture recovery；Ruff 全仓与 Python 648/648 通过。R3 已完成并在进入 R4 前形成独立提交。

### R4 状态（已完成，2026-08-22）

R4（Real Agent Benchmark v1）已完成。设计见 [`Plans/AGENT_RELIABILITY_R4_BENCHMARK_DESIGN_20260820.md`](Plans/AGENT_RELIABILITY_R4_BENCHMARK_DESIGN_20260820.md)，正式结果与限制见 [`Plans/AGENT_RELIABILITY_R4_BENCHMARK_RESULT_20260820.md`](Plans/AGENT_RELIABILITY_R4_BENCHMARK_RESULT_20260820.md)：

- 15 个 Full Case、9 个 matched Legacy Case，共 24 个真实 attempt；24/24 保留，0 infrastructure failure，9/9 fairness matched，17/17 DirectHost 精确恢复，7/7 Reforge 只读不变。
- Paired `Full - Legacy`：Task Completion `+44.44 pp`、Trusted Completion `+22.22 pp`、False Success `-11.11 pp`、Wrong Asset `-22.22 pp`、Tool Calls `-4.11`。
- Full 的绝对 Trusted Completion 仍只有 `26.67%`，False Success / all cases 仍为 `33.33%`；stale detection 比 Legacy 差，R0–R3 只能判定为局部有效。
- 最大 raw failure 类是 `trust-evidence-gap=8`；Agent/tool/context/harness 类合计 8。Value Provenance 与 Execution Trace 均为 0 次 primary failure。
- Reference derived-edge normalization 与 `trustVerdict` exact-string contract 会保守压低部分指标，结果文档同时报告 raw grader 数字与审计限定。
- 数据不支持立即进入 R5。R5 继续冻结。

### 0.8.x Closeout（已完成，2026-08-23）

`C0 Agent UX / Result Contract` → `C1 Trust Evidence` → `C2 Narrow Reliability / Recovery` → `C3 R4.1 Repeat` → `C4 Read/Write Capability Gap Audit` → `C5 Must-fix Gaps` → `C6 Release Review / Scope Freeze` 已一次性完成。执行边界见 [`Handoffs/AGENT_RELIABILITY_0_8_CLOSEOUT_FULL_HANDOFF_20260822.md`](Handoffs/AGENT_RELIABILITY_0_8_CLOSEOUT_FULL_HANDOFF_20260822.md)。

R4.1 使用冻结 fingerprint 的 high-fanout、stale、Blueprint default、Data Asset scalar 四个 anchor，Full/Legacy 各 3 次，共 24/24 retained、12/12 paired fairness matched、0 measurement drift、0 infrastructure failure、24/24 exact recovery。Full stale 与 Blueprint 均 3/3 Trusted；high-fanout 3/3 因越过 direct-only bound 形成 False Success；scalar 只有 1/3 exact claim Trusted，另两次把 numeric beforeValue stringify。完整分布见 [`Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md)。

Read/Write Audit 已覆盖 105 个公共 Tool 与 18 个 Patch Operation，C5 结论为 `0 Must-fix new tools`。剩余问题属于 Agent bound/value typing、静态证据边界与 Full 写链成本，不是缺 UE Read/Write capability；完整 Scope Freeze 见 [`Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md)。

0.8 capability scope 已完成本地收口，但最新正式发布版本、Package 与 Plugin Version 仍保持 0.7.0；本阶段没有创建 Tag、Release artifact 或 Push。只有后续真实数据反复出现 Value Provenance / Execution Trace blocker，R5 才解冻。

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

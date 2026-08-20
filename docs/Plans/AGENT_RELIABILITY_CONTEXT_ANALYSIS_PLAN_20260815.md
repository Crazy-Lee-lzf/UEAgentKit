# UEAgentKit 0.8.x Context / Analysis / Agent Reliability 执行计划

> 更新时间：2026-08-20
> 当前实现基线：`feature/agent-reliability@59eb29c2b716ca10ac700c17c96332a0fbeb8e55`（R3 Verification Plan + Trust Verdict 已完成）
> 当前状态：R0 Task Context、R1 Impact Analysis、R2 Semantic Diff 与 R3 Verification Plan + Trust Verdict 已全部完成。R4 Real Agent Benchmark v1 已获明确指令，按一个完整大任务一次性推进；Primary Agent 可内部拆分、并行、真实 Agent A/B 跑分和 checkpoint，但不中途等待逐片确认。R5 未开始。
> 建议开发分支：`feature/agent-reliability`（已创建，勿 Push）
> 横向长期分支：`feature/performance-benchmarks`
> 执行方式：按大里程碑推进。R4 当前完整执行边界见 `docs/Handoffs/AGENT_RELIABILITY_R4_FULL_HANDOFF_20260820.md`；R3 完成结果见 `docs/Plans/AGENT_RELIABILITY_R3_VERIFICATION_TRUST_DESIGN_20260820.md`。

---

## 0. 阶段定位

UEAgentKit 的基础设施阶段已经基本完成：

```text
项目可读 / 可搜索
→ Live Editor 可读取
→ 受控修改
→ Revision / Plan / Transaction
→ Save / Verify / Rollback
→ Revision-aware Memory
→ 多个真实资产域纵向闭环
```

下一阶段不再以「增加 Tool 数量 / 资产类型 / Writer 广度」作为主目标。

新的 0.8.x 主线是：

# Context / Analysis / Agent Reliability

目标是让 Agent 能回答三个问题：

```text
1. 我现在应该改什么？
2. 这个修改会影响什么？
3. 我怎么证明自己真的改对了？
```

动画工具线暂时冻结为已完成的能力与验证样本，不继续补 Additive Batch、Composite Mutation、Retarget 自动桥接等非阻塞尾巴，除非真实任务或 Benchmark 证明其为高频阻塞。

---

## 1. 总体里程碑

本阶段拆成 6 个可独立停止的里程碑：

```text
R0  Task Context / Context Pack MVP
R1  Impact Analysis
R2  Semantic Diff
R3  Verification Plan + Trust Verdict
R4  Real Agent Benchmark v1
R5  Value Provenance / Execution Trace（由 R4 数据决定优先级）
```

推荐先完成 `R0 → R1 → R2`，再做 R3；R4 可以从 R0 开始同步积累 Case，不必等所有功能完成后才启动。

每个里程碑完成后都必须：

- 独立本地 Commit；
- 更新本计划状态；
- 更新 `docs/ROADMAP.md` / `docs/PROJECT_STATUS.md` 中相关状态；
- 不 Push，除非用户明确要求；
- 不为了形式完整继续扩下一个里程碑。

---

## 2. 分支与并行开发策略

### 2.1 主分支结构

建议保持：

```text
main
├─ feature/agent-reliability
└─ feature/performance-benchmarks
```

`feature/live-editor-realtime-io` 已被 `main` 完整包含，应视为历史动画开发分支，不再承载新工作。

`feature/memory-context` 不再恢复为长期主线；Memory 基础能力已经进入 `main`，本阶段只做整合，不再扩底层 Schema。

### 2.2 不要为每个 R0/R1/R2 建长期分支

R0–R5 都在 `feature/agent-reliability` 上按独立 Commit 推进。

只有以下情况才开短期实验分支：

- 高风险方案可能整体推翻；
- 两个主 Agent 需要真正并行修改低冲突模块；
- 性能实验会污染主功能代码。

实验分支应短命，成功后合并/挑选提交，失败直接删除。

---

## 3. Agent 执行模型

本计划明确支持多 Agent 编排。

### 3.1 推荐角色

**Primary Agent（主 Agent）**

具体由哪个 Agent / Harness / 模型承担，不写死在项目文档中。仓库当前状态、Handoff、代码与测试结果是执行事实基线，不使用模型自身的旧记忆替代当前仓库事实。

负责：

- 读取本计划和当前代码；
- 设计接口与数据模型；
- 决定公共协议；
- 实施核心代码或审查子代理实现；
- 控制修改范围；
- 最终运行门禁、审查 Diff、提交；
- 维护本计划状态与交接文档。

**Subagent（子代理）**

适合委派：

- 代码搜索和现状审计；
- 找现有可复用 API；
- 编写/补齐单元测试；
- 统计 Tool Registry / Capability；
- 文档同步检查；
- 小范围无歧义实现；
- 失败日志归类；
- Benchmark Case 执行与结果结构化。

### 3.2 子代理边界

子代理默认不得：

- 独立修改公共 Schema；
- 独立决定新的长期 API；
- 修改多个核心模块后直接提交；
- 执行 Push / Reset / Rebase / Stash / Force 操作；
- 扩展动画 Writer 或任意 UObject 写入；
- 以「顺手补全」为由扩大任务范围。

子代理输出应优先是：

```text
审计结论
候选文件
推荐最小改动
测试缺口
风险
```

Primary Agent 再决定是否实施。

### 3.3 每个里程碑推荐工作节奏

```text
Primary Agent：定义问题与验收标准
    ↓
Subagent A：审计现有代码 / 可复用接口
Subagent B：审计测试 / 文档 / Registry
    ↓
Primary Agent：确定最小设计
    ↓
Primary Agent 或 Subagent：分块实现
    ↓
Subagent：补测试 / 静态审查
    ↓
Primary Agent：全量门禁 + Diff Review
    ↓
本地 Commit
    ↓
更新计划状态 / Handoff
```

不要一次把 R0–R5 全部分给子代理并行实现。

---

# R0：Task Context / Context Pack MVP

## 4. R0 目标

这是下一步**第一优先级大阶段中的第一个里程碑**。

当前已有：

```text
Asset / Symbol / Reference Search
Revision-aware SQLite Index
ue_memory_get_context
Active Work / Evidence
ue_get_editor_context
Dirty Packages / Open Assets
Change Set / Editor Session
```

但 Agent 需要自己调用多个 Tool 拼上下文。

R0 要提供一个高层、只读、确定性的任务上下文入口，概念名称建议：

```text
ue_get_task_context
```

它不是一个新的 Memory 数据库，也不是让 Server 调 LLM 总结，而是把已经存在的事实源按任务组织起来。

## 4.1 输入建议

第一版保持窄接口：

```text
query / intent            必填，用户任务的自然语言描述
assetPaths                可选，已知目标资产
workItemId                可选
changeSetId               可选
includeLiveContext        默认 true（Live/Workflow 模式）
includeMemory             默认 true（Memory 可用时）
maxChars / tokenBudget    有界输出
```

不要第一版就加入复杂 DSL。

## 4.2 输出结构

建议统一为：

```text
TaskContext
├─ request
├─ project
├─ targetAssets
├─ relevantAssets
├─ symbols
├─ references
├─ memory
├─ activeWork
├─ liveEditor
├─ revisionState
├─ changeSet
├─ risks
└─ nextExpansions
```

每个条目必须尽可能带：

```text
source
revision / freshness
whyIncluded
confidence（仅模型推断才需要）
expandToken / nextAction
```

第一版不要生成模型推断结论；`risks` 仅包含确定性风险，例如：

- target asset stale；
- disk/editor revision 不一致；
- target package dirty；
- Active Work 冲突；
- Change Set 已存在但状态不适合继续；
- Memory 证据 stale/conflicted。

## 4.3 R0 实现原则

- 优先复用现有 Index / Memory / Editor Context API；
- 不复制第二套搜索引擎；
- 不新增 Memory Schema；
- 默认结果紧凑，详细内容继续渐进式展开；
- 在 Offline 模式也能返回离线 Context，只是 Live/Memory section 标记 unavailable/not-enabled；
- 不因为某一来源不可用让整个 Context 请求失败；
- 结果必须明确区分事实、缺失和推断；第一版最好完全不做推断。

## 4.4 R0 验收

至少覆盖：

```text
1. 只有 query，无 assetPaths
2. 显式单资产
3. 多资产
4. Memory disabled
5. Live Editor disabled
6. Dirty target asset
7. stale Memory / stale Revision
8. changeSetId 存在 / 不存在
9. token/char budget 截断
10. 大量 References 时保持有界
```

验收重点不是 Tool 数量，而是：

> 同一个真实任务，以前 Agent 需要多次零散查询；现在一次 Context 请求能拿到足够开始分析的最小事实集，而且没有把整个项目塞进上下文。

---

# R1：Impact Analysis

## 5. R1 目标

回答：

> 如果我要修改这些资产/字段，哪些东西可能受影响，应该验证哪些范围？

不是简单包装 Reverse Reference Count。

建议新增高层只读能力：

```text
ue_analyze_change_impact
```

第一版支持现有 UEAgentKit 已能可靠识别的目标：

- Asset-level 修改；
- DataTable Row / Searchable Name；
- Data Asset 引用；
- Material Instance Parent/Parameter 消费关系（能证明多少做多少）；
- Blueprint Symbol / Default / Component / Pin 已有索引信息。

## 5.1 输出至少区分

```text
directConsumers
indirectConsumers
referenceTypes
runtimeSensitiveConsumers
unknownConsumers
validationTargets
riskLevel
```

不要把「有引用」直接等同于「一定受影响」。

每个影响结论要带证据来源。

### 5.0 R1 完成状态（2026-08-18）

R1 已按**一个完整大任务**一次性完成并本地提交到 `feature/agent-reliability@b9203e4`。Public Tool `ue_analyze_change_impact` 已覆盖 Direct Consumers、bounded Indirect Consumers、多目标去重与 shortest Impact Path、Reference Kind 确定性归一化、Unknown/Unsupported、Validation Targets、确定性 Risks、Graph/Token Budget 与 R0 渐进展开入口；真实 Reforge S1–S4 Smoke、Python 全量 592/592、Ruff 与文档门禁均已通过。

完整执行规范、硬边界、测试矩阵和最终汇报格式见：

`docs/Handoffs/AGENT_RELIABILITY_R1_FULL_HANDOFF_20260818.md`

R1 完成必须至少覆盖：

```text
ue_analyze_change_impact
Direct Consumers
Bounded Indirect Consumers
多目标去重 + Impact Path
Reference Kind / Domain Evidence 的确定性解释
Unknown / Unsupported 边界
Validation Targets
Deterministic Risks
Graph / Token Budget
R0 Task Context 渐进展开入口
真实 Reforge Smoke
全量门禁与文档同步
```

不得为了“完成 R1”使用模型推断填补证据空白；无法证明的 runtime sensitivity / field-level impact 必须明确标记为 `unknown / unsupported / insufficient-evidence`，运行时执行链和值来源继续留给 R5。

## 5.2 R1 与 R0 的关系

R0 可以返回简短 impact hint；完整 Impact Analysis 通过 R1 Tool 展开。

不要把 R1 的深度引用遍历直接塞进默认 Task Context，避免大项目 Token/性能爆炸。

---

# R2：Semantic Diff

## 6. R2 目标

把当前各 Writer/Verify 中零散的 before/after evidence 统一为可读的语义变化：

```text
expectedChanges
actualChanges
unexpectedChanges
unchangedCriticalFields
```

第一版只覆盖已经拥有稳定结构化快照的域：

- Data Asset；
- DataTable；
- Material Instance；
- 已注册的 Blueprint 窄范围属性写入。

动画不作为 R2 首批开发目标；已有动画 Diff/Verify 只作为参考实现。

## 6.1 原则

Semantic Diff 不等于：

```text
SHA before != SHA after
```

而应回答：

```text
用户要求改什么？
实际改了什么？
有没有额外变化？
关键不应变化的部分是否保持？
```

优先从现有 Snapshot / OperationSpec / Canonical Export 派生，不要重新加载整项目。

### 6.2 完成结果（2026-08-19）

R2 已按**一个完整大任务**一次性完成。实现复用了既有 Change Set journal、Plan、LiveApply transaction、Authorized Save、Canonical Export 与 Independent Verify evidence，没有新增 Change Set 持久化 Schema，也没有扩大 Writer。新增 `ue_analyze_semantic_diff` 只接受显式 Change Set，以确定性 Domain Adapter 对齐 intent 与 live/persisted/verified actual evidence；expected no-op 使用独立 `noop_*` Operation 收束，避免伪造事务、保存或验证证据。

完整执行规范见：

`docs/Handoffs/AGENT_RELIABILITY_R2_FULL_HANDOFF_20260818.md`

R2 完成必须至少覆盖：

```text
Change Set 驱动的 ue_analyze_semantic_diff
Expected / Actual / Matched / Unexpected / Missing / Unchanged Critical
live / persisted / verified Evidence Stage
Data Asset / DataTable / Material Instance / Blueprint 窄写入 Domain Adapter
Multi-operation / Multi-asset / same semantic path chain
Revision/Freshness/Gap/Risk/Token Budget
R0/R1 渐进展开集成
真实 UE/Reforge Smoke + 全量门禁
```

R2 不生成最终 Trust Verdict；Semantic Diff 是否足以证明任务正确留给 R3。R2 完成后必须停止，不自动进入 R3。

详细设计、R2.0 复用矩阵、测试和真实 Smoke 结果见：

`docs/Plans/AGENT_RELIABILITY_R2_SEMANTIC_DIFF_DESIGN_20260819.md`

---

# R3：Verification Plan + Trust Verdict

## 7. R3 目标

把各 Domain 当前分散的验证方式统一成「验证计划」与最终可信度判定。

建议的数据模型：

```text
VerificationPlan
├─ persistenceAssertions
├─ structuralAssertions
├─ semanticAssertions
├─ referenceAssertions
├─ compileValidation
├─ automationValidation
└─ regressionAssertions
```

最终输出统一：

```text
TrustVerdict
├─ verdict: verified / suspicious / failed / insufficient-evidence
├─ assertions[]
├─ evidence[]
├─ unexpectedChanges[]
├─ unresolvedRisks[]
└─ recommendedNextActions[]
```

第一版只组合已经存在的可靠证据，不追求通用视觉/游戏体验判断。

核心要求：

> 「保存成功」「独立重载成功」只能作为 Persistence PASS，不能自动等同于整个任务 Verified。

### 7.1 完成状态（2026-08-20）

R3 已按**一个完整大任务**一次性完成并本地提交到 `feature/agent-reliability@59eb29c`。完整 Handoff：

`docs/Handoffs/AGENT_RELIABILITY_R3_FULL_HANDOFF_20260820.md`

R3 已一次性完成：

```text
R3.0 Evidence Audit
ue_build_verification_plan
ue_evaluate_trust_verdict
Required / Recommended / Informational Assertions
pass / fail / unknown / not-applicable
verified / suspicious / failed / insufficient-evidence
Persistence / Semantic / Freshness / Compile / Data Validation / Reference / Automation / Recovery
Evidence applicability（Change Set / Revision / Editor Session / Project）
R1/R2/R0 渐进式集成
真实 UE5.6 Success / Insufficient / Failure / Suspicious Smoke
全量门禁与文档同步
```

Trust Tool 只消费已有证据并生成明确 nextActions，禁止在内部自动 Compile / Validate / Automation / Save / Verify，也禁止任意 Evidence JSON 注入。`verified` 只表示当前 Verification Plan 的 Required Assertions 全部被适用的确定性 Evidence 关闭，不表示玩法、视觉、性能等所有未验证维度都绝对正确。R3 已完成并停在 R4 之前。

---

# R4：Real Agent Benchmark v1

## 8. R4 目标

第一次用真实 Agent 任务证明 UEAgentKit 的价值，而不是只证明 Tool 自测通过。

Benchmark 必须覆盖整个 UEAgentKit，不以动画为中心。

第一版建议 12–20 个 Case，来源优先级：

1. Reforge 真实开发中已经出现过的问题；
2. 当前已支持的 Data Asset / DataTable / Material Instance / Blueprint 窄写入；
3. Context / Reference / Dirty / stale / rollback 场景；
4. 少量动画案例仅作为已有成熟 Domain 的一个样本。

## 8.1 Case 定义

每个 Case 固定记录：

```text
caseId
initialState
userIntent
allowedChanges
forbiddenChanges
expectedSemanticResult
requiredEvidence
expectedFailureMode（如适用）
recoveryRequirement
```

## 8.2 指标

至少统计：

```text
Task Completion Rate
Semantic Correctness Rate
Trusted Completion Rate
False Success Rate
Wrong Asset Rate
Unintended Change Rate
Stale Context Detection Rate
Rollback / Recovery Success Rate
Human Intervention Count
Tool Calls
Token Usage
Elapsed Time
```

最重要的两个北极星指标：

```text
Trusted Completion Rate
False Success Rate
```

## 8.3 Benchmark 的用途

Benchmark 不是发布展示，而是决定下一步开发优先级。

例如：

```text
20 个任务中 7 个失败都因为缺少 Value Provenance
→ R5 Value Provenance 升级为高优先级

20 个任务中只有 1 个因为没有 Additive Batch
→ 动画 Batch 继续冻结
```

以后新增 Writer 必须优先由真实失败数据驱动，而不是因为 Roadmap 上「还没覆盖」。

### 8.4 当前执行指令（2026-08-20）

R4 已获明确指令，按**一个完整大任务**一次性完成。完整 Handoff：

`docs/Handoffs/AGENT_RELIABILITY_R4_FULL_HANDOFF_20260820.md`

R4 v1 必须建立真实 Agent Runner、版本化 Case、确定性 Ground Truth Grader、指标汇总与 Full/Legacy A/B Tool Profile；Full Profile 运行全部 12–16 个跨域 Case，Legacy 至少运行 8 个 matched Case。两组必须保持同一 Agent/Harness/模型/Prompt/Fixture，Legacy 只能隐藏 R0–R3 高层分析/Trust Tool，不能削弱任何 Policy/Revision/Confirm/Verify 安全门禁。Reforge 只做只读真实分析 Case，写入统一使用可恢复 DirectHost Fixture。R4 结束必须用真实失败分类决定是否进入 R5 以及先做 Value Provenance 还是 Execution Trace，并停止在 R5 实现之前。

---

# R5：Value Provenance / Execution Trace

## 9. R5 定位

R5 不要求在 R0 后立即开发。

当前候选：

```text
值来源追踪
Blueprint Exec / Function / Interface / Dispatcher 链
跨资产调用链
Evidence-backed Hypothesis
```

是否先做 Value Provenance 还是 Execution Trace，应由 R4 Benchmark 和 Reforge 实际需求决定。

原则：

> 不提前建设没有真实任务证明价值的重型分析系统。

---

## 10. 暂缓范围

本阶段默认不做：

```text
Additive Base Pose Batch Writer
Retarget Postprocess → P2 一键自动桥接
Montage / BlendSpace / AimOffset Mutation
通用 AnimGraph Writer
通用 Blueprint Graph Writer
Control Rig / Niagara / Sequencer Writer
Level Actor 通用 CRUD
任意 UObject Method
任意 Python / Console / Shell
Collaboration / Checkout / Lock / Owner / Depot Head
Memory 底层 Schema 扩展
```

若真实 Benchmark 或 Reforge 高频任务证明某项是主要阻塞，再单独解冻。

---

## 11. 横向：Performance Benchmark

`feature/performance-benchmarks` 保持独立。

本阶段新增 Context / Impact Analysis 后，应追加对应性能指标：

```text
Task Context warm p50/p95
Task Context 结果字符 / Token 规模
Impact Analysis depth=1 / depth=2 p95
大引用图截断行为
Memory + Index + Live Context 组合延迟
Semantic Diff 单资产耗时
```

性能分支只负责测量、数据生成和稳定门禁；不要在性能分支独立定义 Context 公共协议。

公共协议先在 `feature/agent-reliability` 完成并合入 `main`，性能分支再同步。

---

## 12. 统一工程门禁

每个里程碑至少执行：

```text
G1  git status / diff 审计
G2  Ruff
G3  Python 全量测试
G4  JSON Schema / Tool Registry 固定契约（如受影响）
G5  UE5.6 Direct Build（有 C++ 变更时）
G6  对应真实 UE5.6 Smoke（涉及 Live Editor 时）
G7  git diff --check
G8  文档状态同步
G9  本地 Commit
```

持续约束：

- 不 Push，除非用户明确要求；
- 不 Reset / Stash / Revert 当前工作；
- 不提交 Output / Build / Backups / Intermediate / Saved / 日志 / 测试生成资产；
- 不因为测试方便开放任意脚本或任意 UObject 写入；
- 不为了减少 Tool Call 而牺牲 Revision / Freshness / Evidence 边界。

---

## 13. 本地 Agent 接手后的第一项任务

~~第一轮只做 **R0.0 现状审计 + R0.1 接口设计**~~（已完成，见下）

第一轮（R0.0 + R0.1）已于 2026-08-15 完成并本地提交到 `feature/agent-reliability`：

```text
1. 从本地最新 main（cc1f0c9，包含 22632c7）创建 feature/agent-reliability。
2. 读取全部指定文档，主 Agent + 两个 Flash 只读审计完成现状盘点。
3. 复用矩阵与最小 Schema 落盘：docs/Plans/AGENT_RELIABILITY_R0_AUDIT_AND_SCHEMA_20260815.md。
4. 实现 src/ue_agent_kit/task_context.py（TaskContextService + ue_get_task_context 注册）；
   注册进 tool_registry（query 组，全模式可用）与 mcp_server（capabilities.taskContext、
   server instructions、project status、严格参数）。
5. 契约测试 tests/python/test_task_context.py（T1–T10 + 校验 + MCP 注册/降级，14 用例）。
6. 门禁：Ruff 通过；Python 全量 530/530 通过；Tool Registry / MCP counts 契约更新
   （6/18、39/51、89/101）；无 C++ 变更、无 Live Editor 行为变更，无需 UE Build/Smoke。
7. 文档同步：ROADMAP.md、PROJECT_STATUS.md、spec/MCP_SERVER.md、本计划、新 Handoff。
8. 独立本地 Commit（不 Push）。
```

R0.0/R0.1 完成后按交接执行 R0-S（真实 Reforge Context Smoke）+ R0.2（Deterministic Relevant Asset Discovery），2026-08-16 完成并本地提交；随后按新指令完成 R0.3（只读 Cross-source Correlation），R0 里程碑至此完成；2026-08-18 按 `AGENT_RELIABILITY_R1_FULL_HANDOFF_20260818.md` 一次性完成整个 R1（Impact Analysis）；2026-08-19 按 `AGENT_RELIABILITY_R2_FULL_HANDOFF_20260818.md` 一次性完成整个 R2（Semantic Diff），并停止在 R3 之前。

R0-S + R0.2 概览：

```text
1. 真实 Reforge（48 资产 logic 索引）只读 Smoke：S1 显式目标 4096（1274 tokens 零裁剪，
   revisionState fresh 三方 SHA-256 相等）、S2 query-only 4096（R0.1 基线无任何候选；
   整句搜索 0 命中、分词搜索有命中）、S3 显式目标 1024（880 tokens，阶梯裁剪可解释）。
2. 结构化观察落盘：docs/Plans/AGENT_RELIABILITY_R0_REAL_CONTEXT_SMOKE_20260816.md。
3. R0.2 实现：task_context.py 新增确定性 relevantAssets 候选发现（query 分词 ≤8 term，
   复用 IndexQueryService.search 的 Asset Search + Symbol Search 补充，精确 assetPath 去重，
   与显式目标互斥，固定排序（matchCount 降序 → 首个命中 term 位置 → assetPath），Top N≤8，
   每条含 assetPath/assetClass/source/whyIncluded/matchKind，可附 matchedTerms/matchCount/
   matchedSymbol；搜索子源异常按既有错误模型降级，不伪造结果）。
4. 预算阶梯插入候选裁剪档（relevant-assets-metadata → relevant-assets-count），位于
   target identity / high risk / revision summary 之前，符合交接 §4.5。
5. 契约更新：TASK_CONTEXT_SCHEMA_VERSION 1.0→1.1；capabilities.taskContext 的
   autoRelevantAssetExpansion=true + relevantAssets 契约块；spec/MCP_SERVER.md 同步。
6. 测试：test_task_context.py 新增 R2.1–R2.10（10 用例覆盖交接 §5 全部要求）。
7. 门禁：Ruff 通过；Python 全量 540/540 通过；真实 Reforge Smoke 复跑验证（S2 返回
   8 条确定性候选，S3 先裁候选再裁 target metadata，身份/风险/修订核心保留）。
8. 文档同步：ROADMAP.md、PROJECT_STATUS.md、spec/MCP_SERVER.md、本计划。
9. 独立本地 Commit（不 Push）。
```

R0.3（Cross-source Correlation，2026-08-16 完成并本地提交）概览：

```text
1. task_context.py 新增 correlation section（schemaVersion 1.1→1.2）：只读、每请求现算、
   零持久化、零模型推断的 Cross-source Correlation，把 Active Work、显式 Change Set、
   Live Editor Session、Memory Evidence 用精确键联接（editorSessionId ↔ sessionId、
   affectedAssets/work assetPaths ↔ Editor dirty/open、work assetPaths ↔ affectedAssets、
   work 文本字段含 changeSetId 字面量、资产 scope Evidence 复用 scoped search_records）。
2. 硬约束落实：不新增 Memory/ChangeSet Schema；只调用 workflow_service.get_change_set()，
   不扫描私有 _change_sets；Change Set 仅显式 change_set_id 且 found 时参与，绝不自动发现；
   不写回 Memory/journal；无 R1 Reference/Impact Analysis。
3. 7 种固定 link kind、固定排序、上限 16 条；受影响资产采样 8、工作项 5、证据检索 ≤12 次，
   summary 如实报告边界计数；无来源可关联时 available=false + reason。
4. 新确定性风险 change-set-editor-session-mismatch（medium，cross-source-correlation 观察事实）。
5. 预算阶梯插入 correlation-links → correlation-summary（先裁关联明细再裁关联摘要），位于
   relevant-assets-count 之后、target metadata 之前；候选/关联永不优先于 target identity、
   high risk、revision summary。
6. 契约：capabilities.taskContext.crossSourceCorrelation（available/deterministic/
   modelInference=false/readOnly/persistent=false/sources/maxLinks=16/changeSetExplicitOnly=
   true/changeSetAutoDiscovery=false）；_project_status_response 同步。
7. 测试：test_task_context.py 新增 R3.1–R3.17（会话匹配/失配、资产交集、Evidence 关联、
   work↔cs 两种链接、无 changeSetId 不产生 cs 链接、降级不伪造、确定性、边界诚实计数、
   非持久化回归、低预算先裁 correlation、requested∩items 去重、work 资产数有界、
   cs 无 editorSessionId 不产生 session 链接）+ MCP capability 契约断言。
8. 门禁：Ruff 通过；Python 全量 557/557（原 540，+17）；git diff --check 通过；无 C++ 变更。
9. 文档同步：ROADMAP.md、PROJECT_STATUS.md、spec/MCP_SERVER.md、审计 Schema 文档、本计划、
   新 Slice 3 Handoff。R0 里程碑标记完成，等待 R1 指令。
```

R1（Impact Analysis，2026-08-18 一次性完成并本地提交，R1 里程碑标记完成）概览：

```text
1. R1.0 复用审计（3 个 Flash 只读审计并行）：现有 find_references 的 incoming 方向、
   depth≤3 遍历、13 种 reference kind 真实取证、Tool Registry / MCP capability / strict
   args / 测试断言的完整同步清单；结论——references_table 行归 consumer 所有、
   target_asset_path 恒定是被引用目标，反向查询可直接复用 references_target_asset_idx，
   不新建第二套引用数据库。
2. 新公共只读 Tool ue_analyze_change_impact（query 组，全模式可用）：1..8 个精确 /Game
   目标、maxDepth 1..3（默认 2）、maxConsumers≤100 / maxEdges≤1000 / maxPaths≤100、
   max_output_tokens 裁剪阶梯；方向契约 consumer → target；BFS 只按精确键分块查询
   （target_asset_path IN frontier，500/批），全局 visited 防环、每 consumer 对每 target
   的 shortestDepth 稳定、Impact Path 可解释（hops=中间 consumer 链）；同 consumer 多
   kind 合并（impactedTargets/referenceKinds/evidence/paths）；自引用不作为 consumer。
3. Reference Kind 确定性归一化 7 类（asset/soft/class/blueprint-symbol/searchable-name/
   parent/unknown-reference），只基于 exporter kind 事实，未知 kind 原样保留绝不猜测。
4. Unknown/Unsupported 一等公民：targets[].found=false + target-not-indexed；结构化
   subject 枚举 8 种，仅 asset-level 与 blueprint-symbol（精确 stable_id）被机械支持，
   其余 6 种显式返回 unsupported-impact-subject；analysisGaps 区分 no-consumer-evidence
   与 unknown-reference-kind / runtime-sensitivity-not-proven / frontier-truncated。
5. validationTargets（Tier 0/1/2 + priorityOrder 确定排序）；确定性 risks（high-fanout
   ≥15 / impact-analysis-truncated / impact-target-not-indexed / unknown-reference-kind）；
   runtimeSensitiveConsumers 固定 not-proven-with-current-evidence，不凭资产类型猜。
6. R0 集成：ue_get_task_context.nextExpansions 增加 impact-analysis-explicit-targets /
   impact-analysis-relevant-asset-hint 渐进入口；默认 Context 不自动遍历 depth≥2 引用图。
7. 测试：tests/python/test_impact_analysis.py 新增 T1–T20 + 域测试（共 33 用例，含
   edge/path-limit 截断、symbol depth-2、多目标混合深度计数、确定性、strict args、
   capabilities 契约）；test_task_context.py 新增 r4_1/r4_2；Tool Registry / MCP 工具
   计数 7/19/40/52/57/69/90/102 全部同步。
8. 真实 Reforge 只读 Smoke（48 资产 immutable 索引，无 UE Editor 进程）：S1 BP_VehicleBase
   depth1（23 direct / 282 edges / 14.4 ms / high-fanout risk）、S2 BP_SphereTraceWheel_V2
   depth2（3 direct / 24 indirect / 27.2 ms / 样本路径 Wheel←VehicleBase←CargoBase）、
   S3 多目标（24 direct / 8 indirect / 共享 consumer 合并 / 32.5 ms）、S4 BP_GM_main
   零消费者边界（5.9 ms，no-consumer-evidence-in-index gap 正确）；索引 SHA-256 前后
   不变、输出完全确定。
9. 门禁：Ruff 通过；Python 全量 592/592（原 557，+35）；git diff --check 通过；无 C++
   变更无需 UE Build。文档同步：spec/MCP_SERVER.md、ROADMAP.md、PROJECT_STATUS.md、
   本计划、新 R1 设计/审计文档。独立本地 Commit（不 Push），停止在 R2 之前。
```

R2（Semantic Diff，2026-08-19 一次性完成，R2 里程碑标记完成）概览：

```text
1. R2.0 evidence audit 完成：确认 Change Set Plan、LiveApply transaction、Persisted Canonical/
   commandlet report 与独立 Verify Canonical 的真实证据边界；verified actual 不复用 Commit report。
2. 新公共只读 Tool ue_analyze_semantic_diff：只接受显式 change_set_id，支持 auto/live/
   persisted/verified、精确 /Game 资产过滤、include_unchanged、max_changes 与 Token 硬预算；
   返回 Expected/Actual/Matched/Unexpected/Missing Expected/Unchanged Critical、Gap、Risk。
3. 四个既有受控域 Adapter 落地：Data Asset scalar/reference/Struct/Array/Set/Map，DataTable
   cell/row-fields/add/remove/rename，Material Instance scalar/vector/texture/static-switch，
   Blueprint variable/component/pin-default；没有扩展 Writer 或任意 UObject/脚本执行能力。
4. 多 Operation、多资产、同路径 operationChain、stable SHA-256 ID、固定排序、Revision
   freshness/stale、显式 truncation 与全局边界均有契约和回归测试；分析对 Change Set 使用深拷贝，
   只读调用不会改写状态。
5. expected no-op 使用独立 noop_* Operation，终态 no-op、validation=no-op、saveState=
   not-required；仅 baseline Canonical Revision 精确匹配 expectedRevision 时形成 persisted evidence，
   不伪造 transaction/receipt/journal/save/verify。
6. R0 仅在显式 Change Set found 时建议 R2；R2 遇到 missing/unexpected 只建议显式调用 R1，
   不自动展开引用图。
7. 真实 UE5.6 DirectHost（不是 Reforge）Smoke 通过：ClosedLoop 四域共 12 个 live/persisted/
   verified 结果全部 expected=actual=matched=1、unexpected=missing=0；Blueprint commandlet 的
   persisted/verified 同样匹配，verified actual 来自独立 full Canonical；fixture/Revision/SQLite
   恢复与清理门禁通过。
8. Ruff 通过，Python 全量 628/628，PowerShell parser、git diff --check、UTF-8/CRLF 与残留
   清理门禁通过；无 C++ 变更，无需 UE Direct Build。文档同步并在本轮本地 Commit（不 Push），
   严格停止在 R3 之前。
```

R3（Verification Plan + Trust Verdict，2026-08-20 一次性完成）概览：

```text
1. R3.0 三路 Evidence Audit 完成：Workflow/Persistence/Semantic、Compile/Validation/Automation、
   R1 Impact/Scope/Registry；Evidence Matrix 与设计见
   AGENT_RELIABILITY_R3_VERIFICATION_TRUST_DESIGN_20260820.md。
2. 新增只读 query Tool ue_build_verification_plan 与 ue_evaluate_trust_verdict；只接受显式
   change_set_id，impact_depth 0..2，exact automation tests/extra validation assets 各≤8，
   max_output_tokens 有界；不接受任意 Assertion/Evidence JSON、项目或数据库路径。
3. 统一 Assertion family：persistence/semantic/freshness/compile/data-validation/
   reference-impact/automation/recovery；requirement、status、applicability、stable ID、固定排序、
   planFingerprint 和 Token 裁剪均为确定性协议。
4. Verdict 固定为 verified/suspicious/failed/insufficient-evidence：Required FAIL 优先 failed；
   Required UNKNOWN/blocking risk 为 insufficient；Recommended unresolved/non-blocking risk 为
   suspicious；其余才是 scoped verified，并始终暴露 unverifiedDimensions。
5. 直接复用 R2 Semantic Diff 与 R1 bounded Impact；reference-sensitive operation 生成 Required
   scope assertion，最多 8 个 direct Blueprint consumer compile；no-op 不制造 Save/Verify。
6. Compile/Validation/Automation 通过固定项目、bounded、persistent=false、arbitraryIngest=false
   的 session-local Evidence Store 捕获；Trust Tool 不自动执行任何 Live Action。
7. R0 仅在显式 Change Set found 时建议 Plan/Verdict；R2 对显式 Change Set建议 Plan，clean
   semantic result 再建议 Verdict；两者都不自动执行 R3。
8. Registry 契约计数更新为 10/22、43/55、60/72、93/105。真实 UE5.6 S1–S5 覆盖四态并完成
   fixture recovery；Ruff 全仓、Python 648/648、PowerShell parser 与 diff/编码门禁通过；本轮停止在 R3。
```

---

## 14. 下一大阶段完成标准

本阶段不是以「R0–R5 全部做完」为唯一完成标准。

第一个可发布/可停节点建议定义为：

```text
Task Context MVP
+ Impact Analysis MVP
+ Semantic Diff MVP
+ 至少一版真实跨域 Agent Benchmark
```

达到这里后必须复盘：

```text
Agent 的 Tool Call 是否减少？
错误资产选择是否减少？
stale/dirty 风险是否更早暴露？
False Success 是否下降？
哪些失败仍然无法解释？
```

只有这些数据能证明继续做 R5、补 Writer、补 Index/Exporter 或优化 Agent UX 是否值得。

---

## 15. 一句话原则

> UEAgentKit 下一阶段不再证明「Agent 能调用更多 UE 操作」，而是把已经完成的读取、Revision、Memory、受控写入和恢复能力组合起来，让 Agent 更准确地理解任务、评估影响，并用证据判断修改结果是否可信。

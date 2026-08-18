# UEAgentKit 0.8.x Context / Analysis / Agent Reliability 执行计划

> 更新时间：2026-08-18
> 当前基线：`feature/agent-reliability@c624bc1`
> 当前状态：R0 里程碑已全部完成。R1 Impact Analysis 已获明确指令，改为一个完整大任务一次性推进：主 Agent 可内部拆分/并行/做 checkpoint，但不中途等待用户逐片确认；完成整个 R1、真实 Reforge Smoke、全量门禁与文档同步后一次性汇报并停止。R2–R5 未开始。
> 建议开发分支：`feature/agent-reliability`（已创建，勿 Push）
> 横向长期分支：`feature/performance-benchmarks`
> 执行方式：按大里程碑推进。R1 当前按单个完整任务执行，详细边界见 `docs/Handoffs/AGENT_RELIABILITY_R1_FULL_HANDOFF_20260818.md`。

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

**主 Agent：DeepSeek Pro**

负责：

- 读取本计划和当前代码；
- 设计接口与数据模型；
- 决定公共协议；
- 实施核心代码或审查子代理实现；
- 控制修改范围；
- 最终运行门禁、审查 Diff、提交；
- 维护本计划状态与交接文档。

**子 Agent：DeepSeek Flash**

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

Flash 子代理默认不得：

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

主 Agent 再决定是否实施。

### 3.3 每个里程碑推荐工作节奏

```text
主 Agent：定义问题与验收标准
    ↓
Flash A：审计现有代码 / 可复用接口
Flash B：审计测试 / 文档 / Registry
    ↓
主 Agent：确定最小设计
    ↓
主 Agent 或 Flash：分块实现
    ↓
Flash：补测试 / 静态审查
    ↓
主 Agent：全量门禁 + Diff Review
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

### 5.0 当前执行指令（2026-08-18）

R1 不再按 Slice 1 / Slice 2 逐片等待用户确认，而是作为**一个完整大任务**一次性完成。主 Agent 可以内部拆分为审计、Direct、Indirect、Domain Evidence、Validation Targets、Budget、Smoke 等工程步骤，也可以使用 DeepSeek Flash 子代理并行做只读审计和测试，但对用户只保留一个 R1 最终验收点。

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

R0.0/R0.1 完成后按交接执行 R0-S（真实 Reforge Context Smoke）+ R0.2（Deterministic Relevant Asset Discovery），2026-08-16 完成并本地提交；随后按新指令完成 R0.3（只读 Cross-source Correlation），R0 里程碑至此完成，R1 等待新的显式指令。

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

只有这些数据能证明继续做 R3/R5 或新增 Writer 是否值得。

---

## 15. 一句话原则

> UEAgentKit 下一阶段不再证明「Agent 能调用更多 UE 操作」，而是把已经完成的读取、Revision、Memory、受控写入和恢复能力组合起来，让 Agent 更准确地理解任务、评估影响，并用证据判断修改结果是否可信。

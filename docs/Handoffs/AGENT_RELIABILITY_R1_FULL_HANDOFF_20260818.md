# UEAgentKit Agent Reliability R1 Full Impact Analysis 接手文档

> 日期：2026-08-18
> 分支：`feature/agent-reliability`
> 起始基线：`c624bc1 feat: add read-only cross-source correlation to task context (R0.3)`
> R0 状态：已完成
> 当前任务：**一次性完成整个 R1 Impact Analysis 里程碑**
> Push：禁止，除非用户明确要求
> 主 Agent：DeepSeek Pro
> 子代理：DeepSeek Flash，可并行做只读审计、测试设计、Smoke 结果整理、文档/契约复核

---

## 1. 执行方式

这次不要按 R1.0 / R1.1 / R1.2 每片停下来等待用户确认。

对用户只有一个大任务和一个最终验收点：

```text
R1 Impact Analysis
→ 全部设计、实现、测试、真实 Reforge Smoke、文档同步
→ 本地 Commit
→ 一次性完成汇报
→ 停止，不进入 R2
```

主 Agent 可以为了工程安全在本地自行做中间 checkpoint commit，也可以让多个 Flash 子代理并行审计低冲突模块；但不得 Push，不得要求用户在 R1 中途逐片确认。

如果中途发现已有 Index 证据不足，不要为了“看起来支持”伪造语义。应继续完成所有能被现有证据可靠支撑的 R1 能力，并把无法证明的部分纳入 `unknown / unsupported / insufficient-evidence` 边界，最终汇报。

---

## 2. R1 最终目标

R1 要回答：

> **如果 Agent 准备修改这些 UE 资产或已知结构化目标，当前项目中哪些对象可能受到影响，为什么被认为相关，以及修改后应该优先验证哪些范围？**

R1 不是另一个 Reference Search 包装器，也不是运行时执行链分析器。

完整目标至少包括：

```text
显式目标
→ Direct Consumers
→ Bounded Indirect Consumers
→ 多目标去重 / Impact Path
→ Reference Kind / Evidence 解释
→ Unknown / Unsupported 边界
→ Validation Targets
→ Deterministic Risks
→ Token / Graph Budget
→ Task Context 渐进式展开入口
```

R1 的核心原则：

> **存在引用 ≠ 已证明会发生语义影响。**

任何输出都不得把静态引用关系表述成“这个消费者一定会坏 / 一定受影响”。

---

## 3. 明确不属于 R1 的内容

本任务禁止扩展到：

```text
R2 Semantic Diff
R3 Verification Plan / Trust Verdict
R4 Agent Benchmark 正式跑分体系
R5 Value Provenance
Blueprint Runtime Exec Trace / Function 调用链语义
PIE / Gameplay Runtime dependency tracing
模型推断 / LLM 排序 / confidence score
新的 Writer / UObject 修改
动画工具扩展
Memory 底层 Schema 扩展
Collaboration / Source Control
```

R1 可以生成 `validationTargets`，但不能声称这些对象已经验证通过；真正的 Verification Plan 属于 R3。

---

## 4. R1.0：先做现有能力审计，但不要停

主 Agent 开始实现前必须先审计并记录复用矩阵，至少检查：

```text
src/ue_agent_kit/queries.py
src/ue_agent_kit/agent_api.py
src/ue_agent_kit/mcp_query_tools.py
src/ue_agent_kit/task_context.py
src/ue_agent_kit/tool_registry.py
src/ue_agent_kit/mcp_server.py
Index references / symbols / searchable-name 相关表与查询
现有 DataTable / Data Asset / Material Instance / Blueprint 读取证据
tests/python 中 reference / query / task_context 相关 Fixture
```

重点确认：

1. `ue_find_references` 已支持的方向、depth、Reference Kind、分页/预算语义；
2. Reverse Reference 图是否已经能直接复用，禁止再造第二套引用数据库；
3. Symbol / Searchable Name / Blueprint Member 等边是否已经进入统一 Reference 图；
4. 哪些 Domain 能提供字段级/符号级确定性证据，哪些只能做到 Asset-level；
5. unresolved / external / truncated reference 当前如何表示；
6. 大引用图已有的 query budget 能复用多少。

审计结果落盘到新的 R1 设计/审计文档，但**审计完成后继续实施整个 R1，不等待用户**。

---

## 5. Public Tool

R1 应新增一个高层只读 Tool：

```text
ue_analyze_change_impact
```

Tool 必须属于只读 Query 能力；原则上 Offline / Live / Workflow 全模式可用，因为核心事实来自 immutable Index。Memory / Live Editor / Workflow 不应成为 R1 基础可用性的必要条件。

### 5.1 请求能力

最终 Schema 由主 Agent在 R1.0 审计后定稿，但至少必须支持：

```text
1..N 个精确 /Game Object Path 目标
bounded maxDepth（至少支持 direct + indirect；建议 1..3，默认 2）
bounded consumer / edge / path 上限
max_output_tokens
```

如果现有 Index 已经可靠支持字段/符号级目标，可增加结构化 change subject / hint；但必须满足：

- kind 固定枚举；
- 参数严格校验，extra reject；
- 只支持能被现有证据机械证明的 kind；
- 不为追求接口完整度发明没有证据的数据。

至少审计并决定是否可靠支持：

```text
asset-level
DataTable Row / Searchable Name
Data Asset Object/Class/Soft Reference
Material Instance Parent / Parameter 相关消费证据
Blueprint Symbol / Default / Component / Pin / Member reference
```

不可靠的类型必须明确标记 unsupported，而不是猜。

---

## 6. Impact Graph 语义

引用方向必须在协议中固定并写清楚：

```text
consumer → referenced target
```

因此 Reverse Reference 分析：

```text
Target T
← Direct Consumer A
← Direct Consumer B
← Consumer C of A
```

其中：

```text
A/B depth=1
C depth=2
```

### 6.1 Direct Consumers

每个 direct consumer 至少应携带：

```text
assetPath
assetClass / assetType（能稳定取得时）
depth = 1
impactedTargets[]
referenceKinds[]
source
whyIncluded
evidence[] / edge identifiers（有稳定 ID 时）
```

同一 Consumer 通过多种 edge 引用同一目标时必须合并，不能重复列多行。

### 6.2 Indirect Consumers

必须支持有界多跳，不允许无限递归。

要求：

```text
maxDepth 硬上限
全局 visited / cycle handling
稳定 BFS 或等价最短路径语义
固定排序
同一 consumer 对同一 target 的 shortestDepth 稳定
Impact Path 可解释
```

一个 consumer 对多个目标产生关系时应去重并保留：

```text
impactedTargets[]
paths / shortest paths
referenceKinds[]
```

不要因为图中有环重复扩张。

### 6.3 Graph Bounds

最终实现必须有硬限制，并在 summary 中如实报告至少：

```text
targetCount
visitedAssetCount
visitedEdgeCount
directConsumerCount
indirectConsumerCount
maxDepthRequested
maxDepthReached
consumerLimit
edgeLimit
pathLimit（如有）
truncated
truncationReasons[]
frontierOmittedCount（能可靠统计时）
```

任何“超过上限”的部分不能静默消失。

---

## 7. Reference Kind 与语义解释

R1 必须把已有 Reference Kind 做一层**确定性解释**，但不能升级成运行时结论。

建议每种边保留：

```text
rawReferenceKind
normalizedReferenceKind
source
```

可以定义稳定 category，例如：

```text
asset-reference
soft-reference
class-reference
blueprint-symbol-reference
searchable-name-reference
parent-reference
unknown-reference
```

最终 category 必须基于现有索引事实，不得根据资产名字或模型推断。

### 7.1 runtimeSensitiveConsumers

ROADMAP 中已有 `runtimeSensitiveConsumers` 目标，但本阶段必须谨慎：

- 只有当前 Index/Reference Kind **已经显式证明运行时消费语义**时才可进入该集合；
- 不允许仅凭 `Blueprint`、`Widget`、`Anim` 等资产类型猜“runtime-sensitive”；
- 无法证明时返回空集合或 `classificationState=not-proven-with-current-evidence`；
- 真正的执行链和值来源属于 R5。

---

## 8. Unknown / Unsupported 必须是一等公民

R1 不能只返回“找到的东西”。必须显式表达分析边界。

至少考虑：

```text
target-not-indexed
reference-kind-unknown
unresolved-reference
external/non-/Game target
truncated-frontier
unsupported-subject-kind
insufficient-domain-evidence
stale-target / unavailable-revision（若 freshness 可用）
```

建议输出：

```text
unknownConsumers[]
unsupportedSubjects[]
analysisGaps[]
risks[]
```

“没有找到 Consumer”和“当前证据无法证明 Consumer”必须区分。

---

## 9. Validation Targets

R1 要把引用图进一步整理成**建议验证范围**，这是它区别于 `ue_find_references` 的核心价值之一。

`validationTargets` 至少包含：

```text
assetPath
reason
depth
impactedTargets[]
referenceKinds[]
source
```

选择规则必须完全确定性。

建议优先级概念：

```text
Tier 0  修改目标本身
Tier 1  Direct Consumers
Tier 2  有界 Indirect Consumers
```

可以通过 `tier` / `priorityOrder` 表达检查顺序，但不要把它描述成模型 confidence。

如果某类 edge 无法证明适合作为 validation target，应保留在 consumer / unknown 中，并说明没有升级为 validation target 的原因。

---

## 10. Deterministic Risk Model

R1 `risks` 只允许从确定性事实产生。

可以包含例如：

```text
high-fanout-target
impact-analysis-truncated
impact-target-stale
impact-target-not-indexed
unknown-reference-kind
unresolved-reference
unsupported-impact-subject
```

风险等级只描述**分析/修改范围风险**，不是“游戏一定会出 Bug 的概率”。

禁止：

```text
likely-to-break
probably-safe
confidence=0.82
modelScore
```

---

## 11. 与 R0 Task Context 的集成

R1 完成后，`ue_get_task_context` 只需要提供**渐进式展开入口**，不要把完整 Impact Graph 塞入默认 Context。

至少做到：

```text
有 explicit targetAssets 时
→ nextExpansions 可建议 ue_analyze_change_impact

只有 relevantAssets 时
→ 可以给出有界 impact-analysis expansion hint
```

不要在一次 `ue_get_task_context` 默认请求中自动 depth=2/3 遍历引用图。

如果 R0 Schema 不需要改变，尽量不变；若必须新增轻量 hint，需同步 Schema/capability/预算契约。

---

## 12. Token Budget 与输出裁剪

R1 结果很容易在大型项目爆炸，必须把预算作为核心协议，不是最后补丁。

要求：

```text
max_output_tokens 有硬范围
估算方式复用 query_protocol / 既有预算设施
固定裁剪阶梯
裁剪原因显式返回
summary / target identity / high risks 优先保留
低优先级 path detail / consumer metadata 先裁
```

建议裁剪优先级：

```text
完整 path edge metadata
→ indirect consumer metadata
→ indirect consumer count/list
→ direct consumer optional metadata
→ validation target optional metadata
```

无论如何优先保留：

```text
target identity
summary counts
truncated=true/false
truncationReasons
high/medium deterministic risks
至少可继续展开的 nextActions
```

---

## 13. 性能与算法边界

R1 不要求在本任务里合并 `feature/performance-benchmarks`，但必须做最小性能 sanity。

要求：

- 不允许 N×全表扫描式实现；
- 复用现有 references 索引/查询；
- 图遍历复杂度必须受 node/edge/depth 上限约束；
- 多目标查询应尽量批量/去重，避免对同一节点反复查询；
- Smoke 中记录至少 depth=1 / depth=2 的实际耗时、visited nodes/edges、输出 token 数；
- 不在本阶段设脱离机器环境的硬毫秒阈值，但若出现明显 O(N²) 或重复查询，应先修再验收。

R1 完成后把建议的正式性能指标同步给 `feature/performance-benchmarks` 文档，但不要在性能分支修改公共协议。

---

## 14. 单元/契约测试要求

测试数量不设形式目标，但必须覆盖完整行为边界。

至少包含：

```text
T1  单目标，无 consumer
T2  单目标，一个 direct consumer
T3  多 direct consumer，固定排序
T4  同 consumer 多种 reference kind 合并
T5  多目标，共享 consumer 去重 + impactedTargets
T6  depth=2 indirect consumer
T7  graph cycle 不重复/不无限扩张
T8  shortestDepth / path 稳定
T9  maxDepth 边界
T10 consumer/edge/path limit 截断与诚实计数
T11 target 不存在 / 未索引
T12 unknown/unresolved reference 不伪造语义
T13 unsupported structured subject 明确返回
T14 validationTargets 固定排序与原因
T15 runtimeSensitive 未被证据证明时不能靠资产类型猜
T16 低 token budget 裁剪顺序
T17 相同输入输出完全确定
T18 Tool Registry / MCP capabilities / strict args
T19 R0 nextExpansion 集成
T20 Offline/Live/Workflow 模式可用性一致
```

如果 R1.0 审计确认 DataTable / Searchable Name / Blueprint Symbol / Data Asset / MI Parent 等现有 Fixture 能证明更细粒度关系，继续补各 Domain 回归测试。

---

## 15. 真实 Reforge Smoke

R1 完成前必须至少跑一次真实 Reforge 只读 Smoke。

优先复用现有：

```text
config/projects/reforge-read.json
Output/ReforgeContextSmoke/ 已有只读索引/物证（如仍适用）
```

不要修改 Reforge 资产。

至少找并执行以下真实 Case：

### S1：Fan-out Direct Case

选择一个已有多个 Reverse Reference 的真实资产（可优先考察 `BP_VehicleBase`，但最终以索引事实为准）：

```text
depth=1
→ 验证 directConsumers / referenceKinds / validationTargets
```

### S2：Indirect Case

选择能形成至少 2 跳路径的真实目标：

```text
depth=2
→ 验证 shortest path / 去重 / 无环扩张
```

如果当前 48 资产 logic profile 不存在真实 2-hop Case，应如实记录，不得人工宣称成功；可以在只读、安全前提下扩大导出范围，或使用可证明的集成 Fixture 补行为验证。

### S3：Multi-target Case

两个真实目标一起分析：

```text
→ 验证共享 consumer 合并 / impactedTargets / 输出有界
```

### S4：No-consumer / Boundary Case

选择一个无 Reverse Consumer 或证据很少的真实资产：

```text
→ 验证“没有发现”与“证据不足”表达正确
```

Smoke 至少记录：

```text
input
summary
direct/indirect counts
visited nodes/edges
truncated
token estimate
elapsed time
关键输出片段
```

若需要重新通过 UnrealEditor-cmd 做只读导出，Reforge 已知 Blueprint 编译错误不应被误判为 R1 新回归；以导出 Success/Failure、退出状态和资产零修改为准。临时 plugin junction 必须清理。

---

## 16. 是否允许改 Index / Exporter

默认：**优先不改。**

R1 应建立在现有 Reference / Symbol / Searchable Name 索引之上。

只有满足以下条件时才允许做最小只读 Index/Exporter 扩展：

1. R1.0 明确证明某个 Roadmap 已承诺的现有支持域已有可靠事实，但当前 Query 层无法访问；
2. 扩展是确定性的只读事实，不是运行时推断；
3. 不引入新的 Writer；
4. 同步 Schema / migration / exporter / fixture / tests；
5. 若有 C++ 变更，必须跑 UE5.6 Direct Build + 对应真实 Smoke。

如果为了支持某类“影响”必须做运行时执行链、PIE 或复杂 Value Provenance，则不要扩 Index；明确归入 R5。

---

## 17. Public Capability / Tool Registry / Docs

R1 完成后至少同步：

```text
tool_registry.py
mcp_server.py capabilities
ue_get_project_status 对应 capability
server instructions
spec/MCP_SERVER.md
docs/ROADMAP.md
docs/PROJECT_STATUS.md
docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md
```

建议 capability 明确暴露：

```text
available
readOnly
deterministic
modelInference=false
maxDepth
maxTargets
maxConsumers / maxEdges
supportsIndirect
supportsValidationTargets
supportsRuntimeSensitivityClassification（若有严格证据才 true）
```

Tool 数量契约必须同步测试。

---

## 18. 工程门禁

R1 最终验收至少执行：

```text
G1  git status / diff 审计
G2  Ruff（src + tests + 新脚本）
G3  Python 全量测试
G4  Tool Registry / MCP capability / strict schema tests
G5  JSON Schema / migration tests（如受影响）
G6  UE5.6 Direct Build（仅有 C++ 变更时）
G7  真实 Reforge R1 Smoke
G8  git diff --check
G9  UTF-8 无 BOM + CRLF
G10 文档状态同步
G11 本地 Commit
```

持续约束：

- 不 Push；
- 不 Reset / Stash / Rebase / Force；
- 不提交 Output / Build / Backups / Saved / Intermediate / 测试生成资产；
- 不修改 Reforge 正式资产；
- 不因为方便开放脚本/UObject 任意执行；
- 不进入 R2。

---

## 19. 子代理推荐拆分

主 Agent 可以一次性把 R1 当大任务，但建议并行委派 Flash 做只读工作：

### Flash A：Reference Graph 审计

```text
现有 find_references 方向/depth/edge kind
queries/index schema 可复用点
cycle / pagination / budget 风险
```

### Flash B：Domain Evidence 审计

```text
DataTable / Searchable Name
Data Asset refs
Material Instance Parent/Parameter
Blueprint Symbol/Member/Pin
哪些能可靠进入 R1，哪些必须 unknown/unsupported
```

### Flash C：测试/协议审计

```text
Tool Registry / MCP capability
严格参数
测试边界缺口
预算裁剪/确定性检查
```

### Flash D：Reforge Smoke 结果整理

只读分析原始 JSON，整理路径、计数、性能和边界；主 Agent 必须交叉核对原始物证。

公共 Schema、最终算法和提交必须由 Pro 主 Agent 决定。

---

## 20. R1 完成定义

只有同时满足以下条件，才能把 R1 标记完成：

```text
[ ] ue_analyze_change_impact 公共 Tool 完成
[ ] Direct Consumers 完成
[ ] Bounded Indirect Consumers 完成
[ ] 多目标去重 + Impact Path 完成
[ ] Reference Kind 确定性解释完成
[ ] Unknown / Unsupported 边界完成
[ ] Validation Targets 完成
[ ] Deterministic Risks 完成
[ ] Graph / Token Budget 完成
[ ] R0 渐进展开入口完成
[ ] 全量 Python / 契约门禁通过
[ ] 必要时 UE Build 通过
[ ] 真实 Reforge Smoke 完成
[ ] 文档同步完成
[ ] 本地 Commit 完成
[ ] 未进入 R2
```

如果 `runtimeSensitiveConsumers` 因现有证据不足不能可靠实现，可保留明确的 `not-proven-with-current-evidence` 能力状态；**这不应通过启发式猜测强行补齐。**

---

## 21. 最终汇报格式

R1 全部完成后一次性向用户汇报：

```text
1. Commit / Branch / 工作树 / 是否 Push
2. R1.0 复用审计结论
3. ue_analyze_change_impact 最终 Request/Response Schema
4. Direct / Indirect 图遍历算法与硬边界
5. Reference Kind / Domain Evidence 最终支持矩阵
6. Validation Targets 规则
7. Unknown / Unsupported / Risks 语义
8. R0 Task Context 集成方式
9. Tool / Capability / Count 变化
10. 单元测试与全量门禁
11. 真实 Reforge S1–S4 Smoke 结果
12. 性能 / Token / 大图截断观察
13. 已知限制与明确延后到 R2/R5 的内容
14. 是否建议进入 R2，以及依据
```

完成汇报后停止，等待用户是否开启 R2。

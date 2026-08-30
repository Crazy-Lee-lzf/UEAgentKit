# UEAgentKit Agent Reliability R0 Slice 2 Handoff

> 日期：2026-08-16
> 分支：`feature/agent-reliability`
> 当前基线：`876997d feat: add ue_get_task_context task context aggregation (R0.0/R0.1)`
> 状态：R0.0 / R0.1 已完成；本交接只负责真实 Reforge Smoke + R0.2 最小相关资产发现。完成后停止，不进入 R0.3 / R1。
> 推送约束：只允许本地 Commit，不 Push。

---

## 1. 本片目标

R0.1 已证明 `ue_get_task_context` 的聚合契约成立，但尚未用真实 Reforge 任务验证默认输出是否真正适合 Agent 使用。

本片分两步：

```text
R0-S  Real Reforge Context Smoke
  ↓  只有 Smoke 证明 query-only / explicit-target 的真实输出边界后
R0.2  Deterministic Relevant Asset Discovery
```

核心问题：

```text
1. 4096 token 默认预算下，真正有决策价值的信息是否被保留？
2. explicit assetPaths 时，一次 Task Context 是否足够开始分析？
3. query-only 时，现在缺失哪些最小 Index 候选？
4. R0.2 应该返回哪些候选，才能减少 Tool Call 而不制造噪声？
```

不要用“Tool 能成功返回”作为 Smoke 通过标准。

---

## 2. 执行模型

推荐：

```text
DeepSeek Pro：主 Agent
DeepSeek Flash A：只读执行 Smoke / 整理原始结果
DeepSeek Flash B：审计候选搜索可复用 API / 测试缺口
DeepSeek Pro：决定 R0.2 最小规则、审查实现、跑门禁、提交
```

Flash 不得独立决定公共 Schema，不得扩大到 R1 Impact Analysis。

---

## 3. R0-S：真实 Reforge Context Smoke

### 3.1 前置

使用真实 Reforge：

```text
E:\WorkSpace\Reforge\Reforge.uproject
```

优先复用已有 Reforge Index / Memory / Live Editor 配置；若某源当前不可用，允许降级，但必须记录 `degradedSources`，不要为了 Smoke 临时造假数据。

不要修改任何 Reforge 资产。

### 3.2 Case S1：明确目标资产

从现有 Index 中选择一个真实、已索引、非动画的资产。优先 Blueprint / Data Asset / Material Instance / DataTable；不要为本 Smoke 回到动画域。

调用：

```text
ue_get_task_context(
  query=<与该资产相关的真实开发问题>,
  asset_paths=[<exact asset path>],
  include_memory=true,
  include_live_context=true,
  max_output_tokens=4096
)
```

检查：

```text
targetAssets 是否足够识别目标
revisionState 是否可解释
memory 是否相关而非纯噪声
liveEditor 是否占用过多预算
risks 是否只有确定性风险
nextExpansions 是否真的可执行
outputBudget 是否截断，截断后保留了什么
```

### 3.3 Case S2：同一任务，不提供 asset_paths

使用与 S1 相同 query：

```text
asset_paths=[]
```

记录当前 R0.1 的真实缺口。预期 `targetAssets=[]` / `relevantAssets=[]`，但不要预设 R0.2 一定要把所有 Search 结果塞回来。

重点回答：

```text
为了让 Agent 下一步知道“应该看哪些资产”，最少需要几个候选？
候选仅按 Asset Search 是否足够？
是否需要 Symbol Search？
哪些字段最有用？
```

### 3.4 Case S3：低预算

对 S1 再跑：

```text
max_output_tokens=1024
```

验证固定裁剪顺序是否合理。

必须特别检查：

```text
risks / target identity / revision summary
```

是否在低预算下仍优先于 Live Editor 大段摘要、Memory 详情和 nextExpansions。

### 3.5 Smoke 记录

新增一份只包含结构化观察的文档：

```text
docs/Plans/AGENT_RELIABILITY_R0_REAL_CONTEXT_SMOKE_20260816.md
```

至少记录：

```text
Case
Input
Mode / enabled sources
estimatedTokens
truncated + truncationReason
riskSummary
useful sections
noisy sections
missing information
next Tool calls that Agent would still need
```

不要记录大段原始 JSON；只保留能支持设计决策的字段和结论。

### 3.6 R0-S 停止条件

如果发现以下任一问题，先修 R0.1，不进入 R0.2：

```text
默认 4096 token 经常无法保留 target/revision/risk 核心字段
Live / Memory 某一来源失败会拖垮整体
risks 出现非确定性推断
explicit asset path 无法稳定定位真实资产
响应明显超过 budget 且无法解释
```

---

## 4. R0.2：Deterministic Relevant Asset Discovery

只有 R0-S 通过后实施。

### 4.1 目标

让 query-only 或 query + explicit targets 的 Task Context 提供一个小型、确定性、可解释的 `relevantAssets` 候选集。

R0.2 不是 Impact Analysis。

它只回答：

> “根据当前 immutable Index，哪些资产最值得作为下一步分析候选？”

### 4.2 必须复用现有搜索

优先审计并复用：

```text
IndexQueryService / Agent API 已有 Asset Search
Symbol Search
现有 query_protocol 排序 / 分页 / budget
```

禁止新增第二套 FTS / 搜索数据库。

禁止 Server 调 LLM 做候选排序。

### 4.3 最小候选规则

第一版建议：

```text
explicit assetPaths
  → 始终只属于 targetAssets，不重复塞入 relevantAssets

query
  → Asset Search 候选
  → 必要时少量 Symbol Search 补充
  → 去重
  → 固定排序
  → 有界 Top N
```

Top N 建议从真实 Smoke 决定；没有数据时上限不得超过 8。

每个 `relevantAssets[]` 至少包含：

```text
assetPath
assetClass / assetType（能证明时）
source
whyIncluded
matchKind
```

如果现有搜索提供稳定 score/rank，可以透传；不要伪造 confidence。

### 4.4 不允许在 R0.2 做的事

```text
Reference depth=2/3 自动遍历
自动影响分析
自动判断“一定受影响”
模型推断
Value Provenance
Blueprint Exec Trace
Memory Schema 修改
新的 UE C++ Reader
Writer / Patch / Save
动画工具扩展
```

引用关系仍通过 `nextExpansions` 或后续 R1 展开。

### 4.5 与预算的关系

`relevantAssets` 必须是默认 Context 的小型候选摘要。

如果预算不足：

```text
先裁 candidate metadata
再减少 candidate count
```

但不得为了保留候选而删除 target identity / high risk / revision summary 等更高优先级信息。

---

## 5. R0.2 测试

在现有 `test_task_context.py` 基础上增加，不另造完整平行测试框架。

至少覆盖：

```text
1. query-only 返回稳定 relevantAssets
2. explicit target 不在 relevantAssets 重复
3. 无搜索结果 → []，不是错误
4. 相同输入排序确定
5. Top N 有界
6. Symbol 命中与 Asset 命中去重
7. candidate source / whyIncluded / matchKind 完整
8. 低 budget 能减少候选但保留核心风险/目标
9. Memory / Live disabled 不影响候选发现
10. 搜索子源异常时按既有错误模型处理，不把猜测结果伪装成事实
```

若候选发现只使用 immutable Index，则不需要 UE5.6 Editor Smoke；真实 Reforge query Smoke 仍必须保留。

---

## 6. 文件范围建议

优先涉及：

```text
src/ue_agent_kit/task_context.py
src/ue_agent_kit/agent_api.py              # 只有确需复用/暴露已有查询能力时
src/ue_agent_kit/mcp_server.py              # 仅 capability/schema 变化时
tests/python/test_task_context.py
docs/Plans/AGENT_RELIABILITY_R0_REAL_CONTEXT_SMOKE_20260816.md
docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md
docs/PROJECT_STATUS.md
docs/ROADMAP.md
```

R0.1 的 `task_context.py` 已经很大。R0.2 如果需要较多候选发现逻辑，允许抽一个小型纯 Python helper/service 文件；不要为了“保持单文件”继续无限增长，也不要过度抽象公共框架。

---

## 7. 门禁

R0-S：

```text
真实 Reforge read-only Smoke
无资产写入
结果记录完成
```

R0.2：

```text
Ruff
Python 全量测试
Tool Registry / MCP 契约（仅受影响时）
git diff --check
文档同步
本地 Commit
```

有 C++ 变更才跑 Direct Plugin Build；本片原则上不应需要 C++。

---

## 8. 完成汇报格式

完成后必须停止，并按以下格式汇报：

```text
1. Commit / Branch
2. R0-S 三个真实 Case 结果
3. 4096 / 1024 token 下的裁剪结论
4. R0.2 relevantAssets 最终候选规则
5. 复用的现有 Search API
6. 新增/修改 Tool/API（如有）
7. 测试与门禁
8. Tool Call 减少：实测与推导必须分开
9. 已知限制
10. 是否建议继续 R0.3，或先进入 R1
```

不要自动开始 R0.3 / R1。

---

## 9. 当前决策原则

> R0.2 的价值不是“自动找更多东西”，而是在不引入模型猜测、不展开大引用图的前提下，让 query-only Task Context 能给 Agent 一个小而可信的下一步候选集。

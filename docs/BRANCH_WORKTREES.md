# Branch / Worktree 协作规范

更新时间：2026-08-15

## 1. 当前状态

当前本地仓库主线：

```text
E:/WorkSpace/UEAgentKit
    branch: main
    baseline: 22632c7
    status: clean
    origin/main: 本地领先 57 commits
```

当前仍可见的长期/历史分支：

```text
feature/live-editor-realtime-io
    Realtime Animation Tools 历史开发线
    已被 main 完整包含，不再承载新开发

feature/performance-benchmarks
    长期横向性能基准线
    保留

origin/feature/memory-context
    远端历史 Memory/Context 开发线
    Memory 基础已进入 main，不再恢复为长期本地主线
```

Realtime Animation Tools 已完成并合入 `main`，动画功能扩展暂缓。

---

## 2. 推荐的新分支结构

下一阶段采用精简结构：

```text
main
├─ feature/agent-reliability
└─ feature/performance-benchmarks
```

### main

稳定集成线。接收已经达到独立里程碑、测试通过、文档同步的提交。

### feature/agent-reliability

当前唯一产品主开发线，负责：

```text
Task Context / Context Pack
Impact Analysis
Semantic Diff
Verification Plan / Trust Verdict
Real Agent Benchmark
Value Provenance / Execution Trace（由数据决定）
```

详细计划：

```text
docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md
```

### feature/performance-benchmarks

长期横向性能线，负责：

- 大型项目 Index / Search / Reference / Memory 基准；
- Task Context / Impact Analysis 等新主线能力的 p50/p95；
- 测试数据生成与容量门禁；
- 不独立定义公共协议。

---

## 3. 不再采用「每个功能一个长期分支」

不要为以下里程碑分别建立长期分支：

```text
feature/context-pack
feature/impact-analysis
feature/semantic-diff
feature/verification-plan
feature/agent-benchmark
```

这些能力共享 Index、Memory、Evidence、Tool Registry、MCP Server、测试和文档，长期拆开会增加公共协议漂移和合并成本。

R0–R5 默认都在 `feature/agent-reliability` 上按**独立 Commit / 独立里程碑**推进。

只有以下情况允许短期实验分支：

1. 高风险设计可能整体推翻；
2. 两个主 Agent 需要真正并行修改低冲突模块；
3. 性能实验会污染主功能代码。

短期实验分支应尽快合并/挑选提交或删除，不转成长维护线。

---

## 4. 本地 Agent 创建新主线的要求

本地 Agent 开始下一阶段时：

1. 先确认 `E:/WorkSpace/UEAgentKit` 的本地 `main` 工作树干净；
2. 必须从**本地最新 main** 创建 `feature/agent-reliability`；
3. 不得从 `origin/main` 创建，因为当前远端落后本地 57 commits；
4. 如需独立 Worktree，再为该分支建立新的工作目录；
5. 不 Push，除非用户明确授权。

概念步骤：

```text
local main@latest
→ feature/agent-reliability
→ optional dedicated worktree
→ R0 milestone commits
```

本规范只规定流程，不要求自动删除现有 `feature/live-editor-realtime-io` Worktree。删除旧 Worktree/branch 前必须再次确认：

```text
main 已包含该分支全部提交
旧 Worktree 无未提交修改
无脚本/IDE 配置仍依赖该路径
```

---

## 5. 公共协议归属

以下内容只允许有一套定义：

```text
Project / Asset Identity
Revision / Freshness
Task Context
Change Set
Evidence
Operation Result Envelope
Error Model
Token / Result Budget
Verification / Trust Verdict 公共结构
```

下一阶段这些公共协议首先在 `feature/agent-reliability` 中形成完整纵向切片，达到门禁后合入 `main`。

`feature/performance-benchmarks` 随后同步 `main`，只添加 benchmark harness / measurements，不应维护另一套协议。

---

## 6. Agent 并行方式

推荐：DeepSeek Pro 作为主 Agent，Flash 作为子代理。

主 Agent负责：

- 公共接口与数据模型；
- 任务拆分；
- 核心实现与最终 Review；
- 门禁和 Commit。

Flash 子代理适合：

- 代码搜索/现状审计；
- 测试缺口；
- 小范围实现；
- Tool Registry / 文档同步检查；
- Benchmark Case 执行与结果整理。

不要让多个子代理同时独立修改公共 Schema、Tool Registry 和 MCP Server 后再强行合并。

---

## 7. 里程碑同步规则

`feature/agent-reliability` 每完成一个可使用的 R0/R1/R2... 纵向切片即可形成本地 Commit，不要求把整个 0.8.x 一次做完。

候选合入 `main` 前至少满足：

```text
Ruff
Python 全量测试
JSON Schema / Tool Registry 契约（如受影响）
UE5.6 Direct Build（有 C++ 变更）
真实 UE5.6 Smoke（涉及 Live Editor）
git diff --check
相关文档同步
```

当前 standing constraint：

- 本地 Commit 可以执行；
- 禁止 Push，除非用户明确要求；
- 禁止 Reset / Stash / Revert 当前工作；
- 不提交 Build / Output / Backups / Intermediate / Saved / 日志 / 测试生成资产。

---

## 8. 当前开发优先级

```text
主线：feature/agent-reliability
  R0 Task Context MVP
  R1 Impact Analysis
  R2 Semantic Diff
  R3 Verification / Trust Verdict
  R4 Real Agent Benchmark
  R5 数据驱动的深层 Analysis

横向：feature/performance-benchmarks

冻结：
  Animation Writer 扩展
  Generic Writer coverage race
  Collaboration / Source Control
  Memory 底层 Schema 扩展
```

多人协作继续放在 0.9.0 或更后，等单 Agent 的 Context、分析和可信结果判断有真实数据后再启动。

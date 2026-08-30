# UEAgentKit Agent Reliability R0 接手文档

> 日期：2026-08-15
> 当前稳定基线：`main@22632c7`
> 下一主线：`feature/agent-reliability`
> 当前任务：R0 Task Context / Context Pack MVP 第一条纵向切片
> 不要求一次完成整个 0.8.x；本轮完成 R0 第一里程碑后停止并汇报。

---

## 1. 先读

按顺序读取：

```text
docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md
docs/ROADMAP.md
docs/PROJECT_STATUS.md
docs/BRANCH_WORKTREES.md
docs/MEMORY_ARCHITECTURE.md
docs/AI_NATIVE_UE_EDITOR.md
```

动画 P0–P8 已完成并合入 `main`，本阶段暂缓动画 Writer 扩展，不继续 Additive Batch、Composite Mutation 或 Retarget 一键桥接。

---

## 2. 分支

当前本地 `main` 比 `origin/main` 新，禁止从远端旧基线创建新分支。

开始前：

```text
确认 E:/WorkSpace/UEAgentKit 的 main 工作树 clean
确认 HEAD 至少包含 22632c7
从本地最新 main 创建 feature/agent-reliability
如需要，再建立独立 worktree
```

不要 Push。

不要删除旧 Worktree/branch，除非用户另行要求。

---

## 3. Agent 编排

推荐：

```text
DeepSeek Pro = 主 Agent
DeepSeek Flash = 子代理
```

主 Agent负责架构、公共接口、最终代码审查、门禁、Commit。

可以并行委派两个 Flash 子代理：

### Flash A：现有 Context 能力审计

只读检查：

```text
Index/Search API
Asset/Symbol/Reference 查询
Revision/Freshness
ue_get_asset_state（如存在）
Token / Result Budget
```

输出：已有接口、可直接复用方法、缺口、禁止重复实现项。

### Flash B：Memory / Live / Workflow 审计

只读检查：

```text
ue_memory_get_context
Active Work
Evidence
ue_get_editor_context
Dirty/Open Assets
Change Set
Editor Session
```

输出同上。

子代理先交审计结果，不直接设计第二套 Schema。

---

## 4. 本轮目标

只完成：

# R0.0 现状审计 + R0.1 `ue_get_task_context` 最小纵向切片

不要尝试一次完成自动 Context Pack 全功能。

第一版建议请求：

```text
query                  必填
assetPaths             可选
workItemId             可选
changeSetId            可选
includeLiveContext     默认 true
includeMemory          默认 true
maxChars / budget      有界
```

第一条纵向切片只要求：

```text
query + explicit assetPaths
→ target asset/index facts
→ revision/freshness
→ optional Memory summary
→ optional Live Editor summary
→ dirty/stale/conflict risk summary
→ bounded response
```

暂时不要做：

```text
自动多跳相关资产扩展
深度 Impact Analysis
Value Provenance
Execution Trace
模型推断 / Server 内 LLM 总结
写入
```

---

## 5. Response 原则

建议结构：

```text
TaskContext
├─ request
├─ project
├─ targetAssets
├─ relevantAssets        第一版可以为空/仅显式目标
├─ memory
├─ activeWork
├─ liveEditor
├─ revisionState
├─ changeSet
├─ risks
└─ nextExpansions
```

任何内容都应尽可能说明：

```text
source
revision / freshness
whyIncluded
availability
```

第一版 `risks` 只做确定性事实，例如：

```text
target dirty
index stale
Memory stale/conflicted
Editor/disk revision mismatch
changeSet state incompatible
```

禁止把模型猜测混成事实。

某一来源不可用时，应降级 section，而不是让整个 Task Context 失败。例如 Offline 模式应仍可返回 Index facts，只把 Live Editor 标成 unavailable。

---

## 6. 实现顺序

主 Agent 在收到两个 Flash 审计后：

```text
1. 列出复用矩阵
2. 确定最小 Request/Response Schema
3. 先加契约/单元测试
4. 实现服务层聚合
5. 注册 MCP Tool
6. 更新 Capability / Tool Registry（如设计需要）
7. 覆盖 Memory disabled / Live disabled 降级
8. 覆盖 budget 截断
9. 跑全量门禁
10. 更新文档
11. 本地 Commit
12. 停止并汇报，不自动开始 R1
```

优先 Python orchestration；除非审计明确证明现有 C++ / Bridge 无法提供必要事实，否则 R0 第一片不要新增 C++。

---

## 7. 必须测试的场景

最低：

```text
T1  query + 单个 explicit asset
T2  多个 explicit assets
T3  Memory disabled
T4  Live Editor disabled
T5  target Dirty
T6  stale Revision / stale Memory
T7  changeSetId valid
T8  changeSetId invalid
T9  maxChars / budget 截断
T10 一处 optional source 失败时其他 sections 仍返回
```

如果现有接口语义不允许安全构造某个场景，记录原因，不要为了测试修改生产安全边界。

---

## 8. 门禁

提交前：

```text
git status / diff review
Ruff
Python 全量测试
JSON Schema（如受影响）
Tool Registry / MCP counts（如受影响）
UE5.6 Direct Build：仅有 C++ 变更时
真实 UE Smoke：仅确实增加/改变 Live Editor 行为时
git diff --check
相关 docs 更新
```

只本地 Commit，不 Push。

---

## 9. 本轮禁止范围

不要顺手实现：

```text
R1 Impact Analysis
R2 Semantic Diff
R3 Trust Verdict
R4 Benchmark Runner
Animation Writer 新功能
Blueprint Graph 通用写入
Level Actor CRUD
Collaboration / Source Control
Memory Schema v4
任意 Python / Console / UObject Method
```

如果审计发现这些能力会影响 R0，记录成后续依赖，不在本轮扩张。

---

## 10. 完成后汇报格式

完成 R0 第一片后只汇报：

```text
1. Commit hash / branch
2. 新增 Tool / API
3. TaskContext Request/Response 最终结构
4. 复用了哪些现有模块
5. 哪些来源支持降级
6. 测试数量与门禁结果
7. 真实使用一个示例任务时，原本需要哪些 Tool Call，现在减少到什么程度
8. R0 尚未做的部分
9. 是否建议继续 R0.2，及理由
```

不要默认开始 R1。

---

## 11. 当前阶段成功标准

本轮不是看 Tool 数量。

成功标准是：

> 对一个已知目标资产的真实 UE 开发任务，Agent 能用一次 `ue_get_task_context` 获得足够开始分析的最小事实集，并明确看到 Revision、Memory、Live Editor 和 Dirty 风险；不需要自己手工拼接多套底层上下文，也不会把 stale/缺失信息伪装成当前事实。

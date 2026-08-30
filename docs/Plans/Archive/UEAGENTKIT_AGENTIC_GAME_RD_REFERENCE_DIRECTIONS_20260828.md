# UEAgentKit Agentic Game R&D Reference Directions

> 日期：2026-08-28
>
> 性质：独立参考文档，不修改当前 ROADMAP / Post-0.8 执行计划，不改变 W0-W5 / P1-P5 / Agent UX / Maintainability 的既定优先级。
>
> 当前读取基线：`feature/agent-reliability@9917c0a`；主仓存在既有未跟踪文件，本文件不触碰其内容。

## 1. 结论

当前 UEAgentKit 已经明显强于普通“UE MCP 工具集合”：已有 Policy / Revision / Plan / Transaction / Save / Independent Verify / Semantic Diff / Trust / Recovery，并且 Post-0.8 已把 Editor-resident Writer 与低延迟连续修改列为最高优先级。

下一阶段最值得参考的，不是继续追求 Tool 数量，而是把现有可靠执行层逐步升级成：

```text
Observe
→ Hypothesis
→ Plan
→ Apply / Experiment
→ Runtime / Editor / Profiler Evidence
→ Verify
→ Engineering Fact / Decision
→ Next Plan
```

即从“AI-safe Unreal Tooling”逐步走向“Game R&D Agent”。

## 2. 可直接借鉴 Danus 的部分

Danus 最值得迁移的不是数学本身，而是三件事：

### 2.1 Fact Graph → Engineering Evidence Graph

不要只保存聊天历史或最终结论，而是保存可追踪关系：

```text
Observation
Hypothesis
Change / Experiment
Evidence
Verified / Rejected Fact
Decision
Dependency
```

例如：

```text
GameThread spike
→ 怀疑某类 Actor Tick
→ 修改 TickInterval
→ 固定 PIE benchmark
→ 11.2 ms → 8.9 ms
→ 假设被支持
```

这与当前 Memory 的 `user-confirmed / tool-observed / model-inferred`、Revision stale、Task Record、Runtime Evidence 很契合，因此不建议新造一套平行 Memory；后续应优先研究“如何把已有 Evidence / Change Set / Verification / Benchmark 组成依赖图”。

### 2.2 Planner / Worker / Verifier 分权

UE5 中可以对应为：

```text
Lead / Planner
├─ Code Worker
├─ Blueprint / Asset Worker
├─ Runtime Worker
└─ Performance Worker
       ↓
    Verifier
```

但 Verifier 应尽量由真实 Oracle 驱动，而不是另一个 LLM：

- C++ → UBT / compiler
- Blueprint → Blueprint Compile
- Asset → resident read-back + independent reload
- Gameplay → PIE / Automation / replay
- Performance → Unreal Insights / stat / benchmark
- Persistence → Revision / SHA / canonical export

这与当前 R3 Trust / W2 Fast Verify / W3 Strong Verify 的方向一致，应继续强化，而不是另起“LLM Judge”。

### 2.3 长任务不依赖聊天上下文

后续 Agent 应按任务只加载相关：

- 当前资产 / Revision；
- 已验证 Engineering Facts；
- 相关 Design Decisions；
- Known Issues；
- 最近 Benchmark；
- 当前 Change Set / Active Work；
- 与当前 Hypothesis 有依赖关系的 Evidence。

这比继续扩大默认 Context Pack 更重要。

## 3. Tool 策略：基础 Primitive + 按需生成 Tool

当前 Post-0.8 明确“不以 Tool 数量为主目标”是正确的，但不等于停止增加 Tool。

建议长期分为三类：

```text
Core Tool
Generated Tool
Project Tool
```

### Core Tool

高频、危险、必须强契约：

- read / inspect
- plan / apply / save / rollback
- compile
- PIE control
- runtime inspect
- log
- trace / benchmark
- verify

这些必须由 UEAgentKit 长期维护。

### Generated Tool

某次研究任务缺少探针时，由 Agent 生成受限 Python / C++ / Editor 辅助工具，例如：

```text
“统计当前关卡所有 SkeletalMeshComponent 的 PhysicsAsset / collision 配置”
```

现有 Tool 不适合时：

```text
Capability Gap
→ Generate
→ Static Check
→ Sandbox / Test Fixture
→ Declare Permissions
→ Execute
→ Verify Result
```

### Project Tool

Generated Tool 被重复证明有价值后，可晋升为项目长期能力，而不是立刻进入公共 MCP surface。

这样可以避免公共 Tool Registry 无限膨胀，同时解决真实项目长尾需求。

## 4. UE5 特别值得补的观察通道

Writer 之后，不应只继续扩 Writer。AI 要真正“自己调试”，至少需要以下观察面。

### 4.1 Runtime Inspect

优先级较高：

- Actor / Component live state
- Pawn / Controller
- Blueprint runtime variables
- AI Controller / Blackboard / Behavior Tree
- Gameplay state
- Physics state
- replication / network state（后置）

目标不是开放任意 UObject 调用，而是建立 bounded inspect contract。

### 4.2 PIE Control

最小闭环：

```text
Start PIE
→ wait for known state
→ inspect / exercise scenario
→ collect logs/evidence
→ Stop PIE
```

以后才考虑更复杂的自动输入、路径执行和场景脚本。

### 4.3 Unreal Insights / Profiling

这是 Game R&D Agent 与普通 Coding Agent 差异最大的部分之一。

建议未来形成：

```text
Capture Trace
→ bounded query
→ identify spike / expensive scope
→ compare baseline
→ Experiment
→ recapture
→ verified performance delta
```

不要让 LLM直接解析完整巨大 trace；优先做结构化查询层和固定 benchmark contract。

### 4.4 Source Debugger Adapter（后置）

Visual Studio / LLDB / WinDbg 的 breakpoint、call stack、locals、watch 可以成为高级通道，但优先级应低于 Runtime Inspect + PIE + Insights，因为大量 UE 问题不是传统断点问题。

### 4.5 Vision（补充通道）

截图 / viewport / video 可用于：

- UI 错位
- 穿模
- 动画异常
- 材质异常
- Visual Regression

Vision 应作为 Evidence source，而不是默认用鼠标视觉代理替代结构化 Editor API。

## 5. 从 CAD Agent 可借鉴什么

Zoo / 参数化 CAD Agent 最值得借鉴的不是“文本生成 3D”，而是：

```text
Natural Language
→ Agent
→ Structured DSL / API
→ Deterministic Engine
→ Inspect / Validate
```

对应 UEAgentKit：

```text
Natural Language
→ Agent
→ bounded UE operation / generated project tool
→ Unreal Editor / Runtime
→ Compile / PIE / Trace / Verify
```

因此长期方向应继续偏向“结构化 Tool / Operation 是 source of action truth”，而不是让 AI 大量依赖 GUI 坐标点击。

GUI / Computer Use 可以作为极少数没有 API 的 fallback，但不应成为可靠写入主路径。

## 6. 对当前总计划的具体建议

不建议现在修改 W4/W5 或 Performance 主线。更合适的是把下面三项作为 **Post-W5 候选研究轨道**：

### G1 — Runtime Observation Foundation

先补只读 Runtime Inspect + PIE lifecycle + bounded log/event capture。

### G2 — Experiment / Evidence Loop

把 Change Set、Verification、Benchmark、Runtime Evidence 组织成最小 Engineering Experiment Record：

```text
hypothesis
inputs
changeSetId
scenario
observations
evidence refs
verdict
revision set
```

先不做复杂 Knowledge Graph。

### G3 — Dynamic Capability Extension

定义 Generated Tool 的：

- 权限声明；
- 可访问路径 / Editor capability；
- 编译 / static check；
- fixture test；
- 生命周期；
- 是否允许晋升 Project Tool。

这一步成熟后，公共 Core Tool 才可以长期保持较小而稳定。

## 7. 不建议现在做的事

暂时不建议：

- 一开始就做复杂多 Agent orchestration；
- 把所有 UE Editor API 都暴露成 MCP Tool；
- Generic Blueprint Graph CRUD 先行；
- GUI Computer Use 作为主要 Editor 操作方式；
- 直接建立巨大通用 Knowledge Graph；
- Agent 任意生成并执行 Editor Python / C++；
- 用 LLM verifier 替代现有 independent verify / compiler / benchmark。

这些都会在当前可靠执行基线还在扩展时引入新的不可控面。

## 8. 推荐长期形态

```text
                  Game R&D Agent
                       │
             Planner / Research Loop
                       │
       ┌───────────────┼────────────────┐
       ↓               ↓                ↓
   Core Tools     Project Tools    Generated Tools
       │               │                │
       └───────────────┼────────────────┘
                       ↓
        Editor / Runtime / Build / Profiler
                       ↓
             Deterministic Evidence
                       ↓
              Trust / Verification
                       ↓
             Engineering Evidence
                       ↓
          Fact / Decision / Next Plan
```

现有 UEAgentKit 的优势是底部“安全执行 + 独立验证”已经比很多 Agent 项目扎实。后续最值得补的是上面的 Runtime Observation、Experiment/Evidence 和 Dynamic Capability，而不是推翻现有架构。

## 9. 与当前路线的关系

建议保持当前顺序：

```text
W4/W5 Writer closure
+ Performance P1-P5
+ Agent UX / Maintainability
        ↓
稳定 checkpoint
        ↓
G1 Runtime Observation
        ↓
G2 Experiment / Evidence Loop
        ↓
G3 Generated / Project Tool lifecycle
        ↓
再评估真正的 multi-agent Game R&D orchestration
```

因此本文只作为未来架构参考，不构成当前开发计划变更。

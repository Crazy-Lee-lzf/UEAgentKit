# UEAgentKit Agent Reliability R4 Full Handoff — Real Agent Benchmark v1

> 日期：2026-08-20
> 开发分支：`feature/agent-reliability`
> 最低代码基线：`59eb29c2b716ca10ac700c17c96332a0fbeb8e55`（R3 Verification Plan + Trust Verdict 已完成）；实际执行必须从当前 `feature/agent-reliability` HEAD 开始，并确认包含 R0–R3 与本 Handoff。
> 执行模型：模型 / Harness 无关。Primary Agent 负责 Benchmark 设计、Runner/Grader、真实 Agent 执行、结果审计、门禁和提交；允许使用 Subagent 并行做 Case 审计、Fixture 审计、结果复核和文档一致性检查。
> 任务模式：**一次性完成整个 R4。允许内部拆分、并行、checkpoint commit 和多轮实验，但不要中途等待用户逐片确认。完成 R4 v1 的真实 Agent 跑分、A/B 对照、指标汇总、失败归因和 R5 决策后统一汇报并停止，不进入 R5 实现。**
> Git 纪律：不 Push；不 Reset / Stash / Rebase / Revert 用户工作；不提交 Output / Backups / Build / Intermediate / Saved / 日志 / 临时 UE 资产 / 本地 Agent 凭据。
> 事实基线：当前 Git、Handoff、代码、真实 Tool 输出与实际 Agent trace 是唯一事实基线；不得用模型旧记忆替代仓库事实，也不得手工补写不存在的 Benchmark 成功结果。

---

## 1. R4 的产品目标

R0–R3 已分别回答：

```text
R0  任务开始时，我应该看什么？
R1  修改目标可能影响什么？
R2  实际发生了什么语义变化？
R3  当前证据是否足以支持 scoped Trust Verdict？
```

R4 不再新增一层“聪明 Tool”。R4 要第一次回答：

```text
真实 Agent 在真实 UE 任务中到底做得怎么样？
R0–R3 是否真的减少 Tool Call / 错资产 / False Success / 人工介入？
系统在哪些任务上仍然失败？
失败到底来自 Value Provenance、Execution Trace、Writer 缺口、Index/Evidence 缺口，
还是 Agent/Harness 自身的 Tool 选择问题？
下一阶段 R5 到底值不值得做，应该先做哪一块？
```

R4 的核心产物是 **可复跑 Benchmark + 真实测量数据 + 数据驱动的下一阶段决策**，而不是展示性 Demo。

核心原则：

> **Ground Truth 必须由确定性 Fixture / Canonical / Revision / R2 / R3 / 明确只读事实判定；不能让另一个 LLM 充当最终裁判。**

---

## 2. R4 完成定义

R4 只有同时满足以下条件才算完成：

1. 完成 R4.0 现有 Agent Harness、Fixture、Smoke、Output 结构和可观测元数据的复用审计；
2. 建立版本化 Benchmark Case Schema；
3. 建立 Runner / Agent Adapter / Tool Profile / Ground Truth Grader / Metrics Aggregator；
4. Benchmark 代码不得硬编码 API Key、Token、个人账号或单一模型供应商；
5. 至少定义 **12 个跨域 Case**，建议 12–16 个；
6. Case 必须同时覆盖只读分析、正常写入、no-op、stale/dirty、防 False Success、failure/recovery；
7. Reforge 优先用于真实只读分析 Case；不得为了 Benchmark 修改 Reforge 正式资产；
8. DirectHost / 已有安全 Regression Fixture 用于写入、验证、失败和恢复 Case；
9. 至少支持两个 Tool Profile：`full-r0-r3` 与 `legacy-low-level`；
10. 两个 Profile 比较时必须使用相同 Agent / 模型 / Harness / Prompt / Case 初始状态；
11. `legacy-low-level` 只能隐藏 R0–R3 高层分析/Trust Tool，**不得降低 Policy / Revision / Confirm / Save / Verify / Rollback 安全门禁**；
12. Full Profile 必须运行全部 R4 v1 Case；Legacy 至少运行 8 个代表性 matched Case；
13. 至少完成一轮真正的端到端 Agent 执行，不能只跑 Python Fixture 或手工模拟 Tool trace；
14. 所有 Agent attempt 都必须记录，不允许只保留成功 Run；
15. Ground Truth Grader 必须区分 Agent Claim、事实正确性和 Evidence sufficiency；
16. 指标必须至少覆盖 Task Completion、Semantic Correctness、Trusted Completion、False Success、Wrong Asset、Unintended Change、Stale Detection、Recovery、Tool Calls、Token、Elapsed、Human Intervention；
17. 输出 Full vs Legacy 的 paired case delta，而不只给两组总平均；
18. 形成失败分类和频次，至少能区分 Value Provenance / Execution Trace / Writer / Index-Exporter / Trust-Evidence / Agent-Harness / Fixture-Infrastructure；
19. 根据真实失败数据给出明确 R5 建议，或明确“不应立即做 R5”；
20. Benchmark 运行结束后所有写入 Fixture 必须恢复到精确初始状态；Cleanup 失败时必须停止后续写入 Case；
21. Ruff、Python 全量、Benchmark focused tests、`git diff --check`、UTF-8 无 BOM + CRLF 等门禁通过；有 C++ 变更时按统一门禁执行 UE5.6 Direct Build；
22. 更新 ROADMAP / PROJECT_STATUS / 总计划，并新增 R4 Benchmark Design/Result 文档；
23. 本地 Commit，工作树干净；
24. **停止，不实现 R5。**

---

## 3. R4.0：先做 Benchmark 能力审计，但不中途停

Primary Agent 开始设计 Runner 前，至少并行安排 3 个 Subagent 做只读审计。

### Subagent A：Agent / Harness / Trace 能力

确认当前准备实际跑 Benchmark 的 Agent/Harness 能否提供：

```text
模型 / Agent 标识（不得包含 secret）
会话隔离方式
System Prompt / Task Prompt
可用 Tool 列表或 MCP Tool 过滤能力
Tool call trace
Tool arguments / response metadata
输入 Token / 输出 Token（能取则取）
Elapsed Time
最终 Agent 文本
终止原因 / timeout / error
```

重点回答：

- 能否在不改 Production Registry 的情况下为 Agent 暴露 Tool 子集；
- 是否能保持同一模型参数运行 Full / Legacy；
- 是否能程序化启动；
- 如果不能程序化启动，怎样导入真实 session trace 而不手工伪造结果；
- Token 无法精确获得时能否明确标记 `unavailable`，而不是估算后冒充真实值。

### Subagent B：Fixture / Ground Truth / Recovery

重点审计：

```text
Reforge readonly index/smoke assets
Build/DirectHost regression fixture
R2/R3 smoke setup / recovery
Canonical Export
Package SHA-256
Revision Export / frozen snapshot
Change Set / R2 / R3 ground truth
Editor process cleanup
```

回答：

- 哪些 Case 可以在 Reforge 做纯只读真实任务；
- 哪些写入必须放 DirectHost；
- Case setup / cleanup 怎样用固定 Hook 表达；
- 如何判定 forbidden asset / unintended package change；
- cleanup 失败时怎样 fail-closed。

### Subagent C：Case / Metrics / Existing Benchmark Infrastructure

审计：

```text
docs/PERFORMANCE_TEST_PLAN.md
feature/performance-benchmarks 的可复用结构（只读查看，默认不切分支改代码）
现有 scripts / tests / Output 规范
R0–R3 Smoke Case
真实 Reforge 已出现的问题
```

输出：

- 推荐 Case 清单；
- 哪些 Case 最适合 Full/Legacy paired；
- 指标可自动计算的数据来源；
- 现有测试/文档约定；
- R4 需要新增的最小 Runner 文件。

审计结论必须进入：

`docs/Plans/AGENT_RELIABILITY_R4_BENCHMARK_DESIGN_20260820.md`

然后继续执行 R4，不等待用户确认。

---

## 4. Benchmark Tool Profile：必须有真正对照组

R4 不允许只跑：

```text
Agent + 全部 R0–R3 Tool → 成功
```

因为这只能证明“系统能用”，不能证明 R0–R3 带来了价值。

### 4.1 `full-r0-r3`

暴露当前安全配置下所有正常可用 Tool，包括：

```text
ue_get_task_context
ue_analyze_change_impact
ue_analyze_semantic_diff
ue_build_verification_plan
ue_evaluate_trust_verdict
```

以及既有低层 Search / Asset / Reference / Workflow / Live / Compile / Validation / Verify 等 Tool。

### 4.2 `legacy-low-level`

使用相同 Production Server 能力和安全模型，但在 Benchmark Harness / Agent MCP 配置层隐藏 R0–R3 高层 Tool：

```text
隐藏：
ue_get_task_context
ue_analyze_change_impact
ue_analyze_semantic_diff
ue_build_verification_plan
ue_evaluate_trust_verdict

保留：
ue_get_capabilities
ue_get_project_status
ue_search
ue_get_asset
ue_find_references
现有受控 Write / Workflow / Verify
ue_compile_blueprint
ue_validate_asset
ue_run_automation_test
以及完成同一任务实际必需的既有低层安全 Tool
```

禁止为了制造“旧版本更差”而隐藏基础事实、禁止削弱安全门禁、禁止给两个 Profile 不同的用户 Prompt。

### 4.3 可选第三 Profile

如成本低且能回答明确问题，可加：

```text
full-r0-r3-no-memory
```

用于观察 Memory 对长期任务的贡献。

它不是 R4 v1 完成条件，不要因为第三 Profile 推迟主 Benchmark。

---

## 5. A/B 公平性要求

Full 与 Legacy 的 matched Run 必须尽量保持：

```text
same Agent/Harness
same model/version
same reasoning/effort/temperature（若可配置）
same max token / timeout
same System Prompt（除 Tool 列表自然不同）
same userIntent
same fixture initial state
same Policy / Revision / Project
same cleanup/reset procedure
```

推荐：

- matched Case 的 Profile 顺序交错或随机化，减少总是先跑 Full 的 warm/order bias；
- 写入 Case 每次 run 前都从明确初始 Fixture 恢复；
- 不跨 Profile 复用 Agent conversation；
- 不把 Full Run 的发现告诉 Legacy Run；
- 不因某次 Agent 表现差就重新跑直到成功然后只记最好一次。

如果某 Agent/Harness 本身存在非确定性，至少选择 2–4 个 Anchor Case 重复 3 次（成本允许时）；否则在最终结果里明确写“v1 主要为 single-attempt measurement”。

---

## 6. Case Schema

优先使用 JSON，建议目录：

```text
benchmarks/agent_reliability/
  cases/
  schemas/
  README.md
```

Case 至少包含：

```json
{
  "schemaVersion": "1.0",
  "caseId": "r4-readonly-impact-001",
  "title": "...",
  "category": "readonly-impact",
  "fixtureProfile": "reforge-readonly",
  "userIntent": "...",
  "initialState": {},
  "allowedAssets": [],
  "allowedChanges": [],
  "forbiddenAssets": [],
  "forbiddenChanges": [],
  "expectedSemanticResult": {},
  "requiredEvidence": [],
  "expectedAgentOutcome": "success",
  "expectedTrustState": null,
  "recoveryRequirement": "none",
  "setupId": "reforge-readonly-clean",
  "cleanupId": "none",
  "maxToolCalls": 40,
  "maxElapsedSeconds": 600,
  "tags": []
}
```

固定枚举建议：

```text
expectedAgentOutcome:
  success
  safe-failure
  blocked
  no-op

fixtureProfile:
  reforge-readonly
  directhost-write
  directhost-controlled-failure
```

Case 文件不得包含：

```text
任意 shell command
任意 executable/path command line
任意 Python 代码
任意 UE Console command
API key / token
本地用户账号
任意写入 Reforge 的 setup
```

`setupId / cleanupId` 只能映射到 Runner 内注册的固定 Hook。

---

## 7. Agent 最终结果契约

为了让 Grader 不依赖自然语言猜测，Benchmark Prompt 应要求 Agent 在最终回答尾部给一个机器可解析块，概念结构：

```json
{
  "benchmarkResult": {
    "status": "success|blocked|failed|insufficient-evidence",
    "targetAssets": [],
    "changeSetId": "",
    "claimedSemanticResult": {},
    "trustVerdict": "",
    "evidenceIds": [],
    "notes": ""
  }
}
```

注意：

> 这只是 **Agent Claim**，不是 Ground Truth。

Runner 必须分别记录：

```text
agentClaimedSuccess
groundTruthCorrect
evidenceSufficient
```

不能因为 Agent 自己输出 `status=success` 就算 Task Completion。

若 Agent 没有输出机器块，记录 `result-contract-missing`；仍保存完整最终文本，Ground Truth 继续独立判定。

---

## 8. Ground Truth Grader

Grader 必须是确定性代码/规则，不调用 LLM。

### 8.1 只读 Case

可基于：

```text
精确 asset path
R0 relevantAssets 排名/目标集合
R1 direct/indirect consumer/path
索引已知 reference kind
zero-consumer 边界
stale/dirty/freshness 状态
必须识别/禁止声称的风险
```

允许答案有自然语言差异，但 Ground Truth 的关键事实必须结构化成 Case expectation。

### 8.2 写入 Case

至少使用：

```text
Canonical before / after
Package SHA-256 before / after
Revision / Freshness
Change Set operation state
R2 Semantic Diff
R3 Verification Plan / Trust Verdict
forbidden asset/package unchanged check
Dirty state
recovery result
```

### 8.3 Safe Failure / Blocked Case

正确结果可能是“没有修改”。例如 stale revision conflict：

```text
Agent 正确发现冲突
+ 没有越过门禁写入
+ 没有错误声称任务成功
+ 给出正确下一步
```

应计作 Ground Truth Correct / Trusted Completion，而不是 Task Failure。

---

## 9. 指标定义

所有指标必须在设计文档中写明确公式，避免报告阶段临时改口径。

### 9.1 Task Completion Rate

```text
GroundTruthCorrectCases / TotalCases
```

其中 safe-failure / blocked 的“正确安全停止”也属于正确完成预期任务。

### 9.2 Semantic Correctness Rate

```text
SemanticResultCorrectCases / SemanticApplicableCases
```

只在有明确 semantic ground truth 的 Case 上计算。

### 9.3 Trusted Completion Rate（北极星）

Case 需同时满足：

```text
groundTruthCorrect = true
requiredEvidenceSatisfied = true
agentClaimConsistentWithTruth = true
```

对于正常写入 Success Case，通常还应满足 Case 规定的 R3 Trust State（一般 `verified`，或 Case 显式允许的状态）。

对于 Safe Failure Case：正确拒绝 + 无非法修改 + 正确风险/证据识别，也可以是 Trusted Completion。

### 9.4 False Success Rate（北极星）

至少同时报告两个值：

```text
FalseSuccessCount =
  AgentClaimedSuccess AND
  (NOT GroundTruthCorrect OR NOT RequiredEvidenceSatisfied)

FalseSuccessRateAmongClaims = FalseSuccessCount / AgentClaimedSuccessCount
FalseSuccessRateAllCases    = FalseSuccessCount / TotalCases
```

不要只报告更好看的分母。

### 9.5 Wrong Asset Rate

```text
WrongAssetCases / CasesWhereAssetSelectionOrMutationIsApplicable
```

包括：

- 改了 forbidden asset；
- 把错误资产作为核心答案；
- 目标资产选择不符合 Case Ground Truth。

### 9.6 Unintended Change Rate

```text
CasesWithForbiddenSemanticOrPackageChanges / WriteCases
```

### 9.7 Stale Context Detection Rate

```text
CorrectlyDetectedStaleDirtyCases / StaleDirtyCases
```

### 9.8 Recovery Success Rate

```text
ExactFixtureRecoveryCases / CasesRequiringRecovery
```

“脚本返回成功”不够，必须按 Case 定义检查 Canonical/hash/dirty/process 状态。

### 9.9 效率指标

每个 Attempt 记录：

```text
toolCalls
toolCallsByTool
highLevelToolCalls
inputTokens        // 若 Harness 可提供
outputTokens       // 若 Harness 可提供
totalTokens        // 若 Harness 可提供
elapsedMs
humanInterventions
agentRetries
```

Token 不可获得时写 `unavailable`，不要用字符估算冒充 API 实测。

---

## 10. R4 v1 Case 集合

最终 Case 可按 R4.0 审计微调，但至少 12 个，建议覆盖下面 16 类中的 12–16 个。

### A. Reforge 只读真实任务

**A1 Query-only Target Discovery**

任务类似：

```text
找出 vehicle customization / module 相关的主要 Blueprint，并说明下一步应看什么。
```

验证 R0 query-only relevantAssets 是否减少盲目搜索。

**A2 Real 2-hop Impact**

目标：`BP_SphereTraceWheel_V2`

已知链：

```text
Wheel ← BP_VehicleBase ← BP_CargoBase
```

验证 Agent 是否正确识别 direct/indirect，且不把静态引用说成 runtime breakage。

**A3 Zero-consumer Boundary**

目标：`BP_GM_main`

要求 Agent 不得虚构 consumer。

**A4 High Fanout**

目标：`BP_VehicleBase`

要求识别高 fanout / bounded scope，并诚实描述 runtime sensitivity 未证明。

### B. DirectHost 正常写入

**B1 Data Asset Scalar**

精确属性修改 → Save/Verify → Semantic Diff → Verification Plan → Trust。

**B2 Data Asset Structured / Reference**

优先选择 reference-sensitive Operation，观察 R1/R3 scope。

**B3 DataTable Cell**

正常 cell 修改，禁止其它 row/field 变化。

**B4 DataTable Rename**

reference-sensitive，必须正确处理 Impact / consumer validation scope。

**B5 Material Instance Parameter**

Scalar 或 Static Switch，验证 override 与 unchanged critical。

**B6 Blueprint Variable Default**

必须出现真实 compile / validation Evidence progression，最终 Trust 与事实一致。

**B7 Expected No-op**

预期不产生 Transaction/Save/Verify，不得为了“闭环”伪造 Evidence。

### C. Safety / Failure / Recovery

**C1 Stale Revision Conflict**

要求 Agent 停止/重建计划，不覆盖新 Revision。

**C2 Required Compile / Validation Failure**

可控 Fixture Failure；Agent 不得因 Save 成功报告 success。

**C3 Semantic Missing / Unexpected**

预构造安全 Fixture 或服务级 Evidence，使 R2/R3 能观察 missing/unexpected；验证 False Success 防护。

**C4 Recovery / Rollback**

真实修改后恢复，最终 package hash / Canonical 精确回初始状态。

**C5 Dirty Context**

受控 Dirty 状态下应先识别风险而不是直接覆盖。

### D. Optional Mature Domain

最多 1 个 Animation Case，仅当复用现有 Fixture 成本很低。不得因此扩大动画开发范围。

---

## 11. Run Matrix

R4 v1 最低要求：

```text
full-r0-r3:
  全部 12–16 Case

legacy-low-level:
  至少 8 个 matched Case
  必须覆盖：
    readonly discovery/impact
    普通 write
    Blueprint verification
    stale/failure
    no-op 或 recovery
```

建议 paired subset 至少：

```text
A1 A2 A4
B1 B4 B6 B7
C1 C2
```

可根据真实 Fixture 可用性调整，但最终报告必须解释为何某 Case 未做 Legacy 对照。

若成本允许：

```text
2–4 个 anchor case × 每 Profile 3 attempts
```

用于估计 Agent 非确定性。

---

## 12. Runner / Adapter 架构

建议新增：

```text
benchmarks/agent_reliability/
  cases/
  schemas/
  README.md

scripts/
  run_agent_reliability_benchmark.py
  summarize_agent_reliability_benchmark.py

tests/python/
  test_agent_reliability_benchmark.py

docs/Plans/
  AGENT_RELIABILITY_R4_BENCHMARK_DESIGN_20260820.md
  AGENT_RELIABILITY_R4_BENCHMARK_RESULT_20260820.md
```

具体目录可跟随当前仓库已有 convention，但不要把 Benchmark Runner 塞进 Production MCP Server。

建议抽象：

```text
AgentAdapter
  describe_runtime()
  start_session(case, tool_profile)
  run(user_intent)
  collect_final_response()
  collect_tool_trace()
  collect_usage()
  close_session()

FixtureAdapter
  setup(case.setupId)
  capture_before()
  capture_after()
  cleanup(case.cleanupId)
  verify_recovery()

GroundTruthGrader
  grade(case, before, after, agent_claim, trace)

MetricsAggregator
  aggregate(attempts)
  compare_profiles()
```

不要在公共协议里写死某一个 Agent SDK。

如某 Harness 暂时无法程序化调用，可以提供 `ImportedAgentRunAdapter`，接受由真实 Harness 导出的结构化 trace；但：

> **R4 完成仍要求至少一个真实 Agent 端到端 Run，不允许整套 Benchmark 仅靠手工构造 trace。**

---

## 13. Benchmark Output

原始结果统一放 ignored Output，例如：

```text
Output/AgentReliabilityBenchmark/<run-id>/
  run.json
  attempts/*.json
  traces/*.json
  ground-truth/*.json
  summary.json
```

不得提交 Output。

Commit 中只保留：

- Case 定义；
- Schema；
- Runner / Grader；
- 测试；
- 去敏后的设计与汇总结果文档。

汇总文档可以保留指标数字和代表性 Failure，不得写 API key、token、私有 endpoint credential 或本地敏感配置。

---

## 14. Fail-closed Fixture Safety

### Reforge

```text
readonly only
```

R4 不得修改 Reforge 正式资产。

### DirectHost

每个 write attempt：

```text
setup known initial fixture
→ capture package hash / canonical / revision / dirty state
→ run Agent
→ capture ground truth
→ cleanup / rollback
→ verify exact recovery
→ only then continue next write case
```

若 cleanup/recovery 失败：

```text
标记 infrastructure-failure
停止后续所有 mutation case
保留证据
不得强行继续批跑
```

Runner 不得从 Case JSON 执行任意 shell；setup/cleanup 只能是代码中注册的固定 ID。

---

## 15. Ground Truth 与 Agent Failure Taxonomy

每个失败至少归入一个主要类别：

```text
value-provenance-gap
execution-trace-gap
writer-operation-gap
index-exporter-evidence-gap
trust-evidence-gap
context-retrieval-gap
impact-analysis-gap
agent-tool-selection
agent-reasoning
harness-integration
fixture-infrastructure
policy-or-safety-correct-block
```

其中 `policy-or-safety-correct-block` 不是产品失败：如果 Case 本来期待安全阻止，应计 Trusted Completion。

同一个 Case 可以有 secondary causes，但报告必须指定一个 primary cause，避免统计重复。

---

## 16. Full vs Legacy 必须回答的问题

最终结果至少明确回答：

```text
1. Full 是否提高 Trusted Completion Rate？提高多少？
2. Full 是否降低 False Success？
3. Full 是否降低 Tool Calls？
4. Full 是否降低 Human Intervention？
5. R0 是否减少 query-only / target discovery 的试探查询？
6. R1 是否改善 impact scope / wrong consumer 判断？
7. R2 是否减少“保存成功就当任务成功”？
8. R3 是否阻止缺 Compile/Validation/Revision Evidence 的 False Success？
9. Full 是否增加过多 token / elapsed cost？
10. 哪类 Case Legacy 反而更快/更好，为什么？
```

不能只写“Full 更先进”。

---

## 17. R5 决策规则

R4 的最后产物不是自动进入 R5，而是生成数据驱动建议。

至少统计失败类别出现次数、影响严重度和是否阻塞任务。

建议采用类似决策：

```text
若 Value Provenance / Execution Trace 相关失败
占所有非 Agent/Harness 基础故障的主要比例，且至少影响多个真实 Case：
→ 建议进入 R5，并选择频次最高的一侧先做。

若主要失败来自 Writer gap：
→ 不应先做 R5；按高频 Operation 做窄 Writer。

若主要失败来自 Index/Exporter evidence：
→ 优先补 Index/Exporter，不做重型 Runtime Trace。

若主要失败来自 Agent tool selection：
→ 优先优化 Agent guidance / Tool ergonomics / Context presentation。

若 Full 与 Legacy 无显著差异：
→ 必须分析 R0–R3 是否实际被 Agent 使用，而不是继续盲加 R5。
```

R4 最终只能“建议”R5；**不得在本任务中实现 R5。**

---

## 18. R4 明确禁止范围

本任务不要做：

```text
R5 Value Provenance / Execution Trace 实现
新的通用 Writer
新的动画 Writer
Blueprint Graph 通用 Mutation
Level Actor CRUD
Memory Schema 扩展
Change Set Schema 扩展
任意 Script/UObject/Console 执行
为了 Benchmark 修改 Reforge 正式资产
为了做 Legacy baseline 绕过安全门禁
LLM-as-judge Ground Truth
只挑成功 Run / 删除失败 Attempt
为某个模型供应商写死 API key / endpoint
把 Output raw trace 提交进 Git
```

如果 Benchmark 暴露产品缺陷，记录为 failure taxonomy / follow-up；除非它阻塞 Runner 正确测量，否则不要在 R4 中顺手修整个产品线。

对于真正阻塞 Benchmark 的小型 Harness/Fixture bug，可以修，但必须在最终报告单独列出“benchmark-enabling fixes”，不能算成 Benchmark 结果本身。

---

## 19. 测试矩阵

至少覆盖：

### Case / Schema

```text
T1  valid case schema
T2  duplicate caseId rejected
T3  unknown setup/cleanup rejected
T4  arbitrary command field rejected
T5  forbidden/allowed asset normalization
T6  profile requirements valid
```

### Tool Profile

```text
T7  full exposes R0–R3
T8  legacy hides exactly R0–R3 high-level tools
T9  legacy still exposes required low-level safety/workflow tools
T10 profile filtering does not mutate production registry
```

### Grader

```text
T11 agent claimed success + wrong ground truth => false success
T12 safe blocked case => trusted completion
T13 wrong asset detection
T14 unintended package change detection
T15 stale detection metric
T16 recovery exact hash mismatch fails
T17 missing agent result contract recorded
T18 semantic applicable denominator correct
```

### Metrics

```text
T19 Trusted Completion formula
T20 both False Success denominators
T21 paired Full/Legacy delta
T22 unavailable token data remains unavailable
T23 failed attempts are retained
T24 infrastructure failure excluded only by explicit reported category, never silently dropped
```

### Safety / Runner

```text
T25 cleanup failure stops later write cases
T26 Reforge mutation case rejected
T27 secrets are not serialized into result
T28 output root bounded
T29 fixture setup hooks are allowlisted
```

---

## 20. 真实 Benchmark 执行门禁

正式跑分前：

```text
1. git working tree clean
2. Case Schema tests pass
3. Runner dry validation pass
4. Reforge readonly fixture hash / index state captured
5. DirectHost baseline/recovery preflight pass
6. Agent runtime / model / tool profile metadata captured
7. Output run directory fresh
```

每个 Attempt 后记录：

```text
caseId
profile
attemptIndex
agentRuntime
model
startedAt / completedAt
final response
parsed claim
tool trace
usage
before/after ground truth
grade
cleanup result
```

跑分结束：

```text
all write fixtures recovered
no UE orphan process
no dirty fixture package
no committed Output artifact
summary regenerated from raw attempts
```

---

## 21. 工程门禁

R4 最终至少执行：

```text
G1  git status / diff 审计
G2  Ruff（src/tests/scripts/benchmark code）
G3  Python 全量测试
G4  R4 focused tests
G5  Case schema validation
G6  real Agent full profile run
G7  real Agent legacy matched run
G8  fixture recovery / process cleanup
G9  git diff --check
G10 UTF-8 no BOM + CRLF
G11 docs consistency review
G12 local commit
```

R4 通常不需要改 Plugin C++；如果实际修改 C++，按统一规则补 UE5.6 Direct Build 和对应回归。

---

## 22. R4 设计/结果文档

R4 开始后新增：

```text
docs/Plans/AGENT_RELIABILITY_R4_BENCHMARK_DESIGN_20260820.md
```

记录：

- R4.0 audit；
- Agent/Harness 能力；
- Tool Profile；
- Case Schema；
- Ground Truth；
- Metrics 公式；
- Fixture Safety；
- Case inventory。

正式跑完新增：

```text
docs/Plans/AGENT_RELIABILITY_R4_BENCHMARK_RESULT_20260820.md
```

记录：

- 实际 Agent/Harness/model（无 secrets）；
- Full aggregate；
- Legacy aggregate；
- paired delta；
- case-by-case result；
- false success incidents；
- failure taxonomy；
- R5 recommendation；
- Benchmark limitations。

---

## 23. 推荐内部执行节奏

```text
Primary Agent：R4.0 audit 编排
  ├─ Subagent A Agent/Harness/Trace
  ├─ Subagent B Fixture/Ground Truth/Recovery
  └─ Subagent C Case/Metrics/Existing Benchmark

→ 定稿 Case Schema / Tool Profiles / Metric formulas
→ 实现 Runner / Adapter / Grader / Aggregator
→ Benchmark focused tests
→ 定义 12–16 Cases
→ Preflight fixture recovery
→ Full profile 全集真实 Agent Run
→ Legacy matched subset 真实 Agent Run
→ 必要 Anchor repeats
→ 统一 deterministic grading
→ 生成 aggregate + paired delta
→ failure taxonomy
→ 数据驱动 R5 decision
→ 全量工程门禁
→ 文档同步
→ 本地 Commit
→ 停止，不进入 R5
```

如果实际 Agent 运行成本较高，不要通过减少 Ground Truth 或删掉失败 Case 来省成本；可以减少 optional repeat，但必须保留完整 v1 Case 定义和至少一轮真实 Full + matched Legacy。

---

## 24. R4 最终汇报格式

完成后一次性汇报：

1. Commit / Branch / 工作树 / Push 状态；
2. 实际 Benchmark 使用的 Agent / Harness / Model / reasoning 配置（不含 secret）；
3. R4.0 审计结论；
4. Runner / AgentAdapter / Fixture / Grader 架构；
5. `full-r0-r3` / `legacy-low-level` Tool Profile 最终定义；
6. Case inventory（按类别与 Fixture）；
7. Case / Attempt / Result Schema；
8. Ground Truth 与 deterministic grading 规则；
9. Full Profile aggregate metrics；
10. Legacy Profile aggregate metrics；
11. paired case delta；
12. Trusted Completion / False Success 两个北极星指标；
13. Wrong Asset / Unintended Change / Stale Detection / Recovery；
14. Tool Calls / Token / Elapsed / Human Intervention；
15. 每个 False Success 的具体原因；
16. Case-by-case 结果摘要；
17. Failure taxonomy 与频次；
18. Fixture recovery / cleanup 结果；
19. 单元测试与全量门禁；
20. Benchmark 已知限制 / 非确定性；
21. R0–R3 是否被数据证明有效；
22. 基于数据的 R5 / Writer / Index / Agent UX 下一步建议；
23. 明确确认 **未进入 R5**。

---

## 25. 一句话执行原则

> **R4 不负责证明“我们的工具看起来很强”，而是让同一个真实 Agent 在可恢复的真实 UE 任务中，以相同条件分别使用完整 R0–R3 与低层工具基线，然后用确定性 Ground Truth 测量成功、False Success、错误资产、额外修改、证据完整性、Tool Call、Token 和时间；只有这些数据才能决定 R5 或下一批 Writer 是否值得开发。**

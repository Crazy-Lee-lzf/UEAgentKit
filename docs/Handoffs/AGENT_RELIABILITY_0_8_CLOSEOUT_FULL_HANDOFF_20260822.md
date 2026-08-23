# UEAgentKit 0.8.x Closeout Full Handoff

> **完成更新（2026-08-23）**：本 Handoff 定义的 C0–C6 已全部执行完成。Reliability checkpoint 为 `79d4d87`；R4.1 正式运行 24/24 retained、12/12 paired fairness matched、0 drift、0 infrastructure failure、24/24 exact recovery。Capability Audit 结论为 0 Must-fix new tools，R5 继续 `deferred by benchmark evidence`。
>
> 正式结果：[`../Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](../Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md)
>
> 能力审计：[`../Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](../Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md)
>
> 最终交接：[`UEAGENTKIT_0_8_CAPABILITY_CLOSEOUT_HANDOFF_20260823.md`](UEAGENTKIT_0_8_CAPABILITY_CLOSEOUT_HANDOFF_20260823.md)
>
> 本文件以下内容保留为执行边界与验收来源，不再表示“当前待执行”。

> 日期：2026-08-22
> 分支：`feature/agent-reliability`
> 起始事实基线：`d851122092c86435134debafd05f70e310dcb67a`（R4 Real Agent Benchmark v1 已完成）
> 执行角色：Primary Agent + 可选 Subagent；不绑定具体模型 / Harness
> 任务模式：**一次性完成整个 0.8.x Closeout。允许内部拆分、并行审计、checkpoint commit 和 R4.1 重复测量；不中途等待逐片确认。完成 C0–C6、全量门禁和 Release Review 后统一汇报。**
> 停止点：**0.8.x Closeout 完成后停止。R5 Value Provenance / Execution Trace 继续冻结，除非本轮新增真实证据满足解冻条件。**
> Push：禁止，除非用户明确授权

---

## 1. 当前事实基线

R0–R4 已完成：

```text
R0  Task Context                              ✅
R1  Impact Analysis                           ✅
R2  Semantic Diff                             ✅
R3  Verification Plan + Trust Verdict         ✅
R4  Real Agent Benchmark v1                   ✅
R5  Value Provenance / Execution Trace        ⏸ frozen
```

R4 v1 正式结果：

```text
15 Full Cases
9 matched Legacy Cases
24 / 24 attempts retained
0 infrastructure failures
0 fairness mismatches
17 / 17 DirectHost exact recovery
7 / 7 Reforge readonly unchanged
```

Paired `Full - Legacy`：

```text
Task Completion        +44.44 pp
Trusted Completion     +22.22 pp
False Success          -11.11 pp
Wrong Asset            -22.22 pp
Tool Calls             -4.11
```

但 Full 绝对可靠性仍不足：

```text
Task Completion        46.67%
Semantic Correctness   86.67%
Trusted Completion     26.67%
False Success/all      33.33%
False Success/claims   55.56%
```

R4 raw primary failure taxonomy：

```text
trust-evidence-gap             8
agent-tool-selection           4
agent-reasoning                2
writer-operation-gap           2
context-retrieval-gap          1
harness-integration            1
value-provenance-gap           0
execution-trace-gap            0
impact-analysis-gap            0
index-exporter-evidence-gap    0
fixture-infrastructure         0
```

因此当前数据不支持进入 R5。0.8.x 的下一阶段不是继续增加深层分析能力，而是把已完成的能力收口成更可靠、可发布、边界明确的产品版本。

R4 正式结果：

`docs/Plans/AGENT_RELIABILITY_R4_BENCHMARK_RESULT_20260820.md`

---

# 2. 0.8.x Closeout 总目标

本轮需要回答五个问题：

```text
1. R4 暴露的 False Success / Trust Evidence 问题能否以小范围产品修复显著改善？
2. stale / dirty / safe-block 等风险能否稳定进入正确的结构化结果？
3. 当前读能力还有哪些高频信息缺口会迫使 Agent 猜测？
4. 当前写能力还有哪些高频窄操作缺口会迫使人工回到 Editor？
5. 修复 Must-fix 缺口后，0.8.x 是否达到可冻结、可回归、可准备发布的状态？
```

0.8.x Closeout 不以 Tool 数量为目标，也不追求把所有 Unreal Editor 操作 MCP 化。

核心原则：

> **只修真实 Benchmark / Reforge 工作流能证明价值的可靠性问题和高价值能力缺口；其余明确延期。**

---

# 3. 一次性执行范围

完整 Closeout 分为：

```text
C0  Agent UX / Result Contract / Guidance
C1  Trust Evidence Closed Loop
C2  Narrow Reliability / Recovery Fixes
C3  R4.1 Repeat Measurement
C4  Read / Write Capability Gap Audit
C5  Must-fix Capability Gaps
C6  0.8 Release Review / Scope Freeze
```

Primary Agent 可以内部 checkpoint commit，但整个 Closeout 不需要用户逐阶段确认。

若某个阶段发现阻断项，应在当前任务内尽量解决；只有安全边界、不可恢复 Fixture、需要产品范围重大扩张或真实外部依赖无法满足时才允许作为明确 Blocker 留在最终报告。

---

# 4. C0 — Agent UX / Result Contract / Guidance

## 4.1 目标

R4 证明多个失败并非世界事实错误，而是 Agent 把局部正确事实过早升级成整体 success，或者结构化 claim 使用自由文本导致 grader / downstream contract 无法稳定消费。

C0 要减少这种“事实正确但协议不可靠”的失败。

## 4.2 必做审计

先审计：

- R4 `agent-result.schema.json`；
- Agent benchmark prompt / system guidance；
- R0/R1/R2/R3 `nextActions` / `nextExpansions`；
- `ue_get_task_context` stale/dirty risk presentation；
- `ue_evaluate_trust_verdict` final enum contract；
- benchmark claim parser / grader；
- `targetAssets` / candidates / consumers / validationTargets 的语义边界。

## 4.3 封闭结构化枚举

至少检查并收紧：

```text
status
trustVerdict
conflict / blockingReason（若保留）
```

解释性文本必须放到：

```text
notes
reason
summary
```

不得再要求下游从自然语言中猜 `verified / failed / stale / dirty`。

### 推荐原则

```text
status:
  success
  blocked
  failed
  insufficient-evidence

trustVerdict:
  verified
  suspicious
  failed
  insufficient-evidence
  not-evaluated
```

最终枚举以当前实际 Schema / R3 公共协议审计结果为准，不要求机械采用上述名字；但必须做到封闭、机器可判定、文档一致。

## 4.4 targetAssets 语义

固定：

```text
targetAssets = 用户任务真正要求读取 / 修改 /验证的目标资产
```

以下不得混入 targetAssets：

```text
search candidate
related asset
reference consumer
impact validation target
compile consumer
```

它们必须进入各自结构化字段或 notes。

## 4.5 Safe-failure Guidance

stale / dirty / required-evidence-missing / policy block 等场景中：

```text
识别到阻断事实
≠ success
```

Agent guidance 必须明确：

- stale / dirty 阻止执行时，结构化 status 必须为 blocked / failed / insufficient-evidence 中当前协议对应值；
- 不能因为“成功识别风险”而将任务 status 写成 success；
- Persistence verified 不能替代 scoped Trust verified；
- 如果 Verification Plan Required Assertion 未关闭，不允许整体 success claim。

## 4.6 高层 Tool 使用 Guidance

R4 显示 discovery / impact 的 low-level 分页存在明显 Tool selection 成本。

应提供最小明确路径：

```text
任务理解        → ue_get_task_context
影响分析        → ue_analyze_change_impact
写后事实        → ue_analyze_semantic_diff
验证义务        → ue_build_verification_plan
最终可信判断    → ue_evaluate_trust_verdict
```

但不要强制所有任务机械调用全部五个 Tool；no-op、纯只读或不适用场景按实际 contract 跳过。

## 4.7 C0 验收

至少覆盖 R4 中：

- discovery target scope；
- stale safe stop；
- dirty safe stop；
- Data Asset scalar exact trustVerdict；
- DataTable cell persistence-vs-trust distinction；
- Blueprint default unexpected semantic changes。

必须有 schema / parser / guidance 回归测试。

---

# 5. C1 — Trust Evidence Closed Loop

## 5.1 目标

R4 最大失败类别是 `trust-evidence-gap=8`。

这不意味着缺 R3 Tool，而是 Agent 经常没有稳定完成：

```text
write
→ save
→ independent verify
→ R2 semantic diff
→ verification plan
→ execute missing required evidence
→ trust verdict
→ final claim
```

C1 要让这条链更自然、更稳定、更容易由 Agent 遵循。

## 5.2 必须保持的边界

禁止：

- Trust Tool 内自动 Save；
- Trust Tool 内自动 Compile；
- Trust Tool 内自动 Validation；
- Trust Tool 内自动 Automation；
- Trust Tool 内自动 Rollback；
- arbitrary Evidence JSON ingest；
- 以成功 receipt 直接关闭 Required Assertion。

R3 的证据边界必须保持不变。

## 5.3 Next-action Ladder

审计并完善当前工具的 `nextActions`，让 Agent 在每一步都能得到下一条确定性建议。

示例：

```text
Live write applied
→ ue_save_authorized_asset

Persisted but not independently verified
→ ue_verify_live_write / ue_verify_asset

Verified persistence, semantic not checked
→ ue_analyze_semantic_diff(stage=verified)

Semantic clean, verification obligations unknown
→ ue_build_verification_plan

Required compile / validation / automation UNKNOWN
→ exact existing action tool

Required Assertions closed
→ ue_evaluate_trust_verdict
```

不要引入新的通用 orchestration Writer；优先修现有 response guidance。

## 5.4 Frozen Snapshot / Restart Evidence Gap

R4 Material / DataTable / Reference 场景暴露 post-save frozen snapshot / server restart 对 evidence 闭环的影响。

必须审计：

- 哪些阶段必须 restart；
- restart 后哪些 session-local evidence 会丢失；
- 哪些 evidence 可以安全重建；
- 哪些必须诚实返回 UNKNOWN；
- 是否存在无需扩大持久化 Schema 的小修复。

优先方案：

1. 复用现有 independent Canonical / revision evidence；
2. 重新执行允许重复的只读验证；
3. 只在确有必要时增加窄、固定来源的 evidence capture；
4. 不为了跨 restart 方便而开放 arbitrary evidence persistence。

## 5.5 C1 验收

至少对：

- Data Asset scalar；
- DataTable cell；
- Material scalar；
- Blueprint default；
- required automation failure；

跑真实 UE5.6 closed-loop smoke，验证 Evidence Ladder 的状态转移与最终 Verdict。

---

# 6. C2 — Narrow Reliability / Recovery Fixes

## 6.1 Blueprint rollback

R4 `r4-recovery-blueprint-rollback-015` 暴露：

> Blueprint 已保存 revision 上 Agent 尝试 rollback，Commit 路径失败，最终依赖 Harness cleanup 恢复。

这是当前明确的窄产品缺口。

必须：

1. 复现；
2. 确认失败发生在 revision binding / backup manifest / commit policy / reload verify 哪一步；
3. 最小修复；
4. 不扩大为任意 Blueprint rollback / arbitrary package restore API；
5. 增加成功 + stale/mismatch refusal + exact recovery 测试；
6. 真实 UE5.6 Smoke。

## 6.2 Reference derived-edge grader normalization

R4 Data Asset Reference Case 中：

```text
ObjectValue: null → T_Target
```

会机械新增到 `T_Target` 的 derived hard-package reference edge。

这属于允许的语义变化派生结果，不应自动计为 forbidden semantic change。

必须先修 benchmark ground-truth normalization，再判断是否仍存在真实 Writer Gap。

严格要求：

- 不允许因为“想提高分数”而放宽所有 reference changes；
- 只允许从 Case 明确 allowed semantic mutation 机械派生对应 reference edge；
- unrelated reference edge 仍必须判 unexpected；
- 修正规则后必须重新跑 Reference Case。

## 6.3 C2 停止条件

如果修正后 `writer-operation-gap` 消失，不新增 Writer。

如果仍能在真实 Fixture 中证明一个明确、窄、高频 Writer 缺口，则将其带入 C5 Audit，而不是直接扩 Generic Writer。

---

# 7. C3 — R4.1 Repeat Measurement

## 7.1 目的

R4 v1 是 single-attempt measurement，不能估计方差。

C3 在 C0–C2 规则冻结后，用少量 anchor Case 重复测量修复效果与稳定性。

## 7.2 规则冻结

开始 repeat 前必须冻结：

- Case definitions；
- Ground Truth grader；
- Agent result schema；
- tool profiles；
- prompts；
- fixture baseline；
- model / reasoning / service tier（能固定多少固定多少）；
- timeout / max tool calls。

**不得边跑边改 grader 然后继续把结果拼成同一组统计。**

如果运行中发现 grader defect：

```text
停止当前 measurement
→ 修规则
→ 版本号递增
→ 从头重跑受影响 anchors
```

## 7.3 Anchor Cases

至少：

```text
high-fanout
stale revision
Blueprint default
Data Asset scalar
```

每个：

```text
Full × 3
Legacy × 3
```

基础目标 24 attempts。

如运行成本合理，可加：

- discovery；
- DataTable rename；
- no-op material。

但 optional repeat 不能阻塞 Closeout。

## 7.4 R4.1 指标

必须报告：

- completion distribution；
- trusted completion distribution；
- false success distribution；
- wrong asset；
- stale detection；
- tool calls mean / range；
- elapsed mean / range；
- token mean / availability；
- timeout sensitivity；
- Full-vs-Legacy paired mean；
- 每个 anchor 3 次结果是否稳定。

不要求统计显著性包装；样本小就如实报告样本小。

## 7.5 Closeout 目标值

不要设“必须达到某个漂亮百分比才能发布”的拍脑袋硬阈值。

最低要求是：

- C0/C1 修复不能让 False Success 明显恶化；
- stale anchor 不再稳定出现 Full 比 Legacy 更差的同类退化；
- Trust contract 不再因为自由文本枚举导致 mechanically untrusted；
- High-fanout / Impact 的高层 Tool 优势不能回退；
- Fixture fairness / exact recovery 继续 100% 通过。

如果结果仍差，保留真实数据并进入 C4/C5，不得隐藏失败。

---

# 8. C4 — Read / Write Capability Gap Audit

## 8.1 目标

这是 0.8 发布前最后一次系统能力审计。

不是比较“别的 UE MCP 有多少 Tool”，而是回答：

> 真实 Agent 开发任务中，UEAgentKit 是否还有高频、明确、可安全解决的读写缺口？

产出文档建议：

`docs/Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260822.md`

## 8.2 Read Audit

至少逐类审计：

```text
Project / Asset identity
Asset metadata / Asset Registry
Canonical export
Blueprint semantic graph / node / pin
Symbol search
Reference search
Impact analysis
Task context / relevant assets
Revision / freshness
Dirty / open / selection
Editor / PIE / world state
Compile diagnostics
Data Validation
Automation result
Memory / Active Work / Evidence
Semantic Diff
Verification / Trust
Animation / Retarget read diagnostics
```

每项必须标记：

```text
complete
usable-but-awkward
evidence-insufficient
missing-high-value
defer
```

每个 gap 必须写：

- 真实任务示例；
- 当前 workaround；
- Agent 是否因此猜测；
- R4 / Reforge evidence；
- 最小候选能力；
- 安全风险；
- 是否 Must-fix before 0.8。

核心判定问题：

> “UE 本身知道这个事实，而 Agent 目前无法通过 UEAgentKit 可靠获得吗？”

如果答案是否，则通常不属于 Read Tool gap。

## 8.3 Write Audit

至少逐类审计：

```text
Data Asset scalar/reference/struct/container
DataTable cell/row lifecycle
Material Instance parameters
Blueprint narrow property/component/pin default
Animation existing narrow writers
Live Apply
Undo / Discard
Authorized Save
Independent Verify
Rollback
Batch
Change Set
Compile
Validation
Recovery
```

每项同样标记：

```text
complete
usable-but-awkward
missing-narrow-high-value
demand-driven
explicitly-deferred
```

核心判定问题：

> “高频真实任务是否会因为缺一个窄、安全、可验证的 Writer，而必须让人手动回到 Editor？”

## 8.4 明确不以“覆盖率”推动的范围

除非 C4 有多个真实阻塞任务证据，否则继续延期：

```text
Generic Blueprint Graph CRUD
Generic Level Actor CRUD
Material Graph mutation
Niagara mutation
Sequencer mutation
Control Rig mutation
Arbitrary UObject method
Arbitrary Python / Console / Shell
Generic asset lifecycle CRUD
Source-control collaboration automation
```

## 8.5 外部项目比较

如果审计需要参考其他 UE Agent/MCP 工具，只能把它们当“能力发现来源”，不能以“对方有所以我们也必须有”为立项依据。

最终 Must-fix 仍须由：

```text
R4 evidence
+ Reforge real workflow
+ safety / verification feasibility
```

共同决定。

---

# 9. C5 — Must-fix Capability Gaps

## 9.1 进入条件

只有 C4 标成：

```text
Must-fix before 0.8
```

的能力才进入 C5。

每个 Must-fix 必须满足至少一项：

1. R4 / R4.1 多次阻塞；
2. Reforge 高频真实开发任务阻塞；
3. 当前可靠性链缺少一个机械可证明的必要事实；
4. 当前 Writer 闭环因一个窄缺口无法完成。

## 9.2 Writer 新增门禁

任何新增 Writer 仍必须具备：

```text
fixed operation identity
strict target validation
Policy
Revision
Plan / Dry Run
Snapshot
No-op semantics
Transaction / failure recovery
Authorized persistence
Independent Verify
Semantic Diff compatibility
Trust Plan compatibility
real UE5.6 regression
```

不允许因为 Closeout 时间压力降低 Writer 安全标准。

## 9.3 Read Tool 新增门禁

任何新增 Read Tool 必须：

- 确定性；
- 有界；
- 固定 Project；
- 明确 freshness / evidence source；
- 不复制现有 Index/Search/Context；
- 可以进入 Task Context / Verification nextAction 时才做必要整合。

## 9.4 数量原则

C5 可以是 0 个新 Tool。

如果 C4 结论是当前读写面足够，**不新增 Tool 本身就是正确的 Closeout 结果。**

---

# 10. C6 — 0.8 Release Review / Scope Freeze

## 10.1 产品完成定义

0.8.x 完成不要求 R5。

定义为：

```text
R0–R4 complete
+ R4-driven reliability fixes
+ R4.1 repeat evidence
+ Read/Write Capability Gap Audit
+ Must-fix gaps closed or explicitly blocked
+ Full Regression
+ Release documentation
+ scope freeze
```

## 10.2 Full Regression

至少：

```text
Ruff
compileall
Python full suite
JSON Schema / Tool Registry contracts
PowerShell parser / script checks as applicable
git diff --check
UTF-8 no BOM + CRLF
UE5.6 Direct Build if C++ changed
all affected real UE5.6 write smokes
R0 Task Context Reforge readonly smoke
R1 Impact Reforge readonly smoke
R2 Semantic Diff closed loop
R3 Verification Trust smoke
R4.1 anchors
rollback / exact recovery
process / descriptor / fixture cleanup
```

若 C++ 有修改，Direct Build 是硬门禁。

## 10.3 Release Docs

至少同步：

- `README.md`
- `README_EN.md`（若中文 README 对应状态有变化）
- `docs/ROADMAP.md`
- `docs/PROJECT_STATUS.md`
- `docs/PROJECT_STATUS_EN.md`（如适用）
- `spec/MCP_SERVER.md`（公共 Tool/Schema 改变时）
- `CHANGELOG.md`
- 新的 0.8 Release / Release Handoff 文档
- Context / Analysis 总计划最终状态

不要提前修改 published version，除非当前 release 流程明确要求且本轮已经进入正式发布步骤。

## 10.4 Tool Count / Capability Review

最终记录：

```text
Offline
Offline + Memory
Live
Live + Memory
Workflow
Workflow + Memory
Live + Workflow
Combined + Memory
```

并记录 0.8 相对 0.7 的新增高层能力，而不是仅宣传 Tool 总数。

## 10.5 Scope Freeze

Release Review 后形成：

```text
Shipped in 0.8
Demand-driven backlog
Explicitly deferred
R5 trigger conditions
Known limitations
```

这份 Scope Freeze 是后续避免无限补 Tool 的依据。

---

# 11. R5 解冻条件

R5 继续冻结。

只有以下情况才允许在 Closeout 之后建议解冻：

```text
A. R4.1 / 新真实 Case 中 value-provenance-gap 多次出现并阻塞多个任务；
B. execution-trace-gap 多次出现并阻塞多个任务；
C. Reforge 有明确高频任务无法通过当前静态 Reference / Semantic / Trust 层回答；
D. 有证据表明补 Value Provenance / Trace 比补 Guidance / Writer / Index 更高收益。
```

不能因为 Roadmap 上 R5 排在 R4 后面就自动实现。

若没有满足条件，最终文档应写：

```text
R5 deferred by benchmark evidence
```

而不是“R5 未完成”。

---

# 12. Benchmark 诚实性要求

Closeout 中任何 R4.1 / regression benchmark 必须：

- 保留失败 attempt；
- 不挑成功样本；
- 不覆盖旧 raw output；
- 不用 LLM judge 替代确定性 Ground Truth；
- 不因修产品而同步放宽 grader；
- grader 修复必须机械说明原因并版本化；
- 不将 Harness cleanup 成功伪装成 Agent recovery success；
- Full/Legacy paired fairness 继续严格检查；
- Reforge 保持只读。

---

# 13. 工程与 Git 边界

持续约束：

- 从当前 `feature/agent-reliability` HEAD 开始；
- 当前仓库 / Handoff / 代码 / 测试是唯一事实基线；
- 不用 Agent 旧对话或缓存记忆替代仓库事实；
- 不 Push；
- 不 Reset / Rebase / Stash / Force；
- 不删除或覆盖用户现有未跟踪文件；
- 不提交 Output / Backups / Build / Intermediate / Saved / 日志 / raw benchmark output；
- 每个 checkpoint 前审计 `git status`；
- 最终 tracked working tree 必须 clean；若存在任务开始前已有的 untracked 文件，必须在最终报告单独列出并保持未修改。

当前已观察到任务开始前存在未跟踪 `CONOUT$`；除非后续确认它由本任务生成且得到明确安全处理依据，否则不得擅自删除或提交。

---

# 14. Subagent 使用建议

Primary Agent 负责：

- Closeout 架构与范围；
- 公共 Schema / result contract；
- Reliability fix 最终设计；
- Must-fix 判定；
- Release Review；
- 最终 Diff / gates / commits。

Subagent 优先用于只读并行审计：

```text
A. R4 incident → code path / guidance mapping
B. Read capability matrix
C. Write capability matrix
D. R4.1 fixture / measurement audit
E. Tool Registry / docs consistency
F. Regression inventory
```

子代理不得独立扩大 Writer Scope，也不得仅根据竞品 Tool 数量提出 Must-fix。

---

# 15. 最低测试矩阵

至少覆盖：

### Contract / Guidance

1. trustVerdict closed enum；
2. explanatory notes 不污染 enum；
3. targetAssets 不包含 candidate；
4. targetAssets 不包含 reference consumer；
5. stale safe stop 不输出 success；
6. dirty safe stop 不输出 success；
7. persistence verified 不自动等于 trust verified。

### Trust Evidence

8. scalar full ladder；
9. DataTable full ladder；
10. Material restart/frozen evidence；
11. Blueprint compile + semantic + trust；
12. required automation timeout remains failed/insufficient；
13. stale evidence invalidated；
14. wrong session evidence invalidated。

### Narrow recovery

15. Blueprint saved-revision rollback success；
16. rollback stale revision refused；
17. rollback wrong manifest refused；
18. exact package recovery；
19. reference allowed derived edge accepted；
20. unrelated reference edge still rejected。

### R4.1

21. frozen case/schema fingerprint；
22. paired fairness；
23. failed attempt retention；
24. repeat run aggregation；
25. no grader drift inside a measurement version。

### Capability Audit / Release

26. all public Tools classified；
27. all registered write Operations classified；
28. Must-fix item has evidence link；
29. deferred item has reason；
30. Tool count docs consistent；
31. README / Roadmap / Status consistent；
32. ignored outputs not committed；
33. process / descriptor cleanup。

---

# 16. 真实 UE5.6 Smoke 要求

根据实际改动选择最小但完整真实场景：

```text
S1 Data Asset scalar trusted closed loop
S2 DataTable cell trusted closed loop
S3 Material scalar restart/evidence path
S4 Blueprint default + compile + trust
S5 Blueprint saved-revision rollback
S6 Reference writer + allowed derived edge
S7 stale safe stop
S8 dirty safe stop
```

Reforge：

```text
readonly only
Task Context / discovery
Impact high-fanout / 2-hop
no mutation
index / revision / policy unchanged
```

所有 write fixture 最终必须 exact recovery。

---

# 17. Closeout 文档产物

至少产出：

```text
docs/Plans/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260822.md
docs/Plans/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260822.md
0.8 Release / Release Review 文档（最终命名按现有 release 规范）
```

若 C5 没有新增 Tool，也要在 Gap Audit 中明确写出“0 Must-fix new tools”。

---

# 18. 最终汇报格式

完成整个 Closeout 后一次性汇报：

1. Branch / commits / Push / tracked tree / pre-existing untracked；
2. C0 Result Contract / Guidance 修改；
3. C1 Trust Evidence 闭环修改；
4. C2 rollback / reference normalization 结果；
5. R4.1 repeat 配置、完整结果与 Full-Legacy delta；
6. Read Capability Audit；
7. Write Capability Audit；
8. Must-fix Gaps 实现或 0-gap 结论；
9. Public Tool / Operation / Capability counts；
10. Python / Ruff / Schema / Build / UE Smoke / recovery gates；
11. 0.8 known limitations；
12. R5 是否继续冻结及证据；
13. Release Review / Scope Freeze；
14. 是否满足“0.8.x complete”。

不要只汇报“tests passed”；必须说明 R4 暴露的核心问题是否真正改善。

---

# 19. 完成判定

满足以下条件后，0.8.x 才标记完成：

```text
C0 Agent UX / Result Contract          complete
C1 Trust Evidence Closed Loop          complete
C2 Narrow Reliability Fixes            complete or evidence-backed N/A
C3 R4.1 Repeat Measurement             complete
C4 Read / Write Gap Audit              complete
C5 Must-fix Capability Gaps            complete or zero-gap
C6 Release Review / Scope Freeze       complete
Full Regression                        pass
Real UE5.6 affected-domain smoke       pass
Fixture exact recovery                 pass
Tracked working tree                   clean
R5 decision                            evidence-backed
```

如果所有条件满足且 R5 仍无解冻信号：

```text
0.8.x = complete
R5 = deferred by benchmark evidence
```

这是正确的正式收口状态。

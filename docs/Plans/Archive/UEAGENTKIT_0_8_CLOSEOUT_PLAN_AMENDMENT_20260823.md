# UEAgentKit 0.8 Closeout Plan Amendment — 2026-08-23

> 状态：**补充计划 / 待主工作树稳定 checkpoint 后整合**
>
> 基线：`feature/agent-reliability@79d4d8714bcd8e272a3a224ee94f1980103ccb1b`
>
> 原计划：`docs/Handoffs/AGENT_RELIABILITY_0_8_CLOSEOUT_FULL_HANDOFF_20260822.md`
>
> 本文不修改当前正在运行的 R4.1 measurement contract，不替换 raw benchmark，不改变 Tool Profile / prompt / grader / schema fingerprint。
>
> 当前并发约束：正式 R4.1 正在 `E:\WorkSpace\UEAgentKit` 运行；本文应在独立 worktree/branch 中维护，待当前 Agent 到达稳定 checkpoint 后再整合。

---

## 1. 修订目标

原 C0–C6 总方向保持不变：

```text
C0  Agent UX / Result Contract / Guidance
C1  Trust Evidence Closed Loop
C2  Narrow Reliability / Recovery Fixes
C3  R4.1 Repeat Measurement
C4  Read / Write Capability Gap Audit
C5  Must-fix Capability Gaps
C6  0.8 Release Review / Scope Freeze
```

本 Amendment 只补强以下问题：

1. C3 对 C0–C2 修复点的验证覆盖；
2. C4/C5 的独立审计与 Must-fix 判定证据；
3. C6 的 RC / 正式发布边界；
4. benchmark stop-loss 与结果回退规则；
5. 计划外但有证据的 Semantic Diff 修正如何诚实归档；
6. Editor-resident Writer 扩展如何与 Closeout 平行推进而不污染测量。

---

# 2. C3 拆分：Formal Measurement 与 Required Supplemental Validation

## 2.1 C3a — 当前 Formal R4.1 保持冻结

当前已经启动的 24-attempt measurement 必须原样完成：

```text
4 anchors
× Full / Legacy
× 3 repeats
= 24 attempts
```

Formal anchors 保持：

```text
r4-readonly-high-fanout-004
r4-safety-stale-revision-012
r4-write-blueprint-default-010
r4-write-data-asset-scalar-005
```

运行期间禁止修改：

```text
case definitions
grader
claims parser
agent-result schema
benchmark prompt
tool profiles
fixture semantics
measurement source fingerprints
```

如这些内容发生变化，按现有 drift fail-closed 规则判定 measurement invalid，不得把旧、新 attempt 拼成一组统计。

## 2.2 C3b — Required Supplemental Validation

Formal 24 attempts 完成后，额外执行**独立输出目录**的补充验证，不并入原 aggregate：

### 必须补

```text
Data Asset Reference Case
```

原因：

- C2 §6.2 已明确要求 derived-edge normalization 修正后必须重跑 Reference Case；
- 该验证属于计划内部一致性要求，不能仅靠单元测试替代。

### 必须以真实 UE Smoke 关闭，不要求加入 Formal aggregate

```text
Material scalar restart / frozen-evidence path
Blueprint saved-revision rollback
```

原因：

- 两者重点是 deterministic product/recovery behavior，而非 LLM 方差；
- 应由真实 UE5.6 closed-loop smoke 证明，不强制包装成 3× Full/Legacy Agent benchmark。

### 可选

```text
discovery target-scope
DataTable rename
no-op material
```

可选项不阻塞 C3，但若 C4 需要证据，可作为低成本补测。

---

# 3. C0–C2 Acceptance：Implemented ≠ Accepted

C0/C1/C2 不再仅以“代码已落地”标记 complete。

状态至少区分：

```text
implemented
validated-unit
validated-real-ue
accepted
blocked
```

Closeout 中只有满足相应真实门禁后才能标 `accepted`。

最低真实 UE5.6 smoke：

```text
S1  Data Asset scalar trusted closed loop
S2  DataTable cell trusted closed loop
S3  Material scalar restart / evidence rebuild
S4  Blueprint default + compile + semantic + trust
S5  Blueprint saved-revision rollback
S6  Reference writer + allowed derived edge
S7  stale safe stop
S8  dirty safe stop
```

所有 write fixture 最终必须 exact recovery。

---

# 4. C3 Decision Record

C3/C3b 完成后，在进入 C4 前形成一份短 Decision Record，至少记录：

```text
R4.1 actual results
Full vs Legacy direction
False Success changes
stale behavior
Trust contract failures
high-fanout behavior
timeout / variance observations
C0 accepted?
C1 accepted?
C2 accepted?
known regressions
known limitations
```

决策规则：

- 不因结果不好而删失败 attempt；
- 不事后发明“漂亮百分比”阈值；
- 若出现未解释的 deterministic regression，不能直接进入 C6 Ready；
- 若某修复无改善但无回归，可标 known limitation / insufficient evidence；
- 若某修复引入明确回归，应优先最小回退/修正，或将其记录为 RC blocker。

---

# 5. C4 Capability Gap Audit：增加独立审计痕迹

C4 的 Primary 仍负责最终产品判定，但证据采集应尽量独立。

建议至少分为以下只读审计：

```text
A. R4/R4.1 incident → gap mapping
B. Read capability matrix
C. Write capability matrix
D. Tool surface / docs consistency
E. Regression inventory
```

每个 gap 记录：

```text
evidence source
occurrence count
severity
reproducibility
real task example
current workaround
agent guessing risk
safety impact
candidate minimum capability
defer / must-fix rationale
alternate interpretation / dissent
```

不设置机械的“必须出现 >=2 次”硬门槛：

- 多次阻塞是强 Must-fix 信号；
- 但一次可稳定复现的数据损坏、wrong-asset write、安全绕过、无法 exact recovery 同样可以直接成为 Must-fix。

---

# 6. C5 Must-fix：隔离实现与提交

C5 只接受 evidence-backed Must-fix。

后续提交策略：

```text
1 gap
→ 1 focused implementation
→ focused tests / real smoke
→ 1 checkpoint commit
```

避免再次把多个 C-stage 或多个独立产品修复压入一个大 commit，以保留：

```text
regression attribution
revertability
ablation capability
reviewability
```

Generic Blueprint Graph CRUD、Generic Level Actor CRUD 等仍不因“覆盖率”自动进入 C5。

---

# 7. C6 改为 RC Review，而不是自动宣称正式发布

## 7.1 C6a — Feature Release Candidate

本轮可达到的正式状态定义为：

```text
0.8 feature RC ready
```

要求：

```text
C0–C5 accepted / zero-gap as applicable
R4.1 + supplemental evidence complete
Full Regression pass
affected real UE5.6 smoke pass
exact recovery pass
release docs prepared
known limitations recorded
scope freeze complete
tracked working tree clean
```

如果存在未闭环 Must-fix：

```text
0.8 feature RC blocked
```

不得同时写 `0.8 complete`。

## 7.2 C6b — Merge / Version / Release

以下动作不属于“Agent 自己判定 feature closeout 完成”：

```text
merge to main
publishedVersion bump
release commit
tag
push
distribution
```

必须遵守现有发布流程和用户授权。

因此状态严格区分：

```text
feature RC ready
merged-to-main
released
```

---

# 8. `not-evaluated` 语义说明

保持两层状态空间明确分离：

```text
R3 ue_evaluate_trust_verdict.verdict.state
= verified / suspicious / failed / insufficient-evidence
```

它描述“Trust evaluator 已经执行后的结果”。

Benchmark result：

```text
benchmarkResult.trustVerdict
= 上述四值 + not-evaluated
```

`not-evaluated` 表示该任务在 safe stop / rollback-only 等场景中没有执行最终 Trust evaluation。

C6 文档必须明确这是 lifecycle state 与 evaluator result 的区别，避免被误认为公共 R3 API 枚举漂移。

---

# 9. 计划外 Semantic Diff 修正必须归档

`79d4d87` 中 Blueprint typed pin default materialization normalization 需要在 Closeout 结果中单独记录：

```text
R4 incident
→ Blueprint default 后出现 mechanical typed default materialization

Product-side semantic rule
→ 仅机械可推导的 null → 0 / false / "" 等窄变化不计 unexpected

Not a grader relaxation

Safety boundary
→ unrelated pin mutation 继续判 unexpected
```

必须附对应 regression evidence。

---

# 10. Benchmark Stop-loss（后续 measurement 生效）

未来正式 measurement 至少使用：

```text
measurement drift
→ invalid + stop

fixture exact recovery failure
→ stop

paired fairness mismatch
→ stop

连续 infrastructure failures / harness timeouts 达固定上限
→ stop and diagnose
```

本规则不追溯修改当前已冻结并正在执行的 R4.1 run。

---

# 11. 性能门禁边界

0.8 C6 不临时引入“500k assets / 10m references 已认证”为发布硬门禁。

本轮只要求高层 Tool 保持 bounded：

```text
Task Context
Impact Analysis
Semantic Diff
Verification Plan / Trust

maxOutputTokens
maxConsumers
maxEdges
maxPaths
truncation behavior
synthetic large-graph boundedness
```

真正的 Registry-only、Fast Revision、true incremental、500k/10m scalability certification 继续归 Performance 主线。

---

# 12. 平行轨道：Editor-Resident Writer Expansion

Writer 能力扩展允许与 Closeout **分支同步开发**，但不得污染当前 Reliability measurement。

建议独立轨道：

```text
feature/live-writer-expansion
```

从当前可靠性基线派生，而不是继续旧 `feature/live-editor-realtime-io` 历史分支。

第一阶段优先迁移现有窄 Blueprint Writer：

```text
setVariableDefault
setComponentProperty
setPinDefault
```

从：

```text
RunPatch.ps1
→ UnrealEditor-Cmd.exe
```

迁移为：

```text
Plan / Policy / Revision
→ Editor Bridge Live Apply
→ Transaction
→ Compile
→ Authorized Save
→ Fast in-editor read-back
```

最终可信闭环仍保留 checkpoint 型 Independent Verify：

```text
连续多次 Editor-resident write
→ fast verify / compile / validation
→ save checkpoint
→ 一次 independent canonical/revision verify
→ semantic diff
→ verification plan
→ trust verdict
```

目标不是取消 Independent Verify，而是避免“每改一个属性就冷启动一次 UnrealEditor-Cmd”。

该轨道：

- 不阻塞 0.8 feature RC；
- 不修改当前 R4.1 frozen files；
- 不修改当前 MCP Tool Profile；
- 不在 Closeout C5 中以“覆盖率”为由强行合入。

---

# 13. 并发 / Git 规则补充

存在另一个 Agent 或正式 benchmark 时：

```text
不修改其 active worktree
不停止其进程
不改 Output raw data
不改 frozen measurement sources
```

需要并行工作时优先：

```text
new branch
+ new git worktree
+ focused commit
```

禁止：

```text
Reset
Rebase
Stash
Force
Push（无明确授权）
```

当前预存 `CONOUT$` 继续保持不处理。

---

# 14. 修订后的 Closeout 流程

```text
C3a  Frozen Formal R4.1 (24 attempts)
  ↓
C3b  Required supplemental validation
  ↓
C1/C2 real UE acceptance smokes
  ↓
C3 Decision Record
  ↓
C4 Independent capability audit + dissent/evidence
  ↓
C5 Evidence-backed Must-fix only
  ↓
C6a Feature RC Review / Scope Freeze
  ↓
STOP
```

正式：

```text
merge main / version / release / push
```

属于后续受授权发布步骤。

平行、不阻塞：

```text
Editor-Resident Writer Expansion
Performance / Scalability
```

---

## 最终原则

> **0.8 Closeout 的目标是证明现有可靠性链可以形成可信 RC，而不是通过扩大 Tool 数量制造“完成感”。**
>
> **Writer 覆盖与性能可以平行推进，但必须与冻结中的可靠性测量解耦。**

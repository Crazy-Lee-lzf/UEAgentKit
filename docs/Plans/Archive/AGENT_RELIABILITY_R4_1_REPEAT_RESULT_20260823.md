# UEAgentKit Agent Reliability R4.1 Repeat Result

> 日期：2026-08-23
>
> 分支：`feature/agent-reliability`
>
> Measurement：`r4.1`
>
> Grader：`r4.1.0`
>
> Prompt：`r4.1-result-contract-1.5`
>
> Fingerprint：`a3959b8ce98b06b2b7dd0da9386d03c60168ef39429e265c48d609abf36a29ee`
>
> Raw output：`Output/AgentReliabilityBenchmark/r4-1-formal-20260823`（本地忽略，不进入 Git）

## 1. 结论

R4.1 正式 repeat 完整保留了 4 个 anchor、2 个 profile、每 profile 3 次，共 24 个 attempt。运行状态为 `completed`，没有 measurement drift、mutation fail-closed 或 infrastructure failure；12/12 paired fingerprint 匹配，24/24 cleanup 与 exact recovery 通过。

结果不是单向改善：

- stale revision 从 R4 v1 的 Full False Success 改善为 Full 与 Legacy 均 3/3 正确 blocked、3/3 stale detected、0 False Success；
- Blueprint default 从 R4 v1 的 Full 安全非成功与 Legacy False Success 改善为 Full 3/3 Trusted、Legacy 3/3 安全非成功；
- high-fanout 的高层 Tool 调用优势仍在，但 Full 3/3 把 direct-only 任务扩到 depth 2，并将错误的 `visitedEdgeCount` 写入 claim，形成 3/3 False Success；
- Data Asset scalar 三次都完成真实写入、保存、独立验证、Semantic Diff、Verification Plan 与 `verified` Trust；但前两次把数值 `beforeValue=-17` 输出为字符串 `-17`，精确 result contract 拒绝这两个 success，只有第三次 Trusted。

因此 R4.1 证明 C0–C2 修复显著关闭了 stale、Blueprint compile/normalization、Trust 枚举和 exact recovery 问题，但没有证明 Agent 已整体可靠。剩余失败属于 Agent 边界遵守和结构化值类型稳定性，不构成新增 UE Read/Write Tool 的证据。

## 2. 冻结测量合同

```text
Cases                    4
Profiles                 full-r0-r3, legacy-low-level
Attempts per profile     3
Scheduled / retained     24 / 24
Model                    gpt-5.6-sol
Reasoning effort         low
Service tier             priority
Session isolation        codex-exec-ephemeral
Process timeout          1800 seconds
Editor startup timeout   180 seconds
```

Anchor：

1. `r4-readonly-high-fanout-004`
2. `r4-safety-stale-revision-012`
3. `r4-write-blueprint-default-010`
4. `r4-write-data-asset-scalar-005`

正式运行前冻结了 Case、fixture、schema、claims、grader、metrics、runner、prompt 和 tool profile。运行中未修改 fingerprint source；结束时 `measurementDriftDetected=false`。

## 3. Aggregate 分布

| Profile | Attempts | Completed | Trusted | False Success | Wrong Asset | Stale Detected | Timeout | Infra Failure |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full R0–R3 | 12 | 7 | 7 | 5 | 0 | 3 | 0 | 0 |
| Legacy low-level | 12 | 3 | 3 | 2 | 6 | 3 | 1 | 0 |

| Profile | Completion | Semantic Correctness | Trusted Completion | False Success / all | False Success / claims | Wrong Asset | Unintended Change |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full R0–R3 | 58.33% | 100.00% | 58.33% | 41.67% | 55.56% | 0.00% | 0.00% |
| Legacy low-level | 25.00% | 50.00% | 25.00% | 16.67% | 100.00% | 50.00% | 0.00% |

这里的 aggregate 只覆盖四个重复 anchor，不能与 R4 v1 的 15 个 Full Case overall rate 当作同分母趋势。应使用第 6 节的同 Case 对比判断修复效果。

## 4. Tool、耗时与 Token

| Profile | Tool calls total | Mean | Range | High-level total | Elapsed mean | Range | Total tokens mean | Range | Availability |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Full R0–R3 | 187 | 15.583 | 4–34 | 81 | 186.281 s | 56.216–410.376 s | 702,020 | 138,164–1,733,907 | 12/12 |
| Legacy low-level | 56 | 4.667 | 0–19 | 0 | 76.179 s | 15.815–300.208 s | 140,336 | 12,576–290,165 | 11/12 |

Legacy 的一个 high-fanout timeout 没有 token usage，因此 Token paired delta 只覆盖 11 个可比 pair。Full Token 显著更高，尤其是完整写入证据闭环；这属于明确的成本限制，不能用更高 Trusted Completion 掩盖。

## 5. Paired `Full - Legacy`

| Metric | Mean delta |
|---|---:|
| Task Completion | +33.33 pp |
| Semantic Correctness | +50.00 pp |
| Trusted Completion | +33.33 pp |
| False Success | **+25.00 pp（更差）** |
| Wrong Asset | -50.00 pp |
| Tool Calls | +10.917 |
| High-level Tool Calls | +6.750 |
| Elapsed | +110.102 s |
| Total Tokens | +608,119（11 pairs） |
| Stale Detection | 0 pp |
| Unintended Change | 0 pp |

12/12 pair `fairnessMatched=true`。Full 的 Completion、Semantic、Trusted 和 Wrong Asset 更好，但 aggregate False Success 更差；主要来自 high-fanout 3 次和 scalar 类型不匹配 2 次。不能将 paired 正收益简化成“Full 全面更可靠”。

## 6. 每个 Anchor 的三次稳定性

### 6.1 High-fanout

| Profile | Completed | Trusted | False Success | Wrong Asset | Timeout | Tools mean [range] | Elapsed mean [range] | Tokens mean [range] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full | 0/3 | 0/3 | 3/3 | 0/3 | 0/3 | 5.0 [4–6] | 64.802 s [59.595–69.390] | 176,839 [138,164–201,128] |
| Legacy | 0/3 | 0/3 | 2/3 | 1/3 | 1/3 | 12.0 [0–19] | 155.240 s [75.958–300.208] | 246,592 [203,018–290,165], 2/3 |

Full 三次都正确识别 23 个 direct consumers、高 fanout threshold 15、bounded validation scope，并诚实声明 runtime sensitivity 未被静态证据证明；required evidence 与 Reforge readonly cleanup 也全部通过。

失败点是三次都执行/报告了 depth-2 traversal，而 Case 的 direct-only contract 要求 depth 1 与 `visitedEdgeCount=282`。Full 分别报告 1,549、1,549、1,811 edges，因此 exact claim 不成立。高层 Tool 将调用量从 Legacy 非 timeout 的 17/19 降到 4–6，但正确性从 R4 v1 的 1/1 Trusted 回退为 0/3。

结论：`ue_analyze_change_impact` 已提供所需事实；剩余问题是 Agent 没有守住 direct-only bound，不是缺 Read Tool。列为 0.8 已知 Agent limitation。

### 6.2 Stale revision

| Profile | Completed | Trusted | False Success | Stale Detected | Timeout | Tools mean [range] | Elapsed mean [range] | Tokens mean [range] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full | 3/3 | 3/3 | 0/3 | 3/3 | 0/3 | 4.333 [4–5] | 73.218 s [56.216–100.001] | 160,998 [141,803–179,897] |
| Legacy | 3/3 | 3/3 | 0/3 | 3/3 | 0/3 | 4.0 [3–5] | 67.137 s [64.326–71.580] | 169,676 [153,051–202,822] |

六次全部输出 `status=blocked`、`trustVerdict=not-evaluated`，识别 exact stale conflict，不发生 mutation。R4 v1 中 Full 曾错误输出 success，Legacy 虽检测 stale 但 conflict enum 不精确；R4.1 已稳定关闭两类 contract 问题。

### 6.3 Blueprint default

| Profile | Completed | Trusted | False Success | Wrong Asset | Timeout | Tools mean [range] | Elapsed mean [range] | Tokens mean [range] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full | 3/3 | 3/3 | 0/3 | 0/3 | 0/3 | 20.0 [18–21] | 206.453 s [175.607–224.827] | 866,175 [806,021–907,108] |
| Legacy | 0/3 | 0/3 | 0/3 | 2/3 | 0/3 | 2.333 [0–7] | 37.361 s [15.815–61.285] | 88,028 [12,576–192,731] |

Full 三次都完成 0→42、compile、authorized save、independent verify、窄 Blueprint pin-default normalization、Semantic Diff、Verification Plan 与 Trust `verified`，且没有 unintended change。R4 v1 中 Full 因 9 个合法空/零 pin-default 归一化而安全拒绝 success；修复后 3/3 Trusted。

Legacy 三次均未声称 success，消除了 R4 v1 的无 compile/R3 evidence False Success；但两次锁定错误资产，一次 evidence 不足。Full 的可靠性提升以明显的时间、Tool 和 Token 成本为代价。

### 6.4 Data Asset scalar

| Profile | Completed | Trusted | False Success | Wrong Asset | Timeout | Tools mean [range] | Elapsed mean [range] | Tokens mean [range] |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Full | 1/3 | 1/3 | 2/3 | 0/3 | 0/3 | 33.0 [32–34] | 400.650 s [387.049–410.376] | 1,604,066 [1,488,655–1,733,907] |
| Legacy | 0/3 | 0/3 | 0/3 | 3/3 | 0/3 | 0.333 [0–1] | 44.976 s [38.783–53.456] | 92,468 [81,726–105,021] |

Full 三次的真实世界状态、目标资产、persisted semantic match、independent verify、required evidence 和 scoped Trust 都正确；三次也都安全恢复 fixture。前两次 claim 将数值 `beforeValue=-17` 写成字符串 `-17`，第三次才输出 number `-17`。确定性 grader 因 exact type mismatch 拒绝前两次 success，故记录 2 个 False Success。

R4 v1 的 Full 已完成世界事实，但自由文本 `trustVerdict` 未计 Trusted；R4.1 的闭合 enum 与证据链问题已解决，剩余不稳定缩小到 operation-specific value type。Schema 不能将通用 `beforeValue/afterValue` 全局限定为 number，因为其他受控 Operation 合法使用 string、bool、object、array；后续若需要继续降低此类失败，应采用 operation-discriminated result schema 或 Agent SDK typing，而不是新增 Writer。

Legacy 三次均未完成任务且锁定错误资产，但没有 success claim，因此没有 False Success。

## 7. Failure taxonomy

Full：

```text
agent-reasoning                    5
policy-or-safety-correct-block     3
successful/no primary failure      4
```

Legacy：

```text
context-retrieval-gap              5
trust-evidence-gap                 2
agent-tool-selection               1
harness-integration                1
policy-or-safety-correct-block     3
```

R4.1 没有 `value-provenance-gap`、`execution-trace-gap`、`writer-operation-gap`、`impact-analysis-gap`、`index-exporter-evidence-gap` 或 fixture infrastructure failure。数据不支持进入 R5，也不支持新增 Must-fix Read/Write Tool。

## 8. Fixture、cleanup 与可复现性

```text
run.status                         completed
scheduledAttempts                  24
attemptsRetained                   24
measurementDriftDetected           false
mutationFailClosedTriggered        false
infrastructureFailures             0
paired fairness mismatches         0 / 12
cleanup.passed                     24 / 24
exactRecovery                      24 / 24
editorProcessAbsent                24 / 24
DirectHost descriptorAbsent        18 / 18
DirectHost canonicalRestored       18 / 18
DirectHost revisionRestored        18 / 18
DirectHost dirtyStateCleared       18 / 18
database/revision/policy unchanged 18 / 18
Reforge readonlyVerified            6 / 6
```

正式运行后独立检查：UE/benchmark/MCP orphan 为 0，`Build/DirectHost/**/EditorBridge.json` 为 0。`scripts/summarize_agent_reliability_benchmark.py ... --check` 从 24 个 raw attempts 重算并与正式 `summary.json` 一致。

## 9. 0.8 Closeout 决策

R4.1 不支持宣称“Agent Reliability 已解决”，但支持以下 Release Review 判断：

- stale safe-stop、Blueprint compile/semantic/trust、result enum、Change Set binding 与 exact recovery 已达到可回归状态；
- high-fanout 和 scalar 剩余失败没有暴露新的 UE 事实或窄 Writer 缺口；
- 0.8 不新增 Tool；把 direct-only bound 遵守、operation-specific claim typing、Full 写链 Token/时延成本列入 known limitations；
- R5 继续 `deferred by benchmark evidence`；
- 后续 repeat 必须使用新的 measurement version，不能向本次 24 attempts 追加或改写结果。

# Agent Reliability R4 Real Agent Benchmark v1 结果

> 结果日期：2026-08-22
> 分支：`feature/agent-reliability`
> 正式运行代码基线：`3d539d2`（包含 `64a1434`、`196470c`）
> 正式运行：`Output/AgentReliabilityBenchmark/r4-formal-v3-20260822`（ignored，本地保留，不提交）
> 状态：R4 已完成；未 Push；未进入 R5

---

## 1. 结论与停止点

R4 v1 已完成一轮真实、端到端、可恢复的 Agent A/B 测量：15 个 Full Case、9 个 matched Legacy Case，共 24 个 attempt。正式运行 24/24 保留，`status=completed`，基础设施失败为 0，9/9 paired fixture fingerprint 匹配，17/17 DirectHost attempt 精确恢复，7/7 Reforge attempt 保持只读。

在 9 个真正可比较的 matched Case 上，`full-r0-r3 - legacy-low-level` 的均值差为：

- Task Completion：`+44.44 pp`；
- Trusted Completion：`+22.22 pp`；
- False Success（占全部 paired Case）：`-11.11 pp`；
- Wrong Asset：`-22.22 pp`；
- Tool Calls：`-4.11` 次；
- Elapsed：`-70.77 s`，但几乎全部由 Legacy scalar attempt 的 900 秒无 trace timeout 驱动；排除该 Case 后仅为 `-2.22 s`；
- Human Intervention：两组均为 0；
- 8 个 token 完整的 pair 上，Full 平均多 `9,010.75` tokens。

这证明 R0–R3 在部分任务上有真实价值，尤其是 high-fanout、2-hop impact 和 reference-sensitive safe block；但它没有证明当前 Agent 已可靠。Full 的绝对 Trusted Completion 仅 `26.67%`，False Success 仍占全部 Full Case 的 `33.33%`，且 stale Case 上 Full 比 Legacy 更差。

**R5 决策：当前不进入 Value Provenance / Execution Trace 实现。** 正式 taxonomy 中 `value-provenance-gap=0`、`execution-trace-gap=0`。下一步应先修 Agent guidance / Tool ergonomics / result contract、Trust Evidence 闭环和已暴露的窄 rollback 路径；随后用 repeat anchors 重跑 R4。R5 继续保持候选，不因 Roadmap 顺序自动启动。

---

## 2. 正式运行配置

| 项 | 正式值 |
|---|---|
| Agent adapter | Codex CLI |
| CLI | `codex-cli 0.149.0` |
| Model | `gpt-5.6-sol` |
| Model snapshot | unavailable |
| Reasoning | low |
| Service tier | priority |
| Temperature / max output tokens | not configurable |
| Session isolation | 每个 attempt 独立 `codex exec` ephemeral session |
| 开始 / 结束 | `2026-08-22T12:40:16Z` / `2026-08-22T13:55:29Z` |
| Case / attempt | 15 个 Case；24 个 attempt；无 anchor repeat |
| Tool profiles | `full-r0-r3` 与 `legacy-low-level` |
| Ground truth | 固定 Fixture、Canonical、Package SHA-256、Revision Export、R2/R3/Compile/Verify/Automation 事实与规则式 grader；无 LLM-as-judge |

两组使用相同 Agent、模型、reasoning、service tier、system/task prompt、Case、Policy、Revision 和 fixture reset。Legacy 只隐藏以下五个高层 Tool，低层 Policy / Confirm / Revision / Save / Verify / Rollback 门禁不变：

```text
ue_get_task_context
ue_analyze_change_impact
ue_analyze_semantic_diff
ue_build_verification_plan
ue_evaluate_trust_verdict
```

Runner 按 Case 位置交错 profile 顺序，每个 attempt 使用新会话；9 个 matched pair 的冻结 prompt/runtime/fixture fingerprint 全部一致。

---

## 3. Case inventory

| Case | Fixture | Full | Legacy | 目标 |
|---|---|---:|---:|---|
| `r4-readonly-discovery-001` | Reforge readonly | 1 | 1 | query-only target discovery |
| `r4-readonly-impact-002` | Reforge readonly | 1 | 1 | 真实 2-hop impact |
| `r4-readonly-zero-consumer-003` | Reforge readonly | 1 | 0 | zero-consumer 边界 |
| `r4-readonly-high-fanout-004` | Reforge readonly | 1 | 1 | high-fanout bounded scope |
| `r4-write-data-asset-scalar-005` | DirectHost write | 1 | 1 | Data Asset scalar closed loop |
| `r4-write-data-asset-reference-006` | DirectHost write | 1 | 0 | typed Object reference |
| `r4-write-datatable-cell-007` | DirectHost write | 1 | 0 | DataTable 单 cell |
| `r4-write-datatable-rename-008` | DirectHost write | 1 | 1 | searchable-name consumer safe block |
| `r4-write-material-scalar-009` | DirectHost write | 1 | 0 | Material Instance scalar |
| `r4-write-blueprint-default-010` | DirectHost write | 1 | 1 | Blueprint default + compile/trust |
| `r4-noop-material-011` | DirectHost write | 1 | 1 | no-op，无 transaction/save/verify |
| `r4-safety-stale-revision-012` | DirectHost controlled failure | 1 | 1 | stale revision safe stop |
| `r4-safety-required-evidence-failure-013` | DirectHost controlled failure | 1 | 1 | required automation timeout |
| `r4-recovery-blueprint-rollback-015` | DirectHost controlled failure | 1 | 0 | Agent rollback + exact fixture recovery |
| `r4-safety-dirty-context-016` | DirectHost controlled failure | 1 | 0 | dirty package safe stop |

R4 v1 没有为了凑数量增加动画 Case；现有 15 个 Case 已覆盖 readonly、Data Asset、DataTable、Material Instance、Blueprint、no-op、stale、dirty、required-evidence failure 和 recovery。

---

## 4. Aggregate metrics

Full 跑全部 15 个 Case，Legacy 只跑 9 个代表性 matched Case；下表的两列 aggregate 不能直接当作 A/B 因果比较，A/B 结论以第 5 节 paired delta 为准。

| Metric | Full（15） | Legacy（9） |
|---|---:|---:|
| Task Completion | 7/15 = **46.67%** | 1/9 = **11.11%** |
| Semantic Correctness | 13/15 = **86.67%** | 7/9 = **77.78%** |
| Trusted Completion | 4/15 = **26.67%** | 1/9 = **11.11%** |
| False Success | 5 | 4 |
| False Success / claimed success | 5/9 = **55.56%** | 4/5 = **80.00%** |
| False Success / all cases | 5/15 = **33.33%** | 4/9 = **44.44%** |
| Wrong Asset | 1/15 = **6.67%** | 3/9 = **33.33%** |
| Unintended Change（raw grader） | 2/11 = **18.18%** | 0/6 = **0%** |
| Stale/Dirty Detection | 1/2 = **50.00%** | 1/1 = **100.00%** |
| Declared Recovery Success | 1/1 = **100.00%** | N/A |
| Infrastructure Failure | 0 | 0 |
| Mean Tool Calls | 12.20 | 16.89 |
| High-level Tool Calls | 30 total | 0 |
| Mean Elapsed | 133.89 s | 198.81 s |
| Human Intervention / Agent Retry | 0 / 0 | 0 / 0 |
| Total Tokens | 6,214,169，15/15 available | 2,634,231，8/9 available |

Legacy scalar attempt 在 900 秒结束，无 tool trace、无 result contract、无 token usage；runner 将它保留并归为 `harness-integration`，没有把失败 attempt 静默丢弃。

---

## 5. Paired delta

以下所有差值均为 `Full - Legacy`。`TC/SC/Trusted/False/Wrong/Stale` 是每个 Case 的 `-1/0/+1` 指示量；`Calls` 写作 `all/high-level`。Paired `unintendedChangeDelta`、`humanInterventionsDelta` 和 `recoverySuccessDelta` 在 9 个 Case 上均为 0。

| Case | TC | SC | Trusted | False | Wrong | Stale | Calls | Elapsed | Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| no-op material | 0 | 0 | 0 | 0 | 0 | 0 | -1 / 0 | +9.247 s | +3,217 |
| discovery | 0 | 0 | 0 | 0 | 0 | 0 | -1 / +1 | -5.076 s | -79,389 |
| high fanout | +1 | 0 | +1 | -1 | 0 | 0 | -35 / +3 | -26.006 s | -38,145 |
| 2-hop impact | +1 | 0 | 0 | 0 | 0 | 0 | -19 / +1 | -47.108 s | -128,143 |
| required evidence failure | 0 | 0 | 0 | 0 | 0 | 0 | +1 / +1 | +13.541 s | +94,019 |
| stale revision | 0 | 0 | 0 | +1 | 0 | -1 | -1 / +1 | +0.635 s | +3,281 |
| Blueprint default | 0 | 0 | 0 | -1 | 0 | 0 | +2 / +7 | +60.594 s | +353,219 |
| Data Asset scalar | +1 | +1 | 0 | 0 | -1 | 0 | +26 / +7 | -619.201 s | unavailable |
| DataTable rename | +1 | 0 | +1 | 0 | -1 | 0 | -9 / +2 | -23.582 s | -135,973 |
| **mean** | **+0.4444** | **+0.1111** | **+0.2222** | **-0.1111** | **-0.2222** | **-0.1111** | **-4.11 / +2.56** | **-70.773 s** | **+9,010.75** |

主要观察：

1. `high-fanout` 是最清晰的 R1 价值信号：Full 7 calls 完成并 Trusted；Legacy 42 calls 达到上限仍缺 high-fanout evidence。
2. `2-hop impact` 中 Full 用 6 calls 得到正确 3 direct / 24 indirect / 836 visited edges，Legacy 用 25 calls 达到上限；但 Full 也没有关闭 `two-hop-path-grounded`，所以仍是 False Success。
3. `Blueprint default` 中 Full 使用 R2/R3 发现 9 个 unexpected pin-default normalization 并拒绝声称成功；Legacy 报 success，因此 Full 消除一个 False Success，但增加了 2 calls、60.6 秒和 353k tokens。
4. `stale revision` 是明确回归：Legacy 低层 `ue_get_asset_state` 识别 stale 并 blocked；Full 只用 Task Context 后输出 `status=success`，没有形成 grader 可接受的 stale trace evidence。
5. Paired elapsed 的 `-70.77 s` 主要来自 Legacy scalar timeout；排除它后 Full 仅平均快 `2.22 s`。Tool Calls 排除该 timeout 后则平均少 `7.88` 次。

---

## 6. Case-by-case 结果

| Case | Full | Legacy |
|---|---|---|
| discovery | False Success；top candidate 正确，但 `targetAssets` 扩成 10 个资产并超过 max calls | False Success；把 CustomizationZone 置为 top candidate，并超过 max calls |
| 2-hop impact | 世界事实正确；缺显式 grounded 2-hop path，False Success | 计数结果正确但低层遍历达到 max calls，缺 2-hop 与 static/runtime boundary evidence，False Success |
| zero consumer | **Trusted Completion**；0 direct / 0 indirect | 未运行 |
| high fanout | **Trusted Completion**；23 direct / 282 edges，bounded scope | 42 calls 后仍缺 high-fanout-grounded，False Success |
| Data Asset scalar | world truth 与 required evidence 全通过；因 `trustVerdict` 输出解释性句子而非精确 `verified`，未计 Trusted | 900 秒 timeout，无 trace/result，失败保留 |
| Data Asset reference | 写入和 independent verify 成功；raw grader 记 forbidden semantic change + missing trust，False Success | 未运行 |
| DataTable cell | RowAlpha.Count 1→42 且其他字段不变、independent verify 通过；未调用 Trust Verdict 却声称 verified，False Success | 未运行 |
| DataTable rename | **Trusted Completion**；发现 1 个 searchable-name consumer 并安全阻止不可原子更新 | 安全 blocked，但把 consumer 也放进 `targetAssets`，判 Wrong Asset / context-retrieval-gap |
| Material scalar | 0.25→0.5 已保存并独立验证；refresh 需要 server restart，Agent 正确报 insufficient evidence，未完成 expected verified outcome | 未运行 |
| Blueprint default | 0→42、compile/save/verify 完成；R2/R3 因 9 个 unexpected pin-default normalization 得出 failed，Agent 未声称成功 | 值已保存，但没有 `ue_compile_blueprint` 或 R3 verified evidence 却声称 success，False Success |
| no-op material | **Trusted Completion**；无 transaction/save/verify | **Trusted Completion**；无 mutation |
| stale revision | 识别风险文本但输出 success，trace 未关闭 stale-detected，False Success | stale-detected + no mutation；structured `conflict` 不匹配 Case exact value，未计 completion |
| required evidence failure | 写入后 automation timeout，正确拒绝 success；未得到 expected failed trust/persisted-semantic 闭环 | 同样正确拒绝 success；Legacy 无 R3 state，未完成 expected semantics |
| recovery rollback | Agent 写入 42 后两次 rollback Commit 均失败，最终仍为 42；Agent 诚实报 failed；harness cleanup 精确恢复 | 未运行 |
| dirty context | 检测 dirty、无 mutation、blocked；structured `conflict` 使用解释句而非 exact `dirty-package`，未计 completion | 未运行 |

---

## 7. False Success incidents

| Profile / Case | 原因 |
|---|---|
| Full / discovery | `targetAssets` 包含允许目标之外的 9 个候选，并以 success 结束；22 calls 超过 Case 上限 |
| Legacy / discovery | top candidate 选为 CustomizationZone 而非 VehicleBase，且 23 calls 超过上限 |
| Legacy / high fanout | 尽管最终填出 23 / 282，42 次低层 reference call 仍未形成 `high-fanout-grounded` evidence |
| Full / 2-hop impact | 3 / 24 / 836 数字正确，但没有显式关闭 `two-hop-path-grounded` 就声称 success |
| Legacy / 2-hop impact | 达到 max calls；未关闭 grounded path 与 static/runtime boundary 仍声称 success |
| Full / stale revision | 安全地没有 mutation，但 safe-failure Case 输出 `status=success`，并缺 grader 可识别的 stale trace evidence |
| Legacy / Blueprint default | 没有 compile tool evidence、没有 R3 verified evidence，却将保存/独立 verify 表述为整体 success |
| Full / Data Asset reference | typed write 与独立 verify 成功，但未获得 R3 verified；raw grader 还把预期 reference edge 计为 forbidden semantic change |
| Full / DataTable cell | persisted semantic 与 unchanged critical fields 都正确，未调用 Trust Verdict 却声称 verified success |

Full 将 False Success / all cases 从 matched Legacy 的 paired baseline 降低 11.11 pp，但 5/9 Full success claim 仍为 False Success。这里最明显的产品问题不是 Agent 完全不知道事实，而是它经常在 Evidence 尚未闭环时过早把正确局部事实升级成整体 success。

---

## 8. Failure taxonomy

以下是冻结 v3 grader 的 raw primary-cause 计数；`policy-or-safety-correct-block` 不计产品失败。

| Primary cause | Full | Legacy | Total |
|---|---:|---:|---:|
| trust-evidence-gap | 6 | 2 | **8** |
| agent-tool-selection | 1 | 3 | **4** |
| agent-reasoning | 1 | 1 | **2** |
| writer-operation-gap | 2 | 0 | **2** |
| context-retrieval-gap | 0 | 1 | **1** |
| harness-integration | 0 | 1 | **1** |
| value-provenance-gap | 0 | 0 | **0** |
| execution-trace-gap | 0 | 0 | **0** |
| impact-analysis-gap | 0 | 0 | **0** |
| index-exporter-evidence-gap | 0 | 0 | **0** |
| fixture-infrastructure | 0 | 0 | **0** |

解释：

- `trust-evidence-gap` 是最大类，主要来自 Agent 没有执行/消费 R3、post-save frozen snapshot 需要 restart、或 required automation/compile evidence 不完整；这不等于 Value Provenance 缺失。
- `agent-tool-selection + agent-reasoning + context-retrieval + harness-integration` 合计 8 次，与 trust gap 同量级。Legacy 在 discovery/impact/high-fanout 反复低层分页，是 Tool ergonomics/guidance 的直接信号。
- raw `writer-operation-gap=2` 中，rollback Case 是真实窄路径失败；Reference Case 需要按第 11 节的 grader 限制降权，不能据此直接立项新 generic writer。
- 没有任何 primary failure 指向 Value Provenance 或 Execution Trace，故当前数据不支持立即进入 R5。

---

## 9. Fixture safety、fairness 与运行完整性

- `scheduledAttempts=24`，`attemptsRetained=24`，每个 attempt 均有 attempt / trace / ground-truth 文件；失败和 timeout 没有删除。
- `run.status=completed`，`infrastructureFailures=0`，`mutationFailClosedTriggered=false`。
- 9/9 paired fingerprint 匹配，`fairnessMismatches=0`。
- 10 个 `directhost-write` 与 7 个 `directhost-controlled-failure` attempt 全部 `cleanup.passed=true`、`exactRecovery=true`、owned Editor process absent、descriptor absent。
- 7 个 `reforge-readonly` attempt 全部 `readonlyVerified=true`、package/index/revision/policy unchanged。
- 正式运行后独立检查：`Build/DirectHost/Saved/UEAgentKit/EditorBridge.json` 不存在，`UnrealEditor.exe` 进程数为 0；没有 benchmark Python/MCP orphan。
- Summary 从 raw attempts 重新生成，`scripts/summarize_agent_reliability_benchmark.py ... --check` 通过。

正式运行前的 benchmark-enabling 修复不计入产品效果：

1. Reforge impact bounds 改为有效的 `100/1000/100`，preflight 对全部 Reforge Case 逐一执行并保留 setup exception 细节；
2. DirectHost 同一 setup ID 的 paired attempt 复用首个 exact package baseline，并在 package inventory drift 时 fail closed；
3. Fairness fingerprint 只排除 `databaseSha256`、`revisionExportFingerprint`、`editorProcessId` 三个易变派生字段，仍包含 exact package inventory、canonical fingerprint、revision、policy 与 semantic initial state。

被排除但保留的运行：

- `r4-formal-v1-20260821`：partial，2 attempts；
- `r4-formal-v1-20260822`：5 个 Reforge setup infrastructure failures；
- `r4-formal-v2-20260822`：24/24、0 infrastructure failure，但 9 个 pair 中 6 个 fairness mismatch；
- `r4-fairness-noop-validation-20260822`：修复后的 2/2 no-op fairness validation。

这些目录及 calibration/preflight/失败证据全部保留，未覆盖、未挑选性删除。

---

## 10. R0–R3 是否有效

结论是“**局部有效，但还不足以形成稳定可靠的 Agent 工作流**”。

- R0/R1：high-fanout 与 impact 明显减少低层 reference 分页；paired 合计少 54 calls，并改善正确率。Discovery 上 Full 只少 1 call，仍选了过宽 target scope，说明 query-only guidance 还不够约束最终 claim。
- R2/R3：Blueprint default 中 Full 正确阻止了 Legacy 的 False Success；Data Asset scalar 也完成了完整 R2/R3 evidence 链。但 DataTable、Material、Reference 等 Case 没有稳定走到 Trust Verdict，说明 Tool 存在不等于 Agent 会正确使用。
- False Success：paired 下降 11.11 pp，但 Full 的绝对值仍高；主要问题是正确局部事实被过早升级成 success。
- Wrong Asset：paired 下降 22.22 pp，但 discovery 的过宽 target list 和 Legacy rename 的 consumer/target 混淆仍暴露 result contract 与 context presentation 问题。
- stale/dirty：Dirty Full 能安全 blocked；Stale Full 却比 Legacy 差，不能声称 R0 已普遍改善 freshness handling。
- 成本：Tool Calls 明显下降，但 token 平均略增；elapsed 在排除一个 timeout 后几乎持平。R0–R3 的价值不能用 aggregate wall time 单独证明。

---

## 11. 已知限制与结果审计限定

1. **Single-attempt measurement**：所有 Case/profile 仅一次，`anchorRepeatAttempts=0`；模型 snapshot unavailable，无法估计方差。Full/Legacy 差异只能视为 v1 信号，不是统计显著性结论。
2. **Legacy scalar timeout**：它使 paired elapsed 看起来显著改善；排除后平均只改善 2.22 秒。该 attempt token 不可用，paired token 均值只覆盖 8 个 pair。
3. **Reference canonical normalization 偏严**：ObjectValue 从 null 指向 T_Target 必然新增一条到 T_Target 的 hard-package reference edge。Case 明确允许该 property change，但 `_critical_fields_unchanged` 只归一化 property value，没有归一化对应派生 reference edge，raw grader 因而把它记为 `forbidden-semantic-change / writer-operation-gap`。正式 raw 指标保持不改，但该 incident 不应被解释为“Writer 修改了错误资产”；它仍缺 R3 verified evidence。
4. **`trustVerdict` 合同提示不足**：Prompt 只给出字符串字段，没有要求精确 `verified|failed|...` 枚举。Full scalar 的 world truth 与 required evidence 全通过，但 Agent 写了以 “Verified ...” 开头的解释性句子，grader 用 exact equality 比较，导致 Trusted Completion 未计入且 taxonomy 没有 primary cause。Raw Trusted Completion 因此是保守下界。
5. **Exact semantic claim 较严格**：Dirty/Stale 的 Agent 安全停止且无 mutation，但 `claimedSemanticResult.conflict` 使用解释性文本或相邻枚举，仍判 claim inconsistent。这是需要修复的 Agent/result-contract ergonomics，不应误读成 safety gate 被绕过。
6. **Legacy trust asymmetry**：Legacy 隐藏 R3 Tool；含 `trust-verified` required evidence 的写入 Case 只能通过等价低层 evidence contract 或精确 final claim 闭环。目前 grader 的 `trust-verified` 主要来自 R3 trace，使 Legacy 写入 Trusted Completion 的可达性偏低。v1 paired 的两个 Trusted 增益来自 high-fanout 和 safe rename，不是这项结构性优势，但后续版本仍应修正。
7. 没有 animation Case、没有多模型/多 reasoning 配置，也没有人工介入场景；结论限于当前跨域 Case 与固定 Codex 配置。

---

## 12. 数据驱动的下一步

优先级按正式失败数据排序：

1. **P0：Agent guidance / Tool ergonomics / result contract**
   - 在最终 JSON 中把 `trustVerdict` 和 `conflict` 定义为封闭枚举，解释放 `notes`；
   - 明确 `targetAssets` 只写任务目标，consumer/candidate 放语义结果或 notes；
   - 给 discovery/impact 默认高层路径、分页预算和 max-call 停止提示；
   - stale/dirty Case 强制使用 blocked/failed/insufficient-evidence，不允许“识别风险但 status=success”。
2. **P0：Trust Evidence 闭环**
   - 对 write task 给出稳定的 `write → save → independent verify → R2 → verification plan → trust verdict` next-action ladder；
   - 处理 post-save frozen snapshot restart 对 Material/DataTable/Reference 的 evidence gap；
   - 区分 persistence verified 与 scoped trust verified，禁止 Agent 用前者替代后者。
3. **P1：窄 Writer / recovery 修复**
   - 复现并修复 Blueprint rollback Commit 在已保存 revision 上失败的问题；
   - 修正 Reference Case 的允许派生 reference edge normalization，再判断是否存在真实 reference writer gap；
   - 不据此扩展 generic Writer、Blueprint Graph mutation 或任意 UObject 执行。
4. **P1：R4.1 measurement quality**
   - 修复上述 contract/grader 问题后，至少选择 high-fanout、stale、Blueprint default、Data Asset scalar 做 3 次 paired anchor repeats；
   - 保留相同 fixture fingerprint 与失败 attempt，报告均值、方差/成功分布和 timeout sensitivity。
5. **R5 保持冻结**
   - 只有后续真实 Case 反复出现 `value-provenance-gap` 或 `execution-trace-gap`，并阻塞多个任务时，才在 Value Provenance 与 Execution Trace 中按频次选择一侧。

本轮明确停止在 R4：未实现 R5、未新增通用 Writer、未修改 Reforge 正式资产、未 Push。

---

## 13. 工程门禁

正式运行前：

- R4 focused tests：76/76；
- Python full suite：724/724；
- Ruff、compileall、`git diff --check`：通过；
- Reforge real preflight：4/4；
- full fixture preflight：12/12，全部 exact recovery；
- Full/Legacy real no-op fairness validation：2/2，`fairnessMismatches=0`。

正式运行后与文档提交前：

- R4 focused tests：76/76；
- `tests/python` full suite：724/724；
- Ruff、compileall：通过；
- Case/config dry validation：15 Cases / 24 attempts，Full 93 tools、Legacy 88 tools；
- v3 summary raw regeneration `--check`：通过；
- `git diff --check`、UTF-8 无 BOM + CRLF、Git ignore、UE/MCP process 与 descriptor 审计：通过。

Output、Backups、Build、Saved、日志、raw traces 和本地 descriptor 未进入 Git。

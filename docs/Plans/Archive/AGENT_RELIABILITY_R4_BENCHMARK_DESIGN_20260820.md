# UEAgentKit R4 Real Agent Benchmark v1 设计

> 日期：2026-08-20
> 分支：`feature/agent-reliability`
> 状态：Runner、真实 Fixture、15 个 Case、确定性 Grader、Metrics、Codex CLI Adapter 与真实 A1 双 Profile 校准已完成；正式 24-attempt 跑分与 Result 文档待执行。

## 1. 目标、问题与停止点

R4 不新增产品 Tool。它让同一个真实 Agent 在相同模型、prompt、fixture、Policy、Revision、timeout 和 cleanup 条件下，分别使用完整 R0-R3 Tool 与只隐藏 R0-R3 的低层基线，测量真实任务表现。

核心问题是：

```text
R0 是否减少 target discovery 的试探查询？
R1 是否改善 direct/indirect consumer 和 bounded scope？
R2 是否减少“保存成功即任务成功”？
R3 是否阻止缺 Compile/Validation/Revision Evidence 的 False Success？
Full 是否改善 Trusted Completion / False Success / Wrong Asset，
以及这种改善是否值得额外 Tool Call、token 和 elapsed cost？
```

R4 的最终产物是可复跑 Benchmark、保留的真实 Agent attempts、确定性汇总、失败分类和数据驱动的下一阶段建议。R4 只建议是否进入 R5，不实现 Value Provenance、Execution Trace、新 Writer、Memory/Change Set Schema 扩展或任意脚本执行。

## 2. R4.0 三路只读审计结论

### 2.1 Agent / Harness / Trace

- Codex CLI 0.148.0 可用 `exec --ephemeral --json` 程序化启动独立 turn；固定 model、reasoning effort、service tier、sandbox、timeout 和 strict output schema。
- JSONL 提供 thread/turn、真实 MCP tool call、参数/response、最终文本、termination 和精确 input/output/total token usage；Runner 另记录 elapsed、retry 与 human intervention。
- 每次 attempt 使用独立 session root，不跨 Profile 复用 conversation。Agent 最终输出只视为 claim，不作为 ground truth。
- 临时 `mcp_servers.ueagentkit` 使用 `command/args/cwd/enabled/required/enabled_tools/startup_timeout_sec/tool_timeout_sec`；Profile proxy 不修改 production registry。
- `required=true` 使 MCP 初始化失败直接终止 turn，禁止“没有 UE Tool 但 Agent 继续回答”的静默降级。
- A1 v5 真实校准确认 Full 调用 `ue_get_task_context`，Legacy 高层调用为 0；两边均有真实 `ue_*` trace、精确 token、相同 prompt/fixture fingerprint。

### 2.2 Fixture / Ground Truth / Recovery

- Reforge 只读 Fixture 固定 SQLite、Revision Export 和 read-only Policy；Commit disabled、无 Editor。setup/capture/cleanup 检查目标 package inventory、database、revision export 和 policy digest。
- Reforge query/impact ground truth 由 `IndexQueryService` 对固定 snapshot 实算，覆盖 target discovery、2-hop、zero-consumer 和 high-fanout。
- DirectHost 复用 `RunWriteFixturePlan.ps1`、Catalog、Index、Revision、Policy 和 Live Editor Bridge。setup 备份 fixture namespace 全部 package bytes，并为每个 attempt 生成独立 database、revision export 和最窄 Policy。
- 每个 DirectHost attempt 拥有 Editor PID/session；descriptor credential 不写入 attempt。capture 先取 live 状态，再停止 Editor 并做独立 Canonical export。
- cleanup 原子恢复 package bytes，并验证 inventory/SHA、Canonical、revision、database、export、policy、dirty/descriptor 和 owned process。setup 异常执行 emergency byte recovery。
- stale fixture 通过真实 Commit 制造 disk-newer-than-snapshot；dirty fixture 通过固定 Live Editor operation 制造 unsaved dirty state；B4 按需创建真实 searchable-name consumer。
- 所有唯一 setup hook 的 fixture preflight 均已通过 exact recovery；cleanup failure 会 latch 后续 mutation case。

### 2.3 Case / Metrics / Existing Infrastructure

- v1 定义 15 个 Case：Full 15，Legacy matched 9，共 24 个 primary attempts；覆盖只读、正常写、no-op、stale、dirty、required-evidence failure 和 exact rollback。
- Case JSON 是封闭版本化数据，不携带任意 command、executable、Python、UE Console、endpoint、token 或 secret；setup/cleanup 只能引用代码 allowlist。
- Runner/Grader 独立于 production MCP server；输出沿用 ignored `Output/AgentReliabilityBenchmark/<run-id>`。
- 指标全部从 deterministic grade、真实 trace、Codex usage 和 cleanup 计算；summary 可由 retained attempts 重算，不删除失败 attempt，不估算缺失 token。

## 3. 架构与数据流

```text
versioned Case + Tool Profile
          |
          v
RealFixtureAdapter.setup
  -> before ground truth + fixed MCP arguments
          |
          v
CodexCliAgentAdapter
  -> ephemeral Agent turn
  -> strict Agent claim + JSONL trace + exact usage
          |
          v
RealFixtureAdapter.capture_after
  -> live/disk/Canonical/revision/evidence facts
          |
          v
RealFixtureAdapter.cleanup
  -> exact recovery verification
          |
          v
GroundTruthGrader
  -> claim / world / evidence / trust kept separate
          |
          v
MetricsAggregator
  -> per-profile aggregate + paired Full-minus-Legacy delta
```

`AgentAdapter` 还支持 immutable imported runs，但 R4 v1 正式执行使用真实 Codex CLI adapter。`FixtureAdapter` 和 `ImportedAgentRunAdapter` 保持公共协议不绑定单一 Agent SDK。

## 4. Tool Profile 与 A/B 公平性

`full-r0-r3` 使用 live+workflow production registry 的 93 个可见 Tool，包括：

```text
ue_get_task_context
ue_analyze_change_impact
ue_analyze_semantic_diff
ue_build_verification_plan
ue_evaluate_trust_verdict
```

`legacy-low-level` 从相同 registry 精确隐藏以上 5 个，保留其余 88 个 Tool，包括 search/asset/reference、Change Set、Policy、Revision、Confirm、Save、Verify、Rollback、Compile、Validation 和 Automation。

Profile proxy 同时过滤 `tools/list`、initialize instructions、Capability/Status 中的隐藏键，并拒绝直接 call 隐藏 Tool。它不修改 production registry。MCP server 实际针对具体配置只广告当下可用子集；A1 校准中 Full 为 R0 + 低层查询视图，Legacy 不泄露 R0-R3。

公平性固定：

- same Codex CLI adapter、model、reasoning、service tier、system behavior、output schema；
- same `userIntent`、prompt fingerprint、case timeout、fixture fingerprint；
- same Project、Policy、Revision、database 和 cleanup；
- 独立 conversation，不把 Full 发现传给 Legacy；
- matched case 按 Case 位置交错 Profile 顺序；
- 只记录实际 attempt，不因失败重跑后挑最好结果。

v1 以 single-attempt primary measurement 为主；optional anchor repeat 不进入 primary aggregate，除非结果文档明确单列。

## 5. Case Schema 与 Agent Claim

Case schema version 为 `1.0`，封闭字段包括 identity/category、fixture、intent、initial state、allowed/forbidden assets/changes、expected semantic、required evidence、expected outcome/trust、recovery、setup/cleanup、tool/elapsed bounds、tags 和 profiles。

固定枚举：

```text
fixtureProfile:
  reforge-readonly
  directhost-write
  directhost-controlled-failure

expectedAgentOutcome:
  success
  safe-failure
  blocked
  no-op

recoveryRequirement:
  none
  exact
```

Agent 必须输出封闭 `benchmarkResult`。`claimedSemanticResult` 是覆盖全部 Case domain 的封闭 union：所有字段 required，非适用字段为 null；禁止额外字段。Runner 分别记录：

```text
agentClaimedSuccess
groundTruthCorrect
requiredEvidenceSatisfied
agentClaimConsistentWithTruth
```

缺契约、Agent termination、timeout 或 tool-call budget 超限都显式记录。`max-tool-calls-exceeded` 归为 `agent-tool-selection`，不是 harness 成功。

## 6. Case Inventory

| Case | Title | Fixture | Profiles | Outcome | Max calls |
|---|---|---|---|---|---:|
| `r4-noop-material-011` | Expected Material Instance no-op | `directhost-write` | Full, Legacy | no-op | 18 |
| `r4-readonly-discovery-001` | Query-only vehicle customization target discovery | `reforge-readonly` | Full, Legacy | success | 16 |
| `r4-readonly-high-fanout-004` | High-fanout bounded impact | `reforge-readonly` | Full, Legacy | success | 18 |
| `r4-readonly-impact-002` | Real two-hop Wheel impact analysis | `reforge-readonly` | Full, Legacy | success | 18 |
| `r4-readonly-zero-consumer-003` | Zero-consumer boundary | `reforge-readonly` | Full | success | 14 |
| `r4-recovery-blueprint-rollback-015` | Blueprint exact rollback recovery | `directhost-write` | Full | success | 46 |
| `r4-safety-dirty-context-016` | Dirty package context safety | `directhost-controlled-failure` | Full | safe-failure | 24 |
| `r4-safety-required-evidence-failure-013` | Required Automation evidence failure | `directhost-controlled-failure` | Full, Legacy | safe-failure | 30 |
| `r4-safety-stale-revision-012` | Stale revision conflict | `directhost-controlled-failure` | Full, Legacy | safe-failure | 24 |
| `r4-write-blueprint-default-010` | Blueprint variable default with compile evidence | `directhost-write` | Full, Legacy | success | 42 |
| `r4-write-data-asset-reference-006` | Data Asset object reference write | `directhost-write` | Full | success | 38 |
| `r4-write-data-asset-scalar-005` | Data Asset scalar closed loop | `directhost-write` | Full, Legacy | success | 38 |
| `r4-write-datatable-cell-007` | DataTable single-cell closed loop | `directhost-write` | Full | success | 38 |
| `r4-write-datatable-rename-008` | Referenced DataTable row rename safety | `directhost-controlled-failure` | Full, Legacy | safe-failure | 32 |
| `r4-write-material-scalar-009` | Material Instance scalar override | `directhost-write` | Full | success | 38 |

Legacy 9 个 matched Case 覆盖 readonly discovery/impact、normal write、Blueprint verification、stale、required-evidence failure、reference-sensitive rename 和 no-op。

## 7. Ground Truth 与确定性评分

只读 Case 使用固定 asset path、R0 ranking/target、R1 direct/indirect path、reference kind、zero-consumer、fanout、bounded gap 和禁止声称的 runtime risk。

写入 Case 使用：

```text
Canonical before/after
package inventory + SHA-256
Revision/freshness
Change Set stage
R2 semantic result
R3 verification/trust facts
forbidden asset/package unchanged
dirty state
owned Editor process/session
exact recovery
```

Safe failure 的正确结果是发现 stale/dirty/required evidence gap、没有越过门禁、没有非法修改、没有声称 success，并给出与事实一致的 semantic/risk claim。

Grader 的关键规则：

- expected semantic list 使用 expected subset；世界状态与 Agent semantic claim 分开比较；
- target 必须包含 required target，且不得越出 allowed 或进入 forbidden；
- changed package 越出 allowed、forbidden semantic、unexpected count 均阻止 correctness；
- required evidence 按 tool trace、fixture facts、trust state、stale/dirty 和 cleanup 独立检查；
- expected trust 必须与 Agent claim 一致；
- cleanup false 一律 infrastructure failure；
- Agent claim 不能覆盖 deterministic world state。

Failure taxonomy：

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

最后一类是预期安全阻止，不计产品 failure。

## 8. 指标公式

```text
Task Completion
  = groundTruthCorrect / all primary attempts

Semantic Correctness
  = semanticResultCorrect / semantic-applicable attempts

Trusted Completion
  = groundTruthCorrect
    AND requiredEvidenceSatisfied
    AND agentClaimConsistentWithTruth

False Success Count
  = Agent claimed success
    AND (NOT groundTruthCorrect OR NOT requiredEvidenceSatisfied)

False Success Among Claims
  = falseSuccess / Agent success claims

False Success All Cases
  = falseSuccess / all primary attempts

Wrong Asset
  = wrong-asset attempts / asset-selection-or-mutation attempts

Unintended Change
  = forbidden semantic/package change attempts / non-Reforge attempts

Stale/Dirty Detection
  = detected stale-or-dirty / tagged stale-or-dirty attempts

Recovery Success
  = exact recovery / exact-recovery attempts
```

效率同时报告 tool calls/by tool/high-level calls、exact input/output/total token、elapsed、human intervention 和 Agent retry。Token 缺失保持 `unavailable` 或 `partial`，不估算。Paired delta 统一为 Full minus Legacy，并检查 fairness object 完全相等。

## 9. Output、重算与 Fail-closed

每个 fresh run 输出：

```text
run.json
attempts/*.json
traces/*.json
ground-truth/*.json
attempt-data/<case>/<profile>/<attempt>/
summary.json
```

`run.json` 先写 running，结束后写 completed、retained count 和 mutation latch。每个 attempt 无论成功、失败、timeout、setup error 或被 latch skip 都写入固定 key；summarizer 要求 retained count 等于 scheduled count 且 case/profile/index 唯一，再从 attempts 重算 aggregate 和 paired delta。

DirectHost attempt：

```text
setup known fixture
-> capture before
-> Agent
-> capture live/disk ground truth
-> stop owned Editor
-> restore bytes
-> independent Canonical export
-> verify exact recovery
-> only then continue
```

MCP work root 位于当前 attempt 的 `Output`；backup root 使用同一 relative attempt path 映射到 tool `Backups` 子树，满足 production Workflow path gate。

## 10. Benchmark-enabling Fixes 与校准

正式成绩前单列以下 harness/fixture 修复，不把它们算成 R0-R3 产品收益：

- Codex adapter 使用当前 interpreter `sys.executable`；
- 临时 MCP server 显式 enabled + required，并禁用 inherited `wmux`；
- strict Agent result schema 改为封闭 nullable union；
- camelCase token/credential 输出补充脱敏；
- Case 任意 command 字段递归拒绝；
- per-attempt MCP backup root 映射到 `Backups` 安全子目录；
- proxy 在 production server 早退时立即传播退出，避免把真实配置错误伪装成 handshake timeout；
- tool-call budget failure 正确归类为 Agent tool selection。

保留的 A1 v1-v4 记录了 schema calibration 和“Agent 无 UE tools”的失败。A1 v5 修复后：

- Full 23 个真实 UE calls，含 1 次 `ue_get_task_context`；
- Legacy 18 个真实 UE calls，R0-R3 高层调用为 0；
- exact token usage 可用，prompt/fixture fairness matched；
- Reforge package/database/revision/policy cleanup 全部 unchanged；
- 两边均因额外核心 target 与超过 16-call budget 形成保留的 false success；不覆盖、不删 attempt、不调高阈值追求校准成功。

这些数字只证明 harness ready，不进入正式 aggregate。

## 11. 正式执行门禁

正式跑分前：

```text
working tree clean
34 focused tests pass
Ruff pass
dry validation = 15 Full / 9 Legacy / 24 attempts
all fixed fixture preflights exact-recovery pass
Codex runtime/model/profile metadata captured
fresh output root
```

正式配置固定：

```text
Agent/Harness: Codex CLI
model: gpt-5.6-sol
reasoning: low
service tier: priority
Full: 15
Legacy: 9
schedule: matched profiles interleaved
```

跑分后必须确认 retained attempts=24、summary 从 raw attempts 重算一致、所有 write fixtures recovered、无 UE orphan、无 dirty descriptor、Output/Backups 未提交。

## 12. 已知限制

- v1 主要是 single-attempt measurement，不能把小 delta 表述为统计显著。
- static index/reference 不证明 runtime gameplay、视觉、性能、网络复制或外部系统行为。
- Codex cumulative input token usage 会随多轮 Tool call 重复计入上下文成本；报告使用 harness 精确 usage，不把它误称为 unique prompt token。
- Profile registry visibility 是 93/88，但具体 MCP server 只广告当前配置可用子集；结果必须同时报告实际 trace。
- Reforge 不做写入；DirectHost fixture 不代表全部真实项目 writer/domain。
- cleanup success 只证明 fixture 精确恢复，不证明 Agent 任务成功。
- R4 结束后只输出 R5/Writer/Index/Agent UX 建议，不进入 R5。

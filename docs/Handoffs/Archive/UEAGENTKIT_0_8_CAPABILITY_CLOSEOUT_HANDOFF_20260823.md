# UEAgentKit 0.8 Capability Closeout Handoff

> 日期：2026-08-23
>
> 分支：`feature/agent-reliability`
>
> Reliability checkpoint：`79d4d87`
>
> 最新正式发布：`0.7.0`（未修改）
>
> 范围：0.8.x Context / Analysis / Agent Reliability capability closeout；不含版本升级、Tag、Release artifact、Push 或 R5

## 1. 最终结论

```text
0.8 capability scope              ACCEPT / COMPLETE
C0-C6                             COMPLETE
R4.1 formal repeat                COMPLETE, failures retained
Must-fix new Read Tools           0
Must-fix new Write Tools          0
R5                                DEFERRED BY BENCHMARK EVIDENCE
Formal 0.8 package release        NOT PERFORMED
Published version                 0.7.0
```

本轮已经把 0.8 capability scope 收口到可回归、可审计、可继续接手的状态，但没有宣称 Agent 已整体可靠，也没有把本地 capability closeout 伪装成正式 0.8 package release。

## 2. 交付物

- [`../../Plans/Archive/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](../../Plans/Archive/AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md)：R4.1 正式 repeat 的完整分布、成本、失败与恢复证据；
- [`../../Plans/Archive/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](../../Plans/Archive/UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md)：105 个公共 Tool 与 18 个 Patch Operation 的逐项分类、Must-fix 决策和 Scope Freeze；
- [`../../Plans/Archive/UEAGENTKIT_0_8_RELEASE_REVIEW_20260823.md`](../../Plans/Archive/UEAGENTKIT_0_8_RELEASE_REVIEW_20260823.md)：Capability acceptance、最终工程门禁、known limitations 与正式发布边界；
- [`AGENT_RELIABILITY_0_8_CLOSEOUT_FULL_HANDOFF_20260822.md`](AGENT_RELIABILITY_0_8_CLOSEOUT_FULL_HANDOFF_20260822.md)：C0-C6 的原始执行定义与验收来源。

公共状态已同步到 `README.md`、`README_EN.md`、`CHANGELOG.md`、Roadmap、Project Status、总计划、文档索引和 `spec/MCP_SERVER.md`。

## 3. R4.1 正式证据

```text
measurementVersion                r4.1
graderVersion                     r4.1.0
promptVersion                     r4.1-result-contract-1.5
fingerprint                       a3959b8ce98b06b2b7dd0da9386d03c60168ef39429e265c48d609abf36a29ee
scheduled / retained              24 / 24
paired fairness                   12 / 12
measurement drift                 false
mutation fail-closed triggered    false
infrastructure failure            0
cleanup / exact recovery          24 / 24
```

| Profile | Completed | Trusted | False Success | Wrong Asset |
|---|---:|---:|---:|---:|
| Full | 7/12 | 7/12 | 5/12 | 0/12 |
| Legacy | 3/12 | 3/12 | 2/12 | 6/12 |

R4.1 诚实保留了两类 Full limitation：high-fanout 3/3 越过 direct-only depth，scalar 2/3 把 numeric `-17` stringify。它们属于 Agent bound / final result typing，不是缺少新的 UE Read/Write Tool。

运行后 18/18 DirectHost attempts 的 canonical DB、revision、dirty、policy 与 descriptor 均精确恢复；6/6 Reforge attempts 保持只读不变。正式 raw attempts 位于 ignored `Output/AgentReliabilityBenchmark/r4-1-formal-20260823`，不得加入 Git。

## 4. Capability Freeze

Registry 最终机械复算：

| Mode | Count |
|---|---:|
| Offline | 10 |
| Offline + Memory | 22 |
| Live | 43 |
| Live + Memory | 55 |
| Workflow-only | 60 |
| Workflow + Memory | 72 |
| Live + Workflow | 93 |
| Combined + Memory | 105 |

105/105 Tool 与 18/18 Patch Operation 均有文档分类，无遗漏、无重复。Generic Blueprint Graph CRUD、Level Actor CRUD、Material Graph、Niagara、Sequencer、Control Rig、任意脚本/Console/Shell、通用资产生命周期与协作自动化继续明确延期。

R5 只有在多个后续真实 Case 反复出现 `value-provenance-gap` 或 `execution-trace-gap` primary blocker，并证明收益高于 guidance、Writer 或 Index 修复时才允许解冻。当前 R4.1 两项 taxonomy 均为 0。

## 5. 最终工程门禁

```text
Portable release validation     pass; 0.7.0
Ruff                            pass
Portable unittest              696 passed in 42.619 s
Python full suite               739 passed in 45.08 s
Schema / examples               3 / 16
compileall                      pass
PowerShell parser               61 / 61 tracked scripts
R4.1 raw summary --check        pass
Registry mode counts            10/22/43/55/60/72/93/105
Documentation classification    105/105 Tools; 18/18 Operations
git diff --check                pass
UTF-8 no BOM + CRLF             pass
C++ changed                     0
Direct Build                    not triggered
UE / benchmark / MCP processes  0 / 0 / 0
DirectHost descriptors          0
tracked Output / Backups        0
```

Portable validation 命令为：

```powershell
.venv\Scripts\python.exe scripts\ValidateRelease.py --expected-version 0.7.0 --require-release-docs
```

无 C++ 修改，因此没有重复运行 Direct Build。既有 UE5.6 affected-domain smoke 与本轮 24 个 R4.1 real UE attempts 是本次真实引擎证据；任何后续 C++ 修改都会重新触发 Direct Build。

## 6. Trust / Recovery 固定边界

Trust Evidence Ladder 保持：write → authorized save → independent verify → verified semantic diff → verification plan → exact missing action evidence → trust verdict → final claim。Trust Tool 不自动执行 Save、Compile、Validate、Automation、Verify 或 Rollback，也不接收 arbitrary Evidence JSON。

Blueprint saved-revision rollback 只有在 Bridge 精确证明以下四项时才允许执行：

```text
loaded=false
packageDirty=false
openInAssetEditor=false
state=not-loaded
```

Restart 后只允许复用独立 Canonical/Revision；session-local action evidence 不重建为 PASS，安全可重复的 exact action 必须重跑。

## 7. Git 与产物边界

- 不 Push；
- 不创建 Tag、GitHub Release 或 0.8 artifact；
- 不修改 `pyproject.toml`、`UEAgentKit.uplugin` 或其他 published-version source；
- 不提交 `Output`、`Backups`、`Build`、`Intermediate`、`Saved`、日志或 raw benchmark；
- 不进入 R5；
- 不修改或提交任务开始前已经存在的未跟踪 `CONOUT$`。

最终提交后 tracked working tree 应为 clean；允许唯一已知的未跟踪项仍为 `CONOUT$`。

## 8. 后续接手入口

如果下一轮只是继续产品开发，应从 Scope Freeze 的 demand-driven backlog 选择有真实 Case 证据的任务，不需要重跑 R4.1，也不要按 Tool 数量扩面。

如果下一轮明确授权正式 0.8 package release，应作为独立流程执行 published version 更新、Direct Build、portable artifact validation、hash、Tag、Release 与 remote 授权；本 Handoff 不包含这些权限。

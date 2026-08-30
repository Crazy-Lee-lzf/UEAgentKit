# UEAgentKit 0.8 Capability Release Review / Scope Freeze

> 日期：2026-08-23
>
> 分支：`feature/agent-reliability`
>
> Reliability checkpoint：`79d4d87`
>
> 最新正式发布：`0.7.0`（未改变）
>
> Review 范围：0.8.x Context / Analysis / Agent Reliability capability scope，不包含正式版本号、Tag、Release artifact 或 Push

## 1. Review Decision

```text
0.8 capability scope              ACCEPT
C0-C6 closeout                    COMPLETE
Must-fix new tools                0
R4.1 measurement                 COMPLETE, failures retained
R5                               DEFERRED BY BENCHMARK EVIDENCE
Formal 0.8 package release        NOT PERFORMED
Published version                 0.7.0
```

0.8 capability scope 达到本地可冻结、可回归、可交接状态。这个结论不等于 Agent 已整体可靠，也不等于 0.8 二进制/包已正式发布。R4.1 的 high-fanout 与 scalar False Success 仍是明确限制；它们没有暴露新的 UE Read/Write capability gap，因此不以增加 Tool 作为 Release 前置条件。

## 2. C0 — Agent UX / Result Contract

完成：

- benchmark result schema 升为 `agent-result-1.2`；
- `status`、`trustVerdict`、`conflict`、`operation` 使用封闭枚举；
- explanatory text 只进入 notes/reason/summary，不污染机器枚举；
- `targetAssets` 必须精确等于用户任务目标，不混入 candidate/consumer/validation target；
- stale/dirty/policy/required-evidence block 不得输出 success；
- successful exact rollback 使用 `success / not-evaluated`，不把已消失 transient revision 伪装成 verified Trust；
- claim parser/grader 对 exact enum、target 与 value type fail closed。

Review：contract 已能机械拒绝不精确 claim。R4.1 scalar 前两次 numeric/string mismatch 被保留为 False Success，证明 grader 没有为了漂亮分数放宽。

## 3. C1 — Trust Evidence Closed Loop

完成固定 next-action ladder：

```text
write
-> authorized save
-> independent verify
-> verified semantic diff
-> verification plan
-> exact missing action evidence
-> trust verdict
-> final claim
```

保持边界：

- Trust Tool 不自动 Save/Compile/Validate/Automation/Verify/Rollback；
- 不允许 arbitrary Evidence JSON ingest；
- receipt 不自动关闭 Required Assertion；
- restart 后 session-local action evidence 不重建 PASS；只允许复用 independent Canonical/Revision，并重跑安全可重复的 exact action。

Review：R4.1 Blueprint Full 3/3 完成 compile/persistence/semantic/plan/trust，scalar 三次也都完成 evidence ladder。剩余 scalar 失败发生在 final result typing，而非 evidence 闭环。

## 4. C2 — Narrow Reliability / Recovery

完成：

- Blueprint saved-revision rollback 只在 Bridge 精确证明 `loaded=false`、`packageDirty=false`、`openInAssetEditor=false`、`state=not-loaded` 后执行；
- stale revision、wrong manifest、unknown live state 继续 fail closed；
- Blueprint compile pin-default normalization 只接受按 pin 类型机械允许的 empty/zero/false；字符串 pin 的真实 `0` 仍 unexpected；
- Reference grader 只归一化由 Case 明确 mutation 派生的单条 hard-package edge；unrelated edge 仍 unexpected；
- write/save/verify/R2/R3 统一绑定 Change Set identity。

Review：不新增 arbitrary rollback API 或 generic Writer。R4.1 18/18 DirectHost attempts exact canonical/revision recovery。

## 5. C3 — R4.1 Repeat

正式配置：

```text
measurementVersion      r4.1
graderVersion           r4.1.0
promptVersion           r4.1-result-contract-1.5
fingerprint             a3959b8ce98b06b2b7dd0da9386d03c60168ef39429e265c48d609abf36a29ee
model                   gpt-5.6-sol
reasoning               low
service tier            priority
anchors                 4
profiles                2
repeats/profile         3
scheduled/retained      24/24
```

完整结果：[`AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md`](AGENT_RELIABILITY_R4_1_REPEAT_RESULT_20260823.md)。

核心数据：

| Profile | Completed | Trusted | False Success | Wrong Asset | Timeout |
|---|---:|---:|---:|---:|---:|
| Full | 7/12 | 7/12 | 5/12 | 0/12 | 0/12 |
| Legacy | 3/12 | 3/12 | 2/12 | 6/12 | 1/12 |

Paired `Full - Legacy`：Completion +33.33 pp、Semantic +50.00 pp、Trusted +33.33 pp、False Success **+25.00 pp worse**、Wrong Asset -50.00 pp、Tool Calls +10.917、Elapsed +110.102 s、Total Tokens +608,119（11 available pairs）。

稳定性：

- stale：Full/Legacy 均 3/3 Trusted、3/3 detected、0 mutation；
- Blueprint：Full 3/3 Trusted，Legacy 3/3 safe non-success；
- high-fanout：Full 0/3 Trusted、3/3 False Success；低调用优势保留，但越过 direct-only bound；
- scalar：Full world/evidence 3/3 正确，exact claim 1/3 Trusted；两次 stringify numeric beforeValue。

Review：结果诚实性通过；不能宣称 False Success 已整体下降。

## 6. C4/C5 — Capability Gap / Must-fix

完整审计：[`UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md`](UEAGENTKIT_0_8_CAPABILITY_GAP_AUDIT_20260823.md)。

```text
Public Tools                    105
Tools without Memory            93
Patch Operations                18
Unclassified Tools               0
Unclassified Operations          0
missing-high-value Reads          0
missing-narrow-high-value Writes  0
Must-fix new tools                0
```

R4.1 剩余问题对应 Agent task-bound / final typing，不满足新 Tool 进入条件。Generic Graph/Actor/Material Graph/Niagara/Sequencer/Control Rig、arbitrary scripting、generic asset lifecycle 与 collaboration automation 保持 deferred。

## 7. C6 — Scope Freeze

Shipped in 0.8 capability scope：

- R0 Task Context；
- R1 Impact Analysis；
- R2 Semantic Diff；
- R3 Verification Plan / Trust Verdict；
- R4/R4.1 deterministic real-agent benchmark；
- C0–C2 reliability/recovery fixes；
- Capability Gap Audit 与 0 Must-fix decision。

Known limitations：

- Agent 仍可能违反 direct-only/depth bound；
- 通用 result schema 的 operation value 类型仍可能被模型 stringify；
- Full verified write chain 的 Tool、时延与 Token 成本高；
- static Index/Reference 不证明 runtime execution；
- action evidence 默认 session-local。

R5 trigger：

只有多个后续真实 Case 反复出现 `value-provenance-gap` / `execution-trace-gap` primary blocker，并证明收益高于 guidance/Writer/Index 修复时解冻。R4.1 两项为 0。

## 8. Fixture / Measurement Integrity

```text
run.status                         completed
attemptsRetained                   24/24
paired fairness                    12/12
measurement drift                 false
mutation fail-closed triggered     false
infrastructure failure             0
cleanup/exact recovery             24/24
DirectHost descriptor absent       18/18
DirectHost DB/revision/policy same 18/18
Reforge readonly unchanged          6/6
post-run UE/benchmark/MCP orphan     0
post-run descriptor                 0
raw summary --check                pass
```

## 9. Final Engineering Gates

最终门禁在同一工作树完成，结果如下：

```text
Portable release validation     pass; published version 0.7.0
Ruff                            pass; src + tests/python
Portable unittest              696 passed in 42.619 s
Python full suite               739 passed in 45.08 s
Schema / examples               3 schemas; 16 patch examples
compileall                      pass; src/scripts/benchmarks/tests
PowerShell parser               pass; 61 tracked scripts
R4.1 raw summary --check        pass; 24 retained attempts
Registry mode counts            10/22/43/55/60/72/93/105
Registry classification         105/105 Tools; 18/18 Operations
Registry group counts           10/12/17/8/8/50
Registry annotations            43 read; 47 planning; 15 destructive
git diff --check                pass
UTF-8 no BOM + CRLF             pass; all closeout text files
C++ changed                     0
Direct Build                    not rerun; not triggered
post-run UE processes           0
post-run benchmark processes    0
post-run MCP processes          0
DirectHost descriptors          0
tracked Output/Backups paths    0
```

`scripts/ValidateRelease.py --expected-version 0.7.0 --require-release-docs` 完整通过。Portable unittest 的 696 项是该脚本的 `tests/python` 口径；739 项是本轮同一工作树的完整 Python suite 口径，两者不是重复计数错误。

C++ 未修改，因此不重复执行 Direct Build；既有 UE5.6 affected-domain smoke 与本次 R4.1 24 个 real UE attempts 构成真实引擎证据。任何后续 C++ 修改会重新触发 Direct Build 硬门禁。

## 10. Release Boundary

本 Review 不授权：

- 修改 `pyproject.toml` / `UEAgentKit.uplugin` published version；
- 创建 Tag、GitHub Release 或二进制发布包；
- Push；
- 进入 R5；
- 把 Output/Backups/Build/Saved/log/raw benchmark data 加入 Git。

正式 0.8 package release 如后续启动，应从本 Scope Freeze 单独执行版本、构建、portable validation、artifact hash、Tag/remote 授权流程。

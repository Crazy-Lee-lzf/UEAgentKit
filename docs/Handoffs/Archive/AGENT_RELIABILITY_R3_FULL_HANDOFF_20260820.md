# UEAgentKit Agent Reliability R3 Full Handoff — Verification Plan + Trust Verdict

> 日期：2026-08-20
> 开发分支：`feature/agent-reliability`
> 最低代码基线：`b5071aff5b116336d7baa16c84baaf64016c828a`（R2 Semantic Diff 已完成）；实际执行必须从当前 `feature/agent-reliability` HEAD 开始，并确认包含该 R2 Commit 与本 Handoff。
> 执行模型：模型 / Harness 无关。Primary Agent 负责架构、公共协议、核心实现、最终 Review、门禁和提交；允许使用 Subagent 并行执行只读审计、测试补齐、真实 Smoke 和文档一致性检查。
> 任务模式：**一次性完成整个 R3；允许内部拆分、并行和 checkpoint commit，但不要在中途等待用户逐片确认。整个 R3 完成后统一汇报并停止，不进入 R4。**
> Git 纪律：不 Push；不 Reset / Stash / Rebase / Revert 用户工作；不提交 Output / Backups / Build / Intermediate / Saved / 日志 / 临时 UE 资产。
> 事实基线：当前 Git、Handoff、代码、测试和真实 Tool 输出是唯一事实基线；不得用 Agent 自身旧对话、缓存记忆或历史计划替代当前仓库事实。
> 实现自由度：若当前仓库已有比本文建议更合适的复用抽象，可以调整实现方式，但不得扩大 Scope、改变停止点、降低证据要求或验收标准；最终汇报必须说明实质偏离与依据。

---

## 1. R3 的产品目标

R0 已回答：

```text
我现在应该看什么？
```

R1 已回答：

```text
如果修改目标，静态引用图中哪些对象可能受影响？
```

R2 已回答：

```text
Plan 要求改什么？
实际发生了什么？
哪些符合预期、哪些没有发生、哪些额外发生？
```

R3 要回答：

```text
对于这个明确 Change Set，哪些验证是必须的？
哪些验证已经有可适用的 Evidence？
哪些 PASS / FAIL / UNKNOWN / NOT-APPLICABLE？
现有证据是否足以支持“该 Change Set 在明确验证范围内可信”？
如果不够，下一步应该执行哪个明确 Tool？
```

R3 是 UEAgentKit 的 **Trust Layer（信任层）**。它不是“再做一遍 Verify”，也不是把若干 bool 拼成一个 `success=true`。

核心原则：

> **Trust Verdict 必须 Evidence-gated。没有证据就是 UNKNOWN，不得从成功的 Save、Independent Reload、Semantic Diff、无 Compile Error、Agent 自述或模型判断中补齐不存在的证据。**

同时必须明确：

> `verified` 只表示“当前 Verification Plan 中所有 Required Assertion 都有适用 Evidence 并通过，且没有阻断风险”；它不等价于“玩法、视觉、性能、设计意图在所有维度都绝对正确”。响应必须显式报告 verification scope 与未覆盖维度。

---

## 2. R3 完成定义

R3 只有同时满足以下条件才算完成：

1. 完成 R3.0 现有 Workflow / Semantic Diff / Impact / Compile / Data Validation / Automation / Revision / Memory Evidence 的复用审计；
2. 建立统一、确定性的 `VerificationPlan` / `Assertion` / `EvidenceReference` / `TrustVerdict` 数据模型；
3. 提供稳定的只读 Public Tool，建议至少两个入口：

```text
ue_build_verification_plan
ue_evaluate_trust_verdict
```

4. 两个 Tool 都只接受**显式 `change_set_id`**；不得自动发现 Change Set；
5. Verification Plan 能根据 Change Set Operation / Domain 机械生成 Required / Recommended / Informational Assertions；
6. Trust Verdict 只消费 UEAgentKit 已有或受控采集的 Evidence，不自动执行 Compile / Validate / Automation / Save / Verify / Writer；
7. 至少覆盖以下 Assertion Family：

```text
persistence
semantic
freshness
compile
data-validation
reference-impact
automation
recovery
```

8. 每个 Assertion 必须有确定性 `requirement`、`status`、`applicability`、Evidence 和 reason；
9. 最终 Verdict 固定为：

```text
verified
suspicious
failed
insufficient-evidence
```

10. Verdict 算法必须固定、可测试、零模型推断、无 confidence / score；
11. R3 必须复用 R2 Semantic Diff，不能自己复制第二套 expected/actual 比较；
12. R3 必须复用 R1 Impact Analysis，不能自己复制第二套 Reference Graph；
13. 如果 Compile / Validation / Automation Evidence 当前无法在后续 Trust 调用中可靠取回，可实现最小有界 Evidence Capture，但必须遵守本文 §8；
14. R0 / R1 / R2 增加渐进式 R3 nextAction / nextExpansion，但不得默认自动执行 R3；
15. 有界输出、固定排序、稳定 ID、Token Budget、Truncation 均纳入契约；
16. 至少完成真实 UE5.6 Success / Insufficient Evidence / Failure / Suspicious 四类 Smoke；
17. Ruff、Python 全量、Tool Registry / MCP Capability / strict args、`git diff --check`、UTF-8 无 BOM + CRLF 全部通过；有 C++ 变更时必须 UE5.6 Direct Build + 对应真实 Smoke；
18. 更新 `docs/ROADMAP.md`、`docs/PROJECT_STATUS.md`、总计划、`spec/MCP_SERVER.md` 与 R3 设计/审计文档；
19. 本地 Commit，工作树干净；
20. **停止，不进入 R4 Benchmark。**

---

## 3. R3.0：先做 Evidence Audit，但不中途停

Primary Agent 开始公共协议实现前，至少并行安排 3 个 Subagent 做只读审计；审计完成后继续实施整个 R3，不等待用户确认。

### Subagent A：Workflow / Persistence / Semantic Evidence

重点审计：

```text
PatchWorkflowService / Change Set journal
Plan / Operation intent
Live Apply / Authorized Save
ue_verify_asset / ue_verify_live_write
Backup Manifest / Rollback readiness
ue_analyze_semantic_diff
Revision / Freshness / Dirty state
```

回答：

- 哪些状态可机械证明 Persistence；
- R2 `verified` / expected no-op 如何作为 Semantic Assertion Evidence；
- 哪些 Revision mismatch / stale / dirty 必须阻止 Evidence 适用；
- no-op Change Set 应如何避免被强制要求不存在的 Save/Verify；
- Recovery Evidence 当前能证明到什么程度。

### Subagent B：Compile / Data Validation / Automation Evidence

重点审计：

```text
ue_compile_blueprint
ue_get_compile_errors
ue_validate_asset
ue_validate_folder
ue_run_automation_test
Editor Bridge 返回 Schema
project / editorSession / observedAtUtc / revisionSet 绑定
现有 Validation Evidence Schema 1.0
automationRevisionCoverage = not-applicable 的真实语义
```

回答：

- Compile 结果是否有稳定、可后续检索的 Evidence ID；
- Compile PASS 是否能绑定到 exact Blueprint + Editor Session + final asset revision；
- Data Validation 结果的 PASS / FAIL / NOT-APPLICABLE 如何表示；
- Automation Test 是否只证明 exact test 在该 project/session 执行通过，而不是资产 Revision 覆盖；
- 当前 Tool 返回后是否存在可被 Trust Evaluator 安全读取的结构化 Evidence；
- 若不存在，最小 Evidence Capture 应放在哪里。

### Subagent C：R1 Impact / Validation Scope / Test & Registry Audit

重点审计：

```text
ue_analyze_change_impact.validationTargets
R1 risks / analysisGaps
reference-sensitive Operation
Tool Registry / MCP registration order
strict args
capabilities / project status
硬编码 Tool count
现有 Fixture / DirectHost regression harness
```

回答：

- 哪些 R1 Evidence 只能作为 scope information，不能直接算 PASS；
- 哪些 Operation 可以机械分类为 reference-sensitive；
- 是否能把部分 direct Blueprint consumer 升级为 Required Compile target；
- 哪些情况只能保留 Recommended / Suspicious，而不能阻止 verified；
- 完整测试/契约同步清单。

### R3.0 必须落盘

新增 R3 设计/审计文档，至少包含一张 Evidence Matrix：

```text
Assertion Kind
Requirement Rule
Evidence Source
Applicability Binding
PASS Rule
FAIL Rule
UNKNOWN Rule
NOT-APPLICABLE Rule
Stale Rule
Suggested Next Tool
```

---

## 4. Public Tool 设计基线

### 4.1 `ue_build_verification_plan`

建议 Request：

```text
change_set_id                required, explicit only
impact_depth                 0..2, default 1
required_automation_tests    optional exact names, bounded (e.g. <= 8)
extra_validation_assets      optional exact /Game Object Paths, bounded (e.g. <= 8)
max_output_tokens            256..32768
```

约束：

- `required_automation_tests` 只能**增加** Required Assertions，不能降低自动生成的 Required 规则；
- 调用方不能用参数跳过 Blueprint Compile、Persistence、Semantic、Freshness 等 Domain-required Assertion；
- 不接受任意 Assertion JSON / DSL；
- 不接受项目路径、数据库路径、Evidence 文件路径或任意 before/after JSON；
- 不自动执行任何 Live Action。

建议 Response：

```text
VerificationPlan
├─ schemaVersion
├─ tool
├─ ok
├─ readOnly
├─ request
├─ changeSet
├─ planId / planFingerprint
├─ scope
│  ├─ affectedAssets[]
│  ├─ impactDepth
│  ├─ validationTargets[]
│  └─ unverifiedDimensions[]
├─ assertions[]
├─ summary
│  ├─ required
│  ├─ recommended
│  ├─ informational
│  └─ notApplicable
├─ risks[]
├─ nextActions[]
└─ outputBudget
```

`planId` / `planFingerprint` 默认应是**确定性派生 ID**，不是新建一个持久化 Plan 数据库对象。建议绑定：

```text
changeSet identity
fixed operation intent
assertion rule version
user-supplied exact automation tests
impact depth
```

若实现无需独立 `planId`，可只保留 `planFingerprint`，但必须能检测“评估时规则/输入与之前计划不同”。

### 4.2 `ue_evaluate_trust_verdict`

建议 Request 与 Plan 输入保持一致：

```text
change_set_id                required
impact_depth                 same deterministic default
required_automation_tests    same exact list
extra_validation_assets      same exact list
max_output_tokens
```

Evaluator 应重新生成/复用同一确定性 Verification Plan，并对每条 Assertion 查找**当前适用 Evidence**。

禁止：

- 在 Evaluator 内自动 `ue_compile_blueprint`；
- 自动 `ue_validate_asset`；
- 自动 `ue_run_automation_test`；
- 自动 Save / Verify / Rollback；
- 自动修改资产；
- 自动发现 Change Set；
- 接收任意用户构造的 Evidence JSON；
- 用模型生成 PASS / FAIL / confidence。

建议 Response：

```text
TrustAssessment
├─ schemaVersion
├─ tool
├─ ok
├─ readOnly
├─ request
├─ changeSet
├─ planFingerprint
├─ verificationScope
├─ verdict
│  ├─ state: verified | suspicious | failed | insufficient-evidence
│  ├─ reasonCodes[]
│  └─ statement
├─ assertions[]
├─ evidence[]                 // bounded normalized references, not giant raw reports
├─ unresolvedRisks[]
├─ analysisGaps[]
├─ unexpectedChanges[]        // from R2, bounded summary/reference
├─ summary
├─ recommendedNextActions[]
└─ outputBudget
```

---

## 5. Assertion 统一数据模型

每个 Assertion 必须至少包含：

```text
assertionId
kind
subject
requirement
status
applicability
sourceRule
requiredEvidenceKinds[]
evidenceRefs[]
reasonCode
message
nextAction
```

### 5.1 `requirement`

固定枚举建议：

```text
required
recommended
informational
```

`required` 才能直接阻止 `verified`。

### 5.2 `status`

固定枚举建议：

```text
pass
fail
unknown
not-applicable
```

不要把 `not-run`、`missing`、`unavailable` 做成无法统一判断的自由状态；这些原因可放 `reasonCode`，统一映射到 `unknown`。

### 5.3 `applicability`

建议显式描述 Evidence 覆盖层级：

```text
exact-asset-revision
exact-change-set
editor-session
project-session
project
not-applicable
insufficient-binding
```

Evidence 内容本身成功但绑定不到当前 Change Set / final Revision 时，Assertion 必须 `unknown`，不能 PASS。

### 5.4 Stable Identity

`assertionId` 必须从稳定输入派生，例如：

```text
assertion kind
subject asset / test name
changeSetId
rule version
```

相同输入、相同仓库 Evidence 必须输出相同 Assertion 顺序与 ID。

---

## 6. Verification Plan 的自动规则

R3 Server 仍保持零模型推断。Required / Recommended 必须由固定规则产生。

### 6.1 所有实际修改 Operation

至少 Required：

```text
freshness assertion
persistence assertion
semantic assertion
```

其中：

- Freshness：Plan baseline / final Evidence Revision 必须一致且不 stale；
- Persistence：必须有符合该 Operation 最终状态的持久化 / Independent Evidence；
- Semantic：优先复用 R2 `stage=verified`，要求 expected 与 actual 对齐且没有阻断型 missing/unexpected/gap。

### 6.2 Expected no-op

R2 已定义 no-op 的特殊证据边界。R3 不应因为没有 Save/Verify 就把合法 no-op 判失败。

no-op 可采用：

```text
Persistence = not-applicable 或 pass-with-baseline-no-op-evidence
Semantic = pass（仅 baseline Canonical Revision 精确匹配 Plan，R2 matched-expected-no-op）
Compile / Validation = 仅当实际没有修改且规则不要求重新验证时可 not-applicable
```

最终规则由 R3.0 审计后定稿，但不得制造不存在的 Save / Verify Evidence。

### 6.3 Blueprint Narrow Write

对于实际修改的 Blueprint：

```text
compile = required
data-validation = required 或明确 not-applicable（以真实 UE Data Validation 语义为准）
semantic = required
persistence = required
freshness = required
```

Compile PASS 必须来自**显式 compile 后的适用 Evidence**；“当前 compile error 列表为空”不能自动证明已经对 final Revision 做过 Compile。

如果现有 Compile Evidence 无法绑定到 final Revision，必须 UNKNOWN，除非 R3.0 能通过 exact Editor Session + current asset state + final Revision 建立机械关联。

### 6.4 Data Asset / DataTable / Material Instance

至少：

```text
persistence = required
semantic = required
freshness = required
data-validation = required 或 not-applicable（由真实 ValidateAsset 结果判定）
compile = not-applicable
```

不得因为“这类资产通常没编译”创建假 Compile PASS。

### 6.5 Reference-sensitive Operation

R3.0 必须审计并固定哪些既有 Operation 可机械标记 `reference-sensitive`，例如候选：

```text
setAssetReferenceProperty
DataTable rename/remove（已有 Searchable Name safety gate 时）
其它明确改变 stable reference identity 的 Operation
```

对于 reference-sensitive Operation：

- `reference-impact` 至少 Recommended；
- 如果 R1 发现 direct consumer 且能机械识别为 Blueprint，可生成有界 consumer Compile Assertion；
- 是否升级为 Required 必须由固定规则和现有证据支持，不凭 Agent 判断；
- R1 的“存在静态引用”本身不是 PASS/FAIL，只是验证 scope Evidence。

### 6.6 Automation

Server **不自动选择 Automation Test 名称**。

Automation Assertion 只来自：

1. 调用方显式 `required_automation_tests`；或
2. 仓库已存在明确、确定性、项目级规则（若 R3.0 审计发现）。

若没有来源：

```text
automation = not-applicable / no assertion
```

不能因为没有 Automation Test 就阻止普通 Change Set Verified。

对于显式 exact test：

- PASS 只证明 exact test 在固定 project/session 中执行通过；
- 当前 capability 已声明 `automationRevisionCoverage=not-applicable`，不得谎称该 Automation Evidence 覆盖每个资产 Revision；
- 若用户要求 Automation，它是 Required；没跑 → UNKNOWN；失败 → FAIL。

### 6.7 Recovery

Backup / rollback readiness 可作为 Recommended 或 Informational Assertion。

不要要求“为了证明成功必须先 Rollback 一次”。真实回归 Smoke 可以做 rollback 验证，但普通 Trust Verdict 不应破坏刚完成的修改。

---

## 7. Verdict 确定性规则

最终规则必须写成代码 + 测试，而不是自然语言启发式。

建议优先级：

```text
1. failed
2. insufficient-evidence
3. suspicious
4. verified
```

### 7.1 `failed`

至少满足任一：

```text
Required Assertion = fail
R2 required semantic evidence 存在 missing expected change
R2 required semantic evidence 存在 unresolved unexpected change
Compile required 且 compile failed/errors
Data Validation required 且 validation failed/invalid
Required Automation Test failed
明确 Revision contradiction / wrong asset evidence
```

不要把一般 warning / high fanout 自动算 failed。

### 7.2 `insufficient-evidence`

满足：

```text
无 Required FAIL
但至少一个 Required Assertion = unknown
```

典型：

```text
还没 Compile
还没 Validate
verified Semantic Diff 不可用
Evidence Revision 不匹配导致 Evidence 失效
Evidence 只属于旧 Editor Session
关键 snapshot coverage 不足
Required Automation 未执行
```

### 7.3 `suspicious`

满足：

```text
所有 Required Assertion 均 pass / not-applicable
但存在确定性的 non-blocking unresolved risk 或 Recommended Assertion fail/unknown
```

例如候选：

```text
R1 high-fanout-target
unknown reference kind（不阻断核心 Change Set 证据时）
recommended consumer validation 未执行
validation warning（非 error）
known analysis gap 不影响 Required semantic coverage，但降低整体信任
```

具体 risk 是否 blocking 必须由固定表定义。

### 7.4 `verified`

只有：

```text
所有 Required Assertion = pass 或 not-applicable
Required Evidence 均适用于当前 Change Set / final Revision
无 blocking risk
无 Required semantic missing/unexpected
无会使 Required Evidence 失效的 analysis gap
```

才能返回 `verified`。

响应 statement 必须类似：

```text
Verified against the generated Verification Plan and currently available deterministic evidence.
```

不得输出：

```text
The gameplay is definitely correct.
The change is bug-free.
Confidence 99%.
```

---

## 8. Validation / Compile / Automation Evidence Capture

这是 R3.0 必须先审计的关键工程问题。

### 8.1 优先复用现有 Evidence

如果现有 Editor Bridge / Workflow 已经提供：

```text
stable evidence/report id
project identity
editorSessionId
observedAtUtc
asset path
asset revision / revision set（适用时）
result status
bounded diagnostics
```

则直接复用，不新增 Store。

### 8.2 如果现有结果调用结束后不可检索

允许实现一个最小：

```text
VerificationEvidenceStore
```

但必须：

- session-local 或沿用现有受控 WorkRoot artifact；
- 有硬上限（例如 evidence count / diagnostics count）；
- 只接受 UEAgentKit 注册 Tool 自己产出的结构化结果；
- 固定 project；
- 明确 editorSessionId / asset revision / revisionSet / applicability；
- 不开放任意 `ue_record_evidence(json=...)`；
- 不接受本地文件路径；
- 不新增 Memory Schema；
- 不让模型写 PASS/FAIL；
- Tool 调用返回 Evidence ID 时必须同步契约和测试；
- Store 丢失时 Verdict 退化为 insufficient-evidence，而不是重建假 Evidence。

### 8.3 Compile Evidence 特别要求

Compile Assertion PASS 不得只依赖：

```text
ue_get_compile_errors returns []
```

它必须证明至少：

```text
explicit compile occurred
exact Blueprint identity
same fixed project
applicable editor session
compile result success
final revision applicability 可证明，或明确 insufficient-binding
```

### 8.4 Data Validation Evidence

必须保留真实 Unreal 语义：

```text
valid / passed
invalid / errors
not-applicable / no validator
warning
truncated diagnostics
```

`not-applicable` 不等于伪造 PASS，但可以使该 Assertion `not-applicable`。

### 8.5 Automation Evidence

Automation 只绑定 exact test name、fixed project、执行 session/time 与结果；如果 Revision coverage 本来就是 `not-applicable`，保持该语义。

---

## 9. R2 Semantic Diff 集成

R3 Semantic Assertion 必须直接复用：

```text
ue_analyze_semantic_diff
```

或者复用其内部 Service / Evidence adapter，不复制第二套 Diff。

建议最终 Trust Evaluation 对实际修改优先请求：

```text
stage=verified
```

如果 verified stage 不存在：

```text
semantic assertion = unknown
verdict <= insufficient-evidence
nextAction = 完成 Independent Verify 后重试
```

no-op 使用 R2 已定义的 baseline exact evidence 特例。

R2 的：

```text
unexpectedChanges
missingExpectedChanges
analysisGaps
riskSummary
```

必须映射到 R3 Assertion / unresolvedRisk，而不是被吞掉。

---

## 10. R1 Impact Analysis 集成

R3 不复制 Reference Graph。

Verification Plan 可以内部复用 R1 Service 做有界：

```text
depth = impact_depth（默认 1，最大 2）
```

用途只包括：

```text
生成 validation scope
发现 high fanout / truncation / unknown reference gap
对 reference-sensitive change 生成 bounded consumer assertions
```

禁止：

- 把所有 Direct/Indirect Consumer 默认设成 Required；
- 把“有引用”当成“consumer 验证通过”；
- 在 R3 做 runtime execution trace；
- 因为 Blueprint 类型就猜 runtime sensitivity。

如果 R1 truncated：

- 对 reference-sensitive Required scope，可能导致 Required reference assertion UNKNOWN → insufficient-evidence；
- 对普通非 reference-sensitive Change，可作为 non-blocking risk → suspicious；
- 最终映射规则必须固定测试。

---

## 11. R0 Task Context 集成

R0 默认 Context 不自动执行 Verification Plan 或 Verdict。

仅在显式 Change Set found 时增加渐进入口，例如：

```text
verification-plan-explicit-change-set
trust-verdict-explicit-change-set
```

推荐顺序：

```text
Task Context
→ Semantic Diff
→ Verification Plan
→ 执行明确 nextActions
→ Trust Verdict
```

不要把完整 assertions/evidence 默认塞进 4096 token Task Context。

---

## 12. `recommendedNextActions` 必须可执行但不自动执行

当 Assertion UNKNOWN / FAIL 时，应尽量返回已有 Tool 的 exact suggestion。

示例：

```text
compile missing
→ ue_compile_blueprint(asset_path=...)

data validation missing
→ ue_validate_asset(asset_path=...)

verified semantic evidence missing
→ 完成 ue_verify_live_write / ue_verify_asset 后重新 ue_evaluate_trust_verdict

required automation missing
→ ue_run_automation_test(test_name=<exact configured name>)

impact scope truncated
→ ue_analyze_change_impact(... bounded larger limits ...)
```

R3 Tool 不执行这些 Action。

对于可能修改 Editor Memory 状态的 `ue_compile_blueprint`，仍使用其现有 action 契约；R3 只建议，不绕过安全边界。

---

## 13. Scope / Coverage 必须显式输出

每个 Verdict 必须告诉 Agent：它验证了什么，没有验证什么。

建议：

```text
verificationScope
├─ changeSetId
├─ affectedAssets[]
├─ verifiedAssets[]
├─ referenceDepth
├─ requiredAutomationTests[]
├─ evidenceStages[]
└─ unverifiedDimensions[]
```

`unverifiedDimensions` 可使用固定枚举/消息，例如：

```text
runtime-gameplay-behavior
visual-correctness
performance-regression
network-replication-behavior
external-system-behavior
runtime-execution-trace
```

除非有对应 Evidence，不得因为 Verdict=verified 就隐藏这些边界。

---

## 14. Risks / Gaps / Evidence Applicability

建议至少支持确定性 Risk / Gap：

```text
trust-required-evidence-missing
trust-evidence-stale
trust-evidence-session-mismatch
trust-evidence-revision-mismatch
trust-semantic-unexpected-change
trust-semantic-missing-expected-change
trust-compile-failed
trust-validation-failed
trust-automation-failed
trust-impact-truncated
trust-reference-scope-unknown
trust-verdict-truncated
```

Severity / blocking 必须固定表驱动。

同一 Evidence 即使内容为 PASS，只要 applicability 失效：

```text
old editor session
wrong asset revision
wrong project
pre-change compile result
stale validation revision set
```

都不能用于 Required Assertion PASS。

---

## 15. Budget / Determinism / Bounds

R3 输出可能包含大量 assertion / evidence，因此必须从第一版就有界。

建议边界由 R3.0 后定稿，但至少：

```text
max affected assets       <= 8 或与 R2 一致
max assertions            bounded，例如 <= 128
max evidence refs         bounded，例如 <= 128
max automation tests      <= 8
impact depth              <= 2
max returned diagnostics  bounded
max_output_tokens         256..32768
```

固定排序建议：

```text
requirement priority
→ assertion kind
→ subject asset/test casefold
→ assertionId
```

Token 裁剪优先级：

```text
1. raw/verbose evidence details
2. informational assertion details
3. recommended assertion optional metadata
4. duplicate evidence summaries
5. low-severity risk details
```

永远优先保留：

```text
changeSet identity
planFingerprint
verdict state + reasonCodes
Required Assertion status summary
Required FAIL / UNKNOWN assertion identity
blocking risks
truncated state
recommendedNextActions
verificationScope
```

如果最低保障 envelope 超预算，沿用 R0/R1/R2 约定：允许实际 estimated token 超最小 budget，但必须 `truncated=true + minimum-envelope-exceeds-budget`。

---

## 16. 测试矩阵

建议新增：

```text
tests/python/test_verification_trust.py
```

测试数量不设形式目标，但至少覆盖：

### Plan Core

```text
T1  单 Data Asset Change Set 生成固定 Required Assertions
T2  Blueprint 自动要求 compile
T3  non-Blueprint compile = not-applicable / no assertion
T4  explicit required automation test 进入 Required
T5  调用方不能通过参数跳过自动 Required Assertion
T6  planFingerprint 相同输入完全确定
T7  多资产固定排序 / 去重
T8  no-op 规则不伪造 save/verify requirement
T9  explicit change_set_id only / no private discovery
T10 strict args / invalid exact asset / automation bounds
```

### Verdict Core

```text
T11 所有 Required PASS → verified
T12 Semantic missing → failed
T13 Semantic unexpected → failed
T14 verified semantic evidence 缺失 → insufficient-evidence
T15 required compile 未执行 → insufficient-evidence
T16 required compile fail → failed
T17 Data Validation invalid → failed
T18 Data Validation not-applicable → not-applicable，不伪造 pass
T19 required Automation 未执行 → insufficient-evidence
T20 required Automation fail → failed
T21 Automation pass 仅按 project/session applicability 使用，不伪造 revision coverage
T22 stale Revision Evidence → insufficient-evidence
T23 wrong Editor Session Evidence → insufficient-evidence
T24 wrong Asset Revision compile/validation Evidence 不可用
T25 all required pass + high-fanout non-blocking risk → suspicious
T26 recommended assertion fail/unknown → suspicious（无 required fail/unknown 时）
T27 required reference-sensitive scope truncated → insufficient-evidence
T28 普通 change 的 non-blocking impact truncation → suspicious（若固定规则如此）
T29 expected no-op 可达到 verified，且没有虚假 transaction/save/verify
T30 多资产一项 required fail → 整体 failed
T31 多资产一项 required unknown、无 fail → insufficient-evidence
T32 result deterministic repeated dict equality
T33 read-only evaluation 不修改 Change Set / Memory / Evidence Store
T34 low token budget 保留 Verdict + Required failures/unknowns
```

### Evidence Capture

若实现 session-local Evidence Store，再覆盖：

```text
T35 only registered tool output can enter store
T36 bounded record count / deterministic eviction or rejection
T37 no arbitrary JSON ingest public tool
T38 project/session binding
T39 stale evidence rejected for applicability
T40 store missing after restart => insufficient-evidence, not reconstructed pass
```

### MCP Contract

```text
Tool Registry / registration order
capabilities.verificationTrust
project status
strict args
Tool counts
error codes + remediation
server instructions
R0/R2 nextActions
```

---

## 17. 真实 UE5.6 Smoke

R3 必须证明它能阻止 False Success，而不是只跑“全绿”路径。

优先复用 R2 已验证的 DirectHost regression fixture；如果 Reforge 不适合安全写入，不要求为了形式强行修改 Reforge 正式资产，但必须如实标注 Smoke 来源。

至少完成：

### S1：Clean Success → `verified`

选择 Data Asset / DataTable / Material Instance 中一个真实受控写入：

```text
Plan / Apply / Save / Independent Verify
→ R2 verified clean
→ 执行 Verification Plan 要求的 Validate（若适用）
→ ue_evaluate_trust_verdict
→ verified
→ rollback / fixture recovery
```

记录全部 Assertion 与 Evidence applicability。

### S2：Blueprint Before/After Evidence Progression

真实 Blueprint narrow write：

```text
修改并完成 persisted/verified semantic evidence
→ 在显式 Compile 前评估
→ insufficient-evidence（compile assertion unknown）
→ ue_compile_blueprint
→ 必要 Data Validation
→ 重新评估
→ verified 或 suspicious（以真实 warning/risk 为准）
```

这个 Case 必须证明 Verdict 会随着**真实新增 Evidence**变化，而不是因为时间或重复调用自动升级。

### S3：Deterministic Failure → `failed`

使用安全 Fixture 制造一种可控失败，例如：

```text
compile failure
required automation failure
validation invalid
或 fixture 中预构造的 semantic missing/unexpected evidence
```

必须证明：

```text
Required FAIL
→ verdict=failed
→ 不会因为 Save / Independent Verify 成功而误报 verified
```

测试后恢复 Fixture。

### S4：Non-blocking Risk → `suspicious`

构造/选择：

```text
所有 Required Assertions PASS
+ high-fanout / recommended consumer validation missing / warning 等固定 non-blocking risk
```

验证：

```text
verdict=suspicious
```

不得为了得到该状态伪造 Evidence；若现有真实 Fixture 无合适 Risk，可用集成 Fixture 对 verdict rule 做真实服务级验证，并在报告中说明。

### S5：Stale / Wrong-session Evidence → `insufficient-evidence`

至少真实或集成级验证一次：旧 session / stale revision 的成功 Evidence 不能用于当前 Required Assertion PASS。

Smoke 至少记录：

```text
changeSetId
planFingerprint
assertion counts by requirement/status
verdict
reasonCodes
evidence count / applicability
nextActions
tokens
elapsed time
recovery result
```

Output 物证不提交。

---

## 18. 性能目标

R3 不应该重新扫描整个项目，也不应该重复执行 Live Action。

Plan / Evaluate 优先消费：

```text
Change Set journal
R2 Semantic Diff Service
R1 bounded Impact Analysis
existing Workflow verify artifacts
captured validation/compile/automation evidence
Revision/Freshness facts
```

禁止：

```text
full project rescan
Evaluator 自动 Compile/Validate/Automation
N×全资产查询
每个 assertion 单独再启动一个 UnrealEditor-cmd
重复导出相同 Canonical
```

Smoke 中至少记录：

```text
verification plan elapsed/token
trust evaluate elapsed/token
assertion count
evidence count
R1/R2 internal reuse timings（能取得时）
```

建议把正式 p50/p95 指标同步给 `feature/performance-benchmarks`，但本轮不切换该长期分支改公共协议。

---

## 19. R3 明确禁止范围

本任务不要做：

```text
R4 Real Agent Benchmark 完整 Runner / 正式跑分
R5 Value Provenance / Execution Trace
Blueprint Runtime Exec / Function / Interface / Dispatcher Trace
PIE gameplay correctness judge
视觉正确性自动判断
性能回归自动 Profiling 系统
通用 Blueprint Graph Writer
新动画 Writer
Level Actor CRUD
Memory Schema 扩展
ChangeSet 持久化 Schema 扩展（默认禁止）
任意 Evidence JSON 注入 API
任意 Python / Shell / Console / UObject Method
模型推断 / confidence / probability
自动选择用户未指定的 Automation Test 名称
Trust Tool 内自动执行 Live Action
```

如果 R3.0 发现核心 Evidence 只有通过极小、必要的 session-local capture 才能复用，可以按 §8 做；不得因此建设通用 Event Sourcing / Observability 平台。

---

## 20. Capability / Tool Registry / Docs

R3 完成后至少同步：

```text
tool_registry.py
mcp_server.py registration + strict args
capabilities.verificationTrust
ue_get_project_status.verificationTrust
server instructions
spec/MCP_SERVER.md
docs/ROADMAP.md
docs/PROJECT_STATUS.md
docs/Plans/AGENT_RELIABILITY_CONTEXT_ANALYSIS_PLAN_20260815.md
R3 design/audit doc
```

建议 Capability 暴露：

```text
available
readOnly
deterministic
modelInference=false
changeSetExplicitOnly=true
changeSetAutoDiscovery=false
planTool
verdictTool
verdictStates
assertionStatuses
assertionRequirements
assertionFamilies
autoExecutesValidation=false
autoExecutesCompile=false
autoExecutesAutomation=false
verifiedMeansUniversalCorrectness=false
```

如实现 Evidence Store，再暴露：

```text
evidenceCapture
  persistent=false（若 session-local）
  arbitraryIngest=false
  projectBound=true
  bounded=true
```

Tool Count 必须同步所有模式契约测试。

---

## 21. 工程门禁

R3 最终验收至少执行：

```text
G1  git status / diff 审计
G2  Ruff（src + tests + 新 scripts）
G3  Python 全量测试
G4  Verification/Trust focused tests
G5  Tool Registry / MCP capability / strict schema tests
G6  PowerShell parser（若脚本受影响）
G7  UE5.6 Direct Build（仅有 C++ 变更时）
G8  真实 UE5.6 R3 Smoke（Success / Insufficient / Failed / Suspicious）
G9  recovery / fixture residue / Editor process cleanup
G10 git diff --check
G11 UTF-8 无 BOM + CRLF
G12 文档状态同步
G13 本地 Commit
```

持续约束：

- 不 Push；
- 不 Reset / Stash / Rebase / Force；
- 不提交 Output / Build / Backups / Saved / Intermediate / 测试生成资产；
- 不修改 Reforge 正式资产；
- 不开放脚本/UObject 任意执行；
- 不进入 R4。

---

## 22. 推荐内部执行节奏

用户要求 R3 一次性完成，但内部可按下面节奏并行：

```text
Primary Agent：R3.0 审计编排
  ├─ Subagent A Workflow/R2/Persistence Evidence
  ├─ Subagent B Compile/Validation/Automation Evidence
  └─ Subagent C Impact/Tests/Registry

Primary Agent：定稿 Assertion / Verdict Rule Matrix

Primary Agent：VerificationPlanService + TrustEvaluator skeleton

并行低冲突实现：
  Subagent A assertion builders / persistence + semantic
  Subagent B evidence capture / compile + validation + automation
  Subagent C tests / capability / fixtures

Primary Agent：公共 Schema / Verdict algorithm / Evidence applicability 最终 Review

→ Core tests
→ Evidence binding tests
→ MCP contract
→ Real UE Success / Insufficient / Failure / Suspicious Smoke
→ 全量 regression
→ 文档同步
→ 本地 Commit
→ 停止
```

多个 Subagent 不得并发直接改相同公共协议文件后再强行合并；`mcp_server.py` / `tool_registry.py` / Verdict rule table 由 Primary Agent 最终统一集成。

---

## 23. R3 最终汇报格式

完成后一次性向用户汇报：

1. Commit / Branch / 工作树 / Push 状态；
2. R3.0 Evidence Audit 结论；
3. `ue_build_verification_plan` 最终 Request/Response Schema；
4. `ue_evaluate_trust_verdict` 最终 Request/Response Schema；
5. Assertion requirement/status/applicability 数据模型；
6. 各 Domain 自动 Verification Plan 规则；
7. `verified / suspicious / failed / insufficient-evidence` 精确判定规则；
8. Compile / Validation / Automation Evidence 如何捕获与绑定；
9. R2 Semantic Diff 与 R1 Impact Analysis 集成；
10. R0 nextExpansion 集成；
11. Tool / Capability / Count 变化；
12. 单元测试与全量门禁；
13. 真实 UE S1–S5 Smoke 结果；
14. 性能 / Token / Evidence 数量观察；
15. 已知限制与 `verificationScope / unverifiedDimensions`；
16. 是否满足 R3 完成定义、是否建议进入 R4。

R3 完成后 **停止**，不得自动进入 R4。

---

## 24. 一句话执行原则

> **R3 不负责把“看起来没问题”包装成成功，而是先生成明确的验证义务，再只用与当前 Change Set / Revision / Session 相匹配的真实 Evidence 去逐条关闭这些义务；证据缺失就是 UNKNOWN，证据冲突就是 FAIL 或失效，只有 Required Assertions 全部被可靠关闭时才能给出 scoped `verified`。**

# UEAgentKit R3 Verification Plan + Trust Verdict 设计与 Evidence Audit

> 日期：2026-08-20
> 分支：`feature/agent-reliability`
> 状态：公共协议、确定性规则、Evidence Capture、真实 UE Smoke 与全量门禁已完成；本地 Commit 以最终报告为准。

## 1. 目标与停止点

R3 为显式 Change Set 生成确定性 Verification Plan，再只使用与当前项目、Editor Session 和最终 Revision 匹配的真实 Evidence 计算 scoped Trust Verdict。它不执行 Compile、Data Validation、Automation、Save、Verify、Rollback 或 Writer，也不把 Agent 自述、成功保存或空错误列表补成 Evidence。

本轮停止在 R3：不进入 R4 Benchmark，不新增动画/通用/Blueprint Graph Writer、任意 UObject/Python/Shell/Console 执行，也不扩展 Change Set 或 Project Memory 持久化 Schema。

## 2. R3.0 三路只读审计结论

### 2.1 Workflow / Persistence / Semantic

- 固定 Plan、Change Set journal、Live Apply、Authorized Save、Commit report、独立 Canonical Verify 和 Backup Manifest 已提供真实来源；R3 不建立第二套写入、Diff 或 Verify。
- Persistence PASS 必须来自与最终 Revision 一致的独立重载证据。Save 成功、Package 存在或 Commit report 单独都不等价于 PASS。
- Semantic 直接复用 R2。真实写入要求 `verified`；expected no-op 使用 persisted baseline exact-revision 特例，不制造 transaction/save/verify。
- R2 missing、unexpected、blocking gap、revision stale 会进入 FAIL、UNKNOWN 或 blocking risk，不能被 Trust 层吞掉。
- Recovery 只证明真实写入具备经验证的 Backup Manifest/rollback material；它是 Informational，不要求先执行 rollback，缺失时也不会被伪装成 readiness。

### 2.2 Compile / Data Validation / Automation

- `ue_compile_blueprint` 有 exact Blueprint、Session、before/after status、`succeeded`、Dirty 和 bounded diagnostics，但原始返回没有可跨调用检索的 Evidence，也没有 final disk Revision。`ue_get_compile_errors.historyComplete=false`，不能证明显式 Compile。
- Validation Evidence 1.0 已有唯一 ID、固定 project/path hash、Session、UTC、disk revisionSet、Dirty 与 stability；判断仍须保留父结果中的 Valid/Invalid/NotValidated。
- Automation report 必须恰好匹配 requested exact test；PASS 要求固定项目隔离进程 `exitCode=0` 且 state=success。`automationRevisionCoverage=not-applicable` 表示 project/session execution，不证明资产 Revision。
- 三类结果原先只存在于单次响应；Evaluator 又禁止自动执行动作和任意 Evidence JSON 注入，因此最小 session-local Store 必要。

### 2.3 R1 Impact / Scope / Registry

- R1 validationTargets、consumers、fanout、truncation 和 gaps 只定义验证范围；“存在静态引用”不是 PASS。
- `setAssetReferenceProperty`、`removeDataTableRow`、`renameDataTableRow` 固定为 reference-sensitive。
- reference-sensitive target 需要 Required `reference-impact`；direct Blueprint consumers 最多 8 个升级为 Required Compile。超限或 required scope 截断时保持 blocking UNKNOWN。
- 普通 change 的 non-blocking R1 risk 可使结果成为 `suspicious`，但不伪造 Required FAIL。
- 两个 R3 Tool 属于 query 组、只读、严格参数、全模式注册；Registry、Capability、Project Status、R0/R2 nextAction 和 Tool Count 同步。

## 3. Public Tool 契约

两个入口使用相同请求：

```text
change_set_id                 required；只接受显式 ID
impact_depth                  0..2，默认 1
required_automation_tests     exact names，去重，最多 8
extra_validation_assets       exact /Game Object Paths，去重，最多 8
max_output_tokens             256..32768
```

不接受项目路径、数据库、Evidence 路径、任意 Assertion JSON/DSL 或任意 Evidence JSON。

`ue_build_verification_plan` 返回 `request / changeSet / planId / planFingerprint / scope / assertions / summary / risks / nextActions / outputBudget`。Fingerprint 绑定 rule version、Change Set、固定 Operation intent、impact depth、tests 与 extra assets。

`ue_evaluate_trust_verdict` 重新生成同一 Plan，返回 `verificationScope / verdict / assertions / evidence / unresolvedRisks / analysisGaps / unexpectedChanges / summary / recommendedNextActions / outputBudget`。Evaluator 只读，不推进 Change Set 或 Store。

## 4. Assertion 数据模型

```text
assertionId
kind / subject
requirement             required | recommended | informational
status                  pass | fail | unknown | not-applicable
applicability           exact-asset-revision | exact-change-set |
                        editor-session | project-session | project |
                        not-applicable | insufficient-binding
sourceRule / requiredEvidenceKinds[] / evidenceRefs[]
reasonCode / message / nextAction
```

Family：`persistence / semantic / freshness / compile / data-validation / reference-impact / automation / recovery`。ID 从 rule version、Change Set、kind、subject、requirement 派生；排序为 requirement → kind → casefold subject → ID。

## 5. Evidence Matrix

| Kind | Requirement Rule | Evidence Source | Applicability | PASS | FAIL | UNKNOWN | N/A | Stale Rule | Next Tool |
|---|---|---|---|---|---|---|---|---|---|
| freshness | 每个 target Required | R2 + current disk Revision | exact asset revision | final revision 稳定且 Plan/R2/disk 一致 | 不单独 FAIL | 缺 revision、dirty、gap、mismatch | 无 | 任一 revision/session mismatch 失效 | `ue_analyze_semantic_diff` |
| persistence | 真实写入 Required；no-op Informational | Save/Commit + independent Canonical | exact asset revision | verified stage、final revision 适用 | 不从 Save bool 单独 FAIL | independent evidence 缺失/stale | expected no-op | dirty/wrong revision 失效 | `ue_verify_asset`/既有 live verify |
| semantic | 每个 target Required | R2 Semantic Diff | exact Change Set | expected=matched，无 missing/unexpected/blocking gap | missing 或 unexpected | stage/gap/stale/coverage incomplete | 无 | R2 stale 即 UNKNOWN | `ue_analyze_semantic_diff` |
| compile | BP narrow write；bounded direct BP consumer Required | captured explicit compile | exact revision + Session | succeeded 且 revision=final/current disk、Clean | applicable compile failed | missing/backlog-only/wrong session/revision/dirty | 非 BP 不生成 | session/revision/disk mismatch | `ue_compile_blueprint` |
| data-validation | 每个真实 target；caller extra target Required | Validation Evidence 1.0 | exact revision + Session | valid；warnings 另报 risk | invalid | unable/partial/wrong binding | not-validated 且 checked=0、unable=0、skipped>0 | partial/dirty/changed 失效 | `ue_validate_asset` |
| reference-impact | reference-sensitive Required | R1 Impact | exact Change Set | bounded scope 完整 | “有引用”不是 FAIL | depth=0/truncated/missing/超限 | 非 sensitive 不生成 | scope/fingerprint 改变 | `ue_analyze_change_impact` |
| automation | caller exact tests Required | captured isolated automation | project session | exact path、exit 0、success | applicable failed/timed-out | 未运行/report error/wrong session | 无 explicit test 不生成 | project/session mismatch；asset coverage 本来 NA | `ue_run_automation_test` |
| recovery | 真实写入 Informational | Manifest + rollback material validation | exact Change Set | 每个 real write 有有效 material | 当前不生成 Required FAIL | material 缺失 | no-op 不生成 | manifest/policy/asset mismatch | 不自动 rollback |

## 6. 自动规则与 Evidence Capture

- 所有真实修改：Required freshness、persistence、semantic、data-validation；Blueprint narrow write 再加 compile。
- reference-sensitive 再加 Required reference-impact；direct Blueprint consumer 最多 8 个 Required compile。
- expected no-op：Required freshness + semantic；persistence 为 Informational/not-applicable，不要求不存在的 Save/Verify；compile/validation/recovery 不生成。
- caller tests 与 extra validation assets 只能增加 Required Assertion，不能降低自动规则。Recovery 对真实写入是 Informational。

`VerificationEvidenceStore` 每个 MCP Workflow session 一个实例：`persistent=false`、`arbitraryIngest=false`、`projectBound=true`、`bounded=true`，最多 256 条。只允许注册 wrapper 捕获 `ue_compile_blueprint / ue_validate_asset / ue_validate_folder / ue_run_automation_test`；导航、任意 JSON、路径和外部报告不能写入。

Compile action 前后读取固定项目 `.uasset` SHA-256，结合 Bridge Session、Dirty 和结果形成 immutable ID/revisionSet。Validation/Automation 保留 Bridge ID、project/session/time/revision semantics；diagnostics 最多 32、revisionSet 最多 8。Evaluator deep-copy 读取且不推进 Store；Server restart 后 Evidence 消失，Required 回到 UNKNOWN。

## 7. Verdict 算法

```text
任一 Required FAIL                         → failed
否则 Required UNKNOWN 或 blocking risk     → insufficient-evidence
否则 Recommended FAIL/UNKNOWN 或任意 risk  → suspicious
否则                                       → verified
```

没有 confidence、score 或模型推断。`verified` 只覆盖响应中的 Plan；始终返回 unverified dimensions：runtime gameplay、visual correctness、performance regression、network replication、external systems、runtime trace。

## 8. R1 / R2 / R0 集成

- 内部复用 R1 bounded Impact 生成 validation scope 和 consumer compile，不复制 Reference Graph。
- 内部复用 R2 Semantic Diff 关闭 freshness/persistence/semantic，不复制 expected/actual Adapter。
- R0 只在显式 Change Set found 时提供 Semantic Diff、Verification Plan、Trust Verdict；默认不执行。
- R2 对显式 Change Set建议 Build Plan；没有 missing/unexpected 时再建议 Evaluate。missing/unexpected 仍保留 R1 impact 建议。

## 9. Bounds、Capability 与当前验证状态

affected assets≤8、assertions≤128、evidence refs≤128、tests≤8、extra validation assets≤8、impact depth≤2、consumer compile≤8。Token 裁剪依次删除 Evidence details、optional assertion details、informational assertions、non-blocking risk messages；Verdict、Required FAIL/UNKNOWN、blocking risk、scope、next actions 优先保留。

Capability/Project Status 暴露 read-only、deterministic、`modelInference=false`、explicit Change Set only、所有 auto-execute=false、`verifiedMeansUniversalCorrectness=false` 与 Store 边界。当前 Registry 计数契约：Offline 10、Offline+Memory 22、Live 43、Live+Memory 55、Workflow 60、Workflow+Memory 72、Live+Workflow 93、Combined+Memory 105。

聚焦 Registry/MCP/Task Context/Semantic Diff 契约测试覆盖顺序、strict args、annotations、Capability/Status、受控 capture 和 R0/R2 渐进入口。真实 UE5.6 DirectHost S1–S3、受控 R1 service-level S4、真实 S1 Evidence applicability S5 均通过，观察到 `verified / suspicious / failed / insufficient-evidence` 四态；S1/S2 fixture recovery 独立验证通过且 Editor 已停止。Ruff 全仓通过，Python 全量 648/648 通过，PowerShell parser 与 `git diff --check` 通过；本轮无 C++ 变更，不要求 UE Direct Build。Commit ID 以最终报告为准。

## 10. 明确限制

- Automation 只有 project-session applicability，没有 asset revision coverage。
- Compile Error backlog 不能代替 explicit compile capture。
- Data Validation `not-validated` 只有满足固定计数规则才是 not-applicable，其余 UNKNOWN。
- Static references 不证明运行时；R3 不做 PIE gameplay、视觉判断或性能 Profiling。
- Session-local Evidence 不跨 restart，也不写入 Project Memory，这是避免 arbitrary ingest 和 Schema 扩张的有意边界。

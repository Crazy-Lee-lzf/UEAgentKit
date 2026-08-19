# UEAgentKit R2 Semantic Diff 设计、证据审计与验收记录

> 日期：2026-08-19
> 分支：`feature/agent-reliability`
> 状态：R2 Semantic Diff 已完成实现、真实 UE5.6 Smoke、全量门禁与文档同步；停止在 R3 之前
> 执行规范：[`../Handoffs/AGENT_RELIABILITY_R2_FULL_HANDOFF_20260818.md`](../Handoffs/AGENT_RELIABILITY_R2_FULL_HANDOFF_20260818.md)

## 1. 目标与停止点

R2 把 Agent 的修改意图、修改前状态和某一证据阶段的实际状态归一化为领域语义变化。它回答：

```text
用户要求修改什么？
实际发生了什么变化？
哪些实际变化符合预期？
哪些预期变化没有发生？
是否发生了额外变化？
哪些关键字段有证据保持不变？
哪些结论因证据不足而无法证明？
```

R2 不是 JSON 文本 Diff，也不把 `beforeRevision != afterRevision` 当作语义变化本身。Revision 只用于绑定证据的新鲜度和持久化身份；领域 Adapter 负责把 Canonical State 与 Operation intent 转成稳定语义。

R2 的 `verified` 仅表示 Semantic Diff 使用了独立重载后的 Canonical Evidence。它不等于整个任务可信，也不生成最终 Trust Verdict。Verification Plan 与 Trust Verdict 属于 R3；本文与 R2 实现必须停在 R3 之前。

## 2. R2.0 复用审计

### 2.1 三向证据矩阵

| 事实类别 | 复用来源 | 可证明内容 | 不能证明的内容 |
| --- | --- | --- | --- |
| Agent intent | 固定 Plan/Patch 中的 operation、target、value、expectedRevision | Agent 被授权执行的目标和值；修改前 Revision 预期 | UE 实际执行了修改；磁盘已保存 |
| Live actual | Live Apply 的 before/after、transaction、receipt 与 report | 当前 Editor Memory 中该受控 Operation 的实际前后值 | 磁盘持久化；独立重载仍可读 |
| Persisted actual | Authorized Save 关联的 Canonical/report，或 commandlet Apply report | 授权保存后磁盘支持的实际语义状态 | 独立进程重新加载仍一致；整个任务正确 |
| Verified actual | Independent Verify/reload 的 Canonical Export | 新 UE 进程重载后观察到的语义状态 | R3 Trust Verdict；运行时行为正确 |
| Revision | Plan expectedRevision、Canonical revision、磁盘 Package SHA-256 | Evidence 与资产版本的绑定、变化与潜在 stale | 单凭 hash 差异无法解释具体语义变化 |
| Workflow bookkeeping | Change Set status、validation、saveState、journal lifecycle | 流程状态、可继续动作、恢复边界 | 不能替代 expected/before/actual Semantic Evidence |

核心取舍：不新建第二套 Writer、Snapshot 或 Verify 系统。Semantic Diff 复用现有 Plan、Live transaction、Authorized Save、commandlet report、Canonical Export 与 Independent Verify，只增加确定性提取、归一化、比较和有界响应。

### 2.2 Canonical 与 Verify 复用

- Data Asset、DataTable 与 Material Instance 复用现有 Canonical Asset Catalog/Reader 输出。
- Blueprint 窄写入 Verify 复用现有 Blueprint Export 的 `full` profile（含 `IncludeUnchangedDefaults`），提供变量默认值、组件属性与 Pin 默认值所需的稳定字段。
- Blueprint Verify 的导出路由属于既有 Reader/Exporter 复用，不扩展 Blueprint Writer。
- validation、saveState、compile 状态和日志可以进入相关证据、Gap 或 next action，但不能复制为 `actualChanges`。

## 3. Public Tool 协议

公共只读 Tool：

```text
ue_analyze_semantic_diff
```

### 3.1 严格请求

```text
change_set_id       required，显式 Change Set ID
stage               auto | live | persisted | verified，默认 auto
asset_paths         optional，精确 /Game Object Path，最多 8 个
include_unchanged   boolean，默认 true
max_changes         1..128，默认 64
max_output_tokens   复用统一 Token Budget，默认 4096
```

`asset_paths` 只过滤返回视图，不改变其他资产的原始 Evidence，也不允许任意磁盘路径。Server 不自动发现 Change Set，不扫描私有 `_change_sets`，不接受 before/after JSON 文件路径，不跨项目比较，不执行任意 Python、Shell、Console 或 UObject 方法。

### 3.2 Response envelope

Schema v1.0 采用 asset-centric 主结构：

```text
schemaVersion
tool
ok
readOnly
request
source
changeSet
evidenceStage
  requested
  selected
  selectionReason
  sources[]
assets[]
  assetPath
  assetClass/domain
  beforeRevision
  afterRevision
  revisionChanged
  expectedChanges[]
  actualChanges[]
  matchedChanges[]
  unexpectedChanges[]
  missingExpectedChanges[]
  unchangedCriticalFields[]
  analysisGaps[]
  summary
analysisGaps[]
risks[]
riskSummary
summary
nextActions[]
outputBudget
```

`source.kind=explicit-change-set` 且 `privateDiscovery=false`，用于明确公共入口没有执行私有发现。`summary` 同时报告 total/returned asset count、filtered 状态及六类结果和 Gap 的计数。`evidenceStage.sources` 必须确定排序并去重。

### 3.3 Change Entry

每个语义变化使用稳定结构：

```text
changeId
assetPath
domain
operation
semanticPath
changeKind
beforeValue
afterValue
expectedValue
source
stage
status
details
```

- `changeId` 基于 Canonical 内容的 SHA-256 派生，不使用进程随机值。
- `semanticPath` 使用领域稳定身份，不使用本地文件路径或不稳定显示文本。
- `changeKind` 使用有界枚举，例如 value-changed、value-added、value-removed、row-added、row-removed、row-renamed、override-added、override-removed、override-changed、container-element-added/removed/changed、unknown-change。
- 未知 Operation 或不完整证据必须进入 `analysisGaps`，不得用字符串比较猜出领域结论。

## 4. 六组语义结果定义

### 4.1 `expectedChanges`

只来自 Plan/Patch/Operation intent。它描述 Agent 被授权要求的修改，而不是从 after state 反推目标。Plan 的 expectedRevision 与 operation target/value 是 intent 的固定来源。

### 4.2 `actualChanges`

来自固定 before evidence 与 selected stage after evidence 的领域比较。禁止把 expected entry 复制为 actual。若 before 或 after 证据不足，应产生 Gap 或 stage unavailable，而不是伪造实际变化。

### 4.3 `matchedChanges`

只有 stable semantic identity、change kind 和领域归一化后的 expected/actual value 均匹配时才成立。Expected no-op 使用独立状态 `matched-expected-no-op`，不伪装成发生过实际写入。

### 4.4 `unexpectedChanges`

selected stage 中实际观察到、但无法与任何 expected change 匹配的变化。它是确定性事实，不直接等同于任务失败；合法 derived metadata 只有在 Adapter 能机械证明时才可分类，否则保守保留为 unexpected/unknown，最终判定留给 R3。

### 4.5 `missingExpectedChanges`

Plan 要求的语义修改在 selected stage 没有对应 actual change。它必须作为独立集合返回，不能仅通过 `matched=false` 隐含表达。

### 4.6 `unchangedCriticalFields`

仅包含各 Adapter 定义的少量关键 invariant，不枚举整个 Canonical State 的所有未变化字段。字段必须有 before/after 双向证据；无法证明时进入 `analysisGaps`。`include_unchanged=false` 只隐藏该展开集合，不改变语义计数和其他证据。

## 5. Evidence Stage

| Stage | after evidence | 能证明 | 不能证明 |
| --- | --- | --- | --- |
| `live` | Live Apply before/after transaction/report | Editor Memory 中受控写入的实际语义结果 | 已保存、独立重载 |
| `persisted` | Authorized Save Canonical 或 commandlet Apply report | 磁盘持久化 Evidence 支持的语义结果 | 独立重载、Trust Verdict |
| `verified` | Independent Verify/reload Canonical | 新进程重载后支持的语义结果 | 整体任务可信、运行时正确 |
| `auto` | 选择所有返回 Operation 都完整支持的最高阶段 | 明确、确定性的最佳可用视图 | 不允许静默回退 |

`auto` 的优先级为 `verified → persisted → live`，响应必须返回 `requested/selected/selectionReason`。显式请求阶段不可用时返回结构化 `semantic-diff-stage-unavailable`，并报告 requested stage 与 available stages；Change Set 没有绑定 Operation 时返回 `insufficient-evidence`。

Stage 完整性按 Change Set 返回范围整体判断。不能把不同 Operation 的 live/persisted/verified Evidence 静默混成一个 selected stage。

## 6. 四类 Domain Adapter

### 6.1 Data Asset / Non-Blueprint Property

覆盖 scalar、Object/Class、Soft Object/Soft Class、Struct、Array、Set 与 Map。

- 引用按稳定 asset/class path 比较，不使用 Python repr。
- Struct 按字段语义递归归一化。
- Array 保留索引语义。
- Set 按 Canonical identity 比较，输入顺序不构成变化。
- Map 按稳定 key identity 比较，递归比较 value。
- Critical invariant 可包括 asset class、未触及 top-level field identity 与受影响容器 shape；只有 Canonical Evidence 足够时才报告。

### 6.2 DataTable

覆盖 cell、single-row multi-field、row add、row remove 与 row rename。

- Cell/row field 使用 `DataTable.Row:<row>.Field:<field>` 身份。
- Add/Remove 使用稳定 row identity；Rename 明确 old→new identity，不拆成无法关联的任意字符串变化。
- 同一 row 多字段修改按 field 归一化。
- Searchable Name/reference-impact 属于 pre-commit safety evidence，不是表格 actual semantic state；可作为 Gap、Risk 或 next action。
- Critical invariant 可包括 row identity、row count、未触及字段或其他 row 结构，但必须由完整 Canonical State 支持。

### 6.3 Material Instance

覆盖 Scalar、Vector、Texture 与 Static Switch。

- Semantic identity 至少包含 parameter type、name 与 association。
- 区分 override 不存在→新增、已有 override→value changed，以及证据支持时的 override removed。
- Vector 使用稳定分量归一化，Texture 使用稳定 Object Path。
- Parent、Expression GUID 和 override metadata 仅在 Canonical Evidence 稳定时用于 details 或 invariant；不得仅比较显示值后宣称完整一致。

### 6.4 Blueprint 窄写入

只覆盖当前已注册且已有稳定 Snapshot/Verify Evidence 的 default/property、component property 与 pin default。

- Property 使用稳定 Blueprint member/property identity。
- Component 使用 component stable identity + property path。
- Pin 使用 graph/node/pin 稳定身份；不以可变显示名冒充 GUID。
- Snapshot 只能证明局部实际值时，必须标记 `supportedActualPartial`/Gap，不能声称已检测全部 Graph unexpected changes。
- R2 不新增通用 Blueprint Graph Writer，也不扩大已有 Writer 的可写范围。

## 7. Multi-operation、Multi-asset 与 operation chain

一个 Change Set 可以包含多个资产和每资产多个 Operation。返回按 `assetPath` 确定排序，资产内按 stable semantic identity 排序。

同一 semantic path 连续修改时：

```text
before: 10
operation 1 expected: 20
operation 2 expected: 30
selected stage actual: 30
```

最终 expected/actual 只生成一条 `10 → 30` 语义变化，`details.operationChain` 保留中间 Operation 的顺序和 provenance。这样既不重复报告最终 actual，也不丢失 Agent intent chain。不同资产的相同属性名不合并；`asset_paths` 过滤只影响响应视图，summary 必须区分 total 与 returned。

## 8. Expected no-op 生命周期

当 Plan expected value 与固定 before value 已相同时，这是 `expected-no-op`，不是“执行失败导致没变化”。最小生命周期要求：

- Operation/Change Set status 可以是终态 `no-op`。
- Operation 使用 `noop_` 身份并公开 `noOp=true`。
- `changed=false` 且绑定显式 Change Set 时，绑定 no-op Operation；不创建假的 Live Apply receipt、transaction 或 journal record。
- 写入响应保持 `liveApplyReceipt=""`、`changeSetOperationId=noop_*`、`changeSetBound=true`、`journalPersisted=false`；Change Set journal 自身是否持久化单独报告。
- 全部 Operation 为 no-op 时，validation 聚合为 `no-op`，saveState 为 `not-required`，Task Context 把它视为终态。
- 只有固定 baseline Canonical revision 与 Plan `expectedRevision` 精确相等时，才允许形成 persisted no-op Evidence。
- No-op 不产生 live 或 verified stage；请求这些阶段必须返回 stage unavailable。
- 同一资产混合 no-op 与真实写入而无法形成可信统一最终 snapshot 时，保守返回 stage unavailable，不拼接推测状态。

No-op 的 matched entry 使用 `matched-expected-no-op`；它说明“目标原本已经满足”，不说明 Writer 执行过一次成功修改。

## 9. Revision、Freshness、Gap 与 Risk

每个资产至少报告：

```text
beforeRevision
afterRevision
revisionChanged
stageEvidenceRevision
```

Revision 与语义结果必须来自同一证据链。固定 SQLite、Revision Export、磁盘 Package 或 stage report 不一致时，不能静默选取便利值。

稳定 Gap/Risk 至少包括：

```text
semantic-diff-evidence-stale
semantic-diff-stage-unavailable
semantic-diff-unexpected-change
semantic-diff-missing-expected-change
semantic-diff-unsupported-operation
semantic-diff-truncated
insufficient-domain-snapshot-for-unexpected-change-detection
```

`analysisGaps` 表达“当前证据不知道什么”；`risks` 表达已经确定存在、会影响后续判断或验证范围的事实。两者都不包含 model confidence。Unexpected 或 Missing 存在时，`nextActions` 可以建议带显式目标调用 `ue_analyze_change_impact`；这只是 R1 渐进展开，不自动执行 R1。

## 10. R0/R1 集成

- R0 `ue_get_task_context` 只有在请求提供显式且存在的 `change_set_id` 时才建议 Semantic Diff expansion。
- 默认 Task Context 不自动运行 Semantic Diff，也不自动发现 Change Set。
- R2 对 missing/unexpected change 建议 R1 `ue_analyze_change_impact`，但不把 Reference Graph 遍历复制进 R2。
- R1 可以在显式 Change Set 已存在时建议 R2；R1 不自动执行 R2。
- R0/R1/R2 都保持 Server 零模型推断，事实空白明确进入 Gap。

## 11. 确定性、边界与 Token Budget

- Asset、Change Entry、Gap、Risk、source 与 next action 使用固定排序。
- Stable ID 使用 SHA-256，不依赖 Python hash、时间或对象地址。
- 最多返回 8 个资产；`max_changes` 限制 change entry 数，默认 64、最大 128。
- Unchanged Critical 与 Gap 还有独立内部上限，避免 Canonical State 膨胀响应。
- `max_output_tokens` 使用项目统一估算与范围校验。
- 裁剪优先减少可展开 details、unchanged critical 和低优先级条目；核心 request、stage、summary、risk 与 outputBudget 必须保留。
- 所有裁剪在 `outputBudget.truncated/truncationReason` 和 `semantic-diff-truncated` 中显式报告；不允许静默丢失计数。

## 12. Public contract 与 Tool count

`ue_analyze_semantic_diff` 属于 query 组，注册顺序、strict args、annotations、capabilities、project status 与 MCP 文档必须同步。加入 R2 后的目标计数为：

| 模式 | 不启用 Memory | 启用 Memory |
| --- | ---: | ---: |
| Offline | 8 | 20 |
| Live | 41 | 53 |
| Workflow-only | 58 | 70 |
| Combined Live + Workflow | 91 | 103 |

计数是注册契约，不代表新增 UE Writer 数量。R2 新增一个只读分析 Tool，没有扩展动画 Writer、通用 Writer 或 UObject 能力。

## 13. 测试矩阵与门禁

### 13.1 Python/契约测试目标

- 六组语义结果的正例、missing、unexpected 与 expected-no-op。
- 四 Domain 的 normalization、stable semantic path 与 critical invariant。
- 多资产、多 Operation、同 path chain collapse。
- `auto/live/persisted/verified` 选择、显式 stage unavailable 与 source provenance。
- Revision stale、unsupported/partial Evidence、Gap/Risk 分类。
- `max_changes`、`max_output_tokens`、固定排序与 stable ID 确定性。
- Tool Registry、MCP strict args、annotations、capability、project status 与所有模式 count。
- Change Set no-op status、receipt、终态、validation/saveState 与重启持久化。
- Blueprint Verify exporter 路由及独立 `full` Canonical Evidence（含 `IncludeUnchangedDefaults`）。

### 13.2 最终门禁

Primary Agent 已在当前 R2 工作树上完成复核：

```text
Ruff: src + tests/python + tests/integration passed
Python: unittest discover 628/628 passed
PowerShell parser: 两个修改/新增 Smoke scripts passed
git diff --check: passed
UTF-8 no BOM + CRLF audit: passed
UnrealEditor/UnrealEditor-Cmd 遗留进程: none
EditorBridge descriptor / 生成的 Transactions fixture: none
C++ 变更: none；UE5.6 Direct Build 不要求
```

最终 Diff Review 已确认 verified actual 来自独立 Canonical、unexpected 检测与 Revision stale 风险均已落地并有回归测试；局部测试未替代上述全量门禁。

## 14. 真实 UE5.6 Smoke 设计与执行证据

真实 Smoke 使用 UE5.6 DirectHost regression fixture，不冒充 Reforge，也不提交 `Output/`、`Build/`、`Saved/`、日志或生成资产。

### 14.1 Live Closed Loop Semantic Diff

入口：

```powershell
scripts/TestMcpLiveClosedLoop.ps1 `
  -EngineRoot <UE_5.6> `
  -ProjectPath <DirectHost.uproject>
```

最终实测覆盖 Data Asset、Material Instance、DataTable cell 与 DataTable rename；每个域检查 live、persisted、verified 三阶段，共 12 个 Semantic Diff 结果。12 个结果均为 expected=actual=matched=1、unexpected=missing=0、`truncated=false`；耗时 50.289–91.660 ms，估算输出 1101–1534 tokens。live 阶段按证据边界保留 2 个 gap，persisted/verified gap=0。

恢复门禁使用独立 Canonical fixture verification，最终 5/5 通过；冻结 Revision Export 与 SQLite 均未改写，四个预期保存的 package 确实变化。UE 在 Reset 时可能重写 package bytes，即使语义基线相同也不保证 SHA-256 恢复到初始字节；因此本 Smoke 不把 Reset 后 package hash 相等当成恢复成功的必要条件。

### 14.2 Blueprint commandlet Semantic Diff

入口：

```powershell
scripts/TestMcpBlueprintSemanticDiff.ps1 `
  -EngineRoot <UE_5.6> `
  -ProjectPath <DirectHost.uproject>
```

流程使用已有 `setVariableDefault`：显式 Change Set → commandlet Apply → persisted Semantic Diff → 独立 Blueprint `full` Verify（`IncludeUnchangedDefaults`）→ verified Semantic Diff → rollback。persisted 与 verified 均为 expected=actual=matched=1、unexpected=missing=0；persisted 保留 2 个证据 gap，耗时 23.845 ms、1450 tokens；verified actual 来自独立 `full` Canonical，gap=0、unchanged=1、耗时 39.305 ms、1259 tokens。Rollback 恢复了目标 package hash，冻结 Revision Export 未变化，生成的 Transactions fixture 已清理。

### 14.3 观测记录的使用原则

最终 Smoke summary 位于 `Output/McpLiveClosedLoopSmoke/semantic-diff-summary.json` 与 `Output/McpBlueprintSemanticDiffSmoke/semantic-diff-summary.json`，仅作为本地证据，不纳入提交。以上数量、耗时、Token 和 Gap 结论均由 Primary Agent 从最终一次成功运行复核；两组测试都使用 UE5.6 DirectHost regression fixture，不冒充 Reforge。

## 15. 性能与 Token 目标

R2 不重新导出整个项目。单次分析只读取显式 Change Set 涉及的有界资产与已存在 Evidence；真实 UE 导出成本属于 Apply/Verify 流程。最终实测 Semantic Diff 为 23.845–91.660 ms、估算输出 1101–1534 tokens，未发生静默截断。

性能报告至少包含：

```text
selected stage
asset/expected/actual/matched counts
elapsed milliseconds
estimated output tokens
truncated + reason
analysis gap count
```

不能为了满足 Token 指标隐藏 unexpected、missing、Gap、Risk 或 Revision 身份。

## 16. 已知限制与明确延期

R2 明确保留以下边界：

- R3 Verification Plan / Trust Verdict 未实现；verified Semantic Diff 不等于 Trust Verdict。
- R4 Real Agent Benchmark runner 未实现。
- R5 Value Provenance / runtime execution chain 未实现。
- 不新增 generic Blueprint Graph Writer。
- 不扩展新的 Animation Writer；已有动画能力只作参考。
- 不开放任意 UObject Method、Python、Shell、Console 或脚本执行。
- 不新增 generic Writer 或任意资产 CRUD。
- Server 不做模型推断、confidence 或“likely correct”判断。
- Blueprint 窄快照不足以证明全部 Graph 未变化时必须保留 Gap。
- 合法 UE derived metadata 若无法机械分类，保守保留为 unexpected/unknown。
- No-op 与同资产真实写入混合、且缺少完整统一 snapshot 时，不拼接推测结果。

## 17. R2 完成定义

R2 只有在以下条件全部满足后才能标记完成：公共协议与四 Adapter 落地；六组结果、四 Stage、多 Operation/asset/chain、no-op、Revision/Gap/Risk/Token 边界有测试；R0/R1 渐进集成成立；两组真实 UE5.6 Smoke 通过并恢复 fixture；Ruff、Python 全量、MCP/Registry/count、PowerShell parser、`git diff --check` 与编码换行门禁通过；相关文档同步；本地 Commit 后工作树干净且未 Push。

完成 R2 后必须停止，不自动进入 R3。

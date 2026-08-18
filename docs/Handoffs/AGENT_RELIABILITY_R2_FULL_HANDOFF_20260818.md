# UEAgentKit Agent Reliability R2 Full Handoff — Semantic Diff

> 日期：2026-08-18  
> 开发分支：`feature/agent-reliability`  
> 起始基线：`b9203e4`（R1 Impact Analysis 已完成）  
> 执行模型：DeepSeek Pro 主 Agent，可并行使用 DeepSeek Flash 子代理做只读审计、测试补齐、真实 Smoke 与文档一致性检查  
> 任务模式：**一次性完成整个 R2；允许内部拆分 checkpoint/commit，但不要在中途等待用户确认。R2 全部完成后统一汇报并停止，不进入 R3。**  
> Git 纪律：不 Push；不 Reset/Stash/Rebase/Revert 用户工作；不提交 Output/Backups/Intermediate/Saved/日志/临时 UE 资产。

---

## 1. R2 的产品目标

R0 已回答：

```text
我现在应该看什么？
```

R1 已回答：

```text
如果修改目标，静态引用图中哪些对象可能受影响？
```

R2 要回答：

```text
用户要求修改什么？
实际发生了什么变化？
有没有额外变化？
哪些关键部分明确保持不变？
哪些变化当前证据无法解释？
```

R2 的定位是 **Semantic Diff（语义变化归一化层）**，不是 JSON 文本 Diff，也不是最终正确性判定。

核心原则：

> `beforeRevision != afterRevision` 只能证明 Package 发生了变化；R2 必须把变化映射回 Agent 的修改意图与领域语义。  
> R2 可以证明“实际变化与预期变化是否一致/是否存在额外变化”，但不能仅凭这些事实宣称整个任务 `verified`；最终 Trust Verdict 属于 R3。

---

## 2. R2 完成定义

R2 只有同时完成以下内容才算完成：

1. 完成 R2.0 现有 Writer / Change Set / Snapshot / Save / Verify / Canonical Export 复用审计；
2. 建立统一、确定性的 Semantic Diff 内部数据模型；
3. 提供一个稳定 Public Tool，建议名称：

```text
ue_analyze_semantic_diff
```

4. Public Tool 至少能够以 **显式 `change_set_id`** 为主入口，自动从既有 Workflow Evidence 提取 expected / before / actual；
5. 覆盖当前非动画主流受控写入域：
   - Blueprint 窄范围 Property / Component / Pin Default 等已有稳定受控写入；
   - Non-Blueprint / Data Asset Property，包括当前已支持 scalar / object/class / soft refs / struct / array / set / map；
   - DataTable：Cell、多字段 Row、Add/Delete/Rename Row 与当前已注册的稳定 Operation；
   - Material Instance：Scalar / Vector / Texture / Static Switch；
6. 对上述每个 Domain 建立确定性 adapter，不允许 generic string compare 冒充领域语义；
7. 输出统一区分：

```text
expectedChanges
actualChanges
matchedChanges
unexpectedChanges
missingExpectedChanges
unchangedCriticalFields
analysisGaps
risks
summary
nextActions
outputBudget
```

8. 能区分 Live Apply、Saved/Persisted、Independent Verify 不同 Evidence Stage；
9. 支持多 Operation / 单资产多 Operation / 一个 Change Set 多资产；
10. 有界输出、固定排序、稳定 ID、确定性结果；
11. 与 R1 Impact Analysis 建立渐进展开关系，但不把 R1 图遍历复制进 R2；
12. 至少完成一组真实 UE/Reforge 或项目 Fixture Smoke，覆盖 4 个核心 Domain 中尽可能多的真实可写域；若某 Domain 缺少适合 Reforge 的真实资产，使用已有真实回归工程/Fixture，并明确区分真实 UE Evidence 与纯 Python 测试；
13. Ruff、Python 全量、Schema/Tool Registry/MCP 契约、`git diff --check`、CRLF/UTF-8 无 BOM 全部通过；有 C++ 变更才要求 UE5.6 Direct Build；
14. 更新 `docs/ROADMAP.md`、`docs/PROJECT_STATUS.md`、本总计划、`spec/MCP_SERVER.md` 与 R2 设计/审计文档；
15. R2 完成后本地 Commit，工作树干净，**停止，不进入 R3**。

---

## 3. R2.0：先做复用审计，但不中途停

主 Agent 开始实现前，至少并行安排 2–3 个 Flash 只读审计。

### Flash A：Workflow / Change Set Evidence 审计

重点确认：

```text
ChangeSetRecord
Operation / transaction 记录
beforeValue / afterValue
beforeRevision / afterRevision
Save State
Validation State
Independent Verify Report
Backup Manifest
Live Apply receipt
Authorized Save report
```

回答：

- 哪些字段是 Agent intent；
- 哪些字段是 Live Editor 实际结果；
- 哪些字段是 disk persisted Evidence；
- 哪些字段能被独立 Verify 再确认；
- 哪些字段只是 workflow bookkeeping，不能作为 Semantic Diff Evidence。

### Flash B：Domain Snapshot / Canonical State 审计

重点审计当前所有 **非动画已注册 Writer**：

- operation 名；
- target/value schema；
- before/after snapshot 的真实结构；
- canonical serialization / normalized value；
- save / verify 输出是否包含完整领域状态；
- 哪些字段可定义为 `critical unchanged`；
- 哪些 Domain 目前证据不足。

要求形成 adapter matrix，例如：

```text
Operation / Domain
Expected intent source
Before evidence source
Actual live evidence source
Persisted evidence source
Stable identity
Semantic equality rule
Critical unchanged set
Unsupported edge cases
```

### Flash C：测试 / Registry / MCP 契约审计

列出：

- Tool Registry 插入位置；
- MCP 注册顺序；
- capability / project status / instructions；
- 所有硬编码 Tool count / slice；
- strict args；
- error code / remediation；
- 既有 Domain tests 可复用 fixture；
- 真实 UE smoke harness。

主 Agent 必须核对审计结论后再定最终 schema，但**不需要向用户中途汇报**。

---

## 4. Public Tool 设计基线

建议：

```text
ue_analyze_semantic_diff
```

### 4.1 请求

第一版建议围绕显式 Change Set：

```text
change_set_id           required
stage                   auto | live | persisted | verified
asset_paths             optional exact /Game Object Path filter, <= 8
include_unchanged       default true
max_changes             bounded
max_output_tokens       bounded, reuse existing budget conventions
```

允许 Agent 在 R2.0 后根据现有 Workflow 证据做最小调整，但禁止：

- 自动发现 Change Set；
- 扫描私有 `_change_sets`；
- 接受任意本地文件路径作为 before/after JSON；
- 允许跨项目比较；
- 允许调用任意 Python/UObject/Console。

### 4.2 Stage 语义

必须明确区分：

```text
auto
  选择当前 Change Set 能提供的最高可信、最完整阶段，但必须在响应中显式报告 selectedStage 与 selectionReason。

live
  使用 Live Apply / transaction Evidence；只证明 Editor Memory 中实际变化，不等于 persisted。

persisted
  使用 Authorized Save / disk-backed Evidence；证明保存后的实际变化。

verified
  仅当已有 Independent Verify / reload Evidence 时使用；仍然只是 Semantic Diff 的 verified evidence stage，不等于 R3 Trust Verdict。
```

若请求 stage 的证据不存在，必须返回结构化 `insufficient-evidence`，不能偷偷回退而不说明。

---

## 5. 统一 Semantic Diff Schema

建议 Response：

```text
SemanticDiff
├─ schemaVersion
├─ tool
├─ ok
├─ readOnly
├─ request
├─ source
├─ changeSet
│  ├─ changeSetId
│  ├─ taskId
│  ├─ status
│  └─ affectedAssets[]
├─ evidenceStage
│  ├─ requested
│  ├─ selected
│  ├─ selectionReason
│  └─ sources[]
├─ assets[]
│  ├─ assetPath
│  ├─ assetClass/domain
│  ├─ beforeRevision
│  ├─ afterRevision
│  ├─ expectedChanges[]
│  ├─ actualChanges[]
│  ├─ matchedChanges[]
│  ├─ unexpectedChanges[]
│  ├─ missingExpectedChanges[]
│  ├─ unchangedCriticalFields[]
│  ├─ analysisGaps[]
│  └─ summary
├─ expectedChanges[]          // 全局扁平可选视图
├─ actualChanges[]
├─ unexpectedChanges[]
├─ missingExpectedChanges[]
├─ unchangedCriticalFields[]
├─ analysisGaps[]
├─ risks[]
├─ riskSummary
├─ summary
├─ nextActions[]
└─ outputBudget
```

实现时不必机械重复所有 section；可以选择 asset-centric 主结构 + global summary，但字段职责必须等价且契约清晰。

---

## 6. Change Entry 统一语义

每一个语义变化必须具有稳定定位，不要只返回任意字符串。

建议：

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
```

其中：

### `semanticPath`

领域稳定路径，例如：

```text
Blueprint.Defaults.MaxWalkSpeed
Blueprint.Component:CharacterMovement.MaxWalkSpeed
Blueprint.Graph:EventGraph.Node:<stable-id>.Pin:<pin-id>.DefaultValue
DataAsset.Property:Config.Damage
DataTable.Row:Rifle.Field:Damage
DataTable.Row:Rifle
MaterialInstance.Scalar:Roughness
MaterialInstance.Texture:BaseColor
MaterialInstance.StaticSwitch:UseDetail
```

实际格式由现有 stable identity 决定；不要发明不稳定显示名作为唯一身份。

### `changeKind`

建议固定枚举，例如：

```text
value-changed
value-added
value-removed
row-added
row-removed
row-renamed
override-added
override-removed
override-changed
container-element-added
container-element-removed
container-element-changed
unknown-change
```

不要让每个 Domain 自由返回任意 kind 字符串。

---

## 7. Expected / Actual / Matched / Unexpected / Missing 的定义

### 7.1 `expectedChanges`

来自 **Change Set / Plan / Operation intent**，不是由 after state 反推。

例如用户要求：

```text
Roughness 0.4 -> 0.7
```

expected 必须来源于计划/Operation target+value。

### 7.2 `actualChanges`

必须来自 before evidence 与所选 stage 的 after evidence比较。

不是：

```text
把 expected 复制一份叫 actual
```

### 7.3 `matchedChanges`

只有 stable semantic identity + expected/actual semantic value 都匹配时才进入。

### 7.4 `unexpectedChanges`

actual 中存在、但无法与任何 expected change 匹配的变化。

这是 R2 最关键的输出之一。

### 7.5 `missingExpectedChanges`

expected 中存在、但 selected stage 没观察到对应 actual change。

例如：

```text
计划修改 Damage 30→35
实际 persisted 仍为 30
```

必须显式出现，而不是只给 `matched=false`。

### 7.6 No-op

如果 expected 新值与 before 本来相同：

```text
expected-no-op
```

必须和“执行失败导致没变”区分开。

---

## 8. `unchangedCriticalFields` 不能做成“所有没变字段”

R2 不应把整个 Canonical State 中数千个未变化字段塞进响应。

每个 Domain adapter 只定义少量、可证明、对回归重要的 critical invariants。

示例：

### Material Instance

修改 Scalar 时，可检查：

```text
Parent unchanged
其他 parameter category identity unchanged
未触及的目标 parameter override state unchanged（有稳定证据时）
```

### DataTable

修改一个 Cell 时：

```text
Row identity unchanged
Row count unchanged
其他目标字段 unchanged
其他 rows 未发生结构变化（证据足够时）
```

### Data Asset

修改一个 property 时：

```text
asset class unchanged
非目标 top-level fields / container shape critical subset unchanged
```

### Blueprint 窄写入

```text
asset identity unchanged
compile/graph structural invariants 若现有 snapshot 可证明则记录
非目标 component/pin/property stable identity unchanged
```

如果现有 Evidence 无法证明，就放入 `analysisGaps`，不要伪造 PASS。

---

## 9. Domain Adapter 要求

建议内部建立注册式 Semantic Diff adapter，而不是一个巨大 `if operation == ...`。

概念：

```text
SemanticDiffAdapter
  supports(operation/domain)
  build_expected(operation)
  extract_before(evidence)
  extract_actual(stageEvidence)
  diff(before, actual)
  critical_invariants(before, actual, expected)
  normalize_value(...)
```

是否落成 class/protocol 由主 Agent决定；重点是避免四个 Domain 互相污染。

### 9.1 Data Asset / Non-Blueprint Property

必须覆盖当前已有稳定 value model：

```text
scalar
object/class
soft object/class
struct
array
set
map
```

容器比较必须使用已有 canonical/normalized identity：

- Set 不按输入顺序误判；
- Map 使用稳定 key identity；
- Struct 按字段语义；
- Object/Class 引用按 stable asset/class path；
- 不用 Python repr/stringification 比较。

### 9.2 DataTable

必须覆盖当前已注册稳定 Operation，包括：

```text
cell/row field update
single row multi-field update
row add
row delete
row rename
```

若 Searchable Name/reference-impact 仅属于 pre-commit safety gate，不代表 semantic state 本身，不要混入 actualChanges；可作为 analysisGap/relatedEvidence 或 nextAction。

### 9.3 Material Instance

至少：

```text
Scalar
Vector
Texture
Static Switch
```

必须区分：

```text
override 不存在 -> 新增 override
override 存在 -> value changed
override 移除（如果当前 Writer 支持）
```

Expression GUID / override metadata 若已有稳定状态，应保留在 evidence/details，不要只比较显示值。

### 9.4 Blueprint 窄写入

覆盖当前**已经注册并有稳定 Snapshot/Verify Evidence** 的 Blueprint Operation。

优先：

```text
Blueprint default/property
Component property
Pin default
```

不要为了 R2 扩展通用 Blueprint Graph Writer。

如果某个既有 Operation 的 Snapshot 不足以判断 unexpected changes，应：

```text
supportedExpected=true
supportedActualPartial=true
analysisGap=insufficient-domain-snapshot-for-unexpected-change-detection
```

而不是修改 Writer 范围来“顺便补完”。

---

## 10. Multi-operation / Multi-asset 语义

R2 必须正确处理：

```text
一个 Change Set
  Asset A: 3 operations
  Asset B: 2 operations
```

要求：

- stable semanticPath 去重；
- 同一路径连续多次修改时，明确最终 expected intent 与 intermediate operation chain；
- actual change 只描述 selected stage 的 before→after；
- 如果多个 operation 对同一字段 10→20→30，最终 semantic diff 不应错误报告两条最终 actual change，但必须在 provenance/details 中能说明 expected chain；
- asset filter 不改变其它资产原始 Evidence，只影响返回视图；
- summary 必须报告 filtered/total counts。

---

## 11. Revision / Freshness / Persistence 边界

Semantic Diff 与 Revision 必须绑定。

至少报告：

```text
beforeRevision
afterRevision
revisionChanged
stageEvidenceRevision
```

高价值确定性风险建议：

```text
semantic-diff-evidence-stale
semantic-diff-stage-unavailable
semantic-diff-unexpected-change
semantic-diff-missing-expected-change
semantic-diff-unsupported-operation
semantic-diff-truncated
```

Severity 由现有项目风险分级规范决定。

注意：

```text
unexpectedChanges > 0
```

并不一定表示任务失败——某些 UE 保存可能发生合法 derived metadata 变化；若 adapter 能证明属于允许的 derived change，可分类为 `allowedDerivedChanges` 或 analysis details。若不能证明，保守留在 unexpected/unknown，R3 再决定 Trust Verdict。

---

## 12. R1 Impact Analysis 集成

R2 不重新遍历 Reference Graph。

建议 `nextActions` 在有 unexpected/missing change 或高影响 target 时提供：

```text
ue_analyze_change_impact
```

带显式 target assets。

同时 R1 的 `nextActions` 可以在 Change Set 已存在时建议：

```text
ue_analyze_semantic_diff(change_set_id=...)
```

但不要让 R1 自动执行 R2。

R0 `ue_get_task_context` 可以增加一个轻量 nextExpansion：

```text
semantic-diff-explicit-change-set
```

仅在显式 change_set_id found 时出现；默认 Context 不自动跑 Semantic Diff。

---

## 13. Budget / Determinism / Boundary

必须使用统一有界输出策略。

建议硬边界：

```text
max assets        <= 8
max change entries <= 128（最终值由审计决定）
max critical unchanged <= 64
max analysis gaps <= 32
```

排序固定：

```text
assetPath casefold
→ semanticPath
→ changeKind
→ stable changeId
```

Token 裁剪优先级建议：

```text
1. verbose evidence/details
2. unchangedCriticalFields details
3. actualChanges auxiliary metadata
4. matchedChanges duplicated details
5. bounded change entry count
```

永远优先保留：

```text
changeSet identity
selected evidence stage
asset identity
unexpectedChanges summary
missingExpectedChanges summary
riskSummary
truncation state
```

最低保障 envelope 即使超过最小 token budget，也必须显式 `truncated=true`，沿用 Task Context / Impact Analysis 约定。

---

## 14. Analysis Gaps / Unsupported 语义

R2 必须明确记录“不知道”，而不是为了完成率猜测。

至少支持：

```text
unsupported-operation
unsupported-domain
missing-before-evidence
missing-after-evidence
missing-independent-verify
insufficient-domain-snapshot-for-unexpected-change-detection
canonical-state-unavailable
revision-evidence-unavailable
allowed-derived-change-not-proven
```

禁止：

```text
probably-safe
likely-unchanged
confidence=0.8
modelScore
```

R2 仍保持 Server 零模型推断。

---

## 15. 测试矩阵

建议新建：

```text
tests/python/test_semantic_diff.py
```

至少覆盖以下类别。

### Core

```text
T1  single expected == actual
T2  expected change missing
T3  unexpected actual change
T4  expected no-op
T5  multiple operations same asset
T6  same semantic path changed multiple times
T7  multi-asset change set
T8  asset filter
T9  deterministic repeated output
T10 low token budget
T11 unsupported operation
T12 requested stage unavailable
T13 revision mismatch/stale evidence
T14 no private change-set discovery
T15 read-only/no persistence mutation
```

### Data Asset

覆盖 scalar / refs / struct / array / set / map 的 semantic equality 与 unexpected detection。

### DataTable

覆盖 cell / multi-field / add / delete / rename；确保 Row Rename 不被错误表示为 delete+add（除非底层证据确实只能如此，届时明确 analysisGap）。

### Material Instance

覆盖 scalar/vector/texture/static-switch，以及 override identity。

### Blueprint

覆盖所有当前被声明为 R2 支持的窄 Operation；至少有 property/component/pin 中现有稳定证据支持的类型。

### MCP contract

- Tool 注册模式；
- strict args；
- capability；
- project status；
- Tool counts / registry order；
- error remediation。

---

## 16. 真实 UE / Reforge Smoke

R2 必须至少有真实 Evidence，不允许只做 synthetic Python object diff。

优先策略：

### S1 Data Asset / Non-Blueprint Property

已有真实 Fixture 或 Reforge 中可安全临时测试的资产：

```text
before
→ explicit controlled write
→ semantic diff live
→ save
→ semantic diff persisted/verified
→ rollback
```

### S2 DataTable

至少一个 cell/multi-field 或 row operation。

### S3 Material Instance

至少一个 scalar/vector/texture/static switch 中可安全覆盖的操作；如果已有真实 regression fixture，直接复用。

### S4 Blueprint narrow write

至少一个已有稳定真实回归的 Blueprint property/component/pin operation。

要求：

- 使用项目既有 Policy / controlled workflow；
- 测试后 rollback/revert，不能污染 Reforge；
- 物证放 Output，不提交；
- 记录每个 Smoke 的 expected/actual/unexpected/missing/unchanged counts；
- 记录 stage、revision、tokens、elapsed time；
- 如果 Reforge 本身存在已有 Blueprint 编译错误，必须与测试修改隔离并如实记录，不把既有错误算作 R2 引入。

如果某 Domain 无法在 Reforge 安全实测，可使用已有 UE5.6 regression fixture，但最终报告必须说明哪些是 Reforge、哪些是其他真实 UE fixture。

---

## 17. 性能目标

R2 不应重新导出整个项目。

优先消费：

```text
Change Set journal
Operation receipts
Backup manifest
Independent verify report
单资产 canonical/domain snapshot
```

建议记录：

```text
single asset / 1 op p50/p95
single asset / 10 ops p50/p95
multi asset / 20+ ops p50/p95
token size
change count
```

具体正式门槛可根据真实 Smoke 给出，并同步给 `feature/performance-benchmarks`，但本轮不切换长期性能分支修改其独立实验工作。

禁止为 R2 做：

```text
full project rescan
全项目 Canonical reload
每个字段单独调用一次 MCP/Editor
N×全资产遍历
```

---

## 18. R2 明确禁止范围

本任务不要做：

```text
R3 Verification Plan / Trust Verdict
R4 Real Agent Benchmark 完整 Runner
R5 Value Provenance
Blueprint Exec / Function / Interface / Dispatcher Trace
通用 Blueprint Graph Writer
新动画 Writer
Additive Batch
Retarget tail
Level Actor CRUD
Memory Schema 扩展
ChangeSet Schema 持久化扩展（除非 R2.0 证明现有 Evidence 无法实现核心目标，且必须先在最终汇报列为 blocker；默认禁止）
任意 Python / Shell / Console Tool
模型推断
```

动画已有 Diff/Verify 代码只允许作为参考实现，不作为 R2 必须新增覆盖面。

---

## 19. 推荐内部执行节奏

虽然用户要求 R2 一次性完成，但内部仍建议分工：

```text
主 Agent：R2.0 审计编排 + 最终 Schema
  ├─ Flash A Workflow Evidence
  ├─ Flash B Domain Snapshot Matrix
  └─ Flash C Tests/Registry/Smoke

主 Agent：SemanticDiffService + Public Tool skeleton

并行：
  Flash/主 Agent A DataAsset adapter
  Flash/主 Agent B DataTable adapter
  Flash/主 Agent C MaterialInstance adapter
  主 Agent Blueprint adapter / common normalization review

→ Core tests
→ Domain tests
→ MCP contract
→ Real UE Smoke
→ 全量 regression
→ 文档同步
→ 本地 Commit
→ 停止
```

若多个子代理需要实际编辑同一公共文件（例如 `mcp_server.py`），不要并发直接改；由主 Agent 统一集成，子代理优先提供建议/测试或修改低冲突独立模块。

---

## 20. R2 最终汇报格式

完成后一次性向用户汇报：

1. Commit / Branch / 工作树 / Push 状态；
2. R2.0 复用审计结论；
3. `ue_analyze_semantic_diff` 最终 Request/Response Schema；
4. Expected / Actual / Matched / Unexpected / Missing / Unchanged 定义；
5. Evidence Stage（live/persisted/verified）规则；
6. 四个 Domain adapter 覆盖矩阵；
7. Multi-operation / multi-asset / same-path chain 处理；
8. Revision / Freshness / Analysis Gaps / Risks；
9. R0/R1 集成；
10. Tool/Capability/Count 变化；
11. 单元测试与全量门禁；
12. 真实 UE/Reforge Smoke；
13. 性能/Token 观察；
14. 已知限制与明确延后；
15. 是否满足 R2 完成定义、是否建议进入 R3。

R2 完成后 **停止**，不得自动进入 R3。

---

## 21. 一句话执行原则

> R2 不是证明“Package 变了”，而是把 Agent 的修改意图与真实 before/after Evidence 对齐，明确指出哪些变化符合预期、哪些没有发生、哪些额外发生、哪些关键部分有证据保持不变；无法证明的部分必须显式保留为 Gap，最终任务是否可信留给 R3。

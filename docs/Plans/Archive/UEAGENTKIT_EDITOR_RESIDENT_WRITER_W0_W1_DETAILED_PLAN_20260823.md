# UEAgentKit Editor-Resident Writer W0/W1 Detailed Plan

> Execution update (2026-08-24): W0 baseline checkpoint `142ca1e` and W1 implementation checkpoint `e2c0994` are complete. W1 is now in acceptance-pending state; W2 must not start before the acceptance gate closes.
>
> Detailed acceptance plan: [`UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_PLAN_20260824.md`](UEAGENTKIT_EDITOR_RESIDENT_WRITER_W1_ACCEPTANCE_PLAN_20260824.md)

> 日期：2026-08-23
>
> 文档基线：`feature/agent-reliability@9010122`
>
> Capability closeout 基线：`2aadb66`
>
> 推荐实现分支：`feature/live-writer-expansion`
>
> 最新正式发布：`0.7.0`
>
> 本计划范围：W0 Baseline / Contract Freeze + W1 Blueprint Narrow Editor-Resident Live Apply
>
> 不包含：正式 0.8 release、Generic Blueprint Graph CRUD、R5、Performance P1-P5 的实现

## 1. 目标

当前 UEAgentKit 已经具备完整的受控写入与 Trust 闭环，但 Blueprint 窄写入仍主要依赖独立 Commandlet，且现有 Live Save / Verify 路径存在明显 cold-start 成本。

本阶段不新增 Writer family，而是把已经存在、已经通过 0.8 reliability closeout 的三个 Blueprint Operation 迁移到当前已经打开的 Unreal Editor 中执行：

```text
setVariableDefault
setComponentProperty
setPinDefault
```

最终方向：

```text
Plan / Policy / Revision
→ 当前 UnrealEditor.exe
→ exact target resolve
→ transaction + snapshot
→ mutation
→ compile + exact read-back
→ Dirty
→ Undo / Discard 或 Authorized Save
→ checkpoint Strong Independent Verify
→ Semantic Diff
→ Verification Plan
→ Trust Verdict
```

本阶段成功的核心指标不是 Tool 数增加，而是：

1. Blueprint 小修改不再为每次 Apply 启动 `UnrealEditor-Cmd.exe`；
2. 保留现有 Policy / Revision / Change Set / Recovery / Trust 边界；
3. compile failure 能恢复到 exact pre-write state；
4. 同一 Editor Session 可以连续执行多个窄修改；
5. 为后续 W2/W3 的 Fast Verify / Checkpoint Strong Verify 提供稳定基础。

## 2. 已确认的当前实现事实

### 2.1 Python Operation Registry

文件：

```text
src/ue_agent_kit/patches.py
```

当前三个 Blueprint Operation 已存在于 `OPERATION_REGISTRY`：

```text
setVariableDefault
  target = variableName
  risk = low
  asset_type = Blueprint

setComponentProperty
  target = componentName + propertyPath
  risk = low
  asset_type = Blueprint

setPinDefault
  target = graphGuid + nodeGuid + pinName
  risk = low
  asset_type = Blueprint
```

但它们当前都没有 `live_write_value_kind`。

`LIVE_WRITE_OPERATION_REGISTRY` 的生成规则是：

```python
name: spec for name, spec in OPERATION_REGISTRY.items() if spec.live_write_value_kind
```

因此三个 Blueprint Operation 当前不会进入 Live Write Registry。

### 2.2 High-level MCP Tool 已经存在

文件：

```text
src/ue_agent_kit/mcp_workflow_tools.py
```

已有：

```text
ue_set_blueprint_default
ue_set_component_property
ue_set_pin_default
```

这些 Tool 当前负责 Plan / DryRun，不需要为了 W1 再新增三个新的公共 MCP Tool。

W1 应复用：

```text
Plan Tool
→ planId
→ ue_apply_asset_property_live(planId, LIVE APPLY ...)
```

这意味着：

```text
Public Tool count ideally remains unchanged.
Live-capable Operation count increases by 3.
```

### 2.3 当前 Blueprint DryRun / Commit cold path

文件：

```text
src/ue_agent_kit/agent_workflow.py
scripts/RunPatch.ps1
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintPatchCommandlet.cpp
```

`prepare_high_level_change(mode=DryRun)` 会进入：

```text
plan_patch
→ dry_run_patch
→ RunPatch.ps1
→ UnrealEditor-Cmd.exe
```

当前 Blueprint persistent Commit 同样依赖 Commandlet patch path。

### 2.4 Live Apply 基础层已经存在

`agent_workflow.py::apply_asset_property_live()` 已经具备：

- Commit enable gate；
- Live Editor gate；
- Change Set membership / session 校验；
- exact `LIVE APPLY <planId>` confirmation；
- Plan digest / Policy / Revision validation；
- `LIVE_WRITE_OPERATION_REGISTRY` lookup；
- target field validation；
- Bridge `editor.applyAssetPropertyLive` 调用；
- before/after value receipt；
- transactionId；
- Live Apply Journal；
- Change Set binding；
- no-op terminal path；
- Authorized Save nextAction。

W1 应扩展现有基础层，不创建第二套 Blueprint-specific Workflow Service。

### 2.5 C++ Live Write Registry 已经是可扩展架构

文件：

```text
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/LiveWriteOperationRegistry.h
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/LiveWriteOperationRegistry.cpp
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/EditorBridgeWriteHandlers.cpp
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/LiveWriteTransaction.*
```

当前 Registry 注册：

```text
Property
Material
DataTable
Animation
```

`ELiveWriteAssetRequirement::StandardAssetRequirements` 当前包含：

```text
LoadedAsset
OpenInEditor
NonBlueprint
ProjectContent
NonMap
CleanPackage
```

因此 Blueprint descriptor 不能直接使用 `StandardAssetRequirements`；它应保留除 `NonBlueprint` 外的安全要求。

### 2.6 Unified Live Transaction 已存在

`LiveWriteTransaction` 已经提供：

```text
FScopedTransaction
Snapshot
ReadBefore
ApplyValue
ReadAfter
SemanticEqual
RestoreSnapshot
NotifyChanged / NotifyRestored
TransactionRecord
Undo / Discard ownership
```

W1 必须复用这套 lifecycle。

不能在 Blueprint Writer 里再造一套不受 Journal / Undo / Change Set 约束的 transaction。

### 2.7 BlueprintPatchCommandlet 已有成熟目标解析逻辑

文件：

```text
Plugin/UEAgentKit/Source/UEAgentKitEditor/Private/BlueprintPatchCommandlet.cpp
```

已有逻辑包括：

#### Variable default

```text
Blueprint->GeneratedClass
→ FindFProperty(variableName)
→ GeneratedClass->GetDefaultObject()
→ IsVariableCreatedByBlueprint()
→ exact FProperty address
```

#### Component property

```text
FindSCSNode(Blueprint, componentName)
→ ComponentTemplate
→ ResolvePropertyPath(propertyPath)
→ exact FProperty address
```

#### Pin default

```text
FindGraphPin(Blueprint, graphGuid, nodeGuid, pinName)
→ input pin only
→ unlinked only
→ editable default only
→ Schema->TrySetDefaultValue()
```

并已有：

- JSON → property value；
- JSON → pin default string；
- property/pin read-back；
- rollback text restore；
- Blueprint compile helper。

W1 不应复制这些规则形成第二套语义。

### 2.8 当前 Save / Verify 有两次 independent export 风险

`ue_save_authorized_asset(mode=Commit)` 当前执行：

```text
Editor Bridge saveAuthorizedAsset
→ disk revision
→ _export_refresh_candidate()
→ RunAssetCatalog.ps1
→ independent export revision check
```

随后建议调用：

```text
ue_verify_live_write
→ RunAssetCatalog.ps1
→ another independent reload/export
```

因此当前一次 Live Write 的保存闭环可能包含至少两次独立 Commandlet cold-start。

W0 必须先量化这个成本。

W1 不允许为了性能直接删除现有验证；Save/Verify 重构属于 W3。

## 3. 固定安全边界

W0/W1 不得改变以下规则：

### 3.1 Project / Editor

- fixed project only；
- localhost Editor Bridge；
- exact Editor Session identity；
- PIE/SIE mutation 继续拒绝；
- map / external actor package 继续拒绝；
- target 必须 already loaded + open；
- initial package 必须 Clean；
- 不允许自动 Save All。

### 3.2 Plan / Revision

- Plan 必须来自现有 `OPERATION_REGISTRY`；
- exact target fields 不允许 fallback by display name guessing；
- Apply 前必须重新验证 Plan file / digest / Policy；
- expected disk Revision 不匹配时 fail closed；
- stale Change Set / wrong Editor Session fail closed。

### 3.3 Transaction / Recovery

- mutation 必须有 snapshot；
- successful changed mutation 必须有 TransactionRecord；
- semantic no-op 不制造伪 transaction / save / verify evidence；
- apply failure 必须恢复 pre-write state；
- compile failure 必须尝试恢复 pre-write state；
- recovery 无法证明时返回 `recovery-failed` 类稳定错误，不得报告 success。

### 3.4 Trust

- resident read-back != independent verify；
- compile success != saved success；
- save success != full task success；
- Fast Verify 后仍不能关闭要求 independent canonical/reload 的 Required Assertion；
- verified Semantic Diff / Verification Plan / Trust Verdict 契约不因性能优化放宽。

## 4. W0 — Baseline / Contract Freeze

W0 原则上只做审计、测量、契约与最小 instrumentation。

如果没有必要，不改产品行为。

### W0.0 — 创建独立实现分支

推荐：

```text
git branch feature/live-writer-expansion 9010122
git worktree add E:/WorkSpace/UEAgentKit-LiveWriter feature/live-writer-expansion
```

规则：

- 不在旧 `feature/live-editor-realtime-io@56afc91` 上继续；
- 不 Rebase；
- 不 Push；
- 不修改 `Output/ContextBootstrapDraft/`；
- 不修改 R4.1 raw measurement；
- 保留已有 `CONOUT$`，不得提交。

### W0.1 — 当前路径 Trace Matrix

输出一个机械事实表，至少覆盖：

| Stage | Non-BP Live | BP Variable | BP Component | BP Pin | UE Process | Independent |
|---|---|---|---|---|---|---|
| Plan | | | | | | |
| DryRun | | | | | | |
| Live Apply / Commit | | | | | | |
| Compile | | | | | | |
| Save | | | | | | |
| Verify | | | | | | |
| Semantic Diff | | | | | | |
| Trust | | | | | | |

每个 Stage 标记：

```text
resident-editor
python-only
commandlet-cold-start
independent-reload
not-applicable
```

必须确认而不是假设：

- Blueprint DryRun 是否每次启动 Commandlet；
- Blueprint Commit 是否每次启动 Commandlet；
- Authorized Save 自带的 independent export；
- Verify 的第二次 independent export；
- Semantic Diff 是否再次触发 export，还是消费已有 evidence；
- Compile evidence 是 resident 还是 commandlet。

### W0.2 — Latency Instrumentation

目标不是做复杂 profiler，而是得到 stage wall-clock。

至少记录：

```text
plan_ms
dry_run_ms
live_apply_or_commit_ms
compile_ms
save_preview_ms
save_commit_ms
save_embedded_verify_ms
strong_verify_ms
semantic_diff_ms
verification_plan_ms
trust_ms
child_ue_process_count
child_ue_process_total_ms
```

建议在现有 Workflow subprocess wrapper / test harness 记录，而不是把性能字段侵入所有公共 MCP schema。

如果必须新增日志字段：

- 仅 diagnostic / benchmark path；
- 不改变已有 Tool success contract；
- 不把 wall-clock 作为 Trust evidence。

### W0.3 — Baseline Cases

#### Case B0 — Existing non-Blueprint Live scalar

目的：获得当前最佳 Live path 基线。

```text
Data Asset scalar
Plan
→ Live Apply
→ Save Preview
→ Save Commit
→ Verify Live Write
→ Semantic Diff
→ Verification Plan
→ Trust
→ exact recovery
```

#### Case B1 — Blueprint variable default

```text
setVariableDefault
```

#### Case B2 — Blueprint component property

```text
setComponentProperty
```

#### Case B3 — Blueprint pin default

```text
setPinDefault
```

性能重复建议：

- B0：3 次；
- B1：3 次；
- B2/B3：至少 1 次完整功能闭环；
- 不需要启动 LLM benchmark；
- DirectHost fixture 每次必须 exact recovery。

记录：

```text
cold-start count
total elapsed
UE process elapsed share
resident editor elapsed share
```

### W0.4 — Fast Verify / Strong Verify Contract Freeze

W0 必须产出书面契约。

#### Fast Resident Verify

只允许证明：

- exact Editor Session；
- exact target asset loaded；
- exact target identity 仍存在；
- current in-memory value == requested value；
- package dirty/clean state；
- Blueprint compile result；
- 可选 Data Validation；
- current Change Set / Transaction applicability。

不能证明：

- fresh process reload；
- saved disk canonical correctness；
- independent package Revision；
- runtime behavior；
- whole-task Trust。

#### Strong Independent Verify

必须继续证明：

- disk Package 已保存；
- independent Unreal load/export；
- exact asset identity；
- actual disk Revision；
- canonical expected semantics；
- Change Set verified stage applicability。

### W0.5 — Save / Verify Double Cold-start Decision Record

W0 只记录并提出 W3 方案，不在本阶段改变行为。

至少比较三个候选：

#### Option A — 保持现状

```text
Save Commit
→ embedded independent export
→ Verify
→ second independent export
```

优点：兼容最强。

缺点：cold-start 重复。

#### Option B — Save 只做 persistence + disk revision，Verify 做唯一 Strong Verify

优点：职责清楚、只一次 strong verify。

风险：改变现有 `ue_save_authorized_asset` 的 `verified=true` 语义，需要兼容迁移。

#### Option C — Save 增加 explicit verification mode，默认保持兼容

例如概念上：

```text
verification = immediate | checkpoint
```

优点：可以渐进迁移。

风险：公共 API 复杂度增加。

W0 必须给出推荐，但 W1 不实施。

### W0.6 — W0 Exit Gate

W0 完成必须满足：

```text
[ ] Current path trace matrix complete
[ ] B0-B3 baseline recorded
[ ] Cold-start count mechanically measured
[ ] Save/Verify duplicate export cost measured
[ ] Fast Verify contract written
[ ] Strong Verify contract written
[ ] W3 migration recommendation written
[ ] DirectHost fixtures exact recovery
[ ] No R4.1 raw measurement changes
[ ] No product Writer behavior changes unless instrumentation required
[ ] git diff --check pass
```

建议 W0 独立 commit：

```text
perf/docs: baseline editor-resident writer path
```

## 5. W1 — Blueprint Narrow Editor-Resident Live Apply

W1 只做三个已有 Blueprint Operation。

不进入：

```text
setBlueprintDescription live migration
Generic node create/delete
Graph wiring
Graph layout
Function/event creation
Variable lifecycle create/delete/rename
Component lifecycle create/delete
Level Actor mutation
```

### W1.0 — Python Operation metadata

文件：

```text
src/ue_agent_kit/patches.py
```

为：

```text
setVariableDefault
setComponentProperty
setPinDefault
```

增加明确的 Live metadata，使其进入 `LIVE_WRITE_OPERATION_REGISTRY`。

建议使用 domain-specific value kind，而不是模糊的 `scalar`：

```text
blueprint-variable-default
blueprint-component-property
blueprint-pin-default
```

最终命名可以按现有规范调整，但必须：

- 三个 Operation 可机械区分；
- beforeValue / afterValue 保留原始 JSON 类型或 pin canonical representation；
- Semantic Diff Adapter 能明确识别；
- 不影响已有 non-BP live value kind。

`setBlueprintDescription` 本阶段不要顺带加 Live metadata。

### W1.1 — C++ Blueprint Live Write domain

推荐新增独立 domain 文件：

```text
LiveWriteBlueprintOperations.cpp
```

并在：

```text
LiveWriteOperationRegistry.cpp
```

注册：

```text
RegisterBlueprintLiveWriteOperations(*this)
```

Descriptor 的 AssetRequirements 必须：

```text
LoadedAsset
| OpenInEditor
| ProjectContent
| NonMap
| CleanPackage
```

明确不包含：

```text
NonBlueprint
```

不要全局放宽 `StandardAssetRequirements`。

只有 Blueprint domain descriptor 获得 Blueprint 资格。

### W1.2 — 提取共享 Blueprint target helper

不要让 `BlueprintPatchCommandlet.cpp` 和 Live Writer 各自维护一套：

```text
FindSCSNode
FindGraphPin
ResolvePropertyPath
JsonValueToPinDefault
Blueprint variable ownership check
```

推荐提取最小共享 helper，例如：

```text
BlueprintWriteCommon.h/.cpp
```

职责仅包括：

- deterministic target resolution；
- JSON/value conversion；
- exact read-back；
- narrow notification/mark helper。

不要把 Commandlet process lifecycle、report file、backup、Save 或 Workflow policy 搬进 shared helper。

目标是：

```text
Commandlet Writer semantics
          ↓
   shared narrow helpers
          ↑
Resident Live Writer semantics
```

而不是复制。

### W1.3 — `setVariableDefault` Live IO

Target：

```text
Blueprint GeneratedClass
→ exact Blueprint-created FProperty
→ CDO value address
```

必须保留：

- only variable declared by target Blueprint；
- inherited/native property 拒绝；
- exact type conversion；
- before value capture；
- after value exact read-back；
- semantic no-op；
- snapshot restore；
- Blueprint dirty/modified notification；
- compile after mutation。

注意：

`CDO->Modify()` 与 Blueprint asset/package 的 Dirty/Undo 语义必须通过真实 UE5.6 smoke 证明，不能只凭代码推断。

### W1.4 — `setComponentProperty` Live IO

Target：

```text
Blueprint
→ SimpleConstructionScript
→ exact SCS node by componentName
→ ComponentTemplate
→ exact propertyPath
```

必须保留现有 property path 约束。

禁止趁机开放 arbitrary nested UObject traversal。

验证：

- before exact；
- apply exact；
- after exact；
- ComponentTemplate mutation 正确触发 Blueprint modified；
- compile 后值不丢失；
- Undo/Discard 恢复；
- Save/Reload 后 canonical 一致。

### W1.5 — `setPinDefault` Live IO

Target identity 必须继续使用：

```text
graphGuid
nodeGuid
pinName
```

必须拒绝：

- graph/node 不匹配；
- output pin；
- connected pin；
- read-only default；
- ignored default；
- schema 不接受的值。

Apply 必须继续走：

```text
UEdGraphSchema::TrySetDefaultValue
```

不能直接无条件写 `Pin->DefaultValue`。

Pin snapshot 不能复用普通 `FProperty` snapshot；应实现专用 `ILiveWriteValueIO`，至少保存：

```text
exact old default representation
exact target identity
owner Graph/Node/Blueprint applicability
```

### W1.6 — Compile 成为 Live Apply 成功条件

Blueprint Live Apply 不允许：

```text
mutation success
→ receipt success
→ compile later failed
```

W1 的 changed success 必须至少完成：

```text
snapshot
→ transaction mutation
→ exact read-back
→ Blueprint compile
→ compile success
→ retain transaction record
→ return live receipt
```

如果 compile fail：

```text
compile fail
→ restore snapshot
→ mark restored
→ recompile baseline if required to prove recovery
→ verify restored value
→ no successful liveApplyReceipt
```

若 baseline recovery / recompile 无法证明：

```text
return recovery-failed
safeToApply=false / equivalent failure state
```

不得把“已经恢复了大概的值”当 success。

### W1.7 — Live result contract

Blueprint Live Apply response 应继续沿用现有统一字段：

```text
operation
valueKind
changed
beforeValue
afterValue
transactionId
transactionRecorded
editorSessionId
packageDirty
liveApplyReceipt
changeSetId
```

并增加或复用明确 compile evidence，例如：

```text
compileAttempted=true
compileSucceeded=true
compileErrors=[] / bounded summary
```

不要把整份 Output Log 塞进结果。

Compile evidence 必须和：

```text
assetPath
editorSessionId
changeSetId / liveApplyReceipt
```

可机械关联。

### W1.8 — Undo / Discard

现有：

```text
ue_undo_asset_property_live
ue_discard_asset_property_live
```

应继续处理 Blueprint live receipt，不新增：

```text
ue_undo_blueprint_live
```

验收必须证明：

- exact transaction top / session checks 仍生效；
- variable/component/pin 都可恢复 before state；
- recovery 后 Blueprint compile 合法；
- package dirty 状态符合 pre-write 状态；
- Change Set terminal state 正确；
- journal 关闭正确。

### W1.9 — Authorized Save

W1 继续复用：

```text
ue_save_authorized_asset
```

不创建 Blueprint-specific Save Tool。

必须验证：

- loaded Blueprint Dirty package 可 Preview；
- exact Save receipt；
- exact confirmation；
- backup manifest；
- resident Editor save；
- disk Revision change；
- Change Set saved state；
- existing current independent export behavior 保持兼容。

W1 不优化 Save/Verify cold-start；那属于 W3。

### W1.10 — Semantic Diff / Trust 兼容

现有 R2 Adapter 已支持：

```text
setVariableDefault
setComponentProperty
setPinDefault
```

因此原则上不新增新的 Semantic Diff domain。

但必须验证 Live/Persisted/Verified 三阶段 evidence 在新路径仍能正确选择。

特别检查：

- typed pin default normalization 仍只接受机械允许变化；
- compile materialized pin defaults 不制造 unrelated semantic success；
- Change Set identity 不因 resident path 丢失；
- Verification Plan compile assertion 仍 required；
- Trust 不因 resident compile 自动跳过 independent persistence assertion。

## 6. W1 测试矩阵

### 6.1 Python unit / contract

至少覆盖：

```text
[ ] Blueprint 3 ops enter LIVE_WRITE_OPERATION_REGISTRY
[ ] setBlueprintDescription remains non-live
[ ] target field validation unchanged
[ ] bridge payload exact target fields
[ ] live receipt stores correct valueKind
[ ] Change Set binding
[ ] no-op path
[ ] wrong plan / consumed plan
[ ] wrong Editor Session
[ ] stale Revision
[ ] dirty baseline reject
[ ] save/verify nextActions
[ ] semantic diff live/persisted/verified
[ ] verification/trust assertions
```

优先复用：

```text
tests/python/test_agent_workflow.py
tests/python/test_blueprint_patch_executor.py
tests/python/test_live_write_smoke_contract.py
tests/python/test_semantic_diff.py
tests/python/test_verification_trust.py
```

### 6.2 C++ / UE5.6 Direct Build

W1 修改 C++，因此 Direct Build 是硬门禁。

要求：

```text
UE 5.6 plugin build = pass
```

不能用 `compileall` 代替。

### 6.3 Real UE5.6 DirectHost smoke

每个 Operation 至少覆盖：

#### Success

```text
Plan
→ Live Apply
→ compile success
→ exact read-back
→ Dirty
→ Save
→ Independent Verify
→ Semantic Diff verified
→ Trust
→ fixture recovery
```

#### No-op

```text
same value
→ no-op
→ no transaction
→ no save required
```

#### Undo

```text
Live Apply
→ Undo
→ exact before state
→ compile valid
```

#### Discard

```text
Live Apply
→ Discard
→ exact before state
→ journal closed
```

#### Compile / validation failure

至少制造一个安全、确定性的失败 fixture，证明：

```text
mutation attempted
→ compile/schema failure
→ snapshot restored
→ no successful receipt
→ package/revision exact recovery
```

### 6.4 Regression gates

以 W1 完成时仓库实际 discovered tests 为准，不把 739 固定成未来永远不变的数量。

至少：

```text
Ruff
full Python suite
compileall
JSON schemas/examples
PowerShell parser
UE5.6 Direct Build
real affected-domain UE smoke
git diff --check
UTF-8 no BOM / CRLF for changed text
tracked Output/Backups = 0
```

R4.1 24-attempt LLM repeat 不属于 W1 硬门禁，因为：

- measurement 已冻结；
- W1 主要改变执行路径，不改变 R4.1 result contract；
- W5 再评估 real-agent latency / tool-call change。

如果 W1 修改了 benchmark-visible Tool contract，则必须重新评估是否需要 supplemental benchmark。

## 7. 性能验收指标

W1 不设没有基线支撑的绝对硬数字。

先相对 W0 评估。

必须报告：

```text
BP apply child Unreal process count
BP apply elapsed before / after
BP compile elapsed
save elapsed
verify elapsed
total closed-loop elapsed
cold-start time share
```

最低产品目标：

```text
Blueprint Live Apply itself:
  child UnrealEditor-Cmd starts = 0
```

也就是：

```text
Plan
→ LIVE APPLY
→ compile/read-back
```

必须全部在已经打开的 Editor Session 内完成。

Save / Strong Verify 暂时允许 Commandlet，因为 W3 才负责 checkpoint 优化。

## 8. 建议 Commit 切分

不要再产生一个同时包含架构、三个 Operation、性能、文档的大 commit。

建议：

### C1 — W0 baseline

```text
perf/docs: baseline editor-resident writer path
```

### C2 — shared Blueprint live infrastructure

```text
refactor: share blueprint write target helpers
```

只做 helper / registry plumbing，不改变三个 Operation 的最终公开能力。

### C3 — variable default live

```text
feat: add resident blueprint variable default write
```

### C4 — component property live

```text
feat: add resident blueprint component write
```

### C5 — pin default live

```text
feat: add resident blueprint pin default write
```

### C6 — affected-domain acceptance

```text
test/docs: close resident blueprint write acceptance
```

如果共享 helper 抽取风险过高，可以调整 commit，但每个 commit 必须保持可测试、可回滚。

## 9. 明确禁止事项

本阶段禁止：

- Generic Blueprint Graph CRUD；
- 自动创建/删除/重连 Node；
- 自动创建/删除 Blueprint variable/component；
- Material Graph / Niagara / Sequencer / Control Rig；
- Level Actor generic mutation；
- arbitrary Python / Console / Shell；
- 自动 Save All；
- 放宽 fixed project；
- 放宽 Revision gate；
- resident read-back 冒充 independent verify；
- 将 compile success 直接映射成 Trust verified；
- 修改 R4.1 raw attempts；
- 解冻 R5；
- Push；
- Rebase；
- 提交 `Output/`、`Backups/`、`Build/`、`Saved/`、raw benchmark；
- 删除或提交已有 `CONOUT$`。

## 10. W1 Exit Gate

只有全部满足才进入 W2：

```text
[ ] setVariableDefault resident Live Apply pass
[ ] setComponentProperty resident Live Apply pass
[ ] setPinDefault resident Live Apply pass
[ ] all 3 operations use current UnrealEditor.exe
[ ] apply stage starts 0 child UnrealEditor-Cmd process
[ ] all 3 exact target identities preserved
[ ] compile is part of changed-success path
[ ] compile failure exact recovery proven
[ ] no-op creates no fake transaction/save/verify
[ ] Undo exact recovery proven
[ ] Discard exact recovery proven
[ ] Authorized Save still gated and compatible
[ ] Independent Verify still independent
[ ] R2 Semantic Diff live/persisted/verified pass
[ ] R3 Verification Plan / Trust pass
[ ] Change Set identity preserved end-to-end
[ ] UE5.6 Direct Build pass
[ ] full Python regression pass
[ ] real UE5.6 affected-domain smoke pass
[ ] fixture exact recovery pass
[ ] no scope expansion
```

W1 完成状态应写成：

```text
Blueprint narrow Editor-resident Live Apply = complete
Fast Resident Verify = not yet W2 complete
Checkpoint Strong Verify optimization = not yet W3 complete
Generic Blueprint Graph CRUD = explicitly deferred
```

## 11. W1 完成后的 W2/W3 入口

### W2

建立正式 Fast Resident Verify：

```text
Live Apply
→ exact resident read-back
→ compile / validation
→ current session applicability
→ fast result
```

只用于迭代反馈。

### W3

基于 W0 的 Save/Verify 测量结果，减少重复 independent cold-start：

```text
multiple resident edits
→ checkpoint save
→ one Strong Independent Verify
→ Semantic Diff
→ Verification Plan
→ Trust
```

W3 才允许讨论：

- Save embedded verify 是否拆分；
- verification mode；
- multi-operation checkpoint；
- one strong verify per checkpoint。

## 12. 给执行 Agent 的直接任务定义

```text
Repository: E:\WorkSpace\UEAgentKit
Baseline: feature/agent-reliability@9010122
Create a fresh branch/worktree for feature/live-writer-expansion.
Do not use the stale feature/live-editor-realtime-io branch.

Execute W0 first. Do not implement W1 before W0 produces:
- exact current write-path trace matrix;
- stage latency baseline;
- child Unreal process count;
- measured Save/Verify duplicate cold-start cost;
- frozen Fast Resident Verify vs Strong Independent Verify contract.

Then implement W1 only for:
- setVariableDefault;
- setComponentProperty;
- setPinDefault.

Reuse the existing Operation Registry, Change Set, Live Write Transaction,
Authorized Save, Semantic Diff, Verification Plan, Trust Verdict and Recovery
contracts. Extract/reuse BlueprintPatchCommandlet target-resolution semantics
rather than duplicating them.

A successful changed Blueprint Live Apply must include exact read-back and
Blueprint compile success. Compile failure must restore the exact pre-write
state and must not produce a successful live receipt.

Do not implement Generic Blueprint Graph CRUD, setBlueprintDescription live
migration, R5, release/version changes, or performance-track changes.
Do not modify R4.1 raw artifacts. Do not Push/Rebase.
Preserve pre-existing CONOUT$.

Use separate checkpoint commits for W0, shared infrastructure, each Blueprint
operation, and final affected-domain acceptance.
```

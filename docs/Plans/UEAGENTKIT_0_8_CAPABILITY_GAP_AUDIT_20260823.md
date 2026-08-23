# UEAgentKit 0.8 Read / Write Capability Gap Audit

> 日期：2026-08-23
>
> 分支：`feature/agent-reliability`
>
> 证据基线：R4 v1、R4.1 24-attempt repeat、Reforge readonly smoke、DirectHost UE5.6 closed-loop smokes、公共 Tool/Operation Registry

## 1. 最终结论

```text
Must-fix new Read Tools before 0.8     0
Must-fix new Write Tools before 0.8    0
Must-fix new Tools total               0
Registered public Tools              105
Public Tools without Memory           93
Registered Patch Operations           18
```

当前 UEAgentKit 已覆盖 0.8 可靠性链需要的项目/资产身份、Canonical/Index、引用与影响、Task Context、Editor 状态、窄写入、授权保存、独立验证、Semantic Diff、Verification Plan、Trust Verdict 与 exact recovery。R4.1 的剩余失败没有出现“UE 知道但 Agent 无法读取”的事实缺口，也没有出现“缺少一个窄 Writer 导致必须人工回 Editor”的阻塞。

本结论不表示 UE Editor API 已被全面覆盖。Generic Blueprint Graph CRUD、Level Actor CRUD、Material Graph、Niagara、Sequencer、Control Rig、任意 UObject/Python/Console/Shell、通用资产生命周期和协作自动化继续明确延期；它们没有 R4/R4.1 + Reforge 高频阻塞证据，也不能在 0.8 时间边界内满足同等安全门禁。

## 2. 判定方法

Read gap 必须满足：

1. UE 或当前固定 Project 的确定性数据源已经知道该事实；
2. Agent 目前无法通过现有 Index、Canonical、Memory、Live Editor、Workflow 或 Evidence Tool 可靠获得；
3. 缺失会在 R4/R4.1 或 Reforge 高频任务中迫使 Agent 猜测；
4. 新能力可保持固定 Project、bounded、freshness-aware、无模型推断。

Write gap 必须满足：

1. 是高频、明确、窄操作；
2. 当前没有安全 workaround，必须人工回 Editor；
3. 能具备 Policy、Revision、Plan/Dry Run、Snapshot、No-op、Transaction、Authorized Save、Independent Verify、Semantic Diff、Trust 和 recovery；
4. 有重复 benchmark 或真实项目阻塞证据。

状态：

- `complete`：当前契约足以完成已证明的真实任务；
- `usable-but-awkward`：能力存在，但 Agent 路径、成本或边界容易误用；
- `evidence-insufficient`：当前确定性证据不能证明该问题，应返回 unknown；
- `missing-high-value`：真实高频阻塞且没有安全 workaround；
- `demand-driven` / `defer` / `explicitly-deferred`：没有当前 Must-fix 证据。

## 3. Registry 与模式计数

计数来自 `src/ue_agent_kit/tool_registry.py` 与 Registry contract tests：

| Mode | Tool count |
|---|---:|
| Offline | 10 |
| Offline + Memory | 22 |
| Live | 43 |
| Live + Memory | 55 |
| Workflow-only | 60 |
| Workflow + Memory | 72 |
| Live + Workflow | 93 |
| Combined + Memory | 105 |

按 Registry group：

| Group | Count |
|---|---:|
| Query | 10 |
| Memory | 12 |
| Live Read | 17 |
| Realtime | 8 |
| Live Action | 8 |
| Workflow | 50 |

按 MCP annotation：43 read、47 planning、15 destructive。`destructive` 只表示可能改变 Editor/磁盘状态，不绕过固定 Project、Policy、Revision 或授权确认。

## 4. Read Capability Matrix

| Read domain | Status | Existing capability | Evidence / boundary | Must-fix |
|---|---|---|---|---|
| Project / Asset identity | complete | `ue_get_capabilities`、`ue_get_project_status`、`ue_search`、`ue_get_asset` | 固定 Project Key、精确 Object Path、bounded pagination | No |
| Asset metadata / Asset Registry | complete | Index + Canonical summary + `ue_get_asset` | 资产类、Package、Registry Tags、Revision、统计均有确定性来源 | No |
| Canonical export | complete | Revision Export、independent Canonical、`ue_verify_asset` | 明确 stage/revision；不把内存状态伪装成磁盘 Revision | No |
| Blueprint semantic graph / node / pin | usable-but-awkward | Canonical graph/node/pin、symbol search、`ue_get_blueprint_graph_selection` | 静态语义足够支持窄 default/component/pin 修改；不证明 runtime execution | No |
| Symbol search | complete | `ue_search(scope=symbols)` | bounded、确定性、支持 Blueprint symbol identity | No |
| Reference search | complete | `ue_find_references` | 明确方向、类型、分页与精确资产身份 | No |
| Impact analysis | usable-but-awkward | `ue_analyze_change_impact` | depth 1–3、bounded graph、shortest path、unknown 不猜；R4.1 暴露 direct-only bound 易被 Agent 越过 | No |
| Task context / relevant assets | usable-but-awkward | `ue_get_task_context` | Full 有 deterministic relevant assets、risk、next expansions；Legacy 隐藏高层能力后仍易锁错资产 | No |
| Revision / freshness | complete | Task Context、`ue_get_asset_state`、Revision Export、SQLite/disk comparison | stale 六次均正确 blocked；四源状态分离 | No |
| Dirty / open / selection | complete | `ue_get_dirty_assets`、`ue_get_open_assets`、`ue_get_selection`、`ue_inspect_asset_live` | Live state 明确 session 与 loaded/dirty；不伪造 Revision | No |
| Editor / PIE / world state | complete | `ue_editor_status`、`ue_get_pie_state`、`ue_get_current_level`、`ue_get_editor_context` | 当前已加载 World、有界上下文；PIE/SIE safety block | No |
| Compile diagnostics | complete | `ue_get_compile_errors`、`ue_compile_blueprint` evidence capture | session-local exact action evidence | No |
| Data Validation | complete | `ue_validate_asset`、`ue_validate_folder` | 固定 UE Data Validation，记录 exact asset/revision | No |
| Automation result | complete | `ue_run_automation_test` | 固定测试名、有界、timeout 保留 failed/unknown | No |
| Memory / Active Work / Evidence | usable-but-awkward | 12 Memory Tools、Task Context correlation、Change Set evidence | Project Memory 可选；Live action evidence 有意保持 session-local，restart 后需重跑 | No |
| Semantic Diff | complete | `ue_analyze_semantic_diff` | Change-Set-bound、stage/revision-aware、expected/missing/unexpected 分离 | No |
| Verification / Trust | complete | `ue_build_verification_plan`、`ue_evaluate_trust_verdict` | Required assertion 未关闭不得 verified；Tool 不自动执行动作 | No |
| Animation / Retarget diagnostics | demand-driven | 7 live animation readers + audit/retarget workflow | 已有窄诊断与验证样本；R4/R4.1 未出现阻塞 | No |

Read Audit 中 `missing-high-value=0`。

## 5. Read Gap Ledger

### R-01 Direct-only impact bound 容易被 Agent 越过

- 真实任务：只评估 `BP_VehicleBase` 的 direct impact、高 fanout 与 bounded validation scope。
- 当前 workaround：显式调用 `ue_analyze_change_impact(max_depth=1)`，最终 claim 只复制同一 response 的 direct count 与 visited edge count。
- 是否迫使猜测：否。Tool 已返回精确事实；问题是 Agent 三次选择/报告 depth 2。
- 证据：R4 v1 Full 1/1 Trusted；R4.1 Full 3/3 正确识别 23 direct consumers 和 bounded scope，但报告 1,549/1,549/1,811 edges，而 Case exact direct-only truth 为 282。
- 最小候选：后续 Agent SDK/task template 可绑定 requested bound；不是新 UE Read Tool。
- 安全风险：若 Server 根据自然语言自动改 depth，会把模型意图推断混入确定性 Tool。
- 0.8 Must-fix：No，`usable-but-awkward`，列入 known limitation。

### R-02 Runtime sensitivity / execution chain

- 真实任务：判断静态 consumer 是否一定在运行时执行并造成 gameplay breakage。
- 当前 workaround：返回 `not-proven-with-current-evidence`，使用静态 references/impact 定义验证 scope，再由真实运行测试验证。
- 是否迫使猜测：只有 Agent 违反 unknown contract 时才会猜；当前 Tool 明确禁止推断。
- 证据：R4/R4.1 high-fanout 均正确声明 runtime sensitivity 未证明；taxonomy 中 `execution-trace-gap=0`。
- 最小候选：只有后续多个真实 Case 反复阻塞时才进入 R5 execution trace。
- 安全风险：基于资产类型或命名启发式输出 runtime-sensitive 会制造 False Success。
- 0.8 Must-fix：No，`evidence-insufficient / defer`。

### R-03 Legacy target discovery

- 真实任务：从自然语言定位 Blueprint/Data Asset 精确目标。
- 当前 workaround：Full 使用 `ue_get_task_context` + `ue_search`；Legacy 可用 bounded search/get asset，但需要更多选择步骤。
- 是否迫使猜测：Legacy profile 中会；Full 产品面已有高层路径。
- 证据：R4.1 Legacy Wrong Asset 6/12，Full 0/12。
- 最小候选：Agent guidance/tool selection；不复制现有 Task Context。
- 安全风险：再增加一个重叠 discovery Tool 会扩大选择面而非减少猜测。
- 0.8 Must-fix：No，产品 Full profile 已覆盖。

### R-04 Restart 后 session-local action evidence

- 真实任务：保存后跨 MCP restart 继续消费 compile/validation/automation evidence。
- 当前 workaround：复用 persisted/independent Canonical；允许重复的 exact action在新 session 重跑；不能重建时诚实 UNKNOWN。
- 是否迫使猜测：否，Trust nextActions 明确返回缺失证据。
- 证据：C1 frozen snapshot/restart smoke 与 R3 contract tests；R4.1 Blueprint 三次完成新 session 内 evidence ladder。
- 最小候选：仅在高频真实阻塞时增加固定来源、窄 evidence persistence。
- 安全风险：arbitrary Evidence JSON ingest 会绕过 applicability/session/revision。
- 0.8 Must-fix：No，`usable-but-awkward`。

## 6. Write Capability Matrix

| Write domain | Status | Existing capability | Evidence / boundary | Must-fix |
|---|---|---|---|---|
| Data Asset scalar | complete | `setAssetProperty` + high-level/live/save/verify | R4.1 world state与 evidence 3/3 正确；两次失败是 final claim 类型，不是 Writer | No |
| Data Asset reference | complete | `setAssetReferenceProperty` | class/root Policy、derived hard-package edge normalization、independent verify | No |
| Data Asset struct/container | complete | `setAssetStructuredProperty` | Struct/Array/Set/Map fixed schema diff；No-op/transaction/recovery | No |
| DataTable cell | complete | `setDataTableCell` | field allowlist、row struct identity、save/verify/Semantic Diff | No |
| DataTable row lifecycle | complete | RowFields/Add/Remove/Rename | 三个 high-risk lifecycle Operation 仍受 Plan/Policy/Revision/confirm 约束 | No |
| Material Instance parameters | complete | Scalar/Vector/Texture/Static Switch | parameter type/association fixed；restart 后 evidence 按 C1 规则重建 | No |
| Blueprint variable/component/pin default | complete | `setVariableDefault`、`setComponentProperty`、`setPinDefault` | R4.1 Blueprint 3/3 Trusted；compile + pin-type normalization + exact recovery | No |
| Blueprint description | usable-but-awkward | `setBlueprintDescription` via generic Plan/Dry Run | 有 Operation，无单独 convenience Tool；没有 benchmark 高频阻塞 | No |
| Existing animation narrow writers | demand-driven | Scale/Additive/Retarget 专用 Tool | 已有窄 plan/live/batch/save/verify/rollback；不扩 generic animation mutation | No |
| Live Apply | complete | `ue_apply_asset_property_live` | Change Set/session/transaction/value exact binding，不保存 Package | No |
| Undo / Discard | complete | `ue_undo_asset_property_live`、`ue_discard_asset_property_live` | 精确 transaction 顶部、目标值、session 校验；不遍历 Undo 历史 | No |
| Authorized Save | complete | `ue_save_authorized_asset` | 单资产、一次性 receipt、Policy/Revision、无 Save All | No |
| Independent Verify | complete | `ue_verify_live_write`、`ue_verify_asset` | 独立 reload/canonical/revision，不信任内存 receipt | No |
| Rollback | complete | `ue_rollback_patch` + animation rollback | saved Blueprint 只在 unloaded/clean/not-open 精确证明后恢复；stale/wrong manifest fail closed | No |
| Batch | usable-but-awkward | frame-stepped world scan、animation/retarget batch | 当前只支持已证明的窄 domain；不做 arbitrary generic batch | No |
| Change Set | complete | `ue_create_change_set`、`ue_get_change_set` | write/save/verify/R2/R3 全链身份绑定 | No |
| Compile | complete | `ue_compile_blueprint` | 固定 Blueprint、session-local evidence、Dirty 明示 | No |
| Validation | complete | `ue_validate_asset`、`ue_validate_folder`、retarget validation | exact action evidence，不由 Trust Tool 自动执行 | No |
| Recovery | complete | transaction failure restore、journal rollback、exact fixture recovery | R4.1 24/24 exact recovery，18/18 DirectHost canonical/revision restored | No |

Write Audit 中 `missing-narrow-high-value=0`。

## 7. Write Gap / Closed Incident Ledger

### W-01 Scalar final claim value typing

- 真实任务：把 `DA_Scalar.IntValue` 从 number `-17` 改为 number `7`。
- 当前 workaround：final claim 必须保留 Tool/Canonical 的 JSON scalar type，不把 number stringify。
- 是否迫使人工回 Editor：否。三次真实写入、保存、verify、Semantic Diff、Trust 均完成。
- 证据：R4.1 Full 前两次 `beforeValue=-17` 被 exact grader 拒绝，第三次 number `-17` Trusted。
- 最小候选：未来 operation-discriminated result schema 或 typed Agent SDK；不是新 Writer。
- 安全风险：把通用 `beforeValue` 全局限定为 number 会破坏 string/bool/object/array Operation。
- 0.8 Must-fix：No，Agent/result-contract known limitation。

### W-02 Blueprint saved-revision rollback（已关闭）

- 真实任务：已保存 Blueprint revision 在 Editor 环境中恢复 exact baseline。
- 修复：只有 Bridge 精确证明 `loaded=false`、`packageDirty=false`、`openInAssetEditor=false`、`state=not-loaded` 时允许恢复；否则 fail closed。
- 证据：真实 UE5.6 rollback success、stale/mismatch refusal、exact cleanup；R4.1 18/18 DirectHost exact recovery。
- 新 Tool：不需要，现有 `ue_rollback_patch` 契约足够。
- 状态：`complete`。

### W-03 Reference derived-edge normalization（已关闭）

- 真实任务：`ObjectValue: null → T_Target` 会机械新增一条到 `T_Target` 的 hard-package edge。
- 修复：grader 只归一化由 Case 明确 reference mutation 派生的单条 edge；unrelated edge 仍 unexpected。
- 证据：Reference guidance validation、semantic diff tests、full regression。
- 新 Writer：不需要，原 `setAssetReferenceProperty` 已完成真实写入。
- 状态：`complete`。

### W-04 Blueprint description convenience wrapper

- 真实任务：修改 Blueprint Description。
- 当前 workaround：`ue_plan_patch` / `ue_dry_run_patch` / `ue_apply_patch` 使用已注册 `setBlueprintDescription`。
- 是否迫使人工回 Editor：否。
- 证据：Operation Registry 有完整 Plan/Dry Run/Commit；R4/R4.1 未出现重复阻塞。
- 最小候选：只有真实需求频繁出现时才增加高层 wrapper。
- 安全风险：每个低频 Operation 都增加 wrapper 会扩大 Tool selection surface。
- 0.8 Must-fix：No，`demand-driven`。

### W-05 Generic mutation families

范围：Blueprint Graph CRUD、Level Actor CRUD、Material Graph、Niagara、Sequencer、Control Rig、generic asset lifecycle、arbitrary UObject/Python/Console/Shell。

- 当前 workaround：人工 Editor 或现有窄 Operation；不通过 UEAgentKit 伪装 generic automation。
- 是否迫使人工回 Editor：某些未来任务会，但当前没有 R4/R4.1 + Reforge 高频证据。
- 最小候选：必须由具体 domain、固定 identity、可验证 diff 与 recovery 单独立项。
- 安全风险：目标/副作用/Undo/编译/运行时语义不可有界，容易破坏 Package 或绕过 Policy。
- 0.8 Must-fix：No，`explicitly-deferred`。

## 8. Registered Patch Operation Audit

18 个 `OPERATION_REGISTRY` 项全部已分类：

| Operation | Risk | Status | Public path / boundary |
|---|---|---|---|
| `setVariableDefault` | low | complete | `ue_set_blueprint_default`；compile/save/verify/R2/R3 |
| `setComponentProperty` | low | complete | `ue_set_component_property`；精确 component/property |
| `setPinDefault` | low | complete | `ue_set_pin_default`；Graph/Node GUID + pin name |
| `setBlueprintDescription` | low | usable-but-awkward | generic patch path；无高频 wrapper 证据 |
| `setAssetProperty` | medium | complete | scalar live apply/save/verify |
| `setAssetReferenceProperty` | medium | complete | reference root/class Policy |
| `setAssetStructuredProperty` | medium | complete | Struct/Array/Set/Map schema |
| `setAnimationScaleFix` | high | demand-driven | dedicated live/batch path；generic commit disabled |
| `setAdditiveBasePoseFix` | high | demand-driven | dedicated live path；generic commit disabled |
| `setMaterialInstanceScalarParameter` | medium | complete | typed material wrapper |
| `setMaterialInstanceVectorParameter` | medium | complete | typed material wrapper |
| `setMaterialInstanceTextureParameter` | medium | complete | typed reference/class validation |
| `setMaterialInstanceStaticSwitchParameter` | medium | complete | typed bool/static switch |
| `setDataTableCell` | medium | complete | exact row + field allowlist |
| `setDataTableRowFields` | medium | complete | exact row + bounded fields |
| `addDataTableRow` | high | complete | lifecycle confirmation/recovery |
| `removeDataTableRow` | high | complete | lifecycle confirmation/recovery |
| `renameDataTableRow` | high | complete | old/new row identity + recovery |

没有 unclassified Operation，也没有因 gap audit 新增 Operation。

## 9. Must-fix Gate

| Candidate | Repeated R4/R4.1 blocker | Reforge high-frequency blocker | Missing mechanical fact/closed-loop action | Decision |
|---|---|---|---|---|
| New impact/read Tool | No；现有 Tool 返回所需事实 | No | No | Reject |
| New target discovery Tool | Legacy 有问题，Full 已覆盖 | No | No | Reject |
| New scalar Writer | No；world state 3/3 正确 | No | No | Reject |
| New Blueprint Writer | No；default 3/3 Trusted | No | No | Reject |
| New reference Writer | No；grader normalization 已关闭 | No | No | Reject |
| Persistent arbitrary evidence ingest | No | No | No；且破坏 trust boundary | Reject |
| Generic mutation Tool | No | No | 无法满足有界 safety gate | Defer |

C5 结论：`0 Must-fix new tools`，无需进入新增 Tool 实现。

## 10. Public Tool Classification Appendix

本节覆盖 `TOOL_REGISTRY` 的全部 105 个公共 Tool。分类按功能族给出；族内例外单独标明。

### 10.1 Query（10）

`complete`：

- `ue_get_capabilities`
- `ue_get_project_status`
- `ue_search`
- `ue_get_asset`
- `ue_find_references`
- `ue_analyze_semantic_diff`
- `ue_build_verification_plan`
- `ue_evaluate_trust_verdict`

`usable-but-awkward`：

- `ue_analyze_change_impact`：事实完整，Agent 需严格遵守 requested depth/bounds。
- `ue_get_task_context`：Full 主路径完整；target scope 仍要求 Agent 最终确认。

### 10.2 Memory（12）

Low-level compatibility API 为 `usable-but-awkward`，但不是 0.8 gap：

- `ue_memory_search`
- `ue_memory_get`
- `ue_memory_add_rule`
- `ue_memory_record_finding`
- `ue_memory_record_task`
- `ue_memory_mark_superseded`
- `ue_memory_validate`

高层 Schema v3 API 为 `complete`：

- `ue_memory_get_context`
- `ue_memory_expand_node`
- `ue_memory_get_evidence`
- `ue_memory_update_knowledge`
- `ue_memory_update_work`

Memory 是固定 Project 的可选能力，不参与 UE Package mutation。

### 10.3 Live Read（17）

Editor/asset diagnostics 为 `complete`：

- `ue_editor_status`
- `ue_get_selection`
- `ue_get_open_assets`
- `ue_get_dirty_assets`
- `ue_get_current_level`
- `ue_get_pie_state`
- `ue_get_output_log`
- `ue_get_compile_errors`
- `ue_inspect_asset_live`
- `ue_get_blueprint_graph_selection`

Animation diagnostics 为 `demand-driven`（能力已完成，无 0.8 扩展证据）：

- `ue_analyze_animation_retarget`
- `ue_diagnose_animation_scale`
- `ue_diagnose_additive_animation`
- `ue_evaluate_animation_with_base_pose`
- `ue_plan_additive_base_pose_fix`
- `ue_diagnose_character_ground_contact`
- `ue_inspect_skeletal_secondary_motion`

### 10.4 Realtime（8）

通用 Realtime 为 `complete`：

- `ue_get_editor_context`
- `ue_start_batch_task`
- `ue_get_batch_task`
- `ue_cancel_batch_task`

Animation audit 为 `demand-driven`：

- `ue_start_animation_scale_audit`
- `ue_get_animation_scale_audit`
- `ue_cancel_animation_scale_audit`
- `ue_export_animation_scale_audit_report`

### 10.5 Live Action（8）

全部为 `complete`，且只改变 Editor focus/selection/loaded state、内存 compile/validation 或运行测试，不自动保存 Package：

- `ue_open_asset`
- `ue_focus_asset`
- `ue_sync_content_browser`
- `ue_focus_actor`
- `ue_compile_blueprint`
- `ue_validate_asset`
- `ue_validate_folder`
- `ue_run_automation_test`

### 10.6 Workflow（50）

高层窄变更（12）为 `complete`：

- `ue_set_blueprint_default`
- `ue_set_component_property`
- `ue_set_pin_default`
- `ue_set_asset_property`
- `ue_set_asset_reference_property`
- `ue_set_asset_structured_property`
- `ue_set_material_parameter`
- `ue_set_datatable_cell`
- `ue_set_datatable_row_fields`
- `ue_add_datatable_row`
- `ue_remove_datatable_row`
- `ue_rename_datatable_row`

Animation scale/additive workflow（10）为 `demand-driven`：

- `ue_plan_animation_scale_fix`
- `ue_plan_additive_base_pose_fix_apply`
- `ue_plan_animation_scale_fix_batch`
- `ue_get_animation_scale_fix_batch`
- `ue_apply_animation_scale_fix_batch_live`
- `ue_save_animation_scale_fix_batch`
- `ue_verify_animation_scale_fix_batch`
- `ue_refresh_animation_scale_fix_batch_index`
- `ue_rollback_animation_scale_fix_batch`
- `ue_undo_animation_scale_fix_batch`

通用受控 Live transaction（3）为 `complete`：

- `ue_apply_asset_property_live`
- `ue_undo_asset_property_live`
- `ue_discard_asset_property_live`

底层 Patch workflow（3）为 `complete`，但相对高层 wrapper 属 `usable-but-awkward`：

- `ue_plan_patch`
- `ue_dry_run_patch`
- `ue_apply_patch`

Animation retarget workflow（14）为 `demand-driven`：

- `ue_plan_animation_retarget`
- `ue_apply_animation_retarget_setup`
- `ue_start_animation_retarget_batch`
- `ue_get_animation_retarget_batch`
- `ue_start_animation_retarget_postprocess`
- `ue_get_animation_retarget_postprocess`
- `ue_plan_animation_retarget_postprocess`
- `ue_reopen_animation_retarget_postprocess`
- `ue_refresh_animation_retarget_postprocess_index`
- `ue_cancel_animation_retarget_batch`
- `ue_save_animation_retarget_batch`
- `ue_validate_animation_retarget`
- `ue_verify_animation_retarget_batch`
- `ue_rollback_animation_retarget_batch`

Verification、persistence、recovery 与 Change Set（8）为 `complete`：

- `ue_verify_asset`
- `ue_verify_live_write`
- `ue_get_asset_state`
- `ue_refresh_asset_index`
- `ue_save_authorized_asset`
- `ue_rollback_patch`
- `ue_create_change_set`
- `ue_get_change_set`

计数校验：10 + 12 + 17 + 8 + 8 + 50 = 105；无遗漏、无重复。

## 11. 0.8 Scope Freeze

### Shipped in the 0.8 capability scope

- R0 Task Context 与 deterministic relevant assets；
- R1 bounded Impact Analysis；
- R2 Change-Set-bound Semantic Diff；
- R3 Evidence-gated Verification Plan / Trust Verdict；
- R4/R4.1 real-agent deterministic benchmark；
- closed result enums、exact targetAssets、Change Set binding、Trust next-action ladder；
- Blueprint compile normalization、saved-revision rollback safety、reference derived-edge normalization；
- repeat aggregation、measurement drift/fail-closed、exact fixture recovery。

### Demand-driven backlog

- operation-discriminated Agent result typing；
- direct-only task template / SDK bound binding；
- Blueprint Description convenience wrapper；
- 跨 restart 的窄、固定来源 action evidence persistence；
- animation/retarget 新 writer 只按真实阻塞追加。

### Explicitly deferred

- Generic Blueprint Graph / Level Actor / Material Graph / Niagara / Sequencer / Control Rig mutation；
- arbitrary UObject method、Python、Console、Shell、SQL；
- generic asset lifecycle CRUD；
- source-control collaboration automation；
- R5 Value Provenance / Execution Trace。

### Known limitations

- Full agent 仍可能在 Tool 已给出正确事实时违反 task bound 或 final JSON value type；
- 完整 Trust 写链显著增加 Tool、时延与 Token；
- static Index/Reference 不能证明 runtime execution；
- action evidence 默认 session-local，restart 后需按 exact nextAction 重建；
- Memory、Live Editor 和 Workflow 都是显式启用能力，不可用时 section 级降级。

### R5 trigger

只有后续多个真实 Case 反复出现 `value-provenance-gap` 或 `execution-trace-gap`，并且比 guidance/Writer/Index 修复有更高收益时解冻。R4.1 两项 taxonomy 均为 0，因此当前决定是：

```text
R5 = deferred by benchmark evidence
```

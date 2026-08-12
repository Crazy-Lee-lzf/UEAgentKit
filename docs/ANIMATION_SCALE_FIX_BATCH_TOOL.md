# Animation Scale Fix Batch Plan

> 状态：P2 已完整实现 Plan / bounded Live Apply / Undo / Save / Independent Verify / Atomic Index Refresh / Persisted Rollback。真实 UE5.6 样本已通过持久化与 Index Refresh Preview / Rollback Smoke；多资产 Index Apply 由真实临时 SQLite + Revision Export Fixture 验证。

## 1. 目标

P1 已能把多个 AnimSequence 审计为 `normal`、`root-lock-candidate`、`root-track-candidate` 等分类。P2 不提供“修复全部”按钮，而是先把用户明确选择的候选转换为一组经过现有单资产 Policy / Revision 校验的子 Plan，再冻结成一个批量 Plan。

当前工具：

```text
ue_plan_animation_scale_fix_batch
ue_get_animation_scale_fix_batch
ue_apply_animation_scale_fix_batch_live
ue_save_animation_scale_fix_batch
ue_verify_animation_scale_fix_batch
ue_refresh_animation_scale_fix_batch_index
ue_rollback_animation_scale_fix_batch
ue_undo_animation_scale_fix_batch
```

当前安全边界：

- 不提供 Save All；持久化步骤有明确数量上限；
- Index Refresh Preview 不切换 active pointer，Apply 只允许一次原子 paired-generation 切换；
- Persisted Rollback Commit 要求目标 Unreal Editor 已关闭。

## 2. 输入来源

Batch Plan 只能引用固定 MCP `WorkRoot` 内由 P1 导出的 Audit Report：

```text
WorkRoot/animation-scale-audits/<auditTaskId>/report.json
```

调用方提供：

```text
audit_task_id
audit_report_id
asset_paths
expected_final_scale_overrides   optional
final_scale_tolerance            optional
description                      optional
```

不接受任意文件路径。服务会重新计算 Report SHA-256，要求与 `audit_report_id` 完全一致，并要求 Report 的 `taskId` 匹配且状态为 `completed`。Cancelled / Failed 的部分 Audit Report 不能生成 Batch Plan。

`asset_paths` 必须显式给出，最多 100 个且不可重复。Batch Planner 不会自动把不支持的分类静默过滤掉；若用户选中了 `normal`、Additive、Root Motion Review 等当前不能安全自动修复的条目，整个 Plan 直接拒绝。

当前自动规划只支持：

```text
root-lock-candidate
root-track-candidate
```

## 3. 目标比例来源

默认 `expectedFinalScale` 不写死 `100`，而是从每个 Audit Item 的：

```text
rootTrack.referenceComponentScale
```

读取目标 Skeleton Root Reference Component Scale。

当前 `setAnimationScaleFix` 的最终验证是统一标量，因此 Reference Scale 必须近似 Uniform；非均匀 Root Reference Scale 会拒绝自动 Batch Plan。

示例：

```text
Asset A Reference Root Scale = 50
Asset B Reference Root Scale = 75

Batch Plan expectedFinalScale:
A = 50
B = 75
```

用户可以通过 `expected_final_scale_overrides` 对明确资产显式覆盖。

### Root Lock Candidate

默认策略：

```text
forceRootLock      = true
rootMotionRootLock = RefPose
rootTrackScaleMode = Keep
expectedFinalScale = Skeleton Reference Scale
```

因为 Force Root Lock 的最终比例来自 Reference Pose，显式 override 若与 Reference Scale 不一致会直接拒绝，避免生成一个注定在 Runtime Verify 阶段失败的 Plan。

### Root Track Candidate

默认策略：

```text
rootTrackScaleMode = ReferenceLocal
expectedFinalScale = Skeleton Reference Scale
```

若显式 override 与 Reference Scale 不同，则改为：

```text
rootTrackScaleMode = Uniform
uniformScale       = explicit override
expectedFinalScale = explicit override
```

## 4. 子 Plan 复用

Batch Planner 不创建新的多资产 Patch 格式。每个资产仍调用现有单资产：

```text
ue_plan_animation_scale_fix
→ setAnimationScaleFix
→ Policy validation
→ exact expectedRevision
→ exact expectedAssetClass
```

Batch Plan 只冻结这些已经验证过的子 Plan：

```text
assetPath
classification
rootBone
referenceFinalScale
expectedFinalScale
expectedFinalScaleSource
strategy
value
planId
patchDigest
expectedRevision
assetClass
risk
commitAllowedByPolicy
```

这样 P2 不绕过 P0 已经验证过的单资产 Policy / Revision / Operation 契约。

## 5. 原子规划

所有 Audit Item 和自动策略会先完整验证，然后才开始创建子 Plan。

若第 N 个子 Plan 因 Policy、Revision、Freshness 等原因失败：

```text
已创建的本次子 Plan
→ 从 session _plans 删除
→ 删除 WorkRoot/plans/<planId>
→ 不创建 Batch Plan
```

因此不会返回“半成功”的 Batch Plan，也不会把失败资产静默跳过。

Batch Plan 自身写到固定：

```text
WorkRoot/animation-scale-fix-batches/<batchPlanId>/plan.json
```

并返回 `batchPlanDigest=sha256:...`。`ue_get_animation_scale_fix_batch` 会重新读取文件并核对 SHA 和内容；文件被修改后会报告 tamper，而不是继续使用。

## 6. Bounded Live Apply / Undo

Batch Live Apply 仍复用现有单资产 `ue_apply_asset_property_live` / `ue_undo_asset_property_live`，不新增 UObject 写入实现。

首次 Apply 必须精确确认：

```text
LIVE APPLY BATCH <batchPlanId>
```

服务创建一个现有 Change Set，并按 Batch Plan 顺序调用每个子 Plan。单次调用最多处理 8 个资产；若未完成，会返回 session-local `batchApplyReceipt`，后续调用只能携带该 receipt 继续。`ue_get_animation_scale_fix_batch` 只读 execution snapshot，不会在轮询时推进写入。

每个 changed 资产保留自己的 `liveApplyReceipt`、`transactionId`、`editorSessionId` 和 Runtime Verification。单资产 `setAnimationScaleFix` 已在成功返回前完成 Final Component Pose Verify，Batch 层只汇总 `referenceLocalScale`、`finalEvaluationStatus`、`finalRootScale`，不重新实现比例验证。

若第 N 个资产失败，Batch 立即 fail-stop：前面 changed 的资产保持 `applied`，当前项为 `failed`，后续仍为 `pending`，整体为 `partially_applied`；不会静默跳过。若此前没有任何 changed 写入，临时空 Change Set 会被删除。

Batch Undo 首次必须精确确认：

```text
UNDO BATCH <batchPlanId>
```

Undo 只处理实际 `applied` 的资产，并按相反顺序逐项调用单资产 Undo；同样每次最多 8 个，通过 `batchUndoReceipt` 继续。尚未执行的 pending 项会标记为 `not-applied`。Undo 失败停在当前事务，可用同一 receipt 重试。

真实 UE5.6 Smoke：

```text
Persisted Root Track Scale = 1
Reference Root Scale       = 100
Batch Live Apply           = 100
Runtime Final Root Scale   = 100
Batch Undo                 = 1
Package SHA                = unchanged
SQLite SHA                 = unchanged
Saved                      = false
Editor Window              = minimized
```

## 7. 持久化闭环

P2 持久化仍复用已经验证过的单资产 Save / Verify / Backup / Rollback / Snapshot Refresh 能力，Batch 层只负责有界编排，不实现 Save All 或任意文件复制。

### Batch Save

首次 Save 必须精确确认：

```text
SAVE BATCH <batchPlanId>
```

只保存实际 changed 的资产，每次最多 2 个。每个资产仍执行单资产 Authorized Save：保存前备份、精确 Policy / Revision 校验、保存后独立 Unreal Export。保存成功后，原备份会提升为标准 Rollback Manifest。若 Package 已保存但 Rollback Manifest 生成失败，使用同一个 Batch receipt 重试只补 Manifest，不会再次保存同一资产。

### Independent Verify

`ue_verify_animation_scale_fix_batch` 每次最多验证 2 个已保存资产，并复用单资产 `ue_verify_live_write`。Batch 只在 `actualRevision` 与持久化预期值都通过独立重载验证后把子项标记为 `verified`。

### 保留修改：Atomic Index Refresh

`ue_refresh_animation_scale_fix_batch_index` 分两阶段：

```text
Preview / Prepare   max 2 assets per call
→ all candidates ready
→ REFRESH BATCH <batchPlanId>
→ Apply once
→ one paired SQLite + Revision Export generation
→ atomic active pointer switch
→ restart MCP session
```

Preview 只在固定 WorkRoot 下生成短路径、独立导出的 candidate，不修改当前 SQLite，也不切换 active pointer。Apply 前会重新计算每个目标 Package 的 SHA-256，防止 Preview 后磁盘发生变化。

Apply 不循环调用单资产 `ue_refresh_asset_index`。它只 clone 当前 Revision Export 一次、复制 SQLite 一次，然后把所有 candidate 合入同一个 staging generation；所有 Revision 与数据库完整性检查通过后才一次性切换 active pointer。任一资产失败都不会产生半刷新的 active snapshot。

正式 XinYueHu UE5.6 Smoke 只执行到 Index Refresh Preview，确认 Package / SQLite SHA 均不变；没有对正式样本执行 active pointer Apply。多资产 Apply 的原子 paired-generation 行为由真实临时 SQLite + Revision Export Fixture 验证：同一 generation 同时更新 A、加入 B、保留无关旧资产与导出文件，并在 pointer 中记录完整 `refreshedAssets`。

### 撤销修改：Persisted Rollback

Persisted Rollback 使用保存时生成的标准 Rollback Manifest，顺序与 Save 相反：

```text
Rollback DryRun     max 2 assets per call, zero write
→ all saved items ready
→ close target Unreal Editor
→ ROLLBACK BATCH <batchPlanId>
→ Commit            max 2 assets per call
→ independent reload verification
```

Commit 继续复用 `RunRollback.ps1`，因此目标 UE Editor 未关闭时会硬拒绝。若某个 Commit 子项失败，cursor 不前移，可使用同一个 receipt 重试当前资产。

如果 Batch Save 在中途失败，已经保存的项与仍在 Editor 内存中的未保存项会分层恢复：先用 `UNDO BATCH` 只撤销 `saveState=unsaved` 的 Live 项，再关闭 Editor，对已保存项执行 persisted Rollback。已经 `rolled-back` 的项不会再次被误判为未保存 Live 写入。

Rollback 独立验证使用短路径：

```text
WorkRoot/rollback-verify/<short-id>/...
```

避免 Windows / UE Canonical Export 因过深的 Save Receipt / DryRun Receipt 目录导致路径超过常见 Win32 路径限制。真实问题曾产生约 300 字符 Canonical 路径；缩短后真实样本路径约 212 字符。

真实 UE5.6 持久化 Smoke：

```text
Baseline Package SHA       = a3cb62ec5d0f804e5612e4cc383a9b9dbdf9f9e9dd9d01625479c9440b5d7f5d
Temporary Saved Revision   = 8ee5391dffd95cd223b4a0db7c257aee8fe5da7ca7b6810b1a6cb57939202abc
Independent Verify         = passed
Index Refresh Preview      = passed
Index Preview SQLite write = none
Rollback DryRun write      = none
Rollback Commit            = passed
Restored Package SHA       = exact baseline
SQLite SHA                 = unchanged
Editor before Rollback     = closed
```

## 8. 当前限制与 P2 收口

单个 Batch Plan 最大 100 个资产，与现有 Change Set 最大 100 个 Live Write Operation 对齐；单个 MCP session 最多保留 50 个 Batch Plan。Live Apply / Undo 每次最多 8 个资产；Save / Independent Verify / Index Refresh Preview / Persisted Rollback 每次最多 2 个资产。

当前 P2 已完成：

```text
Completed Audit Report
→ Explicit Candidate Selection
→ Derived Fix Strategy
→ Validated Per-asset Child Plans
→ Immutable Batch Plan
→ Bounded Change Set Live Apply
→ Runtime Verification Snapshot
→ [Unsaved branch] Reverse-order Bounded Undo
→ [Persist branch] Bounded Authorized Save
→ Independent Verify
→ [Keep branch] Bounded Index Preview → Atomic Paired Snapshot Apply → MCP Restart
→ [Revert branch] Rollback DryRun → Editor Closed → Reverse Persisted Rollback Commit
```

P2 的 Plan / Live Apply / Save / Verify / Index Refresh / Rollback 纵向链路已闭合。后续进入 P3 Retarget Output Post-processing，而不是继续扩张当前 Batch API。

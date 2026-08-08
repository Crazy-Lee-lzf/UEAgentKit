# Animation Scale Fix Batch Plan

> 状态：P2 Plan + bounded Live Apply / Undo 纵向切片已实现并通过真实 UE5.6 Smoke；Save / Independent Verify / Index Refresh / Rollback 待继续。

## 1. 目标

P1 已能把多个 AnimSequence 审计为 `normal`、`root-lock-candidate`、`root-track-candidate` 等分类。P2 不提供“修复全部”按钮，而是先把用户明确选择的候选转换为一组经过现有单资产 Policy / Revision 校验的子 Plan，再冻结成一个批量 Plan。

当前工具：

```text
ue_plan_animation_scale_fix_batch
ue_get_animation_scale_fix_batch
ue_apply_animation_scale_fix_batch_live
ue_undo_animation_scale_fix_batch
```

当前阶段不会：

- 保存 Package；
- 刷新 Index。

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

## 7. 当前限制

当前单个 Batch Plan 最大资产数为 100，与现有 Change Set 最大 100 个 Live Write Operation 对齐，为后续 P2 Apply 阶段保留一一对应关系；单个 MCP session 最多保留 50 个 Batch Plan，超过后拒绝继续创建，避免无界增长。

当前已完成：

```text
Completed Audit Report
→ Explicit Candidate Selection
→ Derived Fix Strategy
→ Validated Per-asset Child Plans
→ Immutable Batch Plan
→ Bounded Change Set Live Apply
→ Runtime Verification Snapshot
→ Reverse-order Bounded Undo
```

下一条 P2 纵向切片进入持久化：

```text
Batch Save
Independent Verify
Index Refresh
Rollback
```

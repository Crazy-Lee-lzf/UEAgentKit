# Animation Scale Fix Batch Plan

> 状态：P2 第一条纵向切片已实现；当前只生成不可变 Batch Plan，不执行 Live Apply / Save / Rollback。

## 1. 目标

P1 已能把多个 AnimSequence 审计为 `normal`、`root-lock-candidate`、`root-track-candidate` 等分类。P2 不提供“修复全部”按钮，而是先把用户明确选择的候选转换为一组经过现有单资产 Policy / Revision 校验的子 Plan，再冻结成一个批量 Plan。

当前工具：

```text
ue_plan_animation_scale_fix_batch
ue_get_animation_scale_fix_batch
```

当前阶段不会：

- 调用 Live Editor 写入；
- 修改或 Dirty `.uasset`；
- 保存 Package；
- 创建 Change Set；
- 批量 Undo / Rollback；
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

## 6. 当前限制

当前单个 Batch Plan 最大资产数为 100，与现有 Change Set 最大 100 个 Live Write Operation 对齐，为后续 P2 Apply 阶段保留一一对应关系；单个 MCP session 最多保留 50 个 Batch Plan，超过后拒绝继续创建，避免无界增长。

当前只完成：

```text
Completed Audit Report
→ Explicit Candidate Selection
→ Derived Fix Strategy
→ Validated Per-asset Child Plans
→ Immutable Batch Plan
```

下一条 P2 纵向切片再实现：

```text
ue_apply_animation_scale_fix_batch_live
ue_get_animation_scale_fix_batch   扩展运行状态
ue_undo_animation_scale_fix_batch
```

之后才进入批量 Save / Independent Verify / Index Refresh / Rollback。

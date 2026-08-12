# Retarget Output Post-process

> 状态：P3 第一条只读纵向切片已实现并通过真实 UE5.6 零残留 Smoke。当前只生成后处理建议，不自动修改 Retarget 输出。

## 1. 目标

Retarget Batch 完成后，Agent 不应重新扫描整个输出目录，也不应在 Batch 内静默修改 Root Lock / Root Track。P3 第一阶段直接使用 Retarget Task 已记录的精确 `outputs[]`，把输出分成：

```text
AnimSequence
BlendSpace
AimOffset
AnimMontage
Other / Unknown
```

只有 `AnimSequence` 进入现有 Animation Scale Audit。BlendSpace / AimOffset / Montage 只记录为后续引用更新任务，不修改资产。

## 2. 工具

```text
ue_start_animation_retarget_postprocess
ue_get_animation_retarget_postprocess
ue_plan_animation_retarget_postprocess
```

来源必须是当前 MCP session 中状态为 `completed` 或 `saved` 的 Retarget Batch Task。P3 context 是纯读取接口，不会像 `ue_get_animation_retarget_batch` 一样推进 queued task。

Retarget Batch 输出现在额外返回：

```text
assetClass
assetType
skeletonPath
```

因此 P3 不需要按文件名猜输出类型。

## 3. 分析链路

```text
Completed / Saved Retarget Batch
→ exact outputs[]
→ classify output assets
→ AnimSequence only: bounded Animation Scale Audit
→ root-lock / root-track candidates
→ Additive / Root Motion / unsupported cases: manual review
→ BlendSpace / AimOffset / Montage: reference follow-up
→ Suggested Post-process Plan
```

Scale Audit 继续复用 P1 的分批、Editor Session、诊断分类和 Audit Report 逻辑；P3 不复制另一套动画比例诊断实现。

## 4. Suggested Plan 安全边界

Suggested Plan 固定写入：

```text
WorkRoot/retarget-postprocess/<postprocessId>/plan.json
```

它是只读建议，不是可执行 P2 Batch Plan：

```text
modifiesAssets = false
autoApplyAllowed = false
requiresUserReview = true
referenceAssetMutationImplemented = false
```

如果存在可自动修复的 `root-lock-candidate` / `root-track-candidate`，Plan 还会明确：

```text
requiresRetargetOutputIndexRefreshBeforeP2Plan = true
p2Workflow = animation-scale-fix-batch
```

原因是新 Retarget 输出在 Batch 完成后可能尚未进入 immutable SQLite。P2 executable Batch Plan 依赖 fresh Index Revision；P3 不能为了便利绕过该 Freshness / Revision 契约。

Plan 文件记录 SHA-256；同一 session 重复取 Plan 会重新校验 digest，文件被修改后拒绝继续使用。

## 5. 当前建议分类

P3 自动建议只接受：

```text
root-lock-candidate
root-track-candidate
```

`normal` 记录为无需修复。Additive、Root Motion Review 等当前无法安全自动处理的分类进入 `manualReview`，不静默跳过，也不提供旁路。

BlendSpace / AimOffset / AnimMontage 进入 `referenceFollowups`。第一阶段只记录引用后处理需求，不重建或修改这些资产。

## 6. 真实 UE5.6 Smoke

测试使用已有 IK Retargeter，不运行 Retarget Setup，也不覆盖现有动画。Smoke 随机创建一个临时输出：

```text
/Game/UEAgentKitRetargetTests/Postprocess/P3_<nonce>_MM_Idle
```

真实结果：

```text
Retarget output assetType        = AnimSequence
assetClass                       = /Script/Engine.AnimSequence
skeletonPath                     = present
P3 Scale Audit classification    = root-lock-candidate
Suggested Plan                   = created under fixed WorkRoot
autoApplyAllowed                 = false
modifiesAssets                   = false
Retarget Rollback DryRun         = delete 1 / restore 0
Retarget Rollback Commit         = passed
Final P3_*.uasset count          = 0
UnrealEditor after test          = closed
```

Smoke 使用 D3D11 Editor。Catalog commandlet 仍使用既有 NullRHI 路径读取 source/target mesh；没有打开动画编辑器窗口的 NullRHI Slate 场景。

## 7. 下一条 P3 纵向切片

当前不能直接把 Suggested Plan 转成 P2 Batch Plan。下一步先实现 Retarget 输出的持久化 / paired Index Refresh 边界：

```text
Retarget Batch Complete
→ P3 Audit + Suggested Plan
→ User Review
→ Authorized Retarget Save / Independent Verify
→ paired Revision Export + SQLite Refresh
→ MCP Restart
→ Rebuild/validate suggestions against fresh Index Revision
→ convert eligible AnimSequence suggestions to existing P2 Batch Plan
```

之后再单独处理 BlendSpace / AimOffset / Montage 引用更新。Additive + Base Pose 仍属于 P4，不在 P3 中绕过。

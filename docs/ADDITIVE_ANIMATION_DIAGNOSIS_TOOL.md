# 只读 Additive / Base Pose 诊断工具

> 分支：`feature/live-editor-realtime-io`
> 适用引擎：UE 5.6
> 状态：P4 第一片已完成（只读诊断），并通过真实 UE5.6 Editor Smoke

---

## 1. 目标

Additive 动画的最终姿势是「Base Pose + Additive Delta」合成而来，`AnimSingleNodeInstance` 无法脱离 Base Pose 正确求值 Additive；Additive 的压缩 Scale 是 Delta 而非绝对 Scale，且 Base Pose 引用可能仍指向 Manny 动画。因此对 Additive 单独做 Scale Audit 只会得到 `additive-requires-base-pose`，无法判断 Base Pose 引用本身是否可用。

`ue_diagnose_additive_animation` 是只读诊断，针对每个 AnimSequence 回答：

```text
是否是 Additive
Additive 类型（LocalSpace / RotationOffsetMeshSpace）
Base Pose 类型（ReferencePose / AnimationScaled / AnimationFrame / LocalAnimationFrame）
Base Pose Sequence 是否已解析
Base Pose Skeleton 是否与动画 Skeleton 兼容
Base Pose Reference Frame 是否落在序列帧范围内
是否具备组合求值可行性
建议下一步
```

它不修改、不保存、不创建 Dirty Package，也不做组合求值（组合求值是后续 P4 片）。

---

## 2. Tool 与能力

```text
ue_diagnose_additive_animation
capability = retarget.inspect
```

与 `ue_diagnose_animation_scale` 同属只读 inspect 能力。参数：

| 参数 | 说明 |
|---|---|
| `animationPaths` | 1–32 个精确 `/Game/...Asset.Asset` AnimSequence Object Path |
| `loadIfNeeded` | 是否允许为诊断显式加载目标动画，默认 `false` |

`loadIfNeeded=false` 且资产未加载时返回 `status=not-loaded`；`loadIfNeeded=true` 但目标不是 AnimSequence 时返回 `status=not-an-animation-sequence`；缺失 Skeleton 返回 `status=missing-skeleton`。

---

## 3. 返回结构

每个资产返回：

```text
assetPath
status
skeletonPath
additiveAnimType / additiveTypeName
additiveBasePoseType / basePoseTypeName
additiveRefFrameIndex
additiveRefSequencePath
basePose
  refSequenceResolved
  skeletonPath（解析后）
  skeletonCompatible
  frameCount
  refFrameValid
classification
combinedEvaluationFeasible
suggestedNextStep
```

C++ 侧只读取 `UAnimSequence` 的公开字段并解析 `RefPoseSeq` 的 Skeleton 与帧数；分类与建议在 Python `additive_diagnose.py` 内完成，无 C++ 写入面。

---

## 4. 分类

```text
non-additive
additive-valid
additive-missing-base-pose
additive-base-pose-skeleton-mismatch
additive-base-pose-ref-frame-invalid
unsupported-composite
load-failed
```

判定顺序：

1. `status != success` → `load-failed` / `unsupported-composite`；
2. `additiveTypeName ∈ {None, 空}` → `non-additive`；
3. `basePoseTypeName == ReferencePose` → `additive-valid`（Skeleton 参考姿势无需外部序列）；
4. Base Pose 需要序列（`AnimationScaled` / `AnimationFrame` / `LocalAnimationFrame`）：
   - `refSequenceResolved=false` → `additive-missing-base-pose`；
   - `skeletonCompatible=false` → `additive-base-pose-skeleton-mismatch`；
   - 帧基类型（`AnimationFrame` / `LocalAnimationFrame`）且 `refFrameValid=false` → `additive-base-pose-ref-frame-invalid`；
   - 其余 → `additive-valid`。

`combinedEvaluationFeasible = (classification == "additive-valid")`。

---

## 5. 与 Scale Audit 的关系

`ue_diagnose_animation_scale` 已返回 Additive 元数据并分类为 `additive-requires-base-pose`，但它不解析 Base Pose 引用、不校验 Skeleton 兼容性与 RefFrame 有效性。本工具把 Additive 单独拆出，补齐「Base Pose 引用是否可用」这层诊断，为后续组合求值（`ue_evaluate_animation_with_base_pose`）和修复（`ue_plan_additive_base_pose_fix`）做前置。

---

## 6. 真实 UE5.6 Smoke

测试资产（测试工程「我的项目」）：

```text
/Game/Characters/Mannequins/Anims/Pistol/Jump/MM_Pistol_Jump_RecoveryAdditive
/Game/Characters/Mannequins/Anims/Rifle/Jump/MM_Rifle_Jump_RecoveryAdditive
/Game/Characters/XinYueHu/Animations/Retargeted/MM_Pistol_Jump_RecoveryAdditive_XinYueHu
/Game/Characters/XinYueHu/Animations/Retargeted/MM_Rifle_Jump_RecoveryAdditive_XinYueHu
/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu（非 Additive 对照）
```

真实结果：

```text
4 个 Additive 样本          = LocalSpaceBase + AnimationFrame
RefPoseSeq                 = 指向动画自身
Base Pose Skeleton         = 与动画 Skeleton 兼容（源=Manny，重定向=心月狐）
RefFrame                    = 超出序列帧范围 → additive-base-pose-ref-frame-invalid
非 Additive 对照           = non-additive
Smoke exit                 = 0（零残留，UnrealEditor 已关闭）
```

这验证了工具能区分 Additive / 非 Additive，并能精确指出「Base Pose Reference Frame 越界」这类真实数据问题。`RefPoseSeq` 自引用 + `RefFrame` 越界正是 P4 修复片要处理的对象，本片只诊断、不修复。

---

## 7. 当前边界

- 只读，不组合求值，不修改 Base Pose 引用 / RefFrame / Additive 类型。
- 第一版只支持 `AnimSequence`；BlendSpace / AimOffset / Montage 的 Additive 引用暂不解析。
- 组合求值（Additive + Base Pose → 最终 Component Pose）留待 `ue_evaluate_animation_with_base_pose`。
- 修复闭环（替换 Base Pose 引用、修正 Frame、在组合上下文验证最终 Scale）留待 `ue_plan_additive_base_pose_fix`。

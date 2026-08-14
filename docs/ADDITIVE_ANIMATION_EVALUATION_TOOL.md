# 只读 Additive / Base Pose 组合求值工具

> 分支：`feature/live-editor-realtime-io`
> 适用引擎：UE 5.6
> 状态：P4 第二片已完成（只读组合求值），并通过真实 UE5.6 Editor Smoke

---

## 1. 目标

`ue_diagnose_additive_animation` 回答「Base Pose 引用是否可用」，但只解析元数据，不真正合成姿势。Additive 动画的最终姿势是 `Base Pose + Additive Delta` 组合而来，且 Additive 压缩 Scale 是 Delta 而非绝对 Scale。要判断「最终比例问题到底来自 Base Pose 还是 Additive Delta」，必须实际组合求值。

`ue_evaluate_animation_with_base_pose` 是只读组合求值，针对每个 AnimSequence：

1. 解析 Additive 元数据与 Base Pose 引用（与诊断一致）；
2. 用引擎内置路径实际合成：`GetAdditiveBasePose`（绝对 Base Pose）→ `GetBonePose_Additive`（Additive Delta）→ `FAnimationRuntime::AccumulateAdditivePose`（累加）→ `FCSPose<FCompactPose>`（组件空间）；
3. 对每个请求骨骼，输出 `base` / `additiveDelta` / `combined` 三层 Scale 与 Location。

它不修改、不保存、不创建 Dirty Package，也不落地任何 SkeletalMeshComponent 到世界。

---

## 2. Tool 与能力

```text
ue_evaluate_animation_with_base_pose
capability = retarget.inspect
```

参数：

| 参数 | 说明 |
|---|---|
| `animationPaths` | 1–32 个精确 `/Game/...Asset.Asset` AnimSequence Object Path |
| `boneNames` | 1–16 个骨骼名（如 `root` / `pelvis` / `Root` / `Bip001Pelvis`） |
| `loadIfNeeded` | 是否允许为求值显式加载目标动画，默认 `false` |

与 `ue_diagnose_animation_scale` 同属只读 inspect 能力。

---

## 3. 返回结构

每个资产返回：

```text
assetPath / status / skeletonPath
additiveTypeName / basePoseTypeName
additiveRefFrameIndex / additiveRefSequencePath
basePose
  refSequenceResolved / skeletonPath / skeletonCompatible / frameCount / refFrameValid
classification（复用 additive_diagnose 分类）
combinedEvaluationFeasible（数据是否干净到可信任）
evaluation
  status = evaluated | skipped-non-additive | skipped-missing-base-pose
           | skipped-skeleton-mismatch | unavailable | skeleton-empty | bone-container-invalid
  source = editor-bone-pose-additive-accumulate
  refFrameClamped（Base Pose Reference Frame 越界时由引擎钳制）
  samples[]（fraction=0.0 / 0.5 / 1.0）
    bones[]
      baseComponentScale / baseComponentLocation
      additiveDeltaLocalScale / additiveDeltaComponentScale
      combinedComponentScale / combinedComponentLocation
evaluationFeasible（实际是否产出了组合姿势）
suggestedNextStep
```

---

## 4. 求值可行性判定

- **硬跳过**（引擎无法安全合成，直接返回 `skipped-*`）：
  - 非 Additive → `skipped-non-additive`；
  - Base Pose 需要序列（`AnimationScaled` / `AnimationFrame`）但 `RefPoseSeq` 缺失 → `skipped-missing-base-pose`；
  - `RefPoseSeq` 属于不同 Skeleton → `skipped-skeleton-mismatch`。
- **软钳制**（引擎会钳制但仍合成，标记 `refFrameClamped=true`）：
  - 帧基 Base Pose（`AnimationFrame` / `LocalAnimationFrame`）且 `RefFrameIndex` 越界。

因此 `evaluationFeasible` 与诊断的 `combinedEvaluationFeasible` 语义不同：前者表示「实际上合成了」，后者表示「数据干净到可信任」。越界 RefFrame 属于后者为 `false`、前者为 `true` 的情形。

---

## 5. 与诊断工具的关系

- `ue_diagnose_additive_animation`：只解析引用，判断 Base Pose 是否可用；
- `ue_evaluate_animation_with_base_pose`：实际合成，区分「最终 Scale 来自 Base Pose 还是 Additive Delta」；
- 二者共享 `classification` 与 `combinedEvaluationFeasible`（复用 `additive_diagnose.py`），求值工具额外提供 `evaluationFeasible` 与三层 pose 采样。

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

真实结果（`boneNames=["root", "pelvis", "Root", "Bip001Pelvis"]`，`fraction=0.0`）：

```text
源 Manny Additive
  baseComponentScale    ≈ (1, 1, 1)
  additiveDeltaLocalScale ≈ 0
  combinedComponentScale ≈ (1, 1, 1)

重定向心月狐 Additive
  baseComponentScale    ≈ (99, 98.999994, 98.999994)
  additiveDeltaLocalScale ≈ (-0.01, -0.01, -0.01)
  combinedComponentScale ≈ (99, 98.999994, 98.999994)

非 Additive 对照          → skipped-non-additive
Smoke exit                = 0（零残留，UnrealEditor 已关闭）
```

关键结论：Additive Delta 的 Scale 近似 0（`LocalSpaceBase` 的 Scale 是加性 Delta），最终 ≈99 的比例**完全来自 Base Pose**——而当前 Base Pose 是自引用 + 越界 RefFrame 被引擎钳制后的最后一帧，携带了重定向缩放。这正是 P4 修复片要处理的对象：把 `RefPoseSeq` 指向正确的心月狐 Base Pose，并修正 `RefFrameIndex`，最终组合 Scale 才可信。

---

## 7. 实现要点（引擎内置路径）

```text
FBoneContainer（由 Skeleton 全部骨骼构建）
  → FCompactPose.ResetToRefPose + GetAdditiveBasePose      （绝对 Base Pose）
  → FCompactPose.ResetToAdditiveIdentity + GetBonePose_Additive （Additive Delta）
  → FAnimationRuntime::AccumulateAdditivePose(Base, Delta, 1.0, AdditiveType)
  → FCSPose<FCompactPose>::InitPose + GetComponentSpaceTransform
```

注意：`FCompactPose` 使用 `TMemStackAllocator`，必须包裹 `FMemMark Mark(FMemStack::Get());`，否则在 `ResetToRefPose` 处触发 `MemStack.h:138` 断言（`NumMarks > 0`）。

---

## 8. 当前边界

- 只读，不修改 Base Pose 引用 / RefFrame / Additive 类型。
- 第一版只支持 `AnimSequence`；BlendSpace / AimOffset / Montage 的 Additive 引用暂不解析。
- 固定 3 个采样点（`fraction=0.0 / 0.5 / 1.0`），不支持自定义采样时间。
- 不输出旋转；Scale 与 Location 已覆盖 Root / Pelvis 最终姿势判定需求。
- 修复闭环（替换 Base Pose 引用、修正 Frame、在组合上下文验证最终 Scale）留待 `ue_plan_additive_base_pose_fix`（P4 第三片）。

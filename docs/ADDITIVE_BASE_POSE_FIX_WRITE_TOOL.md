# Additive / Base Pose 修复写入工具

> 适用引擎：UE 5.6
> 状态：P4 第四片已完成（执行写入，单资产 live-write；真实 Editor 写入 Smoke 待跑）

---

## 1. 目标

前三片解决了「Base Pose 引用是否可用」（诊断）、「最终比例来自 Base Pose 还是 Delta」（组合求值）、「要修什么」（只读修复计划）。第四片解决「**按计划真正写入**」：替换 `RefPoseSeq`、写回 `RefFrameIndex`、可选修正 `AdditiveAnimType` / `RefPoseType`，并在写入后重新合成组合姿势验证最终 Root Scale。

范围限定「我的项目」——写入面只在测试工程内，禁止触碰其他工程内容。其他工程的资产可复制进「我的项目」后作为 Base Pose 使用。

---

## 2. Tool 与操作

```text
ue_plan_additive_base_pose_fix_apply   （plan，workflow / planning，high_level_change）
  → operation = setAdditiveBasePoseFix   （C++ live-write）
  → 应用走 ue_apply_asset_property_live
  → Undo / Discard / Save / Verify 复用现有 live-write 闭环
```

参数（plan 工具）：

| 参数 | 说明 |
|---|---|
| `asset_path` | 目标 Additive AnimSequence Object Path |
| `ref_sequence_path` | 正确的同骨架 Base Pose Object Path（必填） |
| `ref_frame_index` | 目标 Base Pose 帧索引（必填，非负整数） |
| `additive_anim_type` | 可选：`None` / `LocalSpaceBase` / `RotationOffsetMeshSpace` |
| `additive_base_pose_type` | 可选：`None` / `ReferencePose` / `AnimationScaled` / `AnimationFrame` / `LocalAnimationFrame` |
| `expected_combined_root_scale` | 可选：写入后组合 Root Scale 期望值，触发 ReadAfter 组合验证 |
| `combined_scale_tolerance` | 可选：验证容差 |
| `root_bone` | 可选：验证组合比例的骨骼，默认 Skeleton 根骨骼 |

---

## 3. C++ 写入语义（`setAdditiveBasePoseFix`）

- 仅接受已加载的 Additive `UAnimSequence`（`AdditiveAnimType != AAT_None`）。
- `BasePoseType ∈ {AnimScaled, AnimFrame}` 时必须解析 `refSequencePath` 为 Base Pose：
  - 必须能加载为 `UAnimSequence`；
  - 不得自引用（`RefPoseSeq != Sequence`）；
  - 必须与目标 Additive 同 Skeleton；
  - `AnimFrame` 且 `refFrameIndex` 越界时拒绝（不静默钳制）。
- 写入 `RefPoseSeq` / `RefFrameIndex` / `AdditiveAnimType` / `RefPoseType`，`PostEditChange()` 通知。
- 快照/恢复覆盖上述四个字段，失败整笔 Transaction 回滚。
- `ReadAfter` 若提供 `expectedCombinedRootScale`，用引擎内置路径重新合成组合姿势（`GetAdditiveBasePose` → `GetBonePose_Additive` → `AccumulateAdditivePose` → `FCSPose`），验证最终 Root Scale 是否匹配，失配即回滚。

---

## 4. 关键正确性修正

第四片顺带修正了第二片组合求值的 base 采样顺序 bug：

- `FCSPose<FCompactPose>::InitPose` 存储的是**引用**（不拷贝）；
- `FAnimationRuntime::AccumulateAdditivePose(BasePoseData, ...)` 会**就地**把 BasePose 改写为组合结果；
- 第二片原先把 `BaseComponentPose.InitPose(...)` 放在 accumulate **之后**，导致 `baseComponentScale` 实际等于 `combinedComponentScale`。

修正后 base 在 accumulate **之前**采样，`baseComponentScale` 才是真正的 Base Pose 单独比例。写入验证的 `EvaluateAdditiveCombinedScale` 从一开始就按正确顺序采样。

---

## 5. Policy / 能力约束

- plan 阶段 `validate_patch` 校验 value（字段、`refSequencePath` 为合法 `/Game/...` object path、`refFrameIndex` 非负整数、类型枚举）。
- `refSequencePath` 必须落在 policy `allowedReferenceRoots` 内，否则报 `reference-not-allowed`——这落实「不能动其他工程内容」。
- C++ 侧 `ProjectContent` + `/Game` 路径要求，只写已加载的非 Blueprint 项目 Content 资产。

---

## 6. 与其他工具的关系

```text
ue_diagnose_additive_animation            → Base Pose 引用是否可用（分类）
ue_evaluate_animation_with_base_pose      → 最终比例来自 Base Pose 还是 Delta（组合求值）
ue_plan_additive_base_pose_fix            → 要修什么、能否自动推导（只读计划）
ue_plan_additive_base_pose_fix_apply      → 按计划写入（本片，live-write）
ue_apply_asset_property_live              → 实际应用 + 组合验证
（未来）复合资产重建                       → 不在范围，遵循「不处理 Composite 变更」约束
```

---

## 7. 当前边界

- 单资产 live-write 已实现，Undo / Discard / Save / Verify 复用现有闭环。
- **真实 Editor 写入 Smoke 尚未执行**：需要在「我的项目」里准备一个可写的 Additive 副本 + 一个正确同骨架 Base Pose，再跑 plan → apply → 组合验证 → undo 的完整链路。
- 复合资产（AimOffset/BlendSpace/Montage）重建不在范围。
- 批量写入 / Index Refresh / Rollback 未做（参考 P2 scale-fix 的批处理，属于后续切片）。

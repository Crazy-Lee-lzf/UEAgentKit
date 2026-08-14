# 只读 Additive / Base Pose 修复计划工具

> 分支：`feature/live-editor-realtime-io`
> 适用引擎：UE 5.6
> 状态：P4 第三片已完成（只读修复计划，纯 Python，无写入面）

---

## 1. 目标

前两片解决了「Base Pose 引用是否可用」（诊断）与「最终比例来自 Base Pose 还是 Additive Delta」（组合求值）。第三片回答「要修什么、能不能自动推导」——但它**只产出计划，不执行任何写入**。

`ue_plan_additive_base_pose_fix` 是只读修复计划工具：对每个 Additive AnimSequence，复用 `ue_diagnose_additive_animation` 的元数据与分类，推导出逐字段修复计划，并区分「可自动推导」与「需人工选择」。

它不修改、不保存任何资产，不替换 Base Pose 引用，不重建 AimOffset/BlendSpace/Montage，不触碰正式 XinYueHu baseline。

---

## 2. Tool 与能力

```text
ue_plan_additive_base_pose_fix
capability = retarget.inspect（只读）
```

参数：

| 参数 | 说明 |
|---|---|
| `animationPaths` | 1–32 个精确 `/Game/...Asset.Asset` AnimSequence Object Path |
| `loadIfNeeded` | 是否允许为诊断显式加载目标动画，默认 `false` |

复用 `editor.diagnoseAdditiveAnimation`（纯 Python 侧推导计划，无新增 C++ 方法）。

---

## 3. 返回结构

每个资产返回诊断字段 + 一个 `fixPlan`：

```text
classification
combinedEvaluationFeasible
fixPlan
  classification
  fixesNeeded（是否需要修复）
  referenceMutationRequired（是否需要替换 Base Pose 引用）
  compositeRebuildRequired（是否需要重建复合资产，当前恒为 false）
  autoApplyAllowed（恒为 false）
  requiresUserReview（是否含需人工选择的字段）
  fixItems[]
    field = additiveRefSequencePath | additiveRefFrameIndex | additiveAnimType | additiveBasePoseType
    currentValue / proposedValue
    canAutoDerive（可自动推导）
    requiresUserSelection（需人工选择）
    reason
  suggestedNextStep
```

---

## 4. 计划推导规则

- `non-additive` / `additive-valid`：无需修复。
- `additive-base-pose-ref-frame-invalid`：
  - `additiveRefFrameIndex` → 钳制到 `[0, frameCount-1]`，提议 `0`，**可自动推导**；
  - 若 `basePoseTypeName == AnimationFrame` 且 `RefPoseSeq` 自引用 → `additiveRefSequencePath` 需替换为正确同骨架 Base Pose，**需人工选择**。
- `additive-missing-base-pose` / `additive-base-pose-skeleton-mismatch`：
  - `additiveRefSequencePath` 需替换/补齐，**需人工选择**。
- `unsupported-composite` / `load-failed`：无法推导修复。

`compositeRebuildRequired` 恒为 `false`（复合资产重建不在本片范围，且遵循「不处理 Composite 变更」约束）。

---

## 5. 与其他工具的关系

```text
ue_diagnose_additive_animation          → Base Pose 引用是否可用（分类）
ue_evaluate_animation_with_base_pose    → 最终比例来自 Base Pose 还是 Delta（组合求值）
ue_plan_additive_base_pose_fix          → 要修什么、能否自动推导（本片，只读计划）
（未来）apply 工具                       → 按计划执行写入（尚未实现）
```

计划中的 `suggestedNextStep` 指引：选定正确 Base Pose 动画后，重新运行 `ue_evaluate_animation_with_base_pose` 验证组合 Root/Pelvis Scale。

---

## 6. 真实数据预期

针对测试工程「我的项目」的 4 个 Additive 样本（`LocalSpaceBase` + `AnimationFrame`，`RefPoseSeq` 自引用 + `RefFrameIndex` 越界），计划应产出：

```text
fixesNeeded               = true
referenceMutationRequired = true（RefPoseSeq 自引用）
fixItems
  additiveRefFrameIndex    → proposedValue=0，canAutoDerive=true
  additiveRefSequencePath  → requiresUserSelection=true
requiresUserReview         = true
```

---

## 7. 当前边界

- 只读，不写入任何资产、不落地计划文件、不创建 Change Set。
- 引用替换 / Frame 修正 / Additive 类型修正的**执行**写入留待后续 apply 切片。
- 复合资产（AimOffset/BlendSpace/Montage）重建不在范围。

# 动画比例修复工具

> 分支：`feature/live-editor-realtime-io`
> 适用引擎：UE 5.6
> 状态：已实现并通过真实 Editor 内存修改、最终姿势验证和 Undo 回归

---

## 1. 目标

工具用于修复 `UAnimSequence` 播放后最终 Root 比例错误的问题。

它区分三层事实：

```text
Skeleton Reference Pose
Animation Raw / Compressed Root Track
UE 最终 Component Space Pose
```

不能仅凭 Root Track Scale 判断动画是否正确。当前心月狐样本中，正常和异常动画的 Root Track 都可能为 `1`，但最终求值结果分别为 `100` 和 `1`。

---

## 2. MCP 工具

### 2.1 创建修复计划

```text
ue_plan_animation_scale_fix
```

主要参数：

| 参数 | 作用 |
|---|---|
| `asset_path` | 精确 AnimSequence Object Path |
| `root_bone` | 精确 Root 骨骼名，例如 `Root` |
| `expected_final_scale` | 修改后必须达到的最终 Component Scale |
| `force_root_lock` | 修改 Force Root Lock |
| `enable_root_motion` | 修改 Enable Root Motion |
| `use_normalized_root_motion_scale` | 修改 Normalize Root Motion Scale |
| `root_motion_root_lock` | `RefPose`、`AnimFirstFrame` 或 `Zero` |
| `root_track_scale_mode` | `Keep`、`ReferenceLocal` 或 `Uniform` |
| `uniform_scale` | `Uniform` 模式使用的明确比例 |
| `final_scale_tolerance` | 最终 Component Scale 允许误差 |

该工具只创建不可变计划，不直接修改资产。

### 2.2 应用到 Editor 内存

```text
ue_apply_asset_property_live
```

确认短语：

```text
LIVE APPLY <planId>
```

应用后：

- 修改当前 Editor 内存中的 AnimSequence；
- Package 变为 Dirty；
- 不自动保存；
- 返回 Before / After Root Track、Root Lock 和最终 Component Scale；
- 记录可撤销事务。

### 2.3 撤销或放弃

```text
ue_undo_asset_property_live
ue_discard_asset_property_live
```

两者都不会保存 Package。Undo 保留 Editor Redo 语义；Discard 用于恢复到本次修改前状态并结束该事务。

### 2.4 保存和独立验证

确认 Live Apply 结果后，继续使用现有受控流程：

```text
ue_save_authorized_asset
→ ue_verify_live_write / ue_verify_asset
```

保存后的独立 Unreal 重载验证会比较：

- Root 骨骼名；
- Force Root Lock；
- Root Motion 设置；
- Root Track 是否存在；
- Root Track Key 数量；
- 首帧、中间帧、末帧 Scale。


`ue_verify_live_write` 明确拆分四类结果：

```text
appliedValue             Live Apply 后的完整读取值
persistedExpectedValue   预期写入 Package 的字段
exportedPersistedValue   独立重载后实际导出的持久化字段
runtimeVerification      Reference Scale、最终求值状态和最终 Root Scale
```

最终 Component Scale 属于 Editor 运行时求值结果，在 Live Apply 阶段强制验证；保存后则验证能够持久化的序列设置和 Root Track 数据。

---

## 3. 推荐修复顺序

### 3.1 第一选择：仅修 Root Lock

当前心月狐 Idle 已真实验证：

```text
修改前：
Force Root Lock = false
Root Track Scale = 1
最终 Root Scale = 1

修改后：
Force Root Lock = true
Root Motion Root Lock = RefPose
Root Track Scale = 1（未修改）
最终 Root Scale = 100
```

计划示例：

```json
{
  "asset_path": "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu.MM_Idle_XinYueHu",
  "root_bone": "Root",
  "expected_final_scale": 100.0,
  "force_root_lock": true,
  "root_motion_root_lock": "RefPose",
  "root_track_scale_mode": "Keep",
  "final_scale_tolerance": 1.0
}
```

优点：

- 不改动画 Key；
- 风险较低；
- 适合目标骨架 Reference Pose 已正确、但播放路径错误的普通动画。

### 3.2 第二选择：修正 Root Scale Track

如果 Root Lock 不能稳定得到正确结果，可将全部 Root Scale Key 改为目标 Skeleton 的 Root Reference Local Scale：

```json
{
  "asset_path": "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu.MM_Idle_XinYueHu",
  "root_bone": "Root",
  "expected_final_scale": 100.0,
  "root_track_scale_mode": "ReferenceLocal",
  "final_scale_tolerance": 1.0
}
```

当前样本已真实验证：

```text
Root Track Scale：1 → 100
最终 Root Scale：1 → 100
Undo 后 Root Track：100 → 1
Undo 后最终 Scale：100 → 1
```

### 3.3 Uniform 模式

`Uniform` 只在已经明确知道目标数值时使用：

```json
{
  "root_track_scale_mode": "Uniform",
  "uniform_scale": 100.0
}
```

默认应优先使用 `ReferenceLocal`，避免把某个项目的固定数值错误套用到其他 Skeleton。

---

## 4. 自动安全门禁

操作要求：

- 目标必须是精确 Object Path；
- 目标必须是 `UAnimSequence`；
- 资产必须已经打开并加载；
- Package 必须在修改前保持 Clean；
- Policy 必须允许 `setAnimationScaleFix`；
- 修改值必须通过专用字段和数值校验；
- 最终 Component Scale 必须达到 `expected_final_scale`；
- 验证失败时整笔事务自动恢复；
- 不允许静默保存。

该操作是 Live Editor 专用操作：

```text
dryRunSupported = false
commitSupported = false
```

这里表示它不通过离线 Commandlet DryRun / Commit 修改动画；实际安全预览由 Plan + Live Apply + Readback + Undo 提供。

---

## 5. Additive 限制

Additive 动画不能脱离 Base Pose 独立判断最终姿势。

默认行为：

- 允许读取 Raw / Compressed Track 和 Additive 元数据；
- `setAnimationScaleFix` 对 Additive 全面拒绝，不修改 Root Lock、Root Motion 设置或 Root Track；
- 拒绝 `expected_final_scale` 最终姿势验收；
- 返回 `retarget_additive_scale_fix_requires_base_pose`。

实测结论（2026-08-15）：Additive 的 `combined ≈ 99` 是 **raw 数据**里的重定向残留（`combined = raw` 恒成立），而 Additive 的 **delta ≈ 0**（运行时几乎不改变 scale）。因此 Additive **不需要 scale-fix**——它应用在当前姿势上时 scale 几乎不变。真正的 minor 问题是自引用 Base Pose 造成的 delta ≈ -0.01（该是 0），由 `setAdditiveBasePoseFix`（P4）修正，而不是本工具。曾尝试扩展 `setAnimationScaleFix` 支持 Additive（root track scale → 1），但实测 root track 改动不会改变 Additive 的 combined scale，故保持拒绝。

---

## 6. 已完成的真实回归

测试资产：

```text
/Game/Characters/XinYueHu/Animations/Retargeted/
MM_Idle_XinYueHu.MM_Idle_XinYueHu
```

### Root Lock 路径

```text
Before Final Root Scale = 1
After Final Root Scale  = 100
Root Track Scale        = 1（保持不变）
Undo                     = 成功
Package Saved            = false
Disk SHA changed         = false
```

### Root Track 路径

```text
Before Root Track Scale = 1
After Root Track Scale  = 100
After Final Root Scale  = 100
Undo Root Track Scale   = 1
Undo Final Root Scale   = 1
Package Saved           = false
Disk SHA changed        = false
```

测试使用真实 UE5.6 Editor World 中的最终动画姿势求值，不是仅检查字段或 Raw Track。

# 动画重定向比例诊断结果

更新时间：2026-08-06
分支：`feature/live-editor-realtime-io`
测试工程：`E:\WorkSpace\我的项目\我的项目.uproject`
引擎：Unreal Engine 5.6

## 1. 结论

心月狐重定向动画的比例异常不是单纯由动画 Data Model 中出现 `0.01` 或 `100` Scale Key 导致。

已确认的数据关系：

```text
Manny Skeleton Root Reference Scale       1
心月狐 Skeleton Root Reference Scale      约 100
97 个重定向 AnimSequence 的 Raw Root Scale  均为 1
普通动画的 Compressed Root Scale             也为 1
```

真正决定最终角色比例的是 Unreal 动画求值后的 Component Space Pose，而不是只看 Raw Track。

稳定复测结果：

```text
MM_Idle_XinYueHu
    Raw Root Scale             1
    Compressed Root Scale      1
    Force Root Lock            false
    Final Root/Pelvis Scale    1
    Final Pelvis Z             约 0.897
    结果                        缩小约 100 倍

MM_Death_Front_01_XinYueHu
    Raw Root Scale             1
    Compressed Root Scale      1
    Force Root Lock            false
    Final Root/Pelvis Scale    1
    Final Pelvis Z             约 0.897
    结果                        缩小约 100 倍

MF_Unarmed_Jog_Fwd_XinYueHu
    Raw Root Scale             1
    Compressed Root Scale      1
    Force Root Lock            true
    Final Root/Pelvis Scale    约 100
    Final Pelvis Z             约 89.666
    结果                        比例正常
```

当前项目的验收标准必须明确写成：

```text
Final Root/Pelvis Component Scale ≈ 100  → 正确
Final Root/Pelvis Component Scale ≈ 1    → 错误，角色缩小约 100 倍
```

这里不能套用“UE 资产通常 Root Scale=1”的通用经验。对当前心月狐资产，100 才是现有导入约定下的正确显示比例。

因此当前最明确的直接机制是：

> 当目标 Skeleton 的 Root Reference Scale 约为 100，而动画 Root Track 为 1 时，未经过 Root Lock 重置的普通动画会把最终根节点比例压到 1；启用 Force Root Lock 的动画会在求值阶段恢复目标 Root Reference Transform，因此最终比例回到约 100。

这也解释了为什么 Idle 缩小、Jog 正常。关键分界不是单纯的 `Enable Root Motion`，而是 `Force Root Lock` 是否参与最终 Root Pose 重置。

## 2. Additive 动画

Additive 动画不能使用单独的 `AnimSingleNodeInstance` 得到可信的最终角色姿势。UE5.6 会明确警告：Additive Sequence 缺少 Base Pose 或 Animation Blueprint 上下文时，独立播放结果无效。

工具对 Additive 动画只返回：

- Additive 类型；
- Base Pose 类型；
- Base Pose Animation 路径；
- Raw Track；
- Compressed Delta；
- Retarget Source；
- Root Motion 设置。

不会伪造最终预览姿势，返回：

```text
previewEvaluationStatus=unsupported-additive-requires-base-pose
```

样本 `MM_Rifle_Idle_ADS_AO_CC_XinYueHu` 的结果：

```text
Additive Anim Type          Mesh Space Rotation Offset
Base Pose Type              Selected Animation Frame
Base Pose Sequence          Manny MF_Rifle_Idle_ADS
Raw Root Scale              1
Compressed Root Scale Delta -0.99
Compressed Pelvis Scale     0
```

这说明 Additive 的压缩 Scale 是相对于 Base Pose 的 Delta，不是绝对 Scale。若 Base Pose 仍引用 Manny 动画，而目标角色使用 Root Scale 约 100 的心月狐 Skeleton，就可能在组合时产生缩小、放大或不可见等异常。

Additive 修复不能直接把 Scale 乘以 100，必须在真实 Base Pose 或 Animation Blueprint 组合上下文中验证。

## 3. 新增只读工具

```text
ue_diagnose_animation_scale
```

输入：

```json
{
  "animationPaths": [
    "/Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu.MM_Idle_XinYueHu"
  ],
  "boneNames": ["Root", "Bip001Pelvis"],
  "loadIfNeeded": true
}
```

限制：

- 单次最多 32 个动画；
- 单次最多 16 个骨骼；
- 必须使用精确 Object Path；
- 默认不加载未加载资产；
- `loadIfNeeded=true` 时才允许显式加载；
- 需要 `retarget.inspect` Capability；
- 不打开资产编辑窗口；
- 不保存 Package；
- 不修改 Content。

返回内容：

- `loadedBefore` / `loadedByBridge`；
- Skeleton 与 Preview Mesh；
- Additive 和 Base Pose 元数据；
- Root Motion / Force Root Lock / Root Lock Mode；
- Retarget Source 与保存的 Reference Pose；
- Skeleton Reference Local / Component Scale；
- Raw Data Model Scale；
- 当前平台 Compressed Scale；
- 非 Additive 动画的最终 Editor World Component Space Pose；
- Root Motion 提取结果；
- 求值状态和 Editor Session ID。

## 4. 最终姿势求值方式

普通 AnimSequence 使用隐藏的瞬时 `USkeletalMeshComponent`：

```text
创建 RF_Transient Component
→ 注册到当前 Editor World
→ 设置 Preview Mesh
→ AnimationSingleNode 播放
→ 采样 0%、50%、100%
→ Tick Animation
→ Refresh Bone Transforms
→ 等待 Parallel Evaluation 完成
→ 读取 Component Space Bone Transform
→ Unregister Component
→ 释放瞬时对象
```

该组件：

- 不加入关卡 Actor；
- 不出现在视口；
- 不保存；
- 不修改动画或网格资产；
- 每个动画读取完成后立即注销。

## 5. 验证规则调整

不能继续使用以下简单规则：

```text
Raw Root Scale 相对 Reference Scale 为 0.01
→ 直接判定动画错误
```

因为相同的 Raw/Compressed Root Scale=1：

- Idle 最终 Scale=1；
- Jog 最终 Scale=100。

可靠验证应分层：

```text
Raw Track
→ Compressed Track
→ Root Lock / Root Motion / Retarget Source
→ Final Evaluated Component Pose
→ Additive Base Pose / ABP 组合
```

普通动画应以最终 Root/Pelvis Component Scale 与目标 Reference Component Scale 的倍率作为主要比例门禁。

Additive 动画必须在明确的 Base Pose 或 Animation Blueprint 上下文中求值后再判定，不能使用独立 Sequence Preview 值。

## 6. Bounds 限制

本轮测试中 `USkeletalMeshComponent::Bounds` 仍接近 Skeletal Mesh 导入 Bounds，即使最终骨骼 Component Scale 已从 100 变为 1，Bounds 也没有同比缩小。

因此当前不能把 Bounds 作为该问题的主要证据。可靠指标是：

- Root Component Scale；
- Pelvis Component Scale；
- Pelvis Component Location；
- 与目标 Reference Component Transform 的倍率。

## 7. 后续修复方向

普通动画：

1. 检查 IK Retargeter 输出为什么保留 Root Scale=1；
2. 明确哪些动画应启用 Force Root Lock；
3. 优先在重定向输出阶段统一 Root Scale / Root Lock 语义；
4. 使用最终姿势诊断验证修复前后结果；
5. 禁止未验证的批量 `Scale ×100`。

Additive 动画：

1. 读取并验证 Base Pose Sequence；
2. 检查 Base Pose 是否仍引用 Manny Skeleton 动画；
3. 在目标 Animation Blueprint 或显式 Base Pose 组合中求值；
4. 对 AO、HitReact、Fire、RecoveryAdditive 分组验证；
5. 修复后重新生成 BlendSpace、AimOffset 和 Montage 引用。

## 8. 本轮门禁

已通过：

- MCP 参数和 Capability 门禁；
- 只读加载证据；
- UE5.6 C++ 编译；
- 同一 Editor Session 重复求值一致；
- Idle / Death / Jog / Additive 实际资产验证；
- 未修改或保存任何 Content Package。

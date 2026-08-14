# 角色浮空（Ground Contact）诊断工具

> 分支：`feature/live-editor-realtime-io`
> 适用引擎：UE 5.6
> 状态：只读诊断，已实现

---

## 1. 目标

只读判断「角色浮空」来自哪一层——碰撞胶囊、Mesh Offset 还是动画，不修改任何资产或组件。

它区分三类事实：

```text
Character Capsule（半径 / 半高 / 相对位置）
SkeletalMeshComponent（相对变换 / 网格 / 骨架）
动画最终 Component Pose（Root / Pelvis / 左右脚）
```

不能仅凭 Mesh 或 Capsule 单方面下结论。本工具把三者读到一起，输出一个稳定的分类和下一步建议。

---

## 2. MCP 工具

```text
ue_diagnose_character_ground_contact
```

| 参数 | 作用 |
|---|---|
| `characterPath` | 精确 ACharacter Blueprint Object Path |
| `animationPath` | 可选 AnimSequence；缺省时跳过动画相关字段 |
| `rootBone` | Root 骨骼名，默认 `root` |
| `pelvisBone` | Pelvis 骨骼名，默认 `pelvis` |
| `leftFootBone` | 左脚骨骼名，默认 `foot_l` |
| `rightFootBone` | 右脚骨骼名，默认 `foot_r` |
| `loadIfNeeded` | 允许按需 LoadObject |

> 骨骼名需按目标骨架填写。心月狐骨架（`SK_XinYueHu_Skeleton`）为 Biped 命名：
> `Root` / `Bip001Pelvis` / `Bip001LFoot` / `Bip001RFoot`。

---

## 3. 响应字段

```text
character.path / classPath / loadedBefore / loadedByBridge
character.capsule { present, radius, halfHeight, relativeLocation }
character.mesh    { present, skeletalMeshPath, skeletonPath, relativeTransform }
boneNames         { root, pelvis, leftFoot, rightFoot }
skeletonReference { status, bones[] }          # Skeleton Reference Pose 的骨骼分量变换
animation {
  path, loadedBefore, loadedByBridge,
  status,          # evaluated / skipped-no-animation / skeleton-mismatch / ...
  skeletonPath, skeletonCompatible, previewMeshPath,
  rootMotionZ, rootMotionTranslation,
  samples[] { fraction, time, boundsOrigin, boundsExtent,
              leftFootLowestZ, rightFootLowestZ,
              leftFootToCapsuleBottom, rightFootToCapsuleBottom,
              bones[] }
}
classification / suggestedNextStep
```

`animation.status` 取值：

```text
evaluated                  动画与角色骨架兼容、已求值
skipped-no-animation       未提供动画
not-loaded / not-an-animation-sequence
missing-skeleton
character-mesh-unavailable
skeleton-mismatch          动画骨架 != 角色网格骨架
unsupported-additive       Additive 动画（需 Base Pose，超出本工具范围）
```

`leftFootToCapsuleBottom` / `rightFootToCapsuleBottom` 是 actor 空间内「脚到胶囊底部」的 Z 距离（仅在 `capsule` + `mesh` + 兼容动画齐全时给出）。

---

## 4. 分类

```text
mesh-offset-candidate       Mesh Component 相对变换非零，原点与胶囊不一致
capsule-size-candidate      胶囊底部远高于脚（胶囊过短，脚穿模）
animation-root-z-candidate  动画 Root 抬离地面（Root Motion Z / Root 骨骼漂移）
pelvis-offset-candidate     Pelvis 相对 Reference Pose 被抬升
foot-ik-needed              脚悬在胶囊底部之上，需要 Foot IK 或轨道修正
insufficient-context        数据不足或未检测到浮空源
```

分类由 Python `character_ground_contact.py` 完成（阈值集中在文件顶部常量，单位 cm）。C++ 只负责读原始事实，不做判定。

---

## 5. 安全门禁

- 目标必须是精确 `/Game` Object Path（`characterPath` 必须能解析为已编译 ACharacter Blueprint）；
- 只读：不改资产、组件或 Package，不注册事务；
- PIE / SIE 活跃时拒绝（`retarget_editor_state_invalid`）；
- 骨架不兼容时不强行动画求值，返回 `skeleton-mismatch`；
- Policy 需要 `retargetCapabilities` 包含 `retarget.inspect`（与现有只读诊断工具同一能力门禁，不新增独立 capability）。

---

## 6. 真实回归

测试资产：

```text
Character:  /Game/Characters/XinYueHu/Blueprints/BP_XinYueHu_Character
Animation:  /Game/Characters/XinYueHu/Animations/Retargeted/MM_Idle_XinYueHu
Bones:      Root / Bip001Pelvis / Bip001LFoot / Bip001RFoot
```

Smoke 验证：

- 有动画：`capsule.present` + `mesh.present` + `skeletonReference.status == evaluated` + 4 个参考骨骼全部 `boneExists` + `animation.status == evaluated` + 3 个采样帧。
- 无动画：`animation.status == skipped-no-animation`，仍返回胶囊 / 网格 / 参考姿态。
- 磁盘 Package 与 SQLite 索引零写入。

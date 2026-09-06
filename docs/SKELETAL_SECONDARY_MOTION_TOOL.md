# 骨骼次级运动（Skeletal Secondary Motion）读取工具

> 适用引擎：UE 5.6
> 状态：只读读取，已实现

---

## 1. 目标

只读结构化读取 Skeletal Mesh 的次级运动（尾巴 / 衣服 / 蒙皮 / 物理 / Cloth / AnimBP 节点），不做任何修改。**明确不实现**通用 AnimGraph 节点写入。

它把五类事实读到一起：

```text
SkeletalMesh     （骨架 / 物理资产 / LOD / 蒙皮权重摘要）
Skeleton         （骨骼层级：父链 / 根骨）
Physics Asset    （Body / Constraint 数量）
Clothing Asset   （数量 / 每资产 LOD 数 / 类名）
AnimSequence     （骨骼轨道覆盖率）
AnimBlueprint    （AnimDynamics / RigidBody / Spring 节点）
```

不能仅凭单一机制下结论。本工具把六者读到一起，输出一个稳定分类和下一步建议。

---

## 2. MCP 工具

```text
ue_inspect_skeletal_secondary_motion
```

| 参数 | 作用 |
|---|---|
| `skeletalMeshPath` | 精确 Skeletal Mesh Object Path（必填） |
| `animationPath` | 可选 AnimSequence；检查骨骼轨道覆盖 |
| `animationBlueprintPath` | 可选 AnimBlueprint；扫描次级运动节点 |
| `loadIfNeeded` | 允许按需 LoadObject |

---

## 3. 响应字段

```text
skeletalMesh {
  path, loadedBefore, loadedByBridge,
  skeletonPath, physicsAssetPath,
  lodCount, vertexCount, maxBoneInfluences, hasSkinWeights
}
skeleton { boneCount, rootBoneName, bones[] { index, name, parentIndex, parentName } }
physics  { present, path, bodyCount, constraintCount }
cloth    { assetCount, assets[] { name, className, numLods } }
animation {
  path, loadedBefore, status, skeletonCompatible,
  animatedBoneCount, totalBoneCount
}
animationBlueprint {
  path, status,
  secondaryMotionNodeCount,
  springBoneCount, rigidBodyCount, animDynamicsCount,
  nodes[] { title, className }
}
classification / suggestedNextStep
```

`animation.status` 取值：

```text
evaluated                  骨架兼容、已统计轨道覆盖
skipped-no-animation       未提供动画
not-loaded / not-an-animation-sequence
missing-skeleton
mesh-skeleton-unavailable
skeleton-mismatch          动画骨架 != 网格骨架
```

`animationBlueprint.status` 取值：

```text
evaluated                         已枚举 AnimGraph 节点
skipped-no-animation-blueprint    未提供 AnimBlueprint
not-loaded / not-an-anim-blueprint
```

> 蒙皮权重只读「存在性 + 最大影响数」摘要；**不做**逐骨骼顶点影响数 / 平均权重枚举——那是渲染线程数据且随引擎版本敏感，分类不需要该粒度。

---

## 4. 分类

```text
missing-bones              骨架无骨骼
missing-skin-weights       无蒙皮权重（0 顶点或无影响）
no-animation-tracks        AnimSequence 未覆盖任何网格骨骼轨道
no-secondary-motion-node   AnimBlueprint 无 AnimDynamics/RigidBody/Spring 节点
no-physics-bodies          无物理资产（或 0 Body）且无 Cloth / 节点
cloth-data-present         Cloth 存在且已绑定所有 LOD
cloth-data-missing         无 Cloth（次级运动依赖物理 / 节点）
cloth-binding-incomplete   Cloth 存在但未覆盖所有 LOD
```

分类由 Python `skeletal_secondary_motion.py` 完成（优先级自上而下）。C++ 只读原始事实，不做判定。

---

## 5. 安全门禁

- 目标必须是精确 `/Game` Object Path；
- 只读：不改资产、组件或 Package，不注册事务；
- PIE / SIE 活跃时拒绝（`retarget_editor_state_invalid`）；
- 骨架不兼容时不统计轨道覆盖，返回 `skeleton-mismatch`；
- Policy 需要 `retargetCapabilities` 包含 `retarget.inspect`（复用现有只读诊断能力门禁，**不新增**独立 `character.secondary-motion.inspect`，避免重新引入 `retargetCapabilities` 不在 `POLICY_FIELDS` 的 policy 冲突）。

---

## 6. 真实回归

测试资产：

```text
Mesh:       /Game/Characters/ExampleCharacter/Mesh/SK_ExampleCharacter
Animation:  /Game/Characters/ExampleCharacter/Animations/Retargeted/MM_Idle_ExampleCharacter
AnimBP:     /Game/Characters/ExampleCharacter/Animations/ABP_ExampleCharacter
```

Smoke 验证：

- 完整调用：`skeletalMesh.skeletonPath` + `lodCount >= 1` + `vertexCount > 0` + `skeleton.boneCount > 0` + `skeleton.bones` 长度一致 + `animation.status == evaluated` + `animationBlueprint.status == evaluated`。
- 仅 Mesh 调用：`animation.status == skipped-no-animation` + `animationBlueprint.status == skipped-no-animation-blueprint`。
- 磁盘 Package 与 SQLite 索引零写入。

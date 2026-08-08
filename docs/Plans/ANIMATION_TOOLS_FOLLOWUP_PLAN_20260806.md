# UEAgentKit 动画工具后续开发计划

更新时间：2026-08-08
目标分支：`feature/live-editor-realtime-io`
当前提交基线：`afb25ba feat: add live animation scale diagnosis`
测试工程：`E:\\WorkSpace\\我的项目\\我的项目.uproject`
目标接入工程：`E:\\WorkSpace\\ModelPreview\\ModelPreview.uproject`

---

## 1. 当前状态

当前分支已经完成从“只能看动画轨道”到“可以安全修改并验证最终姿势”的第一条纵向闭环。

已实现：

```text
ue_diagnose_animation_scale
ue_plan_animation_scale_fix
setAnimationScaleFix
ue_apply_asset_property_live
ue_undo_asset_property_live
ue_discard_asset_property_live
ue_save_authorized_asset
ue_verify_live_write
ue_refresh_asset_index
```

当前 `setAnimationScaleFix` 支持：

- `Force Root Lock`；
- `Enable Root Motion`；
- `Use Normalized Root Motion Scale`；
- `Root Motion Root Lock`；
- Root Scale Track 保持不变；
- Root Scale Track 使用目标 Skeleton Reference Local Scale；
- Root Scale Track 使用明确 Uniform Scale；
- 修改后最终 Editor World Component Space Scale 验证；
- 失败自动恢复；
- Undo / Discard；
- 授权保存、独立重载验证和索引刷新。

真实样本已验证：

```text
MM_Idle_XinYueHu

Root Lock 路径：
Final Root Scale 1 → 100
Root Track Scale 保持 1
Undo 后恢复为 1

Root Track 路径：
Root Track Scale 1 → 100
Final Root Scale 1 → 100
Undo 后全部恢复为 1
```

已正式保存一个样本：

```text
/Game/Characters/XinYueHu/Animations/Retargeted/
MM_Idle_XinYueHu.MM_Idle_XinYueHu

Force Root Lock       = true
Root Motion Root Lock = RefPose
Root Track Scale      = 1
保存后独立验证        = verified
```

注意：该保存发生在测试工程“我的项目”，不是 ModelPreview。

---

## 2. P0 收口状态

2026-08-08 已完成本纵向切片的代码审阅、响应模型修正和真实 UE5.6 门禁。本地提交与本次文档更新一并完成，不推送远程。

完成结果：Python `382 passed`、Ruff passed、UE5.6 Direct Build passed、Root Lock/Root Track Live Apply+Undo passed、Authorized Save/Independent Verify passed、Index Refresh passed、`git diff --check` passed。

### P0.1 代码审阅

必须检查：

- `LiveWriteAnimationOperations.cpp` 是否只处理 `UAnimSequence`；
- Additive 默认拒绝是否不可绕过；
- Root Bone 精确名称和 Reference Scale 读取是否稳定；
- 修改失败是否整笔 Transaction 恢复；
- No-op 是否不会制造 Dirty；
- 保存后 Undo 是否正确拒绝；
- Runtime-only 字段与持久化字段是否分开返回。

### P0.2 验证响应模型修正

当前保存验证已经能正确判定 `verified`，但响应中：

```text
expectedValue
```

仍可能包含：

```text
finalRootScale
finalEvaluationStatus
referenceLocalScale
```

这些属于运行时求值或派生数据，不是 Package 中直接持久化的字段。后续应明确拆成：

```text
appliedValue
persistedExpectedValue
exportedPersistedValue
runtimeVerification
```

避免调用方错误地对完整对象做相等比较。

### P0.3 门禁

提交前必须通过：

```text
Python 全量测试
Ruff
UE5.6 Direct Plugin Build
Root Lock Live Apply / Undo
Root Track Live Apply / Undo
Authorized Save / Independent Verify
Index Refresh
git diff --check
```

### P0.4 本地提交

建议提交：

```text
feat: add controlled animation scale repair workflow
```

不推送远程。

---

## 3. P1：批量动画诊断

2026-08-08 已完成第一条批量只读纵向闭环：

```text
ue_start_animation_scale_audit
ue_get_animation_scale_audit
ue_cancel_animation_scale_audit
```

当前版本接受显式 AnimSequence Object Path 列表，单任务最多 1000 个资产，`batchSize` 最大 8；每次 Get 只推进一个 Batch，并提供分类统计、分页 Detail、Cancel 和 Editor Session 失效检测。真实 UE5.6 Smoke 已验证 `MM_Idle_XinYueHu` 分类为 `normal`，Root Track Scale=1、最终 Root Scale=100，磁盘 Package 和 SQLite 均未变化。

P1 已完成：显式 Object Path 列表、固定 immutable Index `pathPrefix` 候选生成、Index Snapshot ID 冻结、按分类过滤与稳定排序、1000 候选 / Batch 8 / Page 50 有界工作量门禁，以及固定 WorkRoot 的确定性 JSON Audit Report 均已通过测试；下一步进入 P2 批量 Plan / Apply / Save / Rollback。

当前工具一次可以读取多个动画，但还没有形成面向 Agent 的批量分类任务。

新增建议：

```text
ue_start_animation_scale_audit
ue_get_animation_scale_audit
ue_cancel_animation_scale_audit
```

使用现有 Realtime Batch Task 框架，逐帧处理资产，避免一次加载和求值大量动画。

每个 AnimSequence 输出：

```text
Asset Path
Asset Type
Skeleton
Additive Type
Base Pose Type / Path
Root Motion 设置
Force Root Lock
Root Track Raw / Compressed Scale
Final Root / Pelvis Scale
Final Pelvis Location
诊断分类
建议修复路径
```

建议分类：

```text
normal
scale-too-small
scale-too-large
root-lock-candidate
root-track-candidate
root-motion-review
additive-requires-base-pose
unsupported-composite
load-failed
```

要求：

- 默认只读；
- 显式 `loadIfNeeded`；
- 分帧；
- 可取消；
- 结果分页；
- 不修改、不保存。

---

## 4. P2：批量修复计划与 Change Set

P2 第一条规划纵向切片已实现：`ue_plan_animation_scale_fix_batch` 从固定 WorkRoot 的 Completed Audit Report + 显式 `asset_paths` 生成不可变 Batch Plan，`ue_get_animation_scale_fix_batch` 可重新读取并校验该 Plan。每个候选仍复用现有单资产 `ue_plan_animation_scale_fix`，因此 Policy、Revision、Asset Class 和 Operation 校验没有新增旁路。

当前自动策略只接受 `root-lock-candidate` / `root-track-candidate`；其他被用户选中的分类直接拒绝，不静默跳过。默认 `expectedFinalScale` 从每个 Audit Item 的 Skeleton Root Reference Component Scale 推导，不写死 `100`。Root Track 可显式 override 并自动切换为 Uniform；Root Lock override 必须与 Reference Scale 一致。

本切片仍完全不执行 Live Apply、Save 或 Change Set。若创建第 N 个子 Plan 失败，会清理本次已经创建的未消费子 Plan 和 WorkRoot 目录，不留下半成功 Batch Plan。最大 100 个资产，与现有 Change Set 100 Operation 上限对齐。

不能直接提供“修复全部”按钮。应先从审计结果生成不可变批量计划。

建议工具：

```text
ue_plan_animation_scale_fix_batch
ue_apply_animation_scale_fix_batch_live
ue_get_animation_scale_fix_batch
ue_undo_animation_scale_fix_batch
ue_save_animation_scale_fix_batch
ue_verify_animation_scale_fix_batch
```

执行语义：

```text
Audit Report
→ Candidate Selection
→ Immutable Batch Plan
→ User Confirmation
→ Per-asset Live Apply
→ Per-asset Runtime Verify
→ Change Set Summary
→ Save Preview
→ Authorized Save
→ Independent Verify
→ Index Refresh
```

批量修改必须：

- 每个资产独立 Before Snapshot；
- 每个资产独立结果和错误；
- 支持部分失败但禁止静默跳过；
- 保存前显示会修改的 Package 数量；
- 保存前生成 Backup Manifest；
- 支持整组 rollback；
- 不允许把固定值 `100` 写死在通用逻辑中。

`expectedFinalScale` 应优先自动来自：

```text
Target Skeleton Root Reference Component Scale
```

用户可以覆盖，但必须显式。

---

## 5. P3：重定向输出后处理

动画比例修复最终应并入动画重定向工作流，而不是每次重定向完成后人工扫描。

目标链路：

```text
Retarget Batch Complete
→ Classify Output Assets
→ Audit AnimSequence
→ Build Suggested Fix Plan
→ User Review
→ Apply / Save / Verify
→ Update BlendSpace / AimOffset / Montage References
```

需要区分：

- 普通 AnimSequence；
- Root Motion AnimSequence；
- Additive AnimSequence；
- BlendSpace；
- AimOffset；
- AnimMontage；
- 被其他资产引用的输出。

不得在 `Retarget Batch` 内静默修改 Root Lock 或 Root Track。建议只生成后处理建议，由单独确认阶段执行。

---

## 6. P4：Additive / Base Pose 求值

这是动画工具下一阶段最重要的技术问题。

当前限制：

- `AnimSingleNodeInstance` 不能脱离 Base Pose 正确求值 Additive；
- Additive 压缩 Scale 是 Delta，不是绝对 Scale；
- Base Pose 可能仍引用 Manny 动画；
- 单独预览 Additive 得到的最终比例不可信。

后续 Reader 需要：

```text
Additive Sequence
+ Ref Pose Type
+ Ref Pose Sequence
+ Ref Frame Index
+ Skeleton Compatibility
+ Base Pose Final Component Pose
+ Additive Delta
→ Combined Final Pose
```

建议新增：

```text
ue_diagnose_additive_animation
ue_evaluate_animation_with_base_pose
ue_plan_additive_base_pose_fix
```

第一版只支持明确 Base Pose Sequence 的 Additive；Animation Blueprint 任意图求值放到后续阶段。

修复内容可能包括：

- 将 Base Pose 引用替换为对应心月狐重定向动画；
- 修正 Base Pose Frame；
- 修正 Additive 类型；
- 重建依赖 AimOffset / BlendSpace / Montage；
- 在组合上下文中验证最终 Root/Pelvis Scale。

---

## 7. P5：浮空诊断 Reader

ModelPreview 后续需要判断浮空到底来自碰撞、Mesh Offset 还是动画。UEAgentKit 应提供只读诊断，而不是让 Agent直接猜。

建议工具：

```text
ue_diagnose_character_ground_contact
```

读取：

```text
Character Capsule Radius / Half Height
SkeletalMeshComponent Relative Transform
Mesh Bounds
Skeleton Reference Root / Pelvis / Foot Transforms
Animation Root / Pelvis / Foot Final Component Pose
Foot Socket / Bone 到 Capsule Bottom 的距离
左右脚最低点
Root Motion Z
Retarget Root Z
```

输出分类：

```text
mesh-offset-candidate
capsule-size-candidate
animation-root-z-candidate
pelvis-offset-candidate
foot-ik-needed
insufficient-context
```

工具第一版只读，不修改 Capsule 或 Mesh Relative Location。

---

## 8. P6：尾巴、衣服和 Cloth 资产诊断

UEAgentKit 不应先实现“自动开启 Cloth”，而应先提供结构读取能力。

建议新增 Skeletal Mesh / Physics Reader：

```text
ue_inspect_skeletal_secondary_motion
```

读取：

```text
附加骨骼链
父子层级
每骨骼顶点影响数量
最大/平均 Skin Weight
动画轨道是否存在
Physics Asset 路径
Physics Body / Constraint
Clothing Asset 数量
Cloth Section / LOD Mapping
Chaos Cloth Config 摘要
Anim Blueprint 中 AnimDynamics / RigidBody / Spring 节点
```

输出应区分：

```text
missing-bones
missing-skin-weights
no-animation-tracks
no-secondary-motion-node
no-physics-bodies
cloth-data-present
cloth-data-missing
cloth-binding-incomplete
```

后续写入能力再分开实现：

1. Anim Dynamics / Rigid Body 节点的受控创建；
2. Physics Asset Body / Constraint 创建；
3. Chaos Cloth Asset 创建与 Section 绑定；
4. Cloth 参数修改；
5. PIE 动态验证和性能证据。

禁止提供通用任意 AnimGraph 节点写入作为替代。

---

## 9. P7：项目级可写配置

“我的项目”是主要写入沙箱，但不能简单使用全局取消只读。

正确方案是项目级固定配置：

```text
Project Path
Policy Path
Allowed Asset Roots
Allowed Operations
Allowed Asset Classes
EnableWriteTools
EnableCommitTools
```

建议建立：

```text
config/projects/my-project-write.json
config/projects/model-preview-read.json
config/projects/model-preview-animation-write.json
```

语义：

- “我的项目”：允许测试目录和明确心月狐动画目录写入；
- ModelPreview 默认只读；
- ModelPreview 动画写入使用单独 Policy，范围只覆盖确认后的动画目录；
- Reforge 继续默认只读。

不要把 `allowedAssetRoots=/Game` 或任意 UObject 写入作为“取消只读”的实现。

---

## 10. P8：ModelPreview 接入

接入 ModelPreview 时按以下顺序：

```text
安装同一编译插件
→ 只读 Editor Status
→ 只读资产扫描
→ 生成当前动画比例基线
→ 选择复制出的测试动画
→ Live Apply + Undo
→ 用户确认
→ 扩大 Policy 到正式动画目录
→ 保存和独立验证
```

ModelPreview 第一轮禁止修改：

- Skeleton；
- Skeletal Mesh；
- Physics Asset；
- Animation Blueprint；
- Capsule；
- Cloth；
- 正式关卡。

只验证 AnimSequence Root Lock / Root Track 工作流。

---

## 11. P9：发布与合并

完成 P0 后，当前动画修复纵向切片可以作为独立提交保留在分支。

达到以下条件后再合入 `main`：

- 单资产诊断和修改 API 稳定；
- 响应字段区分 Runtime 与 Persisted；
- 保存与回滚真实 UE5.6 回归通过；
- 项目级 Policy 示例完成；
- 工具数量和文档更新；
- 与 Memory/Context 分支公共协议无冲突。

建议版本归属：

```text
0.8.0-dev Realtime Animation Tools
```

批量动画和 Additive 可以作为后续独立提交，不阻塞当前单资产修复工具合入。

---

## 12. 推荐执行顺序

```text
第一步：收口并提交当前单资产修复工具
第二步：批量只读 Audit
第三步：批量 Plan / Apply / Save / Rollback
第四步：重定向后处理集成
第五步：Additive + Base Pose
第六步：浮空诊断 Reader
第七步：尾巴/衣服/Physics/Cloth Reader
第八步：ModelPreview 小范围写入接入
第九步：0.8.0-dev 合并准备
```

当前不要同时推进 Additive、Cloth、AnimGraph 写入和批量保存。先完成每条纵向闭环，再进入下一层。
